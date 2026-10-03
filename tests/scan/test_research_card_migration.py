"""D3/D4:解析桥 → 候选 JSON → 与旧读法对拍;fail-closed 读取;决策记录用枚举不用文本反推。"""
import json

import pytest

from autoresearch.scan import decision_finalize as df
from autoresearch.scan.l4 import card_io, card_render
from autoresearch.scan.l4.parsers import _finalist_row

FULL_CARD = """# 决策卡 — 600000 甲 @ 2026-09-01

**Rating**: Overweight

| 评级 | 现价 | EV目标(+%,T+2开盘预期带) | 上行/下行 | R:R | 时间框架 | 建议仓位 | 触发位 | 置信度 |
|---|---|---|---|---|---|---|---|---|
| **Overweight** | 10.20 | 10.45(+2.5%) | +3% / −2% | 1.5 | 隔夜 | 10% | 跌破 10.0 放弃 | 中 |

**评分卡**:基本面 强 ｜ 估值 中 ｜ 技术·资金 强 ｜ 盈利质量 中 ｜ 偿付(爆雷) 中 ｜ 催化 强
OW三门:主力真在 ✓ ｜ 业绩真兑现 ✓ ｜ 估值不透支 ✗ → 一门失守
Rubric: Hold — 净分+3→Overweight,OW门未过(估值不透支)→压Hold
**偏离**: 主力席位连续三日净买,催化明确,接受估值门失守

入场否决(T+1 尾盘前):收盘价 > 10.40 放弃本次入场
[执行线] pct_chg <= 3.0 → 当日涨超 3% 放弃本次尾盘入场
[价格线] close < 9.80 → 止损
FINAL TRANSACTION PROPOSAL: **BUY**
"""

EARLY_STOP_CARD = """# 决策卡 — 000001 乙 @ 2026-09-01  ·  〔早停·表面 DD〕

**Rating**: Underweight

**早停**:停于 P3 ｜ 停因:资金流出 ｜ 详情略

FINAL TRANSACTION PROPOSAL: **HOLD**
"""


@pytest.fixture()
def scan_dir(tmp_path):
    (tmp_path / "details").mkdir()
    (tmp_path / "details" / "600000.md").write_text(FULL_CARD, encoding="utf-8")
    (tmp_path / "details" / "000001.md").write_text(EARLY_STOP_CARD, encoding="utf-8")
    return tmp_path


def test_bridge_reads_the_same_facts_as_the_legacy_row(scan_dir):
    card = card_io.card_from_markdown(scan_dir, "600000", analysis_date="2026-09-01")
    legacy = _finalist_row(scan_dir, {"code": "600000", "ticker": "600000"})
    assert card["initial_rating"] == legacy["rating"] == "Overweight"
    assert card["proposal"] == legacy["proposal"] == "BUY"
    assert card["target"] == legacy["target"] and card["rr"] == legacy["rr"] == "1.5"
    assert card["confidence"] == legacy["conf"] == "中"
    assert card["gates"] == {"主力真在": "PASS", "业绩真兑现": "PASS", "估值不透支": "FAIL"}
    assert card["dimensions"]["基本面"] == "强" and card["dimensions"]["偿付"] == "中"
    assert card["rating_deviation_reason"].startswith("主力席位")
    assert card["entry_veto"] == ["收盘价 > 10.40 放弃本次入场"]
    assert card["exec_lines"] and card["exec_lines"][0].startswith("[执行线]")
    assert {w["kind"] for w in card["tripwires"]} == {"exec", "price"}   # 盯梢线走 tripwire_watch 的同一解析器
    assert card["card_origin"] == "parser_bridge_v1"


def test_bridge_fills_unknowns_instead_of_inventing(scan_dir):
    card = card_io.card_from_markdown(scan_dir, "000001", analysis_date="2026-09-01")
    assert card["early_stop"] == {"phase": "P3", "reason": "资金流出"}
    assert card["gates"] == dict.fromkeys(card["gates"], "UNKNOWN")
    assert set(card["dimensions"].values()) == {"未核"}
    assert card["theses"] == [] and card["evidence_refs"] == [] and card["scenarios"] == []
    assert card["probability_basis"] == "not_provided" and card["target"] == "未核"


def test_bridge_keeps_unreviewed_gates_unknown():
    text = """# 决策卡 — 600000 甲 @ 2026-09-01

**Rating**: Hold
OW三门:主力真在 ✓ ｜ 业绩真兑现 未核 ｜ 估值不透支 未核
FINAL TRANSACTION PROPOSAL: **HOLD**
"""

    card = card_io.card_from_text(
        text, code="600000", analysis_date="2026-09-01", holding=False
    )

    assert card["gates"] == {
        "主力真在": "PASS",
        "业绩真兑现": "UNKNOWN",
        "估值不透支": "UNKNOWN",
    }


def test_missing_card_is_none_not_an_empty_card(scan_dir):
    assert card_io.card_from_markdown(scan_dir, "999999", analysis_date="2026-09-01") is None


def test_render_preserves_the_rating_anchor(scan_dir):
    from autoresearch.agents.utils.rating import parse_rating
    card = card_io.card_from_markdown(scan_dir, "600000", analysis_date="2026-09-01")
    text = "# 另一种研究标题\n" + card_render.render_anchors(card, rules_version="legacy-v1")
    assert parse_rating(text, strict=True) == "Overweight"
    assert "**偏离**" in text and "FINAL TRANSACTION PROPOSAL: BUY" in text


def test_render_refuses_a_silent_rating_deviation(scan_dir):
    card = card_io.card_from_markdown(scan_dir, "600000", analysis_date="2026-09-01")
    card["rating_deviation_reason"] = ""
    with pytest.raises(ValueError):
        card_render.render_anchors(card)


def test_candidate_writer_produces_json_and_compare_without_touching_markdown(scan_dir):
    before = (scan_dir / "details" / "600000.md").read_bytes()
    compare = card_io.write_candidate_cards(scan_dir, analysis_date="2026-09-01",
                                            codes=["600000", "000001", "999999"], pinned=["000001"])
    assert (scan_dir / "details" / "600000.md").read_bytes() == before
    assert (scan_dir / "details" / "600000.research.json").exists()
    assert compare["missing"] == ["999999"]
    written = json.loads((scan_dir / "card_compare.json").read_text(encoding="utf-8"))
    assert written["codes"]["600000"].get("rating") is None            # 对拍一致的键不出现
    assert written["codes"]["600000"]["gate_states"]["legacy"] == "unsupported_in_legacy"
    pinned = json.loads((scan_dir / "details" / "000001.research.json").read_text(encoding="utf-8"))
    assert pinned["holding"] is True


def test_read_card_is_fail_closed_under_json_authority(scan_dir):
    calls = []

    def legacy(scan_dir_, code):
        calls.append(code)
        return {"rating": "Hold"}

    assert card_io.read_card(scan_dir, "600000", card_source="legacy_md", legacy_reader=legacy) == {"rating": "Hold"}
    with pytest.raises(FileNotFoundError):
        card_io.read_card(scan_dir, "600000", card_source="research_json_v1", legacy_reader=legacy)
    assert calls == ["600000"], "JSON 权威下缺文件不许偷偷退回 legacy"
    (scan_dir / "details" / "600000.research.json").write_text("{not json", encoding="utf-8")
    with pytest.raises(ValueError):
        card_io.read_card(scan_dir, "600000", card_source="research_json_v1", legacy_reader=legacy)
    with pytest.raises(ValueError):
        card_io.read_card(scan_dir, "600000", card_source="whatever", legacy_reader=legacy)


def test_read_card_rejects_identity_mismatch(scan_dir):
    card_io.write_candidate_cards(scan_dir, analysis_date="2026-09-01", codes=["600000"])
    good = json.loads((scan_dir / "details" / "600000.research.json").read_text(encoding="utf-8"))
    (scan_dir / "details" / "000001.research.json").write_text(json.dumps(good), encoding="utf-8")
    with pytest.raises(ValueError):
        card_io.read_card(scan_dir, "000001", card_source="research_json_v1", legacy_reader=None)


def test_decision_records_use_structured_gates_when_present(scan_dir, monkeypatch):
    card = card_io.card_from_markdown(scan_dir, "600000", analysis_date="2026-09-01")
    row = card_io.card_to_row({"code": "600000", "_source_rating": "Overweight",
                               "_post_verify_rating": "Overweight"}, card)
    row["rating"] = "Hold"                       # 假设 ensemble 折回到 Hold
    monkeypatch.setattr(df, "contract_hash_for", lambda d: None, raising=False)
    records = df._build_decision_records(scan_dir, [row], {}, {})
    (record,) = records
    assert record.gate_states == {"主力真在": "PASS", "业绩真兑现": "PASS", "估值不透支": "FAIL"}


def test_decision_records_still_parse_text_without_structured_facts(scan_dir, monkeypatch):
    legacy = _finalist_row(scan_dir, {"code": "600000", "ticker": "600000"})
    legacy.update({"_source_rating": "Overweight", "_post_verify_rating": "Overweight", "rating": "Hold"})
    monkeypatch.setattr(df, "contract_hash_for", lambda d: None, raising=False)
    (record,) = df._build_decision_records(scan_dir, [legacy], {}, {})
    assert record.gate_states["估值不透支"] == "FAIL"


def test_structured_and_text_paths_agree_on_the_same_card(scan_dir, monkeypatch):
    """同一张卡两条路走出来的门态必须一致 —— 否则切换权威那天读数会跳。"""
    monkeypatch.setattr(df, "contract_hash_for", lambda d: None, raising=False)
    card = card_io.card_from_markdown(scan_dir, "600000", analysis_date="2026-09-01")
    structured = card_io.card_to_row({"code": "600000", "_source_rating": "Overweight",
                                      "_post_verify_rating": "Overweight", "rating": "Overweight"}, card)
    legacy = _finalist_row(scan_dir, {"code": "600000", "ticker": "600000"})
    legacy.update({"_source_rating": "Overweight", "_post_verify_rating": "Overweight", "rating": "Overweight"})
    (a,), (b,) = (df._build_decision_records(scan_dir, [structured], {}, {}),
                  df._build_decision_records(scan_dir, [legacy], {}, {}))
    assert a.gate_states == b.gate_states and a.early_stop == b.early_stop
    assert a.final_rating == b.final_rating and a.proposal == b.proposal


def _freeze_rules(scan_dir, version="skills-gap-v2"):
    capsule = scan_dir.parent.parent / "capsule"
    profile = capsule / "verification/profile.json"
    profile.parent.mkdir(parents=True, exist_ok=True)
    profile.write_text(json.dumps({"kind": "scan-market", "card_rules_version": version}))
    return capsule


def test_v2_row_preserves_failed_model_card_and_machine_suggestion(tmp_path):
    scan = tmp_path / "run/staging/2026-09-01"
    (scan / "details").mkdir(parents=True)
    _freeze_rules(scan)
    path = scan / "details/600000.md"
    path.write_text(FULL_CARD)
    before = path.read_bytes()
    row = _finalist_row(scan, {"code": "600000", "lane": "pinned"})
    assert row["rating"] == "Overweight" and row["proposal"] == "BUY"
    assert row["rubric_suggest"] == "Hold"
    assert row["card_incomplete"] is True
    assert "PASS" in row["card_validation_error"]
    assert path.read_bytes() == before


def test_v2_pinned_unreviewed_card_remains_in_coverage_denominator(tmp_path):
    from autoresearch.scan.report_sections import _review_ctx
    from autoresearch.scan.self_review import review

    scan = tmp_path / "run/staging/2026-09-01"
    (scan / "details").mkdir(parents=True)
    _freeze_rules(scan)
    (scan / "details/000001.md").write_text(
        "**Rating**: Hold\n**早停**:停于 P3 ｜ 停因:资金流出\nFINAL TRANSACTION PROPOSAL: **HOLD**\n"
    )
    row = _finalist_row(scan, {"code": "000001", "lane": "pinned"})
    ctx = _review_ctx(scan, [row])
    result = review(ctx)
    assert ctx["n_cards_expected"] == 1 and ctx["n_cards_present"] == 0
    assert len(ctx["finalists"]) == 1 and row["rating"] == "Hold"
    assert result["ok"] is False
    assert any("PINNED" in item["detail"] for item in result["failures"])


def test_explicit_legacy_bridge_keeps_missing_dimensions_unknown():
    card = card_io.card_from_text(
        EARLY_STOP_CARD, code="000001", analysis_date="2026-09-01", holding=False,
        rules_version="legacy-v1",
    )
    assert set(card["dimensions"].values()) == {"未核"}
    assert card["proposal"] == "HOLD" and card["initial_rating"] == "Underweight"


def test_current_renderer_cannot_use_deviation_to_bypass_gate_fail(scan_dir):
    card = card_io.card_from_markdown(scan_dir, "600000", analysis_date="2026-09-01")
    with pytest.raises(ValueError, match="PASS"):
        card_render.render_anchors(card)


def test_invalid_v2_decision_record_retains_raw_rating_and_failure(tmp_path):
    scan = tmp_path / "run/staging/2026-09-01"
    (scan / "details").mkdir(parents=True)
    _freeze_rules(scan)
    (scan / "details/600000.md").write_text(FULL_CARD)
    row = _finalist_row(scan, {"code": "600000", "lane": "pinned"})
    row["_source_rating"] = row["rating"]
    (record,) = df._build_decision_records(scan, [row], {}, {})
    assert record.source_rating == "Overweight"
    assert record.final_rating == "—" and record.proposal == "—"
    assert record.first_rejection_stage == "L4_CARD_INVALID"
    assert "PASS" in record.reason
    assert row["rating"] == "Overweight"


def test_v2_valid_card_uses_calculated_suggestion_and_stays_complete(tmp_path):
    from autoresearch.scan.report_sections import _review_ctx
    from autoresearch.scan.self_review import review

    scan = tmp_path / "run/staging/2026-09-01"
    (scan / "details").mkdir(parents=True)
    _freeze_rules(scan)
    text = FULL_CARD.replace("估值不透支 ✗", "估值不透支 ✓")
    (scan / "details/600000.md").write_text(text)
    row = _finalist_row(scan, {"code": "600000", "lane": "pinned"})
    assert row["rating"] == "Overweight" and row["rubric_suggest"] == "Overweight"
    assert row["card_incomplete"] is False
    ctx = _review_ctx(scan, [row])
    assert ctx["n_cards_expected"] == ctx["n_cards_present"] == 1
    assert review(ctx)["ok"] is True


def test_v2_empty_deviation_cannot_borrow_next_line_as_reason():
    from autoresearch.scan.l4.rubric import validate_card_decision

    text = "**Rating**: Underweight\n**偏离**:\nFINAL TRANSACTION PROPOSAL: **SELL**\n"
    card = card_io.card_from_text(text, code="600000", analysis_date="2026-09-01", holding=False)
    assert card["rating_deviation_reason"] == ""
    with pytest.raises(ValueError, match="deviation"):
        validate_card_decision(card)


def test_v2_missing_rating_does_not_fabricate_hold(tmp_path):
    scan = tmp_path / "run/staging/2026-09-01"
    (scan / "details").mkdir(parents=True)
    _freeze_rules(scan)
    (scan / "details/600000.md").write_text("分析无结论\nFINAL TRANSACTION PROPOSAL: **HOLD**\n")
    row = _finalist_row(scan, {"code": "600000"})
    assert row["card_incomplete"] and row["rating"] == "—"


def test_v2_invalid_card_is_not_exported_as_a_final_rating(tmp_path):
    rows = [{"code": "600000", "rating": "Overweight", "card_incomplete": True}]
    df._dump_final_ratings(tmp_path, rows)
    assert json.loads((tmp_path / "_final_ratings.json").read_text()) == {"600000": "—"}
    assert rows[0]["rating"] == "Overweight"
