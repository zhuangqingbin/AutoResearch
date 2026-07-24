"""卡片契约 lint:P4 倾向/变化项/复用与早停豁免 + banner 合并留痕。合成,无网络。

spec: docs/specs/2026-07-03-scan-run-reliability-design.md §1
"""
from __future__ import annotations

import pandas as pd

from autoresearch.learning.self_review import card_contract_lint

FULL_OK = "# 卡\n**Rating**: Hold\n进入P4倾向: Hold\n变化项(vs 档案):无\n"
FULL_NO_P4 = "# 卡\n**Rating**: Hold\n**一行多空**:多:x ｜ 空:y\n"
STOP = "# 卡\n**Rating**: Hold\n早停因: 资金流出\n"
REUSE = "♻️ 复用卡\n**Rating**: Hold\n"
# 07-06 假阳:标题标〔早停·表面 DD〕但正文无「早停因」字样的卡,被当满卡查 P4 行
STOP_TITLE_ONLY = ("# 决策卡 — 002185 华天科技 @ 2026-07-06  ·  〔早停·表面 DD〕\n\n"
                    "**Rating**: Underweight\n\nFINAL TRANSACTION PROPOSAL: **HOLD**\n")
# 满卡标题正常,正文却带一个含「早停」的说明性小标题——豁免只认首行,warn 不得被吞
FULL_STRAY_HEADING = ("# 决策卡 — 000001 甲 @ 2026-07-06\n\n**Rating**: Hold\n\n"
                      "## 早停判断:未触发,完整走完 P4+P5\n\n"
                      "FINAL TRANSACTION PROPOSAL: **HOLD**\n")


def _mk(root, date, cards):
    d = root / date
    (d / "details").mkdir(parents=True)
    for code, text in cards.items():
        (d / "details" / f"{code}.md").write_text(text, encoding="utf-8")
    return d


def test_p4_line_lint(tmp_path):
    d = _mk(tmp_path, "2026-07-03",
            {"000001": FULL_NO_P4, "000002": STOP, "000003": REUSE, "000004": FULL_OK})
    out = card_contract_lint(d)
    checks = {(x["code"], x["check"]) for x in out}
    assert ("000001", "卡片契约·P4倾向缺失") in checks
    assert not any(c == "000002" for c, _ in checks)          # 早停免 P4 检
    assert not any(c == "000003" for c, _ in checks)          # 复用卡全豁免
    assert not any(c == "000004" for c, _ in checks)
    assert all(x["severity"] == "warn" for x in out)


def test_early_stop_title_card_exempt_from_p4_line(tmp_path):
    # 标题行含〔早停…〕即认早停卡,不强求正文再写「早停因」——两代卡片格式都豁免 P4 检
    d = _mk(tmp_path, "2026-07-06", {"002185": STOP_TITLE_ONLY})
    fires = card_contract_lint(d)
    assert not [f for f in fires if f["check"].startswith("卡片契约·P4倾向")], fires


def test_full_card_with_stray_early_stop_heading_still_warns(tmp_path):
    # 豁免面收紧回归:满卡正文的杂散「早停」小标题不构成早停卡,缺 P4 行必须照旧 warn
    d = _mk(tmp_path, "2026-07-06", {"000001": FULL_STRAY_HEADING})
    fires = card_contract_lint(d)
    assert [f for f in fires if f["check"] == "卡片契约·P4倾向缺失"], fires


def test_dossier_change_section_lint(tmp_path):
    # 前日该票有卡 → 今日档案可注入 → 卡缺"变化项" → warn
    prev = _mk(tmp_path, "2026-07-02", {"000001": FULL_OK})
    pd.DataFrame([{"code": "000001", "name": "甲", "sector": "半导体", "lane": "trend",
                   "conviction": 60, "risk": "r"}]).to_csv(prev / "finalists.csv", index=False)
    d = _mk(tmp_path, "2026-07-03", {"000001": FULL_NO_P4})
    out = card_contract_lint(d)
    assert any(x["check"] == "卡片契约·变化项缺失" for x in out)
    (d / "details" / "000001.md").write_text(FULL_OK, encoding="utf-8")
    assert not any(x["check"] == "卡片契约·变化项缺失" for x in card_contract_lint(d))


def _mk_cov_dossier(code):
    from autoresearch.dossier import schema
    p = schema.dossier_path(code)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("---\ncode: " + code + "\nname: x\nsector: x\npool_status: active\n"
                 "entered: 2026-07-23\nentry_reason: pinned\ninitiated: 2026-07-23\n"
                 "last_refresh: null\nlast_delta: null\n---\n", encoding="utf-8")


def test_card_lint_covered_stock_requires_reconcile_section(tmp_path):
    from autoresearch.learning.self_review import card_contract_lint
    d = tmp_path / "details"
    d.mkdir(parents=True)
    _mk_cov_dossier("300857")
    (d / "300857.md").write_text(FULL_OK, encoding="utf-8")        # 有变化项、无档案对账
    warns = [w for w in card_contract_lint(tmp_path)
             if w["check"] == "卡片契约·档案对账缺失"]
    assert len(warns) == 1 and warns[0]["code"] == "300857"


def test_card_lint_covered_stock_with_reconcile_ok(tmp_path):
    from autoresearch.learning.self_review import card_contract_lint
    d = tmp_path / "details"
    d.mkdir(parents=True)
    _mk_cov_dossier("300858")
    (d / "300858.md").write_text(FULL_OK + "\n**档案对账**:驱动无变化;风险无触发;判例一致\n",
                                 encoding="utf-8")
    assert not [w for w in card_contract_lint(tmp_path)
                if w["check"] == "卡片契约·档案对账缺失"]


def test_banner_merges_lint_and_gate_fires(tmp_path):
    """assemble banner 合并 lint(且 gate_fires 留痕)。"""
    from autoresearch.scan.assemble import build_summary
    d = tmp_path / "s"
    d.mkdir()
    (d / "meta.json").write_text("{}", encoding="utf-8")
    pd.DataFrame([{"code": "000001", "name": "甲", "sector": "半导体"}]).to_csv(
        d / "finalists.csv", index=False)
    (d / "details").mkdir()
    (d / "details" / "000001.md").write_text(FULL_NO_P4, encoding="utf-8")
    md = build_summary(d, "2026-07-03", "1200", "20260703_1200")
    assert "卡片契约·P4倾向缺失" in md
    gf = (d / "gate_fires.csv").read_text(encoding="utf-8")
    assert "P4倾向缺失" in gf                                  # lint 在 dump 前合并 → 留痕
