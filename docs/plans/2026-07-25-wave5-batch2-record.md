# Wave5 批 2 实施记录(宏观接线 + macro_state 解耦 + token 真计量)

> 2026-07-25 实跑。设计依据 `docs/specs/2026-07-25-scan-wave5-live-mainruler-macro-metering-design.md` 章 ③、④A。
> 批 2 未单独写 plan——批 1 的 plan 已验证工作流有效,批 2 直接按 spec 逐项 TDD 实施并记录于此。

全量 **1602 passed**(批 1 结束时 1571 + 本批新增 31)。

| commit | 内容 |
|---|---|
| `37d312d` | market_pack 接入北向/两融/行业资金/指数估值分位 |
| `154c8f9` | macro_state 从 spine 解耦 + 新鲜度进汇总屏 + 资金面消费契约 |
| `73749b1` | usage_harvest 真 token 计量(去重 + 计价倍率加权) |
| `ebc8ff0` | SKILL CP7 接线 usage_harvest |

## ③A market_pack 扩容

四个端点(`moneyflow_hsgt` / `margin` / `moneyflow_ind_ths` / `index_dailybasic`)此前只活在 macro full 里,而且那边的函数**直接返回 markdown 字符串** —— scan 想用其中的数字只能重新实现一遍。抽出结构化取数层 `autoresearch/data/macro_cn.py`,macro 侧四个渲染器改成薄壳共用同一事实源;取数落 `_macro_cn.json`,两个 pack 入口(帧 / staging)presence-gated 读。

**真数据首读(2026-07-24,零降级,四端点权限齐全)**:

| 指标 | 读数 |
|---|---|
| 北向 最新 / 5日累计 | +28.4 亿 / **+183.6 亿** |
| 两融余额 / 5日变动 | 26804 亿 / **−1485.8 亿** |
| 行业资金 top | 半导体 **+102 亿**(第二名电子化学品仅 +10 亿) |
| 指数 PE(近1年分位) | 上证 16.4(**28%**)· 沪深300 14.4(73%)· 中证500 35.6(58%)· 创业板 44.4(**76%**) |

**旧 pack 完全看不见的东西**:外资 5 日净买 184 亿的同时杠杆资金撤了 1486 亿(方向相反);上证在低估区而创业板在高估区(哑铃两端的估值证据)。这类背离正是「市场结构」小节该说而过去说不出的内容 —— 所以同批给 macro-brief 与 playbook 加了消费契约(pack 有 `cross_money`/`index_val` 时第 2 节必引),防新数据白接。

## ③B macro_state:根因不是 bug,是门设错了位置

侦察报告说「macro full 骨架齐全但从未跑通」。读代码后发现更准确的诊断:

- `write_macro_state` 的**唯一硬依赖是 `1_spine/decision.md`**(sector_map / premortem 都是 best-effort);
- 但它**只在 `assemble.main()` 里被调用**,而 assemble 要求 **~20 个分段文件齐全**才肯往下走;
- 于是「下游 lite 真正消费的那个机读产物」被「整份 20 节报告」这道最贵的门扣着 —— 一个月没人跑。

修法是解耦而不是修 bug:`python -m autoresearch.macro.state <dir>` 只要 `decision.md` 就能落 `macro_state.json`,缺它则明确报 MISSING。这样每周补齐只需**一个 agent 写一节**,而不是 20 节。同时把新鲜度放进 prelude 汇总屏(`宏观 full 摘要:✓/✗ …`)—— 恒缺一个月而无人察觉,本身就是没有仪表的后果。

## ④A token 真计量:两个必须守住的点

Spike 成功:subagent transcript 每条 assistant 消息自带 `message.usage`,含 `cache_read_input_tokens` / `cache_creation_input_tokens`,还带 `attributionAgent` / `effort` / `model`。OTEL 那条路不必再等。

**1) 去重是硬要求。** 流式更新让同一 `message.id` 的 usage 重复落多行 —— 实测一个 Explore agent **109 行 usage / 49 条唯一 id**,直接求和把 cache_read 从 4.81M 虚报成 9.83M(整整一倍)。按 id 分组取最后一条。

**2) 必须按计价倍率加权。** cache读 ×0.1、5m写 ×1.25、1h写 ×2(transcript 分开记了 5m/1h,不能混算)。本 session 4 个侦察 agent:

- 原始输入 **10.42M** → 加权 **1.78M**(cache 命中率 93.9%,成本被压掉 83%)
- 单个 Explore agent:cache读 4.81M + cache写 283k + 生输入 0.1k = 原始 5.09M → 加权 **835k**

对照:`assemble` 的 bytes÷2.8 估算说整场扫描 ~154k,项目自估 ~1M。**4 个侦察 agent 的加权量就已经是自估全场的 1.8 倍** —— 「先仪表化再精准砍」这个基调是对的,按旧估算去砍会砍错地方。

## 与 spec 的偏差(两处)

1. **spec 写「四函数公共化」,实际是「抽出结构化取数层 + 渲染器改薄壳」**:原函数返回 markdown,直接搬过来 scan 用不了数字。
2. **spec 的 ③B 写「跑一次 harvest+assemble 修断点」,实际发现没有断点可修** —— assemble 本身是好的,病在 macro_state 被它的 20 文件门扣着。改为解耦 CLI,比原方案便宜得多(每周一个 agent 而非二十个)。

## 顺带记下的两个观察(未改,留给④C)

- **`_ts_call` 的退避不区分可重试与不可重试**:「没有权限 / 参数错」这类重试必然再失败的错误也照睡 4 次共 ~9 秒。真扫描里每个无权限端点都在白等。改重试分类要单独评估,别顺手动。
- **`frame.py` 的 stdout 是 `market_pack.json` 的 payload**(`--json > market_pack.json`),往那里打任何计数行都会毁掉 JSON。批 1 已加反向测试钉死。

## 剩余

- ③ 的两周验收:market_view 开篇不再写「无新鲜宏观视图」——需要先跑一次 macro full 的 LLM 节产出 `decision.md`(下 session,agent def 已改)。
- ④A 的首次真实扫描读数:CP7 跑 `usage_harvest --out reports/scan/<run>/token_usage.md`,拿到真分布后才评审 ④C 第二刀。
- 批 3(ic_by_regime 裁决 + 板块动量 replay)未动。
