# scan 哨兵模式 + token 计量补全 + universe cache 提速 设计

日期:2026-07-03 ｜ 分支:feat/scan-sentinel-economy ｜ 状态:实施中

## 背景(07-02 实跑 22:38,~60 分钟全程)

时间线:universe 21:54(含网络 ~15m)→ 策略师 +6m → **L3 +14m** → merge +4m →
预harvest+13 subagent 并发 +11m → 红队/整合 +9m。token 表只有 L4 一行有数(15 调用/
14k 输出下界),L3/策略师/红队全 "—"(稿没落 staging)。

## 1. 哨兵模式(menu.sentinel_advice,确定性建议;人拍板)

07-02 的教训:菜单病在 21:54 的 L2 体检就已判明,后面 40 分钟 L3+15 卡只是确认。
- `sentinel_advice(scan_dir, frac_lo=0.03, frac_hi=0.05) -> (level, reason)`:
  判据用**全市场**健康上涨占比(`healthy_riser_mask` on L1_scored_full——不受自家 L2
  采样影响;healthy 通道上线后 L2 健康数已被桶"治愈",不能再当哨兵判据)× regime:
  占比 <3%(或谓词缺列时不判)→ `sentinel`;3–5% ∧ risk_off → `sentinel`;3–5% → `consider`;
  ≥5% → `full`。07-02(6.2%,range)→ full = 正确:通道修好后该日**应该**全扫。
- `l4_card.pick_sentinel_candidates(scan_dir, k=2)`:哨兵日红队对象 = L2 gbdt_score top-k
  (哨兵日无 L3 conviction)。
- **哨兵档流程(SKILL)**:策略师 + 观察单日检 + 日历 + 机会成本红队×2(产出进观察单)
  + assemble(presence-gated,无 finalists 也出报告:菜单读数/观察单/日历)。
  跳过 L3 全表与整轮 L4 → 省 ~70% token 与 ~35 分钟。**advice 是建议,人拍板**;
  哨兵日 retro 照常归因(L1/L2 全在),影子对照照算 = 错过率可监控。

## 2. token 计量补全(_stage_token_estimate + 落稿契约)

- 估算器:新增 **策略师行**(market_view.md + `_strategist*`);L3 行并入
  `L3_judged_full.csv`;skeptic 行改 **skeptic/红队**(`_v_*` + verify.csv);
  调用数=稿件数。表尾注明**落稿契约**:编排按 playbook 落 `_l3_table.md`(L3 输入)/
  `_l4_prompt_<code>.md`(每卡完整 prompt)后,本表 ≈ 输入+输出全量下界;缺稿段 "—"。
- playbook 硬性落稿纪律(L3/L4/skeptic 三处);诚实边界:真实计费只有 Claude Code
  `/usage` 可见,系统内永远是可测下界。

## 3. universe 提速(_harvest_vol_series 走湖)

20 次 `pro.daily(trade_date=d)` 直拉 → `get_or_fetch("daily", {"trade_date": d})`
(endpoints 已有 policy:key=date/settle=eod):已结算日湖命中零网络、盘中当日拉新不缓存。
相邻两天重跑 → 19/20 命中。失败回退直拉(外层 try 不变)。网络路径不做离线单测(诚实注明),
靠 `--selftest` 与真实跑动验证。

## 测试
sentinel_advice 阈值/缺列降级、pick_sentinel_candidates、token 估算器(策略师行/L3 csv/
_v_ 计数/缺稿 "—")。合成,无网络。
