# 两段决策卡候选

状态：候选已实现并通过独立规格与代码质量复核；验证结果及未完成项以 [第二批记录](../research/2026-09-30-agent-skills-batch2-readout.md) 为准。该候选增加一次独立上下文推理，未完成评价前不切换默认。

## 选择与冻结

现有 v1 begin request 保持单段研究。候选 request 使用 `schema_version: 2`，其余原有字段保留，并增加 `card_research_profile: "two-stage-v1"`。入口仍是：

```bash
export AUTORESEARCH_ENGINE=codex
uv run --no-sync python -m autoresearch.session_agent begin --request-file <请求文件>
```

合法值为 `single-stage-v1` 和 `two-stage-v1`。两段候选只支持 `stock-research/LITE` 与 `scan-market/AUTO`；FULL 不因使用 v2 request 自动改变图。

选择在 begin 构建计划前冻结。运行中改配置或部署新代码不能把旧 plan 升级；希望改变研究图时应创建新 run。`card_source` 继续表示卡片的读取权威，与本开关分别记录。

## 产物语义

| 阶段 | 输入范围 | 输出 |
|---|---|---|
| initial | 冻结事实、主尺时间窗、描述性地形及必要深层事实 | 专用初评 JSON；不能作为可发布决策卡 |
| decision | 同一事实、已接受初评、L3 先验与本阶段补充证据 | 最终决策卡及改判记录 |

初评记录 subject、frame/fact manifest hashes、六维、三门、评级、风险、缺口和来源引用。初评不接收 L3 conviction、论点、排名、旧评级或通过 force-full 原因泄露的先验。边界检查应覆盖允许读取的全部文件，不能只检查 prompt。

改判记录的 `rating` 比较初评评级与最终卡评级。完整 ResearchCard 既有字段 `initial_rating` 在本项目中承载原始最终卡评级，不能用第一阶段评级覆盖它。改判可以来自新事实，也可以来自对已有事实的纠错；必须留下原因。

`research.card.facts` 是无网络的 COMPUTE 操作：从冻结原始输入按 owner 白名单投影，记录原始来源与投影内容哈希，不通过清洗旧 prompt 制造初判材料。初评使用 `research.card.initial.v1`，最终决策使用 `research.card.decision.v1`；已有最终 Markdown 卡片路径和业务身份沿用。

当前 run 的新增产物位置：

| 工作流 | 事实 / 初评 / 改判记录 |
|---|---|
| stock LITE | staging 下 `session_outputs/card.facts.json`、`card.initial.json`、`card.changes.json` |
| scan | staging 下 `session_attempts/<code>/a<n>/facts.json`、`initial.json`、`changes.json` |

scan 的 a1 原始 slim/deep 也复制到本票 attempt 目录。重试引用成功初评的原 artifact 和事实身份；新的 attempt 目录不意味着必须重新生成初评。

## 恢复与证据

成功初评绑定原字节及输入身份。最终决策重试时复用它；只有初评自身失败才重新执行初评。初评成功不等于整张卡完成。两个阶段使用不同的独立上下文，不能将同一次派发的重连能力解释为可复用已结束对话。

独立上下文不等于统计独立，声明了读取清单也不等于操作系统隔离。宿主真实执行、调用量、延迟和改判依据须另行验收；本候选不构成收益提升证明。
