# scan 运行可靠性四件套(卡片契约lint / prelude一键 / attribution刷新 / 触价命中)设计

日期:2026-07-03 ｜ 分支:feat/scan-run-reliability ｜ 状态:实施中

首航(07-02)暴露的四个运行层缺口,全确定性:

## 1. 卡片契约 lint(`self_review.card_contract_lint`)

实证:首航 2 张满卡都没写 `进入P4倾向` 行(p4_seen=0)——LLM 段契约靠 playbook 嘱咐会被忘,
必须机器抓。检查(全 **warn**,不阻发布;并入 banner + gate_fires 留痕):
- 满卡(非早停/非复用)缺 `进入P4倾向:` 行 → 阶段效能计量断供;
- 该票档案可注入(`render_dossier` 非空)而卡缺 `变化项` 节 → 增量研究契约未履行;
- 复用卡跳过全部检查(机器写的)。
assemble `_self_review_banner` 在 dump_gate_fires **前**合并 lint 结果(留痕)。

## 2. prelude 一键化(`autoresearch/scan/prelude.py`)

开扫前全部确定性步骤收成一条命令(各自 try 包裹,失败不阻断后续,最后汇总屏):
attribution 刷新 → retro pending 列出(**只备料不代跑诊断**——诊断是 LLM 段)→ consensus 拉
(限频容忍)→ universe(regime-aware 默认开)→ 日历 harvest → 观察单日检(**触发置顶警报**)
→ menu 体检/L4 预算/哨兵建议 → journal + buy_ledger 刷新。
`--skip a,b` 调试用。编排骨架 `_run_steps` 可单测;整链是已测组件的编排,真跑验证。

## 3. attribution 刷新(`retro.refresh_attributions` + CLI `retro refresh`)

治"买单 ledger 永远 —":attribution 在 retro 时一次性落账,fwd_5/10 未成熟即为 NaN 且永不回填。
对已 done 老日:缺 `fwd_10_oc`/`hi_10_oc` 列或 fwd_5/fwd_10 全 NaN → 重跑 `attribute(date)`
(幂等,价格走 cache)。挂 prelude 首步,fwd 成熟后自动补齐。

## 4. 触价命中(realized_returns 增 `hi_10_oc` + buy_ledger 换口径)

"10 日内**摸到过**目标价"比"第 10 日收盘 ≥ 目标"更贴近真实止盈:
- `realized_returns` 增 `hi_10_oc` = max(high[D+1..D+10]) / open(D+1) − 1(与 fwd_10_oc 同基);
- buy_ledger:目标幅(close_D 基)经 gap_d1 换算到 o1 基后与 hi_10_oc 比;缺 hi → 回退旧收盘口径。

## 测试
lint(满卡/早停/复用/档案变化项/banner 合并)、refresh(monkeypatch attribute:该刷才刷/幂等)、
buy_ledger 触价(hi 摸到但收盘没到 → 命中;缺 hi 回退)、prelude `_run_steps` 骨架。合成,无网络。
