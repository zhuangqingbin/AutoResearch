"""P4:任务包「本次参数」块 + agent 文件对齐 + Q6 effort 对齐 + Q10 legacy 宿主冻结(2026-09-27)。"""
from __future__ import annotations

import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
AGENTS = ROOT / ".claude" / "agents"


def _cfg(tmp_path, monkeypatch, cfg: dict) -> None:
    p = tmp_path / "scan_config.jsonc"
    p.write_text(json.dumps(cfg, ensure_ascii=False), encoding="utf-8")
    monkeypatch.setattr("autoresearch.scan.user_config.DEFAULT_PATH", p)


def test_exec_line_thresholds_follow_config_and_reach_outcome(tmp_path, monkeypatch):
    from autoresearch.contracts import agent_output as ao
    from autoresearch.scan import outcome

    _cfg(tmp_path, monkeypatch, {"execution": {"entry_line": {"pct_chg_max": 2.5, "pos_in_range_max": 0.6}}})
    assert ao.exec_line_thresholds() == (2.5, 0.6)
    assert outcome.exec_line_thresholds() == (2.5, 0.6)          # outcome 与契约层同一函数
    monkeypatch.setattr("autoresearch.scan.user_config.DEFAULT_PATH", tmp_path / "nope.jsonc")
    assert ao.exec_line_thresholds() == (ao.EXEC_LINE_MAX_PCT_1D, ao.EXEC_LINE_MAX_POS_IN_RANGE)


def test_task_package_params_block_renders_config_values(tmp_path, monkeypatch):
    from autoresearch.scan.l4 import prompts

    _cfg(tmp_path, monkeypatch, {"l4": {"rubric": {"rating_bands": {"Buy": 1}, "force_full": {"conviction_min": 50}}},
                                 "execution": {"entry_line": {"pct_chg_max": 2.5}},
                                 "self_review": {"citation_min": 2}, "l4_intel": {"max_queries": 7}})
    block = prompts.params_block()
    assert "本次参数" in block
    assert "Buy≥1" in block and "OW≥2" in block
    assert "conviction≥50" in block and "通道≥4" in block
    assert "pct_chg <= 2.5" in block and "pos_in_range < 0.7" in block
    assert "≥2 行" in block and "网查软顶 7 条" in block


def test_sector_brief_web_search_cap_is_rendered_from_config(tmp_path, monkeypatch):
    from autoresearch.session_agent.dispatch import sector_brief_web_searches
    from autoresearch.contracts import scan_config as reg

    _cfg(tmp_path, monkeypatch, {"sector": {"brief_web_searches": 0}})
    assert sector_brief_web_searches({"sector": {"brief_web_searches": 0}}) == 0
    assert sector_brief_web_searches({}) == 2
    js = (ROOT / ".claude/workflows/scan-market.js").read_text(encoding="utf-8")
    m = re.search(r"brief_web_searches\s*\?\?\s*(\d+)", js)
    assert m and int(m.group(1)) == reg.find("sector", "brief_web_searches").default
    assert "零新取数。`" not in js                                  # 旧的写死文案已换成参数渲染


def test_agent_files_defer_code_twinned_numbers_to_the_task_package():
    card = (AGENTS / "l4-card.md").read_text(encoding="utf-8")
    assert "本次参数" in card
    assert ">8KB" not in card                                     # 与 4096B 地板矛盾的旧句已删
    rank = (AGENTS / "l3-rank.md").read_text(encoding="utf-8")
    assert "~60 只" not in rank and "~60 候选" not in rank
    brief = (AGENTS / "sector-brief.md").read_text(encoding="utf-8")
    assert "派发 prompt 给出" in brief


def test_agent_frontmatter_effort_matches_the_config_tier():
    """Q6:mailbox 模式传不了 effort,吃的是定义文件 —— 定义文件的 effort 必须等于 config 解析值。"""
    from autoresearch.scan.user_config import load_user_config, resolve_agent_bundle
    from autoresearch.contracts import scan_config as reg

    roles = resolve_agent_bundle(load_user_config(ROOT / reg.CONFIG_PATH), engine="claude")["roles"]
    for role, name in (("strategist", "macro-brief"), ("sector_brief", "sector-brief"), ("l3_rank", "l3-rank"),
                       ("l4_intel", "l4-intel"), ("l4_card", "l4-card")):
        head = (AGENTS / f"{name}.md").read_text(encoding="utf-8").split("---", 2)[1]
        m = re.search(r"^effort:\s*(\w+)", head, re.M)
        assert m, name
        assert m.group(1) == roles[role]["effort"], f"{name}: frontmatter effort {m.group(1)} ≠ config {roles[role]['effort']}"


def test_legacy_host_reads_the_frozen_contract_config_when_a_run_is_active(tmp_path, monkeypatch):
    """Q10:AUTORESEARCH_RUN_ID 在场且 run 根有 run_contract.json → knob 读冻结的 user_config,不读活文件。"""
    from autoresearch.common import workspace as ws
    from autoresearch.common.run_identity import write_run_contract
    from autoresearch.scan import user_config as uc
    from autoresearch.scan.run_bootstrap import prepare_scan_run

    run_id = "20260927T010203456789Z"
    monkeypatch.setenv("AUTORESEARCH_RUN_ID", run_id)
    monkeypatch.setattr(ws, "context_root", lambda: tmp_path / "context")
    workspace = ws.run_root("scan-market", run_id)
    workspace.mkdir(parents=True)
    contract = prepare_scan_run("2026-09-26", config={"l0": {"cap_floor_yi": 11}}, run_id=run_id,
                                workspace_path=workspace, engine=ws.ENGINE)
    write_run_contract(workspace / "run_contract.json", contract)
    monkeypatch.setattr(uc, "DEFAULT_PATH", uc._PRODUCTION_DEFAULT_PATH)   # tests/scan conftest 会把它指到 tmp,这里还原成生产路径
    assert uc._frozen_contract_config() == {"l0": {"cap_floor_yi": 11}}
    assert uc.knob("l0", "cap_floor_yi", None, 30.0) == 11          # 生产 DEFAULT_PATH 未被覆盖 → 走冻结契约
    monkeypatch.setattr("autoresearch.scan.user_config.DEFAULT_PATH", tmp_path / "live.jsonc")
    (tmp_path / "live.jsonc").write_text(json.dumps({"l0": {"cap_floor_yi": 12}}), encoding="utf-8")
    assert uc.knob("l0", "cap_floor_yi", None, 30.0) == 12          # 显式覆盖 DEFAULT_PATH(测试 / SA 冻结拷贝)恒优先
