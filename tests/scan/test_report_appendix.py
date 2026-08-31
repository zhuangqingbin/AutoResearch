"""`render_appendix` 纯渲染契约(design 2026-08-28-summary-slimdown §4.2 / §6.5 / §6.9 / §6.10)。

锁四件事:
  ① **结构完整性**:A–G 七节标题 + 七个 `<a id>` 锚恒在、顺序固定 —— 素材缺席印 `ABSENT`,
     不靠删整节表达 absence(否则 sentinel / 无 Tier-3 的合法缺席会被误报成丢件);
  ② **事实归属**:L3 全文进 C、漏斗表进 B、耗时表进 E、门柱直方图进 D(§4.1.1);
  ③ **纯函数**:同一 model 连渲两次字节相同,且源码里不出现 `open(` / `Path(`
     —— 防后人把「回去读一下盘」偷偷加回渲染层;
  ④ **展示层预算**:满料 fixture ≤ `APPENDIX_WARN_BYTES`,`appendix_budget_warn` 只告警不截断。

全部用手造 `ReportModel`,零 IO、零 staging。
"""
from __future__ import annotations

import ast
import inspect

import pytest

from autoresearch.scan import report_appendix as ra
from autoresearch.scan.report_appendix import appendix_budget_warn, render_appendix
from autoresearch.scan.report_model import (
    ABSENT,
    APPENDIX_ANCHORS,
    APPENDIX_SECTIONS,
    APPENDIX_WARN_BYTES,
    FOOTNOTES,
    METHOD_ANCHORS,
    RUN_OBSERVATION_DETAIL_MARKERS,
    ReportModel,
)

_KEYS = [k for k, _ in APPENDIX_SECTIONS]


def _anchor(key: str) -> str:
    return f'<a id="{APPENDIX_ANCHORS[key]}"></a>'


def _sections(md: str) -> dict[str, str]:
    """按锚切片 → {节 key: 该节全文}(含锚行本身)。"""
    idx = [md.index(_anchor(k)) for k in _KEYS]
    return {k: md[idx[i]:(idx[i + 1] if i + 1 < len(idx) else len(md))]
            for i, k in enumerate(_KEYS)}


# ── fixtures ───────────────────────────────────────────────────────────────
@pytest.fixture
def empty_model() -> ReportModel:
    """全空:只有身份三件,所有条件素材缺席。"""
    return ReportModel(analysis_date="", hhmm="", folder="")


@pytest.fixture
def full_model() -> ReportModel:
    """满料:仿 08-26 真跑的素材形状(生产者块自带 `##`/`###` 标题也照搬)。"""
    return ReportModel(
        analysis_date="2026-08-26",
        hhmm="2120",
        folder="20260826_2120",
        meta={"universe": 4316, "l2_engine": "stratified"},
        n_l1=4316,
        n_l2=201,
        finals_rows=[
            {"ticker": "002926", "code": "002926", "name": "华西证券", "sector": "证券Ⅱ",
             "conviction": "70",
             "thesis": "本表 40 只里唯一 main_net_ratio 0.11 / cmf_20 0.15 三者同向为正的票,\nPB 0.88 破净。",
             "risk": "小市值券商 beta 最高|两融余额 5 日 -424.49 亿显示杠杆在退。",
             "catalyst": "中报披露收官周(8/31 截止),券商业绩集中释放。"},
            {"ticker": "001332", "code": "001332", "name": "锡装股份", "sector": "专用设备",
             "conviction": "40",
             "thesis": "深跌落刀无主力,资金不得作论点。",
             "risk": "pct_60d -44.05 < -20 且 main_net_ratio 0。",
             "catalyst": "无;近 10 日为董事会决议公告(中性)。"},
        ],
        market_view_raw="# 市场研判 — 2026-08-26\n\n## 1. 定调\nrange 震荡市 + 杠杆退潮。\n\n"
                        "## 2. 市场结构\n宽度 44.67% 站上 MA60、多头排列仅 12.21%。",
        funnel_readout="### 📉 今日漏斗读数\n- **0 买**:10 只 finalist 深核后无一进买单。",
        funnel_table_lines=[
            "| 阶段 | 名称 | 出量 | 引擎 | 卡点标准 |", "|---|---|---:|---|---|",
            "| L0 | 选集 | 4316 | 确定性 | 全A 5509 → 硬门 |",
            "| L2 | 粗排 | 201 | 分层采样/stratified | sn_composite 排序+风格桶 floor |",
        ],
        degraded_line="- **⚠️ 数据降级**(B 级增强端点缺失):hk_hold×1",
        stage_overview_lines=[
            "\n**召回(L1)** — 复合分 top;快因子主导排序。",
            "- 行业分布 top5:半导体(50)、软件开发(35)",
            "- 代表股:华友钴业, 铜陵有色",
        ],
        menu_health_block="### 🍱 L2 菜单体检(vs 全市场)\n- **行业集中度 top3**:半导体 6%(13)",
        timing_lines=[
            "## 各阶段耗时 & 落盘字节",
            "| 阶段 | 引擎 | effort | 墙钟 | LLM 调用 | 落盘字节 | 说明 |",
            "|---|---|---|---:|---:|---:|---|",
            "| L3 精排 | Opus·holistic | max | 19m23s | 1 | 252047 | 通看全表选 finalists |",
            "| **合计** | — | — | 129m59s | **22** | — | 墙钟 = mtime 推导下界 |",
        ],
        gate_histogram_block="**OW三门失守分布**(4 卡可解析):主力真在✗ 2 · 业绩真兑现✗ 2 · 估值不透支✗ 1",
        verify_detail_lines=[
            "", "### 🛡️ Tier-3 买单多空辩论(2 只:1 维持 / 1 降级 / 0 否决)",
            "- **002926** 降级:多:资金三指标同向;空:板块 beta 退潮 ｜ 触发:两融退",
        ],
        banner_full="> 🛑 自检未通过(发布前须先修根因) — fail 1 / warn 2\n"
                    "> ⚠️ **intel限频**:自报 22 条 > cap 20\n"
                    "> ⚠️ **intel时效窗**:最新一条 12 日前\n"
                    "> 🛑 **覆盖率不足**:卡 8/10",
        review_result={
            "ok": False, "n_fail": 1, "n_warn": 2,
            "failures": [
                {"key": "intel限频", "msg": "自报 22 条 > cap 20", "severity": "warn"},
                {"key": "intel时效窗", "msg": "最新一条 12 日前", "severity": "warn"},
                {"key": "覆盖率不足", "msg": "卡 8/10", "severity": "fail"},
            ],
        },
    )


# ── ① 结构完整性 ───────────────────────────────────────────────────────────
@pytest.mark.parametrize("fx", ["empty_model", "full_model"])
def test_seven_sections_and_anchors_present_in_order(fx, request):
    md = render_appendix(request.getfixturevalue(fx))
    assert md.startswith("# 扫描附录 — ")
    pos = -1
    for key, title in APPENDIX_SECTIONS:
        a, h = md.find(_anchor(key)), md.find(f"\n## {title}\n")
        assert a >= 0, f"{key} 锚缺失"
        assert h >= 0, f"{title} 标题缺失"
        assert a < h, f"{key}:锚必须紧邻标题之前"
        assert a > pos, f"{key} 节序错乱"
        pos = h


def test_empty_model_marks_absence_and_drops_nothing(empty_model):
    md = render_appendix(empty_model)
    sec = _sections(md)
    assert set(sec) == set(_KEYS)
    # F 节(方法与口径)是**恒定注文**,天然没有「缺席」态 —— 其余六节都必须显式印 ABSENT。
    for key in [k for k in _KEYS if k != "methods"]:
        assert ABSENT in sec[key], f"{key} 节缺 ABSENT 标记(空素材必须留痕,不得删节)"
    assert ABSENT not in sec["methods"]
    # 结构节数量恒等于契约表(不多不少)
    assert md.count("\n## ") == len(APPENDIX_SECTIONS)


def test_producer_blocks_never_emit_h2_inside_a_section(full_model):
    """生产者块自带的 `## 各阶段耗时` 必须被压成 `###` —— 否则在 E 节里劈出兄弟节、锚导航断链。"""
    md = render_appendix(full_model)
    heads = [ln for ln in md.split("\n") if ln.startswith("## ")]
    assert heads == [f"## {t}" for _, t in APPENDIX_SECTIONS]
    assert "### 各阶段耗时 & 落盘字节" in md
    assert "### 🍱 L2 菜单体检(vs 全市场)" in md


# ── ② 事实归属 ─────────────────────────────────────────────────────────────
def test_full_model_routes_material_to_the_right_section(full_model):
    sec = _sections(render_appendix(full_model))
    # A:自检全文(逐条,不是聚合计数)
    assert "intel限频" in sec["self_review"] and "intel时效窗" in sec["self_review"]
    assert "覆盖率不足" in sec["self_review"] and "卡 8/10" in sec["self_review"]
    # B:漏斗表 / 降级 / 卡点 / 菜单 / 0买机制
    assert "| L0 | 选集 | 4316 | 确定性 | 全A 5509 → 硬门 |" in sec["funnel"]
    assert "hk_hold×1" in sec["funnel"]
    assert "代表股:华友钴业, 铜陵有色" in sec["funnel"]
    assert "行业集中度 top3" in sec["funnel"]
    assert "10 只 finalist 深核后无一进买单" in sec["funnel"]
    # C:策略师全文 + L3 逐票(风险/催化/论点/conviction)+ Tier-3
    assert "宽度 44.67% 站上 MA60" in sec["research"]
    for fr in full_model.finals_rows:
        assert fr["name"] in sec["research"]
        assert fr["catalyst"].split("|")[0][:12] in sec["research"]
        assert fr["risk"].split("|")[0][:12] in sec["research"]
    assert "conv 70" in sec["research"]
    assert "Tier-3 买单多空辩论" in sec["research"]
    # D:门柱直方图 + 两口径指向 F
    assert "OW三门失守分布" in sec["gates"]
    assert f"(#{METHOD_ANCHORS['gate']})" in sec["gates"]
    # E:耗时全表
    assert "| L3 精排 | Opus·holistic | max | 19m23s | 1 | 252047 | 通看全表选 finalists |" in sec["runtime"]
    assert "129m59s" in sec["runtime"]
    # G:三条局限 + 六段漏斗免责 + 溯源尾注
    for line in ra.HONEST_LIMITATIONS:
        assert line in sec["limitations"]
    assert ra.FUNNEL_DISCLAIMER in sec["limitations"]
    assert "reports/scan/20260826_2120/" in sec["limitations"]
    # 反向:决策层事实不得在附录二次展开(漏斗表只在 B、耗时表只在 E)
    assert "OW三门失守分布" not in sec["funnel"]
    assert "129m59s" not in sec["funnel"]


def test_l4_cards_are_index_links_not_copies(full_model):
    """`details/` 的卡与 `trace/` 法证文件只列索引链接 —— appendix 不是第三份权威源(§4.1.2)。"""
    sec = _sections(render_appendix(full_model))
    assert "[决策卡](details/华西证券.md)" in sec["research"]
    assert "[决策卡](details/锡装股份.md)" in sec["research"]
    assert "`trace/decision_records.json`" in sec["gates"]


def test_free_text_is_flattened_to_one_line_per_fact(full_model):
    """C 节逐票是「一行一事实」:CSV 里的换行/竖线不得劈出新行、劈坏表格。"""
    md = render_appendix(full_model)
    for ln in md.split("\n"):
        if ln.startswith("  - 风险:"):
            assert "|" not in ln
    assert "  - 论点:本表 40 只里唯一 main_net_ratio 0.11 / cmf_20 0.15 三者同向为正的票, PB 0.88 破净。" in md


# ── ③ managed 块 / F 节锚 ──────────────────────────────────────────────────
def test_run_observation_detail_marker_appears_exactly_once(full_model, empty_model):
    start, end = RUN_OBSERVATION_DETAIL_MARKERS
    for model in (full_model, empty_model):
        md = render_appendix(model)
        assert md.count(start) == 1 and md.count(end) == 1
        assert md.index(start) < md.index(end)
        sec = _sections(md)
        assert start in sec["runtime"], "detail 块必须落在 E 节(post_run 按锚回退时才找得到)"
        # 块内先留空:post_run observe 注入前不得有内容,否则注入会被当成重复事实
        body = md[md.index(start) + len(start):md.index(end)]
        assert body.strip() == ""


def test_methods_section_expands_every_footnote_with_stable_anchor(full_model):
    sec = _sections(render_appendix(full_model))["methods"]
    assert sec.count("\n### ") == len(FOOTNOTES)
    for key, title, text in FOOTNOTES:
        anchor = f'<a id="{METHOD_ANCHORS[key]}"></a>'
        assert anchor in sec, f"{key} 方法锚缺失"
        assert sec.index(anchor) < sec.index(f"### {title}")
        assert text in sec, f"{key} 注文未展开"
    # 锚 id 只认语义 key,不按出现顺序编号(节序一变编号就漂移)
    assert "method-1" not in sec and "口径1" not in sec


# ── ④ 纯函数 / 预算 ────────────────────────────────────────────────────────
def test_render_is_deterministic_byte_for_byte(full_model):
    assert render_appendix(full_model) == render_appendix(full_model)


def _module_ast():
    return ast.parse(inspect.getsource(ra))


def _imported_names() -> set[str]:
    names: set[str] = set()
    for node in ast.walk(_module_ast()):
        if isinstance(node, ast.Import):
            names |= {a.name for a in node.names}
        elif isinstance(node, ast.ImportFrom):
            base = node.module or ""
            names |= {base} | {f"{base}.{a.name}" for a in node.names}
    return names


def _called_names() -> set[str]:
    out: set[str] = set()
    for node in ast.walk(_module_ast()):
        if isinstance(node, ast.Call):
            f = node.func
            if isinstance(f, ast.Name):
                out.add(f.id)
            elif isinstance(f, ast.Attribute):
                out.add(f.attr)
    return out


def test_render_appendix_never_touches_the_filesystem():
    """字面探针 + AST 探针双保险 —— 防后人把「回去读一下盘」偷偷加回渲染层。"""
    src = inspect.getsource(render_appendix)
    for forbidden in ("open(", "Path(", "read_text", "write_text"):
        assert forbidden not in src, f"render_appendix 出现 {forbidden} —— 渲染层禁止读写盘"
    assert not [n for n in _imported_names() if "pathlib" in n or n.split(".")[0] in ("os", "io")]
    assert not ({"open", "read_text", "write_text", "read_bytes", "glob", "exists"}
                & _called_names())


def test_no_rating_or_review_recomputation():
    """渲染层不得 import 评级/落盘/自检链路(fold 与 review 只在 prepare 阶段做一次)。"""
    bad = [n for n in _imported_names()
           if any(x in n for x in ("decision_finalize", "self_review", "assemble", "publisher"))]
    assert not bad, f"report_appendix 触到 {bad}"
    assert not ({"review", "parse_rating", "_dump_final_ratings", "_dump_decision_records"}
                & _called_names())


def test_full_fixture_fits_the_display_budget(full_model):
    md = render_appendix(full_model)
    assert len(md.encode("utf-8")) <= APPENDIX_WARN_BYTES
    assert appendix_budget_warn(md) is None


def test_budget_warn_reports_bytes_and_never_truncates():
    text = "啊" * (APPENDIX_WARN_BYTES // 3 + 10)          # 每字 3 字节 → 必超
    n = len(text.encode("utf-8"))
    warn = appendix_budget_warn(text)
    assert warn is not None
    assert str(n) in warn and str(APPENDIX_WARN_BYTES) in warn
    assert "\n" not in warn                                 # 一行展示层文案
    assert appendix_budget_warn("") is None


# ── A 节:banner 全文 / 兜底 / 缺席 三态 ────────────────────────────────────
def test_a_prints_banner_verbatim_without_a_second_expansion(full_model):
    """banner 在场 → 逐条原样;**不再**另开一张明细表(同一批 fail/warn 不说两遍)。"""
    sec = _sections(render_appendix(full_model))["self_review"]
    for line in full_model.banner_full.split("\n"):
        assert line in sec
    assert sec.count("intel限频") == 1
    assert sec.count("覆盖率不足") == 1
    assert ABSENT not in sec


def test_a_falls_back_to_review_result_when_banner_missing(full_model):
    """banner 未回填但 review_result 在 → 逐条兜底展开,不把 warn 静默吞掉。"""
    m = ReportModel(**{**full_model.__dict__, "banner_full": ""})
    sec = _sections(render_appendix(m))["self_review"]
    assert "fail 1 / warn 2" in sec
    assert "- ⚠️ **intel限频**:自报 22 条 > cap 20" in sec
    assert "- 🛑 **覆盖率不足**:卡 8/10" in sec
    assert ABSENT not in sec


def test_a_is_absent_when_review_was_not_attached(full_model):
    """两个素材都缺 → A 节印 ABSENT,**不许**回头跑自检读盘补。"""
    m = ReportModel(**{**full_model.__dict__, "banner_full": "", "review_result": {}})
    sec = _sections(render_appendix(m))["self_review"]
    assert ABSENT in sec and "intel限频" not in sec


def test_a_accepts_legacy_failure_keys(full_model):
    """兼容旧键名 `check`/`detail`(prepare 若直接冻结 review() 原结果也认)。"""
    m = ReportModel(**{**full_model.__dict__, "banner_full": "", "review_result": {
        "failures": [{"check": "覆盖率不足", "detail": "卡 8/10", "severity": "fail"}]}})
    sec = _sections(render_appendix(m))["self_review"]
    assert "**覆盖率不足**:卡 8/10" in sec and ABSENT not in sec
