"""E1:效率基线读模型 + 宿主能力报告。

这组测试守的是同一件事的两面:**不知道就说不知道**。计量缺了写 null 而不是 0,能力没试过
记 NO_EVIDENCE 而不是 False —— 两处一旦含糊,后面「省了多少」和「能不能换 runner」的判断
就都建在沙上了。
"""
import pytest

from autoresearch.research import efficiency_baseline as eb


def observation(**changes):
    row = {"run_id": "20260901_2100", "measurement_status": "MEASURED",
           "weighted_input_proxy": 6_000_000.0, "interactive_wall_s": 3600.0,
           "estimated_usd": 42.0, "budget_band": "GREEN", "real_scan": True}
    return dict(row, **changes)


def record(**changes):
    row = {"observation": observation(), "engine": "claude", "mature_finalists": 8,
           "attempted_tasks": 10, "failed_tasks": 1}
    return dict(row, **changes)


def test_observation_keys_match_the_production_artifact():
    """键名以 `scan.budget.observe_run` 落的产物为准,不是计划草稿里的名字。"""
    import inspect

    from autoresearch.scan import budget
    source = inspect.getsource(budget.observe_run)
    for key in eb.OBSERVATION_KEYS:
        assert f'"{key}"' in source, f"{key} 不在生产观测里"


def test_complete_row_computes_per_finalist_cost():
    row = eb.efficiency_row(observation(), engine="claude", mature_finalists=8,
                            attempted_tasks=10, failed_tasks=1)
    assert row["coverage"] == "COMPLETE"
    assert row["proxy_per_finalist"] == pytest.approx(750_000.0)
    assert row["failure_rate"] == pytest.approx(0.1)


def test_unmeasured_run_reports_null_not_zero():
    """「没量到」不是「没花钱」。给 0,后面每一句『省了 x%』都建在沙上。"""
    row = eb.efficiency_row(observation(measurement_status="UNMEASURED",
                                        weighted_input_proxy=None, estimated_usd=None),
                            engine="claude", mature_finalists=8,
                            attempted_tasks=10, failed_tasks=1)
    assert row["weighted_input_proxy"] is None
    assert row["estimated_usd"] is None and row["proxy_per_finalist"] is None
    assert row["measurement_status"] == "UNMEASURED"


def test_measured_flag_wins_over_a_present_number():
    """产物里有数但 measurement_status 是 UNMEASURED —— 以状态为准,不采信那个数。"""
    row = eb.efficiency_row(observation(measurement_status="UNMEASURED"),
                            engine="claude", mature_finalists=8,
                            attempted_tasks=10, failed_tasks=1)
    assert row["weighted_input_proxy"] is None


def test_zero_finalists_does_not_divide():
    row = eb.efficiency_row(observation(), engine="claude", mature_finalists=0,
                            attempted_tasks=10, failed_tasks=1)
    assert row["proxy_per_finalist"] is None


@pytest.mark.parametrize("field", sorted(eb.CALLER_SUPPLIED))
def test_every_missing_caller_field_is_named(field):
    kwargs = {"engine": "claude", "mature_finalists": 8,
              "attempted_tasks": 10, "failed_tasks": 1}
    kwargs[field] = None
    row = eb.efficiency_row(observation(), **kwargs)
    assert row["coverage"].startswith("MISSING:") and field in row["coverage"]


@pytest.mark.parametrize("key", sorted(eb.OBSERVATION_KEYS))
def test_every_missing_observation_key_is_named(key):
    obs = observation()
    del obs[key]
    row = eb.efficiency_row(obs, engine="claude", mature_finalists=8,
                            attempted_tasks=10, failed_tasks=1)
    assert key in row["coverage"]


def test_failed_more_than_attempted_is_a_data_error():
    with pytest.raises(ValueError):
        eb.efficiency_row(observation(), engine="claude", mature_finalists=8,
                          attempted_tasks=2, failed_tasks=5)


def test_zero_attempted_tasks_has_no_failure_rate():
    row = eb.efficiency_row(observation(), engine="claude", mature_finalists=8,
                            attempted_tasks=0, failed_tasks=0)
    assert row["failure_rate"] is None


def test_rows_helper_maps_every_caller_field():
    rows = eb.efficiency_rows([record(), record(engine="codex")])
    assert [row["engine"] for row in rows] == ["claude", "codex"]


# ───────────────────────── cohort ─────────────────────────

def test_cohort_never_merges_engines_or_modes():
    rows = eb.efficiency_rows([
        record(),
        record(engine="codex"),
        record(observation=observation(real_scan=False)),
    ])
    summary = eb.cohort_summary(rows)
    keys = {(g["engine"], g["real_scan"]) for g in summary["groups"]}
    assert keys == {("claude", True), ("codex", True), ("claude", False)}


def test_incomplete_rows_are_excluded_and_counted():
    rows = eb.efficiency_rows([record(), record(mature_finalists=None)])
    summary = eb.cohort_summary(rows)
    assert summary["excluded"] == 1
    assert sum(g["n"] for g in summary["groups"]) == 1


def test_a_computable_but_incomplete_row_is_still_excluded():
    """只缺 `attempted_tasks` 时单票成本照样算得出来 —— 但这一行的覆盖不全,不能进 cohort。

    这条是变异探针逼出来的:原来那个用例缺的是 `mature_finalists`,它同时让
    `proxy_per_finalist` 变成 None,于是「按 coverage 排除」这一腿零鉴别力 —— 删掉它测试
    照样全绿。
    """
    (row,) = eb.efficiency_rows([record(attempted_tasks=None)])
    assert row["proxy_per_finalist"] is not None      # 算得出来
    assert row["coverage"].startswith("MISSING:")     # 但覆盖不全
    summary = eb.cohort_summary([row])
    assert summary == {"groups": [], "excluded": 1}


def test_median_of_an_even_cohort_averages_the_middle_two():
    rows = eb.efficiency_rows([
        record(mature_finalists=4),      # 1_500_000
        record(mature_finalists=8),      #   750_000
    ])
    (group,) = eb.cohort_summary(rows)["groups"]
    assert group["median_proxy_per_finalist"] == pytest.approx(1_125_000.0)


def test_empty_cohort_is_empty_not_zero():
    assert eb.cohort_summary([]) == {"groups": [], "excluded": 0}


# ───────────────────────── 宿主能力 ─────────────────────────

def test_capability_without_evidence_is_false_and_says_why():
    """「没试过」与「试过不行」在决策上完全不同;只报布尔会把前者读成后者。"""
    report = eb.capability_report({})
    assert set(report) == set(eb.CAPABILITIES)
    assert all(item == {"available": False, "evidence": "NO_EVIDENCE"}
               for item in report.values())


def test_claiming_available_without_evidence_does_not_stick():
    report = eb.capability_report({"deterministic_exec": {"available": True}})
    assert report["deterministic_exec"] == {"available": False, "evidence": "NO_EVIDENCE"}


def test_evidence_backed_capability_is_recorded():
    report = eb.capability_report({
        "deterministic_exec": {"available": True, "evidence": "tmp_path 里跑通 python -c,exit=0"}})
    assert report["deterministic_exec"]["available"] is True
    assert "exit=0" in report["deterministic_exec"]["evidence"]


def test_unknown_capability_name_is_refused():
    with pytest.raises(ValueError):
        eb.capability_report({"gpu_access": {"available": True, "evidence": "x"}})


def test_runner_needs_all_four_capabilities():
    full = {name: {"available": True, "evidence": "probe"} for name in eb.CAPABILITIES}
    assert eb.runner_is_unblocked(eb.capability_report(full))
    partial = dict(full)
    partial["inference_handoff"] = {"available": False, "evidence": "宿主无会话交接原语"}
    assert not eb.runner_is_unblocked(eb.capability_report(partial))


def test_deterministic_exec_probe_runs_a_real_command(tmp_path):
    """这一项自己就能实测:在 tmp_path 起一个无网络的确定性进程。"""
    import subprocess
    import sys
    marker = tmp_path / "probe.txt"
    done = subprocess.run([sys.executable, "-c",
                           f"open({str(marker)!r}, 'w').write('ok')"],
                          cwd=tmp_path, capture_output=True, check=False)
    report = eb.capability_report({"deterministic_exec": {
        "available": done.returncode == 0 and marker.read_text() == "ok",
        "evidence": f"subprocess exit={done.returncode}, cwd={tmp_path.name}"}})
    assert report["deterministic_exec"]["available"] is True
    assert not eb.runner_is_unblocked(report)      # 单项为真不等于可以换 runner
