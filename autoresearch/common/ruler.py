#!/usr/bin/env python3
"""主评判尺单点(Wave11 批A)。换尺 = 改 MAIN_RULER 一行;严禁在消费点散写列名字符串。

gap_c1_o2 = open[D+2]/close[D+1] − 1(2026-08-05 用户裁定:T+1 收盘买 → T+2 开盘卖,隔夜)。
沿革:fwd_2_oc(2026-07-10 裁定)→ gap_c1_o2(2026-08-05 裁定);旧列降参考不删。
"""
MAIN_RULER = "gap_c1_o2"     # T16(2026-08-05 用户裁定)换值;fwd_2_oc 降参考尺,不删
GAP_CLIP = 0.31              # 单日板极值:主板10/创业科创20/北交所30cm,取最宽+容差
ENTRY_FLAG = "buyable_c1"    # T+1 收盘封涨停=买不进 → 剔样本
EXIT_FLAG = "unsellable_o2"  # T+2 一字跌停开=卖不出 → 标旗不剔(剔了会美化)
TOUCH_COL = "gap_c1_o2"      # 隔夜窗唯一实现价=T+2 开 → 触价尺=gap 本身(设计稿 touch_o2 的去重简化)
SCHEMA_SWITCH_V4 = "2026-08-07"   # 卡契约 v4 日期分界(T17 绑执行日真值 `date +%F`);此前旧卡判定逐字节不受影响
