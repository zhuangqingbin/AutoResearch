from pathlib import Path

from autoresearch.scan import assemble


def _mk_staging(tmp_path: Path) -> Path:
    sd = tmp_path / "2026-07-21"
    (sd / "details").mkdir(parents=True)
    (sd / "details" / "300857.md").write_text(
        "# 决策卡 — 300857 协创数据 @ 2026-07-21\n协创数据 07-21 大涨 11.4%。\n"
        "**Rating**: Underweight\nFINAL TRANSACTION PROPOSAL: **SELL**\n", encoding="utf-8")
    (sd / "finalists.csv").write_text("code,name,sector,lane\n300857,协创数据,消费电子,pinned\n",
                                      encoding="utf-8")
    return sd


def test_publish_details_appends_reconcile_tail(tmp_path, monkeypatch):
    sd = _mk_staging(tmp_path)
    out = tmp_path / "pub"
    out.mkdir()
    # 注入假 bars:实涨 1.0% → 断言 11.4% 不符
    monkeypatch.setattr("autoresearch.scan.price_claims.bars_for",
                        lambda c, ds, today: {"20260721": 1.0})
    n = assemble._publish_details(sd, out)
    assert n == 1
    body = (out / "协创数据.md").read_text(encoding="utf-8")
    assert "价格断言对账" in body and "11.4" in body and "1.0" in body
    # staging 卡不动
    assert "价格断言对账" not in (sd / "details" / "300857.md").read_text(encoding="utf-8")


def test_publish_details_all_clear_line(tmp_path, monkeypatch):
    sd = _mk_staging(tmp_path)
    out = tmp_path / "pub"
    out.mkdir()
    monkeypatch.setattr("autoresearch.scan.price_claims.bars_for",
                        lambda c, ds, today: {"20260721": 11.4})
    assemble._publish_details(sd, out)
    body = (out / "协创数据.md").read_text(encoding="utf-8")
    assert "价格断言对账" in body and "0 条不符" in body


def test_publish_details_survives_bars_crash(tmp_path, monkeypatch):
    sd = _mk_staging(tmp_path)
    out = tmp_path / "pub"
    out.mkdir()
    def boom(c, ds, today):
        raise RuntimeError("lake down")
    monkeypatch.setattr("autoresearch.scan.price_claims.bars_for", boom)
    assert assemble._publish_details(sd, out) == 1     # 不炸,卡照发(对账段缺席)


def test_ensemble_fold_ow_only_down():
    assert assemble._apply_ensemble_fold("Overweight", {"median": "Hold"}) == "Hold"
    assert assemble._apply_ensemble_fold("Hold", {"median": "Overweight"}) == "Hold"


def test_ensemble_fold_sell_review_only_milder():
    rec = {"median": "Hold", "trigger": "sell_review"}
    assert assemble._apply_ensemble_fold("Sell", rec) == "Hold"          # 复核救回误卖
    rec2 = {"median": "Sell", "trigger": "sell_review"}
    assert assemble._apply_ensemble_fold("Underweight", rec2) == "Underweight"  # 不向更狠折


def test_ensemble_fold_degraded_noop():
    rec = {"median": "Hold", "trigger": "sell_review", "degraded": True}
    assert assemble._apply_ensemble_fold("Sell", rec) == "Sell"
