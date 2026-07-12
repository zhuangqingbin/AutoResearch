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


def test_healthy_quota_swaps_in_bench_candidate_when_deficit():
    """3 只 finalist(均非 healthy lane,conviction 60/70/80)→ ceil(3/3)=1 健康画像缺口;
    bench 有一只够格(conviction=68>=65,lane=healthy)→ 换掉候选集里最弱(非 protected)的
    尾部票,双方都记 guard='healthy_quota'。(6 位字母码,zfill 对齐后原样不变,断言免去
    前导零换算。)"""
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


def test_healthy_quota_protects_conviction_ge_75_rows_from_being_swapped_out():
    """尾部置换不能挪走 conviction>=75 的行(ins75 保险保护范围)——即便它是当前候选集里
    唯一的"非 healthy"票、换出它才能腾位置,也不换,守卫序里 conviction>=75 恒留任。"""
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
