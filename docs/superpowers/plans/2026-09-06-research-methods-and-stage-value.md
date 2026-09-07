# Research Methods and Stage Value Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox syntax for tracking. Use inline execution unless delegation is explicitly authorized.

**Goal:** 将公司研究、隔夜选股、实际执行三类能力分别量化，并用可复现、时点一致的离线研究评价 L3/L4 与新因子的增量价值。

**Architecture:** 只读取本引擎冻结研究记录与共享行情数据，实验方案先登记、结果写独立目录。复用 A 的日等权统计和 C 的执行结果；不恢复学习层、自动权重调整、每日影子漏斗或历史反馈回注。

**Tech Stack:** Python、pandas、NumPy、现有 common.stats、pytest、版本化 JSON/Markdown；没有新模型训练服务。

**Status（2026-09-07）：** F1/F4/F5/F6/F7/F8 工程仪器已交付；预注册现会强制校验引擎、代码、
输入 manifest、测试区间、证据/成本模式和成熟政策。W3 v1 因人口与伪 p 值失效，已由独立 v2
纠偏重算取代。F2/F3 模板仍按冻结裁决延期；“样本成熟”与“优势成立”仍不等于工程完成。

**前置裁决（索引 §9 Q-F、Q-冻结）：** ① **敏感尺**：主尺 `gap_c1_o2` 只用于决策类结论；`fwd_5_oc`/`fwd_10_oc` 与 `ruler.REL_MARKET`/`REL_SECTOR` 并报、**永不进 BUY**（08-21 已证低位转强在周级尺翻正而隔夜尺为负；只用主尺，F4 只会第三次推出「判断层隔夜负」）。② **模板改动（F2/F3）是 B 类**：lite-playbook 是 scan L4 的模板，受 08-26 A0 冻结（09-中攒 20 结果日）约束，且与 08-31 稿 D4 卡 v5 合并成**一次人批**。③ **不新造仪器**：F4 读 08-28 G3 已上线的 `scan/populations.py` 产物（`_ledger/populations/<run>.parquet` 正交布尔 + `views/stage_rulers.csv`，已接 prelude `ledger_views` 步与 nightly_close）；F5 把 `overseas_event_census.moving_block_diff` 下沉到 `common/stats.py`，不另写一套循环块。

**接口（Consumes / Produces）：**

| 方向 | 内容 | 精确形状 |
|---|---|---|
| Consumes（A） | `common/stats.day_equal_bootstrap(frame, value_col, ...)`、`bh_fdr(pvalues, alpha)`、`maturity_verdict(*, scan_days, subgroup_n, ...)` | 原样调用，不复制 |
| Consumes（populations） | 一行一候选 | `FLAG_COLUMNS = in_l1, in_l2, pass1_kept, l3_judged, is_finalist, is_bench, l4_dispatched, l4_rejected, e6_candidate, e6_eligible, is_buy`（`UNKNOWN ≠ False`）；`UNIVERSE_RULERS = (gap_c1_o2, fwd_5_oc, fwd_10_oc)`；`RULER_BLOCK = {1, 5, 10}` |
| Consumes（C） | 每样本 | `evidence_mode`、`fill_rule_version`、`cost_model_version`、`net_return_realized`（null 保留） |
| Consumes（B） | 事实质量 | `verification_metrics()` 四率 |
| Produces | 实验目录 | `reports_<engine>/research/stage_value/<experiment_id>/{spec, input_manifest, coverage, statistics, manifest}.json + daily_delta.csv + readout.md`（根登记见索引 Q-R） |

**Design:** [主设计 §5.2/§10](../specs/2026-09-06-research-reliability-and-system-evolution-design.md#10-工作包-f股票研究方法演进) · [全阶段索引](2026-09-06-research-system-implementation-index.md)

## 0. 文件与研究范围

| 文件 | 职责 |
|---|---|
| autoresearch/contracts/research_experiment.py | 实验身份、估计对象、分割与假设声明 |
| autoresearch/research/experiment_io.py | 方案冻结、输入身份、独立输出 |
| autoresearch/research/stage_value.py | 读 populations parquet / stage_rulers 的同人口阶段增量估计与缺失覆盖；**不另建 candidate_audit 表** |
| autoresearch/common/stats.py | `moving_block_diff` 从 overseas_event_census 下沉 + 单序列 `block_mean_ci`（共用一个抽块函数） |
| autoresearch/research/robustness.py | 标签重叠清除、embargo、walk-forward 分割；不含第二套 block bootstrap |
| autoresearch/research/probability_eval.py | 概率事件核对、Brier 与可靠性分组 |
| .claude/skills/stock-research/engine-playbook.md | full 的经营机制与可证伪论点 |
| .claude/skills/stock-research/lite-playbook.md | lite 的隔夜时点/传导/入场反证 |
| tests/research/test_experiment_io.py | 冻结、跨引擎和覆盖拒绝 |
| tests/research/test_stage_value.py | 配对人口、缺结果、零 BUY |
| tests/common/test_block_bootstrap.py、tests/research/test_robustness.py | 下沉 parity（同对象 + golden 逐位）、单序列区间、切分和多重比较 |
| tests/research/test_probability_eval.py | 缺失、极端概率、事件混用拒绝 |
| docs/research/2026-09-06-research-evaluation-protocol.md | 实施时新增的人工可读实验协议 |

三类评价分别输出，不使用一个“综合分”将事实错误与偶然盈利抵消：

| 能力 | 主要读数 | 不能替代它的东西 |
|---|---|---|
| 研究事实 | B 的证据支持、反证覆盖、未知与来源缺失率 | 单次涨跌、三角色一致 |
| 选择增量 | 同日同人口的排序、相对收益、避雷/错杀代价 | 多出 Buy、漂亮叙事 |
| 执行经济性 | C 的成本后收益、尾部、未成交、占用与窗口违约 | 理论开收盘收益、相对排名 |

## Task F1：先冻结实验契约，再看结果

- [ ] **Step 1：新增研究方案结构；必须包含以下字段。**

~~~python
MAIN_RULER = "gap_c1_o2"          # 字面量:contracts 不导入 common;与 common/ruler.MAIN_RULER 同值,由 tests 锁死
SENSITIVITY_RULERS = frozenset({"fwd_5_oc", "fwd_10_oc", "rel_gap_market", "rel_gap_sector"})

REQUIRED_SPEC = {
    "schema_version", "experiment_id", "engine", "created_at", "code_sha",
    "prompt_hashes", "input_manifest_hash", "experiment_family", "hypotheses",
    "population_rule", "selection_rule", "ruler", "sensitivity_rulers", "return_unit",
    "baseline", "evidence_mode", "weighting", "cost_model_version", "split",
    "purge_rule", "embargo_sessions", "bootstrap", "multiplicity",
    "maturity_policy", "quality_constraints", "stop_rule",
}

def validate_spec(value):
    if set(value) != REQUIRED_SPEC or value["schema_version"] != 1:
        raise ValueError("invalid experiment spec")
    if value["ruler"] != MAIN_RULER or value["return_unit"] != "fraction":
        raise ValueError("wrong overnight label or unit")
    if not isinstance(value["sensitivity_rulers"], list) or not set(value["sensitivity_rulers"]) <= SENSITIVITY_RULERS:
        raise ValueError("unregistered sensitivity ruler")
    if value["weighting"] != "day_equal":
        raise ValueError("stage comparison requires declared day-equal weighting")
    if not value["hypotheses"] or not value["experiment_family"]:
        raise ValueError("hypothesis family must be registered")
    if type(value["embargo_sessions"]) is not int or value["embargo_sessions"] < 0:
        raise ValueError("invalid embargo")
    return value
~~~

主尺只服务决策类结论；`sensitivity_rulers` 可为空列表，非空时每把尺与主尺并列输出，F8 结论章节禁止用敏感尺给决策背书（Q-F）。相邻分析日在 fwd_5/10 上窗口重叠，`scan/populations.RULER_BLOCK` 已声明块长 {1, 5, 10}，F5 沿用。字面量漂移锁（contracts 不能 import common，所以靠测试而不是 import 保同值）：

~~~python
from autoresearch.common import ruler
from autoresearch.scan import populations
from autoresearch.contracts import research_experiment as rx

def test_ruler_literals_do_not_drift():
    assert rx.MAIN_RULER == ruler.MAIN_RULER
    assert {ruler.REL_MARKET, ruler.REL_SECTOR} <= rx.SENSITIVITY_RULERS
    assert set(populations.UNIVERSE_RULERS) - {ruler.MAIN_RULER} <= rx.SENSITIVITY_RULERS
~~~

hypotheses 每项含机制、预期方向、可得时点、候选指标定义、适用人口、标签、拒绝条件。split 写明确日期段，不能只写“前后各半”。stop_rule 固定样本终点/成熟政策，不在收益好看时提前停止并继续叫固定方案。

- [ ] **Step 2：为排他写入和 spec hash 增加测试；先确认新模块缺失导致 RED。**

~~~python
import pytest
from autoresearch.research.experiment_io import freeze_spec

def test_spec_cannot_be_silently_replaced(tmp_path):
    spec = {"experiment_id": "fixture", "hypothesis": "fixed before results"}
    path, digest = freeze_spec(tmp_path, spec)
    assert len(digest) == 64 and path.exists()
    with pytest.raises(FileExistsError):
        freeze_spec(tmp_path, dict(spec, hypothesis="changed after results"))
~~~

- [ ] **Step 3：实现排他冻结函数；验证完整 spec 在上层入口执行。**

~~~python
from pathlib import Path
from autoresearch.common.atomic import canonical_json, sha256_bytes

def freeze_spec(directory, spec):
    path = Path(directory) / "spec.json"
    payload = canonical_json(spec).encode("utf-8")
    with path.open("xb") as stream:
        stream.write(payload)
        stream.flush()
    return path, sha256_bytes(payload)
~~~

实验目录先用 exist_ok=False 创建。冻结失败/中断不继续评价；失败目录保留状态清单，新实验换 ID，不提供自动 --force。

- [ ] **Step 4：输入 manifest 绑定每个 run 的代码/prompt、数据日、source cutoff、卡片版本、统计版本、填单模式。只读本引擎白名单路径，目录枚举不跨引擎。**

文件 hash 证明输入身份，不证明输入在当时可得；另检查 source first_seen/received≤decision cutoff。历史重跑的 LLM 结果标 hindsight_replay，不与 frozen_forward 的前向判断合并。

## Task F2：full 研究的经营机制与反证模板

**冻结（Q-冻结）：** engine-playbook 只服务 stock-research full，不进 scan；但同属模板改动，与 F3 同批人批、`prompt_hashes` 留痕。

- [ ] **Step 1：在 engine-playbook.md 的既有“决策主线/证据附录”结构内增加以下段落，不再增加平行报告骨架。**

> 每条核心论点用“经营变量→利润/现金流→市场预期差→验证事件”表达。明确已知事实、分析推断和未知信息；事实关联 observation/claim，推断列假设。长期公司质量不能直接推导下一个隔夜窗口上涨。

| thesis_id | 经营驱动与机制 | 市场已预期什么/证据 | 可观察指标及来源 | 验证日期 | 反证条件 | 未知信息 |
|---|---|---|---|---|---|---|
| 每条独立编号 | 价格/销量/订单/利用率如何进入利润和现金流 | 无可靠预期数据则写未知 | 指标定义、口径、来源 ID | 日/区间及精度 | 能否否决机制的具体条件 | 不填成乐观假设 |

- [ ] **Step 2：加入行业方法路由表；用于组织事实，不是行业买卖方向。**

| 行业经济关系 | 重点变量 | 典型核对与反证 |
|---|---|---|
| 周期/资源 | 供需、库存、价格与单位成本 | 名义价格上涨但价差/现金流未改善 |
| 制造 | 订单兑现、产能、交付、应收和库存 | 收入增长但回款/周转恶化 |
| 消费 | 量价、渠道库存、复购、促销和现金转换 | 出货增长主要来自压货或补贴 |
| 金融 | 资产质量、负债成本、期限与资本约束 | 收益改善依赖风险迁延或非经常项 |
| 科技/软件 | 订单质量、续费、单位经济和研发转化 | 指标增长与收入确认/现金不匹配 |
| 医药 | 试验/审批/支付/商业化节点及证据等级 | 中间终点被误当最终结果或收入 |

- [ ] **Step 3：创建人工评审 fixture：有机制但无指标、指标有来源但时间晚于决策、目标价缺估值假设、反证不能证伪、财报重述四类问题均应显式暴露。**
- [ ] **Step 4：将模板改动作为研究行为变更单独提交；在固定材料上比较事实支持与反证覆盖，不以字数增长验收。**

卖方目标价只作为市场预期参照；行业适用性、报表口径、公告可得时点由研究者核对。A 股 full 与海外公司各用适用披露来源，不将 A 股数据合同机械套用所有市场。

## Task F3：lite 研究的隔夜传导契约

**冻结与合并（Q-冻结）：** lite-playbook 是 scan L4 的模板，改动属 B 类，等 08-26 A0 冻结解除；与 08-31 稿 D4 卡 v5（基率行 / 情绪地形 / 题材位 / 接力盘一问 / 检索密度置信门 / 日期腿）合并成**一次人批**，不分两次改 def。本节第 5 问已由 lite-playbook 现有『T+2 开盘应对预案（低开 = 卖不出必须先写）』覆盖——引用不重写；第 1–4 问是新增。

- [ ] **Step 1：向 lite-playbook.md 追加以下必答问题；保持现有三门、P0–P5、持仓强制核查、隔夜 R:R 和零 BUY 规则。**

> 1. 新信息第一次可得的时点是什么？本次研究是否确实在截止前看到？
> 2. 从信息到 D+1 收盘、再到 D+2 开盘的传导是什么？为什么不是仅有长期基本面理由？
> 3. 先前价格/资金变化是否已经反映信息？“已反映”是证据还是推断？
> 4. 最强反向解释是什么？哪个入场前可观察事实会否决当前论点？
> 5. 若 D+2 开盘卖不出，暴露是什么？没有成交/盘口证据时明确未知。

- [ ] **Step 2：每条回答关联 source cutoff、hypothesis_id 和窗口；无可靠机制允许结论“无法证明隔夜优势”，不降低三门凑单。**
- [ ] **Step 3：添加行为验收材料：仅估值低、公告昨晚已出且当日已大涨、利好是附条件计划、D+2 才公开的事件、无法退出。合格回答要区分已知/推断/未知，不要求统一看空。**
- [ ] **Step 4：三情景概率只标 subjective，不从高/中/低机械映射成 80%/60%/40%；先保留定性展示，数值校准仅在 F7 的离线实验使用。**

若 D 已上线，论点写入 ResearchCard.theses；D 未上线时先使用同结构附表，不能等待 schema 上线才区分事实和假设。

## Task F4：L3/L4 阶段增量仪器

- [ ] **Step 1：输入是 `scan/populations.py` 已产的 `_ledger/populations/<run>.parquet`（一行一候选，正交布尔 `in_l2 / pass1_kept / l3_judged / is_finalist / is_bench / l4_dispatched / l4_rejected / e6_candidate / is_buy`，`UNKNOWN ≠ False`）与 `views/stage_rulers.csv`；F4 只加 `population_hash` 校验、阶段对的选择规则与本节估计，**不另建审计表**。阶段对：菜单→L3 = `in_l2` vs `is_finalist`；L3→L4 = `is_finalist` vs `is_finalist ∧ ¬l4_rejected`；L4→E6 = `e6_candidate` vs `is_buy`。`UNKNOWN` 的行进 coverage，不当 False。**

明确比较：菜单→L3、L3→L4、L4→情报纠错、纠错→复核。相同代码重复出现在不同 run 时按预登记 run 选择规则去重，不能事后选表现更好的一次。

- [ ] **Step 2：新增下列阶段配对测试。**

~~~python
import pandas as pd
from autoresearch.research.stage_value import paired_daily_selection

def test_selection_delta_uses_the_same_days_candidate_population():
    frame = pd.DataFrame([
        {"date": "20260901", "code": "600000", "baseline": True, "refined": True, "value": .02},
        {"date": "20260901", "code": "600001", "baseline": True, "refined": False, "value": -.02},
    ])
    result = paired_daily_selection(frame)
    assert result.iloc[0]["delta"] == .02
    assert result.iloc[0]["status"] == "COMPLETE"

def test_missing_rejected_outcome_does_not_become_zero():
    frame = pd.DataFrame([
        {"date": "20260901", "code": "600000", "baseline": True, "refined": True, "value": .02},
        {"date": "20260901", "code": "600001", "baseline": True, "refined": False, "value": None},
    ])
    result = paired_daily_selection(frame)
    assert result.iloc[0]["status"] == "INCOMPLETE_OUTCOMES"
    assert pd.isna(result.iloc[0]["delta"])
~~~

- [ ] **Step 3：实现逐日共同人口比较，不跨日错配。**

~~~python
import numpy as np
import pandas as pd

def paired_daily_selection(frame):
    required = {"date", "code", "baseline", "refined", "value"}
    if required - set(frame.columns) or frame.duplicated(["date", "code"]).any():
        raise ValueError("missing columns or duplicate candidate")
    if frame[["date", "code"]].isna().any().any():
        raise ValueError("candidate identity required")
    for col in ("baseline", "refined"):
        if frame[col].isna().any() or not frame[col].map(lambda x: isinstance(x, (bool, np.bool_))).all():
            raise ValueError("selection must be explicit boolean")
    rows = []
    for day, group in frame.groupby("date", sort=True):
        values = pd.to_numeric(group["value"], errors="coerce")
        if np.isinf(values.to_numpy(dtype=float)).any():
            raise ValueError("infinite return")
        baseline, refined = group["baseline"], group["refined"]
        needed = baseline | refined
        missing = int(values[needed].isna().sum())
        status = ("INCOMPLETE_OUTCOMES" if missing
                  else "EMPTY_SELECTION" if not baseline.any() or not refined.any()
                  else "COMPLETE")
        left = values[baseline].mean() if status == "COMPLETE" else None
        right = values[refined].mean() if status == "COMPLETE" else None
        rows.append({"date": day, "baseline": left, "refined": right,
                     "delta": right - left if status == "COMPLETE" else None,
                     "status": status, "n_baseline": int(baseline.sum()),
                     "n_refined": int(refined.sum()), "missing_outcomes": missing})
    return pd.DataFrame(rows)
~~~

只有通过同 population_hash、ruler、cost/evidence_mode 检查的表才传入本函数。这里比较预登记集合的等权收益，不声称不同股票逐票存在完全反事实配对。EMPTY_SELECTION 的收益未知，不能将零 BUY 写成策略失败；“现金收益=0”的比较需单独定义资金分配规则，不更改正式 BUY 语义。

- [ ] **Step 4：在 COMPLETE 日序列上复用 common.stats.day_equal_bootstrap(delta)；原 paired_delta_interval 对多行日仍是事件行等权，不直接拿它替代本包日等权。**
- [ ] **Step 5：增加市场/行业超额、排序 Spearman、被否决票尾部损失和机会损失；每项保存人口、行业分类的历史版本和基准定义。中位数与均值基准不能静默替换。**

行业比较使用当时行业分类，不拿今天的存续公司名单回填历史；退市、停牌、卖不出样本不得因难算就消失。无法获得收益/分类者留 coverage，不虚构标签。

- [ ] **Step 6：若旧数据缺被筛掉股票的 L4 卡，仅评价已有选择与行情结果；不推断它们的 L4 评级。必要补充按事前固定抽样建立一次性离线实验，禁止回补生产名单。**

抽样记录 random_seed、sampling_frame_hash、sample_probability；非等概率抽样用已登记权重作总体估计，不把抽到的高风险样本直接代表所有被否决股票。

## Task F5：时间依赖稳健性，承接 A 的后续范围

- [ ] **Step 1：把 `research/overseas_event_census.moving_block_diff` 的抽块逻辑下沉到 `common/stats.py`（重叠块、起点 0..n−block 含端点、值/旗成对搬运、一侧为空作废计数——语义原样），旧模块同对象转发；再加单序列版本 `block_mean_ci` 复用同一个抽块函数。输入是按完整研究交易日历排序的日等权均值，非股票行。不要第二套循环块实现。**

~~~python
import math
import numpy as np

def block_index(rng, n, block):
    """与 moving_block_diff 同一抽块约定:重叠块、起点均匀取自 0..n-block(含端点)、拼接后截到 n。"""
    n_blocks = int(math.ceil(n / block))
    starts = rng.integers(0, n - block + 1, size=n_blocks)
    return (starts[:, None] + np.arange(block)[None, :]).ravel()[:n]

def block_mean_ci(values, *, block, seed, n_boot=2000, alpha=.05):
    x = np.asarray(values, dtype=float)
    if x.ndim != 1 or not np.isfinite(x).all():
        raise ValueError("ordered finite daily observations required")
    if type(block) is not int or block < 1:
        raise ValueError("invalid block size")
    if type(n_boot) is not int or n_boot < 1 or not 0 < alpha < 1:
        raise ValueError("invalid bootstrap parameters")
    if len(x) <= block or len(x) < 2:
        return {"point": float(x.mean()) if len(x) else None,
                "lo": None, "hi": None, "status": "INSUFFICIENT_BLOCKS"}
    rng = np.random.default_rng(seed)
    draws = np.array([x[block_index(rng, len(x), block)].mean() for _ in range(n_boot)])
    lo, hi = np.quantile(draws, [alpha / 2, 1 - alpha / 2])
    return {"point": float(x.mean()), "lo": float(lo), "hi": float(hi), "status": "COMPUTED"}
~~~

`block_mean_ci` 与下沉后的 `moving_block_diff` 共用 `block_index`，抽块约定逐位不变。下沉步骤：先在原实现上对固定输入（seed 20260829、block 5、n_boot 10_000）录 golden；再搬函数体；旧模块 `from autoresearch.common.stats import moving_block_diff` 同对象转发；parity 测试断言同对象且 golden 逐位相等。它是敏感性仪器，不替换已完成 A 的生产统计入口。不能把相隔数周的两个有扫描日拼成相邻交易日；日历缺口要分连续段或声明该试验只按观察序列重采样及局限。不补造缺失日收益。

- [ ] **Step 2：测试常数序列、固定 seed 可复现、无穷值拒绝、短样本无区间、block=1 的独立日语义，以及下沉 parity。**

~~~python
import pytest
from autoresearch.common import stats
from autoresearch.research import overseas_event_census as legacy

def test_block_bootstrap_constant_and_small_samples():
    result = stats.block_mean_ci([.01] * 40, block=5, seed=7)
    assert result["lo"] == pytest.approx(.01)
    assert result["hi"] == pytest.approx(.01)
    assert stats.block_mean_ci([.01] * 4, block=5, seed=7)["lo"] is None
    assert result == stats.block_mean_ci([.01] * 40, block=5, seed=7)

def test_moving_block_diff_is_the_same_object_after_sinking():
    assert legacy.moving_block_diff is stats.moving_block_diff
~~~

- [ ] **Step 3：预登记 block 长度敏感性集合 = `scan/populations.RULER_BLOCK` 的 1/5/10 个交易日并全部报告，禁止选择最显著的长度。跨年度/波动区间/行业的稳定性单列，同一时间样本多个切片不算独立验证。**
- [ ] **Step 4：walk-forward 用明确 train/validation/test 日期段；按每行 label_start/label_end 清除与测试标签重叠的训练行，再按交易日历执行 embargo。**

~~~python
def overlaps(left_start, left_end, right_start, right_end):
    return left_start < right_end and right_start < left_end

def purge_overlap(train, test_intervals):
    return [row for row in train if not any(
        overlaps(row["label_start"], row["label_end"], start, end)
        for start, end in test_intervals)]
~~~

时间均为已解析的带时区 datetime，窗口用左闭右开。embargo 取实际交易日历，不以两个自然日代替两个交易日。阈值、因子和成本模型在留出集上不可重调；任何重调开启新实验，旧测试集记为已见。

## Task F6：因子机制、多重比较与实验台账

- [ ] **Step 1：每个新因子登记机制、方向、计算公式、可得时点、适用股票、标签、基准、试验家族和试验次数；金融披露使用公告可得日期而非财报所属期。**

**首批登记家族 = 08-31 稿 W3 三格**：首板缩量回调低吸 / 晚封板次日溢价 / 机构席位 5–20 日。每格按 08-28 隔夜普查协议走同一仪器（`research/overnight_census`：日等权、门槛 `CI_LOWER_PP = 0.15` 绝对毛 = `COST_PP` 往返成本），过门才进 08-28 Q9 裁决树，结果只进文档。机构席位格的标签是 5–20 日，必须作为 `sensitivity_rulers`（fwd_5_oc/fwd_10_oc）登记，不能拿主尺硬套；两格涨停板族按 08-28 读数属 X_ORACLE（不可成交），先答 C 的 `close_auction_limit_v1` 成交问题再谈收益。
- [ ] **Step 2：现有 common.stats.bh_fdr 已实现 BH，不再重复实现。调用前验证 p 值，且 p 值来源必须适合时间相关/配对结构。普通 bootstrap 区间不能直接变成随意定义的 p 值。**

~~~python
import math
from autoresearch.common.stats import bh_fdr

def family_adjustment(pvalues, *, dependence, alpha=.05):
    if not 0 < alpha < 1:
        raise ValueError("invalid alpha")
    ps = [float(p) for p in pvalues]
    if any(not math.isfinite(p) or not 0 <= p <= 1 for p in ps):
        raise ValueError("invalid p-values")
    if dependence not in {"independent_or_positive", "arbitrary"}:
        raise ValueError("dependence assumption required")
    result = bh_fdr(ps, alpha=alpha)
    factor = sum(1 / i for i in range(1, len(ps) + 1)) if dependence == "arbitrary" else 1
    return [dict(row, q=min(1, row["q"] * factor),
                 rejected=min(1, row["q"] * factor) <= alpha,
                 method="BY" if dependence == "arbitrary" else "BH")
            for row in result]
~~~

该 BY 扩展用于任意依赖下更保守的家族校正；BH 的适用依赖假设需明示，不能一律称任意相关下受控。实现与 [SciPy 官方说明](https://docs.scipy.org/doc/scipy/reference/generated/scipy.stats.false_discovery_control.html) 的定义对照，不为此新增运行时 SciPy 依赖。

- [ ] **Step 3：加入已有 BH 输出保持、BY 的 q≥BH、输入顺序不变、空家族、NaN/越界拒绝的测试。**
- [ ] **Step 4：结果同时报告效应大小、区间、未调整 p、调整 q、家族大小和失败试验；保留负结果，不只归档胜者。**

大量试验后挑最好回测存在选择偏差，不能凭一个漂亮留出结果宣布消除过拟合；试验家族和重复尝试要进入结论限制。[Bailey 等：The Probability of Backtest Overfitting](https://www.davidhbailey.com/dhbpapers/backtest-prob.pdf)

不调用 factor_lab.calibrate/calibrate_regimes 写生产权重，不向 L3/L4 prompt 注入历史胜率，不恢复已退役 learning。离线研究发现只能形成可审阅的新策略提案。

## Task F7：概率、事实可信度和复核相关错误分开评价

- [ ] **Step 1：首个概率实验明确事件为“计划隔夜窗口、已声明执行模式与成本模型下，完整样本的净收益>0”；缺成交/缺费用不是 y=0，而是未标注。**
- [ ] **Step 2：实现 Brier 与固定分组读数，不能与事实支持置信度混算。**

~~~python
import math

def probability_metrics(rows, *, event_id, edges=(0, .2, .4, .6, .8, 1)):
    if len(edges) < 2 or edges[0] != 0 or edges[-1] != 1 or any(a >= b for a, b in zip(edges, edges[1:])):
        raise ValueError("invalid fixed bins")
    valid = []
    for row in rows:
        if row["event_id"] != event_id:
            raise ValueError("mixed probability targets")
        if row["p"] is None or row["y"] is None:
            continue
        p, y = row["p"], row["y"]
        if type(p) not in {int, float} or not math.isfinite(p) or not 0 <= p <= 1 or y not in (0, 1):
            raise ValueError("invalid probability or outcome")
        valid.append((p, y))
    bins = []
    for index, (low, high) in enumerate(zip(edges, edges[1:])):
        pairs = [(p, y) for p, y in valid if low <= p < high or (index == len(edges) - 2 and p == high)]
        bins.append({"low": low, "high": high, "n": len(pairs),
                     "mean_p": sum(p for p, _ in pairs) / len(pairs) if pairs else None,
                     "observed_rate": sum(y for _, y in pairs) / len(pairs) if pairs else None})
    return {"n": len(valid), "missing": len(rows) - len(valid),
            "brier": sum((p - y) ** 2 for p, y in valid) / len(valid) if valid else None,
            "bins": bins}
~~~

Brier 同时受概率区分能力和校准影响，不能仅凭分数下降声称“概率已校准”；同时展示可靠性分组、样本量和基准概率。概率校准器如需拟合，训练与评价样本必须分开。[scikit-learn 官方校准说明](https://scikit-learn.org/stable/modules/calibration.html)

- [ ] **Step 3：测试 p=0/1、空组、全部缺标签、混事件拒绝，及如下固定分数。**

~~~python
from autoresearch.research.probability_eval import probability_metrics

def test_probability_score_keeps_missing_separate():
    rows = [{"event_id": "net-positive-v1", "p": .8, "y": 1},
            {"event_id": "net-positive-v1", "p": .2, "y": 0},
            {"event_id": "net-positive-v1", "p": .9, "y": None}]
    result = probability_metrics(rows, event_id="net-positive-v1")
    assert result["brier"] == pytest.approx(.04)
    assert result["n"] == 2 and result["missing"] == 1
~~~

- [ ] **Step 4：复核实验保存 reviewer/model/prompt/input IDs、是否看到原结论、先验 rating、独立发现的反证。使用共同可标注案例计算错误重合率，不把三票一致当三份独立证据。**

~~~python
def error_overlap(first, second, labelled):
    common = set(first) & set(second) & set(labelled)
    a = {key for key in common if first[key] != labelled[key]}
    b = {key for key in common if second[key] != labelled[key]}
    union = a | b
    return {"n_common": len(common), "both_wrong": len(a & b),
            "error_jaccard": len(a & b) / len(union) if union else None}
~~~

没有任一错误时 Jaccard 记 null，不据此声称独立；共同错误可来自共享来源或模型，需保留来源维度解释。

## Task F8：读数、成熟门与研究交付

- [ ] **Step 1：CLI 接口固定为 stage_value --spec <已冻结方案> --input-manifest <输入清单>；输出由 spec.engine 对应 workspace 根解析，新建 reports_<engine>/research/stage_value/<experiment_id>/。**
- [ ] **Step 2：输出 spec.json、input_manifest.json、candidate_audit.csv、daily_delta.csv、coverage.json、statistics.json、readout.md、manifest.json；每份派生文件登记 hash，禁止写回 scan run。**
- [ ] **Step 3：报告固定章节：估计对象/人口、前向或回放属性、事实质量、选择增量、执行经济性、稳健性/多重比较、资源成本、缺失偏差、结论和明确不支持的结论。**
- [ ] **Step 4：复用 common.stats.maturity_verdict，明确哪些维度适用；样本小则 IMMATURE。区间跨零是“不确定”，不自动等于“完全无用”或“已经等价”。**
- [ ] **Step 5：A 已提供 --out-json 与拒绝覆盖能力。若复跑 overnight_census，必须同时指定两个新落点，不能仅传 --out；不再排期重复开发输出隔离。**

~~~text
python -m autoresearch.research.overnight_census
  --out reports_<engine>/research/<新实验目录>/readout.md
  --out-json reports_<engine>/research/<新实验目录>/census.json
~~~

这是执行接口说明，不在文档验收时运行昂贵的普查。路径由 workspace 按当前引擎解析，命令里不写死引擎名；落点若在 research 根，登记见索引 Q-R。

- [ ] **Step 6：运行以下测试，并确认所有写操作只发生在 tmp_path。**

~~~bash
uv run --no-sync python -m pytest -q tests/research/test_experiment_io.py tests/common/test_block_bootstrap.py tests/research/test_stage_value.py tests/research/test_robustness.py tests/research/test_probability_eval.py tests/contracts/test_layering.py
~~~

## 验收、提交与回滚

| 编号 | 完成定义 |
|---|---|
| F01 | spec 在结果前冻结；改假设必须新实验 ID |
| F02 | full 每个核心论点有指标/来源/验证日/反证，不用长期好公司替代隔夜逻辑 |
| F03 | lite 不使用未来事实、行业优选或历史胜率给本股三门加分 |
| F04 | 阶段比较同日同人口；缺反事实、零 BUY、未成交不伪造零收益 |
| F05 | 日等权、block 敏感性、标签重叠清除和多重比较均可复现 |
| F06 | 主观概率、事实支持率、净收益概率与复核错误相关性分开 |
| F07 | 产物不回注权重/prompt；旧 run 不变；质量/效率/经济性分别报告 |
| F08 | 真实前向证据不足时明确 IMMATURE，不写“策略优势已验证” |
| F09 | 敏感尺读数与主尺并列，结论章节无一处用敏感尺给决策类结论背书 |
| F10 | F4 的人口来自 populations parquet 且 `population_hash` 可追；`moving_block_diff` 下沉后 golden 逐位相等 |

提交按实验 IO、模板变更、stage_value、稳健统计、概率/复核、读数六批。模板改动与工程仪器拆提交，便于区分行为差异。仪器测试通过后仍需足够前向样本，工期估计不包括这段市场等待。

回滚为停用新离线仪器/撤回模板版本；旧研究结果保留版本，不改生产参数。增加现金择时门、改变 relative_buy、持有期或执行权限均需另行策略决策，不因本研究出现正结果自动上线。
