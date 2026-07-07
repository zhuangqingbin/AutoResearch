# scan/analyze 研究深度增强波 设计

- **日期**: 2026-07-07
- **状态**: 设计待审(brainstorming 产出,用户已认可范围 + A1)
- **作者**: Claude(session 内)+ 用户拍板
- **相关**: `.claude/skills/scan-market/SKILL.md`·`STAGES.md`、`.claude/skills/stock-research/lite-playbook.md`、`.claude/skills/macro-research/macro-playbook.md`、`autoresearch/analyze/harvest.py`、`autoresearch/common/uzi_lenses.py`、`autoresearch/data/tushare_enrich.py`、`.claude/workflows/scan-market.js`

---

## 1. 背景与问题

用户驱动(2026-07-07)三问:① 新闻要用 **WebSearch 活体调研**最新相关新闻才准;② 股票**筹码形态**能否更深;③ 每阶段能否**不断深入**。审计现状(file:line 实证):

- **新闻**:活体 WebSearch 只在 `l4-card` P5「补催化日期」;L3 靠预取 `anns_d` 公告标题词表情感(`l3_news.py`,断链回退东财 `L3_webnews`)+ `l3_catalyst.py` 增减持/回购/调研计数;`sector-brief`/`market_view` **pack-only 无网查**(tools 无 WebSearch)。→ **无"逐票/逐行业活体新闻语义调研"**。`l3_catalyst.py:5` 自曝:"30/30 卡'无明确催化'……是探测盲(只有公告情感+日历)"。
- **筹码/游资**:`analyze/harvest.py:1158–1165` 的 `--slim` 路径(scan L4 卡实际读)**已含**融资趋势(`_uzi_margin`)、杀猪盘/派发风险(`_uzi_trap`)、量价吸筹(`_uzi_volprice`)、A股原生财报;但 **`harvest.py:1166` 一句 `if not slim:` 把龙虎榜游资席位识别 `_uzi_seats`(=`uzi_lenses.lhb_seats`,已识别机构 vs 游资 + Phase A 反指标注)挡在 slim 外** → L4 卡无游资身份。`factor_lab` 已算 `chip_concentration`(集中度)/`price_to_cost`(>1 浮盈/<1 套牢)/`cost_premium`(`factor_lab.py:287–291`,cyq_perf 拉 15/50/85 分位),但 slim 筹码块只见 `winner_rate`+`cost_50pct`(`tushare_enrich.py:87–97`)。
- **深度**:`l4-card` P0–P5 渐进 + `force_full_card`(conviction≥70∧channels≥4,`l4_card.py:337`)已有;但 **scan 恒 lite**(`SKILL:68,80`),L3 单遍 holistic,无多轮——本波**不碰**(见 §2 非目标)。
- **macro**:`market_view` 由 workflow inline `agentType:'claude'` 派(`scan-market.js:50–52`),**无专用 agent-def、无 WebSearch**;而 `sector-brief` 有专用 leaf agent。不对称。

**核心结论**:#2/#4a 大半是**已建能力没接进 slim**(游资/trap/margin/筹码分布 lens 都在),非新建;#1(新闻)是主要 net-new(agent 行为 + WebSearch 预算);macro-brief 是补对称。

---

## 2. 目标 / 非目标

### 目标
1. **#1 新闻活体调研**:`l4-card`/`sector-brief`/`macro-brief` 加 WebSearch 有界新闻调研(预算 cap、claim 落日期、as-of≤分析日过滤)。
2. **#2 + #4a 筹码/资金/陷阱上卡**:游资席位识别进 slim(**A1**)、筹码分布上卡、trap 增强、rubric 强调。
3. **macro-brief agent**:新建 agent-def + workflow 改派 + 契约锚。

### 非目标(YAGNI)
- **4b 产业链联动缓做**:顶撞 L4 独立性铁律(每卡独立 context、不知他票结论),真做另立"产业链综述层"(L4 后读已完成独立卡再综合),不进本波。
- **L3 不加活体 WebSearch**:200 只逐票网查太贵;继续预取 `anns_d` + 东财回退。
- **不碰** scan→full 升级 / L3 多轮 / **L4 独立性铁律**。
- **L0/L1/L2/L5 确定性层不动**(parity 锁死)。

---

## 3. 架构

### 3.1 #2 + #4a 筹码/资金/陷阱(确定性数据层为主)
- **A. 游资进卡(A1)**:`harvest.py:1166` 去掉 `_uzi_seats` 的 `if not slim` gating(slim 也渲染席位块)。多日 `top_inst` 取数成本走 **pledge 式"跨 scan 日复用 + 限频"**(参 `l4_card pledge`:`context/scan` 复用近 N 日已拉、远离限频)。`lhb_seats` 已产 机构 vs 游资席位 + 对倒 + Phase A"机构上榜买入后续偏弱=反指"标注。
- **B. 筹码分布上卡**:slim 筹码块补 `chip_concentration`/`price_to_cost`/套牢盘结构(cost_15/50/85 分位派生);源=`factor_lab` 已算、在 L1 row(slim 复用 L1 因子行)。**确认 render,缺则加**(`tushare_enrich` 或 slim 组装侧)。
- **C. trap 增强**:`uzi_lenses.trap_signals` 现只覆盖"派发空半";扩规则集——质押>阈 + 股东减持共振 / 商誉减值 / 游资对倒(=A 的席位)→ `render_trap_block` 硬旗。**确定性、零 LLM = 把删掉的 buy-skeptic 攻击面(估值/解禁质押/主力背离/业绩雷/派发)用规则找回**。
- **D. rubric 强调**:`l4-card.md` 评分卡"技术·资金"维 + 铁律,明确要求读 席位/筹码分布/trap 旗;**不改早停逻辑/评级映射/OW 三门**。

### 3.2 #1 新闻活体调研(agent 行为)
- **L4**:`l4-card` P3 加**有界新闻调研子步**——每卡 WebSearch **≤3 条**(cap,可调)定向查询(`<名称> 最新 公告/业绩/催化/风险 近1月`),读到 claim **必落日期 + as-of≤分析日过滤**(推广现有 P5 前视铁律);token 表加"L4 新闻网查"行。**cap 默认低**,0 买/病菜单日可关。
- **sector-brief / macro-brief**:tools 加 `WebSearch/WebFetch`;agent-def/playbook 加"pack 数字 + 最新头条网查(标『实时网查』、落日期)"。
- **L3 不动**。

### 3.3 macro-brief agent
- 新建 `.claude/agents/macro-brief.md`:`model: opus`、`tools: Read, Write, Grep, Glob, WebSearch, WebFetch`;system prompt = `macro-playbook.md` §68–98「市场研判首席策略师」烤入(**6 小节** + 防锚定不变量:1–3 描述性地形喂 L3/L4、4–5 规范性仅 L5、个股不评级不锚定卡片)。
- `scan-market.js:50–52` inline `agentType:'claude'` → `agentType:'macro-brief'`,prompt 缩到指向 `market_pack.json` + 落点 `market_view.md`。
- 契约锚进 `test_agent_defs.py`:`_NAMES` 加 `macro-brief`;anchors = 6 小节标题/防锚定铁律;真值源 `macro-playbook`;断言含 WebSearch tool。

### 3.4 关键不变量(必须守)
- **L4 独立性**:每卡独立 context、不知他票结论——新闻/席位/trap 都是**本票数据**,不引跨票。
- **防锚定**:`market_view` 1–3 描述性地形喂 L3/L4、方向只进 L5;新闻调研只报本票事实不喊单;个股评级只由 rubric 三门。
- **as-of**:新闻 claim 必 ≤分析日(现有 P5 铁律推广到 P3 网查)。
- **parity**:确定性层(L1/L2/L5)不动;新块 **presence-gated**(取数失败降级占位,不抛)。
- **成本可见**:席位取数/新闻网查 **计入 token 表 + 限频复用**。

---

## 4. 交付分期(边建边验)
1. **P1 筹码/资金/陷阱(确定性,可测,低风险)**:A 游资进 slim + B 筹码分布 + C trap 增强 + D rubric。pytest。**先落=白捡已建能力**。
2. **P2 macro-brief agent**:agent-def + workflow 改派 + `test_agent_defs` 契约。小、独立。
3. **P3 新闻活体调研**:`l4-card` P3 子步 + sector/macro WebSearch + 预算 cap + token 表行。agent 行为,靠冒烟。
4. **P4 端到端冒烟**:下次真扫描核账——席位/筹码/新闻是否上卡、token/墙钟增量、L4 独立性未破。

---

## 5. 测试策略
- **P1 确定性**:`trap_signals` 扩展单测(质押+减持共振/商誉/对倒触发)、slim 契约测试(席位块 presence)、chip 分布派生单测。延续现有 739 绿。
- **契约**:`test_agent_defs` 加 macro-brief 锚 + WebSearch tool 断言。
- **新闻/rubric**:agent 行为无单测,靠 P4 冒烟 + as-of 过滤 helper 单测(若抽出确定性函数)。

---

## 6. 风险 / 待定
- **游资多日 top_inst 取数成本/限频**:靠"跨 scan 日复用"缓解;真成本 P4 见。
- **新闻网查 token 膨胀**:cap + 0 买日可关;P4 OTEL 核。
- **trap 增强误报**:advisory 硬旗但**不自动改评级**(与质押旗同——取证后再议升门,人拍板)。
- **`lhb_seats` "机构反指"先验**:Phase A 实测,注入 prompt 时标来源/样本,避免过拟合。
- **待定**:B 的 chip 分布究竟在 slim 已 render 还是需补——P1 首步核实。

---

## 7. 成功判据
下次真扫描:L4 卡可见 **游资席位 + 筹码分布 + trap 硬旗**,且每卡有**活体新闻调研落日期**;`macro-brief` 产出 `market_view` 且带最新头条;**L4 独立性 / 防锚定 / parity 未破**;token 增量**可见可控**。
