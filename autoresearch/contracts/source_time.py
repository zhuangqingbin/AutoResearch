"""Versioned source owners share timestamps without converting precision to certainty."""
from datetime import datetime, timedelta

TIME_FIELDS = frozenset({"published_at", "first_available_at", "received_at"})
SOURCE_TIME_FIELDS = TIME_FIELDS | {"timestamp_precision"}


def latest_possible(value, precision):
    if value is None:
        return None
    stamp = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if stamp.tzinfo is None:
        raise ValueError("source timestamp requires timezone")
    delta = {"second": timedelta(0), "minute": timedelta(minutes=1), "day": timedelta(days=1)}[precision]
    return stamp + delta


def validate_source_times(value):
    if not isinstance(value, dict) or set(value) != SOURCE_TIME_FIELDS:
        raise ValueError("invalid source timing fields")
    precisions = value["timestamp_precision"]
    if not isinstance(precisions, dict) or set(precisions) != TIME_FIELDS:
        raise ValueError("invalid source timestamp precision fields")
    for field in TIME_FIELDS:
        stamp, precision = value[field], precisions[field]
        if stamp is None:
            if precision is not None:
                raise ValueError("unknown source timestamp requires unknown precision")
            continue
        if not isinstance(stamp, str) or precision not in {"second", "minute", "day"}:
            raise ValueError("invalid source timestamp or precision")
        latest_possible(stamp, precision)
    return value
