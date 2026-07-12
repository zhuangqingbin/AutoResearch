#!/usr/bin/env python3
"""留一日回放裁决器 —— shrinkage 基率(P0-3)的裁决法:零 token,当天可跑。

design: docs/specs/2026-07-12-selflearning-optimization-brainstorm.md §4 P0-3 裁决法。

方法论(留一日回放/walk-forward):对 attribution 覆盖的每个真实 scan 日 t(按时间排序),
只用 t **之前**(不含 t)的历史数据分别按"原始桶值(raw)"和"收缩估计(shrunk)"算出对第 t
日的预测,再跟第 t 日**当天自己**实际发生的桶级真实值比较,累计 MAE(平均绝对误差)。
三条轨道对应四消费点里"率"性质的三个(第四个 write_base_rates 本身复用 flip_stats +
自己的 by_rating 收缩,没有独立的"当日实际"可比,不单列轨道):

  ① 翻案率(flip_rate)—— 桶=lane,ground truth=当日该 lane 高确信(conviction≥70)行
     被 L4 判 ≤Underweight/Sell 的占比(定义同 `cross_calib.flip_stats`)。
  ② 触达率(touch8_rate)—— 桶=regime,ground truth=当日 attribution `hi_2_oc`≥8% 占比
     (定义同 `buy_ledger.hi2_calibration`)。
  ③ 左尾率(tail_rate)—— 桶=gate check,ground truth=当日被拦票 `fwd_2_oc`≤-5% 占比
     (定义同 `gate_ledger.roll`)。

只统计"当日该桶确有观测"的 (day, bucket) 对。raw 需要历史桶 n>=1 才有定义;shrunk 恒有
定义(历史桶零观测时退化回全局池化值)——为公平比较 MAE,只在"raw 与 shrunk 都有定义"的
样本上计 MAE;冷启动样本(历史该桶零观测,只有 shrunk 能报)单独计数,不掺进 MAE(不为
拉低 shrunk 的 MAE 而灌水)。

  uv run --no-sync python -m autoresearch.learning.shrink_replay   # → reports/learning/shrink_replay.md
"""
from __future__ import annotations

import json
import statistics
from pathlib import Path

import pandas as pd

from autoresearch.learning.cross_calib import _HICONV, _LOW, _days as _list_days
from autoresearch.learning.shrink import DEFAULT_K, shrink as _shrink_fn

_LEFT_TAIL = -0.05
_TOUCH8 = 0.08


# ───────────────────────── 逐日 loader(单日切片,供回放的"t 之前"历史累积 + "t 当天"真值用) ─────────────────────────


def _day_flip_obs(day_dir: Path) -> pd.DataFrame:
    """单日 `L3_judged_full.csv` × 当日 `final_ratings` → 高确信(conv≥70)行的 [lane, is_flip]。

    与 `cross_calib.flip_stats` 单日切片同定义(无卡行经 `final.notna()` 剔除,不入分母)。
    """
    jp = day_dir / "L3_judged_full.csv"
    if not jp.exists():
        return pd.DataFrame(columns=["lane", "is_flip"])
    try:
        j = pd.read_csv(jp, dtype={"code": str})
    except Exception:  # noqa: BLE001
        return pd.DataFrame(columns=["lane", "is_flip"])
    if "code" not in j.columns or "lane" not in j.columns:
        return pd.DataFrame(columns=["lane", "is_flip"])
    from autoresearch.scan.health import final_ratings  # lazy 防环
    j["code"] = j["code"].astype(str).str.zfill(6)
    ratings = final_ratings(day_dir)
    j["final"] = j["code"].map(ratings)
    j = j[j["final"].notna()]
    conv = pd.to_numeric(j.get("conviction"), errors="coerce")
    hi = j[conv >= _HICONV].copy()
    if not len(hi):
        return pd.DataFrame(columns=["lane", "is_flip"])
    hi["is_flip"] = hi["final"].isin(_LOW).astype(float)
    return hi[["lane", "is_flip"]]


def _day_touch_obs(day_dir: Path) -> pd.DataFrame:
    """单日 `retro/attribution.csv` 的 `hi_2_oc` × 当日 `meta.json` regime → [regime, touch8]。

    与 `buy_ledger.hi2_calibration` 单日切片同定义。regime 缺(无 `meta.json`/无 `regime`
    键)→ 该日无法归桶,返回空表(仍可能贡献 `all` 池,但本回放只测"桶"这一维,不测 all)。
    """
    ap = day_dir / "retro" / "attribution.csv"
    if not ap.exists():
        return pd.DataFrame(columns=["regime", "touch8"])
    try:
        attr = pd.read_csv(ap, dtype={"code": str})
    except Exception:  # noqa: BLE001
        return pd.DataFrame(columns=["regime", "touch8"])
    if "hi_2_oc" not in attr.columns:
        return pd.DataFrame(columns=["regime", "touch8"])
    vals = pd.to_numeric(attr["hi_2_oc"], errors="coerce").dropna()
    if not len(vals):
        return pd.DataFrame(columns=["regime", "touch8"])
    regime = None
    mp = day_dir / "meta.json"
    if mp.exists():
        try:
            regime = json.loads(mp.read_text(encoding="utf-8")).get("regime")
        except Exception:  # noqa: BLE001
            regime = None
    if not regime:
        return pd.DataFrame(columns=["regime", "touch8"])
    return pd.DataFrame({"regime": str(regime), "touch8": (vals >= _TOUCH8).astype(float).to_numpy()})


def _day_tail_obs(day_dir: Path) -> pd.DataFrame:
    """单日 `gate_fires.csv` × `retro/attribution.csv` 的 `fwd_2_oc` → [check, tail]。

    与 `gate_ledger.roll` 单日切片同定义(未成熟票 fwd_2_oc 缺 → 丢弃,不进分母)。
    """
    gf = day_dir / "gate_fires.csv"
    ap = day_dir / "retro" / "attribution.csv"
    if not gf.exists() or not ap.exists():
        return pd.DataFrame(columns=["check", "tail"])
    try:
        fires = pd.read_csv(gf, dtype={"code": str})
        attr = pd.read_csv(ap, dtype={"code": str})
    except Exception:  # noqa: BLE001
        return pd.DataFrame(columns=["check", "tail"])
    fires = fires[fires["code"].astype(str).str.len() > 0]
    if not len(fires) or "fwd_2_oc" not in attr.columns:
        return pd.DataFrame(columns=["check", "tail"])
    fires = fires.copy()
    fires["code"] = fires["code"].astype(str).str.zfill(6)
    attr = attr.copy()
    attr["code"] = attr["code"].astype(str).str.zfill(6)
    j = fires.merge(attr[["code", "fwd_2_oc"]], on="code", how="left")
    j["fwd2"] = pd.to_numeric(j["fwd_2_oc"], errors="coerce")
    j = j.dropna(subset=["fwd2"])
    if not len(j):
        return pd.DataFrame(columns=["check", "tail"])
    j["tail"] = (j["fwd2"] <= _LEFT_TAIL).astype(float)
    return j[["check", "tail"]]


# ───────────────────────── 通用留一日回放引擎 ─────────────────────────


def _replay_track(day_dirs: list[Path], loader, bucket_col: str, obs_col: str,
                  k: float = DEFAULT_K) -> dict:
    """对 `day_dirs`(按时间排序)逐日留一日回放:day[i] 只用 day[0:i] 的历史预测,
    与 day[i] 当天实际比较。返回 `{n_days, n_pairs, n_coldstart, mae_raw, mae_shrunk, detail}`。

    `loader(day_dir)` → 该日 DataFrame,须含 `bucket_col`(分组键)与 `obs_col`(0/1 浮点,
    可直接 `.mean()` 出"率")两列;返回空表 = 该日此轨道无现场,自然跳过。
    """
    history: list[pd.DataFrame] = []
    pairs: list[tuple[str, str, float, float, float]] = []
    coldstart = 0
    n_days_used = 0
    for d in day_dirs:
        today = loader(d)
        non_empty_hist = [h for h in history if len(h)]      # 剔空表,防 pd.concat 的 FutureWarning
        if non_empty_hist and len(today):
            hist = pd.concat(non_empty_hist, ignore_index=True)
            if len(hist):
                n_days_used += 1
                p_global = float(hist[obs_col].mean())
                bucket_hist = hist.groupby(bucket_col)[obs_col].agg(["mean", "count"])
                today_mean = today.groupby(bucket_col)[obs_col].mean()
                today_n = today.groupby(bucket_col)[obs_col].count()
                for bucket, actual in today_mean.items():
                    if int(today_n.get(bucket, 0)) < 1:
                        continue
                    if bucket in bucket_hist.index:
                        p_b = float(bucket_hist.loc[bucket, "mean"])
                        n_b = float(bucket_hist.loc[bucket, "count"])
                        shrunk = _shrink_fn(p_b, n_b, p_global, k)
                        pairs.append((str(d.name), str(bucket), p_b,
                                     float(shrunk) if shrunk is not None else p_b, float(actual)))
                    else:
                        coldstart += 1   # raw 无历史可用(未定义),shrunk 会退化回 p_global——不计入 MAE
        history.append(today)

    if not pairs:
        return {"n_days": n_days_used, "n_pairs": 0, "n_coldstart": coldstart,
                "mae_raw": None, "mae_shrunk": None, "detail": []}
    err_raw = [abs(r - a) for _, _, r, _s, a in pairs]
    err_shrunk = [abs(s - a) for _, _, _r, s, a in pairs]
    return {"n_days": n_days_used, "n_pairs": len(pairs), "n_coldstart": coldstart,
            "mae_raw": round(statistics.fmean(err_raw), 4),
            "mae_shrunk": round(statistics.fmean(err_shrunk), 4),
            "detail": pairs}


def replay_flip(scan_root: Path | str | None = None, k: float = DEFAULT_K) -> dict:
    """翻案率轨道(桶=lane)留一日回放。"""
    scan_root = Path(scan_root or "context/scan")
    days = _list_days(scan_root, 10_000)
    return _replay_track(days, _day_flip_obs, bucket_col="lane", obs_col="is_flip", k=k)


def replay_touch(scan_root: Path | str | None = None, k: float = DEFAULT_K) -> dict:
    """触达率轨道(桶=regime)留一日回放。"""
    scan_root = Path(scan_root or "context/scan")
    days = _list_days(scan_root, 10_000)
    return _replay_track(days, _day_touch_obs, bucket_col="regime", obs_col="touch8", k=k)


def replay_tail(scan_root: Path | str | None = None, k: float = DEFAULT_K) -> dict:
    """左尾率轨道(桶=gate check)留一日回放。"""
    scan_root = Path(scan_root or "context/scan")
    days = _list_days(scan_root, 10_000)
    return _replay_track(days, _day_tail_obs, bucket_col="check", obs_col="tail", k=k)


# ───────────────────────── 结论行 + 报告 ─────────────────────────


def verdict(track: dict, min_pairs: int = 5) -> str:
    """结论行:样本太少不下结论;否则原样比 MAE,shrunk 不优则明确说"按纪律应整体回滚"。"""
    if track["n_pairs"] < min_pairs:
        return f"样本过少((日,桶)对 n={track['n_pairs']}<{min_pairs}),不下结论,继续攒账本"
    mr, ms = track["mae_raw"], track["mae_shrunk"]
    if ms < mr:
        return f"shrunk 更优(MAE shrunk={ms} < raw={mr})"
    if ms > mr:
        return f"raw 更优(MAE raw={mr} < shrunk={ms})——按裁决法纪律,shrunk 不优则整体回滚(config 回滚杆)"
    return f"打平(MAE 相等={mr})"


def render(flip: dict, touch: dict, tail: dict, k: float) -> list[str]:
    out = [f"# shrinkage 基率留一日回放裁决(P0-3 裁决法;k={k})", "",
           "_方法论:day[i] 只用 day[0:i] 的历史(walk-forward)分别出 raw/shrunk 预测,"
           "与 day[i] 当天实际比较,MAE(平均绝对误差)越低越准。冷启动样本(历史该桶零观测,"
           "raw 无定义)不计入 MAE,单独计数,不为拉低 shrunk 的 MAE 灌水。_", ""]
    for name, track in (("① 翻案率(flip_rate,桶=lane)", flip),
                        ("② 触达率(touch8_rate,桶=regime)", touch),
                        ("③ 左尾率(tail_rate,桶=gate check)", tail)):
        out += [f"## {name}", "",
                f"- 可用日数(有历史可回放)={track['n_days']};(日,桶)样本对 n={track['n_pairs']};"
                f"冷启动(仅 shrunk 可算,未计入 MAE)n={track['n_coldstart']}",
                f"- MAE(raw)={track['mae_raw']}  MAE(shrunk)={track['mae_shrunk']}",
                f"- 结论:{verdict(track)}", ""]
    return out


def main() -> int:
    flip = replay_flip()
    touch = replay_touch()
    tail = replay_tail()
    lines = render(flip, touch, tail, DEFAULT_K)
    out = Path("reports/learning/shrink_replay.md")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines))
    print(f"[shrink_replay] → {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
