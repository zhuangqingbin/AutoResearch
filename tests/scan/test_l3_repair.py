"""L3 lint 只修失败行；未失败行由确定性 merge 原样保留。"""
from __future__ import annotations

import json

import pandas as pd
import pytest

from autoresearch.scan.agents.l3_select import (
    apply_repair_patch,
    build_repair_pack,
)

DATE = "2026-07-28"


def _fixture(tmp_path):
    scan = tmp_path / DATE
    scan.mkdir()
    pd.DataFrame([
        {"code": "000001", "pe": 10.0, "pct_60d": 1.0},
        {"code": "000002", "pe": 20.0, "pct_60d": 2.0},
        {"code": "000003", "pe": 30.0, "pct_60d": 3.0},
    ]).to_csv(scan / "L2_gbdt_top200.csv", index=False)
    judged = [
        {"code": "000001", "thesis": "PE 10", "catalyst": "", "conviction": 61},
        {"code": "000002", "thesis": "PE 99", "catalyst": "", "conviction": 62},
        {"code": "000003", "thesis": "PE 30", "catalyst": "", "conviction": 63},
    ]
    (scan / "_l3_judged.json").write_text(
        json.dumps(judged, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return scan, judged


def test_repair_pack_contains_only_failing_rows_and_legal_evidence(tmp_path):
    scan, _ = _fixture(tmp_path)

    pack = build_repair_pack(DATE, root=tmp_path)

    assert pack["codes"] == ["000002"]
    assert [row["code"] for row in pack["rows"]] == ["000002"]
    assert pack["rows"][0]["evidence"]["pe"] == 20.0
    assert "000001" not in pack["prompt"]
    assert "000003" not in pack["prompt"]
    assert (scan / "_l3_repair_prompt.md").read_text(encoding="utf-8") == pack["prompt"]


def test_apply_patch_preserves_all_unrequested_rows(tmp_path):
    scan, before = _fixture(tmp_path)
    build_repair_pack(DATE, root=tmp_path)
    (scan / "_l3_repair_patch.json").write_text(
        json.dumps([{"code": "000002", "thesis": "PE 20"}], ensure_ascii=False),
        encoding="utf-8",
    )

    got = apply_repair_patch(DATE, root=tmp_path)
    after = json.loads((scan / "_l3_judged.json").read_text(encoding="utf-8"))

    assert got == {"patched": 1, "preserved": 2, "codes": ["000002"]}
    assert after[0] == before[0]
    assert after[2] == before[2]
    assert after[1]["thesis"] == "PE 20"
    assert after[1]["conviction"] == 62


@pytest.mark.parametrize("patch,match", [
    ([{"code": "000001", "thesis": "PE 10"}], "unrequested"),
    ([
        {"code": "000002", "thesis": "PE 20"},
        {"code": "000002", "thesis": "PE 20"},
    ], "duplicate"),
    ([{"code": "000002", "thesis": "PE 20", "risk": "改别的字段"}], "fields"),
])
def test_apply_patch_rejects_scope_escape(tmp_path, patch, match):
    scan, _ = _fixture(tmp_path)
    build_repair_pack(DATE, root=tmp_path)
    (scan / "_l3_repair_patch.json").write_text(
        json.dumps(patch, ensure_ascii=False), encoding="utf-8"
    )
    with pytest.raises(ValueError, match=match):
        apply_repair_patch(DATE, root=tmp_path)


def test_apply_patch_rejects_still_invalid_thesis_before_overwrite(tmp_path):
    scan, before = _fixture(tmp_path)
    build_repair_pack(DATE, root=tmp_path)
    (scan / "_l3_repair_patch.json").write_text(
        json.dumps([{"code": "000002", "thesis": "PE 88"}]), encoding="utf-8"
    )
    with pytest.raises(ValueError, match="still fails"):
        apply_repair_patch(DATE, root=tmp_path)
    assert json.loads((scan / "_l3_judged.json").read_text(encoding="utf-8")) == before
