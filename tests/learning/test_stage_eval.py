from autoresearch.learning import stage_eval


def test_selftest():
    assert stage_eval._selftest() == 0


def test_stage_eval_main_horizon_is_t2():
    """主口径契约(2026-07-10 用户裁定持仓 1~2 日):超短主尺 = fwd_2_oc。"""
    assert stage_eval._RET_MAIN == "fwd_2_oc"
