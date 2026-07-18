#!/usr/bin/env python3
"""S1 市场情绪温度计 —— tushare limit_list_d 入湖 + 五序列 + 合成评分 + 五相位(零 LLM 纯函数)。

design: docs/specs/2026-07-07-memory-astrategy-optimization-design.md §S1;
        docs/specs/2026-07-11-funnel-p0p1-wave-plan.md Task 4/5。

动机:连续 0 买日的第二层根因 = range 市 swing/超跌反弹品相缺供给——现有 regime 三块
(risk_off/range/trend)是宽基/广度口径,缺一个**短线情绪维度**(冰点/修复段的超跌反弹
品相从未被激活)。本模块只产**数据 + 纯函数**,不接菜单/预算联动(那是下一波,见
plan doc §拍板记录);消费端见 Task 5(market_pack `temperature` 块 + L5 🌡 展示)。

五个日频序列(`daily_metrics`,presence-gated——依赖的输入缺 → 对应键 None,不报错):
  - n_limit_up / n_limit_down / n_fried:今日涨停 U / 跌停 D / 炸板 Z 家数。
  - max_streak:今日最高连板(仅 U 行 `limit_times` 取 max;Z/D 行该列为 NaN,
    07-11 真数据探针已验证)。
  - promote_rate:**首板→二板**晋级率 = 今日 limit_times==2 且昨日 limit_times==1 的
    只数 / 昨日 limit_times==1 的只数。民间口径的"晋级率"特指最基础的一级晋级,更高阶
    连板逐日样本太薄不纳入统计(且不入 composite,仅描述性,见下)。
  - fried_rate:炸板率 = Z / (U + Z)。
  - yesterday_premium:昨日**全体**涨停码(不分板数),今日涨跌幅均值("昨涨停今溢价")。

合成评分(`score`,0–100,权重 0.40/0.20/0.20/0.20 分别对应 n_limit_up/max_streak/
(1-fried_rate)/yesterday_premium)与五相位(`phase`:冰点<20 / 修复 / 发酵 / 高潮≥65 /
退潮,±3 分滞回防抖:带内小幅回落不切相位)——**v1 权重与分段阈值是待校准先验,
不是拟合结果**;`promote_rate` 暂不入 composite(信息量待验证)。真实校准(温度分段 ×
市场 fwd_1/fwd_2 条件分布,及与 regime 交叉表)走 Task 5 的
`autoresearch.scan.temperature_calib` 报告,本模块之后按证据调整,不在本次改动范围内。

`rollup(start, end)` 逐交易日 fetch → daily_metrics → score → phase,按 `date` 幂等
upsert 写 `CSV_PATH`(`context/learning/temperature.csv`)。CLI:
  uv run --no-sync python -m autoresearch.scan.temperature backfill <start> [end]
  uv run --no-sync python -m autoresearch.scan.temperature show <date>
"""
from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd

from autoresearch.data.contracts import DataContractError

# 数据落盘根(测试/Task 5 消费端以 monkeypatch 改向)。读写一律在函数体内读"现值"
# (`CSV_PATH` 或 `temperature.CSV_PATH`),不要把它绑进函数默认参数——default 在函数定义时
# 就绑定求值,monkeypatch 模块属性后不会再生效,会悄悄读到改前的旧值。
CSV_PATH = Path("context/learning/temperature.csv")

_COLS = ["date", "n_limit_up", "n_limit_down", "n_fried", "max_streak",
         "promote_rate", "fried_rate", "yesterday_premium", "score", "phase"]

_LU_COLS = ["ts_code", "limit", "limit_times"]


# ───────────────────────── 纯函数:五序列 → 评分 → 相位 ─────────────────────────


def daily_metrics(lu: pd.DataFrame, prev_lu: pd.DataFrame | None,
                   today_pct: pd.DataFrame | None) -> dict:
    """今日涨跌停明细(+ 昨日明细 + 今日全市场涨跌幅)→ 五序列原始指标(presence-gated)。

    lu/prev_lu 列:ts_code, limit(U/D/Z), limit_times;today_pct 列:ts_code, pct_chg。
    prev_lu 缺(None/空)→ promote_rate/yesterday_premium 降级 None;today_pct 缺 →
    yesterday_premium 降级 None。其余四键只依赖 `lu`,恒计算(`lu` 应总是被调用方保证
    有效——真正的"取数失败"由调用方在调用本函数前就跳过该日,见 `rollup`)。
    """
    if lu is None:
        lu = pd.DataFrame(columns=_LU_COLS)
    up = lu[lu["limit"] == "U"]
    down = lu[lu["limit"] == "D"]
    fried = lu[lu["limit"] == "Z"]
    n_limit_up, n_limit_down, n_fried = len(up), len(down), len(fried)

    streak = pd.to_numeric(up["limit_times"], errors="coerce").dropna()
    max_streak = int(streak.max()) if len(streak) else None

    denom_fried = n_limit_up + n_fried
    fried_rate = (n_fried / denom_fried) if denom_fried > 0 else None

    promote_rate = None
    yesterday_premium = None
    if prev_lu is not None and len(prev_lu):
        prev_up = prev_lu[prev_lu["limit"] == "U"]
        prev_streak = pd.to_numeric(prev_up["limit_times"], errors="coerce")
        first_board = prev_up.loc[prev_streak == 1, "ts_code"]
        if len(first_board):
            today_streak = pd.to_numeric(up["limit_times"], errors="coerce")
            second_today = set(up.loc[today_streak == 2, "ts_code"])
            promote_rate = float(first_board.isin(second_today).sum()) / len(first_board)
        if today_pct is not None and len(today_pct):
            prev_up_codes = set(prev_up["ts_code"])
            sub = today_pct[today_pct["ts_code"].isin(prev_up_codes)]
            if len(sub):
                yesterday_premium = float(pd.to_numeric(sub["pct_chg"], errors="coerce").mean())

    return {
        "n_limit_up": n_limit_up,
        "n_limit_down": n_limit_down,
        "n_fried": n_fried,
        "max_streak": max_streak,
        "promote_rate": promote_rate,
        "fried_rate": fried_rate,
        "yesterday_premium": yesterday_premium,
    }


def _norm(v, lo: float, hi: float) -> float | None:
    """线性归一到 [0,1](v 缺/NaN → None;越界截断到 [0,1])。"""
    if v is None or v != v:
        return None
    return max(0.0, min(1.0, (float(v) - lo) / (hi - lo)))


def score(m: dict) -> float | None:
    """五序列中的 4 键(promote_rate 暂不入)→ 0-100 情绪温度;任一核心键缺 → None。"""
    fr_norm = _norm(m.get("fried_rate"), 0.0, 0.5)
    parts = [
        (_norm(m.get("n_limit_up"), 10, 150), 0.40),
        (_norm(m.get("max_streak"), 1, 8), 0.20),
        (None if fr_norm is None else 1 - fr_norm, 0.20),
        (_norm(m.get("yesterday_premium"), -3.0, 5.0), 0.20),
    ]
    if any(p is None for p, _ in parts):
        return None
    return round(100 * sum(p * w for p, w in parts), 1)


def phase(s: float | None, prev_s: float | None, prev_phase: str | None) -> str:
    """五相位:冰点(<20)/修复/发酵/高潮(≥65)/退潮,±3 分滞回(带内小幅回落不切)。

    s 缺 → 沿用 prev_phase(无历史则 "未知");中间两带(20-40、40-65)按涨跌方向
    (`rising`)定名——上行入场用"修复/发酵",下行离场统一记"退潮"。
    """
    if s is None:
        return prev_phase or "未知"
    if prev_s is not None and abs(s - prev_s) < 3 and prev_phase:
        return prev_phase
    rising = prev_s is None or s >= prev_s
    if s < 20:
        return "冰点"
    if s >= 65:
        return "高潮"
    if s >= 40:
        return "发酵" if rising else "退潮"
    return "修复" if rising else "退潮"


# ───────────────────────── I/O:CSV 幂等读写 ─────────────────────────


def _load(path: Path) -> pd.DataFrame:
    if not path.exists():
        return pd.DataFrame(columns=_COLS)
    try:
        # float_precision="round_trip":pandas 默认 C 解析器对满精度(~17位)浮点小数
        # 不保证 bit-exact 回读(实测最后 1-2 位漂移)——部分区间 rollup 会把"读回的旧行"
        # 和"新算的行"一起整表重写,默认解析器会让未改动的历史行悄悄漂移小数位;
        # round_trip 解析器慢但精确回读,避免这种"读了就变"的假幂等。
        return pd.read_csv(path, dtype={"date": str}, float_precision="round_trip")
    except Exception as exc:  # noqa: BLE001 — 损坏文件不炸,当空表(下次 rollup 会重建)
        print(f"[temperature] {path} 读取失败({exc!r})→ 当空表处理", file=sys.stderr)
        return pd.DataFrame(columns=_COLS)


def _write(df: pd.DataFrame, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(path, index=False)


def _upsert(existing: pd.DataFrame, new: pd.DataFrame) -> pd.DataFrame:
    """按 date 幂等合并(new 命中的日期整行覆盖旧值;新日期追加;段外历史不受影响)。"""
    if not len(new):
        return existing
    if len(existing):
        existing = existing[~existing["date"].isin(new["date"])]
        out = pd.concat([existing, new], ignore_index=True)
    else:
        out = new
    return out.sort_values("date", kind="stable").reset_index(drop=True)


def _iso(d: str) -> str:
    """'20260105' → '2026-01-05'。"""
    return f"{d[:4]}-{d[4:6]}-{d[6:]}"


def _seed_phase_state(existing: pd.DataFrame, seed_iso: str | None) -> tuple[float | None, str | None]:
    """已持久化 CSV 里种子日的 (score, phase),供 rollup 接续滞回(缺 → (None, None))。"""
    if not seed_iso or not len(existing):
        return None, None
    row = existing[existing["date"] == seed_iso]
    if not len(row):
        return None, None
    sc, ph = row["score"].iloc[0], row["phase"].iloc[0]
    return (float(sc) if pd.notna(sc) else None, str(ph) if pd.notna(ph) else None)


# ───────────────────────── rollup:逐日 fetch → 指标 → 落盘 ─────────────────────────


def rollup(start: str, end: str, path: Path | str | None = None) -> pd.DataFrame:
    """[start, end] 闭区间逐交易日 fetch → daily_metrics → score → phase,增量幂等写 CSV。

    - trade_cal 多带 1 个 `start` 前的交易日做 `prev_lu` 种子(让 `start` 当日也能算
      promote_rate/yesterday_premium);phase 滞回状态优先接续**已持久化 CSV** 里种子日的
      score/phase(冷启动/无历史 → None,退化为按阈值直接分类)——这样 prelude 逐日增量
      调用才能真正接上滞回,不会每次重跑都从"未知"重新起跳。
    - 单日取数失败(权限/限频/网络)或空返回 → print warn 并跳过该日(不中断整段回填);
      跳过日之后 `prev_lu` 断链(下一有效日 promote_rate/yesterday_premium 诚实降级
      None),但 phase 沿用最近一次成功日的状态(hysteresis 不因单日缺数据被打断)。
    - 全部日期都失败(如 limit_list_d 无权限)→ 打印明确降级信息;CSV 仍会被写出
      (哪怕是空表)——"存在即取过"惯例,presence-gated 下游(Task 5)全静默。
    - 按 `date` 幂等 upsert:重跑同段覆盖同日行,不重复追加;段外既有历史不受影响。

    返回:本次 [start, end] 新计算的逐日 metrics+score+phase 帧(非整份历史;完整历史见
    `CSV_PATH` 或 CLI `show`)。
    """
    out_path = Path(path) if path is not None else CSV_PATH
    existing = _load(out_path)
    s, e = start.replace("-", ""), end.replace("-", "")

    from autoresearch.data.cache import get_or_fetch
    from autoresearch.data.tushare_source import _pro, _trade_days, fetch_limit_list_d

    cal: list[str] = []
    try:
        pro = _pro()
        cal_start = (pd.Timestamp(s) - pd.Timedelta(days=15)).strftime("%Y%m%d")
        cal = _trade_days(pro, cal_start, e)
    except Exception as exc:  # noqa: BLE001 — 无 token/网络 → 全段降级,不炸
        print(f"[temperature] 交易日历取数失败({exc!r})→ 空回填(presence-gated)", file=sys.stderr)

    before = [d for d in cal if d < s]
    window = [d for d in cal if s <= d <= e]

    new_rows: list[dict] = []
    if not window:
        print(f"[temperature] {start}~{end} 无交易日或日历不可用 → 空回填", file=sys.stderr)
    else:
        seed_date = before[-1] if before else None
        prev_lu = None
        if seed_date is not None:
            try:
                seed_lu = fetch_limit_list_d(seed_date)
                prev_lu = seed_lu if seed_lu is not None and len(seed_lu) else None
            except Exception as exc:  # noqa: BLE001
                print(f"[temperature] 种子日 {seed_date} 取数失败({exc!r})→ "
                      f"{_iso(window[0])} 的 promote/premium 降级", file=sys.stderr)

        prev_score, prev_phase = _seed_phase_state(
            existing, _iso(seed_date) if seed_date else None)

        for d in window:
            try:
                lu = fetch_limit_list_d(d)
                pct = get_or_fetch("daily", {"trade_date": d, "fields": "ts_code,pct_chg"})
            except DataContractError:
                # **契约违约必须炸穿**:本函数读的 `daily` 是 A 级地基,它坏了不是"温度计少一天"
                # 的问题 —— 是**湖里有毒源**,所有下游(volprice 组/回放/扫描)都会中招。吞掉它
                # 只会让整条链继续静默跑残缺(这正是 2026-07-12 事故的形态,且温度计自己就是
                # 那次窄表毒化的始作俑者)。让它上抛 → doctor --purge → 重拉。
                raise
            except Exception as exc:  # noqa: BLE001 — 限频/权限/网络 → 跳过该日(相位降级,可接受)
                print(f"[temperature] {d} 取数失败({exc!r})→ 跳过", file=sys.stderr)
                prev_lu = None
                continue
            if lu is None or not len(lu):
                print(f"[temperature] {d} 无涨跌停数据(空返回)→ 跳过", file=sys.stderr)
                prev_lu = None
                continue
            m = daily_metrics(lu, prev_lu, pct)
            sc = score(m)
            ph = phase(sc, prev_score, prev_phase)
            new_rows.append({"date": _iso(d), **m, "score": sc, "phase": ph})
            prev_lu, prev_score, prev_phase = lu, sc, ph

        if not new_rows:
            print("[temperature] 全部交易日取数失败 → 空回填(presence-gated,下游全静默)",
                  file=sys.stderr)

    new_df = pd.DataFrame(new_rows, columns=_COLS)
    merged = _upsert(existing, new_df)
    _write(merged, out_path)
    return new_df


def show(date: str, path: Path | str | None = None) -> dict | None:
    """读已回填 CSV 里某一天的 metrics+score+phase(缺该日/无 csv → None)。"""
    df = _load(Path(path) if path is not None else CSV_PATH)
    if not len(df):
        return None
    row = df[df["date"] == date]
    if not len(row):
        return None
    rec = row.iloc[0].to_dict()
    return {k: (None if isinstance(v, float) and v != v else v) for k, v in rec.items()}


# ───────────────────────── CLI ─────────────────────────


def main(argv: list[str] | None = None) -> int:
    import argparse
    from datetime import date as _date

    ap = argparse.ArgumentParser(description="S1 情绪温度计:limit_list_d 五序列/评分/五相位(零 LLM)")
    sub = ap.add_subparsers(dest="cmd", required=True)
    bp = sub.add_parser("backfill", help="回填 [start, end] 交易日(end 缺省=今天)")
    bp.add_argument("start")
    bp.add_argument("end", nargs="?", default=None)
    sp = sub.add_parser("show", help="打印某日 metrics/score/phase(读已回填 CSV)")
    sp.add_argument("date")
    args = ap.parse_args(argv)

    if args.cmd == "backfill":
        end = args.end or _date.today().strftime("%Y-%m-%d")
        out = rollup(args.start, end)
        print(f"[temperature] 回填 {args.start}~{end}:新写入 {len(out)} 行 → {CSV_PATH}")
        return 0

    row = show(args.date)
    if row is None:
        print(f"[temperature] {args.date} 无数据(未回填,或该日取数失败/presence-gated)")
        return 0
    print(f"[temperature] {args.date}: " + " ".join(f"{k}={v}" for k, v in row.items()))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
