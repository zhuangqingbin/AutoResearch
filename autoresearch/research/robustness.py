#!/usr/bin/env python3
"""F5 步 3/4:块长敏感性(全报不挑)+ walk-forward 的标签重叠清除与交易日 embargo。

实施计划:`docs/superpowers/plans/2026-09-06-research-methods-and-stage-value.md` Task F5。

**这里没有第二套 block bootstrap** —— 区间一律走 `common.stats.block_mean_ci`
(它与 `moving_block_diff` 共用同一个 `block_index`)。同仓两套抽块就是 A 包治过的病。

三条纪律,写在模块头是因为它们比任何一个函数都长命:

1. **块长预登记且全部报告**。事后挑最显著的那个长度,等于又做了一次没登记的检验;
   集合与 `scan.populations.RULER_BLOCK` 同源(主尺 1 日 / fwd_5 5 日 / fwd_10 10 日)。
2. **标签重叠按每行的 label 区间清除**,不是按「训练集结束日」一刀切:`fwd_10_oc` 的标签
   跨 10 个 session,训练集最后 10 行的标签早就伸进测试期了(López de Prado 的 purging)。
3. **embargo 数交易日,不数自然日**。两个自然日碰上周末就是零个交易日,那道闸等于没关。
   日历由调用方传入(`sessions`):本层不读湖,`data.market_panel.lake_trade_days` 是它的
   生产者,谁调谁传。
"""
from __future__ import annotations

from datetime import datetime

from autoresearch.common.stats import DEFAULT_BOOT, block_mean_ci

#: 预登记的块长集合(交易日)。与 `scan.populations.RULER_BLOCK` 的值域同源;不许跑完再加。
BLOCK_SENSITIVITY: tuple[int, ...] = (1, 5, 10)


def block_sensitivity(values, *, seed: int, blocks: tuple[int, ...] = BLOCK_SENSITIVITY,
                      n_boot: int = DEFAULT_BOOT) -> list[dict]:
    """对同一条日等权序列跑**全部**预登记块长 → 每个块长一行。

    返回里没有 `best` / `selected` 这类字段:能挑的字段一旦存在,迟早有人挑。样本装不下某个
    块长时那一行是 `INSUFFICIENT_BLOCKS`,**不删行** —— 删了读者就看不出这个长度试过。
    """
    out = []
    for block in blocks:
        interval = block_mean_ci(values, block=block, seed=seed, n_boot=n_boot)
        out.append({"block": block, "point": interval["point"], "lo": interval["lo"],
                    "hi": interval["hi"], "status": interval["status"]})
    return out


def _aware(value, *, field: str) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None:
        raise ValueError(f"{field} must be a timezone-aware datetime")
    return value


def overlaps(left_start, left_end, right_start, right_end) -> bool:
    """两个**左闭右开**区间是否相交。

    右开是刻意的:测试期起点恰等于训练标签终点时不算重叠 —— 那一行的标签在测试期开始的
    瞬间就已经实现,留着它不构成前视。
    """
    for name, value in (("left_start", left_start), ("left_end", left_end),
                        ("right_start", right_start), ("right_end", right_end)):
        _aware(value, field=name)
    return left_start < right_end and right_start < left_end


def purge_overlap(train: list[dict], test_intervals) -> list[dict]:
    """清除标签区间与任一测试区间相交的训练行。

    每行必须带 `label_start` / `label_end`(带时区,左闭右开,start < end)。区间倒置一律抛错:
    那是上游算错了标签窗,静默放过它会让「清除了重叠」这个结论本身不成立。
    """
    intervals = list(test_intervals)
    kept = []
    for row in train:
        start = _aware(row["label_start"], field="label_start")
        end = _aware(row["label_end"], field="label_end")
        if end <= start:
            raise ValueError("label window must be half-open with start < end")
        if not any(overlaps(start, end, test_start, test_end)
                   for test_start, test_end in intervals):
            kept.append(row)
    return kept


def embargo_intervals(test_intervals, *, sessions, embargo_sessions: int):
    """把每个测试区间的右端**按交易日历**往后推 `embargo_sessions` 个 session。

    `sessions` 是升序的交易日清单(`YYYY-MM-DD` 或 `YYYYMMDD`);区间右端必须**落在**日历上
    ——不在日历上说明调用方给的不是交易日边界,那时推几个 session 都是猜。

    推过日历末尾一律抛错,不夹到最后一天:「日历还没到那天」是**不知道**,把它读成「就到
    最后一天」会让禁运期凭空缩短。
    """
    if type(embargo_sessions) is not int or embargo_sessions < 0:
        raise ValueError("embargo_sessions must be a non-negative int")
    ordered = [str(day).replace("-", "") for day in sessions]
    if embargo_sessions == 0:
        return list(test_intervals)
    out = []
    for start, end in test_intervals:
        _aware(start, field="test_start")
        _aware(end, field="test_end")
        key = end.date().isoformat().replace("-", "")
        if key not in ordered:
            raise ValueError(f"test interval end {key} is not a trading session")
        index = ordered.index(key) + embargo_sessions
        if index >= len(ordered):
            raise ValueError("embargo runs past the end of the supplied trading calendar")
        target = ordered[index]
        moved = end.replace(year=int(target[:4]), month=int(target[4:6]), day=int(target[6:]))
        out.append((start, moved))
    return out


def split_train(train: list[dict], test_intervals, *, sessions,
                embargo_sessions: int) -> list[dict]:
    """先扩 embargo、再清重叠 —— 顺序反了,禁运期里的行会被留下。"""
    return purge_overlap(train, embargo_intervals(test_intervals, sessions=sessions,
                                                  embargo_sessions=embargo_sessions))
