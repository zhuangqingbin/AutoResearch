from autoresearch.analyze.run_profile import analyze_profile
from autoresearch.contracts import stages as vocab
from autoresearch.scan.run_profile import scan_profile

def test_analyze_vocab_registered():
    assert "stock-research" in vocab.RUN_KINDS
    assert vocab.ANALYZE_STAGES == ("harvest", "intel", "write", "assemble", "publish")
    assert vocab.ANALYZE_LITE_STAGES == ("harvest", "card")
    assert vocab.ANALYZE_MODES == ("FULL", "LITE")

def test_analyze_profile_full():
    p = analyze_profile(mode="FULL")
    assert p.kind == "stock-research"
    assert p.expected_stages == vocab.ANALYZE_STAGES
    assert p.replayable_stages == ()          # 走湖前诚实为零(T9 之后另议)
    assert "company-intel" in p.conditional_roles

def test_analyze_profile_lite_skips_intel():
    p = analyze_profile(mode="LITE")
    assert p.expected_stages == vocab.ANALYZE_LITE_STAGES
    assert p.agent_roles == ()                # lite 不派情报员(engine-playbook「lite 一律不派」)

def test_scan_vocab_untouched():
    # 既有 scan 词汇必须逐字未动(test_stage_vocabulary 用 `is` 锁副本,这里再锁个防呆)
    assert vocab.MODES == ("FULL", "FORCED_FULL", "SENTINEL_EMPTY", "SENTINEL_PINNED")
    assert vocab.STAGES[0] == "frame" and vocab.STAGES[-1] == "finalize"


def test_analyze_tags_agent_index_as_l1_evidence(): # D6.5
    """`agents/index.json` 是 harness 自己写的摘要视图,不是原文 —— analyze 侧标 L1;
    scan 侧那份逐字一样的规则仍是默认 L0,两个 profile 互不影响。"""
    analyze_rule = analyze_profile().artifact_rules
    scan_rule = scan_profile().artifact_rules

    analyze_agent_index = next(r for r in analyze_rule if r.key == "agent_index")
    scan_agent_index = next(r for r in scan_rule if r.key == "agent_index")

    assert analyze_agent_index.evidence_level == "L1"
    assert scan_agent_index.evidence_level == "L0"
    # key/selector/source/required_when 逐字不变 —— 只换了 evidence_level 一个字段。
    assert analyze_agent_index.key == scan_agent_index.key
    assert analyze_agent_index.selector == scan_agent_index.selector
    assert analyze_agent_index.source == scan_agent_index.source
    assert analyze_agent_index.required_when == scan_agent_index.required_when


def test_analyze_rules_are_otherwise_identical_to_scan_at_l0():
    """除了 `agent_index`,analyze 的规则集与 scan 逐字相同,包括仍是 L0。"""
    analyze_by_key = {r.key: r for r in analyze_profile().artifact_rules}
    scan_by_key = {r.key: r for r in scan_profile().artifact_rules}

    assert set(analyze_by_key) == set(scan_by_key)
    for key in analyze_by_key:
        if key == "agent_index":
            continue
        assert analyze_by_key[key] == scan_by_key[key]
        assert analyze_by_key[key].evidence_level == "L0"
