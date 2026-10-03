#!/usr/bin/env python3
"""C5 离线执行评价 CLI —— 三种证据模式**分别**汇总,排他输出,缺失不填零。

实施计划:`docs/superpowers/plans/2026-09-06-execution-evaluation.md` Task C5。

    uv run --no-sync python -m autoresearch.research.execution_audit \\
        --experiment-id EXEC_20260907_01 \\
        [--snapshots <脱敏快照 CSV>] [--trades <broker trades.csv>] --policy <成本模型 JSON> \\
        [--runs-root <已发布 scan run 的根>] [--lake-daily <lake/daily>]

三种模式各自一张分母表,**永不合并**:
- EOD_PROXY:只有湖(日线);买腿可行性 = `forward_returns` 的 `buyable_c1`,收益 = 主尺
  `gap_c1_o2`。这是历史代理,不是成交。
- SNAPSHOT_SIMULATED:有 14:45 快照;入场条件 = `entry_condition`(与卡面执行线同形式);
  成交 = `closing_auction_fill`(收盘集合竞价限价单,版本 close_auction_limit_v1),成本 =
  `simulated_leg`(印花税只收卖腿)。
- OBSERVED_FILL:有券商成交;损益 = `position_pnl`(真费用、真数量;没有卖出腿就没有已实现)。

`decision_at` 从 run 的时间锚派生;没给 `--runs-root` 或 run 非 ACTIONABLE 的快照进覆盖表,
不进分母。输出目录 `<reports_root>/research/execution/<experiment_id>/` **排他创建**;已有冻结
扫描目录禁止作为落点。产物名已在 `contracts.artifacts` 登记。
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import re
import sys
from collections import defaultdict
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

import pandas as pd

from autoresearch.common import execution_math as em, ruler as _ruler, workspace as ws
from autoresearch.common.forward_returns import forward_frame
from autoresearch.data.market_panel import lake_trade_days, load_lake_pivots
from autoresearch.research import execution_import as imp, execution_ledger

EXPERIMENT_ID_RE = re.compile(r"[A-Za-z0-9_-]{1,80}")
ASSESSMENT_FIELDS: tuple[str, ...] = (
    "evidence_mode", "position_id", "code", "session", "run_id", "decision_at", "ready_quality",
    "entry_verdict", "entry_reason", "fill_rule_version", "exit_fill_rule_version",
    "entry_state", "exit_state",
    "cost_model_version", "buy_qty", "sell_qty", "remaining_qty", "gross_return",
    "realized_pnl", "unrealized_pnl", "net_pnl_cash", "net_return_realized",
    "holding_window_breached", "corporate_action_status",
)


def create_output_dir(experiment_id: str, *, parent: Path | None = None) -> Path:
    if not EXPERIMENT_ID_RE.fullmatch(str(experiment_id)):
        raise ValueError("invalid experiment id")
    base = Path(parent) if parent is not None else ws.reports_root() / "research" / "execution"
    base.mkdir(parents=True, exist_ok=True)
    output = base / experiment_id
    output.mkdir(exist_ok=False)
    return output


def _sha256(path: Path | None) -> str | None:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest() if path else None


def _dec(value) -> str | None:
    return None if value is None else format(Decimal(value), "f")


def _execution_blocks(runs_root: Path | None) -> dict[str, dict]:
    """已发布 run 目录名 → 时间锚块(`exec_anchor.read_execution`)。没给根 → 空表。"""
    if runs_root is None:
        return {}
    from autoresearch.scan.exec_anchor import read_execution
    out = {}
    for run in sorted(p for p in Path(runs_root).iterdir() if p.is_dir()):
        out[run.name] = read_execution(run)
    return out


def _eod_rows(codes_by_session: dict[str, set[str]], *, lake_daily: Path | None) -> list[dict]:
    """EOD_PROXY:湖里的主尺读数 + 买腿可行性旗。D+2 未落湖 → 收益缺失(状态不是故障)。"""
    days = lake_trade_days(lake_daily)
    rows = []
    for session, codes in sorted(codes_by_session.items()):
        d = session.replace("-", "")
        if d not in days:
            for code in sorted(codes):
                rows.append({"evidence_mode": "EOD_PROXY", "code": code, "session": session,
                             "entry_verdict": "UNKNOWN", "entry_reason": "NOT_IN_LAKE",
                             "entry_state": "UNKNOWN", "exit_state": "UNKNOWN",
                             "fill_rule_version": "eod_proxy", "gross_return": None})
            continue
        idx = days.index(d)
        if idx == 0:
            for code in sorted(codes):
                rows.append({"evidence_mode": "EOD_PROXY", "code": code, "session": session,
                             "entry_verdict": "UNKNOWN", "entry_reason": "NO_PRIOR_SESSION_IN_LAKE",
                             "entry_state": "UNKNOWN", "exit_state": "UNKNOWN",
                             "fill_rule_version": "eod_proxy", "gross_return": None})
            continue
        # 主尺的数据日 D 是买腿日(session)的前一交易日:D+1 收盘买、D+2 开盘卖
        data_day = days[idx - 1]
        window = days[max(0, idx - 2): min(len(days), idx + 12)]
        frame = forward_frame(load_lake_pivots(window, lake_daily), days, data_day)
        for code in sorted(codes):
            if frame is None or code not in frame.index:
                rows.append({"evidence_mode": "EOD_PROXY", "code": code, "session": session,
                             "entry_verdict": "UNKNOWN", "entry_reason": "CODE_NOT_IN_FRAME",
                             "entry_state": "UNKNOWN", "exit_state": "UNKNOWN",
                             "fill_rule_version": "eod_proxy", "gross_return": None})
                continue
            buyable = frame.loc[code, _ruler.ENTRY_FLAG]
            unsellable = frame.loc[code, _ruler.EXIT_FLAG]
            gap = frame.loc[code, _ruler.MAIN_RULER]
            if pd.isna(buyable):
                entry_state, reason = "UNKNOWN", "D1_MISSING"        # 未知不是「可买」
            elif not bool(buyable):
                entry_state, reason = "NO_FILL", "LIMIT_UP_SEALED"
            else:
                entry_state, reason = "FILLED", "EOD_PROXY"
            rows.append({"evidence_mode": "EOD_PROXY", "code": code, "session": session,
                         "entry_verdict": "PASS" if entry_state == "FILLED" else "UNKNOWN",
                         "entry_reason": reason, "entry_state": entry_state,
                         "exit_state": ("UNKNOWN" if pd.isna(unsellable)
                                        else "NO_FILL" if bool(unsellable) else "FILLED"),
                         "fill_rule_version": "eod_proxy",
                         "gross_return": None if pd.isna(gap) else _dec(gap)})
    return rows


def _snapshot_rows(snapshots: list[dict], *, blocks: dict[str, dict], policy: dict,
                   max_age_seconds: float, lake_daily: Path | None,
                   simulation_qty: str | None = None) -> tuple[list[dict], list[dict]]:
    """SNAPSHOT_SIMULATED:时点验证 → 收盘买腿 → D+2 开盘卖腿 → 版本化成本。"""
    rows, coverage = [], []
    days = lake_trade_days(lake_daily)
    for snap in snapshots:
        block = blocks.get(snap["run_id"])
        if block is None:
            coverage.append({"snapshot_id": snap["snapshot_id"], "reason": "RUN_BLOCK_MISSING"})
            continue
        decision_at, status = em.decision_at_from_execution_block(block)
        if decision_at is None:
            coverage.append({"snapshot_id": snap["snapshot_id"], "reason": f"RUN_{status}"})
            continue
        row = dict(snap, decision_at=decision_at.isoformat())
        verdict = em.entry_condition(row, max_age_seconds=max_age_seconds)
        session = decision_at.date().isoformat()
        d = session.replace("-", "")
        close = sealed = exit_open = unsellable = None
        exit_due = False
        if d in days:
            idx = days.index(d)
            window = days[max(0, idx - 1): min(len(days), idx + 3)]
            piv = load_lake_pivots(window, lake_daily)
            frame = forward_frame(piv, days, days[idx - 1]) if idx >= 1 else None
            if piv and snap["code"] in piv["close"].index:
                value = piv["close"].loc[snap["code"], d]
                close = None if pd.isna(value) else _dec(value)
            if frame is not None and snap["code"] in frame.index:
                entry_flag = frame.loc[snap["code"], _ruler.ENTRY_FLAG]
                sealed = None if pd.isna(entry_flag) else not bool(entry_flag)
                exit_flag = frame.loc[snap["code"], _ruler.EXIT_FLAG]
                unsellable = None if pd.isna(exit_flag) else bool(exit_flag)
            if idx + 1 < len(days):
                exit_due = True
                exit_day = days[idx + 1]
                if piv and snap["code"] in piv["open"].index and exit_day in piv["open"].columns:
                    value = piv["open"].loc[snap["code"], exit_day]
                    exit_open = None if pd.isna(value) else _dec(value)
        fill = (em.closing_auction_fill(snapshot_last=snap["last"], limit_bps=policy["slippage_bps"],
                                        close_price=close, entry_sealed=sealed)
                if verdict["verdict"] == "PASS" else
                {"state": "NOT_SUBMITTED", "reason": verdict["reason"],
                 "fill_rule_version": "close_auction_limit_v1"})
        exit_fill = (em.open_auction_fill(open_price=exit_open, exit_unsellable=unsellable,
                                          due=exit_due)
                     if fill["state"] == "FILLED" else
                     {"state": "NOT_DUE" if not exit_due else "UNKNOWN",
                      "reason": "ENTRY_NOT_FILLED", "fill_rule_version": "open_auction_v1"})
        gross = (Decimal(exit_fill["price"]) / Decimal(fill["price"]) - 1
                 if fill["state"] == exit_fill["state"] == "FILLED" else None)
        pnl = None
        if gross is not None and simulation_qty is None:
            coverage.append({"snapshot_id": snap["snapshot_id"], "reason": "MISSING_SIMULATION_QTY"})
        elif gross is not None:
            cost_args = {key: policy[key] for key in (
                "slippage_bps", "commission_rate", "minimum_commission", "tax_rate",
                "tax_sides", "transfer_fee_rate")}
            buy = em.simulated_leg(price=fill["price"], qty=simulation_qty, side="BUY", **cost_args)
            sell = em.simulated_leg(price=exit_fill["price"], qty=simulation_qty, side="SELL", **cost_args)
            buy_fees = buy["commission"] + buy["tax"] + buy["transfer_fee"]
            sell_fees = sell["commission"] + sell["tax"] + sell["transfer_fee"]
            pnl = em.position_pnl(buy_qty=simulation_qty, buy_notional=buy["notional"],
                                  buy_fees=buy_fees, sell_qty=simulation_qty,
                                  sell_notional=sell["notional"], sell_fees=sell_fees,
                                  mark_price=None, cash_distribution="0", receivable="0")
        rows.append({"evidence_mode": "SNAPSHOT_SIMULATED",
                     "position_id": f"sim:{snap['run_id']}:{snap['code']}",
                     "code": snap["code"], "session": session, "run_id": snap["run_id"],
                     "decision_at": decision_at.isoformat(), "ready_quality": block.get("ready_quality"),
                     "entry_verdict": verdict["verdict"], "entry_reason": fill.get("reason"),
                     "fill_rule_version": fill["fill_rule_version"],
                     "exit_fill_rule_version": exit_fill["fill_rule_version"],
                     "entry_state": fill["state"], "exit_state": exit_fill["state"],
                     "cost_model_version": policy["cost_model_version"],
                     "buy_qty": simulation_qty if fill["state"] == "FILLED" else None,
                     "sell_qty": simulation_qty if exit_fill["state"] == "FILLED" else None,
                     "remaining_qty": (None if simulation_qty is None or fill["state"] != "FILLED"
                                       else "0" if exit_fill["state"] == "FILLED" else simulation_qty),
                     "gross_return": _dec(gross),
                     "realized_pnl": _dec(pnl["realized_pnl"]) if pnl else None,
                     "unrealized_pnl": _dec(pnl["unrealized_pnl"]) if pnl else None,
                     "net_pnl_cash": _dec(pnl["net_pnl_cash"]) if pnl else None,
                     "net_return_realized": _dec(pnl["net_return_realized"]) if pnl else None,
                     "holding_window_breached": bool(exit_due and exit_fill["state"] != "FILLED"),
                     "corporate_action_status": "NONE"})
    return rows, coverage


def _observed_rows(fills: list[dict], *, policy: dict) -> tuple[list[dict], list[dict]]:
    """OBSERVED_FILL:逐时序 FIFO 配对,多轮交易不并成一个 position。"""
    del policy
    return execution_ledger.build_episodes(fills)


def _daily_metrics(rows: list[dict]) -> list[dict]:
    """按 (evidence_mode, session) 的日等权读数;缺失不填零,分母只数有值的。"""
    groups: dict[tuple, list[dict]] = defaultdict(list)
    for row in rows:
        groups[(row["evidence_mode"], row["session"])].append(row)
    out = []
    for (mode, session), members in sorted(groups.items()):
        gross = [Decimal(r["gross_return"]) for r in members if r.get("gross_return") is not None]
        net = [Decimal(r["net_return_realized"]) for r in members
               if r.get("net_return_realized") is not None]
        out.append({"evidence_mode": mode, "session": session, "n": len(members),
                    "n_filled": sum(r.get("entry_state") == "FILLED" for r in members),
                    "n_no_fill": sum(r.get("entry_state") == "NO_FILL" for r in members),
                    "n_unknown": sum(r.get("entry_state") == "UNKNOWN" for r in members),
                    "n_gross": len(gross), "mean_gross": _dec(sum(gross) / len(gross)) if gross else None,
                    "n_net": len(net), "mean_net_realized": _dec(sum(net) / len(net)) if net else None})
    return out


def _write_csv(path: Path, rows: list[dict], fields) -> None:
    with path.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=list(fields))
        writer.writeheader()
        for row in rows:
            writer.writerow({k: ("" if row.get(k) is None else row.get(k)) for k in fields})


def _readout(metrics: list[dict], coverage: dict) -> str:
    lines = ["# execution_audit 读数", "",
             "> 三种证据模式**分别**汇总,不合并;缺失不填零。EOD_PROXY 是历史代理不是成交;"
             "SNAPSHOT_SIMULATED 的成交是规则版本下的模拟;只有 OBSERVED_FILL 是真成交。", "",
             "| 模式 | 交易日 | n | 成交 | 未成交 | 未知 | 毛收益均值(n) | 净已实现均值(n) |",
             "|---|---|---|---|---|---|---|---|"]
    for m in metrics:
        lines.append(f"| {m['evidence_mode']} | {m['session']} | {m['n']} | {m['n_filled']} | "
                     f"{m['n_no_fill']} | {m['n_unknown']} | {m['mean_gross'] or '—'} ({m['n_gross']}) | "
                     f"{m['mean_net_realized'] or '—'} ({m['n_net']}) |")
    lines += ["", "## 覆盖", "", "```json", json.dumps(coverage, ensure_ascii=False, indent=1), "```", ""]
    return "\n".join(lines)



def study_simulated_fills(frozen, snapshots, cost):
    """snapshot_last.v1: plan-window observations only, never daily auction proxies."""
    from zoneinfo import ZoneInfo

    from autoresearch.contracts.execution import parse_aware

    legs, coverage = [], []
    args = {
        key: cost[key]
        for key in (
            "slippage_bps",
            "commission_rate",
            "minimum_commission",
            "tax_rate",
            "tax_sides",
            "transfer_fee_rate",
        )
    }
    for day in frozen["days"]:
        for candidate in day["candidates"]:
            plan = candidate["plan"]
            if plan["execution_mode"] != "SNAPSHOT_SIMULATED":
                coverage.append({"code": plan["code"], "reason": "PLAN_MODE_MISMATCH"})
                continue
            from autoresearch.research.execution_ledger import _aware_time, _calendar_window_valid

            windows = [
                _aware_time(plan.get(key))
                for key in (
                    "entry_window_start",
                    "entry_window_end",
                    "exit_window_start",
                    "exit_window_end",
                )
            ]
            if not _calendar_window_valid(plan, windows):
                coverage.append({"code": plan["code"], "reason": "INVALID_FROZEN_CALENDAR"})
                continue
            for side, prefix in (("BUY", "entry"), ("SELL", "exit")):
                low, high = [
                    parse_aware(plan[prefix + "_window_" + key]) for key in ("start", "end")
                ]
                options = [
                    dict(row, decision_at=high.isoformat())
                    for row in snapshots
                    if row["code"] == plan["code"]
                    and row["run_id"] == candidate["run_id"]
                    and low <= parse_aware(row["market_event_at"]) <= high
                ]
                options = [
                    row
                    for row in options
                    if row.get("timestamp_precision") == "second"
                    and em.snapshot_visibility(row) == "AVAILABLE"
                ]
                if len(options) != 1:
                    coverage.append(
                        {
                            "code": plan["code"],
                            "side": side,
                            "reason": "MISSING_WINDOW_SNAPSHOT"
                            if not options
                            else "AMBIGUOUS_WINDOW_SNAPSHOT",
                        }
                    )
                    break
                row = options[0]
                if (
                    row["suspended"] is not False
                    or row["last"] is None
                    or row["limit_down_price"] is None
                    or row["limit_up_price"] is None
                    or not Decimal(row["limit_down_price"])
                    < Decimal(row["last"])
                    < Decimal(row["limit_up_price"])
                ):
                    coverage.append(
                        {"code": plan["code"], "side": side, "reason": "LIQUIDITY_UNKNOWN"}
                    )
                    break
                leg = em.simulated_leg(price=row["last"], qty=candidate["qty"], side=side, **args)
                if side == "BUY" and leg["notional"] + leg["commission"] + leg["tax"] + leg[
                    "transfer_fee"
                ] > Decimal(frozen["policy"]["initial_cash"]) * Decimal(candidate["weight"]):
                    coverage.append({"code": plan["code"], "reason": "FROZEN_ALLOCATION_EXCEEDED"})
                    break
                local = high.astimezone(ZoneInfo("Asia/Shanghai"))
                legs.append(
                    {
                        "evidence_mode": "SNAPSHOT_SIMULATED",
                        "account_hash": frozen["policy"]["account_hash"],
                        "code": plan["code"],
                        "fill_id": "sim:" + row["snapshot_id"] + ":" + side,
                        "side": side,
                        "trade_date": local.date().isoformat(),
                        "trade_time": local.timetz().isoformat(),
                        "qty": candidate["qty"],
                        "amount": str(leg["notional"]),
                        "commission": str(leg["commission"]),
                        "stamp_tax": str(leg["tax"]),
                        "transfer_fee": str(leg["transfer_fee"]),
                        "other_fee": "0",
                        "source_observation_id": row["source_observation_id"],
                    }
                )
    return legs, coverage


def execution_study_readout(
    directory, *, fills, snapshots, cost, trade_evidence=None, order_status=None, import_errors=None
):
    """Consume prospective study freezes; all modes retain every analysis date."""
    from zoneinfo import ZoneInfo

    from autoresearch.research import forward_study, probability_eval as pe
    from autoresearch.research.execution_ledger import _aware_time, execution_plan_hash

    frozen = forward_study.execution_inputs(directory)
    policy = frozen["policy"]
    from autoresearch.common.atomic import canonical_json, sha256_bytes

    frozen_cost = frozen.get("cost_model")
    if frozen_cost is None or canonical_json(cost) != canonical_json(frozen_cost):
        raise ValueError("runtime parameters differ from frozen cost model")
    if policy["cost_model_version"] != cost["cost_model_version"]:
        raise ValueError("cost model differs from preregistered policy")
    if fills and not trade_evidence:
        raise ValueError("explicit authorized trades import required")
    sessions = [d["date"] for d in frozen["days"]]
    simulated, sim_coverage = study_simulated_fills(frozen, snapshots, cost)
    result = {
        "schema_version": 1,
        "protocol_sha256": frozen["protocol_sha256"],
        "cost_model_sha256": sha256_bytes(canonical_json(frozen_cost).encode()),
        "modes": {},
        "trade_import": trade_evidence,
        "simulation_coverage": sim_coverage,
        "probability_rows": [],
        "import_errors": import_errors or [],
        "limitations": [
            "EOD_PROXY does not create executions or net NAV from daily prices",
            "Human authorization is the explicit file import; no broker access or identity attestation is implied",
        ],
    }
    # Market observations only value inventory at the final minute of a session.
    marks = {}
    for row in snapshots:
        observed = _aware_time(row["market_event_at"])
        close = datetime.fromisoformat(row["session_date"] + "T15:00:00+08:00")
        if (
            observed
            and 0 <= (close - observed).total_seconds() <= 60
            and em.snapshot_visibility(dict(row, decision_at=close.isoformat())) == "AVAILABLE"
        ):
            key = (row["session_date"], row["code"])
            if key in marks:
                marks[key] = None
            else:
                marks[key] = row["last"]
    candidates = [c for d in frozen["days"] for c in d["candidates"]]
    for c in candidates:
        if Decimal(c["weight"]) > Decimal(policy["max_weight"]):
            raise ValueError("candidate exceeds frozen weight limit")
    for day in frozen["days"]:
        if (
            len(day["candidates"]) > policy["max_positions"]
            or sum((Decimal(c["weight"]) for c in day["candidates"]), Decimal(0)) > 1
        ):
            raise ValueError("candidate allocation exceeds frozen portfolio limits")
    episodes, _ = execution_ledger.build_episodes(fills)
    for mode, legs in (
        ("EOD_PROXY", []),
        ("SNAPSHOT_SIMULATED", simulated),
        ("OBSERVED_FILL", fills),
    ):
        decisions = {
            d["date"]: {
                "status": d["status"],
                "research_status": d["status"],
                "execution_status": "NOT_DUE",
            }
            for d in frozen["days"]
        }
        for c in candidates:
            plan = c["plan"]
            entry = _aware_time(plan["entry_window_start"])
            entry_day = entry.astimezone(ZoneInfo("Asia/Shanghai")).date().isoformat()
            if entry_day in decisions:
                # A plan in another evidence mode cannot create a counterfactual fill.
                decision = decisions[entry_day]
                decision["status"] = "SELECTED"
                decision.pop("execution_status", None)
                matched = [
                    r
                    for r in (order_status or [])
                    if r["session"] == entry_day
                    and r["code"] == plan["code"]
                    and r["evidence_mode"] == mode
                ]
                order = {
                    "code": plan["code"],
                    "requested_qty": c["qty"],
                    "plan_hash": execution_plan_hash(plan),
                    "state": "EXECUTION_UNKNOWN",
                    "source_observation_id": None,
                }
                if len(matched) == 1 and matched[0]["state"] == "NO_FILL":
                    order.update(
                        state="NO_FILL", source_observation_id=matched[0]["source_observation_id"]
                    )
                decision.setdefault("orders", []).append(order)
        windows = [
            {
                "code": c["plan"]["code"],
                "entry_date": _aware_time(c["plan"]["entry_window_start"])
                .astimezone(ZoneInfo("Asia/Shanghai"))
                .date()
                .isoformat(),
                "exit_date": _aware_time(c["plan"]["exit_window_end"])
                .astimezone(ZoneInfo("Asia/Shanghai"))
                .date()
                .isoformat(),
            }
            for c in candidates
        ]
        for session, declaration in decisions.items():
            sectors = {
                c["plan"]["code"]: c.get("sector")
                for source_day in frozen["days"]
                if source_day["date"] <= session
                for c in source_day["candidates"]
            }
            declaration.update(plan_windows=windows, sectors=sectors)
            if import_errors and mode == "OBSERVED_FILL":
                declaration["status"] = "DATA_UNKNOWN"
        panel_legs = [r for r in legs if r["trade_date"] in sessions]
        panel = execution_ledger.execution_day_panel(
            sessions,
            decisions=decisions,
            fills=panel_legs,
            marks=marks,
            policy=policy,
            evidence_mode=mode,
        )
        panel["outside_panel_fills"] = [
            r["fill_id"] for r in legs if r["trade_date"] not in sessions
        ]
        panel["planned_window_returns"] = []
        result["modes"][mode] = panel
    for day in frozen["days"]:
        for c in day["candidates"]:
            plan = c["plan"]
            entry_day = (
                _aware_time(plan["entry_window_start"])
                .astimezone(ZoneInfo("Asia/Shanghai"))
                .date()
                .isoformat()
            )
            eligible = [
                ep for ep in episodes if ep["code"] == plan["code"] and ep["session"] == entry_day
            ]
            ids = set(eligible[0]["fill_ids"]) if len(eligible) == 1 else set()
            rows = [r for r in fills if r["fill_id"] in ids]
            initial_values = [pos.get("market_value") for pos in policy["initial_positions"]]
            initial_capital = (
                Decimal(policy["initial_cash"])
                + sum((Decimal(value) for value in initial_values), Decimal(0))
                if all(value is not None for value in initial_values)
                else None
            )
            row = pe.execution_probability_row(
                c["declaration"],
                plan=plan,
                fills=rows,
                sizing={"qty": c["qty"], "weight": c["weight"], "initial_capital": initial_capital},
            )
            row["candidate_sha256"] = sha256_bytes(canonical_json(c).encode())
            row["candidate_artifact_sha256"] = day.get("candidate_artifact_sha256")
            row.update(analysis_date=day["date"], case_id=execution_plan_hash(plan))
            if import_errors:
                row["y"] = None
                row["missing_reasons"].append("IMPORT_ERRORS")
            result["probability_rows"].append(row)
            result["modes"]["OBSERVED_FILL"]["planned_window_returns"].append(
                {
                    "plan_hash": row["plan_hash"],
                    "net_return": row["execution"]["net_return_realized"]
                    if not row["missing_reasons"]
                    else None,
                    "missing_reasons": row["missing_reasons"],
                }
            )
    # Training observations must be separately frozen; no test labels feed the baseline.
    result["probability"] = pe.clustered_probability_metrics(
        result["probability_rows"],
        event_id=pe.PLANNED_OVERNIGHT_EVENT,
        training_rows=frozen.get("probability_training", []),
        split=frozen["protocol"]["template"]["split"],
    )
    return result
def run(*, experiment_id: str, snapshots: Path | None, trades: Path | None, policy: Path,
        runs_root: Path | None, lake_daily: Path | None, max_age_seconds: float,
        parent: Path | None = None, engine: str | None = None,
        simulation_qty: str | None = None, forward_study: Path | None = None,
        order_status: Path | None = None) -> Path:
    engine = engine or ws.detect_engine()
    cost = imp.load_policy(policy)
    snap_rows, snap_errors = imp.load_snapshots(snapshots, engine=engine) if snapshots else ([], [])
    fills, fill_errors = imp.load_trades(trades) if trades else ([], [])
    blocks = _execution_blocks(runs_root)
    study = execution_study_readout(forward_study, fills=fills, snapshots=snap_rows, cost=cost,
        trade_evidence={"path":str(trades),"sha256":_sha256(trades),"import_basis":"EXPLICIT_IMPORT"} if trades else None,
        order_status=imp.load_order_status(order_status) if order_status else [], import_errors=fill_errors) if forward_study else None
    # 先验证输入、再创建输出(中断目录保留失败 manifest,不自动覆盖重跑)
    output = create_output_dir(experiment_id, parent=parent)
    sim_rows, sim_coverage = _snapshot_rows(snap_rows, blocks=blocks, policy=cost,
                                            max_age_seconds=max_age_seconds, lake_daily=lake_daily,
                                            simulation_qty=simulation_qty)
    by_session: dict[str, set[str]] = defaultdict(set)
    for row in sim_rows:
        by_session[row["session"]].add(row["code"])
    for fill in fills:
        if fill["side"] == "BUY":
            by_session[fill["trade_date"]].add(fill["code"])
    eod_rows = _eod_rows(by_session, lake_daily=lake_daily) if by_session else []
    obs_rows, obs_coverage = _observed_rows(fills, policy=cost)
    rows = eod_rows + sim_rows + obs_rows
    metrics = _daily_metrics(rows)
    sim_excluded = [row for row in sim_coverage if row["reason"] != "MISSING_SIMULATION_QTY"]
    sim_cost_missing = [row for row in sim_coverage if row["reason"] == "MISSING_SIMULATION_QTY"]
    coverage = {
        "snapshots": {"loaded": len(snap_rows), "rejected": len(snap_errors),
                      "not_in_denominator": sim_excluded, "cost_not_computed": sim_cost_missing,
                      "errors": snap_errors},
        "trades": {"loaded": len(fills), "rejected": len(fill_errors), "errors": fill_errors,
                   "not_in_denominator": obs_coverage},
        "runs_root": str(runs_root) if runs_root else None, "n_run_blocks": len(blocks),
        "modes": {m: sum(r["evidence_mode"] == m for r in rows)
                  for m in ("EOD_PROXY", "SNAPSHOT_SIMULATED", "OBSERVED_FILL")},
    }
    _write_csv(output / "assessments.csv", rows, ASSESSMENT_FIELDS)
    _write_csv(output / "daily_metrics.csv", metrics, list(metrics[0]) if metrics else
               ["evidence_mode", "session", "n"])
    (output / "coverage.json").write_text(json.dumps(coverage, ensure_ascii=False, indent=1) + "\n",
                                          encoding="utf-8")
    (output / "readout.md").write_text(_readout(metrics, coverage), encoding="utf-8")
    if study is not None:
        (output / "execution_day_panel.json").write_text(json.dumps(study,ensure_ascii=False,indent=2,default=str)+"\n")
    manifest = {
        "schema_version": 1, "experiment_id": experiment_id, "engine": engine,
        "as_of": datetime.now(timezone.utc).isoformat(),
        "inputs": {"snapshots": _sha256(snapshots), "trades": _sha256(trades), "policy": _sha256(policy)},
        "cost_model_version": cost["cost_model_version"],
        "fill_rule_versions": sorted({version for r in rows for version in
                                      (r.get("fill_rule_version"), r.get("exit_fill_rule_version"))
                                      if version}),
        "max_age_seconds": max_age_seconds, "simulation_qty": simulation_qty,
        "ruler": _ruler.MAIN_RULER,
        "calendar_source": "lake/daily filenames",
        "outputs": {name: _sha256(output / name) for name in
                    ("assessments.csv", "daily_metrics.csv", "coverage.json", "readout.md")},
        "n_rows": len(rows), "n_coverage_excluded": (len(sim_excluded) + len(snap_errors)
                                                       + len(fill_errors) + len(obs_coverage)),
    }
    if study is not None:
        manifest["outputs"]["execution_day_panel.json"] = _sha256(output / "execution_day_panel.json")
        manifest["forward_protocol_sha256"] = study["protocol_sha256"]
        manifest["inputs"]["order_status"] = _sha256(order_status)
    (output / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=1) + "\n",
                                          encoding="utf-8")
    return output


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="离线执行评价:三模式分别汇总,排他输出,缺失不填零")
    ap.add_argument("--experiment-id", required=True)
    ap.add_argument("--snapshots", default=None, help="明确授权的脱敏快照 CSV")
    ap.add_argument("--trades", default=None, help="broker trades.csv(显式路径;不自动访问券商根)")
    ap.add_argument("--policy", required=True, help="版本化成本模型 JSON")
    ap.add_argument("--runs-root", default=None, help="已发布 scan run 根(派生 decision_at)")
    ap.add_argument("--lake-daily", default=None)
    ap.add_argument("--max-age-seconds", type=float, default=60.0,
                    help="快照新鲜度(显式研究参数,不冒充项目缺省)")
    ap.add_argument("--simulation-qty", default=None,
                    help="快照模拟的显式参考数量;缺省只报毛收益,成本后收益未知")
    ap.add_argument("--forward-study", help="冻结分析日与execution_portfolio配置的前向研究目录")
    ap.add_argument("--order-status", help="显式授权的订单状态JSON;缺记录不代表未成交")
    a = ap.parse_args(argv)
    try:
        out = run(experiment_id=a.experiment_id,
                  snapshots=Path(a.snapshots) if a.snapshots else None,
                  trades=Path(a.trades) if a.trades else None, policy=Path(a.policy),
                  runs_root=Path(a.runs_root) if a.runs_root else None,
                  lake_daily=Path(a.lake_daily) if a.lake_daily else None,
                  max_age_seconds=a.max_age_seconds, simulation_qty=a.simulation_qty,
                  forward_study=Path(a.forward_study) if a.forward_study else None,
                  order_status=Path(a.order_status) if a.order_status else None)
    except FileExistsError as exc:
        print(f"[execution_audit] 落点已存在,拒绝覆盖:{exc}", file=sys.stderr)
        return 2
    print(f"[execution_audit] → {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


def execution_funnel(rows: list[dict]) -> list[dict]:
    """Report each execution stage against its own explicitly known denominator.

    Inputs declare nullable boolean candidate/orderable/submitted/filled/
    exit_complete/window_breached independently, grouped by evidence_mode. Actual
    fills do not prove an order submission record exists; daily open prices do not
    establish fills. Unknown states never count as false or enter a denominator.
    """
    stages = ("candidate", "orderable", "submitted", "filled", "exit_complete", "window_breached")
    groups = defaultdict(list)
    for row in rows:
        if not row.get("evidence_mode"):
            raise ValueError("evidence_mode is required")
        for stage in stages:
            if row.get(stage) is not None and type(row[stage]) is not bool:
                raise ValueError(f"{stage} must be boolean or null")
        groups[row["evidence_mode"]].append(row)
    result = []
    for mode, members in sorted(groups.items()):
        counts = {}
        for stage in stages:
            values = [row[stage] for row in members if row.get(stage) is not None]
            counts[stage] = {"n": sum(values), "denominator": len(values),
                             "missing": len(members) - len(values),
                             "rate": sum(values) / len(values) if values else None}
        result.append({"evidence_mode": mode, "n": len(members), "stages": counts})
    return result
