#!/usr/bin/env python3
"""P0-5 · 教训边际收益记账 lesson_yield —— 给"反思是否净正贡献"装证伪器(确定性,零 LLM)。

design: docs/specs/2026-07-12-selflearning-optimization-brainstorm.md §4-P0-5
plan:   docs/plans/2026-07-12-selflearning-p0-plan.md T5

`retro.mtm_check_guards`(retro.py:233-265)已能对**单日**判一条带 guard 经验的 support/
refute,但从不累计——本模块**复用它**(逐日调用,`apply=False`,不重写谓词执行、不产生
副作用)对每个历史 scan 日的 `attribution.csv` 重放同一条 guard,把逐日 excess 转成
"遵循该教训的反事实 Δpp"并累计成曲线:

    delta_d = -excess_d = 市场均值fwd_2_oc − 命中组均值fwd_2_oc
    (excess_d 直接取自 `mtm_check_guards` 的返回值,不重算)

guard 全是"拦买"型(条件成立 = 建议避开/降级),所以"遵循该教训" = 避开命中组、改持
市场均值仓位;delta_d>0 = 当天避开有益(与 verdict=support 同号),delta_d≤0 = 当天
避开无益或有害(与 verdict=refute 同号)。cum_delta = Σdelta_d —— 逐日等权累计的净值
曲线(不按命中股数加权;每个有效交易日贡献一步,如同净值曲线),命中股数计入 n_cum
只作为裁决法的样本量门槛。

裁决法(§4-P0-5,原文):
  累计命中样本 n(=Σ有效日 n)≥20 且 cum_delta≤0 → 报表标"提名 retire(人批)"——
  只提名,不调用 `feedback_store.retire_lesson`,沿用 MTM"降级只提名"既定纪律。
  全体带 guard lessons 合计边际(Σ cum_delta,仅计入 n_cum>0 的条目;且 Σn_cum≥
  RETIRE_MIN_N 才判,防止用零星噪声触发)|Σ|<_AGG_FLAT_EPS(0.5pp)→ 报表追加
  "触发 P2-2(cap 收缩实验)"提示行。

只评估**当前 active 且 global scope** 的经验(与生产侧 `retro.write_retro_input` 喂
`mtm_check_guards` 的口径一致:`fs.lessons_for([("global", "*")])`)——guard 谓词是
market-wide 列判断,industry/ticker scope 的经验若也这样重放会把全市场当成其 scope
误算;retro.py 本身也没有这层 scope 过滤,不在本模块/本波修(超出 T5 边界)。

用法:
  uv run --no-sync python -m autoresearch.learning.lesson_yield   # → reports/learning/lesson_yield.md
"""
from __future__ import annotations

from pathlib import Path

import pandas as pd

import autoresearch.learning.feedback_store as fs
import autoresearch.learning.retro as retro

RETIRE_MIN_N = 20        # 裁决法:累计命中样本门槛(§4-P0-5 原文 "n≥20")
_DAY_MIN_N = 5           # 逐日门槛:与 retro.mtm_check_guards 自身默认 min_n 一致(复用,不重开新阈值)
_AGG_FLAT_EPS = 0.005    # 全体合计边际 |Σ cum_delta| < 0.5pp 视为 "≈0"(触发 P2-2 提示)

_COLS = ["id", "rule", "guard", "n_days_hit", "n_cum", "support", "refute",
         "cum_delta", "status", "curve"]


def active_guard_lessons() -> list[dict]:
    """当前 active + global scope + 带 guard 的经验(口径同 `retro.write_retro_input`)。"""
    return [r for r in fs.lessons_for([("global", "*")]) if isinstance(r.get("guard"), dict)]


def _walk_attribution(scan_root: Path) -> list[tuple[str, pd.DataFrame]]:
    """按日期升序读取全部历史 `context/scan/<date>/retro/attribution.csv`(缺失/损坏日跳过)。"""
    out: list[tuple[str, pd.DataFrame]] = []
    for p in sorted(scan_root.glob("*/retro/attribution.csv")):
        try:
            attr = pd.read_csv(p, dtype={"code": str})
        except Exception:  # noqa: BLE001
            continue
        if len(attr) and "fwd_2_oc" in attr.columns:
            out.append((p.parent.parent.name, attr))
    return out


def compute_yield(lessons: list[dict], days: list[tuple[str, pd.DataFrame]],
                  day_min_n: int = _DAY_MIN_N) -> pd.DataFrame:
    """逐日复用 `retro.mtm_check_guards`(apply=False)重放每条 lesson 的 guard,累计成
    反事实 Δpp 曲线 + support/refute 计数 + 裁决法状态。纯函数,无 IO、不改 lessons.jsonl。

    输出按 cum_delta 升序(最拖累/最该关注的排最前)。空 lessons 或空 days → 空表(presence-gated)。
    自行过滤掉无有效 guard({field,op,value} dict)的条目——与 `retro.mtm_check_guards`
    对它们"静默 continue、从不出现在结果里"的行为一致,不依赖调用方预先过滤。
    """
    lessons = [r for r in lessons if isinstance(r.get("guard"), dict)]
    if not lessons or not days:
        return pd.DataFrame(columns=_COLS)
    acc: dict[str, dict] = {r["id"]: {"n_cum": 0, "delta_cum": 0.0, "support": 0, "refute": 0,
                                       "n_days_hit": 0, "curve": []} for r in lessons}
    for day, attr in days:
        for r in retro.mtm_check_guards(attr, lessons, day, min_n=day_min_n, apply=False):
            if r.get("excess") is None or r.get("verdict") == "skip":
                continue                                    # 该日样本不足/无有效 mkt,不入曲线
            a = acc.get(r["id"])
            if a is None:
                continue
            delta = round(-float(r["excess"]), 6)           # 反事实 Δ:避开命中组相对市场均值的收益
            a["n_cum"] += int(r["n"])
            a["delta_cum"] = round(a["delta_cum"] + delta, 6)
            a["n_days_hit"] += 1
            if r["verdict"] in ("support", "refute"):
                a[r["verdict"]] += 1
            a["curve"].append({"date": day, "n": int(r["n"]), "excess": r["excess"],
                               "delta": delta, "cum_delta": a["delta_cum"]})
    rows = []
    for lsn in lessons:
        a = acc[lsn["id"]]
        n_cum, cum_delta = a["n_cum"], a["delta_cum"]
        if n_cum < RETIRE_MIN_N:
            status = f"样本不足(n={n_cum}<{RETIRE_MIN_N})"
        elif cum_delta <= 0:
            status = "提名 retire(人批)"
        else:
            status = "净贡献为正,继续观察"
        rows.append({"id": lsn["id"], "rule": str(lsn.get("rule", ""))[:60],
                     "guard": lsn.get("guard"), "n_days_hit": a["n_days_hit"], "n_cum": n_cum,
                     "support": a["support"], "refute": a["refute"], "cum_delta": cum_delta,
                     "status": status, "curve": a["curve"]})
    out = pd.DataFrame(rows, columns=_COLS)
    return out.sort_values("cum_delta", ascending=True).reset_index(drop=True) if len(out) else out


def roll(scan_root: Path | str | None = None, lessons: list[dict] | None = None,
        day_min_n: int = _DAY_MIN_N) -> pd.DataFrame:
    """CLI 主入口的确定性核心:扫描历史 scan 日 × 当前带 guard lessons → 逐条累计报表。

    `lessons=None` → 走生产口径(`active_guard_lessons()`,读真实 `context/knowledge`);
    传显式 `lessons` 供测试注入合成经验,绕开 feedback_store 读写。
    """
    scan_root = Path(scan_root or "context/scan")
    lessons = active_guard_lessons() if lessons is None else lessons
    days = _walk_attribution(scan_root)
    return compute_yield(lessons, days, day_min_n=day_min_n)


def _fmt_pp(x) -> str:
    return "—" if x is None or pd.isna(x) else f"{x * 100:+.2f}pp"


def render(df: pd.DataFrame, n_lessons_total: int = 0) -> list[str]:
    """报表:逐条累计 Δpp 摘要表 + 提名 retire 清单 + 全体合计边际 flat 提示(P2-2)+ 逐日曲线。"""
    out = ["# 教训边际收益记账(lesson_yield · P0-5 反思证伪器)", ""]
    if df is None or not len(df):
        out += [f"_当前 active lessons 共 {n_lessons_total} 条,带 guard 的 0 条 —— "
               "guard 覆盖率 0,无可评估条目(见 P1-2 guard 强制化提案)。_"]
        return out

    out += [f"当前 active lessons {n_lessons_total} 条,带 guard 可评估 {len(df)} 条。", "",
           "| id | 规则(节选) | 命中天数 | 累计命中n | support | refute | 累计Δ(反事实) | 状态 |",
           "|---|---|---|---|---|---|---|---|"]
    for r in df.itertuples(index=False):
        out.append(f"| `{r.id}` | {r.rule} | {r.n_days_hit} | {r.n_cum} | {r.support} | "
                   f"{r.refute} | {_fmt_pp(r.cum_delta)} | {r.status} |")

    nominated = df[df["status"] == "提名 retire(人批)"]
    if len(nominated):
        out += ["", "## 提名 retire(人批;只提名不动作,沿用 MTM 降级只提名纪律)"]
        out += [f"- `{r.id}`:累计命中 n={r.n_cum},累计Δ={_fmt_pp(r.cum_delta)}"
               f"(support {r.support}/refute {r.refute})" for r in nominated.itertuples(index=False)]

    evaluated = df[df["n_cum"] > 0]
    if len(evaluated) and evaluated["n_cum"].sum() >= RETIRE_MIN_N:
        agg = float(evaluated["cum_delta"].sum())
        out += ["", f"## 全体合计边际:{_fmt_pp(agg)}(Σ n_cum={int(evaluated['n_cum'].sum())})"]
        if abs(agg) < _AGG_FLAT_EPS:
            out.append(f"⚠️ 合计边际 ≈0(|Σ|<{_AGG_FLAT_EPS * 100:.1f}pp)——"
                       "触发 P2-2(cap 收缩实验候选,见 brainstorm §4-P2-2)。")

    curve_lines = []
    for r in df.itertuples(index=False):
        if not r.curve:
            continue
        curve_lines.append(f"- `{r.id}`:")
        curve_lines += [f"  - {c['date']}: n={c['n']} excess={c['excess']:+.4f} "
                        f"Δ={_fmt_pp(c['delta'])} 累计Δ={_fmt_pp(c['cum_delta'])}" for c in r.curve]
    if curve_lines:
        out += ["", "### 逐条命中曲线(逐日累计,presence-gated;仅列有命中的日)", *curve_lines]
    return out


def main() -> int:
    lessons = active_guard_lessons()
    df = roll(lessons=lessons)
    total = len(fs.lessons_for([("global", "*")]))
    body = "\n".join(render(df, n_lessons_total=total))
    out = Path("reports/learning/lesson_yield.md")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(body + "\n", encoding="utf-8")
    print(body)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
