# RunContract and ArtifactIndex Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在不改变召回、精排、评级和 BUY 产出的前提下，为每次 scan 建立唯一运行契约，并为关键生产产物生成可校验、可发布的索引。

**Architecture:** 新增一个小型 `run_contract.py` 作为运行身份单一事实源，现有 `frame --json` 在最早阶段写出契约并仅向 market pack 暴露短引用。扩展现有 `artifacts.py` 为静态关键产物注册表和内容指纹器，由 `assemble` 在所有最终文件落盘后生成索引；`health` 只做影子校验并报告不一致，本批不把新控制面升级成阻断门。

**Tech Stack:** Python 3.12、标准库 `dataclasses/hashlib/json/subprocess/datetime/pathlib`、pandas、pytest。

---

## Scope and invariants

本计划只覆盖总纲 Wave 1 的前两个独立交付物：

1. `RunContract`
2. `ArtifactIndex`

以下能力不进入本批：

- `StageResult`
- `DecisionRecord`
- post-run outbox
- Workflow hash 硬门
- L3/L4 业务逻辑调整
- 门槛、rubric、评级或 BUY 数量调整

必须保持的行为：

- 0 BUY 合法，不设 BUY 配额。
- `frame --json` 的 stdout 仍是一份纯 JSON。
- 缺少新控制面文件时，历史 scan 和合成测试仍能发布。
- 新校验先以 `ABSENT / OK / INVALID` 影子状态进入 `run_health.json`，不阻断生产。
- `input_hash` 未由生产者证明时写 `null`，不伪造血缘关系。

## File map

- Create: `autoresearch/scan/run_contract.py`
  - 构建、序列化、校验和原子写入运行契约。
- Modify: `autoresearch/scan/artifacts.py`
  - 保留 `read_finalists()`；新增关键产物注册表、内容哈希和索引写出。
- Modify: `autoresearch/scan/frame.py`
  - 最早阶段写 `run_contract.json`，并向 market pack 加入短引用。
- Modify: `autoresearch/scan/health.py`
  - 加入契约完整性与配置一致性影子检查。
- Modify: `autoresearch/scan/assemble.py`
  - manifest 固化 contract 身份；最终写出并发布 artifact index。
- Create: `tests/scan/test_run_contract.py`
  - 契约规范化、哈希、篡改拒绝、原子写入测试。
- Create: `tests/scan/test_artifacts.py`
  - 单文件、集合、缺失、空文件和 report 产物索引测试。
- Modify: `tests/scan/test_frame_json_clean.py`
  - 锁定纯 stdout 和契约双写。
- Modify: `tests/scan/test_health.py`
  - 锁定 `ABSENT / OK / INVALID` 三态。
- Modify: `tests/scan/test_assemble.py`
  - 锁定 manifest、trace 和最终 artifact index 接线。
- Modify: `docs/specs/2026-07-28-scan-market-unified-optimization-master-design.md`
  - 记录 Wave 1 第一批的实际落地边界和影子态。

执行拓扑说明：Task 2 的 frame 接线读取 `artifact_schema_versions()`，因此实际 TDD 顺序为
Task 1 → Task 3 → Task 2 → Task 4 → Task 5 → Task 6；任务编号保留设计阅读顺序。

### Task 1: RunContract domain object

**Files:**

- Create: `tests/scan/test_run_contract.py`
- Create: `autoresearch/scan/run_contract.py`

- [x] **Step 1: Write canonicalization, identity, persistence, and tamper tests**

```python
from __future__ import annotations

import json
from datetime import datetime, timezone

import pytest

from autoresearch.scan.run_contract import (
    RunContract,
    load_run_contract,
    write_run_contract,
)

DATE = "2026-07-28"
NOW = datetime(2026, 7, 28, 12, 34, 56, 123456, tzinfo=timezone.utc)


def _build(user_config: dict | None = None) -> RunContract:
    return RunContract.build(
        analysis_date=DATE,
        user_config=user_config or {},
        pinned={"kept": [], "expired": []},
        data_policy={"source": "tushare", "cap_floor_yi": 30.0, "include_bj": True},
        stage_budgets={"l3_finalist_max": 10, "pinned_cap": 5, "pinned_ttl_days": 10},
        artifact_schema_versions={"market_pack": 1, "finalists": 1},
        git_sha="abc1234",
        now=NOW,
    )


def test_config_hash_is_canonical_but_contract_identity_is_explicit():
    left = _build({"agents": {"l4_card": {"effort": "high"}}, "redteam_prob": 0.1})
    right = _build({"redteam_prob": 0.1, "agents": {"l4_card": {"effort": "high"}}})
    assert left.config_hash == right.config_hash
    assert left.contract_hash == right.contract_hash
    assert left.run_id == "20260728T123456123456Z"


def test_contract_hash_covers_pinned_and_data_policy():
    base = _build()
    changed = RunContract.build(
        analysis_date=DATE,
        user_config={},
        pinned={"kept": [{"code": "000001"}], "expired": []},
        data_policy={"source": "tushare", "cap_floor_yi": 30.0, "include_bj": True},
        stage_budgets={"l3_finalist_max": 10, "pinned_cap": 5, "pinned_ttl_days": 10},
        artifact_schema_versions={"market_pack": 1, "finalists": 1},
        git_sha="abc1234",
        now=NOW,
    )
    assert changed.contract_hash != base.contract_hash


def test_write_and_load_round_trip(tmp_path):
    path = tmp_path / "run_contract.json"
    written = write_run_contract(path, _build())
    assert written == path
    loaded = load_run_contract(path)
    assert loaded.to_dict() == _build().to_dict()
    assert not (tmp_path / "run_contract.json.tmp").exists()


def test_load_rejects_tampered_contract(tmp_path):
    path = write_run_contract(tmp_path / "run_contract.json", _build())
    raw = json.loads(path.read_text(encoding="utf-8"))
    raw["data_policy"]["source"] = "em"
    path.write_text(json.dumps(raw), encoding="utf-8")
    with pytest.raises(ValueError, match="contract_hash"):
        load_run_contract(path)
```

- [x] **Step 2: Run the tests and verify the module is missing**

Run:

```bash
uv run --no-sync python -m pytest tests/scan/test_run_contract.py -q
```

Expected: collection fails with `ModuleNotFoundError: No module named 'autoresearch.scan.run_contract'`.

- [x] **Step 3: Implement the immutable contract and canonical hashes**

Create `autoresearch/scan/run_contract.py` with this complete interface:

```python
#!/usr/bin/env python3
"""scan-market 的运行身份契约；只记录事实，不承载流水线业务规则。"""
from __future__ import annotations

import hashlib
import json
import subprocess
from dataclasses import asdict, dataclass, replace
from datetime import datetime, timezone
from pathlib import Path

RUN_CONTRACT_SCHEMA_VERSION = 1


def canonical_json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def sha256_json(value: object) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def resolve_git_sha(repo_root: Path | str = ".") -> str:
    try:
        proc = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=Path(repo_root),
            check=True,
            capture_output=True,
            text=True,
        )
        return proc.stdout.strip() or "unknown"
    except (OSError, subprocess.CalledProcessError):
        return "unknown"


@dataclass(frozen=True)
class RunContract:
    schema_version: int
    analysis_date: str
    run_id: str
    created_at: str
    git_sha: str
    user_config: dict
    config_hash: str
    agents: dict
    pinned: dict
    data_policy: dict
    stage_budgets: dict
    artifact_schema_versions: dict[str, int]
    contract_hash: str

    def _hash_payload(self) -> dict:
        payload = asdict(self)
        payload.pop("contract_hash")
        return payload

    def to_dict(self) -> dict:
        return asdict(self)

    def short_ref(self) -> dict:
        return {
            "schema_version": self.schema_version,
            "run_id": self.run_id,
            "contract_hash": self.contract_hash,
            "config_hash": self.config_hash,
        }

    @classmethod
    def build(
        cls,
        *,
        analysis_date: str,
        user_config: dict,
        pinned: dict,
        data_policy: dict,
        stage_budgets: dict,
        artifact_schema_versions: dict[str, int],
        git_sha: str | None = None,
        now: datetime | None = None,
        repo_root: Path | str = ".",
    ) -> "RunContract":
        stamp = now or datetime.now(timezone.utc)
        if stamp.tzinfo is None:
            stamp = stamp.replace(tzinfo=timezone.utc)
        stamp = stamp.astimezone(timezone.utc)
        created_at = stamp.isoformat(timespec="microseconds").replace("+00:00", "Z")
        run_id = stamp.strftime("%Y%m%dT%H%M%S%fZ")
        normalized_config = json.loads(canonical_json(user_config))
        base = cls(
            schema_version=RUN_CONTRACT_SCHEMA_VERSION,
            analysis_date=analysis_date,
            run_id=run_id,
            created_at=created_at,
            git_sha=git_sha if git_sha is not None else resolve_git_sha(repo_root),
            user_config=normalized_config,
            config_hash=sha256_json(normalized_config),
            agents=normalized_config.get("agents") or {},
            pinned=json.loads(canonical_json(pinned)),
            data_policy=json.loads(canonical_json(data_policy)),
            stage_budgets=json.loads(canonical_json(stage_budgets)),
            artifact_schema_versions=dict(sorted(artifact_schema_versions.items())),
            contract_hash="",
        )
        return replace(base, contract_hash=sha256_json(base._hash_payload()))

    @classmethod
    def from_dict(cls, raw: dict) -> "RunContract":
        contract = cls(**raw)
        if contract.schema_version != RUN_CONTRACT_SCHEMA_VERSION:
            raise ValueError(
                f"unsupported run contract schema_version={contract.schema_version}"
            )
        if contract.config_hash != sha256_json(contract.user_config):
            raise ValueError("run contract config_hash mismatch")
        if contract.contract_hash != sha256_json(contract._hash_payload()):
            raise ValueError("run contract contract_hash mismatch")
        return contract


def write_run_contract(path: Path | str, contract: RunContract) -> Path:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    temp = target.with_name(f"{target.name}.tmp")
    temp.write_text(
        json.dumps(contract.to_dict(), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    temp.replace(target)
    return target


def load_run_contract(path: Path | str) -> RunContract:
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError("run contract root must be an object")
    return RunContract.from_dict(raw)
```

- [x] **Step 4: Run the focused tests**

Run:

```bash
uv run --no-sync python -m pytest tests/scan/test_run_contract.py -q
```

Expected: `4 passed`.

- [x] **Step 5: Commit the domain object**

```bash
git add autoresearch/scan/run_contract.py tests/scan/test_run_contract.py
git commit -m "feat(scan): add immutable run contract"
```

### Task 2: Write RunContract at the frame boundary

**Files:**

- Modify: `tests/scan/test_frame_json_clean.py`
- Modify: `autoresearch/scan/frame.py:155-178`

- [x] **Step 1: Add a failing frame integration test**

Append:

```python
def test_json_mode_writes_contract_and_short_ref(monkeypatch, capsys, tmp_path):
    _patch_deps(monkeypatch, tmp_path)
    monkeypatch.setattr("autoresearch.scan.run_contract.resolve_git_sha", lambda root=".": "deadbeef")
    rc = frame.main([DATE, "--json"])
    payload = json.loads(capsys.readouterr().out)
    contract_path = tmp_path / "context" / "scan" / DATE / "run_contract.json"
    contract = json.loads(contract_path.read_text(encoding="utf-8"))

    assert rc == 0
    assert payload["run_contract"] == {
        "schema_version": 1,
        "run_id": contract["run_id"],
        "contract_hash": contract["contract_hash"],
        "config_hash": contract["config_hash"],
    }
    assert contract["analysis_date"] == DATE
    assert contract["git_sha"] == "deadbeef"
    assert contract["data_policy"] == {
        "source": "tushare",
        "cap_floor_yi": 30.0,
        "include_bj": True,
    }
    assert contract["stage_budgets"] == {
        "l3_finalist_max": 10,
        "pinned_cap": 5,
        "pinned_ttl_days": 10,
    }
```

- [x] **Step 2: Verify the new assertion fails**

Run:

```bash
uv run --no-sync python -m pytest tests/scan/test_frame_json_clean.py::test_json_mode_writes_contract_and_short_ref -q
```

Expected: FAIL because `run_contract.json` is absent.

- [x] **Step 3: Build and write the contract before emitting stdout**

Replace the current `if args.json:` block in `frame.main()` with:

```python
    if args.json:
        from autoresearch.scan.artifacts import artifact_schema_versions
        from autoresearch.scan.run_contract import RunContract, write_run_contract
        from autoresearch.scan.user_config import load_pinned, load_user_config

        user_cfg = load_user_config()
        pinned_cfg = user_cfg.get("pinned") or {}
        pinned_cap = int(pinned_cfg.get("cap", 5))
        pinned_ttl = int(pinned_cfg.get("ttl_days", 10))
        l3_cfg = user_cfg.get("l3") or {}
        pinned = load_pinned(
            analysis_date,
            cap=pinned_cap,
            ttl_days=pinned_ttl,
        )
        contract = RunContract.build(
            analysis_date=analysis_date,
            user_config=user_cfg,
            pinned=pinned,
            data_policy={
                "source": args.source,
                "cap_floor_yi": args.cap_floor,
                "include_bj": not args.exclude_bj,
            },
            stage_budgets={
                "l3_finalist_max": int(l3_cfg.get("finalist_max", 10)),
                "pinned_cap": pinned_cap,
                "pinned_ttl_days": pinned_ttl,
            },
            artifact_schema_versions=artifact_schema_versions(),
        )
        echo_dir = Path("context/scan") / analysis_date
        echo_dir.mkdir(parents=True, exist_ok=True)
        write_run_contract(echo_dir / "run_contract.json", contract)
        (echo_dir / "user_config_echo.json").write_text(
            json.dumps(user_cfg, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        print(json.dumps(
            {
                **pack,
                "macro_state": mstate,
                "macro_state_note": mnote,
                "user_config": user_cfg,
                "run_contract": contract.short_ref(),
            },
            ensure_ascii=False,
            indent=2,
        ))
```

- [x] **Step 4: Run frame regression tests**

Run:

```bash
uv run --no-sync python -m pytest \
  tests/scan/test_frame_json_clean.py \
  tests/scan/test_frame.py \
  tests/scan/test_user_config.py -q
```

Expected: all tests pass; stdout purity tests remain green.

- [x] **Step 5: Commit the frame boundary**

```bash
git add autoresearch/scan/frame.py tests/scan/test_frame_json_clean.py
git commit -m "feat(scan): emit run contract from frame"
```

### Task 3: Critical ArtifactIndex

**Files:**

- Create: `tests/scan/test_artifacts.py`
- Modify: `autoresearch/scan/artifacts.py`

- [x] **Step 1: Add failing tests for files, collections, missing and empty status**

```python
from __future__ import annotations

import json
from datetime import datetime, timezone

from autoresearch.scan.artifacts import build_artifact_index, write_artifact_index

NOW = datetime(2026, 7, 28, 14, 0, tzinfo=timezone.utc)


def _by_name(index: dict) -> dict[str, dict]:
    return {row["name"]: row for row in index["artifacts"]}


def test_index_hashes_scan_and_report_artifacts(tmp_path):
    scan = tmp_path / "context" / "scan" / "2026-07-28"
    report = tmp_path / "reports" / "scan" / "20260728_1400"
    (scan / "details").mkdir(parents=True)
    report.mkdir(parents=True)
    (scan / "market_pack.json").write_text('{"breadth":{}}', encoding="utf-8")
    (scan / "details" / "000001.md").write_text("# card", encoding="utf-8")
    (scan / "details" / "000002.md").write_text("# card 2", encoding="utf-8")
    (report / "summary.md").write_text("# summary", encoding="utf-8")
    (report / "manifest.json").write_text('{"analysis_date":"2026-07-28"}', encoding="utf-8")

    index = build_artifact_index(scan, report_dir=report, now=NOW)
    rows = _by_name(index)

    assert index["schema_version"] == 1
    assert index["analysis_date"] == "2026-07-28"
    assert rows["market_pack"]["status"] == "PRESENT"
    assert len(rows["market_pack"]["content_hash"]) == 64
    assert rows["l4_cards"]["status"] == "PRESENT"
    assert len(rows["l4_cards"]["content_hash"]) == 64
    assert rows["summary"]["status"] == "PRESENT"
    assert rows["finalists"]["status"] == "MISSING"
    assert rows["finalists"]["content_hash"] is None
    assert rows["market_pack"]["input_hash"] is None


def test_index_distinguishes_empty_from_missing(tmp_path):
    scan = tmp_path / "2026-07-28"
    scan.mkdir()
    (scan / "finalists.csv").write_bytes(b"")
    rows = _by_name(build_artifact_index(scan, now=NOW))
    assert rows["finalists"]["status"] == "EMPTY"
    assert rows["l3_judged"]["status"] == "MISSING"


def test_write_index_is_atomic_and_carries_contract_identity(tmp_path):
    scan = tmp_path / "2026-07-28"
    scan.mkdir()
    (scan / "run_contract.json").write_text(json.dumps({
        "run_id": "run-1",
        "contract_hash": "a" * 64,
    }), encoding="utf-8")
    path = write_artifact_index(scan, now=NOW)
    index = json.loads(path.read_text(encoding="utf-8"))
    assert path == scan / "artifact_index.json"
    assert index["run_id"] == "run-1"
    assert index["contract_hash"] == "a" * 64
    assert not (scan / "artifact_index.json.tmp").exists()
```

- [x] **Step 2: Verify imports fail**

Run:

```bash
uv run --no-sync python -m pytest tests/scan/test_artifacts.py -q
```

Expected: collection fails because `build_artifact_index` and `write_artifact_index` do not exist.

- [x] **Step 3: Extend artifacts.py with a finite registry and streaming hashes**

Keep the existing `read_finalists()` unchanged and add:

```python
import hashlib
import json
from dataclasses import asdict, dataclass
from datetime import datetime, timezone

ARTIFACT_INDEX_SCHEMA_VERSION = 1


@dataclass(frozen=True)
class ArtifactSpec:
    name: str
    schema_version: int
    producer: str
    path: str
    root: str = "scan"


CRITICAL_ARTIFACTS = (
    ArtifactSpec("run_contract", 1, "frame", "run_contract.json"),
    ArtifactSpec("market_pack", 1, "frame", "market_pack.json"),
    ArtifactSpec("l1_full", 1, "universe", "L1_scored_full.csv"),
    ArtifactSpec("l1_recall", 1, "universe", "L1_recall_top1000.csv"),
    ArtifactSpec("l2", 1, "l2_stratify", "L2_gbdt_top200.csv"),
    ArtifactSpec("l3_judged", 1, "l3_rank", "L3_judged_full.csv"),
    ArtifactSpec("finalists", 1, "l3_rank", "finalists.csv"),
    ArtifactSpec("l4_cards", 1, "l4_stock", "details/*.md"),
    ArtifactSpec("ensemble", 1, "l4_ensemble", "_ensemble_*.json"),
    ArtifactSpec("final_ratings", 1, "assemble", "_final_ratings.json"),
    ArtifactSpec("gate_fires", 1, "assemble", "gate_fires.csv"),
    ArtifactSpec("early_stop", 1, "assemble", "_early_stop.json"),
    ArtifactSpec("run_health", 1, "health", "run_health.json"),
    ArtifactSpec("summary", 1, "assemble", "summary.md", root="report"),
    ArtifactSpec("manifest", 1, "assemble", "manifest.json", root="report"),
)


def artifact_schema_versions() -> dict[str, int]:
    return {spec.name: spec.schema_version for spec in CRITICAL_ARTIFACTS}


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _canonical_hash(value: object) -> str:
    raw = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def _artifact_ref(spec: ArtifactSpec, root: Path | None) -> dict:
    base = root if root is not None else Path("__missing_artifact_root__")
    paths = sorted(p for p in base.glob(spec.path) if p.is_file()) if root is not None else []
    nonempty = [path for path in paths if path.stat().st_size > 0]
    status = "MISSING" if not paths else ("EMPTY" if not nonempty else "PRESENT")
    if len(paths) == 1:
        content_hash = _sha256_file(paths[0])
    elif paths:
        content_hash = _canonical_hash([
            {"path": path.relative_to(base).as_posix(), "content_hash": _sha256_file(path)}
            for path in paths
        ])
    else:
        content_hash = None
    created_at = None
    if paths:
        created_at = datetime.fromtimestamp(
            max(path.stat().st_mtime for path in paths),
            tz=timezone.utc,
        ).isoformat(timespec="seconds").replace("+00:00", "Z")
    return {
        **asdict(spec),
        "input_hash": None,
        "content_hash": content_hash,
        "status": status,
        "created_at": created_at,
    }


def build_artifact_index(
    scan_dir: Path | str,
    *,
    report_dir: Path | str | None = None,
    now: datetime | None = None,
) -> dict:
    scan = Path(scan_dir)
    report = Path(report_dir) if report_dir is not None else None
    contract = {}
    contract_path = scan / "run_contract.json"
    if contract_path.exists():
        try:
            loaded = json.loads(contract_path.read_text(encoding="utf-8"))
            contract = loaded if isinstance(loaded, dict) else {}
        except (OSError, json.JSONDecodeError):
            contract = {}
    rows = [
        _artifact_ref(spec, scan if spec.root == "scan" else report)
        for spec in CRITICAL_ARTIFACTS
    ]
    stamp = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    return {
        "schema_version": ARTIFACT_INDEX_SCHEMA_VERSION,
        "analysis_date": scan.name,
        "run_id": contract.get("run_id"),
        "contract_hash": contract.get("contract_hash"),
        "generated_at": stamp.isoformat(timespec="seconds").replace("+00:00", "Z"),
        "artifacts": rows,
        "coverage": {
            "registered": len(rows),
            "present": sum(row["status"] == "PRESENT" for row in rows),
            "empty": sum(row["status"] == "EMPTY" for row in rows),
            "missing": sum(row["status"] == "MISSING" for row in rows),
            "content_hashed": sum(row["content_hash"] is not None for row in rows),
            "input_hashed": sum(row["input_hash"] is not None for row in rows),
        },
    }


def write_artifact_index(
    scan_dir: Path | str,
    *,
    report_dir: Path | str | None = None,
    now: datetime | None = None,
) -> Path:
    scan = Path(scan_dir)
    scan.mkdir(parents=True, exist_ok=True)
    target = scan / "artifact_index.json"
    temp = target.with_name(f"{target.name}.tmp")
    temp.write_text(
        json.dumps(
            build_artifact_index(scan, report_dir=report_dir, now=now),
            ensure_ascii=False,
            indent=2,
        ) + "\n",
        encoding="utf-8",
    )
    temp.replace(target)
    return target
```

- [x] **Step 4: Run artifact and legacy finalists tests**

Run:

```bash
uv run --no-sync python -m pytest \
  tests/scan/test_artifacts.py \
  tests/scan/test_l4_reuse.py::test_read_finalists_preserves_ticker_leading_zeros -q
```

Expected: all tests pass, including the pre-existing six-digit code contract.

- [x] **Step 5: Commit the index core**

```bash
git add autoresearch/scan/artifacts.py tests/scan/test_artifacts.py
git commit -m "feat(scan): index critical scan artifacts"
```

### Task 4: Shadow contract health

**Files:**

- Modify: `tests/scan/test_health.py`
- Modify: `autoresearch/scan/health.py`

- [x] **Step 1: Add tests for ABSENT, OK, and INVALID states**

Add imports:

```python
from datetime import datetime, timezone

from autoresearch.scan.run_contract import RunContract, write_run_contract
```

Append:

```python
def _write_contract(day, *, config=None):
    contract = RunContract.build(
        analysis_date=day.name,
        user_config=config or {},
        pinned={"kept": [], "expired": []},
        data_policy={"source": "tushare"},
        stage_budgets={"l3_finalist_max": 10},
        artifact_schema_versions={"market_pack": 1},
        git_sha="abc",
        now=datetime(2026, 7, 28, tzinfo=timezone.utc),
    )
    write_run_contract(day / "run_contract.json", contract)
    return contract


def test_run_health_contract_absent_is_advisory(tmp_path):
    d = _mk_day(tmp_path, "2026-07-28")
    assert run_health(d)["run_contract"] == {
        "status": "ABSENT",
        "run_id": None,
        "contract_hash": None,
        "errors": [],
        "echo_config_match": None,
        "market_pack_config_match": None,
    }


def test_run_health_contract_ok_when_echoes_match(tmp_path):
    d = _mk_day(tmp_path, "2026-07-28")
    cfg = {"agents": {"l3_rank": {"effort": "high"}}}
    contract = _write_contract(d, config=cfg)
    (d / "user_config_echo.json").write_text(json.dumps(cfg), encoding="utf-8")
    (d / "market_pack.json").write_text(json.dumps({"user_config": cfg}), encoding="utf-8")
    result = run_health(d)["run_contract"]
    assert result["status"] == "OK"
    assert result["contract_hash"] == contract.contract_hash
    assert result["echo_config_match"] is True
    assert result["market_pack_config_match"] is True


def test_run_health_contract_invalid_on_config_drift(tmp_path):
    d = _mk_day(tmp_path, "2026-07-28")
    _write_contract(d, config={"redteam_prob": 0.1})
    (d / "user_config_echo.json").write_text(
        json.dumps({"redteam_prob": 0.2}),
        encoding="utf-8",
    )
    result = run_health(d)["run_contract"]
    assert result["status"] == "INVALID"
    assert result["echo_config_match"] is False
    assert "user_config_echo config_hash mismatch" in result["errors"]
```

- [x] **Step 2: Verify the health field is absent**

Run:

```bash
uv run --no-sync python -m pytest \
  tests/scan/test_health.py::test_run_health_contract_absent_is_advisory \
  tests/scan/test_health.py::test_run_health_contract_ok_when_echoes_match \
  tests/scan/test_health.py::test_run_health_contract_invalid_on_config_drift -q
```

Expected: FAIL with missing `run_contract` health field.

- [x] **Step 3: Implement presence-gated contract health**

Add to `autoresearch/scan/health.py`:

```python
def _json_object(path: Path) -> dict | None:
    if not path.exists():
        return None
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else None
    except (OSError, json.JSONDecodeError):
        return None


def run_contract_health(scan_dir: Path) -> dict:
    from autoresearch.scan.run_contract import load_run_contract, sha256_json

    scan = Path(scan_dir)
    path = scan / "run_contract.json"
    empty = {
        "status": "ABSENT",
        "run_id": None,
        "contract_hash": None,
        "errors": [],
        "echo_config_match": None,
        "market_pack_config_match": None,
    }
    if not path.exists():
        return empty
    try:
        contract = load_run_contract(path)
    except (OSError, json.JSONDecodeError, TypeError, ValueError) as exc:
        return {
            **empty,
            "status": "INVALID",
            "errors": [f"run_contract unreadable: {exc}"],
        }

    errors: list[str] = []
    if contract.analysis_date != scan.name:
        errors.append(
            f"analysis_date mismatch: contract={contract.analysis_date} dir={scan.name}"
        )
    echo = _json_object(scan / "user_config_echo.json")
    pack = _json_object(scan / "market_pack.json")
    echo_match = None if echo is None else sha256_json(echo) == contract.config_hash
    pack_cfg = pack.get("user_config") if pack is not None else None
    pack_match = (
        None
        if not isinstance(pack_cfg, dict)
        else sha256_json(pack_cfg) == contract.config_hash
    )
    if echo_match is False:
        errors.append("user_config_echo config_hash mismatch")
    if pack_match is False:
        errors.append("market_pack user_config config_hash mismatch")
    return {
        "status": "INVALID" if errors else "OK",
        "run_id": contract.run_id,
        "contract_hash": contract.contract_hash,
        "errors": errors,
        "echo_config_match": echo_match,
        "market_pack_config_match": pack_match,
    }
```

Add this key to the dictionary returned by `run_health()`:

```python
            "run_contract": run_contract_health(scan_dir),
```

- [x] **Step 4: Run health tests**

Run:

```bash
uv run --no-sync python -m pytest tests/scan/test_health.py -q
```

Expected: all health tests pass; existing historical fixtures report `ABSENT` without failing.

- [x] **Step 5: Commit shadow validation**

```bash
git add autoresearch/scan/health.py tests/scan/test_health.py
git commit -m "feat(scan): report run contract health"
```

### Task 5: Publish contract identity and ArtifactIndex

**Files:**

- Modify: `tests/scan/test_assemble.py`
- Modify: `autoresearch/scan/assemble.py:1200-1295`

- [x] **Step 1: Make the assemble fixture carry a deterministic contract**

Add imports:

```python
from datetime import datetime, timezone

from autoresearch.scan.artifacts import ARTIFACT_INDEX_SCHEMA_VERSION
from autoresearch.scan.run_contract import RunContract, write_run_contract
```

At the end of `_build_scan_dir()` before `return scan`, add:

```python
    contract = RunContract.build(
        analysis_date=_DATA_DATE,
        user_config={},
        pinned={"kept": [], "expired": []},
        data_policy={"source": "tushare"},
        stage_budgets={"l3_finalist_max": 10},
        artifact_schema_versions={"market_pack": 1},
        git_sha="abc123",
        now=datetime(2026, 6, 21, 1, 2, 3, tzinfo=timezone.utc),
    )
    write_run_contract(scan / "run_contract.json", contract)
```

Replace the fixture return value with:

```python
    return {
        "summary_path": summary_path,
        "out_base": out_base,
        "md": md,
        "trace": out_base / "trace",
        "scan_dir": scan,
    }
```

Append integration tests:

```python
def test_manifest_records_contract_identity(published):
    manifest = json.loads(
        (published["out_base"] / "manifest.json").read_text(encoding="utf-8")
    )
    contract = json.loads(
        (published["trace"] / "run_contract.json").read_text(encoding="utf-8")
    )
    assert manifest["run_id"] == contract["run_id"]
    assert manifest["contract_hash"] == contract["contract_hash"]
    assert manifest["run_contract_schema_version"] == 1
    assert manifest["artifact_index_schema_version"] == ARTIFACT_INDEX_SCHEMA_VERSION


def test_artifact_index_is_written_and_published(published):
    staging = published["scan_dir"]
    index_path = staging / "artifact_index.json"
    trace_path = published["trace"] / "artifact_index.json"
    assert index_path.exists()
    assert trace_path.exists()
    index = json.loads(index_path.read_text(encoding="utf-8"))
    rows = {row["name"]: row for row in index["artifacts"]}
    assert index["contract_hash"]
    for name in ("run_contract", "l1_full", "l2", "finalists", "l4_cards",
                 "final_ratings", "gate_fires", "run_health", "summary", "manifest"):
        assert rows[name]["status"] == "PRESENT", name


def test_trace_run_health_is_final_refresh(published):
    staging = published["scan_dir"]
    assert (
        published["trace"] / "run_health.json"
    ).read_bytes() == (staging / "run_health.json").read_bytes()
```

- [x] **Step 2: Verify publication tests fail**

Run:

```bash
uv run --no-sync python -m pytest \
  tests/scan/test_assemble.py::test_manifest_records_contract_identity \
  tests/scan/test_assemble.py::test_artifact_index_is_written_and_published \
  tests/scan/test_assemble.py::test_trace_run_health_is_final_refresh -q
```

Expected: FAIL because manifest lacks contract fields and artifact index is not published.

- [x] **Step 3: Publish run_contract through the existing trace map**

Add one entry to `_publish_pipeline()`:

```python
        "run_contract.json": "run_contract.json",
```

- [x] **Step 4: Build manifest fields from a validated contract**

Immediately before writing `manifest.json`, construct:

```python
    from autoresearch.scan.artifacts import ARTIFACT_INDEX_SCHEMA_VERSION
    from autoresearch.scan.run_contract import load_run_contract

    manifest = {
        "analysis_date": analysis_date,
        "generated_at": now.isoformat(timespec="seconds"),
        "hhmm": hhmm,
        "artifact_index_schema_version": ARTIFACT_INDEX_SCHEMA_VERSION,
    }
    contract_path = scan_dir / "run_contract.json"
    if contract_path.exists():
        with contextlib.suppress(Exception):
            contract = load_run_contract(contract_path)
            manifest.update({
                "run_id": contract.run_id,
                "contract_hash": contract.contract_hash,
                "run_contract_schema_version": contract.schema_version,
            })
    (out_base / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False),
        encoding="utf-8",
    )
```

This remains presence-gated: legacy staging directories without a contract keep publishing.

- [x] **Step 5: Generate the final index after summary and refreshed health**

After the second `_health.write_run_health(scan_dir)` and before `index_md`, add:

```python
    with contextlib.suppress(Exception):
        from autoresearch.scan.artifacts import write_artifact_index

        artifact_index_path = write_artifact_index(scan_dir, report_dir=out_base)
        trace_dir = out_base / "trace"
        trace_dir.mkdir(parents=True, exist_ok=True)
        shutil.copy2(artifact_index_path, trace_dir / "artifact_index.json")
        refreshed_health = scan_dir / "run_health.json"
        if refreshed_health.exists():
            shutil.copy2(refreshed_health, trace_dir / "run_health.json")
```

The write order is intentional:

1. manifest exists
2. summary exists
3. `gate_fires.csv` exists
4. refreshed `run_health.json` exists
5. index hashes the final snapshot
6. trace receives the same final health snapshot

- [x] **Step 6: Run assemble, health, and artifact tests**

Run:

```bash
uv run --no-sync python -m pytest \
  tests/scan/test_assemble.py \
  tests/scan/test_health.py \
  tests/scan/test_artifacts.py -q
```

Expected: all tests pass.

- [x] **Step 7: Commit publication wiring**

```bash
git add autoresearch/scan/assemble.py tests/scan/test_assemble.py
git commit -m "feat(scan): publish artifact index and contract identity"
```

### Task 6: Documentation, compatibility, and full regression

**Files:**

- Modify: `docs/specs/2026-07-28-scan-market-unified-optimization-master-design.md`
- Modify: `docs/plans/2026-07-28-run-contract-artifact-index-plan.md`

- [x] **Step 1: Record the exact Wave 1 batch status**

Add under the Wave 1 section:

```markdown
#### 2026-07-28 第一批实现状态

已进入影子双写：

- `frame --json` 写 `run_contract.json`，market pack 仅携带短引用；
- `run_health.json` 报告契约 `ABSENT / OK / INVALID`，暂不阻断；
- `assemble` 把 contract 身份固化进 manifest；
- 15 类关键产物进入 `artifact_index.json`，最终快照随 trace 发布；
- 历史 staging 缺少 contract 时保持兼容。

尚未升级为生产硬门：

- Workflow 参数与 contract hash 的逐段回显；
- GATE1 的 config/pinned/hash fail-fast；
- 生产者级 `input_hash`；
- `StageResult / DecisionRecord / outbox`。
```

- [x] **Step 2: Run formatter/lint checks for touched Python files**

Run:

```bash
uv run --no-sync python -m compileall -q \
  autoresearch/scan/run_contract.py \
  autoresearch/scan/artifacts.py \
  autoresearch/scan/frame.py \
  autoresearch/scan/health.py \
  autoresearch/scan/assemble.py
```

Expected: exit code 0 and no output.

- [x] **Step 3: Run the focused regression suite**

Run:

```bash
uv run --no-sync python -m pytest \
  tests/scan/test_run_contract.py \
  tests/scan/test_artifacts.py \
  tests/scan/test_l4_reuse.py::test_read_finalists_preserves_ticker_leading_zeros \
  tests/scan/test_frame_json_clean.py \
  tests/scan/test_frame.py \
  tests/scan/test_user_config.py \
  tests/scan/test_health.py \
  tests/scan/test_assemble.py -q
```

Expected: all selected tests pass.

- [x] **Step 4: Run the full suite**

Run:

```bash
uv run --no-sync python -m pytest -q
```

Expected: all tests pass. If an environment-only network test is collected, preserve its exact failure separately and confirm every deterministic test remains green.

- [x] **Step 5: Verify no selection behavior changed**

Run:

```bash
git diff -- \
  autoresearch/scan/agents/l3_select.py \
  autoresearch/scan/gates.py \
  autoresearch/scan/l4_card.py \
  autoresearch/agents/utils/rating.py
```

Expected: no output.

- [x] **Step 6: Inspect final diff and working tree**

Run:

```bash
git diff --check
git status --short
```

Expected: `git diff --check` exits 0; status lists only the planned files before the final commit.

- [x] **Step 7: Mark completed checkboxes and commit the batch record**

```bash
git add \
  docs/specs/2026-07-28-scan-market-unified-optimization-master-design.md \
  docs/plans/2026-07-28-run-contract-artifact-index-plan.md
git commit -m "docs(scan): record contract control-plane batch"
```

## Acceptance criteria

- `run_contract.json` includes analysis date, run id, git SHA, exact user config and hash, agent overrides, normalized pinned rows, data policy, static stage budgets, artifact schema versions, and whole-contract hash.
- Key ordering changes do not change `config_hash`; pinned/data-policy changes do change `contract_hash`.
- `frame --json` stdout remains a single valid JSON document.
- `run_health.json` distinguishes absent legacy data from valid and invalid contract state without blocking.
- The finite registry covers market pack, L1, L2, L3 judged, finalists, L4 cards, ensemble, final ratings, gate fires, early stop, health, summary, manifest, and the contract itself.
- Artifact status distinguishes `PRESENT`, `EMPTY`, and `MISSING`; present content gets a SHA-256 hash.
- Manifest and trace carry the validated contract identity when available.
- Artifact index is built after summary, manifest, gate fires, and the final health refresh.
- Existing historical inputs without contract/index remain readable and publishable.
- No files that decide L3 selection, gates, L4 rubric, rating parsing, or BUY thresholds are modified.
