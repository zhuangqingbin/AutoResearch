"""L3 pass1 确定性分诊(200→~60)+ 影子账本 `_l3_pass1_cut.csv` + prepare_l3_table 两遍法接线。

design: docs/plans/2026-07-12-l3-merge-plan.md Task 1(L3 合并波)。NO network,纯确定性。

pass1 规则(按序 union、去重;结果按 gbdt_score/composite 降序,超 target 截尾):
  ① pinned 全入(`pinned` 布尔列;缺列退化用 `recall_channels=="pinned"` 哨兵值)
  ② 多路共振全入(`n_channels>=3`,真实 L2 列)
  ③ healthy lane 全入(`recall_channels` 含 "healthy" 通道 token,集合 membership)
  ④ 剩余名额按各召回通道内名次(gbdt_score 降序)轮询填满到 target(K 自适应)
cut = df − kept,`prepare_l3_table(two_pass=True)` 落它到 `_l3_pass1_cut.csv`(影子账本,
供 attribution 证明分诊没吃赢家)。two_pass=False = 回滚杆,现行为逐字节不变。
"""
from __future__ import annotations

import pandas as pd

from autoresearch.scan.agents.l3_select import (
    l3_table_md,
    prepare_l3_table,
    triage_l2_for_l3,
)


def _row(code, name="甲", composite=80.0, gbdt_score=None, n_channels=1,
        recall_channels="composite", pinned=None, industry="电子"):
    r = {"code": code, "name": name, "industry": industry, "composite": composite,
        "gbdt_score": composite if gbdt_score is None else gbdt_score,
        "n_channels": n_channels, "recall_channels": recall_channels}
    if pinned is not None:
        r["pinned"] = pinned
    return r


# ───────────────────────── ①②③ 全入规则 ─────────────────────────


def test_triage_keeps_pinned_bool_column_even_if_weak():
    """pinned=True 全入,即便分最低、target 很小挤不进轮询也留。"""
    rows = [_row("000099", composite=1.0, gbdt_score=1.0, n_channels=1,
                recall_channels="momentum", pinned=True)]
    rows += [_row(f"{i:06d}", composite=99.0 - i, gbdt_score=99.0 - i, n_channels=1,
                  recall_channels="momentum") for i in range(1, 6)]
    df = pd.DataFrame(rows)
    kept, cut = triage_l2_for_l3(df, target=1)
    assert "000099" in set(kept["code"])
    assert "000099" not in set(cut["code"])


def test_triage_keeps_pinned_via_recall_channels_sentinel_fallback():
    """无 `pinned` 列(旧 staging/未启用保送)→ 退化用 recall_channels=='pinned' 哨兵值
    (`universe._inject_pinned_l1` 对新注入行的确定性 marker)。"""
    rows = [_row("000099", composite=1.0, gbdt_score=1.0, n_channels=0, recall_channels="pinned")]
    rows += [_row(f"{i:06d}", composite=99.0 - i, gbdt_score=99.0 - i, n_channels=1,
                  recall_channels="momentum") for i in range(1, 6)]
    df = pd.DataFrame(rows)
    assert "pinned" not in df.columns
    kept, _ = triage_l2_for_l3(df, target=1)
    assert "000099" in set(kept["code"])


def test_triage_keeps_resonance_rows_n_channels_ge_3():
    rows = [_row("000099", composite=1.0, gbdt_score=1.0, n_channels=3,
                recall_channels="momentum|value|growth")]
    rows += [_row(f"{i:06d}", composite=99.0 - i, gbdt_score=99.0 - i, n_channels=1,
                  recall_channels="momentum") for i in range(1, 6)]
    df = pd.DataFrame(rows)
    kept, _ = triage_l2_for_l3(df, target=1)
    assert "000099" in set(kept["code"])


def test_triage_keeps_healthy_lane_rows():
    rows = [_row("000099", composite=1.0, gbdt_score=1.0, n_channels=1, recall_channels="healthy")]
    rows += [_row(f"{i:06d}", composite=99.0 - i, gbdt_score=99.0 - i, n_channels=1,
                  recall_channels="momentum") for i in range(1, 6)]
    df = pd.DataFrame(rows)
    kept, _ = triage_l2_for_l3(df, target=1)
    assert "000099" in set(kept["code"])


def test_triage_healthy_membership_not_just_first_channel():
    """healthy 判据是集合 membership,不是 `_row_lane` 的"仅首通道"渲染判据——
    "momentum|healthy"(healthy 不在首位)也该全入。"""
    rows = [_row("000099", composite=1.0, gbdt_score=1.0, n_channels=1,
                recall_channels="momentum|healthy")]
    rows += [_row(f"{i:06d}", composite=99.0 - i, gbdt_score=99.0 - i, n_channels=1,
                  recall_channels="momentum") for i in range(1, 6)]
    df = pd.DataFrame(rows)
    kept, _ = triage_l2_for_l3(df, target=1)
    assert "000099" in set(kept["code"])


# ───────────────────────── 缺列兜底(不崩,该规则贡献 0 行) ─────────────────────────


def test_triage_missing_n_channels_column_skips_resonance_rule_gracefully():
    rows = [_row(f"{i:06d}", composite=99.0 - i, gbdt_score=99.0 - i, recall_channels="momentum")
           for i in range(6)]
    df = pd.DataFrame(rows).drop(columns=["n_channels"])
    kept, cut = triage_l2_for_l3(df, target=3)          # 不该 KeyError
    assert len(kept) == 3 and len(cut) == 3
    assert set(kept["code"]) == {"000000", "000001", "000002"}   # 剩纯按 gbdt_score 轮询/收尾


def test_triage_missing_recall_channels_column_skips_rules_1_and_3_gracefully():
    rows = [_row(f"{i:06d}", composite=99.0 - i, gbdt_score=99.0 - i, n_channels=1)
           for i in range(6)]
    df = pd.DataFrame(rows).drop(columns=["recall_channels"])
    kept, cut = triage_l2_for_l3(df, target=3)          # 不该 KeyError
    assert len(kept) == 3 and len(cut) == 3


# ───────────────────────── target 截断(超额部分按 gbdt/composite 分) ─────────────────────────


def test_triage_target_truncates_mandatory_overflow_by_score():
    """8 只全 healthy(规则③全入)但 target=5 → 按 gbdt_score 降序留 top5,其余入 cut。"""
    rows = [_row(f"{i:06d}", composite=float(i), gbdt_score=float(i), n_channels=1,
                recall_channels="healthy") for i in range(8)]
    df = pd.DataFrame(rows)
    kept, cut = triage_l2_for_l3(df, target=5)
    assert len(kept) == 5 and len(cut) == 3
    assert set(kept["code"]) == {"000003", "000004", "000005", "000006", "000007"}


# ───────────────────────── kept/cut 无遗漏无重叠(精确划分 df) ─────────────────────────


def test_triage_kept_and_cut_partition_df_exactly():
    rows = ([_row("p00001", composite=1.0, gbdt_score=1.0, pinned=True)]
           + [_row(f"h{i:05d}", composite=float(i), gbdt_score=float(i), recall_channels="healthy")
              for i in range(5)]
           + [_row(f"m{i:05d}", composite=float(i) + 50, gbdt_score=float(i) + 50,
                    recall_channels="momentum") for i in range(20)]
           + [_row(f"v{i:05d}", composite=float(i) + 30, gbdt_score=float(i) + 30,
                    recall_channels="value") for i in range(20)])
    df = pd.DataFrame(rows)
    kept, cut = triage_l2_for_l3(df, target=15)
    kept_codes, cut_codes = set(kept["code"]), set(cut["code"])
    assert not (kept_codes & cut_codes)
    assert kept_codes | cut_codes == set(df["code"])
    assert len(kept) + len(cut) == len(df)
    assert len(kept) == 15


def test_triage_preserves_all_original_columns():
    rows = [_row(f"{i:06d}", composite=float(i), gbdt_score=float(i)) for i in range(5)]
    df = pd.DataFrame(rows)
    kept, cut = triage_l2_for_l3(df, target=3)
    assert list(kept.columns) == list(df.columns)
    assert list(cut.columns) == list(df.columns)


def test_triage_target_ge_len_df_keeps_all_cut_empty():
    rows = [_row(f"{i:06d}", composite=float(i), gbdt_score=float(i)) for i in range(5)]
    df = pd.DataFrame(rows)
    kept, cut = triage_l2_for_l3(df, target=60)
    assert len(kept) == 5 and len(cut) == 0


def test_triage_empty_df_returns_empty_both():
    df = pd.DataFrame(columns=["code", "name", "composite", "gbdt_score"])
    kept, cut = triage_l2_for_l3(df, target=60)
    assert len(kept) == 0 and len(cut) == 0


# ───────────────────────── ④ 每通道轮询填满(K 自适应) ─────────────────────────


def test_triage_round_robin_pulls_from_multiple_channels_fairly():
    """全无 mandatory 命中 → 全靠④轮询;target=4、2 通道各 10 只 → 两通道都该分到名额
    (不是某一通道占满全部 target)。"""
    rows = ([_row(f"m{i:05d}", composite=100.0 - i, gbdt_score=100.0 - i, recall_channels="momentum")
            for i in range(10)]
           + [_row(f"v{i:05d}", composite=100.0 - i, gbdt_score=100.0 - i, recall_channels="value")
              for i in range(10)])
    df = pd.DataFrame(rows)
    kept, _ = triage_l2_for_l3(df, target=4)
    codes = set(kept["code"])
    assert any(c.startswith("m") for c in codes)
    assert any(c.startswith("v") for c in codes)
    assert "m00000" in codes and "v00000" in codes    # 通道内名次最高的先取


def test_triage_round_robin_picks_best_within_channel_first():
    rows = [_row("weak01", composite=1.0, gbdt_score=1.0, recall_channels="momentum"),
           _row("strong", composite=99.0, gbdt_score=99.0, recall_channels="momentum")]
    df = pd.DataFrame(rows)
    kept, cut = triage_l2_for_l3(df, target=1)
    assert set(kept["code"]) == {"strong"}
    assert set(cut["code"]) == {"weak01"}


def test_triage_dedup_code_counted_once_across_rule_and_channel_membership():
    """同票既满足②规则又同时挂在多个通道名下 → 只占一个名额(不因多规则命中被重复计数)。"""
    rows = [_row("dual00", composite=50.0, gbdt_score=50.0, n_channels=3,
                recall_channels="momentum|value|growth")]
    rows += [_row(f"o{i:05d}", composite=10.0 - i, gbdt_score=10.0 - i, recall_channels="momentum")
            for i in range(3)]
    df = pd.DataFrame(rows)
    kept, cut = triage_l2_for_l3(df, target=4)
    assert list(kept["code"]).count("dual00") == 1
    assert len(kept) == 4 and len(cut) == 0


def test_triage_backfill_sentinel_excluded_from_channel_queues_but_still_backfilled():
    """"(backfill)" 是 quota_union 补位哨兵,不是真实召回通道名,不参与④轮询分桶;
    但仍会被"填满收尾"步骤按分捡回,不会凭空遗漏。"""
    rows = [_row("bf0001", composite=95.0, gbdt_score=95.0, n_channels=0, recall_channels="(backfill)"),
           _row("m00001", composite=10.0, gbdt_score=10.0, recall_channels="momentum")]
    df = pd.DataFrame(rows)
    kept, cut = triage_l2_for_l3(df, target=2)
    assert len(kept) == 2 and len(cut) == 0


# ───────────────────────── prepare_l3_table:two_pass 接线 ─────────────────────────


def _mk_l2(root, date, rows):
    d = root / date
    d.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(rows).to_csv(d / "L2_gbdt_top200.csv", index=False)
    (d / "L3_news").mkdir(exist_ok=True)
    for r in rows:
        (d / "L3_news" / f"{r['code']}.json").write_text("[]", encoding="utf-8")
    return d


def test_prepare_two_pass_default_true_writes_cut_csv_and_header(tmp_path):
    """two_pass 缺省(None)、无 scan_config.json(conftest 已隔离)→ 默认走 True(新基线)。"""
    base = tmp_path / "context" / "scan"
    rows = [_row(f"{i:06d}", composite=90 - i, gbdt_score=90 - i) for i in range(5)]
    d = _mk_l2(base, "2026-07-09", rows)

    res = prepare_l3_table("2026-07-09", root=base, do_harvest=False)

    assert (d / "_l3_pass1_cut.csv").exists()
    text = (d / "_l3_table.md").read_text(encoding="utf-8")
    assert "pass1 分诊" in text and "_l3_pass1_cut.csv" in text
    assert res["pass1_kept"] == 5 and res["pass1_cut"] == 0     # 5 行 < pass1_target(60),全留


def test_prepare_two_pass_false_is_byte_identical_parity(tmp_path):
    """回滚杆:two_pass=False 时 `_l3_table.md` 与直接调用 l3_table_md(同参数)逐字节相同,
    不写 cut csv,返回 dict 不带 pass1_* 键(与本 task 之前实现完全一致)。"""
    base = tmp_path / "context" / "scan"
    rows = [_row(f"{i:06d}", composite=90 - i, gbdt_score=90 - i) for i in range(5)]
    d = _mk_l2(base, "2026-07-09", rows)

    res = prepare_l3_table("2026-07-09", root=base, do_harvest=False, two_pass=False)

    expected = l3_table_md("2026-07-09", root=base, delta=True, dist_flag=True, reg_flag=True,
                          cat_flag=True, sector_terrain=True, misread_flag=True,
                          pinned_flag=True, pinned_path=None, lane_blocks=True)
    actual = (d / "_l3_table.md").read_text(encoding="utf-8")
    assert actual == expected
    assert not (d / "_l3_pass1_cut.csv").exists()
    assert set(res.keys()) == {"codes", "table_bytes"}


def test_prepare_two_pass_false_never_touches_user_config(tmp_path, monkeypatch):
    """显式 two_pass=False 是纯净回滚杆:不该调 load_user_config(哪怕它会炸也不该报错)。"""
    base = tmp_path / "context" / "scan"
    rows = [_row(f"{i:06d}", composite=90 - i, gbdt_score=90 - i) for i in range(3)]
    _mk_l2(base, "2026-07-09", rows)

    def _boom(*a, **k):
        raise AssertionError("two_pass=False 不该调用 load_user_config")
    monkeypatch.setattr("autoresearch.scan.user_config.load_user_config", _boom)

    prepare_l3_table("2026-07-09", root=base, do_harvest=False, two_pass=False)   # 不该抛


def test_prepare_two_pass_none_reads_config_pass1_target_override(tmp_path, monkeypatch):
    """two_pass=None(缺省)读 `load_user_config().get("l3")`;`pass1_target` 覆盖生效 →
    真触发截断分诊(镜像 test_frame_json_echo_reflects_real_config 的 DEFAULT_PATH 显式冲销手法)。"""
    cfg_dir = tmp_path / ".claude" / "skills" / "scan-market"
    cfg_dir.mkdir(parents=True)
    (cfg_dir / "scan_config.jsonc").write_text('{"l3": {"pass1_target": 2}}', encoding="utf-8")
    monkeypatch.setattr("autoresearch.scan.user_config.DEFAULT_PATH", cfg_dir / "scan_config.jsonc")

    base = tmp_path / "context" / "scan"
    rows = [_row(f"{i:06d}", composite=90 - i, gbdt_score=90 - i, recall_channels="momentum")
           for i in range(5)]
    d = _mk_l2(base, "2026-07-09", rows)

    res = prepare_l3_table("2026-07-09", root=base, do_harvest=False)

    assert res["pass1_kept"] == 2 and res["pass1_cut"] == 3
    cut_csv = pd.read_csv(d / "_l3_pass1_cut.csv", dtype={"code": str})
    assert len(cut_csv) == 3


def test_prepare_two_pass_explicit_true_overrides_config_false(tmp_path, monkeypatch):
    """显式参数优先于配置文件(镜像项目里"CLI 显式 flag > 本文件"的既有优先级惯例)。"""
    cfg_dir = tmp_path / ".claude" / "skills" / "scan-market"
    cfg_dir.mkdir(parents=True)
    (cfg_dir / "scan_config.jsonc").write_text('{"l3": {"two_pass": false}}', encoding="utf-8")
    monkeypatch.setattr("autoresearch.scan.user_config.DEFAULT_PATH", cfg_dir / "scan_config.jsonc")

    base = tmp_path / "context" / "scan"
    rows = [_row(f"{i:06d}", composite=90 - i, gbdt_score=90 - i) for i in range(3)]
    d = _mk_l2(base, "2026-07-09", rows)

    prepare_l3_table("2026-07-09", root=base, do_harvest=False, two_pass=True)
    assert (d / "_l3_pass1_cut.csv").exists()
