#!/usr/bin/env python3
"""ResearchCard 的读写(D3/D4):解析桥、版本化读取、行适配。

Q-D ②(2026-09-07):对拍期的候选 JSON **由解析桥从现有 md 派生**,不改 agent 模板——
agent 原生写 JSON 的模板改动与 D4 v5/F3 合并一次人批(解冻后)。所以本模块现在有两条路:

- `card_from_markdown`:现有 owner 解析器(`parse_rating(strict)` / `gate_status` /
  `parse_early_stop` / `_parse_dashboard` / `parse_tripwires`)→ 一张合法 ResearchCard,
  `card_origin="parser_bridge_v1"`。md 里没有的字段(theses / evidence_refs / scenarios /
  六维)按**未知**填(空列表 / 未核),不编。
- `read_card`:按 run 冻结的 `card_source` 读。`research_json_v1` 下缺文件/坏 JSON/错版本/
  身份不符**明确失败**,不 catch 后偷偷退回 legacy;`legacy_md` / `candidate_json` 走注入的
  旧读法(回调注入,避免 card_io → parser → card_io 循环)。

`write_candidate_cards` 是对拍期的入口:每张 md 派生一份 `details/<code>.research.json`,
差异写 `card_compare.json`。**都不供生产决策消费**。
"""
from __future__ import annotations

import json
import re
from pathlib import Path

from autoresearch.agents.utils.rating import parse_rating
from autoresearch.contracts.agent_output import (
    CARD_SCHEMA_VERSION,
    OW_GATES,
    PROPOSALS,
    RUBRIC_DIMENSIONS,
)
from autoresearch.contracts.research_card import validate_card
from autoresearch.scan.l4.parsers import (
    _CONF_RE,
    _DEV_RE,
    _GATESEG_RE,
    _PROPOSAL_RE,
    _decision_text,
    _get,
    _mark_after,
    _parse_dashboard,
    parse_early_stop,
)
from autoresearch.scan.tripwire_watch import parse_tripwires

# 卡面写法「基本面 强 ｜ 估值 中」:名字与档位之间是空白,也可能是冒号/竖线/加粗号。
_DIM_RE = re.compile(
    r"(基本面|估值|技术·?资金|盈利质量|偿付(?:\(爆雷\)|（爆雷）)?|催化)[\s|:：*]*(强|中|弱|未核)(?![\w])")
_DEV_REASON_RE = re.compile(r"\*\*\s*偏离\s*\*\*[:：]?\s*([^\n]+)")
_EXEC_LINE_RE = re.compile(r"^\s*\[执行线\][^\n]*$", re.MULTILINE)
_VETO_RE = re.compile(r"入场否决[^:：\n]*[:：]\s*([^\n｜|]+)")


def _norm_dim(name: str) -> str:
    for ch in "·()（）爆雷 　":
        name = name.replace(ch, "")
    return name


def _dimensions(text: str) -> dict[str, str]:
    dims = dict.fromkeys(RUBRIC_DIMENSIONS, "未核")
    for m in _DIM_RE.finditer(text):
        key = _norm_dim(m.group(1))
        if key in dims and dims[key] == "未核":
            dims[key] = m.group(2)
    return dims


def _gates(text: str) -> dict[str, str]:
    """Preserve the card's three states instead of treating an absent mark as PASS."""
    states = dict.fromkeys(OW_GATES, "UNKNOWN")
    for match in reversed(list(_GATESEG_RE.finditer(text))):
        segment = match.group(0)
        marks = {gate: _mark_after(segment, gate) for gate in OW_GATES}
        if not any(marks.values()):
            continue
        for gate, mark in marks.items():
            if mark == "✓":
                states[gate] = "PASS"
            elif mark == "✗":
                states[gate] = "FAIL"
        break
    return states


def card_from_text(text: str, *, code: str, analysis_date: str, holding: bool,
                   engine: str | None = None) -> dict:
    dash = _parse_dashboard(text)
    rating = parse_rating(text, strict=True) or parse_rating(text)
    prop = _PROPOSAL_RE.search(text)
    conf = _get(dash, "置信度") or (_CONF_RE.search(text).group(1) if _CONF_RE.search(text) else None)
    conf = conf if conf in ("高", "中", "低") else None
    dev = _DEV_REASON_RE.search(text)
    card = {
        "schema_version": CARD_SCHEMA_VERSION, "card_origin": "parser_bridge_v1",
        "code": str(code).zfill(6), "analysis_date": analysis_date, "ruler": "gap_c1_o2",
        "dimensions": _dimensions(text), "gates": _gates(text), "early_stop": parse_early_stop(text),
        "theses": [], "evidence_refs": [],
        "initial_rating": rating,
        "proposal": prop.group(1).upper() if prop and prop.group(1).upper() in PROPOSALS else "HOLD",
        "rating_deviation_reason": (dev.group(1).strip() if dev else ("见卡面" if _DEV_RE.search(text) else "")),
        "confidence": conf, "scenarios": [], "probability_basis": "not_provided",
        "holding": bool(holding), "management": "",
        "target": _get(dash, "EV目标", "目标") or "未核", "rr": _get(dash, "R:R") or "未核",
        "summary": "", "entry_veto": [m.group(1).strip() for m in _VETO_RE.finditer(text)],
        "exec_lines": [m.group(0).strip() for m in _EXEC_LINE_RE.finditer(text)],
        "tripwires": parse_tripwires(text), "base_rate_row": None, "ev": _get(dash, "EV目标") or None,
        "conviction": None, "as_of": None, "engine": engine, "model": None,
    }
    return validate_card(card)


def card_from_markdown(scan_dir: Path | str, code: str, *, analysis_date: str, holding: bool = False,
                       engine: str | None = None) -> dict | None:
    """定位 details/<code>.md → ResearchCard;无卡 → None(不是空卡)。"""
    text = _decision_text(Path(scan_dir), str(code))
    if text is None:
        return None
    return card_from_text(text, code=code, analysis_date=analysis_date, holding=holding, engine=engine)


def read_card(scan_dir: Path | str, code: str, *, card_source: str, legacy_reader):
    """按冻结的 `card_source` 读;`research_json_v1` 缺失/损坏/错身份**明确失败**,不静默 fallback。"""
    if card_source in {"legacy_md", "candidate_json"}:
        return legacy_reader(scan_dir, code)
    if card_source != "research_json_v1":
        raise ValueError("unknown card source")
    path = Path(scan_dir) / "details" / f"{str(code).zfill(6)}.research.json"
    card = validate_card(json.loads(path.read_text(encoding="utf-8")))
    if card["code"] != str(code).zfill(6):
        raise ValueError("card identity mismatch")
    return card


def card_to_row(fr: dict, card: dict) -> dict:
    """ResearchCard → 旧 report model 字段(与 `_finalist_row` 同形),另带 `_card_facts` 给决策记录。"""
    return {
        **fr, "rating": card["initial_rating"], "proposal": card["proposal"],
        "conf": card["confidence"] or "—", "target": card["target"], "rr": card["rr"],
        "l4": card["summary"] or "—", "rubric_dev": bool(card["rating_deviation_reason"]),
        "_card_facts": {"gate_states": dict(card["gates"]), "early_stop": card["early_stop"],
                        "evidence_refs": list(card["evidence_refs"])},
    }


def write_candidate_cards(scan_dir: Path | str, *, analysis_date: str, codes, pinned=(),
                          engine: str | None = None) -> dict:
    """对拍期入口:每张 md 派生一份 `.research.json`,差异写 `card_compare.json`。

    比较对象是 `_finalist_row` 那一侧的旧读法(它就是生产权威),键集见 `card_render.COMPARE_KEYS`;
    旧解析器不支持的键记 unsupported_in_legacy。缺卡的票记 `missing`,不编空卡。
    """
    from autoresearch.scan.l4.card_render import compare_facts
    from autoresearch.scan.l4.parsers import _finalist_row

    scan_dir = Path(scan_dir)
    pinned = {str(c).zfill(6) for c in pinned}
    compare: dict[str, dict] = {"schema_version": 1, "analysis_date": analysis_date,
                                "card_origin": "parser_bridge_v1", "codes": {}, "missing": []}
    for raw in codes:
        code = str(raw).zfill(6)
        card = card_from_markdown(scan_dir, code, analysis_date=analysis_date,
                                  holding=code in pinned, engine=engine)
        if card is None:
            compare["missing"].append(code)
            continue
        path = scan_dir / "details" / f"{code}.research.json"
        path.write_text(json.dumps(card, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
        legacy = _finalist_row(scan_dir, {"code": code, "ticker": code})
        legacy_facts = {"rating": legacy.get("rating"), "proposal": legacy.get("proposal"),
                        "target": legacy.get("target"), "rr": legacy.get("rr")}
        candidate = {"rating": card["initial_rating"], "proposal": card["proposal"],
                     "gate_states": card["gates"], "early_stop": card["early_stop"],
                     "evidence_refs": card["evidence_refs"], "holding": card["holding"],
                     "target": card["target"], "rr": card["rr"]}
        compare["codes"][code] = compare_facts(legacy_facts, candidate)
    (scan_dir / "card_compare.json").write_text(
        json.dumps(compare, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    return compare
