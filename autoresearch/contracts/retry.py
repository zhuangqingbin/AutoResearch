#!/usr/bin/env python3
"""重试分类学 —— 「哪些错误值得再来一次」的唯一声明(零 IO,零上层依赖)。

design: `docs/specs/2026-08-29-full-coverage-research-system-brainstorm.md` §2.2 K7。

## 为什么是两套而不是一套

仓里有两处「瞬时错误」清单,名字几乎一样、内容**故意不同**,2026-08-30 实测:

    scan/l4/intel_status.TRANSIENT_ERRORS = (RATE_LIMIT, CONNECTION, TIMEOUT, ENOTFOUND)
    scan/l4_tasks.TRANSIENT_ERRORS        = {RATE_LIMIT, CONNECTION, TIMEOUT, STALE_TASK}

它们管的是两件事:

- **`INTEL_RESEARCH`** —— 情报站再发一次网查值不值。`ENOTFOUND` 是 DNS 解析失败,
  重试有意义;`STALE_TASK` 在这里毫无意义(网查没有租约这回事)。
- **`TASK_ATTEMPT`** —— 任务簿要不要给这只票再来一次。`STALE_TASK` = 上一次认领的
  租约过期(进程被 SIGKILL / 断电),**正是**该重试的情形;而 `ENOTFOUND` 属于取数层
  自己的事,在任务层已经被归进 `CONNECTION`。

**所以不要把它们合并**。两个名字长得像,是本仓最容易被「顺手统一」掉的一对 ——
统一的后果不是报错,是**默默改掉了两条重试策略里的一条**。把它们并排放在这里,
就是为了让「看起来该合并」的人先读到这段。

JS 侧的 `TRANSIENT`(`l4-stock.js`)用的是 **`INTEL_RESEARCH`** 口径(它在情报相位
决定要不要重搜),由 `contracts.emit` 生成进 workflow 的行内块。
"""
from __future__ import annotations

#: 情报再搜口径:网查失败到什么程度还值得再发一次。
INTEL_RESEARCH: tuple[str, ...] = ("RATE_LIMIT", "CONNECTION", "TIMEOUT", "ENOTFOUND")

#: 任务簿重试口径:一只票的一次尝试失败后还给不给它第二次。
#: `STALE_TASK` = 租约过期(上一个认领它的进程死了),必须可重试,否则那只票永远卡住。
TASK_ATTEMPT: frozenset[str] = frozenset(
    {"RATE_LIMIT", "CONNECTION", "TIMEOUT", "STALE_TASK"}
)

#: 两套的**交集** = 无论哪一层都算瞬时的错误。仅供诊断/展示,不要拿它当判据 ——
#: 判据必须显式选边,因为差集里的两个成员各自都是有理由的。
BOTH: frozenset[str] = frozenset(INTEL_RESEARCH) & TASK_ATTEMPT
