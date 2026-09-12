#!/usr/bin/env python3
"""结果账本 —— 一只推荐票事后到底涨没涨(确定性、零 LLM、零网络、**只记不学**)。

design: docs/specs/2026-08-26-scene-retention-and-buy-owner-design.md §4.4

## 病灶

2026-08-21「整个 learning 层退役」删掉了 `relative_ledger` / `buy_ledger` / `retro` —— 那些
是**从历史里学、回注 prompt、自动提案**的腿,删得对。但它们顺手带走了唯一一件**记录**:
从此没有任何东西回答「这只推荐票后来怎么样了」。E6 转正设计稿的 U6 写着「转正后的记分册 =
relative_buy 账本本身」,而那本账已经没有实体。用户第 ① 件事(「用于复盘当时的推荐股票
链路」)缺的正是这条腿。

**判据仍然是那一条**:输入没人生产就不留 —— 这次输入(推荐本身)天天在产,缺的是记录者。
所以本模块只有「记」:

- **不**回注任何 prompt、**不**改任何权重/门/评级、**不**产生 proposal;
- 消费者只有两个:`chain_view` 的 ⑩ 结果段,与 prelude 汇总屏一行读数;
- **不进 brief、不喂任何 agent**(同 `l4_rejection` 日读的边界)。

## 口径(与 `research.edge_census` 逐字同源)

前向收益走 `factor_lab.forward_returns`(同一实现)、可交易折叠走 `ruler.entry_tradable`、
`|gap| > GAP_CLIP` 视作数据错置 NaN —— 两者读数因此可以直接对表。三个相对列的分母各不相同,
**列名即口径**,不要混读:

| 列 | 口径 |
|---|---|
| `rel_gap_market` | gap − 当日全湖可交易**等权均值**(`ruler.REL_MARKET`,E6 口径) |
| `rel_gap_sector` | gap − 同行业可交易**等权均值**(`ruler.REL_SECTOR`) |
| `excess_med_market` | gap − 当日全湖可交易**中位**(`edge_census` 主列口径) |

`exec_ok` = 设计稿 A4 的 T+1 收盘执行条件(当日涨幅 ≤3% ∧ 收盘不在当日区间上 30% ∧ 未封
涨停)。**它是量尺不是门**:即便 A4 尚未上线,先把它记下来,才有「BUY 无条件」与
「BUY ∧ 执行条件满足」两条曲线可比。证据见设计稿 §1.5/§1.6(四年全湖逐年同号)。

## 落在哪(与设计稿的一处偏离,实施时决定)

设计稿写的是 `trace/outcome.json`(run 目录内)。**实施时改到 `_ledger/` 下**,因为同一波
刚刚给 run 目录立了「发布后不再变」的 MANIFEST 不变量 —— 事后往 run 里写文件会让每个 run
的 `verify` 永远报一条 `extra`,等于自己把刚立的哨兵弄哑。现在:

    reports_<engine>/scan/_ledger/outcome/<run_id>.json     # 逐 run 明细
    reports_<engine>/scan/_ledger/recommendations.csv       # 跨 run 索引(按 run_id,code 幂等 upsert)

  uv run --no-sync python -m autoresearch.scan.outcome fill [--today 2026-08-26] [--limit 5]
  uv run --no-sync python -m autoresearch.scan.outcome line
"""
from __future__ import annotations

import argparse
import contextlib
import csv
import hashlib
import json
import re
from datetime import (
    date as _date,
    datetime as _datetime,
    timedelta as _timedelta,
    timezone as _timezone,
)
from pathlib import Path

import numpy as np
import pandas as pd

from autoresearch.common import forward_returns as _fwd, ruler as _ruler, workspace as ws
from autoresearch.contracts.agent_output import (
    EXEC_LINE_MAX_PCT_1D,
    EXEC_LINE_MAX_POS_IN_RANGE,
)
from autoresearch.data import market_panel as _panel
from autoresearch.scan.run_naming import is_run_dir

OUTCOME_SCHEMA_VERSION = 2
#: schema 2(2026-09-12 Task C2):逐 run JSON 在 schema 1 的全部字段之上新增
#: `outcome_status`/`reason`/`calendar_quality`/`calendar_digest`/`t1`/`t2`(doc 级,
#: §2.2 五态之一)——非 `MATURE` 时 `rows` 恒为 `{}`(没有可汇总的主尺值,状态本身就是
#: 那一行缺席的原因,不是数字旁边的装饰,见 `compute_outcome`)。`recommendations.csv`
#: 同步增加同名的 `outcome_status`/`calendar_quality`/`calendar_digest`/`t1`/`t2` 五列
#: (doc 级值逐行广播,同 `anchor_session`/`exec_lag`/`actionability` 的既定手法)加
#: `exec_outcome_status`(行级,`compute_outcome` 已经算好)——合计六列,与 brief §3 字面
#: 同源。
LEDGER_DIRNAME = "_ledger"
LEDGER_CSV = "recommendations.csv"
MAIN = _ruler.MAIN_RULER

# ── 日期契约状态(2026-09-12 交易日历完整性 P0,§2.2;字面量与验收矩阵逐字同源)──
#
# `market_frame` 的 `outcome_status` 只取这五个值之一。C2 会把它们接进
# `recommendations.csv`/`ledger_views.py`/`chain_view.py`——本文件(Task C1)只负责
# 正确产出,不碰那三处消费点。
MATURE = "MATURE"
PENDING_SESSION = "PENDING_SESSION"
MISSING_MARKET_DATA = "MISSING_MARKET_DATA"
UNVERIFIED_CALENDAR = "UNVERIFIED_CALENDAR"
INVALID_ANALYSIS_DATE = "INVALID_ANALYSIS_DATE"
#: 唯一可信的日历质量标签(与 `exec_anchor.trading_sessions` 的三级回退同一份字面量)。
#: `lake_partitions`/`weekday_heuristic` 一律不可信——即便它们恰好给出了正确的日期。
TRADE_CAL_QUALITY = "trade_cal"

#: A4 执行线阈值(设计稿 §3 路A)。**先量后用**:上线与否是产品裁定,这里只负责记下
#: 「若按此执行会怎样」。证据:四年全湖 1086 日,收在当日区间上 30% 的票隔夜比全体差
#: 0.13~0.27pp,**逐年同号**(2022–2026 无一年反号)。
#:
#: D8.3⑤:数值真身搬去 `contracts.agent_output`(两份手写文档与这里各写一份、没有
#: 测试对齐的病灶登记在那边的模块 docstring)。这里的两个名字**引用同一个对象**
#: (`is` 恒成立),不是复制——改数值只需要改那一处。
EXEC_MAX_PCT_1D = EXEC_LINE_MAX_PCT_1D
EXEC_MAX_POS_IN_RANGE = EXEC_LINE_MAX_POS_IN_RANGE

LEDGER_COLUMNS = (
    # `mode`/`src` 是**读这本账之前必须先看的两列**,不是装饰:
    #  - `mode`  shadow 期的 BUY 在报告里明写「非正式·不执行」,与 active 期的 BUY 不是同一件事;
    #  - `src`   `shared` = 这一行读自共享 staging(同数据日重跑会覆盖),未必是本 run 当时那份。
    #    实测 2026-08-13/08-18 两天:brief 印的是 BLOCKED,而共享 staging 的决策文件被后来的
    #    影子回放改写成 `buys=[688766]`(还是一只 📌 持仓)。不分列读 = 把两条假 BUY 算进战绩。
    "run_id", "analysis_date", "mode", "src",
    # ── 日历完整性(2026-09-12 §2,schema 2)。**这三列与 `actionability` 同一条既定纪律**:
    #  缺这一列(schema 1 的老行,本波之前写的账本)= 未知,不是「已核验」—— `ledger_line`
    #  与其它消费者一律 `str(row.get(...) or "")` 读,空值与缺列同一个待遇,绝不当 MATURE
    #  算进均值(2026-08-28 §2.4 G1 的 `actionability` 就是这样处理老行的,这里照抄)。
    #  `outcome_status` 只有恰好是字面量 `"MATURE"` 才可信;`calendar_quality` 只有恰好是
    #  `"trade_cal"` 才可信——弱回退(`lake_partitions`/`weekday_heuristic`)、空、旧行
    #  统统落「未验证」桶,不会因为凑巧对上日期就被提级。
    "outcome_status", "calendar_quality", "calendar_digest",
    "code", "name", "sector", "role", "lane", "guard",
    "conviction", "rating", "proposal", "early_stop_reason", "e6_rank", "e6_eligible",
    "e6_buy", "buyable_c1", "t1", "t2", "t1_open", "t1_high", "t1_low", "t1_close", "t1_pct_chg",
    "t1_pos_in_range", "exec_ok", "t2_open", "gap_c1_o2", "rel_gap_market",
    "rel_gap_sector", "excess_med_market", "fwd_5_oc", "fwd_10_oc", "ruler",
    # ── 时间锚(2026-08-28 §2.4 G1)。**读 BUY 战绩前必须先看 `actionability`** ──
    #  `anchor_session` 是本行主尺真正的买腿日:正常 run = analysis_date 的下一交易日
    #  (与上面各列同源);迟到 run(报告在 T+1 收盘后才就绪)的那一天已经过去了,
    #  上面的 `gap_c1_o2` 记的是一笔**下不了的单**(且是**毛收益**——本模块从未接入
    #  broker,不能读成实际成交)。`exec_gap_c1_o2` 是同一把尺从第一个真正来得及的尾盘
    #  起算的反事实**估计**,**绝不与上面那列混算均值**(两个人口)。`exec_outcome_status`
    #  是这份反事实自己的成熟状态(§2.2 五态之一,或 `None`=不适用/无需算,2026-09-12
    #  C1 re-review Q2)——**空单元格(不适用)与一个失败态字符串是两个不同的意思**,
    #  写盘时不能塌缩成同一种「空」:Python `None` 经 csv 模块写出即为空单元格,失败态
    #  写出的是它自己的状态字符串(如 `UNVERIFIED_CALENDAR`),两者天然可辨。
    "anchor_session", "exec_lag", "actionability", "exec_gap_c1_o2", "exec_outcome_status",
    "computed_at",
)


# ───────────────────────── 定位:run 目录 → 当日事实 ─────────────────────────

def ledger_root(reports_root: Path | None = None) -> Path:
    return Path(reports_root or (ws.reports_root() / "scan")) / LEDGER_DIRNAME


def _read_json(base: Path, *names: str) -> object | None:
    for name in names:
        p = base / name
        if p.is_file():
            with contextlib.suppress(OSError, json.JSONDecodeError):
                return json.loads(p.read_text(encoding="utf-8"))
    return None


def _read_rows(base: Path, *names: str) -> list[dict]:
    for name in names:
        p = base / name
        if p.is_file():
            with contextlib.suppress(OSError, UnicodeDecodeError), \
                p.open(encoding="utf-8-sig", newline="") as fh:
                return list(csv.DictReader(fh))
    return []


def _z6(value: object) -> str:
    return str(value or "").split(".")[0].strip().zfill(6)


def run_facts(run_dir: Path | str) -> dict:
    """一次 run 的「当天推荐了谁、判成什么」—— 只读,缺什么就少什么(不伪造)。

    读盘优先级 `trace/staging/` → `trace/` → 共享 staging;最后那级会被标记,因为同数据日
    重跑会覆盖它(实测 64 个已发布 run 只剩 49 个 staging)。
    """
    run = Path(run_dir)
    manifest = _read_json(run, "manifest.json") or {}
    date = str(manifest.get("analysis_date") or "")
    bases = [run / "trace" / "staging", run / "trace"]
    if date:
        bases.append(ws.scan_root() / date)
    used_shared = False

    def pick_rows(*names: str) -> list[dict]:
        nonlocal used_shared
        for i, base in enumerate(bases):
            rows = _read_rows(base, *names)
            if rows:
                used_shared = used_shared or (i == 2)
                return rows
        return []

    def pick_json(*names: str) -> object | None:
        nonlocal used_shared
        for i, base in enumerate(bases):
            doc = _read_json(base, *names)
            if doc is not None:
                used_shared = used_shared or (i == 2)
                return doc
        return None

    finalists = pick_rows("L3_fine_finalists.csv", "finalists.csv")
    ratings = pick_json("_final_ratings.json") or {}
    early = pick_json("_early_stop.json") or {}
    decision = pick_json("_relative_buy_decision.json") or {}
    judged = {_z6(r.get("code")): r for r in pick_rows("L3_judged_full.csv")}

    buys = {_z6(b.get("code")) for b in (decision.get("buys") or [])}
    cand = {_z6(c.get("code")): c for c in (decision.get("candidates") or [])}

    rows: dict[str, dict] = {}
    for fr in finalists:
        code = _z6(fr.get("code") or fr.get("ticker"))
        if code == "000000":
            continue
        lane = str(fr.get("lane") or "")
        guard = str(fr.get("guard") or "")
        rows[code] = {
            "code": code,
            "name": str(fr.get("name") or ""),
            "sector": str(fr.get("sector") or ""),
            "lane": lane,
            "guard": guard,
            "conviction": fr.get("conviction"),
            "role": ("pinned" if lane == "pinned" else
                     "composite_seat" if guard == "composite_seat" else "finalist"),
        }
    for code, rating in (ratings.items() if isinstance(ratings, dict) else []):
        code = _z6(code)
        rows.setdefault(code, {"code": code, "name": "", "sector": "", "lane": "",
                               "guard": "", "conviction": None, "role": "rated"})
        rows[code]["rating"] = rating
    for code, row in rows.items():
        j = judged.get(code) or {}
        row.setdefault("rating", None)
        row.setdefault("name", str(j.get("name") or ""))
        if not row.get("sector"):
            row["sector"] = str(j.get("sector") or "")
        if row.get("conviction") in (None, ""):
            row["conviction"] = j.get("conviction")
        stop = early.get(code) if isinstance(early, dict) else None
        row["early_stop_reason"] = (stop or {}).get("reason") if isinstance(stop, dict) else None
        c = cand.get(code)
        row["e6_rank"] = (c or {}).get("rank")
        row["e6_eligible"] = (c or {}).get("eligible")
        row["e6_buy"] = code in buys
        if code in buys:
            row["role"] = "BUY"
    return {"analysis_date": date, "rows": rows, "used_shared": used_shared,
            "contract_run_id": str(manifest.get("run_id") or ""),
            "decision_mode": str(decision.get("mode") or ""),
            "rule_version": str(decision.get("rule_version") or "")}


# ───────────────────────── 日期解析:可信交易日历 → T+1/T+2(零行情读取) ─────────────────────────
#
# 病灶(2026-09-12 独立 P0,立案见 §1):旧 `market_frame` 用**湖分区排序后的位置**取
# T+1/T+2 —— 湖缺某天,后面的文件就顶替成了"T+2",一笔隔夜交易被错记成跨越缺口的多日
# 持仓(09-01、09-07 两次 run 共 21 行日期错位)。修复:先用**注入的可信日历**把 D 的
# T+1/T+2 定成精确日期(`resolve_outcome_sessions`,零行情读取),`market_frame` 再按
# 这两个精确日期去读行情——湖里没有就是 `MISSING_MARKET_DATA`,绝不顺延到下一个分区。

_SESSION_LOOKBACK_DAYS = 10        # D 之前的请求缓冲:只需要够判断"D 是不是交易日"
#: D 之后的请求缓冲。比 A 股最长连续休市(春节 ~10 个自然日)之后再找到第 10 个交易日
#: 还要宽——10 个交易日最多跨 2 个周末(~4 天)+ 一段长假,45 个自然日留足余量。
_SESSION_LOOKAHEAD_DAYS = 45
#: 迟到执行锚找"前一个可信交易日"专用的回看窗——同样要跨过最长连续休市。
_PREDECESSOR_LOOKBACK_DAYS = 20

_DASHED_DATE_RE = re.compile(r"^(\d{4})-(\d{2})-(\d{2})$")
_COMPACT_DATE_RE = re.compile(r"^(\d{4})(\d{2})(\d{2})$")


def _compact_or_none(value: object) -> str | None:
    """`"2026-09-01"`/`"20260901"` → `"20260901"`;格式非法或日期不存在 → `None`。"""
    s = str(value or "").strip()
    m = _DASHED_DATE_RE.fullmatch(s) or _COMPACT_DATE_RE.fullmatch(s)
    if not m:
        return None
    y, mo, da = m.groups()
    try:
        _date(int(y), int(mo), int(da))
    except ValueError:
        return None
    return f"{y}{mo}{da}"


def _dashed(compact: str) -> str:
    return f"{compact[:4]}-{compact[4:6]}-{compact[6:8]}"


def _shift(compact: str, days: int) -> str:
    d = _date(int(compact[:4]), int(compact[4:6]), int(compact[6:8])) + _timedelta(days=days)
    return d.strftime("%Y%m%d")


def _normalize_today(today: object) -> str:
    """`today` → 紧凑日期字符串,按 Asia/Shanghai 的交易日期解释。

    `None` = 用真实当前时刻(生产默认);中国全年无夏令时,固定 +8 小时换算,不依赖
    `zoneinfo`/`pytz`(与 `exec_anchor._gate4_approved_at` 同一手法)。UTC 感知的
    `datetime` 先转换再取日期,不直接砍掉时区(§2.1 第5条)。裸 `date`/格式化字符串
    按已经是"上海交易日期"直接采信。
    """
    if today is None:
        now = _datetime.now(_timezone.utc) + _timedelta(hours=8)
        return now.strftime("%Y%m%d")
    if isinstance(today, _datetime):
        dt = today
        if dt.tzinfo is not None:
            dt = dt.astimezone(_timezone.utc) + _timedelta(hours=8)
        return dt.strftime("%Y%m%d")
    if isinstance(today, _date):
        return today.strftime("%Y%m%d")
    return _compact_or_none(today) or ""


def _digest(start: str, end: str, quality: str, sessions: tuple[str, ...]) -> str:
    """日历来源的规范化摘要(§2.1 第6条)——质量 + 请求范围 + 所用 session 列表逐字哈希。"""
    payload = json.dumps({"start": start, "end": end, "quality": quality,
                          "sessions": list(sessions)}, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


def _fetch_trusted_sessions(start_c: str, end_c: str, *, calendar
                            ) -> tuple[list[str], str, str, str | None]:
    """如实转达 `calendar(start,end)` 的结果——**不在这里判断可信与否**,只负责规范化
    (紧凑日期、去重排序)与如实报告失败。调用方(`resolve_outcome_sessions`/
    `_trusted_predecessor`)各自按自己的用途决定"够不够可信"。

    返回 `(sessions, quality, digest, error)`;`calendar()` 抛异常时 `sessions=[]`、
    `quality="error"`、`error` 是可读的异常描述(不静默吞掉——弱回退/请求异常都要
    在 `reason` 里现出原因)。
    """
    try:
        raw, quality = calendar(_dashed(start_c), _dashed(end_c))
    except Exception as exc:  # noqa: BLE001 - 日历来源必须如实报异常,不静默降级为弱日历
        return [], "error", _digest(start_c, end_c, "error", ()), f"{type(exc).__name__}: {exc}"
    sessions = sorted({c for c in (_compact_or_none(s) for s in (raw or [])) if c})
    digest = _digest(start_c, end_c, str(quality or ""), tuple(sessions))
    return sessions, str(quality or ""), digest, None


def resolve_outcome_sessions(analysis_date: str, *, calendar, today: object = None) -> dict:
    """纯日期解析(零行情读取):`analysis_date` → 可信日历下的 T+1/T2(+T5/T10 备用)。

    `calendar` 为 `(start,end)->(sessions,quality)`,与 `exec_anchor.trading_sessions`
    同形状,测试注入合成日历。返回 dict 恒含:

    - `status`:`"OK"`(日期已解析,T+2 已到,调用方可以去读行情)或 §2.2 表格里的
      `PENDING_SESSION`/`MISSING_MARKET_DATA` 之外的两个日期级状态
      (`UNVERIFIED_CALENDAR`/`INVALID_ANALYSIS_DATE`)——`MISSING_MARKET_DATA` 本函数
      判不出来(它零行情读取),留给 `market_frame` 在拿到 `"OK"` 之后按精确日期查湖。
    - `reason`:人读的原因,任何非 `"OK"` 状态都不为空。
    - `calendar_quality`/`calendar_digest`:来源质量 + 规范化摘要(§2.1 第6条)。
    - `t1`/`t2`/`t5`/`t10`:紧凑日期或 `None`(日历覆盖不到就是 `None`,不猜)。
    - `sessions`:已按 `today` 之前(含)过滤前的完整可信 session 列表(紧凑、排序),
      供 `market_frame` 建行情窗口用——**位置对齐用这份日历,不用湖文件列表**。

    判定顺序逐字对应 §2.1:①日历来源(quality 必须 `trade_cal`)②弱回退/空/异常/范围
    不完整一律 `UNVERIFIED_CALENDAR`,不因日期"恰好对上"提高可信度③确认覆盖到所需
    后续 session 之后,才能用"D 不在可信交易日集合"判 `INVALID_ANALYSIS_DATE`——不能
    据弱/窄日历下此结论④T+1/T2 只从日历选,T+2 晚于 `today` → `PENDING_SESSION`。
    """
    today_c = _normalize_today(today)
    D = _compact_or_none(analysis_date)
    base: dict = {
        "analysis_date": D if D is not None else str(analysis_date), "today": today_c,
        "t1": None, "t2": None, "t5": None, "t10": None,
        "calendar_quality": "", "calendar_digest": "", "sessions": [],
    }
    if D is None:
        return {**base, "status": INVALID_ANALYSIS_DATE,
                "reason": "analysis_date 格式非法(非 YYYY-MM-DD/YYYYMMDD 合法日期)"}
    start, end = _shift(D, -_SESSION_LOOKBACK_DAYS), _shift(D, _SESSION_LOOKAHEAD_DAYS)
    sessions, quality, digest, err = _fetch_trusted_sessions(start, end, calendar=calendar)
    base.update(calendar_quality=quality, calendar_digest=digest, sessions=sessions)
    if err:
        return {**base, "status": UNVERIFIED_CALENDAR, "reason": f"日历请求异常:{err}"}
    if quality != TRADE_CAL_QUALITY:
        return {**base, "status": UNVERIFIED_CALENDAR,
                "reason": f"日历质量不可信:{quality or '空'}"}
    if not sessions:
        return {**base, "status": UNVERIFIED_CALENDAR, "reason": "日历为空"}
    after = [s for s in sessions if s > D]
    if len(after) < 2:
        return {**base, "status": UNVERIFIED_CALENDAR,
                "reason": "请求范围不完整:可信日历未覆盖到 T+2"}
    if D not in sessions:
        return {**base, "status": INVALID_ANALYSIS_DATE,
                "reason": "analysis_date 不是可信交易日历里的交易日"}
    t1, t2 = after[0], after[1]
    t5 = after[4] if len(after) >= 5 else None
    t10 = after[9] if len(after) >= 10 else None
    base.update(t1=t1, t2=t2, t5=t5, t10=t10)
    if t2 > today_c:
        return {**base, "status": PENDING_SESSION, "reason": "可信目标 T+2 尚未到"}
    return {**base, "status": "OK", "reason": ""}


def _trusted_predecessor(day: str, *, calendar) -> str | None:
    """`day`(紧凑或带横杠)在可信日历里**严格早于它**的最后一个 session。

    只有 `quality == trade_cal` 才采信;找不到/不可信一律 `None`——调用方(迟到执行锚)
    据此决定"无法核验时 `exec_gap_c1_o2` 保持 null",绝不滑到湖里恰好存在的更早分区。
    """
    d = _compact_or_none(day)
    if d is None:
        return None
    start = _shift(d, -_PREDECESSOR_LOOKBACK_DAYS)
    sessions, quality, _digest, err = _fetch_trusted_sessions(start, d, calendar=calendar)
    if err or quality != TRADE_CAL_QUALITY or not sessions:
        return None
    before = [s for s in sessions if s < d]
    return before[-1] if before else None


# ───────────────────────── 市场事实:精确日期 → 湖 → 前向收益 ─────────────────────────

def market_frame(date: str, *, lake_daily: Path | None = None,
                 calendar=None, today: object = None) -> tuple[pd.DataFrame | None, dict]:
    """当日全湖前向收益帧 + T+1 盘口(open/high/low/close/pct_chg)。

    `date`/`lake_daily` 与旧签名逐字兼容(正 T1);新增 `calendar`(缺省惰性默认到
    `exec_anchor.trading_sessions`,避免模块加载就拉 tushare)与 `today`(显式注入,
    缺省取真实当前时刻)。

    先调 `resolve_outcome_sessions` 把 D 的 T+1/T+2 定成精确日期(零行情读取),
    只有日历可信且 T+2 已到,才去按精确日期查湖——湖里没有 D/T+1/T+2 的分区就是
    `MISSING_MARKET_DATA`,**不**顺延到下一个存在的分区、不填零、不悄悄放行。

    `fr is None` 覆盖全部非成熟情形(`meta["outcome_status"]` 如实区分是哪一种,
    §2.2 的五态之一);`fr` 非空则 `meta["outcome_status"] == MATURE`。`meta` 恒含
    `outcome_status, reason, calendar_quality, calendar_digest, t1, t2,
    missing_sessions`;`MATURE` 时另附 `n`/`t5`/`t10`/`fwd5_verified`/`fwd10_verified`
    (5/10 日旁列的窗口是否可核验——`compute_outcome` 据此在行组装时做 null 门,主尺
    绝不因此被拖累)。
    """
    if calendar is None:
        from autoresearch.scan import exec_anchor as _anchor
        calendar = _anchor.trading_sessions
    resolved = resolve_outcome_sessions(date, calendar=calendar, today=today)
    meta: dict = {
        "outcome_status": resolved["status"],
        "reason": resolved["reason"],
        "calendar_quality": resolved["calendar_quality"],
        "calendar_digest": resolved["calendar_digest"],
        "t1": resolved["t1"],
        "t2": resolved["t2"],
        "missing_sessions": [],
    }
    if resolved["status"] != "OK":
        return None, meta
    D, t1, t2 = resolved["analysis_date"], resolved["t1"], resolved["t2"]
    present = set(_panel.lake_trade_days(lake_daily))
    missing = [d for d in (D, t1, t2) if d not in present]
    if missing:
        meta.update(outcome_status=MISSING_MARKET_DATA, missing_sessions=missing,
                    reason=f"行情缺失(湖无分区):{','.join(missing)}")
        return None, meta
    # 位置对齐用可信日历的 session 列表(已按 today 截断),不用湖文件列表——
    # 这正是本次修复的核心:湖缺的日子不再让后面的文件顶替成"下一个交易日"。
    window = [s for s in resolved["sessions"] if s <= resolved["today"]]
    piv = _panel.load_lake_pivots(window, lake_daily)
    fr = _fwd.forward_frame(piv, window, D)
    if fr is None or fr.empty:
        close_cols = set(piv.get("close", pd.DataFrame()).columns)
        still_missing = [d for d in (D, t1, t2) if d not in close_cols] or [D, t1, t2]
        meta.update(outcome_status=MISSING_MARKET_DATA, missing_sessions=still_missing,
                    reason="前向收益帧为空(分区文件存在但内容缺失)")
        return None, meta
    for col, key in (("t1_open", "open"), ("t1_high", "high"), ("t1_low", "low"),
                     ("t1_close", "close"), ("t1_pct_chg", "pct_chg")):
        series = piv.get(key)
        fr[col] = series[t1] if (series is not None and t1 in series.columns) else np.nan
    fr["t2_open"] = piv["open"][t2] if t2 in piv["open"].columns else np.nan
    span = fr["t1_high"] - fr["t1_low"]
    fr["t1_pos_in_range"] = ((fr["t1_close"] - fr["t1_low"]) / span).where(span > 0)
    meta["outcome_status"] = MATURE
    meta["reason"] = ""
    meta["n"] = int(len(fr))
    t5, t10, today_c = resolved.get("t5"), resolved.get("t10"), resolved["today"]
    meta["t5"], meta["t10"] = t5, t10
    # 5/10 日旁列的"是否可核验"——日历够远 + 已经到期 + 湖里真有那天的分区,三条同时
    # 成立才算数;任何一条不满足都是 False,行组装(`compute_outcome`)据此把
    # `fwd_5_oc`/`fwd_10_oc` 置 null,绝不因此拖累上面已经判定的 `MATURE` 主尺。
    meta["fwd5_verified"] = bool(t5 and t5 <= today_c and t5 in present)
    meta["fwd10_verified"] = bool(t10 and t10 <= today_c and t10 in present)
    return fr, meta


def _relative_columns(fr: pd.DataFrame, sectors: dict[str, str]) -> pd.DataFrame:
    """三个相对列。分母各不相同,**列名即口径**(见模块 docstring 的表)。"""
    ok = _ruler.entry_tradable(fr, ruler_name=MAIN)
    gap = pd.to_numeric(fr[MAIN], errors="coerce")
    base = gap[ok & gap.notna()]
    out = pd.DataFrame(index=fr.index)
    out[_ruler.REL_MARKET] = gap - base.mean() if len(base) else np.nan
    out["excess_med_market"] = gap - base.median() if len(base) else np.nan
    sec = pd.Series({c: sectors.get(c, "") for c in fr.index})
    sec_mean = base.groupby(sec.reindex(base.index)).mean()
    out[_ruler.REL_SECTOR] = gap - sec.map(sec_mean).where(sec.astype(str) != "")
    return out


def _num(value) -> float | None:
    if value is None:
        return None
    try:
        f = float(value)
    except (TypeError, ValueError):
        return None
    return None if pd.isna(f) else round(f, 6)


def exec_anchor_frame(execution: dict | None, *, lake_daily: Path | None = None,
                      calendar=None, today: object = None
                      ) -> tuple[pd.DataFrame | None, dict]:
    """迟到 run 的**反事实**帧:主尺改从第一个真正来得及的尾盘起算。

    正常 run(`exec_lag == 0`)返回 `(None, ...)` —— 锚点与主帧逐字相同,再算一遍纯属
    浪费。只有报告在 T+1 收盘之后才就绪的那些 run 才有第二个锚点,而它们的主帧记的是
    一笔**下不了的单**(61 个 run 里 8 个,13%)。

    2026-09-12 修复:锚点(`first_available_session` 的**前一个**交易日)改用**可信
    日历**定位(`_trusted_predecessor`),不再用湖分区列表猜前一天——旧写法在真前驱
    缺湖时会悄悄滑到更早的、湖里恰好存在的那个分区,把反事实算到错误的日子上。

    `forward_returns` 的买腿恒为 D+1,所以把前驱日当 D,它的 D+1 正好落在
    `first_available_session` 上,口径与主帧逐字同源(同一 `forward_returns`、同一
    `GAP_CLIP`),不另造一把尺。

    2026-09-12 fix round 1(review finding 1):返回 `(fr, meta)`——`fr is None` 曾经
    同时代表三种完全不同的情形,调用方分不出来:①根本没有 exec_lag(正常 run,不适用,
    不是失败)②前驱 session 在可信日历里定位不出来③前驱定位到了,但它自己的
    T+1/T+2 行情不可核验/缺失/未成熟。`meta["exec_outcome_status"]` 把三者拆开:
    ①是 `None`(不适用);②③直接复用**主尺同一份状态词表**——③是把前驱日当 D 重新
    调一次 `market_frame` 算出来的 `outcome_status`(可能是 `MATURE`/`PENDING_SESSION`/
    `MISSING_MARKET_DATA`/`UNVERIFIED_CALENDAR`/`INVALID_ANALYSIS_DATE` 中的任何一个,
    原样转发,不折叠);②是 `_trusted_predecessor` 自己就找不到可信前驱,统一记
    `UNVERIFIED_CALENDAR`(根因同属"日历不可信",与③共享词表但语义不同,靠 `reason`
    与上一层调用上下文区分)。

    `exec_outcome_status` 与主尺的 `outcome_status` **恒正交**:本函数只在内部另起一次
    独立的 `market_frame(anchor_date, ...)` 调用,不读、不写调用方的主 `fr`/`meta`,
    因此 exec 侧失败绝不会让已经判定 `MATURE` 的主尺被拖累,反之亦然
    (`compute_outcome` 侧还用 `contextlib.suppress(Exception)` 再加一层保险)。
    """
    first = str((execution or {}).get("first_available_session") or "")
    if not first or not (execution or {}).get("exec_lag"):
        return None, {"exec_outcome_status": None, "reason": "", "anchor_session": None}
    if calendar is None:
        from autoresearch.scan import exec_anchor as _anchor
        calendar = _anchor.trading_sessions
    anchor = _trusted_predecessor(first, calendar=calendar)
    if anchor is None:
        return None, {"exec_outcome_status": UNVERIFIED_CALENDAR,
                      "reason": "迟到锚的前一交易日无法用可信日历核验", "anchor_session": None}
    fr, meta = market_frame(_dashed(anchor), lake_daily=lake_daily, calendar=calendar, today=today)
    return fr, {"exec_outcome_status": meta["outcome_status"], "reason": meta["reason"],
               "anchor_session": anchor}


def compute_outcome(run_dir: Path | str, *, lake_daily: Path | None = None,
                    calendar=None, today: object = None) -> dict | None:
    """一次 run → 结果文档。

    `calendar`/`today` 原样转发给 `market_frame`/`exec_anchor_frame`(§2.1 的显式
    `today` 注入):缺省时两者各自惰性默认到 `exec_anchor.trading_sessions` 与真实
    当前时刻,测试可注入合成日历与固定 `today` 让"成熟与否"完全确定性可控。

    2026-09-12 §2(ruling #2,C1 re-review 遗留给 C2 的那半):只有 run/facts **本身**
    定位不到(没有数据日,或当天一只票都没记录到)才沿用旧的裸 `None` 返回——`fill`
    据此记「跳过原因」(见其 `skip_reasons`)。只要 run/facts 定位得到,不管
    `market_frame` 判成五态里的哪一态,本函数都返回一份**带状态的文档**,不再用裸
    `None` 丢掉原因:`outcome_status`/`reason`/`calendar_quality`/`calendar_digest`/
    `t1`/`t2` 恒在 doc 顶层。非 `MATURE` 时 `complete=False`、`rows={}`——**没有可汇总
    的主尺数值**,状态本身就是那一行缺席的原因,不是数字旁边的装饰(不允许一份非
    `MATURE` 文档携带任何可 sum 的主尺值)。
    """
    run = Path(run_dir)
    facts = run_facts(run)
    date = facts["analysis_date"]
    if not date or not facts["rows"]:
        return None
    fr, meta = market_frame(date, lake_daily=lake_daily, calendar=calendar, today=today)
    # 时间锚(§2.4 G1):这份报告到底什么时候才能下单。正常 run 与主帧同锚;
    # 迟到 run 另算一份反事实帧,**两者绝不混算**(列名即人口)。与主尺的成熟与否
    # 无关——即便主尺非 MATURE,run 自己"何时才就绪"仍是一条独立、随时可读的事实。
    from autoresearch.scan import exec_anchor as _anchor

    execution = _anchor.read_execution(run)
    doc: dict = {
        "schema_version": OUTCOME_SCHEMA_VERSION,
        "run_id": run.name,
        "contract_run_id": facts["contract_run_id"],
        "analysis_date": date,
        "ruler": MAIN,
        "outcome_status": meta.get("outcome_status") or "",
        "reason": meta.get("reason") or "",
        "calendar_quality": meta.get("calendar_quality") or "",
        "calendar_digest": meta.get("calendar_digest") or "",
        "t1": meta.get("t1"), "t2": meta.get("t2"),
        "decision_mode": facts["decision_mode"],
        "rule_version": facts["rule_version"],
        "read_from_shared_staging": facts["used_shared"],
        "exec_line": {"max_pct_1d": EXEC_MAX_PCT_1D, "max_pos_in_range": EXEC_MAX_POS_IN_RANGE},
        "execution": execution,
    }
    if fr is None:
        doc.update(complete=False, n_rows=len(facts["rows"]), n_scored=0, rows={})
        return doc
    exec_fr, exec_status = None, None
    with contextlib.suppress(Exception):
        exec_fr, exec_meta = exec_anchor_frame(execution, lake_daily=lake_daily,
                                               calendar=calendar, today=today)
        exec_status = exec_meta.get("exec_outcome_status")
    sectors = {code: str(row.get("sector") or "") for code, row in facts["rows"].items()}
    rel = _relative_columns(fr, sectors)
    ok_entry = _ruler.entry_tradable(fr, ruler_name=MAIN)
    # 5/10 日旁列的 null 门(2026-09-12 §2.3 末条,ruling #7):窗口未核验/不完整时
    # 置 null,但**绝不能拖累上面已经判定的隔夜主尺**——`fr`/`meta["outcome_status"]`
    # 已经是 MATURE 了,这里只决定 `fwd_5_oc`/`fwd_10_oc` 两列各自要不要置 null。
    fwd5_ok = bool(meta.get("fwd5_verified"))
    fwd10_ok = bool(meta.get("fwd10_verified"))

    rows: dict[str, dict] = {}
    for code, row in sorted(facts["rows"].items()):
        m = fr.loc[code] if code in fr.index else None
        pct1 = _num(m["t1_pct_chg"]) if m is not None else None
        pos = _num(m["t1_pos_in_range"]) if m is not None else None
        buyable = bool(ok_entry.loc[code]) if (m is not None and code in ok_entry.index) else None
        exec_ok = None
        if pct1 is not None and pos is not None and buyable is not None:
            exec_ok = bool(buyable and pct1 <= EXEC_MAX_PCT_1D and pos < EXEC_MAX_POS_IN_RANGE)
        rows[code] = {
            **{k: row.get(k) for k in ("code", "name", "sector", "role", "lane", "guard",
                                       "conviction", "rating", "early_stop_reason",
                                       "e6_rank", "e6_eligible", "e6_buy")},
            "buyable_c1": buyable,
            "t1_open": _num(m["t1_open"]) if m is not None else None,
            "t1_high": _num(m["t1_high"]) if m is not None else None,
            "t1_low": _num(m["t1_low"]) if m is not None else None,
            "t1_close": _num(m["t1_close"]) if m is not None else None,
            "t1_pct_chg": pct1,
            "t1_pos_in_range": pos,
            "exec_ok": exec_ok,
            "t2_open": _num(m["t2_open"]) if m is not None else None,
            MAIN: _num(m[MAIN]) if m is not None else None,
            _ruler.REL_MARKET: _num(rel.loc[code, _ruler.REL_MARKET]) if code in rel.index else None,
            _ruler.REL_SECTOR: _num(rel.loc[code, _ruler.REL_SECTOR]) if code in rel.index else None,
            "excess_med_market": _num(rel.loc[code, "excess_med_market"]) if code in rel.index else None,
            "fwd_5_oc": _num(m["fwd_5_oc"]) if (m is not None and fwd5_ok) else None,
            "fwd_10_oc": _num(m["fwd_10_oc"]) if (m is not None and fwd10_ok) else None,
            "exec_gap_c1_o2": (_num(exec_fr.loc[code, MAIN])
                               if exec_fr is not None and code in exec_fr.index else None),
            # review finding 1(2026-09-12 fix round 1):exec 侧状态,doc 级单值逐行广播
            # (与 `_ledger_rows` 里 anchor_session/exec_lag/actionability 同一手法)。
            # Task C2 把它接进 `recommendations.csv`(`LEDGER_COLUMNS` 新增的
            # `exec_outcome_status` 列——见 `_ledger_rows` 的 allowlist)。
            "exec_outcome_status": exec_status,
        }
    # 「成熟」= 主尺算得出来的行占多数。少数票停牌/新股缺数是常态,不该让整份结果反复重算。
    n_scored = sum(1 for r in rows.values() if r.get(MAIN) is not None)
    doc.update({
        "complete": bool(rows) and n_scored >= max(1, len(rows) // 2),
        "n_rows": len(rows), "n_scored": n_scored,
        "rows": rows,
    })
    return doc


# ───────────────────────── 落盘:逐 run JSON + 跨 run CSV ─────────────────────────

def outcome_path(run_id: str, reports_root: Path | None = None) -> Path:
    return ledger_root(reports_root) / "outcome" / f"{run_id}.json"


def write_outcome(doc: dict, reports_root: Path | None = None) -> Path:
    p = outcome_path(doc["run_id"], reports_root)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_name(f"{p.name}.tmp")
    tmp.write_text(json.dumps(doc, ensure_ascii=False, sort_keys=True, indent=1),
                   encoding="utf-8")
    tmp.replace(p)
    return p


def _ledger_rows(doc: dict) -> list[dict]:
    """`doc["rows"]` 为空(非 `MATURE`,§2)时返回 `[]`——CSV 里没有一行属于这个 run,
    这正是撤回(withdrawal,bullet 4)与整体替换(bullet 5)在这一层的落点。
    """
    stamp = doc.get("computed_at") or ""
    anchor = doc.get("execution") or {}
    out = []
    for code, row in sorted(doc["rows"].items()):
        out.append({
            "run_id": doc["run_id"], "analysis_date": doc["analysis_date"], "code": code,
            "mode": doc.get("decision_mode") or "",
            "src": "shared" if doc.get("read_from_shared_staging") else "run",
            # 日历完整性五列(2026-09-12 §2,schema 2):doc 级单值逐行广播,同
            # anchor_session/exec_lag/actionability 的既定手法——它们回答的是「这整个
            # run 的结果算没算出来、算得对不对」,不是某一只票独有的属性。
            "outcome_status": doc.get("outcome_status") or "",
            "calendar_quality": doc.get("calendar_quality") or "",
            "calendar_digest": doc.get("calendar_digest") or "",
            "t1": doc.get("t1"), "t2": doc.get("t2"),
            **{k: row.get(k) for k in LEDGER_COLUMNS
               if k not in ("run_id", "analysis_date", "mode", "src", "code",
                            "outcome_status", "calendar_quality", "calendar_digest",
                            "t1", "t2", "ruler", "computed_at", "anchor_session",
                            "exec_lag", "actionability")},
            "ruler": doc["ruler"],
            # 三列同源于 doc 级 execution(逐行相同):读 BUY 战绩前先看 actionability。
            "anchor_session": anchor.get("first_available_session"),
            "exec_lag": anchor.get("exec_lag"),
            "actionability": anchor.get("actionability_status"),
            "computed_at": stamp,
        })
    return out


def upsert_ledger(doc: dict, reports_root: Path | None = None) -> int:
    """按 `(run_id, code)` 幂等 upsert 进跨 run 索引 CSV;返回本次写入/更新的行数。

    幂等是硬要求:`fill` 每晚可能对同一个 run 重算(结果尚未成熟时会重试),重复 append
    会让「BUY n 笔」这种最基础的计数直接翻倍。

    2026-09-12 §5(bullet 5)——**一个 run 的行整体替换,不是合并**:写回之前先把这个
    `run_id` 名下的全部旧行丢弃,再整批放回这次算出来的新集合(可能是空集合——重算
    从 MATURE 收缩成非 MATURE 时就是这样,即撤回/withdrawal,见 bullet 4)。只
    upsert 新出现的非空行、残留旧行不管,会让重算把行数从 N 个收缩到 0 个时,昨天那
    N 行原样留在表里,和今天的失败状态并排展示成"两个版本各展示一半"的错觉——这正是
    这份 CSV 存在的意义要防的事。这个替换严格按 `run_id` 划界,不影响其它 run 的行
    (verified by `test_whole_run_replace_does_not_touch_other_runs_rows`)。
    """
    path = ledger_root(reports_root) / LEDGER_CSV
    path.parent.mkdir(parents=True, exist_ok=True)
    existing: dict[tuple[str, str], dict] = {}
    if path.is_file():
        with path.open(encoding="utf-8-sig", newline="") as fh:
            for r in csv.DictReader(fh):
                existing[(str(r.get("run_id", "")), _z6(r.get("code")))] = r
    run_id = str(doc["run_id"])
    existing = {key: row for key, row in existing.items() if key[0] != run_id}
    fresh = _ledger_rows(doc)
    for r in fresh:
        existing[(r["run_id"], r["code"])] = r
    with path.open("w", encoding="utf-8", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=list(LEDGER_COLUMNS), extrasaction="ignore")
        w.writeheader()
        for key in sorted(existing):
            w.writerow({k: existing[key].get(k, "") for k in LEDGER_COLUMNS})
    return len(fresh)


def load_ledger(reports_root: Path | None = None) -> list[dict]:
    path = ledger_root(reports_root) / LEDGER_CSV
    if not path.is_file():
        return []
    with path.open(encoding="utf-8-sig", newline="") as fh:
        return list(csv.DictReader(fh))


def published_runs(reports_root: Path | None = None) -> list[Path]:
    """已发布 run 目录。两种目录名都认(新 `20260825-0826_2000` / legacy `20260826_2000`)。

    判据从「`[:2]=='20'` 且含下划线」收紧到 `run_naming.is_run_dir` —— 旧判据会把
    `_ledger`/`_failed`/`_capsule_archive` 之外任何以 20 开头带下划线的目录都当 run
    (它们只是恰好没有 `manifest.json` 才没出事)。
    """
    base = Path(reports_root or (ws.reports_root() / "scan"))
    if not base.is_dir():
        return []
    return sorted(p for p in base.iterdir()
                  if p.is_dir() and is_run_dir(p.name)
                  and (p / "manifest.json").is_file())


def _is_settled(doc: object) -> bool:
    """安全跳过(不重算、不重写)的唯一条件:schema 2、日历可信(`trade_cal`)、
    `outcome_status == MATURE`、且 `complete`——四条同时成立才算。

    2026-09-12 §5(bullet 3/C10):旧代码只看 `complete` 一个布尔,schema 1 的旧文档
    (当年"湖分区排序位置"算出来的日期,可能整个错位)一旦 `complete=True` 就被永久
    信任、再也不会被重新核验。schema 2 但缺日历质量字段的文档(同样是磁盘上真实可能
    出现的形状——例如被外部工具手写、或未来某次迁移中途产物)同理不能被信任。这四条
    与 `actionability` 的既定纪律同源:缺字段/字段不是恰好那个可信字面量,一律当作
    "还没核验过",不是"已核验通过"。
    """
    if not isinstance(doc, dict):
        return False
    return (doc.get("schema_version") == OUTCOME_SCHEMA_VERSION
            and doc.get("calendar_quality") == TRADE_CAL_QUALITY
            and doc.get("outcome_status") == MATURE
            and bool(doc.get("complete")))


def fill(*, reports_root: Path | None = None, lake_daily: Path | None = None,
         limit: int | None = None, now: str | None = None,
         rebuild: bool = False, calendar=None, today: object = None) -> dict:
    """回填全部**未核验或未算过**的已发布 run。

    返回 `{filled, skipped, rows, runs, skip_reasons}`——`skip_reasons` 是
    `{run_id: 原因}`,2026-09-12 ruling #2 要求:即便一次 `fill()` 决定不重算某个 run,
    也不能连"为什么跳过"都是静默的(`already_verified_complete` = 已核验成熟,安全跳过;
    `run_or_facts_not_locatable` = `compute_outcome` 连 run/facts 都定位不到,唯一仍
    返回裸 `None` 的情形)。

    幂等 + 增量:只有 `_is_settled` 判真的 run 才直接跳过(不重算、不重写),所以每天
    跑它的成本只与「昨天新出的 run + 还没被核验通过的老 run」成正比——schema 1、或
    schema 2 但缺日历质量字段的旧文档都不在"安全跳过"之列,会被重新核验(bullet 3)。

    只要 `compute_outcome` 返回了文档(不是裸 `None`),就会被写盘并计入 `filled`——
    哪怕它不是 `MATURE`:2026-09-12 起,一份带 `outcome_status`/`reason` 的"还没成熟"
    文档本身就是有效产出(ruling #2),不再是"没算出来就什么都不留"。

    撤回(withdrawal,bullet 4):如果磁盘上的旧文档曾经 `complete=True`,而这次重算
    的新文档不再 `complete`(核验通不过了,或数据被发现缺失),旧的行级数值不会被
    悄悄丢弃——整份旧文档的一个紧凑快照存进新文档的 `previous` 键(不进 `rows`,不会
    被 `_ledger_rows`/CSV/任何统计读到),新文档本身按非 MATURE 的规则写盘
    (`rows={}`),CSV 里这个 run 的旧行随之被整体撤下(`upsert_ledger` 的整体替换,
    见其 docstring)。前镜像因此"留了痕但不再算数",不是被销毁,也不会跟新的失败
    状态并排展示成一个还有效的数字。

    `calendar`/`today` 原样转发给 `compute_outcome`(缺省即生产默认:真实交易日历、
    真实当前时刻);测试可注入两者让整条回填链路的"成熟与否"确定性可控,不依赖
    真实 tushare/挂钟时间。与 `--today` CLI 参数(`now`,只用于 `computed_at` 留痕
    时间戳)是两个不同的概念,不要混用。
    """
    filled, skipped, n_rows, touched = 0, 0, 0, []
    skip_reasons: dict[str, str] = {}
    for run in published_runs(reports_root):
        existing = None
        p = outcome_path(run.name, reports_root)
        if p.is_file():
            with contextlib.suppress(OSError, json.JSONDecodeError):
                existing = json.loads(p.read_text(encoding="utf-8"))
        # `rebuild`:口径变了才需要重算已核验通过的 run。默认关着 —— 每晚跑的成本
        # 必须只与「昨天新出的 + 还没核验通过的」成正比,而不是与历史长度成正比。
        if not rebuild and _is_settled(existing):
            skipped += 1
            skip_reasons[run.name] = "already_verified_complete"
            continue
        doc = compute_outcome(run, lake_daily=lake_daily, calendar=calendar, today=today)
        if doc is None:
            skipped += 1
            skip_reasons[run.name] = "run_or_facts_not_locatable"
            continue
        if isinstance(existing, dict) and existing.get("complete") and not doc.get("complete"):
            # 撤回:前镜像存档,不进 `rows`(不会被任何统计读到)。
            doc["previous"] = {k: existing.get(k) for k in
                               ("schema_version", "outcome_status", "calendar_quality",
                                "complete", "t1", "t2", "rows")}
        doc["computed_at"] = now or ""
        write_outcome(doc, reports_root)
        n_rows += upsert_ledger(doc, reports_root)
        filled += 1
        touched.append(run.name)
        if limit and filled >= limit:
            break
    return {"filled": filled, "skipped": skipped, "rows": n_rows, "runs": touched,
            "skip_reasons": skip_reasons}


# ───────────────────────── 读数(prelude 汇总屏一行)─────────────────────────

MIN_LEDGER_N = 20


def ledger_line(reports_root: Path | None = None) -> str:
    """汇总屏一行 —— **仅人看,不喂任何 agent、不进 brief**(同 l4_rejection 日读的边界)。

    <20 笔时只印「攒样本 n/20」不印均值:小样本均值会被当成结论读,而这条线存在的意义
    正是不让人再凭印象说「最近推荐得挺准」。

    均值印的是**毛收益**(`gap_c1_o2`,推荐票的前向收益)——本模块从未接入 broker,
    没有任何输入能证明一笔真的成交了,所以这里绝不能把它说成"实际成交"或"净收益"
    (2026-09-12 §7 C14:标签与人口不能混)。
    """
    rows = load_ledger(reports_root)
    if not rows:
        return "结果账本:空(还没回填过 —— `python -m autoresearch.scan.outcome fill`)"
    # 只数 **active 期**的 BUY:shadow 期的那几笔在当天报告里明写「非正式·不执行」,
    # 混进来就是把没执行过的东西算进战绩(且其中两笔的决策文件还是被影子回放改写出来的)。
    all_buys = [r for r in rows
                if str(r.get("e6_buy")).lower() == "true" and str(r.get("mode")) == "active"]
    shadow_n = sum(1 for r in rows if str(r.get("e6_buy")).lower() == "true"
                   and str(r.get("mode")) != "active")
    # 时间锚(§2.4 G1):报告在 T+1 收盘之后才就绪的那些 run,主尺买腿是**已经过去的价格**。
    # 它们不进主均值(否则 13% 的假单被算进战绩),单列一个计数如实说明被排除了几笔。
    # 老行没有这一列(账本先于本波)→ 视为未知,同样不进主均值,计入 `unknown_n`。
    buys = [r for r in all_buys if str(r.get("actionability") or "") == "ACTIONABLE"]
    late_n = sum(1 for r in all_buys
                 if str(r.get("actionability") or "") in {"LATE_REVALIDATION_REQUIRED", "EXPIRED"})
    unknown_n = len(all_buys) - len(buys) - late_n
    # 日历完整性(2026-09-12 §2,同一条既定纪律):迁移前的老行(schema 1,或 schema 2
    # 但缺这两列)与本次重算判定"日历不可信/行情缺失"的新行一样,都不能被算进已核验的
    # 均值——`str(x or "")` 让"缺列"与"这一列恰好是空字符串"得到同一个待遇,只有
    # 恰好等于 `MATURE`/`trade_cal` 两个字面量才算通过。
    verified = [r for r in buys if str(r.get("outcome_status") or "") == MATURE
               and str(r.get("calendar_quality") or "") == TRADE_CAL_QUALITY]
    unverified_n = len(buys) - len(verified)
    scored = [r for r in verified if _num(r.get(MAIN)) is not None]
    if len(scored) < MIN_LEDGER_N:
        return (f"结果账本:{len(rows)} 行 · active BUY {len(all_buys)} 笔"
                f"(可执行 {len(buys)}·已成熟 {len(scored)})"
                + (f" · 另 shadow 期 {shadow_n} 笔不计" if shadow_n else "")
                + (f" · 迟到 {late_n} 笔不计" if late_n else "")
                + (f" · 锚未知 {unknown_n} 笔不计" if unknown_n else "")
                + (f" · 日历未验证 {unverified_n} 笔不计" if unverified_n else "")
                + f" · 攒样本 {len(scored)}/{MIN_LEDGER_N},不印均值")
    gaps = [_num(r.get(MAIN)) for r in scored]
    rel = [_num(r.get(_ruler.REL_MARKET)) for r in scored
           if _num(r.get(_ruler.REL_MARKET)) is not None]
    ok = [r for r in scored if str(r.get("exec_ok")).lower() == "true"]
    ok_gaps = [_num(r.get(MAIN)) for r in ok]
    txt = (f"结果账本:BUY {len(scored)} 笔 · 毛 gap {100 * float(np.mean(gaps)):+.2f}pp"
           f" · 相对市场 {100 * float(np.mean(rel)):+.2f}pp" if rel else
           f"结果账本:BUY {len(scored)} 笔 · 毛 gap {100 * float(np.mean(gaps)):+.2f}pp")
    if ok_gaps:
        txt += (f" · 执行线内 {len(ok_gaps)} 笔 {100 * float(np.mean(ok_gaps)):+.2f}pp")
    if late_n or unknown_n or unverified_n:
        txt += f" · 排除迟到 {late_n}/锚未知 {unknown_n}/日历未验证 {unverified_n} 笔"
    return txt + f" · 主尺 {MAIN}(推荐毛收益·可执行口径;只记不学;仅人看;未接 broker,非实际成交)"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="结果账本(确定性、只记不学)")
    ap.add_argument("command", choices=["fill", "line"])
    ap.add_argument("--limit", type=int, default=None, help="本次最多回填几个 run")
    ap.add_argument("--today", default=None, help="computed_at 时间戳(留痕用)")
    ap.add_argument("--rebuild", action="store_true",
                    help="连已成熟的 run 一起重算(口径变更后用;默认增量)")
    args = ap.parse_args(argv)
    if args.command == "line":
        print(ledger_line())
        return 0
    res = fill(limit=args.limit, now=args.today, rebuild=args.rebuild)
    print(json.dumps({"ok": True, **res}, ensure_ascii=False, sort_keys=True))
    print(ledger_line())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
