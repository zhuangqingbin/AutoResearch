from pathlib import Path

from autoresearch.learning.self_review import product_shape_lint


def _mk(tmp_path: Path, card: str, code: str = "688689") -> Path:
    sd = tmp_path / "2026-07-21"
    (sd / "details").mkdir(parents=True)
    (sd / "details" / f"{code}.md").write_text(card, encoding="utf-8")
    (sd / "finalists.csv").write_text(f"code,name,sector,lane\n{code},银河微电,半导体,\n",
                                      encoding="utf-8")
    return sd


FULL_SPARSE = ("# 决策卡 — 688689 银河微电 @ 2026-07-21\n进入P4倾向: Hold\n"
               "07-19 一条引用\n**Rating**: Underweight\n")
EARLY_STOP = ("# 决策卡 — 688689 银河微电 @ 2026-07-21  ·  〔早停·表面 DD〕\n"
              "**Rating**: Hold\n")
FULL_RICH = ("# 决策卡 — 688689 银河微电 @ 2026-07-21\n进入P4倾向: Hold\n"
             + "\n".join(f"2026-07-{d:02d} 引用{d}" for d in range(10, 17))
             + "\n**Rating**: Hold\n")


def _hits(out, check):
    return [o for o in out if o["check"] == check]


def test_citation_density_warns_sparse_full_card(tmp_path):
    sd = _mk(tmp_path, FULL_SPARSE)
    out = product_shape_lint(sd, "2026-07-21")
    hits = _hits(out, "citation_density")
    assert len(hits) == 1 and hits[0]["severity"] == "warn" and hits[0]["code"] == "688689"


def test_citation_density_exempts_early_stop_and_rich(tmp_path):
    assert not _hits(product_shape_lint(_mk(tmp_path, EARLY_STOP), "2026-07-21"),
                     "citation_density")


def test_citation_density_rich_card_clean(tmp_path):
    assert not _hits(product_shape_lint(_mk(tmp_path, FULL_RICH), "2026-07-21"),
                     "citation_density")


def test_price_claim_mismatch_probe(tmp_path, monkeypatch):
    card = ("# 决策卡 — 688689 银河微电 @ 2026-07-21\n进入P4倾向: Hold\n"
            "银河微电 07-21 大涨 9.5%\n" + FULL_RICH.split("进入P4倾向: Hold\n")[1])
    sd = _mk(tmp_path, card)
    monkeypatch.setattr("autoresearch.scan.price_claims.bars_for",
                        lambda c, ds, today: {"20260721": 1.0})
    hits = _hits(product_shape_lint(sd, "2026-07-21"), "price_claim_mismatch")
    assert len(hits) == 1 and hits[0]["severity"] == "warn"
