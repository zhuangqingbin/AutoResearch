#!/usr/bin/env python3
"""下一波候选账本 —— §0.4 的两张强制表变成一次会抛错的检查(确定性,零 LLM)。

design: docs/specs/2026-08-03-scan-next-wave-brainstorm-design.md §0.4 / §5 / §6

设计稿写了一句很容易被当成客套话的规矩:

> 每个候选立项前必须补齐两张表,**缺任一项不得转 implementation plan**:
> ① 继承矩阵(Wave9/Wave10/STAGES × 继承|替代|新增|需用户重裁)② evidence_manifest。

上一波的教训是「文档里的叮嘱不会自己生效」(Wave9 三个盲区、B4 漏搬 helper 都是这么来的)。
所以本模块把那两张表做成**数据结构 + 校验器**:

- 缺继承矩阵任一权威 → `CandidateError`,不是「review 时提醒一下」;
- B 类(改名单/证据/评级/阻断/调度)没有 `registry_family` → 抛错(D1 之后,该字段是
  文档性标签,不再有活的 registry 去交叉核验它 —— 2026-08-19 用户裁决 A3);
- `REJECTED` 没写重开条件 → 抛错(§3.2 O2 的教训:否决必须带可重开的门,否则半年后
  又有人拿同一个 t 值来提一遍);
- `BLOCKED_BY_DATA` 没写 capability gate → 抛错(§2.4 F3);
- 成本分栏缺列、或写了「零成本 / 可忽略」这类被 §5-4 点名删除的措辞 → 抛错。

**边界**:本账本记录**意图与权威**,它不改任何配置、名单、权重或提示词。它唯一的
权力是「拒绝让一个没补齐表的候选自称已立项」。行为变更的实际治理链条(D1 之后):
影子账本直接呈证 → proposal 交人批 → 开发会话改 config/代码,详见 SKILL.md「实验治理」节。

  uv run --no-sync python -m autoresearch.research.candidates            # 渲染报告
  uv run --no-sync python -m autoresearch.research.candidates --check    # CI 门(违约 → 退出码 1)
"""
from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict, dataclass
from pathlib import Path

from autoresearch.common import workspace as ws

SCHEMA_VERSION = 1
DEFAULT_OUT = ws.reports_root() / "research/next_wave_candidates.md"
DEFAULT_JSON = ws.reports_root() / "research/next_wave_candidates.json"

# ── §0.4-1 继承矩阵:三个权威 × 四种关系 ──────────────────────────────
AUTHORITIES = ("Wave9", "Wave10", "STAGES")
RELATIONS = ("继承", "替代", "新增", "需用户重裁", "无关")

# ── §0.4-3 变更类别:优先级与风险等级分开 ─────────────────────────────
CHANGE_CLASSES = {
    "M": "测量 —— 只新增 ledger/报表/探针,不进提示词、名单或阻断路径",
    "I": "数据基建 —— 写湖/索引/回放契约,消费者默认关闭",
    "B": "生产行为 —— 改名单/证据输入/评级/阻断/调度语义,必须 registry + shadow + rollback",
}
PRIORITIES = ("P0", "P1", "P2")
STATUSES = {
    "OPEN": "已登记,未开工",
    "PLANNED": "已转 implementation plan",
    "IMPLEMENTED": "软件已落地(B 类另需 registry 在册;软件完成 ≠ 研究成熟)",
    "IMMATURE": "已落地但样本/功效不足,不得据此下结论",
    "UNKNOWN": "区间过宽,既不能说有效也不能说无效",
    "REJECTED": "已否决 —— 必须写明可重开条件",
    "BLOCKED_BY_DATA": "被数据能力阻断 —— 必须写明 capability gate",
}

# ── §5-4 成本分栏:六列缺一不可 ───────────────────────────────────────
COST_COLUMNS = ("engineering", "data_storage", "network_ratelimit",
                "llm_tokens", "wallclock", "failure_risk")
# §5-4 原话:「『确定性脚本=零成本』『20KB≈token 可忽略』均删除」。写进代码,免得又滑回来。
BANNED_COST_PHRASES = ("零成本", "可忽略", "忽略不计", "不计成本", "negligible", "free")


class CandidateError(ValueError):
    """候选违反了 §0.4 的立项前置条件 —— 立项时就该失败,不能等 review 去发现。"""


@dataclass(frozen=True)
class Candidate:
    id: str
    title: str
    section: str                       # 设计稿章节号
    change_class: str                  # M | I | B
    priority: str                      # P0 | P1 | P2
    status: str
    inheritance: dict                  # 权威 → 关系(三个权威必须全覆盖)
    falsification_step: str            # §0.3-1 最小证伪步
    probe: str                         # §5-3 至少一个会变红的探针
    rollback: str
    cost: dict                         # §5-4 六列
    evidence_metrics: tuple = ()       # evidence_manifest 的 metric_id(§0.4-2)
    registry_family: str | None = None  # B 类必填
    reopen_conditions: str = ""        # REJECTED 必填
    capability_gate: tuple = ()        # BLOCKED_BY_DATA 必填
    blocked_by: tuple = ()
    note: str = ""

    def as_dict(self) -> dict:
        return asdict(self)


def validate_one(c: Candidate) -> list[str]:
    """单个候选的违约清单(空 = 合规)。纯函数,供 `validate` 与单测共用。"""
    bad: list[str] = []
    if c.change_class not in CHANGE_CLASSES:
        bad.append(f"change_class={c.change_class!r} 不在 {sorted(CHANGE_CLASSES)}")
    if c.priority not in PRIORITIES:
        bad.append(f"priority={c.priority!r} 不在 {list(PRIORITIES)}")
    if c.status not in STATUSES:
        bad.append(f"status={c.status!r} 不在 {sorted(STATUSES)}")

    missing_auth = [a for a in AUTHORITIES if a not in c.inheritance]
    if missing_auth:
        bad.append(f"继承矩阵缺权威 {missing_auth} —— §0.4-1 要求三个全覆盖,"
                   "不得再写「与 Wave10/Wave9 无重叠」")
    for authority, relation in c.inheritance.items():
        if authority not in AUTHORITIES:
            bad.append(f"继承矩阵含未知权威 {authority!r}")
        elif relation not in RELATIONS:
            bad.append(f"{authority} 的关系 {relation!r} 不在 {list(RELATIONS)}")

    for label, value in (("falsification_step", c.falsification_step),
                         ("probe", c.probe), ("rollback", c.rollback)):
        if not str(value).strip():
            bad.append(f"{label} 为空 —— §0.3-1/§5-3 强制每案各一条")

    missing_cost = [k for k in COST_COLUMNS if not str(c.cost.get(k, "")).strip()]
    if missing_cost:
        bad.append(f"成本分栏缺列 {missing_cost}(§5-4 六列缺一不可)")
    for key, value in c.cost.items():
        if key not in COST_COLUMNS:
            bad.append(f"成本分栏含未知列 {key!r}")
        for phrase in BANNED_COST_PHRASES:
            if phrase in str(value).lower():
                bad.append(f"成本列 {key} 含被 §5-4 删除的措辞 {phrase!r}:{value!r}")

    if c.change_class == "B" and not c.registry_family:
        bad.append("B 类候选缺 registry_family —— 行为变更必须挂在一个 trial_family 上")
    if c.change_class != "B" and c.registry_family:
        bad.append(f"{c.change_class} 类不该有 registry_family={c.registry_family!r}:"
                   "它若真要走 registry,说明它其实是 B 类")
    if c.status == "REJECTED" and not c.reopen_conditions.strip():
        bad.append("REJECTED 缺 reopen_conditions —— 否决必须带可重开的门(§3.2 判例)")
    if c.status == "BLOCKED_BY_DATA" and not c.capability_gate:
        bad.append("BLOCKED_BY_DATA 缺 capability_gate(§2.4 F3)")
    return bad


def validate(candidates=None) -> dict:
    """全账本校验 → `{ok, problems: {id: [...]}, n}`。

    D1(2026-08-19,用户裁决 A3):B 类 `IMPLEMENTED` 对照 registry 的交叉检查已随
    experiment_registry 家族整删而摘除 —— registry 不复存在,无从核对「是否真走过状态机」。
    `registry_family` 字段本身**保留**:它仍是 B 类候选「挂在哪个 trial family 下」的
    文档性标签(见 `validate_one`),只是不再有一个活的 registry 去交叉核验。
    """
    items = list(CANDIDATES if candidates is None else candidates)
    problems: dict[str, list[str]] = {}
    seen: set[str] = set()
    for c in items:
        bad = validate_one(c)
        if c.id in seen:
            bad.append(f"候选 id 重复:{c.id!r}")
        seen.add(c.id)
        if bad:
            problems[c.id] = bad

    return {"ok": not problems, "problems": problems, "n": len(items)}


# ══════════════════════════ 候选池(设计稿全文的机器可读投影)══════════════════════════
#
# 每一条都对应设计稿的一节。改这里 = 改立项事实,必须过 review。
# 状态语义提醒(Wave1-5 的判例):**软件完成 ≠ 研究成熟** —— `IMPLEMENTED` 只说代码在,
# 说的不是这条腿的结论可以引用。

def _cost(engineering: str, storage: str, network: str, llm: str,
          wallclock: str, risk: str) -> dict:
    return dict(zip(COST_COLUMNS,
                    (engineering, storage, network, llm, wallclock, risk), strict=True))


CANDIDATES: tuple[Candidate, ...] = (
    # ───────────────────────── §1 新闻 ─────────────────────────
    Candidate(
        id="D1_news_catalog", title="统一新闻观测目录 news_catalog",
        section="§1.1", change_class="I", priority="P1", status="IMPLEMENTED",
        inheritance={"Wave9": "继承", "Wave10": "无关", "STAGES": "新增"},
        falsification_step="先对既有 stock_news_em/anns_d 分片生成 1 日 manifest,用转载/更正 "
                           "fixture 验 canonical/observation 分离,并验 L3 与 L4 两个 cutoff 不互污染",
        probe="first_seen 缺失率必须=0;late-arrival fixture 不得泄漏进更早的 cutoff 回放",
        rollback="目录消费者关闭即可;原始湖不迁不毁不覆盖",
        cost=_cost("1 个模块 + 1 套单测", "manifest 为指纹表,原文仍在各自湖;字节数以实测报表为准",
                   "夜间批量,受既有 akshare/tushare 限频约束", "0(确定性,不进提示词)",
                   "分钟级 inventory 扫描", "源 schema 漂移 → 契约记账降级"),
        evidence_metrics=("news_catalog.observations", "news_catalog.first_seen_missing_rate"),
        note="核心诊断不是「全仓无新闻存储」,而是没有统一观测清单回答「某阶段截止时系统看到了什么」",
    ),
    Candidate(
        id="D1C1_replay_diagnostic", title="复盘重放:retro/t1 增「当时已可见新闻面」",
        section="§1.1-消费1", change_class="M", priority="P1", status="IMPLEMENTED",
        inheritance={"Wave9": "无关", "Wave10": "继承", "STAGES": "新增"},
        falsification_step="只写诊断 artifact;一旦进入判断 prompt 即转 B 类重新立项",
        probe="同一 (run, stage) 回放两次结果必须逐值一致",
        rollback="删诊断段落即可",
        cost=_cost("复用 D1 的 replay 接口", "诊断文本随 retro 产物", "0(读目录不联网)",
                   "0", "秒级", "目录缺失 → 该节缺省不渲染"),
        blocked_by=("D1_news_catalog",),
    ),
    Candidate(
        id="D1C2_intel_reads_catalog", title="l4-intel 先读目录再补查",
        section="§1.1-消费2", change_class="B", priority="P2", status="OPEN",
        inheritance={"Wave9": "替代", "Wave10": "无关", "STAGES": "替代"},
        registry_family="l4_intel_prompt",
        falsification_step="影子对比:调用数/耗时/覆盖/错引/终局质量,网查数必须来自真实 "
                           "tool-call telemetry,不得用稿件自报",
        probe="telemetry 记录的 search/fetch 次数与稿件自报数并列存,两者背离即告警",
        rollback="registry rollback 到当前 intel prompt 指针",
        cost=_cost("prompt 改写 + 影子对照", "复用 D1 目录", "预期下降(先复用后补查)",
                   "input 增(注入摘要)/ output 预期降,须实测 prompt delta",
                   "预期下降", "复用错过更正 → 需更正/缺口专查兜底"),
        blocked_by=("D1_news_catalog", "D3_claim_ledger"),
    ),
    Candidate(
        id="D1C3_l3_second_source", title="L3 公告第二源:目录 → cninfo 实时",
        section="§1.1-消费3", change_class="B", priority="P2", status="OPEN",
        inheritance={"Wave9": "替代", "Wave10": "无关", "STAGES": "替代"},
        registry_family="l3_news_source",
        falsification_step="命中目录即免网络:先测命中率与空桶残留率,再谈换序",
        probe="换序前后 L3 表的公告行必须逐值可 diff,差异必须能归因到具体 observation",
        rollback="恢复 fallback 单源顺序",
        cost=_cost("改 l3_news 兜底顺序", "复用目录", "预期下降", "0(确定性段)",
                   "预期下降", "目录过期 → 回落实时路径"),
        blocked_by=("D1_news_catalog",),
    ),
    Candidate(
        id="D2_typed_events", title="确定性事件类型学(多标签 + 生命周期)",
        section="§1.2", change_class="I", priority="P1", status="IMPLEMENTED",
        inheritance={"Wave9": "继承", "Wave10": "无关", "STAGES": "新增"},
        falsification_step="先做 PIT 覆盖矩阵:fallback 是逐票近窗,不得假设已有全市场历史湖",
        probe="生命周期分桶(预案/实施/完成/终止)必须能在 fixture 上分开;"
              "同一标题同时命中多标签时不得被压成单 type",
        rollback="artifact 无消费者,删除即可",
        cost=_cost("规则表 + 覆盖矩阵", "结构化 parquet/csv", "0(读目录)", "0",
                   "秒级/日", "词表漂移 → rule_version 分版留痕"),
        evidence_metrics=("typed_events.coverage_rate",),
        note="业绩类不做追涨信号(附录E 已否);非业绩类也不得用当日涨幅入场",
        blocked_by=("D1_news_catalog",),
    ),
    Candidate(
        id="D3_claim_ledger", title="情报治理 v2:claim ledger + lint 分层 + 日期焊接检测",
        section="§1.3", change_class="I", priority="P1", status="IMPLEMENTED",
        inheritance={"Wave9": "继承", "Wave10": "继承", "STAGES": "替代"},
        falsification_step="先只做 ledger 与三段 verdict;缺引用/错引只拒 intel 稿、不拒票",
        probe="对不上日期的 claim 必须落 UNVERIFIED 而非 FALSE(覆盖不足时不得自动判假)",
        rollback="lint 降回原单层 guard",
        cost=_cost("解析器 + ledger + lint 分层", "claim 行级 csv",
                   "verify 阶段可选联网,默认关", "0(确定性解析)", "秒级/稿",
                   "解析失败 → 该 claim 标 UNPARSED,不拒票"),
        evidence_metrics=("claim_ledger.unverified_rate",),
    ),
    Candidate(
        id="D4_finalist_fulltext", title="finalist 公告正文(≤10 只/日)",
        section="§1.4", change_class="I", priority="P1", status="IMPLEMENTED",
        inheritance={"Wave9": "继承", "Wave10": "无关", "STAGES": "新增"},
        falsification_step="先测中文 PDF 抽取后的**实际 prompt delta**(真 tokenizer),"
                           "不用「2,000 字节≈2KB token」这类换算",
        probe="注入摘录必须带 source_observation_id + page + excerpt_hash;缺一即拒注",
        rollback="不注入即回到现状(D4 是证据增强,不是门读全文)",
        cost=_cost("抓取 + 抽取 + 预算器", "PDF/HTML 冷存储单列计量",
                   "≤10 只/日 × 近 10 日公告", "input 增量以实测 prompt delta 为准",
                   "分钟级", "抽取失败 → 该票无摘录,不阻断"),
        blocked_by=("D1_news_catalog",),
    ),
    # ───────────────────────── §2 衍生品 ─────────────────────────
    Candidate(
        id="F1_options_terrain", title="期权市场地形(PCR / 持仓结构)",
        section="§2.2", change_class="I", priority="P1", status="IMPLEMENTED",
        inheritance={"Wave9": "替代", "Wave10": "无关", "STAGES": "新增"},
        falsification_step="lagged PCR/ΔPCR/zscore 对次日 breadth 或 regime transition 的**增量**;"
                           "同期相关不算领先证据",
        probe="opt_basic 必须强制分页(首页恰 12,000 = 分页上限而非全量);"
              "coverage 对账不平即 fail",
        rollback="market_pack.derivatives 键删除即可(未进 strategist allowlist)",
        cost=_cost("分页拉取 + 联结 + 分桶", "期权日频 parquet",
                   "opt_basic 分页多次调用", "0(不进 prompt)", "分钟级/日",
                   "限频/权限波动 → 当日 derivatives 缺省"),
        evidence_metrics=("options.pcr_coverage",),
        note="PCR 不能区分买 put 与卖 put,不得直接标「看空」;跨产品需名义/delta 归一",
    ),
    Candidate(
        id="F1b_iv_surface", title="F1 二期:IV 分位 / 25Δ skew / 期限结构",
        section="§2.2-二期", change_class="I", priority="P2", status="OPEN",
        inheritance={"Wave9": "替代", "Wave10": "无关", "STAGES": "新增"},
        falsification_step="QVIX 只提供波动率指数/分位,不能替代 25Δ skew;自算 IV 先验 "
                           "put-call parity 推隐含远期/分红,不得固定 q=0",
        probe="solver 失败率、no-arbitrage 违反数、到期天数与流动性过滤后的样本数逐日留痕",
        rollback="不产出该组字段",
        cost=_cost("IV solver + 无套利过滤", "曲面快照", "同 F1",
                   "0", "分钟级", "solver 不收敛 → 该合约剔除并记账"),
        blocked_by=("F1_options_terrain",),
    ),
    Candidate(
        id="F2_style_spread", title="风格温差(MO 中证1000 vs IO 沪深300)",
        section="§2.3", change_class="I", priority="P1", status="IMPLEMENTED",
        inheritance={"Wave9": "替代", "Wave10": "无关", "STAGES": "新增"},
        falsification_step="先分别构建期限匹配、换月调整的指标再比较;原始 PCR 差与成交额比"
                           "只能是探索特征",
        probe="期限对齐失败(两边最近月不同)时必须报 UNALIGNED,不得裸比",
        rollback="仅展示,不进 regime;删字段即回滚",
        cost=_cost("期限对齐 + z-score", "复用 F1 湖", "0(复用)", "0", "秒级",
                   "CFFEX 合约换月 → 对齐失败即缺省"),
        blocked_by=("F1_options_terrain",),
        note="只有对次日 breadth/regime transition 有稳定增量且 no-harm 过线,才提 regime 第四信号",
    ),
    Candidate(
        id="F3_cb_factors", title="可转债个股候选(CB return / underlying / premium)",
        section="§2.4", change_class="I", priority="P2", status="BLOCKED_BY_DATA",
        inheritance={"Wave9": "替代", "Wave10": "无关", "STAGES": "新增"},
        falsification_step="capability gate 四条不过则整线停在数据可行性,不回填伪 PIT 因子",
        probe="cb_basic.conv_price 是当前截面 —— 拿它重算历史转股价值即前视,"
              "gate 必须验历史转股价调整序列可按 first_seen_ts 回放",
        rollback="不排产",
        capability_gate=(
            "历史转股价调整序列或公告解析可得,并能按 first_seen_ts 回放",
            "cb_call/公告能标强赎、到期、上市/退市与暂停窗口",
            "多债映射一只正股有确定聚合规则,成交额单位/合约口径完成对账",
            "新债、低流动性与妖债过滤预注册;缺失不与无转债股票直接横比",
        ),
        cost=_cost("待 gate 通过后估", "待估", "cb_price_chg 当前 token 无权限",
                   "0", "待估", "权限缺失 → 整线 BLOCKED"),
        note="「数据全通、一晚跑三因子」的原结论已撤销;premium_delta 的符号不天然代表抢跑",
    ),
    # ───────────────────────── §3 L2 ─────────────────────────
    Candidate(
        id="O1_winner_capture_slo", title="winner-capture@K 升格 SLO(端到端 + 条件召回)",
        section="§3.1", change_class="M", priority="P0", status="IMPLEMENTED",
        inheritance={"Wave9": "无关", "Wave10": "继承", "STAGES": "替代"},
        falsification_step="先固定 winner 定义(主尺 ruler.MAIN_RULER,现 gap_c1_o2 + top-decile "
                           "+ 绝对阈 + 入场腿可买 entry_flag_for());"
                           "不得把 retro 的复合 winner 与纯 top-decile 混叫一个标签",
        probe="报警线必须用当日**之前**的 expanding P25;拿全期分位会让回看时永远不报警",
        rollback="账本无消费者,删报告即可",
        cost=_cost("SLO 计算 + 报告", "逐日 csv", "0(读既有产物)", "0", "秒级",
                   "attribution 缺失日 → 该日标 IMMATURE 而非 0"),
        evidence_metrics=("l2_slo.wc_l2_all", "l2_slo.wc_l2_given_l1"),
        note="winner capture 是主 SLO 但不是唯一指标 —— 扩大 K 或集中追热点可被动做高",
    ),
    Candidate(
        id="O2_dist_high_252", title="52 周高距离族",
        section="§3.2", change_class="B", priority="P2", status="REJECTED",
        inheritance={"Wave9": "无关", "Wave10": "无关", "STAGES": "继承"},
        registry_family="l1_composite_factor",
        falsification_step="已完成:全样本 rank-IC −0.0023、两半反号、risk_off IC −0.082(t=−2.29)",
        probe="守卫测试:该因子族不得出现在 composite 权重表或 L2 floor 桶里",
        rollback="不适用(未上线)",
        reopen_conditions="① 预注册 top-decile 阈值旗而非线性因子;② 使用新增 OOS;"
                          "③ 单列 risk_off no-harm;④ 通过统一成熟门。四条全满足前状态恒 REJECTED",
        cost=_cost("0(不排产)", "0", "0", "0", "0", "误以为它「优于 pct_60d」即等于有效"),
        note="t≈+2.62 只表示它优于更差的 pct_60d,不表示绝对有效",
    ),
    Candidate(
        id="O3_regime_caps", title="L2 regime 化 sector cap(半特性清算)",
        section="§3.3", change_class="B", priority="P2", status="OPEN",
        inheritance={"Wave9": "无关", "Wave10": "无关", "STAGES": "替代"},
        registry_family="l2_regime_caps",
        falsification_step="P0 只清算:形参已建未接线(universe 全部调用点均未传),"
                           "先补文档与探针;真接入会改 L2 构成 → B 类 challenger",
        probe="接线探针:universe 生产调用点一旦开始传 regime_caps 而 registry 无对应 "
              "ACTIVE 实验,测试必须变红",
        rollback="不传 regime/regime_caps 即回到固定 cap",
        cost=_cost("variant contract + replay 对照", "replay 输出根", "0",
                   "0(L2 零 LLM)", "replay 网格按日数计", "risk_off 仅 11 日 → 恒 IMMATURE"),
        note="挂着参数没人喂,下一个读代码的人会以为 regime 化已生效 —— §0.3-5 的形态",
    ),
    Candidate(
        id="O4_cap_floor_grid", title="cap/floor 参数扫描(replay 网格)",
        section="§3.4", change_class="I", priority="P1", status="IMPLEMENTED",
        inheritance={"Wave9": "无关", "Wave10": "无关", "STAGES": "替代"},
        falsification_step="先补 replay variant_spec + definition_hash + 独立输出根,"
                           "防 staging 幂等误复用 baseline",
        probe="两个不同 variant 的 definition_hash 必须不同,且输出根不得互相覆盖",
        rollback="网格产物在独立根下,删目录即可",
        cost=_cost("网格编排", "每 variant 一个输出根 —— 磁盘按 variant×日数增长",
                   "回放逐日取数(受 tushare 限频)", "0", "日数 × variant 数 × 单日回放时长",
                   "单日失败不中断整段,但会让该 variant 样本变薄"),
        note="capfloor20 测的是 L0 市值 floor,只可复用对照框架,不是 L2 sector cap 的近亲实证",
    ),
    Candidate(
        id="O5_feature_gate", title="新特征统一闸门(治理条款)",
        section="§3.5", change_class="M", priority="P0", status="IMPLEMENTED",
        inheritance={"Wave9": "继承", "Wave10": "继承", "STAGES": "替代"},
        falsification_step="唯一入口:capability/PIT gate → factor_lab → replay 对照 → "
                           "registry challenger。没有第二条路",
        probe="闸门检查器对「跳过 factor_lab 直接进 composite」的特征必须报 BLOCKED",
        rollback="闸门只裁决不改配置,无需回滚",
        cost=_cost("检查器 + 单测", "阶段状态 json", "0", "0", "毫秒级",
                   "闸门本身失效 → 特征绕过治理"),
    ),
    # ───────────────────────── §4 全景四件 ─────────────────────────
    Candidate(
        id="G41_gate_manifest_fix", title="「业绩真兑现」门 evidence_manifest 勘误",
        section="§4.1", change_class="M", priority="P0", status="IMPLEMENTED",
        inheritance={"Wave9": "无关", "Wave10": "替代", "STAGES": "继承"},
        falsification_step="08-03 prelude 的「拦11/拦对25%/错杀60%」来自 cross_calib 的另一分组,"
                           "不是 A11 v3 单门 attribution —— 先把 manifest 钉死在 v3",
        probe="shrink 不得出现在裁门路径上:守卫测试对「用收缩值决定门去留」必须变红",
        rollback="不适用(勘误)",
        cost=_cost("manifest 语义扩表 + 守卫", "复用既有账本", "0", "0", "秒级",
                   "口径再次被混引"),
        evidence_metrics=("gate_v3.业绩真兑现.false_kill_rate",
                          "gate_v3.业绩真兑现.correct_rate"),
        note="当前裁定 = IMMATURE(v3 单门 n=6:CORRECT 2 / NEUTRAL 2 / FALSE_KILL 2)",
    ),
    Candidate(
        id="G41_gate_evidence_exp", title="gate_true_delivery_evidence(D4 有/无全文 paired shadow)",
        section="§4.1-实验1", change_class="B", priority="P2", status="OPEN",
        inheritance={"Wave9": "无关", "Wave10": "替代", "STAGES": "继承"},
        registry_family="gate_true_delivery_evidence",
        falsification_step="同一候选、同一门版本;只裁证据是否改变 claim 正确率与门分歧,"
                           "不裁门松紧",
        probe="treatment 两臂必须共享同一门版本 hash,不同即拒绝出结论",
        rollback="registry rollback;实验期间门不变",
        cost=_cost("影子编排", "两臂产物", "D4 抓取", "两臂 prompt 各计一次",
                   "同日双跑", "两臂配对断裂 → 该日作废"),
        blocked_by=("D4_finalist_fulltext",),
    ),
    Candidate(
        id="G41_gate_recal_exp", title="gate_true_delivery_recal(现门不变,只记 counterfactual)",
        section="§4.1-实验2", change_class="B", priority="P2", status="OPEN",
        inheritance={"Wave9": "无关", "Wave10": "替代", "STAGES": "继承"},
        registry_family="gate_true_delivery_recal",
        falsification_step="在完整 gate participation cohort 上记录每个 binding/counterfactual "
                           "结果;禁止直接放宽,只生成 shadow verdict",
        probe="影子腿不得写生产 decision_records;写入即变红",
        rollback="删影子账本",
        cost=_cost("影子判据重算", "counterfactual csv", "0", "0", "秒级",
                   "样本不足 20 个 binding 成熟事件 → 恒 IMMATURE"),
        note="至少 20 个真实 binding 成熟事件且功效足够才可 RECOMMENDED;"
             "门总量价值只能作背景守卫,不能替代本门归因",
    ),
    Candidate(
        id="G42_nightly_hardening", title="闭环债自动化:nightly runner 加固",
        section="§4.2", change_class="I", priority="P0", status="IMPLEMENTED",
        inheritance={"Wave9": "无关", "Wave10": "继承", "STAGES": "替代"},
        falsification_step="加固既有 nightly_close,不从零新建;`0 rows` 可能是合法 NOOP,"
                           "不能单靠行数判活",
        probe="互斥锁必须真的挡住第二个实例;心跳/run ledger 必须随真实输入债务变化",
        rollback="卸载 launchd plist;手动跑法不变",
        cost=_cost("锁/心跳/幂等/catch-up + plist", "run ledger jsonl",
                   "夜间批量受限频约束", "0(确定性段;LLM 诊断腿仍人工)",
                   "夜间窗口", "与当日 scan 抢写 task book/T1/dossier"),
        evidence_metrics=("nightly.runs", "nightly.stale_lock_recoveries"),
        note="「债务导致权重停在旧教训」目前无因果证据,已从设计依据删除",
    ),
    Candidate(
        id="G42_gate0_hard_block", title="债务硬闸 GATE0/preflight",
        section="§4.2-4", change_class="B", priority="P2", status="OPEN",
        inheritance={"Wave9": "无关", "Wave10": "继承", "STAGES": "新增"},
        registry_family="scan_preflight_gate",
        falsification_step="当前 GATE1 在 L2 之后,不能声称「不开始扫描」;真硬闸须在 "
                           "frame/universe/LLM 之前另设 GATE0",
        probe="override 必须记 actor/reason/expiry;过期 override 必须失效",
        rollback="policy 降回 advisory",
        cost=_cost("preflight 挂载点", "policy 状态", "0", "0", "毫秒级",
                   "可用性风险 —— 需 availability SLO + 故障演练"),
        note="硬闸属 B 类可用性变更,不进零风险 P0;数据完整性/成熟标签污染才考虑 hard block",
    ),
    Candidate(
        id="G43_nested_l4_dispatch", title="L4 派发下沉(workflow 嵌套滑窗)",
        section="§4.3", change_class="B", priority="P1", status="OPEN",
        inheritance={"Wave9": "需用户重裁", "Wave10": "无关", "STAGES": "替代"},
        registry_family="l4_dispatch_topology",
        falsification_step="先跑 capability/chaos probe:基本调用、args/result、并发上限、"
                           "父取消、子失败、timeout、parent death、resumeFromRunId、"
                           "task-book lease/heartbeat、重复执行幂等",
        probe="probe 账本必须逐项记 PASS/FAIL/UNTESTED;UNTESTED 不得被读成 PASS",
        rollback="保留现行主会话滑窗",
        cost=_cost("probe + A/B telemetry", "probe 账本",
                   "0", "A/B 两臂 end-to-end prompt/cache/token 实测",
                   "墙钟为 A/B 主要读数之一", "父子失败语义未验证 → 可能丢票"),
        note="与 Wave9 的 L4 单工作流全链直接重叠,必须标『需用户重裁』,不得并行两套权威;"
             "主会话 15% 与每日省 $5–8 均为**待测假设**",
    ),
    Candidate(
        id="G44_l3_marginal", title="L3 边际价值实验(排序价值计量)",
        section="§4.4", change_class="M", priority="P0", status="IMPLEMENTED",
        inheritance={"Wave9": "无关", "Wave10": "继承", "STAGES": "替代"},
        falsification_step="先补可复原性:pass1 是 union/floor/round-robin 选择集,"
                           "`_l3_pass1_cut.csv` 不能天然定义 top-K 反事实",
        probe="kept 必须带 selection_reason;缺 reason 的行会让 tier-1 反事实无从构造 → 变红",
        rollback="账本无消费者",
        cost=_cost("kept 产物 + tier-1 估计器", "每日 kept csv", "0", "0", "秒级",
                   "样本不足 → UNKNOWN(不得读成「排序无价值」)"),
        evidence_metrics=("l3_marginal.excess2_delta",),
        note="结论只裁排序价值,不自动撤销 L3 的证据组装、红队与压缩职责",
    ),
    Candidate(
        id="G45_consensus_prereg", title="consensus 自动预注册触发",
        section="§4.5", change_class="M", priority="P1", status="IMPLEMENTED",
        inheritance={"Wave9": "无关", "Wave10": "继承", "STAGES": "继承"},
        falsification_step="触发条款写死:n≥60 且两半 IC 同号 且 |IC|>0.02 → 生成 "
                           "PREREGISTERED spec(人批才往前走)",
        probe="自动腿断言:status 输出的 n 必须周周增长 —— 不增长即这条腿死了(§0.3-4 判例)",
        rollback="删自动生成的 spec(未 approve 即无生产影响)",
        cost=_cost("触发器 + 断言", "spec json", "report_rc 限频 1 次/小时",
                   "0", "秒级", "限频导致 n 停滞 → 断言变红(正是它该做的)"),
    ),
    Candidate(
        id="G45_temperature_v2", title="温度 v2:衍生品/新闻量入市场温度",
        section="§4.5", change_class="B", priority="P2", status="OPEN",
        inheritance={"Wave9": "无关", "Wave10": "无关", "STAGES": "替代"},
        registry_family="market_temperature",
        falsification_step="F1 只能用归一、换月调整且有领先性证据的读数;D1 只有固定全局 feed、"
                           "覆盖/freshness 归一后的新闻量可作候选",
        probe="选择性 L2 逐票抓取禁止进入市场温度 —— 守卫必须拒绝 selective 来源",
        rollback="温度序列回落现有五序列",
        cost=_cost("温度扩序列 + 重标定", "复用 F1/D1", "0", "0", "秒级",
                   "选择偏差混入 → 温度失真"),
        blocked_by=("F1_options_terrain", "D1_news_catalog"),
    ),
    Candidate(
        id="G45_dossier_debt", title="档案债清偿排期(季度对账 + 待建档)",
        section="§4.5", change_class="M", priority="P1", status="IMPLEMENTED",
        inheritance={"Wave9": "无关", "Wave10": "继承", "STAGES": "继承"},
        falsification_step="不新建机制,纯排期:季度对账 20251231 × 19 只一次批跑;"
                           "18 只待建档按 ≤3/晚 ≈ 6 晚消化",
        probe="排期表必须由真实 pool/ledger 派生 —— 池子变了排期就得变",
        rollback="不排期即回到人肉记得",
        cost=_cost("排期渲染", "复用 dossier 账本", "首覆走既有 prefetch",
                   "首覆为 LLM 段(≤3/晚)", "夜间", "池扩张快于消化 → 债务不收敛"),
    ),
)


# ───────────────────────── 渲染 / CLI ─────────────────────────


def _cell(value) -> str:
    return str(value).replace("|", r"\|").replace("\n", " ") if value not in (None, "") else "—"


def render(candidates=None, result: dict | None = None) -> str:
    items = list(CANDIDATES if candidates is None else candidates)
    result = result or validate(items)
    # 嵌套引号的 f-string 是 3.12+ 语法,而本项目 requires-python = ">=3.10" —— 先算好再插值。
    verdict = "通过" if result["ok"] else f"{len(result['problems'])} 条违约"
    lines = [
        "# 下一波候选账本(§0.4 两张强制表的机器可读投影)",
        "",
        "> 本账本记录**意图与权威**,不改任何配置、名单、权重或提示词。"
        "它唯一的权力是拒绝让没补齐表的候选自称已立项。",
        "",
        f"- schema_version `{SCHEMA_VERSION}` · 候选 {len(items)} 条 · 校验 {verdict}",
        "",
        "## 变更类别",
        "",
        "| 类 | 含义 |", "|---|---|",
    ]
    lines += [f"| `{k}` | {v} |" for k, v in CHANGE_CLASSES.items()]
    lines += ["", "## 候选 × 继承矩阵", "",
              "| id | 章节 | 类 | 优先级 | 状态 | Wave9 | Wave10 | STAGES | registry family |",
              "|---|---|---|---|---|---|---|---|---|"]
    for c in items:
        lines.append("| " + " | ".join(_cell(v) for v in (
            f"`{c.id}`", c.section, c.change_class, c.priority, c.status,
            c.inheritance.get("Wave9"), c.inheritance.get("Wave10"),
            c.inheritance.get("STAGES"), c.registry_family)) + " |")

    lines += ["", "## 逐条:最小证伪步 / 探针 / 回滚 / 成本", ""]
    for c in items:
        lines += [f"### `{c.id}` —— {c.title}", "",
                  f"- 章节 {c.section} · 类别 **{c.change_class}** · {c.priority} · 状态 **{c.status}**",
                  f"- 最小证伪步:{c.falsification_step}",
                  f"- 会变红的探针:{c.probe}",
                  f"- 回滚:{c.rollback}"]
        if c.evidence_metrics:
            lines.append("- evidence_manifest 指标:"
                         + "、".join(f"`{m}`" for m in c.evidence_metrics))
        if c.capability_gate:
            lines.append("- capability gate:")
            lines += [f"  {i}. {g}" for i, g in enumerate(c.capability_gate, 1)]
        if c.reopen_conditions:
            lines.append(f"- 可重开条件:{c.reopen_conditions}")
        if c.blocked_by:
            lines.append("- 依赖:" + "、".join(f"`{b}`" for b in c.blocked_by))
        if c.note:
            lines.append(f"- 注:{c.note}")
        lines += ["", "| " + " | ".join(COST_COLUMNS) + " |",
                  "|" + "|".join(["---"] * len(COST_COLUMNS)) + "|",
                  "| " + " | ".join(_cell(c.cost.get(k)) for k in COST_COLUMNS) + " |", ""]

    lines += ["## 校验", ""]
    if result["ok"]:
        lines.append("_全部候选补齐 §0.4 两张表。_")
    else:
        for cid, problems in sorted(result["problems"].items()):
            lines += [f"- ⚠️ `{cid}`:"] + [f"  - {p}" for p in problems]
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="下一波候选账本(§0.4 立项前置检查)")
    ap.add_argument("--check", action="store_true", help="只校验;有违约 → 退出码 1")
    ap.add_argument("--out", default=str(DEFAULT_OUT))
    ap.add_argument("--json", dest="json_out", default=str(DEFAULT_JSON))
    a = ap.parse_args(argv)

    result = validate()
    if a.check:
        for cid, problems in sorted(result["problems"].items()):
            for p in problems:
                print(f"  ✗ {cid}: {p}", file=sys.stderr)
        print(f"[candidates] {result['n']} 条候选 —— "
              + ("全部合规" if result["ok"] else f"{len(result['problems'])} 条违约"))
        return 0 if result["ok"] else 1

    out = Path(a.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(render(result=result), encoding="utf-8")
    payload = {"schema_version": SCHEMA_VERSION,
               "candidates": [c.as_dict() for c in CANDIDATES],
               "validation": result}
    jp = Path(a.json_out)
    jp.parent.mkdir(parents=True, exist_ok=True)
    jp.write_text(json.dumps(payload, ensure_ascii=False, indent=2,
                             default=list) + "\n", encoding="utf-8")
    print(f"[candidates] {result['n']} 条 → {out} / {jp}"
          + ("" if result["ok"] else f";⚠️ {len(result['problems'])} 条违约"))
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
