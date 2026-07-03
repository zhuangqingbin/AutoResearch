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
