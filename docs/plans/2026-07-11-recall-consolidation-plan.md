# Plan A1:召回整编 + reversal_confirm 四段确认通道

spec: docs/specs/2026-07-11-recall-gate-pinned-config-design.md §2。
门(全 task):`uv run --no-sync python -m ruff check .` exit0 + `uv run --no-sync python -m pytest tests/ -q` 全绿。git add 只点名文件。
执行顺序注意:T4 的配置开关依赖 Plan A3-T1(scan_config loader)先行;T1/T2/T3 无跨 plan 依赖可先跑。

### Task 1: 通道整编报告 CLI(证据件,零 LLM)

**Files:** Create `autoresearch/research/channel_audit.py`;Test `tests/research/test_channel_audit.py`。

- 输入:历史各日 `context/scan/<date>/L1_channels.csv`(列含 code+lane/channel,grep 现文件确认列名)+ `retro/attribution.csv` 的 fwd_2_oc。
- 输出 `reports/channel_audit_<date>.md` 三节:①各路**累计** T+2 账本(mean/unique_excess_t2、hit_t2、n;n<10 日标 ⚠ 薄样本);②两两召回集 **Jaccard 重叠矩阵**(共同召回码 / 并集);③unique_excess_t2 排序 + 整编建议行(仅陈述数据,不自动动配置)。
- CLI:`python -m autoresearch.research.channel_audit [--days 30]`。
- Steps:失败测试(合成两日 staging fixture,两通道一重叠一独占,断言 Jaccard 与 unique_excess 数值)→ RED → 实现 → GREEN → commit `feat(research): 通道整编报告 CLI(累计T+2账本+召回重叠矩阵,整编证据件)`。

### Task 2: 反转确认三新因子 + IC 三门验证(跑动型,controller 可直做)

**Files:** Modify `autoresearch/research/factor_lab.py`(特征侧,grep `def _features\|FEATURES` 定位注入点);Test 既有 factor 测试文件补断言。

- 新因子(lake 的 vol/high/low/close 全可算):`vol_ratio_20`(当日量/20日均量;⚠ 旧 `vol_ratio` 曾被 IC 回测剔除,新名新口径勿复用旧名)、`dist_low_60`(现价距 60 日低点 %)、`days_no_new_low`(连续不创 60 日新低天数)。
- 跑批:`factor_lab evaluate` 全历史(107 成型日)出三因子 IC/ICIR/decile;**三门**=两半同号 ∧ |ICIR_fwd_2_oc| 前半 ∧ spread_t≥2;结论记 progress.md(过门与否都记,不过门则 T3 的 lens 仅用其做门谓词不进权重)。
- commit `feat(research): 反转确认三因子入 factor_lab(量比/距低点/不创新低)+ IC 三门读数`。

### Task 3: lens_reversal_confirm + 通道注册

**Files:** Modify `autoresearch/common/scoring.py`(镜像 lens_reversal :167 结构)、`autoresearch/scan/recall/channels.py`;Test `tests/` 既有 lens/channel 测试文件追加。

- `lens_reversal_confirm(df)`:四段谓词——①前置低位(pct_60d≤−25 或 dist_low_60≤15);②衰竭企稳(days_no_new_low≥10 且 5日均量<20日均量 且 rsi6 从超卖回升,列缺则该子条件跳过=presence-gated);③**确认起爆硬门**(vol_ratio_20≥1.5 且 [站上MA20 或 破20日高];无量=不入);④可交易(复用现涨跌停检查)。评分=低位30+企稳30+确认40。**禁用 CMF-20 作确认信号**(汇川/柳工 day1-2 滞后实证,docstring 记 why)。
- `@channel("reversal_confirm", quota=200, floor=50, desc="反转确认(四段,起爆日硬门)")`——**注册即上线**(recall_channels=None 全注册语义;这是本波显式交付非 parity 违例,plan 明示);旧 `reversal` 不动,双路并跑即影子对照。
- Steps:失败测试(合成 frame:一只完美四段票必入、一只无量突破票必拒、一只仍创新低票必拒)→ RED → 实现 → GREEN → commit `feat(scan): reversal_confirm 四段确认通道(起爆日硬门,与旧 reversal 并跑对照)`。

### Task 4: 并跑对照验收(跑动+裁决,≥10 日后)

无代码。channel_eval 按 lane 自动分行计量 reversal vs reversal_confirm(unique_excess_t2/hit_rate_t2)。≥10 日读数后出裁决:新路胜 → 经 scan_config `funnel.recall_channels` 退役旧路(依赖 A3-T1);未胜 → 继续积累或调参。结论记 progress.md + 观察窗起点记 run meta。

### Task 5: 默认整编案提案(人批门,不自动执行)

**Files:** 无代码(用 `feedback_store.add_proposal`,kind="channel_merge")。

- 读 T1 报告数据,逐条起草独立提案:northbound 退役→L4 advisory 行;momentum+heat 合并 trend(quota 200);accumulation 并入 reversal_confirm;growth 150→100。每条带报告读数为证。
- 人批后仅改 scan_config `funnel` 键(不删代码,可回滚)。批前一切不动。
