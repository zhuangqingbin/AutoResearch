"""intel 结构化状态 A5/A6/A7:三正交字段 / 瞬时错重试 / 旧事件归一化。合成,无网络。"""
from __future__ import annotations

import json

import pytest

from autoresearch.scan.l4.intel_status import (
    MAX_ATTEMPTS,
    TRANSIENT_ERRORS,
    IntelStatus,
    IntelStatusError,
    from_guard,
    is_transient,
    load_status,
    normalize_stale_scores,
    runtime_cap,
    trimmed_note,
    write_normalization,
    write_status,
)

NEW_HDR = ("| 日期 | 时效窗 | 事件(一行,含量级) | 源 | 净分 |\n"
           "|---|---|---|---|---|\n")
OLD_HDR = ("| 日期 | 事件(一行,含量级) | 源 | 2日内可发酵? | 净分 |\n"
           "|---|---|---|---|---|\n")


# ────────────────────── A5:三条正交字段 ──────────────────────

def test_three_fields_are_orthogonal():
    """取数成功的稿可能被裁、被裁的稿卡照样读 —— 三件事互不蕴含。"""
    st = from_guard({"action": "TRIMMED", "claimed": 39, "hard_cap": 30,
                     "dropped_rows": 0}, code="000651", scan_dir="/nope")
    assert st.acquisition == "FULL"                 # 取到了
    assert st.guard == "TRIMMED"                    # 被守卫审计过
    assert st.availability_for_card == "INTEL"      # 卡照样读得到


def test_disabled_is_not_degraded():
    """主动关功能不是事故 —— 混在一起会让「关了」和「挂了」长得一样。"""
    off = from_guard(None, code="x", scan_dir="/nope", enabled=False)
    assert off.acquisition == "DISABLED" and not off.degraded
    assert "非事故" in off.headline()

    dead = from_guard(None, code="x", scan_dir="/nope", attempts=3,
                      error_class="ENOTFOUND")
    assert dead.acquisition == "DEGRADED" and dead.degraded
    assert dead.availability_for_card == "CARD_FALLBACK"


def test_trimmed_with_zero_dropped_rows_does_not_claim_a_real_cut():
    """§A5:`TRIMMED, dropped_rows=0` 不得伪装成"真裁了内容"(07-31 000651 实况)。"""
    note = trimmed_note(0)
    assert "未删事件行" in note and "已裁剪" not in note
    assert "按时效窗裁掉" in trimmed_note(4)
    assert "UNMEASURED" in trimmed_note(None)      # 不知道就说不知道


def test_rejected_falls_back_to_card_websearch():
    st = from_guard({"action": "REJECTED", "claimed": 40}, code="x", scan_dir="/nope")
    assert st.guard == "REJECTED" and st.availability_for_card == "CARD_FALLBACK"


def test_retried_full_is_distinguishable_from_first_try():
    st = from_guard({"action": "KEPT", "claimed": 9}, code="x", scan_dir="/nope",
                    attempts=2)
    assert st.acquisition == "RETRIED_FULL" and "重试后成功" in st.headline()


def test_unknown_enum_is_rejected_at_construction():
    """状态不合契约要在**构造时**失败,不能等报告去解析自然语言反推。"""
    with pytest.raises(IntelStatusError):
        IntelStatus(code="x", acquisition="MAYBE")
    with pytest.raises(IntelStatusError):
        from_guard({"action": "WEIRD"}, code="x", scan_dir="/nope")


def test_runtime_cap_reads_the_frozen_echo_only(tmp_path):
    """cap 的唯一事实源是**冻结的** echo —— 跑后被改的 scan_config 现值不算数。"""
    assert runtime_cap(tmp_path) is None            # 取不到就是 None,不猜默认值
    (tmp_path / "user_config_echo.json").write_text(
        json.dumps({"l4_intel": {"max_queries": 20}}), encoding="utf-8")
    assert runtime_cap(tmp_path) == 20


def test_status_roundtrips_through_disk(tmp_path):
    st = from_guard({"action": "KEPT", "claimed": 8}, code="000001", scan_dir=tmp_path)
    write_status(tmp_path, st)
    back = load_status(tmp_path, "000001")
    assert back is not None and back.guard == "KEPT"
    assert load_status(tmp_path, "999999") is None
    assert not list(tmp_path.glob("*.tmp"))


# ────────────────────── A6:只重试瞬时错 ──────────────────────

@pytest.mark.parametrize("err", TRANSIENT_ERRORS)
def test_transient_errors_are_retryable(err):
    assert is_transient(err) and is_transient(err.lower())


@pytest.mark.parametrize("err", ["SCHEMA_ERROR", "OTHER", "", None])
def test_non_transient_errors_are_not_retried(err):
    """重试 SCHEMA_ERROR 只是把一次失败变成三次失败 + 三倍延迟。"""
    assert not is_transient(err)


def test_max_attempts_is_first_try_plus_two_retries():
    assert MAX_ATTEMPTS == 3


def test_degraded_headline_is_rendered_from_state_not_written_by_an_llm():
    st = from_guard(None, code="x", scan_dir="/nope", attempts=3,
                    error_class="ENOTFOUND")
    line = st.headline()
    assert line.startswith("🕳️") and "ENOTFOUND" in line and "3 次" in line


# ────────────────────── A7:旧事件净分归一化 ──────────────────────

def test_normalizer_actually_fires_on_a_stale_row():
    """**这条最重要**:全语料历史上零改动,所以必须有一个合成用例证明它开得了火,
    否则它就是个永不变红的绿灯。"""
    text = NEW_HDR + "| 2026-07-01 | 背景 | 老新闻 | [源](http://x) | +1.0 |\n"
    fixed, norm = normalize_stale_scores(text, "2026-07-31", code="000001")
    assert len(norm.changed) == 1
    assert norm.changed[0]["before"] == 1.0 and norm.changed[0]["after"] == 0.0
    assert norm.changed[0]["gap_days"] == 30
    assert "0.0" in fixed.splitlines()[-1]
    assert "+1.0 |" not in fixed.splitlines()[-1]


def test_catalyst_window_is_exempt_from_decay():
    """`催化挂` 只认结构化字段;它不该被衰减(300857 的 06-04 行正是这种)。"""
    text = NEW_HDR + "| 2026-06-04 | 催化挂(Q3放量未兑现) | 待兑现 | [源](http://x) | +1.0 |\n"
    _, norm = normalize_stale_scores(text, "2026-07-31", code="300857")
    assert norm.changed == [] and norm.unmeasured == []


def test_catalyst_is_not_guessed_from_the_body_text():
    """不从正文猜「中报/投产」等词 —— 猜词会让普通背景新闻免于衰减。"""
    text = NEW_HDR + "| 2026-07-01 | 背景 | 中报投产催化在望 | [源](http://x) | +1.0 |\n"
    _, norm = normalize_stale_scores(text, "2026-07-31", code="000001")
    assert len(norm.changed) == 1        # 正文里有"催化"也照样衰减


def test_fresh_rows_are_untouched():
    text = NEW_HDR + "| 2026-07-28 | 24h | 新事件 | [源](http://x) | +1.0 |\n"
    fixed, norm = normalize_stale_scores(text, "2026-07-31", code="000001")
    assert norm.changed == [] and fixed == text


def test_already_zero_rows_are_untouched():
    text = NEW_HDR + "| 2026-07-01 | 背景 | 老新闻 | [源](http://x) | 0.0 |\n"
    _, norm = normalize_stale_scores(text, "2026-07-31", code="000001")
    assert norm.changed == []


def test_decayed_arrow_form_is_read_as_the_post_decay_value():
    """真稿格式 `+1→0.0(>1周衰减)`:生效值是箭头**之后**那个数,已衰减不该再判未衰减。"""
    text = NEW_HDR + "| 2026-07-01 | >1周 | 老新闻 | [源](http://x) | +1→0.0(>1周衰减) |\n"
    _, norm = normalize_stale_scores(text, "2026-07-31", code="000001")
    assert norm.changed == [] and norm.unmeasured == []


def test_old_schema_without_a_window_column_is_unmeasured_with_the_right_reason():
    """旧稿(51 份真稿)没有时效窗列 → UNMEASURED,且理由要说对是「无该列」。"""
    text = OLD_HDR + "| 2026-07-01 | 老新闻 | [源](http://x) | 否 | +1.0 |\n"
    _, norm = normalize_stale_scores(text, "2026-07-31", code="000001")
    assert norm.changed == [] and len(norm.unmeasured) == 1
    assert "无时效窗列" in norm.unmeasured[0]["reason"]


def test_column_positions_are_taken_from_the_header_not_assumed():
    """两代 schema 的净分列位置不同 —— 按位置解析会在半数语料上失明。"""
    text = OLD_HDR + "| 2026-07-01 | 事件 | [源](http://x) | 否 | +1.0 |\n"
    _, norm = normalize_stale_scores(text, "2026-07-31")
    assert "净分格不可解析" not in json.dumps(norm.to_dict(), ensure_ascii=False)


def test_bad_date_or_unknown_window_is_unmeasured_not_changed():
    text = (NEW_HDR
            + "| 2026-13-45 | 背景 | 坏日期 | [源](http://x) | +1.0 |\n"
            + "| 2026-07-01 | 谜之窗 | 未知窗 | [源](http://x) | +1.0 |\n")
    fixed, norm = normalize_stale_scores(text, "2026-07-31")
    assert norm.changed == [] and len(norm.unmeasured) == 2
    assert fixed == text                       # 不判 ≠ 判它合规:一个字都不改


def test_missing_header_is_unmeasured():
    _, norm = normalize_stale_scores("没有表格\n", "2026-07-31")
    assert "表头缺失" in norm.unmeasured[0]["reason"]


def test_normalization_is_persisted_with_provenance(tmp_path):
    text = NEW_HDR + "| 2026-07-01 | 背景 | 老新闻 | [源](http://x) | +1.0 |\n"
    _, norm = normalize_stale_scores(text, "2026-07-31", code="000001")
    path = write_normalization(tmp_path, norm)
    assert path is not None
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["code"] == "000001" and len(payload["source_hash"]) == 64
    assert payload["changed"][0]["before"] == 1.0


def test_no_change_no_file(tmp_path):
    text = NEW_HDR + "| 2026-07-28 | 24h | 新事件 | [源](http://x) | +1.0 |\n"
    _, norm = normalize_stale_scores(text, "2026-07-31", code="000001")
    assert write_normalization(tmp_path, norm) is None


# ────────────────────── 接线守卫 ──────────────────────

def test_agent_definition_no_longer_hardcodes_a_cap():
    """§A5:角色里写死数字会和当日配置形成两个事实源(此前 ≤15 vs 配置 20)。"""
    from pathlib import Path

    src = Path(".claude/agents/l4-intel.md").read_text(encoding="utf-8")
    assert "≤15" not in src and "≤ 15" not in src
    assert "runtime cap" in src and "user_config_echo" in src


def test_workflow_retries_transient_intel_errors_and_records_status():
    from pathlib import Path

    src = Path(".claude/workflows/l4-stock.js").read_text(encoding="utf-8")
    for anchor in ("ENOTFOUND", "非瞬时错不重试", "intel_status", "--normalize"):
        assert anchor in src, anchor
