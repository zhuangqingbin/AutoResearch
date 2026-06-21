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
"""
from __future__ import annotations

import hashlib
import json
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
                  confidence: float = 0.6, day: str | None = None, guard: dict | None = None) -> dict:
    """新建或强化一条经验。已存在 → reinforce_count++、last_reinforced 更新、evidence 并集、confidence 升。

    guard={field,op,value}(可选,E·程序性记忆):带 guard 的经验从『建议文本』升为 self_review 的
    **确定性硬门**(发布买单触发即 fail)——经验反复强化后由 retro/feedback skill 给它写 guard 落地。
    """
    day = day or _today()
    lid = slug if slug.startswith("ls_") else f"ls_{slug}"
    recs = _read_jsonl(_LESSONS)
    idx = next((i for i, r in enumerate(recs) if r["id"] == lid), None)
    if idx is None:
        rec = {"id": lid, "scope": _norm_scope(scope), "rule": rule, "evidence": list(evidence),
               "confidence": round(float(confidence), 2), "created": day, "last_reinforced": day,
               "reinforce_count": 1, "status": "active"}
        if guard is not None:
            rec["guard"] = guard
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
            hit = True
    if hit:
        _write_jsonl(_LESSONS, recs)
    return hit


def scope_match(lesson_scope: dict, query_scopes) -> bool:
    """global 经验永远命中;否则 (kind,value) 须在查询集合内。"""
    if lesson_scope.get("kind") == "global":
        return True
    q = {(s["kind"], s["value"]) if isinstance(s, dict) else tuple(s) for s in query_scopes}
    return (lesson_scope.get("kind"), lesson_scope.get("value")) in q


def lessons_for(query_scopes) -> list[dict]:
    """按范围过滤 active 经验,confidence 降序。query_scopes: list[dict|tuple]。"""
    hits = [r for r in _read_jsonl(_LESSONS)
            if r.get("status") == "active" and scope_match(r.get("scope", {}), query_scopes)]
    return sorted(hits, key=lambda r: r.get("confidence", 0), reverse=True)


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


# ───────────────────────── 建议 + 审计 ─────────────────────────


def add_proposal(kind: str, summary: str, rationale: str = "", diff_sketch: str = "",
                 ts: str | None = None) -> dict:
    """结构性改动建议(待批)。kind ∈ {factor,gate,prompt_rule}。"""
    ts = ts or _now_ts()
    day = ts[:10].replace("-", "")
    seq = sum(1 for r in _read_jsonl(_PROPOSALS) if r.get("id", "").startswith(f"pr_{day}_")) + 1
    rec = {"id": f"pr_{day}_{seq:03d}", "ts": ts, "kind": kind, "summary": summary,
           "rationale": rationale, "diff_sketch": diff_sketch, "status": "open"}
    _append_jsonl(_PROPOSALS, rec)
    return rec


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
    "主力净流入是 **1–2 周 swing** 信号,非 T+1。",
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


def render_calibration_block(query_scopes=None, lane="reversion", with_feedback: bool = False) -> str:
    """命中经验时:自学习经验(优先)叠加在 IC 基线上;无命中时:逐字回退基线(老路径不破)。

    lane='trend' → 趋势延续版校准(动量为正、主力还在=健康、只砍衰竭);
    lane='reversion'(默认)→ 原 T+1 均值回归基线;**不带 lane 调用结果与改前逐字一致**。
    with_feedback=True(E1·检索式记忆)→ 额外把**近期同域未蒸馏反馈**注在最前(最高优先,别再犯);
    默认 False → 输出与改前逐字一致(空 store / 全退休仍回退基线)。
    """
    intro, body, baseline = (
        (_TREND_INTRO, _TREND_BODY, _TREND_CALIBRATION) if lane == "trend"
        else (_BASELINE_INTRO, _BASELINE_BODY, _BASELINE_CALIBRATION)
    )
    scopes = query_scopes or [("global", "*")]
    hits = lessons_for(scopes)
    fb = recent_feedback_for(scopes) if with_feedback else []
    if not hits and not fb:
        return baseline               # 无经验无反馈 → 逐字基线(老路径不破)
    lines = ["## ⚠️ 因子方向经验校准(自学习 + IC 基线,**务必写进每个 subagent prompt**)"]
    if fb:
        lines += ["### 近期同域反馈(未蒸馏,最高优先——别再犯)"]
        lines += [_feedback_bullet(f) for f in fb]
    if hits:
        lines += ["### 自学习经验(你的反馈 + retro 复盘,优先级高)"]
        lines += [_lesson_bullet(h) for h in hits]
    lines += ["", "### IC 回测基线", intro, body]
    return "\n".join(lines)


def render_lessons_md() -> str:
    """active 经验的人读视图(按 scope 分组、conf 降序)。"""
    recs = [r for r in _read_jsonl(_LESSONS) if r.get("status") == "active"]
    if not recs:
        return "# 经验库(lessons)\n\n_(空)_\n"
    by_kind: dict[str, list[dict]] = {}
    for r in recs:
        by_kind.setdefault(r.get("scope", {}).get("kind", "global"), []).append(r)
    out = ["# 经验库(lessons) —— 由 lessons.jsonl 渲染,勿手改本文件\n"]
    for kind in ("global", "sector", "industry", "ticker"):
        grp = sorted(by_kind.get(kind, []), key=lambda r: r.get("confidence", 0), reverse=True)
        if not grp:
            continue
        out.append(f"## {kind}")
        for r in grp:
            sc = r.get("scope", {})
            tag = "" if kind == "global" else f"`{sc.get('value')}` "
            out.append(f"- {tag}**{r['rule']}**  "
                       f"_(conf {r.get('confidence', 0):.2f}, 强化 {r.get('reinforce_count', 1)} 次,"
                       f"更新 {r.get('last_reinforced', '?')}; 证据 {'/'.join(map(str, r.get('evidence', [])[:3]))})_")
        out.append("")
    return "\n".join(out)


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


def write_lessons_md(path: str = "context/knowledge/lessons.md") -> Path:
    """把 active 经验渲染落盘成人读 lessons.md(便于手工策展)。"""
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(render_lessons_md(), encoding="utf-8")
    return p


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


if __name__ == "__main__":
    raise SystemExit(_selftest() if "--selftest" in sys.argv else 0)
