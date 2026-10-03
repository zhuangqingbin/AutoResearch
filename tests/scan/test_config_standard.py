"""scan_config 标准(九条规则)的机器判定:`autoresearch/scan/config_standard.py`。

第一条用例是验收:生产 `scan_config.jsonc` 必须零违规。其余每条规则一个**变异探针**:把现场
改坏一处,对应规则必须亮红 —— 「绿灯不等于有灯」,每条探针都在证明这盏灯真的会红。
规则原文见 `.claude/skills/scan-market/SKILL.md`「配置」节。
"""
from __future__ import annotations

import types
from pathlib import Path

import pytest

from autoresearch.contracts import scan_config as reg
from autoresearch.scan import config_standard as std

REPO = Path(__file__).resolve().parents[2]
PROD = REPO / reg.CONFIG_PATH
TEXT = PROD.read_text(encoding="utf-8")


def rules(violations) -> set[str]:
    return {v.rule for v in violations}


def _drop_line(text: str, needle: str) -> str:
    lines = text.split("\n")
    hits = [i for i, line in enumerate(lines) if needle in line]
    assert len(hits) == 1, f"{needle!r} 命中 {len(hits)} 行"
    del lines[hits[0]]
    return "\n".join(lines)


def _edit_line(text: str, needle: str, new_line: str) -> str:
    lines = text.split("\n")
    hits = [i for i, line in enumerate(lines) if needle in line]
    assert len(hits) == 1, f"{needle!r} 命中 {len(hits)} 行"
    lines[hits[0]] = new_line
    return "\n".join(lines)


# ───────────────────────── 验收:生产文件零违规 ─────────────────────────


def test_production_config_has_no_violations():
    violations = std.lint_all(PROD, repo_root=REPO)
    assert violations == [], "\n".join(str(v) for v in violations)


def test_text_rules_pass_on_production_text():
    assert std.lint_text(TEXT) == []


# ───────────────────────── R4:文件 ↔ 注册表键集相等 ─────────────────────────


def test_r4_missing_key_in_file_is_red():
    mutated = _drop_line(TEXT, '"min_list_days": 0')
    assert "R4" in rules(std.lint_text(mutated))


def test_r4_unregistered_key_in_file_is_red():
    mutated = TEXT.replace('    "min_list_days": 0', '    "min_list_days": 0,\n    "ghost_key": 1,          // 野键')
    mutated = mutated.replace('"ghost_key": 1,          // 野键', '"ghost_key": 1          // 野键').replace(
        '    "min_list_days": 0,\n', '    "min_list_days": 0,                // 上市天数门槛(0 = 关)\n', 1)
    assert "R4" in rules(std.lint_text(mutated))


def test_r4_missing_group_child_is_red():
    mutated = _drop_line(TEXT, '"per_sector": 2')
    assert "R4" in rules(std.lint_text(mutated))


# ───────────────────────── R5:注释格式 ─────────────────────────


def test_r5_leaf_without_trailing_comment_is_red():
    mutated = _edit_line(TEXT, '"recall_n": 1000', '    "recall_n": 1000,')
    assert "R5" in rules(std.lint_text(mutated))


def test_r5_paragraph_comment_inside_block_is_red():
    mutated = TEXT.replace('    "recall_n": 1000,', '    // 2026-07-11 channel_audit 累计裁决\n    "recall_n": 1000,')
    assert "R5" in rules(std.lint_text(mutated))


def test_r5_date_in_trailing_comment_is_red():
    mutated = _edit_line(TEXT, '"recall_n": 1000', '    "recall_n": 1000,                  // L1 召回收口数(2026-07-11 定)')
    assert "R5" in rules(std.lint_text(mutated))


def test_r5_history_word_in_trailing_comment_is_red():
    mutated = _edit_line(TEXT, '"recall_n": 1000', '    "recall_n": 1000,                  // L1 召回收口数;回滚杆 = 改回 800')
    assert "R5" in rules(std.lint_text(mutated))


def test_r5_overlong_trailing_comment_is_red():
    mutated = _edit_line(TEXT, '"recall_n": 1000', '    "recall_n": 1000,                  // ' + "长" * 81)
    assert "R5" in rules(std.lint_text(mutated))


def test_r5_block_header_not_rendered_from_registry_is_red():
    header = reg.render_block_header("l0")
    mutated = TEXT.replace(header, "  // ── l0 · 选集硬门 ── 生效点 我随便写的")
    assert "R5" in rules(std.lint_text(mutated))


def test_r5_file_header_longer_than_eight_lines_is_red():
    extra = "\n".join("  // 多余的头注 %d" % i for i in range(9))
    mutated = TEXT.replace("{\n", "{\n" + extra + "\n", 1)
    assert "R5" in rules(std.lint_text(mutated))


def test_r5_structured_block_comment_still_obeys_text_rules():
    mutated = _edit_line(TEXT, '"l3_rank":       { "tier": "critical" },',
                         '    "l3_rank":       { "tier": "critical" },      // L3 精排(2026-07-06 起 opus)')
    assert "R5" in rules(std.lint_text(mutated))


# ───────────────────────── R6:分区与顺序 ─────────────────────────


def test_r6_block_out_of_order_is_red():
    a = reg.render_block_header("l0") + '\n' + TEXT.split(reg.render_block_header("l0") + "\n", 1)[1].split("\n  },\n", 1)[0] + "\n  },\n"
    b = reg.render_block_header("funnel") + '\n' + TEXT.split(reg.render_block_header("funnel") + "\n", 1)[1].split("\n  },\n", 1)[0] + "\n  },\n"
    mutated = TEXT.replace(a, "@@A@@").replace(b, a).replace("@@A@@", b)
    assert mutated != TEXT
    assert "R6" in rules(std.lint_text(mutated))


def test_r6_zone_header_in_wrong_place_is_red():
    zone2 = reg.render_zone_header(reg.ZONE_RUNTIME)
    mutated = TEXT.replace("\n" + zone2 + "\n", "\n", 1)
    mutated = mutated.replace(reg.render_block_header("retention"), zone2 + "\n\n" + reg.render_block_header("retention"))
    assert "R6" in rules(std.lint_text(mutated))


# ───────────────────────── R3:单侧宿主的键要标宿主 ─────────────────────────


def test_r3_single_host_key_without_tag_is_red():
    mutated = _edit_line(TEXT, '"streaming_l4": true', '    "streaming_l4": true               // L4 流式任务簿')
    assert "R3" in rules(std.lint_text(mutated))


def _fake_registry(*keys):
    return types.SimpleNamespace(KEYS=tuple(keys), R8_SCOPE=reg.R8_SCOPE, CODE_CONSTANTS=reg.CODE_CONSTANTS,
                                 DOC_FILES=reg.DOC_FILES)


def test_r3_js_host_key_needs_a_js_consumer():
    key = reg.Key("performance", "streaming_l4", type="bool", default=True, hosts=reg.HOST_JS,
                  consumers=("autoresearch.scan.frame:build_market_frame",))
    assert "R3" in rules(std.lint_consumers(registry=_fake_registry(key), repo_root=REPO))


def test_r3_both_hosts_key_needs_js_and_session_agent_consumers():
    key = reg.Key("l4_intel", "enabled", type="bool", default=False, hosts=reg.HOST_BOTH,
                  consumers=(".claude/workflows/l4-stock.js",))
    assert "R3" in rules(std.lint_consumers(registry=_fake_registry(key), repo_root=REPO))


# ───────────────────────── R2:消费者必须存在且真的读这个键 ─────────────────────────


def test_r2_production_registry_consumers_all_check_out():
    assert [v for v in std.lint_consumers(registry=reg, repo_root=REPO) if v.rule == "R2"] == []


def test_r2_missing_consumer_symbol_is_red():
    key = reg.Key("l0", "cap_floor_yi", type="nonneg", default=30.0,
                  consumers=("autoresearch.scan.frame:no_such_function",))
    assert "R2" in rules(std.lint_consumers(registry=_fake_registry(key), repo_root=REPO))


def test_r2_consumer_that_never_mentions_the_key_is_red():
    key = reg.Key("l0", "not_a_real_key", type="nonneg", default=30.0,
                  consumers=("autoresearch.scan.frame:build_market_frame",))
    assert "R2" in rules(std.lint_consumers(registry=_fake_registry(key), repo_root=REPO))


def test_r2_group_child_must_appear_in_some_consumer():
    key = reg.Key("l3", "lowturn", kind=reg.KIND_GROUP, type="dict", children=("enabled", "no_such_child"),
                  consumers=("autoresearch.common.turnup:lowturn_flag", "autoresearch.scan.l3.prompt:prepare_l3_table"))
    assert "R2" in rules(std.lint_consumers(registry=_fake_registry(key), repo_root=REPO))


def test_r2_js_consumer_must_mention_the_key():
    key = reg.Key("performance", "no_such_flag", type="bool", default=True, hosts=reg.HOST_JS,
                  consumers=(".claude/workflows/scan-market.js",))
    assert "R2" in rules(std.lint_consumers(registry=_fake_registry(key), repo_root=REPO))


# ───────────────────────── R7:代码里的缺省字面量 == 注册表缺省 ─────────────────────────


def test_r7_production_defaults_agree_everywhere():
    assert [v for v in std.lint_defaults(registry=reg, repo_root=REPO)] == []


def test_r7_js_literal_default_that_disagrees_is_red():
    key = reg.Key("l4_intel", "max_queries", type="posint", default=999, hosts=reg.HOST_BOTH,
                  consumers=(".claude/workflows/l4-stock.js",))
    assert "R7" in rules(std.lint_defaults(registry=_fake_registry(key), repo_root=REPO))


def test_r7_python_knob_literal_default_that_disagrees_is_red():
    key = reg.Key("l0", "cap_floor_yi", type="nonneg", default=31.0,
                  consumers=("autoresearch.scan.frame:build_market_frame",))
    assert "R7" in rules(std.lint_defaults(registry=_fake_registry(key), repo_root=REPO))


# ───────────────────────── R8:散落常量 ratchet ─────────────────────────


def _tmp_repo(tmp_path, source: str) -> Path:
    pkg = tmp_path / "autoresearch" / "scan"
    pkg.mkdir(parents=True)
    (tmp_path / "autoresearch" / "__init__.py").write_text("", encoding="utf-8")
    (pkg / "__init__.py").write_text("", encoding="utf-8")
    (pkg / "menu.py").write_text(source, encoding="utf-8")
    return tmp_path


def test_r8_unregistered_numeric_constant_is_red(tmp_path):
    root = _tmp_repo(tmp_path, "KNIFE_MAX = 0.60\n\n\ndef l4_budget(scan_dir, base=30, floor=12):\n    return base\n")
    fake = types.SimpleNamespace(R8_SCOPE=("autoresearch/scan/**/*.py",), CODE_CONSTANTS=())
    found = std.lint_constants(registry=fake, repo_root=root)
    assert "R8" in rules(found)
    where = {v.where for v in found}
    assert "autoresearch.scan.menu:KNIFE_MAX" in where
    assert "autoresearch.scan.menu:l4_budget.base" in where
    assert "autoresearch.scan.menu:l4_budget.floor" in where


def test_r8_allowlisted_constant_is_clean(tmp_path):
    root = _tmp_repo(tmp_path, "KNIFE_MAX = 0.60\n")
    fake = types.SimpleNamespace(R8_SCOPE=("autoresearch/scan/**/*.py",),
                                 CODE_CONSTANTS=(("autoresearch.scan.menu:KNIFE_MAX", "P2 待迁:落刀旗阈"),))
    assert std.lint_constants(registry=fake, repo_root=root) == []


def test_r8_non_tunable_shapes_are_ignored(tmp_path):
    src = ('SCHEMA_VERSION = 1\n_PRIVATE = 5\nNAMES = ("a", "b")\nFLAG = True\n'
           'def _helper(x, k=5):\n    return x\n\ndef public(x, k=0, j=1, n=None):\n    return x\n')
    root = _tmp_repo(tmp_path, src)
    fake = types.SimpleNamespace(R8_SCOPE=("autoresearch/scan/**/*.py",), CODE_CONSTANTS=())
    assert std.lint_constants(registry=fake, repo_root=root) == []   # 版本号 / 私有名 / 字符串集 / 布尔 / 0·1 缺省都不算可调常量


def test_r8_production_scope_is_fully_registered():
    assert [v for v in std.lint_constants(registry=reg, repo_root=REPO)] == []


def test_r8_stale_allowlist_entry_is_red(tmp_path):
    root = _tmp_repo(tmp_path, "KNIFE_MAX = 0.60\n")
    fake = types.SimpleNamespace(R8_SCOPE=("autoresearch/scan/**/*.py",),
                                 CODE_CONSTANTS=(("autoresearch.scan.menu:KNIFE_MAX", "P2 待迁:落刀旗阈"),
                                                 ("autoresearch.scan.menu:GONE", "P2 待迁:已不存在")))
    found = std.lint_constants(registry=fake, repo_root=root)
    assert [v.where for v in found] == ["autoresearch.scan.menu:GONE"]


# ───────────────────────── R9:文档不复述键值 ─────────────────────────


def test_r9_doc_restating_a_key_value_is_red(tmp_path):
    doc = tmp_path / "SKILL.md"
    doc.write_text("卡数由 `l4.max_cards` 决定,max_cards: 13 是现值。\n", encoding="utf-8")
    fake = types.SimpleNamespace(KEYS=reg.KEYS, DOC_FILES=("SKILL.md",))
    assert "R9" in rules(std.lint_docs(registry=fake, repo_root=tmp_path))


def test_r9_doc_mentioning_a_key_without_value_is_clean(tmp_path):
    doc = tmp_path / "SKILL.md"
    doc.write_text("卡数由 `l4.max_cards` 决定,见 scan_config.jsonc。\n", encoding="utf-8")
    fake = types.SimpleNamespace(KEYS=reg.KEYS, DOC_FILES=("SKILL.md",))
    assert std.lint_docs(registry=fake, repo_root=tmp_path) == []


def test_r9_production_docs_do_not_restate_values():
    assert std.lint_docs(registry=reg, repo_root=REPO) == []


# ───────────────────────── fix-headers 与 CLI ─────────────────────────


def test_fix_headers_regenerates_block_and_zone_headers():
    mutated = TEXT.replace(reg.render_block_header("l0"), "  // ── l0 · 旧的一句话 ── 生效点 过时")
    fixed = std.fix_headers(mutated)
    assert fixed == TEXT


def test_cli_reports_violations_and_exits_nonzero(tmp_path, capsys):
    bad = tmp_path / "scan_config.jsonc"
    bad.write_text(_edit_line(TEXT, '"recall_n": 1000', '    "recall_n": 1000,'), encoding="utf-8")
    rc = std.main(["--path", str(bad), "--rules", "R5"])
    out = capsys.readouterr().out
    assert rc == 1 and "[R5]" in out and "recall_n" in out


def test_cli_is_quiet_and_zero_on_a_clean_file(capsys):
    rc = std.main(["--path", str(PROD), "--rules", "R4,R5,R6"])
    assert rc == 0
    assert capsys.readouterr().out.strip().endswith("0 条违规")
