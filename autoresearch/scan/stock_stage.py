#!/usr/bin/env python3
"""Per-stock L3/L4 StageResult writers and CLI."""
from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

import pandas as pd

from autoresearch.scan.stage_result import (
    StageResult,
    contract_hash_for,
    load_stage_result,
    write_stage_result,
)

_CODE_RE = re.compile(r"^\d{6}$")


def _code6(value: object) -> str:
    code = str(value).strip().split(".")[0].zfill(6)
    if not _CODE_RE.fullmatch(code):
        raise ValueError(f"invalid stock code: {value!r}")
    return code


def _scalar(value: object) -> object:
    if value is None or pd.isna(value):
        return None
    return value.item() if hasattr(value, "item") else value


def _truthy(value: object) -> bool:
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "yes", "y"}


def record_l3_results(scan_dir: Path | str) -> list[Path]:
    """把 L3 全量判断逐票投影为可独立读取的 StageResult。"""
    scan = Path(scan_dir)
    judged_path = scan / "L3_judged_full.csv"
    judged = pd.read_csv(judged_path, dtype={"code": str})
    if "code" not in judged.columns:
        raise ValueError("L3_judged_full.csv missing code")

    finalists: set[str] = set()
    finalist_path = scan / "finalists.csv"
    if finalist_path.exists():
        finalist_rows = pd.read_csv(finalist_path, dtype={"code": str})
        if "code" in finalist_rows.columns:
            finalists = {
                _code6(value)
                for value in finalist_rows["code"].tolist()
            }

    paths = []
    for _, row in judged.iterrows():
        code = _code6(row["code"])
        selected = (
            code in finalists
            if finalist_path.exists()
            else _truthy(row.get("finalist"))
        )
        metrics = {
            "code": code,
            "selected": selected,
            "lane": _scalar(row.get("lane")),
            "conviction": _scalar(row.get("conviction")),
            "fragility": _scalar(row.get("fragility")),
        }
        result = StageResult.build(
            stage=f"l3_{code}",
            analysis_date=scan.name,
            status="SUCCEEDED",
            artifacts=["l3_judged", "finalists"],
            metrics=metrics,
            warnings=[],
            error=None,
            contract_hash=contract_hash_for(scan),
        )
        paths.append(write_stage_result(scan, result))
    return paths


def record_l4_result(
    scan_dir: Path | str,
    code: str,
    *,
    error: str | None = None,
    reused: bool = False,
) -> StageResult:
    """记录卡片/ensemble 当下事实；终评级仍只由 DecisionRecord 定义。"""
    scan = Path(scan_dir)
    code6 = _code6(code)
    card_path = scan / "details" / f"{code6}.md"
    card_present = card_path.is_file() and card_path.stat().st_size > 0
    text = card_path.read_text(encoding="utf-8") if card_present else ""

    source_rating = None
    early_stop = None
    ensemble = None
    provisional_rating = None
    warnings = []
    if card_present:
        from autoresearch.agents.utils.rating import parse_rating
        from autoresearch.scan.decision_finalize import (
            _apply_ensemble_fold,
            _load_ensemble,
        )
        from autoresearch.scan.l4.parsers import parse_early_stop

        source_rating = parse_rating(text)
        early_stop = parse_early_stop(text)
        ensemble = _load_ensemble(scan).get(code6)
        provisional_rating = _apply_ensemble_fold(
            source_rating,
            ensemble,
        )
        if ensemble and ensemble.get("degraded"):
            warnings.append("ensemble degraded")

    failure = error or (None if card_present else "L4 card missing")
    artifacts = ["l4_cards"] if card_present else []
    if ensemble is not None:
        artifacts.append("ensemble")
    result = StageResult.build(
        stage=f"l4_{code6}",
        analysis_date=scan.name,
        status="FAILED" if failure else "SUCCEEDED",
        artifacts=artifacts,
        metrics={
            "code": code6,
            "card_present": card_present,
            "early_stop": early_stop,
            "source_rating": source_rating,
            "provisional_rating": provisional_rating,
            "ensemble_present": ensemble is not None,
            "reused": bool(reused),
        },
        warnings=warnings,
        error=failure,
        contract_hash=contract_hash_for(scan),
    )
    path = write_stage_result(scan, result)
    return load_stage_result(path)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="stock_stage")
    sub = parser.add_subparsers(dest="command", required=True)
    l4 = sub.add_parser("l4")
    l4.add_argument("date")
    l4.add_argument("code")
    l4.add_argument("--root", default="context/scan")
    l4.add_argument("--error")
    l4.add_argument("--reused", action="store_true")
    args = parser.parse_args(argv)

    try:
        result = record_l4_result(
            Path(args.root) / args.date,
            args.code,
            error=args.error,
            reused=args.reused,
        )
        print(
            json.dumps(
                result.to_dict(),
                ensure_ascii=False,
                sort_keys=True,
            )
        )
        return 0 if result.status == "SUCCEEDED" else 1
    except Exception as exc:  # noqa: BLE001 — one structured CLI error
        print(
            json.dumps(
                {
                    "status": "INVALID",
                    "error": f"{type(exc).__name__}: {exc}",
                },
                ensure_ascii=False,
                sort_keys=True,
            )
        )
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
