"""shrink() 收缩估计原语 + n_tag/shrink_config helpers。合成,无网络。

spec: docs/specs/2026-07-12-selflearning-optimization-brainstorm.md §4 P0-3(C9-C12)。
双轨语义:裁决门槛仍用硬 n;本模块只服务"注入锚"(喂 LLM 读的数字)。n<3 绝对禁注。
"""
from __future__ import annotations

from autoresearch.learning.shrink import (
    DEFAULT_K,
    MIN_N_INJECT,
    n_tag,
    shrink,
    shrink_config,
)

# ───────────────────────── shrink() 公式本体 ─────────────────────────


def test_shrink_formula_matches_weighted_average():
    # p̂=(n·p+k·p_g)/(n+k):n=10,p=0.5,k=15,p_g=0.2 → (5+3)/25=0.32
    assert abs(shrink(0.5, 10, 0.2, k=15) - 0.32) < 1e-9


def test_shrink_pulls_toward_global_more_when_n_small():
    far = shrink(1.0, 1, 0.1, k=15)       # n=1:几乎全拉向全局
    near = shrink(1.0, 1000, 0.1, k=15)   # n=1000:k=15 相形见绌,几乎不拉
    assert far < near
    assert abs(near - 1.0) < 0.05
    assert far < 0.2


def test_shrink_converges_to_raw_as_n_grows():
    val = shrink(0.7, 100_000, 0.1, k=15)
    assert abs(val - 0.7) < 1e-3


def test_shrink_symmetric_when_bucket_equals_global():
    # 桶=全局 时,收缩不改变数值(单一同质桶场景,如全项目只有一条 lane 有数据)
    assert abs(shrink(0.42, 7, 0.42, k=15) - 0.42) < 1e-9


# ───────────────────────── 退化边界 ─────────────────────────


def test_shrink_zero_n_bucket_falls_back_to_global():
    assert shrink(None, 0, 0.42, k=15) == 0.42
    assert shrink(0.9, 0, 0.42, k=15) == 0.42     # n=0 桶即便 p_bucket 有值也不可信,退化全局


def test_shrink_missing_global_falls_back_to_bucket():
    assert shrink(0.6, 5, None, k=15) == 0.6


def test_shrink_both_missing_is_none():
    assert shrink(None, 0, None, k=15) is None


def test_shrink_k_zero_disables_shrinkage():
    assert shrink(0.6, 5, 0.2, k=0) == 0.6
    assert shrink(None, 0, 0.2, k=0) == 0.2      # k=0 仍退化(无桶数据没有"原始值"可用)


# ───────────────────────── n_tag:四消费点共享的注入格式 ─────────────────────────


def test_n_tag_marks_thin_below_threshold():
    assert n_tag(3, thin_n=10) == "(n=3⚠)"
    assert n_tag(10, thin_n=10) == "(n=10)"
    assert n_tag(None, thin_n=5) == "(n=0⚠)"


def test_min_n_inject_floor_is_three():
    assert MIN_N_INJECT == 3


# ───────────────────────── shrink_config:learning.{shrink,shrink_k} 读取 ─────────────────────────


def test_shrink_config_defaults_when_block_absent():
    assert shrink_config({}) == (True, DEFAULT_K)


def test_shrink_config_reads_learning_block():
    assert shrink_config({"learning": {"shrink": False, "shrink_k": 20}}) == (False, 20.0)


def test_shrink_config_partial_block_defaults_missing_half():
    assert shrink_config({"learning": {"shrink_k": 8}}) == (True, 8.0)


def test_shrink_config_none_arg_reads_real_load_user_config(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)   # 无 scan_config.json → load_user_config() 返回 {} → 默认基线
    assert shrink_config(None) == (True, DEFAULT_K)
