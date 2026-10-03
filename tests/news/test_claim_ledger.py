"""claim ledger 单测 —— §1.3 D3 五条逐条钉死 + 红线「只拒稿不拒票」。"""
from __future__ import annotations

import pandas as pd
import pytest

from autoresearch.news import catalog as nc, claim_ledger as cl


def _cat(tmp_path, items=()):
    cat = nc.NewsCatalog(tmp_path / "catalog")
    if items:
        cat.ingest(list(items))
    return cat


def _obs(**over):
    base = {"source": "cninfo", "title": "关于回购股份的公告", "url": "https://x/1",
            "published_ts": "2026-08-01 09:00:00",
            "first_seen_ts": "2026-08-01 18:00:00", "available_stage": "L3",
            "event_date": "2026-08-01", "codes": ("000651",)}
    base.update(over)
    return nc.Observation(**base)


# ── 1. 原子 claim + 「引用只是展示格式」 ──────────────────────


def test_claims_are_atomic_not_whole_paragraphs():
    text = "- 公司公告回购 [巨潮|2026-08-01|https://x/1]\n- 股东拟减持 [巨潮|2026-08-02|https://x/2]"
    claims = cl.parse_claims(text, subject="000651")
    assert len(claims) == 2
    assert {c.predicate for c in claims} == {"回购", "减持"}


def test_a_citation_alone_does_not_make_a_claim_verified():
    """`[source|date|url]` 长得像引用,但它只是展示格式。"""
    claims = cl.parse_claims("- 公司回购 [巨潮|2026-08-01|https://x/1]", subject="000651")
    assert claims[0].status == cl.UNVERIFIED
    assert "展示格式" in claims[0].note


def test_unparseable_line_is_unparsed_not_refuted():
    claims = cl.parse_claims("- 一句没有引用的话", subject="000651")
    assert claims[0].status == cl.UNPARSED
    assert "不拒票" in claims[0].note


def test_claim_id_is_stable_and_content_addressed():
    a = cl.claim_id("000651", "回购", "公司回购", "2026-08-01")
    b = cl.claim_id("000651", "回购", "公司回购", "2026-08-01")
    c = cl.claim_id("000651", "回购", "公司回购", "2026-08-02")
    assert a == b != c


def test_effective_date_falls_back_to_the_body():
    claims = cl.parse_claims("- 2026-08-05 公司中标 [新闻||https://x/9]",
                             subject="000651")
    assert claims[0].effective_date == "2026-08-05"


def test_blank_and_heading_lines_are_skipped():
    assert cl.parse_claims("\n# 标题\n\n", subject="000651") == []


# ── 2. lint 分层 ────────────────────────────────────────────────


def test_missing_observation_ids_yields_unverified_not_refuted(tmp_path):
    claim = cl.parse_claims("- 公司回购 [巨潮|2026-08-01|https://x/1]",
                            subject="000651")[0]
    linted = cl.lint_claim(claim, catalog=_cat(tmp_path))
    assert linted.status == cl.UNVERIFIED
    assert any("不能充当真实性校验" in r for r in linted.lint["reasons"])


def test_three_layers_are_recorded_separately(tmp_path):
    claim = cl.parse_claims("- 公司回购 [巨潮|2026-08-01|https://x/1]",
                            subject="000651")[0]
    linted = cl.lint_claim(claim, catalog=_cat(tmp_path))
    assert set(linted.lint["verdicts"]) == set(cl.LINT_LAYERS)


def test_artifact_without_independent_field_check_is_unknown(tmp_path):
    cat = _cat(tmp_path, [_obs()])
    oid = cat.observations().iloc[0]["source_observation_id"]
    claim = cl.parse_claims("- 公司宣布重大重组 [巨潮|2026-08-01|https://x/1]",
                            subject="000651")[0]
    claim.citation_observation_ids = [oid]
    linted = cl.lint_claim(claim, catalog=cat,
                           artifacts={oid: {"text": "关于回购股份的公告全文"}})
    assert linted.lint["verdicts"][cl.CONTENT_SUPPORTS] == cl.UNKNOWN
    assert linted.status == cl.UNVERIFIED
    assert linted.blame == cl.BLAME_UNKNOWN


def test_keyword_and_matching_date_leave_semantic_support_unknown(tmp_path):
    cat = _cat(tmp_path, [_obs()])
    oid = cat.observations().iloc[0]["source_observation_id"]
    claim = cl.parse_claims("- 公司回购股份 [巨潮|2026-08-01|https://x/1]",
                            subject="000651")[0]
    claim.citation_observation_ids = [oid]
    linted = cl.lint_claim(claim, catalog=cat,
                           artifacts={oid: {"text": "关于回购股份的公告全文"}})
    assert linted.status == cl.UNVERIFIED


def test_uncontrolled_predicate_is_unknown_not_refuted(tmp_path):
    """谓语不在受控词表 → 无法从正文判定 → UNKNOWN。不猜,更不判假。"""
    cat = _cat(tmp_path, [_obs()])
    oid = cat.observations().iloc[0]["source_observation_id"]
    claim = cl.parse_claims("- 公司经营状况良好 [巨潮|2026-08-01|https://x/1]",
                            subject="000651")[0]
    claim.citation_observation_ids = [oid]
    linted = cl.lint_claim(claim, catalog=cat,
                           artifacts={oid: {"text": "关于回购股份的公告全文"}})
    assert linted.lint["verdicts"][cl.CONTENT_SUPPORTS] == cl.UNKNOWN
    assert linted.status == cl.UNVERIFIED


def test_naive_substring_matching_would_produce_false_miscitation(tmp_path):
    """「公司回购股份」不会逐字出现在「关于回购股份的公告」里 —— 子串判假是错的。

    这是 price_claims Wave10 A3 那一课的移植:自信的误指控比漏报贵得多。
    """
    cat = _cat(tmp_path, [_obs()])
    oid = cat.observations().iloc[0]["source_observation_id"]
    claim = cl.parse_claims("- 公司回购股份 [巨潮|2026-08-01|https://x/1]",
                            subject="000651")[0]
    claim.citation_observation_ids = [oid]
    artifact_text = "关于回购股份的公告全文"
    assert claim.value not in artifact_text          # 子串确实对不上
    linted = cl.lint_claim(claim, catalog=cat, artifacts={oid: {"text": artifact_text}})
    assert linted.status == cl.UNVERIFIED              # 谓语匹配仍不能确认本公司事件


def test_no_artifact_is_unknown_not_fail(tmp_path):
    """没抓过不等于抓不到。"""
    cat = _cat(tmp_path, [_obs()])
    oid = cat.observations().iloc[0]["source_observation_id"]
    claim = cl.parse_claims("- 公司回购 [巨潮|2026-08-01|https://x/1]",
                            subject="000651")[0]
    claim.citation_observation_ids = [oid]
    linted = cl.lint_claim(claim, catalog=cat, artifacts={})
    assert linted.lint["verdicts"][cl.ACCESSIBLE] == cl.UNKNOWN
    assert linted.status == cl.UNVERIFIED


# ── 3. 日期焊接 ────────────────────────────────────────────────


def test_date_weld_is_detected(tmp_path):
    cat = _cat(tmp_path, [_obs()])
    oid = cat.observations().iloc[0]["source_observation_id"]
    claim = cl.parse_claims("- 公司回购 [巨潮|2026-06-01|https://x/1]",
                            subject="000651")[0]
    claim.citation_observation_ids = [oid]
    weld = cl.detect_date_weld(claim, catalog=cat)
    assert weld["verdict"] == cl.FAIL
    assert "日期焊接" in weld["reasons"][0]


def test_small_lag_is_tolerated(tmp_path):
    """公告盘后/周末发出 → 首见日天然滞后,容忍窗内不算焊接。"""
    cat = _cat(tmp_path, [_obs()])
    oid = cat.observations().iloc[0]["source_observation_id"]
    claim = cl.parse_claims("- 公司回购 [巨潮|2026-08-03|https://x/1]",
                            subject="000651")[0]
    claim.citation_observation_ids = [oid]
    assert cl.detect_date_weld(claim, catalog=cat)["verdict"] == cl.PASS


def test_missing_coverage_is_unknown_not_false(tmp_path):
    """「我们没存」不等于「它不存在」—— 覆盖不足时不自动判假(§1.3-3)。"""
    cat = _cat(tmp_path)
    claim = cl.parse_claims("- 公司回购 [巨潮|2026-08-01|https://x/1]",
                            subject="000651")[0]
    claim.citation_observation_ids = ["ln_nonexistent#r0"]
    weld = cl.detect_date_weld(claim, catalog=cat)
    assert weld["verdict"] == cl.UNKNOWN
    assert "不等于" in weld["reasons"][0]


def test_claim_without_date_is_unknown(tmp_path):
    claim = cl.Claim(claim_id="x", subject="000651", predicate="p", value="v",
                     effective_date=None)
    assert cl.detect_date_weld(claim, catalog=_cat(tmp_path))["verdict"] == cl.UNKNOWN


# ── 4. 源/模型责任分离 + 不发布伪精确 ─────────────────────────


def test_blame_is_undetermined_unless_refuted():
    claim = cl.Claim(claim_id="x", subject="s", predicate="p", value="v",
                     effective_date=None, status=cl.UNVERIFIED)
    assert cl._blame_from({cl.CONTENT_SUPPORTS: cl.UNKNOWN}, claim.status) == \
        cl.BLAME_UNKNOWN


def test_field_mismatch_blames_stale_fact():
    assert cl._blame_from({cl.FIELDS_MATCH: cl.FAIL, cl.CONTENT_SUPPORTS: cl.UNKNOWN},
                          cl.REFUTED) == cl.BLAME_STALE


def test_source_precision_is_suppressed_on_thin_samples():
    claims = [cl.Claim(claim_id=f"c{i}", subject="s", predicate="p", value="v",
                       effective_date=None, raw_citation="[巨潮|d|u]",
                       status=cl.REFUTED if i == 0 else cl.VERIFIED)
              for i in range(3)]
    result = cl.source_precision(claims)
    entry = result["sources"]["巨潮"]
    assert entry["n"] == 3 and entry["refuted"] == 1
    assert entry["precision"] is None                     # 3 条不发布 33% 这种数
    assert "选择性" in entry["precision_suppressed_reason"]


def test_source_precision_is_published_once_the_sample_is_thick():
    claims = [cl.Claim(claim_id=f"c{i}", subject="s", predicate="p", value="v",
                       effective_date=None, raw_citation="[巨潮|d|u]",
                       status=cl.REFUTED if i < 2 else cl.VERIFIED)
              for i in range(25)]
    entry = cl.source_precision(claims)["sources"]["巨潮"]
    assert entry["precision"] == pytest.approx(1 - 2 / 25)


def test_source_precision_empty():
    assert cl.source_precision(pd.DataFrame())["sources"] == {}


# ── 5. cap 改为可观测预算 ──────────────────────────────────────


def test_missing_telemetry_is_not_over_budget():
    """缺计量 ≠ 超预算 —— 这正是与旧「自报超 30 必拒」的分界。"""
    budget = cl.check_budget(None)
    assert budget["over_budget"] is False and budget["basis"] == "none"


def test_self_report_alone_never_triggers_over_budget():
    budget = cl.check_budget({"self_reported_queries": 99})
    assert budget["over_budget"] is False
    assert "只作诊断" in budget["note"]


def test_real_telemetry_triggers_over_budget():
    budget = cl.check_budget({"tool_search_calls": cl.DEFAULT_SEARCH_BUDGET + 1})
    assert budget["over_budget"] is True
    assert budget["basis"] == "tool_telemetry"


def test_self_report_drift_is_recorded_alongside_the_truth():
    budget = cl.check_budget({"tool_search_calls": 12, "self_reported_queries": 30})
    assert budget["self_report_drift"] == 18
    assert budget["over_budget"] is False       # 判据只看真实调用数


def test_wall_clock_budget():
    assert cl.check_budget({"wall_seconds": cl.DEFAULT_WALL_SECONDS + 1})["over_budget"]


# ── 红线:只拒稿不拒票 ─────────────────────────────────────────


def test_rejecting_a_draft_never_rejects_the_ticket(tmp_path):
    cat = _cat(tmp_path, [_obs()])
    oid = cat.observations().iloc[0]["source_observation_id"]
    review = cl.review_draft("- 公司宣布重大重组 [巨潮|2026-06-01|https://x/1]",
                             subject="000651", catalog=cat,
                             artifacts={oid: {"text": "无关正文"}})
    assert review["ticket_verdict"] == "KEEP"
    assert "只拒稿不拒票" in review["red_line"]


def test_clean_draft_is_accepted(tmp_path):
    review = cl.review_draft("- 公司回购 [巨潮|2026-08-01|https://x/1]",
                             subject="000651", catalog=_cat(tmp_path))
    assert review["draft_verdict"] == "ACCEPT_DRAFT"


def test_over_budget_rejects_the_draft_only(tmp_path):
    review = cl.review_draft("- 公司回购 [巨潮|2026-08-01|https://x/1]",
                             subject="000651", catalog=_cat(tmp_path),
                             telemetry={"tool_search_calls": 999})
    assert review["draft_verdict"] == "REJECT_DRAFT"
    assert review["ticket_verdict"] == "KEEP"


def test_cli_exit_code_is_zero_even_when_the_draft_is_rejected(tmp_path, capsys):
    draft = tmp_path / "_l4_intel_000651.md"
    draft.write_text("- 一句没有引用的话\n", encoding="utf-8")
    assert cl.main(["lint", str(draft), "--catalog-root",
                    str(tmp_path / "catalog")]) == 0
    assert "只拒稿不拒票" in capsys.readouterr().out


# ── 落盘 ───────────────────────────────────────────────────────


def test_ledger_roundtrip(tmp_path):
    claims = cl.parse_claims("- 公司回购 [巨潮|2026-08-01|https://x/1]",
                             subject="000651")
    path = cl.write_ledger(tmp_path / "day", claims)
    frame = pd.read_csv(path, dtype=str, keep_default_na=False)
    assert len(frame) == 1
    assert list(frame.columns) == cl._LEDGER_COLUMNS


def test_price_verifier_is_not_reused_for_announcement_facts():
    """§1.4 核验分工:公告事实另建 verifier,不得复用价格对账器。"""
    import inspect

    source = inspect.getsource(cl)
    assert "price_claims" in source                 # 只在注释里说明边界
    assert "from autoresearch.scan.price_claims" not in source
    assert "import price_claims" not in source


def test_industry_keyword_cannot_establish_company_benefit():
    claim = cl.Claim(claim_id="benefit", subject="600000", predicate="回购",
                    value="本公司已完成回购并受益", effective_date=None)
    verdict, _ = cl._supports(claim, ["行业鼓励上市公司回购，未提及这家公司。"])
    assert verdict == cl.UNKNOWN
