#!/usr/bin/env python3
"""闭环学习层 · 知识库读写 + 经验召回/渲染(零 LLM,确定性)。

四个 store(`context/knowledge/`,随 context gitignore):
  * feedback.jsonl  —— 情节记忆:用户对研报的每次反馈(原话 + Claude 蒸馏的病因/纠正规则)。
  * lessons.jsonl   —— 语义记忆:策展后的"经验规则"(真值源;带 confidence/退休)。
  * proposals.jsonl —— 结构性改动建议(待批:新因子/门槛/prompt 规则)。
  * changelog.jsonl —— 自动重标定审计(权重 sha + top 变化,可回滚)。

判断/蒸馏由 Claude 在 session 内做(零付费 LLM);本模块只做确定性的存取 + 注回渲染。
注回核心:render_calibration_block(scopes) —— 把命中经验叠加在 IC 基线上,注入 L2/L3 prompt;
store 空时**逐字回退**到现有手写基线,老路径不破。

用法:uv run --no-sync python -m autoresearch.learning.feedback_store --selftest
     uv run --no-sync python -m autoresearch.learning.feedback_store show <pid>   # 打印提案 diff+evidence
     uv run --no-sync python -m autoresearch.learning.feedback_store apply <pid>  # 打印施工指引(不改文件)
"""
from __future__ import annotations

import difflib
import hashlib
import json
import re
import shutil
import sys
from datetime import datetime
from pathlib import Path

# 真值根目录(可被 set_root 改向,供自测用 tempdir)
KNOW = Path("context/knowledge")

_FEEDBACK = "feedback.jsonl"
_LESSONS = "lessons.jsonl"
_PROPOSALS = "proposals.jsonl"
_CHANGELOG = "changelog.jsonl"


def set_root(path: Path) -> None:
    """改向知识库根目录(自测用)。"""
    global KNOW
    KNOW = Path(path)


def _f(name: str) -> Path:
    return KNOW / name


def _now_ts() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _today() -> str:
    return datetime.now().strftime("%Y-%m-%d")


# ───────────────────────── JSONL 原语 ─────────────────────────


def _read_jsonl(name: str) -> list[dict]:
    p = _f(name)
    if not p.exists():
        return []
    out: list[dict] = []
    for ln in p.read_text(encoding="utf-8").splitlines():
        ln = ln.strip()
        if ln:
            out.append(json.loads(ln))
    return out


def _read_jsonl_tolerant(name: str) -> list[dict]:
    """同 _read_jsonl 但坏行/非 dict 行跳过(看板·nag 用:一行损坏不阻整本渲染)。"""
    p = _f(name)
    if not p.exists():
        return []
    out: list[dict] = []
    for ln in p.read_text(encoding="utf-8").splitlines():
        ln = ln.strip()
        if not ln:
            continue
        try:
            rec = json.loads(ln)
        except json.JSONDecodeError:
            continue
        if isinstance(rec, dict):
            out.append(rec)
    return out


def _append_jsonl(name: str, rec: dict) -> None:
    p = _f(name)
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(rec, ensure_ascii=False, default=str) + "\n")


def _write_jsonl(name: str, recs: list[dict]) -> None:
    p = _f(name)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("".join(json.dumps(r, ensure_ascii=False, default=str) + "\n" for r in recs),
                 encoding="utf-8")


def _norm_scope(scope) -> dict:
    """统一成 {kind,value};接受 dict 或 (kind,value)。"""
    if isinstance(scope, dict):
        return {"kind": scope.get("kind", "global"), "value": scope.get("value", "*")}
    kind, value = scope
    return {"kind": kind, "value": value}


# ───────────────────────── 反馈(情节) ─────────────────────────


def record_feedback(skill: str, scope, report: str, note: str, verdict: str,
                    root_cause: str = "", corrective_rule: str = "",
                    ts: str | None = None, lesson_id: str | None = None) -> dict:
    """落一条用户反馈。verdict ∈ {wrong_rating,missed,false_positive,good_call,process}。"""
    ts = ts or _now_ts()
    day = ts[:10].replace("-", "")
    seq = sum(1 for r in _read_jsonl(_FEEDBACK) if r.get("id", "").startswith(f"fb_{day}_")) + 1
    rec = {"id": f"fb_{day}_{seq:03d}", "ts": ts, "skill": skill, "scope": _norm_scope(scope),
           "report": report, "note": note, "verdict": verdict, "root_cause": root_cause,
           "corrective_rule": corrective_rule, "lesson_id": lesson_id,
           "status": "distilled" if lesson_id else "open"}
    _append_jsonl(_FEEDBACK, rec)
    return rec


# ───────────────────────── 经验(语义) ─────────────────────────


def upsert_lesson(slug: str, scope, rule: str, evidence: list[str],
                  confidence: float = 0.6, day: str | None = None, guard: dict | None = None,
                  regimes: list[str] | None = None) -> dict:
    """新建或强化一条经验。已存在 → reinforce_count++、last_reinforced 更新、evidence 并集、confidence 升。

    guard={field,op,value}(可选,E·程序性记忆):带 guard 的经验从『建议文本』升为 self_review 的
    **确定性硬门**(发布买单触发即 fail)——经验反复强化后由 retro/feedback skill 给它写 guard 落地。
    regimes(可选,R1):经验只在这些 regime 生效(如 ["risk_off","range"]);缺省 = 全 regime
    (老记录兼容)。retro 写经验时标注当日 regime,防 regime 翻转后集体中毒。
    """
    day = day or _today()
    lid = slug if slug.startswith("ls_") else f"ls_{slug}"
    recs = _read_jsonl(_LESSONS)
    idx = next((i for i, r in enumerate(recs) if r["id"] == lid), None)
    if idx is None:
        rec = {"id": lid, "scope": _norm_scope(scope), "rule": rule, "evidence": list(evidence),
               "confidence": round(float(confidence), 2), "created": day, "last_reinforced": day,
               "reinforce_count": 1, "status": "active", "valid_from": day}   # M3·失效记账起点
        if guard is not None:
            rec["guard"] = guard
        if regimes:
            rec["regimes"] = list(regimes)
        recs.append(rec)
    else:
        rec = recs[idx]
        rec["rule"] = rule or rec["rule"]
        rec["scope"] = _norm_scope(scope)
        merged = list(rec.get("evidence", []))
        for e in evidence:
            if e not in merged:
                merged.append(e)
        rec["evidence"] = merged
        rec["confidence"] = round(min(0.95, float(rec.get("confidence", 0.6)) + 0.05), 2)
        rec["last_reinforced"] = day
        rec["reinforce_count"] = int(rec.get("reinforce_count", 1)) + 1
        rec["status"] = "active"
        if guard is not None:                 # 升/更新硬门(None 则保留原 guard,不误清)
            rec["guard"] = guard
        if regimes is not None:               # 同理:None 保留原 regimes
            rec["regimes"] = list(regimes)
        recs[idx] = rec
    _write_jsonl(_LESSONS, recs)
    return rec


def retire_lesson(slug: str, day: str | None = None) -> bool:
    """退休一条经验(regime 翻转 / 停止复现)。"""
    day = day or _today()
    lid = slug if slug.startswith("ls_") else f"ls_{slug}"
    recs = _read_jsonl(_LESSONS)
    hit = False
    for r in recs:
        if r["id"] == lid:
            r["status"] = "retired"
            r["retired"] = day
            r["invalid_at"] = day        # M3·失效时点(退休不删,留时点审计)
            hit = True
    if hit:
        _write_jsonl(_LESSONS, recs)
    return hit


def mtm_update(slug: str, verdict: str, day: str | None = None, note: str = "") -> dict | None:
    """经验 mark-to-market(R2+R7):用市场结果给经验记支持/反驳账,confidence 机械升降。

    verdict ∈ {support, refute};同 lesson 同日同 verdict 幂等。confidence:support +0.03、
    refute **−0.08**(反驳惩罚 > 支持奖励:记忆宁可谦逊),clip [0.20, 0.95]。
    `refute≥3 ∧ refute>support` → 自动 add_proposal 提名摘 guard/退休(**人批,不自动动门**)。
    """
    assert verdict in ("support", "refute"), verdict
    day = day or _today()
    lid = slug if slug.startswith("ls_") else f"ls_{slug}"
    recs = _read_jsonl(_LESSONS)
    idx = next((i for i, r in enumerate(recs) if r["id"] == lid), None)
    if idx is None:
        return None
    rec = recs[idx]
    mtm = rec.setdefault("mtm", {"support": 0, "refute": 0})
    if mtm.get(f"last_{verdict}") == day:                 # 幂等
        return rec
    mtm[verdict] = int(mtm.get(verdict, 0)) + 1
    mtm[f"last_{verdict}"] = day
    if note:
        mtm["note"] = note
    delta = 0.03 if verdict == "support" else -0.08
    rec["confidence"] = round(min(0.95, max(0.20, float(rec.get("confidence", 0.6)) + delta)), 2)
    recs[idx] = rec
    _write_jsonl(_LESSONS, recs)
    if (mtm["refute"] >= 3 and mtm["refute"] > mtm.get("support", 0)
            and not any(p.get("kind") == "lesson" and lid in p.get("summary", "")
                        for p in _read_jsonl(_PROPOSALS))):
        add_proposal("lesson", f"摘除 guard/退休提名: {lid}",
                     rationale=f"MTM 反驳 {mtm['refute']} vs 支持 {mtm.get('support', 0)};"
                               f"rule: {rec.get('rule', '')[:80]}",
                     diff_sketch="人批后 retire_lesson 或 upsert_lesson(guard=…) 摘门")
    return rec


def open_proposals(today: str | None = None) -> list[dict]:
    """R4·看板:open 状态 proposals + 天龄,age 降序(积压最久的最先看见)。"""
    today = today or _today()
    out = [{"id": p.get("id"), "age_days": _days_between(p.get("ts", "")[:10], today),
            "kind": p.get("kind"), "summary": p.get("summary", "")}
           for p in _read_jsonl(_PROPOSALS) if p.get("status") == "open"]
    return sorted(out, key=lambda r: r["age_days"], reverse=True)


# ───────────────────── R4+ · 看板自清洁(机器只整理,不裁决) ─────────────────────
# 降低人工裁决成本:annotate 纯标注(龄/配对/疑失效),nag_lines 渲染紧凑提醒行;
# 裁决永远走人(feedback / scan-retro),这里只把「看一眼看板」的成本压到几行。

_PROPOSAL_ID_RE = re.compile(r"pr_\d{8}_\d{3}")
_MOOT_TERMS = ("carryover", "滞回保席", "观察单", "watchlist", "T+5", "fwd_5")   # 已退役机制词表
_STALE_DAYS = 14


def annotate_open_proposals(recs: list[dict] | None = None, today: str | None = None) -> list[dict]:
    """看板自清洁·纯标注(零裁决):每条 open 提案 → 原字段 + age_days/stale/pair_with/maybe_moot。

    * age_days:ts 与 today 天差;stale = age>14(积压提示)。
    * pair_with:summary/rationale 引用了**另一条 open 提案** id(pr_YYYYMMDD_NNN,排除自己)
      → 记对方 id,提示「一起收」(真实案例:pr_20260714_003 的裁决建议引 pr_20260624_001)。
    * maybe_moot:命中已退役机制词表 → 记命中词列表(仅提示,机制是否真退役由人判)。
    recs 可注入(测试);None → 读 proposals.jsonl(坏行跳过,不阻看板)。
    """
    if recs is None:
        recs = _read_jsonl_tolerant(_PROPOSALS)
    today = today or _today()
    opens = [r for r in recs if r.get("status") == "open"]
    open_ids = {str(r.get("id")) for r in opens}
    out: list[dict] = []
    for r in opens:
        text = f"{r.get('summary', '')}\n{r.get('rationale', '')}"
        age = _days_between(str(r.get("ts", ""))[:10], today)
        pair = next((m for m in _PROPOSAL_ID_RE.findall(text)
                     if m != r.get("id") and m in open_ids), None)
        low = text.lower()
        out.append({**r, "age_days": age, "stale": age > _STALE_DAYS, "pair_with": pair,
                    "maybe_moot": [w for w in _MOOT_TERMS if w.lower() in low]})
    return out


def proposals_nag_lines(max_lines: int = 6, today: str | None = None) -> list[str]:
    """看板 nag 紧凑行(assemble ⏳节用)。排序:🚨/P0 字样 → 有配对 → stale → 龄大。

    行 = `- \\`id\\` [kind·龄d·↔配对·疑失效:词] summary截40`;超过 max_lines 截断并补
    「…共 N 条 open」。只整理不裁决:配对/疑失效都是给人看的收纳提示。
    """
    anns = annotate_open_proposals(today=today)

    def _key(a: dict) -> tuple:
        s = str(a.get("summary", ""))
        return ("🚨" in s or "P0" in s, bool(a.get("pair_with")),
                bool(a.get("stale")), a.get("age_days", 0))

    anns.sort(key=_key, reverse=True)
    lines: list[str] = []
    for a in anns[:max_lines]:
        tags = [str(a.get("kind") or "?"), f"{a.get('age_days', 0)}d"]
        if a.get("pair_with"):
            tags.append(f"↔{a['pair_with']}")
        if a.get("maybe_moot"):
            tags.append("疑失效:" + "/".join(a["maybe_moot"]))
        summ = str(a.get("summary", ""))
        cut = summ[:40] + ("…" if len(summ) > 40 else "")
        lines.append(f"- `{a.get('id', '?')}` [{'·'.join(tags)}] {cut}")
    if len(anns) > max_lines:
        lines.append(f"- …共 {len(anns)} 条 open")
    return lines


def scope_match(lesson_scope: dict, query_scopes) -> bool:
    """global 经验永远命中;否则 (kind,value) 须在查询集合内。"""
    if lesson_scope.get("kind") == "global":
        return True
    q = {(s["kind"], s["value"]) if isinstance(s, dict) else tuple(s) for s in query_scopes}
    return (lesson_scope.get("kind"), lesson_scope.get("value")) in q


_SCOPE_RANK = {"ticker": 3, "industry": 3, "sector": 2, "global": 1}   # 精度:具体 > 泛化


def lessons_for(query_scopes, regime: str | None = None) -> list[dict]:
    """按范围过滤 active 经验;排序 = confidence 降序 → last_reinforced 降序 → scope 精度(R6)。

    regime 给定(R1)→ 带 `regimes` 字段的经验须包含之,否则过滤;缺字段 = 全 regime 生效;
    regime=None → 行为同旧(parity)。query_scopes: list[dict|tuple]。
    """
    hits = [r for r in _read_jsonl(_LESSONS)
            if r.get("status") == "active" and scope_match(r.get("scope", {}), query_scopes)
            and (regime is None or not r.get("regimes") or regime in r["regimes"])]
    return sorted(hits, key=lambda r: (r.get("confidence", 0), r.get("last_reinforced", ""),
                                       _SCOPE_RANK.get(r.get("scope", {}).get("kind"), 0)),
                  reverse=True)


def recent_feedback_for(query_scopes, k: int = 3,
                        verdicts: tuple[str, ...] = ("wrong_rating", "false_positive", "missed"),
                        only_open: bool = True) -> list[dict]:
    """E1·检索式记忆:近期**同域、未蒸馏**(open)的反馈(踩过的坑),scope+verdict 命中,ts 倒序取 k。

    注入判断 prompt 让 agent**在判断当下**就避开刚被用户标错的坑——补『flag 到 distill』之间的延迟
    (蒸馏成 lesson 前,原始反馈也该influence下一轮)。good_call/process 不注入(只防错,不复述对的)。
    """
    fb = [f for f in _read_jsonl(_FEEDBACK)
          if (not only_open or f.get("status") == "open")
          and f.get("verdict") in verdicts
          and scope_match(f.get("scope", {}), query_scopes)]
    return sorted(fb, key=lambda r: r.get("ts", ""), reverse=True)[:k]


def promotion_candidates(min_count: int = 3, min_conf: float = 0.7) -> list[dict]:
    """E2·够格从『建议』升『程序性硬门』的经验:active + 反复强化(count≥min_count)+ 高 conf 且**还没 guard**。

    交 retro/feedback skill 给它写 {field,op,value} → `upsert_lesson(guard=...)` → self_review 自动按它拦。
    """
    return [r for r in _read_jsonl(_LESSONS)
            if r.get("status") == "active" and not r.get("guard")
            and int(r.get("reinforce_count", 1)) >= min_count
            and float(r.get("confidence", 0)) >= min_conf]


# ───────────────────────── M2 · 写入四操作裁决 ─────────────────────────
# 落 lesson 前先结构化召回相似旧条(scope/regime/文本 三信号,**零 embedding**)→ Claude 判
# op ∈ {ADD,UPDATE,DELETE,NOOP} → adjudicate 确定性执行。防经验库长大后重复/矛盾条无人裁决。


def _bigrams(s: str) -> set[str]:
    """字符二元组集合(中文无分词依赖):去标点/空白后取相邻 2-gram。"""
    t = "".join(ch for ch in str(s) if ch.isalnum())      # CJK 亦 isalnum → 保留
    if len(t) < 2:
        return {t} if t else set()
    return {t[i:i + 2] for i in range(len(t) - 1)}


def _jaccard(a: set, b: set) -> float:
    u = a | b
    return len(a & b) / len(u) if u else 0.0


def _scope_sim(sc_a, sc_b) -> float:
    """scope 相似:全等 1.0;任一 global 0.4;同 kind 不同 value 0.5;否则 0。"""
    a, b = _norm_scope(sc_a), _norm_scope(sc_b)
    if a == b:
        return 1.0
    if a.get("kind") == "global" or b.get("kind") == "global":
        return 0.4
    return 0.5 if a.get("kind") == b.get("kind") else 0.0


def similar_lessons(rule: str, scope, regimes: list[str] | None = None, k: int = 5) -> list[dict]:
    """结构化召回 top-k active 相似经验(供 Claude 判 op)。score = 0.5·文本 + 0.3·scope + 0.2·regime。

    regime:任一方为空(=全 regime)视作匹配(1.0),否则 Jaccard。**不用向量**,纯确定性可复现。
    """
    cand_bi = _bigrams(rule)
    ra = set(regimes or [])
    scored: list[tuple[float, dict]] = []
    for r in _read_jsonl(_LESSONS):
        if r.get("status") != "active":
            continue
        txt = _jaccard(cand_bi, _bigrams(r.get("rule", "")))
        scp = _scope_sim(scope, r.get("scope", {}))
        rb = set(r.get("regimes") or [])
        reg = 1.0 if (not ra or not rb) else _jaccard(ra, rb)
        scored.append((0.5 * txt + 0.3 * scp + 0.2 * reg, r))
    scored.sort(key=lambda t: (t[0], t[1].get("last_reinforced", "")), reverse=True)
    return [r for _, r in scored[:k]]


def _supersede(old_id: str, new_id: str, day: str) -> None:
    """DELETE 用:旧条失效(记 invalid_at + superseded_by),不物理删(退休不删)。"""
    recs = _read_jsonl(_LESSONS)
    for r in recs:
        if r["id"] == old_id:
            r["status"] = "retired"
            r["retired"] = day
            r["invalid_at"] = day
            r["superseded_by"] = new_id
    _write_jsonl(_LESSONS, recs)


def adjudicate(op: str, candidate: dict, target_id: str | None = None, day: str | None = None) -> dict | None:
    """确定性执行 Claude 判定的写入 op(判断由 Claude,存取确定性)。candidate={slug,scope,rule,evidence,...}。

    ADD    → 新经验(candidate 独立入库)。
    UPDATE → 折进 target(改写 rule 文本 + evidence 并集 + 强化++),**保 target 的 id/MTM 账**,candidate slug 不入库。
    DELETE → 语义『取代』:candidate 作为新真值入库,target 失效并 superseded_by=新条(退休不删)。
    NOOP   → 重复,不动库。
    每次裁决落 changelog(kind=lesson_adjudicate)可回滚/审计。
    """
    op = op.upper()
    day = day or _today()
    if op == "ADD":
        rec = upsert_lesson(candidate["slug"], candidate["scope"], candidate["rule"],
                            candidate.get("evidence", []), confidence=candidate.get("confidence", 0.6),
                            day=day, regimes=candidate.get("regimes"))
    elif op == "NOOP":
        rec = next((r for r in _read_jsonl(_LESSONS) if r["id"] == target_id), None) if target_id else None
    elif op == "UPDATE":
        if not target_id:
            raise ValueError("UPDATE 需 target_id")
        tgt = next((r for r in _read_jsonl(_LESSONS) if r["id"] == target_id), None)
        if tgt is None:
            raise ValueError(f"UPDATE target 不存在: {target_id}")
        rec = upsert_lesson(target_id, tgt.get("scope", candidate["scope"]), candidate["rule"],
                            candidate.get("evidence", []), day=day)   # 按 id 折入:改写 rule+并集+强化,保 MTM
    elif op == "DELETE":
        if not target_id:
            raise ValueError("DELETE 需 target_id")
        rec = upsert_lesson(candidate["slug"], candidate["scope"], candidate["rule"],
                            candidate.get("evidence", []), confidence=candidate.get("confidence", 0.6),
                            day=day, regimes=candidate.get("regimes"))
        _supersede(target_id, rec["id"], day)
    else:
        raise ValueError(f"未知 op: {op}(仅 ADD/UPDATE/DELETE/NOOP)")
    _append_jsonl(_CHANGELOG, {"id": f"adj_{_now_ts().replace(':', '').replace('-', '')}",
                               "ts": _now_ts(), "kind": "lesson_adjudicate", "op": op,
                               "target_id": target_id, "result_id": rec["id"] if rec else None, "day": day})
    return rec


# ───────────────────────── 建议 + 审计 ─────────────────────────


def add_proposal(kind: str, summary: str, rationale: str = "", diff_sketch: str = "",
                 ts: str | None = None) -> dict:
    """结构性改动建议(待批)。kind ∈ {factor,gate,prompt_rule,prompt_patch}。"""
    ts = ts or _now_ts()
    day = ts[:10].replace("-", "")
    seq = sum(1 for r in _read_jsonl(_PROPOSALS) if r.get("id", "").startswith(f"pr_{day}_")) + 1
    rec = {"id": f"pr_{day}_{seq:03d}", "ts": ts, "kind": kind, "summary": summary,
           "rationale": rationale, "diff_sketch": diff_sketch, "status": "open"}
    _append_jsonl(_PROPOSALS, rec)
    return rec


# ───────────────── prompt_patch(Plan B T1·经验 → 提示词补丁) ─────────────────
# 锚集来自 grep -n "卡契约 v3|超短口径|机构面网查|FINAL TRANSACTION PROPOSAL|Rubric建议|进入P4倾向"
# tests/test_agent_defs.py autoresearch/ —— l4-card 机器契约核心锚串:部分被 self_review/health/
# assemble/l4_reuse 的正则原样解析(卡片契约),部分被 test_agent_defs.py 锁 agent↔playbook 同步;
# proposed_text 绝不能让它们从 target_file 消失,否则下游解析器或契约同步测试失明/失步。
_CONTRACT_ANCHORS = (
    "卡契约 v3",
    "超短口径",
    "机构面网查",
    "FINAL TRANSACTION PROPOSAL",
    "Rubric建议",
    "进入P4倾向",
)
_MAX_OPEN_PROMPT_PATCH = 5   # open 状态 prompt_patch 计数上限;防无节制堆积无人处理


def add_prompt_patch(target_file: str, anchor_text: str, current_text: str,
                     proposed_text: str, evidence: list[str]) -> dict:
    """经验 → 提示词补丁提案:对某 playbook/agent 文案的改写建议,只出建议不自动改文件。

    三重校验(核心安全,任一不过直接 raise,不静默降级/不部分写入):
    ① `target_file` 必须存在,否则 `FileNotFoundError`。
    ② `proposed_text` 不得让 `_CONTRACT_ANCHORS` 任何一个契约锚从 target_file 消失,否则
       `ValueError`——模拟把 target_file 现有全文里的 `current_text` 换成 `proposed_text`,
       原文里有的锚若换后不在了就是删锚,直接拒(`current_text` 为空 = 纯追加,不模拟替换)。
    ③ 当前 open 状态、kind=prompt_patch 的提案数已达 `_MAX_OPEN_PROMPT_PATCH` 时拒绝新起草,
       否则 `RuntimeError`——先清积压(批准/拒绝)再写新的,防看板堆成摆设无人处理。

    起草门槛(见 retro-playbook「起草 prompt_patch」节):同型失误 ≥2 次 + 账本读数支撑才起草,
    不是每次诊断都升级到改提示词文案。`evidence` 拼进 rationale;target_file/anchor_text/
    current_text/proposed_text 打包 JSON 存 diff_sketch,供 Task2 `show` 复原成人读 diff。
    """
    p = Path(target_file)
    if not p.exists():
        raise FileNotFoundError(f"add_prompt_patch: target_file 不存在: {target_file}")

    original = p.read_text(encoding="utf-8")
    after = original.replace(current_text, proposed_text) if current_text else original
    for anchor in _CONTRACT_ANCHORS:
        if anchor in original and anchor not in after:
            raise ValueError(
                f"add_prompt_patch: proposed_text 会让契约锚「{anchor}」从 {target_file} 消失,禁止起草")

    open_n = sum(1 for r in _read_jsonl(_PROPOSALS)
                if r.get("status") == "open" and r.get("kind") == "prompt_patch")
    if open_n >= _MAX_OPEN_PROMPT_PATCH:
        raise RuntimeError(
            f"add_prompt_patch: open 状态 prompt_patch 已有 {open_n} 条"
            f"(上限 {_MAX_OPEN_PROMPT_PATCH}),先批复/拒绝积压再起草新的")

    diff_sketch = json.dumps({
        "target_file": target_file, "anchor_text": anchor_text,
        "current_text": current_text, "proposed_text": proposed_text,
    }, ensure_ascii=False)
    return add_proposal("prompt_patch", f"{anchor_text}({target_file})"[:120],
                        rationale="\n".join(str(e) for e in evidence), diff_sketch=diff_sketch)


def set_proposal_status(pid: str, status: str) -> bool:
    """status ∈ {open,approved,rejected,applied}。"""
    recs = _read_jsonl(_PROPOSALS)
    hit = False
    for r in recs:
        if r["id"] == pid:
            r["status"] = status
            hit = True
    if hit:
        _write_jsonl(_PROPOSALS, recs)
    return hit


# ───────────── Plan B T2 · proposals show/apply 辅助流(prompt_patch 施工辅助) ─────────────
# show = 只读打印 diff+evidence;apply = 只读打印施工指引 + set_proposal_status 收尾。
# 两者都**绝不 Edit/Write target_file**——实际改文件永远是人批后的会话内手改动作(见
# retro-playbook.md「4.5 起草 prompt_patch」节)。

_CONTRACT_FILE_BASENAMES = ("l4-card.md", "lite-playbook.md", "SKILL.md", "STAGES.md")


def _is_contract_file(target_file: str) -> bool:
    """target_file 是否属机器契约文件集(按 basename 命中,不看目录)。

    l4-card.md(agent 定义)/ lite-playbook.md(stock-research 真值源)/ 任意 SKILL.md / STAGES.md
    (各 skill 文档)——命中即由 `test_agent_defs.py`/`test_skill_docs_refs.py` 之一锁着契约锚或
    接线,施工后必须重跑二者(`apply_proposal` 据此在指引末尾强制列出验门命令)。
    """
    return Path(target_file).name in _CONTRACT_FILE_BASENAMES


def _get_proposal(pid: str) -> dict:
    rec = next((r for r in _read_jsonl(_PROPOSALS) if r.get("id") == pid), None)
    if rec is None:
        raise KeyError(f"提案不存在: {pid}")
    return rec


def _prompt_patch_payload(rec: dict) -> dict | None:
    """rec 是合法 prompt_patch 载体(kind 对且 diff_sketch 可解出 target_file)→ payload dict;否则 None。"""
    if rec.get("kind") != "prompt_patch":
        return None
    try:
        payload = json.loads(rec.get("diff_sketch") or "{}")
    except (json.JSONDecodeError, TypeError):
        return None
    return payload if isinstance(payload, dict) and "target_file" in payload else None


def show_proposal(pid: str) -> str:
    """`show <pid>`:prompt_patch 提案 → 人读 target_file + current→proposed diff + evidence。

    kind != prompt_patch 或 diff_sketch 不是预期 JSON payload → 退化打印 summary/rationale/
    diff_sketch 原文(不崩溃,仍可读)。只读 proposals.jsonl,零副作用。pid 不存在 → KeyError。
    """
    rec = _get_proposal(pid)
    lines = [f"# 提案 {rec['id']} · kind={rec.get('kind')} · status={rec.get('status')}",
             f"summary: {rec.get('summary', '')}"]
    payload = _prompt_patch_payload(rec)
    if payload:
        lines.append(f"target_file: {payload.get('target_file', '')}")
        if payload.get("anchor_text"):
            lines.append(f"anchor: {payload['anchor_text']}")
        current = str(payload.get("current_text", ""))
        proposed = str(payload.get("proposed_text", ""))
        diff = list(difflib.unified_diff(current.splitlines(), proposed.splitlines(),
                                         fromfile="current", tofile="proposed", lineterm=""))
        lines += ["", "```diff", *diff, "```"]
    else:
        lines.append(f"diff_sketch: {rec.get('diff_sketch', '')}")
    lines += ["", "evidence:"]
    ev_lines = [ln for ln in str(rec.get("rationale", "")).splitlines() if ln.strip()]
    lines += ([f"- {ln}" for ln in ev_lines] if ev_lines else ["- (无)"])
    return "\n".join(lines)


def apply_proposal(pid: str) -> str:
    """`apply <pid>`:**不自动改文件**——只打印施工指引,实际编辑永远是人批后的会话内手改。

    target_file 命中 `_is_contract_file` → 指引末尾强制列出必跑命令(test_agent_defs.py +
    doc-lint=test_skill_docs_refs.py);随后 `set_proposal_status(pid, "applied")` 收尾
    (退出 `open_proposals` 看板)。

    **硬约束**:本函数从不 Edit/Write target_file、甚至不重新读它——指引里的 current_text/
    proposed_text 全部来自 proposals.jsonl 里已存的 diff_sketch,调用前后 target_file 磁盘内容
    逐字不变(这是安全边界,不是实现细节——测试锁死)。
    """
    rec = _get_proposal(pid)
    lines = [f"# 施工指引 · 提案 {rec['id']}({rec.get('kind')})",
             "以下步骤须在人批后于会话内手工编辑完成——本命令不改任何文件:"]
    payload = _prompt_patch_payload(rec)
    target_file = payload.get("target_file") if payload else None
    if payload and target_file:
        lines += [
            f"1. 打开 target_file: {target_file}",
            "2. 把 current_text 原样替换为 proposed_text(契约锚字符串已由 add_prompt_patch 校验保留):",
            f"   current_text  = {payload.get('current_text', '')!r}",
            f"   proposed_text = {payload.get('proposed_text', '')!r}",
            "3. 保存后人工核对改动与本提案 evidence 一致。",
        ]
        if _is_contract_file(target_file):
            lines += [
                "",
                "**契约文件命中 —— 施工后必跑(强制,勿跳过)**:",
                "    uv run --no-sync python -m pytest tests/test_agent_defs.py "
                "tests/test_skill_docs_refs.py   # test_skill_docs_refs.py = doc-lint",
            ]
    else:
        lines += [f"summary: {rec.get('summary', '')}", f"diff_sketch: {rec.get('diff_sketch', '')}",
                  "(非 prompt_patch 结构或缺 target_file——按 summary/rationale 人工处理,无处方级指引)"]
    lines += ["", f"evidence/rationale: {rec.get('rationale', '')}"]
    set_proposal_status(pid, "applied")
    lines += ["", f"→ 提案 {pid} 状态已置为 applied(收尾;第 2 步手工编辑仍需你确认已完成)。"]
    return "\n".join(lines)


def log_change(retro_date: str, before_sha: str, after_sha: str, top_changes: list[dict],
               panel_dates_n: int, ts: str | None = None, kind: str = "recalibrate") -> dict:
    """自动重标定审计一条。"""
    ts = ts or _now_ts()
    rec = {"id": f"cl_{ts.replace(':', '').replace('-', '')}", "ts": ts, "kind": kind,
           "retro_date": retro_date, "before_sha": before_sha, "after_sha": after_sha,
           "top_changes": top_changes, "panel_dates_n": panel_dates_n}
    _append_jsonl(_CHANGELOG, rec)
    return rec


# ───────────────────────── 注回:校准块渲染 ─────────────────────────

_BASELINE_HEADER = "## ⚠️ 因子方向经验校准(L2/L3/L4 通用,**务必写进每个 subagent prompt**)"
_BASELINE_INTRO = ("来自 `factor_lab` 的 T+1 IC 回测(spec §实证),几条**与直觉相反**、"
                   "上一轮测试中 L2/L3 误读、被 L4 反向打脸的:")
_BASELINE_BODY = "\n".join([
    "- **高获利盘 winner_rate(>90)= 抛压/见顶风险,不是\"筹码健康/顶配\"**(十分位 −42bps)。"
    "低获利盘=套牢盘多=有上行空间。",
    "- **高量比 / 高 RSI(超买)= T+1 偏弱**(vol_ratio −15bps);"
    "`pct_60d 极高 + RSI 高 + winner 满` = **抛物线顶 → 回避**,别当\"强势延续\"。",
    "- **主力**看 `main_net_ratio`(大单+特大单净占比),**散户**看 `retail_net_yi`(小单);"
    "主力净流入对 T+1/T+2 近中性、T+5/10 才最强 —— **超短主尺下不作核心多头论点,仅作共振确认**。",
    "- **价值(低 PE)在 T+1 反而偏弱**(成长/动量续涨);价值用于\"不追高\",非\"次日动量\"。",
    "- **优先留**:涨幅适中(未过热)+ 主力真实进场(main_net_ratio 正)+ 筹码有空间(获利盘不满)"
    "+ 基本面干净;纯动量抛物线顶,L4 大概率 Underweight,别堆到精排顶端。",
])
_BASELINE_CALIBRATION = f"{_BASELINE_HEADER}\n{_BASELINE_INTRO}\n{_BASELINE_BODY}"

# 趋势延续 lane 版校准(L2 双赛道用):不砍强势,只辨健康强势 vs 衰竭顶
_TREND_HEADER = "## ⚠️ 因子方向经验校准 · 趋势延续 lane(**务必写进每个 subagent prompt**)"
_TREND_INTRO = ("趋势 lane:**不砍强势,只辨健康强势 vs 衰竭顶**。"
                "IC 实证:动量(pct_60d 十分位多空 +68bps/t=2.6、above_ma60 t=3.7)T+1 为正——"
                "强势延续是默认假设,别因涨多了就回避。")
_TREND_BODY = "\n".join([
    "- **健康强势 → 留**:涨幅高但 `main_net_ratio ≥ 0`(主力还在)+ `np_yoy > 0`(业绩跟得上);"
    "此时 **winner_rate 满 / RSI 超买不是卖点**(主力没撤就不是派发)。",
    "- **衰竭顶 → 砍**(且仅此):放量滞涨(主力净占比深负 <−4%)、业绩证伪(np_yoy 负)、"
    "满获利盘**且主力流出**、抛物线(涨极高 + RSI≥85 且主力不在)。",
    "- **板块共振 + 龙虎榜接力看持续性**;机构上榜净买入 ≈ 反指(后续偏弱)。",
    "- 仍排:基本面证伪 / 纯题材无主力承接 / 量价背离。",
])
_TREND_CALIBRATION = f"{_TREND_HEADER}\n{_TREND_INTRO}\n{_TREND_BODY}"


def _lesson_bullet(lsn: dict) -> str:
    sc = lsn.get("scope", {})
    tag = "" if sc.get("kind") == "global" else f"[{sc.get('value')}] "
    ev = "/".join(str(e) for e in lsn.get("evidence", [])[:2])
    guard = ""
    if isinstance(lsn.get("guard"), dict):
        g = lsn["guard"]
        guard = f" 〖硬门 {g.get('field')}{g.get('op')}{g.get('value')}〗"
    return f"- {tag}{lsn['rule']}{guard}  _(conf {lsn.get('confidence', 0):.2f}; {ev})_"


def _feedback_bullet(fb: dict) -> str:
    sc = fb.get("scope", {})
    tag = "" if sc.get("kind") == "global" else f"[{sc.get('value')}] "
    rule = fb.get("corrective_rule") or fb.get("root_cause") or fb.get("note", "")
    return f"- {tag}{str(rule)[:60]}  _({fb.get('verdict')}; {fb.get('id')})_"


_LESSON_CAP = 8   # R6·注入 cap:防经验库长大后校准块膨胀成 prompt 噪声


def render_calibration_block(query_scopes=None, lane="reversion", with_feedback: bool = False,
                             regime: str | None = None) -> str:
    """命中经验时:自学习经验(优先)叠加在 IC 基线上;无命中时:逐字回退基线(老路径不破)。

    lane='trend' → 趋势延续版校准(动量为正、主力还在=健康、只砍衰竭);
    lane='reversion'(默认)→ 原 T+1 均值回归基线;**不带 lane 调用结果与改前逐字一致**。
    with_feedback=True(E1·检索式记忆)→ 额外把**近期同域未蒸馏反馈**注在最前(最高优先,别再犯);
    默认 False → 输出与改前逐字一致(空 store / 全退休仍回退基线)。
    regime 给定(R1)→ 只注入当日 regime 适用的经验(带 regimes 标注的按标过滤);None = 老行为。
    经验条目 cap=_LESSON_CAP(R6),命中 ≤cap 时输出与无 cap 逐字一致。
    """
    intro, body, baseline = (
        (_TREND_INTRO, _TREND_BODY, _TREND_CALIBRATION) if lane == "trend"
        else (_BASELINE_INTRO, _BASELINE_BODY, _BASELINE_CALIBRATION)
    )
    scopes = query_scopes or [("global", "*")]
    hits = lessons_for(scopes, regime=regime)
    fb = recent_feedback_for(scopes) if with_feedback else []
    if not hits and not fb:
        return baseline               # 无经验无反馈 → 逐字基线(老路径不破)
    lines = ["## ⚠️ 因子方向经验校准(自学习 + IC 基线,**务必写进每个 subagent prompt**)"]
    if fb:
        lines += ["### 近期同域反馈(未蒸馏,最高优先——别再犯)"]
        lines += [_feedback_bullet(f) for f in fb]
    if hits:
        lines += ["### 自学习经验(你的反馈 + retro 复盘,优先级高)"]
        lines += [_lesson_bullet(h) for h in hits[:_LESSON_CAP]]
        if len(hits) > _LESSON_CAP:
            lines += [f"_(另有 {len(hits) - _LESSON_CAP} 条低置信经验未注入,见 lessons.jsonl)_"]
    lines += ["", "### IC 回测基线", intro, body]
    return "\n".join(lines)


# ───────────────────────── 生命周期 + 审计/回滚(Phase 3) ─────────────────────────


def _days_between(d_old: str, d_new: str) -> int:
    try:
        return (datetime.strptime(d_new, "%Y-%m-%d") - datetime.strptime(d_old, "%Y-%m-%d")).days
    except (TypeError, ValueError):
        return 0


def decay_lessons(today: str | None = None, stale_days: int = 30, step: float = 0.1,
                  min_conf: float = 0.3) -> list[str]:
    """防腐烂:久未强化的经验 confidence 衰减;低于 min_conf 自动退休。每日最多衰减一次(last_decayed 幂等)。"""
    today = today or _today()
    recs = _read_jsonl(_LESSONS)
    changed: list[str] = []
    for r in recs:
        if r.get("status") != "active" or r.get("last_decayed") == today:
            continue
        last = r.get("last_reinforced") or r.get("created") or today
        if _days_between(last, today) > stale_days:
            r["confidence"] = round(max(0.0, float(r.get("confidence", 0.6)) - step), 2)
            r["last_decayed"] = today
            if r["confidence"] < min_conf:
                r["status"] = "retired"
                r["retired"] = today
                r["invalid_at"] = today       # M3·衰减退休同记失效时点
            changed.append(r["id"])
    if changed:
        _write_jsonl(_LESSONS, recs)
    return changed


def snapshot_weights(path: str = "context/factor_lab/weights.json") -> str | None:
    """快照 weights.json → weights.<sha8>.json,返回 sha(供 retro 重标定前留底、回滚)。"""
    p = Path(path)
    if not p.exists():
        return None
    sha = hashlib.sha1(p.read_bytes()).hexdigest()[:8]
    shutil.copy(p, p.with_name(f"weights.{sha}.json"))
    return sha


def rollback_weights(sha: str, path: str = "context/factor_lab/weights.json",
                     ts: str | None = None) -> bool:
    """把 weights.<sha>.json 覆盖回 weights.json,并记一条 rollback 审计。"""
    p = Path(path)
    snap = p.with_name(f"weights.{sha}.json")
    if not snap.exists():
        return False
    cur = hashlib.sha1(p.read_bytes()).hexdigest()[:8] if p.exists() else "none"
    shutil.copy(snap, p)
    log_change("rollback", cur, sha, [], 0, ts=ts, kind="rollback")
    return True


# ───────────────────────── 离线自测 ─────────────────────────


def _selftest() -> int:
    import tempfile
    fails: list[str] = []

    with tempfile.TemporaryDirectory() as td:
        set_root(Path(td) / "knowledge")

        # 1) 反馈 round-trip
        fb = record_feedback("scan-market", ("global", "*"), "reports/x.md",
                             "winner_rate 高被当利好,错了", "wrong_rating",
                             "高获利盘=抛压", "winner_rate>90 视为见顶风险", ts="2026-06-19T10:00:00")
        if fb["id"] != "fb_20260619_001" or fb["status"] != "open":
            fails.append(f"feedback id/status 错: {fb}")
        if len(_read_jsonl(_FEEDBACK)) != 1:
            fails.append("feedback 未落盘")

        # 2) 经验 upsert:新建 → 强化
        upsert_lesson("winner_rate_topping", ("global", "*"),
                      "winner_rate>90=抛压/见顶,非筹码健康;低 winner_rate=有上行空间。",
                      ["factor_lab IC -42bps", "fb_20260619_001"], confidence=0.6, day="2026-06-19")
        l2 = upsert_lesson("winner_rate_topping", ("global", "*"),
                           "winner_rate>90=抛压/见顶,非筹码健康;低 winner_rate=有上行空间。",
                           ["retro 2026-06-19 漏赢家 winner 中位 50"], day="2026-06-20")
        if l2["reinforce_count"] != 2 or l2["confidence"] != 0.65:
            fails.append(f"lesson 强化错: count={l2['reinforce_count']} conf={l2['confidence']}")
        if len(l2["evidence"]) != 3:
            fails.append(f"evidence 未并集: {l2['evidence']}")

        # 3) 范围过滤:global 命中 / 行业命中 / 不相关不命中
        upsert_lesson("electronics_overheat", ("industry", "电子"),
                      "电子板块过热回避", ["x"], day="2026-06-20")
        g = lessons_for([("industry", "医药")])
        if not any(r["id"] == "ls_winner_rate_topping" for r in g):
            fails.append("global 经验未对任意范围命中")
        if any(r["id"] == "ls_electronics_overheat" for r in g):
            fails.append("电子经验对医药范围误命中")
        e = lessons_for([("industry", "电子")])
        if not any(r["id"] == "ls_electronics_overheat" for r in e):
            fails.append("电子经验对电子范围未命中")

        # 4) 渲染:有命中 → 含经验 + 基线;空 store → 逐字基线
        blk = render_calibration_block([("industry", "电子")])
        if "自学习经验" not in blk or "winner_rate>90" not in blk or "IC 回测基线" not in blk:
            fails.append("render 命中态缺经验/基线")
        if "电子板块过热回避" not in blk:
            fails.append("render 未含命中的行业经验")

        # 5) 退休 → 不再命中 + 校准块回退
        retire_lesson("winner_rate_topping", "2026-06-21")
        retire_lesson("electronics_overheat", "2026-06-21")
        if lessons_for([("industry", "电子")]):
            fails.append("退休后仍被 lessons_for 返回")
        if render_calibration_block([("global", "*")]) != _BASELINE_CALIBRATION:
            fails.append("全退休后未逐字回退基线")

        # 6) 建议 + 审计 + 回退基线(空 store)
        pr = add_proposal("gate", "cap_floor 30→20 亿", "近10复盘日 14 个 missed_l0 卡 20-30亿",
                          ts="2026-06-20T18:00:00")
        if pr["status"] != "open" or not set_proposal_status(pr["id"], "approved"):
            fails.append("proposal 写/改状态错")
        log_change("2026-06-19", "aaaa1111", "bbbb2222",
                   [{"group": "momentum", "industry": "__global__", "before": 0.026, "after": 0.031}],
                   23, ts="2026-06-20T18:01:00")
        if len(_read_jsonl(_CHANGELOG)) != 1:
            fails.append("changelog 未落盘")

        # 7) 空 store 渲染 = 逐字基线(老路径不破)
        set_root(Path(td) / "empty")
        if render_calibration_block([("global", "*")]) != _BASELINE_CALIBRATION:
            fails.append("空 store 未逐字回退基线")
        # 7b) 趋势 lane:非基线 + 含趋势经验;reversion(默认)仍逐字基线
        trend_blk = render_calibration_block([("global", "*")], lane="trend")
        if (trend_blk == _BASELINE_CALIBRATION or "趋势 lane" not in trend_blk
                or "主力还在" not in trend_blk):
            fails.append(f"趋势 lane 校准应区别于基线且含趋势经验: {trend_blk[:50]}")
        if render_calibration_block([("global", "*")], lane="reversion") != _BASELINE_CALIBRATION:
            fails.append("reversion lane(默认)未逐字回退基线")

        # 8) Phase 3:经验衰减→退休(幂等)+ 权重快照→回滚
        set_root(Path(td) / "know3")
        upsert_lesson("stale_rule", ("global", "*"), "久未强化的规则", ["x"],
                      confidence=0.35, day="2026-01-01")
        decayed = decay_lessons(today="2026-06-20", stale_days=30)
        recs8 = {r["id"]: r for r in _read_jsonl(_LESSONS)}
        if "ls_stale_rule" not in decayed or recs8["ls_stale_rule"]["status"] != "retired":
            fails.append(f"衰减未退休: {recs8.get('ls_stale_rule')}")
        if decay_lessons(today="2026-06-20", stale_days=30):
            fails.append("同日重复衰减(应幂等)")
        wp = Path(td) / "w" / "weights.json"
        wp.parent.mkdir(parents=True)
        wp.write_text('{"weights":{"__global__":{"momentum":0.02}}}', encoding="utf-8")
        sha = snapshot_weights(str(wp))
        wp.write_text('{"weights":{"__global__":{"momentum":0.99}}}', encoding="utf-8")
        if not rollback_weights(sha, str(wp)) or "0.02" not in wp.read_text(encoding="utf-8"):
            fails.append("快照/回滚未复原 weights")

        # 9) E · 程序性 guard 持久化 + 升门候选 + 检索式反馈注入
        set_root(Path(td) / "know_e")
        lg = upsert_lesson("wr_guard", ("global", "*"), "winner_rate>90=见顶", ["x"],
                           confidence=0.8, day="2026-06-20", guard={"field": "winner_rate", "op": ">", "value": 90})
        if lg.get("guard", {}).get("field") != "winner_rate":
            fails.append("guard 未持久化到经验")
        if not any(isinstance(r.get("guard"), dict) for r in _read_jsonl(_LESSONS)):
            fails.append("guard 未落盘(self_review 取不到)")
        # guard 在 reinforce 时不被 None 误清
        if upsert_lesson("wr_guard", ("global", "*"), "winner_rate>90=见顶", ["y"], day="2026-06-21").get("guard") is None:
            fails.append("reinforce(guard=None)误清了 guard")
        # 够格升门:无 guard + count≥3 + 高conf → 入候选;已带 guard 的不入
        for d in ("2026-06-20", "2026-06-21", "2026-06-22"):
            upsert_lesson("ripe", ("global", "*"), "够格升门", [d], confidence=0.75, day=d)  # conf→0.85,count3
        cand = {r["id"] for r in promotion_candidates(min_count=3, min_conf=0.7)}
        if "ls_ripe" not in cand or "ls_wr_guard" in cand:
            fails.append(f"promotion_candidates 错(应含 ripe、不含已带guard的): {cand}")
        # 检索式反馈:open + 同域 + verdict 命中,with_feedback 注入;默认不注入(老路径不破)
        record_feedback("scan-market", ("industry", "电子"), "reports/y.md", "买了电子高位票次日跌",
                        "false_positive", "高位追涨", "电子高位不追", ts="2026-06-21T09:00:00")
        rf = recent_feedback_for([("industry", "电子")])
        if not rf or rf[0]["verdict"] != "false_positive":
            fails.append(f"recent_feedback_for 未命中近期同域反馈: {rf}")
        blk_fb = render_calibration_block([("industry", "电子")], with_feedback=True)
        if "近期同域反馈" not in blk_fb or "电子高位不追" not in blk_fb:
            fails.append("with_feedback=True 未注入近期反馈")
        if "近期同域反馈" in render_calibration_block([("industry", "电子")]):
            fails.append("默认(with_feedback=False)不应注入反馈(老路径)")
        if "〖硬门 winner_rate>90〗" not in render_calibration_block([("global", "*")]):
            fails.append("带 guard 的经验未在校准块标注硬门")

    if fails:
        print("SELFTEST ❌")
        for f in fails:
            print("  -", f)
        return 1
    print("SELFTEST ✅  反馈/经验 upsert·强化/范围召回/校准块渲染·回退/建议·审计 "
          "+ E(guard 程序性硬门 / 升门候选 / 检索式反馈注入)全过")
    return 0


def main() -> int:
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    if "--selftest" in sys.argv:
        return _selftest()
    if len(args) >= 2 and args[0] == "show":
        print(show_proposal(args[1]))
        return 0
    if len(args) >= 2 and args[0] == "apply":
        print(apply_proposal(args[1]))
        return 0
    print(__doc__)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
