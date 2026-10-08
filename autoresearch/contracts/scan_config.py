"""scan_config.jsonc 注册表 —— 「配置标准」的机器真身(2026-09-27)。

`.claude/skills/scan-market/scan_config.jsonc` 放**值**,本文件放**事实**:每个键属于哪个块、
哪个分区、什么类型、代码内建缺省是多少、由哪一侧宿主读、真实消费者是哪些函数。
`autoresearch/scan/user_config.py` 的三张白名单表(顶层块 / 子键 / 类型)全部从这里派生,
`autoresearch/scan/config_standard.py` 拿它对账现场(九条规则,见 SKILL.md「配置」节),
`tests/scan/test_config_standard.py` 与 PostToolUse hook 是它的两只手。

登记纪律:
- 没有消费者的键不许登记(R2);消费者写成 ``pkg.module:function``(该函数源码里必须出现
  这个键名字面量)、``pkg.module``(整模块)或 ``.claude/workflows/x.js``(整文件)。
- ``hosts``:``python`` = 两宿主共用的 Python 代码读;``js`` = 只有 legacy workflow 读;
  ``sa`` = 只有 session_agent 读;``js+sa`` = 两侧编排各自读(须各登记一个消费者)。
- ``default`` = 代码内建缺省(删 key 即回落的值);``None`` = 各消费点自定,R7 不对账。
- ``kind``:``scalar`` 单值;``data`` 数据字典(内层键是数据不是参数);``group`` 参数组
  (内层每个键都是参数,须逐行注释,``children`` 列全);``structured`` 结构块(agents 类,
  形状由 resolve_agent_bundle 校验)。
- 块顺序 = 文件顺序;分区一(漏斗行为)必须整体排在分区二(运行时)之前。
"""
from __future__ import annotations

import math
import re
from dataclasses import dataclass, field

# ── 词汇 ────────────────────────────────────────────────────────────────────
ZONE_BEHAVIOR = "behavior"
ZONE_RUNTIME = "runtime"
ZONES = (ZONE_BEHAVIOR, ZONE_RUNTIME)
ZONE_TITLES = {
    ZONE_BEHAVIOR: "分区一 · 漏斗行为(改值会改变选股 / 评级 / BUY)",
    ZONE_RUNTIME: "分区二 · 运行时(改值只影响成本 / 速度 / 时序 / 通知 / 留存)",
}

HOST_PYTHON = "python"
HOST_JS = "js"
HOST_SA = "sa"
HOST_BOTH = "js+sa"
HOSTS = (HOST_PYTHON, HOST_JS, HOST_SA, HOST_BOTH)
_HOST_TAGS = {HOST_JS: "仅 legacy workflow", HOST_SA: "仅 session_agent"}

KIND_SCALAR = "scalar"
KIND_DATA = "data"
KIND_GROUP = "group"
KIND_STRUCTURED = "structured"
KINDS = (KIND_SCALAR, KIND_DATA, KIND_GROUP, KIND_STRUCTURED)

CONSTANT_REASON_KINDS = ("P2 待迁", "P3 待迁", "不进 config")

# ── 类型校验器(原 user_config._t_*,搬到这里成为单源)────────────────────────
_DATE_RE = re.compile(r"\d{4}-\d{2}-\d{2}")


def _t_any(v): return True
def _t_num(v): return isinstance(v, (int, float)) and not isinstance(v, bool)
def _t_bool(v): return isinstance(v, bool)
def _t_str(v): return isinstance(v, str) and v.strip() != ""
def _t_posint(v): return isinstance(v, int) and not isinstance(v, bool) and v > 0
def _t_posnum(v): return _t_num(v) and math.isfinite(v) and v > 0
def _t_nonneg(v): return _t_num(v) and v >= 0
def _t_nonneg_int(v): return isinstance(v, int) and not isinstance(v, bool) and v >= 0
def _t_fraction(v): return _t_num(v) and 0 <= v <= 1
def _t_source(v): return v in {"em", "tushare"}
def _t_dict(v): return isinstance(v, dict)
def _t_int_dict(v): return isinstance(v, dict) and all(_t_nonneg_int(x) for x in v.values())
def _t_num_dict(v): return isinstance(v, dict) and all(_t_nonneg(x) for x in v.values())
def _t_str_list(v): return isinstance(v, (list, tuple)) and all(isinstance(x, str) and x for x in v)
def _t_rbmode(v): return v in {"shadow", "active"}
def _t_rbpool(v): return v in {"finalists", "composite"}
def _t_date_or_null(v): return v is None or (isinstance(v, str) and _DATE_RE.fullmatch(v) is not None)
def _t_profile(v): return v in {"calibrated", "preference"}
def _t_channel(v): return isinstance(v, str) and v in {"none", "bark", "mail", "file"}
def _t_path_or_null(v): return v is None or (isinstance(v, str) and v.strip() != "")
def _t_num_list(v): return isinstance(v, (list, tuple)) and len(v) >= 1 and all(_t_num(x) for x in v)
def _t_recall_mode(v): return v in {"multi", "composite"}
def _t_clock(v): return isinstance(v, str) and re.fullmatch(r"([01]?\d|2[0-3]):[0-5]\d", v) is not None


#: L1 十个因子组名(`common/scoring._GROUPS` 从这里转出,契约层单源;顺序即权重字典的键序)。
PREFERENCE_GROUPS: tuple[str, ...] = ("momentum", "fund_main", "fund_retail", "chip", "north",
                                      "tech", "growth", "value", "volprice", "rz")


def _t_pref_weights(v):
    """键集恰为 `PREFERENCE_GROUPS`、逐个有限、且绝对值之和≠0。

    全零权重会让 `combine_group_scores` 的 Σ|w| 为 0 → composite 全 NaN → L2 退化成输入行序
    且不抛异常;键集 / 有限性 / 和 三项各自独立判,单独失败不被后面的检查吞掉。
    """
    if not isinstance(v, dict) or set(v) != set(PREFERENCE_GROUPS):
        return False
    if not all(_t_num(x) and math.isfinite(x) for x in v.values()):
        return False
    return sum(abs(float(x)) for x in v.values()) > 0


#: type 名 → (校验函数, 报错时给人看的期望描述)
VALIDATORS: dict[str, tuple] = {
    "any": (_t_any, "任意"),
    "num": (_t_num, "number"),
    "bool": (_t_bool, "boolean"),
    "str": (_t_str, "非空字符串"),
    "posint": (_t_posint, "正整数"),
    "posnum": (_t_posnum, "number>0"),
    "nonneg": (_t_nonneg, "number≥0"),
    "nonneg_int": (_t_nonneg_int, "int≥0"),
    "fraction": (_t_fraction, "0~1 的数"),
    "source": (_t_source, "em|tushare"),
    "dict": (_t_dict, "object"),
    "int_dict": (_t_int_dict, "object:值全为 int≥0"),
    "num_dict": (_t_num_dict, "object:值全为 number≥0"),
    "str_list": (_t_str_list, "非空字符串数组"),
    "rbmode": (_t_rbmode, "shadow|active"),
    "rbpool": (_t_rbpool, "finalists|composite"),
    "date_or_null": (_t_date_or_null, "YYYY-MM-DD 或 null"),
    "profile": (_t_profile, "calibrated|preference"),
    "channel": (_t_channel, "none|bark|mail|file"),
    "path_or_null": (_t_path_or_null, "非空路径字符串 或 null"),
    "clock": (_t_clock, "HH:MM"),
    "num_list": (_t_num_list, "数值数组"),
    "recall_mode": (_t_recall_mode, "multi|composite"),
    "pref_weights": (_t_pref_weights, "object:恰含 scoring._GROUPS 十键的有限数、且绝对值之和≠0"),
}


# ── 登记表 ──────────────────────────────────────────────────────────────────
@dataclass(frozen=True)
class Block:
    name: str
    zone: str
    summary: str          # 块头注的一句话


@dataclass(frozen=True)
class Key:
    block: str
    key: str                                   # structured 块为 ""
    kind: str = KIND_SCALAR
    type: str = "any"
    default: object = None
    hosts: str = HOST_PYTHON
    consumers: tuple[str, ...] = ()
    children: tuple[str, ...] = ()             # kind=group 时列全内层参数
    default_sites: tuple[str, ...] = ()        # R7:缺省字面量还出现在哪些文件(消费者之外)


BLOCKS: tuple[Block, ...] = (
    Block("pinned", ZONE_BEHAVIOR, "保送持仓策略(票单在 pinned.jsonc)"),
    Block("signals", ZONE_BEHAVIOR, "跨阶段谓词阈(落刀 / 健康上涨 / 主力失真 / 质押 / regime / 温度 / 看多行业)"),
    Block("l0", ZONE_BEHAVIOR, "选集硬门"),
    Block("funnel", ZONE_BEHAVIOR, "L1 召回(多路 → quota_union → top recall_n)"),
    Block("l2", ZONE_BEHAVIOR, "粗排(确定性分层采样)"),
    Block("sector", ZONE_BEHAVIOR, "行业 brief 旁路(L2 后并发)"),
    Block("calendar", ZONE_BEHAVIOR, "日历事实(解禁 / 指数调样)"),
    Block("sentinel", ZONE_BEHAVIOR, "哨兵档(全市场健康上涨占比过低时跳过 L3/L4)"),
    Block("l3", ZONE_BEHAVIOR, "精排(pass1 确定性分诊 + pass2 holistic)"),
    Block("l4", ZONE_BEHAVIOR, "决策卡卡数"),
    Block("l4_intel", ZONE_BEHAVIOR, "活体情报站"),
    Block("relative_buy", ZONE_BEHAVIOR, "相对 BUY(E6)"),
    Block("execution", ZONE_BEHAVIOR, "执行时钟(运营入场截止 / 过期滞后)"),
    Block("agents", ZONE_RUNTIME, "agent 角色 → 档位(注册角色闭集,必须列全)"),
    Block("agent_engines", ZONE_RUNTIME, "档位 → 各引擎 model / effort"),
    Block("budgets", ZONE_RUNTIME, "预算告警与并发帽(不拥有截断权)"),
    Block("prelude", ZONE_RUNTIME, "前奏步骤"),
    Block("runner", ZONE_RUNTIME, "无人值守场时钟(scan_run)"),
    Block("readiness", ZONE_RUNTIME, "湖就绪探针与预热结算时刻"),
    Block("l4_tasks", ZONE_RUNTIME, "L4 任务簿:重试 / 卡死 / slim / tushare 槽"),
    Block("l4_watch", ZONE_RUNTIME, "L4 进度播报"),
    Block("performance", ZONE_RUNTIME, "调度开关"),
    Block("self_review", ZONE_RUNTIME, "发布前自检(GATE4 判据)"),
    Block("report", ZONE_RUNTIME, "报告体积预算"),
    Block("retention", ZONE_RUNTIME, "现场留存"),
    Block("delivery", ZONE_RUNTIME, "无人值守场送达"),
    Block("overseas", ZONE_RUNTIME, "海外事件日历(只做风险可见)"),
    Block("tripwire", ZONE_RUNTIME, "持仓盯梢"),
    Block("session", ZONE_RUNTIME, "session_v1 宿主(mailbox / headless)"),
    Block("shells", ZONE_RUNTIME, "legacy workflow 中继壳"),
    Block("dossier", ZONE_RUNTIME, "覆盖档案层"),
    Block("observability", ZONE_RUNTIME, "账本读数样本门与 lint 阈(只影响观测)"),
)

_UNIVERSE_RUN = "autoresearch.scan.universe:run"
_FRAME_BUILD = "autoresearch.scan.frame:build_market_frame"
_L3_PREPARE = "autoresearch.scan.l3.prompt:prepare_l3_table"
_RB_CONFIGURED = "autoresearch.scan.relative_buy:configured_relative_buy"
_BUDGET_NORMALIZE = "autoresearch.scan.budget:normalize_budgets"
_L4_STOCK_JS = ".claude/workflows/l4-stock.js"

#: 并发帽内建缺省(l4_tasks.DEFAULT_CAPS / budget.DEFAULT_BUDGETS / mailbox_cli 回退都从这里转出)。
#: l4_stock 在 legacy workflow = L4 派发宽度(64 = 事实无上限);在 session_agent = 全部角色并发上限。
DEFAULT_CONCURRENCY: dict[str, int] = {"tushare": 4, "l4_stock": 64}
#: intel 自报网查条数的软顶缺省(prompt 的「≤N 条」、确定性裁稿、发布前 lint 三处共用)。
DEFAULT_INTEL_MAX_QUERIES = 20

KEYS: tuple[Key, ...] = (
    # ── pinned ──
    Key("pinned", "cap", type="posint", default=5,
        consumers=("autoresearch.scan.user_config:load_pinned", "autoresearch.scan.run_bootstrap:prepare_scan_run")),
    Key("pinned", "ttl_days", type="posint", default=10,
        consumers=("autoresearch.scan.user_config:load_pinned", "autoresearch.scan.run_bootstrap:prepare_scan_run")),
    # ── signals ──
    Key("signals", "knife_pct_60d", type="num", default=-20.0,
        consumers=("autoresearch.common.scoring:falling_knife_mask",)),
    Key("signals", "healthy_pct60_range", kind=KIND_DATA, type="num_list", default=None,
        consumers=("autoresearch.common.scoring:healthy_riser_mask",)),
    Key("signals", "main_flow_distortion", kind=KIND_GROUP, type="dict", default=None, children=("ratio", "abs_yi"),
        consumers=("autoresearch.common.scoring:main_net_distortion_label",)),
    Key("signals", "pledge_pct", kind=KIND_GROUP, type="dict", default=None, children=("high", "warn"),
        consumers=("autoresearch.common.scoring:pledge_flag_label",)),
    Key("signals", "regime_thresholds", kind=KIND_GROUP, type="dict", default=None, children=("risk_on", "risk_off"),
        consumers=("autoresearch.common.regime:classify_regime",)),
    Key("signals", "temperature_bands", kind=KIND_GROUP, type="dict", default=None,
        children=("cold", "warm", "hot", "hysteresis"),
        consumers=("autoresearch.scan.temperature:temperature_bands", "autoresearch.scan.temperature:phase")),
    Key("signals", "healthy_sectors", kind=KIND_GROUP, type="dict", default=None,
        children=("min_members", "knife_median_min", "mom_center", "mom_half", "main_pos_min", "top_k"),
        consumers=("autoresearch.scan.market:healthy_sectors_cfg", "autoresearch.scan.market:sector_healthy_top3")),
    # ── l0 ──
    Key("l0", "cap_floor_yi", type="nonneg", default=30.0,
        consumers=(_FRAME_BUILD, _UNIVERSE_RUN, "autoresearch.scan.run_bootstrap:_effective_data_policy")),
    Key("l0", "include_bj", type="bool", default=True,
        consumers=(_FRAME_BUILD, _UNIVERSE_RUN, "autoresearch.scan.run_bootstrap:_effective_data_policy")),
    Key("l0", "source", type="source", default="tushare",
        consumers=(_FRAME_BUILD, _UNIVERSE_RUN, "autoresearch.scan.run_bootstrap:_effective_data_policy")),
    Key("l0", "min_amount_yi", type="nonneg", default=0.0, consumers=(_FRAME_BUILD, _UNIVERSE_RUN)),
    Key("l0", "min_list_days", type="nonneg_int", default=0, consumers=(_FRAME_BUILD, _UNIVERSE_RUN)),
    # ── funnel(L1)──
    Key("funnel", "regime_aware", type="bool", default=None,      # prelude 缺省 True / universe 直跑 False,有意 parity
        consumers=("autoresearch.scan.prelude:run_prelude", _UNIVERSE_RUN)),
    Key("funnel", "recall_n", type="posint", default=1000, consumers=(_UNIVERSE_RUN,)),
    Key("funnel", "l2_n", type="posint", default=200, consumers=(_UNIVERSE_RUN,)),
    Key("funnel", "recall_channels", kind=KIND_DATA, type="str_list", default=None,
        consumers=("autoresearch.scan.universe:_funnel_overlay", "autoresearch.scan.self_review:channel_liveness_lint")),
    Key("funnel", "channel_quotas", kind=KIND_DATA, type="int_dict", default=None,
        consumers=("autoresearch.scan.universe:_funnel_overlay",)),
    Key("funnel", "channel_floors", kind=KIND_DATA, type="int_dict", default=None,
        consumers=("autoresearch.scan.universe:_funnel_overlay",)),
    Key("funnel", "weight_profile", type="profile", default="calibrated", consumers=(_UNIVERSE_RUN,)),
    Key("funnel", "recall_mode", type="recall_mode", default="multi",
        consumers=(_UNIVERSE_RUN, "autoresearch.scan.universe:configured_recall_mode")),
    Key("funnel", "heat_weights", kind=KIND_GROUP, type="dict", default=None, children=("turnover", "vol_ratio"),
        consumers=("autoresearch.scan.recall.channels:heat_weights", "autoresearch.scan.recall.channels:heat")),
    Key("funnel", "panel_lookback_days", type="posint", default=60,
        consumers=("autoresearch.scan.frame:panel_cfg",)),
    Key("funnel", "panel_min_days", kind=KIND_GROUP, type="dict", default=None, children=("vol", "turnup"),
        consumers=("autoresearch.scan.frame:panel_cfg",)),
    Key("funnel", "event_lookback_days", type="posint", default=10,
        consumers=("autoresearch.scan.universe:event_lookback_days",)),
    Key("funnel", "lens_top_n", type="posint", default=50,
        consumers=("autoresearch.scan.universe:lens_top_n",)),
    Key("funnel", "preference_weights", kind=KIND_DATA, type="pref_weights", default=None, consumers=(_UNIVERSE_RUN,)),
    # ── l2 ──
    Key("l2", "sector_cap", type="num", default=0.20, consumers=(_UNIVERSE_RUN,)),
    Key("l2", "knife_cap", type="bool", default=False, consumers=(_UNIVERSE_RUN,)),
    Key("l2", "floors", kind=KIND_DATA, type="dict", default=None, consumers=(_UNIVERSE_RUN,)),   # 值型由 l2_stratify 现算;capsule 密钥门测试借它做自由槽
    Key("l2", "knife_cap_exempt_styles", kind=KIND_DATA, type="str_list", default=None,
        consumers=("autoresearch.scan.recall.l2_stratify:knife_cap_exempt_styles",)),
    Key("l2", "sector_seats", kind=KIND_GROUP, type="dict", default=None,
        children=("enabled", "per_sector", "max_sectors"),
        consumers=(_UNIVERSE_RUN, "autoresearch.scan.sector_seats:pick_sector_seats")),
    # ── sector ──
    Key("sector", "reuse_ttl_days", type="posint", default=5,
        consumers=("autoresearch.sector.reuse:main", "autoresearch.session_agent.domain_ops:scan_sector_prepare",
                   "autoresearch.session_agent.domain_ops:collect_sector_snapshot")),
    Key("sector", "max_briefs", type="posint", default=6,
        consumers=("autoresearch.sector.pack:main", "autoresearch.session_agent.domain_ops:scan_sector_prepare")),
    Key("sector", "healthy_top3_extra", type="bool", default=True,
        consumers=("autoresearch.sector.pack:select_briefing_sectors",)),
    Key("sector", "brief_web_searches", type="nonneg_int", default=2, hosts=HOST_BOTH,
        consumers=(".claude/workflows/scan-market.js", "autoresearch.session_agent.dispatch:sector_brief_web_searches")),
    Key("sector", "reuse_mom_shift_pp", type="posnum", default=3.0,
        consumers=("autoresearch.sector.reuse:configured_mom_shift_pp",)),
    Key("sector", "red_top_n", type="posint", default=3, consumers=("autoresearch.sector.pack:sector_cfg",)),
    Key("sector", "l2_conc_top_n", type="posint", default=3, consumers=("autoresearch.sector.pack:sector_cfg",)),
    Key("sector", "leaders_n", type="posint", default=5, consumers=("autoresearch.sector.pack:sector_cfg",)),
    Key("sector", "terrain_max_rows", type="posint", default=40, consumers=("autoresearch.sector.pack:sector_cfg",)),
    Key("sector", "readthrough_max", type="posint", default=4, consumers=("autoresearch.sector.pack:sector_cfg",)),
    # ── calendar ──
    Key("calendar", "index_rebalance", type="bool", default=False,
        consumers=("autoresearch.scan.calendar:harvest_calendar", "autoresearch.scan.health:index_events_health",
                   "autoresearch.scan.relative_buy:_index_events_input",
                   "autoresearch.scan.report_sections:prepare_report_model")),
    Key("calendar", "index_rebalance_flow", type="bool", default=False,
        consumers=("autoresearch.scan.index_events:harvest_index_events",)),
    Key("calendar", "horizon_days", type="posint", default=35,
        consumers=("autoresearch.scan.calendar:calendar_cfg", "autoresearch.scan.calendar:harvest_calendar")),
    Key("calendar", "unlock_flag", kind=KIND_GROUP, type="dict", default=None, children=("within_days", "min_ratio_pct"),
        consumers=("autoresearch.scan.calendar:calendar_cfg", "autoresearch.scan.calendar:calendar_flags")),
    Key("calendar", "section", kind=KIND_GROUP, type="dict", default=None, children=("window_days", "big_ratio_pct"),
        consumers=("autoresearch.scan.calendar:calendar_cfg", "autoresearch.scan.calendar:calendar_section")),
    Key("calendar", "index_post_window", type="posint", default=3,
        consumers=("autoresearch.scan.index_events:post_window",)),
    # ── sentinel ──
    Key("sentinel", "auto_below", type="fraction", default=0.03,
        consumers=("autoresearch.scan.menu:sentinel_thresholds",)),
    Key("sentinel", "consider_below", type="fraction", default=0.05,
        consumers=("autoresearch.scan.menu:sentinel_thresholds",)),
    # ── l3 ──
    Key("l3", "two_pass", type="bool", default=True, consumers=(_L3_PREPARE,)),
    Key("l3", "pass1_target", type="posint", default=60,
        consumers=(_L3_PREPARE, ".claude/workflows/scan-market.js", "autoresearch.session_agent.dispatch:_pass1_target")),
    Key("l3", "composite_seat", kind=KIND_GROUP, type="dict", default=None,
        children=("enabled", "m", "exclude_knife"),
        consumers=("autoresearch.scan.l3.merge:composite_seat_cfg",
                   "autoresearch.scan.l3.merge:composite_seat_exclude_knife")),
    Key("l3", "finalist_min", type="posint", default=7,
        consumers=("autoresearch.scan.l4.card_count:effective_caps",)),
    Key("l3", "guards", kind=KIND_GROUP, type="dict", default=None,
        children=("chase_1d_pct", "sector_cap", "healthy_quota_frac", "conv_force_in", "conv_min",
                  "qualify_conv", "lane_floors"),
        consumers=("autoresearch.scan.l3.merge:guards_cfg", "autoresearch.scan.l3.merge:merge_l3_finalists_v3",
                   "autoresearch.scan.l3.merge:_exclusion_reason", "autoresearch.scan.l3.merge:pick_composite_seats")),
    Key("l3", "pass1", kind=KIND_GROUP, type="dict", default=None,
        children=("resonance_cap", "resonance_min_channels", "healthy_mandatory"),
        consumers=("autoresearch.scan.l3.triage:pass1_cfg", "autoresearch.scan.l3.triage:triage_l2_for_l3")),
    Key("l3", "table", kind=KIND_GROUP, type="dict", default=None,
        children=("delta", "delta_tol", "sections"),
        consumers=("autoresearch.scan.l3.prompt:table_cfg",)),
    Key("l3", "profile", kind=KIND_GROUP, type="dict", default=None,
        children=("pct60_high", "pct60_mid", "pct60_low", "pct1_big", "near_high", "vol_ratio_big",
                  "pe_low", "pe_high", "winner_full", "winner_trapped", "rsi_overbought", "rsi_oversold"),
        consumers=("autoresearch.scan.l3.prompt:profile_cfg", "autoresearch.scan.l3.prompt:row_profile")),
    Key("l3", "lookback_days", kind=KIND_DATA, type="int_dict", default=None,
        consumers=("autoresearch.scan.l3.prompt:lookback_days", "autoresearch.scan.l3.evidence:harvest_l3_evidence",
                   "autoresearch.scan.agents.l3_news:harvest_l3_news",
                   "autoresearch.scan.agents.l3_catalyst:harvest_catalyst")),
    Key("l3", "lowturn", kind=KIND_GROUP, type="dict", default=None,
        children=("enabled", "max_dist_high_60", "max_pct_60d", "min_vol_ratio_20", "min_pct_5d",
                  "require_above_ma20", "require_ma5_gt_ma10", "fund", "knife_pct_60d", "pass1_cap"),
        consumers=(_L3_PREPARE, "autoresearch.scan.recall.channels:_lowturn_cfg",
                   "autoresearch.common.turnup:lowturn_flag")),
    # ── l4 ──
    Key("l4", "max_cards", type="posint", default=13,
        consumers=("autoresearch.scan.l4.card_count:effective_caps", "autoresearch.scan.self_review:l4_card_count_lint")),
    Key("l4", "budget_flags", type="bool", default=True,
        consumers=("autoresearch.scan.l4.card_count:effective_caps",)),
    Key("l4", "shadow_fields", type="bool", default=True,
        consumers=("autoresearch.scan.l4.prompts:params_block",)),
    Key("l4", "budget", kind=KIND_GROUP, type="dict", default=None,
        children=("base", "floor", "flags", "tiers"),
        consumers=("autoresearch.scan.menu:budget_cfg", "autoresearch.scan.menu:l4_budget",
                   "autoresearch.scan.menu:zero_buy_streak")),
    Key("l4", "rubric", kind=KIND_GROUP, type="dict", default=None,
        children=("rating_bands", "force_full"),
        consumers=("autoresearch.scan.l4.rubric:rubric_cfg", "autoresearch.scan.l4.rubric:rubric_rating",
                   "autoresearch.scan.l4.rubric:force_full_card")),
    Key("l4", "brief", kind=KIND_GROUP, type="dict", default=None,
        children=("dossier_days", "echo_lookback_days"),
        consumers=("autoresearch.scan.dossier:configured_max_days", "autoresearch.scan.l4.prompts:configured_echo_lookback")),
    Key("l4", "producers", kind=KIND_GROUP, type="dict", default=None,
        children=("pledge_reuse_days", "lhb_reuse_days", "lhb_window_days", "lhb_tail",
                  "consensus_window_days", "consensus_min_days"),
        consumers=("autoresearch.scan.l4.producers:producers_cfg", "autoresearch.scan.l4.producers:fetch_pledge",
                   "autoresearch.scan.l4.producers:fetch_seats", "autoresearch.scan.l4.producers:fetch_consensus")),
    Key("l4", "slim", kind=KIND_GROUP, type="dict", default=None,
        children=("min_bytes",),
        consumers=("autoresearch.scan.l4.producers:slim_min_bytes", "autoresearch.scan.l4_tasks:prepare_slim",
                   "autoresearch.scan.l4.prompts:slim_hint")),
    Key("l4", "review", kind=KIND_GROUP, type="dict", default=None, hosts=HOST_BOTH,
        children=("ow_ratings", "sell_review_pinned_only", "max_runs", "spread_escalate"),
        consumers=(_L4_STOCK_JS, "autoresearch.scan.decision_finalize:review_cfg",
                   "autoresearch.scan.decision_finalize:review_trigger",
                   "autoresearch.scan.decision_finalize:_ensemble_flag",
                   "autoresearch.session_agent.domain_ops:scan_review_plan",
                   "autoresearch.session_agent.workflows.scan:review3_expansion")),
    # ── l4_intel ──
    Key("l4_intel", "enabled", type="bool", default=False, hosts=HOST_BOTH,
        consumers=(_L4_STOCK_JS,
                   "autoresearch.session_agent.workflows.scan:_frozen_intel_enabled",
                   "autoresearch.session_agent.domain_ops:scan_l4_prepare")),
    Key("l4_intel", "max_queries", type="posint", default=DEFAULT_INTEL_MAX_QUERIES, hosts=HOST_BOTH,
        consumers=(_L4_STOCK_JS, "autoresearch.session_agent.domain_ops:scan_l4_prepare",
                   "autoresearch.scan.l4.intel_guard:configured_soft_cap",
                   "autoresearch.scan.self_review:product_shape_lint"),
        default_sites=("autoresearch/session_agent/dispatch.py", "autoresearch/scan/self_review.py")),
    Key("l4_intel", "skip_when_dead", type="bool", default=False,
        consumers=("autoresearch.scan.l4.intel_gate:decide",)),
    Key("l4_intel", "hard_cap", type="posint", default=30,
        consumers=("autoresearch.scan.l4.intel_guard:guard_cfg",)),
    Key("l4_intel", "soft_trim_keep", type="posint", default=10,
        consumers=("autoresearch.scan.l4.intel_guard:guard_cfg",)),
    Key("l4_intel", "stale_gap_days", type="posint", default=7,
        consumers=("autoresearch.scan.l4.intel_status:status_cfg",)),
    Key("l4_intel", "max_attempts", type="posint", default=3, hosts=HOST_JS,
        consumers=(_L4_STOCK_JS, "autoresearch.scan.l4.intel_status:status_cfg")),
    Key("l4_intel", "resume_max_age_s", type="posint", default=86400,
        consumers=("autoresearch.scan.l4.intel_status:status_cfg",)),
    Key("l4_intel", "dead_gate", kind=KIND_GROUP, type="dict", default=None,
        children=("main_inflow_max", "cmf_max", "obv_max"),
        consumers=("autoresearch.scan.l4.intel_gate:dead_gate_factors",)),
    # ── relative_buy(E6)──
    Key("relative_buy", "mode", type="rbmode", default="shadow", consumers=(_RB_CONFIGURED,)),
    Key("relative_buy", "exclude_pinned", type="bool", default=False, consumers=(_RB_CONFIGURED,)),
    Key("relative_buy", "pool", type="rbpool", default="finalists", consumers=(_RB_CONFIGURED,)),
    Key("relative_buy", "tiering", type="bool", default=False, consumers=(_RB_CONFIGURED,)),
    Key("relative_buy", "rebalance_gate", type="bool", default=False,
        consumers=("autoresearch.scan.relative_buy:configured_rebalance_gate",)),
    Key("relative_buy", "max_buys", type="posint", default=1,
        consumers=("autoresearch.scan.relative_buy:rule_params", "autoresearch.scan.relative_buy:build_decision")),
    Key("relative_buy", "liquidity_pctl_floor", type="fraction", default=0.10,
        consumers=("autoresearch.scan.relative_buy:rule_params", "autoresearch.scan.relative_buy:_hard_gate")),
    Key("relative_buy", "redflag", kind=KIND_GROUP, type="dict", default=None,
        children=("early_stop_reasons", "ratings", "proposals"),
        consumers=("autoresearch.scan.relative_buy:rule_params", "autoresearch.scan.relative_buy:_hard_gate")),
    Key("relative_buy", "risk_early_stop_reasons", kind=KIND_DATA, type="str_list", default=None,
        consumers=("autoresearch.scan.relative_buy:rule_params", "autoresearch.scan.relative_buy:_raw_faces")),
    Key("relative_buy", "scoring", kind=KIND_GROUP, type="dict", default=None,
        children=("recall_weights", "evidence", "risk_bonus", "missing_fill"),
        consumers=("autoresearch.scan.relative_buy:rule_params", "autoresearch.scan.relative_buy:_raw_faces",
                   "autoresearch.scan.relative_buy:_faces_table")),
    # ── execution ──
    Key("execution", "entry_line", kind=KIND_GROUP, type="dict", default=None, children=("pct_chg_max", "pos_in_range_max"),
        consumers=("autoresearch.contracts.agent_output:exec_line_thresholds",)),
    Key("execution", "entry_cutoff", type="clock", default="14:45",
        consumers=("autoresearch.scan.exec_anchor:entry_cutoff",)),
    Key("execution", "expiry_lag_sessions", type="posint", default=3,
        consumers=("autoresearch.scan.exec_anchor:expiry_lag_sessions",)),
    # ── agents / agent_engines(structured)──
    Key("agents", "", kind=KIND_STRUCTURED, type="dict",
        consumers=("autoresearch.scan.user_config:validate_user_config", "autoresearch.scan.user_config:resolve_agent_bundle")),
    Key("agent_engines", "", kind=KIND_STRUCTURED, type="dict",
        consumers=("autoresearch.scan.user_config:validate_user_config", "autoresearch.scan.user_config:_dual_role_specs")),
    # ── budgets ──
    Key("budgets", "cache_hit_min", type="fraction", default=0.85, consumers=(_BUDGET_NORMALIZE,)),
    Key("budgets", "run_weighted_warn", type="posnum", default=7_000_000, consumers=(_BUDGET_NORMALIZE,)),
    Key("budgets", "run_weighted_target", type="posnum", default=5_000_000, consumers=(_BUDGET_NORMALIZE,)),
    Key("budgets", "stage_cost_usd", kind=KIND_DATA, type="num_dict", default=None, consumers=(_BUDGET_NORMALIZE,)),
    Key("budgets", "stage_wall_seconds", kind=KIND_DATA, type="num_dict", default=None, consumers=(_BUDGET_NORMALIZE,)),
    Key("budgets", "concurrency", kind=KIND_DATA, type="int_dict", default=None,
        consumers=(_BUDGET_NORMALIZE, "autoresearch.scan.l4_tasks:main",
                   "autoresearch.session_agent.mailbox_cli:resolve_max_parallel",
                   "autoresearch.session_agent.domain_ops:scan_l4_prepare")),
    Key("budgets", "min_real_scans", type="posint", default=10, consumers=(_BUDGET_NORMALIZE,)),
    Key("budgets", "baseline_run", type="str", default="20260727_2140", consumers=(_BUDGET_NORMALIZE,)),
    Key("budgets", "maturity", kind=KIND_GROUP, type="dict", default=None, children=("phase1", "phase2"),
        consumers=(_BUDGET_NORMALIZE, "autoresearch.scan.budget:evaluate_history")),
    Key("budgets", "relative", kind=KIND_GROUP, type="dict", default=None,
        children=("window", "min_runs", "warn_ratio", "alarm_ratio"),
        consumers=("autoresearch.scan.budget:_relative_policy",)),
    # ── prelude ──
    Key("prelude", "skip_steps", kind=KIND_DATA, type="str_list", default=None,
        consumers=("autoresearch.scan.prelude:configured_skip_steps",)),
    Key("prelude", "announcement_lookback", type="posint", default=5,
        consumers=("autoresearch.scan.prelude:announcement_lookback",)),
    # ── runner ──
    Key("runner", "window_start", type="clock", default="21:10", consumers=("autoresearch.scan.scan_run:runner_cfg",)),
    Key("runner", "hard_stop", type="clock", default="01:00", consumers=("autoresearch.scan.scan_run:runner_cfg",)),
    Key("runner", "min_runner_minutes", type="posint", default=10, consumers=("autoresearch.scan.scan_run:runner_cfg",)),
    Key("runner", "run_timeout_minutes", type="posnum", default=180, consumers=("autoresearch.scan.scan_run:runner_cfg",)),
    Key("runner", "live_run_window_min", type="posint", default=90, consumers=("autoresearch.scan.scan_run:runner_cfg",)),
    Key("runner", "kill_grace_s", type="posnum", default=5.0, consumers=("autoresearch.scan.scan_run:runner_cfg",)),
    Key("runner", "subprocess_timeout_s", type="posint", default=1800, consumers=("autoresearch.scan.scan_run:runner_cfg",)),
    Key("runner", "force_full", type="bool", default=False, consumers=("autoresearch.scan.scan_run:runner_cfg",)),
    # ── readiness ──
    Key("readiness", "min_rows", type="posint", default=5300, consumers=("autoresearch.scan.readiness:readiness_cfg",)),
    Key("readiness", "stable_polls", type="posint", default=2, consumers=("autoresearch.scan.readiness:readiness_cfg",)),
    Key("readiness", "interval_s", type="posnum", default=300, consumers=("autoresearch.scan.readiness:readiness_cfg",)),
    Key("readiness", "deadline", type="clock", default="22:30", consumers=("autoresearch.scan.readiness:readiness_cfg",)),
    Key("readiness", "late_recheck_s", type="posnum", default=30, consumers=("autoresearch.scan.readiness:readiness_cfg",)),
    Key("readiness", "settle_hhmm", type="clock", default="19:15", consumers=("autoresearch.scan.prewarm:settle_minutes",)),
    # ── l4_tasks ──
    Key("l4_tasks", "max_attempts", type="posint", default=2, consumers=("autoresearch.scan.l4_tasks:tasks_cfg",)),
    Key("l4_tasks", "stale_after_s", type="posint", default=3600, consumers=("autoresearch.scan.l4_tasks:tasks_cfg",)),
    Key("l4_tasks", "slim_retries", type="nonneg_int", default=1, consumers=("autoresearch.scan.l4_tasks:tasks_cfg",)),
    Key("l4_tasks", "slim_workers", type="posint", default=4, consumers=("autoresearch.scan.l4_tasks:tasks_cfg",)),
    Key("l4_tasks", "slot_poll_s", type="posnum", default=5.0, consumers=("autoresearch.scan.l4_tasks:tasks_cfg",)),
    Key("l4_tasks", "slot_heartbeat_s", type="posint", default=60, consumers=("autoresearch.scan.l4_tasks:tasks_cfg",)),
    # ── l4_watch ──
    Key("l4_watch", "stale_min", type="posint", default=30, consumers=("autoresearch.scan.l4_watch:watch_cfg",)),
    Key("l4_watch", "interval_s", type="posnum", default=5.0, consumers=("autoresearch.scan.l4_watch:watch_cfg",)),
    # ── self_review ──
    Key("self_review", "coverage_min", type="fraction", default=0.8, consumers=("autoresearch.scan.self_review:self_review_cfg",)),
    Key("self_review", "composite_floor", type="num", default=30.0, consumers=("autoresearch.scan.self_review:self_review_cfg",)),
    Key("self_review", "sector_max", type="fraction", default=0.6, consumers=("autoresearch.scan.self_review:self_review_cfg",)),
    Key("self_review", "winner_rate_max", type="num", default=88, consumers=("autoresearch.scan.self_review:self_review_cfg",)),
    Key("self_review", "overheat", kind=KIND_GROUP, type="dict", default=None, children=("pct60", "rsi6"),
        consumers=("autoresearch.scan.self_review:self_review_cfg", "autoresearch.scan.self_review:review")),
    Key("self_review", "liveness_escalate_streak", type="posint", default=3,
        consumers=("autoresearch.scan.self_review:self_review_cfg",)),
    Key("self_review", "citation_min", type="nonneg_int", default=6, consumers=("autoresearch.scan.self_review:self_review_cfg",)),
    Key("self_review", "intel_stale_days", kind=KIND_GROUP, type="dict", default=None, children=("v1", "v2"),
        consumers=("autoresearch.scan.self_review:self_review_cfg", "autoresearch.scan.self_review:_intel_stale_days")),
    # ── report ──
    Key("report", "brief_max_bytes", type="posint", default=3000, consumers=("autoresearch.scan.report_model:report_cfg",)),
    Key("report", "tone_chars", type="posint", default=34, consumers=("autoresearch.scan.report_model:report_cfg",)),
    Key("report", "summary_target_bytes", type="posint", default=12 * 1024, consumers=("autoresearch.scan.report_model:report_cfg",)),
    Key("report", "summary_warn_bytes", type="posint", default=16 * 1024, consumers=("autoresearch.scan.report_model:report_cfg",)),
    Key("report", "appendix_target_bytes", type="posint", default=20 * 1024, consumers=("autoresearch.scan.report_model:report_cfg",)),
    Key("report", "appendix_warn_bytes", type="posint", default=24 * 1024, consumers=("autoresearch.scan.report_model:report_cfg",)),
    Key("report", "evidence_max_chars", type="posint", default=80, consumers=("autoresearch.scan.report_model:report_cfg",)),
    # ── overseas / tripwire ──
    Key("overseas", "horizon_days", type="posint", default=14, consumers=("autoresearch.scan.overseas:overseas_cfg",)),
    Key("overseas", "rows_summary", type="posint", default=4, consumers=("autoresearch.scan.overseas:overseas_cfg",)),
    Key("overseas", "rows_brief", type="posint", default=1, consumers=("autoresearch.scan.overseas:overseas_cfg",)),
    Key("overseas", "rows_tripwire", type="posint", default=3, consumers=("autoresearch.scan.overseas:overseas_cfg",)),
    Key("tripwire", "date_lead_days", type="nonneg_int", default=3, consumers=("autoresearch.scan.tripwire_watch:date_lead_days",)),
    # ── session(hosts=sa)──
    Key("session", "global_intel_max_queries", type="posint", default=8, hosts=HOST_SA,
        consumers=("autoresearch.session_agent.workflows.macro:global_intel_policy",)),
    Key("session", "timeouts", kind=KIND_GROUP, type="dict", default=None, hosts=HOST_SA, children=("mailbox", "headless", "fallback_s", "codex_open_s"),
        consumers=("autoresearch.session_agent.config:session_cfg", "autoresearch.session_agent.dispatch:build_request",
                   "autoresearch.session_agent.mailbox_cli:_build_executor",
                   "autoresearch.session_agent.executors.headless_codex:HeadlessCodexExecutor.__init__")),
    Key("session", "max_turns", kind=KIND_DATA, type="int_dict", default=None, hosts=HOST_SA,
        consumers=("autoresearch.session_agent.config:session_cfg", "autoresearch.session_agent.executors.headless_claude:HeadlessClaudeExecutor.__init__")),
    Key("session", "tier_max_turns", kind=KIND_DATA, type="int_dict", default=None, hosts=HOST_SA,
        consumers=("autoresearch.session_agent.config:session_cfg", "autoresearch.session_agent.executors.headless_claude:HeadlessClaudeExecutor.max_turns_for")),
    Key("session", "default_max_turns", type="posint", default=60, hosts=HOST_SA,
        consumers=("autoresearch.session_agent.config:session_cfg", "autoresearch.session_agent.executors.headless_claude:HeadlessClaudeExecutor.max_turns_for")),
    Key("session", "mailbox", kind=KIND_GROUP, type="dict", default=None, hosts=HOST_SA, children=("never_taken_factor", "wait_s", "dead_heartbeats", "by_reference"),
        consumers=("autoresearch.session_agent.config:session_cfg", "autoresearch.session_agent.executors.mailbox:wait_request",
                   "autoresearch.session_agent.executors.mailbox:MailboxExecutor.__init__", "autoresearch.session_agent.mailbox_cli:add_parsers")),
    Key("session", "max_attempts", type="posint", default=2, hosts=HOST_SA,
        consumers=("autoresearch.session_agent.config:session_cfg", "autoresearch.session_agent.runner:_session_max_attempts")),
    Key("session", "runner", kind=KIND_GROUP, type="dict", default=None, hosts=HOST_SA, children=("poll_seconds", "max_rounds", "timeout_multiplier", "fanout_warmup_s"),
        consumers=("autoresearch.session_agent.config:session_cfg", "autoresearch.session_agent.mailbox_cli:add_parsers")),
    # ── shells(hosts=js)──
    Key("shells", "detached_max_rounds_scan", type="posint", default=40, hosts=HOST_JS, consumers=(".claude/workflows/scan-market.js",)),
    Key("shells", "detached_max_rounds_l4", type="posint", default=20, hosts=HOST_JS, consumers=(_L4_STOCK_JS,)),
    Key("shells", "wait_seconds", type="posint", default=100, hosts=HOST_JS, consumers=(".claude/workflows/scan-market.js", _L4_STOCK_JS)),
    Key("shells", "misses_lost", type="posint", default=3, hosts=HOST_JS, consumers=(".claude/workflows/scan-market.js", _L4_STOCK_JS)),
    Key("shells", "trace_calls_per_target", type="posint", default=2, hosts=HOST_JS, consumers=(".claude/workflows/scan-market.js", _L4_STOCK_JS)),
    Key("shells", "tail_lines", type="posint", default=15, hosts=HOST_JS, consumers=(".claude/workflows/scan-market.js", _L4_STOCK_JS)),
    # ── dossier ──
    Key("dossier", "pool_cap", type="posint", default=30, consumers=("autoresearch.dossier.config:dossier_cfg", "autoresearch.dossier.pool:_pool_cap")),
    Key("dossier", "recent_days", type="posint", default=20, consumers=("autoresearch.dossier.config:dossier_cfg", "autoresearch.dossier.pool:_recent_scan_days")),
    Key("dossier", "entry_min", type="posint", default=2, consumers=("autoresearch.dossier.config:dossier_cfg", "autoresearch.dossier.pool:_refresh_unlocked")),
    Key("dossier", "init_per_night", type="posint", default=3, consumers=("autoresearch.dossier.config:dossier_cfg", "autoresearch.dossier.debt_slo")),
    Key("dossier", "max_pending_age_days", type="posint", default=2, consumers=("autoresearch.dossier.config:dossier_cfg",)),
    Key("dossier", "throughput_window_days", type="posint", default=7, consumers=("autoresearch.dossier.config:dossier_cfg",)),
    Key("dossier", "intel_gap_max_lines", type="posint", default=2, consumers=("autoresearch.dossier.config:dossier_cfg", "autoresearch.dossier.delta:intel_dossier_gaps")),
    Key("dossier", "summary_cap", type="posint", default=3000, consumers=("autoresearch.dossier.config:dossier_cfg", "autoresearch.dossier.schema")),
    Key("dossier", "research_body_cap", type="posint", default=12000, consumers=("autoresearch.dossier.config:dossier_cfg", "autoresearch.dossier.schema")),
    Key("dossier", "stale_days", type="posint", default=90, consumers=("autoresearch.dossier.config:dossier_cfg", "autoresearch.dossier.schema")),
    # ── observability ──
    Key("observability", "min_ledger_n", type="posint", default=20, consumers=("autoresearch.scan.observability:observability_cfg",)),
    Key("observability", "min_session_n", type="posint", default=20, consumers=("autoresearch.scan.observability:observability_cfg",)),
    Key("observability", "realized_min_n", type="posint", default=20, consumers=("autoresearch.scan.observability:observability_cfg",)),
    Key("observability", "winner_decile", type="fraction", default=0.9, consumers=("autoresearch.scan.observability:observability_cfg",)),
    Key("observability", "swing_readout_min_days", type="posint", default=40, consumers=("autoresearch.scan.observability:observability_cfg",)),
    Key("observability", "swing_readout_min_clusters", type="posint", default=10, consumers=("autoresearch.scan.observability:observability_cfg",)),
    Key("observability", "menu_knife_tolerance", type="fraction", default=0.06, consumers=("autoresearch.scan.observability:observability_cfg",)),
    Key("observability", "price_claim_tol_pp", type="posnum", default=1.5, consumers=("autoresearch.scan.observability:observability_cfg",)),
    Key("observability", "unknown_rate_tolerance", type="fraction", default=0.05, consumers=("autoresearch.scan.observability:observability_cfg",)),
    Key("observability", "round_trip_cost_bp", type="posnum", default=12, consumers=("autoresearch.scan.observability:observability_cfg",)),
    Key("observability", "exec_floor_pct_1d", type="num", default=-3.0, consumers=("autoresearch.scan.observability:observability_cfg",)),
    Key("observability", "exec_floor_pos_in_range", type="fraction", default=0.1, consumers=("autoresearch.scan.observability:observability_cfg",)),
    Key("observability", "fingerprint", kind=KIND_GROUP, type="dict", default=None,
        children=("window", "min_runs", "share_delta", "ratio_warn"),
        consumers=("autoresearch.scan.behavior_fingerprint:policy",)),
    Key("observability", "nan_warn", type="fraction", default=0.30, consumers=("autoresearch.scan.observability:observability_cfg",)),
    # ── performance ──
    Key("performance", "streaming_l4", type="bool", default=True, hosts=HOST_JS,
        consumers=(".claude/workflows/scan-market.js",)),
    # ── retention ──
    Key("retention", "bind_transcripts", type="bool", default=True,
        consumers=("autoresearch.scan.transcript_binder:_configured_bind_transcripts",)),
    Key("retention", "archive_transcript_agents", kind=KIND_DATA, type="str_list", default=None,
        consumers=("autoresearch.scan.retention:retention_cfg",)),
    Key("retention", "lake_window_days", type="posint", default=70, consumers=("autoresearch.scan.retention:retention_cfg",)),
    Key("retention", "capsule_stale_after_min", type="posint", default=5, consumers=("autoresearch.trace.capsule:stale_after",)),
    Key("retention", "salvage_window_min", type="posint", default=20, consumers=("autoresearch.scan.salvage:salvage_windows",)),
    Key("retention", "salvage_max_hours", type="posint", default=8, consumers=("autoresearch.scan.salvage:salvage_windows",)),
    Key("retention", "codex_transcript_lookback_days", type="posint", default=10,
        consumers=("autoresearch.scan.transcript_binder:codex_lookback_days",)),
    # ── delivery ──
    Key("delivery", "channel", type="channel", default="none",
        consumers=("autoresearch.scan.delivery:configured_delivery",)),
    Key("delivery", "file_dir", type="path_or_null", default=None,
        consumers=("autoresearch.scan.delivery:configured_delivery",)),
    Key("delivery", "bark_body_limit", type="posint", default=3000, consumers=("autoresearch.scan.delivery:delivery_limits",)),
    Key("delivery", "http_timeout_s", type="posnum", default=15.0, consumers=("autoresearch.scan.delivery:delivery_limits",)),
    Key("delivery", "mail_timeout_s", type="posint", default=60, consumers=("autoresearch.scan.delivery:delivery_limits",)),
)

#: R8 —— 散落在代码里、config 还够不着的可调常量 allowlist:(位置, 理由)。理由必须以
#: `CONSTANT_REASON_KINDS` 之一开头;「待迁」条目只能减不能加(测试锁上限)。位置写法:
#: ``pkg.module:NAME``(模块级常量)/ ``pkg.module:func.param``(函数数值缺省)/ ``path.js:NAME``。
CODE_CONSTANTS: tuple[tuple[str, str], ...] = (
    ("autoresearch.scan.brief:MAX_BYTES", "不进 config:已是 report.brief_max_bytes 的内建缺省"),
    ("autoresearch.scan.brief:TONE_CHARS", "不进 config:已是 report.tone_chars 的内建缺省"),
    ("autoresearch.scan.buyability:MENU_KNIFE_TOLERANCE", "不进 config:已是 observability.menu_knife_tolerance 的内建缺省"),
    ("autoresearch.scan.config_standard:MAX_COMMENT_CHARS", "不进 config:标准自身的阈(改 = 改标准,不是改参数)"),
    ("autoresearch.scan.config_standard:MAX_FILE_HEADER_LINES", "不进 config:标准自身的阈(改 = 改标准,不是改参数)"),
    ("autoresearch.scan.delivery:BARK_BODY_LIMIT", "不进 config:已是 delivery.bark_body_limit 的内建缺省"),
    ("autoresearch.scan.delivery:HTTP_TIMEOUT", "不进 config:已是 delivery.http_timeout_s 的内建缺省"),
    ("autoresearch.scan.events:market_event_counts.lookback_days", "不进 config:已是 funnel.event_lookback_days 的内建缺省(universe.run 显式传入)"),
    ("autoresearch.scan.exec_anchor:EXEC_DECISION_CUTOFF", "不进 config:已是 execution.entry_cutoff 的内建缺省"),
    ("autoresearch.scan.exec_anchor:EXCHANGE_CUTOFF", "不进 config:交易所收盘集合竞价时刻,是事实不是参数"),
    ("autoresearch.scan.gates:gate2.budget", "不进 config:skip 路径的形参缺省,生产由 GATE1 回显"),
    ("autoresearch.scan.index_events:POST_WINDOW", "不进 config:已是 calendar.index_post_window 的内建缺省"),
    ("autoresearch.scan.index_events:trading_days_window.before", "不进 config:普查(census)窗口,不是扫描参数"),
    ("autoresearch.scan.index_events:trading_days_window.after", "不进 config:普查(census)窗口,不是扫描参数"),
    ("autoresearch.scan.l3.merge:CHASE_1D_PCT", "不进 config:已是 l3.guards.chase_1d_pct 的内建缺省"),
    ("autoresearch.scan.l3.prompt:PROFILE_DEFAULTS", "不进 config:已是 l3.profile 的内建缺省"),
    ("autoresearch.scan.scan_run:SUBPROCESS_TIMEOUT_S", "不进 config:已是 runner.subprocess_timeout_s 的内建缺省"),
    ("autoresearch.session_agent.executors.headless_codex:OPEN_TIMEOUT_S", "不进 config:已是 session.timeouts.codex_open_s 的内建缺省"),
    ("autoresearch.scan.l4_tasks:STALE_AFTER_S", "不进 config:已是 l4_tasks.stale_after_s 的内建缺省"),
    ("autoresearch.scan.l4_tasks:SLOT_POLL_S", "不进 config:已是 l4_tasks.slot_poll_s 的内建缺省"),
    ("autoresearch.scan.l4_tasks:SLOT_HEARTBEAT_S", "不进 config:已是 l4_tasks.slot_heartbeat_s 的内建缺省"),
    ("autoresearch.scan.delivery:MAIL_TIMEOUT", "不进 config:已是 delivery.mail_timeout_s 的内建缺省"),
    ("autoresearch.common.scoring:HEALTHY_PCT60_RANGE", "不进 config:已是 signals.healthy_pct60_range 的内建缺省"),
    ("autoresearch.scan.temperature:TEMPERATURE_BANDS_DEFAULT", "不进 config:已是 signals.temperature_bands 的内建缺省"),
    ("autoresearch.scan.recall.channels:HEAT_WEIGHTS_DEFAULT", "不进 config:已是 funnel.heat_weights 的内建缺省"),
    ("autoresearch.scan.menu:SENTINEL_AUTO_BELOW", "不进 config:已是 sentinel.auto_below 的内建缺省"),
    ("autoresearch.scan.menu:SENTINEL_CONSIDER_BELOW", "不进 config:已是 sentinel.consider_below 的内建缺省"),
    ("autoresearch.scan.l4.rubric:RATING_BANDS_DEFAULT", "不进 config:已是 l4.rubric.rating_bands 的内建缺省"),
    ("autoresearch.scan.l4.rubric:FORCE_FULL_DEFAULT", "不进 config:已是 l4.rubric.force_full 的内建缺省"),
    ("autoresearch.scan.l4.producers:PRODUCERS_DEFAULTS", "不进 config:已是 l4.producers 的内建缺省"),
    ("autoresearch.scan.l4.producers:SLIM_MIN_BYTES", "不进 config:已是 l4.slim.min_bytes 的内建缺省"),
    ("autoresearch.scan.l3.merge:CONV_FORCE_IN", "不进 config:已是 l3.guards.conv_force_in 的内建缺省"),
    ("autoresearch.scan.l3.merge:CONV_MIN", "不进 config:已是 l3.guards.conv_min 的内建缺省"),
    ("autoresearch.scan.l3.triage:RESONANCE_MIN_CHANNELS", "不进 config:已是 l3.pass1.resonance_min_channels 的内建缺省"),
    ("autoresearch.scan.l3.prompt:LOOKBACK_DEFAULTS", "不进 config:已是 l3.lookback_days 的内建缺省"),
    ("autoresearch.scan.l3.merge:L3_SECTOR_CAP", "不进 config:已是 l3.guards.sector_cap 的内建缺省"),
    ("autoresearch.scan.l3.merge:HEALTHY_QUOTA_FRAC", "不进 config:已是 l3.guards.healthy_quota_frac 的内建缺省"),
    ("autoresearch.scan.l3.merge:COMPOSITE_SEAT_M", "不进 config:已是 l3.composite_seat.m 的内建缺省"),
    ("autoresearch.scan.l3.merge:merge_l3_finalists_v3.finalist_max", "不进 config:v3 旧路径形参缺省;卡数由 l4.max_cards 决定"),
    ("autoresearch.scan.l3.merge:write_finalists.budget", "不进 config:GATE1 回显 l4_budget 的形参缺省"),
    ("autoresearch.scan.l3.triage:RESONANCE_CAP", "不进 config:已是 l3.pass1.resonance_cap 的内建缺省"),
    ("autoresearch.scan.l3.triage:triage_l2_for_l3.target", "不进 config:已是 l3.pass1_target 的内建缺省"),
    ("autoresearch.scan.l4.card_count:DEFAULT_MAX_CARDS", "不进 config:已是 l4.max_cards 的内建缺省"),
    ("autoresearch.scan.populations:MIN_IC_NAMES", "不进 config:截面尺定义的一部分(改 = 改尺,不是调参)"),
    ("autoresearch.scan.l3.rule_flags:WINNER_FRAGILE", "不进 config:l3-rank 契约 ⑤ 的字面阈值(改 = 改契约)"),
    ("autoresearch.scan.behavior_fingerprint:DEFAULT_POLICY", "不进 config:已是 observability.fingerprint 的内建缺省"),
    ("autoresearch.scan.l4.intel_gate:DEAD_MAIN_INFLOW_YI_MAX", "不进 config:已是 l4_intel.dead_gate.main_inflow_max 的内建缺省"),
    ("autoresearch.scan.l4.intel_gate:DEAD_CMF_MAX", "不进 config:已是 l4_intel.dead_gate.cmf_max 的内建缺省"),
    ("autoresearch.scan.l4.intel_gate:DEAD_OBV_MAX", "不进 config:已是 l4_intel.dead_gate.obv_max 的内建缺省"),
    ("autoresearch.scan.l4.intel_gate:STOP_T0_NEGATIVE_SHARE", "不进 config:预注册停机规则,改 = 重新立案"),
    ("autoresearch.scan.l4.intel_gate:STOP_GE_HOLD_SHARE", "不进 config:预注册停机规则,改 = 重新立案"),
    ("autoresearch.scan.l4.intel_gate:T0_NEGATIVE_MAX", "不进 config:预注册停机规则,改 = 重新立案"),
    ("autoresearch.scan.l4.intel_guard:HARD_CAP_DEFAULT", "不进 config:已是 l4_intel.hard_cap 的内建缺省"),
    ("autoresearch.scan.l4.intel_guard:SOFT_TRIM_KEEP", "不进 config:已是 l4_intel.soft_trim_keep 的内建缺省"),
    ("autoresearch.scan.l4.intel_status:MAX_ATTEMPTS", "不进 config:已是 l4_intel.max_attempts 的内建缺省"),
    ("autoresearch.scan.l4.parsers:pick_opportunity_candidates.k", "不进 config:无调用者,该删不该配"),
    ("autoresearch.scan.l4_tasks:MAX_ATTEMPTS", "不进 config:已是 l4_tasks.max_attempts 的内建缺省"),
    ("autoresearch.scan.ledger_views:MIN_SESSION_N", "不进 config:已是 observability.min_session_n 的内建缺省"),
    ("autoresearch.scan.outcome:MIN_LEDGER_N", "不进 config:已是 observability.min_ledger_n 的内建缺省"),
    ("autoresearch.scan.overseas:T2_OPEN", "不进 config:交易所开盘时刻,是事实不是参数"),
    ("autoresearch.scan.overseas:MAX_SUMMARY_ROWS", "不进 config:已是 overseas.rows_summary 的内建缺省"),
    ("autoresearch.scan.overseas:MAX_BRIEF_ROWS", "不进 config:已是 overseas.rows_brief 的内建缺省"),
    ("autoresearch.scan.overseas:MAX_TRIPWIRE_ROWS", "不进 config:已是 overseas.rows_tripwire 的内建缺省"),
    ("autoresearch.scan.populations:RULER_HORIZON", "不进 config:尺的定义(主尺用户裁定不换),不是可调参数"),
    ("autoresearch.scan.populations:WINNER_DECILE", "不进 config:已是 observability.winner_decile 的内建缺省"),
    ("autoresearch.scan.populations:RULER_BLOCK", "不进 config:尺的定义(主尺用户裁定不换),不是可调参数"),
    ("autoresearch.scan.price_claims:UNKNOWN_RATE_TOLERANCE_PP", "不进 config:已是 observability.unknown_rate_tolerance 的内建缺省"),
    ("autoresearch.scan.published_days:resolve.limit", "不进 config:目录枚举上限,非行为"),
    ("autoresearch.scan.published_days:previous_scan_days.limit", "不进 config:目录枚举上限,非行为"),
    ("autoresearch.scan.published_days:previous_staging_dirs.limit", "不进 config:目录枚举上限,非行为"),
    ("autoresearch.scan.readiness:MIN_ROWS", "不进 config:已是 readiness.min_rows 的内建缺省"),
    ("autoresearch.scan.readiness:STABLE_POLLS", "不进 config:已是 readiness.stable_polls 的内建缺省"),
    ("autoresearch.scan.readiness:INTERVAL_S", "不进 config:已是 readiness.interval_s 的内建缺省"),
    ("autoresearch.scan.readiness:DEADLINE", "不进 config:已是 readiness.deadline 的内建缺省"),
    ("autoresearch.scan.readiness:LATE_RECHECK_S", "不进 config:已是 readiness.late_recheck_s 的内建缺省"),
    ("autoresearch.scan.recall.l2_stratify:DEFAULT_FLOORS", "不进 config:已是 l2.floors 的内建缺省"),
    ("autoresearch.scan.recall.l2_stratify:stratified_l2.l2_n", "不进 config:已是 funnel.l2_n 的内建缺省"),
    ("autoresearch.scan.recall.l2_stratify:stratified_l2.sector_cap_frac", "不进 config:已是 l2.sector_cap 的内建缺省"),
    ("autoresearch.scan.recall.l2_stratify:select_l2.sector_cap_frac", "不进 config:已是 l2.sector_cap 的内建缺省"),
    ("autoresearch.scan.relative_buy:EXPECTED_ABS_GAP_MIN_N", "不进 config:代码零消费者(只在文档串里出现)"),
    ("autoresearch.scan.relative_buy:LIQUIDITY_PCTL_FLOOR", "不进 config:已是 relative_buy.liquidity_pctl_floor 的内建缺省"),
    ("autoresearch.scan.report_model:EVIDENCE_MAX_CHARS", "不进 config:已是 report.evidence_max_chars 的内建缺省"),
    ("autoresearch.scan.retention:LAKE_WINDOW_DAYS", "不进 config:已是 retention.lake_window_days 的内建缺省"),
    ("autoresearch.scan.scan_run:LIVE_RUN_WINDOW", "不进 config:已是 runner.live_run_window_min 的内建缺省"),
    ("autoresearch.scan.scan_run:RUN_TIMEOUT_MINUTES", "不进 config:已是 runner.run_timeout_minutes 的内建缺省"),
    ("autoresearch.scan.scan_run:WINDOW_START", "不进 config:已是 runner.window_start 的内建缺省"),
    ("autoresearch.scan.scan_run:HARD_STOP", "不进 config:已是 runner.hard_stop 的内建缺省"),
    ("autoresearch.scan.scan_run:MIN_RUNNER_MINUTES", "不进 config:已是 runner.min_runner_minutes 的内建缺省"),
    ("autoresearch.scan.scan_run:KILL_GRACE_S", "不进 config:已是 runner.kill_grace_s 的内建缺省"),
    ("autoresearch.scan.sector_seats:pick_sector_seats.per_sector", "不进 config:已是 l2.sector_seats.per_sector 的内建缺省"),
    ("autoresearch.scan.sector_seats:pick_sector_seats.max_sectors", "不进 config:已是 l2.sector_seats.max_sectors 的内建缺省"),
    ("autoresearch.scan.self_review:LIVENESS_ESCALATE_STREAK", "不进 config:已是 self_review.liveness_escalate_streak 的内建缺省"),
    ("autoresearch.scan.structural_audit:render.required", "不进 config:审计报表的展示参数"),
    ("autoresearch.scan.swing_seat:READOUT_MIN_DAYS", "不进 config:已是 observability.swing_readout_min_days 的内建缺省"),
    ("autoresearch.scan.swing_seat:READOUT_MIN_CLUSTERS", "不进 config:已是 observability.swing_readout_min_clusters 的内建缺省"),
    ("autoresearch.scan.universe:aggregate_sectors.top_sectors", "不进 config:缺省路径无调用者(唯一调用点显式传 3)"),
    ("autoresearch.sector.pack:select_briefing_sectors.k", "不进 config:已是 sector.max_briefs 的内建缺省"),
    ("autoresearch.sector.reuse:find_reusable.ttl_days", "不进 config:已是 sector.reuse_ttl_days 的内建缺省"),
    ("autoresearch.dossier.debt_slo:MAX_PENDING_AGE_DAYS", "不进 config:已是 dossier.max_pending_age_days 的内建缺省"),
    ("autoresearch.dossier.debt_slo:THROUGHPUT_WINDOW_DAYS", "不进 config:已是 dossier.throughput_window_days 的内建缺省"),
    ("autoresearch.dossier.debt_slo:NIGHTLY_CAP", "不进 config:已是 dossier.init_per_night 的内建缺省"),
    ("autoresearch.dossier.mainbz:mainbz_latest.periods", "不进 config:数据形状(取最近几期主营),非行为"),
    ("autoresearch.dossier.schema:SUMMARY_CAP", "不进 config:已是 dossier.summary_cap 的内建缺省"),
    ("autoresearch.dossier.schema:RESEARCH_BODY_CAP", "不进 config:已是 dossier.research_body_cap 的内建缺省"),
    ("autoresearch.dossier.schema:STALE_DAYS", "不进 config:已是 dossier.stale_days 的内建缺省"),
    ("autoresearch.common.scoring:KNIFE_PCT_60D", "不进 config:已是 signals.knife_pct_60d 的内建缺省"),
    ("autoresearch.session_agent.executors.base:FALLBACK_TIMEOUT", "不进 config:已是 session.timeouts.fallback_s 的内建缺省"),

    ("autoresearch.session_agent.executors.headless_claude:EXCERPT_CHARS", "不进 config:日志摘录长度,非行为"),
    ("autoresearch.session_agent.executors.mailbox:NEVER_TAKEN_FACTOR", "不进 config:已是 session.mailbox.never_taken_factor 的内建缺省"),
    ("autoresearch.session_agent.executors.mailbox:DEFAULT_WAIT_SECONDS", "不进 config:已是 session.mailbox.wait_s 的内建缺省"),
    ("autoresearch.session_agent.executors.mailbox:DEAD_HEARTBEATS", "不进 config:已是 session.mailbox.dead_heartbeats 的内建缺省"),
    ("autoresearch.session_agent.runner:SESSION_MAX_ATTEMPTS", "不进 config:已是 session.max_attempts 的内建缺省"),
)

#: R8 扫描范围(仓库根相对 glob)。
R8_SCOPE: tuple[str, ...] = (
    "autoresearch/scan/**/*.py",
    "autoresearch/sector/*.py",
    "autoresearch/dossier/*.py",
    "autoresearch/common/scoring.py",
    "autoresearch/common/regime.py",
    "autoresearch/common/turnup.py",
    "autoresearch/session_agent/**/*.py",
    ".claude/workflows/*.js",
)

#: R9 —— 不得复述键值的文档。
DOC_FILES: tuple[str, ...] = (
    ".claude/skills/scan-market/SKILL.md",
    ".claude/skills/scan-market/STAGES.md",
    "docs/ops/scan-ops.md",
    "docs/flow-handbook.md",
)

#: 配置文件与守卫自身(hook 的路径匹配也用这份)。
CONFIG_PATH = ".claude/skills/scan-market/scan_config.jsonc"
GUARDED_PATHS: tuple[str, ...] = (
    ".claude/skills/scan-market/",
    "autoresearch/contracts/scan_config.py",
    "autoresearch/scan/user_config.py",
)


# ── 注册表级 knob(2026-09-27):下层(common / dossier / trace)读 config 不产生向上 import ──
#: `scan.user_config` import 时把 `load_user_config` 装进来;没装(纯 common 用法)= 内建缺省。
_LOADER = None


def install_loader(fn) -> None:
    global _LOADER
    _LOADER = fn


def knob(block: str, key: str, cli_value, default, cfg: dict | None = None):
    """显式值 > scan_config > 内建缺省(与 `user_config.knob` 同语义,同一份实现)。

    配置层故障 → stderr 留痕后回 default(降级必须可见)。
    """
    if cli_value is not None:
        return cli_value
    if cfg is None:
        if _LOADER is None:
            return default
        try:
            cfg = _LOADER() or {}
        except Exception as e:  # noqa: BLE001 — 坏配置响亮警告后按默认跑,不让扫描失败
            import sys
            print(f"[warn] scan_config 读取失败({e!r})→ {block}.{key} 用内建默认 {default!r}",
                  file=sys.stderr)
            return default
    v = (cfg.get(block) or {}).get(key)
    return default if v is None else v


# ── 派生视图 ─────────────────────────────────────────────────────────────────
def block_order() -> tuple[str, ...]:
    return tuple(b.name for b in BLOCKS)


def block(name: str) -> Block:
    for b in BLOCKS:
        if b.name == name:
            return b
    raise KeyError(name)


def keys_of(block_name: str) -> tuple[Key, ...]:
    return tuple(k for k in KEYS if k.block == block_name)


def find(block_name: str, key: str) -> Key | None:
    for k in KEYS:
        if k.block == block_name and k.key == key:
            return k
    return None


def top_whitelist() -> frozenset[str]:
    return frozenset(k.block for k in KEYS)


def sub_whitelist() -> dict[str, frozenset[str]]:
    out: dict[str, set[str]] = {}
    for k in KEYS:
        if k.kind == KIND_STRUCTURED:
            continue
        out.setdefault(k.block, set()).add(k.key)
    return {b: frozenset(v) for b, v in out.items()}


def knob_types() -> dict[tuple[str, str], tuple]:
    return {(k.block, k.key): VALIDATORS[k.type] for k in KEYS
            if k.kind != KIND_STRUCTURED and k.type != "any"}


def host_tag(hosts: str) -> str:
    return _HOST_TAGS.get(hosts, "")


def zone_of(block_name: str) -> str:
    return block(block_name).zone


def constant_reason_kind(reason: str) -> str | None:
    for kind in CONSTANT_REASON_KINDS:
        if reason.startswith(kind):
            return kind
    return None


def render_symbol(sym: str) -> str:
    """``autoresearch.scan.frame:build_market_frame`` → ``scan/frame.build_market_frame``;js 路径原样去前缀。"""
    if sym.endswith(".js"):
        return sym.replace(".claude/", "")
    mod, _, fn = sym.partition(":")
    mod = mod.removeprefix("autoresearch.").replace(".", "/")
    return f"{mod}.{fn}" if fn else mod


def render_block_header(block_name: str) -> str:
    b = block(block_name)
    seen: list[str] = []
    for k in keys_of(block_name):
        for sym in k.consumers:
            shown = render_symbol(sym)
            if shown not in seen:
                seen.append(shown)
    return f"  // ── {b.name} · {b.summary} ── 生效点 {' · '.join(seen)}"


def render_zone_header(zone: str) -> str:
    return f"  // ═══ {ZONE_TITLES[zone]} ═══"
