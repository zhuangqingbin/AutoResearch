# Wave10 退役审计(B 节)

> 对应 `docs/specs/2026-08-01-wave10-report-ops-slimdown-zerobuy-design.md` §B0–B5。
> B0 协议:①引用分层 → ②查将删 test 是否兼锁活契约 → ③判身份 → ④写本审计 → ⑤删后全测 +
> 相邻活守卫定向变异测试。**每个 family 一个独立 commit。**
> R7:运行时程序只产 `RETIRE_ELIGIBLE`,**不自删源码**;历史 specs 是审计记录,不为通过
> grep 而改写。

## 身份判据

| 身份 | 含义 | 处置 |
|---|---|---|
| `DEAD` | 零调用点,且从未接线 | 立即删 |
| `ABANDONED` | 曾接线、现已退役且不会回来 | 立即删 |
| `ROLLBACK_LEVER` | 新路的回滚开关,新路尚未攒够稳定证据 | 只标 `RETIRE_ELIGIBLE` 条件,不删 |
| `LIVE` | 仍有真消费者 | 保留(必要时补测试锁住) |

---

## B1 · 零调用残件

### B1-a `autoresearch/scan/l4_reuse.py` —— `ABANDONED`

**引用分层**(2026-08-01 实测):

| 层 | 命中 | 性质 |
|---|---|---|
| 生产 py | `sector/reuse.py:2`、`self_review.py:453`、`feedback_store.py:473` | **全是注释/docstring 提及**,零 import、零调用 |
| workflow | `scan-market.js:268` | 历史注释:「TTL 复用(l4_reuse --apply)已于 2026-07-29 退役(用户裁定 R5「不要任何复用」)」 |
| tests | `tests/scan/test_l4_reuse.py`(13 例) | 见下 |
| active docs | 无 | — |
| historical docs | 17 份 | `HISTORICAL_REFERENCE`,不动 |

**身份依据**:用户 2026-07-29 裁定「不要任何复用」,生产调用当日退役;`reuse_decision` /
`reuse_pass` / `write_reused_card` 三个公开函数今日零调用点。

⚠️ **易混淆**:`autoresearch/sector/reuse.py` 是**行业 brief 的 TTL 复用**,与本项无关,
且由 `scan-market.js:168` 真调用 —— **LIVE,不得连坐**。

**②查 test 双职 —— 逮到一个**:
`test_read_finalists_preserves_ticker_leading_zeros` 直接锁 `autoresearch/scan/artifacts.py`
的 `read_finalists` 前导零契约(该文件第 111 行的注释自己写明了这一点),而它是**全仓唯一**
锁这个契约的测试。这正是 [[deadcode-cleanup-wave-20260719]] 的教训:删退役特性的 test 会
静默孤立它「顺带」锁的 live 契约。**处置:先迁移该用例,再删原文件。**

其余 12 例只测 reuse 决策逻辑;`〔卡契约 v3〕` 样本串另有 10 个测试文件锁着,不构成双职。

**顺带记账(不在本波处置)**:`read_finalists` 今日在生产侧**也没有调用者**(只有定义)。
它可能本身就是死码,但判它需要另一轮溯源(前导零那一刀当年修的是 assemble 误判缺卡,
现行链路是否改走别处未查)。本波只保留契约锁,标 `RETIRE_ELIGIBLE(待溯源)`。

### B1-b `reuse` config 白名单 —— `ABANDONED`

`user_config.py:81/87/154` 的 `reuse` 键与 `{max_age_days, price_delta_pct}` 嵌套白名单
只服务已退役的 L4 卡复用;`scan_config.jsonc:80` 已是注释掉的示例行。随 B1-a 同 commit 删。

### B1-c `redteam_prob` —— `DEAD`

| 位置 | 性质 |
|---|---|
| `scan/config.py:38` | ScanConfig 字段定义 |
| `scan/user_config.py:81/142/154` | 白名单 + 注释 |
| `scan_config.jsonc:79` | 已注释掉的示例行 |
| tests ×3 | 只做**配置往返**断言(hash 稳定性 / echo / 合并),不测任何行为 |

**零消费者**:全仓无一处读 `sc.redteam_prob`。删字段 + 白名单 + active 注释;
测试里把它换成另一个仍存在的字段(往返契约本身要留,换个载体即可)。

### B1-d `pinned.cap` / `pinned.ttl_days` —— `LIVE`(撤回首稿删除提议)

`frame.py:229-230` 真读它们并烤进 run contract(`pinned_cap` / `pinned_ttl_days`)。
设计稿 §8 已据 premise-check 撤回删除,本波**保留并补消费链测试**。

### B1-e OTEL 注释残迹 —— 只清 active 注释

历史设计文档不动(`HISTORICAL_REFERENCE`)。

---

## B2 · 观察单残件族

第一交付物是**逐文件身份分类**(`DEAD_READ` / `BACKWARD_COMPAT` / `LIVE_OTHER_PURPOSE`),
不是先删。退役令 fb_20260714_002 不变;journal 恒 0 的「触发」列从新报告 schema 删除,
历史 CSV/Markdown reader 继续兼容旧列、**不回写历史**。

_(待填:分类表)_

---

## B3 · `earlystop_shadow` 整族

_(待填:引用分层 + producer 接线核验)_

---

## B4 · 未启用路径身份重判

_(待填:`stable_context_blocks` 离线 benchmark;`sector_brief_mode=finalist_only` 的
research experiment 归属)_

---

## B5 · 回滚杆到期状态

| 回滚杆 | `RETIRE_ELIGIBLE` 判据 | 当前进度 |
|---|---|---|
| `streaming_l4=false` 旧批量 GATE3 | 流式路径累计 10 次真实扫描且 `structural_failure_n=0` | _(待测)_ |
| `_ensemble.json` 旧批量双读 | 与旧批量路径同生死 | 同上 |
| abstention v1 | v2 达 10 个 mature day 且两日重跑一致 | v2 现 8 个 mature day |

代码不得在第 10 日 assemble 后自行修改/删除源码(R7)。
