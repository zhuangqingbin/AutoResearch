#!/usr/bin/env python3
"""P0-4·过程分/结果分分离 · 机检版(纯确定性 checklist,零 LLM)。

design: docs/specs/2026-07-12-selflearning-optimization-brainstorm.md §4 P0-4;
plan:   docs/plans/2026-07-12-selflearning-p0-plan.md T2。

结果分 = fwd_2(retro.py 已有,attribution.csv)。过程分 = 逐 finalist 6 项确定性布尔
checklist(全部读自**现成产物**,不新造判定逻辑,只把既有仪器的输出汇总成表):

  1. **数字机检回环通过** —— 复用 `l3_select` 的 thesis 数字机检纯函数
     (`_thesis_number_tokens`/`_row_numeric_pool`/`_approx_in_pool`,与 `lint_judged`
     同一套判定,只是逐票拆开而非报全天 ok)对 `_l3_judged.json`(+`L2_gbdt_top200.csv`)
     逐票重算。缺 `_l3_judged.json`(旧日期/two_pass 关闭)→ 该日全体 False。
  2. **盲读微pass 节存在** —— 卡文含「独立初判」字样(l4-card.md 铁律"先读数据后读论点"
     的落地检测)。**局限**:这是对既定提示词汇的文本启发式,非机器契约行(卡片模板未
     强制这个小节标题)——若未来该提示词措辞改了,这项检测需要跟着更新。
  3. **基率或目标锚行已渲染** —— 该票 `_l4_prompt_<code>.md`(任务包,已含
     `_base_rate_mark`/`_target_calib_mark` 的输出)含 🔁 或 📐 行。
  4. **卡片契约 lint 通过** —— `self_review.card_contract_lint(scan_dir)` 无该码命中。
  5. **评级=rubric 建议自洽** —— 卡文 `**Rating**` 与 `**Rubric建议**` 一致,或有
     **偏离** 说明(与卡片模板契约"必须=Rubric建议,否则下一行偏离硬理由"同语义;
     不同于 `self_review.review` 的"仅买单·仅评级更激进"版本,这里检的是全卡通用自洽)。
  6. **slim>10KB** —— `context/<code>[后缀]_<date>_slim.md` 文件体积(NO_DATA 空稿判据)。

presence-gated:任一支撑产物缺失(该日某功能未启用/旧日期没有该产物)→ 对应项判 **False**,
不是跳过整行——process_score 是"能否验证到位"的下界读数,不是乐观占位(§5 局限4)。

用法:
  uv run --no-sync python -m autoresearch.learning.process_score 2026-07-08
"""
from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

_CHECKS = ["chk_numeric_loop", "chk_blind_pass", "chk_base_rate_or_target",
           "chk_card_contract", "chk_rubric_consistent", "chk_slim_size"]
_SLIM_MIN_BYTES = 10 * 1024   # spec 字面:slim>10KB(l4-card 铁律的 ">8KB 可信" 是更松的门槛,这里按 spec 从严)


# ───────────────────────── 逐项 checklist(纯读现成产物) ─────────────────────────


def _numeric_loop_pass_map(scan_dir: Path) -> dict[str, bool] | None:
    """checklist①:逐票『数字机检回环』是否通过。

    复用 `l3_select` 的纯函数对 `_l3_judged.json`(现成产物,two_pass pass2 判断产出)逐条
    重算——不新写判定逻辑,也不改 `lint_judged` 只报全天 ok/reason 字符串的既有契约(该函数
    供 workflow 打回自修用,不便反解逐票真假)。缺 `_l3_judged.json` → None(该日不可判定,
    调用方应把它当"该日全体 False"处理)。
    """
    from autoresearch.scan.agents.l3_select import (
        _approx_in_pool,
        _row_numeric_pool,
        _thesis_number_tokens,
    )

    judged_path = Path(scan_dir) / "_l3_judged.json"
    if not judged_path.exists():
        return None
    try:
        picks = json.loads(judged_path.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001 — 坏 json 视为不可判定
        return None
    if not isinstance(picks, list):
        return None

    l2_by_code: dict = {}
    l2p = Path(scan_dir) / "L2_gbdt_top200.csv"
    if l2p.exists():
        try:
            l2 = pd.read_csv(l2p, dtype={"code": str})
            l2["code"] = l2["code"].astype(str).str.zfill(6)
            l2_by_code = {r["code"]: r for _, r in l2.iterrows()}
        except Exception:  # noqa: BLE001
            l2_by_code = {}

    out: dict[str, bool] = {}
    for pick in picks:
        if not isinstance(pick, dict):
            continue
        code = str(pick.get("code", "")).zfill(6)
        thesis = str(pick.get("thesis") or "")
        catalyst = str(pick.get("catalyst") or "")
        pool = _row_numeric_pool(pick, l2_by_code.get(code))
        ok = True
        for tok in _thesis_number_tokens(thesis):
            try:
                tv = float(tok)
            except ValueError:
                continue
            if _approx_in_pool(tv, pool) or tok in catalyst:
                continue
            ok = False
            break
        out[code] = ok
    return out


def _slim_path(scan_dir: Path, code: str, date: str) -> Path | None:
    """`context/<code>[后缀]_<date>_slim.md` —— 同 `assemble._stage_token_estimate` 的
    slim_root 反推惯例(`scan_dir.parent.parent`);code 前缀 + glob 兜底后缀(.SH/.SZ/.SS/无)。
    A 股代码恒 6 位,前缀匹配不会误撞另一票。无匹配 → None。
    """
    slim_root = Path(scan_dir).parent.parent
    if not slim_root.exists():
        return None
    matches = sorted(slim_root.glob(f"{code}*_{date}_slim.md"))
    return matches[0] if matches else None


def compute_process_scores(scan_dir: Path | str) -> pd.DataFrame:
    """逐 finalist 6 项确定性 checklist → DataFrame(code + 6 bool + process_score 0-6)。

    无 `finalists.csv`/空表 → 空 DataFrame(调用方据此不落盘,presence-gated 老路不破)。
    卡片文件缺失(`_decision_text` 找不到)→ 该码 6 项全 False、process_score=0(无法验证
    任何一项,不是"部分通过")。
    """
    scan_dir = Path(scan_dir)
    date = scan_dir.name
    cols = ["code", *_CHECKS, "process_score"]

    from autoresearch.agents.utils.rating import parse_rating
    from autoresearch.learning import self_review
    from autoresearch.scan.assemble import _DEV_RE, _RUBRIC_RE, _decision_text, _read_csv

    finals = _read_csv(scan_dir / "finalists.csv")
    if not finals:
        return pd.DataFrame(columns=cols)

    loop_map = _numeric_loop_pass_map(scan_dir)
    lint_bad = {str(f.get("code", "")).zfill(6)
                for f in self_review.card_contract_lint(scan_dir) if f.get("code")}

    rows: list[dict] = []
    for fr in finals:
        ticker = (fr.get("ticker") or fr.get("code") or "").strip()
        code = ticker.split(".")[0].zfill(6) if ticker else ""
        text = _decision_text(scan_dir, ticker) if ticker else None
        if text is None:
            rows.append({"code": code, **dict.fromkeys(_CHECKS, False), "process_score": 0})
            continue

        chk_numeric = bool(loop_map.get(code, False)) if loop_map is not None else False
        chk_blind = "独立初判" in text

        pp = scan_dir / f"_l4_prompt_{code}.md"
        chk_baserate = False
        if pp.exists():
            try:
                ptxt = pp.read_text(encoding="utf-8")
                chk_baserate = ("🔁" in ptxt) or ("📐" in ptxt)
            except Exception:  # noqa: BLE001
                chk_baserate = False

        chk_contract = code not in lint_bad

        rating = parse_rating(text)
        rub = _RUBRIC_RE.search(text)
        rubric_suggest = rub.group(1).title() if rub else ""
        rubric_dev = bool(_DEV_RE.search(text))
        chk_rubric = bool(rubric_suggest) and (rubric_suggest == rating or rubric_dev)

        sp = _slim_path(scan_dir, code, date)
        chk_slim = bool(sp is not None and sp.stat().st_size > _SLIM_MIN_BYTES)

        vals = {"chk_numeric_loop": chk_numeric, "chk_blind_pass": chk_blind,
                "chk_base_rate_or_target": chk_baserate, "chk_card_contract": chk_contract,
                "chk_rubric_consistent": chk_rubric, "chk_slim_size": chk_slim}
        rows.append({"code": code, **vals, "process_score": sum(1 for v in vals.values() if v)})

    return pd.DataFrame(rows, columns=cols)


def write_process_scores(scan_dir: Path | str) -> Path | None:
    """落 `<scan_dir>/process_scores.csv`;无 finalists/空表 → 不写(presence-gated,老路不破)。

    调用方(assemble.run)须自行 contextlib.suppress——本函数不吞异常,便于单测直接断言。
    """
    scan_dir = Path(scan_dir)
    df = compute_process_scores(scan_dir)
    if df.empty:
        return None
    p = scan_dir / "process_scores.csv"
    df.to_csv(p, index=False)
    return p


# ───────────────────────── CLI ─────────────────────────


def main(argv: list[str] | None = None) -> int:
    import argparse

    ap = argparse.ArgumentParser(prog="python -m autoresearch.learning.process_score",
                                 description="P0-4 逐卡过程分 checklist(单日,零 LLM)")
    ap.add_argument("date", help="分析日 YYYY-MM-DD")
    ap.add_argument("--root", default="context/scan", help="scan 根目录(默认 context/scan)")
    args = ap.parse_args(argv)

    scan_dir = Path(args.root) / args.date
    df = compute_process_scores(scan_dir)
    if df.empty:
        print(f"[process_score] {args.date} 无 finalists,跳过")
        return 0
    p = scan_dir / "process_scores.csv"
    df.to_csv(p, index=False)
    dist = {int(k): int(v) for k, v in df["process_score"].value_counts().sort_index().items()}
    print(f"[process_score] {args.date} {len(df)} 卡 → {p}")
    print(f"  process_score 分布(0-6):{dist}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
