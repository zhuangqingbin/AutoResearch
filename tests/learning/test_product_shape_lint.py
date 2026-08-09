"""product_shape_lint(线 C·产物形状六探针)——合成 staging fixture,零 LLM 零网络。

覆盖:六条各自触发 + 干净目录零输出 + 空/坏目录不炸(presence-gated,绝不抛异常)。
判据镜像生产:pinned 身份只认 finalists.csv 的 lane(judged 存原召回 lane);
force_full 走 l4_card.force_full_card 真身(finalists conviction/lane + L2 n_channels)。
"""
from __future__ import annotations

import json

import pandas as pd

from autoresearch.learning.self_review import (
    product_shape_lint,
    retired_symbol_lint,
    stale_ruler_lint,
    workflow_literal_lint,
)

DATE = "2026-07-17"


def _by(rows: list[dict], check: str) -> list[dict]:
    return [r for r in rows if r["check"] == check]


def _mk_claude_root(tmp_path, **files):
    """构造 `.claude/{skills,agents,workflows}` 形状的 fixture 根目录。

    `files` 例:`{"skills/demo-skill/SKILL.md": "...", "workflows/demo.js": "..."}`
    (相对路径 → 文件内容),自动建父目录。
    """
    root = tmp_path / ".claude"
    for rel, content in files.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")
    return root


def _write_health(d, *, n_full=1, anns=0.2):
    (d / "run_health.json").write_text(json.dumps(
        {"anns_empty_rate": anns, "l4_phases": {"n_cards": 2, "n_earlystop": 1, "n_full": n_full}}),
        encoding="utf-8")


def _mk_clean(tmp_path):
    """干净全量 staging:1 真选 + 1 保送;intel 2 份(=全 finalist 行 2 − 复用 0;保送也派)带 URL;judged 全字段非空。"""
    d = tmp_path / DATE
    (d / "details").mkdir(parents=True)
    pd.DataFrame([
        {"code": "600285", "name": "羚锐制药", "conviction": 70, "lane": "healthy"},
        {"code": "600519", "name": "保送票", "conviction": 50, "lane": "pinned"},
    ]).to_csv(d / "finalists.csv", index=False)
    (d / "_l3_judged.json").write_text(json.dumps([
        # 保送票在 judged 里存的是原召回 lane(trend)——pinned 身份只在 finalists.csv
        {"code": "600519", "name": "保送票", "lane": "trend", "finalist": False,
         "thesis": "持仓否决理由完整", "risk": "有风险段", "catalyst": "有催化段"},
        {"code": "600285", "name": "羚锐制药", "lane": "healthy", "finalist": True,
         "thesis": "真选论点", "risk": "r", "catalyst": "c"},
    ], ensure_ascii=False), encoding="utf-8")
    _write_health(d)
    # 声明行带「网查 N 条」= 生产形态(agent def 的契约锚);限频探针(10)据此对账,
    # 干净盘必须自报且 ≤cap,否则基线会卡在新探针上。
    (d / "_l4_intel_600285.md").write_text(
        "# 活体情报\n## 事件段\n| 2026-07-16 | 事件 | 源 https://example.com/a | 是 | +1 |\n"
        "## 声明行\n网查 12 条 ｜ as-of ≤ 2026-07-17\n",
        encoding="utf-8")
    (d / "_l4_intel_600519.md").write_text(          # 保送票同走 l4-stock 链 → 也有 intel 稿
        "# 活体情报\n## 事件段\n(近 14 天无重大事件)源 https://example.com/b\n"
        "## 声明行\n网查 9 条 ｜ as-of ≤ 2026-07-17\n",
        encoding="utf-8")
    (d / "market_pack.json").write_text(json.dumps(
        {"sector_healthy_top3": [{"industry": "动物保健Ⅱ"}, {"industry": "商用车"}]},
        ensure_ascii=False), encoding="utf-8")
    (d / "market_view.md").write_text("# 市场研判\n宽度极窄,防守为主。\n", encoding="utf-8")
    # 引用密度探针(Wave1 ⑤-5)要求满卡带日期引用 >=6 行——附无价格断言的纯日期引用行,
    # 免得"干净盘"基线卡在新探针上(不带 %/涨跌字样,不触发价格断言对账探针)。
    _cite = "\n".join(f"2026-07-{i:02d} 引用{i}" for i in range(10, 17))
    (d / "details" / "600285.md").write_text(f"〔卡契约 v3〕满卡\n{_cite}", encoding="utf-8")
    (d / "details" / "600519.md").write_text(f"〔卡契约 v3〕保送满卡\n{_cite}", encoding="utf-8")
    return d


# ── 基线:干净零输出 / 空目录不炸 ──────────────────────────────────────────

def test_clean_dir_zero_output(tmp_path):
    assert product_shape_lint(_mk_clean(tmp_path), DATE) == []


def test_empty_and_missing_dir_no_crash(tmp_path):
    assert product_shape_lint(tmp_path, DATE) == []                      # 空目录
    assert product_shape_lint(tmp_path / "nope" / DATE, DATE) == []      # 不存在的目录


def test_corrupt_files_silent(tmp_path):
    d = tmp_path / DATE
    d.mkdir()
    (d / "finalists.csv").write_text("\x00\x01garbage", encoding="utf-8")
    (d / "_l3_judged.json").write_text("{not json", encoding="utf-8")
    (d / "run_health.json").write_text("[]", encoding="utf-8")           # 非 dict
    (d / "market_pack.json").write_text("{bad", encoding="utf-8")
    (d / "market_view.md").write_text("x", encoding="utf-8")
    out = product_shape_lint(d, DATE)                                    # 绝不抛异常
    assert isinstance(out, list) and out == []


# ── 1) 保送§2非空 ──────────────────────────────────────────────────────────

def test_pinned_empty_thesis_warn(tmp_path):
    d = _mk_clean(tmp_path)
    (d / "_l3_judged.json").write_text(json.dumps([
        {"code": "600519", "lane": "trend", "thesis": "", "risk": " ", "catalyst": "有"},
        {"code": "600285", "thesis": "真选论点", "risk": "r", "catalyst": "c"},
    ], ensure_ascii=False), encoding="utf-8")
    rows = _by(product_shape_lint(d, DATE), "产物形状·保送§2空")
    assert len(rows) == 1 and rows[0]["code"] == "600519"
    assert rows[0]["severity"] == "warn"
    assert "thesis" in rows[0]["detail"] and "risk" in rows[0]["detail"]
    assert "catalyst" not in rows[0]["detail"]                           # 非空键不点名


def test_pinned_missing_entry_warn_and_truepick_exempt(tmp_path):
    d = _mk_clean(tmp_path)
    # judged 只剩真选(且真选 thesis 空也不查——本条只盯保送);保送票整条缺 → warn
    (d / "_l3_judged.json").write_text(json.dumps(
        [{"code": "600285", "thesis": "", "risk": "", "catalyst": ""}]), encoding="utf-8")
    rows = _by(product_shape_lint(d, DATE), "产物形状·保送§2空")
    assert len(rows) == 1 and rows[0]["code"] == "600519" and "无条目" in rows[0]["detail"]
    # judged 文件整个缺 → presence-gated,本条静默
    (d / "_l3_judged.json").unlink()
    assert _by(product_shape_lint(d, DATE), "产物形状·保送§2空") == []


# ── 2) force_full 探针 ────────────────────────────────────────────────────

def test_force_full_silent_warn(tmp_path):
    d = _mk_clean(tmp_path)
    _write_health(d, n_full=0)                       # 保送票恒命中 force_full,但 n_full=0
    rows = _by(product_shape_lint(d, DATE), "产物形状·force_full未生效")
    assert len(rows) == 1 and rows[0]["severity"] == "warn"
    assert "600519" in rows[0]["detail"] and "n_full=0" in rows[0]["detail"]


def test_force_full_channels_path_via_l2(tmp_path):
    d = _mk_clean(tmp_path)                          # 去掉保送票 → 只剩强先验通路②
    pd.DataFrame([{"code": "600285", "name": "羚锐制药", "conviction": 72, "lane": "healthy"}],
                 ).to_csv(d / "finalists.csv", index=False)
    pd.DataFrame([{"code": "600285", "n_channels": 5, "l2_lane_reserved": False}],
                 ).to_csv(d / "L2_gbdt_top200.csv", index=False)
    _write_health(d, n_full=0)
    rows = _by(product_shape_lint(d, DATE), "产物形状·force_full未生效")
    assert len(rows) == 1 and "600285" in rows[0]["detail"]


def test_force_full_zero_hits_info(tmp_path):
    d = _mk_clean(tmp_path)                          # 无保送、conviction<70 → 0 命中
    pd.DataFrame([{"code": "600285", "name": "羚锐制药", "conviction": 55, "lane": "healthy"}],
                 ).to_csv(d / "finalists.csv", index=False)
    out = product_shape_lint(d, DATE)
    rows = _by(out, "产物形状·force_full零命中")
    assert len(rows) == 1 and rows[0]["severity"] == "info" and DATE in rows[0]["detail"]
    assert _by(out, "产物形状·force_full未生效") == []


def test_force_full_reused_card_not_counted(tmp_path):
    d = _mk_clean(tmp_path)                          # 唯一命中票(保送)是 ♻️ 复用卡 → 派发时
    (d / "details" / "600519.md").write_text(        # 已跳过,不算命中 → 0 命中走 info
        "♻️ **复用卡**(源 2026-07-15)\n〔卡契约 v3〕", encoding="utf-8")
    _write_health(d, n_full=0)
    out = product_shape_lint(d, DATE)
    assert _by(out, "产物形状·force_full未生效") == []
    assert len(_by(out, "产物形状·force_full零命中")) == 1


# ── 3) intel 稿数 ─────────────────────────────────────────────────────────

def test_intel_count_mismatch_warn(tmp_path):
    d = _mk_clean(tmp_path)                          # 全 finalist 行 2 − 复用 0 = 期望 2,实际 3(多出非名单稿)
    (d / "_l4_intel_000002.md").write_text("不在 finalists 的稿 https://x.com/1\n", encoding="utf-8")
    rows = _by(product_shape_lint(d, DATE), "产物形状·intel稿数不符")
    assert len(rows) == 1 and rows[0]["severity"] == "warn"
    assert "3 份" in rows[0]["detail"] and "期望 2" in rows[0]["detail"]
    # 少派同样逮:删掉保送稿 → 1 份 ≠ 期望 2
    (d / "_l4_intel_000002.md").unlink()
    (d / "_l4_intel_600519.md").unlink()
    rows2 = _by(product_shape_lint(d, DATE), "产物形状·intel稿数不符")
    assert len(rows2) == 1 and "1 份" in rows2[0]["detail"] and "期望 2" in rows2[0]["detail"]


def test_intel_probe_variant_not_counted(tmp_path):
    """`_probe` 变体稿不计入 intel 稿数(600285 正稿 + probe 变体 → 仍算 1 份)。

    Wave9 R5:本用例原名 test_intel_count_reuse_subtracted,锁的是"期望数按 ♻️
    复用扣减"(TTL 复用已退役,该扣减已从 probe 3 删除,见 self_review.py)——但它
    **同时**顺带锁住了一条无关的独立契约(`_probe` 变体不计数),删整个用例会静默
    丢失后者的回归覆盖。这里只删复用前提(finalists 行数覆盖 + ♻️ 卡),保留
    `_probe` 断言,改用 `_mk_clean` 默认的 2 行 baseline(期望数天然 = 2,
    不依赖复用扣减也成立)。
    """
    d = _mk_clean(tmp_path)
    assert _by(product_shape_lint(d, DATE), "产物形状·intel稿数不符") == []
    (d / "_l4_intel_600285_probe.md").write_text("变体稿 https://x.com/p", encoding="utf-8")
    assert _by(product_shape_lint(d, DATE), "产物形状·intel稿数不符") == []


def test_intel_disabled_no_output(tmp_path):
    d = _mk_clean(tmp_path)
    (d / "_l4_intel_600285.md").unlink()             # 0 份 intel = 未启用 → 本条不出
    (d / "_l4_intel_600519.md").unlink()
    assert _by(product_shape_lint(d, DATE), "产物形状·intel稿数不符") == []


def test_intel_pretrim_archive_not_counted(tmp_path):
    """复核回归修复(W9-B2-fix):TRIMMED 裁前留档 `<code>.pretrim` 不计入 intel 稿数。

    留档命名刻意不以 `.md` 结尾 —— 已用真实 `Path.glob` 验证过:带 `.md` 的双后缀
    命名(如 `<code>.pretrim.md`)会被 `glob("_l4_intel_*.md")` 顺带命中,让这条探针
    把留档当成第 3 份独立情报稿(600285/600519 之外)而虚报「intel 稿数不符」;不带
    `.md` 的名字对这条裸 glob(以及同文件「intel零URL」探针用的同一份 `intel_files`)
    天然不可见,不需要在探针里加任何特判。
    """
    d = _mk_clean(tmp_path)
    assert _by(product_shape_lint(d, DATE), "产物形状·intel稿数不符") == []
    (d / "_l4_intel_600285.pretrim").write_text(
        "裁剪前留档全文(供 lint 审计,非独立情报稿)https://x.com/pretrim", encoding="utf-8")
    out = product_shape_lint(d, DATE)
    assert _by(out, "产物形状·intel稿数不符") == []
    assert _by(out, "产物形状·intel零URL") == []      # 顺带验证:也不会被当成第二份稿重复审计


# ── 4) anns 去伪 ──────────────────────────────────────────────────────────

def test_anns_expected_info(tmp_path):
    d = _mk_clean(tmp_path)
    _write_health(d, anns=1.0)
    rows = _by(product_shape_lint(d, DATE), "产物形状·anns去伪")
    assert len(rows) == 1 and rows[0]["severity"] == "info"
    assert "no-permission" in rows[0]["detail"]


# ── 5) market_view 防锚定 ─────────────────────────────────────────────────

def test_market_view_anchor_leak_warn(tmp_path):
    d = _mk_clean(tmp_path)
    (d / "market_view.md").write_text("# 市场研判\n看多**动物保健Ⅱ**,逢低配置。\n",
                                      encoding="utf-8")
    rows = _by(product_shape_lint(d, DATE), "产物形状·market_view防锚定")
    assert len(rows) == 1 and rows[0]["severity"] == "warn"
    assert "动物保健Ⅱ" in rows[0]["detail"] and "商用车" not in rows[0]["detail"]


# ── 6) intel 零URL ────────────────────────────────────────────────────────

def test_intel_zero_url_warn(tmp_path):
    d = _mk_clean(tmp_path)
    (d / "_l4_intel_600285.md").write_text("# 活体情报\n## 事件段\n(无链接)\n", encoding="utf-8")
    rows = _by(product_shape_lint(d, DATE), "产物形状·intel零URL")
    assert len(rows) == 1 and rows[0]["severity"] == "warn" and rows[0]["code"] == "600285"
    # 修回带 URL → 清零
    (d / "_l4_intel_600285.md").write_text("源 http://example.com/a\n", encoding="utf-8")
    assert _by(product_shape_lint(d, DATE), "产物形状·intel零URL") == []


def test_return_row_shape(tmp_path):
    d = _mk_clean(tmp_path)
    _write_health(d, anns=1.0)
    rows = product_shape_lint(d, DATE)
    assert rows and all(set(r) == {"check", "severity", "detail", "code"} for r in rows)


# ── 10) intel 限频对账(Wave6 Q1-②)───────────────────────────────────────
# 07-24 实测:11 稿自报 18/18/17/15/20/26/23/16/17/21/25 条,cap=15 → 10 只超限,
# 最高 26(cap 的 173%)。声明行一直在写这个数,全仓却没有任何消费者(pr_20260714_007)。

def test_intel_query_cap_over_warn(tmp_path):
    d = _mk_clean(tmp_path)
    (d / "user_config_echo.json").write_text('{"l4_intel": {"max_queries": 15}}', encoding="utf-8")
    (d / "_l4_intel_600285.md").write_text(
        "源 https://e.com/a\n## 声明行\n网查 26 条 ｜ as-of ≤ 2026-07-17\n", encoding="utf-8")

    rows = _by(product_shape_lint(d, DATE), "产物形状·intel限频")

    assert len(rows) == 1 and rows[0]["severity"] == "warn" and rows[0]["code"] == "600285"
    assert "26" in rows[0]["detail"] and "15" in rows[0]["detail"]


def test_intel_query_cap_at_cap_is_clean(tmp_path):
    """恰好等于 cap 不算超(07-24 的 600018 就是 15/15)。"""
    d = _mk_clean(tmp_path)
    (d / "user_config_echo.json").write_text('{"l4_intel": {"max_queries": 15}}', encoding="utf-8")
    (d / "_l4_intel_600285.md").write_text(
        "源 https://e.com/a\n## 声明行\n网查 15 条\n", encoding="utf-8")

    assert _by(product_shape_lint(d, DATE), "产物形状·intel限频") == []


def test_intel_query_cap_missing_declaration_warn(tmp_path):
    """没自报 = 无法对账,必须显式 flag —— 「文件/字段缺失是弱证据」,不得以缺推断合规。"""
    d = _mk_clean(tmp_path)
    (d / "_l4_intel_600285.md").write_text("源 https://e.com/a\n(没有声明行)\n", encoding="utf-8")

    rows = _by(product_shape_lint(d, DATE), "产物形状·intel限频")

    assert len(rows) == 1 and rows[0]["code"] == "600285"
    assert "未自报" in rows[0]["detail"]


def test_intel_query_cap_reads_echo_config(tmp_path):
    """cap 取当日 user_config_echo,不是硬编码 15 —— 改了 config 就该按新 cap 对账。

    变异验证:把读 echo 那几行删掉退回硬编码 15,本测试变红(20 条在 cap=25 下不该报)。
    """
    d = _mk_clean(tmp_path)
    (d / "user_config_echo.json").write_text('{"l4_intel": {"max_queries": 25}}', encoding="utf-8")
    (d / "_l4_intel_600285.md").write_text(
        "源 https://e.com/a\n## 声明行\n网查 20 条\n", encoding="utf-8")

    assert _by(product_shape_lint(d, DATE), "产物形状·intel限频") == []


# ── 11) 退役符号·指令性引用(Wave11 D4)─────────────────────────────────────
# T21 台账(docs/research/2026-08-07-skill-tombstone-ledger.md)的自动守卫:文档不得再教人
# 跑一个已经不存在的命令/开关。RETIRED_SYMBOLS/墓碑标记词表是给定契约,不在这里重新发明。

def test_retired_symbol_instructive_reference_fails(tmp_path):
    root = _mk_claude_root(tmp_path, **{
        "skills/demo-skill/SKILL.md": "步骤:`python -m autoresearch.scan.progress <date>` 看进度。\n",
    })
    rows = retired_symbol_lint(root)
    assert len(rows) == 1 and rows[0]["severity"] == "fail"
    assert "scan.progress" in rows[0]["detail"] and "SKILL.md:1" in rows[0]["detail"]


def test_retired_symbol_tombstone_same_line_passes(tmp_path):
    """墓碑(同行有退役标记)→ pass —— brief 原句范例。"""
    root = _mk_claude_root(tmp_path, **{
        "skills/demo-skill/SKILL.md": "observe_watchlist 已退役,勿再跑。\n",
    })
    assert retired_symbol_lint(root) == []


def test_retired_symbol_whitelist_is_per_line_not_per_file(tmp_path):
    """墓碑白名单按行、不按文件:同一文档里墓碑行放行、活指令行仍拦下——不能因文件里有
    一处墓碑就让全文件的同名符号都免检(否则退役符号提过一次「已退役」,后面几百行怎么
    教人用它都会被放行)。
    """
    text = "observe_watchlist 已退役,勿再跑。\n补跑 observe_watchlist 一次看看。\n"
    root = _mk_claude_root(tmp_path, **{"skills/demo-skill/SKILL.md": text})
    rows = retired_symbol_lint(root)
    assert len(rows) == 1
    assert "SKILL.md:2" in rows[0]["detail"]          # 只拦第 2 行(活指令),第 1 行(墓碑)不报


def test_retired_symbol_scans_agents_and_workflows_too(tmp_path):
    """扫描域是 skills/agents/workflows 三处,不止 skills。"""
    root = _mk_claude_root(tmp_path, **{
        "agents/demo.md": "调用 l4_reuse 逻辑重放历史卡。\n",
        "workflows/demo.js": "// 走 sector_brief_mode=finalist_only 通道\n",
    })
    rows = retired_symbol_lint(root)
    hit_files = {r["detail"].split(":")[0] for r in rows}
    assert hit_files == {"demo.md", "demo.js"}


def test_retired_symbol_ignores_non_md_js_files(tmp_path):
    """扫描域限定 .md(skills/agents)与 .js(workflows)—— jsonc 配置注释等不算文档,
    不该把配置文件里的措辞差异(如「已删」而非五个给定标记词)误判成文档 bug。
    """
    root = _mk_claude_root(tmp_path, **{
        "skills/demo-skill/scan_config.jsonc": "// l4_reuse 已删\n",
    })
    assert retired_symbol_lint(root) == []


def test_retired_symbol_missing_root_no_crash(tmp_path):
    assert retired_symbol_lint(tmp_path / "nope") == []
    assert retired_symbol_lint(tmp_path) == []        # 存在但空(无 skills/agents/workflows 子目录)


# ── 12) workflow AGENT_DEFAULTS 外内联字面量(Wave11 D4)────────────────────

_AGENT_DEFAULTS_JS = (
    "const cfg = {}\n"
    "const AGENT_DEFAULTS = {\n"
    "  gp_shell:      { model: 'sonnet', effort: 'low' },\n"
    "  l3_repair:     { effort: 'medium' },\n"
    "}\n"
    "const AG = (role) => ({ ...(AGENT_DEFAULTS[role] || {}), ...((cfg.agents || {})[role] || {}) })\n"
)


def test_workflow_literal_outside_block_fails(tmp_path):
    text = _AGENT_DEFAULTS_JS + "\nawait agent({ agentType: 'x', model: 'haiku' })\n"
    root = _mk_claude_root(tmp_path, **{"workflows/demo.js": text})
    rows = workflow_literal_lint(root)
    assert len(rows) == 1 and rows[0]["severity"] == "fail"
    assert "demo.js:8" in rows[0]["detail"]


def test_workflow_literal_inside_block_passes(tmp_path):
    root = _mk_claude_root(tmp_path, **{"workflows/demo.js": _AGENT_DEFAULTS_JS})
    assert workflow_literal_lint(root) == []


def test_workflow_literal_effort_outside_block_fails(tmp_path):
    """规则同时管 `effort:`,不止 `model:`。"""
    text = _AGENT_DEFAULTS_JS + "\nconst r = await rerun({ effort: 'max' })\n"
    root = _mk_claude_root(tmp_path, **{"workflows/demo.js": text})
    rows = workflow_literal_lint(root)
    assert len(rows) == 1 and "demo.js:8" in rows[0]["detail"]


def test_workflow_literal_no_agent_defaults_block_skipped(tmp_path):
    """没有 AGENT_DEFAULTS 表的 workflow(如 t1-review.js 走独立的 cfg.agents.t1_diag/t1_synth
    通道)天然不受本规则约束——presence-gated 跳过,不是本规则的检查对象。
    """
    text = "const r = { effort: AG.t1_diag?.effort ?? 'high', model: 'haiku' }\n"
    root = _mk_claude_root(tmp_path, **{"workflows/demo.js": text})
    assert workflow_literal_lint(root) == []


def test_workflow_literal_cannot_be_filtered_by_grep_v_agent_defaults(tmp_path):
    """回归 brief 点名的坑:表内的行本身不含 "AGENT_DEFAULTS" 这个词,不能靠字符串排除法
    判块,必须真正算出块的起止行号区间——这里显式验证块内那行确实不含该词、但仍被正确
    判定为「在块内」而 pass。
    """
    root = _mk_claude_root(tmp_path, **{"workflows/demo.js": _AGENT_DEFAULTS_JS})
    src = (root / "workflows" / "demo.js").read_text(encoding="utf-8")
    gp_shell_line = src.splitlines()[2]
    assert "model: '" in gp_shell_line and "AGENT_DEFAULTS" not in gp_shell_line
    assert workflow_literal_lint(root) == []


def test_workflow_literal_missing_root_no_crash(tmp_path):
    assert workflow_literal_lint(tmp_path / "nope") == []


# ── 接线:product_shape_lint 经 scan_dir 祖先目录自动接上 11)/12)(Wave11 D4)────────

def test_product_shape_lint_wires_retired_symbol_and_workflow_literal(tmp_path):
    """product_shape_lint 用 `scan_dir.parent.parent.parent / ".claude"` 推导 .claude 根
    (与 usage_reconcile_lint 同手法)——生产 scan_dir == context/scan/<date> 时上三级 ==
    仓库根。这里摆一个同构的三级目录验证接线本身,不只测两个独立函数。
    """
    scan_dir = tmp_path / "context" / "scan" / DATE
    scan_dir.mkdir(parents=True)
    _mk_claude_root(tmp_path, **{
        "skills/demo-skill/SKILL.md": "跑 `python -m autoresearch.scan.progress <date>`。\n",
    })
    rows = _by(product_shape_lint(scan_dir, DATE), "产物形状·退役符号指令性引用")
    assert len(rows) == 1 and "scan.progress" in rows[0]["detail"]


def test_product_shape_lint_existing_fixture_shape_unaffected_by_doc_lints(tmp_path):
    """既有 `_mk_clean` 式 scan_dir(tmp_path 下 1 级)上三级不构成真 `.claude` ——presence-gated
    静默跳过,新规则不会用仓库当前 `.claude` 内容污染单跑目录的产物形状断言(回归锁)。
    """
    d = _mk_clean(tmp_path)
    rows = product_shape_lint(d, DATE)
    assert _by(rows, "产物形状·退役符号指令性引用") == []
    assert _by(rows, "产物形状·workflow内联字面量") == []


# ── T14:旧尺(fwd_2_oc)裸写防复发 lint(新增文件粒度)──────────────────────────

def _mk_git_repo(tmp_path, tracked: dict, new: dict):
    """建一个真 git 仓库:`tracked` 先 commit(=存量文件),`new` 只写盘不 commit(=新增)。

    lint 的「新增文件」判据走真 git plumbing,所以这里必须是真仓库——用注入的假名单测
    等于把被测的那条腿换掉(变异探针会证明:注释掉 git 那腿,存量文件不追溯的用例立刻变红)。
    """
    import subprocess
    root = tmp_path / "repo"
    root.mkdir()
    run = lambda *a: subprocess.run(["git", *a], cwd=root, check=True,
                                    capture_output=True, text=True)
    run("init", "-q")
    run("config", "user.email", "t@t.t")
    run("config", "user.name", "t")
    for rel, content in tracked.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")
    if tracked:
        run("add", "-A")
        run("commit", "-qm", "base")
    for rel, content in new.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")
    return root


_STALE = "产物形状·旧尺裸写"


def test_stale_ruler_new_file_bare_write_fails(tmp_path):
    """新增模块裸写 `fwd_2_oc` 且无「参考尺」注记 → fail(本 lint 的存在理由)。"""
    root = _mk_git_repo(tmp_path, {"keep.py": "x = 1\n"},
                        {"autoresearch/scan/newmod.py": "COL = 'fwd_2_oc'\n"})
    rows = _by(stale_ruler_lint(root), _STALE)
    assert len(rows) == 1, rows
    assert "newmod.py" in rows[0]["detail"] and rows[0]["severity"] == "fail"


def test_stale_ruler_new_file_with_reference_mark_passes(tmp_path):
    """同样是新增文件,但带「参考尺」注记 → 放行(合法情形要有一个合法的写法)。"""
    root = _mk_git_repo(tmp_path, {"keep.py": "x = 1\n"}, {
        "autoresearch/scan/newmod.py":
            "# 参考尺 fwd_2_oc(旧主尺,降参考不删);主尺走 ruler.MAIN_RULER\n"
            "REF = 'fwd_2_oc'\n",
    })
    assert _by(stale_ruler_lint(root), _STALE) == []


def test_stale_ruler_existing_file_not_retroactive(tmp_path):
    """存量(已 commit)文件裸写旧尺 → **不追溯**。286 处历史命中不得天天报警。"""
    root = _mk_git_repo(tmp_path, {"autoresearch/old.py": "COL = 'fwd_2_oc'\n"}, {})
    assert _by(stale_ruler_lint(root), _STALE) == []


def test_stale_ruler_existing_file_edited_still_not_retroactive(tmp_path):
    """存量文件被**改动**(非新增)仍不追溯——粒度确实是「新增文件」,不是「改动文件」。"""
    root = _mk_git_repo(tmp_path, {"autoresearch/old.py": "COL = 'fwd_2_oc'\n"}, {})
    (root / "autoresearch" / "old.py").write_text(
        "COL = 'fwd_2_oc'\nEXTRA = 2\n", encoding="utf-8")
    assert _by(stale_ruler_lint(root), _STALE) == []


def test_stale_ruler_claude_live_main_ruler_claim_fails(tmp_path):
    """`.claude/` 文本出现「主尺 fwd_2_oc」措辞 → fail,**不论文件新旧**。

    这是 T13 治的那个病(PANORAMA:704「权重校准主尺仍 fwd_2_oc」)的复发探针:
    活指令句式与「新增文件」无关,存量文件里写出来同样有害。
    """
    root = _mk_git_repo(tmp_path, {
        ".claude/skills/demo/SKILL.md": "权重校准主尺仍 fwd_2_oc,两把尺勿混。\n",
    }, {})
    rows = _by(stale_ruler_lint(root), _STALE)
    assert len(rows) == 1, rows
    assert "主尺" in rows[0]["detail"] and rows[0]["severity"] == "fail"


def test_stale_ruler_claude_lineage_wording_passes(tmp_path):
    """`.claude/` 里的**沿革**写法(「当时是 fwd_2_oc,现 gap_c1_o2」)→ 放行。

    没有这条,T13 刚写好的沿革注记会被自己的 lint 天天判违规(「先给合法情形一个标记」)。
    """
    root = _mk_git_repo(tmp_path, {
        ".claude/skills/demo/SKILL.md":
            "对齐主尺 `MAIN_RULER`——当时是 `fwd_2_oc`,现 `gap_c1_o2`,两者同样只需 D+2。\n",
    }, {})
    assert _by(stale_ruler_lint(root), _STALE) == []


def test_stale_ruler_live_repo_is_clean():
    """**活体验收**:T13 扫完后,本仓库真实 `.claude/` 树 + 未提交新增文件必须零命中。

    这条是「探针有没有灯」的对手方——lint 若写得过宽(比如把沿革注记也判违规),
    它会立刻在真实仓库上变红,而不是等到下一个人踩坑。
    """
    from pathlib import Path
    repo = Path(__file__).resolve().parents[2]
    assert _by(stale_ruler_lint(repo), _STALE) == []


def test_stale_ruler_non_git_dir_no_crash(tmp_path):
    """非 git 目录 / 无 git 可执行 → presence-gated 静默跳过,绝不抛异常。"""
    assert stale_ruler_lint(tmp_path / "nope") == []
    assert stale_ruler_lint(tmp_path) == []


def test_product_shape_lint_wires_stale_ruler(tmp_path):
    """接线锁:product_shape_lint 经 `scan_dir` 上三级推**仓库根**接上 14)。

    与 11)/12) 不同,本探针要的是仓库根(它既查 `.claude` 也要在根上跑 git),接线写错一级
    (传成 `.claude` 根)这条会立刻变红。
    """
    import subprocess
    repo = tmp_path
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "t@t.t"], cwd=repo, check=True,
                   capture_output=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=repo, check=True,
                   capture_output=True)
    scan_dir = repo / "context" / "scan" / DATE
    scan_dir.mkdir(parents=True)
    (repo / ".claude" / "skills" / "demo").mkdir(parents=True)
    (repo / ".claude" / "skills" / "demo" / "SKILL.md").write_text(
        "权重校准主尺仍 fwd_2_oc,两把尺勿混。\n", encoding="utf-8")
    rows = _by(product_shape_lint(scan_dir, DATE), "产物形状·旧尺裸写")
    assert len(rows) >= 1 and any("主尺" in r["detail"] for r in rows), rows


def test_stale_ruler_recall_on_real_pre_t13_offenders(tmp_path):
    """**召回锁(反假绿灯)**:拿 T13 之前(commit 551d236)那 8 行真实违规原文回测,必须 8/8 逮到。

    第一版判据写成 `主尺仍 fwd_2_oc` 一类的精巧正则,回测只逮到 3/8 —— 一个逮不住自己
    那条病的探针就是假绿灯(家训:「绿灯不等于有灯」)。这条把召回钉死;判据若被改回
    窄句式匹配,它立刻变红。

    ⚠️ 本条**必须驱动 `stale_ruler_lint` 真身**:第一版写成"直接拿模块常量自己比对",
    变异测试当场证明它零鉴别力——把 lint 体内的判据收窄回 3/8 的正则,它照样绿。
    """
    import subprocess
    from autoresearch.learning.self_review import (
        _STALE_RULER_LIVE_MARK, _STALE_RULER_TOKEN,
    )
    files = (".claude/skills/scan-market/SKILL.md", ".claude/skills/scan-retro/SKILL.md",
             ".claude/skills/scan-retro/retro-playbook.md", "docs/PANORAMA.md")
    hist = {}
    for rel in files:
        r = subprocess.run(["git", "show", f"551d236:{rel}"], capture_output=True, text=True)
        if r.returncode != 0:                       # 浅克隆等拿不到旧对象 → 本条无从断言
            return
        hist[rel] = r.stdout
    # 历史原文全部 **commit 进** tmp 仓库 = 存量文件 → 规则①(新增粒度)天然不开火,
    # 命中数纯粹归功于规则②,这样才量得准。
    root = _mk_git_repo(tmp_path, hist, {})
    expected = sum(1 for text in hist.values() for ln in text.splitlines()
                   if _STALE_RULER_TOKEN in ln and _STALE_RULER_LIVE_MARK in ln)
    assert expected == 8, f"历史违规行数变了({expected}),基线需复核"
    rows = _by(stale_ruler_lint(root), _STALE)
    assert len(rows) == 8, f"召回 {len(rows)}/8 —— 判据太窄,会漏掉真实病灶写法:{rows}"
    # PANORAMA 必须在扫描域内(本病最刺眼的 :704 就长在它身上,只守 .claude 等于守错门)
    assert any("PANORAMA" in r["detail"] for r in rows), rows


# ── 12b) Wave12-T33:lint 扩面到 `.claude/skills/**/*.md` 的 Agent(model=…) 字面量 ──


def test_skill_agent_model_literal_fails(tmp_path):
    """`lite-playbook.md:178` 型字面量必红 —— skill 文档是活指令,写死档位=第二个事实源。"""
    md = "## 与 scan-market 的衔接\n\nL4 每只一个 `Agent(model='opus')` 调本 skill。\n"
    root = _mk_claude_root(tmp_path, **{"skills/stock-research/lite-playbook.md": md})
    rows = workflow_literal_lint(root)
    hits = [r for r in rows if r["check"] == "产物形状·skill内联字面量"]
    assert len(hits) == 1 and hits[0]["severity"] == "fail"
    assert "lite-playbook.md:3" in hits[0]["detail"]


def test_skill_agent_effort_literal_fails(tmp_path):
    md = "派发时写 `Agent(agentType='l4-card', effort=\"max\")`。\n"
    root = _mk_claude_root(tmp_path, **{"skills/x/SKILL.md": md})
    hits = [r for r in workflow_literal_lint(root)
            if r["check"] == "产物形状·skill内联字面量"]
    assert len(hits) == 1


def test_skill_literal_with_exemption_mark_passes(tmp_path):
    """加豁免注记必绿 —— 先给合法情形一个标记,再谈加严检查(2026-07-27 家训)。"""
    md = "示例(仅作示意):`Agent(model='opus')`。\n"
    root = _mk_claude_root(tmp_path, **{"skills/x/SKILL.md": md})
    assert [r for r in workflow_literal_lint(root)
            if r["check"] == "产物形状·skill内联字面量"] == []


def test_skill_prose_mentioning_effort_is_not_flagged(tmp_path):
    """散文里提 effort/model 不算违规 —— 判据是 `Agent(` 调用形态,不是关键词。

    这条是探针的**误报对照**:没有它,规则很容易被写成"出现 effort 就报",
    然后天天报警天天被无视(狼来了,把探针公信力一起磨掉)。
    """
    md = "各 stage 的 effort 与 model 见 scan_config.jsonc;l4_card 当前 effort=max。\n"
    root = _mk_claude_root(tmp_path, **{"skills/x/SKILL.md": md})
    assert [r for r in workflow_literal_lint(root)
            if r["check"] == "产物形状·skill内联字面量"] == []


def test_skill_lint_missing_dir_no_crash(tmp_path):
    root = _mk_claude_root(tmp_path, **{"workflows/demo.js": _AGENT_DEFAULTS_JS})
    assert [r for r in workflow_literal_lint(root)
            if r["check"] == "产物形状·skill内联字面量"] == []


def test_real_repo_skills_are_clean_of_agent_literals():
    """活体验收:真的 `.claude/skills/**` 现在必须一条不剩(lite-playbook 已随本波修正)。

    造 fixture 能红不代表生产干净 —— 这条读真目录,退化了会当场红。
    """
    hits = [r for r in workflow_literal_lint(".claude")
            if r["check"] == "产物形状·skill内联字面量"]
    assert hits == [], f"生产 skill 文档仍有内联档位字面量:{[h['detail'] for h in hits]}"
