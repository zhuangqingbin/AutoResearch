#!/usr/bin/env python3
"""legacy 账本冻结(E6 转正,task-2.4)—— 旧绝对门那两本账什么时候停止长大。

design: `docs/specs/2026-08-18-e6-activation-learning-slimdown-design.md` §3 E3
(`zero_buy_ledger` 按 activate 日切断新行 + render 加 legacy 横幅;`buy_ledger` 冻结
legacy,保历史渲染、停新行)。`zero_buy_ledger` 的模块 docstring 自 2026-08-08 起就预告
了这件事,这里是照它落地。

**为什么必须停**:E6 接管 BUY 之后,「≥Overweight 的张数」不再是买单数。让这两本账继续
按旧口径记新行,等于在同一张报表里把两种**定义不同的东西**接成一条趋势线 —— 这正是
`relative_ledger` 模块 docstring 反复强调的「定义断层,分列并置,不得连线读」。冻结不是
删除:历史行与渲染原样保留,它们仍是旧绝对门那段历史的唯一记录。

**冻结日的事实源只有一个**:`scan_config.jsonc` 的 `relative_buy.activate_date`
(2026-08-11 用户裁定:config = 全流程唯一参数事实源)。未配置 → `None` → 不冻结 =
现行为(parity)。翻 `mode` 到 active 的那次改动必须同时填它,否则两本账会继续长大。
"""
from __future__ import annotations


def cutoff() -> str | None:
    """legacy 冻结日(YYYY-MM-DD);未配置/配置层故障 → `None` = 不冻结(parity)。"""
    try:
        from autoresearch.scan.relative_buy import activate_date
        return activate_date()
    except Exception:  # noqa: BLE001 — 账本刷新不该被配置层故障拖垮
        return None


def frozen(day: str, cut: str | None) -> bool:
    """该 scan 日是否落在冻结区(**含冻结日当天**)。

    边界含等号:activate 日当天 BUY 已由 E6 拥有,旧账不该再为它记一行。
    """
    return bool(cut) and str(day) >= str(cut)


def banner(cut: str | None, *, what: str) -> list[str]:
    """render 顶部的 legacy 横幅。未冻结 → `[]`(现行为,报表逐字不变)。

    `what` = 这本账记的是什么(供人一眼看出被接管的是哪件事)。
    """
    if not cut:
        return []
    return [
        f"> 🧊 **legacy 冻结**:E6 相对决策层自 **{cut}**(含)起正式接管 BUY —— "
        f"本账本{what}**不再产生新行**,以下全部是冻结前的历史读数。",
        "> 新账在 `relative_buy.md`(相对 BUY 收益 / BLOCKED 原因 / action_coverage)。"
        "**两账决策对象与人口都不同,分列并置,不得接成一条曲线读。**",
        "",
    ]
