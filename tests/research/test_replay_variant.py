"""replay variant 契约单测 —— §3.4 P0-3。

治的病:`replay_day` 的幂等判据是「staging 全在场就跳过」。跑参数网格时第二个 variant
落进同一个 root 会被判「已完成」而复用**上一个 variant** 的产物 —— 网格表上出现一排
相同读数,看起来像「参数不敏感」,实际根本没跑。
"""
from __future__ import annotations

import json

import pytest

from autoresearch.research import replay


def _spec(**over) -> replay.VariantSpec:
    base = {"name": "cap015", "sector_cap_frac": 0.15}
    base.update(over)
    return replay.VariantSpec(**base)


# ── definition_hash:改参数才换 hash ─────────────────────────────


def test_hash_changes_with_a_parameter():
    assert _spec(sector_cap_frac=0.15).definition_hash != \
        _spec(sector_cap_frac=0.25).definition_hash


def test_hash_is_stable_for_the_same_definition():
    assert _spec().definition_hash == _spec().definition_hash


def test_renaming_does_not_change_the_definition():
    """改名不该换定义 —— 否则同一份参数会占两个根、白跑一遍。"""
    assert _spec(name="a").definition_hash == _spec(name="b").definition_hash


def test_note_does_not_enter_the_hash():
    assert _spec(note="x").definition_hash == _spec(note="y").definition_hash


def test_every_tuned_parameter_enters_the_hash():
    """新增参数忘了进 hash = 该参数的网格全部共用一个根。逐个钉死。"""
    base = _spec().definition_hash
    for field_name, value in (("l2_n", 150), ("floors", {"趋势": 5}),
                              ("sector_cap_frac", 0.9), ("regime_caps", {"trend": 0.5}),
                              ("weights", "current"), ("regime_aware", False),
                              ("funnel", {"recall_channels": ["momentum"]}),
                              ("extras", {"l0_min_amount_yi": 2.0})):
        assert _spec(**{field_name: value}).definition_hash != base, field_name


# ── 独立输出根 ──────────────────────────────────────────────────


def test_variant_root_is_physically_separate(tmp_path):
    root = replay.bind_variant(_spec(), tmp_path)
    assert root != tmp_path
    assert root.name.startswith("_v_cap015_")
    assert (root / replay.VARIANT_SPEC_FILE).exists()


def test_baseline_keeps_the_plain_root(tmp_path):
    """baseline 仍用主根 —— 既有产物不迁移。"""
    assert replay.baseline_spec().output_root(tmp_path) == tmp_path


def test_two_variants_do_not_share_a_root(tmp_path):
    a = replay.bind_variant(_spec(name="a", sector_cap_frac=0.15), tmp_path)
    b = replay.bind_variant(_spec(name="b", sector_cap_frac=0.25), tmp_path)
    assert a != b


def test_same_name_different_definition_is_rejected(tmp_path):
    """同名不同定义共用一个根正是要防的事 —— 抛错,不覆盖也不静默复用。"""
    replay.bind_variant(_spec(name="grid", sector_cap_frac=0.15), tmp_path)
    root = _spec(name="grid", sector_cap_frac=0.15).output_root(tmp_path)
    # 手工把另一份定义塞进同一个根(模拟有人改了参数但沿用目录)
    (root / replay.VARIANT_SPEC_FILE).write_text(
        json.dumps({"schema_version": 1, "spec": {"name": "grid"},
                    "definition_hash": "f" * 64}), encoding="utf-8")
    with pytest.raises(replay.VariantError, match="不得共用输出根"):
        replay.bind_variant(_spec(name="grid", sector_cap_frac=0.15), tmp_path)


def test_rebinding_the_same_definition_is_idempotent(tmp_path):
    a = replay.bind_variant(_spec(), tmp_path)
    b = replay.bind_variant(_spec(), tmp_path)
    assert a == b


def test_corrupt_spec_file_raises(tmp_path):
    root = replay.bind_variant(_spec(), tmp_path)
    (root / replay.VARIANT_SPEC_FILE).write_text("{ nope", encoding="utf-8")
    with pytest.raises(replay.VariantError, match="读不动"):
        replay.bind_variant(_spec(), tmp_path)


def test_read_variant_roundtrip(tmp_path):
    root = replay.bind_variant(_spec(), tmp_path)
    payload = replay.read_variant(root)
    assert payload["definition_hash"] == _spec().definition_hash
    assert payload["spec"]["sector_cap_frac"] == 0.15
    assert replay.read_variant(tmp_path / "nope") is None


# ── universe kwargs 投影 ────────────────────────────────────────


def test_only_explicit_parameters_are_passed_through():
    """None 一律不传 —— 传 None 会覆盖 universe.run 的默认值,那是另一种参数漂移。"""
    assert replay.VariantSpec(name="x").universe_kwargs() == {}
    kwargs = _spec(l2_n=150, floors={"趋势": 5}).universe_kwargs()
    assert kwargs == {"l2_n": 150, "l2_floors": {"趋势": 5}, "l2_sector_cap": 0.15}


def test_extras_are_forwarded():
    assert _spec(extras={"l0_min_amount_yi": 2.0}).universe_kwargs()[
        "l0_min_amount_yi"] == 2.0


# ── 幂等不再跨 variant 复用 ─────────────────────────────────────


def test_idempotent_skip_is_scoped_to_the_variant_root(tmp_path, monkeypatch):
    """A 跑完后,B 不该被判成「已完成」。这是本次修复的核心断言。"""
    calls = []

    def _fake_run(date, **kwargs):
        outdir = kwargs["outdir"]
        outdir.mkdir(parents=True, exist_ok=True)
        for name in replay._STAGING:
            (outdir / name).write_text("x", encoding="utf-8")
        calls.append((date, str(outdir), kwargs.get("l2_sector_cap")))
        return {"l2_n": 200, "recall_n": 1000, "universe": 5000}

    monkeypatch.setattr("autoresearch.scan.universe.run", _fake_run)

    a = _spec(name="a", sector_cap_frac=0.15)
    b = _spec(name="b", sector_cap_frac=0.25)
    r1 = replay.replay_day("2026-06-01", tmp_path, variant=a, attribute=False)
    r2 = replay.replay_day("2026-06-01", tmp_path, variant=b, attribute=False)
    r3 = replay.replay_day("2026-06-01", tmp_path, variant=a, attribute=False)

    assert r1["status"] == "ok" and r2["status"] == "ok"
    assert r3["status"] == "skip"          # 同 variant 重跑仍幂等
    assert len(calls) == 2
    assert {c[2] for c in calls} == {0.15, 0.25}    # 两个格点真的用了不同的 cap
    assert calls[0][1] != calls[1][1]               # 且落在不同的目录


def test_replay_day_records_the_variant_identity(tmp_path, monkeypatch):
    def _fake_run(date, **kwargs):
        kwargs["outdir"].mkdir(parents=True, exist_ok=True)
        return {"l2_n": 200, "recall_n": 1000, "universe": 5000}

    monkeypatch.setattr("autoresearch.scan.universe.run", _fake_run)
    out = replay.replay_day("2026-06-01", tmp_path, variant=_spec(), attribute=False)
    assert out["variant"] == "cap015"
    assert out["definition_hash"] == _spec().definition_hash


def test_baseline_replay_is_unchanged(tmp_path, monkeypatch):
    """不传 variant = 现行为逐字节不变(产物仍落主根,返回不带 variant 身份)。"""
    def _fake_run(date, **kwargs):
        kwargs["outdir"].mkdir(parents=True, exist_ok=True)
        assert "l2_sector_cap" not in kwargs      # 不传 variant 就不该注参数
        return {"l2_n": 200, "recall_n": 1000, "universe": 5000}

    monkeypatch.setattr("autoresearch.scan.universe.run", _fake_run)
    out = replay.replay_day("2026-06-01", tmp_path, attribute=False)
    assert out["variant"] is None
    assert (tmp_path / "2026-06-01").exists()
