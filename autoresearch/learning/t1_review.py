#!/usr/bin/env python3
"""scan-market · T+1 判断层复盘快环(确定性层,零 LLM)。

用户裁定 2026-07-17(fb_20260717_001):
  自学习快环 = **T 报告的真选票 vs T+1 收盘**——T 推的这些票,T+1 收盘哪些准哪些不准、
  为什么、不准的如何优化、准的如何强化。保送(pinned)不算:判断层复盘只考它自己选的票。
  只做 T→T+1 **相邻交易日**间隔(周末/节假日顺延;不看更长 horizon——票会太多,且超短
  语义只关心次日)。T+1 当晚即可复盘,比慢环(retro,D+2 成熟)快一个交易日。

与慢环 retro 的分工(勿混,也勿建平行实现——本模块复用 factor_lab 取数/日历原语):
  retro     = 漏斗召回归因(全市场谁涨了没进池),D+2,喂**权重**重标定(唯一自动腿)。
  t1_review = 判断层精度(L3 选中 + L4 评级的票,次日兑现如何、为什么),D+1,
              喂 **prompt 侧**经验/提案(人批;诊断叙事由 t1-review workflow 的 agent 做)。

两把尺(勿混,项目有尺子错配的疤):
  本环主尺 = cc1(T 收盘 → T+1 收盘;判断对错的信息尺,= T+1 当日 pct_chg 口径);
  参考尺   = oc1(T+1 开 → T+1 收;可实现口径,报告晚上出、最早 T+1 开盘才能建仓)。
  持仓/权重校准主尺仍 = fwd_2_oc(2026-07-10 裁定),本环**不动它**。

产物:context/scan/<T>/t1_review/{scorecard.csv,scorecard.md,diagnoses.json,report.md,done.json}
账本:context/learning/t1_review.jsonl(逐票行,按 T 日幂等整替)

  uv run --no-sync python -m autoresearch.learning.t1_review pending          # 待复盘对
  uv run --no-sync python -m autoresearch.learning.t1_review build 2026-07-16 --json
  uv run --no-sync python -m autoresearch.learning.t1_review finalize 2026-07-16
  uv run --no-sync python -m autoresearch.learning.t1_review backfill 2026-07-14
  uv run --no-sync python -m autoresearch.learning.t1_review report
"""
from __future__ import annotations

import argparse
import json
import re
from datetime import datetime, timedelta
from pathlib import Path

import pandas as pd

# 保送/观察单直通(已退役 fb_20260714_002)/菜单滞回(已退役 pr_20260716_006)——都不是
# L3 当日选的票,不进判断层成绩;历史 scan 目录仍有后两种 lane 的存量行,故三者都留集合里。
_NON_GENUINE_LANES = {"pinned", "watchlist_trigger", "carryover"}
_DIR_THR = 0.015       # 方向判定阈(**legacy 回退**,仅当 z 不可得):|超额| ≥ 1.5pp
_SURPRISE_THR = 0.03   # 惊奇阈(legacy 回退):|超额| ≥ 3pp
# ── v2 尺(2026-07-17 调研落地:短线信号业界口径 = 行业中性 + 截面稳健 z,盖帽 ±3)──
# 固定 pp 阈的病:暴跌日截面离散度巨大,±1.5pp 满地都是"准";平静日又太钝。
# z = 行业内超额 / 截面稳健σ(1.4826×MAD),随日自适应;行业中性先把 β/板块共振剥掉
# (07-16 首跑 4/5 票被诊"市场β"= 这笔账本该由确定性层付,不该烧 agent)。
_Z_DIR = 0.5           # 方向判定:|z| ≥ 0.5 且 |行业超额| ≥ 0.8pp(双门,防微离散日噪声)
_Z_SURPRISE = 1.5      # 惊奇:|z| ≥ 1.5 必诊
_MIN_EXCESS = 0.008    # 方向判定的绝对 pp 地板
_EPOCH = "2026-07-10"  # 卡契约 v3 起点;更早的旧 swing 语义卡不回补(尺子语义不同)
_LEDGER = Path("context/learning/t1_review.jsonl")


# ───────────────────────── 纯函数:判定 ─────────────────────────


_L4_CONF_RE = re.compile(r"置信度[::]\s*(高|中|低)")


def _l4_confidence(scan_dir: Path, code6: str) -> str:
    """卡里的 **L4 自己的**置信度(高/中/低);读不到 → ""。

    Wave7 P3:既有的 conviction 校准曲线量的是 **L3** 的 0-100 分(来自 finalists.csv),
    而"深核完这一只之后你有多确信"是另一个量 —— 后者才是 L4 这一层的自我校准。
    L4 的**数值** conviction 只活在 workflow 返回值里、从未落盘(pr_20260717_005 那次
    标度不一致也是同一个原因),但卡片正文一直写着定性的「置信度: 高/中/低」——
    零新数据就能建起这条曲线,不必为此改编排层去持久化一个数字。
    """
    p = Path(scan_dir) / "details" / f"{code6}.md"
    if not p.exists():
        return ""
    try:
        m = _L4_CONF_RE.search(p.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return ""
    return m.group(1) if m else ""


def verdict(rating: str, excess: float | None, z: float | None = None) -> str:
    """方向判定。OW/Buy 期望超额+、UW/Sell 期望超额-、Hold 无方向主张(记 —)。

    v2(z 给定):判「准/不准」需双门——|z| ≥ _Z_DIR(截面显著)且 |excess| ≥ _MIN_EXCESS
    (绝对地板);z=None(老数据/行业缺)→ legacy 固定 pp 阈。excess 为空 → '缺价'。
    """
    if excess is None or pd.isna(excess):
        return "缺价"
    if z is not None and not pd.isna(z):
        hi = z >= _Z_DIR and excess >= _MIN_EXCESS
        lo = z <= -_Z_DIR and excess <= -_MIN_EXCESS
        if rating in ("Overweight", "Buy"):
            return "准" if hi else ("不准" if lo else "中性")
        if rating in ("Underweight", "Sell"):
            return "准" if lo else ("不准" if hi else "中性")
        return "—"
    if rating in ("Overweight", "Buy"):
        return "准" if excess >= _DIR_THR else ("不准" if excess <= -_DIR_THR else "中性")
    if rating in ("Underweight", "Sell"):
        return "准" if excess <= -_DIR_THR else ("不准" if excess >= _DIR_THR else "中性")
    return "—"


def _robust_sigma(resid: pd.Series) -> float:
    """截面稳健σ = 1.4826 × MAD(业界短线信号标准口径;对涨跌停厚尾稳健)。"""
    r = pd.to_numeric(resid, errors="coerce").dropna()
    if len(r) < 30:
        return float("nan")
    mad = float((r - r.median()).abs().median())
    return 1.4826 * mad if mad > 1e-6 else float("nan")


def _limit_mark(code: str, cc1: float | None) -> str:
    """涨跌停近似旗(≥板幅×0.98 记 ≈涨停/≈跌停;近似口径,诚实标 ≈)。一字板≈不可交易。"""
    if cc1 is None or pd.isna(cc1):
        return ""
    from autoresearch.research.factor_lab import _board_limit
    lim = _board_limit(str(code)) / 100.0
    if cc1 >= lim * 0.98:
        return "≈涨停"
    if cc1 <= -lim * 0.98:
        return "≈跌停"
    return ""


# ───────────────────────── 日历 / 取价(可注入,测试离线) ─────────────────────────


def next_trade_day(t: str, cal: list[str] | None = None) -> str | None:
    """T 的下一交易日(YYYY-MM-DD);T 非交易日或日历不含 → None。cal 可注入(测试)。"""
    d0 = t.replace("-", "")
    if cal is None:
        import autoresearch.research.factor_lab as fl
        from autoresearch.data.tushare_source import _trade_days
        end = (datetime.strptime(t, "%Y-%m-%d") + timedelta(days=20)).strftime("%Y%m%d")
        cal = _trade_days(fl._pro(), d0, end)          # 交易所日历含未来日,跨长假足够
    cal = [c.replace("-", "") for c in cal if c.replace("-", "") >= d0]
    if not cal or cal[0] != d0 or len(cal) < 2:
        return None
    n = cal[1]
    return f"{n[:4]}-{n[4:6]}-{n[6:]}"


def _fetch_prices(t: str, t1: str) -> pd.DataFrame:
    """两日 daily 入 factor_lab 缓存 → 全市场帧 [code, close_t, open_t1, close_t1, cc1, oc1, hi_oc]。

    T+1 daily 空(未结算/未发布)→ ValueError 诚实失败——**绝不静默返回空帧**
    (数据契约铁律:降级不留痕才是真病;空帧会让准率算在 0 只票上照样退出码 0)。
    """
    import autoresearch.research.factor_lab as fl
    d0, d1 = t.replace("-", ""), t1.replace("-", "")
    pro = fl._pro()
    for d in (d0, d1):
        fl._cache("daily", d, fl._fetch(pro, "daily", d))
    piv = fl.load_price_pivots([d0, d1])
    cl, op, hi = piv["close"], piv["open"], piv["high"]
    if d1 not in cl.columns or cl[d1].notna().sum() < 100:
        raise ValueError(f"T+1({t1}) daily 未结算/未发布(通常 17:00 后可用),稍后再试")
    if d0 not in cl.columns:
        raise ValueError(f"T({t}) daily 缺失,无法计算 cc1")
    out = pd.DataFrame({
        "close_t": cl[d0], "open_t1": op[d1], "close_t1": cl[d1],
    })
    out["cc1"] = out["close_t1"] / out["close_t"] - 1.0
    out["oc1"] = out["close_t1"] / out["open_t1"] - 1.0
    out["hi_oc"] = hi[d1] / out["open_t1"] - 1.0
    out = out.reset_index().rename(columns={"index": "code", "ts_code": "code"})
    try:                                        # 行业映射(行业中性用);缺 → 退市场基准
        basic = fl._load_basic()
        out = out.merge(basic[["code", "industry"]], on="code", how="left")
    except Exception:  # noqa: BLE001
        pass
    return out


# ───────────────────────── 记分卡 ─────────────────────────


def build_scorecard(t: str, scan_root: Path | str | None = None,
                    prices: pd.DataFrame | None = None,
                    cal: list[str] | None = None) -> dict:
    """构建 T→T+1 记分卡。返回 {"t","t1","market_cc","scorecard","excluded"}。

    - 真选 = finalists 里 lane 不在 `_NON_GENUINE_LANES` 的行(用户裁定:保送不算);
    - 评级 = health.final_ratings(与 assemble/发布报告**同口径**,verify 降级已折回);
    - 基准 = 全市场等权 cc1 均值(prices 帧全量算,再筛真选行)。
    prices/cal 可注入(测试离线);prices 需含全市场行(基准要全量)。
    """
    scan_root = Path(scan_root or "context/scan")
    sdir = scan_root / t
    fp = sdir / "finalists.csv"
    if not fp.exists():
        raise FileNotFoundError(f"{fp} 不存在:{t} 无 finalist,无从复盘")
    fin = pd.read_csv(fp, dtype=str)
    fin["code"] = fin["code"].str.zfill(6)
    lane = fin["lane"] if "lane" in fin.columns else pd.Series("", index=fin.index)
    lane = lane.fillna("")
    excluded = {ln: int((lane == ln).sum()) for ln in sorted(_NON_GENUINE_LANES) if (lane == ln).any()}
    genuine = fin[~lane.isin(_NON_GENUINE_LANES)].copy()

    t1 = next_trade_day(t, cal)
    if t1 is None:
        raise ValueError(f"{t} 非交易日或下一交易日未知,无 T+1 可复盘")
    if prices is None:
        prices = _fetch_prices(t, t1)
    prices = prices.copy()
    prices["code"] = prices["code"].astype(str).str.zfill(6)
    market_cc = float(pd.to_numeric(prices["cc1"], errors="coerce").mean())

    from autoresearch.scan.health import final_ratings
    ratings = final_ratings(sdir)

    # ── v2:行业中性超额 + 截面稳健 z(把 β/板块共振留给确定性层,agent 只诊真 idio)──
    cc_all = pd.to_numeric(prices["cc1"], errors="coerce")
    if "industry" in prices.columns and prices["industry"].notna().any():
        ind_cnt = prices.groupby("industry")["code"].transform("count")
        ind_mean = prices.groupby("industry")["cc1"].transform("mean")
        bench = ind_mean.where(ind_cnt >= 3, market_cc)     # 小行业(<3 只)退市场基准
        prices = prices.assign(_bench=pd.to_numeric(bench, errors="coerce").fillna(market_cc))
    else:
        prices = prices.assign(_bench=market_cc)
    resid_all = cc_all - prices["_bench"]
    sigma = _robust_sigma(resid_all)

    rows = genuine.merge(prices, on="code", how="left")
    rows["rating"] = rows["code"].map(ratings).fillna("无卡")
    # Wave5 ②C:早停卡不判准/不准(Hold 无方向主张),但它们的 cc1 分布是「早停杀对了没有」
    # 的第一手证据 —— 进表进桶,解快环"真选全 Hold 无从评"的样本饥饿。
    import json as _json
    _esp = sdir / "_early_stop.json"
    _es: dict = {}
    if _esp.is_file():
        try:
            _es = _json.loads(_esp.read_text(encoding="utf-8")) or {}
        except Exception:  # noqa: BLE001 — 缺/坏文件只退化为空列
            _es = {}
    rows["early_stop"] = rows["code"].map(
        lambda c: (_es.get(str(c).zfill(6)) or {}).get("reason", ""))
    rows["l4_conf"] = rows["code"].map(lambda c: _l4_confidence(sdir, str(c).zfill(6)))
    rows["conviction"] = pd.to_numeric(rows.get("conviction"), errors="coerce")
    rows["excess"] = pd.to_numeric(rows["cc1"], errors="coerce") - market_cc
    rows["excess_ind"] = pd.to_numeric(rows["cc1"], errors="coerce") - rows["_bench"]
    rows["z"] = (rows["excess_ind"] / sigma).clip(-3, 3) if pd.notna(sigma) else float("nan")
    rows["verdict"] = [verdict(r, e, z) for r, e, z in
                       zip(rows["rating"], rows["excess_ind"], rows["z"], strict=True)]
    zs = pd.to_numeric(rows["z"], errors="coerce")
    rows["surprise"] = zs.abs().ge(_Z_SURPRISE).fillna(False) if zs.notna().any() \
        else rows["excess"].abs() >= _SURPRISE_THR
    rows["limit"] = [_limit_mark(c, v) for c, v in zip(rows["code"], rows["cc1"], strict=True)]
    # 一字开盘板(开=盘中高 且 涨幅近板)= T+1 开盘买不到 → 不计入可实现统计
    from autoresearch.research.factor_lab import _board_limit
    lim = rows["code"].map(lambda c: _board_limit(str(c)) / 100.0)
    rows["sealed"] = (pd.to_numeric(rows.get("hi_oc"), errors="coerce").abs() <= 1e-9) \
        & (pd.to_numeric(rows["cc1"], errors="coerce") >= lim * 0.98)
    rows["sealed"] = rows["sealed"].fillna(False)
    # 分诊(ERL:失败启发式 > 成功启发式;token 花在错与惊奇上):不准 / 惊奇 / 方向票 |z|≥1
    rows["needs_diag"] = (rows["verdict"] == "不准") | rows["surprise"] \
        | (rows["verdict"].isin(["准", "中性"]) & zs.abs().ge(1.0).fillna(False))
    keep = ["code", "name", "lane", "rating", "conviction", "close_t", "close_t1",
            "cc1", "oc1", "hi_oc", "excess", "excess_ind", "z", "verdict", "surprise",
            "sealed", "needs_diag", "limit", "early_stop", "l4_conf"]
    sc = rows[[c for c in keep if c in rows.columns]].sort_values(
        "excess_ind", ascending=False, na_position="last").reset_index(drop=True)
    return {"t": t, "t1": t1, "market_cc": round(market_cc, 6),
            "sigma": None if pd.isna(sigma) else round(float(sigma), 5),
            "scorecard": sc, "excluded": excluded}


def render_scorecard_md(res: dict) -> str:
    """紧凑 md(供诊断 agent 读 + 嵌综合报告)。百分数一位小数;剔除声明必须在场。"""
    sc, pct = res["scorecard"], lambda v: ("—" if pd.isna(v) else f"{v * 100:+.1f}%")
    has_z = "z" in sc.columns and pd.to_numeric(sc["z"], errors="coerce").notna().any()
    lines = [f"# T+1 记分卡 · {res['t']} 报告 → {res['t1']} 收盘",
             f"- 市场基准(全A等权 cc1):{pct(res['market_cc'])};真选 {len(sc)} 只"
             + (";剔除不计入:" + "、".join(f"{k}×{v}" for k, v in res["excluded"].items())
                if res["excluded"] else ";无保送/存量豁免行"),
             "- 尺:cc1 = T收→T+1收;行业超额 = cc1 − 同业均值(已剥板块共振);"
             "z = 行业超额/截面稳健σ(判定尺,自适应当日离散度);oc1 = T+1开→收(可实现参考)",
             "", "| 代码 | 名称 | 评级 | conv | cc1 | oc1 | 市场超额 | 行业超额 | z | 判定 | 旗 |",
             "|---|---|---|---|---|---|---|---|---|---|---|"]
    for _, r in sc.iterrows():
        conv = "—" if pd.isna(r.get("conviction")) else f"{r['conviction']:.0f}"
        zs = "—" if not has_z or pd.isna(r.get("z")) else f"{r['z']:+.2f}"
        flag = " ".join(x for x in [
            r.get("limit", ""), "🔒一字板" if r.get("sealed") else "",
            "⚡惊奇" if r.get("surprise") else "", "🩺必诊" if r.get("needs_diag") else ""] if x)
        lines.append(f"| {r['code']} | {r.get('name', '')} | {r['rating']} | {conv} "
                     f"| {pct(r['cc1'])} | {pct(r.get('oc1'))} | {pct(r['excess'])} "
                     f"| {pct(r.get('excess_ind'))} | {zs} | {r['verdict']} | {flag} |")
    d = sc[sc["verdict"].isin(["准", "不准", "中性"])]
    nd = int(sc["needs_diag"].sum()) if "needs_diag" in sc.columns else len(sc)
    lines += ["", f"方向票判定:准 {int((d['verdict'] == '准').sum())} / "
                  f"不准 {int((d['verdict'] == '不准').sum())} / 中性 {int((d['verdict'] == '中性').sum())}"
                  f";Hold(无方向主张){int((sc['verdict'] == '—').sum())} 只;"
                  f"🩺必诊 {nd} 只(其余一句话带过,token 花在错与惊奇上)"]
    # 早停桶(Wave5 ②C):不判准/不准 —— Hold 无方向主张,只记分布。真选全 Hold 的日子
    # (最近 5 次里有 3 次)此前整张记分卡无信号可读,这一段是那些日子唯一的读数。
    if "early_stop" in sc.columns and (sc["early_stop"].astype(str) != "").any():
        es = sc[sc["early_stop"].astype(str) != ""]
        lines += ["", f"**早停桶**({len(es)} 张;不判准/不准,只记分布):"]
        for reason, g in es.groupby("early_stop"):
            cc = pd.to_numeric(g["cc1"], errors="coerce")
            avg = "—" if not cc.notna().any() else f"{cc.mean() * 100:+.1f}%"
            lines.append(f"- {reason} ×{len(g)}:T+1 cc1 均值 {avg}"
                         f"({'、'.join(str(c) for c in g['code'])})")
    return "\n".join(lines) + "\n"


# ───────────────────────── 账本 / 状态 ─────────────────────────


def append_ledger(res: dict, diagnoses: dict[str, dict] | None = None,
                  path: Path | str | None = None) -> int:
    """逐票行落账本;按 T 日幂等(同日旧行整替,重跑安全)。diagnoses={code: {mechanism, why}}。"""
    path = Path(path or _LEDGER)
    path.parent.mkdir(parents=True, exist_ok=True)
    old = []
    if path.exists():
        old = [json.loads(ln) for ln in path.read_text(encoding="utf-8").splitlines() if ln.strip()]
    old = [r for r in old if r.get("t") != res["t"]]
    ts = datetime.now().isoformat(timespec="seconds")
    diagnoses = diagnoses or {}
    new = []
    def _num(v, nd=5):
        return None if v is None or pd.isna(v) else round(float(v), nd)

    for _, r in res["scorecard"].iterrows():
        d = diagnoses.get(str(r["code"]), {})
        new.append({"t": res["t"], "t1": res["t1"], "code": str(r["code"]),
                    "name": r.get("name"), "rating": r["rating"],
                    "conviction": _num(r.get("conviction"), 1),
                    "l4_conf": r.get("l4_conf", ""),
                    "cc1": _num(r["cc1"]), "oc1": _num(r.get("oc1")),
                    "excess": _num(r["excess"]),
                    "excess_ind": _num(r.get("excess_ind")), "z": _num(r.get("z"), 3),
                    "verdict": r["verdict"], "surprise": bool(r["surprise"]),
                    "sealed": bool(r.get("sealed", False)),
                    "limit": r.get("limit") or "",
                    "mechanism": d.get("mechanism"), "why": d.get("why"),
                    "stage": d.get("stage"),
                    "diagnosed": bool(d), "ts": ts})
    path.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in old + new),
                    encoding="utf-8")
    return len(new)


def ledger_tail_summary(k: int = 10, path: Path | str | None = None) -> dict:
    """近 k 个 T 日的聚合(供综合 agent 查「重复模式」:同 mechanism 反复出现 → 候选经验)。"""
    path = Path(path or _LEDGER)
    if not path.exists():
        return {"days": 0, "n": 0, "direction": {}, "mechanisms": {}}
    rows = [json.loads(ln) for ln in path.read_text(encoding="utf-8").splitlines() if ln.strip()]
    days = sorted({r["t"] for r in rows})[-k:]
    rows = [r for r in rows if r["t"] in days]
    dirs = [r for r in rows if r["verdict"] in ("准", "不准", "中性")]
    mech: dict[str, int] = {}
    for r in rows:
        if r.get("mechanism"):
            mech[r["mechanism"]] = mech.get(r["mechanism"], 0) + 1
    return {"days": len(days), "n": len(rows),
            "direction": {v: sum(1 for r in dirs if r["verdict"] == v) for v in ("准", "不准", "中性")},
            "mechanisms": dict(sorted(mech.items(), key=lambda kv: -kv[1]))}


# ───────────────────── 自我迭代腿(2026-07-17 用户裁定:不能止步于复盘文档) ─────────────────────
#
# 全链:综合官写 candidates.json(稳定 key)→ finalize 记入候选账本(逐日计 n_days)
#   → 次日 L3 表自动注入「复盘观察(n=…)」(render_t1_calibration_block,advisory 数据非指令)
#   → 同 key 累计 n_days≥2 自动立案 proposals.jsonl(prompt_rule,一键人批)
#   → 人批成 lesson 后由 feedback_store.render_calibration_block 注入(pr_20260716_005 同波接线)。
# 半自动边界不变:自动的是「观察的注入」与「提案的起草」,改规则/prompt 文件仍人批。

_CAND_LEDGER = Path("context/learning/t1_candidates.jsonl")
_PROMOTE_N_DAYS = 2      # 同 key 出现 ≥2 个 T 日 → 自动立案(防单日噪声直通提案板)


def load_candidates(path: Path | str | None = None) -> list[dict]:
    p = Path(path or _CAND_LEDGER)
    if not p.exists():
        return []
    return [json.loads(ln) for ln in p.read_text(encoding="utf-8").splitlines() if ln.strip()]


def upsert_candidates(t: str, items: list[dict], path: Path | str | None = None) -> list[dict]:
    """把某日综合官的候选并进账本(按 key upsert;同日重跑幂等——days 集合去重)。

    items = [{"key": 短横线稳定slug, "text": 一句话}];key 由综合官起,prompt 会把既有
    open key 喂回去促其复用(跨日收敛靠这个)。返回更新后的全账本。
    """
    p = Path(path or _CAND_LEDGER)
    p.parent.mkdir(parents=True, exist_ok=True)
    recs = {r["key"]: r for r in load_candidates(p)}
    for it in items or []:
        key = str(it.get("key", "")).strip()
        if not key:
            continue
        r = recs.get(key) or {"key": key, "days": [], "texts": [], "filed_pr": None,
                              "stage": None}
        if t not in r["days"]:
            r["days"] = sorted(set(r["days"]) | {t})
        txt = str(it.get("text", "")).strip()
        if txt and txt not in r["texts"]:
            r["texts"] = (r["texts"] + [txt])[-3:]          # 只留最近 3 版表述
        if it.get("stage"):                                  # 归责路由:L3/L4/intel/gate/process
            r["stage"] = str(it["stage"])
        recs[key] = r
    out = sorted(recs.values(), key=lambda r: (-len(r["days"]), r["key"]))
    p.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in out), encoding="utf-8")
    return out


def promote_candidates(path: Path | str | None = None, add_proposal=None) -> list[str]:
    """候选 → 提案的自动起草:同 key 累计 ≥_PROMOTE_N_DAYS 个 T 日且未立案 → add_proposal。

    只起草不裁决(kind=prompt_rule,status=open 等人批);立案后回写 filed_pr 防重复起草。
    """
    p = Path(path or _CAND_LEDGER)
    recs = load_candidates(p)
    if add_proposal is None:
        from autoresearch.learning.feedback_store import add_proposal as _ap
        add_proposal = _ap
    filed: list[str] = []
    for r in recs:
        if r.get("filed_pr") or len(r.get("days", [])) < _PROMOTE_N_DAYS:
            continue
        rec = add_proposal(
            kind="prompt_rule",
            summary=f"T1快环·重复模式自动立案:{r['key']}(已在 {len(r['days'])} 个 T 日独立出现)",
            rationale=("T+1 判断层复盘快环(fb_20260717_001)跨日收敛的候选经验,自动起草待人批。\n"
                       f"出现日:{('、'.join(r['days']))}\n最近表述:\n"
                       + "\n".join(f"  - {t}" for t in r.get("texts", []))),
            diff_sketch="人批后走 lesson 裁决(ADD)或 prompt_patch;经验注入面 = "
                        "feedback_store.render_calibration_block(L3 表已接线)。",
        )
        r["filed_pr"] = rec["id"]
        filed.append(rec["id"])
    if filed:
        p.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in recs),
                     encoding="utf-8")
    return filed


def render_t1_calibration_block(k: int = 10, path: Path | str | None = None,
                                cand_path: Path | str | None = None,
                                stage: str | None = None) -> str:
    """「T+1 快环校准块」(纯账本派生数据,advisory 非指令,零人批自动注入)。

    stage 路由(ERL 教训:insight 无差别全量注入随规模退化,检索相关性 > 数量):
    'L3' → 只带 stage∈{L3,gate,process,None} 的观察(注 L3 表);
    'L4' → 只带 stage∈{L4,intel} 的观察(注 L4 共享指令);None → 全量(报告/人看)。
    内容:近 k 日方向票准率 + 机制直方图 + 复盘观察(候选,带 n_days/立案状态)。
    账本空 → ""(presence-gated,零字节 = parity)。防锚定:只给「上次错在哪」的事实,
    不给「今天该选谁」的指令——与 market_view 地形段同一防锚定哲学。
    """
    _ROUTE = {"L3": {"L3", "gate", "process", None, ""},
              "L4": {"L4", "intel"}}
    tail = ledger_tail_summary(k, path)
    cands = load_candidates(cand_path)
    if stage in _ROUTE:
        cands = [r for r in cands if (r.get("stage") or None) in _ROUTE[stage]]
    if not tail["n"] and not cands:
        return ""
    who = f",{stage} 相关" if stage else ""
    lines = [f"## 🔄 T+1 快环校准(近 {tail['days']} 个 T 日 {tail['n']} 只次;数据非指令{who})"]
    d = tail["direction"]
    n_dir = sum(d.values())
    if n_dir:
        lines.append(f"- 方向票:准 {d.get('准', 0)} / 不准 {d.get('不准', 0)} / 中性 {d.get('中性', 0)}"
                     + ("(⚠n<10 只看不裁)" if n_dir < 10 else ""))
    if tail["mechanisms"]:
        lines.append("- 兑现机制直方图:" + "、".join(f"{m}×{c}" for m, c in
                                                     list(tail["mechanisms"].items())[:5]))
    for r in cands[:5]:
        st = f"已立案 {r['filed_pr']} 待批" if r.get("filed_pr") else f"n={len(r['days'])} 日,观察中"
        tag = f"·{r['stage']}" if r.get("stage") else ""
        txt = (r.get("texts") or [""])[-1]
        lines.append(f"- 复盘观察〔{st}{tag}〕:{txt[:90]}")
    return "\n".join(lines)


def pending_pairs(today: str | None = None, scan_root: Path | str | None = None,
                  cal: list[str] | None = None, max_pairs: int = 5) -> list[dict]:
    """待复盘 (T, T+1) 对:有 finalists+details、无 t1_review/done.json、T+1 ≤ today。

    只回补 `_EPOCH`(卡契约 v3)之后的日子;最多回 max_pairs 对(旧→新)。
    T+1 == today 也算(当晚数据结算后即可复盘;未结算 build 会诚实失败)。
    """
    today = today or datetime.now().strftime("%Y-%m-%d")
    scan_root = Path(scan_root or "context/scan")
    if not scan_root.exists():
        return []
    if cal is None:
        import autoresearch.research.factor_lab as fl
        from autoresearch.data.tushare_source import _trade_days
        cal = _trade_days(fl._pro(), _EPOCH.replace("-", ""), today.replace("-", ""))
    cal = [c.replace("-", "") for c in cal]
    pos = {d: i for i, d in enumerate(cal)}
    out = []
    for dd in sorted(p for p in scan_root.iterdir() if p.is_dir()):
        t = dd.name
        if t < _EPOCH or not (dd / "finalists.csv").exists():
            continue
        if not (dd / "details").is_dir() or not any((dd / "details").glob("*.md")):
            continue
        if (dd / "t1_review" / "done.json").exists():
            continue
        i = pos.get(t.replace("-", ""))
        if i is None or i + 1 >= len(cal):        # T 非交易日 / 日历里没有 T+1
            continue
        n = cal[i + 1]
        if n > today.replace("-", ""):            # T+1 还没到(显式校验,不依赖日历截断)
            continue
        out.append({"t": t, "t1": f"{n[:4]}-{n[4:6]}-{n[6:]}"})
    return out[-max_pairs:]


def mark_done(t: str, mode: str, summary: dict | None = None,
              scan_root: Path | str | None = None) -> Path:
    p = Path(scan_root or "context/scan") / t / "t1_review" / "done.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps({"date": t, "mode": mode, "ts": datetime.now().isoformat(timespec="seconds"),
                             "summary": summary or {}}, ensure_ascii=False), encoding="utf-8")
    return p


# ───────────────────────── 编排入口(CLI 调) ─────────────────────────


def _sanitize(o):
    if isinstance(o, float) and pd.isna(o):
        return None
    if isinstance(o, dict):
        return {k: _sanitize(v) for k, v in o.items()}
    if isinstance(o, list):
        return [_sanitize(v) for v in o]
    return o


def _stage(res: dict, scan_root: Path | str | None = None) -> Path:
    """记分卡落 staging(csv + md + build_meta.json);build 只发生一次,后续步骤读盘。"""
    out_dir = Path(scan_root or "context/scan") / res["t"] / "t1_review"
    out_dir.mkdir(parents=True, exist_ok=True)
    res["scorecard"].to_csv(out_dir / "scorecard.csv", index=False)
    (out_dir / "scorecard.md").write_text(render_scorecard_md(res), encoding="utf-8")
    (out_dir / "build_meta.json").write_text(json.dumps(
        {"t": res["t"], "t1": res["t1"], "market_cc": res["market_cc"],
         "sigma": res.get("sigma"), "excluded": res["excluded"]},
        ensure_ascii=False), encoding="utf-8")
    return out_dir


def _load_staged(t: str, scan_root: Path | str | None = None) -> dict:
    """从 staging 复原 res(finalize 不重建、不碰网络——build 后数据修订也不会让数字漂)。"""
    sdir = Path(scan_root or "context/scan") / t / "t1_review"
    meta = json.loads((sdir / "build_meta.json").read_text(encoding="utf-8"))
    sc = pd.read_csv(sdir / "scorecard.csv", dtype={"code": str})
    sc["code"] = sc["code"].str.zfill(6)
    for c in ("conviction", "close_t", "close_t1", "cc1", "oc1", "hi_oc",
              "excess", "excess_ind", "z"):
        if c in sc.columns:
            sc[c] = pd.to_numeric(sc[c], errors="coerce")
    # csv 往返 bool 变串 → 重推导(z 在场用 z 口径,老 staging 退 legacy)
    zs = pd.to_numeric(sc.get("z"), errors="coerce") if "z" in sc.columns else None
    if zs is not None and zs.notna().any():
        sc["surprise"] = zs.abs().ge(_Z_SURPRISE).fillna(False)
    else:
        sc["surprise"] = sc["excess"].abs() >= _SURPRISE_THR
    for bcol in ("sealed", "needs_diag"):
        if bcol in sc.columns:
            sc[bcol] = sc[bcol].astype(str).str.lower().eq("true")
    sc["limit"] = sc.get("limit", pd.Series("", index=sc.index)).fillna("")
    return {"t": meta["t"], "t1": meta["t1"], "market_cc": meta["market_cc"],
            "sigma": meta.get("sigma"), "excluded": meta["excluded"], "scorecard": sc}


def build_and_stage(t: str, scan_root: Path | str | None = None,
                    prices: pd.DataFrame | None = None, cal: list[str] | None = None) -> dict:
    """build + 写盘(scorecard.csv/md/build_meta)→ 返回 workflow 用的整包 JSON(含 ledger 近况)。"""
    res = build_scorecard(t, scan_root=scan_root, prices=prices, cal=cal)
    out_dir = _stage(res, scan_root=scan_root)
    sc = res["scorecard"]
    def _pctv(v):
        return None if v is None or pd.isna(v) else round(float(v) * 100, 2)

    rows = []
    for _, r in sc.iterrows():
        rows.append({"code": str(r["code"]), "name": r.get("name"), "rating": r["rating"],
                     "conviction": None if pd.isna(r.get("conviction")) else float(r["conviction"]),
                     "l4_conf": r.get("l4_conf", ""),
                     "cc_pct": _pctv(r["cc1"]), "oc_pct": _pctv(r.get("oc1")),
                     "excess_pct": _pctv(r["excess"]), "excess_ind_pct": _pctv(r.get("excess_ind")),
                     "z": None if pd.isna(r.get("z")) else round(float(r["z"]), 2),
                     "verdict": r["verdict"], "surprise": bool(r["surprise"]),
                     "sealed": bool(r.get("sealed", False)),
                     "needs_diag": bool(r.get("needs_diag", True)),
                     "limit": r.get("limit") or ""})
    cfg: dict = {}
    try:                                     # agent 模型/effort 统一由 scan_config.jsonc 管控
        from autoresearch.scan.user_config import load_user_config
        cfg = load_user_config().get("agents") or {}
    except Exception:  # noqa: BLE001 — 配置坏了不挡复盘(workflow 用内建默认)
        cfg = {}
    open_cands = [{"key": r["key"], "n_days": len(r["days"]),
                   "text": (r.get("texts") or [""])[-1], "filed_pr": r.get("filed_pr")}
                  for r in load_candidates()[:8]]
    return _sanitize({"t": res["t"], "t1": res["t1"],
                      "market_cc_pct": round(res["market_cc"] * 100, 2),
                      "n": len(sc), "excluded": res["excluded"], "rows": rows,
                      "dir": str(out_dir), "scorecard_md": str(out_dir / "scorecard.md"),
                      "ledger_tail": ledger_tail_summary(),
                      "agents_cfg": cfg, "open_candidates": open_cands})


def finalize(t: str, scan_root: Path | str | None = None,
             ledger_path: Path | str | None = None,
             cand_path: Path | str | None = None, add_proposal=None) -> dict:
    """综合稿收尾:验 report.md 在场 → diagnoses.json 并进账本 → 候选账本 upsert +
    重复模式自动立案(自我迭代腿)→ done(mode=full)。

    report.md 缺 → SystemExit(2):综合稿没写就不算复盘完(防「跑了一半像跑完」)。
    candidates.json 缺 = 当日无候选,合法(不是失败)。
    """
    sdir = Path(scan_root or "context/scan") / t / "t1_review"
    if not (sdir / "report.md").exists():
        raise SystemExit(f"[t1_review] {sdir / 'report.md'} 缺失:综合稿未写,拒绝 finalize")
    diagnoses = {}
    dp = sdir / "diagnoses.json"
    if dp.exists():
        raw = json.loads(dp.read_text(encoding="utf-8"))
        items = raw if isinstance(raw, list) else raw.get("diagnoses", [])
        diagnoses = {str(d["code"]).zfill(6): d for d in items if d.get("code")}
    res = _load_staged(t, scan_root=scan_root)
    n = append_ledger(res, diagnoses=diagnoses, path=ledger_path)
    cp = sdir / "candidates.json"
    filed: list[str] = []
    if cp.exists():
        cand_items = json.loads(cp.read_text(encoding="utf-8"))
        upsert_candidates(t, cand_items if isinstance(cand_items, list) else [], path=cand_path)
        filed = promote_candidates(path=cand_path, add_proposal=add_proposal)
    sc = res["scorecard"]
    summary = {"n": n, "diagnosed": len(diagnoses),
               "right": int((sc["verdict"] == "准").sum()),
               "wrong": int((sc["verdict"] == "不准").sum()),
               "promoted": filed}
    mark_done(t, "full", summary, scan_root=scan_root)
    return summary


def backfill_day(t: str, scan_root: Path | str | None = None,
                 ledger_path: Path | str | None = None,
                 prices: pd.DataFrame | None = None, cal: list[str] | None = None) -> dict:
    """确定性回补(无诊断叙事):scorecard + 账本行(diagnosed=false)+ done(mode=deterministic)。

    给「快环诞生前/漏跑日」用——数字先进账本攒统计,叙事诊断只对当日新鲜对跑(token 经济)。
    """
    res = build_scorecard(t, scan_root=scan_root, prices=prices, cal=cal)
    _stage(res, scan_root=scan_root)
    n = append_ledger(res, path=ledger_path)
    sc = res["scorecard"]
    summary = {"n": n, "right": int((sc["verdict"] == "准").sum()),
               "wrong": int((sc["verdict"] == "不准").sum())}
    mark_done(t, "deterministic", summary, scan_root=scan_root)
    return summary


def render_ledger_report(k: int = 20, path: Path | str | None = None) -> str:
    """账本累计视图(按评级的方向准率 + mechanism 直方图)。n 小照实标注,不装权威。"""
    path = Path(path or _LEDGER)
    if not path.exists():
        return "t1_review 账本为空(快环还没跑过)。\n"
    rows = [json.loads(ln) for ln in path.read_text(encoding="utf-8").splitlines() if ln.strip()]
    days = sorted({r["t"] for r in rows})[-k:]
    rows = [r for r in rows if r["t"] in days]

    def _ex(r):                                   # 期望值口径:行业超额优先,老行退市场超额
        return r.get("excess_ind") if r.get("excess_ind") is not None else r.get("excess")

    lines = [f"# T+1 快环账本 · 近 {len(days)} 个 T 日 · {len(rows)} 只次",
             "(期望值口径 = 行业中性超额;🔒一字板不计可实现)"]
    for rat in ("Overweight", "Buy", "Underweight", "Sell"):
        sub = [r for r in rows if r["rating"] == rat and r["verdict"] in ("准", "不准", "中性")
               and not r.get("sealed")]
        if not sub:
            continue
        ok = sum(1 for r in sub if r["verdict"] == "准")
        bad = sum(1 for r in sub if r["verdict"] == "不准")
        sign = 1.0 if rat in ("Overweight", "Buy") else -1.0   # 顺方向收益(UW 的"赢"=跌)
        pnl = [sign * _ex(r) for r in sub if _ex(r) is not None]
        wins, losses = [p for p in pnl if p > 0], [p for p in pnl if p <= 0]
        # 期望值三件套(Mauboussin:胜率单看会骗人,要配盈亏比):胜率 × 均赢 vs 均亏
        exp = (f"胜率 {len(wins)}/{len(pnl)},均赢 {sum(wins) / len(wins) * 100:+.2f}pp"
               f" / 均亏 {sum(losses) / len(losses) * 100:+.2f}pp"
               if wins and losses else
               f"顺方向超额均值 {sum(pnl) / len(pnl) * 100:+.2f}pp" if pnl else "无可实现样本")
        lines.append(f"- {rat}:n={len(sub)} 准{ok}/不准{bad};{exp}"
                     + ("(⚠n<10 只看不裁)" if len(sub) < 10 else ""))
    holds = [r for r in rows if r["verdict"] == "—"]
    if holds:
        surp = sum(1 for r in holds if r["surprise"])
        lines.append(f"- Hold(无方向主张):n={len(holds)},⚡惊奇 {surp} 只")
    # conviction 校准(Tetlock:70 档就该 70% 兑现;n 小如实标注,攒够才谈曲线)
    dirs = [r for r in rows if r["verdict"] in ("准", "不准") and r.get("conviction") is not None]
    if dirs:
        buckets = [("≤55", lambda c: c <= 55), ("56-70", lambda c: 55 < c <= 70),
                   (">70", lambda c: c > 70)]
        parts = []
        for name, f in buckets:
            b = [r for r in dirs if f(r["conviction"])]
            if b:
                hit = sum(1 for r in b if r["verdict"] == "准")
                parts.append(f"{name}: {hit}/{len(b)}")
        if parts:
            lines.append("- L3 conviction 校准(方向票命中/样本):" + "、".join(parts)
                         + "(⚠攒够 n≥20 才谈校准曲线)")
    # L4 自己的置信度曲线(Wave7 P3):上面那条量的是 **L3** 的 0-100 分,回答"漏斗选得准不准";
    # 这条量的是卡片深核完之后写下的「置信度: 高/中/低」,回答"L4 知不知道自己什么时候更靠谱"。
    # 两条都要:L3 高确信被 L4 翻案(🔁 行)与 L4 高置信仍判错,是两种完全不同的病。
    l4 = [r for r in rows if r["verdict"] in ("准", "不准") and (r.get("l4_conf") or "")]
    if l4:
        parts4 = []
        for name in ("高", "中", "低"):
            b = [r for r in l4 if r.get("l4_conf") == name]
            if b:
                parts4.append(f"{name}: {sum(1 for r in b if r['verdict'] == '准')}/{len(b)}")
        if parts4:
            lines.append("- L4 置信度校准(卡内自评 · 方向票命中/样本):" + "、".join(parts4)
                         + "(⚠攒够 n≥20 才谈校准曲线;高置信命中率若不高于低置信 = 自评失效)")
    mech = ledger_tail_summary(k, path)["mechanisms"]
    if mech:
        lines.append("- 诊断机制直方图:" + "、".join(f"{m}×{c}" for m, c in mech.items())
                     + " ← 同机制 ≥2 次 = 候选经验(人批立案)")
    return "\n".join(lines) + "\n"


def main() -> int:
    ap = argparse.ArgumentParser(description="T+1 判断层复盘快环(确定性层)")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("pending", help="待复盘 (T,T+1) 对")
    p.add_argument("--max", type=int, default=5)
    p.add_argument("--json", action="store_true")
    p = sub.add_parser("build", help="构建记分卡并落 staging")
    p.add_argument("date")
    p.add_argument("--json", action="store_true", help="打印 workflow 用整包 JSON")
    p = sub.add_parser("finalize", help="综合稿收尾(验 report.md → 账本 → done)")
    p.add_argument("date")
    p = sub.add_parser("backfill", help="确定性回补一日(无诊断叙事)")
    p.add_argument("date")
    p = sub.add_parser("report", help="账本累计视图")
    p.add_argument("-k", type=int, default=20)
    a = ap.parse_args()
    if a.cmd == "pending":
        pairs = pending_pairs(max_pairs=a.max)
        print(json.dumps(pairs, ensure_ascii=False) if a.json else
              ("\n".join(f"{p['t']} → {p['t1']}" for p in pairs) or "无待复盘对"))
    elif a.cmd == "build":
        pack = build_and_stage(a.date)
        print(json.dumps(pack, ensure_ascii=False) if a.json else
              f"[t1_review] {a.date}→{pack['t1']} 记分卡 {pack['n']} 只(剔除 {pack['excluded']})"
              f" → {pack['scorecard_md']}")
    elif a.cmd == "finalize":
        print(json.dumps(finalize(a.date), ensure_ascii=False))
    elif a.cmd == "backfill":
        print(json.dumps(backfill_day(a.date), ensure_ascii=False))
    elif a.cmd == "report":
        print(render_ledger_report(a.k), end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
