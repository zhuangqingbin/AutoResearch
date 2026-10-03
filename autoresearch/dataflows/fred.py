"""FRED (Federal Reserve Economic Data) macro vendor.

Fetches macroeconomic time series — policy rates, Treasury yields, inflation,
labor, growth — from the St. Louis Fed's free API. Used by the news analyst to
ground macro commentary in actual numbers rather than headlines alone.

A free API key (https://fred.stlouisfed.org/docs/api/api_key.html) is read from
``FRED_API_KEY``; if it is unset the vendor raises ``FredNotConfiguredError`` so
the routing layer treats it as "unavailable" rather than a hard crash.
"""
import logging
import os
import re
from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

import requests

from .errors import VendorNotConfiguredError

logger = logging.getLogger(__name__)

ADAPTER_VERSION = "fred.vintage.v1"
FRED_TIMEZONE = ZoneInfo("America/Chicago")

FRED_API_BASE = "https://api.stlouisfed.org/fred"

# Network timeout (seconds) so a stalled request can't hang the agents,
# mirroring the Alpha Vantage client.
REQUEST_TIMEOUT = 30

# Default trailing window when the caller does not specify one. A year captures
# the trend and the year-over-year base for most monthly/quarterly series.
DEFAULT_LOOKBACK_DAYS = 365

# Rows cap for the rendered table: recent values matter most for a decision, and
# daily series (yields, VIX) over a long window would otherwise flood context.
MAX_ROWS = 40

# Curated human-friendly aliases -> FRED series IDs. Anything not listed is used
# verbatim as a raw FRED series ID, so power users are never limited to this set.
MACRO_SERIES = {
    # Policy rate & Treasury yields
    "fed_funds_rate": "FEDFUNDS",
    "federal_funds_rate": "FEDFUNDS",
    "fed_funds": "FEDFUNDS",
    "2y_treasury": "DGS2",
    "10y_treasury": "DGS10",
    "30y_treasury": "DGS30",
    "10y_2y_spread": "T10Y2Y",
    "yield_curve": "T10Y2Y",
    # Inflation
    "cpi": "CPIAUCSL",
    "core_cpi": "CPILFESL",
    "pce": "PCEPI",
    "core_pce": "PCEPILFE",
    "inflation_expectations": "T10YIE",
    # Growth & output
    "real_gdp": "GDPC1",
    "gdp": "GDP",
    "industrial_production": "INDPRO",
    # Labor
    "unemployment_rate": "UNRATE",
    "unemployment": "UNRATE",
    "nonfarm_payrolls": "PAYEMS",
    "payrolls": "PAYEMS",
    "initial_claims": "ICSA",
    # Money & markets
    "m2": "M2SL",
    "money_supply": "M2SL",
    "vix": "VIXCLS",
    "dollar_index": "DTWEXBGS",
    # Sentiment & housing
    "consumer_sentiment": "UMCSENT",
    "housing_starts": "HOUST",
    "retail_sales": "RSAFS",
}


class FredNotConfiguredError(VendorNotConfiguredError):
    """Raised when FRED is selected but no API key is configured.

    A VendorNotConfiguredError (and thus still a ValueError), so the routing
    layer's "vendor unavailable" handling and existing ValueError callers both
    keep working.
    """


def get_api_key() -> str:
    """Retrieve the FRED API key from the environment."""
    api_key = os.getenv("FRED_API_KEY")
    if not api_key:
        raise FredNotConfiguredError(
            "FRED_API_KEY environment variable is not set. Get a free key at "
            "https://fred.stlouisfed.org/docs/api/api_key.html."
        )
    return api_key


def _resolve_series_id(indicator: str) -> str:
    """Map a friendly alias to a FRED series ID, or pass a raw ID through."""
    key = indicator.strip().lower().replace(" ", "_").replace("-", "_")
    if key in MACRO_SERIES:
        return MACRO_SERIES[key]
    # Not a known alias: treat the input as a raw FRED series ID (FRED IDs are
    # conventionally uppercase, e.g. "DGS10", "CPIAUCSL").
    return indicator.strip().upper()


def _request(path: str, params: dict) -> dict:
    """GET a FRED endpoint, surfacing FRED's JSON error body on a bad request."""
    api_params = {**params, "api_key": get_api_key(), "file_type": "json"}
    from autoresearch.common.source_capture import record_source_response

    started_at = datetime.now(timezone.utc).isoformat()
    response = None
    try:
        response = requests.get(
            f"{FRED_API_BASE}/{path}", params=api_params, timeout=REQUEST_TIMEOUT
        )
        if response.status_code == 400:
            try:
                message = response.json().get("error_message", response.text)
            except ValueError:
                message = response.text
            raise ValueError(f"FRED request failed: {message}")
        response.raise_for_status()
        outcome = response.json()
    except BaseException as exc:
        outcome = exc
        raise
    finally:
        ended_at = datetime.now(timezone.utc).isoformat()
        try:
            record_source_response(
                provider="fred", endpoint=path,
                params={**params, "adapter_version": ADAPTER_VERSION},
                outcome=outcome,
                raw_bytes=response.content if response is not None else None,
                consumer_artifact_ids=[],
                started_at=started_at, ended_at=ended_at,
                source_timing={
                    # Point-vintage realtime_start is clipped to the query period;
                    # it is not evidence of the original publication timestamp.
                    "published_at": None, "first_available_at": None,
                    "received_at": ended_at,
                    "timestamp_precision": {
                        "published_at": None, "first_available_at": None,
                        "received_at": "second",
                    },
                },
            )
        except BaseException:
            # Receipt I/O must never turn cancellation into an ordinary vendor
            # failure that the harvest/router would swallow and continue past.
            if not isinstance(outcome, BaseException) or isinstance(outcome, Exception):
                raise

    return outcome


def _iso_date(value: str, field: str) -> date:
    if not isinstance(value, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
        raise ValueError(f"{field} must be YYYY-MM-DD")
    return date.fromisoformat(value)


def _vintage_window(curr_date, vintage_date, knowledge_cutoff):
    _iso_date(curr_date, "curr_date")
    vintage_date = curr_date if vintage_date is None else vintage_date
    vintage = _iso_date(vintage_date, "vintage_date")
    cutoff = curr_date if knowledge_cutoff is None else knowledge_cutoff
    intraday = None
    if isinstance(cutoff, str) and re.fullmatch(r"\d{4}-\d{2}-\d{2}", cutoff):
        cutoff_date = _iso_date(cutoff, "knowledge_cutoff")
    else:
        if not isinstance(cutoff, str) or "T" not in cutoff:
            raise ValueError("knowledge_cutoff must be a date or timezone-aware ISO timestamp")
        intraday = datetime.fromisoformat(cutoff.replace("Z", "+00:00"))
        if intraday.tzinfo is None or intraday.utcoffset() is None:
            raise ValueError("knowledge_cutoff requires timezone")
        cutoff_date = intraday.astimezone(FRED_TIMEZONE).date()
    if vintage > cutoff_date:
        raise ValueError("vintage_date exceeds knowledge_cutoff")
    complete_day = datetime.combine(vintage + timedelta(days=1), time.min, FRED_TIMEZONE)
    known_by_cutoff = intraday is None or complete_day <= intraday
    return vintage_date, cutoff, known_by_cutoff


def _contains_vintage(row, vintage_date):
    """Reject incompatible response intervals without treating them as release times."""
    start, end = row.get("realtime_start"), row.get("realtime_end")
    if start is not None:
        _iso_date(start, "response realtime_start")
    if end is not None:
        _iso_date(end, "response realtime_end")
    return (start is None or start <= vintage_date) and (end is None or vintage_date <= end)


def _series_unavailable_message(indicator: str, series_id: str, detail: str = "") -> str:
    """Instructive message when a FRED series cannot be fetched.

    Returned (not raised) so a bad indicator — e.g. a non-US series an agent
    requests for a foreign ticker, which FRED does not carry — lets the agent
    correct itself or proceed, instead of crashing the whole run. Mirrors the
    ``NO_DATA_AVAILABLE`` sentinel the vendor router returns for missing prices.
    """
    reason = f" ({detail})" if detail else ""
    aliases = ", ".join(sorted(MACRO_SERIES))
    return (
        f"MACRO_DATA_UNAVAILABLE: FRED has no series '{series_id}' for indicator "
        f"'{indicator}'{reason}. FRED covers US macro data only. Retry with a "
        f"known alias ({aliases}) or a valid FRED series ID, or proceed without "
        f"this indicator. Do not fabricate macro values."
    )


def get_macro_data(
    indicator: str,
    curr_date: str,
    look_back_days: int | None = None,
    *,
    vintage_date: str | None = None,
    knowledge_cutoff: str | None = None,
) -> str:
    """Fetch a FRED macroeconomic series as a formatted markdown report.

    Args:
        indicator: A friendly alias (e.g. "cpi", "unemployment", "10y_treasury")
            or a raw FRED series ID (e.g. "CPIAUCSL", "DGS10").
        curr_date: End of the observation window (yyyy-mm-dd). Observation dates
            alone do not constrain subsequent releases or revisions.
        look_back_days: Trailing window length; ``None`` uses DEFAULT_LOOKBACK_DAYS.
        vintage_date: Historical information date, defaulting to curr_date.
        knowledge_cutoff: Research date or timezone-aware ISO timestamp. Defaults
            to curr_date. A same-day vintage cannot establish intraday availability.

    Returns:
        A markdown report with the series title, units, frequency, the latest
        value, the change over the window, and a recent observation table.
    """
    vintage_date, cutoff, known_by_cutoff = _vintage_window(
        curr_date, vintage_date, knowledge_cutoff
    )
    if not known_by_cutoff:
        return ("MACRO_DATA_UNAVAILABLE: intraday availability unknown for "
                f"FRED vintage {vintage_date} (day precision), cutoff {cutoff}. "
                "Use a completed earlier vintage day or timestamped release evidence.")
    realtime = {"realtime_start": vintage_date, "realtime_end": vintage_date}
    if look_back_days is None:
        look_back_days = DEFAULT_LOOKBACK_DAYS

    end_dt = datetime.strptime(curr_date, "%Y-%m-%d")
    start_date = (end_dt - timedelta(days=look_back_days)).strftime("%Y-%m-%d")
    series_id = _resolve_series_id(indicator)

    try:
        metadata = _request("series", {"series_id": series_id, **realtime})
        meta = metadata.get("seriess") or []
        if not _contains_vintage(metadata, vintage_date):
            meta = []
        meta = [row for row in meta if _contains_vintage(row, vintage_date)]
    except FredNotConfiguredError:
        # No API key: let the router treat FRED as "unavailable" (it subclasses
        # ValueError, so this must propagate, not be swallowed below).
        raise
    except ValueError as e:
        # FRED rejected the lookup (e.g. HTTP 400 "series does not exist" for a
        # non-US indicator). Return a recoverable message instead of crashing.
        return _series_unavailable_message(indicator, series_id, detail=str(e))
    if not meta:
        return _series_unavailable_message(indicator, series_id)
    info = meta[0]
    title = info.get("title", series_id)
    units = info.get("units_short") or info.get("units", "")
    frequency = info.get("frequency", "")
    seasonal = info.get("seasonal_adjustment_short", "")

    response = _request(
        "series/observations",
        {
            "series_id": series_id,
            **realtime,
            "observation_start": start_date,
            "observation_end": curr_date,
            "sort_order": "asc",
        },
    )
    observations = response.get("observations", []) if _contains_vintage(response, vintage_date) else []

    # FRED encodes a missing observation as ".".
    points = [
        (o["date"], o["value"])
        for o in observations
        if o.get("value") not in (".", None, "")
        and _contains_vintage(o, vintage_date)
        and o.get("date", "") <= curr_date
    ]

    header = (
        f"## FRED: {title} ({series_id})\n"
        f"- Units: {units}\n"
        f"- Frequency: {frequency}"
        f"{f' ({seasonal})' if seasonal else ''}\n"
        f"- Window: {start_date} to {curr_date}\n"
        f"- Vintage: {vintage_date} (day precision); intraday availability unknown\n"
        f"- Knowledge cutoff: {cutoff}\n"
    )

    if not points:
        return header + (
            f"\nMACRO_DATA_UNAVAILABLE: No observations for {series_id} in this window. The series may "
            f"report less frequently than the window length; widen look_back_days."
        )

    first_date, first_val = points[0]
    last_date, last_val = points[-1]
    try:
        delta = float(last_val) - float(first_val)
        base = float(first_val)
        pct = f" ({delta / base * 100:+.2f}%)" if base != 0 else ""
        summary = (
            f"\n**Latest:** {last_val} ({last_date}) | "
            f"**Change over window:** {delta:+.2f}{pct} "
            f"from {first_val} ({first_date})\n"
        )
    except ValueError:
        summary = f"\n**Latest:** {last_val} ({last_date})\n"

    shown = points
    note = ""
    if len(points) > MAX_ROWS:
        shown = points[-MAX_ROWS:]
        note = f"\n_(showing the most recent {MAX_ROWS} of {len(points)} observations)_\n"

    table = (
        "\n| Date | Value |\n| --- | --- |\n"
        + "\n".join(f"| {d} | {v} |" for d, v in shown)
        + "\n"
    )

    return header + summary + note + table
