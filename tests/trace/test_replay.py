"""Replay must be unable to succeed by accident."""

from __future__ import annotations

import importlib
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace

import pandas as pd
import pytest

from autoresearch.trace import replay as R
from autoresearch.trace.blobs import blob_path, put_dataframe
from autoresearch.trace.source_receipts import record_response


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


def _receipt(capsule: Path, outcome: object, *, occurrence_time: int) -> dict:
    handle = SimpleNamespace(
        capsule=capsule,
        engine="codex",
        run_id="20260914T010203000000Z",
    )
    stamp = f"2026-09-14T01:02:{occurrence_time:02d}Z"
    return record_response(
        handle,
        {
            "engine": handle.engine,
            "run_id": handle.run_id,
            "task_id": "scan.frame",
            "attempt": 1,
            "provider": "tushare",
            "endpoint": "daily",
            "normalized_params": {"trade_date": "20260912"},
            "started_at": stamp,
            "ended_at": stamp,
            "as_of": "20260914",
            "available_at": stamp,
            "consumer_refs": [],
        },
        outcome,
    )


def test_frozen_read_round_trips_without_lake_or_network(tmp_path):
    capsule = _capsule(tmp_path)
    frame = pd.DataFrame({"ts_code": ["600000.SH"], "close": [10.5]})
    _freeze_read(capsule, "daily", {"trade_date": "20260825"}, frame)

    restored = R.frame_for(capsule, "daily", {"trade_date": "20260825"})

    pd.testing.assert_frame_equal(restored, frame)


def test_strict_replay_consumes_repeated_responses_in_occurrence_order(tmp_path):
    capsule = _capsule(tmp_path)
    first = pd.DataFrame({"close": [10.0]})
    second = pd.DataFrame({"close": [11.0]})
    _receipt(capsule, first, occurrence_time=1)
    _receipt(capsule, second, occurrence_time=2)
    R.reset_replay_sequence(capsule)

    pd.testing.assert_frame_equal(
        R.frame_for(capsule, "daily", {"trade_date": "20260912"}), first
    )
    pd.testing.assert_frame_equal(
        R.frame_for(capsule, "daily", {"trade_date": "20260912"}), second
    )
    with pytest.raises(R.SourceSequenceMismatch, match="response sequence exhausted"):
        R.frame_for(capsule, "daily", {"trade_date": "20260912"})


def test_strict_replay_raises_the_recorded_failure_before_later_success(tmp_path):
    from autoresearch.data.contracts import DataContractError

    capsule = _capsule(tmp_path)
    _receipt(capsule, DataContractError("bad schema"), occurrence_time=1)
    _receipt(capsule, pd.DataFrame({"close": [11.0]}), occurrence_time=2)
    R.reset_replay_sequence(capsule)

    with pytest.raises(DataContractError, match="bad schema"):
        R.frame_for(capsule, "daily", {"trade_date": "20260912"})
    pd.testing.assert_frame_equal(
        R.frame_for(capsule, "daily", {"trade_date": "20260912"}),
        pd.DataFrame({"close": [11.0]}),
    )


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


def test_legacy_replay_refuses_to_write_a_frozen_capsule(tmp_path):
    capsule = _capsule(tmp_path)
    (capsule / "verification").mkdir()
    (capsule / "verification/ROOT.json").write_text("{}", encoding="utf-8")

    with pytest.raises(RuntimeError, match="external output_dir"):
        R.replay(
            "20260827T010203456789Z",
            capsule=capsule,
            analysis_date="2026-08-25",
            stages=("l4",),
            specs=(),
            runner=lambda *a: 0,
        )

    audit = tmp_path / "audit"
    R.replay(
        "20260827T010203456789Z",
        capsule=capsule,
        analysis_date="2026-08-25",
        stages=("l4",),
        specs=(),
        runner=lambda *a: 0,
        output_dir=audit,
    )
    assert (audit / "replay.json").is_file()
    assert not (capsule / "verification/replay.json").exists()


def test_l5_spec_compares_both_halves_of_the_publish_bundle():
    """§6.3:L5 的产物是发布包(summary + appendix)。只比对 summary 会让「附录没重现」
    在 replay 结论里完全不可见 —— 「产物能证明跑过什么、不能证明没跑过什么」。"""
    specs = {spec.stage: spec for spec in R.default_stage_specs("2026-08-28")}
    assert specs["l5"].outputs == ("summary.md", "appendix.md")


def test_l5_replay_is_partial_when_only_the_summary_reproduces(tmp_path):
    """summary 逐字节命中、appendix 没产出 → 不许报 FULL(半个发布包不是可重放)。"""
    capsule = _capsule(tmp_path)
    (capsule / "products/staging/summary.md").write_text("决策层\n", encoding="utf-8")
    (capsule / "products/staging/appendix.md").write_text("现场层\n", encoding="utf-8")

    def runner(spec, scratch, env):
        (scratch / "staging/summary.md").write_text("决策层\n", encoding="utf-8")
        return 0

    result = R.replay(
        "20260828T010203456789Z",
        capsule=capsule,
        analysis_date="2026-08-28",
        stages=("l5",),
        runner=runner,
    )

    assert result["replayability"] == R.PARTIAL
    outputs = {row["name"]: row for row in result["stages"][0]["outputs"]}
    assert outputs["summary.md"]["match"] is True
    assert outputs["appendix.md"]["match"] is False and outputs["appendix.md"]["frozen"] is True


def test_l5_replay_is_full_when_both_halves_reproduce(tmp_path):
    capsule = _capsule(tmp_path)
    (capsule / "products/staging/summary.md").write_text("决策层\n", encoding="utf-8")
    (capsule / "products/staging/appendix.md").write_text("现场层\n", encoding="utf-8")

    def runner(spec, scratch, env):
        (scratch / "staging/summary.md").write_text("决策层\n", encoding="utf-8")
        (scratch / "staging/appendix.md").write_text("现场层\n", encoding="utf-8")
        return 0

    result = R.replay(
        "20260828T010203456789Z",
        capsule=capsule,
        analysis_date="2026-08-28",
        stages=("l5",),
        runner=runner,
    )

    assert result["replayability"] == R.FULL


# --- stage spec 的 argv 必须真能跑 -------------------------------------------


def test_every_stage_spec_module_is_importable_and_runnable():
    """死 argv 是「永不变红的绿灯」。

    既有用例全程注入假 runner,于是 l2 的 spec 指向一个**根本不存在的模块**
    (`autoresearch.scan.l2_stratify`,真身在 `autoresearch/scan/recall/`)也一路绿。
    真跑走的是 `uv run … python -m <argv[0]>`,所以「可导入」还不够 —— 没有
    `main()` 的模块 `python -m` 一样跑不起来(`recall.l2_stratify` 正是如此)。
    """
    for spec in R.default_stage_specs("2026-08-26"):
        mod_name = spec.argv[0]
        assert (
            importlib.util.find_spec(mod_name) is not None
        ), f"{spec.stage}: 模块不存在 {mod_name}"
        mod = importlib.import_module(mod_name)
        assert hasattr(
            mod, "main"
        ), f"{spec.stage}: {mod_name} 没有 main(),python -m 跑不起来"


def test_l1_and_l2_are_one_universe_run_not_two(tmp_path):
    """L1/L2 的真实生产者是**同一条命令**:`scan.universe` 内部调 `recall.l2_stratify.
    select_l2`,一次写出三份 CSV。把 l2 的 argv 也写成 universe 会把它跑两遍(浪费,
    且第二遍的湖/时钟状态未必与第一遍相同)—— 正确形状是一个 spec 声明三产物、只跑一次。
    """
    capsule = _capsule(tmp_path)
    calls = []

    def runner(spec, scratch, env):
        calls.append(spec.argv)
        return 0

    R.replay(
        "20260829T010203456789Z",
        capsule=capsule,
        analysis_date="2026-08-26",
        stages=("l1", "l2"),
        runner=runner,
        keep_scratch=False,
    )

    assert len(calls) == 1, f"universe 被跑了 {len(calls)} 次:{calls}"
    assert calls[0][0] == "autoresearch.scan.universe"


def test_l1_and_l2_stay_separate_rows_with_their_own_outputs(tmp_path):
    """合并的是**执行**,不是对外结构。`capsule` / `completeness` 的读侧仍必须看见
    `l1` 与 `l2` 两行,且每行只汇报自己那几份产物 —— 否则改的就不止是 replay 自己。
    """
    capsule = _capsule(tmp_path)
    products = {
        "L1_scored_full.csv": "code,score\n600000,1.0\n",
        "L1_recall_top1000.csv": "code,score\n600000,1.0\n",
        "L2_gbdt_top200.csv": "code,score\n600000,1.0\n",
    }
    for name, text in products.items():
        (capsule / "products/staging" / name).write_text(text, encoding="utf-8")

    def runner(spec, scratch, env):
        for name, text in products.items():
            (scratch / "staging" / name).write_text(text, encoding="utf-8")
        return 0

    result = R.replay(
        "20260829T010203456789Z",
        capsule=capsule,
        analysis_date="2026-08-26",
        stages=("l1", "l2"),
        runner=runner,
        keep_scratch=False,
    )

    rows = {row["stage"]: row for row in result["stages"]}
    assert set(rows) == {"l1", "l2"}
    assert [o["name"] for o in rows["l1"]["outputs"]] == [
        "L1_scored_full.csv",
        "L1_recall_top1000.csv",
    ]
    assert [o["name"] for o in rows["l2"]["outputs"]] == ["L2_gbdt_top200.csv"]
    assert result["replayability"] == R.FULL


def test_l2_goes_partial_when_only_the_l1_half_reproduces(tmp_path):
    """一条命令三产物:L2 那份没重现出来,`l2` 行必须红,而不是被 `l1` 的命中带绿。"""
    capsule = _capsule(tmp_path)
    for name in ("L1_scored_full.csv", "L1_recall_top1000.csv", "L2_gbdt_top200.csv"):
        (capsule / "products/staging" / name).write_text(
            "code,score\n600000,1.0\n", encoding="utf-8"
        )

    def runner(spec, scratch, env):
        for name in ("L1_scored_full.csv", "L1_recall_top1000.csv"):
            (scratch / "staging" / name).write_text(
                "code,score\n600000,1.0\n", encoding="utf-8"
            )
        return 0

    result = R.replay(
        "20260829T010203456789Z",
        capsule=capsule,
        analysis_date="2026-08-26",
        stages=("l1", "l2"),
        runner=runner,
        keep_scratch=False,
    )

    rows = {row["stage"]: row for row in result["stages"]}
    assert rows["l1"]["match"] is True
    assert rows["l2"]["match"] is False
    assert result["replayability"] == R.PARTIAL
