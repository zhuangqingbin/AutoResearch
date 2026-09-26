# 指数调样事件进推荐流程设计（2026-09-25）：日历事实 + 生效前夜守卫

> 用户 2026-09-25 提出「一只股票即将被纳入某个指数（指数资金的利好）要在整个推荐过程中加入考虑，加在哪里你来优化」。
> brainstorm 讨论稿 `docs/specs/2026-09-25-index-inclusion-signal-brainstorm.md` 做了前提检查（隔夜尺普查）与三路线比较；用户当晚裁定 **E1–E5 全按推荐**：
> E1 需求改写为「日历事实 + 生效前夜守卫」而非加分；E2 守卫对全部调样票（调入与调出、六指数）一刀；E3 预测器 spike 立即做（已做，负结果，见 §2.8）；E4 所有生产改动排在可买性对齐波批 4 十日真跑之后；E5 只覆盖 A 股六指数。
> 本稿是这些裁定的设计。**零实施。**

## 0. 一句话

「即将纳入指数」在本系统的隔夜主尺上不是利好：公告后追买零边，生效前跑道为负，生效日前夜买入（= 和被动资金在同一个收盘价买）显著为负；所以它以**事实日期**进日历（L4 简报 / summary / 档案 / 行业包四处免费继承），并在 E6 加一道**独立计数的硬门**：扫描日等于调样生效前夜的调样票不得成为 BUY。零 LLM，一年真正起作用约四个交易夜，其余日子逐字 parity。

## 1. 现场事实（实施时按这些 `file:line` 下手；写稿时逐条核过）

**F1 主尺读数（附录 A）**：17 次调样、1652 调入票次、对照 = 同指数未变动成分股。A−1 夜 +0.21pp（t_day 3.2，需预测名单）；公告夜 −0.07；跑道 −0.10（t_day −5.4）；**E−1 夜 −0.31pp、胜率 35%**（中证500 −0.72、胜 22%、t_day −5.6；中证1000 −0.25）；沪深300/A500/科创50 的 E−1 ≈0。生效日成交额 = 前 20 日均的 1.86×（未变动股 1.1×）——被动流是真的，隔夜是负的。

**F2 预测器 spike（附录 B）**：复刻编制规则，沪深300 精度 37–70%，预测集 A−1 夜 +0.21pp 不显著，误报票 −0.44pp、胜率 19%；不达预注册判据。**路线 C 不立项**。

**F3 时机**：可买性对齐波批 0–3 已于 09-25 合入 main，批 4 = 十日真跑，`docs/superpowers/plans/2026-09-24-buyability-realignment.md:3043`「期间不改规则」。本稿生产改动全部排在批 4 之后。**下一次半年调样：公告 2026-11-27（周五盘后）、生效前夜 2026-12-10（周四扫描）、生效 2026-12-11 收盘。**这是第一次真跑验收机会；错过则等 2027-06。

**F4 日历模块**：`autoresearch/scan/calendar.py:2-9` 铁律「日历是事实日期非方向」；`_CAL_COLS = ["code","kind","event_date","detail","ratio"]`（:23），`kind ∈ {unlock, disclosure}`（:66, :83）；`harvest_calendar`（:36）在 prelude `calendar` 步跑，codes = L2-200 ∪ finalists（`prelude.py:409-425`）；`calendar_flags`（:102）被 **L4 简报 `l4/context.py:283-286`、summary `report_sections.py:1178`、sector pack `sector/pack.py:337-345`、档案 §6 `dossier/builder.py:182-187`** 四处消费。加一种 `kind` = 四处同时继承。

**F5 E6 硬门**：`relative_buy.py:233` `_HARD_GATES = ("tradable","data_a","contract","no_redflag")`；`_hard_gate`（:628）一门一行 `fail(gate, detail)`；`_field_usage(tiering)`（:283）从 `_HARD_GATES` **派生**，改常量即自动进决策文件；`build_decision` 是纯函数，盘上输入（如 `card_snapshot`）由 `write_decision/verify_decision` 在 I/O 边界读入（:1101 起）。**E6 当前没有任何催化/日历输入。**

**F6 数据层**：`autoresearch/data/endpoints.py:33` 端点必须登记（未登记 `policy()` 抛 KeyError）；`index_weight / index_basic / fund_basic / fund_share / fund_nav` **均未登记、本 token 均有权限**（09-25 实测）。契约 `data/contracts.py:109` 一行 `_c(tier, cols, min_rows, note, empty_ok, persist_violations)`；未登记契约 = 不校验（:302）。缓存键三型（`cache.py:164-183`）：`date` 取 `("trade_date","date","ann_date","cal_date")`，`as_of` 实体取 `("ts_code","symbol","code","exchange_id","exchange")`——**`index_code` / `nav_date` / 公告 `id` 都不在键参数表里**，登记前要先扩键参数表或换参数名（§2.1）。

**F7 中证公告源**（09-25 实测）：`GET csindex-home/announcement/queryAnnouncementByType?type=1` 返回三数组，`indexrebalancingAnnouncements` 只给最新 5 条、不分页；`GET announcement/queryAnnouncementById?id=` 返回 `content`（HTML）+ `enclosureList[].fileUrl`（`oss-ch.csindex.com.cn/notice/*.xlsx`）；附件表结构 = 每指数一行：`指数代码 | 指数简称 | 调出(代码,简称)×2 | 调入(代码,简称)×2`（2026-09-09 临时调整 166 行解析成功）。历史不可回溯 → 从上线日起自建湖累积；半年调样日期另有规则兜底。

**F8 既有事件通道不可借**：L1 `event` 通道默认停用、裁决仪器 `channel_audit` 已随 learning 层删除（`STAGES.md:64`），L2「事件」桶 `floor=0` 是承重的（`l2_stratify.py:34-41`）。`STAGES.md:363` 已把「业绩预告做 L1 事件通道」列为实证否决方向（公告后追买无肉）。

**F9 防锚定**：`market.py:259`（healthy top3 不喂 L3/L4）+ 行业席位稿自认「用意被部分跨过」并立三条对冲（`docs/specs/2026-09-24-buyability-realignment-design.md:79-94`）。本稿不给 L3 加任何列，不给 L4 加方向词；唯一带方向的句子只在 E−1 夜出现，且是「禁止」不是「买」。

**F10 产物与旋钮纪律**：新产物先进 `autoresearch/contracts/artifacts.py` 的 `ARTIFACTS`（`tests/contracts/test_registry_parity.py:284` 守卫）；新旋钮三件套 = `user_config.py` 白名单 + `_KNOB_TYPES` + 真实 `knob()` 消费点 + `tests/scan/test_config_knobs.py`（`SKILL.md:78`）；agent 行为改动两引擎同修（`.claude/agents/l4-card.md` ↔ `.codex/agents/l4_card.toml`）。

**F11 缺席 ≠ 否**（09-25 五连撞）：每个新字段都要能分开「源不可达」「源可达无事件」「有事件但不在守卫相位」「守卫命中」四个世界。

## 2. 设计

### 2.1 数据源与登记（批 B0，全部惰性）

| 端点 / 源 | 用途 | key | 契约 | 备注 |
|---|---|---|---|---|
| `csindex_rebalance_list` | 最新调样公告列表（title/publishDate/id） | `as_of`（每取数日一份快照，`snapshot: True`） | B，`persist_violations=False` | 新 source adapter `autoresearch/data/sources/csindex.py`（`sources/__init__.py:58-70` 的 `_fetch_eastmoney` 同型分支） |
| `csindex_rebalance_detail` | 公告正文 + 附件 xlsx 解析后的长表 | `as_of`，实体 = 公告 `ann_id`（`cache.py:39` 实体键参数表加 `ann_id` 一项）；内容不可变，取数前先找湖里任一 `<ann_id>@*.parquet`（`_load_detail`，`autoresearch/scan/index_events.py`），命中即直接读湖，只对湖里没有的 id 真正取一次网 | B，`persist_violations=False` | 必须带「两个 id 不撞键」测试 |
| `index_weight` | 月末成分快照：普查/回填/对账（公告 vs 实现） | `as_of`，实体 = `index_code`（实体键参数表加 `index_code`），as_of = `end_date` → 键形如 `000300SH@20260630` | B | 一指数一月一份，永不重取 |
| `index_basic` | 六指数元数据 | `static` | B | 可选 |
| `fund_basic` / `fund_share` / `fund_nav` | ETF 规模 → `flow_adv_days` 描述字段 | `static` / `date` / `date`（`cache.py:37` 日期键参数表加 `nav_date`） | B，`empty_ok=True` | **批 B3，presence-gated**；缺则描述行不带数字 |

规则：写湖剥 `fields`（`cache.py:238-254`）；走 `cache.get_or_fetch`，不学 `calendar.py:53,71` 的裸 `pro.*`；B 级降级一律 `record_degradation` 进 `degraded.json`（`contracts.py:244-264`）。

**六指数白名单**（E5）：`000300.SH 沪深300 · 000905.SH 中证500 · 000852.SH 中证1000 · 000510.SH 中证A500 · 000688.SH 科创50 · 399006.SZ 创业板指`。附件里其它指数（上证380、三板…）的行**入湖但不进事件表**——湖是全量事实，事件表是产品选择。

**已知覆盖缺口（实施后发现，原设计未预见）**：`399006` 创业板指是深证/国证口径指数，其调样公告**不在**本模块读的中证指数公司（csindex.com.cn）公告源里发布——公告驱动的这条路径（本节与 §2.2/§2.3）永远不会为它产出一行事件，不是「暂时没取到」，是这个源结构性不覆盖它。白名单仍保留这一项：不会因此误判（只是从不命中，不污染其它五个指数），但读者不该假设六个指数在这条路径上是对称的。真正能覆盖创业板指调样对账的是 `index_weight` 月末成分快照普查（附录 A / `research/index_rebalance_census.py`），该普查按快照差分，不依赖公告源，不受此限制。

### 2.2 事件表 `index_events.csv`（先进 ARTIFACTS）

`Artifact("index_events", "index_events.csv", "staging", "prelude", "calendar", "csv", "gated", required_when="calendar.index_rebalance 开且中证公告源可达(design 2026-09-25 §2.2)")`

| 列 | 含义 | 缺席语义 |
|---|---|---|
| `code` | 6 位 | |
| `index_code` / `index_name` | 六指数之一 | |
| `side` | `add` / `drop` | 同票同 E 在两指数分别出现两行，**不合并**（净额只在 `flow_adv_days` 里算） |
| `ann_date` | 公告日 | |
| `eff_close_date` | **被动调仓的那个收盘日**（半年调样 = 第二个周五；公告文本写「X 日起生效」则取 X 的前一交易日，写「X 日收市后」则取 X） | 解析不出（如「自退市日起」）→ 空，`phase=unknown_eff` |
| `phase` | 按扫描日 D 算：`announced_runup`(A≤D≤E−2) / **`passive_close_eve`**(D=E−1) / `effective`(D=E) / `post`(E<D≤E+3) / `unknown_eff` | |
| `source` | `csindex`(公告原文可解析) / `rule`(仅周期规则推日期,无名单) / `none`(两者都不成立——解析不出且不套规则,如临时调整公告;此时行仍保留,`phase=unknown_eff`) | |
| `flow_adv_days` | Σ(该指数 ETF 规模 × 新权重)/ADV20，跨指数净额；批 B3 之前恒空 | 空 = 未计算，不是 0 |

生产者：prelude `calendar` 步内新增一腿（不加 `STEP_NAMES`），codes = L2-200 ∪ finalists ∪ **当日 E6 候选**（finalist ⊂ L2-200，已覆盖）。**源不可达 → 文件缺席 + `degraded.json` 一行**；源可达无事件 → 只有表头的空文件。两者在 `run_health.index_events.source ∈ {ok, absent, disabled, error}` 分开记（四态，见 §2.7；`disabled` = 旋钮关且无文件，与「旋钮开但源不可达」的 `absent`、「文件在场但读不出来」的 `error` 是三个不同的因）。`index_events.csv` 本身收**六指数全部**公告行（不过 codes 过滤）；`calendar.csv` 第三腿才按上面这份 codes（L2∪finalists∪当日候选）过滤——同一份中证公告数据在两个产物里的人口不同，前者是全量事实、后者是产品选择。

**生效日解析细则（实施后补，原设计未预见的三条边界，均已有 fixture/测试锁定）**：

- **`parse_effective_date` 的歧义裁决故意保守**（`autoresearch/data/sources/csindex.py`）：正文常同时出现两类日期候选——引用规则本身的生效日（常见于括注引文）与本次调整真正的操作生效日，两者用词相同、位置先后不是可靠信号。裁决顺序：恰有一个 `after_close`（收市后）候选就是它，不论 `from_date`（起生效）候选有多少个；否则候选总数恰为 1 就是它；否则（0 个或 ≥2 个仍不满足前两条）→ `(None, None)`，诚实退化，不猜。这条纪律不是理论推演：真实撞过的两种坏启发式——「取第一个匹配」会选中《指数编制细则》引文里规则本身的生效日而不是本次调整的日期（fixture：「…（2026年3月1日起生效），中证指数有限公司决定自2026年6月15日起正式实施本次样本调整」，first-match 会选到 3 月 1 日）；「取最后一个匹配」会选中*下一次*调样的日期而不是本次的（fixture：「本次调整于2026年6月12日收市后生效，下次调整预计于2026年12月11日起实施」，last-match 会选到 12 月 11 日）。歧义时退化为按周期规则推算的日期或 `unknown_eff`，绝不是猜一个。
- **节假日生效日不做 snap**：解析出/规则推出的生效日 E 若落在交易日窗口内却不是真实交易日（疑似节假日/未开市），`phase_for`（`autoresearch/scan/index_events.py`）只标 `unknown_eff`，绝不悄悄挪到最近的交易日——指数公司遇到这种情况往哪边挪没有任何文档说明，猜错方向恰好是这道门要防的反转本身（真正的 E−1 会被错认成 `announced_runup`，或反之）。代价是：即使相位已降级为 `unknown_eff`，`eff_close_date` 这一列**仍保留**解析/规则算出的日期字符串，不清空——所以「这一行有没有日期」不是「相位有没有解析出来」的可靠代理。两个下游消费者（`calendar.harvest_calendar` 写日历第三腿、`calendar_section` 算 summary 市场级计数）因此都必须直接判 `phase != "unknown_eff"`，不能只判日期非空；前者最初就是按日期非空写的，已在本波 fix-round-1 撞出一个真实缺陷并改正。
- **「临时」调整公告不落规则兜底**：`eff_close_from` 的 `is_temporary`（取自公告**标题**，见 §2.2 生产者段的取数入口）一旦为真，即使公告月份恰好落在半年/季度调样的规律月份（2/5/8/11 月），也不套用 `rule_eff_close_date` 那张周期表，直接退化为 `(None, "none")` → `unknown_eff`。理由：周期规则表只描述常规调样的月份规律；一条文字解析不出日期的临时调整公告如果仍套用该表，等于凭空编出一个「下一次常规调样」的日期，会在一个什么都不会发生的夜晚点亮 E6 硬门——这比诚实地说「不知道」危险得多。

### 2.3 日历第三腿（批 B1；旋钮 `calendar.index_rebalance`（平铺布尔，镜像 `l2.knife_cap`），代码内建默认 `False` = parity；**生产配置已开 `True`**）

`harvest_calendar` 读 `index_events.csv` 落 `kind="index_rebalance"`，`event_date=eff_close_date`，`detail=f"{index_name} {调入|调出}|{phase}"`（实施后补：`|{phase}` 后缀不是展示字面量的一部分——`calendar_flags` 读回时用 `str(detail).partition("|")` 把它切掉,只用来在渲染时分「生效前夜」与「其它相位」两种文案,`calendar.csv` 本身对读者呈现的仍是切掉后缀的那一半），`ratio=flow_adv_days`（可空）。

`calendar_flags` 文案按相位分两种，**只有 E−1 带方向词，且方向是「禁止」**：

- 其它相位（事实）：`- 📅 **指数调样**:{E} 收盘生效({index_name} 调入;ETF 被动买入≈{x} 天 ADV)——事实日期非方向`
- `passive_close_eve`：`- ⛔ **指数调样生效前夜**:{index_name} 调入/调出于 {E} 收盘生效;今晚买入 = 与被动资金同价买入,隔夜尺历史为负(docs/specs/2026-09-25…§1)→ 入场行写 禁止`

`calendar_section`（summary 📅 未来 14 天）加一行市场级：`{E} 指数调样生效:沪深300 ×19 / 中证500 ×50 …(finalist 涉及 N 只)`。档案 §6、sector pack 无需改动（继承 `calendar_flags` / by_kind）。

### 2.4 E6 硬门 `rebalance_close`（批 B2；旋钮 `relative_buy.rebalance_gate`，代码内建默认 `False` = parity；**生产配置已开 `True`**）

- 第五门**只在旋钮开时存在**：`_hard_gates(rebalance_gate)` 返回四门或五门（镜像 `_field_usage(tiering)` 的做法，`relative_buy.py:283`），`_field_usage` 同时接两个开关（`hard_gate.role` 文案的「四类/五类」措辞同样跟着这个开关走，不是恒定字面量——task-12 附带修复 C）；旋钮关 → 决策文件与 v4.0 **逐字节相同**（O4 的判据），开 → `hard_gate.fields` 五项 + 新增 `index_events` 块。配置读取用独立的 `configured_rebalance_gate()`，不掺进既有的 `configured_relative_buy()` 五元组（`autoresearch/scan/post_run.py`：`configured_relative_buy()` 仍只返回 `(mode, exclude_pinned, activate_date, pool, tiering)` 五项）——`publish_run_observation` 每次调用只读一次 `configured_rebalance_gate()`，把同一个值原样传给 `safe_write_decision`/`safe_verify_decision` 的 `rebalance_gate=` 形参（两者按 `decision_write` 是 `"write"` 还是别的值二选一执行，不是同时都跑），镜像 `tiering=` 既有的传法，不是让两个写者各自去读一次配置。`RULE_VERSION` 无条件升 `"e6.v4.1"`（逐字镜像 v4.0 对 `tiering` 的先例——常量字符串本身不受旋钮影响）；旋钮关 = 除 `rule_version` 这一个字符串外逐字节相同（O4 的判据同此，见 §3.1）。
- `_hard_gate` 第 ⑤ 段：
  - 旋钮开但源不可达（`index_events.csv` 缺席）→ `gates["rebalance_close"]=True` 放行，决策文件 `index_events.source=absent, gate_evaluated=false`；
  - 该票有 `phase == "passive_close_eve"` 的行（add 或 drop，任一指数；E2 裁定）→ `fail("rebalance_close", f"指数调样生效前夜:{index_name} {调入|调出} E={eff_close_date}")`（明细译成中文调入/调出，不是原始 `side` 字面量 `add`/`drop`——同 `calendar.py` 日历行、`self_review` 卡片契约 lint 三处统一译法，task-12 附带修复 B）；
  - 否则 `True`，`gate_evaluated=true`。
- `write_decision/verify_decision` 在 I/O 边界读 `index_events.csv` 传入 `build_decision(index_events=...)`，保持纯函数与字节级 parity 纪律。
- 决策文件新增块（仅旋钮开时）`index_events: {source: ok|absent|disabled|error, n_rows, n_candidates_in_events, n_unresolved_eff, hits: [...], gate_evaluated: bool}`；`blocked_reasons` 出现 `hard_gate.rebalance_close` 分桶。**不并入 `no_redflag`**（F11）。旋钮关时该块缺席 = 「仪器未上线」，读历史决策文件先看有没有这个块再下结论（09-25 第 5 条教训）。
- **`source` 是四态，不是两态（实施后补，fix round 1 引入 `error`，final whole-branch review minor-1 引入 `disabled`）**：`absent` = 旋钮开但 `calendar.index_rebalance` 那条腿也开着，源当天真的不可达；`disabled` = `calendar.index_rebalance` 这条腿本身关着——单杆回滚（只关日历腿、留着 E6 门）会留下的常态，不是任何意义上的降级；`error` = 文件在场但读不出来（如 `write_index_events` 是非原子写——直接 `to_csv`，不像 `write_decision` 自己用的临时文件 + `replace`——中断的一次会留下一个读不出来的半成品，如零字节文件）。三者门后果完全相同（全员放行），但因不同，不得合并成一个值：如果不设 `error`，读取异常会不设防地穿透 `_index_events_input`，进而穿透整个 `write_decision`/`verify_decision`，导致当天**完全不产出决策文件**（比任何一道门单独否决都坏，`_relative_buy_decision.json` 是 `buyability`/`relative_ledger` 都要读的产物）；而如果把 `absent`/`disabled`/`error` 中的任意两个合并成同一个值，就是把不同的因编码进同一个值——本项目的「缺席 ≠ 否」纪律明确禁止这么做（`absent`/`disabled` 的区分同理适用于 `run_health.index_events` 与 brief ③，见 §2.7 与 minor-1）。`_index_events_input`（`relative_buy.py`）在 I/O 边界 try/except 读盘异常时置位 `error`，用 `knob("calendar", "index_rebalance", ...)` 区分 `absent`/`disabled`；`build_decision` 本身不读盘，也不判断「为什么」是 `None`，只原样记账。`n_unresolved_eff`（I3）：候选中带 `phase="unknown_eff"` 行的只数——门沉默的五种因里唯一不可读的一种，门放行且不进 `hits`，此前与「没有调样事件」在决策文件与 brief 上都长得一模一样。
- 与「成功日 ≥1 BUY」裁定的关系：E−1 夜若全部合格票都是调样票 → 当日 BLOCKED 是**正确输出**（同 08-19 R-E2 语义：无合格票则 BLOCKED）。一年最多 4 天。

### 2.5 L4 卡规则（批 B2，两引擎同修）

`.claude/agents/l4-card.md` + `.codex/agents/l4_card.toml` + `.claude/skills/stock-research/lite-playbook.md` 各加一条：「简报日历行出现 ⛔ 指数调样生效前夜 → 入场行写 `禁止`（理由归入现有 `其他`，不改七词早停契约、不改评级）；其它调样相位只作事实，不因纳入抬评级、不因调出压评级。」`tests/test_agent_defs.py` 三处锚。`self_review` 加一条 warn（非 block）：finalist 当日在 `passive_close_eve` 而卡入场行为 `允许`。E6 门是确定性兜底，不依赖 agent 听话。

### 2.6 stock-research full（批 B3）

`autoresearch/analyze/blocks_ashare.py:313,317` 的「指数调样 → WebSearch 补」换成确定性行：读湖最新 `index_weight` 给出「当前属于:沪深300/中证A500…」，读 `csindex_rebalance_detail` 湖给出近 60 日涉及本票的调样事件（有 → 事实行；无 → 「近 60 日无调样事件（源可达）」；源不可达 → 「调样事件:源不可达」）。政策窗口仍留 WebSearch。美股不动（`us-intel.md:46` 已有面）。

**确定性行是主源，WebSearch 是它的兜底，不是它的替代（实施后补，final whole-branch review，I4 option c）**：`index_weight`（成分快照）当前在 production 里**没有任何自动生产者**——B3 批（ETF 规模描述字段，`autoresearch/scan/index_flow.py`）之前，唯一会写它的是手工普查 CLI（`autoresearch/research/index_rebalance_census.py`），日常 scan-market 跑动不会碰它。这与「指数调样 → WebSearch 补」被换成确定性行、且该行无条件调用（不看 akshare 装没装）的设计意图相冲突：合并后这条成分行会**永久**读成「湖内无（未取数）」，而本节与 `.claude/skills/stock-research/lite-playbook.md`/`engine-playbook.md` 都在断言「调样不再网查」——一份文档声称有能力，产线上却没有任何东西在产这份数据。

被否的两个选项：(a) 把 `flow_adv_days` 那条旋钮打开——审查证明那个旋钮只在**已经有事件**的日子才触发，买到的是一个 ADV 数字，不解决"成分快照没人产"这件事；(b) 加一条月度刷新——那是变更冻结期内的新生产节拍，不该在一次修复波里加。

采纳：`index_membership_lines`（`autoresearch/analyze/index_membership.py`）额外返回 `lake_has_nothing: bool`——只问成分腿（`index_weight` 快照是否曾被取数），不看事件腿（后者由 scan-market 每日日历步顺带产，会随时间自愈，「近 60 日无调样事件（源可达）」本身已是一句有信息量的确定性答案，不该触发网查）。`blocks_ashare.ashare_corporate_calendar` 据此在成分行之后补一句 WebSearch 兜底（`'{code} 是否属于沪深300/中证500/中证1000/中证A500/科创50/创业板指 最新成分'`，标注『实时网查』、不计入确定性 context），**仅在** `lake_has_nothing=True` 时补；湖里有数据时确定性行是更好的答案，搜索仍应保持退役状态。原尾注「指数调样已由确定性行供给，不再网查」的无条件断言已删——那句话现在只对一半情形为真。这条兜底会在 B3 批的数据生产者真正上线后失去用武之地，但不在此之前删除它。

### 2.7 观察义务与归因

- `run_health.json` 加 `index_events: {source ok|absent|disabled|error, n_rows, n_finalists_involved, n_passive_close_eve, n_unresolved_eff}`（旋钮开或文件在场才出现，见 `index_events_health`，`autoresearch/scan/health.py`；`disabled` = 旋钮关且无文件，`absent` = 旋钮开但源不可达，`error` = 文件在场但读不出来（实施后补，final whole-branch review I2：读表异常此前会不设防地穿透整个 `run_health`，四个生产调用点都是 `contextlib.suppress(Exception)`，一炸就是整份体检都不刷新），三者是不同的因；`n_unresolved_eff`（I3）= `phase="unknown_eff"` 的行数，门沉默五因里唯一不可读的一种）。**门命中数不在这里**：`run_health` 在 E6 门跑之前就写盘，读不到门到底否决了谁——命中数只在决策文件 `index_events.hits`（旋钮开时的四态 `source: ok|absent|disabled|error`，见 §2.4）与 brief ③（有命中时加一行「⛔ 调样前夜否决 N 只」；`n_unresolved_eff>0` 时另加一行「❓ N 只候选生效日待定」）。
- `_buyability.json`：命中时 `wall="gates"` 的子原因可读出 `rebalance_close`（不新增 wall 态）。
- 半年读数（`docs/research/`）：每个 E−1 日，被拦票的实现 `gap_c1_o2` 与同日未变动成分股对照——**预期为负**；若连续两次调样为正，回滚杆一行关门并把读数交用户裁。
- 对账：月末 `index_weight` 快照 vs 公告名单 → `csindex` 解析器的正确率读数（`n_matched/n_announced`），进 prelude 摘要一行。

### 2.8 路线 C（预测名单席位）：负结果与重开条件

判据预注册为「预测集（含误报）A−1 夜超额 ≥ +0.4pp 且胜率 ≥ 65%」。读数（附录 B）：沪深300 +0.21pp / 49%，科创50 +0.55pp / 54%。**不立项。**

失败模式已定位：tushare `total_mv` 对 A+H/ADR 公司含境外股份（百济神州 12.8×、华虹 4.7×），中证按 A 股口径排名，这些票被系统性高估挤占缓冲区；ESG 负面剔除无数据不可复现。**误报票被市场惩罚**（−0.44pp、胜率 19%），因此该通道**精度 < 70% 时绝不能启用**。

重开条件：出现权威 A 股口径市值源（A+H 映射或 A 股总股本）后按同一判据重跑 spike；或改用券商预测名单（LLM/网查路径）——与本项目确定性优先相悖且每年只 2–4 夜，不推荐。若重开，形态 = 只在 D=A−1 那天镜像 `sector_seats` 注入保留席位，L3 图例三条对冲，E6 同规则比较（Wave12 裁定⑥：通道不自产 BUY）。

### 2.9 双引擎

Python 侧共用；agent 定义两侧同改（§2.5）；`.codex` 侧 hook 若需用户批准一次（09-15 记忆）在计划里标出。

## 3. 验收

### 3.1 离线（零 LLM，各批完成判据）

| # | 读数 | 通过判据 | 怎么读 |
|---|---|---|---|
| O1 | 解析器 fixture：小样本合成 xlsx（实施后补：`tests/data/test_csindex_source.py` 提交的是一份 9 行的合成夹具，同 2026-09-09 临时调整公告的真实版式，不是那份公告本身的 166 行——166 行只在 Task 2 的一次性「真源冒烟」手工核对过，从未进测试） | `test_parse_adjustment_xlsx_long_table`：白名单成员沪深300 的 drop/add 两个集合、中证500 的 add 行数，以及**非白名单控制组**上证380 的 drop 行数均按 fixture 逐项核对，且解析出的 code 恒 6 位、无「-」占位——上证380 与沪深300/中证500 的行同样全部入长表，证明解析器本身不按白名单过滤；非六指数行的白名单过滤发生在下游 `build_index_events` 的白名单判定，不在这个解析器测试里 | `tests/data/test_csindex_source.py` |
| O2 | 生效日推导 | 「X 日起生效」→ X 前一交易日；「X 日收市后」→ X；「自退市日起」→ 空 + `unknown_eff` | 同上，`test_parse_effective_date` 的参数化 fixture |
| O3 | 键不撞 | 两个公告 id / 两个指数同月 → 湖中两份文件 | `tests/data/test_index_event_endpoints.py` |
| O4 | parity | 旋钮关（`calendar.index_rebalance=false` ∧ `relative_buy.rebalance_gate=false`）→ `_relative_buy_decision.json` **除 `rule_version` 外**逐字节不变（`RULE_VERSION` 无条件升到 `"e6.v4.1"`，逐字镜像 v4.0 对 `tiering` 的先例，见 §2.4）。实施后补，纠正两处过度承诺：① 只测了决策文件半边，`calendar.csv` 的旋钮关 parity 不是靠回放历史 run 验证的——它靠构造性论证成立（第三腿整段在 `if index_rebalance:` 之后，旋钮关时那段代码从不执行，是既有单测 `test_harvest_calendar_third_leg_is_off_by_default` 锁的不变量），没有做过、也不需要做跨历史 run 的字节级回放；② 决策文件那半边可回放的历史 run 是 **7 个**不是 8 个（63/70 已发布 run 没有 staging 镜像或没有决策文件，不可回放） | 回放脚本（decision 半边）+ 单测（calendar 半边的构造性论证） |
| O5 | 门活体 | 构造 `index_events` 使某候选处于 `passive_close_eve` → 该票 `hard_gate.rebalance_close=false`，`blocked_reasons` 出现该桶，`gate_evaluated=true` | `tests/scan/test_relative_buy.py`（实施后补：不是独立文件——`test_rebalance_gate_vetoes_the_passive_close_eve_row_and_says_where` 等用例并入了既有的 `test_relative_buy.py`） |
| O6 | 缺席 ≠ 否 | 源不可达 → 文件缺席 + `degraded.json` 行 + 决策文件 `source=absent, gate_evaluated=false`；源可达无事件 → 空表 + `source=ok, n_rows=0, gate_evaluated=true`；日历腿本身关着 → `source=disabled`（final whole-branch review 追加的第四态，见 minor-1） | 同上文件，`test_rebalance_gate_source_*` 系列用例 |
| O7 | 变异 | 删掉 `_hard_gate` 第 ⑤ 段 → O5 红；删掉日历第三腿 → 日历测试红；删掉 agent def 那一行 → 锚测试红 | 手工变异一次，记录在 PR 说明 |
| O8 | 普查可复现 | `python -m autoresearch.research.index_rebalance_census` 复现附录 A 的 E−1 行（±0.02pp） | 研究 CLI |

### 3.2 真实扫描（2026-12 周期，两引擎各自计；量仪器不量市场）

| # | 日期 | 读数 | 通过判据 |
|---|---|---|---|
| L1 | 2026-11-27（公告日）晚扫描 | `index_events.csv` 行数 | > 0，且六指数各自行数与公告附件一致（对账脚本） |
| L2 | 11-30 → 12-09 | finalist 涉及调样票时 L4 简报出现 📅 事实行；卡评级分布 vs 同画像非调样票 | 无系统性抬升（防锚定观察项，同席位稿 :93） |
| L3 | **2026-12-10（E−1）** | 决策文件 `index_events.gate_evaluated=true`；候选中调样票全部 `rebalance_close=false`；brief ③ 有 ⛔ 行；两引擎卡入场行对这些票为 `禁止`（self_review 无 warn） | 全部成立 |
| L4 | 12-11 收盘后 | summary 📅 列出当日生效事件 | 出现 |
| L5 | 12-14 | 被拦票实现 `gap_c1_o2` vs 同指数未变动股 | **只记录**（n 太小不设门）；进半年读数 |

## 4. 批次、顺序、回滚杆

| 批 | 内容 | 何时 | 回滚杆 |
|---|---|---|---|
| B0 | 端点/契约登记、`csindex` adapter、`index_events` 产物登记与生产者、普查 CLI 产品化 | 批 4 冻结窗内可在 worktree 开发，**合并等批 4 结束** | 全惰性（无消费者），无需杆 |
| B1 | 日历第三腿 + flags/section 文案 | 批 4 后；**2026-11-27 前合并** | `calendar.index_rebalance=false`（一行，逐字 parity；**生产已开 `true`**，与 B2 应同开同关） |
| B2 | E6 硬门 + 决策文件块 + agent def 两引擎 + self_review warn + run_health/brief | 同上 | `relative_buy.rebalance_gate=false`（一行；**生产已开 `true`**，与 B1 应同开同关） |
| B3 | ETF 规模 → `flow_adv_days`；stock-research 确定性行 + WebSearch 兜底（实施后补，I4） | 12 月周期之后 | `calendar.index_rebalance_flow=false`（一行，独立于 `calendar.index_rebalance`，代码内建默认 `False`；实施后补：spec 原文未提这个旋钮名，B3 落地时才补——presence-gated，缺则文案无数字） |

顺序理由：B1 与 B2 分杆——「看得见」和「否决」是两件事，观察 L2 需要 B1 单独在场。

**B3 尚未上线的那一半（实施后补，final whole-branch review，I4）**：`index_weight`（成分快照,
`analyze/index_membership._membership` 的数据源）在 production 里目前**没有任何自动生产者**——
`calendar.index_rebalance_flow` 打开只会补一个 ETF 规模数字（`flow_adv_days`），不会让
`index_weight` 被取数,两者是不同的三源（`fund_basic`/`fund_share`/`fund_nav` vs
`index_weight`）。合并后到 B3 的成分数据生产者真正接上之前，stock-research full 档的成分行会
持续读成「湖内无（未取数）」；`ashare_corporate_calendar`（`analyze/blocks_ashare.py`）在这段
时间内补了一句 WebSearch 兜底（§2.6），条件是确定性行自己报告「湖内（成分）真的什么都没有」——
这不是本节表里的哪一根杆能开关的东西，是数据层缺一个尚未排期的生产者，读者不该把 B3 这一行的
「presence-gated」读成「B3 一上线这条兜底就自动消失」：兜底会一直挂着，直到有代码真的去写
`index_weight`。

## 5. 测试计划（按批）

- B0：adapter 用 fixture（列表 JSON + 详情 JSON + xlsx）离线测；契约 B 级降级记账测；键不撞测；ARTIFACTS 登记 hygiene 自动覆盖。
- B1：`calendar_flags` 三相位文案锚；`calendar_section` 市场行；旋钮三件套测试；parity 回放（O4）。
- B2：O5/O6 两组；`_field_usage(tiering)` 含五门；agent def 锚（两引擎）；self_review warn 用例；`brief` ③ 文案。
- 变异（O7）每批一次，写进提交说明。

## 6. 文档同步

`STAGES.md`（calendar 步、E6 五门、「已被实证否决的方向」加「指数纳入正向催化 / 预测名单席位」两条）；`SKILL.md` 配置表；`scan_config.jsonc` 两个新块带【生效点】/⚖️证据/回滚杆注；`.claude/agents/l4-card.md` 与 `.codex/agents/l4_card.toml`；`lite-playbook.md`；`engine-playbook.md:98`（调样不再网查）；`README` 数据源表加中证公告源。

## 7. 明确不做

不做正向加分（L3 列 / L4 方向词 / E6 面）；不复活 L1 `event` 通道、不借「事件」桶；不加 L3 守卫；不给 `l4-intel` 加第七面（`tests/scan/test_frozen_b_class_boundary.py:57` 冻结）；不进 composite；不做 MSCI / 富时 / 港股通（北向无合法机读源）；不做美股（stock-research 美股档已有 us-intel 面）；不换主尺、不提 swing、不重开 learning、不恢复 L4 复用；批 4 冻结窗内不合并任何非惰性改动。

## 附录 A：隔夜尺普查（2026-09-25，抛弃型 `index_census.py`，只读湖 + tushare）

事件按规则推（E = 6/12 月第二个周五，科创50 另 3/9；A = E − 14 天），调入/调出 = `index_weight` 相邻月末快照之差，对照 = 同指数未变动成分股，t_day 按（调样, 指数）格聚合。

调入票，5 个半年调样指数（剔科创50），n=1652：

| 扫描日 D | 超额 pp | 胜率 | t_obs | t_day |
|---|---|---|---|---|
| A−2 | −0.18 | 47% | −2.9 | −2.0 |
| A−1（预测夜） | +0.21 | 57% | 5.1 | 3.2 |
| A（公告夜） | −0.07 | 50% | −1.5 | −0.9 |
| A+1..E−3 | −0.10 | 49% | −5.6 | −5.4 |
| E−2 | −0.04 | 44% | −0.9 | −0.2 |
| **E−1（买 E 收 → 卖 E+1 开）** | **−0.31** | **35%** | −8.2 | −2.3 |
| E | −0.05 | 49% | −1.4 | −0.6 |
| E+1..E+3 | −0.07 | 49% | −2.6 | −2.4 |

分指数 E−1：中证500 −0.72（胜 22%，t_day −5.6）· 中证1000 −0.25（t_day −2.7）· 沪深300 +0.15 · A500 +0.05 · 科创50 +0.31（均不显著）。分指数 A−1：沪深300 +0.74（胜 75%）· A500 +0.63（胜 73%）· 科创50 +1.03（胜 79%）· 中证500 +0.10 · 中证1000 +0.16 · 创业板指 0.00。调出票各相位 −0.13 ~ +0.01。生效日成交额倍数：调入 1.86× vs 未变动 1.1×。ETF 规模（仅 ETF）沪深300 ≈3031 亿 / A500 ≈2040 亿 / 科创50 ≈1737 亿 / 创业板指 ≈958 亿 / 中证1000 ≈411 亿 / 中证500 ≈80 亿。

**两个人口，不要混用（实施后补）**：上表主表头（n=1652）**剔除科创50**（半年 vs 季度调样，周期不同）。`research/index_rebalance_census.py` 的 CLI 默认输出（`run_census()` 的 `tables` 键）不做这次剔除，六指数全池——同一相位 `5.E-1`（调入票）在该池读数为 n=1698、超额 `-0.297pp`（四舍五入 `-0.30pp`）。两个数字**都是各自人口下正确的读数**（互相印证，相差 <0.02pp），但量的是不同人口：本附录引用 −0.31pp 时说的是「5 个半年调样指数」，`tables.add["5.E-1"]` 说的是「六指数全池」——不能把后者当成前者的复现，也不能反过来引用。

边界：n=17 次调样、2022–2026 单一大周期、未扣成本、月末快照含临时调整污染、事件日期按规则推未逐条核公告。

## 附录 B：预测器 spike（2026-09-25，抛弃型 `predictor_spike.py`）

复刻规则：过去一年日均成交额剔后 50%（科创50 剔后 10%）→ 日均总市值排名 → 缓冲区 240/360（科创50 40/60）→ 10% 上限；数据截止 4/10 月末（科创50 1/4/7/10 月末）；A+H 公司按名称匹配 + 科创板手工名单改用 A 股流通市值近似。

| 指数 | 次数 | 精度 | 召回 | 预测集 A−1 夜 | 误报票 | 完美预见 |
|---|---|---|---|---|---|---|
| 沪深300 | 7 | 37–70% | 64–94% | +0.21pp · 胜 49% · t_event 0.6 | −0.44pp · 胜 19% | +0.90pp · 胜 80% |
| 科创50 | 14 | 0–80% | 25–100% | +0.55pp · 胜 54% · t_event 2.7 | +0.05pp | +1.29pp · 胜 78% |

「总市值/流通市值 > 1.12 视为 A+H」的启发式是错的（1450 只被标，抓到的是限售股），已弃。
