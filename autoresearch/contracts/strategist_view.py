#!/usr/bin/env python3
"""策略师能看见 market_pack 的哪些键 —— 判断层的**投影边界**声明(零 IO,不导入上层)。

2026-09-07(E5 步 5)从 `scan/strategist_pack.py` 下沉。搬的理由不是「少一条边」,而是这份
名单本身是**跨包的边界事实**:`derivatives` 要自证「我还没进判断层」(08-24 期权普查的
I 类边界),`macro` 的产物要说明「我的键一个都不在里面」。让下层包为了读一份边界名单去
import `scan`,方向正好反了。

**只加不减需过 review**:每加一个键,就是多给策略师一个可以据以锚定的东西。名单在这里,
投影逻辑仍在 `scan/strategist_pack.py`(它 import 本模块,同对象)。
"""
from __future__ import annotations

ALLOWED_KEYS: tuple[str, ...] = (
    "regime",
    "breadth",
    "money",
    "valuation",
    "temperature",
    "cross_money",
    "index_val",
    "macro_state",
    "macro_state_note",
    "today_slice",
    "sectors",
)

#: 明确拒绝并写进产物的键 —— 让「为什么少了它」可查,而不是让人以为投影漏了。
#: 未列在这里的新键同样进不来(默认拒绝),只是不会被单独点名。
DENIED_KEYS: tuple[str, ...] = (
    "sector_healthy_top3",   # L5 专用的确定性看多行业排名 —— 策略师看到就会复述
    "run_contract",          # 运行契约:与市场地形无关,且含 pinned 等决策面事实
    "user_config",           # 用户配置:同上
)
