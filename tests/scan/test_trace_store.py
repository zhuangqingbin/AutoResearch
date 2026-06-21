"""TraceStore put/get + manifest round-trips, and Pipeline resume (skip done stages). NO network.

design: docs/specs/2026-06-22-autoresearch-arch-redesign-design.md §D/§A.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from autoresearch.scan.config import ScanConfig
from autoresearch.scan.context import RunContext
from autoresearch.scan.pipeline import Pipeline
from autoresearch.scan.stages.base import Stage
from autoresearch.trace import schema
from autoresearch.trace.store import TraceStore, new_run_id

# ───────────────────────── TraceStore ─────────────────────────


def test_put_get_df_roundtrip(tmp_path):
    store = TraceStore(tmp_path)
    rid = new_run_id()
    df = pd.DataFrame({"code": ["000001", "600519"], "composite": [88.5, 91.2]})
    path = store.put_df(rid, schema.L1_RECALL, df)
    assert path.exists()
    assert path == store.stage_path(rid, schema.L1_RECALL)
    back = store.get_df(rid, schema.L1_RECALL)
    assert list(back["code"]) == ["000001", "600519"]
    assert back["composite"].tolist() == [88.5, 91.2]


def test_put_df_records_stage_in_manifest(tmp_path):
    store = TraceStore(tmp_path)
    rid = new_run_id()
    df = pd.DataFrame({"code": ["600000"], "composite": [50.0]})
    store.put_df(rid, schema.L1_RECALL, df)
    meta = store.get_meta(rid)
    assert meta["stages"][schema.L1_RECALL]["status"] == "done"
    assert meta["stages"][schema.L1_RECALL]["rows"] == 1
    assert "generated_at" in meta["stages"][schema.L1_RECALL]
    assert store.has_stage(rid, schema.L1_RECALL)
    assert store.stage_done(rid, schema.L1_RECALL)


def test_put_meta_merges_top_level_and_stages(tmp_path):
    store = TraceStore(tmp_path)
    rid = new_run_id()
    store.put_df(rid, schema.L0_UNIVERSE, pd.DataFrame({"code": ["600000"]}))
    store.put_meta(rid, {"analysis_date": "2026-06-20", "config": {"recall_n": 1000}})
    store.put_meta(rid, {"l2_engine": "composite-linear"})
    meta = store.get_meta(rid)
    # both top-level keys survive the second merge…
    assert meta["analysis_date"] == "2026-06-20"
    assert meta["l2_engine"] == "composite-linear"
    assert meta["config"]["recall_n"] == 1000
    # …and the stage recorded by put_df is preserved through put_meta merges
    assert meta["stages"][schema.L0_UNIVERSE]["status"] == "done"
    assert "generated_at" in meta


def test_get_df_missing_raises(tmp_path):
    store = TraceStore(tmp_path)
    rid = new_run_id()
    assert not store.has_stage(rid, schema.L2_RANK)
    try:
        store.get_df(rid, schema.L2_RANK)
        raise AssertionError("expected FileNotFoundError")
    except FileNotFoundError:
        pass


def test_schema_coerce_warns_and_fills_missing_required(tmp_path):
    """put_df on a frame missing a required col → coerce fills it NaN (frame still stored)."""
    store = TraceStore(tmp_path)
    rid = new_run_id()
    # L1_RECALL requires {code, composite}; omit composite
    store.put_df(rid, schema.L1_RECALL, pd.DataFrame({"code": ["600000"]}))
    back = store.get_df(rid, schema.L1_RECALL)
    assert "composite" in back.columns
    assert bool(np.isnan(back["composite"].iloc[0]))


# ───────────────────────── Pipeline resume ─────────────────────────


class _CountingStage(Stage):
    """A trivial stage that records each run and writes one tiny artifact."""

    def __init__(self, name: str, artifact: str, calls: list[str], rows: int = 3):
        self.name = name
        self._artifact = artifact
        self._calls = calls
        self._rows = rows

    def outputs(self):
        return [self._artifact]

    def run(self, ctx: RunContext) -> None:
        self._calls.append(self.name)
        df = pd.DataFrame({"code": [f"60000{i}" for i in range(self._rows)],
                           "composite": list(range(self._rows))})
        ctx.trace.put_df(ctx.run_id, self._artifact, df)


def _ctx(tmp_path) -> RunContext:
    return RunContext(analysis_date="2026-06-20", config=ScanConfig(),
                      run_id="20260620_0900", trace=TraceStore(tmp_path))


def test_pipeline_runs_all_stages_first_pass(tmp_path):
    calls: list[str] = []
    stages = [_CountingStage("A", schema.L0_UNIVERSE, calls),
              _CountingStage("B", schema.L1_RECALL, calls)]
    ctx = _ctx(tmp_path)
    Pipeline(stages).run(ctx)
    assert calls == ["A", "B"]
    assert ctx.trace.has_stage(ctx.run_id, schema.L0_UNIVERSE)
    assert ctx.trace.has_stage(ctx.run_id, schema.L1_RECALL)


def test_pipeline_resume_skips_done_stages(tmp_path):
    """Second run with resume=True skips the stage whose output already exists + done."""
    calls: list[str] = []
    stages = [_CountingStage("A", schema.L0_UNIVERSE, calls),
              _CountingStage("B", schema.L1_RECALL, calls)]
    ctx = _ctx(tmp_path)
    Pipeline(stages).run(ctx)                 # A, B
    calls.clear()
    Pipeline(stages).run(ctx, resume=True)    # both done → skip both
    assert calls == []


def test_pipeline_resume_runs_only_missing_stage(tmp_path):
    """If only A's artifact exists, resume runs B but skips A."""
    calls: list[str] = []
    a = _CountingStage("A", schema.L0_UNIVERSE, calls)
    b = _CountingStage("B", schema.L1_RECALL, calls)
    ctx = _ctx(tmp_path)
    a.run(ctx)                                # only A's artifact present
    calls.clear()
    Pipeline([a, b]).run(ctx, resume=True)
    assert calls == ["B"]                     # A skipped, B ran


def test_pipeline_from_stage_forces_rerun(tmp_path):
    """from_stage='B' forces B (and anything after) to rerun even if its artifact exists."""
    calls: list[str] = []
    a = _CountingStage("A", schema.L0_UNIVERSE, calls)
    b = _CountingStage("B", schema.L1_RECALL, calls)
    c = _CountingStage("C", schema.L2_RANK, calls)
    ctx = _ctx(tmp_path)
    Pipeline([a, b, c]).run(ctx)              # A, B, C
    calls.clear()
    Pipeline([a, b, c]).run(ctx, resume=True, from_stage="B")
    # A skipped (resume + done), B & C forced
    assert calls == ["B", "C"]
