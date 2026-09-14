"""Re-run the deterministic stages from frozen capsule inputs only.

Replay answers the third, independent question: *given only what this capsule
froze, do the deterministic stages still produce the same bytes?*  It must never
be able to succeed by accident — so it reads sources exclusively through
``reads.jsonl`` and the content-addressed blobs, writes into a throwaway scratch
directory, and refuses to touch the lake, the network, the report directory, the
original staging tree, or the outcome ledger.

LLM stages are never re-executed: their output is not reproducible, so they are
reported as ``EVIDENCE_ONLY`` rather than pretended to be replayable.

**L1 与 L2 是同一条命令(2026-08-29)。** 在此之前 l2 的 spec 写的是
``("autoresearch.scan.l2_stratify", date)`` —— 那个模块**根本不存在**(真身在
``autoresearch/scan/recall/l2_stratify.py``,而且它没有 ``main()``,``python -m``
跑不起来)。这条死 argv 从未变红,因为既有用例全程注入假 runner:一个「永不变红的
绿灯」。L2 的真实生产者是 ``autoresearch.scan.universe`` —— 它内部调
``recall.l2_stratify.select_l2``,一次写出 ``L1_scored_full.csv`` /
``L1_recall_top1000.csv`` / ``L2_gbdt_top200.csv`` 三份。所以修法不是把 l2 的 argv
也写成 universe(那会把 universe 跑两遍:浪费,且两遍之间湖/时钟状态未必相同,足以
凭空造出一个「不可重放」的假结论),而是**合成一个 spec、声明三份产物、只跑一次**。

对外结构不变:``l1`` / ``l2`` 两个旧名保留为 :data:`STAGE_ALIASES` 里的别名,各自映到
同一次执行的结果,并**只汇报自己名下那几份产物** —— 于是 ``capsule`` /
``completeness`` 的读侧(以及 ``capsule replay --stage l2`` 这类命令行)逐字节不变。
显式注入的 ``specs=`` 永远优先于别名解析,老调用方的行为一并不动。
"""

from __future__ import annotations

import io
import json
import os
import shutil
import stat
import subprocess
import sys
import tempfile
from collections.abc import Callable, Sequence
from contextvars import ContextVar
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import quote

import pandas as pd

from autoresearch.contracts import stages as vocab
from autoresearch.contracts.forensic import validate_evidence_plan
from autoresearch.contracts.operation_replay import OPERATION_REPLAY_CLASSIFICATION
from autoresearch.contracts.replay import (
    validate_replay_plan,
    validate_replay_result,
)
from autoresearch.trace.atomic import atomic_write_json, canonical_json, sha256_bytes
from autoresearch.trace.blobs import blob_path
from autoresearch.trace.offline import IsolatedResult, OfflineLayout, create_offline_layout
from autoresearch.trace.source_lineage import normalized_params
from autoresearch.trace.source_receipts import (
    SourcePayloadMissing,
    SourceSequenceMismatch,
    read_receipts,
    replay_response,
)

REPLAY_ENV = "AUTORESEARCH_REPLAY_CAPSULE"
SCHEMA_VERSION = 1

FULL = "FULL"
PARTIAL = "PARTIAL"
NONE = "NONE"
EVIDENCE_ONLY = "EVIDENCE_ONLY"


class ReplayInputMissing(RuntimeError):
    """A replayed read has no frozen blob; replay must stop, never fall back."""


_REPLAY_CURSORS: ContextVar[dict[tuple[str, str, int, str], int] | None] = ContextVar(
    "autoresearch_replay_cursors",
    default=None,
)


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


def build_index(capsule: Path | str) -> dict[str, list[dict]]:
    """Map every legacy read to its ordered occurrences without overwriting."""
    index: dict[str, list[dict]] = {}
    for row in _read_rows(Path(capsule)):
        key = canonical_json(
            {
                "endpoint": str(row.get("endpoint")),
                "params": row.get("normalized_params"),
            }
        )
        index.setdefault(key, []).append(row)
    return index


def reset_replay_sequence(capsule: Path | str | None = None) -> None:
    """Reset response occurrence cursors before one isolated replay execution."""
    current = _REPLAY_CURSORS.get() or {}
    if capsule is None:
        _REPLAY_CURSORS.set({})
        return
    prefix = str(Path(capsule).resolve())
    _REPLAY_CURSORS.set({key: value for key, value in current.items() if key[0] != prefix})


def _cursor_key(capsule: Path, key: str) -> tuple[str, str, int, str]:
    task_id = str(os.environ.get("AUTORESEARCH_TASK_ID", "")).strip()
    try:
        attempt = int(str(os.environ.get("AUTORESEARCH_ATTEMPT", "1")).strip())
    except ValueError:
        attempt = 1
    return (str(capsule.resolve()), task_id, attempt, key)


def _next_occurrence(capsule: Path, key: str) -> int:
    cursor_key = _cursor_key(capsule, key)
    current = _REPLAY_CURSORS.get() or {}
    position = current.get(cursor_key, 0)
    _REPLAY_CURSORS.set({**current, cursor_key: position + 1})
    return position


def _strict_matches(capsule: Path, endpoint: str, params: object) -> list[dict]:
    rows = read_receipts(capsule)
    wanted = normalized_params(params)
    task_id = str(os.environ.get("AUTORESEARCH_TASK_ID", "")).strip()
    attempt_raw = str(os.environ.get("AUTORESEARCH_ATTEMPT", "")).strip()
    attempt = int(attempt_raw) if attempt_raw.isdigit() else None
    return [
        row
        for row in rows
        if row["endpoint"] == str(endpoint)
        and row["normalized_params"] == wanted
        and (not task_id or row["task_id"] == task_id)
        and (attempt is None or row["attempt"] == attempt)
    ]


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
    key = read_key(endpoint, params)
    position = _next_occurrence(root, key)
    strict = _strict_matches(root, endpoint, params)
    if strict:
        if position >= len(strict):
            raise SourceSequenceMismatch(
                f"frozen response sequence exhausted for endpoint={endpoint!r} "
                f"params={params!r} occurrence={position + 1}"
            )
        try:
            value = replay_response(root, strict[position]["receipt_id"])
        except SourcePayloadMissing as exc:
            raise ReplayInputMissing(str(exc)) from exc
        if not isinstance(value, pd.DataFrame):
            raise ReplayInputMissing(
                f"frozen response for {endpoint!r} is not a DataFrame"
            )
        return value
    rows = build_index(root).get(key, [])
    if position >= len(rows):
        if rows:
            raise SourceSequenceMismatch(
                f"frozen response sequence exhausted for endpoint={endpoint!r} "
                f"params={params!r} occurrence={position + 1}"
            )
        raise ReplayInputMissing(
            f"no frozen read for endpoint={endpoint!r} params={params!r}"
        )
    row = rows[position]
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
    strict_ids = {row["receipt_id"]: row for row in read_receipts(root)}
    for row in _read_rows(root):
        receipt = strict_ids.get(str(row.get("source_receipt_id") or ""))
        if receipt is not None:
            digest = receipt["payload_hash"]
            if blob_path(root, digest).is_file():
                continue
        else:
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


@dataclass(frozen=True)
class ReplayContext:
    """The runner-visible view.  It deliberately has no expected or frozen path."""

    unit_id: str
    code: Path
    inputs: Path
    runtime: Path
    work: Path
    outputs: Path
    effects: Path
    audit: Path
    env: dict[str, str]
    operation_request: dict
    source_receipts: tuple[dict, ...]

    def output_path(self, artifact_id: str) -> Path:
        target = self.outputs / _safe_name(artifact_id)
        target.parent.mkdir(parents=True, exist_ok=True)
        return target


#: 单元名一律来自契约词汇(`contracts.stages`),不在这里另立一套 —— 「该有什么」的分母
#: 有四个版本正是 spec §2.2 K3 的病。这里只保留**行为**:每个执行单元跑什么、产什么。
L1L2 = vocab.L1L2_UNIT

#: 旧阶段名 → (真正执行的 spec, 该名字对外汇报的产物子集)。
#: 合并的是**执行**,不是对外结构:`l1` 仍只汇报两份 L1 产物、`l2` 仍只汇报 L2 那份,
#: 所以 `l2` 那一行不会被 `l1` 的命中带绿,反之亦然。模块 docstring 记了为什么合并。
#: 键与「映到谁」出自 `vocab.REPLAY_UNIT_ALIASES`;这里只补每个别名对外汇报的产物视图,
#: 词汇表新增一个别名而这里忘了给视图 → 建表时当场 KeyError,不会安静少报一份产物。
_ALIAS_OUTPUTS: dict[str, tuple[str, ...]] = {
    "l1": ("L1_scored_full.csv", "L1_recall_top1000.csv"),
    "l2": ("L2_gbdt_top200.csv",),
}
STAGE_ALIASES: dict[str, tuple[str, tuple[str, ...]]] = {
    unit: (target, _ALIAS_OUTPUTS[unit])
    for unit, target in vocab.REPLAY_UNIT_ALIASES.items()
}


def default_stage_specs(
    analysis_date: str, *, kind: str = "scan-market"
) -> tuple[StageSpec, ...]:
    """One spec per **execution unit** declared in the contracts vocabulary.

    The unit names and their order come from `vocab.REPLAY_EXEC_UNITS`; only the argv and
    the product list live here.  A unit in the vocabulary with no plan (or a plan for a
    unit nobody replays) raises instead of silently skipping a stage — the l2 dead-argv bug
    was invisible for exactly that reason.

    非 `scan-market` 的 kind 返回**空表**。`stock-research` 的 `replayable_stages` 是
    `()`(`analyze/run_profile` 里已诚实声明:它的每一步要么带网络取数、要么是 LLM),
    给它套 scan 的 l0/l1/l2/l5 argv 会去跑一趟真的全市场扫描 —— 那不是重放,
    那是在别人的现场里另起一趟。
    """
    if kind != "scan-market":
        return ()
    plans: dict[str, tuple[tuple[str, ...], tuple[str, ...]]] = {
        "l0": (
            ("autoresearch.scan.frame", analysis_date, "--json-out", "market_pack.json"),
            ("market_pack.json",),
        ),
        # 一条命令三产物:`universe` 内部调 `recall.l2_stratify.select_l2`,L1 与 L2
        # 从来就是同一次执行的两半。历史上它们是两个 spec,而 l2 的 argv 指向一个
        # 不存在的模块 —— 见模块 docstring。
        L1L2: (
            ("autoresearch.scan.universe", analysis_date),
            ("L1_scored_full.csv", "L1_recall_top1000.csv", "L2_gbdt_top200.csv"),
        ),
        # L5 的产物是**一个发布包**(2026-08-28 §6.3):summary=决策层、appendix=现场层。
        # 只比对 summary 会让「附录没重现出来」这件事在 replay 里完全不可见,而
        # 「产物能证明跑过什么、不能证明没跑过什么」正是这层要防的洞。
        "l5": (
            ("autoresearch.scan.assemble", analysis_date),
            ("summary.md", "appendix.md"),
        ),
    }
    drift = set(plans) ^ set(vocab.REPLAY_EXEC_UNITS)
    if drift:
        raise ValueError(
            f"replay units and execution plans disagree: {sorted(drift)} "
            f"(vocabulary={vocab.REPLAY_EXEC_UNITS!r}, plans={sorted(plans)!r})"
        )
    return tuple(StageSpec(unit, *plans[unit]) for unit in vocab.REPLAY_EXEC_UNITS)


def _resolve_stage(
    stage: str, specs: dict[str, StageSpec]
) -> tuple[StageSpec | None, tuple[str, ...] | None]:
    """Find the spec that answers for ``stage``, plus the outputs it may report.

    Exact name first, so an explicitly injected ``specs=`` always wins over the
    alias table (old callers and tests must behave byte-identically).  Only then
    do we fall back to :data:`STAGE_ALIASES`, which maps the historical ``l1`` /
    ``l2`` names onto the single merged run.  A ``None`` view means "report every
    output this spec declares" — today's behaviour for every non-aliased stage.
    """
    spec = specs.get(stage)
    if spec is not None:
        return spec, None
    target, view = STAGE_ALIASES.get(stage, (None, None))
    if target is None:
        return None, None
    return specs.get(target), view


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


# --- session_v1 replay ------------------------------------------------------

_EXECUTABLE_MODES = frozenset({"COMPUTE", "SOURCE_REPLAY", "EFFECT_PLAN"})
_SUCCESS_STATUSES = frozenset({"MATCH", "EXPECTED_FAILURE", "CONTROL_VERIFIED", "EVIDENCE_ONLY"})


def _safe_name(value: str) -> str:
    return quote(value, safe="_.-")


def _capsule_root(root: Path | str) -> Path:
    candidate = Path(root)
    direct = candidate / "evidence/evidence_plan.json"
    nested = candidate / "capsule/evidence/evidence_plan.json"
    if direct.is_file():
        return candidate.resolve()
    if nested.is_file():
        return (candidate / "capsule").resolve()
    return candidate.resolve()


def _is_relative_to(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
        return True
    except ValueError:
        return False


def _regular_payload(path: Path) -> bytes:
    info = path.lstat()
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode):
        raise ValueError(f"replay input must be a regular file: {path}")
    return path.read_bytes()


def _tree_snapshot(root: Path) -> dict[str, tuple[str, str | None]]:
    rows: dict[str, tuple[str, str | None]] = {}
    for path in sorted(root.rglob("*")):
        relative = path.relative_to(root).as_posix()
        info = path.lstat()
        if stat.S_ISLNK(info.st_mode):
            rows[relative] = ("symlink", os.readlink(path))
        elif stat.S_ISDIR(info.st_mode):
            rows[relative] = ("directory", None)
        elif stat.S_ISREG(info.st_mode):
            rows[relative] = ("file", sha256_bytes(path.read_bytes()))
        else:
            rows[relative] = ("special", None)
    return rows


def _copy_ref(capsule: Path, ref: dict, target: Path) -> str | None:
    source = capsule / ref["captured_path"]
    try:
        source.resolve(strict=True).relative_to(capsule)
        payload = _regular_payload(source)
    except (FileNotFoundError, OSError, ValueError):
        return f"ARTIFACT_MISSING:{ref['artifact_id']}"
    if sha256_bytes(payload) != ref["sha256"]:
        return f"ARTIFACT_HASH_MISMATCH:{ref['artifact_id']}"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(payload)
    return None


def _make_read_only(root: Path) -> None:
    if not root.exists():
        return
    for path in sorted(root.rglob("*"), reverse=True):
        if path.is_symlink():
            raise ValueError(f"replay scratch input contains symlink: {path}")
        path.chmod(0o555 if path.is_dir() else 0o444)
    root.chmod(0o555)


def _restore_identity(capsule: Path, layout: OfflineLayout, plan: dict) -> tuple[str, list[str]]:
    missing: list[str] = []
    runtime_target = layout.runtime / "runtime_manifest.json"
    reason = _copy_ref(capsule, plan["runtime_ref"], runtime_target)
    runtime_status = "UNAVAILABLE"
    runtime: dict | None = None
    if reason:
        missing.append(reason)
    else:
        try:
            runtime = json.loads(runtime_target.read_text(encoding="utf-8"))
        except (OSError, ValueError, TypeError):
            missing.append("RUNTIME_MANIFEST_INVALID")

    bundle = capsule / "identity/source_tree.tar.zst"
    manifest = capsule / "identity/source_tree_manifest.json"
    restored = False
    if bundle.is_file() and manifest.is_file():
        try:
            from autoresearch.trace.source_tree import restore_source_tree

            layout.code.rmdir()
            result = restore_source_tree(bundle, layout.code, manifest_path=manifest)
            if result["code_tree_hash"] != plan["code_tree_hash"]:
                raise ValueError("captured source hash differs from replay plan")
            restored = True
        except Exception as exc:  # the reason is evidence, never a live-code fallback
            missing.append(f"SOURCE_TREE_UNAVAILABLE:{type(exc).__name__}")
    else:
        missing.append("SOURCE_TREE_UNAVAILABLE:MISSING_BUNDLE")
    if not restored:
        runtime_status = "UNAVAILABLE"
        layout.code.mkdir(exist_ok=True)
    elif runtime is not None:
        try:
            from autoresearch.trace.source_tree import check_runtime_availability

            dependency_probe = subprocess.run(
                ["uv", "pip", "freeze", "--python", sys.executable],
                capture_output=True,
                check=False,
            )
            dependencies = dependency_probe.stdout if dependency_probe.returncode == 0 else None
            availability = check_runtime_availability(
                runtime,
                dependencies=dependencies,
                portable_runtime=capsule / "identity/portable_runtime.tar.zst",
            )
            runtime_status = availability["status"]
            if runtime_status == "UNAVAILABLE":
                missing.append(f"RUNTIME_IDENTITY_UNAVAILABLE:{availability['reason']}")
        except Exception as exc:
            missing.append(f"RUNTIME_IDENTITY_UNAVAILABLE:{type(exc).__name__}")
    _make_read_only(layout.code)
    _make_read_only(layout.runtime)
    return runtime_status, missing


def _source_inputs(
    capsule: Path,
    unit: dict,
    unit_inputs: Path,
) -> tuple[tuple[dict, ...], list[str], Path]:
    wanted = set(unit["source_receipt_ids"])
    rows = {row["receipt_id"]: row for row in read_receipts(capsule)}
    selected: list[dict] = []
    missing: list[str] = []
    source_capsule = unit_inputs / "source_capsule"
    for receipt_id in unit["source_receipt_ids"]:
        row = rows.get(receipt_id)
        if row is None:
            missing.append(f"SOURCE_RECEIPT_MISSING:{receipt_id}")
            continue
        selected.append(row)
        for digest in (row["payload_hash"], row.get("raw_hash")):
            if not digest:
                continue
            source = blob_path(capsule, digest)
            target = source_capsule / blob_path(Path("."), digest)
            try:
                payload = _regular_payload(source)
            except (FileNotFoundError, OSError, ValueError):
                missing.append(f"SOURCE_PAYLOAD_MISSING:{receipt_id}:{digest}")
                continue
            if sha256_bytes(payload) != digest:
                missing.append(f"SOURCE_PAYLOAD_HASH_MISMATCH:{receipt_id}:{digest}")
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(payload)
    if selected:
        receipt_path = source_capsule / "lineage/source_receipts.jsonl"
        receipt_path.parent.mkdir(parents=True, exist_ok=True)
        receipt_path.write_text(
            "".join(canonical_json(row) + "\n" for row in selected), encoding="utf-8"
        )
    if wanted != {row["receipt_id"] for row in selected}:
        missing.extend(
            f"SOURCE_RECEIPT_MISSING:{receipt_id}"
            for receipt_id in sorted(wanted - {row["receipt_id"] for row in selected})
        )
    return tuple(selected), sorted(set(missing)), source_capsule


def _normalized_text(path: Path) -> bytes:
    return path.read_text(encoding="utf-8").replace("\r\n", "\n").encode("utf-8")


def _compare_output(produced: Path, expected: Path, policy: str) -> bool:
    if policy in {"CANONICAL_JSON", "STATE_MUTATION"}:
        return canonical_json(json.loads(produced.read_text(encoding="utf-8"))) == canonical_json(
            json.loads(expected.read_text(encoding="utf-8"))
        )
    if policy == "PARQUET_VALUES":
        try:
            pd.testing.assert_frame_equal(pd.read_parquet(produced), pd.read_parquet(expected))
            return True
        except AssertionError:
            return False
    if policy == "TEXT_NORMALIZED":
        return _normalized_text(produced) == _normalized_text(expected)
    return produced.read_bytes() == expected.read_bytes()


def _runner_value(outcome: object, field: str, default=None):
    if isinstance(outcome, dict):
        return outcome.get(field, default)
    return getattr(outcome, field, default)


def _attests_path(path: Path, roots: tuple[str, ...]) -> bool:
    resolved = path.resolve()
    return any(_is_relative_to(resolved, Path(root)) for root in roots)


def _isolation_verdict(outcome: object, capsule: Path, layout: OfflineLayout) -> str:
    """Only a result minted by the system sandbox can attest ENFORCED."""
    if not isinstance(outcome, IsolatedResult) or outcome.isolation_status != "ENFORCED":
        return "UNKNOWN"
    required_reads = (capsule, layout.expected)
    required_writes = (
        capsule,
        layout.code,
        layout.inputs,
        layout.expected,
        layout.runtime,
    )
    if not all(_attests_path(path, outcome.denied_read_roots) for path in required_reads):
        return "FAILED"
    if not all(_attests_path(path, outcome.denied_write_roots) for path in required_writes):
        return "FAILED"
    return "ENFORCED"


def _run_mode(capsule: Path) -> str:
    for path, field in (
        (capsule / "products/staging/run_mode.json", "mode"),
        (capsule / "identity/session/plan.json", "requested_mode"),
        (capsule / "identity/session/request.json", "requested_mode"),
    ):
        if path.is_file():
            try:
                value = json.loads(path.read_text(encoding="utf-8")).get(field)
            except (OSError, ValueError, TypeError):
                continue
            if isinstance(value, str) and value:
                return value
    return "UNKNOWN"


def execute_replay(
    plan: dict,
    frozen_root: Path | str,
    output_dir: Path | str,
    runner: Callable[[dict, ReplayContext], object],
) -> dict:
    """Execute a frozen ReplayPlan without exposing or modifying its source capsule."""
    validate_replay_plan(plan)
    capsule = _capsule_root(frozen_root)
    output = Path(output_dir).resolve()
    if _is_relative_to(output, capsule):
        raise ValueError("replay output_dir must be outside the frozen capsule")
    evidence_plan = validate_evidence_plan(
        json.loads((capsule / "evidence/evidence_plan.json").read_text(encoding="utf-8"))
    )
    if any(
        evidence_plan[field] != plan[replay_field]
        for field, replay_field in (
            ("engine", "engine"),
            ("run_id", "run_id"),
            ("plan_hash", "plan_hash"),
            ("evidence_plan_hash", "evidence_plan_hash"),
        )
    ):
        raise ValueError("replay plan does not match the frozen evidence denominator")
    before = _tree_snapshot(capsule)
    layout = create_offline_layout(output)
    identity_status, identity_missing = _restore_identity(capsule, layout, plan)
    missing = list(identity_missing)
    diffs: list[str] = []
    effects: list[dict] = []
    unit_results: list[dict] = []
    status_by_unit: dict[str, str] = {}
    executable_isolation: list[str] = []
    operation_classes = OPERATION_REPLAY_CLASSIFICATION

    requested_scope = list(evidence_plan["scope"])
    scene_refs = 0
    scene_present = 0

    for unit in plan["units"]:
        unit_id = unit["unit_id"]
        safe_unit = _safe_name(unit_id)
        mode = unit["mode"]
        blocked = [
            dependency
            for dependency in unit["dependencies"]
            if status_by_unit.get(dependency) not in _SUCCESS_STATUSES
        ]
        if blocked:
            reason = f"DEPENDENCY_NOT_REPLAYED:{','.join(blocked)}"
            missing.append(reason)
            result = {
                "unit_id": unit_id,
                "status": "MISSING_INPUT",
                "matched": False,
                "exit_code": None,
                "output_diffs": [],
                "reason": reason,
            }
            unit_results.append(result)
            status_by_unit[unit_id] = result["status"]
            continue

        if mode == "CONTROL_ONLY":
            control_root = layout.inputs / "control" / safe_unit
            local_missing = []
            for ref in unit["input_refs"]:
                scene_refs += 1
                reason = _copy_ref(
                    capsule,
                    ref,
                    control_root / _safe_name(ref["artifact_id"]),
                )
                if reason:
                    local_missing.append(reason)
                else:
                    scene_present += 1
            _make_read_only(control_root)
            missing.extend(local_missing)
            result = {
                "unit_id": unit_id,
                "status": "CONTROL_VERIFIED" if not local_missing else "MISSING_INPUT",
                "matched": not local_missing,
                "exit_code": None,
                "output_diffs": [],
                "reason": None if not local_missing else ";".join(local_missing),
            }
            unit_results.append(result)
            status_by_unit[unit_id] = result["status"]
            continue

        if mode == "EVIDENCE_ONLY":
            reinjected = layout.inputs / "reinjected" / safe_unit
            local_missing = []
            for ref in unit["input_refs"]:
                scene_refs += 1
                reason = _copy_ref(
                    capsule,
                    ref,
                    reinjected / "evidence" / _safe_name(ref["artifact_id"]),
                )
                if reason:
                    local_missing.append(reason)
                else:
                    scene_present += 1
            for ref in unit["expected_outputs"]:
                scene_refs += 1
                reason = _copy_ref(capsule, ref, reinjected / _safe_name(ref["artifact_id"]))
                if reason:
                    local_missing.append(reason)
                else:
                    scene_present += 1
            _make_read_only(reinjected)
            missing.extend(local_missing)
            result = {
                "unit_id": unit_id,
                "status": "EVIDENCE_ONLY" if not local_missing else "MISSING_INPUT",
                "matched": False,
                "exit_code": None,
                "output_diffs": [],
                "reason": (
                    "frozen model output reinjected without inference"
                    if not local_missing
                    else ";".join(local_missing)
                ),
            }
            unit_results.append(result)
            status_by_unit[unit_id] = result["status"]
            continue

        operation = unit["operation"]
        if operation not in operation_classes or operation_classes[operation] == "TEST_ONLY":
            reason = f"UNSUPPORTED_OPERATION:{operation}"
            missing.append(reason)
            result = {
                "unit_id": unit_id,
                "status": "UNSUPPORTED",
                "matched": False,
                "exit_code": None,
                "output_diffs": [],
                "reason": reason,
            }
            unit_results.append(result)
            status_by_unit[unit_id] = result["status"]
            continue

        unit_inputs = layout.inputs / "units" / safe_unit
        expected_root = layout.expected / safe_unit
        output_root = layout.outputs / safe_unit
        effect_root = layout.effects / safe_unit
        for path in (unit_inputs, expected_root, output_root, effect_root):
            path.mkdir(parents=True, exist_ok=True)
        local_missing: list[str] = []
        operation_request: dict = {}
        for ref in unit["input_refs"]:
            scene_refs += 1
            target = unit_inputs / "artifacts" / _safe_name(ref["artifact_id"])
            reason = _copy_ref(capsule, ref, target)
            if reason:
                local_missing.append(reason)
            else:
                scene_present += 1
                if ref["artifact_id"].startswith("operation.request:"):
                    try:
                        operation_request = json.loads(target.read_text(encoding="utf-8"))
                    except (OSError, ValueError, TypeError):
                        local_missing.append(f"OPERATION_REQUEST_INVALID:{unit_id}")
        expected_paths: dict[str, Path] = {}
        for ref in unit["expected_outputs"]:
            scene_refs += 1
            target = expected_root / _safe_name(ref["artifact_id"])
            reason = _copy_ref(capsule, ref, target)
            if reason:
                local_missing.append(reason)
            else:
                scene_present += 1
                expected_paths[ref["artifact_id"]] = target
        source_receipts, source_missing, source_capsule = _source_inputs(
            capsule, unit, unit_inputs
        )
        if any(
            row["task_id"] != unit["task_id"] or row["attempt"] != unit["attempt"]
            for row in source_receipts
        ):
            local_missing.append(f"SOURCE_RECEIPT_IDENTITY_MISMATCH:{unit_id}")
        scene_refs += len(unit["source_receipt_ids"])
        scene_present += len(source_receipts)
        local_missing.extend(source_missing)
        if not operation_request:
            local_missing.append(f"OPERATION_REQUEST_MISSING:{unit_id}")
        elif any(
            operation_request.get(field) != expected
            for field, expected in (
                ("task_id", unit["task_id"]),
                ("attempt", unit["attempt"]),
                ("operation", unit["operation"]),
            )
        ):
            local_missing.append(f"OPERATION_REQUEST_IDENTITY_MISMATCH:{unit_id}")
        _make_read_only(unit_inputs)
        _make_read_only(expected_root)
        if local_missing:
            missing.extend(local_missing)
            reason = ";".join(sorted(set(local_missing)))
            result = {
                "unit_id": unit_id,
                "status": "MISSING_INPUT",
                "matched": False,
                "exit_code": None,
                "output_diffs": [],
                "reason": reason,
            }
            unit_results.append(result)
            status_by_unit[unit_id] = result["status"]
            continue

        env = {
            "AUTORESEARCH_ENGINE": plan["engine"],
            REPLAY_ENV: str(source_capsule),
            "AUTORESEARCH_TASK_ID": unit["task_id"],
            "AUTORESEARCH_ATTEMPT": str(unit["attempt"]),
            "AUTORESEARCH_FROZEN_CLOCK": str(
                operation_request.get("frozen_clock") or plan["frozen_clock"]
            ),
        }
        context = ReplayContext(
            unit_id=unit_id,
            code=layout.code,
            inputs=unit_inputs,
            runtime=layout.runtime,
            work=layout.work / safe_unit,
            outputs=output_root,
            effects=effect_root,
            audit=layout.audit / safe_unit,
            env=env,
            operation_request=operation_request,
            source_receipts=source_receipts,
        )
        context.work.mkdir(parents=True, exist_ok=True)
        context.audit.mkdir(parents=True, exist_ok=True)
        try:
            outcome = runner(unit, context)
            exit_code = _runner_value(outcome, "exit_code")
            if type(exit_code) is not int:
                raise TypeError("replay runner did not return an integer exit_code")
        except Exception as exc:
            reason = f"RUNNER_FAILED:{type(exc).__name__}:{exc}"
            result = {
                "unit_id": unit_id,
                "status": "EXECUTION_FAILED",
                "matched": False,
                "exit_code": None,
                "output_diffs": [],
                "reason": reason,
            }
            unit_results.append(result)
            status_by_unit[unit_id] = result["status"]
            diffs.append(reason)
            executable_isolation.append("FAILED")
            continue

        executable_isolation.append(_isolation_verdict(outcome, capsule, layout))
        returned_effects = _runner_value(outcome, "effects", [])
        if isinstance(returned_effects, list):
            effects.extend(returned_effects)
        expectation = unit["failure_expectation"]
        output_diffs: list[str] = []
        if exit_code != 0:
            error = _runner_value(outcome, "error", {})
            category = str(error.get("code") or error.get("category") or "") if isinstance(error, dict) else ""
            message_hash = (
                sha256_bytes(canonical_json(error).encode("utf-8"))
                if isinstance(error, dict)
                else ""
            )
            expected_failure = bool(
                expectation
                and category == expectation["category"]
                and message_hash == expectation["message_hash"]
            )
            status = "EXPECTED_FAILURE" if expected_failure else "EXECUTION_FAILED"
            reason = (
                "recorded failure reproduced"
                if expected_failure
                else f"UNEXPECTED_EXIT:{exit_code}"
            )
            if reason:
                diffs.append(f"{unit_id}:{reason}")
        elif expectation is not None:
            status = "MISMATCH"
            reason = "EXPECTED_FAILURE_NOT_REPRODUCED"
            diffs.append(f"{unit_id}:{reason}")
        else:
            for artifact_id, expected_path in expected_paths.items():
                produced = context.output_path(artifact_id)
                if not produced.is_file():
                    item = f"OUTPUT_NOT_PRODUCED:{unit_id}:{artifact_id}"
                    output_diffs.append(item)
                    missing.append(item)
                    continue
                try:
                    matches = _compare_output(
                        produced, expected_path, unit["comparison_policy"]["policy"]
                    )
                except (OSError, ValueError, TypeError):
                    matches = False
                if not matches:
                    output_diffs.append(f"OUTPUT_MISMATCH:{unit_id}:{artifact_id}")
            status = "MATCH" if not output_diffs else "MISMATCH"
            reason = None if status == "MATCH" else "replayed output differs"
            diffs.extend(output_diffs)
        result = {
            "unit_id": unit_id,
            "status": status,
            "matched": status in {"MATCH", "EXPECTED_FAILURE"},
            "exit_code": exit_code,
            "output_diffs": output_diffs,
            "reason": reason,
        }
        unit_results.append(result)
        status_by_unit[unit_id] = result["status"]

    required_units = sum(unit["mode"] in _EXECUTABLE_MODES for unit in plan["units"])
    executed_units = sum(
        row["status"] in {"MATCH", "MISMATCH", "EXPECTED_FAILURE", "EXECUTION_FAILED"}
        for row in unit_results
    )
    if executable_isolation and all(value == "ENFORCED" for value in executable_isolation):
        isolation_status = "ENFORCED"
    elif any(value == "FAILED" for value in executable_isolation):
        isolation_status = "FAILED"
    else:
        isolation_status = "UNKNOWN"
    if scene_refs == 0 or scene_present == 0:
        scene_status = "NONE"
    elif scene_present == scene_refs:
        scene_status = "COMPLETE"
    else:
        scene_status = "PARTIAL"
    clean_missing = sorted(set(missing))
    clean_diffs = sorted(set(diffs))
    executable_results = [
        row
        for unit, row in zip(plan["units"], unit_results, strict=True)
        if unit["mode"] in _EXECUTABLE_MODES
    ]
    full = bool(
        required_units > 0
        and executed_units == required_units
        and all(row["status"] in {"MATCH", "EXPECTED_FAILURE"} for row in executable_results)
        and identity_status != "UNAVAILABLE"
        and isolation_status == "ENFORCED"
        and not clean_missing
        and not clean_diffs
    )
    compute_status = "FULL" if full else "NONE" if executed_units == 0 else "PARTIAL"
    result = {
        "schema_version": 1,
        "engine": plan["engine"],
        "run_id": plan["run_id"],
        "run_mode": _run_mode(capsule),
        "replay_plan_hash": plan["replay_plan_hash"],
        "requested_scope": requested_scope,
        "required_units": required_units,
        "executed_units": executed_units,
        "scene_status": scene_status,
        "compute_status": compute_status,
        "model_status": "EVIDENCE_ONLY",
        "identity_status": identity_status,
        "isolation_status": isolation_status,
        "unit_results": unit_results,
        "effects": effects,
        "missing": clean_missing,
        "diffs": clean_diffs,
    }
    validate_replay_result(result)
    atomic_write_json(layout.audit / "replay.json", result)
    after = _tree_snapshot(capsule)
    if after != before:
        raise RuntimeError("frozen capsule changed during replay")
    return result


def replay(
    run_id: str,
    *,
    capsule: Path | str,
    analysis_date: str,
    stages: Sequence[str] = vocab.REPLAY_UNITS,
    runner: Callable[[StageSpec, Path, dict], int] = _subprocess_runner,
    specs: Sequence[StageSpec] | None = None,
    keep_scratch: bool = True,
    kind: str = "scan-market",
    output_dir: Path | str | None = None,
) -> dict:
    """Replay the named deterministic stages inside a throwaway scratch tree."""
    root = Path(capsule).resolve()
    frozen = (root / "verification/ROOT.json").is_file()
    if frozen and output_dir is None:
        raise RuntimeError("frozen capsule replay requires an external output_dir")
    reset_replay_sequence(root)
    gaps = missing_blobs(root)
    resolved_specs = {
        spec.stage: spec
        for spec in (
            specs
            if specs is not None
            else default_stage_specs(analysis_date, kind=kind)
        )
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
    # 一个 spec 在一次 replay 里最多执行一次。`l1` 与 `l2` 映到同一个合并 spec,重复执行
    # 会把 universe 跑两遍,还会让第二遍先删掉第一遍刚产出的文件 —— 结果凭空多出一个
    # 「不可重放」。键是**被解析到的 spec 名**,不是请求名。
    executed: dict[str, dict] = {}
    for stage in stages:
        spec, view = _resolve_stage(stage, resolved_specs)
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
        outcome = executed.get(spec.stage)
        if outcome is None:
            # scratch 是**整目录拷贝**冻结产物来的(上面 copytree) —— 所以本 stage 声明的产物
            # 在 runner 跑之前就已经躺在那儿了。不先删掉它们,一个**什么都没产出**的 stage 也会
            # 逐字节"命中",replay 报 FULL:那正是「产物能证明跑过什么、不能证明没跑过什么」
            # 被反过来用。删完再跑,`produced.is_file()` 才真的等于「这次跑出来了」。
            # (2026-08-29:appendix 进 L5 outputs 后由 `test_l5_replay_is_partial_when_only_
            #  the_summary_reproduces` 逮到;缺陷本身在单产物时代就存在,只是没有第二个产物照出来。)
            # 删的是 spec 声明的**全部**产物(不是别名视图),因为它们出自同一次执行。
            for name in spec.outputs:
                stale = scratch / "staging" / name
                if stale.is_file():
                    stale.unlink()
            try:
                outcome = {"code": runner(spec, scratch, env), "error": None}
            except (ReplayInputMissing, SourceSequenceMismatch) as exc:
                outcome = {"code": None, "error": str(exc)}
            executed[spec.stage] = outcome
        if outcome["error"] is not None:
            rows.append(
                {
                    "stage": stage,
                    "status": PARTIAL,
                    "match": False,
                    "reason": outcome["error"],
                    "outputs": [],
                }
            )
            continue
        code = outcome["code"]
        outputs = []
        matched = code == 0
        for name in (spec.outputs if view is None else view):
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
    verdict_path = (
        root / "verification/replay.json"
        if output_dir is None
        else Path(output_dir) / "replay.json"
    )
    if verdict_path.is_file():
        current = json.loads(verdict_path.read_text(encoding="utf-8"))
        if canonical_json(current) != canonical_json(result):
            raise FileExistsError("replay output already exists with different content")
    else:
        atomic_write_json(verdict_path, result)
    if not keep_scratch:
        shutil.rmtree(scratch, ignore_errors=True)
    return result


__all__ = [
    "EVIDENCE_ONLY",
    "FULL",
    "L1L2",
    "NONE",
    "PARTIAL",
    "REPLAY_ENV",
    "STAGE_ALIASES",
    "ReplayInputMissing",
    "ReplayContext",
    "StageSpec",
    "active_capsule",
    "build_index",
    "canonical_hash",
    "default_stage_specs",
    "frame_for",
    "missing_blobs",
    "read_key",
    "replay",
    "execute_replay",
]
