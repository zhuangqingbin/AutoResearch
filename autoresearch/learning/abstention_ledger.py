#!/usr/bin/env python3
"""Hash-verified causal verdicts for zero-BUY scan days."""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass, replace
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

from autoresearch.common import workspace as ws
from autoresearch.common.ruler import MAIN_RULER
from autoresearch.scan.run_contract import sha256_json

ABSTENTION_VERDICT_SCHEMA_VERSION = 2
_SUPPORTED_SCHEMA_VERSIONS = (1, 2)
STATUSES = {
    "IMMATURE",
    "FALSE",
    "CORRECT",
    "NEUTRAL",
    "NOT_ABSTAINED",
}
DATA_QUALITIES = {"COMPLETE", "DEGRADED"}

# v2 新增字段(Wave8 W8-11)。v1 记录的哈希锁的是**当时的事实**,不能因为加字段就
# 重算 —— 故 `_hash_payload` 在 schema_version<2 时剔除这三个键,两代记录各按自己的
# 版本校验哈希。`status_v2` 用 None 表示「无 shadow 数据」,**不新增 STATUSES 枚举值**
# (枚举有 render/roll 等迭代消费方,新值会污染它们的分桶)。
_V2_ONLY_FIELDS = ("status_v2", "shadow_opportunity_codes", "recall_ceiling_n")


@dataclass(frozen=True)
class AbstentionVerdict:
    schema_version: int
    date: str
    status: str
    n_bought: int
    n_rejected: int
    n_opportunities: int
    opportunity_codes: list[str]
    data_quality: str
    reasons: list[str]
    generated_at: str
    verdict_hash: str
    # ── v2(Wave8 W8-11)──
    status_v2: str | None = None          # shadow_buys 口径裁决;None = 无 shadow 数据
    shadow_opportunity_codes: tuple[str, ...] = ()
    recall_ceiling_n: int = 0             # 旧全市场口径降级来的诊断数(召回上限,非裁决)

    def _hash_payload(self) -> dict:
        payload = asdict(self)
        payload.pop("verdict_hash")
        if int(self.schema_version) < 2:
            for key in _V2_ONLY_FIELDS:
                payload.pop(key, None)
        else:
            payload["shadow_opportunity_codes"] = list(self.shadow_opportunity_codes)
        return payload

    def semantic_payload(self) -> dict:
        payload = self._hash_payload()
        payload.pop("generated_at")
        return payload

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def build(
        cls,
        *,
        date: str,
        status: str,
        n_bought: int,
        n_rejected: int,
        opportunity_codes: list[str],
        data_quality: str,
        reasons: list[str],
        now: datetime | None = None,
        status_v2: str | None = None,
        shadow_opportunity_codes: list[str] | None = None,
        recall_ceiling_n: int = 0,
    ) -> AbstentionVerdict:
        if status not in STATUSES:
            raise ValueError(f"invalid abstention status: {status!r}")
        if data_quality not in DATA_QUALITIES:
            raise ValueError(
                f"invalid abstention data_quality: {data_quality!r}"
            )
        stamp = now or datetime.now(timezone.utc)
        if stamp.tzinfo is None:
            stamp = stamp.replace(tzinfo=timezone.utc)
        stamp = stamp.astimezone(timezone.utc)
        codes = sorted(
            {
                str(code).strip().split(".")[0].zfill(6)
                for code in opportunity_codes
            }
        )
        base = cls(
            schema_version=ABSTENTION_VERDICT_SCHEMA_VERSION,
            date=str(date),
            status=status,
            n_bought=int(n_bought),
            n_rejected=int(n_rejected),
            n_opportunities=len(codes),
            opportunity_codes=codes,
            data_quality=data_quality,
            reasons=list(dict.fromkeys(str(reason) for reason in reasons)),
            generated_at=stamp.isoformat(
                timespec="microseconds"
            ).replace("+00:00", "Z"),
            verdict_hash="",
            status_v2=status_v2,
            shadow_opportunity_codes=tuple(sorted(
                {str(c).strip().split(".")[0].zfill(6)
                 for c in (shadow_opportunity_codes or [])})),
            recall_ceiling_n=int(recall_ceiling_n),
        )
        return replace(
            base,
            verdict_hash=sha256_json(base._hash_payload()),
        )

    @classmethod
    def from_dict(cls, raw: dict) -> AbstentionVerdict:
        raw = dict(raw)
        if int(raw.get("schema_version", 0)) not in _SUPPORTED_SCHEMA_VERSIONS:
            raise ValueError(
                "unsupported abstention verdict schema_version="
                f"{raw.get('schema_version')}"
            )
        # v1 记录缺 v2 字段 → 补默认值(哈希仍按 v1 payload 校验,见 `_hash_payload`)
        raw.setdefault("status_v2", None)
        raw.setdefault("recall_ceiling_n", 0)
        raw["shadow_opportunity_codes"] = tuple(raw.get("shadow_opportunity_codes") or ())
        verdict = cls(**raw)
        if verdict.status not in STATUSES:
            raise ValueError("invalid abstention verdict status")
        if verdict.data_quality not in DATA_QUALITIES:
            raise ValueError("invalid abstention verdict data_quality")
        if verdict.verdict_hash != sha256_json(verdict._hash_payload()):
            raise ValueError("abstention verdict hash mismatch")
        return verdict


def _bool_series(
    frame: pd.DataFrame,
    name: str,
    *,
    default: bool,
) -> pd.Series:
    if name not in frame.columns:
        return pd.Series(default, index=frame.index, dtype=bool)
    values = frame[name]
    if values.dtype == bool:
        return values.fillna(default)
    return values.astype(str).str.strip().str.lower().isin(
        {"true", "1", "yes", "是"}
    )


def _health_degraded(health: dict | None) -> bool:
    if not health:
        return False
    if health.get("core_missing"):
        return True
    for key in (
        "run_contract",
        "stage_results",
        "decision_records",
        "post_run",
    ):
        status = (health.get(key) or {}).get("status")
        if status in {"INVALID", "MISMATCH"}:
            return True
    return False


def classify_abstention(
    rejection_rows: pd.DataFrame,
    *,
    health: dict | None = None,
    now: datetime | None = None,
    shadow_codes: list[str] | None = None,
) -> AbstentionVerdict:
    """0 买日的因果裁决(Wave8 W8-11 引入 v2,2026-08-19 v1 headline 拔杆退役)。

    - `status_v2`(**主口径,唯一 ledger headline**):判据只看 `shadow_codes`(=
      系统当日最想买的 K 只,`shadow_buys.csv`)—— "如果门放行,我会买的东西"有没有
      真跑赢。`shadow_codes` 为空(该日无 shadow 数据)→ `status_v2=None`,不入统计。
    - `status`(v1,**schema 字段保留但 headline 已退役**):判据 = `rejection_attribution.csv`
      里任一 tradable 票相对市场中位 ≥+2pp。**那张表覆盖全市场**(07-24 实测 5,528 行、
      opportunity 命中 1,299 只)→ 5,000 只票的市场几乎天天恒真。它量的是**召回上限**,
      不是弃权决策,与 paper_nav「门在挣钱」的读数长期互扇——这正是 v1 headline 被拔的
      原因(判据:v2 达 ≥10 个 mature day 且两日重跑一致,2026-08-19 实测 19/19 全成熟,
      Wave10 §B5 回滚杆③判据达成)。字段/计算**不删**——`scan/market.py::_abstention_verdict_line`
      (逐日弃权裁决行)、`scan/health.py`(健康检查 payload)、`learning/zero_buy_ledger.py`
      (因果裁决小节)三个下游消费点仍直接读 `verdict.status`/`roll()` 的 `status` 列,
      本次改动**只退役 `render()`(本模块自己的 `abstention_ledger.md` 表)里的 v1 headline
      与 `recall_ceiling_n` 诊断列**,不动 schema、不动上述三个消费点。
    """
    rows = rejection_rows.copy()
    date = (
        str(rows["date"].iloc[0])
        if len(rows) and "date" in rows.columns
        else ""
    )
    actions = rows.get(
        "final_action",
        pd.Series("ABSTAIN", index=rows.index),
    ).astype(str)
    bought = actions == "BUY"
    rejected = ~bought
    n_bought = int(bought.sum())
    n_rejected = int(rejected.sum())

    quality = rows.get(
        "gate_state_quality",
        pd.Series("NOT_APPLICABLE", index=rows.index),
    ).astype(str)
    stages = rows.get(
        "first_rejection_stage",
        pd.Series("", index=rows.index),
    ).astype(str)
    undecidable = bool(
        ((quality == "UNKNOWN") | (stages == "DATA_UNDECIDABLE")).any()
    )
    degraded = undecidable or _health_degraded(health)
    reasons = []
    if undecidable:
        reasons.append("undecidable_candidates")
    if _health_degraded(health):
        reasons.append("control_health_degraded")
    data_quality = "DEGRADED" if degraded else "COMPLETE"

    if n_bought:
        return AbstentionVerdict.build(
            date=date,
            status="NOT_ABSTAINED",
            n_bought=n_bought,
            n_rejected=n_rejected,
            opportunity_codes=[],
            data_quality=data_quality,
            reasons=[*reasons, "buy_present"],
            now=now,
        )

    mature = _bool_series(rows, "mature", default=False)
    buyable = _bool_series(rows, "buyable", default=False)
    eligible = rejected & mature & buyable
    if not eligible.any():
        return AbstentionVerdict.build(
            date=date,
            status="IMMATURE",
            n_bought=0,
            n_rejected=n_rejected,
            opportunity_codes=[],
            data_quality=data_quality,
            reasons=[*reasons, "t2_immature"],
            now=now,
        )

    excess = pd.to_numeric(
        rows.get(
            "excess_2",
            pd.Series(float("nan"), index=rows.index),
        ),
        errors="coerce",
    )
    opportunity = (
        _bool_series(rows, "opportunity", default=False)
        & eligible
    )
    opportunity_codes = (
        rows.loc[opportunity, "code"].astype(str).str.zfill(6).tolist()
        if "code" in rows.columns
        else []
    )
    realized = excess[eligible & excess.notna()]

    def _verdict_from(hit_codes: list[str], hit_reason: str,
                      realized_scope: pd.Series) -> tuple[str, list[str]]:
        """同一套判定逻辑,只换**标的集** —— 两个口径必须问同一个问题、可比。

        ⚠️ 「机会」与「全都跑输了吗」必须**同集**:v2 若只把 FALSE 换成 shadow 口径、
        CORRECT 仍看全市场,就永远说不出 CORRECT(5,000 只票里总有人 ≥+2pp),
        等于换了个方式继续恒真。
        """
        if hit_codes:
            return "FALSE", [hit_reason]
        if degraded:
            return "NEUTRAL", []
        if len(realized_scope) and bool((realized_scope <= -0.02).all()):
            return "CORRECT", ["all_rejected_underperformed_band"]
        return "NEUTRAL", ["inside_economic_band"]

    status, extra = _verdict_from(
        opportunity_codes, "market_relative_opportunity", realized)
    reasons += extra

    # ── v2:机会只认 shadow_buys(系统当日最想买的 K 只)──
    norm_shadow = {str(c).strip().split(".")[0].zfill(6) for c in (shadow_codes or [])}
    if norm_shadow and "code" in rows.columns:
        codes6 = rows["code"].astype(str).str.zfill(6)
        in_shadow = codes6.isin(norm_shadow)
        shadow_hit = opportunity & in_shadow
        shadow_codes_hit = sorted(codes6[shadow_hit].tolist())
        realized_shadow = excess[eligible & in_shadow & excess.notna()]
        status_v2, extra_v2 = _verdict_from(
            shadow_codes_hit, "shadow_buy_outperformed", realized_shadow)
        reasons += [r for r in extra_v2 if r not in reasons]
    else:
        status_v2, shadow_codes_hit = None, []   # 无 shadow 数据:不判,不入统计

    return AbstentionVerdict.build(
        date=date,
        status=status,
        n_bought=0,
        n_rejected=n_rejected,
        opportunity_codes=opportunity_codes,
        data_quality=data_quality,
        reasons=reasons,
        status_v2=status_v2,
        shadow_opportunity_codes=shadow_codes_hit,
        recall_ceiling_n=len(opportunity_codes),
        now=now,
    )


def abstention_verdict_path(scan_dir: Path | str) -> Path:
    return Path(scan_dir) / "retro" / "abstention_verdict.json"


def load_abstention_verdict(path: Path | str) -> AbstentionVerdict:
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError("abstention verdict root must be an object")
    return AbstentionVerdict.from_dict(raw)


def shadow_codes_for(date: str, path: Path | str | None = None) -> list[str]:
    """该扫描日的影子买单代码(系统当日最想买的 K 只);无数据 → []。"""
    src = Path(path or ws.learning_root() / "shadow_buys.csv")
    if not src.exists():
        return []
    try:
        df = pd.read_csv(src, dtype={"code": str})
    except Exception:  # noqa: BLE001 — 读不动就是没有,不臆测
        return []
    if "date" not in df.columns or "code" not in df.columns:
        return []
    hit = df[df["date"].astype(str) == str(date)]
    return hit["code"].astype(str).str.zfill(6).tolist()


def write_abstention_verdict(
    scan_dir: Path | str,
    rejection_rows: pd.DataFrame,
    *,
    health: dict | None = None,
    now: datetime | None = None,
    shadow_path: Path | str | None = None,
) -> Path:
    scan = Path(scan_dir)
    target = abstention_verdict_path(scan)
    target.parent.mkdir(parents=True, exist_ok=True)
    verdict = classify_abstention(
        rejection_rows,
        health=health,
        now=now,
        shadow_codes=shadow_codes_for(scan.name, shadow_path),
    )
    if verdict.date != scan.name:
        verdict = AbstentionVerdict.build(
            date=scan.name,
            status=verdict.status,
            n_bought=verdict.n_bought,
            n_rejected=verdict.n_rejected,
            opportunity_codes=verdict.opportunity_codes,
            data_quality=verdict.data_quality,
            reasons=verdict.reasons,
            now=now,
            status_v2=verdict.status_v2,
            shadow_opportunity_codes=list(verdict.shadow_opportunity_codes),
            recall_ceiling_n=verdict.recall_ceiling_n,
        )
    if target.exists():
        existing = load_abstention_verdict(target)
        if existing.semantic_payload() == verdict.semantic_payload():
            return target
    temp = target.with_name(f"{target.name}.tmp")
    temp.write_text(
        json.dumps(verdict.to_dict(), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    temp.replace(target)
    return target


def roll(scan_root: Path | str | None = None) -> pd.DataFrame:
    root = Path(scan_root or ws.scan_root())
    rows = []
    for path in sorted(root.glob("*/retro/abstention_verdict.json")):
        verdict = load_abstention_verdict(path)
        if verdict.status == "NOT_ABSTAINED":
            continue
        rows.append(
            {
                "date": verdict.date,
                "status": verdict.status,
                "status_v2": verdict.status_v2,
                "n_bought": verdict.n_bought,
                "n_rejected": verdict.n_rejected,
                "n_opportunities": verdict.n_opportunities,
                "recall_ceiling_n": verdict.recall_ceiling_n,
                "opportunity_codes": "|".join(verdict.opportunity_codes),
                "shadow_opportunity_codes": "|".join(verdict.shadow_opportunity_codes),
                "data_quality": verdict.data_quality,
                "reasons": "|".join(verdict.reasons),
            }
        )
    columns = [
        "date",
        "status",
        "status_v2",
        "n_bought",
        "n_rejected",
        "n_opportunities",
        "recall_ceiling_n",
        "opportunity_codes",
        "shadow_opportunity_codes",
        "data_quality",
        "reasons",
    ]
    return pd.DataFrame(rows, columns=columns).sort_values(
        "date"
    ).reset_index(drop=True)


def render(ledger: pd.DataFrame) -> list[str]:
    lines = [
        f"# 0-BUY 因果裁决账本（主尺 {MAIN_RULER}，相对市场中位）",
        "",
    ]
    if ledger is None or not len(ledger):
        return lines + ["_无弃权裁决；未成熟日不会被静默省略。_"]
    order = ("CORRECT", "FALSE", "NEUTRAL", "IMMATURE")
    v2 = ledger.get("status_v2")
    n_v2 = int(v2.notna().sum()) if v2 is not None else 0
    counts_v2 = v2.dropna().value_counts().to_dict() if n_v2 else {}
    lines.append(
        "- **状态(shadow_buys 口径)**:"
        + (" · ".join(f"{s} {counts_v2.get(s, 0)}" for s in order)
           if n_v2 else "_尚无 shadow 数据_")
        + f"(已判 {n_v2}/{len(ledger)} 日)"
    )
    lines += [
        "",
        "| 日期 | 裁决 | 被拒 | shadow机会 | 数据质量 |",
        "|---|---|---:|---|---|",
    ]
    for row in ledger.itertuples(index=False):
        s2 = getattr(row, "status_v2", None)
        s2 = "—(no_shadow)" if s2 is None or (isinstance(s2, float) and pd.isna(s2)) else s2
        shadow = getattr(row, "shadow_opportunity_codes", "") or "—"
        lines.append(
            f"| {row.date} | {s2} | {row.n_rejected} "
            f"| {shadow} | {row.data_quality} |"
        )
    lines += [
        "",
        "_机会只认当日 `shadow_buys`(系统最想买的 K 只)—— 「如果门放行我会买的东西」"
        "有没有真跑赢。FALSE 只认次日开盘可交易且相对当日市场中位 ≥+2pp；"
        "UNKNOWN/坏事实只能令裁决降级，不能伪装成正确弃权。_\n"
        "_v1(全市场任一票 ≥+2pp 口径)已于 2026-08-19 从本表退役"
        "（判据：v2 达 ≥10 个 mature day 且两日重跑一致，实测 19/19 全成熟且两次"
        "重跑逐字一致，Wave10 §B5 回滚杆③判据达成）——5,000 只的市场里近乎恒真"
        "（07-24 实测命中 1,299 只），量的是召回上限而非弃权决策。v1 字段仍在 schema"
        "里供下游消费（`market.py` 逐日弃权裁决行 / `health.py` 健康检查 /"
        "`zero_buy_ledger.py` 因果裁决小节），只是不再是本表的 headline。_",
    ]
    return lines


def main() -> int:
    ledger = roll()
    target = ws.reports_root() / "learning/abstention_ledger.md"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("\n".join(render(ledger)) + "\n", encoding="utf-8")
    print(f"[abstention_ledger] {len(ledger)} 日 → {target}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
