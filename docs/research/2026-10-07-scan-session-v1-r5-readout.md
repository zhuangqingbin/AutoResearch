# session_v1 全扫第 5 场读数（2026-10-07 夜，Claude 宿主）

分析日 2026-09-30（国庆休市；D1=10-08 收盘入场、D2=10-09 开盘退出），生产配置（非持仓 5 卡 = L3 4 + 证据席 1，持仓 688981/300750），mailbox 执行器，`--max-parallel 8 --timeout-multiplier 2`，主会话 Opus 5.5·max（新进程 23:05 启动，晚于全部 hook/agent/settings 改动），研究角色钉版 `claude-opus-5-5`、情报员 `claude-sonnet-5-5`，宿主 2.1.292。10-04 的 R4-1~R4-7 修复（未提交）首次真跑。

Run `20261007T151001404046Z` · **FAILED**（runner BLOCKED，capsule `reports_claude/scan/_failed/20261007T151001404046Z`），**未发布**。

## 结论先行

- 编排首次在 Claude 宿主上**零故障跑完 L4 全部研究**：27 次推理派发全部原样 prompt（`prompt_verbatim=True`），0.1–4.6 s 内绑定；0 次边界拒绝、0 次超时、0 次 429、0 次重试。
- 10-04 修复在真跑生效：R4-1（单阶段 a2 契约）未触发但卡 prompt 已带 `research-decision-v2`；R4-6 L3 40/40 全判；R4-7 行业 brief 9/9 接受；新闻目录夜跑腿恢复（最新 2.5 h）；情报 1800 s 超时没有被触发。
- 唯一阻断 **R5-1**：688578 OW 卡的独立复核 review2 被 `validate_scenario_estimate` 拒绝（`scenario return contradicts declared entry/exit`）→ CONTRACT_ERROR 不可重试 → `REVIEW_UNAVAILABLE` → 必需复核缺口 → BLOCKED。E6 / assemble / GATE4 未运行，**本场无系统 BUY**。

## 时间线（UTC）

| 段 | 时间 | 读数 |
|---|---|---|
| begin → frame/prelude | 15:10–15:15 | 预热未跑，L0–L2 全额取数 |
| GATE1 | 15:20 | sentinel=full（健康上涨 6.5%），菜单预算 30 |
| 市场研判 | 15:12–15:17 | 震荡退潮，温度 52.4；科技硬件链失血、医药/农业走强、银行吸金；北向 5 日 +115 亿 vs 融资 5 日 −430 亿 |
| 行业 brief | 15:21–15:26 | 9/9 接受（其他家电Ⅱ、造纸、数字媒体、半导体、汽车零部件、化学制药、基础建设、贸易Ⅱ、冶钢原料） |
| L3 | 15:26–15:56 | 29 分钟；40/40 全判；入围 4（全医药）；lint 过，repair skip |
| GATE2 → L4 prepare | 15:59–16:07 | 人口 7 = 入围 4 + 证据席 1 + 📌2 |
| L4 情报 | 16:08–16:30 | 7/7 接受（8–12 分钟/个） |
| L4 卡 | 16:25–17:09 | 7/7 机检接受 |
| 复核 | 16:51–17:30 | 688981 review2 接受；688578 review2 被拒 |
| runner 退出 | 17:30 | BLOCKED；随后显式 `capsule finalize --business-status FAILED` |

slim 由 runner 串行生成（确定性 lane），7 只累计约 31 分钟，`ready_queue_wait` 显示 300750 slim 排队 2005 s——是 L4 段的关键路径。

## L4 卡（未发布，主卡均已机检接受）

| 代码 | 名称 | 来源 | 评级 / FINAL | 停 | 要点 |
|---|---|---|---|---|---|
| 688578 | 艾力斯 | L3 lowturn 70 | **Overweight / BUY** | 满卡 | 三门 PASS；主观隔夜 EV ≈ +0.1%（入场价未知，精确 EV 未核）；兑现机制不成立 |
| 600276 | 恒瑞医药 | L3 lowturn 68 | Hold / HOLD | P3·基本面恶化 | 业绩门 FAIL（EPS 0.34 vs 0.40 预期，近 4 期 3 次不及）；授权利好在入场前已定价 |
| 688222 | 成都先导 | L3 trend 64 | Hold / HOLD | P3·估值透支 | PE 127.7、fwd 124x，卖方均值目标 43.6 < 收盘 44.69 |
| 688073 | 毕得医药 | L3 growth 57 | Hold / HOLD | P3·其他 | 主力门 FAIL；10-09 起大股东可减持 3.01%（公告原文未核） |
| 603235 | 天新药业 | 证据席 52 | Hold / HOLD | P3·其他 | 窗内无催化，业绩门 FAIL（H1 净利 −10.3%） |
| 688981 | 中芯国际📌 | 持仓 | **Underweight / SELL** | 满卡（已读 deep） | 9-30 收 111.99 已破 9-29 卡清仓线 114.07 与减半线 115.29；review2 同为 UW/SELL（conviction 62） |
| 300750 | 宁德时代📌 | 持仓 | Hold / HOLD | 满卡（已读 deep） | 净分 +2 但主力门 FAIL（CMF −0.22、OBV −0.32）压回 Hold；单季毛利率 28.2→24.8→23.2% |

688578 review2（被拒，不进接受链，只作一致性旁证）：Hold / HOLD，conviction 45，主动偏离评分卡 OW，理由「隔夜 EV 仅 +0.15%、R:R≈1.1」——与 a1 结论分歧。

## 缺陷

| # | 缺陷 | 证据 | 建议修法（待裁） |
|---|---|---|---|
| R5-1 🟥 | 卡面声明入场价时，情景收益 / EV / R:R 三项校验都要求与公式 ≤1e-9 一致；卡面契约只给整除样例（10.20/10.00→0.02），没有精度说明。R:R 是比值（0.0240/0.0220=1.0909…），四舍五入必被拒。agent 无计算工具。 | `contracts/execution.py:351-381` 三处 `Decimal('0.000000001')`；契约原文 `common/card_decision.py:263`；被拒卡写 `0.0240/0.0020/-0.0220`、`rr 1.09`，精确值 0.024018…/0.001971…/-0.021957… | ① 校验按声明精度容差（半个末位，且封顶 0.00005，即要求 ≥4 位小数），R:R 同理；契约补一句「收益与 R:R 保留 4 位小数」。② 或 scan 用途（D1 收盘未知）规定 entry 必须为 null，由 root 计算收益。两条可并用。 |
| R5-2 🟧 | 复核员违反冻结窗「未知入场价不报精确 EV」，以分析日收盘 111.58 作假设入场价。主卡 7/7 都守住了 entry=null。 | review2 卡「assumed_entry_price=111.58(声明假设:D1 收平于 9/30 收盘)」 | review 派发 prompt 与主卡同源，需在复核指令里重申；或采用 R5-1 ② |
| R5-3 🟨 | mailbox 模式下 `complete` 前无法 precheck：`capsule/agents/session/requests/session-<task>-a<n>.json` 是请求记录，不是 submission envelope（`fields mismatch: missing host_receipt_id/outputs`）。FULL 档靠 precheck + 同 attempt 修订救回的做法在 scan 宿主循环里不可用。 | 本场对 market_view 试跑 precheck 返回 CONTRACT_ERROR | `mailbox` 增加 `precheck` 子命令（按 result 文件组装候选 submission），或在 `complete` 时先跑领域校验并允许同 attempt 修订一次 |
| R5-4 🟨 | slim 在 knowledge_cutoff（15:10Z）之后才取数（卖方目标/一致预期 16:36Z 采集）。休市期间价格未变，本场无实害。 | 688981 卡自报 | 冻结窗与 slim 取数时间对账（PIT） |
| R5-5 ⬜ | autobind 监视器在本会话尚未派过子 agent 时找不到 `<session>/subagents` 而退出。 | 首次启动报 `cannot locate this session's transcript dir: []` | 已在开发工具里加回退（按 `<session>.jsonl` 推导目录），非产品代码 |

## 计量（usage_harvest，按官方价估算）

1 主会话 + 27 subagent，加权输入 8.52M、输出 1.73M、cache 命中 94.4%，**估算 $52.72**：主会话 $15.63（加权 60%，宿主逐字重建 27 份长 prompt + 长上下文），l4-card ×9 $22.62，l3-rank $5.12，l4-intel ×7 $6.79，macro-brief $0.94，sector-brief ×9 $1.62。

## 宿主操作经验（本场新增）

1. 长 prompt 用「差异视图」逐字重建：同类请求与首份参考按 task_id 段落替换后逐行比对，只抄差异行；27/27 `prompt_verbatim=True`。
2. 持仓卡与普通卡只差第 20 行 `usage=holding_review`；深市票第 8 行 `venue` 为 XSHE。
3. 后台起「新请求 / runner 退出 / 心跳停」一次性监视器后再结束回合，比 90 s 轮询省上下文。
