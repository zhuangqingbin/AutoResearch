"""零 LLM 行为指纹(2026-10-03 A6)。

真实前科:09-22 起评级分布从 UW 居多变成 Hold 居多、卡与卡之间的 EV / R:R 几乎没有方差,
同一周里契约改动、菜单换档、模型换代叠在一起 —— 没有任何监控会对「分布变了」或「方差消失」
报警。指纹只读已在盘的产物,对最近的常态给出偏离标签,不拥有门。
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from autoresearch.scan import behavior_fingerprint as bf, run_drift

POLICY = {"window": 10, "min_runs": 3, "share_delta": 0.25, "ratio_warn": 1.5}


def _staging(root: Path, *, ratings: dict, early=None, stances=None, ev=None, rr=None,
             card_bytes=None, intel_urls=None) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    (root / "_final_ratings.json").write_text(json.dumps(ratings), encoding="utf-8")
    if early is not None:
        (root / "_early_stop.json").write_text(json.dumps(early), encoding="utf-8")
    if stances is not None:
        candidates = []
        for code, stance in stances.items():
            ctx = {"entry_stance": stance, "parse_status": "OK",
                   "ev_target": (ev or {}).get(code), "rr": (rr or {}).get(code),
                   "card_kind": "earlystop" if code in (early or {}) else "full"}
            candidates.append({"code": code, "pinned": False, "card_context": ctx})
        (root / "_relative_buy_decision.json").write_text(
            json.dumps({"candidates": candidates}), encoding="utf-8")
    if card_bytes is not None:
        (root / "details").mkdir(exist_ok=True)
        for code, size in card_bytes.items():
            (root / "details" / f"{code}.md").write_text("x" * size, encoding="utf-8")
    if intel_urls is not None:
        for code, n in intel_urls.items():
            body = "\n".join(f"- 来源 https://example.com/{code}/{i}" for i in range(n))
            (root / f"_l4_intel_{code}.md").write_text(body, encoding="utf-8")
    return root


CODES = [f"60000{i}" for i in range(8)]


def _typical(root: Path, *, hold=2, uw=6, card=5000, ev_spread=True) -> Path:
    ratings = {c: ("Hold" if i < hold else "Underweight" if i < hold + uw else "Overweight")
               for i, c in enumerate(CODES)}
    early = {c: {"phase": "P3", "reason": "基本面恶化"} for c in CODES[:4]}
    stances = {c: ("PROHIBITED" if i % 2 else "CONDITIONAL") for i, c in enumerate(CODES)}
    ev = {c: (-0.5 + 0.2 * i if ev_spread else -0.2) for i, c in enumerate(CODES[4:])}
    rr = {c: (0.5 + 0.1 * i if ev_spread else 0.8) for i, c in enumerate(CODES[4:])}
    return _staging(root, ratings=ratings, early=early, stances=stances, ev=ev, rr=rr,
                    card_bytes=dict.fromkeys(CODES, card), intel_urls=dict.fromkeys(CODES, 6))


def test_fingerprint_reads_shares_and_dispersion_from_existing_artifacts(tmp_path):
    fp = bf.fingerprint(_typical(tmp_path / "s"))

    assert fp["measured"] is True and fp["n_cards"] == 8
    assert fp["shares"]["rating_hold"] == pytest.approx(0.25)
    assert fp["shares"]["rating_uw_minus"] == pytest.approx(0.75)
    assert fp["shares"]["rating_ow_plus"] == pytest.approx(0.0)
    assert fp["shares"]["early_stop"] == pytest.approx(0.5)
    assert fp["shares"]["entry_prohibited"] == pytest.approx(0.5)
    assert fp["stop_reasons"] == {"基本面恶化": 4}
    assert fp["scalars"]["card_bytes_median"] == 5000
    assert fp["scalars"]["intel_urls_median"] == 6
    assert fp["scalars"]["ev_std"] > 0 and fp["scalars"]["rr_std"] > 0


@pytest.mark.parametrize("text,value", [
    ("315.4(−0.3%)·带 310–322", -0.3),
    ("113.7(-0.3%);带 111.2–116.4", -0.3),
    ("302–308(−0.8%~+1.2%,EV ≈ −0.1%)", -0.1),
    ("117.5~119.5(EV ≈ 118.2,−0.35%)", -0.35),
    ("119.7(EV -0.3%;带 118.1–121.4)", -0.3),
    ("≈292.1(+0.05%;带 287.6–296.4)", 0.05),
    ("302–308(−0.8%~+1.2%)", None),                    # 区间、没标 EV:不猜
    ("—", None),
    (None, None),
])
def test_ev_is_read_from_the_real_dashboard_cell_formats(text, value):
    """复审 M-1:card_context 里的 EV 是散文格,此前 float() 一律失败 → 常数化探测从来不响。"""
    got = bf._ev_pct(text)
    assert got == (pytest.approx(value) if value is not None else None)


@pytest.mark.parametrize("text,value", [("0.8/1", 0.8), ("0.69/1", 0.69), ("1.0", 1.0),
                                        ("0.75", 0.75), ("1:2", None), ("0.8/0", None), (None, None)])
def test_rr_is_read_from_the_real_dashboard_cell_formats(text, value):
    got = bf._rr(text)
    assert got == (pytest.approx(value) if value is not None else None)


def test_missing_inputs_are_none_not_zero(tmp_path):
    fp = bf.fingerprint(_staging(tmp_path / "s", ratings={"600000": "Hold"}))
    assert fp["shares"]["rating_hold"] == 1.0
    assert fp["shares"]["entry_prohibited"] is None          # 没有决策文件:没看过 ≠ 零
    assert fp["shares"]["early_stop"] is None                # 没有早停文件
    assert fp["scalars"]["intel_urls_median"] is None
    assert fp["scalars"]["ev_std"] is None


def test_a_run_without_cards_is_unmeasured(tmp_path):
    fp = bf.fingerprint(_staging(tmp_path / "s", ratings={}))
    assert fp["measured"] is False


def _hist(tmp_path, n, **kw) -> list[dict]:
    return [{"run_id": f"r{i}", "real_scan": True,
             "fingerprint": bf.fingerprint(_typical(tmp_path / f"h{i}", **kw))} for i in range(n)]


def test_compare_needs_a_baseline_first(tmp_path):
    cur = bf.fingerprint(_typical(tmp_path / "c"))
    assert bf.compare(cur, _hist(tmp_path, 2), POLICY)["status"] == "NO_BASELINE"


def test_the_same_shape_is_normal(tmp_path):
    cur = bf.fingerprint(_typical(tmp_path / "c"))
    got = bf.compare(cur, _hist(tmp_path, 5), POLICY)
    assert got["status"] == "NORMAL" and got["deviations"] == []


def test_the_real_20260922_shape_is_a_rating_mix_deviation(tmp_path):
    """UW 居多 → Hold 居多:持有占比 0.25 → 0.75,超过 share_delta。"""
    cur = bf.fingerprint(_typical(tmp_path / "c", hold=6, uw=2))
    got = bf.compare(cur, _hist(tmp_path, 5), POLICY)
    assert got["status"] == "DEVIATION"
    names = {d["metric"] for d in got["deviations"]}
    assert {"rating_hold", "rating_uw_minus"} <= names


def test_dispersion_collapse_is_a_deviation(tmp_path):
    """卡与卡之间的 EV / R:R 方差消失 = 输出常数化(§3.6),ratio ≤ 1/ratio_warn 即报。"""
    cur = bf.fingerprint(_typical(tmp_path / "c", ev_spread=False))
    got = bf.compare(cur, _hist(tmp_path, 5), POLICY)
    names = {d["metric"] for d in got["deviations"]}
    assert {"ev_std", "rr_std"} <= names
    collapse = next(d for d in got["deviations"] if d["metric"] == "ev_std")
    assert collapse["kind"] == "ratio" and collapse["value"] == 0


def test_card_length_jump_is_a_ratio_deviation(tmp_path):
    cur = bf.fingerprint(_typical(tmp_path / "c", card=9000))
    got = bf.compare(cur, _hist(tmp_path, 5), POLICY)
    assert [d["metric"] for d in got["deviations"]] == ["card_bytes_median"]


def test_unmeasured_current_run_claims_nothing(tmp_path):
    cur = bf.fingerprint(_staging(tmp_path / "c", ratings={}))
    assert bf.compare(cur, _hist(tmp_path, 5), POLICY)["status"] == "UNMEASURED"


def test_policy_defaults_come_from_scan_config():
    from autoresearch.scan.user_config import _PRODUCTION_DEFAULT_PATH, load_user_config

    assert bf.policy(load_user_config(_PRODUCTION_DEFAULT_PATH)) == POLICY


def test_fragment_and_detail_lines(tmp_path):
    cur = bf.fingerprint(_typical(tmp_path / "c", hold=6, uw=2))
    behavior = bf.compare(cur, _hist(tmp_path, 5), POLICY)
    obs = {"fingerprint": cur, "behavior": behavior}
    assert bf.summary_fragment(obs).startswith("行为:DEVIATION(")
    assert "持有占比" in bf.summary_fragment(obs)
    assert any(line.startswith("  - 持有占比:0.75 vs 中位 0.25") for line in bf.detail_lines(obs))
    assert bf.summary_fragment({}) == "行为:—"


def test_published_history_is_backfilled_from_the_staging_mirror(tmp_path):
    """上线第一场就要有基线:老 run 没有 fingerprint 块,从它自己的 trace/staging 现算。"""
    run = tmp_path / "20260917-0917_2152" / "trace"
    _typical(run / "staging")
    (run / "_budget_observation.json").write_text(json.dumps(
        {"run_id": "r917", "analysis_date": "2026-09-17", "real_scan": True}), encoding="utf-8")

    rows, _ = run_drift.load_history(tmp_path)

    assert rows[0]["fingerprint"]["n_cards"] == 8
