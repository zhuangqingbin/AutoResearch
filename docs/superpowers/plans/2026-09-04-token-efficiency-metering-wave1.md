# Token Efficiency Metering Wave 1 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [x]`) syntax for tracking.

**Goal:** Deliver the measurement-first slice of the approved two-line design: reliable Claude transcript statistics, explicit panorama cohorts, session self-binding, and weighted run-budget bands without changing research selection behavior.

**Architecture:** Claude transcript parsing remains behind the existing adapter contract. `usage_harvest` remains the per-run usage authority, `usage_panorama` is a read-only aggregate view, and `budget.observe_run` remains the only owner of run health colors. The existing 09-03 relay/session fixes are imported as a separate commit before the new metering work.

**Tech Stack:** Python 3.13, dataclasses, argparse, JSON/Markdown artifacts, pytest, Ruff, Claude workflow JavaScript contract tests.

**Scope:** This plan implements D0, S1, and S5. D1/D2/S2 are a second implementation wave because they change Claude process configuration and workflow agent behavior. S3 is conditional on S2 evidence; S4 is explicitly excluded by the approved design.

---

### Task 1: Import and verify the 09-03 relay/session fixes (S1)

**Files:**
- Modify: `.claude/workflows/l4-stock.js`
- Modify: `autoresearch/trace/capsule.py`
- Modify: `tests/scan/test_forensic_workflow.py`
- Modify: `tests/trace/test_capsule.py`
- Modify: `tests/trace/test_transcript_adapters.py`

- [x] **Step 1: Apply only the pre-existing S1 tests from the main worktree**

The failing tests must assert these exact behaviors:

```python
assert "const RELAY_ROLES = new Set(['gp-shell', 'trace-control'])" in source
assert "if (RELAY_ROLES.has(role)) return rawAgent(prompt, options)" in source
assert handle.contract.session_ref == "5d26c487-dfe2-4351-ace2-ec52effc6d99"
assert handle.contract.session_ref is None  # malformed or Codex session
```

- [x] **Step 2: Run the tests and verify RED**

Run:

```bash
export AUTORESEARCH_ENGINE=codex
/Users/qingbin.zhuang/Personal/TradingAgents/.venv/bin/python -m pytest -q \
  tests/scan/test_forensic_workflow.py \
  tests/trace/test_capsule.py \
  tests/trace/test_transcript_adapters.py
```

Expected: failures mention missing `RELAY_ROLES`, missing `harness_session_ref`, or absent automatic session binding.

- [x] **Step 3: Apply the minimal production changes**

Add this relay branch before the existing `tracedAgent` try block:

```javascript
const RELAY_ROLES = new Set(['gp-shell', 'trace-control'])
async function tracedAgent(invocationId, role, prompt, options) {
  if (RELAY_ROLES.has(role)) return rawAgent(prompt, options)
  // existing business-agent boundary logic remains byte-for-byte below
}
```

Add the Claude-only binding helper and use explicit arguments first:

```python
_HARNESS_SESSION_REF_RE = re.compile(r"[0-9a-fA-F][0-9a-fA-F-]{7,63}\Z")

def harness_session_ref(engine: str) -> str | None:
    if engine != "claude":
        return None
    raw = str(os.environ.get("CLAUDE_CODE_SESSION_ID", "")).strip()
    return raw if _HARNESS_SESSION_REF_RE.match(raw) else None

# inside begin_run, before contract construction
if session_ref is None:
    session_ref = harness_session_ref(engine)
```

- [x] **Step 4: Run S1 tests and full regression suite**

Expected: targeted tests pass; full suite matches the clean baseline.

- [x] **Step 5: Commit S1 independently**

```bash
git add .claude/workflows/l4-stock.js autoresearch/trace/capsule.py \
  tests/scan/test_forensic_workflow.py tests/trace/test_capsule.py \
  tests/trace/test_transcript_adapters.py
git commit -m "fix(trace): bind Claude sessions and skip relay boundaries"
```

Do not add `.claude/skills/scan-market/pinned.jsonc`.

### Task 2: Add a single transcript statistics contract (D0 foundation)

**Files:**
- Modify: `autoresearch/trace/transcripts/base.py`
- Modify: `autoresearch/trace/transcripts/claude.py`
- Modify: `autoresearch/trace/usage_harvest.py`
- Modify: `tests/trace/test_transcript_adapters.py`
- Modify: `tests/trace/test_usage_harvest.py`

- [x] **Step 1: Write failing adapter contract tests**

Create a synthetic Claude JSONL fixture in the test using duplicate streaming IDs, one tool request/result, one compact boundary, a user turn, and two assistant turns after the last user. Assert:

```python
stats = ClaudeTranscriptAdapter(projects_root=tmp_path).stats(ref)
assert stats.usage == ClaudeTranscriptAdapter(projects_root=tmp_path).usage(ref)
assert stats.first_context_tokens == 37
assert stats.context_tokens == (37, 91)
assert stats.compact_pre_tokens == (210_000,)
assert stats.suspected_tail == 1
assert stats.started_at == "2026-09-04T01:00:00Z"
assert stats.ended_at == "2026-09-04T01:00:06Z"
assert stats.tool_requests["Bash"] == 1
assert stats.tool_results["Bash"] > 0
```

Also assert `build_ledger()` exposes the compatibility alias and version:

```python
assert ledger["metric_version"] == "weighted-input-v1"
assert ledger["totals"]["weighted_input_proxy"] == ledger["totals"]["weighted_in"]
```

- [x] **Step 2: Run the two test modules and verify RED**

Expected: `ClaudeTranscriptAdapter` has no `stats` method and the ledger lacks the new fields.

- [x] **Step 3: Add immutable statistics types and protocol method**

Implement this public contract in `base.py`:

```python
@dataclass(frozen=True)
class TranscriptStats:
    normalized: NormalizedTranscript
    usage: UsageRecord
    started_at: str | None
    ended_at: str | None
    context_tokens: tuple[int, ...]
    first_context_tokens: int | None
    compact_pre_tokens: tuple[int, ...]
    suspected_tail: int
    tool_requests: Mapping[str, int] = field(default_factory=dict)
    tool_results: Mapping[str, int] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "context_tokens", tuple(self.context_tokens))
        object.__setattr__(self, "compact_pre_tokens", tuple(self.compact_pre_tokens))
        object.__setattr__(self, "tool_requests", _freeze(self.tool_requests))
        object.__setattr__(self, "tool_results", _freeze(self.tool_results))

class TranscriptAdapter(Protocol):
    def locate(self, run_identity: RunIdentity) -> list[TranscriptRef]: ...
    def stats(self, ref: TranscriptRef) -> TranscriptStats: ...
    def normalize(self, ref: TranscriptRef) -> NormalizedTranscript: ...
    def usage(self, ref: TranscriptRef) -> UsageRecord: ...
```

- [x] **Step 4: Refactor the Claude adapter around one parser**

Implement `stats(ref)` as the only raw JSONL parser. It must keep the last row per `message.id`, calculate context as `input + cache_read + cache_creation`, read compact boundaries only from explicit `compact_boundary.preTokens`/`compactMetadata.preTokens`, count tool-result characters against the matching tool-use ID, and calculate `suspected_tail=max(assistant_messages_after_last_user-1, 0)`. `usage()` returns `stats(ref).usage`; `normalize()` returns `stats(ref).normalized`.

- [x] **Step 5: Add ledger metric provenance**

Keep ledger schema compatibility and add:

```python
ledger = {
    "schema_version": 1,
    "metric_version": "weighted-input-v1",
    # existing keys
}
totals["weighted_input_proxy"] = totals["weighted_in"]
```

- [x] **Step 6: Run targeted tests, Ruff, and commit**

```bash
git add autoresearch/trace/transcripts/base.py autoresearch/trace/transcripts/claude.py \
  autoresearch/trace/usage_harvest.py tests/trace/test_transcript_adapters.py \
  tests/trace/test_usage_harvest.py
git commit -m "feat(trace): expose immutable Claude transcript statistics"
```

### Task 3: Register metering artifacts and build explicit panorama selection

**Files:**
- Modify: `autoresearch/contracts/artifacts.py`
- Create: `autoresearch/trace/usage_panorama.py`
- Create: `tests/trace/test_usage_panorama.py`
- Modify: `tests/contracts/test_registry_parity.py`

- [x] **Step 1: Write RED tests for registry, selectors, and privacy**

Tests must cover:

```python
assert "metering" in artifacts.ROOTS
assert artifacts.by_name("panorama_json").path == "panorama_*.json"
assert artifacts.by_name("panorama_md").stage == "observe"
with pytest.raises(SelectionError, match="explicit session/run or complete time range"):
    validate_selection(Selection(engine="claude", cohort="candidate"))
assert payload["sessions"][0]["status"] == "UNCLAIMED"
assert payload["sessions"][1]["status"] == "UNMEASURED_TIME"
assert "prompt-secret" not in json.dumps(payload)
```

The CLI test must reject a write when `ws.ENGINE == "codex"`, even if `--engine claude` was supplied.

- [x] **Step 2: Verify RED**

Expected: missing module/root/artifacts.

- [x] **Step 3: Add the artifact declarations**

```python
# Add "metering" as the final member of the existing ROOTS tuple.
Artifact("panorama_json", "panorama_*.json", "metering", "observe",
         "usage_panorama", "json", "gated", required_when="usage_panorama --write")
Artifact("panorama_md", "panorama_*.md", "metering", "observe",
         "usage_panorama", "md", "gated", required_when="usage_panorama --write")
```

Update the root documentation so `metering` resolves to `$RPT/_metering` and is outside every run directory.

- [x] **Step 4: Implement the panorama read model**

`usage_panorama.py` must expose:

```python
@dataclass(frozen=True)
class Selection:
    engine: str
    cohort: str
    sessions: tuple[str, ...] = ()
    run_ids: tuple[str, ...] = ()
    from_ts: datetime | None = None
    to_ts: datetime | None = None

class SelectionError(ValueError): ...

def validate_selection(selection: Selection) -> None: ...
def build_panorama(selection: Selection, *, projects_root: Path, repo_root: Path) -> dict: ...
def render_panorama(payload: dict) -> str: ...
def write_panorama(payload: dict, markdown: str, *, reports_root: Path,
                   now: datetime) -> tuple[Path, Path]: ...
def main(argv: list[str] | None = None) -> int: ...
```

Rules: only Claude is accepted in this wave; at least one explicit session/run or a complete from/to interval is required; run IDs resolve through capsule contracts and never through subagent-count heuristics; explicit selectors intersect the time range; timestamps come from transcripts, never mtime. Store only stable role/agent labels and aggregate sizes—never prompt, command, or tool-result text.

Writes use UTC `YYYYMMDDTHHMMSSZ`, temp-file replacement, and `chmod(0o600)`. JSON is canonical source; Markdown renders only that payload. Provenance includes selection hash, session/run lists, git SHA, metric version, price date, timezone, exclusions, and input file content hashes.

- [x] **Step 5: Run targeted tests, contract tests, Ruff, and commit**

```bash
git add autoresearch/contracts/artifacts.py autoresearch/trace/usage_panorama.py \
  tests/trace/test_usage_panorama.py tests/contracts/test_registry_parity.py
git commit -m "feat(trace): add explicit token panorama read model"
```

### Task 4: Add weighted run-budget bands and mature cohort gates (S5 core)

**Files:**
- Modify: `autoresearch/scan/budget.py`
- Modify: `autoresearch/scan/user_config.py`
- Modify: `.claude/skills/scan-market/scan_config.jsonc`
- Modify: `tests/scan/test_budget.py`
- Modify: `tests/scan/test_config_knobs.py`

- [x] **Step 1: Write boundary-first RED tests**

Parameterize `None`, `5_000_000`, `5_000_001`, `7_000_000`, and `7_000_001` and assert:

```python
expected = {
    None: ("RED", "DEGRADED"),
    5_000_000: ("GREEN", "SUCCEEDED"),
    5_000_001: ("YELLOW", "SUCCEEDED"),
    7_000_000: ("YELLOW", "SUCCEEDED"),
    7_000_001: ("RED", "DEGRADED"),
}
assert got["budget_band"] == expected[value][0]
assert got["status"] == expected[value][1]
assert got["truncated"] is False
```

Add validation tests rejecting booleans, zero, negatives, and `target > warn`. Add a 10-run history test with weighted p50 exactly 5M and nearest-rank p90 exactly 7M; 9 runs remain `IMMATURE`.

- [x] **Step 2: Verify RED**

Expected: unknown config keys and missing budget-band fields.

- [x] **Step 3: Implement configuration and observation bands**

Add defaults and normalized values:

```python
"run_weighted_warn": 7_000_000,
"run_weighted_target": 5_000_000,
```

Add both keys to the `budgets` whitelist and positive-number validators. Reject `target > warn` after per-key type validation.

`observe_run` reads `totals.weighted_input_proxy`, falling back only to the compatible `totals.weighted_in`. RED creates a warning and degrades; YELLOW creates an `advisories` entry but does not by itself degrade; GREEN creates neither. Existing cache/USD/wall warnings retain their current degradation semantics.

`evaluate_history` requires weighted values for every comparable real scan, reports `weighted_p50`/`weighted_p90`, and adds both threshold booleans to `targets` while retaining existing USD/wall/cache governance.

- [x] **Step 4: Run targeted tests and commit**

```bash
git add autoresearch/scan/budget.py autoresearch/scan/user_config.py \
  .claude/skills/scan-market/scan_config.jsonc tests/scan/test_budget.py \
  tests/scan/test_config_knobs.py
git commit -m "feat(scan): add weighted token budget bands"
```

### Task 5: Publish weighted health without fuzzy report insertion

**Files:**
- Modify: `autoresearch/scan/post_run.py`
- Modify: `tests/scan/test_wave3_observation.py`
- Modify: `tests/scan/test_publisher_artifact_map.py`

- [x] **Step 1: Write RED report-refresh tests**

Assert the managed block displays proxy/band and remains idempotent. When markers are missing, assert the report bytes are unchanged and the observation is degraded with an explicit warning. Verify refreshed artifact index/trace hashes after a successful replacement.

- [x] **Step 2: Verify RED**

Expected: current stable-anchor fallback mutates markerless reports and current renderer lacks weighted fields.

- [x] **Step 3: Implement strict managed replacement**

For run-observation refresh only, replace content only when both markers are present. If either marker is missing, return original text and warning; never append or anchor-insert. Add weighted proxy and band to the compact and detailed renderers, showing `—/RED` for unmeasured values.

After successful atomic replacements, preserve the existing order: report budget → run health → artifact index → trace mirror → retention. Repeated post-run calls with identical inputs must produce identical bytes.

- [x] **Step 4: Run post-run/publisher tests and commit**

```bash
git add autoresearch/scan/post_run.py tests/scan/test_wave3_observation.py \
  tests/scan/test_publisher_artifact_map.py
git commit -m "feat(scan): publish weighted token health atomically"
```

### Task 6: Wave 1 verification and handoff

**Files:**
- Modify: `docs/superpowers/specs/2026-09-04-token-efficiency-two-lines-design.md`
- Modify: `docs/superpowers/plans/2026-09-04-token-efficiency-metering-wave1.md`

- [x] **Step 1: Run focused verification**

```bash
export AUTORESEARCH_ENGINE=codex
/Users/qingbin.zhuang/Personal/TradingAgents/.venv/bin/python -m pytest -q \
  tests/trace/test_transcript_adapters.py tests/trace/test_usage_harvest.py \
  tests/trace/test_usage_panorama.py tests/contracts/test_registry_parity.py \
  tests/scan/test_budget.py tests/scan/test_config_knobs.py \
  tests/scan/test_wave3_observation.py tests/scan/test_publisher_artifact_map.py \
  tests/scan/test_forensic_workflow.py tests/trace/test_capsule.py
uv run --no-sync ruff check autoresearch/trace autoresearch/scan/budget.py \
  autoresearch/scan/post_run.py autoresearch/scan/user_config.py tests/trace \
  tests/scan/test_budget.py tests/scan/test_wave3_observation.py
```

- [x] **Step 2: Run the full suite**

Use the project environment that contains the declared data/runtime integrations:

```bash
export AUTORESEARCH_ENGINE=codex
/Users/qingbin.zhuang/Personal/TradingAgents/.venv/bin/python -m pytest -q
```

Expected: no failures; only the same environment/data-dependent skips as baseline.

- [x] **Step 3: Audit scope and invariants**

Verify:

```bash
rg -n "agentType: 'general-purpose'" .claude/workflows
rg -n "l4_intel|max_queries" .claude/skills/scan-market/scan_config.jsonc
git diff --check
git status --short
```

Wave 1 must not migrate shell agent types, alter `max_queries`, change rating rubrics, read/write Claude report roots from Codex, or include `pinned.jsonc`.

- [x] **Step 4: Update status and commit the documentation**

Mark only D0/S1/S5-code as implemented. Keep live smoke scan and 10-run maturity gates pending because they require Claude runtime evidence.

```bash
git add docs/superpowers/specs/2026-09-04-token-efficiency-two-lines-design.md \
  docs/superpowers/plans/2026-09-04-token-efficiency-metering-wave1.md
git commit -m "docs: record token metering wave 1 implementation"
```

---

## 执行与复核记录(2026-09-05 接手完成)

Task 1–5 由 Codex 实施(5 commits),**Task 1 Step 4 与整个 Task 6 未做** —— 也就是全量套件
一次没跑。接手后补跑,逮到 2 条红,都是本波自己引入的:

| 红 | 根因 | 修法 |
|---|---|---|
| `test_cli_help_exits_zero[usage_panorama]` | 引擎守卫写在 `argparse` 之前,`--help` 被一起拦掉退 2 | 守卫移到 `parse_args` 之后;仍先于 `build_panorama`(`test_cli_read_is_forbidden_from_codex_before_build` 仍绿) |
| `test_frame_json_clean` | 两个新 budgets 键随 `normalize_budgets` 进 run contract,金样本没跟上 | 补进快照;值是 float(经 `_finite_number`),不是 jsonc 里的 int |

另有两处不在计划内、但接手时必须处理的:

1. **发布线账本被顺手删了三个指标**。Task 5 的未提交改动把 `post_run` 的 budget StageResult
   metrics 从 9 项砍到 6 项(丢 `measurement_status` / `maturity_status` / `denominators`)。
   这三项自 `4a1ac56` 起就在 main 上,计划没有任何一步要求删。已恢复,并在
   `test_markerless_report_is_unchanged_and_degrades_observation` 里锁死键集
   ——**变异实测:删任一项即红**(此前删掉全套测试都不会红)。
2. **脱敏的投机 `ast.parse` 往 stderr 泼 5456 行 SyntaxWarning**。`identity._python_credential_ranges`
   把任意用户文本当 Python 试解析,`\|` `\s` 这类"能编译、只是可疑"的转义只发警告、
   `except SyntaxError` 接不住。main 上就有,但 `usage_panorama` 是第一个把它放大到
   淹没真读数的工具。已消音并补守卫(`test_speculative_python_parse_stays_silent_on_user_text`,
   变异实测有鉴别力)。加锁是因为脱敏会在多线程下跑,而 `catch_warnings` 改的是进程级过滤器栈。

### 验证读数

- 全量套件:**codex 引擎 4952 passed / 12 skipped / 0 failed**、**claude 引擎同为
  4952 passed / 12 skipped / 0 failed**(接手前是 4949 passed + 2 failed;+3 = 修好的 2 条
  与新增的账本探针)。跳过项与基线同因:gitignored 的历史产物不在场、文件系统拒绝字节路径。
  两个引擎都跑是因为本仓有过「只在 codex 下开发 → 合并后 claude 侧 11 条夹具红」的事故。
- `ruff check`:本波触及文件干净。`autoresearch/trace/` 下有 1 条 I001 是 **main 上既有**的
  欠账(同命令在 main 同样报),不在本波修。
- CLI 真跑(不是干跑):`--help` 两引擎均退 0;读路径 15 场会话全 MEASURED;`--write` 落
  `reports_claude/_metering/`,mode 0600,JSON 2.9MB + 由它渲染的 MD。

### 留给下一波的观察(本波不动)

`budget.observe_run(persist=True)` 自持久化那条腿**已经没有生产调用点了**:唯一的生产调用
方 `post_run.publish_run_observation` 现在传 `persist=False`(为了一次性发布最终态、保住字节
幂等),`budget.py` 也没有 `__main__`。默认值留着是为了不破公开签名,但它此刻只被单测走到。
下一波若要收编,先 grep 确认没有新调用方,再连默认值一起改——不要只删分支。

### 仍然敞着的门

设计稿 §10 记的两道门一道没过:**一次真 smoke scan 没跑**、**10 次可比真跑的成熟 cohort
没有**。软件面完成 ≠ 成本目标达成,别把本记录当验收。
