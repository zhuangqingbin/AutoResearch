# Scan Forensic Run Capsule Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让每次 `scan-market` 从启动起就拥有独立、可冻结、可校验、可重放的法证级 run capsule；成功、失败和中断都能按 `run_id` 还原命令、尝试、输入、agent 可见证据和根因，且缺证据时机器明确报红。

**Architecture:** 在 `autoresearch.trace` 下建立 append-only 取证控制面：run-scoped workspace + 双状态机 + hash-chain events 是地基；命令捕获、精确 source lineage、身份快照和 transcript adapters 向同一 capsule 写证据；`RunProfile` 物化 expected evidence，completeness/replay 与 MANIFEST/root ledger 分别回答“齐不齐、能否重放、是否被改”；旧 `scan.retention` 在新链真跑验收前保持兼容，不作为新完整性结论来源。

**Tech Stack:** Python 3.11、pytest、stdlib (`dataclasses`, `hashlib`, `json`, `subprocess`, `tarfile`, `fcntl`, `signal`)、pandas/pyarrow、zstandard、现有 Claude Workflow JS、现有 `uv run --no-sync` 工具链。

---

## Execution guardrails

- 所有命令先执行 `export AUTORESEARCH_ENGINE=codex`；测试和实现只允许读写 `context_codex/`、`reports_codex/` 与共享 `lake/`。
- 开工前用 `superpowers:using-git-worktrees` 从包含本计划的提交创建独立 worktree。不要复制当前主工作区里未提交的 `pinned.jsonc`、`frame.py`、`l4/prompts.py`、`self_review.py` 或研究笔记改动。
- 每个任务遵循红灯测试 → 最小实现 → 目标测试 → 相关回归 → 独立提交。不得把多个任务压成一次大提交。
- Phase A–E 完成前，`autoresearch.scan.retention.retain()` 仍是兼容路径；不得让新 UI 把旧 MANIFEST 绿灯称为 `completeness_ok=true`。
- 所有新 JSON/JSONL 使用 UTF-8、排序键和尾换行；所有时间保存 UTC `Z`；所有写入走同目录临时文件 + `replace()`，append-only 文件使用 `fcntl.flock`。

## File map

新增文件：

- `autoresearch/trace/atomic.py`：canonical JSON、原子写、sha256、锁内 JSONL 追加。
- `autoresearch/trace/events.py`：run event schema、hash chain、验证器。
- `autoresearch/trace/capsule_models.py`：业务/证据状态、RunHandle、Checkpoint、FinalizationResult。
- `autoresearch/trace/capsule.py`：begin/load/checkpoint/finalize/recover/verify/inspect/replay CLI。
- `autoresearch/trace/exec_capture.py`：子进程 argv、日志、signal、invocation 事实捕获。
- `autoresearch/trace/blobs.py`：内容寻址 blob 存储。
- `autoresearch/trace/source_lineage.py`：精确读点与 coverage。
- `autoresearch/trace/identity.py`：代码、prompt、环境、依赖快照和脱敏。
- `autoresearch/trace/transcripts/base.py`：稳定 adapter/types。
- `autoresearch/trace/transcripts/claude.py`：现有 Claude schema 适配。
- `autoresearch/trace/transcripts/codex.py`：Codex rollout schema 适配和显式绑定。
- `autoresearch/trace/transcripts/__init__.py`：按 engine 派发。
- `autoresearch/trace/completeness.py`：expected evidence 和覆盖率判定。
- `autoresearch/trace/replay.py`：只读 scratch 重放。
- `autoresearch/scan/run_bootstrap.py`：在任何取数前构造完整 Scan RunContract v3。
- `autoresearch/scan/run_profile.py`：scan full/sentinel/failed/interrupted 的 evidence 规则。
- `tests/trace/fixtures/{claude,codex}/`：脱敏后的最小 transcript fixtures。
- `tests/trace/test_{atomic,capsule_models,events,capsule,exec_capture,blobs,source_lineage,identity,transcript_adapters,completeness,replay,finalization,recovery,repairs}.py`。
- `tests/scan/test_run_profile.py`、`tests/scan/test_forensic_workflow.py`、`tests/integration/test_scan_capsule_faults.py`。

修改文件：

- `autoresearch/common/workspace.py`
- `autoresearch/analyze/harvest.py`
- `autoresearch/scan/run_contract.py`
- `autoresearch/scan/frame.py`
- `autoresearch/scan/l4/producers.py`
- `autoresearch/scan/l4/prompts.py`
- `autoresearch/scan/l4_tasks.py`
- `autoresearch/scan/stage_result.py`
- `autoresearch/data/cache.py`
- `autoresearch/trace/usage_harvest.py`
- `autoresearch/scan/publisher.py`
- `autoresearch/scan/post_run.py`
- `autoresearch/scan/retention.py`
- `autoresearch/scan/chain_view.py`
- `.claude/workflows/scan-market.js`
- `.claude/workflows/l4-stock.js`
- `.claude/skills/scan-market/SKILL.md`
- `.claude/skills/scan-market/STAGES.md`
- `pyproject.toml`
- `uv.lock`

## Phase A — Run identity, scoped workspace, events, attempts

### Task 1: Make workspace resolution run-scoped and engine-safe

**Files:**

- Modify: `autoresearch/common/workspace.py`
- Modify: `autoresearch/analyze/harvest.py`
- Modify: `autoresearch/scan/l4/producers.py`
- Modify: `autoresearch/scan/l4/prompts.py`
- Modify: `autoresearch/scan/l4_tasks.py`
- Modify: `tests/common/test_workspace.py`
- Modify: `tests/analyze/test_harvest.py`
- Modify: `tests/scan/test_harvest_slim.py`
- Modify: `tests/scan/test_l4_dispatch_pack.py`
- Modify: `tests/scan/test_l4_tasks.py`
- Modify: `tests/scan/test_retention.py`
- Modify: `tests/scan/test_frame_json_clean.py`

- [ ] **Step 1: Add failing path-contract tests**

Append tests that lock legacy fallback, run scoping, validation, and Codex-only roots:

```python
def test_scan_dir_uses_run_scoped_staging_when_run_id_is_present(monkeypatch):
    monkeypatch.setattr(workspace, "ENGINE", "codex")
    monkeypatch.setenv("AUTORESEARCH_RUN_ID", "20260827T010203456789Z")
    assert workspace.scan_dir("2026-08-27") == Path(
        "context_codex/scan_runs/20260827T010203456789Z/staging/2026-08-27"
    )
    assert workspace.scan_root() == Path(
        "context_codex/scan_runs/20260827T010203456789Z/staging"
    )
    assert workspace.scan_input_dir("2026-08-27") == Path(
        "context_codex/scan_runs/20260827T010203456789Z/staging/2026-08-27/_external_inputs"
    )


def test_scan_dir_keeps_legacy_path_without_run_id(monkeypatch):
    monkeypatch.setattr(workspace, "ENGINE", "codex")
    monkeypatch.delenv("AUTORESEARCH_RUN_ID", raising=False)
    assert workspace.scan_dir("2026-08-27") == Path("context_codex/scan/2026-08-27")
    assert workspace.scan_input_dir("2026-08-27") == Path("context_codex")


@pytest.mark.parametrize("bad", ["", "../x", "run/x", "latest", "20260827_0102"])
def test_explicit_run_id_rejects_unsafe_or_ambiguous_values(monkeypatch, bad):
    monkeypatch.setenv("AUTORESEARCH_RUN_ID", bad)
    if bad == "":
        assert workspace.active_run_id() is None
    else:
        with pytest.raises(ValueError, match="AUTORESEARCH_RUN_ID"):
            workspace.active_run_id()


def test_codex_run_paths_never_resolve_to_claude(monkeypatch):
    monkeypatch.setattr(workspace, "ENGINE", "codex")
    monkeypatch.setenv("AUTORESEARCH_RUN_ID", "20260827T010203456789Z")
    resolved = str(workspace.scan_dir("2026-08-27"))
    assert "context_codex" in resolved
    assert "claude" not in resolved
```

- [ ] **Step 2: Run the tests and confirm the red light**

Run:

```bash
export AUTORESEARCH_ENGINE=codex
uv run --no-sync python -m pytest tests/common/test_workspace.py -q
```

Expected: failures because `active_run_id()` and `scan_run_root()` do not exist and `scan_dir()` still returns `context_codex/scan/<date>`.

- [ ] **Step 3: Implement validated scoped paths**

Add the validator/root helpers to `workspace.py` and replace the existing `scan_root()` and `scan_dir()` definitions with the versions below; do not leave duplicate definitions:

```python
import re

_RUN_ID_RE = re.compile(r"^\d{8}T\d{12}Z$")


def active_run_id(environ=None) -> str | None:
    env = os.environ if environ is None else environ
    value = str(env.get("AUTORESEARCH_RUN_ID", "")).strip()
    if not value:
        return None
    if not _RUN_ID_RE.fullmatch(value):
        raise ValueError(f"AUTORESEARCH_RUN_ID={value!r} 非法")
    return value


def scan_run_root(run_id: str | None = None) -> Path:
    value = run_id or active_run_id()
    if value is None:
        raise ValueError("缺 AUTORESEARCH_RUN_ID，无法解析 run-scoped workspace")
    if not _RUN_ID_RE.fullmatch(value):
        raise ValueError(f"AUTORESEARCH_RUN_ID={value!r} 非法")
    return context_root() / "scan_runs" / value


def scan_root() -> Path:
    run_id = active_run_id()
    return scan_run_root(run_id) / "staging" if run_id else context_root() / "scan"


def scan_dir(date) -> Path:
    return scan_root() / str(date)


def scan_input_dir(date) -> Path:
    return scan_dir(date) / "_external_inputs" if active_run_id() else context_root()
```

- [ ] **Step 4: Move slim/deep into the scoped input directory**

In `analyze.harvest`, choose `ROOT / ws.scan_input_dir(trade_date)` for slim output and keep the legacy context root for non-slim full reports. Use `mkdir(parents=True, exist_ok=True)`.

In `l4.producers.harvest_slim_batch()` and `l4_tasks.initialize()`, default the slim context to `ws.scan_input_dir(date)`. In `l4/prompts.py`, render the resolved slim/deep paths and resolved `ws.scan_dir(date)` paths into each dispatch prompt instead of the historical `context/...` literals. Legacy invocations without a run ID must render the same effective engine root they use on disk.

Add tests proving two run IDs on the same analysis date produce different slim paths, the second harvest cannot overwrite the first, and the L4 task book/prompt hashes point at the scoped file.

- [ ] **Step 5: Remove Claude literals from engine-sensitive fixtures**

In the four failing Codex-path tests, build roots from `workspace.context_root()` / `workspace.reports_root()` or monkeypatch the relevant module-level root. Keep literal `context_claude` only in tests whose subject is historical Claude-path remapping.

- [ ] **Step 6: Verify and commit**

Run:

```bash
export AUTORESEARCH_ENGINE=codex
uv run --no-sync python -m pytest tests/common/test_workspace.py tests/analyze/test_harvest.py tests/scan/test_harvest_slim.py tests/scan/test_l4_dispatch_pack.py tests/scan/test_l4_tasks.py tests/scan/test_retention.py tests/scan/test_frame_json_clean.py -q
git add autoresearch/common/workspace.py autoresearch/analyze/harvest.py autoresearch/scan/l4/producers.py autoresearch/scan/l4/prompts.py autoresearch/scan/l4_tasks.py tests/common/test_workspace.py tests/analyze/test_harvest.py tests/scan/test_harvest_slim.py tests/scan/test_l4_dispatch_pack.py tests/scan/test_l4_tasks.py tests/scan/test_retention.py tests/scan/test_frame_json_clean.py
git commit -m "feat(trace): isolate scan staging by run id"
```

Expected: all selected tests pass; `git diff --cached --name-only` contains no user-owned dirty file.

### Task 2: Introduce RunContract v3 and immutable lifecycle models

**Files:**

- Create: `autoresearch/trace/atomic.py`
- Create: `autoresearch/trace/capsule_models.py`
- Modify: `autoresearch/scan/run_contract.py`
- Modify: `tests/scan/test_run_contract.py`
- Create: `tests/trace/test_atomic.py`
- Create: `tests/trace/test_capsule_models.py`

- [ ] **Step 1: Write failing v1/v2/v3 compatibility and state tests**

```python
def test_v3_contract_carries_engine_kind_workspace_and_session_ref():
    contract = _build(
        run_kind="scan-market",
        engine="codex",
        workspace_path="context_codex/scan_runs/20260827T010203456789Z",
        session_ref="01a03dbe-7173-76a3-ac96-919ae6936e71",
    )
    assert contract.schema_version == 3
    assert contract.engine == "codex"
    assert contract.run_kind == "scan-market"
    assert contract.session_ref.startswith("01a03dbe")


def test_v2_contract_still_verifies_without_v3_fields(tmp_path):
    raw = make_v2_contract_dict()
    path = tmp_path / "run_contract.json"
    path.write_text(json.dumps(raw), encoding="utf-8")
    loaded = load_run_contract(path)
    assert loaded.schema_version == 2
    assert loaded.run_kind == "scan-market"
    assert loaded.engine == ""


def test_business_and_evidence_states_are_orthogonal():
    state = RunState.build(
        run_id=RUN_ID,
        business_status=BusinessStatus.SUCCEEDED,
        evidence_status=EvidenceStatus.EVIDENCE_INCOMPLETE,
    )
    assert state.business_status == "SUCCEEDED"
    assert state.evidence_status == "EVIDENCE_INCOMPLETE"


def test_atomic_json_write_replaces_complete_document_and_removes_temp(tmp_path):
    path = tmp_path / "state.json"
    atomic_write_json(path, {"b": 2, "a": 1})
    assert json.loads(path.read_text(encoding="utf-8")) == {"a": 1, "b": 2}
    assert path.read_text(encoding="utf-8").endswith("\n")
    assert not (tmp_path / "state.json.tmp").exists()
```

- [ ] **Step 2: Confirm failures**

Run:

```bash
export AUTORESEARCH_ENGINE=codex
uv run --no-sync python -m pytest tests/scan/test_run_contract.py tests/trace/test_atomic.py tests/trace/test_capsule_models.py -q
```

Expected: v3 fields and lifecycle enums are absent.

- [ ] **Step 3: Add stable primitives and models**

`atomic.py` must expose these concrete functions:

```python
def canonical_json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def sha256_file(path: Path | str) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def atomic_write_json(path: Path | str, value: object) -> Path:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    temp = target.with_name(f"{target.name}.tmp")
    temp.write_text(json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    temp.replace(target)
    return target
```

`capsule_models.py` must define `BusinessStatus(ACTIVE, SUCCEEDED, FAILED, INTERRUPTED)`, `EvidenceStatus(PENDING, COMPLETE, EVIDENCE_INCOMPLETE, LEGACY_PARTIAL)`, `Replayability(FULL, PARTIAL, NONE, EVIDENCE_ONLY)`, immutable `RunState`, `RunHandle`, `Checkpoint`, and `FinalizationResult`. `RunState.build()` normalizes UTC and refuses illegal business transitions.

- [ ] **Step 4: Upgrade RunContract without breaking old hashes**

Set schema version to 3, supported versions to `(1, 2, 3)`, add `_V3_FIELDS`, and exclude those keys when `schema_version < 3`:

```python
_V3_FIELDS = ("run_kind", "engine", "workspace_path", "session_ref")

# RunContract defaults after v2 fields
run_kind: str = "scan-market"
engine: str = ""
workspace_path: str = ""
session_ref: str | None = None

# inside _hash_payload
if self.schema_version < 3:
    for key in _V3_FIELDS:
        payload.pop(key, None)
```

`build()` must accept those four keyword arguments. `run_bootstrap` passes the allocated run workspace explicitly for capsule runs; legacy one-shot callers default `workspace_path` to `str(ws.scan_dir(analysis_date))` so the contract never claims a directory that was not created.

It must also accept `run_id: str | None = None`; use the injected validated ID when capsule bootstrap has already allocated one, otherwise keep the timestamp-generated legacy behavior:

```python
resolved_run_id = run_id or stamp.strftime("%Y%m%dT%H%M%S%fZ")
if not re.fullmatch(r"\d{8}T\d{12}Z", resolved_run_id):
    raise ValueError(f"invalid run_id: {resolved_run_id!r}")
```

- [ ] **Step 5: Verify and commit**

```bash
export AUTORESEARCH_ENGINE=codex
uv run --no-sync python -m pytest tests/scan/test_run_contract.py tests/trace/test_atomic.py tests/trace/test_capsule_models.py -q
git add autoresearch/trace/atomic.py autoresearch/trace/capsule_models.py autoresearch/scan/run_contract.py tests/scan/test_run_contract.py tests/trace/test_atomic.py tests/trace/test_capsule_models.py
git commit -m "feat(trace): define forensic run lifecycle"
```

### Task 3: Add append-only hash-chained execution events

**Files:**

- Create: `autoresearch/trace/events.py`
- Create: `tests/trace/test_events.py`

- [ ] **Step 1: Write mutation-sensitive tests**

```python
def _append_three(path):
    append_event(path, run_id=RUN_ID, engine="codex", stage="prelude",
                 invocation_id="inv-1", attempt=1, subject=None,
                 event_type="RUN_STARTED", payload={})
    append_event(path, run_id=RUN_ID, engine="codex", stage="frame",
                 invocation_id="inv-2", attempt=1, subject=None,
                 event_type="COMMAND_STARTED", payload={"argv": ["python", "-m", "x"]})
    append_event(path, run_id=RUN_ID, engine="codex", stage="frame",
                 invocation_id="inv-2", attempt=1, subject=None,
                 event_type="COMMAND_COMPLETED", payload={"exit_code": 0})


def test_event_chain_detects_delete_insert_reorder_and_edit(tmp_path, subtests):
    original = tmp_path / "events.jsonl"
    _append_three(original)
    rows = original.read_text(encoding="utf-8").splitlines()
    mutations = {
        "delete": [rows[0], rows[2]],
        "insert": [rows[0], json.dumps({"seq": 2}), rows[1], rows[2]],
        "reorder": [rows[1], rows[0], rows[2]],
        "edit": [rows[0], rows[1].replace("frame", "l4"), rows[2]],
    }
    for name, changed in mutations.items():
        with subtests.test(name=name):
            path = tmp_path / f"{name}.jsonl"
            path.write_text("\n".join(changed) + "\n", encoding="utf-8")
            assert verify_event_chain(path)["ok"] is False


def test_concurrent_append_produces_contiguous_sequence(tmp_path):
    path = tmp_path / "events.jsonl"
    with ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(lambda n: append_test_event(path, n), range(80)))
    result = verify_event_chain(path)
    assert result == {"ok": True, "n": 80, "error": None, "last_hash": result["last_hash"]}
```

- [ ] **Step 2: Confirm red**

```bash
export AUTORESEARCH_ENGINE=codex
uv run --no-sync python -m pytest tests/trace/test_events.py -q
```

Expected: module import fails.

- [ ] **Step 3: Implement the locked append operation**

The event hash must cover the full event except `event_hash`; store full 64-character hashes:

```python
EVENT_SCHEMA_VERSION = 1
GENESIS_HASH = "0" * 64


def _event_hash(event: dict) -> str:
    payload = {key: value for key, value in event.items() if key != "event_hash"}
    return sha256_bytes(canonical_json(payload).encode("utf-8"))


def append_event(path: Path | str, **fields) -> dict:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("a+", encoding="utf-8") as handle:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        handle.seek(0)
        rows = [json.loads(line) for line in handle if line.strip()]
        previous = rows[-1] if rows else None
        event = {
            "schema_version": EVENT_SCHEMA_VERSION,
            "seq": len(rows) + 1,
            "ts": utc_now(),
            **fields,
            "prev_hash": previous["event_hash"] if previous else GENESIS_HASH,
        }
        event["event_hash"] = _event_hash(event)
        handle.seek(0, os.SEEK_END)
        handle.write(canonical_json(event) + "\n")
        handle.flush()
        os.fsync(handle.fileno())
        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
    return event
```

`verify_event_chain()` must validate JSON shape, `seq`, `prev_hash`, recomputed hash, run ID consistency, and return the first failing line.

- [ ] **Step 4: Verify and commit**

```bash
export AUTORESEARCH_ENGINE=codex
uv run --no-sync python -m pytest tests/trace/test_events.py -q
git add autoresearch/trace/events.py tests/trace/test_events.py
git commit -m "feat(trace): add hash chained run events"
```

### Task 4: Implement begin/load/checkpoint and dual-write StageResult attempts

**Files:**

- Create: `autoresearch/trace/capsule.py`
- Create: `autoresearch/scan/run_bootstrap.py`
- Create: `tests/trace/test_capsule.py`
- Create: `tests/scan/test_run_bootstrap.py`
- Modify: `autoresearch/scan/frame.py`
- Modify: `tests/scan/test_frame.py`
- Modify: `autoresearch/scan/stage_result.py`
- Modify: `tests/scan/test_stage_result.py`

- [ ] **Step 1: Write lifecycle and append-attempt tests**

```python
def test_begin_run_creates_active_spool_before_staging(tmp_path, monkeypatch):
    redirect_roots(monkeypatch, tmp_path)
    handle = begin_run("scan-market", DATE, "codex", {"agents": {}}, now=NOW)
    assert handle.workspace == tmp_path / "context_codex/scan_runs" / handle.run_id
    assert handle.staging == handle.workspace / "staging" / DATE
    state = json.loads((handle.workspace / "state.json").read_text(encoding="utf-8"))
    assert state["business_status"] == "ACTIVE"
    assert state["evidence_status"] == "PENDING"
    assert verify_event_chain(handle.capsule / "events/events.jsonl")["ok"] is True


def test_checkpoint_never_overwrites_an_attempt(tmp_path, monkeypatch):
    handle = begin_fixture_run(tmp_path, monkeypatch)
    first = checkpoint(handle.run_id, "l3", "FAILED", [], {}, error="schema")
    second = checkpoint(handle.run_id, "l3", "SUCCEEDED", ["_l3_judged.json"], {})
    assert first.attempt == 1 and second.attempt == 2
    assert (handle.capsule / "stages/l3/attempt-1/result.json").is_file()
    assert (handle.capsule / "stages/l3/attempt-2/result.json").is_file()
    assert json.loads((handle.capsule / "stages/l3/attempt-1/result.json").read_text())["error"] == "schema"


def test_stage_result_keeps_latest_compat_snapshot_and_appends_forensic_attempt(tmp_path, monkeypatch):
    handle = begin_fixture_run(tmp_path, monkeypatch)
    monkeypatch.setenv("AUTORESEARCH_RUN_ID", handle.run_id)
    record_stage_result(handle.staging, stage="gate1", status="FAILED", artifacts=[], metrics={}, warnings=[], error="bad")
    record_stage_result(handle.staging, stage="gate1", status="SUCCEEDED", artifacts=["L2"], metrics={}, warnings=[], error=None)
    assert load_stage_result(handle.staging / "stage_results/gate1.json").status == "SUCCEEDED"
    attempts = sorted((handle.capsule / "stages/gate1").glob("attempt-*/result.json"))
    assert len(attempts) == 2
    assert json.loads(attempts[0].read_text())["error"] == "bad"


def test_frame_reuses_bootstrap_contract_instead_of_minting_second_run_id(tmp_path, monkeypatch):
    handle = begin_scan_fixture(tmp_path, monkeypatch)
    monkeypatch.setenv("AUTORESEARCH_RUN_ID", handle.run_id)
    run_frame_with_fetches_stubbed(handle.analysis_date)
    contract = load_run_contract(handle.staging / "run_contract.json")
    assert contract.run_id == handle.run_id
    assert contract.contract_hash == handle.contract.contract_hash


def test_frame_refuses_active_run_without_matching_contract(tmp_path, monkeypatch):
    redirect_roots(monkeypatch, tmp_path)
    monkeypatch.setenv("AUTORESEARCH_RUN_ID", "20260827T010203456789Z")
    with pytest.raises(RuntimeError, match="RunContract v3"):
        resolve_active_scan_contract(DATE)
```

- [ ] **Step 2: Confirm red**

```bash
export AUTORESEARCH_ENGINE=codex
uv run --no-sync python -m pytest tests/trace/test_capsule.py tests/scan/test_stage_result.py -q
```

- [ ] **Step 3: Implement the lifecycle API**

Extract the configuration-only setup currently embedded in `frame.main()` into `run_bootstrap.prepare_scan_run()`: load and validate `scan_config`, resolve agents, pinned snapshot, effective data policy, stage budgets, artifact schema versions, prompt hashes, and RunContract v3 without fetching market data. `RunContract.build()` accepts an optional validated `run_id` so bootstrap/capsule/workspace use one identity.

`begin_run()` must create `$CTX/scan_runs/<run_id>/{staging/<date>,capsule/...}`, call that bootstrap for `kind=scan-market`, write the complete RunContract v3 into workspace, staging and `capsule/identity/`, write `state.json`, and append `RUN_STARTED`. It must use `mkdir(exist_ok=False)` so collision cannot attach to another run.

When `AUTORESEARCH_RUN_ID` is present, `frame` loads and verifies the existing contract before any fetch, takes effective knobs from it, and refuses date/engine/config mismatches. Without a run ID it keeps the legacy one-shot bootstrap path for historical commands/tests. It never mints a second identity inside an active run.

`load_run()` resolves only under `ws.context_root()/scan_runs`; path traversal and unknown run IDs fail.

`checkpoint()` computes the next attempt from existing directories, snapshots supplied artifact paths through the blob store interface introduced in Task 7, writes `inputs.json`, `outputs.json`, `result.json`, then emits exactly one terminal stage event plus `CHECKPOINT_WRITTEN`. Until Task 7 lands, empty artifacts are valid and non-empty artifacts are copied byte-for-byte into `products/staging/`.

Use this CLI shape:

```text
python -m autoresearch.trace.capsule begin scan-market 2026-08-27 --engine codex --config-file .claude/skills/scan-market/scan_config.jsonc
python -m autoresearch.trace.capsule checkpoint <run_id> <stage> <status> --artifact <path>
python -m autoresearch.trace.capsule inspect <run_id>
```

All three emit one canonical JSON object on stdout and diagnostics only on stderr.

- [ ] **Step 4: Add best-effort StageResult dual write**

After the compatibility snapshot succeeds, `safe_record_stage_result()` must call `capsule.checkpoint()` only when `AUTORESEARCH_RUN_ID` is present. A capsule failure is printed as `[capsule] ...` to stderr and does not change the business return value. `record_stage_result()` itself remains deterministic and unaware of process environment.

- [ ] **Step 5: Verify and commit**

```bash
export AUTORESEARCH_ENGINE=codex
uv run --no-sync python -m pytest tests/trace/test_capsule.py tests/scan/test_run_bootstrap.py tests/scan/test_frame.py tests/scan/test_stage_result.py -q
git add autoresearch/trace/capsule.py autoresearch/scan/run_bootstrap.py autoresearch/scan/frame.py autoresearch/scan/stage_result.py tests/trace/test_capsule.py tests/scan/test_run_bootstrap.py tests/scan/test_frame.py tests/scan/test_stage_result.py
git commit -m "feat(trace): begin runs and retain every stage attempt"
```

## Phase B — Commands, task transitions, identity, exact source lineage

### Task 5: Capture every deterministic CLI invocation and its streamed logs

**Files:**

- Create: `autoresearch/trace/exec_capture.py`
- Create: `tests/trace/test_exec_capture.py`
- Modify: `.claude/workflows/scan-market.js`
- Modify: `.claude/workflows/l4-stock.js`
- Create: `tests/scan/test_forensic_workflow.py`

- [ ] **Step 1: Write process, signal, redaction, and workflow tests**

```python
def test_capture_preserves_argv_and_separate_streams(tmp_path, monkeypatch):
    handle = begin_fixture_run(tmp_path, monkeypatch)
    result = run_captured(
        handle,
        stage="frame",
        argv=[sys.executable, "-c", "import sys; print('out'); print('err', file=sys.stderr)"],
        invocation_id="inv-frame-1",
    )
    assert result.exit_code == 0
    meta = json.loads((handle.capsule / "events/invocations.json").read_text())["inv-frame-1"]
    assert meta["argv"][1:] == ["-c", "import sys; print('out'); print('err', file=sys.stderr)"]
    assert gzip_text(handle.capsule / "logs/frame/inv-frame-1.stdout.log.gz") == "out\n"
    assert gzip_text(handle.capsule / "logs/frame/inv-frame-1.stderr.log.gz") == "err\n"


def test_capture_records_secret_presence_without_value(tmp_path, monkeypatch):
    monkeypatch.setenv("TUSHARE_TOKEN", "super-secret-value")
    result = captured_fixture(tmp_path, monkeypatch)
    meta = result.invocation
    assert meta["environment"]["TUSHARE_TOKEN"] == {"present": True}
    assert "super-secret-value" not in json.dumps(meta)


def test_workflows_require_run_id_and_wrap_python_commands():
    scan = Path(".claude/workflows/scan-market.js").read_text(encoding="utf-8")
    l4 = Path(".claude/workflows/l4-stock.js").read_text(encoding="utf-8")
    assert "args.run_id" in scan and "AUTORESEARCH_RUN_ID" in scan
    assert "autoresearch.trace.exec_capture" in scan
    assert "autoresearch.trace.exec_capture" in l4
    assert "`${CTX}/scan/${date}`" not in scan
```

- [ ] **Step 2: Confirm red**

```bash
export AUTORESEARCH_ENGINE=codex
uv run --no-sync python -m pytest tests/trace/test_exec_capture.py tests/scan/test_forensic_workflow.py -q
```

- [ ] **Step 3: Implement `run_captured()` and CLI passthrough**

Use `subprocess.Popen(argv, stdout=PIPE, stderr=PIPE, start_new_session=True)`, two reader threads that append bytes to temporary log files and echo to the parent streams, and deterministic gzip with `mtime=0` after process exit. Install SIGINT/SIGTERM handlers that forward the signal to the child process group, flush logs, emit `COMMAND_FAILED` with the signal, then restore prior handlers.

The CLI parser must require:

```text
--run-id <id> --stage <stage> --invocation-id <id> [--attempt N] [--subject CODE] -- <argv...>
```

Child environment must include `AUTORESEARCH_RUN_ID`, `AUTORESEARCH_STAGE`, `AUTORESEARCH_INVOCATION_ID`; recorded environment is restricted to locale/timezone/Python path plus presence-only secret keys.

- [ ] **Step 4: Wire both workflows without shell-string quoting**

Change `R` to a function that prefixes every Python module call:

```javascript
const RUN_ID = (typeof args === 'string' && args ? JSON.parse(args).run_id : (args && args.run_id))
if (!RUN_ID) throw new Error('args.run_id 必填；先运行 autoresearch.trace.capsule begin')
const SD = `${CTX}/scan_runs/${RUN_ID}/staging/${date}`
const PY = (stage, invocation) =>
  `AUTORESEARCH_ENGINE=${ENGINE} AUTORESEARCH_RUN_ID=${RUN_ID} ` +
  `uv run --no-sync python -m autoresearch.trace.exec_capture --run-id ${RUN_ID} ` +
  `--stage ${stage} --invocation-id ${invocation} -- uv run --no-sync python -m`
```

Replace each deterministic command prefix with `PY(stage, stableInvocationId)`. Keep existing business commands, redirections, gates, and retry decisions byte-for-byte after the prefix. For repeated per-stock commands include `${code}` and attempt in invocation ID.

- [ ] **Step 5: Verify workflow syntax and commit**

```bash
export AUTORESEARCH_ENGINE=codex
uv run --no-sync python -m pytest tests/trace/test_exec_capture.py tests/scan/test_forensic_workflow.py tests/scan/test_workflow_syntax.py tests/scan/test_wave3_workflows.py -q
git add autoresearch/trace/exec_capture.py tests/trace/test_exec_capture.py tests/scan/test_forensic_workflow.py .claude/workflows/scan-market.js .claude/workflows/l4-stock.js
git commit -m "feat(trace): capture scan commands and logs"
```

### Task 6: Append L4 task transitions and agent role boundaries

**Files:**

- Modify: `autoresearch/scan/l4_tasks.py`
- Modify: `tests/scan/test_l4_tasks.py`
- Modify: `tests/scan/test_l4_tasks_running_visibility.py`
- Modify: `.claude/workflows/l4-stock.js`

- [ ] **Step 1: Write retry-history and role-boundary tests**

```python
def test_success_does_not_erase_previous_l4_failure_from_capsule(tmp_path, monkeypatch):
    handle, book = initialized_task_run(tmp_path, monkeypatch, code="600000")
    l4_tasks.failure(book, "600000", "TIMEOUT")
    l4_tasks.claim(book, "600000")
    l4_tasks.success(book, "600000")
    events = read_events(handle)
    assert [e["event_type"] for e in events if e.get("subject") == "600000"] == [
        "TASK_RETRY_SCHEDULED", "TASK_CLAIMED", "TASK_SUCCEEDED"
    ]
    assert events[0]["payload"]["error_class"] == "TIMEOUT"


def test_agent_boundary_records_role_subject_and_attempt(tmp_path, monkeypatch):
    handle = begin_fixture_run(tmp_path, monkeypatch)
    record_agent_boundary(handle.run_id, "AGENT_DISPATCHED", role="l4-card",
                          subject="600000", invocation_id="agent-l4-card-600000-2", attempt=2)
    event = read_events(handle)[-1]
    assert (event["payload"]["role"], event["subject"], event["attempt"]) == ("l4-card", "600000", 2)
```

- [ ] **Step 2: Confirm red, then implement event mapping**

Map task book transitions as follows: claim → `TASK_CLAIMED`; transient failure with another try → `TASK_RETRY_SCHEDULED`; terminal non-transient → `TASK_BLOCKED`; exhausted failure → `TASK_FAILED`; success → `TASK_SUCCEEDED`. The event payload includes old/new status, error class, task-book hash, prompt/slim/card hashes. Calls are best-effort and only active with a run ID.

Add `capsule agent-event` CLI and wrap every workflow `agent(...)` call with a dispatch event before the call and completed/failed event after it. This boundary is the authoritative `(run_id, invocation_id, role, subject, attempt)` binding later consumed by transcript adapters.

- [ ] **Step 3: Verify and commit**

```bash
export AUTORESEARCH_ENGINE=codex
uv run --no-sync python -m pytest tests/scan/test_l4_tasks.py tests/scan/test_l4_tasks_running_visibility.py tests/scan/test_forensic_workflow.py tests/scan/test_workflow_syntax.py -q
git add autoresearch/scan/l4_tasks.py tests/scan/test_l4_tasks.py tests/scan/test_l4_tasks_running_visibility.py .claude/workflows/l4-stock.js
git commit -m "feat(trace): retain l4 retries and agent boundaries"
```

### Task 7: Snapshot executable identity and enforce secret redaction

**Files:**

- Create: `autoresearch/trace/identity.py`
- Create: `tests/trace/test_identity.py`
- Modify: `autoresearch/trace/capsule.py`
- Modify: `pyproject.toml`
- Modify: `uv.lock`

- [ ] **Step 1: Write identity and leakage tests**

```python
def test_identity_contains_dirty_patch_untracked_sources_and_environment(tmp_path, monkeypatch):
    repo = make_dirty_repo(tmp_path)
    out = tmp_path / "identity"
    result = snapshot_identity(repo, out, engine="codex")
    assert (out / "code.patch").read_text(encoding="utf-8")
    assert (out / "untracked_sources.tar.zst").stat().st_size > 0
    manifest = json.loads((out / "source_manifest.json").read_text())
    assert "autoresearch/new_rule.py" in manifest["untracked"]
    assert json.loads((out / "environment.json").read_text())["engine"] == "codex"
    assert (out / "dependencies.txt").is_file()


def test_secret_values_never_enter_identity_or_redacted_transcript(tmp_path, monkeypatch):
    token = "sk-live-abcdefghijklmnopqrstuvwxyz123456"
    monkeypatch.setenv("TUSHARE_TOKEN", token)
    result = redact_value({"authorization": f"Bearer {token}", "text": token})
    assert token not in json.dumps(result.value)
    assert result.hits >= 2
    assert scan_for_secrets(json.dumps(result.value).encode())["ok"] is True
```

- [ ] **Step 2: Confirm red, implement exact snapshot scope**

Capture:

- `git rev-parse HEAD`, branch, `git status --porcelain`;
- `git diff --binary --no-ext-diff HEAD` into `code.patch`;
- untracked files only under `autoresearch/`, `.claude/agents/`, `.claude/skills/`, `.claude/workflows/` plus root `pyproject.toml`, `uv.lock`, `AGENTS.md`, `CLAUDE.md` into a deterministic tar (sorted names, uid/gid/mtime zero) compressed with `zstandard`;
- all files under agent/skill/workflow roots into `identity/prompts/`, plus SHA-256 source manifest;
- Python/uv/OS/architecture/timezone/locale, engine/model/effort/service tier when provided;
- `uv pip freeze`, and hashes of `pyproject.toml` / `uv.lock`;
- secret variables as `{present: bool}` only.

Add `zstandard>=0.23` as a direct project dependency and refresh `uv.lock`; do not rely on an unrelated transitive dependency to keep deterministic tar.zst support installed.

Redaction must recursively replace values for key names matching `token|secret|password|authorization|cookie|api[_-]?key` and bearer/key-like string patterns with `[REDACTED]`. A high-entropy scan runs over every identity/transcript artifact before finalization.

- [ ] **Step 3: Call identity snapshot inside `begin_run()` before `RUN_STARTED` completes**

If identity capture partially fails, write the successful pieces, emit `EVIDENCE_MISSING`, and keep state `ACTIVE/PENDING`; do not delete the run.

- [ ] **Step 4: Verify and commit**

```bash
export AUTORESEARCH_ENGINE=codex
uv run --no-sync python -m pytest tests/trace/test_identity.py tests/trace/test_capsule.py -q
git add autoresearch/trace/identity.py autoresearch/trace/capsule.py tests/trace/test_identity.py tests/trace/test_capsule.py pyproject.toml uv.lock
git commit -m "feat(trace): snapshot executable run identity"
```

### Task 8: Add content-addressed blobs and exact `get_or_fetch` lineage

**Files:**

- Create: `autoresearch/trace/blobs.py`
- Create: `autoresearch/trace/source_lineage.py`
- Create: `tests/trace/test_blobs.py`
- Create: `tests/trace/test_source_lineage.py`
- Modify: `autoresearch/data/cache.py`
- Modify: `tests/data/test_cache.py`
- Modify: `autoresearch/scan/retention.py`
- Modify: `tests/scan/test_retention.py`

- [ ] **Step 1: Write blob and all cache-path tests**

```python
@pytest.mark.parametrize("case,expected", [
    ("hit", "CACHE_HIT"),
    ("fetch_write", "FETCHED_CACHED"),
    ("live", "FETCHED_LIVE"),
    ("unsettled", "FETCHED_UNSETTLED"),
    ("refused", "FETCHED_REFUSED_LAKE"),
])
def test_get_or_fetch_records_exact_access(case, expected, traced_cache_case):
    df, rows, capsule = traced_cache_case(case)
    assert rows[-1]["access"] == expected
    assert rows[-1]["rows"] == len(df)
    assert rows[-1]["status"] == "SUCCEEDED"
    if rows[-1]["blob_hash"]:
        assert blob_path(capsule, rows[-1]["blob_hash"]).is_file()


def test_get_or_fetch_records_contract_exception(traced_cache_failure):
    with pytest.raises(DataContractError):
        traced_cache_failure.run()
    row = traced_cache_failure.rows()[-1]
    assert row["status"] == "FAILED"
    assert row["error_type"] == "DataContractError"


def test_blob_store_deduplicates_identical_content(tmp_path):
    first = put_bytes(tmp_path, b"same")
    second = put_bytes(tmp_path, b"same")
    assert first == second
    assert len(list((tmp_path / "blobs/sha256").rglob("*"))) == 2
```

- [ ] **Step 2: Confirm red**

```bash
export AUTORESEARCH_ENGINE=codex
uv run --no-sync python -m pytest tests/trace/test_blobs.py tests/trace/test_source_lineage.py tests/data/test_cache.py -q
```

- [ ] **Step 3: Implement blob storage and source event schema**

Blob path is `capsule/blobs/sha256/<first-two>/<full-hash>`. Write with `O_EXCL` temp + `replace`, verify existing blob bytes on collision, chmod `0600`.

`trace_access()` writes `lineage/reads.jsonl` under a lock and emits a matching `SOURCE_READ`, `SOURCE_FETCHED`, or `SOURCE_FAILED` event. Normalize params by sorting keys and redacting secret-like values. Record endpoint, policy key/settle, exact path, hash, bytes, rows, columns hash, access, invocation/stage, start/end, status/error.

For DataFrames without a lake file, serialize a deterministic parquet blob after contract validation. Always blob `static`, `live`, snapshot and refused-lake reads; blob date/as_of cache reads as well in Phase 1 so replay is self-contained.

- [ ] **Step 4: Instrument every return/raise path in `get_or_fetch()`**

Use one context object at function entry and call `finish_success(df, access, path)` immediately before each return. Wrap the full body in `try/except BaseException` and call `finish_failure(exc)` before re-raising. If no active run ID, tracing is a no-op and existing cache behavior stays byte-compatible.

Derive legacy `trace/lake_manifest.json` from successful `reads.jsonl` rows. Add the missing `write_lake_manifest()` call to legacy `retain()` now, but label its schema `exact_reads` only when lineage exists and `window_guess` otherwise.

- [ ] **Step 5: Verify and commit**

```bash
export AUTORESEARCH_ENGINE=codex
uv run --no-sync python -m pytest tests/trace/test_blobs.py tests/trace/test_source_lineage.py tests/data/test_cache.py tests/scan/test_retention.py -q
git add autoresearch/trace/blobs.py autoresearch/trace/source_lineage.py autoresearch/data/cache.py autoresearch/scan/retention.py tests/trace/test_blobs.py tests/trace/test_source_lineage.py tests/data/test_cache.py tests/scan/test_retention.py
git commit -m "feat(trace): capture exact source lineage and blobs"
```

## Phase C — Engine-aware transcript evidence and truthful usage

### Task 9: Define the transcript protocol and preserve Claude behavior

**Files:**

- Create: `autoresearch/trace/transcripts/base.py`
- Create: `autoresearch/trace/transcripts/claude.py`
- Create: `autoresearch/trace/transcripts/__init__.py`
- Create: `tests/trace/fixtures/claude/agent-l4-card.jsonl`
- Create: `tests/trace/test_transcript_adapters.py`
- Modify: `autoresearch/trace/usage_harvest.py`
- Modify: `tests/trace/test_usage_harvest.py`

- [ ] **Step 1: Extract a minimal sanitized Claude fixture and write parity tests**

The fixture contains duplicate streaming usage for one message ID, one tool request/result, an API error, a later terminal response, model/effort/agent attribution, and no real prompt content.

```python
def test_claude_adapter_preserves_usage_dedup_and_retry_status(claude_ref):
    normalized = ClaudeTranscriptAdapter().normalize(claude_ref)
    usage = ClaudeTranscriptAdapter().usage(claude_ref)
    assert usage.messages == 2
    assert usage.status == "RETRIED_SUCCEEDED"
    assert usage.agent == "l4-card"
    assert [item.kind for item in normalized.items] == ["message", "tool_request", "tool_result", "error", "message"]


def test_adapter_registry_selects_by_engine():
    assert isinstance(adapter_for("claude"), ClaudeTranscriptAdapter)
    with pytest.raises(ValueError, match="unsupported engine"):
        adapter_for("unknown")
```

- [ ] **Step 2: Confirm red, then move Claude parsing behind the protocol**

Define immutable `RunIdentity`, `TranscriptRef`, `NormalizedItem`, `NormalizedTranscript`, `UsageRecord`, and a `TranscriptAdapter` Protocol with `locate`, `normalize`, `usage`.

Move `_iter_rows`, `_meta_agent`, current message-id dedup/status logic into `ClaudeTranscriptAdapter`; keep old `usage_of`, `collect`, `collect_session` as compatibility wrappers that construct a Claude ref and convert the dataclass to the old dict schema.

- [ ] **Step 3: Verify exact legacy parity and commit**

```bash
export AUTORESEARCH_ENGINE=codex
uv run --no-sync python -m pytest tests/trace/test_transcript_adapters.py tests/trace/test_usage_harvest.py -q
git add autoresearch/trace/transcripts autoresearch/trace/usage_harvest.py tests/trace/fixtures/claude tests/trace/test_transcript_adapters.py tests/trace/test_usage_harvest.py
git commit -m "refactor(trace): put claude transcripts behind adapter"
```

### Task 10: Implement Codex rollout binding, normalization, archival, and usage

**Files:**

- Create: `autoresearch/trace/transcripts/codex.py`
- Create: `tests/trace/fixtures/codex/rollout.jsonl`
- Modify: `autoresearch/trace/transcripts/__init__.py`
- Modify: `autoresearch/trace/capsule.py`
- Modify: `autoresearch/trace/usage_harvest.py`
- Modify: `tests/trace/test_transcript_adapters.py`
- Modify: `tests/trace/test_usage_harvest.py`

- [ ] **Step 1: Add sanitized Codex schema fixture and binding tests**

Fixture rows must include `session_meta`, `turn_context` with model/effort, visible `response_item` messages, `custom_tool_call`, `custom_tool_call_output`, an error event, and multiple `event_msg.payload.type=token_count` cumulative snapshots.

```python
def test_codex_usage_uses_last_cumulative_snapshot_not_sum(codex_ref):
    usage = CodexTranscriptAdapter().usage(codex_ref)
    assert usage.model == "gpt-5.6-sol"
    assert usage.effort == "high"
    assert usage.input == 207681
    assert usage.cache_read == 200448
    assert usage.output == 1671
    assert usage.reasoning_output == 1119


@pytest.mark.parametrize("candidates,status", [([], "GONE"), (["a", "b"], "AMBIGUOUS")])
def test_codex_locator_never_guesses_latest_mtime(tmp_path, candidates, status):
    refs = locate_candidates(make_identity(tmp_path), [tmp_path / name for name in candidates])
    assert refs[0].status == status
    assert all(ref.status != "PRESENT" for ref in refs)


def test_explicit_binding_is_authoritative(tmp_path, monkeypatch):
    handle = begin_fixture_run(tmp_path, monkeypatch)
    source = copy_fixture("codex/rollout.jsonl", tmp_path)
    bind_transcript(handle.run_id, source, role="l4-card", subject="600000",
                    invocation_id="agent-l4-card-600000-1")
    refs = CodexTranscriptAdapter().locate(identity_for(handle))
    assert [(r.status, r.role, r.subject) for r in refs] == [("PRESENT", "l4-card", "600000")]


def test_visible_tool_calls_are_indexed_and_results_are_blobbed(tmp_path, monkeypatch):
    handle = begin_fixture_run(tmp_path, monkeypatch)
    source = copy_fixture("codex/rollout.jsonl", tmp_path)
    bind_transcript(handle.run_id, source, role="l4-intel", subject="600000",
                    invocation_id="agent-l4-intel-600000-1")
    materialize_transcripts(handle.run_id)
    rows = read_jsonl(handle.capsule / "lineage/external_tools.jsonl")
    assert rows[0]["tool_name"] == "web.search_query"
    assert rows[0]["capture_level"] == "HARNESS_RESPONSE"
    assert blob_path(handle.capsule, rows[0]["result_hash"]).is_file()
```

- [ ] **Step 2: Confirm red, implement Codex normalization**

Use the last `token_count.info.total_token_usage` per bound role segment for total usage, or the delta between role-boundary start/end cumulative snapshots when a session contains multiple roles. Never sum cumulative snapshots. Map `cached_input_tokens` to `cache_read`, `cache_write_input_tokens` to `cache_create`, and keep `reasoning_output_tokens` separate.

Normalize only visible messages and tool call/result payloads. Drop `encrypted_content` and model-internal reasoning. Apply deterministic redaction before writing `agents/raw/*.jsonl.gz`; gzip uses `mtime=0`. Write one row per expected invocation into `agents/index.json` with `PRESENT/GONE/AMBIGUOUS/UNSUPPORTED/NOT_EXPECTED`.

For every visible tool request/result pair, append `lineage/external_tools.jsonl` with tool name, redacted request, timestamps, status, owning role/stage/invocation, response blob hash, URL/title when present, and `capture_level=HARNESS_RESPONSE`. Never label a harness summary as raw webpage content. A request without a result remains an explicit failed/incomplete row.

- [ ] **Step 3: Add explicit bind and engine-aware harvest CLI**

Commands:

```text
python -m autoresearch.trace.capsule bind-transcript <run_id> <path> --role l4-card --subject 600000 --invocation-id agent-l4-card-600000-1
python -m autoresearch.trace.usage_harvest --engine codex --run-id <run_id> --out <md> --json-out <json>
```

When usage cannot be parsed, ledger row status is `UNMEASURED`, `estimated_usd` is `null`, and Markdown prints `— (UNMEASURED)`. A zero-token, successfully parsed transcript may print `$0.0000`; missing/unparsed evidence may not.

- [ ] **Step 4: Verify and commit**

```bash
export AUTORESEARCH_ENGINE=codex
uv run --no-sync python -m pytest tests/trace/test_transcript_adapters.py tests/trace/test_usage_harvest.py -q
git add autoresearch/trace/transcripts/codex.py autoresearch/trace/transcripts/__init__.py autoresearch/trace/capsule.py autoresearch/trace/usage_harvest.py tests/trace/fixtures/codex tests/trace/test_transcript_adapters.py tests/trace/test_usage_harvest.py
git commit -m "feat(trace): archive and meter codex transcripts"
```

### Task 11: Materialize every expected agent invocation

**Files:**

- Modify: `autoresearch/trace/capsule.py`
- Modify: `.claude/workflows/scan-market.js`
- Modify: `.claude/workflows/l4-stock.js`
- Modify: `tests/scan/test_forensic_workflow.py`
- Modify: `tests/trace/test_transcript_adapters.py`

- [ ] **Step 1: Write coverage tests for strategist, sector, L3, card, intel, and ensemble**

```python
def test_agent_index_has_one_explicit_row_per_reached_invocation(full_agent_event_fixture):
    index = materialize_agent_index(full_agent_event_fixture)
    keys = {(row["role"], row["subject"], row["attempt"]) for row in index["invocations"]}
    assert ("strategist", None, 1) in keys
    assert ("sector-brief", "银行", 1) in keys
    assert ("l3-rank", None, 1) in keys
    assert ("l4-card", "600000", 1) in keys
    assert ("l4-intel", "600000", 1) in keys
    assert index["coverage"] == {"expected": 5, "present": 5, "missing": 0}
```

- [ ] **Step 2: Make workflow invocation IDs deterministic and collision-free**

Use `<role>-<subject-or-market>-<attempt>` for agent invocations and emit both boundaries even on `.catch()`. Sector subjects use a SHA-256-derived 12-character safe key plus the original sector in payload. Ensemble reviewers include reviewer ordinal.

At CP7, materialize index from dispatch events and explicit transcript bindings. A reached dispatch without bound/locatable transcript is `GONE` or `AMBIGUOUS`; a configuration-disabled leg is `NOT_EXPECTED`; a failed dispatch still needs a row.

- [ ] **Step 3: Verify and commit**

```bash
export AUTORESEARCH_ENGINE=codex
uv run --no-sync python -m pytest tests/scan/test_forensic_workflow.py tests/trace/test_transcript_adapters.py tests/scan/test_workflow_syntax.py -q
git add autoresearch/trace/capsule.py .claude/workflows/scan-market.js .claude/workflows/l4-stock.js tests/scan/test_forensic_workflow.py tests/trace/test_transcript_adapters.py
git commit -m "feat(trace): account for every agent invocation"
```

## Phase D — Expected evidence, completeness, deterministic replay

### Task 12: Define scan `RunProfile` and expected evidence

**Files:**

- Create: `autoresearch/scan/run_profile.py`
- Create: `tests/scan/test_run_profile.py`
- Create: `autoresearch/trace/completeness.py`
- Create: `tests/trace/test_completeness.py`

- [ ] **Step 1: Write full/sentinel/failed/interrupted and mutation tests**

```python
@pytest.mark.parametrize("mode,expected_l4", [
    ("FULL", "REQUIRED"),
    ("FORCED_FULL", "REQUIRED"),
    ("SENTINEL_EMPTY", "NOT_EXPECTED"),
])
def test_expected_evidence_is_mode_aware(mode, expected_l4, profile_fixture):
    expected = build_expected(profile_fixture(mode=mode))
    assert expected.rule("agents/l4-card/*").disposition == expected_l4


def test_failed_run_requires_failure_evidence_and_marks_downstream_not_reached(profile_fixture):
    expected = build_expected(profile_fixture(business_status="FAILED", last_stage="l3"))
    assert expected.rule("stages/l3/*/result.json").disposition == "REQUIRED"
    assert expected.rule("logs/l3/*.stderr.log.gz").disposition == "REQUIRED"
    assert expected.rule("stages/l4/*").disposition == "NOT_REACHED"


def test_deleting_required_transcript_fails_completeness(complete_capsule):
    assert evaluate(complete_capsule)["completeness_ok"] is True
    required = next((complete_capsule / "agents/raw").glob("*.jsonl.gz"))
    required.unlink()
    result = evaluate(complete_capsule)
    assert result["completeness_ok"] is False
    assert str(required.relative_to(complete_capsule)) in result["missing_required"]
```

- [ ] **Step 2: Confirm red, then implement declarative rules**

Define:

```python
@dataclass(frozen=True)
class ArtifactRule:
    key: str
    selector: str
    source: str
    required_when: str


@dataclass(frozen=True)
class RunProfile:
    kind: str
    expected_stages: tuple[str, ...]
    agent_roles: tuple[str, ...]
    artifact_rules: tuple[ArtifactRule, ...]
    replayable_stages: tuple[str, ...]
```

The scan profile must explicitly cover identity, commands/logs, L0–L5 outputs, strategist/sector/L3/L4/intel/ensemble events, exact A-grade source reads, truthful usage, published products, decision records, replay, and failure evidence. `expected.json` stores every expanded item with `REQUIRED/PRESENT/MISSING/NOT_EXPECTED/NOT_REACHED` and a reason.

`completeness.json` separately reports required/present/missing/not_expected/not_reached counts; agent/source/log/replay coverage; durability; `completeness_ok`. It does not call MANIFEST verification.

- [ ] **Step 3: Verify and commit**

```bash
export AUTORESEARCH_ENGINE=codex
uv run --no-sync python -m pytest tests/scan/test_run_profile.py tests/trace/test_completeness.py -q
git add autoresearch/scan/run_profile.py autoresearch/trace/completeness.py tests/scan/test_run_profile.py tests/trace/test_completeness.py
git commit -m "feat(trace): evaluate mode aware evidence completeness"
```

### Task 13: Replay deterministic stages from capsule blobs only

**Files:**

- Create: `autoresearch/trace/replay.py`
- Create: `tests/trace/test_replay.py`
- Modify: `autoresearch/data/cache.py`
- Modify: `tests/data/test_cache.py`
- Modify: `autoresearch/trace/capsule.py`

- [ ] **Step 1: Write no-network/no-original-path and hash-parity tests**

```python
def test_replay_provider_reads_only_capsule_blobs(replay_capsule, monkeypatch):
    monkeypatch.setattr(sources, "fetch", lambda *a, **k: pytest.fail("network called"))
    shutil.rmtree(replay_capsule.original_staging)
    result = replay(replay_capsule.run_id, stages=("l0", "l1", "l2", "l5"))
    assert result["replayability"] == "FULL"
    assert all(row["match"] for row in result["stages"])
    assert Path(result["scratch"]).is_dir()
    assert not any(str(replay_capsule.report_dir) in row["output_path"] for row in result["stages"])


def test_missing_blob_yields_partial_with_exact_gap(replay_capsule):
    missing = replay_capsule.remove_source_blob("daily", "20260825")
    result = replay(replay_capsule.run_id, stages=("l0", "l1", "l2"))
    assert result["replayability"] == "PARTIAL"
    assert missing in result["missing_blobs"]
```

- [ ] **Step 2: Add an explicit replay source provider**

When `AUTORESEARCH_REPLAY_CAPSULE` is set, `get_or_fetch()` resolves normalized `(endpoint, params)` through frozen `reads.jsonl` and reads the referenced parquet blob. A miss raises `ReplayInputMissing`; it never falls back to lake or network. Production behavior without the variable remains unchanged.

- [ ] **Step 3: Implement scratch replay orchestration**

Create scratch with `tempfile.mkdtemp(prefix=f"autoresearch-replay-{run_id}-")`, copy only required config/prompt/business inputs, set replay env, and invoke the existing deterministic module entrypoints through `exec_capture` in replay mode. Hash CSV canonically by parsed rows/columns and JSON by canonical JSON; hash Markdown byte-for-byte after normalizing only generated timestamps explicitly listed in a per-stage normalizer.

Write `verification/replay.json` with `FULL/PARTIAL/NONE`; LLM stages are always `EVIDENCE_ONLY` and are not executed.

- [ ] **Step 4: Verify and commit**

```bash
export AUTORESEARCH_ENGINE=codex
uv run --no-sync python -m pytest tests/trace/test_replay.py tests/data/test_cache.py -q
git add autoresearch/trace/replay.py autoresearch/data/cache.py autoresearch/trace/capsule.py tests/trace/test_replay.py tests/data/test_cache.py
git commit -m "feat(trace): replay deterministic stages from frozen blobs"
```

## Phase E — Finalization, integrity root, recovery, repair, publishing UI

### Task 14: Finalize with MANIFEST, detached root ledger, and local archive

**Files:**

- Modify: `autoresearch/trace/capsule.py`
- Modify: `autoresearch/trace/atomic.py`
- Create: `tests/trace/test_finalization.py`

- [ ] **Step 1: Write idempotence and tamper tests**

```python
def test_finalize_is_idempotent_and_ledger_is_append_only(finalizable_run):
    first = finalize(finalizable_run.run_id, BusinessStatus.SUCCEEDED, finalizable_run.report_dir)
    second = finalize(finalizable_run.run_id, BusinessStatus.SUCCEEDED, finalizable_run.report_dir)
    assert first.root_hash == second.root_hash
    rows = read_valid_ledger(finalizable_run.ledger)
    assert len(rows) == 1
    assert rows[0]["revision"] == 1


def test_rewriting_manifest_cannot_hide_tampering(finalized_run):
    victim = finalized_run.report_dir / "summary.md"
    victim.write_text("tampered", encoding="utf-8")
    write_manifest(finalized_run.report_dir)
    result = verify(finalized_run.run_id)
    assert result["integrity_ok"] is False
    assert result["root_ledger_ok"] is False


def test_final_archive_is_self_contained_and_local_only(finalized_run):
    archive = finalized_run.archive
    assert archive.name.endswith(".tar.zst")
    assert archive.stat().st_size > 0
    assert verify_archive(archive, finalized_run.root_hash)["ok"] is True
    assert finalized_run.result.durability == "LOCAL_ONLY"
```

- [ ] **Step 2: Implement the fixed finalization order**

For success, copy/move the capsule into the existing report dir; for failure/interruption freeze into `$RPT/scan/_failed/<run_id>`. Then:

1. materialize transcripts and usage;
2. write expected/completeness/replay;
3. write `capsule.json` and terminal state;
4. write MANIFEST excluding `verification/MANIFEST.sha256` and `verification/ROOT.json`;
5. compute root as SHA-256 of MANIFEST bytes;
6. write ROOT with root, manifest hash, event-chain tail, completeness hash;
7. build deterministic `$RPT/scan/_capsule_archive/<run_id>.tar.zst` outside the report directory and compute its hash;
8. append one locked ledger revision containing root and archive hash, previous row hash, then its own final row hash;
9. chmod files `0444`, dirs `0555` after all writes;
10. reopen and verify everything.

The ledger path is `reports_codex/scan/_ledger/run_capsules.jsonl`. Duplicate `(run_id, revision, root_hash)` is a no-op. Ledger validation rejects revision gaps and broken `prev_hash`.

If archive creation fails, catch it before the ledger append, emit `EVIDENCE_MISSING`, set evidence/durability to `EVIDENCE_INCOMPLETE/ARCHIVE_FAILED`, regenerate completeness → MANIFEST → ROOT because those files changed, then append a ledger row with `archive_hash=null` and the failure class. The business report remains in place and the spool remains available for repair.

- [ ] **Step 3: Verify and commit**

```bash
export AUTORESEARCH_ENGINE=codex
uv run --no-sync python -m pytest tests/trace/test_finalization.py tests/trace/test_events.py tests/trace/test_completeness.py -q
git add autoresearch/trace/capsule.py autoresearch/trace/atomic.py tests/trace/test_finalization.py
git commit -m "feat(trace): freeze capsules with detached integrity roots"
```

### Task 15: Recover stale runs and add append-only repair overlays

**Files:**

- Modify: `autoresearch/trace/capsule.py`
- Create: `tests/trace/test_recovery.py`
- Create: `tests/trace/test_repairs.py`
- Modify: `autoresearch/scan/prelude.py`
- Modify: `autoresearch/scan/prewarm.py`

- [ ] **Step 1: Write stale/PID-reuse/SIGTERM/repair tests**

```python
def test_recover_stale_active_run_as_interrupted(stale_run, monkeypatch):
    monkeypatch.setattr(process_probe, "matches", lambda lease: False)
    results = recover_stale_runs(now=stale_run.heartbeat + timedelta(minutes=6),
                                 stale_after=timedelta(minutes=5))
    assert results[0].business_status == "INTERRUPTED"
    failure = json.loads((results[0].final_path / "failure.json").read_text())
    assert failure["last_reliable_checkpoint"] == "l3"


def test_live_pid_with_different_start_time_is_stale(stale_run, monkeypatch):
    monkeypatch.setattr(process_probe, "pid_exists", lambda pid: True)
    monkeypatch.setattr(process_probe, "started_at", lambda pid: stale_run.process_started + 10)
    assert lease_is_live(stale_run.lease) is False


def test_repair_writes_overlay_and_preserves_base_root(incomplete_finalized_run):
    base = incomplete_finalized_run.root_hash
    result = repair(incomplete_finalized_run.run_id, reason="transcript restored")
    assert result.revision == 2
    assert result.base_root_hash == base
    assert result.composite_root_hash != base
    assert incomplete_finalized_run.root_hash == base
    assert result.overlay_path.name == "revision-2"
```

- [ ] **Step 2: Implement lease heartbeat and recovery**

`state.json` contains hostname, pid, process start time, heartbeat, current invocation. `exec_capture` refreshes heartbeat at most every 30 seconds while a child runs. `recover_stale_runs()` only marks stale after age threshold and failed PID+start-time identity. It appends `RUN_INTERRUPTED`, checkpoints current spool, finalizes to `_failed`, and never removes the source workspace when finalization is incomplete.

Call recovery at the very start of `prelude.run()` and `prewarm.run()`, before new fetches. Errors print a warning and do not alter the new run.

- [ ] **Step 3: Implement repair overlays**

`finalize --repair <run_id> --reason <text>` can add only files absent from the base view. It writes `$RPT/scan/_repairs/<run_id>/revision-N/`, its own MANIFEST, and a composite ROOT/ledger revision referencing base root + all overlay roots. Any collision with an existing base path or attempt to alter base bytes fails.

- [ ] **Step 4: Verify and commit**

```bash
export AUTORESEARCH_ENGINE=codex
uv run --no-sync python -m pytest tests/trace/test_recovery.py tests/trace/test_repairs.py tests/scan/test_prelude.py tests/scan/test_prewarm.py -q
git add autoresearch/trace/capsule.py autoresearch/scan/prelude.py autoresearch/scan/prewarm.py tests/trace/test_recovery.py tests/trace/test_repairs.py
git commit -m "feat(trace): recover interrupted runs with repair overlays"
```

### Task 16: Switch L5/CP7 to the forensic finalizer and correct all UI semantics

**Files:**

- Modify: `autoresearch/scan/publisher.py`
- Modify: `autoresearch/scan/post_run.py`
- Modify: `autoresearch/scan/retention.py`
- Modify: `autoresearch/scan/chain_view.py`
- Modify: `autoresearch/scan/health.py`
- Modify: `tests/scan/test_publisher_health_snapshot.py`
- Modify: `tests/scan/test_post_run.py`
- Modify: `tests/scan/test_retention.py`
- Modify: `tests/scan/test_chain_view.py`

- [ ] **Step 1: Write false-green regression tests**

```python
def test_chain_view_never_calls_manifest_integrity_scene_completeness(run_with_missing_transcript):
    rendered = render_chain(run_with_missing_transcript.report_dir, "600000")
    assert "integrity PASS" in rendered
    assert "completeness FAIL" in rendered
    assert "现场完整性 ✓" not in rendered
    assert "l4-card transcript" in rendered


def test_successful_business_report_survives_archive_failure(publish_fixture, monkeypatch):
    monkeypatch.setattr(capsule, "build_archive", lambda *a, **k: (_ for _ in ()).throw(OSError("disk full")))
    summary = publisher.run(DATE, scan_dir=publish_fixture.scan, out_root=publish_fixture.reports)
    assert summary.is_file()
    evidence = json.loads((summary.parent / "capsule/verification/completeness.json").read_text())
    assert evidence["completeness_ok"] is False
    assert evidence["durability"] == "ARCHIVE_FAILED"
```

- [ ] **Step 2: Enforce the CP7 order**

Publisher remains responsible for business report assembly only. Post-run performs: gate4 → engine-aware usage → usage reconcile → observe → expected → replay → completeness → finalize → post-final verify. On an unrecoverable business exception before publisher returns, the controlling CLI invokes `finalize(..., FAILED)` with error metadata.

Wrap the main body of `scan-market.js` in a top-level `try/catch` after `RUN_ID` is validated. The catch calls `capsule finalize <run_id> --business-status FAILED --error-json <redacted-json>` through a captured deterministic command, logs its failure without hiding the original exception, then rethrows. `l4-stock.js` records per-stock failure events but does not finalize the whole run; the parent orchestration owns that business decision. SIGKILL remains the stale-recovery path.

`manifest.json` gains `capsule_schema_version`, `business_status`, `evidence_status`; ROOT stays outside its own manifest cycle. Frozen success runs receive no later in-place writes. Outcome ledger remains external and links by run ID.

- [ ] **Step 3: Downgrade old retention to an explicit compatibility adapter**

For active forensic runs, `retain()` delegates to capsule checkpoint/finalize preparation and returns both integrity/completeness state. For old runs without RunContract v3 it keeps mirror/snapshot/MANIFEST behavior and labels evidence `LEGACY_PARTIAL`. Never rewrite historical MANIFEST files.

- [ ] **Step 4: Render six separate evidence facts**

`chain_view` and `index.md` show: business status, evidence status, integrity, completeness, replayability, durability, dirty patch presence, and agent/source/log coverage. Green “现场可复盘” requires `business_status=SUCCEEDED`, `evidence_status=COMPLETE`, `integrity_ok=true`, `completeness_ok=true`, and replay not `NONE`.

- [ ] **Step 5: Verify and commit**

```bash
export AUTORESEARCH_ENGINE=codex
uv run --no-sync python -m pytest tests/scan/test_publisher_health_snapshot.py tests/scan/test_post_run.py tests/scan/test_retention.py tests/scan/test_chain_view.py tests/trace/test_finalization.py -q
git add autoresearch/scan/publisher.py autoresearch/scan/post_run.py autoresearch/scan/retention.py autoresearch/scan/chain_view.py autoresearch/scan/health.py tests/scan/test_publisher_health_snapshot.py tests/scan/test_post_run.py tests/scan/test_retention.py tests/scan/test_chain_view.py
git commit -m "feat(scan): publish truthful forensic evidence status"
```

## Phase F — Fault injection, documentation, and production acceptance

### Task 17: Add the twelve-scenario integration fault matrix

**Files:**

- Create: `tests/integration/test_scan_capsule_faults.py`
- Create: `tests/integration/fixtures/scan_capsule/`

- [ ] **Step 1: Build a deterministic mini-scan harness**

The harness uses a 3-stock synthetic universe, fixture parquet blobs, stub transcript adapters, and real capsule/exec/finalize paths. It must not call Tushare, web, Claude, or Codex.

- [ ] **Step 2: Parameterize all required fault points**

```python
@pytest.mark.parametrize("scenario,business,evidence,last_stage", [
    ("success_full", "SUCCEEDED", "COMPLETE", "gate4"),
    ("prelude_child_fail_continue", "SUCCEEDED", "COMPLETE", "gate4"),
    ("l3_fail_repair_success", "SUCCEEDED", "COMPLETE", "gate4"),
    ("l4_transient_retry", "SUCCEEDED", "COMPLETE", "gate4"),
    ("l4_blocked", "SUCCEEDED", "COMPLETE", "gate4"),
    ("after_l3_before_l4_prompt", "FAILED", "COMPLETE", "l3"),
    ("sigterm_before_assemble", "INTERRUPTED", "COMPLETE", "l4"),
    ("sigkill_then_recover", "INTERRUPTED", "COMPLETE", "l4"),
    ("same_date_second_run", "SUCCEEDED", "COMPLETE", "gate4"),
    ("archive_write_fail", "SUCCEEDED", "EVIDENCE_INCOMPLETE", "gate4"),
    ("published_file_mutated", "SUCCEEDED", "EVIDENCE_INCOMPLETE", "gate4"),
    ("required_missing_before_manifest", "SUCCEEDED", "EVIDENCE_INCOMPLETE", "gate4"),
])
def test_fault_matrix(mini_scan, scenario, business, evidence, last_stage):
    result = mini_scan.run(scenario)
    assert result.business_status == business
    assert result.evidence_status == evidence
    assert result.last_reliable_checkpoint == last_stage
    assert result.failure_classification_is_explicit
```

- [ ] **Step 3: Add cross-run immutability assertions**

The same-date scenario must assert distinct run directories and unchanged first root after second completion. Outcome backfill must not change either root. Deleting a required transcript after freeze must fail integrity and completeness independently.

- [ ] **Step 4: Verify and commit**

```bash
export AUTORESEARCH_ENGINE=codex
uv run --no-sync python -m pytest tests/integration/test_scan_capsule_faults.py -q
git add tests/integration/test_scan_capsule_faults.py tests/integration/fixtures/scan_capsule
git commit -m "test(trace): inject scan capsule lifecycle faults"
```

### Task 18: Update operator workflow, run full verification, and conduct one real Codex acceptance run

**Files:**

- Modify: `.claude/skills/scan-market/SKILL.md`
- Modify: `.claude/skills/scan-market/STAGES.md`
- Modify: `CLAUDE.md`
- Modify: `docs/superpowers/specs/2026-08-27-scan-forensic-run-capsule-design.md`
- Create: `docs/research/2026-08-27-scan-forensic-capsule-acceptance.md`

- [ ] **Step 1: Document the exact production sequence**

The skill must require:

```bash
export AUTORESEARCH_ENGINE=codex
RUN_JSON=$(uv run --no-sync python -m autoresearch.trace.capsule begin scan-market <date> --engine codex --config-file .claude/skills/scan-market/scan_config.jsonc)
RUN_ID=$(printf '%s' "$RUN_JSON" | jq -r .run_id)
```

Pass `run_id` to Workflow args. CP7 uses:

```bash
uv run --no-sync python -m autoresearch.trace.usage_harvest --engine codex --run-id "$RUN_ID" --out "$SD/token_usage.md" --json-out "$SD/_token_usage.json"
uv run --no-sync python -m autoresearch.trace.capsule finalize "$RUN_ID" --business-status SUCCEEDED --report-dir "$REPORT_DIR"
uv run --no-sync python -m autoresearch.trace.capsule verify "$RUN_ID"
```

Document bind-transcript, inspect, replay, recovery, and repair commands. Remove claims that retention is automatically “complete” merely because MANIFEST passes.

- [ ] **Step 2: Run static and targeted verification**

```bash
export AUTORESEARCH_ENGINE=codex
uv run --no-sync python -m pytest tests/trace tests/common/test_workspace.py tests/data/test_cache.py tests/scan/test_run_contract.py tests/scan/test_stage_result.py tests/scan/test_retention.py tests/scan/test_chain_view.py tests/scan/test_forensic_workflow.py tests/integration/test_scan_capsule_faults.py -q
uv run --no-sync python scripts/check_workflow_js.py .claude/workflows/scan-market.js .claude/workflows/l4-stock.js
```

Expected: zero failures; workflow syntax checker exits 0.

- [ ] **Step 3: Run the entire suite under the mandatory engine**

```bash
export AUTORESEARCH_ENGINE=codex
uv run --no-sync python -m pytest -q
```

Expected: zero failures. If an existing test hardcodes a Claude root but does not specifically test historical Claude compatibility, repair the fixture to use `ws.context_root()` and rerun the entire suite.

- [ ] **Step 4: Run one real Codex full scan and record evidence**

Use the current trading date only when Tushare EOD data is settled; otherwise use the most recent settled trading date already present in `lake/`. Do not loosen business gates to force BUY or full mode.

Acceptance document must record commands and machine outputs for:

- independent run-scoped staging;
- explicit agent index rows for strategist/L3/every L4/intel/ensemble reached;
- all A-grade lake reads present in exact lineage or explicitly uncovered;
- Codex usage measured or honestly `UNMEASURED`, never pseudo-zero;
- `integrity_ok=true`, `completeness_ok=true`, deterministic replay `FULL`;
- one copied capsule mutation producing integrity failure;
- one copied required-transcript deletion producing completeness failure;
- a same-date second synthetic run leaving first root unchanged;
- outcome backfill leaving root unchanged.

Never mutate the original frozen acceptance run for negative tests; clone it under a temporary test directory.

- [ ] **Step 5: Mark design implemented and commit docs**

The design status is already `已批准，待实施` in the plan commit. After real acceptance, change it to `已实施并验收` and add the acceptance document link.

```bash
git add .claude/skills/scan-market/SKILL.md .claude/skills/scan-market/STAGES.md CLAUDE.md docs/superpowers/specs/2026-08-27-scan-forensic-run-capsule-design.md docs/research/2026-08-27-scan-forensic-capsule-acceptance.md
git commit -m "docs(scan): operate and verify forensic run capsules"
git status --short
```

Expected: the implementation worktree is clean. The original user worktree may remain dirty with its pre-existing files; do not alter or clean them.

## Final review checklist

- [ ] `rg -n 'TODO|TBD|placeholder|NotImplemented|context_claude|reports_claude' autoresearch/trace autoresearch/common/workspace.py autoresearch/scan/run_profile.py tests/trace tests/integration/test_scan_capsule_faults.py` has no implementation placeholder and no accidental Claude-root dependency. Historical compatibility fixtures may contain `context_claude` with an explanatory test name.
- [ ] Delete/reorder/edit event mutations all fail chain verification.
- [ ] Delete a required artifact before MANIFEST generation makes completeness fail; MANIFEST cannot hide it.
- [ ] Rewrite MANIFEST after tampering still fails detached root verification.
- [ ] Run-scoped staging prevents same-date overwrite.
- [ ] Failed and interrupted runs exist under `_failed/<run_id>` with logs/error/last checkpoint.
- [ ] L4 retry success preserves prior errors and attempts.
- [ ] Transcript index has explicit rows for every reached expected agent; no mtime guessing.
- [ ] Codex cumulative usage is differenced/last-sampled, never summed; unmeasured is not `$0`.
- [ ] Exact lineage comes from actual read points and every referenced blob is inside capsule.
- [ ] Replay scratch never writes report, original staging, lake, or outcome ledger.
- [ ] Success/failure capsules are read-only; repairs are overlays with a new ledger revision.
- [ ] `chain_view`, `index.md`, CP7 separately report business/evidence/integrity/completeness/replay/durability.
- [ ] Full pytest passes with `AUTORESEARCH_ENGINE=codex`.
