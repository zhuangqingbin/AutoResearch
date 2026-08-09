"""summary.md 重排回归(Wave12 T26 / 批C C2)——**减层不减料**。

四条契约(任务书 Step 1):
  ① 节序:决策仪表盘 → 投资建议 → 保送持仓 → 差一点/弃权 banner **前置**
  ② 行业研判节 = 每行业**一行**地形首句 + 指向 `trace/sector_briefs/<行业>.md` 的链接,
     研判段全文不再嵌入(节字节上限 2,000B;文件本就单独发布在 trace/)
  ③ 经验/未决反馈节 = 表格化(id / 一句话 / guard 状态)
  ④ `near_miss` 附录、诚实局限、成本观测原样保留;组合视角节增「旧 OW 基率」分账行
     (与 brief ③ 同源渲染,定义断层不连线)

合成 fixture,零网络;所有产物落 tmp_path。
"""
from __future__ import annotations

import csv
import json

import pandas as pd
import pytest

from autoresearch.scan import report_sections as rs
from autoresearch.scan.assemble import build_summary

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


def _section(md: str, header: str) -> str:
    i = md.find(header)
    if i < 0:
        return ""
    j = md.find("\n## ", i + len(header))
    return md[i:j] if j != -1 else md[i:]


@pytest.fixture
def md(tmp_path):
    return build_summary(_scan(tmp_path), _D, "1200", _F)


# ───────────────────────────── ① 节序:决策主线前置 ─────────────────────────────

def test_decision_line_comes_before_context_sections(md):
    """决策主线(仪表盘→投资建议→保送持仓→差一点/弃权)必须排在**背景节之前**。"""
    order = [rs.DASHBOARD_HEADER, "## 3. 投资建议", "## 📌 保送持仓"]
    idx = [md.find(x) for x in order]
    assert all(i >= 0 for i in idx), f"节缺失:{dict(zip(order, idx))}"
    assert idx == sorted(idx), f"决策主线节序错:{dict(zip(order, idx))}"
    for background in ("## 🏭 行业研判", "## 1. 漏斗(数量)", "## 2. 各阶段卡点"):
        assert md.find(background) > idx[-1], f"{background} 应排在决策主线之后"


def test_dashboard_placeholder_is_managed_block(md):
    """仪表盘是 managed 块:assemble 落占位,publisher 收尾用 brief 同源事实注入。"""
    assert rs.DASHBOARD_START in md and rs.DASHBOARD_END in md
    assert md.index(rs.DASHBOARD_START) < md.index(rs.DASHBOARD_END)


def test_inject_dashboard_replaces_managed_block(md):
    out = rs.inject_dashboard(md, "**① 市场**:震荡\n**③ 结论**\n- 生产 BUY 0 只")
    assert "**① 市场**:震荡" in out
    assert "此处为占位" not in out, "注入后不应留占位文案"
    assert rs.DASHBOARD_START in out and rs.DASHBOARD_END in out
    assert rs.inject_dashboard(out, "**① 市场**:趋势").count(rs.DASHBOARD_START) == 1


# ───────────────────────────── ② 行业研判降级 ─────────────────────────────

def test_sector_section_is_one_row_per_industry_with_link(tmp_path):
    d = _scan(tmp_path, n_industries=3)
    sec = rs._sector_view_section(d)
    assert sec.startswith("## 🏭 行业研判")
    for i in range(3):
        assert f"测试行业{i}" in sec
        assert f"trace/sector_briefs/测试行业{i}.md" in sec, "必须给出原文链接(减层不减料)"
    assert "最大证伪点" not in sec, "研判段全文不得再嵌入 summary"
    assert "格局与表达" not in sec
    assert "看多" in sec, "行业方向(一个字段)仍留在 summary"
    body = [ln for ln in sec.splitlines() if ln.startswith("| 测试行业")]
    assert len(body) == 3, "每行业恰一行"


def test_sector_section_byte_cap_beats_raw_embed(tmp_path):
    """**有鉴别力**:先证明旧口径(原文嵌研判段)确实 >2,000B,再断言新节 ≤2,000B。"""
    d = _scan(tmp_path, n_industries=8)
    raw = sum(len(p.read_bytes()) for p in sorted((d / "sector_briefs").glob("*.md")))
    assert raw > rs.SECTOR_SECTION_MAX_BYTES, f"探针失效:原文只有 {raw}B"
    sec = rs._sector_view_section(d)
    assert len(sec.encode("utf-8")) <= rs.SECTOR_SECTION_MAX_BYTES, \
        f"行业节 {len(sec.encode('utf-8'))}B > {rs.SECTOR_SECTION_MAX_BYTES}B"
    assert sec.count("\n| 测试行业") == 8, "8 个行业一个都不能少(减层不减料)"


def test_sector_section_absent_without_briefs(tmp_path):
    d = _scan(tmp_path, n_industries=0)
    assert rs._sector_view_section(d) == ""     # presence-gated,老路不破


# ───────────────────────────── ③ 经验节表格化 ─────────────────────────────

def test_knowledge_note_is_a_table_with_id_and_guard(monkeypatch):
    lessons = [
        {"id": "ls_alpha", "scope": {"kind": "global", "value": "*"}, "confidence": 0.87,
         "rule": "【勘误】漏斗对深跌票有逐级收紧的拒绝梯度;越往下游被拒的越是纯接刀。\n"
                 "后面还有很长很长的第二段第三段,旧版把整段原文都倒进 summary。",
         "guard": {"field": "winner_rate", "op": ">", "value": 90},
         "mtm": {"support": 9, "refute": 0}},
        {"id": "ls_beta", "scope": {"kind": "industry", "value": "银行Ⅱ"},
         "confidence": 0.2, "rule": "用户明确不想要下跌趋势的票。",
         "guard_na_reason": "偏好类,无数值判据", "mtm": {"support": 1, "refute": 2}},
    ]
    monkeypatch.setattr(rs, "_lessons_and_open_feedback",
                        lambda rows: (lessons, [{"verdict": "process", "note": "n",
                                                 "id": "fb_1"}]))
    note = rs._knowledge_note([{"code": "300476", "sector": "元件"}])
    assert note.startswith("## 📌 经验 / 未决反馈")
    assert "| lesson | 一句话 | guard | conf | MTM |" in note
    assert "| `ls_alpha` |" in note and "| `ls_beta` |" in note
    assert "winner_rate>90" in note, "guard 状态必须落表(它是硬门的真身)"
    assert "9/0" in note, "MTM support/refute 是降级依据,不能丢"
    assert "后面还有很长很长的第二段" not in note, "全文留 context/knowledge/,不再进 summary"
    assert "context/knowledge" in note, "必须告诉读者全文在哪(减层不减料)"
    assert "漏斗对深跌票有逐级收紧" in note, "【勘误】旁注剥掉后必须还能取到规则正文"


def test_gist_survives_a_whole_line_of_annotation():
    """真数据形状:整个首行都是【…勘误…】旁注,规则正文在第二行。剥块不当 → 单元格空成 '—'。"""
    rule = ("【2026-07-15 机制勘误 —— 原文把病因记成「L2 是动量训练的 GBDT champion」,"
            "该模块已于 2026-07-13 整簇删除。现象仍在,归因需改口。】\n"
            "漏斗对「深跌 + 主力净出」的超卖反转票有逐级收紧的拒绝梯度。\n第三行不该被取到。")
    gist = rs._gist(rule)
    assert gist != "—" and "漏斗对" in gist
    assert "机制勘误" not in gist and "第三行" not in gist


def test_knowledge_note_empty_store_is_still_silent(monkeypatch):
    monkeypatch.setattr(rs, "_lessons_and_open_feedback", lambda rows: ([], []))
    assert rs._knowledge_note([{"code": "300476"}]) == ""


# ───────────────────────────── ④ 保留件 + 旧 OW 基率分账行 ─────────────────────────────

def test_ow_base_line_is_split_account(tmp_path, monkeypatch):
    monkeypatch.setattr(rs, "_ow_base_rate_for",
                        lambda _root: {"n": 9, "n_realized": 3, "win2": 0.0,
                                       "mean2": -0.007})
    md = build_summary(_scan(tmp_path), _D, "1200", _F)
    line = next(ln for ln in md.splitlines() if "旧 OW 基率" in ln)
    assert "9 笔" in line and "0%" in line
    assert "定义断层" in line and "不连线" in line
    assert md.find("旧 OW 基率") > md.find("### 组合视角")


def test_preserved_sections_survive_the_reorder(md):
    for anchor in ("## 诚实局限", "各阶段耗时 & 落盘字节", "精排(L3)入选",
                   "### 组合视角", "OW三门失守分布", "## 2. 各阶段卡点"):
        assert anchor in md, f"重排丢了 {anchor}(减层不减料被违反)"


def test_no_content_class_is_dropped(tmp_path):
    """减层不减料的直接断言:重排后 §2/§3 的每个 finalist 仍逐只在场。"""
    d = _scan(tmp_path)
    md = build_summary(d, _D, "1200", _F)
    for name in ("甲", "乙", "持仓票"):
        assert name in md


# ─────────────── I-3:summary 总字节回归锁(T26 的**全部产出理由**) ───────────────
#
# 第一版把 47,814B → 30,951B 只写在报告里、没有任何测试守着 —— 下一个人加一节顶回 47KB,
# 2300 条测试全绿。下面这条把它焊成断言,并且**自带鉴别力证明**:同一份 fixture 先用
# 「旧口径」(行业节嵌研判段全文 + 经验节倒 rule 原文)渲染一次,断言它确实 >38KB,
# 再断言现口径 ≤38KB。只断言后半句的话,合成盘天然只有几 KB,这条会是恒绿的假灯。

def _old_style_sector_section(scan_dir) -> str:
    """T26 之前的行业节:每行业**原文嵌研判段全文**。"""
    from autoresearch.sector.brief import extract_view, parse_direction
    parts = []
    for p in sorted((scan_dir / "sector_briefs").glob("*.md")):
        view = extract_view(p.read_text(encoding="utf-8"))
        if view:
            parts.append(f"**{p.stem}**(方向:{parse_direction(view) or '—'})\n\n{view}")
    return "## 🏭 行业研判(sector-research lite · 仅整合层)\n\n" + "\n\n".join(parts)


def _old_style_knowledge_note(rows) -> str:
    """T26 之前的经验节:逐条倒 `rule` **整段原文**。"""
    lessons, open_fb = rs._lessons_and_open_feedback(rows)
    lines = ["## 📌 经验 / 未决反馈(闭环记忆)", "**生效经验**:"]
    lines += [f"- {lsn['rule']}  _(conf {lsn.get('confidence', 0):.2f})_" for lsn in lessons]
    lines += ["**未决反馈**:"] + [f"- ({f.get('verdict')}) {f.get('note', '')}" for f in open_fb]
    return "\n".join(lines) + "\n"


_FAT_LESSON = ("【2026-07-15 机制勘误 —— 原文把病因记成「L2 是动量训练的 GBDT champion」,"
               "该模块已于 2026-07-13 整簇删除,L2 现为确定性分层采样器。现象仍在,归因需改口。】\n"
               + "漏斗对「深跌 + 主力净出」的超卖反转票有逐级收紧的拒绝梯度,"
                 "且梯度是判据叠加的结果而非某一个模块。" * 12)


def _fat_scan(tmp_path):
    """按真 08-06 run 的量级造:8 个行业 brief + 12 条长 lesson + 12 只 finalist。"""
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


def test_summary_total_bytes_regression_lock(tmp_path, monkeypatch):
    """T26 Step 2 硬验收落成断言:summary 总字节 ≤ `rs.SUMMARY_MAX_BYTES`(38KB)。"""
    lessons = [{"id": f"ls_{i}", "scope": {"kind": "global", "value": "*"},
                "confidence": 0.8, "rule": _FAT_LESSON, "mtm": {"support": 9, "refute": 0}}
               for i in range(12)]
    fb = [{"id": f"fb_{i}", "verdict": "process", "note": "用户反馈" * 30} for i in range(9)]
    monkeypatch.setattr(rs, "_lessons_and_open_feedback", lambda rows: (lessons, fb))
    d = _fat_scan(tmp_path)

    # ① 鉴别力证明:同一份 fixture 用旧口径渲染,必须真的撑破 38KB
    md_new = build_summary(d, _D, "1200", _F)
    old_extra = (len(_old_style_sector_section(d).encode("utf-8"))
                 - len(rs._sector_view_section(d).encode("utf-8"))
                 + len(_old_style_knowledge_note([{"code": "300476"}]).encode("utf-8"))
                 - len(rs._knowledge_note([{"code": "300476"}]).encode("utf-8")))
    old_bytes = len(md_new.encode("utf-8")) + old_extra
    assert old_bytes > rs.SUMMARY_MAX_BYTES, \
        f"探针失效:旧口径只有 {old_bytes}B,压不到 {rs.SUMMARY_MAX_BYTES}B 门槛"

    # ② 契约本体
    assert len(md_new.encode("utf-8")) <= rs.SUMMARY_MAX_BYTES, \
        f"summary {len(md_new.encode('utf-8'))}B > {rs.SUMMARY_MAX_BYTES}B(T26 交付量回退)"
    # ③ 减层不减料:12 条 lesson / 9 条反馈 / 8 个行业一条不少
    for i in range(12):
        assert f"`ls_{i}`" in md_new
    for i in range(9):
        assert f"`fb_{i}`" in md_new
    for i in range(8):
        assert f"测试行业{i}" in md_new


def test_summary_max_bytes_is_the_task_book_number():
    """常量钉字面量 —— 否则「把门槛调大」也能让上面那条变绿(常量同漂型假灯)。"""
    assert rs.SUMMARY_MAX_BYTES == 38 * 1024


# ─────────────── I-5:两个门柱生产者同屏必须各自打标 ───────────────

def test_gate_histogram_declares_its_basis(md):
    """summary 的门柱行由 `gate_status` 解析卡片自由文本,与 🧭 仪表盘 ③ 的结构化读数
    **不是同一个数**。同屏不打标 = 读者随机相信一个。"""
    assert "OW三门失守分布" in md
    assert rs.GATE_HIST_BASIS_NOTE in md, "门柱行缺口径标注"
    assert "以结构化那侧为准" in md
    assert md.index("OW三门失守分布") < md.index(rs.GATE_HIST_BASIS_NOTE)


def test_gate_hist_basis_note_pins_both_producer_names():
    """标注必须点名两个生产者,否则读者不知道「另一个数」在哪、为什么不同。"""
    note = rs.GATE_HIST_BASIS_NOTE
    assert "gate_status" in note and "decision_records.gate_states" in note


# ─────────────── M-12:任务书 ④ 点名的保留件补断言 ───────────────

def test_near_miss_banner_is_front_loaded_and_appendix_stays_at_tail(tmp_path, monkeypatch):
    """①「差一点/弃权 banner 前置」+ ④「near_miss 附录原样保留」。

    banner 必须排在背景节(📈 市场)**之前**,逐只附录仍留在文末 —— §R6:只给个案不给分母
    会把读者推向绕门,两者的视觉层级不能合并。
    """
    from autoresearch.scan import near_miss
    facts = near_miss.NearMissFacts(
        date=_D, is_zero_buy=True,
        shadow=[{"code": "300476", "name": "甲", "conviction": 72,
                 "binding": ["主力真在"], "close": 5.2}],
        gate_counts={"主力真在": 1, "业绩真兑现": 0, "估值不透支": 0},
        gate_history={}, abstention=None, as_of_days=[_D])
    monkeypatch.setattr(near_miss, "build", lambda *a, **k: facts)
    md = build_summary(_scan(tmp_path), _D, "1200", _F)
    banner_at, appendix_at = md.find("🎯 差一点"), md.find("🕯️ 影子观察附录")
    assert banner_at > 0 and appendix_at > 0, "差一点 banner / 影子附录 丢了"
    # 锚取必然在场的背景节(`## 📈 今日 A 股市场` 在合成盘 presence-gated 不出,拿它当锚
    # 会 find→-1、断言恒真,又是一个假灯)
    funnel_at = md.find("## 1. 漏斗(数量)")
    assert funnel_at > 0
    assert banner_at < funnel_at, "弃权 banner 未前置到决策主线"
    assert appendix_at > md.find("## 📌 经验"), "逐只附录不该爬到决策主线"
    assert near_miss.DISCLAIMER in md, "附录的「未过门,非建议」免责被丢了"


def test_observation_anchor_survives_for_cost_section(md):
    """④「💸 成本观测」由 `post_run.inject_run_observation_section` 注在 `## 诚实局限` 之前;
    重排必须保住那个锚(锚没了 → 成本节会被追到文末、脱离上下文)。"""
    assert "\n## 诚实局限" in md
    from autoresearch.scan.post_run import inject_run_observation_section
    out = inject_run_observation_section(md, "## 💸 成本与时延观测\n\n- 计量:UNMEASURED")
    assert out.index("💸 成本与时延观测") < out.index("## 诚实局限")
