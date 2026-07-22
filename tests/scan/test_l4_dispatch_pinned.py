from pathlib import Path

from autoresearch.scan.agents.l4_card import dispatch_plan


def test_dispatch_meta_carries_pinned_flag(tmp_path: Path):
    sd = tmp_path / "2026-07-21"
    sd.mkdir(parents=True)
    (sd / "finalists.csv").write_text(
        "code,name,sector,lane\n300857,协创数据,消费电子,pinned\n002926,华西证券,证券Ⅱ,healthy\n",
        encoding="utf-8")
    for c in ("300857", "002926"):
        (sd / f"_l4_prompt_{c}.md").write_text("pkg", encoding="utf-8")
    plan = dispatch_plan("2026-07-21", root=tmp_path)
    assert plan["meta"]["300857"]["pinned"] is True
    assert plan["meta"]["002926"]["pinned"] is False
