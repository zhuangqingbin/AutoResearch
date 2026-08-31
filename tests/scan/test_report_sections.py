"""`summary.md`(决策层)/ `appendix.md`(现场层)双文件报告包的结构契约。

design: `docs/specs/2026-08-28-summary-slimdown-design.md` §4.1(11 节)/ §4.1.1(事实归属)/
§4.2(appendix A–G)/ §4.3(文风)/ §5(逐节去向)/ §6.9(语义口径链接)/ §6.10(字节预算)。

原文件锁的是 T26 的 22 节旧节序(`## 3. 投资建议` / `## 1. 漏斗` / `### 组合视角` …)。
B+ 重构把 summary 压成 11 节决策层、现场内容整体下沉 `appendix.md`,所以本文件按
**「搬走的断言跟着搬」** 迁移:同一份 fixture 再渲一份 appendix,逐条在附录侧接住。
`## 诚实局限` 锚字面(💸 注入的回退锚)与两个门柱生产者必须各自打标这两条**不变**。

合成 fixture,零网络;所有产物落 tmp_path。
"""
from __future__ import annotations

import csv
import dataclasses
import json
import re

import pandas as pd
import pytest

from autoresearch.scan import report_sections as rs
from autoresearch.scan.assemble import build_summary
from autoresearch.scan.report_appendix import render_appendix
from autoresearch.scan.report_model import (
    ABSENT,
    APPENDIX_ANCHORS,
    APPENDIX_SECTIONS,
    APPENDIX_TARGET_BYTES,
    APPENDIX_WARN_BYTES,
    METHOD_ANCHORS,
    SUMMARY_TARGET_BYTES,
    SUMMARY_WARN_BYTES,
)

_D = "2026-07-03"
_F = "20260703_1200"

_BRIEF_TPL = """# 行业 brief — {ind} @ {date}

## 地形段(喂 L3/L4 · 描述性)
- **链定位一句**:{ind} 是一条以中游制造为核心的链,收入由量与价两端驱动;上游原材料\
占成本大头,下游需求受政策与库存周期影响,盈利节奏随开工率与价差摆动。
- **景气读数**:成分 42 只 · 中位60日 -1.35% · 中位np_yoy 3.42%
- **估值地形**:中位PE 6.04(P25 5.23 / P75 6.74)· 中位PB 0.54

## 研判段(仅 L5)
**行业方向**: {direction} — 资金与龙头在说话,但行业中位没跟上。
- 景气位置:磨底偏稳,内部分化。价的层面是"大盘股上行、中位下行"的剪刀差,\
说明资金在往大市值高股息集中而非全行业普涨;盈利端个位数正增长,估值仍在净资产折价区,\
缺的是盈利加速而非资金。这一段在旧版里被**原文嵌进 summary**,是 13.4KB 的来源。
- 格局与表达:利润集中在负债成本优势环节,存款基础厚、活期占比高的龙头在重定价周期里\
成本改善直接落到息差;议价弱的中小玩家资产端还要多承担区域信用成本。
- 最大证伪点:若中报坐实息差企稳并把中位增速抬到高个位数,同时中位60日转正、\
健康上涨数翻倍,本"中性/磨底"研判作废转上行;反向则转下行判定。
_个股评级只由本股 rubric 三门决定;Claude 推理产出,仅供研究,非投资建议。_
"""


def _scan(tmp_path, *, n_industries: int = 3, pinned: bool = True):
    d = tmp_path / "s"
    (d / "details").mkdir(parents=True)
    (d / "meta.json").write_text(json.dumps({"regime": "range", "universe": 4237,
                                             "universe_raw": 5496, "recall_n": 1000,
                                             "l2_n": 203}), encoding="utf-8")
    rows = [
        {"code": "300476", "name": "甲", "sector": "元件", "lane": "value",
         "thesis": "AI 光模块需求超预期", "risk": "估值高", "catalyst": "Q2 财报"},
        {"code": "000686", "name": "乙", "sector": "证券Ⅱ", "lane": "value",
         "thesis": "券商β", "risk": "winner80", "catalyst": "无"},
    ]
    if pinned:
        rows.append({"code": "600000", "name": "持仓票", "sector": "银行Ⅱ",
                     "lane": "pinned", "thesis": "长期观察仓", "risk": "—",
                     "catalyst": "—"})
    pd.DataFrame(rows).to_csv(d / "finalists.csv", index=False)
    for r in rows:
        (d / "details" / f"{r['code']}.md").write_text(
            "# 决策卡\n**Rubric建议**: 净分-1 ｜ OW三门 主力真在✗·业绩真兑现✓·估值不透支✗ → 压Hold\n"
            "**Rating**: Hold\n", encoding="utf-8")
    briefs = d / "sector_briefs"
    briefs.mkdir()
    for i in range(n_industries):
        ind = f"测试行业{i}"
        (briefs / f"{ind}.md").write_text(
            _BRIEF_TPL.format(ind=ind, date=_D, direction=("看多" if i == 0 else "中性")),
            encoding="utf-8")
    return d


def _render(d, date: str = _D, hhmm: str = "1200", folder: str = _F):
    """一次 `prepare_report_model` → 两处纯渲染(§4.1.2)→ (summary, appendix, model)。

    **搬去 appendix 的断言必须在同一 fixture 上接住**,否则「减层」和「丢料」分不开。
    """
    model = rs.prepare_report_model(d, date, hhmm, folder)
    summary = rs.render_summary(model)
    model = rs.attach_review(model, summary)          # appendix A 要非聚合全文 banner
    return summary, render_appendix(model), model


def _table_rows(md: str, header: str) -> list[str]:
    """取 `header` 之后紧跟的那张 Markdown 表的**数据行**(去表头 + 分隔行)。"""
    i = md.find(header)
    assert i >= 0, f"找不到节标题 {header}"
    rows: list[str] = []
    for ln in md[i:].splitlines()[1:]:
        if ln.startswith("|"):
            rows.append(ln)
        elif rows:
            break
    assert len(rows) >= 2, f"{header} 后面没有表(只有 {len(rows)} 行)"
    return rows[2:]


@pytest.fixture
def md(tmp_path):
    """兼容薄壳 `build_summary` 的产出(publisher 走三段式;两者语义等价)。"""
    return build_summary(_scan(tmp_path), _D, "1200", _F)


@pytest.fixture
def pair(tmp_path):
    return _render(_scan(tmp_path))


# ───────────────────────────── ① 节序:决策主线前置 ─────────────────────────────

def test_decision_line_comes_before_context_sections(pair):
    """§4.1 新节序:仪表盘 → 行动 → 候选 → 保送 → 为什么没有 BUY(决策主线连成一条)。

    旧断言锁的是 `## 3. 投资建议` 这类带编号的标题,已随重构消失;**节序契约本体不变**。
    背景节(漏斗数量 / 各阶段卡点)不再「排在后面」,而是整体离开决策层 → 见附录侧断言。
    """
    summary, appendix, _ = pair
    order = [rs.DASHBOARD_HEADER, "## 行动", "## 候选(", "## 📌 保送持仓", "## 为什么没有 BUY"]
    idx = [summary.find(x) for x in order]
    assert all(i >= 0 for i in idx), f"节缺失:{dict(zip(order, idx, strict=True))}"
    assert idx == sorted(idx), f"决策主线节序错:{dict(zip(order, idx, strict=True))}"

    for background in ("漏斗数量", "各阶段卡点"):
        assert background not in summary, f"{background} 是现场层素材,不该回流决策层"
        assert background in appendix, f"{background} 从 summary 搬走后在 appendix 也没有 = 丢件"


def test_dashboard_placeholder_is_managed_block(md):
    """仪表盘是 managed 块:assemble 落占位,publisher 收尾用 brief 同源事实注入。"""
    assert rs.DASHBOARD_START in md and rs.DASHBOARD_END in md
    assert md.index(rs.DASHBOARD_START) < md.index(rs.DASHBOARD_END)


def test_inject_dashboard_replaces_managed_block(md):
    out = rs.inject_dashboard(md, "**① 市场**:震荡\n**③ 结论**\n- 生产 BUY 0 只")
    assert "**① 市场**:震荡" in out
    block = out.split(rs.DASHBOARD_START, 1)[1].split(rs.DASHBOARD_END, 1)[0]
    assert "此处为占位" not in block, "注入后仪表盘块不应留占位文案"
    assert rs.DASHBOARD_START in out and rs.DASHBOARD_END in out
    assert rs.inject_dashboard(out, "**① 市场**:趋势").count(rs.DASHBOARD_START) == 1


# ───────────────────────────── ② 行业研判节(D6 已整段退役)─────────────────────────────
#
# `_sector_view_section` 与其字节预算机制(`SECTOR_SECTION_MAX_BYTES`/`_terrain_gist`)已随
# 2026-08-19 D6(⚖A6,用户裁定)整段删除:brief 研判段本身被砍除,summary 不再有任何行业
# 研判节,行业方向叙事完全由确定性 top3(`render_sector_top3`)独扛。原三条用例(逐行链接/
# 字节预算鉴别力/presence-gated 空态)测的对象已不存在,随之摘除,不留占位测试。

# ───────────────────────────── ③ 经验节表格化 ─────────────────────────────

# ───────────────────────────── ④ 保留件 + 下沉件 ─────────────────────────────

def test_preserved_sections_survive_the_reorder(pair):
    """减层不减料 —— 逐条说清「谁留决策层、谁下沉附录」,两边都要真的在。"""
    summary, appendix, _ = pair

    # 留在决策层的
    assert "## 诚实局限" in summary
    assert "板块集中度" in summary, "旧「### 组合视角」的内容类别(标题退役,内容仍在行动节)"

    # 下沉附录的(旧断言原样搬过来,靠同一 fixture 的第二份渲染接住)
    for anchor in ("各阶段耗时 & 落盘字节", "L3 逐票", "OW三门失守分布",
                   "各阶段卡点", "漏斗数量", rs.GATE_HIST_BASIS_NOTE):
        assert anchor in appendix, f"重排把 {anchor} 弄丢了(减层不减料被违反)"

    # 且**不许两边都印**(§4.1.1 事实归属:现场层素材只有一个展开点)
    for moved in ("各阶段耗时 & 落盘字节", "OW三门失守分布", "L3 逐票"):
        assert moved not in summary, f"{moved} 同时留在 summary = 同一事实印两次"


def test_no_content_class_is_dropped(tmp_path):
    """减层不减料的直接断言:重排后候选/保送表的每个 finalist 仍逐只在场。"""
    summary, appendix, _ = _render(_scan(tmp_path))
    for name in ("甲", "乙", "持仓票"):
        assert name in summary
    for thesis in ("AI 光模块需求超预期", "券商β", "长期观察仓"):
        assert thesis in appendix, "L3 论点全文下沉附录 C 后必须逐票还在"


# ─────────────── I-3:报告包字节预算 + 内容类别锁(防「删空也绿」) ───────────────
#
# 旧口径是「summary ≤38KB」单条上限。B+ 之后一条上限已经不够:把内容整段删掉,字节
# 断言只会更绿。§7 明令**加内容类别断言**、且**不许用通用 ≥5KB 下界替代内容检查** ——
# 下界只能证明"有字",证明不了"候选表还在、附录七节还在"。

def _fat_scan(tmp_path):
    """按真 08-06 run 的量级造:8 个行业 brief + 12 只 genuine finalist + 1 只 📌 保送。"""
    d = _scan(tmp_path, n_industries=8)
    rows = list(csv.DictReader((d / "finalists.csv").open(encoding="utf-8")))
    extra = [dict(rows[0], code=f"{300000 + i:06d}", name=f"测试票{i}", lane="value")
             for i in range(10)]
    with (d / "finalists.csv").open("w", encoding="utf-8", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows + extra)
    for r in extra:
        (d / "details" / f"{r['code']}.md").write_text(
            "# 决策卡\n**Rubric建议**: 净分-1 ｜ OW三门 主力真在✗ → 压Hold\n**Rating**: Hold\n",
            encoding="utf-8")
    return d


def test_report_bundle_stays_inside_the_display_budget(tmp_path):
    """§6.10:summary ≤ warn 16KB、appendix ≤ warn 24KB(展示层预算,不截断不毙 GATE4)。"""
    summary, appendix, _ = _render(_fat_scan(tmp_path))

    n_sum, n_app = len(summary.encode("utf-8")), len(appendix.encode("utf-8"))
    assert n_sum <= SUMMARY_WARN_BYTES, f"summary {n_sum}B > {SUMMARY_WARN_BYTES}B(决策层回胖)"
    assert n_app <= APPENDIX_WARN_BYTES, f"appendix {n_app}B > {APPENDIX_WARN_BYTES}B"


def test_bundle_content_classes_survive_the_budget(tmp_path):
    """预算达标**不等于**内容还在:逐类别锁死,把「删空也绿」这条路堵上。

    变异校验(每条各锁一类):把候选表行循环改成 `rows[:3]`、把 appendix 某一节的
    renderer 返回 `[]`、把 L3 逐票块删掉 —— 上面那条字节用例只会更绿,本条会红。
    """
    d = _fat_scan(tmp_path)
    summary, appendix, model = _render(d)

    # ① 候选/保送表**逐行**在场:行数 == finalist 数(不是"表还在"这种弱断言)
    assert len(model.genuine_rows) == 12 and len(model.pinned_rows) == 1
    assert len(_table_rows(summary, "## 候选(")) == len(model.genuine_rows), \
        "候选表行数 ≠ finalist 数(有票被静默漏渲染;字节断言这时只会更绿)"
    assert len(_table_rows(summary, "## 📌 保送持仓")) == len(model.pinned_rows)
    assert f"## 候选({len(model.genuine_rows)} 只)" in summary, "标题里的只数与表行数必须同源"
    for name in ("甲", "乙", "测试票0", "测试票9", "持仓票"):
        assert name in summary

    # ② appendix A–G 七节标题 + 七个锚全在(结构完整性,不靠删节表达 absence)
    for key, title in APPENDIX_SECTIONS:
        assert f"## {title}" in appendix, f"appendix 缺结构节 {title}"
        assert f'<a id="{APPENDIX_ANCHORS[key]}"></a>' in appendix, f"appendix 缺锚 {key}"

    # ③ 下沉的现场素材逐类在场(每类挑一个只有它有的指纹串)
    assert f"L3 逐票({len(model.rows)} 只)" in appendix
    assert "AI 光模块需求超预期" in appendix            # L3 论点全文
    assert "OW三门失守分布" in appendix                 # 门柱直方图
    assert "各阶段耗时 & 落盘字节" in appendix          # 运行遥测表
    assert "| L0 | 选集 | 4237 |" in appendix           # 漏斗数量表(真数字,不是空壳)
    for name in ("甲", "测试票9"):
        assert f"[决策卡](details/{name}.md)" in appendix, "appendix C 只列索引链接,链接不能丢"


def test_byte_budget_constants_are_the_design_numbers():
    """常量钉字面量 —— 否则「把门槛调大」也能让预算用例变绿(常量同漂型假灯)。"""
    assert (SUMMARY_TARGET_BYTES, SUMMARY_WARN_BYTES) == (12 * 1024, 16 * 1024)
    assert (APPENDIX_TARGET_BYTES, APPENDIX_WARN_BYTES) == (20 * 1024, 24 * 1024)


def test_report_sections_summary_max_bytes_is_the_warn_alias():
    """`report_sections.SUMMARY_MAX_BYTES` 必须与 `report_model` 的 warn 上限同值。

    §2 契约表:「`SUMMARY_MAX_BYTES = 38×1024` … 本稿改 16 KB」;§6.10 只承认四个预算常量。
    两个同名常量各持一个值(`report_sections` 38KB vs `report_model` 16KB)=「配置双事实源」,
    下一个人 `from report_sections import SUMMARY_MAX_BYTES` 会拿到一个作废了的门槛。
    """
    assert rs.SUMMARY_MAX_BYTES == SUMMARY_WARN_BYTES, (
        f"report_sections.SUMMARY_MAX_BYTES={rs.SUMMARY_MAX_BYTES} 仍是旧 38KB 门槛,"
        f"report_model 侧已是 {SUMMARY_WARN_BYTES}B —— 同名常量两个值")


# ─────────────── I-5:两个门柱生产者不再同屏,标注随块下沉 ───────────────

def test_gate_histogram_declares_its_basis(pair):
    """门柱直方图(`gate_status` 解析卡片自由文本)整块下沉 appendix D,标注随行(§4.1 节 6)。

    旧断言锁的是「summary 里同屏两个门柱数、各自打标」;新契约更硬 —— **决策层只留结构化
    那一侧**,自由文本那份连同两口径说明一起进附录,同屏打架的可能性从根上去掉。
    """
    summary, appendix, _ = pair

    assert "OW三门失守分布" in appendix
    assert rs.GATE_HIST_BASIS_NOTE in appendix, "门柱行缺口径标注"
    assert "以结构化那侧为准" in appendix
    assert appendix.index("OW三门失守分布") < appendix.index(rs.GATE_HIST_BASIS_NOTE)
    assert "## D. 门柱与资格" in appendix
    assert appendix.index("## D. 门柱与资格") < appendix.index("OW三门失守分布")

    assert "OW三门失守分布" not in summary, "自由文本口径的直方图回流决策层 = 两个数又同屏了"
    assert rs.GATE_HIST_BASIS_NOTE not in summary


def test_gate_hist_basis_note_pins_both_producer_names():
    """标注必须点名两个生产者,否则读者不知道「另一个数」在哪、为什么不同。"""
    note = rs.GATE_HIST_BASIS_NOTE
    assert "gate_status" in note and "decision_records.gate_states" in note


# ─────────────── M-12:任务书 ④ 点名的保留件补断言 ───────────────

def test_observation_anchor_survives_for_cost_section(md):
    """④「💸 成本观测」由 `post_run.inject_run_observation_section` 注在 `## 诚实局限` 之前;
    重排必须保住那个锚(锚没了 → 成本节会被追到文末、脱离上下文)。"""
    assert "\n## 诚实局限" in md
    from autoresearch.scan.post_run import inject_run_observation_section
    out = inject_run_observation_section(md, "## 💸 成本与时延观测\n\n- 计量:UNMEASURED")
    assert out.index("💸 成本与时延观测") < out.index("## 诚实局限")


# ═══════════ 新增(§7 鉴别力):语义口径链接 / appendix 骨架 / 文风 / 事实归属 ═══════════

def test_every_appendix_anchor_used_by_summary_lands_in_the_appendix(pair):
    """§6.9:summary 里每一个 `appendix.md#…` 锚都必须能在 appendix 命中。

    变异校验:改掉 `report_model.APPENDIX_ANCHORS`/`METHOD_ANCHORS` 任一侧、或在 appendix
    里换个 `<a id>` 写法,本条立刻变红 —— 「链接指向不存在的锚」在渲染期不会报任何错。
    """
    summary, appendix, _ = pair
    anchors = set(re.findall(r"appendix\.md#([A-Za-z0-9_-]+)", summary))

    assert anchors, "summary 一个 appendix 链接都没有 = 语义链接根本没接线"
    for a in sorted(anchors):
        tag = f'<a id="{a}"></a>'
        assert tag in appendix, f"summary 链到了 appendix 里不存在的锚 #{a}"
        # 锚必须**贴着标题**:光有个 <a id> 浮在正文里,点过去落在半截段落上
        after = appendix.split(tag, 1)[1].lstrip("\n").splitlines()
        assert after and after[0].lstrip().startswith("#"), \
            f"锚 #{a} 后面不是标题行(点进去落不到节上):{after[:1]}"


def test_method_anchors_do_not_shift_when_footnotes_are_reordered(monkeypatch, pair):
    """§6.9:口径锚是**语义 key**,不是按出现顺序分配的编号 —— 节序一变编号就漂移。

    把 `FOOTNOTES` 整个倒序重渲:锚集合必须逐字不变(只有段落顺序变)。若谁改成
    `[口径1]`/`#footnote-1` 这类位置编号,本条立刻变红。
    """
    _summary, appendix, model = pair
    before = set(re.findall(r'<a id="(method-[A-Za-z0-9_-]+)"></a>', appendix))
    assert before == set(METHOD_ANCHORS.values()), "F 节锚与 METHOD_ANCHORS 单一事实源不同步"

    import autoresearch.scan.report_appendix as ra
    monkeypatch.setattr(ra, "FOOTNOTES", tuple(reversed(ra.FOOTNOTES)))
    after_text = render_appendix(model)
    after = set(re.findall(r'<a id="(method-[A-Za-z0-9_-]+)"></a>', after_text))

    assert after == before, "口径锚随 FOOTNOTES 顺序漂了 = 按出现顺序编号"
    assert after_text != appendix, "倒序后一个字节都没变?说明这条探针根本没作用到渲染上"


def test_summary_carries_no_inline_caption_words(tmp_path):
    """§4.3 文风 3:决策层正文不出现「口径:」「注:」「由来:」—— 注文集中 appendix F。

    这条同时是**门柱直方图回流探针**:`GATE_HIST_BASIS_NOTE` 以「_口径:」开头,谁把它
    搬回 summary,本条马上红。
    """
    d = _scan(tmp_path)
    (d / "market_view.md").write_text(
        "# 市场研判 — 2026-07-03\n\n"
        "1. **一句话定调**:震荡磨底。\n"
        "2. **市场结构**:宽度 44.67% 站上 MA60。\n"
        "3. **板块红黑榜**:强侧贵金属 +16.10%。\n"
        "4. **操作基调**:总仓位三到五成。\n"
        "5. **关注**:中报收官周。\n"
        "6. 仅供研究,非投资建议。\n", encoding="utf-8")
    summary, appendix, _ = _render(d)

    for word in ("口径:", "注:", "由来:"):
        assert word not in summary, f"决策层出现夹注「{word}」——注文只进 appendix F"
    assert "口径:" in appendix, "夹注下沉后 appendix 一个都没有 = 注文被删掉了,不是下沉"
    assert "## F. 方法与口径" in appendix


def test_appendix_skeleton_is_always_present_even_with_no_material(tmp_path):
    """§4.2:A–G 骨架恒在;条件素材缺席印 `无 / NOT_EXPECTED`,**不靠删整节表达 absence**。

    否则「appendix 缺任一节」这条检查会把 sentinel / 无 Tier-3 的合法缺席误报成丢件。
    """
    d = tmp_path / "empty"
    d.mkdir()
    (d / "meta.json").write_text("{}", encoding="utf-8")
    (d / "finalists.csv").write_text("code,name,sector\n", encoding="utf-8")

    _summary, appendix, _ = _render(d)

    for key, title in APPENDIX_SECTIONS:
        assert f"## {title}" in appendix, f"素材全缺时 appendix 少了结构节 {title}"
        assert f'<a id="{APPENDIX_ANCHORS[key]}"></a>' in appendix
    assert ABSENT in appendix, "缺席素材必须显式印 `无 / NOT_EXPECTED`(空串分不清没有/没渲染)"
    # G 节恒定文本(诚实局限三条全文)只在附录,不在决策层
    from autoresearch.scan.report_appendix import HONEST_LIMITATIONS
    assert HONEST_LIMITATIONS[1] in appendix
    assert HONEST_LIMITATIONS[1] not in _summary


# ── 事实归属 mutation(§4.1.1;删掉任一去重约束 → 本节变红)────────────────────

def test_regime_and_temperature_are_not_reprinted_in_summary(tmp_path):
    """regime / 温度的唯一展开点 = 🧭 仪表盘①。模型里有这两行,渲染层**必须不印**。

    变异校验:在 `render_summary` 里加回 `out += [model.regime_line, model.temp_line]`
    (旧 22 节里 H1 下面那两行),本条立刻变红。
    """
    d = _scan(tmp_path)
    model = rs.prepare_report_model(d, _D, "1200", _F)
    model = dataclasses.replace(
        model,
        regime_line="**市场 regime**:range(MUTATION_REGIME_MARK)",
        temp_line="🌡 市场温度 24(退潮 MUTATION_TEMP_MARK)")

    out = rs.render_summary(model)

    assert "MUTATION_REGIME_MARK" not in out, "regime 行回流决策层 = 定调被印第二次"
    assert "MUTATION_TEMP_MARK" not in out, "温度行回流决策层 = 同一事实印两次"


def test_buy_and_blocked_conclusions_never_leak_into_render_summary(tmp_path):
    """BUY / BLOCKED / 0买**结论**归仪表盘③;节 6 只出统计(早停分桶 + 三门 ✗ 计数)。

    fixture 里故意躺一份 `_relative_buy_decision.json`(blocked + 一只 BUY)。`render_summary`
    是纯函数、不读盘,所以它的任何字样出现在正文 = 有人把这份文件接回了渲染层 —— 那正是
    2026-08-19「8 份 brief 里 4 份读到前一日决策文件」的病灶形状。
    """
    d = _scan(tmp_path)
    (d / "_relative_buy_decision.json").write_text(json.dumps({
        "schema_version": 1, "mode": "active", "date": _D,
        "buys": [{"code": "300476", "basis": "relative", "rank": 1}],
        "blocked": True, "blocked_reasons": ["MUTATION_BLOCK_REASON"], "counts": {},
    }, ensure_ascii=False), encoding="utf-8")

    summary, _appendix, _model = _render(d)

    assert "BLOCKED" not in summary, "BLOCKED 结论泄漏进决策层正文(唯一展开点=仪表盘③)"
    assert "MUTATION_BLOCK_REASON" not in summary
    assert "BUY(相对决策层)" not in summary
    # 节 6 本体仍在(把统计一起删掉不算"没泄漏")
    assert "## 为什么没有 BUY" in summary
    assert "满卡 3 张三门 ✗" in summary


def test_pinned_holdings_are_a_separate_table_not_merged(pair):
    """持仓逐票评级/依据的唯一展开点 = 📌 保送表(仪表盘④ 只给总动作)。

    保送票**不得**混进候选表 —— 混表会让「候选 N 只」这个数把保送票也算进去。
    """
    summary, _appendix, model = pair
    cand = _table_rows(summary, "## 候选(")
    pinned = _table_rows(summary, "## 📌 保送持仓")

    assert len(cand) == len(model.genuine_rows) == 2, "候选表行数与 genuine 人口对不上"
    assert all("持仓票" not in ln for ln in cand), "📌 保送票混进了候选表"
    assert len(pinned) == 1 and "持仓票" in pinned[0]
    assert "保送理由" in summary, "保送表的独有列(保送理由)丢了 = 两张表被并成一张"
