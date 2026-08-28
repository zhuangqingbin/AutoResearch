"""Replay must be unable to succeed by accident."""

from __future__ import annotations

import json
from pathlib import Path

import pandas as pd
import pytest

from autoresearch.trace import replay as R
from autoresearch.trace.blobs import blob_path, put_dataframe


def _capsule(tmp_path: Path) -> Path:
    capsule = tmp_path / "capsule"
    (capsule / "lineage").mkdir(parents=True)
    (capsule / "products/staging").mkdir(parents=True)
    return capsule


def _freeze_read(capsule: Path, endpoint: str, params: dict, frame: pd.DataFrame) -> str:
    digest = put_dataframe(capsule, frame)
    row = {
        "endpoint": endpoint,
        "normalized_params": params,
        "normalized_blob_hash": digest,
        "blob_hash": None,
        "status": "OK",
    }
    with (capsule / "lineage/reads.jsonl").open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(row, sort_keys=True) + "\n")
    return digest


def test_frozen_read_round_trips_without_lake_or_network(tmp_path):
    capsule = _capsule(tmp_path)
    frame = pd.DataFrame({"ts_code": ["600000.SH"], "close": [10.5]})
    _freeze_read(capsule, "daily", {"trade_date": "20260825"}, frame)

    restored = R.frame_for(capsule, "daily", {"trade_date": "20260825"})

    pd.testing.assert_frame_equal(restored, frame)


def test_missing_read_raises_instead_of_falling_back(tmp_path):
    capsule = _capsule(tmp_path)

    with pytest.raises(R.ReplayInputMissing, match="no frozen read"):
        R.frame_for(capsule, "daily", {"trade_date": "20260825"})


def test_missing_blob_is_reported_with_its_exact_key(tmp_path):
    capsule = _capsule(tmp_path)
    digest = _freeze_read(
        capsule, "daily", {"trade_date": "20260825"}, pd.DataFrame({"a": [1]})
    )
    blob_path(capsule, digest).unlink()

    gaps = R.missing_blobs(capsule)

    assert len(gaps) == 1
    assert "20260825" in gaps[0]
    with pytest.raises(R.ReplayInputMissing, match="frozen blob is gone"):
        R.frame_for(capsule, "daily", {"trade_date": "20260825"})


def test_cache_in_replay_mode_never_calls_network(tmp_path, monkeypatch):
    from autoresearch.data import cache

    capsule = _capsule(tmp_path)
    frame = pd.DataFrame({"ts_code": ["600000.SH"], "close": [10.5]})
    _freeze_read(capsule, "daily", {"trade_date": "20260825"}, frame)
    monkeypatch.setenv(R.REPLAY_ENV, str(capsule))

    def _explode(*args, **kwargs):
        raise AssertionError("network called during replay")

    restored = cache.get_or_fetch("daily", {"trade_date": "20260825"}, fetch=_explode)

    pd.testing.assert_frame_equal(restored, frame)


def test_cache_in_replay_mode_refuses_unknown_reads(tmp_path, monkeypatch):
    from autoresearch.data import cache

    capsule = _capsule(tmp_path)
    monkeypatch.setenv(R.REPLAY_ENV, str(capsule))

    with pytest.raises(R.ReplayInputMissing):
        cache.get_or_fetch("daily", {"trade_date": "20260825"}, fetch=lambda *a: None)


def test_production_path_is_unchanged_without_the_env_var(tmp_path, monkeypatch):
    from autoresearch.data import cache

    monkeypatch.delenv(R.REPLAY_ENV, raising=False)
    assert R.active_capsule() is None
    calls = []

    def _fetch(endpoint, params):
        calls.append(endpoint)
        return pd.DataFrame({"ts_code": ["600000.SH"], "close": [1.0]})

    monkeypatch.setattr(cache, "LAKE", tmp_path / "lake")
    # 契约照常拒绝这个玩具帧 —— 关键是取数路径**被走到了**,重放短路没有生效。
    from autoresearch.data.contracts import DataContractError

    with pytest.raises(DataContractError):
        cache.get_or_fetch("daily", {"trade_date": "20260825"}, fetch=_fetch)
    assert calls == ["daily"]


def _spec(stage="l1", outputs=("L1_scored_full.csv",)):
    return R.StageSpec(stage, ("fixture.module", "2026-08-25"), outputs)


def test_replay_matches_frozen_products_and_reports_full(tmp_path):
    capsule = _capsule(tmp_path)
    (capsule / "products/staging/L1_scored_full.csv").write_text(
        "code,score\n600000,1.0\n", encoding="utf-8"
    )
    _freeze_read(capsule, "daily", {"d": "1"}, pd.DataFrame({"a": [1]}))

    def runner(spec, scratch, env):
        assert env[R.REPLAY_ENV] == str(capsule.resolve())
        assert not any(key.startswith("AUTORESEARCH_RUN_ID") for key in env)
        (scratch / "staging/L1_scored_full.csv").write_text(
            "code,score\n600000,1.00\n", encoding="utf-8"
        )
        return 0

    result = R.replay(
        "20260827T010203456789Z",
        capsule=capsule,
        analysis_date="2026-08-25",
        stages=("l1",),
        specs=(_spec(),),
        runner=runner,
    )

    assert result["replayability"] == R.FULL
    assert all(row["match"] for row in result["stages"])
    assert Path(result["scratch"]).is_dir()
    assert str(capsule) not in result["stages"][0]["outputs"][0]["output_path"]


def test_replay_output_mismatch_is_partial_not_silent(tmp_path):
    capsule = _capsule(tmp_path)
    (capsule / "products/staging/L1_scored_full.csv").write_text(
        "code,score\n600000,1.0\n", encoding="utf-8"
    )

    def runner(spec, scratch, env):
        (scratch / "staging/L1_scored_full.csv").write_text(
            "code,score\n600000,2.0\n", encoding="utf-8"
        )
        return 0

    result = R.replay(
        "20260827T010203456789Z",
        capsule=capsule,
        analysis_date="2026-08-25",
        stages=("l1",),
        specs=(_spec(),),
        runner=runner,
    )

    assert result["replayability"] == R.PARTIAL
    assert result["stages"][0]["match"] is False


def test_missing_blob_downgrades_a_matching_replay_to_partial(tmp_path):
    capsule = _capsule(tmp_path)
    (capsule / "products/staging/L1_scored_full.csv").write_text(
        "code,score\n600000,1.0\n", encoding="utf-8"
    )
    digest = _freeze_read(capsule, "daily", {"d": "1"}, pd.DataFrame({"a": [1]}))
    blob_path(capsule, digest).unlink()

    def runner(spec, scratch, env):
        (scratch / "staging/L1_scored_full.csv").write_text(
            "code,score\n600000,1.0\n", encoding="utf-8"
        )
        return 0

    result = R.replay(
        "20260827T010203456789Z",
        capsule=capsule,
        analysis_date="2026-08-25",
        stages=("l1",),
        specs=(_spec(),),
        runner=runner,
    )

    assert result["replayability"] == R.PARTIAL
    assert result["missing_blobs"]


def test_llm_stage_is_evidence_only_and_never_executed(tmp_path):
    capsule = _capsule(tmp_path)
    executed = []

    def runner(spec, scratch, env):
        executed.append(spec.stage)
        return 0

    result = R.replay(
        "20260827T010203456789Z",
        capsule=capsule,
        analysis_date="2026-08-25",
        stages=("l4",),
        specs=(),
        runner=runner,
    )

    assert executed == []
    assert result["stages"][0]["status"] == R.EVIDENCE_ONLY
    assert result["replayability"] == R.NONE


def test_replay_writes_its_verdict_into_the_capsule(tmp_path):
    capsule = _capsule(tmp_path)

    R.replay(
        "20260827T010203456789Z",
        capsule=capsule,
        analysis_date="2026-08-25",
        stages=("l4",),
        specs=(),
        runner=lambda *a: 0,
    )

    payload = json.loads((capsule / "verification/replay.json").read_text())
    assert payload["replayability"] == R.NONE
