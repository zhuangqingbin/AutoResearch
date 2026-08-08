#!/usr/bin/env python3
"""门归因 v3 —— 被绑定门拦下的每一票,到底拦对了还是漏了肉(确定性,零 LLM)。

design: docs/specs/2026-08-01-wave10-report-ops-slimdown-zerobuy-design.md §A11

`gate_ledger` 回答"这道门平均拦得怎么样"(mean/左尾);本模块回答**逐票四态**:

  CORRECT     excess2 < 0          拦对了(被拦票跑输市场)
  NEUTRAL     0 ≤ excess2 < +2pp   经济上无差别
  FALSE_KILL  excess2 ≥ +2pp       漏肉——设计稿把「错杀」的定义收在这一处,唯一
  UNMEASURED  缺 T+2 / 不可交易 / 门状态不可判

两个 cohort **不是同一序列**,`cohort_version` 是每行必填字段,不得互相冒充(§R5):

  (两者的"T+2 收益"都读**主尺** `ruler.MAIN_RULER`(现 `gap_c1_o2`);落盘列名 `fwd_2_oc`/
   `excess_2` 沿自旧尺年代**固定不漂移**,每行另带 `ruler` 列记写入那一刻的真值 —— 跨尺行
   禁止揉进同一个均值,见本文件 `_day_facts` 与 STAGES.md「账本定义断层」。)

  legacy_gate_ledger  市场基准 = 全表主尺 **均值**;**不去重**;不看可交易性。
                      只为迁移复现 —— 主力门 36/7/20(总 63)是它的读数,不是 v3 的。
  v3                  市场基准 = 可交易且成熟票的主尺 **中位**(与
                      `rejection_attribution` / abstention v2 / C3 同源);同日同票踩
                      ≥2 道门 → `MULTI_GATE` 单列,**不重复算进三个单门分母**;
                      不可交易 / 未成熟 / 门状态不可判 → `UNMEASURED`。

去重不是形式功夫:07-31 全量实测 legacy 路 65 个 (日,码) 里有 47 个同时踩 ≥2 道门,
「主力门拦了 63 次」里绝大多数其实是多门共同拦的,单门分母被系统性放大。

EXP-1 与 C3 只读 v3。

  uv run --no-sync python -m autoresearch.learning.gate_attribution
  uv run --no-sync python -m autoresearch.learning.gate_attribution --check-legacy
"""
from __future__ import annotations

from pathlib import Path

import pandas as pd

from autoresearch.common.ruler import MAIN_RULER, entry_tradable

COHORT_LEGACY = "legacy_gate_ledger"
COHORT_V3 = "v3"
COHORTS = (COHORT_LEGACY, COHORT_V3)

OUTCOMES = ("CORRECT", "NEUTRAL", "FALSE_KILL", "UNMEASURED")
FALSE_KILL_MIN_EXCESS = 0.02          # +2pp —— 设计稿 §A11 的唯一「错杀」定义

BINDING_PREFIX = "OW三门·"            # `level == "binding"` ⟺ check 以此开头(247 行实测)
SINGLE_GATES = ("主力真在", "业绩真兑现", "估值不透支")
MULTI_GATE = "MULTI_GATE"
UNDECIDABLE_GATE = "UNDECIDABLE"

_MARKET_BASELINE = {
    COHORT_LEGACY: "mean_all_rows",
    COHORT_V3: "median_tradable_mature",
}
_COLUMNS = [
    "cohort_version", "date", "gate", "code", "tradable", "mature",
    "fwd_2_oc", "market_fwd_2", "market_baseline", "excess_2",  # fwd_2_oc:参考尺,固定列名,勿随主尺漂移(持久化 ledger 列)
    "outcome", "outcome_reason", "source", "ruler",   # ruler:fwd_2_oc/excess_2 取值来自哪个 MAIN_RULER(写入那一刻的真值)
]
_PARTICIPATION_COLUMNS = [
    "cohort_version", "date", "gate", "code", "n_gates_failed", "sole_killer",
    "tradable", "mature", "fwd_2_oc", "market_fwd_2", "market_baseline",  # fwd_2_oc:参考尺,固定列名,勿随主尺漂移
    "excess_2", "outcome", "outcome_reason", "source", "ruler",
]
_SUMMARY_COLUMNS = [
    "cohort_version", "gate", "ruler", "n_days", "n_fires",
    "CORRECT", "NEUTRAL", "FALSE_KILL", "UNMEASURED",
    "measured_n", "false_kill_rate", "correct_rate", "mean_excess_2",
]
# ruler 分段是 summarize() 分组键的一部分(见下)——T16 flip 前后的行落进同一 (cohort,gate)
# 时,mean_excess_2 绝不能把 fwd_2_oc 尺与 gap_c1_o2 尺的 excess_2 揉进一个均值里算;
# 历史行(本列上线前写的)缺 ruler → 读侧 `row.get("ruler", "fwd_2_oc")` 兜底,见 `_day_facts`。


def classify_outcome(
    excess_2: float | None,
    *,
    tradable: bool,
    mature: bool,
    gate_state_known: bool = True,
) -> tuple[str, str]:
    """逐票四态 + 理由码。纯函数 —— 四态的唯一定义点,别在渲染层再判一次。

    顺序有意义:门状态不可判时,这一拦**归不到任何一道门头上**,所以既不能算它拦对、
    也不能算它错杀(设计稿 §A11 的"事实坏"→ UNMEASURED)。
    """
    if not gate_state_known:
        return "UNMEASURED", "gate_state_unknown"
    if not tradable:
        return "UNMEASURED", "not_tradable"
    if not mature:
        return "UNMEASURED", "t2_immature"
    if excess_2 is None or pd.isna(excess_2):
        return "UNMEASURED", "no_market_baseline"
    if float(excess_2) >= FALSE_KILL_MIN_EXCESS:
        return "FALSE_KILL", "excess2_ge_2pp"
    if float(excess_2) >= 0:
        return "NEUTRAL", "inside_economic_band"
    return "CORRECT", "underperformed_market"


def _bool_series(frame: pd.DataFrame, name: str, default: bool) -> pd.Series:
    if name not in frame.columns:
        return pd.Series(default, index=frame.index, dtype=bool)
    values = frame[name]
    if values.dtype == bool:
        return values.fillna(default)
    return values.astype(str).str.strip().str.lower().isin(
        {"true", "1", "yes", "是"}
    )


def binding_fires(scan_dir: Path | str) -> pd.DataFrame:
    """该日被绑定门的拦截行 → `[date, gate, code, source]`(gate 已剥 `OW三门·` 前缀)。

    取数规则与 `gate_ledger.roll` 一致:有 `decision_records.json` → 结构化桶**取代**
    CSV 的 `OW三门·*` 行;否则回退 CSV。`test_gate_attribution` 与 `--check-legacy`
    两处对账锁住这个一致性 —— 两边取数一旦分叉,legacy 复现立刻对不上。

    产物形状/卡片契约等 lint 行 `level` 为空,不是决策门,不进本账。
    """
    from autoresearch.learning.rejection_attribution import decision_gate_bucket
    from autoresearch.scan.decision_read_model import read_decisions

    day = Path(scan_dir)
    frames: list[pd.DataFrame] = []
    has_structured = (day / "decision_records.json").exists()

    csv_path = day / "gate_fires.csv"
    if csv_path.exists() and not has_structured:
        try:
            fires = pd.read_csv(csv_path, dtype={"code": str})
        except Exception:  # noqa: BLE001 — 读不动就是没有,不臆测
            fires = pd.DataFrame()
        if len(fires) and {"check", "code"} <= set(fires.columns):
            fires = fires[
                fires["check"].astype(str).str.startswith(BINDING_PREFIX)
            ].copy()
            if len(fires):
                frames.append(pd.DataFrame({
                    "date": day.name,
                    "gate": fires["check"].astype(str).str[len(BINDING_PREFIX):],
                    "code": fires["code"].astype(str).str.zfill(6),
                    "source": "gate_fires_csv",
                }))

    if has_structured:
        rows = [
            {"date": day.name, "gate": bucket,
             "code": str(code).strip().split(".")[0].zfill(6),
             "source": "decision_records"}
            for code, decision in read_decisions(day).items()
            if (bucket := decision_gate_bucket(decision)) is not None
        ]
        if rows:
            frames.append(pd.DataFrame(rows))

    if not frames:
        return pd.DataFrame(columns=["date", "gate", "code", "source"])
    out = pd.concat(frames, ignore_index=True)
    return out[out["code"].astype(str).str.len() > 0].reset_index(drop=True)


def gate_participation(scan_dir: Path | str) -> pd.DataFrame:
    """该日**每道门各自的 FAIL 名单** → `[date, gate, code, n_gates_failed, source]`。

    与 `binding_fires` 是两个问题,不要混用:

      `binding_fires` → 这一拦**归谁的账**(多门共拦 → `MULTI_GATE`,不重复进单门分母)
      `gate_participation` → 这道门**评过并否掉了谁**(多门共拦时三道门各记一次)

    错杀率必须走前者(否则同一票被三道门各算一次分母);EXP-1 这类"换掉某道门口径"的
    影子实验必须走后者 —— 它要考核的人口是"主力门参与否决过的票",而不是"只有主力门
    否决的票"。07-31 实测:后者只有 17 只,按前者定义样本永远攒不到 EXP-1 的 50 门槛。
    """
    from autoresearch.scan.decision_read_model import read_decisions

    day = Path(scan_dir)
    has_structured = (day / "decision_records.json").exists()
    rows: list[dict] = []

    if has_structured:
        for code, decision in read_decisions(day).items():
            states = {
                gate: decision.gate_states.get(gate, "UNKNOWN")
                for gate in SINGLE_GATES
            }
            failed = [gate for gate, state in states.items() if state == "FAIL"]
            for gate in failed:
                rows.append({
                    "date": day.name, "gate": gate,
                    "code": str(code).strip().split(".")[0].zfill(6),
                    "n_gates_failed": len(failed),
                    "source": "decision_records",
                })
    else:
        csv_path = day / "gate_fires.csv"
        if not csv_path.exists():
            return pd.DataFrame(
                columns=["date", "gate", "code", "n_gates_failed", "source"])
        try:
            fires = pd.read_csv(csv_path, dtype={"code": str})
        except Exception:  # noqa: BLE001
            return pd.DataFrame(
                columns=["date", "gate", "code", "n_gates_failed", "source"])
        if not len(fires) or not {"check", "code"} <= set(fires.columns):
            return pd.DataFrame(
                columns=["date", "gate", "code", "n_gates_failed", "source"])
        fires = fires[
            fires["check"].astype(str).str.startswith(BINDING_PREFIX)
        ].copy()
        fires["gate"] = fires["check"].astype(str).str[len(BINDING_PREFIX):]
        fires["code"] = fires["code"].astype(str).str.zfill(6)
        fires = fires[fires["gate"].isin(SINGLE_GATES)].drop_duplicates(
            subset=["gate", "code"])
        per_code = fires.groupby("code")["gate"].nunique().to_dict()
        rows = [{
            "date": day.name, "gate": r.gate, "code": r.code,
            "n_gates_failed": int(per_code.get(r.code, 1)),
            "source": "gate_fires_csv",
        } for r in fires.itertuples(index=False)]

    return pd.DataFrame(
        rows, columns=["date", "gate", "code", "n_gates_failed", "source"])


def normalize_gate(gate: str) -> str:
    """结构化桶的中文标签 → 稳定的机器标签(单门名保留中文,与门本身同名)。

    门标签的**唯一**转换点:`gate_ledger` 的中文桶名与本模块的机器标签必须映射到同一个
    metric_id,否则同一道门会在证据清单里以两个名字各活一份。
    """
    if gate == "多门":
        return MULTI_GATE
    if gate == "不可判":
        return UNDECIDABLE_GATE
    return gate


def _collapse_multi_gate(fires: pd.DataFrame) -> pd.DataFrame:
    """v3 去重:同日同票踩 ≥2 道单门 → 一行 `MULTI_GATE`,不重复进单门分母。"""
    if not len(fires):
        return fires
    singles = fires[fires["gate"].isin(SINGLE_GATES)]
    multi_codes = {
        code for code, group in singles.groupby("code")
        if group["gate"].nunique() >= 2
    }
    kept = fires[~(fires["gate"].isin(SINGLE_GATES) & fires["code"].isin(multi_codes))]
    collapsed = (
        singles[singles["code"].isin(multi_codes)]
        .drop_duplicates(subset=["code"])
        .assign(gate=MULTI_GATE)
    )
    out = pd.concat([kept, collapsed], ignore_index=True)
    # 同一门内同票多行(CSV 历史格式允许)在 v3 里也只算一次
    return out.drop_duplicates(subset=["gate", "code"]).reset_index(drop=True)


def _day_facts(
    scan_dir: Path,
    cohort: str,
) -> tuple[pd.DataFrame, float | None] | None:
    """`retro/attribution.csv` → (逐票事实表, 当日市场基准)。缺表 → None(尚未成熟,不臆造)。"""
    attr_path = scan_dir / "retro" / "attribution.csv"
    if not attr_path.exists():
        return None
    try:
        attr = pd.read_csv(attr_path, dtype={"code": str})
    except Exception:  # noqa: BLE001
        return None
    if not len(attr) or "code" not in attr.columns:
        return None

    attr["code"] = attr["code"].astype(str).str.zfill(6)
    # T16 硬化:老 scan 日(早于该主尺列存在)可能整列缺失 —— `attr.get(MAIN_RULER)` 缺列时
    # 返回 None(标量),`pd.to_numeric(None)` 会退化成标量 NaN 而非 Series,下游 `.notna()`
    # 直接崩(AttributeError)。缺列应像"整列 NaN"一样静默降级,不是让整条 roll() 断链。
    fwd2 = pd.to_numeric(attr[MAIN_RULER], errors="coerce") if MAIN_RULER in attr.columns \
        else pd.Series(float("nan"), index=attr.index)
    # C1 修复(final-review 2026-08-08):入场旗跟随 MAIN_RULER 选(entry_tradable 单点),
    # 不是硬编码 `_bool_series(attr, "buyable", True)`——那条换尺后仍读 D+1 开盘旗。
    tradable = _bool_series(attr, "tradable", True) & entry_tradable(attr)

    if cohort == COHORT_LEGACY:
        # gate_ledger 的基准:全表均值,不过滤可交易性 —— 复现用,别拿去做研究。
        market = float(fwd2.mean()) if fwd2.notna().any() else None
    else:
        eligible = tradable & fwd2.notna()
        market = float(fwd2[eligible].median()) if eligible.any() else None

    # 内部定长字段名(下方 `_fact_row` 用 `.fwd_2_oc` 属性访问取值,属性名不能动态化)——
    # 固定叫 fwd_2_oc,不随 MAIN_RULER 漂移;取值仍来自上面已按主尺读的 fwd2。
    facts = pd.DataFrame({
        "code": attr["code"],
        "fwd_2_oc": fwd2,
        "tradable": tradable,
    }).drop_duplicates(subset=["code"]).set_index("code")
    return facts, market


def _fact_row(facts: pd.DataFrame, code: str) -> tuple[float | None, bool]:
    if code not in facts.index:
        return None, True
    fact = facts.loc[code]
    fwd = None if pd.isna(fact.fwd_2_oc) else float(fact.fwd_2_oc)
    return fwd, bool(fact.tradable)


def build_day(
    scan_dir: Path | str,
    *,
    cohort: str = COHORT_V3,
) -> pd.DataFrame:
    """单日逐票归因行(**归谁的账**口径;多门共拦 → `MULTI_GATE`)。"""
    if cohort not in COHORTS:
        raise ValueError(f"unknown cohort_version: {cohort!r}")
    day = Path(scan_dir)
    resolved = _day_facts(day, cohort)
    if resolved is None:
        return pd.DataFrame(columns=_COLUMNS)
    facts, market = resolved

    fires = binding_fires(day)
    if not len(fires):
        return pd.DataFrame(columns=_COLUMNS)
    fires = fires.assign(gate=fires["gate"].map(normalize_gate))
    if cohort == COHORT_V3:
        fires = _collapse_multi_gate(fires)

    rows = []
    for fire in fires.itertuples(index=False):
        fwd, is_tradable = _fact_row(facts, fire.code)
        excess = None if (fwd is None or market is None) else fwd - market
        if cohort == COHORT_LEGACY:
            # legacy 只问"跑赢没有",不看可交易性/门状态 —— 保真复现,不悄悄改口径
            outcome, reason = classify_outcome(
                excess, tradable=True, mature=fwd is not None)
        else:
            outcome, reason = classify_outcome(
                excess,
                tradable=is_tradable,
                mature=fwd is not None,
                gate_state_known=fire.gate != UNDECIDABLE_GATE,
            )
        rows.append({
            "cohort_version": cohort,
            "date": fire.date,
            "gate": fire.gate,
            "code": fire.code,
            "tradable": is_tradable,
            "mature": fwd is not None,
            "fwd_2_oc": fwd,
            "market_fwd_2": market,
            "market_baseline": _MARKET_BASELINE[cohort],
            "excess_2": excess,
            "outcome": outcome,
            "outcome_reason": reason,
            "source": fire.source,
            "ruler": MAIN_RULER,
        })
    return pd.DataFrame(rows, columns=_COLUMNS)


def roll(
    scan_root: Path | str | None = None,
    *,
    cohort: str = COHORT_V3,
) -> pd.DataFrame:
    """跨日逐票归因长表。"""
    root = Path(scan_root or "context/scan")
    if not root.exists():
        return pd.DataFrame(columns=_COLUMNS)
    days = sorted(p for p in root.iterdir() if p.is_dir())
    frames = [f for day in days if len(f := build_day(day, cohort=cohort))]
    if not frames:
        return pd.DataFrame(columns=_COLUMNS)
    return pd.concat(frames, ignore_index=True).sort_values(
        ["date", "gate", "code"]
    ).reset_index(drop=True)


def build_participation_day(scan_dir: Path | str) -> pd.DataFrame:
    """单日**每道门 FAIL 名单**的逐票归因(EXP-1 这类换口径实验的人口口径,始终 v3 基准)。"""
    day = Path(scan_dir)
    resolved = _day_facts(day, COHORT_V3)
    if resolved is None:
        return pd.DataFrame(columns=_PARTICIPATION_COLUMNS)
    facts, market = resolved
    fires = gate_participation(day)
    if not len(fires):
        return pd.DataFrame(columns=_PARTICIPATION_COLUMNS)

    rows = []
    for fire in fires.itertuples(index=False):
        fwd, is_tradable = _fact_row(facts, fire.code)
        excess = None if (fwd is None or market is None) else fwd - market
        outcome, reason = classify_outcome(
            excess, tradable=is_tradable, mature=fwd is not None)
        rows.append({
            "cohort_version": COHORT_V3,
            "date": fire.date,
            "gate": fire.gate,
            "code": fire.code,
            "n_gates_failed": int(fire.n_gates_failed),
            "sole_killer": int(fire.n_gates_failed) == 1,
            "tradable": is_tradable,
            "mature": fwd is not None,
            "fwd_2_oc": fwd,
            "market_fwd_2": market,
            "market_baseline": _MARKET_BASELINE[COHORT_V3],
            "excess_2": excess,
            "outcome": outcome,
            "outcome_reason": reason,
            "source": fire.source,
            "ruler": MAIN_RULER,
        })
    return pd.DataFrame(rows, columns=_PARTICIPATION_COLUMNS)


def roll_participation(scan_root: Path | str | None = None) -> pd.DataFrame:
    """跨日 participation 长表(每道门各自的 FAIL 名单)。"""
    root = Path(scan_root or "context/scan")
    if not root.exists():
        return pd.DataFrame(columns=_PARTICIPATION_COLUMNS)
    days = sorted(p for p in root.iterdir() if p.is_dir())
    frames = [f for day in days if len(f := build_participation_day(day))]
    if not frames:
        return pd.DataFrame(columns=_PARTICIPATION_COLUMNS)
    return pd.concat(frames, ignore_index=True).sort_values(
        ["date", "gate", "code"]
    ).reset_index(drop=True)


def summarize(rows: pd.DataFrame) -> pd.DataFrame:
    """长表 → 每门 outcome 分布。比率分母是 `measured_n`(= 非 UNMEASURED),不是 `n_fires`。

    分组键含 `ruler`(不只 cohort_version/gate)—— T16 flip 前后的拦截行落进同一
    (cohort, gate) 时,`mean_excess_2` 绝不能把 fwd_2_oc 尺与 gap_c1_o2 尺的 excess_2
    揉进一个均值算,那是算混合物。今天单一 MAIN_RULER 常量下每组自然仍是一行(不改变
    现有单尺场景的行为),ruler 分裂只在真正跨尺时才显现出多行。
    """
    if rows is None or not len(rows):
        return pd.DataFrame(columns=_SUMMARY_COLUMNS)
    rows = rows.copy()
    if "ruler" in rows.columns:
        rows["ruler"] = rows["ruler"].fillna("fwd_2_oc")
    else:
        rows["ruler"] = "fwd_2_oc"
    out = []
    for (cohort, gate, ruler), group in rows.groupby(
        ["cohort_version", "gate", "ruler"], sort=False
    ):
        counts = group["outcome"].value_counts().to_dict()
        measured = sum(counts.get(o, 0) for o in OUTCOMES if o != "UNMEASURED")
        excess = pd.to_numeric(group["excess_2"], errors="coerce").dropna()
        out.append({
            "cohort_version": cohort,
            "gate": gate,
            "ruler": ruler,
            "n_days": int(group["date"].nunique()),
            "n_fires": int(len(group)),
            **{o: int(counts.get(o, 0)) for o in OUTCOMES},
            "measured_n": measured,
            "false_kill_rate": (
                round(counts.get("FALSE_KILL", 0) / measured, 4) if measured else None
            ),
            "correct_rate": (
                round(counts.get("CORRECT", 0) / measured, 4) if measured else None
            ),
            "mean_excess_2": round(float(excess.mean()), 6) if len(excess) else None,
        })
    frame = pd.DataFrame(out, columns=_SUMMARY_COLUMNS)
    return frame.sort_values(
        ["cohort_version", "n_fires"], ascending=[True, False]
    ).reset_index(drop=True)


def _summary_table(summary: pd.DataFrame, *, rates: bool = True) -> list[str]:
    """`rates=False` 用于 participation 表 —— 那张表的分母重复计数,**结构上**不给出比率列,
    而不是印出来再用一句话叮嘱别看(说明文字管不住读表的人)。"""
    def pct(x):
        return "—" if x is None or pd.isna(x) else f"{x:.1%}"

    def sig(x):
        return "—" if x is None or pd.isna(x) else f"{x * 100:+.2f}%"

    rate_head = " 错杀率 | 拦对率 |" if rates else ""
    rate_sep = "---|---|" if rates else ""
    lines = [
        "| 门 | 尺 | 天数 | 拦次 | CORRECT | NEUTRAL | FALSE_KILL | UNMEASURED | "
        f"可测分母 |{rate_head} 被拦ex2均值 |",
        f"|---|---|---:|---:|---:|---:|---:|---:|---:|{rate_sep}---|",
    ]
    for r in summary.itertuples(index=False):
        rate_cells = (
            f" {pct(r.false_kill_rate)} | {pct(r.correct_rate)} |" if rates else ""
        )
        lines.append(
            f"| {r.gate} | {getattr(r, 'ruler', 'fwd_2_oc')} | {r.n_days} | {r.n_fires} | {r.CORRECT} | {r.NEUTRAL} "
            f"| {r.FALSE_KILL} | {r.UNMEASURED} | {r.measured_n} |"
            f"{rate_cells} {sig(r.mean_excess_2)} |"
        )
    return lines


def render_migration(
    legacy: pd.DataFrame,
    v3: pd.DataFrame,
    participation: pd.DataFrame | None = None,
) -> list[str]:
    """迁移报告:legacy 与 v3 并排,但**显式标注两者不是同一序列**(§A11 验收)。"""
    lines = [
        "# 门归因 v3 · 迁移报告(CORRECT / NEUTRAL / FALSE_KILL / UNMEASURED)",
        "",
        "> ⚠️ 下面**两张表是两个 cohort,不是同一序列的前后两版**。",
        "> 比较单个数字的大小没有意义 —— 它们的市场基准、去重规则、可交易性过滤都不同。",
        "",
        f"- `FALSE_KILL` 的唯一定义:`excess_2 ≥ +{FALSE_KILL_MIN_EXCESS:.0%}`。",
        "- `gate_ledger` 的「拦对率(左尾≤-5%)」量的是**被拦票的左尾保护率**,"
        "与错杀率不是同一个量,不得互相翻译。",
        "",
        "## legacy cohort(仅供迁移复现)",
        "",
        f"市场基准 = 全表 `{MAIN_RULER}` 均值 · **不去重**(同票踩多门会重复进各单门分母)"
        " · 不过滤可交易性。",
        "",
    ]
    if legacy is None or not len(legacy):
        lines.append("_无 legacy 数据_")
    else:
        lines += _summary_table(legacy)
    lines += [
        "",
        "## v3 cohort(EXP-1 与 C3 唯一读的契约)",
        "",
        f"市场基准 = 可交易且成熟票的 `{MAIN_RULER}` **中位**(与 `rejection_attribution` /"
        " abstention v2 同源) · 同日同票踩 ≥2 道门 → `MULTI_GATE` 单列 ·"
        " 不可交易/未成熟/门状态不可判 → `UNMEASURED`。",
        "",
    ]
    if v3 is None or not len(v3):
        lines.append("_无 v3 数据_")
    else:
        lines += _summary_table(v3)
    lines += [
        "",
        "_单门分母在 v3 里显著小于 legacy 是**预期结果**,不是数据丢失:"
        "同票踩多门的拦截被移出单门,归入 `MULTI_GATE`。_",
    ]
    if participation is not None and len(participation):
        lines += [
            "",
            "## v3 participation(每道门各自的 FAIL 名单 —— EXP-1 的人口)",
            "",
            "上表回答「这一拦归谁的账」(错杀率的正确分母);本表回答「这道门评过并否掉了谁」。"
            "多门共拦时三道门**各记一次**,分母重复计数,因此本表**不提供比率列** ——"
            "它只用于给「换掉某道门口径」的影子实验一个足够大的人口。",
            "",
        ]
        lines += _summary_table(participation, rates=False)
    return lines


def check_against_gate_ledger(
    scan_root: Path | str | None = None,
) -> tuple[bool, list[str]]:
    """legacy cohort 对账 `gate_ledger.roll()`:拦次与拦对数必须逐门相等。

    这是防"两处各写一套取数"的探针 —— 任何一边改了取数规则,这里立刻变红。
    """
    from autoresearch.learning.gate_ledger import roll as gate_roll

    legacy = roll(scan_root, cohort=COHORT_LEGACY)
    summary = summarize(legacy).set_index("gate")
    ledger = gate_roll(scan_root)
    notes, ok = [], True
    binding = ledger[ledger["check"].astype(str).str.startswith(BINDING_PREFIX)]
    for row in binding.itertuples(index=False):
        gate = normalize_gate(row.check[len(BINDING_PREFIX):])
        if gate not in summary.index:
            ok = False
            notes.append(f"✗ {gate}: gate_ledger 有 {int(row.n_fires)} 次,本模块无该门")
            continue
        mine = summary.loc[gate]
        want_correct = (
            None if pd.isna(row.hit_rate) else round(float(row.hit_rate) * int(row.n_fires))
        )
        fires_ok = int(mine.n_fires) == int(row.n_fires)
        correct_ok = want_correct is None or int(mine.CORRECT) == want_correct
        ok = ok and fires_ok and correct_ok
        notes.append(
            f"{'✓' if fires_ok and correct_ok else '✗'} {gate}: "
            f"拦次 {int(mine.n_fires)} vs {int(row.n_fires)} · "
            f"CORRECT {int(mine.CORRECT)} vs {want_correct}"
        )
    if not notes:
        notes.append("_gate_ledger 无被绑定门数据_")
    return ok, notes


def main(argv: list[str] | None = None) -> int:
    import argparse

    ap = argparse.ArgumentParser(description="门归因 v3(四态,确定性)")
    ap.add_argument("--scan-root", default=None)
    ap.add_argument("--check-legacy", action="store_true",
                    help="只对账 gate_ledger,不写产物;不一致时退出码 1")
    args = ap.parse_args(argv)

    if args.check_legacy:
        ok, notes = check_against_gate_ledger(args.scan_root)
        print("═══ legacy cohort 对账 gate_ledger ═══")
        for note in notes:
            print(f"  {note}")
        print(f"  —— {'一致' if ok else '⚠️ 不一致(取数规则已分叉)'}")
        return 0 if ok else 1

    legacy = summarize(roll(args.scan_root, cohort=COHORT_LEGACY))
    v3_rows = roll(args.scan_root, cohort=COHORT_V3)
    part_rows = roll_participation(args.scan_root)
    report = Path("reports/learning/gate_attribution.md")
    report.parent.mkdir(parents=True, exist_ok=True)
    report.write_text(
        "\n".join(render_migration(
            legacy, summarize(v3_rows), summarize(part_rows))) + "\n",
        encoding="utf-8",
    )
    out_dir = Path("context/learning")
    out_dir.mkdir(parents=True, exist_ok=True)
    v3_rows.to_csv(out_dir / "gate_attribution_v3.csv", index=False)
    part_rows.to_csv(out_dir / "gate_participation_v3.csv", index=False)
    print(f"[gate_attribution] legacy {int(legacy['n_fires'].sum()) if len(legacy) else 0} 次 · "
          f"v3 归因 {len(v3_rows)} 次 · v3 participation {len(part_rows)} 次 → {report}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
