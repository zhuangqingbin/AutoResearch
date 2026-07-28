#!/usr/bin/env python3
"""Stable early-stop shadow sampling and full-review outcome ledger."""
from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

import pandas as pd

from autoresearch.agents.utils.rating import parse_rating
from autoresearch.scan.decision_read_model import (
    read_decisions,
    read_final_ratings,
)
from autoresearch.scan.run_contract import sha256_json

QUEUE_SCHEMA_VERSION = 1
MIN_SAMPLE_RATE = 0.10
MAX_SAMPLE_RATE = 0.20
DEFAULT_SAMPLE_RATE = 0.15
MIN_REASON_N = 10
_LEDGER_COLUMNS = [
    "date",
    "code",
    "stop_phase",
    "stop_reason",
    "production_rating",
    "shadow_rating",
    "fwd_2_oc",
    "market_fwd_2",
    "excess_2",
]


def sample_score(date: str, code: str) -> float:
    """Map a date/code pair to a stable [0, 1) SHA-256 score."""
    digest = hashlib.sha256(
        f"{date}:{str(code).zfill(6)}".encode("utf-8")
    ).hexdigest()
    return int(digest[:16], 16) / 2**64


def _legacy_stops(scan: Path) -> list[dict]:
    path = scan / "_early_stop.json"
    if not path.exists():
        return []
    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError("legacy early-stop fact must be an object")
    ratings = read_final_ratings(scan)
    return [
        {
            "code": str(code).zfill(6),
            "phase": str((meta or {}).get("phase", "")),
            "reason": str((meta or {}).get("reason", "其他")),
            "production_rating": ratings.get(str(code).zfill(6), "—"),
            "decision_ref": f"_early_stop.json#{str(code).zfill(6)}",
        }
        for code, meta in raw.items()
    ]


def _recorded_stops(scan: Path) -> list[dict]:
    decision_path = scan / "decision_records.json"
    if not decision_path.exists():
        return _legacy_stops(scan)
    return [
        {
            "code": code,
            "phase": decision.early_stop["phase"],
            "reason": decision.early_stop["reason"],
            "production_rating": decision.final_rating,
            "decision_ref": f"decision_records.json#{code}",
        }
        for code, decision in read_decisions(scan).items()
        if decision.early_stop is not None
    ]


def _queue_payload(
    scan: Path,
    *,
    sample_rate: float,
) -> dict:
    if not MIN_SAMPLE_RATE <= float(sample_rate) <= MAX_SAMPLE_RATE:
        raise ValueError(
            f"sample_rate must be within {MIN_SAMPLE_RATE:.2f}–"
            f"{MAX_SAMPLE_RATE:.2f}"
        )
    items = []
    for stop in sorted(_recorded_stops(scan), key=lambda row: row["code"]):
        score = sample_score(scan.name, stop["code"])
        if score >= sample_rate:
            continue
        items.append(
            {
                **stop,
                "sample_score": round(score, 12),
                "status": "PENDING",
            }
        )
    payload = {
        "schema_version": QUEUE_SCHEMA_VERSION,
        "date": scan.name,
        "sample_rate": float(sample_rate),
        "sampling": "sha256(date:code)/2^64",
        "production_effect": "NONE",
        "items": items,
    }
    payload["items_hash"] = sha256_json(items)
    return payload


def write_shadow_queue(
    scan_dir: Path | str,
    *,
    sample_rate: float = DEFAULT_SAMPLE_RATE,
) -> Path:
    """Atomically create a byte-stable shadow queue from recorded early stops."""
    scan = Path(scan_dir)
    payload = _queue_payload(scan, sample_rate=sample_rate)
    target = scan / "shadow" / "earlystop_queue.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    temp = target.with_suffix(target.suffix + ".tmp")
    temp.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    temp.replace(target)
    return target


def load_shadow_queue(scan_dir: Path | str) -> dict:
    path = Path(scan_dir) / "shadow" / "earlystop_queue.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    if (
        payload.get("schema_version") != QUEUE_SCHEMA_VERSION
        or payload.get("date") != Path(scan_dir).name
        or not isinstance(payload.get("items"), list)
        or payload.get("items_hash") != sha256_json(payload["items"])
    ):
        raise ValueError("invalid early-stop shadow queue")
    return payload


def store_shadow_card(
    scan_dir: Path | str,
    code: str,
    markdown: str,
) -> Path:
    """Store a supplied review only in the queue's shadow details directory."""
    scan = Path(scan_dir)
    code = str(code).zfill(6)
    queue = load_shadow_queue(scan)
    if code not in {item["code"] for item in queue["items"]}:
        raise ValueError(f"{code} not queued for early-stop shadow review")
    target = scan / "shadow" / "earlystop_details" / f"{code}.md"
    target.parent.mkdir(parents=True, exist_ok=True)
    temp = target.with_suffix(target.suffix + ".tmp")
    temp.write_text(str(markdown), encoding="utf-8")
    temp.replace(target)
    return target


def _as_bool(frame: pd.DataFrame, column: str, default: bool) -> pd.Series:
    if column not in frame.columns:
        return pd.Series(default, index=frame.index, dtype=bool)
    values = frame[column]
    if values.dtype == bool:
        return values.fillna(default)
    return values.astype(str).str.strip().str.lower().isin(
        {"true", "1", "yes", "是"}
    )


def build_shadow_ledger(
    scan_dir: Path | str,
    attribution: pd.DataFrame | None = None,
) -> pd.DataFrame:
    """Join completed shadow cards to production rating, stop reason, and T+2."""
    scan = Path(scan_dir)
    queue = load_shadow_queue(scan)
    if attribution is None:
        attr_path = scan / "retro" / "attribution.csv"
        attribution = (
            pd.read_csv(attr_path, dtype={"code": str})
            if attr_path.exists()
            else pd.DataFrame(columns=["code", "fwd_2_oc"])
        )
    attr = attribution.copy()
    if "code" not in attr.columns:
        raise ValueError("attribution missing code")
    attr["code"] = attr["code"].astype(str).str.zfill(6)
    attr["fwd_2_oc"] = pd.to_numeric(attr.get("fwd_2_oc"), errors="coerce")
    attr["buyable"] = _as_bool(attr, "buyable", True)
    attr["tradable"] = _as_bool(attr, "tradable", True)
    usable = attr["buyable"] & attr["tradable"] & attr["fwd_2_oc"].notna()
    market = (
        float(attr.loc[usable, "fwd_2_oc"].median())
        if usable.any()
        else None
    )
    facts = attr.set_index("code")
    rows = []
    for item in queue["items"]:
        code = item["code"]
        card_path = scan / "shadow" / "earlystop_details" / f"{code}.md"
        if not card_path.exists():
            continue
        fwd = None
        if code in facts.index:
            fact = facts.loc[code]
            if isinstance(fact, pd.DataFrame):
                fact = fact.iloc[0]
            value = pd.to_numeric(
                pd.Series([fact.get("fwd_2_oc")]),
                errors="coerce",
            ).iloc[0]
            if (
                not pd.isna(value)
                and bool(fact.get("buyable", True))
                and bool(fact.get("tradable", True))
            ):
                fwd = float(value)
        excess = None if fwd is None or market is None else fwd - market
        rows.append(
            {
                "date": scan.name,
                "code": code,
                "stop_phase": item["phase"],
                "stop_reason": item["reason"],
                "production_rating": item["production_rating"],
                "shadow_rating": parse_rating(
                    card_path.read_text(encoding="utf-8")
                ),
                "fwd_2_oc": None if fwd is None else round(fwd, 5),
                "market_fwd_2": None if market is None else round(market, 5),
                "excess_2": None if excess is None else round(excess, 5),
            }
        )
    return pd.DataFrame(rows, columns=_LEDGER_COLUMNS)


def reason_summary(rows: pd.DataFrame) -> pd.DataFrame:
    """Keep every reason bucket undecided until ten mature shadow reviews."""
    columns = [
        "stop_reason",
        "n_reviews",
        "n_mature",
        "mean_excess_2",
        "status",
    ]
    if rows is None or not len(rows):
        return pd.DataFrame(columns=columns)
    output = []
    for reason, group in rows.groupby("stop_reason"):
        mature = pd.to_numeric(group["excess_2"], errors="coerce").dropna()
        output.append(
            {
                "stop_reason": reason,
                "n_reviews": len(group),
                "n_mature": len(mature),
                "mean_excess_2": (
                    round(float(mature.mean()), 5) if len(mature) else None
                ),
                "status": "MATURE" if len(mature) >= MIN_REASON_N else "IMMATURE",
            }
        )
    return pd.DataFrame(output, columns=columns).sort_values(
        ["n_mature", "stop_reason"],
        ascending=[False, True],
    ).reset_index(drop=True)


def roll(scan_root: Path | str = "context/scan") -> pd.DataFrame:
    frames = []
    for queue_path in sorted(
        Path(scan_root).glob("*/shadow/earlystop_queue.json")
    ):
        frame = build_shadow_ledger(queue_path.parents[1])
        if len(frame):
            frames.append(frame)
    return (
        pd.concat(frames, ignore_index=True)
        if frames
        else pd.DataFrame(columns=_LEDGER_COLUMNS)
    )


def render(rows: pd.DataFrame) -> str:
    summary = reason_summary(rows)
    lines = [
        "# 早停影子深审账本",
        "",
        "_影子卡不改变正式卡、评级、交易建议或生产时延。_",
        "",
        f"- 已完成影子深审:{len(rows)}",
    ]
    if not len(summary):
        return "\n".join(lines + ["- 暂无已完成样本。"]) + "\n"
    lines += [
        "",
        "| 停因 | 深审n | 成熟n | mean excess2 | 状态 |",
        "|---|---:|---:|---:|---|",
    ]
    for row in summary.itertuples(index=False):
        mean = (
            "—"
            if row.mean_excess_2 is None or pd.isna(row.mean_excess_2)
            else f"{row.mean_excess_2:+.2%}"
        )
        lines.append(
            f"| {row.stop_reason} | {row.n_reviews} | {row.n_mature}"
            f" | {mean} | {row.status} |"
        )
    lines.append("")
    lines.append("_每个停因成熟 n<10 时一律不得修改早停规则。_")
    return "\n".join(lines) + "\n"


def main() -> int:
    args = sys.argv[1:]
    if args and args[0] == "queue" and len(args) >= 2:
        scan = Path("context/scan") / args[1]
        rate = float(args[2]) if len(args) >= 3 else DEFAULT_SAMPLE_RATE
        print(write_shadow_queue(scan, sample_rate=rate))
        return 0
    if args and args[0] == "show" and len(args) >= 3:
        scan = Path("context/scan") / args[1]
        code = str(args[2]).zfill(6)
        queue = load_shadow_queue(scan)
        item = next((row for row in queue["items"] if row["code"] == code), None)
        if item is None:
            raise SystemExit(f"{code} not queued")
        print(json.dumps(item, ensure_ascii=False, sort_keys=True))
        return 0
    rows = roll()
    output = Path("reports/learning/earlystop_shadow.md")
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(render(rows), encoding="utf-8")
    print(f"[earlystop_shadow] {len(rows)} reviews → {output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
