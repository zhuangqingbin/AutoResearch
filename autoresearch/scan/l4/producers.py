"""L4 deterministic pledge, seat, institution, and slim producers."""
from __future__ import annotations

import re
from pathlib import Path

import pandas as pd

_SLIM_ANCHORS = (
    "## Verified market snapshot",
    "### Latest verified OHLCV row",
    "## Market context",
    "## Fundamentals overview",
)
_SLIM_CLOSE_RE = re.compile(
    r"\|\s*Close\s*\|\s*([0-9]+(?:\.[0-9]+)?)\s*\|"
)


def _tushare_pledge(code6: str) -> tuple[float, str] | None:
    """默认取数器:tushare pledge_stat 最新一期 → (ratio, end_date);失败/空 → None。"""
    from autoresearch.data.tushare_enrich import _pro, _ts_call, _tscode
    pro = _pro()
    pl = _ts_call(lambda: pro.pledge_stat(ts_code=_tscode(code6)))
    if pl is None or not len(pl):
        return None
    row = pl.sort_values("end_date").tail(1).iloc[0]
    r = pd.to_numeric(pd.Series([row["pledge_ratio"]]), errors="coerce").iloc[0]
    return None if pd.isna(r) else (float(r), str(row["end_date"]))

def fetch_pledge(scan_dir: Path | str, codes=None, fetch_fn=None,
                 reuse_days: int = 7) -> pd.DataFrame:
    """finalists 级质押取数 → `pledge.csv`(code,pledge_ratio,end_date)。零 LLM。

    近 reuse_days 内其他 scan 日已拉过的 code 直接复用(周频数据,不重拉);缺的走
    fetch_fn(默认 tushare pledge_stat,~30 calls/日远离限频),单票失败降级跳过。
    spec 2026-07-05 §5.2。
    """
    from datetime import datetime
    scan_dir = Path(scan_dir)
    if codes is None:
        fp = scan_dir / "finalists.csv"
        if not fp.exists():
            return pd.DataFrame(columns=["code", "pledge_ratio", "end_date"])
        codes = pd.read_csv(fp, dtype={"code": str})["code"].tolist()
    want = [str(c).split(".")[0].zfill(6) for c in codes]

    def _d(name: str):
        try:
            return datetime.strptime(name, "%Y-%m-%d")
        except ValueError:
            return None

    today = _d(scan_dir.name)
    rows: dict[str, dict] = {}
    if today is not None and scan_dir.parent.exists():
        for sib in sorted((p for p in scan_dir.parent.iterdir() if p.is_dir()), reverse=True):
            sd = _d(sib.name)
            if sd is None or sib == scan_dir or not 0 <= (today - sd).days <= reuse_days:
                continue
            pp = sib / "pledge.csv"
            if not pp.exists():
                continue
            try:
                prev = pd.read_csv(pp, dtype={"code": str})
            except Exception:  # noqa: BLE001
                continue
            prev["code"] = prev["code"].astype(str).str.zfill(6)
            for _, r in prev.iterrows():
                c = r["code"]
                if c in want and c not in rows:
                    rows[c] = {"code": c, "pledge_ratio": r.get("pledge_ratio"),
                               "end_date": r.get("end_date")}
    fetch_fn = fetch_fn or _tushare_pledge
    for c in want:
        if c in rows:
            continue
        try:
            got = fetch_fn(c)
        except Exception:  # noqa: BLE001 — 单票降级隔离
            got = None
        if got is not None:
            rows[c] = {"code": c, "pledge_ratio": got[0], "end_date": got[1]}
    out = pd.DataFrame([rows[c] for c in want if c in rows],
                       columns=["code", "pledge_ratio", "end_date"])
    out.to_csv(scan_dir / "pledge.csv", index=False)
    return out

def _tushare_seats_by_date(dates: list[str]) -> dict[str, pd.DataFrame]:
    """按 trade_date bulk 龙虎榜机构明细(一天一调,非逐票)。date=YYYYMMDD。"""
    from autoresearch.data.tushare_source import _pro, _ts_call
    pro = _pro()
    out: dict[str, pd.DataFrame] = {}
    for d in dates:
        try:
            df = _ts_call(lambda d=d: pro.top_inst(trade_date=d))
        except Exception:  # noqa: BLE001 — 单日降级隔离
            df = None
        if df is not None and len(df):
            out[d] = df
    return out

def fetch_seats(scan_dir: Path | str, codes=None, bulk_fn=None, reuse_days: int = 7,
                window_days: int = 20) -> pd.DataFrame:
    """finalists 龙虎榜机构 vs 游资席位聚合 → `seats.csv`(code,inst_net_wan,retail_net_wan,n_appear)。

    成本控制:`top_inst` 按日 bulk **一次**再对全 finalists 过滤聚合(非 lhb_seats 逐票×15);
    近 reuse_days 内其他 scan 日已算的 code 直接复用。mirror `fetch_pledge`。零 LLM。
    """
    from datetime import datetime, timedelta

    from autoresearch.data.tushare_source import _code6, _pro, _trade_days, resolve_momentum_dates
    scan_dir = Path(scan_dir)
    cols = ["code", "inst_net_wan", "retail_net_wan", "n_appear"]
    if codes is None:
        fp = scan_dir / "finalists.csv"
        if not fp.exists():
            return pd.DataFrame(columns=cols)
        codes = pd.read_csv(fp, dtype={"code": str})["code"].tolist()
    want = [str(c).split(".")[0].zfill(6) for c in codes]

    def _d(name: str):
        try:
            return datetime.strptime(name, "%Y-%m-%d")
        except ValueError:
            return None

    today = _d(scan_dir.name)
    rows: dict[str, dict] = {}
    # 1) 跨 scan 日复用(mirror fetch_pledge)
    if today is not None and scan_dir.parent.exists():
        for sib in sorted((p for p in scan_dir.parent.iterdir() if p.is_dir()), reverse=True):
            sd = _d(sib.name)
            if sd is None or sib == scan_dir or not 0 <= (today - sd).days <= reuse_days:
                continue
            pp = sib / "seats.csv"
            if not pp.exists():
                continue
            try:
                prev = pd.read_csv(pp, dtype={"code": str})
            except Exception:  # noqa: BLE001
                continue
            prev["code"] = prev["code"].astype(str).str.zfill(6)
            for _, r in prev.iterrows():
                c = r["code"]
                if c in want and c not in rows:
                    rows[c] = {k: r.get(k) for k in cols}
    missing = [c for c in want if c not in rows]
    # 2) 缺的:按日 bulk 一次,聚合全 missing
    if missing:
        try:
            pro = _pro()
            last = resolve_momentum_dates(pro, scan_dir.name)[0]
            start = (datetime.strptime(last, "%Y%m%d") - timedelta(days=window_days)).strftime("%Y%m%d")
            dates = _trade_days(pro, start, last)[-15:]
        except Exception:  # noqa: BLE001
            dates = []
        frames = (bulk_fn or _tushare_seats_by_date)(dates) if dates else {}
        agg = {c: {"inst": 0.0, "retail": 0.0, "n": 0} for c in missing}
        for df in frames.values():
            if df is None or not len(df):
                continue
            c6 = _code6(df["ts_code"])
            for c in missing:
                sub = df[c6 == c]
                if not len(sub):
                    continue
                agg[c]["n"] += 1
                for _, r in sub.iterrows():
                    _nb = r.get("net_buy")                       # NaN 守卫:`nan or 0 → nan` 会毒化整 code 聚合
                    net = 0.0 if (_nb is None or pd.isna(_nb)) else float(_nb)
                    if "机构专用" in str(r.get("exalter", "")):
                        agg[c]["inst"] += net
                    else:
                        agg[c]["retail"] += net
        for c in missing:
            a = agg[c]
            rows[c] = {"code": c, "inst_net_wan": round(a["inst"] / 1e4, 0),
                       "retail_net_wan": round(a["retail"] / 1e4, 0), "n_appear": a["n"]}
    out = pd.DataFrame([rows[c] for c in want if c in rows], columns=cols)
    out.to_csv(scan_dir / "seats.csv", index=False)
    return out

def fetch_consensus(scan_dir: Path | str, codes=None, window: int = 30,
                    cache_root: Path | None = None) -> pd.DataFrame:
    """finalists 卖方一致预期修正 → `consensus.csv`(code,n_reports,eps_delta_pct)。零 LLM。

    从 report_rc 缓存(consensus.pull/backfill 积累)取分析日前最近 `window` 个缓存日,
    前后对半为两窗,算 FY 一致 EPS 中位修正(research/consensus.consensus_delta)。
    缓存日 <10 → 空表不落盘(样本太薄禁注,presence-gated)。advisory:不进分、不设门。
    """
    scan_dir = Path(scan_dir)
    date = scan_dir.name
    from autoresearch.research.consensus import _dir, _load_span, consensus_delta
    stems = sorted(p.stem for p in _dir(cache_root).glob("*.pkl")
                   if p.stem <= date.replace("-", ""))[-window:]
    if len(stems) < 10:
        return pd.DataFrame(columns=["code", "n_reports", "eps_delta_pct"])
    half = len(stems) // 2
    old_span, new_span = (stems[0], stems[half - 1]), (stems[half], stems[-1])
    fy = date[:4]
    delta = consensus_delta(date, old_span, new_span, fy, cache_root=cache_root)
    recent = _load_span(new_span, cache_root)
    recent["code"] = recent["ts_code"].astype(str).str[:6]
    n_rep = recent.groupby("code").size().rename("n_reports")
    out = delta.merge(n_rep, on="code", how="left")
    out["n_reports"] = out["n_reports"].fillna(0).astype(int)
    if codes is not None:
        want = {str(c).split(".")[0].zfill(6) for c in codes}
        out = out[out["code"].isin(want)]
    out = out[["code", "n_reports", "eps_delta_pct"]].reset_index(drop=True)
    if len(out):
        out.to_csv(scan_dir / "consensus.csv", index=False)
    return out

def _tushare_fund_hold(period: str) -> pd.DataFrame | None:
    """默认取数器:tushare `fund_portfolio`(翻页取数在 `tushare_source._fetch_fund_portfolio`)。"""
    from autoresearch.data.tushare_source import _fetch_fund_portfolio, _pro
    return _fetch_fund_portfolio(_pro(), period)

def fetch_fund_hold(scan_dir: Path | str, codes=None, fetch_fn=None) -> pd.DataFrame:
    """finalists 基金重仓聚合 → `fund_hold.csv`(code,n_funds,mkv_yi,n_funds_delta)。零 LLM。

    Plan 1 Task 6 探针裁决"可用"(context/factor_lab/cache/probes/fund_portfolio_20260710.json):
    该端点不支持按个股直查(ts_code/ann_date/period 三选一起效,个股不在其中)→ 反查姿势 =
    按最近季度末(`common.scoring.latest_reported_quarter`)批量拉取(翻页,默认走
    `tushare_source._fetch_fund_portfolio`)再本地按 symbol(重仓股代码)过滤聚合。
    `n_funds_delta` = 与上一季度末(`prev_quarter`)对比家数环比。**季度滞后**
    (定期报告披露制,非实时)——advisory,不进分不设门。缺权限/端点异常/无覆盖 → 空表
    不落盘(presence-gated,mirror `fetch_pledge`)。
    """
    from autoresearch.common.scoring import latest_reported_quarter, prev_quarter
    scan_dir = Path(scan_dir)
    cols = ["code", "n_funds", "mkv_yi", "n_funds_delta"]
    if codes is None:
        fp = scan_dir / "finalists.csv"
        if not fp.exists():
            return pd.DataFrame(columns=cols)
        codes = pd.read_csv(fp, dtype={"code": str})["code"].tolist()
    want = {str(c).split(".")[0].zfill(6) for c in codes}
    if not want:
        return pd.DataFrame(columns=cols)

    def _agg(df: pd.DataFrame | None) -> pd.DataFrame:
        empty = pd.DataFrame(columns=["code", "n_funds", "mkv_yi"])
        if df is None or not len(df) or "symbol" not in df.columns or "ts_code" not in df.columns:
            return empty
        d = df.copy()
        d["code"] = d["symbol"].astype(str).str.split(".").str[0].str.zfill(6)
        d = d[d["code"].isin(want)]
        if not len(d):
            return empty
        n_funds = d.groupby("code")["ts_code"].nunique().rename("n_funds")
        mkv_src = pd.to_numeric(d["mkv"], errors="coerce") if "mkv" in d.columns else pd.Series(0.0, index=d.index)
        mkv_yi = (mkv_src.groupby(d["code"]).sum() / 1e8).rename("mkv_yi")
        return pd.concat([n_funds, mkv_yi], axis=1).reset_index()

    fetch_fn = fetch_fn or _tushare_fund_hold
    period = latest_reported_quarter(scan_dir.name)
    try:
        cur = fetch_fn(period)
    except Exception:  # noqa: BLE001 — 端点/权限降级隔离,行可选
        cur = None
    out = _agg(cur)
    if not len(out):
        return pd.DataFrame(columns=cols)

    try:
        prev_raw = fetch_fn(prev_quarter(period))
    except Exception:  # noqa: BLE001
        prev_raw = None
    prev_agg = _agg(prev_raw)
    if len(prev_agg):
        out = out.merge(prev_agg[["code", "n_funds"]].rename(columns={"n_funds": "n_funds_prev"}),
                        on="code", how="left")
        out["n_funds_delta"] = out["n_funds"] - out["n_funds_prev"].fillna(0)
        out = out.drop(columns=["n_funds_prev"])
    else:
        out["n_funds_delta"] = float("nan")
    out = out[cols].reset_index(drop=True)
    out.to_csv(scan_dir / "fund_hold.csv", index=False)
    return out

def _default_harvest_slim(ticker: str, date: str, ctx_root: Path) -> Path:
    import subprocess
    import sys

    subprocess.run(
        [sys.executable, "-m", "autoresearch.analyze.harvest", ticker, date, "stock", "--slim"],
        check=False)
    return ctx_root / f"{ticker}_{date}_slim.md"

def _slim_defect(path: Path | None, min_bytes: int) -> tuple[int, str | None]:
    """判一份 slim 能不能用。返回 (bytes, 缺陷描述);缺陷 None = 合格。

    **规模检查与结构检查分开**(2026-07-14 生产回归 + [[data-contracts-fail-fast]] 教训):
    · 结构+内容 = 能不能用的真判据(锚齐 ∧ OHLCV Close 是真数值)
    · 体积 = 只兜真垃圾(空文件/截断),**不参与"数据够不够"的判断**

    为什么不能再用体积当主判据:2026-07-14 药石科技(300725)slim **8176B,差 16 字节**没够
    8192B 门槛 —— 24 节一个不缺、行情/主力/筹码全真,只是当期新闻少几条 → 被 GATE3 误杀,
    整条流水线在 60min / 1.6M token / 33 agent 全完成后被毙。该门槛此前已因同类误杀从
    10_240B 降到 8_192B;再降一次只是把棘轮往下拧,治不了"拿体积当结构用"这个病根。
    """
    if path is None or not Path(path).exists():
        return 0, "文件不存在"
    p = Path(path)
    size = p.stat().st_size
    if size < min_bytes:                                  # 真垃圾地板(空/截断)
        return size, f"<{min_bytes}B(疑空稿/截断)"
    try:
        text = p.read_text(encoding="utf-8", errors="replace")
    except OSError as e:                                  # noqa: BLE001
        return size, f"读取失败:{e}"
    missing = [a for a in _SLIM_ANCHORS if a not in text]
    if missing:
        return size, f"结构缺块:{', '.join(missing)}"
    if not _SLIM_CLOSE_RE.search(text):
        return size, "结构齐但 OHLCV Close 无数值(NO_DATA 占位)"
    return size, None

def harvest_slim_batch(date: str, root: Path | None = None, min_bytes: int = 4_096,
                       retries: int = 1, harvest_fn=None, ctx_root: Path | None = None,
                       workers: int = 4) -> dict:
    """按 _harvest_list.txt 批量 harvest slim,**失败响亮**(修 603799 静默失败坑 = GATE 3)。

    合格判据见 `_slim_defect`:**结构+内容**决定能不能用,体积只兜真垃圾(地板 4KB)。
    offender 重试 `retries` 次仍有缺陷/含 .SH → 记失败。harvest_fn(ticker, date)->Path
    可注入(测试用),默认 shell 到 analyze.harvest --slim。

    workers=4 默认并发(spec §P3);subprocess 取数为 I/O 密集,限频靠 per-ticker retries
    串行重试承担。workers<=1 退化原串行 for 循环(兼容旧行为/便于对串行时序敏感的测试)。
    """
    base = Path(root) if root else Path("context/scan")
    scan_dir = base / date
    ctx = ctx_root or Path("context")
    tickers = [t for t in (scan_dir / "_harvest_list.txt").read_text(encoding="utf-8").split() if t]
    hv = harvest_fn or (lambda t, dt: _default_harvest_slim(t, dt, ctx))

    def _one(t: str) -> dict | None:
        """单票 harvest+判定;返回失败记录或 None(成功)。"""
        if ".SH" in t:                                    # 归一漏网(GATE 3 防线)
            return {"ticker": t, "bytes": -1, "why": ".SH 未归一"}
        size, why = 0, "未 harvest"
        for _ in range(retries + 1):
            try:
                size, why = _slim_defect(hv(t, date), min_bytes)
            except Exception as e:                        # noqa: BLE001
                size, why = 0, f"harvest 异常:{e}"
            if why is None:
                return None
        return {"ticker": t, "bytes": int(size), "why": why}

    if workers <= 1:
        results = [_one(t) for t in tickers]
    else:
        from concurrent.futures import ThreadPoolExecutor
        with ThreadPoolExecutor(max_workers=workers) as ex:
            results = list(ex.map(_one, tickers))       # map 保序 → failures 原序
    failures = [r for r in results if r]
    return {"ok": not failures, "n": len(tickers), "failures": failures}
