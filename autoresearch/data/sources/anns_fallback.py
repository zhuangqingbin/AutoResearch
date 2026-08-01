#!/usr/bin/env python3
"""公告标题流 **B 级兜底源**(Wave9 A-1)。

主源 tushare `anns_d` 自 2026-07-18 起无接口权限,`L3_news/` 整目录空稿
(2026-07-29 实测 `anns_empty_rate=1.0`),而 self_review 把它当 expected 放行 ——
"无权限"与"当日故障"在产物上长得一模一样,公告面**静默单腿**。

本模块提供 akshare 侧兜底:拉个股公告标题,归一成 `L3_news/<code>.json` 同构行。
**B 级契约**:取不到就返回空 + 由调用方记账降级,绝不抛异常阻断漏斗。

Step 5 真接口冒烟裁决记录(2026-07-29):
  - `ak.stock_notice_report(symbol="全部", date=...)`(东财全市场单日公告)实测
    `ok=false`(耗时 ~70s 翻 12 页仍拉不到可用行,`columns=[]`)—— **弃用**。该接口本身
    也不吃 `code6`(只能按日期拉全市场,得自己按代码过滤),契约上就比下面这个差。
  - 改用 `ak.stock_zh_a_disclosure_report_cninfo(symbol=code6, market="沪深京",
    start_date, end_date)`(巨潮资讯个股公告查询)实测 **可用**。真实列名 =
    `['代码', '简称', '公告标题', '公告时间', '公告链接']`—— 注意日期列是
    **`公告时间`**,不是原猜的 `公告日期`(`_pick` 候选键已补上,`公告日期` 仍保留在
    候选表首位,兼容本文件单测夹具用的列名)。`market="沪深京"` 覆盖沪/深/京三所全部
    A 股(含北交所 92xxxx),不必按 code6 前缀分流。000651 用生产同款 `_LOOKBACK_DAYS`
    (90 天)窗口实测拉到 **22** 行(先前记录的「111 行」是一年窗口探查值,只用于确认
    接口本身可用,和生产窗口不是同一量纲——复核 Minor 2 指出两个数字对不上,此处已
    按生产窗口重新实测替换为真实数)。

复核轮 1(2026-07-29):`SOURCE_TAG` 曾误取值 `"em"`——本仓库既有惯例里 `"em"` 专指
东财(见 `scan/universe.py`/`scan/frame.py` 的 `--source` choices),而本模块实际连的
是巨潮 cninfo,标签与实际供应商不符会污染未来"按供应商做数据质量归因"的场景,
已改为 `"cninfo"`,与真实接口名一致。

final-fix C-1(2026-07-30):本模块此前虽已写好,但**全仓零生产调用点**——三个"消费者"
只 import `SOURCE_TAG` 常量,`harvest_l3_news`(唯一生产者)从不调 `fetch_anns`,
`health.anns_source_status` 因此恒判 `blind`。已在 `l3_news.harvest_l3_news` 里对
每一只仍是空桶的票补调本模块(见该函数 docstring)。

`_LOOKBACK_DAYS=90` 真实批跑定标(2026-07-30,接线后跑通即测,非估):对 2026-07-29
真实 L2-200 候选(`context/scan/2026-07-29/L2_gbdt_top200.csv`,203 只)逐票真调
`fetch_anns`(不落盘、不改任何生产文件,纯读)——**命中 202/203(99.5%)、0 次异常、
平均每只命中票 15.49 行、总计 3128 行**,唯一 0 命中的 603611 在 90 天窗口内确实没有
公告(非取数失败)。整批 203 次真实网络调用耗时 316.8s,均 1.56s/call。结论:90 天
窗口对当前"主源无权限"的真实场景**命中率极高**,兜底源能扛住绝大多数票的公告面;
接线前这条定标从未有机会发生(数据没有生产调用点,90 天这个数字纯粹是经验值)。
"""
from __future__ import annotations

import contextlib
import json
import time
from datetime import datetime, timedelta
from pathlib import Path

SOURCE_TAG = "cninfo"
CACHE_VERSION = 1
NEGATIVE_TTL_SECONDS = 6 * 3600     # 负缓存有时限:6h

# Wave10 A9:此前 203 只票是**串行**真调,实测 316.8s(1.56s/call)——L3 附加约 5min。
# 两条腿一起上:
#   ① 磁盘缓存,key = (code, 分析日, source, version)。**正缓存长期有效**:公告是
#      as-of 固定的历史事实,同一分析日重跑不该再查一次网;
#   ② **负缓存有时限**(6h)。这一条必须与正缓存分开 —— 把「这次没查到」永久缓存下来
#      等于把一次网络抖动固化成「这只票没有公告」,而那正是本模块要治的病
#      (「无权限」与「当日故障」在产物上长得一样)。
_CACHE_ROOT = Path("context/cache/anns_fallback")

_LOOKBACK_DAYS = 90   # 兜底源窗口:约一季度,够覆盖近期披露且不做无界历史查询


def _raw_notices(code6: str, date: str):
    """原始取数(测试 monkeypatch 此函数;真身走 akshare 巨潮资讯 cninfo 接口)。"""
    import akshare as ak
    end = date.replace("-", "")
    start = (datetime.strptime(end, "%Y%m%d") - timedelta(days=_LOOKBACK_DAYS)).strftime("%Y%m%d")
    return ak.stock_zh_a_disclosure_report_cninfo(
        symbol=str(code6).zfill(6), market="沪深京", start_date=start, end_date=end)


def _pick(row: dict, *keys: str) -> str:
    for k in keys:
        v = row.get(k)
        if v is not None and str(v).strip():
            return str(v).strip()
    return ""


def _cache_path(code6: str, date: str) -> Path:
    return _CACHE_ROOT / str(date) / f"{str(code6).zfill(6)}.v{CACHE_VERSION}.json"


def _cache_read(code6: str, date: str) -> list[dict] | None:
    """命中 → 行列表(可为空列表);未命中/负缓存过期 → None。"""
    path = _cache_path(code6, date)
    if not path.exists():
        return None
    with contextlib.suppress(Exception):
        payload = json.loads(path.read_text(encoding="utf-8"))
        if payload.get("source") != SOURCE_TAG or payload.get("version") != CACHE_VERSION:
            return None
        rows = payload.get("rows")
        if not isinstance(rows, list):
            return None
        if rows:
            return rows                                   # 正缓存:长期有效
        age = time.time() - float(payload.get("at", 0))    # 负缓存:过期即重查
        return rows if age < NEGATIVE_TTL_SECONDS else None
    return None


def _cache_write(code6: str, date: str, rows: list[dict]) -> None:
    path = _cache_path(code6, date)
    with contextlib.suppress(Exception):
        path.parent.mkdir(parents=True, exist_ok=True)
        temp = path.with_name(f"{path.name}.tmp")
        temp.write_text(json.dumps(
            {"source": SOURCE_TAG, "version": CACHE_VERSION, "at": time.time(),
             "rows": rows}, ensure_ascii=False), encoding="utf-8")
        temp.replace(path)


def fetch_anns_batch(codes, date: str, *, limit: int = 20,
                     workers: int = 8, cache: bool = True) -> dict[str, list[dict]]:
    """有界并发批量取(Wave10 A9)。逐票仍走 `fetch_anns`(含缓存),只是不再排队等。

    并发上界写死为**有界**:兜底源是别人的服务,无界并发既不礼貌也会触发限频 ——
    那会把「慢」换成「被拒」,不是改进。
    """
    from concurrent.futures import ThreadPoolExecutor

    want = [str(c).zfill(6) for c in codes]
    if not want:
        return {}
    out: dict[str, list[dict]] = {}
    with ThreadPoolExecutor(max_workers=max(1, int(workers))) as pool:
        for code, rows in zip(want, pool.map(
                lambda c: fetch_anns(c, date, limit=limit, cache=cache), want),
                strict=False):
            out[code] = rows
    return out


def fetch_anns(code6: str, date: str, *, limit: int = 20,
               cache: bool = False) -> list[dict]:
    """→ `[{"ann_date": "YYYYMMDD", "title": str, "source": SOURCE_TAG}, ...]`,失败/无数据 → `[]`。

    as-of 铁律:`ann_date > date` 的行一律丢弃(前视污染)。

    `cache` **默认关**(Wave10 A9 复核实测):缓存写的是进程全局路径,如果这个纯取数函数
    偷偷带上它,任何直调者(包括测试)都会读到别人留下的条目 —— 实测就让 5 条既有用例
    读到真实缓存而不是自己的夹具。**取数是取数,缓存是效果**,由知道自己在批量跑的
    调用方(`fetch_anns_batch`)显式打开。
    """
    cached = _cache_read(code6, date) if cache else None
    if cached is not None:
        return cached
    cut = date.replace("-", "")
    try:
        df = _raw_notices(code6, date)
    except Exception:  # noqa: BLE001 — B 级源:取数失败降级为空,由调用方记账
        # **不写缓存**:异常是"没查成",不是"没有公告"。把它记成负缓存会让一次网络抖动
        # 在 6h 内一直伪装成"这只票没公告"——正是本模块存在要治的那类静默。
        return []
    if df is None or not len(df):
        if cache:
            _cache_write(code6, date, [])      # 真·无料 → 负缓存(有时限)
        return []

    rows: list[dict] = []
    for rec in df.to_dict("records"):
        title = _pick(rec, "公告标题", "title", "名称")
        raw_date = _pick(rec, "公告日期", "公告时间", "ann_date", "日期")
        if not title or not raw_date:
            continue
        ann = raw_date.replace("-", "")[:8]
        if not ann.isdigit() or ann > cut:      # as-of ≤ 分析日
            continue
        rows.append({"ann_date": ann, "title": title, "source": SOURCE_TAG})
        if len(rows) >= limit:
            break
    if cache:
        _cache_write(code6, date, rows)
    return rows


def probe() -> dict:
    """冒烟自检:接口在不在、字段名对不对。CLI `python -m ...anns_fallback` 调。

    走 `_raw_notices` 同一条真实路径(000651 作探针票),而非另起一套断言——
    确保这条自检真的反映 `fetch_anns` 当下会打到的接口。
    """
    out = {"ok": False, "source": SOURCE_TAG, "columns": [], "error": None}
    with contextlib.suppress(Exception):
        today = datetime.now().strftime("%Y-%m-%d")
        df = _raw_notices("000651", today)
        out["columns"] = list(df.columns)[:12]
        out["ok"] = bool(len(df))
    return out


if __name__ == "__main__":
    import json
    print(json.dumps(probe(), ensure_ascii=False))
