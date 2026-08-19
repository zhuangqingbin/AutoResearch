#!/usr/bin/env python3
"""确定性事件类型学 —— 多标签 + 生命周期(确定性,零 LLM,I 类:不进提示词)。

design: docs/specs/2026-08-03-scan-next-wave-brainstorm-design.md §1.2 D2

## 升级了什么

现状(`scan/agents/l3_news.py`)只产**方向 tag**:利多/利空/中性。三处不够用:

1. **一个标题可以同时是几件事**:「关于对深交所问询函回复暨风险提示的公告」既是
   问询回复,又是风险提示。压成单 `type` 会丢掉一半。
2. **回购/减持/重组必须区分生命周期**:预案 / 实施 / 完成 / 终止。「拟回购」与
   「回购完成」在价格上是两件相反的事,一个 `type` 覆盖不了。
3. **匹配位置要留痕**:`matched_spans` 让「为什么判成这个 type」可复核 —— 词表命中
   在哪几个字上,是唯一能事后证伪的东西。

## 用途分层(§1.2,不可越级)

  ① 落结构化 artifact               → **I 类**(本模块的全部)
  ② 渲染到 L3/L4 prompt             → **已是 B 类**,须 registry
  ③ 每个 type 的候选因子            → 各自走 factor_lab

所以本模块**只产 artifact**,不导出任何「注入用」渲染函数。

## 两条假设纪律

- **业绩类不做追涨信号**:业绩预告披露后追买已被否(强制披露季 T+5 超额 −0.27%/
  胜率 35%,追缺口 −2.92%)。`ASSUMPTION_POLICY` 把它写进模块常量。
- **非业绩类也一律不用当日涨幅入场**(当日 ≥9.5% 的 350 只 fwd_2_oc 超额 −3.67pp,
  t=−11.91;**该实证在参考尺 `fwd_2_oc` 下测得**,现主尺 = `gap_c1_o2`,未在新尺复测——
  纪律本身不因换尺松动,要翻案须按新尺重跑并单独预注册)。任何 type 要做信号,
  必须**单独预注册**方向、窗口与 no-harm。

## 信号日期

按扫描 cutoff / 下一可交易日滚动,**不能只用 ann_date** —— 公告可能在盘后甚至周末发出,
拿 ann_date 当信号日等于假设自己能在公告瞬间成交。

  uv run --no-sync python -m autoresearch.news.typed_events tag "关于回购股份的进展公告"
  uv run --no-sync python -m autoresearch.news.typed_events coverage
"""
from __future__ import annotations

import argparse
import json
import re
from dataclasses import asdict, dataclass
from pathlib import Path

import pandas as pd

from autoresearch.common import workspace as ws

SCHEMA_VERSION = 1
RULE_VERSION = "typed_events.v1"
OUT_JSON = ws.reports_root() / "research/typed_events_coverage.json"

# ── 生命周期(顺序有意义:后面的覆盖前面的)────────────────────────
PLAN = "plan"            # 预案 / 拟
IN_PROGRESS = "in_progress"   # 实施中 / 进展
COMPLETED = "completed"  # 完成 / 实施完毕
TERMINATED = "terminated"     # 终止 / 取消 / 撤回
UNKNOWN_LIFECYCLE = "unknown"
LIFECYCLES = (PLAN, IN_PROGRESS, COMPLETED, TERMINATED, UNKNOWN_LIFECYCLE)

_LIFECYCLE_WORDS: list[tuple[str, tuple[str, ...]]] = [
    # 顺序 = 优先级:终止 > 完成 > 进展 > 预案。「终止回购方案」必须判终止,不是预案。
    (TERMINATED, ("终止", "取消", "撤回", "终结", "不再实施", "放弃")),
    (COMPLETED, ("完成", "实施完毕", "已实施完成", "结果公告", "届满", "实施完成")),
    (IN_PROGRESS, ("进展", "实施情况", "进度", "首次实施", "部分实施")),
    (PLAN, ("预案", "拟", "计划", "方案", "提议", "意向")),
]

# ── 事件类型(多标签)────────────────────────────────────────────
#
# `lifecycle_aware=True` 的类型**必须**区分生命周期(§1.2 点名的回购/减持/重组)。
# `earnings=True` 的类型受「业绩类不做追涨信号」约束。
@dataclass(frozen=True)
class EventType:
    name: str
    words: tuple
    direction: str = ""          # 词表先验方向,**不是**结论
    strength: float = 1.0
    lifecycle_aware: bool = False
    earnings: bool = False


EVENT_TYPES: tuple[EventType, ...] = (
    EventType("buyback", ("回购",), "利多", 2.0, lifecycle_aware=True),
    EventType("shareholder_increase", ("增持",), "利多", 2.0, lifecycle_aware=True),
    EventType("shareholder_reduce", ("减持",), "利空", 2.0, lifecycle_aware=True),
    EventType("restructuring", ("重组", "收购", "资产注入", "吸收合并"), "利多", 2.0,
              lifecycle_aware=True),
    EventType("equity_incentive", ("股权激励", "员工持股"), "利多", 1.0,
              lifecycle_aware=True),
    EventType("private_placement", ("定增", "非公开发行", "向特定对象发行"), "", 1.0,
              lifecycle_aware=True),
    EventType("order_win", ("中标", "订单", "签约", "框架协议"), "利多", 1.5),
    EventType("approval", ("获批", "批准", "取得注册证", "通过审核"), "利多", 1.5),
    EventType("regulator_inquiry", ("问询函", "关注函", "监管函"), "利空", 1.5),
    EventType("regulator_penalty", ("立案", "处罚", "违规", "警示函"), "利空", 2.0),
    EventType("litigation", ("诉讼", "仲裁", "冻结", "查封"), "利空", 1.5),
    EventType("clarification", ("澄清", "辟谣", "说明公告", "风险提示"), "", 1.0),
    EventType("pledge", ("质押", "解押"), "", 1.0),
    EventType("delisting_risk", ("退市", "*ST", "暂停上市"), "利空", 2.0),
    EventType("impairment", ("商誉减值", "资产减值", "计提减值"), "利空", 1.5),
    EventType("earnings_forecast", ("业绩预告", "预增", "预减", "预亏", "预盈", "扭亏"),
              "", 1.0, earnings=True),
    EventType("earnings_report", ("年度报告", "半年度报告", "季度报告", "业绩快报"),
              "", 1.0, earnings=True),
)
_TYPES_BY_NAME = {t.name: t for t in EVENT_TYPES}

# 否定/澄清词 —— 命中即把**方向**中性化(保守不翻转)。类型标签仍保留:
# 「否认重组传闻」依然是一条 restructuring 相关的观测,只是方向不能算利多。
_NEGATORS = ("未", "不", "否认", "澄清", "辟谣", "无", "暂不", "拟不", "取消")

ASSUMPTION_POLICY = {
    "earnings": ("业绩预告披露后追买已被否(强制披露季 T+5 超额 −0.27%/胜率 35%,"
                 "追缺口 −2.92%)—— 业绩类**不直接做追涨信号**"),
    "all_types": ("任何 type 都不得用**当日涨幅**入场(当日 ≥9.5% 的 350 只 "
                  "fwd_2_oc 超额 −3.67pp,t=−11.91;该实证在参考尺 fwd_2_oc 下测得,"
                  "现主尺 gap_c1_o2 未复测);要做信号必须单独预注册"
                  "方向、窗口与 no-harm"),
    "signal_date": ("信号日按扫描 cutoff / 下一可交易日滚动,不能只用 ann_date —— "
                    "公告可能盘后或周末发出"),
    "usage_tier": ("① 落 artifact = I 类;② 渲染进 L3/L4 prompt = **B 类**须 registry;"
                   "③ 每个 type 的候选因子各自走 factor_lab"),
}


@dataclass
class TypedEvent:
    """一条标题的类型学结果。主键引用 `source_observation_id`(§1.2)。"""
    source_observation_id: str
    title: str
    event_types: list
    lifecycle_status: str
    direction: str
    strength: float
    matched_spans: list
    confidence: float
    ambiguous: bool
    rule_version: str = RULE_VERSION
    first_seen_ts: str | None = None

    def as_dict(self) -> dict:
        return asdict(self)


def _spans(title: str, words: tuple) -> list[dict]:
    out = []
    for word in words:
        for m in re.finditer(re.escape(word), title):
            out.append({"word": word, "start": m.start(), "end": m.end()})
    return out


def lifecycle_of(title: str) -> tuple[str, list[dict]]:
    """生命周期 + 命中位置。优先级见 `_LIFECYCLE_WORDS`(终止 > 完成 > 进展 > 预案)。"""
    text = str(title or "")
    for status, words in _LIFECYCLE_WORDS:
        spans = _spans(text, words)
        if spans:
            return status, spans
    return UNKNOWN_LIFECYCLE, []


def tag(title: str, *, source_observation_id: str = "",
        first_seen_ts: str | None = None) -> TypedEvent:
    """标题 → 多标签事件记录。**一个标题可以同时属于多个 type**。

    `ambiguous=True` 的两种情形:①命中 ≥2 个方向相反的 type;②命中了否定词。
    两者都不该被下游当成确定的方向 —— 标出来,让消费者自己决定要不要用。
    """
    text = str(title or "")
    hits: list[str] = []
    spans: list[dict] = []
    pos = neg = 0.0
    lifecycle_aware = False
    earnings = False
    for etype in EVENT_TYPES:
        found = _spans(text, etype.words)
        if not found:
            continue
        hits.append(etype.name)
        spans.extend({**s, "type": etype.name} for s in found)
        lifecycle_aware = lifecycle_aware or etype.lifecycle_aware
        earnings = earnings or etype.earnings
        if etype.direction == "利多":
            pos += etype.strength
        elif etype.direction == "利空":
            neg += etype.strength

    negated = any(w in text for w in _NEGATORS)
    lifecycle, life_spans = lifecycle_of(text)
    if lifecycle_aware:
        spans.extend({**s, "type": "__lifecycle__"} for s in life_spans)

    direction = ""
    if not negated:
        if pos > neg:
            direction = "利多"
        elif neg > pos:
            direction = "利空"
    # 终止一件利多的事 ≈ 利空,但**不翻转方向**(保守):只标 ambiguous 让消费者判断。
    ambiguous = bool(negated or (pos > 0 and neg > 0)
                     or (lifecycle == TERMINATED and direction == "利多"))

    confidence = 0.0
    if hits:
        confidence = 0.9 if not ambiguous else 0.4
        if lifecycle_aware and lifecycle == UNKNOWN_LIFECYCLE:
            confidence -= 0.2      # 该分生命周期却分不出 → 降信心,不是硬判
    return TypedEvent(
        source_observation_id=source_observation_id, title=text,
        event_types=sorted(hits), lifecycle_status=lifecycle,
        direction=direction, strength=round(max(pos, neg), 3),
        matched_spans=spans, confidence=round(max(0.0, confidence), 3),
        ambiguous=ambiguous, first_seen_ts=first_seen_ts)


def is_earnings(event: TypedEvent) -> bool:
    return any(_TYPES_BY_NAME[t].earnings for t in event.event_types
               if t in _TYPES_BY_NAME)


def assert_not_chase_signal(event: TypedEvent, *, same_day_return: float | None,
                            preregistered: bool = False) -> None:
    """把两条假设纪律做成检查:业绩类不追涨、任何 type 都不得用当日涨幅入场。"""
    if same_day_return is not None:
        raise ValueError(
            f"事件 {event.event_types} 想用当日涨幅({same_day_return:+.2%})入场 —— "
            + ASSUMPTION_POLICY["all_types"])
    if is_earnings(event) and not preregistered:
        raise ValueError(
            f"业绩类事件 {event.event_types} 想直接做信号 —— "
            + ASSUMPTION_POLICY["earnings"])


# ───────────────────────── 从目录批量打标 ─────────────────────────


def tag_catalog(catalog=None, *, stage: str = "L3",
                cutoff: str | None = None) -> pd.DataFrame:
    """目录里的观测 → 类型学长表。`cutoff` 给定则走 PIT 回放,否则全量(仅供离线研究)。"""
    from autoresearch.news.catalog import NewsCatalog

    cat = catalog or NewsCatalog()
    obs = (cat.replay(None, cutoff, stage) if cutoff else cat.observations())
    if not len(obs):
        return pd.DataFrame(columns=["source_observation_id", "title", "event_types",
                                     "lifecycle_status", "direction", "strength",
                                     "confidence", "ambiguous", "rule_version",
                                     "first_seen_ts"])
    rows = []
    for row in obs.itertuples(index=False):
        event = tag(row.title, source_observation_id=row.source_observation_id,
                    first_seen_ts=row.first_seen_ts)
        record = event.as_dict()
        record["event_types"] = "|".join(record["event_types"])
        record["matched_spans"] = json.dumps(record["matched_spans"],
                                             ensure_ascii=False)
        rows.append(record)
    return pd.DataFrame(rows)


# ───────────────────────── PIT 覆盖矩阵(最小证伪步)─────────────────────────


def coverage_matrix(catalog=None) -> dict:
    """**先做 PIT 覆盖矩阵**,再谈 factor_lab(§1.2 最小证伪步)。

    设计稿的原话:「当前 fallback 是逐票近窗,**不能假设已有全市场历史湖**、更不能承诺
    『零新数据一晚回填』。只有覆盖和 first_seen 合格的 type 才进 factor_lab。」

    所以矩阵按 type × scope 统计,并对每个 type 给一个 `factor_lab_eligible`:
    要求该 type 有足够的 `market_wide` 观测,且 first_seen 全部为 `observed`
    (snapshot_inferred 的时间是推断的,拿它做因子日期等于自造 PIT)。
    """
    from autoresearch.news.catalog import (
        BASIS_OBSERVED,
        SCOPE_MARKET_WIDE,
        NewsCatalog,
    )

    cat = catalog or NewsCatalog()
    obs = cat.observations()
    if not len(obs):
        return {"schema_version": SCHEMA_VERSION, "rule_version": RULE_VERSION,
                "n_observations": 0, "types": {}, "assumption_policy": ASSUMPTION_POLICY}

    tagged = tag_catalog(cat)
    merged = tagged.merge(
        obs[["source_observation_id", "scope", "first_seen_basis", "available_stage"]],
        on="source_observation_id", how="left")

    types: dict[str, dict] = {}
    for etype in EVENT_TYPES:
        mask = merged["event_types"].fillna("").str.split("|").map(
            lambda names, n=etype.name: n in names)
        sub = merged[mask]
        market_wide = int((sub["scope"] == SCOPE_MARKET_WIDE).sum())
        observed = int((sub["first_seen_basis"] == BASIS_OBSERVED).sum())
        lifecycles = ({str(k): int(v) for k, v in
                       sub["lifecycle_status"].value_counts().items()} if len(sub) else {})
        reasons = []
        if market_wide < _MIN_MARKET_WIDE:
            reasons.append(f"market_wide 观测 {market_wide} < {_MIN_MARKET_WIDE}")
        if len(sub) and observed < len(sub):
            reasons.append(f"{len(sub) - observed} 条 first_seen 是 snapshot_inferred "
                           "—— 拿推断时间做因子日期等于自造 PIT")
        if not len(sub):
            reasons.append("零观测")
        if etype.lifecycle_aware and lifecycles.get(UNKNOWN_LIFECYCLE, 0) > 0:
            reasons.append(f"{lifecycles[UNKNOWN_LIFECYCLE]} 条生命周期不可判 —— "
                           "该 type 必须区分预案/实施/完成/终止")
        types[etype.name] = {
            "n": int(len(sub)), "market_wide": market_wide, "observed": observed,
            "lifecycles": lifecycles,
            "ambiguous": int(sub["ambiguous"].sum()) if len(sub) else 0,
            "earnings": etype.earnings,
            "lifecycle_aware": etype.lifecycle_aware,
            "factor_lab_eligible": not reasons,
            "blocking_reasons": reasons,
        }
    return {"schema_version": SCHEMA_VERSION, "rule_version": RULE_VERSION,
            "n_observations": int(len(obs)), "types": types,
            "assumption_policy": ASSUMPTION_POLICY}


_MIN_MARKET_WIDE = 30      # 进 factor_lab 前该 type 至少要有的全市场观测数


def render(matrix: dict) -> str:
    lines = ["# 事件类型学 PIT 覆盖矩阵(先覆盖,后 factor_lab)", "",
             f"- rule_version `{matrix['rule_version']}` · "
             f"观测 {matrix['n_observations']}",
             "",
             "| type | n | market_wide | observed | 生命周期 | 歧义 | 可进 factor_lab | 阻断原因 |",
             "|---|---:|---:|---:|---|---:|---|---|"]
    for name, row in matrix["types"].items():
        life = "、".join(f"{k}:{v}" for k, v in sorted(row["lifecycles"].items())) or "—"
        lines.append(
            f"| `{name}` | {row['n']} | {row['market_wide']} | {row['observed']} "
            f"| {life} | {row['ambiguous']} "
            f"| {'✅' if row['factor_lab_eligible'] else '⛔'} "
            f"| {'、'.join(row['blocking_reasons']) or '—'} |")
    lines += ["", "## 假设纪律", ""]
    lines += [f"- **{k}**:{v}" for k, v in matrix["assumption_policy"].items()]
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="确定性事件类型学(§1.2 D2)")
    sub = ap.add_subparsers(dest="cmd", required=True)
    t = sub.add_parser("tag", help="给一条标题打标(调试用)")
    t.add_argument("title")
    c = sub.add_parser("coverage", help="PIT 覆盖矩阵")
    c.add_argument("--catalog-root", default=None)
    c.add_argument("--json-out", default=str(OUT_JSON))

    a = ap.parse_args(argv)
    if a.cmd == "tag":
        print(json.dumps(tag(a.title).as_dict(), ensure_ascii=False, indent=2))
        return 0
    from autoresearch.news.catalog import NewsCatalog

    matrix = coverage_matrix(NewsCatalog(a.catalog_root))
    out = Path(a.json_out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(matrix, ensure_ascii=False, indent=2) + "\n",
                   encoding="utf-8")
    print(render(matrix))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
