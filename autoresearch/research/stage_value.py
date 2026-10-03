#!/usr/bin/env python3
"""F4/F8:L3/L4 阶段增量仪器 —— 同日同人口、日等权、主尺与敏感尺并列、缺结果不填零。

实施计划:`docs/superpowers/plans/2026-09-06-research-methods-and-stage-value.md` Task F4/F8。

**输入是 `scan/populations.py` 已产的人口表**(`$RPT/scan/_ledger/populations/<run_key>.parquet`:
一行一候选,正交布尔 `in_l2 / pass1_kept / l3_judged / is_finalist / l4_rejected / e6_candidate /
is_buy`,`UNKNOWN ≠ False`,每把尺带 `status_<ruler>`),**不另建审计表**。阶段对:

    菜单→L3  = in_l2        vs is_finalist
    L3→L4    = is_finalist  vs is_finalist ∧ ¬l4_rejected
    L4→E6    = e6_candidate vs is_buy

每日在共同人口上比等权收益(`refined − baseline`);任一侧有人口但结果未成熟 → 该日
`INCOMPLETE_OUTCOMES`,delta 缺失**不是零**;任一侧空集 → `EMPTY_SELECTION`——零 BUY 不是
策略失败,「现金收益 = 0」的比较需要单独定义资金分配规则,这里不替它定。旗为 UNKNOWN
(pd.NA)的行进 coverage,不当 False。

区间用 A 包的 `day_equal_bootstrap`(COMPLETE 日的 delta 序列);块长敏感性走 `robustness`;
成熟与否走 `maturity_verdict`。**主尺只服务决策类结论**;`sensitivity_rulers` 与主尺并列输出,
读数章节禁止用敏感尺给决策类结论背书(Q-F)。

    uv run --no-sync python -m autoresearch.research.stage_value \\
        --spec <已冻结方案 spec.json> --population <populations parquet 或 csv> [--population …]
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from collections import Counter
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

from autoresearch.common import ruler as _ruler, workspace as ws
from autoresearch.common.stats import day_equal_bootstrap, maturity_verdict
from autoresearch.contracts.research_experiment import validate_spec
from autoresearch.research import experiment_io as eio
from autoresearch.research.registration import (
    file_manifest,
    parse_maturity_policy,
    registered_date_slice,
    registered_test_range,
    verify_code_provenance,
    verify_engine,
    verify_manifest,
    verify_modes,
)
from autoresearch.research.robustness import block_sensitivity

STAGE_PAIRS: dict[str, dict] = {
    "menu_to_l3": {"baseline": "in_l2", "refined": "is_finalist"},
    "l3_to_l4": {"baseline": "is_finalist", "refined": "is_finalist", "refined_not": "l4_rejected"},
    "l4_to_e6": {"baseline": "e6_candidate", "refined": "is_buy"},
}
COMPLETE, INCOMPLETE, EMPTY = "COMPLETE", "INCOMPLETE_OUTCOMES", "EMPTY_SELECTION"
EVIDENCE_MODES = {"EOD_PROXY", "RETRO_REPLAY"}
COST_MODELS = {"none"}
BEHAVIOR_ROOTS = (
    "autoresearch/research/stage_value.py",
    "autoresearch/research/registration.py",
    "autoresearch/research/robustness.py",
    "autoresearch/common/stats.py",
    "autoresearch/contracts/research_experiment.py",
)


def paired_daily_selection(frame: pd.DataFrame, *, allow_unknown_selection: bool = False) -> pd.DataFrame:
    """一行一候选(`date, code, baseline, refined, value`)→ 逐日共同人口的等权 delta。"""
    required = {"date", "code", "baseline", "refined", "value"}
    if required - set(frame.columns) or frame.duplicated(["date", "code"]).any():
        raise ValueError("missing columns or duplicate candidate")
    if frame[["date", "code"]].isna().any().any():
        raise ValueError("candidate identity required")
    for col in ("baseline", "refined"):
        if ((not allow_unknown_selection and frame[col].isna().any())
                or not frame[col].dropna().map(lambda x: isinstance(x, (bool, np.bool_))).all()):
            raise ValueError("selection must be explicit boolean")
    rows = []
    for day, group in frame.groupby("date", sort=True):
        values = pd.to_numeric(group["value"], errors="coerce")
        if np.isinf(values.to_numpy(dtype=float)).any():
            raise ValueError("infinite return")
        unknown = group[['baseline', 'refined']].isna().any(axis=1)
        if unknown.any():
            rows.append({'date': day, 'baseline': None, 'refined': None, 'delta': None,
                         'status': 'UNKNOWN_SELECTION', 'n_baseline': int(group['baseline'].eq(True).sum()),
                         'n_refined': int(group['refined'].eq(True).sum()),
                         'missing_outcomes': int(values.isna().sum()), 'unknown_selections': int(unknown.sum())})
            continue
        baseline, refined = group["baseline"].astype(bool), group["refined"].astype(bool)
        needed = baseline | refined
        missing = int(values[needed].isna().sum())
        status = (INCOMPLETE if missing
                  else EMPTY if not baseline.any() or not refined.any()
                  else COMPLETE)
        left = float(values[baseline].mean()) if status == COMPLETE else None
        right = float(values[refined].mean()) if status == COMPLETE else None
        rows.append({"date": day, "baseline": left, "refined": right,
                     "delta": right - left if status == COMPLETE else None,
                     "status": status, "n_baseline": int(baseline.sum()),
                     "n_refined": int(refined.sum()), "missing_outcomes": missing})
    return pd.DataFrame(rows)


def evaluate_candidates(
    frame: pd.DataFrame,
    *,
    stage: str,
    ruler: str,
    evidence_root: Path | str,
    allow_unknown_selection: bool = False,
) -> tuple[pd.DataFrame, dict]:
    """Evaluate one candidate table and bind its exact standalone operation evidence."""
    result = paired_daily_selection(frame, allow_unknown_selection=allow_unknown_selection)
    from autoresearch.trace.operation_evidence import record_operation_evidence

    evidence = record_operation_evidence(
        "research.evaluate",
        parameters={"stage": stage, "ruler": ruler},
        inputs={"research.candidates": frame.to_csv(index=False)},
        outputs={"research.evaluation": result.to_csv(index=False)},
        effects=[
            {
                "kind": "CANDIDATE_EVALUATION",
                "rows": int(len(result)),
                "stage": stage,
                "ruler": ruler,
            }
        ],
        code_paths=[Path(__file__)],
        evidence_root=evidence_root,
    )
    return result, evidence


def pairs_from_population(table: pd.DataFrame, *, stage: str, ruler: str) -> tuple[pd.DataFrame, dict]:
    """populations 表 → 某阶段对在某把尺上的 (`date, code, baseline, refined, value`) + coverage。

    旗为 pd.NA 的行**不当 False**:保留在共同人口与 coverage,整日不计配对统计。
    尺未成熟(`status_<ruler>` ≠ MATURE)的行 value 记 NaN → 该日 INCOMPLETE。
    """
    spec = STAGE_PAIRS[stage]
    cols = {spec["baseline"], spec["refined"]} | ({spec["refined_not"]} if "refined_not" in spec else set())
    missing = sorted(cols - set(table.columns))
    if missing:
        raise ValueError(f"population table lacks flags {missing}")
    if ruler not in table.columns:
        raise ValueError(f"population table lacks ruler column {ruler!r}")
    date_col = "analysis_date" if "analysis_date" in table.columns else "date"
    if date_col not in table.columns:
        raise ValueError("population table lacks analysis_date")
    flags = table[list(cols)]
    for column in flags:
        if not flags[column].dropna().map(lambda v: isinstance(v, (bool, np.bool_))).all():
            raise ValueError('population flags must be boolean or unknown')
    unknown = flags.isna().any(axis=1)
    baseline = table[spec['baseline']].astype('boolean')
    refined = table[spec['refined']].astype('boolean')
    if 'refined_not' in spec:
        refined = refined & ~table[spec['refined_not']].astype('boolean')
    # Even a logically false conjunction cannot hide an unobserved stage decision.
    refined = refined.mask(unknown)
    value = pd.to_numeric(table[ruler], errors='coerce')
    status_col = f'status_{ruler}'
    if status_col in table:
        value = value.where(table[status_col] == 'MATURE')
    frame = pd.DataFrame({'date': table[date_col].astype(str).values,
                          'code': table['code'].astype(str).str.zfill(6).values,
                          'baseline': baseline.array, 'refined': refined.array, 'value': value.values})
    coverage = {'stage': stage, 'ruler': ruler, 'n_rows': int(len(table)),
                'unknown_flags': int(unknown.sum()), 'n_used': int((~unknown).sum()),
                'unknown_dates': int(table.loc[unknown, date_col].nunique())}
    sector_column = next((c for c in ('sector', 'industry') if c in table.columns), None)
    sectors = Counter(str(v) for v in table[sector_column].dropna() if str(v)) if sector_column else Counter()
    sector_n = sum(sectors.values())
    coverage.update(date_count=int(table[date_col].nunique()), security_days=int(len(table)),
                    rejected_rows=int((~refined).sum()),
                    unselected_rows=int((~frame['baseline'] & ~frame['refined']).sum()),
                    industry_counts=dict(sorted(sectors.items())), industry_missing=len(table)-sector_n,
                    industry_hhi=sum((n/sector_n)**2 for n in sectors.values()) if sector_n else None)
    return frame, coverage


def summarize_daily(daily: pd.DataFrame, *, seed: int, n_boot: int,
                    min_scan_days: int = 20) -> dict:
    """COMPLETE 日的 delta:日等权区间(A 包原语)+ 块长敏感性(全报)+ 成熟判定。"""
    complete = daily[daily["status"] == COMPLETE]
    counts = daily["status"].value_counts().to_dict()
    out = {"n_days": int(len(daily)), "n_complete": int(len(complete)), "paired_date_count": int(len(complete)),
           "status_counts": {k: int(v) for k, v in counts.items()},
           "point": None, "lo": None, "hi": None, "n_boot": n_boot, "seed": seed,
           "block_sensitivity": None, "maturity": None}
    if len(complete):
        frame = pd.DataFrame({"date": complete["date"].values, "delta": complete["delta"].values})
        interval = day_equal_bootstrap(frame, "delta", n_boot=n_boot, seed=seed)
        out.update(point=interval.point, lo=interval.lo, hi=interval.hi)
        out["block_sensitivity"] = block_sensitivity(complete["delta"].to_numpy(dtype=float),
                                                    seed=seed, n_boot=n_boot)
    out["maturity"] = asdict(maturity_verdict(
        scan_days=int(len(complete)), min_scan_days=min_scan_days
    ))
    return out


def _sha256(path: Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _load_population(path: Path) -> pd.DataFrame:
    return pd.read_parquet(path) if path.suffix == ".parquet" else pd.read_csv(path, dtype={"code": str})


def _validate_registered_rows(table: pd.DataFrame, spec: dict) -> None:
    date_col = "analysis_date" if "analysis_date" in table.columns else "date"
    if date_col not in table.columns:
        raise ValueError("population table lacks analysis_date")
    dates = table[date_col].astype(str).str.strip().str.replace("-", "", regex=False)
    if not dates.str.fullmatch(r"[0-9]{8}").all():
        raise ValueError("population table contains invalid analysis dates")
    start, end = registered_test_range(spec)
    outside = ~dates.ge(start) | ~dates.lt(end)
    if outside.any():
        bad = sorted(dates[outside].unique().tolist())
        raise ValueError(f"rows outside registered test interval [{start},{end}): {bad}")


def _readout(spec: dict, stats: dict, coverage: list[dict]) -> str:
    main = spec["ruler"]
    lines = [f"# stage_value — {spec['experiment_id']}", "",
             f"> 估计对象:同日同人口等权 delta(refined − baseline),日等权区间;主尺 `{main}` 只服务决策类结论,"
             f"敏感尺 {spec['sensitivity_rulers'] or '(无)'} 并列观察、**不给决策背书**。前向/回放属性:"
             f"`{spec['evidence_mode']}`。区间跨零是「不确定」,不是「无用」也不是「等价」。", "",
             "## 选择增量", "", "| 阶段对 | 尺 | 完整日 | 点估计 | 95% CI | 成熟 |", "|---|---|---|---|---|---|"]
    for key, s in stats.items():
        stage, ruler = key.split("|")
        mat = s["maturity"]["status"] if isinstance(s["maturity"], dict) else getattr(s["maturity"], "status", "?")
        ci = f"[{s['lo']:.5f}, {s['hi']:.5f}]" if s["lo"] is not None else "—"
        pt = f"{s['point']:.5f}" if s["point"] is not None else "—"
        tag = "" if ruler == main else "(敏感尺)"
        lines.append(f"| {stage} | {ruler}{tag} | {s['n_complete']}/{s['n_days']} | {pt} | {ci} | {mat} |")
    lines += ["", "## 缺失与覆盖", "", "```json", json.dumps(coverage, ensure_ascii=False, indent=1), "```", "",
              "## 明确不支持的结论", "",
              "- 不据此改 BUY 语义、三门、权重或 prompt(F07);敏感尺读数不构成换尺依据。",
              "- 零 BUY 日是 EMPTY_SELECTION,不是策略失败;缺反事实的被否决票未被推断。",
              "- 未达成熟门的行不写「优势成立」;区间跨零写「不确定」。", ""]
    if spec['evidence_mode'] == 'RETRO_REPLAY':
        lines.append('- RETRO_REPLAY 不能排除模型记忆泄漏；文件截止正确不等于历史模型无未来信息。')
    lines.append('- 本仪器不自动推广候选；软件错误、未来信息、越界、必需证据丢失或错误发布任一非零均禁止采纳。')
    return "\n".join(lines)


def run(*, spec_path: Path, populations: list[Path], parent: Path | None = None) -> Path:
    spec = validate_spec(json.loads(Path(spec_path).read_text(encoding="utf-8")))
    verify_engine(spec)
    verify_modes(spec, evidence_modes=EVIDENCE_MODES, cost_models=COST_MODELS)
    maturity_days = parse_maturity_policy(spec["maturity_policy"])
    code_identity = verify_code_provenance(spec, BEHAVIOR_ROOTS)
    input_identity = file_manifest(populations, registered_date_slice(spec))
    input_digest = verify_manifest(spec, input_identity)
    table = pd.concat([_load_population(p) for p in populations], ignore_index=True)
    _validate_registered_rows(table, spec)
    base = Path(parent) if parent is not None else ws.reports_root() / "research" / "stage_value"
    output = eio.create_experiment_dir(base, spec["experiment_id"])
    eio.freeze_spec(output, spec)
    rulers = [spec["ruler"], *spec["sensitivity_rulers"]]
    seed, n_boot = int(spec["bootstrap"]["seed"]), int(spec["bootstrap"]["n_boot"])
    daily_frames, stats, coverage, operation_ids = [], {}, [], []
    for stage in STAGE_PAIRS:
        for ruler in rulers:
            frame, cov = pairs_from_population(table, stage=stage, ruler=ruler)
            if len(frame):
                daily, evidence = evaluate_candidates(
                    frame,
                    stage=stage,
                    ruler=ruler,
                    evidence_root=output / "_operation_evidence",
                    allow_unknown_selection=True,
                )
                operation_ids.append(evidence["operation_id"])
            else:
                daily = pd.DataFrame(
                    columns=[
                        "date",
                        "baseline",
                        "refined",
                        "delta",
                        "status",
                        "n_baseline",
                        "n_refined",
                        "missing_outcomes",
                    ]
                )
            for col in ("baseline", "refined", "delta"):     # 全 NA 的 object 列 → float,合并时 dtype 一致
                daily[col] = pd.to_numeric(daily[col], errors="coerce").astype(float)
            daily.insert(0, "ruler", ruler)
            daily.insert(0, "stage", stage)
            daily_frames.append(daily)
            stats[f"{stage}|{ruler}"] = summarize_daily(
                daily, seed=seed, n_boot=n_boot, min_scan_days=maturity_days
            )
            coverage.append(cov)
    all_daily = pd.concat(daily_frames, ignore_index=True)
    all_daily.to_csv(output / "daily_delta.csv", index=False)
    (output / "coverage.json").write_text(json.dumps(coverage, ensure_ascii=False, indent=1) + "\n",
                                          encoding="utf-8")
    (output / "statistics.json").write_text(json.dumps(stats, ensure_ascii=False, indent=1, default=str) + "\n",
                                            encoding="utf-8")
    (output / "input_manifest.json").write_text(json.dumps({
        "declared_sha256": spec["input_manifest_hash"], "observed_sha256": input_digest,
        "manifest": input_identity, "engine": spec["engine"],
        "note": "hash 证明输入身份,不证明输入在当时可得"},
        ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    (output / "readout.md").write_text(_readout(spec, stats, coverage), encoding="utf-8")
    manifest = {"schema_version": 1, "experiment_id": spec["experiment_id"], "engine": spec["engine"],
                "as_of": datetime.now(timezone.utc).isoformat(), "spec_sha256": eio.spec_digest(output),
                "ruler": spec["ruler"], "sensitivity_rulers": spec["sensitivity_rulers"],
                "main_ruler_is": _ruler.MAIN_RULER,
                "operation_ids": operation_ids,
                "registration": {"input_manifest_sha256": input_digest,
                                 "code": code_identity,
                                 "test_interval": registered_date_slice(spec),
                                 "maturity_min_scan_days": maturity_days,
                                 "split_application": "TEST_ONLY_NO_TRAIN_ROWS"},
                "outputs": {name: _sha256(output / name) for name in
                            ("daily_delta.csv", "coverage.json", "statistics.json", "readout.md",
                             "input_manifest.json")}}
    (output / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=1) + "\n",
                                          encoding="utf-8")
    return output


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="阶段增量仪器:同日同人口、日等权、主尺与敏感尺并列")
    ap.add_argument("--spec", required=True, help="已冻结方案 spec.json")
    ap.add_argument("--population", action="append", required=True, help="populations parquet/csv,可多次")
    a = ap.parse_args(argv)
    try:
        out = run(spec_path=Path(a.spec), populations=[Path(p) for p in a.population])
    except FileExistsError as exc:
        print(f"[stage_value] 落点已存在,拒绝覆盖(换 experiment_id):{exc}", file=sys.stderr)
        return 2
    print(f"[stage_value] → {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
