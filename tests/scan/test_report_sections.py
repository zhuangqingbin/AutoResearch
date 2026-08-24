"""summary.md 重排回归(Wave12 T26 / 批C C2)——**减层不减料**。

四条契约(任务书 Step 1):
  ① 节序:决策仪表盘 → 投资建议 → 保送持仓 → 差一点/弃权 banner **前置**
  ② 行业研判节 = 每行业**一行**地形首句 + 指向 `trace/sector_briefs/<行业>.md` 的链接,
     研判段全文不再嵌入(节字节上限 2,000B;文件本就单独发布在 trace/)
  ③ 经验/未决反馈节 = 表格化(id / 一句话 / guard 状态)
  ④ 诚实局限、成本观测原样保留(`near_miss` 附录与「旧 OW 基率」分账行已随 2026-08-21 闭环退役删除)
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
    for background in ("## 1. 漏斗(数量)", "## 2. 各阶段卡点"):
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


# ───────────────────────────── ② 行业研判节(D6 已整段退役)─────────────────────────────
#
# `_sector_view_section` 与其字节预算机制(`SECTOR_SECTION_MAX_BYTES`/`_terrain_gist`)已随
# 2026-08-19 D6(⚖A6,用户裁定)整段删除:brief 研判段本身被砍除,summary 不再有任何行业
# 研判节,行业方向叙事完全由确定性 top3(`render_sector_top3`)独扛。原三条用例(逐行链接/
# 字节预算鉴别力/presence-gated 空态)测的对象已不存在,随之摘除,不留占位测试。

# ───────────────────────────── ③ 经验节表格化 ─────────────────────────────

# ───────────────────────────── ④ 保留件 + 旧 OW 基率分账行 ─────────────────────────────

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
    """T26 之前的行业节:每行业**原文嵌研判段全文**。

    `brief.py` 的 `extract_view`/`parse_direction` 已随 D6(⚖A6)退役——研判段本身
    被整段砍除,现行代码不再有任何函数产出这类内容。这里就地内联等价的历史抽取逻辑
    (纯字符串处理,不依赖已删除的符号),只为下面 ①「旧口径确实撑破 38KB」的鉴别力
    证明服务,不代表当前契约。
    """
    import re as _re
    _dir_re = _re.compile(r"\*\*行业方向\*\*\s*[::]\s*(看多|中性|看空)")

    def _old_extract_view(text: str) -> str:
        out, on = [], False
        for ln in text.splitlines():
            if ln.strip().startswith("## 研判段"):
                on = True
                continue
            if on and ln.startswith("## "):
                break
            if on:
                out.append(ln)
        return "\n".join(out).strip()

    parts = []
    for p in sorted((scan_dir / "sector_briefs").glob("*.md")):
        view = _old_extract_view(p.read_text(encoding="utf-8"))
        if view:
            m = _dir_re.search(view)
            parts.append(f"**{p.stem}**(方向:{m.group(1) if m else '—'})\n\n{view}")
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


def test_summary_total_bytes_regression_lock(tmp_path):
    """T26 Step 2 硬验收落成断言:summary 总字节 ≤ `rs.SUMMARY_MAX_BYTES`(38KB)。

    **鉴别力现状要说清楚**(2026-08-21 learning 层退役):原来这条用例带一个 ① 鉴别力探针
    ——「同一份 fixture 用旧口径渲染必须真的撑破 38KB」,而旧口径的胖肉主要来自经验/未决
    反馈节(12 条 lesson × 整段 rule 原文)。那一节已随 `feedback_store` 整节删除,探针剩下
    的行业节只有 22KB,压不到门槛 —— 继续留着它就是个恒失败的假探针,故删。**② 契约本体
    仍是真锁**:summary 涨过 38KB 这条用例照样变红。原 ③「减层不减料」断言的对象(12 条
    lesson / 9 条反馈全在场)随该节一并作废。
    """
    d = _fat_scan(tmp_path)
    md_new = build_summary(d, _D, "1200", _F)
    assert len(md_new.encode("utf-8")) <= rs.SUMMARY_MAX_BYTES, \
        f"summary {len(md_new.encode('utf-8'))}B > {rs.SUMMARY_MAX_BYTES}B(T26 交付量回退)"


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

def test_observation_anchor_survives_for_cost_section(md):
    """④「💸 成本观测」由 `post_run.inject_run_observation_section` 注在 `## 诚实局限` 之前;
    重排必须保住那个锚(锚没了 → 成本节会被追到文末、脱离上下文)。"""
    assert "\n## 诚实局限" in md
    from autoresearch.scan.post_run import inject_run_observation_section
    out = inject_run_observation_section(md, "## 💸 成本与时延观测\n\n- 计量:UNMEASURED")
    assert out.index("💸 成本与时延观测") < out.index("## 诚实局限")
