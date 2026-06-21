# AutoResearch 架构重构设计（scan/analyze/macro 统一漏斗）

> Phase 1 = 架构骨架（本 spec）。Phase 2 = L1 多路召回（单独 spec）。Phase 3 = L3 Opus-high 精修 + FinGPT 情感特征（单独 spec）。
> 设计经 brainstorming 收敛；实现 big-bang 重构、分步落地、每步 golden 对拍。

## 目标 / 非目标

**目标**
- 把扁平 `scripts/`（18 文件）重构成 `autoresearch/` 包,所有 skill（scan/analyze/macro）走统一 **Stage 契约**。
- **数据/模型/训练三层解耦**:统一数据层(取一次永不重取)+ 可插拔模型框架 + 统一粗排训练架构。
- **trace 现场全留**:每段结果都存;确定性段(L0/L1/L2)可复现,LLM 段(L3+)留逐字现场——供复盘/自我迭代。
- 包名对齐 repo:`tradingagents/` → `autoresearch/`(pyproject `name` 已是 `autoresearch`)。

**非目标(本 spec 不做,留后续 phase)**
- L1 改多路召回(Phase 2);L3 改 Opus-high holistic 精修 + 情感特征(Phase 3)。
- 本 spec 把现有各段**逻辑原样搬上新架构**(behavior-preserving),不改各段做什么。

## 决策摘要(brainstorming 定）
- 迁移 = **big-bang 结构**,但**分步落地 + 每步对拍**(非单一巨型 commit）。
- 数据层 = **统一**(cache + 特征 registry + store),全项目共享。
- 存储格式 = **Parquet + ZSTD**(数值数据最省、列裁剪);**存在即命中**,无 catalog DB;DuckDB 仅作可选按需查询。
- 模型 = **原生实现到我们接口**(Qlib 作架构/超参参考,无 qlib 运行时)+ **统一 Trainer** + **champion–challenger 门**。
- DataHandler 撑 **三态 feature_set**:`core`(横截面)/`seq`(每股滚动窗)/`graph`(关系图);**全 Qlib zoo 作原生迁移目标**,torch 作依赖(随表格神经/序列模型迁入时加;core 的 5 个树/线性模型不需要 torch)。
- trace 复现机制**只挂 L0/L1/L2**;L3+ 只存逐字 prompt+response;**每段结果都存**。

---

## A. 包结构 & Stage 契约

```
autoresearch/                       # ← 原 tradingagents/ 改名(27 文件 import 机械替换)
  dataflows/  agents/utils/         # 现有保留(rating.py 等)
  common/        # sw_sector_map · uzi_lenses · vol_series · 文本/评级工具
  data/          # 统一数据层:cache · sources · features(registry)· handler · endpoints(policy)
  models/        # 模型框架:base(Model ABC)· registry · trainer · catalog · gbdt/linear/xgb/cat/dbl/...
  trace/         # 现场存储:store · schema
  learning/      # feedback_store · self_review · retro · stage_eval(迁入,scan+feedback+retro 共用)
  scan/          # 扫描管道:pipeline · context · config · cli · stages/{l0..l5} · agents/{l3,l4,verify}
  analyze/       # analyze-ticker:stages/ · agents/ · cli
  macro/         # macro-research:stages/ · cli
```

**Stage 契约** `scan/stages/base.py`:
```python
class Stage(ABC):
    name: str
    def inputs(self) -> list[ArtifactKey]: ...     # 声明读哪些 trace 产物 / lake 特征
    def outputs(self) -> list[ArtifactKey]: ...    # 声明写哪些
    def run(self, ctx: RunContext) -> None: ...     # 读 typed 输入 → 写 typed 输出到 trace
```
- `pipeline.py`:按序跑 Stage,支持**断点续跑**(outputs 已在 trace 且 manifest=done 则跳过),`--from <stage>` 强制重跑。
- `context.py`:`RunContext`(analysis_date, run_id, 配置, DataHandler 句柄, TraceStore 句柄)。
- 段间**只经 trace 产物通信**,不传大 DataFrame。
- CLI:`python -m autoresearch.scan run <date>` / `analyze <ticker>` / `macro`;旧 `scripts/*.py` 删除或留薄 shim。

## B. 统一数据层（Parquet 湖 + 存在即命中）

```
autoresearch/data/
  lake/<endpoint>/<key>.parquet   # ZSTD;key = date / period / entity@as_of / static
  cache.py      # get_or_fetch(endpoint, params):exists?读 : 拉→原子写(临时文件 rename)
  sources.py    # tushare/akshare/fred/yfinance 唯一取数入口
  endpoints.py  # 端点 policy registry(见下)
  features.py   # 特征 registry:name → {source, compute, deps, kind}
  handler.py    # DataHandler.materialize(date|dates, feature_set, kind) → typed 帧/张量
```

**端点 policy registry**(`endpoints.py`)——决定怎么 key、是否入湖、今天是否取新:
```python
ENDPOINTS = {
  "daily":            {"key": "date",        "settle": "eod"},    # ① 入湖·永不重取
  "stk_holdernumber": {"key": "as_of",       "settle": "eod"},    # ② 按取数日快照
  "spot_em":          {"key": None,          "settle": "live"},   # ③ 不缓存(盘中)
  # … 新端点 = 加一行
}
```
**规则**:`date < 今天(或收盘后)`→ 可入湖;`date == 今天盘中`→ 取新。空结果写空 parquet(存在即"取过且为空")。

**分桶**(全量数据归类):
- **① 入湖·永不重取**:daily/daily_basic/stk_factor_pro/cyq_perf/moneyflow/hk_hold/margin_detail/block_trade/top_list·top_inst/forecast/express/income·balance·cashflow·fina_indicator/dividend/pledge_stat/index_dailybasic/limit_list/FRED/akshare 宏观/yfinance 历史。
- **② 入湖·按取数日快照**:股东户数/解禁队列/卖方目标/news/stock_basic·trade_cal。
- **③ 不入湖·总取新**:spot_em/fund_flow_rank 今日/zt_pool/当天盘中未结算。
- **④ 进 trace(非 lake)**:各段产物 + LLM 现场(见 D)。
- **⑤ 独立 store**:weights/gbdt_model(models/)、feedback/lessons(learning/)。

**迁移**:现 `context/factor_lab/cache/*.pkl`(84 天)→ parquet 迁进 lake,抽样校验值一致。

## C. 模型框架（统一粗排训练架构）

**① Model 接口**(`models/base.py`)——只管学和打分,不碰取数/特征:
```python
class Model(ABC):
    feature_set: str          # "core28" / "seq60" / "graph" … 我们特征库的命名视图
    kind: str                 # tabular | seq | graph
    def fit(self, ds: Dataset) -> FitReport: ...
    def predict(self, feats) -> pd.Series: ...   # → 每只一个横截面分(越高越看多)
    def save(self, path); @classmethod load(cls, path)
```

**② Registry + config 实例化**:`@register("lgbm")` + `build(cfg)`;换模型 = 改 config `kind`,不动数据/段。

**③ 三态 DataHandler**(都从 lake 物化,零重取):
- `core` 横截面表格 [股 × 特征] → Linear/LightGBM/XGBoost/CatBoost/DoubleEnsemble/MLP/TabNet
- `seq` 每股滚动窗 [股 × 时间 × 特征](lake 日线时序切)→ LSTM/GRU/ALSTM/TCN/Transformer/Localformer/TFT/TRA
- `graph` 横截面 + 个股关系图(申万行业共属 → 现成;相关性图可选)→ GATs/HIST/IGMTF/SFM

**④ 统一 Trainer**(`models/trainer.py`)——所有模型同一条训练/评估/晋升流水线:
```python
class Trainer:
    def __init__(self, handler, label: LabelSpec, splitter: TimeSeriesSplit): ...
    def train(self, model_cfg, dates) -> TrainedModel:
        panel = handler.materialize(dates, model_cfg.feature_set, model_cfg.kind)  # 我们特征,从 lake
        ds_tr, ds_va = splitter.split(panel, self.label)                           # 时序切(无前视)
        m = build(model_cfg); m.fit(ds_tr); 
        return TrainedModel(m, self.evaluate(m, ds_va))                            # oos rank-IC 统一口径
    def promote_if_better(self, challenger, champion) -> bool: ...                  # champion 门统一
```
- label(T+1 开到开 rank-norm)、时序切、oos rank-IC、champion–challenger **全在 Trainer 定义一次**,kind-agnostic(所有模型最终吐横截面分 → 同口径评)。
- 加模型 = 写 Model 子类 + 注册 + 声明 feature_set/kind,**自动走统一流水线**。

**⑤ champion–challenger**:`models/store/<name>/<version>.pkl` 版本化 + `champion.json` 指针;challenger oos 赢现任 champion 才晋升。**linear 是默认 champion**;GBDT/神经/序列/图都是挑战者,谁 oos 赢谁上线。"绝不部署比线性差的模型"= 通用机制。

**⑥ 全 zoo 迁移**(`models/catalog.py` 在册,状态 ported/pending)——Qlib 作架构参考,原生实现:
- **Phase 1 实交付**:Linear / LightGBM / XGBoost / CatBoost / DoubleEnsemble(core,无 torch)。
- **torch 表格**:MLP / TabNet(core,torch)。
- **序列**:LSTM/GRU/ALSTM/TCN/Transformer/Localformer/TFT/TRA(seq,torch + 序列 DataHandler)。
- **图/其他**:GATs/HIST/IGMTF/SFM/KRNN…(graph,torch + 图层)。
- catalog 列全部,前置(feature_set 层 + torch)满足即逐个迁,统一进 Trainer。

## D. trace 现场存储

```
trace/<run_id>/                  # run_id=<YYYYMMDD>_<HHMM>(运行时刻;analysis_date 在 manifest,解耦)
  manifest.json    # analysis_date · 配置 · 各段 status+耗时 · git sha · champion 模型版本
  stages/          # 每段 typed 结果:L0_universe/L1_recall/L2_rank/L3_finalists(.parquet)、L4_cards/<code>.md + ratings.parquet、L5_summary.md
  inputs/          # 仅 L0/L1/L2:<stage>.json = {feature_set, lake_keys:[...], model:champion@v3, config}
  agents/          # 仅 L3+:L3_select/{prompt,response} · L4/<code>/{prompt,card} · verify/<code>/{bull,bear,verdict}
  metrics/         # stage_eval edge(.parquet) + token 估算(.json)
```

- **产物 typed**:`trace/schema.py` 定义每段产物列/dtype,读写经 schema 校验(防字段漂移)。表格 parquet,文本 md/json。
- **复现只给 L0/L1/L2**(确定性):存输入指针(lake keys + feature_set + 模型版本 + config),lake 不可变 → 可重放出同一结果。
- **L3+ 只留逐字现场**(LLM 非确定):prompt + response 原样,供 retro/feedback 看与学,**不假装能复现**。
- **每段结果都存**(L0–L5),无论确定与否。
- **输入是指针不是拷贝**:源在 lake、不复制进 run。
- **断点续跑**:outputs 在 trace 即跳过;段间只经 trace 通信。
- **复盘读 trace**:stage 产物 join lake 已结算前瞻收益算 edge;feedback 读 agents/ 真实 prompt+response 蒸馏经验。
- `reports/scan/<run>/` 仍是人看渲染视图(summary + 卡);trace 是机器全量现场,同 run 目录并存。

## E. 迁移 + golden 对拍 + 测试

**golden 对拍**(`autoresearch/scan/parity.py`):
- 重构前 `parity capture <date>`:现管道**确定性产物**(L0/L1/L2)快照成 golden。
- 重构后 `parity check <date>`:新管道在**同一 lake + 同 weights/model** 重跑,diff 新 vs golden。
- **不变量按集合+排序对**(非逐位浮点):召回集合一致、L2 top200 一致、名次一致即过(1e-9 容差)。
- **L3+ 不对内容**(LLM 非确定),只**结构校验**(schema/可解析/段齐)。

**测试**:每模块 `--selftest` 搬进 `tests/`(pytest)当回归网。

**分步落地(每步 green 再下一步,独立 commit)**:
1. 脚手架:`tradingagents/→autoresearch/` 改名 + 建空子包 + 搬 common/learning,selftest 绿。
2. data lake + DataHandler:`factor_lab/cache/*.pkl → parquet` 迁移(抽样校验),取数走新层。
3. 确定性段 L0/L1/L2 上 Stage 契约 + models/Trainer → **golden 对拍**。
4. L3/L4/L5 + agents + assemble 上契约 → 结构校验。
5. analyze/macro 进包上契约。
6. 改 skill 文档 + CLI、删旧 scripts。

## 成功标准(Phase 1 Done 的定义)
- `python -m autoresearch.scan run <date>` 端到端跑通,产物落 typed trace。
- **golden 对拍通过**:新 L0/L1/L2 与重构前**召回集/排名一致**。
- 五个 native 模型(linear/lgbm/xgb/cat/dbl)走统一 Trainer + champion 门;`models/catalog.py` 列全 zoo。
- 数据层**跨 skill 零重复取数**(lake 命中)、`pkl→parquet` 迁移值一致。
- 全 pytest 绿(原 selftest 迁入)。
- skill 文档/命令更新到 `python -m autoresearch.*`。

## 风险 / 开放项
- 包改名波及 27 文件 + 扁平 import 收口——机械但面广,靠 pytest 兜底。
- `pkl→parquet` 迁移须校验值一致(抽样 + 行数 + dtype)。
- 序列/图 feature_set + 神经模型训练慢、易过拟合 → champion 门是关键闸(赢不过线性不上线)。
- graph 关系图(相关性版)留可选,先用申万行业共属。
