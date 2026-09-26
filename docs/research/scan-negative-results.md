# scan-market 负结果、退役清单与开放线头

> 2026-09-26 自 `.claude/skills/scan-market/STAGES.md` 迁入(A2-3):「已被实证否决的方向」「开放线头」「历史产物」「行为变更的入口」的退役清单、L1 停用通道与 L3 硬约束的证据出处。skill 文档只留现行机制,历史与证据住这里。内容原样,冲突以源码为准。

## 已被实证否决的方向(勿重启)

- **L2 上模型**:全 zoo 负 IC + 回测无稳健 alpha;新特征 IC 过硬之前不复活。
- **业绩预告做 L1 事件通道**:强制披露季 T+5 超额 −0.27%/胜率 35%,追缺口 −2.92%——公告后追买无肉;alpha 若有,在披露前的预期变化里。
- **指数纳入当正向催化**(2026-09-25):17 次调样隔夜尺普查,公告夜 −0.07pp、生效前跑道 −0.10pp(t_day −5.4)、生效前夜 −0.31pp(胜率 35%);「纳入=利好」在主尺不成立,只作事实日期 + 生效前夜守卫(E6 v4.1)。
- **预测调样名单做席位**(2026-09-25):完美预见沪深300 A−1 夜 +0.90pp·胜80%、科创50 +1.29pp·胜78% 看似有肉;但复刻编制规则的预测器精度仅 37–70%(沪深300)/0–80%(科创50),用预测名单算的 A−1 夜超额两者皆不显著,误报票反被罚 −0.44pp·胜率19%;判据「精度≥70% 且胜率≥65%」不达标,路线 C 不立项,重开条件见 `docs/specs/2026-09-25-index-inclusion-signal-design.md` §2.8。
- **追当日大涨**(2026-07-25):−4.85pp t=−13.6;隔夜主尺上最高桶 −0.96%(整区间同号,`docs/research/2026-08-08-overnight-evidence-gap.md` ①)。→ L3 硬约束 H、守卫⑦。
- **健康上涨强制配额**(2026-08-22 撤):edge 普查(`docs/research/2026-08-22-edge-census.md`)量到该画像三把尺全负(L1·healthy 隔夜 −0.37pp t=−5.58、fwd_10 −4.76pp t=−5.5;L3·lane·healthy −0.38pp t=−3.56),此前被三处强制送到最前排;三处强制同批撤除。→ L3 硬约束 A。
- **channel 共振当加分项**:2026-08-21 L2 200 实测 `n_channels` 与 `dist_high_60` spearman 0.34、与 `pct_60d` 0.29;≥3 路的 10 只中位 rsi6 72–76、pct_5d +12%,1 路的 129 只中位 pct_60d −20.2%——多路共振 ≈ 已涨起来。→ L3 rubric ①,pass1 规则②由「≥3 路全入」改「按 composite 取前 5」。
- **trend lane 高确信**:conviction≥70 历史被 L4 翻案 33%(n=52)。→ L3 硬约束 D。
- **同行业扎堆**:2026-08-21 贵金属 4 席 + 下游饰品 1 席 = 5/9。→ L3 硬约束 I、守卫⑧。
- **低位转强当隔夜信号**:决策尺 −0.24pp(132 日,显著),正超额只在 5~10 日尺(+0.63/+0.99pp)。→ L3 硬约束 G 的证据边界;它进 finalist 是为打开候选池形状。
- **菜单滞回(carryover)**:token 会计坐实"保席从不省 token,只会 0 成本或 +1 Opus",且系统性推翻 L3 的拒绝——拒绝恰是本机器唯一被证明有效的功能。
- **L4 TTL 复用**:用户裁定「不要任何复用」(复用票不跑 intel 会新闻冻结)。
- **`stable_context_blocks` 共享块置前**:预估节省 4% < 10% 门,不值得双路维护。
- **`performance.sector_brief_mode` A/B**:`finalist_only` 会让 L3 看不到判断型行业 brief、可能改变 finalists,按「性能开关不拥有评级」铁律它不是性能开关。
- **涨停数据做打板/隔日溢价信号**:负结果,只进温度计。
- **L3.5 收窄层**:用户裁定完全移除;回测结论「只有 conviction≥70 有 T+2 edge」已内化为行为化定义。

## L1 默认停用的 4 路(及理由)

| 通道 | quota/floor | 停用理由 |
|---|---|---|
| accumulation | 120/30 | unique 超额 −0.21%,原并入 reversal_confirm |
| northbound | 120/30 | hk_ratio T+2 IC −0.108,信息已在 L4 简报行 |
| sector_momentum | 150/0 | EXP-2 challenger 数据腿,唯一消费者随 2026-08-21 闭环退役删除,现在无尺可裁 |
| event | 80/20 | 公告事件(回购/增持去重、调研只作有无;排序 `ev_hard`+composite);取证渠道随闭环删除;L2「事件」桶 floor=0 |

⚠️ `funnel.recall_channels` **缺省 = 用全部 14 路**(`config.py` 的 `recall_channels: list[str] | None = None`),删掉整行会把这 4 路一并上线。注册数真值:`uv run --no-sync python -c "from autoresearch.scan.recall.registry import registered_channels; print(len(registered_channels()))"`。

**L1 已知局限**:risk_off 样本薄(11 日);horizon 之争未决;momentum 早期「unique +0.75%」已过期且符号翻负(26 日 −1.07%,`docs/research/2026-08-04-momentum-phase-conditional-ic.md`);`pre_healthy` 语义在 2026-07-25 有定义断层,跨该日读 `L2_pre_healthy.csv` 不能直接连线。

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

- **v3.0(2026-08-26 §3 路A)**:A2 硬门扩集(`research_rating ∈ {Sell, Underweight}` 或卡面 `FINAL TRANSACTION PROPOSAL: SELL` 或早停停因 ∈ {基本面恶化, 估值透支, 涨停追高, 数据不足} → 否决;留在集合外的 {其他, 题材透支, 资金流出} 在隔夜尺上对 Hold/UW 无区分力);A1/A3 候选池 `relative_buy.pool` 可切 `composite`(L3·finalist 一族 40 日隔夜相对超额 −0.27pp t=−3.94 显著为负,composite 是全表唯一正证据 +0.14pp t=3.05);A4 执行线两行(四年全湖 1086 日收在当日区间上 30% 的票隔夜比全体差 0.13~0.27pp,逐年同号)。
- **v4.0(2026-09-24 §2.6)**:`relative_buy.tiering` 总闸;票级 `data_a`;入场门(`entry_stance=="PROHIBITED"` 进 `no_redflag`);A/R 分级;盲卡不入账。**A 级只记录、不设门**:158 张真实卡里 A 级恒 0——早停卡结构性不得写「允许」而 70% 是早停卡;设成门会逼着放松 Hold 四条件(09-12 四笔亏损 BUY 的病根)。买过禁止票的两天(09-09/09-10)早于 `card_context` schema,`card_says_prohibited` 在生产里从未被记录过。
- **v4.1(2026-09-25 §2.4)**:`relative_buy.rebalance_gate` 第五硬门 `rebalance_close`(调样生效前夜的调样票否决);`index_events` 块 `source` 四态 `ok|absent|disabled|error` 各自留痕;证据:17 次调样 E−1 −0.31pp、胜率 35%(中证500 −0.72pp·胜 22%)。

## 历史产物(只读,无人再生产)

盘上已有的账本文件原样保留:`$CTX/learning/*.jsonl`、`$CTX/scan/*/retro/*.csv`、`$RPT/learning/*.md`、`$CTX/knowledge/`(lessons/proposals/precedents.db)。代码侧零读侧、零写侧,纯归档。`$CTX/learning/` 目录名是历史遗留,里面仍有两样活的:`temperature.csv`(S1 温度计)与 `usage_reconcile.jsonl`(计量 streak);目录不改名。

## 开放线头(诚实局限)

1. regime 块 horizon 之争待 T+5 数据裁决;risk_off 块样本薄(11 日)。
2. healthy 通道反事实、capfloor20 —— 取证渠道随 2026-08-21 闭环退役整个消失;要重开得先重新造一把前向尺。reversal_confirm 与旧 reversal 的 A/B 于 2026-08-19 结束、2026-08-21 重开(起爆硬门③由 `ma_bull` 改为 `above_ma20 ∧ ma5_gt_ma10`),裁决腿已删,配额 150 保留但无尺可裁;`self_review.channel_liveness_lint` 逐日盯「启用通道 0 行」。
3. consensus 积累 <60 日不入线上;anns_d 无接口权限 → 公告情感列曾空(2026-07-30 起 cninfo 兜底,非零率 ~60%)。
4. 温度计菜单/预算联动待相位判定质量复审。
5. L2「行业」= 东财所处行业(129 个细标签),`l2.sector_cap` 20% 几乎从不触发(08-26 最大单行业 6.5%);真正拦扎堆的是 L3 守卫⑧的 3 席帽。要让 L2 的帽起作用得先收缩到申万一级粗度(`common/sw_sector_map.py`)。
6. 仅供研究,非投资建议。
