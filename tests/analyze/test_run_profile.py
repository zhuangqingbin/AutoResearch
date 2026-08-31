from autoresearch.analyze.run_profile import analyze_profile
from autoresearch.contracts import stages as vocab

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
