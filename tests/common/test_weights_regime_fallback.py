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


def test_production_weights_file_regimes_are_gate_validated_only():
    """08-04 实测生产 weights.json 无 regimes 块(该状态本断言曾锁过,现已如约变红)。

    T16(2026-08-07,用户裁定后修复):`factor_lab.calibrate_regimes(require_split_half=True)`
    现为默认行为——单桶样本量够 `min_dates` 不再直接落盘,必须**另外**通过
    `split_half_regime_gate`(按日期切两半、`__global__` 组 signed IC 符号一致率 >50%)才写进
    `regimes`;未过门的桶记入 `meta.regimes_pending`,请求时回落 flat 并走
    `record_degradation("weights_regime", ...)` 记账(见 test_weights_regime.py 同族测试)。

    08-07 实测:`range` 桶过门(77.8%,9 可比因子组 7 一致)落盘;`trend`/`risk_off` 两桶因
    regime 本身按时间聚簇、各自整桶集中在单一半区,两半符号一致门"判不了"它们(不是"判定不
    过"),留在 `regimes_pending`,不落盘。本断言因此从"必须为空"改为"只能是 range 的子集"
    ——若未来出现 range 之外的桶,必须先确认它是通过同一道 `split_half_regime_gate` 落盘的
    (`meta.split_half_gate[<regime>].rate > 0.5`),而不是又退回"单桶样本够就写"的旧行为。
    """
    from pathlib import Path

    path = Path("context/factor_lab/weights.json")
    if not path.exists():
        pytest.skip("无生产 weights.json")
    data = json.loads(path.read_text(encoding="utf-8"))
    regimes = data.get("regimes") or {}
    meta = data.get("meta") or {}
    assert set(regimes) <= {"range"}, (
        f"weights.json 现在有 regimes={sorted(regimes)} —— 若这是有意为之,"
        "请确认新增的桶各自通过了 meta.split_half_gate 的两半符号一致门"
        "(rate > 0.5),而不是绕过了 require_split_half 直接写入,并更新本断言")
    if "range" in regimes:
        gate = (meta.get("split_half_gate") or {}).get("range")
        assert gate is not None and gate.get("rate", 0) > 0.5, (
            "range 桶落盘了,但 meta.split_half_gate 里查不到它的过门读数"
            "(> 0.5)——落盘与门验证脱钩了")
