"""agent-defs lint —— `.claude/agents/` 叶子 agent 定义与源码/playbook 的最低一致性。

2026-07-05:scan 叶子 agent 化(l4-card / sector-brief;buy-skeptic 07-07 移除)——稳定人设烤进
agent system prompt(派发 prompt 缩到两行、前缀吃 cache、契约不再靠每次转述)。
本文件治两类漂移:① agent 文件缺/frontmatter 坏;② 关键**机器契约锚**(进入P4倾向 /
FINAL TRANSACTION PROPOSAL / OW三门名 / 两段标题)在 agent 定义与真值源间失同步。
"""
from __future__ import annotations

from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
AGENTS = ROOT / ".claude" / "agents"
SKILLS = ROOT / ".claude" / "skills"

_NAMES = ("l4-card", "sector-brief", "macro-brief", "l3-rank")


def _agent_text(name: str) -> str:
    p = AGENTS / f"{name}.md"
    assert p.exists(), f"缺 agent 定义:{p.relative_to(ROOT)}"
    return p.read_text(encoding="utf-8")


def test_agent_files_exist_with_frontmatter():
    """叶子 agent 定义在位:frontmatter 有 name/description/model: opus(全 Opus 设计)。"""
    for name in _NAMES:
        text = _agent_text(name)
        assert text.startswith("---"), f"{name}: 缺 frontmatter"
        head = text.split("---", 2)[1]
        assert f"name: {name}" in head, f"{name}: frontmatter name 不符"
        assert "description:" in head, f"{name}: 缺 description"
        assert "model: opus" in head, f"{name}: 应为 model: opus(scan 全 Opus 设计)"


def test_l4_card_contract_anchors_synced():
    """l4-card 与 lite-playbook 的机器契约锚一致(卡被 parse_rating/lint/stage_eval 直接读)。"""
    from autoresearch.scan.agents.l4_card import _OW_GATES  # 单一事实源
    from autoresearch.scan.self_review import _CARD_V4_MARKER  # 单一事实源(T17/T24)
    agent = _agent_text("l4-card")
    playbook = (SKILLS / "stock-research" / "lite-playbook.md").read_text(encoding="utf-8")
    anchors = ["进入P4倾向", "FINAL TRANSACTION PROPOSAL", "**Rating**",
               "断言分级", "一致预期差",
               "早停只向下", "Rubric建议", "一段话研判", "L3 论点裁决",
               "已核数字摘录", "多写不多读", "龙虎榜席位", "活体新闻",
               # 锚必须独有:"停因:" 曾被既有的 "早停因:" 整段吃掉 → 删了早停行测试照绿
               # T24:卡契约标记行 v3→v4(隔夜口径)换代,锚随之从字面量改引 self_review 的
               # 单一事实源(承 T17 docstring「标记行本体进模板是 T24 的事」的既定手法)。
               "早停卡短格式", "**早停**: 停于", _CARD_V4_MARKER, "超短口径",
               "机构面网查", "先读数据后读论点", "活体情报", "持仓管理", "档案对账",
               # Wave6:①独立初判从散文铁律升格为**卡结构元素**(07-24 实测 0/11 卡含此串,
               # chk_blind_pass 全 fail —— 指令在、检查在,缺的是机器可核的标签行);
               # ②转引标题标注(07-24 两条 price_claim_mismatch 的真身是转述媒体标题)。
               "**独立初判**:", "〔转引标题〕",
               # T18(2026-09-24):入场行(机读契约)——`**入场**:` 行前缀与规则段落
               # 「入场行」小节名必须两边同步(E6 v4 A/R 分级的机读依据)。
               "**入场**:", "入场行",
               # T18 fix round 3:Hold 未过门时"条件 vs 禁止"二选一的判据(指得出近端
               # 前置条件 → 条件;论点本身负 → 禁止)——两个值下游分级不同(条件仍可
               # 当 R 级买、禁止硬否决),judgment 必须两边同步,不能只改一侧。
               "论点本身负",
               *(g for g in _OW_GATES)]
    for a in anchors:
        assert a in agent, f"l4-card 缺契约锚「{a}」"
        assert a in playbook, f"lite-playbook 缺契约锚「{a}」(真值源被改,先同步 agent 定义)"


def test_l4_card_v4_marker_full_line_byte_identical():
    """v4 标记行的『说明句』尾段此前只被前缀 `_CARD_V4_MARKER` 锚定(见上一测试)——
    self_review.card_v4_marker_lint 只做前缀子串匹配,「目标带与 EV 均指 T+2 开盘;止损=
    T+1 尾盘入场否决条件;开盘预案三分支……」这段尾巴从未被逐字节回归锁,两模板未来各自
    漂移不会被任何测试察觉(Task 24 review 追加发现的缝)。

    这里把「前缀 + 说明句,到行尾为止」整行纳入比对:①两文件必须逐字节相同;②每个文件内部
    两处出现(早停卡/满卡模板)也必须彼此相同——用 `set()` 去重后长度须为 1,不是只挑一次
    出现比对(否则一个文件内部两处早停卡/满卡说明句先各自漂移,这条测试也不会红)。
    """
    import re

    from autoresearch.scan.self_review import _CARD_V4_MARKER

    agent = _agent_text("l4-card")
    playbook = (SKILLS / "stock-research" / "lite-playbook.md").read_text(encoding="utf-8")
    pat = re.compile(re.escape(_CARD_V4_MARKER) + r".*")
    agent_lines = set(pat.findall(agent))
    playbook_lines = set(pat.findall(playbook))
    assert agent_lines, "l4-card 未找到 v4 标记行"
    assert playbook_lines, "lite-playbook 未找到 v4 标记行"
    assert len(agent_lines) == 1, f"l4-card 内两处标记行说明句不一致:{agent_lines}"
    assert len(playbook_lines) == 1, f"lite-playbook 内两处标记行说明句不一致:{playbook_lines}"
    assert agent_lines == playbook_lines, (
        f"两文件标记行说明句不一致:l4-card={agent_lines!r} ≠ playbook={playbook_lines!r}")


def test_l4_card_research_body_anchors_synced():
    """研报体段名/缺档声明与 `product_shape_lint` 探针 10 同源(Wave9 B-3)。

    探针 10 用子串判"卡里有没有研报体/缺档声明";子串在 `self_review.py` 改名而
    `l4-card.md` 模板不同步改 = 静默脱钩——agent 照旧写新名字的段,lint 永远读不到、
    天天误报"缺失"(product_shape_lint 探针 10 docstring 已述同一条缝)。只对
    l4-card.md 检查,不比照 lite-playbook.md ——研报体是 scan L4 专属(依赖档案四节
    全文内联注入,只在 `autoresearch/scan/l4/prompts.py`/`context.py` 接线),不是
    stock-research lite 独立单票用法的通用契约,两份 playbook 本就该在这一点上分叉。
    """
    from autoresearch.scan.self_review import (
        _MICRO_REPORT_HDR,
        _NO_DOSSIER_DECL,
        _RESEARCH_BODY_HDR,
    )
    agent = _agent_text("l4-card")
    for a in (_RESEARCH_BODY_HDR, _MICRO_REPORT_HDR, _NO_DOSSIER_DECL):
        assert a in agent, f"l4-card 缺研报体契约锚「{a}」(与 product_shape_lint 探针10 脱钩)"


def test_l3_rank_anchors_present():
    """l3-rank 契约锚:T+2 兑现机制维 + conviction 行为化重锚(≥70 限额)+ mechanism 输出字段
    + finalist tier 语义(finalist/bench 二分、≥75 误杀保险、宁缺毋滥不凑数)。

    l3-rank 无 lite-playbook 式的独立真值源(screening-playbook 已退役),故只做单文件
    存在性检查,不做双侧同步(与 test_l4_card_contract_anchors_synced 的双文件模式不同)。
    """
    agent = _agent_text("l3-rank")
    for a in ("兑现机制", "≥70", "mechanism", "finalist", "bench", "≥75", "宁缺毋滥",
              "lowturn", "低位转强"):     # 2026-08-21:B 条例外 + G 条席位 + lane 枚举
        assert a in agent, f"l3-rank 缺契约锚「{a}」"


def test_sector_brief_anchors_synced():
    """sector-brief 单段标题与 brief.py 机器契约同源(extract_terrain)。

    2026-08-19 D6(⚖A6,用户裁定):研判段整段砍除,`VIEW_HDR`/`**行业方向**` 已随之
    退役(brief 不再判断方向)——契约锚收窄为只剩地形段标题。
    """
    from autoresearch.sector.brief import TERRAIN_HDR  # 单一事实源
    agent = _agent_text("sector-brief")
    for a in (TERRAIN_HDR, "不编", "实时网查"):
        assert a in agent, f"sector-brief 缺契约锚「{a}」"
    assert "WebSearch" in agent.split("---", 2)[1], "sector-brief frontmatter 缺 WebSearch tool"
    playbook = (SKILLS / "sector-research" / "sector-playbook.md").read_text(encoding="utf-8")
    assert "实时网查" in playbook, "sector-playbook lite 段缺实时网查 note(agent↔真值源漂移)"


def test_skill_docs_wire_agent_types():
    """scan SKILL/STAGES 派发口径指向 agent 类型(防"agent 建了没人用"的接线漂移)。"""
    skill = (SKILLS / "scan-market" / "SKILL.md").read_text(encoding="utf-8")
    stages = (SKILLS / "scan-market" / "STAGES.md").read_text(encoding="utf-8")
    for name in _NAMES:
        assert name in skill or name in stages, f"scan 文档未接线 agent 类型「{name}」"


def test_macro_brief_anchors_synced():
    """macro-brief 六小节标题 + 防锚定铁律与 macro-playbook 末节(市场研判 lite)同源。"""
    agent = _agent_text("macro-brief")
    playbook = (SKILLS / "macro-research" / "macro-playbook.md").read_text(encoding="utf-8")
    anchors = ["一句话定调", "市场结构", "板块红黑榜", "操作基调",
               "描述性地形", "不锚定卡片"]
    for a in anchors:
        assert a in agent, f"macro-brief 缺契约锚「{a}」"
        assert a in playbook, f"macro-playbook 缺契约锚「{a}」(真值源被改,先同步 agent 定义)"
    assert "实时网查" in agent, "macro-brief 缺契约锚「实时网查」"
    assert "实时网查" in playbook, "macro-playbook lite 段缺实时网查 note(agent↔真值源漂移)"
    assert "WebSearch" in agent.split("---", 2)[1], "macro-brief frontmatter 缺 WebSearch tool"


def test_l4_intel_def():
    """l4-intel:sonnet·max 盲搜情报员;**结构性盲=工具级**(无 Read/Grep/Glob);六面契约锚在位。

    Wave3.5:档案已知底改由派发 prompt **内嵌摘要文本**提供(内嵌代替授权)——
    盲性回到工具级保证,不再靠"授权 Read + 人设自觉"(同目录躺着 _l3_table.md)。
    """
    text = _agent_text("l4-intel")
    head = text.split("---", 2)[1]
    assert "model: sonnet" in head and "effort: max" in head
    assert "WebSearch" in head and "WebFetch" in head and "Write" in head
    for banned in ("Read", "Grep", "Glob"):
        assert banned not in head, f"结构性盲:不得有 {banned}(可读/探索仓库)"
    # Wave10 A5:cap 不再写死在角色里(此前 `≤15` 与当日配置 20 是**两个事实源**)。
    # 锚随之从具体数字换成「唯一事实源」这件事本身 —— 锚的作用是钉住契约,不是钉住某个数。
    assert "≤15" not in text and "≤ 15" not in text, "角色不得再硬写 cap"
    for a in ("事件段", "题材段", "机构段", "互动段", "负面增量段", "声明行",
              "as-of", "六面全查", "runtime cap", "user_config_echo",
              "净分", "只报本票事实", "只攒料不判断", "不编", "盲",
              "已知底",
              # Wave6 Q1:①来源必须是可点击链接 —— 旧铁律只要求「站点名」,所以 07-24
              # 11/11 稿零 URL 其实是**完全合规**的,罚它的 lint 才是孤儿;②本票行情数字
              # 禁区(涨跌幅由确定性 slim 供给);③声明行的网查数现在有对账探针了。
              "来源URL 必落", "行情数字不自报", "〔转引标题〕"):
        assert a in text, f"l4-intel 缺契约锚「{a}」"


def test_l4_intel_price_ban_scoped_to_own_stock():
    """行情数字禁区只针对**本票** —— 同题材涨停家数/产业链价格是题材强度证据,必须照查。

    禁区若写成「一切数字不许写」会误伤 `题材段` 的「同题材今日涨停家数」要求(该要求
    在同一份 def 里),两条指令互相打架 → agent 只能猜。这里钉死例外条款在场。
    """
    text = _agent_text("l4-intel")
    assert "同题材今日强度" in text or "涨停家数" in text, "题材强度要求被误删"
    assert "不是本票行情" in text, "禁区缺例外条款 → 与题材段的涨停家数要求打架"

def test_l4_intel_wired_in_docs():
    skill = (SKILLS / "scan-market" / "SKILL.md").read_text(encoding="utf-8")
    stages = (SKILLS / "scan-market" / "STAGES.md").read_text(encoding="utf-8")
    assert "l4-intel" in skill or "l4-intel" in stages, "scan 文档未接线 l4-intel(Task 7 落)"


def test_dossier_chain_wired_in_stages_doc():
    """N-6(2026-07-24 终审同批建议):Wave3.5 T5 Step 1 往 STAGES.md 补了覆盖档案链一段,
    但 Step 3 的文档接线测试没做——机制对操作者可见的这段描述本身零锚,可被静默删除/
    漂移而无人察觉(与 M-17 的病因同族)。断言两个关键机器契约锚在场:池文件
    `coverage_pool.json`、季度对账 CLI `dossier.reconcile`。
    """
    stages = (SKILLS / "scan-market" / "STAGES.md").read_text(encoding="utf-8")
    assert "coverage_pool" in stages, "STAGES.md 未接线覆盖池 coverage_pool"
    assert "dossier.reconcile" in stages, "STAGES.md 未接线季度对账 dossier.reconcile"


def test_l4_stock_workflow_sell_review_anchors():
    """l4-stock.js 的 SELL 双复核契约锚(Wave1 ⑤-3):trigger 字段 + pinned 消费在场。"""
    js = (ROOT / ".claude" / "workflows" / "l4-stock.js").read_text(encoding="utf-8")
    for a in ("sell_review", "ow_review", "A.pinned", "proposal"):
        assert a in js, f"l4-stock.js 缺 SELL 双复核锚「{a}」"


def test_l4_stock_ensemble_early_stop_anchors():
    """同档早止的承重锚(Wave6 T2)。

    07-24 的 601869 三票全 UW(spread 0)白烧第三跑。两票同档时三票中位数学上已定,
    跳过 run3 结果逐字节相同(数学前提由 test_ensemble_fold 钉死)。

    `earlyStopped ? false :` 是「早止不算 degraded」的判据本体 —— 删掉它(把早止并进
    degraded)会让 SELL 复核该折不折,这是本改动最贵的写反方式,锚必须钉在这行上。
    """
    js = (ROOT / ".claude" / "workflows" / "l4-stock.js").read_text(encoding="utf-8")
    assert "const r2 = await rerun(2)" in js, "run2 未串行化 → 无从同档早止"
    assert "earlyStopped ? false :" in js, "早止被并进 degraded = SELL 复核该折不折"
    assert "early_stopped: earlyStopped" in js, "产物未记早止标记(账本无法区分 2 跑/3 跑)"


def test_l4_stock_workflow_dossier_summary_anchors():
    """l4-stock.js 的 intel 已知底**消费**契约锚(Wave3.5 review R1 I-1)。

    生产者(dispatch_plan)把摘要塞进 meta 只是一半——真正交付是它被拼进 Intel prompt
    尾部。变异实测证明这条锚此前零覆盖:删掉 `${knownBase}` 拼接(=消费腿整条死掉,
    meta 照带摘要但没人读它)后**全量 1468/1468 仍 PASS**,是本 repo 反复烧的 FN-1 族
    (生产者接线了、消费者没接/被静默删,产物看起来一切正常)。`test_dossier_init_workflow_anchors`
    已有同款先例。
    """
    js = (ROOT / ".claude" / "workflows" / "l4-stock.js").read_text(encoding="utf-8")
    for a in ("A.dossierSummary", "knownBase", "${knownBase}", "已知底"):
        assert a in js, f"l4-stock.js 缺 intel 已知底消费锚「{a}」"


def test_dossier_init_workflow_anchors():
    js = (ROOT / ".claude" / "workflows" / "dossier-init.js").read_text(encoding="utf-8")
    for a in ("dossier-init", "builder", "lint", "LLM:待首覆"):
        assert a in js, f"dossier-init.js 缺锚「{a}」"


def test_dossier_init_agent_def():
    p = ROOT / ".claude" / "agents" / "dossier-init.md"
    text = p.read_text(encoding="utf-8")
    for a in ("model: opus", "三情景", "证伪触发点", "断言分级", "不改确定性节"):
        assert a in text, f"dossier-init.md 缺契约锚「{a}」"


def _agent_defaults_block(src: str, filename: str) -> str:
    """切出 `const AGENT_DEFAULTS = {...}` 整块文本(含首尾大括号,`}` 独占一行)。"""
    start = src.index("const AGENT_DEFAULTS = {")
    end = src.index("\n}\n", start)
    assert end > start, f"{filename}: AGENT_DEFAULTS 表未闭合(找不到独占一行的 `}}`)"
    return src[start:end + 2]


def test_workflow_shell_wrappers_use_agent_defaults():
    """纯壳 agent(跑命令 / 转述 JSON / 写文件)必须走 AGENT_DEFAULTS 单一事实源(Wave11-B2)。

    原测试(`test_workflow_shell_wrappers_use_haiku`)锁的是「壳必须内联 `model: 'haiku'`」——
    07-24 真计量:13 个 general-purpose 吃掉 798k 加权(全场 14.5%),其中 7 个 2-消息纯壳
    ≈287k 加权纯过路费,降档省的就是这份 opus 系统前缀。2026-08-05 事故(haiku 壳遇长命令
    转后台会 pkill 生产作业)后壳类缺省改 sonnet,Wave11-B2 又把 model/effort 单一事实源
    收进每个 workflow 顶部的 `AGENT_DEFAULTS` 表 + `AG(role)` 解析器 —— 原测试按字面
    `model: 'haiku'` 计数,与新架构互斥(该字符串在健康仓库里现在应该是 0 次,不再是 ≥5)。
    这里改锁新契约的等价物:
    ① 调用点不得有 AGENT_DEFAULTS 表外的内联 model 字面量(单一事实源不被绕过、不被 sed 漏改);
    ② 壳类角色(gp_shell/gp_shell_json)缺省钉在 sonnet —— 防止有人删掉 model 键悄悄回落
       到 opus 系统前缀(原测试保护的成本目标),也防止悄悄改回 haiku(08-05 事故的教训);
    ③ 判断类 agentType 不得被误配 haiku(原测试第三条锁原样保留,防「顺手把整个文件 sed 一遍」)。
    """
    wf_dir = ROOT / ".claude" / "workflows"
    all_js = {p.name: p.read_text(encoding="utf-8") for p in wf_dir.glob("*.js")}

    # 只有这三个文件走 AGENT_DEFAULTS(t1-review.js 已于 D3(2026-08-19)随 t1-review LLM
    # 腿一并退役——独立的 cfg.agents.t1_diag/t1_synth 通道不再存在)。
    shell_using = {"scan-market.js", "l4-stock.js", "dossier-init.js"}
    for name in sorted(shell_using):
        assert name in all_js, f"{name} 不存在(workflow 被改名?同步更新本测试)"
        src = all_js[name]
        assert "const AG = (role) =>" in src, f"{name} 缺 AG(role) 解析器(Wave11-B2)"
        block = _agent_defaults_block(src, name)
        rest = src.replace(block, "", 1)
        assert "model: '" not in rest, (
            f"{name} 在 AGENT_DEFAULTS 表外仍有内联 model 字面量 —— 单一事实源被绕过,"
            f"调用点应改用 `...AG(role)`")
        assert "AG('gp_shell')" in src, f"{name} 没有调用点使用 AG('gp_shell')"
        assert "AG('gp_shell_json')" in src, f"{name} 没有调用点使用 AG('gp_shell_json')"
        for line in block.splitlines():
            s = line.strip()
            if s.startswith("gp_shell:") or s.startswith("gp_shell_json:"):
                assert "'sonnet'" in s, (
                    f"{name}: AGENT_DEFAULTS 的 {s!r} 未钉 sonnet —— "
                    f"08-05 事故:壳类缺省不得静默回落到 opus/haiku")
                assert "haiku" not in s and "opus" not in s, f"{name}: {s!r} 误写危险 model"

    # 判断 agent 不得被误降 haiku(防「顺手把整个文件 sed 一遍」;原测试第三条锁原样保留,
    # 联合扫全部 workflow)。
    joined = "".join(all_js.values())
    for real in ("l3-rank", "l4-card", "l4-intel", "macro-brief", "sector-brief", "dossier-init"):
        assert f"agentType: '{real}', model: 'haiku'" not in joined, \
            f"{real} 是判断 agent,不得降 haiku"


def test_l4_stock_card_and_ensemble_bind_to_distinct_roles():
    """[task-8-review.md Minor,CONFIRMED] card 用 `l4_card` / rerun 用 `ens_review` 的
    绑定关系必须被锁定,不能只查"两个 role 名字符串都在文件某处出现过"。

    `test_workflow_shell_wrappers_use_agent_defaults` 只断言 `AG('l4_card')`/
    `AG('ens_review')` 这两个子串在文件里存在,不追踪"哪一行用了哪一个"——如果有人手滑
    把 `card` 调用点(渐进深度 DD 主卡)与 `rerun()`(≥OW/SELL 双复核 run2/3)的 role 互换,
    或把其中一个错改成对方,这两个字符串依旧都在场,那条测试不会变红。这正是
    brief 点名"最危险的失误"——本测试把绑定关系本身钉死。

    与之呼应的生产事故(task-8-review.md Important):`ens_review` 与 `l4_card` 在
    scan_config.jsonc 里是两个独立 role,若被静默接反,复核 run 与主卡谁用哪档 effort
    会整个错位而没有任何报错。
    """
    src = (ROOT / ".claude" / "workflows" / "l4-stock.js").read_text(encoding="utf-8")
    card_anchor = "label: `card:${code}`, phase: 'Card', schema: CARD"
    ens_anchor = "label: `ens${i}:${code}`, phase: 'Verify', schema: CARD"
    assert src.count(card_anchor) == 1, f"card 调用点锚点漂移或不唯一:{card_anchor!r},先更新本测试"
    assert src.count(ens_anchor) == 1, f"ensemble rerun 调用点锚点漂移或不唯一:{ens_anchor!r},先更新本测试"

    card_head = src.split(card_anchor)[0][-200:]
    ens_head = src.split(ens_anchor)[0][-200:]
    assert "AG('l4_card')" in card_head, f"card 调用点未使用 AG('l4_card'):{card_head!r}"
    assert "AG('ens_review')" in ens_head, f"ensemble rerun 调用点未使用 AG('ens_review'):{ens_head!r}"
    assert "AG('ens_review')" not in card_head, "card 调用点误用了 ens_review role(主卡吃了复核档位)"
    assert "AG('l4_card')" not in ens_head, "ensemble rerun 调用点误用了 l4_card role(复核吃了主卡档位)"


def test_scan_market_workflow_pinned_roster_log():
    """派发前必须逐只列出 pinned 名单(07-21 漏传 args.pinned → 持仓 SELL 双复核断链)。

    探针 9 sell_review_missing 是事后 warn;真正断的是派发那一秒的记忆,所以契约必须
    出现在 handoff 日志里。删掉那行 log,本测试应变红。
    """
    js = (ROOT / ".claude" / "workflows" / "scan-market.js").read_text(encoding="utf-8")
    # 锚必须只出现在**承重行**里:第一版用 "📌 保送票"/"args.pinned" 做锚,结果这两串在上方
    # 注释里也有 → 删掉整条 log 测试照绿(与 "停因:" 同一个病)。下面两个锚只有 log 语句有。
    assert "${pinnedCodes.join('/')}" in js, "scan-market.js 缺 pinned 名单 log(派发时刻不可见)"
    assert "args.pinned:true" in js, "scan-market.js 的 pinned log 未点名 args.pinned:true 传参要求"
    assert "metaAll[c].pinned" in js, "pinned 名单没读 meta.pinned(名单恒空 = 假绿灯)"


def test_scan_market_workflow_live_anchors():
    """直播锚:GATE2 必须逐只列名单(g2.meta 已带 name/sector,此前只 log 计数)。"""
    js = (ROOT / ".claude" / "workflows" / "scan-market.js").read_text(encoding="utf-8")
    assert "_prelude_summary.md" in js, "prelude 汇总屏未指路(末15行截断依旧)"
    assert "L3入围" in js, "GATE2 未逐只 log 入围名单"
    assert "g2.meta" in js or "metaAll" in js, "GATE2 名单未读 meta(name/sector 白算)"


def test_scan_market_skill_live_contract():
    """8 检查点直播契约必须在 SKILL.md 里(主会话从 L0 到 L5 静默是 Wave5 ① 的起因)。"""
    md = (SKILLS / "scan-market" / "SKILL.md").read_text(encoding="utf-8")
    assert "过程直播契约" in md
    # 锚取**表格行**而非裸 "CPn":裸串会被正文里的「**CP5 滚动表做法**」满足 → 删掉表行
    # 测试照绿(第一版实测)。表是契约本体,散文是补充说明。
    for cp in ("CP0", "CP1", "CP2", "CP3", "CP4", "CP5", "CP6", "CP7"):
        assert f"| {cp} |" in md, f"SKILL.md 直播契约表缺 {cp} 行"
    assert "autoresearch.scan.render" in md, "SKILL.md 未告诉主会话怎么调 render CLI"
    assert "_prelude_summary.md" in md, "SKILL.md 未要求转播前奏汇总屏全文"
    assert "停因分桶" in md, "SKILL.md 收尾未要求 0买日播停因分桶(旧不实判词会复辟)"


def test_cp7_broadcasts_brief_verbatim():
    """Wave12-T28 入口切换:CP7 播报 = **读 brief.md 原文 + 附路径**。

    brief 是确定性模板产物(≤3KB、零 LLM),主会话再复述一遍等于第二次编数面。锚同时钉
    直播契约表的 CP7 行与步骤 5 的汇报段——只改一处会走漂。
    """
    md = (SKILLS / "scan-market" / "SKILL.md").read_text(encoding="utf-8")
    # 契约表整块在 blockquote 里,行首是 "> " —— 用 startswith("| CP7 |") 会永远 StopIteration
    cp7 = next(ln for ln in md.splitlines() if "| CP7 |" in ln)
    assert "brief.md" in cp7, "CP7 行未切到 brief.md"
    assert "原文" in cp7, "CP7 未写明「原文转播」(复述 = 第二次编数面)"
    assert "brief.md" in md.split("**汇报(CP7)**")[1][:400], "步骤 5 汇报段未指向 brief"


def test_skill_docs_say_nothing_machine_reads_summary_prose():
    """分层安全性的前提必须写在文档里:**没有机器消费者解析 `summary.md` 正文**,
    结论都在结构化文件里。不写下来,下一个人重排 summary 时会以为自己在动机器契约。

    (2026-08-21 learning 层退役前这条断言点名的是 `t1_review`/`retro` 两个消费者;
     它们已随闭环删除,性质从"那两个不读"变成"没有人读",措辞随之更新。)
    """
    md = (SKILLS / "scan-market" / "SKILL.md").read_text(encoding="utf-8")
    hits = [ln for ln in md.splitlines()
            if "summary" in ln and ("不读" in ln or "不解析" in ln)]
    assert len(hits) >= 2, f"SKILL.md 需在两处写明没有机器消费者读 summary 正文,现 {len(hits)} 处"
    for ln in hits[:2]:
        assert "机器消费者" in ln, f"措辞不构成断言:{ln}"


def test_macro_brief_consumes_new_pack_blocks():
    """Wave5 ③A:新接的 cross_money/index_val 必须有**消费者**契约。

    生产者接线了、消费者没接是本仓 FN-1 家族的常客(pack 多两块、market_view 照样只复述
    老 24 个标量 = 白接)。锚同时钉 agent def 与 playbook 真值源。
    """
    agent = _agent_text("macro-brief")
    playbook = (SKILLS / "macro-research" / "macro-playbook.md").read_text(encoding="utf-8")
    for a in ("cross_money", "index_val"):
        assert a in agent, f"macro-brief 未消费 pack 新块「{a}」"
        assert a in playbook, f"macro-playbook 未同步 pack 新块「{a}」"
    assert "macro_cn_degraded" in agent, "macro-brief 未要求披露取数降级(降级不留痕=本仓红线)"


def test_usage_harvest_wired_in_skill():
    """真计量必须有消费者(Wave5 ④A)。

    生产者建好、SKILL 不提 = 没人会跑它,就是本仓 FN-1 家族的又一例。锚取 CLI 模块路径
    与加权口径两项——删掉 CP7 的计量段就会红。
    """
    md = (SKILLS / "scan-market" / "SKILL.md").read_text(encoding="utf-8")
    assert "autoresearch.trace.usage_harvest" in md, "SKILL 未接线 token 真计量 CLI"
    assert "加权" in md, "SKILL 未说明按计价倍率加权(原始 token 会把「贵在哪」排反)"


def test_scan_market_skill_documents_wave3_recovery_and_measurement_contract():
    """Wave 3 不是只把代码接上：未来编排者必须知道批次、重放、回滚和计量回填。

    ⚠️ **锚更新(final-review 2026-08-08 D1)**:原锚 `滑窗` 第三次踩进同一个失效模式——
    Wave11-C3 把派发协议由「滑窗补派」改回「一次性全派」,`滑窗` 这个词此后在 SKILL.md 里
    唯一的命中变成 :103「回滚杆**不能**恢复旧滑窗节奏」,又是一句**否定**该词所指契约的话,
    不是契约本身在场。改锚为 `一次性全派`(SKILL.md:36/63/102 三处都在陈述当前真实派发
    行为,不是历史注或否定句)。

    这不是第一次:W8-8 时原锚是 `批次内并行`,换成 `滑窗` + `task_book 全 SUCCEEDED` 时就
    已经记录过同一个坑——派发契约由「批次间顺序 + 批次内并行」改为滑窗补派(每完成一只
    补派一只)后,`批次内并行` 仍然绿,因为新文本里有一句「旧契约是「按 dispatch_batches
    批次间顺序、批次内并行」——每批要等最慢者」,锚匹配到了这句**否定该契约的历史注**
    (同族:price_claims 把卡片"引用并拒绝"的 intel 断言当成卡片自己的断言)。当时定下的
    教训是「锚要挑只可能出现在活契约里的词」——道理没错,但只写在 docstring 里挡不住
    下一次协议变更时被忘记应用,Wave11-C3 复发就是证据。改协议的那次改动应该同一个 PR
    里把锚也扫一遍,不是留给下一次 review 抓。
    """
    skill = (SKILLS / "scan-market" / "SKILL.md").read_text(encoding="utf-8")
    stages = (SKILLS / "scan-market" / "STAGES.md").read_text(encoding="utf-8")
    for anchor in (
        "dispatch_batches",
        "一次性全派",
        "task_book 全 SUCCEEDED",
        "RATE_LIMIT",
        "streaming_l4",
        # Wave10 B4:另两个性能开关已退役 —— 锚改成"退役这件事本身"被写进契约文档,
        # 否则下次有人照着旧文档去设一个不存在的开关。
        "Wave10 B4 退役两个",
        # 引擎隔离(2026-08-11):doc 命令统一 $CTX 记号(= context_<engine>,bash 块顶行取值)
        "--json-out $CTX/scan/<date>/_token_usage.json",
        "autoresearch.scan.post_run <date> observe",
        "IMMATURE",
        "10 次真实扫描",
    ):
        assert anchor in skill, f"SKILL.md 缺 Wave 3 运行契约:{anchor}"
    assert "_l4_tasks.json" in stages
    assert "PENDING/RUNNING/SUCCEEDED/FAILED/BLOCKED" in stages
    assert "预算只告警" in stages


def test_experiment_registry_governance_retired_from_skill_docs():
    """D1(2026-08-19,用户裁决 A3):experiment_registry 家族的状态机已整删,文档不得
    再教用户跑一个已删除的 CLI —— 镜像下面 `test_otel_path_retired_from_skill_docs`
    的判例(同一族病:文档教已删代码,后人照做直接 ModuleNotFoundError)。

    变异验证:把 `autoresearch.learning.experiment_registry` 这类命令写回任一文档,
    本测试变红。
    """
    skill = (SKILLS / "scan-market" / "SKILL.md").read_text(encoding="utf-8")
    stages = (SKILLS / "scan-market" / "STAGES.md").read_text(encoding="utf-8")
    for doc, nm in ((skill, "SKILL.md"), (stages, "STAGES.md")):
        assert "autoresearch.learning.experiment_registry" not in doc, \
            f"{nm} 仍在教已删除的 experiment_registry CLI"
        assert "autoresearch.learning.promotion" not in doc, \
            f"{nm} 仍在教已删除的 promotion CLI"
        assert "autoresearch.learning.rollback_watch" not in doc, \
            f"{nm} 仍在教已删除的 rollback_watch CLI"
    # D2(2026-08-21,用户裁定「整个 learning 层退役」):连"影子账本呈证 → proposal 人批"
    # 那套也没了(证据来源整包删除)。两份文档必须**明说现在是普通开发改动**,不能只删
    # 旧描述留一片空白 —— 空白会让下一个人以为治理还在、只是自己没找到。
    for anchor in ("普通开发改动", "learning 层退役"):
        assert anchor in stages, f"STAGES.md 缺现行说明关键词:{anchor}"
    assert "learning" in skill and "退役" in skill, "SKILL.md 未说明闭环已退役"
    for doc, nm in ((skill, "SKILL.md"), (stages, "STAGES.md")):
        for gone in ("autoresearch.learning.retro", "autoresearch.learning.t1_review",
                     "autoresearch.learning.cross_calib"):
            assert gone not in doc, f"{nm} 仍在教已删除的 {gone} CLI"


def test_experiment_registry_family_modules_are_gone():
    """模块真删了(不是只改文档)—— 否则「退役」只是叙事(镜像 test_telemetry_module_is_gone)。

    D2(2026-08-21,用户裁定「整个 learning 层退役」)把范围从"治理状态机"扩到**整包**:
    `autoresearch.learning` 这个包名本身必须已经不存在。原来 D1 特意保留的
    `experiment_template.py`(当时是 `gate_recal`/`l3_marginal` 的真实依赖)随那两个模块
    一起删 —— 依赖方没了,方法学模块也就没有消费者了。

    **正锚同样重要**:三件门/尺是搬家不是删,它们必须在 `autoresearch.scan` 下可 import,
    否则「搬家」也会变成只存在于叙事里的说法。
    """
    import importlib.util

    assert importlib.util.find_spec("autoresearch.learning") is None, \
        "autoresearch.learning 仍在:文档说整包退役、代码还在 = 只存在于叙事里的改动"
    for mod in ("autoresearch.scan.self_review", "autoresearch.scan.tripwire_watch"):
        assert importlib.util.find_spec(mod) is not None, \
            f"{mod} 应已搬到 scan 包(GATE4 判据 / 持仓盯梢尺,不是学习件)"
    from autoresearch.scan import temperature_calib
    for fn in ("trade_days", "market_nav"):
        assert hasattr(temperature_calib, fn), \
            f"temperature_calib.{fn} 应已内联(原 learning.paper_nav 的读湖工具)"


def test_otel_path_retired_from_skill_docs():
    """OTEL 那条路已退役(Wave6 E1):文档不得再教用户跑一个已删除的 CLI。

    `trace/telemetry.py` 自 2026-07-05 建成起零生产调用点、全仓无一个 `token_telemetry.md`,
    2026-07-27 删除。留着文档 = 后人照做直接 ModuleNotFoundError,且两套计量并存不知信谁。

    变异验证:把 telemetry 命令写回任一文档,本测试变红。
    """
    skill = (SKILLS / "scan-market" / "SKILL.md").read_text(encoding="utf-8")
    stages = (SKILLS / "scan-market" / "STAGES.md").read_text(encoding="utf-8")
    for doc, nm in ((skill, "SKILL.md"), (stages, "STAGES.md")):
        assert "trace.telemetry" not in doc, f"{nm} 仍在教已删除的 telemetry CLI"
        assert "CLAUDE_CODE_ENABLE_TELEMETRY" not in doc, f"{nm} 仍在教 OTEL env"
    assert "usage_harvest" in stages, "STAGES 计量节必须改记 usage_harvest 为唯一正典"


def test_telemetry_module_is_gone():
    """模块真删了(不是只改文档)—— 否则「退役」只是叙事。"""
    import importlib.util
    assert importlib.util.find_spec("autoresearch.trace.usage_harvest") is not None
    assert importlib.util.find_spec("autoresearch.trace.telemetry") is None, \
        "telemetry 模块仍在:文档说退役、代码还在 = 又一个只存在于叙事里的改动"


def test_l4_stock_normalizes_conviction_scale():
    """conviction 必须归一到 0-100(pr_20260717_005)。

    07-14 实测:北方华创回传 0.62,其余 8 只是 60–78 整数 —— 同一字段两种标度。
    下游 `force_full_card` 判据是 conviction>=70,0.62 会被当成极低确信 → 强制满卡
    静默失效(FN-1 家族:网建成了但恒不触发)。
    """
    js = (ROOT / ".claude" / "workflows" / "l4-stock.js").read_text(encoding="utf-8")
    assert "normConviction" in js, "l4-stock.js 未做 conviction 标度归一"
    assert "card.conviction = normConviction(card.conviction)" in js, \
        "归一函数定义了却没用上(生产者无消费者)"


def test_run_health_refreshed_after_summary():
    """run_health 的 artifacts/missing 必须在 gate_fires 落盘后再刷一次(Wave6 Q6)。

    07-24 实锤:run_health.json 13:16:21 记 missing=[verify.csv, gate_fires.csv],
    而 gate_fires.csv 13:16:22 就存在 —— 快照取早了。不能简单前后调换:
    `product_shape_lint` 的 force_full 探针**读** run_health 且 presence-gated,
    挪到 build_summary 之后会让那个探针静默失效。故保留前一次 + 之后补刷一次。
    """
    src = (ROOT / "autoresearch" / "scan" / "publisher.py").read_text(encoding="utf-8")
    # 2026-08-29 B+:summary 写盘从 `summary_path.write_text(md)` 变成**发布包双写**
    # `_write_report_bundle(out_base, md, appendix_md)`(两份都非空才动盘,各自 tmp+replace)。
    # 锚随之更新;被守的不变量一个字没变:run_health 必须在报告落盘**之后**再刷一次。
    after = src.split("_write_report_bundle(", 1)
    assert len(after) == 2, "summary 写盘锚点漂移,先更新本测试"
    assert "_health.write_run_health(scan_dir)" in after[1], \
        "报告落盘之后没有补刷 run_health → missing 列表继续说假话"


# ═══════ Wave12-T33:AGENT_DEFAULTS 三/四文件 + Python `_ROLE_FALLBACK` 的机器锁 ═══════


def _parse_agent_defaults(src: str, name: str) -> dict:
    """从 workflow js 里把 `const AGENT_DEFAULTS = {...}` 解析成 Python dict。

    **不是** grep 计数,也不是正则抠值:整块取出来交给 node 求值再回 JSON —— 这样
    "值改了没同步"才真的锁得住(数行数 ≠ 数事件,同波家训)。node 缺席则跳过,口径同
    `test_workflow_js_syntax.py`。
    """
    import json
    import shutil
    import subprocess

    if shutil.which("node") is None:
        pytest.skip("本机无 node,跳过(口径同 test_workflow_js_syntax.py)")
    block = _agent_defaults_block(src, name)
    script = f"const AGENT_DEFAULTS = {block[block.index('{'):]};\nconsole.log(JSON.stringify(AGENT_DEFAULTS))"
    r = subprocess.run(["node", "-e", script], capture_output=True, text=True,
                       timeout=10, check=False)
    assert r.returncode == 0, f"{name} 的 AGENT_DEFAULTS 块无法求值:{r.stderr}"
    return json.loads(r.stdout)


def test_shell_defaults_identical_across_workflows():
    """壳类档位(gp_shell/gp_shell_json)在**所有**带表的 workflow 里必须逐字段相等。

    Wave11 把它们抄进了每个 workflow 顶部;三份副本靠人记得同步 = 迟早漂移。
    这里做**求值后相等**断言(不是字符串比对):任一文件改一个值,本测试必红。
    """
    wf_dir = ROOT / ".claude" / "workflows"
    tables = {}
    for p in sorted(wf_dir.glob("*.js")):
        src = p.read_text(encoding="utf-8")
        if "const AGENT_DEFAULTS = {" not in src:
            continue
        tables[p.name] = _parse_agent_defaults(src, p.name)
    assert len(tables) >= 3, f"应至少三个 workflow 带 AGENT_DEFAULTS 表,实际 {sorted(tables)}"

    shells = {name: {k: v for k, v in tbl.items() if k.startswith("gp_shell")}
              for name, tbl in tables.items() if any(k.startswith("gp_shell") for k in tbl)}
    assert len(shells) >= 3
    ref_name, ref = sorted(shells.items())[0]
    for name, tbl in shells.items():
        assert tbl == ref, (f"{name} 的壳类缺省与 {ref_name} 不一致:{tbl} vs {ref} —— "
                            f"三处副本必须同值,别靠人记得同步")


def test_workflow_defaults_agree_with_python_role_fallback():
    """workflow 兜底表 × Python `_ROLE_FALLBACK` 必须逐 role 相等(Wave12-T33 单一事实源)。

    resolved 传到时 workflow 吃 resolved,传不到时吃自己那张表 —— 两条腿值不同,
    就会出现"同一次跑,传没传 resolved 结果不一样"的幽灵差异。这条把它钉死。
    """
    from autoresearch.scan.user_config import _ROLE_FALLBACK

    wf_dir = ROOT / ".claude" / "workflows"
    checked = 0
    for p in sorted(wf_dir.glob("*.js")):
        src = p.read_text(encoding="utf-8")
        if "const AGENT_DEFAULTS = {" not in src:
            continue
        for role, spec in _parse_agent_defaults(src, p.name).items():
            assert role in _ROLE_FALLBACK, f"{p.name} 的 role {role!r} 不在 Python 兜底表里"
            assert spec == _ROLE_FALLBACK[role], (
                f"{p.name} 的 {role} 兜底={spec},Python `_ROLE_FALLBACK`={_ROLE_FALLBACK[role]} "
                f"—— 两条腿必须同值")
            checked += 1
    assert checked >= 12, f"应至少核对 12 条 role 兜底,实际 {checked}"


def test_all_workflows_consume_resolved_agent_config():
    """四个 workflow 都必须**先看 resolved**,AGENT_DEFAULTS 只是兜底(Wave12-T33)。

    少一个文件没接线,那个阶段就会绕过单一事实源继续用自己的解释 —— FN-1 家族的形状。
    """
    wf_dir = ROOT / ".claude" / "workflows"
    for p in sorted(wf_dir.glob("*.js")):
        src = p.read_text(encoding="utf-8")
        if "const AGENT_DEFAULTS = {" not in src:
            continue
        assert "resolved_agents" in src, f"{p.name} 没消费 cfg.resolved_agents"
        assert "RESOLVED[role]" in src, f"{p.name} 的 AG(role) 没有 resolved 优先分支"
