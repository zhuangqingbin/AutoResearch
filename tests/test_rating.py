"""Unit tests for `autoresearch.agents.utils.rating.parse_rating`(D1.6/D8.3)。

`parse_rating` 此前只有一档:两遍启发式(标签行优先,退回全文第一个评级词),没有专属
测试文件(既有引用点见 `tests/scan/test_publish_details_intel.py` 等,但那些测的是各自
模块的行为,不是本函数)。本文件补上直接单测,并补 D8.3 新增的 ``strict`` 档:strict=True
只认 `contracts.agent_output` L4_CARD 契约声明的行首 `**Rating**:` 标签,找不到 → ``None``
(不再兜底猜成 "Hold")。
"""
from __future__ import annotations

from autoresearch.agents.utils.rating import parse_rating


def test_parse_rating_strict_requires_keyed_line():
    """非 strict 档(旧行为)在这句散文上其实**兜底**到默认值,不是真的抓到 "Buy"——
    "不是 Buy,更接近观望" 里 "Buy," 与后面的中文字符零间隔粘连,两遍启发式都
    抓不到干净的 "buy" 词元(`.strip("*:.,")` 只剥边界字符,中间的逗号剥不掉,句尾的
    中文句号「。」也不在剥离字符集里),于是落到 `default="Hold"`。

    这里必须断言**具体值**而非 `x == A or x`(那种写法对任何实现都恒真,是零鉴别力的
    绿灯,复核轮 1 逮到——变异自证见 batch-B-report.md「修复轮 1」节)。"""
    prose = "我们认为它不是 Buy,更接近观望。"
    assert parse_rating(prose) == "Hold"      # 非 strict:抓不到词元 → 兜底默认(旧行为,必须不变)
    assert parse_rating(prose, strict=True) is None             # 新:strict 不猜


def test_parse_rating_strict_reads_keyed():
    assert parse_rating("**Rating**: Underweight", strict=True) == "Underweight"


def test_parse_rating_strict_tolerates_fullwidth_colon_and_hyphen():
    """契约 pattern 认全角冒号 `：` 与连字符 `-`,不只是英文冒号。"""
    assert parse_rating("Rating：Sell", strict=True) == "Sell"
    assert parse_rating("Rating - Buy", strict=True) == "Buy"


def test_parse_rating_strict_rejects_non_keyed_prose():
    """strict 档不认"卡面正文里恰好出现评级词"——必须是行首标签。"""
    card = "Rubric建议: Overweight\n分析师认为估值合理,接近 Hold。"
    assert parse_rating(card, strict=True) is None


def test_parse_rating_non_strict_default_is_hold_when_nothing_found():
    assert parse_rating("这段话完全没有评级信息。") == "Hold"
