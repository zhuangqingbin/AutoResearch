"""统一闸门单测 —— §3.5 O5「没有第二条路」。"""
from __future__ import annotations

import json

import pytest

from autoresearch.research import feature_gate as fg


def _rec(**stages) -> fg.FeatureRecord:
    return fg.FeatureRecord(feature_id="cb_premium_delta", stages=dict(stages))


def test_fresh_feature_starts_at_capability():
    info = fg.status(_rec())
    assert info["next_stage"] == "capability"
    assert info["furthest_passed"] is None
    assert info["cleared_for_production"] is False


def test_stages_are_ordered_and_gate_is_sequential():
    assert fg.STAGES == ("capability", "factor_lab", "replay", "registry", "production")


def test_declaring_a_later_stage_pass_does_not_skip_an_earlier_block():
    """手填 registry=PASS 但 capability 没过 → 结算后仍是 ⛔。这是闸门的全部权力。"""
    info = fg.status(_rec(registry=fg.PASS))
    assert info["stages"]["registry"] == fg.BLOCKED
    assert info["next_stage"] == "capability"


def test_pass_chain_advances_the_frontier():
    info = fg.status(_rec(capability=fg.PASS, factor_lab=fg.PASS))
    assert info["furthest_passed"] == "factor_lab"
    assert info["next_stage"] == "replay"
    assert info["stages"]["registry"] == fg.BLOCKED


def test_all_stages_pass_clears_production():
    info = fg.status(_rec(**dict.fromkeys(fg.STAGES, fg.PASS)))
    assert info["cleared_for_production"] is True
    assert info["next_stage"] is None


def test_assert_allowed_blocks_a_skipped_stage():
    with pytest.raises(fg.FeatureGateError, match="没有第二条路"):
        fg.assert_allowed(_rec(capability=fg.PASS), "registry")


def test_assert_allowed_passes_for_the_next_legitimate_stage():
    fg.assert_allowed(_rec(capability=fg.PASS), "factor_lab")


def test_assert_allowed_rejects_unknown_stage():
    with pytest.raises(fg.FeatureGateError, match="未知阶段"):
        fg.assert_allowed(_rec(), "vibes")


def test_error_message_tells_you_what_to_do_next():
    with pytest.raises(fg.FeatureGateError) as exc:
        fg.assert_allowed(_rec(), "replay")
    assert "PIT 可回放" in str(exc.value)


def test_requirements_for_next_are_surfaced():
    info = fg.status(_rec(capability=fg.PASS))
    assert any("锁定 OOS" in r for r in info["requirements_for_next"])


# ── 账本读写 ────────────────────────────────────────────────────


def test_upsert_and_load_roundtrip(tmp_path):
    ledger = tmp_path / "gate.json"
    fg.upsert("cb_premium", stage="capability", verdict=fg.PASS,
              description="可转债溢价变动", evidence="docs/probe.md", path=ledger)
    records = fg.load(ledger)
    assert records["cb_premium"].stages["capability"] == fg.PASS
    assert records["cb_premium"].evidence["capability"] == "docs/probe.md"
    assert records["cb_premium"].description == "可转债溢价变动"


def test_upsert_rejects_unknown_stage(tmp_path):
    with pytest.raises(fg.FeatureGateError):
        fg.upsert("x", stage="nope", path=tmp_path / "g.json")


def test_load_missing_or_corrupt_ledger_is_empty(tmp_path):
    assert fg.load(tmp_path / "nope.json") == {}
    bad = tmp_path / "bad.json"
    bad.write_text("{ nope", encoding="utf-8")
    assert fg.load(bad) == {}


def test_saved_ledger_states_the_policy(tmp_path):
    ledger = tmp_path / "g.json"
    fg.upsert("x", stage="capability", verdict=fg.PENDING, path=ledger)
    payload = json.loads(ledger.read_text(encoding="utf-8"))
    assert "没有第二条路" in payload["policy"]
    assert payload["stages"] == list(fg.STAGES)


# ── 渲染 / CLI ──────────────────────────────────────────────────


def test_render_marks_blocked_stages(tmp_path):
    md = fg.render({"x": _rec(registry=fg.PASS)})
    assert "⛔" in md and "没有第二条路" in md


def test_render_empty():
    assert "| — |" in fg.render({})


def test_cli_upsert_then_list(tmp_path, capsys):
    ledger = tmp_path / "g.json"
    assert fg.main(["--ledger", str(ledger), "--feature", "f1",
                    "--stage", "capability", "--verdict", "PASS"]) == 0
    capsys.readouterr()
    assert fg.main(["--ledger", str(ledger), "--list"]) == 0
    out = capsys.readouterr().out
    assert "f1" in out and "factor_lab" in out


def test_cli_writes_report(tmp_path):
    ledger, out = tmp_path / "g.json", tmp_path / "r.md"
    fg.upsert("f1", stage="capability", verdict=fg.PASS, path=ledger)
    assert fg.main(["--ledger", str(ledger), "--out", str(out)]) == 0
    assert "统一闸门" in out.read_text(encoding="utf-8")
