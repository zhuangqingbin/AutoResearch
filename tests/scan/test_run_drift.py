"""跨 run 的身份漂移与相对预算带(2026-10-03)。

真实前科:2026-09-22 `opus` 别名改指 Opus 5.5,同样的 `effort: max` 下每张卡的输出 token 中位数
1.9 万 → 5.1 万,整场成本 +61%。当时的预算带是绝对阈值、每场都 RED,对账按家族比,没有任何东西
把「这一场和上一场不是同一个东西」说出来。
"""
from __future__ import annotations

import json

import pytest

from autoresearch.scan import run_drift as rd
from autoresearch.trace import usage_harvest as U

PROMPTS = {
    ".claude/agents/l4-card.md": "a" * 64,
    ".claude/agents/l4-intel.md": "b" * 64,
    ".claude/skills/scan-market/pinned.jsonc": "c" * 64,
    ".claude/skills/scan-market/scan_config.jsonc": "d" * 64,
}
POLICY = {"window": 10, "min_runs": 3, "warn_ratio": 1.5, "alarm_ratio": 2.0}


def _row(agent, model, *, effort="max", output=20_000, role="subagent", version="2.1.283",
         messages=6, weighted=100_000):
    return {
        "role": role, "agent": agent, "model": model, "models": [model], "effort": effort,
        "host_version": version, "status": "SUCCEEDED", "messages": messages, "input": 10,
        "output": output, "cache_read": 1000, "cache_create": 100, "cache_create_5m": 100,
        "cache_create_1h": 0, "weighted_in": weighted, "failure_count": 0, "retry_count": 0,
        "discarded": False, "estimated_usd": 1.0, "discarded_usd": 0.0,
    }


def _ledger(*, card="claude-opus-5", intel="claude-sonnet-5", card_out=(18_000, 19_000, 21_000),
            intel_out=(25_000, 26_000), version="2.1.283", effort="max", shells=2):
    rows = [_row("(主会话)", "claude-opus-5", role="main", version=version, output=60_000)]
    rows += [_row("l4-card", card, output=o, version=version, effort=effort) for o in card_out]
    rows += [_row("l4-intel", intel, output=o, version=version) for o in intel_out]
    rows += [_row("general-purpose", "claude-sonnet-5", effort="low", output=600, version=version,
                  messages=2) for _ in range(shells)]
    return U.build_ledger(rows, source="fixture")


def _identity(ledger, prompts=PROMPTS, config_hash="cfg-1", engine="claude"):
    return rd.identity(ledger, prompt_hashes=prompts, config_hash=config_hash, engine=engine)


# ── identity / cohort ────────────────────────────────────────────────────────


def test_identity_lists_research_agents_and_keeps_relay_and_main_out_of_the_cohort():
    got = _identity(_ledger())
    assert got["host_versions"] == ["2.1.283"]
    assert got["main_models"] == ["claude-opus-5"]
    assert got["agents"] == {
        "l4-card": {"models": ["claude-opus-5"], "efforts": ["max"]},
        "l4-intel": {"models": ["claude-sonnet-5"], "efforts": ["max"]},
    }
    assert got["relay_agents"] == {"general-purpose": {"models": ["claude-sonnet-5"], "efforts": ["low"]}}
    assert got["agent_prompts"] == {
        ".claude/agents/l4-card.md": "a" * 64, ".claude/agents/l4-intel.md": "b" * 64}
    assert got["engine"] == "claude"
    assert len(got["cohort_key"]) == 12 and len(got["model_cohort"]) == 12


def test_model_cohort_ignores_contract_edits_but_cohort_key_does_not():
    base = _identity(_ledger())
    edited = _identity(_ledger(), prompts={**PROMPTS, ".claude/agents/l4-card.md": "e" * 64})
    assert edited["model_cohort"] == base["model_cohort"]
    assert edited["cohort_key"] != base["cohort_key"]


def test_editing_an_agent_that_did_not_run_does_not_move_the_cohort():
    base = _identity(_ledger())
    other = _identity(_ledger(), prompts={**PROMPTS, ".claude/agents/dossier-init.md": "9" * 64})
    assert other["cohort_key"] == base["cohort_key"]
    assert ".claude/agents/dossier-init.md" not in other["agent_prompts"]


@pytest.mark.parametrize("engine,agent,paths", [
    ("claude", "macro-brief", [".claude/agents/macro-brief.md"]),
    ("codex", "strategist", [".codex/agents/strategist.toml", ".claude/agents/macro-brief.md"]),
    ("codex", "l4-ensemble", [".codex/agents/ens_review.toml", ".claude/agents/l4-card.md"]),
    ("codex", "sector-brief", [".codex/agents/sector_brief.toml", ".claude/agents/sector-brief.md"]),
    ("claude", "(未标注)", []),
])
def test_agent_names_resolve_to_contract_files_only_through_the_registry(engine, agent, paths):
    assert rd._prompt_paths(engine, agent) == paths


def test_contract_facts_are_none_when_the_contract_did_not_record_them():
    assert rd.contract_facts(None) == {"prompt_hashes": {}, "config_hash": None, "code_sha": None,
                                       "schema_hash": None}
    facts = rd.contract_facts({"git_sha": "abc", "git_dirty": True, "config_hash": "c" * 64,
                               "artifact_schema_versions": {"x": 1}, "prompt_hashes": PROMPTS})
    assert facts["code_sha"] == "abc+dirty"
    assert facts["config_hash"] == "c" * 64 and len(facts["schema_hash"]) == 12


def test_codex_identity_reads_its_own_agent_definitions_and_relays():
    """Codex 的 agent 契约是 `.codex/agents/*.toml`,壳叫 gp-shell / trace-control。

    拿 `.claude/agents/*.md` 的哈希给 Codex 的 run 定 cohort,等于用别人的指令证明自己没变。
    """
    prompts = {**PROMPTS, ".codex/agents/l4_card.toml": "9" * 64}
    rows = [_row("l4-card", "gpt-5.6-sol", effort="xhigh", version="0.159.3"),
            _row("gp-shell", "gpt-5.6-luna", effort="low", version="0.159.3"),
            _row("trace-control", "gpt-5.6-luna", effort="low", version="0.159.3")]
    got = _identity(U.build_ledger(rows), prompts=prompts, engine="codex")

    assert got["engine"] == "codex"
    # Codex 的 toml 只是壳,卡片契约照读 `.claude/agents/l4-card.md`:两份都进身份。
    assert got["agent_prompts"] == {".codex/agents/l4_card.toml": "9" * 64,
                                    ".claude/agents/l4-card.md": "a" * 64}
    assert sorted(got["relay_agents"]) == ["gp-shell", "trace-control"]
    assert list(got["agents"]) == ["l4-card"]
    # 同样的 agent 事实换一个引擎,不是同一个 cohort。
    assert got["cohort_key"] != _identity(U.build_ledger(rows), prompts=prompts)["cohort_key"]


def test_cohort_key_moves_with_model_effort_and_agent_prompts_only():
    base = _identity(_ledger())["cohort_key"]
    assert _identity(_ledger(card="claude-opus-5-5"))["cohort_key"] != base
    assert _identity(_ledger(effort="high"))["cohort_key"] != base
    edited = {**PROMPTS, ".claude/agents/l4-card.md": "e" * 64}
    assert _identity(_ledger(), prompts=edited)["cohort_key"] != base
    # 不该切 cohort 的:宿主版本、持仓清单 / 配置文件哈希、壳的数量、主会话模型、每张卡的 token 数
    pinned = {**PROMPTS, ".claude/skills/scan-market/pinned.jsonc": "f" * 64}
    assert _identity(_ledger(), prompts=pinned)["cohort_key"] == base
    assert _identity(_ledger(), config_hash="cfg-2")["cohort_key"] == base
    assert _identity(_ledger(version="2.1.287"))["cohort_key"] == base
    assert _identity(_ledger(shells=0))["cohort_key"] == base
    assert _identity(_ledger(card_out=(90_000, 91_000, 92_000)))["cohort_key"] == base


def _with(ledger, *rows):
    return U.build_ledger([*ledger["rows"], *rows], source="fixture")


REPAIR_PROMPTS = {**PROMPTS, ".claude/agents/l3-repair.md": "7" * 64}


def test_a_conditional_role_running_or_not_does_not_move_either_cohort_key():
    """复审 I-2:L3 修复只在校验没过时跑;它在不在场让 cohort 翻面,会把 Q8 十日窗无故重置。"""
    base = _identity(_ledger(), prompts=REPAIR_PROMPTS)
    repaired = _identity(_with(_ledger(), _row("l3-repair", "claude-opus-5", effort="medium")),
                         prompts=REPAIR_PROMPTS)
    assert "l3-repair" in repaired["agents"]                      # 事实照记
    assert ".claude/agents/l3-repair.md" in repaired["agent_prompts"]
    assert repaired["model_cohort"] == base["model_cohort"]
    assert repaired["cohort_key"] == base["cohort_key"]
    codex = [_row("l4-card", "gpt-5.6-sol", effort="xhigh", version="0.159.3")]
    lone = _identity(U.build_ledger(codex), engine="codex")
    reviewed = _identity(U.build_ledger(
        [*codex, _row("l4-ensemble", "gpt-5.6-sol", effort="xhigh", version="0.159.3")]),
        engine="codex")
    assert reviewed["model_cohort"] == lone["model_cohort"]


def test_a_run_with_only_conditional_agents_has_no_cohort():
    got = _identity(U.build_ledger([_row("l3-repair", "claude-opus-5", effort="medium")]))
    assert got["agents"] and got["model_cohort"] is None and got["cohort_key"] is None


def test_identity_of_an_unmeasured_ledger_claims_nothing():
    got = rd.identity({}, prompt_hashes=None, config_hash=None)
    assert got["agents"] == {} and got["cohort_key"] is None and got["host_versions"] == []


def test_identity_is_derived_from_rows_when_an_old_ledger_has_no_identity_block():
    ledger = _ledger()
    old = {key: value for key, value in ledger.items() if key != "identity"}
    for row in old["rows"]:
        row.pop("models"), row.pop("host_version")
    got = _identity(old)
    assert got["agents"]["l4-card"] == {"models": ["claude-opus-5"], "efforts": ["max"]}
    assert got["host_versions"] == []                      # 老账本没记版本:不猜


# ── drift ────────────────────────────────────────────────────────────────────


def test_no_previous_run_is_no_baseline_not_same():
    assert rd.drift(None, _identity(_ledger())) == {
        "status": "NO_BASELINE", "baseline_run": None, "changes": []}


def test_identical_identity_is_same():
    prev = {"run_id": "r1", "identity": _identity(_ledger())}
    assert rd.drift(prev, _identity(_ledger())) == {
        "status": "SAME", "baseline_run": "r1", "changes": []}


def test_the_real_20260922_shape_is_reported_as_two_model_changes_and_a_host_change():
    prev = {"run_id": "20260917T125201296461Z", "identity": _identity(_ledger())}
    cur = _identity(_ledger(card="claude-opus-5-5", intel="claude-sonnet-5-5", version="2.1.284"))

    got = rd.drift(prev, cur)

    assert got["status"] == "CHANGED" and got["baseline_run"] == "20260917T125201296461Z"
    assert got["changes"] == [
        {"kind": "host", "scope": "宿主", "before": ["2.1.283"], "after": ["2.1.284"]},
        {"kind": "model", "scope": "l4-card", "before": ["claude-opus-5"], "after": ["claude-opus-5-5"]},
        {"kind": "model", "scope": "l4-intel", "before": ["claude-sonnet-5"], "after": ["claude-sonnet-5-5"]},
    ]


def test_a_previous_run_from_the_other_engine_is_not_a_baseline():
    prev = {"run_id": "r-codex", "identity": _identity(_ledger(), engine="codex")}
    assert rd.drift(prev, _identity(_ledger()))["status"] == "NO_BASELINE"


def test_effort_prompt_config_and_agent_set_changes_are_each_named():
    prev = {"run_id": "r1", "identity": _identity(_ledger())}
    edited = {**PROMPTS, ".claude/agents/l4-card.md": "e" * 64}
    cur = _identity(_ledger(effort="high", intel_out=()), prompts=edited, config_hash="cfg-2")

    kinds = [(c["kind"], c["scope"]) for c in rd.drift(prev, cur)["changes"]]

    assert kinds == [("agent_set", "l4-intel"), ("effort", "l4-card"),
                     ("prompt", ".claude/agents/l4-card.md"), ("config", "scan_config")]


def test_a_conditional_role_appearing_is_not_a_change_but_its_model_change_still_is():
    prev = {"run_id": "r1", "identity": _identity(_ledger(), prompts=REPAIR_PROMPTS)}
    repaired = _identity(_with(_ledger(), _row("l3-repair", "claude-opus-5", effort="medium")),
                         prompts=REPAIR_PROMPTS)
    assert rd.drift(prev, repaired) == {"status": "SAME", "baseline_run": "r1", "changes": []}
    both = {"run_id": "r2", "identity": repaired}
    moved = _identity(_with(_ledger(), _row("l3-repair", "claude-opus-5-5", effort="medium")),
                      prompts=REPAIR_PROMPTS)
    assert [(c["kind"], c["scope"]) for c in rd.drift(both, moved)["changes"]] == [
        ("model", "l3-repair")]


def test_a_contract_recorded_on_only_one_side_is_not_a_change():
    """`PROMPT_SOURCES` 扩表后,上一场没记的文件不能被说成「当时没有、现在有了」。"""
    prev = {"run_id": "r1", "identity": _identity(
        _ledger(), prompts={".claude/agents/l4-card.md": "a" * 64})}
    assert rd.drift(prev, _identity(_ledger()))["status"] == "SAME"


def test_unknown_host_version_on_either_side_is_not_reported_as_a_change():
    old = _ledger()
    for row in old["rows"]:
        row["host_version"] = "—"
    prev = {"run_id": "r1", "identity": _identity(U.build_ledger(old["rows"]))}
    assert rd.drift(prev, _identity(_ledger()))["status"] == "SAME"


def test_unmeasured_current_run_is_unmeasured_never_same():
    prev = {"run_id": "r1", "identity": _identity(_ledger())}
    cur = rd.identity({}, prompt_hashes=PROMPTS, config_hash="cfg-1")
    assert rd.drift(prev, cur)["status"] == "UNMEASURED"


# ── canary (research.drift 的生产调用者) ───────────────────────────────────


def test_canary_projects_the_identity_pair_onto_research_drift():
    prev = {"run_id": "r917", "identity": _identity(_ledger(), config_hash="cfg-1")}
    cur = _identity(_ledger(card="claude-opus-5-5"), config_hash="cfg-1")

    got = rd.canary(prev, cur)

    assert got["baseline_run"] == "r917"
    assert got["model_state"] == "CHANGED" and "observed_model" in got["changed_fields"]
    assert got["canary_required"] is True
    assert got["automatic_prompt_update"] is False


def test_canary_ignores_a_conditional_role_present_on_one_side_only():
    prev = {"run_id": "r1", "identity": _identity(_ledger(), prompts=REPAIR_PROMPTS)}
    repaired = _identity(_with(_ledger(), _row("l3-repair", "claude-opus-5", effort="medium")),
                         prompts=REPAIR_PROMPTS)
    got = rd.canary(prev, repaired)
    assert got["model_state"] == "SAME_OBSERVED_ID" and got["changed_fields"] == []
    # 两边都在场时,条件角色的模型变化照样进 canary
    moved = _identity(_with(_ledger(), _row("l3-repair", "claude-opus-5-5", effort="medium")),
                      prompts=REPAIR_PROMPTS)
    assert "observed_model" in rd.canary({"run_id": "r2", "identity": repaired}, moved)["changed_fields"]
    # 常设角色缺席照样算(intel 被关掉 = 真的变了)
    no_intel = _identity(_ledger(intel_out=()), prompts=REPAIR_PROMPTS)
    assert "observed_model" in rd.canary(prev, no_intel)["changed_fields"]


def test_canary_counts_unknown_fields_and_is_none_without_a_baseline():
    cur = _identity(_ledger())
    assert rd.canary(None, cur) is None
    same = rd.canary({"run_id": "r1", "identity": _identity(_ledger())}, cur)
    # 两边都没记代码版本与 schema:research.drift 把未知也算进 canary —— 比 drift 保守。
    assert same["changed_fields"] == [] and set(same["unknown_fields"]) == {"source_schema_hash", "code_sha"}
    assert same["canary_required"] is True


# ── usage_shape / relative ───────────────────────────────────────────────────


def test_usage_shape_is_per_agent_medians_plus_totals():
    shape = rd.usage_shape(_ledger())
    assert shape["measured"] is True
    assert shape["agents"]["l4-card"] == {"n": 3, "output_median": 19_000, "output_total": 58_000,
                                          "messages_median": 6}
    assert shape["agents"]["(主会话)"]["n"] == 1
    assert shape["output_total"] == 60_000 + 58_000 + 51_000 + 1_200


def test_usage_shape_of_an_unmeasured_ledger_is_not_zero():
    assert rd.usage_shape({}) == {"schema_version": 1, "measured": False, "output_total": None,
                                  "weighted_input": None, "agents": {}}


def _obs(run, ledger, *, real=True):
    return {"run_id": run, "analysis_date": "2026-09-01", "real_scan": real,
            "usage_shape": rd.usage_shape(ledger)}


def test_relative_needs_min_runs_before_it_says_anything():
    history = [_obs("r1", _ledger()), _obs("r2", _ledger())]
    got = rd.relative(rd.usage_shape(_ledger()), history, POLICY)
    assert got["status"] == "NO_BASELINE" and got["n_baseline"] == 2


def test_relative_policy_defaults_come_from_the_budget_config_defaults():
    from autoresearch.scan.budget import DEFAULT_BUDGETS

    history = [_obs(f"r{i}", _ledger()) for i in range(DEFAULT_BUDGETS["relative"]["min_runs"])]
    assert rd.relative(rd.usage_shape(_ledger()), history)["status"] == "NORMAL"
    assert rd.relative(rd.usage_shape(_ledger()), history[:-1])["status"] == "NO_BASELINE"


def _cobs(run, ledger):
    obs = _obs(run, ledger)
    obs["identity"] = _identity(ledger)
    return obs


def test_relative_prefers_the_same_model_cohort_once_it_has_enough_runs():
    old = [_cobs(f"old{i}", _ledger()) for i in range(6)]
    new_ledger = _ledger(card="claude-opus-5-5", card_out=(50_000, 50_600, 51_000))
    cohort = _identity(new_ledger)["model_cohort"]

    first = rd.relative(rd.usage_shape(new_ledger), old, POLICY, cohort=cohort)
    assert first["scope"] == "all" and first["status"] == "SPIKE"        # 换代那一场:跳变可见

    settled = old + [_cobs(f"new{i}", new_ledger) for i in range(3)]
    later = rd.relative(rd.usage_shape(new_ledger), settled, POLICY, cohort=cohort)
    assert later["scope"] == "cohort" and later["status"] == "NORMAL"   # 三场之后:只和新常态比
    assert later["n_baseline"] == 3


def test_the_real_jump_is_a_spike_on_card_output_even_if_weighted_input_is_only_elevated():
    history = [_obs(f"r{i}", _ledger()) for i in range(6)]
    now = rd.usage_shape(_ledger(card="claude-opus-5-5", card_out=(46_000, 50_600, 61_000)))

    got = rd.relative(now, history, POLICY)

    assert got["status"] == "SPIKE"
    assert got["worst"] == "output_median:l4-card"
    assert got["metrics"]["output_median:l4-card"] == {
        "value": 50_600, "baseline_median": 19_000, "ratio": 2.66}


@pytest.mark.parametrize("card_out,status", [
    ((19_000, 19_000, 19_000), "NORMAL"),
    ((28_400, 28_400, 28_400), "NORMAL"),       # 1.49x
    ((28_500, 28_500, 28_500), "ELEVATED"),     # 1.50x
    ((37_900, 37_900, 37_900), "ELEVATED"),     # 1.99x
    ((38_000, 38_000, 38_000), "SPIKE"),        # 2.00x
])
def test_relative_thresholds_are_inclusive_at_the_configured_ratio(card_out, status):
    history = [_obs(f"r{i}", _ledger(card_out=(19_000, 19_000, 19_000))) for i in range(5)]
    got = rd.relative(rd.usage_shape(_ledger(card_out=card_out)), history, POLICY)
    assert got["metrics"]["output_median:l4-card"]["baseline_median"] == 19_000
    assert got["status"] == status


def test_relative_uses_only_the_last_window_of_measured_real_scans():
    cheap = [_obs(f"old{i}", _ledger(card_out=(5_000, 5_000, 5_000))) for i in range(20)]
    recent = [_obs(f"new{i}", _ledger()) for i in range(10)]
    noise = [_obs("drill", _ledger(card_out=(1, 1, 1)), real=False),
             {"run_id": "blind", "real_scan": True, "usage_shape": rd.usage_shape({})}]
    got = rd.relative(rd.usage_shape(_ledger()), cheap + recent + noise, POLICY)
    assert got["n_baseline"] == 10 and got["status"] == "NORMAL"


def test_relative_ignores_agents_that_have_no_baseline_instead_of_dividing_by_nothing():
    history = [_obs(f"r{i}", _ledger(intel_out=())) for i in range(4)]
    got = rd.relative(rd.usage_shape(_ledger()), history, POLICY)
    assert "output_median:l4-intel" not in got["metrics"]      # 历史里没有它:不造基线
    # 总输出仍然照实比:多出一类 agent 让它从 119,200 升到 170,200。
    assert got["metrics"]["output_total"] == {"value": 170_200, "baseline_median": 119_200,
                                              "ratio": 1.43}
    assert got["status"] == "NORMAL" and got["worst"] is None


def test_unmeasured_current_run_has_no_relative_verdict():
    history = [_obs(f"r{i}", _ledger()) for i in range(6)]
    assert rd.relative(rd.usage_shape({}), history, POLICY)["status"] == "UNMEASURED"


# ── history loader ───────────────────────────────────────────────────────────


def _publish(root, rel, payload, usage=None):
    path = root / rel / "trace" / "_budget_observation.json"
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps(payload), encoding="utf-8")
    if usage is not None:
        (path.parent / "staging").mkdir()
        (path.parent / "staging" / "_token_usage.json").write_text(json.dumps(usage), encoding="utf-8")


def test_history_keeps_one_row_per_run_when_both_layouts_carry_it(tmp_path):
    payload = {"run_id": "20261008T130000000000Z", "analysis_date": "2026-10-08", "real_scan": True}
    _publish(tmp_path, "20261008-1008_2201", payload)
    _publish(tmp_path, "runs/20261008T130000000000Z/p1/report", payload)
    rows, unreadable = rd.load_history(tmp_path)
    assert [row["run_id"] for row in rows] == ["20261008T130000000000Z"] and unreadable == 0


def test_history_reads_both_publication_layouts_in_run_order(tmp_path):
    _publish(tmp_path, "20260917-0917_2152",
             {"run_id": "20260917T125201296461Z", "analysis_date": "2026-09-17", "real_scan": True})
    _publish(tmp_path, "runs/20261008T130000000000Z/p1/report",
             {"run_id": "20261008T130000000000Z", "analysis_date": "2026-10-08", "real_scan": True})
    _publish(tmp_path, "20260911-0912_1248",
             {"run_id": "20260912T035541989512Z", "analysis_date": "2026-09-11", "real_scan": True})
    (tmp_path / "20260901-bad" / "trace").mkdir(parents=True)
    (tmp_path / "20260901-bad" / "trace" / "_budget_observation.json").write_text("{not json")

    rows, unreadable = rd.load_history(tmp_path, exclude_run_id="20261008T130000000000Z")

    assert [row["run_id"] for row in rows] == ["20260912T035541989512Z", "20260917T125201296461Z"]
    assert unreadable == 1


def test_history_backfills_shape_and_identity_from_the_published_usage_ledger(tmp_path):
    ledger = _ledger()
    _publish(tmp_path, "20260917-0917_2152",
             {"run_id": "r917", "analysis_date": "2026-09-17", "real_scan": True}, usage=ledger)

    rows, _ = rd.load_history(tmp_path, engine="claude")

    assert rows[0]["usage_shape"]["agents"]["l4-card"]["output_median"] == 19_000
    assert rows[0]["identity"]["agents"]["l4-card"]["models"] == ["claude-opus-5"]
    assert rows[0]["identity"]["agent_prompts"] == {}       # 没有契约文件:留空,不编
    assert rows[0]["identity"]["engine"] == "claude"


def test_history_backfills_contract_hashes_from_the_published_run_contract(tmp_path):
    _publish(tmp_path, "20260917-0917_2152",
             {"run_id": "r917", "analysis_date": "2026-09-17", "real_scan": True}, usage=_ledger())
    contract = {"engine": "claude", "git_sha": "deadbeef", "config_hash": "c" * 64,
                "prompt_hashes": PROMPTS, "artifact_schema_versions": {"x": 1}}
    (tmp_path / "20260917-0917_2152" / "trace" / "run_contract.json").write_text(
        json.dumps(contract), encoding="utf-8")

    ident = rd.load_history(tmp_path)[0][0]["identity"]

    assert ident["agent_prompts"] == {".claude/agents/l4-card.md": "a" * 64,
                                      ".claude/agents/l4-intel.md": "b" * 64}
    assert ident["config_hash"] == "c" * 64 and ident["code_sha"] == "deadbeef"
    assert ident["cohort_key"] == _identity(_ledger(), config_hash="whatever")["cohort_key"]


def test_history_of_a_missing_reports_root_is_empty_not_an_error(tmp_path):
    assert rd.load_history(tmp_path / "nope") == ([], 0)


# ── rendering ────────────────────────────────────────────────────────────────


def test_summary_fragment_names_the_change_and_the_worst_metric():
    drift = {"status": "CHANGED", "baseline_run": "r1", "changes": [
        {"kind": "model", "scope": "l4-card", "before": ["claude-opus-5"], "after": ["claude-opus-5-5"]},
        {"kind": "host", "scope": "宿主", "before": ["2.1.283"], "after": ["2.1.284"]}]}
    relative = {"status": "SPIKE", "n_baseline": 6, "worst": "output_median:l4-card",
                "metrics": {"output_median:l4-card": {"value": 50_600, "baseline_median": 19_000,
                                                      "ratio": 2.66}}}
    assert rd.summary_fragment({"drift": drift, "relative": relative}) == (
        "身份:CHANGED(2 项:l4-card 模型、宿主 版本) · 相对:SPIKE ×2.66(l4-card 输出中位)")


def test_summary_fragment_for_a_quiet_run_and_for_a_run_with_no_data():
    quiet = {"drift": {"status": "SAME", "changes": []},
             "relative": {"status": "NORMAL", "worst": None, "metrics": {}}}
    assert rd.summary_fragment(quiet) == "身份:SAME · 相对:NORMAL"
    assert rd.summary_fragment({}) == "身份:— · 相对:—"
    no_base = {"drift": {"status": "NO_BASELINE", "changes": []},
               "relative": {"status": "NO_BASELINE", "scope": "all", "metrics": {}, "worst": None}}
    assert rd.summary_fragment(no_base) == "身份:NO_BASELINE · 相对:NO_BASELINE"


def test_detail_lines_print_before_and_after_for_every_change():
    drift = {"status": "CHANGED", "baseline_run": "r1", "changes": [
        {"kind": "model", "scope": "l4-card", "before": ["claude-opus-5"], "after": ["claude-opus-5-5"]}]}
    lines = rd.detail_lines({"drift": drift, "relative": {"status": "NO_BASELINE", "n_baseline": 1,
                                                         "metrics": {}, "worst": None},
                             "identity": {"cohort_key": "abc123def456", "host_versions": ["2.1.284"]}})
    assert lines[0] == "- 身份:CHANGED(对上一场 r1) · cohort `abc123def456` · 宿主 2.1.284"
    with_main = rd.detail_lines({"identity": {"cohort_key": "abc123def456", "host_versions": [],
                                              "main_models": ["claude-fable-5-1"]}})
    assert with_main[0] == "- 身份:— · cohort `abc123def456` · 主会话 claude-fable-5-1"
    assert "  - l4-card 模型:claude-opus-5 → claude-opus-5-5" in lines
    assert lines[-1] == "- 相对预算带:NO_BASELINE(可比的已发布真实扫描 1 场)"
