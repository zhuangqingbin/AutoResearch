#!/usr/bin/env python3
"""Executable identity checks for preregistered offline research runs."""
from __future__ import annotations

import re
import subprocess
from datetime import datetime
from pathlib import Path

from autoresearch.common import workspace as ws
from autoresearch.common.atomic import canonical_json, sha256_bytes, sha256_file

MANIFEST_SCHEMA_VERSION = 1
_MATURITY = re.compile(
    r"(?:scan_days\s*>=\s*([1-9][0-9]*)|"
    r"common\.stats\.maturity_verdict\(scan_days\s*>=\s*([1-9][0-9]*)\))"
)


def _date(value) -> str:
    text = str(value).strip().replace("-", "")
    try:
        datetime.strptime(text, "%Y%m%d")
    except ValueError as exc:
        raise ValueError(f"invalid registered date: {value!r}") from exc
    return text


def parse_maturity_policy(text: str) -> int:
    """Return the registered minimum scan days for the two supported grammars."""
    match = _MATURITY.fullmatch(str(text).strip())
    if not match:
        raise ValueError(f"unsupported maturity policy: {text!r}")
    return int(match.group(1) or match.group(2))


def registered_test_range(spec: dict) -> tuple[str, str]:
    """Return the normalized half-open test interval ``[start, end)``."""
    try:
        start, end = spec["split"]["test"]
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError("registered test interval required") from exc
    start, end = _date(start), _date(end)
    if start >= end:
        raise ValueError("registered test interval must be half-open and ordered")
    return start, end


def registered_date_slice(spec: dict) -> dict:
    start, end = registered_test_range(spec)
    return {"test_start": start, "test_end": end, "semantics": "[start,end)"}


def file_manifest(paths, date_slice: dict) -> dict:
    """Hash exact input files and bind their presence/absence to a date slice."""
    unique = sorted({str(Path(path).expanduser().resolve()) for path in paths})
    files = []
    for raw in unique:
        path = Path(raw)
        exists = path.is_file()
        files.append({
            "path": raw,
            "exists": exists,
            "size": path.stat().st_size if exists else None,
            "sha256": sha256_file(path) if exists else None,
        })
    return {
        "schema_version": MANIFEST_SCHEMA_VERSION,
        "date_slice": date_slice,
        "files": files,
    }


def manifest_digest(payload: dict) -> str:
    return sha256_bytes(canonical_json(payload).encode("utf-8"))


def verify_manifest(spec: dict, payload: dict) -> str:
    observed = manifest_digest(payload)
    declared = str(spec.get("input_manifest_hash") or "")
    if observed != declared:
        raise ValueError(
            f"input manifest mismatch: declared={declared!r}, observed={observed}"
        )
    return observed


def verify_engine(spec: dict) -> str:
    declared = str(spec.get("engine") or "")
    if declared != ws.ENGINE:
        raise ValueError(
            f"registered engine mismatch: declared={declared!r}, runtime={ws.ENGINE!r}"
        )
    return declared


def _git(args: list[str], root: Path, *, check: bool = True) -> subprocess.CompletedProcess:
    try:
        return subprocess.run(
            ["git", *args], cwd=root, check=check, capture_output=True, text=True
        )
    except (OSError, subprocess.CalledProcessError) as exc:
        raise ValueError(f"code provenance cannot be verified: git {' '.join(args)}") from exc


def verify_code_provenance(spec: dict, behavior_roots,
                           *, repo_root: Path | str = ".") -> dict:
    """Require a real commit and no committed or dirty behavior drift from it."""
    declared = str(spec.get("code_sha") or "")
    if not re.fullmatch(r"[0-9a-fA-F]{40}", declared):
        raise ValueError("code provenance requires a full 40-hex code_sha")
    root = Path(repo_root).resolve()
    _git(["cat-file", "-e", f"{declared}^{{commit}}"], root)
    observed = _git(["rev-parse", "HEAD"], root).stdout.strip()
    paths = sorted({str(Path(path)) for path in behavior_roots})
    changed = _git(["diff", "--name-only", declared, observed, "--", *paths], root).stdout.splitlines()
    if changed:
        raise ValueError(f"code provenance behavior drift: {sorted(changed)}")
    dirty = _git(
        ["status", "--porcelain", "--untracked-files=all", "--", *paths], root
    ).stdout.splitlines()
    if dirty:
        raise ValueError(f"code provenance dirty behavior paths: {sorted(dirty)}")
    return {"declared": declared.lower(), "observed": observed, "behavior_roots": paths}


def verify_modes(spec: dict, *, evidence_modes: set[str], cost_models: set[str]) -> None:
    if spec.get("evidence_mode") not in evidence_modes:
        raise ValueError(f"unsupported evidence mode: {spec.get('evidence_mode')!r}")
    if spec.get("cost_model_version") not in cost_models:
        raise ValueError(f"unsupported cost model: {spec.get('cost_model_version')!r}")


__all__ = [
    "file_manifest",
    "manifest_digest",
    "parse_maturity_policy",
    "registered_date_slice",
    "registered_test_range",
    "verify_code_provenance",
    "verify_engine",
    "verify_manifest",
    "verify_modes",
]
