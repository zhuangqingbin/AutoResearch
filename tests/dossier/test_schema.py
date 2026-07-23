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
