"""Freeze bounded scan efficiency replay recipes and compare observed decisions.

This offline entry never launches a model or changes a production run. Settings must
be frozen into a separate experimental dispatch contract before its C4 binding.
"""
from __future__ import annotations

import copy
import json
import math
import re
from pathlib import Path

from autoresearch.common import workspace as ws
from autoresearch.common.atomic import canonical_json, sha256_bytes
from autoresearch.research import noise_floor
from autoresearch.research.evidence_refs import read_json_ref, read_ref, read_request, write_derived

PROFILES = {
    "intel_high": ("scan.l4.intel", "effort", "xhigh", "high"),
    "intel_medium": ("scan.l4.intel", "effort", "xhigh", "medium"),
    "sector_medium": ("sector.brief", "effort", "high", "medium"),
    "catalog_1000": (None, "context_profile", None, 1000),
}
CONTROLS = {"role_instructions", "access_policy", "hooks", "downstream_contract"}
REQUEST_FIELDS = {"experiment_id", "profile", "repetitions", "min_tasks", "margin",
                  "max_role_invocations", "controls", "tasks"}
TASK_FIELDS = {"task_id", "role", "model", "effort", "skills_max_context_tokens", "inputs"}
USAGE_FIELDS = ("input_tokens", "cached_input_tokens", "output_tokens", "wall_seconds", "metering_complete")


def _integer(value, minimum, maximum, name):
    if type(value) is not int or not minimum <= value <= maximum:
        raise ValueError(f"invalid {name}: expected {minimum}..{maximum}")


def _argv(overrides):
    return [item for key, value in overrides.items()
            for item in ("-c", f"{key}={json.dumps(value)}")]


def prepare(request: dict) -> dict:
    """Hash-verify source snapshots; emit exactly one treatment factor per job."""
    if ws.ENGINE != "codex":
        raise ValueError("Codex experiment engine required")
    if not isinstance(request, dict) or set(request) != REQUEST_FIELDS:
        raise ValueError("invalid experiment request fields")
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,80}", str(request["experiment_id"])):
        raise ValueError("invalid experiment identity")
    profile = request["profile"]
    if profile not in PROFILES:
        raise ValueError("unknown experimental profile")
    _integer(request["repetitions"], 2, 5, "repetitions")
    _integer(request["min_tasks"], 20, 100, "min_tasks")
    _integer(request["max_role_invocations"], 1, 1000, "role invocation budget")
    margin = request["margin"]
    if type(margin) not in {int, float} or not math.isfinite(margin) or not 0 <= margin <= .1:
        raise ValueError("invalid equivalence margin")
    controls = request["controls"]
    if not isinstance(controls, dict) or set(controls) != CONTROLS:
        raise ValueError("all four frozen access/role/downstream controls required")
    for ref in controls.values():
        read_ref(ref)
    tasks = request["tasks"]
    if not isinstance(tasks, list) or not request["min_tasks"] <= len(tasks) <= 100:
        raise ValueError("bounded task cohort below min_tasks or above 100")
    invocations = len(tasks) * request["repetitions"] * 2
    if invocations > request["max_role_invocations"]:
        raise ValueError("role invocation budget exceeded")
    expected_role, factor, expected_effort, treatment = PROFILES[profile]
    jobs, seen, input_cases = [], set(), set()
    for task in tasks:
        if not isinstance(task, dict) or set(task) != TASK_FIELDS:
            raise ValueError("invalid frozen task fields")
        task_id = task["task_id"]
        if not isinstance(task_id, str) or not re.fullmatch(r"[A-Za-z0-9_.-]{1,100}", task_id) or task_id in seen:
            raise ValueError("invalid or duplicate task identity")
        seen.add(task_id)
        if task["role"] not in {"scan.l4.intel", "sector.brief"} or expected_role not in {None, task["role"]}:
            raise ValueError("profile cannot change this role")
        if task["model"] != "gpt-5.6-sol":
            raise ValueError("profile must preserve the observed production model")
        if task["effort"] != (expected_effort or {"scan.l4.intel": "xhigh", "sector.brief": "high"}[task["role"]]):
            raise ValueError("baseline effort does not match this profile")
        _integer(task["skills_max_context_tokens"], 1, 10000, "skills catalog baseline")
        if factor == "context_profile" and task["skills_max_context_tokens"] <= treatment:
            raise ValueError("catalog candidate must reduce the observed baseline budget")
        if not isinstance(task["inputs"], dict) or not task["inputs"]:
            raise ValueError("frozen task inputs required")
        for ref in task["inputs"].values():
            read_ref(ref)
        # Caller-assigned task IDs, artifact labels or copied paths must not
        # turn repeated identical material into independent quality cases.
        input_case = tuple(sorted({ref["sha256"] for ref in task["inputs"].values()}))
        if input_case in input_cases:
            raise ValueError("duplicate independent input case")
        input_cases.add(input_case)
        baseline = {"model": task["model"], "model_reasoning_effort": task["effort"],
                    "skills.max_context_tokens": task["skills_max_context_tokens"]}
        candidate = {**baseline, ("model_reasoning_effort" if factor == "effort"
                                  else "skills.max_context_tokens"): treatment}
        for arm, overrides in (("baseline", baseline), ("candidate", candidate)):
            for repetition in range(1, request["repetitions"] + 1):
                jobs.append({"job_id": f"{task_id}.{arm}.{repetition}", "task_id": task_id,
                    "role": task["role"], "arm": arm, "repetition": repetition,
                    "inputs": copy.deepcopy(task["inputs"]), "codex_overrides": overrides.copy(),
                    "argv_overrides": _argv(overrides),
                    "apply_on": ["open", "work"],
                    "freeze_before_binding": True})
    return {"schema_version": 1, "engine": "codex", "request": copy.deepcopy(request),
        "factor": factor, "controls": copy.deepcopy(controls), "jobs": jobs,
        "role_invocations": invocations, "opening_plus_work_calls": 2 * invocations,
        "downstream_calls": "SEPARATELY_BOUNDED_PRODUCTION_CONTINUATION_REQUIRED",
        "runtime_verification": "UNVERIFIED_UNTIL_REAL_HOST_EVIDENCE",
        "production_adoption": "NOT_AUTHORIZED"}


def manifest_digest(manifest):
    return sha256_bytes((canonical_json(manifest) + "\n").encode())


def freeze(request, output: Path):
    return write_derived(Path(output), prepare(request))


def probe_catalog(manifest, *, codex_bin="codex", cwd=None):
    """Zero-inference CLI prompt rendering; capture hashes/counts, not prompt text.

    The debug renderer is a local CLI capability, and may be unavailable or blocked.
    Its output establishes a context-size observation, never C4 hook enforcement.
    """
    import subprocess

    if manifest != prepare(manifest["request"]):
        raise ValueError("frozen manifest changed")
    if manifest["request"]["profile"] != "catalog_1000":
        raise ValueError("catalog profile required for offline prompt probe")
    rows = []
    seen = set()
    instructions = read_ref(manifest["controls"]["role_instructions"]).decode("utf-8")
    for job in manifest["jobs"]:
        settings = job["codex_overrides"]
        key = canonical_json(settings)
        if key in seen:
            continue
        seen.add(key)
        command = [str(codex_bin), "-C", str(Path(cwd or ".").resolve()),
            "-m", settings["model"], "debug", "prompt-input", *job["argv_overrides"],
            "-c", f"developer_instructions={json.dumps(instructions)}", "OK"]
        row = {"arm": job["arm"], "settings": settings, "status": "UNVERIFIED",
               "model_visible_chars": None, "prompt_sha256": None, "exit_code": None}
        try:
            result = subprocess.run(command, capture_output=True, text=True, timeout=25)
            row["exit_code"] = result.returncode
            if result.returncode != 0:
                row["status"] = "BLOCKED"
                row["diagnostic_sha256"] = sha256_bytes(result.stderr.encode())
            else:
                prompt = json.loads(result.stdout)
                if not isinstance(prompt, (dict, list)):
                    raise ValueError("prompt renderer did not return an object or list")
                row.update(status="OBSERVED", model_visible_chars=len(canonical_json(prompt)),
                           prompt_sha256=sha256_bytes(result.stdout.encode()))
        except (OSError, subprocess.TimeoutExpired, ValueError) as exc:
            row.update(status="UNAVAILABLE", error_class=type(exc).__name__)
        rows.append(row)
    return {"schema_version": 1, "manifest_sha256": manifest_digest(manifest),
        "probe": "CODEX_DEBUG_PROMPT_INPUT", "model_calls": 0, "observations": rows,
        "character_count_is_tokens": False, "runtime_c4_verification": "UNVERIFIED",
        "production_adoption": "NOT_AUTHORIZED"}


def readout(manifest: dict, observations: list[dict]) -> dict:
    """Compare the entire frozen cohort, rejecting unknown decisions or usage.

    Host evidence is hash-bound, but its claims still require independent transcript,
    access-boundary and grader review. This readout never authorizes deployment.
    """
    if manifest != prepare(manifest["request"]):
        raise ValueError("frozen manifest changed")
    digest = manifest_digest(manifest)
    jobs = {job["job_id"]: job for job in manifest["jobs"]}
    if (not isinstance(observations, list) or len(observations) != len(jobs)
            or {row.get("job_id") for row in observations} != set(jobs)):
        raise ValueError("complete, unique frozen observation cohort required")
    bits = {arm: {} for arm in ("baseline", "candidate")}
    totals = {arm: dict.fromkeys(("input_tokens", "cached_input_tokens", "output_tokens", "wall_seconds"), 0)
              for arm in bits}
    invocation_ids, transcript_paths, transcript_hashes, card_paths = (set() for _ in range(4))
    for row in observations:
        job = jobs[row["job_id"]]
        proof = read_json_ref(row["host_evidence_ref"])
        if (proof.get("job_id") != job["job_id"] or proof.get("manifest_sha256") != digest
                or proof.get("observed_overrides") != job["codex_overrides"]
                or proof.get("real_session") is not True
                or proof.get("access_verdict") != "PASS" or proof.get("domain_verdict") != "PASS"
                or any(type(proof.get(key)) is not int or proof[key] != 0
                       for key in ("critical_errors", "access_faults", "publication_faults"))):
            raise ValueError("host evidence does not verify this frozen job and quality controls")
        usage = {key: row.get(key) for key in USAGE_FIELDS}
        if (proof.get("decision_card_ref") != row.get("decision_card_ref")
                or canonical_json(proof.get("usage")) != canonical_json(usage)):
            raise ValueError("host evidence card/metering binding mismatch")
        session, thread = proof.get("session_id"), proof.get("thread_id")
        if (any(not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9_.:-]{1,200}", value)
                for value in (session, thread)) or session in invocation_ids or thread in invocation_ids):
            raise ValueError("independent session/thread identities required for each job")
        transcript = proof.get("transcript_ref")
        read_ref(transcript)
        transcript_path = str(Path(transcript["path"]).resolve())
        if transcript_path in transcript_paths or transcript["sha256"] in transcript_hashes:
            raise ValueError("independent transcript source required for each job")
        invocation_ids.update((session, thread))
        transcript_paths.add(transcript_path)
        transcript_hashes.add(transcript["sha256"])
        if row.get("metering_complete") is not True:
            raise ValueError("complete metering required")
        for key in totals[job["arm"]]:
            value = row.get(key)
            if type(value) not in {int, float} or not math.isfinite(value) or value < 0:
                raise ValueError(f"unknown or invalid metering: {key}")
            if key != "wall_seconds" and type(value) is not int:
                raise ValueError(f"invalid token metering: {key}")
            totals[job["arm"]][key] += value
        if row["cached_input_tokens"] > row["input_tokens"]:
            raise ValueError("cached input exceeds total input metering")
        card = read_ref(row["decision_card_ref"]).decode("utf-8")
        card_path = str(Path(row["decision_card_ref"]["path"]).resolve())
        if card_path in card_paths:
            raise ValueError("independent downstream decision card source required for each job")
        card_paths.add(card_path)
        decision = noise_floor.decision_bits(card)
        if decision["rating"] is None or decision["entry"] == "UNKNOWN" or decision["veto"] is None:
            raise ValueError("unknown decision cannot establish equivalence")
        bits[job["arm"]].setdefault(job["task_id"], []).append(decision)
    floor = noise_floor.self_agreement(bits["baseline"])
    cross = noise_floor.cross_agreement(bits["baseline"], bits["candidate"])
    request = manifest["request"]
    equivalent = noise_floor.equivalence(floor, cross, margin=request["margin"], min_tasks=request["min_tasks"])
    # Early stopping is also a frozen decision, even though the legacy helper's
    # KEY_BITS only includes rating, veto and entry.
    early_ok = cross["early_exact"] >= floor["early_exact"] - request["margin"]
    if not early_ok and equivalent["verdict"] == "EQUIVALENT":
        equivalent["verdict"] = "NOT_EQUIVALENT"
    base = totals["baseline"]["input_tokens"] + totals["baseline"]["output_tokens"]
    candidate = totals["candidate"]["input_tokens"] + totals["candidate"]["output_tokens"]
    return {"schema_version": 1, "manifest_sha256": digest, "observed_jobs": len(observations),
        "floor": floor, "candidate_self": noise_floor.self_agreement(bits["candidate"]),
        "cross": cross, "equivalence": equivalent, "early_stop_equivalent": early_ok,
        "usage": totals, "token_savings_fraction": (base - candidate) / base if base else None,
        "token_basis": "INPUT_INCLUDES_CACHED_OUTPUT_INCLUDES_REASONING",
        "wall_basis": "SUM_OF_OBSERVED_JOB_DURATIONS_NOT_SCAN_WALL_TIME",
        "quota_savings": None, "production_adoption": "NOT_AUTHORIZED",
        "evidence_assurance": "HOST_AND_GRADER_PROVENANCE_REVIEW_REQUIRED"}


def main(argv=None):
    import argparse

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("freeze", "probe", "readout"))
    parser.add_argument("--request", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--codex-bin", default="codex", help="Offline probe executable only")
    args = parser.parse_args(argv)
    request = read_request(args.request)
    if args.action == "freeze":
        result = freeze(request, Path(args.output))
    elif args.action == "probe":
        result = write_derived(Path(args.output), probe_catalog(read_json_ref(request["manifest_ref"]),
                                                               codex_bin=args.codex_bin))
    else:
        manifest = read_json_ref(request["manifest_ref"])
        rows = read_json_ref(request["observations_ref"])["observations"]
        result = write_derived(Path(args.output), readout(manifest, rows))
    print(json.dumps(result))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
