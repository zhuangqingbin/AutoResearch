"""主评判尺单点解析函数 —— C1 修复(final-review 2026-08-08)。

`entry_flag_for`/`entry_tradable` 是「资格过滤该读哪个入场旗」的唯一出处,消费点不得
各自写三元表达式。这里锁三件事:①换尺后选对腿(gap_c1_o2 → buyable_c1,其余 → buyable);
②`<NA>` 显式折 `default`(默认 True,与 `ruler_compare.gap_frame` 同一选择),不是静默 False;
③三种 dtype(native bool / 可空 boolean / CSV 往返 object 字符串)行为一致。
"""
from __future__ import annotations

import pandas as pd
import pytest

from autoresearch.common import ruler

# ───────────────────────── entry_flag_for ─────────────────────────


def test_entry_flag_for_default_follows_main_ruler():
    assert ruler.MAIN_RULER == "gap_c1_o2"          # 前提断言:换尺已 flip(护栏,防未来回滚静默漂移语义)
    assert ruler.entry_flag_for() == "buyable_c1"


def test_entry_flag_for_explicit_gap_ruler():
    assert ruler.entry_flag_for("gap_c1_o2") == ruler.ENTRY_FLAG == "buyable_c1"


def test_entry_flag_for_legacy_ruler_falls_back_to_buyable():
    assert ruler.entry_flag_for("fwd_2_oc") == "buyable"


@pytest.mark.parametrize("horizon", ["fwd_1_oo", "fwd_5_oc", "fwd_10_oc", "hi_2_oc"])
def test_entry_flag_for_other_o1_based_horizons_use_legacy_flag(horizon):
    """fwd_5_oc/fwd_10_oc 等参考 horizon 入场腿仍是 D+1 开盘(o1),不是 gap_c1_o2 的 c1。"""
    assert ruler.entry_flag_for(horizon) == "buyable"


# ───────────────────────── entry_tradable:列缺席 ─────────────────────────


def test_entry_tradable_missing_column_defaults_true():
    df = pd.DataFrame({"code": ["000001", "000002"]})
    out = ruler.entry_tradable(df)
    assert out.tolist() == [True, True]
    assert out.dtype == bool


def test_entry_tradable_missing_column_respects_default_false():
    df = pd.DataFrame({"code": ["000001"]})
    out = ruler.entry_tradable(df, default=False)
    assert out.tolist() == [False]


# ───────────────────────── entry_tradable:native bool dtype ─────────────────────────


def test_entry_tradable_native_bool_dtype_reads_through():
    df = pd.DataFrame({"buyable_c1": [True, False, True]})
    out = ruler.entry_tradable(df)
    assert out.tolist() == [True, False, True]


def test_entry_tradable_respects_ruler_name_override_reads_legacy_column():
    """回滚杆场景:ruler_name="fwd_2_oc" 时读 "buyable",哪怕当前 MAIN_RULER 仍是 gap_c1_o2。"""
    df = pd.DataFrame({"buyable": [True, False], "buyable_c1": [False, True]})
    out = ruler.entry_tradable(df, ruler_name="fwd_2_oc")
    assert out.tolist() == [True, False]


# ───────────────────────── entry_tradable:可空 boolean dtype 的 <NA> ─────────────────────────


def test_entry_tradable_nullable_boolean_na_defaults_true():
    """镜像 ruler_compare.gap_frame 的 buyable_c1.fillna(True) 选择(deferred #8)。"""
    s = pd.array([True, False, pd.NA], dtype="boolean")
    df = pd.DataFrame({"buyable_c1": s})
    out = ruler.entry_tradable(df)
    assert out.tolist() == [True, False, True]
    assert out.dtype == bool


def test_entry_tradable_nullable_boolean_na_respects_default_false():
    s = pd.array([True, False, pd.NA], dtype="boolean")
    df = pd.DataFrame({"buyable_c1": s})
    out = ruler.entry_tradable(df, default=False)
    assert out.tolist() == [True, False, False]


# ───────────────────────── entry_tradable:CSV 往返后的 object dtype ─────────────────────────


def test_entry_tradable_csv_roundtrip_object_dtype(tmp_path):
    """真实场景:attribution.csv 写盘再读回——含 NaN 的布尔列变成 object dtype 字符串。"""
    s = pd.array([True, False, pd.NA], dtype="boolean")
    df = pd.DataFrame({"code": ["000001", "000002", "000003"], "buyable_c1": s})
    p = tmp_path / "attribution.csv"
    df.to_csv(p, index=False)
    back = pd.read_csv(p, dtype={"code": str})
    assert back["buyable_c1"].dtype == object          # 往返后确实退化成 object(锁住场景前提)
    out = ruler.entry_tradable(back)
    assert out.tolist() == [True, False, True]


# ───────────────────────── C1 的核心场景:老旗放行、新旗拦下 ─────────────────────────


def test_entry_tradable_c1_regression_old_buyable_true_new_buyable_c1_false():
    """review 描述的确切场景:盘中开板、尾盘封死——旧 buyable=True,新 buyable_c1=False,
    换尺后必须被挡在资格外(C1 修复前:10 个消费点仍读旧列,这行会被误判为可交易)。
    """
    df = pd.DataFrame({"buyable": [True], "buyable_c1": [False]})
    out = ruler.entry_tradable(df)
    assert out.tolist() == [False]
