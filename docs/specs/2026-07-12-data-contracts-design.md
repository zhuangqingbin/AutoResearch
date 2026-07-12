# 数据契约(data contracts)· 设计稿

> 日期:2026-07-12。来源:用户裁定——"**为什么会有数据为空?取数以后要有一个全面校验,为空的时候要抛出异常阻断**"。
> 状态:**已实施**(`autoresearch/data/contracts.py`,1382 测试绿,真数据正/负向验证均通过)。
> 触发事件:漏斗回放器 M1 对拍逮到的 lake 窄表毒化事故(见 `2026-07-12-funnel-replay-l35-removal-design.md` §2.4)。

---

## 1. 为什么会有"空数据":三条来路,一个下水道

底层其实是对的:`tushare_source._ts_call` 重试 4 次后 **`raise last`** —— 取数失败会抛异常。病全在**上一层**。

| # | 来路 | 实例 | 性质 |
|---|---|---|---|
| ① | **调用方把异常吞成空** | `_harvest_vol_series` 失败 → 返回空帧;`_fetch_hk_hold` 失败 → `None`;`tushare_enrich`/`keyless`/`handler` 另有 8 处 | 🚨 真病 |
| ② | **缓存把"空"永久钉死** | `cache._atomic_write` 空帧也写("存在==取过且为空")→ 一次失败拉到的空入湖后**永不重拉**(实测:`hk_hold` 14/419 空、`stk_surv` 2/12);同族=窄表毒化、factor_lab 空 pickle | 🚨 真病 |
| ③ | **真实的空** | 无北向额度的日子 `hk_hold` 就是空;某日无公告 → `forecast`/`anns_d` 空 | ✅ 合法 |

**而这三条空最后都汇入同一个下水道** —— `scoring.composite_score`:

```python
comp  += (s - 0.5).fillna(0.0) * w           # 某组全 NaN → 贡献 0
wabs  += s.notna().astype(float) * w.abs()   # 该组从分母里消失
raw    = comp / wabs.replace(0, np.nan)      # 其余组权重被自动放大
```

**某个因子组整组死掉,composite 照样输出一个 0–100 的漂亮分数**,漏斗照常跑完、退出码 0。2026-07-12 实证:volprice 组因 lake 毒化而整组 NaN → 全市场打分失真 **98.8%**、L2 名单 jaccard 掉到 **0.36**,唯一的信号是一行淹没在日志里的 warn。

> **核心诊断:系统有降级能力,但没有"我降级了"的传达能力。** 真正要修的不是"有降级",而是"降级是隐形的"。

## 2. 为什么不是"见空就抛"

无差别抛异常会打断两类合法路径:①真实的空(上表 ③);②presence-gated 的增强端点(质押/席位/调研/一致预期缺失时漏斗仍成立——那是设计)。故**分级**:

| 级别 | 端点 | 空/残缺时 |
|---|---|---|
| **A(地基)** | `daily` / `daily_basic` / `moneyflow` / `cyq_perf` / `stk_factor_pro` / `stock_basic` / `trade_cal` | **`DataContractError` 阻断**,且**拒绝入湖**(脏数据一旦落盘就被钉死,重跑也自愈不了) |
| **B(增强)** | 北向 / 两融 / 龙虎榜 / 公告 / 质押 / 新闻 / 宏观 | 降级 + **记账**(`degradations()`),不阻断 |

哲学承接既有的 `assert_tushare_ready`(「空结果 → 抛错中止,不静默跑残缺」),把它从"3 个端点的发布就绪探测"推广成"每个端点、每次取数的内容契约",并补上它管不到的两处:**取数后的内容**(行数/列)与**湖命中路径**。

## 3. 校验挂在哪(三条路径 + 两道出口门)

1. **取数后、写湖前**(`cache.get_or_fetch`)→ A 级违约 → 抛 + **不写湖**。
2. **湖命中后**(同上)→ **原设计的盲区**:历史脏数据(空帧/窄表)读出来照样毒化下游,且**不会**再经过取数路径的任何检查。
3. **未结算日**(date≥today,不入湖)→ **只查空、不查列**(`cols=False`):这份数据不持久化、只服务当次调用,调用方要哪几列是它自己的事(温度计只要 `ts_code,pct_chg`)。但**空仍要抛**(数据没发布,下游必残废)。
4. **因子帧出口门**(`check_market_frame`,接在 `build_market_frame` 末尾)——最后一道防线:前面每道校验都可能被新的 `try/except`、新的取数路径绕过,但**打分帧本身残缺就是残缺**。查 A 级列 + 整列全 NaN。
5. **`_harvest_vol_series` 失败即抛**(不再静默返回空帧)——它是 volprice 组的唯一来源。

**契约异常不得被吞**:`DataContractError` 在任何 `except Exception` 处都必须 re-raise(已修 `frame.py`、`temperature.py`——负向验证时逮到温度计还在吞它;`l3_news`/`l3_catalyst`/`keyless` 消费的是 B 级端点,契约本就不对它们抛,其 except 是正确的 presence-gated 设计,不动)。

## 4. 规模性 vs 结构性(测试隔离的边界)

- **结构性**(空帧 / 缺列 / 整列全 NaN)= 无论数据规模都成立的 bug,**永远启用**。窄表毒化的签名恰恰是"行数够、但缺 high/low/amount"。
- **规模性**(行数腰斩线 3000)= 只在"我以为拉的是全市场"时才有意义。单测用合成小 fixture(几十~几百只)是常态 → `tests/conftest.py` 全局关掉 `CHECK_ROWS`(golden parity 的 600 只帧就是被它误伤的);契约自身的行数逻辑由 `tests/data/test_contracts.py` 显式打开开关来测。

两者性质不同,**别合并成一个开关**——否则关掉规模检查时会把真正的病一起放行(已有测试锁死这条边界)。

## 5. 湖体检 CLI

```bash
uv run --no-sync python -m autoresearch.data.contracts doctor          # 列出违约(A 级毒源 / B 级空帧 / 坏文件)
uv run --no-sync python -m autoresearch.data.contracts doctor --purge  # 删掉 A 级毒源与坏文件 → 下次取数重拉
```

B 级空帧**不删**(多为真实的空,删了只会每天重拉一次空)。

## 6. 验收(2026-07-12 已完成)

- **单测**:23 条新测试;全量 **1382 passed**。
- **真湖体检**:4000+ 个 parquet → A 级违约 **0**、B 级空帧 16(`hk_hold` 14 + `stk_surv` 2,全是真实的空)、坏文件 0 —— **零误报**。
- **正向(真数据端到端)**:回放 2026-07-07 全链路跑通(L2=204 / winners=552,与修复前一致),无契约误报、无降级记录。
- **负向(注入毒化)**:把 `daily/20260707.parquet` 改成窄表 → ① `doctor` 当场检出(`缺列 ['amount','close','high','low','open']`);② 生产路径(回放)**被阻断**并给出自愈路径。**同一个事故,修复前是"静默失真 98.8% + 退出码 0",现在是当场炸 + 告诉你怎么修。**

## 7. 开放线头

- `handler.py` / `tushare_enrich.py` / `keyless.py` 里仍有 ~8 处 `except → 返回空`,消费的都是 B 级端点(契约不对它们抛),但它们**自己**没有记账 —— 下一波可把这些降级也接进 `degradations()`,让 B 级降级的可见性覆盖到最后一公里。
- `degradations()` 目前是进程级记账,尚未落盘进 `meta.json` / 报告(`render()` 已就绪,待接 `universe.run` 与 assemble 的报告头)。
