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
不是先删。退役令 fb_20260714_002 不变。

### 前提核验(三条,全部实测)

| 问题 | 实测 |
|---|---|
| 还有谁产生 `lane=watchlist_trigger`? | **零生产者**;历史 `finalists.csv` 里也**零命中** |
| `context/watchlist.csv` 还活着吗? | **活着**:文件在(2026-07-06),`sector/pack.py:129` 真读它当行业选择器 |
| journal 的「触发」列真恒 0 吗? | 30 个扫描日:非空 10 行、**非零 0 行、累计 0**;`watchlist_status.csv` 仍留在 10 个历史扫描日里 |

### 逐文件分类

| 文件 : 位置 | 用法 | 身份 | 动作 |
|---|---|---|---|
| `learning/journal.py:75` | 读 `watchlist_status.csv` 数「触发」 | **`DEAD_READ`** | **删**列 + 读取 |
| `scan/gates.py:32,70,76` | `_EXEMPT_LANES` 含 `watchlist_trigger` | `DEAD_READ`(残迹) | **不删**,见下 |
| `learning/retro.py:712` | 真选口径排除该 lane | `DEAD_READ`(残迹) | 不删 |
| `learning/self_review.py:367,411` | 保送§2 空检查豁免该 lane | `DEAD_READ`(残迹) | 不删 |
| `learning/t1_review.py:41` | `_NON_GENUINE_LANES` | `DEAD_READ`(残迹) | 不删 |
| `learning/stage_eval.py:249` | 同上 | `DEAD_READ`(残迹) | 不删 |
| `sector/pack.py:7,129` | 读 `context/watchlist.csv` 选行业 | **`LIVE_OTHER_PURPOSE`** | **不动** |
| `learning/feedback_store.py:245` | `_MOOT_TERMS` 含「观察单/watchlist」= 检测引用已退役机制的 lesson | **`LIVE_OTHER_PURPOSE`** | **不动** |
| `scan/menu.py:4`、`learning/zero_buy_ledger.py:4`、`scan/agents/l3_catalyst.py:4` | docstring 里的 design 引用 | `HISTORICAL_REFERENCE` | 不动 |
| `scan/prelude.py:379`、`scan/health.py:20`、`scan/artifacts.py:8`、`scan/report_sections.py:687` | 退役说明注释 | `HISTORICAL_REFERENCE` | 不动 |

### 为什么 5 处 `watchlist_trigger` 残迹**不删**

它们是**排除集里的一个字符串**:`lane == "watchlist_trigger"` 永远不成立,所以既不改变
行为、也不产生错读数。删它们要动 5 个文件的判断逻辑,而收益只是 grep 干净 —— §B2 明写
「不为追求零 grep 强删」。**与 journal 那一列的区别是关键**:恒 0 的**列会显示给人看**,
读者会以为「今天没触发」而不是「这条腿已经没人喂了」;排除集里的死字符串不显示给任何人。
**会误导人的死码优先删,只是碍眼的死码留着。**

### journal「触发」列的处置

删列 + 删读取;渲染表头与汇总行同步。历史 `watchlist_status.csv`(10 个扫描日)**原样
留在盘上,不回写、不删**;测试里保留一个「盘上仍有 stale watchlist_status.csv」的夹具 ——
退役要退得干净,不是退成一颗雷。`journal.roll()` 的唯一生产消费者
`evidence_manifest.py:252` 不读该列,schema 变更对它无影响。

---

## B3 · `earlystop_shadow` 整族 —— `ABANDONED`

### 前提核验(设计稿 §B3 的四条,4/4 实测吻合)

| 立案说法 | 实测 |
|---|---|
| 当前 scan root 零 `shadow/earlystop_queue.json` | ✅ `find context/scan -name "earlystop_queue*"` 无命中 |
| `write_shadow_queue` 只有 CLI/tests 调用 | ✅ 生产侧唯一调用点是它自己的 `main()`(`earlystop_shadow.py:328`) |
| 账本 0 reviews | ✅ `reports/learning/earlystop_shadow.md`:「已完成影子深审:0」 |
| 无生产 producer | ✅ 见下 |

**判定**:不是「队列有货没人取」,而是**消费者与账本接了、producer 从未接线**。

    接了的:  health.py(读队列算 pending/completed)、post_run.py(RETRO_FINALIZED 消费者
              + 账本刷新名单)、artifacts.py(两条 ArtifactSpec)、独立 workflow、账本渲染
    没接的:  **往队列里写东西的那一步** —— 全仓没有任何生产路径调用 `write_shadow_queue`

所以它从来没有产生过一条数据。不新增自动派发来拯救沉没成本(§B3)。

### ②查 test 双职 —— 无

6 个用例全部围绕影子队列本身;`sample_score`(sha256 稳定采样)在全仓**只服务本模块**,
不构成通用采样契约。`tests/scan/test_workflow_syntax.py` 里那条顺带断言
「scan-market.js 不得派发 earlystop-shadow」在 workflow 文件消失后已恒真,无迁移对象。

### 删除清单

| 文件 / 位置 | 动作 |
|---|---|
| `autoresearch/learning/earlystop_shadow.py` | 删 |
| `.claude/workflows/earlystop-shadow.js` | 删 |
| `tests/learning/test_earlystop_shadow.py`(6 例) | 删 |
| `tests/scan/test_workflow_syntax.py::test_earlystop_shadow_workflow_is_separate_and_shadow_only` | 删(留一条说明注释) |
| `tests/test_agent_defs.py::test_earlystop_shadow_workflow_forces_full_review_without_production_writes` | 删 |
| `autoresearch/scan/artifacts.py` | 删两条 ArtifactSpec(索引 28→26,计数断言同步) |
| `autoresearch/scan/post_run.py` | 从 `RETRO_FINALIZED` 消费者集与账本刷新名单里移除 |
| `autoresearch/scan/health.py` | 删 `shadow/earlystop_queue.json` 读取块 |

⚠️ **不连坐**:`autoresearch/learning/earlystop_ledger.py` 是**早停分桶账本**(nightly_close
真跑),与影子深审无关,LIVE。

历史设计与 commit 保留。lessons 记:**消费者与账本接了,producer 从未接线** —— 一个特性
可以看起来"已建成"(有账本、有 workflow、有 health 读数)而其实一条数据都没产生过。

---

## B4 · 未启用路径身份重判

### B4-a `performance.stable_context_blocks` —— `ABANDONED`(收益 4.0% < 10% 门)

零 LLM 零网络的离线 benchmark:对 **2026-07-31 真扫描日**在两个隔离副本里各重建一次
全部 10 份 L4 prompt(`write_dispatch_pack(stable_context=True/False)`),逐项比对。

| 判据(§B4) | 实测 | 结论 |
|---|---|---|
| prompt **事实等价** | 逐票数字多重集 **10/10 一致** | ✅ 等价 |
| **manifest 完整性** | `_l4_prompt_manifest.json` 10 份 prompt、逐块 `content_sha256` | ✅ 完整 |
| **共享前缀字节收益** | legacy 2,030 B → stable 2,468 B(+438 B/份) | — |
| **预估节省** | 438 × 9 ≈ 3,942 B = L4 prompt 总输入 99,719 B 的 **4.0%** | ❌ **< 10%** |

**判定 `ABANDONED`**:事实等价、manifest 也完整,但**收益不到门槛的一半**。它不是"坏",
是"不值得"——留着就得永远双路维护 `prompts.py` 里那一串 `if stable_context:` 分支,
而那是最不该有分叉的确定性路径(`context.py:447` 的注释正是在提醒"legacy 路要记得镜像
stable 分支的 dossier_sections 调用",这种镜像义务本身就是双路的税)。

⚠️ **过程留痕**:我第一次查 manifest 时找的是 `_l4_shared_manifest.json`,没找到就差点
写成"manifest 未落=事实不完整"。真实文件名是 `_l4_prompt_manifest.json`。
**「文件不存在」永远是弱证据 —— 断言缺失前先 grep 源码里的写盘路径。**

### B4-b `performance.sector_brief_mode=finalist_only` —— `ABANDONED`

按 §B4 铁律「性能开关不拥有评级」:它让 L3 看不到原本的判断型行业 brief,**可能改变
finalists**,不能靠 dispatch 数离线证明评级等价。当前 registry(`context/learning/
experiments/registry.json`)里只有一个 `exp_20260729_l3_hard_constraint_f`,**没有**任何
获批的、覆盖本开关的 research experiment。§B4 明确不允许"继续维持默认 false"作第三选项。

**判定 `ABANDONED`。**

### ✅ 两项删除已执行(2026-08-02)

**删除清单**:`scan/context_blocks.py` 与其 tests · `prompts.py` 的三处 `stable_context`
分支 · `l4_card.py` 的 `--stable-context` 旗标 · `user_config` 的两个 performance 键 ·
workflow 的两处开关与 `finalist_only` 分支 · `scan_config.jsonc` · STAGES/SKILL 文档。

**parity 验收(这类删除唯一站得住的证据)**:删前先对 2026-07-31 真扫描日的 10 份 legacy
prompt 取 sha256 基线,删后重建 → **10/10 字节完全一致**。生产输出一个字节都没变。

**B0 ②查 test 双职**:
- `test_l4_prompt_cache_prefix.py` 的两条(默认==显式False / stable 模式块序)随参数删除 ——
  它们顺带锁的「共享块须在逐股字节之前 + 头部 byte-identical」由同文件
  `test_prompts_share_byte_identical_head` 完整覆盖(legacy 现在是唯一的路),无孤儿;
- `test_user_config` / `test_wave3_workflows` / `test_agent_defs` 的相关用例**保契约换载体**
  (performance 白名单往返改用 `streaming_l4`;顺序契约改为断言「只有一条 brief 路」;
  文档锚改为锚住「退役这件事本身」——否则下次有人照着旧文档去设一个不存在的开关)。

**过程中的两次自伤(记下来,别再犯)**:
1. 用 `ruff check --fix` 顺手修 lint,它把 `l4_card.py` 的**再导出面**(20 个 `from
   l4.context import ...`)当未使用导入整块删掉 —— 而 `_OW_GATES` 等正被测试当**单一事实源**
   import。本仓库 HEAD 本身就有 189 条同类告警(全是有意的再导出),**这个仓库不能跑 `--fix`**。
2. 删 `scan_config.jsonc` 的 performance 尾项时留下**尾随逗号**,导致全仓配置加载崩溃、
   33 个测试连坐 —— 而症状分布得毫无规律(CLI entrypoint、gate_ledger、cross_calib…),
   差点被当成删除本身的回归去查。**改 jsonc 后必须立刻解析一次。**

---

## B5 · 回滚杆到期状态

2026-08-01 实测,**三根全部未到期**:

| 回滚杆 | `RETIRE_ELIGIBLE` 判据 | 实测进度 | 状态 |
|---|---|---|---|
| `streaming_l4=false` 旧批量 GATE3 | 流式路径累计 **10** 次真实扫描且 `structural_failure_n=0` | 带 task-book 的真实扫描日 **4** 个(07-28/29/30/31) | ❌ 还差 6 次 |
| `_ensemble.json` 旧批量双读 | 与旧批量路径同生死 | 旧格式最后一次产出 2026-07-10;此后走逐票 `_ensemble_<code>.json` | ❌ 随上一条 |
| abstention v1 | v2 达 **10** 个 mature day 且两日重跑一致 | v2 已裁决 **8** 日 | ❌ 还差 2 日 |

**均只记条件,不删**(R2:证据不够只标 `RETIRE_ELIGIBLE` 条件)。
`structural_failure_n` 目前**没有对应的落盘计量** —— 判据里写了这个量,但全仓无一处产出它。
在第 10 次真实扫描到来之前必须先把它接上,否则到期那天只能靠人回忆"这 10 次有没有出过
结构失败",那等于没有判据。**记为本波未做项。**

代码不得在第 10 日 assemble 后自行修改/删除源码(R7)。
