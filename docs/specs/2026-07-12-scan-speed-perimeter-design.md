# scan-market 周边提速包(perimeter speed)设计稿

- 日期:2026-07-12
- 状态:已拍板(用户批准六刀口;速度线只动「周边」,L3 判断结构明确不动)
- 基线读数:run `reports/scan/20260712_1857`(数据日 2026-07-10,详见下)
- 关联:`docs/specs/2026-07-12-funnel-replay-l35-removal-design.md`(workflow 现状)、
  `docs/specs/2026-07-12-data-contracts-design.md`(P1 完整性守卫复用其分级思想)、
  `docs/specs/2026-07-12-l4-intel-station-brainstorm.md`(P4 的 intel 并行窗口)

## 0. 背景:20260712_1857 的墙钟画像

`_stage_timing.json`(mtime 推导下界)+ staging mtime 复原的关键路径:

| 段 | 墙钟 | 病灶 |
|---|---:|---|
| 会话前段(17:42→17:58) | ~15m | calibrate 17:43 落权重 → frame 17:58;**无仪器黑洞**(冷湖日还要 +~10m universe 取数) |
| L0-L2 ∥ 策略师 | 3m05s | 健康 |
| sector-pack → [briefs×6 ∥ l3-prepare] | ~6m | 两腿并行各 ~6m;l3-prepare 内 **L3_news 204 只逐股串行**(evidence 已是 bulk) |
| L3-rank 单 agent(max) | 25m35s | 全程 44%。**本包不动**(用户裁定;它是下一波标的) |
| L4 prep(6 条 CLI 串行) | ~3m | pledge/seats/calendar/consensus 互相独立却串跑 |
| slim 预取 ×11 | 6m08s | `harvest_slim_batch` for 循环逐只 subprocess,~33s/只 |
| L4 卡 ×11 并发(xhigh) | 10m08s | 健康(最慢卡决定) |
| 买单 ensemble(run2/3) | ~5m | 仅有买日;语义成本,不动 |
| assemble + GATE4 | ~1m | 健康 |
| 编排壳(bash()/gate() spawn ×~12) | ~5m 散布 | 每个 agent 壳 ~20-40s 起步 |

另:下次跑 `l4_intel.enabled=true` 首跑,11×sonnet·max 盲搜与 slim 同 barrier,**不处理会把 L4 前段顶到 10m+**。

## 1. 目标 / 非目标

**目标**:判断质量零改动(L3/L4 的模型、effort、prompt、评级语义、漏斗数量全不碰),纯编排/I/O 层提速。
0买日 59m → **~42m**;有买日+intel 开 ~70m → **~55m**;冷数据日感知另省 10-15m(预热)。

**非目标**(明确不做,防 scope creep):
- L3 26m 的结构/effort(降载实验、拒绝者重构)——下一波单独设计;
- sector_brief effort 降档(config 旋钮已存在,用户单独拍);
- 质量/仪器线其余项(产物形状断言自动化、anns 断链根因、process_scores 口径)——另立;
- 买单 ensemble 的 +5m(语义成本,保留)。

## 2. 六个刀口

### P1 · Cron 预热(感知 −10~15m,冷数据日)

- **现状**:开扫前主会话手动跑 calibrate/prelude(会话前段黑洞);冷湖日 universe 全市场取数 ~10m 落在 workflow GATE1 前。
- **改法**:
  - 新 `autoresearch.scan.prewarm` CLI(或 `prelude --prewarm`):自动解析**最近已结算交易日**(交易日历,非 today)→ lake harvest(universe 同款取数入湖)→ `factor_lab` calibrate(走 `retro.recalibrate_and_log` 快照通道)→ temperature rollup。全程幂等:湖已有该日数据 = 命中空转,秒退。
  - `scripts/prewarm.sh` 包一层 + **launchd plist**(交易日一~五 19:30);手动 fallback = 同一命令。
- **关键护栏(湖毒化前科,见 memory lake-narrow-fields-poisoning)**:**完整性守卫后才入湖**——日线行数 ≥ 阈值(如 ≥4000)且 moneyflow 非空率达标,任一不达标 → **整批丢弃不写、退出码非零留痕**,当晚真扫描自己重拉。守卫实现挂 `autoresearch/data/contracts.py` 既有分级(B 级:降级必记账;此处升格为"不达标不入湖")。
- **19:30 依据**:tushare 日线 ~16:00 结算、moneyflow ~16-18:00;龙虎榜(19:00+)不在预热范围(L3 evidence 扫描时才拉)。
- **风险**:launchd 环境变量(TUSHARE_TOKEN)——plist 里显式注入或 source 用户 profile;预热失败不阻断任何东西(晚间扫描回落现路径)。

### P2 · L3_news 迟取 + 并行(−2~3m)

- **现状**:`l3_select prepare` 串行 = evidence(`harvest_l3_evidence`,bulk by date,已快)→ news(`l3_news.py` **204 只逐股串行** `get_or_fetch("stock_news_em")`)→ pass1(`triage_l2_for_l3`)→ 表。
- **改法**:顺序改为 evidence → **pass1 先行** → news **只拉 kept ~60**、ThreadPool 6-8 并发 → 表渲染。
- **已核实的前提**:pass1 分诊只用 gbdt_score/composite/pinned/recall_channels/n_channels/healthy 列,**不吃 sentiment/news**(l3_select.py:43-61)。
- **形状变化两处(点名,验收时对照)**:
  1. `_l3_pass1_cut.csv` 不再带 sentiment/news 派生列(attribution 用前向收益证明"分诊没吃赢家",不受影响);
  2. `L3_news/` 覆盖从 ~204 收到 ~60。下游读者已穷举:`health.py`(anns_empty_rate → 在 60 只上算率,语义不变,**分母字段注明**)、`l4_reuse.py`(只读 finalists,必然 ⊂ kept 60,安全)、producer 自身。
- **并发护栏**:akshare/东财 keyless 端点,6-8 workers 温和;单票失败降级为空稿(现有语义),不阻断。

### P3 · slim 预取并行(−4.5m)

- **现状**:`l4_card.harvest_slim_batch`(l4_card.py:1125)for 循环逐只 subprocess,~33s/只 ×11 = 6m08s。
- **改法**:ThreadPoolExecutor(**默认 4 workers**,`--workers` 旋钮),收集全部结果后统一判 → GATE3「失败响亮」语义不变(min_bytes=8192、.SH 检查照旧)。
- **限频护栏**:单票遇 429/连接类异常 → 该票降级串行重试一次;两次失败按现语义响亮报。
- 诚实注:intel 开启后 L4 前段 = max(intel, prep+slim),slim 的收益部分被 intel 遮蔽;intel 关/0 finalist 日全额兑现。

### P4 · intel 提前 + L4 prep 并行(−3~5m,兼 intel 首跑防线)

- ① **prep 并行**:`scan-market.js` l4-prep 一条 bash 内,`l4_reuse` 最前、`prompts` 最后**不变**(07-07 排序坑不变量:生产者先于 prompts),中间四条独立生产者(pledge/seats/calendar/consensus)改 `( a & b & c & d & wait )`,各自 `|| true` 语义保留。
- ② **intel 提前到 GATE2 后**:`gates.py` gate2 JSON 增 `meta: {code: {name, sector}}`(gate2 本就读 finalists.csv,零新取数);workflow 里 intel thunks 从「dispatch-plan 之后」移到「GATE2 通过后立刻」,与 l4-prep + GATE3 slim 全窗并行,barrier 收在派卡前。intel 盲于 L3 论点(设计不变量),只需 code/name/sector/date,提前零语义损失。
- **代价(接受并 log)**:被 l4_reuse carryover 跳派发的票 intel 白跑一只 sonnet(近期 reuse=0)。
- ③ **intel 设界**:scan_config 增 `l4_intel.max_queries`(**默认 15 = 现状**,prompt 模板读它);首跑实测 intel 墙钟 >8m 再拧,不预先降质。

### P5 · 编排壳收敛(−1~1.5m)

- `sector-list` agent 并进 sector-pack:一个 gate 调用跑 `reuse --apply; pack; python -c "<打印待写行业 JSON>"`,schema `{sectors}`(2026-07-09 的「schema 顶层必须 object」教训照搬);
- `finalists 写盘 + GATE2` 合成一个 gate:`l3_select finalists … && gates gate2 …`,返回 gate2 JSON。
- 每省一个 spawn ≈ 20-40s;l2-check/GATE1 等条件分支壳保留(便宜且承担重试逻辑)。

### P6 · 仪器补全(验收前提)

- `stage_timing.py` 增三行:**prelude**(预热/会话前段,锚 = weights/lake mtime → `_t0`)、**ensemble**(卡 barrier → `_ensemble.json` mtime)、**assemble**(ensemble → summary mtime);
- 效能表(assemble 渲染)effort/model 列改读 `user_config_echo.json`,修「表写 medium、实际 xhigh」失真(本次实锤:echo=strategist high/sector_brief sonnet·high/l4_card xhigh,表写 session/Opus·low/medium);
- intel 行接真实墙钟(enabled 时);预热产物在表内单列一行(跑过=显示、没跑=—)。

## 3. 墙钟账(估)

| 场景 | 现状 | 本包后 |
|---|---:|---:|
| 0买日、intel 关 | 59m | **~42m** |
| 有买日、intel 开(下次跑) | ~70m(外推) | **~55m** |
| 冷数据日感知(+会话前段) | +15m | ~0(预热已跑) |

构成(intel 开、有买日):prelude 3m + briefs floor 6m + **L3 26m(不动)** + max(intel ~6-8m, prep 1m+slim 1.5m) + 卡 10m + ensemble 5m + assemble 1m + 壳 ~2m。

## 4. 验收

1. 全测试绿;新增并发路径有单测(P2 迟取的列形状、P3 workers=1 与现基线产物等价、P4 gate2 meta 契约);conftest 隔离三件套照旧。
2. **真实命令冒烟**(FN-1 族教训:接线 ≠ 生效):`prewarm` 真跑一次(含守卫拒绝路径的假数据测试);下次真扫描 `_stage_timing.json` 对照第 3 节账目。
3. parity:所有新旋钮缺省即现行为;产物变化只有 P2 点名的两处(cut.csv 列、L3_news 覆盖面)。
4. launchd plist 装载后 `launchctl list` 可见 + 次日 19:30 产物 mtime 佐证。

## 5. 风险清单

- 预热在非交易日/数据未结算时触发 → 交易日历判定 + 完整性守卫双保险,失败零影响(回落现路径);
- akshare/yfinance 并发限频 → workers 保守(4/6-8)+ 单票串行重试;
- intel 提前后若 GATE3 失败(slim 断链),intel 已花的 sonnet 沉没 → 接受(GATE3 失败本身罕见且要人工介入);
- workflow JS 改动无单测覆盖 → 靠 `test_recall_wiring.py` 式契约测试锁 gate2 JSON 字段 + 下次真跑验收。

## 6. 停车场(本包不做,已记录)

L3 26m 降载/角色重估(速度×alpha 交点,最大标的)、sector_brief effort 旋钮、产物形状断言自动化(pinned thesis 非空/force_full_card 生效数/intel 稿数)、anns_empty_rate=1.0 根因、run_health 期望清单与 process_scores 口径(verify.csv→ensemble、8KB vs 10KB、blind_pass N/A)、finalist 换手 9% 与卡复用 0 的观察。
