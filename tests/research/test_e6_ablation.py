"""E6 召回面消融(2026-10-03 B3):离线重放已发布决策,`recall_strength` 退出排序会换掉谁。

评价协议 §1「E6 召回」:同一硬门、候选池、max_buys,仅移除召回排序面 —— 回答多路召回是否被重复
奖励(n_channels 在 L1 里已经加过一次分)。只读已发布 `_relative_buy_decision.json` 与账本标签,
不改任何生产产物。
"""
from __future__ import annotations

import json

import pandas as pd
import pytest

from autoresearch.research import e6_ablation as ab


def _cand(code, *, target, recall, evidence=0.5, risk=0.5, stance="CONDITIONAL", eligible=True,
          pinned=False, amount=0.5):
    return {"code": code, "eligible": eligible, "pinned": pinned, "in_pool": True,
            "amount_pctl": amount, "card_context": {"entry_stance": stance},
            "faces": {"target_align": target, "recall_strength": recall,
                      "evidence": evidence, "risk_safety": risk}}


def _decision(cands, buys, *, pool="finalists", tiering=True, date="2026-09-29"):
    return {"date": date, "mode": "active", "pool": pool, "tiering": tiering,
            "exclude_pinned": True, "max_buys": 1, "rule_version": "e6.v4.1",
            "candidates": cands, "buys": [{"code": c} for c in buys]}


def test_replay_reproduces_the_actual_buy_before_ablating():
    cands = [_cand("600001", target=0.6, recall=0.9), _cand("600002", target=0.7, recall=0.1)]
    got = ab.replay(_decision(cands, ["600001"]))
    assert got["replayed"] == ["600001"] and got["fidelity"] is True
    assert got["ablated"] == ["600002"]                   # 去掉召回面后换人
    assert got["changed"] is True


def test_allowed_cards_still_go_first_after_ablation():
    cands = [_cand("600001", target=0.9, recall=0.9),
             _cand("600002", target=0.1, recall=0.1, stance="ALLOWED")]
    got = ab.replay(_decision(cands, ["600002"]))
    assert got["replayed"] == got["ablated"] == ["600002"]  # A 级桶优先,与面无关


def test_pinned_and_ineligible_names_never_become_the_ablated_buy():
    cands = [_cand("600001", target=0.5, recall=0.9),
             _cand("600002", target=0.9, recall=0.0, pinned=True),
             _cand("600003", target=0.95, recall=0.0, eligible=False)]
    got = ab.replay(_decision(cands, ["600001"]))
    assert got["ablated"] == ["600001"] and got["changed"] is False


def test_composite_pool_days_are_skipped_because_recall_never_ranked_there():
    cands = [_cand("600001", target=0.6, recall=0.9)]
    assert ab.replay(_decision(cands, ["600001"], pool="composite")) is None


def _labels(tmp_path, date, gaps):
    path = tmp_path / "ledger/evaluations/outcome_labels.v2/universe" / f"{date}.parquet"
    path.parent.mkdir(parents=True)
    pd.DataFrame({"code": list(gaps), "gap_c1_o2": list(gaps.values()),
                  "status_gap_c1_o2": "MATURE", "buyable_c1": True}).to_parquet(path, index=False)
    return tmp_path / "ledger"


def test_readout_pairs_the_actual_and_ablated_buys_with_their_outcomes(tmp_path):
    run = tmp_path / "reports/scan/20260929-0929_2213/trace/staging"
    run.mkdir(parents=True)
    cands = [_cand("600001", target=0.6, recall=0.9), _cand("600002", target=0.7, recall=0.1)]
    (run / "_relative_buy_decision.json").write_text(
        json.dumps(_decision(cands, ["600001"])), encoding="utf-8")
    ledger = _labels(tmp_path, "2026-09-29", {"600001": -0.01, "600002": 0.02})

    rows = ab.readout(tmp_path / "reports/scan", ledger_root=ledger)

    assert rows == [{"run": "20260929-0929_2213", "date": "2026-09-29", "actual": "600001",
                     "ablated": "600002", "changed": True, "fidelity": True,
                     "gap_actual": -0.01, "gap_ablated": 0.02}]
    summary = ab.summarize(rows)
    assert summary["n_days"] == 1 and summary["n_changed"] == 1
    assert summary["mean_gap_diff_changed"] == pytest.approx(0.03)   # 消融 − 实际


def test_face_ic_is_read_within_each_days_eligible_candidates(tmp_path):
    cands = [_cand(f"60000{i}", target=0.1 * i, recall=1 - 0.1 * i) for i in range(8)]
    gaps = {f"60000{i}": 0.001 * i for i in range(8)}
    ledger = _labels(tmp_path, "2026-09-29", gaps)
    got = ab.face_ic(_decision(cands, ["600007"]), ledger_root=ledger)
    assert got["target_align"] == pytest.approx(1.0)
    assert got["recall_strength"] == pytest.approx(-1.0)
