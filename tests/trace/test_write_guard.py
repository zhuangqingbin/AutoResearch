from __future__ import annotations

import json

import pytest

from autoresearch.common import workspace as ws
from autoresearch.session_agent import artifacts
from autoresearch.session_agent.workflows.stock import publish_stock
from autoresearch.trace.write_guard import assert_output_path, assert_write_allowed
from tests.forensic_fixtures import redirect_roots
from tests.forensics.support import make_case


@pytest.fixture
def forensic_case(tmp_path, monkeypatch):
    redirect_roots(monkeypatch, tmp_path)
    return make_case("stock-research", "business", tmp_path)


def test_active_run_allows_only_its_workflow_operation(forensic_case):
    assert (
        assert_write_allowed(
            forensic_case.handle.run_id,
            "stock.publish",
            forensic_case.handle.engine,
        )
        == forensic_case.handle
    )
    with pytest.raises(RuntimeError, match="RUN_OPERATION_NOT_OWNED"):
        assert_write_allowed(forensic_case.handle.run_id, "macro.publish", "codex")


def test_unknown_run_does_not_create_a_workspace(tmp_path, monkeypatch):
    redirect_roots(monkeypatch, tmp_path)
    before = list(tmp_path.rglob("*"))
    with pytest.raises(RuntimeError, match="RUN_NOT_FOUND"):
        assert_write_allowed("20260914T120000000000Z", "stock.publish", "codex")
    assert list(tmp_path.rglob("*")) == before


def test_guard_rejects_cross_engine_identity(forensic_case):
    with pytest.raises(RuntimeError, match="RUN_ENGINE_MISMATCH"):
        assert_write_allowed(forensic_case.handle.run_id, "stock.publish", "claude")


def test_real_handle_with_missing_state_fails_closed(forensic_case):
    (forensic_case.handle.workspace / "state.json").unlink()
    before = forensic_case.snapshot_persistent_tree()

    with pytest.raises(RuntimeError, match="RUN_STATE_INVALID"):
        publish_stock(
            forensic_case.handle,
            reports_root=ws.run_reports_root("stock-research") / "missing-state",
        )

    assert forensic_case.snapshot_persistent_tree() == before


def test_output_path_must_stay_in_the_current_engine_root(forensic_case, tmp_path):
    allowed = ws.run_reports_root("stock-research")
    assert_output_path(allowed / "runs/p1/report.md", allowed)
    with pytest.raises(RuntimeError, match="OUTPUT_ROOT_MISMATCH"):
        assert_output_path(tmp_path.parent / "elsewhere/report.md", allowed)


def test_domain_publisher_rejects_bundle_path_escape_before_creating_parent(
    tmp_path,
    monkeypatch,
):
    redirect_roots(monkeypatch, tmp_path)
    case = make_case("macro-research", "business", tmp_path)
    bundle_path = case.handle.staging / "session_outputs/macro.publication.json"
    bundle = json.loads(bundle_path.read_text(encoding="utf-8"))
    bundle["output_name"] = "../../escaped.md"
    bundle_path.write_text(json.dumps(bundle), encoding="utf-8")
    # Bind the malformed publication as an actual input so this probes the path
    # guard after artifact integrity, rather than failing on an absent registry row.
    artifacts.register_artifact(case.handle, "macro.publication.bundle", bundle_path, "WRITE")
    artifacts.bind_artifact_hash(case.handle, "macro.publication.bundle")
    before = case.snapshot_persistent_tree()

    with pytest.raises(RuntimeError, match="OUTPUT_ROOT_MISMATCH"):
        case.produce_changed_report()

    assert case.snapshot_persistent_tree() == before
