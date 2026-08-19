#!/usr/bin/env python3
"""scan_config.json 用户配置层 —— 白名单加载 + ScanConfig 映射 + frame --json 回显。

design: docs/specs/2026-07-11-recall-gate-pinned-config-design.md §4.2。
plan: docs/plans/2026-07-11-pinned-config-plan.md Task 1(全波地基)。

白名单外顶层键 / funnel·pinned·reuse 白名单外子键 → raise(防拼写错静默失效);缺文件 → {}
(=现行为,parity)。`agents` 的 role 闭集 + model/effort 子键校验见 test_user_config_roles.py
(Wave11 B1)。
"""
from __future__ import annotations

import json

import pytest

from autoresearch.common import workspace as ws
from autoresearch.scan.user_config import (
    _strip_jsonc,
    load_pinned,
    load_user_config,
)


def test_strip_jsonc_removes_comments_keeps_strings():
    """// 行注释与 /* */ 块注释剥离;字符串内的 // 原样保留。"""
    import json
    src = '''{
      // 顶层说明
      "funnel": {"recall_channels": ["a", "b"]},  // 行尾说明
      /* 块注释 */
      "reuse": {"max_age_days": 4}
    }'''
    assert json.loads(_strip_jsonc(src)) == {"funnel": {"recall_channels": ["a", "b"]},
                                             "reuse": {"max_age_days": 4}}
    # 字符串内的 // 不被误删
    assert json.loads(_strip_jsonc('{"pinned": {"cap": 5}, "x": "a//b"}'))["x"] == "a//b"


def test_load_user_config_parses_jsonc(tmp_path):
    """带 // 说明的真 scan_config.json 能正常加载 + 白名单校验。"""
    p = tmp_path / "scan_config.json"
    p.write_text('{\n  // 召回通道整编\n  "funnel": {"recall_channels": ["composite", "value"]}\n}',
                 encoding="utf-8")
    assert load_user_config(p)["funnel"]["recall_channels"] == ["composite", "value"]


def test_load_pinned_parses_jsonc(tmp_path):
    """带 // 说明的真 pinned.json(空 active 列表)→ kept 空,不炸。"""
    p = tmp_path / "pinned.json"
    p.write_text('[\n  // 保送票清单(每条 {code,note,expires});空=无保送\n]', encoding="utf-8")
    assert load_pinned("2026-07-11", path=p) == {"kept": [], "expired": []}

_VALID_FULL = {
    "agents": {"l4_card": {"model": "opus", "effort": "high"}},
    "funnel": {
        "recall_channels": ["composite", "momentum", "value"],
        "channel_quotas": {"momentum": 200},
        "channel_floors": {"momentum": 40},
    },
    "pinned": {"cap": 5, "ttl_days": 10},
    "l4_intel": {"enabled": True, "max_queries": 20},
}


# ───────────────────────── load_user_config:合法文件全键 ─────────────────────────


def test_load_user_config_valid_file_all_keys(tmp_path):
    p = tmp_path / "scan_config.json"
    p.write_text(json.dumps(_VALID_FULL), encoding="utf-8")
    assert load_user_config(p) == _VALID_FULL


# ───────────────────────── load_user_config:坏键 raise ─────────────────────────


def test_load_user_config_unknown_top_key_raises(tmp_path):
    p = tmp_path / "scan_config.json"
    p.write_text(json.dumps({"typo_key": 1}), encoding="utf-8")
    with pytest.raises(ValueError, match="typo_key"):
        load_user_config(p)


@pytest.mark.parametrize("block,bad", [
    ("funnel", {"recall_channels": ["momentum"], "bogus_sub": 1}),
    ("pinned", {"cap": 5, "bogus_sub": 1}),
    ("l4_intel", {"enabled": True, "bogus_sub": 1}),
])
def test_load_user_config_unknown_sub_key_raises(tmp_path, block, bad):
    p = tmp_path / "scan_config.json"
    p.write_text(json.dumps({block: bad}), encoding="utf-8")
    with pytest.raises(ValueError, match="bogus_sub"):
        load_user_config(p)


# ───────────────────────── load_user_config:缺文件空 dict ─────────────────────────


def test_load_user_config_missing_file_returns_empty(tmp_path):
    assert load_user_config(tmp_path / "nope.json") == {}


def test_load_user_config_default_path_missing_returns_empty(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)   # 无 .claude/skills/scan-market/scan_config.json → {}
    assert load_user_config() == {}


# ── Wave12-T33:`apply_to_scan_config` 已删除(生产零调用点,08-09 复核) ──
#
# 它自 2026-07-11 落地起就只有测试在调,却在 `config.py` 的 docstring 里被写成
# 「现存消费方」—— 一处会让人误以为"配置已经喂进 ScanConfig 了"的文档-实现落差。
# 真实路径是各消费点自己 `load_user_config()` 现读需要的块。
# 原来那四条测试只是在测"这个映射函数把 dict 抄进 dataclass",随函数一并删;
# 它们**顺带**锁着的"这些顶层键在白名单内"由下面各块的 `load_user_config` 断言继续锁
# (删 test 会静默孤立它顺带锁的 live 契约 —— 2026-07-19 家训,所以逐条接管而不是一删了之)。


def test_whitelisted_top_level_keys_still_load(tmp_path):
    """接管上面被删测试顺带锁着的契约:agents/funnel/pinned/l4_intel 顶层键仍在白名单内。"""
    p = tmp_path / "scan_config.jsonc"
    p.write_text(json.dumps(_VALID_FULL), encoding="utf-8")
    cfg = load_user_config(p)
    assert cfg["funnel"]["recall_channels"] == ["composite", "momentum", "value"]
    assert cfg["funnel"]["channel_quotas"] == {"momentum": 200}
    assert cfg["funnel"]["channel_floors"] == {"momentum": 40}
    assert cfg["agents"] == _VALID_FULL["agents"]
    assert cfg["pinned"] == _VALID_FULL["pinned"]
    assert cfg["l4_intel"] == _VALID_FULL["l4_intel"]


def test_removed_mapper_stays_removed():
    """回归锁:别把它加回来。要加回来必须先有真实生产调用点,否则又是一处文档-实现落差。"""
    import autoresearch.scan.user_config as uc
    assert not hasattr(uc, "apply_to_scan_config")


# ───────────────────────── l4_intel:新顶层键白名单 + 透传 ScanConfig ─────────────────────────


def test_l4_intel_whitelisted(tmp_path):
    p = tmp_path / "scan_config.jsonc"
    p.write_text('{"l4_intel": {"enabled": true}}', encoding="utf-8")
    cfg = load_user_config(p)
    assert cfg["l4_intel"]["enabled"] is True


def test_l4_intel_unknown_subkey_raises(tmp_path):
    p = tmp_path / "scan_config.jsonc"
    p.write_text('{"l4_intel": {"enable": true}}', encoding="utf-8")   # 拼写错
    with pytest.raises(ValueError, match="l4_intel"):
        load_user_config(p)


def test_l4_intel_block_survives_whitelist(tmp_path):
    p = tmp_path / "scan_config.jsonc"
    p.write_text(json.dumps({"l4_intel": {"enabled": True}}), encoding="utf-8")
    assert load_user_config(p)["l4_intel"] == {"enabled": True}


def test_l4_intel_max_queries_allowed(tmp_path):
    from autoresearch.scan.user_config import load_user_config
    p = tmp_path / "scan_config.json"
    p.write_text('{"l4_intel": {"enabled": true, "max_queries": 10}}', encoding="utf-8")
    cfg = load_user_config(p)
    assert cfg["l4_intel"] == {"enabled": True, "max_queries": 10}


def test_l4_intel_unknown_subkey_still_raises(tmp_path):
    import pytest

    from autoresearch.scan.user_config import load_user_config
    p = tmp_path / "scan_config.json"
    p.write_text('{"l4_intel": {"enabled": true, "max_query": 10}}', encoding="utf-8")
    with pytest.raises(ValueError, match="max_query"):
        load_user_config(p)


# ───────────────────────── l3:两遍法分诊新顶层键白名单 + 透传 ScanConfig(镜像 l4_intel 三连) ─────────────────────────
# design: docs/plans/2026-07-12-l3-merge-plan.md Task 1。


def test_l3_whitelisted(tmp_path):
    p = tmp_path / "scan_config.jsonc"
    p.write_text(json.dumps({"l3": {"two_pass": False, "pass1_target": 40, "finalist_max": 8}}),
                encoding="utf-8")
    cfg = load_user_config(p)
    assert cfg["l3"] == {"two_pass": False, "pass1_target": 40, "finalist_max": 8}


def test_l3_unknown_subkey_raises(tmp_path):
    p = tmp_path / "scan_config.jsonc"
    p.write_text(json.dumps({"l3": {"two_pas": True}}), encoding="utf-8")   # 拼写错
    with pytest.raises(ValueError, match="l3"):
        load_user_config(p)


def test_l3_block_survives_whitelist(tmp_path):
    raw = {"two_pass": True, "pass1_target": 60, "finalist_max": 10}
    p = tmp_path / "scan_config.jsonc"
    p.write_text(json.dumps({"l3": raw}), encoding="utf-8")
    assert load_user_config(p)["l3"] == raw


# ───────────────────────── learning:基率收缩估计新顶层键白名单 + 透传 ScanConfig(镜像 l3/l4_intel 三连) ─────────────────────────
# spec: docs/specs/2026-07-12-selflearning-optimization-brainstorm.md §4 P0-3。


def test_learning_whitelisted(tmp_path):
    p = tmp_path / "scan_config.jsonc"
    p.write_text(json.dumps({"learning": {"shrink": False, "shrink_k": 20}}), encoding="utf-8")
    cfg = load_user_config(p)
    assert cfg["learning"] == {"shrink": False, "shrink_k": 20}


def test_learning_unknown_subkey_raises(tmp_path):
    p = tmp_path / "scan_config.jsonc"
    p.write_text(json.dumps({"learning": {"shrinkage": True}}), encoding="utf-8")   # 拼写错
    with pytest.raises(ValueError, match="learning"):
        load_user_config(p)


def test_learning_block_survives_whitelist(tmp_path):
    raw = {"shrink": True, "shrink_k": 15}
    p = tmp_path / "scan_config.jsonc"
    p.write_text(json.dumps({"learning": raw}), encoding="utf-8")
    assert load_user_config(p)["learning"] == raw


# ───────────────────────── budgets:只观测不截断 ─────────────────────────


def test_budgets_whitelisted_and_applied(tmp_path):
    raw = {
        "cache_hit_min": 0.85,
        "stage_cost_usd": {"l4-card": 20},
        "stage_wall_seconds": {"L3精排": 900},
        "concurrency": {
            "tushare": 4,
            "web_search": 3,
            "web_fetch": 3,
            "l4_stock": 4,
        },
        "min_real_scans": 10,
        "baseline_run": "20260727_2140",
    }
    p = tmp_path / "scan_config.jsonc"
    p.write_text(json.dumps({"budgets": raw}), encoding="utf-8")
    cfg = load_user_config(p)
    assert cfg["budgets"] == raw


def test_budgets_unknown_subkey_raises(tmp_path):
    p = tmp_path / "scan_config.jsonc"
    p.write_text(json.dumps({"budgets": {"truncate_on_overrun": True}}), encoding="utf-8")
    with pytest.raises(ValueError, match="truncate_on_overrun"):
        load_user_config(p)


# ───────────────────────── performance:调度开关，不拥有评级语义 ─────────────────────────


def test_performance_switches_whitelisted_and_applied(tmp_path):
    raw = {
        "streaming_l4": True,
    }
    p = tmp_path / "scan_config.jsonc"
    p.write_text(json.dumps({"performance": raw}), encoding="utf-8")

    cfg = load_user_config(p)

    assert cfg["performance"] == raw


@pytest.mark.parametrize("raw", [
    {"streaming_l4": "yes"},
    {"streaming_l4": 1},
    {"sector_brief_mode": "selected"},
])
def test_performance_switches_reject_invalid_values(tmp_path, raw):
    p = tmp_path / "scan_config.jsonc"
    p.write_text(json.dumps({"performance": raw}), encoding="utf-8")
    with pytest.raises(ValueError, match="performance"):
        load_user_config(p)


def test_performance_unknown_subkey_raises(tmp_path):
    p = tmp_path / "scan_config.jsonc"
    p.write_text(
        json.dumps({"performance": {"truncate_on_overrun": True}}),
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="truncate_on_overrun"):
        load_user_config(p)


# ───────────────────────── relative_buy(E6):mode/exclude_pinned 三件套 ─────────────────────────
# design: .superpowers/sdd/2026-08-18-e6-activation-slimdown-implementation-plan/task-2.1-brief.md。
# 本任务只把开关**装上**(白名单+类型校验),默认值仍是 mode=shadow(=现行为,parity)——翻
# active 是用户逐项过完裁决表后的独立动作,不在这三个 task 的范围内。


def load_from(tmp_path, raw: dict) -> dict:
    """写临时 scan_config.jsonc → 加载校验后的 dict。本文件目前没有同类既有 helper,按
    brief 要求补一个 3 行版本(其余测试沿用各自内联的 `p.write_text(...)` 写法)。"""
    p = tmp_path / "scan_config.jsonc"
    p.write_text(json.dumps(raw), encoding="utf-8")
    return load_user_config(p)


def test_relative_buy_block_accepted(tmp_path):
    cfg = load_from(tmp_path, {"relative_buy": {"mode": "shadow", "exclude_pinned": True}})
    assert cfg["relative_buy"]["mode"] == "shadow"


def test_relative_buy_active_mode_accepted(tmp_path):
    """mode 白名单是 {shadow, active} 双值集合,不是只认 shadow 的字面量钉死。"""
    cfg = load_from(tmp_path, {"relative_buy": {"mode": "active"}})
    assert cfg["relative_buy"]["mode"] == "active"


def test_relative_buy_bad_mode_raises(tmp_path):
    with pytest.raises(ValueError):
        load_from(tmp_path, {"relative_buy": {"mode": "live"}})


def test_relative_buy_unknown_subkey_raises(tmp_path):
    with pytest.raises(ValueError):
        load_from(tmp_path, {"relative_buy": {"mode": "shadow", "ttl": 3}})


def test_relative_buy_exclude_pinned_bad_type_raises(tmp_path):
    with pytest.raises(ValueError, match="非法"):
        load_from(tmp_path, {"relative_buy": {"exclude_pinned": "yes"}})


def test_relative_buy_activate_date_accepts_a_date_or_null(tmp_path):
    """task-2.4:legacy 账本冻结日。null = 不冻结(现行为);字符串必须是 YYYY-MM-DD。"""
    assert load_from(tmp_path, {"relative_buy": {"activate_date": None}})[
        "relative_buy"]["activate_date"] is None
    assert load_from(tmp_path, {"relative_buy": {"activate_date": "2026-08-20"}})[
        "relative_buy"]["activate_date"] == "2026-08-20"


def test_relative_buy_activate_date_bad_shape_raises(tmp_path):
    """`"20260820"` / `"明天"` 这种会静默让冻结永不生效 —— 错型必须 raise,不许静默。"""
    for bad in ("20260820", "2026/08/20", "明天", 20260820, True):
        with pytest.raises(ValueError, match="非法"):
            load_from(tmp_path, {"relative_buy": {"activate_date": bad}})


# ───────────────────────── frame --json:user_config 回显 + run meta 落盘 ─────────────────────────


def test_frame_json_echoes_user_config_block(monkeypatch, tmp_path, capsys):
    """无 scan_config.json → user_config={} 仍回显块 + 落 context/scan/<date>/user_config_echo.json。"""
    from autoresearch.scan import frame as scan_frame
    from tests.scan._synth_universe import synth_universe

    df = synth_universe(n=30, seed=1)
    monkeypatch.setattr(scan_frame, "build_market_frame",
                        lambda d, **kw: (df, {"universe_raw": 30, "universe": 30, "after_gate_a": 30}))
    monkeypatch.setattr("autoresearch.macro.state.load_macro_state",
                        lambda today, regime_today=None, path=None:
                        (None, "无 macro_state.json → 只用日频 pack"), raising=True)
    monkeypatch.chdir(tmp_path)   # 隔离:防止真实 context/scan/<date> 历史 staging 被写入

    rc = scan_frame.main(["2026-07-11", "--json"])
    out = capsys.readouterr().out
    assert rc == 0
    assert '"user_config"' in out

    echo = tmp_path / ws.scan_root() / "2026-07-11" / "user_config_echo.json"
    assert echo.exists()
    assert json.loads(echo.read_text(encoding="utf-8")) == {}


def test_frame_json_echo_reflects_real_config(monkeypatch, tmp_path, capsys):
    """真有 scan_config.json → 回显值与文件一致(run meta = 可复现凭据)。"""
    from autoresearch.scan import frame as scan_frame
    from tests.scan._synth_universe import synth_universe

    df = synth_universe(n=30, seed=2)
    monkeypatch.setattr(scan_frame, "build_market_frame",
                        lambda d, **kw: (df, {"universe_raw": 30, "universe": 30, "after_gate_a": 30}))
    monkeypatch.setattr("autoresearch.macro.state.load_macro_state",
                        lambda today, regime_today=None, path=None:
                        (None, "无 macro_state.json → 只用日频 pack"), raising=True)
    monkeypatch.chdir(tmp_path)
    cfg_dir = tmp_path / ".claude" / "skills" / "scan-market"
    cfg_dir.mkdir(parents=True)
    (cfg_dir / "scan_config.jsonc").write_text(
        json.dumps({"pinned": {"cap": 3}}), encoding="utf-8")
    monkeypatch.setattr("autoresearch.scan.user_config.DEFAULT_PATH",   # 显式指回本测试的真配置(冲销 conftest 隔离)
                        cfg_dir / "scan_config.jsonc")

    rc = scan_frame.main(["2026-07-12", "--json"])
    out = capsys.readouterr().out
    assert rc == 0
    assert '"cap": 3' in out

    echo = tmp_path / ws.scan_root() / "2026-07-12" / "user_config_echo.json"
    assert json.loads(echo.read_text(encoding="utf-8")) == {"pinned": {"cap": 3},
                                                            "engine": ws.ENGINE}


def test_cli_main_prints_validated_json(tmp_path, monkeypatch, capsys):
    """CLI 回显白名单校验 **+ resolve** 后的 JSON(scan-retro 喂 t1-review workflow args.cfg 用)。

    ⚠️ Wave12-T33 修复轮 1 改了契约:原版用**半份**配置(只有 `t1_diag`)断言原样回显。
    现在 CLI 会对有 `agents` 的配置跑 `resolve_agent_config(require_all=True)`,半份配置
    直接 raise(那条路径由 `test_user_config_cli_fails_loudly_on_partial_config` 单独锁)。
    这里改用完整闭集,断言**两件事同时成立**:原始 `agents` 块原样保留(t1-review 的兜底链
    还读它)+ 多出 `resolved_agents`(t1-review 的 resolved 优先分支靠它才不是死代码)。
    """
    import json

    from autoresearch.scan import user_config as uc
    agents = {role: {"effort": "high"} for role in sorted(uc._AGENT_ROLES)}
    agents["gp_shell"] = {"model": "sonnet", "effort": "low"}
    agents["gp_shell_json"] = {"model": "sonnet", "effort": "low"}
    p = tmp_path / "scan_config.jsonc"
    p.write_text("{\n  // 注释\n  \"agents\": " + json.dumps(agents) + "\n}", encoding="utf-8")
    monkeypatch.setattr(uc, "DEFAULT_PATH", p)
    assert uc.main() == 0
    out = json.loads(capsys.readouterr().out)
    assert out["agents"] == agents
    assert set(out["resolved_agents"]) == uc._AGENT_ROLES


# ── Wave12-T33:frame --json 必须 materialize resolved agent config ──


def test_frame_json_materializes_resolved_agent_config(monkeypatch, tmp_path, capsys):
    """接线锁(FN-1 家族):`frame --json` 要把 resolved 落盘 + 回显进 user_config。

    没这一步,workflow 拿不到 `cfg.resolved_agents`,会永远吃自己那张 AGENT_DEFAULTS
    兜底表 —— 单一事实源就只存在于文档里(「消费者读没人生产的产物」的镜像)。
    """
    from autoresearch.scan import frame as scan_frame
    from autoresearch.scan.user_config import _AGENT_ROLES, RESOLVED_FILENAME
    from tests.scan._synth_universe import synth_universe

    df = synth_universe(n=30, seed=3)
    monkeypatch.setattr(scan_frame, "build_market_frame",
                        lambda d, **kw: (df, {"universe_raw": 30, "universe": 30, "after_gate_a": 30}))
    monkeypatch.setattr("autoresearch.macro.state.load_macro_state",
                        lambda today, regime_today=None, path=None:
                        (None, "无 macro_state.json → 只用日频 pack"), raising=True)
    monkeypatch.chdir(tmp_path)
    cfg_dir = tmp_path / ".claude" / "skills" / "scan-market"
    cfg_dir.mkdir(parents=True)
    agents = {role: {"effort": "high"} for role in sorted(_AGENT_ROLES)}
    agents["gp_shell"] = {"model": "sonnet", "effort": "low"}
    agents["gp_shell_json"] = {"model": "sonnet", "effort": "low"}
    (cfg_dir / "scan_config.jsonc").write_text(json.dumps({"agents": agents}), encoding="utf-8")
    monkeypatch.setattr("autoresearch.scan.user_config.DEFAULT_PATH",
                        cfg_dir / "scan_config.jsonc")

    assert scan_frame.main(["2026-08-09", "--json"]) == 0
    out = capsys.readouterr().out

    resolved_file = tmp_path / ws.scan_root() / "2026-08-09" / RESOLVED_FILENAME
    assert resolved_file.exists(), "frame --json 没落 _resolved_agent_config.json"
    assert set(json.loads(resolved_file.read_text(encoding="utf-8"))["roles"]) == _AGENT_ROLES
    assert '"resolved_agents"' in out, "payload 的 user_config 块没带 resolved_agents(workflow 拿不到)"

    echo = json.loads((tmp_path / ws.scan_root() / "2026-08-09"
                       / "user_config_echo.json").read_text(encoding="utf-8"))
    assert set(echo["resolved_agents"]) == _AGENT_ROLES
    # echo / market_pack / run_contract 三处仍同哈希(health.py 的 config_hash 校验靠它)
    from autoresearch.scan.health import run_contract_health
    health = run_contract_health(tmp_path / ws.scan_root() / "2026-08-09")
    assert health["errors"] == [], health["errors"]
    assert health["echo_config_match"] is True


def test_frame_json_without_agents_block_stays_parity(monkeypatch, tmp_path, capsys):
    """parity:机器上根本没有配置文件(`{}`)→ 不落 resolved、不炸。

    fail-fast 的靶子是"配了一半"和"配了但空",不是"这台机器上没这个文件"——
    把这条也变成硬失败会毙掉离线/测试路径。
    """
    from autoresearch.scan import frame as scan_frame
    from autoresearch.scan.user_config import RESOLVED_FILENAME
    from tests.scan._synth_universe import synth_universe

    df = synth_universe(n=30, seed=4)
    monkeypatch.setattr(scan_frame, "build_market_frame",
                        lambda d, **kw: (df, {"universe_raw": 30, "universe": 30, "after_gate_a": 30}))
    monkeypatch.setattr("autoresearch.macro.state.load_macro_state",
                        lambda today, regime_today=None, path=None:
                        (None, "无 macro_state.json → 只用日频 pack"), raising=True)
    monkeypatch.chdir(tmp_path)
    assert scan_frame.main(["2026-08-09", "--json"]) == 0
    assert not (tmp_path / ws.scan_root() / "2026-08-09" / RESOLVED_FILENAME).exists()


# ── Wave12-T33 修复轮 1(I2):CLI 必须产出 resolved_agents,否则 t1-review 那条腿是拆半的 ──


def test_user_config_cli_emits_resolved_agents(tmp_path, monkeypatch, capsys):
    """生产者接线锁:`python -m autoresearch.scan.user_config` 必须吐出 `resolved_agents`。

    这条是 I2 的直接验收。修复前实跑该 CLI,顶层键只有
    `['agents','funnel','l3','l4_intel','learning','performance']` —— 没有 `resolved_agents`,
    于是 `t1-review.js` 的 `RESOLVED = cfg.resolved_agents || {}` 在生产上恒空,
    那条 resolved 优先分支是死代码(`.claude/skills/scan-retro/SKILL.md:22` 明写
    t1-review 的 args.cfg 来自本 CLI)。
    """
    from autoresearch.scan.user_config import _AGENT_ROLES, main

    cfg_dir = tmp_path / ".claude" / "skills" / "scan-market"
    cfg_dir.mkdir(parents=True)
    agents = {role: {"effort": "high"} for role in sorted(_AGENT_ROLES)}
    agents["gp_shell"] = {"model": "sonnet", "effort": "low"}
    agents["gp_shell_json"] = {"model": "sonnet", "effort": "low"}
    (cfg_dir / "scan_config.jsonc").write_text(json.dumps({"agents": agents}), encoding="utf-8")
    monkeypatch.setattr("autoresearch.scan.user_config.DEFAULT_PATH",
                        cfg_dir / "scan_config.jsonc")

    assert main() == 0
    out = json.loads(capsys.readouterr().out)
    assert "resolved_agents" in out, "CLI 没产出 resolved_agents —— t1-review 路仍在自己解释默认值"
    assert set(out["resolved_agents"]) == _AGENT_ROLES
    assert out["agents"] == agents, "原始 agents 块必须原样保留(t1-review 的兜底链还读它)"


def test_user_config_cli_fails_loudly_on_partial_config(tmp_path, monkeypatch):
    """配了一半 → CLI **raise**(与 frame 主路同一把尺)。

    主路 fail 而 retro 路静默降级,才是更糟的不一致 —— 所以这里不给"局部编排放宽"的后门。
    """
    from autoresearch.scan.user_config import main

    cfg_dir = tmp_path / ".claude" / "skills" / "scan-market"
    cfg_dir.mkdir(parents=True)
    (cfg_dir / "scan_config.jsonc").write_text(
        json.dumps({"agents": {"t1_diag": {"effort": "high"}}}), encoding="utf-8")
    monkeypatch.setattr("autoresearch.scan.user_config.DEFAULT_PATH",
                        cfg_dir / "scan_config.jsonc")
    with pytest.raises(ValueError, match="缺生产必填 role"):
        main()


def test_user_config_cli_without_config_file_stays_parity(tmp_path, monkeypatch, capsys):
    """没有配置文件 → 原样输出(parity),不 resolve 也不炸。

    这一层不炸的分工写在 `frame.py` 同款注释里:本层管"配坏了",workflow 管"根本没配"
    (`t1-review.js:24-26` 的空 cfg throw 会当场拒跑)。
    """
    from autoresearch.scan.user_config import main

    monkeypatch.setattr("autoresearch.scan.user_config.DEFAULT_PATH",
                        tmp_path / "nope.jsonc")
    assert main() == 0
    assert json.loads(capsys.readouterr().out) == {}


def test_materialized_resolved_is_byte_identical_to_echoed_one(monkeypatch, tmp_path, capsys):
    """对账真伪的前提:`_resolved_agent_config.json` 与 echo 里那份必须是**同一张表**。

    `usage_reconcile` 读文件、workflow 读 echo —— 两边若各自重新解释一遍 config,
    "对账"就只是两次独立计算碰巧相等,而不是对同一份事实源。这里逐字段比对钉死。
    """
    from autoresearch.scan import frame as scan_frame
    from autoresearch.scan.user_config import _AGENT_ROLES, RESOLVED_FILENAME
    from tests.scan._synth_universe import synth_universe

    df = synth_universe(n=30, seed=7)
    monkeypatch.setattr(scan_frame, "build_market_frame",
                        lambda d, **kw: (df, {"universe_raw": 30, "universe": 30, "after_gate_a": 30}))
    monkeypatch.setattr("autoresearch.macro.state.load_macro_state",
                        lambda today, regime_today=None, path=None:
                        (None, "无 macro_state.json → 只用日频 pack"), raising=True)
    monkeypatch.chdir(tmp_path)
    cfg_dir = tmp_path / ".claude" / "skills" / "scan-market"
    cfg_dir.mkdir(parents=True)
    agents = {role: {"effort": "high"} for role in sorted(_AGENT_ROLES)}
    agents["l4_card"] = {"effort": "max"}
    agents["gp_shell"] = {"model": "sonnet", "effort": "low"}
    agents["gp_shell_json"] = {"model": "sonnet", "effort": "low"}
    (cfg_dir / "scan_config.jsonc").write_text(json.dumps({"agents": agents}), encoding="utf-8")
    monkeypatch.setattr("autoresearch.scan.user_config.DEFAULT_PATH",
                        cfg_dir / "scan_config.jsonc")

    assert scan_frame.main(["2026-08-09", "--json"]) == 0
    capsys.readouterr()

    scan_dir = tmp_path / ws.scan_root() / "2026-08-09"
    on_disk = json.loads((scan_dir / RESOLVED_FILENAME).read_text(encoding="utf-8"))["roles"]
    echoed = json.loads((scan_dir / "user_config_echo.json").read_text(encoding="utf-8"))["resolved_agents"]
    assert on_disk == echoed, "落盘的 resolved 与 echo 里那份不是同一张表 —— 对账是假的"

    # 而且 usage_reconcile 真的读的是这一份(不是自己重新解释 config)
    from autoresearch.scan.user_config import load_resolved_agent_config
    assert load_resolved_agent_config(scan_dir) == echoed
