# Scene Reconstruction via Transcript Binding — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让两引擎(Claude / Codex)的任一 run、任一只票在 `chain_view` 一屏回答「读了什么、搜了什么、写了什么、可见推理文本、E6 为什么选它」,缺的部分有账。

**Architecture:** 新模块 `autoresearch/scan/transcript_binder.py` 在 `post_run observe` 里把 judgement-leg transcript(Claude:subagent jsonl;Codex:主线程 rollout 的 ordinal 区段)按「写到本 run staging 根的产物路径」确定性地绑到 capsule 已有的 invocation 上;capsule 现成的 `materialize_agent_index` 负责归档与归一化;`chain_view` 新增 ⑦b 段读 `capsule/agents/`(其次账本外部索引);E6 决策记录升 schema 2 记 `card_context` 与 `why`;已冻结 run 走 `_ledger/agents_index/` 离线索引与 `_ledger/salvage/` 抢救。全部零 LLM、只记不学。

**Tech Stack:** Python 3.12,`uv run --no-sync`,pytest,pandas/pyarrow(P0 任务),现有 `autoresearch.trace.capsule` / `autoresearch.trace.transcripts.{claude,codex}` / `autoresearch.scan.{post_run,retention,chain_view,relative_buy,user_config}` / `autoresearch.contracts.artifacts`。

**Spec:** `docs/superpowers/specs/2026-09-12-scene-reconstruction-transcript-binding-design.md`(b5df331)。本计划覆盖 spec 批 1–3(Task 1–11)与 P0(Task 12,**仅当用户裁定 Q1=并入时执行**);spec §9.2 A3 exec_check(Q2)**不在本计划**。

## Global Constraints

- 引擎隔离:只写当前引擎根 `context_<engine>/`、`reports_<engine>/`;Codex rollout 只读 `~/.codex/sessions/`,Claude transcript 只读 `~/.claude/projects/`;`lake/` 不碰。
- **已冻结的 run 目录一个字节不写**(MANIFEST 不变量);冻结 run 的产物一律落 `reports_<engine>/scan/_ledger/`。
- 绑定必须发生在 run 仍 active 时(`capsule.require_active_run`),即 `post_run observe` 内、`retain()` 之前、`_finalize_forensic_run` 之前;绑定失败只打一行 stderr,**不阻断发布**。
- 候选多于期望时写 `AMBIGUOUS`、一份不绑;**禁止按 mtime 猜**(STAGES.md 第 241 行)。
- 产物路径命中必须落在本 run 的 staging 根下(`context_<engine>/scan_runs/<RUN_ID>/staging/<date>/`),前一天同名文件不得匹配。
- 新产物**先登记 `autoresearch/contracts/artifacts.py` 再写代码**,登记后跑 `uv run --no-sync python -m autoresearch.contracts.emit --write` 并让 `tests/contracts/` 绿;不新增 prelude 步骤(`STEP_NAMES` 与 `test_step_names_inventory` 不动)。
- E6 `RULE_VERSION` 不变(仍 `e6.v3.0`);只加记录字段,选择语义一字不动;writer-1/writer-2 parity 必须保持(`verify_decision` 逐字节比较)。
- 每个任务的测试跑法:`uv run --no-sync python -m pytest <path> -q`(**不要 `| tail`**,管道会吞退出码);每批结束跑全量两遍:`AUTORESEARCH_ENGINE=claude uv run --no-sync python -m pytest -q -x` 与 `AUTORESEARCH_ENGINE=codex uv run --no-sync python -m pytest -q -x`,再 `uv run --no-sync ruff check autoresearch tests`。
- zsh:含 `*`、`?`、`[` 的参数必须加引号(`--include='*.py'`)。
- 提交信息尾行:`Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>`。
- 变异探针(spec §11.2):每条守卫写完先把被守的那段改坏,确认对应用例变红,再改回。

---

## File Structure

| 文件 | 责任 | 任务 |
|---|---|---|
| `autoresearch/trace/capsule.py`(改) | `bind_transcript(stage=)`;`agent_expectations()` 公开别名 + Codex 回退(TASK 事件 / 产物在场)+ `expectation_source`;`_TRANSCRIPT_SCHEMA_VERSION` 1→2 | 1, 3 |
| `autoresearch/trace/transcripts/base.py`(改) | `content_digest(content) -> (bytes, sha256)` | 2 |
| `autoresearch/trace/transcripts/claude.py`(改) | `stats_from_rows(rows, ref)`;tool_result 带 bytes/sha256 | 2 |
| `autoresearch/trace/transcripts/codex.py`(改) | `normalize_rows(rows, ref)`;`reasoning_summary` 项;tool_result 带 bytes/sha256 | 2 |
| `autoresearch/scan/transcript_binder.py`(新) | 标记表、身份推导、Claude/Codex 定位器、绑定、报告、离线索引、CLI | 4, 5, 6, 10 |
| `autoresearch/scan/post_run.py`(改) | `publish_run_observation` 里 `retain()` 之前调 `safe_bind_run(scan)` | 6 |
| `autoresearch/scan/user_config.py`(改) | 白名单 `retention.bind_transcripts` | 6 |
| `.claude/skills/scan-market/scan_config.jsonc` / `SKILL.md` / `STAGES.md`(改) | 配置块、配置表行、capsule 段一句 | 6 |
| `autoresearch/contracts/artifacts.py`(改) | 登记 `transcript_bindings` / `agents_index_ledger` / `agents_index_normalized` / `salvage_provenance` | 6, 10, 11 |
| `autoresearch/scan/chain_view.py`(改) | ⑦ slim 三级查找;⑦b 研究员现场;① 绑定行;⑧ card_context/why;Sources 加 capsule/ledger/salvage 来源 | 7, 9, 10, 11 |
| `autoresearch/scan/l4/parsers.py`(改) | `parse_card_context(text) -> dict` | 8 |
| `autoresearch/scan/relative_buy.py`(改) | `SCHEMA_VERSION = 2`;候选 `card_context`;顶层 `why`;`inputs["details_cards"]` | 9 |
| `autoresearch/scan/salvage.py`(新) | 老 run 抢救 CLI | 11 |
| `autoresearch/scan/outcome.py`(改,P0) | `market_frame` 交易日历核对 | 12 |
| `tests/trace/test_transcript_adapters.py`(改) | Task 1–3 用例 | 1–3 |
| `tests/scan/test_transcript_binder.py`(新) | Task 4–6、10 用例 | 4–6, 10 |
| `tests/scan/test_chain_view_agents.py`(新) | ⑦b / ① / ⑧ / 来源级 | 7, 9, 10, 11 |
| `tests/scan/test_parsers_card_context.py`(新) | Task 8 | 8 |
| `tests/scan/test_relative_buy.py`(改) | schema 2 用例 | 9 |
| `tests/scan/test_salvage.py`(新) | Task 11 | 11 |
| `tests/scan/test_outcome_calendar.py`(新,P0) | Task 12 | 12 |

---

## 批 1(spec §5–§7)

### Task 1: `bind_transcript` 接受显式 `stage`

**Files:**
- Modify: `autoresearch/trace/capsule.py:1562-1600`(`bind_transcript`)
- Test: `tests/trace/test_transcript_adapters.py`

**Interfaces:**
- Produces: `bind_transcript(run_id, path, *, role, invocation_id, subject=None, engine=None, start_ordinal=None, end_ordinal=None, stage: str | None = None) -> dict`。`stage=None` 时行为与今天逐字相同(读 `AUTORESEARCH_STAGE`,缺省 `"l4"`)。

- [ ] **Step 1: 写失败用例**

在 `tests/trace/test_transcript_adapters.py` 末尾追加:

```python
def test_bind_transcript_accepts_explicit_stage(codex_run):
    """绑定器按角色传 stage(l3-rank → l3);不传时仍读 AUTORESEARCH_STAGE / 缺省 l4。"""
    import json

    handle, source = codex_run
    row = bind_transcript(
        handle.run_id, source, role="l3-rank",
        invocation_id="l3-rank-market-1", stage="l3",
    )
    assert row["stage"] == "l3"
    bindings = [json.loads(line) for line in
                (handle.capsule / "agents/bindings.jsonl").read_text(encoding="utf-8").splitlines()
                if line.strip()]
    assert bindings[-1]["stage"] == "l3"
    events = [json.loads(line) for line in
              (handle.capsule / "events/events.jsonl").read_text(encoding="utf-8").splitlines()
              if line.strip()]
    bound = [e for e in events if e["event_type"] == "TRANSCRIPT_BOUND"]
    assert bound[-1]["stage"] == "l3"


def test_bind_transcript_rejects_bad_stage(codex_run):
    handle, source = codex_run
    with pytest.raises(ValueError, match="invalid stage name"):
        bind_transcript(handle.run_id, source, role="l3-rank",
                        invocation_id="l3-rank-market-1", stage="L3 stage")
```

- [ ] **Step 2: 跑用例确认失败**

Run: `uv run --no-sync python -m pytest tests/trace/test_transcript_adapters.py -q -k "explicit_stage or bad_stage"`
Expected: FAIL,`TypeError: bind_transcript() got an unexpected keyword argument 'stage'`

- [ ] **Step 3: 实现**

在 `bind_transcript` 签名末尾加 `stage: str | None = None`,并把原来的

```python
    stage = _validate_stage(
        str(os.environ.get("AUTORESEARCH_STAGE", "")).strip() or "l4"
    )
```

改为

```python
    stage = _validate_stage(
        stage if stage is not None
        else (str(os.environ.get("AUTORESEARCH_STAGE", "")).strip() or "l4")
    )
```

同时在 CLI `bind-transcript` 子命令(`capsule.py:3324` 附近)加 `bind.add_argument("--stage", default=None)`,并在分发处把 `stage=args.stage` 传进去。

- [ ] **Step 4: 跑用例确认通过**

Run: `uv run --no-sync python -m pytest tests/trace/test_transcript_adapters.py -q`
Expected: 全绿(既有用例不受影响)

- [ ] **Step 5: 提交**

```bash
git add autoresearch/trace/capsule.py tests/trace/test_transcript_adapters.py
git commit -m "feat(trace): bind_transcript accepts explicit stage

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 2: 适配器三处小改(tool_result 摘要、Codex reasoning_summary、读文件与归一化拆两步)

**Files:**
- Modify: `autoresearch/trace/transcripts/base.py`
- Modify: `autoresearch/trace/transcripts/claude.py:219-262`(`stats`)、`:328-337`(tool_result 块)
- Modify: `autoresearch/trace/transcripts/codex.py:36-42`(常量)、`:257-380`(`normalize`)
- Test: `tests/trace/test_transcript_adapters.py`

**Interfaces:**
- Produces(base):`content_digest(content: object) -> tuple[int, str]`,`(utf-8 字节数, sha256 hex)`;`None` → `(0, sha256(b""))`;非 str 用 `json.dumps(content, ensure_ascii=False, sort_keys=True)`。
- Produces(claude):`ClaudeTranscriptAdapter.stats_from_rows(rows: list[dict], ref: TranscriptRef) -> TranscriptStats`;`stats(ref)` = 读文件 + `stats_from_rows`。tool_result 项 payload 多两键 `bytes`、`sha256`。
- Produces(codex):`CodexTranscriptAdapter.normalize_rows(rows: list[dict], ref: TranscriptRef) -> NormalizedTranscript`(内部先 `_segment`);`normalize(ref)` = `_rows(ref)` + `normalize_rows`。新项 kind `reasoning_summary`,payload `{"text": str}`;`encrypted_reasoning` 仍丢。tool_result 项多 `bytes`、`sha256`。

- [ ] **Step 1: 写失败用例**

追加到 `tests/trace/test_transcript_adapters.py`:

```python
def test_content_digest_is_stable_for_str_and_json():
    import hashlib
    from autoresearch.trace.transcripts.base import content_digest

    n, digest = content_digest("hello")
    assert n == 5 and digest == hashlib.sha256(b"hello").hexdigest()
    n2, d2 = content_digest([{"b": 1, "a": 2}])
    assert d2 == hashlib.sha256('[{"a": 2, "b": 1}]'.encode("utf-8")).hexdigest()
    assert n2 == len('[{"a": 2, "b": 1}]')
    assert content_digest(None) == (0, hashlib.sha256(b"").hexdigest())


def test_claude_tool_result_carries_bytes_and_sha256(claude_ref):
    import hashlib

    normalized = ClaudeTranscriptAdapter().normalize(claude_ref)
    results = [item for item in normalized.items if item.kind == "tool_result"]
    assert results, "fixture 里应有一个 tool_result"
    payload = results[0].payload
    raw = "Synthetic fixture result."
    assert payload["bytes"] == len(raw.encode("utf-8"))
    assert payload["sha256"] == hashlib.sha256(raw.encode("utf-8")).hexdigest()


def test_claude_stats_from_rows_equals_stats_from_file(claude_ref):
    adapter = ClaudeTranscriptAdapter()
    rows = read_jsonl(claude_ref.path)
    from_rows = adapter.stats_from_rows(rows, claude_ref)
    from_file = adapter.stats(claude_ref)
    assert [i.kind for i in from_rows.normalized.items] == [i.kind for i in from_file.normalized.items]
    assert from_rows.usage == from_file.usage


def _codex_rows_with_reasoning(tmp_path):
    import json

    rows = [
        {"timestamp": "2026-09-08T13:44:00.000Z", "ordinal": 1, "type": "session_meta",
         "payload": {"id": "t1", "session_id": "t1", "cwd": "/repo", "timestamp": "2026-09-08T13:44:00.000Z"}},
        {"timestamp": "2026-09-08T13:44:01.000Z", "ordinal": 2, "type": "turn_context",
         "payload": {"model": "gpt-5.6-sol",
                     "collaboration_mode": {"settings": {"reasoning_effort": "high"}}}},
        {"timestamp": "2026-09-08T13:44:02.000Z", "ordinal": 3, "type": "response_item",
         "payload": {"type": "reasoning",
                     "summary": [{"type": "summary_text", "text": "先读 prompt 再读 slim"}]}},
        {"timestamp": "2026-09-08T13:44:03.000Z", "ordinal": 4, "type": "response_item",
         "payload": {"type": "encrypted_reasoning", "encrypted_content": "opaque"}},
        {"timestamp": "2026-09-08T13:44:04.000Z", "ordinal": 5, "type": "response_item",
         "payload": {"type": "custom_tool_call", "call_id": "c1", "name": "exec",
                     "input": "cat /repo/context_codex/scan_runs/R/staging/2026-09-08/_l4_prompt_600000.md"}},
        {"timestamp": "2026-09-08T13:44:05.000Z", "ordinal": 6, "type": "response_item",
         "payload": {"type": "custom_tool_call_output", "call_id": "c1", "output": "hello"}},
    ]
    path = tmp_path / "rollout-synthetic.jsonl"
    path.write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in rows) + "\n",
                    encoding="utf-8")
    return rows, path


def test_codex_keeps_plain_reasoning_summary_and_drops_encrypted(tmp_path):
    import hashlib

    rows, path = _codex_rows_with_reasoning(tmp_path)
    ref = TranscriptRef(engine="codex", path=path, status="PRESENT", role="l4-card",
                        subject="600000", invocation_id="l4-card-600000-1")
    adapter = CodexTranscriptAdapter()
    normalized = adapter.normalize(ref)
    kinds = [i.kind for i in normalized.items]
    assert kinds == ["reasoning_summary", "tool_request", "tool_result"]
    assert normalized.items[0].payload["text"] == "先读 prompt 再读 slim"
    assert "opaque" not in str([dict(i.payload) for i in normalized.items])
    result = normalized.items[2].payload
    assert result["bytes"] == 5
    assert result["sha256"] == hashlib.sha256(b"hello").hexdigest()
    # 行级入口与文件入口等价(离线索引用它对 gz 归档在内存里归一化)
    from_rows = adapter.normalize_rows(rows, ref)
    assert [i.kind for i in from_rows.items] == kinds


def test_codex_normalize_rows_applies_ordinal_segment(tmp_path):
    rows, path = _codex_rows_with_reasoning(tmp_path)
    ref = TranscriptRef(engine="codex", path=path, status="PRESENT", role="l4-card",
                        subject="600000", invocation_id="l4-card-600000-1",
                        start_ordinal=5, end_ordinal=6)
    normalized = CodexTranscriptAdapter().normalize_rows(rows, ref)
    assert [i.kind for i in normalized.items] == ["tool_request", "tool_result"]
```

- [ ] **Step 2: 跑用例确认失败**

Run: `uv run --no-sync python -m pytest tests/trace/test_transcript_adapters.py -q -k "content_digest or bytes_and_sha256 or stats_from_rows or reasoning_summary or ordinal_segment"`
Expected: FAIL(`ImportError: content_digest` / `AttributeError: stats_from_rows` / kinds 不含 `reasoning_summary`)

- [ ] **Step 3: 实现 `base.content_digest`**

在 `autoresearch/trace/transcripts/base.py` 顶部 `import hashlib, json`(若无),在 `is_external_tool` 之前加:

```python
def content_digest(content: object) -> tuple[int, str]:
    """工具结果的 (utf-8 字节数, sha256)。内容本身留在 raw 归档里;视图靠这两个数回答
    「读到的 slim 是不是发布时那份」,不用把 8KB 原文再印一遍。"""
    if content is None:
        raw = b""
    elif isinstance(content, str):
        raw = content.encode("utf-8")
    else:
        raw = json.dumps(content, ensure_ascii=False, sort_keys=True).encode("utf-8")
    return len(raw), hashlib.sha256(raw).hexdigest()
```

- [ ] **Step 4: 实现 Claude 侧**

`claude.py`:`from autoresearch.trace.transcripts.base import content_digest`(加进既有 import 列表)。把 `stats` 拆成:

```python
    def stats(self, ref: TranscriptRef) -> TranscriptStats:
        """Parse one Claude JSONL once into normalization, usage, and diagnostics."""
        self._validate_ref(ref, "inspect")
        return self.stats_from_rows(list(self._iter_rows(ref.path)), ref)

    def stats_from_rows(self, rows: list[dict], ref: TranscriptRef) -> TranscriptStats:
        """行级入口(2026-09-12 设计 §6.2③):离线索引对 gz 归档在内存里归一化时走这里,
        文件版 `stats` 只是「读文件 + 本函数」。"""
        last_message_row: dict[str, int] = {}
        ...(原 stats 从 `for idx, row in enumerate(rows)` 起的全部正文,原样搬入)
```

在 tool_result 块的 `add("tool_result", {...})` 里加两键:

```python
                elif block_type == "tool_result":
                    request_id = block.get("tool_use_id")
                    n_bytes, digest = content_digest(block.get("content"))
                    add(
                        "tool_result",
                        {
                            "tool_use_id": request_id,
                            "content": block.get("content"),
                            "is_error": bool(block.get("is_error")),
                            "bytes": n_bytes,
                            "sha256": digest,
                        },
                        timestamp,
                    )
```

- [ ] **Step 5: 实现 Codex 侧**

`codex.py`:常量改为

```python
_SKIPPED_RESPONSE_ITEMS = frozenset({"encrypted_reasoning"})
_REASONING_ITEMS = frozenset({"reasoning"})
```

加辅助方法(放在 `_text` 之后):

```python
    @staticmethod
    def _reasoning_summary(payload: dict) -> str:
        """Codex 明文 reasoning 摘要(`summary[].text` / `content[].text`);加密体不在此列。"""
        parts: list[str] = []
        for key in ("summary", "content"):
            value = payload.get(key)
            if not isinstance(value, list):
                continue
            for entry in value:
                if isinstance(entry, dict) and isinstance(entry.get("text"), str) and entry["text"].strip():
                    parts.append(entry["text"].strip())
        return "\n".join(parts)
```

把 `normalize` 拆成:

```python
    def normalize(self, ref: TranscriptRef) -> NormalizedTranscript:
        return self.normalize_rows(self._rows(ref), ref)

    def normalize_rows(self, rows: list[dict], ref: TranscriptRef) -> NormalizedTranscript:
        """Keep visible messages, tool traffic, plain reasoning summaries and errors; drop
        encrypted internals.  行级入口:离线索引与文件版共用。"""
        rows = self._segment(rows, ref)
        model, effort = self._context(rows)
        ...(原正文)
```

在 `if row.get("type") != "response_item" or kind in _SKIPPED_RESPONSE_ITEMS: continue` 之后、`if kind in _MESSAGE_ITEMS:` 之前插入:

```python
            if kind in _REASONING_ITEMS:
                summary = self._reasoning_summary(payload)
                if summary:
                    add("reasoning_summary", {"text": summary}, timestamp)
                continue
```

`_RESULT_ITEMS` 分支改为:

```python
            elif kind in _RESULT_ITEMS:
                text = self._text(payload.get("output"))
                n_bytes, digest = content_digest(text)
                add(
                    "tool_result",
                    {
                        "tool_call_id": payload.get("call_id"),
                        "call_id": payload.get("call_id"),
                        "content": text,
                        "is_error": bool(payload.get("is_error")),
                        "bytes": n_bytes,
                        "sha256": digest,
                    },
                    timestamp,
                )
```

`web_search_end` 那个 `add("tool_result", ...)` 同样加 `"bytes"`/`"sha256"`(对 `{"query":..., "results":...}` 取 `content_digest`)。

- [ ] **Step 6: 跑用例确认通过 + 既有用例仍绿**

Run: `uv run --no-sync python -m pytest tests/trace -q`
Expected: 全绿。若 `test_codex_normalize_keeps_visible_items_only` 因 fixture 里 ordinal 6 的 `reasoning` 行有摘要而多出一个 `reasoning_summary`,把该用例的期望列表按新行为更新(fixture 第 6 行有 `summary` 文本才会出现;没有则不变)。

- [ ] **Step 7: 提交**

```bash
git add autoresearch/trace/transcripts/base.py autoresearch/trace/transcripts/claude.py autoresearch/trace/transcripts/codex.py tests/trace/test_transcript_adapters.py
git commit -m "feat(trace): tool_result digests, codex reasoning_summary, row-level normalize entry

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 3: agent 期望回退(Codex)与 `expectation_source`

**Files:**
- Modify: `autoresearch/trace/capsule.py:1455`(`_TRANSCRIPT_SCHEMA_VERSION`)、`:1970-2064`(`materialize_agent_index`)、`_agent_expectations`(其上方)
- Test: `tests/trace/test_transcript_adapters.py`

**Interfaces:**
- Produces: `agent_expectations(handle: RunHandle) -> dict[str, dict]`(公开别名,行含 `invocation_id, role, subject, subject_key, attempt, dispatched, terminal, expectation_source`)。
- `expectation_source ∈ {"agent_events", "task_events", "products"}`;`agents/index.json` 每行带该键;`_TRANSCRIPT_SCHEMA_VERSION = 2`。
- 回退规则(仅当 run **零** `AGENT_*` 事件):l4-card 由 `TASK_CLAIMED/TASK_SUCCEEDED/TASK_FAILED`(stage=`l4`,invocation `l4-task-<code>-attempt-<n>`)派生为 `l4-card-<code>-<n>`;`l3-rank`/`l3-repair`/`strategist` 由 `handle.staging` 下 `_l3_judged.json`/`_l3_repair_patch.json`/`market_view.md` 在场派生 `<role>-market-1`;`l4-intel` 由 `_l4_intel_<code>.md` 派生;`sector-brief` 由 `sector_briefs/<行业>.md` 按行业名排序派生 `sector-brief-<n>-1`(subject=行业名,subject_key=`subject_key(行业名)`);`l4-card` 无任务事件时由 `details/<code>.md` 派生。

- [ ] **Step 1: 写失败用例**

```python
def _write_products(staging, *, codes=("600000",), sectors=("半导体",)):
    staging.mkdir(parents=True, exist_ok=True)
    (staging / "_l3_judged.json").write_text("[]", encoding="utf-8")
    (staging / "market_view.md").write_text("# 市场研判\n", encoding="utf-8")
    (staging / "details").mkdir(exist_ok=True)
    (staging / "sector_briefs").mkdir(exist_ok=True)
    for code in codes:
        (staging / f"_l4_intel_{code}.md").write_text("# intel\n", encoding="utf-8")
        (staging / "details" / f"{code}.md").write_text("# card\n", encoding="utf-8")
    for name in sectors:
        (staging / "sector_briefs" / f"{name}.md").write_text("# brief\n", encoding="utf-8")


def test_codex_expectations_fall_back_to_task_events_and_products(codex_run):
    """Codex 在会话内自己跑各角色,零 AGENT 事件;期望改从 TASK 事件与产物在场派生,
    缺的仍记 GONE(spec §5.5)。"""
    import json
    from autoresearch.trace.events import append_event

    handle, _ = codex_run
    events = handle.capsule / "events/events.jsonl"
    for event_type in ("TASK_CLAIMED", "TASK_SUCCEEDED"):
        append_event(events, run_id=handle.run_id, engine="codex", stage="l4",
                     invocation_id="l4-task-600000-attempt-1", attempt=1, subject="600000",
                     event_type=event_type, payload={"attempt": 1})
    _write_products(handle.staging, codes=("600000",), sectors=("半导体",))

    index = materialize_agent_index(handle.run_id)

    rows = {r["invocation_id"]: r for r in index["invocations"]}
    assert rows["l4-card-600000-1"]["expectation_source"] == "task_events"
    assert rows["l4-card-600000-1"]["terminal"] == "COMPLETED"
    assert rows["l4-intel-600000-1"]["expectation_source"] == "products"
    assert rows["l3-rank-market-1"]["expectation_source"] == "products"
    assert rows["strategist-market-1"]["expectation_source"] == "products"
    sector = rows["sector-brief-1-1"]
    assert sector["subject"] == "半导体"
    assert sector["subject_key"] == capsule_mod.subject_key("半导体")
    assert {r["status"] for r in rows.values()} == {"GONE"}
    assert index["coverage"] == {"expected": 5, "present": 0, "missing": 5}
    assert index["schema_version"] == 2
    on_disk = json.loads((handle.capsule / "agents/index.json").read_text(encoding="utf-8"))
    assert on_disk["coverage"] == index["coverage"]


def test_fallback_is_not_used_when_agent_events_exist(codex_run):
    """有 AGENT 事件的 run 一律以事件链为准,产物在场不再另造期望(避免 Claude run 里
    sector-brief 被计两次)。"""
    handle, _ = codex_run
    record_agent_boundary(handle.run_id, "AGENT_DISPATCHED", role="l4-card",
                          invocation_id="l4-card-600000-1", attempt=1, subject="600000")
    _write_products(handle.staging, codes=("600000", "600001"), sectors=("半导体",))

    index = materialize_agent_index(handle.run_id)

    ids = sorted(r["invocation_id"] for r in index["invocations"] if r["expected"])
    assert ids == ["l4-card-600000-1"]
    assert index["invocations"][0]["expectation_source"] == "agent_events"
```

- [ ] **Step 2: 跑用例确认失败**

Run: `uv run --no-sync python -m pytest tests/trace/test_transcript_adapters.py -q -k "fall_back or not_used_when_agent_events"`
Expected: FAIL(`KeyError: 'l4-card-600000-1'` / `expectation_source` 缺失)

- [ ] **Step 3: 实现**

在 `capsule.py` 的 `_agent_expectations` 上方加常量与两个派生函数,并把原函数改名为 `_expectations_from_agent_events`:

```python
_TASK_EVENT_TYPES = frozenset({"TASK_CLAIMED", "TASK_SUCCEEDED", "TASK_FAILED"})
_TASK_INVOCATION_RE = re.compile(r"^l4-task-(?P<code>\d{6})-attempt-(?P<attempt>\d+)$")
#: 产物在场 → 单例角色期望(Codex 回退;spec §5.5)。
_PRODUCT_SINGLETONS = (
    ("l3-rank", "_l3_judged.json"),
    ("l3-repair", "_l3_repair_patch.json"),
    ("strategist", "market_view.md"),
)
_INTEL_PRODUCT_RE = re.compile(r"^_l4_intel_(?P<code>\d{6})\.md$")
_CARD_PRODUCT_RE = re.compile(r"^(?P<code>\d{6})\.md$")


def _expectation_row(invocation_id: str, *, role: str, subject: str | None,
                     subject_key: str | None, attempt: int, source: str,
                     dispatched: bool = True, terminal: str | None = None) -> dict:
    return {
        "invocation_id": invocation_id, "role": role, "subject": subject,
        "subject_key": subject_key, "attempt": attempt, "dispatched": dispatched,
        "terminal": terminal, "expectation_source": source,
    }


def _expectations_from_task_events(handle: RunHandle) -> dict[str, dict]:
    rows: dict[str, dict] = {}
    path = handle.capsule / "events/events.jsonl"
    if not path.is_file():
        return rows
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        event = json.loads(line)
        if event.get("event_type") not in _TASK_EVENT_TYPES or event.get("stage") != "l4":
            continue
        match = _TASK_INVOCATION_RE.match(str(event.get("invocation_id") or ""))
        if not match:
            continue
        code, attempt = match.group("code"), int(match.group("attempt"))
        invocation_id = f"l4-card-{code}-{attempt}"
        row = rows.setdefault(invocation_id, _expectation_row(
            invocation_id, role="l4-card", subject=code, subject_key=code,
            attempt=attempt, source="task_events"))
        if event["event_type"] == "TASK_SUCCEEDED":
            row["terminal"] = "COMPLETED"
        elif event["event_type"] == "TASK_FAILED":
            row["terminal"] = "FAILED"
    return rows


def _expectations_from_products(handle: RunHandle, *, skip_cards: bool) -> dict[str, dict]:
    rows: dict[str, dict] = {}
    staging = handle.staging
    if not staging.is_dir():
        return rows
    for role, name in _PRODUCT_SINGLETONS:
        if (staging / name).is_file():
            invocation_id = f"{role}-market-1"
            rows[invocation_id] = _expectation_row(
                invocation_id, role=role, subject=None, subject_key=None, attempt=1,
                source="products", terminal="COMPLETED")
    for path in sorted(staging.glob("_l4_intel_*.md")):
        match = _INTEL_PRODUCT_RE.match(path.name)
        if match:
            code = match.group("code")
            invocation_id = f"l4-intel-{code}-1"
            rows[invocation_id] = _expectation_row(
                invocation_id, role="l4-intel", subject=code, subject_key=code, attempt=1,
                source="products", terminal="COMPLETED")
    if not skip_cards:
        for path in sorted((staging / "details").glob("*.md")) if (staging / "details").is_dir() else []:
            match = _CARD_PRODUCT_RE.match(path.name)
            if match:
                code = match.group("code")
                invocation_id = f"l4-card-{code}-1"
                rows[invocation_id] = _expectation_row(
                    invocation_id, role="l4-card", subject=code, subject_key=code, attempt=1,
                    source="products", terminal="COMPLETED")
    briefs = sorted(p.stem for p in (staging / "sector_briefs").glob("*.md")) \
        if (staging / "sector_briefs").is_dir() else []
    for n, name in enumerate(briefs, start=1):
        invocation_id = f"sector-brief-{n}-1"
        rows[invocation_id] = _expectation_row(
            invocation_id, role="sector-brief", subject=name, subject_key=subject_key(name),
            attempt=1, source="products", terminal="COMPLETED")
    return rows


def agent_expectations(handle: RunHandle) -> dict[str, dict]:
    """One row per reached agent invocation.  事件链是权威;**零 AGENT 事件**(Codex 在会话内
    自己跑各角色)时才回退到 TASK 事件与产物在场(spec §5.5),行上 `expectation_source`
    写明来源。"""
    rows = _expectations_from_agent_events(handle)
    for row in rows.values():
        row["expectation_source"] = "agent_events"
    if rows:
        return rows
    rows = _expectations_from_task_events(handle)
    rows.update(_expectations_from_products(handle, skip_cards=bool(rows)))
    return rows


_agent_expectations = agent_expectations   # 既有内部调用点不改名
```

`_expectations_from_agent_events` 就是原 `_agent_expectations` 的正文(原样,不加 `expectation_source`)。在 `materialize_agent_index` 里,`if expectation:` 分支加一行 `row["expectation_source"] = expectation.get("expectation_source")`,`else` 分支加 `row["expectation_source"] = None`。把 `_TRANSCRIPT_SCHEMA_VERSION = 1` 改为 `2`。

- [ ] **Step 4: 修既有 schema 断言**

Run: `grep -rn "schema_version" tests/trace/test_transcript_adapters.py tests/trace/test_capsule.py tests/integration`
对断言 `agents/index.json`、`bindings.jsonl`、`normalized/*.json` 的 `schema_version == 1` 的用例改成 `== 2`;与 transcript 无关的 `schema_version` 不动。

- [ ] **Step 5: 跑用例确认通过**

Run: `uv run --no-sync python -m pytest tests/trace tests/integration -q`
Expected: 全绿

- [ ] **Step 6: 变异探针**

把 `agent_expectations` 里 `if rows: return rows` 之后的两行回退注释掉 → `test_codex_expectations_fall_back_to_task_events_and_products` 必红;改回。

- [ ] **Step 7: 提交**

```bash
git add autoresearch/trace/capsule.py tests/trace/test_transcript_adapters.py tests/trace/test_capsule.py
git commit -m "feat(trace): derive Codex agent expectations from task events and products

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 4: 绑定器核心 + Claude 定位器(`transcript_binder.py`)

**Files:**
- Create: `autoresearch/scan/transcript_binder.py`
- Test: `tests/scan/test_transcript_binder.py`

**Interfaces:**
- Produces:
  - `MARKERS: tuple[Marker, ...]`;`Marker(role, product: re.Pattern, inputs: tuple[re.Pattern, ...], subject_kind: "code"|"sector"|None)`。
  - `Identity(role, subject, subject_key, verified: bool, product: str | None)`(frozen dataclass)。
  - `derive_identity(writes: Sequence[str], reads: Sequence[str], roots: Sequence[str]) -> tuple[Identity | None, str]`,第二项 ∈ `{"PRODUCT", "INPUT_ONLY", "MULTI_PRODUCT", "NONE"}`;`roots` 是 staging 根的**绝对路径字符串**(尾带 `/`),产物路径必须以其一开头。
  - `Candidate(path: Path, agent: str, first_ts: str | None, writes: list[str], reads: list[str], start_ordinal: int | None = None, end_ordinal: int | None = None, identity: Identity | None = None, reason: str = "")`(dataclass)。
  - `claude_candidates(scan_dir: Path) -> tuple[list[Candidate], dict]`(读 `_token_usage.json`)。
  - `assign(candidates, expectations: dict[str, dict], roots) -> tuple[list[dict], list[dict]]` → `(rows, unmatched)`;row 键:`invocation_id, role, subject, subject_key, attempt, status, reason, path, product, start_ordinal, end_ordinal, segment_quality, expectation_source`;`status ∈ {BOUND, UNVERIFIED_BY_PRODUCT, AMBIGUOUS, GONE, BOUND_WITHOUT_EXPECTATION}`。
  - `STAGE_FOR_ROLE = {"l3-rank": "l3", "l3-repair": "l3", "l4-card": "l4", "l4-intel": "l4", "strategist": "stage0", "sector-brief": "stage0"}`;`USAGE_AGENT_TO_ROLE = {"macro-brief": "strategist"}`。
- Consumes: `capsule.subject_key(display)`(Task 3 已公开)、`retention.ARCHIVE_TRANSCRIPT_AGENTS`。

- [ ] **Step 1: 写失败用例**

新建 `tests/scan/test_transcript_binder.py`:

```python
"""现场重建绑定器(2026-09-12 设计 §5)。判据:产物路径命中且在本 run staging 根下;
候选多于期望 → AMBIGUOUS 一份不绑;只有输入无产物 → UNVERIFIED_BY_PRODUCT。"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from autoresearch.scan import transcript_binder as tb

ROOT = "/ctx/context_claude/scan_runs/R1/staging/2026-09-09/"
OTHER = "/ctx/context_claude/scan_runs/R0/staging/2026-09-08/"


def _exp(invocation_id, role, subject=None, subject_key=None, attempt=1, source="agent_events"):
    return {"invocation_id": invocation_id, "role": role, "subject": subject,
            "subject_key": subject_key if subject_key is not None else subject,
            "attempt": attempt, "dispatched": True, "terminal": "COMPLETED",
            "expectation_source": source}


def test_identity_from_card_product_under_run_root():
    ident, why = tb.derive_identity(
        writes=[ROOT + "details/688411.md"], reads=[ROOT + "_l4_prompt_688411.md"], roots=[ROOT])
    assert why == "PRODUCT"
    assert (ident.role, ident.subject, ident.subject_key, ident.verified) == ("l4-card", "688411", "688411", True)
    assert ident.product == "details/688411.md"


def test_product_outside_run_root_does_not_match():
    """前一天同名 details/<code>.md 不得命中(spec §5.2 根校验)。"""
    ident, why = tb.derive_identity(writes=[OTHER + "details/688411.md"], reads=[], roots=[ROOT])
    assert (ident, why) == (None, "NONE")


def test_input_only_is_unverified_identity():
    ident, why = tb.derive_identity(writes=[], reads=[ROOT + "_l4_prompt_688411.md"], roots=[ROOT])
    assert why == "INPUT_ONLY"
    assert ident.role == "l4-card" and ident.subject == "688411" and ident.verified is False


def test_every_role_marker_resolves():
    from autoresearch.trace.capsule import subject_key

    cases = {
        ROOT + "_l4_intel_688411.md": ("l4-intel", "688411", "688411"),
        ROOT + "sector_briefs/个护用品.md": ("sector-brief", "个护用品", subject_key("个护用品")),
        ROOT + "_l3_judged.json": ("l3-rank", None, None),
        ROOT + "_l3_repair_patch.json": ("l3-repair", None, None),
        ROOT + "market_view.md": ("strategist", None, None),
    }
    for path, expected in cases.items():
        ident, why = tb.derive_identity(writes=[path], reads=[], roots=[ROOT])
        assert why == "PRODUCT", path
        assert (ident.role, ident.subject, ident.subject_key) == expected, path


def test_two_different_products_in_one_transcript_is_multi_product():
    ident, why = tb.derive_identity(
        writes=[ROOT + "details/688411.md", ROOT + "details/600000.md"], reads=[], roots=[ROOT])
    assert (ident, why) == (None, "MULTI_PRODUCT")


def _claude_jsonl(path: Path, *, writes=(), reads=(), ts="2026-09-09T13:50:00.000Z"):
    blocks = []
    for r in reads:
        blocks.append({"type": "tool_use", "id": f"r{len(blocks)}", "name": "Read", "input": {"file_path": r}})
    for w in writes:
        blocks.append({"type": "tool_use", "id": f"w{len(blocks)}", "name": "Write",
                       "input": {"file_path": w, "content": "# card\n"}})
    # 带 model/usage/effort:materialize 时 Claude 适配器要算 usage,缺字段会把行判成 UNSUPPORTED
    rows = [{"type": "assistant", "timestamp": ts, "agentId": path.stem, "effort": "max",
             "message": {"id": "m1", "role": "assistant", "model": "claude-opus-5", "content": blocks,
                         "usage": {"input_tokens": 1, "output_tokens": 1,
                                   "cache_read_input_tokens": 0, "cache_creation_input_tokens": 0}}}]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in rows) + "\n", encoding="utf-8")
    return path


def _usage(scan: Path, rows):
    scan.mkdir(parents=True, exist_ok=True)
    (scan / "_token_usage.json").write_text(json.dumps({"schema_version": 1, "rows": rows}), encoding="utf-8")


def test_claude_candidates_take_only_judgement_agents(tmp_path):
    scan = tmp_path / "staging"
    card = _claude_jsonl(tmp_path / "h" / "agent-a1.jsonl", writes=[ROOT + "details/688411.md"])
    shell = _claude_jsonl(tmp_path / "h" / "agent-a2.jsonl", writes=[])
    _usage(scan, [
        {"agent": "l4-card", "role": "subagent", "path": str(card)},
        {"agent": "general-purpose", "role": "subagent", "path": str(shell)},
        {"agent": "(主会话)", "role": "main", "path": str(shell)},
        {"agent": "l4-intel", "role": "subagent", "path": str(tmp_path / "h" / "missing.jsonl")},
    ])
    cands, counts = tb.claude_candidates(scan)
    assert [c.agent for c in cands] == ["l4-card", "l4-intel"]
    assert cands[0].writes == [ROOT + "details/688411.md"] and cands[0].first_ts == "2026-09-09T13:50:00.000Z"
    assert cands[1].reason == "GONE_FILE"
    assert counts == {"claude_usage_rows": 2}


def test_assign_binds_pairs_by_attempt_order(tmp_path):
    a = _claude_jsonl(tmp_path / "a.jsonl", writes=[ROOT + "details/688411.md"], ts="2026-09-09T13:50:00.000Z")
    b = _claude_jsonl(tmp_path / "b.jsonl", writes=[ROOT + "details/688411.md"], ts="2026-09-09T14:10:00.000Z")
    cands = [tb.Candidate(b, "l4-card", "2026-09-09T14:10:00.000Z", [ROOT + "details/688411.md"], []),
             tb.Candidate(a, "l4-card", "2026-09-09T13:50:00.000Z", [ROOT + "details/688411.md"], [])]
    exps = {"l4-card-688411-1": _exp("l4-card-688411-1", "l4-card", "688411"),
            "l4-card-688411-2": _exp("l4-card-688411-2", "l4-card", "688411", attempt=2)}
    rows, unmatched = tb.assign(cands, exps, [ROOT])
    by_id = {r["invocation_id"]: r for r in rows}
    assert by_id["l4-card-688411-1"]["path"] == str(a) and by_id["l4-card-688411-1"]["status"] == "BOUND"
    assert by_id["l4-card-688411-2"]["path"] == str(b) and by_id["l4-card-688411-2"]["attempt"] == 2
    assert unmatched == []


def test_assign_marks_ambiguous_when_candidates_exceed_expected_attempts(tmp_path):
    a = _claude_jsonl(tmp_path / "a.jsonl", writes=[ROOT + "details/688411.md"])
    b = _claude_jsonl(tmp_path / "b.jsonl", writes=[ROOT + "details/688411.md"])
    cands = [tb.Candidate(a, "l4-card", None, [ROOT + "details/688411.md"], []),
             tb.Candidate(b, "l4-card", None, [ROOT + "details/688411.md"], [])]
    rows, _ = tb.assign(cands, {"l4-card-688411-1": _exp("l4-card-688411-1", "l4-card", "688411")}, [ROOT])
    statuses = [r["status"] for r in rows if r["invocation_id"] == "l4-card-688411-1"]
    assert statuses == ["AMBIGUOUS"]
    assert "2 candidates > 1 expected" in rows[0]["reason"]


def test_assign_reports_gone_unverified_and_unmatched(tmp_path):
    only_input = _claude_jsonl(tmp_path / "i.jsonl", reads=[ROOT + "_l4_prompt_600000.md"])
    stray = _claude_jsonl(tmp_path / "s.jsonl", writes=[OTHER + "details/688411.md"])
    cands = [tb.Candidate(only_input, "l4-card", None, [], [ROOT + "_l4_prompt_600000.md"]),
             tb.Candidate(stray, "l4-card", None, [OTHER + "details/688411.md"], [])]
    exps = {"l4-card-600000-1": _exp("l4-card-600000-1", "l4-card", "600000"),
            "l4-card-688411-1": _exp("l4-card-688411-1", "l4-card", "688411")}
    rows, unmatched = tb.assign(cands, exps, [ROOT])
    by_id = {r["invocation_id"]: r for r in rows}
    assert by_id["l4-card-600000-1"]["status"] == "UNVERIFIED_BY_PRODUCT"
    assert by_id["l4-card-688411-1"]["status"] == "GONE"
    assert by_id["l4-card-688411-1"]["reason"] == "no candidate transcript"
    assert unmatched == [{"path": str(stray), "reason": "NONE"}]


def test_assign_binds_without_expectation_when_events_are_missing(tmp_path):
    a = _claude_jsonl(tmp_path / "a.jsonl", writes=[ROOT + "sector_briefs/半导体.md"])
    cands = [tb.Candidate(a, "sector-brief", None, [ROOT + "sector_briefs/半导体.md"], [])]
    rows, _ = tb.assign(cands, {}, [ROOT])
    from autoresearch.trace.capsule import subject_key

    assert rows[0]["status"] == "BOUND_WITHOUT_EXPECTATION"
    assert rows[0]["invocation_id"] == f"sector-brief-{subject_key('半导体')}-1"
```

- [ ] **Step 2: 跑用例确认失败**

Run: `uv run --no-sync python -m pytest tests/scan/test_transcript_binder.py -q`
Expected: FAIL,`ModuleNotFoundError: autoresearch.scan.transcript_binder`

- [ ] **Step 3: 实现模块(第一部分:标记、身份、Claude 定位器、assign)**

```python
#!/usr/bin/env python3
"""现场重建:把 judgement-leg transcript 绑到 capsule 的 agent invocation 上(2026-09-12 设计 §5)。

零 LLM、零联网。Claude:每份 subagent jsonl = 一次调用;Codex:主线程 rollout 的一个 ordinal
区段 = 一次调用。判据只有一条:**写到本 run staging 根下的产物路径**。候选多于期望 →
AMBIGUOUS 且一份不绑(STAGES.md 第 241 行:禁止按 mtime 猜)。

产物:staging `_transcript_bindings.json`(谁缺、为什么缺的账;capsule 的 agents/index.json 只说
PRESENT/GONE)。离线模式(`--offline`)对已冻结 run 写 `_ledger/agents_index/<run_id>.json`。
"""
from __future__ import annotations

import argparse
import gzip
import json
import re
import sys
from collections.abc import Iterator, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path

from autoresearch.common import workspace as ws

REPORT_FILENAME = "_transcript_bindings.json"
SCHEMA_VERSION = 1
CODEX_SESSIONS_ROOT = Path.home() / ".codex" / "sessions"
RUN_WINDOW_SLACK = timedelta(minutes=5)
USAGE_AGENT_TO_ROLE = {"macro-brief": "strategist"}
STAGE_FOR_ROLE = {"l3-rank": "l3", "l3-repair": "l3", "l4-card": "l4", "l4-intel": "l4",
                  "strategist": "stage0", "sector-brief": "stage0"}
_PATH_TOKEN_RE = re.compile(r"/[^\s\"'\\`)\]]+")


@dataclass(frozen=True)
class Marker:
    role: str
    product: re.Pattern
    inputs: tuple[re.Pattern, ...]
    subject_kind: str | None


MARKERS: tuple[Marker, ...] = (
    Marker("l4-card", re.compile(r"(?:^|/)details/(?P<subject>\d{6})\.md$"),
           (re.compile(r"_l4_prompt_(?P<subject>\d{6})\.md$"),), "code"),
    Marker("l4-intel", re.compile(r"(?:^|/)_l4_intel_(?P<subject>\d{6})\.md$"), (), "code"),
    Marker("sector-brief", re.compile(r"(?:^|/)sector_briefs/(?P<subject>[^/]+)\.md$"),
           (re.compile(r"/sector/\d{4}-\d{2}-\d{2}/(?P<subject>[^/]+)\.json$"),), "sector"),
    Marker("l3-rank", re.compile(r"(?:^|/)_l3_judged\.json$"), (re.compile(r"_l3_table\.md$"),), None),
    Marker("l3-repair", re.compile(r"(?:^|/)_l3_repair_patch\.json$"),
           (re.compile(r"_l3_repair_prompt\.md$"),), None),
    Marker("strategist", re.compile(r"(?:^|/)market_view\.md$"), (re.compile(r"strategist_pack\.json$"),), None),
)
MARKER_BY_ROLE = {m.role: m for m in MARKERS}


@dataclass(frozen=True)
class Identity:
    role: str
    subject: str | None
    subject_key: str | None
    verified: bool
    product: str | None


@dataclass
class Candidate:
    path: Path
    agent: str
    first_ts: str | None
    writes: list[str]
    reads: list[str]
    start_ordinal: int | None = None
    end_ordinal: int | None = None
    identity: Identity | None = None
    reason: str = ""
    segment_quality: str | None = None


def _subject_key(kind: str | None, subject: str | None) -> str | None:
    if subject is None:
        return None
    if kind == "sector":
        from autoresearch.trace.capsule import subject_key
        return subject_key(subject)
    return subject


def _under_root(path: str, roots: Sequence[str]) -> str | None:
    for root in roots:
        if path.startswith(root):
            return path[len(root):]
    return None


def derive_identity(writes: Sequence[str], reads: Sequence[str],
                    roots: Sequence[str]) -> tuple[Identity | None, str]:
    """(身份, 依据)。依据 = PRODUCT / INPUT_ONLY / MULTI_PRODUCT / NONE。产物必须在 roots 之一下。"""
    found: list[Identity] = []
    for path in writes:
        rel = _under_root(path, roots)
        if rel is None:
            continue
        for marker in MARKERS:
            hit = marker.product.search(rel)
            if hit:
                subject = hit.groupdict().get("subject")
                found.append(Identity(marker.role, subject, _subject_key(marker.subject_kind, subject),
                                      True, rel))
    distinct = {(i.role, i.subject_key) for i in found}
    if len(distinct) == 1:
        return found[0], "PRODUCT"
    if len(distinct) > 1:
        return None, "MULTI_PRODUCT"
    for path in reads:
        for marker in MARKERS:
            for pattern in marker.inputs:
                hit = pattern.search(path)
                if hit:
                    subject = hit.groupdict().get("subject")
                    return Identity(marker.role, subject, _subject_key(marker.subject_kind, subject),
                                    False, None), "INPUT_ONLY"
    return None, "NONE"


# ───────────────────────── Claude 定位器 ─────────────────────────

def _iter_jsonl(path: Path) -> Iterator[dict]:
    opener = gzip.open if path.suffix == ".gz" else open
    with opener(path, "rt", encoding="utf-8", errors="replace") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except Exception:  # noqa: BLE001 - 半行不该丢整份
                continue
            if isinstance(row, dict):
                yield row


def claude_paths(rows: Iterator[dict]) -> tuple[list[str], list[str], str | None]:
    """Claude transcript → (Write 目标, Read/Glob 路径, 首行时间戳)。"""
    writes: list[str] = []
    reads: list[str] = []
    first_ts: str | None = None
    for row in rows:
        ts = row.get("timestamp")
        if first_ts is None and isinstance(ts, str):
            first_ts = ts
        message = row.get("message")
        content = message.get("content") if isinstance(message, dict) else None
        if not isinstance(content, list):
            continue
        for block in content:
            if not isinstance(block, dict) or block.get("type") != "tool_use":
                continue
            inp = block.get("input") or {}
            name = block.get("name")
            if name == "Write" and isinstance(inp.get("file_path"), str):
                writes.append(inp["file_path"])
            elif name in ("Read", "Glob", "Grep"):
                for key in ("file_path", "pattern", "path"):
                    value = inp.get(key)
                    if isinstance(value, str):
                        reads.append(value)
    return writes, reads, first_ts


def claude_candidates(scan_dir: Path | str) -> tuple[list[Candidate], dict]:
    """`_token_usage.json` 的 subagent 行(只取判断腿五角色)→ 候选。壳 agent 与主会话不收。"""
    from autoresearch.scan.retention import ARCHIVE_TRANSCRIPT_AGENTS

    usage = Path(scan_dir) / "_token_usage.json"
    if not usage.is_file():
        return [], {"claude_usage_rows": 0, "reason": "no-usage-ledger"}
    try:
        rows = (json.loads(usage.read_text(encoding="utf-8")) or {}).get("rows") or []
    except (OSError, json.JSONDecodeError):
        return [], {"claude_usage_rows": 0, "reason": "bad-usage-ledger"}
    out: list[Candidate] = []
    seen: set[str] = set()
    for row in rows:
        agent = str(row.get("agent") or "")
        if row.get("role") not in (None, "subagent") or agent not in ARCHIVE_TRANSCRIPT_AGENTS:
            continue
        src = str(row.get("path") or "")
        if not src or src in seen:
            continue
        seen.add(src)
        path = Path(src)
        if path.is_symlink() or not path.is_file():
            out.append(Candidate(path, agent, None, [], [], reason="GONE_FILE"))
            continue
        writes, reads, first_ts = claude_paths(_iter_jsonl(path))
        out.append(Candidate(path, agent, first_ts, writes, reads))
    return out, {"claude_usage_rows": len(out)}


# ───────────────────────── 期望 × 候选 ─────────────────────────

def _row(invocation_id: str, identity: Identity | None, expectation: dict | None, *,
         status: str, reason: str, cand: Candidate | None, attempt: int | None) -> dict:
    return {
        "invocation_id": invocation_id,
        "role": (expectation or {}).get("role") or (identity.role if identity else None),
        "subject": (expectation or {}).get("subject") if expectation else (identity.subject if identity else None),
        "subject_key": (expectation or {}).get("subject_key") if expectation else (identity.subject_key if identity else None),
        "attempt": attempt,
        "status": status,
        "reason": reason,
        "path": str(cand.path) if cand else None,
        "product": (cand.identity.product if cand and cand.identity else None),
        "start_ordinal": cand.start_ordinal if cand else None,
        "end_ordinal": cand.end_ordinal if cand else None,
        "segment_quality": cand.segment_quality if cand else None,
        "expectation_source": (expectation or {}).get("expectation_source"),
    }


def assign(candidates: Sequence[Candidate], expectations: dict[str, dict],
           roots: Sequence[str]) -> tuple[list[dict], list[dict]]:
    """把候选配到期望上。返回 (每个期望/绑定一行, 未匹配候选)。"""
    unmatched: list[dict] = []
    groups: dict[tuple[str, str | None], list[Candidate]] = {}
    for cand in candidates:
        if cand.reason == "GONE_FILE":
            unmatched.append({"path": str(cand.path), "reason": "GONE_FILE"})
            continue
        if cand.identity is None:
            cand.identity, why = derive_identity(cand.writes, cand.reads, roots)
            if cand.identity is None:
                unmatched.append({"path": str(cand.path), "reason": why})
                continue
        groups.setdefault((cand.identity.role, cand.identity.subject_key), []).append(cand)

    exp_groups: dict[tuple[str, str | None], list[dict]] = {}
    for exp in expectations.values():
        exp_groups.setdefault((str(exp.get("role")), exp.get("subject_key")), []).append(exp)
    for group in exp_groups.values():
        group.sort(key=lambda e: int(e.get("attempt") or 1))

    rows: list[dict] = []
    for key, cands in sorted(groups.items(), key=lambda kv: (kv[0][0], str(kv[0][1]))):
        cands.sort(key=lambda c: (c.first_ts is None, c.first_ts or "", str(c.path)))
        exps = exp_groups.pop(key, [])
        if exps and len(cands) > len(exps):
            reason = f"{len(cands)} candidates > {len(exps)} expected attempts"
            for exp in exps:
                rows.append(_row(exp["invocation_id"], None, exp, status="AMBIGUOUS", reason=reason,
                                 cand=None, attempt=exp.get("attempt")))
            for cand in cands:
                unmatched.append({"path": str(cand.path), "reason": "AMBIGUOUS"})
            continue
        for k, cand in enumerate(cands, start=1):
            if exps:
                exp = exps[k - 1]
                status = "BOUND" if cand.identity.verified else "UNVERIFIED_BY_PRODUCT"
                reason = "product matched" if cand.identity.verified else "input marker only; product not written"
                rows.append(_row(exp["invocation_id"], cand.identity, exp, status=status, reason=reason,
                                 cand=cand, attempt=exp.get("attempt")))
            else:
                ident = cand.identity
                invocation_id = f"{ident.role}-{ident.subject_key or 'market'}-{k}"
                rows.append(_row(invocation_id, ident, None, status="BOUND_WITHOUT_EXPECTATION",
                                 reason="no dispatch/task event for this role+subject", cand=cand, attempt=k))
    for exps in exp_groups.values():
        for exp in exps:
            rows.append(_row(exp["invocation_id"], None, exp, status="GONE",
                             reason="no candidate transcript", cand=None, attempt=exp.get("attempt")))
    rows.sort(key=lambda r: (str(r["role"]), str(r["subject_key"] or ""), str(r["invocation_id"])))
    unmatched.sort(key=lambda u: u["path"])
    return rows, unmatched
```

- [ ] **Step 4: 跑用例确认通过**

Run: `uv run --no-sync python -m pytest tests/scan/test_transcript_binder.py -q`
Expected: 全绿

- [ ] **Step 5: 变异探针**

把 `derive_identity` 里 `rel = _under_root(path, roots)` 改成 `rel = path` → `test_product_outside_run_root_does_not_match` 必红;改回。

- [ ] **Step 6: 提交**

```bash
git add autoresearch/scan/transcript_binder.py tests/scan/test_transcript_binder.py
git commit -m "feat(scan): transcript binder core with product-path identity and Claude locator

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 5: Codex 定位器与 ordinal 区段

**Files:**
- Modify: `autoresearch/scan/transcript_binder.py`
- Test: `tests/scan/test_transcript_binder.py`

**Interfaces:**
- Produces:
  - `rollout_meta(path: Path) -> dict | None`(`session_meta.payload`);`rollout_bounds(path) -> tuple[str | None, str | None]`(首末行 `timestamp`)。
  - `enumerate_rollouts(sessions_root: Path, cwd: str, start: datetime, end: datetime) -> list[Path]`。
  - `is_subagent_rollout(meta: dict) -> bool`;`parent_thread_id(meta) -> str | None`。
  - `segment_for(rows: Sequence[dict], marker: Marker, subject: str | None, roots: Sequence[str]) -> tuple[int, int, bool, str | None] | None` → `(start_ordinal, end_ordinal, verified, product_rel)`。
  - `mark_segment_quality(cands: list[Candidate]) -> None`(同一 path 上区间重叠 → `interleaved`,否则 `exclusive`)。
  - `codex_candidates(handle, expectations: dict[str, dict], roots, *, sessions_root: Path | None = None, now: datetime | None = None) -> tuple[list[Candidate], dict]`。
- Consumes: `capsule.RunHandle`(`capsule/events/events.jsonl` 里 `RUN_STARTED` 的 `ts`)。

- [ ] **Step 1: 写失败用例**

追加到 `tests/scan/test_transcript_binder.py`:

```python
CODEX_ROOT = "/repo/context_codex/scan_runs/R1/staging/2026-09-08/"


def _rollout(path: Path, rows, *, cwd="/repo", thread="t1", parent=None, session="t1",
             ts0="2026-09-08T13:44:00.000Z"):
    meta = {"id": thread, "session_id": session, "cwd": cwd, "timestamp": ts0}
    if parent:
        meta["parent_thread_id"] = parent
        meta["source"] = {"subagent": {"thread_spawn": {"parent_thread_id": parent, "depth": 1}}}
    full = [{"timestamp": ts0, "ordinal": 1, "type": "session_meta", "payload": meta}] + rows
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in full) + "\n", encoding="utf-8")
    return path


def _exec(ordinal, cmd, ts="2026-09-08T14:00:00.000Z"):
    return {"timestamp": ts, "ordinal": ordinal, "type": "response_item",
            "payload": {"type": "custom_tool_call", "call_id": f"c{ordinal}", "name": "exec",
                        "input": "const r = await tools.exec_command({cmd:\"" + cmd + "\"})"}}


def test_enumerate_rollouts_filters_by_cwd_and_window(tmp_path):
    from datetime import datetime, timezone

    root = tmp_path / "sessions"
    good = _rollout(root / "2026/09/08/rollout-2026-09-08T21-41-33-aaa.jsonl",
                    [_exec(2, "cat " + CODEX_ROOT + "_l3_table.md", ts="2026-09-08T13:50:00.000Z")])
    _rollout(root / "2026/09/08/rollout-2026-09-08T09-00-00-bbb.jsonl",
             [_exec(2, "ls", ts="2026-09-08T01:00:00.000Z")], ts0="2026-09-08T01:00:00.000Z")   # 窗口外
    _rollout(root / "2026/09/08/rollout-2026-09-08T21-50-00-ccc.jsonl",
             [_exec(2, "ls", ts="2026-09-08T13:55:00.000Z")], cwd="/elsewhere")                 # 别的仓
    start = datetime(2026, 9, 8, 13, 39, tzinfo=timezone.utc)
    end = datetime(2026, 9, 8, 15, 0, tzinfo=timezone.utc)
    assert tb.enumerate_rollouts(root, "/repo", start, end) == [good]


def test_segment_for_anchors_on_input_and_product_within_root():
    rows = [
        _exec(2, "cat " + CODEX_ROOT + "_l4_prompt_600000.md"),
        _exec(3, "cat " + CODEX_ROOT + "_external_inputs/600000.SH_2026-09-08_slim.md"),
        _exec(4, "cat > " + CODEX_ROOT + "details/600000.md <<'EOF'\\n# card\\nEOF"),
        _exec(5, "cat " + CODEX_ROOT + "_l4_prompt_600001.md"),
        _exec(6, "cat > /repo/context_codex/scan_runs/R0/staging/2026-09-07/details/600001.md <<'EOF'\\nx\\nEOF"),
    ]
    seg = tb.segment_for(rows, tb.MARKER_BY_ROLE["l4-card"], "600000", [CODEX_ROOT])
    assert seg == (2, 4, True, "details/600000.md")
    seg2 = tb.segment_for(rows, tb.MARKER_BY_ROLE["l4-card"], "600001", [CODEX_ROOT])
    assert seg2 == (5, 5, False, None)          # 产物写到了别的 run 根下 → 只认输入,UNVERIFIED
    assert tb.segment_for(rows, tb.MARKER_BY_ROLE["l4-card"], "600002", [CODEX_ROOT]) is None


def test_segment_quality_marks_overlaps():
    a = tb.Candidate(Path("/r.jsonl"), "l4-card", None, [], [], start_ordinal=2, end_ordinal=6)
    b = tb.Candidate(Path("/r.jsonl"), "l4-card", None, [], [], start_ordinal=4, end_ordinal=8)
    c = tb.Candidate(Path("/r.jsonl"), "l4-card", None, [], [], start_ordinal=9, end_ordinal=10)
    tb.mark_segment_quality([a, b, c])
    assert (a.segment_quality, b.segment_quality, c.segment_quality) == ("interleaved", "interleaved", "exclusive")


def test_codex_candidates_bind_main_rollout_segments(codex_run, tmp_path, monkeypatch):
    """codex_run 夹具来自 tests/scan/conftest.py:一个 active Codex run。"""
    from datetime import datetime, timezone
    from autoresearch.trace.events import append_event

    handle, _ = codex_run
    root = str(handle.staging) + "/"
    events = handle.capsule / "events/events.jsonl"
    for event_type in ("TASK_CLAIMED", "TASK_SUCCEEDED"):
        append_event(events, run_id=handle.run_id, engine="codex", stage="l4",
                     invocation_id="l4-task-600000-attempt-1", attempt=1, subject="600000",
                     event_type=event_type, payload={"attempt": 1})
    (handle.staging / "_l3_judged.json").write_text("[]", encoding="utf-8")
    sessions = tmp_path / "sessions"
    started = json.loads(events.read_text(encoding="utf-8").splitlines()[0])["ts"]
    _rollout(sessions / "2026/08/27/rollout-2026-08-27T01-02-03-aaa.jsonl", [
        _exec(2, "cat " + root + "_l3_table.md", ts=started),
        _exec(3, "cat > " + root + "_l3_judged.json <<'EOF'\\n[]\\nEOF", ts=started),
        _exec(4, "cat " + root + "_l4_prompt_600000.md", ts=started),
        _exec(5, "cat > " + root + "details/600000.md <<'EOF'\\n# card\\nEOF", ts=started),
    ], cwd=str(tmp_path), ts0=started)
    monkeypatch.setattr(tb, "CODEX_SESSIONS_ROOT", sessions)
    monkeypatch.setattr(tb.ws, "context_root", lambda: tmp_path / "context_codex")

    from autoresearch.trace.capsule import agent_expectations

    cands, counts = tb.codex_candidates(handle, agent_expectations(handle), [root],
                                        now=datetime(2026, 8, 27, 3, 0, tzinfo=timezone.utc))
    by_role = {c.identity.role: c for c in cands}
    assert (by_role["l3-rank"].start_ordinal, by_role["l3-rank"].end_ordinal) == (2, 3)
    assert (by_role["l4-card"].start_ordinal, by_role["l4-card"].end_ordinal) == (4, 5)
    assert by_role["l4-card"].segment_quality == "exclusive"
    assert counts["codex_mains"] == 1


def test_codex_two_main_rollouts_is_ambiguous_not_guessed(codex_run, tmp_path, monkeypatch):
    from datetime import datetime, timezone

    handle, _ = codex_run
    events = handle.capsule / "events/events.jsonl"
    started = json.loads(events.read_text(encoding="utf-8").splitlines()[0])["ts"]
    sessions = tmp_path / "sessions"
    for name in ("aaa", "bbb"):
        _rollout(sessions / f"2026/08/27/rollout-2026-08-27T01-02-03-{name}.jsonl",
                 [_exec(2, "ls", ts=started)], cwd=str(tmp_path), thread=name, session=name, ts0=started)
    monkeypatch.setattr(tb, "CODEX_SESSIONS_ROOT", sessions)
    monkeypatch.setattr(tb.ws, "context_root", lambda: tmp_path / "context_codex")
    cands, counts = tb.codex_candidates(handle, {}, [str(handle.staging) + "/"],
                                        now=datetime(2026, 8, 27, 3, 0, tzinfo=timezone.utc))
    assert cands == [] and counts["codex_mains"] == 2 and counts["reason"] == "AMBIGUOUS"
```

- [ ] **Step 2: 跑用例确认失败**

Run: `uv run --no-sync python -m pytest tests/scan/test_transcript_binder.py -q -k "rollout or segment or codex"`
Expected: FAIL,`AttributeError: module ... has no attribute 'enumerate_rollouts'`

- [ ] **Step 3: 实现**

追加到 `transcript_binder.py`(`assign` 之后):

```python
# ───────────────────────── Codex 定位器 ─────────────────────────

def _parse_ts(value: object) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def rollout_meta(path: Path) -> dict | None:
    for row in _iter_jsonl(path):
        if row.get("type") == "session_meta":
            payload = row.get("payload")
            return payload if isinstance(payload, dict) else None
    return None


def rollout_bounds(path: Path) -> tuple[str | None, str | None]:
    first = last = None
    for row in _iter_jsonl(path):
        ts = row.get("timestamp")
        if isinstance(ts, str):
            first = first or ts
            last = ts
    return first, last


def is_subagent_rollout(meta: dict) -> bool:
    source = meta.get("source")
    return isinstance(source, dict) and isinstance(source.get("subagent"), dict)


def parent_thread_id(meta: dict) -> str | None:
    value = meta.get("parent_thread_id")
    if isinstance(value, str) and value:
        return value
    spawn = ((meta.get("source") or {}).get("subagent") or {}).get("thread_spawn") or {}
    value = spawn.get("parent_thread_id")
    return value if isinstance(value, str) and value else None


def enumerate_rollouts(sessions_root: Path, cwd: str, start: datetime, end: datetime) -> list[Path]:
    """`<root>/Y/M/D/rollout-*.jsonl`,日期跨 [start, end],cwd 相等且时间戳与窗口重叠。"""
    out: list[Path] = []
    day = start.date()
    while day <= end.date():
        folder = sessions_root / f"{day.year:04d}" / f"{day.month:02d}" / f"{day.day:02d}"
        for path in sorted(folder.glob("rollout-*.jsonl")) if folder.is_dir() else []:
            meta = rollout_meta(path)
            if not meta or str(meta.get("cwd") or "") != cwd:
                continue
            first, last = rollout_bounds(path)
            t_first, t_last = _parse_ts(first), _parse_ts(last)
            if t_first is None or t_last is None or t_last < start or t_first > end:
                continue
            out.append(path)
        day += timedelta(days=1)
    return sorted(out)


def _payload_paths(row: dict) -> list[str]:
    if row.get("type") != "response_item":
        return []
    text = json.dumps(row.get("payload"), ensure_ascii=False)
    return [m.group(0) for m in _PATH_TOKEN_RE.finditer(text)]


def segment_for(rows: Sequence[dict], marker: Marker, subject: str | None,
                roots: Sequence[str]) -> tuple[int, int, bool, str | None] | None:
    """按内容锚定一个调用的 ordinal 区段:起点=输入标记首现(无输入标记的角色取产物首现),
    终点=产物末现;产物必须在 roots 下。只有输入 → (start, start, False, None)。"""
    input_hits: list[int] = []
    product_hits: list[tuple[int, str]] = []
    for row in rows:
        ordinal = row.get("ordinal")
        if not isinstance(ordinal, int):
            continue
        for token in _payload_paths(row):
            for pattern in marker.inputs:
                hit = pattern.search(token)
                if hit and (subject is None or hit.groupdict().get("subject") == subject):
                    input_hits.append(ordinal)
            rel = _under_root(token, roots)
            if rel is not None:
                hit = marker.product.search(rel)
                if hit and (subject is None or hit.groupdict().get("subject") == subject):
                    product_hits.append((ordinal, rel))
    if product_hits:
        start = min(input_hits) if input_hits else product_hits[0][0]
        end_ordinal, product = max(product_hits, key=lambda p: p[0])
        return start, max(end_ordinal, start), True, product
    if input_hits:
        return min(input_hits), min(input_hits), False, None
    return None


def mark_segment_quality(cands: list[Candidate]) -> None:
    for cand in cands:
        cand.segment_quality = "exclusive"
    for i, a in enumerate(cands):
        for b in cands[i + 1:]:
            if a.path != b.path or None in (a.start_ordinal, a.end_ordinal, b.start_ordinal, b.end_ordinal):
                continue
            if a.start_ordinal <= b.end_ordinal and b.start_ordinal <= a.end_ordinal:
                a.segment_quality = b.segment_quality = "interleaved"


def _run_started(handle) -> datetime | None:
    events = handle.capsule / "events/events.jsonl"
    if not events.is_file():
        return None
    for row in _iter_jsonl(events):
        if row.get("event_type") == "RUN_STARTED":
            return _parse_ts(row.get("ts"))
    return None


def codex_candidates(handle, expectations: dict[str, dict], roots: Sequence[str], *,
                     sessions_root: Path | None = None,
                     now: datetime | None = None) -> tuple[list[Candidate], dict]:
    """主线程恰一份 → 每个期望在其内锚一个区段;子线程整份按产物路径认领。"""
    root = Path(sessions_root) if sessions_root else CODEX_SESSIONS_ROOT
    started = _run_started(handle)
    counts: dict = {"codex_rollouts": 0, "codex_mains": 0}
    if started is None:
        counts["reason"] = "NO_RUN_STARTED_EVENT"
        return [], counts
    end = now or datetime.now(timezone.utc)
    cwd = str(ws.context_root().resolve().parent)
    files = enumerate_rollouts(root, cwd, started - RUN_WINDOW_SLACK, end)
    metas = {path: rollout_meta(path) or {} for path in files}
    mains = [p for p in files if not is_subagent_rollout(metas[p])]
    counts.update({"codex_rollouts": len(files), "codex_mains": len(mains)})
    if len(mains) != 1:
        counts["reason"] = "AMBIGUOUS" if mains else "GONE"
        counts["candidates"] = [str(p) for p in mains]
        return [], counts
    main = mains[0]
    main_id = str(metas[main].get("id") or "")
    rows = list(_iter_jsonl(main))
    out: list[Candidate] = []
    for exp in sorted(expectations.values(), key=lambda e: str(e["invocation_id"])):
        marker = MARKER_BY_ROLE.get(str(exp.get("role")))
        if marker is None:
            continue
        seg = segment_for(rows, marker, exp.get("subject") if marker.subject_kind != "sector"
                          else exp.get("subject"), roots)
        if seg is None:
            continue
        start, end_ordinal, verified, product = seg
        out.append(Candidate(main, str(exp["role"]), None, [], [], start_ordinal=start,
                             end_ordinal=end_ordinal,
                             identity=Identity(str(exp["role"]), exp.get("subject"), exp.get("subject_key"),
                                               verified, product)))
    for child in files:
        if child == main or parent_thread_id(metas[child]) != main_id:
            continue
        crow = list(_iter_jsonl(child))
        tokens = [t for row in crow for t in _payload_paths(row)]
        ident, why = derive_identity(tokens, tokens, roots)
        if ident is not None:
            first, _ = rollout_bounds(child)
            out.append(Candidate(child, ident.role, first, tokens, tokens, identity=ident))
    mark_segment_quality(out)
    return out, counts
```

`sector-brief` 的 `subject` 是行业名,产物正则的 `subject` 组也是行业名,所以 `segment_for` 直接比 `exp["subject"]`(上面那个三元式两支相同,保留是为了让读者看见这一点;实现时可简化成 `exp.get("subject")`)。

- [ ] **Step 4: 跑用例确认通过**

Run: `uv run --no-sync python -m pytest tests/scan/test_transcript_binder.py -q`
Expected: 全绿

- [ ] **Step 5: 变异探针**

把 `segment_for` 改成返回整文件 `(min_ordinal, max_ordinal, ...)` → `test_segment_for_anchors_on_input_and_product_within_root` 与 `test_segment_quality_marks_overlaps` 必红;改回。

- [ ] **Step 6: 提交**

```bash
git add autoresearch/scan/transcript_binder.py tests/scan/test_transcript_binder.py
git commit -m "feat(scan): Codex rollout locator with ordinal segments

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 6: 接线 observe、配置开关、绑定报告、ARTIFACTS、文档

**Files:**
- Modify: `autoresearch/scan/transcript_binder.py`(`bind_run` / `safe_bind_run` / `configured_bind_transcripts` / `main`)
- Modify: `autoresearch/scan/post_run.py:905-918`(`publish_run_observation` 里 `retain()` 之前)
- Modify: `autoresearch/scan/user_config.py:90-160`
- Modify: `autoresearch/contracts/artifacts.py:202` 附近(staging/observe 段)
- Modify: `.claude/skills/scan-market/scan_config.jsonc`、`SKILL.md`(配置表)、`STAGES.md:233`
- Test: `tests/scan/test_transcript_binder.py`、`tests/scan/test_post_run.py`

**Interfaces:**
- Produces:
  - `bind_run(run_id: str, scan_dir: Path | str, *, engine: str | None = None, sessions_root: Path | None = None, now: datetime | None = None) -> dict`(报告文档,同时写 `scan_dir/_transcript_bindings.json` 并对 BOUND/UNVERIFIED/BOUND_WITHOUT_EXPECTATION 行调 `capsule.bind_transcript`)。
  - `safe_bind_run(scan_dir) -> dict | None`(失败纪律版;开关关/无 active run 时也写一份 `enabled=false` 的报告)。
  - `configured_bind_transcripts() -> bool`(`scan_config.jsonc` `retention.bind_transcripts`,缺省 `True`)。
  - 报告形状(spec §5.7):`{"schema_version": 1, "run_id", "engine", "enabled", "reason", "candidates": {...}, "rows": [...], "unmatched": [...], "counts": {"bound", "unverified", "ambiguous", "gone", "without_expectation"}}`。

- [ ] **Step 1: 写失败用例**

追加到 `tests/scan/test_transcript_binder.py`:

```python
@pytest.fixture
def claude_active_run(tmp_path, monkeypatch):
    """一个 active 的 Claude run(与 tests/trace 的 claude_run 同法,但落在 scan 包里)。"""
    from autoresearch.common import workspace as ws_mod
    from autoresearch.trace import capsule as capsule_mod

    monkeypatch.setattr(ws_mod, "ENGINE", "claude")
    monkeypatch.setattr(ws_mod, "context_root", lambda: tmp_path / "context_claude")
    monkeypatch.setattr(ws_mod, "reports_root", lambda: tmp_path / "reports_claude")
    monkeypatch.delenv("AUTORESEARCH_RUN_ID", raising=False)
    monkeypatch.setattr("autoresearch.scan.user_config.DEFAULT_PINNED_PATH", tmp_path / "missing-pinned.jsonc")
    monkeypatch.setattr(capsule_mod, "snapshot_identity",
                        lambda *a, **k: {"ok": True, "components": {}, "missing": [], "errors": []})
    handle = capsule_mod.begin_run("scan-market", "2026-09-09", "claude", {}, session_ref="sess-1")
    monkeypatch.setenv("AUTORESEARCH_RUN_ID", handle.run_id)
    return handle


def _dispatch(handle, role, code):
    from autoresearch.trace.capsule import record_agent_boundary

    for event_type in ("AGENT_DISPATCHED", "AGENT_COMPLETED"):
        record_agent_boundary(handle.run_id, event_type, role=role,
                              invocation_id=f"{role}-{code}-1", attempt=1, subject=code)


def test_bind_run_end_to_end_claude(claude_active_run, tmp_path):
    from autoresearch.trace.capsule import materialize_agent_index

    handle = claude_active_run
    root = str(handle.staging) + "/"
    _dispatch(handle, "l4-card", "600000")
    _dispatch(handle, "l4-intel", "600000")
    card = _claude_jsonl(tmp_path / "harness" / "agent-c.jsonl",
                         reads=[root + "_l4_prompt_600000.md"], writes=[root + "details/600000.md"])
    intel = _claude_jsonl(tmp_path / "harness" / "agent-i.jsonl", writes=[root + "_l4_intel_600000.md"])
    _usage(handle.staging, [{"agent": "l4-card", "role": "subagent", "path": str(card)},
                            {"agent": "l4-intel", "role": "subagent", "path": str(intel)}])

    report = tb.bind_run(handle.run_id, handle.staging, engine="claude")

    assert report["counts"] == {"bound": 2, "unverified": 0, "ambiguous": 0, "gone": 0, "without_expectation": 0}
    on_disk = json.loads((handle.staging / tb.REPORT_FILENAME).read_text(encoding="utf-8"))
    assert on_disk == report
    index = materialize_agent_index(handle.run_id)
    assert index["coverage"] == {"expected": 2, "present": 2, "missing": 0}
    rows = {r["invocation_id"]: r for r in index["invocations"]}
    assert rows["l4-card-600000-1"]["subject"] == "600000"
    assert (handle.capsule / "agents/normalized/l4-card-600000-1.json").is_file()
    bindings = (handle.capsule / "agents/bindings.jsonl").read_text(encoding="utf-8")
    assert '"stage": "l4"' in bindings


def test_bind_run_is_idempotent(claude_active_run, tmp_path):
    handle = claude_active_run
    root = str(handle.staging) + "/"
    _dispatch(handle, "l4-card", "600000")
    card = _claude_jsonl(tmp_path / "harness" / "agent-c.jsonl", writes=[root + "details/600000.md"])
    _usage(handle.staging, [{"agent": "l4-card", "role": "subagent", "path": str(card)}])
    first = tb.bind_run(handle.run_id, handle.staging, engine="claude")
    second = tb.bind_run(handle.run_id, handle.staging, engine="claude")
    assert first == second
    lines = [l for l in (handle.capsule / "agents/bindings.jsonl").read_text(encoding="utf-8").splitlines() if l.strip()]
    assert len(lines) == 1


def test_safe_bind_run_writes_disabled_report_when_knob_is_off(tmp_path, monkeypatch):
    monkeypatch.setattr(tb, "configured_bind_transcripts", lambda: False)
    scan = tmp_path / "staging"
    scan.mkdir()
    report = tb.safe_bind_run(scan)
    assert report["enabled"] is False and report["reason"] == "retention.bind_transcripts=false"
    assert (scan / tb.REPORT_FILENAME).is_file()


def test_safe_bind_run_without_active_run_is_honest(tmp_path, monkeypatch):
    monkeypatch.delenv("AUTORESEARCH_RUN_ID", raising=False)
    scan = tmp_path / "staging"
    scan.mkdir()
    report = tb.safe_bind_run(scan)
    assert report["enabled"] is False and "no active run" in report["reason"]


def test_safe_bind_run_never_raises(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(tb, "bind_run", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))
    monkeypatch.setenv("AUTORESEARCH_RUN_ID", "20260909T131053825538Z")
    scan = tmp_path / "staging"
    scan.mkdir()
    assert tb.safe_bind_run(scan) is None
    assert "绑定失败" in capsys.readouterr().err


def test_configured_bind_transcripts_default_true_and_reads_config(monkeypatch):
    monkeypatch.setattr("autoresearch.scan.user_config.load_user_config", lambda: {})
    assert tb.configured_bind_transcripts() is True
    monkeypatch.setattr("autoresearch.scan.user_config.load_user_config",
                        lambda: {"retention": {"bind_transcripts": False}})
    assert tb.configured_bind_transcripts() is False
```

追加到 `tests/scan/test_post_run.py`:

```python
def test_publish_run_observation_binds_transcripts_before_retain(tmp_path, monkeypatch):
    """接线验证(FN-1 家训:生产者没接线 = 没做):observe 必须在 retain 之前调 safe_bind_run,
    否则绑定报告进不了镜像、绑定也赶不上 finalize。"""
    from autoresearch.scan import retention, transcript_binder

    calls: list[str] = []
    monkeypatch.setattr(transcript_binder, "safe_bind_run", lambda scan: calls.append("bind") or {})
    monkeypatch.setattr(retention, "retain", lambda scan, run: calls.append("retain") or
                        {"errors": [], "mirrored": 0, "inputs": {}, "transcripts": {}, "lake_files": 0,
                         "manifest": None, "evidence_status": "LEGACY_PARTIAL",
                         "integrity_ok": None, "completeness_ok": None})
    scan = tmp_path / "2026-07-31"
    scan.mkdir()
    (scan / "_l4_tasks.json").write_text(json.dumps({"schema_version": 1, "date": "2026-07-31", "tasks": {}}),
                                         encoding="utf-8")
    report = tmp_path / "report"
    report.mkdir()
    (report / "manifest.json").write_text(json.dumps({"analysis_date": "2026-07-31"}), encoding="utf-8")

    publish_run_observation(scan, real_scan=False, report_dir=report)

    assert calls == ["bind", "retain"]
```

若该用例因空 report 目录在 `write_artifact_index(scan, report_dir=report)` 处抛错,再 monkeypatch `autoresearch.scan.artifacts.write_artifact_index` 为 `lambda scan, report_dir=None: (scan / "artifact_index.json").write_text("{}") or scan / "artifact_index.json"`;用例的判据只是调用顺序。

- [ ] **Step 2: 跑用例确认失败**

Run: `uv run --no-sync python -m pytest tests/scan/test_transcript_binder.py tests/scan/test_post_run.py -q -k "bind_run or safe_bind or configured or binds_transcripts"`
Expected: FAIL(`AttributeError: bind_run` / `safe_bind_run` / calls == []`)

- [ ] **Step 3: 实现 `bind_run` / `safe_bind_run` / 开关 / CLI**

追加到 `transcript_binder.py`:

```python
# ───────────────────────── 编排、报告、开关 ─────────────────────────

def configured_bind_transcripts() -> bool:
    """`scan_config.jsonc` 的 `retention.bind_transcripts`;缺文件/缺块/错型 → True(内建默认)。"""
    try:
        from autoresearch.scan.user_config import load_user_config
        block = load_user_config().get("retention") or {}
    except Exception as exc:  # noqa: BLE001 — 配置层故障不挡发布,但降级必须可见
        print(f"[transcript_binder] scan_config 读取失败({exc!r})→ bind_transcripts 用内建默认 True",
              file=sys.stderr)
        block = {}
    value = block.get("bind_transcripts", True)
    return value if isinstance(value, bool) else True


def _write_report(scan_dir: Path, doc: dict) -> dict:
    target = Path(scan_dir) / REPORT_FILENAME
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_name(target.name + ".tmp")
    tmp.write_text(json.dumps(doc, ensure_ascii=False, sort_keys=True, indent=1) + "\n", encoding="utf-8")
    tmp.replace(target)
    return doc


def _empty_report(run_id: str | None, engine: str, *, enabled: bool, reason: str) -> dict:
    return {"schema_version": SCHEMA_VERSION, "run_id": run_id, "engine": engine, "enabled": enabled,
            "reason": reason, "candidates": {}, "rows": [], "unmatched": [],
            "counts": {"bound": 0, "unverified": 0, "ambiguous": 0, "gone": 0, "without_expectation": 0}}


def _counts(rows: Sequence[dict]) -> dict:
    keys = {"BOUND": "bound", "UNVERIFIED_BY_PRODUCT": "unverified", "AMBIGUOUS": "ambiguous",
            "GONE": "gone", "BOUND_WITHOUT_EXPECTATION": "without_expectation"}
    out = {v: 0 for v in keys.values()}
    for row in rows:
        out[keys[row["status"]]] += 1
    return out


def bind_run(run_id: str, scan_dir: Path | str, *, engine: str | None = None,
             sessions_root: Path | None = None, now: datetime | None = None) -> dict:
    from autoresearch.trace import capsule as _capsule

    handle = _capsule.require_active_run(run_id)
    engine = str(engine or handle.engine)
    expectations = _capsule.agent_expectations(handle)
    roots = sorted({str(handle.staging) + "/", str(handle.staging.resolve()) + "/"})
    if engine == "claude":
        candidates, counts = claude_candidates(Path(scan_dir))
    else:
        candidates, counts = codex_candidates(handle, expectations, roots,
                                              sessions_root=sessions_root, now=now)
    rows, unmatched = assign(candidates, expectations, roots)
    for row in rows:
        if row["status"] not in ("BOUND", "UNVERIFIED_BY_PRODUCT", "BOUND_WITHOUT_EXPECTATION"):
            continue
        _capsule.bind_transcript(
            run_id, row["path"], role=str(row["role"]), invocation_id=str(row["invocation_id"]),
            subject=row["subject_key"], engine=engine,
            start_ordinal=row["start_ordinal"], end_ordinal=row["end_ordinal"],
            stage=STAGE_FOR_ROLE.get(str(row["role"]), "l4"),
        )
    doc = {"schema_version": SCHEMA_VERSION, "run_id": run_id, "engine": engine, "enabled": True,
           "reason": None, "candidates": counts, "rows": rows, "unmatched": unmatched,
           "counts": _counts(rows)}
    return _write_report(Path(scan_dir), doc)


def safe_bind_run(scan_dir: Path | str) -> dict | None:
    """失败纪律版:开关关/无 active run 也写一份 `enabled=false` 的报告;异常只打一行。"""
    scan = Path(scan_dir)
    try:
        if not configured_bind_transcripts():
            return _write_report(scan, _empty_report(ws.active_run_id(), ws.ENGINE, enabled=False,
                                                     reason="retention.bind_transcripts=false"))
        run_id = ws.active_run_id()
        if run_id is None:
            return _write_report(scan, _empty_report(None, ws.ENGINE, enabled=False,
                                                     reason="no active run (AUTORESEARCH_RUN_ID unset)"))
        return bind_run(run_id, scan)
    except Exception as exc:  # noqa: BLE001 — 留存是加法,不能毁掉一次跑完的扫描
        print(f"[transcript_binder] 绑定失败: {type(exc).__name__}: {exc}", file=sys.stderr)
        return None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="现场重建:transcript 绑定(零 LLM)")
    parser.add_argument("scan", nargs="?", help="staging 目录(active run;默认 $AUTORESEARCH_RUN_ID 的 staging)")
    parser.add_argument("--run-id", default=None)
    args = parser.parse_args(argv)
    run_id = args.run_id or ws.active_run_id()
    if run_id is None:
        print("缺 --run-id / AUTORESEARCH_RUN_ID", file=sys.stderr)
        return 2
    scan = Path(args.scan) if args.scan else ws.scan_run_root(run_id) / "staging"
    if not args.scan:
        dates = sorted(p for p in scan.iterdir() if p.is_dir()) if scan.is_dir() else []
        scan = dates[-1] if dates else scan
    doc = bind_run(run_id, scan)
    print(json.dumps({"ok": True, "counts": doc["counts"], "report": str(Path(scan) / REPORT_FILENAME)},
                     ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 4: 接线 `post_run.publish_run_observation`**

在 `post_run.py` 的

```python
        with contextlib.suppress(Exception):
            from autoresearch.scan.retention import retain

            ret = retain(scan, report)
```

之前插入:

```python
        # 现场重建(2026-09-12 设计 §5.1):把判断腿 transcript 绑到 capsule 调用上。必须在
        # retain 之前(报告要进镜像)、finalize 之前(绑定要求 run 仍 active)。失败只打一行。
        from autoresearch.scan import transcript_binder as _binder

        _binder.safe_bind_run(scan)
```

- [ ] **Step 5: 配置开关**

`user_config.py`:`_TOP_WHITELIST` 加 `"retention"`;`_SUB_WHITELIST` 加 `"retention": {"bind_transcripts"}`;`_KNOB_TYPES` 加 `("retention", "bind_transcripts"): (_t_bool, "boolean")`。

`.claude/skills/scan-market/scan_config.jsonc` 在 `"relative_buy"` 块之前加:

```jsonc
  // ── 现场重建(2026-09-12 设计 §5.1)【生效点:post_run observe → transcript_binder.safe_bind_run】
  // 把判断腿 transcript(Claude subagent jsonl / Codex 主线程 rollout 区段)绑到 capsule 的
  // agent invocation 上;false = 回到 2026-09-12 前的行为(agents/index.json 全 GONE)。
  "retention": { "bind_transcripts": true },
```

`SKILL.md` 配置表加一行:

```
| 收尾 | `retention.bind_transcripts` | true·false | `transcript_binder.configured_bind_transcripts` → `post_run.publish_run_observation`(retain 之前)→ `capsule.bind_transcript`。false = 不绑定,`agents/index.json` 回到全 GONE(2026-09-12 设计 §5.8 回滚杆) |
```

`STAGES.md` 第 233 行「CP7 的 `post_run observe` 末尾调 `capsule finalize`」那句之前加一句:「observe 在 `retain()` 之前先跑 `transcript_binder.safe_bind_run`(Claude 按 `_token_usage.json` 行、Codex 按 `~/.codex/sessions/` 主线程 rollout 的 ordinal 区段,判据=写到本 run staging 根的产物路径;报告 `_transcript_bindings.json`;2026-09-12 设计 §5),」。

- [ ] **Step 6: 登记 ARTIFACTS**

`artifacts.py` 在 `Artifact("relative_buy_decision", ...)` 之后加:

```python
    Artifact("transcript_bindings", "_transcript_bindings.json", "staging", "observe", "transcript_binder",
             "json", "gated", required_when="post_run observe 跑过(retention.bind_transcripts 关着也写 enabled=false)"),
```

Run: `uv run --no-sync python -m autoresearch.contracts.emit --write && uv run --no-sync python -m pytest tests/contracts -q`
Expected: 全绿(若 `emit --write` 改了 workflow 文件,一并提交)

- [ ] **Step 7: 跑用例确认通过 + 全量**

Run: `uv run --no-sync python -m pytest tests/scan/test_transcript_binder.py tests/scan/test_post_run.py tests/scan/test_user_config.py tests/contracts -q`
Expected: 全绿

Run: `AUTORESEARCH_ENGINE=claude uv run --no-sync python -m pytest -q -x && AUTORESEARCH_ENGINE=codex uv run --no-sync python -m pytest -q -x && uv run --no-sync ruff check autoresearch tests`
Expected: 全绿

- [ ] **Step 8: 变异探针**

注释掉 `post_run.py` 里 `_binder.safe_bind_run(scan)` → `test_publish_run_observation_binds_transcripts_before_retain` 必红;改回。

- [ ] **Step 9: 提交**

```bash
git add autoresearch/scan/transcript_binder.py autoresearch/scan/post_run.py autoresearch/scan/user_config.py autoresearch/contracts/artifacts.py .claude/skills/scan-market/scan_config.jsonc .claude/skills/scan-market/SKILL.md .claude/skills/scan-market/STAGES.md .claude/workflows tests/scan/test_transcript_binder.py tests/scan/test_post_run.py
git commit -m "feat(scan): bind judgement-leg transcripts in post_run observe (retention.bind_transcripts)

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 7: `chain_view` ⑦ 修查找、新增 ⑦b 研究员现场、① 绑定行

**Files:**
- Modify: `autoresearch/scan/chain_view.py`(`Sources`、`_sec_identity`、`_sec_l4`、新 `_sec_l4b`、`render`)
- Test: `tests/scan/test_chain_view_agents.py`(新)

**Interfaces:**
- Produces:
  - `Sources.capsule: Path`、`Sources.ledger: Path`、`Sources.agents_index() -> tuple[str | None, dict | None]`(来源 `"capsule"` → `"ledger"` → `None`)、`Sources.normalized_doc(source: str, row: dict) -> dict | None`。
  - `find_input(src: Sources, pattern: str) -> tuple[Path | None, str | None]`(三级:`trace/inputs/slim/` → `trace/staging/_external_inputs/` → 共享 staging `_external_inputs/`;返回命中级别)。
  - `_sec_l4b(src, code6) -> list[str]`(段标题 `## ⑦b 研究员现场`)。
  - ① 段新行 `- transcript 绑定:<n_bound>/<n_rows> · AMBIGUOUS <k> · 来源 <capsule|ledger|缺席>`。
- Consumes: `_transcript_bindings.json`(Task 6 报告)、`capsule/agents/index.json` 与 `agents/normalized/<invocation>.json`(Task 3 形状:`items[].{index,kind,payload,timestamp}`)。

- [ ] **Step 1: 写失败用例**

新建 `tests/scan/test_chain_view_agents.py`:

```python
"""chain_view ⑦b「研究员现场」(2026-09-12 设计 §7)。判据:读了什么带字节与哈希、没读什么
单独一行、写了什么与发布卡对得上、可见推理有截断、来源级如实标注、两次渲染逐字节相等。"""
from __future__ import annotations

import hashlib
import json

from autoresearch.common import workspace as ws
from autoresearch.scan import chain_view

CARD = "# 决策卡 — 603317 天味食品 @ 2026-08-25\n\n**Rating**: Hold\n"
ROOT = "/ctx/context_claude/scan_runs/R1/staging/2026-08-25/"


def _run(tmp_path):
    run = tmp_path / ws.reports_root() / "scan" / "20260825-0825_2149"
    (run / "details").mkdir(parents=True)
    (run / "trace" / "staging" / "_external_inputs").mkdir(parents=True)
    (run / "manifest.json").write_text(json.dumps({"analysis_date": "2026-08-25"}), encoding="utf-8")
    (run / "brief.md").write_text("- ✅ relative BUY:天味食品 603317\n", encoding="utf-8")
    (run / "details" / "天味食品.md").write_text(CARD, encoding="utf-8")
    (run / "trace" / "staging" / "_external_inputs" / "603317.SZ_2026-08-25_slim.md").write_text(
        "# slim\n" + "x" * 9000, encoding="utf-8")
    return run


def _normalized(run, invocation_id, items, *, source="capsule"):
    if source == "capsule":
        base = run / "capsule" / "agents"
        rel = f"agents/normalized/{invocation_id}.json"
    else:
        base = ws.reports_root() / "scan" / "_ledger" / "agents_index" / run.name
        rel = f"{run.name}/normalized/{invocation_id}.json"
    (base / "normalized").mkdir(parents=True, exist_ok=True)
    (base / "normalized" / f"{invocation_id}.json").write_text(json.dumps({
        "schema_version": 2, "engine": "claude", "invocation_id": invocation_id, "role": "l4-card",
        "subject": "603317", "status": "SUCCEEDED", "model": "claude-opus-5", "effort": "max",
        "items": [{"index": i, "kind": k, "payload": p, "timestamp": "2026-08-25T13:50:00.000Z"}
                  for i, (k, p) in enumerate(items)]}, ensure_ascii=False), encoding="utf-8")
    return rel


def _index(run, rows, *, source="capsule"):
    doc = {"schema_version": 2, "run_id": "R1", "invocations": rows,
           "coverage": {"expected": len(rows), "present": sum(r["status"] == "PRESENT" for r in rows),
                        "missing": sum(r["status"] != "PRESENT" for r in rows)}}
    if source == "capsule":
        path = run / "capsule" / "agents" / "index.json"
    else:
        path = ws.reports_root() / "scan" / "_ledger" / "agents_index" / f"{run.name}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(doc, ensure_ascii=False), encoding="utf-8")


def _bindings(run, rows):
    (run / "trace" / "staging" / "_transcript_bindings.json").write_text(json.dumps({
        "schema_version": 1, "run_id": "R1", "engine": "claude", "enabled": True, "reason": None,
        "candidates": {}, "rows": rows, "unmatched": [],
        "counts": {"bound": sum(r["status"] == "BOUND" for r in rows), "unverified": 0,
                   "ambiguous": sum(r["status"] == "AMBIGUOUS" for r in rows), "gone": 0,
                   "without_expectation": 0}}, ensure_ascii=False), encoding="utf-8")


def _card_items():
    slim = "# slim\n" + "x" * 9000
    return [
        ("message", {"message_id": "m1", "role": "assistant", "text": "先读 prompt。" * 80}),
        ("tool_request", {"message_id": "m1", "tool_use_id": "r1", "tool_name": "Read",
                          "input": {"file_path": ROOT + "_l4_prompt_603317.md"}}),
        ("tool_result", {"tool_use_id": "r1", "content": "prompt", "is_error": False,
                         "bytes": 6, "sha256": hashlib.sha256(b"prompt").hexdigest()}),
        ("tool_request", {"message_id": "m1", "tool_use_id": "r2", "tool_name": "Read",
                          "input": {"file_path": ROOT + "_external_inputs/603317.SZ_2026-08-25_slim.md"}}),
        ("tool_result", {"tool_use_id": "r2", "content": slim, "is_error": False,
                         "bytes": len(slim.encode()), "sha256": hashlib.sha256(slim.encode()).hexdigest()}),
        ("tool_request", {"message_id": "m2", "tool_use_id": "s1", "tool_name": "WebSearch",
                          "input": {"query": "天味食品 公告"}}),
        ("tool_result", {"tool_use_id": "s1", "content": "http://a http://b", "is_error": False,
                         "bytes": 17, "sha256": hashlib.sha256(b"http://a http://b").hexdigest()}),
        ("tool_request", {"message_id": "m3", "tool_use_id": "w1", "tool_name": "Write",
                          "input": {"file_path": ROOT + "details/603317.md", "content": CARD}}),
        ("tool_result", {"tool_use_id": "w1", "content": "ok", "is_error": False,
                         "bytes": 2, "sha256": hashlib.sha256(b"ok").hexdigest()}),
    ]


def test_l4b_lists_reads_unread_searches_writes_and_text(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    run = _run(tmp_path)
    rel = _normalized(run, "l4-card-603317-1", _card_items())
    _index(run, [{"invocation_id": "l4-card-603317-1", "role": "l4-card", "subject": "603317",
                  "subject_key": "603317", "status": "PRESENT", "reason": None, "normalized": rel,
                  "model": "claude-opus-5", "effort": "max", "items": 9,
                  "expectation_source": "agent_events"}])
    _bindings(run, [{"invocation_id": "l4-card-603317-1", "role": "l4-card", "subject": "603317",
                     "subject_key": "603317", "attempt": 1, "status": "BOUND", "reason": "product matched",
                     "path": "/h/agent-a.jsonl", "product": "details/603317.md", "start_ordinal": None,
                     "end_ordinal": None, "segment_quality": None, "expectation_source": "agent_events"}])

    text = chain_view.render(run, "603317")

    assert "## ⑦b 研究员现场" in text
    assert "- transcript 绑定:1/1 · AMBIGUOUS 0 · 来源 capsule" in text
    assert "l4-card-603317-1" in text and "PRESENT" in text and "model claude-opus-5" in text
    assert "_l4_prompt_603317.md · 6 B · sha " + hashlib.sha256(b"prompt").hexdigest()[:12] + " · [prompt]" in text
    assert "[slim]" in text
    assert "- 没读:intel、deep" in text
    assert "- 搜了:1 次" in text and "天味食品 公告 · URL 2" in text
    assert "details/603317.md · 与发布卡 一致" in text
    assert "可见推理" in text and ("先读 prompt。" * 80)[:300] in text and ("先读 prompt。" * 80) not in text


def test_l4_slim_lookup_finds_staging_external_inputs(tmp_path, monkeypatch):
    """E3 假读数:slim 在 trace/staging/_external_inputs 却被印成「缺席」。"""
    monkeypatch.chdir(tmp_path)
    run = _run(tmp_path)
    text = chain_view.render(run, "603317")
    assert "603317.SZ_2026-08-25_slim.md" in text and "来源 staging" in text
    assert "2026-08-26 前未留存" not in text


def test_l4b_falls_back_to_ledger_index_and_labels_it(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    run = _run(tmp_path)
    rel = _normalized(run, "l4-card-603317-1", _card_items(), source="ledger")
    _index(run, [{"invocation_id": "l4-card-603317-1", "role": "l4-card", "subject": "603317",
                  "subject_key": "603317", "status": "PRESENT", "reason": None, "normalized": rel,
                  "model": "claude-opus-5", "effort": "max", "items": 9,
                  "expectation_source": "products", "source": "trace_transcripts"}], source="ledger")
    text = chain_view.render(run, "603317")
    assert "来源 ledger" in text and "[prompt]" in text


def test_l4b_absent_is_named(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    run = _run(tmp_path)
    text = chain_view.render(run, "603317")
    assert "## ⑦b 研究员现场" in text
    assert "缺席(无 capsule agents 索引,也无账本外部索引)" in text
    assert "- transcript 绑定:未记录" in text


def test_l4b_gone_row_prints_reason(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    run = _run(tmp_path)
    _index(run, [{"invocation_id": "l4-card-603317-1", "role": "l4-card", "subject": "603317",
                  "subject_key": "603317", "status": "GONE",
                  "reason": "reached dispatch has no bound transcript", "normalized": None,
                  "model": None, "effort": None, "items": None, "expectation_source": "agent_events"}])
    text = chain_view.render(run, "603317")
    assert "GONE · reached dispatch has no bound transcript" in text


def test_render_is_byte_stable(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    run = _run(tmp_path)
    rel = _normalized(run, "l4-card-603317-1", _card_items())
    _index(run, [{"invocation_id": "l4-card-603317-1", "role": "l4-card", "subject": "603317",
                  "subject_key": "603317", "status": "PRESENT", "reason": None, "normalized": rel,
                  "model": "claude-opus-5", "effort": "max", "items": 9,
                  "expectation_source": "agent_events"}])
    assert chain_view.render(run, "603317") == chain_view.render(run, "603317")
```

- [ ] **Step 2: 跑用例确认失败**

Run: `uv run --no-sync python -m pytest tests/scan/test_chain_view_agents.py -q`
Expected: FAIL(`⑦b` 不存在 / 「2026-08-26 前未留存」仍在)

- [ ] **Step 3: 实现 `Sources` 扩展与 `find_input`**

`chain_view.py` 顶部加 `import hashlib, re`。`Sources.__init__` 末尾加:

```python
        self.capsule = self.run / "capsule"
        self.ledger = ws.reports_root() / "scan" / "_ledger"
```

`Sources` 加两个方法:

```python
    def agents_index(self) -> tuple[str | None, dict | None]:
        """agents 索引:capsule 在场优先,其次账本外部索引(冻结 run 的离线索引),都没有 → (None, None)。"""
        for source, path in (("capsule", self.capsule / "agents" / "index.json"),
                             ("ledger", self.ledger / "agents_index" / f"{self.run.name}.json")):
            if path.is_file():
                try:
                    return source, json.loads(path.read_text(encoding="utf-8"))
                except (OSError, json.JSONDecodeError):
                    return source, None
        return None, None

    def normalized_doc(self, source: str, row: dict) -> dict | None:
        rel = row.get("normalized")
        if not rel:
            return None
        base = self.capsule if source == "capsule" else self.ledger / "agents_index"
        path = base / str(rel)
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
```

模块级函数(放在 `_sec_l4` 之前):

```python
def find_input(src: Sources, pattern: str) -> tuple[Path | None, str | None]:
    """slim/deep 三级查找:trace/inputs/slim → trace/staging/_external_inputs → 共享 staging。"""
    levels = [(src.inputs / "slim", "inputs"), (src.mirror / "_external_inputs", "staging")]
    if src.shared is not None:
        levels.append((src.shared / "_external_inputs", "shared"))
    for base, level in levels:
        if not base.is_dir():
            continue
        hit = next(iter(sorted(base.glob(pattern))), None)
        if hit is not None:
            if level == "shared":
                src.used_shared = True
            return hit, level
    return None, None
```

`_sec_l4` 里把 `slim = ...`/`deep = ...` 两处改为:

```python
    slim, slim_level = find_input(src, f"*{code6}*_slim.md")
    deep, deep_level = find_input(src, f"*{code6}*_slim_deep.md")
    out += [
        f"- 派发 prompt:{src.rel(prompt)}",
        f"- slim(P1–P3 表面块):{src.rel(slim)}"
        + (f" · {slim.stat().st_size} B(>8KB 才可信) · 来源 {slim_level}" if slim else "(未留存)"),
        f"- deep(P4 深核):{src.rel(deep)}" + (f" · 来源 {deep_level}" if deep else ""),
        f"- 活体情报:{src.rel(intel)}",
    ]
```

- [ ] **Step 4: 实现 ⑦b**

```python
_READ_TOOLS = {"Read", "Glob", "Grep"}
_SEARCH_TOOLS = {"WebSearch", "WebFetch", "web_search"}
_INPUT_KINDS = (("_l4_prompt_", "prompt"), ("_slim_deep.md", "deep"), ("_slim.md", "slim"),
                ("_l4_intel_", "intel"), ("/dossiers/", "dossier"))
_KIND_ORDER = {"prompt": 0, "slim": 1, "deep": 2, "intel": 3, "dossier": 4, "其它": 5}
_EXPECTED_READS = {"l4-card": ("prompt", "slim", "intel", "deep"), "l4-intel": ()}
_PATH_TOKEN_RE = re.compile(r"/[^\s\"'\\`)\]]+")
_TEXT_CAP, _TEXT_MAX = 300, 6


def _classify(path: str) -> str:
    for needle, kind in _INPUT_KINDS:
        if needle in path:
            return kind
    return "其它"


def _short_path(path: str) -> str:
    return path.rsplit("/", 1)[-1] if "/" in path else path


def _agent_evidence(doc: dict) -> dict:
    """normalized items → {reads, searches, writes, texts}。Claude 读 Read/Glob/Grep 请求;
    Codex 读 exec 命令串里的路径 token(`> path`/`tee path` 记作写)。"""
    items = doc.get("items") or []
    results: dict = {}
    for it in items:
        if it.get("kind") == "tool_result":
            p = it.get("payload") or {}
            results[p.get("tool_use_id") or p.get("tool_call_id") or p.get("call_id")] = p
    reads: list[tuple[str, str, object, object]] = []
    searches: list[tuple[str, int]] = []
    writes: list[tuple[str, str | None]] = []
    texts: list[str] = []
    for it in items:
        kind, p = it.get("kind"), it.get("payload") or {}
        if kind == "message" and p.get("role") in ("assistant", "agent") and p.get("text"):
            texts.append(str(p["text"]))
        elif kind == "reasoning_summary" and p.get("text"):
            texts.append("[reasoning] " + str(p["text"]))
        if kind != "tool_request":
            continue
        rid = p.get("tool_use_id") or p.get("tool_call_id") or p.get("call_id")
        res = results.get(rid) or {}
        name = p.get("tool_name")
        inp = p.get("input") if isinstance(p.get("input"), dict) else {}
        if name in _READ_TOOLS:
            path = str(inp.get("file_path") or inp.get("pattern") or inp.get("path") or "")
            reads.append((path, _classify(path), res.get("bytes"), res.get("sha256")))
        elif name in _SEARCH_TOOLS:
            query = str(inp.get("query") or inp.get("url") or (inp.get("action") or {}).get("query") or "")
            searches.append((query, str(res.get("content") or "").count("http")))
        elif name == "Write":
            content = inp.get("content")
            digest = hashlib.sha256(str(content).encode("utf-8")).hexdigest() if isinstance(content, str) else None
            writes.append((str(inp.get("file_path") or ""), digest))
        elif name == "exec":
            text = p.get("input") if isinstance(p.get("input"), str) else json.dumps(p.get("input"), ensure_ascii=False)
            for tok in _PATH_TOKEN_RE.findall(text or ""):
                if f"> {tok}" in text or f"tee {tok}" in text:
                    writes.append((tok, None))
                else:
                    reads.append((tok, _classify(tok), res.get("bytes"), res.get("sha256")))
    reads.sort(key=lambda r: (_KIND_ORDER.get(r[1], 9), r[0]))
    return {"reads": reads, "searches": searches, "writes": writes, "texts": texts}


def _published_card_sha(src: Sources, code6: str) -> str | None:
    for cand in sorted((src.run / "details").glob("*.md")) if (src.run / "details").is_dir() else []:
        txt = cand.read_text(encoding="utf-8", errors="ignore")
        if code6 in txt[:400]:
            return hashlib.sha256(txt.encode("utf-8")).hexdigest()
    return None


def _sec_l4b(src: Sources, code6: str) -> list[str]:
    out = ["", "## ⑦b 研究员现场"]
    source, index = src.agents_index()
    if source is None or not isinstance(index, dict):
        return out + ["- 缺席(无 capsule agents 索引,也无账本外部索引)"]
    bindings = src.doc("_transcript_bindings.json")
    brows = {r.get("invocation_id"): r for r in ((bindings or {}).get("rows") or [])} if isinstance(bindings, dict) else {}
    rows = [r for r in (index.get("invocations") or [])
            if str(r.get("subject_key") or r.get("subject") or "") == code6 and r.get("role") in ("l4-intel", "l4-card")]
    rows.sort(key=lambda r: (str(r.get("role")), str(r.get("invocation_id"))))
    if not rows:
        return out + [f"- 本票无 l4-card / l4-intel 调用记录(来源 {source})"]
    published = _published_card_sha(src, code6)
    for row in rows:
        b = brows.get(row.get("invocation_id")) or {}
        head = (f"- **{row.get('invocation_id')}**:{row.get('status')} · {row.get('reason') or '—'}"
                f" · 期望来源 {row.get('expectation_source') or '?'} · 来源 {source}")
        if row.get("status") == "PRESENT":
            head += f" · model {row.get('model') or '?'} · effort {row.get('effort') or '?'} · items {row.get('items')}"
        if b.get("start_ordinal") is not None:
            head += f" · ordinal {b.get('start_ordinal')}–{b.get('end_ordinal')} · 区段 {b.get('segment_quality') or '?'}"
        out.append(head)
        if row.get("status") != "PRESENT":
            continue
        doc = src.normalized_doc(source, row)
        if doc is None:
            out.append("  - normalized 文件读不到")
            continue
        ev = _agent_evidence(doc)
        for path, kind, n_bytes, sha in ev["reads"]:
            out.append(f"  - 读了:{_short_path(path)} · {n_bytes if n_bytes is not None else '?'} B"
                       f" · sha {str(sha)[:12] if sha else '?'} · [{kind}]")
        seen = {kind for _, kind, _, _ in ev["reads"]}
        missing = [k for k in _EXPECTED_READS.get(str(row.get("role")), ()) if k not in seen]
        out.append("  - 没读:" + ("、".join(missing) if missing else "—"))
        if ev["searches"]:
            out.append(f"  - 搜了:{len(ev['searches'])} 次 · " + " ｜ ".join(f"{q} · URL {n}" for q, n in ev["searches"]))
        else:
            out.append("  - 搜了:0 次")
        for path, digest in ev["writes"]:
            verdict = ("hash 未知" if digest is None or published is None
                       else ("一致" if digest == published else "不一致"))
            out.append(f"  - 写了:{_short_path(path)} · 与发布卡 {verdict}")
        if not ev["writes"]:
            out.append("  - 写了:—(产物未写出)")
        if ev["texts"]:
            out.append("  - 可见推理:")
            for i, text in enumerate(ev["texts"][:_TEXT_MAX], start=1):
                out.append(f"    {i}. {text[:_TEXT_CAP]}" + ("…" if len(text) > _TEXT_CAP else ""))
        else:
            out.append("  - 可见推理:无(harness 未落盘)")
    return out
```

`render` 的段序列改为 `(_sec_identity, _sec_passport, _sec_l1, _sec_l2, _sec_pass1, _sec_l3, _sec_l4, _sec_l4b, _sec_e6, _sec_brief, _sec_outcome)`。

`_sec_identity` 末尾(`return out` 之前)加:

```python
    bindings = src.doc("_transcript_bindings.json")
    source, _ = src.agents_index()
    if isinstance(bindings, dict) and isinstance(bindings.get("rows"), list):
        counts = bindings.get("counts") or {}
        bound = int(counts.get("bound", 0)) + int(counts.get("unverified", 0)) + int(counts.get("without_expectation", 0))
        out.append(f"- transcript 绑定:{bound}/{len(bindings['rows'])} · AMBIGUOUS {int(counts.get('ambiguous', 0))}"
                   f" · 来源 {source or '缺席'}")
    else:
        out.append("- transcript 绑定:未记录(2026-09-12 前的 run)")
```

- [ ] **Step 5: 跑用例确认通过 + 既有 chain_view 用例**

Run: `uv run --no-sync python -m pytest tests/scan/test_chain_view_agents.py tests/scan/test_chain_view.py -q`
Expected: 全绿。`test_absent_pieces_are_named_not_skipped` 若断言旧文案「2026-08-26 前未留存」,改成「未留存」。

- [ ] **Step 6: 对 09-09 真 run 冒烟(只读)**

Run: `uv run --no-sync python -m autoresearch.scan.chain_view 20260909-0909_2209 688411 | sed -n '/## ⑦ /,/## ⑧/p'`
Expected: ⑦ 段 slim 行印出 `trace/staging/_external_inputs/688411.SS_2026-09-09_slim.md · 8xxx B · 来源 staging`;⑦b 段印「缺席(无 capsule agents 索引…)」还是「本票无调用记录」取决于该 run 的 `capsule/agents/index.json`(它在,全 GONE)→ 应印 GONE 行带 reason。**这一步不写盘。**

- [ ] **Step 7: 提交**

```bash
git add autoresearch/scan/chain_view.py tests/scan/test_chain_view_agents.py tests/scan/test_chain_view.py
git commit -m "feat(scan): chain_view 7b researcher scene, fixed slim lookup, binding line

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

## 批 2(spec §8)

### Task 8: `parsers.parse_card_context`

**Files:**
- Modify: `autoresearch/scan/l4/parsers.py`
- Test: `tests/scan/test_parsers_card_context.py`(新)

**Interfaces:**
- Produces:
  - `parse_card_context(text: str) -> dict` → `{"card_kind": "earlystop"|"full", "proposal": "BUY"|"HOLD"|"SELL"|None, "ev_target": str|None, "rr": str|None, "no_new_position": bool|None, "exec_lines_present": bool}`。
  - `read_card_text(scan_dir: Path | str, ticker: str) -> str | None`(`_decision_text` 的公开别名)。
- Consumes: `contracts.agent_output.EXEC_LINE_MAX_PCT_1D` / `EXEC_LINE_MAX_POS_IN_RANGE`。

- [ ] **Step 1: 写失败用例**

```python
"""卡面上下文解析(2026-09-12 设计 §8):E6 记录用,只记不学。"""
from __future__ import annotations

from autoresearch.scan.l4.parsers import parse_card_context, read_card_text

EARLYSTOP = """# 决策卡 — 688411 海博思创 @ 2026-09-09  ·  〔早停·表面 DD〕
## 决策仪表盘
| 评级 | 现价 | 时间框架 | 触发位 | 置信度 |
|---|---|---|---|---|
| **Hold** | 172.91 | 隔夜 | 不新开仓;持有者 T+2 开盘 ≥180 减 | 中 |
**Rating**: Hold
**早停**: 停于 P3 ｜ 停因:资金流出
- [执行线] pct_chg <= 3.0 → 当日涨超 3% 放弃本次尾盘入场
- [执行线] pos_in_range < 0.7 → 收盘在当日区间上 30% 放弃入场
FINAL TRANSACTION PROPOSAL: **HOLD**
"""

FULL = """# 决策卡 — 000426 兴业银锡 @ 2026-08-21
## 决策仪表盘
| 评级 | 现价 | EV目标(T+2 开盘预期带) | 上行 | 下行 | R:R | 时间框架 | 仓位 | 触发位 | 置信度 |
|---|---|---|---|---|---|---|---|---|---|
| **Hold** | 40.88 | 40.3–41.9(中枢 41.0,**EV +0.1%**) | +2.5% | −3.0% | **0.83 : 1** | 隔夜 | 0%(不新建仓) | 收盘 < 38.30 | 中 |
**Rating**: Hold
FINAL TRANSACTION PROPOSAL: **HOLD**
"""


def test_earlystop_card_context():
    got = parse_card_context(EARLYSTOP)
    assert got == {"card_kind": "earlystop", "proposal": "HOLD", "ev_target": None, "rr": None,
                   "no_new_position": True, "exec_lines_present": True}


def test_full_card_context_reads_ev_rr_and_position():
    got = parse_card_context(FULL)
    assert got["card_kind"] == "full" and got["proposal"] == "HOLD"
    assert got["ev_target"].startswith("40.3–41.9") and got["rr"] == "0.83 : 1"
    assert got["no_new_position"] is True
    assert got["exec_lines_present"] is False        # 这张卡没写执行线


def test_exec_lines_with_drifted_threshold_are_not_present():
    text = EARLYSTOP.replace("pct_chg <= 3.0", "pct_chg <= 5.0")
    assert parse_card_context(text)["exec_lines_present"] is False


def test_sell_proposal_and_unknown_position():
    text = "# 决策卡\n| 评级 | 现价 |\n|---|---|\n| **Underweight** | 5.55 |\nFINAL TRANSACTION PROPOSAL: **SELL**\n"
    got = parse_card_context(text)
    assert got["proposal"] == "SELL" and got["no_new_position"] is None and got["card_kind"] == "full"


def test_read_card_text_finds_by_code(tmp_path):
    (tmp_path / "details").mkdir()
    (tmp_path / "details" / "688411.md").write_text(EARLYSTOP, encoding="utf-8")
    assert read_card_text(tmp_path, "688411") == EARLYSTOP
    assert read_card_text(tmp_path, "600000") is None
```

- [ ] **Step 2: 跑用例确认失败**

Run: `uv run --no-sync python -m pytest tests/scan/test_parsers_card_context.py -q`
Expected: FAIL,`ImportError: cannot import name 'parse_card_context'`

- [ ] **Step 3: 实现**

`parsers.py` 加 import `from autoresearch.contracts.agent_output import EXEC_LINE_MAX_PCT_1D, EXEC_LINE_MAX_POS_IN_RANGE`,在 `parse_early_stop` 之后加:

```python
_EXEC_LINE_RE = re.compile(
    r"^\s*-\s*\[执行线\]\s*(?:pct_chg\s*<=\s*(?P<pct>[0-9.]+)|pos_in_range\s*<\s*(?P<pos>[0-9.]+))",
    re.MULTILINE)
_NO_NEW_POSITION_RE = re.compile(r"不建仓|不新开仓|不新建仓")
_EARLYSTOP_MARK = "〔早停"

read_card_text = _decision_text   # 公开别名(2026-09-12):E6 记录层按 code 取卡文


def parse_card_context(text: str) -> dict:
    """卡面上下文(2026-09-12 设计 §8):E6 只记不读。早停卡没有 EV/R:R → None。
    `exec_lines_present` = 两行 `[执行线]` 在场**且**阈值等于 `contracts.agent_output` 常量。"""
    dash = _parse_dashboard(text)
    prop = _PROPOSAL_RE.search(text)
    pct = pos = None
    for m in _EXEC_LINE_RE.finditer(text or ""):
        if m.group("pct"):
            pct = float(m.group("pct"))
        if m.group("pos"):
            pos = float(m.group("pos"))
    exec_present = (pct is not None and pos is not None
                    and pct == float(EXEC_LINE_MAX_PCT_1D) and pos == float(EXEC_LINE_MAX_POS_IN_RANGE))
    position_cells = " ".join(v for k, v in dash.items() if any(n in k for n in ("仓位", "触发位")))
    early = _EARLYSTOP_MARK in (text or "")[:400] or parse_early_stop(text) is not None
    return {
        "card_kind": "earlystop" if early else "full",
        "proposal": prop.group(1).upper() if prop else None,
        "ev_target": _get(dash, "EV目标", "目标") or None,
        "rr": _get(dash, "R:R") or None,
        "no_new_position": (bool(_NO_NEW_POSITION_RE.search(position_cells)) if position_cells else None),
        "exec_lines_present": exec_present,
    }
```

- [ ] **Step 4: 跑用例确认通过**

Run: `uv run --no-sync python -m pytest tests/scan/test_parsers_card_context.py tests/scan -q -k "parsers or card_context"`
Expected: 全绿

- [ ] **Step 5: 提交**

```bash
git add autoresearch/scan/l4/parsers.py tests/scan/test_parsers_card_context.py
git commit -m "feat(scan): parse_card_context for E6 record layer

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 9: E6 决策记录 schema 2(`card_context` + `why` + `consulted`)与 ⑧ 渲染

**Files:**
- Modify: `autoresearch/scan/relative_buy.py:133`(`SCHEMA_VERSION`)、`build_decision`(候选行 / 返回文档)
- Modify: `autoresearch/scan/chain_view.py:365-391`(`_sec_e6`)
- Test: `tests/scan/test_relative_buy.py`、`tests/scan/test_chain_view_agents.py`

**Interfaces:**
- Produces:
  - `SCHEMA_VERSION = 2`;`RULE_VERSION` 不变。
  - 候选行新键 `card_context: dict | None` = `parse_card_context(卡文) + {"consulted": {"card_ev": False, "card_exec_lines": False, "card_position": False}}`;卡缺席 → `None`。
  - 顶层新键 `why: str`(固定模板)与 `inputs["details_cards"]: "<found>/<candidates>"`。
  - `why_line(pool, seat_codes, candidates, excluded, eligible, buys) -> str`(纯函数,可测)。
- Consumes: `parsers.read_card_text` / `parsers.parse_card_context`(Task 8)。

- [ ] **Step 1: 写失败用例**

在 `tests/scan/test_relative_buy.py` 的 `_build_scan` 签名加 `cards: dict[str, str] | None = None`,函数末尾 `return scan` 之前加:

```python
    if cards:
        (scan / "details").mkdir(exist_ok=True)
        for code, text in cards.items():
            (scan / "details" / f"{code}.md").write_text(text, encoding="utf-8")
```

追加用例:

```python
_HOLD_CARD = ("# 决策卡 — {code}  ·  〔早停·表面 DD〕\n| 评级 | 现价 | 时间框架 | 触发位 | 置信度 |\n|---|---|---|---|---|\n"
              "| **Hold** | 10 | 隔夜 | 不新开仓 | 中 |\n**Rating**: Hold\n**早停**: 停于 P3 ｜ 停因:资金流出\n"
              "- [执行线] pct_chg <= 3.0 → x\n- [执行线] pos_in_range < 0.7 → y\nFINAL TRANSACTION PROPOSAL: **HOLD**\n")


def test_card_context_is_recorded_not_consulted(tmp_path):
    scan = _build_scan(tmp_path, [Cand("000001", rating="Hold", seat=True)],
                       cards={"000001": _HOLD_CARD.format(code="000001")})
    doc = build_decision(scan, DATE, mode=MODE_ACTIVE, pool="composite")
    cc = _by_code(doc)["000001"]["card_context"]
    assert cc["card_kind"] == "earlystop" and cc["proposal"] == "HOLD"
    assert cc["no_new_position"] is True and cc["exec_lines_present"] is True
    assert cc["consulted"] == {"card_ev": False, "card_exec_lines": False, "card_position": False}
    assert doc["schema_version"] == 2 and doc["rule_version"] == RULE_VERSION
    assert doc["inputs"]["details_cards"] == "1/1"


def test_missing_card_gives_null_context_and_is_counted(tmp_path):
    scan = _build_scan(tmp_path, [Cand("000001", rating="Hold")])
    doc = build_decision(scan, DATE)
    assert _by_code(doc)["000001"]["card_context"] is None
    assert doc["inputs"]["details_cards"] == "0/1"


def test_why_line_names_pool_vetoes_pinned_and_buy(tmp_path):
    cands = [Cand("000001", rating="Hold", seat=True, composite_rank=3),
             Cand("000002", rating="Underweight", seat=True, composite_rank=1),
             Cand("000003", rating="Sell", seat=True, composite_rank=2),
             Cand("000004", rating="Hold", pinned=True, composite_rank=4),
             Cand("000005", rating="Hold", composite_rank=5)]
    scan = _build_scan(tmp_path, cands, cards={"000001": _HOLD_CARD.format(code="000001")})
    doc = build_decision(scan, DATE, mode=MODE_ACTIVE, exclude_pinned=True, pool="composite")
    assert doc["buys"][0]["code"] == "000001"
    rank = _by_code(doc)["000001"]["rank"]
    n_elig = sum(1 for c in doc["candidates"] if c["eligible"])
    assert doc["why"] == (f"池=composite(席 3):2 只被硬门否决(Sell×1、UW×1),持仓排除 1,不在池 1;"
                          f"合格 {n_elig},BUY=#{rank}/{n_elig} 000001;卡面 Hold·HOLD·不新开仓;E6 未读 EV/执行线。")


def test_why_line_when_blocked(tmp_path):
    scan = _build_scan(tmp_path, [Cand("000001", rating="Sell", seat=True)])
    doc = build_decision(scan, DATE, mode=MODE_ACTIVE, pool="composite")
    assert doc["blocked"] is True
    assert doc["why"].startswith("池=composite(席 1):1 只被硬门否决(Sell×1)")
    assert "BUY=无(blocked)" in doc["why"]


def test_schema2_keeps_writer_parity(tmp_path):
    scan = _build_scan(tmp_path, [Cand("000001", rating="Hold", seat=True)],
                       cards={"000001": _HOLD_CARD.format(code="000001")})
    write_decision(scan, DATE, mode=MODE_ACTIVE, pool="composite")
    assert verify_decision(scan, DATE, mode=MODE_ACTIVE, pool="composite")["action"] == "noop"
```

`Cand` 若无 `seat` 字段,先确认既有 fixture(`finalists.csv` 写 `composite_seat` 用的正是 `c.seat`)——它已存在(`_build_scan` 引用 `c.seat`)。

在 `tests/scan/test_chain_view_agents.py` 追加:

```python
def test_e6_section_prints_card_context_and_why(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    run = _run(tmp_path)
    st = run / "trace" / "staging"
    (st / "_relative_buy_decision.json").write_text(json.dumps({
        "schema_version": 2, "mode": "active", "rule_version": "e6.v3.0", "blocked": False, "pool": "composite",
        "why": "池=composite(席 1):0 只被硬门否决(),持仓排除 0;合格 1,BUY=#1/1 603317;卡面 Hold·HOLD·不新开仓;E6 未读 EV/执行线。",
        "buys": [{"code": "603317", "rank": 1, "basis": "relative"}],
        "candidates": [{"code": "603317", "eligible": True, "rank": 1, "faces": {}, "hard_gate": {},
                        "relative_decision_score": 0.5, "research_rating": "Hold",
                        "card_context": {"card_kind": "earlystop", "proposal": "HOLD", "ev_target": None,
                                         "rr": None, "no_new_position": True, "exec_lines_present": True,
                                         "consulted": {"card_ev": False, "card_exec_lines": False,
                                                       "card_position": False}}}],
        "excluded": []}, ensure_ascii=False), encoding="utf-8")
    text = chain_view.render(run, "603317")
    assert '"no_new_position": true' in text and "- why:池=composite(席 1)" in text


def test_e6_section_schema1_is_labelled(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    run = _run(tmp_path)
    st = run / "trace" / "staging"
    (st / "_relative_buy_decision.json").write_text(json.dumps({
        "mode": "active", "rule_version": "e6.v2.0", "blocked": False, "buys": [],
        "candidates": [{"code": "603317", "eligible": True, "rank": 1}], "excluded": []}), encoding="utf-8")
    assert "schema 1,无卡面上下文" in chain_view.render(run, "603317")
```

- [ ] **Step 2: 跑用例确认失败**

Run: `uv run --no-sync python -m pytest tests/scan/test_relative_buy.py tests/scan/test_chain_view_agents.py -q -k "card_context or why_line or parity or e6_section"`
Expected: FAIL(`KeyError: 'card_context'` / `'why'` / schema 1)

- [ ] **Step 3: 实现 relative_buy**

`SCHEMA_VERSION = 2`(第 133 行)。在 `build_decision` 上方加:

```python
CONSULTED_NONE = {"card_ev": False, "card_exec_lines": False, "card_position": False}
_VETO_LABELS = (("research_rating=Underweight", "UW"), ("research_rating=Sell", "Sell"),
                ("卡面提案", "SELL提案"), ("早停红灯", "早停红灯"), ("成交额分位", "流动性"), ("ST/退市", "ST"))


def _card_context(scan: Path, code: str) -> dict | None:
    """卡面上下文(spec §8):只记不学。卡缺席 → None(留痕在 inputs.details_cards)。"""
    from autoresearch.scan.l4.parsers import parse_card_context, read_card_text

    text = read_card_text(scan, code)
    if text is None:
        return None
    return {**parse_card_context(text), "consulted": dict(CONSULTED_NONE)}


def _veto_label(row: dict) -> str:
    detail = str(row.get("detail") or "")
    for needle, label in _VETO_LABELS:
        if needle in detail:
            return label
    reason = str(row.get("reason") or "")
    return reason.split(".", 1)[1] if reason.startswith("hard_gate.") else reason


def why_line(pool: str, seat_codes: set[str], candidates: list[dict], excluded: list[dict],
             eligible: list[dict], buys: list[dict]) -> str:
    """固定模板(spec §8):池、席内硬门否决计数、持仓排除、合格数、BUY 名次、卡面三要素、E6 未读声明。"""
    in_pool = {row["code"] for row in candidates if row.get("in_pool")} if pool == POOL_COMPOSITE \
        else {row["code"] for row in candidates}
    vetoes: dict[str, int] = {}
    pinned_n = not_in_pool = 0
    for row in excluded:
        if row["reason"] == "pinned_holding":
            pinned_n += 1
        elif row["reason"] == "not_in_pool":
            not_in_pool += 1
        elif row["code"] in in_pool:
            label = _veto_label(row)
            vetoes[label] = vetoes.get(label, 0) + 1
    veto_txt = "、".join(f"{k}×{v}" for k, v in sorted(vetoes.items()))
    head = (f"池={pool}(席 {len(seat_codes) if pool == POOL_COMPOSITE else len(candidates)}):"
            f"{sum(vetoes.values())} 只被硬门否决({veto_txt}),持仓排除 {pinned_n}"
            + (f",不在池 {not_in_pool}" if not_in_pool else "") + f";合格 {len(eligible)}")
    if not buys:
        return head + ",BUY=无(blocked);E6 未读 EV/执行线。"
    code = buys[0]["code"]
    row = next(r for r in candidates if r["code"] == code)
    cc = row.get("card_context") or {}
    stance = ("不新开仓" if cc.get("no_new_position") else
              "可建仓" if cc.get("no_new_position") is False else "仓位未知")
    return (head + f",BUY=#{row.get('rank')}/{len(eligible)} {code};"
            f"卡面 {row.get('research_rating')}·{cc.get('proposal') or '?'}·{stance};E6 未读 EV/执行线。")
```

`build_decision` 里候选行字典加一键(放在 `"in_pool"` 之后):

```python
            "card_context": _card_context(scan, code),
```

返回文档里加两键(放在 `"pool_members"` 之后)并给 `inputs` 加计数:`"why": why_line(pool, seat_codes, candidates, excluded, eligible, buys)`;找到返回文档里的 `"inputs": {...}` 字典,加 `"details_cards": f"{sum(1 for r in candidates if r.get('card_context') is not None)}/{len(candidates)}"`。(`inputs` 键在文件尾部的返回字典里;若它在别处组装,加在组装处。)

- [ ] **Step 4: 实现 ⑧ 渲染**

`chain_view._sec_e6` 在 `if code6 in [...]: ✅` 之前加:

```python
    if isinstance(row.get("card_context"), dict):
        out += _kv([("卡面上下文", json.dumps(row["card_context"], ensure_ascii=False, sort_keys=True))])
    else:
        out.append("- 卡面上下文:schema 1,无卡面上下文" if not row.get("card_context") else "- 卡面上下文:—")
    if doc.get("why"):
        out.append(f"- why:{doc['why']}")
```

- [ ] **Step 5: 跑用例确认通过 + 全量**

Run: `uv run --no-sync python -m pytest tests/scan/test_relative_buy.py tests/scan/test_chain_view_agents.py tests/scan/test_post_run.py tests/scan/test_outcome.py tests/scan/test_report_sections.py -q`
Expected: 全绿(`test_repeated_build_is_byte_stable` 必须仍绿;若 `_serialize_decision` 对 dict 排序,新键无影响)。

Run: `AUTORESEARCH_ENGINE=claude uv run --no-sync python -m pytest -q -x && AUTORESEARCH_ENGINE=codex uv run --no-sync python -m pytest -q -x`
Expected: 全绿

- [ ] **Step 6: 对 09-09 真 run 离线重算核对(只读,不写盘)**

```bash
uv run --no-sync python - <<'EOF'
from pathlib import Path
from autoresearch.scan.relative_buy import build_decision
doc = build_decision(Path("reports_claude/scan/20260909-0909_2209/trace/staging"), "2026-09-09",
                     mode="active", exclude_pinned=True, pool="composite")
row = next(c for c in doc["candidates"] if c["code"] == "688411")
print(doc["why"]); print(row["card_context"])
EOF
```
Expected:`why` 为「池=composite(席 3):2 只被硬门否决(Sell×1、UW×1),持仓排除 2,不在池 2;合格 5,BUY=#3/5 688411;卡面 Hold·HOLD·不新开仓;E6 未读 EV/执行线。」;`card_context` 的 `no_new_position=True`、`exec_lines_present=True`、`card_kind="earlystop"`。若 `build_decision` 因该 staging 缺 `L1_scored_full.csv` 等而抛错,改用 `reports_claude/scan/20260909-0909_2209/trace/staging` 之外的 `trace/` 同名文件路径不可行时,跳过本步并在提交信息注明「真 run 核对待下次真跑」。

- [ ] **Step 7: 变异探针**

删掉 `"card_context": _card_context(scan, code)` 那行 → `test_card_context_is_recorded_not_consulted` 与 `test_schema2_keeps_writer_parity`(若 writer-1 已写过含 card_context 的文件)必红;改回。

- [ ] **Step 8: 提交**

```bash
git add autoresearch/scan/relative_buy.py autoresearch/scan/chain_view.py tests/scan/test_relative_buy.py tests/scan/test_chain_view_agents.py
git commit -m "feat(scan): E6 decision schema 2 records card_context and why (record-only)

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

## 批 3(spec §6.3、§9.1)

### Task 10: 已冻结 run 的离线外部索引(`--offline`)

**Files:**
- Modify: `autoresearch/scan/transcript_binder.py`(`_under_root` 改子串匹配;新 `offline_index`、`offline_roots`、CLI `--offline`)
- Modify: `autoresearch/contracts/artifacts.py`(账本段)
- Test: `tests/scan/test_transcript_binder.py`

**Interfaces:**
- Produces:
  - `offline_roots(run_dir: Path) -> list[str]` → `["/scan_runs/<capsule_run_id>/staging/<date>/", "/scan/<date>/"]`(子串根;`capsule_run_id` 依次取 `capsule/capsule.json.run_id` → `trace/run_contract.json.run_id` → `manifest.json.run_id`;取不到只返回第二项)。
  - `_under_root(path, roots)` 改为**子串**语义:`idx = path.find(root)`;`idx >= 0` → 返回 `path[idx + len(root):]`(active run 传绝对根时 `idx == 0`,行为不变)。
  - `offline_index(run_dir: Path | str, *, engine: str | None = None, sessions_root: Path | None = None, ledger_root: Path | None = None, now: datetime | None = None) -> dict`:写 `<ledger>/agents_index/<run_dir.name>.json` 与 `<ledger>/agents_index/<run_dir.name>/normalized/<invocation>.json`;**不写 run 目录**。
  - 索引行 = capsule 行同键 + `source: "trace_transcripts" | "codex_sessions"`、`start_ordinal`、`end_ordinal`、`segment_quality`;顶层 `computed_at`、`coverage`。
- Consumes: `capsule.agent_expectations`(接受任何带 `.capsule` / `.staging` 属性的对象)、`ClaudeTranscriptAdapter.stats_from_rows`、`CodexTranscriptAdapter.normalize_rows`(Task 2)。

- [ ] **Step 1: 写失败用例**

追加到 `tests/scan/test_transcript_binder.py`:

```python
import gzip


def _frozen_run(tmp_path, monkeypatch, *, engine="claude", run_name="20260825-0825_2149", date="2026-08-25",
                capsule_run_id="20260825T123650212028Z"):
    from autoresearch.common import workspace as ws_mod

    monkeypatch.setattr(ws_mod, "ENGINE", engine)
    monkeypatch.setattr(ws_mod, "context_root", lambda: tmp_path / f"context_{engine}")
    monkeypatch.setattr(ws_mod, "reports_root", lambda: tmp_path / f"reports_{engine}")
    run = tmp_path / f"reports_{engine}" / "scan" / run_name
    (run / "trace" / "staging" / "details").mkdir(parents=True)
    (run / "trace" / "transcripts").mkdir()
    (run / "manifest.json").write_text(json.dumps({"analysis_date": date, "run_id": capsule_run_id,
                                                   "generated_at": f"{date}T21:49:00"}), encoding="utf-8")
    (run / "trace" / "run_contract.json").write_text(json.dumps({"run_id": capsule_run_id, "analysis_date": date}),
                                                     encoding="utf-8")
    (run / "trace" / "staging" / "details" / "603317.md").write_text("# card\n", encoding="utf-8")
    (run / "trace" / "staging" / "_l4_intel_603317.md").write_text("# intel\n", encoding="utf-8")
    return run


def test_under_root_matches_substring_roots():
    root = "/scan_runs/20260825T123650212028Z/staging/2026-08-25/"
    assert tb._under_root("/Users/x/context_claude" + root + "details/603317.md", [root]) == "details/603317.md"
    assert tb._under_root("/Users/x/context_claude/scan_runs/OTHER/staging/2026-08-25/details/603317.md", [root]) is None


def test_offline_index_claude_from_archived_transcripts(tmp_path, monkeypatch):
    run = _frozen_run(tmp_path, monkeypatch)
    root = "/Users/x/context_claude/scan_runs/20260825T123650212028Z/staging/2026-08-25/"
    raw = _claude_jsonl(tmp_path / "raw" / "agent-a1.jsonl",
                        reads=[root + "_l4_prompt_603317.md"], writes=[root + "details/603317.md"])
    (run / "trace" / "transcripts" / "l4-card-agent-a1.jsonl.gz").write_bytes(
        gzip.compress(raw.read_bytes(), mtime=0))
    (run / "trace" / "transcripts" / "_index.json").write_text(json.dumps({
        "schema_version": 1, "agents": ["l4-card"],
        "transcripts": [{"agent": "l4-card", "file": "agent-a1.jsonl", "status": "PRESENT"}]}), encoding="utf-8")

    before = sorted(str(p) for p in run.rglob("*"))

    doc = tb.offline_index(run)

    assert sorted(str(p) for p in run.rglob("*")) == before        # run 目录一个字节不写
    ledger = tmp_path / "reports_claude" / "scan" / "_ledger" / "agents_index"
    assert (ledger / f"{run.name}.json").is_file()
    rows = {r["invocation_id"]: r for r in doc["invocations"]}
    card = rows["l4-card-603317-1"]
    assert card["status"] == "PRESENT" and card["source"] == "trace_transcripts"
    assert card["expectation_source"] == "products"
    assert (ledger / card["normalized"]).is_file()
    normalized = json.loads((ledger / card["normalized"]).read_text(encoding="utf-8"))
    assert [i["kind"] for i in normalized["items"]][:2] == ["tool_request", "tool_request"]
    assert rows["l4-intel-603317-1"]["status"] == "GONE"
    assert doc["coverage"] == {"expected": 2, "present": 1, "missing": 1}


def test_offline_index_codex_from_sessions(tmp_path, monkeypatch):
    from autoresearch.trace.events import append_event

    run = _frozen_run(tmp_path, monkeypatch, engine="codex", run_name="20260908-0908_2246", date="2026-09-08",
                      capsule_run_id="20260908T134424076077Z")
    events = run / "capsule" / "events" / "events.jsonl"
    events.parent.mkdir(parents=True)
    append_event(events, run_id="20260908T134424076077Z", engine="codex", stage="run",
                 invocation_id="run-20260908T134424076077Z", attempt=1, subject=None,
                 event_type="RUN_STARTED", payload={}, ts="2026-09-08T13:44:24.354518Z")
    append_event(events, run_id="20260908T134424076077Z", engine="codex", stage="l4",
                 invocation_id="l4-task-603317-attempt-1", attempt=1, subject="603317",
                 event_type="TASK_CLAIMED", payload={"attempt": 1}, ts="2026-09-08T14:16:23.743361Z")
    root = "/Users/x/context_codex/scan_runs/20260908T134424076077Z/staging/2026-09-08/"
    sessions = tmp_path / "sessions"
    _rollout(sessions / "2026/09/08/rollout-2026-09-08T21-41-33-aaa.jsonl", [
        _exec(2, "cat " + root + "_l4_prompt_603317.md", ts="2026-09-08T14:20:00.000Z"),
        _exec(3, "cat > " + root + "details/603317.md <<'EOF'\\n# card\\nEOF", ts="2026-09-08T14:30:00.000Z"),
    ], cwd=str(tmp_path), ts0="2026-09-08T13:44:30.000Z")
    monkeypatch.setattr(tb.ws, "context_root", lambda: tmp_path / "context_codex")

    doc = tb.offline_index(run, sessions_root=sessions,
                           now=__import__("datetime").datetime(2026, 9, 8, 15, 0, tzinfo=__import__("datetime").timezone.utc))

    row = {r["invocation_id"]: r for r in doc["invocations"]}["l4-card-603317-1"]
    assert row["status"] == "PRESENT" and row["source"] == "codex_sessions"
    assert (row["start_ordinal"], row["end_ordinal"]) == (2, 3)
    assert row["expectation_source"] == "task_events"
```

`append_event` 若不接受 `ts=`,改为写入后用 `json` 直接改行里的 `ts`;判据只需要 RUN_STARTED 的时间戳落在 rollout 窗口内。

- [ ] **Step 2: 跑用例确认失败**

Run: `uv run --no-sync python -m pytest tests/scan/test_transcript_binder.py -q -k "under_root or offline"`
Expected: FAIL(`AttributeError: offline_index` / `_under_root` 子串用例红)

- [ ] **Step 3: 实现**

`_under_root` 改为:

```python
def _under_root(path: str, roots: Sequence[str]) -> str | None:
    """path 在某个 root 之下 → 返回 root 之后的相对部分。root 可以是绝对根(active run)或
    `/scan_runs/<id>/staging/<date>/` 这样的子串根(冻结 run 的离线索引)。"""
    for root in roots:
        idx = path.find(root)
        if idx >= 0:
            return path[idx + len(root):]
    return None
```

追加:

```python
# ───────────────────────── 离线索引(已冻结 run;不写 run 目录)─────────────────────────

def _json_or_empty(path: Path) -> dict:
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
        return doc if isinstance(doc, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def offline_roots(run_dir: Path) -> list[str]:
    run = Path(run_dir)
    manifest = _json_or_empty(run / "manifest.json")
    date = str(manifest.get("analysis_date") or "")
    capsule_id = (_json_or_empty(run / "capsule" / "capsule.json").get("run_id")
                  or _json_or_empty(run / "trace" / "run_contract.json").get("run_id")
                  or manifest.get("run_id"))
    roots = []
    if capsule_id and date:
        roots.append(f"/scan_runs/{capsule_id}/staging/{date}/")
    if date:
        roots.append(f"/scan/{date}/")
    return roots


def _archived_claude_candidates(run: Path) -> list[Candidate]:
    tdir = run / "trace" / "transcripts"
    out: list[Candidate] = []
    for path in sorted(tdir.glob("*.jsonl.gz")) if tdir.is_dir() else []:
        agent = path.name.split("-agent-", 1)[0]
        writes, reads, first_ts = claude_paths(_iter_jsonl(path))
        out.append(Candidate(path, agent, first_ts, writes, reads))
    return out


def _run_window(run: Path) -> tuple[datetime | None, datetime | None]:
    events = run / "capsule" / "events" / "events.jsonl"
    started = ended = None
    if events.is_file():
        for row in _iter_jsonl(events):
            ts = _parse_ts(row.get("ts"))
            if ts is None:
                continue
            if row.get("event_type") == "RUN_STARTED":
                started = ts
            ended = ts if ended is None or ts > ended else ended
    return started, ended


def _normalize_for_ledger(engine: str, row: dict, out_dir: Path) -> dict:
    """绑定行 → normalized JSON(与 capsule/agents/normalized 同形)。返回补充到索引行的字段。"""
    from autoresearch.trace.transcripts.base import TranscriptRef

    source = Path(str(row["path"]))
    rows = list(_iter_jsonl(source))
    ref = TranscriptRef(engine=engine, path=source, status="PRESENT", role=str(row["role"]),
                        subject=row.get("subject_key"), invocation_id=str(row["invocation_id"]),
                        start_ordinal=row.get("start_ordinal"), end_ordinal=row.get("end_ordinal"))
    if engine == "claude":
        from autoresearch.trace.transcripts.claude import ClaudeTranscriptAdapter
        normalized = ClaudeTranscriptAdapter().stats_from_rows(rows, ref).normalized
    else:
        from autoresearch.trace.transcripts.codex import CodexTranscriptAdapter
        normalized = CodexTranscriptAdapter().normalize_rows(rows, ref)
    out_dir.mkdir(parents=True, exist_ok=True)
    target = out_dir / f"{row['invocation_id']}.json"
    target.write_text(json.dumps({
        "schema_version": 2, "engine": engine, "invocation_id": row["invocation_id"],
        "role": normalized.ref.role, "subject": normalized.ref.subject, "status": normalized.status,
        "model": normalized.model, "effort": normalized.effort,
        "items": [{"index": it.index, "kind": it.kind, "payload": dict(it.payload), "timestamp": it.timestamp}
                  for it in normalized.items],
    }, ensure_ascii=False, sort_keys=True, indent=1) + "\n", encoding="utf-8")
    import hashlib
    raw = source.read_bytes()
    return {"normalized": f"{out_dir.parent.name}/normalized/{target.name}",
            "source_sha256": hashlib.sha256(raw).hexdigest(), "source_bytes": len(raw),
            "items": len(normalized.items), "model": normalized.model, "effort": normalized.effort}


def offline_index(run_dir: Path | str, *, engine: str | None = None, sessions_root: Path | None = None,
                  ledger_root: Path | None = None, now: datetime | None = None) -> dict:
    """已冻结 run → `_ledger/agents_index/<run>.json`(+ normalized/)。期望来自 capsule 事件链
    (无事件 → 产物在场),候选来自 trace/transcripts(Claude)或 ~/.codex/sessions(Codex)。"""
    from types import SimpleNamespace
    from autoresearch.trace.capsule import agent_expectations

    run = Path(run_dir)
    resolved_engine = str(engine or ("codex" if "reports_codex" in run.resolve().as_posix() else ws.ENGINE))
    ledger = Path(ledger_root) if ledger_root else ws.reports_root() / "scan" / "_ledger"
    out_dir = ledger / "agents_index" / run.name
    handle = SimpleNamespace(capsule=run / "capsule", staging=run / "trace" / "staging",
                             run_id=run.name, engine=resolved_engine)
    expectations = agent_expectations(handle)
    roots = offline_roots(run)
    source_label = "trace_transcripts"
    counts: dict = {}
    if resolved_engine == "claude":
        candidates = _archived_claude_candidates(run)
    else:
        source_label = "codex_sessions"
        started, ended = _run_window(run)
        candidates, counts = [], {"reason": "NO_RUN_STARTED_EVENT"}
        if started is not None:
            fake = SimpleNamespace(capsule=run / "capsule")
            candidates, counts = codex_candidates(fake, expectations, roots, sessions_root=sessions_root,
                                                  now=now or (ended + RUN_WINDOW_SLACK if ended else None))
    rows, unmatched = assign(candidates, expectations, roots)
    invocations = []
    for row in rows:
        item = {**row, "source": source_label,
                "status": "PRESENT" if row["status"] in ("BOUND", "UNVERIFIED_BY_PRODUCT", "BOUND_WITHOUT_EXPECTATION")
                else row["status"],
                "normalized": None, "source_sha256": None, "source_bytes": None, "items": None,
                "model": None, "effort": None}
        if item["status"] == "PRESENT":
            item.update(_normalize_for_ledger(resolved_engine, row, out_dir / "normalized"))
        invocations.append(item)
    present = sum(1 for r in invocations if r["status"] == "PRESENT")
    doc = {"schema_version": 2, "run_id": run.name, "engine": resolved_engine, "source": source_label,
           "computed_at": (now or datetime.now(timezone.utc)).isoformat(),
           "roots": roots, "candidates": counts, "invocations": invocations, "unmatched": unmatched,
           "coverage": {"expected": len(invocations), "present": present, "missing": len(invocations) - present}}
    (ledger / "agents_index").mkdir(parents=True, exist_ok=True)
    (ledger / "agents_index" / f"{run.name}.json").write_text(
        json.dumps(doc, ensure_ascii=False, sort_keys=True, indent=1) + "\n", encoding="utf-8")
    return doc
```

`codex_candidates` 的 `_run_started(handle)` 只读 `handle.capsule`,所以 `SimpleNamespace(capsule=...)` 够用。`main` 加:

```python
    parser.add_argument("--offline", default=None, help="已冻结 run 目录或 run 名 → _ledger/agents_index/")
    parser.add_argument("--sessions-root", default=None)
    ...
    if args.offline:
        target = Path(args.offline)
        run = target if target.exists() else ws.reports_root() / "scan" / args.offline
        doc = offline_index(run, sessions_root=Path(args.sessions_root) if args.sessions_root else None)
        print(json.dumps({"ok": True, "coverage": doc["coverage"], "run": run.name}, ensure_ascii=False, sort_keys=True))
        return 0
```

- [ ] **Step 4: 登记 ARTIFACTS**

账本段加:

```python
    Artifact("agents_index_ledger", "agents_index/*.json", "ledger", "observe", "transcript_binder", "json",
             "conditional", required_when="对已冻结 run 跑过 transcript_binder --offline"),
    Artifact("agents_index_normalized", "agents_index/*/normalized", "ledger", "observe", "transcript_binder", "dir",
             "conditional", required_when="同上"),
```

Run: `uv run --no-sync python -m autoresearch.contracts.emit --write && uv run --no-sync python -m pytest tests/contracts -q`

- [ ] **Step 5: 跑用例确认通过**

Run: `uv run --no-sync python -m pytest tests/scan/test_transcript_binder.py tests/scan/test_chain_view_agents.py -q`
Expected: 全绿

- [ ] **Step 6: 对 09-09 真 run 跑离线索引(写账本目录,不写 run 目录)**

Run: `uv run --no-sync python -m autoresearch.scan.transcript_binder --offline 20260909-0909_2209 && uv run --no-sync python -m autoresearch.scan.chain_view 20260909-0909_2209 688411 | sed -n '/## ⑦b/,/## ⑧/p'`
Expected(spec §11.1③):索引 36 行、PRESENT ≥ 34;⑦b 对 688411 印出 `_l4_prompt_688411.md`、`688411.SS_2026-09-09_slim.md`(8332 B)、`_l4_intel_688411.md` 三条「读了」、「没读:deep」、「写了:688411.md · 与发布卡 一致」。若 PRESENT < 34,把 `unmatched` 列表贴进提交信息,**不改判据凑数**。

- [ ] **Step 7: 提交**

```bash
git add autoresearch/scan/transcript_binder.py autoresearch/contracts/artifacts.py .claude/workflows tests/scan/test_transcript_binder.py
git commit -m "feat(scan): offline agents index for frozen runs (ledger, read-only run dirs)

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 11: 老 run 抢救 `salvage` 与 `chain_view` 第四级来源

**Files:**
- Create: `autoresearch/scan/salvage.py`
- Modify: `autoresearch/scan/chain_view.py`(`Sources.__init__` / `find` / `render` 页脚)
- Modify: `autoresearch/contracts/artifacts.py`
- Test: `tests/scan/test_salvage.py`(新)、`tests/scan/test_chain_view_agents.py`

**Interfaces:**
- Produces:
  - `salvage_run(run_dir, *, shared_root: Path | None = None, ledger_root: Path | None = None, sessions_root: Path | None = None, now: datetime | None = None) -> dict`:写 `<ledger>/salvage/<run>/{_relative_buy_decision.json, market_view.md, transcripts/rollout-*.jsonl.gz, provenance.json}`。
  - `provenance.json` 形状:`{"schema_version": 1, "run_id", "analysis_date", "generated_at", "files": {"<name>": {"source", "exists", "mtime", "sha256", "verdict"}}, "codex_rollouts": [...], "computed_at"}`,`verdict ∈ {MATCHES_RUN_WINDOW, OVERWRITTEN_BY_LATER_RUN, UNKNOWN, ABSENT}`。
  - `verdict_for(mtime: datetime, generated_at: datetime, later_runs: Sequence[datetime]) -> str`(纯函数)。
  - `Sources.salvage: Path`;`Sources.find` 第四级;`Sources.used_salvage: bool`;页脚一行「本视图有片段读自账本抢救目录」。
  - CLI `python -m autoresearch.scan.salvage <run_id>|--all`。

- [ ] **Step 1: 写失败用例**

新建 `tests/scan/test_salvage.py`:

```python
"""老 run 抢救(2026-09-12 设计 §9.1):共享 staging 里的 E6 决策/市场研判拷进账本外目录并带来源判定;
不写 run 目录一个字节;同日后跑覆盖 → OVERWRITTEN_BY_LATER_RUN。"""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path

from autoresearch.common import workspace as ws
from autoresearch.scan import salvage


def _run(tmp_path, name, generated_at, date="2026-08-17"):
    run = tmp_path / ws.reports_root() / "scan" / name
    (run / "trace").mkdir(parents=True)
    (run / "manifest.json").write_text(json.dumps({"analysis_date": date, "generated_at": generated_at}),
                                       encoding="utf-8")
    return run


def _shared(tmp_path, date, mtime_iso):
    d = tmp_path / ws.context_root() / "scan" / date
    d.mkdir(parents=True, exist_ok=True)
    p = d / "_relative_buy_decision.json"
    p.write_text('{"buys": [{"code": "000779"}]}', encoding="utf-8")
    (d / "market_view.md").write_text("# 市场研判\n", encoding="utf-8")
    ts = datetime.fromisoformat(mtime_iso).timestamp()
    for f in (p, d / "market_view.md"):
        os.utime(f, (ts, ts))
    return d


def test_verdict_pure_function():
    g = datetime(2026, 8, 17, 21, 50)
    assert salvage.verdict_for(datetime(2026, 8, 17, 21, 51), g, []) == "MATCHES_RUN_WINDOW"
    assert salvage.verdict_for(datetime(2026, 8, 17, 22, 15), g, [datetime(2026, 8, 17, 22, 15)]) == "OVERWRITTEN_BY_LATER_RUN"
    assert salvage.verdict_for(datetime(2026, 8, 19, 9, 0), g, []) == "UNKNOWN"


def test_salvage_copies_with_provenance_and_marks_overwritten(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    early = _run(tmp_path, "20260817_2150", "2026-08-17T21:50:00")
    late = _run(tmp_path, "20260817_2215", "2026-08-17T22:15:00")
    _shared(tmp_path, "2026-08-17", "2026-08-17T22:15:30")
    before = sorted(str(p) for p in early.rglob("*"))

    res_early = salvage.salvage_run(early)
    res_late = salvage.salvage_run(late)

    ledger = tmp_path / ws.reports_root() / "scan" / "_ledger" / "salvage"
    prov_early = json.loads((ledger / "20260817_2150" / "provenance.json").read_text(encoding="utf-8"))
    prov_late = json.loads((ledger / "20260817_2215" / "provenance.json").read_text(encoding="utf-8"))
    assert prov_early["files"]["_relative_buy_decision.json"]["verdict"] == "OVERWRITTEN_BY_LATER_RUN"
    assert prov_late["files"]["_relative_buy_decision.json"]["verdict"] == "MATCHES_RUN_WINDOW"
    assert (ledger / "20260817_2215" / "_relative_buy_decision.json").read_text(encoding="utf-8") == '{"buys": [{"code": "000779"}]}'
    assert (ledger / "20260817_2215" / "market_view.md").is_file()
    assert sorted(str(p) for p in early.rglob("*")) == before          # run 目录一字不动
    assert res_early["files"]["market_view.md"]["verdict"] == "OVERWRITTEN_BY_LATER_RUN"


def test_salvage_skips_runs_that_own_their_decision(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    run = _run(tmp_path, "20260826_2120", "2026-08-26T21:20:00", date="2026-08-26")
    (run / "trace" / "staging").mkdir()
    (run / "trace" / "staging" / "_relative_buy_decision.json").write_text("{}", encoding="utf-8")
    res = salvage.salvage_run(run)
    assert res["skipped"] == "run owns _relative_buy_decision.json"


def test_salvage_absent_shared_is_recorded(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    run = _run(tmp_path, "20260701_2238", "2026-07-01T22:38:00", date="2026-07-01")
    res = salvage.salvage_run(run)
    assert res["files"]["_relative_buy_decision.json"]["verdict"] == "ABSENT"


def test_cli_all_is_idempotent(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    _run(tmp_path, "20260817_2215", "2026-08-17T22:15:00")
    _shared(tmp_path, "2026-08-17", "2026-08-17T22:15:30")
    assert salvage.main(["--all"]) == 0
    first = capsys.readouterr().out
    assert salvage.main(["--all"]) == 0
    assert capsys.readouterr().out == first
```

在 `tests/scan/test_chain_view_agents.py` 追加:

```python
def test_e6_section_reads_salvage_and_flags_it(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    run = _run(tmp_path)
    (run / "trace" / "staging").mkdir(exist_ok=True)
    sal = ws.reports_root() / "scan" / "_ledger" / "salvage" / run.name
    sal.mkdir(parents=True)
    (sal / "_relative_buy_decision.json").write_text(json.dumps({
        "mode": "active", "rule_version": "e6.v2.0", "blocked": False,
        "buys": [{"code": "603317", "rank": 1, "basis": "relative"}],
        "candidates": [{"code": "603317", "eligible": True, "rank": 1}], "excluded": []}), encoding="utf-8")
    text = chain_view.render(run, "603317")
    assert "✅ **本票就是当日 BUY**" in text
    assert "本视图有片段读自账本抢救目录" in text
```

- [ ] **Step 2: 跑用例确认失败**

Run: `uv run --no-sync python -m pytest tests/scan/test_salvage.py tests/scan/test_chain_view_agents.py -q -k "salvage"`
Expected: FAIL,`ModuleNotFoundError: autoresearch.scan.salvage`

- [ ] **Step 3: 实现 `salvage.py`**

```python
#!/usr/bin/env python3
"""老 run 抢救(2026-09-12 设计 §9.1):E6 决策与市场研判只在共享 staging 的那些 run,
把还在的文件拷进 `_ledger/salvage/<run_id>/` 并附来源判定。不写 run 目录(MANIFEST 不变量)。
只记不学;幂等。"""
from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

from autoresearch.common import workspace as ws
from autoresearch.scan.run_naming import is_run_dir

FILES = ("_relative_buy_decision.json", "market_view.md")
WINDOW = timedelta(minutes=30)
LATER_SLACK = timedelta(minutes=5)


def _manifest(run: Path) -> dict:
    try:
        return json.loads((run / "manifest.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def _naive(value: datetime) -> datetime:
    return value.replace(tzinfo=None) if value.tzinfo else value


def verdict_for(mtime: datetime, generated_at: datetime, later_runs) -> str:
    mtime, generated_at = _naive(mtime), _naive(generated_at)
    for later in later_runs:
        if _naive(later) <= mtime + LATER_SLACK:
            return "OVERWRITTEN_BY_LATER_RUN"
    if abs(mtime - generated_at) <= WINDOW:
        return "MATCHES_RUN_WINDOW"
    return "UNKNOWN"


def _owns_decision(run: Path) -> bool:
    return any((run / "trace").rglob("_relative_buy_decision.json")) if (run / "trace").is_dir() else False


def _later_same_date_runs(run: Path, date: str, generated_at: datetime) -> list[datetime]:
    out = []
    for sibling in run.parent.iterdir():
        if sibling == run or not sibling.is_dir() or not is_run_dir(sibling.name):
            continue
        man = _manifest(sibling)
        if man.get("analysis_date") != date:
            continue
        try:
            when = datetime.fromisoformat(str(man.get("generated_at")))
        except (TypeError, ValueError):
            continue
        if _naive(when) > _naive(generated_at):
            out.append(when)
    return sorted(out)


def _codex_rollouts(run: Path, sessions_root: Path | None, now: datetime | None) -> list[dict]:
    from autoresearch.scan import transcript_binder as tb

    started, ended = tb._run_window(run)
    if started is None:
        return []
    root = sessions_root or tb.CODEX_SESSIONS_ROOT
    cwd = str(ws.context_root().resolve().parent)
    end = now or ((ended or started) + tb.RUN_WINDOW_SLACK)
    return [{"path": str(p)} for p in tb.enumerate_rollouts(root, cwd, started - tb.RUN_WINDOW_SLACK, end)]


def salvage_run(run_dir: Path | str, *, shared_root: Path | None = None, ledger_root: Path | None = None,
                sessions_root: Path | None = None, now: datetime | None = None) -> dict:
    run = Path(run_dir)
    if _owns_decision(run):
        return {"run_id": run.name, "skipped": "run owns _relative_buy_decision.json"}
    man = _manifest(run)
    date = str(man.get("analysis_date") or "")
    try:
        generated_at = datetime.fromisoformat(str(man.get("generated_at")))
    except (TypeError, ValueError):
        generated_at = None
    shared = (Path(shared_root) if shared_root else ws.context_root() / "scan") / date
    ledger = Path(ledger_root) if ledger_root else ws.reports_root() / "scan" / "_ledger"
    target = ledger / "salvage" / run.name
    target.mkdir(parents=True, exist_ok=True)
    later = _later_same_date_runs(run, date, generated_at) if generated_at else []
    files: dict = {}
    for name in FILES:
        src = shared / name
        if not src.is_file():
            files[name] = {"source": str(src), "exists": False, "mtime": None, "sha256": None, "verdict": "ABSENT"}
            continue
        raw = src.read_bytes()
        mtime = datetime.fromtimestamp(src.stat().st_mtime)
        (target / name).write_bytes(raw)
        files[name] = {"source": str(src), "exists": True, "mtime": mtime.isoformat(timespec="seconds"),
                       "sha256": hashlib.sha256(raw).hexdigest(),
                       "verdict": verdict_for(mtime, generated_at, later) if generated_at else "UNKNOWN"}
    rollouts = []
    if ws.ENGINE == "codex" or "reports_codex" in run.resolve().as_posix():
        (target / "transcripts").mkdir(exist_ok=True)
        for item in _codex_rollouts(run, sessions_root, now):
            src = Path(item["path"])
            dest = target / "transcripts" / (src.name + ".gz")
            dest.write_bytes(gzip.compress(src.read_bytes(), mtime=0))
            rollouts.append({**item, "archived": str(dest)})
    doc = {"schema_version": 1, "run_id": run.name, "analysis_date": date,
           "generated_at": man.get("generated_at"), "files": files, "codex_rollouts": rollouts,
           "later_same_date_runs": [w.isoformat(timespec="seconds") for w in later],
           "computed_at": (now or datetime.now(timezone.utc)).isoformat(timespec="seconds")}
    (target / "provenance.json").write_text(json.dumps(doc, ensure_ascii=False, sort_keys=True, indent=1) + "\n",
                                            encoding="utf-8")
    return doc


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="老 run 抢救(零 LLM;只写 _ledger/salvage/)")
    ap.add_argument("run", nargs="?", help="run 目录或 run 名")
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--sessions-root", default=None)
    args = ap.parse_args(argv)
    base = ws.reports_root() / "scan"
    if args.all:
        runs = sorted(p for p in base.iterdir() if p.is_dir() and is_run_dir(p.name) and (p / "manifest.json").is_file())
    elif args.run:
        p = Path(args.run)
        runs = [p if p.exists() else base / args.run]
    else:
        ap.error("给 run 或 --all")
    fixed_now = datetime(2000, 1, 1, tzinfo=timezone.utc) if args.all else None   # --all 的 stdout 必须幂等
    summary = []
    for run in runs:
        res = salvage_run(run, sessions_root=Path(args.sessions_root) if args.sessions_root else None, now=fixed_now)
        summary.append({"run": run.name, "skipped": res.get("skipped"),
                        "verdicts": {k: v["verdict"] for k, v in (res.get("files") or {}).items()}})
    print(json.dumps(summary, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

(`--all` 时 `computed_at` 固定为哨兵值只为让 stdout 幂等;单 run 模式记真实时刻。)

- [ ] **Step 4: `chain_view` 第四级来源**

`Sources.__init__` 加 `self.salvage = self.ledger / "salvage" / self.run.name` 与 `self.used_salvage = False`;`find` 在共享 staging 之后加:

```python
        if self.salvage.is_dir():
            for name in names:
                p = self.salvage / name
                if p.is_file():
                    self.used_salvage = True
                    return p
```

`render` 页脚在 `if src.used_shared:` 之后加:

```python
    if src.used_salvage:
        body += ["", "---", "",
                 "⚠️ **本视图有片段读自账本抢救目录**(`_ledger/salvage/<run>/`,来源判定见其 `provenance.json`)"
                 "—— 判定为 OVERWRITTEN_BY_LATER_RUN 的片段**不是本 run 当时那份**。"]
```

- [ ] **Step 5: 登记 ARTIFACTS**

```python
    Artifact("salvage_provenance", "salvage/*/provenance.json", "ledger", "observe", "salvage", "json",
             "conditional", required_when="跑过 python -m autoresearch.scan.salvage"),
```

Run: `uv run --no-sync python -m autoresearch.contracts.emit --write && uv run --no-sync python -m pytest tests/contracts -q`

- [ ] **Step 6: 跑用例确认通过 + 真数据抢救**

Run: `uv run --no-sync python -m pytest tests/scan/test_salvage.py tests/scan/test_chain_view_agents.py tests/scan/test_chain_view.py -q`
Expected: 全绿

Run: `uv run --no-sync python -m autoresearch.scan.salvage --all`
Expected(spec E10):13 个 run 有 `_relative_buy_decision.json` 抢救件,其中 `20260817_2150` 的 verdict 为 `OVERWRITTEN_BY_LATER_RUN`,其余 12 个 `MATCHES_RUN_WINDOW`;46 个 `ABSENT`;6 个 `skipped`。把这三组数字写进提交信息。

- [ ] **Step 7: 提交**

```bash
git add autoresearch/scan/salvage.py autoresearch/scan/chain_view.py autoresearch/contracts/artifacts.py .claude/workflows tests/scan/test_salvage.py tests/scan/test_chain_view_agents.py
git commit -m "feat(scan): salvage shared-staging E6 decisions and market views into the ledger

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

## P0(spec §13 Q1 — **仅当用户裁定并入时执行**)

### Task 12: 结果账本 T+1/T+2 按交易日历核对(`outcome.market_frame`)

**Files:**
- Modify: `autoresearch/scan/outcome.py:220-248`(`market_frame`)、`compute_outcome`(文档加 `calendar_quality`)
- Test: `tests/scan/test_outcome_calendar.py`(新)

**Interfaces:**
- Produces: `market_frame(date, *, lake_daily=None, calendar=None) -> tuple[pd.DataFrame | None, dict]`;`calendar` 缺省 `exec_anchor.trading_sessions`,签名 `(start: str, end: str) -> (sessions: list["YYYY-MM-DD"], quality: str)`。湖里的 D+1/D+2 必须等于日历的 T+1/T+2,否则返回 `(None, {"reason": "湖缺交易日:…", "calendar_quality": quality})`;`quality == "lake_partitions"` 时无法核对,照旧放行但 meta 带 `calendar_quality`。`compute_outcome` 文档新键 `calendar_quality`。
- 立案证据:`ledger-t2-slides-when-lake-lags-20260912`(09-01 run t2=20260907;09-07 run t2=20260910)。

- [ ] **Step 1: 写失败用例**

```python
"""账本 T+2 取错交易日历(2026-09-12 逮到):湖缺日时 market_frame 把 T+2 滑到下一份文件。
修法:湖的 D+1/D+2 必须等于交易日历的 T+1/T+2,否则未成熟(返回 None),绝不滑。"""
from __future__ import annotations

import pandas as pd
import pytest

from autoresearch.scan import outcome


def _lake(tmp_path, days):
    lake = tmp_path / "daily"
    lake.mkdir()
    for i, d in enumerate(days):
        pd.DataFrame({"ts_code": ["600000.SH", "000001.SZ"], "trade_date": [d, d],
                      "open": [10.0 + i, 20.0 + i], "high": [11.0 + i, 21.0 + i], "low": [9.0 + i, 19.0 + i],
                      "close": [10.5 + i, 20.5 + i], "pct_chg": [1.0, 1.0], "amount": [1e5, 1e5]}
                     ).to_parquet(lake / f"{d}.parquet")
    return lake


def _calendar(sessions):
    return lambda start, end: ([f"{s[:4]}-{s[4:6]}-{s[6:]}" for s in sessions], "trade_cal")


def test_gap_in_lake_is_immature_not_slid(tmp_path):
    lake = _lake(tmp_path, ["20260901", "20260902", "20260907", "20260908"])   # 缺 0903/0904
    fr, meta = outcome.market_frame("2026-09-01", lake_daily=lake,
                                    calendar=_calendar(["20260901", "20260902", "20260903", "20260904", "20260907", "20260908"]))
    assert fr is None
    assert "湖缺交易日" in meta["reason"] and meta["calendar_quality"] == "trade_cal"
    assert "20260903" in meta["reason"]


def test_complete_lake_matches_calendar(tmp_path):
    lake = _lake(tmp_path, ["20260901", "20260902", "20260903", "20260904"])
    fr, meta = outcome.market_frame("2026-09-01", lake_daily=lake,
                                    calendar=_calendar(["20260901", "20260902", "20260903", "20260904"]))
    assert fr is not None and (meta["t1"], meta["t2"]) == ("20260902", "20260903")
    assert meta["calendar_quality"] == "trade_cal"
    assert fr.loc["600000", "t2_open"] == 12.0


def test_lake_partitions_quality_cannot_verify_and_says_so(tmp_path):
    lake = _lake(tmp_path, ["20260901", "20260902", "20260907"])
    fr, meta = outcome.market_frame("2026-09-01", lake_daily=lake,
                                    calendar=lambda s, e: (["2026-09-01", "2026-09-02", "2026-09-07"], "lake_partitions"))
    assert fr is not None and meta["calendar_quality"] == "lake_partitions"


def test_calendar_failure_is_immature(tmp_path):
    lake = _lake(tmp_path, ["20260901", "20260902", "20260903"])

    def boom(start, end):
        raise RuntimeError("tushare down")

    fr, meta = outcome.market_frame("2026-09-01", lake_daily=lake, calendar=boom)
    assert fr is None and "日历不可用" in meta["reason"]
```

- [ ] **Step 2: 跑用例确认失败**

Run: `uv run --no-sync python -m pytest tests/scan/test_outcome_calendar.py -q`
Expected: FAIL,`TypeError: market_frame() got an unexpected keyword argument 'calendar'`

- [ ] **Step 3: 实现**

`market_frame` 改为:

```python
def market_frame(date: str, *, lake_daily: Path | None = None,
                 calendar=None) -> tuple[pd.DataFrame | None, dict]:
    """当日全湖前向收益帧 + T+1 盘口。`None` = 未成熟(状态不是故障)。

    2026-09-12 P0:湖里的 D+1/D+2 **必须等于交易日历的 T+1/T+2**。此前只看湖文件列表,湖缺
    0903/0904 时把 09-01 的 T+2 滑到 09-07,一笔隔夜被记成 3 日持有(21/84 行错)。日历来自
    `exec_anchor.trading_sessions`(trade_cal → lake_partitions → weekday);`lake_partitions`
    质量下日历就是湖本身,无法核对,照旧放行但把质量写进 meta。日历本身不可用 → 未成熟。
    """
    from datetime import date as _date, timedelta as _td

    P = _panel.lake_trade_days(lake_daily)
    D = str(date).replace("-", "")
    if D not in P:
        return None, {"reason": "非交易日或湖里没有该日"}
    idx = P.index(D)
    if idx + 2 >= len(P):
        return None, {"reason": "D+2 尚未落湖(结果未成熟)"}
    if calendar is None:
        from autoresearch.scan.exec_anchor import trading_sessions as calendar
    d0 = _date.fromisoformat(f"{D[:4]}-{D[4:6]}-{D[6:]}")
    try:
        sessions, quality = calendar(D, (d0 + _td(days=20)).strftime("%Y%m%d"))
    except Exception as exc:  # noqa: BLE001 - 日历坏了就等,不猜
        return None, {"reason": f"日历不可用:{type(exc).__name__}: {exc}"}
    expected = [s.replace("-", "") for s in sessions if s.replace("-", "") > D][:2]
    lake_next = [P[idx + 1], P[idx + 2]]
    if quality != "lake_partitions" and expected != lake_next:
        return None, {"reason": f"湖缺交易日:日历要求 T+1/T+2={expected},湖里是 {lake_next}",
                      "calendar_quality": quality}
    window = P[max(0, idx - 1): min(len(P), idx + 13)]
    piv = _panel.load_lake_pivots(window, lake_daily)
    fr = _fwd.forward_frame(piv, P, D)
    if fr is None or fr.empty:
        return None, {"reason": "前向收益帧为空", "calendar_quality": quality}
    D1 = P[idx + 1]
    for col, key in (("t1_open", "open"), ("t1_high", "high"), ("t1_low", "low"),
                     ("t1_close", "close"), ("t1_pct_chg", "pct_chg")):
        series = piv.get(key)
        fr[col] = series[D1] if (series is not None and D1 in series.columns) else np.nan
    fr["t2_open"] = piv["open"][P[idx + 2]] if P[idx + 2] in piv["open"].columns else np.nan
    span = fr["t1_high"] - fr["t1_low"]
    fr["t1_pos_in_range"] = ((fr["t1_close"] - fr["t1_low"]) / span).where(span > 0)
    return fr, {"t1": D1, "t2": P[idx + 2], "n": int(len(fr)), "calendar_quality": quality}
```

`compute_outcome` 返回字典加 `"calendar_quality": meta.get("calendar_quality")`。

- [ ] **Step 4: 跑用例确认通过 + 既有账本用例**

Run: `uv run --no-sync python -m pytest tests/scan/test_outcome_calendar.py tests/scan/test_outcome.py tests/scan/test_ledger_views.py -q`
Expected: 全绿(既有用例 monkeypatch 了 `market_frame`,不受影响)

- [ ] **Step 5: 重算账本(数据操作,做完贴读数)**

Run: `uv run --no-sync python -m autoresearch.scan.outcome fill --rebuild --today 2026-09-XX`(XX = 当天)
Expected:`_ledger/outcome/20260901-0902_0154.json` 的 `t2 == "20260903"`,`20260907-0907_2233.json` 的 `t2 == "20260909"`;新华保险 601336 的 `t2_open == 60.8`、`gap_c1_o2 ≈ 0.0041`。核对命令:

```bash
uv run --no-sync python -c "
import json
for r in ('20260901-0902_0154','20260907-0907_2233'):
    j=json.load(open(f'reports_claude/scan/_ledger/outcome/{r}.json')); print(r, j['t1'], j['t2'], j.get('calendar_quality'))
print(json.load(open('reports_claude/scan/_ledger/outcome/20260901-0902_0154.json'))['rows']['601336']['t2_open'])"
```

然后 `uv run --no-sync python -m autoresearch.scan.outcome line`,把输出贴进提交信息。

- [ ] **Step 6: 变异探针**

把 `if quality != "lake_partitions" and expected != lake_next:` 改成 `if False:` → `test_gap_in_lake_is_immature_not_slid` 必红;改回。

- [ ] **Step 7: 提交**

```bash
git add autoresearch/scan/outcome.py tests/scan/test_outcome_calendar.py
git commit -m "fix(scan): outcome ledger requires lake D+1/D+2 to match the trading calendar

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

## 收尾:全量验收与真跑

- [ ] 两引擎全量:`AUTORESEARCH_ENGINE=claude uv run --no-sync python -m pytest -q` 与 `AUTORESEARCH_ENGINE=codex uv run --no-sync python -m pytest -q`,以及 `uv run --no-sync ruff check autoresearch tests`。把两个 passed 数写进最后一条提交信息。
- [ ] 下一次真扫描(Claude 与 Codex 各一次,spec §11.1④):当日 BUY 的 `chain_view` 有 ⑦b;`capsule verify` 的 agents 轴转绿;`token_usage.md`(Codex)不再写「无 transcript」。**这是验收不是开发**,读数写进 `docs/research/2026-09-XX-scene-reconstruction-acceptance.md`。
- [ ] 记忆:实施完成后更新 `scene-reconstruction-design-20260912` 记忆(状态改为已实施、提交号、绿灯数、真跑验收待/完)。

## 自审记录(写计划时对照 spec)

| spec 节 | 任务 | 备注 |
|---|---|---|
| §4 统一 invocation id | Task 3/4/5 | sector-brief 的 `<n>` 由期望表给出;无期望时用 `subject_key` 代 n |
| §5.1 生效点/失败纪律/开关/`stage` 形参 | Task 1, 6 | |
| §5.2 核心规则与产物路径表 | Task 4 | `MARKERS` |
| §5.3 Claude 定位器 | Task 4 | |
| §5.4 Codex 定位器与区段 | Task 5 | 子线程整份认领 |
| §5.5 Codex 期望回退 | Task 3 | `expectation_source` |
| §5.6 歧义/失败/幂等 | Task 4, 6 | `test_bind_run_is_idempotent` |
| §5.7 绑定报告 | Task 6 | |
| §5.8 回滚杆 | Task 6 | `retention.bind_transcripts` |
| §6.1 现成链 | — | 不改 |
| §6.2 三处小改 | Task 2 | |
| §6.3 离线索引 | Task 10 | |
| §7 chain_view 四处 | Task 7(①/⑦/⑦b)、Task 9(⑧)、Task 10/11(来源级) | |
| §8 E6 schema 2 | Task 8, 9 | |
| §9.1 抢救 | Task 11 | |
| §9.2 exec_check 账本腿 | **不在本计划**(Q2) | |
| §10 ARTIFACTS | Task 6, 10, 11 | |
| §11 验收/探针/回滚 | 各任务「变异探针」步 + 收尾 | |
| §13 Q1 T+2 修复 | Task 12(裁定后) | |

占位扫描:无 TBD/TODO;每个代码步都给了代码。类型一致性:`Candidate`/`Identity`/`assign` 的键名在 Task 4、5、6、10 一致;`agent_expectations` 行键(`subject_key`/`expectation_source`)在 Task 3、4、10 一致;`content_digest` 在 Task 2 定义、Task 7 消费其 `bytes`/`sha256` 键。
