#!/usr/bin/env python3
"""scan-market · T+1 判断层复盘快环(确定性层,零 LLM)。

用户裁定 2026-07-17(fb_20260717_001):
  自学习快环 = **T 报告的真选票 vs T+1 收盘**——T 推的这些票,T+1 收盘哪些准哪些不准、
  为什么、不准的如何优化、准的如何强化。保送(pinned)不算:判断层复盘只考它自己选的票。
  只做 T→T+1 **相邻交易日**间隔(周末/节假日顺延;不看更长 horizon——票会太多,且超短
  语义只关心次日)。T+1 当晚即可复盘,比慢环(retro,D+2 成熟)快一个交易日。

与慢环 retro 的分工(勿混,也勿建平行实现——本模块复用 factor_lab 取数/日历原语):
  retro     = 漏斗召回归因(全市场谁涨了没进池),D+2,喂**权重**重标定(唯一自动腿)。
  t1_review = 判断层精度(L3 选中 + L4 评级的票,次日兑现如何)。D3(2026-08-19,用户裁定 A5)
              退役 LLM 逐票诊断/候选自动立案链后,本环收窄为**确定性**记分卡(D+1 初判)+
              隔夜 gap 终判(D+2,`gap_finalize_pending`,nightly_close 接线)+ 账本派生的
              🔄 校准块注入 L3/L4 prompt(`render_t1_calibration_block`);不再产出 prompt
              侧新经验/自动立案,同类规则改经 feedback skill 人工立案。

两把尺(勿混,项目有尺子错配的疤;2026-08-05 用户裁定后再分初判/终判两层):
  D+1 初判尺 = cc1(T 收盘 → T+1 收盘;当晚就能算,判断层快环的速度优势全靠它,
              = T+1 当日 pct_chg 口径)——`build_scorecard`/`append_ledger` 当晚写下的 `verdict`
              列就是这把尺量出来的,**D+2 终判落地后仍原样留在 scorecard/账本里,不覆盖不抹除**。
  D+2 终判尺 = gap_c1_o2(T+1 收盘 → T+2 开盘,隔夜;2026-08-05 用户裁定)——**准不准的
              对外口径 = 这把尺**,由 `gap_finalize_pending` 在 T+2 晚(nightly_close 接线)
              回填 `final_verdict`,cc1 的 `verdict` 降级为「D+1 初判」参考。初判/终判方向
              相反的票,诊断必须说清隔夜发生了什么(不能假装没这回事)。
  参考尺   = oc1(T+1 开 → T+1 收;可实现口径,报告晚上出、最早 T+1 开盘才能建仓;终判尺
              换成 gap 后,oc1 仍只是辅助参考,不是本环任何 verdict 的判定尺)。
  跟 MAIN_RULER 的关系(勿混为一谈):持仓/权重校准主尺(autoresearch.common.ruler.MAIN_RULER,
  2026-08-05 裁定后同样指向 "gap_c1_o2")与本环的 D+2 终判尺**公式同源、计算路径独立**——
  本环出于「当晚必须能跑」的速度约束,自建轻量 daily 抓取(`_fetch_gap_prices`)+ 独立
  z 计算,不跑 ruler/factor_lab 的主帧管线,不读 MAIN_RULER 当前值。

产物:context/scan/<T>/t1_review/{scorecard.csv,scorecard.md,build_meta.json,done.json}
  ── scorecard.csv 的 gap_c1_o2/z_gap/final_verdict 三列由 D+2 晚 `gap_finalize_pending`
     回填(nightly_close 接线),D+1 `build` 时不产生;回填前 pd.read_csv 读不到这三列很正常。
     (diagnoses.json/report.md 曾由已退役的 t1-review LLM workflow 产出,D3 后不再生成;
     历史日盘上的这两个文件仍可读,不回收)
账本:context/learning/t1_review.jsonl(逐票行,按 T 日幂等整替;gap 回填只并入
  gap_c1_o2/z_gap/final_verdict 三键,既有 diagnosed/mechanism/why 等字段原样保留)

  uv run --no-sync python -m autoresearch.learning.t1_review pending          # 待复盘对
  uv run --no-sync python -m autoresearch.learning.t1_review build 2026-07-16
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

from autoresearch.common import workspace as ws
from autoresearch.common.ruler import MAIN_RULER

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
_LEDGER = ws.context_root() / "learning/t1_review.jsonl"


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


def _fetch_gap_prices(t1: str, t2: str) -> pd.DataFrame:
    """T+1 收 → T+2 开 隔夜窗全市场帧 [code, close_t1, open_t2, gap_c1_o2, industry?]。

    复用 `_fetch_prices`(通用两日 daily 抓取):把 (t1, t2) 当它的 (t, t1) 参数传入,
    它返回的 close_t/open_t1 正好是本函数要的 close_t1(T+1 收)/open_t2(T+2 开);
    但它算好的 cc1/oc1/hi_oc 口径不对(那三列是 close_t2 相关),本函数只取字段改名、
    自算 gap_c1_o2,不沿用那三列——省一次抓取,不省口径。T+2 daily 未发布/未结算 →
    复用 `_fetch_prices` 的 ValueError(诚实失败,同 D+1 cc1 一样不静默返回空帧)。
    """
    raw = _fetch_prices(t1, t2)
    cols = ["code", "close_t", "open_t1"]
    if "industry" in raw.columns:
        cols.append("industry")
    out = raw[cols].rename(columns={"close_t": "close_t1", "open_t1": "open_t2"})
    out["gap_c1_o2"] = out["open_t2"] / out["close_t1"] - 1.0
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
    scan_root = Path(scan_root or ws.scan_root())
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
    """逐票行落账本;按 T 日幂等(同日旧行整替,重跑安全)。diagnoses={code: {mechanism, why}}。

    每行打 `ruler`(写入那一刻的 `MAIN_RULER` 真值)——本环主尺是 cc1/oc1(与 MAIN_RULER
    无关),但账本族统一打标便于审计"这行是哪个主尺纪元写的"。历史行(本列上线前写的)
    round-trip 经 json.loads→json.dumps 天然不获得这列(旧行不回写 tag);读侧
    `row.get("ruler", "fwd_2_oc")` 兜底。
    """
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
                    "diagnosed": bool(d), "ts": ts, "ruler": MAIN_RULER})
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


# ───────────────────── T+1 快环校准块(账本读侧) ─────────────────────
#
# 历史沿革(2026-07-17 用户裁定建的「自我迭代腿」):综合官写 candidates.json(稳定 key)→
# finalize 记入候选账本 → 同 key 累计 n_days≥2 自动立案 proposals.jsonl(prompt_rule)→
# 人批成 lesson 后经 feedback_store.render_calibration_block 注入。
# **D3(2026-08-19,用户裁定 A5)退役**:t1-review LLM workflow(合诊+综合官)与写侧/自动
# 立案链(`upsert_candidates`/`promote_candidates`/`finalize`)已删除——候选账本不再有
# 新数据写入,`t1_candidates.jsonl` 已归档(archive/20260819/)。未来同类规则须人工经
# feedback skill 立案,不再有自动通道。
# `load_candidates` 只作为下面 `render_t1_calibration_block` 的只读依赖保留(读历史/
# 测试注入的候选账本;账本本体已归档后,生产默认路径下天然返回 []——presence-gated 零字节)。

_CAND_LEDGER = ws.context_root() / "learning/t1_candidates.jsonl"


def load_candidates(path: Path | str | None = None) -> list[dict]:
    p = Path(path or _CAND_LEDGER)
    if not p.exists():
        return []
    return [json.loads(ln) for ln in p.read_text(encoding="utf-8").splitlines() if ln.strip()]


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
    scan_root = Path(scan_root or ws.scan_root())
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
    p = Path(scan_root or ws.scan_root()) / t / "t1_review" / "done.json"
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
    out_dir = Path(scan_root or ws.scan_root()) / res["t"] / "t1_review"
    out_dir.mkdir(parents=True, exist_ok=True)
    res["scorecard"].to_csv(out_dir / "scorecard.csv", index=False)
    (out_dir / "scorecard.md").write_text(render_scorecard_md(res), encoding="utf-8")
    (out_dir / "build_meta.json").write_text(json.dumps(
        {"t": res["t"], "t1": res["t1"], "market_cc": res["market_cc"],
         "sigma": res.get("sigma"), "excluded": res["excluded"]},
        ensure_ascii=False), encoding="utf-8")
    return out_dir


def build_and_stage(t: str, scan_root: Path | str | None = None,
                    prices: pd.DataFrame | None = None, cal: list[str] | None = None) -> dict:
    """build + 写盘(scorecard.csv/md/build_meta)→ 返回 CLI `build` 播报用的摘要 dict。

    D3(2026-08-19,用户裁定 A5)退役 t1-review LLM 腿后,本函数不再打包逐票 rows/
    agents_cfg/open_candidates/ledger_tail(那是喂 t1-review.js 合诊/综合官 agent 的
    schema,workflow 已删除)——只留 CLI `build` 一行播报要用的摘要字段。
    """
    res = build_scorecard(t, scan_root=scan_root, prices=prices, cal=cal)
    out_dir = _stage(res, scan_root=scan_root)
    return _sanitize({"t": res["t"], "t1": res["t1"],
                      "market_cc_pct": round(res["market_cc"] * 100, 2),
                      "n": len(res["scorecard"]), "excluded": res["excluded"],
                      "dir": str(out_dir), "scorecard_md": str(out_dir / "scorecard.md")})


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


def _industry_neutral_gap(frame: pd.DataFrame) -> pd.DataFrame:
    """行业中性超额 + 截面稳健 z(gap 口径)。镜像 `build_scorecard` 里 cc1 的同一套算法
    (行业均值退市场基准、n<3 小行业退场基准、`_robust_sigma` 稳健σ、z 盖帽 ±3)——**同法,
    只换尺**,不发明新阈值(方向/惊奇判定仍由 `verdict()` 读 `_Z_DIR`/`_MIN_EXCESS` 常量)。

    输入需含 code/gap_c1_o2(industry 可选);输出追加 `_resid_gap`(行业中性超额,喂
    `verdict()` 的 excess 参数)与 `z_gap` 两列,原列不改。
    """
    out = frame.copy()
    market_gap = float(pd.to_numeric(out["gap_c1_o2"], errors="coerce").mean())
    if "industry" in out.columns and out["industry"].notna().any():
        ind_cnt = out.groupby("industry")["code"].transform("count")
        ind_mean = out.groupby("industry")["gap_c1_o2"].transform("mean")
        bench = ind_mean.where(ind_cnt >= 3, market_gap)
        out["_bench_gap"] = pd.to_numeric(bench, errors="coerce").fillna(market_gap)
    else:
        out["_bench_gap"] = market_gap
    out["_resid_gap"] = pd.to_numeric(out["gap_c1_o2"], errors="coerce") - out["_bench_gap"]
    sigma = _robust_sigma(out["_resid_gap"])
    out["z_gap"] = (out["_resid_gap"] / sigma).clip(-3, 3) if pd.notna(sigma) else float("nan")
    return out


def _update_ledger_gap(t: str, merged: pd.DataFrame, path: Path | str | None = None) -> int:
    """把 gap 终判字段并进账本既有行(整替当日行,幂等)。

    按 code 合并:既有行(`append_ledger`/`backfill_day` 已写的)只追加/覆写
    `gap_c1_o2`/`z_gap`/`final_verdict` 三键,其余字段原样保留——尤其
    `diagnosed`/`mechanism`/`why`/`stage`:gap 终判发生在 D+1 诊断之后,绝不能把当晚
    人工诊断的产出覆写掉(整替的对象是"gap 那三键",不是整行)。
    当日账本行缺失(理论不该发生——T 的 D+1 build/backfill 没跑过 gap 就没得终判;
    防御性兜底)→ 新建一行(diagnosed=false)。

    I4 修复(final-review 2026-08-08):`ruler` **不在**"追加/覆写三键"之列——本函数曾经
    额外 `setdefault("ruler", MAIN_RULER)`,对本字段上线前写的旧行(无 `ruler`)会反向
    打上**今天的** `MAIN_RULER`,违反上面这句 docstring 自己的承诺,也违反"历史产物不
    改写"家训(2026-08-08 活体验收实测把 103 条 07-10~08-04 的历史行改写成
    `ruler=gap_c1_o2`,方向反了:离换尺最近的行标旧尺、最老的行标新尺)。既有行本来就带
    `ruler`(`append_ledger` 写入那一刻打的)→ 保留不动;本来没有 → 继续没有,读侧按既有
    惯例 `row.get("ruler", "fwd_2_oc")` 兜底(旧行诚实标旧尺,不是回改)。新建整行的兜底
    分支(当日账本行缺失,理论不该发生)不受影响——那是一整行的**新写**,不是对旧行的
    改写,`ruler` 打当前 `MAIN_RULER` 仍是正确的"写入那一刻的真值"。
    """
    path = Path(path or _LEDGER)
    old = []
    if path.exists():
        old = [json.loads(ln) for ln in path.read_text(encoding="utf-8").splitlines() if ln.strip()]
    by_code = {r["code"]: r for r in old if r.get("t") == t}
    others = [r for r in old if r.get("t") != t]

    def _num(v, nd=5):
        return None if v is None or pd.isna(v) else round(float(v), nd)

    new_rows = []
    covered = set()
    for _, r in merged.iterrows():
        code = str(r["code"])
        covered.add(code)
        existing = by_code.get(code)
        # I4 修复:existing 不是 None → 这是一条历史行(不论它有没有 ruler 字段),原样
        # 复制,不碰 ruler(既不新增也不覆写)。existing 是 None 才是真正的"新建整行"分支
        # (理论不该发生的防御性兜底),这才是唯一该打当前 MAIN_RULER 的地方。
        base = dict(existing) if existing is not None else \
            {"t": t, "code": code, "diagnosed": False, "ruler": MAIN_RULER}
        base["gap_c1_o2"] = _num(r.get("gap_c1_o2"))
        base["z_gap"] = _num(r.get("z_gap"), 3)
        base["final_verdict"] = r.get("final_verdict")
        new_rows.append(base)
    new_rows += [r for c, r in by_code.items() if c not in covered]   # 防丢票
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in others + new_rows),
                    encoding="utf-8")
    return len(new_rows)


def gap_finalize_pending(today: str | None = None, scan_root: Path | str | None = None,
                         ledger_path: Path | str | None = None,
                         cal: list[str] | None = None,
                         gap_prices: dict[str, pd.DataFrame] | None = None,
                         ) -> tuple[int, list[str]]:
    """D+2 晚:回填隔夜 gap 终判(2026-08-05 用户裁定)。nightly_close 接线调用。

    终评尺 = gap_c1_o2(T+1 收 → T+2 开);cc1 的既有 `verdict` 列降为 D+1 初判,**不覆盖、
    不抹除**——两者在 scorecard.csv 与账本里并存。判定法复用既有 `verdict()` 纯函数与
    v2 常量(行业中性 + 截面稳健 z,同法,尺换 gap;见 `_industry_neutral_gap`)。

    扫描 `context/scan/*/t1_review/scorecard.csv`:已有非空 `final_verdict` 的日跳过
    (幂等,不重复回填);0 行的日(当日真选 0 只)跳过,留给下次;否则取 build_meta 的
    `t1`、算 `t2 = next_trade_day(t1)`,`t2` 未知或还没到 `today` → 跳过留给下次(以上三种
    都是"这日还没到时候",不算失败,不进返回值的失败名单)。

    真正进入处理的日子,**取数 + 计算 + 写盘整段纳入同一个 per-day 异常边界**——任一步
    抛异常(T+2 daily 未发布/写盘 IO 错误等)都只放弃这一日、continue 到下一日,不连坐
    (同 `nightly_close._t1_backfill`/`_retro_refresh`「单日失败不拖累其余日」的既有惯例;
    早前版本只把 try/except 包在取数那一步,计算/写盘裸奔——单日写盘失败会中断整个循环,
    后续待终判日全部漏跑且无声,已修)。写盘顺序刻意把 `scorecard.csv`(本函数的幂等判据
    所在)放最后:账本/build_meta 写失败时 csv 仍是「未终判」的旧状态,下次夜跑会正确地
    把这日判成待处理并重试,不会卡在半写状态。

    gap_prices 可注入(测试离线):{t1 日期: DataFrame},DataFrame 形状同
    `_fetch_gap_prices` 返回(至少含 code/gap_c1_o2,industry 可选)。
    返回:`(本次成功回填的日数, 失败日期列表)`——失败列表如实列出被跳过的日子,不静默
    少做(调用方 `nightly_close._t1_gap_finalize` 会把它拼进汇总行)。
    """
    today = today or datetime.now().strftime("%Y-%m-%d")
    scan_root = Path(scan_root or ws.scan_root())
    if not scan_root.exists():
        return 0, []
    if cal is None:
        import autoresearch.research.factor_lab as fl
        from autoresearch.data.tushare_source import _trade_days
        end = (datetime.strptime(today, "%Y-%m-%d") + timedelta(days=10)).strftime("%Y%m%d")
        cal = _trade_days(fl._pro(), _EPOCH.replace("-", ""), end)

    n_done = 0
    failed: list[str] = []
    for dd in sorted(p for p in scan_root.iterdir() if p.is_dir()):
        t = dd.name
        if t < _EPOCH:
            continue
        rd = dd / "t1_review"
        sc_path, meta_path = rd / "scorecard.csv", rd / "build_meta.json"
        if not sc_path.exists() or not meta_path.exists():
            continue
        try:
            sc = pd.read_csv(sc_path, dtype={"code": str})
        except Exception:  # noqa: BLE001 — 坏 csv 留给人工排查,不阻塞其余日;非"进入处理"故不进 failed
            continue
        if "final_verdict" in sc.columns and len(sc) \
                and sc["final_verdict"].fillna("").astype(str).ne("").all():
            continue                                       # 幂等:已终判过
        if not len(sc):
            continue                                       # 当日真选 0 只,无票可终判
        sc["code"] = sc["code"].str.zfill(6)
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        t1_date = meta["t1"]
        t2 = next_trade_day(t1_date, cal)
        if t2 is None or t2 > today:
            continue                                       # T+2 未知或还没到

        try:
            gp = gap_prices[t1_date] if gap_prices is not None else _fetch_gap_prices(t1_date, t2)
            gp = gp.copy()
            gp["code"] = gp["code"].astype(str).str.zfill(6)
            gp = _industry_neutral_gap(gp)

            merged = sc.merge(gp[["code", "gap_c1_o2", "_resid_gap", "z_gap"]], on="code", how="left")
            merged["final_verdict"] = [
                verdict(r, e, z) for r, e, z in
                zip(merged["rating"], merged["_resid_gap"], merged["z_gap"], strict=True)]
            merged = merged.drop(columns=["_resid_gap"])

            _update_ledger_gap(t, merged, path=ledger_path)
            meta["t2"] = t2
            meta["market_gap"] = round(
                float(pd.to_numeric(gp["gap_c1_o2"], errors="coerce").mean()), 6)
            sigma_gap = _robust_sigma(gp["_resid_gap"])
            meta["sigma_gap"] = None if pd.isna(sigma_gap) else round(float(sigma_gap), 5)
            meta_path.write_text(json.dumps(meta, ensure_ascii=False), encoding="utf-8")
            merged.to_csv(sc_path, index=False)                # 幂等判据列所在,刻意放最后写
        except Exception:  # noqa: BLE001 — 单日整段(取数/计算/写盘)失败不连坐,留给下次夜跑
            failed.append(t)
            continue
        n_done += 1
    return n_done, failed


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
        print(f"[t1_review] {a.date}→{pack['t1']} 记分卡 {pack['n']} 只(剔除 {pack['excluded']})"
              f" → {pack['scorecard_md']}")
    elif a.cmd == "backfill":
        print(json.dumps(backfill_day(a.date), ensure_ascii=False))
    elif a.cmd == "report":
        print(render_ledger_report(a.k), end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
