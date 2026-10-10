import copy
import importlib
import json

import pytest

from autoresearch.common.atomic import sha256_bytes


def module():
    return importlib.import_module("autoresearch.research.scan_efficiency_experiment")


@pytest.fixture
def source(tmp_path, monkeypatch):
    from autoresearch.common import workspace as ws

    monkeypatch.setattr(ws, "ENGINE", "codex")
    monkeypatch.setattr(ws, "context_root", lambda: tmp_path / "context_codex")
    monkeypatch.setattr(ws, "reports_root", lambda: tmp_path / "reports_codex")
    root = ws.context_root()
    root.mkdir()

    def ref(name, value):
        path = root / name
        path.write_text(value if isinstance(value, str) else json.dumps(value))
        return {"path": str(path), "sha256": sha256_bytes(path.read_bytes())}

    controls = {key: ref(key, key) for key in
                ("role_instructions", "access_policy", "hooks", "downstream_contract")}
    return ref, {"experiment_id": "intel-high-001", "profile": "intel_high",
        "repetitions": 2, "min_tasks": 20, "margin": 0.0, "max_role_invocations": 80,
        "controls": controls, "tasks": [{"task_id": f"t{i}", "role": "scan.l4.intel",
            "model": "gpt-5.6-sol", "effort": "xhigh", "skills_max_context_tokens": 10000,
            "inputs": {"task": ref(f"input{i}.json", {"case": i})}} for i in range(20)]}


def observations(manifest, ref):
    result = []
    card = "**Rating**: Hold\n**入场**: 禁止\nFINAL TRANSACTION PROPOSAL: **HOLD**\n"
    for job in manifest["jobs"]:
        name = job["job_id"]
        decision_ref = ref(f"card-{name}.md", card)
        usage = {"input_tokens": 100 if job["arm"] == "baseline" else 80,
            "cached_input_tokens": 70 if job["arm"] == "baseline" else 60,
            "output_tokens": 10, "wall_seconds": 1.0, "metering_complete": True}
        proof = ref(f"proof-{name}.json", {"job_id": name, "manifest_sha256":
            module().manifest_digest(manifest), "observed_overrides": job["codex_overrides"],
            "real_session": True, "access_verdict": "PASS", "domain_verdict": "PASS",
            "critical_errors": 0, "access_faults": 0, "publication_faults": 0,
            "decision_card_ref": decision_ref, "usage": usage,
            "session_id": f"session-{name}", "thread_id": f"thread-{name}",
            "transcript_ref": ref(f"transcript-{name}.jsonl", {"session_id": name})})
        result.append({"job_id": name, "host_evidence_ref": proof,
            "decision_card_ref": decision_ref, **usage})
    return result


def test_manifest_freezes_one_factor_and_preserves_access_controls(source):
    _, request = source
    manifest = module().prepare(request)
    assert len(manifest["jobs"]) == 80
    assert manifest["opening_plus_work_calls"] == 160
    assert manifest["controls"] == request["controls"]
    baseline, candidate = manifest["jobs"][0], manifest["jobs"][2]
    assert baseline["codex_overrides"]["model_reasoning_effort"] == "xhigh"
    assert candidate["codex_overrides"]["model_reasoning_effort"] == "high"
    assert baseline["inputs"] == candidate["inputs"]
    assert 'model_reasoning_effort="high"' in candidate["argv_overrides"]
    assert manifest["production_adoption"] == "NOT_AUTHORIZED"


@pytest.mark.parametrize("profile,role,effort,expected", [
    ("intel_medium", "scan.l4.intel", "xhigh", "medium"),
    ("sector_medium", "sector.brief", "high", "medium"),
    ("catalog_1000", "scan.l4.intel", "xhigh", "xhigh"),
])
def test_opt_in_profiles(profile, role, effort, expected, source):
    _, request = source
    request["profile"] = profile
    for task in request["tasks"]:
        task.update(role=role, effort=effort)
    manifest = module().prepare(request)
    job = manifest["jobs"][2]
    assert job["codex_overrides"]["model_reasoning_effort"] == expected
    if profile == "catalog_1000":
        assert job["codex_overrides"]["skills.max_context_tokens"] == 1000
        assert 'skills.max_context_tokens=1000' in job["argv_overrides"]


@pytest.mark.parametrize("change,match", [
    ({"repetitions": 1}, "repetitions"), ({"max_role_invocations": 79}, "budget"),
    ({"profile": "production"}, "profile"), ({"margin": -0.1}, "margin"),
    ({"min_tasks": 1}, "min_tasks"),
])
def test_invalid_protocol_rejected(change, match, source):
    _, request = source
    request.update(change)
    with pytest.raises(ValueError, match=match):
        module().prepare(request)


def test_final_card_cannot_be_downgraded_and_frozen_sources_cannot_drift(source):
    _, request = source
    request["tasks"][0]["role"] = "scan.l4.card"
    with pytest.raises(ValueError, match="role"):
        module().prepare(request)
    request["tasks"][0]["role"] = "scan.l4.intel"
    request["controls"]["hooks"]["sha256"] = "a" * 64
    with pytest.raises(ValueError, match="hash mismatch"):
        module().prepare(request)


def test_readout_requires_complete_cohort_and_real_observed_treatment(source):
    ref, request = source
    manifest = module().prepare(request)
    rows = observations(manifest, ref)
    result = module().readout(manifest, rows)
    assert result["equivalence"]["verdict"] == "EQUIVALENT"
    assert result["token_savings_fraction"] == pytest.approx(20 / 110)
    assert result["production_adoption"] == "NOT_AUTHORIZED"
    assert result["evidence_assurance"] == "HOST_AND_GRADER_PROVENANCE_REVIEW_REQUIRED"
    with pytest.raises(ValueError, match="cohort"):
        module().readout(manifest, rows[:-1])
    bad = copy.deepcopy(rows)
    bad[0]["host_evidence_ref"] = ref("bad-proof.json", {"job_id": "wrong"})
    with pytest.raises(ValueError, match="host evidence"):
        module().readout(manifest, bad)


def test_unknown_decisions_and_incomplete_metering_never_pass(source):
    ref, request = source
    manifest = module().prepare(request)
    rows = observations(manifest, ref)
    rows[0]["decision_card_ref"] = ref("unknown.md", "unparseable")
    rebind_observation(rows[0], ref, "unknown-proof")
    with pytest.raises(ValueError, match="unknown decision"):
        module().readout(manifest, rows)
    rows = observations(module().prepare(request), lambda n, v: ref("again-" + n, v))
    rows[0]["metering_complete"] = False
    with pytest.raises(ValueError, match="metering"):
        module().readout(manifest, rows)


def test_exclusive_freeze_validates_engine_output_scope(source, tmp_path):
    _, request = source
    with pytest.raises(ValueError, match="scope"):
        module().freeze(request, tmp_path / "outside.json")
    path = tmp_path / "context_codex" / "experiment.json"
    result = module().freeze(request, path)
    assert result["sha256"] == sha256_bytes(path.read_bytes())
    with pytest.raises(FileExistsError):
        module().freeze(request, path)


@pytest.mark.parametrize("change", [
    {"real_session": False}, {"critical_errors": 1}, {"access_faults": 1},
    {"domain_verdict": "UNKNOWN"}, {"observed_overrides": {}},
])
def test_bad_quality_or_unobserved_profile_cannot_pass(change, source):
    ref, request = source
    manifest = module().prepare(request)
    rows = observations(manifest, ref)
    from autoresearch.research.evidence_refs import read_json_ref

    proof = read_json_ref(rows[0]["host_evidence_ref"])
    rows[0]["host_evidence_ref"] = ref("bad-proof.json", {**proof, **change})
    with pytest.raises(ValueError, match="host evidence"):
        module().readout(manifest, rows)


def test_manifest_mutation_and_early_stop_drift_rejected(source):
    ref, request = source
    manifest = module().prepare(request)
    rows = observations(manifest, ref)
    changed = copy.deepcopy(manifest)
    changed["jobs"][0]["codex_overrides"]["model"] = "gpt-5.6-luna"
    with pytest.raises(ValueError, match="manifest changed"):
        module().readout(changed, rows)
    for row in rows:
        if ".candidate." in row["job_id"]:
            row["decision_card_ref"] = ref("early-" + row["job_id"],
                "**早停**: 停于 P3 ｜ 停因:估值透支\n**Rating**: Hold\n**入场**: 禁止\n"
                "FINAL TRANSACTION PROPOSAL: **HOLD**\n")
            rebind_observation(row, ref, "early-proof-" + row["job_id"])
    got = module().readout(manifest, rows)
    assert got["early_stop_equivalent"] is False
    assert got["equivalence"]["verdict"] == "NOT_EQUIVALENT"


def test_cli_freeze_and_readout_only_create_derived_files(source, tmp_path, capsys):
    ref, request = source
    request_path = tmp_path / "request.json"
    request_path.write_text(json.dumps(request))
    frozen = tmp_path / "context_codex" / "manifest.json"
    assert module().main(["freeze", "--request", str(request_path), "--output", str(frozen)]) == 0
    manifest_ref = json.loads(capsys.readouterr().out)
    manifest = json.loads(frozen.read_text())
    rows_ref = ref("observations.json", {"observations": observations(manifest, ref)})
    readout_path = tmp_path / "readout-request.json"
    readout_path.write_text(json.dumps({"manifest_ref": manifest_ref, "observations_ref": rows_ref}))
    output = tmp_path / "context_codex" / "readout.json"
    assert module().main(["readout", "--request", str(readout_path), "--output", str(output)]) == 0
    assert json.loads(output.read_text())["observed_jobs"] == 80


def test_offline_catalog_probe_uses_local_renderer_and_never_executes_inference(source, monkeypatch):
    import subprocess

    _, request = source
    request["profile"] = "catalog_1000"
    commands = []

    def render(command, **kwargs):
        commands.append(command)
        assert command[command.index("debug") + 1] == "prompt-input"
        assert "exec" not in command
        assert "--ignore-user-config" not in command
        assert "--dangerously-bypass-hook-trust" not in command
        return subprocess.CompletedProcess(command, 0, json.dumps([{"text": "catalog"}]), "")

    monkeypatch.setattr(subprocess, "run", render)
    result = module().probe_catalog(module().prepare(request))
    assert len(commands) == 2
    assert result["model_calls"] == 0
    assert all(row["status"] == "OBSERVED" for row in result["observations"])
    assert result["character_count_is_tokens"] is False
    assert result["runtime_c4_verification"] == "UNVERIFIED"
    assert "catalog" not in json.dumps(result)


def test_offline_probe_sandbox_failure_stays_unverified(source, monkeypatch):
    import subprocess

    _, request = source
    request["profile"] = "catalog_1000"
    monkeypatch.setattr(subprocess, "run", lambda command, **kwargs:
        subprocess.CompletedProcess(command, 1, "", "Operation not permitted"))
    result = module().probe_catalog(module().prepare(request))
    assert all(row["status"] == "BLOCKED" and row["model_visible_chars"] is None
               for row in result["observations"])


def rebind_observation(row, ref, name):
    from autoresearch.research.evidence_refs import read_json_ref

    proof = read_json_ref(row["host_evidence_ref"])
    proof.update(decision_card_ref=row["decision_card_ref"], usage={key: row[key] for key in
        ("input_tokens", "cached_input_tokens", "output_tokens", "wall_seconds", "metering_complete")})
    row["host_evidence_ref"] = ref(name + ".json", proof)


@pytest.mark.parametrize("tamper", ["card", "usage", "wall", "cached"])
def test_valid_host_proof_cannot_be_paired_with_other_cards_or_cheaper_usage(tamper, source):
    ref, request = source
    manifest = module().prepare(request)
    rows = observations(manifest, ref)
    if tamper == "card":
        rows[2]["decision_card_ref"] = rows[0]["decision_card_ref"]
    elif tamper == "wall":
        rows[2]["wall_seconds"] = .1
    elif tamper == "cached":
        rows[2]["cached_input_tokens"] = 80
    else:
        rows[2].update(input_tokens=1, cached_input_tokens=0, output_tokens=1)
    with pytest.raises(ValueError, match="binding"):
        module().readout(manifest, rows)


@pytest.mark.parametrize("field", ["session_id", "thread_id", "transcript_ref"])
def test_independent_repeats_cannot_reuse_one_session_thread_or_transcript(field, source):
    ref, request = source
    manifest = module().prepare(request)
    rows = observations(manifest, ref)
    from autoresearch.research.evidence_refs import read_json_ref

    first = read_json_ref(rows[0]["host_evidence_ref"])
    duplicate = read_json_ref(rows[1]["host_evidence_ref"])
    duplicate[field] = first[field]
    rows[1]["host_evidence_ref"] = ref("repeated-proof.json", duplicate)
    with pytest.raises(ValueError, match="independent"):
        module().readout(manifest, rows)


def test_distinct_file_paths_or_labels_cannot_manufacture_independent_input_cases(source):
    ref, request = source
    for i, task in enumerate(request["tasks"]):
        task["inputs"] = {f"renamed-{i}": ref(f"alias-{i}.json", {"one-case": True})}
    with pytest.raises(ValueError, match="independent input"):
        module().prepare(request)


def test_session_and_thread_aliases_cannot_relabel_one_invocation(source):
    ref, request = source
    manifest = module().prepare(request)
    rows = observations(manifest, ref)
    from autoresearch.research.evidence_refs import read_json_ref

    first = read_json_ref(rows[0]["host_evidence_ref"])
    alias = read_json_ref(rows[1]["host_evidence_ref"])
    alias["thread_id"] = first["session_id"]
    rows[1]["host_evidence_ref"] = ref("aliased-proof.json", alias)
    with pytest.raises(ValueError, match="independent"):
        module().readout(manifest, rows)


def test_copied_transcript_paths_are_still_one_source(source):
    ref, request = source
    manifest = module().prepare(request)
    rows = observations(manifest, ref)
    from autoresearch.research.evidence_refs import read_json_ref, read_ref

    first = read_json_ref(rows[0]["host_evidence_ref"])
    alias = read_json_ref(rows[1]["host_evidence_ref"])
    alias["transcript_ref"] = ref("copied-transcript.jsonl", read_ref(first["transcript_ref"]).decode())
    rows[1]["host_evidence_ref"] = ref("copied-proof.json", alias)
    with pytest.raises(ValueError, match="independent transcript"):
        module().readout(manifest, rows)


def test_rebound_card_proof_cannot_reuse_same_source_for_independent_jobs(source):
    ref, request = source
    manifest = module().prepare(request)
    rows = observations(manifest, ref)
    rows[1]["decision_card_ref"] = rows[0]["decision_card_ref"]
    rebind_observation(rows[1], ref, "rebound-card-proof")
    with pytest.raises(ValueError, match="independent downstream"):
        module().readout(manifest, rows)
