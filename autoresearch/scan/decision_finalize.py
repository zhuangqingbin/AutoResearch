"""Verify/ensemble folds and canonical DecisionRecord finalization."""
from __future__ import annotations

import contextlib
import json
import sys
from pathlib import Path

from autoresearch.agents.utils.rating import RATINGS_5_TIER
from autoresearch.scan.l4.parsers import (
    _GATES3,
    _decision_text,
    _read_csv,
    _strip,
    gate_status,
    parse_early_stop,
    write_early_stop,
)

TIER_RANK = {r: i for i, r in enumerate(RATINGS_5_TIER)}
_VERDICT_BADGE = {"维持": "✅维持", "降级": "⚠️降级", "否决": "🛑否决"}
_PROPOSAL_BY_RATING = {
    "Buy": "BUY",
    "Overweight": "BUY",
    "Hold": "HOLD",
    "Underweight": "SELL",
    "Sell": "SELL",
}


def _load_verify(scan_dir: Path) -> dict[str, dict]:
    """读 Tier-3 多空辩论 verify.csv(code,verdict,bull,bear,trigger,consensus)→ {code: {...}}。

    bull(最强多头)+ consensus(PM 3 透镜共识)是 A/B 新增列;老 4 列 schema(无 bull/consensus)
    仍兼容,缺列回空串(无 verify.csv 则整表空,老路不破)。
    """
    out: dict[str, dict] = {}
    for r in _read_csv(scan_dir / "verify.csv"):
        if r.get("code"):
            out[str(r["code"]).strip().zfill(6)] = {
                "verdict": _strip(r.get("verdict", "")), "bull": _strip(r.get("bull", "")),
                "bear": _strip(r.get("bear", "")), "trigger": _strip(r.get("trigger", "")),
                "consensus": _strip(r.get("consensus", ""))}
    return out

def _verify_badge(code: str, vmap: dict[str, dict]) -> str:
    v = vmap.get(str(code).zfill(6))
    return _VERDICT_BADGE.get(v["verdict"], v["verdict"]) if v else "—"

def _apply_verify_downgrade(rating: str, verdict: str) -> str:
    """Tier-3 红队折回评级:降级=降一档、否决=至少 Hold(踢出 ≥OW 买单);维持/未验=不变。

    解决『OW⚠️降级』自相矛盾——买单上不该挂系统自己都不信的评级。
    """
    idx = TIER_RANK.get(rating, 99)
    if idx >= len(RATINGS_5_TIER):
        return rating
    if verdict == "降级":
        idx = min(idx + 1, len(RATINGS_5_TIER) - 1)
    elif verdict == "否决":
        idx = max(idx, TIER_RANK["Hold"])
    return RATINGS_5_TIER[idx]

def _load_ensemble(scan_dir: Path) -> dict[str, dict]:
    """读买单复核 ensemble(B10 集成配方;task-11-brief——≥OW **新派**卡各追加 2 独立 run
    取中位)`_ensemble.json` → {code: {ratings, median, spread}}。

    两种产物布局都认(fb_20260714_003 起 L4 = 每股独立 workflow):
    · 旧批量:`_ensemble.json` = `[{"code","ratings":[...],"median":...,"spread":int}]`
    · 新每股:`_ensemble_<code>.json` = 单条 record(dict 或 单元素 list)——每股 workflow 各写
      各的文件,天然无并发写竞态;per-code 文件后读,同 code 覆盖旧批量文件。
    无文件/坏 json → {}(presence-gated,老路不破,同 `_load_verify` 惯例)。
    """
    out: dict[str, dict] = {}

    def _ingest(raw) -> None:
        for r in raw if isinstance(raw, list) else [raw]:
            if isinstance(r, dict) and r.get("code"):
                out[str(r["code"]).strip().zfill(6)] = r

    for p in [scan_dir / "_ensemble.json", *sorted(scan_dir.glob("_ensemble_*.json"))]:
        if not p.exists():
            continue
        try:
            _ingest(json.loads(p.read_text(encoding="utf-8")))
        except Exception:  # noqa: BLE001 — 可选层,坏 json 不挡整份报告发布
            continue
    return out

def _ensemble_flag(rec: dict | None) -> bool:
    """🎭 人裁条件:3 run 分歧 ≥2 档;或复核 run 缺席退化(degraded,N<3)且仍有分歧(spread>0)——
    degraded(复核 run 不齐)时 workflow 与本侧均不折回,仅 ens_flag 强制人裁展示,1 档分歧
    也不许静默消失(T9-11 review Important#2)。"""
    if not rec:
        return False
    spread = int(rec.get("spread") or 0)
    return spread >= 2 or (bool(rec.get("degraded")) and spread > 0)

def _apply_ensemble_fold(rating: str, rec: dict | None) -> str:
    """复核折回:ow_review(默认)只向下(更靠 Sell)折;sell_review 只向温和折(救误卖持仓,
    Wave1 ⑤-3)。degraded(复核 run 不齐)→ 原样不折,交 ens_flag 人裁。
    median/rating 不在五档词表(脏数据)→ 原样不动,不报错。
    """
    if not rec or rec.get("degraded"):
        return rating
    median = rec.get("median")
    if median not in TIER_RANK or rating not in TIER_RANK:
        return rating
    if rec.get("trigger") == "sell_review":
        return median if TIER_RANK[median] < TIER_RANK[rating] else rating
    return median if TIER_RANK[median] > TIER_RANK[rating] else rating

def _ensemble_dissent_lines(emap: dict[str, dict]) -> list[str]:
    """买单复核 spread≥2(3 run 评级分歧 ≥2 档)→ 组合视角节人裁提示行;无分歧 → []。"""
    lines = []
    for code, e in sorted(emap.items()):
        if _ensemble_flag(e):
            ratings = e.get("ratings") or []
            lines.append(f"🎭 买单复核分歧:{code} {len(ratings)} run={ratings},已按中位折回,建议人工复核")
    return lines

def _dump_final_ratings(scan_dir: Path, rows: list[dict]) -> None:
    """P0-2(坏账③修复):把 ensemble/verify 折回后的**终评级**落 `<scan_dir>/_final_ratings.json`
    (`{code: rating}`)。

    此前 retro 归因只读发布报告 `details/*.md` 的**卡面**评级(`parse_rating`)——Tier-3 红队
    降级/否决 + 买单 ensemble 折回都只改了 `build_summary` 内存里的 `rows["rating"]`,从未写回
    卡片文件,导致被折回的 OW(如 06-30 胜宏)仍以卡面 OW 进 attribution,污染 `bought`/评级基率
    (STAGES.md 开放线头 #6)。`retro._buylist` 优先 join 本文件(presence-gated,缺文件回退卡面
    解析,老路不破)。调用时机:两个 fold 循环(verify 降级 + ensemble 折回)都已跑完、`rows.sort`
    之前——此时 `rows` 里的 `rating` 即最终值。IO 失败不阻发布(同 assemble 其余 staging 写手惯例)。
    """
    import contextlib
    with contextlib.suppress(Exception):
        out = {str(r.get("code", "")).zfill(6): r.get("rating", "—")
               for r in rows if r.get("code")}
        (Path(scan_dir) / "_final_ratings.json").write_text(
            json.dumps(out, ensure_ascii=False), encoding="utf-8")
    with contextlib.suppress(Exception):   # Wave5 ②C:早停分桶落盘(0买真机制记账,独立文件
        write_early_stop(scan_dir)         # 不动 _final_ratings.json 的 {code: rating} 契约)

def _build_decision_records(
    scan_dir: Path,
    rows: list[dict],
    vmap: dict[str, dict],
    emap: dict[str, dict],
) -> list:
    """从既有卡面与折回检查点构造结构化事实。"""
    from autoresearch.scan.decision_record import DecisionRecord
    from autoresearch.scan.stage_result import contract_hash_for

    records = []
    qualified = {"Buy", "Overweight"}
    for row in rows:
        code = str(row.get("code", "")).zfill(6)
        text = _decision_text(scan_dir, code)
        gates = gate_status(text or "")
        gate_states = dict.fromkeys(_GATES3, "UNKNOWN")
        if gates is not None:
            gate_states.update(
                {
                    gate: "FAIL" if failed else "PASS"
                    for gate, failed in gates.items()
                }
            )
        early = parse_early_stop(text or "")
        source = row.get("_source_rating", "—")
        post_verify = row.get("_post_verify_rating", source)
        final = row.get("rating", "—")
        verify = vmap.get(code)
        ensemble = emap.get(code)

        if final in qualified:
            first_rejection = None
            reason = "qualified"
        elif text is None:
            first_rejection = "L4_CARD_MISSING"
            reason = "card_missing"
        elif early:
            first_rejection = f"L4_{early['phase']}_EARLY_STOP"
            reason = f"early_stop:{early['phase']}:{early['reason']}"
        elif source not in qualified:
            first_rejection = "L4_RUBRIC"
            failed_gates = [
                gate for gate, state in gate_states.items() if state == "FAIL"
            ]
            reason = "rubric:" + (
                "|".join(failed_gates) if failed_gates else source
            )
        elif post_verify not in qualified:
            first_rejection = "VERIFY"
            reason = f"verify:{(verify or {}).get('verdict', 'unknown')}"
        else:
            first_rejection = "ENSEMBLE"
            reason = f"ensemble:{(ensemble or {}).get('median', 'unknown')}"

        refs = [f"finalists.csv#{code}"]
        if text is not None:
            refs.append(f"details/{code}.md")
        if verify:
            refs.append(f"verify.csv#{code}")
        if ensemble:
            per_code = scan_dir / f"_ensemble_{code}.json"
            refs.append(
                per_code.name if per_code.exists() else f"_ensemble.json#{code}"
            )
        records.append(
            DecisionRecord.build(
                analysis_date=scan_dir.name,
                contract_hash=contract_hash_for(scan_dir),
                code=code,
                source_rating=source,
                rubric_rating=row.get("rubric_suggest") or "—",
                gate_states=gate_states,
                early_stop=early,
                ensemble_ratings=list(
                    row.get("_ensemble_ratings")
                    or (ensemble or {}).get("ratings")
                    or []
                ),
                final_rating=final,
                proposal=_PROPOSAL_BY_RATING.get(final, "—"),
                reason=reason,
                evidence_refs=refs,
                first_rejection_stage=first_rejection,
            )
        )
    return records

def _dump_decision_records(
    scan_dir: Path,
    rows: list[dict],
    vmap: dict[str, dict],
    emap: dict[str, dict],
) -> None:
    """影子双写的完整安全边界；构造或落盘失败均不阻断报告。"""
    from autoresearch.scan.decision_record import safe_write_decision_records

    try:
        records = _build_decision_records(scan_dir, rows, vmap, emap)
    except Exception as exc:  # noqa: BLE001 — 影子事实不能阻断 L5
        print(f"[decision_record] 写入失败: {exc}", file=sys.stderr)
        return
    safe_write_decision_records(scan_dir, records)


load_ensemble = _load_ensemble
