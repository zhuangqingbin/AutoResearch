#!/usr/bin/env python3
"""industry 标签(东财所处行业/申万一级)→ ~7 大类板块。用于 factor_lab 层级收缩的中间层。

universe 的 `industry` 来自 stock_yjbb_em 的「所处行业」(东财口径,~80+ 标签),不是规整的
申万一级。这里用「子串包含」把任意标签归到 ~7 大类,鲁棒于口径漂移;未命中 → '其它'。
校准期(P1 Task 10)用真实 universe 的 industry.value_counts() 复核,把落「其它」的大标签补进规则。
"""
from __future__ import annotations

# 大类板块(收缩中间层)。键 = 大类,值 = 命中子串(对 industry 标签做"包含"匹配)。
_SECTOR_RULES: dict[str, tuple[str, ...]] = {
    "周期资源": ("煤炭", "石油", "石化", "有色", "钢铁", "化工", "化学", "采掘", "建材", "建筑材料"),
    "制造": ("机械", "电力设备", "电气", "军工", "国防", "汽车", "新能源", "光伏", "电池",
             "装备", "工业", "电源设备", "运输设备", "仪器仪表"),
    "消费": ("食品", "饮料", "白酒", "家电", "家用电器", "纺织", "服装", "服饰", "轻工", "造纸",
             "商贸", "零售", "贸易", "社会服务", "旅游", "酒店", "餐饮", "美容", "护理",
             "农林", "牧渔", "养殖", "种植", "饲料", "食品饮料"),
    "医药": ("医药", "生物", "医疗", "中药", "器械", "化学制药", "医疗器械"),
    "TMT成长": ("电子", "半导体", "计算机", "软件", "通信", "传媒", "互联网", "光模块",
                "消费电子", "元件", "光学", "游戏", "影视", "IT", "云"),
    "金融地产": ("银行", "保险", "证券", "非银", "金融", "房地产", "地产", "多元金融"),
    "公用": ("公用", "电力", "燃气", "水务", "环保", "交通运输", "港口", "高速", "公路",
             "机场", "航运", "物流", "运输", "铁路"),
}


def super_sector(industry: str | None) -> str:
    """industry 标签 → 大类板块;无标签/未命中 → '其它'。"""
    s = str(industry or "")
    for sector, needles in _SECTOR_RULES.items():
        if any(n in s for n in needles):
            return sector
    return "其它"


def _selftest() -> int:
    cases = {
        "煤炭开采": "周期资源", "半导体": "TMT成长", "白酒": "消费", "中药Ⅱ": "医药",
        "股份制银行Ⅱ": "金融地产", "电力": "公用", "电池": "制造", "未知行业xyz": "其它",
        "": "其它", None: "其它",
    }
    fails = [f"{k!r}→{super_sector(k)} 期望 {v}" for k, v in cases.items() if super_sector(k) != v]
    if fails:
        print("SELFTEST ❌")
        for f in fails:
            print(" -", f)
        return 1
    print(f"SELFTEST ✅  super_sector 映射 {len(cases)} 例全过")
    return 0


if __name__ == "__main__":
    import sys

    raise SystemExit(_selftest() if "--selftest" in sys.argv else 0)
