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
    assert rec["n_pnl"] == 2               # I-1(M8 补强):分母=刨掉 sealed 的可实现样本
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
    assert "pnl n=" not in up   # m-1:rec 无 n_pnl 时不得渲染出自相矛盾的 "(pnl n=0)"


def test_render_track_block_empty_when_no_data(tmp_path):
    assert ledger.render_track_block("300857", scan_root=tmp_path,
                                     ledger_path=tmp_path / "nope.jsonl") == ""


def test_render_discloses_pnl_sample_and_neutral(tmp_path):
    """M-2/M-3:样本量与中性数都要写出来(注入面读数不得含糊)。"""
    import json

    from autoresearch.dossier import ledger
    p = tmp_path / "t1.jsonl"
    rows = [
        {"t": "2026-07-14", "code": "300857", "rating": "Underweight",
         "verdict": "准", "excess_ind": -0.03, "sealed": False},
        {"t": "2026-07-15", "code": "300857", "rating": "Sell",
         "verdict": "准", "excess_ind": -0.02, "sealed": True},    # 计方向不计 pnl
        {"t": "2026-07-16", "code": "300857", "rating": "Underweight",
         "verdict": "中性", "excess_ind": 0.001, "sealed": False},
    ]
    p.write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in rows) + "\n",
                 encoding="utf-8")
    rec = ledger.code_track_record("300857", ledger_path=p)
    assert rec["n_dir"] == 3
    val = ledger.render_precedent_value(5, rec)
    assert "/中性1" in val and "pnl n=2" in val    # I-1:判例行两项披露都要验数值,不靠字面蒙混
    block = ledger.render_track_block("300857", scan_root=tmp_path / "nope",
                                      ledger_path=p)
    assert "pnl n=2" in block and "/中性1" in block  # I-1:§7 同面同口径(勿分叉),两项都验数值


def test_retro_buckets_reads_only_needed_columns(tmp_path, monkeypatch):
    """M-15:attribution.csv ≈5000×29,只需 code/bucket 两列。"""
    import pandas as pd

    from autoresearch.dossier import ledger
    rd = tmp_path / "2026-07-14" / "retro"
    rd.mkdir(parents=True)
    (rd / "attribution.csv").write_text(
        "code,name,bucket,fwd_2_oc\n300857,协创,recalled_cut,0.01\n", encoding="utf-8")
    seen = {}
    real = pd.read_csv

    def spy(path, **kw):
        seen.update(kw)
        return real(path, **kw)

    monkeypatch.setattr(pd, "read_csv", spy)
    assert ledger.retro_buckets("300857", scan_root=tmp_path) == {"recalled_cut": 1}
    assert set(seen.get("usecols") or []) == {"code", "bucket"}


def test_retro_buckets_missing_bucket_column_degrades_safely(tmp_path):
    """M-15 降级路:老 CSV 缺 bucket 列 → usecols 令 pd.read_csv 抛 ValueError →
    现有 except 跳过该日,不挡其余日子的聚合(安全降级,非整体失败)。"""
    from autoresearch.dossier import ledger
    old = tmp_path / "2026-07-13" / "retro"
    old.mkdir(parents=True)
    (old / "attribution.csv").write_text("code,name\n300857,协创\n", encoding="utf-8")
    new = tmp_path / "2026-07-14" / "retro"
    new.mkdir(parents=True)
    (new / "attribution.csv").write_text("code,bucket\n300857,caught\n", encoding="utf-8")
    assert ledger.retro_buckets("300857", scan_root=tmp_path) == {"caught": 1}
