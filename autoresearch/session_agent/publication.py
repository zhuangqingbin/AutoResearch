"""Publication adapters for completed subscription-session runs."""

from __future__ import annotations

from collections.abc import Callable

_PUBLISHERS: dict[str, Callable[[object], object]] = {}


def register_publisher(run_kind: str, publisher: Callable[[object], object]) -> None:
    if not run_kind or not callable(publisher):
        raise ValueError("run kind and callable publisher required")
    if run_kind in _PUBLISHERS and _PUBLISHERS[run_kind] is not publisher:
        raise RuntimeError(f"publisher already registered: {run_kind}")
    _PUBLISHERS[run_kind] = publisher


def publish(handle):
    """Invoke the registered domain publisher for a finished task graph."""
    try:
        publisher = _PUBLISHERS[handle.contract.run_kind]
    except KeyError as exc:
        raise RuntimeError(
            f"no session publisher registered for {handle.contract.run_kind}"
        ) from exc
    return publisher(handle)


__all__ = ["publish", "register_publisher"]
