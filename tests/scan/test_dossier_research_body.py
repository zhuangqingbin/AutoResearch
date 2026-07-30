"""决策卡档案研报体(Wave9 B-3):`product_shape_lint` 探针 10。

有档案票(`_dossier_present.json` 记录)必须写「研报体(档案δ)」或「微研报」段;
无档案票必须写「档案未建」缺档声明行。两侧口径都对齐 `.claude/agents/l4-card.md`
模板措辞——这是同一批改动的两半,漂了就是「lint 罚一个没被告知过的 agent」。
"""
from autoresearch.learning.self_review import product_shape_lint


def _mk(tmp_path, card_body: str, has_dossier: bool):
    d = tmp_path / "2026-07-29"
    (d / "details").mkdir(parents=True)
    (d / "details" / "000651.md").write_text(card_body, encoding="utf-8")
    (d / "finalists.csv").write_text("code,name,lane\n000651,格力电器,healthy\n",
                                     encoding="utf-8")
    (d / "_dossier_present.json").write_text(
        '["000651"]' if has_dossier else "[]", encoding="utf-8")
    return d


FULL = """# 决策卡 — 000651 格力电器 @ 2026-07-29
## 维度评分卡
| 基本面 | 强 |
**Rating**: Hold
"""


def test_warns_when_dossier_exists_but_no_research_body(tmp_path):
    d = _mk(tmp_path, FULL, has_dossier=True)
    hits = product_shape_lint(d, "2026-07-29")
    assert any("研报体" in h["check"] for h in hits)


def test_no_warn_when_research_body_present(tmp_path):
    d = _mk(tmp_path, FULL + "\n## 研报体(档案δ)\n- **业务**:空调\n", has_dossier=True)
    hits = product_shape_lint(d, "2026-07-29")
    assert not any("研报体" in h["check"] for h in hits)


def test_no_warn_when_no_dossier_but_declared(tmp_path):
    d = _mk(tmp_path, FULL + "\n研报体:档案未建(已插队今晚建档)\n", has_dossier=False)
    hits = product_shape_lint(d, "2026-07-29")
    assert not any("研报体" in h["check"] for h in hits)


def test_warns_when_no_dossier_and_no_declaration(tmp_path):
    d = _mk(tmp_path, FULL, has_dossier=False)
    hits = product_shape_lint(d, "2026-07-29")
    assert any("研报体" in h["check"] for h in hits)
