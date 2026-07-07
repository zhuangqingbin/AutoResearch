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
                   price_tol: float = 0.05, conv_max: float = 70.0) -> dict:
    """单票复用判定。全部条件过才 reuse=True;reasons 记第一批否决因(可多条)。"""
    scan_dir = Path(scan_dir)
    code6 = str(code).split(".")[0].zfill(6)
    out = {"code": code6, "reuse": False, "prior_date": None, "prior_rating": None,
           "age_days": None, "price_chg": None, "reasons": []}
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
        chg = abs(c_now / c_prev - 1.0)
        out["price_chg"] = round(c_now / c_prev - 1.0, 4)
        if chg > price_tol:
            out["reasons"].append(f"Δ价 {chg:+.1%}>±{price_tol:.0%}")
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


def reuse_pass(scan_dir: Path | str, max_age_days: int = 4, price_tol: float = 0.05,
               conv_max: float = 70.0, apply: bool = False) -> pd.DataFrame:
    """对全部 finalists 出复用决策表;apply=True 时给可复用票写卡。"""
    scan_dir = Path(scan_dir)
    p = scan_dir / "finalists.csv"
    if not p.exists():
        return pd.DataFrame(columns=["code", "reuse", "prior_date", "prior_rating", "reasons"])
    fin = pd.read_csv(p, dtype={"code": str})
    rows = []
    for code in fin["code"].astype(str).str.zfill(6):
        dec = reuse_decision(code, scan_dir, max_age_days=max_age_days,
                             price_tol=price_tol, conv_max=conv_max)
        if apply and dec["reuse"]:
            dec["written"] = write_reused_card(code, scan_dir, dec, price_tol=price_tol) is not None
        rows.append({**dec, "reasons": ";".join(dec["reasons"]) or "—"})
    return pd.DataFrame(rows)


def carryover_candidates(scan_dir: Path | str, cap: int = 5) -> pd.DataFrame:
    """菜单滞回候选:最近前一 scan 日 finalists(前卡 ≤Hold)∩ 今日 L2 − 今日 finalists。

    07-03 病灶:churn 90%(repeat 3/30)把 TTL 复用架空(仅救 2 张)——保席让前卡沿 TTL
    摊销、个股档案有连续性;**复用/重研仍由 reuse_decision 门定**(价格/公告/regime 不动)。
    按今日 l2_rank 取前 cap;缺前日/缺 L2 → 空帧。
    """
    scan_dir = Path(scan_dir)
    l2p, fp = scan_dir / "L2_gbdt_top200.csv", scan_dir / "finalists.csv"
    if not l2p.exists() or not fp.exists():
        return pd.DataFrame()
    prev = sorted((p for p in scan_dir.parent.iterdir()
                   if p.is_dir() and p.name[:2] == "20" and p.name < scan_dir.name
                   and (p / "finalists.csv").exists()), reverse=True)
    if not prev:
        return pd.DataFrame()
    # regime 翻转日关 carryover(2026-07-06):昨日 regime 的 ≤Hold 票拖进今日新 regime 重烧
    # = 低价值重复(如 range→risk_off 把上一档的票全烧成 Hold)。翻转 → 不保席,让今日菜单自己定。
    r_now, r_prev = _regime_of(scan_dir), _regime_of(prev[0])
    if r_now is not None and r_prev is not None and r_now != r_prev:
        return pd.DataFrame()
    pf = pd.read_csv(prev[0] / "finalists.csv", dtype={"code": str})
    if "code" not in pf.columns:
        return pd.DataFrame()
    prev_codes = set(pf["code"].astype(str).str.zfill(6))
    fin = pd.read_csv(fp, dtype={"code": str})
    today_codes = set(fin["code"].astype(str).str.zfill(6)) if "code" in fin.columns else set()
    l2 = pd.read_csv(l2p, dtype={"code": str})
    if "code" not in l2.columns:
        return pd.DataFrame()
    l2["code"] = l2["code"].astype(str).str.zfill(6)
    cand = l2[l2["code"].isin(prev_codes - today_codes)].copy()
    cand["_rk"] = pd.to_numeric(cand.get("l2_rank"), errors="coerce")
    cand = cand.sort_values("_rk", na_position="last")
    from autoresearch.agents.utils.rating import parse_rating  # lazy
    keep: list[dict] = []
    for _, r in cand.iterrows():
        cp = prev[0] / "details" / f"{r['code']}.md"
        if not cp.exists():
            continue
        if parse_rating(cp.read_text(encoding="utf-8")) not in _REUSABLE:
            continue                                   # ≥OW 前卡不滞回(买点必进正常菜单重研)
        keep.append({"ticker": r["code"], "code": r["code"], "name": r.get("name", ""),
                     "sector": r.get("industry", ""), "lane": "carryover",
                     "thesis": f"(滞回保席:{prev[0].name} finalist 连续性;复用/重研由 l4_reuse 门定)"})
        if len(keep) >= cap:
            break
    return pd.DataFrame(keep)


def append_carryover(scan_dir: Path | str, cap: int = 5) -> int:
    """把滞回候选追加进 finalists.csv(幂等:code 已在则不再追;镜像 watchlist.append_express)。"""
    scan_dir = Path(scan_dir)
    ca = carryover_candidates(scan_dir, cap=cap)
    if not len(ca):
        return 0
    fp = scan_dir / "finalists.csv"
    fin = pd.read_csv(fp, dtype={"code": str})
    out = pd.concat([fin, ca[[c for c in ca.columns if c in fin.columns or c in
                              ("ticker", "code", "name", "sector", "thesis", "lane")]]],
                    ignore_index=True)
    out.to_csv(fp, index=False)
    return len(ca)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="L4 卡片 TTL 复用(确定性;默认 dry-run)")
    ap.add_argument("date", help="scan 日 YYYY-MM-DD")
    ap.add_argument("--max-age", type=int, default=4, help="TTL 日历天,默认 4(覆盖周末)")
    ap.add_argument("--price-tol", type=float, default=0.05, help="价格容差,默认 5%%")
    ap.add_argument("--apply", action="store_true", help="给可复用票写 ♻️ 卡(默认只打表)")
    ap.add_argument("--carryover", type=int, nargs="?", const=5, default=0, metavar="CAP",
                    help="先做菜单滞回保席(昨日 finalist∩今日 L2 追加 lane=carryover;默认关,给值即开)")
    args = ap.parse_args(argv)
    if args.carryover:
        nc = append_carryover(Path("context/scan") / args.date, cap=args.carryover)
        print(f"[carryover] 滞回保席 {nc} 只(lane=carryover;复用/重研仍由下方 TTL 门定)")
    df = reuse_pass(Path("context/scan") / args.date, max_age_days=args.max_age,
                    price_tol=args.price_tol, apply=args.apply)
    n = int(df["reuse"].sum()) if len(df) else 0
    print(df.to_string(index=False) if len(df) else "(无 finalists)")
    print(f"[l4_reuse] 可复用 {n}/{len(df)}{'(已写卡)' if args.apply else '(dry-run)'} "
          f"—— 复用票不派 subagent,每张省一次 Opus 渐进 DD")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
