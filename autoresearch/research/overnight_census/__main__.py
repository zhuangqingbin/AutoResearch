#!/usr/bin/env python3
"""隔夜集中信号普查 · CLI 与集成层(2026-08-28)。

    uv run --no-sync python -m autoresearch.research.overnight_census --backfill
    uv run --no-sync python -m autoresearch.research.overnight_census --run

预注册:`docs/research/2026-08-28-overnight-concentrated-census.md` §0(先 commit 后看读数)。
设计稿:`docs/specs/2026-08-28-overnight-concentrated-signal-census-design.md`(含版本溯源头)。

本层负责三件**跨模块**的事(各族/面板/回填的实现分别在 families / panel / backfill):

1. **五态判读**(预注册 §0.4)。`core.judge` 给的是四态;本层把它的「未证」按经济门
   拆成「经济性证伪」(CI 上界 < COST 门 → 已排除覆盖成本的可能)与「未决」(区间仍跨门)。
   拆开的理由:证伪普查里「我们排除了它」和「我们没量出来」是两个结论,合成一个会把
   前者的信息丢掉。
2. **actionability 轴**。F3/F5 的输入含 EOD 终值(`limit`/`last_time`/`fd_amount`/`exec_ok`
   都在收盘后才定),故整族只能给理论上界 —— 它们**不得**输出「历史正候选」,一律降级为
   `ORACLE_*`。这是设计稿 v2 逮到 v1 的缺陷之一(v1 只把 T1 标了上界)。
3. **日等权区间由公共原语算,本层不再覆盖**。`core.cell_stats` 与本层曾各有一份实现:
   底层的区间围绕行等权中心,本层事后用 `day_equal_ci` 覆盖来补偿。现在两者同走
   `common.stats.day_equal_bootstrap`,core 一次算对,本层只挑方法串。
   两个入口**各自保留既有默认 seed**(core `20260828` / 公共层 `20260803`);本层显式传
   公共层那个 —— 08-28 那份读数印的就是它,少传一次数字会静默换一批。
"""
from __future__ import annotations

import argparse
import json
import subprocess
import time
from pathlib import Path

import pandas as pd

from autoresearch.common import stats as _stats, workspace as ws

from . import backfill as backfill_mod, core, families, panel as panel_mod, render as render_mod

# ── 五态(预注册 §0.4;字符串即读数表里的字面量,不要改) ──
POS_HIST = "历史正候选"
ECON_RULED_OUT = "经济性证伪"
HARMFUL = "显著有害"
UNDECIDED = "未决"
THIN = "数据不足"

# ── actionability(预注册 §0.4) ──
R_ELIGIBLE = "R_REPORT_ELIGIBLE"
X_ORACLE = "X_ORACLE_ONLY"
ORACLE_EDGE = "ORACLE_EDGE"
ORACLE_RULED_OUT = "ORACLE_RULED_OUT"
ORACLE_INCONCLUSIVE = "ORACLE_INCONCLUSIVE"

MAIN_VALUE_COL = "gap_pp"          # 判读只认绝对毛(预注册 §0.2)
REL_VALUE_COL = "rel_gap_pp"       # 并印,不判读

# ── 区间方法串(读数表与 meta 里的字面量,值不要改;此处是它的唯一事实源)──
CI_METHOD = "date_cluster_bootstrap(day-equal)"
CI_METHOD_NO_INTERVAL = "date_cluster_bootstrap(day-equal, n_days<2 → 无区间)"


def day_equal_ci(rows: pd.DataFrame, value_col: str = MAIN_VALUE_COL,
                 date_col: str = "date") -> tuple[float | None, float | None, str]:
    """日等权 95% 区间(pp)。→ (lo, hi, method)。日数 < 2 → (None, None, ...)(诚实:无跨日方差)。

    **兼容入口**:区间由 `common.stats.day_equal_bootstrap` 提供(与 `core.cell_stats` 同一
    实现、同一公共层默认 seed),本函数只保留既有返回格式。`_stat_cell` 已不再调它 ——
    留着是因为它是这份读数口径的公开名字,外部脚本可以拿单独一批行问同一个问题。
    """
    if rows is None or len(rows) == 0 or value_col not in getattr(rows, "columns", []):
        return None, None, CI_METHOD_NO_INTERVAL
    iv = _stats.day_equal_bootstrap(rows, value_col, date_col=date_col)
    if iv.n_clusters < 2:
        return None, None, CI_METHOD_NO_INTERVAL
    return iv.lo, iv.hi, CI_METHOD


def five_state(st: dict, *, ci_low, ci_high) -> str:
    """四态 → 五态。**样本门先判**(与 `core.judge` 同一口径,不重复实现,直接读它)。"""
    base = core.judge({**st, "ci_low_pp": ci_low, "ci_high_pp": ci_high})
    if base == core.THIN:
        return THIN
    if base == core.POS:
        return POS_HIST
    if ci_high is not None and ci_high < 0:
        return HARMFUL
    if ci_high is not None and ci_high < core.CI_LOWER_PP:
        return ECON_RULED_OUT      # 已排除「覆盖假设成本」——毛均值略正也不叫 BUY
    return UNDECIDED


def actionability_of(cell: dict) -> str:
    """R 族 → `R_REPORT_ELIGIBLE`(仅表示时点可供报告,≠ 生产已批准);X 族 → `X_ORACLE_ONLY`。"""
    return X_ORACLE if cell.get("timing") == "X" else R_ELIGIBLE


def oracle_verdict(state: str) -> str:
    """X_ORACLE 格不得输出「历史正候选」(输入含 EOD 终值)。三态化。"""
    if state == POS_HIST:
        return ORACLE_EDGE
    if state in (ECON_RULED_OUT, HARMFUL):
        return ORACLE_RULED_OUT
    if state == THIN:
        return THIN
    return ORACLE_INCONCLUSIVE


def _exalters(events: dict) -> pd.Series:
    """`top_inst.exalter` 列;表缺席或无该列 → 空 Series(不是 None,也不伪造)。

    注意不能写 `events.get("top_inst") or pd.DataFrame()` —— DataFrame 的真值是歧义的,
    pandas 会直接抛 ValueError(本层 2026-08-28 冒烟即被它逮到)。
    """
    df = events.get("top_inst")
    if df is None or not len(df) or "exalter" not in df.columns:
        return pd.Series(dtype=object)
    return df["exalter"]


def _git_sha() -> str:
    try:
        return subprocess.run(["git", "rev-parse", "--short", "HEAD"], capture_output=True,
                              text=True, timeout=10).stdout.strip() or "unknown"
    except Exception:
        return "unknown"


def _stat_cell(cell: dict) -> dict:
    """一格 → 带统计与判读的格。绝对列判读,相对列并印。

    区间**只算一次**:`core.cell_stats` 现在自己就是日等权口径,本层不再事后覆盖。
    `seed` 显式传公共层的那个 —— 见模块 docstring 第 3 点,这是读数数值的兼容契约。
    """
    rows, kind = cell.get("rows"), cell.get("sample_kind", "event")
    source = (rows if rows is not None
              else pd.DataFrame({"date": [], MAIN_VALUE_COL: []}))
    st = core.cell_stats(source, value_col=MAIN_VALUE_COL, sample_kind=kind,
                         seed=_stats.DEFAULT_SEED)
    lo, hi = st["ci_low_pp"], st["ci_high_pp"]
    st["ci_method"] = CI_METHOD if st["n_days"] >= 2 else CI_METHOD_NO_INTERVAL
    rel = core.cell_stats(rows, value_col=REL_VALUE_COL, sample_kind=kind,
                          seed=_stats.DEFAULT_SEED) \
        if rows is not None and REL_VALUE_COL in getattr(rows, "columns", []) else None
    st["rel_mean_pp"] = (rel or {}).get("mean_pp")

    state = five_state(st, ci_low=lo, ci_high=hi)
    act = actionability_of(cell)
    out = dict(cell)
    out["stats"] = st
    out["state"] = state
    out["actionability"] = act
    out["verdict"] = oracle_verdict(state) if act == X_ORACLE else state
    out.pop("rows", None)          # 渲染层不需要原始行;json 也别塞几十万行
    out["_rows"] = rows
    return out


#: markdown 读数的历史默认落点 —— 它是一份**已提交**的研究结论,所以默认路径也受覆盖保护。
DEFAULT_OUT_MD = Path("docs/research/2026-08-28-overnight-concentrated-census-readout.md")


def default_out_json() -> Path:
    """JSON 的默认落点(本引擎根;`reports_root()` 是路径唯一事实源,不手拼引擎名)。"""
    return ws.reports_root() / "research" / "overnight_census" / "_overnight_census.json"


def resolve_outputs(out: Path | str | None = None, out_json: Path | str | None = None,
                    *, force: bool = False) -> tuple[Path, Path]:
    """定两个落点,并在任一目标已存在时**拒绝**(除非 `force`)。→ `(md, json)`。

    为什么默认也拦:markdown 的默认值就是 08-28 那份已提交的读数,JSON 的默认值是上一轮的
    机读结论。「重算写独立目录、冻结报告不原地改写」是项目不变量,而这个入口过去是敞开的
    —— `--out` 只管 markdown,JSON 连指路的办法都没有。

    `force` 是留给操作者的显式逃逸口(会进命令留痕),不是给守卫开的后门:它只在人明确
    说「就要盖掉这一份」时才该出现。
    """
    md = Path(out) if out else DEFAULT_OUT_MD
    js = Path(out_json) if out_json else default_out_json()
    if force:
        return md, js
    clashes = [str(p) for p in (md, js) if p.exists()]
    if clashes:
        raise FileExistsError(
            "普查目标已存在,拒绝覆盖上一轮研究结论:" + "、".join(clashes)
            + f"。改道:--out <新 md> --out-json <新 json>(默认 {DEFAULT_OUT_MD} / "
            f"{default_out_json()});确实要盖掉这一份:--force")
    return md, js


def run_census(since: str | None = None, until: str | None = None, *,
               rebuild: bool = False, out: Path | None = None,
               out_json: Path | None = None, force: bool = False) -> dict:
    """全流程:面板 → 事件 → 16 格 → 统计判读 → F5 → 渲染落盘。

    落点**先定后跑**:面板要 40–90 分钟,把覆盖检查放到最后等于没有守卫。
    """
    out_md, out_json_path = resolve_outputs(out, out_json, force=force)
    t0 = time.time()
    panel = panel_mod.build_panel(since=since, until=until, rebuild=rebuild)
    events = families.load_events(lake_root=None, since=since, until=until)
    youzi = core.load_youzi_seats()
    cells = families.all_cells(panel, events, youzi=youzi)
    scored = [_stat_cell(c) for c in cells]

    # F5:只叠在 R-confirmatory 主格上(X 族本身已是 oracle,再叠一层无意义)
    f5 = []
    for c in scored:
        if c.get("kind") != "main" or c.get("timing") != "R":
            continue
        with_c, without_c = families.exec_layer_split(c.get("_rows"), panel, family=c["family"])
        for tag, sub in (("with_exec_ok", with_c), ("without_exec_ok", without_c)):
            s = _stat_cell({**sub, "family": f"F5·{c['family']}·{tag}",
                            "label": f"{c.get('label','')} × {tag}", "kind": "exploratory",
                            "timing": "X"})
            f5.append(s)

    pmeta = dict(panel.attrs.get("meta") or {})
    n_pos = sum(1 for c in scored if c["verdict"] == POS_HIST)
    meta = {
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "git_sha": _git_sha(),
        "prereg": "docs/research/2026-08-28-overnight-concentrated-census.md §0",
        "ruler": "gap_c1_o2", "cost_pp": core.COST_PP, "ci_threshold_pp": core.CI_LOWER_PP,
        "ci_method": CI_METHOD,
        # 换估计量必须留版本 + 可复现参数:光有 `ci_method` 这个名字复现不出同一批数字,
        # 而新旧版本的读数**不许直接拼成趋势**。
        "statistics_version": core.STATISTICS_VERSION,
        "ci_seed": _stats.DEFAULT_SEED, "ci_n_boot": _stats.DEFAULT_BOOT,
        "ci_alpha": _stats.DEFAULT_ALPHA,
        "ci_method_detail": ("先日内均值再跨日等权,独立日重采样;"
                             "**不是**连续交易日 moving-block 区间"),
        "multiplicity": "per-cell 95% CI(max-T simultaneous 待用户裁决,见设计稿溯源头 (a))",
        "n_cells": len(scored), "n_main": sum(1 for c in scored if c.get("kind") == "main"),
        "n_positive": n_pos, "n_f5": len(f5),
        "panel": pmeta,
        # render 的 meta 段读**顶层**键(`first`/`last`/`n_days`/`n_rows`/`coverage`),
        # 面板自报的窗口埋在 `panel.attrs["meta"]` 里 —— 不摊平的话读数会印「湖窗 — → —」,
        # 一份不写自己样本窗的读数是不可引用的。
        "lake_first": pmeta.get("built_first") or pmeta.get("lake_first"),
        "lake_last": pmeta.get("built_last") or pmeta.get("lake_last"),
        "n_days": pmeta.get("n_days"), "n_rows": pmeta.get("n_rows"),
        "engine": ws.ENGINE, "n_clipped": pmeta.get("n_clipped"),
        "mv_coverage": pmeta.get("mv_coverage"), "mv_filled": pmeta.get("mv_filled"),
        "is_st_source": pmeta.get("is_st_source"),
        "coverage": {**(backfill_mod.coverage_report() or {}),
                     **(families.events_coverage(events) or {})},
        "seat_coverage": core.seat_coverage(_exalters(events), youzi),
        "seconds": round(time.time() - t0, 1),
    }
    for c in scored + f5:
        c.pop("_rows", None)
    md = render_mod.render_markdown(scored + f5, meta)
    js = render_mod.render_json(scored + f5, meta)

    out_md.parent.mkdir(parents=True, exist_ok=True)
    out_md.write_text(md, encoding="utf-8")
    out_json_path.parent.mkdir(parents=True, exist_ok=True)
    out_json_path.write_text(json.dumps(js, ensure_ascii=False, indent=2, default=str),
                             encoding="utf-8")
    print(f"读数 → {out_md}\njson  → {out_json_path}\n{meta['seconds']}s · "
          f"正证据 {n_pos}/{len(scored)} 格", flush=True)
    return {"meta": meta, "cells": scored, "f5": f5,
            "md": str(out_md), "json": str(out_json_path)}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="overnight_census", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--backfill", action="store_true", help="一次性回填(联网,约 40–90 分钟)")
    ap.add_argument("--backfill-tables", default=None, help="逗号分隔;默认全部")
    ap.add_argument("--run", action="store_true", help="跑普查并落读数")
    ap.add_argument("--since", default=None, help="判读窗起点 YYYYMMDD(默认全湖)")
    ap.add_argument("--until", default=None)
    ap.add_argument("--rebuild", action="store_true", help="重建面板缓存")
    ap.add_argument("--coverage", action="store_true", help="只印各表覆盖率")
    ap.add_argument("--out", default=None, help=f"markdown 读数落点(默认 {DEFAULT_OUT_MD})")
    ap.add_argument("--out-json", default=None,
                    help="JSON 读数落点(默认本引擎根 research/overnight_census/)")
    ap.add_argument("--force", action="store_true",
                    help="目标已存在时照样写 —— 会盖掉上一轮的研究结论,请确认")
    a = ap.parse_args(argv)

    if a.coverage:
        print(json.dumps(backfill_mod.coverage_report(), ensure_ascii=False, indent=2, default=str))
        return 0
    if a.backfill:
        tabs = a.backfill_tables.split(",") if a.backfill_tables else None
        print(json.dumps(backfill_mod.backfill_all(since=a.since or "20220302", until=a.until,
                                                   tables=tabs),
                         ensure_ascii=False, indent=2, default=str))
    if a.run:
        run_census(since=a.since, until=a.until, rebuild=a.rebuild,
                   out=Path(a.out) if a.out else None,
                   out_json=Path(a.out_json) if a.out_json else None,
                   force=a.force)
    if not (a.backfill or a.run or a.coverage):
        ap.print_help()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
