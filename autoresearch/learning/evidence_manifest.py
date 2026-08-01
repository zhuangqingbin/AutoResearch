#!/usr/bin/env python3
"""证据清单 —— 每个被引用的数字都带着它的分子、分母、cohort 和来源指纹(确定性,零 LLM)。

design: docs/specs/2026-08-01-wave10-report-ops-slimdown-zerobuy-design.md §A0 / §1.3

治的病有三个,都不是"算错了",而是**引用时走样**:

1. **手抄分母** —— 设计稿/报告里写"30 日 23 个 0买日",而收益账本的分母其实是 26 日。
   两个数各自都对,拼在一句话里就错了。本模块让每个比例强制带 `denominator_id`,
   而 `denominator_id` 只登记一次、只属于一个 cohort:跨 cohort 拼句在**写入时**就失败。
2. **语义误读** —— gate_ledger 的 39% 是「被拦票左尾(≤-5%)保护率」,被读成「错杀率」。
   本模块给每个指标一个受控 `semantic`,而每个 `semantic` 钉死它允许的来源字段;
   把 `tail_rate` 挂到 `false_kill_rate` 上会直接抛错(见 `test_evidence_manifest`)。
3. **过期快照** —— 数字没有 as-of,读的人不知道它截到哪天。本模块每条都写 `as_of`
   (数据覆盖到的最后一天,不是跑这条命令的墙钟)与 `source_hashes`。

**边界**:本模块**不重算底层收益**,只做既有账本的 cohort 对齐与来源指纹。源之间冲突
→ 该指标 `status=CONFLICT` 并拒绝进入结论句,但**不阻断日常 scan**(证据出问题不该
让扫描停摆,该让引用它的句子闭嘴)。

  uv run --no-sync python -m autoresearch.learning.evidence_manifest
  uv run --no-sync python -m autoresearch.learning.evidence_manifest --day 2026-07-31
  uv run --no-sync python -m autoresearch.learning.evidence_manifest --check docs/research/...json
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field
from pathlib import Path

import pandas as pd

SCHEMA_VERSION = 1
QUERY_VERSION = "wave10.a0.1"

# ── cohort:分母的命名空间。同一个数字落在不同 cohort 里就是不同的事实 ──────────
COHORTS: dict[str, str] = {
    "raw_run": "journal 的每个 scan 日行 —— 含非交易日/缺卡/未成熟,"
               "**不得直接作收益或 0买比例的分母**",
    "valid_completed": "跑完且有 finalists 与决策卡的扫描日",
    "t2_mature": "retro/attribution 已回填 fwd_2_oc 的扫描日",
    "experiment_eligible": "v3 门归因里 outcome≠UNMEASURED 的可交易成熟候选",
    "legacy_migration": "仅供迁移复现的旧口径(gate_ledger 全表均值·不去重)——"
                        "**不是研究 cohort**,不得与上面四个并列比较",
}


@dataclass(frozen=True)
class Semantic:
    """一个受控语义 + 它唯一允许的来源字段。"""
    definition: str
    source_field: str
    cohorts: tuple[str, ...]


# 语义 → 来源字段的绑定表。改这里等于改事实定义,必须过 review。
SEMANTICS: dict[str, Semantic] = {
    "scan_day_count": Semantic(
        "扫描日计数", "journal.date", ("raw_run", "valid_completed", "t2_mature")),
    "zero_buy_day_count": Semantic(
        "买单数为 0 的扫描日计数", "*.n_bought", ("raw_run", "t2_mature")),
    "market_fwd2_mean": Semantic(
        "该日全市场 fwd_2_oc 均值,按日再取均值",
        "zero_buy_ledger.mkt_fwd2", ("t2_mature",)),
    "abstention_verdict_count": Semantic(
        "abstention v2(shadow_buys 口径)逐日裁决计数",
        "abstention_ledger.status_v2", ("t2_mature",)),
    "abstention_degraded_count": Semantic(
        "数据质量为 DEGRADED 的裁决日计数",
        "abstention_ledger.data_quality", ("t2_mature",)),
    "portfolio_return": Semantic(
        "组合累计收益(影子/真实各自口径)", "paper_nav.total_return",
        ("t2_mature",)),
    "portfolio_trade_count": Semantic(
        "组合成交笔数", "paper_nav.n_trades", ("t2_mature",)),
    "left_tail_protection_rate": Semantic(
        "被拦票 fwd_2_oc ≤ -5% 的占比(收缩估计)—— 量的是**左尾保护**,"
        "**不是错杀率**,两者不得互相翻译",
        "gate_ledger.tail_rate", ("legacy_migration",)),
    "gate_block_count": Semantic(
        "该门的拦截次数", "gate_attribution.n_fires",
        ("experiment_eligible", "legacy_migration")),
    "false_kill_rate": Semantic(
        "被拦票 excess_2 ≥ +2pp 的占比(A11 v3 归因口径)",
        "gate_attribution.false_kill_rate",
        ("experiment_eligible", "legacy_migration")),
    "correct_block_rate": Semantic(
        "被拦票 excess_2 < 0 的占比(A11 v3 归因口径)",
        "gate_attribution.correct_rate",
        ("experiment_eligible", "legacy_migration")),
    "gate_mean_excess2": Semantic(
        "被拦票相对市场基准的 T+2 超额均值",
        "gate_attribution.mean_excess_2",
        ("experiment_eligible", "legacy_migration")),
    "experiment_count": Semantic(
        "registry 内实验计数", "experiment_registry.experiments",
        ("raw_run",)),
}


class EvidenceError(ValueError):
    """清单违反了它自己的契约 —— 写入时就该失败,不能等读的人去发现。"""


@dataclass(frozen=True)
class Denominator:
    denominator_id: str
    value: int
    cohort: str
    definition: str


@dataclass
class Metric:
    metric_id: str
    semantic: str
    value: float | int | None
    numerator: float | int | None
    denominator_id: str | None
    cohort: str
    as_of: str | None
    source_paths: list[str]
    source_hashes: dict[str, str]
    query_version: str = QUERY_VERSION
    status: str = "OK"
    note: str | None = None
    # 由 semantic 反查填入(见 `Manifest.add`)。它存在的唯一理由是:让**事后**改
    # `semantic` 却不改来源的篡改能被 `validate` 逮住 —— 「39% 改叫错杀率」正是这个动作。
    source_field: str | None = None


@dataclass
class Manifest:
    schema_version: int = SCHEMA_VERSION
    scope: str = "cross_day"
    as_of: str | None = None
    cohorts: dict[str, str] = field(default_factory=lambda: dict(COHORTS))
    denominators: dict[str, dict] = field(default_factory=dict)
    metrics: dict[str, dict] = field(default_factory=dict)
    registry_inventory: dict = field(default_factory=dict)
    conflicts: list[dict] = field(default_factory=list)

    # ── 写入侧契约(违反即抛,别指望读的人发现)────────────────────────────
    def declare_denominator(self, denom: Denominator) -> None:
        if denom.cohort not in COHORTS:
            raise EvidenceError(f"unknown cohort {denom.cohort!r}")
        existing = self.denominators.get(denom.denominator_id)
        if existing is None:
            self.denominators[denom.denominator_id] = asdict(denom)
            return
        if existing["cohort"] != denom.cohort:
            raise EvidenceError(
                f"denominator {denom.denominator_id!r} 已属于 cohort "
                f"{existing['cohort']!r},不能再登记成 {denom.cohort!r} —— "
                "raw 与 mature 的天数不是同一个分母"
            )
        if existing["value"] != denom.value:
            self.conflicts.append({
                "kind": "denominator_value",
                "denominator_id": denom.denominator_id,
                "values": sorted({existing["value"], denom.value}),
            })

    def add(self, metric: Metric) -> None:
        semantic = SEMANTICS.get(metric.semantic)
        if semantic is None:
            raise EvidenceError(f"unknown semantic {metric.semantic!r}")
        if metric.source_field is None:
            metric.source_field = semantic.source_field
        elif metric.source_field != semantic.source_field:
            raise EvidenceError(
                f"metric {metric.metric_id!r} 的来源字段 {metric.source_field!r} "
                f"与 semantic {metric.semantic!r} 绑定的 "
                f"{semantic.source_field!r} 不符"
            )
        if metric.cohort not in COHORTS:
            raise EvidenceError(f"unknown cohort {metric.cohort!r}")
        if metric.cohort not in semantic.cohorts:
            raise EvidenceError(
                f"semantic {metric.semantic!r} 不允许 cohort {metric.cohort!r};"
                f"允许的是 {semantic.cohorts}"
            )
        if metric.denominator_id is not None:
            declared = self.denominators.get(metric.denominator_id)
            if declared is None:
                raise EvidenceError(
                    f"metric {metric.metric_id!r} 引用了未登记的分母 "
                    f"{metric.denominator_id!r}"
                )
            if declared["cohort"] != metric.cohort:
                raise EvidenceError(
                    f"metric {metric.metric_id!r} 声明 cohort {metric.cohort!r},"
                    f"却用了 {declared['cohort']!r} 的分母 "
                    f"{metric.denominator_id!r} —— 跨 cohort 拼句"
                )
        prior = self.metrics.get(metric.metric_id)
        if prior is not None and prior["value"] != metric.value:
            self.conflicts.append({
                "kind": "metric_value",
                "metric_id": metric.metric_id,
                "values": [prior["value"], metric.value],
            })
            metric.status = "CONFLICT"
        self.metrics[metric.metric_id] = asdict(metric)

    def flag_conflict(self, kind: str, **detail) -> None:
        self.conflicts.append({"kind": kind, **detail})

    def to_dict(self) -> dict:
        payload = asdict(self)
        for entry in self.conflicts:
            mid = entry.get("metric_id")
            if mid and mid in payload["metrics"]:
                payload["metrics"][mid]["status"] = "CONFLICT"
        return payload

    def quotable(self, metric_id: str) -> bool:
        """能不能把这个数写进结论句 —— CONFLICT 的指标一律不能。"""
        record = self.metrics.get(metric_id)
        return bool(record) and record.get("status") == "OK" and (
            record.get("value") is not None
        )


def file_hash(path: Path | str) -> str | None:
    target = Path(path)
    if not target.exists() or not target.is_file():
        return None
    return hashlib.sha256(target.read_bytes()).hexdigest()[:16]


def _hashes(paths: list[Path]) -> tuple[list[str], dict[str, str]]:
    existing = [p for p in paths if p.exists()]
    return (
        [str(p) for p in existing],
        {str(p): h for p in existing if (h := file_hash(p))},
    )


def _rate(numerator: int, denominator: int) -> float | None:
    return round(numerator / denominator, 6) if denominator else None


# ────────────────────────── 跨日清单 ──────────────────────────

def build(scan_root: Path | str | None = None,
          registry_path: Path | str | None = None) -> Manifest:
    """跨日证据清单 —— 设计稿 §1.1 的每个数字都由本函数再生。"""
    from autoresearch.learning import gate_attribution as ga
    from autoresearch.learning.abstention_ledger import roll as abstention_roll
    from autoresearch.learning.journal import roll as journal_roll
    from autoresearch.learning.zero_buy_ledger import roll as zero_buy_roll

    root = Path(scan_root or "context/scan")
    manifest = Manifest()

    _add_journal(manifest, journal_roll(root), root)
    _add_zero_buy(manifest, zero_buy_roll(root), root)
    _add_abstention(manifest, abstention_roll(root), root)
    _add_paper_nav(manifest)
    _add_gates(manifest, ga, root)
    _add_registry(manifest, registry_path)
    _cross_check_buys(manifest, journal_roll(root), zero_buy_roll(root))

    dates = [
        m["as_of"] for m in manifest.metrics.values() if m.get("as_of")
    ]
    manifest.as_of = max(dates) if dates else None
    return manifest


def _add_journal(manifest: Manifest, journal: pd.DataFrame, root: Path) -> None:
    paths, hashes = _hashes([root])
    if not len(journal):
        return
    as_of = str(journal["date"].max())
    buys = pd.to_numeric(journal["buys"], errors="coerce").fillna(0)
    n_days = int(len(journal))
    manifest.declare_denominator(Denominator(
        "journal_scan_days", n_days, "raw_run",
        "journal 的行数 —— 运营历史,不是收益分母"))
    manifest.add(Metric(
        "journal.scan_days", "scan_day_count", n_days, n_days,
        "journal_scan_days", "raw_run", as_of, paths, hashes))
    manifest.add(Metric(
        "journal.zero_buy_days", "zero_buy_day_count",
        int((buys == 0).sum()), int((buys == 0).sum()),
        "journal_scan_days", "raw_run", as_of, paths, hashes,
        note="raw cohort —— 与 zero_buy_ledger 的成熟日计数不是同一个数,不得混用"))

    complete = journal[
        journal["finalists"].notna() & pd.to_numeric(
            journal["cards"], errors="coerce").fillna(0).gt(0)
    ]
    manifest.declare_denominator(Denominator(
        "valid_completed_days", int(len(complete)), "valid_completed",
        "有 finalists 且至少一张决策卡的扫描日"))
    manifest.add(Metric(
        "journal.valid_completed_days", "scan_day_count",
        int(len(complete)), int(len(complete)),
        "valid_completed_days", "valid_completed", as_of, paths, hashes))


def _add_zero_buy(manifest: Manifest, ledger: pd.DataFrame, root: Path) -> None:
    if not len(ledger):
        return
    paths, hashes = _hashes(
        sorted(root.glob("*/retro/attribution.csv"))[-1:] or [root])
    as_of = str(ledger["date"].max())
    zero = ledger[ledger["n_bought"] == 0]
    some = ledger[ledger["n_bought"] > 0]
    manifest.declare_denominator(Denominator(
        "zero_buy_mature_days", int(len(ledger)), "t2_mature",
        "retro 已归因、可算 fwd 的扫描日"))
    manifest.declare_denominator(Denominator(
        "zero_buy_mature_zero_days", int(len(zero)), "t2_mature",
        "上述成熟日中买单为 0 的日"))
    manifest.declare_denominator(Denominator(
        "zero_buy_mature_bought_days", int(len(some)), "t2_mature",
        "上述成熟日中有买单的日"))
    manifest.add(Metric(
        "zero_buy.mature_days", "scan_day_count", int(len(ledger)),
        int(len(ledger)), "zero_buy_mature_days", "t2_mature",
        as_of, paths, hashes))
    manifest.add(Metric(
        "zero_buy.mature_zero_days", "zero_buy_day_count", int(len(zero)),
        int(len(zero)), "zero_buy_mature_days", "t2_mature",
        as_of, paths, hashes))
    for label, frame, denom in (
        ("zero", zero, "zero_buy_mature_zero_days"),
        ("bought", some, "zero_buy_mature_bought_days"),
    ):
        value = frame["mkt_fwd2"].mean()
        manifest.add(Metric(
            f"zero_buy.{label}_day_market_fwd2_mean", "market_fwd2_mean",
            None if pd.isna(value) else round(float(value), 6),
            None, denom, "t2_mature", as_of, paths, hashes))


def _add_abstention(manifest: Manifest, ledger: pd.DataFrame, root: Path) -> None:
    if not len(ledger):
        return
    paths, hashes = _hashes(sorted(root.glob("*/retro/abstention_verdict.json")))
    as_of = str(ledger["date"].max())
    judged = ledger[ledger["status_v2"].notna()]
    manifest.declare_denominator(Denominator(
        "abstention_v2_judged_days", int(len(judged)), "t2_mature",
        "abstention v2(shadow_buys 口径)已裁决的日"))
    counts = judged["status_v2"].value_counts().to_dict()
    for status in ("CORRECT", "FALSE", "NEUTRAL", "IMMATURE"):
        manifest.add(Metric(
            f"abstention_v2.{status.lower()}_days", "abstention_verdict_count",
            int(counts.get(status, 0)), int(counts.get(status, 0)),
            "abstention_v2_judged_days", "t2_mature", as_of, paths, hashes))
    degraded = int((judged["data_quality"] == "DEGRADED").sum())
    manifest.add(Metric(
        "abstention_v2.degraded_days", "abstention_degraded_count",
        degraded, degraded, "abstention_v2_judged_days", "t2_mature",
        as_of, paths, hashes))


def _add_paper_nav(manifest: Manifest) -> None:
    """paper_nav 只读它已落盘的汇总 —— 本模块不重算收益(§A0 边界)。"""
    summary = Path("reports/learning/paper_nav.md")
    paths, hashes = _hashes([summary])
    lanes = _parse_paper_nav(summary)
    if not lanes:
        return
    as_of = lanes.pop("as_of", None)
    hold_days = lanes.pop("hold_days", None)
    n_summaries = lanes.pop("n_summaries", 1)
    note = (f"hold={hold_days} 主表(报告内共 {n_summaries} 张成绩单);"
            "组合政策的观察差,**不能**归因到某一根门柱 —— 真实/影子的成交数与暴露不同")
    for lane, (ret, trades) in lanes.items():
        denom_id = f"paper_nav_{lane}_trades"
        manifest.declare_denominator(Denominator(
            denom_id, int(trades), "t2_mature", f"{lane} 线成交笔数"))
        manifest.add(Metric(
            f"paper_nav.{lane}_return", "portfolio_return", ret, None,
            denom_id if trades else None, "t2_mature", as_of, paths, hashes,
            note=note))
        manifest.add(Metric(
            f"paper_nav.{lane}_trades", "portfolio_trade_count",
            int(trades), int(trades), None, "t2_mature",
            as_of, paths, hashes))


def _parse_paper_nav(path: Path) -> dict:
    """从 paper_nav 报告抽四条线的累计收益与成交笔数。抽不到 → 空(宁可缺,不臆造)。

    ⚠️ 报告里有**两张**成绩单(主表 hold=2,对照表 hold=10),各带一行「截至」汇总。
    设计稿 §1.1 引的是主表 —— 这里只取**第一行**汇总,并把 hold 期限写进 `note`;
    「随便 grep 一个数字」正是 A0 要消灭的引用方式。
    """
    import re

    if not path.exists():
        return {}
    text = path.read_text(encoding="utf-8")
    summaries = re.findall(
        r"- \*\*截至 (\d{8})\*\*:真实 (-?[\d.]+)%\((\d+) 笔\)"
        r" vs 影子 (-?[\d.]+)%\((\d+) 笔\)"
        r"(?: vs 影子\(sized\) (-?[\d.]+)%)?"
        r" vs 市场 (-?[\d.]+)%",
        text,
    )
    if not summaries:
        return {}
    as_of, real, n_real, shadow, n_shadow, sized, market = summaries[0]
    hold = re.search(r"持(\d+)交易日", text)
    out = {
        "as_of": as_of,
        "hold_days": int(hold.group(1)) if hold else None,
        "n_summaries": len(summaries),
        "real": (round(float(real) / 100, 6), int(n_real)),
        "shadow": (round(float(shadow) / 100, 6), int(n_shadow)),
        "market_equal_weight": (round(float(market) / 100, 6), 0),
    }
    if sized:
        # sized 是同一批 shadow 信号的另一种仓位法 → 笔数与 shadow 同源
        out["shadow_sized"] = (round(float(sized) / 100, 6), int(n_shadow))
    return out


def _add_gates(manifest: Manifest, ga, root: Path) -> None:
    """门归因:v3 进 experiment_eligible;legacy 只作迁移基线,cohort 另立。"""
    paths, hashes = _hashes(sorted(root.glob("*/gate_fires.csv"))[-1:] or [root])
    for cohort, rows in (
        ("experiment_eligible", ga.roll(root, cohort=ga.COHORT_V3)),
        ("legacy_migration", ga.roll(root, cohort=ga.COHORT_LEGACY)),
    ):
        if not len(rows):
            continue
        as_of = str(rows["date"].max())
        prefix = "gate_v3" if cohort == "experiment_eligible" else "gate_legacy"
        for row in ga.summarize(rows).itertuples(index=False):
            slug = f"{prefix}.{row.gate}"
            denom_id = f"{slug}.measured_n"
            manifest.declare_denominator(Denominator(
                denom_id, int(row.measured_n), cohort,
                f"{row.gate} 门被拦且 outcome 可测的候选数"))
            manifest.add(Metric(
                f"{slug}.n_fires", "gate_block_count", int(row.n_fires),
                int(row.n_fires), None, cohort, as_of, paths, hashes,
                note="计数,不是比率 —— 别拿 measured_n 当它的分母"))
            for metric_name, semantic, value, numerator in (
                ("false_kill_rate", "false_kill_rate", row.false_kill_rate,
                 int(row.FALSE_KILL)),
                ("correct_rate", "correct_block_rate", row.correct_rate,
                 int(row.CORRECT)),
            ):
                manifest.add(Metric(
                    f"{slug}.{metric_name}", semantic,
                    None if value is None or pd.isna(value) else float(value),
                    numerator, denom_id, cohort, as_of, paths, hashes))
            manifest.add(Metric(
                f"{slug}.mean_excess_2", "gate_mean_excess2",
                None if row.mean_excess_2 is None or pd.isna(row.mean_excess_2)
                else float(row.mean_excess_2),
                None, denom_id, cohort, as_of, paths, hashes))

    _add_gate_left_tail(manifest, root, paths, hashes)


def _add_gate_left_tail(manifest: Manifest, root: Path,
                        paths: list[str], hashes: dict[str, str]) -> None:
    """gate_ledger 的左尾保护率单列 —— 它**不是**错杀率,`semantic` 钉死这一点。"""
    from autoresearch.learning.gate_ledger import roll as gate_roll

    ledger = gate_roll(root)
    if not len(ledger):
        return
    from autoresearch.learning.gate_attribution import (
        BINDING_PREFIX,
        normalize_gate,
    )

    binding = ledger[ledger["check"].astype(str).str.startswith(BINDING_PREFIX)]
    for row in binding.itertuples(index=False):
        gate = normalize_gate(row.check[len(BINDING_PREFIX):])
        if row.tail_rate is None or pd.isna(row.tail_rate):
            continue
        denom_id = f"gate_legacy.{gate}.tail_n"
        manifest.declare_denominator(Denominator(
            denom_id, int(row.tail_n), "legacy_migration",
            f"{gate} 门被拦票中 fwd_2_oc 非空的观测数"))
        manifest.add(Metric(
            f"gate_legacy.{gate}.left_tail_protection_rate",
            "left_tail_protection_rate", float(row.tail_rate), None,
            denom_id, "legacy_migration", None, paths, hashes,
            note="被拦票跌破 -5% 的占比;把它读成错杀率是 Wave10 立案时点名的误读"))


def _add_registry(manifest: Manifest, registry_path: Path | str | None) -> None:
    """registry inventory —— 后续实验不得绕开已在册的 family 另开冲突实验(§1.3-5)。"""
    from autoresearch.learning.experiment_registry import (
        DEFAULT_REGISTRY,
        RegistryError,
        load_registry,
    )

    path = Path(registry_path or DEFAULT_REGISTRY)
    paths, hashes = _hashes([path])
    try:
        payload = load_registry(path)
    except RegistryError as exc:
        manifest.flag_conflict("registry_unreadable", path=str(path), error=str(exc))
        return
    experiments = payload.get("experiments", {})
    manifest.registry_inventory = {
        "path": str(path),
        "stable_baseline": (payload.get("stable_baseline") or {}).get("name"),
        "active_by_family": payload.get("active_by_family", {}),
        "experiments": {
            exp_id: {
                "status": record.get("status"),
                # `trial_family` 才是 family —— §C2.0 的「同 family 不得并开」规则看的是它。
                # 此前这里取的是 challenger_pointer.kind(如 "shadow_gate"),那是**载体类型**,
                # 两个不同 family 的影子实验会显示成同一个 family,冲突检查形同虚设。
                "family": record.get("trial_family"),
                "pointer_kind": (record.get("challenger_pointer") or {}).get("kind"),
                "primary_metric": record.get("primary_metric"),
                "definition_hash": record.get("definition_hash"),
                "expires_date": record.get("expires_date"),
            }
            for exp_id, record in experiments.items()
        },
    }
    manifest.declare_denominator(Denominator(
        "registry_experiments", len(experiments), "raw_run", "registry 内实验总数"))
    manifest.add(Metric(
        "registry.experiment_count", "experiment_count", len(experiments),
        len(experiments), "registry_experiments", "raw_run", None, paths, hashes))


def _cross_check_buys(manifest: Manifest, journal: pd.DataFrame,
                      zero_buy: pd.DataFrame) -> None:
    """journal 与 zero_buy 对同一天的买单数必须一致(spec 2026-07-12 P0-1 的 D5 病)。

    这是真的会分叉的两本账 —— 历史上一本读卡面、一本读 attribution。对不上就是 CONFLICT,
    引用它们的句子必须闭嘴,但扫描照跑。
    """
    if not len(journal) or not len(zero_buy):
        return
    left = journal.set_index("date")["buys"]
    right = zero_buy.set_index("date")["n_bought"]
    for date in left.index.intersection(right.index):
        a, b = left.loc[date], right.loc[date]
        if pd.isna(a):
            continue
        if int(a) != int(b):
            manifest.flag_conflict(
                "buy_count_disagreement", date=str(date),
                journal=int(a), zero_buy_ledger=int(b))


# ────────────────────────── 单日清单 ──────────────────────────

def build_day(scan_dir: Path | str) -> Manifest:
    """单日证据清单:该日的 cohort 归属 + 自己的门归因分布。"""
    from autoresearch.learning import gate_attribution as ga

    day = Path(scan_dir)
    manifest = Manifest(scope="day", as_of=day.name)
    attr = day / "retro" / "attribution.csv"
    paths, hashes = _hashes([day / "gate_fires.csv",
                             day / "decision_records.json", attr])
    manifest.registry_inventory = {}
    manifest.metrics["day.cohort_membership"] = {
        "metric_id": "day.cohort_membership",
        "raw_run": True,
        "valid_completed": (day / "finalists.csv").exists(),
        "t2_mature": attr.exists(),
        "status": "OK",
        "query_version": QUERY_VERSION,
        "source_paths": paths,
        "source_hashes": hashes,
    }
    rows = ga.build_day(day, cohort=ga.COHORT_V3)
    if not len(rows):
        return manifest
    for row in ga.summarize(rows).itertuples(index=False):
        denom_id = f"gate_v3.{row.gate}.measured_n"
        manifest.declare_denominator(Denominator(
            denom_id, int(row.measured_n), "experiment_eligible",
            f"{row.gate} 门当日可测候选数"))
        manifest.add(Metric(
            f"gate_v3.{row.gate}.n_fires", "gate_block_count",
            int(row.n_fires), int(row.n_fires), None,
            "experiment_eligible", day.name, paths, hashes))
        manifest.add(Metric(
            f"gate_v3.{row.gate}.false_kill_rate", "false_kill_rate",
            None if row.false_kill_rate is None or pd.isna(row.false_kill_rate)
            else float(row.false_kill_rate),
            int(row.FALSE_KILL), denom_id, "experiment_eligible",
            day.name, paths, hashes))
    return manifest


# ────────────────────────── 渲染 / 校验 / CLI ──────────────────────────

def render(manifest: Manifest | dict) -> list[str]:
    """Markdown **完全**由 manifest 渲染 —— 这里不许出现任何字面量数字(§1.3-3)。"""
    payload = manifest.to_dict() if isinstance(manifest, Manifest) else manifest
    lines = [
        "# Wave10 证据清单(每个数字都带分母、cohort 与来源指纹)",
        "",
        f"- schema_version `{payload['schema_version']}` · "
        f"as_of `{payload.get('as_of') or '—'}`",
        "",
        "## cohort 定义",
        "",
        "| cohort | 含义 |",
        "|---|---|",
    ]
    lines += [f"| `{name}` | {desc} |" for name, desc in payload["cohorts"].items()]
    lines += ["", "## 分母登记表", "",
              "| denominator_id | 值 | cohort | 定义 |", "|---|---:|---|---|"]
    for denom in payload["denominators"].values():
        lines.append(
            f"| `{denom['denominator_id']}` | {denom['value']} "
            f"| `{denom['cohort']}` | {denom['definition']} |"
        )
    lines += ["", "## 指标", "",
              "| metric_id | 值 | 分子 | 分母 | cohort | as_of | 状态 |",
              "|---|---|---:|---|---|---|---|"]
    for metric in payload["metrics"].values():
        if "semantic" not in metric:
            continue
        denom = metric.get("denominator_id")
        denom_value = (
            payload["denominators"].get(denom, {}).get("value")
            if denom else None
        )
        value = metric["value"]
        shown = "—" if value is None else (
            f"{value:.4f}" if isinstance(value, float) else str(value))
        lines.append(
            f"| `{metric['metric_id']}` | {shown} "
            f"| {'—' if metric['numerator'] is None else metric['numerator']} "
            f"| {'—' if denom is None else f'`{denom}`={denom_value}'} "
            f"| `{metric['cohort']}` | {metric.get('as_of') or '—'} "
            f"| {metric['status']} |"
        )
    inventory = payload.get("registry_inventory") or {}
    if inventory.get("experiments"):
        lines += ["", "## registry inventory", "",
                  "| 实验 | 状态 | family | 主判据 | 到期 |", "|---|---|---|---|---|"]
        for exp_id, record in inventory["experiments"].items():
            lines.append(
                f"| `{exp_id}` | {record['status']} | {record['family']} "
                f"| {record['primary_metric']} | {record['expires_date']} |"
            )
    conflicts = payload.get("conflicts") or []
    lines += ["", "## 冲突", ""]
    lines += (
        [f"- ⚠️ `{c['kind']}`:" + " · ".join(
            f"{k}={v}" for k, v in c.items() if k != "kind") for c in conflicts]
        if conflicts else ["_无 —— 各源对同一事实的读数一致_"]
    )
    lines += [
        "",
        "_CONFLICT 的指标不得进入任何结论句;扫描不因此阻断。_",
    ]
    return lines


def validate(payload: dict) -> list[str]:
    """校验已落盘的清单(冻结快照的守卫)。返回问题列表,空 = 通过。"""
    problems = []
    if payload.get("schema_version") != SCHEMA_VERSION:
        problems.append(f"schema_version={payload.get('schema_version')}")
    denominators = payload.get("denominators", {})
    for metric in payload.get("metrics", {}).values():
        if "semantic" not in metric:
            continue
        mid = metric["metric_id"]
        semantic = SEMANTICS.get(metric["semantic"])
        if semantic is None:
            problems.append(f"{mid}: 未知 semantic {metric['semantic']!r}")
            continue
        if metric.get("source_field") != semantic.source_field:
            problems.append(
                f"{mid}: 来源字段 {metric.get('source_field')!r} 与 semantic "
                f"{metric['semantic']!r} 绑定的 {semantic.source_field!r} 不符 —— "
                "改了标签没改来源 = 语义注入"
            )
        if metric["cohort"] not in semantic.cohorts:
            problems.append(
                f"{mid}: semantic {metric['semantic']!r} 不允许 cohort "
                f"{metric['cohort']!r}"
            )
        denom_id = metric.get("denominator_id")
        if denom_id and denom_id in denominators:
            if denominators[denom_id]["cohort"] != metric["cohort"]:
                problems.append(
                    f"{mid}: 跨 cohort 引用分母 {denom_id!r}")
        elif denom_id:
            problems.append(f"{mid}: 分母 {denom_id!r} 未登记")
    return problems


def main(argv: list[str] | None = None) -> int:
    import argparse

    ap = argparse.ArgumentParser(description="Wave10 证据清单(确定性)")
    ap.add_argument("--scan-root", default=None)
    ap.add_argument("--day", default=None, help="只生成该扫描日的单日清单")
    ap.add_argument("--check", default=None, help="校验一份已落盘的清单 JSON")
    ap.add_argument("--freeze", default=None,
                    help="把当前清单冻结到该路径(docs/ 下的审计快照,进 git)")
    args = ap.parse_args(argv)

    if args.check:
        payload = json.loads(Path(args.check).read_text(encoding="utf-8"))
        problems = validate(payload)
        print(f"═══ 校验 {args.check} ═══")
        for problem in problems:
            print(f"  ✗ {problem}")
        print(f"  —— {'通过' if not problems else f'{len(problems)} 处违约'}")
        return 0 if not problems else 1

    root = Path(args.scan_root or "context/scan")
    if args.freeze:
        # reports/ 与 context/ 都 gitignore,审计快照必须落在进版本控制的 docs/ 下
        manifest = build(root)
        target = Path(args.freeze)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(
            json.dumps(manifest.to_dict(), ensure_ascii=False,
                       sort_keys=True, indent=2) + "\n",
            encoding="utf-8")
        problems = validate(manifest.to_dict())
        print(f"[evidence_manifest] 冻结 {len(manifest.metrics)} 指标 → {target}"
              + ("" if not problems else f";⚠️ {len(problems)} 处违约"))
        return 0 if not problems else 1

    if args.day:
        day = root / args.day
        manifest = build_day(day)
        target = day / "evidence_manifest.json"
        target.write_text(
            json.dumps(manifest.to_dict(), ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8")
        print(f"[evidence_manifest] {args.day} → {target}")
        return 0

    manifest = build(root)
    out_json = Path("reports/learning/wave10_evidence.json")
    out_json.parent.mkdir(parents=True, exist_ok=True)
    out_json.write_text(
        json.dumps(manifest.to_dict(), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8")
    out_md = Path("reports/learning/wave10_evidence.md")
    out_md.write_text("\n".join(render(manifest)) + "\n", encoding="utf-8")
    print(f"[evidence_manifest] {len(manifest.metrics)} 指标 · "
          f"{len(manifest.denominators)} 分母 · {len(manifest.conflicts)} 冲突 "
          f"→ {out_json} / {out_md}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
