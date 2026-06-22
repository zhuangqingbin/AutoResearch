"""Phase 2: trace schema 含 L1_CHANNELS + L1_RECALL provenance 列。"""
from __future__ import annotations

from autoresearch.trace import schema


def test_l1_channels_schema_registered():
    assert schema.L1_CHANNELS == "L1_channels"
    sch = schema.get_schema(schema.L1_CHANNELS)
    assert sch is not None
    assert set(sch.required) == {"channel", "code"}


def test_l1_recall_has_provenance_optional_cols():
    sch = schema.get_schema(schema.L1_RECALL)
    assert {"recall_channels", "n_channels", "best_rank"} <= set(sch.optional)
