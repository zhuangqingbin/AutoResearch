# Wave12 T1 · 热度榜端点活体探针(东财人气 / 雪球关注)

日期:2026-08-09。akshare 版本:`1.18.64`(`uv run --no-sync python -c "import akshare; print(akshare.__version__)"`)。

探针脚本(scratch,不入库):`/private/tmp/claude-503/.../scratchpad/probe_hot_rank.py`,
依次实调 4 个候选端点,记录真实返回。**结论:两源均可用,不触发 BLOCKED_BY_DATA。**

## 结论表

| 函数 | 行数 | 关键列非空率 | 日期语义 | 裁定 |
|---|---|---|---|---|
| `stock_hot_rank_em()` | 100 | 全部列 100% | 快照(当前时刻榜单,无历史参数) | **可用**——东财人气榜,全市场 TOP100 快照,零参数调用 |
| `stock_hot_follow_xq(symbol="最热门")` | 5619 | 股票代码/简称/最新价 100%,关注 99.9% | 快照(累计关注数,无历史参数) | **可用**——雪球全市场关注度快照(`symbol` 是分类选择器,默认值即目标分类,非个股代码) |
| `stock_hot_rank_detail_em(symbol=...)` | 366(单只股票 366 天) | 时间/排名/证券代码 100%,新晋/铁杆粉丝 99.7% | **历史**(该股逐日排名序列,366 天) | 不适用——个股维度(entity-scoped)钻取接口,非全市场快照;覆盖全市场需 ~5,400 次调用,不满足夜间一次性采集目标 |
| `stock_hot_rank_latest_em(symbol=...)` | 10 | 全部列 100% | 快照(单只股票的排名元数据,含 `calcTime`) | 不适用——**不是榜单**,是单只股票的 key-value 元数据查询(`item`/`value` 两列,10 个字段:`marketType`/`marketAllCount`/`calcTime`/`innerCode`/`srcSecurityCode`/`rank`/`rankChange`/`hisRankChange`/`hisRankChange_rank`/`flag`);函数名"latest"具有误导性,`symbol` 是**必需的个股定位参数**,不接受空调用 |

**裁定:两源(东财人气榜 / 雪球关注度)均可用,不进入 BLOCKED_BY_DATA 分支。** T2/T3 按计划实施。

## 逐源详情

### 1. `stock_hot_rank_em()` —— 东财人气榜(采用)

零参数,~1 秒返回。真实样本:

```
   当前排名        代码  股票名称     最新价        涨跌额    涨跌幅
0     1  SH603259  药明康德  154.82  13.144218   8.49
1     2  SZ301308   江波龙  386.60  21.378980   5.53
2     3  SH600664  哈药股份    6.84   0.681948   9.97
```

列:`当前排名`(int64)、`代码`(object,**前缀式** `SH`/`SZ` + 6 位数字,如 `SH603259`——**不是**
tushare 后缀式 `603259.SH`,也不是裸 6 位数字)、`股票名称`、`最新价`、`涨跌额`、`涨跌幅`。全列 100% 非空。

日期语义:**纯快照**——接口不接受任何日期/历史参数,只返回"此刻"的 TOP100 榜单。今晚不采,
今晚这份榜单就永久丢失(不能像 `daily` 那样事后用 `trade_date` 参数回补)。这正是本任务排最优先的
唯一理由。

### 2. `stock_hot_follow_xq(symbol="最热门")` —— 雪球关注度(采用)

`symbol="最热门"` 是**分类选择器**(默认值本身就是目标分类,不是个股代码),调用耗时 ~13 秒
(内部分页 29 次,`tqdm` 进度条写 stderr)。真实样本:

```
       股票代码  股票简称         关注      最新价
0  SH600519  贵州茅台  3696076.0  1309.22
1  SH601318  中国平安  3139999.0    53.38
```

列:`股票代码`(前缀式,同上)、`股票简称`、`关注`(float64,**累计关注数快照**,不是"今日新增")、
`最新价`。5619 行≈覆盖全市场,按关注数降序排列(未显式给排名列,但顺序本身即排名,行位置可当秩)。

**与任务书 Interfaces 的偏差**:任务书猜测的列名 `hot_follow_xq(date, code, follow_delta?, …)`
里的 `follow_delta` **不存在**——`关注` 是累计总数快照,不是增量。若未来要做"关注度环比变化"的
消费者,需要靠本湖积累两天快照后**自己做差**,不是端点原生字段。已如实记入下方"移交 T2 的设计决策"。

日期语义:同样纯快照,无历史参数。

### 3. `stock_hot_rank_detail_em(symbol="SZ000665")` —— 不适用(个股钻取)

需要个股 `symbol`,返回**该股**过去 366 天的逐日排名序列(`时间`/`排名`/`证券代码`/`新晋粉丝`/
`铁杆粉丝`)。这其实是一个"单票排名历史"接口——**它自己就有历史**,但代价是必须逐票调用
(全市场覆盖需要 ~5,400 次请求),与本任务"夜间一次性采集全市场快照"的目标不匹配,不采用。
(留作未来"个股维度深挖"候选,不在本次 T2/T3 范围。)

### 4. `stock_hot_rank_latest_em(symbol="SZ000665")` —— 不适用(非榜单,是元数据查询)

任务书按函数名猜测它可能是"最新（全市场）榜单",实测**并非如此**——它同样需要 `symbol`,
返回该股的 10 个 key-value 元数据字段(`marketType`/`marketAllCount`/`calcTime`/`innerCode`/
`srcSecurityCode`/`rank`/`rankChange`/`hisRankChange`/`hisRankChange_rank`/`flag`),本质是
"这只股票现在排第几、比昨天变了多少"的单票查询,不是可回填的市场快照榜单。不采用。

## 移交 T2 的设计决策(premise-check 发现,写清差异)

1. **端点注册键必须是 akshare 真实函数名**,不是任务书里写的概念名 `hot_rank_em`/`hot_follow_xq`。
   `autoresearch/data/sources/__init__.py:_fetch_akshare` 用 `getattr(ak, endpoint)` 路由,
   endpoint 字符串必须逐字节等于 `ak.` 后的函数名——即 `"stock_hot_rank_em"` /
   `"stock_hot_follow_xq"`,与 `stock_news_em`/`stock_lhb_stock_statistic_em` 等既有登记同一约定。
   这也自然成为 lake 目录名(`context/lake/stock_hot_rank_em/`)。

2. **`stock_news_em` 先例里藏着一个未触发的活 bug,不能照抄"把 `as_of` 塞进 params"这个写法**:
   `tripwire_watch.py:107` 调 `get_or_fetch("stock_news_em", {"symbol": code6, "as_of": ...})`——
   但 `ak.stock_news_em` 的真实签名是 `(symbol: str = '603777')`,**不接受 `as_of`**。
   `cache._lake_params` 只剥 `fields`,`as_of` 会原样透传进 `_fetch_akshare` 的 `fn(**params)`,
   实测直接 `TypeError: stock_news_em() got an unexpected keyword argument 'as_of'`
   (已用 `ak.stock_news_em(symbol='000012', as_of='20260625')` 复现,见下方"实测复现"块)。
   这条路径至今没被真正测出来,是因为它唯一的调用点 `_news_titles()` 外层包了
   `except Exception: return None`,且工作区已有 1,891 个历史 parquet(`.../000012@20260625.parquet`
   等)大概率命中湖(`path.exists()` 分支不会再调用 `fetch`)——一旦撞上真正的湖未命中(新代码/
   新日期组合),这条路会静默退化成"查不到新闻",而不是报错。**这不是本 task 的改动范围**
   (`tripwire_watch.py` 不在 T1/T2/T3 文件清单内),如实记录,不顺手修。

   对 T2 的直接影响:两个新端点的 `get_or_fetch` 调用**只用空 `params={}`**——
   `stock_hot_rank_em()` 本就零参数;`stock_hot_follow_xq()` 的 `symbol` 默认值已是目标分类
   `"最热门"`,不传等价于传。`as_of`/`entity` 缓存键改用 `_cache_key` 的原生兜底
   (`entity` 缺省 `"all"`,`as_of` 缺省 `today` 参数)推导,不额外塞任何键——从根上避免同类
   "为了拼缓存键而污染真实 API 调用参数"的 bug。

3. **契约 key 模式选 `as_of` 不选 `date`**:`stock_hot_rank_em`/`stock_hot_follow_xq` 的原始返回
   都不带日期列,若用 `key="date"` 且 `params` 里没有 `trade_date`/`date` 等键,
   `cache._cache_key` 会退化成字面量 `"unkeyed"`——每晚都覆写同一个文件,历史全部丢失,
   与本任务"把快照按天留底"的目标背道而驰。`as_of` 模式的原生兜底(空 `params` → `all@<today>`)
   才是正确的按天分区键,与 `stock_news_em` 的 `{entity}@{as_of}` 同一族。

4. **原始列名保持不译**:`stock_news_em` 落湖的真实 parquet(`context/lake/stock_news_em/
   000012@20260625.parquet`)列名是原始中文(`关键词`/`新闻标题`/…),未做任何改名/结构化——
   本次两个新端点沿用同一惯例,不重命名 `代码`→`code`、不注入额外 `date` 列(日期由文件名
   `all@YYYYMMDD.parquet` 承载,与 `stock_news_em` 的 `{code}@{date}.parquet` 同一约定)。
   这样"写湖一律剥 fields"（多列无害少列是灾难)与"历史产物不改写"两条纪律都不会因为一次
   自作主张的 schema 设计而被破坏。

5. **主尺绑定条款(Global Constraints 第 1 条)不适用于本任务**:T1-T3 只做原始快照入湖,
   不产出任何进入 composite_score / 决策卡的"读数列",没有新列需要绑定
   `gap_c1_o2`/`rel_gap_market`/`rel_gap_sector`。留痕说明,不是遗漏。

6. **T3 的夜间挂点核实**:任务书原写"与 `limit_list_d` 预热同位"。实测 `limit_list_d` 的夜间
   预热路径是 `prewarm.py::_temperature` → `temperature.rollup` → `tushare_source.fetch_limit_list_d`
   → `cache.get_or_fetch("limit_list_d", ...)`,即 **`autoresearch/scan/prewarm.py`**;
   `autoresearch/learning/nightly_runner.py` 全文 grep 零 `limit_list_d` 命中,它只是
   `nightly_close.run()` 外面的锁/心跳/幂等加固层,自己不发起任何取数调用。premise-check
   结论:T3 的采集步骤挂 `prewarm.py`,不挂 `nightly_runner.py`——与 Global Constraints
   "prewarm.py 与 nightly_runner.py 二选一"条款一致,选 prewarm.py。

7. **`prelude.py` "B 级降级汇总行已有机制"的准确含义**:`prelude.py` 本体**没有**调用
   `contracts.render()`/`contracts.degradations()` 的通用降级渲染(那条机制只接在
   `universe.run()` → `degraded.json` → `assemble._degraded_line()` 这条**报告生成**链路上,
   `prelude.py` 自己不产报告)。而且 `prewarm.py` 与 `prelude.py`/`universe.py` 是**不同进程**
   (夜间 19:30 vs 次日开扫),`contracts._DEGRADED` 是进程内模块级列表,夜间进程记的账无法
   穿透到次日 `universe.run()` 那次全新进程里。真正"已有机制"是 `prelude.py::prewarm_line()`
   ——它读 `_prewarm.json` 的存在性/mtime,渲染进 `render_summary()` 那屏(唯一穿透进程边界
   的介质,因为它是**落盘文件**)。T3 的"纳入"因此落在:①`prewarm.py` 新增步骤把两源的
   成功/失败结果写进 `_prewarm.json` 的 `steps[]`(复用既有 `_step()` 包装,零新增机制);
   ②`prewarm_line()` 读该 step 的 `note`,失败时追加告警片段。这是对任务书"已有机制"表述的
   精确化,不是另起炉灶。

## 实测复现(`stock_news_em` as_of 泄漏,佐证决策 2)

```
$ uv run --no-sync python -c "
import akshare as ak
ak.stock_news_em(symbol='000012', as_of='20260625')
"
TypeError: stock_news_em() got an unexpected keyword argument 'as_of'
```

## 探针脚本输出全文(节选,完整见 scratch 脚本注释里的调用清单)

见本报告"逐源详情"节内嵌样本;完整 stdout 已在 T1 commit 前人工核对,不额外落盘(scratch 探针
按纪律不入库)。
