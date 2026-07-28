"""L3 input loading and evidence harvesting."""
from __future__ import annotations

import json
from pathlib import Path

import pandas as pd


def load_l3_input(date: str, root: Path | None = None) -> pd.DataFrame:
    """读 L2 粗排产物(L2_gbdt_top200.csv)+ 合并已 harvest 的 L3 增量证据摘要 → L3 选股输入帧。

    证据摘要列(表内一眼可见,不必逐 json 翻):lhb_n(龙虎榜上榜条数)、has_forecast/has_express
    (预告/快报有无)。证据未 harvest → 三列缺省 0/False。
    """
    import json
    root = root or Path("context/scan")
    df = pd.read_csv(root / date / "L2_gbdt_top200.csv", dtype={"code": str})
    df["code"] = df["code"].astype(str).str.zfill(6)
    ev_dir = root / date / "L3_evidence"
    if ev_dir.exists():
        rows = []
        for c in df["code"]:
            fp = ev_dir / f"{c}.json"
            if fp.exists():
                ev = json.loads(fp.read_text(encoding="utf-8"))
                rows.append({"code": c, "lhb_n": len(ev.get("longhu", [])),
                             "has_forecast": bool(ev.get("forecast")), "has_express": bool(ev.get("express"))})
            else:
                rows.append({"code": c, "lhb_n": 0, "has_forecast": False, "has_express": False})
        df = df.merge(pd.DataFrame(rows), on="code", how="left")
    # Phase 3:并入公告情感 digest(L3_news/<code>.json,缺则缺省 0/""/—)。
    from autoresearch.scan.agents.l3_news import news_digest
    news_dir = root / date / "L3_news"
    drows = []
    for c in df["code"]:
        fp = news_dir / f"{c}.json"
        anns = json.loads(fp.read_text(encoding="utf-8")) if fp.exists() else []
        drows.append({"code": c, **news_digest(anns)})
    df = df.merge(pd.DataFrame(drows), on="code", how="left")
    return df

def harvest_l3_evidence(date: str, codes: list[str], root: Path | None = None) -> dict:
    """对 L2 保留的 ~200 只补 L1 没有的真证据(龙虎榜/预告/快报)。bulk by date 一次拉、本地过滤;

    失败/无权限降级标注。产出 context/scan/<date>/L3_evidence/<code>.json,返回 {code: evidence}。
    2026-07-12 P2a:三端点改走 get_or_fetch(policy 早已注册)——已结算日湖命中零网络,预热(P1)可预拉。
    """
    import json

    from autoresearch.data import cache as _cache  # 经模块属性调用,测试可 monkeypatch
    from autoresearch.data.tushare_source import _code6, _pro, resolve_momentum_dates
    root = root or Path("context/scan")
    out_dir = root / date / "L3_evidence"
    out_dir.mkdir(parents=True, exist_ok=True)
    pro = _pro()
    last = resolve_momentum_dates(pro, date)[0]
    want = {str(c).zfill(6) for c in codes}
    ev: dict[str, dict] = {c: {"code": c} for c in want}

    def _bulk(label, fn, key_field="ts_code"):
        try:
            df = fn()
            if df is None or df.empty:
                return
            df = df.assign(_c=_code6(df[key_field]))
            for c, g in df[df["_c"].isin(want)].groupby("_c"):
                ev[c].setdefault(label, []).extend(g.drop(columns=["_c"]).to_dict("records"))  # 累积(可多日)
        except Exception as e:  # noqa: BLE001
            ev.setdefault("_errors", {}).setdefault(label, str(e))   # 端点级错误记一次,不污染每只

    _bulk("longhu", lambda: _cache.get_or_fetch("top_list", {"trade_date": last}, today=date))  # 龙虎榜席位(游资/机构)
    # forecast/express 需 ann_date 或 ts_code(period 单参不够)→ 扫最近 ~10 个交易日的公告
    from datetime import datetime, timedelta

    from autoresearch.data.tushare_source import _trade_days
    start = (datetime.strptime(last, "%Y%m%d") - timedelta(days=30)).strftime("%Y%m%d")
    for dd in _trade_days(pro, start, last)[-10:]:
        _bulk("forecast", lambda dd=dd: _cache.get_or_fetch("forecast", {"ann_date": dd}, today=date))   # 业绩预告
        _bulk("express", lambda dd=dd: _cache.get_or_fetch("express", {"ann_date": dd}, today=date))     # 快报
    for c in want:
        (out_dir / f"{c}.json").write_text(json.dumps(ev[c], ensure_ascii=False, default=str), encoding="utf-8")
    return ev
