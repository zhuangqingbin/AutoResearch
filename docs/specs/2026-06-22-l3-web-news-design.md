# L3 外源新闻(akshare 媒体 @ L3 + WebSearch @ L4 finalists)— 设计

> 状态:已 brainstorm 定稿(2026-06-22)。下一步 → writing-plans。
> 关联:`docs/specs/2026-06-22-l3-opus-sentiment-design.md`(anns_d 公告情感);`autoresearch/scan/agents/l3_news.py`。

## 1. 背景与动机

L3 holistic 选股现有情感信号 = **tushare `anns_d`(公告标题)** digest(`news_n/news_tags/news_head`,
`l3_news.py`)。公告是**官方披露**,覆盖事件(回购/减持/预告…)但**不含媒体/外源视角**(研报评级、
突发、政策、订单、舆情)。用户要"L3 多一个通过 web 查最新消息的外源信息"。

**机制(brainstorm 定):hybrid**——
- **akshare 个股新闻(`stock_news_em`)→ L3**:确定性入湖、免费 keyless、镜像 anns_d,给**全 ~200**
  L2 survivors 加一层**媒体情感**(广度,零额外 token)。
- **Claude WebSearch → L4 finalists(~30)**:对深挖的少数股**真·联网**抓最新外源催化(深度,按次 token)。

## 2. 目标 / 非目标

**目标**
1. L3 紧凑表新增**媒体新闻 digest**(`med_n/med_tags/med_head`),与 anns_d 的 `news_*` 并列,
   Claude 通看 ~200 选 30 时区分 **公告 vs 媒体** 两路情感。
2. L4 finalists 决策卡(analyze-ticker-lite)增**WebSearch 实时外源催化**一步 → 纳入 `catalyst/risk`。

**非目标**
- 不替换 anns_d(公告仍在,二者互补)。
- 不对全 ~200 跑 WebSearch(只 finalists,控成本 + 控不可复现)。
- 不改 L1/L2(确定性漏斗不动)。
- akshare 新闻**不进确定性打分**(有软文噪声)——仅作 Claude 的定性情感佐证。

## 3. 架构

```
  L3 (全 ~200, 确定性)                          L4 finalists (~30, agentic)
  akshare stock_news_em(symbol)               analyze-ticker-lite subagent
    │ get_or_fetch(as_of 入湖,keyless)           │ + WebSearch("<name> 最新 研报/突发/政策")
    ▼ 归一 新闻标题→title 发布时间→ann_date         ▼ 提炼 1–2 条 真·催化 + 时效
  news_digest(prefix="med") → med_n/med_tags/med_head   → 卡片 catalyst / risk(定性佐证)
    │ load_l3_input merge(与 anns_d news_* 并列)
    ▼
  L3 紧凑表 → holistic 选股(公告 vs 媒体 两路情感)
```

### Part A — L3 akshare 媒体新闻(确定性)

`autoresearch/scan/agents/l3_news.py` 扩:

- `news_digest(anns, prefix="news")` —— 加 `prefix` 形参(默认 `"news"`,anns_d 调用**不变**);
  输出键 `f"{prefix}_n"` / `f"{prefix}_tags"` / `f"{prefix}_head"`。复用利多/利空关键词打标。
- `harvest_l3_web_news(date, codes, root=None)` —— 逐 code:
  `get_or_fetch("stock_news_em", {"symbol": code}, today=date)`(端点已登记 `as_of/akshare`,入湖缓存);
  归一 akshare 列(`新闻标题→title`、`发布时间→ann_date`)→ 落 `context/scan/<date>/L3_webnews/<code>.json`。
  **best-effort**:单 code 失败/空 → 空列表(降级,不阻塞)。返回 `{code: [news]}`。
- `l3_select.load_l3_input` —— 并入 `news_digest(web, prefix="med")` → `med_n/med_tags/med_head` 列;
  `_L3_COLS` 追加这三列(紧挨 anns_d 的 `news_*`)。缺 → 缺省 `0/""/—`。

### Part B — L4 finalists WebSearch(agentic)

`screening-playbook.md` L4 段加一步(**prompt/playbook,非确定性代码**):
- L4 Tier-1 决策卡 subagent(analyze-ticker-lite,~30 finalists)在建卡前对该股
  **WebSearch**(如 `"<名称> <代码> 最新 研报 突发 政策 订单"`)→ 提炼 **1–2 条真·催化 + 时效**
  (注明日期/来源),纳入卡片 `催化` / `风险`,作**定性佐证**。
- **边界**:数字/评级仍出自确定性 slim context(继承 analyze-ticker-lite 铁律);WebSearch 仅定性、
  仅 finalists;无网/无结果 → 跳过(卡片照常)。

## 4. 文件清单

| 动作 | 文件 | 职责 |
|---|---|---|
| 改 | `autoresearch/scan/agents/l3_news.py` | `news_digest` 加 prefix;新增 `harvest_l3_web_news` |
| 改 | `autoresearch/scan/agents/l3_select.py` | `load_l3_input` 并入 `med_*`;`_L3_COLS` 追列 |
| 改 | `.claude/skills/scan-market/screening-playbook.md` | L3 段记媒体新闻;L4 段加 WebSearch 步 |
| 改 | `.claude/skills/scan-market/SKILL.md` | L3 行注媒体情感 + L4 WebSearch |

## 5. 测试(合成 fixture,无网络)

- `tests/scan/test_l3_news.py`(扩):`news_digest(prefix="med")` 键名正确;anns_d 默认 prefix 不变。
- `harvest_l3_web_news`:注入 `get_or_fetch` 桩(返回带 `新闻标题/发布时间` 的帧)→ 归一 + 落 json +
  digest;单 code 异常 → 空列表降级、不抛。
- `tests/scan/test_l3_news_table.py`(扩):`load_l3_input` 含 `med_n/med_tags/med_head`,缺数据时缺省。
- Part B 无单测(prompt/playbook)。

## 6. 验收标准

1. L3 紧凑表含 `med_*`(媒体)与 `news_*`(公告)两路情感列;无网/无权限 → 缺省、不报错。
2. `harvest_l3_web_news` 入湖缓存(as_of)、断点续(二次命中零取数)、单股降级隔离。
3. screening-playbook L4 段写明 WebSearch 步 + 边界(仅 finalists、仅定性)。
4. 新增/改动有合成 fixture 测试,`pytest` 全绿,ruff 干净。
5. **诚实**:媒体新闻软文噪声 → 仅情感佐证须价量/基本面背书;WebSearch 不可复现/有时延 → 仅 finalists 定性。

## 7. 诚实风险

- akshare `stock_news_em` 逐股拉(~200 次)→ 首跑慢(~分钟级),as_of 缓存后零成本;无网/被限 → 降级缺省。
- WebSearch 结果随时间漂移、不可复现 → 只作定性催化,绝不进确定性打分或评级数字。
- 媒体新闻含软文/旧闻 → digest 标 recency(最新标题),Claude 自行判时效与可信度。
