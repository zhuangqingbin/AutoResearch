#!/usr/bin/env python3
"""统计裁决原语 —— 区间、等价、多重检验、功效、成熟门(确定性,零 LLM,零 scipy)。

design: docs/specs/2026-08-03-scan-next-wave-brainstorm-design.md §5-2/§5-3

治的病只有一个,但它在全稿里反复出现:**「不显著」被当成「等价」讲**。

  - §4.4:「未显著但区间宽 = `UNKNOWN/IMMATURE`,绝不是『价值≈0 实锤』」
  - §4.1:「样本不足或区间跨 margin 均为 `IMMATURE/UNKNOWN`,不是『门有效』或『门无效』」
  - §3.4:「regime 子样本不足时明确报 `IMMATURE`,不要求伪造『全段同向』」

三句话是同一条纪律。所以本模块的 `equivalence_verdict` **不返回布尔**:它返回
`DIFFERENT / EQUIVALENT / UNKNOWN` 三态,而 `UNKNOWN` 是区间跨了等价边界时的**唯一**
出口 —— 调用方想把它读成「无差异」就得自己写死这句谎话,而不是从一个 `if not significant`
里顺手滑出来。

**为什么不用 scipy**:`pyproject.toml` 的运行依赖里没有它(当前环境有,是别人的传递依赖)。
判据代码不能建在一个随时会消失的地基上,故正态/Beta 分位数在本模块内自带实现。

**为什么 bootstrap 按日聚簇**:同一扫描日的几十只票共享当天的市场冲击,行级重采样会把
「1 天 × 60 只」当成 60 个独立样本 → 区间窄到假。全稿的 paired unit 一律是**扫描日**。
"""
from __future__ import annotations

import math
from dataclasses import asdict, dataclass

import numpy as np
import pandas as pd

# ── 统一成熟门(§1.2;event/typed-event/F1/F2/O 系列共用一把尺)────────────────
MATURITY_MIN_SCAN_DAYS = 20        # 真实扫描日(不是自然日,不是回放日)
MATURITY_MIN_SUBGROUP = 10         # 关键细分(regime / lane / gate)的最小样本
MATURITY_MIN_UNIQUE = 30           # 召回 unique(只此一路召回的票)
MATURITY_MIN_REGIMES = 2           # 覆盖的 regime 数

DEFAULT_BOOT = 2000
DEFAULT_ALPHA = 0.05
DEFAULT_SEED = 20260803            # 固定种子:同输入同区间(判据不许每跑一次换个数)


@dataclass(frozen=True)
class Interval:
    """一个点估计 + 它的区间 + 区间是怎么来的(方法与样本量必须随数一起走)。"""
    point: float | None
    lo: float | None
    hi: float | None
    n: int
    n_clusters: int
    method: str
    alpha: float = DEFAULT_ALPHA

    def as_dict(self) -> dict:
        return asdict(self)

    @property
    def excludes_zero(self) -> bool:
        if self.lo is None or self.hi is None:
            return False
        return self.lo > 0 or self.hi < 0

    @property
    def width(self) -> float | None:
        if self.lo is None or self.hi is None:
            return None
        return float(self.hi - self.lo)


_EMPTY = Interval(None, None, None, 0, 0, "empty")


# ───────────────────────── 正态 / Beta 分位数(无 scipy) ─────────────────────────


def norm_cdf(x: float) -> float:
    return 0.5 * (1.0 + math.erf(float(x) / math.sqrt(2.0)))


def norm_ppf(p: float) -> float:
    """标准正态分位数(Acklam 有理逼近 + 一步 Halley 修正,|误差| < 1e-12)。"""
    p = float(p)
    if not 0.0 < p < 1.0:
        raise ValueError(f"norm_ppf 需要 0<p<1,收到 {p!r}")
    a = (-3.969683028665376e+01, 2.209460984245205e+02, -2.759285104469687e+02,
         1.383577518672690e+02, -3.066479806614716e+01, 2.506628277459239e+00)
    b = (-5.447609879822406e+01, 1.615858368580409e+02, -1.556989798598866e+02,
         6.680131188771972e+01, -1.328068155288572e+01)
    c = (-7.784894002430293e-03, -3.223964580411365e-01, -2.400758277161838e+00,
         -2.549732539343734e+00, 4.374664141464968e+00, 2.938163982698783e+00)
    d = (7.784695709041462e-03, 3.224671290700398e-01, 2.445134137142996e+00,
         3.754408661907416e+00)
    plow, phigh = 0.02425, 1 - 0.02425
    if p < plow:
        q = math.sqrt(-2 * math.log(p))
        x = (((((c[0] * q + c[1]) * q + c[2]) * q + c[3]) * q + c[4]) * q + c[5]) / \
            ((((d[0] * q + d[1]) * q + d[2]) * q + d[3]) * q + 1)
    elif p > phigh:
        q = math.sqrt(-2 * math.log(1 - p))
        x = -(((((c[0] * q + c[1]) * q + c[2]) * q + c[3]) * q + c[4]) * q + c[5]) / \
            ((((d[0] * q + d[1]) * q + d[2]) * q + d[3]) * q + 1)
    else:
        q, r = p - 0.5, (p - 0.5) ** 2
        x = (((((a[0] * r + a[1]) * r + a[2]) * r + a[3]) * r + a[4]) * r + a[5]) * q / \
            (((((b[0] * r + b[1]) * r + b[2]) * r + b[3]) * r + b[4]) * r + 1)
    e = norm_cdf(x) - p                               # Halley 修正
    u = e * math.sqrt(2 * math.pi) * math.exp(x * x / 2)
    return x - u / (1 + x * u / 2)


def _betacf(a: float, b: float, x: float) -> float:
    """正则化不完全 Beta 的连分式(Lentz)—— `betainc` 的核心。"""
    tiny = 1e-30
    qab, qap, qam = a + b, a + 1.0, a - 1.0
    c, d = 1.0, 1.0 - qab * x / qap
    if abs(d) < tiny:
        d = tiny
    d = 1.0 / d
    h = d
    for m in range(1, 301):
        m2 = 2 * m
        aa = m * (b - m) * x / ((qam + m2) * (a + m2))
        d = 1.0 + aa * d
        if abs(d) < tiny:
            d = tiny
        c = 1.0 + aa / c
        if abs(c) < tiny:
            c = tiny
        d = 1.0 / d
        h *= d * c
        aa = -(a + m) * (qab + m) * x / ((a + m2) * (qap + m2))
        d = 1.0 + aa * d
        if abs(d) < tiny:
            d = tiny
        c = 1.0 + aa / c
        if abs(c) < tiny:
            c = tiny
        d = 1.0 / d
        delta = d * c
        h *= delta
        if abs(delta - 1.0) < 3e-16:
            break
    return h


def betainc(a: float, b: float, x: float) -> float:
    """正则化不完全 Beta 函数 I_x(a,b) —— Beta 分布的 CDF。"""
    if x <= 0.0:
        return 0.0
    if x >= 1.0:
        return 1.0
    lbeta = (math.lgamma(a + b) - math.lgamma(a) - math.lgamma(b)
             + a * math.log(x) + b * math.log1p(-x))
    front = math.exp(lbeta)
    if x < (a + 1.0) / (a + b + 2.0):
        return front * _betacf(a, b, x) / a
    return 1.0 - math.exp(
        math.lgamma(a + b) - math.lgamma(a) - math.lgamma(b)
        + b * math.log1p(-x) + a * math.log(x)) * _betacf(b, a, 1.0 - x) / b


def beta_ppf(p: float, a: float, b: float) -> float:
    """Beta 分位数(对 `betainc` 二分;单调,60 次迭代 → 精度 ~1e-18)。"""
    if p <= 0.0:
        return 0.0
    if p >= 1.0:
        return 1.0
    lo, hi = 0.0, 1.0
    for _ in range(200):
        mid = 0.5 * (lo + hi)
        if betainc(a, b, mid) < p:
            lo = mid
        else:
            hi = mid
        if hi - lo < 1e-15:
            break
    return 0.5 * (lo + hi)


def beta_binomial_interval(successes: int, n: int, *, prior_a: float = 1.0,
                           prior_b: float = 1.0, mass: float = 0.95) -> Interval:
    """比例的 Beta-Binomial 等尾可信区间(§4.1「Beta-Binomial 或 date-cluster 区间」)。

    n=0 时**不返回 0.5**,返回空区间 —— 「没有观测」和「一半一半」是两件事,把前者渲染成
    后者正是 §4.1 要禁的那类伪精确。
    """
    n = int(n)
    successes = int(successes)
    if n <= 0:
        return Interval(None, None, None, 0, 0, "beta_binomial", 1 - mass)
    if not 0 <= successes <= n:
        raise ValueError(f"successes={successes} 不在 [0, n={n}]")
    tail = (1.0 - mass) / 2.0
    a, b = prior_a + successes, prior_b + (n - successes)
    return Interval(
        point=successes / n,
        lo=beta_ppf(tail, a, b),
        hi=beta_ppf(1.0 - tail, a, b),
        n=n, n_clusters=n, method=f"beta_binomial(a={prior_a},b={prior_b})",
        alpha=1 - mass,
    )


# ───────────────────────── date-cluster bootstrap ─────────────────────────


def _clusters(frame: pd.DataFrame, value_col: str,
              date_col: str) -> tuple[list[np.ndarray], int]:
    values = pd.to_numeric(frame[value_col], errors="coerce")
    keep = values.notna()
    sub = frame.loc[keep]
    vals = values.loc[keep].to_numpy(dtype=float)
    if not len(vals):
        return [], 0
    keys = (sub[date_col].astype(str).to_numpy() if date_col in sub.columns
            else np.array([str(i) for i in range(len(vals))]))
    groups = [vals[keys == k] for k in pd.unique(keys)]
    return groups, len(vals)


def date_cluster_bootstrap(frame: pd.DataFrame, value_col: str, *,
                           date_col: str = "date", n_boot: int = DEFAULT_BOOT,
                           alpha: float = DEFAULT_ALPHA,
                           seed: int = DEFAULT_SEED) -> Interval:
    """按**扫描日**聚簇重采样的均值区间。

    重采样的单位是日,不是行:同一天的票共享当天的市场冲击,行级 bootstrap 会把
    「1 天 × 60 只」当 60 个独立样本,区间窄到假(§4.4「paired/date-cluster bootstrap」)。
    单日的方差 → 区间恒为点(n_clusters=1),这是诚实的:一天的数据就是没有跨日方差。
    """
    groups, n = _clusters(frame, value_col, date_col)
    if not groups:
        return Interval(None, None, None, 0, 0, "date_cluster_bootstrap", alpha)
    point = float(np.concatenate(groups).mean())
    if len(groups) == 1:
        return Interval(point, None, None, n, 1, "date_cluster_bootstrap(n_days=1)", alpha)
    rng = np.random.default_rng(seed)
    idx = np.arange(len(groups))
    draws = np.empty(n_boot, dtype=float)
    for i in range(n_boot):
        pick = rng.choice(idx, size=len(groups), replace=True)
        draws[i] = float(np.concatenate([groups[j] for j in pick]).mean())
    lo, hi = np.quantile(draws, [alpha / 2.0, 1.0 - alpha / 2.0])
    return Interval(point, float(lo), float(hi), n, len(groups),
                    f"date_cluster_bootstrap(B={n_boot},seed={seed})", alpha)


def day_equal_bootstrap(frame: pd.DataFrame, value_col: str, *,
                        date_col: str = "date", n_boot: int = DEFAULT_BOOT,
                        alpha: float = DEFAULT_ALPHA,
                        seed: int = DEFAULT_SEED) -> Interval:
    """**日等权**均值 + 它自己的区间:先日内取均值,再跨日等权重采样。

    与 `date_cluster_bootstrap` 的区别只有一处,但它决定了 `point` 和 `lo/hi` 是不是同一个
    量:上面那个函数按日**聚簇**重采样,可是抽完之后对**行**取均值 —— 于是「一天 100 只 +
    一天 1 只」里热闹那天拿走 99% 的权重,得到的是**行等权**中心。本函数先把每天压成一行
    再交给它,于是两个数描述同一个估计量。

    `n` = 有效**事件行**数(只描述覆盖),`n_clusters` = 有效**观测日**数(统计权重的单位)。
    事件多不换来统计权重 —— 这是「日内复制不变性」:把某一天的全部事件等比例复制,
    点估计与区间都不动。

    ⚠️ 三条不可扩大解释:

    - 这仍是**独立日重采样**,不是连续交易日 moving-block 区间;日与日之间的时序相关性
      没有被处理(交易日对齐的 block 方法见 `research/overseas_event_census.moving_block_diff`)。
    - 本函数**不查交易日历**:日期身份、缺日、连续性都归上游数据契约。`date_col` 里出现
      什么就按什么分组。
    - 单日 → 有点估计、`lo/hi=None`(没有跨日方差可估),不是「区间为零」。

    坏输入一律炸,不静默降级:缺列(缺 `date_col` 时旧函数会退化成「每行一簇」给出一个窄到
    假的区间,那是最难发现的错)、`n_boot`/`alpha` 越界、值里有 ±inf(收益的分母为零,
    均值会被它整个吞掉)。NaN 与不可解析值按既有清洗规则**排除**(不是当 0)。
    """
    if frame is None or value_col not in frame or date_col not in frame:
        raise ValueError(f"day_equal_bootstrap 缺列(需要 {value_col!r} 与 {date_col!r});"
                         "缺列不能当成空样本 —— 会退化成每行一簇的假区间")
    if isinstance(n_boot, bool) or not isinstance(n_boot, int) or n_boot < 1:
        raise ValueError(f"n_boot 必须是正整数,收到 {n_boot!r}")
    if not 0 < alpha < 1:
        raise ValueError(f"alpha 必须落在 (0, 1),收到 {alpha!r}")

    values = pd.to_numeric(frame[value_col], errors="coerce")
    dates = frame[date_col].astype(str).str.strip()
    keep = values.notna() & ~dates.isin(["", "nan", "NaT", "None"])
    work = pd.DataFrame({
        "__date": dates[keep].to_numpy(dtype=object),
        "__value": values[keep].to_numpy(dtype=float),
    })
    if not np.isfinite(work["__value"].to_numpy()).all():
        raise ValueError(f"day_equal_bootstrap 的 {value_col!r} 含 ±inf —— "
                         "无穷值不是缺失值,不能当 NaN 排除,请上游查分母为零的行")
    daily = work.groupby("__date", as_index=False, sort=True)["__value"].mean()
    interval = date_cluster_bootstrap(
        daily, "__value", date_col="__date", n_boot=n_boot, alpha=alpha, seed=seed,
    )
    return Interval(
        interval.point, interval.lo, interval.hi,
        len(work), interval.n_clusters,
        f"day_equal/{interval.method}", interval.alpha,
    )


def paired_delta_interval(frame: pd.DataFrame, actual_col: str, baseline_col: str, *,
                          date_col: str = "date", n_boot: int = DEFAULT_BOOT,
                          alpha: float = DEFAULT_ALPHA,
                          seed: int = DEFAULT_SEED) -> Interval:
    """配对差(actual − baseline)的按日聚簇区间 —— §4.4 tier-1 与 §4.1 实验的主尺。

    配对**必须先做**再聚合:同日同候选集的两个读数相减,把市场共同项消掉;先各自平均
    再相减会把配对结构丢掉(两边的日集合一旦不同,差值就不是同一件事的差)。
    """
    work = frame.copy()
    work["__delta"] = (pd.to_numeric(work[actual_col], errors="coerce")
                       - pd.to_numeric(work[baseline_col], errors="coerce"))
    return date_cluster_bootstrap(work, "__delta", date_col=date_col,
                                  n_boot=n_boot, alpha=alpha, seed=seed)


# ───────────────────────── 三态裁决:等价 / 不同 / 未知 ─────────────────────────

DIFFERENT = "DIFFERENT"
EQUIVALENT = "EQUIVALENT"
UNKNOWN = "UNKNOWN"


def equivalence_verdict(interval: Interval, margin: float,
                        *, margin_lo: float | None = None) -> str:
    """等价 / 不同 / 未知 —— **三态**,不是「显著 vs 不显著」。

    `margin` = 预注册的等价边界(默认对称 ±margin;非对称场景传 `margin_lo`)。

    - `EQUIVALENT`:整个区间落在等价带内 —— 只有这一种情况才可以说「无增量」。
    - `DIFFERENT` :整个区间在等价带之外的同一侧(既排除 0,也排除等价带)。
    - `UNKNOWN`   :其余全部 —— 含「区间跨 0 但很宽」「区间压着 margin」「无区间」。

    §4.4 的原话:「只有整个置信区间落入等价区间才可说『排序无增量』;未显著但区间宽=
    `UNKNOWN/IMMATURE`,绝不是『价值≈0 实锤』」。本函数是那句话的唯一实现点。
    """
    if margin < 0:
        raise ValueError("margin 必须 ≥ 0")
    lo_bound = -abs(margin) if margin_lo is None else float(margin_lo)
    hi_bound = abs(margin)
    if interval.lo is None or interval.hi is None:
        return UNKNOWN
    if lo_bound <= interval.lo and interval.hi <= hi_bound:
        return EQUIVALENT
    if interval.lo > hi_bound or interval.hi < lo_bound:
        return DIFFERENT
    return UNKNOWN


def no_harm_verdict(interval: Interval, harm_margin: float) -> str:
    """no-harm 单边判据:区间下界 ≥ −harm_margin → `EQUIVALENT`(没变差);
    上界 < −harm_margin → `DIFFERENT`(确实变差);跨界 → `UNKNOWN`。"""
    if harm_margin < 0:
        raise ValueError("harm_margin 必须 ≥ 0")
    if interval.lo is None or interval.hi is None:
        return UNKNOWN
    if interval.lo >= -abs(harm_margin):
        return EQUIVALENT
    if interval.hi < -abs(harm_margin):
        return DIFFERENT
    return UNKNOWN


# ───────────────────────── 多重检验 / 功效 ─────────────────────────


def bh_fdr(pvalues, alpha: float = DEFAULT_ALPHA) -> list[dict]:
    """Benjamini-Hochberg。返回按**原始顺序**的 `[{i, p, q, rejected}]`。

    §1.2/§3.4 都要求「多重检验/FDR 修正」:一次网格扫 20 个参数、一次事件类型学试 12 个
    type,不修正的话必然刷出几个 p<0.05 的假发现。
    """
    ps = [float(p) for p in pvalues]
    m = len(ps)
    if not m:
        return []
    order = sorted(range(m), key=lambda i: ps[i])
    q = [0.0] * m
    running = 1.0
    for rank in range(m, 0, -1):
        i = order[rank - 1]
        running = min(running, ps[i] * m / rank)
        q[i] = running
    return [{"i": i, "p": ps[i], "q": q[i], "rejected": q[i] <= alpha} for i in range(m)]


#: 检验族的依赖假设。**没有默认值** —— 调用方必须说清楚,不说就报错。
#: `independent_or_positive` = 独立或 PRDS(正回归依赖)→ BH 控 FDR;
#: `arbitrary` = 任意依赖 → BY(BH 的 q 乘 Σ_{i=1..m} 1/i),更保守。
DEPENDENCE_ASSUMPTIONS: tuple[str, ...] = ("independent_or_positive", "arbitrary")


def family_adjustment(pvalues, *, dependence: str, alpha: float = DEFAULT_ALPHA) -> list[dict]:
    """一个检验族的 FDR 校正 → 与入参**同序**的 `[{i, p, q, rejected, method}]`。

    BH 本体不重写(`bh_fdr` 已被海外/衍生品普查用着);这里加的是**依赖假设**这一层:

    - 因子之间通常既不独立、也不保证正相关,那时 BH 的 FDR 控制**没有保证**。BY 用
      `q_BY = min(1, q_BH · Σ_{i=1..m} 1/i)` 换取任意依赖下的控制,代价是更保守。
    - `dependence` 是 keyword-only 且**无缺省**:一律称「任意相关下受控」是错的,不写又会
      被当成写了。让它必须出现在调用点,读结论的人才知道这一族按哪个假设算的。

    `rejected` 一律按**校正后**的 q 判 —— 报 BY 的 q 却用 BH 的门,等于没校正。
    p 值必须是 [0, 1] 内的有限数:NaN/inf/越界一律抛错,不静默截断。

    对照定义:https://docs.scipy.org/doc/scipy/reference/generated/scipy.stats.false_discovery_control.html
    (只对照,不引入运行时 SciPy 依赖。)
    """
    if dependence not in DEPENDENCE_ASSUMPTIONS:
        raise ValueError(f"dependence assumption required, one of {DEPENDENCE_ASSUMPTIONS}")
    if not 0 < alpha < 1:
        raise ValueError("invalid alpha")
    ps = [float(p) for p in pvalues]
    if any(not math.isfinite(p) or not 0 <= p <= 1 for p in ps):
        raise ValueError("invalid p-values")
    rows = bh_fdr(ps, alpha=alpha)
    if dependence == "independent_or_positive":
        return [dict(row, method="BH") for row in rows]
    factor = sum(1.0 / i for i in range(1, len(ps) + 1))
    out = []
    for row in rows:
        q = min(1.0, row["q"] * factor)
        out.append(dict(row, q=q, rejected=q <= alpha, method="BY"))
    return out


def proportion_power(p0: float, p1: float, n: int, alpha: float = DEFAULT_ALPHA) -> float:
    """两比例(独立、等样本)双侧检验的近似功效 —— 用于「功效是否足够」的门,不是判据本身。"""
    if n <= 0:
        return 0.0
    p0, p1 = float(p0), float(p1)
    pbar = (p0 + p1) / 2.0
    se_null = math.sqrt(2.0 * pbar * (1.0 - pbar) / n) if 0 < pbar < 1 else 0.0
    se_alt = math.sqrt((p0 * (1 - p0) + p1 * (1 - p1)) / n)
    if se_alt <= 0:
        return 1.0 if p0 != p1 else float(alpha)
    z = norm_ppf(1.0 - alpha / 2.0)
    delta = abs(p1 - p0)
    return float(norm_cdf((delta - z * se_null) / se_alt))


def min_n_for_proportion(p0: float, p1: float, *, power: float = 0.8,
                         alpha: float = DEFAULT_ALPHA, cap: int = 100_000) -> int | None:
    """达到目标功效所需的每组最小 n;`cap` 内达不到 → `None`(诚实地说「这个差做不出来」)。"""
    if p0 == p1:
        return None
    n = 2
    while n <= cap:
        if proportion_power(p0, p1, n, alpha) >= power:
            return n
        n = n + 1 if n < 50 else int(n * 1.15) + 1
    return None


# ───────────────────────── 统一成熟门 ─────────────────────────

MATURE = "MATURE"
IMMATURE = "IMMATURE"


@dataclass(frozen=True)
class Maturity:
    status: str
    missing: tuple[str, ...]
    observed: dict

    def as_dict(self) -> dict:
        return {"status": self.status, "missing": list(self.missing),
                "observed": dict(self.observed)}


def maturity_verdict(*, scan_days: int, subgroup_n: int | None = None,
                     unique_n: int | None = None, regimes: int | None = None,
                     min_scan_days: int = MATURITY_MIN_SCAN_DAYS,
                     min_subgroup: int = MATURITY_MIN_SUBGROUP,
                     min_unique: int = MATURITY_MIN_UNIQUE,
                     min_regimes: int = MATURITY_MIN_REGIMES) -> Maturity:
    """统一成熟门(§1.2)—— 取代各处自造的局部口径(如 event 通道的「≥10 日累计>0」)。

    `None` = 该维度对本案不适用(如纯门归因没有「召回 unique」),跳过而不是当 0 判死。
    """
    observed = {"scan_days": scan_days, "subgroup_n": subgroup_n,
                "unique_n": unique_n, "regimes": regimes}
    missing: list[str] = []
    for label, value, floor in (("scan_days", scan_days, min_scan_days),
                                ("subgroup_n", subgroup_n, min_subgroup),
                                ("unique_n", unique_n, min_unique),
                                ("regimes", regimes, min_regimes)):
        if value is not None and int(value) < floor:
            missing.append(f"{label}={value}<{floor}")
    return Maturity(IMMATURE if missing else MATURE, tuple(missing), observed)


def expanding_p25(series, *, min_history: int = 10) -> list[float | None]:
    """逐点「当日**之前**」的 expanding P25 报警线(§3.1)。

    为什么不是全期分位:全期分位含未来 → 今天的报警线由明天的数据决定,回看时永远「没报警」。
    历史不足 `min_history` → `None`(不报警,而不是拿 2 个点定分位)。
    """
    values = [float(v) for v in series]
    out: list[float | None] = []
    for i in range(len(values)):
        prior = values[:i]
        out.append(float(np.quantile(prior, 0.25)) if len(prior) >= min_history else None)
    return out


# ───────────────────── 块 bootstrap:时间相关下的区间(F5 下沉) ─────────────────────
#
# 2026-09-06(工作包 F5)从 `research/overseas_event_census` **机械搬入**,抽块约定与统计
# 口径逐字未改;旧模块同对象转发。搬的理由与 A 包同一条:同仓两套 block bootstrap = 同一个
# 估计量两处实现,读数迟早悄悄分叉。
#
# 下面三个常量是搬迁前那份 §0 预注册值。census 侧仍保留自己的 `BLOCK`/`N_BOOT`/`SEED`
# 名字(那是它的预注册记录),两边同值由
# `tests/common/test_block_bootstrap.py::test_preregistered_constants_do_not_drift_from_the_shared_defaults`
# 钉死 —— 靠测试,不靠谁 import 谁。

MOVING_BLOCK = 5                   # 5 个交易日 moving block
MOVING_BLOCK_BOOT = 10_000
MOVING_BLOCK_SEED = 20260829


def block_index(rng: np.random.Generator, n: int, block: int) -> np.ndarray:
    """重叠块抽样的下标 —— **本仓唯一**一份抽块实现。

    起点均匀取自 `0..n-block`(**含端点**),抽 `ceil(n/block)` 个块拼接后截到 `n`。
    块**不绕回**序列开头:绕回等于把首尾接起来,那是循环块 bootstrap,与这里的口径不是
    一回事,换过去所有历史读数都要重算。
    """
    n_blocks = int(math.ceil(n / block))
    starts = rng.integers(0, n - block + 1, size=n_blocks)      # 含端点
    return (starts[:, None] + np.arange(block)[None, :]).ravel()[:n]


def circular_block_index(rng: np.random.Generator, n: int, block: int) -> np.ndarray:
    """Circular moving-block indices for null tests where every day may start a block."""
    n_blocks = int(math.ceil(n / block))
    starts = rng.integers(0, n, size=n_blocks)
    return ((starts[:, None] + np.arange(block)[None, :]) % n).ravel()[:n]


@dataclass(frozen=True)
class BootResult:
    """一次 moving-block bootstrap 的全部产出。`point=None` = 这批样本算不出差值。"""

    point: float | None
    lo: float | None
    hi: float | None
    p: float | None
    n_boot: int
    n_valid: int
    block: int
    seed: int

    def as_dict(self) -> dict:
        return asdict(self)


def _diff(values: np.ndarray, flags: np.ndarray) -> float | None:
    """事件日均值 − 非事件日均值。任一侧为空 → None(**不是 0**)。"""
    a, b = values[flags], values[~flags]
    if not len(a) or not len(b):
        return None
    return float(a.mean() - b.mean())


def moving_block_diff(values, flags, *, block: int = MOVING_BLOCK,
                      n_boot: int = MOVING_BLOCK_BOOT, alpha: float = DEFAULT_ALPHA,
                      seed: int = MOVING_BLOCK_SEED) -> BootResult:
    """moving-block bootstrap 的「事件日 − 非事件日」均值差。

    `values` / `flags` 必须**按日期升序**且长度相同:块的意义就是时序相邻,乱序等于没做块。

    做法:`block_index` 抽重叠块,`(value, flag)` **成对**搬运 —— 拆开搬就把「哪天是事件日」
    这个结构洗掉了,那是置换检验不是 bootstrap。每抽一次重算差值,得到 θ̂ 的抽样分布:

      * 95% CI = 分位 [α/2, 1−α/2];
      * 双侧 p = 2·min(P(θ*≤0), P(θ*≥0)),下限截到 `1/n_valid`(10,000 次抽样分辨不出
        比 1e-4 更小的 p,报 0 是伪精确)。

    某次抽样若一侧为空(全是事件日或全不是)→ 该次**作废并计数**,不折成 0。有效抽样
    < n_boot/2 → 返回 `p=None`(区间不可信),由调用方判成「未证」而不是「显著」。
    """
    v = np.asarray(values, dtype=float)
    f = np.asarray(flags, dtype=bool)
    if v.shape != f.shape:
        raise ValueError(f"values/flags 长度不一致:{v.shape} vs {f.shape}")
    n = len(v)
    point = _diff(v, f) if n else None
    block = max(1, int(block))
    if n <= block or point is None:
        return BootResult(point, None, None, None, n_boot, 0, block, seed)
    rng = np.random.default_rng(seed)
    draws: list[float] = []
    for _ in range(n_boot):
        idx = block_index(rng, n, block)
        d = _diff(v[idx], f[idx])
        if d is not None:
            draws.append(d)
    n_valid = len(draws)
    if n_valid < n_boot // 2:
        return BootResult(point, None, None, None, n_boot, n_valid, block, seed)
    arr = np.asarray(draws, dtype=float)
    lo, hi = np.quantile(arr, [alpha / 2.0, 1.0 - alpha / 2.0])
    tail = min(float((arr <= 0).mean()), float((arr >= 0).mean()))
    p = max(2.0 * tail, 1.0 / n_valid)
    return BootResult(point, float(lo), float(hi), float(min(1.0, p)), n_boot, n_valid, block, seed)


def block_mean_ci(values, *, block: int, seed: int, n_boot: int = DEFAULT_BOOT,
                  alpha: float = DEFAULT_ALPHA) -> dict:
    """单序列均值的块 bootstrap 区间 —— 与 `moving_block_diff` 共用 `block_index`。

    输入是**按完整研究交易日历排序的日等权均值**(不是股票行):块要有意义,相邻两项就必须
    是相邻交易日。日历有缺口时,调用方要么分连续段跑,要么在读数里声明「只按观察序列重采样」
    这条局限 —— 本函数不替它补造缺失日的收益。

    样本短到装不下两个块 → `INSUFFICIENT_BLOCKS` + 区间 `None`(点估计照给)。`nan`/`inf`
    一律拒绝:块 bootstrap 对缺失值没有意义,悄悄跳过等于换了个人口。
    """
    x = np.asarray(values, dtype=float)
    if x.ndim != 1:
        raise ValueError("ordered one-dimensional daily observations required")
    if not np.isfinite(x).all():
        raise ValueError("ordered finite daily observations required")
    if type(block) is not int or block < 1:
        raise ValueError("invalid block size")
    if type(n_boot) is not int or n_boot < 1 or not 0 < alpha < 1:
        raise ValueError("invalid bootstrap parameters")
    if len(x) <= block or len(x) < 2:
        return {"point": float(x.mean()) if len(x) else None,
                "lo": None, "hi": None, "status": "INSUFFICIENT_BLOCKS"}
    rng = np.random.default_rng(seed)
    draws = np.asarray([x[block_index(rng, len(x), block)].mean() for _ in range(n_boot)])
    lo, hi = np.quantile(draws, [alpha / 2.0, 1.0 - alpha / 2.0])
    return {"point": float(x.mean()), "lo": float(lo), "hi": float(hi), "status": "COMPUTED"}


@dataclass(frozen=True)
class BlockMeanTest:
    """Two-sided moving-block test of a time-ordered series mean against zero."""

    point: float | None
    pvalue: float | None
    status: str
    n: int
    n_boot: int
    n_valid: int
    block: int
    seed: int

    def as_dict(self) -> dict:
        return asdict(self)


def block_mean_test(values, *, block: int, seed: int,
                    n_boot: int = DEFAULT_BOOT) -> BlockMeanTest:
    """Test ``mean(values) == 0`` with a centered moving-block null.

    The observations must already be ordered daily equal-weight values.  The
    finite-sample ``+1`` correction prevents a bootstrap p-value of zero.
    """
    x = np.asarray(values, dtype=float)
    if x.ndim != 1:
        raise ValueError("ordered one-dimensional daily observations required")
    if not np.isfinite(x).all():
        raise ValueError("ordered finite daily observations required")
    if type(block) is not int or block < 1:
        raise ValueError("invalid block size")
    if type(n_boot) is not int or n_boot < 1:
        raise ValueError("invalid bootstrap parameters")
    point = float(x.mean()) if len(x) else None
    if len(x) < 2:
        return BlockMeanTest(
            point, None, "INSUFFICIENT_OBSERVATIONS", len(x), n_boot, 0, block, seed
        )
    if len(x) <= block:
        return BlockMeanTest(
            point, None, "INSUFFICIENT_BLOCKS", len(x), n_boot, 0, block, seed
        )

    centered = x - point
    rng = np.random.default_rng(seed)
    draws = np.asarray([
        centered[circular_block_index(rng, len(centered), block)].mean()
        for _ in range(n_boot)
    ])
    extreme = int((np.abs(draws) >= abs(point)).sum())
    pvalue = float((extreme + 1) / (n_boot + 1))
    return BlockMeanTest(
        point, pvalue, "COMPUTED", len(x), n_boot, n_boot, block, seed
    )
