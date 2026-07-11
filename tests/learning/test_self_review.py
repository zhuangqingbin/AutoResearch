from autoresearch.learning import self_review


def test_selftest():
    assert self_review._selftest() == 0


def test_failures_carry_code_and_dump_gate_fires(tmp_path):
    """R3·门审计地基:failure 带结构化 code;dump_gate_fires 幂等落 csv(无 fail 也写表头)。"""
    import csv

    import autoresearch.learning.self_review as sr
    res = sr.review({"finalists": [{"code": "300001", "rating": "Buy", "winner_rate": 95}],
                     "n_cards_expected": 1, "n_cards_present": 1, "summary_text": ""})
    hit = [f for f in res["failures"] if f["check"].startswith("经验红线")]
    assert hit and hit[0]["code"] == "300001"
    p = sr.dump_gate_fires(tmp_path, res, "2026-07-02")
    rows = list(csv.DictReader(p.open(encoding="utf-8")))
    assert any(r["code"] == "300001" and r["severity"] == "fail" for r in rows)
    # 无 failures → 只有表头(区分"没拦"与"没跑")
    p2 = sr.dump_gate_fires(tmp_path, {"failures": []}, "2026-07-02")
    assert list(csv.DictReader(p2.open(encoding="utf-8"))) == []


def test_flow_lint_buy_without_skeptic_and_missing_strategist():
    """流程完备性:买单 skeptic 已移除(2026-07-06 用户决定)→ 买单无 verify 不再 fail;
    finalists≥5 无 market_view=warn;小 fixture 不误报。"""
    r = self_review.review({
        "finalists": [{"code": "1", "rating": "Overweight", "composite": 60,
                       "winner_rate": 40, "sector": "电子"}],
        "n_cards_expected": 1, "n_cards_present": 1,
        "flow": {"buys_n": 1, "verify_n": 0, "has_market_view": True, "finalists_n": 1}})
    assert r["ok"] and not any("买单未过skeptic" in x["check"] for x in r["failures"])
    r2 = self_review.review({
        "finalists": [], "n_cards_expected": 0, "n_cards_present": 0,
        "flow": {"buys_n": 0, "verify_n": 0, "has_market_view": False, "finalists_n": 8}})
    assert any("策略师未跑" in x["check"] for x in r2["failures"]) and r2["ok"]   # warn 非 fail
    r3 = self_review.review({
        "finalists": [], "n_cards_expected": 0, "n_cards_present": 0,
        "flow": {"buys_n": 0, "verify_n": 0, "has_market_view": False, "finalists_n": 1}})
    assert not any("策略师" in x["check"] for x in r3["failures"])                # 小 fixture 不误报


# ───────────────────────── intel as-of 前视机检(l4-intel station Task 6) ─────────────────────────


def test_intel_future_dates_warn(tmp_path):
    """`_l4_intel_*.md` 事件段表格行首日期晚于扫描日 → intel_future_dates warn(advisory,不挡发布)。"""
    (tmp_path / "_l4_intel_000001.md").write_text(
        "# 活体情报 — 000001 @ 2026-07-09\n## 事件段(≤10 行)\n"
        "| 2026-07-20 | 未来事件 | x | 是 | +1 |\n## 题材段\n无\n", encoding="utf-8")
    out = self_review.intel_future_dates_lint(tmp_path, "2026-07-09")
    assert any(c["check"] == "intel_future_dates" and c["severity"] == "warn" for c in out)


def test_intel_future_dates_ignores_body_prose_and_past_rows(tmp_path):
    """只查事件段**表格行首**日期列——正文里提到的未来催化时点合法;表格行日期未晚于扫描日不触发。"""
    (tmp_path / "_l4_intel_000002.md").write_text(
        "# 活体情报 — 000002 @ 2026-07-09\n## 事件段(≤10 行)\n"
        "| 2026-07-05 | 历史事件 | x | 否 | 0 |\n"
        "预计 2026-08-15 发布中报,是后续核心催化。\n## 题材段\n无\n", encoding="utf-8")
    out = self_review.intel_future_dates_lint(tmp_path, "2026-07-09")
    assert not any(c["check"] == "intel_future_dates" for c in out)


def test_intel_future_dates_no_files_returns_empty(tmp_path):
    """无 `_l4_intel_*.md`(config 未启用/未派发)→ 空列表,不报错。"""
    assert self_review.intel_future_dates_lint(tmp_path, "2026-07-09") == []


def test_intel_future_dates_wired_into_banner(tmp_path):
    """接线回归:`_self_review_banner` 真把 intel 前视 warn 合入(不是只有独立函数存在而未接生产)。

    `_self_review_banner` 按 `scan_dir.name` 取扫描日(同 dump_gate_fires 惯例)——scan_dir
    须真名为日期字符串,否则 intel_future_dates_lint 内的字典序日期比较会比错对象。
    """
    from autoresearch.scan.assemble import _self_review_banner
    scan_dir = tmp_path / "2026-07-09"
    scan_dir.mkdir()
    (scan_dir / "_l4_intel_000001.md").write_text(
        "# 活体情报 — 000001 @ 2026-07-09\n## 事件段(≤10 行)\n"
        "| 2026-07-20 | 未来事件 | x | 是 | +1 |\n## 题材段\n无\n", encoding="utf-8")
    banner = _self_review_banner(scan_dir, [], "")
    assert "intel_future_dates" in banner
