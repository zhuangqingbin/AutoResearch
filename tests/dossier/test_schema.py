from autoresearch.dossier.schema import (
    SECTIONS,
    SUMMARY_ANCHORS,
    SUMMARY_HEAD,
    est_tokens,
    lint_dossier,
    parse_frontmatter,
    render_frontmatter,
)

META = {"code": "300857", "name": "协创数据", "sector": "消费电子", "pool_status": "active",
        "entered": "2026-07-23", "entry_reason": "pinned", "initiated": None,
        "last_refresh": None, "last_delta": None}


def _ok_doc() -> str:
    summary = SUMMARY_HEAD + "\n" + "\n".join(f"- {a} x" for a in SUMMARY_ANCHORS) + "\n"
    body = "\n".join(f"{s}\n(内容)\n" for s in SECTIONS)
    return render_frontmatter(META) + "\n" + summary + "\n" + body


def test_frontmatter_roundtrip():
    doc = _ok_doc()
    meta = parse_frontmatter(doc)
    assert meta["code"] == "300857" and meta["entry_reason"] == "pinned"


def test_parse_frontmatter_garbage_empty():
    assert parse_frontmatter("no frontmatter here") == {}


def test_lint_ok_doc_clean():
    assert lint_dossier(_ok_doc()) == []


def test_lint_reports_missing_section_and_anchor():
    doc = _ok_doc().replace("## 5. 风险矩阵", "## 5. 风险").replace("- 判例: x\n", "")
    issues = lint_dossier(doc)
    assert any("风险矩阵" in i for i in issues) and any("判例" in i for i in issues)


def test_lint_summary_over_cap():
    doc = _ok_doc().replace("- 判例: x", "- 判例: " + "长" * 5000)
    assert any("summary>cap" in i for i in lint_dossier(doc))


def test_est_tokens_cjk():
    assert est_tokens("字" * 28) == 30      # 28字×3B=84B ÷2.8 = 30


def test_staleness_issues():
    from autoresearch.dossier import schema
    head = ("---\ncode: 300857\nname: x\nsector: x\npool_status: active\n"
            "entered: 2026-01-01\nentry_reason: pinned\ninitiated: {ini}\n"
            "last_refresh: {ref}\nlast_delta: 2026-07-24\n---\n")
    fresh = head.format(ini="2026-01-01", ref="2026-07-01")
    assert schema.staleness_issues(fresh, "2026-07-24") == []
    stale = head.format(ini="2026-01-01", ref="2026-03-01")
    iss = schema.staleness_issues(stale, "2026-07-24")
    assert len(iss) == 1 and "档案陈旧" in iss[0] and "2026-03-01" in iss[0]
    # I-3(2026-07-24 终审):天数/阈值必须是 staleness_age()/STALE_DAYS 本身算出来的,不是
    # 巧合凑出的文案——把「距今」润色成「已过」、把 STALE_DAYS 90→60 两个变异都曾在旧
    # 测试(只断言 "档案陈旧" in iss[0])下存活,这里把数值钉死。
    assert schema.staleness_age(stale, "2026-07-24") == 145
    assert f"距今 145 日(>{schema.STALE_DAYS})" in iss[0]
    # 边界(m-2):age==cap_days 不报,age==cap_days+1 才报,防 off-by-one 无锁定回归
    at_cap = head.format(ini="2026-01-01", ref="2026-04-25")     # 恰好 90 日前
    assert schema.staleness_age(at_cap, "2026-07-24") == schema.STALE_DAYS
    assert schema.staleness_issues(at_cap, "2026-07-24") == []
    over_cap = head.format(ini="2026-01-01", ref="2026-04-24")   # 91 日前
    assert schema.staleness_age(over_cap, "2026-07-24") == schema.STALE_DAYS + 1
    assert schema.staleness_issues(over_cap, "2026-07-24") != []
    # last_refresh 空 → 退回 initiated 计龄;m-1(2026-07-24 终审):措辞须如实指名
    # initiated,不得冒充"last_refresh 有值"(生产首批 4/4 真档案全走这条回退路径)。
    no_ref = head.format(ini="2026-01-01", ref="null")
    iss_fallback = schema.staleness_issues(no_ref, "2026-07-24")
    assert len(iss_fallback) == 1
    assert "initiated 2026-01-01" in iss_fallback[0]
    assert "last_refresh 未设" in iss_fallback[0]
    assert "last_refresh 2026-01-01" not in iss_fallback[0]
    # 两者皆空 → 不报(骨架未首覆,归 pending_init 管)
    both = head.format(ini="null", ref="null")
    assert schema.staleness_issues(both, "2026-07-24") == []
    assert schema.staleness_age(both, "2026-07-24") is None


def test_staleness_age():
    """I-3:staleness_age 是"天数"的单一事实源(int|None)。staleness_issues 内部调它
    渲染文案,prelude 直接调它 + STALE_DAYS 拼消息——不再有第二份"从文案反解天数"的
    逻辑(此前 prelude 用 `.split('距今 ')`,文案/阈值一改就读错,两个变异活体存活)。
    """
    import pytest

    from autoresearch.dossier import schema
    head = ("---\ncode: 300857\nname: x\nsector: x\npool_status: active\n"
            "entered: 2026-01-01\nentry_reason: pinned\ninitiated: {ini}\n"
            "last_refresh: {ref}\nlast_delta: 2026-07-24\n---\n")
    fresh = head.format(ini="2026-01-01", ref="2026-07-01")
    assert schema.staleness_age(fresh, "2026-07-24") == 23
    both = head.format(ini="null", ref="null")
    assert schema.staleness_age(both, "2026-07-24") is None
    # ref 存在但格式畸形(手误漏横杠,终审活体复现原值)→ None,留痕转交 staleness_issues(I-1)
    malformed = head.format(ini="2026-01-01", ref="20260830")
    assert schema.staleness_age(malformed, "2026-07-24") is None
    # today 畸形不吞——它是全池共享输入,悄悄返回 None 会让整池探针看起来"全新鲜"(I-1)
    with pytest.raises(ValueError):
        schema.staleness_age(fresh, "20260724")


def test_staleness_issues_ref_malformed_reports_not_silent():
    """I-1(2026-07-24 终审):last_refresh 格式畸形(非空但解析失败)→ 明确的「档案日期
    畸形」issue,不再吞成 `[]`。此前该分支与"两日期皆空"共用同一个 `except: return []`,
    而 `lint_dossier` 对 frontmatter 日期零校验——两边都不报 = 降级不留痕(`reconcile.main
    --today` 一次手误就能让畸形日期写进档案且此后 1.5 年不再告警)。
    """
    from autoresearch.dossier import schema
    head = ("---\ncode: 300857\nname: x\nsector: x\npool_status: active\n"
            "entered: 2026-01-01\nentry_reason: pinned\ninitiated: 2026-01-01\n"
            "last_refresh: {ref}\nlast_delta: 2026-07-24\n---\n")
    bad = head.format(ref="20260830")     # 手误漏横杠(终审活体复现原值)
    iss = schema.staleness_issues(bad, "2026-07-24")
    assert len(iss) == 1
    assert "档案日期畸形" in iss[0] and "last_refresh" in iss[0] and "20260830" in iss[0]
    assert schema.staleness_age(bad, "2026-07-24") is None

    # last_refresh 缺、initiated 畸形 → fallback 链路同样要报,且字段名须指对(不能仍写 last_refresh)
    head2 = ("---\ncode: 300857\nname: x\nsector: x\npool_status: active\n"
             "entered: 2026-01-01\nentry_reason: pinned\ninitiated: {ini}\n"
             "last_refresh: null\nlast_delta: 2026-07-24\n---\n")
    bad2 = head2.format(ini="not-a-date")
    iss2 = schema.staleness_issues(bad2, "2026-07-24")
    assert len(iss2) == 1
    assert "档案日期畸形" in iss2[0] and "initiated" in iss2[0]


def test_staleness_issues_today_malformed_raises():
    """I-1:today 畸形不得静默吞成"新鲜"——staleness_issues 原样抛出(经 staleness_age)。"""
    import pytest

    from autoresearch.dossier import schema
    doc = ("---\ncode: 300857\nname: x\nsector: x\npool_status: active\n"
           "entered: 2026-01-01\nentry_reason: pinned\ninitiated: 2026-01-01\n"
           "last_refresh: 2026-07-01\nlast_delta: 2026-07-24\n---\n")
    with pytest.raises(ValueError):
        schema.staleness_issues(doc, "20260724")
