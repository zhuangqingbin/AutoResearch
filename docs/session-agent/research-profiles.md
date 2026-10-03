# 版本化研究请求与候选流程

当前样例使用 begin schema v4。旧 v1/v2/v3 仍按其原字段契约解析；请求字段版本和卡片规则版本是两回事。
新 run 的 `skills-gap-v3` 与评级阈值会冻结在 capsule verification profile 中，不能修改已冻结运行来升级规则。

## 字段

| 字段 | 值与用途 |
|---|---|
| `card_research_profile` | `single-stage-v1` 默认；`two-stage-v1` 仅 scan 与单股 LITE |
| `research_context.venue` | 真实交易所，如 `XSHG`、`XSHE`、`XNYS`、`XNAS`；不从六位代码伪造海外身份 |
| `research_context.usage` | `scan`、`standalone`、`holding_review`、`macro`、`sector`、`dossier`，须与入口匹配 |
| `research_context.calendar_source_path` | 明确来源的交易所日历 JSON；缺失保留 null，不能手填 D1/D2 代替来源 |
| `macro_research_profile` | `serial21` 默认；宏观 FULL 可显式选 `six_groups_v1` |
| `macro_optional_products` | 可选 `4_crossasset/credit.md`、`6_meso_evidence/industry_cycle.md`、`1_spine/debate.md` |
| `sector_brief_profile` | `legacy` 默认；scan / 行业 LITE 可显式选 `deterministic-v1` |

所有字段在 begin 时固定；host_profile 的能力和 session_ref 必须换成本次实际宿主证据。
文档样例中的分析日和证券只是格式示例，不是有效交易窗口。日历缺失、时间未知会限制执行，不能靠降低评级遮蔽来源缺口。

## 宏观六组候选

前三区域、跨资产、中观组并行；中美/variant 读取前三组，风险组读取此前所有组，决策/行业配置组读取所有上游。
仍逐一验证原 21 份必需产物，选中的 optional 也参与冻结哈希。一个组失败只重试该组；部分文件不能算组完成。
分歧、原始事实及配置理由保留；只有任务数减少不能宣称质量相同或成本更低。

## 行业确定性候选

数值地形直接从本次冻结 pack 渲染，注明单位、来源哈希、实际数据日期和分类映射覆盖。
来源日期不明不能用目录名或文件修改时间补齐。仅重大新事件、来源冲突、必需缺口触发有界事件任务；
事件只能追加通过来源核验的事实，不能改数值字段或写买卖方向。现有语义验证器未覆盖的行业断言保持未核。
稳定事实可在来源与有效期仍成立时复用；价格地形每个目标日重建。

## 决策卡与档案

FULL/LITE/scan 共用 `research-decision-v2` 结构与六维三门校验。情景收益通过声明的入场价或区间计算，
未知分母不输出精确 EV/R:R。`decision-claim-uses-v1` 声明事实用途，root 将真实卡字节、frame 与 task/attempt
绑定；模型不填写来源 PASS。声明覆盖率不等于全文语义完整性。

档案 `dossier-facts-v1` 区分 STABLE / DYNAMIC / HYPOTHESIS。有效日、公开时间、动态快照时间、假设反证条件
分别记录；旧无类型叙述不自动升级为已核事实。本次 slim、新闻、主尺和执行条件仍重新生成。

这些 profile 均不改变默认宿主验收门。`session_v1` 仍为显式 PILOT，真实质量、成本与收益比较另行积累。

## 档案维护与读回

季度对账仍使用原 `dossier.reconcile` 入口，新增可选的 typed fact 更新：

```bash
export AUTORESEARCH_ENGINE=codex
uv run --no-sync python -m autoresearch.dossier.reconcile <报告期YYYYMMDD> \
  --code <六位代码> --today <分析日YYYY-MM-DD> \
  --fact-updates <本引擎内的事实列表.json> --knowledge-cutoff <带时区的截止时间>
```

`--fact-updates` 必须同时指定 `--code`。输入是 `dossier.facts.validate_fact` 定义的精确字段列表，
不能提交 evidence/PASS。活跃运行使用冻结 frame 与正式源；离线自填截止只允许 UNKNOWN 留痕，
不能把历史数字改日期或改成 STABLE 就重新复用。假设到期/来源冲突/缺来源均进入历史，保留旧值和来源。
原季度实际业绩取数逻辑保留；这条命令不是零网络演练。

对已发布档案，读取顺序为 committed publication base 加可验证的维护链。
`knowledge/dossiers/_maintenance/<code>.json` 只引用既有 `_operation_evidence`，每一环都校验
opening、candidate、subject、状态与 before/after 哈希；持锁 CAS 拒绝过期写入。
维护提交不冒称一次新的 session publication。新发布 base 不套用旧链，坏链不回退到任意镜像。
`dossier.delta` 和 `dossier.reconcile` 支持已有操作证据回放，旧无 fact_context 的证据仍按旧契约处理。

## 质量与效率的离线评价

声明范围审计、案例库、实际用量和候选比较按 [研究质量闭环](research-quality-workflow.md) 执行。`material_conclusion_audit_v1` 与 `stage_effort_v1` 是离线实验 family，不是新增 begin 参数。真实宿主、人工 gold 和前向观察状态分别验收。
