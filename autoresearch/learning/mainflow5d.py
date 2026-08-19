#!/usr/bin/env python3
"""EXP-1「OW 主力真在门:5 日持续性」影子数据腿 + 两条影子实验的日级观测起账(Wave12-T20)。

## 病灶(FN-1 家族)

2026-08-01 预注册了两条影子实验来回答「漏斗到底有没有漏掉肉」:

* **EXP-1** `exp_20260801_ow_gate_mainflow5d` —— 主力资金 5 日持续性作为 OW 门的 challenger;
* **EXP-2** `exp_20260801_recall_sector_momentum` —— 板块动量影子召回通道。

两条的 `observations` 至今 `[]`,不是因为数据不够,而是**从来没有代码去算它**:
`gate_attribution.py` 里没有任何 5 日窗逻辑,`recall/channels.py` 里没有 `sector_momentum`。
2026-08-07 被删的 `wave10_experiments.py` 只是**注册脚本**——它写完 registry 就没有第二个职责。
消费者在等一个没人生产的产物。本模块把线接上,让观测开始进账。

## 职责边界

* **EXP-1 在这里算**:PIT 五交易日窗 loader + spec 逐字的 challenger 判据 + 逐行 shadow
  verdict 落 `context/learning/exp1_mainflow5d.jsonl`。
* **EXP-2 在这里读**:通道本体在 `scan/recall/channels.py::sector_momentum`,长表由
  `scan/universe.write_shadow_variants` 落 `shadow/L1_channels_plus_sectormom.csv`;本模块
  只把「今天这路召回了几只、其中几只是它独有的」记成一条观测。裁决仍归
  `channel_audit --variant plus_sectormom`(与 accumulation 2026-07-11 退役同一套
  `unique_excess_t2`),本模块不裁决。

`observe_day()` 是**唯一**的日级入口,两条实验各 append 一条观测 —— 这也是本 task 的
「会变的量」:自动/影子的腿必须有一个会变的量做断言,否则它死了也像活着(2026-07-16
权重自动重标定连续 4 次 NO-OP、空转两周无人察觉的判例)。

## challenger 判据(spec 原文,逐字实现,不许"优化")

    sum(main_net_yi, T-4..T) > 0 ∧ positive_days >= 3 ∧ main_distortion == false

## PIT 纪律

* 区间严格是**五个交易日**,不是自然日 —— 交易日由 moneyflow 湖分区文件名决定(与
  `paper_nav.trade_days` / `ruler_compare.lake_days` 同姿势,不查交易日历、不猜)。
* **缺任一日 → 该票 `UNMEASURED`**;`assemble` **不得**联网补(Wave10 Gate0 裁决表原话)。
  本模块只读 `context/lake/moneyflow/*.parquet` 与当日 `L1_scored_full.csv`,没有任何取数
  调用点 —— 「不联网」是结构性的,不是靠自觉。
* 判据要三个输入,少任何一个都记 `UNMEASURED`,不许把缺失的 `main_distortion` 当 False 放行。

## 读数标尺

verdict 行显式带 `ruler`(= `common.ruler.MAIN_RULER`)。本模块自己**不算收益**——前向收益
的归属在 `gate_participation_v3.csv` 的 `excess_2`/`outcome` 列里,本模块只对那份人口逐行
落 challenger 判定,配对由 `(date, code)` 完成。

  uv run --no-sync python -m autoresearch.learning.mainflow5d <date>
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import pandas as pd

from autoresearch.common import workspace as ws
from autoresearch.common.ruler import MAIN_RULER
from autoresearch.learning import experiment_registry as registry

EXP1_ID = "exp_20260801_ow_gate_mainflow5d"
EXP2_ID = "exp_20260801_recall_sector_momentum"

WINDOW_DAYS = 5                       # 「区间严格为**五个交易日**,非自然日」(spec 原文)
MIN_POSITIVE_DAYS = 3                 # positive_days >= 3
GATE_NAME = "主力真在"                 # 人口 = gate_participation_v3.csv 里这道门的行

DEFAULT_LAKE = ws.lake_root() / "moneyflow"
DEFAULT_SCAN_ROOT = ws.scan_root()
DEFAULT_POPULATION = ws.context_root() / "learning/gate_participation_v3.csv"
DEFAULT_LEDGER = ws.context_root() / "learning/exp1_mainflow5d.jsonl"
SHADOW_VARIANT = "plus_sectormom"
SHADOW_CHANNEL = "sector_momentum"

STATUS_MEASURED = "MEASURED"
STATUS_UNMEASURED = "UNMEASURED"


# ── PIT 五交易日窗 ──────────────────────────────────────────────────────────


def _code6(value: object) -> str:
    return str(value or "").strip().split(".")[0].zfill(6)


def lake_days(lake: Path | str | None = None) -> list[str]:
    """湖分区文件名(YYYYMMDD)升序 —— 交易日的唯一来源,不查日历、不猜周末。"""
    root = Path(lake or DEFAULT_LAKE)
    if not root.exists():
        return []
    return sorted(p.stem for p in root.glob("*.parquet")
                  if len(p.stem) == 8 and p.stem.isdigit())


def window_days(date: str, lake: Path | str | None = None) -> list[str]:
    """T-4..T 的**五个交易日**(含 T);湖里不足 5 个 → `[]`(不拿 3 天当 5 天用)。

    `date` 可以是 `2026-08-05` 或 `20260805`。T 本身不在湖里(盘后还没落)→ 同样 `[]`:
    窗口的右端点必须真是分析日,否则量的是另一段时间。
    """
    key = str(date).replace("-", "")
    days = [d for d in lake_days(lake) if d <= key]
    if not days or days[-1] != key or len(days) < WINDOW_DAYS:
        return []
    return days[-WINDOW_DAYS:]


def _main_net_yi(day: str, lake: Path) -> dict[str, float] | None:
    """一个湖分区 → {code6: main_net_yi};读不了 → `None`(与「文件不在」同义)。"""
    path = Path(lake) / f"{day}.parquet"
    if not path.exists():
        return None
    try:
        raw = pd.read_parquet(path)
    except Exception:  # noqa: BLE001 — 坏分区按缺日处理,不炸整条腿
        return None
    from autoresearch.data.tushare_source import _moneyflow_struct_cols
    struct = _moneyflow_struct_cols(raw)
    return {_code6(c): float(v) for c, v in
            zip(struct["code"], struct["main_net_yi"], strict=True) if v == v}


def load_window(date: str, codes, lake: Path | str | None = None) -> dict[str, dict]:
    """{code6: {status, values, missing_days, window}} —— **缺任一日 → UNMEASURED**。

    零联网:只读 `lake` 下已有的 parquet。窗口不足 5 个交易日 → 全体 UNMEASURED(此时
    `window=[]`、`missing_days=[]`,`reason` 说明是历史不足而不是个股缺行)。
    """
    lake = Path(lake or DEFAULT_LAKE)
    want = [_code6(c) for c in codes]
    window = window_days(date, lake)
    if len(window) < WINDOW_DAYS:
        return {code: {"status": STATUS_UNMEASURED, "values": [], "missing_days": [],
                       "window": [], "reason": "lake_window_short"} for code in want}

    per_day = {day: (_main_net_yi(day, lake) or {}) for day in window}
    out: dict[str, dict] = {}
    for code in want:
        values, missing = [], []
        for day in window:
            value = per_day[day].get(code)
            if value is None:
                missing.append(day)
            else:
                values.append(float(value))
        out[code] = {
            "status": STATUS_UNMEASURED if missing else STATUS_MEASURED,
            "values": values,
            "missing_days": missing,
            "window": list(window),
            "reason": "missing_day" if missing else None,
        }
    return out


# ── challenger 判据(spec 逐字) ────────────────────────────────────────────


def positive_days(values) -> int:
    """严格为正的日数。0 不算正 —— 边界写死,免得下一个人"顺手"改成 `>= 0`。"""
    return sum(1 for v in values if float(v) > 0.0)


def challenger_pass(values, *, main_distortion: bool) -> bool:
    """`sum > 0 ∧ positive_days >= 3 ∧ ¬main_distortion`(spec `definition.challenger` 原文)。"""
    vals = [float(v) for v in values]
    return (sum(vals) > 0.0
            and positive_days(vals) >= MIN_POSITIVE_DAYS
            and not bool(main_distortion))


def _distortion_index(date: str, scan_root: Path | str | None = None) -> dict[str, bool]:
    """当日 `L1_scored_full.csv` → {code6: main_distortion}。

    口径走 `scoring.main_net_distortion_label` **单一事实源**(L3 表 `main_dist` 列 = L4 简报
    标注 = 这里,同一个谓词),不另造第二套判型。读的是**当日生产帧**本身,所以它天然 PIT:
    那正是当日那道门看到的数。读不到该票 → 不进索引(调用方据此记 UNMEASURED,不按 False 放行)。
    """
    from autoresearch.common.scoring import main_net_distortion_label

    path = Path(scan_root or DEFAULT_SCAN_ROOT) / str(date) / "L1_scored_full.csv"
    if not path.exists():
        return {}
    try:
        frame = pd.read_csv(path, dtype={"code": str})
    except Exception:  # noqa: BLE001
        return {}
    if not {"main_net_ratio", "main_inflow_yi"}.issubset(frame.columns):
        return {}
    out: dict[str, bool] = {}
    for row in frame.itertuples(index=False):
        label = main_net_distortion_label(getattr(row, "main_net_ratio", None),
                                          getattr(row, "main_inflow_yi", None))
        out[_code6(row.code)] = bool(label)
    return out


def population_dates(population_path: Path | str | None = None) -> list[str]:
    """人口文件里出现过 `gate=='主力真在'` 的全部日期(升序去重)。

    `observe_pending` 用它决定"哪些日子该有一条 EXP-1 观测"。人口文件由
    `gate_attribution` 在夜间刷新,所以这张表会**随时间增长** —— 这正是本腿"会变的量"
    的源头。
    """
    path = Path(population_path or DEFAULT_POPULATION)
    if not path.exists():
        return []
    try:
        frame = pd.read_csv(path, dtype={"code": str})
    except Exception:  # noqa: BLE001
        return []
    if not {"date", "gate"}.issubset(frame.columns):
        return []
    hit = frame[frame["gate"].astype(str) == GATE_NAME]
    return sorted({str(d) for d in hit["date"] if str(d).strip()})


def shadow_dates(scan_root: Path | str | None = None) -> list[str]:
    """落了 `shadow/L1_channels_plus_sectormom.csv` 的扫描日(EXP-2 的观测日)。

    EXP-2 的**长表**腿本来就是自动的(每次真扫描 `write_shadow_variants` 都跑),断的只是
    registry 的观测计数。所以待观测日要取"人口日 ∪ 影子长表日"的并集:某天门一个都没否
    (EXP-1 人口为 0)不代表 EXP-2 那天没东西可记。
    """
    root = Path(scan_root or DEFAULT_SCAN_ROOT)
    if not root.exists():
        return []
    return sorted(
        d.name for d in root.iterdir()
        if d.is_dir() and d.name[:2] == "20"
        and (d / "shadow" / f"L1_channels_{SHADOW_VARIANT}.csv").exists())


def observed_dates(registry_path: Path | str | None = None,
                   experiment_id: str = EXP1_ID) -> set[str]:
    """registry 里已记过观测的日期键。读不到该实验 → 空集(不炸)。"""
    try:
        record = registry.get_experiment(
            Path(registry_path or registry.DEFAULT_REGISTRY), experiment_id)
    except registry.RegistryError:
        return set()
    return {str(o.get("key")) for o in (record.get("observations") or [])
            if o.get("kind") == registry.SHADOW_OBSERVATION_KIND}


def observe_pending(today: str | None = None, *,
                    population_path: Path | str | None = None,
                    scan_root: Path | str | None = None,
                    lake: Path | str | None = None,
                    registry_path: Path | str | None = None,
                    ledger_path: Path | str | None = None,
                    limit: int | None = None) -> dict:
    """**尚未观测的日子 → 逐日 `observe_day`**(夜间腿的真身,Wave12-T20 修复轮 1)。

    ## 为什么需要这个函数(复核 C1)

    首版只写了 `observe_day` 和一个手工 CLI,**零自动调用点** —— 于是那 20 条观测是一次性
    回填、**永远不会增长**。更糟:回填顺手抹掉了 `observations == []` 这个信号,而它正是当初
    暴露「EXP-1/EXP-2 预注册后数据腿从没实现」的唯一线索。registry 看起来有 20 条观测、
    一切正常,实际腿仍然没在跑 —— **比它要修的原病更难被发现**。本仓库家训:自动腿必须有
    一个会变的量做断言,否则它死了也像活着。

    ## 语义

    待观测日 = (人口日 ∪ 影子长表日) − 已观测日,再按 `today` 截断(不观测未来)。
    **幂等 + 自愈**:漏跑一晚,第二晚自动把欠的补上(同 `_retro_refresh` 的姿势);
    重复跑不产生第二条(`append_observation` 按 `(kind, key)` 去重)。

    单日失败**不连坐**其余日(逐日 try:某天的湖分区坏了不该让整条腿停摆),失败日计入
    `failed` 如实返回。
    """
    reg = Path(registry_path or registry.DEFAULT_REGISTRY)
    wanted = set(population_dates(population_path)) | set(shadow_dates(scan_root))
    if today:
        wanted = {d for d in wanted if d <= str(today)}
    todo = sorted(wanted - observed_dates(reg))
    if limit is not None:
        todo = todo[:limit]

    done, failed = [], []
    for date in todo:
        try:
            observe_day(date, population_path=population_path, scan_root=scan_root,
                        lake=lake, registry_path=reg, ledger_path=ledger_path)
            done.append(date)
        except Exception as exc:  # noqa: BLE001 — 单日失败不连坐,如实记账
            failed.append({"date": date, "error": f"{type(exc).__name__}: {exc}"})
    return {"pending": len(todo), "observed": done, "failed": failed,
            "n_observations": len(observed_dates(reg))}


def population(date: str, population_path: Path | str | None = None) -> list[str]:
    """人口 = `gate_participation_v3.csv` 中当日 `gate == '主力真在'` 的票(spec 原文:
    「即该门参与否决过的票;attribution 口径样本不足以支撑本实验」)。去重、升序。"""
    path = Path(population_path or DEFAULT_POPULATION)
    if not path.exists():
        return []
    try:
        frame = pd.read_csv(path, dtype={"code": str})
    except Exception:  # noqa: BLE001
        return []
    if not {"date", "gate", "code"}.issubset(frame.columns):
        return []
    hit = frame[(frame["date"].astype(str) == str(date))
                & (frame["gate"].astype(str) == GATE_NAME)]
    return sorted({_code6(c) for c in hit["code"]})


def verdict_rows(date: str, *, population_path: Path | str | None = None,
                 scan_root: Path | str | None = None,
                 lake: Path | str | None = None) -> list[dict]:
    """对当日人口逐行落 shadow verdict(确定性、零 LLM、零联网)。"""
    codes = population(date, population_path)
    if not codes:
        return []
    windows = load_window(date, codes, lake)
    distortion = _distortion_index(date, scan_root)

    rows: list[dict] = []
    for code in codes:
        info = windows[code]
        flag = distortion.get(code)
        if info["status"] != STATUS_MEASURED:
            status, reason, verdict = STATUS_UNMEASURED, info["reason"], None
        elif flag is None:
            # 判据的第三个输入读不到 → 不许猜。把缺失的 main_distortion 当 False 会让
            # challenger 单方面变宽松,直接污染 false_kill_rate_delta_pp(primary metric)。
            status, reason, verdict = STATUS_UNMEASURED, "missing_main_distortion", None
        else:
            status, reason = STATUS_MEASURED, None
            verdict = challenger_pass(info["values"], main_distortion=flag)
        rows.append({
            "experiment_id": EXP1_ID,
            "date": str(date),
            "code": code,
            "challenger_pass": verdict,
            "status": status,
            "reason": reason,
            "window": info["window"],
            "sum_main_net_yi": (round(sum(info["values"]), 6)
                                if status == STATUS_MEASURED else None),
            "positive_days": (positive_days(info["values"])
                              if status == STATUS_MEASURED else None),
            "main_distortion": flag,
            "missing_days": info["missing_days"],
            "ruler": MAIN_RULER,
        })
    return rows


def _append_jsonl(path: Path | str, rows: list[dict]) -> int:
    """逐行追加(幂等:同 `(date, code)` 已在账本里就不再写)。返回真正新增的行数。"""
    target = Path(path)
    seen: set[tuple[str, str]] = set()
    if target.exists():
        for line in target.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                old = json.loads(line)
            except json.JSONDecodeError:
                continue
            seen.add((str(old.get("date")), str(old.get("code"))))
    fresh = [r for r in rows if (r["date"], r["code"]) not in seen]
    if not fresh:
        return 0
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("a", encoding="utf-8") as handle:
        for row in fresh:
            handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")
    return len(fresh)


# ── EXP-2:影子召回长表的日级读数 ───────────────────────────────────────────


def sector_momentum_facts(date: str, scan_root: Path | str | None = None) -> dict:
    """`shadow/L1_channels_plus_sectormom.csv` → 一条观测的 facts。

    长表缺席 → `source="ABSENT"` + 计数全 `None`(**不是 0**)。「今天没跑」和「跑了但一只
    都没召回」必须区分得开,否则又是一条「死了也像活着」的腿。
    """
    path = (Path(scan_root or DEFAULT_SCAN_ROOT) / str(date) / "shadow"
            / f"L1_channels_{SHADOW_VARIANT}.csv")
    facts = {"variant": SHADOW_VARIANT, "channel": SHADOW_CHANNEL, "date": str(date),
             "source": "ABSENT", "n_recalled": None, "n_unique": None,
             "n_channels_in_variant": None}
    if not path.exists():
        return facts
    try:
        frame = pd.read_csv(path, dtype={"code": str})
    except Exception:  # noqa: BLE001
        return facts
    if not {"channel", "code"}.issubset(frame.columns) or not len(frame):
        return facts
    frame["code"] = frame["code"].map(_code6)
    mine = frame[frame["channel"] == SHADOW_CHANNEL]
    n_by_code = frame.groupby("code")["channel"].nunique()
    facts.update({
        "source": "PRESENT",
        "n_recalled": int(len(mine)),
        # 独占 = 当日只有本路召回它 —— 与 `channel_audit.day_channel_stats` 的 `n_unique`
        # 同一口径(从长表自身的 channel×code 计数推,不依赖 recall_channels provenance)。
        "n_unique": int(sum(1 for c in mine["code"] if n_by_code.get(c, 0) == 1)),
        "n_channels_in_variant": int(frame["channel"].nunique()),
    })
    return facts


# ── 日级入口:两条实验各 +1 条观测 ──────────────────────────────────────────


def observe_day(date: str, *, population_path: Path | str | None = None,
                scan_root: Path | str | None = None,
                lake: Path | str | None = None,
                registry_path: Path | str | None = None,
                ledger_path: Path | str | None = None,
                observed_at: str | None = None) -> dict:
    """跑一天 → EXP-1 verdict 落账本 + 两条实验各 append **一条**观测。

    幂等:同一 `date` 重复跑不产生第二条观测(`registry.append_observation` 按
    `(kind, key=date)` 去重),账本按 `(date, code)` 去重。
    """
    reg = Path(registry_path or registry.DEFAULT_REGISTRY)
    rows = verdict_rows(date, population_path=population_path,
                        scan_root=scan_root, lake=lake)
    appended = _append_jsonl(ledger_path or DEFAULT_LEDGER, rows)

    measured = [r for r in rows if r["status"] == STATUS_MEASURED]
    exp1_facts = {
        "date": str(date),
        "population": f"gate_participation_v3.csv · gate=={GATE_NAME}",
        "n_population": len(rows),
        "n_measured": len(measured),
        "n_unmeasured": len(rows) - len(measured),
        "n_challenger_pass": sum(1 for r in measured if r["challenger_pass"]),
        "n_challenger_reject": sum(1 for r in measured if r["challenger_pass"] is False),
        "window_days": WINDOW_DAYS,
        "rule": "sum(main_net_yi, T-4..T) > 0 ∧ positive_days >= 3 ∧ main_distortion == false",
        "ruler": MAIN_RULER,
        # ⚠️ `ledger_rows_appended` **故意不进 facts**:它是"这次跑动写了几行"这个**运行时**
        # 的量(重跑同一天=0),不是"这一天观测到了什么"。放进 facts 会让同一天重跑的
        # facts hash 变化 → `append_observation` 的幂等门把它判成"同 key 不同 facts"直接
        # 抛错。第一版就是这么写的,被 `test_observation_is_idempotent_per_date` 当场逮住。
    }
    exp2_facts = sector_momentum_facts(date, scan_root)
    exp2_facts["ruler"] = MAIN_RULER

    out = {}
    for exp_id, facts in ((EXP1_ID, exp1_facts), (EXP2_ID, exp2_facts)):
        out[exp_id] = registry.append_observation(reg, exp_id, facts=facts,
                                                  key=str(date), observed_at=observed_at)
    return {"date": str(date), "rows": len(rows), "ledger_rows_appended": appended,
            "observations": out}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        prog="python -m autoresearch.learning.mainflow5d",
        description="EXP-1 5日主力持续性影子 verdict + EXP-1/EXP-2 日级观测起账")
    ap.add_argument("date", help="分析日 YYYY-MM-DD")
    ap.add_argument("--registry", default=None)
    ap.add_argument("--ledger", default=None)
    ap.add_argument("--scan-root", default=None)
    ap.add_argument("--population", default=None)
    ap.add_argument("--lake", default=None)
    args = ap.parse_args(argv)
    try:
        got = observe_day(args.date, population_path=args.population,
                          scan_root=args.scan_root, lake=args.lake,
                          registry_path=args.registry, ledger_path=args.ledger)
    except registry.RegistryError as exc:
        print(json.dumps({"error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 2
    print(json.dumps({"date": got["date"], "rows": got["rows"],
                      "ledger_rows_appended": got["ledger_rows_appended"],
                      "experiments": sorted(got["observations"])},
                     ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
