# Orchestration Efficiency and Layering Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox syntax for tracking. Use inline execution unless delegation is explicitly authorized.

**Goal:** 用可核对的当前计量推进确定性命令执行和依赖方向收敛，保留推理质量、任务恢复和法证证据。

**Architecture:** 不替换 taskbook/capsule/UsageLedger；新适配器复用现有执行捕获，推理阶段返回宿主信封。分层改动以共享纯计算下沉和业务声明注入为单位，不通过动态 import 隐藏依赖。

**Tech Stack:** Python、现有 exec_capture、l4_tasks、contracts、pytest；宿主支持时使用现有 Workflow；无付费 LLM API。

**Status:** 待实施；09-04 metering Wave 1 已有实现，不重做。非 LLM runner 的接入以宿主能力验证为前提（E3 是条件项，见索引 Q-E）。

**接口（Consumes / Produces）：**

| 方向 | 内容 | 精确形状 |
|---|---|---|
| Consumes | 任务身份 | `scan/l4_tasks.preflight(book, code, expected_attempt=)` / `mark_success(book, code, expected_attempt=)` / `mark_failure(...)`；`scan/stage_result.contract_hash_for(scan_dir) -> str \| None` |
| Consumes | 执行留证 | `trace/exec_capture.run_captured(handle, stage, argv, *, invocation_id, attempt, subject)` |
| Consumes | capsule 接缝 | `trace/capsule.begin_run(kind, analysis_date, engine, config, *, now, session_ref, bootstrap)`（`bootstrap=None` 时现惰性 import `scan.run_bootstrap.prepare_scan_run`，E5 要拆的就是这条边）；`trace/usage_reconcile._reconcile_core(echo, rows, *, date, census, resolved)` |
| Consumes（E1） | 计量账本 | `scan/budget.observe_run(scan_dir, usage_ledger, timing, *, budgets, run_id, real_scan, persist)` 的观测；键名以 Step 0 的 rg 为准 |
| Produces（E2） | 推理信封 | `contracts/inference_task.validate_envelope(dict) -> dict`（九字段见代码）；`scan/deterministic_runner.verify_handoff(...)` 只校验不改任务簿 |
| Produces（E4） | 纯计算 | `common/forward_returns.forward_returns(piv, P, D, fwd) -> pd.DataFrame`、`_board_limit(code) -> float`、`forward_frame(piv, P, D) -> DataFrame \| None`；`data/market_panel.lake_trade_days(lake_daily=None) -> list[str]`、`load_lake_pivots(dates, lake_daily=None) -> dict[str, DataFrame]` |

**Design:** [主设计 §9](../specs/2026-09-06-research-reliability-and-system-evolution-design.md#9-工作包-e编排效率与依赖收敛) · [全阶段索引](2026-09-06-research-system-implementation-index.md)

## 0. 前置阅读与文件

先完整阅读 [09-04 两线设计](../specs/2026-09-04-token-efficiency-two-lines-design.md) 中的已实施范围、成熟 cohort 与质量门。该文历史成本属于其声明的引擎，不是 Codex 当前成本。本次不运行 Claude transcript 计量命令，不读其他引擎产物。

| 文件 | 职责 |
|---|---|
| autoresearch/contracts/inference_task.py | 信封字段与纯形状校验 |
| autoresearch/scan/deterministic_runner.py | argv 捕获、停在任务边界、失败传播 |
| autoresearch/scan/l4_tasks.py | 继续独占 claim/attempt/终态/产物 hash |
| autoresearch/trace/exec_capture.py | 复用已有进程/日志/信号留证；只有接口不足时作兼容扩展 |
| autoresearch/common/forward_returns.py | 下沉已有收益纯计算，保持 legacy 语义 |
| autoresearch/data/market_panel.py | 湖日期与面板读取，IO 不搬到纯计算层 |
| autoresearch/research/edge_census.py、factor_lab.py | 旧接口兼容转发 |
| autoresearch/trace/capsule.py、usage_reconcile.py | 将业务声明依赖改为显式参数 |
| autoresearch/scan/run_bootstrap.py、run_profile.py | 负责业务对象组装与注入 |
| tests/scan/test_deterministic_runner.py | 宿主交接与任务冲突 |
| tests/common/test_forward_returns_parity.py | 搬迁前后数值与 dtype |
| tests/contracts/test_layering.py | 棘轮清单只减不增 |

## Task E1：当前基线与路由能力验证

- [ ] **Step 0：先列真实字段名。** 当前 `budget.observe_run` 的观测与 `trace/usage_panorama` 里只有 `weighted_input_proxy`、`wall_seconds` 一类键；`mature_finalists`、`metering_version`、`failed_tasks`、`attempted_tasks` **查无**（2026-09-06 rg）。用 rg 列出观测键，把下面函数的输入映射到真实键；缺的键先在 contracts 登记再产，不得假设存在。

- [ ] **Step 1：从本引擎已绑定 run 的 UsageLedger 读当前结果，记录 run_id、模式、finalist/pinned 数、情报覆盖、wall time、调用次数、失败/重试、input/output token、weighted proxy 与 metering_version。**
- [ ] **Step 2：缺 token 则写 null，缺 transcript 关联则计量不完整；不从输出字数估“真实 token”，不借历史 Claude 数字补齐。**
- [ ] **Step 3：新增只读聚合函数，成熟样本只按已登记 coverage 字段纳入。**

~~~python
REQUIRED_KEYS = ("run_id", "engine", "metering_version", "mature_finalists",
                 "weighted_input_proxy", "wall_seconds", "failed_tasks", "attempted_tasks")

def efficiency_rows(records):
    result = []
    for row in records:
        missing = [key for key in REQUIRED_KEYS if key not in row]
        count = row.get("mature_finalists")
        tokens = row.get("weighted_input_proxy")
        attempted = row.get("attempted_tasks")
        failed = row.get("failed_tasks")
        result.append({
            "run_id": row.get("run_id"), "engine": row.get("engine"),
            "metering_version": row.get("metering_version"),
            "mature_finalists": count,
            "proxy_per_finalist": tokens / count if tokens is not None and count else None,
            "wall_seconds": row.get("wall_seconds"),
            "failure_rate": failed / attempted if attempted and failed is not None else None,
            "coverage": "COMPLETE" if not missing else "MISSING:" + ",".join(missing),
        })
    return result
~~~

这只是读模型，不拥有预算红黄绿；继续使用 budget.observe_run。不同模式分层比较，不用总成本降低证明每票效率变好。

- [ ] **Step 4：在 tmp_path 的无网络 fixture 执行一个确定性命令，记录当前宿主是否具备直接进程工具、是否能传 run handle、是否支持暂停后人工/会话接续。**

结果写 capability_report.json，字段 deterministic_exec、capture_binding、inference_handoff、safe_resume 四项布尔及证据。缺 inference_handoff 时保留旧 Workflow 或人工接续，不实现假会话 API。

**决策点：** 只在当前计量满足已有升级条件且宿主能力成立时启用 runner。这里是条件明确的可选路线，不影响 E4/E5 的分层工作；不预先承诺节省百分比。

## Task E2：建立推理信封，复用任务身份

- [ ] **Step 1：新增 inference_task.py 的形状校验。**

~~~python
import re

FIELDS = {
    "schema_version", "engine", "run_id", "task_id", "role",
    "input_artifact_ids", "input_contract_hash", "expected_output_contract", "attempt",
}

def validate_envelope(value):
    if set(value) != FIELDS:
        raise ValueError("missing or unknown inference envelope fields")
    if type(value["schema_version"]) is not int or value["schema_version"] != 1:
        raise ValueError("invalid envelope version")
    if type(value["attempt"]) is not int or value["attempt"] < 1:
        raise ValueError("invalid attempt")
    if not re.fullmatch("[0-9a-f]{64}", value["input_contract_hash"]):
        raise ValueError("invalid contract hash")
    if not isinstance(value["input_artifact_ids"], list) or not value["input_artifact_ids"]:
        raise ValueError("inputs required")
    if any(not isinstance(x, str) or not x for x in value["input_artifact_ids"]):
        raise ValueError("invalid artifact identity")
    return value
~~~

run_id 使用上层 workspace.validate_run_id 验证，contracts 不反向导入 common。role 和 expected_output_contract 必须在现有注册表中匹配，不允许宿主自造角色；task_id 使用现有任务键，L4 为 taskbook 的六位 code，不平行生成 UUID。

- [ ] **Step 2：在 scan/deterministic_runner.py 中加入运行绑定检查。**

~~~python
from autoresearch.common import workspace as ws
from autoresearch.contracts.inference_task import validate_envelope

def verify_handoff(envelope, *, engine, run_id, task, input_hash,
                   available_artifacts, registered_contracts):
    validate_envelope(envelope)
    ws.validate_run_id(envelope["run_id"])
    expected = (engine, run_id, task["code"], task["attempt"], input_hash)
    actual = tuple(envelope[k] for k in (
        "engine", "run_id", "task_id", "attempt", "input_contract_hash"))
    if actual != expected or task["status"] != "RUNNING":
        raise ValueError("stale or foreign handoff")
    if not set(envelope["input_artifact_ids"]) <= set(available_artifacts):
        raise ValueError("missing registered input")
    key = (envelope["role"], envelope["expected_output_contract"])
    if key not in registered_contracts:
        raise ValueError("unknown role/output contract")
    return envelope
~~~

input_hash 从既有 contract_hash_for 获取，不自行 hash 某个字符串代替 run 契约。available_artifacts 必须已验证路径、hash、所属引擎和可得时间。以上函数只做校验，不能代替 taskbook 锁内的终态检查。

- [ ] **Step 3：写坏 hash、旧 attempt、错 engine、错 run、缺产物与未知角色测试。**

~~~python
import pytest
from autoresearch.contracts.inference_task import validate_envelope

def envelope():
    return {
        "schema_version": 1, "engine": "codex",
        "run_id": "20260901T000000000001Z", "task_id": "600000",
        "role": "l4-card", "input_artifact_ids": ["prompt", "slim"],
        "input_contract_hash": "a" * 64,
        "expected_output_contract": "ResearchCard.v1", "attempt": 1,
    }

@pytest.mark.parametrize("changes", [{"attempt": 0}, {"attempt": True},
                                    {"input_contract_hash": "bad"},
                                    {"input_artifact_ids": []}])
def test_invalid_envelope_rejected(changes):
    with pytest.raises(ValueError):
        validate_envelope(dict(envelope(), **changes))
~~~

ResearchCard.v1 只有 D 注册后才可使用；D 未上线时必须传现有 L4_CARD 对应契约名/版本，不通过硬编码字符串绕过注册表。

## Task E3：确定性执行与恢复，不启动第二份任务

**条件项（索引 Q-E）：** 09-04 裁定「扫描留 workflow.js 做减法」。本任务只在 E1 的 capability_report 四项为真且用户裁 Q-E 立项后开工，不排进固定波次；E2 信封不依赖它。

- [ ] **Step 1：实现捕获包装；argv 由代码中的阶段注册表生成，禁止从模型自由文本生成 shell 命令。**

~~~python
from autoresearch.trace.exec_capture import run_captured

def execute_step(handle, *, stage, argv, invocation_id, attempt, subject=None):
    if not isinstance(argv, list) or not argv or any(not isinstance(x, str) for x in argv):
        raise ValueError("argv must be a nonempty string list")
    result = run_captured(
        handle, stage, argv, invocation_id=invocation_id,
        attempt=attempt, subject=subject,
    )
    if result.exit_code != 0:
        raise RuntimeError(f"deterministic step failed: exit={result.exit_code}")
    return result
~~~

不使用 shell=True，不在 args 内拼接分号/重定向；环境留证和脱敏复用 exec_capture，不再写另一套日志捕获器。exit=0 仅代表进程完成，产物契约门未过不能 mark_success。

- [ ] **Step 2：给 execute_step 写 monkeypatch 测试，断言 argv 不被拼成字符串，非零 exit 会传播。**

~~~python
from types import SimpleNamespace
from autoresearch.scan import deterministic_runner as runner

def test_capture_preserves_argv_and_error(monkeypatch):
    seen = {}
    def capture(handle, stage, argv, **kwargs):
        seen["argv"] = argv
        return SimpleNamespace(exit_code=7, invocation={"id": kwargs["invocation_id"]})
    monkeypatch.setattr(runner, "run_captured", capture)
    argv = ["python", "-c", "print('literal;not a shell')"]
    with pytest.raises(RuntimeError):
        runner.execute_step(object(), stage="unit", argv=argv,
                            invocation_id="fixture-one", attempt=1)
    assert seen["argv"] == argv
~~~

- [ ] **Step 3：调用已有 preflight(book,code,expected_attempt=...) 领取执行权，严格处理返回 action；不是已领取状态就不派发。**
- [ ] **Step 4：每个确定性段完成后跑对应输出契约；推理段输出信封并停止。宿主完成后校验 handoff，再调用 mark_success(book,code,expected_attempt=attempt)，由其锁内重新检查状态与产物。**
- [ ] **Step 5：失败通过 mark_failure 的既有 error_class 闭集记录，保留日志和 attempt。只重试已知可安全重试的阶段；任务存活状态不明时先 reconcile，不凭超时直接再启动。**

不得在预热、抓取失败后重新跑整段推理来凑成功；重试命令的 invocation_id 必须独立且可追到同一 task/attempt。

- [ ] **Step 6：测试并发领取只成功一方、迟到回报拒绝、输入 hash 变化不复用、kill/信号保留执行证据、部分产物不得成功、重启不重复执行 SUCCEEDED 任务。**

复用 tests/scan/test_l4_tasks*.py 和 tests/trace/test_exec_capture.py 的 active handle / tmp_path fixtures，不伪造生产 RunHandle。无任务簿的 LEGACY 分支不冒充具备幂等恢复能力。

## Task E4：收益纯计算下沉，保留统计口径

- [ ] **Step 1：在修改前保存内存 golden fixture；覆盖主板/科创/创业/北交代码、缺 D+2、缺 high/low、pd.NA、封板退出与非均衡日样本。**
- [ ] **Step 2：将 factor_lab.py 中 _board_limit 与 forward_returns 的完整函数体机械搬入 common/forward_returns.py；只保留 NumPy/pandas 依赖。**

_board_limit 是既有历史代理的规则近似，本次只搬迁，不宣称它已覆盖所有日期、ST/新股状态或实际成交制度。真实可交易规则由 C 的版本化来源解释；改变规则要另写行为差异测试。

- [ ] **Step 3：原 factor_lab.py 用同对象导入作兼容转发。**

~~~python
from autoresearch.common.forward_returns import _board_limit, forward_returns
~~~

- [ ] **Step 4：将 edge_census.lake_trade_days/load_lake_pivots 完整搬入 data/market_panel.py；forward_frame 的收益计算和 GAP_CLIP 下沉 common/forward_returns.py，输入仍为已读好的 piv/P/D。**

不把读湖 IO 塞入 contracts，不更改缺失日期成熟判定、收益单位、均值/中位数基准。scan/outcome、populations、ledger_views 改为直接引用新的数据与纯计算 owner；旧 edge_census 接口转发保留。调用者清单以实施时 rg 为准：2026-09-06 实测引用 `forward_frame`/`load_lake_pivots`/`lake_trade_days` 的还有 `analyze/ledger.py`、`research/overnight_census/{backfill,families,panel}.py`、`research/overseas_event_census.py`——转发期它们不改，逐个迁完再删转发。

common/forward_returns.py 的包装器直接调用同模块函数，不保留原来的 research.factor_lab 反向引用：

~~~python
from autoresearch.common import ruler as _ruler

def forward_frame(piv, P, D):
    if not piv or D not in P:
        return None
    frame = forward_returns(piv, P, D, fwd=10)
    bad = frame[_ruler.MAIN_RULER].abs() > _ruler.GAP_CLIP
    frame.attrs["n_clipped"] = int(bad.fillna(False).sum())
    frame.loc[bad.fillna(False), _ruler.MAIN_RULER] = np.nan
    return frame
~~~

NumPy 的 np 来自同模块搬迁的既有导入；转发到旧 edge_census.py 的代码为：

~~~python
from autoresearch.common.forward_returns import forward_frame
from autoresearch.data.market_panel import lake_trade_days, load_lake_pivots
~~~

- [ ] **Step 5：添加对象与数值 parity 测试；同输入所有列、index、dtype、attrs 相等。**

~~~python
from autoresearch.common import forward_returns as shared
from autoresearch.research import factor_lab

def test_legacy_forward_returns_is_same_implementation():
    assert factor_lab.forward_returns is shared.forward_returns
    assert factor_lab._board_limit is shared._board_limit
~~~

golden 对照必须在搬迁前从原实现产生；搬迁后两个相同函数对拍只能验证转发，不能独自证明数值没有变。

## Task E5：业务声明注入及残余依赖台账

- [ ] **Step 1：将 capsule 所需 ArtifactSpec/CRITICAL_ARTIFACTS 的共享声明放入 contracts，业务选择仍由 scan.run_profile 提供；trace 接受已组装 profile，不自行调用 prepare_scan_run。**
- [ ] **Step 2：将“准备 scan run→创建 capsule”的组合调用移到 scan.run_bootstrap；已有 trace 面向旧业务的入口保留显式弃用桥，并记录尚未移除的边。不能把桥藏进 importlib 冒充解耦完成。**
- [ ] **Step 3：usage_reconcile 改接收 resolved_agent_config 参数，scan 入口调用 load_resolved_agent_config 后传入。配置校验不变，trace 不读取 scan.user_config。**

capsule.begin_run 已有 bootstrap 参数，优先使用这个现成接缝，不再新造 bootstrap 协议。在 scan/run_bootstrap.py 新增的业务组合入口：

~~~python
def begin_scan_run(analysis_date, engine, config, *, now=None, session_ref=None):
    from autoresearch.trace.capsule import begin_run
    return begin_run("scan-market", analysis_date, engine, config,
                     now=now, session_ref=session_ref, bootstrap=prepare_scan_run)
~~~

prepare_scan_run 是该模块的现有函数。调用点全部迁移后，trace.begin_run 的 bootstrap=None 分支明确报缺业务输入，移除默认 import scan 的行为；旧 CLI 的业务路由迁到 scan 入口并在使用文档写明迁移命令。

usage_reconcile._reconcile_core 已接收 resolved 参数，保留其比较逻辑。新 reconcile(..., *, resolved_agent_config=None) 的返回段使用：

~~~python
def reconcile_with_resolved(echo, rows, *, date, census, resolved_agent_config):
    if resolved_agent_config is None:
        raise ValueError("resolved agent config must be supplied by caller")
    return _reconcile_core(echo, rows, date=date, census=census,
                           resolved=resolved_agent_config or (echo.get("resolved_agents") or {}))
~~~

此接入段放在 trace/usage_reconcile.py；scan 调用者负责 load_resolved_agent_config。新参数先 keyword-only 兼容扩展，逐调用点迁移后再删除旧桥。scan 和 analyze 分别执行 profile/capsule 测试，防止以 scan 专用字段污染通用 trace。

- [ ] **Step 4：逐项记录剩余 scan→research 边：consensus 的生产数据接口搬到 data，研究诊断保留 research；prelude 的 rejection readout 若暂未解耦就明确保留该边，不删 allowlist 条目。**
- [ ] **Step 5：dossier/sector/derivatives→scan 的业务回调改由上层组合根传入；common→data 的 IO 拆到 data 适配器，data→trace 的留证通过既有 hook/参数注入。每次只迁一条调用链并测实际执行。**

残余调用点落实到文件，不以“其他地方清理一下”代替任务：

| 文件 | 迁移的具体输入/行为 |
|---|---|
| dossier/builder.py、dossier/delta.py | calendar_flags、scan dossier 组装与 final ratings 由上层提供 |
| dossier/pool.py | pinned 输入由上层解析后传入，禁止改变持仓清单 |
| sector/reuse.py、sector/pack.py | previous_staging_dirs 与 knob 改传已解析值 |
| derivatives/options_lake.py | ALLOWED_KEYS 的纯词表移入 contracts，再两侧引用 |
| common/scoring.py、common/uzi_lenses.py | 计算与取数/降级记录分离，保留实际降级留痕 |
| data/cache.py | source_lineage 与 replay 通过注入接口接入；replay 的禁网络约束不能因解耦丢失 |

表中路径前缀均为 autoresearch/。每条迁移先用 rg 确认最新调用者，再建立保持旧参数行为的适配测试；在执行该条前不移动其他条的文件。

以上残余清单属于本包后半段，不要求同一个提交清空所有边。函数体迁移以原源码为基线，必须保留完整参数与错误语义；不为减少图上的边数增加空抽象层。

- [ ] **Step 6：只有真实静态与运行时引用均消失才缩小 KNOWN_UPWARD；新增 contracts 文件一条上行边都不允许。**

~~~bash
uv run --no-sync python -m pytest -q tests/contracts/test_layering.py tests/scan/test_deterministic_runner.py tests/common/test_forward_returns_parity.py tests/scan/test_l4_tasks.py tests/scan/test_stock_stage.py tests/trace/test_exec_capture.py
~~~

### E5 残余边台账(2026-09-07 实测,`tests/contracts/test_layering.py::_edges()`)

| 边 | 文件 | 状态 | 收敛路径 |
|---|---|---|---|
| trace → scan | `trace/capsule.py` | 未清 | `begin_run(bootstrap=None)` 的惰性 `scan.run_bootstrap` 兜底 + `scan.artifacts`;E5 步 1/2 |
| trace → scan | `trace/usage_reconcile.py` | **已收窄**(E5 步 3):库函数不读 scan,只剩 `_resolved_via_legacy_bridge` 一个显式旧桥 | CP7 命令路由迁到 scan 入口后删桥 |
| trace → news | `trace/evidence_index.py` | 未清 | 读 `claim_ledger.LEDGER_NAME`;B4 ③ 之后 claim_ledger 休眠,可把常量下沉 contracts |
| scan → research | `scan/l4/producers.py` | 未清 | `consensus` 的生产数据接口搬到 data,研究诊断留 research |
| scan → research | `scan/populations.py` | **已收窄**(E4):收益/湖读取改走 common/data,只剩 `MIN_CROSS_SECTION` 一个阈值 | 阈值下沉 contracts 或 common |
| scan → research | `scan/prelude.py` | 未清 | `consensus.pull` + `edge_census.rejection_line/readout`;若暂不解耦就明确保留,不删 allowlist |
| scan → research | `scan/outcome.py`、`scan/ledger_views.py` | **已清**(E4) | — |
| data → trace | `data/cache.py` | 未清 | source_lineage 与 replay 通过注入接口接入;replay 禁网络约束不能因解耦丢失 |
| common → data | `common/scoring.py`、`common/uzi_lenses.py` | 未清 | 计算与取数/降级记录分离,保留降级留痕 |
| dossier → scan | `dossier/builder.py`、`dossier/delta.py`、`dossier/pool.py` | 未清 | calendar_flags / final ratings / pinned 由上层解析后传入 |
| sector → scan | `sector/pack.py`、`sector/reuse.py` | 未清 | previous_staging_dirs 与 knob 改传已解析值 |
| derivatives → scan | `derivatives/options_lake.py` | 未清 | `ALLOWED_KEYS` 纯词表移入 contracts |

KNOWN_UPWARD 八条边**一条都没 stale**(每条至少还剩一个文件);棘轮没有放宽。每条迁移先用 rg
确认最新调用者,一次只迁一条调用链并测实际执行。

## 发布验收与回滚

| 维度 | 放行条件 |
|---|---|
| 质量 | GATE1/2/4、情报覆盖、卡片字段、失败率无未经解释的退化 |
| 效率 | 当前同引擎成熟 cohort 实测；wall time、总成本与每 finalist 成本同时报告 |
| 恢复 | 无重复任务、伪成功、迟到覆盖、冻结产物改写 |
| 架构 | 指定调用链确实向下依赖；残余边有清单、不新增豁免 |
| 决策 | 同 fixture 的 rubric/终评级/relative_buy 不因工程迁移改变 |

提交按 E1/E2/E3/E4/E5 拆分，E4 内一次只搬一组函数；跨模块批次合并前跑全量测试。旧 Workflow 可在下一任务边界恢复；运行中任务先完成或冻结，再切换路由。当前用户只要求文档，不在本次会话启动 runner、修改宿主设置或执行全市场扫描。
