"""Re-run the deterministic stages from frozen capsule inputs only.

Replay answers the third, independent question: *given only what this capsule
froze, do the deterministic stages still produce the same bytes?*  It must never
be able to succeed by accident — so it reads sources exclusively through
``reads.jsonl`` and the content-addressed blobs, writes into a throwaway scratch
directory, and refuses to touch the lake, the network, the report directory, the
original staging tree, or the outcome ledger.

LLM stages are never re-executed: their output is not reproducible, so they are
reported as ``EVIDENCE_ONLY`` rather than pretended to be replayable.
"""

from __future__ import annotations

import io
import json
import os
import shutil
import tempfile
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path

import pandas as pd

from autoresearch.trace.atomic import atomic_write_json, canonical_json, sha256_bytes
from autoresearch.trace.blobs import blob_path
from autoresearch.trace.source_lineage import normalized_params

REPLAY_ENV = "AUTORESEARCH_REPLAY_CAPSULE"
SCHEMA_VERSION = 1

FULL = "FULL"
PARTIAL = "PARTIAL"
NONE = "NONE"
EVIDENCE_ONLY = "EVIDENCE_ONLY"


class ReplayInputMissing(RuntimeError):
    """A replayed read has no frozen blob; replay must stop, never fall back."""


def _read_rows(capsule: Path) -> list[dict]:
    path = Path(capsule) / "lineage/reads.jsonl"
    if not path.is_file():
        return []
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def read_key(endpoint: str, params: object) -> str:
    """The stable identity of one read: endpoint plus the same normalization the
    lineage writer used.  Keying on raw params would miss on any value the writer
    had to coerce, and a replay miss must mean *absent*, not *spelled differently*."""
    return canonical_json(
        {"endpoint": str(endpoint), "params": normalized_params(params)}
    )


def build_index(capsule: Path | str) -> dict[str, dict]:
    """Map every frozen read to its row; later reads of one key win."""
    index: dict[str, dict] = {}
    for row in _read_rows(Path(capsule)):
        key = canonical_json(
            {
                "endpoint": str(row.get("endpoint")),
                "params": row.get("normalized_params"),
            }
        )
        index[key] = row
    return index


def active_capsule() -> Path | None:
    """The capsule this process must replay from, or ``None`` in production."""
    raw = str(os.environ.get(REPLAY_ENV, "")).strip()
    if not raw:
        return None
    capsule = Path(raw)
    if not capsule.is_dir():
        raise ReplayInputMissing(f"replay capsule is not a directory: {capsule}")
    return capsule


def frame_for(capsule: Path | str, endpoint: str, params: object) -> pd.DataFrame:
    """Resolve one read through the frozen lineage; a miss is fatal by design."""
    root = Path(capsule)
    row = build_index(root).get(read_key(endpoint, params))
    if row is None:
        raise ReplayInputMissing(
            f"no frozen read for endpoint={endpoint!r} params={params!r}"
        )
    digest = row.get("normalized_blob_hash") or row.get("blob_hash")
    if not digest:
        raise ReplayInputMissing(
            f"frozen read for {endpoint!r} has no blob: status={row.get('status')!r}"
        )
    path = blob_path(root, digest)
    if not path.is_file():
        raise ReplayInputMissing(f"frozen blob is gone: {digest}")
    return pd.read_parquet(io.BytesIO(path.read_bytes()))


def missing_blobs(capsule: Path | str) -> list[str]:
    """Every frozen read whose bytes are no longer inside the capsule."""
    root = Path(capsule)
    gaps = []
    for row in _read_rows(root):
        digest = row.get("normalized_blob_hash") or row.get("blob_hash")
        if not digest or not blob_path(root, digest).is_file():
            gaps.append(
                canonical_json(
                    {
                        "endpoint": str(row.get("endpoint")),
                        "params": row.get("normalized_params"),
                    }
                )
            )
    return sorted(set(gaps))


# --- output comparison ------------------------------------------------------


def canonical_hash(path: Path) -> str:
    """Hash a product by meaning, not by incidental byte layout.

    CSV is parsed and re-serialized canonically (incidental dtype or float
    formatting differences must not read as a replay mismatch), JSON is canonicalized, and
    everything else is hashed byte-for-byte.
    """
    suffix = path.suffix.lower()
    if suffix == ".csv":
        # Parsed, not literal: `1.0` and `1.00` are the same number, and a
        # formatting difference must not read as a replay mismatch.  Precision
        # differences still change the parsed float and are still caught.
        frame = pd.read_csv(path)
        payload = canonical_json(
            {
                "columns": list(frame.columns),
                "rows": frame.where(pd.notna(frame), None).to_dict(orient="records"),
            }
        ).encode("utf-8")
    elif suffix == ".json":
        payload = canonical_json(json.loads(path.read_text(encoding="utf-8"))).encode(
            "utf-8"
        )
    else:
        payload = path.read_bytes()
    return sha256_bytes(payload)


@dataclass(frozen=True)
class StageSpec:
    """One deterministic stage: how to re-run it and what it must reproduce."""

    stage: str
    argv: tuple[str, ...]
    outputs: tuple[str, ...]


def default_stage_specs(analysis_date: str) -> tuple[StageSpec, ...]:
    return (
        StageSpec(
            "l0",
            ("autoresearch.scan.frame", analysis_date, "--json-out", "market_pack.json"),
            ("market_pack.json",),
        ),
        StageSpec(
            "l1",
            ("autoresearch.scan.universe", analysis_date),
            ("L1_scored_full.csv", "L1_recall_top1000.csv"),
        ),
        StageSpec(
            "l2",
            ("autoresearch.scan.l2_stratify", analysis_date),
            ("L2_gbdt_top200.csv",),
        ),
        StageSpec(
            "l5",
            ("autoresearch.scan.assemble", analysis_date),
            ("summary.md",),
        ),
    )


def _subprocess_runner(spec: StageSpec, scratch: Path, env: dict) -> int:
    import subprocess

    completed = subprocess.run(  # noqa: S603 - argv is built from the frozen spec
        ["uv", "run", "--no-sync", "python", "-m", *spec.argv],
        cwd=str(scratch),
        env=env,
        capture_output=True,
        check=False,
    )
    return completed.returncode


def replay(
    run_id: str,
    *,
    capsule: Path | str,
    analysis_date: str,
    stages: Sequence[str] = ("l0", "l1", "l2", "l5"),
    runner: Callable[[StageSpec, Path, dict], int] = _subprocess_runner,
    specs: Sequence[StageSpec] | None = None,
    keep_scratch: bool = True,
) -> dict:
    """Replay the named deterministic stages inside a throwaway scratch tree."""
    root = Path(capsule).resolve()
    gaps = missing_blobs(root)
    resolved_specs = {
        spec.stage: spec
        for spec in (specs if specs is not None else default_stage_specs(analysis_date))
    }
    scratch = Path(tempfile.mkdtemp(prefix=f"autoresearch-replay-{run_id}-"))
    frozen_products = root / "products/staging"
    if frozen_products.is_dir():
        shutil.copytree(frozen_products, scratch / "staging", dirs_exist_ok=True)
    env = {
        key: value
        for key, value in os.environ.items()
        if not key.startswith("AUTORESEARCH_")
    }
    env[REPLAY_ENV] = str(root)
    env["AUTORESEARCH_ENGINE"] = str(os.environ.get("AUTORESEARCH_ENGINE", ""))

    rows: list[dict] = []
    for stage in stages:
        spec = resolved_specs.get(stage)
        if spec is None:
            rows.append(
                {
                    "stage": stage,
                    "status": EVIDENCE_ONLY,
                    "match": None,
                    "reason": "stage is not deterministically replayable",
                    "outputs": [],
                }
            )
            continue
        try:
            code = runner(spec, scratch, env)
        except ReplayInputMissing as exc:
            rows.append(
                {
                    "stage": stage,
                    "status": PARTIAL,
                    "match": False,
                    "reason": str(exc),
                    "outputs": [],
                }
            )
            continue
        outputs = []
        matched = code == 0
        for name in spec.outputs:
            produced = scratch / "staging" / name
            frozen = frozen_products / name
            row = {
                "output_path": str(produced),
                "name": name,
                "frozen": frozen.is_file(),
                "produced": produced.is_file(),
                "match": False,
            }
            if frozen.is_file() and produced.is_file():
                row["match"] = canonical_hash(frozen) == canonical_hash(produced)
            matched = matched and row["match"]
            outputs.append(row)
        rows.append(
            {
                "stage": stage,
                "status": "REPLAYED",
                "match": matched,
                "exit_code": code,
                "reason": None if matched else "output differs from the frozen product",
                "outputs": outputs,
            }
        )

    replayed = [row for row in rows if row["status"] == "REPLAYED"]
    if not replayed:
        state = NONE
    elif gaps or not all(row["match"] for row in replayed):
        state = PARTIAL
    else:
        state = FULL
    result = {
        "schema_version": SCHEMA_VERSION,
        "run_id": run_id,
        "replayability": state,
        "scratch": str(scratch),
        "missing_blobs": gaps,
        "stages": rows,
    }
    atomic_write_json(root / "verification/replay.json", result)
    if not keep_scratch:
        shutil.rmtree(scratch, ignore_errors=True)
    return result


__all__ = [
    "EVIDENCE_ONLY",
    "FULL",
    "NONE",
    "PARTIAL",
    "REPLAY_ENV",
    "ReplayInputMissing",
    "StageSpec",
    "active_capsule",
    "build_index",
    "canonical_hash",
    "default_stage_specs",
    "frame_for",
    "missing_blobs",
    "read_key",
    "replay",
]
