"""Wave10 预注册实验与监视器(§C2):spec 契约 / family 冲突门 / monitor 不进 registry。

合成,无网络。这一层的价值全在**注册时就拦住**的那些事:definition 改了必须换 id、
同 family 不得并开、monitor 不许伪装成可 activate 的实验。
"""
from __future__ import annotations

import json

import pytest

from autoresearch.learning.experiment_registry import (
    RegistryError,
    load_registry,
    register_experiment,
    set_stable_baseline,
)
from autoresearch.learning.wave10_experiments import (
    MONITOR_SCHEMA_VERSION,
    assert_no_family_conflict,
    exp1_spec,
    exp2_spec,
    monitor_specs,
    register_all,
    write_monitors,
)


@pytest.fixture
def registry(tmp_path):
    path = tmp_path / "registry.json"
    set_stable_baseline(path, name="test-baseline", pointer="test",
                        content_hash="a" * 64, approved_by="test",
                        approved_at="2026-08-01T00:00:00+08:00", note="test")
    return path


# ────────────────────────── spec 契约 ──────────────────────────

@pytest.mark.parametrize("spec_fn", [exp1_spec, exp2_spec])
def test_specs_register_cleanly(registry, spec_fn):
    rec = register_experiment(registry, spec_fn(), registered_at="2026-08-01T00:00:00+08:00")
    assert rec["status"] == "PREREGISTERED"
    assert len(rec["definition_hash"]) == 64


@pytest.mark.parametrize("spec_fn", [exp1_spec, exp2_spec])
def test_specs_fill_all_five_guard_domains(spec_fn):
    """§C2.0:进 registry 的 challenger 必须填满五域 —— 少一域 registry 会直接拒。"""
    spec = spec_fn()
    for guards in (spec["promotion_guards"], spec["rollback_guards"]):
        assert set(guards) == {"research", "decision", "token", "speed", "architecture"}
        assert all(conditions for conditions in guards.values())


@pytest.mark.parametrize("spec_fn", [exp1_spec, exp2_spec])
def test_minimums_match_the_design_table(spec_fn):
    """固定成熟门:禁止「10–20 日」这种可选停止(§R8)。"""
    m = spec_fn()["minimums"]
    assert m == {"forward_days": 20, "mature_events": 50,
                 "unique_events": 50, "regimes": 2}


def test_exp1_population_is_participation_not_attribution():
    """人口口径写死在 definition 里 —— attribution 口径只有 17 例,永远攒不到 50。"""
    definition = exp1_spec()["definition"]
    assert "gate_participation_v3" in definition["population"]
    assert "attribution" in definition["population"]      # 明写为什么不用它


def test_exp1_forbids_online_backfill():
    """§8 裁决:缺任一 moneyflow 分区 → UNMEASURED,assemble 不得临时联网。"""
    assert "UNMEASURED" in exp1_spec()["definition"]["data_source"]
    assert "不得" in exp1_spec()["definition"]["data_source"]


def test_exp2_is_isolated_from_production():
    """floor=0 / 不占 quota / 不写生产 finalists —— 晋升前零生产副作用(R4)。"""
    isolation = exp2_spec()["definition"]["isolation"]
    assert "floor=0" in isolation and "不占 quota" in isolation
    assert "不写生产 finalists" in isolation


def test_exp2_keeps_event_channel_samples_separate():
    """与 pr_20260725_001 共用仪器但不共样本。"""
    assert "不得混样本" in exp2_spec()["definition"]["shared_instrument"]


@pytest.mark.parametrize("spec_fn", [exp1_spec, exp2_spec])
def test_architecture_guard_requires_zero_production_diff(spec_fn):
    arch = spec_fn()["promotion_guards"]["architecture"]
    assert any(c["metric"] == "production_artifact_diff" and c["op"] == "eq"
               and c["value"] == 0 for c in arch)


def test_changing_a_definition_without_changing_the_id_is_rejected(registry):
    """§6 实验门:definition hash 变了必须新 id,不能边跑边改。"""
    register_experiment(registry, exp1_spec(), registered_at="2026-08-01T00:00:00+08:00")
    tampered = exp1_spec()
    tampered["definition"]["challenger"] = "换了个判据"
    with pytest.raises(RegistryError, match="definition hash changed"):
        register_experiment(registry, tampered, registered_at="2026-08-01T00:00:00+08:00")


def test_registration_is_idempotent(registry):
    first = register_experiment(registry, exp1_spec(),
                                registered_at="2026-08-01T00:00:00+08:00")
    second = register_experiment(registry, exp1_spec(),
                                 registered_at="2026-08-02T00:00:00+08:00")
    assert first["definition_hash"] == second["definition_hash"]
    assert len(load_registry(registry)["experiments"]) == 1


# ────────────────────────── family 冲突门 ──────────────────────────

def test_same_family_cannot_be_opened_twice(registry):
    """同 family 并开会互相污染样本 —— 这条规则必须是会抛错的检查,不是文档里的话。"""
    register_experiment(registry, exp1_spec(), registered_at="2026-08-01T00:00:00+08:00")
    rival = exp2_spec()
    rival["id"] = "exp_rival"
    rival["trial_family"] = exp1_spec()["trial_family"]
    with pytest.raises(RegistryError, match="已被"):
        assert_no_family_conflict(rival, registry)


def test_l3_prompt_family_is_held_by_the_existing_experiment(registry):
    """§C2.0:l3_prompt family 在既有实验处置前不得新开。"""
    spec = exp1_spec()
    spec["trial_family"] = "l3_prompt"
    with pytest.raises(RegistryError, match="l3_prompt"):
        assert_no_family_conflict(spec, registry)


def test_closed_experiment_frees_its_family(registry):
    """已关闭的实验不该永远占着 family —— 否则这道门会变成永久堵塞。"""
    register_experiment(registry, exp1_spec(), registered_at="2026-08-01T00:00:00+08:00")
    payload = load_registry(registry)
    payload["experiments"][exp1_spec()["id"]]["status"] = "ROLLED_BACK"
    from autoresearch.learning.experiment_registry import write_registry
    write_registry(registry, payload)

    rival = exp2_spec()
    rival["id"] = "exp_rival"
    rival["trial_family"] = exp1_spec()["trial_family"]
    assert_no_family_conflict(rival, registry)   # 不抛


def test_register_all_registers_both(registry):
    records = register_all(registry, registered_at="2026-08-01T00:00:00+08:00")
    assert {r["id"] for r in records} == {exp1_spec()["id"], exp2_spec()["id"]}
    assert all(r["status"] == "PREREGISTERED" for r in records)


# ────────────────────────── monitor 不进 registry ──────────────────────────

def test_monitors_are_not_registry_experiments(registry):
    """§C2.0 末句:EXP-0/3 是 monitor,不得伪装成可 activate 的实验。"""
    register_all(registry, registered_at="2026-08-01T00:00:00+08:00")
    ids = set(load_registry(registry)["experiments"])
    assert not any(mon["id"] in ids for mon in monitor_specs())


def test_monitors_declare_no_promotion_machinery():
    """monitor 没有 promotion_guards / primary_metric —— 它本来就没有对照组。"""
    for mon in monitor_specs():
        assert "promotion_guards" not in mon and "primary_metric" not in mon
        assert mon["schema_version"] == MONITOR_SCHEMA_VERSION
        assert "不产 RECOMMENDED" in mon["verdict_policy"] or "只观察" in mon["verdict_policy"]


def test_regime_flip_monitor_forbids_optional_stopping():
    mon = next(m for m in monitor_specs() if m["id"].endswith("regime_flip"))
    assert "自动延长" in mon["maturity"]
    assert "第 5 日停" in mon["stopping_rule"]


def test_target_calib_monitor_does_not_reimplement_existing_work():
    """EXP-3 是既有功能验收 —— p60 锚已接线,本波不重复实现(§8 裁决)。"""
    mon = next(m for m in monitor_specs() if "target_calib" in m["id"])
    assert "不重复实现现有功能" in mon["verdict_policy"]


def test_write_monitors_roundtrips(tmp_path):
    target = write_monitors(tmp_path / "monitors.json")
    payload = json.loads(target.read_text(encoding="utf-8"))
    assert payload["schema_version"] == MONITOR_SCHEMA_VERSION
    assert len(payload["monitors"]) == 2
    assert not list(tmp_path.glob("*.tmp"))


def test_evidence_manifest_reports_trial_family_not_pointer_kind():
    """family 是 §C2.0 冲突规则的键 —— 报成 challenger_pointer.kind 会让检查形同虚设。"""
    from autoresearch.learning.evidence_manifest import build

    inventory = build().registry_inventory.get("experiments") or {}
    if exp1_spec()["id"] not in inventory:
        pytest.skip("活体 registry 里尚无本实验")
    record = inventory[exp1_spec()["id"]]
    assert record["family"] == exp1_spec()["trial_family"]
    assert record["pointer_kind"] == exp1_spec()["challenger_pointer"]["kind"]


def test_frozen_specs_match_the_live_registry():
    """预注册只有可被第三方验证才算数 —— registry 在 gitignore 的 context/ 下,
    所以 docs/ 里那份冻结副本的 definition_hash 必须与它逐一相等。对不上 =
    有人在看到结果之后改了判据。"""
    from pathlib import Path

    from autoresearch.learning.experiment_registry import load_registry

    frozen_path = Path("docs/research/2026-08-01-wave10-experiment-specs.json")
    if not frozen_path.exists():
        pytest.skip("冻结副本不在")
    frozen = {e["id"]: e["definition_hash"]
              for e in json.loads(frozen_path.read_text(encoding="utf-8"))["experiments"]}
    live = {k: v["definition_hash"]
            for k, v in load_registry().get("experiments", {}).items()}
    for exp_id, digest in frozen.items():
        if exp_id not in live:
            pytest.skip(f"活体 registry 尚未注册 {exp_id}")
        assert live[exp_id] == digest, f"{exp_id} 的判据在注册后被改过"


def test_freeze_reproduces_the_same_hashes(tmp_path):
    """冻结是纯函数 —— 同一份 spec 冻两次必须字节一致(否则 hash 比对没有意义)。"""
    from autoresearch.learning.wave10_experiments import freeze_specs

    a = freeze_specs(tmp_path / "a.json").read_text(encoding="utf-8")
    b = freeze_specs(tmp_path / "b.json").read_text(encoding="utf-8")
    assert a == b
