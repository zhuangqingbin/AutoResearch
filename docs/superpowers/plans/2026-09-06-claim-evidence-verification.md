# Claim Evidence Verification Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox syntax for tracking. Use inline execution unless delegation is explicitly authorized.

**Goal:** 将重大事件的“关键词出现”与“证据支持断言”分开，接通主体、事件、状态、金额和时间的可审计校验。

**Architecture:** B1 收紧旧入口，B2 新增结构化证据比较器及引用绑定适配器。原文、观测身份仍归现有 catalog/blob；比较器无网络、无模型调用。拒稿与股票评级维持原有边界。

**Tech Stack:** Python 标准库、Decimal、现有 news/trace 契约、pytest；不新增模型服务。

**Status:** 待实施；本文代码是开发基准，不代表生产已接通。

**前置裁决（索引 §9 Q-B）：** `claim_ledger` 的 `review_draft`/`lint_claim`/`write_ledger`/`_supports`/`source_precision` 当前**生产零调用者**（2026-09-06 rg：只有 `tests/news/test_claim_ledger.py` 与 `tests/trace/test_evidence_index.py`；`.claude/` 零引用；四个 importer 只用 `LEDGER_NAME` 常量或在注释里提到它）。真在跑的防编造守卫是 `scan/l4/intel_guard.lint_claims`（`self_review`、`intel_status` 调用）。因此 **B1 单独提交没有任何生产效果**，B4 接线才让本包生效，而接线是 08-28 外源稿的 B 类项（Q1 未裁）且受 08-26 A0 冻结约束。Q-B 若裁③「把 v2 语义装进 intel_guard」，B2/B3 不变，B4 的接入点改为 intel_guard 的事件行（见 B4 末段）。

**接口（Consumes / Produces）：**

| 方向 | 内容 | 精确形状 |
|---|---|---|
| Consumes | catalog 观测 | `observations[id]["available_at"]`：带时区 ISO8601 或 None |
| Consumes | blob 原文 | `texts[id]`：str，其 sha256 与 `quote_spans[].blob_hash` 对应 |
| Consumes | 决策时点 | `decision_at`：由 `scan/exec_anchor.read_execution(run_dir)` 的 `first_available_session` + `exec_decision_cutoff`（"14:45"）+ `timezone_assumed` 派生；`actionability_status != "ACTIONABLE"` 的 run 不得改判为可得 |
| Produces（给 D2 `evidence_refs`） | 每条引用 | `{"claim_id": str, "source_observation_id": str, "blob_hash": "[0-9a-f]{64}", "rule_version": str, "support_verdict": "PASS"\|"FAIL"\|"UNKNOWN"}` |
| Produces（给 F） | 事实质量读数 | `verification_metrics()` 四率，分母零为 null |

**Design:** [主设计 §6](../specs/2026-09-06-research-reliability-and-system-evolution-design.md#6-工作包-b重大事件证据校验) · [全阶段索引](2026-09-06-research-system-implementation-index.md)

## 0. 范围与文件

| 文件 | 改动与职责 |
|---|---|
| autoresearch/news/claim_ledger.py | B1 保守判定；B2 接收已绑定引用与结构化比较结果 |
| autoresearch/contracts/claim_evidence.py | 纯字段声明及结构校验，不导入 news/trace |
| autoresearch/news/claim_support.py | 字段比较、原文定位、逐字段理由 |
| autoresearch/news/claim_binding.py | 原断言→观测→blob→经复核字段，唯一接入适配器 |
| autoresearch/trace/evidence_index.py | 记录新规则版本与证据引用，不拥有语义裁决 |
| tests/news/test_claim_support.py | 生命周期、金额口径、引用不足等确定性案例 |
| tests/news/test_claim_binding.py | 真实调用链连接及错配拒绝 |
| tests/fixtures/news/claim_support_v2.json | 80 条经人工复核的功能验收案例（回购/增持/减持/中标各 20） |

开始前先跑基线。引擎由 `common/workspace.py` 自动判定；**不要 `export AUTORESEARCH_ENGINE`**（显式 env 恒优先，Claude 会话照抄会写进 codex 根、违反 I02）：

~~~bash
git status --short
uv run --no-sync python -m pytest -q tests/news/test_claim_ledger.py tests/trace/test_evidence_index.py
~~~

预期：既有基线通过；如失败记录原因，不顺手修改无关代码。用户的 pinned.jsonc 不属于提交范围。

## Task B1：移除“关键词即支持”的错误确信

- [ ] **Step 1：在 tests/news/test_claim_support.py 添加回归测试。**

~~~python
import pytest
from autoresearch.news.claim_ledger import Claim, _supports, UNKNOWN

@pytest.mark.parametrize("text", [
    "公司公告终止回购计划，尚未实施回购。",
    "公司拟以不超过10亿元回购股份。",
    "本页仅展示公告摘要。",
    "",
])
def test_keyword_or_missing_text_is_not_full_support(text):
    claim = Claim("c1", "600000", "回购", "已完成10亿元回购", "2026-09-01")
    verdict, reason = _supports(claim, [text])
    assert verdict == UNKNOWN
    assert reason
~~~

- [ ] **Step 2：运行新增文件，确认旧实现出现 PASS/FAIL 与 UNKNOWN 的不符。**

~~~bash
uv run --no-sync python -m pytest -q tests/news/test_claim_support.py
~~~

- [ ] **Step 3：替换 claim_ledger._supports 的函数体，并更新 RULE_VERSION。**

~~~python
RULE_VERSION = "claim_ledger.v2.conservative"

def _supports(claim: Claim, texts: list[str]) -> tuple[str, str]:
    return UNKNOWN, "仅有自由文本或关键词；缺字段级证据，未核实不等于反驳"
~~~

保留函数签名；现有依赖“关键词即 VERIFIED”的测试改为 UNVERIFIED。价格字段独立校验与 ticket_verdict=KEEP 不变。B1 的价值是减少错误 PASS，不声称已增加语义覆盖。

- [ ] **Step 4：运行新增文件和既有 claim ledger 测试；提交仅这两个文件。**

预期：正反两类关键词探针都为 UNKNOWN；已有明确价格反证不受影响。

## Task B2：定义事件字段和原文定位契约

- [ ] **Step 1：将以下声明加入 contracts/claim_evidence.py。**

~~~python
from decimal import Decimal, InvalidOperation
import re

RULE_VERSION = "claim_support.v2"
IDENTITY_FIELDS = ("subject_code", "event_id", "predicate")
SEMANTIC_FIELDS = (
    "lifecycle", "assertion_kind", "polarity", "amount_value",
    "amount_unit", "amount_basis", "effective_at",
)
ENUMS = {
    "predicate": {"回购", "增持", "减持", "中标"},
    "lifecycle": {"plan", "in_progress", "completed", "terminated", "unknown"},
    "assertion_kind": {"actual", "forecast", "conditional", "quotation"},
    "polarity": {"affirmed", "negated", "uncertain"},
    "amount_unit": {"CNY", "shares", "percent", None},
    "amount_basis": {"planned_cap", "executed_total", "contract_total", "unknown", None},
}

def validate_event(event):
    required = set(IDENTITY_FIELDS + SEMANTIC_FIELDS)
    if required - event.keys():
        raise ValueError("missing event fields")
    if not re.fullmatch("[0-9]{6}", str(event["subject_code"])):
        raise ValueError("invalid subject code")
    if not isinstance(event["event_id"], str) or not event["event_id"]:
        raise ValueError("event identity required")
    for key, allowed in ENUMS.items():
        if event[key] not in allowed:
            raise ValueError(f"invalid {key}")
    value = event["amount_value"]
    if value is not None:
        if not isinstance(value, str):
            raise ValueError("amount must be a decimal string")
        try:
            amount = Decimal(value)
        except InvalidOperation as exc:
            raise ValueError("invalid amount") from exc
        if not amount.is_finite() or amount < 0:
            raise ValueError("invalid amount")
        if event["amount_unit"] is None or event["amount_basis"] in {None, "unknown"}:
            raise ValueError("amount requires unit and basis")
    validate_effective_time(event["effective_at"])
    return event
~~~

谓语首批四个：回购 / 增持 / 减持 / 中标。`claim_ledger._PREDICATE_WORDS` 本有 19 个受控词，「增持」是 A 股最常被拿来拉抬的正面断言，首批不能缺席；其余（立案/问询/重组/涨停…）等 B5 读数后按类扩，不一次全开。ClaimEvidence 外层字段完整使用主设计 §6.2；以上是其中的 event 对象，不以此子集替代整个证据包。外层 schema_version=2，并包含 claim_id、source_observation_ids、quote_spans、extraction_origin、verification_basis、rule_version。有效时间使用：

~~~json
{
  "start": "2026-09-01T00:00:00+08:00",
  "end": "2026-09-02T00:00:00+08:00",
  "precision": "day"
}
~~~

区间为左闭右开；精度不够时不能假装精确秒。标准化保留原金额“10亿元”及换算依据，转换后金额为 "1000000000" CNY。股份数、百分比不互相比较。

- [ ] **Step 2：为缺字段、错代码、未知枚举、NaN/Infinity、金额无单位分别加入参数化测试；运行确认校验明确拒绝。**

~~~python
from autoresearch.contracts.claim_evidence import validate_event

def event(**changes):
    result = dict(
        subject_code="600000", event_id="repurchase-2026-01", predicate="回购",
        lifecycle="completed", assertion_kind="actual", polarity="affirmed",
        amount_value="1000000000", amount_unit="CNY", amount_basis="executed_total",
        effective_at={"start": "2026-09-01T00:00:00+08:00",
                      "end": "2026-09-02T00:00:00+08:00", "precision": "day"},
    )
    return dict(result, **changes)

@pytest.mark.parametrize("changes", [
    {"subject_code": "unknown"}, {"predicate": "传闻"},
    {"amount_value": "NaN"}, {"amount_value": "Infinity"},
    {"amount_value": "100", "amount_unit": None},
])
def test_invalid_event_rejected(changes):
    with pytest.raises(ValueError):
        validate_event(event(**changes))
~~~

- [ ] **Step 3：加入时间区间校验测试：naive 时间、end≤start、非法精度均拒绝；start/end 与时区验证使用 datetime.fromisoformat 后直接比较。**

~~~python
from datetime import datetime

def validate_effective_time(value):
    if value is None:
        return
    if set(value) != {"start", "end", "precision"}:
        raise ValueError("invalid effective time")
    start, end = (datetime.fromisoformat(value[k]) for k in ("start", "end"))
    if start.tzinfo is None or end.tzinfo is None or end <= start:
        raise ValueError("invalid effective interval")
    if value["precision"] not in {"second", "minute", "day", "range"}:
        raise ValueError("invalid time precision")
~~~

validate_event 已在返回前调用 validate_effective_time；将本函数放入同一模块。日期字符串精度转换由适配器完成，不把来源缺失时间补成零点。

## Task B3：实现保守字段比较器

- [ ] **Step 1：添加以下比较测试，先确认新模块缺失导致 RED。**

~~~python
from autoresearch.news.claim_support import compare_events
from autoresearch.contracts.claim_evidence import IDENTITY_FIELDS, SEMANTIC_FIELDS

CHECKED = set(IDENTITY_FIELDS + SEMANTIC_FIELDS)

def test_complete_same_event_supports_claim():
    assert compare_events(event(), event(), checked_fields=CHECKED)["verdict"] == "PASS"

@pytest.mark.parametrize("changes,expected", [
    ({"lifecycle": "terminated"}, "FAIL"),
    ({"lifecycle": "plan", "amount_basis": "planned_cap"}, "FAIL"),
    ({"event_id": "another-plan"}, "UNKNOWN"),
    ({"subject_code": "600001"}, "UNKNOWN"),
    ({"polarity": "uncertain"}, "UNKNOWN"),
])
def test_counterevidence_requires_same_known_event(changes, expected):
    assert compare_events(event(), event(**changes), checked_fields=CHECKED)["verdict"] == expected

def test_unreviewed_session_extraction_is_unknown():
    assert compare_events(event(), event(), checked_fields=set())["verdict"] == "UNKNOWN"
~~~

- [ ] **Step 2：创建 claim_support.py，加入如下纯比较器。**

~~~python
from decimal import Decimal
from hashlib import sha256
from autoresearch.contracts.claim_evidence import (
    IDENTITY_FIELDS, SEMANTIC_FIELDS, RULE_VERSION, validate_event,
)

def quote_matches(text, span):
    start, end = span["start"], span["end"]
    return (
        type(start) is int and type(end) is int and 0 <= start < end <= len(text)
        and sha256(text.encode("utf-8")).hexdigest() == span["blob_hash"]
        and text[start:end] == span["text"]
    )

def compare_events(claim, source, *, checked_fields):
    validate_event(claim)
    validate_event(source)
    fields = {}
    for key in IDENTITY_FIELDS:
        fields[key] = "PASS" if (
            key in checked_fields and claim[key] == source[key]
        ) else "UNKNOWN"
    identity_ok = all(value == "PASS" for value in fields.values())
    for key in SEMANTIC_FIELDS:
        left, right = claim[key], source[key]
        if not identity_ok or key not in checked_fields:
            fields[key] = "UNKNOWN"
        elif left is None:
            fields[key] = "PASS"  # 断言没有声称此可选字段，不制造新主张
        elif right is None or right in ("unknown", "uncertain"):
            fields[key] = "UNKNOWN"
        elif key == "effective_at" and left != right:
            fields[key] = "UNKNOWN"  # 不同精度或时段交叠需单独复核
        elif key == "amount_value":
            fields[key] = "PASS" if Decimal(left) == Decimal(right) else "FAIL"
        else:
            fields[key] = "PASS" if left == right else "FAIL"
    verdict = ("FAIL" if "FAIL" in fields.values()
               else "PASS" if all(v == "PASS" for v in fields.values())
               else "UNKNOWN")
    return {"verdict": verdict, "fields": fields, "rule_version": RULE_VERSION}
~~~

checked_fields 不是 LLM 自报可信度：只能由来源结构化字段的白名单映射，或带 reviewer 身份和原文 hash 的人工复核记录提供。标题词表和未复核 session_extraction 默认不提供这些证明。时间不一致的 UNKNOWN 不可通过宽松“±3天”自动变 PASS。

- [ ] **Step 3：扩充边界：同公司旧计划完成/新计划终止分别绑定、明确否定、已执行金额与计划上限相等、正文被截断、多个引用互相冲突、原文字符偏移和 hash 改变。**

~~~python
from hashlib import sha256
from autoresearch.news.claim_support import quote_matches

def test_quote_is_relocatable_and_tamper_evident():
    text = "公告：本次回购已完成。"
    span = {"start": 3, "end": len(text), "text": text[3:],
            "blob_hash": sha256(text.encode("utf-8")).hexdigest()}
    assert quote_matches(text, span)
    assert not quote_matches(text + "更正", span)
    assert not quote_matches(text, dict(span, start=0))
~~~

完整性不足的材料不得提供反驳字段；同事件多份有效材料冲突时输出 source_conflict、UNKNOWN，不任意选第一份。公告“截至某日”的累计量需同口径比较。

## Task B4：接通引用身份，不把新校验器变成孤立单测

- [ ] **Step 1：新增 claim_binding.py；显式接收外层证据包、可信 catalog 观测和 blob 文本，不从自由文本 URL 猜 source_observation_id。**

~~~python
from autoresearch.news.claim_support import compare_events, quote_matches

def support_bound_claim(claim_event, bundle, *, observations, texts,
                        trusted_fields, decision_at):
    ids = bundle["source_observation_ids"]
    if not ids or any(i not in observations or i not in texts for i in ids):
        return {"verdict": "UNKNOWN", "reason": "SOURCE_NOT_BOUND"}
    if any(observations[i]["available_at"] is None
           or observations[i]["available_at"] > decision_at for i in ids):
        return {"verdict": "UNKNOWN", "reason": "NOT_AVAILABLE_AT_DECISION"}
    spans = bundle["quote_spans"]
    if not spans or any(
        s["source_observation_id"] not in ids
        or not quote_matches(texts[s["source_observation_id"]], s)
        for s in spans
    ):
        return {"verdict": "UNKNOWN", "reason": "QUOTE_NOT_VERIFIED"}
    return compare_events(claim_event, bundle["event"],
                          checked_fields=trusted_fields)
~~~

`decision_at` 不由调用方自由填：scan run 用 `scan/exec_anchor.read_execution(run_dir)` 派生——`first_available_session` 当日的 `exec_decision_cutoff`（14:45）按 `timezone_assumed` 组合；`actionability_status` 不是 `ACTIONABLE` 的 run 一律返回 `{"verdict": "UNKNOWN", "reason": "RUN_NOT_ACTIONABLE"}`，不得改判（08-28 逮到 13% 的 run 在 T+1 收盘后才批准，账本记了下不了的单）。available_at 与 decision_at 在进入该函数前转换为带时区 datetime；缺失值保留 None。bundle 只允许同一已明确的 event_id；跨来源冲突先归并为 source_conflict。claim_id 及主体必须与 ledger 中待校验 claim 一致，否则拒绝输入，不将别条断言的证明借来使用。

- [ ] **Step 2：扩展 review_draft 的可选关键字输入为 claim_bindings=None、as_of=None；保留现有参数。先 parse_claims，再按 claim_id 显式绑定 observation IDs，调用 lint_claim，最后覆盖 v2 内容支持与字段验证结果并重新计算 status/blame。**

接入段使用以下顺序，不复用旧 date_weld 的宽松 PASS 为新证据背书：

~~~python
def apply_v2_verdict(claim, support, *, accessible, temporal_verdict):
    verdicts = dict(claim.lint["verdicts"])
    verdicts[ACCESSIBLE] = accessible
    verdicts[CONTENT_SUPPORTS] = support["verdict"]
    verdicts[FIELDS_MATCH] = temporal_verdict
    claim.lint.update(verdicts=verdicts, support_v2=support,
                      rule_version=RULE_VERSION)
    claim.status = _status_from(verdicts, claim.status)
    claim.blame = _blame_from(verdicts, claim.status)
    return claim
~~~

此函数放在 claim_ledger.py，常量与汇总函数均为该模块已有定义。`RULE_VERSION` 就是 B1 定义的 `claim_ledger.v2.conservative`（入口/汇总规则）；`support["rule_version"]` 是 `contracts/claim_evidence.RULE_VERSION`（字段比较规则）。一个特性只有这两个版本串，ledger 行两者同记，不再出现第三个字面量。引用绑定不完整时 accessible/temporal_verdict=UNKNOWN；实际已绑定、时点已验证才 PASS。B1 旧入口无 v2 输入时继续保守 UNKNOWN。ACCEPT_DRAFT 不代表每条事实 VERIFIED。

- [ ] **Step 3：在 tests/news/test_claim_binding.py 测试完整 review_draft 链：有来源支持→VERIFIED；缺映射→UNVERIFIED；同事件明确反证→REFUTED 且 ticket_verdict 仍 KEEP；未来观测→UNVERIFIED。**

每个测试同时断言 claims[].citation_observation_ids 非空、lint.support_v2 存在、evidence_index 能追到同一 blob；不可只直接调用 compare_events 冒充接线通过。

- [ ] **Step 4：在 evidence_index 的参考条目增加 rule_version、claim_id、observation IDs、quote blob hash；旧 schema 读取保留默认未知，新字段不触发自动交易动作。**

索引只投影这些引用字段，不重复保存全文、不替代 claim_ledger 汇总。回归命令：

~~~bash
uv run --no-sync python -m pytest -q tests/news tests/trace/test_evidence_index.py tests/scan/test_intel_claims_lint.py tests/contracts/test_layering.py
~~~

**Q-B 裁③时的替代接入（索引建议项）：** B2/B3 与 `claim_binding.support_bound_claim` 原样保留；接入点从 `review_draft` 改为 `scan/l4/intel_guard.lint_claims` 的事件行（它已按 `self_code`/`trade_date` 逐行核对涨停等可验证事实，由 `self_review`、`intel_status` 调用，是唯一真在跑的守卫）。做法：把行内的回购/增持/减持/中标断言解析为 event，经 `support_bound_claim` 得 verdict 后写进 intel_guard 现有 lint 结果结构的**新增字段**（不改既有 verdict 语义）；`review_draft` 路径不再扩展。回归命令加 `tests/scan/test_intel_guard.py`；B5 的 80 条案例改在 intel 稿件上标注。

## Task B5：功能验收、质量读数及上线

- [ ] **Step 1：建立 80 条人工标注案例：回购/增持/减持/中标各 20 条，每类 7 条支持、7 条反证、6 条不足。人工标注和来源授权是执行依赖，不能用自动生成文本冒充已标注真实样本。**
- [ ] **Step 2：每条记录保存 case_id、event、source_event、原文来源/hash、预期逐字段 verdict、reviewer、标注日期、困难类型。原文授权不足时仅保存允许引用的片段与定位信息。**
- [ ] **Step 3：生成四项分母明确的读数：错误 PASS/所有预测 PASS、错误 FAIL/所有预测 FAIL、UNKNOWN/全部案例、缺原文/全部案例；分母零写 null。同时报 B1→B2 覆盖增量。**

~~~python
def verification_metrics(rows):
    def rate(wrong, predicted):
        selected = [r for r in rows if r["predicted"] == predicted]
        return (sum(r["expected"] != wrong for r in selected) / len(selected)
                if selected else None)
    n = len(rows)
    return {
        "false_pass_rate": rate("PASS", "PASS"),
        "false_fail_rate": rate("FAIL", "FAIL"),
        "unknown_rate": sum(r["predicted"] == "UNKNOWN" for r in rows) / n if n else None,
        "missing_text_rate": sum(not r["has_text"] for r in rows) / n if n else None,
    }
~~~

- [ ] **Step 4：发布条件：合成硬边界零错误 PASS；80 条结果逐例可查；新增可验证覆盖不能为零；所有 UNKNOWN 有原因。80 条不是推断总体准确率足够高的证明。**
- [ ] **Step 5：先独立研究运行，再接新 run 的 lint；真实扫描仍执行 GATE1/2/4。记录受到纠错影响的研究判断，不要求错误判断继续保持 parity。**

**提交边界：** B1 单独提交；B2/B3 契约与比较器一批；B4 接线一批；B5 验收资料一批。每批 git add 只列该批文件，先 git diff --check，再提交。

**回滚：** 可停用 v2 接入，保留 B1 的保守判定；不可恢复“关键词证明完整事实”。不重写冻结 run、不删除历史证据、不把拒稿升级为拒票。
