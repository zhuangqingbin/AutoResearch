"""发布层 details 卡片尾部并入活体情报原文(fb_20260714_004)。NO network.

用户反馈:details 里看不到 L4 intel 的新闻依据。修法 = `_publish_details` 发布副本尾部附
`_l4_intel_<code>.md` 原文(presence-gated,staging 卡不动);评级解析不受附录影响
(parse_rating 两遍法先认卡面 `Rating:` 标签行)。
"""
from __future__ import annotations

from autoresearch.agents.utils.rating import parse_rating
from autoresearch.scan.assemble import _publish_details

_CARD = (
    "# 决策卡 — {code} 测试股 @ 2026-07-14\n\n"
    "| 评级 | 现价 |\n|---|---|\n| **Hold** | 10.0 |\n\n"
    "**Rating**: **Hold**\n\nFINAL TRANSACTION PROPOSAL: **HOLD**\n"
)


def _scan(tmp_path, codes):
    d = tmp_path / "scan"
    (d / "details").mkdir(parents=True)
    rows = ["code,name,sector"] + [f"{c},测试股{c},半导体" for c in codes]
    (d / "finalists.csv").write_text("\n".join(rows) + "\n", encoding="utf-8")
    for c in codes:
        (d / "details" / f"{c}.md").write_text(_CARD.format(code=c), encoding="utf-8")
    out = tmp_path / "out"
    out.mkdir()
    return d, out


def test_intel_appended_when_present(tmp_path):
    d, out = _scan(tmp_path, ["000651"])
    (d / "_l4_intel_000651.md").write_text(
        "# 活体情报 — 000651\n| 2026-07-14 | 欧洲高温空调断货 | 央视财经 | 是 | +1 |\n",
        encoding="utf-8")
    assert _publish_details(d, out) == 1
    md = (out / "测试股000651.md").read_text(encoding="utf-8")
    assert "🕵️ 当日活体情报" in md and "欧洲高温空调断货" in md
    # 附录不得污染评级解析(intel 里出现"买入/涨停"等字样时尤其重要)
    assert parse_rating(md) == "Hold"
    # staging 卡不动
    assert "活体情报" not in (d / "details" / "000651.md").read_text(encoding="utf-8")


def test_no_intel_no_appendix(tmp_path):
    d, out = _scan(tmp_path, ["300857"])
    assert _publish_details(d, out) == 1
    md = (out / "测试股300857.md").read_text(encoding="utf-8")
    assert "活体情报" not in md


def test_intel_with_rating_words_does_not_flip_parse(tmp_path):
    """intel 常含券商评级词(如研报"维持买入");附在卡尾不得翻转卡面评级。"""
    d, out = _scan(tmp_path, ["002371"])
    (d / "_l4_intel_002371.md").write_text(
        "# 活体情报 — 002371\n浙商证券研报维持\"买入\"评级;另有报道称当日涨停(Buy side chatter)。\n",
        encoding="utf-8")
    _publish_details(d, out)
    md = (out / "测试股002371.md").read_text(encoding="utf-8")
    assert parse_rating(md) == "Hold"
