import json
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


# ── I-1 修(2026-07-23 终审):_DATED 收紧为真日历日,比值/区间/百分区间不再虚增 n_cited ──
# 干扰行(比值 R:R 1.8/1、PE band 20-30、百分区间 5-10%)旧 _DATED 全计成日期 → n_cited 虚增 6,
# 满卡 <6 门槛「恒绿打不响」;收紧后只计真日历行(月1-12/日1-31/后不接%),该卡回落到 3 行 → warn。
FULL_NOISE = ("# 决策卡 — 688689 银河微电 @ 2026-07-21\n进入P4倾向: Hold\n"
              "R:R 1.8/1 全表最高\n"          # 干扰1:比值,不该计
              "PE band 20-30 估值中枢\n"      # 干扰2:估值区间,不该计
              "预计 5-10% 下行空间\n"          # 干扰3:百分比区间,不该计
              "07-19 龙虎榜机构净买入\n"        # 真日期行:计
              "07-18 券商密集调研\n"           # 真日期行:计
              "**Rating**: Hold\n")


def test_citation_density_ignores_ratio_and_range_noise(tmp_path):
    sd = _mk(tmp_path, FULL_NOISE)
    hits = _hits(product_shape_lint(sd, "2026-07-21"), "citation_density")
    # 旧 _DATED:6 行(含 3 干扰)≥6 恒绿不 warn;收紧后 3 行(标题+2 真日期)<6 → warn
    assert len(hits) == 1 and hits[0]["severity"] == "warn"
    assert "3 行" in hits[0]["detail"], hits[0]["detail"]


# ── I-2 修(2026-07-23 终审):pinned SELL 双复核 tripwire(probe 9)——招牌 SELL 双复核无防呆 ──

def _mk_pinned(tmp_path: Path, rating: str, ensemble: dict | None, code: str = "300857") -> Path:
    sd = tmp_path / "2026-07-21"
    (sd / "details").mkdir(parents=True)
    (sd / "details" / f"{code}.md").write_text(
        f"# 决策卡 — {code} 协创数据 @ 2026-07-21\n进入P4倾向: {rating}\n"
        f"2026-07-21 持仓复核\n**Rating**: {rating}\n", encoding="utf-8")
    (sd / "finalists.csv").write_text(
        f"code,name,sector,lane\n{code},协创数据,消费电子,pinned\n", encoding="utf-8")
    if ensemble is not None:
        (sd / f"_ensemble_{code}.json").write_text(json.dumps(ensemble), encoding="utf-8")
    return sd


def test_sell_review_missing_warns_pinned_underweight(tmp_path):
    # pinned 持仓卡 = Underweight 但没有 _ensemble_ 文件 → 双复核静默漏跑 → warn
    sd = _mk_pinned(tmp_path, "Underweight", ensemble=None)
    hits = _hits(product_shape_lint(sd, "2026-07-21"), "sell_review_missing")
    assert len(hits) == 1 and hits[0]["severity"] == "warn" and hits[0]["code"] == "300857"


def test_sell_review_present_exempts(tmp_path):
    # pinned = Sell 且 _ensemble_ 存在且 trigger=sell_review → 双复核已跑 → 不 warn
    sd = _mk_pinned(tmp_path, "Sell",
                    ensemble={"code": "300857", "median": "Hold", "trigger": "sell_review"})
    assert not _hits(product_shape_lint(sd, "2026-07-21"), "sell_review_missing")
