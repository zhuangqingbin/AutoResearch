"""运行模式四态(Wave10 A2):判定 / 不从产物空否反推 / 冻结快照 / 门语义。合成,无网络。"""
from __future__ import annotations

import json

import pandas as pd
import pytest

from autoresearch.scan.gates import gate2, gate4
from autoresearch.scan.run_mode import (
    FORCED_FULL,
    FULL,
    GATE2_SKIP_REASON,
    SENTINEL_EMPTY,
    SENTINEL_PINNED,
    RunMode,
    RunModeError,
    build_pinned_finalists,
    decide,
    load,
    pinned_from_contract,
    write,
    write_pinned_finalists,
)

# ────────────────────────── 四态判定 ──────────────────────────

@pytest.mark.parametrize("level,force,codes,expect", [
    ("full", False, [], FULL),
    ("full", False, ["300857"], FULL),
    ("sentinel", True, ["300857"], FORCED_FULL),
    ("sentinel", True, [], FORCED_FULL),
    ("sentinel", False, ["300857"], SENTINEL_PINNED),
    ("sentinel", False, [], SENTINEL_EMPTY),
])
def test_four_states(level, force, codes, expect):
    assert decide(sentinel_level=level, force_full=force,
                  pinned_codes=codes).mode == expect


def test_sentinel_pinned_declares_no_selection_conclusion():
    """中间档的全部意义:跑了持仓 L4,但**没有**全市场选股结论、**没有** L3 判断。"""
    m = decide(sentinel_level="sentinel", force_full=False, pinned_codes=["300857"])
    assert m.has_selection_conclusion is False and m.has_l3_judgment is False
    assert m.is_sentinel


def test_forced_full_is_not_disguised_as_full():
    """人推翻了确定性判据 —— 报告要说出来,不能长得跟正常全扫一样。"""
    m = decide(sentinel_level="sentinel", force_full=True, pinned_codes=[],
               sentinel_reason="材料枯竭")
    assert m.mode == FORCED_FULL and m.mode != FULL
    assert "override" in m.banner() and "材料枯竭" in m.banner()


def test_full_run_has_no_banner():
    assert decide(sentinel_level="full", force_full=False, pinned_codes=[]).banner() == ""


def test_banner_states_no_market_wide_conclusion():
    m = decide(sentinel_level="sentinel", force_full=False,
               pinned_codes=["300857", "688766"], sentinel_reason="健康上涨 1.9%")
    banner = m.banner()
    assert "仅持仓复核" in banner and "无全市场选股结论" in banner
    assert "2 只" in banner and "健康上涨 1.9%" in banner


def test_codes_are_normalized_and_deduped():
    m = decide(sentinel_level="sentinel", force_full=False,
               pinned_codes=["857", "000857", "857.SZ", ""])
    assert m.pinned_codes == ["000857"]


def test_unknown_mode_is_rejected():
    with pytest.raises(RunModeError):
        RunMode(mode="MAYBE")


# ────────────────── 只读文件,不从产物空否反推 ──────────────────

def test_missing_file_is_unknown_not_full(tmp_path):
    """§R9:缺文件是"不知道",**不是** FULL —— 调用方不得因此推断模式。"""
    assert load(tmp_path) is None


def test_roundtrip(tmp_path):
    m = decide(sentinel_level="sentinel", force_full=False, pinned_codes=["300857"],
               sentinel_reason="材料枯竭", contract_hash="abc")
    write(tmp_path, m)
    back = load(tmp_path)
    assert back is not None and back.mode == SENTINEL_PINNED
    assert back.contract_hash == "abc" and back.pinned_codes == ["300857"]
    assert back.created_at.endswith("Z")


# ────────────────── 候选只来自冻结快照 ──────────────────

def test_candidates_come_from_the_frozen_contract_not_the_config(tmp_path):
    """`pinned.jsonc` 跑后可能被改;报告必须忠实于**那次运行**。"""
    (tmp_path / "run_contract.json").write_text(json.dumps({
        "contract_hash": "h1",
        "pinned": {"kept": [{"code": "300857"}, {"code": "688766"}]},
    }), encoding="utf-8")
    # 故意放一份内容不同的 pinned.jsonc —— 它不该被读
    (tmp_path / "pinned.jsonc").write_text('[{"code":"999999"}]', encoding="utf-8")
    codes, digest = pinned_from_contract(tmp_path)
    assert codes == ["300857", "688766"] and digest == "h1"
    assert "999999" not in codes


def test_missing_contract_yields_no_candidates(tmp_path):
    assert pinned_from_contract(tmp_path) == ([], None)


def test_pinned_finalists_fill_from_todays_l2(tmp_path):
    pd.DataFrame([{"code": "300857", "name": "协创数据", "sector": "计算机",
                   "gbdt_score": 71.0}]).to_csv(
        tmp_path / "L2_gbdt_top200.csv", index=False)
    frame = build_pinned_finalists(tmp_path, ["300857"])
    row = frame.iloc[0]
    assert row["name"] == "协创数据" and row["sector"] == "计算机"
    assert row["lane"] == "pinned" and row["selection_source"] == "sentinel_pinned"
    assert bool(row["l3_judged"]) is False and bool(row["data_missing"]) is False


def test_missing_l2_row_degrades_honestly_instead_of_dropping_the_stock(tmp_path):
    """缺行不是"跳过这只票" —— 持仓复核照跑,只是把"确定性字段一无所知"写在脸上。"""
    frame = build_pinned_finalists(tmp_path, ["300857"])
    assert len(frame) == 1
    assert bool(frame.iloc[0]["data_missing"]) is True
    assert frame.iloc[0]["name"] == ""


def test_written_finalists_are_pinned_only(tmp_path):
    (tmp_path / "run_contract.json").write_text(json.dumps({
        "contract_hash": "h", "pinned": {"kept": [{"code": "300857"}]}}),
        encoding="utf-8")
    codes, _ = pinned_from_contract(tmp_path)
    write_pinned_finalists(tmp_path, codes)
    frame = pd.read_csv(tmp_path / "finalists.csv", dtype={"code": str})
    assert list(frame["lane"]) == ["pinned"]
    assert not list(tmp_path.glob("*.tmp"))


# ────────────────────────── 门语义 ──────────────────────────

def test_gate2_skip_is_not_a_pass(tmp_path):
    """哨兵·仅持仓档没有 L3 → 这道门**不适用**,不是"通过"。伪造通过会让
    「L3 跑过且选空」与「L3 根本没跑」在账上长得一样。"""
    res = gate2(tmp_path, skip_reason=GATE2_SKIP_REASON)
    assert res["ok"] is True
    assert res["status"] == "SKIPPED_NOT_APPLICABLE"
    assert res["reason"] == GATE2_SKIP_REASON
    assert res["n"] == 0


def test_gate2_without_skip_still_fails_on_missing_finalists(tmp_path):
    res = gate2(tmp_path)
    assert res["ok"] is False and "status" not in res


def _fires(tmp_path, rows):
    import csv
    with (tmp_path / "gate_fires.csv").open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["date", "code", "check", "severity", "detail"])
        w.writeheader()
        for r in rows:
            w.writerow(r)


def test_gate4_drops_buylist_dependent_fails_when_there_is_no_selection(tmp_path):
    _fires(tmp_path, [{"date": "d", "code": "", "check": "覆盖率不足",
                       "severity": "fail", "detail": "决策卡 0/10"}])
    assert gate4(tmp_path)["ok"] is False          # 无 run_mode → 照旧失败

    write(tmp_path, decide(sentinel_level="sentinel", force_full=False,
                           pinned_codes=["300857"]))
    res = gate4(tmp_path)
    assert res["ok"] is True and res["run_mode"] == SENTINEL_PINNED
    assert "无选股结论" in res["reason"]


def test_gate4_still_enforces_everything_else_in_sentinel_pinned(tmp_path):
    """不要求 buy-list ≠ 什么都不要求 —— 持仓卡的完备性照查。"""
    _fires(tmp_path, [{"date": "d", "code": "300857", "check": "经验红线·获利盘满",
                       "severity": "fail", "detail": "winner_rate 92"}])
    write(tmp_path, decide(sentinel_level="sentinel", force_full=False,
                           pinned_codes=["300857"]))
    assert gate4(tmp_path)["ok"] is False


def test_gate4_full_mode_is_unchanged(tmp_path):
    _fires(tmp_path, [{"date": "d", "code": "", "check": "覆盖率不足",
                       "severity": "fail", "detail": "决策卡 1/10"}])
    write(tmp_path, decide(sentinel_level="full", force_full=False, pinned_codes=[]))
    assert gate4(tmp_path)["ok"] is False          # FULL 档照旧被这条 fail 卡住


# ────────────────────────── 接线守卫 ──────────────────────────

def test_workflow_reads_run_mode_and_has_the_middle_branch():
    from pathlib import Path

    src = Path(".claude/workflows/scan-market.js").read_text(encoding="utf-8")
    assert "autoresearch.scan.run_mode" in src
    assert "SENTINEL_PINNED" in src and "SENTINEL_EMPTY" in src
    assert GATE2_SKIP_REASON in src
    # 中间档必须真的派发持仓、而不是像旧哨兵那样直接 return 0 只
    branch = src.split("if (runMode === 'SENTINEL_PINNED')")[1][:900]
    assert "l4-handoff" in branch and "dispatch" in branch


def test_report_banner_is_read_from_the_file():
    from pathlib import Path

    src = Path("autoresearch/scan/report_sections.py").read_text(encoding="utf-8")
    assert "from autoresearch.scan.run_mode import load as _load_run_mode" in src
    assert "_mode.banner()" in src
