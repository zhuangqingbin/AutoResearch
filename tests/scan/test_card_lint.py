"""卡片契约 lint:P4 倾向/变化项/复用与早停豁免 + banner 合并留痕。合成,无网络。

spec: docs/specs/2026-07-03-scan-run-reliability-design.md §1
"""
from __future__ import annotations

import pandas as pd

from autoresearch.scan.self_review import card_contract_lint

FULL_OK = "# 卡\n**Rating**: Hold\n进入P4倾向: Hold\n变化项(vs 档案):无\n**入场**: 允许\n"
FULL_NO_P4 = "# 卡\n**Rating**: Hold\n**一行多空**:多:x ｜ 空:y\n"
STOP = "# 卡\n**Rating**: Hold\n早停因: 资金流出\n**入场**: 禁止\n"
REUSE = "♻️ 复用卡\n**Rating**: Hold\n"
# 07-06 假阳:标题标〔早停·表面 DD〕但正文无「早停因」字样的卡,被当满卡查 P4 行
STOP_TITLE_ONLY = ("# 决策卡 — 002185 华天科技 @ 2026-07-06  ·  〔早停·表面 DD〕\n\n"
                    "**Rating**: Underweight\n\nFINAL TRANSACTION PROPOSAL: **HOLD**\n")
# 满卡标题正常,正文却带一个含「早停」的说明性小标题——豁免只认首行,warn 不得被吞
FULL_STRAY_HEADING = ("# 决策卡 — 000001 甲 @ 2026-07-06\n\n**Rating**: Hold\n\n"
                      "## 早停判断:未触发,完整走完 P4+P5\n\n"
                      "FINAL TRANSACTION PROPOSAL: **HOLD**\n")


def _mk(root, date, cards):
    d = root / date
    (d / "details").mkdir(parents=True)
    for code, text in cards.items():
        (d / "details" / f"{code}.md").write_text(text, encoding="utf-8")
    return d


def test_p4_line_lint(tmp_path):
    d = _mk(tmp_path, "2026-07-03",
            {"000001": FULL_NO_P4, "000002": STOP, "000003": REUSE, "000004": FULL_OK})
    out = card_contract_lint(d)
    checks = {(x["code"], x["check"]) for x in out}
    assert ("000001", "卡片契约·P4倾向缺失") in checks
    assert not any(c == "000002" for c, _ in checks)          # 早停免 P4 检
    assert not any(c == "000003" for c, _ in checks)          # 复用卡全豁免
    assert not any(c == "000004" for c, _ in checks)
    assert all(x["severity"] == "warn" for x in out)


def test_early_stop_title_card_exempt_from_p4_line(tmp_path):
    # 标题行含〔早停…〕即认早停卡,不强求正文再写「早停因」——两代卡片格式都豁免 P4 检
    d = _mk(tmp_path, "2026-07-06", {"002185": STOP_TITLE_ONLY})
    fires = card_contract_lint(d)
    assert not [f for f in fires if f["check"].startswith("卡片契约·P4倾向")], fires


def test_full_card_with_stray_early_stop_heading_still_warns(tmp_path):
    # 豁免面收紧回归:满卡正文的杂散「早停」小标题不构成早停卡,缺 P4 行必须照旧 warn
    d = _mk(tmp_path, "2026-07-06", {"000001": FULL_STRAY_HEADING})
    fires = card_contract_lint(d)
    assert [f for f in fires if f["check"] == "卡片契约·P4倾向缺失"], fires


def test_card_contract_lint_flags_missing_entry_line(tmp_path):
    """T18:`**入场**: 允许|禁止|条件` 行缺失 → warn(E6 v4 A/R 分级的机读依据)。

    fix round 1:第三张卡照抄模板占位符原文(`<禁止|条件(<一句>)>`,未填值)——
    lint 必须**与 parser 问同一个问题**(`has_machine_entry_line`,单一事实源编译自
    `agent_output.L4_CARD.field("entry").pattern`),不能用裸子串判"在场"。裸子串会把
    这张卡误判合规,而 `l4/parsers._ENTRY_LINE_RE` 不匹配占位符,下游
    `entry_source` 仍会落回 `"prose"`——lint 全绿、解析层却读到"没有行",两道验收门
    就会读到两个不一致的答案(coordinator 复现的缺陷)。
    """
    d = _mk(tmp_path, "2026-09-17", {
        "600018": ("# 决策卡 — 600018 上港 @ 2026-09-17  ·  〔早停·表面 DD〕\n"
                   "**早停**: 停于 P3 ｜ 停因:资金流出\nFINAL TRANSACTION PROPOSAL: **HOLD**\n"),
        "600035": ("# 决策卡 — 600035 楚天 @ 2026-09-17  ·  〔早停·表面 DD〕\n"
                   "**入场**: 禁止\n**早停**: 停于 P3 ｜ 停因:资金流出\nFINAL TRANSACTION PROPOSAL: **HOLD**\n"),
        "600036": ("# 决策卡 — 600036 招商银行 @ 2026-09-17  ·  〔早停·表面 DD〕\n"
                   "**一行多空**: 多 <…> ｜ 空 <…>\n"
                   "**入场**: <禁止|条件(<一句>)>   ← 机读契约(2026-09-24);早停卡不得写「允许」\n"
                   "**早停**: 停于 P3 ｜ 停因:资金流出\nFINAL TRANSACTION PROPOSAL: **HOLD**\n"),
    })
    hits = [h for h in card_contract_lint(d) if h["check"] == "卡片契约·入场行缺失"]
    codes = {h["code"] for h in hits}
    assert codes == {"600018", "600036"}                # 缺行 + 未填占位符都必须 warn
    assert "600035" not in codes                         # 真填了值的卡不该警
    assert all(h["severity"] == "warn" for h in hits)

    from autoresearch.contracts.agent_output import has_machine_entry_line
    assert has_machine_entry_line("**入场**: 禁止") is True
    assert has_machine_entry_line("**入场**: <禁止|条件(<一句>)>") is False     # 占位符不算


def test_card_contract_lint_warns_when_card_allows_entry_on_rebalance_eve(tmp_path, monkeypatch):
    """2026-09-25 §2.5:该票今晚是调样生效前夜,卡入场行却写『允许』→ warn(E6 门会否决,但卡应自己写 禁止)。

    brief 原字面卡面文本没有『评级』仪表盘表——`parse_card_context._parse_dashboard` 找不到表
    会在够到 `**入场**` 行之前整段早退(`entry_stance` 恒 UNKNOWN),三张卡全部读不出 ALLOWED,
    这条测试原样抄反而会红。这里按仓库既有真实卡面形状(`test_parsers_card_context.py` 的
    `| 评级 | 现价 | 仓位 |` 三行)补一张最小表,断言本身(哪只该警、警什么)照抄不动。

    minor-8(final whole-branch review)后这条检查按 `configured_rebalance_gate()` 门控;
    `tests/scan/conftest.py` 的 autouse `_isolate_default_pinned` 把 `DEFAULT_PATH` 指向不存在
    的路径,所以本用例显式 monkeypatch `load_user_config` 打开这道门,不依赖真实生产配置。
    """
    import pandas as pd

    import autoresearch.scan.user_config as uc
    monkeypatch.setattr(uc, "load_user_config", lambda path=None: {"relative_buy": {"rebalance_gate": True}})

    from autoresearch.scan import index_events as ie
    d = _mk(tmp_path, "2026-12-10", {
        "600035": ("# 决策卡 — 600035 楚天 @ 2026-12-10\n**Rating**: Hold\n"
                   "| 评级 | 现价 | 仓位 |\n|---|---|---|\n| Hold | 10 | 10% |\n进入P4倾向: Hold\n"
                   "**入场**: 允许\nFINAL TRANSACTION PROPOSAL: **HOLD**\n"),
        "600018": ("# 决策卡 — 600018 上港 @ 2026-12-10\n**Rating**: Hold\n"
                   "| 评级 | 现价 | 仓位 |\n|---|---|---|\n| Hold | 10 | 10% |\n进入P4倾向: Hold\n"
                   "**入场**: 禁止\nFINAL TRANSACTION PROPOSAL: **HOLD**\n"),
        "600036": ("# 决策卡 — 600036 招行 @ 2026-12-10\n**Rating**: Hold\n"
                   "| 评级 | 现价 | 仓位 |\n|---|---|---|\n| Hold | 10 | 10% |\n进入P4倾向: Hold\n"
                   "**入场**: 允许\nFINAL TRANSACTION PROPOSAL: **HOLD**\n"),
    })
    ie.write_index_events(d, pd.DataFrame([
        {"code": "600035", "index_code": "000905", "index_name": "中证500", "side": "add", "ann_date": "20261127",
         "eff_close_date": "20261211", "phase": "passive_close_eve", "source": "csindex", "flow_adv_days": None},
        {"code": "600018", "index_code": "000300", "index_name": "沪深300", "side": "drop", "ann_date": "20261127",
         "eff_close_date": "20261211", "phase": "passive_close_eve", "source": "csindex", "flow_adv_days": None},
        {"code": "600036", "index_code": "000300", "index_name": "沪深300", "side": "add", "ann_date": "20261127",
         "eff_close_date": "20261211", "phase": "announced_runup", "source": "csindex", "flow_adv_days": None},
    ], columns=ie.EVENT_COLS))
    hits = [h for h in card_contract_lint(d) if h["check"] == "卡片契约·调样前夜入场允许"]
    assert {h["code"] for h in hits} == {"600035"}            # 600018 写了禁止;600036 不在守卫相位
    # fix(task-12 附带修复 B):明细渲染中文"调入"而非原始 side 字面量"add"——与
    # relative_buy.py 的 excluded 明细、calendar.py:109 的翻译口径三处统一。
    assert hits[0]["severity"] == "warn" and "中证500 调入" in hits[0]["detail"]


def test_card_contract_lint_rebalance_check_is_silent_without_events_file(tmp_path):
    d = _mk(tmp_path, "2026-12-10", {"600035": "# 决策卡\n**Rating**: Hold\n进入P4倾向: Hold\n**入场**: 允许\n"})
    assert not [h for h in card_contract_lint(d) if h["check"] == "卡片契约·调样前夜入场允许"]


def test_card_contract_lint_rebalance_check_is_silent_when_gate_knob_is_off(tmp_path, monkeypatch):
    """minor-8(final whole-branch review):这条 warn 断言『E6 硬门 rebalance_close 会否决』——
    只有 `relative_buy.rebalance_gate` 真的开着才是真话。文档化的单杆回滚(只关这一个开关,
    `calendar.index_rebalance` 仍开着继续产 `index_events.csv`)会让文件在场 + 相位命中,
    但门本身根本不存在;此前这条检查只按『文件在场』判,不看门旋钮,回滚后仍会印出一句假话。"""
    import pandas as pd

    import autoresearch.scan.user_config as uc
    from autoresearch.scan import index_events as ie
    monkeypatch.setattr(uc, "load_user_config", lambda path=None: {"relative_buy": {"rebalance_gate": False}})
    d = _mk(tmp_path, "2026-12-10", {
        "600035": ("# 决策卡 — 600035 楚天 @ 2026-12-10\n**Rating**: Hold\n"
                   "| 评级 | 现价 | 仓位 |\n|---|---|---|\n| Hold | 10 | 10% |\n进入P4倾向: Hold\n"
                   "**入场**: 允许\nFINAL TRANSACTION PROPOSAL: **HOLD**\n"),
    })
    ie.write_index_events(d, pd.DataFrame([
        {"code": "600035", "index_code": "000905", "index_name": "中证500", "side": "add", "ann_date": "20261127",
         "eff_close_date": "20261211", "phase": "passive_close_eve", "source": "csindex", "flow_adv_days": None},
    ], columns=ie.EVENT_COLS))
    assert not [h for h in card_contract_lint(d) if h["check"] == "卡片契约·调样前夜入场允许"]


def test_card_contract_lint_survives_a_corrupt_index_events_file(tmp_path, monkeypatch):
    """I2(final whole-branch review):`index_events.csv` 非原子写,中断的一次会留下一个读不出来
    的半成品。两个生产调用点(`report_sections.py` 的 `self_review_banner`/`_review_extras`)
    都是 `contextlib.suppress(Exception)`——旧代码这里一炸,**全部**卡片契约发现都消失,包括
    与调样毫无关系、这条 branch 添加之前就已经存在的检查(入场行缺失/P4倾向缺失),这些正是
    `l4-card.md` 的 agent 规则依赖的机检。"""
    import autoresearch.scan.user_config as uc
    monkeypatch.setattr(uc, "load_user_config", lambda path=None: {"relative_buy": {"rebalance_gate": True}})
    d = _mk(tmp_path, "2026-12-10", {"600035": "# 决策卡\n**Rating**: Hold\n"})   # 缺入场行 + 缺 P4 行
    (d / "index_events.csv").write_bytes(b"")              # 零字节:中断写留下的半成品
    checks = {h["check"] for h in card_contract_lint(d)}
    assert "卡片契约·入场行缺失" in checks and "卡片契约·P4倾向缺失" in checks
    assert "卡片契约·调样前夜入场允许" not in checks         # 读不出来 → 这一条诚实地什么都不加


def test_card_contract_lint_rebalance_check_says_drop_not_add_for_a_drop_row(tmp_path, monkeypatch):
    """fix(task-12 附带修复 B)的另一半:side="drop" 必须译成"调出"——只测 add→"调入" 分支
    会漏掉一个恒返回"调入"的坏 ternary。minor-8 后按门旋钮门控,见上一条测试的说明。"""
    import pandas as pd

    import autoresearch.scan.user_config as uc
    from autoresearch.scan import index_events as ie
    monkeypatch.setattr(uc, "load_user_config", lambda path=None: {"relative_buy": {"rebalance_gate": True}})
    d = _mk(tmp_path, "2026-12-10", {
        "600018": ("# 决策卡 — 600018 上港 @ 2026-12-10\n**Rating**: Hold\n"
                   "| 评级 | 现价 | 仓位 |\n|---|---|---|\n| Hold | 10 | 10% |\n进入P4倾向: Hold\n"
                   "**入场**: 允许\nFINAL TRANSACTION PROPOSAL: **HOLD**\n"),
    })
    ie.write_index_events(d, pd.DataFrame([
        {"code": "600018", "index_code": "000300", "index_name": "沪深300", "side": "drop", "ann_date": "20261127",
         "eff_close_date": "20261211", "phase": "passive_close_eve", "source": "csindex", "flow_adv_days": None},
    ], columns=ie.EVENT_COLS))
    hits = [h for h in card_contract_lint(d) if h["check"] == "卡片契约·调样前夜入场允许"]
    assert len(hits) == 1 and "调出" in hits[0]["detail"] and "调入" not in hits[0]["detail"]


def test_dossier_change_section_lint(tmp_path):
    # 前日该票有卡 → 今日档案可注入 → 卡缺"变化项" → warn
    prev = _mk(tmp_path, "2026-07-02", {"000001": FULL_OK})
    pd.DataFrame([{"code": "000001", "name": "甲", "sector": "半导体", "lane": "trend",
                   "conviction": 60, "risk": "r"}]).to_csv(prev / "finalists.csv", index=False)
    d = _mk(tmp_path, "2026-07-03", {"000001": FULL_NO_P4})
    out = card_contract_lint(d)
    assert any(x["check"] == "卡片契约·变化项缺失" for x in out)
    (d / "details" / "000001.md").write_text(FULL_OK, encoding="utf-8")
    assert not any(x["check"] == "卡片契约·变化项缺失" for x in card_contract_lint(d))


def _mk_cov_dossier(code):
    """已首覆 + 含合格摘要块 → `injectable_summary` 真 → lint/注入器同判"可注入"。"""
    from autoresearch.dossier import schema
    p = schema.dossier_path(code)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("---\ncode: " + code + "\nname: x\nsector: x\npool_status: active\n"
                 "entered: 2026-07-23\nentry_reason: pinned\ninitiated: 2026-07-23\n"
                 "last_refresh: null\nlast_delta: null\n---\n"
                 f"{schema.SUMMARY_HEAD}\n- 业务: x\n- 驱动: x\n- 带位: x\n"
                 "- 风险: x\n- 催化: x\n- 判例: x\n", encoding="utf-8")


def _mk_cov_dossier_no_summary(code):
    """已首覆但**缺摘要块**——`injectable_summary` 假(与注入器 `_dossier_summary_mark`
    的"不注入"同判)。review R1 important 逮到的缝:此形态下 lint 曾仍报「档案对账缺失」,
    与注入器"根本没告诉 LLM 要写这节"矛盾(假阳)。"""
    from autoresearch.dossier import schema
    p = schema.dossier_path(code)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("---\ncode: " + code + "\nname: x\nsector: x\npool_status: active\n"
                 "entered: 2026-07-23\nentry_reason: pinned\ninitiated: 2026-07-23\n"
                 "last_refresh: null\nlast_delta: null\n---\n", encoding="utf-8")


def test_card_lint_covered_stock_requires_reconcile_section(tmp_path):
    from autoresearch.scan.self_review import card_contract_lint
    d = tmp_path / "details"
    d.mkdir(parents=True)
    _mk_cov_dossier("300857")
    (d / "300857.md").write_text(FULL_OK, encoding="utf-8")        # 有变化项、无档案对账
    warns = [w for w in card_contract_lint(tmp_path)
             if w["check"] == "卡片契约·档案对账缺失"]
    assert len(warns) == 1 and warns[0]["code"] == "300857"


def test_card_lint_covered_stock_with_reconcile_ok(tmp_path):
    from autoresearch.scan.self_review import card_contract_lint
    d = tmp_path / "details"
    d.mkdir(parents=True)
    _mk_cov_dossier("300858")
    (d / "300858.md").write_text(FULL_OK + "\n**档案对账**:驱动无变化;风险无触发;判例一致\n",
                                 encoding="utf-8")
    assert not [w for w in card_contract_lint(tmp_path)
                if w["check"] == "卡片契约·档案对账缺失"]


def test_card_lint_covered_no_summary_not_injectable_no_reconcile_warn(tmp_path):
    """review R1 important 修复回归锁:initiated 但摘要块缺失(不可注入)→ lint 不报
    「档案对账缺失」——与注入器 `_dossier_summary_mark`(同一 `injectable_summary` 门)同判,
    杜绝"没注入却照查"的假阳。"""
    from autoresearch.scan.self_review import card_contract_lint
    d = tmp_path / "details"
    d.mkdir(parents=True)
    _mk_cov_dossier_no_summary("300859")
    (d / "300859.md").write_text(FULL_OK, encoding="utf-8")        # 有变化项、无档案对账
    assert not [w for w in card_contract_lint(tmp_path)
                if w["check"] == "卡片契约·档案对账缺失"]


def test_banner_merges_lint_and_gate_fires(tmp_path):
    """assemble banner 合并 lint(且 gate_fires 留痕)。"""
    from autoresearch.scan.assemble import build_summary
    d = tmp_path / "s"
    d.mkdir()
    (d / "meta.json").write_text("{}", encoding="utf-8")
    pd.DataFrame([{"code": "000001", "name": "甲", "sector": "半导体"}]).to_csv(
        d / "finalists.csv", index=False)
    (d / "details").mkdir()
    (d / "details" / "000001.md").write_text(FULL_NO_P4, encoding="utf-8")
    md = build_summary(d, "2026-07-03", "1200", "20260703_1200")
    assert "卡片契约·P4倾向缺失" in md
    gf = (d / "gate_fires.csv").read_text(encoding="utf-8")
    assert "P4倾向缺失" in gf                                  # lint 在 dump 前合并 → 留痕
