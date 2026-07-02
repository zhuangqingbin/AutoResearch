# 观察单触发器 + 0买对照 + L2菜单体检 实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: superpowers:executing-plans。TDD,合成 fixture,无网络。
> 本计划为紧凑版:API 签名/词表/数据契约详见 spec `docs/specs/2026-07-02-scan-watchlist-and-health-metrics-design.md`(同日执行,spec 即详细设计)。

**Goal:** 观察单从死文本变活仪表(结构化+日检+L5嵌入)、0买日市场对照 ledger、L2 菜单确定性体检。

**Tech:** 纯 pandas/stdlib,零 LLM,复用 staging/嵌入/ledger 三个既有模式。

**Global Constraints:** `uv run --no-sync`;presence-gated 嵌入(staging 缺→不加节,parity 不破);缺列降级不抛;测试合成 fixture 无网络。

---

### Task 1: `autoresearch/scan/watchlist.py` + tests
- Files: Create `autoresearch/scan/watchlist.py`、`tests/scan/test_watchlist.py`
- [ ] 失败测试:load 缺文件空帧 / ingest_verify 草拟+按(code,born)去重 / check 四态+manual+unknown / render 触发置顶+空串
- [ ] 实现 `load_watchlist / ingest_verify / check / run_check / render_watchlist_block`(词表 v1:close_above/close_below/ma_bull/money_pos/manual;overall 规则见 spec §2.1)
- [ ] `uv run --no-sync pytest tests/scan/test_watchlist.py -q` 过 → commit `feat(scan): watchlist 结构化观察单 + 触发器日检`

### Task 2: `autoresearch/scan/menu.py` + tests
- Files: Create `autoresearch/scan/menu.py`、`tests/scan/test_menu_health.py`
- [ ] 失败测试:健康上涨计数(L2 vs 全市场)/ 落刀占比 / 行业 top3 / 缺列降级 / 缺文件 ""
- [ ] 实现 `menu_health(scan_dir) -> str`(spec §2.2 五项指标)
- [ ] pytest 过 → commit `feat(scan): L2 菜单体检块`

### Task 3: `autoresearch/learning/zero_buy_ledger.py` + tests
- Files: Create `autoresearch/learning/zero_buy_ledger.py`、`tests/learning/test_zero_buy_ledger.py`
- [ ] 失败测试:合成 2 日 attribution(0买/有买)→ roll 字段、render 对照行、空目录优雅
- [ ] 实现 `roll / render / main`(镜像 channel_ledger;CLI → `reports/learning/zero_buy_ledger.md`)
- [ ] pytest 过 → commit `feat(learning): 0买日市场对照 ledger`

### Task 4: assemble 嵌入 + SKILL 接线
- Files: Modify `autoresearch/scan/assemble.py`、`tests/scan/test_assemble.py`、`.claude/skills/scan-market/SKILL.md`
- [ ] 失败测试:有 watchlist_status.csv → summary 含 `👀 观察单日检`;无 → 不含;menu 同理(两态四断言)
- [ ] assemble:buy-list 段后嵌 watchlist 块、L2 概览后嵌 menu 块(lazy import,presence-gated)
- [ ] SKILL:步骤1后 run_check + 过目呈现"已触发";skeptic 后 ingest_verify + 编排层补 conds;闭环节提 zero_buy_ledger
- [ ] 全量 `uv run --no-sync pytest -q` + ruff → commit `feat(scan): 观察单/菜单块嵌入 L5 + SKILL 接线`

### Task 5: 运行态种子 + 真数据冒烟
- [ ] `context/watchlist.csv` 种子:胜宏 300476(narrative/conds=[ma_bull, close_above:314, manual:中报毛利止跌], invalidation=[close_below:298.5], born=2026-06-30)+ ingest 06-30 verify 对拍去重
- [ ] 真数据冒烟:对 `context/scan/2026-06-30` 跑 run_check + menu_health + zero_buy_ledger,人工核对(胜宏应"临近/待触发"、菜单应复现"健康上涨≈0"、ledger 应有 06-24 行)
- [ ] merge 回 main(本地),分支删除
