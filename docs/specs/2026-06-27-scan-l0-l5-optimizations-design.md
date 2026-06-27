# scan-market L0–L5 优化套件 — 设计

> 状态:设计已定,自主实施(用户授权"全部做到结束")。一条分支 `scan-l0-l5-optimizations`,
> 每项 TDD + 单独 commit。**铁律:所有行为变更默认关(flag)/默认保形 → L0/L1/L2 golden parity 不破。**

## 背景

`reports`/`context` 漏斗六段当前实现(2026-06-27 核对源码):
- L0 选集 `_recall_gate_a` 轻门 → L1 召回 9 路 channel + `composite_score`(IC 校准)→ L2 `select_l2`
  确定性分层采样(ML-free)→ L3 单 holistic Opus 精排 → L4 一只一 Opus 渐进深度 → L5 `assemble` 整合。
- **根因痛点**:`composite` 权重 = **单 horizon T+1 IC**(`weights.json` 84 日校准),momentum/tech/chip/
  volprice 的 IC 几乎全负(T+1 均值回归)。趋势 regime 下这些符号翻正,但静态校准看不到 → "连续两天 0 买"
  式的 regime 错配。
- L2 的 `l2_fwd5` champion 已弃用("确定性 L2 无 alpha"),但"确定性无 alpha ≠ 学习排序无 alpha"未验证。
- 情感链路全程关键词粗标(`_EVENT_TAGS`),未用上 Claude 当情感引擎。
- 闭环 `channel_ledger`/`retro` 只出 proposal,调权重/quota 仍人工。

## 目标与非目标

**目标**:把上面四个根因做成可上线的确定性机制(+ 若干低风险 leaf 项)。
**非目标**:不改 L3/L4 的 in-session LLM 判断主干(那是 skill 编排,非确定性代码);不引入付费 API;
不在确定性层放 LLM。alpha 类结论(regime 权重是否真赢、champion 是否真有 alpha)需真数据跑验证,
本套件只交付**可复现的机制 + 单测(合成 fixture,无网络)**,真数据校准/对照另跑。

## 架构:新增/改动单元

### F — `autoresearch/common/regime.py`(新增,地基)
确定性市场 regime 分类器,**零网络**,从已有横截面帧算。L1 权重选择、L5 标注、闭环 drift 共用。
```python
@dataclass(frozen=True)
class RegimeState:
    label: str          # "trend" | "range" | "risk_off"
    breadth: float      # 站上 MA60 占比 0..1(缺 above_ma60 → pct_60d>0 占比代理)
    med_mom: float      # 截面 median pct_60d
    n: int
    def to_dict(self) -> dict: ...

def classify_regime(frame, *, breadth_hi=0.55, breadth_lo=0.30,
                    ma_col="above_ma60", mom_col="pct_60d") -> RegimeState
```
规则(policy,阈值可调,落 meta 备查):breadth≥hi 且 med_mom>0 → `trend`;breadth≤lo 且 med_mom<0
→ `risk_off`;否则 `range`。空帧/缺列 → 安全退化 `range`(中性,不误导)。

### A — L1 多 horizon + regime-aware 权重(默认关)
- **schema**:`weights.json` 增可选 `regimes` 块:
  `{"meta":…, "weights": {…flat 默认…}, "regimes": {"trend": {"weights": {…}}, "range": {…}, "risk_off": {…}}}`。
- `common.scoring._load_weights(path, regime=None)`:`regime` 给定且 `regimes[regime]` 存在 → 返回
  `{"meta":…, "weights": regimes[regime]["weights"]}`;否则返回 flat(**现行为,parity 不破**)。
- `factor_lab.calibrate_regimes(...)`:把 84 日 panel 按"每日 regime"(`classify_regime` 逐日)分桶,
  每桶内复用现 `_ic_by` 逻辑算 IC → 写 `regimes` 块(同时保留 flat `weights` = 全样本,向后兼容)。
  多 horizon:`calibrate` 增 `label_col` 参,可对 `fwd_1_oo`/`fwd_5_oc`/`fwd_10_oc` 任一校准(默认仍 T+1,
  parity 不破);regime 桶 + 多 horizon 正交,可组合产出 `regimes` 块。
- **接线**:`ScanConfig.regime_aware: bool = False`。L1Recall/`universe.run`:off → 原样;on → 从召回帧
  `classify_regime` 得 label,`_load_weights(regime=label)`。off 时 golden parity 严格复现。

### D — 闭环半自动(默认 advisory,不自动改线上)
- `channel_ledger.propose_quota_adjustments(ledger, *, min_days=3, neg_thresh=0.0)` →
  `[{channel, cur_quota, proposed_quota, reason}]`:持续负边际超额(`mean_unique_excess_t5<neg_thresh`
  且 `n_days≥min_days`)→ 提议降 quota;持续正 → 提议升。纯函数,**不写线上**。
- `channel_ledger.apply_proposals(proposals, channel_defaults, *, max_delta_frac=0.25)` →
  新 quota dict(单次调整幅度封顶 25%,防抖)+ audit 行。**仅当显式调用**才生效(默认人工 gate)。
- `regime.detect_drift(current: RegimeState, weights_meta: dict)` → `(drifted: bool, reason)`:
  weights.json `meta` 记其校准期主导 regime;今日 regime 与之不同 ≥ 一定持续 → `drifted=True`。
  surfaced 进 `self_review`(warn)+ assemble banner("regime 漂移,建议重校准")。

### C — 情感链路升级(`autoresearch/scan/agents/l3_news.py`,仍零 LLM)
- `score_title(title) -> (direction: str, intensity: float)`:在 `_EVENT_TAGS` 基础上加
  ①否定/反讽词消解(如"未/不/否认/澄清"翻转或置中)②强度(命中词数 + 强词权重)③去重。
- `news_digest` 增数值字段 `*_sent`(∈[-1,1],利多正/利空负,按 intensity 加权)与原 `*_tags` 并存,
  喂 L3 holistic 表多一列净情感分。保持确定性(LLM 细化仍在 L3 holistic,不下沉到取数)。

### B — L2 champion A/B 评估 harness(`autoresearch/research/l2_eval.py` 新增)
- `forward_compare(panel, *, l2_n=200, label_col="fwd_5_oc")` → `{stratified: {mean_fwd, hit, n}, champion: {…},
  delta}`:同一召回帧上,`select_l2`(分层)vs champion 选 top-N,比前向收益。纯函数(panel 带 fwd 列)。
- CLI `python -m autoresearch.research.l2_eval <args>`:真数据跑出对照(结论留人看)。
- **接线**:`ScanConfig.l2_engine: str = "stratified"`(`"champion"` 时 L2 走 `l2_model.champion_scores`
  重排 + 分层 floor 兜底);默认 stratified → parity 不破。verdict 待真数据,先交机制。

### Leaf 项(各独立小 commit)
- **L0**:`_recall_gate_a` 增可配 `min_list_days`(次新,默认 0=关)+ `min_amount_yi`(流动性地板,默认 0=关)。
- **L2**:`stratified_l2` 增可选 `sector_cap_by_regime`(trend 放宽 cap、risk_off 收紧;默认 None=现行固定 cap);
  L2 输出透传 `sector_mom`(行业动量 = 申万一级组 median pct_60d)列给 L3,补 sector-neutral 抹掉的行业 beta。
- **L5**:summary 顶部加一行 regime 定性 + drift 提示;`A_pipeline` 对每只 finalist 落"各段得分轨迹"
  (L1 composite/rank → L2 rank → L3 conviction)便于复盘卡点;`self_review` 增 false_positive 反推钩子(用
  retro 的历史误判检查"有无规则覆盖")。
- **L4**:`l4_card.force_full_card(brief_prior)` 高先验强制满卡白名单(P0 先验极强者不被早停误杀);
  `l4_card.audit_rubric_gates(card_text, gates)` 抽检卡片自评 gates 与正文一致性(防自评 gaming)。

## 数据流与错误处理
- regime 计算失败/数据缺 → `range`(中性),全链不阻塞。
- 所有新列/新 key 缺失时下游按"无则退现行"处理(老路不破),与现有 degrade 语义一致。
- 行为开关全默认关:`regime_aware=False`、`l2_engine="stratified"`、leaf 门 floor=0。开关开启才改输出。

## 测试(合成 fixture,无网络)
每单元一组 pytest:regime 三态 + 退化;`_load_weights` regime 选择 + 缺 regimes 退 flat(parity);
`calibrate_regimes` 分桶 IC 正确 + 仍写 flat;ledger propose/apply 幅度封顶 + 方向;`score_title` 否定/强度;
`forward_compare` delta 符号;leaf 各门退化不抛。**新增"parity 守卫"测试**:`regime_aware=False` 下
`_load_weights(regime="trend")==_load_weights(regime=None)`(无 regimes 块时)。

## 上线顺序
F → A → D → C → B → Leaf。每步 commit;全套 `uv run --no-sync python -m pytest` + ruff 通过后本地合并 main。
真数据校准(`calibrate_regimes`)/对照(`l2_eval`)作为合并后的运行步骤,不阻塞代码合并。
