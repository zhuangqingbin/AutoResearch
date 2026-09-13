"""Codex interactive-session capability adapter."""
from __future__ import annotations

from autoresearch.session_agent.hosts.base import observe_host


def profile_from_observation(**observation) -> dict:
    value = {"schema_version": 1, "engine": "codex", **observation}
    return observe_host(value)


__all__ = ["profile_from_observation"]
