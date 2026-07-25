"""macro_state 从 spine 直接落盘(Wave5 ③B)。

根因:`write_macro_state` 只需 `1_spine/decision.md`,却只在 `assemble.main()` 里被调用,
而 assemble 要 ~20 个分段齐全 —— 最便宜的机读产物被最贵的门扣着,于是 macro_state 自
2026-06-22 起恒为 None、market_view 一个月开篇都写「无新鲜宏观视图」。本测试锁住解耦。
"""
from __future__ import annotations

import json

from autoresearch.macro import state as S

_DECISION = """# S1 执行摘要
当前处于「增长下行 + 流动性宽松」象限。

- OVERALL 风险档: **Rating**: Overweight
- A股: **Rating**: Overweight
- 美股: **Rating**: Hold
- 黄金: **Rating**: Buy
"""


def _spine(tmp_path, date="2026-07-25", with_optional=False):
    root = tmp_path / date
    (root / "1_spine").mkdir(parents=True)
    (root / "1_spine" / "decision.md").write_text(_DECISION, encoding="utf-8")
    if with_optional:
        (root / "2_meso").mkdir()
        (root / "2_meso" / "sector_map.md").write_text(
            "- 半导体: **Rating**: Overweight\n- 银行: **Rating**: Underweight\n", encoding="utf-8")
        (root / "1_spine" / "premortem.md").write_text(
            "- **美债利率反弹**:压制成长股估值\n", encoding="utf-8")
    return root


def test_readiness_only_requires_decision(tmp_path):
    root = _spine(tmp_path)
    rd = S.state_readiness(root)
    assert rd["ok"] is True
    assert rd["required"] == "1_spine/decision.md"
    assert "2_meso/sector_map.md" in rd["missing_optional"]


def test_readiness_false_without_decision(tmp_path):
    root = tmp_path / "2026-07-25"
    root.mkdir(parents=True)
    assert S.state_readiness(root)["ok"] is False


def test_cli_writes_state_without_full_report(tmp_path, capsys):
    """核心:没有那 20 个分段文件,照样能出 macro_state —— 这就是解耦本身。"""
    root = _spine(tmp_path, with_optional=True)
    rc = S.main([str(root), "--out-dir", str(tmp_path)])
    assert rc == 0
    st = json.loads((tmp_path / S.STATE_NAME).read_text(encoding="utf-8"))
    assert st["as_of"] == "2026-07-25"
    assert st["overall_rating"] == "Overweight"
    assert st["risk_stance"] == "risk_on"
    assert st["cross_asset"]["黄金"] == "Buy"
    assert st["ashare_sectors"]["半导体"] == "Overweight"
    assert st["key_risks"], "premortem 在场时 key_risks 不该为空"
    assert "增长下行" in (st["quadrant_raw"] or "")


def test_cli_fails_loudly_without_decision(tmp_path, capsys):
    root = tmp_path / "2026-07-25"
    root.mkdir(parents=True)
    assert S.main([str(root)]) == 1
    assert "MISSING" in capsys.readouterr().out


def test_written_state_is_consumable_by_loader(tmp_path):
    """闭环:写出来的东西必须能被 load_macro_state 读回(生产者/消费者同源)。"""
    root = _spine(tmp_path)
    S.main([str(root), "--out-dir", str(tmp_path)])
    st, note = S.load_macro_state("2026-07-26", path=tmp_path / S.STATE_NAME)
    assert st is not None, note
    assert "新鲜" in note
