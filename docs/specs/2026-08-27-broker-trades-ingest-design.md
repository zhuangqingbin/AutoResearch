# 券商成交记录取数层 —— 设计稿(里程碑 1:两户成交数据走通)

日期:2026-08-27 · 状态:**格式无关核心已实施**(计划 `docs/plans/2026-08-27-broker-trades-ingest-plan.md`,分支 feature/broker-ingest)**→ 待真样本探针建券商 adapter**
路径:brainstorming(architectural)· 复盘层**明确延后**(用户 08-27:「先把怎么拿到交易数据走通」)

## §0 一句话

把用户两个 A 股账户(**太平洋证券**、**国泰海通**)的真实买卖记录,从手机 App 导出的文件里
**确定性、零 LLM** 地解析成一张标准化成交表,幂等落到 `context_<engine>/broker/trades.csv`,
带数据契约与跨源核对。复盘(行为体检 / 与 scan 对表 / 逐笔叙事 / pinned 自动化)是**下一稿**的事,
本稿只负责「数据在、对、可重跑」。

## §1 背景与裁定

- 用户提问(08-27):「自动读取我 A 股证券账户一段时间的买卖操作,然后复盘,可以实现吗」。
- 环境:**只用手机 App**(无 PC 客户端、无程序化接口权限);机器是 macOS;两个账户。
- 用户裁定(08-27,本稿依据):
  1. **先取数走通,复盘以后再说**;
  2. 认可路线「A 中国结算主干 + B 券商交割单补字段/核对 + C 截图兜底」,**先用真样本探针再定主干**;
  3. 认可落点用 iCloud Drive 文件夹;
  4. 认可「自动」的天花板 = **每期在 App 点一次导出,之后全自动**。
- 与 2026-08-21「整个 learning 层退役」的关系:那层死在「输入没人产」;本稿的输入(成交)由券商
  逐笔生产,且姿态与 `scan/outcome.py` 一致——**只记不学**:不回注 prompt、不改门/权重/评级、
  不产 proposal(08-13 裁定:复盘/反馈流程不得改 `.claude/**`,本稿及后续复盘稿同样受约束)。

## §2 可行性结论(外网可证 / 需探针两栏分开)

| 入口 | 已证实 | 未证实(探针定) |
|---|---|---|
| 中国结算 App(官方跨券商) | 实名认证 → 选证券账户 → 申请导出 → **电子对账单发到邮箱**;可导**最近 3 年**;一个入口覆盖两户 | 文件格式(PDF/Excel)、是否含成交价与费用明细 |
| 国泰海通君弘 App | 「交易 → 更多 → 综合查询 → 对账单 → 导出 PDF」 | 逐笔「交割单」入口与导出格式(Excel/邮箱)、可查年限 |
| 太平洋证券 App(原太牛,2024-06 更名) | 无官方说明;Mac App Store 有官方通达信 Mac 客户端 v8.07 | App 内交割单能否导出/发邮箱;Mac 客户端能否输出文件 |
| 程序化接口(QMT/PTrade/XTP) | 无权限、Windows 为主、且**一般只给当日成交** | —— 本稿划掉 |
| GUI 自动化(easytrader 类) | Windows only、脆、违反条款 | —— 本稿划掉 |

**自动化天花板**(用户已认可):导出这一下不可自动(无 API、登录要短信)。可自动的是
「文件落到 Mac 之后的一切」。

## §3 路线裁定

- **A · 中国结算一处取两户** = 候选主干:单解析器、双户、3 年回填、邮件投递(将来可接 Gmail MCP)。
- **B · 券商各导交割单** = 补费用明细(佣金/印花税/过户费/成交编号)+ 与 A 跨源核对。
- **C · 截图 → 本 session 读图** = 兜底(太平洋若导不出);零解析器,靠契约兜住识读错。
- **探针先行**(§10):A 的文件内容没证实之前不写任何解析器,**不凭想象写格式**。

## §4 边界

在本里程碑内:解析 / 标准化 / 契约 / 幂等 / 跨源核对 / 摘要屏 / 测试。
**不在**:复盘指标、与 scan 产物 join、自动改 `pinned.jsonc`、Gmail 自动拉附件、持仓/NAV 曲线、
任何 LLM 调用。这些挂 §14。

## §5 架构

```
autoresearch/broker/                      # 新域包,与 scan/analyze/macro 并列;零 LLM、零网络
  __init__.py
  schema.py        # 标准化成交表列定义 + 行级/文件级契约(复用 data.contracts 的 DataContractError/record_degradation)
  adapters/
    __init__.py    # sniff(): 按 magic bytes + 编码嗅探选 adapter(不信扩展名)
    chinaclear.py  # 中国结算电子对账单 → raw 行
    gtht.py        # 国泰海通君弘 交割单/对账单 → raw 行
    tpy.py         # 太平洋证券 交割单 → raw 行(探针若证明导不出,本文件不建)
    screenshot.py  # 读「Claude 读图后手写的标准 CSV」→ raw 行(与其他源走同一契约)
  ingest.py        # CLI:解析→契约→写 raw/<source_kind>.csv(幂等)→ 重建 trades.csv → 摘要屏 → ingest_log
  reconcile.py     # CLI:跨源核对(只报不裁)

context_<engine>/broker/                  # 已被 .gitignore 的 context_*/ 覆盖;个人财务数据,不进 lake/
  accounts.jsonc   # 股东账号/资金账号 → 别名(tpy|gtht)映射;中国结算文件按此归户
  inbox/           # 原始文件落点(可为指向 iCloud Drive 文件夹的软链);按来源子目录
    chinaclear/  gtht/  tpy/  screenshot/
  raw/<source_kind>.csv   # 每源一张,只追加,行级去重(row_hash)
  trades.csv       # 合并表(每次 ingest 由 raw/* 确定性重建,不手改)
  ingest_log.jsonl # 每次导入留痕:文件 sha256、来源、期间、行数、新增/重复、A 违规、B 降级
```

`workspace.py` 新增 `broker_root() -> context_root() / "broker"`(裸根字面量契约由既有 grep 探针锁)。

数据流:`inbox/<src>/文件` → adapter → raw 行(DataFrame)→ `schema.validate()`(A 级抛/B 级记账)
→ upsert `raw/<src>.csv` → `merge()` 重建 `trades.csv` → 摘要屏 + `ingest_log`。

## §6 标准化成交表(`trades.csv`,一笔一行)

| 列 | 类型 | 说明 |
|---|---|---|
| `account` | str | 别名 `tpy` / `gtht`(**不落真实账号**) |
| `trade_date` | YYYY-MM-DD | 成交日 |
| `trade_time` | HH:MM:SS 或空 | 成交时刻(B 级) |
| `code` | 6 位字符串 | 前导零保留(`zfill(6)`,同 finalists 前科) |
| `ts_code` | str | `symbol_utils.to_ts_code(code)`(单一事实源) |
| `name` | str | 证券名称(B 级) |
| `side` | `BUY` / `SELL` / `OTHER` | 由 `biz_type` 映射;非买卖(红利/配股/申购/中签/转托管/利息/税)一律 `OTHER` **保留不丢**(现金流真相) |
| `biz_type` | str | 源文件业务类型原文 |
| `price` / `qty` / `amount` | float | 成交价 / 成交量(恒正,方向在 side)/ 成交金额(毛) |
| `commission` / `stamp_tax` / `transfer_fee` / `other_fee` | float | 费用四项(B 级;缺则 NaN + 记账) |
| `net_amount` | float | 清算金额,带号:BUY 为负、SELL 为正;源有「发生金额/清算金额」优先取源值 |
| `balance_after` | float | 剩余持仓(B 级;核对用) |
| `trade_id` | str | 源「成交编号」;缺则 `h:` + sha1(`account|date|time|code|side|price|qty|amount|seq`)[:16];合并时主源只有哈希 id 而低优先源带真成交编号 → 取真的 |
| `source_kind` / `source_file` | str | `chinaclear` / `gtht` / `tpy` / `screenshot`;源文件 basename |
| `sources` | str | 合并后来源集合(如 `gtht+chinaclear`) |
| `ingested_at` | ISO | 首次入表时间(合并后取各源最早) |

`seq` = 同一文件内「(account, date, code, side, price, qty) 相同」组的序号(先按 (时刻, 成交编号) 稳定排序再编号,
与文件行序无关)——**同价分笔成交是合法的两行**,不得被去重吃掉;`row_hash` 含 `seq`。

## §7 契约(承接 `data/contracts.py` 两级哲学 + 07-12 用户裁定「为空即抛」)

**文件级 A**:0 行 → `DataContractError`;账户无法归户(中国结算文件里的股东账号不在 `accounts.jsonc`)→ 抛,
明写缺哪个账号;期间无法识别 → 抛。

**行级 A**(违反即整文件拒收,不落 raw):`trade_date` 可解析且不晚于今天;`code` 6 位数字(BUY/SELL 必须;OTHER 行允许空);
任一数值列**非空但不可解析**(`5.0O`/`--`/`¥12`)—— 解析失败不许伪装成「缺」;`side` ∈ 枚举;
BUY/SELL 行 `price>0`、`qty>0`、且 **`|amount − price×qty| ≤ max(1.0 元, 0.5%×amount)`**
——这条专门兜截图识读错位/丢位,也兜 PDF 抽表串列。

**行级 B**(降级 + `record_degradation("broker/<src>", …)` 记账,摘要屏必印):费用四项缺、`trade_time` 缺、
`name` 缺、`balance_after` 缺;`net_amount` 源值与 `amount ± 费用` 偏差 > 1 元(记账,不改源值);
BUY 的 `qty` 非 100 整数倍**只 warn 不拦**(科创板/北交所允许 1 股递增)。

`DataContractError` 不得被吞(既有原则);CLI 退出码非 0。`ingest_log` 的拒收条目只记错误首行 + 问题条数,
**不记逐笔明细**(§13);逐行原因只在 stderr。

## §8 幂等与合并

- **文件幂等**:`sha256` 已在 `ingest_log` → 跳过并打印「已导入」;`--force` 重解析(仍按 row_hash 去重)。
- **行幂等**:`raw/<src>.csv` 以 `row_hash` 为键 upsert;同文件重跑 = 0 新增(验收项)。
- **跨源合并**(`merge()`,每次全量重建 `trades.csv`,确定性):
  - 自然键 = `schema.natural_key`(**merge 与 reconcile 共用同一定义**,不许各写各的):
    `(account, trade_date, code, side, fmt(price), fmt(qty), OTHER 行再加 fmt(amount))`,数值折到 4 位小数
    (adapter 用 amount/qty 反推 price 的浮点噪音不致错配);OTHER 行没有价量,不加 amount 同日两笔现金流
    (利息 1.5 与转入 50000、红利 88 与税 −17.6)会被并成一笔——复核逮到的规格缺陷,已修。**按计数**匹配(multiset,配合 `seq`);
  - 来源优先级 **券商交割单(gtht/tpy) > chinaclear > screenshot**:高优先源提供 `trade_id` 与主值,
    低优先源只**补缺**(如 chinaclear 无费用、券商有,取券商;反之亦然);
  - 只在一个源出现的行照常入表,`sources` 标单源。
- **跨源核对**(`reconcile.py`,只报不裁):对每个账户 × 两源共同覆盖的期间,输出
  「两边都有 / 仅 A / 仅 B」三桶清单 + 金额合计差;有差异**不自动裁决**,列出来给人看。
  桶**只计 BUY/SELL**;OTHER 行(红利/税/利息/转账)另起一行按源计数——中国结算结构性没有利息/转账类,
  把它们算进桶只会造出永久噪音。窗口按两源**成交日交集**推定,不是导出覆盖期(adapter 目前不声明覆盖期;
  这意味着一源最后一笔之后另一源的成交落在窗外——报告头一行明写这一点,后续 adapter 能声明覆盖期时再换)。
  这是 A 主干「内容未证实」的长期保险丝:每期都跑,一致率掉了立刻可见。

## §9 各 adapter(格式由探针决定;本节只定**不变的规则**与已知坑)

- **嗅探**(`adapters.sniff`):按 magic bytes 定真身——`PK` → xlsx、`D0 CF 11 E0` → xls、`%PDF` → pdf、
  `<html`/`<table` → HTML 表、其余按分隔文本;编码依次试 `utf-8-sig` → `gb18030`。
  已知坑:券商「xls」常是 **GBK 制表符文本或 HTML**,扩展名撒谎。
- **依赖**:`pyproject` 现只有 pandas。**探针证明哪种格式真实存在才加哪个**(xlsx→`openpyxl`,
  xls→`xlrd`,pdf→`pdfplumber`);不预加。
- **PDF 密码**(中国结算/券商对账单常用身份证后 6 位):从 env `BROKER_PDF_PASSWORD`(`.env` 已 gitignore)读,
  **永不写日志、永不进 ingest_log**;缺失且 PDF 加密 → 抛,提示设 env。
- **chinaclear**:一个文件可能含两户 → 按股东账号经 `accounts.jsonc` 归户(§7 文件级 A)。
- **screenshot(读图契约)**:Claude 在 session 内读截图后,写 `inbox/screenshot/<account>_<起>-<止>.csv`,
  表头**固定**为 `trade_date,trade_time,code,name,biz_type,price,qty,amount,commission,stamp_tax,transfer_fee,other_fee,net_amount,balance_after`
  (缺的列留空),之后与其他源走**完全相同**的契约与合并;`source_kind=screenshot`,合并优先级最低。
  读图不是「自动」,但它让「太平洋导不出」不阻塞整条线。

## §10 探针协议(设计评审通过后、写实施计划之前执行)

用户侧(三份样本,放同一文件夹,告诉我路径;建议 iCloud Drive `broker-inbox/`,手机分享面板「存储到文件」即可):

1. **中国结算 App**:实名认证 → 分别选**两个**证券账户 → 申请导出 → 邮箱收附件 → 存入 `chinaclear/`
   (PDF 若加密,不要把密码发到对话里,写进 `.env` 的 `BROKER_PDF_PASSWORD`)。
2. **君弘 App**:交易 → 更多 → 综合查询 → 优先找**交割单**(选最长期间,导出 Excel / 发邮箱);
   若只有对账单 PDF,也要一份 → `gtht/`。
3. **太平洋证券 App**:交易 → 查询 → 交割单/历史成交 → 看有无「导出 / 发送邮箱」;有则 → `tpy/`;
   没有则**逐页全屏截图(含表头)** → `screenshot/`。可选:Mac App Store 官方客户端试「输出」。

我侧(对每份文件记录并追加到本稿 §附录,bump 版本):真身格式与编码、列清单与原文列名、期间与行数、
是否含费用/成交编号/剩余持仓、账户识别字段、PDF 是否加密、
以及**A 主干是否成立**(中国结算文件含成交价与数量 = 成立;只有持仓变动无价格 = A 降为核对源,B 升主干)。
探针结论直接决定 §9 建哪些 adapter、加哪些依赖;**实施计划在探针之后写**。

## §11 CLI 与摘要屏

```
uv run --no-sync python -m autoresearch.broker.ingest <文件|目录> [--account tpy|gtht] [--force] [--dry-run]
uv run --no-sync python -m autoresearch.broker.reconcile [--account …] [--since YYYY-MM-DD]
```

`--account` 仅对券商源必填(文件本身不带券商身份时);中国结算源按 `accounts.jsonc` 自动归户。
**`--account` 与文件名自带的账户冲突 → 该文件拒收**(不是覆盖:覆盖会把另一户的成交改名后被 row_hash 去重吞掉,
复核逮到的静默丢数据路径)。整批先定来源再动文件:来源不明/无 adapter 退 2 且一个字节不写;坏文件(空/解析失败)
按文件拒收、批次继续、`trades.csv` 照常重建。
摘要屏(每次 ingest 末尾,一屏读完):

```
[broker] 文件 3 · 新导入 2 · 已导入跳过 1
  ↷ 已导入 gtht_20260101-20260826.csv
  tpy   2026-06-01..2026-08-26  BUY 41 / SELL 39 / OTHER 3   成交额 1,234,567  费用 1,234  B降级(本次) 2(commission 缺 ×80)
  gtht  2026-01-05..2026-08-26  BUY 12 / SELL 12 / OTHER 1   成交额   345,678  费用   456  B降级(本次) 0
trades.csv 重建:108 行 · 多源匹配 24 行 · 单源 84 行
```

账户行的笔数/期间/成交额是 `trades.csv` 全量,`B降级(本次)` 只算本次导入的文件、**按账户拆分**(中国结算一份文件
含两户时不会把一份的降级算到每个账户头上);不印 `A违规`(能进 trades 的文件按定义 A 违规为 0,拒收数在首行)。
配对明细看 `reconcile`。

```
```

## §12 测试(`tests/broker/`)

- 夹具**全部合成**:列布局照抄探针看到的真实版式,数值假造;真文件不进仓。
- 契约:空文件抛;`amount≠price×qty` 抛且**不落 raw**;费用缺 → 记账且摘要屏可见;
  未知股东账号 → 抛并指名。
- 幂等:同文件两次 ingest → `trades.csv` 字节相同、`ingest_log` 第二次为「跳过」;`--force` 后 0 新增。
- 合并:同价分笔两行不被吃;跨源计数匹配(2 vs 1 → 仅 A 桶 1);优先级补缺方向正确。
- 嗅探:扩展名 `.xls` 内容为 GBK 文本 / HTML 各一例。
- 代码归一:`000001`/`1`/`600519.SS` → `code`/`ts_code`;`workspace` 裸根 grep 探针继续绿。
- 每条用例先问「把实现这段删掉它会红吗」(Wave35 变异教训);不写只锁形状不锁行为的测试。

## §13 隐私

个人财务数据只在 `context_<engine>/broker/`(gitignore)与 `.env`;代码、测试、日志、memory、设计稿
**不出现真实账号、身份证位、金额明细**;`ingest_log` 只记文件哈希、期间与计数,不记任何一笔明细。

## §14 验收(里程碑 1)与后续

验收(真样本):① 每份样本 A 违规 0;② 重跑 0 新增;③ 两源重叠期间 reconcile 一致率打印;
④ 摘要屏一屏读完;⑤ 全仓测试保持绿(现 2974)。

后续(各自另立稿,顺序由用户定):复盘层(行为体检 / 与 scan 结果账本 join / 逐笔叙事);
真实持仓 → `pinned.jsonc` 自动同步;Gmail MCP 自动拉附件(先验证能否取附件);中国结算 3 年回填。

## §附录 探针记录

本稿 v1 不含探针结果;§10 执行后逐文件追加于此(格式/编码/列清单/期间/行数/费用有无/账户字段/主干裁决)。
