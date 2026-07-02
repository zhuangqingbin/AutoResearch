# scan-market 观察单触发器日检 + 0买对照读数 + L2 菜单体检 — 设计

- 状态:设计定稿(待实现)
- 日期:2026-07-02
- 关联:`docs/specs/2026-07-01-scan-market-strategist-view-design.md`(staging/嵌入先例)、`autoresearch/learning/channel_ledger.py`(跨日 ledger 先例)
- 触及:`autoresearch/scan/watchlist.py`(新)、`autoresearch/scan/menu.py`(新)、`autoresearch/learning/zero_buy_ledger.py`(新)、`autoresearch/scan/assemble.py`(嵌入)、`.claude/skills/scan-market/SKILL.md`

## 1. 背景与目标

哑铃/避险市里 scan 连续 0 买(06-24→07-01 六日),系统的高价值产物从买单转移到**待触发观察单**(胜宏:"8月中报 beat + 站回多头排列 + 守 314/298")。但现状三个缺口:

1. **观察单是死文本**:触发条件写进报告就没人再看,无日检、无到期、无状态。
2. **0 买无对照**:无法区分"纪律空仓(对)"和"漏斗失明(错)"——需要一个数字:0 买日之后市场实际怎么走。
3. **菜单病发现太晚**:06-30 那种"L2-200 健康上涨 0 只"要 L4 烧完 token 才被人看见,应该在 L2 一出就有确定性报表喊出来。

三件全是**确定性、零 LLM**,复用既有 staging/嵌入模式。

## 2. 组件设计

### 2.1 `autoresearch/scan/watchlist.py`(新)

**存储**:`context/watchlist.csv`(跨日活状态,gitignored),列:
`code,name,born,expiry,source,narrative,conds,invalidation,note`
- `conds` / `invalidation`:JSON 数组字符串,机判条件词表 v1:
  - `{"kind":"close_above","value":314.0}` — 收盘 ≥ value
  - `{"kind":"close_below","value":298.5}` — 收盘 ≤ value(用于 invalidation)
  - `{"kind":"ma_bull"}` — L1 列 `ma_bull > 0`(站回多头排列)
  - `{"kind":"money_pos"}` — `main_net_ratio>0 且 cmf_20>0`(资金转正)
  - `{"kind":"manual","text":"中报毛利止跌"}` — 不可机判,恒返回 `manual`
- `expiry`:born + 默认 45 个日历日(建 row 时可覆盖)。

**API**(纯函数为主,synthetic 可测):
- `load_watchlist(path) -> DataFrame`:缺文件 → 空帧(带列)。
- `ingest_verify(scan_dir, path) -> int`:从 `verify.csv` 的 `verdict==降级` 行**草拟**新条目(narrative=trigger 列原文、conds=[]、source=skeptic、born=scan 日),按 `(code, born)` 去重 append。结构化 conds 由编排层(Claude/PM)在 scan 流程里补——机器只搬运不理解。
- `check(wl, l1_full, date) -> DataFrame`:逐条目逐条件判 `yes/no/unknown/manual`(unknown=缺列/该 code 不在 L1_full);overall:
  - `失效`:invalidation 任一 yes,或 date > expiry;
  - `触发`:全部非 manual 条件 yes 且 ≥1 条(manual 条件未清 → 状态 `触发(待人工项)`);
  - `临近`:≥1 条 yes;其余 `待触发`。
- `run_check(date, scan_dir, path) -> DataFrame`:读 `L1_scored_full.csv` + watchlist → check → 写 `context/scan/<date>/watchlist_status.csv`。
- `render_watchlist_block(status) -> str`:L5 嵌入块 `### 👀 观察单日检`,触发置顶、失效折行;空 → ""。

### 2.2 `autoresearch/scan/menu.py`(新)

- `menu_health(scan_dir) -> str`:读 `L2_gbdt_top200.csv` + `L1_scored_full.csv` → markdown 块 `### 🍱 L2 菜单体检`:
  - 行业集中度 top3(占比,vs sector cap);
  - 落刀面(pct_60d<−20)占比:L2 vs 全市场;
  - **健康上涨**(0<pct_60d<40 且 main_net_ratio>0 且 cmf_20>0)计数:L2 vs 全市场(06-30 病灶指标);
  - 估值:L2 中位 PE(正值)、PE>60 占比 vs 全市场;
  - 风格桶计数(`l2_lane_reserved` 救回数 + `recall_channels` 风格粗分)。
  - 缺文件/缺列 → 对应行降级或返回 ""(不抛)。

### 2.3 `autoresearch/learning/zero_buy_ledger.py`(新,镜像 channel_ledger)

- `roll(scan_root) -> DataFrame`:遍历 `context/scan/*/retro/attribution.csv`,每日一行:
  `date, n_bought(attribution.bought 求和), mkt_fwd1(全市场 fwd_1_oo 均值), mkt_fwd5(fwd_5_oc 均值,NaN 容忍)`。
- `render(df) -> list[str]`:md 表 + 汇总行:**0买日 vs 有买日的市场后市对照**(0买日 mkt_fwd5 均值为负 = 空仓对了;显著为正 = 失明预警)。
- CLI:`python -m autoresearch.learning.zero_buy_ledger` → `reports/learning/zero_buy_ledger.md`。

### 2.4 `assemble.py` 嵌入(L5)

- buy-list 段之后:`watchlist_status.csv` 存在 → 嵌 `render_watchlist_block`;
- 阶段概览 L2 行之后:`menu_health(scan_dir)` 非空 → 嵌。
- 均 presence-gated:staging 缺 → 不加节(老目录重跑 parity 不破);assemble 保持零 LLM(只读文件)。

### 2.5 SKILL.md 流程接线

- 步骤 1 之后(universe 跑完):`watchlist.run_check(date, scan_dir)` + 把"已触发"条目在过目时呈现;
- 买单 skeptic 之后:`ingest_verify` 草拟 + **编排层(Claude)为新条目补结构化 conds**(词表 v1);
- L5 自动嵌入(无需操作)。

## 3. 数据契约

- 输入列依赖(已实测存在于 `L1_scored_full.csv`):`code,name,close,ma_bull,main_net_ratio,cmf_20,pct_60d,pe`。
- `watchlist_status.csv` 列:`code,name,status,detail(逐条件 k=v;分隔),narrative,born,expiry`。

## 4. 测试(TDD,合成 fixture,无网络)

- `tests/scan/test_watchlist.py`:load 缺文件空帧;ingest_verify 草拟+去重;check 四态(触发/临近/待触发/失效)+ manual 态 + unknown 降级;render 触发置顶/空串。
- `tests/scan/test_menu_health.py`:合成 L2/L1 → 断言健康上涨计数、落刀占比、行业 top、缺列降级、缺文件 ""。
- `tests/learning/test_zero_buy_ledger.py`:合成 attribution.csv × 2 日(0买/有买)→ roll 行数与字段、render 对照行;空目录优雅。
- `tests/scan/test_assemble.py` 增:有 watchlist_status → summary 含 `👀 观察单日检`;无 → 不含(parity);menu 同理。

## 5. 非目标

- 不解析 L4 卡片散文里的触发条件(机器只搬 verify.csv,结构化交编排层);
- 不做盘中/实时监控(日频,随 scan 跑);
- 不自动升级评级(触发只提示复核,评级仍由 L4/rubric 定);
- 0买对照不进当日 summary(fwd 当日未知,只进 learning ledger)。
