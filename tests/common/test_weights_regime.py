"""_load_weights(regime=...) 单测 —— regime-aware 权重选择。temp 文件,无网络。

覆盖 spec §A:
  - regime 给定且 regimes[regime] 存在 → 返回该 regime 的 weights
  - 缺 regimes 块 → 退 flat(**parity 锚**:与 regime=None 同)
  - regime=None → 恒 flat(即便有 regimes 块)
  - regime 不在 regimes → 退 flat
  - 缺文件 → 内置先验(老行为不破)
"""
from __future__ import annotations

import json

from autoresearch.common.scoring import _PRIOR_WEIGHTS, _load_weights

_FLAT = {"meta": {"source": "x"}, "weights": {"__global__": {"momentum": -0.03, "value": 0.01}}}
_WITH_REGIMES = {
    "meta": {"source": "x"},
    "weights": {"__global__": {"momentum": -0.03, "value": 0.01}},
    "regimes": {
        "trend": {"weights": {"__global__": {"momentum": 0.05, "value": 0.00}}},
        "risk_off": {"weights": {"__global__": {"momentum": -0.08, "value": 0.03}}},
    },
}


def _write(tmp_path, obj):
    p = tmp_path / "weights.json"
    p.write_text(json.dumps(obj), encoding="utf-8")
    return str(p)


def test_regime_selects_block(tmp_path):
    w = _load_weights(_write(tmp_path, _WITH_REGIMES), regime="trend")
    assert w["weights"]["__global__"]["momentum"] == 0.05      # trend 权重(翻正)


def test_missing_regimes_block_degrades_to_flat(tmp_path):
    path = _write(tmp_path, _FLAT)
    w_reg = _load_weights(path, regime="trend")
    w_flat = _load_weights(path, regime=None)
    assert w_reg["weights"] == w_flat["weights"]               # parity:无 regimes → 退 flat


def test_regime_none_is_always_flat(tmp_path):
    w = _load_weights(_write(tmp_path, _WITH_REGIMES), regime=None)
    assert w["weights"]["__global__"]["momentum"] == -0.03     # flat,不取 regime


def test_unknown_regime_degrades_to_flat(tmp_path):
    w = _load_weights(_write(tmp_path, _WITH_REGIMES), regime="nonesuch")
    assert w["weights"]["__global__"]["momentum"] == -0.03


def test_missing_file_returns_prior():
    w = _load_weights("does/not/exist.json", regime="trend")
    assert w == _PRIOR_WEIGHTS                                 # 老行为不破


def test_default_call_unchanged(tmp_path):
    # 无 regime 参数(旧调用)→ flat,签名向后兼容
    w = _load_weights(_write(tmp_path, _FLAT))
    assert w["weights"]["__global__"]["value"] == 0.01
