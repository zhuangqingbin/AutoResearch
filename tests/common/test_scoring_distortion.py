"""主力占比失真谓词(单一事实源):反号/微量两型。2026-07-03 实证:两型精确命中
L4 逐卡辟谣的 ~18/30 finalist(占比正·绝对净出 11 只 + 微盘放大 7 只)。合成,无网络。
"""
from __future__ import annotations

from autoresearch.common.scoring import main_net_distortion_label


def test_label_sign_flip_and_micro_and_clean():
    assert main_net_distortion_label(0.012, -2.55) == "反号"    # 胜宏 07-03:占比正·绝对净出
    assert main_net_distortion_label(0.087, 0.03) == "微量"     # 东北证券:+8.7% 撑在 0.03亿 上
    assert main_net_distortion_label(0.073, 0.39) == "微量"     # 0.39亿 < 0.5亿
    assert main_net_distortion_label(0.14, 0.91) == ""          # 绝对近 1 亿,可信
    assert main_net_distortion_label(-0.03, -1.17) == ""        # 占比为负=没人当多头论点,不标
    assert main_net_distortion_label(0.01, 0.1) == ""           # 占比 <2%,弱读数不标微量
    assert main_net_distortion_label(None, 1.0) == ""           # 缺值容错
    assert main_net_distortion_label(0.05, None) == ""
