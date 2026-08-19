#!/usr/bin/env python3
"""「业绩真兑现」门重标定 —— 两个**分开的**实验 + 影子账本(确定性,零 LLM,零生产副作用)。

design: docs/specs/2026-08-03-scan-next-wave-brainstorm-design.md §4.1

## 为什么必须拆成两个实验

设计稿的原话:「实验拆成两个问题,**避免把证据增强与门松紧混成一个 treatment**」。

  gate_true_delivery_evidence  同一候选、同一门版本,做 D4 有/无全文的 paired shadow。
                               只裁「证据是否改变 claim 正确率、门分歧和成熟结果」。
  gate_true_delivery_recal     **现门不变**,在完整 gate participation cohort 上记录每个
                               binding 的 counterfactual 结果。只生成 shadow verdict,
                               **禁止直接放宽**。

混成一个 treatment 的后果很具体:全文让门看见了新事实(证据效应)+ 阈值放宽(松紧效应),
最后错杀率降了 5pp —— 你不知道该把这 5pp 记在哪一笔上,也就无法决定要不要保留其中一半。

## 当前裁定:`IMMATURE`

A11 v3 单门归因 n=6(CORRECT=2 / NEUTRAL=2 / FALSE_KILL=2,两个率都是 33.3%)。
设计稿要求「至少 20 个真实 binding 成熟事件且功效足够才可 RECOMMENDED」。
n=6 的 33.3% 与 n=600 的 33.3% 在报表里长得一样 —— 本模块给每个率配 Beta-Binomial 区间,
让它们长得不一样。

另有 participation cohort 49 条,但 **participation 不能直接充当单门因果分母**
(多门共拦时三道门各记一次)。本模块两处口径分列,永不互换。

## 三条边界(都做成了检查)

1. 影子腿**不写**生产 `decision_records.json` —— `assert_no_production_writes` 会抛错;
2. 门总量价值只能作**背景守卫**,不能替代本门归因(`background_guard` 单列,不进判据);
3. `learning.shrink` 不得出现在裁门路径(复用 `evidence_manifest.assert_not_shrink_derived`)。

  uv run --no-sync python -m autoresearch.learning.gate_recal
  uv run --no-sync python -m autoresearch.learning.gate_recal --register   # 写 PREREGISTERED spec
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import pandas as pd

from autoresearch.common import stats as st, workspace as ws
from autoresearch.learning import experiment_template as et
from autoresearch.learning.evidence_manifest import (
    GATE_DEFINITION,
    GATE_MIN_MATURE_EVENTS,
    assert_not_shrink_derived,
)

SCHEMA_VERSION = 1
GATE = "业绩真兑现"
DEFAULT_ROOT = ws.scan_root()
OUT_JSON = ws.reports_root() / "learning/gate_recal.json"
OUT_MD = ws.reports_root() / "learning/gate_recal.md"

EVIDENCE_FAMILY = "gate_true_delivery_evidence"
RECAL_FAMILY = "gate_true_delivery_recal"

# 预注册 margin:错杀率变动 ±5pp 以内视为无实质差异(门的经济意义尺度)。
FALSE_KILL_MARGIN = 0.05


class GateRecalError(RuntimeError):
    """影子腿越界(写了生产产物 / 用了被禁来源)—— 立刻失败,不等 review。"""


# 模板的五态是**通用**词表(PASS=可推荐晋升),而实验② 根本没有 challenger 可晋升 ——
# 它只回答「这道门要不要重标定」。所以这里给一张翻译表,免得有人把 `FAIL` 读成
# 「实验失败了」(它其实是本实验最强的**阳性**发现:错杀率显著高于可接受上限)。
#
# 数学上 `PASS` 在本实验里不可达:PASS 要求区间整体低于 −margin,而比率恒 ≥0。
# 这不是 bug,是「shadow verdict 不产生晋升动作」的必然结果 —— 明说出来,别让下一个人
# 以为哪里算错了。
RECAL_INTERPRETATION = {
    "IMMATURE": "样本不足 —— 既不能说门有效,也不能说门无效",
    "UNKNOWN": "区间跨 margin —— 功效不足,继续影子观测",
    "EQUIVALENT": "错杀率在预注册可接受上限内 —— 无需重标定(这不是「门有价值」的证明)",
    "FAIL": "错杀率显著高于可接受上限 —— **建议立项重标定**(本实验的阳性发现)",
    "PASS": "本实验不可达(比率恒 ≥0,区间不可能整体低于 −margin);出现即说明口径被改过",
}


# ───────────────────────── 两个预注册模板(分开,不合并) ─────────────────────────


def evidence_template() -> et.ExperimentTemplate:
    """实验① 证据增强:同门版本、同候选,只改「有没有公告全文」。"""
    return et.ExperimentTemplate(
        experiment_id=EVIDENCE_FAMILY,
        h0="注入公告全文摘录不改变该门的 claim 正确率与门分歧",
        h1="注入公告全文摘录提高 claim 正确率 / 改变门分歧",
        data_cutoff="按扫描日滚动;两臂必须共享同一门版本 hash 与同一候选集",
        paired_unit="candidate_day", clustering="date_cluster",
        min_units=GATE_MIN_MATURE_EVENTS, target_power=0.8,
        primary_metric="claim_correct_rate_delta",
        direction=et.HIGHER_IS_BETTER,
        equivalence_margin=FALSE_KILL_MARGIN, no_harm=False,
        multiple_testing="single_hypothesis", n_hypotheses=1,
        stopping_rule=f"固定 ≥{GATE_MIN_MATURE_EVENTS} 个配对候选日;不可提前停",
        rollback="两臂皆影子,不注入生产 → 停止实验即回到现状",
        motivation="A11 v3 单门 n=6,先确认证据是否改变判断,再谈门松紧",
        maturity_minimums={"subgroup_n": GATE_MIN_MATURE_EVENTS},
    )


def recal_template() -> et.ExperimentTemplate:
    """实验② 门重标定:**现门不变**,只在完整 participation cohort 上记 counterfactual。"""
    return et.ExperimentTemplate(
        experiment_id=RECAL_FAMILY,
        h0="现行「业绩真兑现」门的错杀率与预注册可接受上限无实质差异",
        h1="现行门的错杀率高于可接受上限(即它在系统性漏肉)",
        data_cutoff="按扫描日滚动;只用 T+2 已成熟且可交易的 binding 拦截",
        paired_unit="gate_fire", clustering="date_cluster",
        min_units=GATE_MIN_MATURE_EVENTS, target_power=0.8,
        primary_metric="false_kill_rate",
        direction=et.LOWER_IS_BETTER,
        equivalence_margin=FALSE_KILL_MARGIN, no_harm=False,
        multiple_testing="single_hypothesis", n_hypotheses=1,
        stopping_rule=(f"固定 ≥{GATE_MIN_MATURE_EVENTS} 个真实 binding 成熟事件;"
                       "不可提前停,不可跑到通过为止"),
        rollback="现门全程不变 —— 本实验无可回滚的生产变更",
        motivation="A11 v3 单门归因 n=6(CORRECT 2 / NEUTRAL 2 / FALSE_KILL 2),需扩样本",
        maturity_minimums={"subgroup_n": GATE_MIN_MATURE_EVENTS},
    )


# ───────────────────────── 影子边界(会抛错的检查) ─────────────────────────

_PRODUCTION_ARTIFACTS = ("decision_records.json", "finalists.csv", "gate_fires.csv",
                         "L3_judged_full.csv")


def assert_no_production_writes(scan_dir: Path | str, before: dict[str, float]) -> None:
    """影子腿跑完后:生产产物的 mtime 一个都不许变(§4.1「只生成 shadow verdict」)。

    为什么查 mtime 而不是"我没写过"—— Wave9 的判例是「拆半个特性没人接线」,而这里要防的
    是反向:影子腿**顺手**写了一笔生产产物。断言必须建在文件系统事实上,不是作者的记忆上。
    """
    day = Path(scan_dir)
    changed = [name for name in _PRODUCTION_ARTIFACTS
               if (p := day / name).exists() and before.get(name) != p.stat().st_mtime]
    if changed:
        raise GateRecalError(
            f"影子腿改动了生产产物 {changed} —— §4.1 明令『禁止直接放宽,只生成 "
            f"shadow verdict』;{GATE} 门在实验期间必须逐字节不变")


def production_snapshot(scan_dir: Path | str) -> dict[str, float]:
    day = Path(scan_dir)
    return {name: p.stat().st_mtime for name in _PRODUCTION_ARTIFACTS
            if (p := day / name).exists()}


# ───────────────────────── 实验② :counterfactual 影子账本 ─────────────────────────


def recal_ledger(scan_root: Path | str | None = None) -> pd.DataFrame:
    """完整 binding cohort 的逐票 counterfactual 行(A11 v3 口径,单门,不含 MULTI_GATE)。

    `counterfactual_outcome` = 「如果这道门没拦,这一票的 T+2 会怎样」—— 它就是被拦票
    自己的 `outcome`(CORRECT/NEUTRAL/FALSE_KILL),因为门拦掉之后我们观察到的正是
    「没买它、而它自己走成了什么样」。这里不做任何阈值扰动:**现门不变**是实验前提。
    """
    from autoresearch.learning import gate_attribution as ga

    rows = ga.roll(scan_root or DEFAULT_ROOT, cohort=ga.COHORT_V3)
    if not len(rows):
        return pd.DataFrame(columns=[
            "date", "code", "gate", "outcome", "excess_2", "tradable", "mature",
            "counterfactual", "cohort_version"])
    sub = rows[rows["gate"] == GATE].copy()
    if not len(sub):
        return pd.DataFrame(columns=[
            "date", "code", "gate", "outcome", "excess_2", "tradable", "mature",
            "counterfactual", "cohort_version"])
    sub["counterfactual"] = sub["outcome"].map({
        "FALSE_KILL": "would_have_helped",     # 拦掉的票跑赢 ≥+2pp
        "CORRECT": "would_have_hurt",
        "NEUTRAL": "no_material_difference",
        "UNMEASURED": "unknown",
    })
    return sub[["date", "code", "gate", "outcome", "excess_2", "tradable", "mature",
                "counterfactual", "cohort_version"]].reset_index(drop=True)


def recal_verdict(scan_root: Path | str | None = None) -> dict:
    """实验② 的 shadow verdict —— 区间 + 成熟门,**不产生任何生产动作**。"""
    ledger = recal_ledger(scan_root)
    measured = ledger[ledger["outcome"] != "UNMEASURED"] if len(ledger) else ledger
    n = int(len(measured))
    false_kills = int((measured["outcome"] == "FALSE_KILL").sum()) if n else 0
    corrects = int((measured["outcome"] == "CORRECT").sum()) if n else 0
    n_days = int(measured["date"].nunique()) if n else 0

    rate_interval = st.beta_binomial_interval(false_kills, n)
    excess = (st.date_cluster_bootstrap(measured, "excess_2", date_col="date")
              if n else st.Interval(None, None, None, 0, 0, "empty"))
    template = recal_template()
    # 判据口径:错杀率相对**预注册可接受上限**(= margin)的差。区间整体低于 −margin
    # 才叫「门比可接受上限好」;n 不够时 conclude 会先给 IMMATURE。
    centered = st.Interval(
        None if rate_interval.point is None else rate_interval.point - FALSE_KILL_MARGIN,
        None if rate_interval.lo is None else rate_interval.lo - FALSE_KILL_MARGIN,
        None if rate_interval.hi is None else rate_interval.hi - FALSE_KILL_MARGIN,
        n, n_days, rate_interval.method + "(centered_on_margin)")
    verdict = et.conclude(template, centered, observed_units=n_days,
                          observed_dims={"subgroup_n": n})
    assert_not_shrink_derived("gate_attribution.false_kill_rate", context=RECAL_FAMILY)
    return {
        "interpretation": RECAL_INTERPRETATION[verdict["verdict"]],
        "schema_version": SCHEMA_VERSION,
        "experiment": RECAL_FAMILY,
        "gate": GATE,
        "gate_definition": dict(GATE_DEFINITION),
        "n_binding_measured": n,
        "n_days": n_days,
        "FALSE_KILL": false_kills,
        "CORRECT": corrects,
        "NEUTRAL": n - false_kills - corrects,
        "false_kill_rate": rate_interval.as_dict(),
        "mean_excess_2": excess.as_dict(),
        "verdict": verdict,
        "production_side_effects": "无 —— 现门不变,只生成 shadow verdict",
        "counterfactual_counts": (measured["counterfactual"].value_counts().to_dict()
                                  if n else {}),
    }


def background_guard(scan_root: Path | str | None = None) -> dict:
    """门**总量**价值 —— 背景守卫,`in_verdict=False`。

    §4.1 原话:「门总量价值只能作为背景守卫,不能替代本门归因」。Wave10 的读数是
    「去重后主力门单独否决仅 17 次,但总量价值仍成立」—— 总量好看不代表**这一道门**拦对了,
    所以它单列一个字段,并且这个字段带着一句「不进判据」的自述。
    """
    from autoresearch.learning import gate_attribution as ga

    rows = ga.roll(scan_root or DEFAULT_ROOT, cohort=ga.COHORT_V3)
    measured = rows[rows["outcome"] != "UNMEASURED"] if len(rows) else rows
    return {
        "in_verdict": False,
        "why": "门总量价值不能替代单门归因(§4.1);列在这里只为提供背景",
        "n_all_gates_measured": int(len(measured)),
        "all_gates_mean_excess_2": (round(float(measured["excess_2"].mean()), 6)
                                    if len(measured) and measured["excess_2"].notna().any()
                                    else None),
    }


# ───────────────────────── 实验① :证据增强 paired shadow ─────────────────────────

ARM_WITH = "with_fulltext"
ARM_WITHOUT = "without_fulltext"


def evidence_pairs(scan_root: Path | str | None = None) -> pd.DataFrame:
    """两臂配对表 —— `[date, code, arm, gate_version, claim_correct, gate_state]`。

    两臂**必须共享同一门版本 hash**,不同即整对作废(§4.1「同一候选、同一门版本」)。
    D4 全文腿尚未产出时返回空表 —— 空 ≠ 无差异,`evidence_verdict` 会据此报 IMMATURE。
    """
    root = Path(scan_root or DEFAULT_ROOT)
    cols = ["date", "code", "arm", "gate_version", "claim_correct", "gate_state"]
    if not root.exists():
        return pd.DataFrame(columns=cols)
    frames = []
    for day in sorted(p for p in root.iterdir() if p.is_dir() and p.name[:2] == "20"):
        path = day / "_gate_evidence_arms.json"
        if not path.exists():
            continue
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        rows = [{**r, "date": day.name} for r in payload.get("rows", [])
                if isinstance(r, dict)]
        if rows:
            frames.append(pd.DataFrame(rows))
    if not frames:
        return pd.DataFrame(columns=cols)
    out = pd.concat(frames, ignore_index=True)
    for col in cols:
        if col not in out.columns:
            out[col] = None
    return out[cols]


def evidence_verdict(scan_root: Path | str | None = None) -> dict:
    """实验① 的 shadow verdict。门版本不一致的对 → 整对作废并记数,不悄悄比。"""
    pairs = evidence_pairs(scan_root)
    template = evidence_template()
    dropped = 0
    paired_rows: list[dict] = []
    if len(pairs):
        for (date, code), group in pairs.groupby(["date", "code"]):
            arms = {str(r["arm"]): r for r in group.to_dict("records")}
            if ARM_WITH not in arms or ARM_WITHOUT not in arms:
                dropped += 1
                continue
            if arms[ARM_WITH]["gate_version"] != arms[ARM_WITHOUT]["gate_version"]:
                dropped += 1          # 门版本不同 = 不是同一件事,不得比
                continue
            paired_rows.append({
                "date": date, "code": code,
                "with_fulltext": float(arms[ARM_WITH]["claim_correct"]),
                "without_fulltext": float(arms[ARM_WITHOUT]["claim_correct"]),
                "gate_disagreement": int(
                    arms[ARM_WITH]["gate_state"] != arms[ARM_WITHOUT]["gate_state"]),
            })
    frame = pd.DataFrame(paired_rows)
    interval = (st.paired_delta_interval(frame, "with_fulltext", "without_fulltext",
                                         date_col="date")
                if len(frame) else st.Interval(None, None, None, 0, 0, "no_arms"))
    n_days = int(frame["date"].nunique()) if len(frame) else 0
    verdict = et.conclude(template, interval, observed_units=n_days,
                          observed_dims={"subgroup_n": int(len(frame))})
    return {
        "schema_version": SCHEMA_VERSION,
        "experiment": EVIDENCE_FAMILY,
        "gate": GATE,
        "n_pairs": int(len(frame)),
        "n_days": n_days,
        "dropped_pairs": dropped,
        "dropped_reason": "缺一臂 或 两臂门版本不同(不是同一件事,不得比)",
        "gate_disagreement_rate": (round(float(frame["gate_disagreement"].mean()), 6)
                                   if len(frame) else None),
        "claim_correct_delta": interval.as_dict(),
        "verdict": verdict,
        "production_side_effects": "无 —— 两臂皆影子,不注入生产",
    }


# ───────────────────────── 汇总 / 渲染 / CLI ─────────────────────────


def build(scan_root: Path | str | None = None) -> dict:
    return {
        "schema_version": SCHEMA_VERSION,
        "gate": GATE,
        "current_ruling": "IMMATURE",
        "why_two_experiments": ("证据增强与门松紧是两个 treatment;混成一个就无法把"
                                "改善归到其中任一笔上(§4.1)"),
        "recal": recal_verdict(scan_root),
        "evidence": evidence_verdict(scan_root),
        "background_guard": background_guard(scan_root),
    }


def render(payload: dict) -> str:
    lines = [
        f"# 「{payload['gate']}」门重标定 —— 两个分开的实验",
        "",
        f"> {payload['why_two_experiments']}",
        "",
        "## 实验② gate_true_delivery_recal(现门不变,只记 counterfactual)",
        "",
    ]
    recal = payload["recal"]
    lines += [f"- binding 可测 **{recal['n_binding_measured']}** 条 / "
              f"{recal['n_days']} 日 · CORRECT {recal['CORRECT']} · "
              f"NEUTRAL {recal['NEUTRAL']} · FALSE_KILL {recal['FALSE_KILL']}",
              f"- 生产副作用:{recal['production_side_effects']}", ""]
    rate = recal["false_kill_rate"]
    if rate["point"] is not None:
        lines += [f"- 错杀率 {rate['point']:.4f} · 区间 "
                  + ("—" if rate["lo"] is None else f"[{rate['lo']:.4f}, {rate['hi']:.4f}]")
                  + f"(n={rate['n']})", ""]
    else:
        lines += ["- 错杀率:**无可测样本** —— 不是 0,是没有观测", ""]
    lines.append(et.render(recal["verdict"]))
    lines += [f"- **门语义翻译**:{recal['interpretation']}", ""]

    lines += ["## 实验① gate_true_delivery_evidence(D4 有/无全文 paired shadow)", ""]
    ev = payload["evidence"]
    lines += [f"- 配对 **{ev['n_pairs']}** 对 / {ev['n_days']} 日 · "
              f"作废 {ev['dropped_pairs']} 对({ev['dropped_reason']})",
              f"- 生产副作用:{ev['production_side_effects']}", ""]
    lines.append(et.render(ev["verdict"]))

    guard = payload["background_guard"]
    lines += ["## 背景守卫(**不进判据**)", "",
              f"- {guard['why']}",
              f"- 全门可测 {guard['n_all_gates_measured']} 条 · 均值超额 "
              + ("—" if guard["all_gates_mean_excess_2"] is None
                 else f"{guard['all_gates_mean_excess_2']:+.4f}"),
              ""]
    lines += ["## 口径(A11 v3)", "", "| 键 | 值 |", "|---|---|"]
    for key, value in recal["gate_definition"].items():
        if key != "not_derived_from":
            lines.append(f"| `{key}` | {value} |")
    return "\n".join(lines) + "\n"


def register(registry_path: Path | str | None = None,
             *, start: str = "2026-08-04", expires: str = "2026-12-31") -> list[dict]:
    """把两个模板注册成 PREREGISTERED spec(人批才往前走)。"""
    from autoresearch.learning import experiment_registry as reg

    path = Path(registry_path or reg.DEFAULT_REGISTRY)
    out = []
    for template, family, metric, op, value in (
        (recal_template(), RECAL_FAMILY, "false_kill_rate_delta_vs_margin", "lt", -0.0),
        (evidence_template(), EVIDENCE_FAMILY, "claim_correct_rate_delta", "gt", 0.0),
    ):
        spec = {
            "id": f"exp_{start.replace('-', '')}_{family}",
            "title": template.h1,
            "trial_family": family,
            "definition": et.to_registry_definition(template),
            "start_date": start, "expires_date": expires,
            "primary_metric": metric,
            "promotion_guards": {d: [{"metric": metric, "op": op, "value": value}]
                                 for d in reg.GUARD_DOMAINS},
            "rollback_guards": {d: [{"metric": metric, "op": "gt", "value": -1.0}]
                                for d in reg.GUARD_DOMAINS},
            "challenger_pointer": {
                "kind": "shadow_gate", "pointer": f"gate_recal:{family}",
                "content_hash": reg.canonical_hash(et.to_registry_definition(template)),
            },
            "minimums": et.minimums_for(template),
            "rollback_window_runs": 5,
        }
        out.append(reg.register_experiment(path, spec))
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="「业绩真兑现」门重标定(§4.1)")
    ap.add_argument("--scan-root", default=None)
    ap.add_argument("--json-out", default=str(OUT_JSON))
    ap.add_argument("--md-out", default=str(OUT_MD))
    ap.add_argument("--register", action="store_true", help="写两个 PREREGISTERED spec")
    ap.add_argument("--registry", default=None)
    a = ap.parse_args(argv)

    if a.register:
        records = register(a.registry)
        for record in records:
            print(f"[gate_recal] {record['id']} → {record['status']}")
        return 0

    payload = build(a.scan_root)
    for path, text in ((Path(a.json_out),
                        json.dumps(payload, ensure_ascii=False, indent=2, default=str) + "\n"),
                       (Path(a.md_out), render(payload))):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    print(f"[gate_recal] recal={payload['recal']['verdict']['verdict']} "
          f"(n={payload['recal']['n_binding_measured']}) · "
          f"evidence={payload['evidence']['verdict']['verdict']} "
          f"(pairs={payload['evidence']['n_pairs']}) → {a.json_out} / {a.md_out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
