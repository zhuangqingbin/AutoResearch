import importlib

import pytest

from autoresearch.common import workspace as ws
from tests.contracts.test_research_case import case

SPLITS = {"train": ["2026-01-01", "2026-02-01"], "validation": ["2026-02-01", "2026-03-01"],
          "test": ["2026-03-01", "2026-04-01"]}


def module():
    return importlib.import_module("autoresearch.research.casebook")


def test_exclusive_frozen_casebook_and_hash(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    out = ws.context_root() / "cases" / "v1"
    result = module().freeze_casebook([case(engine=ws.ENGINE)], split_spec=SPLITS, output_dir=out)
    assert len(result["sha256"]) == 64
    with pytest.raises(FileExistsError):
        module().freeze_casebook([case(engine=ws.ENGINE)], split_spec=SPLITS, output_dir=out)


def test_same_event_cannot_leak_across_time_split(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    rows = [case(engine=ws.ENGINE), case(case_id="case2", engine=ws.ENGINE,
            analysis_date="2026-03-02", knowledge_cutoff="2026-03-02T15:00:00+08:00", split="test")]
    result = module().freeze_casebook(rows, split_spec=SPLITS, output_dir=ws.context_root() / "cases")
    from autoresearch.research.evidence_refs import read_json_ref
    frozen = read_json_ref(result)
    assert all(r["eligibility"] == "EXCLUDED" for r in frozen["cases"])
    assert all(r["exclusion_reason"] == "RELATED_EVENT_CROSSES_SPLIT" for r in frozen["cases"])


def test_wrong_engine_and_split_rejected(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    for row in [case(engine="claude" if ws.ENGINE == "codex" else "codex"),
                case(engine=ws.ENGINE, split="test")]:
        with pytest.raises(ValueError):
            module().freeze_casebook([row], split_spec=SPLITS, output_dir=ws.context_root() / "bad")


def test_starter_has_40_proposals_and_fixed_quotas():
    rows = module().starter_cases(engine=ws.ENGINE)
    assert len(rows) == 40
    assert len({r["case_id"] for r in rows}) == 40
    assert all(r["label_state"] == "PROPOSED" and r["gold_label"] is None for r in rows)
    assert len({r["failure_type"] for r in rows}) == 10
