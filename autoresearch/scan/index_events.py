#!/usr/bin/env python3
"""指数调样事件表 `index_events.csv`(确定性,零 LLM;design 2026-09-25 §2.2)。

一行 = (票, 指数, 调入|调出)。相位按**扫描日 D** 算:
  announced_runup    A ≤ D ≤ E−2         事实日期,不作论点
  passive_close_eve  D = E−1(E 的前一交易日)   ← E6 硬门 `rebalance_close` 命中的唯一相位
  effective          D = E
  post               E < D ≤ E+3
  unknown_eff        生效日解析不出(如「自退市日起」)
E = 被动调仓的那个收盘日:「X 日收市后生效」→ X;「X 日起生效/实施」→ X 的前一交易日;半年定期调样
(公告在 5/11 月)规则兜底 = 次月第二个周五。

六指数白名单是**产品选择**:附件里其它指数(上证380/三板…)入湖不进表。

**缺席 ≠ 否**:源不可达 → 返回 None + `record_degradation`(调用方不写文件);源可达但无窗口内事件 →
只有表头的空帧(写成只有表头的文件)。跨指数迁移(沪深300 调出 = 中证500 调入)**两行都保留**。
"""
from __future__ import annotations

import datetime as dt
import sys
from pathlib import Path

import pandas as pd

from autoresearch.common import workspace as ws

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
    """半年定期调样兜底:5 月公告 → 6 月第二个周五;11 月公告 → 12 月第二个周五;其它月份无规则。"""
    y, m = int(ann_date[:4]), int(ann_date[4:6])
    if m == 5:
        return second_friday(y, 6)
    if m == 11:
        return second_friday(y, 12)
    return None


def prev_trading_day(day: str, trading_days: list[str]) -> str | None:
    earlier = [d for d in trading_days if d < day]
    return earlier[-1] if earlier else None


def eff_close_from(parsed_date: str | None, kind: str | None, ann_date: str,
                   trading_days: list[str]) -> tuple[str | None, str]:
    """→ (被动调仓收盘日, source)。source ∈ {csindex, rule, none}。"""
    if parsed_date and kind == "after_close":
        return parsed_date, "csindex"
    if parsed_date and kind == "from_date":
        return prev_trading_day(parsed_date, trading_days), "csindex"
    rule = rule_eff_close_date(ann_date)
    return (rule, "rule") if rule else (None, "none")


def phase_for(scan_date: str, ann_date: str, eff_close_date: str | None,
              trading_days: list[str]) -> str | None:
    day = scan_date.replace("-", "")[:8]
    if day < ann_date:
        return None
    if not eff_close_date:
        return "unknown_eff"
    e_minus_1 = prev_trading_day(eff_close_date, trading_days)
    later = [d for d in trading_days if d > eff_close_date]
    e_plus = later[POST_WINDOW - 1] if len(later) >= POST_WINDOW else (later[-1] if later else eff_close_date)
    if day == e_minus_1:
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
def _looks_like_sample_adjustment(title: str) -> bool:
    return "样本" in title and any(k in title for k in ("调整", "调入", "调出"))


def _latest_list_snapshot() -> pd.DataFrame | None:
    from autoresearch.data import cache
    files = sorted((cache.LAKE / _LIST_EP).glob("all@*.parquet"))
    return pd.read_parquet(files[-1]) if files else None


def _load_detail(ann_id: str, today: str, fetch_detail) -> pd.DataFrame | None:
    from autoresearch.data import cache
    from autoresearch.data.contracts import record_degradation
    cached = sorted((cache.LAKE / _DETAIL_EP).glob(f"{ann_id}@*.parquet"))
    if cached:
        return pd.read_parquet(cached[-1])                     # 公告不可变:任一份留底都算
    try:
        return cache.get_or_fetch(_DETAIL_EP, {"ann_id": ann_id}, today=today, fetch=fetch_detail)
    except Exception as e:  # noqa: BLE001
        record_degradation(_DETAIL_EP, f"公告 {ann_id} 详情取数失败({type(e).__name__}: {e})", key=ann_id)
        return None


def build_index_events(scan_date: str, *, today: str | None = None, fetch_list=None, fetch_detail=None,
                       trading_days: list[str] | None = None) -> pd.DataFrame | None:
    """None = 源不可达(已记降级;调用方不落文件);空帧 = 源可达、六指数无窗口内事件。"""
    from autoresearch.data import cache
    from autoresearch.data.contracts import record_degradation
    from autoresearch.data.sources.csindex import parse_effective_date

    day = scan_date.replace("-", "")[:8]
    as_of = today or day
    try:
        lst = cache.get_or_fetch(_LIST_EP, {}, today=as_of, fetch=fetch_list)
    except cache.SnapshotDateError:
        lst = _latest_list_snapshot()                          # 补跑/回放:只读湖里最新快照
        if lst is None:
            record_degradation(_LIST_EP, "补跑日无湖内列表快照,快照接口不得写假历史", key=day)
            return None
    except Exception as e:  # noqa: BLE001
        record_degradation(_LIST_EP, f"取数失败({type(e).__name__}: {e})", key=day)
        return None
    if lst is None or lst.empty:
        return pd.DataFrame(columns=EVENT_COLS)

    tds = trading_days if trading_days is not None else trading_days_window(scan_date)[0]
    rows: list[dict] = []
    for ann in lst.sort_values("publish_date", ascending=False).itertuples(index=False):
        if not _looks_like_sample_adjustment(str(ann.title)):
            continue
        detail = _load_detail(str(ann.ann_id), as_of, fetch_detail)
        if detail is None or detail.empty:
            continue
        head = detail.iloc[0]
        ann_date = str(head["publish_date"])
        parsed, kind = parse_effective_date(str(head["content_text"] or ""))
        eff, source = eff_close_from(parsed, kind, ann_date, tds)
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
    return pd.DataFrame(rows, columns=EVENT_COLS)


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
    """build → 落 `index_events.csv`(源不可达时**不落文件**,让缺席保持可读)。"""
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
