# Phase A — UZI 增量 → 新增 L1 候选因子(IC 证伪)

**Goal:** 把市场级可算的 UZI 数据(融资融券/大宗/龙虎榜机构席位)接成 `factor_lab` 候选因子,跑 T+1 rank-IC,**用数据判它们进不进复合分**。
**Spec:** `docs/specs/2026-06-20-uzi-integration-design.md` §3。
**前置:** 无(全在 `factor_lab.py` 现成骨架上扩展;实测端点全部可达)。

## 文件
- 改 `scripts/factor_lab.py`(新端点 + 新因子 + CANDIDATES)

## Task 1 — 新端点缓存
- `_FIELDS` 加:
  - `margin`: `"ts_code,rzye,rqye,rzmre,rzche,rzrqye"`(margin_detail,trade_date 维)
  - `block`: `"ts_code,price,vol,amount"`(block_trade)
  - `top_inst`: `"trade_date,ts_code,exalter,buy,sell,net_buy"`
- `harvest`:成型日 F 循环里多缓存这 3 个端点(`_cache(ep, d, _fetch(pro, ep, d))`);daily/daily_basic 已缓存,增量仅新端点。
- 注:`_fetch` 走 `getattr(pro,ep)(trade_date=day, fields=f)`,三者签名兼容。

## Task 2 — 新因子(factor_frame)
读缓存 pkl,merge 到 `f`(按 code),计算(单调即可,单位不苛求):
- `rz_ratio = rzye / circ_mv`(融资杠杆水平;circ_mv 来自 daily_basic,已有)
- `rz_buy_intensity = rzmre / (amount_千元)`(融资买入强度)
- `block_premium = mean(block.price by code) / close − 1`(大宗折溢价;稀疏)
- `block_intensity = sum(block.amount by code) / circ_mv`(大宗活跃;稀疏)
- `lhb_inst_net = sum(top_inst.net_buy where exalter=="机构专用" by code) / amount`(龙虎榜机构净买;稀疏事件)
覆盖率低的(block/lhb)merge 后 NaN → 保持 NaN(IC 仅在非空子集算,同 hk_ratio)。

## Task 3 — CANDIDATES + eval
- `CANDIDATES` 加 5 个新因子(方向先验:rz 类 +1 试,block_premium −1 试,其余 +1;真符号由 IC 定)。
- `uv run --no-sync python scripts/factor_lab.py harvest`(只补新端点)→ `eval`。
- 读 `ic_table.csv`:看每个新因子 `IC_fwd_1_oo` / `ICIR` / `t` / IC_h1 vs IC_h2(regime 稳定性)。

## Task 4 — 结论 + 纳入(只纳有信号的)
- **有 T+1 信号**(|IC| 显著、半样本同号)→ 并入 `screen_market._factor_groups`(融资类并入资金组或新设"杠杆"组)+ `calibrate` 重标定 `weights.json`。
- **噪声/稀疏**(block/lhb IC 不稳)→ **退到 L3 证据**,不进 L1;spec §实证 记一句。
- retro 后续持续校准(已有闭环)。

## Task 5 — 测试 + 验收
- `factor_lab.py --selftest`(IC/收缩数学)仍绿;ruff 绿。
- 产出:`ic_table.csv` 含新因子行 + 一段"进/不进复合分"的数据结论。
- 不碰排除文件;不 commit;`uv run --no-sync`;tushare venv-only。

## 验收
新因子有**明确数据裁决**(IC 进/不进);进的并入复合分并重标定。**这是 UZI 增量价值的可证伪检验。**
