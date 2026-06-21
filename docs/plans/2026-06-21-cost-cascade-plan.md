# scan-market 成本级联 + 买单对抗验证 — 实施计划

> 设计:`docs/specs/2026-06-21-cost-cascade-design.md`。直接改 main(个人研究直连约定);commit 仅在用户明说时。

## 顺序(每步可独立验证)

### S1 · `scan_pipeline.py` 级联选择器(纯代码,可测)
- `parse_ratings_from_details(details_dir) -> dict[code,rating]`:glob `details/*.md`,复用项目 `parse_rating` 提五档;读不到标 `None`。
- `pick_buy_candidates(ratings, include=('Buy','Overweight')) -> list[code]`:Tier-2 名单(K2 门槛)。
- `pick_buylist(ratings, floor='Overweight') -> list[code]`:verify 名单(评级 ≥ floor)。
- `batch_finalists(df, size=3) -> Iterator[(idx, sub_df)]`:Tier-1 分批(对齐 `slice_l2_llm` 风格)。
- **selftest 段**:造 5 张假 rating → 断言 tier2/buylist 选对;`batch_finalists` 30→10 批。
- 验证:`uv run --no-sync python scripts/scan_pipeline.py`(selftest)+ `ruff check scripts/scan_pipeline.py`。

### S2 · `screening-playbook.md` 重写 L3/L4 + 新增 L4.5
- 漏斗一图:L4 标 `Tier-1 Sonnet ×全 / Tier-2 Opus ×买点候选`;token 注改「Opus 仅 ~12 顶尖调用」。
- **L3 节**:模板加 `Agent(model='sonnet')`;判别口径不变。
- **L4 节**:拆 Tier-1(Sonnet,`batch_finalists` 3 只/子代理,完整 rubric)/ Tier-2(Opus,`pick_buy_candidates`,K1 默认 lite-on-Opus,注明切全量的开关)。
- **新增 L4.5 节**:`pick_buylist` → 每只 Opus skeptic;给 skeptic prompt 模板(攻击面 + verdict 枚举 + 触发位);产物 `_v_<code>.md` + `verify.csv(code,verdict,bear,trigger)`。
- 验证:人读自洽;`grep` 确认 model 指令落到每个 subagent 模板。

### S3 · `SKILL.md` 同步
- 阶段表 / token 注 / 漏斗图与 playbook 一致。

### S4 · `assemble_scan.py` 归档 + summary 徽标
- `_archive_reasoning`:加 `_l4_tier2_*`→`reasoning/l4/`、`_v_*`+`verify.csv`→`reasoning/verify/`。
- summary ③:buy-list 每行尾接 verify verdict 徽标(✅/⚠️/🛑 + 一句 bear)。读 `context/scan/<date>/verify.csv`(无则跳过,老路不破)。
- 验证:`uv run --no-sync python scripts/assemble_scan.py`(selftest)+ ruff。

### S5 · 全量验证
- 7 selftest(uzi_lenses / feedback_store / scan_pipeline / assemble_scan / self_review / retro / factor_lab)全绿 + `ruff check scripts/`。
- (可选,用户要才跑)重扫 6-18 验证买单方向一致 + verify 落盘。

## 不做 / 边界
- 不碰 L0/L1/L2、因子、召回权重。
- 不引付费 LLM API。
- 不动禁改文件(fred.py / test_fred.py / .DS_Store / .cursor-zsh / .omc / .vscode)。
- commit 仅用户明说;不 push。

## 回滚
- 纯增量:S1 新增函数不改旧签名;S2/S3 playbook 文本可 `git checkout`;S4 `_archive_reasoning`/summary 加分支(verify.csv 不存在=老行为)。任一步坏 → 单文件回退,不连坐。
