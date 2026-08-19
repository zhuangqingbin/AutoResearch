# 决策文件双写者 × 08-11 taskbook hash 失配 —— 三线取证

- 日期：2026-08-19
- 起因：`.superpowers/sdd/2026-08-18-e6-activation-slimdown-implementation-plan/task-1.7-brief.md`（线①）+ 本波 E4 brief_lint 第⑥条上线即逮到的 08-13 不一致（线②，调查中扩为主线）
- 基线：HEAD=`8cda66e`，工作树干净
- 纪律：`context_claude/` 与 `reports_claude/` 下的 **run 现场产物一律只读**；一切复现都在 scratch 拷贝上做（例外见 §0.1 披露）

---

## 0. 摘要

| 线 | 一句话结论 |
|---|---|
| ① 08-11 的 27 条 `ARTIFACT_HASH_MISMATCH` | **(a) 噪音，但机制既不是「同日重跑」也不是「竞态」**——是 08-11 引擎分根迁移（`context/` → `context_claude/`）让账本记的**路径串**失效；27/27 产物在迁移后的位置上**逐字节等于账本 hash**。同病波及 08-03..08-11 全部 7 个可测日（累计 216 条全是同一个假阳）。已修 + 加锁（§1.4）。 |
| ② 发布报告 vs 决策文件不一致 | **假设部分成立、机制更简单也更严重**：`_relative_buy_decision.json` 确有两个写者，但两者的差异**不是** gate4 stage_result 落盘时序，而是 **writer-1 读的 `run_health.json` 是 `build_summary` 之前那份快照**——它报的 `decision_records.status` 是 `ABSENT`，于是**每一次「当日首次 assemble」的 brief 都被系统性地印成 BLOCKED**。08-13 复现逐字段吻合。**BUY 688766 是对的，发布出去的 BLOCKED 是假阴。** 8 份 brief 中 4 份与决策文件不一致。**转正后发布 BUY 与记分册 BUY 会分家**（§2.6）。 |
| ③ reconcile 会不会把那 27 条补记回 SUCCEEDED | **不会，双重不可能。** 实测 08-11 book 拷贝跑 `reconcile` → `recovered=[]`、文件字节零变化。理由：(i) 9 票**已全是 SUCCEEDED**，`reconcile` 只处理 `RUNNING`；(ii) 即便是 RUNNING，它按**账本记的路径**查文件，08-11 那些 legacy 路径根本解析不到，只会落进 `skipped`。 |

### 0.1 披露：本次调查实际发生的写盘

- `reports_claude/learning/structural_audit.md` 被重新生成 **2 次**（修复前 1 次 = brief Step 1 指定的命令；修复后 1 次，让盘上读数与代码一致）。该文件是**滚动派生报表**（nightly/prelude 每日重算），不是 run 现场产物；留一份写着「216 次结构失败」的旧读数在盘上比重算更有害。
- 除此之外，`context_claude/**` 与 `reports_claude/scan/**` **零写入**。所有复现（08-13 writer-1 状态、08-11/08-12 reconcile）都在 `scratchpad/` 的拷贝上做，并逐个校验了原件 hash 未变。

---

## 1. 线①：08-11 的 27 条 `ARTIFACT_HASH_MISMATCH`

### 1.1 第一刀：27 条根本不是「hash 不符」

```
findings n = 27
Counter({'ARTIFACT_HASH_MISMATCH': 27})
Counter({'产物已不在盘上': 27})       ← 27/27 全是这一条 detail
```

`structural_audit._rehash_findings` 有三个分支：无 hash / **路径不在盘上** / hash 不符。27 条全部落在**第二个**分支。立案文档（设计稿 §2.1、task-1.7 brief）把它读成了「hash 失配」，并据此提出「同日重跑改了内容」与「`mark_success` 竞态」两个假设——**两个假设量的都不是这条 detail 在说的事**。（本仓判例：「量错对象」，`premise-check-before-implementing-20260728`。）

### 1.2 第二刀：为什么路径不在盘上

`08-11` 的 book 逐票时间线（9 票，全 `SUCCEEDED`，`attempt=1`、`last_error` 全空、`structural_events=[]`）：

| code | status | updated_at | 记录的 card 路径 |
|---|---|---|---|
| 000999 | SUCCEEDED | 2026-08-11T12:40:31Z | `context/scan/2026-08-11/details/000999.md` |
| 600398 | SUCCEEDED | 2026-08-11T12:33:11Z | `context/scan/2026-08-11/details/600398.md` |
| …（其余 7 票同形，12:32–12:57Z 之间逐个收尾）| | | |

**没有一票重试、没有一条 `last_error`、没有一条活体结构事件**——「同日重跑」与「竞态」在时间线上都无立足点。

真因在路径的**第一段**：`context/`。2026-08-11 用户裁定引擎分根（`autoresearch/common/workspace.py`，`context/` → `context_claude/` + `context_codex/`），仓库里 `context/` 目录**已不存在**（实测 `test -d context` → NO）。账本记的是迁移前的串。

全仓 book 的路径前缀分布（实测）：

| 日期区间 | artifact 路径前缀 | 备注 |
|---|---|---|
| 2026-07-28 … 2026-08-11 | `context/` ×339 | 分根前 |
| 2026-08-12 … 2026-08-18 | `context_claude/` ×90 | 分根后 |

**08-11 是最后一个分根前的扫描日。**

### 1.3 第三刀：产物本身完好

把每条 legacy 路径重挂到 `context_claude/` 下重算 sha256，与账本记的 `content_hash` 逐条比对：

| 日期 | 迁移后 hash 相符 | 不符 | 真丢失 |
|---|---:|---:|---:|
| 07-28 | 33 | 0 | 0 |
| 07-29 | 20 | **7** | 0 |
| 07-30 | 33 | 0 | 0 |
| 07-31 | 30 | 0 | 0 |
| 08-03 | 30 | 0 | 0 |
| 08-04 | 27 | 0 | 0 |
| 08-05 | 30 | 0 | 0 |
| 08-06 | 33 | 0 | 0 |
| 08-07 | 36 | 0 | 0 |
| 08-10 | 33 | 0 | 0 |
| **08-11** | **27** | **0** | **0** |
| **合计** | **332** | **7** | **0** |

唯一的 7 条不符全在 07-29，且**全是 `prompt`**，mtime 全为 `2026-07-30T11:07:53`——正是 `structural_audit` docstring 里早已写明的 `write_dispatch_pack` 修复验证跑，归 `PROMPT_REBUILT`（观察类）已被现有豁免覆盖。

### 1.4 裁定与修复

**裁定 = (a) 噪音**。不是同日重跑（无重跑痕迹、内容零变化），也不是竞态（`mark_success` 与产物写入的一致性由 27/27 hash 相符**直接证伪**了失配）。是**第三个原因：引擎分根迁移让路径串失效**。

> 这正是 `plan-defects-outnumber-impl-slips` 里那条「二元措辞掩盖粒度差」：brief 给的是 (a)/(b) 二选一，真答案在选项外。

**已实施**（brief 授权的 (a) 分支）：

- `autoresearch/scan/structural_audit.py`
  - 新增观察类 kind `ARTIFACT_PATH_MIGRATED`（进 `OBSERVATION_KINDS`，**不进失败数**）。
  - 新增 `_legacy_context_root()` + `_resolve(recorded) -> (Path, bool)`：账本原样命中优先；**仅当**原样不在盘上、且路径首段恰是「现根去掉引擎后缀」的旧根名时，才重挂到 `ws.context_root()` 下试一次；两次都不在 → 原样返回，照旧判失败。
  - `_rehash_findings` 判据顺序（**顺序即语义**）：无 hash → `COMPLETION_MISJUDGED`；两处都不在盘 → `ARTIFACT_HASH_MISMATCH`；**hash 不符 → 迁移与否都不豁免**（prompt→`PROMPT_REBUILT`，其余→`ARTIFACT_HASH_MISMATCH`）；hash 相符且走了别名 → `ARTIFACT_PATH_MIGRATED`。
  - `render()` 加脚注说明该 kind。
  - **不写裸根字面量**：`tests/common/test_workspace.py` ③ 的 AST grep 探针对生产代码零容忍，旧根名由 `ws.context_root().name.split("_")[0]` 现算。

- `tests/scan/test_structural_audit.py` 新增 3 条（附 `_legacy_path_book` 夹具，造「路径串在旧根、文件在现根」的真实形状）：
  1. `test_pre_engine_split_paths_are_an_observation_not_a_failure`：`failure_n==0`、`ARTIFACT_PATH_MIGRATED==3`、`observation_n==3`（**必须可见，不是静默豁免**）。
  2. `test_migrated_path_with_changed_content_is_still_a_real_failure`：迁移位置上 card 被改 → 仍 `failure_n==1`。别名只解释迁移，不替内容变更打掩护。
  3. `test_truly_missing_artifact_is_still_a_failure`：两处都找不到 → 仍判失败。

**变异校验（真红灯，不是绿灯剧场）**：把 `_resolve` 的别名分支删掉退回旧口径 → `3 failed, 16 passed`，三条新用例全红。恢复后 `tests/scan/test_structural_audit.py` + `tests/common/test_workspace.py` + `tests/scan/test_l4_tasks.py` = **37 passed**，`ruff` clean。

### 1.5 ⚠️ 修复的治理后果（必须由用户裁决，不得默认放行）

| | 修复前 | 修复后 |
|---|---|---|
| 可测日累计结构失败 | **216** | **0** |
| 连续干净 streak | **4/10**（被 08-11 打断） | **11/10**（被 07-31「不可测」打断） |
| 回滚杆①（退役 streaming_l4 旧批量兜底） | 未到期 | **机械上已满足** |

**建议不要把这当成自动 GO**，两条理由：

1. **口径变了不等于历史被验证过**。08-03..08-11 那 7 天的「干净」是这次重算出来的，快照那半确实干净，但它们的活体三类事件当时**确实记了**（`structural_events=[]`），所以这 7 天是合法的可测干净日——这一条站得住。真正站不住的是下面这条。
2. **判官对最该看的那种失败是瞎的**。`_rehash_findings` **只查 `status=="SUCCEEDED"` 的票**。2026-08-12 全部 9 票卡死 `RUNNING`（`mark_success` 从未执行，E6 contract 门当天团灭全部候选——设计稿 §2.1 点名的事故日），而 `structural_audit` 给它的评分是 **`failure_n=0` / 明细「无事件」**，并且这个「干净日」现在正躺在那条 11 天连胜里。**回滚杆①的判据看不见它要防的那类结构失败。** 见 §4 建议 P1-1。

---

## 2. 线②：发布报告与决策文件的历史不一致

### 2.1 两个写者确认存在（假设的前半成立）

`_relative_buy_decision.json` 的写入只有一条代码路径：`post_run.publish_run_observation`（`post_run.py:556`）→ `relative_buy.safe_write_decision`（`post_run.py:643`）。而 `publish_run_observation` 有**两个调用点**：

| # | 调用点 | 时机 |
|---|---|---|
| writer-1 | `publisher._run_publish` → `publisher.py:314` | assemble 内部，**brief 渲染之前** |
| writer-2 | `post_run` CLI `observe`（`post_run.py:870`，STAGES 步骤 5 最后一条命令，`.claude/skills/scan-market/SKILL.md:157`） | brief 落盘**之后**，**无条件原子重写** |

`brief.py:414` 读的是**盘上那份文件**（不是现算）。所以：**brief 印的永远是 writer-1 的答案，盘上最终留下的永远是 writer-2 的答案。**

实测 mtime（8 个 run 全部同形，决策文件恒晚于 brief 2–25 秒）：

```
20260813_2218  brief 22:18:39   decision 22:18:42
20260817_2215  brief 22:15:33   decision 22:15:36
20260818_2043  brief 20:43:54   decision 20:44:19
```

### 2.2 但差异**不是**假设里的 gate4 时序，而是「run_health 快照早于它所报告的产物」

任务给的主要假设是「gate4 的 stage_result 在 brief 之后才落盘 → `stage_results.failed` 变化 → `data_a` 翻转」。**这条不成立**：08-13 的 `stage_results/gate4.json` 状态是 `SUCCEEDED`，`gate_fires.csv` 31 行**零 fail**，`failed` 前后都是空。

真机制在 `publisher._run_publish` 的写盘顺序里：

```
publisher.py:273   write_run_health(scan_dir)        ← 快照 #1
publisher.py:305   md = build_summary(...)           ← decision_records.json / _final_ratings.json / gate_fires.csv 在这里才写
publisher.py:314   publish_run_observation(...)      ← ★ writer-1 写决策文件，读的是快照 #1
publisher.py:346   record_gate_stage_result(gate4)
publisher.py:363   write_run_health(scan_dir)        ← 快照 #2（对 brief 来说太晚了）
publisher.py:375   _publish_brief(...)               ← brief 读盘上的决策文件 = writer-1 的
publisher.py:395   brief_lint(...)                   ← 此刻文件仍是 writer-1 的（见 §2.5）
```

`relative_buy._data_contract_ok`（`relative_buy.py:300-321`）判 `data_a` 时读的是 **`run_health.json` 里的 `decision_records.status`**，而不是 `decision_records.json` 本身。当日**首次** assemble 时，快照 #1 是在 `decision_records.json` 存在之前拍的 → `health.decision_records_health` 返回 `status="ABSENT"`（`health.py:469-480`）→ `_data_contract_ok` 第 5 判失败 → **`hard_gate.data_a` 对当日全部候选团灭** → `blocked=True`。

而同一次 `build_decision` 读 `decision_records.json` **本体**取评级时，文件早已在盘上（22:18:26 写、22:18:4x 读）→ `contract` 门正常放行。**「data_a 全灭而 contract 不灭」这个奇怪组合，正是这个机制的指纹。**

> `relative_buy.py:717` 的 I-1 注释写着：「两道门是独立防线：**run_health 写得早/写得乐观时 data_a 会被骗过**，contract 仍拦得住。」——作者预见了「写得早」，但只想到它会造成**假放行**，没想到它造成的是**假拦截**。
>
> `report_sections.py:270-278` 也已明确写过「finalizer 必须排在 `build_summary` 之后，否则会判 BLOCKED」——顺序确实修对了（`decision_records.json` 已在盘），但**报告这件事的 `run_health.json` 没跟着刷新**，修了一半。

### 2.3 复现（决定性，逐字段吻合）

在 scratch 拷贝上重建 writer-1 时刻：移走 `build_summary` 之后才产生的文件 → `write_run_health`（= 快照 #1）→ 把三份产物放回（= `build_summary` 跑完，但 run_health **不刷新**）→ `build_decision`：

```
[快照#1]        decision_records.status = ABSENT | stage_results.status = OK | failed = [] | core_missing = []
[快照#1]        _data_contract_ok -> (False, "decision_records.status='ABSENT'(非 OK)")
[writer-1 时刻] blocked = True | buys = []
                blocked_reasons = [{'reason':'hard_gate.data_a','n':11}, {'reason':'hard_gate.no_redflag','n':1}]
                counts = {'candidates': 11, 'eligible': 0, ...}
```

当天真实发布的 `reports_claude/scan/20260813_2218/brief.md:8`：

```
🕶 影子 relative BUY(非正式·不执行):BLOCKED(全部候选被硬资格否决:hard_gate.data_a×11、hard_gate.no_redflag×1;候选 11 / 合格 0)
```

**逐字段吻合（11/1/11/0）。机制确认。**

版本混淆的排除：盘上 6 份决策文件全是 `rule_version = e6.v1.1`（未被我的 v1.2 代码改写过）；且 `_data_contract_ok` 的 `decision_records.status != "OK"` 这一判在 v1.1（`git show 811900a^`）与 v1.2 之间**逐字未变**，复现有效。

### 2.4 哪个答案是对的：**BUY 688766**

判据 = 当天真实的数据契约状态：

| 判据 | 08-13 实测 |
|---|---|
| `run_health.core_missing` | `[]` |
| `run_contract.status` | `OK` |
| `stage_results.status` / `failed` | `OK` / `[]`（45 个 stage 全 SUCCEEDED） |
| `decision_records` | `OK`，n=11，`contract_hash_match` / `final_ratings_match` / `early_stop_match` 全 true |
| `stage_results/gate4.json` | `SUCCEEDED`，31 checks |
| `gate_fires.csv` | 31 行、**0 条 fail** |

当日数据契约**确实成立**，BLOCKED 唯一的成因是「读了一份早于 `decision_records.json` 的 run_health 快照」。**发布给用户的 BLOCKED 是假阴（false negative），事后重算的 BUY 688766 才是对的。**

（唯一残留噪声：`context_claude/scan/2026-08-13/run_health.json` 的 mtime 是 2026-08-18T20:46:59——`retro.py:753` 的回放会重算历史日的 run_health。但 `write_run_health` 是对 scan 目录的**纯快照**，底层 `stage_results/` 与 `decision_records.json` 都是 08-13 原件，故重算结果与当日 writer-2 时刻等价；writer-2 当日现场写下的 BUY 688766 是独立佐证。）

### 2.5 附带发现：E4 的同源 lint 在跑内是结构性瞎的

`brief_lint` ⑥（`self_review.py:1332-1345`）在 `publisher.py:395` 跑，此刻决策文件**还是 writer-1 的**，与 brief 天然同源 → 通过。只有事后重跑（文件已被 writer-2 覆盖）才会报。实测：

```
事后 re-lint（决策文件 = writer-2 的 BUY）
  fail brief③相对BUY与决策文件不同源 | brief ③ 段渲染代码 ['无'] != 决策文件 buys[].code ['688766']
在跑内 lint（决策文件 = writer-1 的 BLOCKED）
  （无「同源」行 = 该检查通过）
```

**它上线即逮到 08-13，靠的正是「事后跑」这个偶然。** 在生产 in-run 位置上，这条 lint 永远绿——「永不变红的绿灯」同族（`wave35-mutation-testing-20260724`）。

### 2.6 全历史不一致表（08-10 起全部 8 份 brief）

| run | 数据日 | brief ③ 印的 | 盘上决策文件 | 一致 | 形态 |
|---|---|---|---|:--:|---|
| 20260810_0128 | 2026-08-07 | BLOCKED data_a×12、no_redflag×1（候选 12） | BLOCKED data_a×12、no_redflag×1 | ✅ | 巧合一致（当日**真**有 data 病，见下） |
| 20260810_2109 | 2026-08-10 | BLOCKED data_a×11、no_redflag×1（候选 11） | BLOCKED data_a×11、no_redflag×1 | ✅ | 同上 |
| 20260811_2057 | 2026-08-11 | BLOCKED data_a×9、no_redflag×2（候选 9） | BLOCKED data_a×9、no_redflag×2 | ✅ | 同上 |
| 20260812_2037 | 2026-08-12 | BLOCKED **contract×9、data_a×9**、no_redflag×1 | BLOCKED **contract×9**、no_redflag×1 | ❌ | **理由集不同**（data_a 事后翻正；结论仍 BLOCKED，因 contract 真拦） |
| **20260813_2218** | **2026-08-13** | **BLOCKED data_a×11、no_redflag×1** | **BUY 688766** | ❌ | **结论翻转** |
| 20260817_2150 | 2026-08-17 | BLOCKED data_a×9（候选 9） | BUY 000779 | ❌ | 结论翻转（该 run 为当日**首跑**，随后被 2215 取代） |
| 20260817_2215 | 2026-08-17 | BUY 000779 | BUY 000779 | ✅ | **健康日**：当日**二次** assemble，快照 #1 里已有首跑留下的 `decision_records.json` → data_a 通过 |
| **20260818_2043** | **2026-08-18** | **BLOCKED data_a×10、no_redflag×2** | **BUY 688766** | ❌ | **结论翻转** |

**统计**：8 份 brief 中 **4 份不一致**（3 份结论翻转 + 1 份理由集不同）。

**规律**（这才是重点，不是「偶发事故」）：

- **当日首次 assemble → brief 恒被印成 BLOCKED**（快照 #1 必然早于 `decision_records.json`）。8 份 brief 里 7 份是首跑，7 份全 BLOCKED。
- **当日二次及以后 assemble → 正常**（上一跑的 `decision_records.json` 还在盘上）。唯一的二次跑 20260817_2215 是唯一印出 BUY 的 brief。
- 08-07/08-10/08-11 那三天「一致」是**巧合**：那三天 gate4 因 self_review 卫生/计量 fail 行而 FAILED（设计稿 §2.1），`data_a` 事后重算**照样**是 False，两个错误撞在同一个答案上。**不能拿它们当「这条链路没病」的证据。** 逐日只读实测：

  ```
  2026-08-07  gate4=FAILED     failed=['gate4']  dr=OK -> data_a=(False, "...failed_data=['gate4']")
  2026-08-10  gate4=FAILED     failed=['gate4']  dr=OK -> data_a=(False, "...failed_data=['gate4']")
  2026-08-11  gate4=FAILED     failed=['gate4']  dr=OK -> data_a=(False, "...failed_data=['gate4']")
  2026-08-12  gate4=SUCCEEDED  failed=[]         dr=OK -> data_a=(True, '')     ← 但 contract 门真拦(9 票 RUNNING)
  2026-08-13  gate4=SUCCEEDED  failed=[]         dr=OK -> data_a=(True, '')     ← brief 却印了 BLOCKED
  2026-08-17  gate4=SUCCEEDED  failed=[]         dr=OK -> data_a=(True, '')
  2026-08-18  gate4=SUCCEEDED  failed=[]         dr=OK -> data_a=(True, '')     ← brief 却印了 BLOCKED
  ```

  （盘上 run_health 全部无 `failed_data` 键，`_data_contract_ok` 按注释回落 v1.1 口径读 `failed`——历史判定不被 E1a 改写，符合设计。）
- 记忆里那条「08-17 brief 读了早 25s 的半成品」的事故记录，指的应该是 20260817_2150 这一跑；**它不是偶发的 25 秒竞态，是首跑必现的确定性缺陷**。

### 2.7 对批次 2 转正的影响（必答）

**结论：会。发布给用户看的 BUY 与记分册记的 BUY，在当日首跑上系统性地不是同一个答案。**

证据链：

1. **记分册读盘上的文件**：`relative_ledger.roll()`（`relative_ledger.py:398-415`）`_json_doc(day / DECISION_FILENAME)` 直接读 `_relative_buy_decision.json`——即 **writer-2** 的答案。
2. **发布层读同一个文件、但在 writer-2 之前**：`brief.py:414`，即 **writer-1** 的答案。
3. **实锤**：`context_claude/learning/relative_buy.jsonl` 最近 3 个 BUY 日 —

   | 日期 | 记分册记的 | 当天 brief 印的 |
   |---|---|---|
   | 2026-08-13 | **BUY 688766**（已成熟 +2.19%） | **BLOCKED** |
   | 2026-08-17 | BUY 000779 | BUY 000779（2215 二次跑）✅ |
   | 2026-08-18 | **BUY 688766** | **BLOCKED** |

   **3 天里有 2 天，记分册给系统记了一笔用户从没在报告上看见的 BUY。**

4. **转正后只会更糟，不会自愈**：设计稿 §3 E3 规定 active 模式下 `publisher` 只认 `_relative_buy_decision.json`（`decision_finalize` 的 proposal、`report_sections` 的组合视角/仓位、`health.count_buys`、`brief.py` 的 ✅ 行全部改读它）。于是：
   - **brief ③ 会在几乎每个新扫描日印出「✅ relative BUY：BLOCKED」**，而记分册当晚记一笔 BUY。Wave12 R-E2「成功日必须给一个可执行答案」的产品裁定当场落空——**转正等于把一个假阴挂上正式招牌**。
   - `brief_lint` ⑤（`BRIEF_LINT_SEVERITY["brief·BUY契约(active 期)"] == "fail"`）会在**每个首跑日**报 fail → `append_gate_fires` → CP7 的 gate4 CLI 复算成 **FAILED**。该 check 名不在 `common/failclass.EXEMPT_PREFIXES` 里 → `fail_class` 默认 **data** → 进 `stage_results.failed_data`。此后**任何一次 run_health 重算**（当日二次 assemble、nightly、retro 回放）都会让 `data_a` 变 False。**即：一次假阴 BLOCKED 会亲手制造出一条 data 类硬门失败，把「本来只是渲染早了一步」升级成「这一天数据契约真的破了」**——错误从此自洽，事后再查也看不出原始成因。
   - 更下游的 `decision_finalize` / `report_sections` 跑在 `build_summary` **内部**（`publisher.py:305`），比 writer-1 还早一站——active 模式下它们读到的会是**上一跑（甚至上一天）**的决策文件。E3 切换表里这三个消费点的挂点时序**尚未验证**。

5. **票会不会「换人」而不只是「有/无」**：目前 8 天历史里**未观测到**同一天两个写者选出不同代码的情况；`data_a` 是**日级**门，翻转只会造成 BLOCKED↔BUY，不会换票。但**逐票**的 `contract` 门读 `_l4_tasks.json`，而 E1b 的 `reconcile` 现在挂在 `publish_run_observation` 内（两个写者各跑一次，中间隔着整个 CP7 链）——一票在这中间从 RUNNING 被补记成 SUCCEEDED，就足以让它进合格池并可能拿到 rank1。**结构上可达，证据上未发生**（本仓判例：产物能证明跑过什么、不能证明没跑过什么）。

**给批次 2 的硬结论：在 §4 的 P0-1 修好之前，不要把 mode 翻 `active`。** 这不是「转正后再优化」的事项，是转正的**前置阻断项**。

---

## 3. 线③：reconcile 与线①的交叉

**结论：`l4_tasks.reconcile` 不可能把 08-11 那 27 条静默补记回 SUCCEEDED。**

实测（scratch 拷贝，原件 hash 前后校验未变）：

```
2026-08-11: recovered=[] skipped=[] | book_bytes_changed=False | statuses={'SUCCEEDED': 9}
2026-08-12: recovered=['000568','003013','300857','600519','600988','601009','601229','601869','688766']
            | book_bytes_changed=True  | statuses={'SUCCEEDED': 9}
```

两重独立理由：

1. **状态闸**：`l4_tasks.py:522-523` `if task.get("status") != "RUNNING": continue`。08-11 九票**已全是 SUCCEEDED**，循环第一行就跳过；`if recovered:` 守卫使得**文件根本不被写**（实测字节零变化）。（这是 `354eed5`「收窄到仅 RUNNING」复核修复的直接收益——修复前的「非 SUCCEEDED 皆自愈」也同样碰不到 SUCCEEDED，但会去动 FAILED/BLOCKED。）
2. **路径闸**：即便假设某票是 RUNNING，`l4_tasks.py:526-529` 按**账本记的原样路径**做 `p.is_file()`——08-11 记的是分根前的 legacy 串，解析不到任何文件 → 进 `skipped`，**不会**补记。换言之：线①那些票恰恰因为路径失效而**天然免疫**于 reconcile。

08-12 那 9 票被全数 recover，证明 reconcile 对它的**设计目标**（`mark_success` 未执行、卡死 RUNNING）确实有效，且两条闸互不干扰。

**一条限制须留痕（不是本次缺陷，是这类自愈的固有性质）**：`reconcile` 补记时用 `_artifact(p, content_hash=_sha256(p))` **现算** hash（`l4_tasks.py:537-539`），所以被 recover 的票在事后重验里**永远不可能** hash 失配——它的账本 hash 是 reconcile 时刻取的，不是生产时刻取的。这不是「洗白 08-11」（reconcile 在 08-11 一次都不会跑），而是说：**`recovered=true` 的票，其 hash 只证明「reconcile 那一刻文件是这样」**。`recovered` 标记让它可审计，够用；但 §1.5 说的「判官看不见 RUNNING 票」这个盲区与它叠加，值得在 P1-1 一并处理。

---

## 4. 建议动作清单

> 除线①已实施的那一项外，以下**全部只是建议，本次零实施**。线②的修复方案须先经用户裁定。

### P0（转正阻断项）

**P0-1 · 决策文件在「它所依赖的事实全部定稿」之后才写第一次**

- 改哪个文件的什么：`autoresearch/scan/publisher.py`——把 `publish_run_observation`（`:314`）的调用移到 `_health.write_run_health`（`:363`）**之后**、`_publish_brief`（`:375`）**之前**；`summary.md` 里的 observation managed 块改由该次结果注入（现在 `md` 在 `:305` 就成型、`:326` 落盘，需拆成「先落 summary，再注入」或把注入延后）。
  - 备选（改动更小、风险更集中）：在 `publisher.py:314` **之前**补一次 `_health.write_run_health(scan_dir)`。代价是多一次快照，收益是 writer-1 读到的 `decision_records.status` 立刻变 `OK`。**推荐先做这一条**，它把 4/8 不一致直接归零，且不动 `publish_run_observation` 的位置。
- 验收怎么测：
  1. 单测（新）：造一个「`run_health.json` 早于 `decision_records.json`」的最小 scan 目录 → 跑 `_run_publish` 的可测切片 → 断言写出的 `_relative_buy_decision.json` 里 `blocked is False`。**变异校验**：把补写的那次 `write_run_health` 删掉，测试必红。
  2. 回归探针（新）：对 08-13 的 scratch 拷贝跑「首跑」序列 → brief ③ 渲染出的代码集合必须等于 `{688766}`（今天是 `∅`）。
  3. 活体：下一次真实扫描的**首跑** brief ③ 必须印出 BUY 而不是 BLOCKED——这是唯一能证明修好了的证据。

**P0-2 · 让「brief 与决策文件同源」这条 lint 在跑内真的会红**

- 改哪个文件的什么：`autoresearch/scan/publisher.py:395` 那次 `brief_lint` 现在与 writer-1 天然同源，零鉴别力。两条路选一：
  - (i) P0-1 落地后，**决策文件在 brief 前后各只有一个值** → 加一条断言「brief_lint 跑完之后，文件内容与 brief 渲染时读到的那份 byte-identical」；
  - (ii) 若保留双写者，则把 `post_run observe` 的第二次写改成**幂等校验**：重算结果与盘上不一致时**不覆盖**，而是落一条 data 类 fail 行（「同一天两次现算给出不同决策」本身就是必须报警的事实）。
- 验收怎么测：变异校验——人为让两个时刻的输入不同（如把 `decision_records.json` 在 brief 之后改掉），in-run lint **必须**变红。今天这个变异是绿的（§2.5 实测），所以这条测试从一开始就有鉴别力。

**P0-3 · 转正前先验 E3 三个早于 writer-1 的消费点**

- 改哪个文件的什么：不改，先测。`decision_finalize.py:24-30,264`、`report_sections.py:332,355,732`、`health.py:245-247` 都跑在 `build_summary`（`publisher.py:305`）内部，比 writer-1 早一站。active 模式下它们读 `_relative_buy_decision.json` 会读到**上一跑/上一日**的文件。
- 验收怎么测：造「scan 目录里放着前一日决策文件」的夹具 → active 模式跑 `build_summary` → 断言这三处**不得**采信过期文件（要么报缺席，要么由 P0-1 的新顺序保证文件已是当日的）。**这条不做，转正就是把 §2.7 的分家问题从 1 处扩散到 4 处。**

### P1（判据可信度）

**P1-1 · `structural_audit` 必须能看见「卡死在 RUNNING」**

- 改哪个文件的什么：`autoresearch/scan/structural_audit.py:_rehash_findings` 现在 `if task.get("status") != "SUCCEEDED": continue`，于是 08-12（9 票全 RUNNING、E6 当天团灭）被评为 `failure_n=0`「无事件」，且这个「干净日」正躺在 11 天连胜里。建议新增失败类 `TASK_STUCK_RUNNING`：run 已发布（`stage_results/assemble.json` 在场）而票仍非终态 → 计失败。
- 验收怎么测：对 `context_claude/scan/2026-08-12` 跑 `audit_day` **必须**返回 `failure_n==9`；streak 相应从 11 掉到 6（08-13/17/18 + …），回滚杆①回到未到期。变异校验：删掉新判据，08-12 又变「干净」→ 测试红。
- **这条与 §1.5 直接相关：在它落地前，11/10 的读数不足以支撑退役 streaming_l4 兜底。**

**P1-2 · 立案文档更正**

- 改哪个文件的什么：`docs/specs/2026-08-18-e6-activation-learning-slimdown-design.md` §2.1 与 §3 E1b 里「08-11 structural_audit 记 `ARTIFACT_HASH_MISMATCH` ×27（同族疑点）」的表述——它把 08-12 的 RUNNING 事故与 08-11 的路径迁移噪音**并成了同族**，实测二者无关（§1、§3）。同段的「BLOCKED 4 日根因」也需补一句：08-07/08-10/08-11 的 `data_a` 团灭**有两个独立成因叠加**（gate4 卫生 fail + 本文的 run_health 快照过早），E1a 只治了前者。
- 验收怎么测：文档改动，无测试；但 §2.1 的「E1a 修完 BLOCKED 就会消失」这个预期**是错的**——不修 P0-1，08-13 型首跑 BLOCKED 照旧。

**P1-3 · 决策文件带上 `generated_at` / 写者身份**

- 改哪个文件的什么：`relative_buy.build_decision` 输出目前**没有** `generated_at`（实测 6 份文件该字段全 None/缺失），也不记是谁写的。加 `generated_at` 会破坏 byte-stable 契约（`test_relative_buy.py:572-576` 锁着），所以建议改为在 `write_decision` 落盘时**另写**一份 `_relative_buy_decision.meta.json`（writer 身份 + 时间戳 + 输入文件 hash），决策文件本体保持 byte-stable。
- 验收怎么测：断言两个写者留下两条 meta 记录且 `inputs_hash` 不同——**今天要靠 mtime 猜的事，以后直接读出来**。

### P2（记账卫生）

**P2-1 · 记分册补一列「当日发布层是否印出了同一个答案」**

- 改哪个文件的什么：`learning/relative_ledger.row_from_decision` 加一列 `published_agreement ∈ {AGREE, DISAGREE, NO_BRIEF}`（比对同数据日最后一个 run 的 brief ③）。历史 15 行按本文 §2.6 回填。
- 验收怎么测：回填后 2026-08-13 / 2026-08-18 两行必须是 `DISAGREE`。转正后这一列恒 `AGREE` 才算 P0-1 真修好了。

**P2-2 · 07-28..07-31 四个不可测日的处置**

- 现状：这 4 天无 `structural_events` 键，永远 UNMEASURED，是 streak 的**永久天花板**（往前最多数到 08-03）。既然它们的快照那半现在已判干净（§1.3），建议在设计稿里显式裁定：要么承认「计量上线前不可追认」并把 required 的分母口径写成「计量上线后的连续日」，要么明确 streak 上限就是 11 天。
- 验收怎么测：`render()` 的分母行已同屏显示「可测 11 / 不可测 4」，改口径时该行必须同步——加一条断言锁住两个数之和 == `runs_total`。

---

## 5. 复现命令清单（全部只读或 scratch 内）

```bash
# 线① 取证
uv run --no-sync python -m autoresearch.scan.structural_audit          # 生成滚动报表(唯一写盘,见 §0.1)
# 逐票时间线 / 路径前缀普查 / 迁移后 hash 逐条比对 —— 见 §1.1-1.3 的表,脚本为一次性 heredoc

# 线② 复现(scratch 拷贝)
cp -R context_claude/scan/2026-08-13 <scratch>/repro/2026-08-13
#   → 移走 build_summary 之后才产生的文件 → write_run_health() → 放回三份产物 → build_decision()

# 线③ 复现(scratch 拷贝,仅拷 book,卡片走真实只读路径)
cp context_claude/scan/2026-08-1{1,2}/_l4_tasks.json <scratch>/
uv run --no-sync python -c "from autoresearch.scan.l4_tasks import reconcile; print(reconcile('<scratch>/book_2026-08-11.json'))"

# 线① 修复验收
uv run --no-sync python -m pytest tests/scan/test_structural_audit.py tests/common/test_workspace.py tests/scan/test_l4_tasks.py -q
```
