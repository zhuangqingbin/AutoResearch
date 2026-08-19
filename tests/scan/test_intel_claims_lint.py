"""D13/A9:intel 他票涨停/连板断言确定性对账(用户裁决,2026-08-19)。

情报编造已复发 3 次(07-14 涨停捏造 P0 事故/07-16 日期焊接/08-13 五条断言三条编造),
此前每次都靠事后人工发现。`lint_claims` 补一道零 LLM 的确定性 lint:他票涨停/连板
断言缺 URL,或带 URL 但与湖收盘数据不符 → 行尾标〔未核〕。红线:只标注、不拒稿——
`lint_claims` 是纯函数,天然不 raise/exit,本文件不需要另测退出码。
"""

from __future__ import annotations

from autoresearch.scan.l4.intel_guard import guard_intel, lint_claims

# ── lint_claims 本体:四条核心用例(缺URL / 与湖不符 / 吻合不标 / 本票排除)──────


def test_missing_url_gets_flagged():
    text = "002230 今日涨停,资金抢筹明显。\n"
    out, meta = lint_claims(text, self_code="600519", trade_date="20260819")
    assert out == "002230 今日涨停,资金抢筹明显。〔未核·缺URL〕\n"
    assert meta == {"no_url": 1, "mismatch": 0}


def test_url_but_lake_disagrees_gets_flagged():
    text = "002230 今日涨停 http://example.com/news/1\n"
    out, meta = lint_claims(text, self_code="600519", trade_date="20260819",
                             pct_lookup=lambda code, date: 2.0)
    assert out == "002230 今日涨停 http://example.com/news/1〔未核·与湖不符〕\n"
    assert meta == {"no_url": 0, "mismatch": 1}


def test_url_and_lake_agrees_untouched():
    """带 URL 且湖吻合(>=9)→ 原文一字不改,两个计数都是 0。"""
    text = "002230 今日涨停 http://example.com/news/1\n"
    out, meta = lint_claims(text, self_code="600519", trade_date="20260819",
                             pct_lookup=lambda code, date: 10.02)
    assert out == text
    assert meta == {"no_url": 0, "mismatch": 0}


def test_self_code_claim_not_flagged():
    """本票自己的涨停断言不归本函数管(排除条件用 self_code)。"""
    text = "600519 今日涨停,一字板。\n"
    out, meta = lint_claims(text, self_code="600519", trade_date="20260819",
                             pct_lookup=lambda code, date: 2.0)
    assert out == text
    assert meta == {"no_url": 0, "mismatch": 0}


# ── 补充边界:无从对账 / 非断言行 / 多他票 ────────────────────────────────────


def test_unqueryable_lake_only_flags_missing_url_not_mismatch():
    """湖查不到(缺分区/缺该票,pct_lookup 返回 None)→ 无从对账;
    有 URL 时不标,无 URL 时仍标缺URL(两条规则相互独立)。"""
    with_url = "002230 今日涨停 http://x\n"
    out, meta = lint_claims(with_url, self_code="600519", trade_date="20260819",
                             pct_lookup=lambda code, date: None)
    assert out == with_url
    assert meta == {"no_url": 0, "mismatch": 0}

    without_url = "002230 今日涨停\n"
    out2, meta2 = lint_claims(without_url, self_code="600519", trade_date="20260819",
                               pct_lookup=lambda code, date: None)
    assert out2 == "002230 今日涨停〔未核·缺URL〕\n"
    assert meta2 == {"no_url": 1, "mismatch": 0}


def test_lint_claims_is_idempotent_on_already_tagged_lines():
    """`l4-stock.js` 对同一份稿跑两趟 guard_intel(intel_guard CLI 直调 + intel_status
    --normalize 内部又调一次)——已标过的行第二趟必须原样透传,不再叠一层〔未核〕。"""
    once = "002230 今日涨停,资金抢筹明显。〔未核·缺URL〕\n"
    out, meta = lint_claims(once, self_code="600519", trade_date="20260819")
    assert out == once, "已标过的行不得被二次标注(幂等)"
    assert meta == {"no_url": 1, "mismatch": 0}

    once_mismatch = "002230 今日涨停 http://x〔未核·与湖不符〕\n"
    out2, meta2 = lint_claims(once_mismatch, self_code="600519", trade_date="20260819",
                               pct_lookup=lambda code, date: 2.0)
    assert out2 == once_mismatch
    assert meta2 == {"no_url": 0, "mismatch": 1}


def test_code_adjacent_to_chinese_text_with_no_separator_is_still_detected():
    """常见中文写法「隆基绿能601012今日涨停」代码与中文直接相连、无分隔符——
    `\\b\\d{6}\\b` 在此会因 Python 3 把中文当作 `\\w` 而漏检(已实测坐实),
    必须用数字邻接负向环视,不依赖 `\\b`。"""
    text = "隆基绿能601012今日涨停,带动板块\n"
    out, meta = lint_claims(text, self_code="600519", trade_date="20260819")
    assert out == "隆基绿能601012今日涨停,带动板块〔未核·缺URL〕\n"
    assert meta == {"no_url": 1, "mismatch": 0}


def test_lines_without_claim_words_untouched():
    text = "002230 今日大涨 8.5%,资金关注度提升。\n"
    out, meta = lint_claims(text, self_code="600519", trade_date="20260819",
                             pct_lookup=lambda code, date: 2.0)
    assert out == text
    assert meta == {"no_url": 0, "mismatch": 0}


def test_multi_ticker_line_flags_if_any_mismatches():
    text = "002230/300451 今日双双涨停 http://x\n"
    out, meta = lint_claims(
        text, self_code="600519", trade_date="20260819",
        pct_lookup=lambda code, date: 10.0 if code == "002230" else 1.0)
    assert out.endswith("〔未核·与湖不符〕\n")
    assert meta == {"no_url": 0, "mismatch": 1}


def test_default_lake_lookup_reads_real_parquet_schema(tmp_path, monkeypatch):
    """不注入 pct_lookup 时,缺省真读 lake/daily/<date>.parquet 的 ts_code/pct_chg
    (经 to_ts_code 归一后对账;缺该票 = 无从对账,不标)。"""
    import pandas as pd

    from autoresearch.scan.l4 import intel_guard as ig

    lake_root = tmp_path / "lake"
    (lake_root / "daily").mkdir(parents=True)
    df = pd.DataFrame({"ts_code": ["002230.SZ", "600519.SH"], "pct_chg": [9.98, 0.12]})
    df.to_parquet(lake_root / "daily" / "20260819.parquet")
    monkeypatch.setattr(ig.ws, "lake_root", lambda: lake_root)

    matched = "002230 今日涨停 http://x\n"
    out, meta = lint_claims(matched, self_code="600519", trade_date="20260819")
    assert out == matched and meta == {"no_url": 0, "mismatch": 0}   # 9.98>=9,吻合不标

    not_in_lake = "300001 今日涨停 http://x\n"
    out2, meta2 = lint_claims(not_in_lake, self_code="600519", trade_date="20260819")
    assert out2 == not_in_lake and meta2 == {"no_url": 0, "mismatch": 0}  # 缺该票,不标


# ── 串接进 guard_intel:KEPT/TRIMMED 落盘路径 + .orig 侧车惯例 ─────────────────


def test_guard_intel_kept_path_annotates_and_archives_orig(tmp_path):
    scan_dir = tmp_path / "2026-08-19"
    scan_dir.mkdir()
    body = ("# 活体情报 — 600519 @ 2026-08-19\n"
            "## 题材段\n002230 今日涨停,题材梯队龙头。\n"
            "## 声明行\n网查 5 条 ｜ as-of ≤ 2026-08-19\n")
    (scan_dir / "_l4_intel_600519.md").write_text(body, encoding="utf-8")

    out = guard_intel(scan_dir, "600519", hard_cap=30)

    assert out["action"] == "KEPT"
    assert out["claims_lint"] == {"no_url": 1, "mismatch": 0,
                                   "orig_as": "_l4_intel_600519.orig"}
    kept = (scan_dir / "_l4_intel_600519.md").read_text(encoding="utf-8")
    assert "〔未核·缺URL〕" in kept
    orig = scan_dir / "_l4_intel_600519.orig"
    assert orig.exists()
    assert orig.read_text(encoding="utf-8") == body, "侧车必须逐字节保留标注前原文"
    assert "〔未核" not in orig.read_text(encoding="utf-8")


def test_guard_intel_no_orig_archive_when_nothing_flagged(tmp_path):
    scan_dir = tmp_path / "2026-08-19"
    scan_dir.mkdir()
    body = ("# 活体情报 — 600519 @ 2026-08-19\n"
            "## 题材段\n无题材归属。\n"
            "## 声明行\n网查 5 条 ｜ as-of ≤ 2026-08-19\n")
    (scan_dir / "_l4_intel_600519.md").write_text(body, encoding="utf-8")

    out = guard_intel(scan_dir, "600519", hard_cap=30)

    assert out["claims_lint"] == {"no_url": 0, "mismatch": 0, "orig_as": None}
    assert not (scan_dir / "_l4_intel_600519.orig").exists()
    assert (scan_dir / "_l4_intel_600519.md").read_text(encoding="utf-8") == body


def test_guard_intel_second_invocation_does_not_double_tag(tmp_path):
    """真实生产路径:l4-stock.js 对同一份稿跑两趟 guard_intel(intel_guard CLI 直调 +
    intel_status --normalize 内部又调一次)。第二趟必须是稳定不动点:文件内容、
    `.orig` 侧车都不再变化,`claims_lint` 计数仍准确反映当前标记状态。"""
    scan_dir = tmp_path / "2026-08-19"
    scan_dir.mkdir()
    body = ("# 活体情报 — 600519 @ 2026-08-19\n"
            "## 题材段\n002230 今日涨停,题材梯队龙头。\n"
            "## 声明行\n网查 5 条 ｜ as-of ≤ 2026-08-19\n")
    (scan_dir / "_l4_intel_600519.md").write_text(body, encoding="utf-8")

    out1 = guard_intel(scan_dir, "600519", hard_cap=30)
    after_first = (scan_dir / "_l4_intel_600519.md").read_text(encoding="utf-8")
    orig_after_first = (scan_dir / "_l4_intel_600519.orig").read_text(encoding="utf-8")

    out2 = guard_intel(scan_dir, "600519", hard_cap=30)
    after_second = (scan_dir / "_l4_intel_600519.md").read_text(encoding="utf-8")
    orig_after_second = (scan_dir / "_l4_intel_600519.orig").read_text(encoding="utf-8")

    assert "〔未核·缺URL〕〔未核·缺URL〕" not in after_second, "不得叠标两层"
    assert after_second == after_first, "第二趟是稳定不动点,文件内容不再变化"
    assert orig_after_second == orig_after_first == body, ".orig 必须始终是真原文,不被第二趟污染"
    # orig_as 报的是"这一趟有没有新归档"(同 pretrim_as 的既有语义),不是"归档是否存在"——
    # 第一趟真标注了 → 有归档;第二趟落盘内容已不变(幂等)→ 没有新归档,orig_as=None。
    assert out1["claims_lint"] == {"no_url": 1, "mismatch": 0,
                                    "orig_as": "_l4_intel_600519.orig"}
    assert out2["claims_lint"] == {"no_url": 1, "mismatch": 0, "orig_as": None}


def test_orig_archive_invisible_to_intel_md_glob(tmp_path):
    """留档命名故意不带 `.md`(同 `.pretrim` 惯例)——不会被 glob('_l4_intel_*.md') 顺带命中。"""
    scan_dir = tmp_path / "2026-08-19"
    scan_dir.mkdir()
    body = "# 活体情报 — 600519\n002230 今日涨停。\n## 声明行\n网查 5 条\n"
    (scan_dir / "_l4_intel_600519.md").write_text(body, encoding="utf-8")

    guard_intel(scan_dir, "600519", hard_cap=30)

    matches = sorted(p.name for p in scan_dir.glob("_l4_intel_*.md"))
    assert matches == ["_l4_intel_600519.md"], (
        "留档 _l4_intel_600519.orig 不该出现在 _l4_intel_*.md 的匹配结果里")
