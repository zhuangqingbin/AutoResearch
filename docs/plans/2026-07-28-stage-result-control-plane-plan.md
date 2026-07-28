# StageResult Control Plane Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 为 scan 的关键阶段写出统一、带契约身份且可校验的结构化结果，同时保持现有 gate JSON、退出码、选股和评级行为不变。

**Architecture:** 新增 `stage_result.py` 作为有限状态和原子快照的单一实现，每个阶段写 `context/scan/<date>/stage_results/<stage>.json`。`frame/prelude/gates/assemble` 只在既有业务结果已经形成后影子双写；`health` 汇总完整性和失败阶段，`ArtifactIndex` 登记并发布这些快照。

**Tech Stack:** Python 3.12、标准库 `dataclasses/enum/hashlib/json/datetime/pathlib/re`、pytest、现有 RunContract/ArtifactIndex。

---

## Scope and invariants

本批只实现 `StageResult`，不实现 `DecisionRecord` 或 post-run outbox。

合法状态：

```text
PENDING
RUNNING
SUCCEEDED
DEGRADED
FAILED
SKIPPED
```

单份结果包含：

```text
schema_version
stage
analysis_date
status
artifacts
metrics
warnings
error
contract_hash
recorded_at
result_hash
```

不变量：

- `gate1/gate2/gate4()` 纯函数保持只读。
- gate CLI stdout 仍只输出原有 `{ok, gate, reason, ...}`。
- gate CLI 退出码保持 `ok → 0 / false → 1`。
- StageResult 写失败只向 stderr 告警，不改变生产阶段结果。
- 相同语义结果重复写入时逐字节不变，避免 assemble 影子 gate4 与正式 gate4 二次调用造成 hash 漂移。
- StageResult 的 `artifacts` 只存 ArtifactIndex 的逻辑名称。
- 本批不修改 L3 精排、L4 rubric、评级折回或 BUY 判据。

## File map

- Create: `autoresearch/scan/stage_result.py`
  - 状态枚举、结果对象、hash 校验、原子/幂等读写、安全降级入口。
- Create: `tests/scan/test_stage_result.py`
  - 状态、hash、篡改、路径安全、幂等和 contract 绑定测试。
- Modify: `autoresearch/scan/gates.py`
  - gate CLI 影子双写；导出 gate-result 适配器供 assemble 复用。
- Modify: `tests/scan/test_gates.py`
  - 锁定 stdout/退出码兼容与成功/失败 StageResult。
- Modify: `autoresearch/scan/frame.py`
  - frame 成功后写 StageResult。
- Modify: `tests/scan/test_frame_json_clean.py`
  - 锁定 frame StageResult 和 stdout 纯净。
- Modify: `autoresearch/scan/prelude.py`
  - 汇总步骤结果为 `SUCCEEDED/DEGRADED`。
- Modify: `tests/scan/test_prelude.py`
  - 锁定 prelude 状态、metrics 和 warnings。
- Modify: `autoresearch/scan/artifacts.py`
  - 注册 `stage_results/*.json`。
- Modify: `tests/scan/test_artifacts.py`
  - 锁定集合指纹。
- Modify: `autoresearch/scan/health.py`
  - 汇总 StageResult 完整性、失败/降级/跳过和 contract mismatch。
- Modify: `tests/scan/test_health.py`
  - 锁定 `ABSENT/OK/INVALID` 与业务状态分列。
- Modify: `autoresearch/scan/assemble.py`
  - 在最终 health/index 前写 assemble + gate4 结果并发布快照。
- Modify: `tests/scan/test_assemble.py`
  - 锁定最终顺序、trace 发布和 ArtifactIndex 覆盖。
- Modify: `docs/specs/2026-07-28-scan-market-unified-optimization-master-design.md`
  - 记录 Wave 1 第二批边界。

### Task 1: StageResult domain and persistence

**Files:**

- Create: `tests/scan/test_stage_result.py`
- Create: `autoresearch/scan/stage_result.py`

- [x] **Step 1: Write failing domain tests**

```python
from __future__ import annotations

import json
from datetime import datetime, timezone

import pytest

from autoresearch.scan.run_contract import RunContract, write_run_contract
from autoresearch.scan.stage_result import (
    StageResult,
    StageStatus,
    load_stage_result,
    record_stage_result,
    write_stage_result,
)

NOW = datetime(2026, 7, 28, 15, 0, tzinfo=timezone.utc)


def _result(*, status=StageStatus.SUCCEEDED, now=NOW):
    return StageResult.build(
        stage="gate1",
        analysis_date="2026-07-28",
        status=status,
        artifacts=["l2"],
        metrics={"l2_n": 200},
        warnings=[],
        error=None,
        contract_hash="a" * 64,
        now=now,
    )


def test_stage_result_round_trip_and_status_enum(tmp_path):
    path = write_stage_result(tmp_path, _result())
    loaded = load_stage_result(path)
    assert path == tmp_path / "stage_results" / "gate1.json"
    assert loaded.status == "SUCCEEDED"
    assert loaded.to_dict() == _result().to_dict()


def test_write_is_semantically_idempotent(tmp_path):
    first = write_stage_result(tmp_path, _result())
    before = first.read_bytes()
    later = datetime(2026, 7, 28, 16, 0, tzinfo=timezone.utc)
    second = write_stage_result(tmp_path, _result(now=later))
    assert second == first
    assert second.read_bytes() == before


def test_changed_semantics_replace_snapshot(tmp_path):
    path = write_stage_result(tmp_path, _result())
    before = path.read_bytes()
    write_stage_result(tmp_path, _result(status=StageStatus.FAILED))
    assert path.read_bytes() != before
    assert load_stage_result(path).status == "FAILED"


def test_load_rejects_tampered_result(tmp_path):
    path = write_stage_result(tmp_path, _result())
    raw = json.loads(path.read_text(encoding="utf-8"))
    raw["metrics"]["l2_n"] = 0
    path.write_text(json.dumps(raw), encoding="utf-8")
    with pytest.raises(ValueError, match="result_hash"):
        load_stage_result(path)


@pytest.mark.parametrize("stage", ["../gate1", "Gate 1", "", "x/y"])
def test_stage_name_rejects_unsafe_paths(stage):
    with pytest.raises(ValueError, match="stage"):
        StageResult.build(
            stage=stage,
            analysis_date="2026-07-28",
            status="SUCCEEDED",
            artifacts=[],
            metrics={},
            warnings=[],
            error=None,
            contract_hash=None,
            now=NOW,
        )


def test_record_binds_valid_run_contract(tmp_path):
    contract = RunContract.build(
        analysis_date="2026-07-28",
        user_config={},
        pinned={"kept": [], "expired": []},
        data_policy={"source": "tushare"},
        stage_budgets={},
        artifact_schema_versions={},
        git_sha="abc",
        now=NOW,
    )
    write_run_contract(tmp_path / "run_contract.json", contract)
    path = record_stage_result(
        tmp_path,
        stage="gate2",
        status="FAILED",
        artifacts=["finalists"],
        metrics={"budget": 10},
        warnings=[],
        error="finalists 空",
        now=NOW,
    )
    assert load_stage_result(path).contract_hash == contract.contract_hash
```

- [x] **Step 2: Verify RED**

Run:

```bash
uv run --no-sync python -m pytest tests/scan/test_stage_result.py -q
```

Expected: collection fails with `ModuleNotFoundError`.

- [x] **Step 3: Implement the complete StageResult module**

Create `autoresearch/scan/stage_result.py`:

```python
#!/usr/bin/env python3
"""scan 阶段结果的有限状态、完整性校验和原子快照。"""
from __future__ import annotations

import json
import re
import sys
from dataclasses import asdict, dataclass, replace
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path

from autoresearch.scan.run_contract import canonical_json, load_run_contract, sha256_json

STAGE_RESULT_SCHEMA_VERSION = 1
_STAGE_RE = re.compile(r"^[a-z][a-z0-9_-]{0,63}$")


class StageStatus(str, Enum):
    PENDING = "PENDING"
    RUNNING = "RUNNING"
    SUCCEEDED = "SUCCEEDED"
    DEGRADED = "DEGRADED"
    FAILED = "FAILED"
    SKIPPED = "SKIPPED"


def _validate_stage(stage: str) -> str:
    if not _STAGE_RE.fullmatch(stage):
        raise ValueError(f"invalid stage name: {stage!r}")
    return stage


@dataclass(frozen=True)
class StageResult:
    schema_version: int
    stage: str
    analysis_date: str
    status: str
    artifacts: list[str]
    metrics: dict
    warnings: list[str]
    error: str | None
    contract_hash: str | None
    recorded_at: str
    result_hash: str

    def _hash_payload(self) -> dict:
        payload = asdict(self)
        payload.pop("result_hash")
        return payload

    def semantic_payload(self) -> dict:
        payload = self._hash_payload()
        payload.pop("recorded_at")
        return payload

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def build(
        cls,
        *,
        stage: str,
        analysis_date: str,
        status: StageStatus | str,
        artifacts: list[str],
        metrics: dict,
        warnings: list[str],
        error: str | None,
        contract_hash: str | None,
        now: datetime | None = None,
    ) -> "StageResult":
        stage = _validate_stage(stage)
        status_value = StageStatus(status).value
        stamp = now or datetime.now(timezone.utc)
        if stamp.tzinfo is None:
            stamp = stamp.replace(tzinfo=timezone.utc)
        stamp = stamp.astimezone(timezone.utc)
        base = cls(
            schema_version=STAGE_RESULT_SCHEMA_VERSION,
            stage=stage,
            analysis_date=analysis_date,
            status=status_value,
            artifacts=list(dict.fromkeys(str(v) for v in artifacts)),
            metrics=json.loads(canonical_json(metrics)),
            warnings=[str(v) for v in warnings],
            error=None if error is None else str(error),
            contract_hash=contract_hash,
            recorded_at=stamp.isoformat(timespec="microseconds").replace("+00:00", "Z"),
            result_hash="",
        )
        return replace(base, result_hash=sha256_json(base._hash_payload()))

    @classmethod
    def from_dict(cls, raw: dict) -> "StageResult":
        result = cls(**raw)
        _validate_stage(result.stage)
        StageStatus(result.status)
        if result.schema_version != STAGE_RESULT_SCHEMA_VERSION:
            raise ValueError(
                f"unsupported stage result schema_version={result.schema_version}"
            )
        if result.result_hash != sha256_json(result._hash_payload()):
            raise ValueError("stage result result_hash mismatch")
        return result


def stage_result_path(scan_dir: Path | str, stage: str) -> Path:
    return Path(scan_dir) / "stage_results" / f"{_validate_stage(stage)}.json"


def contract_hash_for(scan_dir: Path | str) -> str | None:
    path = Path(scan_dir) / "run_contract.json"
    if not path.exists():
        return None
    try:
        return load_run_contract(path).contract_hash
    except (OSError, TypeError, ValueError, json.JSONDecodeError):
        return None


def load_stage_result(path: Path | str) -> StageResult:
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError("stage result root must be an object")
    return StageResult.from_dict(raw)


def write_stage_result(scan_dir: Path | str, result: StageResult) -> Path:
    target = stage_result_path(scan_dir, result.stage)
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists():
        try:
            existing = load_stage_result(target)
            if existing.semantic_payload() == result.semantic_payload():
                return target
        except (OSError, TypeError, ValueError, json.JSONDecodeError):
            pass
    temp = target.with_name(f"{target.name}.tmp")
    temp.write_text(
        json.dumps(result.to_dict(), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    temp.replace(target)
    return target


def record_stage_result(
    scan_dir: Path | str,
    *,
    stage: str,
    status: StageStatus | str,
    artifacts: list[str],
    metrics: dict,
    warnings: list[str],
    error: str | None,
    now: datetime | None = None,
) -> Path:
    scan = Path(scan_dir)
    result = StageResult.build(
        stage=stage,
        analysis_date=scan.name,
        status=status,
        artifacts=artifacts,
        metrics=metrics,
        warnings=warnings,
        error=error,
        contract_hash=contract_hash_for(scan),
        now=now,
    )
    return write_stage_result(scan, result)


def safe_record_stage_result(scan_dir: Path | str, **kwargs) -> Path | None:
    try:
        return record_stage_result(scan_dir, **kwargs)
    except Exception as exc:
        print(f"[stage_result] {kwargs.get('stage', '?')} 写入失败: {exc}", file=sys.stderr)
        return None
```

- [x] **Step 4: Verify GREEN**

Run:

```bash
uv run --no-sync python -m pytest tests/scan/test_stage_result.py -q
```

Expected: `9 passed`.

- [x] **Step 5: Commit**

```bash
git add autoresearch/scan/stage_result.py tests/scan/test_stage_result.py
git commit -m "feat(scan): add stage result snapshots"
```

### Task 2: Gate CLI dual-write

**Files:**

- Modify: `tests/scan/test_gates.py`
- Modify: `autoresearch/scan/gates.py:115-132`

- [x] **Step 1: Add failing CLI compatibility tests**

Append:

```python
def test_gate_cli_writes_success_stage_result_without_changing_stdout(tmp_path, monkeypatch, capsys):
    d = tmp_path / "context" / "scan" / "2026-07-28"
    d.mkdir(parents=True)
    pd.DataFrame({"code": ["000001"]}).to_csv(d / "finalists.csv", index=False)
    monkeypatch.chdir(tmp_path)
    from autoresearch.scan.gates import main
    from autoresearch.scan.stage_result import load_stage_result

    rc = main(["gate2", "2026-07-28", "--budget", "10"])
    legacy = json.loads(capsys.readouterr().out)
    result = load_stage_result(d / "stage_results" / "gate2.json")

    assert rc == 0
    assert legacy["ok"] is True and legacy["finalists"] == ["000001"]
    assert "status" not in legacy
    assert result.status == "SUCCEEDED"
    assert result.artifacts == ["finalists"]
    assert result.metrics == {"budget": 10, "n": 1}
    assert result.error is None


def test_gate_cli_writes_failed_stage_result_and_keeps_rc_one(tmp_path, monkeypatch, capsys):
    d = tmp_path / "context" / "scan" / "2026-07-28"
    d.mkdir(parents=True)
    monkeypatch.chdir(tmp_path)
    from autoresearch.scan.gates import main
    from autoresearch.scan.stage_result import load_stage_result

    rc = main(["gate1", "2026-07-28"])
    legacy = json.loads(capsys.readouterr().out)
    result = load_stage_result(d / "stage_results" / "gate1.json")

    assert rc == 1 and legacy["ok"] is False
    assert result.status == "FAILED"
    assert result.artifacts == []
    assert result.error == legacy["reason"]
```

- [x] **Step 2: Verify RED**

Run:

```bash
uv run --no-sync python -m pytest \
  tests/scan/test_gates.py::test_gate_cli_writes_success_stage_result_without_changing_stdout \
  tests/scan/test_gates.py::test_gate_cli_writes_failed_stage_result_and_keeps_rc_one -q
```

Expected: FAIL because `stage_results/gate*.json` is absent.

- [x] **Step 3: Add a finite gate adapter and call it from main**

Add before `main()`:

```python
def record_gate_stage_result(scan_dir: Path, result: dict, *, budget: int | None = None):
    from autoresearch.scan.stage_result import safe_record_stage_result

    gate = str(result.get("gate", ""))
    artifact_map = {
        "gate1": ("l2", "L2_gbdt_top200.csv"),
        "gate2": ("finalists", "finalists.csv"),
        "gate4": ("gate_fires", "gate_fires.csv"),
    }
    artifact_name, filename = artifact_map[gate]
    artifacts = [artifact_name] if (Path(scan_dir) / filename).exists() else []
    if gate == "gate1":
        metrics = {
            key: result[key]
            for key in ("sentinel_level", "l4_budget", "l2_n")
            if key in result
        }
    elif gate == "gate2":
        metrics = {"budget": int(budget if budget is not None else 30)}
        if "n" in result:
            metrics["n"] = int(result["n"])
    else:
        metrics = {"n_checks": int(result["n_checks"])} if "n_checks" in result else {}
    return safe_record_stage_result(
        scan_dir,
        stage=gate,
        status="SUCCEEDED" if result.get("ok") else "FAILED",
        artifacts=artifacts,
        metrics=metrics,
        warnings=[],
        error=None if result.get("ok") else str(result.get("reason", "gate failed")),
    )
```

In `main()`, after computing `res` and before printing:

```python
    record_gate_stage_result(
        scan_dir,
        res,
        budget=a.budget if a.gate == "gate2" else None,
    )
```

- [x] **Step 4: Run gate regression**

Run:

```bash
uv run --no-sync python -m pytest tests/scan/test_gates.py -q
```

Expected: all tests pass; pure `gate2()` read-only test remains green.

- [x] **Step 5: Commit**

```bash
git add autoresearch/scan/gates.py tests/scan/test_gates.py
git commit -m "feat(scan): shadow-write gate stage results"
```

### Task 3: Frame and prelude stage snapshots

**Files:**

- Modify: `tests/scan/test_frame_json_clean.py`
- Modify: `tests/scan/test_prelude.py`
- Modify: `autoresearch/scan/frame.py`
- Modify: `autoresearch/scan/prelude.py`

- [x] **Step 1: Add a failing frame assertion**

Extend `test_json_mode_writes_contract_and_short_ref`:

```python
    from autoresearch.scan.stage_result import load_stage_result
    stage = load_stage_result(
        tmp_path / "context" / "scan" / DATE / "stage_results" / "frame.json"
    )
    assert stage.status == "SUCCEEDED"
    assert stage.artifacts == ["run_contract"]
    assert stage.contract_hash == contract["contract_hash"]
    assert stage.metrics["frame_rows"] == 1
    assert stage.metrics["sentinel_level"] == "full"
```

- [x] **Step 2: Add failing prelude success/degraded tests**

Append to `tests/scan/test_prelude.py`:

```python
def test_run_prelude_writes_succeeded_stage_result(tmp_path, monkeypatch):
    from autoresearch.scan.prelude import run_prelude
    from autoresearch.scan.stage_result import load_stage_result

    monkeypatch.chdir(tmp_path)
    results = run_prelude("2026-07-28", skip=(
        "retro_refresh", "retro_pending", "t1_pending", "learning_health",
        "consensus", "temperature", "universe", "calendar", "catalyst",
        "menu", "ledgers", "dossier_pool",
    ))
    stage = load_stage_result(
        tmp_path / "context" / "scan" / "2026-07-28" / "stage_results" / "prelude.json"
    )
    assert results == []
    assert stage.status == "SUCCEEDED"
    assert stage.metrics == {"n_failed": 0, "n_steps": 0}
    assert stage.warnings == []


def test_run_prelude_writes_degraded_stage_result(tmp_path, monkeypatch):
    from autoresearch.scan import prelude
    from autoresearch.scan.stage_result import load_stage_result

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(prelude, "_run_steps", lambda steps: [
        {"step": "universe", "ok": False, "note": "RuntimeError: boom"},
        {"step": "calendar", "ok": True, "note": "ok"},
    ])
    prelude.run_prelude("2026-07-28", skip=())
    stage = load_stage_result(
        tmp_path / "context" / "scan" / "2026-07-28" / "stage_results" / "prelude.json"
    )
    assert stage.status == "DEGRADED"
    assert stage.metrics == {"n_failed": 1, "n_steps": 2}
    assert stage.warnings == ["universe: RuntimeError: boom"]
```

- [x] **Step 3: Verify RED**

Run:

```bash
uv run --no-sync python -m pytest \
  tests/scan/test_frame_json_clean.py::test_json_mode_writes_contract_and_short_ref \
  tests/scan/test_prelude.py::test_run_prelude_writes_succeeded_stage_result \
  tests/scan/test_prelude.py::test_run_prelude_writes_degraded_stage_result -q
```

Expected: FAIL because frame/prelude result files are absent.

- [x] **Step 4: Record frame after the contract exists**

In `frame.main()`, after `write_run_contract(...)` and `user_config_echo.json`:

```python
        from autoresearch.scan.stage_result import safe_record_stage_result
        safe_record_stage_result(
            echo_dir,
            stage="frame",
            status="SUCCEEDED",
            artifacts=["run_contract"],
            metrics={
                "frame_rows": int(counts["after_gate_a"]),
                "l0_rows": int(counts["universe"]),
                "regime": reg.get("label"),
                "sentinel_level": level,
            },
            warnings=[],
            error=None,
        )
```

- [x] **Step 5: Record prelude after the summary file**

At the end of `run_prelude()`, before `return results`:

```python
    from autoresearch.scan.stage_result import safe_record_stage_result

    failed = [r for r in results if not r["ok"]]
    artifact_candidates = (
        ("l1_full", "L1_scored_full.csv"),
        ("l1_recall", "L1_recall_top1000.csv"),
        ("l2", "L2_gbdt_top200.csv"),
    )
    safe_record_stage_result(
        scan_dir,
        stage="prelude",
        status="DEGRADED" if failed else "SUCCEEDED",
        artifacts=[
            name for name, filename in artifact_candidates
            if (scan_dir / filename).exists()
        ],
        metrics={"n_steps": len(results), "n_failed": len(failed)},
        warnings=[f"{r['step']}: {r['note']}" for r in failed],
        error=None,
    )
```

- [x] **Step 6: Run frame/prelude regressions**

Run:

```bash
uv run --no-sync python -m pytest \
  tests/scan/test_frame_json_clean.py \
  tests/scan/test_prelude.py \
  tests/scan/test_prelude_t0.py \
  tests/scan/test_prelude_summary.py -q
```

Expected: all tests pass.

- [x] **Step 7: Commit**

```bash
git add \
  autoresearch/scan/frame.py \
  autoresearch/scan/prelude.py \
  tests/scan/test_frame_json_clean.py \
  tests/scan/test_prelude.py
git commit -m "feat(scan): record frame and prelude results"
```

### Task 4: ArtifactIndex and health visibility

**Files:**

- Modify: `tests/scan/test_artifacts.py`
- Modify: `tests/scan/test_health.py`
- Modify: `autoresearch/scan/artifacts.py`
- Modify: `autoresearch/scan/health.py`

- [x] **Step 1: Add a failing ArtifactIndex collection test**

In `test_index_hashes_scan_and_report_artifacts`, create:

```python
    (scan / "stage_results").mkdir()
    (scan / "stage_results" / "gate1.json").write_text(
        '{"stage":"gate1"}',
        encoding="utf-8",
    )
```

Add:

```python
    assert rows["stage_results"]["status"] == "PRESENT"
    assert len(rows["stage_results"]["content_hash"]) == 64
```

- [x] **Step 2: Add failing health tests**

Append:

```python
def test_run_health_stage_results_absent_is_advisory(tmp_path):
    d = _mk_day(tmp_path, "2026-07-28")
    result = run_health(d)["stage_results"]
    assert result == {
        "status": "ABSENT",
        "counts": {},
        "failed": [],
        "degraded": [],
        "skipped": [],
        "invalid_files": [],
        "contract_hash_mismatches": [],
    }


def test_run_health_summarizes_valid_stage_results(tmp_path):
    from autoresearch.scan.stage_result import record_stage_result

    d = _mk_day(tmp_path, "2026-07-28")
    record_stage_result(
        d, stage="prelude", status="DEGRADED", artifacts=[], metrics={},
        warnings=["universe: boom"], error=None,
    )
    record_stage_result(
        d, stage="gate1", status="FAILED", artifacts=[], metrics={},
        warnings=[], error="L2 missing",
    )
    result = run_health(d)["stage_results"]
    assert result["status"] == "OK"
    assert result["counts"] == {"DEGRADED": 1, "FAILED": 1}
    assert result["failed"] == ["gate1"]
    assert result["degraded"] == ["prelude"]


def test_run_health_flags_stage_contract_mismatch_and_corruption(tmp_path):
    from autoresearch.scan.stage_result import record_stage_result

    d = _mk_day(tmp_path, "2026-07-28")
    _write_contract(d)
    record_stage_result(
        d, stage="gate1", status="SUCCEEDED", artifacts=[], metrics={},
        warnings=[], error=None,
    )
    path = d / "stage_results" / "gate1.json"
    raw = json.loads(path.read_text(encoding="utf-8"))
    raw["contract_hash"] = "b" * 64
    path.write_text(json.dumps(raw), encoding="utf-8")
    (d / "stage_results" / "broken.json").write_text("{", encoding="utf-8")

    result = run_health(d)["stage_results"]
    assert result["status"] == "INVALID"
    assert result["invalid_files"] == ["broken.json", "gate1.json"]
```

- [x] **Step 3: Verify RED**

Run:

```bash
uv run --no-sync python -m pytest \
  tests/scan/test_artifacts.py::test_index_hashes_scan_and_report_artifacts \
  tests/scan/test_health.py::test_run_health_stage_results_absent_is_advisory \
  tests/scan/test_health.py::test_run_health_summarizes_valid_stage_results \
  tests/scan/test_health.py::test_run_health_flags_stage_contract_mismatch_and_corruption -q
```

Expected: FAIL because registry/health fields are absent.

- [x] **Step 4: Register StageResult collection**

Add to `CRITICAL_ARTIFACTS` in `artifacts.py`:

```python
    ArtifactSpec("stage_results", 1, "control_plane", "stage_results/*.json"),
```

- [x] **Step 5: Implement health aggregation**

Add before `run_health()`:

```python
def stage_results_health(scan_dir: Path) -> dict:
    from collections import Counter

    from autoresearch.scan.stage_result import load_stage_result

    scan = Path(scan_dir)
    directory = scan / "stage_results"
    empty = {
        "status": "ABSENT",
        "counts": {},
        "failed": [],
        "degraded": [],
        "skipped": [],
        "invalid_files": [],
        "contract_hash_mismatches": [],
    }
    paths = sorted(directory.glob("*.json")) if directory.is_dir() else []
    if not paths:
        return empty
    contract = run_contract_health(scan)
    expected_hash = (
        contract["contract_hash"]
        if contract["status"] in {"OK", "INVALID"} and contract["contract_hash"]
        else None
    )
    results = []
    invalid = []
    mismatches = []
    for path in paths:
        try:
            result = load_stage_result(path)
            results.append(result)
            if expected_hash is not None and result.contract_hash != expected_hash:
                mismatches.append(result.stage)
        except (OSError, TypeError, ValueError, json.JSONDecodeError):
            invalid.append(path.name)
    counts = Counter(result.status for result in results)
    return {
        "status": "INVALID" if invalid or mismatches else "OK",
        "counts": dict(sorted(counts.items())),
        "failed": sorted(r.stage for r in results if r.status == "FAILED"),
        "degraded": sorted(r.stage for r in results if r.status == "DEGRADED"),
        "skipped": sorted(r.stage for r in results if r.status == "SKIPPED"),
        "invalid_files": sorted(invalid),
        "contract_hash_mismatches": sorted(mismatches),
    }
```

Add to `run_health()`:

```python
            "stage_results": stage_results_health(scan_dir),
```

- [x] **Step 6: Correct the corruption test to separate integrity and contract mismatch**

Replace the body after the valid `gate1` write with:

```python
    from autoresearch.scan.stage_result import StageResult, write_stage_result

    mismatch = StageResult.build(
        stage="gate2",
        analysis_date=d.name,
        status="SUCCEEDED",
        artifacts=[],
        metrics={},
        warnings=[],
        error=None,
        contract_hash="b" * 64,
    )
    write_stage_result(d, mismatch)
    (d / "stage_results" / "broken.json").write_text("{", encoding="utf-8")

    result = run_health(d)["stage_results"]
    assert result["status"] == "INVALID"
    assert result["invalid_files"] == ["broken.json"]
    assert result["contract_hash_mismatches"] == ["gate2"]
```

- [x] **Step 7: Run artifact and health regressions**

Run:

```bash
uv run --no-sync python -m pytest \
  tests/scan/test_artifacts.py \
  tests/scan/test_health.py -q
```

Expected: all tests pass.

- [x] **Step 8: Commit**

```bash
git add \
  autoresearch/scan/artifacts.py \
  autoresearch/scan/health.py \
  tests/scan/test_artifacts.py \
  tests/scan/test_health.py
git commit -m "feat(scan): expose stage result health"
```

### Task 5: Assemble final snapshots and trace publication

**Files:**

- Modify: `tests/scan/test_assemble.py`
- Modify: `autoresearch/scan/assemble.py`

- [x] **Step 1: Add failing finalization tests**

Append:

```python
def test_assemble_records_final_stage_results(published):
    from autoresearch.scan.stage_result import load_stage_result

    stage_dir = published["scan_dir"] / "stage_results"
    assemble_result = load_stage_result(stage_dir / "assemble.json")
    gate4_result = load_stage_result(stage_dir / "gate4.json")
    assert assemble_result.status == "SUCCEEDED"
    assert assemble_result.artifacts == [
        "final_ratings", "gate_fires", "run_health", "summary", "manifest",
    ]
    assert assemble_result.metrics["n_cards"] == 3
    assert gate4_result.status == "FAILED"
    assert gate4_result.artifacts == ["gate_fires"]
    assert "覆盖率不足" in gate4_result.error


def test_stage_results_are_published_and_indexed(published):
    stage_trace = published["trace"] / "stage_results"
    assert (stage_trace / "assemble.json").exists()
    assert (stage_trace / "gate4.json").exists()
    index = json.loads(
        (published["scan_dir"] / "artifact_index.json").read_text(encoding="utf-8")
    )
    rows = {row["name"]: row for row in index["artifacts"]}
    assert rows["stage_results"]["status"] == "PRESENT"
    assert len(rows["stage_results"]["content_hash"]) == 64
    health = json.loads(
        (published["scan_dir"] / "run_health.json").read_text(encoding="utf-8")
    )
    assert health["stage_results"]["status"] == "OK"
    assert health["stage_results"]["counts"] == {"FAILED": 1, "SUCCEEDED": 1}
    assert health["stage_results"]["failed"] == ["gate4"]
```

- [x] **Step 2: Verify RED**

Run:

```bash
uv run --no-sync python -m pytest \
  tests/scan/test_assemble.py::test_assemble_records_final_stage_results \
  tests/scan/test_assemble.py::test_stage_results_are_published_and_indexed -q
```

Expected: FAIL because assemble/gate4 snapshots are absent.

- [x] **Step 3: Record assemble and shadow gate4 before final health**

After `summary_path.write_text(...)` and before the second `_health.write_run_health(...)`:

```python
    from autoresearch.scan.stage_result import safe_record_stage_result

    safe_record_stage_result(
        scan_dir,
        stage="assemble",
        status="SUCCEEDED",
        artifacts=["final_ratings", "gate_fires", "run_health", "summary", "manifest"],
        metrics={"n_cards": n_cards, "n_trace_before_final": n_pipe},
        warnings=[],
        error=None,
    )
    with contextlib.suppress(Exception):
        from autoresearch.scan.gates import gate4, record_gate_stage_result

        record_gate_stage_result(scan_dir, gate4(scan_dir))
```

The existing final health write must remain after these calls.

- [x] **Step 4: Publish the stage result directory before ArtifactIndex copy**

Inside the existing final ArtifactIndex block, before `write_artifact_index(...)`:

```python
        stage_source = scan_dir / "stage_results"
        stage_trace = out_base / "trace" / "stage_results"
        if stage_source.is_dir():
            stage_trace.mkdir(parents=True, exist_ok=True)
            for stage_file in sorted(stage_source.glob("*.json")):
                shutil.copy2(stage_file, stage_trace / stage_file.name)
                n_pipe += 1
```

- [x] **Step 5: Run assemble/gate regressions**

Run:

```bash
uv run --no-sync python -m pytest \
  tests/scan/test_assemble.py \
  tests/scan/test_gates.py \
  tests/scan/test_health.py \
  tests/scan/test_artifacts.py -q
```

Expected: all tests pass.

- [x] **Step 6: Commit**

```bash
git add autoresearch/scan/assemble.py tests/scan/test_assemble.py
git commit -m "feat(scan): finalize and publish stage results"
```

### Task 6: Documentation and full verification

**Files:**

- Modify: `docs/specs/2026-07-28-scan-market-unified-optimization-master-design.md`
- Modify: `docs/plans/2026-07-28-stage-result-control-plane-plan.md`

- [x] **Step 1: Record Wave 1 second-batch status**

Add under the existing first-batch status:

```markdown
#### 2026-07-28 第二批实现状态

已进入影子双写：

- `frame / prelude / gate1 / gate2 / assemble / gate4` 写统一 StageResult；
- gate CLI 的旧 JSON 与退出码保持兼容；
- 相同语义的重复结果逐字节幂等；
- health 分列结果完整性与 `FAILED / DEGRADED / SKIPPED`；
- StageResult collection 进入 ArtifactIndex 并随 trace 发布。

仍待下一批：

- Workflow 直接以 StageResult 作为分支输入；
- L3/L4 单票级 StageResult；
- `DecisionRecord / outbox`。
```

将第一批状态中的“15 类关键产物”改为“首批 15 类关键产物”，并注明本批加入
`stage_results` 后注册表为 16 类。

- [x] **Step 2: Compile touched modules**

Run:

```bash
uv run --no-sync python -m compileall -q \
  autoresearch/scan/stage_result.py \
  autoresearch/scan/gates.py \
  autoresearch/scan/frame.py \
  autoresearch/scan/prelude.py \
  autoresearch/scan/artifacts.py \
  autoresearch/scan/health.py \
  autoresearch/scan/assemble.py
```

Expected: exit 0, no output.

- [x] **Step 3: Run focused regression**

Run:

```bash
uv run --no-sync python -m pytest \
  tests/scan/test_stage_result.py \
  tests/scan/test_gates.py \
  tests/scan/test_frame_json_clean.py \
  tests/scan/test_prelude.py \
  tests/scan/test_prelude_t0.py \
  tests/scan/test_prelude_summary.py \
  tests/scan/test_artifacts.py \
  tests/scan/test_health.py \
  tests/scan/test_assemble.py -q
```

Expected: all selected tests pass.

- [x] **Step 4: Run full regression**

Run:

```bash
uv run --no-sync python -m pytest -q
```

Expected: all tests pass.

- [x] **Step 5: Verify selection and rating files are untouched**

Run:

```bash
git diff -- \
  autoresearch/scan/agents/l3_select.py \
  autoresearch/scan/l4_card.py \
  autoresearch/agents/utils/rating.py
```

Expected: no output.

- [x] **Step 6: Verify plan quality and working tree**

Run:

```bash
rg -n "T""BD|T""ODO|implement la""ter|fill in det""ails|appropriate er""ror|Write tests f""or|Similar t""o" \
  docs/plans/2026-07-28-stage-result-control-plane-plan.md
git diff --check
git status --short
```

Expected: the placeholder scan has no output; diff check exits 0; status contains only planned files.

- [x] **Step 7: Mark completed checkboxes and commit docs**

```bash
git add \
  docs/specs/2026-07-28-scan-market-unified-optimization-master-design.md \
  docs/plans/2026-07-28-stage-result-control-plane-plan.md
git commit -m "docs(scan): record stage result control plane"
```

## Acceptance criteria

- All six legal statuses are validated by one enum.
- Unsafe stage names cannot escape `stage_results/`.
- Every snapshot has an integrity hash and optional RunContract binding.
- Equivalent repeated writes preserve bytes and timestamp.
- Gate pure functions remain read-only.
- Gate CLI legacy stdout and exit codes remain unchanged.
- Frame and prelude record success/degradation without affecting their outputs.
- Assemble records both its own result and the shadow gate4 result before final health/index.
- Health separates invalid control-plane files from valid business failures.
- ArtifactIndex registers 16 critical artifact types including StageResult collection.
- Trace publishes the same StageResult bytes indexed in staging.
- Full test suite passes and selection/rating source files are untouched.
