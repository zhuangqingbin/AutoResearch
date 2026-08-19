"""gate4 fail 行的门类判定 —— E1a(2026-08-18 设计稿 §3)。

决定一条 self_review fail 是否连坐当日相对 BUY(`relative_buy._data_contract_ok`
的日级 data_a 门)。白名单口径:只有明确与「当日数据可信度无关」的两类前缀豁免;
未登记的 check 一律按 "data" 处理 —— fail-safe:新检查默认连坐,想豁免必须显式登记。
立案:08-07/10/11 三天文档卫生 lint + 计量病经 gate4 团灭全天 BUY(spec §2.1)。
"""
from __future__ import annotations

#: 前缀 → 类别。key 必须与 `learning/self_review.py` 各 check 名的「组前缀」逐字一致。
EXEMPT_PREFIXES: dict[str, str] = {
    "产物形状·": "hygiene",           # 退役符号引用/skill内联字面量/旧尺裸写 等 repo 文档病
    "usage_reconcile·": "metering",   # token 计量对账 streak,与市场数据无关
}


def fail_class(check: object) -> str:
    text = str(check or "")
    for prefix, cls in EXEMPT_PREFIXES.items():
        if text.startswith(prefix):
            return cls
    return "data"
