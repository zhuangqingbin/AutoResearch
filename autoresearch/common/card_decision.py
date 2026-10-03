"""Shared pure card semantics and the versioned Markdown decision projection."""

from __future__ import annotations

import json
import re

from autoresearch.agents.utils.rating import (
    RATINGS_5_TIER,
    proposal_for_rating,
    validate_rating_and_proposal,
)
from autoresearch.contracts.agent_output import (
    EARLY_STOP_PHASES,
    OW_GATES,
    RUBRIC_DIMENSIONS,
    STOP_REASONS,
)
from autoresearch.contracts.profiles import CARD_RATING_BANDS_DEFAULT
from autoresearch.contracts.research_card import REQUIRED, validate_card

_RUBRIC_DIMS = RUBRIC_DIMENSIONS
_OW_GATES = OW_GATES
_DIM_SCORE = {"强": 1, "中": 0, "弱": -1}

RATING_BANDS_DEFAULT = CARD_RATING_BANDS_DEFAULT


def _norm_dim(k: str) -> str:
    """维度名归一:技术·资金→技术资金、偿付(爆雷)→偿付,去修饰/空白对齐锚键。"""
    s = str(k)
    for ch in "·()（）爆雷 　":
        s = s.replace(ch, "")
    return s


def rubric_rating(dims: dict, gates: dict, *, bands: dict | None = None) -> tuple[str, str]:
    """C·LLM-as-judge 评分卡:6 维(强+1/中0/弱−1)净分定档 + 3 道 OW 硬门 → 确定性建议评级 + 约束因。

    动机:Sonnet 凭 gestalt 过度多报(实测 6-18:10 OW vs Opus 3 OW),撑大 Tier-2 复核量。把评级
    **派生**自评分卡——净分映射档位,但**任一 OW 门未过则 ≥Overweight 一律压到 Hold**(对齐 Tier-1
    『三条全中才 OW』)。卡片据此自检:`**Rating**` 必须 = 建议,否则显式写 `**偏离**:<硬理由>`。

    dims: {维度: 强|中|弱}(缺/不识别按 中=0;键名容错 技术·资金 / 偿付(爆雷));
    gates: {主力真在|业绩真兑现|估值不透支: bool}(缺按 False 保守)。
    返回 (建议评级, 约束因)。
    """
    nd = {_norm_dim(k): v for k, v in (dims or {}).items()}
    net = sum(_DIM_SCORE.get(str(nd.get(d, "中")).strip(), 0) for d in _RUBRIC_DIMS)
    bands = bands or RATING_BANDS_DEFAULT
    if net >= bands["Buy"]:
        base = "Buy"
    elif net >= bands["Overweight"]:
        base = "Overweight"
    elif net >= bands["Hold"]:
        base = "Hold"
    elif net >= bands["Underweight"]:
        base = "Underweight"
    else:
        base = "Sell"
    order = {r: i for i, r in enumerate(RATINGS_5_TIER)}
    failed = [g for g in _OW_GATES if not (gates or {}).get(g, False)]
    if order[base] < order["Hold"] and failed:  # 想给 ≥OW 但有门没过 → 压 Hold(防过度多报)
        return "Hold", f"净分{net:+d}→{base},OW门未过({'、'.join(failed)})→压Hold"
    suffix = "(OW门3/3)" if order[base] < order["Hold"] else ""
    return base, f"净分{net:+d}→{base}{suffix}"


def validate_card_decision(card: dict, *, bands: dict | None = None) -> tuple[str, str]:
    """Validate structure and research semantics without changing model output.

    This validates the card's rating/action, not E6's conditional entry decision.
    Pinned incomplete cards raise with their evidence intact for coverage accounting.
    """
    from autoresearch.contracts.research_card import validate_card

    validate_card(card)
    if card["schema_version"] == 2 and card["scenario_estimate"] is not None:
        from autoresearch.common.execution_math import validate_scenario_estimate

        validate_scenario_estimate(card["scenario_estimate"])
    suggestion, reason = rubric_rating(
        card["dimensions"],
        {key: state == "PASS" for key, state in card["gates"].items()},
        bands=bands,
    )
    rating = card["initial_rating"]
    if card["proposal"] != proposal_for_rating(rating):
        raise ValueError("card rating and proposal disagree")
    if rating in {"Buy", "Overweight"}:
        if card["early_stop"] is not None:
            raise ValueError("early-stop card cannot recommend Buy/Overweight")
        if any(state != "PASS" for state in card["gates"].values()):
            raise ValueError("Buy/Overweight requires all three OW gates PASS")
    if card["holding"] and any(card["dimensions"][key] == "未核" for key in ("盈利质量", "偿付")):
        raise ValueError("PINNED_QUALITY_UNREVIEWED: pinned card is incomplete")
    if rating != suggestion and not card["rating_deviation_reason"].strip():
        raise ValueError(
            f"rating deviation requires explicit justification ({suggestion}: {reason})"
        )
    return suggestion, reason


DECISION_FIELDS = frozenset(
    {
        "schema_version",
        "subject",
        "venue",
        "dimensions",
        "gates",
        "early_stop",
        "rating_deviation_reason",
        "holding",
    }
)


def card_from_decision_text(
    text: str, *, subject: str, venue: str, analysis_date: str, holding: bool | None = None
) -> dict:
    """Parse the declared v2 block in the authoritative Markdown; never infer identity."""
    matches = re.findall(r"```research-decision-v2\s*\n(.*?)\n```", text, re.S)
    if len(matches) != 1:
        raise ValueError("exactly one research-decision-v2 Markdown block required")
    data = json.loads(matches[0])
    if (
        not isinstance(data, dict)
        or set(data) != DECISION_FIELDS
        or type(data["schema_version"]) is not int
        or data["schema_version"] != 2
    ):
        raise ValueError("invalid research-decision-v2 fields")
    if data["subject"] != subject or data["venue"] != venue:
        raise ValueError("decision subject/venue differs from frozen frame")
    if holding is not None and data["holding"] is not holding:
        raise ValueError("decision holding usage differs from frozen input")
    scenario_blocks = re.findall(r"```conditional-scenarios-v1\s*\n(.*?)\n```", text, re.S)
    if len(scenario_blocks) > 1:
        raise ValueError("at most one conditional-scenarios-v1 block allowed")
    estimate = None
    if scenario_blocks:
        from autoresearch.common.execution_math import validate_scenario_estimate

        estimate = validate_scenario_estimate(json.loads(scenario_blocks[0]))
    from decimal import Decimal

    for display in re.finditer(
        r"(EV(?:目标)?|R:R)[*_`]*[ \t]*[:：|]\s*[*_`]*\s*([-+]?\d+(?:\.\d+)?)(%)?", text, re.I
    ):
        if estimate is None or estimate["entry"] is None:
            raise ValueError("precise EV/R:R needs declared entry")
        metric = display.group(1).upper()
        number = Decimal(display.group(2)) / (100 if display.group(3) else 1)
        if metric.startswith("EV"):
            declared = estimate["ev_range"]
            if (
                declared is None
                or Decimal(declared["low"]) != Decimal(declared["high"])
                or number != Decimal(declared["low"])
            ):
                raise ValueError("displayed EV differs from declared scenario EV")
        elif estimate["rr"] is None or number != Decimal(estimate["rr"]):
            raise ValueError("displayed R:R differs from declared scenario R:R")
    rating, proposal = validate_rating_and_proposal(text)
    card = dict.fromkeys(REQUIRED - {"code"})
    card.update(
        data,
        scenario_estimate=estimate,
        card_origin="parser_bridge_v1",
        analysis_date=analysis_date,
        ruler="gap_c1_o2",
        initial_rating=rating,
        proposal=proposal,
        probability_basis="not_provided",
        confidence=None,
        management="",
        target="未核",
        rr="未核",
        summary="",
        scenarios=[],
        theses=[],
        evidence_refs=[],
        entry_veto=[],
        exec_lines=[],
        tripwires=[],
    )
    return validate_card(card)


def validate_decision_text(
    text: str, *, subject: str, frame: dict, holding: bool | None = None, bands: dict | None = None,
    effective_card: dict | None = None,
    reviewed_card: dict | None = None,
    venue: str | None = None,
) -> dict:
    from autoresearch.contracts.execution import validate_decision_frame

    validate_decision_frame(frame)
    if venue is not None and venue != frame["venue"] and not (
        frame["usage"] == "scan" and {venue, frame["venue"]} <= {"XSHG", "XSHE", "XBSE"}
    ):
        raise ValueError("card venue differs from frozen frame scope")
    card = card_from_decision_text(
        text,
        subject=subject,
        venue=venue or frame["venue"],
        analysis_date=frame["analysis_session"],
        holding=frame["usage"] == "holding_review" if holding is None else holding,
    )
    for projection in (effective_card, reviewed_card):
        if projection is not None and any(
            projection[key] != card[key] for key in card if key not in {"dimensions", "gates"}
        ):
            raise ValueError("effective evidence projection may only change dimensions/gates")
    verified = effective_card or card
    reviewed = reviewed_card or verified
    # Review checks (holding quality reviewed, rating vs. its rubric) read what the model reviewed;
    # the machine suggestion stays the verified view (own declarations remain UNKNOWN until S01).
    validate_card_decision(reviewed, bands=bands)
    suggestion, reason = rubric_rating(
        verified["dimensions"],
        {key: state == "PASS" for key, state in verified["gates"].items()},
        bands=bands,
    )
    return {
        "card": card,
        "effective_card": verified,
        "reviewed_card": reviewed,
        "machine_suggestion": suggestion,
        "machine_reason": reason,
        "execution": {
            "actionability_status": "UNKNOWN"
            if frame["calendar_quality"] == "UNKNOWN"
            else "PENDING_EXECUTION_APPROVAL",
            "decision_frame": frame,
        },
    }


def decision_instruction(*, subject: str, venue: str) -> str:
    sample = {
        "schema_version": 2,
        "subject": subject,
        "venue": venue,
        "dimensions": dict.fromkeys(RUBRIC_DIMENSIONS, "未核"),
        "gates": dict.fromkeys(OW_GATES, "UNKNOWN"),
        "early_stop": None,
        "rating_deviation_reason": "",
        "holding": False,
    }
    return (
        "\n## skills-gap-v3 卡面契约（MD 权威，JSON 仅投影）\n"
        "FULL PM / LITE / scan 最终卡必须保留 Rating 与 FINAL TRANSACTION PROPOSAL 锚，附唯一以下机器块；"
        "把样例未知值换成实际六维/三门证据，不得照抄评级。holding 按冻结用途或 pinned lane；未核盈利质量/偿付阻断持仓完整发布。"
        "Buy/Overweight 三门必须全 PASS 且不得早停；评级偏离机器建议须填具体理由。\n"
        "```research-decision-v2\n" + json.dumps(sample, ensure_ascii=False) + "\n```\n"
        "满卡 early_stop 保持 null；早停卡改为恰好两键的对象 "
        + json.dumps({"phase": "P3", "reason": STOP_REASONS[3]}, ensure_ascii=False)
        + "：phase∈" + "|".join(EARLY_STOP_PHASES) + "（与卡面「**早停**: 停于 P<n>」一致），reason∈"
        + "|".join(STOP_REASONS) + "（与停因词表一致）。\n"
        "有价格情景时附唯一 conditional-scenarios-v1 JSON 块。字段：schema_version=1，entry=null 或 {low,high}正十进制字符串，"
        "probability_basis=not_provided|subjective，scenarios=[{name:bull|base|bear,exit_price,return_range:null|{low,high},probability:null|十进制字符串}]，ev_range=null|{low,high}，rr=null|十进制字符串。"
        "收益区间下界=exit/high(entry)-1，上界=exit/low(entry)-1；例 entry 10.00/10.00、exit 10.20→0.02/0.02。"
        "缺entry时return_range/ev_range/rr全null、卡面EV/R:R标未核；区间entry的rr须null。概率只能主观，三情景合计1，不从置信度生成概率。"
        "holding_review 保留真实成本与退出偏离，不冒充新建仓。\n"
    )
