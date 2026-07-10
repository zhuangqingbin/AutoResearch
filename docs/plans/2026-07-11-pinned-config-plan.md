# Plan A3:保送直通(pinned.json L1→L5)+ 配置收口(scan_config.json)

spec: docs/specs/2026-07-11-recall-gate-pinned-config-design.md §4。
门/纪律同 A1。**本 plan T1 是全波地基(A1-T4/A2-T4 依赖),最先执行。**

### Task 1: scan_config loader + 白名单校验 + frame 回显(地基)

**Files:** Create `autoresearch/scan/user_config.py`;Modify `autoresearch/scan/config.py`(ScanConfig 加字段:`l4_gate: dict|None`、`agents: dict|None`、`pinned: dict|None`、`redteam_prob: float|None`、`reuse: dict|None`,全默认 None=parity)、`autoresearch/scan/frame.py:127-152`(--json 输出加 `"user_config"` 块+run meta 落盘);Test `tests/scan/test_user_config.py`。

- `load_user_config(path=None) -> dict`:默认读 `.claude/skills/scan-market/scan_config.json`;**白名单键**=`{agents, l4_gate, funnel:{recall_channels,channel_quotas,channel_floors}, pinned:{cap,ttl_days}, redteam_prob, reuse:{max_age_days,price_delta_pct}}`,未知键/未知子键 **raise**(防拼写错静默失效);缺文件 → `{}`(=现行为)。
- `apply_to_scan_config(cfg, sc: ScanConfig) -> ScanConfig`:funnel 键映射到既有字段(recall_channels/channel_quotas/channel_floors),新键挂新字段。
- frame --json:输出 dict 加 `"user_config": cfg`;同时写 `context/scan/<date>/user_config_echo.json`(run meta,可复现凭据)。
- Steps:失败测试(三例:合法文件全键/坏键 raise/缺文件空 dict+frame 回显含块)→ RED → 实现 → GREEN → commit `feat(scan): scan_config.json 用户配置层(白名单校验+ScanConfig映射+frame回显)`。

### Task 2: workflow 消费(agents model/effort 管控)

**Files:** Modify `.claude/workflows/scan-market.js`(顶部取 `args.config`;各 `agent()` 调用的 effort/agentType 处改 `cfg.agents?.<stage>?.effort ?? '<现值>'`,model 同理;涉及点:strategist :52 / sector-brief :94 / l3-rank :101 / l4-card :139 / 杂务 low 两处)、`.claude/skills/scan-market/SKILL.md` + `STAGES.md`(Stage 0 说明:frame --json 产出的 user_config 随 Workflow args 传入);Test:`node --check` + `tests/scan/test_l4_prompt_cache_prefix.py` 必绿 + doc 契约测试。

- 优先级链落文档:**scan_config > workflow 内建 > agent def frontmatter 默认**。
- 缺 config/缺键 = 现硬编码值(fallback 写在 `??` 右侧,零行为变化)。
- commit `feat(scan): workflow agents model/effort 经 scan_config 管控(fallback=现值·parity)`。

### Task 3: pinned.json loader + L1 强注 + L2 强留

**Files:** Create `.claude/skills/scan-market/pinned.json.example`(带 note 的示例,真文件 gitignore 由用户建);Modify `autoresearch/scan/user_config.py`(`load_pinned(today) -> list[dict]`:cap≤5 超出截断打 warn、`expires` 缺省=added+10 交易日、过期条目返回于 `expired` 列表供报告备注)、L1 召回合并点(grep `registered_channels\|gate_rank` 定位 recall 汇总处:pinned 强注 lane="pinned" **不占 recall_n**;湖无该票数据 → 注入占位行带 `data_missing=True`)、L2(`select_l2` 调用点后强留,**不占 l2_n**);Test `tests/scan/test_pinned.py`(强注/不挤位/过期/超 cap 四例)。

- commit `feat(scan): pinned 保送 L1 强注+L2 强留(lane=pinned·不占坑·cap5·TTL10)`。

### Task 4: pinned L3→L5 直通 + 记账隔离

**Files:** Modify `autoresearch/scan/agents/l3_select.py`(L3 表 📌 列,presence-gated)、finalists 合流点(pinned 强留,lane=pinned;**进 A2 闸的 exempt 集**=不占 6~10)、`autoresearch/scan/agents/l4_card.py`(write_dispatch_pack:pinned 票必出 prompt;♻️ 复用规则照常但 📌 标记透传)、`autoresearch/scan/assemble.py`(L5 「📌 保送」节:每票一行 评级+note+expired 备注;presence-gated 无 pinned=无节);Test:合流/pack/assemble 测试各追加。

- 记账:channel_eval 按 lane 天然分行(pinned 独立成绩行=用户手工票记分卡,无需新码,验收时确认)。
- commit `feat(scan): pinned L3📌→finalists豁免→L4必卡→L5保送节(全程直通·记账隔离)`。

### Task 5: 端到端冒烟(跑动型,controller)

- scratch 副本(机制同 07-10 T9,历史 staging 零触碰):造 pinned.json 单票 → 依序跑 L1/L2 合成注入 → 合流 → pack → 一张真卡 → assemble 片段;验:lane=pinned 贯通、L5 📌 节渲染、不占配额、过期条目备注、user_config_echo 落盘。结论记 progress.md。**下次全量真实扫描=正式验收**(改一处 effort → run meta 回显对上)。
