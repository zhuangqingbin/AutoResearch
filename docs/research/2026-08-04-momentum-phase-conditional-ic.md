# 2026-08-04 · 动量/热度的**相位条件性**:池化读数是个混合物

> 起因:用户观察「推荐的股票都是走势上涨了很久的,会不会有追高风险」。
> 结论:**追高风险是真的,但它是相位条件的** —— 池化的 −1.07% 掩盖了一个符号翻转。
> 我立案时的假设(「动量只在退潮为负」)被数据**推翻**,方向正好相反。

---

## 0. 一句话

动量/热度在**上涨相位**(发酵/高潮)有害且区间整体为负;在**回撤相位**(退潮/冰点)
点估计 ≈0、区间跨 0(UNKNOWN)。**因此「统一砍 quota」是错的处方** ——
它对唯一有害的那半天不够狠,对无害的那半天又白砍。

---

## 1. 立案假设与它的下场

立案时我写的是:

> 如果 momentum 只在退潮相位为负、在发酵/高潮为正,那结论就完全不同(该做的是相位切菜单)。

**数据说反了**。发酵/高潮才是它亏钱的地方,退潮反而不亏。
记下来是因为这正是「先问哪把尺子真的预测」的判例:池化数字看起来像「动量整体没用」,
切开之后它变成「动量在特定相位有害」——两者的处方完全不同。

---

## 2. 读数(date-cluster bootstrap,95%)

数据源:`context/scan/*/L1_channels.csv` × `retro/attribution.csv`,26 个成熟扫描日
(2026-06-17 ~ 2026-08-03);主尺 `unique_excess_t2`(**只被该路召回**的票的 T+2 超额);
相位来自 `context/learning/temperature.csv`。

| 路 | 回撤侧(退潮/冰点)n=14 | 上涨侧(发酵/高潮/修复)n=12 |
|---|---|---|
| **momentum** | **+0.0000** [−0.0162, +0.0174] 跨 0 | **−0.0232** [−0.0393, **−0.0074**] **整区间<0** |
| **heat** | −0.0092 [−0.0362, +0.0190] 跨 0 | **−0.0408** [−0.0597, **−0.0224**] **整区间<0** |
| healthy | −0.0198 [−0.0330, −0.0065] | −0.0215 [−0.0342, −0.0086] |
| **value** | **+0.0181** [**+0.0107**, +0.0263] **整区间>0** | +0.0075 [+0.0000, +0.0150] 贴 0 |
| **reversal** | **+0.0063** [**+0.0028**, +0.0097] **整区间>0** | +0.0037 [−0.0022, +0.0088] 跨 0 |

**成熟度**:momentum / heat / value / reversal 两侧均 n≥10 → `MATURE`(按 §1.2 统一成熟门的
关键细分 ≥10 一档)。**healthy 两侧 n=9 / n=7 → `IMMATURE`,本稿不对它下任何结论。**

---

## 3. 敏感性:分桶边界不是挑出来的

「修复」相位归属存疑(它是从底部的恢复),故做三种口径:

| 口径 | momentum 上涨侧 | heat 上涨侧 |
|---|---|---|
| A 修复归上涨侧 | −0.0232 [−0.0393, −0.0074] | −0.0408 [−0.0597, −0.0224] |
| B 修复归回撤侧 | −0.0226 [−0.0397, −0.0053] | −0.0408 [−0.0608, −0.0203] |
| C 剔除修复 | −0.0226 [−0.0397, −0.0053] | −0.0408 [−0.0608, −0.0203] |

三种口径下「上涨侧整区间为负、回撤侧跨 0」**均成立**。结论不依赖边界选择。

---

## 4. 与既有裁定的关系

- **不推翻** 2026-07-17 用户裁定(「不要跌势票」= 产品偏好非预测主张)。本稿量的是
  **动量召回路的边际价值**,不是「该不该买跌势票」。两件事。
- **与 §0.2「追当日大涨」负结果同向但不同尺**:那条是 −3.67pp / t=−11.91(当日 ≥9.5%
  的 350 只),量的是**单日暴涨**;本稿量的是**召回路 × 市场相位**。前者更狠,且已是铁律。
- **与 O2(52 周高距离族,REJECTED)一致**:「离高点近」作为**线性因子** rank-IC ≈ 0。
  本稿不改变该否决 —— 有害的是「在上涨相位里追动量」,不是「离高点近」。

---

## 5. 三个 regime 形状的缺口(同一天查出来的)

动量的价值是相位条件的,而系统里**三处相位机制都没在跑**:

| # | 位置 | 状态 |
|---|---|---|
| 1 | L1 权重 `context/factor_lab/weights.json` | `regimes: []`,`meta.source = factor_lab.calibrate` —— 写 regimes 块的是 **`calibrate-regimes`** 模式。不是代码回归,是最后一次校准跑错了模式(或被 flat 覆盖)。`regime_aware=True` 因此一直回落 flat。 |
| 2 | L2 sector cap `l2_stratify.regime_caps` | 已建未接线(O3,本波已补文档+AST 探针,**未接线**) |
| 3 | L1 召回 quota | 完全 regime-blind(`channel_quotas` 是静态 dict) |

**这三条独立地把「相位」这件事关掉了**,而数据说相位正是价值所在。

---

## 6. 为什么 `channel_ledger` 的 advisory 不该照做

`reports/learning/channel_ledger.md` 目前提议:momentum 250→188、healthy 150→112、
heat 150→112。这三条是**基于池化数字**算的。按本稿:

- momentum 统一降 quota → 在回撤侧(14/26 天,占一半以上)砍掉一条**不亏钱**的路;
- healthy 降 quota → 它两侧都 `IMMATURE`,**没有裁决依据**;
- heat 降 quota → 这条**方向正确**(两侧都不为正,上涨侧 −4.08% 且整区间为负)。

**结论:三条 advisory 里只有 heat 那条站得住。**

---

## 7. 下一步(全部需人工批准,本稿不改任何生产配置)

1. **重跑 `factor_lab calibrate-regimes`** —— 修 §5-1。这会重写 L1 权重 → 改菜单 → **B 类**;
2. **相位条件 quota** 立项为 challenger(`recall_quota_regime` family):
   上涨相位压 momentum/heat,回撤相位不动。预注册 margin + no-harm,走 registry;
3. **heat quota 250→150→?** 可作为独立的、证据最强的一条单独提;
4. **healthy 继续攒样本** —— 两侧 n<10,现在动它没有依据。

---

## 8. 复现

```bash
uv run --no-sync python - <<'PY'
from pathlib import Path
import pandas as pd
from autoresearch.research import channel_audit as ca, replay
from autoresearch.common import stats as st
root, ph = Path("context/scan"), replay.phase_map()
rows = []
for d in ca._scan_dates(root, 9999):
    loaded = ca._load_day(root, d)
    if loaded is None: continue
    for r in ca.day_channel_stats(*loaded).to_dict("records"):
        rows.append({"date": d, "phase": ph.get(d, "未知"),
                     "channel": r.get("channel"), "ux": r.get("unique_excess_t2")})
daily = pd.DataFrame(rows).dropna(subset=["ux"])
for ch in ["momentum", "heat"]:
    for label, keep in (("回撤", {"退潮","冰点"}), ("上涨", {"发酵","高潮","修复"})):
        sub = daily[(daily.channel == ch) & (daily.phase.isin(keep))]
        iv = st.date_cluster_bootstrap(sub, "ux", date_col="date", n_boot=3000)
        print(ch, label, iv.n_clusters, round(iv.point, 4), (round(iv.lo,4), round(iv.hi,4)))
PY
```

---

_仅供研究,非投资建议。本稿不授权任何生产变更。_
