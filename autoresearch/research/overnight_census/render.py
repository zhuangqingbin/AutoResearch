#!/usr/bin/env python3
"""隔夜集中信号普查 · 读数渲染(markdown + json)。

一张表把 16 格全印出来 —— **包括没过样本门的**。设计稿 §2.4 的纪律在渲染层只有一条:
「不显著 ≠ 有 alpha」,反过来「不显著」也**不是**「已证不存在」。所以:

  * 表头下强制印 `正证据 X 格 / 共 N 格(主格 6);不显著 ≠ 有 alpha` —— 一次同时测 16 格,
    纯噪声下也会有格「显著」,这行数字是读者手里唯一的多重比较刻度;
  * 四态中文(`正证据` / `显著负` / `未证` / `样本不足`)原样印,不合并成「有/没有」二元;
  * `显著负` 加 🚨 —— 它们不是「没用的格」,是**避雷单的材料**(知道哪些夜不能持有,
    和知道哪些夜能赚一样值钱);
  * F3 全族是 X_ORACLE 事后上界,时点列印 `X`,`notes` 里带「生产不可见」,任何路径都不得
    把它渲染成可执行结论。

输入形状(由 `__init__` 组装):

    cells_with_stats = [ {**families.all_cells() 的格 dict,
                          "stats": core.cell_stats(...) 的返回,
                          "verdict": core.judge(stats)} , ... ]

`stats` 也允许平铺在格 dict 上(`_st()` 两种都吃)。`meta` 全部键可选,给什么印什么 ——
渲染层不编数,缺的键印「—」而不是 0。
"""
from __future__ import annotations

import json
import math

import pandas as pd

from . import core, families

DISCLAIMER = "_仅供研究,非投资建议。_"
NEG_MARK = "🚨"

#: 渲染层的四个**计数桶**(CONTRACT 的四态)。
_VERDICTS = ("正证据", "显著负", "未证", "样本不足")

# 判读词表比四态多:`core.judge` 给四态,而 `__main__` 走设计稿 §2.4 的五态 + §2.4 的 oracle
# 三态。渲染层**照原样印**收到的那个词,只在计数时归桶 —— 认不出的词绝不能被悄悄改写成
# 「样本不足」(那正是 2026-08-01「gate_status 读不懂加粗 → 17.4% 的卡被判三门全过」同一族
# 的病:展示层看不懂一个值,于是给了它一个看起来很正常的默认)。
POSITIVE_STATES = ("正证据", "历史正候选")
NEGATIVE_STATES = ("显著负", "显著有害")
THIN_STATES = ("样本不足", "数据不足", "数据/功效不足")
#: X_ORACLE 格的正读数**不计入**正证据 —— 设计稿 §2.1:oracle 无论多强都不进全局 H1。
ORACLE_POSITIVE_STATES = ("ORACLE_EDGE",)

_MAIN_HEAD = ("| 族 | 格 | 时点 | 层 | kind | n_events | n_days | 毛均值 pp | 净 pp | 中位 pp | "
              "胜率 | 95% CI(pp) | 逐年 | 两半 | 次·周 | 判读 |")
_MAIN_SEP = "|---|---|:--:|:--:|:--:|---:|---:|---:|---:|---:|---:|---|:--:|:--:|---:|---|"


# ───────────────────────── 取值 ─────────────────────────

def _st(cell: dict) -> dict:
    """格的统计块。`cell["stats"]` 优先;没有就当统计键平铺在格上(两种组装方式都吃)。"""
    s = cell.get("stats")
    return s if isinstance(s, dict) else cell


def _isna(x) -> bool:
    if x is None:
        return True
    if isinstance(x, float) and math.isnan(x):
        return True
    try:
        return bool(pd.isna(x))
    except (TypeError, ValueError):
        return False


def _f(x, nd: int = 2, sign: bool = True) -> str:
    if _isna(x):
        return "—"
    return f"{float(x):{'+' if sign else ''}.{nd}f}"


def _pct(x) -> str:
    return "—" if _isna(x) else f"{float(x):.0%}"


def _int(x) -> str:
    return "—" if _isna(x) else f"{int(x):d}"


def _sign_char(x) -> str:
    """逐年 / 两半的符号位:`+` / `−` / `·`(该年无读数)。0 归入 `+` 与均值同号的判定由 core 做,
    这里只显示符号,不参与判读。"""
    if _isna(x):
        return "·"
    return "+" if float(x) > 0 else ("−" if float(x) < 0 else "0")


def _yearly(st: dict) -> str:
    y = st.get("yearly") or {}
    return "".join(_sign_char(y.get(k)) for k in core.JUDGE_YEARS)


def _halves(st: dict) -> str:
    return f"{_sign_char(st.get('half1_pp'))}{_sign_char(st.get('half2_pp'))}"


def _ci(st: dict) -> str:
    lo, hi = st.get("ci_low_pp"), st.get("ci_high_pp")
    if _isna(lo) or _isna(hi):
        return "—"
    return f"[{float(lo):+.2f}, {float(hi):+.2f}]"


def _verdict(cell: dict) -> str:
    """格的判读词,**原样返回**(四态 / 五态 / oracle 三态都吃)。只有真的没有判读时才回落。"""
    v = cell.get("verdict") or _st(cell).get("verdict") or cell.get("state")
    return v if isinstance(v, str) and v.strip() else "样本不足"


def _bucket(v: str) -> str:
    """判读词 → 四个计数桶之一。认不出的词进「未证」桶(**表里仍印原词**),不假装是样本不足。"""
    if v in POSITIVE_STATES:
        return "正证据"
    if v in NEGATIVE_STATES:
        return "显著负"
    if v in THIN_STATES:
        return "样本不足"
    return "未证"


def _row(cell: dict) -> str:
    st = _st(cell)
    v = _verdict(cell)
    b = _bucket(v)
    mark = f"{NEG_MARK} " if b == "显著负" else ""
    strong = "**" if b in ("正证据", "显著负") or v in ORACLE_POSITIVE_STATES else ""
    return (f"| {cell.get('family', '—')} | {cell.get('label', '—')} | "
            f"{cell.get('timing', '—')} | {cell.get('tier') or '—'} | {cell.get('kind', '—')} | "
            f"{_int(st.get('n_events'))} | {_int(st.get('n_days'))} | "
            f"{strong}{_f(st.get('mean_pp'))}{strong} | {_f(st.get('net_pp'))} | "
            f"{_f(st.get('median_pp'))} | {_pct(st.get('hit'))} | {_ci(st)} | "
            f"`{_yearly(st)}` | `{_halves(st)}` | {_f(st.get('freq_per_week'), 1, sign=False)} | "
            f"{mark}{strong}{v}{strong} |")


def _table(cells: list[dict]) -> list[str]:
    if not cells:
        return ["_(空)_", ""]
    return [_MAIN_HEAD, _MAIN_SEP, *(_row(c) for c in cells), ""]


def counts(cells_with_stats: list[dict]) -> dict:
    """判读计数 —— markdown 与 json 共用同一个口径,防两处各算一遍算出两个数。

    `by_verdict` 是**原词**计数(不归桶),四态键是归桶后的计数。两份都落,任何被归桶的词
    都还能在 `by_verdict` 里找回来。
    """
    out = dict.fromkeys(_VERDICTS, 0)
    raw: dict[str, int] = {}
    for c in cells_with_stats:
        v = _verdict(c)
        raw[v] = raw.get(v, 0) + 1
        out[_bucket(v)] += 1
    out["by_verdict"] = raw
    out["n_cells"] = len(cells_with_stats)
    out["n_main"] = sum(1 for c in cells_with_stats if c.get("kind") == "main")
    out["n_main_positive"] = sum(1 for c in cells_with_stats if c.get("kind") == "main"
                                 and _bucket(_verdict(c)) == "正证据")
    out["n_oracle_edge"] = sum(1 for c in cells_with_stats
                               if _verdict(c) in ORACLE_POSITIVE_STATES)
    return out


def split_f5(cells_with_stats: list[dict]) -> tuple[list[dict], list[dict]]:
    """按 `CELL_ORDER` 排序,并把 F5 派生格从 16 格本体里分出来。

    F5 是叠在别的格上的**条件层**,不是第 17、18… 格 —— 混进「共 N 格」会把多重比较的刻度
    读错(N 从 16 变成 28,而它们根本不是独立的假设)。
    """
    order = {f: i for i, f in enumerate(families.CELL_ORDER)}
    ordered = sorted(cells_with_stats, key=lambda c: order.get(c.get("family"), 99))
    is_f5 = [str(c.get("family", "")).startswith("F5") for c in ordered]
    return ([c for c, f in zip(ordered, is_f5, strict=True) if not f],
            [c for c, f in zip(ordered, is_f5, strict=True) if f])


def multiplicity_line(cells_with_stats: list[dict]) -> str:
    """**强制行**(设计稿 §2.3):一次测 N 格,单看某一格的 95% CI 会系统性高估证据强度。"""
    c = counts(cells_with_stats)
    return (f"**正证据 {c['正证据']} 格 / 共 {c['n_cells']} 格(主格 {c['n_main']});"
            f"不显著 ≠ 有 alpha** —— 一次同时测 {c['n_cells']} 格,纯噪声下也会有格「显著」;"
            f"反过来,未证也不是「已证不存在」。")


# ───────────────────────── meta 段 ─────────────────────────

def _get(meta: dict, *names, default=None):
    for n in names:
        if n in meta and meta[n] is not None:
            return meta[n]
    return default


def _meta_lines(cells_with_stats: list[dict], meta: dict) -> list[str]:
    lake = _get(meta, "lake", default={}) or {}
    first = _get(meta, "lake_first", default=lake.get("first"))
    last = _get(meta, "lake_last", default=lake.get("last"))
    n_days = _get(meta, "n_days", default=lake.get("n_days"))
    n_rows = _get(meta, "n_rows")
    rows_txt = "" if _isna(n_rows) else f";面板行 {_int(n_rows)}"
    L = ["## 4. meta(口径与现场)", "",
         f"- 湖窗 **{first or '—'} → {last or '—'}**;面板交易日 {_int(n_days)} 天{rows_txt}",
         f"- 引擎 `{_get(meta, 'engine', default='—')}` · run `{_get(meta, 'run_id', default='—')}`"
         f" · git `{_get(meta, 'git_sha', default='—')}`"
         + (" **dirty**" if _get(meta, "git_dirty") else ""),
         f"- 成本 `COST_PP={core.COST_PP}`(印花税 5bps 卖 + 佣金 2.5bps×2 + 滑点 5bps);"
         f"正证据门 `CI_LOWER_PP={core.CI_LOWER_PP}`;样本门 event `n_events≥{core.MIN_EVENTS}"
         f" ∧ n_days≥{core.MIN_DAYS_EVENT}` / wide `n_days≥{core.MIN_DAYS_WIDE}`;"
         f"稳定性 `{'/'.join(core.JUDGE_YEARS)} 至少 {core.MIN_YEARS_SAME_SIGN} 年同号 ∧ 两半同号`",
         f"- 人口 `CAP_FLOOR_YI={core.CAP_FLOOR_YI}`;F3 族**不要求** `buyable_c1`(生产不可见的"
         f"镜像人口),其余格要求", ""]

    seat = _get(meta, "seat", "seat_coverage", default={}) or {}
    if seat:
        fb_txt = (f" {NEG_MARK} 已回退关键字规则,F2a 降 exploratory" if seat.get("fallback")
                  else " ✅ 未回退")
        L += [f"- 席位名单匹配率 **{_pct(seat.get('match_rate'))}**"
              f"(下限 {core.SEAT_MATCH_MIN:.0%}){fb_txt}"
              f";榜行 {_int(seat.get('n'))}(游资 {_int(seat.get('n_youzi'))} / 机构 "
              f"{_int(seat.get('n_inst'))} / 北向 {_int(seat.get('n_north'))})", ""]

    cov = _get(meta, "coverage", "tables", default={}) or {}
    if cov:
        L += ["### 4.1 源表覆盖", "", "| 表 | 文件 | 区间 | 覆盖率 | 缺日 | 行 |",
              "|---|---:|---|---:|---:|---:|"]
        for name, c in cov.items():
            c = c or {}
            L.append(f"| `{name}` | {_int(c.get('n_files'))} | "
                     f"{c.get('first') or '—'} → {c.get('last') or '—'} | "
                     f"{_pct(c.get('coverage'))} | {_int(c.get('missing_days'))} | "
                     f"{_int(c.get('n_rows'))} |")
        L.append("")

    drops = _get(meta, "drops", default={}) or {}
    sealed = {c["family"]: c.get("n_sealed_dropped", 0) for c in cells_with_stats
              if c.get("n_sealed_dropped")}
    L += ["### 4.2 剔除计数", ""]
    if drops:
        L += ["- " + "、".join(f"{k} {v}" for k, v in drops.items())]
    L += ["- 因 D+1 不可买(封板/停牌)剔除(**R 族;剔了会美化账本,故显式计数**):"
          + ("、".join(f"`{k}` {v}" for k, v in sealed.items()) if sealed else "无"), ""]

    L += ["### 4.3 格注(口径全文)", ""]
    for c in cells_with_stats:
        if c.get("notes"):
            L.append(f"- `{c.get('family')}` {c.get('label', '')}:{c['notes']}")
    L.append("")
    return L


def _f5_pairs_from_cells(f5_cells: list[dict]) -> list[dict]:
    """把混在 cells 里的 F5 格(`F5·<格>·with_exec_ok` / `…·without_exec_ok`)配成对。

    `__main__` 把 F5 直接追加进 cells 而不是塞 `meta["f5"]`,两种喂法都得能出这张表 ——
    否则这一节会静静地印「未提供」,而实际上算过了。
    """
    by: dict[str, dict] = {}
    for c in f5_cells:
        fam = str(c.get("family", ""))
        side = "without" if "without" in fam else ("with" if "with" in fam else None)
        if side is None:
            continue
        key = fam.split("·")[1] if "·" in fam else fam
        by.setdefault(key, {"family": key})[side] = _st(c)
    return [v for _, v in sorted(by.items()) if "with" in v or "without" in v]


def _f5_lines(meta: dict, f5_cells: list[dict] | None = None) -> list[str]:
    """F5 T+1 条件层:with / without 同表并印 + 配对增量。

    两种喂法都吃:`meta["f5"] = [{"family","with":stats,"without":stats,"delta_pp"?}]`,
    或把 `F5·…·with_exec_ok` 格直接混在 `cells_with_stats` 里。都没有 → 说没算,不拿 0 顶。
    """
    rows = _get(meta, "f5", default=None) or _f5_pairs_from_cells(f5_cells or [])
    L = ["## 3. F5 · T+1 条件层增量(叠在 F1/F2 每格上)", "",
         "`exec_ok = t1_pct_chg ≤ 3 ∧ t1_pos_in_range < 0.7 ∧ buyable_c1`,与 "
         "`scan/outcome.py` 的**事后记账**定义锁相等 —— 读的是 D+1 收盘结果,"
         f"所以整层是 X_ORACLE,**不能**说成 14:45 的过滤规则。{NEG_MARK} 慎读。", ""]
    if not rows:
        return L + ["_未提供(`meta['f5']` 缺席,cells 里也没有 F5 格)—— 不是「没有差异」,是没算。_",
                    ""]
    L += ["| 格 | with n | with 毛均值 pp | without n | without 毛均值 pp | 增量 pp |",
          "|---|---:|---:|---:|---:|---:|"]
    for r in rows:
        w, wo = r.get("with") or {}, r.get("without") or {}
        d = r.get("delta_pp")
        if _isna(d) and not _isna(w.get("mean_pp")) and not _isna(wo.get("mean_pp")):
            d = float(w["mean_pp"]) - float(wo["mean_pp"])
        L.append(f"| {r.get('family', '—')} | {_int(w.get('n_events'))} | "
                 f"{_f(w.get('mean_pp'))} | {_int(wo.get('n_events'))} | "
                 f"{_f(wo.get('mean_pp'))} | {_f(d)} |")
    return L + [""]


# ───────────────────────── 入口 ─────────────────────────

def render_markdown(cells_with_stats: list[dict], meta: dict) -> str:
    """16 格读数 → markdown。主格表 → exploratory 表 → F5 增量表 → meta 段 → 免责行。"""
    cells_with_stats = list(cells_with_stats or [])
    meta = dict(meta or {})
    body, f5_cells = split_f5(cells_with_stats)
    main = [c for c in body if c.get("kind") == "main"]
    expl = [c for c in body if c.get("kind") != "main"]

    L = ["# 隔夜集中信号普查 · 读数", "",
         "> 预注册见 `docs/specs/2026-08-28-overnight-concentrated-signal-census-design.md` §2/§3"
         "(commit 早于本读数)。主尺 `gap_c1_o2` = close(D+1) → open(D+2),单位 pp。", "",
         multiplicity_line(body), ""]

    c = counts(body)
    L += [f"**H0「预注册主格无一达到成本后正期望」:"
          f"{'被推翻' if c['n_main_positive'] else '未被推翻'}**"
          f" —— 主格 {c['n_main']} 格里正证据 {c['n_main_positive']} 格;"
          f"全表 正证据 {c['正证据']} / 显著负 {c['显著负']} / 未证 {c['未证']} / "
          f"样本不足 {c['样本不足']}"
          + (f";另有 oracle 正读数 {c['n_oracle_edge']} 格(X 族,不进 H0)"
             if c["n_oracle_edge"] else "") + "。"]
    extra = [k for k in c["by_verdict"] if k not in _VERDICTS]
    if extra:
        L += ["", "_原词计数(归桶前):"
              + "、".join(f"`{k}` {v}" for k, v in sorted(c["by_verdict"].items())) + "_"]
    L += ["", "## 1. 主格(预注册 confirmatory 候选)", ""]
    L += _table(main)
    L += ["## 2. exploratory(不参与 H0;正读数只能叫「候选」)", ""]
    L += _table(expl)
    L += _f5_lines(meta, f5_cells)
    L += _meta_lines(body, meta)
    L += ["---", "",
          f"_判读四态:**正证据** = 绝对毛均值日聚簇 95% CI 下界 ≥ {core.CI_LOWER_PP}pp "
          f"∧ 逐年 ≥{core.MIN_YEARS_SAME_SIGN}/4 同号 ∧ 两半同号;**{NEG_MARK} 显著负** = CI 上界 < 0"
          f"(避雷单材料,不是「没用的格」);**未证** = 区间跨门(不显著 ≠ 有 alpha,也 ≠ 已证不存在);"
          f"**样本不足** = 事件族 n_events<{core.MIN_EVENTS} 或 n_days<{core.MIN_DAYS_EVENT}"
          f"(宽族 n_days<{core.MIN_DAYS_WIDE}),只印数不判。净 pp = 毛 − {core.COST_PP} 成本。_", "",
          "_F3 全族 `X_ORACLE`:用 D+1 收盘后才存在的 `limit`/`last_time`/`fd_amount` 事后构造,"
          "只能判理论上界,**不是**「14:57 挂单能成交」的证明;F3d 的数值是同日配对**增量**不是收益。_",
          "", DISCLAIMER]
    return "\n".join(L) + "\n"


def _jsonable(x):
    if isinstance(x, dict):
        return {str(k): _jsonable(v) for k, v in x.items()}
    if isinstance(x, (list, tuple)):
        return [_jsonable(v) for v in x]
    if isinstance(x, pd.DataFrame):
        return {"n_rows": int(len(x)), "columns": [str(c) for c in x.columns]}
    if _isna(x):
        return None
    if hasattr(x, "item"):
        try:
            return x.item()
        except (ValueError, AttributeError):
            return str(x)
    if isinstance(x, (str, int, float, bool)):
        return x
    return str(x)


def render_json(cells_with_stats: list[dict], meta: dict) -> dict:
    """同一份读数的机器可读版。`rows`(DataFrame)不落 json,只落形状 —— 观测明细归 parquet,
    读数 json 是给对账用的,不是第二份数据源。"""
    body, f5_cells = split_f5(list(cells_with_stats or []))
    out_cells = []
    for c in body + f5_cells:
        st = _st(c)
        out_cells.append({
            "family": c.get("family"), "label": c.get("label"), "kind": c.get("kind"),
            "timing": c.get("timing"), "tier": c.get("tier"),
            "sample_kind": c.get("sample_kind"), "notes": c.get("notes"),
            "n_sealed_dropped": int(c.get("n_sealed_dropped") or 0),
            "verdict": _verdict(c),
            "stats": _jsonable({k: v for k, v in st.items()
                                if k not in ("rows", "stats", "notes")}),
        })
    return {"schema": "overnight_census/1", "counts": counts(body), "n_f5": len(f5_cells),
            "cell_order": list(families.CELL_ORDER), "main_cells": list(families.MAIN_CELLS),
            "thresholds": {"COST_PP": core.COST_PP, "CI_LOWER_PP": core.CI_LOWER_PP,
                           "MIN_EVENTS": core.MIN_EVENTS, "MIN_DAYS_EVENT": core.MIN_DAYS_EVENT,
                           "MIN_DAYS_WIDE": core.MIN_DAYS_WIDE,
                           "MIN_YEARS_SAME_SIGN": core.MIN_YEARS_SAME_SIGN,
                           "JUDGE_YEARS": list(core.JUDGE_YEARS),
                           "CAP_FLOOR_YI": core.CAP_FLOOR_YI,
                           "SEAT_MATCH_MIN": core.SEAT_MATCH_MIN},
            "meta": _jsonable(meta or {}), "cells": out_cells,
            "disclaimer": DISCLAIMER}


def dumps_json(cells_with_stats: list[dict], meta: dict) -> str:
    return json.dumps(render_json(cells_with_stats, meta), ensure_ascii=False, indent=1)
