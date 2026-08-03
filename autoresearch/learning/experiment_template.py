#!/usr/bin/env python3
"""统一实验模板 —— §5-2 的十项必填 + 「未显著 ≠ 等价」的裁决器(确定性,零 LLM)。

design: docs/specs/2026-08-03-scan-next-wave-brainstorm-design.md §5-1 / §5-2

`experiment_registry` 管的是**权威与状态机**(谁批的、能不能激活、回滚指向哪);本模块管
**方法学**:一个实验开工前必须先写清 H0/H1、cutoff、配对单位、聚类方式、最小样本与功效、
等价或 no-harm margin、多重检验、停止规则、rollback —— §5-2 的原话是「统一实验模板」。

两条纪律做成代码:

1. **可选停止是作弊**:`stopping_rule` 必填,且 `min_units` 必须在开工前定死。
   「跑到通过为止」在 Wave10 已被点名(EXP-1 的 negative_result_policy)。
2. **未显著 ≠ 等价**:`conclude()` 返回五态 —— `IMMATURE / UNKNOWN / EQUIVALENT /
   PASS / FAIL`。区间宽而跨 0 只会得到 `UNKNOWN`,永远得不到 `EQUIVALENT`;而
   `IMMATURE/UNKNOWN/FAIL` 一律 `DO_NOT_PROMOTE`(§5-1「IMMATURE/UNKNOWN/FAIL 不批」)。

还有一条写在 §5-1 末尾、最容易被业务压力冲掉的:**0 BUY 不是放松门的理由**。
`assert_not_zero_buy_justification` 把它变成一次会抛错的检查 —— 任何用「今天又 0 买」
当动机的实验都不能通过模板校验。

  uv run --no-sync python -m autoresearch.learning.experiment_template --show <id>
"""
from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict, dataclass
from pathlib import Path

from autoresearch.common import stats as st

SCHEMA_VERSION = 1

HIGHER_IS_BETTER = "higher_is_better"
LOWER_IS_BETTER = "lower_is_better"
DIRECTIONS = (HIGHER_IS_BETTER, LOWER_IS_BETTER)

# 配对单位:全稿的默认是**扫描日**(§4.4「按扫描日 paired/date-cluster bootstrap」)。
PAIRED_UNITS = ("scan_day", "candidate_day", "gate_fire", "run")
CLUSTERINGS = ("date_cluster", "none")
MULTIPLE_TESTING = ("bh_fdr", "bonferroni", "single_hypothesis")

# §5-1:0 BUY 不是放松门的理由。写死禁用动机,免得下次「连着 11 天 0 买」时它自己溜回来。
_BANNED_MOTIVATIONS = ("0 买", "0买", "零买", "zero buy", "zero-buy", "没有买单")

VERDICTS = ("IMMATURE", "UNKNOWN", "EQUIVALENT", "PASS", "FAIL")
PROMOTABLE = ("PASS",)


class TemplateError(ValueError):
    """实验模板缺项或自相矛盾 —— 开工前就该失败。"""


@dataclass(frozen=True)
class ExperimentTemplate:
    """§5-2 的十项。少一项就不是一个可裁决的实验,只是一个想法。"""
    experiment_id: str
    h0: str
    h1: str
    data_cutoff: str                   # 数据 cutoff(as-of;含「哪天之后的数据不许进」)
    paired_unit: str
    clustering: str
    min_units: int                     # 最小样本(预注册,不可事后调)
    target_power: float
    primary_metric: str
    direction: str
    equivalence_margin: float          # 等价或 no-harm margin(同一个数,用途见 no_harm)
    no_harm: bool
    multiple_testing: str
    n_hypotheses: int
    stopping_rule: str
    rollback: str
    motivation: str = ""
    # 统一成熟门(§1.2)在 min_units 之外的**下限**,如 {"unique_n": 30, "regimes": 2}。
    # 是「要求」不是「观测」—— 模板是预注册,观测值在 `conclude(observed_dims=...)` 时才给。
    maturity_minimums: dict | None = None

    def as_dict(self) -> dict:
        return asdict(self)


def validate(t: ExperimentTemplate) -> list[str]:
    """模板违约清单(空 = 合规)。"""
    bad: list[str] = []
    for label, value in (("experiment_id", t.experiment_id), ("h0", t.h0), ("h1", t.h1),
                         ("data_cutoff", t.data_cutoff), ("primary_metric", t.primary_metric),
                         ("stopping_rule", t.stopping_rule), ("rollback", t.rollback)):
        if not str(value).strip():
            bad.append(f"{label} 为空 —— §5-2 十项必填")
    if t.paired_unit not in PAIRED_UNITS:
        bad.append(f"paired_unit={t.paired_unit!r} 不在 {list(PAIRED_UNITS)}")
    if t.clustering not in CLUSTERINGS:
        bad.append(f"clustering={t.clustering!r} 不在 {list(CLUSTERINGS)}")
    if t.direction not in DIRECTIONS:
        bad.append(f"direction={t.direction!r} 不在 {list(DIRECTIONS)}")
    if t.multiple_testing not in MULTIPLE_TESTING:
        bad.append(f"multiple_testing={t.multiple_testing!r} 不在 {list(MULTIPLE_TESTING)}")
    if int(t.min_units) < 1:
        bad.append("min_units 必须 ≥1,且必须在开工前定死(可选停止是作弊)")
    if not 0.0 < float(t.target_power) < 1.0:
        bad.append("target_power 必须在 (0,1)")
    if float(t.equivalence_margin) <= 0:
        bad.append("equivalence_margin 必须 >0 —— margin=0 时任何区间都判不出等价")
    if int(t.n_hypotheses) < 1:
        bad.append("n_hypotheses 必须 ≥1")
    if int(t.n_hypotheses) > 1 and t.multiple_testing == "single_hypothesis":
        bad.append(f"n_hypotheses={t.n_hypotheses} 却声明 single_hypothesis —— "
                   "同时试多个假设必须声明修正方式(§1.2/§3.4)")
    if t.paired_unit != "scan_day" and t.clustering == "none":
        bad.append("非 scan_day 配对 + 无聚类 = 把同日多行当独立样本,区间会窄到假")
    bad += assert_not_zero_buy_justification(t.motivation, raising=False)
    return bad


def assert_not_zero_buy_justification(motivation: str, *, raising: bool = True) -> list[str]:
    """§5-1 末句:**0 BUY 不是放松门的理由**。

    这不是文风检查 —— 07 月连续 0 买的那几周,「门是不是太严了」的压力真实存在,而账本
    当时给出的答案恰恰相反(0 买日市场 fwd 为负 = 空仓方向正确)。用 0 买当动机立的实验,
    从第一行就已经在找想要的答案。
    """
    text = str(motivation or "").lower()
    hits = [p for p in _BANNED_MOTIVATIONS if p.lower() in text]
    if not hits:
        return []
    problem = (f"实验动机含 {hits} —— §5-1:0 BUY 不是放松门的理由。"
               "要立项请写它自己的 H1(如「该门的错杀率高于 X」),不要写「今天又没买」")
    if raising:
        raise TemplateError(problem)
    return [problem]


def require_valid(t: ExperimentTemplate) -> ExperimentTemplate:
    problems = validate(t)
    if problems:
        raise TemplateError(f"{t.experiment_id}: " + "; ".join(problems))
    return t


# ───────────────────────── 裁决:五态,`UNKNOWN` 不许伪装成 `EQUIVALENT` ─────────────────────────


_DIM_FLOOR_KWARG = {"subgroup_n": "min_subgroup", "unique_n": "min_unique",
                    "regimes": "min_regimes"}


def conclude(t: ExperimentTemplate, interval: st.Interval, *,
             maturity: st.Maturity | None = None,
             observed_units: int | None = None,
             observed_dims: dict | None = None) -> dict:
    """区间 + 成熟度 → 五态裁决 + 是否可推荐晋升。

    优先级有意义:**成熟度先于结论**。样本不够时连「未知」都不该说,直接 `IMMATURE` ——
    否则一个 n=3 的宽区间会被读成「我们试过了,没差别」。

    `direction` 决定哪一侧算「更好」:`lower_is_better` 的指标(如 false_kill_rate)
    区间整体低于 −margin 才是 `PASS`。

    成熟门的**下限**来自模板(预注册),**观测值**来自 `observed_units`/`observed_dims`。
    模板声明了下限却没给观测 → 该维度按 0 判(声明了要 30 个 unique 却一个都没报,
    只能是不达标,不能当「不适用」放行)。
    """
    require_valid(t)
    units = interval.n_clusters if observed_units is None else int(observed_units)
    floors = dict(t.maturity_minimums or {})
    observed = dict(observed_dims or {})
    kwargs = {_DIM_FLOOR_KWARG[k]: int(v) for k, v in floors.items()
              if k in _DIM_FLOOR_KWARG}
    kwargs.update({k: int(observed.get(k, 0)) for k in floors if k in _DIM_FLOOR_KWARG})
    mat = maturity or st.maturity_verdict(scan_days=units,
                                          min_scan_days=int(t.min_units), **kwargs)
    if mat.status == st.IMMATURE:
        return _verdict(t, interval, "IMMATURE", "SAMPLE_IMMATURE", mat, units)

    if interval.lo is None or interval.hi is None:
        return _verdict(t, interval, "UNKNOWN", "NO_INTERVAL", mat, units)

    margin = float(t.equivalence_margin)
    # 统一到「越大越好」的坐标系再判:`lower_is_better` 的指标(false_kill_rate 之类)
    # 把区间取负后,「改善」与「变差」的方向就和 higher 一致 —— 两套符号逻辑各写一遍
    # 正是最容易写反的地方。
    oriented = interval if t.direction == HIGHER_IS_BETTER else st.Interval(
        None if interval.point is None else -interval.point,
        -interval.hi, -interval.lo, interval.n, interval.n_clusters,
        interval.method + "(negated)", interval.alpha)

    if oriented.lo > margin:
        return _verdict(t, interval, "PASS", "BEYOND_MARGIN_IMPROVING", mat, units)
    if oriented.hi < -margin:
        return _verdict(t, interval, "FAIL", "BEYOND_MARGIN_HARMING", mat, units)
    equivalence = (st.no_harm_verdict(oriented, margin) if t.no_harm
                   else st.equivalence_verdict(oriented, margin))
    if equivalence == st.EQUIVALENT:
        return _verdict(t, interval, "EQUIVALENT", "INSIDE_EQUIVALENCE_BAND", mat, units)
    return _verdict(t, interval, "UNKNOWN", "INTERVAL_SPANS_MARGIN", mat, units)


def _verdict(t: ExperimentTemplate, interval: st.Interval, verdict: str,
             reason: str, mat: st.Maturity, units: int) -> dict:
    """§5-1:只有 `PASS` 可以推荐晋升;其余一律 `DO_NOT_PROMOTE`。

    `EQUIVALENT` 也不晋升 —— 它说的是「challenger 没变好」,那本来就没有理由换掉基线。
    """
    return {
        "schema_version": SCHEMA_VERSION,
        "experiment_id": t.experiment_id,
        "verdict": verdict,
        "reason": reason,
        "recommendation": "PROMOTE" if verdict in PROMOTABLE else "DO_NOT_PROMOTE",
        "primary_metric": t.primary_metric,
        "direction": t.direction,
        "equivalence_margin": float(t.equivalence_margin),
        "no_harm": bool(t.no_harm),
        "interval": interval.as_dict(),
        "maturity": mat.as_dict(),
        "observed_units": units,
        "min_units": int(t.min_units),
        "multiple_testing": t.multiple_testing,
        "n_hypotheses": int(t.n_hypotheses),
        "stopping_rule": t.stopping_rule,
        # 读的人最容易犯的错就在这一行:把 UNKNOWN 抄成「无差异」。
        "readback_guard": ("UNKNOWN 表示区间过宽,既不能说有增量也不能说无增量;"
                           "只有 EQUIVALENT 才可以写「无增量」"),
    }


def to_registry_definition(t: ExperimentTemplate) -> dict:
    """模板 → registry spec 的 `definition` 块(方法学随 definition_hash 一起被钉死)。

    钉死的价值在于:改了 margin、改了 min_units、改了聚类方式,`definition_hash` 就变,
    registry 会拒绝沿用旧 id —— 「中途把 margin 放宽到刚好通过」这条路被堵死。
    """
    require_valid(t)
    return {
        "template_schema_version": SCHEMA_VERSION,
        "H0": t.h0,
        "H1": t.h1,
        "data_cutoff": t.data_cutoff,
        "paired_unit": t.paired_unit,
        "clustering": t.clustering,
        "min_units": int(t.min_units),
        "target_power": float(t.target_power),
        "primary_metric": t.primary_metric,
        "direction": t.direction,
        "equivalence_margin": float(t.equivalence_margin),
        "no_harm": bool(t.no_harm),
        "multiple_testing": t.multiple_testing,
        "n_hypotheses": int(t.n_hypotheses),
        "stopping_rule": t.stopping_rule,
        "rollback": t.rollback,
        "motivation": t.motivation,
    }


def minimums_for(t: ExperimentTemplate) -> dict:
    """模板 → registry spec 的 `minimums` 块(与统一成熟门 §1.2 对齐)。"""
    dims = t.maturity_minimums or {}
    return {
        "forward_days": int(t.min_units),
        "mature_events": int(dims.get("subgroup_n") or st.MATURITY_MIN_SUBGROUP),
        "unique_events": int(dims.get("unique_n") or 0),
        "regimes": int(dims.get("regimes") or st.MATURITY_MIN_REGIMES),
    }


def render(verdict: dict) -> str:
    lines = [f"### {verdict['experiment_id']} —— **{verdict['verdict']}**",
             "",
             f"- 判据 `{verdict['primary_metric']}`({verdict['direction']}) · "
             f"margin ±{verdict['equivalence_margin']}"
             + ("(no-harm 单边)" if verdict["no_harm"] else ""),
             f"- 理由:`{verdict['reason']}` · 建议:**{verdict['recommendation']}**"]
    iv = verdict["interval"]
    point = "—" if iv["point"] is None else f"{iv['point']:+.4f}"
    band = ("—" if iv["lo"] is None
            else f"[{iv['lo']:+.4f}, {iv['hi']:+.4f}]")
    lines += [f"- 点估计 {point} · 区间 {band} · n={iv['n']} · "
              f"聚簇={iv['n_clusters']} · 方法 `{iv['method']}`",
              f"- 成熟度:{verdict['maturity']['status']}"
              + ("" if not verdict["maturity"]["missing"]
                 else "(缺:" + "、".join(verdict["maturity"]["missing"]) + ")"),
              f"- ⚠️ {verdict['readback_guard']}", ""]
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="统一实验模板(§5-2)")
    ap.add_argument("--template", help="模板 JSON 路径")
    ap.add_argument("--check", action="store_true", help="只校验,违约 → 退出码 1")
    a = ap.parse_args(argv)
    if not a.template:
        ap.error("--template 必填")
    payload = json.loads(Path(a.template).read_text(encoding="utf-8"))
    try:
        t = ExperimentTemplate(**payload)
    except TypeError as exc:
        print(f"  ✗ 模板字段不匹配:{exc}", file=sys.stderr)
        return 1
    problems = validate(t)
    for p in problems:
        print(f"  ✗ {t.experiment_id}: {p}", file=sys.stderr)
    if problems:
        return 1
    print(json.dumps(to_registry_definition(t), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
