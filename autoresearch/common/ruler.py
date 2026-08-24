#!/usr/bin/env python3
"""主评判尺单点(Wave11 批A)。换尺 = 改 MAIN_RULER 一行;严禁在消费点散写列名字符串。

gap_c1_o2 = open[D+2]/close[D+1] − 1(2026-08-05 用户裁定:T+1 收盘买 → T+2 开盘卖,隔夜)。
沿革:fwd_2_oc(2026-07-10 裁定)→ gap_c1_o2(2026-08-05 裁定);旧列降参考不删。
"""
from __future__ import annotations

import numpy as np
import pandas as pd

MAIN_RULER = "gap_c1_o2"     # T16(2026-08-05 用户裁定)换值;fwd_2_oc 降参考尺,不删
GAP_CLIP = 0.31              # 单日板极值:主板10/创业科创20/北交所30cm,取最宽+容差
ENTRY_FLAG = "buyable_c1"    # T+1 收盘封涨停=买不进 → 剔样本
EXIT_FLAG = "unsellable_o2"  # T+2 一字跌停开=卖不出 → 标旗不剔(剔了会美化)
TOUCH_COL = "gap_c1_o2"      # 隔夜窗唯一实现价=T+2 开 → 触价尺=gap 本身(设计稿 touch_o2 的去重简化)
SCHEMA_SWITCH_V4 = "2026-08-07"   # 卡契约 v4 日期分界(T17 绑执行日真值 `date +%F`);此前旧卡判定逐字节不受影响

# T22(Wave12 E6-0,用户 2026-08-08 追加裁定的地基):系统对外只有一种 BUY——"今日可交易
# 全集里相对最值得买"(不承诺绝对上涨)。相对基准 = 全市场可交易等权为主、行业中性超额为辅。
#
# I-1(final-review 2026-08-08/09,人口裁定并留痕):「全市场可交易」显式指**不区分是否
# 过 L0 门**的全体可交易票(`entry_tradable()` 折叠后的全集,含漏在 L0/L1/L2 的票,只要
# 当日真能买、`REL_GAP_RULER` 有数就入分母)——不是仅 L0 过门的子集。这与本任务书 Interfaces
# 一度写的「L0 可交易全集」字面冲突;冲突以用户裁定为准(此处引用的正是用户裁定原文
# "全市场可交易等权"),不是被忽略的差异。理由:①边际分析的 `excess_2`
# 市场基准(见下方 I-2 互指)同样不限于 L0,两条线人口口径需要一致,否则连"漏在 L0 的票
# 相对市场表现如何"这种最基本的复盘问题都答不出来;②`rel_gap_sector` 的分母天然只含
# 有 `industry` 的行(≈L0 过门票),若 `rel_gap_market` 改采 L0-only,两列人口会重新对齐,
# 但代价是丢失"漏在 L0 的票相对全市场基准表现"这个读数——这正是本列存在的意义之一
# (零买复盘/审计要看这个)。留痕(该测试已随 2026-08-21 闭环退役删除):
# 的 `test_missing_industry_makes_rel_gap_sector_nan_but_not_rel_gap_market` 断言了
# L0-missing 但可交易的票确实会拉动市场均值(不是被静默排除),该断言即本裁定的可执行记录。
#
# I-4(final-review 2026-08-08/09,口径钉尺):两列口径钉死在字面量 `REL_GAP_RULER`
# ("gap_c1_o2"),**不**跟随 `MAIN_RULER` 动态漂移——原注释曾声称"换主尺时这两列跟着
# 重算",这是假的:`_backfill_rel_gap_columns` 的幂等门是"两列都在 → 直接 return False"
# (不问口径是否已变);若真跟随 `MAIN_RULER`,批A 回滚杆把 `MAIN_RULER` 改回 `fwd_2_oc`
# 后,老行仍是 gap 口径值、新写的行会变成 fwd_2_oc 口径值,同一列名下静默混存两把尺,且
# 无 `ruler` 型标签区分(`_KEEP`/`ruler` 列那套是为兼容"同一账本存在多种口径历史行"设计
# 的,这里选更简单的路:口径压根不随 `MAIN_RULER` 走,列名即口径,回滚杆不污染)。
REL_MARKET = "rel_gap_market"    # gap_c1_o2 − 当日全市场可交易(entry_tradable)等权均值
REL_SECTOR = "rel_gap_sector"    # gap_c1_o2 − 同申万一级可交易等权均值(行业中性辅;缺行业/该行业当日无可交易成员→NaN,不猜)
REL_GAP_RULER = "gap_c1_o2"      # 字面量,REL_MARKET/REL_SECTOR 的唯一口径来源;不要改用 MAIN_RULER(I-4)

# I-2(final-review 2026-08-08/09):边际分析的 `day_frame`(模块已随 2026-08-21 闭环退役删除)
# 的 `excess_2` 是全仓另一个独立的市场基准(同一批可交易票的 MAIN_RULER **中位**,服务 L3
# 内部反事实比较,跟随当前 MAIN_RULER),其 docstring 明文写"换基准就换了口径,跨模块比较
# 立刻失真,所以这里不另造一个"——REL_MARKET/REL_SECTOR 确实是在造第二个,是刻意的:
# 服务对象不同(E6 对外相对 BUY 账本 vs L3 内部反事实)、统计量不同(均值 vs 中位)、口径
# 稳定性要求不同(钉死字面量 vs 跟随 MAIN_RULER)。两条线在同一天可能给出符号相反的
# "相对表现"读数(均值/中位在右偏分布下相差 10-30bp 是常态),这是已知、记账在案的口径
# 分裂,不是遗漏同步——跨模块比较前必须先确认在读同一条线。

_LEGACY_ENTRY_FLAG = "buyable"   # fwd_2_oc(D+1 开盘买腿)对应旗;换尺前一直如此,不改名


def entry_flag_for(ruler_name: str | None = None) -> str:
    """主尺 → 入场旗列名(资格过滤的**唯一**选旗点;2026-08-08 final-review C1 修复)。

    `gap_c1_o2`(隔夜尺,T+1 收盘买腿)→ `ENTRY_FLAG`("buyable_c1");其余(含 `fwd_2_oc`
    旧尺 —— 批A 回滚杆会把 `MAIN_RULER` 改回它,以及 `fwd_5_oc`/`fwd_10_oc`/`fwd_1_oo` 这
    些同样以 D+1 开盘为入场腿的参考 horizon)→ 落回 "buyable"(D+1 开盘买腿,`sealed` 定义
    见 `factor_lab.forward_returns`)。

    消费点一律经此函数取列名,不得各自写三元表达式 —— 2026-08-08 final-review 抓到 10 个
    资格过滤消费点各自硬编码 "buyable" 字面量,换尺(T16 flip 到 gap_c1_o2)后全部仍读旧腿,
    是「A 建的字段(`ENTRY_FLAG`/`EXIT_FLAG`)B 没消费」的教科书案例(C1)。
    """
    return ENTRY_FLAG if (ruler_name or MAIN_RULER) == "gap_c1_o2" else _LEGACY_ENTRY_FLAG


def entry_tradable(frame: pd.DataFrame, ruler_name: str | None = None,
                   *, default: bool = True) -> pd.Series:
    """`frame[entry_flag_for(ruler_name)]` → 定值(无 `<NA>`)bool Series,消费点资格过滤用。

    行为对齐仓库里 6 处同构 `_bool_series`/`_bool_column` 族工具的三层解析(native bool
    dtype / pandas 可空 "boolean" 扩展 dtype / CSV 往返后的 object dtype 字符串),**唯一
    差异**在"缺席"的折叠方向:

    * 列整体不存在(旧数据 / 该 ruler 分支从未产出过该列)→ 全 `default`。
    * 列存在但个别行是 `<NA>`(pandas 可空布尔的真缺失,如 D+1 pct_chg 缺失的 IPO 首日)
      或字符串解析不出真值(CSV 空单元格)→ 同样折 `default`,**不是**硬编码 False。

    默认 `default=True`("未知按可执行处理")是 2026-08-08 final-review C1 的裁定,与
    原 `research.ruler_compare.gap_frame`(已随 2026-08-21 闭环退役删除)的 `buyable_c1.fillna(True)`
    同一选择 —— docstring 原话「未知按可执行处理,显式选择,镜像 `attribution.csv` 的
    `tradable = buyable.fillna(True) & MAIN_RULER.notna()`」,不为同一个可空布尔发明第二套
    NA 语义。

    这不是在放宽 `_bool_series` 族的既有行为(那 6 处工具遇到别的列——`mature`/`opportunity`/
    `recalled`/`tradable` 本尊等——时的语义完全不受影响,本函数只处理**入场旗**这一列,且是
    消费点应该直接改引用的对象,不是去修那 6 处工具本身)。真正需要"不确定就是不确定"、绝不
    可折叠成任何一侧的地方是**生产** `buyable_c1` 本身(`factor_lab.forward_returns`,已有
    独立测试锁 `<NA>` 不被伪造成 True/False);这里是**消费**它做资格过滤的边界,两处职责
    不同——生产端诚实记录未知,消费端才对"未知怎么算"作出一个记账在案的选择。
    """
    col = entry_flag_for(ruler_name)
    if col not in frame.columns:
        return pd.Series(default, index=frame.index, dtype=bool)
    values = frame[col]
    if values.dtype == bool:
        return values.fillna(default)
    if str(values.dtype) == "boolean":            # pandas 可空扩展 dtype(真 <NA>)
        return values.fillna(default).astype(bool)
    # CSV 往返后常见的 object dtype:字符串 "True"/"False" 混 NaN/None(空单元格)
    as_str = values.astype(str).str.strip().str.lower()
    is_true = as_str.isin({"true", "1", "yes", "是"})
    is_false = as_str.isin({"false", "0", "no", "否"})
    known = is_true | is_false
    return pd.Series(np.where(known, is_true, default), index=values.index, dtype=bool)
