#!/usr/bin/env python3
"""Claude transcript 的确定性估算价格表。

价格源（2026-07-28 快照）：
https://platform.claude.com/docs/en/about-claude/pricing

这里估的是 Claude API 标准全球路由的 token 标价，不冒充 Claude 订阅账单。未知模型、
未知速度档一律返回 UNKNOWN，禁止按家族猜价后伪装成精确成本。
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import date

PRICE_SCHEMA_VERSION = 1
PRICE_SOURCE_URL = "https://platform.claude.com/docs/en/about-claude/pricing"
PRICE_SOURCE_EFFECTIVE_DATE = "2026-07-28"


@dataclass(frozen=True)
class PriceProfile:
    """USD / 1M token；顺序与官方价格表一致。"""

    name: str
    input_per_mtok: float
    cache_write_5m_per_mtok: float
    cache_write_1h_per_mtok: float
    cache_read_per_mtok: float
    output_per_mtok: float

    def to_dict(self) -> dict:
        return asdict(self)


_FABLE_5 = PriceProfile("fable5", 10.0, 12.5, 20.0, 1.0, 50.0)
_OPUS_CURRENT = PriceProfile("opus_current", 5.0, 6.25, 10.0, 0.5, 25.0)
_OPUS_LEGACY = PriceProfile("opus_legacy", 15.0, 18.75, 30.0, 1.5, 75.0)
_SONNET_5_INTRO = PriceProfile("sonnet5_intro", 2.0, 2.5, 4.0, 0.2, 10.0)
_SONNET_CURRENT = PriceProfile("sonnet_current", 3.0, 3.75, 6.0, 0.3, 15.0)
_HAIKU_45 = PriceProfile("haiku45", 1.0, 1.25, 2.0, 0.1, 5.0)
_HAIKU_35 = PriceProfile("haiku35", 0.8, 1.0, 1.6, 0.08, 4.0)
_OPUS_FAST = PriceProfile("opus_fast", 10.0, 12.5, 20.0, 1.0, 50.0)


def _as_date(value: str | date) -> date:
    if isinstance(value, date):
        return value
    return date.fromisoformat(str(value)[:10])


def price_for_model(
    model: str | None,
    *,
    as_of: str | date = PRICE_SOURCE_EFFECTIVE_DATE,
    speed: str | None = "standard",
) -> PriceProfile | None:
    """精确 model id → 当日价格；不认识就 None。

    `claude-opus-5` 等 dateless id 在 Claude 4.6 起是固定版本，不是 evergreen alias，
    因此可以安全按完整版本模式定价。
    """
    model_id = str(model or "").lower()
    speed_name = str(speed or "standard").lower()
    when = _as_date(as_of)

    if speed_name == "fast":
        if "claude-opus-5" in model_id or "claude-opus-4-8" in model_id:
            return _OPUS_FAST
        return None
    if speed_name not in {"standard", "normal", "—", ""}:
        return None

    if "claude-fable-5" in model_id or "claude-mythos-5" in model_id:
        return _FABLE_5
    if any(token in model_id for token in (
        "claude-opus-5",
        "claude-opus-4-8",
        "claude-opus-4-7",
        "claude-opus-4-6",
        "claude-opus-4-5",
    )):
        return _OPUS_CURRENT
    if "claude-opus-4-1" in model_id or model_id in {"claude-opus-4", "opus"}:
        return _OPUS_LEGACY
    if "claude-sonnet-5" in model_id:
        return _SONNET_5_INTRO if when <= date(2026, 8, 31) else _SONNET_CURRENT
    if any(token in model_id for token in (
        "claude-sonnet-4-6",
        "claude-sonnet-4-5",
        "claude-sonnet-4",
        "claude-sonnet-3-7",
        "claude-sonnet-3-5",
    )) or model_id == "sonnet":
        return _SONNET_CURRENT
    if "claude-haiku-4-5" in model_id:
        return _HAIKU_45
    if "claude-haiku-3-5" in model_id or model_id == "haiku":
        return _HAIKU_35
    return None


def estimate_usd(
    model: str | None,
    usage: dict,
    *,
    as_of: str | date = PRICE_SOURCE_EFFECTIVE_DATE,
    speed: str | None = "standard",
) -> dict:
    """usage 五分量 → 分项 USD；未知价保持 None。"""
    profile = price_for_model(model, as_of=as_of, speed=speed)
    base = {
        "pricing_schema_version": PRICE_SCHEMA_VERSION,
        "pricing_source": PRICE_SOURCE_URL,
        "source_effective_date": PRICE_SOURCE_EFFECTIVE_DATE,
        "price_profile": profile.name if profile else None,
    }
    if profile is None:
        speed_name = str(speed or "standard").lower()
        reason = (
            f"unsupported_speed:{speed_name}"
            if speed_name not in {"standard", "normal", "—", ""}
            or speed_name == "fast"
            else f"unknown_model:{model or '—'}"
        )
        return {
            **base,
            "pricing_status": "UNKNOWN",
            "pricing_reason": reason,
            "input_usd": None,
            "cache_read_usd": None,
            "cache_write_5m_usd": None,
            "cache_write_1h_usd": None,
            "output_usd": None,
            "total_usd": None,
        }

    def cost(field: str, rate: float) -> float:
        return float(usage.get(field) or 0) * rate / 1_000_000

    parts = {
        "input_usd": cost("input", profile.input_per_mtok),
        "cache_read_usd": cost("cache_read", profile.cache_read_per_mtok),
        "cache_write_5m_usd": cost(
            "cache_create_5m", profile.cache_write_5m_per_mtok
        ),
        "cache_write_1h_usd": cost(
            "cache_create_1h", profile.cache_write_1h_per_mtok
        ),
        "output_usd": cost("output", profile.output_per_mtok),
    }
    return {
        **base,
        "pricing_status": "PRICED",
        "pricing_reason": None,
        **parts,
        "total_usd": sum(parts.values()),
    }
