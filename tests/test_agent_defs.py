"""agent-defs lint —— `.claude/agents/` 叶子 agent 定义与源码/playbook 的最低一致性。

2026-07-05:scan 叶子 agent 化(l4-card / sector-brief;buy-skeptic 07-07 移除)——稳定人设烤进
agent system prompt(派发 prompt 缩到两行、前缀吃 cache、契约不再靠每次转述)。
本文件治两类漂移:① agent 文件缺/frontmatter 坏;② 关键**机器契约锚**(进入P4倾向 /
FINAL TRANSACTION PROPOSAL / OW三门名 / 两段标题)在 agent 定义与真值源间失同步。
"""
from __future__ import annotations

from pathlib import Path

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
    agent = _agent_text("l4-card")
    playbook = (SKILLS / "stock-research" / "lite-playbook.md").read_text(encoding="utf-8")
    anchors = ["进入P4倾向", "FINAL TRANSACTION PROPOSAL", "**Rating**",
               "断言分级", "一致预期差",
               "早停只向下", "Rubric建议", "一段话研判", "L3 论点裁决",
               "已核数字摘录", "多写不多读", "龙虎榜席位", "活体新闻",
               # 锚必须独有:"停因:" 曾被既有的 "早停因:" 整段吃掉 → 删了早停行测试照绿
               "早停卡短格式", "**早停**: 停于", "卡契约 v3·超短 1~2 日", "超短口径",
               "机构面网查", "先读数据后读论点", "活体情报", "持仓管理", "档案对账",
               # Wave6:①独立初判从散文铁律升格为**卡结构元素**(07-24 实测 0/11 卡含此串,
               # chk_blind_pass 全 fail —— 指令在、检查在,缺的是机器可核的标签行);
               # ②转引标题标注(07-24 两条 price_claim_mismatch 的真身是转述媒体标题)。
               "**独立初判**:", "〔转引标题〕",
               *(g for g in _OW_GATES)]
    for a in anchors:
        assert a in agent, f"l4-card 缺契约锚「{a}」"
        assert a in playbook, f"lite-playbook 缺契约锚「{a}」(真值源被改,先同步 agent 定义)"


def test_l3_rank_anchors_present():
    """l3-rank 契约锚:T+2 兑现机制维 + conviction 行为化重锚(≥70 限额)+ mechanism 输出字段
    + finalist tier 语义(finalist/bench 二分、≥75 误杀保险、宁缺毋滥不凑数)。

    l3-rank 无 lite-playbook 式的独立真值源(screening-playbook 已退役),故只做单文件
    存在性检查,不做双侧同步(与 test_l4_card_contract_anchors_synced 的双文件模式不同)。
    """
    agent = _agent_text("l3-rank")
    for a in ("兑现机制", "≥70", "mechanism", "finalist", "bench", "≥75", "宁缺毋滥"):
        assert a in agent, f"l3-rank 缺契约锚「{a}」"


def test_sector_brief_anchors_synced():
    """sector-brief 两段标题/方向行与 brief.py 机器契约同源(extract_terrain/extract_view/记账)。"""
    from autoresearch.sector.brief import TERRAIN_HDR, VIEW_HDR  # 单一事实源
    agent = _agent_text("sector-brief")
    for a in (TERRAIN_HDR, VIEW_HDR, "**行业方向**", "不编", "实时网查"):
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
    for a in ("事件段", "题材段", "机构段", "互动段", "负面增量段", "声明行",
              "as-of", "六面全查", "≤15", "净分", "只报本票事实", "只攒料不判断", "不编", "盲",
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


def test_earlystop_shadow_workflow_forces_full_review_without_production_writes():
    js = (
        ROOT / ".claude" / "workflows" / "earlystop-shadow.js"
    ).read_text(encoding="utf-8")
    for anchor in (
        "agentType: 'l4-card'",
        "强制走完",
        "Rubric",
        "shadow/earlystop_details",
        "不能改变正式评级",
    ):
        assert anchor in js, f"earlystop-shadow.js 缺影子深审锚「{anchor}」"


def test_dossier_init_workflow_anchors():
    js = (ROOT / ".claude" / "workflows" / "dossier-init.js").read_text(encoding="utf-8")
    for a in ("dossier-init", "builder", "lint", "LLM:待首覆"):
        assert a in js, f"dossier-init.js 缺锚「{a}」"


def test_dossier_init_agent_def():
    p = ROOT / ".claude" / "agents" / "dossier-init.md"
    text = p.read_text(encoding="utf-8")
    for a in ("model: opus", "三情景", "证伪触发点", "断言分级", "不改确定性节"):
        assert a in text, f"dossier-init.md 缺契约锚「{a}」"


def test_workflow_shell_wrappers_use_haiku():
    """纯壳 agent(跑命令 / 转述 JSON / 写文件)必须降 haiku(Wave6 T1)。

    07-24 真计量:13 个 general-purpose 吃掉 798k 加权(全场 14.5%),其中 7 个是 2 消息的
    纯壳,各背 ~60k 的 opus 系统前缀 ≈287k 加权纯过路费。壳本身零判断 —— 门的判据全在
    确定性 CLI 里,agent 只负责执行与转述。

    锚取**承重行**(agentType 与 model 同现在一个 opts 对象里):注释里写了不算,
    删掉任一 `model: 'haiku'` 本测试必须变红。
    """
    wf_dir = ROOT / ".claude" / "workflows"
    all_js = {p.name: p.read_text(encoding="utf-8") for p in wf_dir.glob("*.js")}

    # 全量扫:**任何** workflow 里的 general-purpose 壳都必须带 haiku。
    # 第一版只点名 scan-market/l4-stock 两个文件 → dossier-init.js 的两个壳漏网,
    # 2026-07-27 实测一次建档 249.8k 加权里它们占 27%(67.2k 换 1.0k 输出)。
    # 逐文件枚举的清单会漏掉新增文件;改成全量扫,新 workflow 一进来就被管住。
    for name, src in sorted(all_js.items()):
        bare = src.count("agentType: 'general-purpose', effort:")
        assert bare == 0, f"{name} 有 {bare} 个 general-purpose 壳未降 haiku(纯壳零判断,应降档)"
    assert sum(s.count("agentType: 'general-purpose', model: 'haiku'")
               for s in all_js.values()) >= 5, "壳降档锚整体消失了?(应至少 5 处)"
    # 判断 agent 不得被误降 —— 这条防的是「顺手把整个文件 sed 一遍」
    joined = "".join(all_js.values())
    for real in ("l3-rank", "l4-card", "l4-intel", "macro-brief", "sector-brief", "dossier-init"):
        assert f"agentType: '{real}', model: 'haiku'" not in joined, \
            f"{real} 是判断 agent,不得降 haiku"


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
    """Wave 3 不是只把代码接上：未来编排者必须知道批次、重放、回滚和计量回填。"""
    skill = (SKILLS / "scan-market" / "SKILL.md").read_text(encoding="utf-8")
    stages = (SKILLS / "scan-market" / "STAGES.md").read_text(encoding="utf-8")
    for anchor in (
        "dispatch_batches",
        "批次内并行",
        "RATE_LIMIT",
        "streaming_l4",
        "stable_context_blocks",
        "sector_brief_mode",
        "--json-out context/scan/<date>/_token_usage.json",
        "autoresearch.scan.post_run <date> observe",
        "IMMATURE",
        "10 次真实扫描",
    ):
        assert anchor in skill, f"SKILL.md 缺 Wave 3 运行契约:{anchor}"
    assert "_l4_tasks.json" in stages
    assert "PENDING/RUNNING/SUCCEEDED/FAILED/BLOCKED" in stages
    assert "预算只告警" in stages


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
    after = src.split("summary_path.write_text(md", 1)
    assert len(after) == 2, "summary 写盘锚点漂移,先更新本测试"
    assert "_health.write_run_health(scan_dir)" in after[1], \
        "build_summary 之后没有补刷 run_health → missing 列表继续说假话"
