# 免 token 直连数据源层(keyless sources)设计

> **状态**:brainstorm 通过(参考 `simonlin1212/a-stock-data` 的直连栈 + 本环境可达性实测),待写 TDD plan。母文档:CLAUDE.md 数据层、记忆 `ashare-data-tushare-not-push2`。

**Goal**:加一层**免 token 直连源**,补 tushare 三个缺口——①卖方**前瞻一致预期 EPS**(我们完全没有,L4 要 fwd-PE)②权限缺失被置 NaN 的富因子回填 ③无 token 兜底——**补充/兜底,不替换 tushare**。

**Architecture**:新模块 `autoresearch/data/keyless.py`,统一 `_keyless_get`(UA + 串行限流 + session 复用,防封);各源纯解析函数(可离线测)+ 薄 HTTP 包装 + lake 缓存(复用 `get_or_fetch` 的 `fetch=` 回调)。三源独立、各自降级,任一失败不影响主链。

**Tech Stack**:Python / requests(缺则回落 urllib)/ pandas / pytest;复用 `autoresearch.data.cache.get_or_fetch`。

## Global Constraints

- **补充/兜底,不替换**:tushare 仍主源;keyless 只在「tushare 没有 / 置 NaN / 无 token」时补位。
- **单测零网络**:解析是纯函数(喂 HTML/JSON fixture);HTTP 包装注入 `get=` 桩。
- **降级隔离**:任一源/单只取数抛错 → 返回空帧/None + 记日志,绝不抛到主链。
- **防封**:所有直连走 `_keyless_get`(串行 `_MIN_INTERVAL=1.0s` + 抖动 + UA + `Session` 复用)。**只在 L4 ~30 只深挖用,不进 L0/L1 全市场热路径。**
- **本环境可达性(实测 2026-06-23)**:✅ 腾讯 / 同花顺 / 东财 `datacenter-web`·`push2his` / 巨潮 / 北向;⚠️ `push2.eastmoney.com` → 502(继续避开);mootdx 3 host 选 1 可达(`123.125.108.14:7709`)。

---

## 背景:为什么、踩哪些坑

- 我们(记忆 `ashare-data-tushare-not-push2`)因 push2 被封转 tushare;tushare 需 token + 权限分级,缺权限的富因子降级置 NaN。参考 repo 证明**免 token 多源直连可行**。
- **坑①脆弱**:直连是抓包端点,会断(repo CHANGELOG:财联社下线、百度 PAE 弃用)→ 定位为兜底,主源仍 tushare,降级要干净。
- **坑② push2 仍封**:`datacenter-web`/`push2his` 是不同 host、实测可达,但 `push2.eastmoney.com` 502 → 不用它。
- **坑③限流**:必须串行 + 抖动,故只在 L4 少量股用。

---

## 组件

### 0. 共享层 `autoresearch/data/keyless.py`

```python
_UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"
_MIN_INTERVAL = 1.0

def _keyless_get(url: str, *, params=None, headers=None, timeout=8, encoding=None) -> str:
    """串行限流(_MIN_INTERVAL + 抖动)+ UA + Session 复用的 GET → 返回 text。raise_for_status。"""
```

lake 缓存:经 `get_or_fetch("<endpoint>", params, today=, fetch=<本源 raw 取数回调>)`,在 `cache.policy` 注册各 endpoint(key=code、按日结算——EPS/股东户数等慢变,日级缓存够)。

### 1. 源①:同花顺一致预期 EPS(本期实现,已 de-risk)

**数据位置(实测 600519)**:`worth.html` 内 `<div id="yjycData" class="none">[["2019","32.80","412.06","SJ"],…,["2026","68.82","861.83","YC"],["2027","72.61","909.21","YC"]]</div>`,每行 `[年, EPS, 净利润亿, 类型]`,类型 **SJ=实际 / YC=预测**。静态 HTML,无需 JS。

```python
def parse_consensus_eps(html: str) -> pd.DataFrame:
    """从 worth.html 抽 yjycData JSON blob → DataFrame[year(str), eps(float), np_yi(float), kind('SJ'|'YC')]。
    抽不到 → 空帧(列在)。纯函数。"""

def fetch_consensus_eps(code: str, *, get=_keyless_get, today: str | None = None) -> pd.DataFrame:
    """GET basic.10jqka.com.cn/new/{code}/worth.html(gbk)→ parse_consensus_eps;lake 缓存;出错→空帧。"""

def fwd_eps(df: pd.DataFrame, year: str | int) -> float | None:
    """取某年的预测 EPS(kind=='YC');无该年预测 → None。"""
```

- URL:`https://basic.10jqka.com.cn/new/{code}/worth.html`(`code`=6 位);`encoding="gbk"`;headers 带 `Referer: https://basic.10jqka.com.cn/`。
- 解析:`re.search(r'id="yjycData"[^>]*>(\[.*?\])\s*</div>', html, re.S)` → `json.loads` → DataFrame;`eps`/`np_yi` 转数值。
- **集成**:`autoresearch/analyze/harvest.py` 的 slim/full context 加「卖方前瞻」块——取下一财年 `fwd_eps` → `fwd_PE = 最新价 / fwd_eps`,写进 L4 决策卡可读的 staging,补 brief 里「fwd PE 远低于 TTM 时核实」的真值缺口。**集成为本期第 2 个 task**(fetcher 先独立可测)。

### 2. 源②:东财 datacenter 富因子回填(下一阶段,本 spec 仅定范围)

`datacenter-web.eastmoney.com/api/data/v1/get`(实测可达)统一封 `eastmoney_datacenter(reportName, filter_str, …)`;按 `reportName` 出:股东户数 `RPT_HOLDERNUMLATEST`、融资融券 `RPTA_WEB_RZRQ_GGMX`、大宗 `RPT_DATA_BLOCKTRADE`、龙虎榜 `RPT_DAILYBILLBOARD_DETAILSNEW`、解禁 `RPT_LIFT_STAGE`、分红 `RPT_SHAREBONUS_DET`。**定位**:tushare enrich 缺权限置 NaN 时的回填;接入 `tushare_enrich.py` 的降级分支。**独立 plan 实现。**

### 3. 源③:mootdx 价格/财务兜底(下一阶段,本 spec 仅定范围)

`pip install mootdx`(新依赖)+ host failover(实测仅 `123.125.108.14:7709` 通,需多 host 轮询)。出:日 K(category=4)、37 字段财务。**定位**:`load_ohlcv` 在 tushare/yfinance 不可用时的免 token 兜底 + 无 token 模式。**独立 plan 实现。**

---

## 数据流(源①)

```
L4 深挖某只 → analyze.harvest(code) →（新）fetch_consensus_eps(code)
   _keyless_get worth.html(gbk, 限流)→ parse_consensus_eps → lake 缓存
   → fwd_eps(下一财年)→ fwd_PE = price / fwd_eps → 写进 slim staging「卖方前瞻」块
   → L4 决策卡读它算真 fwd-PE(替代 TTM PE 猜测)
```

## 文件结构(源①)

- **Create** `autoresearch/data/keyless.py`:`_keyless_get`、`parse_consensus_eps`、`fetch_consensus_eps`、`fwd_eps`。
- **Modify** `autoresearch/data/cache.py`(或 policy 注册处):注册 endpoint `ths_consensus_eps`(key=code,日结算)。
- **Modify** `autoresearch/analyze/harvest.py`:slim/full context 加「卖方前瞻 EPS / fwd-PE」块(task 2)。
- **Create** `tests/data/test_keyless.py`:`parse_consensus_eps`(yjycData fixture)、`fwd_eps`、`fetch_consensus_eps`(注入 `get=` 桩,无网络)、降级(blob 缺失→空帧)。

## 测试策略(TDD,零网络)

- `parse_consensus_eps`:喂含 `yjycData` 的合成 HTML 片段 → 断言行数、kind 分类(SJ/YC)、eps 数值、前瞻年取值;喂无 blob 的 HTML → 空帧(列在)。
- `fwd_eps`:DataFrame → 取 YC 年值;无该年 → None。
- `fetch_consensus_eps`:`get=` 注入返回固定 HTML 的桩 → 断言 DataFrame;桩抛错 → 空帧不抛。
- 仿 `tests/scan/test_l3_news.py` 的 monkeypatch 注入风格。

## 非目标(YAGNI)

- 不替换 tushare 任何主路径;不进 L0/L1 全市场热路径。
- 源①不抓「业绩预测详表」逐机构表(read_html 表 2)——只取 yjycData 的年度均值预测(够算 fwd-PE)。
- iwencai(需 key)、财联社(已下线)不做。

## 自检

- 占位:无 TBD。
- 一致:`parse_consensus_eps`/`fetch_consensus_eps`/`fwd_eps` 三处签名贯穿组件/数据流/测试;`kind` 值域 `SJ`/`YC` 固定。
- 范围:源①单 plan 可实现(1 建 + 2 改 + 1 测);②③明确划为后续独立 plan(避免一次过大)。
- 歧义:`code` 6 位无后缀;`encoding="gbk"`;限流只在 L4——均写死。
