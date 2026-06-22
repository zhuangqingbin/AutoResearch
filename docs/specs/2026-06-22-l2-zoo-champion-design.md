# L2 全特征落湖 + 全 zoo 训练 + 多-horizon champion — 设计

> 状态:已 brainstorm 定稿(2026-06-22)。下一步 → writing-plans。
> 关联:`docs/specs/2026-06-22-autoresearch-arch-redesign-design.md`(§B 湖 / §C 模型框架)。

## 1. 背景与动机

L2 粗排现在**实际跑的是线性复合分**,不是 GBDT:`gbdt_model.pkl` 的 `beats_linear=False`
(oos rank-IC GBDT **+0.0173** vs 线性 **+0.0195**),自保门让 `predict_scores` 回落线性。
根因诊断:

- **样本太薄**:训练面板仅 **84 个成型日 / 365k 行**,GBDT 在薄面板上过拟合 → oos 输线性。
- **lake 未建**:`context/lake` 不存在;`DataHandler`(湖读路径)从没被喂过数据。历史只在
  `context/factor_lab/cache/*.pkl`。
- **没有 zoo 训练 runner**:`Trainer` / `save_champion` / `catalog`(20 模型) / `DataHandler`
  (core·seq·graph 三视图)零件齐全,但没有脚本把它们串成"训全 zoo → 比 OOS rank-IC → 晋升
  champion"。`models/store` 为空。

**特征(列)其实已相当完备**:`feature_columns("core")` 45 列已含动量+3 变体 / 资金(主力·散户·
结构) / 筹码 / 技术 / 北向 / 估值 / 多日量价(cmf·obv·vwap·breakout) / **UZI(rz_ratio·
block_premium·lhb_inst_net)**——即"取了没用"的融资融券/大宗/龙虎榜机构在新湖路径已闭合
(旧 factor_lab GBDT 只用其中 29 列子集)。故本项目**不补横截面特征**,把杠杆压在**样本深度**与
**训练/晋升基建**上。

## 2. 目标 / 非目标

**目标**
1. 把 ~2 年全市场历史**落进 parquet 湖**(`context/lake/<endpoint>/<date>.parquet`),取一次永久复用。
2. 一条 runner **训练全 20 个 zoo 模型**(core 7 / seq 10 / graph 3),三个 horizon
   (`fwd_1_oo` / `fwd_5_oc` / `fwd_10_oc`)各产一个 **champion**(OOS rank-IC 最高且 > 线性基线)。
3. L2 默认加载 **swing 对齐的 champion**(`fwd_5`),缺/未胜线性 → 回落线性(铁律不变)。

**非目标(本期不做)**
- 不补季度基本面 / growth 组(慢因子对 T+1/swing 弱,留后续)。
- 不动 `weights.json`(T+1 校准)——线性 composite 仍作 L1 复合分 + GBDT 锚定特征 + L2 回落基线。
- 不加 seq 的额外日级通道(现 r/rng/amt 三通道照旧)。
- L3 web 外源新闻是**独立子项目**(单独 spec)。

## 3. 范围决策(brainstorm 已定)

| 维度 | 决定 |
|---|---|
| 特征范围 | 现有 45 列 core(seq 60 / graph 58 不变),不补基本面 |
| 样本深度 | harvest ~2 年历史入湖(现 84 → 目标 ~160 成型日;step 3 交易日) |
| 训练标签 | `fwd_1_oo`(T+1)+ `fwd_5_oc` + `fwd_10_oc`,各 horizon 一个 champion |
| 模型范围 | 全 20(故障隔离;首个 champion 多半 core 胜) |
| L2 选用 | 默认 `l2_fwd5`(可 `ScanConfig.l2_model` 覆盖) |

## 4. 架构

```
                P-A 落湖                          P-B 训练 + 晋升
  tushare ──get_or_fetch──> context/lake/         DataHandler.materialize(view, label)
  (~2yr)    (eod 永不重取)   <endpoint>/<d>.parquet   │  core/seq/graph 三视图
                                   │                  ▼
              migrate_cache ───────┘            Trainer(label=horizon).train(cfg, dates)
              (84 日 pkl 种子)                        │  → TrainedModel(oos_rank_ic)
                                                      ▼
                                          champion 门:max OOS rank-IC 且 > 线性
                                                      │  save_champion("l2_<horizon>")
                                                      ▼
                                   models/store/l2_fwd5/champion.json
                                                      │
                                   L2Rank / universe.run 加载 → 重排 1000→200
```

### P-A:特征/样本补全 + 落 lake

**两步种子 + 扩历史:**

1. **种子(零取数)**:`migrate_cache.migrate()` 把现有 `context/factor_lab/cache/*.pkl`(84 日)
   迁成 `context/lake/*.parquet`(已实现,幂等)。

2. **扩历史(网络,可断点续)**:新 CLI `python -m autoresearch.data.harvest <start> <end> [--step 3]`:
   - `plan_harvest(pro, start, end, step, back=60, fwd=10)` → `(F, P)`:
     - **F 成型日** = `[start, end]` 内每 `step` 个交易日取一个。
     - **P 价格面板** = `[F[0]-back, F[-1]+fwd]` 的连续交易日(供 60d 动量回看 + 10d 前瞻收益)。
   - 对每个 P 交易日 → `get_or_fetch("daily", {"trade_date": d}, today=end)`。
   - 对每个 F 成型日 → `get_or_fetch(ep, {"trade_date": d}, today=end)`,ep ∈ core 所需 9 端点:
     `daily_basic, stk_factor_pro, cyq_perf, moneyflow, hk_hold, margin_detail, block_trade, top_inst`
     (+ `daily` 已在 P 拉)。
   - `get_or_fetch("stock_basic", {}, today=end)` 一次(static)。
   - **限频** `--sleep 0.35`(礼貌)+ **断点续**(lake 命中即跳,零成本重跑)。
   - **`fields` 不限**:`sources.fetch` 取全列;handler 只读所需列,多余列无害。

3. **handler 保留 3 个 fwd 标签**:`DataHandler.materialize` 现仅留 `fwd_1_oo`+`buyable`;
   改为三个分支(core/seq/graph)都保留 `fwd_1_oo` / `fwd_5_oc` / `fwd_10_oc` / `buyable`,
   让 `Trainer(label=任意 horizon)` 能取到对应标签列。`forward_returns` 已算齐三者(无新数学)。

### P-B:全 zoo 训练 + champion 晋升 runner

**新模块** `autoresearch/models/zoo.py` + CLI:

```
python -m autoresearch.models.zoo train \
    --dates-from 2024-06-01 --dates-to 2026-06-01 --step 3 \
    --horizons fwd_1_oo,fwd_5_oc,fwd_10_oc \
    [--models linear,lgbm,...]   # 缺省 = catalog.ported() 全 20
```

核心函数:

```python
def train_zoo(handler, dates, horizons, model_names=None, *,
              price_dates=None, cap_floor=30.0) -> pd.DataFrame:
    """对 horizons × model_names 笛卡尔积逐个训练,返回 leaderboard;每 horizon 晋升 champion。

    - model_names 缺省 = catalog.ported()(20)。
    - 每 (horizon, model):
        cfg = ModelConfig(kind=MODELS[name]["kind"], feature_set=MODELS[name]["feature_set"])
        trained = Trainer(handler, label=horizon).train(cfg, dates, price_dates=..., cap_floor=...)
        记 oos_rank_ic;**单模型异常 → 记 error 跳过,不中断全 zoo**。
    - 线性基线:每 horizon 用 ModelConfig(kind="linear") 训一次作对照 lin_ic。
    - champion:该 horizon 下 oos_rank_ic 最高且 > lin_ic → save_champion(f"l2_{tag(horizon)}", best, version)。
        无人胜线性 → 不晋升(L2 回落线性)。
    - 返回 leaderboard DataFrame[horizon, model, feature_set, oos_rank_ic, vs_linear, status, champion]。
    """
```

- **horizon → champion 名**:`fwd_1_oo→l2_fwd1`、`fwd_5_oc→l2_fwd5`、`fwd_10_oc→l2_fwd10`。
- **产物**:`context/factor_lab/zoo_leaderboard.csv`(model×horizon×IC vs 线性)+ champion 落
  `models/store/l2_<h>/<version>.pkl` + `champion.json` + reasoning 留痕(每 horizon 一段晋升说明)。
- **Trainer 修一处(整合 bug)**:`Trainer.train` 现在 `materialize(dates, feature_set=cfg.feature_set)`
  **漏传 kind** → seq/graph 模型走 core 分支、特征全 NaN。改为
  `materialize(dates, feature_set=cfg.feature_set, kind=cfg.feature_set, ...)`(core 行为不变,
  修好 seq/graph 取对视图)。

### L2 接线

- `ScanConfig` 加 `l2_model: str = "l2_fwd5"`(已有该字段则改默认)。
- `L2Rank._champion` / `universe.run` 已按 champion 名 `load_champion(name, LinearComposite)` 加载;
  注意 champion 反序列化用 `model_cls` —— zoo 晋升的可能是**任意 kind**(lgbm/mlp/…),故
  `load_champion` 需用**正确的 Model 子类**反序列化。设计:champion.json 记 `kind`,加载时
  `registry` 按 kind 取类(`_REGISTRY[kind]`)→ `cls.load(pkl)`。新增
  `load_champion_any(name)`(按 champion.json 的 kind 自解析),L2 改用它;缺/失败 → 回落 `LinearComposite`。

## 5. 数据流

`tushare → get_or_fetch → lake parquet` → `DataHandler.materialize(view, label=horizon)` →
`Trainer.train → oos rank-IC` → `champion 门 → store` → `L2Rank.load_champion_any("l2_fwd5") →
predict → 重排 top200`。**确定性、零 LLM**(沿用 L2 铁律)。

## 6. 错误处理 / 自保

- **harvest**:单 (endpoint,date) 拉取失败 → `get_or_fetch` 写空 parquet 或抛错;runner 记 warn
  续跑(空端点不阻塞)。断点续靠 lake 命中。**长任务后台跑**。
- **训练**:单模型 OOM/不收敛/接口不符 → try/except 记 `status=error:<msg>`,继续下一个
  (一个坏模型不毁全 zoo)。
- **champion 门**:`oos_rank_ic` 为 NaN 或 ≤ 线性 → 不晋升。**绝不部署比线性差的模型**(铁律)。
- **L2 加载**:champion 缺失 / 反序列化失败 → 回落 `LinearComposite`;golden parity(composite
  口径)仍绿。

## 7. 文件清单

| 动作 | 文件 | 职责 |
|---|---|---|
| 新建 | `autoresearch/data/harvest.py` | lake-native 历史 harvest(plan_harvest + CLI) |
| 改 | `autoresearch/data/handler.py` | materialize 三分支保留 3 个 fwd 标签 |
| 改 | `autoresearch/models/trainer.py` | train 传 `kind=cfg.feature_set`(修 seq/graph) |
| 新建 | `autoresearch/models/zoo.py` | train_zoo + champion 晋升 + leaderboard + CLI |
| 改 | `autoresearch/models/trainer.py` | 加 `load_champion_any(name)`(按 kind 自解析) |
| 改 | `autoresearch/scan/config.py` | `l2_model` 默认 `l2_fwd5` |
| 改 | `autoresearch/scan/stages/l2_rank.py` + `autoresearch/scan/universe.py` | 用 `load_champion_any` |
| 文档 | `.claude/skills/scan-market/{SKILL.md,screening-playbook.md}` | L2 模型/训练/champion 说明更新 |

## 8. 测试策略(合成 fixture,无网络)

- `tests/data/test_harvest.py`:`plan_harvest` 的 F/P 区间正确(step、back、fwd 边界);harvest
  用注入 `fetch` 桩落湖、断点续(二次跑零取数)。
- `tests/data/test_handler_labels.py`:materialize(core/seq/graph)三视图都含
  `fwd_1_oo/fwd_5_oc/fwd_10_oc/buyable`。
- `tests/models/test_zoo.py`:迷你合成 lake 上 `train_zoo` 跑通 → leaderboard 列齐;注入一个
  抛错模型 → 其余照常、该模型 `status=error`;champion 门(只在胜线性时晋升,落 store)。
- `tests/models/test_champion_any.py`:`save_champion` 一个非线性 kind →
  `load_champion_any` 按 kind 正确反序列化 → predict 可用。
- `tests/scan/test_l2_champion.py`:L2 加载 `l2_fwd5` champion 重排;champion 缺失 → 回落线性;
  golden parity(composite)仍绿。
- **真数据冒烟**(非 CI):`migrate()` 84 日 → `train_zoo(core-only, fwd_1_oo, 小样本)` 端到端通。

## 9. 验收标准

1. `python -m autoresearch.data.harvest` 把 ~2 年 9 端点落湖,断点续幂等。
2. `python -m autoresearch.models.zoo train` 产出 leaderboard(20 模型 × 3 horizon,坏模型隔离)+
   每 horizon champion 指针(或诚实标注"无人胜线性")。
3. L2 默认加载 `l2_fwd5` champion;缺则回落线性,parity 绿。
4. 全部新增/改动有合成 fixture 测试,`pytest` 全绿,ruff 干净。
5. **诚实**:扩样本**不保证**胜线性;leaderboard 如实显示,不胜则 L2 继续回落线性、文档写明。

## 10. 诚实风险

- 2 年 harvest 是长限频任务(~1.7k 次调用,已迁的跳过)→ 后台 + 可续。
- 15 个 torch 模型(seq/graph)首次端到端跑大概率暴露**整合 bug**(特征选择 / NaN / 张量形状)——
  计划先跑 core 7 验管道,再 seq/graph,逐个 test/fix。
- seq/graph 特征薄(seq 仅价格三通道)→ 首个 champion 多半 core 胜,符合预期。
- 扩样本是已知最大杠杆,但**非充分条件**;若仍不胜线性,价值落在"诚实 leaderboard + 可复用湖 +
  可插拔 champion 框架",L2 安全回落线性。
