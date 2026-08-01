"""Verify/ensemble folds and canonical DecisionRecord finalization."""
from __future__ import annotations

import contextlib
import json
import sys
from dataclasses import asdict, dataclass
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

# ── Wave10 A1:复核分歧的结构化事实 ────────────────────────────────────────
#
# 立案现场(2026-07-31,920179 凯德石英):`ensemble=[Underweight,Sell,Sell]`,median=Sell,
# trigger=sell_review,spread=1。单向阀按设计**不**把持仓评级改得更悲观(Wave1 ⑤-3 救误卖),
# 于是终评仍是卡面的 UW —— 这一步是对的。**错的是报告里一个字都没有**:
# 三次独立复核有两次说 Sell,读者完全看不到。
#
# 修法不是改阀门(§7 非目标),是让这件事**有名字、有记录、有一行字**。措辞上不写「未折回」
# ——那读起来像 bug;写「持仓保护规则……单向阀未加重终评」,因为它确实是有意行为。
DISSENT_SCHEMA_VERSION = 1
DISSENT_HUMAN_REVIEW = "HUMAN_REVIEW"                    # spread≥2 或 degraded:要人裁
DISSENT_PINNED_SELL_PROTECTION = "PINNED_SELL_PROTECTION"  # 单向阀吃掉的持仓分歧:要可见
_RATING_SHORT = {"Overweight": "OW", "Underweight": "UW"}


def _short(rating: str) -> str:
    return _RATING_SHORT.get(str(rating), str(rating))


@dataclass(frozen=True)
class DissentRecord:
    """一次复核分歧的结构化事实。渲染层只读它,不许自己再从 emap 推一遍。"""
    schema_version: int
    code: str
    lane: str
    trigger: str
    card_rating: str        # 卡面(复核之前)
    median_rating: str      # 复核中位
    final_rating: str       # 终评(阀门之后)
    ratings: tuple[str, ...]
    spread: int
    degraded: bool
    kind: str

    def to_dict(self) -> dict:
        return {**asdict(self), "ratings": list(self.ratings)}


def build_dissent_records(
    rows: list[dict],
    emap: dict[str, dict],
) -> list[DissentRecord]:
    """折回循环跑完后的 `rows` × ensemble → 分歧事实。`rows` 必须已含终评级。"""
    out: list[DissentRecord] = []
    for row in sorted(rows, key=lambda r: str(r.get("code", ""))):
        code = str(row.get("code", "") or "").zfill(6)
        rec = emap.get(code)
        if not rec:
            continue
        card = str(row.get("_source_rating", "—"))
        median = str(rec.get("median", "—"))
        final = str(row.get("rating", "—"))
        lane = str(row.get("lane", "") or "").strip()
        trigger = str(rec.get("trigger", "") or "")
        if _ensemble_flag(rec):
            kind = DISSENT_HUMAN_REVIEW
        elif (lane == "pinned" and trigger == "sell_review"
              and median != card and median in TIER_RANK and card in TIER_RANK):
            kind = DISSENT_PINNED_SELL_PROTECTION
        else:
            continue        # 非持仓的 spread=1 仍然静默:那不是需要人看的事
        out.append(DissentRecord(
            schema_version=DISSENT_SCHEMA_VERSION, code=code, lane=lane,
            trigger=trigger, card_rating=card, median_rating=median,
            final_rating=final, ratings=tuple(rec.get("ratings") or []),
            spread=int(rec.get("spread") or 0),
            degraded=bool(rec.get("degraded")), kind=kind))
    return out


def dissent_line(rec: DissentRecord) -> str:
    """一条分歧 → 一行报告文本。三个评级字段都取自记录,不在这里重新推导。"""
    if rec.kind == DISSENT_PINNED_SELL_PROTECTION:
        harsher = TIER_RANK[rec.median_rating] > TIER_RANK[rec.card_rating]
        relation = "更悲观" if harsher else "更温和"
        return (f"⚠️ 持仓保护规则:{rec.code} 复核中位 {_short(rec.median_rating)} "
                f"比卡面 {_short(rec.card_rating)} {relation};"
                f"单向阀未加重终评(终评 {_short(rec.final_rating)})")
    return (f"🎭 买单复核分歧:{rec.code} {len(rec.ratings)} run={list(rec.ratings)},"
            f"已按中位折回,建议人工复核")


def dump_dissent_records(scan_dir: Path, records: list[DissentRecord]) -> None:
    """落 `<scan_dir>/dissent_records.json` 供 publisher 卡头渲染。IO 失败不阻发布。"""
    with contextlib.suppress(Exception):
        (Path(scan_dir) / "dissent_records.json").write_text(
            json.dumps([r.to_dict() for r in records], ensure_ascii=False, indent=2),
            encoding="utf-8")


def load_dissent_records(scan_dir: Path | str) -> dict[str, dict]:
    """`{code: record}`;缺文件/坏文件 → {}(presence-gated,老路不破)。"""
    path = Path(scan_dir) / "dissent_records.json"
    if not path.exists():
        return {}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return {}
    return {str(r.get("code", "")).zfill(6): r for r in raw if isinstance(r, dict)}


def _ensemble_dissent_lines(emap: dict[str, dict],
                            rows: list[dict] | None = None) -> list[str]:
    """组合视角节的人裁提示行。

    `rows` 缺省 → 只出 spread≥2 的 🎭 行(与 A1 之前逐字一致,老调用点不破);
    传入 → 同时出持仓保护规则行(需要 lane / 卡面评级,只有 rows 里有)。
    """
    if rows is not None:
        return [dissent_line(r) for r in build_dissent_records(rows, emap)]
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


def _tripwire_hits(scan_dir, analysis_date: str, codes: list[str]) -> list[dict]:
    """薄封装(测试 monkeypatch 此函数,不去 mock 整个 tripwire_watch)。

    `card_before=analysis_date`(Wave9 final-fix I-2):两尺分歧框比的是"用户今天实际
    在操作的那条线"(来自**前一交易日**及更早的卡),不能是本次 assemble 刚写完的
    今日卡——那条线是 LLM 今天拿着今天收盘写的,几乎自洽,测不出真实分歧
    (final-review Important-2,07-29/300857 实例:含今日时线=194.73~233.27 把
    205.00 包在带内测不出冲突;严格早于今日时线=210.01,才是当时真实在用的那条)。
    """
    from autoresearch.learning import tripwire_watch
    root = Path(scan_dir).parent if scan_dir else Path("context/scan")
    return tripwire_watch.check(analysis_date, codes=codes, scan_root=root, card_before=analysis_date)


def tripwire_conflicts(scan_dir, analysis_date: str,
                       pinned_rows: list[dict]) -> dict[str, dict]:
    """确定性尺(tripwire 价格线)与 LLM 终评的**冲突集**(Wave9 A-2)。

    冲突 = 保送票**严格早于今天**的最新一张卡的 **价格线** tripwire,拿今日收盘一测,
    触发 ∧ 今日终评 ≠ Sell。
    (2026-07-29 实测:300857 用户当时**在用**的线来自 07-28 的卡(跌破 210.01 清仓),
     07-29 双复核终评 Underweight="减仓",报告里两头各说各话、无裁决材料 —— 本函数只
     **判定并呈现**冲突,**绝不合并**。)

    **Wave9 final-fix I-2**:比对的必须是**前一交易日及更早**的卡(`_tripwire_hits`
    → `tripwire_watch.check(..., card_before=analysis_date)`),不能是本次 assemble
    刚写完的今日卡——否则比的是"LLM 今天拿着今天收盘写的线"(几乎自洽,测不出真实
    分歧,final-review Important-2 实测活体产出≈0)。若该票此前从没写过卡(首次覆盖)
    → `tripwire_watch.check` 对该 code 静默跳过(`latest_card` 返回 None),不报错、
    不出条,自然汇入空结果。

    `date`/`event` 型命中是提醒(披露日临近/新闻旗),与评级不构成对立,不收。

    评级键名:核实生产真实字段(`_finalist_row`/`_buylist_table_lines` 均取 `row["rating"]`,
    与本函数测试夹具的构造一致)—— 就是 `"rating"`,故不需要多键名兜底。

    **同票多条价格线全部收录**(复核 Wave9 A-2 · Minor→must-fix,2026-07-30):一张卡可以
    同时挂多条 `[价格线]`(实例:300857 07-28 卡同时有「跌破 210.01 清仓」+「跌破 196.73
    已应清仓,若仍持有立即处置」),当日可能不止一条同时触发。旧版用 `out.setdefault`
    只留 hits 里**先出现**的一条,会把更紧急的那条静默吞掉——这个框存在的全部意义就是
    把材料摆给人裁,挑一条藏一条是本末倒置。现改为:`all_hits` 是该 code 全部 price 命中
    的结构化列表(`[{"detail": str, "raw": str, "card_date": str}, ...]`,按 hits 原始
    顺序,不排序不去重不挑选;`card_date` = 这条线来自哪一天的卡,给 `_conflict_block`
    渲染用);`tripwire_detail`/`tripwire_raw` 降级为**向后兼容的摘要字段**——全部命中按
    " ｜ " 拼接(单条命中时与旧版逐字节相同,`_conflict_block` 等旧调用方不必改)。
    """
    rows = [r for r in (pinned_rows or []) if r.get("code")]
    if not rows:
        return {}
    rating_of = {str(r["code"]).zfill(6): str(r.get("rating", "") or "") for r in rows}
    try:
        hits = _tripwire_hits(scan_dir, analysis_date, list(rating_of))
    except Exception:  # noqa: BLE001 — advisory 层,坏了不挡发布
        return {}

    out: dict[str, dict] = {}
    for h in hits:
        if h.get("kind") != "price":
            continue
        code = str(h.get("code", "")).zfill(6)
        rating = rating_of.get(code, "")
        if not rating or rating == "Sell":
            continue
        rec = out.setdefault(code, {"rating": rating, "all_hits": []})
        rec["all_hits"].append({"detail": str(h.get("detail", "")), "raw": str(h.get("raw", "")),
                                 "card_date": str(h.get("card_date", "") or "")})
    for rec in out.values():
        rec["tripwire_detail"] = " ｜ ".join(h["detail"] for h in rec["all_hits"] if h["detail"])
        rec["tripwire_raw"] = " ｜ ".join(h["raw"] for h in rec["all_hits"] if h["raw"])
    return out
