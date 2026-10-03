"""模板 ↔ 校验器往返(2026-10-03 复盘稿 A10)。

10-02 首跑的 R1-1(行业 brief 被判「给买卖建议」)与早停行数 44 vs 36、🟥 vs ✅ 都是同一种病:
agent 定义里的模板与下游解析器 / 校验器各写一份,没有任何东西逼它们一致。sector-brief 与
macro-brief 已有往返测试(`tests/session_agent/test_sector_products.py`、`test_macro_lite.py`);
这里补齐其余扫描研究角色:**从活的 agent 定义里取模板,按模板填一份,再交给生产解析器 / 校验器**。
模板改了而解析器没跟上(或反过来),这里先红。
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
AGENTS = ROOT / ".claude" / "agents"


def _definition(name: str) -> str:
    return (AGENTS / f"{name}.md").read_text(encoding="utf-8")


def _fenced(text: str) -> list[str]:
    return re.findall(r"(?ms)^```[^\n]*\n(.*?)^```", text)


def _fill(template: str, **fixed: str) -> str:
    """把模板占位按「先具名、再取第一个选项、最后填 无」的顺序落成一份可读的样例。"""
    for key, value in fixed.items():
        template = template.replace(f"<{key}>", value)
    while re.search(r"<[^<>\n]+>", template):        # 嵌套占位(如 `<禁止|条件(<一句>)>`)由内向外逐层落
        template = re.sub(r"<([^<>|\n]+)\|[^<>\n]*>", lambda m: m.group(1), template)
        template = re.sub(r"<[^<>|\n]+>", "无", template)
    return template


# ── l3-rank:输出段列出的字段 == 契约 v2 字段 ─────────────────────────────────────────


def test_l3_rank_output_section_lists_exactly_the_v2_contract_fields():
    from autoresearch.contracts.agent_output import L3_RANK_FIELDS_V2

    text = _definition("l3-rank")
    output = text.split("## 输出", 1)[1].split("\n## ", 1)[0]
    line = next(ln for ln in output.splitlines() if "`schema_version`" in ln)
    listed = set(re.findall(r"`([a-z][a-z0-9_]*)`", line))
    assert listed == set(L3_RANK_FIELDS_V2), (
        f"定义多了 {sorted(listed - set(L3_RANK_FIELDS_V2))},少了 {sorted(set(L3_RANK_FIELDS_V2) - listed)}")


# ── l4-intel:按模板填一份,声明行与事件表必须能被生产解析器读回 ──────────────────────


def _intel_sample() -> str:
    template = _fenced(_definition("l4-intel"))[0]
    sample = _fill(template, 代码="600519", 名称="贵州茅台", 分析日="2026-09-29", N="8")
    header = "|---|---|---|---|---|"
    row = "| 2026-09-29 | T0 | 公告:回购完成 1.2 亿元 | 上交所 https://www.sse.com.cn/a | +1 |"
    assert header in sample, "模板的事件表分隔行不在了,本用例要测的形状变了"
    return sample.replace(header, header + "\n" + row, 1)


def test_l4_intel_template_round_trips_through_the_guard(tmp_path):
    from autoresearch.contracts.agent_output import L4_INTEL
    from autoresearch.scan.l4 import intel_guard as ig

    sample = _intel_sample()
    assert re.search(L4_INTEL.field("declaration").pattern, sample)
    assert ig.claimed_queries(sample) == 8
    assert len(ig._event_rows(sample)) == 1
    scan = tmp_path / "2026-09-29"
    scan.mkdir()
    ig.intel_path(scan, "600519").write_text(sample, encoding="utf-8")
    verdict = ig.guard_intel(scan, "600519", soft_cap=20)
    assert verdict["action"] == "KEPT" and verdict["claimed"] == 8


# ── l4-card:两种卡形各填一份,评级 / 提案 / 入场 / 早停 / 执行线 / 盯梢线都要读得回 ──────


def _card_sections() -> tuple[str, str]:
    text = _definition("l4-card")
    body = text.split("## 卡片模板", 1)[1].split("## 压缩纪律", 1)[0]
    early, full = body.split("### B. 满卡", 1)
    return early, full


def _card_sample(section: str, *, rating: str, proposal: str) -> str:
    lines = []
    for line in section.splitlines():
        if line.startswith("```") or line.startswith("###"):
            continue
        # 只钉住描述性占位(`<五档>`);早停行、入场行等有格式的契约行一律按模板原样填,才算往返。
        if line.startswith("**Rating**"):
            line = f"**Rating**: {rating}"
        elif line.startswith("FINAL TRANSACTION PROPOSAL"):
            line = f"FINAL TRANSACTION PROPOSAL: **{proposal}**"
        lines.append(line)
    return _fill("\n".join(lines), 代码="600519", 名称="贵州茅台", date="2026-09-29")


@pytest.mark.parametrize("shape,rating,proposal", [("early", "Hold", "HOLD"),
                                                    ("full", "Underweight", "SELL")])
def test_l4_card_templates_round_trip_through_the_production_parsers(shape, rating, proposal):
    from autoresearch.agents.utils.rating import validate_rating_and_proposal
    from autoresearch.contracts.agent_output import _ENTRY_LINE_RE
    from autoresearch.scan.l4.parsers import parse_early_stop

    early, full = _card_sections()
    sample = _card_sample(early if shape == "early" else full, rating=rating, proposal=proposal)

    assert validate_rating_and_proposal(sample) == (rating, proposal)
    entries = _ENTRY_LINE_RE.findall(sample)
    assert len(entries) == 1                                     # 恰好一条机读入场行
    assert entries[0] in ({"禁止", "条件"} if shape == "early" else {"允许", "禁止", "条件"})
    stop = parse_early_stop(sample)
    if shape == "early":
        assert stop is not None and stop["phase"] == "P1"        # 模板的早停行按原样读得回
    else:
        assert stop is None                                      # 满卡不写早停行


def test_l4_card_dsl_examples_parse_back():
    """`[执行线]` 与 `[价格线]` 的样例行就是确定性层核对用的 DSL —— 样例本身必须读得回。"""
    from autoresearch.contracts.agent_output import L4_CARD
    from autoresearch.scan.tripwire_watch import parse_tripwires

    _early, full = _card_sections()
    assert re.search(L4_CARD.field("exec_line_pct").pattern, full)
    assert re.search(L4_CARD.field("exec_line_pos").pattern, full)
    kinds = {row["kind"] for row in parse_tripwires(full)}
    assert {"price", "date"} <= kinds


# ── dossier-init:定义里要求填的节,在档案骨架的节表里都存在 ──────────────────────────


def test_dossier_init_writes_only_into_sections_the_skeleton_has():
    from autoresearch.dossier.schema import SECTIONS, SUMMARY_ANCHORS, SUMMARY_HEAD

    text = _definition("dossier-init")
    targets = text.split("## 研究正文只写四处", 1)[1].split("\n## ", 1)[0]
    numbered = set(re.findall(r"§(\d)", targets))
    assert numbered == {"1", "2", "5"}
    names = {s.split(". ", 1)[0].removeprefix("## ") for s in SECTIONS}
    assert numbered <= names
    assert SUMMARY_HEAD.removeprefix("## ").split("(")[0] in targets
    for anchor in ("业务:", "驱动:", "风险:", "催化:"):
        assert anchor in targets and anchor in SUMMARY_ANCHORS
