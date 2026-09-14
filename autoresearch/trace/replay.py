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
import tempfile
from collections.abc import Callable, Sequence
from contextvars import ContextVar
from dataclasses import dataclass
from pathlib import Path

import pandas as pd

from autoresearch.contracts import stages as vocab
from autoresearch.trace.atomic import atomic_write_json, canonical_json, sha256_bytes
from autoresearch.trace.blobs import blob_path
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
) -> dict:
    """Replay the named deterministic stages inside a throwaway scratch tree."""
    root = Path(capsule).resolve()
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
    atomic_write_json(root / "verification/replay.json", result)
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
