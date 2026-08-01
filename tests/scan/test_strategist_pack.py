"""策略师 pack 单向投影(Wave10 A4):allowlist 默认拒绝 / 禁键不泄漏 / full pack 不被改。

合成,无网络。这一层的意义是把防锚定从**指令级**(prompt 里叮嘱"忽略 sector_healthy_top3")
换成**数据级**(策略师根本读不到)——所以测试必须证明"读不到",而不是"提示了不要读"。
"""
from __future__ import annotations

import json

import pytest

from autoresearch.scan.strategist_pack import (
    ALLOWED_KEYS,
    DENIED_KEYS,
    SCHEMA_VERSION,
    StrategistPackError,
    project,
    validate,
    write,
)


def _full_pack(**extra):
    pack = {
        "regime": {"label": "risk_off"},
        "breadth": {"up": 1200},
        "money": {"main_net": -1.0},
        "valuation": {"pe": 15.0},
        "temperature": {"score": 0.3},
        "cross_money": {"north": -20.0},
        "index_val": {"csi300_pe": 12.0},
        "macro_state": {"phase": "late"},
        "macro_state_note": "note",
        "today_slice": {"n": 4103},
        "sectors": {"半导体": {"n": 10}},
        "sector_healthy_top3": ["半导体", "军工", "券商"],
        "run_contract": {"contract_hash": "abc"},
        "user_config": {"pinned": {"cap": 5}},
    }
    pack.update(extra)
    return pack


def test_denied_keys_never_reach_the_projection():
    body = project(_full_pack())["pack"]
    for key in DENIED_KEYS:
        assert key not in body, f"{key} 泄漏 —— 策略师又能读到它了"
    assert "sector_healthy_top3" not in json.dumps(body, ensure_ascii=False)


def test_allowed_keys_are_all_carried_through():
    body = project(_full_pack())["pack"]
    assert set(body) == set(ALLOWED_KEYS)


def test_unknown_new_key_is_denied_by_default():
    """默认拒绝是关键:将来给 pack 加字段,不该悄悄多给策略师一个可锚定的东西。"""
    payload = project(_full_pack(brand_new_leaderboard=["A", "B"]))
    assert "brand_new_leaderboard" not in payload["pack"]
    assert "brand_new_leaderboard" in payload["dropped_keys"]


def test_projection_carries_provenance():
    payload = project(_full_pack())
    assert payload["schema_version"] == SCHEMA_VERSION
    assert len(payload["source_hash"]) == 64
    assert payload["allowed_keys"] == list(ALLOWED_KEYS)


def test_source_hash_tracks_the_full_pack_not_the_projection():
    """改一个**被剔除**的键,hash 也必须变 —— 它锚的是投影前的整份 pack。"""
    a = project(_full_pack())["source_hash"]
    b = project(_full_pack(sector_healthy_top3=["完全不同"]))["source_hash"]
    assert a != b


def test_projection_does_not_mutate_the_full_pack():
    pack = _full_pack()
    snapshot = json.dumps(pack, ensure_ascii=False, sort_keys=True)
    project(pack)
    assert json.dumps(pack, ensure_ascii=False, sort_keys=True) == snapshot


def test_missing_optional_keys_are_simply_absent():
    payload = project({"regime": {"label": "range"}})
    assert set(payload["pack"]) == {"regime"}
    assert validate(payload) == []


def test_validate_catches_a_leaked_denied_key():
    """落盘后被人手改 / 被别的代码塞回来 —— 读侧也要有守卫。"""
    payload = project(_full_pack())
    assert validate(payload) == []
    payload["pack"]["sector_healthy_top3"] = ["半导体"]
    problems = validate(payload)
    assert problems and any("sector_healthy_top3" in p for p in problems)


def test_validate_catches_any_out_of_allowlist_key():
    payload = project(_full_pack())
    payload["pack"]["something_else"] = 1
    assert any("越权键" in p for p in validate(payload))


def test_write_refuses_to_persist_an_invalid_projection(tmp_path, monkeypatch):
    """自己校验不过就不落盘 —— 别让下游去发现一份已经泄漏的投影。"""
    import autoresearch.scan.strategist_pack as sp

    monkeypatch.setattr(sp, "validate", lambda payload: ["注入的问题"])
    with pytest.raises(StrategistPackError):
        sp.write(_full_pack(), tmp_path / "sp.json")
    assert not (tmp_path / "sp.json").exists()


def test_write_is_atomic_and_readable(tmp_path):
    path = write(_full_pack(), tmp_path / "strategist_pack.json")
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert validate(payload) == []
    assert not list(tmp_path.glob("*.tmp"))


def test_denylist_names_the_three_keys_the_design_calls_out():
    """denylist 是 allowlist 之上的纵深防御,更是**审计说明**:产物里的 `denied_keys`
    要让读的人知道"少的这几块是有意剔除的",而不是投影漏了。设计稿 §A4 点名三个。"""
    assert set(DENIED_KEYS) == {"sector_healthy_top3", "run_contract", "user_config"}


def test_allowlist_and_denylist_do_not_overlap():
    """两边都加了同一个键 = 有人搞混了方向,不该靠字典顺序侥幸通过。"""
    assert not (set(ALLOWED_KEYS) & set(DENIED_KEYS))


def test_non_dict_pack_is_rejected():
    with pytest.raises(StrategistPackError):
        project(["not", "a", "dict"])


def test_frame_is_wired_to_write_the_projection():
    """接线守卫:`frame --json-out` 必须紧跟着落投影 —— 只写了模块没接线 = 特性等于没做。

    这里读**源码**而不是跑 `frame.main()`:后者要真取数据。源码断言的鉴别力靠
    「把 frame.py 那几行删掉,本用例必须变红」来保证(变异测试已核)。
    """
    from pathlib import Path

    src = Path("autoresearch/scan/frame.py").read_text(encoding="utf-8")
    assert "from autoresearch.scan.strategist_pack import write as write_strategist" in src
    assert 'with_name("strategist_pack.json")' in src
    # 必须在 market_pack 落盘之后 —— 投影的输入就是那份 payload
    assert src.index("_atomic_write_json(args.json_out") < src.index("write_strategist(")


def test_projection_of_the_live_pack_hides_the_leaderboard():
    """活体验收:拿真产物投一次,禁键必须不在。缺产物则跳过(不假装跑过)。"""
    from pathlib import Path

    packs = sorted(Path("context/scan").glob("*/market_pack.json"))
    if not packs:
        pytest.skip("无真 market_pack 产物")
    pack = json.loads(packs[-1].read_text(encoding="utf-8"))
    payload = project(pack)
    assert validate(payload) == []
    assert "sector_healthy_top3" in pack, "真 pack 里本来就该有它,否则这条验收是空的"
    assert "sector_healthy_top3" not in payload["pack"]


# ── Wave10 A4 接线守卫:数据级防锚定只有真接上才算数 ──────────────────────────

def _workflow_src() -> str:
    from pathlib import Path
    return Path(".claude/workflows/scan-market.js").read_text(encoding="utf-8")


def test_workflow_hands_the_strategist_only_the_projection():
    """派发 prompt 必须指向投影,且**不得**再把 full market_pack 交给策略师。

    这条是整个 A4 的价值所在:防锚定从「叮嘱它忽略 sector_healthy_top3」变成「它看不到」。
    只写模块不接线 = 特性等于没做(而 07-30/31 已经连续两日复发过)。
    """
    src = _workflow_src()
    dispatch = src.split("agentType: 'macro-brief'")[0].split("() => agent(")[-1]
    assert "strategist_pack.json" in dispatch
    assert "market_pack.json" not in dispatch, "策略师又能读到 full pack 了"


def test_workflow_no_longer_relies_on_a_prompt_level_reminder():
    """指令级约束已被数据级取代 —— 派发 prompt 里不该再出现那句叮嘱。

    留着它无害但会误导:读的人会以为防线还在 prompt 上,从而在别处照抄这种写法。
    """
    src = _workflow_src()
    dispatch = src.split("agentType: 'macro-brief'")[0].split("() => agent(")[-1]
    assert "sector_healthy_top3" not in dispatch


def test_workflow_gates_on_the_projection_being_present():
    """投影缺失比 full pack 缺失更隐蔽(pack-check 会绿)—— 必须自己有一道闸。"""
    src = _workflow_src()
    assert "strategist-pack-check" in src
    assert "strategist-pack-rebuild" in src or "strategist_pack " in src


def test_macro_brief_agent_definition_points_at_the_projection():
    """agent 定义里的路径也要跟上 —— 否则 agent 会按自己的说明去读 full pack。"""
    from pathlib import Path

    src = Path(".claude/agents/macro-brief.md").read_text(encoding="utf-8")
    assert "strategist_pack.json" in src
    assert "context/scan/<date>/market_pack.json" not in src
