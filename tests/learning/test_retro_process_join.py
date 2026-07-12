"""P0-4:retro._join_process_score 单测 —— attribution 加 process_score 列(presence-gated join)。
零 LLM/无网络。

design: docs/specs/2026-07-12-selflearning-optimization-brainstorm.md §4 P0-4;
plan:   docs/plans/2026-07-12-selflearning-p0-plan.md T2。
"""
from __future__ import annotations

import pandas as pd

from autoresearch.learning import retro


def _attr(codes: list[str]) -> pd.DataFrame:
    return pd.DataFrame({"code": codes, "fwd_2_oc": [0.01] * len(codes)})


def test_join_process_score_adds_column_when_file_present(tmp_path):
    sdir = tmp_path / "2026-07-08"
    sdir.mkdir()
    pd.DataFrame({"code": ["300476", "600519"], "process_score": [4, 1]}
                ).to_csv(sdir / "process_scores.csv", index=False)
    out = retro._join_process_score(_attr(["300476", "600519"]), sdir)
    assert "process_score" in out.columns
    by_code = out.set_index("code")
    assert by_code.loc["300476", "process_score"] == 4
    assert by_code.loc["600519", "process_score"] == 1


def test_join_process_score_absent_file_returns_unchanged(tmp_path):
    sdir = tmp_path / "2026-07-08"
    sdir.mkdir()
    attr = _attr(["300476"])
    out = retro._join_process_score(attr, sdir)
    assert "process_score" not in out.columns
    pd.testing.assert_frame_equal(out, attr)


def test_join_process_score_empty_csv_returns_unchanged(tmp_path):
    sdir = tmp_path / "2026-07-08"
    sdir.mkdir()
    pd.DataFrame(columns=["code", "process_score"]).to_csv(sdir / "process_scores.csv", index=False)
    attr = _attr(["300476"])
    out = retro._join_process_score(attr, sdir)
    assert "process_score" not in out.columns


def test_join_process_score_missing_column_returns_unchanged(tmp_path):
    sdir = tmp_path / "2026-07-08"
    sdir.mkdir()
    pd.DataFrame({"code": ["300476"], "chk_slim_size": [True]}).to_csv(
        sdir / "process_scores.csv", index=False)
    attr = _attr(["300476"])
    out = retro._join_process_score(attr, sdir)
    assert "process_score" not in out.columns


def test_join_process_score_code_not_in_scores_gets_nan(tmp_path):
    """attribution 里有,但 process_scores.csv 没有该码(例如非 finalist)→ NaN,不丢行。"""
    sdir = tmp_path / "2026-07-08"
    sdir.mkdir()
    pd.DataFrame({"code": ["300476"], "process_score": [3]}).to_csv(
        sdir / "process_scores.csv", index=False)
    out = retro._join_process_score(_attr(["300476", "999999"]), sdir)
    assert len(out) == 2
    by_code = out.set_index("code")
    assert by_code.loc["300476", "process_score"] == 3
    assert pd.isna(by_code.loc["999999", "process_score"])


def test_keep_whitelist_contains_process_score():
    assert "process_score" in retro._KEEP
