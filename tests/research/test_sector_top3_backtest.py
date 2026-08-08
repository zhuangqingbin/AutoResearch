"""sector_top3_backtest 名实记档(Wave12-T12):模块 docstring / CLI description 里硬写的
"fwd_2_oc" 字样是列名沿革产生的名实不符——计算本身早就跟随 MAIN_RULER(见 main() 里
`forward_returns(piv, P, D, fwd=10)[MAIN_RULER]`),只是文案没跟上。这里只锁文案,不碰
CACHE 依赖的 main() 主体(该函数需要真实 factor_lab CACHE pickle,不在本任务改动范围)。
"""
from __future__ import annotations

from autoresearch.common.ruler import MAIN_RULER
from autoresearch.research import sector_top3_backtest as m


def test_module_docstring_names_current_ruler():
    assert MAIN_RULER in m.__doc__


def test_cli_description_names_current_ruler():
    assert MAIN_RULER in m._DESC
