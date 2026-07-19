#!/usr/bin/env python3
"""漏斗历史回放器 —— 逐日重放确定性漏斗(L0→L1→L2)+ 归因 + 温度相位。零 LLM,离线研究仪器。

design: docs/specs/2026-07-12-funnel-replay-l35-removal-design.md Part B。

**动机**:所有买侧待裁项(通道整编/雷分级/新配额/温度相位联动)都在等前向账本,而一个月只
产 ~20 个样本日;且 06-18 以来的前向窗全是弱市——温度计五相位里"修复/发酵/高潮"从未出现,
"相位切菜单"这一拍板方案至今**零证据**。回放把裁决样本从"每月 20 日"换成"一次 250-500 日",
且覆盖前向窗根本看不到的相位。

**不写第二套漏斗**(FN-1 三连的教训:生产真身 = workflow→prelude→`universe.run` 直调,
"接了 CLI ≠ 生效"):本模块逐日调**同一个** `universe.run(outdir=...)`,归因调
`retro.attribute(scan_root=...)`,通道口径调 `research.channel_audit`(其 `scan_root`
可注入),相位调 `scan.temperature` —— 四处全是既有生产件,本模块只有编排循环 + 聚合。

**边界(诚实局限)**:只回放到 L2 + 相位。L3/L4 是 LLM 判断层,不可回放——回放结论止步于
"谁进了候选池",不主张端到端收益。账本已证病根就在这一段(missed_l1 是 L3/L4 误杀的 5.7 倍)。

## PIT(point-in-time)纪律

1. **权重 PIT(最容易漏的前视偏差)**:`context/factor_lab/weights.json` 是 retro 用**含未来
   前向收益**校准出来的——拿它回放历史 = 用未来的权重预测过去。故回放默认
   `weights='prior'`(内置 `_PRIOR_WEIGHTS`,零校准=零泄漏);`'current'` 有泄漏,只允许用于
   M1 对拍/对照,报告里必标注(`weights_leak` 字段随每份报告落盘)。
2. **端点 PIT**:`fetch_universe_tushare` 只用 daily_basic/daily/moneyflow/yjbb/stock_basic
   (全 EOD、按 trade_date)= 天然 PIT;live-only 端点(spot / 涨停池 `stock_zt_pool_em`)
   不在此路径 —— `_assert_pit_source` 再加一道断言(source 必须 tushare)。
3. **财务 PIT**:`scoring.latest_reported_quarter(analysis_date)` 按当日已披露报告期取。
4. **幸存者偏差**:`stock_basic(list_status='L')` 只含**当前存续**股 → 回放期内已退市的票
   可能拿不到 name/list_date,merge 阶段掉队会系统性高估池子质量(退市票多为跌到底的)——
   缺口异常放大的窗口,其捕获率/超额读数须打折看。
5. **可执行性**:D+1 一字板/停牌不可买 —— `attribution.csv` 的 `buyable` 列已承担此职,
   `channel_audit` 与 `winner_autopsy` 均按可执行口径过滤。

## 用法

    uv run --no-sync python -m autoresearch.research.replay diff 2026-07-09     # M1 单日对拍(可信度门)
    uv run --no-sync python -m autoresearch.research.replay run 2026-06-01 2026-06-30
    uv run --no-sync python -m autoresearch.research.replay report 2026-06-01 2026-06-30
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import pandas as pd

DEFAULT_ROOT = Path("context/replay")
REPORT_ROOT = Path("reports/research/replay")
PROD_ROOT = Path("context/scan")

_PRIOR = "prior"        # 内置先验权重(零校准=零泄漏)——回放默认
_CURRENT = "current"    # 现 weights.json(**有泄漏**)——只用于 M1 对拍/对照

# 一日"已完成"的判据(幂等跳过 / 断点续跑)。L1_channels 只有 recall_mode=multi 才落,
# 但生产恒 multi,回放也恒 multi → 一并要求(缺它 = 该日没跑完,重跑)。
_STAGING = ("L1_scored_full.csv", "L1_recall_top1000.csv", "L1_channels.csv",
            "L2_gbdt_top200.csv", "meta.json")

# M1 对拍:逐字节比这些文件;meta.json 只比确定性字段(不比 weights_source——回放故意换权重源)。
_DIFF_FILES = ("L1_scored_full.csv", "L1_recall_top1000.csv", "L1_channels.csv", "L2_gbdt_top200.csv")
_DIFF_META = ("universe_raw", "universe", "after_gate_a", "recall_n", "l2_n", "l2_engine", "regime")

_PHASES = ["冰点", "修复", "发酵", "高潮", "退潮"]
_MIN_N = 10             # 薄样本门(与 cross_calib/buy_ledger/channel_audit 同款 ⚠禁注惯例)

# 因子帧完整性门(`frame_integrity`):每列 = 一个因子组的代表,缺它 = 该组整组失效、composite 失真。
# cmf_20/obv_mom_20 = volprice 组(多日量价序列,最容易因 lake 缺列而静默降级——见 2026-07-12 事故)。
_CRITICAL_COLS = ("composite", "cmf_20", "obv_mom_20", "pct_60d", "main_net_ratio")


# ───────────────────────── PIT 卫兵 ─────────────────────────


def _assert_pit_source(source: str) -> None:
    """PIT §2:回放只允许 EOD 源。`akshare` 的 em 路径含 live spot 快照(settle=live)→
    回放历史日会拿到**今天**的盘口 = 灾难性前视偏差,直接拒绝而不是静默降级。"""
    if source != "tushare":
        raise ValueError(
            f"回放只允许 source='tushare'(EOD、按 trade_date 取数 = PIT);收到 {source!r}。"
            "akshare/em 路径含 live spot 快照,回放历史日会读到今日盘口(前视偏差)。")


def weights_path_for(weights: str, root: Path | str = DEFAULT_ROOT) -> str | None:
    """回放用的 L1 权重文件路径(PIT §1)。

    - `'prior'`(默认):把内置 `_PRIOR_WEIGHTS` 写成快照文件并返回其路径。该文件**无 `regimes`
      块** → `_load_weights(path, regime=<label>)` 的 regime 分支自然落空、退回 flat 先验
      (零校准=零泄漏);regime 标签本身仍由 `classify_regime` 按**当日横截面**照常算出并落
      meta.json(标签是 PIT 安全的,只有"权重值"才带未来信息)。
    - `'current'`:返回 `None` → `universe.run` 吃 `pick_weights` 默认路径(现 weights.json)。
      **有泄漏**,仅供 M1 对拍(要复现生产就得用生产当时的权重)与对照读数。
    - 其他:当作路径原样透传。M1 对拍传生产当日的 `weights_used.json` 可**精确复现当日权重**
      ——该文件是 `pick_weights` 的返回值快照(meta+weights,无 regimes 块),而 `_load_weights`
      对无 regimes 的文件返回 data 原样,两者恰好咬合。
    """
    if weights == _CURRENT:
        return None
    if weights == _PRIOR:
        from autoresearch.common.scoring import _PRIOR_WEIGHTS

        p = Path(root) / "_weights_prior.json"
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(_PRIOR_WEIGHTS, ensure_ascii=False, indent=2), encoding="utf-8")
        return str(p)
    return str(weights)


def asof_funnel(prod_dir: Path | str) -> dict | None:
    """as-of 召回配置(M1 对拍用)——消掉"配置漂移"这类合法差异。

    **注册表全集,不是从产物反推**。第一版曾用 `L1_channels.csv` 的 channel 唯一值反推"当日
    跑了哪几路",M1 实跑当场证伪:07-09 产物只有 9 路,但当日真实传的是注册表**全部 11 路**
    ——`healthy`/`reversal_confirm` 那天**召回 0 只**(数据条件性,已记录在案),零召回的路在
    产物里根本不可见。少传 2 路 → quota_union 的配额分配变了 → 召回池差 5%。
    **产物能证明"跑过什么",不能证明"没跑过什么"。**

    故这里返回注册表全集 = 07-11 FN-1 修复前 `recall_channels=None` 的真实语义(那时
    scan_config 的 funnel 从未真生效,`recall_select` 直接吃 `registered_channels()`)。
    quotas/floors 给 `{}` = 不覆盖 = 注册表默认(同上,当日就是默认)。

    残余局限:若注册表本身在回放期后新增过通道(如 reversal_confirm 之于更早的日期),全集会
    包含当日**尚不存在**的路 —— 这是无法从今天复现的真配置漂移,M1 报告如实标注即可。
    """
    from autoresearch.scan.recall import registered_channels

    return {"recall_channels": list(registered_channels()),
            "channel_quotas": {}, "channel_floors": {}}


def asof_weights(prod_dir: Path | str) -> str:
    """生产当日的 `weights_used.json`(当日实际权重快照)→ 精确复现当日权重(PIT §1 的对拍档)。
    缺文件 → 回退 `current`(现 weights.json;**有泄漏**,但 M1 只关心管道一致性,不产研究读数)。"""
    p = Path(prod_dir) / "weights_used.json"
    return str(p) if p.exists() else _CURRENT


def m1_check(date: str, prod_root: Path | str = PROD_ROOT,
             replay_root: Path | str | None = None) -> dict:
    """**M1 可信度门**:用 as-of 配置 + as-of 权重重放某个生产日,再与生产 staging 对拍。

    先消掉三类已知合法差异,剩下的才是**回放器自身的缺陷**——这才是本门要逮的东西:
    ① 配置漂移 → `asof_funnel`(注册表全集,见其 docstring 里 M1 实跑证伪的那一课);
    ② 权重漂移 → `asof_weights`(当日 weights_used.json 快照);
    ③ **保送漂移** → `pinned_path` 指向不存在的文件。`pinned.jsonc` 是**当下**的持仓保送清单
       (会强注 L1、+N 只),而历史生产日跑在 pinned 接线之前 / 当时清单不同 —— M1 实跑正是被
       这个 +1 只的 `recall_n` 差异逮到的。回放研究(非 M1)则**保留**默认 pinned 行为:
       "今天这套配置在历史上表现如何"本就该含今天的保送。
    `force=True`:M1 就是要重算,不能吃幂等跳过的旧结果。
    """
    prod = Path(prod_root) / date
    replay_root = Path(replay_root or DEFAULT_ROOT)
    funnel = asof_funnel(prod)
    weights = asof_weights(prod)
    print(f"[m1] as-of 通道:{funnel['recall_channels'] if funnel else '(默认)'}\n"
          f"[m1] as-of 权重:{weights}\n[m1] 保送:禁用(复现当日无保送状态)", file=sys.stderr)
    replay_day(date, replay_root, weights=weights, funnel=funnel, force=True, attribute=False,
               pinned_path=replay_root / "_no_pinned.json")   # 不存在 → kept=[] → 无保送
    res = diff_day(date, prod_root, replay_root)
    res["asof_funnel"] = funnel
    res["asof_weights"] = weights
    return res


def frame_integrity(sdir: Path | str) -> list[str]:
    """因子帧完整性门:L1 打分帧里**整组失效**的因子列 → 列名清单(空 = 健康)。

    为什么需要它:上游取数失败大多被 try/except 吞成"降级"(`_harvest_vol_series` 拿不到
    high/low/amount → 返回空帧 → volprice 列**整列消失** → `composite_score` 对缺列的组自动
    NaN 重归一 → 漏斗照常跑完,只是全市场打分已经失真)。2026-07-12 M1 对拍实证:一个被窄表
    毒化的 lake parquet 就让 composite 失真 98.8%、L2 名单 jaccard 掉到 0.36,而唯一的信号是
    一行淹没在日志里的 warn。回放要拿这些帧做 250-500 日的统计裁决,**这种失真必须当场炸出来**,
    不能事后在报告里找。

    `_CRITICAL_COLS` 每项 = 一个因子组的代表列(缺它 = 该组整体失效)。缺 L1_scored_full →
    返回 `["L1_scored_full.csv"]`(该日根本没跑成)。
    """
    p = Path(sdir) / "L1_scored_full.csv"
    if not p.exists():
        return ["L1_scored_full.csv"]
    try:
        cols = set(pd.read_csv(p, nrows=1).columns)
    except Exception:  # noqa: BLE001
        return ["L1_scored_full.csv(读取失败)"]
    return [c for c in _CRITICAL_COLS if c not in cols]


# ───────────────────────── 回放循环 ─────────────────────────


def trade_days_iso(start: str, end: str) -> list[str]:
    """[start, end] 闭区间的交易日('YYYY-MM-DD')。走 tushare 交易日历(与 temperature/retro 同源)。"""
    from autoresearch.data.tushare_source import _pro, _trade_days

    days = _trade_days(_pro(), start.replace("-", ""), end.replace("-", ""))
    return [f"{d[:4]}-{d[4:6]}-{d[6:]}" for d in days]


def replay_day(date: str, root: Path | str | None = None, *, weights: str = _PRIOR,
               regime_aware: bool = True, source: str = "tushare", force: bool = False,
               attribute: bool = True, funnel: dict | None = None,
               pinned_path: Path | str | None = None) -> dict:
    """回放单日:`universe.run(outdir=root/date)` + `retro.attribute(scan_root=root)`。

    幂等:`_STAGING` 全在场且非 `force` → 直接跳过(断点续跑的基石;500 日批任务中断后重跑
    只补缺口)。`shadow=False`:影子变体 L2 不是回放的目标(R2 用 L1_channels 的 provenance
    就够),省掉每日 4 次重取数。`regime_aware=True` 对齐生产真身(prelude 默认开)。

    `funnel`(可选 `{recall_channels, channel_quotas, channel_floors}`):**None = 用今天的
    `scan_config.jsonc`**(这正是常规回放想要的——"今天这套配置在历史上表现如何");M1 对拍
    则传 `asof_funnel()` 反推的当日配置,先消掉配置漂移。

    `attribute=True` 时顺带算归因(历史日的 fwd 必然成熟);无报告目录 → buylist 空 →
    bucket 里不会有 `caught`(回放本就没有"买入"这回事),`missed_l0/missed_l1/recalled_cut`
    三桶照常 —— 这正是 R3 赢家验尸要的东西。
    """
    _assert_pit_source(source)
    root = Path(root or DEFAULT_ROOT)
    sdir = root / date
    if not force and all((sdir / f).exists() for f in _STAGING):
        return {"date": date, "status": "skip"}

    from autoresearch.scan.universe import run as universe_run

    res = universe_run(date, outdir=sdir, regime_aware=regime_aware, shadow=False, source=source,
                       weights_path=weights_path_for(weights, root),
                       **({"pinned_path": pinned_path} if pinned_path else {}),
                       **(funnel or {}))
    out = {"date": date, "status": "ok", "l2_n": res.get("l2_n"), "recall_n": res.get("recall_n"),
           "universe": res.get("universe")}
    missing = frame_integrity(sdir)
    if missing:                       # 静默降级的因子组 = 被污染的读数(见 frame_integrity)
        out["degraded"] = missing
        print(f"[replay] ⚠ {date} 因子帧缺列 {missing} → 该日整组因子失效,读数不可信",
              file=sys.stderr)
    if attribute:
        out.update(_attribute_one(date, root))
    return out


def _attribute_one(date: str, root: Path) -> dict:
    """单日归因。`report_root` 指向回放根下一个**不存在**的目录 → `_buylist` 找不到报告 → 空
    buylist(回放无买单,这是固有边界)。

    **fwd 未成熟不算失败**:回放窗口的末尾几天(D+2 还没收盘)必然算不出前向收益——但那天的
    漏斗产物是好的,不该因此把整日判死、更不该让整段 run 报错。标 `attr_pending` 留着,
    等 fwd 成熟后 `backfill_attribution` 补(冒烟实证:回放 07-08~07-09,07-09 的 fwd_2 要等
    07-13 收盘)。
    """
    from autoresearch.learning import retro

    try:
        attr = retro.attribute(date, scan_root=root, report_root=root / "_no_reports")
    except RuntimeError as e:                     # "fwd 未实现 / 无价格,暂不能复盘"
        return {"attr_pending": str(e)[:80]}
    return {"attr_n": int(len(attr)),
            "winners": int(attr["winner"].sum()) if "winner" in attr.columns else None}


def backfill_attribution(root: Path | str | None = None) -> dict:
    """补跑归因:有 staging 但缺 `retro/attribution.csv` 的日子(窗口末尾等 fwd 成熟的那几天)。

    幂等判据 `_STAGING` 不含 attribution —— 否则这些日子会被"已完成"跳过、归因永远补不上。
    故补归因是独立入口,`run()` 末尾自动调一次(把上一轮的欠账一并结清)。
    """
    root = Path(root or DEFAULT_ROOT)
    done, pending = [], []
    if not root.exists():
        return {"done": done, "pending": pending}
    for d in sorted(p.name for p in root.iterdir() if p.is_dir() and p.name[:2] == "20"):
        if (root / d / "retro" / "attribution.csv").exists():
            continue
        if not (root / d / "L1_scored_full.csv").exists():
            continue
        r = _attribute_one(d, root)
        (pending if "attr_pending" in r else done).append(d)
    if done or pending:
        print(f"[replay] 补归因:成功 {len(done)},仍未成熟 {len(pending)}", file=sys.stderr)
    return {"done": done, "pending": pending}


def run(start: str, end: str, root: Path | str | None = None, *, weights: str = _PRIOR,
        regime_aware: bool = True, force: bool = False, attribute: bool = True,
        temperature: bool = True, funnel: dict | None = None) -> dict:
    """[start, end] 逐交易日回放。**单日失败不中断整段**(限频/权限/网络偶发 → 记 failed 继续)。

    段末一次性 `temperature.rollup(start, end)` 回填相位(幂等 upsert 到
    `context/learning/temperature.csv`;相位是全市场日频序列,与回放路径无关,故整段一次而非逐日)。
    """
    root = Path(root or DEFAULT_ROOT)
    days = trade_days_iso(start, end)
    if not days:
        return {"start": start, "end": end, "days": 0, "done": [], "skipped": [], "failed": []}

    done: list[str] = []
    skipped: list[str] = []
    failed: list[dict] = []
    for i, d in enumerate(days, 1):
        try:
            r = replay_day(d, root, weights=weights, regime_aware=regime_aware, force=force,
                           attribute=attribute, funnel=funnel)
            (skipped if r["status"] == "skip" else done).append(d)
            print(f"[replay {i}/{len(days)}] {d} {r['status']}"
                  + (f" L2={r.get('l2_n')} winners={r.get('winners')}" if r["status"] == "ok" else ""),
                  file=sys.stderr)
        except Exception as e:  # noqa: BLE001 — 单日失败不中断整段(断点续跑会补)
            failed.append({"date": d, "error": repr(e)[:200]})
            print(f"[replay {i}/{len(days)}] {d} FAILED {e!r}", file=sys.stderr)

    if temperature:
        try:
            from autoresearch.scan.temperature import rollup

            rollup(start, end)
        except Exception as e:  # noqa: BLE001 — 相位缺失只降级 R1/R2 的分相位读数
            print(f"[replay] 温度回填失败({e!r})→ 相位读数将缺失", file=sys.stderr)

    bf = backfill_attribution(root) if attribute else {"done": [], "pending": []}
    summary = {"start": start, "end": end, "days": len(days), "weights": weights,
               "weights_leak": weights == _CURRENT,
               "done": done, "skipped": skipped, "failed": failed,
               "attr_backfilled": bf["done"], "attr_pending": bf["pending"]}
    (root / "_run_summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    return summary


# ───────────────────────── M1 单日对拍(可信度门) ─────────────────────────


def diff_day(date: str, prod_root: Path | str = PROD_ROOT,
             replay_root: Path | str | None = None) -> dict:
    """M1:回放 staging vs 生产 staging 逐文件对拍 —— 回放器的**可信度门**。

    先比文件字节;不等再比结构(行数 / 关键列的逐值)。返回 `{ok, files: {...}, meta: {...}}`。

    **对拍前先读这段**:不一致未必是回放器的 bug,已知三类合法差异——
    ① **配置漂移**:生产当日跑的是当时的 `scan_config.jsonc`(如 07-08/07-09 跑在 FN-1 修复
       之前 = 11 路旧配额;今天的配置是整编后 9 路)→ 召回名单必然不同。要真对拍,得把当日
       配置显式传回 `universe.run`(`recall_channels`/`channel_quotas`)。
    ② **权重漂移**:weights.json 被 retro 持续重标定 → 用 `weights='<当日 weights_used.json>'`
       才能复现(见 `weights_path_for`)。
    ③ **数据漂移**:tushare 对历史行情的事后修订(复权/重述)。
    只有排除这三类之后仍存在的差异,才是回放器自身的缺陷。
    """
    prod, rep = Path(prod_root) / date, Path(replay_root or DEFAULT_ROOT) / date
    out: dict = {"date": date, "prod": str(prod), "replay": str(rep), "files": {}, "ok": True}
    if not prod.exists() or not rep.exists():
        return {**out, "ok": False, "reason": f"缺目录(prod={prod.exists()}, replay={rep.exists()})"}

    for f in _DIFF_FILES:
        p, r = prod / f, rep / f
        if not p.exists() or not r.exists():
            out["files"][f] = {"ok": False, "reason": f"缺文件(prod={p.exists()}, replay={r.exists()})"}
            out["ok"] = False
            continue
        if p.read_bytes() == r.read_bytes():
            out["files"][f] = {"ok": True, "identical": True}
            continue
        dp = pd.read_csv(p, dtype={"code": str})
        dr = pd.read_csv(r, dtype={"code": str})
        info: dict = {"ok": False, "identical": False,
                      "n_prod": int(len(dp)), "n_replay": int(len(dr))}
        if "code" in dp.columns and "code" in dr.columns:
            cp = set(dp["code"].astype(str).str.zfill(6))
            cr = set(dr["code"].astype(str).str.zfill(6))
            info.update(common=len(cp & cr), only_prod=len(cp - cr), only_replay=len(cr - cp),
                        jaccard=round(len(cp & cr) / max(1, len(cp | cr)), 4))
        out["files"][f] = info
        out["ok"] = False

    mp, mr = prod / "meta.json", rep / "meta.json"
    if mp.exists() and mr.exists():
        a = json.loads(mp.read_text(encoding="utf-8"))
        b = json.loads(mr.read_text(encoding="utf-8"))
        meta_diff = {k: {"prod": a.get(k), "replay": b.get(k)}
                     for k in _DIFF_META if a.get(k) != b.get(k)}
        out["meta"] = meta_diff or {"identical": True}
        if meta_diff:
            out["ok"] = False
    return out


# ───────────────────────── 聚合:R1 相位 / R2 通道×相位 / R3 赢家验尸 ─────────────────────────


def phase_map(path: Path | str | None = None) -> dict[str, str]:
    """`temperature.csv` → {date: phase}。缺文件/空表 → {}(presence-gated,下游降级为不分相位)。"""
    from autoresearch.scan import temperature as temp

    p = Path(path) if path is not None else temp.CSV_PATH
    if not p.exists():
        return {}
    try:
        df = pd.read_csv(p)
    except Exception:  # noqa: BLE001
        return {}
    if not len(df) or "date" not in df.columns or "phase" not in df.columns:
        return {}
    return {str(r["date"]): str(r["phase"]) for _, r in df.iterrows() if pd.notna(r.get("phase"))}


def channel_by_phase(root: Path | str | None = None, days: int = 9999,
                     phases: dict[str, str] | None = None) -> dict[str, pd.DataFrame]:
    """**R2**:通道 × 温度相位的累计账本 —— 复用 `channel_audit` 的单日口径,只加"按相位切"。

    返回 {phase: cumulative_ledger 帧};另含 `"__all__"` 键 = 不分相位的全窗账本(与生产
    channel_audit 同口径,可与前向 13 日账本直接对照)。相位缺失的日子归入 `"未知"`。

    为什么值得做:前向 13 日账本只覆盖弱市(risk_off/range)——momentum 在"发酵"段和在"冰点"
    段本该是两条路,单一均值把它们平均成了噪声。这是"相位切菜单"拍板方案的**证据来源**。
    """
    from autoresearch.research import channel_audit as ca

    root = Path(root or DEFAULT_ROOT)
    phases = phases if phases is not None else phase_map()
    dates = ca._scan_dates(root, days)
    daily: dict[str, pd.DataFrame] = {}
    for d in dates:
        loaded = ca._load_day(root, d)
        if loaded is None:
            continue
        ch, attr = loaded
        daily[d] = ca.day_channel_stats(ch, attr)
    if not daily:
        return {}

    out = {"__all__": ca.cumulative_ledger(daily)}
    by_phase: dict[str, dict[str, pd.DataFrame]] = {}
    for d, st in daily.items():
        by_phase.setdefault(phases.get(d, "未知"), {})[d] = st
    for ph in _PHASES + ["未知"]:
        if ph in by_phase:
            out[ph] = ca.cumulative_ledger(by_phase[ph])
    return out


def winner_autopsy(root: Path | str | None = None,
                   phases: dict[str, str] | None = None) -> pd.DataFrame:
    """**R3**:赢家死在哪一层 —— 逐日 `attribution.csv` 的 bucket 聚合(按相位分组)。

    桶语义(`retro.attribute_frame`):`missed_l0`=连 L0 都没进(硬门砍的)/`missed_l1`=进了
    L0 池但没被召回(召回线砍的)/`recalled_cut`=召回了但没走到买入。回放无买单 → 无 `caught`
    (这是回放的固有边界,不是 bug:回放止步 L2,"谁该被买"是 L3/L4 的问题)。

    读数怎么用:`missed_l1` 占比高 = 召回线是瓶颈(前向 14 日账本已给出 5.7× 的读数,这里是
    大样本复核);按相位切开后,若某相位的 missed_l1 显著更高,说明该相位的菜单/配额错配。
    """
    root = Path(root or DEFAULT_ROOT)
    phases = phases if phases is not None else phase_map()
    rows = []
    for p in sorted(root.glob("*/retro/attribution.csv")):
        date = p.parent.parent.name
        try:
            attr = pd.read_csv(p, dtype={"code": str})
        except Exception:  # noqa: BLE001
            continue
        if not len(attr) or "winner" not in attr.columns:
            continue
        if "buyable" in attr.columns:                    # PIT §5:一字板/停牌不可买 → 不算赢家
            attr = attr[attr["buyable"].astype(bool)]
        w = attr[attr["winner"].astype(bool)]
        if not len(w):
            continue
        vc = w["bucket"].astype(str).value_counts() if "bucket" in w.columns else pd.Series(dtype=int)
        rows.append({"date": date, "phase": phases.get(date, "未知"), "n_winners": int(len(w)),
                     "missed_l0": int(vc.get("missed_l0", 0)),
                     "missed_l1": int(vc.get("missed_l1", 0)),
                     "recalled_cut": int(vc.get("recalled_cut", 0)),
                     "caught": int(vc.get("caught", 0))})
    if not rows:
        return pd.DataFrame(columns=["phase", "n_days", "n_winners", "missed_l0", "missed_l1",
                                     "recalled_cut", "caught", "missed_l1_pct"])
    df = pd.DataFrame(rows)
    agg = df.groupby("phase", as_index=False).agg(
        n_days=("date", "count"), n_winners=("n_winners", "sum"),
        missed_l0=("missed_l0", "sum"), missed_l1=("missed_l1", "sum"),
        recalled_cut=("recalled_cut", "sum"), caught=("caught", "sum"))
    agg["missed_l1_pct"] = (100.0 * agg["missed_l1"] / agg["n_winners"].where(agg["n_winners"] > 0)).round(1)
    agg["__k"] = agg["phase"].map(lambda p: _PHASES.index(p) if p in _PHASES else len(_PHASES))
    return agg.sort_values("__k").drop(columns="__k").reset_index(drop=True)


def phase_returns(root: Path | str | None = None,
                  phases: dict[str, str] | None = None) -> pd.DataFrame:
    """**R1**:温度相位 × 全市场 fwd_1/fwd_2 条件分布(择时口径,非选股 IC)。

    复用 `temperature_calib.forward_returns`(市场等权 NAV 累乘),只是样本从"生产跑过的
    107 日"扩到整个回放窗。判决条款(design E1):相位间 fwd_2 条件均值差 ≥ 全样本 σ/2 且
    每相位 n≥30,温度机器才配当"切菜单/切仓位"的开关;否则只保留描述性地位。
    """
    from autoresearch.scan.temperature_calib import forward_returns

    phases = phases if phases is not None else phase_map()
    if not phases:
        return pd.DataFrame(columns=["phase", "n", "mean_fwd_1", "mean_fwd_2", "std_fwd_2", "thin"])
    root = Path(root or DEFAULT_ROOT)
    dates = sorted(d for d in phases if (root / d).exists()) or sorted(phases)
    fr = forward_returns(dates)
    if not len(fr):
        return pd.DataFrame(columns=["phase", "n", "mean_fwd_1", "mean_fwd_2", "std_fwd_2", "thin"])
    fr["phase"] = fr["date"].map(phases)
    agg = fr.groupby("phase", as_index=False).agg(
        n=("date", "count"), mean_fwd_1=("fwd_1", "mean"),
        mean_fwd_2=("fwd_2", "mean"), std_fwd_2=("fwd_2", "std"))
    agg["thin"] = agg["n"] < _MIN_N
    for c in ("mean_fwd_1", "mean_fwd_2", "std_fwd_2"):
        agg[c] = agg[c].astype(float).round(5)
    agg["__k"] = agg["phase"].map(lambda p: _PHASES.index(p) if p in _PHASES else len(_PHASES))
    return agg.sort_values("__k").drop(columns="__k").reset_index(drop=True)


# ───────────────────────── 报告 ─────────────────────────


def _md_table(df: pd.DataFrame, cols: list[str] | None = None) -> list[str]:
    cols = cols or list(df.columns)
    cols = [c for c in cols if c in df.columns]
    if not len(df) or not cols:
        return ["_(无数据)_"]
    out = ["| " + " | ".join(cols) + " |", "|" + "|".join(["---"] * len(cols)) + "|"]
    for _, r in df.iterrows():
        out.append("| " + " | ".join("—" if pd.isna(r[c]) else str(r[c]) for c in cols) + " |")
    return out


def report(start: str, end: str, root: Path | str | None = None,
           out_root: Path | str | None = None, weights: str = _PRIOR) -> Path:
    """R1+R2+R3 汇总成一份 md → `reports/research/replay/<start>_<end>/replay.md`。"""
    root = Path(root or DEFAULT_ROOT)
    outdir = Path(out_root or REPORT_ROOT) / f"{start}_{end}"
    outdir.mkdir(parents=True, exist_ok=True)
    ph = phase_map()

    L = [f"# 漏斗回放报告 {start} → {end}", "",
         f"- staging:`{root}`;权重:**{weights}**"
         + ("(**⚠ 有泄漏**:weights.json 由含未来收益的 retro 校准而来,此读数仅供对照)"
            if weights == _CURRENT else "(内置先验,零校准=零泄漏)"),
         "- 边界:只回放 L0→L2 + 相位;L3/L4(LLM 判断层)不可回放 —— 结论止步『谁进了候选池』。", ""]

    days = sorted(p.name for p in root.iterdir() if p.is_dir() and p.name[:2] == "20") if root.exists() else []
    L += [f"- 回放日数:**{len(days)}**;有相位标注:{sum(1 for d in days if d in ph)}", ""]

    L += ["## R1 · 温度相位 × 全市场前向收益(择时口径)", "",
          "判决(E1):相位间 fwd_2 均值差 ≥ 全样本 σ/2 且每相位 n≥30 → 温度可当切菜单/切仓位的开关;否则只留描述性地位。", ""]
    L += _md_table(phase_returns(root, ph)) + [""]

    L += ["## R2 · 召回通道 × 相位(unique 超额 T+2 为主尺)", "",
          "口径同生产 `channel_audit`(unique=当日仅此一路召回的票);`__all__` 行可与前向 13 日账本直接对照。", ""]
    cbp = channel_by_phase(root, phases=ph)
    if not cbp:
        L += ["_(无 L1_channels/attribution 数据)_", ""]
    else:
        for k, df in cbp.items():
            L += [f"### {'全窗(不分相位)' if k == '__all__' else k}", ""]
            L += _md_table(df, ["channel", "n_days", "unique_excess_t2", "mean_excess_t2",
                                "hit_rate_t2", "thin"]) + [""]

    L += ["## R3 · 赢家验尸(死在哪一层;按相位)", "",
          "`missed_l1` = 进了 L0 池但召回线没捞到(前向 14 日账本读数:是 L3/L4 误杀的 5.7 倍)。"
          "回放无买单 → 无 `caught`,这是回放的固有边界。", ""]
    L += _md_table(winner_autopsy(root, ph)) + [""]

    p = outdir / "replay.md"
    p.write_text("\n".join(L) + "\n", encoding="utf-8")
    return p


# ───────────────────────── CLI ─────────────────────────


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="replay", description="漏斗历史回放器(零 LLM 研究仪器)")
    sub = ap.add_subparsers(dest="cmd", required=True)

    r = sub.add_parser("run", help="逐日回放 [start, end]")
    r.add_argument("start")
    r.add_argument("end")
    r.add_argument("--root", default=None)
    r.add_argument("--weights", default=_PRIOR,
                   help=f"{_PRIOR}(默认,零泄漏)| {_CURRENT}(有泄漏,仅对照)| <weights.json 路径>")
    r.add_argument("--force", action="store_true", help="已完成的日也重跑(默认幂等跳过)")
    r.add_argument("--no-attribute", action="store_true", help="只跑漏斗不算归因")

    d = sub.add_parser("diff", help="纯对拍(不重跑;比已在场的回放 staging vs 生产 staging)")
    d.add_argument("date")
    d.add_argument("--root", default=None)
    d.add_argument("--prod-root", default=str(PROD_ROOT))

    m = sub.add_parser("m1", help="M1 可信度门:as-of 配置+权重重放该生产日,再对拍")
    m.add_argument("date")
    m.add_argument("--root", default=None)
    m.add_argument("--prod-root", default=str(PROD_ROOT))

    p = sub.add_parser("report", help="R1+R2+R3 聚合报告")
    p.add_argument("start")
    p.add_argument("end")
    p.add_argument("--root", default=None)
    p.add_argument("--weights", default=_PRIOR)

    b = sub.add_parser("attr", help="补跑归因(窗口末尾等 fwd 成熟的那几天)")
    b.add_argument("--root", default=None)

    a = ap.parse_args(argv)
    if a.cmd == "run":
        res = run(a.start, a.end, a.root, weights=a.weights, force=a.force,
                  attribute=not a.no_attribute)
        print(json.dumps({k: (len(v) if isinstance(v, list) else v) for k, v in res.items()},
                         ensure_ascii=False))
        return 0 if not res["failed"] else 1
    if a.cmd in ("diff", "m1"):
        res = (m1_check(a.date, a.prod_root, a.root) if a.cmd == "m1"
               else diff_day(a.date, a.prod_root, a.root))
        print(json.dumps(res, ensure_ascii=False, indent=2))
        return 0 if res["ok"] else 1
    if a.cmd == "attr":
        print(json.dumps(backfill_attribution(a.root), ensure_ascii=False))
        return 0
    path = report(a.start, a.end, a.root, weights=a.weights)
    print(f"[replay] 报告 → {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
