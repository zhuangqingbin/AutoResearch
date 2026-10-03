import hashlib
import json

import pytest


def test_producer_archives_actual_input_and_exclusively_freezes(tmp_path):
    from autoresearch.scan.research_provenance import freeze_research_provenance

    source = tmp_path / "actual-input.txt"
    source.write_text("frozen before decision")
    digest = hashlib.sha256(source.read_bytes()).hexdigest()
    target = tmp_path / "staging" / "_research_provenance.json"
    kwargs = {
        "run_id": "20260930T000000000000Z",
        "analysis_date": "2026-09-30",
        "contract_hash": "a" * 64,
        "profile": "single-stage-v1",
        "candidates": {"600000": {"force_full": True, "deep_declared": True}},
        "base_inputs": [{"artifact_id": "card.base", "path": str(source), "sha256": digest}],
    }
    freeze_research_provenance(target, **kwargs)
    value = json.loads(target.read_text())
    archived = target.parent / value["base_inputs"][0]["path"]
    assert archived.read_text() == "frozen before decision"
    assert value["profile"] == "single-stage-v1"
    assert value["candidates"]["600000"]["deep_read_verified"] is None
    with pytest.raises(FileExistsError):
        freeze_research_provenance(target, **kwargs)


def test_producer_refuses_mutated_input_and_self_reported_deep(tmp_path):
    from autoresearch.scan.research_provenance import freeze_research_provenance

    source = tmp_path / "input"
    source.write_text("new")
    kwargs = {
        "run_id": "20260930T000000000000Z",
        "analysis_date": "2026-09-30",
        "contract_hash": "a" * 64,
        "profile": "two-stage-v1",
        "candidates": {},
        "base_inputs": [{"artifact_id": "card.base", "path": str(source), "sha256": "b" * 64}],
    }
    with pytest.raises(ValueError, match="hash"):
        freeze_research_provenance(tmp_path / "a.json", **kwargs)
    kwargs["base_inputs"][0]["sha256"] = hashlib.sha256(source.read_bytes()).hexdigest()
    kwargs["candidates"] = {"600000": {"deep_read_verified": True}}
    with pytest.raises(ValueError, match="deep"):
        freeze_research_provenance(tmp_path / "b.json", **kwargs)


def test_retry_can_finish_partial_archive_but_never_replace_its_bytes(tmp_path):
    from autoresearch.scan.research_provenance import freeze_research_provenance
    source = tmp_path / "source"
    source.write_bytes(b"accepted")
    stage = tmp_path / "staging"
    (stage / "research_inputs").mkdir(parents=True)
    archived = stage / "research_inputs/0000.bin"
    archived.write_bytes(b"different")
    kwargs = {"run_id":"20260930T000000000000Z", "analysis_date":"2026-09-30",
        "contract_hash":"a"*64, "profile":"single-stage-v1", "candidates":{},
        "base_inputs":[{"artifact_id":"raw", "path":str(source), "sha256":hashlib.sha256(b"accepted").hexdigest()}]}
    with pytest.raises(FileExistsError):
        freeze_research_provenance(stage / "_research_provenance.json", **kwargs)
    assert archived.read_bytes() == b"different"
    archived.write_bytes(b"accepted")
    freeze_research_provenance(stage / "_research_provenance.json", **kwargs)
    assert archived.read_bytes() == b"accepted"
