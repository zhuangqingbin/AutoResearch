"""Public host-neutral adapter surface."""

from autoresearch.session_agent.hosts.base import (
    HostCapabilityError,
    observe_host,
    render_request,
    validate_receipt,
)

__all__ = [
    "HostCapabilityError", "observe_host", "render_request", "validate_receipt",
]
