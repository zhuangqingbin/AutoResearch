# summary.md 精简重构设计稿(2026-08-28)

> **状态(2026-08-29)**:**已实施**(B+ 全 8 任务)。真数据离线重渲实测 `summary.md` **27,008 B → 7,335 B(−73%)**、新增 `appendix.md` 22,555 B;发布包双写 / 产物登记 / replay 双比对 / post-run 双刷新全部落地。实施中另逮到并修掉一个**先前就存在**的 replay 缺陷:scratch 由冻结产物整目录拷贝而来,跑 runner 前不清理本 stage 声明的产物 → **什么都不产出的 stage 也会报 FULL**(`trace/replay.py`)。
> **性质**:设计稿(原为零实施稿;实施后保留原文,只加本状态行)。方案采用 **B+**:改动集中在 L5 事实整形 / 渲染 / 发布层(`report_sections.py` / `publisher.py` / 产物登记与 replay),零 LLM、不动任何判断输入与结构化决策产物 → **I 类**,不受 08-26 A0「B 类冻结到 09-中」约束。
> **证据**:08-26 历史基线记录为 27,008 B / 195 行(逐节字节 §1);`report_sections.build_summary` 22 节(`:786-986`);契约与测试清单(§2、§7)为本次读码结果,`file:line` 均为工作树现状。引擎隔离要求见 §8,实现 / 验收不得跨引擎读取历史目录。
> **用户诉求原文**:「summary md 感觉还是比较冗长,语言精要,内容结构逻辑好一些」。
> **姊妹稿**:`2026-08-28-external-evidence-expansion-design.md`(外源信息扩面);其 D-2「隔夜窗海外事件」行在本稿 §4.1 节 9 预留位置。

---

## 0. 边界(既有裁定,本稿全部遵守)

| 裁定 | 出处 | 对本稿的约束 |
|---|---|---|
| 报告双层:`brief.md` 入口(≤3,000 B,确定性),`summary.md` 详细版 | `scan-market/SKILL.md:237` | 本稿不改 brief 文案 / 输入依赖;summary 变决策层,新增 appendix 现场层 |
| **不设收编官 agent**(让 LLM 再压一遍只增加编数面与对账成本) | `SKILL.md:237`,用户裁定 | 精简必须靠**确定性模板**做到,不引入任何 LLM 改写 |
| L5 零 LLM 铁律 | `STAGES.md:184` | 同上 |
| 机器消费者不读、不解析 summary 正文 | `SKILL.md:186`;`tests/test_agent_defs.py:396` 锁这句 | 重排/瘦身不破契约;红线文件 `details/*.md` / `finalists.csv` / `decision_records.json` 一字不动 |
| B 类改动冻结到 09-中攒 20 个结果日 | 08-26 A0 | 不改 l3-rank / l4-card 输出契约(那是 prompt diff);上游结构化短字段列为冻结后候选(§3 方案 C) |
| run 目录 `<数据日>-<发布MMDD_HHMM>` | 08-28 裁定,`scan/run_naming.py` | 样张按新格式 |
| `scan_config.jsonc` = 流程参数唯一事实源 | 08-11 裁定 | 版式不是流程参数,**不加开关**;回滚 = revert(§8) |

---

## 1. 病灶(量化,08-26 样本)

| 节 | 字节 | 占比 | 病 |
|---|---:|---:|---|
| 顶部自检 banner | 2,286 | 8.5% | 13 行只有 5 种;`intel限频` 一稿一行 ×8,同一句话重复 8 遍 |
| §3 投资建议表 | 7,079 | 26.2% | 「L3精排」列塞 L3 全文论点:**中位 292 字 / 最长 347 字一格**;「L4研究·结论」列 3/10 行是 `—`(解析回退链有洞,§6.3);9 列横表不可读 |
| 「精排(L3)入选(风险/催化)」清单 | 5,437 | 20.1% | 每票 300–500 字风险 + 催化散文;无自己的标题,挂在 🍱 菜单体检节下 |
| 📈 策略师六小节 | 2,659 | 9.8% | §1 定调与仪表盘①重复;§4 操作基调与 overlay 行重复;§6「仅供研究」与页脚重复 |
| 各阶段耗时 & 落盘字节 | 2,151 | 8.0% | 运维遥测;附 ~500 字 token 计量说明**每日原样重印** |
| 📌 保送持仓 | 1,154 | 4.3% | 与 §3 同表结构,同病 |
| 漏斗数量 + 各阶段卡点 + 菜单体检 | ~1,900 | 7% | 代表股名单 / 行业分布 top5 对决策无用 |
| 其余(仪表盘 / 组合 / 同链 / 日历 / 行业 / 💸 / 局限) | ~4,400 | 16% | 基本合理;「口径:」夹注 4 处散落正文 |

结构病(与体积无关):

1. 节号 `## 3.` → 无号 → `## 1.` → `## 2.`(决策主线前置后没改号);
2. regime / 温度 / 定调在 H1 下、仪表盘①、策略师 §1 印 3 次;0 买机制在仪表盘③与「📉 今日漏斗读数」印 2 次;
3. 「口径」解释混在读数里(门柱两口径、BUY 出处、列注、方法段),读者要先读 4 段口径才能读表;
4. 每日恒定文本 ≈2.4 KB(诚实局限三条、token 计量说明、列注、行业 top3 方法段)天天重印;
5. 运维遥测(耗时 / 成本 / 落盘字节)与研究结论同一层级并列。

**目标**:决策层 summary ≤ 12 KB(典型 8–10 KB);同一叙事事实只在一个正文位置展开;读序 = 结论 → 行动 → 候选 → 为什么 → 地形 → 日历;现场 / 口径 / 遥测整体下沉到附录文件。**报告包内零独有信息丢失**:summary 删除的独有内容必须进入 appendix;已经存在于结构化权威产物 / `details/` / `trace/` 的内容允许改为链接,不承诺逐字复制所有重复模板。

---

## 2. 必须保住的契约(读码清单)

| 契约 | 出处 | 破了会怎样 |
|---|---|---|
| 4 组 managed 标记 `SCAN_DASHBOARD_*` / `SCAN_PORTFOLIO_*` / `SCAN_OVERLAY_*`(`report_sections.py:278-279,326-329`)、`run-observation:*`(`post_run.py:31-32`) | `_inject_block`(`:289-300`)标记缺失即 no-op | 仪表盘 / 组合 / overlay / 💸 留成占位;shadow-parity 靠它 |
| 注入锚字面 `"\n## 诚实局限"` | `post_run.py:502-512` 标记缺失时按它切 | 💸 块追加到文件末尾 |
| brief ①②③④ 文本**逐字**出现在 summary(`brief.dashboard_block` `:601-618` → 🧭 块) | `self_review.brief_lint` ④(`:1343-1358`)对 `_brief_sources.json` 四字段做**子串**核对 → `brief↔summary不一致` = **fail** → GATE4 毙 run | 仪表盘块只能原样注入,不能改写 |
| `SUMMARY_MAX_BYTES = 38×1024`(`report_sections.py:585`) | `test_summary_total_bytes_regression_lock`(断言,非运行时截断) | 单侧上限;本稿改 16 KB 并补鉴别力探针(§7) |
| `_BANNED` 空泛话术四词(`self_review.py:25`)对 `summary_text` 检 | warn | 保留 |
| 节序真身 = `report_sections.py:833-839` 注释块;`STAGES.md:188-202` 散文副本 | 文档契约 | 两处同改 |
| 测试锁的标题 / 列字面 ~25 个(§7 逐条) | 内部测试,可随改 | 改一处补一处 |
| `index.md` 链接(`health.py:601`)、`ArtifactSpec("summary")`(`artifacts.py:68`)、MANIFEST 哈希(`retention.py:676-733`)、`replay.py:186` | 存在 / 哈希 / L5 可重放输出 | 新增 `appendix.md` 要同步登记 |
| assemble `StageResult.artifacts` 现只登记 `summary` / `manifest`(`publisher.py:366-377`) | 控制面用它声明 L5 实际产物 | 必须加 `appendix`;两文件未齐不得记录发布完成 |
| `post_run observe` 会二次原位改写 summary(`post_run.py:660-686`) | token / 墙钟终值通常晚于首次 publish | appendix E 必须同源刷新;最终字节 / artifact index / MANIFEST 均在刷新后重算 |
| 旧 run 的 `run_contract.artifact_schema_versions` 不含 appendix | 历史现场按当时契约解释,不能倒灌今天的义务 | 重建 artifact index 时 appendix = NOT_EXPECTED(不入 missing);历史目录不回填 |

---

## 3. 方案裁定(B+;C 为冻结后增量)

| | A · 原地瘦身 | B · 直接双文件 | **B+ · 共享事实模型 + 双文件发布包** | C · 上游结构化短字段 |
|---|---|---|---|---|
| 做法 | 22 节不动,砍格内文本、聚合 banner、删恒定段 | 直接从现有 `build_summary` 再拼一份 appendix | 先构造不可变 `ReportModel`,再纯渲染 summary / appendix;两文件作为一个发布包登记、刷新、回放 | l3-rank 增 `thesis_short`(≤40 字);l4-card 直接产出评级同向短依据 |
| 体积 | 27 → ~18 KB | 27 → ~8 KB + 附录 ~15 KB | **同 B**,但可稳定守住预算 | 视配合 B+ |
| 一致性 | 仍有重复 | 两个渲染器可能各自重读 / 重算 | **单次整形、两处渲染;事实归属可测试** | 作者直接给短字段,最好 |
| 契约风险 | 低 | 中(新文件可能只落半套 / 漏登记) | **低且可控**(显式 bundle / replay / legacy 规则) | 改 agent def = prompt diff = B 类,受冻结 |
| 读序改善 | 弱 | 强 | **强** | — |
| 类别 | I | I | **I** | B |

**裁定 B+**。病根不仅是「一个文件同时当研究结论与运行现场」,还包括同一事实被多个渲染入口重复解释。直接 B 能治读序,但会制造 summary / appendix 两个重读点;B+ 用一个不可变事实模型把一致性边界补齐。C 的价值在于「一句依据」由作者写而不是机器截,留到 09-中冻结解除后随 `tests/test_agent_defs.py` 锚一并改。

---

## 4. 目标结构(方案 B+)

### 4.1 `summary.md` = 决策层(11 节;目标 ≤ 12 KB,断言上限 16 KB)

```
0  自检 banner(聚合版:每类一行 + 计数;明细 → appendix §A)               ≤ 6 行
1  H1「A股扫描 · <数据日>(run <id>)」+ 一行证据状态/数据新鲜度(只放报告身份,不放结论)
2  🧭 决策仪表盘(managed;brief ①②③④ 逐字)                               不变
3  ## 行动   overlay 仓位(managed;只写仓位/动作,不复述 BLOCKED 原因)· 组合视角(managed;只写集中度)
              · 同链 1-bet 一行 · 🎭 复核分歧 · 哨兵/override banner(均 presence-gated)
4  ## 候选(N 只)   表:# | 名称 | 板块 | 评级 | 目标(EV) | 一句依据 | L1→L2
5  ## 📌 保送持仓   同表 + 保送理由列(仍分列,不并表);⚖️ 两尺分歧行保留
6  ## BUY 资格与约束 / ## 为什么没有 BUY(presence-gated 标题)
                       单块单源:硬资格否决计数 + 早停分桶 + 三门 ✗(gate_states)
7  ## 市场地形(策略师)   嵌 §2 市场结构 / §3 红黑榜 / §5 关注 原文;§1 已在仪表盘①;§4/§6 → appendix C
8  ## 行业 top3   表 + 一行资格门;方法段 → appendix §F
9  ## 📅 未来 14 天   披露 / 解禁 /(外源稿 D-2「隔夜窗海外事件」预留行)
10 ## 运行事实   紧凑 managed 一行:墙钟 · LLM 调用 · 计量状态 · 数据降级 → appendix E
11 ## 诚实局限   一行 + appendix G 语义链接(锚字面 `"\n## 诚实局限"` 保留)
```

每节规则:

- **一句依据必须与终评级同向**(节 4/5 唯一自由文本列):终评级 ≥OW → L4 多头段;≤Hold → L4 空头段。缺同向段时按 `早停因` → 失守门柱 / 核心风险 → (≥OW 取 L3 thesis;≤Hold 取 L3 risk)回退;全空才印 `—`。**禁止拿反向段补空**,宁缺毋误导。所有层统一 `EVIDENCE_MAX_CHARS = 80`(去 Markdown / 管道 / 换行后按字符截断 + `…`),不再同时存在 80 / 96 两个上限。
- **表列**:删 `L3精排` 全文列(→ 附录 C);`L1召回`/`L2粗排` 合并为 `L1→L2` 一列(`#3632·healthy/主力 → #136`;gbdt 分去掉,它是遗留列名);`🛡️红队` / `🎭` 徽章并入评级格。
- **节 6 单源**:只读 `decision_records.gate_states` + `_relative_buy_decision.json`;`gate_status` 自由文本解析的直方图及其两口径说明整体下沉附录 D(它有漏读加粗 `**✗**` 前科;`GATE_HIST_BASIS_NOTE` 随行)。
- **策略师嵌入按小节切**:`_load_market_view` 现整段嵌,改为按已知精确标题切片取 2/3/5。缺标题 / 解析失败时 summary 只印「策略师分节解析失败;全文见附录 C」并落展示层 warn,**不得整段回退进 summary**;appendix 保留全文。**不改 macro-brief agent**。
- **口径夹注**:正文只留稳定语义链接,如 `[门柱口径](appendix.md#method-gate)` / `[行业资格门](appendix.md#method-sector-top3)`;注文集中附录 F。禁止按出现顺序动态分配 `[口径1]`,避免节序变化后引用漂移。
- **恒定文本**:诚实局限全文、token 计量说明、列注、方法段 → 附录;summary 不印每日相同段落。

### 4.1.1 事实归属(正文唯一展开点)

| 事实 | summary 唯一展开点 | appendix / 权威源 | 其他位置规则 |
|---|---|---|---|
| 报告身份 / 数据日 / run id / 证据状态 | H1 + 身份行 | manifest / capsule | 不进仪表盘 |
| regime / 温度 / 策略师定调 | 仪表盘① | appendix C / market_view | H1、行动、市场地形不得复述定调 |
| BUY / BLOCKED / 0买结论 | 仪表盘③ | appendix D / `_relative_buy_decision.json` | overlay 只写动作,portfolio 只写集中度 |
| 持仓总动作 | 仪表盘④ | decision_records | 行动节不再另印持仓摘要 |
| 持仓逐票评级 / 依据 / tripwire 分歧 | 保送持仓表 | details / decision_records | 仪表盘只给总动作,不展开逐票理由 |
| 仓位区间 / 新开仓动作 / 同链约束 / 分歧 | 行动 | appendix C / managed sources | 不回写仪表盘 |
| 单票评级 / 目标 / 短依据 / 漏斗位次 | 候选 / 保送表 | details / finalists / decision_records | 一票一行,同向短依据 |
| 无 BUY 因果 / 有 BUY 资格约束 | 节 6 | appendix D | 仪表盘只给结论,不展开统计 |
| 市场结构 / 红黑榜 / 未来关注 | 市场地形 | appendix C / market_view | 不复述定调 / 操作基调 |
| 漏斗现场 / 卡点 / 完整 L3 / 门柱口径 | 不进 summary | appendix B–D | appendix 是派生阅读视图,权威源仍为结构化产物 / details |
| 墙钟 / 调用数 / 计量状态 / 降级摘要 | 运行事实 | appendix E | post-run 从同一 observation 同时刷新两处 |

### 4.1.2 共享事实模型

```text
结构化 staging / details / managed sources
                  ↓ prepare_report_model() 仅一次
             immutable ReportModel
                  ├─ render_summary(model)
                  └─ render_appendix(model)
```

- `prepare_report_model` 负责读取、终评级 fold 后的字段整形、事实归属与短依据选择;不渲染 Markdown。
- `render_summary` / `render_appendix` 是纯函数:不得写 `_final_ratings.json` / `decision_records.json` / `gate_fires.csv`,不得各自重读或重算评级。
- 现 `build_summary` 内的决策落盘副作用先在原顺序中完成,再冻结 `ReportModel`;本稿不借重构改变任何业务计算或 writer 时序。
- appendix 是同一模型的诊断展开视图,**不是第三份权威事实源**。从旧 summary 搬出的策略师 / L3 / Tier-3 文本在 C 节保留;已经完整存在于 `details/` 的 L4 卡与 `trace/` 法证文件只列索引链接,不再复制全文。

### 4.2 `appendix.md` = 现场附录(零 LLM;目标 ≤ 20 KB,断言上限 24 KB)

```
# 扫描附录 — <数据日>(run <id>)
<a id="appendix-a-self-review"></a>
## A. 自检明细(全部 fail/warn 行,原样)
<a id="appendix-b-funnel"></a>
## B. 漏斗现场(数量 / 降级 / 卡点 / 菜单 / 0买机制)
<a id="appendix-c-research"></a>
## C. 研究全文(策略师 / L3 / Tier-3)
<a id="appendix-d-gates"></a>
## D. 门柱与资格(OW 三门失守分布 / 两口径说明)
<a id="appendix-e-runtime"></a>
## E. 运行观测(耗时 / 落盘 / token;detail managed 块)
<a id="appendix-f-methods"></a>
## F. 方法与口径
<a id="method-gate"></a>
### 门柱口径
<a id="method-sector-top3"></a>
### 行业资格门
<a id="appendix-g-limitations"></a>
## G. 诚实局限(三条全文)
```

appendix 每节标题恒在;条件素材缺席时明确印 `无 / NOT_EXPECTED`,不靠删除整节表达 absence。这样「appendix 缺任一节」检查的是结构完整性,不会把 sentinel / 无 Tier-3 的合法缺席误报成丢件。

### 4.3 文风契约(写进 `report_sections.py` 模块头 + `STAGES.md` L5 节)

1. **数字先行**:每行以事实 / 数字开头,解释在后;
2. **一行一事实**:不在同一行并列两个口径;
3. **夹注下沉**:正文不出现「口径:」「注:」「由来:」,一律用稳定语义链接进附录 F;
4. **同一事实只印一次**:regime / 温度 / 定调 / BUY 结论以仪表盘为准;无 BUY 因果只在节 6 展开;
5. **恒定文本不进决策层**:已知模板常量(完整局限、token 说明、列注、方法段)只能出现在附录或 `index.md`;不以跨 run 任意 byte 相同作为违规判据;
6. **自由文本单列**:候选表只有「一句依据」一列自由文本,其余全为结构化字段;超 80 字确定性截断。

### 4.4 样张(08-26 数据按 4.1 重排;确定性生成,零 LLM)

```markdown
> ⚠️ 自检 fail 0 / warn 11:intel限频 ×8(自报 22–37 条 > cap 20)· intel时效窗 ×2 · anns兜底承载 ×1 · market_view防锚定 ×1 → [自检明细](appendix.md#appendix-a-self-review)

# A股扫描 · 2026-08-26(run `20260826-0826_2120`)
数据截至 2026-08-26 收盘 · 发布 08-26 21:20

<!-- SCAN_DASHBOARD_START -->
## 🧭 决策仪表盘(与 brief.md ①②③④ 同源)
…(brief 原文逐字,不变)…
<!-- SCAN_DASHBOARD_END -->

## 行动
<!-- SCAN_OVERLAY_START -->**仓位**:range → 基准 3–5 成;本次不开新仓<!-- SCAN_OVERLAY_END -->
<!-- SCAN_PORTFOLIO_START -->板块集中:证券Ⅱ×3 · 保险Ⅱ×1 · 多元金融×1 · 消费电子×1 · 通用设备×1<!-- SCAN_PORTFOLIO_END -->
🔗 同链:证券Ⅱ ×3 = 1 个 bet(华西 Hold / 中金 Hold / 华泰 Hold)

## 候选(9 只)
| # | 名称 | 板块 | 评级 | 目标(EV) | 一句依据 | L1→L2 |
|---|---|---|---|---|---|---|
| 1 | 华西证券 | 证券Ⅱ | **Hold** | 8.44–8.62(中值 −0.2%) | 目标中值 −0.2%;资金三指标虽同向为正,但当前价已贴近 60 日高,隔夜上行空间不足 | #3632·healthy/主力 → #136 |
| 2 | 中金公司 | 证券Ⅱ | **Hold** | — | PB 对行业中位溢价 42% · 价仍压在 50/200 均线下 · 窗内唯一催化不可定向且执行腿可能被停牌切断 | #1934·healthy/主力 → #131 |
| 3 | 华泰证券 | 证券Ⅱ | **Hold** | — | 20 日 CMF/OBV 双负 + 户数升 + 价在 MA50/MA200 下方,今日 +2.96% 是板块脉冲 beta | #947·主力/价值 → #197 |
| … | | | | | | |
| 7 | 锡装股份 | 专用设备 | **Underweight** | — | 中报净利 −20.3% 使 12.1x fwd-PE 作废,全线空头排列 + 主力净出 + 户数上升 | #3·复合/价值 → #11 |
_一句依据 = 终评级同向 L4 段 → 早停因 → 失守门柱/核心风险 → 同向 L3 thesis/risk;反向段不补空;全文见 [研究附录](appendix.md#appendix-c-research) / `details/`。_

## 📌 保送持仓(1 只;不占 L3 名额,独立评判)
| # | 名称 | 板块 | 评级 | 目标(EV) | 一句依据 | L1→L2 | 保送理由 |
|---|---|---|---|---|---|---|---|
| 1 | 协创数据 | 消费电子 | **Underweight** | 231–237(−1.1%) | PB 22.27 + PE 66.23 意味着任何业绩瑕疵都会被放大数倍;主力占比为负叠加监管旗未澄清 | #2628·成长 → #201 | 持仓 |

## 为什么没有 BUY
硬资格否决:no_redflag ×6 · not_in_pool ×4(候选 10 / 合格 4)→ BLOCKED
早停 6 卡:基本面恶化 2 · 资金流出 2 · 题材透支 1 · 其他 1
满卡 4 张三门 ✗:主力真在 3 · 业绩真兑现 2 · 估值不透支 2 [门柱口径](appendix.md#method-gate)

## 市场地形(首席策略师 · 描述性)
2. **市场结构**:宽度 44.67% 站上 MA60、多头排列仅 12.21%;…(原文)…
3. **板块红黑榜**:强侧贵金属 +16.10%(n=12,主力净比 −0.041)…(原文)…
5. **关注**:中报披露收官周(8/31 截止)…(原文)…

## 行业 top3(确定性 healthy 分)
| # | 行业 | n | 主力净比中位 | 主力+占比 | 健康占比 | 60日中位% | 中位PE |
|---|---|---:|---:|---:|---:|---:|---:|
| 1 | 证券Ⅱ | 49 | 0.093 | 0.90 | 0.35 | 2.2 | 17.5 |
| 2 | 工业金属 | 50 | 0.062 | 0.86 | 0.14 | −8.8 | 21.5 |
| 3 | 个护用品 | 9 | 0.011 | 0.78 | 0.11 | −3.3 | 23.9 |
_资格门 n≥8 ∧ 资金门 ∧ 非落刀;只进 L5,不喂 L3/L4 [行业资格门](appendix.md#method-sector-top3)_

## 📅 未来 14 天
- 披露:300857 08-28 · 601688 08-29 · 601995 08-29
- 解禁 ≥5%:600961 09-08(30%)

## 运行事实
<!-- run-observation:start -->墙钟 129m59s · LLM 调用 22 · 计量 MEASURED $51.47(cache 92.6%)· 数据降级 hk_hold×1 → [运行明细](appendix.md#appendix-e-runtime)<!-- run-observation:end -->

## 诚实局限
召回/粗排为启发式 + 主尺 IC 校准,随 regime 漂移;L3/L4 为 Claude 推理;仅供研究,非投资建议 → [完整局限](appendix.md#appendix-g-limitations)
```

估算 ≈ 6.5–8 KB(策略师三小节 ≈ 1.8 KB 是最大单项)。

---

## 5. 逐节变更表(现 22 节 → 去向)

| # | 现节(`build_summary` 序,`report_sections.py`) | 去向 | 规则 |
|---|---|---|---|
| 0 | 自检 banner(`_self_review_banner:671-756`) | summary 聚合版 + 附录 A 全文 | 按 key 分组计数,附范围 |
| 1 | H1 + 六段漏斗免责行(`:842-844`) | summary,改文案 | H1 只留身份/数据新鲜度;免责行 → appendix G |
| 2–3 | regime 行(`:621-648`)+ 🌡 温度行(`:452`) | 删除独立行 | 唯一展开点 = 仪表盘① |
| 4 | 🧭 仪表盘(managed) | 不变 | 契约 |
| 5 | §3 投资建议表(`_buylist_table_lines:758-784`) | 候选表(新列集) | L3 全文 → 附录 C |
| 6 | OW 三门直方图(`gate_histogram:77-100`)+ basis note | 附录 D | summary 节 6 改读结构化源 |
| 7 | 🛡️ Tier-3 辩论明细(`_verify_detail:49-64`) | 行动节一行 + 附录 C | presence-gated |
| 8 | 📅 日历(`calendar.calendar_section`) | 节 9 | 预留海外事件行 |
| 9 | 组合视角(managed `:421-449`) | 行动节 | 标记保留;删重复 BUY 数,只留板块集中度 / 组合风险 |
| 10 | 🎭 分歧行 | 行动节 | |
| 11 | 哨兵 / override banner(`run_mode.banner`) | 行动节 | |
| 12 | 🔗 同链对比表(`_same_chain_block:587-603`) | 行动节一行 | 表 → 行 |
| 13 | overlay(managed `:478-501`) | 行动节 | 标记保留;只留仓位与动作,不复述 BLOCKED 因果 |
| 14 | 📌 保送 + ⚖️ 两尺分歧(`:541-577`,`:503-538`) | 节 5 | 表压缩;分歧行保留 |
| 15 | 📈 策略师 + 📉 漏斗读数(`:919-931`) | 节 7 切片;全文 → 附录 C;📉 → 附录 B | 定调由仪表盘拥有;§4 不逐字复印,实际仓位 / 动作由行动节拥有;解析失败不整段回退 |
| 16 | 🎯 行业 top3(`market.render_sector_top3`) | 节 8 | 方法段 → 附录 F |
| 17 | §1 漏斗数量表(`_funnel_rows`) | 附录 B | |
| 18 | 数据降级行(`_degraded_line`) | 运行事实一行 + 附录 B | |
| 19 | §2 各阶段卡点 + 🍱 + L3 入选清单(`_stage_overview`,`menu.menu_health`) | 附录 B / C | |
| 20 | 耗时表 + token 说明(`_stage_token_estimate:154-260`) | 运行事实一行 + 附录 E | 初次 publish + post-run 终值同源刷新 |
| 21 | 💸(managed,`post_run`) | summary 紧凑 managed 行 + appendix E 完整 managed 块 | 两块同一 observation;锚前;最终刷新后重算索引 / MANIFEST |
| 22 | 诚实局限(`:979-983`) | 一行 + 附录 G | 锚字面保留 |

---

## 6. 机制变更(代码级,零 LLM)

6.1 **先冻结事实、再纯渲染**:把现 `build_summary` 分为「保留原顺序的决策 finalize / 落盘」→ `prepare_report_model(...) -> ReportModel` → `render_summary(model)` / `render_appendix(model)`。两个 renderer 不读盘、不写盘、不做 rating fold;兼容入口 `build_summary` 可暂保留为薄壳,但 publisher 只调用一次 `prepare_report_model`。

6.2 **发布包与失败语义**:`publisher._run_publish` 先在内存生成 summary / appendix,再各自 temp + replace;两者都非空后才记录 assemble `SUCCEEDED`。任一渲染 / 写入失败:不写成功 StageResult、不产出半真半假的 artifact index / MANIFEST,保留 staging 供只重跑 L5;不得用「appendix 缺席但 summary 成功」冒充完整发布。这里失败的是**发布完整性**,不回写评级 / BUY、不把展示故障伪装成研究结论变化。

6.3 **产物契约补齐**:`artifacts.py` 加 `ArtifactSpec("appendix", 1, "assemble", "appendix.md", root="report")`;assemble `StageResult.artifacts` 加 `appendix`;`replay.default_stage_specs(...).l5.outputs` 改为 `("summary.md", "appendix.md")`;`health.index_md` 增「现场附录」链接;retention / MANIFEST 继续目录镜像自动覆盖。`index.md` 的「详细版」文案改为 summary=决策层、appendix=现场/口径/遥测。

6.4 **旧 run 兼容**:重建 artifact index 时以 run 自己记录的 `run_contract.artifact_schema_versions` 为准。记录里没有 `appendix`(含空 map 的 legacy run)→ 不生成 appendix 行、不计 missing;新 run 记录有 appendix → 必须 PRESENT。历史目录不回填、不改名、不因当前 `CRITICAL_ARTIFACTS` 增项变红。

6.5 **post-run 双刷新**:summary 保留既有 `run-observation:start/end`,但 managed 内容改为紧凑一行;appendix E 新增 `run-observation-detail:start/end` 完整块。初次 publish 与 `post_run observe` 都从同一个 observation 对象渲染两块;post-run 写完两文件后再刷新最终字节、artifact index、trace 镜像与 MANIFEST。任一 marker 缺席按各自稳定锚回退并落 warn,不得静默只刷新一边。

6.6 **banner 聚合器**:`self_review.render_banner(rows, aggregate=True)`:按 key 分组 → `key ×n(范围)`;`n=1` 原样;appendix A 用原函数全文输出。

6.7 **评级同向短依据**:`l4/parsers` 提供结构化 `pick_rating_aligned_evidence(card, final_rating, finalist)`;统一 `EVIDENCE_MAX_CHARS = 80`。≥OW 只走 bull/thesis,≤Hold 只走 bear/early-stop/gate/risk;反向段不作为回退。返回 `{text, source, polarity}` 供 appendix C 留痕,summary 只印 text。

6.8 **策略师切片**:`slice_market_view` 按六个已知标题切片,summary 取 2/3/5,appendix C 收全文。解析失败 → summary 一行链接 + self_review warn,不整段回退;不改 macro-brief agent。

6.9 **语义口径链接**:`report_sections.FOOTNOTES: dict[semantic_key, {anchor,text}]` 为单一事实源;summary 固定引用 `gate` / `sector-top3` 等语义 key,appendix F 用显式 ASCII `<a id="...">` 展开。编号不依赖渲染顺序,链接在不同 Markdown renderer 下也稳定。

6.10 **字节预算**:`SUMMARY_TARGET_BYTES = 12×1024`,`SUMMARY_WARN_BYTES = 16×1024`,`APPENDIX_TARGET_BYTES = 20×1024`,`APPENDIX_WARN_BYTES = 24×1024`。测试锁 warn 上限;运行期在**所有 managed 注入完成后的最终文件**计量并落展示层 warn,不截断、不改变评级、不毙 GATE4。

6.11 **文档同改**:`report_sections.py` 节序 / 事实归属注释块、`STAGES.md:188-202` L5 散文、`SKILL.md:183` 产物清单(加 `appendix.md`)、`index.md`「读我」行。

6.12 **不在本稿**:macro `<HHMM>_summary.md`(`macro/assemble.py`,多 agent 分节拼接,结构不同)另案;09-中前不加 / 不改 l3-rank、l4-card agent 输出字段。

---

## 7. 测试影响清单

需改(锁旧字面 / 旧节序):

| 测试 | 现锁 | 改法 |
|---|---|---|
| `tests/scan/test_report_sections.py:94` | 节序 `DASHBOARD_HEADER` < `## 3. 投资建议` < `## 📌 保送持仓`,且 `## 1.`/`## 2.` 在其后 | 改为 `## 候选` < `## 📌 保送持仓` < `## 为什么`;`## 1./## 2.` 断言迁到 appendix |
| `:129 test_preserved_sections_survive_the_reorder` | `各阶段耗时 & 落盘字节`、`精排(L3)入选`、`### 组合视角`、`OW三门失守分布`、`## 2. 各阶段卡点` | 前四项改断言 appendix;`组合视角` 标记仍在 summary |
| `:135 test_no_content_class_is_dropped` | 每个 finalist 名字在 summary | 保留(候选表仍列全) |
| `:214 test_summary_total_bytes_regression_lock` / `:230` | ≤ 38×1024;字面 `38 * 1024` | 最终注入态 summary ≤16×1024;新增 appendix ≤24×1024;**加内容类别断言**防「删空也绿」,不以通用 ≥5KB 下界替代内容检查 |
| `:237/:246` | `OW三门失守分布` + basis note 在 summary | 迁 appendix §D |
| `:254 test_observation_anchor_survives_for_cost_section` | `"\n## 诚实局限"` 存在且 💸 在其前 | 不变 |
| `tests/scan/test_assemble.py:369-379,446,482,496,448/462/484/509` | `## 1. 漏斗`、`## 2. 各阶段`、`## 3. 投资建议`、`L1召回(#/`、`L2粗排(#/`、`L3精排`、`L4研究·结论`、`列注`、`## 各阶段耗时`、`🛡️红队`、`🛡️ Tier-3`、`组合视角`;表头四阶段列;`#5`/`成长`/`g0.5` | 按 §4.1 新列集重写;`g0.5` 类 gbdt 分断言删除;`md.find("## 3. 投资建议")` 切片改 `## 候选` |
| `tests/scan/test_assemble_pinned.py:55-109` | 切片 `## 3. 投资建议` / `## 📌 保送持仓` / `## 1. 漏斗` | 同上 |
| `tests/scan/test_e3b_switch_package.py:69-339` | `BUY(相对决策层) **N** 只`、`3–5 成`、`今日 **BLOCKED**`、`🛑 当日 BLOCKED`、`看到本行说明注入未跑` ×2;`md.split("### 组合视角")` | managed 标记 / active-shadow parity 保留;内容断言改为 dashboard 独占 BUY/BLOCKED、overlay 只含仓位/动作、portfolio 只含集中度 |
| `tests/scan/test_self_review_brief.py:226-253,437-449,519` | 篡改 summary 数字 → fail;summary 缺失 → fail;9 条 severity | 不变(仪表盘逐字) |
| `tests/scan/test_wave3_observation.py:148-172` | 恰一处 `run-observation:start` | summary 紧凑 marker 恰一处 + appendix detail marker 恰一处 |
| `tests/scan/test_market_view_embed.py:18,33` | 策略师整段嵌 | 改为精确切片 + 失败不整段回退两类用例 |
| `tests/scan/test_health.py:419-455`、`test_retention.py:123-140`、`test_artifacts.py:50`、`test_stage_result.py:322-341` | index / MANIFEST / ArtifactSpec 含 summary | 各加 appendix 一行 |
| `tests/test_agent_defs.py:396` | SKILL 文档 ≥2 处「机器不读 summary 正文」 | 文案保留 |
| `tests/trace/test_replay.py` | L5 只比对 `summary.md` | L5 同时比对 summary / appendix |
| `tests/scan/test_post_run.py` | observe 只刷新 summary managed 块 | 同一 observation 刷 summary 紧凑块 + appendix 完整块;刷新后索引 / MANIFEST 一致 |
| publisher 故障测试(新增) | 无双文件部分失败语义 | appendix render / write 注入失败时 assemble 不记成功,重跑 L5 可补齐且不重算评级 |
| artifact legacy 测试(新增) | 全局 registry 即当前义务 | 旧 contract 无 appendix → 不入 missing;新 contract 有 appendix → 缺席必红 |

新增(鉴别力,按 08-24 mutation 教训):

- banner 聚合:8 条同 key → 1 行 ×8,appendix A 仍有 8 条原文;
- 一句依据:bull / bear / early-stop / gate / L3 thesis / L3 risk / 全空各一例;对所有 ≤Hold 断言 `polarity != bull`,对所有 ≥OW 断言 `polarity != bear`;
- 策略师切片:正常六节、标题缺失、标题格式污染三例;失败态 summary 不含整段全文且 warn 在场;
- 语义口径链接:节序重排后 anchor 不变;summary 所有本地 appendix anchor 均能在 appendix 标题命中;
- appendix A–G 标题恒在;条件素材缺席印 `无 / NOT_EXPECTED`;真缺任一结构节 → 红;
- summary 禁止出现「口径:」「注:」「由来:」及已知恒定模板原句;**删除**“两个 run 任意 byte 相同行必须进白名单”的脆弱 diff 测试(真实事实相同也可能合法);
- 事实归属 mutation:删掉 H1 regime / 温度 / BUY、行动节定调 / 持仓 / BLOCKED 原因中的任一去重约束时测试变红;
- 代表模式矩阵:BUY、BLOCKED、合格但 0 BUY、SENTINEL_EMPTY、多 pinned、Tier-3/ensemble 分歧、warn-heavy / degraded、market_view 解析异常;
- 最终态预算:首次 publish 后一次、`post_run observe` 刷新后一次;只以后者作为验收读数。

---

## 8. 实施拆分(单 PR,I 类;8 任务)与回滚

| 任务 | 内容 | 依赖 |
|---|---|---|
| T1 | 常量 + 文风 / 事实归属契约 + 节序注释块(文档先行:`report_sections.py` 头、`STAGES.md`、`SKILL.md`) | — |
| T2 | 保持现有 finalize / writer 时序,抽 `prepare_report_model` + immutable `ReportModel`;锁纯函数边界 | T1 |
| T3 | banner 聚合器 + 评级同向一句依据 + 测试 | T2 |
| T4 | 策略师切片 + 语义口径 anchor + 失败 warn + 测试 | T2 |
| T5 | `render_appendix` + A–G 恒定骨架 + appendix 观测 managed 块 | T2–T4 |
| T6 | publisher 发布包双写 + ArtifactSpec / StageResult / index / replay / legacy / MANIFEST + 故障测试 | T5 |
| T7 | `render_summary` 新节序 + 候选表新列集 + 事实去重 + post-run 双刷新 | T3–T6 |
| T8 | §7 全部测试迁移 + 模式矩阵 / mutation 探针 + 本引擎真 run 离线重渲染对照 | T7 |

- **回滚杆**:revert 该 PR。不设运行时版式开关(版式不是流程参数;开关只会多一份事实源)。失败发布只需重跑确定性 L5,不重跑 L3/L4。
- **引擎隔离**:验收真 run 只读写当前 `$CTX=context_${AUTORESEARCH_ENGINE}` / `$RPT=reports_${AUTORESEARCH_ENGINE}`;Codex 实施必须先 `export AUTORESEARCH_ENGINE=codex`,不得借 `context_claude/` / `reports_claude/`。无本引擎历史 staging 时用提交内 synthetic 模式矩阵 + 新跑一份本引擎 staging,不越界借样本。
- **验收**:① 模式矩阵全部满足 summary 典型 6–12 KB / warn 上限 16 KB、appendix 典型 ≤20 KB / warn 上限 24 KB、`brief_lint` fail 0;② `post_run observe` 后最终字节仍达标且 MANIFEST verify 通过;③ `pytest -q` 全量通过;④ 人读:决策层从头到 📅 不出现「口径:」、regime / 温度 / 定调 / BUY / 持仓各只有一个展开点。

---

## 9. 本轮设计裁定

| # | 问题 | 推荐 |
|---|---|---|
| Q1 | 双文件还是原地瘦身 | **B+**:共享 ReportModel + 双文件发布包 |
| Q2 | 一句依据上限 / `conv` | 全链统一 80 字;conv 进 appendix C |
| Q3 | 候选表 `L1→L2` | 合并,改测试 |
| Q4 | brief ③ 是否砍口径尾巴改 `[口径1]` | **不改 brief**;30 秒入口保持自包含,不依赖 appendix |
| Q5 | macro `<HHMM>_summary.md` | 另案 |
| Q6 | brief ⑤ 是否加 appendix 依赖 | **不加**;appendix 链接只进 summary / index |
| Q7 | appendix 对旧 run 是否追责 | 只对 contract 已登记 appendix 的新 run 必需;旧 run NOT_EXPECTED |
| Q8 | appendix 缺席是否允许 summary 单独冒充成功 | 不允许;保留 staging,只重跑 L5 补齐发布包 |
