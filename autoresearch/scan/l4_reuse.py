#!/usr/bin/env python3
"""scan-market · L4 卡片 TTL 复用(确定性,零 LLM)。

design: docs/specs/2026-07-02-scan-l4-economy-design.md §1

finalist 逐日重叠(07-01 实测 16%,紫光国微窗口更高)= 重复烧 Opus 研究同一批票。
本模块把"近日已出卡、无实质变化"的 Hold/UW 票判为**直接复用**(拷前卡 + ♻️ banner),
不派 subagent——L4 从"每次重研"变"卡片带 TTL"。**≥OW 永不复用**(买点必须重研);
所有条件缺数据保守不复用(公告一项例外:文件缺依价格门兜底,诚实注明)。

  uv run --no-sync python -m autoresearch.scan.l4_reuse 2026-07-02          # dry-run 决策表
  uv run --no-sync python -m autoresearch.scan.l4_reuse 2026-07-02 --apply  # 写复用卡
"""
from __future__ import annotations

import argparse
import re
from datetime import datetime
from pathlib import Path

import pandas as pd

_REUSABLE = {"Hold", "Underweight", "Sell"}

_CARD_SCHEMA_MARK = "卡契约 v3"   # 2026-07-10 超短化;无标记的旧 swing 语义卡禁复用

_GATESEG_RE = re.compile(r"OW三门[^\n→]*")


def _gates_failed(card_text: str) -> int | None:
    """前卡 `OW三门 …` 段的失守数(✗ 计数);无该段 → None(回退 conviction 门)。"""
    m = _GATESEG_RE.search(card_text or "")
    return m.group(0).count("✗") if m else None


def _close_for(d: Path, code6: str) -> float | None:
    """当日收盘价:L1_recall → L2 → L1_scored_full 依次找;都没有 → None(不可比)。"""
    for fname in ("L1_recall_top1000.csv", "L2_gbdt_top200.csv", "L1_scored_full.csv"):
        p = d / fname
        if not p.exists():
            continue
        try:
            df = pd.read_csv(p, dtype={"code": str})
        except Exception:  # noqa: BLE001
            continue
        if "code" not in df.columns or "close" not in df.columns:
            continue
        sub = df[df["code"].astype(str).str.zfill(6) == code6]
        if len(sub):
            v = pd.to_numeric(sub.iloc[0]["close"], errors="coerce")
            return None if pd.isna(v) else float(v)
    return None


def _market_move(now_dir: Path, prev_dir: Path) -> float | None:
    """两日之间**全市场中位**涨跌幅(同窗口基准);任一帧缺/无重叠 → None。

    Wave7 B′-f:复用的价格门原本量绝对涨跌幅,而「这票有没有动」在普涨/普跌日会被整体
    漂移淹没 —— 2026-07-27 全市场中位 +2.67%、141 家涨停,持仓 688766 当日 **-4.58%**
    (相对跑输 7.3pp、资金三线全负),却因绝对值差 0.4pp 没碰到 5% 阈值而被判「没动、可复用」。
    基准取**同一对日期之间**的中位比值,而不是借用 market_pack 的单日 `median_pct_1d`:
    卡龄常跨多个交易日(TTL 4 天),拿单日基准去比多日涨跌是量错了尺子。
    """
    frames = []
    for d in (now_dir, prev_dir):
        p = d / "L1_scored_full.csv"
        if not p.exists():
            return None
        try:
            df = pd.read_csv(p, dtype={"code": str}, usecols=["code", "close"])
        except Exception:  # noqa: BLE001
            return None
        df["code"] = df["code"].astype(str).str.zfill(6)
        df["close"] = pd.to_numeric(df["close"], errors="coerce")
        frames.append(df.dropna(subset=["close"]).drop_duplicates("code"))
    m = frames[0].merge(frames[1], on="code", suffixes=("_now", "_prev"))
    m = m[m["close_prev"] > 0]
    if len(m) < 100:                      # 帧太薄 → 中位数不可信,宁可回退绝对口径
        return None
    return float((m["close_now"] / m["close_prev"] - 1.0).median())


def _regime_of(d: Path) -> str | None:
    p = d / "meta.json"
    if not p.exists():
        return None
    try:
        import json
        return json.loads(p.read_text(encoding="utf-8")).get("regime")
    except Exception:  # noqa: BLE001
        return None


def _has_new_anns(scan_dir: Path, code6: str, since: str) -> bool | None:
    """今日 L3_news 里是否有 ann_date > since(YYYY-MM-DD)的新公告。文件缺 → None(未知)。"""
    p = scan_dir / "L3_news" / f"{code6}.json"
    if not p.exists():
        return None
    try:
        import json
        anns = json.loads(p.read_text(encoding="utf-8"))
        cut = since.replace("-", "")
        return any(str(a.get("ann_date", ""))[:8] > cut for a in anns)
    except Exception:  # noqa: BLE001
        return True     # 解析失败按"有新公告"保守处理


def _conviction_today(scan_dir: Path, code6: str) -> float | None:
    p = scan_dir / "finalists.csv"
    if not p.exists():
        return None
    try:
        df = pd.read_csv(p, dtype={"code": str})
        sub = df[df["code"].astype(str).str.zfill(6) == code6]
        if not len(sub):
            return None
        v = pd.to_numeric(sub.iloc[0].get("conviction"), errors="coerce")
        return None if pd.isna(v) else float(v)
    except Exception:  # noqa: BLE001
        return None


def reuse_decision(code: str, scan_dir: Path | str, max_age_days: int = 4,
                   price_tol: float = 0.05, conv_max: float = 70.0,
                   pinned: bool = False) -> dict:
    """单票复用判定。全部条件过才 reuse=True;reasons 记第一批否决因(可多条)。

    `pinned=True`(`pinned.jsonc` 里的保送持仓)→ **永不复用**,与「≥OW 永不复用」对称:
    买点必须重研,持仓的卖/持判断同样必须当日重研。2026-07-27 实锤:本模块此前对
    「持仓」零认知,688766/601869 两只持仓吃了 07-24 的旧卡,连带**绕过 pinned SELL 双复核**
    (那段只在本次真派发、卡判 SELL 时才触发);而 `force_full: true` 只覆盖哨兵档、
    管不到 reuse —— 两者是独立的两道口子,当晚只能人工把两只从 details/ 移出重派。
    缺省 False = 现行为(parity)。
    """
    scan_dir = Path(scan_dir)
    code6 = str(code).split(".")[0].zfill(6)
    out = {"code": code6, "reuse": False, "prior_date": None, "prior_rating": None,
           "age_days": None, "price_chg": None, "price_excess": None, "reasons": []}
    if pinned:
        out["reasons"].append("📌 保送持仓(持仓判断必须当日重研,且复用会绕过 SELL 双复核)")
        return out
    priors = sorted((p for p in scan_dir.parent.iterdir()
                     if p.is_dir() and p.name[:2] == "20" and p.name < scan_dir.name
                     and (p / "details" / f"{code6}.md").exists()), reverse=True)
    if not priors:
        out["reasons"].append("无前卡")
        return out
    pdir = priors[0]
    text = (pdir / "details" / f"{code6}.md").read_text(encoding="utf-8")
    from autoresearch.agents.utils.rating import parse_rating  # lazy
    rating = parse_rating(text)
    out.update(prior_date=pdir.name, prior_rating=rating)
    try:
        age = (datetime.strptime(scan_dir.name, "%Y-%m-%d")
               - datetime.strptime(pdir.name, "%Y-%m-%d")).days
    except ValueError:
        out["reasons"].append("日期不可解析")
        return out
    out["age_days"] = age
    if _CARD_SCHEMA_MARK not in text:
        out["reasons"].append("旧契约卡(schema v3 前,禁复用)")
    if "♻️" in text:
        out["reasons"].append("前卡即复用卡(禁链式复用)")
    if rating not in _REUSABLE:
        out["reasons"].append(f"前卡评级 {rating}(≥OW 必重研)")
    if age > max_age_days:
        out["reasons"].append(f"超 TTL({age}d>{max_age_days}d)")
    conv = _conviction_today(scan_dir, code6)
    if conv is not None and conv >= conv_max:
        gf = _gates_failed(text)
        if gf is not None and gf >= 2:      # 深否决:L3 再兴奋也别为失真先验重烧 Opus(07-03:conv82→Hold)
            out["reasons"].append(f"(前卡OW门失守{gf}/3=深否决 → 豁免强先验重研)")
        else:
            out["reasons"].append(f"今日 conviction {conv:.0f}≥{conv_max:.0f}(强先验值得重研)")
    c_now, c_prev = _close_for(scan_dir, code6), _close_for(pdir, code6)
    if c_now is None or c_prev is None or not c_prev:
        out["reasons"].append("无价可比(两日 staging 缺 close)")
    else:
        raw = c_now / c_prev - 1.0
        out["price_chg"] = round(raw, 4)
        mkt = _market_move(scan_dir, pdir)
        # 相对市场的超额位移才是「这票有没有动」的正确代理量(见 _market_move 注)。
        # 基准不可得 → 回退绝对口径,并**在 reasons 里留痕**(降级不留痕才是真病)。
        excess = raw if mkt is None else raw - mkt
        out["price_excess"] = round(excess, 4)
        if mkt is None:
            out["reasons"].append("(市场基准不可得,价格门回退绝对口径)")
        if abs(excess) > price_tol:
            base = "" if mkt is None else f",市场 {mkt:+.1%}"
            out["reasons"].append(f"Δ价超额 {excess:+.1%}>±{price_tol:.0%}(实际 {raw:+.1%}{base})")
    r_now, r_prev = _regime_of(scan_dir), _regime_of(pdir)
    if r_now and r_prev and r_now != r_prev:
        out["reasons"].append(f"regime 翻转({r_prev}→{r_now})")
    anns = _has_new_anns(scan_dir, code6, pdir.name)
    if anns:
        out["reasons"].append("有新公告(ann_date>前卡日)")
    elif anns is None and not out["reasons"]:
        out["reasons"].append("(公告数据缺,依价格门放行)")   # 软注记,不否决
    hard = [r for r in out["reasons"] if not r.startswith("(")]
    out["reuse"] = not hard
    return out


def write_reused_card(code: str, scan_dir: Path | str, dec: dict,
                      price_tol: float = 0.05) -> Path | None:
    """拷前卡 + ♻️ banner 写今日 details/<code>.md;今日卡已存在 → 不覆盖返回 None。"""
    scan_dir = Path(scan_dir)
    code6 = str(code).split(".")[0].zfill(6)
    dst = scan_dir / "details" / f"{code6}.md"
    if dst.exists():
        return None
    src = scan_dir.parent / str(dec["prior_date"]) / "details" / f"{code6}.md"
    chg = dec.get("price_chg")
    banner = (f"♻️ **复用卡**(源 {dec['prior_date']},TTL {dec['age_days']}d ｜ "
              f"Δ价 {'—' if chg is None else format(chg, '+.1%')} ｜ 无新公告 ｜ regime 未变)\n"
              f"> 当日未重研,评级沿用下方原卡({dec['prior_rating']});"
              f"失效即重研:|Δ价|>{price_tol:.0%} / 新公告 / regime 翻转 / 观察单触发 / 进近买区。\n\n---\n\n")
    dst.parent.mkdir(parents=True, exist_ok=True)
    dst.write_text(banner + src.read_text(encoding="utf-8"), encoding="utf-8")
    return dst


def _pinned_codes(scan_dir: Path) -> set[str]:
    """当日仍生效的保送持仓码集合(过期条目不算)。读不到 → 空集(不误伤,退回旧行为)。"""
    try:
        from autoresearch.scan.user_config import load_pinned
        kept = load_pinned(Path(scan_dir).name).get("kept") or []
    except Exception:  # noqa: BLE001 — 复用层是成本优化,配置读不到不该阻断
        return set()
    return {str(e.get("code", "")).split(".")[0].zfill(6) for e in kept if e.get("code")}


def reuse_pass(scan_dir: Path | str, max_age_days: int = 4, price_tol: float = 0.05,
               conv_max: float = 70.0, apply: bool = False) -> pd.DataFrame:
    """对全部 finalists 出复用决策表;apply=True 时给可复用票写卡。

    保送持仓从 `pinned.jsonc` 真身读(Wave7 B′-f)——**生产者必须自己接线**:只给
    `reuse_decision` 加形参而不在这里传,等于加了个零调用点的死参数(FN-1 家族前科)。
    """
    scan_dir = Path(scan_dir)
    p = scan_dir / "finalists.csv"
    if not p.exists():
        return pd.DataFrame(columns=["code", "reuse", "prior_date", "prior_rating", "reasons"])
    fin = pd.read_csv(p, dtype={"code": str})
    pinned = _pinned_codes(scan_dir)
    rows = []
    for code in fin["code"].astype(str).str.zfill(6):
        dec = reuse_decision(code, scan_dir, max_age_days=max_age_days,
                             price_tol=price_tol, conv_max=conv_max,
                             pinned=code in pinned)
        if apply and dec["reuse"]:
            dec["written"] = write_reused_card(code, scan_dir, dec, price_tol=price_tol) is not None
        rows.append({**dec, "reasons": ";".join(dec["reasons"]) or "—"})
    return pd.DataFrame(rows)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="L4 卡片 TTL 复用(确定性;默认 dry-run)")
    ap.add_argument("date", help="scan 日 YYYY-MM-DD")
    ap.add_argument("--max-age", type=int, default=4, help="TTL 日历天,默认 4(覆盖周末)")
    ap.add_argument("--price-tol", type=float, default=0.05, help="价格容差,默认 5%%")
    ap.add_argument("--apply", action="store_true", help="给可复用票写 ♻️ 卡(默认只打表)")
    args = ap.parse_args(argv)
    df = reuse_pass(Path("context/scan") / args.date, max_age_days=args.max_age,
                    price_tol=args.price_tol, apply=args.apply)
    n = int(df["reuse"].sum()) if len(df) else 0
    print(df.to_string(index=False) if len(df) else "(无 finalists)")
    print(f"[l4_reuse] 可复用 {n}/{len(df)}{'(已写卡)' if args.apply else '(dry-run)'} "
          f"—— 复用票不派 subagent,每张省一次 Opus 渐进 DD")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
