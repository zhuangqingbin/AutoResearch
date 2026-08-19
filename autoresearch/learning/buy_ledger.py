#!/usr/bin/env python3
"""买单 ledger —— 买后管理度量 + 评级基率(确定性,零 LLM)。

design: docs/specs/2026-07-02-scan-portfolio-memory-design.md §2

系统推了买单之后从没人记账:T+1/T+5/T+10 走成什么样?目标价现实吗?开盘 gap 吃掉多少
edge?本模块逐买单落账(来源=attribution 已实现 fwd + 卡片目标价 + L1 收盘),并聚出
**评级基率**("本系统 OW 历史 T+5 胜率 X%")——样本 ≥min_n 后注入 skeptic/PM 当先验。

  uv run --no-sync python -m autoresearch.learning.buy_ledger   # → reports/learning/buy_ledger.md

## 预定义裁决规则:OW 复核降档(Wave8 W8-17 落账;届时按数据裁,**人拍板**)

**开裁条件**:买单 n≥10(2026-07-29 现 **n=9**,下一单即触发)。
**规则**:与 SELL 侧同款救对率口径(定义见 `ensemble_ledger` 模块 docstring)——
折回救对率 <50% → OW 复核由 2 跑降 1 跑;≥50% → 维持,再攒 5 折复裁。

规则先于数据固定,防"读数出来后挑一个好看的门槛"。不自动执行:改生产要人批,
且先经 `experiment_registry` 预注册(Wave5 治理边界)。
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import pandas as pd

from autoresearch.common import workspace as ws
from autoresearch.common.ruler import MAIN_RULER, SCHEMA_SWITCH_V4, TOUCH_COL
from autoresearch.learning.shrink import MIN_N_INJECT, n_tag, shrink as _shrink_fn, shrink_config

_COLS = ["date", "code", "name", "rating", "gap_open", "fwd_1", "fwd_2", "fwd_5", "fwd_10",
         "hi_10", "hi_2", "target_ret", "target_hit"]
_TARGET_RE = re.compile(r"(\d+(?:\.\d+)?)")
_SCHEMA_SWITCH = "2026-07-10"   # 卡契约 v3(超短)生效日:此前卡=10日语义按 hi_10 判
_HI2_MIN_N = 10   # hi2 校准分组样本门槛(⚠禁注惯例:n<此值的分组丢弃/禁注,all/by_regime 共用)


def _hi_col_for(day: str) -> str:
    """日期分界唯一出处(三段,边界含等号,T17):

    - `< 2026-07-10` → `hi_10_oc`(v3 前旧 swing 卡,10 日语义);
    - `< ruler.SCHEMA_SWITCH_V4` → `hi_2_oc`(v3 超短卡,2 日盘中触达);
    - `>= ruler.SCHEMA_SWITCH_V4` → `ruler.TOUCH_COL`(v4 起隔夜卡,唯一实现价=T+2 开盘)。

    分界日常量单一出处 = `ruler.py`,不在此处写死字面量(两处各写一遍走漂是复发坑)。
    """
    day = str(day)
    if day < _SCHEMA_SWITCH:
        return "hi_10_oc"
    if day < SCHEMA_SWITCH_V4:
        return "hi_2_oc"
    return TOUCH_COL


def _rebase_col_for(col: str) -> str:
    """触价列 `col` → 对应的 rebase 基列名(C2 修复,final-review 2026-08-08)。

    目标幅 `tr` 以 `close[D]` 为基;要跟 `col`(某个窗口的 MFE/实现价)比较,必须先把 `tr`
    rebase 到该窗口自己的基:`hi_2_oc`/`hi_10_oc` 都以 `o1=open[D+1]` 为基 → 用 `gap_d1`
    (=open[D+1]/close[D]−1);`ruler.TOUCH_COL`(v4 起,=`gap_c1_o2`)以 `c1=close[D+1]` 为基
    → 用 `fwd_1_cc`(=close[D+1]/close[D]−1)。

    修复前 v4 分支恒用 `gap_d1`(o1 基)去 rebase 一个 c1 基的窗口,差一个 D+1 日内涨跌幅
    因子:D+1 日内上涨(卡看多时的常态)→ 门槛偏高 → 触达率偏低;D+1 日内下跌 → 反之。
    三段分界各自的基不能串,故按 `col`(不是按 `day`)单点选基。
    """
    return "fwd_1_cc" if col == TOUCH_COL else "gap_d1"


def target_hit_for(day: str, tr: float | None, row, col: str | None = None) -> bool | None:
    """日期分界触价命中:目标幅(close_D 基)rebase 到对应窗口**自己的基**,再与该窗口 MFE/实现价比。

    switch 日(`_SCHEMA_SWITCH`)起卡契约 v3(超短)生效,窗口收窄到 2 日 → 按 `hi_2_oc` 判
    (rebase 基 = `gap_d1`,o1 基);`ruler.SCHEMA_SWITCH_V4` 起卡契约 v4(隔夜)生效,唯一
    实现价=T+2 开盘 → 按 `ruler.TOUCH_COL` 判(rebase 基 = `fwd_1_cc`,c1 基——C2 修复,
    此前误用 o1 基的 `gap_d1`,见 `_rebase_col_for`);更早的卡是 10 日语义 → 按 `hi_10_oc`
    判(rebase 基同 v3,仍是 `gap_d1`;不拿新窗口冤枉旧卡)。`col` 显式传入时跳过日期分界
    直接用该列(T17:`target_calibration` 算 v4 双列过渡的 `hi_2_oc` 参考读数用,不逐日期
    重判;rebase 基仍按 `_rebase_col_for(col)` 跟 `col` 走,不是跟 `day` 走)。缺值 → None
    (诚实标未成熟)。
    """
    if tr is None:
        return None
    col = col or _hi_col_for(day)
    hi = pd.to_numeric(pd.Series([row.get(col)]), errors="coerce").iloc[0]
    if pd.isna(hi):
        return None
    gap = pd.to_numeric(pd.Series([row.get(_rebase_col_for(col))]), errors="coerce").iloc[0]
    t_entry = (1 + tr) / (1 + gap) - 1 if not pd.isna(gap) else tr
    return bool(hi >= t_entry)


def _target_ret(scan_dir: Path, code: str) -> float | None:
    """卡片目标价(仪表盘『目标/EV目标』首个数字)÷ 当日收盘 − 1;缺 → None。"""
    p = scan_dir / "details" / f"{code}.md"
    if not p.exists():
        return None
    from autoresearch.scan.l4.parsers import _get, _parse_dashboard
    dash = _parse_dashboard(p.read_text(encoding="utf-8"))
    m = _TARGET_RE.search(_get(dash, "EV目标", "目标") or "")
    if not m:
        return None
    target = float(m.group(1))
    lp = scan_dir / "L1_scored_full.csv"
    if not lp.exists():
        lp = scan_dir / "L1_recall_top1000.csv"
    if not lp.exists():
        return None
    try:
        l1 = pd.read_csv(lp, dtype={"code": str})
        sub = l1[l1["code"].astype(str).str.zfill(6) == code]
        close = pd.to_numeric(sub.iloc[0]["close"], errors="coerce") if len(sub) else None
    except Exception:  # noqa: BLE001
        return None
    if close is None or pd.isna(close) or not close:
        return None
    return round(float(target / close - 1.0), 4)


def _read_attr(d: Path) -> pd.DataFrame | None:
    """读该 scan 日的 attribution(code 索引);缺/坏 → None。"""
    ap = d / "retro" / "attribution.csv"
    if not ap.exists():
        return None
    try:
        attr = pd.read_csv(ap, dtype={"code": str})
        attr["code"] = attr["code"].astype(str).str.zfill(6)
        return attr.set_index("code")
    except Exception:  # noqa: BLE001
        return None


def roll(scan_root: Path | str | None = None) -> pd.DataFrame:
    """逐 scan 日抽 ≥OW 买单 × attribution 已实现 fwd → ledger 帧。无买单日自然无行。

    **legacy 冻结**(E6 转正,task-2.4):`relative_buy.activate_date`(含)起的 scan 日
    一律不入账 —— 那之后「≥OW 张数」不再是买单数,继续记新行等于把两种定义接成一条
    趋势线。未配置冻结日 → 全量(现行为,parity)。历史行不动。
    """
    from autoresearch.learning import legacy_freeze
    from autoresearch.scan.health import final_ratings  # lazy 防环
    scan_root = Path(scan_root or ws.scan_root())
    cut = legacy_freeze.cutoff()
    rows = []
    if not scan_root.exists():
        return pd.DataFrame(columns=_COLS)
    for d in sorted(p for p in scan_root.iterdir() if p.is_dir() and p.name[:2] == "20"):
        if legacy_freeze.frozen(d.name, cut):
            continue
        ratings = {c: r for c, r in final_ratings(d).items() if r in ("Buy", "Overweight")}
        if not ratings:
            continue
        attr = _read_attr(d)
        names = {}
        fp = d / "finalists.csv"
        if fp.exists():
            try:
                fin = pd.read_csv(fp, dtype={"code": str})
                names = dict(zip(fin["code"].astype(str).str.zfill(6),
                                 fin.get("name", ""), strict=False))
            except Exception:  # noqa: BLE001
                pass
        for code, rating in ratings.items():
            def _a(col, code=code, attr=attr):
                if attr is None or code not in attr.index or col not in attr.columns:
                    return None
                v = pd.to_numeric(pd.Series([attr.at[code, col]]), errors="coerce").iloc[0]
                return None if pd.isna(v) else round(float(v), 6)
            tr = _target_ret(d, code)
            f10, f5 = _a("fwd_10_oc"), _a("fwd_5_oc")
            hi10, hi2, gap = _a("hi_10_oc"), _a("hi_2_oc"), _a("gap_d1")
            hit = (target_hit_for(d.name, tr, attr.loc[code])
                   if (attr is not None and code in attr.index) else None)
            rows.append({"date": d.name, "code": code, "name": names.get(code, ""),
                         "rating": rating, "gap_open": gap, "fwd_1": _a("fwd_1_oo"),
                         "fwd_2": _a(MAIN_RULER),
                         "fwd_5": f5, "fwd_10": f10, "hi_10": hi10, "hi_2": hi2,
                         "target_ret": tr, "target_hit": hit})
    return pd.DataFrame(rows, columns=_COLS)


def target_calibration(scan_root: Path | str | None = None, window: int = 30,
                       min_n: int = 10) -> dict | None:
    """全卡目标触达统计(近 window 个 scan 日,**全评级**非只 ≥OW —— 0 买期样本不断供)。

    只统计**看多目标**(tr>0;UW 向下目标负幅任何上涨都"触达",会稀释过乐观读数——
    07-05 真数据冒烟发现)+ 已成熟行(有对应窗口 MFE/实现价列);触价口径与 roll 同款 helper
    (`target_hit_for`):目标幅(close_D 基)rebase 到**对应窗口自己的基**再与窗口最高/实现价
    比(v3/更早=o1 基,v4=c1 基,见 `_rebase_col_for`,C2 修复);日期分界见 `_hi_col_for`
    (三段,T17):旧卡 `hi_10_oc` → v3 起 `hi_2_oc`(2日 MFE)→ v4 起 `ruler.TOUCH_COL`(T+2
    开盘,唯一实现价)。v4 起额外并陈 `hi_2_oc` 参考读数(`ref_hit_rate`/`ref_n`,双列过渡
    ≥20 交易日不删旧读数,不与主口径混算)。返回 None = 无现场。spec 2026-07-05 §6。
    """
    from autoresearch.scan.health import final_ratings  # lazy 防环
    scan_root = Path(scan_root or ws.scan_root())
    if not scan_root.exists():
        return None
    days = sorted(p for p in scan_root.iterdir() if p.is_dir() and p.name[:2] == "20")
    days = days[-window:]
    if not days:
        return None
    n = 0
    targets, mfes, hits = [], [], []
    ref_hits: list[bool] = []      # v4 双列过渡:hi_2_oc 参考读数(不删旧读数,不混进主口径)
    for d in days:
        attr = _read_attr(d)
        for code in final_ratings(d):
            tr = _target_ret(d, code)
            if tr is None or tr <= 0:        # 只看多目标:向下目标不入过乐观统计
                continue
            n += 1
            if attr is None or code not in attr.index:
                continue
            row = attr.loc[code]
            hit = target_hit_for(d.name, tr, row)
            if hit is None:
                continue
            col = _hi_col_for(d.name)
            hi = pd.to_numeric(pd.Series([attr.at[code, col]]), errors="coerce").iloc[0]
            targets.append(tr)
            mfes.append(float(hi))
            hits.append(hit)
            if col == TOUCH_COL and "hi_2_oc" in attr.columns:   # 仅 v4 起有区分意义
                ref_hit = target_hit_for(d.name, tr, row, col="hi_2_oc")
                if ref_hit is not None:
                    ref_hits.append(ref_hit)
    n_mature = len(hits)
    n_ref = len(ref_hits)
    return {"n": n, "n_mature": n_mature, "window": window, "min_n": min_n,
            "hit_rate": round(sum(hits) / n_mature, 3) if n_mature else None,
            "med_target": round(float(pd.Series(targets).median()), 4) if targets else None,
            "med_mfe": round(float(pd.Series(mfes).median()), 4) if mfes else None,
            "ref_hit_rate": round(sum(ref_hits) / n_ref, 3) if n_ref else None,
            "ref_n": n_ref,
            "thin": n_mature < min_n}


def calibration_line(stats: dict | None) -> str | None:
    """当日件建议行(编排层贴 `_l4_shared_instructions.md`);thin → 禁注文案。

    T17:分界日(`ruler.SCHEMA_SWITCH_V4`)起触达口径改「目标带 vs T+2 开盘」(隔夜窗唯一
    实现价);`hi_2_oc`(2 日盘中触达)读数降参考,双列并陈过渡 ≥20 交易日再议删(承
    07-10 处理 fwd_5/fwd_10 同手法,不删旧读数)——`ref_n`>0(即窗口内已出现 v4 起的成熟
    行)才附参考子句,纯 v3 窗口不显示空参考。
    """
    if stats is None:
        return None
    if stats["thin"]:
        return (f"📐 目标价校准:成熟样本不足(n={stats['n_mature']}<{stats['min_n']})"
                f"⚠样本少·禁注,先积累")
    ref = ""
    if stats.get("ref_n"):
        rr = stats.get("ref_hit_rate")
        ref = (f";参考(hi_2_oc·2日盘中触达) "
               f"{'—' if rr is None else format(rr, '.0%')}(n={stats['ref_n']})")
    return (f"📐 目标价校准:近{stats['window']}scan日触达率(目标带 vs T+2 开盘,v4) "
            f"{stats['hit_rate']:.0%}(成熟 n={stats['n_mature']};中位目标 "
            f"{stats['med_target']:+.0%} vs 中位实现 {stats['med_mfe']:+.0%}{ref})"
            f"——目标幅>{stats['med_mfe']:+.0%} 需给出超额理由")


# ---------------- 目标价 hi_2_oc 基率锚(全 universe 分布,非仅买单;task-6-brief) ----------------


def hi2_calibration(scan_root: Path | str | None = None, window: int = 30,
                    shrink: bool | None = None, k: float | None = None) -> dict:
    """全 universe(非仅买单)目标带分布基率锚:近 window 个 scan 日 attribution.csv 全量
    有值行 concat,按当日 `meta.json` 的 regime 分组(缺文件/缺键该日只进 all,不进分组)。
    分位用 `series.quantile(0.5/0.6)`;`touch8_rate` = 目标带≥8% 占比(即"旧中位目标在该
    窗口的真实触达率")。

    C3 修复(final-review 2026-08-08):主口径逐日按 `_hi_col_for(day)` 选源列——**与
    `target_calibration`/`calibration_line` 同一日期分界**,v4 起(`ruler.SCHEMA_SWITCH_V4`)
    读 `ruler.TOUCH_COL`(T+2 开盘,隔夜窗唯一实现价),此前读 `hi_2_oc`(2 日盘中 MFE)。
    修复前本函数恒读字面量 "hi_2_oc",与同一份 L4 prompt 里日级 `calibration_line` 的 v4
    文案直接矛盾(review 原话:「喂进每张卡的那条仍是 v3 口径」)。window 横跨分界日时两代
    数据诚实并存于 `all`(不强行统一成一种口径);v4 贡献的日子额外把 `hi_2_oc` 计入
    `all_ref`(双列过渡,镜像 `target_calibration` 的 `ref_hit_rate`/`ref_n` 手法,不删旧
    读数)。`by_regime` 的分组值同样取自逐日已选好的源列,不是重新硬编码 hi_2_oc。

    动机:全卡目标触达 43%、中位目标 +8% vs 中位 MFE +4% = 目标价系统性 2× 过乐观。本函数
    给 L4 卡目标价一个**基于真实目标带分布**的基率锚(p60),而非拍脑袋。

    `by_regime` 的 `touch8_rate` 是**收缩估计**(design 2026-07-12-selflearning-optimization-
    brainstorm.md §4 P0-3,C9-C12):p̂=(n·p_regime+k·p_all)/(n+k),`p_all`=`all` 组的
    `touch8_rate`。`shrink`/`k` 缺省 → 读 `scan_config.json` 的 `learning.{shrink,shrink_k}`。
    regime 分组 n<3(`shrink.MIN_N_INJECT`)仍绝对丢弃(禁注 floor,不受 `shrink` 开关影响);
    n∈[3,`_HI2_MIN_N`) 现在会出现(`thin=True` 标记),不再二值断供。`hi2_p50`/`hi2_p60`
    (分位,非"率")本函数不收缩,原始值不变。`all` 组恒返回(即便 n=0),是否据此注入简报由
    调用方 `target_calib_line` 按 `min_n` 把关。
    """
    scan_root = Path(scan_root or ws.scan_root())
    days: list[Path] = []
    if scan_root.exists():
        days = sorted(p for p in scan_root.iterdir() if p.is_dir() and p.name[:2] == "20")
        days = days[-window:]

    def _stats(vals: list[float]) -> dict:
        s = pd.Series(vals, dtype=float)
        n = len(s)
        return {"n": n,
                "hi2_p50": round(float(s.quantile(0.5)), 4) if n else None,
                "hi2_p60": round(float(s.quantile(0.6)), 4) if n else None,
                "touch8_rate": round(float((s >= 0.08).mean()), 4) if n else None}

    all_vals: list[float] = []
    ref_vals: list[float] = []      # v4 起的 hi_2_oc 参考读数(双列过渡,不进主口径/不进分组)
    regime_vals: dict[str, list[float]] = {}
    for d in days:
        attr = _read_attr(d)
        if attr is None:
            continue
        col = _hi_col_for(d.name)
        if col not in attr.columns:
            continue
        s = pd.to_numeric(attr[col], errors="coerce").dropna()
        if not len(s):
            continue
        vals = s.tolist()
        all_vals.extend(vals)
        if col == TOUCH_COL and "hi_2_oc" in attr.columns:   # 仅 v4 贡献的日子才有参考意义
            rs = pd.to_numeric(attr["hi_2_oc"], errors="coerce").dropna()
            ref_vals.extend(rs.tolist())
        regime = None
        mp = d / "meta.json"
        if mp.exists():
            try:
                regime = json.loads(mp.read_text(encoding="utf-8")).get("regime")
            except Exception:  # noqa: BLE001 — 坏 meta 不阻校准,该日只进 all
                regime = None
        if regime:
            regime_vals.setdefault(str(regime), []).extend(vals)

    all_stats = _stats(all_vals)
    p_global = all_stats.get("touch8_rate")
    if shrink is None or k is None:
        cfg_on, cfg_k = shrink_config()
        shrink = cfg_on if shrink is None else shrink
        k = cfg_k if k is None else k

    by_regime: dict = {}
    for r, v in regime_vals.items():
        st = _stats(v)
        n = st["n"]
        if n < MIN_N_INJECT:
            continue                                   # 绝对禁注 floor,不受 shrink 开关影响
        raw = st["touch8_rate"]
        if shrink and raw is not None:
            shrunk = _shrink_fn(raw, n, p_global, k)
            st["touch8_rate"] = round(float(shrunk), 4) if shrunk is not None else raw
        st["thin"] = n < _HI2_MIN_N
        by_regime[r] = st
    out = {"all": all_stats, "by_regime": by_regime}
    if ref_vals:
        out["all_ref"] = _stats(ref_vals)
    return out


def write_target_calib(scan_root: Path | str | None = None, window: int = 30,
                       out_dir: Path | str | None = None) -> dict:
    """`hi2_calibration()` 落盘 → `<out_dir>/target_calib.json`(presence-gated 消费方:
    `l4_card._target_calib_mark` 读此文件组简报 📐 行)。`out_dir` 缺省 = `context/learning`。
    一次性:`python -c "from autoresearch.learning.buy_ledger import write_target_calib as w; w()"`。
    """
    calib = hi2_calibration(scan_root=scan_root, window=window)
    out = Path(out_dir) if out_dir else ws.learning_root()
    out.mkdir(parents=True, exist_ok=True)
    (out / "target_calib.json").write_text(
        json.dumps(calib, ensure_ascii=False, indent=2), encoding="utf-8")
    return calib


def target_calib_line(calib: dict | None, regime: str | None,
                      min_n: int = _HI2_MIN_N) -> str | None:
    """L4 逐卡块 📐 行(全 universe 分布基率锚)。presence-gated:全体 n<min_n → None
    (⚠禁注惯例,整行不注);同 regime 分组若在 `by_regime` 出现(`hi2_calibration` 已按
    `MIN_N_INJECT`=3 过滤,n<10 仍会出现但 `n_tag` 标 ⚠)才追加第二段(p60 + 收缩后
    `touch8_rate`),否则只报全体。

    C3 修复(final-review 2026-08-08):文案跟随 `hi2_calibration` 的日期分界口径——v4 起
    (`ruler.SCHEMA_SWITCH_V4`)锚定隔夜窗唯一实现价(T+2 开盘),与同一份 prompt 里日级
    `calibration_line` 的措辞对齐(此前这条逐卡块硬编码「2 日 MFE」,与日级共享指令的 v4
    文案直接矛盾,L4 agent 会同时收到两个打架的目标约束)。`calib.get("all_ref")` 有数
    (即窗口内已有 v4 贡献的日子)才附 `hi_2_oc` 参考子句(双列过渡,镜像
    `calibration_line` 的 `ref` 手法,不删旧读数);纯 v3 窗口不显示空参考。
    """
    if not calib:
        return None
    allg = calib.get("all") or {}
    n_all = allg.get("n") or 0
    p60 = allg.get("hi2_p60")
    if n_all < min_n or p60 is None:
        return None
    t8 = allg.get("touch8_rate")
    ref = ""
    refg = calib.get("all_ref") or {}
    if refg.get("n"):
        rp60 = refg.get("hi2_p60")
        ref = (f";参考(hi_2_oc·2日盘中触达) "
               f"{'—' if rp60 is None else format(rp60, '+.1%')}(n={refg['n']})")
    line = f"📐 目标校准:全体隔夜窗(目标带 vs T+2 开盘,v4)p60={p60:+.1%}(n={n_all}"
    line += (f"·+8%目标历史触达 {t8:.0%})" if t8 is not None else ")") + ref
    rg = (calib.get("by_regime") or {}).get(regime) if regime else None
    if rg and rg.get("hi2_p60") is not None:
        line += f"·同 regime p60={rg['hi2_p60']:+.1%}{n_tag(rg['n'], min_n)}"
        if rg.get("touch8_rate") is not None:
            line += f"·触达率{rg['touch8_rate']:.0%}"
    return line + "——目标价超 p60 须在卡内给硬理由"


def rating_base_rates(ledger: pd.DataFrame, min_n: int = 10) -> list[dict]:
    """按评级聚基率:n / T+2 胜率&均值(主)/ T+5 胜率&均值(参考)/ 目标命中率。

    n_realized/thin 样本口径按主尺 fwd_2 已实现数(仅当 ledger 无 fwd_2 列即旧数据时回退 fwd_5)。
    """
    if ledger is None or not len(ledger):
        return []
    out = []
    for rating, g in ledger.groupby("rating"):
        has_f2 = "fwd_2" in g.columns
        f2 = pd.to_numeric(g["fwd_2"], errors="coerce").dropna() if has_f2 else pd.Series(dtype=float)
        f5 = pd.to_numeric(g["fwd_5"], errors="coerce").dropna()
        th = g["target_hit"].dropna()
        n_realized = len(f2) if has_f2 else len(f5)
        out.append({"rating": rating, "n": len(g), "n_realized": n_realized,
                    "win2": round(float((f2 > 0).mean()), 3) if len(f2) else None,
                    "mean2": round(float(f2.mean()), 4) if len(f2) else None,
                    "win5": round(float((f5 > 0).mean()), 3) if len(f5) else None,
                    "mean5": round(float(f5.mean()), 4) if len(f5) else None,
                    "target_hit": round(float(th.mean()), 3) if len(th) else None,
                    "thin": n_realized < min_n})
    return sorted(out, key=lambda r: r["rating"])


def _calib_section(calib: dict | None) -> list[str]:
    """『全卡目标校准』节(calib=None → 不加节,presence-gated)。"""
    if calib is None:
        return []
    line = calibration_line(calib)
    return ["", f"## 📐 全卡目标校准(近{calib['window']} scan 日,全评级)",
            f"- 有目标价卡 n={calib['n']},成熟(有对应窗口MFE)n={calib['n_mature']};"
            f"触达率 {'—' if calib['hit_rate'] is None else format(calib['hit_rate'], '.0%')},"
            f"中位目标 {'—' if calib['med_target'] is None else format(calib['med_target'], '+.0%')} "
            f"vs 中位MFE {'—' if calib['med_mfe'] is None else format(calib['med_mfe'], '+.0%')}",
            f"- 当日件建议行:{line}"]


def render(ledger: pd.DataFrame, calib: dict | None = None,
           freeze: str | None = "__config__") -> list[str]:
    """ledger → markdown。`freeze` 缺省现读 config(横幅不能靠调用方"记得传",
    否则冻结了但报表不说 = 读者拿着一本停止更新的账当活账读)。显式传 `None` = 无横幅。"""
    from autoresearch.learning import legacy_freeze
    if freeze == "__config__":
        freeze = legacy_freeze.cutoff()
    out = ["# 买单 ledger(买后 T+1/5/10 + 目标命中 + 开盘 gap;评级基率供 skeptic 先验)", ""]
    out += legacy_freeze.banner(freeze, what="(旧绝对门 ≥Overweight 买单账)")
    if ledger is None or not len(ledger):
        return out + ["_尚无 ≥OW 买单入账(0 买期,机制就绪等首单)_"] + _calib_section(calib)

    def f(x, pct=True):
        if x is None or pd.isna(x):
            return "—"
        return f"{x * 100:+.2f}%" if pct else str(x)

    out += ["| 日期 | 股票 | 评级 | gap开盘 | fwd_1 | fwd_2 | fwd_5 | fwd_10 | 触价hi10 | hi_2 | 目标幅 | 命中 |",
            "|---|---|---|---|---|---|---|---|---|---|---|---|"]
    for r in ledger.itertuples(index=False):
        hit = "—" if r.target_hit is None or pd.isna(r.target_hit) else ("✅" if r.target_hit else "✗")
        out.append(f"| {r.date} | {r.name}({r.code}) | {r.rating} | {f(r.gap_open)} "
                   f"| {f(r.fwd_1)} | {f(r.fwd_2)} | {f(r.fwd_5)} | {f(r.fwd_10)} | {f(r.hi_10)} "
                   f"| {f(r.hi_2)} | {f(r.target_ret)} | {hit} |")
    br = rating_base_rates(ledger)
    if br:
        out += ["", "## 评级基率(n≥10 才可注入 skeptic/PM 当先验)"]
        for b in br:
            thin = " ⚠样本少" if b["thin"] else ""
            out.append(
                f"- **{b['rating']}**:n={b['n']}(已实现 {b['n_realized']}),"
                f"**T+2 胜率 {'—' if b['win2'] is None else format(b['win2'], '.0%')}(主)**"
                f"/均值 {f(b['mean2'])},"
                f"T+5 胜率 {'—' if b['win5'] is None else format(b['win5'], '.0%')}(参考)"
                f"/均值 {f(b['mean5'])},目标命中 "
                f"{('—' if b['target_hit'] is None else format(b['target_hit'], '.0%'))}{thin}")
    out += _calib_section(calib)
    out += ["", "> fwd 列 `—` = 该日 attribution 在 fwd 成熟前写盘(retro 一次性落账)。刷新:对已成熟老日"
            "手动 `retro.attribute('<date>')` 重写 attribution 再重跑本 ledger(拉数走 factor_lab cache,幂等)。"]
    return out


def main() -> int:
    ledger = roll()
    calib = target_calibration()
    out = ws.reports_root() / "learning/buy_ledger.md"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("\n".join(render(ledger, calib=calib)) + "\n", encoding="utf-8")
    line = calibration_line(calib)
    print(f"[buy_ledger] {len(ledger)} 单 → {out}" + (f"\n{line}" if line else ""))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
