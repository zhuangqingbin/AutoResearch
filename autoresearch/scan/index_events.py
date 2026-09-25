#!/usr/bin/env python3
"""指数调样事件表 `index_events.csv`(确定性,零 LLM;design 2026-09-25 §2.2)。

一行 = (票, 指数, 调入|调出)。相位按**扫描日 D** 算:
  announced_runup    A ≤ D ≤ E−2         事实日期,不作论点
  passive_close_eve  D = E−1(E 的前一交易日)   ← E6 硬门 `rebalance_close` 命中的唯一相位
  effective          D = E
  post               E < D ≤ E+3
  unknown_eff        生效日解析不出(如「自退市日起」),且公告没有老过整段窗口(见 #7)
E = 被动调仓的那个收盘日:「X 日收市后生效」→ X;「X 日起生效/实施」→ X 的前一交易日;半年定期调样
(公告在 5/11 月)规则兜底 = 次月第二个周五;科创50 另有季度调样,2/8 月公告兜底到 3/9 月第二个周五。
**规则兜底只描述周期性调样**:标题带「临时」的调整公告即便落在这四个月,也不套用那张周期表
(fix-round-2 #1)——它只在文字解析不出日期时才会被问到,而「临时」公告的月份本就与周期性无关。

**E 必须落在真交易日历上,猜不得**(2026-09-25 review #1/#2):`trading_days_window` 取不到真日历、
只拿到 `weekday_approx` 近似时,`build_index_events` 直接不产表(降级成「源不可达」同一世界)——一张
按近似日历算出的相位表会把 `passive_close_eve` 错标到真实日历上的另一天,而这正是硬门要防的反转:
它会挡下真正该挡的隔夜、放行真正该放的那夜。同理,若解析/规则算出的 E 恰好落在窗口内却不是交易日
(节假日),`phase_for` 只标 `unknown_eff`,绝不悄悄挪到最近的交易日:我们手上没有「指数公司遇到
节假日往哪边挪」的任何证据,猜错方向比诚实地说「不知道」更危险。

六指数白名单是**产品选择**:附件里其它指数(上证380/三板…)入湖不进表。**已知覆盖缺口**:`399006`
创业板指是深证/国证口径指数,其调样公告不在本模块读的中证指数公司(csindex.com.cn)公告源里发布——
公告驱动的这条路径永远不会为它产出一行事件。白名单仍保留这一项(不会误判,只是从不命中);真正能
覆盖创业板指调样对账的是 `index_weight` 月末成分快照普查(census),不受此限制。

**缺席 ≠ 否**:源不可达(含日历只能近似)→ 返回 None + `record_degradation`(调用方不写文件);源可达
但无窗口内事件 → 只有表头的空帧(写成只有表头的文件)。跨指数迁移(沪深300 调出 = 中证500 调入)
**两行都保留**。
"""
from __future__ import annotations

import datetime as dt
import sys
from pathlib import Path

import pandas as pd

from autoresearch.common import workspace as ws
from autoresearch.scan.user_config import knob

INDEX_EVENTS_FILENAME = "index_events.csv"
INDEX_WHITELIST: dict[str, str] = {
    "000300": "沪深300", "000905": "中证500", "000852": "中证1000",
    "000510": "中证A500", "000688": "科创50", "399006": "创业板指",
}
EVENT_COLS = ["code", "index_code", "index_name", "side", "ann_date", "eff_close_date",
              "phase", "source", "flow_adv_days"]
PHASES = ("announced_runup", "passive_close_eve", "effective", "post", "unknown_eff")
POST_WINDOW = 3          # 生效后仍展示 3 个交易日(事实,不作论点)
_LIST_EP = "csindex_rebalance_list"
_DETAIL_EP = "csindex_rebalance_detail"


# ── 日期原语 ────────────────────────────────────────────────────────────────
def second_friday(year: int, month: int) -> str:
    first = dt.date(year, month, 1)
    fridays = [first + dt.timedelta(i) for i in range(31)
               if (first + dt.timedelta(i)).month == month and (first + dt.timedelta(i)).weekday() == 4]
    return fridays[1].strftime("%Y%m%d")


def rule_eff_close_date(ann_date: str) -> str | None:
    """半年定期调样兜底:5 月公告 → 6 月第二个周五;11 月公告 → 12 月第二个周五。
    科创50 另有季度调样(review 2026-09-25 #6):2 月公告 → 3 月第二个周五;8 月公告 → 9 月第二个周五。
    其它月份无规则——半年制指数不在 2/8 月发公告、季度制指数不在 5/11 月发公告,两组规则的月份
    互不相交,合并成一张表不会把哪条腿判错。"""
    y, m = int(ann_date[:4]), int(ann_date[4:6])
    if m == 5:
        return second_friday(y, 6)
    if m == 11:
        return second_friday(y, 12)
    if m == 2:
        return second_friday(y, 3)
    if m == 8:
        return second_friday(y, 9)
    return None


def prev_trading_day(day: str, trading_days: list[str]) -> str | None:
    earlier = [d for d in trading_days if d < day]
    return earlier[-1] if earlier else None


def eff_close_from(parsed_date: str | None, kind: str | None, ann_date: str,
                   trading_days: list[str], *, is_temporary: bool = False) -> tuple[str | None, str]:
    """→ (被动调仓收盘日, source)。source ∈ {csindex, rule, none}。

    review 2026-09-25 #3:「起生效」推前一交易日,如果 `parsed_date` 落在窗口下界之内
    (`prev_trading_day` 返回 `None`)绝不能仍然报 `source="csindex"`——那等于把「给了日期但解析
    不出前一交易日」编码成了「我们有一个 csindex 日期」,是这个仓库反复撞见的「缺席≠否」病的
    又一个变种。诚实退化成 `(None, "none")` 并留痕,让 `phase_for` 按「解析不出」处理。

    review 2026-09-25 fix-round-2 #1:`rule_eff_close_date` 描述的是**周期性**调样的月份规律,
    一条标题带「临时」的调整公告(如 2026-09-25 live 探针抓到的「关于沪深300等指数样本临时
    调整的公告」,同一 feed 里的周期公告都读「关于调整…样本(股)的公告」,没有「临时」二字)
    即便正好落在 2/5/8/11 月,也不遵守那张周期表——`is_temporary=True` 时直接放弃规则兜底,
    退化成 `(None, "none")`,让 `phase_for` 按「解析不出」处理,而不是在一个什么都不会发生的
    夜晚点亮硬门。只挡「猜」,不挡「已解析出的日期」:`parsed_date` 给出的 after_close/from_date
    分支不受此影响——那是正文里明确写出来的日期,不是按月份猜的。
    """
    if parsed_date and kind == "after_close":
        return parsed_date, "csindex"
    if parsed_date and kind == "from_date":
        prev = prev_trading_day(parsed_date, trading_days)
        if prev is None:
            from autoresearch.data.contracts import record_degradation
            record_degradation("trade_cal", f"「{parsed_date} 起生效」在交易日窗口内找不到前一交易日"
                                            "→ 不冒充已解析的 csindex 日期,退化为未知", key=parsed_date)
            return None, "none"
        return prev, "csindex"
    if is_temporary:
        return None, "none"
    rule = rule_eff_close_date(ann_date)
    return (rule, "rule") if rule else (None, "none")


def phase_for(scan_date: str, ann_date: str, eff_close_date: str | None,
              trading_days: list[str]) -> str | None:
    """review 2026-09-25 #2/#7:E 若不是真交易日绝不 snap 到最近交易日;`unknown_eff` 若比整段
    窗口都老就出窗,不再无限期挂着。"""
    from autoresearch.data.contracts import record_degradation

    day = scan_date.replace("-", "")[:8]
    if day < ann_date:
        return None
    if not eff_close_date:
        # #7:「解析不出生效日」不能无限期挂在票上——公告比整段窗口都老,说明它早就该翻篇了。
        # 不然一条文字解析不出日期的临时调整公告,只要还留在只给最新 5 条的 list feed 里,
        # 就会一直产出行(短则几周、长则几个月),把「调仓待定」这个事实一直挂在那些代码上。
        if trading_days and ann_date < trading_days[0]:
            return None
        return "unknown_eff"
    # #2(b):生效日落在窗口内却不是交易日(疑似节假日)——只标「读不出」,决不猜哪天承接被动调仓
    # (`prev_trading_day` 会悄悄把它推到真正的调仓日,让真正的 E−1 反而落进 announced_runup —— 这
    # 正是硬门要防的方向性反转)。
    if trading_days and trading_days[0] <= eff_close_date <= trading_days[-1] and eff_close_date not in trading_days:
        record_degradation("trade_cal", f"生效日 {eff_close_date} 不在交易日历上(疑似节假日/未开市)"
                                        "→ 相位改标 unknown_eff,不猜哪一天承接被动调仓", key=eff_close_date)
        return "unknown_eff"
    e_minus_1 = prev_trading_day(eff_close_date, trading_days)
    later = [d for d in trading_days if d > eff_close_date]
    e_plus = later[POST_WINDOW - 1] if len(later) >= POST_WINDOW else (later[-1] if later else eff_close_date)
    # #2(c):窗口下界之内(或更早)找不到前一交易日——显式记账,不能靠 `day == None` 的哑比较
    # (day 永远是个日期字符串,永远不等于 None)悄悄地什么都不说。
    if e_minus_1 is None:
        record_degradation("trade_cal", f"生效日 {eff_close_date} 落在交易日窗口下界或更早,窗口内找不到"
                                        "它的前一交易日 → 无法判定 passive_close_eve", key=eff_close_date)
    elif day == e_minus_1:
        return "passive_close_eve"
    if day == eff_close_date:
        return "effective"
    if day < eff_close_date:
        return "announced_runup"
    if day <= e_plus:
        return "post"
    return None


def trading_days_window(scan_date: str, *, before: int = 40, after: int = 40) -> tuple[list[str], str]:
    """扫描日前后的交易日(含未来,E 常在湖之外)。tushare trade_cal 不可达 → 工作日近似 + 记降级。"""
    d0 = dt.datetime.strptime(scan_date[:10], "%Y-%m-%d").date()
    start = (d0 - dt.timedelta(days=before)).strftime("%Y%m%d")
    end = (d0 + dt.timedelta(days=after)).strftime("%Y%m%d")
    try:
        from autoresearch.data.tushare_source import _pro, _trade_days
        return _trade_days(_pro(), start, end), "trade_cal"
    except Exception as e:  # noqa: BLE001 — 无 token / 限频 / 断网:降级必须可见
        from autoresearch.data.contracts import record_degradation
        record_degradation("trade_cal", f"交易日历不可达({e!r})→ 调样相位按工作日近似", key=scan_date)
        days = [(dt.datetime.strptime(start, "%Y%m%d").date() + dt.timedelta(i)) for i in range(before + after + 1)]
        return [d.strftime("%Y%m%d") for d in days if d.weekday() < 5], "weekday_approx"


# ── 取数(经 cache;两条脆源路径全部 try,失败记账) ───────────────────────────
def _latest_list_snapshot(day: str) -> pd.DataFrame | None:
    """补跑/回放读湖里最新的列表快照——可能不是 `day` 当日的(review 2026-09-25 #4:这是把
    另一天的观测代入一个对时间敏感的相位计算,静默做这件事和「没有快照」一样危险,必须留痕)。
    湖命中同样要过契约(#5):它跳过了 `cache.get_or_fetch` 的每一道检查,历史脏帧读出来一样
    会毒下游,而且「是湖命中」从来不是免检的理由。
    """
    from autoresearch.data import cache
    from autoresearch.data.contracts import check, record_degradation
    files = sorted((cache.LAKE / _LIST_EP).glob("all@*.parquet"))
    if not files:
        return None
    record_degradation(_LIST_EP, f"补跑读湖内最新快照 {files[-1].name},非 {day} 当日快照", key=day)
    return check(_LIST_EP, pd.read_parquet(files[-1]), key=files[-1].stem, source="lake")


def _load_detail(ann_id: str, today: str, fetch_detail) -> pd.DataFrame | None:
    from autoresearch.data import cache
    from autoresearch.data.contracts import check, record_degradation
    cached = sorted((cache.LAKE / _DETAIL_EP).glob(f"{ann_id}@*.parquet"))
    if cached:
        # #5:公告不可变,任一份留底都算,但湖命中仍必须过契约(不能因为「是湖命中」就免检)。
        return check(_DETAIL_EP, pd.read_parquet(cached[-1]), key=cached[-1].stem, source="lake")
    try:
        return cache.get_or_fetch(_DETAIL_EP, {"ann_id": ann_id}, today=today, fetch=fetch_detail)
    except Exception as e:  # noqa: BLE001
        record_degradation(_DETAIL_EP, f"公告 {ann_id} 详情取数失败({type(e).__name__}: {e})", key=ann_id)
        return None


def build_index_events(scan_date: str, *, today: str | None = None, fetch_list=None, fetch_detail=None,
                       trading_days: list[str] | None = None, with_flow: bool = False) -> pd.DataFrame | None:
    """None = 源不可达(已记降级;调用方不落文件);空帧 = 源可达、六指数无窗口内事件。

    review 2026-09-25 #1:相位只能来自**真交易日历**。调用方没有显式传 `trading_days`(生产路径)
    且真实日历不可达、`trading_days_window` 只给出 `weekday_approx` 近似时,本函数不产表——把这一趟
    降级成「源不可达」同一世界,让硬门看到缺表就放行所有人,而不是拿一张可能把相位判反的表去挡人。
    显式传入 `trading_days` 的路径(全部测试)不受此约束——那是调用方明确信任的日历。

    `with_flow=True`(2026-09-25 §2.2 批 B3):额外算 `flow_adv_days` 描述字段(ETF 被动规模 ×
    权重代理 / ADV20)。只做描述、只填这一列——算不出(三源任一不可达)不影响事件表本身,
    列留空(None),不阻断、不重试;算出来也不进任何门/排序/评级。
    """
    from autoresearch.data import cache
    from autoresearch.data.contracts import record_degradation
    from autoresearch.data.sources.csindex import parse_effective_date

    day = scan_date.replace("-", "")[:8]
    as_of = today or day
    try:
        lst = cache.get_or_fetch(_LIST_EP, {}, today=as_of, fetch=fetch_list)
    except cache.SnapshotDateError:
        lst = _latest_list_snapshot(day)                       # 补跑/回放:只读湖里最新快照(留痕,见 #4)
        if lst is None:
            record_degradation(_LIST_EP, "补跑日无湖内列表快照,快照接口不得写假历史", key=day)
            return None
    except Exception as e:  # noqa: BLE001
        record_degradation(_LIST_EP, f"取数失败({type(e).__name__}: {e})", key=day)
        return None

    # fix-round-2 item 2:日历基准检查必须在「列表是否为空」之前——世界①(日历只能近似,不可信)
    # 与世界②(源可达但列表真的没有事件)必须保持互斥。放在空表检查之后曾会让两者在「近似日历 +
    # 恰好也是空列表」这一刻塌成同一件事(返回空帧而不是 None)。取数(上面)仍须留在检查之前,
    # 让湖在日历退化的日子照样积累它自己的按时快照。
    if trading_days is not None:
        tds = trading_days                                      # 显式传入 = 调用方信任的日历,直接用
    else:
        tds, tds_basis = trading_days_window(scan_date)
        if tds_basis != "trade_cal":                             # #1:近似日历算不出可信相位,宁可不产表
            record_degradation("trade_cal", f"交易日历退化为 {tds_basis}(非 trade_cal)"
                                            "→ 相位判定不可信,index_events 本次不产表", key=day)
            return None

    if lst is None or lst.empty:
        return pd.DataFrame(columns=EVENT_COLS)

    rows: list[dict] = []
    for ann in lst.sort_values("publish_date", ascending=False).itertuples(index=False):
        # #8(review 2026-09-25):不再用标题猜「像不像调样公告」—— list 端点本身只返回调样类
        # 公告,标题关键词过滤只会制造一种静默丢失:换个措辞(「成份股」「名单」而非「样本」)的
        # 真公告会消失进空表,读起来像「源可达无事件」,其实是「公告被我们自己的过滤器吃了」。
        # 也不省事:下面的白名单+code 判定本就会挡掉纯文字、不含调样名单的公告。
        detail = _load_detail(str(ann.ann_id), as_of, fetch_detail)
        if detail is None or detail.empty:
            continue
        head = detail.iloc[0]
        ann_date = str(head["publish_date"])
        parsed, kind = parse_effective_date(str(head["content_text"] or ""))
        # fix-round-2 item 1:标题带「临时」→ 不许走周期规则兜底(见 eff_close_from 的
        # is_temporary 文档)。用的是 list 行的标题(`ann.title`,真实公告标题),不是
        # detail 里的标题字段。
        is_temporary = "临时" in str(ann.title)
        eff, source = eff_close_from(parsed, kind, ann_date, tds, is_temporary=is_temporary)
        phase = phase_for(day, ann_date, eff, tds)
        if phase is None:
            continue
        for r in detail.itertuples(index=False):
            idx = str(r.index_code or "")[:6]
            if idx not in INDEX_WHITELIST or not r.code:
                continue
            rows.append({"code": str(r.code).zfill(6), "index_code": idx, "index_name": INDEX_WHITELIST[idx],
                         "side": r.side, "ann_date": ann_date, "eff_close_date": eff, "phase": phase,
                         "source": source, "flow_adv_days": None})
    df = pd.DataFrame(rows, columns=EVENT_COLS)
    if with_flow and len(df):
        from autoresearch.scan.index_flow import flow_adv_days
        try:
            flow = flow_adv_days(df, day)
            if flow is not None:
                df["flow_adv_days"] = flow.values
        except Exception as e:  # noqa: BLE001 — 描述字段算不出不挡事件表
            print(f"[index_events] flow_adv_days 计算失败({e!r})→ 留空", file=sys.stderr)
    return df


# ── 落盘 / 读回 ─────────────────────────────────────────────────────────────
def write_index_events(scan_dir: Path | str, df: pd.DataFrame) -> Path:
    p = Path(scan_dir) / INDEX_EVENTS_FILENAME
    df.reindex(columns=EVENT_COLS).to_csv(p, index=False)
    return p


def load_index_events(scan_dir: Path | str) -> pd.DataFrame | None:
    """缺文件 → None(源不可达 / 旋钮关);只有表头 → 空帧(源可达无事件)。字符串列不让 pandas 猜数字。"""
    p = Path(scan_dir) / INDEX_EVENTS_FILENAME
    if not p.exists():
        return None
    return pd.read_csv(p, dtype={"code": str, "index_code": str, "ann_date": str, "eff_close_date": str,
                                 "phase": str, "side": str, "source": str})


def events_by_code(df: pd.DataFrame | None) -> dict[str, list[dict]] | None:
    if df is None:
        return None
    out: dict[str, list[dict]] = {}
    for r in df.to_dict("records"):
        out.setdefault(str(r["code"]).zfill(6), []).append(r)
    return out


def harvest_index_events(scan_date: str, scan_dir: Path | str, **kw) -> pd.DataFrame | None:
    """build → 落 `index_events.csv`(源不可达时**不落文件**,让缺席保持可读)。

    `with_flow` 未显式传入(生产路径)→ 读旋钮 `calendar.index_rebalance_flow`(默认 False =
    parity,字段留空;开=多两次 tushare 调用 fund_share/fund_nav)。测试显式传入
    `with_flow=` 的路径不受此约束——那是调用方明确要的值。
    """
    kw.setdefault("with_flow", bool(knob("calendar", "index_rebalance_flow", None, False)))
    df = build_index_events(scan_date, **kw)
    if df is not None:
        write_index_events(scan_dir, df)
    else:
        print(f"[index_events] {scan_date} 中证公告源不可达 → 不落 {INDEX_EVENTS_FILENAME}(已记降级)",
              file=sys.stderr)
    return df


def main(argv: list[str] | None = None) -> int:
    import argparse
    ap = argparse.ArgumentParser(description="指数调样事件表(六指数;网络:中证公告)")
    ap.add_argument("date", help="scan 日 YYYY-MM-DD")
    a = ap.parse_args(argv)
    d = ws.scan_root() / a.date
    d.mkdir(parents=True, exist_ok=True)
    df = harvest_index_events(a.date, d)
    print("源不可达" if df is None else f"{len(df)} 行 → {d / INDEX_EVENTS_FILENAME}")
    return 0 if df is not None else 1


if __name__ == "__main__":
    raise SystemExit(main())
