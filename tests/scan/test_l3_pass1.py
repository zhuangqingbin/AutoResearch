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


def test_triage_healthy_not_mandatory_by_default():
    """2026-08-22 批 (a):healthy lane 不再全入(HEALTHY_MANDATORY=False)。target=1 且另有
    composite 路竞争者时,轮询按通道名序先给 composite,低分 healthy 行被切。
    (旧用例用 momentum 当对照:h<m,healthy 队列恰好排前,即便关掉强留也会因字母序拿到席位——
    那是个不鉴别的绿灯。)"""
    rows = [_row("000099", composite=1.0, gbdt_score=1.0, n_channels=1, recall_channels="healthy")]
    rows += [_row(f"{i:06d}", composite=99.0 - i, gbdt_score=99.0 - i, n_channels=1,
                  recall_channels="composite") for i in range(1, 6)]
    kept, cut = triage_l2_for_l3(pd.DataFrame(rows), target=1)
    assert "000099" not in set(kept["code"]) and "000099" in set(cut["code"])


def test_triage_healthy_mandatory_parity_when_flag_restored(monkeypatch):
    """回滚杆锁:HEALTHY_MANDATORY=True → healthy 全入(含集合 membership:`momentum|healthy`
    healthy 不在首位也算),记 selection_reason=lane/detail=healthy。"""
    from autoresearch.scan.l3 import triage as tr
    monkeypatch.setattr(tr, "HEALTHY_MANDATORY", True)
    rows = [_row("000099", composite=1.0, gbdt_score=1.0, n_channels=1, recall_channels="healthy"),
            _row("000098", composite=2.0, gbdt_score=2.0, n_channels=2, recall_channels="momentum|healthy")]
    rows += [_row(f"{i:06d}", composite=99.0 - i, gbdt_score=99.0 - i, n_channels=1,
                  recall_channels="composite") for i in range(1, 6)]
    kept, _ = triage_l2_for_l3(pd.DataFrame(rows), target=2)
    k = kept.set_index("code")
    assert {"000099", "000098"} <= set(k.index)
    assert (k.loc[["000099", "000098"], "selection_reason"] == "lane").all()
    assert (k.loc[["000099", "000098"], "selection_detail"] == "healthy").all()


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
    """原始列一列不少;`kept` 另加/覆盖 provenance 两列(§4.4),`cut` 不带这两列。

    M-1 修复(final-review 2026-08-08/09):旧版断言 `kept.columns == [*df.columns,
    "selection_reason", "selection_detail"]`(**追加**在末尾)只在"df 本来没有这两列"这个
    已经不真实的输入形状下成立 —— 真实 `load_l3_input(date)` 的 T16 之后产出**已经带**
    L2 口径的这两列(`merit`/`sector` 等,见 triage.py 模块头注释),所以 `kept` 是**就地
    覆盖**成 pass1 口径(列集合/顺序不变,只是值变了),不是追加新列。`cut` 侧则按 I-5
    修复显式 drop 掉这两列(它没有 pass1 层面"为什么被选中"可言,不该借尸还魂 L2 口径的
    旧值)。这里用手搭 df 显式模拟真实输入形状(预置两列 L2 口径旧值),把断言焊在这个
    形状上,不再依赖"df 从不带这两列"的过时前提。
    """
    from autoresearch.scan.l3.triage import PASS1_REASONS

    rows = [_row(f"{i:06d}", composite=float(i), gbdt_score=float(i)) for i in range(5)]
    df = pd.DataFrame(rows)
    df["selection_reason"] = "merit"           # L2 口径旧值(load_l3_input 真实会带这两列)
    df["selection_detail"] = "l2-detail"
    kept, cut = triage_l2_for_l3(df, target=3)

    assert list(kept.columns) == list(df.columns)             # 就地覆盖,不是追加新列
    assert set(kept["selection_reason"]) <= set(PASS1_REASONS)  # 值已被 pass1 口径覆盖,不留 "merit"
    assert list(cut.columns) == [c for c in df.columns
                                 if c not in ("selection_reason", "selection_detail")]


# ───────────────────────── selection_reason(§4.4 可复原性) ─────────────────────────


def test_selection_reason_pinned_beats_other_rules():
    """同时是 pinned 又多路共振 → 记 pinned(优先级顺序与"占名额"口径一致)。"""
    rows = [_row("000099", composite=1.0, gbdt_score=1.0, n_channels=3,
                 recall_channels="momentum|value|healthy", pinned=True)]
    rows += [_row(f"{i:06d}", composite=99.0 - i, gbdt_score=99.0 - i,
                  recall_channels="momentum") for i in range(1, 6)]
    kept, _ = triage_l2_for_l3(pd.DataFrame(rows), target=3)
    reason = kept.set_index("code").loc["000099", "selection_reason"]
    assert reason == "pinned"


def test_selection_reason_marks_resonance_and_healthy_distinctly():
    rows = [_row("000001", composite=1.0, gbdt_score=1.0, n_channels=3,
                 recall_channels="momentum|value|growth"),
            _row("000002", composite=2.0, gbdt_score=2.0, n_channels=1,
                 recall_channels="healthy")]
    kept, _ = triage_l2_for_l3(pd.DataFrame(rows), target=2)
    by_code = kept.set_index("code")
    assert by_code.loc["000001", "selection_reason"] == "conviction_guard"
    assert by_code.loc["000001", "selection_detail"] == "n_channels=3"
    assert by_code.loc["000002", "selection_reason"] == "lane"
    assert by_code.loc["000002", "selection_detail"] == "healthy"


def test_selection_reason_records_the_round_robin_channel():
    rows = ([_row(f"m{i:05d}", composite=100.0 - i, gbdt_score=100.0 - i,
                  recall_channels="momentum") for i in range(5)]
            + [_row(f"v{i:05d}", composite=100.0 - i, gbdt_score=100.0 - i,
                    recall_channels="value") for i in range(5)])
    kept, _ = triage_l2_for_l3(pd.DataFrame(rows), target=4)
    lanes = kept[kept["selection_reason"] == "lane"]
    assert len(lanes) == 4
    assert set(lanes["selection_detail"]) == {"momentum", "value"}


def test_selection_reason_backfill_for_channelless_rows():
    rows = [_row("bf0001", composite=95.0, gbdt_score=95.0, n_channels=0,
                 recall_channels="(backfill)"),
            _row("m00001", composite=10.0, gbdt_score=10.0, recall_channels="momentum")]
    kept, _ = triage_l2_for_l3(pd.DataFrame(rows), target=2)
    by_code = kept.set_index("code")
    assert by_code.loc["bf0001", "selection_reason"] == "backfill"
    assert by_code.loc["m00001", "selection_reason"] == "lane"


def test_every_kept_row_has_a_reason_from_the_vocabulary():
    """没有理由的行会让 tier-1 反事实无从构造 —— 这是 §4.4 点名的那个探针。"""
    from autoresearch.scan.l3.triage import SELECTION_REASONS

    rows = ([_row("p00001", composite=1.0, gbdt_score=1.0, pinned=True)]
            + [_row(f"h{i:05d}", composite=float(i), gbdt_score=float(i),
                    recall_channels="healthy") for i in range(5)]
            + [_row(f"m{i:05d}", composite=float(i) + 50, gbdt_score=float(i) + 50,
                    recall_channels="momentum") for i in range(20)]
            + [_row(f"x{i:05d}", composite=float(i) + 5, gbdt_score=float(i) + 5,
                    n_channels=0, recall_channels="") for i in range(10)])
    kept, _ = triage_l2_for_l3(pd.DataFrame(rows), target=18)
    assert kept["selection_reason"].notna().all()
    assert set(kept["selection_reason"]) <= set(SELECTION_REASONS)
    assert (kept["selection_reason"] != "").all()


def test_reason_survives_index_reset():
    """理由必须按**原始行索引**取:kept 不是 df 的前缀时,按位置取会整体错位。"""
    rows = [_row(f"{i:06d}", composite=float(i), gbdt_score=float(i),
                 recall_channels="momentum") for i in range(6)]
    rows[5] = _row("000005", composite=0.0, gbdt_score=0.0, pinned=True,
                   recall_channels="momentum")
    kept, _ = triage_l2_for_l3(pd.DataFrame(rows), target=2)
    assert kept.set_index("code").loc["000005", "selection_reason"] == "pinned"


def test_empty_df_kept_still_carries_provenance_columns():
    df = pd.DataFrame(columns=["code", "name", "composite", "gbdt_score"])
    kept, cut = triage_l2_for_l3(df, target=60)
    assert "selection_reason" in kept.columns and "selection_detail" in kept.columns
    assert len(kept) == 0 and len(cut) == 0


def test_pass1_meta_records_actual_k_not_just_target():
    """mandatory 超 target 时 kept 会略超 —— tier-1 要用**实际 K**。"""
    from autoresearch.scan.l3.triage import pass1_meta

    rows = [_row(f"p{i:05d}", composite=float(i), gbdt_score=float(i), pinned=True)
            for i in range(7)]
    df = pd.DataFrame(rows)
    kept, cut = triage_l2_for_l3(df, target=3)
    meta = pass1_meta(df, kept, cut, 3)
    assert meta["target"] == 3 and meta["n_kept"] == 7
    assert meta["reason_counts"]["pinned"] == 7 and meta["forced_in"] == 7
    assert meta["n_in"] == 7 and meta["n_cut"] == 0


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


def test_prepare_two_pass_writes_kept_csv_and_meta(tmp_path):
    """§4.4 可复原性:kept 产物 + 自描述 meta 必须与 cut 一起落盘。"""
    import json

    base = tmp_path / "context" / "scan"
    rows = [_row(f"{i:06d}", composite=90 - i, gbdt_score=90 - i,
                 recall_channels="momentum") for i in range(5)]
    d = _mk_l2(base, "2026-07-09", rows)

    res = prepare_l3_table("2026-07-09", root=base, do_harvest=False)

    kept_csv = pd.read_csv(d / "_l3_pass1_kept.csv", dtype={"code": str})
    assert len(kept_csv) == 5
    assert "selection_reason" in kept_csv.columns
    assert kept_csv["selection_reason"].notna().all()

    meta = json.loads((d / "_l3_pass1_meta.json").read_text(encoding="utf-8"))
    assert meta["n_in"] == 5 and meta["n_kept"] == 5 and meta["target"] == 60
    assert meta["rule_version"] and sum(meta["reason_counts"].values()) == 5
    assert res["pass1_meta"] == meta


def test_prepare_two_pass_false_writes_no_kept_artifacts(tmp_path):
    """回滚杆同时管住新产物 —— two_pass=False 时一个新文件都不该出现。"""
    base = tmp_path / "context" / "scan"
    rows = [_row(f"{i:06d}", composite=90 - i, gbdt_score=90 - i) for i in range(3)]
    d = _mk_l2(base, "2026-07-09", rows)

    prepare_l3_table("2026-07-09", root=base, do_harvest=False, two_pass=False)

    assert not (d / "_l3_pass1_kept.csv").exists()
    assert not (d / "_l3_pass1_meta.json").exists()


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


# ───────────────────────── ③b lowturn 强留(2026-08-21) ─────────────────────────


def _lt_row(code, composite, *, turn=True, **kw):
    # recall_channels 与其它行同为 composite:否则规则④的通道轮询会把它当「reversal 队列唯一
    # 成员」第一轮就捞进来,测不出③b 的作用;这里要测的是**分数最低也被强留**。
    r = _row(code, composite=composite, gbdt_score=composite, n_channels=1,
             recall_channels="composite")
    r.update({"pct_60d": -12.0, "dist_high_60": -22.0, "pct_5d": 4.0,
              "above_ma20": 1.0 if turn else 0.0, "ma5_gt_ma10": 1.0, "vol_ratio_20": 1.6,
              "main_inflow_yi": 0.8, "cmf_20": 0.05, "main_net_ratio": -0.01, "name": "甲"})
    r.update(kw)
    return r


def test_triage_lowturn_rows_forced_in_up_to_cap():
    rows = [_lt_row(f"{i:06d}", 1.0 + i) for i in range(5)]            # 5 只旗亮、分数极低
    rows += [_row(f"{100 + i:06d}", composite=99.0 - i, gbdt_score=99.0 - i) for i in range(40)]
    kept, cut = triage_l2_for_l3(pd.DataFrame(rows), target=20, lowturn_cap=3)
    forced = kept[kept["selection_detail"] == "lowturn"]
    assert len(forced) == 3 and set(forced["code"]) == {"000004", "000003", "000002"}
    assert (forced["selection_reason"] == "lane").all()


def test_triage_lowturn_cap_zero_is_parity():
    rows = [_lt_row("000001", 1.0)]
    rows += [_row(f"{100 + i:06d}", composite=99.0 - i, gbdt_score=99.0 - i) for i in range(30)]
    a, _ = triage_l2_for_l3(pd.DataFrame(rows), target=10)
    b, _ = triage_l2_for_l3(pd.DataFrame(rows), target=10, lowturn_cap=0)
    pd.testing.assert_frame_equal(a, b)
    assert "000001" not in set(a["code"])


def test_triage_lowturn_respects_cfg_and_missing_cols():
    rows = [_lt_row("000001", 1.0, vol_ratio_20=1.1)]
    rows += [_row(f"{100 + i:06d}", composite=99.0 - i, gbdt_score=99.0 - i) for i in range(30)]
    kept, _ = triage_l2_for_l3(pd.DataFrame(rows), target=10, lowturn_cap=3)
    assert "000001" not in set(kept["code"])                       # 1.1 < 1.2 不亮
    kept2, _ = triage_l2_for_l3(pd.DataFrame(rows), target=10, lowturn_cap=3,
                                lowturn_cfg={"min_vol_ratio_20": 1.0})
    assert "000001" in set(kept2["code"])
    bare = pd.DataFrame([_row(f"{i:06d}", composite=99.0 - i, gbdt_score=99.0 - i) for i in range(30)])
    kept3, _ = triage_l2_for_l3(bare, target=10, lowturn_cap=3)     # 缺旗列:不炸,不强留
    assert len(kept3) == 10


def test_triage_resonance_is_capped_at_five(monkeypatch):
    """规则② 由「n_channels>=3 全入」收窄为「按 composite 取前 RESONANCE_CAP(5)」(2026-08-22)。

    理由是数学:n_channels 与 dist_high_60 的 spearman 0.34 —— 多路共振 ≈「已经涨起来」,
    不是独立多因子确认。被挤出的共振票不再免检,改与其他 lane 竞争 round-robin。
    """
    from autoresearch.scan.l3.triage import RESONANCE_CAP

    rows = [_row(f"77{i:04d}", composite=10.0 + i, gbdt_score=10.0 + i, n_channels=4,
                 recall_channels="momentum|heat|value|growth") for i in range(8)]
    rows += [_row(f"{i:06d}", composite=99.0 - i, gbdt_score=99.0 - i, n_channels=1,
                  recall_channels="composite") for i in range(30)]
    kept, _cut = triage_l2_for_l3(pd.DataFrame(rows), target=12)
    guarded = kept[kept["selection_reason"] == "conviction_guard"]
    assert len(guarded) == RESONANCE_CAP == 5
    # 强留的必须是 composite 最高的 5 只共振票(不是行序)
    assert set(guarded["code"]) == {f"77{i:04d}" for i in range(3, 8)}
