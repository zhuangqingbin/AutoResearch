from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from autoresearch.session_agent.dispatch import render_prompt
from autoresearch.session_agent.executors.base import ROLE_DISPATCH, agent_type_for
from autoresearch.session_agent.roles import all_roles, get_role

from ._runner_support import profile


def _handle(tmp_path, engine="codex"):
    return SimpleNamespace(engine=engine, staging=tmp_path, analysis_date="2026-09-30",
                           contract=SimpleNamespace(user_config={}))


def test_all_inference_roles_have_engine_specific_physical_agents():
    for role in all_roles():
        if role == "scan.l5":  # implemented by deterministic assembly
            continue
        assert role in ROLE_DISPATCH
        assert ROLE_DISPATCH[role][1]
        for engine in ("claude", "codex"):
            assert agent_type_for(role, engine)
    assert agent_type_for("scan.l3.repair", "claude") == "l3-repair"
    for role in ("stock.market", "stock.pm", "macro.research", "sector.research"):
        assert get_role(role)["context_policy"] == "INDEPENDENT"
        assert ".claude/skills/" not in " ".join(get_role(role)["instruction_refs"])


def test_preflight_reports_host_and_executor_failure_before_dispatch(tmp_path):
    from autoresearch.session_agent.preflight import preflight_roles
    host = profile(independent_context=True, web_search=True, web_fetch=True)
    report = preflight_roles(_handle(tmp_path), ["stock.pm", "global.intel"], host)
    assert report["ok"]
    assert {row["role"] for row in report["roles"]} == {"stock.pm", "global.intel"}
    assert all(row["config_source"] and row["output_boundary"] for row in report["roles"])
    report = preflight_roles(_handle(tmp_path), ["global.intel"], profile())
    assert not report["ok"]
    assert "independent_context" in str(report) and "web_search" in str(report)
    report = preflight_roles(_handle(tmp_path), ["stock.pm"], host, executor="headless")
    assert not report["ok"] and "codex" in str(report)
    report = preflight_roles(_handle(tmp_path), ["stock.pm"], host, executor="mystery")
    assert not report["ok"] and "unknown executor" in str(report)



@pytest.mark.parametrize(("role", "definition", "declared_tools", "missing_tools"), [
    ("global.intel", "global-intel.md", "Write", ("WebSearch", "WebFetch")),
    ("global.intel", "global-intel.md", "Write, WebFetch", ("WebSearch",)),
    ("global.intel", "global-intel.md", "Write, WebSearch", ("WebFetch",)),
    ("stock.pm", "stock-full.md", "Write", ("Read",)),
    ("stock.pm", "stock-full.md", "Read", ("Write",)),
])
def test_claude_preflight_requires_physical_role_tools(
    tmp_path, monkeypatch, role, definition, declared_tools, missing_tools,
):
    from autoresearch.session_agent.preflight import preflight_roles

    original_read_text = Path.read_text
    def changed_definition(path, *args, **kwargs):
        text = original_read_text(path, *args, **kwargs)
        if path == Path(".claude/agents") / definition:
            text = "\n".join(f"tools: {declared_tools}" if line.startswith("tools:") else line
                             for line in text.split("\n"))
        return text
    monkeypatch.setattr(Path, "read_text", changed_definition)
    host = profile(engine="claude", independent_context=True, web_search=True, web_fetch=True)
    report = preflight_roles(_handle(tmp_path, "claude"), [role], host)
    assert not report["ok"]
    for tool in missing_tools:
        assert f"Claude role tools missing {tool}" in report["roles"][0]["reasons"]


@pytest.mark.parametrize("adapter_web", [False, None, "UNKNOWN"])
def test_preflight_rejects_executor_without_web_even_when_host_has_web(tmp_path, adapter_web):
    from autoresearch.session_agent.preflight import preflight_roles
    from autoresearch.session_agent.roles import EXECUTOR_CAPABILITIES

    adapter = SimpleNamespace(name="explicit", capabilities={**EXECUTOR_CAPABILITIES["mailbox"],
                                                             "web": adapter_web})
    host = profile(independent_context=True, web_search=True, web_fetch=True)
    report = preflight_roles(_handle(tmp_path), ["global.intel"], host, executor=adapter)
    assert not report["ok"]
    assert "executor web unavailable or unknown" in report["roles"][0]["reasons"]


def test_preflight_includes_dynamic_template_roles(tmp_path):
    from autoresearch.session_agent.preflight import preflight_plan
    plan = {"tasks": [], "task_templates": [{"allowed_roles": ["sector.intel"]}]}
    with pytest.raises(RuntimeError, match="sector.intel"):
        preflight_plan(_handle(tmp_path), plan, profile())


def test_sector_lite_reads_both_named_inputs_and_rejects_unknown(tmp_path):
    handle = _handle(tmp_path)
    task = {"role": "sector.brief", "task_id": "sector.x.brief", "subject": "电子"}
    args = {"outputs": {"sector.report": str(tmp_path / "out.md")}}
    text = render_prompt(handle, task, 1, inputs={"sector.reuse": "/reuse.json", "sector.pack": "/pack.json"}, **args)
    assert "/reuse.json" in text and "/pack.json" in text
    for bad in ({"sector.pack": "/pack.json"}, {"sector.pack": "/pack.json", "wrong": "/reuse.json"}):
        with pytest.raises(KeyError, match="sector"):
            render_prompt(handle, task, 1, inputs=bad, **args)


def test_scan_sector_brief_keeps_single_registered_pack(tmp_path):
    handle = _handle(tmp_path)
    task = {"role": "sector.brief", "task_id": "scan.sector.electronics", "subject": "电子"}
    text = render_prompt(handle, task, 1, inputs={"scan.sector.x.pack": "/pack.json"}, outputs={"out": "/out.md"})
    assert "读 /pack.json 写 /out.md" in text


def test_macro_global_intel_is_a_bound_input_not_an_unused_definition(tmp_path):
    from autoresearch.session_agent.workflows.macro import build_macro_plan

    from .test_macro import _context, _request

    plan = build_macro_plan(_request(), _context(tmp_path))
    tasks = {task["task_id"]: task for task in plan["tasks"]}
    intel = tasks["macro.global_intel"]
    assert intel["role"] == "global.intel" and intel["independent_context"]
    assert intel["input_artifact_ids"] == ["macro.intel.request"]
    assert tasks["macro.intel.prepare"]["operation"] == "macro.intel.prepare"
    for task in tasks.values():
        if task["role"] == "macro.research":
            assert "macro.intel" in task["input_artifact_ids"]
            assert task["independent_context"]
    assert "macro.data" in tasks["macro.full.validate"]["input_artifact_ids"]
    assert "macro.data" in tasks["macro.assemble"]["input_artifact_ids"]


def test_preflight_preserves_frozen_task_independence_instead_of_guessing(tmp_path):
    from autoresearch.session_agent.preflight import preflight_plan

    from ._runner_support import inf
    task = inf("old.full", role="stock.fundamentals", contract="stock.section.v1", independent=False)
    assert preflight_plan(_handle(tmp_path), {"tasks": [task]}, profile())["ok"]
    task["independent_context"] = True
    with pytest.raises(RuntimeError, match="independent_context"):
        preflight_plan(_handle(tmp_path), {"tasks": [task]}, profile())


def test_runner_rejects_unsupported_executor_before_any_deterministic_work(tmp_path, monkeypatch):
    from autoresearch.session_agent.runner import run_loop

    from ._runner_support import begin_synthetic_run, det, inf
    run = begin_synthetic_run(tmp_path, monkeypatch, [det("fetch"), inf("judge", deps=["fetch"])])
    class Unsupported:
        name = "unsupported"
        def dispatch(self, request):
            raise AssertionError("must not dispatch")
    with pytest.raises(RuntimeError, match="unknown executor"):
        run_loop(run.run_id, Unsupported(), hooks=run.hooks())
    assert run.op_calls == []


@pytest.mark.parametrize("cap", [8, 3])
def test_global_intel_request_is_frozen_neutral_and_consumed(tmp_path, cap):
    import json

    from autoresearch.common.execution_math import build_decision_frame
    from autoresearch.session_agent import artifacts
    from autoresearch.session_agent.domain_ops import macro_intel_prepare

    from .test_service import _handle as run_handle

    handle = run_handle(tmp_path)
    frame = build_decision_frame(analysis_session="2026-09-13", knowledge_cutoff="2026-09-13T12:00:00Z",
                                 venue="XSHG", research_depth="FULL", usage="macro", sessions=[], calendar_quality="UNKNOWN")
    for key, value in {"research.frame": frame, "macro.global_tape": {"direction": "BUY_ALL"},
                       "macro.intel.policy": {"schema_version": 1, "source_budget": {"max_queries": cap, "unit": "SEARCH_AND_FETCH"}}}.items():
        path = handle.staging / (key + ".json")
        path.write_text(json.dumps(value), encoding="utf-8")
        artifacts.register_artifact(handle, key, path, "READ")
    request_path = handle.staging / "request.json"
    artifacts.register_artifact(handle, "macro.intel.request", request_path, "WRITE")
    request = macro_intel_prepare(handle)
    assert request["knowledge_cutoff"] == frame["knowledge_cutoff"]
    assert request["calendar_status"] == "UNAVAILABLE"
    assert request["known_events"] == []
    assert request["source_budget"] == {"max_queries": cap, "unit": "SEARCH_AND_FETCH"}
    assert "BUY_ALL" not in request_path.read_text()
    artifacts.bind_artifact_hash(handle, "macro.intel.request")
    text = render_prompt(handle, {"role": "global.intel", "task_id": "macro.global_intel"}, 1,
                         inputs={"macro.intel.request": str(request_path)}, outputs={"macro.intel": "/out.md"})
    assert "knowledge_cutoff" in text and "source_budget" in text and "/out.md" in text
    assert "BUY_ALL" not in text


def test_company_intel_does_not_receive_upstream_context_text(tmp_path):
    for role in ("company.intel", "us.intel"):
        text = render_prompt(_handle(tmp_path), {"role": role, "task_id": "stock.intel", "subject": "600519.SS"}, 1,
                             inputs={"stock.context": "/secret-opinion.md"}, outputs={"stock.full.intel": "/out.md"})
        assert "600519.SS" in text and "/secret-opinion.md" not in text


@pytest.mark.parametrize(("role", "contract"), [("macro.research", "macro.allocation.v1"), ("scan.l3", "scan.l3.v1")])
def test_host_accepts_explicit_historical_or_section_contracts(role, contract):
    from autoresearch.session_agent.hosts.base import render_request

    from ._runner_support import inf

    task = inf("known.contract", role=role, contract=contract)
    assert render_request(task, profile(web_search=True, web_fetch=True))["expected_output_contract"] == contract


def test_preflight_rejects_unknown_output_contract_before_launch(tmp_path):
    from autoresearch.session_agent.preflight import preflight_plan

    from ._runner_support import inf

    task = inf("bad.contract", role="macro.research", contract="invented.output.v1")
    with pytest.raises(RuntimeError, match="output contract"):
        preflight_plan(_handle(tmp_path), {"tasks": [task]}, profile())
