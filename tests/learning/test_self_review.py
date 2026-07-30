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


# ══ Wave7 批 N:intel 时效三窗机检 ═══════════════════════════════════════════
#
# 下游是超短 T+2 主尺(D 收盘信号 → D+1 开盘买 → D+2 收盘卖),能改变明天开盘定价的
# 只有「今天收盘后到开工这段」的新信息。07-27 实测有情报把「已消化超 1 周」的预告仍打 +1。

_NEW_HEAD = ("# 活体情报 — 000001 甲 @ 2026-07-27\n\n"
             "## 事件段(≤10 行)\n"
             "| 日期 | 时效窗 | 事件 | 源 | 净分 |\n|---|---|---|---|---|\n")
_DECL = "\n## 声明行\n网查 9 条 ｜ T0面=有增量 ｜ 六面覆盖:公告=有料 ｜ as-of ≤ 2026-07-27\n"


def _intel(tmp_path, body: str, decl: str = _DECL, code: str = "000001"):
    (tmp_path / f"_l4_intel_{code}.md").write_text(_NEW_HEAD + body + decl, encoding="utf-8")
    return tmp_path


def test_recency_clean_sheet_has_no_findings(tmp_path):
    d = _intel(tmp_path,
               "| 2026-07-27 | T0 | 盘后公告中标 3.2 亿 | http://x | +2 |\n"
               "| 2026-07-26 | 24h | 行业提价 | http://y | +1 |\n"
               "| 2026-07-23 | 背景 | 调研纪要 | http://z | +0.5 |\n"
               "| 2026-07-10 | 催化挂 | 8-25 披露中报 | http://w | +2 |\n")
    assert self_review.intel_recency_lint(d, "2026-07-27") == []


def test_recency_flags_window_date_mismatch(tmp_path):
    """把 5 天前的事件标成 T0 —— 时效窗是给下游读的口径,标错等于谎报新鲜度。"""
    d = _intel(tmp_path, "| 2026-07-22 | T0 | 五天前的旧闻 | http://x | +1 |\n")
    got = self_review.intel_recency_lint(d, "2026-07-27")
    assert [g["check"] for g in got] == ["intel_window_mismatch"]
    assert "实距 5d" in got[0]["detail"]


def test_recency_flags_stale_event_still_scored(tmp_path):
    """>1 周的事件净分必须衰减到 0(除非指向将来时点的 催化挂)。"""
    d = _intel(tmp_path, "| 2026-07-15 | 背景 | 12 天前中标 | http://x | +2 |\n")
    got = self_review.intel_recency_lint(d, "2026-07-27")
    checks = [g["check"] for g in got]
    assert "intel_stale_score" in checks


def test_recency_allows_stale_dated_future_catalyst(tmp_path):
    """一周前的**报道**说下月披露中报 —— 兑现窗口在将来,催化挂不衰减,不该报。"""
    d = _intel(tmp_path, "| 2026-07-10 | 催化挂 | 8-25 披露中报(预告 +711%) | http://x | +2 |\n")
    assert self_review.intel_recency_lint(d, "2026-07-27") == []


def test_recency_flags_missing_t0_declaration(tmp_path):
    """写「盘后无增量」合法,留空违规 —— 缺字段分不清「查了没料」与「根本没查」。"""
    d = _intel(tmp_path, "| 2026-07-27 | T0 | 盘后公告 | http://x | +1 |\n",
               decl="\n## 声明行\n网查 9 条 ｜ 六面覆盖:公告=有料 ｜ as-of ≤ 2026-07-27\n")
    assert [g["check"] for g in self_review.intel_recency_lint(d, "2026-07-27")] == ["intel_t0_missing"]


def test_recency_accepts_no_increment_as_valid_t0_answer(tmp_path):
    d = _intel(tmp_path, "| 2026-07-26 | 24h | 行业提价 | http://y | +1 |\n",
               decl="\n## 声明行\n网查 9 条 ｜ T0面=盘后无增量 ｜ as-of ≤ 2026-07-27\n")
    assert self_review.intel_recency_lint(d, "2026-07-27") == []


def test_recency_skips_pre_wave7_sheets_silently(tmp_path):
    """旧契约稿(表头「2日内可发酵?」,无时效窗列)整份跳过 —— 新探针不对着历史存量稿刷屏
    (那正是 07-27 十五连报的同一种病:检查跑在指令前面)。"""
    (tmp_path / "_l4_intel_000002.md").write_text(
        "# 活体情报 — 000002 乙 @ 2026-07-27\n\n## 事件段(≤10 行)\n"
        "| 日期 | 事件 | 源 | 2日内可发酵? | 净分 |\n|---|---|---|---|---|\n"
        "| 2026-06-01 | 两月前中标 | http://x | 否 | +2 |\n"
        "\n## 声明行\n网查 9 条 ｜ 六面覆盖:公告=有料\n", encoding="utf-8")
    assert self_review.intel_recency_lint(tmp_path, "2026-07-27") == []


def test_recency_no_intel_files_is_presence_gated(tmp_path):
    assert self_review.intel_recency_lint(tmp_path, "2026-07-27") == []


def test_recency_does_not_double_report_future_dates(tmp_path):
    """前视由 intel_future_dates_lint 管,本探针不重复报。"""
    d = _intel(tmp_path, "| 2026-08-30 | T0 | 未来日期 | http://x | +1 |\n")
    assert [g["check"] for g in self_review.intel_recency_lint(d, "2026-07-27")] == []


# ══ 复核回归修复(W9-B2-fix):TRIMMED 原地覆写真删行 → lint 靠留档补看全稿 ═══════
#
# guard_intel 的 TRIMMED 分支会把裁前全文单独留档到 `<code>.pretrim`(不带 `.md`)。
# 两条 lint 应改为:留档存在时优先读它,而不是已经被砍掉部分行的 canonical 文件。


def test_recency_reads_pretrim_archive_when_present(tmp_path):
    """canonical 单独看是干净的(control,证明"有 archive 才报"不是巧合);
    留档里补回一条 12 天前未衰减净分的违规行(trim_by_recency 最先砍的对象类型)后,
    lint 必须仍然报出 intel_stale_score —— 证明它读的是留档,不是裁剪后的 canonical。
    """
    code = "000001"
    canonical = (_NEW_HEAD +
                 "| 2026-07-27 | T0 | 盘后公告中标 3.2 亿 | http://x | +2 |\n" + _DECL)
    (tmp_path / f"_l4_intel_{code}.md").write_text(canonical, encoding="utf-8")
    assert self_review.intel_recency_lint(tmp_path, "2026-07-27") == []

    archived_full = (_NEW_HEAD +
                      "| 2026-07-27 | T0 | 盘后公告中标 3.2 亿 | http://x | +2 |\n"
                      "| 2026-07-15 | 背景 | 12 天前中标未衰减(已被裁掉) | http://y | +2 |\n"
                      + _DECL)
    (tmp_path / f"_l4_intel_{code}.pretrim").write_text(archived_full, encoding="utf-8")
    got = self_review.intel_recency_lint(tmp_path, "2026-07-27")
    checks = [g["check"] for g in got]
    assert "intel_stale_score" in checks
    assert all(g["code"] == code for g in got if g["check"] == "intel_stale_score")


def test_recency_lint_survives_real_guard_intel_trim(tmp_path):
    """端到端"钉子"测试:不手工摆 archive,让真实 `guard_intel` 完成裁剪。

    构造一份 11 行事件表的超顶稿(claimed=33>hard_cap=30,keep=10):1 条 T0 + 9 条
    干净背景填充行(时效窗/净分都合规,不触发任何 warn)+ 1 条"12 天前背景事件净分
    未衰减"违规行(排最后,时效窗排序里以 rank/原始序号双 tie-break 必被裁掉)。
    真调用 guard_intel 完成裁剪 → 断言违规行确实从 canonical 消失、`dropped_rows==1`
    → intel_recency_lint 扫同一目录,断言仍能抓到 intel_stale_score(证明留档机制在
    真实管线里生效,不是只在手工摆拍的单测里生效)。末尾负对照:删掉留档后同一探针
    真的看不见这条违规了——证明上面的命中确实依赖留档存在,不是巧合。
    """
    from autoresearch.scan.l4.intel_guard import guard_intel

    code = "000009"
    draft = (
        f"# 活体情报 — {code} @ 2026-07-27\n\n"
        "## 事件段\n"
        "| 日期 | 时效窗 | 事件 | 源 | 净分 |\n|---|---|---|---|---|\n"
        "| 2026-07-27 | T0 | 盘后公告中标 3.2 亿 | http://x | +2 |\n"
        "| 2026-07-26 | 背景 | 填充背景事件1 | http://f1 | 0.0 |\n"
        "| 2026-07-25 | 背景 | 填充背景事件2 | http://f2 | 0.0 |\n"
        "| 2026-07-24 | 背景 | 填充背景事件3 | http://f3 | 0.0 |\n"
        "| 2026-07-23 | 背景 | 填充背景事件4 | http://f4 | 0.0 |\n"
        "| 2026-07-22 | 背景 | 填充背景事件5 | http://f5 | 0.0 |\n"
        "| 2026-07-21 | 背景 | 填充背景事件6 | http://f6 | 0.0 |\n"
        "| 2026-07-20 | 背景 | 填充背景事件7 | http://f7 | 0.0 |\n"
        "| 2026-07-26 | 背景 | 填充背景事件8 | http://f8 | 0.0 |\n"
        "| 2026-07-25 | 背景 | 填充背景事件9 | http://f9 | 0.0 |\n"
        "| 2026-07-15 | 背景 | 12天前中标未衰减(违规,应被裁掉) | http://stale | +2 |\n"
        "\n## 声明行\n网查 33 条 ｜ T0面=有增量\n"
    )
    (tmp_path / f"_l4_intel_{code}.md").write_text(draft, encoding="utf-8")

    r = guard_intel(tmp_path, code, hard_cap=30)
    assert r["action"] == "TRIMMED" and r["dropped_rows"] == 1
    archive = tmp_path / f"_l4_intel_{code}.pretrim"
    assert archive.exists()

    canonical_after = (tmp_path / f"_l4_intel_{code}.md").read_text(encoding="utf-8")
    assert "12天前中标未衰减" not in canonical_after       # 违规行确实从 canonical 消失

    got = self_review.intel_recency_lint(tmp_path, "2026-07-27")
    assert any(g["check"] == "intel_stale_score" and g["code"] == code for g in got), (
        "留档存在时,intel_recency_lint 必须仍能抓到已被真实 guard_intel 裁掉的净分未衰减行")

    # 负对照:去掉留档,同一违规行从探针视野消失 —— 证明上面的命中真依赖留档存在。
    archive.unlink()
    got2 = self_review.intel_recency_lint(tmp_path, "2026-07-27")
    assert not any(g["check"] == "intel_stale_score" and g["code"] == code for g in got2)


def test_future_dates_reads_pretrim_archive_when_present(tmp_path):
    """同上,针对 intel_future_dates_lint —— 留档里补回一条被裁掉的前视穿越行。"""
    code = "000003"
    canonical = ("# 活体情报 — 000003 @ 2026-07-09\n## 事件段(≤10 行)\n"
                 "| 2026-07-05 | 历史事件 | x | 否 | 0 |\n## 题材段\n无\n")
    (tmp_path / f"_l4_intel_{code}.md").write_text(canonical, encoding="utf-8")
    assert self_review.intel_future_dates_lint(tmp_path, "2026-07-09") == []

    archived_full = ("# 活体情报 — 000003 @ 2026-07-09\n## 事件段(≤10 行)\n"
                      "| 2026-07-05 | 历史事件 | x | 否 | 0 |\n"
                      "| 2026-07-20 | 被裁掉的未来穿越行 | x | 是 | +1 |\n## 题材段\n无\n")
    (tmp_path / f"_l4_intel_{code}.pretrim").write_text(archived_full, encoding="utf-8")
    out = self_review.intel_future_dates_lint(tmp_path, "2026-07-09")
    assert any(c["check"] == "intel_future_dates" and c["code"] == code for c in out)
