# stock-research 下一代设计稿(2026-08-31):实时证据 × 短线精度 × 现场留存 × lite 复用 × 双引擎一致

> **状态**:brainstorm 产物,待用户裁决(§7);未实施、未提交。
> **五问**(用户 2026-08-30 提出):① 如何用更实时充分的信息;② 如何研究得更精准、达到市场顶级短线研究员水平;③ 如何保留整个研究现场以便复盘;④ 如何方便 scan-market 的 lite 复用;⑤ 如何让 Codex 一致使用。
> **证据底座**:三路只读代码审计(A=取数、B=接缝、C=capsule/contracts/Codex)+ 五路外部调研(W1=LLM 研究系统、W2=A股实时源普查〔本机真探针〕、W3=短线方法论与实证、W4=trace/provenance 标准〔本机真探针〕、W5=跨引擎 skill)——原件八份存 `docs/research/2026-08-31-stock-research-optimization/`,本稿只引结论。另含 08-30 双引擎真跑对比(同日同票 Claude/Codex 各出 full 报告)。
> 所有 `file:line` 为 2026-08-30/31 工作树快照(含未提交改动)。标 **[U]** = 未核实需探针。

---

## 0. 一句话

stock-research 今天是「**历史数据厚、当日盘后增量为零;卡片写得最厚、被读得最薄;研究做完即蒸发(零留痕、零事后记分);两引擎共享 skill 文本但不共享角色与证据**」。本稿的解法不是加 agent、加辩论、加思考预算(外部文献全部证伪这些方向),而是五件结构工程:**盘后增量证据包(已有权限的源接进来)· 卡片契约双写与死契约清理 · analyze 纳入 run capsule + 记分账本 · 执行前核对工具 · 任务包为唯一跨引擎接口**。全部遵守「LLM=可审计信息接口,决策权在确定性层」——这也是 2026 年文献唯一背书的角色分工(W1)。

---

## 0.5 边界(既有裁定,不重提)

| 裁定 | 对本稿的约束 |
|---|---|
| 主尺 `gap_c1_o2`(T+1 尾盘买→T+2 开盘卖)三次裁不换;有正期望才叫 BUY;BUY owner = E6 | 本稿**不做 alpha 主张**;所有新信息的用途 = 风险可见性 / 否决 / 执行核对 / 人审提示,不进召回、不推评级方向 |
| 08-28 隔夜普查 H0 未被推翻(报告能用族全负;收益随可成交性单调递减);08-21 L4 判断层显著负 | 「精准」定义为**更好校准的否决与执行纪律**,不是更会挑隔夜票(W3 把这升格为制度级结论:T+1 贴水 ≈−14bp/夜,顶刊) |
| learning 层退役(08-21):不重开任何「从历史学→回注」;lessons 只走人批 | 本稿的校准/记分账本是**只记不学**——记分≠学习,预注册+Brier 在文献里是"把预测钉在墙上"(W4 §5),不产生任何回注 |
| B 类改动冻结到 09-中攒 20 结果日(08-26 A0) | 改 **scan 判断层输入**(slim/L4 prompt/门)= B 类,冻结;改 **full 档 / 独立 lite / 仪器 / 留痕 / 契约** = I/M 类,冻结窗正是做它们的窗口 |
| `scan_config.jsonc` 唯一流程参数事实源;双引擎隔离只共享 `lake/`;新产物先进 `contracts.ARTIFACTS` 再写代码;数据契约 A 级抛/B 级记账 | 本稿全部新源一律 **B 级 + presence-gated + 记账**;新参数三件套;新产物先登记 |
| 复盘/反馈流程不得改 `.claude/**`(08-13) | agent def / playbook 改动全是人批设计稿改动;A2 的 def 生成块改 prompt 字节须人批并留 `prompt_hashes` 痕 |
| 08-29 全覆盖稿(路 A 七构件)= 架构调度权威;其 Q1–Q12 部分已裁(A1/A2/A7 scan 侧已实施) | 本稿是 A2/A3/A4/A6 在 **analyze 侧的实例化**,不另立架构路线;08-29 的 Q10(stock_state 端口/研究日历)、Q11(新闻层)本稿只对齐**不重裁** |
| 期权只进研究/描述阶段;readthrough 不表因果;情报站盲搜防锚定;L4 复用不恢复 | 照旧 |

---

## 1. 现状(全部是本次审计/实测到的)

### 1.1 取数层:36 端点、0 走湖、盘后增量为零(A 报告)

- `analyze/harvest.py` 2,058 行 71 函数,36 个取数行:**0 行经 `cache.get_or_fetch`**(全绕湖直调)→ 法证 `source_lineage` 对 analyze 零可见、replay 不可能 FULL;**29/36 行是"T 级"降级**(失败只变成 markdown 斜体 `_ERROR fetching…_`,不 `record_degradation` 记账,`harvest.py:1643-1651`)——正是 `data/contracts.py:251-264` 立案要防的"降级不留痕"。A 级阻断 = 0 处;离线跑会写出一份全 `_ERROR_` 的"成功"文件。
- **PIT 漏洞 8 处**(回填历史日期即前视):`^VIX period="5d"`(`:1403`)、`margin_trend_ts end=now`(`uzi_lenses.py:301`)、`fina_indicator/stk_holdernumber/pledge_stat/forecast` 取最新行不锚 curr_date、`earnings_quality/solvency` 未过 `filter_financials_by_date`、`.info` 族全部"此刻"。
- **对隔夜窗口(T+1 尾盘买→T+2 开盘卖),T 日 15:00 后产生的信息一条都不取**:公告正文/互动易/龙虎榜席位/涨停·炸板·连板梯队/题材归属/北向·两融当日/研报评级变化——20 类盲区逐项见 A §2。full 档把这些整体交给 cap 12 的盲搜情报员;**lite 档连情报员都没有**。
- **full 档 45% 字节 = 400 天 OHLCV + 30 天指标序列**(决策密度最低的两块);而 08-28 D-3 新加的外源块在 A 股上**实际恒空**:readthrough 映射表 0 条 active、IV 分位恒"样本不足(0)"(无人写湖)、gnews 用 yfinance 英文名搜中文只回 2 条(`harvest.py:957`)。
- 杂病:assemble 校验是软的(`parse_rating` 找不到评级**默认返回 Hold** 不报错,`rating.py:42-46`;不查 `FINAL TRANSACTION PROPOSAL`);`_ashare_name_from_context` 日期格式错配永远找不到文件(`assemble.py:127` vs `harvest.py:2051`,"A股务必带 --name"是在绕这个 bug);A 股新闻块不过 14 日窗、不去重(实测样本含 40 天前旧闻与重复条);full 档 fwd-PE 因 `price=None` 恒不算(`harvest.py:2042`);两套 OHLCV 成交量单位差 100×(yfinance 股 vs tushare 手)只有价格坑记档;slim 对 scan 的 `_SLIM_ANCHORS` 四标题无 harvest 侧测试;slim 读 L1 的 16 个列名耦合未进 contracts。

### 1.2 接缝:24 组正则、20 个消费者、独立跑零消费者(B 报告)

- **一张 markdown 卡被 20 个消费者用 24 组各自维护的正则拆**(B §2 全表)。契约层 `contracts/agent_output.py` 已声明唯一真身(08-29 P0)但**执行侧未切换**:评级解析仍是"泛正则+全文第一个评级词兜底";OW 三门段只认 `✓/✗` 两个字形,写 `✔✘×` → 判 **PASS**(`parsers.py:305-331`);早停行格式一错 → 整卡被归 L4_RUBRIC、0 买归因整桶挪位;ensemble JSON 经 gp_shell heredoc 写、格式坏静默吞。
- **死契约成堆**:`独立初判` 行自称"机读契约 `chk_blind_pass` 核在场"——**全仓已无 chk_blind_pass**(随 learning 层退役,grep 零命中),模板还在逼 agent 写;断言分级三标签只有 `〔转引标题〕` 一个被消费;`持仓管理` 节零 python 读点;`rubric_rating()` 纯函数零调用(评级全靠卡自报);停因红灯集混入闭集外条目永不可命中;卡面 `FINAL TRANSACTION PROPOSAL` 行在 python 决策链上零消费(E6 用的是由评级派生的值)。
- **卡写出的可执行信息几乎全被丢弃**:入场/目标价/止损/R:R/置信度/conviction/执行线阈值无一进账本或被事后核验——outcome 只吃 rating + 停因两个字段(`outcome.py:156-206`)。「卡片→执行→复盘」闭环里,卡片这头写得最厚、被读得最薄。
- **独立跑 lite**:输入无源(无漏斗简报/L3 前提/intel/档案注入/市场地形——模板的 P0、L3 论点裁决节无从填),输出零消费者(`_lite.md` 全仓零 python 读点;不进 capsule/账本/lint/δ);SKILL 说"可用 scan.assemble 校验"是**文档幻觉级接线**;且 `AUTORESEARCH_RUN_ID` 在位时独立 harvest 的 slim 会写进**别人 run 的法证现场**(`workspace.scan_input_dir` 分支,`workspace.py:125-130`)。
- 模板双真身(l4-card.md ⇄ lite-playbook.md)靠 ~20 个子串锚同步,**数字预算已漂**(早停卡 ≤44 行/≤36 行、满卡 4.5K/3K 两边各说各话);lite-playbook 末节整段内嵌 scan 内部件(含已死的 verify.csv)。tripwire_watch 跨日找昨卡在 run 分区下疑似结构性落空(K4 修法没同步到 `tripwire_watch.py:94-103`)[U 待真跑核]。

### 1.3 现场留存:analyze 零留痕;scan 侧判断层证据也还空着(C 报告)

- **capsule 是 scan-market 专属**:`begin_run` 硬拒其它 kind(`capsule.py:428-429`),七处 `scan` 字面量(load_run 根/report 校验/ledger/failed/archive/repairs/run_mode 来源);**没有 analyze 的 profile/stage/role/mode 词汇**;contracts.ARTIFACTS 零 analyze 行(analyze 的产物名反而躺在"非产物白名单"里);drift 守卫不扫 `autoresearch/analyze`。
- **stock-research 独立跑 = 零留痕**:harvest/assemble 不开 run、不过 exec_capture、不记 lineage;主会话写 14+ 段的工具调用与网查、company-intel/us-intel 的网查,除稿件自报"网查 N 条"外无任何机器可读留痕;为独立技能预留的 `materialize_report_trace(context="stock_full")` 生产零调用者;analyze 报告 manifest 只有 6 键(ticker/name/market/date/generated_at/hhmm)——无 run_id、无代码/prompt hash、无降级账、无引擎。
- **scan 侧同病的另一半**:`bind_transcript` 全仓无生产写者 → 即使 scan,判断层的 transcript/网查/计量证据也是空的,`completeness_ok` 恒假;`usage_harvest.collect_run` 不传 session_ref → Claude token 计量按码读为零行 [U];**Codex adapter 把 `web_search_call` 整类丢弃**(`transcripts/codex.py:37-39,292`)→ Codex 原生网查在 `external_tools.jsonl` 恒零行。
- W4 本机实测补两个收割盲区:subagent transcript 在 `<sid>/subagents/agent-*.jsonl`;**超大工具结果 spill 到 `<sid>/tool-results/`**(本 session 实测 1.7MB WebFetch PDF 只存在于此)——adapter 不收这两个目录,"现场"缺页。

### 1.4 Codex:skill 文本单真身 ✓,角色与证据不一致(C + W5)

- skill 已软链单真身(`~/.codex/skills/* → .claude/skills/*`),`$CTX/$RPT` 路径约定 + AGENTS.md 38 行是全部 Codex 侧指引;**10 个 `.claude/agents/*.md` 零 Codex 等价物**(`~/.codex/agents/` 空目录)——Codex 主会话自演全部角色,情报员的**结构性盲**(独立 context、无 Read)退化为指令级自律。
- W5 核实(官方文档):**Agent Skills 已是开放标准且 Codex 原生支持**(发现路径 `.agents/skills`,官方支持 symlink;frontmatter 公共子集只有 6 字段);**Codex 2026-03 起有原生 subagent**(`.codex/agents/*.toml`,name/description/developer_instructions 必填)与编排工具 spawn_agent/wait_agent;`codex exec --json --output-schema` 可做结构化叶子;有 11 种 hooks;**`web_search` 默认 `cached`(预索引快照非实时)**,情报场景必须 `live`;`codex exec --ephemeral` 完全不落 rollout。本机软链在旧路径 `~/.codex/skills`,现行标准位是 `.agents/skills` [U 需探针本机版本认哪个]。

### 1.5 双引擎真跑对比(2026-08-30,同日同票,地面真相)

| 维度 | Claude(中际旭创 300308) | Codex(同票) |
|---|---|---|
| 报告体量 | 100,136B / 991 行 | 27,649B / 507 行(≈1/4) |
| 段稿体量 | 1.7–8.7KB/段 | 0.3–3KB/段(variant.md 仅 323B) |
| 评级/方向 | Hold(EV +5.3%,至 base R:R≈0) | Hold(EV +8.9%,至 base 0.54)——**结论一致,论据同源** |
| 报告内 URL | **0 个**("实时网查"字样 13 处却无一落 URL) | 6 个 |
| `_company_intel` | 8.6KB / 24 URL(全 T2 媒体) | 3–5KB / 18 URL(**T1 更多**:巨潮/深交所 PDF、工信部) |
| 杂物入侵 | `.omc/state/last-tool-error.json` 落进 `1_analysts/` | `_work/{task_plan,findings,progress}.md` 落进研究目录 |

读法:确定性层(69KB context 两边同源)保证了**结论**一致;差在**厚度、引用纪律与杂物**——这三样恰是"契约没写死"的部分。深度差异是否影响质量需金集对照(D9),不能只按字节判。

### 1.6 与在册设计稿的关系

- 08-29 全覆盖稿的 A3(`stock_pack`)、A6(`ws.stage_dir(kind∈{…,analyze,…})`)、A2(def 生成块/IntelContract)、A4(端口)在 analyze 侧的实例化 = 本稿 D1/D6/D8;调度权威仍归 08-29 稿。
- 08-28 外源稿的 D-5 第二个 adapter("full-report publish 的 web_budget 覆盖")验收**未满足**——本稿 D6 认领。
- 08-28 三问稿的 R3(14:45 执行前核对)本稿 D3 认领 stock 侧;其 G0 账本地基、G5 citation audit 与本稿 D5/D7 同向。

---

## 2. 外部调研十件事(浓缩;原件见附录 A)

1. **端到端 LLM 交易 alpha 已被系统性证伪**(TradingAgents 宣称 Sharpe 8.21 → 净化复现 0.22;泄漏主导回测;20 年长窗否定择时)——本仓「LLM=信息接口、决策在确定性层」是文献唯一背书的分工。**评测协议 P1–P6**(时间完整性/动态池/反事实鲁棒/校准/现实摩擦/多 agent 拆解)可直接当我们复盘协议的模板。(W1,强)
2. **辩论堆轮数、反思记忆环、加大思考预算,三路全负**;唯一稳定增益是**视角异质**;extended thinking 对交易决策 ≈0——深度预算应流向**取证与核算**。(W1,强;与 08-21 退役裁定互证)
3. **schema 合规 ≠ 数值对**(结构化输出值正确率上限 ~83%):契约层管形状,**每个进决策的数字必须由确定性脚本产生或复核**。(W1,强)
4. **头部商用研究 agent 的质量地板 = 句级精确引用 + 双层验证**(Brightwave 字符区间/bbox 级;Hebbia 每主张回链原文引句)——"引用可解析率"已是研究质量一等公民指标。今天我们 full 报告 0 URL,是最大反差。(W1,强)
5. **A 股隔夜为负是 T+1 制度贴水**(顶刊,≈−14bp/夜,20 年稳健);涨停续涨集中在开盘 gap、尾盘能买到的=没封死的=无续涨;纯"T+1 尾盘新开仓"学术正证据 = 0——**"0 BUY 诚实"在文献里是最优策略**。(W3,强)
6. **顶级短线研究员 = 4–6 小时确定性复盘流水线 + 极少出手**,能力清单 12 项,其中**情绪面板/涨停性质标注/席位画像/资金总量/盘中风控 5 项完全可编码**(湖里大半已有);复盘的交付物是**次日 if-then 预案**,不是观点。(W3,强)
7. **基率先行是最大单项改进**(GJP:Brier 0.17 vs 0.26);检索密度是置信的物理量(检得 ≥5 篇才许高置信);LLM 高置信端(≥90%)仍错 15–32%,须打折/双复核;kill criteria = 状态+日期。(W3,强)
8. **三个新格子有外部正证据、可零 LLM 本地复算**:首板缩量回调低吸(华安金工 OOS 正、91% 持仓 1 天、滑点敏感)、14:00–15:00 晚封板次日溢价最高(社区统计,恰是 14:45 工具触达时段)、机构席位上榜 5–20 日窗正。(W3,中)
9. **本机已有权限而 analyze 未用的实时源是一座矿**:tushare `major_news`(隔夜窗实测 604 条/晚,pub_time 到秒)、`kpl_list/kpl_concept`(涨停原因/题材梯队,T+2 8:30)、`report_rc`(每晚 19–22 点券商预测增量=隔夜预期差)、`rt_min`(**实时分钟线**)、`limit_list_d`/`top_list/top_inst`/`hm_list`(游资名录)/`moneyflow_ind_dc`/`hsgt_top10`/`block_trade`;免费直连:巨潮公告流(无小时戳,须自记 first_seen)、新浪 7x24、华尔街见闻 lives、东财快讯、A50 期指夜盘(`hf_CHA50CFD`)、akshare 涨停池族(push2ex 未被封)、互动易。**付费缺口三件**:`anns_d`(公告精确时刻)、`news src=cls`(财联社结构化)、`limit_list_ths`(16:00 涨停原因)。开源同行全靠搜索 API 兜底,没有人打通公告/财联社直连——接上即领先。(W2,本机实测)
10. **现场留存对齐目标**:OTEL GenAI 词表"采名不采版本承诺"(全部仍 Development);OpenInference `retrieval.documents.*` 最适合表达证据链;Inspect EvalLog 是"离线单文件全事件"的成熟范本;**网页原文在两引擎里结构性不可得**(Claude WebFetch=小模型摘要;Codex 只存 snippet)→ L2 原文只能靠**自建 fetch 工具进 blobs CAS**;completeness 应分 **L0 存在性/L1 摘要视图/L2 原文** 三级;replay 可加第四结论=决策一致率(实测决策确定性与准确率不相关 r=−0.11,须独立度量);监管方向(SFC 24EC55/FINRA)与 capsule 同构。(W4,强+本机实测)

---

## 3. 设计原则(从证据推出,五条)

1. **研究产品 = 否决 + 风险可见性 + 执行条件 + 校准的判断**,不是选股。alpha 主张一律不做(§0.5)。"顶级短线研究员水平"翻译成工程语言:**看得全**(他们看的确定性面板我们也看)、**判得诚实**(基率先行、检索密度定置信、高置信打折)、**交付预案**(if-then,可机械执行可精确对账)、**事后记分**(Brier,只记不学)。
2. **时间锚是一等公民**:每类证据带 `data_as_of / available_at / decision_cutoff`;`available_at > cutoff` 的进 `late_evidence` 不进正文。两个 cutoff:`card_cutoff`(T 晚写卡)与 `exec_cutoff`(T+1 14:45)——卡片距执行约 20 小时,**中间落地的信息只能由确定性工具(D3)接住,不能靠重写卡**。
3. **契约优先、双写分工**:markdown 给人、JSON 给机器;一份声明(contracts)生成两侧(def 机器块 + 解析 schema + Codex 角色文件);数字由确定性层产生或复核,LLM 只许引用。
4. **现场三问对齐 capsule 三结论**,并升级:完整性分 L0/L1/L2 三级;新增第四观察(决策一致率,远期);**没有事后读数的现场不叫可复盘**——账本与记分是现场的另一半。
5. **引擎是叶子的宿主,不是流程的主人**:任务包(文件)是唯一跨引擎接口;编排、循环、合并全在确定性 Python;两引擎各自的原生能力(Claude subagent/Workflow、Codex subagent/exec)只做"读任务包→写产物+JSON"。

---

## 4. 目标形态(一张图)

```
                      ┌────────────────────────────────────────────────┐
                      │  contracts/(A1/A2 已有,本稿扩 analyze)          │
                      │  stages(kind=stock-research)· ARTIFACTS(analyze)│
                      │  agent_output(card.json schema · def 生成块)     │
                      └──────┬─────────────────────────┬───────────────┘
                             │ 生成                     │ 生成
              ┌──────────────▼──────────┐   ┌──────────▼─────────────┐
              │ .claude/agents/*.md     │   │ .codex/agents/*.toml   │
              │ (机器契约块=生成物)      │   │ + AGENTS.md 附录(生成) │
              └──────────────┬──────────┘   └──────────┬─────────────┘
                             │ 消费同一任务包            │
   确定性层                   ▼                          ▼
   evidence: stock_pack ──► 任务包 _task_<code>.md ──► 引擎叶子(Claude subagent / codex exec)
   (full/slim 同构建器,     (共享块+逐票块+可选段:      │  写 卡.md + card.json / 报告+report.json
    D-3 外源 + D2 盘后增量,  简报/intel/档案/基率行)     ▼
    全走湖+记账+PIT)                            analyze run capsule(D6)
        │                                       identity/exec/lineage/blobs/transcripts
        │ T+1 14:30                             external_tools(网查 L1)+ fetch 工具(L2)
        ▼                                               │ finalize
   exec_check(D3,确定性):rt_min 分时+公告增量+执行线 ──► │
   go/no-go(20 日不可见影子)                             ▼
                                                analyze 账本(D5,只记不学):
                                                卡→隔夜尺;情景概率→Brier;评级分布对照
```

---

## 5. 构件设计(D1–D9;每件:现状→目标→落点→验收→回滚→类别)

> 类别沿 08-26 约定:**I**=仪器/留痕/契约(不改判断层输入输出语义)、**M**=修 bug/机械等价、**B**=改判断层输入或用户可见决策行为(冻结到 09-中)。**改 prompt 字节但语义不变**的按 08-13 裁定须人批留痕,记 I(人批)。

### D1 · `stock_pack`:取数层证据包化(A3 的 analyze 实例)

- **现状**:§1.1。36 端点绕湖、29 处 T 级降级、8 处 PIT、单文件 2,058 行四象限散在 12 个 if 里、45% 字节是历史序列。
- **目标**:
  1. `autoresearch/evidence/stock_pack.py`(或 analyze 包内先行,搬家跟 08-29 A3 总表走):`load_stock_pack(request, reader) -> RawInputs`(全部 IO,逐端点经 `cache.get_or_fetch`)+ `build_stock_pack(raw, decision_cutoff, profile) -> Pack`(纯函数)+ `render_md(pack, profile)`。**full/slim = 同一构建器两个 profile**,块=函数;`main()` 的四象限 if 改为「(market, tier) → 派发清单」一张表(消灭 SKILL.md:38 那份会漂的手抄清单)。
  2. **PackMeta 时间信封**:`schema_version / data_as_of / available_at / received_at / decision_cutoff / sources[(endpoint,key,hash)] / degradations / freshness`;`available_at > decision_cutoff` 的输入落 `late_evidence` 不进正文。
  3. **降级三态归一**:29 处 T 级 → B 级(`record_degradation` + 块内降级行);「源成功真空」「请求失败」「无权限」三态分开(照 `edgar.FetchOutcome` 四态纪律);报告尾部新增确定性「数据降级账」一节(哪些块没取到,读 degradations——今天读者永远看不见)。
  4. **PIT 8 处修复**:全部锚 `curr_date`;`.info` 类实时字段标注 `as_of=now` 并在 PackMeta 里声明「不可回放」。
  5. **full 瘦身**:400 天 OHLCV → 「日线近 60 + 周线 52」双表(信息保真、字节 ≈−70%);12 指标 30 天序列 → 末值+形态摘要行(序列进 deep 附件,按需读)。省下的预算给 D2 增量块。(幅度待裁 Q7)
  6. 顺手修:assemble 硬校验(必需行缺失 exit 1;`parse_rating` strict 档;查 `FINAL TRANSACTION PROPOSAL`)、`_ashare_name` 日期格式 bug、A 股新闻窗过滤+去重、fwd-PE 把同进程 close 传进去、gnews 用中文简称、`_SLIM_ANCHORS` 与 L1 列名依赖登记进 contracts(ports)、`AUTORESEARCH_OFFLINE` 尊重、macro/harvest 三处 Verbatim 复制回收。
- **落点**:`autoresearch/analyze/`(harvest.py 保留薄 shim 一轮)+ `autoresearch/data/endpoints.py`(缺的键补登记)+ `contracts/artifacts.py`。
- **验收**:①对冻结日期 golden(08-26 capsule 冻结 slim / 08-30 full 样本)**byte-parity**(允许清单显式列出因修 bug 而变的行);②合成用例:断网跑 → 每块降级入账且 exit 码反映 A/B 级;③PIT 红用例:回填日期跑,`^VIX`/`margin` 等 8 处不再取"今天";④`uv run python -m autoresearch.analyze.harvest 300308 <date>` 真跑一次对比耗时(现 ≈40s+,目标不劣化;顺手并发化可选)。
- **回滚**:shim 层一键切回旧 `main()`。
- **类别**:M(bug/记账/PIT)+ I(走湖/拆分/瘦身;瘦身改了 full 报告输入的形状,但 full 无 BUY 所有权,不动冻结样本钟)。

### D2 · 盘后增量块族:把「T 日 15:00 之后的世界」接进证据层

- **现状**:§1.1 第 3 条——确定性层对盘后增量覆盖为零;情报员盲搜在替确定性层补课(每票 $0.56 查接口本可免费拿的东西,08-29 §4.4 同一诊断)。
- **目标**:新增 8 个块(全 B 级、presence-gated、走湖、带 `available_at`),按 W2 实测排优先:

| # | 块 | 源(已实测) | 落地时点 | 进哪档 | 用途(全部描述性/否决/日历) |
|---|---|---|---|---|---|
| ① | 公告增量(T日 15:00 后) | 巨潮 `hisAnnouncement/query` + `disclosure` 最新流(免 key;**自记 first_seen 补小时戳**);tushare `anns_d` 若购权限则升级精确时刻 | 15:00–23:00 主峰 | full + 独立 lite + exec_check | 催化/风险事件的**官方源**;标题分类(减持/问询/立案/中标/业绩)进 tripwire 事件旗对账 |
| ② | 隔夜新闻窗 | tushare `major_news`(已有权限,604 条/晚,pub_time 到秒;窗=T 15:00→T+1 09:30) | 全夜 | full + 独立 lite | 给情报员当**已知底**(它只查接口拿不到的);新闻→`news_catalog`(08-29 Q11 接线方向,不重裁) |
| ③ | 券商预期差 | tushare `report_rc`(已有权限,每晚 19–22 点增量,评级/目标价) | 21 点前后 | full + 独立 lite | 「隔夜预期变化」事实行;修 A §5 缺口 2(目标价变化不再恒 UNMEASURED——自建逐日快照) |
| ④ | 情绪面板(W3 能力项 1) | akshare `stock_zt_pool_em` 族(实时)+ tushare `limit_list_d`(盘后全字段) | 盘中/盘后 | full + 独立 lite + exec_check | 涨停家数/连板高度/炸板率/昨日涨停溢价 → **描述性市场地形**(regime 已有,补微观情绪四数;不进评级) |
| ⑤ | 题材梯队 | tushare `kpl_list`(涨停原因 lu_desc/连板 status/封板时刻)+ `kpl_concept`(题材×涨停数) | T+2 8:30(次晨) | full + 独立 lite(T 晚卡用 T−1 数据并明示滞后) | 「本票属于哪个题材、梯队第几、题材第几天」事实三行(W3 能力项 2/3 的确定性半)|
| ⑥ | 席位与游资画像 | tushare `top_list/top_inst` + `hm_list` 游资名录(已有权限) | 盘后 17–19 点 | full + 独立 lite | 席位名+游资映射(现只有机构/非机构聚合);**方向铁律照旧**(机构上榜多日窗温和正、游资负——引普查与 W3 §3) |
| ⑦ | 外盘隔夜 tape | 新浪 `hf_CHA50CFD`(A50 夜盘)+ 既有 yfinance 美股;`index_global_spot_em` | 17:00–次日 5:15 | full + exec_check + 📌 哨兵 | 隔夜跳空可见性(08-28 外源稿"两窗"的 tape 腿;不推方向) |
| ⑧ | 互动易增量 | akshare `stock_irm_cninfo`(问答到秒) | 准实时 | full | 董秘口径变化;公告①的软补充 |

- **纪律**:每块换算成「事实行」进 pack;**不做**的照旧不做(股吧帖子=假数据型反爬、微博/百度指数、财联社逆向签名——正路是买 tushare news 权限,Q6)。scan 侧 slim/L4 prompt **一个字不动**(B 类冻结;解冻后按 08-29 Q11/A8 一并裁)。
- **落点**:`data/sources/`(新源模块,B 级契约)+ `data/endpoints.py` 登记 + `stock_pack` 块函数 + `contracts.ARTIFACTS`。
- **验收**:①每源一条探针测试(合法空/失败/成功三态);②「时间线还原」用例:对一个真实交易日,`available_at` 逐块落在 W2 §③ 时间线的正确时段;③情报员已知底对照:接①②后 company-intel 稿件与确定性块的重叠率(预期显著下降,情报预算转向接口拿不到的:电话会口径/产业链传闻/政策解读)。
- **回滚**:块级 presence-gate,逐块可关。
- **类别**:I(full/独立 lite/仪器)——**scan slim 注入是 B,另行冻结后裁(Q3)**。

### D3 · `exec_check`:T+1 14:30 执行前核对(确定性工具,认领 08-28 三问稿 R3 的 stock 侧)

- **现状**:卡片写成距执行 ≈20h;执行线两行阈值写在卡上但「解析后故意不判」(B §2 #16);14:45 前无任何工具帮人核对「卡片前提今天还成立吗 + 盘中出了什么新东西」。
- **目标**:`python -m autoresearch.analyze.exec_check <code> [--date]`(零 LLM):
  1. **盘中确认**:tushare `rt_min`(已实测有权限)拉当日分钟线 → 现算 `pct_chg`/`pos_in_range` 判**执行线**两条(达标/放弃,用 `outcome.EXEC_MAX_*` 同一常量——终结"卡值零消费"的双份漂移);盘口异动摘要(akshare `stock_changes_em`,可选)。
  2. **增量事实**:T 日 15:00 → 此刻的公告增量(D2①)、当日情绪面板四数(D2④)、A50/美股隔夜读数(D2⑦)、本票 tripwire 三型线对今日湖数的命中。
  3. **输出**:一屏 go/no-go + 事实行(全描述性,**不改评级、不改卡**);同时落 `exec_check_<code>.json` 到 `$RPT/analyze/_ledger/exec_checks/`(**run 外、append-only**——写卡的 run 在 T 晚已 finalize/冻结,冻结后的 capsule 不得追加文件,08-28 外源稿 §3 不变量;账本行引用原 run_id 对账)。
  4. **影子纪律**:前 20 个使用日**不可见影子**(只落盘不展示,或收盘后才可看——对齐 08-28 三问稿 Q3 的裁法),避免未经裁决影响交易。
- **落点**:`autoresearch/analyze/exec_check.py` + ARTIFACTS 登记;不进 scan 任何阶段。
- **验收**:①对 08-26 run 的两只票回放(用历史分钟线)手工核对执行线判定;②影子期结束后出一页读数(它拦了什么/放了什么 vs 事后隔夜尺)再裁展示(→B)。
- **回滚**:独立 CLI,不接任何自动链,删即回滚。
- **类别**:I(影子)→ B(展示,解冻后)。

### D4 · 卡片/报告 v5:把「顶级短线研究员」的判断结构写进契约(全描述性/否决器)

- **现状**:lite 卡已有预期差/多空自压/开盘预案/执行线/盯梢线;缺的是 W3 清单里的**基率、情绪地形、题材位、接力盘、检索密度、日期腿**;full 报告缺句级 URL(0 个,§1.5)。
- **目标**(模板改动全部人批;字段同时进 `card.json` schema,D8):
  1. **基率行(新,卡顶)**:`基率: 本票今日画像=<格子名> ｜ 普查隔夜基率 <x pp>(n=…) ｜ 例外机制:<一句,答不出写"无——回落基率"`。格子表来自隔夜普查读数的**静态快照文件**(版本化,人批更新;不是动态学习——Q8)。这是 GJP"外部视角"的工程化:先报底色,再谈例外。
  2. **情绪地形行(新)**:涨停家数/连板高度/炸板率/昨日涨停溢价 四数一行(D2④ 事实,不判)。
  3. **题材位三行(新)**:题材名/梯队位/题材第几天(D2⑤ 事实;数据滞后一日时明示)。
  4. **接力盘一问(新,满卡)**:「T+2 开盘**谁来买**?」——接力画像(题材内位置/席位史/辨识度)答不出 = 预期差节直接写「无买方画像」。这是 W3 §④13 的迁移:隔夜的对手方是别人的注意力。
  5. **检索密度置信门(新,数据级)**:卡面置信度=高 要求 intel/网查独立源 ≥N(N 进 scan_config;lint warn 级起步)——Halawi 的"检索密度是置信物理量"。
  6. **kill criteria 补日期腿**:盯梢线 `[日期线]` 由可选升为满卡必有一条(「若 X 未在 Y 日前出现则论点过期」),防 thesis 无限展期。
  7. **死契约清理(减法)**:删「独立初判」机读声明(chk_blind_pass 已死;盲读纪律留在流程文字里)或给它一个真消费者(D8 的 lint),二选一;停因红灯死条目修正;断言分级三标签要么接 lint 要么降为写作指引。
  8. **full 报告引用硬契约**:「网查」级断言必须落 URL 进正文(engine-playbook 铁律升级)+ 确定性 `citation lint`(URL 可解析率;对照 intel 稿 URL 集合)——修 §1.5 的 0-URL 反差;远期对齐 W1 的"句级坐标"(capsule lineage 已有读点,回填正文引用标记)。
  9. **预注册概率**(与 D5 配套):三档情景概率已有——加一行机器可读 `情景概率: bull p / base p / bear p`(现散在文字里),供 Brier 记分。
- **明确不加**:辩论轮数、更多 agent、更长思考、任何"模型觉得会涨"——W1 别做清单。
- **落点**:`lite-playbook.md` + `engine-playbook.md` + `l4-card.md`(经 D8 生成块)+ `contracts/agent_output.py` 字段。
- **验收**:模板变异探针(删基率行 → lint 红);两张真卡(一票有题材/一票无)人工评审;prompt_hashes 留痕。
- **回滚**:模板行级回退。
- **类别**:I(人批;scan 侧 l4-card def 的变更**建议与 scan 解冻同批上**,独立 lite/full 先行——Q3)。

### D5 · analyze 账本与校准记分(只记不学;「现场」的另一半)

- **现状**:独立卡/报告零事后读数;scan 账本只吃 rating+停因;情景概率/目标带从不被记分;评级分布无对照尺。
- **目标**:
  1. **`analyze_ledger`**(`$RPT/analyze/_ledger/`,append-only):每张独立 lite 卡/full 报告一行:run_id、rating、proposal、情景概率、目标带、执行线、tripwire 摘要 + 事后列(隔夜尺 gap_c1_o2 for lite;fwd_5/10/20 与情景带命中 for full)。填数挂 nightly-close 第 5 步(它已活)。
  2. **校准派生表**(research/ 仪器,读 scan `_ledger` + analyze_ledger):按评级档 × 早停停因 × 格子的隔夜尺分布;**Brier/ECE**(情景概率 vs 实现);**评级分布对照**(vs Trading-R1 波动率调整五档基准 SB15/B32/H38/S12/SS3——检查 Hold 挤压/方向偏斜)。输出 markdown 报告,人读;**不回注任何 prompt**。
  3. **反事实翻转探针**(self_review 抽查,低频):随机抽已发布卡,注入反向证据摘要重打一次(独立 context),检查方向/信心变动单调性;结果只进诊断,不改当日产物。(W1 P3;成本可控:抽 1 卡/日)
  4. tripwire 事后核验:`[价格线]`/`[日期线]`/`[事件旗]` 命中率与「触发后实际动作」对账列(数据已在湖+账本,补派生)。
- **验收**:20 个结果日后首读一页(校准曲线 + 评级分布 + tripwire 命中);变异探针:往账本塞一行伪造预测 → Brier 计算变化可见。
- **回滚**:纯增量产物,删表即回滚。
- **类别**:I(账本/仪器)。**与 08-21 退役裁定的关系**:这里没有任何"从历史学→回注",只有记分;若未来有人想把校准表喂回 prompt,须另立 B 类设计并过用户裁决。

### D6 · analyze run capsule:现场留存补齐(C 报告三步 + W4 增强)

- **现状**:§1.3。
- **目标**(顺序即依赖):
  1. **契约先行(零运行器)**:`contracts/stages.py` 加 kind 维度 + analyze 词汇(stages:`harvest → intel → write → assemble → publish`,lite 为 `harvest → card`;roles:`company-intel`/`us-intel`(gated by 市场)/`stock-writer`(主会话,按 `_NON_TRANSCRIPT_ROLES` 或片段绑定);modes:`FULL / LITE`);`contracts/artifacts.py` 加 `analyze_staging/analyze_report` 两根,登记 11 段 + intel 稿 + slim 双件(从"非产物白名单"挪出)+ 报告 + manifest + card.json + exec_check;drift 守卫扫描根加 `autoresearch/analyze`;`analyze_profile()` 复用 `_BASE_RULES`。
  2. **capsule 去 scan 化**(七处字面量 → 按 `run_contract.run_kind` 派发):begin_run/bootstrap、load_run 根(`workspace.run_root(kind)`,对齐 08-29 A6 的 `stage_dir(kind…)`)、report 校验、ledger/failed/archive/repairs 路径、run_mode 来源(analyze 写进 RunContract)、finalize profile、replay 单元表(analyze 的 replayable=harvest,**前提是 D1 走湖**)。分层守卫注意:profile 声明进 contracts,避免 `analyze → scan.run_profile` 新向上边。
  3. **接线三点**:SKILL 的三条命令改经 `exec_capture`;harvest/assemble 进程内 checkpoint(照 `stage_result.py:191-217` 的现成路,**不照抄 tracedAgent 双壳**——08-29 §1.4/Q2);assemble 收尾 `finalize(report_dir=$RPT/analyze/<run>)`;manifest 富化(run_id/engine/model/cfg_hash/prompt_hashes/degradations 计数/intel 在场/web_budget 摘要)。
  4. **transcript 绑定成为流程步(双引擎同一句;不做则 completeness 永假——scan 同病同修)**:`capsule begin --session-ref <id>`;每角色收尾 `capsule bind-transcript <run> <path> --role … --invocation-id …`(Claude=subagents 路径;Codex=rollout `--from-ordinal/--to-ordinal` 片段——机制已实现零调用)。顺手修四件:`usage_harvest` 传 session_ref;**Codex adapter 停止丢弃 `web_search_call`**;adapter 收割面补 `<sid>/subagents/` 与 `<sid>/tool-results/`(W4 实测盲区);D-5 materializer 对 `context=stock_full` 的调用点落在 assemble/publish(补 08-28 外源稿未满足的验收)。
  5. **完整性三级 + 逃逸口**:completeness 对外部输入按 **L0 存在性 / L1 摘要视图 / L2 原文** 分级声明与报告("X% at L2" 替代布尔);capsule 模式禁 `codex exec --ephemeral` 与 `--no-session-persistence`;Codex `web_search` 配置值(cached/live)记入 identity。
  6. **词表对齐(低成本)**:events 负载加 `otel` 命名空间映射(`gen_ai.usage.* / gen_ai.tool.call.* / gen_ai.input.messages` 等,采名不采版本);lineage 字段起名参考 PROV-O(`used/wasDerivedFrom/…`)。远期:capsule 根放 `ro-crate-metadata.json` 自描述封皮。
- **验收**:①独立跑 `stock-research full` 一票 → `analyze_runs/<run_id>/capsule/` 三结论可算、`verify` 绿(合成);②lite 独立跑 → LITE profile expected 不要求 intel;③变异:删一个 bound transcript → completeness 红;④双引擎各跑一次同票,capsule 里两边都有 external_tools 行(Codex 含 web_search)——这同时是 08-28 三问稿 Q5"真跑验收"的 analyze 侧样张。
- **回滚**:analyze 不开 capsule 时一切照旧(begin 是显式步)。
- **类别**:I。
- **裁决点**:全 capsule vs 只走 `materialize_report_trace` 轻路(Q2)。本稿推荐**全 capsule**:机制已 kind 无关,轻路照样绕不开 transcript 绑定,而全 capsule 换来 replay/completeness/账本革命只差七处字面量。

### D7 · 网查证据 L2 主路 + hook 兜底

- **现状**:网页原文两引擎均不可得(W4);capsule `external_tools` 只到 HARNESS_RESPONSE(harness 给 agent 看的摘要);报告正文 0 URL(§1.5)。
- **目标**:
  1. **主路**:`python -m autoresearch.data.web_fetch <url>`(自建,B 级):原始 bytes → blobs CAS(sha256)+ 元数据(url/fetched_at/content_type/hash)进 `source_lineage`,同时产出给模型读的提要文件。**情报员与写手的"必须落 URL"升级为"关键 T1/T2 证据经 web_fetch 留原文"**(cap 内;A 股公告 PDF 优先——消亡最快的一类)。两引擎同一工具 → 顺带解决 Codex hosted 工具不可拦的问题。
  2. **Claude 侧 hook 兜底(立即可做)**:PostToolUse matcher `WebFetch|WebSearch` → 把 `tool_input.url/prompt + tool_response` 追加进当前 run 的 `external_tools.jsonl`(L1 级);顺带收 `<sid>/tool-results/*`。Codex 侧靠 rollout adapter 事后收 `web_search_end`(D6.4 修复后自动获得)。
  3. **兜底**:关键公告/监管页夜批推 Wayback SPN(免费异步,SPN2 参数 [U]);不做 WACZ 除非要对外分发。
- **验收**:一次 full 真跑后抽 5 条正文引用:URL→lineage→blob 三跳可达(L2);hook 兜底在 capsule 关闭时也能落 L1 行。
- **回滚**:工具可选;hook 单条可摘。
- **类别**:I。

### D8 · 卡片双写 `card.json` + 消费收口 + 模板单源(接缝的解法)

- **现状**:§1.2。
- **目标**:
  1. **双写**:卡 agent 写 md(人读,模板不减)同时写 `details/<code6>.json`(机器):`schema_version / code / rating / rubric{dims6, net, gates3} / proposal / early_stop{stage,reason} / scenarios{bull,base,bear:price,p} / ev / rr / conviction / confidence / entry_veto[] / exec_lines[] / tripwires[] / base_rate_row / evidence_refs[] / as_of / engine / model`。schema 单源在 `contracts/agent_output.py`,由 `emit` 生成:Claude def 机器块、JS CARD schema、**Codex `--output-schema` 文件**(写成两引擎交集:全 required + additionalProperties:false,W5 §5)。
  2. **消费收口**:五个 owner 函数(`_finalist_row / gate_status / parse_early_stop / parse_tripwires / _rating_of`)改「JSON 优先、md 解析回退 + 两侧对账 warn」;五个直接读卡者(publisher 注入/price_claims/citation_density/yesterday_echo/chain_view)保持读 md。**python 重算 rubric**:`rubric_rating()` 第一次有调用者——从 card.json 的 dims+gates 重算评级并与卡面对账(warn 进 gate_fires;20 日读数后再裁 python 权威,08-29 §4.5 同案)。
  3. **修脆弱点**(M):gate 记号容错 `✔✘×`;停因红灯死条目;ensemble JSON 不再经 heredoc(python 落盘);`parse_rating` scan 侧切 strict;执行线阈值单源(contracts 常量,卡面两行由模板引用同值)。
  4. **模板单源**:l4-card.md 的「机器契约」段由 `emit-def-block` 生成(A2 def 侧补完),lite-playbook 成唯一散文源;行数/字节预算数字进生成块(终结 44/36 漂移);lite-playbook 末节的 scan 内部件(含死 verify.csv)修正。
  5. **独立跑归位**:独立 lite 用同一 `TaskPackage` 构建器(可选段缺省=「独立模式」,模板的 L3 裁决节自动降级为「无上游论点」);卡落 analyze run 分区(D6)并进 analyze_ledger(D5);`scan_input_dir` 修「独立 harvest 不得写进活跃 scan run」(显式 `--run` 才入 run 现场)。
- **验收**:①对拍期:同卡 md/JSON 双解析逐字段相等(20 卡样本);②变异:JSON 缺 required 字段 → schema 红;③E6/brief/outcome 在 JSON 优先下对 08-26 真 run 回放 byte-parity;④独立跑一票 lite:输出有人接(账本行 + capsule)。
- **回滚**:JSON 优先开关一行退回 md 解析。
- **类别**:M(修脆弱点)+ I(双写/单源;**agent def prompt 字节变化人批**)。scan 消费链的行为不变(读数相同)故不属 B;若对账发现历史卡 JSON/md 分歧率高,先只 warn。

### D9 · 双引擎叶子协议 + 金集对照(Codex 一致性)

- **现状**:§1.4、§1.5。
- **目标**(W5 推荐「路 A 为主干 + 最小 C 子集」):
  1. **任务包 = 唯一接口**:full 档也引入确定性任务包(`_task_full_<code>.md`:pack 路径 + 段清单 + 输出契约 + 基率行素材)——今天 full 靠引擎读 playbook 自由发挥,是 1.5 节厚度漂移的根源。两引擎叶子只做「读任务包→写段稿/卡 + JSON」。
  2. **最小 C 子集**:SKILL frontmatter 收敛标准 6 字段;repo 根 `.agents/skills → .claude/skills` 软链补标准位(旧 `~/.codex/skills` 链保留至探针确认);`emit-def-block` 从同一份 def 声明生成 `.codex/agents/<name>.toml`(name/description/developer_instructions 直译;`tools:` 白名单无对应物记入 unsupported 清单)——Codex 侧**恢复情报员的结构性盲**(独立 subagent 上下文,fork_turns:"none")。
  3. **Codex 叶子适配器**:`autoresearch/engines/codex_leaf.py`(或脚本):`codex exec --json --output-schema <card.schema.json> -o … --sandbox workspace-write -c sandbox_workspace_write.network_access=true`(值待探针)+ 情报役 `web_search="live"`;禁 `--ephemeral`;rollout 路径回传给 D6 绑定。
  4. **金集对照(迁移门)**:冻结任务包(真实历史日,PIT 快照)× 两引擎 × N≥3 次:量 ①五档完全一致率 ②±1 档漂移 ③schema 合规率 ④引用可解析率 ⑤段稿字数分布。08-30 的双引擎真跑(300308/300857)收为金集第 0 号样本。**不达标的叶子留 Claude 单引擎**,而不是降低契约迁就。
  5. **动工前探针五件**(W5 §④):本机 Codex 版本 skills 发现路径;`network_access` 默认值与覆盖;`[agents]` vs `features.multi_agent` 现行开关;`--output-schema` 对 card schema 的 400 边界;AGENTS.md fallback 挂 CLAUDE.md 可行性。
- **验收**:金集报告一页;两引擎产物过同一套 lint/schema;capsule 双引擎对齐(D6 验收④)。
- **回滚**:Codex 叶子不启用时现状(主会话自演)保留,AGENTS.md 注明降级语义(盲性=指令级)。
- **类别**:I。
- **配额提示**:Codex 池与用户日常 ChatGPT 共享 5h 窗+周限(08-28/本次两回撞限都是它);重活错峰,金集跑选低峰时段。

---

## 6. 批次与优先序(建议;I/M 现在,B 等 09-中)

> 每批退出门:验收全绿 + 回滚杆已试拉一次;完成任务数不算完成批次(08-29 §6.1 同律)。

| 批 | 内容 | 前置 | 备注 |
|---|---|---|---|
| **P0(冻结窗内,~1 波)** | D6.1 契约登记(analyze 进 stages/ARTIFACTS/守卫)→ **先登记再写码**;D1 的 M 面(记账 29 处/PIT 8 处/assemble 硬校验/杂 bug);D8.3 脆弱点修复;D5.1 账本骨架 | 无 | 全 M/I;byte-parity 兜底 |
| **P1** | D1 走湖+拆分+瘦身(golden 对拍);D6.2–6.5 capsule kind 化+绑定+adapter 修复+L 级;D7 hook 兜底 | P0 | 绑定这步同时补 scan 的同一缺口 |
| **P2** | D2①–⑧ 盘后增量块(full+独立 lite);D4 模板 v5(人批);D8.1/2/4/5 双写+收口+单源+独立跑归位;D7 主路 fetch 工具 | P1 | D4/D8 的 def 改动集中一次人批,prompt_hashes 留痕 |
| **P3** | D3 exec_check(20 日不可见影子);D5.2–4 校准首读+反事实探针;D9 探针+叶子+金集 | P2 | 影子期与金集可并行 |
| **B 类(解冻后,按 20 结果日读数裁)** | scan slim 注入 D2 块(与 08-29 Q11 合并裁);exec_check 展示;rubric python 权威;l4-card def v5 上 scan | P2/P3 读数 | 每件独立回滚杆 |
| **正交立项(research/,零 LLM,随时)** | W3 三新格子复算:首板缩量回调低吸 / 晚封板溢价 / 机构席位 5–20 日(用湖+账本,普查协议同 08-28) | 无 | 结果只进文档;若正读数过门(CI 下界 ≥+0.15)再按「隔夜普查」裁决树走(Q9) |

---

## 7. 待裁(用户)

> **裁决记录(2026-08-31)**:用户裁定 **Q6 = 不买**(三件付费权限都不上:公告时刻用巨潮免费流+自记 first_seen 代理、涨停原因用已有权限 `kpl_list` 的 T+2 8:30 档、财联社不接);其余 Q1–Q5/Q7–Q10 按本稿推荐执行,并令开工实施计划(P0+P1 计划 = `docs/superpowers/plans/2026-08-31-stock-research-p0-p1.md`)。
> 无明确裁决时默认:M/I 可继续、B 全关、影子只记不展示、不为赶批次放宽门(08-29 §7.0 同律)。

| # | 问题 | 本稿建议 | 不选的后果 |
|---|---|---|---|
| Q1 | 卡片双写 `card.json`(D8)?(agent def prompt 字节变,人批) | **做**;JSON 优先+md 对账回退 | 24 组正则与"无标记=全 PASS"类缺陷继续按件修 |
| Q2 | analyze 留痕走全 capsule(D6)还是 `materialize_report_trace` 轻路? | **全 capsule**(七处字面量之隔;轻路同样绕不开绑定) | 轻路=有网查账无 replay/completeness;两套留痕并存 |
| Q3 | D2/D4 的进档次序:独立 lite 与 full 先行、scan slim 冻结后裁? | **是**(独立 lite 无 BUY 所有权、不进 E6;scan 侧与 08-29 Q11 合并裁) | 若独立 lite 也算判断层则 D2/D4 全体推迟到 09-中 |
| Q4 | exec_check(D3)立项 + 20 日不可见影子? | **做**(rt_min 权限已实测;它是"卡距执行 20h"唯一的确定性补位) | 14:45 决策继续裸奔;执行线阈值双份漂移不闭合 |
| Q5 | Codex 路线 = 任务包叶子 + md→toml 生成 + 金集门(D9)? | **是**;探针五件先行 | Codex 侧永远"主会话自演",盲性与厚度不可比 |
| Q6 | 付费权限:`anns_d`(公告精确时刻)/ `news src=cls`(财联社)/ `limit_list_ths`(16:00 涨停原因),约 ¥1000–2000/年档 | 先跑 D2 免费面 20 日,按"哪块降级最痛"再买(倾向 `anns_d` 优先——公告时刻是隔夜窗的时间轴地基) | 公告小时戳靠自记 first_seen 代理(可用但仅覆盖接入后) |
| Q7 | full 瘦身幅度:OHLCV 400 日→60 日+周线、指标序列→末值+摘要? | **是**(45% 字节换 D2 增量) | full 继续把预算花在历史;90KB 上下文挤压判断 |
| Q8 | 基率行的事实源:普查读数**静态快照**(版本化,人批更新)? | **是**(动态派生=学习环边缘,不做) | 无基率行,inside-view 病灶保持(W3 对 L4 负读数的病理) |
| Q9 | 三新格子复算立项(research/ 仪器)? | **做**(零 LLM、正交;有读数再走裁决树) | 外部正证据搁置;"14:45 工具"候选形态缺证据 |
| Q10 | akshare 1.18.64 → 1.18.94 升级(修财联社 404/巨潮列名断;venv Python 3.13 满足 ≥3.11)+ 20 个在用调用回归探针 | **做**(P1 顺带) | 两个已烂接口继续烂;新接口(注册表/ak.search)用不上 |

---

## 8. 不做清单(负结果与裁定背书;防将来重提)

1. 端到端"LLM 直接给买卖 + 回测收益当证据";用历史日期回看出卡验证 prompt 改动(泄漏必假阳)。(W1)
2. 加辩论轮数/加 agent 编制/加思考预算换决策质量;重建反思-记忆自动学习环。(W1;08-21 裁定)
3. LLM 预测隔夜方向类特性;新闻面押注隔夜 R:R(兑现窗 ~2 天,喂 5–10 日观察单侧)。(W1/W3;隔夜普查)
4. 股吧/淘股吧帖子进决策链(假数据型反爬);微博/百度指数;财联社逆向签名直连(正路=买权限)。(W2)
5. 实体匿名化防泄漏;RL 微调路线。(W1)
6. 期权进筛选/评级/regime;max-pain/OI 磁吸;readthrough 因果化。(08-24/08-28 裁定,重申)
7. WACZ/全网页归档常态化(只对关键公告 PDF 走 CAS+SPN 兜底)。(W4 成本判断)

---

## 9. 局限

- 三路代码审计是**静态只读**;tripwire 跨日失明、`context/` 路径渲染疑点、usage 零行等标 [U] 的结论需下一次真跑证实。
- W2 探针跑在周一盘中+中报季末(公告量峰值),push2 断连含本机 IP 前科成分;源可达性以接入时探针为准。
- 双引擎厚度对比 n=2 票、单日;质量差异结论要等 D9 金集(N≥3 × 多票)。
- 校准/Brier 需要 ≥20 个结果日才有首读;冻结窗恰好是攒样本的窗。
- 外部文献的效应量(华安回测/中金事件表等)未经本地复算,一律"待复算"不作决策依据(Q9 的立项理由正在于此)。
- 本稿全部改动未实施;仅供研究,非投资建议。

---

## 附录 A · 调研原件索引(`docs/research/2026-08-31-stock-research-optimization/`)

| 件 | 内容 | 最有用的三节 |
|---|---|---|
| `A_analyze_audit.md` | harvest/assemble 36 端点全表 + 20 类实时盲区 + 代码健康 | §1 取数清单表 / §2 盲区表 / §6 契约与留痕 |
| `B_l4_seam_audit.md` | 24 机读字段 × 20 消费者矩阵 + 14 焊死点 + JSON 双写最小切口 | §2 字段表 / §3 消费矩阵(死契约清单)/ §6 耦合清单 |
| `C_capsule_contracts_codex_audit.md` | capsule 证据全表 + analyze 零留痕实证 + Codex 一致性四面 + 最短三步 | §1.4 L4 卡在 capsule 里有什么 / §2.2 接入清单 / §6 三步 |
| `W1_llm_equity_agents.md` | 69 源:框架/证伪/评测/工程模式 + 12 启示 + 11 别做 | ①摘要 / ③启示 / ④别做 |
| `W2_ashare_realtime_sources.md` | 72 源普查(本机探针)+ 隔夜时间线 + 已开权限清单 + 14 探针 | ①Top10 / ③时间线 / ⑤风险 |
| `W3_shortterm_methodology.md` | 27 篇核实:制度级隔夜结论 + 12 项能力清单 + 3 新格子 + 方法论迁移 | ②能力清单 / ③实证表 / ⑤对照裁定 |
| `W4_trace_provenance.md` | OTEL/OpenInference/Inspect + Claude/Codex 采集面实拆 + L0/L1/L2 + 网查快照三方案 | ②2/②3 采集面 / ③A 字段映射 / ③C 方案表 |
| `W5_cross_engine_skills.md` | Agent Skills 标准 + Codex subagent/exec/沙箱 + 三路对比与推荐 + 5 探针 | ②能力对照表 / ④推荐架构 / 探针清单 |

## 附录 B · 探针总清单(动工前;全部标注过 [U])

D9 五件(Codex 路径/联网/开关/schema/AGENTS.md)· W2 十四件(深交所 annList、财联社 sign、stk_mins 权限、微博 cookie、韭研登录态等,见 W2 附录)· W4 四件(Codex notify payload、rollout MCP 形态、hooks 对 apply_patch 实测、PostToolUse 截断边界)· 本稿两件(tripwire 跨日在 run 分区的真跑核、任务包 `context/` 路径渲染真因)。
