#!/usr/bin/env python3
"""统一相对决策层 finalizer v1(Wave12 E6-1)—— scan 路线**唯一**拥有最终 BUY 的组件。

design: Wave12 E6-1(用户 2026-08-08 六条裁定)。产物
`context/scan/<date>/_relative_buy_decision.json`。纯确定性:**零 LLM、零联网、只读结构化
产物**。

## 病灶

07-15→08-06 连续 17 个扫描日 0 买单(178 张决策卡里 Overweight = 0)。根因不是"门太严",
而是**旧系统只回答「有没有绝对强到值得买」,却被要求同时回答「今天全市场相对最值得买
谁」**——拿一套绝对门处理两个问题,必然长期 0 BUY。本模块把第二个问题单独立层:

- 对外**只有一个** `BUY` 信号(不设"质量 BUY / 游资 BUY"多套);
- 每个**成功完成**的交易日至少给出一只;
- 最低那一只是**相对 BUY**——"在今日可交易全集里相对最值得买",**不承诺绝对上涨**;
- 相对基准 = 全市场可交易等权为主(`rel_gap_market`)、行业中性超额为辅(`rel_gap_sector`),
  两列都是主尺 `MAIN_RULER`(gap_c1_o2)的超额,不是第二把尺。

## 影子 / 活体开关(v2.0 起装好,默认仍关)

`mode` 形参(默认 `MODE_SHADOW`)v2.0 起接受 `{"shadow", "active"}`——但这只是把开关
**装上**,不是把它**打开**:生产 `scan_config.jsonc` 的 `relative_buy.mode` 默认仍是
`"shadow"`,真正翻到 `"active"` 是用户逐项过完裁决表后的独立批准动作,不由本模块内部
触发。影子期本模块只写自己那一份 JSON:**不改任何生产发布行为、不写 buy ledger、不碰
publisher、不动 `decision_records.json`、不改任何 prompt**;活体真正接管仍要求 ≥20 个
真实扫描日影子 + 五守卫 + 人工批准——"默认不启用"必须连副作用一起不启用。

## v1 规则(**观察前锁定**;任何改动 = 新 `RULE_VERSION`,经影子账本呈证 + proposal 人批)

> 当前 `RULE_VERSION = "e6.v4.1"`。v1.1 只把两道硬门对"产物缺席"的静默放行堵上,
> v1.2 只把 `data_a` 第 4 判改读 `stage_results.failed_data`(gate4 的 hygiene/metering
> 类失败不再连坐当日 BUY),v2.0 只把 `mode` 形参开放接受 `"active"` + 加 `exclude_pinned`
> 过滤(生产默认仍 `shadow`/`False`)。**打分与选择语义与 v1 逐字相同**(8 日回放零变化
> 为证);下面这套规则原文因此仍然逐字有效,不需要按 v1.1/v1.2/v2.0 重读。差异见文件尾
> 「修复轮 1」与 `RULE_VERSION` 常量旁注。v4.0(2026-09-24 可买性对齐 §2.6)加 `tiering`
> 一个开关:**关(内建默认)时与 v3.0 逐字相同**,开时才有卡面入场硬门与 A/R 分级。生产
> 配置开,回滚杆是 `relative_buy.tiering=false` **加** `pool="composite"` 两个键一起回。
> v4.1(2026-09-25 指数调样事件 §2.4)加 `rebalance_gate` 一个开关:**关(内建默认)时与
> v4.0 逐字相同**,开时才有第五门 `rebalance_close` 与顶层 `index_events` 留痕(三态
> `source`:`ok`/`absent`/`error`,fix round 1 补第三态)。回滚杆就是这一个键。

边看结果边调参数 = 作弊。下面每条都是在看到任何一天的影子输出**之前**写死的。

**候选集** = 当日 L4 派发过的票(护照 `l4.dispatched`)。L2 菜单里没走到 L4 的票不是
BUY 候选,也不进 `excluded`——把 190 只 pass1 被切的票记成"被排除"是噪音,不是信息
(护照 `missing[]` 同款教训)。

**硬资格四类**(全过才 `eligible`):

1. `tradable` —— 在当日 **L0 可交易全集**内(`L1_scored_full.csv` 的行)∧ 非 ST/退 ∧ 入场旗
   为真。入场旗列名走 `ruler.entry_flag_for()` **单点**取(严禁写死 `"buyable"` 字面量,
   见 2026-08-08 final-review C1:换尺后 10 个消费点全在读旧腿)。
   ⚠️ **premise 偏差(实测)**:`ruler.entry_tradable` 的旗 `buyable_c1` 由
   `factor_lab.forward_returns` 从 **T+1 收盘**算出——决策当晚它在活体产物里根本不存在
   (实测 `L1_scored_full.csv` 无该列)。ruler 自己的语义是"列整体不存在 → 全 `default`
   (True)",所以活体日这一项等价于"在 L0 可交易全集内";回放帧带上该列时它自动生效。
   这不是替代判据,是同一函数在两种输入下的既定行为。
2. `data_a` —— 当日 A 级数据契约无未解决异常。**日级**判定(全体候选同值),结构化读
   `run_health.json`:`core_missing` 空 ∧ `run_contract.status == "OK"` ∧
   `stage_results.status == "OK"` ∧ `stage_results.failed_data` 空 ∧
   `decision_records.status == "OK"`。三个 status 一律**要求等于 `"OK"`**,不是"不等于坏
   值"——`health.py` 的真实取值域含 `"ABSENT"`(产物根本没写),而"产物缺席"时
   「A 级契约无未解决异常」这句话无从断言,放行它等于把没查过当查过了(复核 I-1:这是
   第 6 处放宽,原版未列入 premise 偏差、也未留痕)。`run_health.json` 整个缺失同样判
   **False**(与 `contracts.py`"A 级空即抛异常阻断"同向)。`failed_data` 是 `failed` 的
   data 类子集(v1.2/E1a):gate4 因文档卫生/计量对账失败不再连坐;历史 `run_health.json`
   无此键时回退读 `failed`。
3. `contract` —— 该票 slim/卡/价格断言契约完整:task-book(`_l4_tasks.json`)该票
   `status == "SUCCEEDED"` ∧ `artifacts.slim/card` 均 `PRESENT` ∧ **护照没把
   `l4.research_rating` 记进 `missing[]`**(卡在盘上却读不出终评级 = 卡契约不完整)
   ∧ 价格断言无 fail。
   ⚠️ **premise 偏差(实测)**:**逐票**价格断言状态在 `context/scan/<date>/` 里没有任何
   生产者——`price_claim_subjects.json` 是日级聚合(无 code),发布层只往
   `reports/scan/<run_id>/details/<中文名>.md` 追一行文字。故本模块读一份 presence-gated 的
   可选产物 `_price_claim_status.json`(`{code: {"n_claims", "n_mismatch"}}`);缺席 →
   `UNMEASURED`,**不当 fail 用**(缺证据不等于有罪),并在 `inputs` 留痕。
4. `no_redflag` v1 判定 = 非 ST/退 ∧ `research_rating != "Sell"` ∧ 早停原因 ∉
   `REDFLAG_EARLY_STOP_REASONS` ∧ 当日成交额分位 ≥ P10(**L0 可交易全集内**)。
   ⚠️ **premise 偏差(实测,2026-08-31 已修 · D8.3 ②)**:早停停因是七选一的机读词表
   (`l4/parsers.py`:数据不足/涨停追高/题材透支/资金流出/估值透支/基本面恶化/其他),
   曾经写死的 "监管/审计红灯" 不在这七选一里,规则逐字实现下永不命中——纯死码,已删除
   (行为零变;见文件尾「v1 已知问题」#1)。

**四个面**(各自转**当日候选内**的中位分位,等权 Borda 平均):

- `target_align` = composite 分当日百分位(护照 `recall.composite_pctl`);
- `recall_strength` = 0.5×pctl(`n_channels`) + 0.5×max(逐路分位);
- `evidence` = 满卡 1.0 / 早停 0.4 基础分 + intel 在场 +0.2 + 档案在场 +0.2 +
  price_claim 干净 +0.2,截到 [0,1];
- `risk_safety` = 1 − 归一风险分(risk_flags 计数 + 早停风险类 + tripwire 严重度)。

**「面内缺失」的 v1 口径**(规则原文只说"面内缺失 = 该面 0.5 并记 `missing`",这里把
"缺失"钉死,免得两个人读出两种意思):该面的**全部**输入都取不到 → 面 = 0.5 并记
`faces_missing`,且该票**不进这一面的分位分母**(否则一个盲票会把别人的分位一起挪走);
只缺部分分量 → 用可得分量,权重在可得分量间重新归一。

**出单**:`eligible` 最高分 = 第 1 只 `BUY(basis=relative)`。并列决胜:`target_align` →
流动性(当日成交额)→ 代码字典序。

**第 2 只起**:`relative_decision_score` 达已验证阈值 ∧ 同 bucket×regime 的 OOS 历史绝对
gap 扣成本为正 —— **v1 影子期无已验证阈值(`SECOND_BUY_THRESHOLD is None`),该门恒不
满足,第 2 只恒不出**。这是设计不是 bug,已被测试钉死。

**`expected_abs_gap`**:同 score bucket×regime 的 OOS 历史均值/CI;样本 <
`EXPECTED_ABS_GAP_MIN_N`(20)→ `UNMEASURED`。**v1 影子期恒 `UNMEASURED`,禁止拍数**,
同样被测试钉死。

**BLOCKED**:全部候选被硬资格否决(或当日无候选)→ `blocked=true` + 分桶
`blocked_reasons`。影子期**只记录**,不影响生产发布。

## 确定性

同输入重复构建 byte 稳定:不写时间戳、不写随机序,所有由数据决定的集合显式排序,落盘走
`sort_keys=True`,浮点一律 `round(…, 6)`。A 股代码一律 stdlib csv 读字符串 + `zfill(6)`
(pandas 会把 `001283` 读成 `1283`)。

  uv run --no-sync python -m autoresearch.scan.relative_buy <date>

(原 `preflight` verb〔E6 转正前体检〕于 2026-08-21 随前向观测账本 `relative_ledger` 一并
 删除 —— 它读的是那本前向观测账本,闭环退役后没有数据源。E6 本身不受影响:BUY 决策的
 写者/校验者 `write_decision`/`verify_decision` 从不依赖账本。)
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import re
import sys
from bisect import bisect_left, bisect_right
from pathlib import Path

from autoresearch.common import workspace as ws
from autoresearch.common.ruler import MAIN_RULER, REL_MARKET, REL_SECTOR, entry_flag_for
from autoresearch.contracts.agent_output import (
    EXEC_LINE_MAX_PCT_1D,
    EXEC_LINE_MAX_POS_IN_RANGE,
)
from autoresearch.scan.l4.parsers import parse_card_context
from autoresearch.scan.passport import build_passport
from autoresearch.trace import blobs as trace_blobs
from autoresearch.trace.identity import redact_residual_secrets

#: schema 2(2026-09-12 scene-reconstruction 设计 §7.2,Task 7)= schema 1 + 纯**观测性**
#: 新增:候选 `card_context`(卡面原文的保守解析,见 `l4/parsers.parse_card_context`)、
#: `observation_rank`(= 旧 `rank` 的显式别名,供 `selection`/`why` 点名引用,不改 `rank`
#: 本身的取值);顶层新增 `selection`(实际池/实际顺序/排序依据/池内名次)、
#: `veto_accounting`(否决股票去重计数 + 逐门命中数)、`field_usage`(字段真实角色表)、
#: `conflicts`(选中票卡面与选择之间的展示性冲突)、`why`(固定渲染的人读解释)。
#: **schema 1 的每一个键与取值逐字不变**——这是本次升版的唯一契约:旧字段不动,只加
#: 新字段;不新增硬门,不改变任何一天的 `buys`/`blocked`/`rank`/`relative_decision_score`
#: (golden 投影测试锁定,见 `tests/scan/test_relative_buy.py`)。
SCHEMA_VERSION = 2
RULE_VERSION = "e6.v4.1"
# v4.1 = v4.0 + `rebalance_gate` 开关(2026-09-25 指数调样事件 §2.4):开 → 第五门 `rebalance_close`
# (扫描日 = 调样生效前夜的调样票否决,调入调出皆算)+ 顶层 `index_events` 块;关 → v4.0 逐字(除本字符串)。
# v4.0 = v3.0 + `tiering` 开关(2026-09-24 可买性对齐 §2.6):开 → ①卡面入场=禁止进 no_redflag 硬门;
# ②BUY 分 A 级(卡面允许入场)/R 级(其余 eligible,裁定①「成功日 ≥1 只」的强制相对)。
# 关(默认)→ v3.0 逐字(golden `test_tiering_off_is_v3_verbatim_even_with_prohibited_card`)。
# 票级 data_a(批 0)不受开关控制:那是缺陷修,不是规则。
# v1.1 = v1 + 两道硬门的 ABSENT 收紧(`data_a` 三个 status 一律要求 `== "OK"`;`contract`
# 消费护照 `missing["l4.research_rating"]`)。**打分与选择语义与 v1 逐字相同** —— 四面算法 /
# Borda 等权平均 / 并列决胜三级 / 第 2 只的门 / `expected_abs_gap` 一个字符未动。
# 升版是**治理留痕**,不是作废重来:收紧的性质是堵住一处非预期放宽(产物缺席被静默当作
# 通过),属恢复本意;8 日回放(2026-07-28..08-06)**零变化** —— 选票/分数/四面/合格数逐日
# 全同 —— 即"已有观测未被污染、不需作废重跑"的实证(见 task-23-report 修复轮 §0.2)。
# 2026-08-09 控制方裁定。
# v1.2 = v1.1 + data_a 第 4 判改读 `stage_results.failed_data`(E1a,2026-08-18 设计稿 §3):
# 文档卫生(产物形状·*)与计量病(usage_reconcile·*)类 gate4 失败不再连坐当日 BUY;
# 数据类失败照旧团灭。历史 run_health 无 failed_data 键 → 回退旧口径,历史判定不改写。
# 打分/选择语义零改动。
# v2.0 = v1.2 + E6 转正瘦身波 task-2.2(2026-08-19):`build_decision` 的 `mode` 形参开放
# 接受 `MODE_ACTIVE`(生产 `scan_config.jsonc` 的 `relative_buy.mode` 默认仍是 `shadow`,
# 真正翻至 `active` 是用户裁决表批准后的独立动作,本次只是把开关**装上**);新增
# `exclude_pinned` 形参 + 输出顶层键(`True` 时 BUY 只在非📌 eligible 内选,被跳过的
# 📌 eligible 票记入 `excluded`[reason=pinned_holding],仍留在候选表与排名内,`rank`
# 字段不因排除而重排)。**打分算法 / 四面 Borda 等权平均 / 并列决胜三级 / 第 2 只的门 /
# `expected_abs_gap` 逐字未动**——升版理由是形参/输出契约扩容,不是规则改写。
DECISION_FILENAME = "_relative_buy_decision.json"
#: P0-2(`docs/research/2026-08-19-decision-file-two-writers-and-taskbook-hash.md` §4)—— 并排
#: 证据侧车:writer-2(`post_run observe`)现算与盘上不一致时,两份的关键字段 + sha256 落这里,
#: 供人核对「两次现算为什么不一样」;它不影响任何门,纯留痕。
MISMATCH_FILENAME = "_relative_buy_decision.mismatch.json"
#: 同一天两次现算给出不同答案 = 数据不可信,必须落 data 类(不带 `产物形状·`/
#: `usage_reconcile·` 前缀 —— 那两个在 `common.failclass.EXEMPT_PREFIXES` 里被判
#: hygiene/metering,会被豁免出 data 类;这条要连坐当日 `data_a`)。
MISMATCH_CHECK_NAME = "相对BUY决策文件·两次现算不一致"
MODE_SHADOW = "shadow"
#: v2.0 起 `build_decision`/`write_decision`/`verify_decision` 接受的第二个合法 `mode`
#: 值——装开关不是翻开关,生产 config 默认仍 `MODE_SHADOW`(见文件头「影子 / 活体开关」)。
MODE_ACTIVE = "active"

# ── v1 锁定的常量(改这里 = 改规则 = 必须换 RULE_VERSION 并走 registry)────────
#: 第 2 只 BUY 的已验证阈值。影子期**没有**——所以第 2 只恒不出。
SECOND_BUY_THRESHOLD: float | None = None
SECOND_BUY_BLOCK_REASON = "v1 影子期无已验证阈值"
#: `expected_abs_gap` 需要的最小 OOS 样本数;不足 → UNMEASURED,禁止拍数。
EXPECTED_ABS_GAP_MIN_N = 20
#: 成交额分位下限(L0 可交易全集内)。
LIQUIDITY_PCTL_FLOOR = 0.10
#: 硬门 ④ 的红灯停因。**v3.0 扩集**(2026-08-26 §3 路A 的 A2):加 {估值透支, 涨停追高, 数据不足}。
#: 判据是**产品一致性不是统计**:E6 出的 BUY 不能与同一张卡自己写的结论打架。留在集合外的
#: 是 {其他, 题材透支, 资金流出} —— 这三档在隔夜尺上对 Hold/UW 无区分力(edge 普查:
#: L4·Hold −0.20 vs UW −0.35,差异不显著;「主力真在」门 PASS−FAIL 甚至反向 −0.22pp),
#: 把它们也当红灯就是拿没有证据的判断去否决有证据的候选。
#: D8.3 ②:曾含 "监管/审计红灯"——它不在 `l4/parsers._STOP_REASONS`(= `contracts.
#: agent_output.STOP_REASONS`)的七词表内,`parse_early_stop` 把闭集外的自由文本一律
#: 折成"其他",这一支逐字实现、永不命中,是纯死码,已删除(行为零变;ratchet 见
#: `tests/scan/test_early_stop_parse.py::test_redflag_reasons_subset_of_closed_set`)。
REDFLAG_EARLY_STOP_REASONS = frozenset({
    "基本面恶化", "估值透支", "涨停追高", "数据不足",
})
#: v3.0 硬门 ④ 否决的评级档(A2)。UW/Sell 卡 + `FINAL TRANSACTION PROPOSAL: SELL` 都不得当 BUY。
#: 实测背景:2026-08-20 金螳螂、2026-08-25 天味食品**两次**把提议 SELL 的 UW 卡发成当日 BUY
#: (v1 已知问题 #6 原话:「提议卖出的票可以当相对 BUY 出这条通路是敞开的」)。
REDFLAG_RATINGS = frozenset({"Sell", "Underweight"})
REDFLAG_PROPOSALS = frozenset({"SELL"})
#: BUY 候选池来源(v3.0)。`POOL_FINALISTS` = v2 的**候选池**(判断层 finalist 全体);
#: `POOL_COMPOSITE` = 只在「证据席」(L3 守卫⑨ `composite_seat`)里选、且按 composite 分排。
#:
#: ⚠️ **回滚杆的边界要说清**(实施时发现设计稿 §5 那句"= v2 逐字"不准确):
#: `scan_config.relative_buy.pool="finalists"` 只回滚**候选池与排序**,**不回滚上面那两条
#: 硬门扩集**(A2 的 UW/SELL 否决对两个池都生效 —— 它是产品一致性要求:BUY 不能与卡面
#: 结论打架,和"从哪个池里选"是两件事)。要连硬门一起回滚,得把 `REDFLAG_RATINGS` 与
#: `REDFLAG_EARLY_STOP_REASONS` 改回 v2 的取值(各一行),那是一次显式的代码改动,不是配置。
POOL_FINALISTS = "finalists"
POOL_COMPOSITE = "composite"
#: `risk_safety` 里算作"风险类"的停因 = 七词表减去 {数据不足, 其他}(那两个是"缺证据/
#: 未归类",不是实质负面发现,且已由 evidence 面的早停基础分反映)。
RISK_EARLY_STOP_REASONS = frozenset({
    "涨停追高", "题材透支", "资金流出", "估值透支", "基本面恶化",
})
#: ST / 退市标记(镜像 `factor_lab.py` 与 `akshare_universe.py` 的同一判据)。
_ST_MARKS = ("ST", "退")

_HARD_GATES = ("tradable", "data_a", "contract", "no_redflag")
#: 第五门(2026-09-25 指数调样事件 §2.4;design F1):扫描日 = 调样生效前夜的调样票不得成为 BUY ——
#: 买 E 收盘 = 和被动资金在同一个收盘价买入,17 次调样隔夜尺 −0.31pp(中证500 −0.72pp、胜率 22%)。
#: **只在旋钮 `relative_buy.rebalance_gate` 开时存在**(镜像 `tiering`),关 = 四门逐字;
#: 独立计数、不并入 no_redflag(「缺席≠否」:命中数要单独可读,见 09-25 五连撞记忆)。
REBALANCE_GATE = "rebalance_close"


def _hard_gates(rebalance_gate: bool) -> tuple[str, ...]:
    """当次运行的硬门元组:旋钮关 = `_HARD_GATES` 逐字;开 = 追加第五门。所有「按门遍历」的地方读它,不读常量。"""
    return _HARD_GATES + (REBALANCE_GATE,) if rebalance_gate else _HARD_GATES


_FACES = ("target_align", "recall_strength", "evidence", "risk_safety")
_TRUTHY = {"true", "1", "yes", "是"}
_FALSY = {"false", "0", "no", "否"}

#: card_context 缺卡 / 未归档时的 `source` 占位(spec §7.2「实际选择依据」+ controller
#: 裁定:`source` 由本文件在 write_decision 的 I/O 边界产出,不是 `parse_card_context`
#: 的职责)。
_ABSENT_CARD_SOURCE = {
    "relative_path": None, "card_sha256": None,
    "snapshot_quality": "unarchived", "blob_digest": None,
    "archive_error": None, "post_hoc": False,
}

#: fix round 1(review "ALSO"):`write_decision`/`verify_decision` 只读**当日**活体卡
#: ——对它们而言"今天"就是唯一在场的版本,今天的常量就是今天生效的契约,不是拿今天的
#: 值去判"历史"卡漂移(spec §7.1 的禁令针对的是后者:替历史卡编一个它当时未必适用的
#: 契约)。因此这里传真值,让 `contract_match` 在活体卡上真正可读——数字唯一真身仍是
#: `contracts.agent_output`,这里只导入用、不重抄字面量(防三份手写拷贝那种漂移,同
#: agent_output.py 里 `EXEC_LINE_MAX_PCT_1D` 旁注点名的病)。**不带 `version` 键**——
#: `EXEC_LINE_MAX_PCT_1D`/`EXEC_LINE_MAX_POS_IN_RANGE` 没有独立于
#: `AGENT_OUTPUT_SCHEMA_VERSION` 的版本号,编一个会把"这两个阈值有没有变"和"整份卡片
#: 契约 schema 有没有变"两件不同的事绑在一起,阈值改了但 schema 没跟着升版时会静默
#: 失配——没有版本号比编一个错的更诚实(`contract_version` 因此在活体卡上恒为 `None`,
#: 这是 `l4/parsers._parse_exec_lines` 的既定行为,不是本次改的)。
_LIVE_EXEC_LINE_CONTRACT = {
    "EXEC_LINE_MAX_PCT_1D": EXEC_LINE_MAX_PCT_1D,
    "EXEC_LINE_MAX_POS_IN_RANGE": EXEC_LINE_MAX_POS_IN_RANGE,
}

#: `field_usage`(spec §7.2)——真实规则**导出**的字段角色表,不是第二份手写清单:
#: hard_gate/ranking 两列直接引用 `_HARD_GATES`/`_FACES`,改那两个常量这里自动跟着变,
#: 不会有人忘记同步一份影子拷贝(同 `benchmark.redflag_early_stop_reasons` 的
#: "导出词表 = 让它自己说话" 纪律)。`display_only` 列的是 `card_context` 的字段——它们
#: 只供人读 / `conflicts` 展示,任何一个都不得进 `_hard_gate`/排序。
#:
#: v4.0(2026-09-24 §2.6,controller Ruling P21)—— `card_context.entry_stance` 的真实
#: 角色随 `tiering` 开关而变:关(v3.0 逐字)时它仍是纯 display_only;开时它离开
#: display_only,真正驱动 `_hard_gate` ④ 的否决与 `build_decision` 选择段的 A/R 分级。
#: 这个字典逐字序列化进每一份决策文件(`"field_usage": ...`)——继续拿一份不随开关变的
#: 模块常量发布,就是让 tiering=True 的生产 run 在书面上否认自己刚做过的事。因此
#: `FIELD_USAGE` 不再是唯一真相,改成 `_field_usage(tiering)` 的一次调用;下面的
#: `FIELD_USAGE = _field_usage(False)` 只是保留给"v3 那份"的既有引用(golden 常量
#: import 等),生产运行按各自的 `tiering` 现选,不恒等于这个常量。
def _field_usage(tiering: bool, rebalance_gate: bool = False) -> dict:
    display_only_fields = ["card_context.ev_target", "card_context.rr", "card_context.position_raw",
                           "card_context.trigger_raw", "card_context.exec_lines",
                           "card_context.entry_stance", "card_context.no_new_position",
                           "card_context.proposal", "card_context.card_kind",
                           "card_context.entry_source"]
    if tiering:
        display_only_fields = [f for f in display_only_fields if f != "card_context.entry_stance"]
    usage = {
        "hard_gate": {
            "fields": list(_hard_gates(rebalance_gate)),
            # fix(task-12 附带修复 C):文案曾恒写"四类",与上面的 `fields` 脱钩——旋钮开时
            # `fields` 已经是五项(多 REBALANCE_GATE),文案却没跟着变,读者会读错门数。同
            # `fields` 一样按 `rebalance_gate` 条件化(不是拍一个新常量):关时这句话逐字
            # 不变(byte parity),开时说真话。做成"unconditional"(直接拼 len(fields) 之类)
            # 曾被试过又撤回——不是因为它会算错,而是它会让这行代码看起来像"永远读当次门数"
            # 从而更容易被后人删掉这个 if,悄悄丢失"关着时必须逐字不变"这条纪律本身;显式
            # 的 if/else 把这条纪律写在字面上。
            "role": ("决定 eligible;五类全过才有资格进入候选池(见 build_decision docstring)" if rebalance_gate
                     else "决定 eligible;四类全过才有资格进入候选池(见 build_decision docstring)"),
        },
        "ranking": {
            "fields": [*_FACES, "amount", "code"],
            "role": ("四面 Borda 等权平均 → relative_decision_score,驱动 rank/observation_rank;"
                    "amount/code 是并列决胜键(某天 buy_pool 实际用了哪几个键,见 "
                    "selection.sort_keys,composite 池只用 target_align 一面)"),
        },
        "display_only": {
            "fields": display_only_fields,
            "role": "仅供人读与 conflicts 展示;不参与 eligible/hard_gate/排序/BUY 选择(本任务硬约束)",
        },
        "research_rating": {
            "role": "hard_gate(no_redflag 的一部分:REDFLAG_RATINGS={Sell,Underweight} 命中即否决)",
        },
        "l4_proposal": {
            "role": ("hard_gate(no_redflag 的一部分:卡面机读 proposal 命中 REDFLAG_PROPOSALS={SELL} "
                    "即否决;取自 decision_records,不是 card_context.proposal——两者来源不同,"
                    "后者是本任务新增的原文解析,见文件尾「已知问题」)"),
        },
    }
    if tiering:
        usage["entry_stance"] = {
            "fields": ["card_context.entry_stance"],
            "role": ("v4.0 tiering=True 时脱离 display_only,真实驱动两件事:① hard_gate"
                    "(no_redflag 的一部分:entry_stance==PROHIBITED 即否决,见 _hard_gate ④);"
                    "② BUY 分级(entry_stance==ALLOWED → A 级/card_backed,其余 eligible → "
                    "R 级/relative_forced,见 build_decision 选择段)。"),
        }
    if rebalance_gate:
        usage["index_events"] = {
            "fields": ["index_events[code].phase"],
            "role": ("hard_gate(第五门 rebalance_close:该票任一行 phase==passive_close_eve 即否决,调入调出"
                     "皆算;源缺席 → 放行并在顶层 index_events.source=absent / gate_evaluated=false 留痕)"),
        }
    return usage


FIELD_USAGE = _field_usage(tiering=False)


# ── 读取原语(容忍缺文件;不容忍猜)──────────────────────────────────────────
def _rows(path: Path) -> list[dict] | None:
    if not path.exists():
        return None
    with path.open(encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


def _json_doc(path: Path) -> object | None:
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def _code(value: object) -> str:
    """`600188.SS` / `2345` / `002345` → `002345`(前导零唯一入口)。"""
    return str(value or "").strip().split(".")[0].zfill(6)


def _float(value: object) -> float | None:
    try:
        got = float(str(value).strip())
    except (TypeError, ValueError):
        return None
    return None if got != got else got


def _is_st(name: object) -> bool:
    text = str(name or "")
    return "ST" in text.upper() or "退" in text


def _entry_flag(value: object, *, default: bool = True) -> bool:
    """入场旗单元格 → bool。列缺席/单元格解析不出真值 → `default`(ruler 同款语义)。"""
    if value is None:
        return default
    text = str(value).strip().lower()
    if text in _TRUTHY:
        return True
    if text in _FALSY:
        return False
    return default


def _mid_rank_pctl(values: dict[str, float]) -> dict[str, float]:
    """{key: 原始值} → {key: 中位分位}。并列同值(公平且确定),n=1 → 0.5。

    用中位经验 CDF 而不是 `rank/n`:后者让并列票拿到不同分位,同分不同命,还会让
    "谁先被遍历到"渗进结果里——确定性就从这种地方漏掉。
    """
    n = len(values)
    if not n:
        return {}
    ordered = sorted(values.values())
    out: dict[str, float] = {}
    for key, value in values.items():
        less = bisect_left(ordered, value)
        equal = bisect_right(ordered, value) - less
        out[key] = round((less + 0.5 * equal) / n, 6)
    return out


def _blend(parts: list[tuple[float, float]]) -> float | None:
    """[(值, 权重)] → 加权均值;权重在**可得**分量间重新归一。全缺 → None。"""
    if not parts:
        return None
    total = sum(weight for _value, weight in parts)
    if total <= 0:
        return None
    return sum(value * weight for value, weight in parts) / total


# ── L0 可交易全集(**决策层**四面分位 + 流动性门的唯一分母)───────────────────
def _universe(scan: Path) -> dict:
    """`L1_scored_full.csv` → L0 可交易全集 + 排除计数 + 逐票成交额/行业。

    **这不是 `rel_gap_market` 的人口**(B-1,2026-08-09 全支终审):本函数的 `members`
    只含**过了 L0 门**的票(`L1_scored_full.csv` 的行),它是决策层自己算四面分位与 P10
    流动性门用的分母;而 `rel_gap_market` 的真分母是**全市场可交易**(`ruler.py` I-1 人口
    裁定,含漏在 L0/L1/L2 的票),在评分时另算(该腿已随 learning 层退役删除)。
    两数常年不等(2026-08-04 实测 4193 vs 5426),`relative_ledger` 明文「不可互换……引用
    时必须点名是哪一个」。产物里对应 `benchmark.market` 的 `definition` / `eval_population`
    两个字段(I-2 已拆开,勿再合并)。

    分母只取 **可交易** 票,不是 `L1_scored_full.csv` 的所有行(T22 `retro._rel_gap_cols`
    同款口径:含停牌 / 买不进的票会把"市场平均"算成不可执行的幻觉基准)。
    """
    rows = _rows(scan / "L1_scored_full.csv")
    flag_col = entry_flag_for()
    has_flag_col = bool(rows) and flag_col in (rows[0].keys() if rows else ())
    members: list[str] = []
    amount: dict[str, float | None] = {}
    industry: dict[str, str] = {}
    excluded = {"st_or_delisting": 0, "entry_flag_false": 0}
    for row in rows or []:
        code = _code(row.get("code"))
        amount[code] = _float(row.get("amount_yi"))
        industry[code] = str(row.get("industry") or "")
        if _is_st(row.get("name")):
            excluded["st_or_delisting"] += 1
            continue
        if has_flag_col and not _entry_flag(row.get(flag_col)):
            excluded["entry_flag_false"] += 1
            continue
        members.append(code)
    members = sorted(set(members))
    amounts = {code: amount[code] for code in members if amount.get(code) is not None}
    return {
        "members": members,
        "member_set": set(members),
        "amount": amount,
        "amount_pctl": _mid_rank_pctl(amounts),
        "industry": industry,
        "sectors": sorted({industry.get(code, "") for code in members} - {""}),
        "excluded": excluded,
        "entry_flag_column": flag_col,
        "entry_flag_present": has_flag_col,
        "rows_present": rows is not None,
    }


def tradable_universe(scan_dir: Path | str) -> list[str]:
    """当日 **L0** 可交易全集(排序后的 6 位代码)—— **决策层**分位/流动性门的人口。

    **不是** `rel_gap_market` 的评分人口(那是全市场可交易,见 `_universe` docstring 的
    B-1 说明);两个人口不可互换,引用时必须点名是哪一个。
    """
    return _universe(Path(scan_dir))["members"]


# ── 日级 A 级契约判定 ───────────────────────────────────────────────────────
_L4_STAGE_RE = re.compile(r"^l4_(\d{6})$")


def _data_contract_ok(scan: Path) -> tuple[bool, str, frozenset[str]]:
    """A 级数据契约:返回 (日级是否 OK, 日级原因, 票级失败码集)。

    v4.0(2026-09-24 §2.6-2):`stage_results.failed_data` 里 `l4_<code>` 形状的项是
    **单票** slim/卡失败,只否决该票;其余项仍是日级(全体连坐)。历史 `run_health` 无
    `failed_data` 键 → 保持 v1.1 旧口径(`failed` 任一项全天否决,不改写历史判定)。
    """
    empty: frozenset[str] = frozenset()
    health = _json_doc(scan / "run_health.json")
    if not isinstance(health, dict):
        return False, "run_health.json 缺失,A 级数据契约无从判定", empty
    if health.get("core_missing"):
        return False, f"core_missing={sorted(health['core_missing'])}", empty
    contract = health.get("run_contract") or {}
    if str(contract.get("status") or "") != "OK":
        return False, f"run_contract.status={contract.get('status')!r}", empty
    stages = health.get("stage_results") or {}
    if str(stages.get("status") or "") != "OK":
        return False, f"stage_results.status={stages.get('status')!r}(非 OK)", empty
    failed_data = stages.get("failed_data")
    per_ticker = empty
    if failed_data is None:            # 历史 run_health 无此键 → v1.1 旧口径(日级连坐)
        day_level = list(stages.get("failed") or [])
    else:
        per_ticker = frozenset(m.group(1) for s in failed_data
                               if (m := _L4_STAGE_RE.match(str(s))))
        day_level = [s for s in failed_data if not _L4_STAGE_RE.match(str(s))]
    if day_level:
        return False, f"stage_results.failed_data={sorted(day_level)}", per_ticker
    records = health.get("decision_records") or {}
    if str(records.get("status") or "") != "OK":
        return False, f"decision_records.status={records.get('status')!r}(非 OK)", per_ticker
    return True, "", per_ticker


# ── 逐票契约(task-book + 价格断言)─────────────────────────────────────────
def _composite_seat_codes(scan: Path) -> set[str]:
    """当日 composite 证据席的码集(v3.0 的候选池定义)。

    真身是 L3 守卫⑨ 写进 `finalists.csv` 的 `guard == "composite_seat"`(或 `lane ==
    "composite"` —— 两个标记同批写下,认哪个都行,认两个更抗一侧被改)。**不在这里重算
    composite 排序**:重算就等于第二套规则,两处迟早给出不同的席位(同族家训:同一条规则
    两处各写一套,迟早在某次改动后给出两个不同的数,而那时没人知道该信哪个)。
    文件缺席 / 列缺席 → 空集 → `pool="composite"` 那天诚实 `blocked`,不悄悄退回全体池。
    """
    rows = _rows(scan / "finalists.csv")
    if not rows:
        return set()
    out: set[str] = set()
    for row in rows:
        guard = str(row.get("guard") or "").strip()
        lane = str(row.get("lane") or "").strip()
        if guard == "composite_seat" or lane == "composite":
            out.add(_code(row.get("code") or row.get("ticker")))
    return out


def _task_book(scan: Path) -> dict | None:
    book = _json_doc(scan / "_l4_tasks.json")
    if not isinstance(book, dict) or not isinstance(book.get("tasks"), dict):
        return None
    return {_code(code): task for code, task in book["tasks"].items()
            if isinstance(task, dict)}


def _price_claim_status(scan: Path) -> dict[str, str]:
    """presence-gated:没有逐票产物就是 `UNMEASURED`,不猜、不当 fail。"""
    doc = _json_doc(scan / "_price_claim_status.json")
    if not isinstance(doc, dict):
        return {}
    out: dict[str, str] = {}
    for code, entry in doc.items():
        if isinstance(entry, dict):
            mismatch = entry.get("n_mismatch")
            out[_code(code)] = "MISMATCH" if mismatch else "CLEAN"
        elif isinstance(entry, str):
            out[_code(code)] = entry.strip().upper()
    return out


def _tripwire_severity(scan: Path) -> dict[str, int]:
    """`_tripwire_conflicts.json` → 逐票冲突条数(v1 严重度 = 命中条数)。

    ⚠️ premise 偏差:该产物**没有** severity 字段,且只覆盖保送(pinned)票。v1 拿
    `all_hits` 的条数当严重度,非保送票恒 0。
    """
    doc = _json_doc(scan / "_tripwire_conflicts.json")
    if not isinstance(doc, dict):
        return {}
    out: dict[str, int] = {}
    for code, entry in doc.items():
        hits = entry.get("all_hits") if isinstance(entry, dict) else None
        out[_code(code)] = len(hits) if isinstance(hits, list) else 0
    return out


# ── 四个面的原始分 ──────────────────────────────────────────────────────────
def _raw_faces(entry: dict, ctx: dict) -> dict[str, float | None]:
    """一只候选的四面**原始**分(尚未转当日候选内分位)。缺 → None。"""
    recall, card = entry["recall"], entry["l4"]
    code = entry["code"]

    target = recall.get("composite_pctl")

    channel_pctls = [chan.get("pctl") for chan in (recall.get("per_channel") or {}).values()
                     if chan.get("pctl") is not None]
    parts: list[tuple[float, float]] = []
    n_channels_pctl = ctx["n_channels_pctl"].get(code)
    if n_channels_pctl is not None:
        parts.append((n_channels_pctl, 0.5))
    if channel_pctls:
        parts.append((max(channel_pctls), 0.5))
    strength = _blend(parts)

    kind = card.get("card_kind")
    if kind is None:
        evidence = None
    else:
        evidence = 1.0 if kind == "full" else 0.4
        if card.get("intel_avail") == "INTEL":
            evidence += 0.2
        if code in ctx["dossier"]:
            evidence += 0.2
        if ctx["price_claim"].get(code) == "CLEAN":
            evidence += 0.2
        evidence = min(1.0, max(0.0, evidence))

    if not card.get("carded"):
        risk = None
    else:
        risk = float(len(card.get("risk_flags") or []))
        if str(card.get("earlystop_reason") or "") in RISK_EARLY_STOP_REASONS:
            risk += 1.0
        risk += float(ctx["tripwire"].get(code, 0))

    return {"target_align": target, "recall_strength": strength,
            "evidence": evidence, "risk_safety_risk": risk}


def _faces_table(entries: list[dict], ctx: dict) -> dict[str, dict]:
    """全体候选 → {code: {"faces": {...}, "missing": [...]}}(面内缺失 = 0.5 + 记账)。"""
    raws = {entry["code"]: _raw_faces(entry, ctx) for entry in entries}

    # risk 先归一(除以候选内最大风险),再取 safety = 1 − 归一风险
    risks = {code: raw["risk_safety_risk"] for code, raw in raws.items()
             if raw["risk_safety_risk"] is not None}
    worst = max(risks.values()) if risks else 0.0
    for raw in raws.values():
        risk = raw.pop("risk_safety_risk")
        raw["risk_safety"] = None if risk is None else (
            1.0 if worst <= 0 else 1.0 - risk / worst)

    out: dict[str, dict] = {code: {"faces": {}, "missing": []} for code in raws}
    for face in _FACES:
        defined = {code: raw[face] for code, raw in raws.items() if raw[face] is not None}
        pctls = _mid_rank_pctl(defined)
        for code in raws:
            if code in pctls:
                out[code]["faces"][face] = pctls[code]
            else:
                out[code]["faces"][face] = 0.5
                out[code]["missing"].append(face)
    for record in out.values():
        record["missing"].sort()
    return out


# ── 硬资格四类 ─────────────────────────────────────────────────────────────
def _hard_gate(entry: dict, ctx: dict) -> tuple[dict[str, bool], list[dict]]:
    """一只候选的当次硬门(旋钮关四门/开五门,见 `_hard_gates`)+ 失败明细(一门一行,便于
    `blocked_reasons` 分桶)。"""
    code = entry["code"]
    gates: dict[str, bool] = {}
    details: list[dict] = []

    def fail(gate: str, detail: str) -> None:
        gates[gate] = False
        details.append({"code": code, "reason": f"hard_gate.{gate}", "detail": detail})

    # ① 入场可交易
    if code not in ctx["universe"]["member_set"]:
        if code not in ctx["universe"]["amount"]:
            fail("tradable", "不在当日 L0 可交易全集(L1_scored_full.csv)")
        elif _is_st(entry.get("name")):
            fail("tradable", "ST/退市标记")
        else:
            fail("tradable", f"入场旗 {ctx['universe']['entry_flag_column']} 为假")
    else:
        gates["tradable"] = True

    # ② A 级数据契约:日级(全体)+ 票级(v4.0:`l4_<code>` 只否决该票)
    day_ok, day_reason, per_ticker = ctx["data_a"]
    if not day_ok:
        fail("data_a", day_reason)
    elif code in per_ticker:
        fail("data_a", f"stage l4_{code} 失败(票级 data_a,不连坐当日其他候选)")
    else:
        gates["data_a"] = True

    # ③ slim / 卡 / 价格断言契约
    book = ctx["task_book"]
    claim = ctx["price_claim"].get(code, "UNMEASURED")
    if book is None:
        fail("contract", "task-book(_l4_tasks.json)缺失,卡契约无从判定")
    elif code not in book:
        fail("contract", "task-book 无该票记录")
    else:
        task = book[code]
        artifacts = task.get("artifacts") or {}
        status = str(task.get("status") or "")
        missing = [name for name in ("slim", "card")
                   if str((artifacts.get(name) or {}).get("status") or "") != "PRESENT"]
        if status != "SUCCEEDED":
            fail("contract", f"task-book status={status!r}(非 SUCCEEDED)")
        elif missing:
            fail("contract", f"产物缺席:{'/'.join(missing)}")
        elif "l4.research_rating" in (entry.get("missing") or []):
            # I-1(复核轮):护照专为这件事写了 `missing.append("l4.research_rating")`
            # (`passport.py`,语义 = "到了这一站、却读不到那个字段")。卡在盘上但读不出
            # 终评级 = 卡契约**不完整**,正是本门要挡的东西。不消费它的后果已被探针实证:
            # `decision_records.json` 缺席(assemble 半途崩)时全体 rating 为 None,
            # `None != "Sell"` 让 no_redflag 也放行,于是四门全过、`blocked=false`、
            # `excluded_rows=0`,一只没有评级的票被发成当日唯一 BUY,并被 T24 的回放
            # 收进影子账本污染 ≥20 日成熟门的观测。
            fail("contract", "护照记 missing:l4.research_rating(卡在盘但读不出终评级)")
        elif claim == "MISMATCH":
            fail("contract", "价格断言与 OHLCV 不符(price_claim=MISMATCH)")
        else:
            gates["contract"] = True

    # ④ 无红灯
    rating = entry["l4"].get("research_rating")
    stop_reason = str(entry["l4"].get("earlystop_reason") or "")
    amount_pctl = ctx["universe"]["amount_pctl"].get(code)
    proposal = str(entry["l4"].get("proposal") or "").upper()
    if _is_st(entry.get("name")):
        fail("no_redflag", f"ST/退市标记:{entry.get('name')!r}")
    elif rating in REDFLAG_RATINGS:
        # v3.0(A2):v1/v2 只挡最末一档 Sell,于是 UW 卡照样能当 BUY 出 —— 08-20/08-25
        # 两次实测。BUY 与卡面结论打架比 0 BUY 更误导,这是产品一致性要求。
        fail("no_redflag", f"research_rating={rating}(v3.0 起 UW/Sell 一律否决)")
    elif proposal in REDFLAG_PROPOSALS:
        # 评级读不出来但卡自己写了 `FINAL TRANSACTION PROPOSAL: SELL` 的情形(两条独立防线:
        # 评级解析可能失手,提案行是卡的机读契约行)。
        fail("no_redflag", f"卡面提案 {proposal}")
    elif ctx.get("tiering") and ctx["card_context"].get(code, {}).get("entry_stance") == "PROHIBITED":
        # v4.0(2026-09-24 §2.6,controller Ruling P21):`tiering` 开时卡面入场=禁止直接
        # 进 no_redflag 硬门(BUY 不能与卡自己写的「禁止开仓」打架)。关(默认)时这一支
        # 不命中,PROHIBITED 只留在 `conflicts` 里当展示性冲突——v3.0 行为逐字不变。
        fail("no_redflag", "卡面入场=禁止(entry_stance=PROHIBITED;v4.0 tiering)")
    elif stop_reason in REDFLAG_EARLY_STOP_REASONS:
        fail("no_redflag", f"早停红灯停因:{stop_reason}")
    elif amount_pctl is not None and amount_pctl < LIQUIDITY_PCTL_FLOOR:
        fail("no_redflag",
             f"成交额分位 {amount_pctl:.4f} < P{int(LIQUIDITY_PCTL_FLOOR * 100)}")
    else:
        gates["no_redflag"] = True

    # ⑤ 指数调样生效前夜(2026-09-25 §2.4;只在旋钮开时存在,见 _hard_gates)
    if ctx.get("rebalance_gate"):
        events = ctx.get("index_events")
        if events is None:
            gates[REBALANCE_GATE] = True          # 源缺席/读取失败 → 放行(缺席≠否);顶层块记
            # source=absent 或 error(fix round 1 补第三态,两者共用这一支的门后果,起因不同)
        else:
            hit = next((r for r in events.get(code, []) if r.get("phase") == "passive_close_eve"), None)
            if hit is None:
                gates[REBALANCE_GATE] = True
            else:
                # fix(task-12 附带修复 B):中文调入/调出,不是原始 side 字面量"add"/"drop"——
                # calendar.py:109 的日历行已经这么翻译同一份事实,两个渲染同一件事的地方不能
                # 各说各话(一个中文一个英文字面量)。
                side_cn = "调入" if hit.get("side") == "add" else "调出"
                fail(REBALANCE_GATE, f"指数调样生效前夜:{hit.get('index_name')} {side_cn} "
                                     f"E={hit.get('eff_close_date')}(买 E 收盘 = 与被动资金同价买入)")

    return {gate: gates.get(gate, False) for gate in ctx.get("hard_gates", _HARD_GATES)}, details


# ── card_context:卡面原文一次性读齐 + 归档(spec §7.2「实际选择依据」段落的 source
# 子字段;controller 裁定归本文件所有,不是 `l4/parsers.parse_card_context` 的职责)───
#
# `build_decision` 只做**纯计算**:它从 `card_snapshot` 参数里按 code 取,取不到就是
# "卡缺失"——不自己读盘。真正的磁盘 I/O(读卡原文、算 hash、试归档)全部在
# `write_decision`/`verify_decision` 的 I/O 边界完成(`_build_card_snapshot`),两个写者
# 用**同一套**逻辑构造这份"固定卡输入",`build_decision(card_snapshot=...)` 才谈得上
# "同输入 → 同输出"的字节级 parity(spec §7.2 硬约束)。


def _read_card_texts(scan: Path) -> tuple[dict[str, dict], dict[str, dict]]:
    """`details/*.md` 全部读一遍(与 `l4.parsers.write_early_stop`/
    `parse_ratings_from_details` 同一遍历方式:glob 全部、文件名 stem 过 `_code` 归一)。
    纯读,不归档、不落盘;不按 `build_decision` 内部算出的候选集反查——那会形成"先算
    候选集才能读卡,读卡结果又要喂回候选集构造"的循环。

    返回 `(texts, unreadable)`(fix round 1,finding 3):**卡缺席与读取失败必须分开
    留痕**——glob 压根没找到这个 code 的文件(= 缺席,不是错误)与 glob 找到了文件但
    `.read_text()` 抛 `OSError`(= 这张卡在,但读不出来,是错误、且有具体原因)是两件不同
    的事,原来的 `except OSError: continue` 把两者叠成同一种"该 code 不在返回值里",
    调用方无从分辨。`texts` = `{code: {text, relative_path, card_sha256}}`(成功读到的);
    `unreadable` = `{code: {relative_path, error}}`(文件存在但读失败的,`error` 是
    `f"{异常类名}: {异常信息}"`,不吞原因)。两个字典的 code 键不相交。
    """
    base = scan / "details"
    texts: dict[str, dict] = {}
    unreadable: dict[str, dict] = {}
    if not base.is_dir():
        return texts, unreadable
    for path in sorted(base.glob("*.md")):
        code = _code(path.stem)
        try:
            text = path.read_text(encoding="utf-8")
        except OSError as exc:
            unreadable[code] = {
                "relative_path": str(path.relative_to(scan)),
                "error": f"{type(exc).__name__}: {exc}",
            }
            continue
        texts[code] = {
            "text": text,
            "relative_path": str(path.relative_to(scan)),
            "card_sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
        }
    return texts, unreadable


def _redact_card_bytes(payload: bytes) -> bytes:
    """卡面内容归档前的脱敏(spec §7.2「保存脱敏后的卡内容」)。委托给共享原语
    `trace.identity.redact_residual_secrets`——那是 `trace.capsule`(归档 transcript/
    tool-results 字节,原名 `_redact_bytes`,已在同批合并里改名并搬走)与
    `trace.transcripts.snapshot` 也在用的**同一个**实现(2026-09-13 三方合并:本文件、
    capsule.py、snapshot.py 原先各自维护一份逐字节相同的脱敏循环,三份互相漂移的风险
    正是这次合并要关掉的——见 `redact_residual_secrets` 自己的 docstring)。改动前后逐
    字节比对过(空/纯文本/中文/Bearer/私钥块/AWS 风格 key/二进制混杂字节等用例),输出
    与原来这份内联实现完全相同,不是"看起来差不多"。
    """
    return redact_residual_secrets(payload)


def _capsule_root_for(scan: Path) -> Path | None:
    """活跃 run 的 capsule 根,只用于**尝试**归档——找不到/看着不像都诚实返回 `None`
    (= 交给调用方标 `unarchived`,不是异常)。

    `scan` 在活跃 run 下是 `<run_root>/staging/<date>/`(`workspace.scan_dir` →
    `scan_root()` 在有 `AUTORESEARCH_RUN_ID` 时等于 `run_root(...)/"staging"`),capsule
    与 staging 同挂在 run 根下——`scan.parent.parent / "capsule"` 与
    `retention._lineage_path` 的**第一个**(最新)候选路径同一个约定,这里复用它,不另造
    第二套猜测规则。**不能用 `evidence.has_capsule`**:那个要求 `capsule.json` 存在,而
    `capsule.json` 只在 `finalize()` 里才写(法证 capsule 生命周期的终态),
    `write_decision` 跑在 finalize **之前**(spec §5.2 顺序:E6 校验 → 绑定/快照 → retain
    → finalize),用它做门槛会让归档在生产里恒假。改用"看起来像不像
    `capsule._create_run_layout` 建出来的那棵树"(至少有 `identity/` 子目录)——纯目录
    存在性 stat,任何异常都不上抛。
    """
    try:
        candidate = scan.parent.parent / "capsule"
        if (candidate.is_dir() and not candidate.is_symlink()
                and (candidate / "identity").is_dir()):
            return candidate
    except OSError:
        pass
    return None


def _archive_card_snapshot(scan: Path, payload: bytes) -> dict:
    """尽力而为地把(已脱敏的)卡原文归档进活跃 capsule 的内容寻址 blob 区。

    没有活跃 run、或归档过程任何一步失败 → `snapshot_quality="unarchived"`,绝不上抛
    ——归档只是留痕,不是决策输入,选择结果不能依赖它是否成功。

    `archive_error`(fix round 1,finding 4):**没有活跃 run**(`capsule is None`)与
    **试了但失败**(`put_bytes`/`_redact_card_bytes` 抛异常——不可写的 capsule、损坏的
    blob 目录、脱敏逻辑本身的 bug……)原来落地时是同一个原因不明的 `unarchived`,原来的
    `except Exception: return {...}` 吞掉了具体是哪一种。前者不是错误(没有 run 可归档
    是正常状态,`archive_error=None`);后者是真失败,必须留下可见原因——与
    `verify_decision` 的既有纪律(不一致时打一行 stderr)对齐,`archive_error` 同时进
    `source`(留痕,给读盘的人)和 stderr(留痕,给盯着日志的人),两处口径一致
    (`f"{type(exc).__name__}: {exc}"`)。
    """
    capsule = _capsule_root_for(scan)
    if capsule is None:
        return {"snapshot_quality": "unarchived", "blob_digest": None, "archive_error": None}
    try:
        digest = trace_blobs.put_bytes(capsule, _redact_card_bytes(payload))
        return {"snapshot_quality": "archived", "blob_digest": digest, "archive_error": None}
    except Exception as exc:  # noqa: BLE001 — 归档尽力而为不得阻断发布,但原因不能被吞
        reason = f"{type(exc).__name__}: {exc}"
        print(f"[relative_buy] 卡快照归档失败(snapshot_quality=unarchived,不影响选择): "
              f"{reason}", file=sys.stderr)
        return {"snapshot_quality": "unarchived", "blob_digest": None, "archive_error": reason}


def _card_source(entry: dict | None, archive: dict) -> dict:
    """`source` 子字段:相对路径 + 卡内容 hash + 归档引用 + 是否事后补充。`post_hoc`
    本任务恒 `False`——只实现"现场随 `write_decision` 同步固定快照"这一条路径;历史
    补录(旧 run 事后重建卡输入)是设计里另一批任务的范围,不在这里悄悄实现一半
    (那样会造出一个没人真正填过 `True` 的死分支,看着像支持、其实从没测过)。
    """
    if entry is None:
        return dict(_ABSENT_CARD_SOURCE)
    return {
        "relative_path": entry.get("relative_path"),
        "card_sha256": entry.get("card_sha256"),
        "snapshot_quality": archive["snapshot_quality"],
        "blob_digest": archive["blob_digest"],
        "archive_error": archive.get("archive_error"),
        "post_hoc": False,
    }


def _build_card_snapshot(scan: Path, *, reuse: dict[str, dict] | None = None) -> dict[str, dict]:
    """写者的「固定卡输入」构造:读一遍 `details/*.md`,逐码尝试归档,产出喂给
    `build_decision(card_snapshot=…)` 的字典。

    `reuse`(仅 `verify_decision` 用,取自已发布文档的 `card_context.source`):某码 fresh
    内容 hash 与 `reuse[code]` 记录的 hash 相同 → **原样复用**那份 `source`,不重新归档、
    不重算——这是两写者字节 parity 的关键:归档是否成功可能因环境(capsule 这一刻是否
    还活跃/可写)在两次调用之间改变,内容没变时绝不能让这种纯环境差异伪装成「两次现算
    不一致」(那会把每天的正常 verify 都吵成假警报)。内容真的变了 → 不复用,现算一份新
    `source`,让既有的整份文档字节比较(`verify_decision` 主体逻辑)自然把这当成不一致
    处理——不必在这里另开一条"卡变了"的专用信道。

    读取失败的 code(fix round 1,finding 3)不进这份字典的"正常"分支——它们带着
    `read_error` 标记单独放进去(不尝试归档:内容都没读到,没有字节可归档),
    `_card_context_for` 据此渲染出"读取失败"而不是"卡缺失"的 card_context。缺席
    (glob 都没找到)的 code 干脆不出现在返回值里,与之前行为一致。
    """
    texts, unreadable = _read_card_texts(scan)
    out: dict[str, dict] = {}
    for code, entry in texts.items():
        prior = (reuse or {}).get(code)
        if prior is not None and prior.get("card_sha256") == entry["card_sha256"]:
            out[code] = {"text": entry["text"], "source": dict(prior)}
            continue
        archive = _archive_card_snapshot(scan, entry["text"].encode("utf-8"))
        out[code] = {"text": entry["text"], "source": _card_source(entry, archive)}
    for code, failure in unreadable.items():
        out[code] = {
            "text": None,
            "read_error": failure,
            "source": {
                "relative_path": failure["relative_path"], "card_sha256": None,
                "snapshot_quality": "unarchived", "blob_digest": None,
                "archive_error": None, "post_hoc": False,
            },
        }
    return out


def _index_events_input(scan: Path, rebalance_gate: bool) -> tuple[dict[str, list[dict]] | None, bool]:
    """v4.1 I/O 边界(镜像 `_build_card_snapshot`):旋钮开才读 `index_events.csv`;缺文件 → `(None, False)`
    (= 源缺席,`build_decision` 记 source=absent 放行);旋钮关 → `(None, False)` 且 `build_decision`
    根本不看它。两个写者共用。

    fix round 1(2026-09-26,coordinator review 第 3 条):该表的写者是非原子写(`write_index_events`
    直接 `to_csv`,不像本文件 `write_decision` 自己用的临时文件+`replace`),中断的一次会留下一个
    读不出来的半成品(如零字节文件)。`pd.read_csv` 一异常就会穿透这里、穿透 `write_decision`/
    `verify_decision`,只有外层 `safe_write_decision`/`safe_verify_decision` 的兜底 catch 接得住——
    但那意味着当天**完全不产出**决策文件,比任何一道门单独否决都坏(`_relative_buy_decision.json`
    是 `buyability`/`relative_ledger` 都要读的产物)。这里按 `configured_rebalance_gate` 已经在用的
    姿势兜底:读失败 → 响亮打一行 stderr、返回 `(None, True)`,第二个返回值(`error`)让
    `build_decision` 把这一天记成 `index_events.source="error"`——不是静默地当成 `"absent"`
    (「读不出来」与「压根没有」是两个不同的因,即使门后果相同;三态见 `build_decision` docstring)。
    """
    if not rebalance_gate:
        return None, False
    from autoresearch.scan.index_events import events_by_code, load_index_events

    try:
        return events_by_code(load_index_events(scan)), False
    except Exception as exc:  # noqa: BLE001 — B 级源读失败不得连累整份决策文件,但降级必须可见
        print(f"[relative_buy] index_events.csv 读取失败({exc!r})→ rebalance_close 门放行"
              f"(记 index_events.source=error,不是 absent)", file=sys.stderr)
        return None, True


def _published_card_sources(target: Path) -> dict[str, dict]:
    """已发布决策文件的逐码 `card_context.source`(供 `verify_decision` 复用判据)。
    文件缺席/损坏/非 dict → 空字典(= 当作"什么都还没发布过",不是错误)。
    """
    if not target.exists():
        return {}
    try:
        doc = json.loads(target.read_bytes().decode("utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return {}
    if not isinstance(doc, dict):
        return {}
    out: dict[str, dict] = {}
    for row in doc.get("candidates") or []:
        if not isinstance(row, dict):
            continue
        code = row.get("code")
        source = (row.get("card_context") or {}).get("source")
        if isinstance(code, str) and isinstance(source, dict):
            out[code] = source
    return out


def _card_context_for(code: str, card_snapshot: dict[str, dict] | None) -> dict:
    """纯函数:固定卡快照 → 该票的 `card_context`(spec §7.1 字段表 + `source` 子字段)。
    不读盘、不归档——所有 I/O 已经在调用方(`write_decision`/`verify_decision`)完成,
    `build_decision` 因此对"卡怎么读到的"保持纯计算(spec §7.2 硬约束)。

    `contract=_LIVE_EXEC_LINE_CONTRACT`(fix round 1,review "ALSO";两处 `parse_card_
    context` 调用——正常路径与下面的读取失败路径——都传同一个真契约,理由见该常量旁注。

    读取失败(fix round 1,finding 3)与卡缺席分开渲染:`card_snapshot[code]` 带
    `read_error` 键 → 不当"文本为空"处理(那会印出"卡片正文为空或缺失"这句**不实**的
    话——文本根本不是空的,是读不出来),改为在 `parse_card_context(None, ...)` 的标准
    空壳基础上,把 `parse_errors` 换成点名文件路径与具体异常原因的那一句;`source.
    relative_path` 仍是已知的(glob 找到过这个文件),只是 `card_sha256` 拿不到(没读到
    内容)——这一对字段组合(有路径、无 hash)本身就是"存在但读不出来"与"压根不存在"
    (两者都 `None`)的可辨结构信号,不必新开一个布尔位。
    """
    snap = (card_snapshot or {}).get(code)
    if snap and snap.get("read_error"):
        failure = snap["read_error"]
        parsed = parse_card_context(None, contract=_LIVE_EXEC_LINE_CONTRACT)
        parsed = {**parsed, "parse_errors": [
            f"text: 卡片文件读取失败({failure['relative_path']}): {failure['error']}"]}
    else:
        text = snap.get("text") if snap else None
        parsed = parse_card_context(text, contract=_LIVE_EXEC_LINE_CONTRACT)
    source = (dict(snap["source"]) if snap and isinstance(snap.get("source"), dict)
             else dict(_ABSENT_CARD_SOURCE))
    return {**parsed, "source": source}


# ── E6 实际选择解释(spec §7.2「实际选择依据」)───────────────────────────────
def _selection_sort_spec(pool: str) -> tuple[list[str], list[str]]:
    """该 pool 的 buy_pool 排序键名 + 方向——**手动**与 `build_decision` 里真正用来
    `.sort()` 的 key 元组保持同序(那两处调用点各自带一行指回这里的旁注,搜
    `_selection_sort_spec` 能看到全部三处)。

    这是记录性的复述,不是从同一份源派生——两个 `.sort()` 调用嵌在 `eligible`/`buy_pool`
    的构造逻辑里,不值得为了消掉这一份复述去拆解那段已经锁定、被大量既有测试钉死的排序
    代码(fix round 1,finding 5 的权衡:**改成单一来源**风险高于**留复述 + 硬 guard**)。
    真正的防线是 `test_finalists_and_composite_sort_key_specs_match_the_real_tiebreak_
    order`:它用刻意做到"前面所有键都打平、只有最后一级 tiebreak 能分胜负"的 fixture,
    独立算出(不借这个函数)两个池各自的期望顺序,再断言真实 `selection["codes"]` 与之
    相等——mutation probe(把某一侧 `.sort()` 的 tiebreak 顺序换掉)证实过它真的会因为
    `.sort()` 改了而变红,不是只测自己抄的这份复述有没有内部自洽。
    """
    if pool == POOL_COMPOSITE:
        return (["target_align", "amount", "code"], ["desc", "desc", "asc"])
    return (["relative_decision_score", "target_align", "amount", "code"],
            ["desc", "desc", "desc", "asc"])


def _sort_key_values(row: dict, names: list[str], universe: dict) -> list:
    raw = {
        "relative_decision_score": row["relative_decision_score"],
        "target_align": row["faces"]["target_align"],
        "amount": universe["amount"].get(row["code"]),
        "code": row["code"],
    }
    return [round(raw[name], 6) if isinstance(raw[name], float) else raw[name]
           for name in names]


def _selection_block(*, pool: str, eligible: list[dict], buy_pool: list[dict],
                     universe: dict, buys: list[dict], winner_code: str | None,
                     second_buy: dict, excluded: list[dict], candidates_n: int) -> dict:
    """spec §7.2「实际选择依据」——只从 `build_decision` 已经算好的 eligible/buy_pool/
    排序值/excluded/second_buy 里摘,不重算第二套排序(那正是 composite 池 E03 场景要堵
    的病:重算迟早和真正选择分家)。`pool_rank` 用 `codes.index()` 现查,不从
    `observation_rank` 反推——两者在 composite 池下可以是不同的数。

    `winner_code`(fix round 1,finding 6):由调用方(`build_decision`)算一次传进来,
    不在这里对 `buys[0]` 再算第二遍——`buys` 仍作为形参保留,只用于 `buys_count`。
    """
    names, directions = _selection_sort_spec(pool)
    codes = [row["code"] for row in buy_pool]
    pinned_excluded = sorted(row["code"] for row in excluded if row["reason"] == "pinned_holding")
    not_in_pool_excluded = sorted(row["code"] for row in excluded if row["reason"] == "not_in_pool")
    return {
        "pool": pool,
        "population": {
            "candidates": candidates_n,
            "passed_hard_gates": len(eligible),
            "after_pinned_exclusion": len(eligible) - len(pinned_excluded),
            "final_pool": len(buy_pool),
        },
        "codes": codes,
        "sort_keys": {
            "names": names, "directions": directions,
            "values": {row["code"]: _sort_key_values(row, names, universe) for row in buy_pool},
        },
        "exclusions": {"pinned_holding": pinned_excluded, "not_in_pool": not_in_pool_excluded},
        "winner": {
            "code": winner_code,
            "pool_rank": (codes.index(winner_code) + 1) if winner_code in codes else None,
        },
        "buys_count": len(buys),
        "second_buy_reason": second_buy["reason"],
    }


def _veto_accounting_block(*, candidates: list[dict], eligible: list[dict],
                           buy_pool: list[dict], excluded: list[dict],
                           candidates_n: int, hard_gates: tuple[str, ...] = _HARD_GATES) -> dict:
    """否决股票数按 code 去重;每门命中数另列(可能重复计一只票);四个流水人口分别
    命名——全文任何地方都不得用"合格"指两个不同的数(bullet 5/9)。

    `hard_gates`(v4.1):当次运行实际生效的硬门元组(`ctx["hard_gates"]`,= `_hard_gates
    (rebalance_gate)` 的结果),不是恒定的 `_HARD_GATES`——第五门只在旋钮开时存在,`by_gate`
    必须按当天真实跑过的门数遍历,不能对关着的旋钮也报一个 `rebalance_close` 键。
    """
    pinned_excluded = sorted(row["code"] for row in excluded if row["reason"] == "pinned_holding")
    vetoed_codes = sorted({row["code"] for row in candidates if not row["eligible"]})
    by_gate = {gate: sum(1 for row in candidates if row["hard_gate"].get(gate) is False)
              for gate in hard_gates}
    return {
        "population": {
            "candidates": candidates_n,
            "passed_hard_gates": len(eligible),
            "after_pinned_exclusion": len(eligible) - len(pinned_excluded),
            "final_pool": len(buy_pool),
        },
        "vetoed_stocks": len(vetoed_codes),
        "vetoed_codes": vetoed_codes,
        "by_gate": by_gate,
    }


def _selection_conflicts(by_code: dict[str, dict], winner_code: str | None) -> list[dict]:
    """spec §7.2:选中票的卡面与 E6 选择之间的**展示性**冲突——只看被选中的那一只(v1
    恒 0/1 只 BUY),三种触发:卡面 PROHIBITED、卡面 CONDITIONAL(前置条件未被证明满足)、
    卡缺失/解析失败(`parse_status == "ERROR"`)。**这是展示事实,不新增门**——本函数不
    读写任何 `hard_gate`/`eligible`/`buys`,调用点也在 `build_decision` 选择完成之后。
    """
    if not winner_code or winner_code not in by_code:
        return []
    card = by_code[winner_code]["card_context"]
    conflicts: list[dict] = []
    stance = card.get("entry_stance")
    if stance == "PROHIBITED":
        conflicts.append({
            "code": winner_code, "type": "card_says_prohibited",
            "detail": (f"卡面 entry_stance=PROHIBITED(position_raw="
                      f"{card.get('position_raw')!r}, trigger_raw={card.get('trigger_raw')!r})"
                      f",E6 仍选中为 BUY"),
        })
    elif stance == "CONDITIONAL":
        conflicts.append({
            "code": winner_code, "type": "condition_not_shown_met",
            "detail": (f"卡面 entry_stance=CONDITIONAL,前置条件未被证明满足"
                      f"(position_raw={card.get('position_raw')!r}, "
                      f"trigger_raw={card.get('trigger_raw')!r})"),
        })
    if card.get("parse_status") == "ERROR":
        conflicts.append({
            "code": winner_code, "type": "card_missing_or_unparseable",
            "detail": f"card_context.parse_errors={card.get('parse_errors')}",
        })
    return conflicts


def _render_why(*, pool: str, selection: dict, by_code: dict[str, dict],
                conflicts: list[dict], tier_counts: dict | None = None,
                tier: str | None = None) -> str:
    """`why`:由 `selection`/`conflicts` 结构化字段**固定渲染**,不另算——用实际池内
    名次(`selection.winner.pool_rank`),绝不用 `observation_rank`(mutation probe:把这里
    换成 observation_rank,composite 池的 E03 测试必须变红)。

    `tier_counts`/`tier`(v4.0):`tiering=True` 时由调用方传入(`None` = 关,不渲染这句)。
    """
    winner_code = selection["winner"]["code"]
    if winner_code is None:
        return "当日 BLOCKED,无 BUY 可解释(候选/硬门/池过滤后无入选)。"
    pool_rank = selection["winner"]["pool_rank"]
    observation_rank = by_code[winner_code].get("observation_rank")
    names = selection["sort_keys"]["names"]
    values = selection["sort_keys"]["values"][winner_code]
    key_text = "、".join(f"{n}={v}" for n, v in zip(names, values, strict=True))
    parts = [
        f"{winner_code} 是 {pool} 池(共 {len(selection['codes'])} 只)第 {pool_rank} 名"
        f"(全体 eligible 观察 rank={observation_rank};两者可能不同,以池内第 {pool_rank} 名"
        f"为准,不是观察 rank)。",
        f"排序依据:{key_text}。",
        f"第 2 只未出:{selection['second_buy_reason']}。",
    ]
    if tier_counts is not None:
        parts.append(f"分级:A 级 {tier_counts['A']} 只 / R 级 {tier_counts['R']} 只,选中 {tier} 级。")
    for c in conflicts:
        parts.append(f"冲突:{c['type']} — {c['detail']}")
    return " ".join(parts)


# ── 主构建 ─────────────────────────────────────────────────────────────────
def build_decision(scan_dir: Path | str, date: str | None = None,
                   mode: str = MODE_SHADOW, exclude_pinned: bool = False,
                   pool: str = POOL_FINALISTS,
                   card_snapshot: dict[str, dict] | None = None,
                   tiering: bool = False,
                   index_events: dict[str, list[dict]] | None = None,
                   rebalance_gate: bool = False,
                   index_events_error: bool = False) -> dict:
    """`context/scan/<date>` → 统一相对决策文档(确定性、零 LLM、零联网、只读)。

    护照**现算**(`passport.build_passport`),不读盘上那份 `_candidate_passport.json`:
    它是同一函数的派生视图,现算才保证决策与当日真实产物同源,不被一份过期文件摆布。

    `exclude_pinned`(v2.0,task-2.2):`True` 时 BUY 只在**非📌**合格候选里选 rank1;
    📌 票仍进 `candidates`(全量,观测语义不变),只是不当 BUY——每个被跳过的📌合格票往
    `excluded` 追加一条 `reason="pinned_holding"`。`rank` 字段照旧按**全体** eligible 排,
    不因排除而重排(观测语义不变)。若当日非📌合格为 0 → 诚实 `blocked=True`,不退回去
    选📌票。缺省 `False` = 现行为(parity)。

    `card_snapshot`(schema 2,Task 7):可选的「固定卡输入」——`{code: {"text":
    str|None, "source": {...}}}`,由 `write_decision`/`verify_decision` 在 I/O 边界读盘
    构造(`_build_card_snapshot`)。本函数只按 code 查表、调用纯函数
    `l4.parsers.parse_card_context` 解析,**不自己读盘、不归档**——保持纯计算是字节级
    parity(两写者同输入 → 同输出)与 golden 投影测试可信的前提。缺省 `None` 时每只候选
    的 `card_context` 一律按"卡缺失"处理(`parse_card_context(None)`),不影响
    `eligible`/`hard_gate`/`buys`/`rank` 等既有字段——这些字段与卡面解析完全独立
    (schema 1 行为逐字不变的证据)。

    `tiering`(v4.0,2026-09-24 可买性对齐 §2.6,controller Ruling P21):缺省 `False` =
    v3.0 逐字(golden `test_tiering_off_is_v3_verbatim_even_with_prohibited_card`)—— 卡面
    `entry_stance` 只进 `conflicts` 展示,不碰 `eligible`/`buys`。`True` 时两件事同时打开:
    ① `entry_stance == "PROHIBITED"` 在 `_hard_gate` ④ 直接否决(`no_redflag=False`);
    ② `buy_pool` 按 `entry_stance == "ALLOWED"` 拆成 A 级(`basis="card_backed"`)/R 级
    (`basis="relative_forced"`,其余 eligible)两桶,A 级非空则从 A 级出 `buys[0]`(带
    `tier="A"`),否则从 R 级出(`tier="R"`);两桶都空则诚实 `blocked`,不强出。`CONDITIONAL`
    /`UNKNOWN` 都落 R 级,不否决、不进 A 级——三值不是两值的化简。实测(P25):A 级在全部
    8 个历史扫描日恒为 0(没有卡带过这条入场线),这是当前证据下的预期结果,不是要调参
    数去凑出来的 bug。

    `index_events` / `rebalance_gate`(v4.1,2026-09-25 指数调样事件 §2.4):`rebalance_gate=False`(缺省)
    = v4.0 逐字,`index_events` 被忽略。`True` 时 `_hard_gate` 多第五门 `rebalance_close`:该票在
    `index_events[code]` 里任一行 `phase == "passive_close_eve"` 即否决(调入调出皆算,E2 裁定);
    `index_events is None`(= 盘上无 index_events.csv:源不可达或日历腿关)→ 全员放行,顶层
    `index_events.source="absent"`、`gate_evaluated=False` 留痕;`{}` = 源可达无事件,`source="ok"`。
    `index_events` 与 `card_snapshot` 同属「固定盘上输入」:由 `write_decision`/`verify_decision`
    在 I/O 边界读盘构造,本函数不自己读文件。

    `index_events_error`(fix round 1,2026-09-26,coordinator review 第 3 条):三态 `source` 的
    第三值。`index_events` 参数本身仍是 `None`(与「源缺席」共用同一个门后果——全员放行),
    这个独立的布尔位才是唯二区分「压根没有」与「读不出来」的信号:`True` 时顶层
    `index_events.source` 记 `"error"` 而不是 `"absent"`(`gate_evaluated` 两者都仍是 `False`——
    这个字段回答的是"门跑了没有",不是"为什么没跑")。由 `write_decision`/`verify_decision` 的
    I/O 边界(`_index_events_input`)在读 `index_events.csv` 抛异常时置位;本函数不自己读盘,
    也不判断「为什么」`index_events` 是 `None`——那是边界已经替它决定好的事实,原样记账。
    """
    if mode not in {MODE_SHADOW, MODE_ACTIVE}:
        raise ValueError(
            f"mode 只接受 {MODE_SHADOW!r}/{MODE_ACTIVE!r}(装开关不是翻开关,翻 active 之外"
            f"的值一律非法);收到 {mode!r}")
    if pool not in {POOL_FINALISTS, POOL_COMPOSITE}:
        raise ValueError(
            f"pool 只接受 {POOL_FINALISTS!r}/{POOL_COMPOSITE!r};收到 {pool!r}")
    scan = Path(scan_dir)
    date = date or scan.name

    passport = build_passport(scan)
    entries = [entry for entry in passport["candidates"].values()
               if entry["l4"].get("dispatched")]
    entries.sort(key=lambda entry: entry["code"])

    universe = _universe(scan)
    seat_codes = _composite_seat_codes(scan)
    dossier = _json_doc(scan / "_dossier_present.json")
    n_channels = {entry["code"]: entry["recall"].get("n_channels")
                  for entry in entries
                  if entry["recall"].get("n_channels") is not None}
    ctx = {
        "universe": universe,
        "data_a": _data_contract_ok(scan),
        "task_book": _task_book(scan),
        "price_claim": _price_claim_status(scan),
        "tripwire": _tripwire_severity(scan),
        "dossier": {_code(code) for code in dossier} if isinstance(dossier, list) else set(),
        "n_channels_pctl": _mid_rank_pctl({code: float(value)
                                           for code, value in n_channels.items()}),
    }
    # v4.0:候选循环前先把 card_context 算齐一遍(避免每票在 _hard_gate 与候选字典构造
    # 里各解析一次 —— `parse_card_context` 是纯函数,重复调用不会给出不同答案,但没理由
    # 算两遍)。`ctx["tiering"]` 让 `_hard_gate` 能读到开关,不必再加一个形参改遍全部
    # 调用点。
    card_ctx = {entry["code"]: _card_context_for(entry["code"], card_snapshot) for entry in entries}
    ctx["card_context"] = card_ctx
    ctx["tiering"] = tiering
    ctx["rebalance_gate"] = rebalance_gate
    ctx["index_events"] = index_events
    ctx["hard_gates"] = _hard_gates(rebalance_gate)

    faces = _faces_table(entries, ctx)
    excluded: list[dict] = []
    candidates: list[dict] = []
    for entry in entries:
        code = entry["code"]
        gates, details = _hard_gate(entry, ctx)
        excluded.extend(details)
        face = faces[code]["faces"]
        candidates.append({
            "code": code,
            "name": entry.get("name"),
            "sector": entry.get("sector"),
            "pinned": bool(entry["l2"].get("pinned")),
            "eligible": all(gates.values()),
            "hard_gate": gates,
            "faces": {name: face[name] for name in _FACES},
            "faces_missing": faces[code]["missing"],
            "relative_decision_score": round(
                sum(face[name] for name in _FACES) / len(_FACES), 6),
            "rank": None,
            # schema 2:`rank` 的显式别名——同一个数,新名字专供 `selection`/`why` 点名
            # 引用,不让"全体观察名次"与"实际池内名次"共用一个容易被读错的名字
            # (spec §7.2:不得把全体观察第 N 名写成实际池内第 N 名)。`rank` 本身取值/
            # 语义逐字不变。
            "observation_rank": None,
            "research_rating": entry["l4"].get("research_rating"),
            "amount_pctl": universe["amount_pctl"].get(code),
            "price_claim": ctx["price_claim"].get(code, "UNMEASURED"),
            "expected_abs_gap": {"value": None, "status": "UNMEASURED", "n": 0},
            # v3.0:该票在不在当日 BUY 候选池里。`finalists` 池 = 全体(与 v2 逐字一致);
            # `composite` 池 = 只有证据席。**不进池 ≠ 不进候选表** —— 全部派发过的票照旧
            # 全量留在 `candidates` 里(观测语义不变),只是不当 BUY。
            "in_pool": (True if pool == POOL_FINALISTS else code in seat_codes),
            # schema 2:卡面原文的保守解析(spec §7.1)。纯查表 + 纯函数,`card_snapshot`
            # 缺该 code → "卡缺失"(不是错误,不影响 eligible/hard_gate/buys)。取自上面
            # 候选循环前已经算好的 `card_ctx`(v4.0),不在这里二次解析。
            "card_context": card_ctx[code],
        })

    by_code = {row["code"]: row for row in candidates}
    # 这段 key 元组的键名/方向复述在 `_selection_sort_spec(POOL_FINALISTS)`(fix round 1,
    # finding 5)——改这里的顺序必须同步改那份复述,`test_finalists_and_composite_sort_
    # key_specs_match_the_real_tiebreak_order` 是防漂移的 guard。
    eligible = sorted(
        (row for row in candidates if row["eligible"]),
        key=lambda row: (-row["relative_decision_score"],
                         -row["faces"]["target_align"],
                         -(universe["amount"].get(row["code"]) or 0.0),
                         row["code"]))
    for rank, row in enumerate(eligible, start=1):
        by_code[row["code"]]["rank"] = rank
        by_code[row["code"]]["observation_rank"] = rank

    # exclude_pinned(v2.0):BUY 池排掉📌持仓(保送不算判例);rank 字段照旧按**全体**
    # eligible 排(上面那个循环),这里只影响谁能当 buys[0]——不重排、不从候选表摘除。
    buy_pool = ([row for row in eligible if not row["pinned"]]
                if exclude_pinned else list(eligible))
    for row in eligible:
        if exclude_pinned and row["pinned"]:
            excluded.append({"code": row["code"], "reason": "pinned_holding",
                             "detail": "📌 持仓不参与相对 BUY(保送不算判例)"})
    if pool == POOL_COMPOSITE:
        # v3.0(A1):BUY 只在证据席里选,并且**按 composite 分排**(`target_align`)——
        # 四面 Borda 平均里只有这一面有隔夜正证据(L2 top20/top50 +0.14/+0.17pp,t 1.96/2.78),
        # 另三面(recall_strength/evidence/risk_safety)在本尺上无证据,降为记录列不进排序。
        dropped = [row for row in buy_pool if not row["in_pool"]]
        for row in dropped:
            excluded.append({"code": row["code"], "reason": "not_in_pool",
                             "detail": "不在 composite 证据席(v3.0 候选池=L3 守卫⑨ 席位)"})
        buy_pool = [row for row in buy_pool if row["in_pool"]]
        # 这段 key 元组的键名/方向复述在 `_selection_sort_spec(POOL_COMPOSITE)`(同上一处
        # finalists 排序的旁注,fix round 1 finding 5)。
        buy_pool.sort(key=lambda row: (-row["faces"]["target_align"],
                                       -(universe["amount"].get(row["code"]) or 0.0),
                                       row["code"]))

    # v4.0(2026-09-24 §2.6,controller Ruling P21):`tiering` 开时把 buy_pool 拆成
    # A 级(卡面 entry_stance==ALLOWED)/R 级(其余 eligible)两桶,A 级非空优先出、否则
    # R 级出;两桶都空诚实 blocked,不强出。两桶各自沿用 buy_pool 已经算好的排序(finalists/
    # composite 两套键均适用,这里不重排)。`tiering` 关(默认)= v3.0 逐字。
    tier_counts: dict[str, int] | None = None
    if tiering:
        a_pool = [row for row in buy_pool if row["card_context"].get("entry_stance") == "ALLOWED"]
        r_pool = [row for row in buy_pool if row["card_context"].get("entry_stance") != "ALLOWED"]
        tier_counts = {"A": len(a_pool), "R": len(r_pool)}
        if a_pool:
            buys = [{"code": a_pool[0]["code"], "basis": "card_backed", "rank": 1, "tier": "A"}]
        elif r_pool:
            buys = [{"code": r_pool[0]["code"], "basis": "relative_forced", "rank": 1, "tier": "R"}]
        else:
            buys = []
    else:
        # 第 2 只起的门:v1 影子期无已验证阈值 → 恒不满足,恒只出 1 只。
        buys = ([{"code": buy_pool[0]["code"], "basis": "relative", "rank": 1}]
                if buy_pool else [])
    second_buy = {"fired": SECOND_BUY_THRESHOLD is not None,
                  "reason": SECOND_BUY_BLOCK_REASON,
                  "threshold": SECOND_BUY_THRESHOLD}

    blocked = not buys
    blocked_reasons: list[dict] = []
    if blocked:
        if not candidates:
            blocked_reasons = [{"reason": "no_candidates", "n": 0}]
        else:
            buckets: dict[str, int] = {}
            for row in excluded:
                buckets[row["reason"]] = buckets.get(row["reason"], 0) + 1
            blocked_reasons = [{"reason": reason, "n": buckets[reason]}
                               for reason in sorted(buckets)]

    # schema 2:实际选择依据 + 否决计数 + 字段角色 + 展示性冲突 + 固定渲染的 why。全部
    # 从上面已经算好的 eligible/buy_pool/excluded/second_buy/candidates 摘,不重算第二套
    # 排序或门(spec §7.2 硬约束)。
    winner_code = buys[0]["code"] if buys else None       # 只算一次(fix round 1, finding 6)
    selection = _selection_block(
        pool=pool, eligible=eligible, buy_pool=buy_pool, universe=universe,
        buys=buys, winner_code=winner_code, second_buy=second_buy, excluded=excluded,
        candidates_n=len(candidates))
    veto_accounting = _veto_accounting_block(
        candidates=candidates, eligible=eligible, buy_pool=buy_pool,
        excluded=excluded, candidates_n=len(candidates), hard_gates=ctx["hard_gates"])
    conflicts = _selection_conflicts(by_code, winner_code)
    why = _render_why(pool=pool, selection=selection, by_code=by_code, conflicts=conflicts,
                      tier_counts=tier_counts, tier=(buys[0].get("tier") if buys else None))

    market_members = universe["members"]
    raw_orphans = passport.get("orphans") or {}
    orphans = {"finalists": sorted(_code(c) for c in
                                   (raw_orphans.get("finalists") or [])),
               "rated": sorted(_code(c) for c in (raw_orphans.get("rated") or []))}
    return {
        "schema_version": SCHEMA_VERSION,
        "rule_version": RULE_VERSION,
        "mode": mode,
        "exclude_pinned": exclude_pinned,
        # v3.0:BUY 候选池来源 + 当日席位码。**读这份决策文件前先看这两个键** ——
        # `finalists` 与 `composite` 是两条不同的规则,读数不可直接相连。
        "pool": pool,
        "pool_members": sorted(seat_codes),
        # v4.0:入场门 + A/R 分级总开关 + 当日分桶计数(`None` = 关,未分级)。
        "tiering": tiering,
        "tier_counts": tier_counts,
        # v4.1:第五门的评估留痕(只在旋钮开时出现;缺这个块 = 仪器未上线,不是「当日无事件」)。
        # fix round 1:三态 `source`——`index_events_error` 只影响这一行;下面四个字段(gate_evaluated/
        # n_rows/n_candidates_in_events/hits)在「错误」与「缺席」两态下都因为 `index_events` 本身
        # 仍是 `None` 而自然算出相同的(False/0/0/[])——两态门后果本就相同,不需要各写一份。
        **({"index_events": {
            "source": ("error" if index_events_error else
                       "absent" if index_events is None else "ok"),
            "gate_evaluated": index_events is not None,
            "n_rows": 0 if index_events is None else sum(len(v) for v in index_events.values()),
            "n_candidates_in_events": sum(1 for row in candidates if row["code"] in (index_events or {})),
            "hits": sorted(row["code"] for row in candidates
                           if row["hard_gate"].get(REBALANCE_GATE) is False),
        }} if rebalance_gate else {}),
        "date": date,
        "ruler": MAIN_RULER,
        "benchmark": {
            "ruler": MAIN_RULER,
            "entry_flag": universe["entry_flag_column"],
            "entry_flag_present": universe["entry_flag_present"],
            "market": {
                # I-2(复核轮)把这句话改对了。原文写的是「L0 可交易全集等权 gap_c1_o2」
                # 并把它挂在 `rel_gap_market` 名下 —— 那是**两个不同人口**:本块的 `n`
                # 是决策层自己算四面分位与 P10 流动性门用的分母(L0 过门票),而
                # `rel_gap_market` 的真分母是**全市场可交易**(`ruler.py` T22 I-1 人口
                # 裁定,含漏在 L0 的票;2026-08-04 实测 4193 vs 5426)。事后评分那个分母
                # 评分时另算,不在本产物里(该腿已随 learning 层退役删除)。
                "definition": "决策层分位/流动性门的分母 = 当日 L0 可交易全集等权",
                "column": REL_MARKET,
                "eval_population": ("全市场可交易(entry_tradable,含漏在 L0 的票)"
                                    "—— 与本块 n 不同,评分时由 relative_ledger 另算"),
                "n": len(market_members),
                "members_sha256": hashlib.sha256(
                    "\n".join(market_members).encode("utf-8")).hexdigest(),
            },
            "sector": {
                "definition": "申万一级可交易等权",
                "column": REL_SECTOR,
                "source_column": "industry",     # 实为 tushare「所处行业」,见文件尾 ⑤
                "n_sectors": len(universe["sectors"]),
            },
            # 红灯词表进产物(复核 Minor):只读 JSON 的人看不见集合里有没有死条目 ——
            # 导出词表 = 让它自己说话。("监管/审计红灯" 那个死条目已在 D8.3 ②删除,
            # 现在这里导出的就是真正生效的四词。)
            "redflag_early_stop_reasons": sorted(REDFLAG_EARLY_STOP_REASONS),
            "excluded_from_benchmark": universe["excluded"],
        },
        "inputs": {
            "l1_scored_full": "PRESENT" if universe["rows_present"] else "ABSENT",
            "task_book": "PRESENT" if ctx["task_book"] is not None else "ABSENT",
            "price_claim_status": ("PRESENT" if ctx["price_claim"] else "ABSENT"),
            "dossier_present": "PRESENT" if ctx["dossier"] else "ABSENT",
            "run_health": ("PRESENT" if (scan / "run_health.json").exists()
                           else "ABSENT"),
        },
        # I-3(复核轮):护照的 `orphans` 原样透传。护照 docstring 点名「下游只读护照的
        # 消费者会以为它们不存在」—— 有评级却不在 L2 菜单里的票永远当不成相对 BUY,而
        # 在「每个成功日至少一只」的裁定下,候选池静默缩水正是要防的病型。不改候选集
        # 口径(那是护照的断言①),只把差额报出来。
        "orphans": orphans,
        "counts": {
            "candidates": len(candidates),
            "eligible": len(eligible),
            "excluded_rows": len(excluded),
            "in_pool": sum(1 for row in candidates if row["in_pool"]),
            "buys": len(buys),
            "with_missing_face": sum(1 for row in candidates if row["faces_missing"]),
            "orphan_finalists": len(orphans["finalists"]),
            "orphan_rated": len(orphans["rated"]),
        },
        "candidates": candidates,
        "buys": buys,
        "second_buy": second_buy,
        "blocked": blocked,
        "blocked_reasons": blocked_reasons,
        "excluded": sorted(excluded, key=lambda row: (row["code"], row["reason"])),
        # schema 2(Task 7,spec §7.2)——见文件头「schema 2」注:纯观测性新增,不改上面
        # 任何一个既有键的取值。
        "selection": selection,
        "veto_accounting": veto_accounting,
        # v4.0(controller Ruling P21 (a)):`field_usage` 是 `tiering` 的函数,不是恒定
        # 常量——tiering=True 的 run 必须报 entry_stance 那天真实做过的两件事(否决 +
        # 分级),不能继续宣称它是 display_only(见 `_field_usage` 旁注)。
        "field_usage": _field_usage(tiering, rebalance_gate),
        "conflicts": conflicts,
        "why": why,
    }


def _serialize_decision(doc: dict) -> bytes:
    """`build_decision` 输出 → byte-stable 契约的 bytes。`write_decision` 与 `verify_decision`
    共用这一个函数 —— 两处各写各的序列化会让「逐字节比较」变成两套口径,防止未来漂移。"""
    return (json.dumps(doc, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8")


# ── E3b 消费侧唯一入口(2026-08-19 task-2.4)────────────────────────────────────
#
# **只读盘、不现算**是这两个函数存在的全部理由。消费者若各自 `build_decision()` 现算,
# 盘上那份(= brief 印给用户的那份)与自己算的那份就会分家 —— 这正是
# `docs/research/2026-08-19-decision-file-two-writers-and-taskbook-hash.md` §2 实测到的
# 病(08-10 起 8 份 brief 里 4 份与决策文件不一致)。发布出去的答案只有一个,消费者一律
# 读它;读不到就诚实降级,不许自己再算一个出来。


def load_decision(scan_dir: Path | str, *, date: str | None = None) -> dict | None:
    """盘读 `_relative_buy_decision.json`。缺席 / 坏 JSON / **过期** → `None`。

    「过期」判据 = 文件里的 `date` 字段 ≠ 期望日(缺省取 `scan_dir` 的目录名)。E3b 的三个
    消费点全部跑在 `build_summary`(`publisher.py:305`)内部,比 writer-1 写决策文件
    (`:325`)**早一站** —— 那一刻 scan 目录里躺着的很可能是**上一日/上一跑**的文件。
    读日期不符的那份 = 把昨天的 BUY 当成今天的,比没有还坏,所以这里直接判 `None` 让
    调用方走「占位符 / 显式回退」路径。

    同一天**上一跑**的文件日期是对的、本函数无法分辨 —— 那是 E3b 裁定 3 明确接受的:
    `run_health.json` 一次发布写多次,最后一次(`publisher.py:375`)在决策文件已在盘之后,
    终版快照因此是对的,早期快照的该字段本就只是中间态。
    """
    scan = Path(scan_dir)
    try:
        doc = json.loads((scan / DECISION_FILENAME).read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return None
    if not isinstance(doc, dict):
        return None
    if str(doc.get("date") or "") != str(date or scan.name):
        return None
    return doc


def configured_relative_buy() -> tuple[str, bool, str | None, str, bool]:
    """`scan_config.jsonc` 的 `relative_buy` 块 →
    `(mode, exclude_pinned, activate_date, pool, tiering)`。

    **消费侧的 mode 事实源是 config,不是决策文件**:E3b 的渲染点要在决策文件写出来**之前**
    就决定"要不要落占位符",那时盘上那份要么不存在要么是过期的,拿它的 `mode` 反推等于让
    昨天的开关决定今天的渲染。config 才是 writer-1 待会儿要用的那份开关(`post_run.py:673`
    同一处读取),两边同源才不会一个落占位、另一个不注入。

    缺文件 / 缺块 / 配置层故障 → `("shadow", False, None, "finalists", False)` = 内建默认 =
    现行为(parity)。故障降级必须留痕(同 `user_config.knob` 纪律),所以异常路径打一行
    stderr。`tiering`(v4.0)是第五个元素,同样缺键 = `False`(= v3.0 逐字,parity)。
    """
    try:
        from autoresearch.scan.user_config import load_user_config

        block = load_user_config().get("relative_buy") or {}
    except Exception as exc:  # noqa: BLE001 — 配置层故障不挡决策发布,但降级必须可见
        print(f"[relative_buy] scan_config 读取失败({exc!r})→ mode/exclude_pinned 用内建默认",
              file=sys.stderr)
        block = {}
    activate = block.get("activate_date")
    pool = str(block.get("pool") or POOL_FINALISTS)
    if pool not in {POOL_FINALISTS, POOL_COMPOSITE}:   # 错型不静默生效(同 knob 纪律)
        print(f"[relative_buy] scan_config 的 relative_buy.pool={pool!r} 非法 → 回落 "
              f"{POOL_FINALISTS!r}", file=sys.stderr)
        pool = POOL_FINALISTS
    return (str(block.get("mode") or MODE_SHADOW),
            bool(block.get("exclude_pinned", False)),
            str(activate) if activate else None,
            pool,
            bool(block.get("tiering", False)))


def configured_rebalance_gate() -> bool:
    """`scan_config.relative_buy.rebalance_gate`(v4.1 第五门总开关)。独立于五元组读取:少改调用点,
    且两个写者(`post_run` 的 write / verify)必须拿同一个值。缺文件 / 缺键 / 配置层故障 → False = v4.0
    逐字(parity),故障路径打一行 stderr(降级必须可见)。"""
    try:
        from autoresearch.scan.user_config import load_user_config

        block = load_user_config().get("relative_buy") or {}
    except Exception as exc:  # noqa: BLE001 — 配置层故障不挡决策发布,但降级必须可见
        print(f"[relative_buy] scan_config 读取失败({exc!r})→ rebalance_gate 用内建默认 False", file=sys.stderr)
        return False
    return bool(block.get("rebalance_gate", False))


def configured_mode() -> str:
    """薄封装:只要 mode 的调用点用这个,别自己再解析一遍 config。"""
    return configured_relative_buy()[0]


def is_active() -> bool:
    """config 的 `relative_buy.mode == "active"`。消费侧一律用它做 active 分支的判据。"""
    return configured_mode() == MODE_ACTIVE


def activate_date() -> str | None:
    """legacy 账本冻结日(`relative_buy.activate_date`);未配置 → `None` = 不冻结(parity)。"""
    return configured_relative_buy()[2]


def configured_pool() -> str:
    """BUY 候选池来源(v3.0)。薄封装,消费点别再自己解析一遍 config。"""
    return configured_relative_buy()[3]


def configured_tiering() -> bool:
    """入场门 + A/R 分级总开关(v4.0)。薄封装,消费点别再自己解析一遍 config。"""
    return configured_relative_buy()[4]


def write_decision(scan_dir: Path | str, date: str | None = None,
                   mode: str = MODE_SHADOW, exclude_pinned: bool = False,
                   pool: str = POOL_FINALISTS, tiering: bool = False,
                   rebalance_gate: bool = False) -> Path:
    """构建并原子落盘。`sort_keys=True` 是 byte 稳定契约的一半,另一半是构建本身无时序量。

    schema 2(Task 7):在这里的 I/O 边界一次性读齐 `details/*.md`、算 hash、尝试归档
    (`_build_card_snapshot`),把结果作为「固定卡输入」传给纯计算的 `build_decision`
    ——卡面读取只在这一处发生,`verify_decision` 复用同一构造函数,两者才谈得上"同一份
    卡输入 → 字节级 parity"。`tiering`(v4.0)原样透传给 `build_decision`,缺省 `False`
    = v3.0 逐字。`rebalance_gate`(v4.1)与 `tiering` 同款透传;两个写者必须用同一个值,
    否则 verify 天天误报。
    """
    scan = Path(scan_dir)
    target = scan / DECISION_FILENAME
    card_snapshot = _build_card_snapshot(scan)
    _events, _events_error = _index_events_input(scan, rebalance_gate)
    payload = _serialize_decision(
        build_decision(scan, date, mode, exclude_pinned, pool,
                       card_snapshot=card_snapshot, tiering=tiering,
                       index_events=_events, index_events_error=_events_error,
                       rebalance_gate=rebalance_gate))
    target.parent.mkdir(parents=True, exist_ok=True)
    temp = target.with_name(f"{target.name}.tmp")
    temp.write_bytes(payload)
    temp.replace(target)
    return target


def safe_write_decision(scan_dir: Path | str, date: str | None = None,
                        mode: str = MODE_SHADOW, exclude_pinned: bool = False,
                        pool: str = POOL_FINALISTS, tiering: bool = False,
                        rebalance_gate: bool = False) -> Path | None:
    """影子件失败不得阻断任何东西(本轮没有任何生产消费者依赖它)。`rebalance_gate`(v4.1)与
    `tiering` 同款透传;两个写者必须用同一个值,否则 verify 天天误报。"""
    try:
        return write_decision(scan_dir, date, mode, exclude_pinned, pool, tiering, rebalance_gate)
    except Exception as exc:  # noqa: BLE001 — 纯影子件失败只记一行,不连累主链
        print(f"[relative_buy] 构建失败: {type(exc).__name__}: {exc}", file=sys.stderr)
        return None


def _decision_digest(doc: dict, raw: bytes) -> dict:
    """决策文档 → 用于并排对比的精简摘要(至少 blocked/buys/counts/blocked_reasons + sha256)。"""
    return {
        "sha256": hashlib.sha256(raw).hexdigest(),
        "blocked": doc.get("blocked"),
        "buys": doc.get("buys"),
        "counts": doc.get("counts"),
        "blocked_reasons": doc.get("blocked_reasons"),
    }


def verify_decision(scan_dir: Path | str, date: str | None = None,
                    mode: str = MODE_SHADOW, exclude_pinned: bool = False,
                    pool: str = POOL_FINALISTS, tiering: bool = False,
                    rebalance_gate: bool = False) -> dict:
    """P0-2:writer-2(`post_run observe`)的第二次「写」改成幂等校验,不再无条件覆盖。

    `mode`/`exclude_pinned`/`tiering`(v2.0/v4.0)与 `write_decision` 同参、原样透传给
    现算的 `build_decision`——两个写者必须用**同一套**规则重算同一天的决策,否则"两次
    现算是否一致"这句话本身就没有意义(writer-1 用 active 算、writer-2 却永远拿 shadow
    去比,每天都会误报"不一致")。`rebalance_gate`(v4.1)与 `tiering` 同款透传;两个写者
    必须用同一个值,否则 verify 天天误报。

    `docs/research/2026-08-19-decision-file-two-writers-and-taskbook-hash.md` §2.1/§2.5:
    `_relative_buy_decision.json` 的两个合法写者(`publisher._run_publish` 与
    `post_run observe`)分处一次发布的两个不同时刻;P0-1 已经让两者读到的 `run_health`
    快照同源,但**没有**东西担保它们永远一致 —— 万一某天两次现算真的不同(比如中途
    `decision_records.json` 被别的进程改写),writer-2 原来的做法是**无条件原子重写**,
    把这件事静默抹掉:盘上文件从此变成了「brief 从未印过的答案」,`relative_ledger`
    读盘上文件记账,于是记账与发布分家 —— 这正是本文件双写者问题的成因,不能让「装了
    护栏」本身重新引入同一种静默改写。

    行为(不覆盖是硬约束,其余是留痕):
      - 现算(`build_decision`,参数与 `write_decision` 一致)并与盘上那份逐字节比较。
      - 相等 → 什么都不做(不重写、不留痕 —— 正常路径必须是安静的)。
      - 盘上文件缺席 → 没有「已发布答案」可比,不是「两次不一致」而是「还没发布过」,
        照写(等价于 write 模式);这不会掩盖任何东西,因为压根没有旧答案被覆盖。
      - 内容不等 → **绝不覆盖盘上那份**:另落一份并排证据 `_relative_buy_decision.
        mismatch.json`,往 `gate_fires.csv` 追加一条 data 类 fail 行(check 名故意不带
        `产物形状·`/`usage_reconcile·` 前缀,不被 `failclass.EXEMPT_PREFIXES` 豁免出
        data 类),并打一行 stderr。`gate_fires.csv` 是「当日发布了什么」的记账本 ——
        同一天两次现算给出不同答案本身就是必须报警的事实,不是可以悄悄了结的噪音。

    返回 `{"match": bool, "action": str, ...}`,便于调用方/测试内省;调用方若只关心
    副作用可以忽略返回值(与 `write_decision` 返回 `Path` 同一习惯,只是这里多一个字段)。

    schema 2(Task 7)卡面 parity:重新读一遍 `details/*.md`,逐码把 fresh 内容 hash 与
    「已发布文档记录的那份 `card_context.source.card_sha256`」比较——相同则原样复用已发布
    的 `source`(不重新归档,见 `_build_card_snapshot` 的 `reuse` 参数),这样卡没变时两次
    现算才能真正字节相等,不被"归档这一刻是否仍处于活跃 run"这种环境差异污染成假
    不一致。卡内容如果真的变了,复用判据自然失效,现算出的整份文档就会与盘上那份不同
    ——直接落进下面既有的"内容不等"分支(mismatch 侧车 + gate_fires),不必另开一条
    "卡变了"的专用信道。
    """
    scan = Path(scan_dir)
    target = scan / DECISION_FILENAME
    card_snapshot = _build_card_snapshot(scan, reuse=_published_card_sources(target))
    _events, _events_error = _index_events_input(scan, rebalance_gate)
    fresh_doc = build_decision(scan, date, mode, exclude_pinned, pool,
                               card_snapshot=card_snapshot, tiering=tiering,
                               index_events=_events, index_events_error=_events_error,
                               rebalance_gate=rebalance_gate)
    fresh_bytes = _serialize_decision(fresh_doc)
    resolved_date = str(fresh_doc.get("date") or date or scan.name)

    if not target.exists():
        target.parent.mkdir(parents=True, exist_ok=True)
        temp = target.with_name(f"{target.name}.tmp")
        temp.write_bytes(fresh_bytes)
        temp.replace(target)
        return {"match": True, "action": "write_absent", "path": str(target)}

    on_disk_bytes = target.read_bytes()
    if on_disk_bytes == fresh_bytes:
        return {"match": True, "action": "noop", "path": str(target)}

    # ── 不等:绝不覆盖,留证据 + 报警 ──
    try:
        on_disk_doc = json.loads(on_disk_bytes.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        on_disk_doc = {}
    on_disk_digest = _decision_digest(on_disk_doc, on_disk_bytes)
    fresh_digest = _decision_digest(fresh_doc, fresh_bytes)
    mismatch_path = scan / MISMATCH_FILENAME
    mismatch_payload = {
        "schema_version": 1,
        "date": resolved_date,
        "decision_filename": DECISION_FILENAME,
        "on_disk": on_disk_digest,
        "recomputed": fresh_digest,
    }
    mismatch_path.write_text(_serialize_decision(mismatch_payload).decode("utf-8"),
                             encoding="utf-8")

    detail = (f"盘上 blocked={on_disk_digest['blocked']} "
              f"buys={[b.get('code') for b in (on_disk_digest['buys'] or [])]} "
              f"(sha256={on_disk_digest['sha256'][:12]}) != 现算 "
              f"blocked={fresh_digest['blocked']} "
              f"buys={[b.get('code') for b in (fresh_digest['buys'] or [])]} "
              f"(sha256={fresh_digest['sha256'][:12]}) —— 见 {mismatch_path.name}")
    print(f"[relative_buy] verify 不一致(盘上那份保持不变): {detail}", file=sys.stderr)

    from autoresearch.scan.self_review import append_gate_fires

    append_gate_fires(scan, [{
        "check": MISMATCH_CHECK_NAME,
        "severity": "fail",
        "detail": detail,
        "code": None,
    }], resolved_date)

    return {"match": False, "action": "mismatch", "path": str(target),
            "mismatch_path": str(mismatch_path)}


def safe_verify_decision(scan_dir: Path | str, date: str | None = None,
                         mode: str = MODE_SHADOW, exclude_pinned: bool = False,
                         pool: str = POOL_FINALISTS, tiering: bool = False,
                         rebalance_gate: bool = False) -> dict | None:
    """`verify_decision` 的失败纪律版:出异常只打一行,与 `safe_write_decision` 同一姿势
    (决策件本身从不阻断发布);但内部真正的「不一致」分支不算异常,是正常返回路径。
    `rebalance_gate`(v4.1)与 `tiering` 同款透传;两个写者必须用同一个值,否则 verify 天天误报。"""
    try:
        return verify_decision(scan_dir, date, mode, exclude_pinned, pool, tiering, rebalance_gate)
    except Exception as exc:  # noqa: BLE001 — 纯影子件失败只记一行,不连累主链
        print(f"[relative_buy] verify 失败: {type(exc).__name__}: {exc}", file=sys.stderr)
        return None


def main(argv: list[str] | None = None) -> int:
    """CLI:构建并写入当日决策文档。"""
    raw = list(argv if argv is not None else sys.argv[1:])
    parser = argparse.ArgumentParser(
        description="统一相对决策层 finalizer v1(影子;确定性、零 LLM、零联网)")
    parser.add_argument("scan", help="分析日(YYYY-MM-DD)或 scan 目录")
    args = parser.parse_args(raw)
    explicit = Path(args.scan)
    scan = explicit if explicit.exists() else ws.scan_root() / args.scan
    target = write_decision(scan)
    doc = json.loads(target.read_text(encoding="utf-8"))
    print(json.dumps({"ok": True, "path": str(target), "mode": doc["mode"],
                      "rule_version": doc["rule_version"], "counts": doc["counts"],
                      "buys": doc["buys"], "blocked": doc["blocked"]},
                     ensure_ascii=False, sort_keys=True))
    return 0


# ── 修复轮 1(2026-08-09,复核 4 条 Important)──────────────────────────────
#
# 四条都是**堵放宽/补留痕**,一个字都没动 v1 规则语义(四面算法 / Borda 等权平均 /
# 并列决胜三级 / 第 2 只的门 / expected_abs_gap)——8 日回放逐日比对**零变化**可证。
#   I-1 `data_a` 三个 status 一律要求 `== "OK"`(`"ABSENT"` 不再当通过)+ `contract`
#       消费护照 `missing["l4.research_rating"]`(卡在盘却读不出评级 = 契约不完整)。
#       两道门是独立防线:run_health 写得早/写得乐观时 data_a 会被骗过,contract 仍拦得住。
#   I-2 `benchmark.market.definition` 原文把决策层分母说成了 `rel_gap_market` 的分母
#       (两个人口,08-04 实测 4193 vs 5426),改对并补 `eval_population`;
#       `column`/`n`/`members_sha256`/`n_sectors` 逐字保留 —— T24 `relative_ledger`
#       正在读它们。
#   I-3 护照 `orphans` + `counts.orphan_*` 透传(静默丢票从此有对账计数)。
#   I-4 真实 run 的副作用断言从「生产目录里不存在该文件」改成目录前后 diff —— 前者会被
#       合法操作打破(文档给的 CLI 命令、T24 挂进 `post_run.observe` 的 `safe_write_decision`),
#       是个会自己变红的假红灯。
#
# ── v1 已知问题(不在本轮改;改 = 新 rule_version + registry)────────────────
#
# 1. **(2026-08-31 已修 · D8.3 ②)** `REDFLAG_EARLY_STOP_REASONS` 曾含 `"监管/审计红灯"`,
#    在现行早停词表(`l4/parsers.py` 七选一)下**永不命中**——纯死条件,已删除(行为零变,
#    ratchet 测试见 `tests/scan/test_early_stop_parse.py`)。真正的监管信号在 L3 侧另有
#    独立探测器(`agents/l3_news.reg_flag`,近 10 日公告命中 立案/问询/关注函/处罚/违规/
#    诉讼/监管/证监会/交易所),但它没有进 L4 的结构化产物。要让"监管红灯"这条信号真正
#    活起来,得先把 `news_reg` 接进逐票产物、再走新 `rule_version` 加回集合,那是另一件事。
# 2. 逐票 `price_claim` 状态没有生产者(见文件头 ③)。在补上 `_price_claim_status.json`
#    的生产者之前,`evidence` 面的 "+0.2 干净" 分量对全体候选恒不给分 = 零鉴别力,
#    硬门 ③ 的价格断言这一支同样恒不触发。
# 3. `tripwire` 严重度只覆盖保送(pinned)票,非保送票恒 0。
# 4. 保送(pinned)持仓**默认不**被排除在 BUY 候选之外(v1 规则默认值;`exclude_pinned`
#    缺省 `False`)。若某天相对 BUY 落在自己已持有的票上,读数会与"新开仓"混在
#    一起——`candidates[].pinned` 已逐票留痕,可做分层统计(retro 的 L3 edge 曾被📌
#    保送污染,同族前科)。v2.0(task-2.2)起可用 `exclude_pinned=True` 显式排除;生产
#    `scan_config.jsonc` 默认仍是 `False`,真要打开是裁决表 A2 批准后的独立动作。
# 5. 行业基准的口径名叫"申万一级",但可用的 `industry` 列是 tushare「所处行业」
#    (2026-08-06 实测 129 个组、带 "Ⅱ" 后缀 = 申万二级粒度)。这个名实不符**不是本模块
#    引入的**:T22 `retro._rel_gap_cols` 生产 `rel_gap_sector` 时按同一列分组、docstring
#    也写"申万一级"。两处必须同时改才有意义,故本轮只把 `source_column` 落进产物留痕。
# 6. `research_rating != "Sell"` 只挡五档里最末一档。实测 2026-08-06 有 4 只 `Underweight`
#    (`decision_records.proposal == "SELL"`)全部通过硬门并进入排序——当天冠军是 Hold 所以
#    没咬到,但"提议卖出的票可以当相对 BUY 出"这条通路是敞开的。规则观察前锁定,本轮不改。
# 7. **(schema 2,Task 7,fix round 1 修订)`_card_context_for` 现在传
#    `contract=_LIVE_EXEC_LINE_CONTRACT`**——最初实现(观察前)把这里恒锁 `None`,理由是
#    误读了 `l4/parsers.py` "不导入这两个常量"的禁令:那条禁令是防止拿**今天**的值去判
#    **历史**卡的漂移(parser 本身不知道调用者手上的卡是今天写的还是十天前的)。但
#    `write_decision`/`verify_decision` 只读**当日**活体卡——对它们而言"今天"是唯一在场
#    的版本,今天的常量就是今天在生效的契约,禁令并不适用。现在两个 `parse_card_context`
#    调用点(正常路径 + 读取失败路径)都传 `_LIVE_EXEC_LINE_CONTRACT`(从
#    `contracts.agent_output` 导入数值、不重抄字面量),`exec_lines.*.contract_match` 在
#    活体卡上能真正读出 MATCH/DRIFTED。仍然**不带 `version`**:那两个常量没有独立于
#    `AGENT_OUTPUT_SCHEMA_VERSION` 的版本号,编一个会把"阈值有没有变"和"整份卡片契约
#    schema 有没有变"两件不同的事捆在一起,阈值改了但 schema 没跟着升版时会静默失配
#    ——没有版本号比编一个错的更诚实,`contract_version` 因此仍恒为 `None`。
if __name__ == "__main__":
    raise SystemExit(main())
