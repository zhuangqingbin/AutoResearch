#!/usr/bin/env python3
"""内置 channel 注册表 —— 全复用 common.scoring(零新因子数学)。

注册数 ≠ 启用数:生产启用路以 `scan_config.funnel.recall_channels` 为准(现 9 路),
`event` / `sector_momentum` 两路**默认不启用**,只在影子变体里跑并各自攒
`unique_excess_t2` 累计证据(见各自 docstring)。

design: docs/specs/2026-06-22-l1-multi-recall-design.md §9 路 channel 表。
每路:对 scored 帧(已含 composite + 因子列)过门 + 按策略信号降序 + 截 top-k。
accumulation 复用 composite_score 既有吸筹判据(底部放量 + 主力未撤),不重写。
"""
from __future__ import annotations

import pandas as pd

from autoresearch.common.scoring import (
    _num,
    _pct,
    lens_growth,
    lens_momentum,
    lens_reversal,
    lens_reversal_confirm,
    lens_value,
)
from autoresearch.scan.recall.base import empty_result, gate_rank
from autoresearch.scan.recall.registry import channel


@channel("composite", quota=400, floor=100, desc="IC 校准复合分(=今天)")
def composite(frame, date, k):
    return gate_rank(frame, None, "composite", k)


@channel("momentum", quota=250, floor=50, desc="趋势龙头(lens_momentum 过门)")
def momentum(frame, date, k):
    g = lens_momentum(frame)
    return gate_rank(g, g["momentum_gate"], "momentum_score", k)


@channel("reversal", quota=200, floor=50, desc="困境反转(lens_reversal 过门)")
def reversal(frame, date, k):
    g = lens_reversal(frame)
    return gate_rank(g, g["reversal_gate"], "reversal_score", k)


@channel("reversal_confirm", quota=200, floor=50, desc="反转确认(四段,起爆日硬门)")
def reversal_confirm(frame, date, k):
    """与 `reversal` 双路并跑(影子对照,不动旧路):lens_reversal_confirm 的起爆日硬门比旧路的
    "边际改善∨资金即放行"更严——channel_eval 按 lane 分行累计 ≥10 日后裁决新旧路优劣。"""
    g = lens_reversal_confirm(frame)
    return gate_rank(g, g["reversal_confirm_gate"], "reversal_confirm_score", k)


@channel("growth", quota=150, floor=40, desc="成长加速(lens_growth 过门)")
def growth(frame, date, k):
    g = lens_growth(frame)
    return gate_rank(g, g["growth_gate"], "growth_score", k)


@channel("value", quota=200, floor=50, desc="行业内低估(lens_value 过门)")
def value(frame, date, k):
    g = lens_value(frame)
    return gate_rank(g, g["value_gate"], "value_score", k)


@channel("main_fund", quota=200, floor=50, desc="主力净流入")
def main_fund(frame, date, k):
    score_col = "main_net_ratio" if "main_net_ratio" in frame.columns else "main_inflow_yi"
    mask = (_num(frame["main_inflow_yi"]) > 0) if "main_inflow_yi" in frame.columns else None
    return gate_rank(frame, mask, score_col, k)


@channel("northbound", quota=120, floor=30, desc="北向(hk_ratio)")
def northbound(frame, date, k):
    mask = (_num(frame["hk_ratio"]) > 0) if "hk_ratio" in frame.columns else None
    return gate_rank(frame, mask, "hk_ratio", k)


@channel("accumulation", quota=120, floor=30, desc="底部吸筹(投机高召回,交下游证伪)")
def accumulation(frame, date, k):
    if "vol_ratio" not in frame.columns:
        return gate_rank(frame, None, "vol_ratio", k)   # -> 空帧
    low_pos = pd.Series(False, index=frame.index)
    if "winner_rate" in frame.columns:
        low_pos = low_pos | (_num(frame["winner_rate"]) < 40)
    if "price_to_cost" in frame.columns:
        low_pos = low_pos | (_num(frame["price_to_cost"]) < 1.0)
    not_high = (_num(frame["pct_60d"]) < 20) if "pct_60d" in frame.columns else pd.Series(True, index=frame.index)
    main_ok = (_num(frame["main_net_ratio"]) >= 0) if "main_net_ratio" in frame.columns else pd.Series(True, index=frame.index)
    mask = (_num(frame["vol_ratio"]) >= 1.5) & low_pos & not_high & main_ok
    return gate_rank(frame, mask, "vol_ratio", k)


@channel("healthy", quota=150, floor=40,
         desc="质量上涨(0<pct60<40 ∧ 主力+ ∧ cmf+;menu_health 病灶指标直接变召回信号)")
def healthy(frame, date, k):
    """swing 品相通道(2026-07-03,治 07-02 根因):261 只健康上涨 0 只进池——温和上涨
    进不了 momentum 路 top(被 100%+ 猛票占满)、不够底不过吸筹门、value 分平平被 range
    权重的 composite 压到 rank 3760。本路以 `healthy_riser_mask`(与菜单体检同一谓词,
    单一事实源)过门,按**主力×资金共振强度**排序(pct(main)+pct(cmf);门内已限温和
    区间,不再按动量排——要质量不要 froth)。缺列 → 空帧降级(与其他路一致)。
    """
    from autoresearch.common.scoring import healthy_riser_mask
    mask = healthy_riser_mask(frame)
    if mask is None:
        return gate_rank(frame, None, "healthy_score", k)   # 缺核心列 → 空帧
    g = frame.copy()
    g["healthy_score"] = _pct(g["main_net_ratio"]).fillna(0.0) + _pct(g["cmf_20"]).fillna(0.0)
    return gate_rank(g, mask, "healthy_score", k)


@channel("heat", quota=200, floor=50,
         desc="高热(成交额量级主轴 × 换手/量比 kicker;捞巨额成交龙头,免疫 composite 的 IC froth 惩罚,交下游证伪)")
def heat(frame, date, k):
    """按成交额绝对体量排序,不过门(top-k 即资金最集中的 k 只)。

    composite 是 T+1 IC 校准——它**故意压抑**抛物线龙头(过热 −8/−15 + 主力出逃拖累),
    像中际旭创(成交额全市场第 2、composite 仅 32)在召回近乎隐形。本路与 composite 正交:
    只看『钱在哪』,floor 保底把成交额最大的 ~50 只无条件送进 L2,让 Claude 定性判断,而非被
    froth 统计惩罚提前筛掉。

    **机制(成交额主导)**:实测百分位混合(amount/turnover/vol_ratio 各取分位再加权)行不通——
    rank 把 386亿 压成 0.9998(与第 100 名仅差 2pt),换手/量比却能 0→1 全摆,于是 surfaces 的全是
    小盘换手异动股,中际旭创(换手仅 2.5%、量比 0.98 偏低)反而进不来。改用**成交额量级当乘法主轴**:
    `heat = amount_yi × (1 + 0.15·pct(换手) + 0.10·pct(量比))`。kicker ≤1.25×,压不过量级,只在
    成交额相近时让换手/量比更高者靠前(东方财富式今日异动)——既锁定中际旭创/龙头,又兼顾活跃度。
    缺 amount_yi → 空帧降级(与其他路一致)。
    """
    if "amount_yi" not in frame.columns:
        return gate_rank(frame, None, "heat_score", k)   # 无成交额主轴 → 空帧
    g = frame.copy()
    kicker = pd.Series(1.0, index=g.index)
    if "turnover" in g.columns:
        kicker = kicker + 0.15 * _pct(g["turnover"]).fillna(0.0)
    if "vol_ratio" in g.columns:
        kicker = kicker + 0.10 * _pct(g["vol_ratio"]).fillna(0.0)
    g["heat_score"] = _num(g["amount_yi"]).fillna(0.0) * kicker
    return gate_rank(g, None, "heat_score", k)


@channel("sector_momentum", quota=150, floor=0,
         desc="板块动量上涨侧(行业 pct_60d 中位 > 0,按板块排序、板块内按 composite;"
              "**影子专用**:floor=0、不在 scan_config.recall_channels)")
def sector_momentum(frame, date, k):
    """EXP-2 `exp_20260801_recall_sector_momentum` 的 challenger 数据腿(Wave12-T20)。

    预注册于 2026-08-01,但**通道本身一直不存在**(`registered_channels()` 里查无此名),
    于是 registry 的 `observations` 空转 —— 消费者在等一个没人生产的产物(FN-1 家族)。

    **信号**:板块动量 = 同行业 `pct_60d` 的**中位**,与 `l2_stratify.stratified_l2` 里
    `sector_mom` 列**同一事实源、同一公式**(那一列在 L2 阶段才生成,L1 帧里没有,所以这里
    自己算,不是另造一个口径)。上涨侧硬门 `sector_mom > 0`:板块动量非正的行业整体不召回。

    **绝不用当日个股涨幅**(`pct_1d`)——2026-07-24 实证「追当日大涨是负价值」(07-21 当日
    ≥9.5% 的 350 只票超额 −3.67pp,t=−11.91;那是旧尺读数,此处只作历史沿革引用)。本路
    连**并列层的第二键**都不许是当日涨幅:同一板块内所有成员的 `sector_mom` 完全相同(它是
    板块级的量广播到成员),若第二键取涨幅,「板块动量选行业、当日涨幅选个股」等于把铁律从
    后门放回来。故第二键 = `composite`(缺列则退回代码升序,确定性但不带信号)。

    **排序实现**:`gate_rank` 只有一个排序键且 `kind="stable"`,所以并列层的顺序 = 传进去的
    帧行序(与 `event` 路 I-3 同一个坑)。这里**先**按 `(sector_mom desc, 第二键 desc)`
    预排,再把 `sector_mom` 交给 `gate_rank` —— stable 排序保留预排序,等价于严格的字典序,
    且不需要发明一个"两个量加权求和"的复合分(权重一旦选错就会让第二键翻掉第一键)。

    **零副作用**(「默认不启用必须连副作用一起不启用」,Wave4 事件桶 floor=10 判例):
    `floor=0` ⇒ `quota_union` 里本路一票都不受保护;`STYLE_CHANNELS` 没有映射到本路的桶
    ⇒ 不进 `DEFAULT_FLOORS`、不改 `merit_need`、不产 `l2_lane_reserved` 标签;不在
    `scan_config.funnel.recall_channels` 里 ⇒ 生产召回逐字节不变。它只在
    `universe.write_shadow_variants` 的 `plus_sectormom` 反事实里跑,产物是
    `shadow/L1_channels_plus_sectormom.csv`,由 `channel_audit --variant plus_sectormom`
    按与 accumulation 2026-07-11 退役同一套 `unique_excess_t2` 裁决。

    **`quota` 不是 0**:预注册 spec 的「不占 quota」说的是**不挤生产名额**(=floor=0),
    而本仓库的 `quota` 是每路 top-k 截断,写 0 会让 `gate_rank(...).head(0)` 恒返回空表 ——
    仪器天天产出「什么都没有」,正是本 task 要修的那个病。取 150(与 healthy/growth 同量级)。

    缺 `industry` / `pct_60d` → 空帧降级(与其余各路同契约)。
    """
    if "industry" not in frame.columns or "pct_60d" not in frame.columns:
        return empty_result()
    g = frame.copy()
    mom = _num(g["pct_60d"])
    g["sector_mom"] = mom.groupby(g["industry"].astype(str).to_numpy()).transform("median")
    mask = g["sector_mom"] > 0
    if not bool(mask.any()):
        return empty_result()                          # 无上涨侧板块 → 不召回
    tiebreak = "composite" if "composite" in g.columns else "code"
    # composite 降序(分越高越靠前);退化到 code 时升序(小代码在前 = 确定性,不带信号)。
    g = g.sort_values(["sector_mom", tiebreak], ascending=[False, tiebreak == "code"],
                      kind="stable")
    return gate_rank(g, mask.reindex(g.index), "sector_mom", k)


@channel("event", quota=80, floor=20,
         desc="公告事件(近10日回购实施/预案+增持,按公告去重计件;调研只做门不排序;非涨幅信号)")
def event(frame, date, k):
    """事件驱动召回(Wave4)——补漏斗唯一的"有实质公告但价格还没反应"缺口。

    **不用当日涨幅**:2026-07-24 实证,07-21 当日 ≥9.5% 的 350 只票 fwd_2_oc −2.06%
    vs 全市场 +1.60%(超额 −3.67pp,t=−11.91)——追当日大涨是负价值。本路只问
    "近 10 交易日有没有发生正催化事件"。

    **排序用 `ev_hard`(真实公司行为:回购实施+回购预案+增持),不用 `ev_pos`**
    (Review Round 1 I-5):门槛仍用 `ev_pos>0`(调研也是弱催化,不完全排除),但排序若
    直接用 `ev_pos`,2026-07-21 真湖实证是**裸 `ev_pos` 降序 top10 全部 10/10 是纯调研**
    (max=82 = 000729 一次接待 82 家机构;而一次回购实施只算 1)——这条召回路会实际变成
    "近 10 日被调研机构家数排行",不是"事件强度"。改按 `ev_hard` 排序后,纯调研票仍可
    经 `ev_pos>0` 入池,但排在所有有真实公司行为的票之后(`ev_pos` 定义见 `events.py`:
    `ev_hard + min(ev_surv_n, 1)`,调研最多贡献 1)。

    **排序分是复合分,不是裸 `ev_hard`**(Review Round 2 I-3 / Round 1 I-1):`ev_hard` 是
    离散小整数,而 `gate_rank` 只有一个排序键、`kind="stable"` ⇒ 并列层内的顺序 = **帧行序**
    (`recall_select` 跑在 `scored.sort_values("composite")` **之前**,进来的是
    `build_market_frame` 的原始 ts_code 序 = 事实上任意)。2026-07-21 真湖(按公告去重后)
    门内分布 `{3:12, 2:26, 1:218}`,quota=80 ⇒ **只有 38 席按信号排,其余 42 席(52%)从
    218 只并列票里靠代码序切**;实测换帧行序(code 升序 vs 随机序)入选名单差 17 只,且
    code 序系统性偏好 `000/002` 前缀。这会直接稀释 `unique_excess_t2` 十日审批的信噪
    ——审的是一个"四分之一名单靠抽签"的排序器,读数无法归因。
    故本路自己造 `event_score`(**不改 `gate_rank` 的公共契约**,其余 10 路照旧):

        event_score = ev_hard + 0.5·「有没有调研」 + 0.2·pct(composite)

    两个 kicker 合计 ≤0.7 < 1,**压不过一个整数级差**(主轴仍是硬事件件数);第二键
    `ev_pos − ev_hard`(即 `min(ev_surv_n,1)`,取值 0/1)让"有真事件又被调研"的排前面;
    第三键 `pct(composite)` 把剩下的并列层排开 —— 并列不再由行序决定。缺 composite 列时
    第三项退化为 0(此时并列层重新退回帧行序,是降级不是设计)。

    缺列 → 空帧降级(与其余 10 路同契约)。门内 `ev_hard` 全 0(整池只有调研、没有一件
    回购/增持)→ 同样退化为空帧,不召回一整池纯调研票。**默认不启用**:须
    `channel_audit` 的 unique_excess_t2 累计 ≥10 日为正 + 人批才进
    scan_config.funnel.recall_channels(与 accumulation 2026-07-11 被裁同纪律)。
    """
    if "ev_pos" not in frame.columns or "ev_hard" not in frame.columns:
        # 两列缺任一都要空帧:若只有 ev_hard 缺而 ev_pos 还在,按 ev_pos 排序会返回非空
        # (那正是 I-5 要治的"调研家数排行");见 test_event_channel_missing_ev_hard_
        # degrades_to_empty。
        return empty_result()                               # 缺列 → 空帧
    mask = frame["ev_pos"].fillna(0.0) > 0
    if not bool(mask.any()):
        return empty_result()                               # 全 0 → 空帧(不召回零事件票)
    hard = _num(frame["ev_hard"]).fillna(0.0)
    if not bool((hard[mask] > 0).any()):
        return empty_result()                               # 门内全是纯调研 → 空帧(不召回)
    g = frame.copy()
    surveyed = (_num(g["ev_pos"]).fillna(0.0) - hard).clip(lower=0.0, upper=1.0)
    tiebreak = _pct(g["composite"]).fillna(0.0) if "composite" in g.columns else 0.0
    g["event_score"] = hard + 0.5 * surveyed + 0.2 * tiebreak
    return gate_rank(g, mask, "event_score", k)
