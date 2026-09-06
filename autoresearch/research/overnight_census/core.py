#!/usr/bin/env python3
"""隔夜集中信号普查 · 纯函数层 —— 成交层 / 席位 / 除权校正 / 逐日聚合 / 四态判读。

设计稿:`docs/specs/2026-08-28-overnight-concentrated-signal-census-design.md`
(§2 判读、§3.4 成交层、§3.5 席位)。接口签名由主会话的并发实施契约钉死。

本模块**零网络、零湖依赖**:除 `load_youzi_seats` 读自己 `assets/` 下的名单快照外不碰磁盘,
所有事实由调用方以 DataFrame / set 传入。这样它才能被单测整段覆盖,也才不会在 `panel.py`
/ `families.py` 换实现时跟着一起坏。

三条贯穿全模块的纪律:

1. **单位是 pp(百分点)**,不是 decimal return。`gap=0.0012` → `0.12`。成本常量 `COST_PP`
   同一单位;在 decimal return 上直接减 `0.15` 是错的,差 100 倍。
2. **「不知道」不折叠成「是」**。`exec_tier` 的三个宽松条件在输入不可解析时一律记 False
   —— 记 True 会把不可成交的一字板洗成「可能成交」,读数直接变假;`adjust_ex_div` 缺
   `close_t1` 返回 None 而不是 0;`cell_stats` 单日返回 None 区间而不是一个假装的点。
3. **样本门先判**。`judge` 先看 n 再看区间:样本不足时哪怕 CI 好看也只报「样本不足」。

跨表单位备忘(本模块不做换算,只在此备案,换算责任在 `families.py`):
`lake/daily.amount` = **千元**;`top_inst.net_buy` 与 `limit_list_d.{amount,fd_amount}` = **元**。
`exec_tier` 里的 `fd_amount / amount` 两个数**同出 `limit_list_d`**,同表同单位,故不换算。
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd

from autoresearch.common import stats as _stats

# ───────────────────────── §0 预注册常量(跑前锁死,不许看完读数再调)─────────────────────────

COST_PP = 0.15                 # 往返成本(pp):印花税 0.05 + 佣金 0.025×2 + 滑点余量 0.05
CI_LOWER_PP = 0.15             # 门槛:绝对毛收益日聚簇 bootstrap 95% CI 下界 ≥ 此值
JUDGE_YEARS = ("2022", "2023", "2024", "2025")   # 2026 只观察
MIN_YEARS_SAME_SIGN = 3        # 逐年 ≥3/4 与全期均值同号
MIN_EVENTS = 300               # 事件族样本门
MIN_DAYS_EVENT = 60
MIN_DAYS_WIDE = 200            # 宽族(F4)样本门
CAP_FLOOR_YI = 30.0            # 市值地板(亿)
SEAT_MATCH_MIN = 0.30          # 席位名单匹配率下限,低于则回退关键字规则并留痕

OBSERVE_YEAR = "2026"          # 有读数、进表,但不进 `yearly_sign_ok` 判据

DEFAULT_SEED = 20260828        # 固定种子:同输入同区间

#: 统计实现版本 —— 换估计量 / 重采样方法 / 人口定义都要换它,新旧读数**不许直接拼成趋势**。
#: `day_equal.v2` = 均值与区间同为日等权(v1 的区间围绕行等权中心,由 CLI 事后覆盖补偿)。
STATISTICS_VERSION = "overnight.day_equal.v2"

# 判读四态(`judge` 的全部出口)。
POS = "正证据"
NEG = "显著负"
UNPROVEN = "未证"
THIN = "样本不足"

# 样本门口径。event = 事件族(F1/F2/F3);wide = 宽族(F4),它天天有票,只数日。
SAMPLE_EVENT = "event"
SAMPLE_WIDE = "wide"
SAMPLE_KINDS = (SAMPLE_EVENT, SAMPLE_WIDE)

# ── 成交层(§3.4)。`limit_list_d.limit` 的三个取值 + 三个层标签 ──
LIMIT_UP = "U"                 # 收盘封在涨停
LIMIT_DOWN = "D"
LIMIT_BROKEN = "Z"             # 炸板:盘中触板、收盘未封
TIER_MUST = "T0"               # 必成交(炸板,收盘价可成交)
TIER_MAYBE = "T1"              # 可能成交
TIER_NOT = "T2"                # 不可成交(早封 + 厚封单 + 一字)
LAST_TIME_LATE = 143000        # 末次封板时间 ≥ 14:30:00 → 尾盘才封,14:57 挂单还排得上
MIN_OPEN_TIMES = 1             # 当日开板过 ≥1 次
MAX_SEAL_RATIO = 0.10          # 封单额 / 成交额 ≤ 10% → 封单薄

# ── 席位四分类(§3.5)──
SEAT_INST = "inst"
SEAT_NORTH = "north"
SEAT_YOUZI = "youzi"
SEAT_OTHER = "other"
INST_SEAT_NAME = "机构专用"
NORTH_SEAT_NAMES = ("沪股通专用", "深股通专用")

ASSET_PATH = Path(__file__).resolve().parent / "assets" / "youzi_seats.json"


# ───────────────────────── 内部解析原语(缺失一律 None,不猜 0)─────────────────────────


def _as_float(value) -> float | None:
    """尽力转 float;None / NaN / inf / 空串 / 不可解析 → **None**,不是 0.0。

    0 是一个真实的数(封单额真的可以是 0),把「读不出来」也写成 0 会让 `fd_amount/amount`
    恰好变成 0 ≤ 0.10,把一字板判成「可能成交」—— 这正是本模块纪律 2 要挡的那种静默折叠。
    `bool` 直接判不可解析:True/False 出现在金额列多半是上游拼错了列,不替它猜。
    """
    if value is None or isinstance(value, bool):
        return None
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    return out if np.isfinite(out) else None


def _parse_hhmmss(value) -> int | None:
    """`last_time` → HHMMSS 整数。'93000' / 93000 / 93000.0 一律 zfill 到 6 位 ⇒ 093000。

    tushare 的 `last_time` 是字符串,但经 parquet / json 往返后常退化成 int 或 float;
    5 位的 `93000` 若不 zfill 就会被读成 09:30:00 之外的东西(直接跟 143000 比也会得出
    「93000 < 143000」这个碰巧正确、但换成 `153000` 就错的结论),故统一补零再比。
    不可解析(None / NaN / 含非数字 / 空 / 超过 6 位)→ None,由调用方当条件不成立处理。
    """
    if value is None:
        return None
    if isinstance(value, str):
        text = value.strip()
    else:
        num = _as_float(value)
        if num is None or num != int(num):
            return None
        text = str(int(num))
    if not text.isdigit() or not 1 <= len(text) <= 6:
        return None
    return int(text.zfill(6))


def _same_sign(a: float | None, b: float | None) -> bool:
    """严格同号:两个都非 None 且**都** > 0 或**都** < 0。恰好 0 → False(0 没有符号)。"""
    if a is None or b is None:
        return False
    return (a > 0 and b > 0) or (a < 0 and b < 0)


# ───────────────────────── §3.4 成交层 ─────────────────────────


def exec_tier(limit: str | None, last_time, open_times, fd_amount, amount) -> str | None:
    """涨停票 T+1 尾盘 14:57 挂涨停价的成交层。

    - `"T0"` 必成交 = `limit == "Z"`(炸板,收盘未封 —— 收盘价买得到)。
    - `"T1"` 可能成交 = `limit == "U"` ∧(`last_time >= 143000` ∨ `open_times >= 1`
      ∨ `fd_amount / amount <= 0.10`)。三者任一成立即可,是一把**宽松上界**。
    - `"T2"` 不可成交 = 其余 `U`(早封 + 厚封单 + 一字)。
    - `limit == "D"`、其他取值、None / NaN → `None`(不是涨停票,本层不适用)。

    输入宽容度与缺失口径(纪律 2):`last_time` 可能是 int / float / str / None,统一 zfill
    到 6 位再比;**任一条件的输入不可解析,该条件记 False**,不记 True。`fd_amount` /
    `amount` 任一缺失或 `amount <= 0` → 封单薄这条记 False。两个金额同出 `limit_list_d`,
    同单位(元),故直接相除不换算。

    只是 EOD 事后上界:`open_times` 可能发生在上午、最终 `fd_amount` 含收盘后信息,所以
    调用方必须把 F3 的结论标成 `X_ORACLE_ONLY`(设计稿 §3.4),不得读成 14:57 真能买到。
    """
    if limit is None:
        return None
    tag = str(limit).strip().upper()
    if tag == LIMIT_BROKEN:
        return TIER_MUST
    if tag != LIMIT_UP:
        return None                      # "D" / "" / "NAN" / 未知代码:一律不猜

    parsed_time = _parse_hhmmss(last_time)
    late_seal = parsed_time is not None and parsed_time >= LAST_TIME_LATE

    opens = _as_float(open_times)
    reopened = opens is not None and opens >= MIN_OPEN_TIMES

    fd, amt = _as_float(fd_amount), _as_float(amount)
    thin_seal = fd is not None and amt is not None and amt > 0 and (fd / amt) <= MAX_SEAL_RATIO

    return TIER_MAYBE if (late_seal or reopened or thin_seal) else TIER_NOT


# ───────────────────────── §3.5 席位 ─────────────────────────


def classify_seat(exalter: str | None, youzi: set[str]) -> str:
    """龙虎榜席位四分类 → `"inst"` | `"north"` | `"youzi"` | `"other"`。

    判定顺序 **inst → north → youzi → other**(第一个命中即返回)。

    `机构专用` / `沪股通专用` / `深股通专用` 用**整串相等**(先 strip)判,不是子串包含 ——
    实测 `lake/top_inst` 464 日里存在「中信建投证券股份有限公司北京商务中心区机构专用证券
    营业部」这样的**真营业部**名(11 行),用子串会把它误算成匿名机构席。

    游资则必须用**子串包含**:同一个营业部在湖里有「…证券营业部」/「…营业部」/带行政区
    等多种写法,名单只存得下一种。名单项两端空白已去(`load_youzi_seats` 负责);空串不参与
    匹配 —— 空串是任何字符串的子串,会把全表判成游资。

    `None` / NaN / 空串 → `"other"`(不是「未知」这一档:契约只有四类,调用方要区分未知
    请自己在上游留旗)。
    """
    if exalter is None:
        return SEAT_OTHER
    name = str(exalter).strip()
    if not name or name.lower() == "nan":
        return SEAT_OTHER
    if name == INST_SEAT_NAME:
        return SEAT_INST
    if name in NORTH_SEAT_NAMES:
        return SEAT_NORTH
    for keyword in youzi or ():
        key = str(keyword).strip()
        if key and key in name:
            return SEAT_YOUZI
    return SEAT_OTHER


def seat_coverage(exalters, youzi) -> dict:
    """名单对湖内营业部名称的覆盖体检 → `{n, n_youzi, n_inst, n_north, match_rate, fallback}`。

    - `n` = 送进来的**全部行数**(含 None / 空 —— 它们也是湖里真实存在的行)。
    - `match_rate` = 命中游资名单的**非机构非北向**行数 / 非机构非北向行数;分母 0 → `0.0`。
      分母刻意剔掉 inst/north:那两类本来就不该被名单匹配,留在分母里等于用「机构席多不多」
      稀释名单质量。
    - `fallback` = `match_rate < SEAT_MATCH_MIN`。**只是一个旗**,本函数不替调用方回退:
      设计稿 §3.5 要求匹配率不足时把该格降 exploratory 并印出规则版与匹配率,而不是静默换
      一套关键字规则接着判主格。

    实测(2026-08-28,`lake/top_inst` 464 日 · 375,791 行):非机构非北向 293,253 行里命中
    62,030 → `match_rate ≈ 0.21`,已低于 0.30 → 现名单**恒触发 fallback**;去掉拉萨天团一项
    只剩 0.05。这个数字本身就是读数,别把它当 bug 修掉。
    """
    n = n_youzi = n_inst = n_north = 0
    for raw in exalters:
        n += 1
        kind = classify_seat(raw, youzi)
        if kind == SEAT_INST:
            n_inst += 1
        elif kind == SEAT_NORTH:
            n_north += 1
        elif kind == SEAT_YOUZI:
            n_youzi += 1
    denom = n - n_inst - n_north
    match_rate = (n_youzi / denom) if denom > 0 else 0.0
    return {"n": n, "n_youzi": n_youzi, "n_inst": n_inst, "n_north": n_north,
            "match_rate": float(match_rate), "fallback": bool(match_rate < SEAT_MATCH_MIN)}


def load_youzi_seats(path: str | Path | None = None) -> set[str]:
    """读席位名单快照 → 供 `classify_seat` 做子串匹配的关键字集合(本模块唯一一次文件读)。

    只取 `names`。`_unmapped_aliases`(如「北京炒家」——源表只写「首板专精,无固定席位」)
    是**留痕**,不参与匹配:它们没有营业部名,放进来只会让匹配率看着好看。

    文件缺失 / 结构不对**直接抛**,不返回空集合:空名单会让 `classify_seat` 把每一个游资席
    悄悄判成 other,而 `match_rate` 变 0、读数照常出表 —— 又一次「降级不留痕」。
    """
    target = Path(path) if path is not None else ASSET_PATH
    payload = json.loads(target.read_text(encoding="utf-8"))
    names = payload.get("names")
    if not isinstance(names, list) or not names:
        raise ValueError(f"{target} 缺少非空 names 列表;席位名单读不出来就不该继续判 F2")
    return {str(item).strip() for item in names if str(item).strip()}


# ───────────────────────── §3.3 F1c 除权除息机械校正 ─────────────────────────


def adjust_ex_div(open_t2, close_t1, stk_div, cash_div_tax) -> float | None:
    """除权除息日 gap 的机械校正(**pp**):

        (open_t2 × (1 + stk_div) + cash_div_tax) / close_t1 − 1,再 ×100。

    `stk_div` = 每股送转比例(10 送 5 → 0.5),`cash_div_tax` = 每股税前现金红利(元)。
    含义:把 D+2 开盘的**除权后**价格还原回 D+1 收盘的**除权前**口径,剩下的才是真 gap。
    纯现金 / 纯送转 / 混合三种方案都由这一个恒等式覆盖(见测试)。

    缺失口径:`close_t1` 缺失或 `<= 0` → `None`(除以 0 或负价没有意义);`open_t2` 缺失也
    → `None` —— 把它当 0 会算出一个 −100pp 的假暴跌。`stk_div` / `cash_div_tax` 缺失按 **0**
    处理,这是唯一允许的「缺失当 0」:没有送转就是没有送转,该项本来就该是 0。

    这只是**报价口径**的机械校正,不含税/到账时点(设计稿 §8-5);它诊断不了真实现金流。
    """
    o2 = _as_float(open_t2)
    c1 = _as_float(close_t1)
    if o2 is None or c1 is None or c1 <= 0:
        return None
    stk = _as_float(stk_div)
    cash = _as_float(cash_div_tax)
    stk = 0.0 if stk is None else stk
    cash = 0.0 if cash is None else cash
    return float(((o2 * (1.0 + stk) + cash) / c1 - 1.0) * 100.0)


# ───────────────────────── §2.3 一格的全部读数 ─────────────────────────


def _empty_stats(sample_kind: str) -> dict:
    """空格的诚实读数:计数为 0,其余全 None。**不抛**,也不返回 0.0 冒充一个估计。"""
    return {
        "n_events": 0, "n_days": 0, "mean_pp": None, "median_pp": None, "hit": None,
        "ci_low_pp": None, "ci_high_pp": None, "net_pp": None,
        "yearly": dict.fromkeys((*JUDGE_YEARS, OBSERVE_YEAR)),
        "yearly_sign_ok": False, "half1_pp": None, "half2_pp": None,
        "halves_sign_ok": False, "freq_per_week": None, "sample_kind": sample_kind,
    }


def cell_stats(frame, *, value_col: str, date_col: str = "date",
               sample_kind: str = "event", seed: int = DEFAULT_SEED) -> dict:
    """一格的全部读数。`frame` 每行 = 一个 (日, 票) 观测,`value_col` 已是 **pp**。

    → `{"n_events","n_days","mean_pp","median_pp","hit","ci_low_pp","ci_high_pp","net_pp",
        "yearly":{"2022":pp|None,…,"2026":pp|None},"yearly_sign_ok","half1_pp","half2_pp",
        "halves_sign_ok","freq_per_week","sample_kind"}`

    口径:

    - **按日等权**:先在每个扫描日内对该格全部事件取均值,再跨日等权。`median` / `hit` 同样
      在日均值序列上算(`hit` = 逐日均值 > 0 的**日**占比)。不这么做的话,「一天 100 只 +
      一天 1 只」会让热闹那天拿走 99% 的权重 —— `n_events` 只描述覆盖,不该换来统计权重。
    - **区间**走 `common.stats.day_equal_bootstrap`,与 `mean_pp` 一样先日内均值、再跨日
      等权 —— 两个数描述**同一个估计量**。`n_events` 是有效事件行数,`n_days` 是有效观测
      日数;事件数量不换来日权重。单日 → 无跨日方差 → `lo/hi` 皆 None,这是诚实的。
      该区间仍是**独立日重采样**,不是连续交易日 moving-block 区间(设计稿 §2.3 的
      5 日 block 主区间属后续独立的方法变更,不在本层)。`seed` 由本函数显式传入。
      ⚠️ 值里有 ±inf 会**抛**(分母为零的收益;均值会被它整个吞掉)。NaN 照旧排除。
    - `net_pp = mean_pp − COST_PP`(同为 pp)。
    - `yearly`:按 `date` 前 4 位分年,**在日均值序列上**再取均值。`JUDGE_YEARS` 之外的年
      (2026、以及数据里出现的任何其他年)照样出数进表,但不进判据。
    - `yearly_sign_ok`:`JUDGE_YEARS` 里**有读数**的年中,与 `mean_pp` 同号的年数
      `>= MIN_YEARS_SAME_SIGN`,**且**有读数的年数 `>= MIN_YEARS_SAME_SIGN`。第二个条件是
      防「只有 2 年有数据、2 年全同号」冒充稳定;不足 → False。
    - `halves`:把**有观测的日**按日期(YYYYMMDD 字符串,字典序即时序)排序后前后均分,
      奇数个时中位那天归后半(`mid = n_days // 2`);两半同号 → `halves_sign_ok`。
      `n_days < 2` → 两半皆 None、`halves_sign_ok=False`。
    - `freq_per_week = n_events / (n_days / 5)`。⚠️ 分母是 **frame 自己的观测日数**(契约明写
      「不查日历」),所以它是「每 5 个**有观测的**交易日几个事件」,**不是**日历频率 ——
      一个一年只响 3 天的信号在这里会显得很频繁。可用性讨论请配 `n_days` 一起读。
    - 空 frame(或全部行的 value/date 不可用)→ 全 None / 0,**不抛**。

    缺列会 `raise`:`date_col` 不在表里时 `date_cluster_bootstrap` 会退化成「每行一簇」并给出
    一个窄到假的区间,那是最难发现的一种错。宁可炸。
    """
    if sample_kind not in SAMPLE_KINDS:
        raise ValueError(f"sample_kind 只能是 {SAMPLE_KINDS},收到 {sample_kind!r}")
    if frame is None:
        raise ValueError("cell_stats 需要一个 DataFrame;None 不等于空格")
    for col in (value_col, date_col):
        if col not in frame.columns:
            raise ValueError(f"cell_stats 缺列 {col!r};缺列不能当成空格,请上游补齐")

    values = pd.to_numeric(frame[value_col], errors="coerce")
    dates = frame[date_col].astype(str).str.strip()
    keep = values.notna() & dates.notna() & ~dates.isin(["", "nan", "NaT", "None"])
    work = pd.DataFrame({"__date": dates[keep].to_numpy(dtype=object),
                         "__v": values[keep].to_numpy(dtype=float)})
    if work.empty:
        return _empty_stats(sample_kind)

    daily = work.groupby("__date")["__v"].mean().sort_index()
    n_events, n_days = int(len(work)), int(len(daily))
    mean_pp = float(daily.mean())
    interval = _stats.day_equal_bootstrap(work, "__v", date_col="__date", seed=seed)

    year_of_day = pd.Series([d[:4] for d in daily.index], index=daily.index)
    yearly_raw = daily.groupby(year_of_day).mean()
    year_keys = sorted({*JUDGE_YEARS, OBSERVE_YEAR, *yearly_raw.index})
    yearly = {y: (float(yearly_raw[y]) if y in yearly_raw.index else None) for y in year_keys}
    judged = [yearly[y] for y in JUDGE_YEARS if yearly.get(y) is not None]
    same_sign_years = sum(1 for v in judged if _same_sign(v, mean_pp))
    yearly_sign_ok = (len(judged) >= MIN_YEARS_SAME_SIGN
                      and same_sign_years >= MIN_YEARS_SAME_SIGN)

    if n_days >= 2:
        mid = n_days // 2
        half1_pp: float | None = float(daily.iloc[:mid].mean())
        half2_pp: float | None = float(daily.iloc[mid:].mean())
        freq_per_week: float | None = float(5.0 * n_events / n_days)
    else:
        half1_pp = half2_pp = freq_per_week = None

    return {
        "n_events": n_events, "n_days": n_days,
        "mean_pp": mean_pp, "median_pp": float(daily.median()),
        "hit": float((daily > 0).mean()),
        "ci_low_pp": interval.lo, "ci_high_pp": interval.hi,
        "net_pp": float(mean_pp - COST_PP),
        "yearly": yearly, "yearly_sign_ok": bool(yearly_sign_ok),
        "half1_pp": half1_pp, "half2_pp": half2_pp,
        "halves_sign_ok": _same_sign(half1_pp, half2_pp),
        "freq_per_week": freq_per_week, "sample_kind": sample_kind,
    }


# ───────────────────────── §2.4 四态判读 ─────────────────────────


def judge(st: dict) -> str:
    """四态。返回 `"正证据"` | `"显著负"` | `"未证"` | `"样本不足"`。

    **样本门先判**(纪律 3):
    `sample_kind == "event"` → `n_events >= MIN_EVENTS` ∧ `n_days >= MIN_DAYS_EVENT`;
    `sample_kind == "wide"`  → `n_days >= MIN_DAYS_WIDE`。不过 → `"样本不足"`,
    **哪怕 CI 已经过线** —— 20 天刷出来的漂亮区间不叫证据。

    `正证据` = `ci_low_pp is not None` ∧ `ci_low_pp >= CI_LOWER_PP` ∧ `yearly_sign_ok`
    ∧ `halves_sign_ok`(三盏灯全亮)。
    `显著负` = `ci_high_pp is not None` ∧ `ci_high_pp < 0`。
    其余 → `未证`。**「未证」不是「已证不存在」**:区间跨 0 只说明这批样本没量出来。

    `sample_kind` 不在 `SAMPLE_KINDS` 里 → `raise`:默认成 event 会让宽族用 60 日的门过关。
    """
    kind = st.get("sample_kind")
    if kind not in SAMPLE_KINDS:
        raise ValueError(f"judge 读到未知 sample_kind={kind!r};必须是 {SAMPLE_KINDS} 之一")

    n_events = int(st.get("n_events") or 0)
    n_days = int(st.get("n_days") or 0)
    if kind == SAMPLE_EVENT:
        enough = n_events >= MIN_EVENTS and n_days >= MIN_DAYS_EVENT
    else:
        enough = n_days >= MIN_DAYS_WIDE
    if not enough:
        return THIN

    ci_low, ci_high = st.get("ci_low_pp"), st.get("ci_high_pp")
    if (ci_low is not None and ci_low >= CI_LOWER_PP
            and bool(st.get("yearly_sign_ok")) and bool(st.get("halves_sign_ok"))):
        return POS
    if ci_high is not None and ci_high < 0:
        return NEG
    return UNPROVEN
