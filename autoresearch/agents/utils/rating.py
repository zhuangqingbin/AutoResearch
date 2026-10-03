"""Shared 5-tier rating vocabulary and a deterministic heuristic parser.

The same five-tier scale (Buy, Overweight, Hold, Underweight, Sell) is used by:
- The Research Manager (investment plan recommendation)
- The Portfolio Manager (final position decision)
- The signal processor (rating extracted for downstream consumers)
- The memory log (rating tag stored alongside each decision entry)

Centralising it here avoids drift between those call sites.
"""

from __future__ import annotations

import re

from autoresearch.contracts.agent_output import L4_CARD

# Canonical, ordered 5-tier scale (most bullish to most bearish).
RATINGS_5_TIER: tuple[str, ...] = (
    "Buy", "Overweight", "Hold", "Underweight", "Sell",
)

_RATING_SET = {r.lower() for r in RATINGS_5_TIER}

# Matches "Rating: X" / "rating - X" / "Rating: **X**" — tolerates markdown
# bold wrappers and either a colon or hyphen separator.
_RATING_LABEL_RE = re.compile(r"rating.*?[:\-][\s*]*(\w+)", re.IGNORECASE)

#: strict 档单一事实源(D8.3):`contracts.agent_output` 的 `L4_CARD` 契约已经声明了
#: 「行首 `**Rating**:` 标签」这个 pattern,并且已经有测试(`tests/contracts/
#: test_registry_parity.py::test_strict_rating_pattern_does_not_take_the_first_word_anywhere`)
#: 拿它跟生产解析器对拍——这里直接复用,不手写第二份正则。
_STRICT_RATING_RE = re.compile(L4_CARD.field("rating").pattern)


def parse_rating(text: str, default: str = "Hold", *, strict: bool = False) -> str | None:
    """Extract a 5-tier rating from prose text.

    Two modes:
    - ``strict=False``(默认,向后兼容旧行为)——两遍启发式:
      1. 先找显式 "Rating: X" 标签行(容忍 markdown 加粗)；
      2. 找不到则退回全文扫到的第一个 5 档评级词。
      两遍都找不到 → 返回 ``default``("Hold")。
    - ``strict=True``(D8.3)——只认 `contracts.agent_output` L4_CARD 契约声明的行首
      `**Rating**:` 标签(容忍 markdown 加粗、全/半角冒号、连字符)；匹配不到 → 返回
      **None**,不再默默兜底猜成 "Hold"(一张卡读不出评级不该被当成"研究员说 Hold"
      悄悄放过——D1.6/D8.3)。

    Returns a Title-cased rating string, or (non-strict) ``default`` / (strict) ``None``.
    """
    if strict:
        m = _STRICT_RATING_RE.search(text)
        if m and m.group(1).lower() in _RATING_SET:
            return m.group(1).capitalize()
        return None

    for line in text.splitlines():
        m = _RATING_LABEL_RE.search(line)
        if m and m.group(1).lower() in _RATING_SET:
            return m.group(1).capitalize()

    for line in text.splitlines():
        for word in line.lower().split():
            clean = word.strip("*:.,")
            if clean in _RATING_SET:
                return clean.capitalize()

    return default


_PROPOSALS = dict(zip(RATINGS_5_TIER, ("BUY", "BUY", "HOLD", "SELL", "SELL")))


def proposal_for_rating(rating: str) -> str:
    """Map a canonical five-tier rating to its action; invalid input never defaults."""
    if not isinstance(rating, str) or rating not in _PROPOSALS:
        raise ValueError(f"invalid five-tier Rating: {rating!r}")
    return _PROPOSALS[rating]


def _anchor_value(text: str, label: str, allowed: tuple[str, ...]) -> str:
    # Inspect every anchor, including malformed later anchors, before accepting one.
    anchors = re.findall(
        rf"(?im)^[ \t]*\**[ \t]*{re.escape(label)}\b[^\r\n]*", text,
    )
    if not anchors:
        raise ValueError(f"missing strict {label} line")
    values = []
    for line in anchors:
        match = re.fullmatch(
            rf"[ \t]*\**[ \t]*{re.escape(label)}[ \t]*\**[ \t]*[:：-][ \t]*"
            r"(?:\*\*)?([A-Za-z]+)(?:\*\*)?[ \t]*(?:[—–-][ \t]+.+)?",
            line, re.IGNORECASE,
        )
        value = next((item for item in allowed
                      if match and item.lower() == match.group(1).lower()), None)
        if value is None:
            raise ValueError(f"invalid strict {label} line: {line}")
        values.append(value)
    if len(set(values)) != 1:
        raise ValueError(f"conflicting {label} lines")
    return values[0]


def validate_rating_and_proposal(text: str) -> tuple[str, str]:
    """Validate all decision anchors and require the action implied by the rating.

    Legacy ``parse_rating`` remains available for historical prose readers.
    New FULL/LITE decision validation uses this fail-closed contract.
    """
    rating = _anchor_value(text, "Rating", RATINGS_5_TIER)
    proposal = _anchor_value(text, "FINAL TRANSACTION PROPOSAL", ("BUY", "HOLD", "SELL"))
    if proposal != proposal_for_rating(rating):
        raise ValueError("Rating and FINAL TRANSACTION PROPOSAL disagree")
    return rating, proposal
