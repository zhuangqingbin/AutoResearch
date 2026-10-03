"""Compatibility gate shared by deterministic legacy orchestration entries."""

LEGACY_ACCESS_REASON = (
    "legacy research has no C4 task-bound dispatch transport; explicit session_v1 remains PILOT"
)


def require_legacy_access() -> None:
    raise ValueError(f"HOST_CAPABILITY_REQUIRED: {LEGACY_ACCESS_REASON}")
