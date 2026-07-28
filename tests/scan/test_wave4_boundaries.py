"""Wave 4 module ownership and compatibility contracts."""
from __future__ import annotations

import ast
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def test_l4_legacy_exports_are_owned_by_domain_modules():
    from autoresearch.scan.agents import l4_card
    from autoresearch.scan.l4 import context, dispatch, parsers, producers, prompts, rubric

    assert l4_card.parse_ratings_from_details is parsers.parse_ratings_from_details
    assert l4_card.rubric_rating is rubric.rubric_rating
    assert l4_card.compose_funnel_brief is context.compose_funnel_brief
    assert l4_card.write_dispatch_pack is prompts.write_dispatch_pack
    assert l4_card.dispatch_plan is dispatch.dispatch_plan
    assert l4_card.fetch_pledge is producers.fetch_pledge


def test_l3_legacy_exports_are_owned_by_domain_modules():
    from autoresearch.scan.agents import l3_select
    from autoresearch.scan.l3 import evidence, merge, prompt, triage, validation

    assert l3_select.harvest_l3_evidence is evidence.harvest_l3_evidence
    assert l3_select.triage_l2_for_l3 is triage.triage_l2_for_l3
    assert l3_select.l3_table_md is prompt.l3_table_md
    assert l3_select.merge_l3_finalists_v3 is merge.merge_l3_finalists_v3
    assert l3_select.lint_judged is validation.lint_judged


def test_assemble_legacy_exports_are_owned_by_l5_modules():
    from autoresearch.scan import assemble, decision_finalize, publisher, report_sections
    from autoresearch.scan.l4 import parsers

    assert assemble.gate_status is parsers.gate_status
    assert assemble._load_ensemble is decision_finalize.load_ensemble
    assert assemble.build_summary is report_sections.build_summary
    assert assemble._publish_details is publisher.publish_details
    assert assemble.run is publisher.run


def test_legacy_entrypoints_are_thin_adapters():
    paths = (
        ROOT / "autoresearch/scan/assemble.py",
        ROOT / "autoresearch/scan/agents/l3_select.py",
        ROOT / "autoresearch/scan/agents/l4_card.py",
    )
    for path in paths:
        source = path.read_text(encoding="utf-8")
        tree = ast.parse(source)
        assert len(source.splitlines()) < 250, path
        functions = [node for node in tree.body if isinstance(node, ast.FunctionDef)]
        assert {node.name for node in functions} <= {"main"}, path


def test_domain_modules_never_import_reporting_adapters():
    paths = [
        *sorted((ROOT / "autoresearch/scan/l3").glob("*.py")),
        *sorted((ROOT / "autoresearch/scan/l4").glob("*.py")),
        ROOT / "autoresearch/scan/decision_finalize.py",
    ]
    forbidden = {
        "autoresearch.scan.assemble",
        "autoresearch.scan.publisher",
        "autoresearch.scan.report_sections",
    }
    for path in paths:
        tree = ast.parse(path.read_text(encoding="utf-8"))
        imported = {
            node.module
            for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom) and node.module
        }
        imported |= {
            alias.name
            for node in ast.walk(tree)
            if isinstance(node, ast.Import)
            for alias in node.names
        }
        assert not (imported & forbidden), (path, imported & forbidden)


def test_workflows_do_not_own_rating_or_gate_functions():
    for rel in (".claude/workflows/scan-market.js", ".claude/workflows/l4-stock.js"):
        source = (ROOT / rel).read_text(encoding="utf-8")
        assert "function rubric_rating" not in source
        assert "function gate_status" not in source
