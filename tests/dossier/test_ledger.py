"""ledger.py 契约:t1 逐笔按票聚合 + retro 桶 + §7/摘要渲染(Wave3 Task 2)。

口径与 t1_review.render_ledger_report 对齐:行业超额优先/sealed 不计可实现/
Hold(verdict「—」)不算方向票/UW·Sell 顺方向 = 负超额为赢(sign=-1)。
"""
import json

from autoresearch.dossier import ledger


def _write_ledger(tmp_path, rows):
    p = tmp_path / "t1_review.jsonl"
    p.write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in rows) + "\n",
                 encoding="utf-8")
    return p


def test_code_track_record_direction_and_sign(tmp_path):
    p = _write_ledger(tmp_path, [
        {"t": "2026-07-14", "code": "300857", "rating": "Underweight",
         "verdict": "准", "excess_ind": -0.03, "sealed": False},
        {"t": "2026-07-15", "code": "300857", "rating": "Underweight",
         "verdict": "不准", "excess_ind": 0.01, "sealed": False},
        {"t": "2026-07-16", "code": "300857", "rating": "Sell",
         "verdict": "准", "excess_ind": -0.02, "sealed": True},   # sealed:计方向不计 pnl
        {"t": "2026-07-16", "code": "300857", "rating": "Hold",
         "verdict": "—", "excess_ind": 0.005, "sealed": False},   # Hold 无方向,不计
        {"t": "2026-07-16", "code": "999999", "rating": "Sell",
         "verdict": "准", "excess_ind": -0.09, "sealed": False},  # 别的票,不计
    ])
    rec = ledger.code_track_record("300857", ledger_path=p)
    assert (rec["n_dir"], rec["right"], rec["wrong"], rec["neutral"]) == (3, 2, 1, 0)
    # pnl 只有前两笔:UW sign=-1 → (+0.03) 与 (-0.01) → 均值 +1.0pp
    assert abs(rec["avg_pp"] - 1.0) < 1e-9


def test_code_track_record_missing_ledger(tmp_path):
    rec = ledger.code_track_record("300857", ledger_path=tmp_path / "nope.jsonl")
    assert rec == {"n_dir": 0, "right": 0, "wrong": 0, "neutral": 0, "avg_pp": None}


def test_retro_buckets(tmp_path):
    for d, bucket in (("2026-07-14", "recalled_cut"), ("2026-07-15", "caught"),
                      ("2026-07-16", "")):
        rd = tmp_path / d / "retro"
        rd.mkdir(parents=True)
        (rd / "attribution.csv").write_text(
            f"code,bucket\n300857,{bucket}\n", encoding="utf-8")
    out = ledger.retro_buckets("300857", scan_root=tmp_path)
    assert out == {"recalled_cut": 1, "caught": 1}       # 空桶不计


def test_render_precedent_value_presence_gated():
    base = ledger.render_precedent_value(5, {"n_dir": 0})
    assert base == "近 10 扫描日入围 5 次"                 # 无战绩 = 现行文本逐字不变(parity)
    up = ledger.render_precedent_value(
        5, {"n_dir": 3, "right": 2, "wrong": 1, "neutral": 0, "avg_pp": 1.0})
    assert up.startswith("近 10 扫描日入围 5 次;t1 方向 3 笔 准2/不准1")
    assert "+1.0pp" in up


def test_render_track_block_empty_when_no_data(tmp_path):
    assert ledger.render_track_block("300857", scan_root=tmp_path,
                                     ledger_path=tmp_path / "nope.jsonl") == ""
