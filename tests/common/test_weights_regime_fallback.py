"""regime 权重回落的**可见性** —— 2026-08-04 修。

治的病:`_load_weights(regime="risk_off")` 在文件没有 regimes 块时静默回落 flat,
而 `pick_weights` 照样返回 `"risk_off"` 这个 label。于是 `weights_used.json`/报告/replay
全都记成「已按 risk_off 加权」,而实际用的是 flat —— **有降级能力,没有「我降级了」的
传达能力**(本仓最贵的那一类病)。

08-04 实测:生产 weights.json 的 `regimes` 是空的(最后一次校准跑的是 `calibrate` 而非
`calibrate-regimes`),`regime_aware=True` 已经静默回落了不知道多久。

纪律:**权重数值必须逐值不变**(仍是 flat),变的只有「说清楚」。
"""
from __future__ import annotations

import json

import pandas as pd
import pytest

from autoresearch.common import scoring
from autoresearch.data import contracts


def _write(tmp_path, *, regimes=None):
    payload = {"meta": {"source": "test"},
               "weights": {"__global__": {"momentum": -0.04, "value": 0.03}}}
    if regimes is not None:
        payload["regimes"] = regimes
    path = tmp_path / "weights.json"
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return str(path)


@pytest.fixture(autouse=True)
def _clean_degradations():
    contracts.clear_degradations()
    yield
    contracts.clear_degradations()


# ── 数值 parity:修的是可见性,不是权重 ──────────────────────────


def test_fallback_weights_are_byte_identical_to_flat(tmp_path):
    path = _write(tmp_path, regimes={})
    flat = scoring._load_weights(path)
    fell_back = scoring._load_weights(path, regime="risk_off")
    assert fell_back["weights"] == flat["weights"]


def test_regime_none_path_is_untouched(tmp_path):
    """regime=None 是 parity 锚 —— 连 meta 都不该多出键。"""
    path = _write(tmp_path, regimes={})
    result = scoring._load_weights(path, regime=None)
    assert "regime_requested" not in result["meta"]
    assert "regime_applied" not in result["meta"]


def test_missing_file_still_falls_back_to_prior(tmp_path):
    result = scoring._load_weights(str(tmp_path / "nope.json"), regime="trend")
    assert result is scoring._PRIOR_WEIGHTS


# ── 可见性:要的和给的分开写 ─────────────────────────────────


def test_fallback_separates_requested_from_applied(tmp_path):
    path = _write(tmp_path, regimes={})
    meta = scoring._load_weights(path, regime="risk_off")["meta"]
    assert meta["regime_requested"] == "risk_off"
    assert meta["regime_applied"] is None          # ← 这一条是整个修复的核心
    assert meta["regime_fallback"] == "flat"


def test_fallback_lists_what_regimes_do_exist(tmp_path):
    path = _write(tmp_path, regimes={"trend": {"weights": {"momentum": 0.1}}})
    meta = scoring._load_weights(path, regime="risk_off")["meta"]
    assert meta["regimes_present"] == ["trend"]


def test_successful_regime_marks_applied(tmp_path):
    path = _write(tmp_path, regimes={"trend": {"weights": {"momentum": 0.1}}})
    result = scoring._load_weights(path, regime="trend")
    assert result["meta"]["regime_applied"] == "trend"
    assert result["weights"] == {"momentum": 0.1}


def test_empty_weights_block_counts_as_missing(tmp_path):
    """`{"trend": {"weights": {}}}` 是空壳 —— 不能当成「有该 regime」。"""
    path = _write(tmp_path, regimes={"trend": {"weights": {}}})
    assert scoring._load_weights(path, regime="trend")["meta"]["regime_applied"] is None


# ── 记账:降级必须留痕 ───────────────────────────────────────


def test_fallback_records_a_degradation(tmp_path):
    scoring._load_weights(_write(tmp_path, regimes={}), regime="risk_off")
    records = [r for r in contracts.degradations() if r["endpoint"] == "weights_regime"]
    assert len(records) == 1
    assert records[0]["key"] == "risk_off"
    assert "calibrate-regimes" in records[0]["reasons"][0]


def test_successful_regime_records_nothing(tmp_path):
    path = _write(tmp_path, regimes={"trend": {"weights": {"momentum": 0.1}}})
    scoring._load_weights(path, regime="trend")
    assert not [r for r in contracts.degradations() if r["endpoint"] == "weights_regime"]


def test_degradation_shows_up_in_the_alert_line(tmp_path):
    scoring._load_weights(_write(tmp_path, regimes={}), regime="risk_off")
    assert "weights_regime" in contracts.render()


# ── pick_weights:label ≠ applied ────────────────────────────


def _frame():
    return pd.DataFrame({"code": [f"{i:06d}" for i in range(50)],
                         "pct_60d": [float(i) for i in range(50)],
                         "close": [10.0] * 50})


def test_label_is_returned_even_when_nothing_was_applied(tmp_path):
    """label 恒非空 —— 所以「有 label」不能当「已生效」。"""
    path = _write(tmp_path, regimes={})
    weights, label = scoring.pick_weights(_frame(), True, path=path)
    assert label is not None                        # 分类结果照常给
    assert scoring.regime_weights_applied(weights) is False   # 但没生效


def test_applied_helper_is_true_only_on_a_real_hit(tmp_path):
    path = _write(tmp_path, regimes={"trend": {"weights": {"momentum": 0.1}},
                                     "range": {"weights": {"momentum": 0.2}},
                                     "risk_off": {"weights": {"momentum": 0.3}}})
    weights, label = scoring.pick_weights(_frame(), True, path=path)
    assert label in ("trend", "range", "risk_off")
    assert scoring.regime_weights_applied(weights) is True


def test_regime_aware_off_is_not_applied(tmp_path):
    weights, label = scoring.pick_weights(_frame(), False,
                                          path=_write(tmp_path, regimes={}))
    assert label is None
    assert scoring.regime_weights_applied(weights) is False


def test_applied_helper_tolerates_garbage():
    assert scoring.regime_weights_applied({}) is False
    assert scoring.regime_weights_applied(None) is False
    assert scoring.regime_weights_applied({"meta": None}) is False


# ── 生产现状的活体断言 ───────────────────────────────────────


def test_production_weights_file_is_currently_regime_blind():
    """08-04 实测:生产 weights.json 无 regimes 块 → regime_aware 一直在静默回落。

    这条会在有人真的跑对 `calibrate-regimes` 之后变红 —— 那正是它该变红的时候
    (提醒:届时必须先过两半符号一致门,见
    docs/research/2026-08-04-momentum-phase-conditional-ic.md §3)。
    """
    from pathlib import Path

    path = Path("context/factor_lab/weights.json")
    if not path.exists():
        pytest.skip("无生产 weights.json")
    data = json.loads(path.read_text(encoding="utf-8"))
    regimes = data.get("regimes") or {}
    assert not regimes, (
        f"weights.json 现在有 regimes={sorted(regimes)} —— 若这是有意为之,"
        "请确认已过两半符号一致门(08-04 的读数是 range 侧 4/4 反号、"
        "trend 与 risk_off 时间上零重叠),并更新本断言")
