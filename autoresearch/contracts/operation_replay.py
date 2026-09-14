"""Closed replay classification for deterministic session operations.

This is contract vocabulary shared by the session planner and the lower trace
executor.  It intentionally contains no builders or business implementation.
"""

from __future__ import annotations

_SOURCE_REPLAY = frozenset({
    "stock.harvest",
    "macro.harvest",
    "macro.lite.frame",
    "sector.prepare",
    "dossier.prefetch",
    "dossier.skeleton",
    "scan.frame",
    "scan.prelude",
    "scan.sector.prepare",
    "scan.l3.prepare",
    "scan.l4.prepare",
    "scan.l4.slim",
    "scan.usage",
})
_EFFECT_PLAN = frozenset({
    "macro.publish",
    "dossier.publish",
    "scan.observe",
})
_TEST_ONLY = frozenset({"test.noop"})
_COMPUTE = frozenset({
    "research.calculate",
    "stock.validate",
    "stock.publish",
    "stock.full.validate",
    "stock.full.assemble",
    "macro.lite.validate",
    "macro.full.validate",
    "macro.full.assemble",
    "sector.validate",
    "sector.publish",
    "dossier.validate",
    "scan.gate1",
    "scan.sector.skip",
    "scan.l3.lint",
    "scan.l3.repair.skip",
    "scan.l3.repair.apply",
    "scan.l3.merge",
    "scan.gate2.skip",
    "scan.l4.skip",
    "scan.l4.intel.status",
    "scan.l4.intel.disabled",
    "scan.review.plan",
    "scan.review.none",
    "scan.review.decide",
    "scan.review.skip",
    "scan.review3.skip",
    "scan.l4.finalize",
    "scan.l4.complete",
    "scan.assemble",
    "scan.gate4",
})
_CONTROL_ONLY = frozenset({"scan.l4.ticket"})

OPERATION_REPLAY_PARTITIONS = {
    "SOURCE_REPLAY": _SOURCE_REPLAY,
    "EFFECT_PLAN": _EFFECT_PLAN,
    "COMPUTE": _COMPUTE,
    "CONTROL_ONLY": _CONTROL_ONLY,
    "TEST_ONLY": _TEST_ONLY,
}
OPERATION_REPLAY_CLASSIFICATION = {
    operation: classification
    for classification, operations in OPERATION_REPLAY_PARTITIONS.items()
    for operation in operations
}
OPERATIONS_REQUIRING_SOURCE_RECEIPTS = frozenset(
    {*_SOURCE_REPLAY, "dossier.publish", "scan.observe"}
)
_members = [
    operation
    for operations in OPERATION_REPLAY_PARTITIONS.values()
    for operation in operations
]
if len(_members) != len(OPERATION_REPLAY_CLASSIFICATION):
    raise RuntimeError("operation replay classifications must not overlap")


def operation_replay_classification(operation: str) -> str:
    try:
        return OPERATION_REPLAY_CLASSIFICATION[operation]
    except KeyError as exc:
        raise KeyError(f"unknown replay operation: {operation}") from exc


__all__ = [
    "OPERATION_REPLAY_CLASSIFICATION",
    "OPERATION_REPLAY_PARTITIONS",
    "OPERATIONS_REQUIRING_SOURCE_RECEIPTS",
    "operation_replay_classification",
]
