from __future__ import annotations

from types import SimpleNamespace

from autoresearch.session_agent.workflows import build_plan

from .test_scan_prelude import context, request


def test_all_research_entry_kinds_are_registered_with_session_v1(tmp_path):
    cases = [
        ("scan-market", "AUTO", None, None),
        ("stock-research", "LITE", "600519.SS", "stock"),
        ("macro-research", "LITE", None, None),
        ("sector-research", "LITE", "半导体", None),
        ("dossier-init", "INIT", "600519", None),
    ]
    for kind, mode, subject, asset_type in cases:
        req = {
            **request(),
            "kind": kind,
            "requested_mode": mode,
            "subject": subject,
            "asset_type": asset_type,
        }
        handle = context(tmp_path / kind)
        handle.contract = SimpleNamespace(
            contract_hash="a" * 64,
            config_hash="b" * 64,
            user_config={"mode": mode},
        )
        plan = build_plan(req, handle)
        assert plan["run_kind"] == kind
        assert plan["orchestration_version"] == "session_v1"
