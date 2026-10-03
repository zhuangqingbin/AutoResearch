# scan-market 负结果、退役清单与开放线头

> 2026-09-26 自 `.claude/skills/scan-market/STAGES.md` 迁入;2026-09-30 校正经验声明的尺、窗口与适用范围。机制冲突以源码为准;证据状态见[声明登记](2026-09-30-agent-skills-evidence-register.md)。当前主尺 `gap_c1_o2`;历史负结果不自动迁为当前效果,产品停用裁定仍按现行代码/配置执行。

## 历史负结果与现行停用裁定

- **L2 上模型**:原模型与旧持有尺的负结果支持当时退役;无新的同尺证据与开发决策前不复活,不推广为所有确定性信号无效。[证据:historical_l2_model_value](2026-09-30-agent-skills-evidence-register.md#historical_l2_model_value)
- **业绩预告做 L1 事件通道**:旧 T+5 摘要为超额 −0.27%/胜率 35%,追缺口 −2.92%;完整窗口待复核,不足以推导所有公告后追买都无效或 alpha 必在披露前。当前仍不启用该路线。[证据:historical_forecast_channel](2026-09-30-agent-skills-evidence-register.md#historical_forecast_channel)
- **指数纳入当正向催化**(2026-09-25):历史 17 次调样、5 个半年调样指数(剔科创50)主表的生效前夜 −0.31pp/胜率 35%,窗口只报 2022–2026,日期与成本仍有局限。现行只作事实日期 + 生效前夜守卫(E6 v4.1),不称为守卫已有前向净收益证明。[证据:historical_index_rebalance](2026-09-30-agent-skills-evidence-register.md#historical_index_rebalance)
- **预测调样名单做席位**(2026-09-25):历史预测集 A−1 夜,沪深300 +0.21pp/胜率49%、科创50 +0.55pp/54%;都未达预设胜率≥65%,沪深300还未达超额≥0.4pp。科创50 t_event=2.7,原“两者皆不显著”摘要不准确;完美预见名单也不能代替预测器实绩。路线 C 不立项,重开条件仍见原设计 §2.8。[证据:historical_index_prediction](2026-09-30-agent-skills-evidence-register.md#historical_index_prediction)
- **追当日大涨**:同尺原研究 2022-03-02→2026-08-05,可执行人口中 D 日涨幅≥9.5%桶的 `gap_c1_o2` 相对超额 −0.96%;它量第二夜,剔除 D+1 不可买票,不外推到当夜打板。→ L3 硬约束 H、守卫⑦。[证据:overnight_chase_executable](2026-09-30-agent-skills-evidence-register.md#overnight_chase_executable)
- **健康上涨强制配额**(2026-08-22 撤):原 calibrated 版本普查中 L1·healthy 隔夜 −0.37pp(t=−5.58,29 日);支持当时撤强制配额的诊断,不等于现行所有健康上涨定义的效果。→ L3 硬约束 A。[证据:overnight_channel_census](2026-09-30-agent-skills-evidence-register.md#overnight_channel_census)
- **channel 共振当加分项**:2026-08-21 单日菜单中 `n_channels` 与位置/涨幅正相关;说明当时通道有重叠,不能推广为多路必然追高或收益差。→ L3 rubric ①,pass1 由「≥3 路全入」改「按 composite 取前 5」。[证据:historical_multichannel_overlap](2026-09-30-agent-skills-evidence-register.md#historical_multichannel_overlap)
- **trend lane 高确信**:历史 conviction≥70 被 L4 改判 33%(n=52),完整窗口待复核;分歧率不是正确率。→ L3 硬约束 D。[证据:historical_trend_confidence](2026-09-30-agent-skills-evidence-register.md#historical_trend_confidence)
- **同行业扎堆**:2026-08-21 单日贵金属及下游占 5/9,属集中度诊断。→ L3 硬约束 I、守卫⑧。[证据:historical_multichannel_overlap](2026-09-30-agent-skills-evidence-register.md#historical_multichannel_overlap)
- **低位转强当隔夜信号**:原谓词在 2025-05-23→2026-08-05 的132日隔夜超额 −0.24pp;5~10日正读数不能迁为隔夜优势。→ L3 硬约束 G 的证据边界;进 finalist 的用途是打开菜单形状。[证据:overnight_lowturn_positive_edge](2026-09-30-agent-skills-evidence-register.md#overnight_lowturn_positive_edge)
- **菜单滞回(carryover)**:当时的 token 会计与选择语义不支持保席方案,维持退役;不再用“拒绝已证有效”作为理由,当前同尺证据不足。[证据:overnight_l4_rejection_unproven](2026-09-30-agent-skills-evidence-register.md#overnight_l4_rejection_unproven)
- **L4 TTL 复用**:用户裁定「不要任何复用」(复用票不跑 intel 会新闻冻结)。
- **`stable_context_blocks` 共享块置前**:当时预估收益未达到维护门槛,维持退役;不是已测得的当前节省比例。
- **`performance.sector_brief_mode` A/B**:`finalist_only` 会让 L3 看不到判断型行业 brief、可能改变 finalists,按「性能开关不拥有评级」铁律它不是性能开关。
- **涨停数据做打板/隔日溢价信号**:现行产品边界只进温度计;第二夜大涨桶研究不能代替当夜打板检验。[证据:overnight_chase_executable](2026-09-30-agent-skills-evidence-register.md#overnight_chase_executable)
- **`_l3_calibration.md`(L3 因子方向经验校准块)**:2026-08-21 随 learning 层退役删除;它没有生产者,此前却被写成 l3-rank 的必读硬约束。勿恢复,除非先有真实生产者。
- **L3.5 收窄层**:用户裁定完全移除;conviction 按现行行为化定义使用,不再把旧 T+2 摘要作为当前隔夜效果保证。

## L1 默认停用的 4 路(及理由)

| 通道 | quota/floor | 停用理由 |
|---|---|---|
| accumulation | 120/30 | 历史 06-22→07-08 旧尺 unique 超额 −0.21%,原并入 reversal_confirm。[证据:historical_accumulation](2026-09-30-agent-skills-evidence-register.md#historical_accumulation) |
| northbound | 120/30 | 历史 hk_ratio T+2 IC −0.108,窗口待复核;信息已在 L4 简报行。[证据:historical_northbound](2026-09-30-agent-skills-evidence-register.md#historical_northbound) |
| sector_momentum | 150/0 | EXP-2 challenger 数据腿,唯一消费者随 2026-08-21 闭环退役删除,现在无尺可裁 |
| event | 80/20 | 公告事件(回购/增持去重、调研只作有无;排序 `ev_hard`+composite);取证渠道随闭环删除;L2「事件」桶 floor=0 |

⚠️ `funnel.recall_channels` **缺省 = 用全部 14 路**(`config.py` 的 `recall_channels: list[str] | None = None`),删掉整行会把这 4 路一并上线。注册数真值:`uv run --no-sync python -c "from autoresearch.scan.recall.registry import registered_channels; print(len(registered_channels()))"`。

**L1 已知局限**:当前主尺已确定为 `gap_c1_o2`,历史 regime/horizon 讨论不能改变它。momentum 的早期 unique +0.75% 与随后26日 −1.07%都是旧 `fwd_2_oc`,不是当前隔夜失效结论。[证据:historical_momentum](2026-09-30-agent-skills-evidence-register.md#historical_momentum) `pre_healthy` 语义在 2026-07-25 有定义断层,跨该日不能直接连线。

## 2026-08-21 learning 层退役清单(同批连带退役;判据只有一条:输入没人生产了,就不留)

| 类别 | 删了什么 |
|---|---|
| 门 | GATE0 preflight(唯一输入是 retro/t1 欠账,闭环一走恒 PASS) |
| 报告节 | `scan/near_miss.py`(三个数据源全是学习账本)、brief ⑦ 欠账节 + 「旧 OW 基率」分账行 + ① 的两尺分歧、summary 的经验/未决反馈节 + 影子 NAV 行 |
| prompt 注入 | L3 表尾两个校准块、L4 的 🔁 基率 / 📐 目标价锚 / 📚 判例 / 行业备忘录 |
| 离线研究仪器 | 漏斗回放器、两尺对照、通道整编、overnight_evidence、立项账本、l2_grid、winner-capture SLO、`lowturn_precheck --live` 腿 —— 全部以 `retro/attribution.csv` 为输入 |
| L1 | 影子漏斗 5 变体 + `write_shadow_variants` + `--no-shadow` |
| 档案 | `dossier/ledger.py`(t1 快环战绩 + retro 归因桶),§7 只剩确定性入围史 |
| 体检/清单 | `run_health` 的 `ledger_freshness`/`retro` 两键、`artifacts` 的 5 个 retro/shadow 产物条目、成本效率的两个分母、`self_review.dump_ow_gate_fires` |
| 开关/入口 | `prewarm --with-calibrate`、`relative_buy preflight` verb、`scan_config.jsonc` 的 `learning` 块 |

D1(2026-08-19,用户裁决 A3)删掉了预注册状态机(`experiment_registry`/`promotion`/`rollback_watch`/`mainflow5d`);D2(2026-08-21)删掉了它剩下的全部证据来源。**E6 不受影响**:相对 BUY 的所有权在 `scan/relative_buy.py`,它从不读账本。

## E6 版本沿革

- **v3.0(2026-08-26 §3 路A)**:A2 硬门扩集(`research_rating ∈ {Sell, Underweight}` 或卡面 `FINAL TRANSACTION PROPOSAL: SELL` 或早停停因 ∈ {基本面恶化, 估值透支, 涨停追高, 数据不足} → 否决);A1/A3 候选池 `relative_buy.pool` 可切 `composite`;A4 执行线两行。历史依据须分开读:08-22 普查 L3 finalist −0.27pp(t=−3.94)的分母为39日,composite +0.14pp有30/39日在校准样本内;不能混成08-26重跑的40日结论或迁到 preference 权重。[证据:overnight_channel_census](2026-09-30-agent-skills-evidence-register.md#overnight_channel_census) 区间位置的1086日盘后代理只给月份窗口,亦不证明盘中可执行性。[证据:historical_close_position](2026-09-30-agent-skills-evidence-register.md#historical_close_position)
- **v4.0(2026-09-24 §2.6)**:`relative_buy.tiering` 总闸;票级 `data_a`;入场门(`entry_stance=="PROHIBITED"` 进 `no_redflag`);A/R 分级;盲卡不入账。**A 级只记录、不设门**:早停卡不得写「允许」会限制 A 级覆盖,当前占比按同版 run/卡集合计量,不以历史早停摘要替代;不得为凑单放松 Hold 四条件。早期禁止票事件还需区分 `card_context` schema 上线前后是否实际记录字段。[证据:historical_p3_stop_distribution](2026-09-30-agent-skills-evidence-register.md#historical_p3_stop_distribution)
- **v4.1(2026-09-25 §2.4)**:`relative_buy.rebalance_gate` 第五硬门 `rebalance_close`(调样生效前夜的调样票否决);`index_events` 块 `source` 四态 `ok|absent|disabled|error` 各自留痕。历史五指数主表 E−1 −0.31pp/胜率35%有事件日期推断与样本范围局限,不视为该版本前向绩效。[证据:historical_index_rebalance](2026-09-30-agent-skills-evidence-register.md#historical_index_rebalance)

## 历史产物(只读,无人再生产)

盘上已有的账本文件原样保留:`$CTX/learning/*.jsonl`、`$CTX/scan/*/retro/*.csv`、`$RPT/learning/*.md`、`$CTX/knowledge/`(lessons/proposals/precedents.db)。代码侧零读侧、零写侧,纯归档。`$CTX/learning/` 目录名是历史遗留,里面仍有两样活的:`temperature.csv`(S1 温度计)与 `usage_reconcile.jsonl`(计量 streak);目录不改名。

## 开放线头(诚实局限)

1. 历史 regime 子样本与持有尺讨论未形成现行版本的样本外证据;当前主尺固定为 `gap_c1_o2`,周级读数不替代它。
2. healthy 通道反事实、capfloor20 —— 取证渠道随 2026-08-21 闭环退役整个消失;要重开得先重新造一把前向尺。reversal_confirm 与旧 reversal 的 A/B 于 2026-08-19 结束、2026-08-21 重开(起爆硬门③由 `ma_bull` 改为 `above_ma20 ∧ ma5_gt_ma10`),裁决腿已删,配额 150 保留但无尺可裁;`self_review.channel_liveness_lint` 逐日盯「启用通道 0 行」。
3. consensus 积累 <60 日不入线上;anns_d 无接口权限 → 公告情感列曾空,2026-07-30 起 cninfo 兜底;当前覆盖率按本次取数状态计量。
4. 温度计菜单/预算联动待相位判定质量复审。
5. L2「行业」= 东财所处行业(129 个细标签),`l2.sector_cap` 20% 与申万一级板块集中度不是同一约束;另有 L3 守卫⑧的 3 席帽。评估行业帽时应同时报告粒度与实际触发数,更改粒度需独立开发裁定。
6. 仅供研究,非投资建议。
