# L3 合并波实施计划——两遍法 + finalist tier 7–10 + 防漏仪器

> **For agentic workers:** REQUIRED SUB-SKILL: superpowers:subagent-driven-development。批次:A=T1∥T3,B=T2∥T4∥T5,T6 收口;executor 不 commit(控制端统一提交)。

**Goal:** 按用户 2026-07-12 裁定：L3.5 并入 L3——L3 直接出 **7–10 只 finalist（数量看当天质量）**，用「确定性 pass1 分诊 200→~60 + l3-rank 深比较出 finalist tier」提升准度，并建**防漏仪器**（pass1 影子账本 + bench 账本 + 误杀保险），确保收窄不吃掉好票。

**Architecture:** pass1 = 零 LLM 分诊（多路共振全入/每通道 top-K/healthy·pinned 全入），被切部分落 `_l3_pass1_cut.csv` 影子；pass2 = l3-rank（max effort）读 ~60 行表深比较，judged 每行加 `finalist` 布尔（7–10 只 true，其余=bench）；merge v3 按 finalist 标记+确定性守卫产 finalists.csv，bench 落 `_l3_bench.csv`；attribution/retro 增加 `l3_bench`/`pass1_cut` 归因层与漏检读数。L3.5 闸机制保留为 passthrough（回测 harness 不删），其收窄职能由 L3 finalist tier 承接。

**背景事实（现状锚）:** `l3_select.py`: `merge_l3_finalists_v2`:455（target=30,trend_quota=10 hybrid）、`write_finalists`:568（budget 来自 workflow `--budget ${g1.l4_budget}`）、`prepare_l3_table`:601、`load_l3_input`:133、`_render_lane_blocks`:247。`l3-rank.md`:34 输出 schema（无 finalist 字段）、:36 conviction 行为化定义（≥70 至多 ~5 只）。workflow `scan-market.js`:109-129（L3 段）。`gates.py` GATE2 count≤budget + `apply_l35_gate`（passthrough）。`menu.l4_budget` base 30/floor 12。

## Global Constraints

- **判定语义**：finalist 上限 = `min(10, l4_budget)`；**宁缺毋滥**——够格不足 7 只就出更少，禁止用 conviction<60 凑数；conviction **≥75 必须 finalist**（误杀保险，确定性强制）；conviction **<55 禁止 finalist**。
- **防漏三件套不可省**：pass1 被切码必须落 `_l3_pass1_cut.csv`；bench（judged 非 finalist）必须落 `_l3_bench.csv`；`L3_judged_full.csv` 全量判断照旧（retro/attribution 依赖）。
- 硬约束 A（健康上涨 ≥1/3）改比例制：finalist 里健康画像 ≥ ceil(n/3)（有够格候选才凑，无则不硬凑）；trend 保底降为 soft 2 席（同样只在够格时）。
- pinned 强留/L3 论点防锚定铁律/市场研判 §1–3 only 等既有不变量一律不动。
- config：顶层新键 `l3`（sub 白名单 `{"two_pass","pass1_target","finalist_max"}`），**默认 two_pass=true/pass1_target=60/finalist_max=10 = 本波后的新基线**（config 是回滚杆而非 opt-in——用户裁定即新常态，在 scan_config.jsonc 注释写明）。
- 测试 `uv run --no-sync python -m pytest <path> -q`；全量绿（基线 1161）；ruff 净。

---

### Task 1: pass1 确定性分诊 + 影子账本 + 表接线

**Files:** Modify `autoresearch/scan/agents/l3_select.py`（新函数 + `prepare_l3_table` 接线）、`autoresearch/scan/user_config.py`（白名单 `l3`）、`.claude/skills/scan-market/scan_config.jsonc`（样例块）;Test `tests/scan/test_l3_pass1.py`（新）、`tests/scan/test_user_config.py`（追加 l3 键测试,镜像 l4_intel 三连）

**Interfaces (produces):**
- `triage_l2_for_l3(df: pd.DataFrame, target: int = 60) -> tuple[pd.DataFrame, pd.DataFrame]`（kept, cut）。kept 规则（按序,去重,超 target 截尾按 gbdt/composite 分）:①`lane=="pinned"` 或 pinned 注入码全入;②多路共振全入（n_channels≥3;先 grep L2 csv 真实列名——`recall_channels`/`n_channels`/`lenses` 哪个在,用真实列;缺列则跳过该规则并在返回 meta 注明）;③healthy lane 全入;④每召回通道按通道内名次 top-K 轮询填满到 target（K 自适应）。cut = df − kept。
- `prepare_l3_table(..., two_pass: bool | None = None)`：two_pass 生效时（默认 True,读 `load_user_config().get("l3")` 覆盖）先 triage 再走现有 delta/lane 渲染,表头加一行「pass1 分诊 {n_in}→{n_kept}(影子 `_l3_pass1_cut.csv`)」;cut 落 `context/scan/<date>/_l3_pass1_cut.csv`（至少 code,name,lane/channel,gbdt 名次列）。two_pass=False = 现行为逐字节不变（回滚杆）。
- user_config：`_TOP_WHITELIST` + `"l3"`,`_SUB_WHITELIST["l3"] = {"two_pass","pass1_target","finalist_max"}`,apply 透传 tuple 加 `"l3"`。

**Steps:** ①先读 l3_select.py:122-266（load_l3_input/compact/prepare）与一份真实 `context/scan/2026-07-09/L2_gbdt_top200.csv` 列名 → ②失败测试（kept 含 pinned/共振/healthy 全入 + target 截断 + cut 无遗漏无重叠 + two_pass=False parity + 白名单三连）→ ③实现 → ④绿 + 真数据冒烟 `uv run --no-sync python -c "...prepare_l3_table('2026-07-09', two_pass=True)"` 看表头行与 cut csv → ⑤ruff。

### Task 2: merge v3 + bench 账本 + write_finalists 收窄

**Files:** Modify `autoresearch/scan/agents/l3_select.py`（`merge_l3_finalists_v3` 新函数;`write_finalists` 改调 v3 并 clamp）;Test `tests/scan/test_l3_merge_v3.py`（新）

**Interfaces (consumes/produces):**
- judged JSON 每元素新字段 `finalist`（bool,T3 的 l3-rank 会写;**缺列向后兼容**）。
- `merge_l3_finalists_v3(judged, budget: int, finalist_max: int = 10) -> tuple[pd.DataFrame, pd.DataFrame]`（finalists, bench）:cap = min(finalist_max, budget);取 `finalist==True` 行 → 守卫序:conviction≥75 未标 finalist 的强制补入（保险,记 `guard` 列="ins75"）→ conviction<55 的剔除（记 bench,`guard`="lt55"）→ 超 cap 按 conviction 截尾 → 健康画像不足 ceil(n/3) 且 bench 有够格(≥65 且 lane==healthy 或健康画像判定)则替换尾部票（`guard`="healthy_quota"）→ trend soft 2 席同理。**缺 finalist 列** → 全体按 conviction 排序取 cap,同守卫。bench = judged − finalists(含 guard 注记)。
- `write_finalists(date, budget, ...)`:调 v3;bench 落 `_l3_bench.csv`(全字段+guard);finalists.csv 照旧(pinned 注入不变,在 v3 之后);返回 dict 加 `bench_n`、`finalist_n`。

**Steps:** TDD:测试覆盖 ①finalist 标记消费 ②ins75 保险 ③lt55 拒绝 ④cap 截尾 ⑤健康比例守卫 ⑥缺列回退 ⑦bench csv 落盘&行数=judged−finalists。真数据冒烟:2026-07-09 judged(无 finalist 列)走回退路径出 ≤10 只。

### Task 3: l3-rank agent def v2（finalist tier 语义）

**Files:** Modify `.claude/agents/l3-rank.md`;Test `tests/test_agent_defs.py::test_l3_rank_anchors_present`（anchors 追加）

**要点（改写,保留 6 维 rubric/硬约束 B-E/防锚定铁律原文）:**
- 描述与正文:「比较式精排出 ~28 只」→「深比较后给出 **finalist tier:7–10 只**(数量看当天质量,`finalist:true`),其余入选写为 **bench**(`finalist:false`,仍全字段判断)——bench 是防漏影子,会被账本追踪,别把够格票藏进 bench」;主表行数说法 200→「~60(pass1 已分诊,影子在 `_l3_pass1_cut.csv`)」。
- 输出 schema(:34)加 `finalist`(true|false);规则行:「`finalist:true` 者 7–10 只,**conviction≥75 必须 true**(误杀保险,确定性层会强制)——除非硬约束 B/E 命中(此时写明);**conviction<55 禁止 true**;够格不足 7 只就出更少,禁止凑数」。
- 硬约束 A 改比例制:「finalist 中健康上涨画像 ≥ 1/3(有够格候选才凑)」。judged 总量仍 ~20–28(finalist+bench),bench 判断质量不许摆烂(账本会验)。
- anchors 测试追加:`"finalist"`、`"bench"`、`"≥75"`、`"宁缺毋滥"`。

### Task 4: workflow + gates + 文档

**Files:** Modify `.claude/workflows/scan-market.js`（meta.phases L3 detail、L3 段 :108-129）、`.claude/skills/scan-market/SKILL.md`（步骤 3,**编辑前重读**）、`.claude/skills/scan-market/STAGES.md`（L3 节）

**改点:** js:`const l3cap = Math.min(10, g1.l4_budget)`;L3-rank 派发 prompt 「目标约 ${g1.l4_budget} 只」→「finalist tier 按质 7~${l3cap} 只(finalist:true)+其余 bench;宁缺毋滥」;log 行同步;`finalists --budget ${l3cap}`、`gate2 --budget ${l3cap}`;GATE2 ✓ log 加 bench 数(若 gate2 JSON 有;没有就只改 finalists log,不动 gate CLI 契约)。SKILL 步骤 3:两遍法+finalist tier+三件影子账本一句话;STAGES L3 节同步;注明「L3.5 闸=passthrough 保留为回测 harness,收窄职能已并入 L3(用户 2026-07-12 裁定)」。`node --check` + 引用 workflow 文本的测试回归。

### Task 5: 学习端——漏检归因 + retro 读数

**Files:** 先读 `autoresearch/learning/` 下 attribution 相关模块（grep `recalled_cut` 定位）;Modify attribution 分类 + retro 汇总;Test 对应测试文件追加

**要点:** ①attribution 阶段标签细分:命中 `_l3_bench.csv` 的赢家 → `l3_bench`(原 recalled_cut 细分),命中 `_l3_pass1_cut.csv` → `pass1_cut`;两文件缺失(旧日期)→ 现行为不变(presence-gated)。②retro/汇总加两行读数:「bench top-5(按 conviction) fwd_2 均值 vs finalists fwd_2 均值」与「pass1_cut 中 T+2 赢家数」——**这两行是"收窄没吃好票"的日常法庭**。落点跟现有 retro 报告/attribution 汇总同文件同风格。TDD。

### Task 6: 全量回归 + 终审 + 收口

全量 pytest+ruff+node --check;真链冒烟(2026-07-09 隔离副本:prepare two_pass 表头行/cut csv/write_finalists 回退路径 ≤10 只/bench csv);review-package 终审(整波);ledger/记忆/设计稿状态。
