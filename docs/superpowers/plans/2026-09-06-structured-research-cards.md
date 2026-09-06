# Structured Research Cards Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox syntax for tracking. Use inline execution unless delegation is explicitly authorized.

**Goal:** 让研究事实拥有版本化结构源，Markdown 成为派生展示，同时保留已有 rubric、复核折回、DecisionRecord 和 relative_buy 的决策所有权。

**Architecture:** contracts 定义 ResearchCard；scan/l4 的 IO 和展示适配器消费它。先候选双产物比较，再对新 run 明确切换权威；旧 run 保持旧 parser，结构化 run 缺失/损坏时明确失败。

**Tech Stack:** Python、JSON、现有 agent_output 词表、rubric、pytest；不更换前端框架或发布文件体系。

**Status:** 待实施；先确认 B 的 evidence refs 接入语义。不是新建另一套终评级系统。

**前置裁决（索引 §9 Q-D）：** 本计划与 08-31 stock-research 稿 **D8「卡片双写 card.json」是同一件事**（用户已裁 Q1「做」，排 P2 未实施）。三处分歧先裁再动工：① **schema 单源**——D8 放 `contracts/agent_output.py` 并由 `emit` 生成 Claude def 机器块 / JS CARD schema / **Codex `--output-schema`**（两引擎交集：全 required + additionalProperties:false）；本稿新建 `contracts/research_card.py`。② **读取策略**——D8「JSON 优先 + md 解析回退 + 两侧对账 warn」；本稿新 run fail-closed。③ **字段集**——D8 有 `entry_veto[] / exec_lines[] / tripwires[] / base_rate_row / ev / conviction / as_of / engine / model`，本稿有 `theses / evidence_refs / scenarios / probability_basis / holding / management`。索引建议：① 走 D8 单源 + emit（`research_card.py` 只放校验函数，词表不复制）；② 对拍期用 D8 策略，20 卡逐字段 parity 后切本稿 fail-closed；③ 取并集。裁定后本稿以裁定为准改字段表，不与 D8 并存两稿。

**接口（Consumes / Produces）：**

| 方向 | 内容 | 精确形状 |
|---|---|---|
| Consumes（B2） | `evidence_refs[]` | `{claim_id, source_observation_id, blob_hash: [0-9a-f]{64}, rule_version, support_verdict ∈ {PASS, FAIL, UNKNOWN}}` |
| Consumes | 词表 | `contracts/agent_output.RATING_ORDER / PROPOSALS / OW_GATES / STOP_REASONS` + D1 下沉后的 `RUBRIC_DIMENSIONS` |
| Consumes | 持仓身份 | 任务簿 / `pinned.jsonc` 解析结果（bool）；卡内 `holding` 只能与之相等 |
| Produces | `card_to_row(fr, card) -> dict` | 旧 report model 字段 + `_card_facts{gate_states, early_stop, evidence_refs}`；`decision_finalize.research_gate_facts(row)` 读它 |
| Produces | `render_anchors(card) -> str` | 前四行锚点字面量与现有 `parse_rating(strict=True)` 兼容 |
| Produces | `card_lint_warnings(card) -> list[str]` | 研究规则告警（不拒卡），由 self_review 上报 |

**Design:** [主设计 §8](../specs/2026-09-06-research-reliability-and-system-evolution-design.md#8-工作包-d结构化研究事实与报告迁移) · [全阶段索引](2026-09-06-research-system-implementation-index.md)

## 0. 单一所有者

| 字段/动作 | 所有者 | 迁移限制 |
|---|---|---|
| 六维、三门、初判、P4、情景、论点 | ResearchCard（新版本） | 研究输入，不拥有正式交易 BUY |
| rubric 建议 | scan/l4/rubric.py | 继续调用现有 rubric_rating |
| verify / ensemble 折回 | decision_finalize.py | 原顺序不变，偏离理由保留 |
| 终评级机读事实 | decision_records.json | 有记录时仍是权威 |
| relative_buy | 现有相对买入选择器 | 不迁移到 ResearchCard |
| 旧 run | 原 parser + 既有 DecisionRecord 读取规则 | 不强制回填 JSON |

关键陷阱：gate_status(text) 的 bool 是“失败”；rubric_rating(dims,gates) 的 bool 是“通过”。新 JSON 只存 PASS/FAIL/UNKNOWN，转换必须显式，不能直接透传 bool。

## 1. 文件职责与消费者清单

| 文件 | 操作 |
|---|---|
| autoresearch/contracts/agent_output.py | 六维闭集下沉，保持已有 rating/proposal/gate 词表 |
| autoresearch/contracts/research_card.py | 新建结构校验与 schema 版本 |
| autoresearch/scan/l4/card_io.py | 版本化读取、身份核对、禁止静默 fallback |
| autoresearch/scan/l4/card_render.py | JSON→Markdown 纯展示 |
| autoresearch/scan/l4/rubric.py | 六维词表改为契约同源；公式不变 |
| autoresearch/scan/l4/parsers.py | _finalist_row 加新版本边界；旧函数体保留 |
| autoresearch/scan/decision_finalize.py | 新 run 的 gate/early_stop 不再从文本反推 |
| autoresearch/scan/report_sections.py | prepare_report_model 消费同源行 |
| `gate_status` / `parse_early_stop` / `parse_tripwires` / `_rating_of`（D8 点名的另四个 owner 函数，位置以 rg 为准） | 新版本改读 JSON；旧函数体保留给 legacy |
| publisher 注入 / price_claims / citation_density / yesterday_echo / chain_view（D8 点名的五个直接读卡者） | 保持读 md，但必须进 D5 的 parity 测试 |
| autoresearch/scan/l4_tasks.py、stock_stage.py、l4_watch.py | 新 JSON 完成门、hash、watcher/CP5 一致 |
| autoresearch/contracts/artifacts.py、scan/run_profile.py | 新产物登记、按版本 required |
| .claude/agents/l4-card.md、.claude/skills/stock-research/lite-playbook.md | 新版输出要求，保留研究三门与早停规则 |
| tests/contracts/test_research_card.py | schema 与未知字段拒绝 |
| tests/scan/test_research_card_migration.py | 比较、切换、故障及决策 parity |

## Task D1：版本化 schema 与同源词表

- [ ] **Step 1：将 rubric.py 的六维 tuple 原样下沉至 agent_output.py，旧模块导入转发；不更改评分函数。**

~~~python
RUBRIC_DIMENSIONS = ("基本面", "估值", "技术资金", "盈利质量", "偿付", "催化")
~~~

- [ ] **Step 2：添加 schema 校验器及失败测试。**

~~~python
import re
from datetime import date
from autoresearch.contracts.agent_output import (
    RATING_ORDER, PROPOSALS, OW_GATES, STOP_REASONS, RUBRIC_DIMENSIONS,
)

SCHEMA_VERSION = 1
REQUIRED = {
    "schema_version", "code", "analysis_date", "ruler", "dimensions", "gates",
    "early_stop", "theses", "evidence_refs", "initial_rating", "proposal",
    "rating_deviation_reason", "confidence", "scenarios", "probability_basis",
    "holding", "management", "target", "rr", "summary",
}

def validate_card(card):
    if set(card) != REQUIRED:
        raise ValueError("missing or unknown card fields")
    if type(card["schema_version"]) is not int or card["schema_version"] != SCHEMA_VERSION:
        raise ValueError("unsupported research card schema")
    if not re.fullmatch("[0-9]{6}", card["code"]):
        raise ValueError("invalid code")
    date.fromisoformat(card["analysis_date"])
    if card["ruler"] != "gap_c1_o2":
        raise ValueError("unsupported lite ruler")
    if set(card["dimensions"]) != set(RUBRIC_DIMENSIONS):
        raise ValueError("six dimensions required")
    if any(v not in {"强", "中", "弱", "未核"} for v in card["dimensions"].values()):
        raise ValueError("invalid dimension")
    if set(card["gates"]) != set(OW_GATES):
        raise ValueError("three gates required")
    if any(v not in {"PASS", "FAIL", "UNKNOWN"} for v in card["gates"].values()):
        raise ValueError("invalid gate state")
    if card["initial_rating"] not in RATING_ORDER or card["proposal"] not in PROPOSALS:
        raise ValueError("invalid rating or P4 proposal")
    if card["confidence"] not in {"高", "中", "低", None}:
        raise ValueError("invalid confidence")
    if card["probability_basis"] not in {"subjective", "not_provided"}:
        raise ValueError("uncalibrated probability must not be labelled calibrated")
    early = card["early_stop"]
    if early is not None:
        if set(early) != {"phase", "reason"}:
            raise ValueError("invalid early-stop shape")
        if early["phase"] not in {"P0", "P1", "P2", "P3", "P4", "P5"} or early["reason"] not in STOP_REASONS:
            raise ValueError("invalid early stop")
    if type(card["holding"]) is not bool:
        raise ValueError("holding must be boolean")
    for field in ("theses", "evidence_refs", "scenarios"):
        if not isinstance(card[field], list):
            raise ValueError(f"{field} must be a list")
    for field in ("management", "summary", "target", "rr", "rating_deviation_reason"):
        if not isinstance(card[field], str):
            raise ValueError(f"{field} must be text")
    validate_nested(card)
    return card
~~~

持仓票「盈利质量/偿付不得未核」是**研究规则，不进 schema 拒绝**：整卡 ValueError 会让持仓票变成 card_missing，E6/brief 就看不到持仓卡（08-26 曾因持仓卡缺席误判）。它放在同模块的 `card_lint_warnings`，由 self_review 作 warn 上报：

~~~python
def card_lint_warnings(card):
    warnings = []
    if card["holding"] and any(card["dimensions"][d] == "未核" for d in ("盈利质量", "偿付")):
        warnings.append("PINNED_QUALITY_UNREVIEWED")
    return warnings
~~~

此 schema 是 scan/lite 的 v1，不拿隔夜 ruler 约束 full 报告的公司经营观察期。嵌套结构在 Task D2 校验。holding 最终须与任务簿/持仓输入核对，不接受模型自行将持仓改成 false 绕过。

- [ ] **Step 3：创建完整 fixture，供本计划各测试复用。**

~~~python
def card_fixture():
    return {
        "schema_version": 1, "code": "600000", "analysis_date": "2026-09-01",
        "ruler": "gap_c1_o2",
        "dimensions": {d: "中" for d in RUBRIC_DIMENSIONS},
        "gates": {g: "UNKNOWN" for g in OW_GATES},
        "early_stop": None, "theses": [], "evidence_refs": [],
        "initial_rating": "Hold", "proposal": "HOLD",
        "rating_deviation_reason": "", "confidence": "低",
        "scenarios": [], "probability_basis": "not_provided",
        "holding": False, "management": "缺少可验证的隔夜触发，不据此建仓。",
        "target": "未核", "rr": "未核", "summary": "三门证据不足。",
    }
~~~

~~~python
import pytest
from autoresearch.contracts.research_card import validate_card

def test_card_cannot_override_final_buy_owner():
    card = card_fixture()
    card["relative_buy"] = True
    with pytest.raises(ValueError):
        validate_card(card)

def test_unknown_gate_is_not_pass():
    card = validate_card(card_fixture())
    from autoresearch.scan.l4.rubric import rubric_rating
    rating, _ = rubric_rating(card["dimensions"],
                              {k: v == "PASS" for k, v in card["gates"].items()})
    assert rating == "Hold"
~~~

先运行新文件确认 RED，再添加实现；运行 contracts 与既有 rubric 测试确认未改三门规则。

## Task D2：嵌套论点、情景与证据引用

- [ ] **Step 1：规定嵌套字段，schema 仅校形状；原文支持由 B 的 matcher 负责。**

| 数组 | 每项必需字段 |
|---|---|
| theses | thesis_id、statement、observable、source_refs、verification_date、falsifier、unknowns |
| evidence_refs | claim_id、source_observation_id、blob_hash、rule_version、support_verdict |
| scenarios | name、assumptions、return_fraction、probability、invalidators |

probability 是数值或 null；提供数值则三情景 bull/base/bear 合计 1、每项 [0,1]，basis 必须 subjective。return_fraction 为收益小数，不接收混 pp 的字符串。缺情景数据保留空列表和 not_provided，不编数字补齐。

- [ ] **Step 2：实现以下嵌套检查并放入同一模块；validate_card 已在返回前调用它。**

~~~python
import math

NESTED_FIELDS = {
    "theses": {"thesis_id", "statement", "observable", "source_refs",
               "verification_date", "falsifier", "unknowns"},
    "evidence_refs": {"claim_id", "source_observation_id", "blob_hash",
                      "rule_version", "support_verdict"},
    "scenarios": {"name", "assumptions", "return_fraction", "probability", "invalidators"},
}

def validate_nested(card):
    for group, required in NESTED_FIELDS.items():
        for row in card[group]:
            if not isinstance(row, dict) or set(row) != required:
                raise ValueError(f"invalid {group} shape")
    for ref in card["evidence_refs"]:
        if ref["support_verdict"] not in {"PASS", "FAIL", "UNKNOWN"}:
            raise ValueError("invalid support verdict")
        if not re.fullmatch("[0-9a-f]{64}", ref["blob_hash"]):
            raise ValueError("invalid evidence hash")
    probs = [s["probability"] for s in card["scenarios"]]
    values = [s["return_fraction"] for s in card["scenarios"]]
    if any(type(v) not in {int, float} or not math.isfinite(v) for v in values if v is not None):
        raise ValueError("invalid return fraction")
    if any(p is not None for p in probs):
        if len(probs) != 3 or {s["name"] for s in card["scenarios"]} != {"bull", "base", "bear"}:
            raise ValueError("three named scenarios required")
        if any(type(p) not in {int, float} or not math.isfinite(p) or not 0 <= p <= 1 for p in probs):
            raise ValueError("invalid subjective probabilities")
        if abs(sum(probs) - 1) > 1e-9 or card["probability_basis"] != "subjective":
            raise ValueError("invalid probability total or basis")
    elif card["probability_basis"] != "not_provided":
        raise ValueError("missing probabilities require not_provided basis")
~~~

- [ ] **Step 3：测试悬空 observation、重复 thesis_id、source_refs 不存在时，card_io 的证据解析阶段拒绝或标显式资料不足；不凭一个合法 hash 字符串认定证据确实存在。**
- [ ] **Step 4：为主观概率的 NaN、负值、缺一档、合计≠1 写参数化测试；三门输出继续来自本股，不接 sector_healthy_top3。**

## Task D3：渲染与 legacy 字段比较

- [ ] **Step 1：创建 card_render.py；先锁定最小机读锚点。**

~~~python
from autoresearch.contracts.research_card import validate_card
from autoresearch.scan.l4.rubric import rubric_rating

def render_anchors(card):
    validate_card(card)
    rating, reason = rubric_rating(
        card["dimensions"], {key: state == "PASS" for key, state in card["gates"].items()})
    if card["initial_rating"] != rating and not card["rating_deviation_reason"].strip():
        raise ValueError("rating deviation requires explicit justification")
    lines = [
        f"**Rating**: {card['initial_rating']}",
        f"FINAL TRANSACTION PROPOSAL: {card['proposal']}",
        f"Rubric: {rating} — {reason}",
        f"置信度：{card['confidence'] or '未核'}",
    ]
    if card["rating_deviation_reason"]:
        lines.append(f"**偏离**: {card['rating_deviation_reason']}")
    if card["early_stop"]:
        early = card["early_stop"]
        lines.append(f"**早停**:停于 {early['phase']} ｜ 停因:{early['reason']}")
    return "\n".join(lines) + "\n"
~~~

完整渲染在这些锚点后按既有卡片版式输出六维表、三门、情景、论点/证据和持仓说明。自由文本中的换行/表格竖线应在展示函数转义，不对事实源做截断修改；派生报告不反写 initial_rating。

- [ ] **Step 2：添加 parser round-trip 测试，标题变更不影响结构化事实。**

~~~python
from autoresearch.agents.utils.rating import parse_rating
from autoresearch.scan.l4.card_render import render_anchors

def test_render_preserves_rating_anchor():
    card = card_fixture()
    text = "# 另一种研究标题\n" + render_anchors(card)
    assert parse_rating(text, strict=True) == card["initial_rating"]
~~~

- [ ] **Step 3：候选双产物阶段写 details/<code>.research.json；Markdown 仍由现有流程生成并作为权威。比较结果仅写 card_compare.json，不供生产决策消费。**

~~~python
def compare_facts(legacy, candidate):
    keys = ("rating", "proposal", "gate_states", "early_stop",
            "evidence_refs", "holding", "target", "rr")
    return {key: {"legacy": legacy.get(key), "candidate": candidate.get(key)}
            for key in keys if legacy.get(key) != candidate.get(key)}
~~~

旧 parser 不支持的字段记 unsupported_in_legacy，不编空值宣称完全一致；改为与任务簿、catalog 或对应原始消费者比对。必须覆盖 pinned、早停、缺卡、零 BUY、verify 降级、ensemble 折回。

## Task D4：按 run 版本切换读取权威

- [ ] **Step 1：在 run_profile 中声明 card_source=legacy_md/candidate_json/research_json_v1；初始化时冻结进契约 hash。缺字段的历史 run 按 legacy_md，未知值失败。**
- [ ] **Step 2：添加严格读取边界，旧 parser 通过回调注入，避免 card_io→parser→card_io 循环。**

~~~python
import json
from pathlib import Path
from autoresearch.contracts.research_card import validate_card

def read_card(scan_dir, code, *, card_source, legacy_reader):
    if card_source in {"legacy_md", "candidate_json"}:
        return legacy_reader(scan_dir, code)
    if card_source != "research_json_v1":
        raise ValueError("unknown card source")
    path = Path(scan_dir) / "details" / f"{code}.research.json"
    card = validate_card(json.loads(path.read_text(encoding="utf-8")))
    if card["code"] != code:
        raise ValueError("card identity mismatch")
    return card
~~~

上游先校 code 与路径包含关系，并使用现有安全产物读取/hash 规则拒绝软链逃逸。analysis_date、holding 与当前 run/任务簿核对；缺文件、坏 JSON、错版本都应明确失败，不 catch 后调用 legacy_reader。

- [ ] **Step 3：_finalist_row 将旧函数体改名 _legacy_finalist_row；新入口按照冻结配置选择，结构化行适配为旧 report model 字段。**

~~~python
def card_to_row(fr, card):
    return {
        **fr, "rating": card["initial_rating"], "proposal": card["proposal"],
        "conf": card["confidence"] or "—", "target": card["target"],
        "rr": card["rr"], "l4": card["summary"],
        "rubric_dev": bool(card["rating_deviation_reason"]),
        "_card_facts": {"gate_states": card["gates"],
                        "early_stop": card["early_stop"],
                        "evidence_refs": card["evidence_refs"]},
    }
~~~

rubric_suggest 仍通过 rubric_rating 得到，不让字段缺席导致展示丢失。prepare_report_model 的 source→verify→ensemble 顺序保持不变。

- [ ] **Step 4：decision_finalize._build_decision_records 用如下分支替换新版本的文本 gate/early 提取；不改折回与终评级构造。**

~~~python
def research_gate_facts(row):
    facts = row["_card_facts"]
    return dict(facts["gate_states"]), facts["early_stop"]
~~~

旧分支保留 gate_status 的 failed_bool→FAIL/PASS 显式转换；新分支直接用枚举。card_missing 判定使用事实源存在性，不能因结构化卡成功但 Markdown 展示生成失败便悄悄认成无研究；展示缺失作为独立发布错误。

## Task D5：产物完整性、宿主模板及切换测试

- [ ] **Step 1：在 artifacts/run_profile 登记 l4_research_cards；只有 research_json_v1 的必要场景将其 required，candidate_json 只作比较附件。旧 run 的 complete 判定不追溯改变。schema 由 `emit` 同时生成 Codex `--output-schema` 文件（D8 ④），两引擎共用一份契约，不手写第二份。**
- [ ] **Step 2：l4_tasks.mark_success、stock_stage、l4_watch/CP5 同时验证 JSON 身份、契约版本、hash、Markdown 派生成功。taskbook 依旧拥有 attempt 与终态，不另建 card 状态机。**
- [ ] **Step 3：模板追加以下版本路由指令；不修改研究 rubric 或配额。**

> card_source=research_json_v1 时，先按已登记 ResearchCard schema 交付结构化事实，由确定性渲染器生成卡面。initial_rating 是本股独立初判，proposal 是 P4 倾向，不得输出正式 relative_buy 字段。三门仅使用本股证据，sector_healthy_top3 不得进入卡片。引用必须关联已有观测；UNKNOWN 不准填为 PASS。

- [ ] **Step 4：测试故障矩阵：坏 JSON+合法 Markdown 必须失败；新 JSON 缺失必须失败；旧 MD-only 可读；迟到旧 attempt JSON 不得覆盖新结果；持仓身份不一致必须失败；全角标点/“不是 Buy”不改变 JSON rating。**
- [ ] **Step 5：对同一 fixture 运行两条 report model 路径，断言 source/post_verify/final rating、proposal、gate、early_stop、evidence refs、持仓身份和 relative_buy 全字段 parity。**

~~~bash
uv run --no-sync python -m pytest -q tests/contracts/test_research_card.py tests/scan/test_research_card_migration.py tests/scan/test_decision_record.py tests/scan/test_decision_read_model.py tests/scan/test_l4_tasks.py tests/scan/test_stock_stage.py tests/contracts/test_layering.py
~~~

预期：新契约/故障测试与旧决策读取测试均通过。真实新版本扫描验收时 GATE1/2/4 必须照常执行。

## 发布、提交和回滚

提交顺序：D1/D2 契约；D3 只读比较；D4 权威切换适配；D5 生产路由与验收。每个提交列明 exact files，不混入用户 pinned.jsonc。

切换前必须有完整消费者清单、全部规定场景通过、无无法解释的决策事实差异。工程迁移不以“买入更多”作为成功条件。

回滚只影响下一次新 run 的版本选择。已冻结为 research_json_v1 的 run 继续用对应读取器，不改成 Markdown 权威；旧读路径保留用于历史版本。卡片结构化失败不允许通过减少字段、放松三门或静默 fallback 掩盖。
