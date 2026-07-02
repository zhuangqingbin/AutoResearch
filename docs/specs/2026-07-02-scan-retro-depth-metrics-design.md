# scan retro 度量深化:T+5 盲区审计 / L3 错杀审计 / floor 自然实验 — 设计

- 状态:设计定稿(待实现)
- 日期:2026-07-02
- 触及:`autoresearch/learning/retro.py`(attribute/write_retro_input)、`.claude/skills/scan-retro/retro-playbook.md`
- 关联:`pr_20260702_001`(regime 块 horizon 之争,本 spec ⑥ 供裁决数据)

## 背景

retro 现状三个度量盲区:① 赢家/盲区归因只有 **fwd_1_oo(T+1)口径**,而 L3/L4 猎的是 T+5 swing——"漏斗对 swing 赢家是否失明"没有对口径的答案(也是 regime 块 T+1 vs fwd_5 之争的裁决数据);② L3 每天杀 ~170 只,**无错杀验尸**(fwd_5 涨得好的落选者长什么样、当时红队理由是什么,从不复盘);③ L2 风格 floor 救回的票(`l2_lane_reserved`)**从未度量**(多样性保底值不值,是信仰不是数据)。

## 设计(全确定性,零 LLM,扩展现有 retro 产物)

### ① T+5 盲区审计(`retro.attribute` 扩展)

- attribution 增列:`winner_5`(tradable ∧ fwd_5_oc ≥ 截面前10% ∧ ≥5%)、`bucket_5`(同现有 bucket 规则但按 winner_5)。fwd_5 未成熟(NaN)→ winner_5=False、bucket_5=""(降级不抛,retro 补跑成熟日自然覆写)。
- `write_retro_input` 增节 **"T+5 盲区(swing 口径)"**:winner_5 数、各桶计数、missed_l1(T+5)top10 因子行——与 T+1 节并排,直接可比"两个 horizon 的漏斗失明差异"。
- **quota 接线**:retro-playbook 第 4 步(建议)增加:`channel_ledger` `n_days≥3` 且某路 `unique_excess_t5` 持续负 → 调 `propose_quota_adjustments`(已有机制,advisory)写 proposals;正路(如 momentum +9.2%)持续强 → 建议升 quota。人批,不自动。

### ② L3 错杀审计(`write_retro_input` 增节)

- **"L3 错杀验尸(fwd_5)"**:集合 = L2-keep(L2_gbdt_top200)∧ 非 finalist ∧ fwd_5_oc 截面前10%;join `L3_judged_full.csv`(thesis/risk/triage_lean/lane/conviction/fragility)→ top10 表(fwd_5、当时的红队理由、lane)。
- 读法(playbook 第 2 步增):错杀群体的 `risk` 文本共性 = L3 的系统性偏见候选(如"获利盘满"恐惧在反转市错杀);反复出现 → 第 5 步 upsert_lesson(经验注回 L3 校准块,E1 管道已有)。
- L3_judged_full 缺(旧目录)→ 节降级为"无 L3 判分数据"。

### ③ floor 自然实验(`write_retro_input` 增节)

- **"L2 floor 自然实验"**:join attribution fwd × `L2_gbdt_top200.l2_lane_reserved` → 三组 fwd_1/fwd_5 均值:`floor 救回`(reserved>0)/ `merit 入选`(reserved=0)/ `被挤掉`(L1 召回 ∧ 非 L2);各组 n。纯函数 `floor_experiment(l2df, attr) -> dict` 可离线测。
- 读法:救回组 ≈ merit 组 → floor 免费(维持);持续显著弱于被挤掉组 → floor 参数该复审(建议,人批)。

## 测试(合成 fixture,无网络)

- `tests/learning/test_retro_depth.py`:
  - winner_5/bucket_5:合成面板(fwd_5 有值/NaN 两态)→ 计数与降级;
  - L3 错杀节:合成 L2/finalists/L3_judged/attr → 入节 top 行含 risk 文本;缺 L3_judged 降级;
  - floor_experiment:合成 reserved/merit/挤掉三组 → 均值与 n 正确。

## 非目标

- 不自动改 quota/floor(advisory,proposals 走人批);
- 不改 winner(T+1)现有口径(向后兼容,新增列并存);
- L3 错杀只出数据与病例,结论/经验由 scan-retro 的 Claude 步骤写。
