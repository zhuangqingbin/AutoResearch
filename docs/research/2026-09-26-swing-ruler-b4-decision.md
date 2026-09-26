# 10 日尺 B4 裁决包(模板 · 冻结窗后由用户填裁)

- 状态:**模板,不是决定**。本文件只备料;是否把 `MAIN_RULER` 从 `gap_c1_o2` 换成 `fwd_10_oc` 是**用户裁决**(spec `docs/superpowers/specs/2026-09-26-daily-engine-consolidation-design.md` §5 B4),本波不实施任何换尺。
- 开裁前提(两条都满足才填):① buyability 批 4 十日真跑的冻结窗已结束;② 普查探针 `h1_ready = true`(H1 n_days ≥ 40):

```bash
uv run --no-sync python -m autoresearch.research.swing_ruler_census \
    --spec docs/research/2026-09-26-swing-ruler-family.spec.json --sizes-only
```

- 读数只采用一次:探针到门后跑**唯一一次**正式普查(登记的停机规则;目录已存在即拒),不挑时点、不重跑到好看为止。

## 0. 裁决问题与预注册分支

| 分支 | 触发(按登记规则,判读见 §1) | 含义 |
|---|---|---|
| A 提案换尺 | H1 = POSITIVE | 起草「`MAIN_RULER` → `fwd_10_oc`」变更计划(§3 清单 + §4 契约加法 + §5 回滚杆),仍由用户批 |
| B 维持 + 观察席只展示 | H1 ≠ POSITIVE 且 H2 = POSITIVE | 不换尺;§12/⑦ 保留为展示 |
| C 关闭 B 线 | H1 与 H2 同时不成立 | 停机规则:观察席只展示不推;memory 记负结果;不追加第五个假设 |

「不成立」= 判读不是 POSITIVE(UNPROVEN / NEGATIVE / INSUFFICIENT 都算);H1 若为 NEGATIVE 属**反向证伪**,要在 §6 明写。

## 1. 普查读数表(填)

来源:`$RPT/research/swing_ruler/FAM_SWING_RULER_20260926/cells.csv`(唯一一次正式读数)。基线列是 2026-09-26 在回填账本副本上的首跑(`docs/research/2026-09-26-swing-ruler-census.md`),只作对照。

| 假设 | 预期 | 基线(09-26 副本) | n_days | n_rows | 均值 pp | 块10 CI pp | q_BY | 判读 |
|---|---|---|---|---|---|---|---|---|
| H1 非 📌 finalist ∩ ≥Hold | positive | 34d · −2.55 · [−5.93, −1.03] · INSUFFICIENT | | | | | | |
| H2 lane = lowturn | positive | 6d · −3.11 · INSUFFICIENT | | | | | | |
| H3 UW / Sell | negative | 28d · −3.72 · [−6.12, −0.50] · INSUFFICIENT | | | | | | |
| H4 E6 BUY 两尺符号一致 | 描述性 | 7 笔 · 71% | — | | — | — | — | DESCRIPTIVE |

块长敏感性(块 1 / 5 / 10 全报,判读只看块 10):从 `readout.md`「块长敏感性」节抄录。

## 2. 观察席 40 日命中(填)

观察席 = §12(`scan/swing_seat.py`,每场落 `_swing_seat.json`):非 📌 finalist ∩ 卡评级 ≥Hold ∩ 卡面入场 ≠ 禁止。它与 H1 的差别只有「入场 ≠ 禁止」这一层过滤。计算口径(与普查同源,事后一次性脚本即可,不进生产):

> **历史席位实际上没有过滤(2026-09-26 复审 M3)。** 「入场」读的是卡面 `**入场**` 机读行,这条契约 09-25(`2fa2d38`)才上线:之前的卡全部读成 `UNKNOWN`(§12 印「未机读」),「入场 ≠ 禁止」对它们是空操作 —— 09-17 那场的 600150 / 600035 / 002078 卡上散文写着「本次不入场」,仍在席内。所以 09-25 之前各场的席位 ≈ H1 人口(外加 composite 席位口径差),「席位 vs H1」的差只能从 09-25 之后的场里读。另:`**入场**` 行回答的是「T+1 尾盘按执行线能否新开仓」(隔夜尺 c1 腿),不是 10 日尺的 D+1 开盘腿 —— 即便在 09-25 之后,这层过滤也是拿隔夜执行线去筛 10 日尺的票,读数时照此理解。

1. 逐场读 `_swing_seat.json` 的 `rows[].code`(同一分析日多场取 run_id 最大者,同普查);
2. 与 `recommendations.csv` 的 `outcome_status_swing == MATURE_10` 行按 `(run_id, code)` 对齐,取 `fwd_10_oc`;
3. 每行减同日全湖 D+1 开盘可买票的 fwd_10_oc 中位(`swing_ruler_census.market_baselines` 同一实现),先日内等权再跨日等权;
4. 未成熟 / 缺值行计数剔除,不当 0。

| 窗口 | 场数 | 席位行 | 成熟行 | 席内超额均值 pp | 命中率(超额 > 0 的日占比) | 块10 CI pp |
|---|---|---|---|---|---|---|
| 冻结窗后首 40 个成熟日 | | | | | | |

## 3. 若换主尺:消费点清单(2026-09-26 快照,裁决当日重跑)

```bash
grep -rn MAIN_RULER autoresearch | grep -v '\.pyc'        # 快照:82 处 / 25 个文件
grep -rn "\"gap_c1_o2\"\|'gap_c1_o2'" autoresearch          # 快照:26 处字面量(不走 MAIN_RULER)
grep -rn "gap_c1_o2\|c1→o2" .claude                          # 快照:6 处(l4-card.md 卡契约、scan_config.jsonc)
```

| 文件 | `MAIN_RULER` 处数 | 换尺时要问的问题 |
|---|---|---|
| `research/factor_lab.py` | 23 | IC/分位表主列与 horizon;历史读数不可与新尺拼趋势 |
| `common/ruler.py` | 13 | 单点定义;`ENTRY_FLAG`/`entry_flag_for`(c1 腿 → D+1 开盘 `buyable`)、`GAP_CLIP`、`REL_GAP_RULER`(I-4 钉死字面量) |
| `research/sector_top3_backtest.py` | 6 | 回测主尺 |
| `scan/relative_buy.py` | 4 | E6 相对 BUY 的评分尺与持有期 —— 是否随主尺迁移是独立裁决 |
| `scan/report_sections.py`、`scan/report_appendix.py`、`scan/relative_facts.py`、`scan/brief.py` | 3 / 2 / 2 / 2 | 报告文案与 brief ③ 的尺名 |
| `common/forward_returns.py` | 3 | `GAP_CLIP` 只对主尺置 NaN |
| `contracts/research_experiment.py` | 3 | 抄来的字面量 + `test_ruler_literals_do_not_drift`;预注册契约的 `ruler` 字段 |
| `analyze/ledger.py` | 3 | stock-research 账本 |
| `scan/ledger_views.py`、`scan/outcome.py`、`scan/populations.py` | 2 / 1 / 1 | 账本 `ruler` 列、`ledger_line` 均值、阶段尺 `RULER_HORIZON`/`RULER_BLOCK` |
| `research/w3_grids.py`、`funnel_variants.py`、`execution_audit.py`、`stage_value.py`、`overseas_event_census.py`、`overnight_census/panel.py`、`edge_census.py`、`swing_ruler_census.py` | 2/2/2/1/1/1/1/1 | 研究仪器:已登记实验的尺不能被静默换掉(新 experiment_id) |
| `scan/self_review.py`、`common/scoring.py`、`common/regime.py` | 1 / 1 / 1 | 自检文案、打分与 regime 口径 |

非代码契约:`.claude/agents/l4-card.md` 的「〔卡契约 v4·隔夜 c1→o2〕」标记与执行线;`scan_config.jsonc` 的尺注释;持有期(T+2 开盘 → T+10 收盘)与执行时点。

## 4. 卡片契约加法:`## 10 日情景`(可选节,草案)

契约**只加不改**(隔夜口径照旧,卡片 v4 不动);草案:

```markdown
## 10 日情景(可选 · 10 日尺 fwd_10_oc:D+1 开盘进 → D+10 收盘出)
- 基准:<一句> · 区间 <对 D+1 开盘 ±x%>
- 证伪线:<价格或事件>(触发即本节作废)
```

落地时的连带件(A 分支才做):`l4-card.md` 模板段 + `l4/parsers` 可选解析(缺节 = 未写,不是否定)+ `card_lint` 缺节不报错 + `test_agent_defs.py` 契约字样锁。

## 5. 回滚杆

- 换尺前(现状):`MAIN_RULER = "gap_c1_o2"` 一行;10 日尺只被观察席与账本读(spec §9 B)。去掉 §12/⑦ = `report_sections.render_summary` 的一行 + `brief._sections` 的 ⑦ 块,其余零影响;`_swing_seat.json` 与 `stage_rulers.csv` 的 fwd_10 孪生格可留作只记不学的读数。
- 换尺后(A 分支):换尺提交单独成批;回滚 = revert 该批(`MAIN_RULER` 一行 + E6 规则版本号 + 卡契约加法);回滚后先跑 `capsule replay` 证明确定性产物回到换尺前的字节。

## 6. 裁决记录(用户填)

- 读数日期 / run: 
- 分支:☐ A 提案换尺 ☐ B 维持 + 只展示 ☐ C 关闭 B 线
- 理由(一句):
- H1 是否反向证伪:☐ 否 ☐ 是(写进 memory 负结果)
- 裁决人 / 日期:
