"""scan_config 注册表(`autoresearch/contracts/scan_config.py`)—— 标准的机器真身。

它登记 `.claude/skills/scan-market/scan_config.jsonc` 每个键的块 / 类型 / 缺省 / 分区 / 宿主 /
消费者;`user_config.py` 的三张白名单表从它派生。这些用例锁「注册表自己不许自相矛盾」与
「白名单确实是派生的」;键与现场是否一致由 `tests/scan/test_config_standard.py` 判。
"""
from __future__ import annotations

from autoresearch.contracts import scan_config as reg


def test_blocks_are_unique_and_zones_do_not_interleave():
    names = [b.name for b in reg.BLOCKS]
    assert len(names) == len(set(names)), "块名重复"
    zones = [b.zone for b in reg.BLOCKS]
    assert set(zones) <= set(reg.ZONES)
    # 分区一(漏斗行为)全部排在分区二(运行时)之前,不许交错
    first_runtime = zones.index(reg.ZONE_RUNTIME)
    assert all(z == reg.ZONE_BEHAVIOR for z in zones[:first_runtime])
    assert all(z == reg.ZONE_RUNTIME for z in zones[first_runtime:])
    assert reg.block_order() == tuple(names)


def test_every_key_points_at_a_known_block_and_vocabulary():
    blocks = {b.name for b in reg.BLOCKS}
    seen = set()
    for k in reg.KEYS:
        assert k.block in blocks, f"{k.block}.{k.key}: 块未登记"
        assert (k.block, k.key) not in seen, f"{k.block}.{k.key}: 重复登记"
        seen.add((k.block, k.key))
        assert k.kind in reg.KINDS, f"{k.block}.{k.key}: kind {k.kind!r}"
        assert k.hosts in reg.HOSTS, f"{k.block}.{k.key}: hosts {k.hosts!r}"
        assert k.type in reg.VALIDATORS, f"{k.block}.{k.key}: type {k.type!r} 无校验器"
        assert k.consumers, f"{k.block}.{k.key}: 零消费者 —— 没有消费点的键不许登记"
        if k.kind == reg.KIND_GROUP:
            assert k.children, f"{k.block}.{k.key}: group 必须列出子键"
    # 每个块至少一个键,否则块是空壳
    for b in reg.BLOCKS:
        assert reg.keys_of(b.name), f"{b.name}: 块下无键"


def test_single_host_keys_carry_their_host_tag_text():
    """hosts=js / sa 的键,尾注必须写的那句宿主标签由注册表统一给出。"""
    assert reg.host_tag("js") == "仅 legacy workflow"
    assert reg.host_tag("sa") == "仅 session_agent"
    assert reg.host_tag("python") == ""
    assert reg.host_tag("js+sa") == ""


def test_user_config_whitelists_are_derived_from_registry():
    from autoresearch.scan import user_config as uc

    assert uc._TOP_WHITELIST == reg.top_whitelist()
    assert uc._SUB_WHITELIST == reg.sub_whitelist()
    types = reg.knob_types()
    assert set(uc._KNOB_TYPES) == set(types)
    for pair, (fn, want) in types.items():
        assert uc._KNOB_TYPES[pair][0] is fn, f"{pair}: 校验器不是同一个对象"
        assert uc._KNOB_TYPES[pair][1] == want


def test_structured_blocks_are_top_level_only():
    """agents / agent_engines 的形状由 resolve_agent_bundle 校验,不进子键白名单。"""
    sub = reg.sub_whitelist()
    for k in reg.KEYS:
        if k.kind == reg.KIND_STRUCTURED:
            assert k.block not in sub, f"{k.block}: structured 块不该有子键白名单"
            assert k.key == "", f"{k.block}: structured 块的 key 应为空串"


def test_block_header_is_rendered_from_registry():
    line = reg.render_block_header("l0")
    assert line.startswith("  // ── l0 · ")
    assert "── 生效点 " in line
    assert "scan/frame.build_market_frame" in line


def test_code_constants_allowlist_reasons_are_from_closed_set():
    for location, reason in reg.CODE_CONSTANTS:
        assert reg.constant_reason_kind(reason) in reg.CONSTANT_REASON_KINDS, f"{location}: {reason!r}"
    locations = [loc for loc, _ in reg.CODE_CONSTANTS]
    assert len(locations) == len(set(locations)), "allowlist 有重复条目"


#: R8 ratchet:代码里「待迁」的可调常量只许减不许增(2026-09-27 登记时 104 条)。
#: 新加一个数值常量 / 数值形参缺省而不进 scan_config,就会撞这条;把它迁进 config 后把数字调小。
PENDING_CONSTANTS_CEILING = 0


def test_pending_code_constants_only_shrink():
    pending = [loc for loc, reason in reg.CODE_CONSTANTS
               if reg.constant_reason_kind(reason) in ("P2 待迁", "P3 待迁")]
    assert len(pending) <= PENDING_CONSTANTS_CEILING, (
        f"待迁常量 {len(pending)} > {PENDING_CONSTANTS_CEILING}:新常量请进 scan_config,而不是登记成待迁")


# ───────────────────────── 注册表级 knob:下层(common / dossier / trace)读 config 不产生向上边 ─────────────────────────


def test_registry_knob_reads_through_the_installed_loader(tmp_path, monkeypatch):
    import json

    import autoresearch.scan.user_config  # noqa: F401 — import 即安装 loader
    cfg = tmp_path / "scan_config.jsonc"
    cfg.write_text(json.dumps({"l0": {"cap_floor_yi": 12}}), encoding="utf-8")
    monkeypatch.setattr("autoresearch.scan.user_config.DEFAULT_PATH", cfg)
    assert reg.knob("l0", "cap_floor_yi", None, 30.0) == 12
    assert reg.knob("l0", "cap_floor_yi", 7, 30.0) == 7          # 显式值恒优先
    assert reg.knob("l0", "min_list_days", None, 0) == 0          # 缺键 = 内建缺省


def test_registry_knob_without_a_loader_falls_back_to_default(monkeypatch):
    monkeypatch.setattr(reg, "_LOADER", None)
    assert reg.knob("l0", "cap_floor_yi", None, 30.0) == 30.0


def test_registry_knob_degrades_loudly_on_a_broken_config(tmp_path, monkeypatch, capsys):
    import autoresearch.scan.user_config  # noqa: F401
    cfg = tmp_path / "scan_config.jsonc"
    cfg.write_text('{"nope": 1}', encoding="utf-8")
    monkeypatch.setattr("autoresearch.scan.user_config.DEFAULT_PATH", cfg)
    assert reg.knob("l0", "cap_floor_yi", None, 30.0) == 30.0
    assert "scan_config 读取失败" in capsys.readouterr().err
