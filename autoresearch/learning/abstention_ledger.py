#!/usr/bin/env python3
"""Hash-verified causal verdicts for zero-BUY scan days."""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass, replace
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

from autoresearch.scan.run_contract import sha256_json

ABSTENTION_VERDICT_SCHEMA_VERSION = 1
STATUSES = {
    "IMMATURE",
    "FALSE",
    "CORRECT",
    "NEUTRAL",
    "NOT_ABSTAINED",
}
DATA_QUALITIES = {"COMPLETE", "DEGRADED"}


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

    def _hash_payload(self) -> dict:
        payload = asdict(self)
        payload.pop("verdict_hash")
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
        )
        return replace(
            base,
            verdict_hash=sha256_json(base._hash_payload()),
        )

    @classmethod
    def from_dict(cls, raw: dict) -> AbstentionVerdict:
        verdict = cls(**raw)
        if verdict.schema_version != ABSTENTION_VERDICT_SCHEMA_VERSION:
            raise ValueError(
                "unsupported abstention verdict schema_version="
                f"{verdict.schema_version}"
            )
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
) -> AbstentionVerdict:
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
    if opportunity_codes:
        status = "FALSE"
        reasons.append("market_relative_opportunity")
    else:
        realized = excess[eligible & excess.notna()]
        if degraded:
            status = "NEUTRAL"
        elif len(realized) and bool((realized <= -0.02).all()):
            status = "CORRECT"
            reasons.append("all_rejected_underperformed_band")
        else:
            status = "NEUTRAL"
            reasons.append("inside_economic_band")
    return AbstentionVerdict.build(
        date=date,
        status=status,
        n_bought=0,
        n_rejected=n_rejected,
        opportunity_codes=opportunity_codes,
        data_quality=data_quality,
        reasons=reasons,
        now=now,
    )


def abstention_verdict_path(scan_dir: Path | str) -> Path:
    return Path(scan_dir) / "retro" / "abstention_verdict.json"


def load_abstention_verdict(path: Path | str) -> AbstentionVerdict:
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError("abstention verdict root must be an object")
    return AbstentionVerdict.from_dict(raw)


def write_abstention_verdict(
    scan_dir: Path | str,
    rejection_rows: pd.DataFrame,
    *,
    health: dict | None = None,
    now: datetime | None = None,
) -> Path:
    scan = Path(scan_dir)
    target = abstention_verdict_path(scan)
    target.parent.mkdir(parents=True, exist_ok=True)
    verdict = classify_abstention(
        rejection_rows,
        health=health,
        now=now,
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
    root = Path(scan_root or "context/scan")
    rows = []
    for path in sorted(root.glob("*/retro/abstention_verdict.json")):
        verdict = load_abstention_verdict(path)
        if verdict.status == "NOT_ABSTAINED":
            continue
        rows.append(
            {
                "date": verdict.date,
                "status": verdict.status,
                "n_bought": verdict.n_bought,
                "n_rejected": verdict.n_rejected,
                "n_opportunities": verdict.n_opportunities,
                "opportunity_codes": "|".join(verdict.opportunity_codes),
                "data_quality": verdict.data_quality,
                "reasons": "|".join(verdict.reasons),
            }
        )
    columns = [
        "date",
        "status",
        "n_bought",
        "n_rejected",
        "n_opportunities",
        "opportunity_codes",
        "data_quality",
        "reasons",
    ]
    return pd.DataFrame(rows, columns=columns).sort_values(
        "date"
    ).reset_index(drop=True)


def render(ledger: pd.DataFrame) -> list[str]:
    lines = [
        "# 0-BUY 因果裁决账本（主尺 fwd_2_oc，相对市场中位）",
        "",
    ]
    if ledger is None or not len(ledger):
        return lines + ["_无弃权裁决；未成熟日不会被静默省略。_"]
    counts = ledger["status"].value_counts().to_dict()
    lines.append(
        "- 状态:"
        + " · ".join(
            f"{status} {counts.get(status, 0)}"
            for status in ("CORRECT", "FALSE", "NEUTRAL", "IMMATURE")
        )
    )
    lines += [
        "",
        "| 日期 | 裁决 | 被拒 | +2pp机会 | 数据质量 | 机会代码 |",
        "|---|---|---:|---:|---|---|",
    ]
    for row in ledger.itertuples(index=False):
        lines.append(
            f"| {row.date} | {row.status} | {row.n_rejected} "
            f"| {row.n_opportunities} | {row.data_quality} "
            f"| {row.opportunity_codes or '—'} |"
        )
    lines += [
        "",
        "_FALSE 只认次日开盘可交易且相对当日市场中位 ≥+2pp；"
        "UNKNOWN/坏事实只能令裁决降级，不能伪装成正确弃权。_",
    ]
    return lines


def main() -> int:
    ledger = roll()
    target = Path("reports/learning/abstention_ledger.md")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("\n".join(render(ledger)) + "\n", encoding="utf-8")
    print(f"[abstention_ledger] {len(ledger)} 日 → {target}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
