"""Pure read-call evidence checks shared by live validation and frozen research.

The trusted source is the bound transcript archive, not an analyst's read claim.
No access to a live session, broker, external path, or current configuration occurs.
"""

from __future__ import annotations

import json
import shlex
from dataclasses import asdict
from pathlib import PurePosixPath

from autoresearch.common.atomic import canonical_json, sha256_bytes
from autoresearch.contracts.forensic import validate_host_evidence_binding
from autoresearch.trace.transcripts.base import (
    complete_host_read,
    hash_tool_response,
    tool_call_id,
)


def ordered_tool_pairs(normalized):
    requests, results = {}, {}
    for position, item in enumerate(normalized.get("items", [])):
        payload = item.get("payload", {})
        call_id = tool_call_id(payload)
        if not call_id:
            continue
        target = (
            requests
            if item.get("kind") == "tool_request"
            else results
            if item.get("kind") == "tool_result"
            else None
        )
        if target is not None:
            target.setdefault(call_id, []).append((position, payload))
    pairs = {}
    for key, calls in requests.items():
        if len(calls) != 1 or len(results.get(key, [])) != 1:
            continue
        (a, call), (b, result) = calls[0], results[key][0]
        if b > a and not result.get("is_error"):
            pairs[key] = (call, result)
    return pairs


def _broker_request(call, broker):
    args = call.get("input")
    if isinstance(args, str):
        args = json.loads(args)
    schemas = {
        "Bash": ("command", {"command", "description", "timeout"}),
        "exec_command": ("cmd", {"cmd", "workdir", "max_output_tokens", "yield_time_ms"}),
        "functions.exec_command": ("cmd", {"cmd", "workdir", "max_output_tokens", "yield_time_ms"}),
    }
    field, allowed = schemas[call["tool_name"]]
    if not isinstance(args, dict) or not set(args) <= allowed:
        raise ValueError("noncanonical broker tool")
    command = args[field]
    prefix = f"{shlex.quote(broker['python'])} -I -S {shlex.quote(broker['script'])} '"
    if not isinstance(command, str) or not command.startswith(prefix) or not command.endswith("'"):
        raise ValueError("noncanonical broker command")
    token = command[len(prefix) : -1]
    request = json.loads(token)
    if (
        set(request) != {"engine", "session_id", "agent_id", "operation", "artifact_id"}
        or request["operation"] != "read"
        or any(not isinstance(v, str) for v in request.values())
        or canonical_json(request).replace("'", "\\u0027") != token
    ):
        raise ValueError("noncanonical broker read")
    return request


def observe_read(
    *,
    normalized,
    binding,
    identity,
    artifact_id,
    artifact_path,
    data,
    manifest=None,
    manifest_sha256=None,
):
    if any(binding.get(key) != value for key, value in identity.items()):
        raise ValueError("read transcript identity mismatch")
    if data.startswith(b"DEEP_EVIDENCE_UNAVAILABLE"):
        return None
    response = asdict(hash_tool_response(data.decode("utf-8")))
    pairs = ordered_tool_pairs(normalized)

    def proof(call_id, source=None):
        value = {
            "artifact_id": artifact_id,
            "binding_id": binding["binding_id"],
            "call_id": call_id,
            "status": "READ_SUCCEEDED",
            "artifact_sha256": sha256_bytes(data),
        }
        if source:
            value["source"] = source
        return value

    for operation in normalized.get("operations", []):
        pair = pairs.get(operation.get("call_id"))
        if not (
            pair
            and pair[0].get("tool_name") in {"Read", "read_file"}
            and operation.get("kind") == "READ_SUCCEEDED"
            and operation.get("path_source") == "tool_input"
            and operation.get("path") == artifact_path
        ):
            continue
        # Raw outputs (Codex read_file) equal the bytes; Claude's text is
        # line-numbered, so only its host-written whole-file record counts.
        raw_output = (
            asdict(hash_tool_response(pair[1].get("content"))) == response
            and operation.get("response") == response
        )
        if raw_output or complete_host_read(
            pair[1].get("host_read"), path=artifact_path,
            sha256=sha256_bytes(data), byte_count=len(data),
        ):
            return proof(operation["call_id"])
    if manifest is None:
        return None
    owner = manifest["identity"]
    if any(
        owner.get(key) != identity[key]
        for key in ("engine", "run_id", "task_id", "attempt", "role")
    ):
        return None
    declared = [
        r
        for r in manifest["reads"] + manifest["conditional_reads"]
        if r["artifact_id"] == artifact_id
    ]
    if len(declared) != 1 or declared[0]["sha256"] != sha256_bytes(data):
        return None
    for call_id, (call, result) in pairs.items():
        try:
            request = _broker_request(call, manifest["broker"])
            if (
                request["artifact_id"] != artifact_id
                or request["engine"] != identity["engine"]
                or request["session_id"] != binding["session_ref"]
            ):
                continue
            child = request["agent_id"]
            if child:
                if child != binding["context_ref"] or request["session_id"] != owner["session_id"]:
                    continue
            elif (
                binding["context_ref"] != binding["session_ref"]
                or binding["parent_context_ref"] != owner["session_id"]
            ):
                continue
            observed = result.get("content")
            if isinstance(observed, str):
                observed = json.loads(observed)
            if (
                not isinstance(observed, dict)
                or type(observed.get("exit_code")) is not int
                or observed["exit_code"] != 0
                or not isinstance(observed.get("output"), str)
                or any(observed.get(k) for k in ("truncated", "is_truncated", "output_truncated"))
            ):
                continue
            observed = json.loads(observed["output"])
            if (
                not isinstance(observed, dict)
                or observed.get("ok") is not True
                or observed.get("schema_version") != 1
                or observed.get("kind") != "TASK_FILE_READ"
                or observed.get("identity") != owner
                or observed.get("artifact_id") != artifact_id
                or observed.get("manifest_sha256") != manifest_sha256
                or observed.get("content") != data.decode("utf-8")
                or observed.get("byte_length") != len(data)
                or observed.get("sha256") != sha256_bytes(data)
            ):
                continue
            return proof(call_id, "TASK_FILE_BROKER")
        except (ValueError, TypeError, KeyError):
            continue
    return None


def verify_read_bundle(value, *, read_bytes):
    """Verify portable files inside an already trusted frozen run/source boundary."""
    if value.get("schema_version") != 1:
        raise ValueError("unsupported read proof")
    binding = validate_host_evidence_binding(value["binding"])

    def read(name):
        ref = value[name]
        relative = PurePosixPath(ref["path"])
        if relative.is_absolute() or ".." in relative.parts:
            raise ValueError("proof reference escapes frozen root")
        data = read_bytes(ref["path"])
        if sha256_bytes(data) != ref["sha256"]:
            raise ValueError("read proof file hash mismatch")
        return data

    raw, normalized, data = read("raw"), read("normalized"), read("artifact")
    if (
        sha256_bytes(raw) != binding["archive_sha256"]
        or sha256_bytes(normalized) != binding["normalized_sha256"]
    ):
        raise ValueError("read proof binding content mismatch")
    manifest_bytes = read("dispatch_manifest") if value.get("dispatch_manifest") else None
    manifest = json.loads(manifest_bytes) if manifest_bytes is not None else None
    if manifest is not None:
        request = read("dispatch_request")
        if sha256_bytes(request) != manifest["request_sha256"]:
            raise ValueError("read proof dispatch hash mismatch")
        dispatch = json.loads(request)
        identity = {
            k: dispatch[k] for k in ("run_id", "engine", "task_id", "attempt", "role", "agent_type")
        }
        identity["session_id"] = dispatch["host_session_ref"]
        if identity != manifest["identity"]:
            raise ValueError("read proof dispatch identity mismatch")
    result = observe_read(
        normalized=json.loads(normalized),
        binding=binding,
        identity=value["identity"],
        artifact_id=value["artifact_id"],
        artifact_path=value["artifact_path"],
        data=data,
        manifest=manifest,
        manifest_sha256=sha256_bytes(manifest_bytes) if manifest_bytes is not None else None,
    )
    # binding.tool_call_ids indexes external-search lineage, not every file read.
    # The exact read request/result is bound by normalized_sha256 above.
    if result is None:
        raise ValueError("successful complete bound read not observed")
    return result
