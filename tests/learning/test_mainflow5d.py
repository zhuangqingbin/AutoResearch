"""EXP-1 `ow_gate_mainflow5d` 影子数据腿(Wave12-T20)+ 两条实验的观测起账。

预注册于 2026-08-01(`exp_20260801_ow_gate_mainflow5d`),challenger 判据写在 spec 里逐字
不动:`sum(main_net_yi, T-4..T) > 0 ∧ positive_days >= 3 ∧ main_distortion == false`。
但**从来没有任何代码算过它**——`gate_attribution.py` 里没有任何 5 日窗逻辑,registry 的
`observations` 至今 `[]`。本文件锁四件事:

1. **PIT 五交易日窗**:窗口是 T-4..T 共**五个交易日**(不是自然日),交易日由 moneyflow
   湖分区文件名决定;**缺任一日 → 该票 `UNMEASURED`**,不联网补、不用 4 天凑数。
2. **judge 判据逐字**:三条件与 spec 一致;任一条件所需的输入缺失 → `UNMEASURED`。
3. **会变的量**:跑一天 → registry 两条实验的 `observations` 各 +1(自动腿必须有一个会变
   的量做断言,否则它死了也像活着 —— 2026-07-16 权重重标定连续 4 次 NO-OP 判例)。
4. **影子观测不得污染 `rollback_watch` 的观察窗**:`observations` 此前只有一种写者
   (post-activation 的 rollback assessment,`observed = len(prior)+1`);影子行混进去会
   让观察窗提前满、或被 `all_pass` 误判。

全部测试自带 tmp registry / tmp lake / tmp 账本 —— `tests/learning/conftest.py` 的
autouse 护栏会对真实 `reports/`、`context/scan/` 的写入直接抛 `PermissionError`。
"""
from __future__ import annotations

import json
from pathlib import Path

import pandas as pd
import pytest

from autoresearch.learning import experiment_registry as registry
from autoresearch.learning import mainflow5d

EXP1 = "exp_20260801_ow_gate_mainflow5d"
EXP2 = "exp_20260801_recall_sector_momentum"

_DAYS = ["20260728", "20260729", "20260730", "20260731", "20260803", "20260804", "20260805"]


# ── fixtures ────────────────────────────────────────────────────────────────


def _write_moneyflow(lake: Path, day: str, rows: dict[str, float]) -> None:
    """{code6: main_net_yi} → 一个 moneyflow 湖分区(用与生产同一组原始列反推)。"""
    lake.mkdir(parents=True, exist_ok=True)
    frame = pd.DataFrame({
        "ts_code": [f"{c}.SZ" for c in rows],
        # main_net_yi = (buy_lg + buy_elg − sell_lg − sell_elg) / 1e4(万元 → 亿)
        "buy_lg_amount": [v * 1e4 for v in rows.values()],
        "sell_lg_amount": [0.0] * len(rows),
        "buy_elg_amount": [0.0] * len(rows),
        "sell_elg_amount": [0.0] * len(rows),
        "buy_sm_amount": [0.0] * len(rows),
        "sell_sm_amount": [0.0] * len(rows),
        "net_mf_amount": [v * 1e4 for v in rows.values()],
    })
    frame.to_parquet(lake / f"{day}.parquet", index=False)


def _lake(tmp_path: Path, series: dict[str, list[float]], days=None) -> Path:
    """{code6: [T-6..T 逐日 main_net_yi]} → 七个分区。"""
    days = days or _DAYS
    lake = tmp_path / "lake" / "moneyflow"
    for i, day in enumerate(days):
        _write_moneyflow(lake, day, {code: vals[i] for code, vals in series.items()})
    return lake


def _population(tmp_path: Path, rows: list[tuple[str, str]]) -> Path:
    """(date, code) → 一份最小 `gate_participation_v3.csv`(真列名,只留用得到的)。"""
    path = tmp_path / "gate_participation_v3.csv"
    pd.DataFrame([{"cohort_version": "v3", "date": d, "gate": "主力真在", "code": c,
                   "n_gates_failed": 1, "sole_killer": True, "tradable": True,
                   "mature": True, "outcome": "CORRECT", "source": "gate_fires_csv",
                   "ruler": "gap_c1_o2"} for d, c in rows]).to_csv(path, index=False)
    return path


def _scan_root(tmp_path: Path, day_ratio: dict[str, dict[str, tuple[float, float]]]) -> Path:
    """{date: {code: (main_net_ratio, main_inflow_yi)}} → 逐日 `L1_scored_full.csv`。"""
    root = tmp_path / "scan"
    for date, rows in day_ratio.items():
        day = root / date
        day.mkdir(parents=True, exist_ok=True)
        pd.DataFrame([{"code": c, "main_net_ratio": r, "main_inflow_yi": a}
                      for c, (r, a) in rows.items()]).to_csv(
            day / "L1_scored_full.csv", index=False)
    return root


def _seed_registry(tmp_path: Path) -> Path:
    """两条实验的最小合法 registry(spec 形状与生产一致,值收敛到测试可读)。"""
    path = tmp_path / "registry.json"
    registry.set_stable_baseline(path, name="test-baseline", pointer="p@1",
                                 content_hash="0" * 64, approved_by="tester",
                                 approved_at="2026-08-01T00:00:00+08:00")
    guards = {d: [{"metric": "m", "op": "gt", "value": 0}]
              for d in registry.GUARD_DOMAINS}
    for exp_id, kind in ((EXP1, "shadow_gate"), (EXP2, "shadow_channel")):
        registry.register_experiment(path, {
            "id": exp_id, "title": exp_id, "trial_family": exp_id,
            "definition": {"challenger": "x"},
            "start_date": "2026-08-01", "expires_date": "2026-11-30",
            "primary_metric": "m",
            "promotion_guards": guards, "rollback_guards": guards,
            "challenger_pointer": {"kind": kind, "pointer": "p", "content_hash": "1" * 64},
            "minimums": {"forward_days": 20, "mature_events": 50,
                         "unique_events": 50, "regimes": 2},
            "rollback_window_runs": 5,
        }, registered_at="2026-08-01T00:00:00+08:00")
    return path


# ── 1. PIT 五交易日窗 ───────────────────────────────────────────────────────


def test_window_is_five_trading_days_ending_at_t(tmp_path):
    lake = _lake(tmp_path, {"000001": [9.0, 9.0, 1.0, 1.0, 1.0, 1.0, 1.0]})
    window = mainflow5d.window_days("2026-08-05", lake=lake)
    assert window == ["20260730", "20260731", "20260803", "20260804", "20260805"]


def test_window_skips_non_trading_days_not_calendar_days(tmp_path):
    """08-01/08-02 是周末,湖里没有分区 —— 窗口必须回溯到 07-30,不是 08-01。"""
    lake = _lake(tmp_path, {"000001": [1.0] * 7})
    assert "20260801" not in mainflow5d.window_days("2026-08-05", lake=lake)
    assert mainflow5d.window_days("2026-08-05", lake=lake)[0] == "20260730"


def test_missing_any_day_makes_the_code_unmeasured(tmp_path):
    """缺任一日 → 该票 UNMEASURED(不用 4 天凑数、不联网补)。"""
    lake = _lake(tmp_path, {"000001": [1.0] * 7, "000002": [1.0] * 7})
    frame = pd.read_parquet(lake / "20260803.parquet")
    frame[frame["ts_code"] != "000002.SZ"].to_parquet(lake / "20260803.parquet", index=False)
    got = mainflow5d.load_window("2026-08-05", ["000001", "000002"], lake=lake)
    assert got["000001"]["status"] == "MEASURED"
    assert got["000002"]["status"] == "UNMEASURED"
    assert got["000002"]["missing_days"] == ["20260803"]


def test_short_lake_history_makes_everything_unmeasured(tmp_path):
    """湖里连 5 个交易日都不够 → 全体 UNMEASURED,而不是拿 3 天当 5 天。"""
    lake = _lake(tmp_path, {"000001": [1.0] * 3}, days=_DAYS[:3])
    got = mainflow5d.load_window("2026-07-30", ["000001"], lake=lake)
    assert got["000001"]["status"] == "UNMEASURED"


def test_loader_never_touches_the_network(tmp_path, monkeypatch):
    """assemble 不得联网补(Wave10 Gate0 裁决表原话)——湖之外任何取数入口都不许被调。"""
    import autoresearch.data.tushare_source as ts

    def _boom(*_a, **_k):
        raise AssertionError("PIT loader 联网了")

    monkeypatch.setattr(ts, "_ts_call", _boom, raising=False)
    lake = _lake(tmp_path, {"000001": [1.0] * 7})
    assert mainflow5d.load_window("2026-08-05", ["000001"], lake=lake)["000001"]["status"] == "MEASURED"


# ── 2. challenger 判据(逐字 spec) ──────────────────────────────────────────


@pytest.mark.parametrize("values,distortion,expected", [
    ([1.0, 1.0, 1.0, -0.5, -0.5], False, True),      # sum=+2.0>0 ∧ pos=3 ∧ ¬失真 → PASS
    ([1.0, 1.0, -3.0, 1.0, 1.0], False, True),       # sum=+1.0>0 ∧ pos=4 → PASS(单日大出不否)
    ([5.0, -1.0, -1.0, -1.0, -1.0], False, False),   # sum=+1.0>0 但 pos=1 < 3 → 拒(持续性)
    ([1.0, 1.0, 1.0, -5.0, 1.0], False, False),      # pos=4 但 sum=−1.0 ≤ 0 → 拒(净额)
    ([0.0, 0.0, 0.0, 0.0, 0.0], False, False),       # sum=0 不算 >0 → 拒(边界)
    ([1.0, 1.0, 1.0, 1.0, 1.0], True, False),        # 前两条件全过但失真 → 拒(第三条真有牙)
])
def test_challenger_rule_is_the_spec_verbatim(values, distortion, expected):
    assert mainflow5d.challenger_pass(values, main_distortion=distortion) is expected


def test_positive_days_counts_strictly_positive_days():
    """`positive_days` 数的是 >0 的日数;0 不算正(边界写死,免得下一个人改成 >=0)。"""
    assert mainflow5d.positive_days([1.0, 0.0, 0.0, 1.0, 1.0]) == 3
    assert mainflow5d.positive_days([1.0, 0.0, 0.0, 0.0, 1.0]) == 2


def test_missing_distortion_input_yields_unmeasured(tmp_path):
    """判据要三个输入,少一个就不许猜 —— 当日 `L1_scored_full.csv` 读不到该票的
    `main_net_ratio`/`main_inflow_yi` → UNMEASURED,不按 `main_distortion=False` 放行。"""
    lake = _lake(tmp_path, {"000001": [1.0] * 7})
    pop = _population(tmp_path, [("2026-08-05", "000001")])
    scan_root = _scan_root(tmp_path, {"2026-08-05": {}})       # 当日帧里没有这只票
    rows = mainflow5d.verdict_rows("2026-08-05", population_path=pop,
                                   scan_root=scan_root, lake=lake)
    assert [r["status"] for r in rows] == ["UNMEASURED"]
    assert rows[0]["challenger_pass"] is None


def test_verdict_row_shape_and_ruler(tmp_path):
    lake = _lake(tmp_path, {"000001": [1.0] * 7})
    pop = _population(tmp_path, [("2026-08-05", "000001")])
    scan_root = _scan_root(tmp_path, {"2026-08-05": {"000001": (0.05, 1.2)}})
    rows = mainflow5d.verdict_rows("2026-08-05", population_path=pop,
                                   scan_root=scan_root, lake=lake)
    assert len(rows) == 1
    row = rows[0]
    assert row["date"] == "2026-08-05" and row["code"] == "000001"
    assert row["challenger_pass"] is True and row["status"] == "MEASURED"
    assert row["ruler"] == "gap_c1_o2"
    assert row["window"] == ["20260730", "20260731", "20260803", "20260804", "20260805"]
    assert row["experiment_id"] == EXP1


def test_population_is_the_main_flow_gate_rows_only(tmp_path):
    """人口 = `gate_participation_v3.csv` 里 gate == '主力真在' 的行(spec 原文)。"""
    path = tmp_path / "pop.csv"
    pd.DataFrame([
        {"cohort_version": "v3", "date": "2026-08-05", "gate": "主力真在", "code": "000001"},
        {"cohort_version": "v3", "date": "2026-08-05", "gate": "估值不透支", "code": "000002"},
    ]).to_csv(path, index=False)
    lake = _lake(tmp_path, {"000001": [1.0] * 7, "000002": [1.0] * 7})
    scan_root = _scan_root(tmp_path, {"2026-08-05": {"000001": (0.05, 1.2),
                                                     "000002": (0.05, 1.2)}})
    rows = mainflow5d.verdict_rows("2026-08-05", population_path=path,
                                   scan_root=scan_root, lake=lake)
    assert [r["code"] for r in rows] == ["000001"]


# ── 3. 会变的量:两条实验的 observations 各 +1 ────────────────────────────────


def _run_one_day(tmp_path, *, date="2026-08-05", channels_rows=None):
    lake = _lake(tmp_path, {"000001": [1.0] * 7, "000002": [-1.0] * 7})
    pop = _population(tmp_path, [(date, "000001"), (date, "000002")])
    scan_root = _scan_root(tmp_path, {date: {"000001": (0.05, 1.2),
                                             "000002": (0.05, 1.2)}})
    shadow = scan_root / date / "shadow"
    shadow.mkdir(parents=True, exist_ok=True)
    rows = channels_rows if channels_rows is not None else [
        {"channel": "sector_momentum", "code": "000001", "channel_rank": 1, "channel_score": 12.0},
        {"channel": "composite", "code": "000002", "channel_rank": 1, "channel_score": 70.0},
    ]
    pd.DataFrame(rows, columns=["channel", "code", "channel_rank", "channel_score"]).to_csv(
        shadow / "L1_channels_plus_sectormom.csv", index=False)
    reg = _seed_registry(tmp_path)
    return dict(date=date, population_path=pop, scan_root=scan_root, lake=lake,
                registry_path=reg, ledger_path=tmp_path / "exp1.jsonl")


def test_one_day_run_adds_one_observation_to_each_experiment(tmp_path):
    """**会变的量**:跑一天 → 两条实验的 observations 各 +1。这条测试是本 task 的心跳探针
    —— 数据腿哪天悄悄不产出了,它立刻变红(2026-07-16 空转 2 周的判例)。"""
    kwargs = _run_one_day(tmp_path)
    before = [len(registry.get_experiment(kwargs["registry_path"], e)["observations"])
              for e in (EXP1, EXP2)]
    assert before == [0, 0]
    mainflow5d.observe_day(**kwargs)
    after = [len(registry.get_experiment(kwargs["registry_path"], e)["observations"])
             for e in (EXP1, EXP2)]
    assert after == [1, 1], f"observations 必须各 +1,实为 {before} → {after}"


def test_observation_is_idempotent_per_date(tmp_path):
    kwargs = _run_one_day(tmp_path)
    mainflow5d.observe_day(**kwargs)
    mainflow5d.observe_day(**kwargs)
    for exp in (EXP1, EXP2):
        assert len(registry.get_experiment(kwargs["registry_path"], exp)["observations"]) == 1


def test_same_day_with_different_facts_is_rejected(tmp_path):
    kwargs = _run_one_day(tmp_path)
    mainflow5d.observe_day(**kwargs)
    with pytest.raises(registry.RegistryError):
        registry.append_observation(kwargs["registry_path"], EXP1,
                                    facts={"n": 999}, key=kwargs["date"])


def test_maturity_clock_starts_at_first_observation_not_preregistration(tmp_path):
    """registry note 必须写死「成熟门自**首条观测**起算」—— 本波诊断出来的病就是
    「预注册 6 天了、可它一条观测都没有」被当成「已经等了 6 天」。"""
    kwargs = _run_one_day(tmp_path)
    mainflow5d.observe_day(**kwargs)
    for exp in (EXP1, EXP2):
        clock = registry.get_experiment(kwargs["registry_path"], exp)["maturity_clock"]
        assert "首条观测" in clock["note"] and "预注册" in clock["note"]
        assert clock["first_observation_key"] == kwargs["date"]
        assert clock["n_observations"] == 1


def test_ledger_jsonl_is_appended_with_one_row_per_population_row(tmp_path):
    kwargs = _run_one_day(tmp_path)
    mainflow5d.observe_day(**kwargs)
    lines = [json.loads(l) for l in
             Path(kwargs["ledger_path"]).read_text(encoding="utf-8").splitlines() if l.strip()]
    assert len(lines) == 2
    assert {r["code"] for r in lines} == {"000001", "000002"}
    assert {r["challenger_pass"] for r in lines} == {True, False}


def test_exp2_observation_counts_the_shadow_channel_long_table(tmp_path):
    kwargs = _run_one_day(tmp_path)
    mainflow5d.observe_day(**kwargs)
    obs = registry.get_experiment(kwargs["registry_path"], EXP2)["observations"][0]
    assert obs["facts"]["n_recalled"] == 1        # sector_momentum 那一行
    assert obs["facts"]["n_unique"] == 1          # 只被本路召回
    assert obs["facts"]["variant"] == "plus_sectormom"


def test_exp2_records_absent_when_shadow_table_missing(tmp_path):
    """影子长表缺席 → 观测仍然要记(记 ABSENT),不是静默不记 —— 「没跑」和「跑了但没
    结果」必须区分得开,否则又是一个「死了也像活着」。"""
    kwargs = _run_one_day(tmp_path)
    (kwargs["scan_root"] / kwargs["date"] / "shadow"
     / "L1_channels_plus_sectormom.csv").unlink()
    mainflow5d.observe_day(**kwargs)
    obs = registry.get_experiment(kwargs["registry_path"], EXP2)["observations"][0]
    assert obs["facts"]["source"] == "ABSENT"
    assert obs["facts"]["n_recalled"] is None


# ── 4. 影子观测不得污染 rollback_watch 的观察窗 ──────────────────────────────


def test_shadow_observations_do_not_shorten_the_rollback_window(tmp_path):
    """影子行混进 `observations` 后,`rollback_watch` 的 `window.observed` 只能数它自己的
    assessment —— 否则 rollback_window_runs=5 会被 4 条影子行提前撑满(直接影响
    ACCEPT_BASELINE 的触发)。"""
    from autoresearch.learning import rollback_watch

    path = _seed_registry(tmp_path)
    for i in range(4):
        registry.append_observation(path, EXP1, facts={"i": i}, key=f"2026-08-0{i + 1}")

    # 走完 PREREGISTERED → ACTIVE 的最短合法路径
    record = registry.get_experiment(path, EXP1)
    payload = registry.load_registry(path)
    payload["experiments"][EXP1]["status"] = "RECOMMENDED"
    payload["experiments"][EXP1]["latest_evaluation"] = {
        "evaluation_hash": "2" * 64, "evaluated_at": "2026-08-06T00:00:00+08:00"}
    registry.write_registry(path, payload)
    registry.approve_experiment(path, EXP1, approved_by="tester",
                                approved_at="2026-08-06T01:00:00+08:00")
    registry.activate_experiment(path, EXP1, activated_by="tester",
                                 activated_at="2026-08-06T02:00:00+08:00")
    assert record["rollback_window_runs"] == 5

    got = rollback_watch.observe(path, EXP1, {"metrics": {"m": 1}}, run_id="r1",
                                 observed_at="2026-08-06T03:00:00+08:00")
    assert got["window"]["observed"] == 1, (
        "rollback 观察窗只数 rollback assessment;4 条影子观测不得把它顶到 5")
    assert got["window"]["all_pass"] is True, "影子行不带 status,不得让 all_pass 塌成 False"
