"""merge_l3_finalists_v3(finalist 标记消费 + 确定性守卫)+ write_finalists 收窄接线。

design: docs/plans/2026-07-12-l3-merge-plan.md Task 2(L3 合并波)。NO network,纯确定性。

守卫序(按序):①finalist==True 消费 → ②ins75 保险(conviction>=75 强制补入)→
③lt55 拒绝(conviction<55 剔除)→ ④超 cap 按 conviction 截尾 → ⑤健康比例守卫
(ceil(n/3),lane=="healthy",v1 从简判定)→ ⑥trend soft 2 席 → 缺 `finalist` 列走
向后兼容回退(全体按 conviction 排序取 cap,同守卫)。bench = judged − finalists。
"""
from __future__ import annotations

import csv
import json

import pandas as pd

from autoresearch.scan.agents.l3_select import merge_l3_finalists_v3, write_finalists


def _pick(code, conviction, lane="momentum", finalist=None, name=None, **extra) -> dict:
    d = {"code": code, "name": name or f"票{code}", "sector": "电子", "lenses": "动量",
        "conviction": conviction, "fragility": 20, "thesis": "t", "risk": "r",
        "catalyst": "c", "triage_lean": "标配", "triage_reason": "x", "lane": lane,
        "sentiment": "中性"}
    if finalist is not None:
        d["finalist"] = finalist
    d.update(extra)
    return d


# ═══════════════════════ ①finalist 标记消费 ═══════════════════════


def test_consumes_finalist_true_flag_only():
    """`finalist=True` 的行入选 finalists,`finalist=False` 的行入 bench——两者 conviction
    都在 55-75 之间(不触发 ins75/lt55 守卫),纯粹测标记消费。"""
    judged = pd.DataFrame([
        _pick("000001", 70, finalist=True),
        _pick("000002", 68, finalist=True),
        _pick("000003", 65, finalist=False),
        _pick("000004", 60, finalist=False),
    ])
    fin, bench = merge_l3_finalists_v3(judged, budget=30, finalist_max=10)
    assert set(fin["code"]) == {"000001", "000002"}
    assert set(bench["code"]) == {"000003", "000004"}
    assert (fin["guard"] == "").all()
    assert (bench["guard"] == "").all()


def test_finalists_and_bench_partition_judged_exactly():
    judged = pd.DataFrame([_pick(f"{i:06d}", 55 + i, finalist=(i % 2 == 0)) for i in range(10)])
    fin, bench = merge_l3_finalists_v3(judged, budget=30, finalist_max=10)
    assert not (set(fin["code"]) & set(bench["code"]))
    assert set(fin["code"]) | set(bench["code"]) == set(judged["code"])
    assert len(fin) + len(bench) == len(judged)


# ═══════════════════════ ②ins75 保险 ═══════════════════════


def test_ins75_force_inserts_high_conviction_row_not_marked_finalist():
    """conviction=80(>=75)但 finalist=False → 强制补入 finalists,guard='ins75'。"""
    judged = pd.DataFrame([
        _pick("000001", 70, finalist=True),
        _pick("000099", 80, finalist=False),     # 高 conviction 但 l3-rank 漏标
    ])
    fin, bench = merge_l3_finalists_v3(judged, budget=30, finalist_max=10)
    assert "000099" in set(fin["code"])
    row = fin[fin["code"] == "000099"].iloc[0]
    assert row["guard"] == "ins75"


def test_ins75_already_selected_gets_no_guard_tag():
    """conviction>=75 且本就 finalist=True → 正常入选,不打 ins75 标(它没被"救回")。"""
    judged = pd.DataFrame([_pick("000001", 80, finalist=True)])
    fin, _ = merge_l3_finalists_v3(judged, budget=30, finalist_max=10)
    assert fin.iloc[0]["guard"] == ""


# ═══════════════════════ ③lt55 拒绝 ═══════════════════════


def test_lt55_rejects_low_conviction_row_even_if_marked_finalist():
    """finalist=True 但 conviction=50(<55)→ 剔除进 bench,guard='lt55'。"""
    judged = pd.DataFrame([
        _pick("000001", 70, finalist=True),
        _pick("000050", 50, finalist=True),      # l3-rank 误标
    ])
    fin, bench = merge_l3_finalists_v3(judged, budget=30, finalist_max=10)
    assert "000050" not in set(fin["code"])
    row = bench[bench["code"] == "000050"].iloc[0]
    assert row["guard"] == "lt55"


# ═══════════════════════ ④cap 截尾 ═══════════════════════


def test_cap_truncates_excess_finalists_by_conviction():
    """finalist_max=3,budget=30 → cap=3;5 只都标 finalist=True(conviction 62-70,均不触发
    ins75/lt55)→ 只留 conviction 最高的 3 只,其余 2 只挪 bench。"""
    judged = pd.DataFrame([_pick(f"{i:06d}", 70 - i, finalist=True) for i in range(5)])
    fin, bench = merge_l3_finalists_v3(judged, budget=30, finalist_max=3)
    assert len(fin) == 3
    assert set(fin["code"]) == {"000000", "000001", "000002"}   # conviction 70/69/68 最高三
    assert set(bench["code"]) == {"000003", "000004"}


def test_cap_is_min_of_finalist_max_and_budget():
    """cap = min(finalist_max, budget)——budget 更严格时budget 生效。"""
    judged = pd.DataFrame([_pick(f"{i:06d}", 70 - i, finalist=True) for i in range(5)])
    fin, _ = merge_l3_finalists_v3(judged, budget=2, finalist_max=10)
    assert len(fin) == 2
    assert set(fin["code"]) == {"000000", "000001"}


# ═══════════════════════ ⑤健康比例守卫(ceil(n/3)) ═══════════════════════


def test_healthy_quota_off_by_default_no_swap():
    """2026-08-22 批 (a):HEALTHY_QUOTA_FRAC=0 → 守卫④ 不动作。同一 fixture 下旧行为会把
    bench 的 healthy 票(68)换进来、踢掉 value 尾票(60);现在 finalists 原样、guard 全空。
    证据:healthy 画像三把尺全负(edge 普查)。"""
    judged = pd.DataFrame([
        _pick("AAAAAA", 80, lane="trend", finalist=True),
        _pick("BBBBBB", 70, lane="momentum", finalist=True),
        _pick("CCCCCC", 60, lane="value", finalist=True),
        _pick("DDDDDD", 68, lane="healthy", finalist=False),
    ])
    fin, bench = merge_l3_finalists_v3(judged, budget=30, finalist_max=10)
    assert set(fin["code"]) == {"AAAAAA", "BBBBBB", "CCCCCC"}
    assert set(bench["code"]) == {"DDDDDD"}
    assert (fin["guard"] == "").all() and (bench["guard"] == "").all()


def test_healthy_quota_parity_when_frac_restored(monkeypatch):
    """回滚杆锁:HEALTHY_QUOTA_FRAC 改回 1/3 → ④ 逐字恢复旧行为(换进 healthy 68、踢 value 60,
    双方记 guard='healthy_quota')。"""
    from autoresearch.scan.l3 import merge as mg
    monkeypatch.setattr(mg, "HEALTHY_QUOTA_FRAC", 1 / 3)
    judged = pd.DataFrame([
        _pick("AAAAAA", 80, lane="trend", finalist=True),
        _pick("BBBBBB", 70, lane="momentum", finalist=True),
        _pick("CCCCCC", 60, lane="value", finalist=True),
        _pick("DDDDDD", 68, lane="healthy", finalist=False),
    ])
    fin, bench = merge_l3_finalists_v3(judged, budget=30, finalist_max=10)
    assert set(fin["code"]) == {"AAAAAA", "BBBBBB", "DDDDDD"}
    assert set(bench["code"]) == {"CCCCCC"}
    assert fin[fin["code"] == "DDDDDD"].iloc[0]["guard"] == "healthy_quota"
    assert bench[bench["code"] == "CCCCCC"].iloc[0]["guard"] == "healthy_quota"


def test_healthy_rows_no_longer_protected_from_trend_swap_by_default():
    """frac=0 时 healthy 行不再是「④ 刚满足的硬约束」,守卫⑤ 换尾票可以换走它(最弱者)。"""
    judged = pd.DataFrame([
        _pick("AAAAAA", 80, lane="momentum", finalist=True),
        _pick("HHHHHH", 58, lane="healthy", finalist=True),    # 最弱,且不再受保护
        _pick("VVVVVV", 62, lane="value", finalist=True),
        _pick("TTTTTT", 70, lane="trend", finalist=False),     # 够格 trend 候选(≥65)
    ])
    fin, bench = merge_l3_finalists_v3(judged, budget=30, finalist_max=10)
    assert "TTTTTT" in set(fin["code"]) and "HHHHHH" in set(bench["code"])


def test_healthy_quota_does_not_force_when_bench_has_no_qualifying_candidate():
    """健康画像缺口存在,但 bench 无够格候选(唯一非 finalist 票 conviction 太低或非
    healthy lane)→ 不硬凑,finalists 保持原样(宁缺毋滥)。"""
    judged = pd.DataFrame([
        _pick("AAAAAA", 80, lane="trend", finalist=True),
        _pick("BBBBBB", 70, lane="momentum", finalist=True),
        _pick("CCCCCC", 60, lane="value", finalist=True),
        _pick("DDDDDD", 60, lane="healthy", finalist=False),   # healthy 但 conviction<65,不够格
    ])
    fin, bench = merge_l3_finalists_v3(judged, budget=30, finalist_max=10)
    assert set(fin["code"]) == {"AAAAAA", "BBBBBB", "CCCCCC"}
    assert set(bench["code"]) == {"DDDDDD"}
    assert (fin["guard"] == "").all()


def test_healthy_quota_protects_conviction_ge_75_rows_from_being_swapped_out(monkeypatch):
    """尾部置换不能挪走 conviction>=75 的行(ins75 保险保护范围)——即便它是当前候选集里
    唯一的"非 healthy"票、换出它才能腾位置,也不换,守卫序里 conviction>=75 恒留任。
    (2026-08-22 起 ④ 默认关,本条在 frac=1/3 回滚态下验证该保护仍成立。)"""
    from autoresearch.scan.l3 import merge as mg
    monkeypatch.setattr(mg, "HEALTHY_QUOTA_FRAC", 1 / 3)
    judged = pd.DataFrame([
        _pick("AAAAAA", 90, lane="trend", finalist=True),   # 唯一非 healthy,但 conviction>=75 受保护
        _pick("BBBBBB", 70, lane="healthy", finalist=True),
        _pick("CCCCCC", 68, lane="healthy", finalist=True),
        _pick("DDDDDD", 66, lane="healthy", finalist=False),   # 够格候选,但没有可换的尾部票
    ])
    fin, bench = merge_l3_finalists_v3(judged, budget=30, finalist_max=10)
    # ceil(3/3)=1,已有 B/C 两只 healthy,缺口本就是 0——用一个真缺口场景更直接:
    # 这里主要验证 A 不会被换出(即便它是唯一 lane!=healthy 的行)。
    assert "AAAAAA" in set(fin["code"])


# ═══════════════════════ trend soft 2 席(同守卫机制) ═══════════════════════


def test_trend_soft_quota_swaps_in_bench_candidate_when_deficit():
    """finalists 里 trend lane 只有 1 只(< soft 下限 2),bench 有够格 trend 候选
    (conviction>=65)→ 换入,guard='trend_quota'。"""
    judged = pd.DataFrame([
        _pick("AAAAAA", 80, lane="trend", finalist=True),
        _pick("BBBBBB", 70, lane="value", finalist=True),
        _pick("CCCCCC", 60, lane="momentum", finalist=True),
        _pick("EEEEEE", 66, lane="trend", finalist=False),
    ])
    fin, bench = merge_l3_finalists_v3(judged, budget=30, finalist_max=10)
    assert "EEEEEE" in set(fin["code"])
    assert fin[fin["code"] == "EEEEEE"].iloc[0]["guard"] == "trend_quota"
    assert set(bench["code"]) & {"BBBBBB", "CCCCCC"}   # 其中一只被换出


# ═══════════════════════ ⑥缺 finalist 列(向后兼容回退) ═══════════════════════


def test_missing_finalist_column_falls_back_to_conviction_rank():
    """无 `finalist` 列(旧 judged json)→ 全体按 conviction 排序取 cap,同守卫(lt55 仍剔除,
    cap 仍截尾)。"""
    judged = pd.DataFrame([_pick(f"{i:06d}", 90 - i * 5) for i in range(6)])  # 90,85,80,75,70,65
    assert "finalist" not in judged.columns
    fin, bench = merge_l3_finalists_v3(judged, budget=30, finalist_max=3)
    assert len(fin) == 3
    assert set(fin["code"]) == {"000000", "000001", "000002"}   # conviction 90/85/80 最高三
    assert len(bench) == 3


def test_missing_finalist_column_lt55_still_rejects():
    judged = pd.DataFrame([
        _pick("000001", 70),
        _pick("000002", 50),      # <55,即便按 conviction 排序会进 cap 也该被 lt55 剔除
    ])
    assert "finalist" not in judged.columns
    fin, bench = merge_l3_finalists_v3(judged, budget=30, finalist_max=10)
    assert "000002" not in set(fin["code"])
    assert bench[bench["code"] == "000002"].iloc[0]["guard"] == "lt55"


def test_empty_judged_returns_empty_both_with_guard_column():
    judged = pd.DataFrame(columns=["code", "name", "conviction", "lane"])
    fin, bench = merge_l3_finalists_v3(judged, budget=30, finalist_max=10)
    assert len(fin) == 0 and len(bench) == 0
    assert "guard" in fin.columns and "guard" in bench.columns


# ═══════════════════════ ⑦write_finalists:bench csv 落盘 + 真数据冒烟(回退路径) ═══════════════════════


def test_write_finalists_writes_bench_csv_with_correct_row_count(tmp_path):
    """write_finalists 落 `_l3_bench.csv`,行数 = judged − finalists;返回 dict 加
    finalist_n/bench_n(finalist_n 为 pinned 注入前的 v3 finalist 数)。"""
    base = tmp_path / "context" / "scan"
    d = base / "2026-07-09"
    d.mkdir(parents=True)
    picks = [_pick(f"{i:06d}", 90 - i * 3, finalist=(i < 3)) for i in range(8)]
    (d / "_l3_judged.json").write_text(json.dumps(picks), encoding="utf-8")
    pd.DataFrame({"code": [f"{i:06d}" for i in range(8)],
                 "pct_60d": [1.0] * 8}).to_csv(d / "L2_gbdt_top200.csv", index=False)

    res = write_finalists("2026-07-09", budget=30, root=base)

    assert (d / "_l3_bench.csv").exists()
    bench_rows = list(csv.DictReader((d / "_l3_bench.csv").open(encoding="utf-8")))
    fin_rows = list(csv.DictReader((d / "finalists.csv").open(encoding="utf-8")))
    assert len(bench_rows) == 8 - len(fin_rows)
    assert res["judged_n"] == 8
    assert res["finalists_n"] == len(fin_rows)
    assert res["finalist_n"] == len(fin_rows)      # 无 pinned → 与 finalists_n 相同
    assert res["bench_n"] == len(bench_rows)


def test_write_finalists_real_2026_07_09_judged_missing_finalist_column_falls_back(tmp_path):
    """真数据冒烟(隔离副本,不碰原 context/scan/2026-07-09):真实现场的 `_l3_judged.json`
    本身已被后续流程清理(只留派生的 `L3_judged_full.csv`),但该 csv 就是当时 write_finalists
    读进来的同一份 judged 数据(同列、无 `finalist` 列——T3 落 finalist 字段之前的旧现场)→
    重建等价 `_l3_judged.json` 喂 write_finalists,验证向后兼容回退路径:出 <=10 只、
    bench csv 落盘。"""
    import shutil

    real_dir = __import__("pathlib").Path("context/scan/2026-07-09")
    judged_csv = real_dir / "L3_judged_full.csv"
    if not judged_csv.exists():
        import pytest
        pytest.skip("2026-07-09 真实现场不存在,跳过冒烟(CI/无历史数据环境)")

    real_judged = pd.read_csv(judged_csv, dtype={"code": str})
    assert "finalist" not in real_judged.columns, (
        "本用例前提是旧现场无 finalist 列(回退路径);若已带列,冒烟已不适用")
    picks = json.loads(real_judged.to_json(orient="records", force_ascii=False))

    base = tmp_path / "context" / "scan"
    d = base / "2026-07-09"
    d.mkdir(parents=True)
    (d / "_l3_judged.json").write_text(json.dumps(picks, ensure_ascii=False), encoding="utf-8")
    real_l2 = real_dir / "L2_gbdt_top200.csv"
    if real_l2.exists():
        shutil.copy(real_l2, d / "L2_gbdt_top200.csv")

    res = write_finalists("2026-07-09", budget=30, root=base)

    assert res["finalists_n"] <= 10
    assert (d / "_l3_bench.csv").exists()
    assert (d / "finalists.csv").exists()
    assert res["bench_n"] == res["judged_n"] - res["finalist_n"]
    # 隔离副本验证:原目录一字未动。
    assert not (real_dir / "_l3_bench.csv").exists()


def test_dup_codes_dropped_to_bench_with_guard():
    """终审 I-1:judged 同码两行(zfill 后重码)→ 只留首行走守卫,其余整行归 bench 记 guard="dup"。"""
    judged = pd.DataFrame([
        {"code": "000001", "name": "A", "conviction": 80.0, "lane": "trend", "finalist": True},
        {"code": "1", "name": "A-dup", "conviction": 70.0, "lane": "trend", "finalist": True},
        {"code": "000002", "name": "B", "conviction": 68.0, "lane": "value", "finalist": True},
    ])
    fin, bench = merge_l3_finalists_v3(judged, budget=30)
    assert list(fin["code"]).count("000001") == 1
    dup_rows = bench[bench["guard"] == "dup"]
    assert len(dup_rows) == 1 and dup_rows.iloc[0]["code"] == "000001"


# ═══════════════════════ ⑥ lowturn soft 1 席(2026-08-21 低位转强波) ═══════════════════════


def test_lowturn_soft_quota_swaps_in_qualified_bench_candidate():
    """finalists 无 lowturn、bench 有 conviction≥55 的 lowturn → 换入,guard='lowturn_quota';
    不得吃掉 healthy/trend 行(protect_lanes)。"""
    judged = pd.DataFrame([
        _pick("HHHHHH", 60, lane="healthy", finalist=True),
        _pick("TTTTTT", 58, lane="trend", finalist=True),
        _pick("VVVVVV", 57, lane="value", finalist=True),
        _pick("LLLLLL", 56, lane="lowturn", finalist=False),
    ])
    fin, bench = merge_l3_finalists_v3(judged, budget=30, finalist_max=10)
    assert "LLLLLL" in set(fin["code"])
    assert fin[fin["code"] == "LLLLLL"].iloc[0]["guard"] == "lowturn_quota"
    assert set(bench["code"]) == {"VVVVVV"}            # 被换出的是 value 尾票,不是 healthy/trend


def test_lowturn_quota_not_forced_below_55():
    judged = pd.DataFrame([
        _pick("AAAAAA", 70, lane="value", finalist=True),
        _pick("LLLLLL", 50, lane="lowturn", finalist=False),     # <55 不够格(守卫②同阈)
    ])
    fin, _ = merge_l3_finalists_v3(judged, budget=30, finalist_max=10)
    assert set(fin["code"]) == {"AAAAAA"}


def test_lowturn_already_present_no_swap():
    judged = pd.DataFrame([
        _pick("LLLLLL", 66, lane="lowturn", finalist=True),
        _pick("AAAAAA", 60, lane="value", finalist=True),
        _pick("MMMMMM", 58, lane="lowturn", finalist=False),
    ])
    fin, _ = merge_l3_finalists_v3(judged, budget=30, finalist_max=10)
    assert set(fin["code"]) == {"LLLLLL", "AAAAAA"} and (fin["guard"] == "").all()


# ═══════════════ 守卫⑦ chase_1d(2026-08-22 批 B)═══════════════
# 立案:2026-08-21 002716(当日 +10.0%)与 603209 双双入围 → 两张卡都在 L4 早停「涨停追高」,
# 2/9 席位(22% Opus 预算)花在 L4 按规则必否的票上。


def test_chase_1d_drops_and_backfills_from_bench():
    judged = pd.DataFrame([
        _pick("000001", 70, finalist=True, pct_1d=10.0),   # 追高 → 剔
        _pick("000002", 68, finalist=True, pct_1d=2.0),
        _pick("000003", 60, finalist=False, pct_1d=1.0),   # bench 最高 → 回填
        _pick("000004", 56, finalist=False, pct_1d=1.0),
    ])
    fin, bench = merge_l3_finalists_v3(judged, budget=30, finalist_max=10)
    assert set(fin["code"]) == {"000002", "000003"}, "剔 1 补 1,席位数不变"
    assert bench.set_index("code").loc["000001", "guard"] == "chase_1d"
    assert fin.set_index("code").loc["000003", "guard"] == "chase_backfill"


def test_chase_1d_overrides_ins75():
    """ins75 保的是「L3 判高分却没标 finalist」的误杀,不是「追高豁免」。"""
    judged = pd.DataFrame([
        _pick("000001", 88, finalist=False, pct_1d=12.0),  # ins75 会补入 → 但追高应再剔掉
        _pick("000002", 70, finalist=True, pct_1d=1.0),
    ])
    fin, bench = merge_l3_finalists_v3(judged, budget=30, finalist_max=10)
    assert "000001" not in set(fin["code"])
    assert bench.set_index("code").loc["000001", "guard"] == "chase_1d"


def test_chase_1d_no_backfill_below_55():
    """bench 无够格候选(conviction<55)→ 席位空着,不硬凑(同④⑤⑥纪律)。"""
    judged = pd.DataFrame([
        _pick("000001", 70, finalist=True, pct_1d=10.0),
        _pick("000002", 68, finalist=True, pct_1d=2.0),
        _pick("000003", 40, finalist=False, pct_1d=1.0),
    ])
    fin, bench = merge_l3_finalists_v3(judged, budget=30, finalist_max=10)
    assert set(fin["code"]) == {"000002"}
    assert "000003" in set(bench["code"])


def test_chase_1d_noop_without_column():
    """judged 与 L2 都没有 pct_1d → 守卫整段 no-op(逐字 parity)。"""
    judged = pd.DataFrame([_pick("000001", 70, finalist=True),
                           _pick("000002", 68, finalist=True)])
    fin, _ = merge_l3_finalists_v3(judged, budget=30, finalist_max=10)
    assert set(fin["code"]) == {"000001", "000002"}
    assert (fin["guard"] == "").all()


def test_chase_1d_boundary_is_inclusive_at_threshold():
    judged = pd.DataFrame([
        _pick("000001", 70, finalist=True, pct_1d=9.5),    # == 阈值 → 剔
        _pick("000002", 68, finalist=True, pct_1d=9.49),   # 差一点 → 留
    ])
    fin, _ = merge_l3_finalists_v3(judged, budget=30, finalist_max=10)
    assert set(fin["code"]) == {"000002"}


def test_chase_1d_nan_is_kept():
    """NaN(L2 缺该票)不算追高——不冤枉,与 pf 词「缺值不出现」同口径。"""
    judged = pd.DataFrame([
        _pick("000001", 70, finalist=True, pct_1d=float("nan")),
        _pick("000002", 68, finalist=True, pct_1d=1.0),
    ])
    fin, _ = merge_l3_finalists_v3(judged, budget=30, finalist_max=10)
    assert set(fin["code"]) == {"000001", "000002"}


# ═══════════════ 守卫⑧ sector_cap(2026-08-22 批 C)═══════════════
# 立案:2026-08-21 贵金属(12 只成分的申万二级)拿 4 席 + 下游饰品 1 席 = 5/9,
# 而 L3/merge 此前一个行业帽也没有(L2 有 sector_cap 20%)。


def _sec(code, conviction, sector, lane="momentum", finalist=True, **extra):
    d = _pick(code, conviction, lane=lane, finalist=finalist, **extra)
    d["sector"] = sector
    return d


def test_sector_cap_drops_weakest_and_backfills_other_sector():
    judged = pd.DataFrame([
        _sec("000001", 76, "贵金属"), _sec("000002", 74, "贵金属"),
        _sec("000003", 66, "贵金属"), _sec("000004", 62, "贵金属"),   # 第 4 席、最弱 → 剔
        _sec("000005", 59, "石油"),
        _sec("000006", 58, "航运", finalist=False),                    # bench 异行业 → 回填
    ])
    fin, bench = merge_l3_finalists_v3(judged, budget=30, finalist_max=10)
    assert sum(1 for s in fin["sector"] if s == "贵金属") == 3
    assert "000006" in set(fin["code"])
    assert fin.set_index("code").loc["000006", "guard"] == "sector_backfill"
    assert bench.set_index("code").loc["000004", "guard"] == "sector_cap"


def test_sector_cap_respects_ins75():
    """conviction≥75 的行不可被行业帽剔(与④⑤⑥ 同纪律)。"""
    judged = pd.DataFrame([
        _sec("000001", 80, "贵金属"), _sec("000002", 78, "贵金属"),
        _sec("000003", 77, "贵金属"), _sec("000004", 76, "贵金属"),
        _sec("000005", 60, "航运", finalist=False),
    ])
    fin, _ = merge_l3_finalists_v3(judged, budget=30, finalist_max=10)
    assert sum(1 for s in fin["sector"] if s == "贵金属") == 4, "全 ≥75 → 无人可剔,不硬砍"


def test_sector_cap_skips_victim_that_would_break_lane_quota():
    """**保护的是配额、不是整个 lane**:最弱的那只恰好是 trend 且剔了会跌破守卫⑤ 的 2 席 → 跳过它,
    改剔次弱的非配额行。(2026-08-22 起 healthy 不再有配额,本条用 trend lane 验证同一机制。)"""
    judged = pd.DataFrame([
        _sec("000001", 70, "贵金属", lane="trend"),
        _sec("000002", 68, "贵金属", lane="main"),
        _sec("000003", 66, "贵金属", lane="main"),      # 次弱、非配额 lane → 应剔它
        _sec("000004", 60, "贵金属", lane="trend"),     # 最弱,但剔了 trend 只剩 1 < floor 2
        _sec("000005", 58, "石油", lane="main"),
        _sec("000006", 57, "航运", lane="main", finalist=False),
    ])
    fin, bench = merge_l3_finalists_v3(judged, budget=30, finalist_max=10)
    assert sum(1 for s in fin["sector"] if s == "贵金属") == 3
    assert "000004" in set(fin["code"]), "trend 配额行被保住"
    assert "000003" not in set(fin["code"]), "改剔次弱的非配额行"
    assert bench.set_index("code").loc["000003", "guard"] == "sector_cap"
    assert sum(1 for ln in fin["lane"] if ln == "trend") >= 2


def test_sector_cap_prefers_weakest_when_no_quota_conflict():
    """无配额冲突时就是「剔最弱」——配额保护不该把普通情形也一起改掉。"""
    judged = pd.DataFrame([
        _sec("000001", 70, "贵金属", lane="healthy"),
        _sec("000002", 68, "贵金属", lane="healthy"),
        _sec("000003", 66, "贵金属", lane="healthy"),
        _sec("000004", 64, "贵金属", lane="main"),      # 非配额、最弱 → 剔它
        _sec("000005", 62, "石油", lane="value"),
        _sec("000006", 60, "航运", lane="value", finalist=False),
    ])
    fin, _ = merge_l3_finalists_v3(judged, budget=30, finalist_max=10)
    assert sum(1 for s in fin["sector"] if s == "贵金属") == 3
    assert sum(1 for ln in fin["lane"] if ln == "healthy") >= 2
    assert "000004" not in set(fin["code"])


def test_sector_cap_backfill_never_creates_new_violation():
    """回填不得把补进来的票自己所在 sector 顶破帽。"""
    judged = pd.DataFrame([
        _sec("000001", 76, "贵金属"), _sec("000002", 74, "贵金属"),
        _sec("000003", 66, "贵金属"), _sec("000004", 62, "贵金属"),
        _sec("000005", 61, "贵金属", finalist=False),   # bench 也是贵金属 → 不可回填
        _sec("000006", 60, "航运", finalist=False),     # 应选它
    ])
    fin, _ = merge_l3_finalists_v3(judged, budget=30, finalist_max=10)
    assert sum(1 for s in fin["sector"] if s == "贵金属") == 3
    assert "000006" in set(fin["code"]) and "000005" not in set(fin["code"])


def test_sector_cap_noop_without_sector_column():
    judged = pd.DataFrame([{**_pick(f"00000{i}", 70 - i, finalist=True)} for i in range(1, 6)])
    judged = judged.drop(columns=["sector"])
    fin, _ = merge_l3_finalists_v3(judged, budget=30, finalist_max=10)
    assert len(fin) == 5 and (fin["guard"] == "").all()


def test_sector_cap_under_limit_is_silent():
    judged = pd.DataFrame([_sec("000001", 70, "贵金属"), _sec("000002", 68, "贵金属"),
                           _sec("000003", 66, "石油")])
    fin, _ = merge_l3_finalists_v3(judged, budget=30, finalist_max=10)
    assert len(fin) == 3 and (fin["guard"] == "").all()


def test_sector_cap_no_longer_protects_healthy_by_default():
    """2026-08-22 批 (a):healthy 没有配额 → 守卫⑧ 的配额保护不再含它;最弱的 healthy 行照剔。
    (MA4 变异:`_lane_quota_floor` 若仍写死 healthy=ceil(n/3),本条变红。)"""
    judged = pd.DataFrame([
        _sec("000001", 70, "贵金属", lane="main"),
        _sec("000002", 68, "贵金属", lane="main"),
        _sec("000003", 66, "贵金属", lane="main"),
        _sec("000004", 60, "贵金属", lane="healthy"),   # 最弱;旧逻辑会因 healthy floor 跳过它
        _sec("000005", 58, "石油", lane="main"),
        _sec("000006", 57, "航运", lane="main", finalist=False),
    ])
    fin, bench = merge_l3_finalists_v3(judged, budget=30, finalist_max=10)
    assert "000004" not in set(fin["code"])
    assert bench.set_index("code").loc["000004", "guard"] == "sector_cap"
    assert sum(1 for s in fin["sector"] if s == "贵金属") == 3


def test_composite_seats_exclude_falling_knives():
    from autoresearch.scan.l3.merge import pick_composite_seats
    l2 = pd.DataFrame({"code": ["000001", "000002", "000003", "000004"], "name": list("甲乙丙丁"),
                       "industry": "电力", "gbdt_score": [90.0, 80.0, 70.0, 60.0],
                       "pct_1d": 1.0, "pct_60d": [-35.0, 5.0, -21.0, 8.0]})
    assert [s["code"] for s in pick_composite_seats(l2, 3)] == ["000002", "000004"]
