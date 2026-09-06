#!/usr/bin/env python3
"""运行日历三视图 —— 「那天跑没跑、选了哪一份、市场怎么走」(确定性、零 LLM、**只记不学**)。

design: docs/specs/2026-08-28-ruler-retention-optimization-brainstorm.md §2.4 G2

## 病灶

`recommendations.csv` 只记**推荐了谁**,不记**那天有没有推荐这件事本身**。于是账本里有
三种日子长得一模一样,都是"没有行":

    没跑扫描的日子      跑了但一只都没买(0-BUY)     跑了但报告没获批
    ↑ 什么都没发生       ↑ 系统主动判断了"空仓"        ↑ 判断作废了

把它们混成一桶,「今天该不该出手」这个整链主 KPI 就永远算不出来 —— 用 0-BUY 日的市场
涨跌去证明"空仓正确",分母里混进了根本没开工的日子。**`NO_RUN` 绝不能被下游当成
0-BUY 日**,这是本模块存在的第一理由。

第二理由是**同日多 run 会被压扁**。61 个已发布 run 里有同数据日重跑、周末跑、失败后
重跑;任何"同一天只取最后一个"的读法都会把失败那次从历史里抹掉 —— 而"那次为什么失
败、重跑改了什么"正是复盘要问的。所以 `runs.csv` **一行一个 run,保留全部尝试**。

第三理由是**市场行**:没有它,0-BUY 日、哨兵日、没跑的日子在账本里全是空白,不是
"空仓正确/错误"。

## 三张 view(可重建的物化视图,不是事实源)

    $RPT/scan/_ledger/views/runs.csv              一行一个 run(全部尝试,不去重)
    $RPT/scan/_ledger/views/session_calendar.csv  一个交易 session 一行(来源 trade_cal)
    $RPT/scan/_ledger/views/market.csv            一个交易日一行(四把可执行尺 + 可买人口)
    $RPT/scan/_ledger/views/_health.json          运维健康(**唯一**带时间戳的文件)
    $RPT/scan/_ledger/notes.jsonl                 人工备注(append-only,机器只写不读)

事实源是 `manifest.json` / capsule 账本 / `_failed/` / 数据湖;这三张表随时可以删掉重建。
所以它们**不写时间戳**:同一批输入重复跑必须 byte 相同,否则"这张表变了"就再也不能
当成"有新事实"的信号。时间戳只进 `_health.json`。

## 只记不学

不回注任何 prompt、不改任何门/权重/评级、不产生 proposal。消费者只有两个:人,与
prelude 汇总屏的一行读数(`line`)。`notes.jsonl` 是**人**写给人看的,`build` 永远不读
它 —— 机器绝不编造 `NO_RUN` 的原因。

  uv run --no-sync python -m autoresearch.scan.ledger_views build [--limit 60]
  uv run --no-sync python -m autoresearch.scan.ledger_views note 2026-08-26 "机器没跑,我在开会"
  uv run --no-sync python -m autoresearch.scan.ledger_views line
"""
from __future__ import annotations

import argparse
import contextlib
import csv
import io
import json
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

from autoresearch.common import ruler as _ruler, workspace as ws
from autoresearch.common.forward_returns import _board_limit
from autoresearch.data import market_panel as _panel
from autoresearch.scan import exec_anchor as _anchor, outcome as _outcome, run_naming as _naming

VIEWS_SCHEMA_VERSION = 1
VIEWS_DIRNAME = "views"
RUNS_CSV = "runs.csv"
SESSIONS_CSV = "session_calendar.csv"
MARKET_CSV = "market.csv"
NOTES_JSONL = "notes.jsonl"
HEALTH_JSON = "_health.json"

#: 四把「T+1 买 / T+2 卖」框内可执行的尺。主尺 `c1o2` = `ruler.MAIN_RULER`(2026-08-05
#: 裁定),其余三把**只观察不判读** —— 本表并列它们是为了让"换尺看起来会不会更好看"
#: 这个问题有据可查,不是换尺提案(用户 2026-08-28 重申执行时点不变)。
MARKET_RULERS = ("c1o2", "o1o2", "o1c2", "c1c2")
#: 每把尺的**买腿**,决定它的可买人口过滤:
#:   c1 腿 = T+1 收盘买 → 当日封涨停买不进;
#:   o1 腿 = T+1 开盘买 → 一字板买不进(封板 ∧ 开盘即最高)。
#: 拿不可成交的票去比可成交的基线,是 2026-08-28 附录 A 旧探针被自己 review 掉的原因。
RULER_LEG = {"c1o2": "c1", "c1c2": "c1", "o1o2": "o1", "o1c2": "o1"}

#: 一行 session 的四种终态。`SELECTED` 之外三种都**不是** 0-BUY:
#:   NO_RUN            那天根本没开工(下游拿它当 0-BUY 就是在伪造"系统判断空仓")
#:   NO_APPROVED_RUN   跑了,但没有一个 SUCCEEDED ∧ ACTIONABLE 的 run
#:   DATA_MISSING      有 run 落在这附近,但证据不足以证明它何时可用 —— 不猜
SESSION_SELECTED = "SELECTED"
SESSION_NO_RUN = "NO_RUN"
SESSION_NO_APPROVED_RUN = "NO_APPROVED_RUN"
SESSION_DATA_MISSING = "DATA_MISSING"

#: 每个指标各自成熟,**没有一枚总的 `complete` 布尔**:gap 类 D+2 成熟,fwd5/fwd10 要
#: 到 D+5/D+10。本表四把尺全是 gap 类(同一成熟日),所以只有一列 `status`;哪天往这里
#: 加 fwd 列,必须给它自己的 status 列,不能挂靠这一列(那正是 `outcome.complete` 把
#: fwd5/10 永久冻成缺失的老病)。
STATUS_PENDING = "PENDING"
STATUS_MATURE = "MATURE"
STATUS_UNAVAILABLE = "UNAVAILABLE"

RUNS_COLUMNS = (
    # `identity_quality` / `ready_quality` 是**读这张表之前必须先看的两列**:
    #  - `identity_quality=legacy`  这个 run 没有 capsule run_id,主键只能退到目录名;
    #  - `ready_quality=estimated`  批准时点是从 `manifest.generated_at` 估的下界,
    #    不是可证明的 GATE4 批准时刻 —— 拿它算 lead time 会系统性偏早。
    "engine", "capsule_run_id", "report_dir_id", "run_local_date", "analysis_date",
    "run_mode", "business_status", "evidence_status", "actionability_status",
    "decision_approved_at", "first_available_session", "exec_lag",
    "n_finalist", "n_buy", "identity_quality", "ready_quality",
)

SESSION_COLUMNS = (
    # `source` = 这一行的**交易日历**从哪来(trade_cal / lake_partitions /
    # weekday_heuristic)。无网络时退化是允许的,静默退化不是:`weekday_heuristic`
    # 不认识节假日,看到它就该把"那天是交易日"这个断言降级。
    "session", "run_ids_ready_before_cutoff", "selected_run_id",
    "selection_reason", "status", "source",
)

MARKET_COLUMNS = (
    "date", "n_buyable_c1", "n_buyable_o1",
    "mean_c1o2", "median_c1o2", "mean_o1o2", "median_o1o2",
    "mean_o1c2", "median_o1c2", "mean_c1c2", "median_c1c2",
    "regime", "temperature", "status",
)

#: 与 `outcome.MIN_LEDGER_N` 同一条理由:小样本均值会被当成结论读。
MIN_SESSION_N = 20


# ───────────────────────── 落点(`_ledger` 唯一事实源在 outcome) ─────────────────────────

def views_root(reports_root: Path | None = None) -> Path:
    """`$RPT/scan/_ledger/views/` —— 走 `outcome.ledger_root`,不另写一处 `_ledger` 字面量。"""
    return _outcome.ledger_root(reports_root) / VIEWS_DIRNAME


def notes_path(reports_root: Path | None = None) -> Path:
    return _outcome.ledger_root(reports_root) / NOTES_JSONL


def health_path(reports_root: Path | None = None) -> Path:
    return views_root(reports_root) / HEALTH_JSON


def _scan_root(reports_root: Path | None = None) -> Path:
    return Path(reports_root or (ws.reports_root() / "scan"))


def _read_json(path: Path) -> dict:
    if not path.is_file():
        return {}
    with contextlib.suppress(OSError, json.JSONDecodeError, UnicodeDecodeError):
        doc = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(doc, dict):
            return doc
    return {}


# ───────────────────────── 原子写(中途 kill 不留半张表) ─────────────────────────

def _cell(value: object) -> str:
    """一个格子的**确定性**文本。浮点先 round(6) 再 str:同一输入必须给同一 byte。"""
    if value is None:
        return ""
    if isinstance(value, bool):
        return "True" if value else "False"
    if isinstance(value, (float, np.floating)):
        number = float(value)
        if not np.isfinite(number):
            return ""
        return str(round(number, 6) + 0.0)      # `+ 0.0` 抹掉 -0.0,否则同值两种写法
    if isinstance(value, (int, np.integer)):
        return str(int(value))
    return str(value)


def render_csv(columns: tuple[str, ...], rows: list[dict]) -> str:
    buf = io.StringIO(newline="")
    writer = csv.DictWriter(buf, fieldnames=list(columns), extrasaction="ignore",
                            lineterminator="\n")
    writer.writeheader()
    for row in rows:
        writer.writerow({key: _cell(row.get(key)) for key in columns})
    return buf.getvalue()


def atomic_write_text(path: Path, text: str) -> Path:
    """先写 `.tmp` 再 `replace` —— 半张表比没有表更危险(它长得像一张完整的表)。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f"{path.name}.tmp")
    tmp.write_text(text, encoding="utf-8")
    tmp.replace(path)
    return path


# ───────────────────────── view ①:runs.csv(全部尝试,不去重) ─────────────────────────

def _capsule_rows(scan: Path) -> dict[str, dict]:
    """capsule 账本 → `{run_id: 最后一个有效 revision}`。

    这是**终态状态机**的记录,与 `manifest` 冲突时它说了算:manifest 在发布早段就写死
    `business_status=SUCCEEDED`(那一刻业务确实成功了),而 finalize 之后的终态只有账本
    知道。`read_valid_ledger` 会在第一处断链停下 —— 被篡改的尾巴不能改写它前面的行。
    """
    from autoresearch.trace import capsule as _capsule

    path = _outcome.ledger_root(scan) / _capsule.LEDGER_NAME
    latest: dict[str, dict] = {}
    with contextlib.suppress(Exception):
        for row in _capsule.read_valid_ledger(path):
            run_id = str(row.get("run_id") or "")
            if not run_id:
                continue
            if int(row.get("revision") or 0) >= int(latest.get(run_id, {}).get("revision") or 0):
                latest[run_id] = row
    return latest


def _failed_dirs(scan: Path) -> list[Path]:
    """`$RPT/scan/_failed/<run_id>/` —— 失败/中断的 run **没有报告目录**,只有这里认识它。"""
    from autoresearch.trace import capsule as _capsule

    root = scan / _capsule.failed_root().name     # 目录名取自 capsule,根按参数(便于隔离)
    if not root.is_dir():
        return []
    return sorted(p for p in root.iterdir() if p.is_dir())


def _run_counts(run_dir: Path, run_mode: str = "") -> tuple[int | None, int | None]:
    """`(n_finalist, n_buy)`。读不到产物 → `(None, None)`,**不写 0**(0 是判断,缺席是状态)。

    唯一的例外是 `SENTINEL_EMPTY`:它的 0 有**结构化事实**背书(`run_mode.json`,§R9
    「不从产物空否反推」的另一面 —— 有那份文件时,空就是真的空)。少了这一条,哨兵日
    会因为 `n_buy` 空白而整天掉出 0-BUY 桶,又变回本模块要修的那个病。
    """
    with contextlib.suppress(Exception):
        facts = _outcome.run_facts(run_dir)
        rows = facts.get("rows") or {}
        if not rows:
            from autoresearch.scan.run_mode import SENTINEL_EMPTY

            return (0, 0) if run_mode == SENTINEL_EMPTY else (None, None)
        n_buy = sum(1 for row in rows.values() if row.get("e6_buy"))
        # `run_facts` 把 BUY 的 role 覆盖在 finalist 身份之上,所以 finalist 数只能按
        # "非 rated-only" 数:E6 只从 finalist 里挑,BUY 行必然也是 finalist 行。
        n_finalist = sum(1 for row in rows.values() if row.get("role") != "rated")
        return n_finalist, n_buy
    return None, None


def _run_mode_of(run_dir: Path) -> str:
    """`run_mode.json` 的四态。**缺文件 = 不知道**,不是 FULL(`run_mode.load` 的既定语义)。"""
    from autoresearch.scan.run_mode import load as _load_mode

    for base in (run_dir / "trace" / "staging", run_dir / "trace"):
        with contextlib.suppress(Exception):
            mode = _load_mode(base)
            if mode is not None:
                return str(mode.mode or "")
    return ""


def collect_runs(reports_root: Path | None = None) -> list[dict]:
    """已发布 run + capsule 账本 + `_failed/` → 一行一个 run(**同日重跑/周末/失败全留**)。

    主键 **`(engine, report_dir_id)`**,不是 `capsule_run_id`。

    🚨 实测(2026-08-28,本波首次真跑逮到):`capsule_run_id` **不唯一**。
    `20260729_2105` / `20260730_0116` / `20260730_2132` 三个已发布 run 带着同一个
    `20260729T113300999873Z`,`20260817_2150` / `20260817_2215` 同理 —— 因为 staging 按
    **数据日**键,同数据日重跑会复用(原地覆盖)那份 `run_contract.json`,capsule 身份
    就跟着被复制到了第二、第三个报告目录里。按它做主键 = 盘上 60 个 run 静默变成 57,
    正是本视图存在的理由(「同日重跑/周末/失败后重跑一个都不许丢」)所要防的那件事。

    报告目录名是唯一真正一 run 一份的东西(publisher 每次发布新建)。`capsule_run_id`
    降为属性,重复时用 `identity_quality=shared_capsule_id` 显式点名 —— 那是**证据层的
    一个真问题**(两个 run 指着同一个法证现场),藏起来比记下来危险得多。
    """
    scan = _scan_root(reports_root)
    engine = ws.ENGINE
    rows: dict[str, dict] = {}                    # key = report_dir_id(唯一一 run 一份)
    seen_capsule: dict[str, str] = {}             # capsule_run_id → 第一个用它的目录

    for run in _outcome.published_runs(scan):
        manifest = _read_json(run / "manifest.json")
        execution = _anchor.read_execution(run)
        parsed = _naming.parse_run_dir(run.name)
        capsule_run_id = str(manifest.get("run_id") or "")
        run_mode = _run_mode_of(run)
        n_finalist, n_buy = _run_counts(run, run_mode)
        shared_with = seen_capsule.setdefault(capsule_run_id, run.name) if capsule_run_id else ""
        rows[run.name] = {
            "engine": engine,
            "capsule_run_id": capsule_run_id,
            "report_dir_id": run.name,
            "run_local_date": (parsed.published_date() if parsed else None),
            "analysis_date": (execution.get("analysis_date")
                              or manifest.get("analysis_date") or None),
            "run_mode": run_mode,
            # 已发布 = 业务成功(publisher 只在成功后建目录);老 manifest 缺这个键是因为
            # 字段比它们晚生,不是"状态未知"。capsule 账本若说了别的,下面会覆盖。
            "business_status": str(manifest.get("business_status") or "SUCCEEDED"),
            "evidence_status": str(manifest.get("evidence_status") or ""),
            "actionability_status": str(execution.get("actionability_status") or _anchor.UNKNOWN),
            "decision_approved_at": execution.get("decision_approved_at"),
            "first_available_session": execution.get("first_available_session"),
            "exec_lag": execution.get("exec_lag"),
            "n_finalist": n_finalist,
            "n_buy": n_buy,
            "identity_quality": ("legacy" if not capsule_run_id
                                 else "shared_capsule_id" if shared_with != run.name
                                 else "capsule"),
            "ready_quality": str(execution.get("ready_quality") or ""),
        }

    # capsule 账本按 `capsule_run_id` 记,而本表按报告目录记 —— 两者是**一对多**
    # (共享 staging 让同一个 capsule id 出现在多个报告目录里,见函数 docstring)。
    by_capsule: dict[str, list[str]] = {}
    for key, existing in rows.items():
        cid = str(existing.get("capsule_run_id") or "")
        if cid:
            by_capsule.setdefault(cid, []).append(key)

    for run_id, row in _capsule_rows(scan).items():
        business = str(row.get("business_status") or "")
        final_path = str(row.get("final_path") or "")
        # 先按 final_path 精确落到那**一个**目录;落不到才把状态应用到共享该 id 的全部目录。
        named = Path(final_path).name if final_path else ""
        targets = [named] if named in rows else by_capsule.get(run_id, [])
        if targets:
            for key in targets:
                existing = rows[key]
                existing["business_status"] = business or existing["business_status"]
                existing["evidence_status"] = str(row.get("evidence_status") or
                                                  existing["evidence_status"])
                if business not in {"SUCCEEDED", "ACTIVE"}:
                    existing["actionability_status"] = _anchor.FAILED
            continue
        rows[f"capsule:{run_id}"] = {
            "engine": str(row.get("engine") or engine),
            "capsule_run_id": run_id,
            "report_dir_id": Path(final_path).name if final_path else "",
            "run_local_date": None,
            "analysis_date": str(row.get("analysis_date") or "") or None,
            "run_mode": "",
            "business_status": business,
            "evidence_status": str(row.get("evidence_status") or ""),
            # 业务没成功的 run 永远没有 ACTIONABLE 状态(不是"未知",是"不适用")。
            "actionability_status": (_anchor.FAILED if business not in {"SUCCEEDED", "ACTIVE"}
                                     else _anchor.UNKNOWN),
            "decision_approved_at": None,
            "first_available_session": None,
            "exec_lag": None,
            "n_finalist": None,
            "n_buy": None,
            "identity_quality": "capsule",
            "ready_quality": "",
        }

    for failed in _failed_dirs(scan):
        run_id = failed.name
        # 本表按报告目录键,`_failed/` 按 capsule id 键 —— 同一个 capsule id 可能对应
        # 多个报告目录(共享 staging)。只要其中**任何一个**已发布,这个残留目录就不许改判。
        if any(rows[key].get("report_dir_id") for key in by_capsule.get(run_id, [])):
            continue
        doc = _read_json(failed / "capsule" / "failure.json") or _read_json(failed / "failure.json")
        row = rows.setdefault(f"capsule:{run_id}", {
            "engine": engine, "capsule_run_id": run_id, "report_dir_id": "",
            "run_local_date": None, "analysis_date": None, "run_mode": "",
            "business_status": "", "evidence_status": "",
            "actionability_status": _anchor.FAILED,
            "decision_approved_at": None, "first_available_session": None,
            "exec_lag": None, "n_finalist": None, "n_buy": None,
            "identity_quality": "capsule", "ready_quality": "",
        })
        row["business_status"] = str(doc.get("business_status") or row["business_status"]
                                     or "FAILED")
        row["actionability_status"] = _anchor.FAILED

    # 按 (数据日, 目录名, capsule id) 排 —— 与新目录名的字典序同向(数据日在前),
    # 同一数据日的多次尝试自然聚在一起,且与墙钟无关(重复跑 byte 稳定)。
    return sorted(rows.values(), key=lambda r: (str(r.get("analysis_date") or ""),
                                                str(r.get("report_dir_id") or ""),
                                                str(r.get("capsule_run_id") or "")))


# ───────────────────────── view ②:session_calendar.csv ─────────────────────────

def _run_ref(row: dict) -> str:
    """一个 run 在 session 视图里的称呼:优先报告目录名(`chain_view <run_id>` 认它)。"""
    return str(row.get("report_dir_id") or row.get("capsule_run_id") or "")


def _sort_key(row: dict) -> tuple:
    """session 内的先后。没有批准时点的 run 排在**前面** —— 不能证明它更晚,就不给它
    "截止前最后一个"的资格(保守方向:宁可不选中,不可选错)。"""
    return (str(row.get("decision_approved_at") or ""),
            str(row.get("report_dir_id") or ""),
            str(row.get("capsule_run_id") or ""))


def build_sessions(runs: list[dict], sessions: list[str], *, source: str) -> list[dict]:
    """一个交易 session 一行。`selected_run_id` = 截止前最后一个 SUCCEEDED ∧ ACTIONABLE。

    分桶键是 `first_available_session`(G1 已经把"批准时刻 < 该 session 的 14:45 运营截止"
    折进去了),所以"截止之前"由归属本身保证。**周末跑的 run 不会消失**:它的
    first available 落在下周一,于是出现在周一那一行,而 `runs.csv` 里仍按周末的
    `run_local_date` 保留原样。
    """
    ready: dict[str, list[dict]] = {day: [] for day in sessions}
    unknown: dict[str, list[dict]] = {}
    for row in runs:
        first = str(row.get("first_available_session") or "")
        if first in ready:
            ready[first].append(row)
            continue
        if first:
            continue        # 就绪日已知,只是不在本次窗口内(`--limit`)—— 不是"证明不了"
        # 证明不了何时可用 → 落到"它最早也不可能早于跑动日之后"的那个 session,
        # 记成 DATA_MISSING。**不能让它静默消失**:消失会把那一天写成 NO_RUN,
        # 而 NO_RUN 在下游是"系统那天没开工"的强断言。
        anchor = str(row.get("run_local_date") or row.get("analysis_date") or "")
        if not anchor:
            continue
        later = [day for day in sessions if day > anchor]
        if later:
            unknown.setdefault(later[0], []).append(row)

    out: list[dict] = []
    for day in sessions:
        candidates = sorted(ready[day], key=_sort_key)
        approved = [r for r in candidates
                    if str(r.get("business_status")) == "SUCCEEDED"
                    and str(r.get("actionability_status")) == _anchor.ACTIONABLE]
        selected = approved[-1] if approved else None
        pending = sorted(unknown.get(day, []), key=_sort_key)
        if selected is not None:
            status = SESSION_SELECTED
            reason = f"last_actionable_of_{len(candidates)}"
        elif candidates:
            status = SESSION_NO_APPROVED_RUN
            reason = f"no_actionable_of_{len(candidates)}"
        elif pending:
            status = SESSION_DATA_MISSING
            reason = "unknown_readiness:" + ";".join(_run_ref(r) for r in pending)
        else:
            status = SESSION_NO_RUN
            reason = ""                  # 机器**绝不编造** NO_RUN 的原因;要写去 notes.jsonl
        out.append({
            "session": day,
            "run_ids_ready_before_cutoff": ";".join(_run_ref(r) for r in candidates),
            "selected_run_id": _run_ref(selected) if selected is not None else "",
            "selection_reason": reason,
            "status": status,
            "source": source,
        })
    return out


# ───────────────────────── view ③:market.csv(四把尺 × 可买人口) ─────────────────────────

def _compact(day: str) -> str:
    return str(day).replace("-", "")


def _dashed(day: str) -> str:
    text = _compact(day)
    return f"{text[:4]}-{text[4:6]}-{text[6:]}" if len(text) == 8 else str(day)


def market_metrics(day: str, trade_days: list[str], piv: dict) -> dict | None:
    """一个交易日的四把尺读数。`day`/`trade_days` 用 compact YYYYMMDD;不成熟 → `None`。

    口径与 `common.forward_returns` / `data.market_panel` **逐字同源**(`lake_trade_days` /
    `load_lake_pivots` / `_board_limit` 都是同一个实现),两边读数因此可以直接对表。另写
    一套板制度或另写一个 pivot 加载器,就是在给同一个问题造第二个答案。
    """
    if not piv or day not in trade_days:
        return None
    index = trade_days.index(day)
    if index + 2 >= len(trade_days):
        return None
    d1, d2 = trade_days[index + 1], trade_days[index + 2]
    o, c, h, pc = piv.get("open"), piv.get("close"), piv.get("high"), piv.get("pct_chg")
    if any(frame is None for frame in (o, c, h, pc)):
        return None
    if d1 not in o.columns or d2 not in o.columns or d1 not in pc.columns:
        return None
    o1, c1, h1, pc1 = o[d1], c[d1], h[d1], pc[d1]
    o2, c2 = o[d2], c[d2]
    limit = pd.Series([_board_limit(code) for code in o.index], index=o.index, dtype=float)
    # 封板收盘 = 当日涨幅≈板 ∧ 收盘≈日高 → T+1 尾盘买不进;
    # 一字板 = 再加上"开盘即最高" → T+1 开盘也买不进(它是封板的真子集)。
    sealed_c1 = (pc1 >= limit * 0.98) & (c1 >= h1 - 1e-6)
    sealed_o1 = sealed_c1 & (o1 >= h1 - 1e-6)
    frame = pd.DataFrame({"c1o2": o2 / c1 - 1.0, "c1c2": c2 / c1 - 1.0,
                          "o1o2": o2 / o1 - 1.0, "o1c2": c2 / o1 - 1.0})
    # 板制度下不可能的幅度 = 数据错(盘后灌数/复权错),置 NaN 而不是让它拉动均值。
    frame = frame.where(frame.abs() <= _ruler.GAP_CLIP)
    buyable = {"c1": (~sealed_c1) & c1.notna(), "o1": (~sealed_o1) & o1.notna()}

    row: dict = {"n_buyable_c1": int(buyable["c1"].sum()),
                 "n_buyable_o1": int(buyable["o1"].sum())}
    scored = 0
    for name in MARKET_RULERS:
        values = frame.loc[buyable[RULER_LEG[name]], name].dropna()
        row[f"mean_{name}"] = float(values.mean()) if len(values) else None
        row[f"median_{name}"] = float(values.median()) if len(values) else None
        scored += int(bool(len(values)))
    row["status"] = STATUS_MATURE if scored else STATUS_UNAVAILABLE
    return row


def market_context(reports_root: Path | None = None) -> dict[str, dict]:
    """`{analysis_date: {regime, temperature}}` —— **presence-gated**:读得到就填,读不到留空。

    regime/temperature 是那天市场的属性(同数据日的多个 run 读同一份湖派生 pack),
    所以按 `report_dir_id` 取最后一个能读出来的 run,不做任何加权/推断。
    """
    out: dict[str, dict] = {}
    for run in _outcome.published_runs(_scan_root(reports_root)):
        manifest = _read_json(run / "manifest.json")
        date = str(manifest.get("analysis_date") or "")
        if not date:
            continue
        pack = (_read_json(run / "trace" / "staging" / "market_pack.json")
                or _read_json(run / "trace" / "market_pack.json"))
        block = pack.get("regime")
        regime = block.get("label") if isinstance(block, dict) else None
        block = pack.get("temperature")
        temperature = block.get("score") if isinstance(block, dict) else None
        if regime is None and temperature is None:
            continue
        out[_dashed(date)] = {"regime": regime, "temperature": temperature}
    return out


def build_market(sessions: list[str], *, lake_daily: Path | None = None,
                 context: dict[str, dict] | None = None) -> list[dict]:
    """每交易日一行。湖里还没有 D+2 → `PENDING`(**状态不是故障**);湖缺了本该有的天 → `UNAVAILABLE`。"""
    trade_days = _panel.lake_trade_days(lake_daily)
    last_lake = trade_days[-1] if trade_days else ""
    wanted = [_compact(day) for day in sessions]
    # 一次装完整个窗口(尾部 +2 个交易日供 D+2 腿),不逐日 IO:60 个交易日 × 5500 只是常态。
    in_lake = [day for day in trade_days if day in set(wanted)]
    window: list[str] = []
    if in_lake:
        first_index = trade_days.index(in_lake[0])
        last_index = min(len(trade_days) - 1, trade_days.index(in_lake[-1]) + 2)
        window = trade_days[first_index:last_index + 1]
    piv = _panel.load_lake_pivots(window, lake_daily) if window else {}

    rows: list[dict] = []
    for session, day in zip(sessions, wanted, strict=True):
        extra = (context or {}).get(session) or {}
        row = {"date": session, "regime": extra.get("regime"),
               "temperature": extra.get("temperature")}
        metrics = market_metrics(day, trade_days, piv) if day in trade_days else None
        if metrics is not None:
            row.update(metrics)
        elif day in trade_days:
            row["status"] = STATUS_PENDING          # D+2 还没落湖 —— 时间没到,不是坏
        elif last_lake and day < last_lake:
            row["status"] = STATUS_UNAVAILABLE      # 湖里缺了一天本该有的数据 = 真缺口
        else:
            row["status"] = STATUS_PENDING          # 比湖还新的 session,还没轮到它
        rows.append(row)
    return rows


# ───────────────────────── 组装 + 运维健康 ─────────────────────────

def _session_span(runs: list[dict], lake_daily: Path | None) -> tuple[str, str] | None:
    anchors = [str(r.get("analysis_date") or "") for r in runs]
    anchors += [str(r.get("run_local_date") or "") for r in runs]
    anchors += [str(r.get("first_available_session") or "") for r in runs]
    known = sorted(day for day in anchors if len(day) == 10)
    if not known:
        return None
    lake = _panel.lake_trade_days(lake_daily)
    end = max(known[-1], _dashed(lake[-1]) if lake else known[-1])
    return known[0], end


def build(*, reports_root: Path | None = None, lake_daily: Path | None = None,
          limit: int | None = None, now: str | None = None) -> dict:
    """物化三张 view + `_health.json`;返回 health 文档。

    `--limit N` 只收窄**日历窗口**(最近 N 个 session,市场行是唯一贵的一段);
    `runs.csv` 永远全量。view 是可重建的物化视图,所以带 limit 跑出来的是一张被截短的
    表 —— 夜间任务不传 limit,人工排查才传。
    """
    scan = _scan_root(reports_root)
    stamp = now or datetime.now(timezone.utc).isoformat(timespec="seconds")
    previous = _read_json(health_path(scan))
    # **先落 attempt 再干活**:这样"每晚都炸"与"每晚都没跑"在文件里长得不一样 ——
    # `last_attempt_at` 一直在走而 `last_success_at` 停在两周前,正是 nightly-close
    # 死了两个月没人发现的那个反面。成功后再整份覆写。
    _write_health(scan, {**previous, "schema_version": VIEWS_SCHEMA_VERSION,
                         "engine": ws.ENGINE, "last_attempt_at": stamp,
                         "last_success_at": previous.get("last_success_at"),
                         "green": False, "green_reason": "本次 build 尚未完成"})
    runs = collect_runs(scan)
    span = _session_span(runs, lake_daily)
    sessions: list[str] = []
    source = "none"
    if span is not None:
        sessions, source = _anchor.trading_sessions(span[0], span[1])
        sessions = sorted(set(sessions))
        if limit and limit > 0:
            sessions = sessions[-limit:]
    session_rows = build_sessions(runs, sessions, source=source)
    market_rows = build_market(sessions, lake_daily=lake_daily,
                               context=market_context(scan))

    root = views_root(scan)
    atomic_write_text(root / RUNS_CSV, render_csv(RUNS_COLUMNS, runs))
    atomic_write_text(root / SESSIONS_CSV, render_csv(SESSION_COLUMNS, session_rows))
    atomic_write_text(root / MARKET_CSV, render_csv(MARKET_COLUMNS, market_rows))

    placed = {ref for row in session_rows
              for ref in str(row["run_ids_ready_before_cutoff"]).split(";") if ref}
    filled = sum(1 for row in market_rows if row.get("status") == STATUS_MATURE)
    pending = sum(1 for row in market_rows if row.get("status") == STATUS_PENDING)
    # `blocked_by_data` = **该算而算不出来**的那些:湖缺了本该有的交易日,或有 run 却
    # 证明不了它何时可用。「0 filled」只有在这个数也是 0 时才算绿 —— 否则这个任务就会
    # 像 `nightly-close` 那样,一边天天"成功"一边什么都没干。
    blocked = (sum(1 for row in market_rows if row.get("status") == STATUS_UNAVAILABLE)
               + sum(1 for row in session_rows if row["status"] == SESSION_DATA_MISSING))
    health = {
        "schema_version": VIEWS_SCHEMA_VERSION,
        "engine": ws.ENGINE,
        "last_attempt_at": stamp,
        "last_success_at": stamp,
        "filled": filled,
        "pending": pending,
        "blocked_by_data": blocked,
        "green": blocked == 0,
        "calendar_source": source,
        "n_runs": len(runs),
        "n_sessions": len(session_rows),
        "n_runs_unplaced": sum(1 for row in runs if _run_ref(row) not in placed),
        "views": [RUNS_CSV, SESSIONS_CSV, MARKET_CSV],
    }
    if not health["green"]:
        health["green_reason"] = (f"{blocked} 个成熟欠账(湖缺交易日 / run 就绪时点不可证)"
                                  " —— 不是「没事可做」")
    if previous.get("last_success_at"):
        health["previous_success_at"] = previous["last_success_at"]
    _write_health(scan, health)
    return health


def _write_health(scan: Path, health: dict) -> Path:
    return atomic_write_text(health_path(scan),
                             json.dumps(health, ensure_ascii=False, sort_keys=True,
                                        indent=1) + "\n")


# ───────────────────────── 人工备注(append-only,机器只写不读) ─────────────────────────

def add_note(date: str, text: str, *, reports_root: Path | None = None,
             now: str | None = None) -> Path:
    """一行人话。**`build` 永远不读它** —— 机器不拿人的解释当事实,也不编造自己的。"""
    path = notes_path(reports_root)
    path.parent.mkdir(parents=True, exist_ok=True)
    row = {"date": _dashed(date), "text": str(text), "engine": ws.ENGINE,
           "recorded_at": now or datetime.now(timezone.utc).isoformat(timespec="seconds")}
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")
    return path


# ───────────────────────── 读数(prelude 汇总屏一行) ─────────────────────────

def _load_csv(path: Path) -> list[dict]:
    if not path.is_file():
        return []
    with path.open(encoding="utf-8-sig", newline="") as fh:
        return list(csv.DictReader(fh))


def _float(value: object) -> float | None:
    text = str(value or "").strip()
    if not text:
        return None
    with contextlib.suppress(ValueError):
        return float(text)
    return None


def session_buckets(reports_root: Path | None = None) -> dict[str, list[float]]:
    """四桶 → 各自的**市场** gap 列表(不是推荐票的 gap)。

    一个 session S 的市场 gap = `market.csv[S 的前一个 session].mean_c1o2`:主尺是
    "T+1 尾盘买 → T+2 开盘卖",在 S 尾盘出手赚到的那一段,记在数据日 S−1 那一行。
    这样 `NO_RUN` 的日子也有市场读数 —— 「那天没跑,而市场涨了」正是要回答的问题。
    """
    scan = _scan_root(reports_root)
    runs = {_run_ref(row): row for row in _load_csv(views_root(scan) / RUNS_CSV)}
    market = {row["date"]: _float(row.get("mean_c1o2"))
              for row in _load_csv(views_root(scan) / MARKET_CSV)
              if row.get("status") == STATUS_MATURE}
    sessions = _load_csv(views_root(scan) / SESSIONS_CSV)
    days = [row["session"] for row in sessions]
    previous = {day: days[i - 1] for i, day in enumerate(days) if i > 0}

    buckets: dict[str, list[float]] = {"BUY": [], "ZERO_BUY": [],
                                       SESSION_NO_RUN: [], SESSION_NO_APPROVED_RUN: []}
    for row in sessions:
        gap = market.get(previous.get(row["session"], ""))
        status = row["status"]
        if status == SESSION_SELECTED:
            n_buy = _float((runs.get(row["selected_run_id"]) or {}).get("n_buy"))
            if n_buy is None:
                continue                    # 选中了但数不出买了几只 → 不进任何桶,不猜
            key = "BUY" if n_buy > 0 else "ZERO_BUY"
        elif status in buckets:
            key = status
        else:
            continue                        # DATA_MISSING 不是四桶之一,单独在 health 里报
        if gap is not None:
            buckets[key].append(gap)
    return buckets


_BUCKET_LABEL = {"BUY": "BUY日", "ZERO_BUY": "0买日",
                 SESSION_NO_RUN: "没跑", SESSION_NO_APPROVED_RUN: "未批准"}


def line(reports_root: Path | None = None) -> str:
    """汇总屏一行 —— **仅人看,不喂任何 agent、不进 brief**(同 `outcome.ledger_line` 的边界)。

    每桶各自攒够 20 个**有效日**才印均值:小样本均值会被当成结论读,而这条线存在的
    意义正是不让人再凭「那天好像跌了,空仓是对的」下判断。
    """
    scan = _scan_root(reports_root)
    if not (views_root(scan) / SESSIONS_CSV).is_file():
        return ("运行日历:空(还没物化过 —— "
                "`python -m autoresearch.scan.ledger_views build`)")
    buckets = session_buckets(scan)
    parts = []
    for key in ("BUY", "ZERO_BUY", SESSION_NO_RUN, SESSION_NO_APPROVED_RUN):
        values = buckets[key]
        label = _BUCKET_LABEL[key]
        if len(values) >= MIN_SESSION_N:
            parts.append(f"{label} {len(values)}日 市场 {100 * float(np.mean(values)):+.2f}pp")
        else:
            parts.append(f"{label} 攒样本 {len(values)}/{MIN_SESSION_N}")
    health = _read_json(health_path(scan))
    tail = "" if health.get("green", True) else f" · ⚠️ 成熟欠账 {health.get('blocked_by_data')}"
    return ("运行日历:" + " · ".join(parts)
            + f" · 主尺 {_ruler.MAIN_RULER}(NO_RUN≠0买;只记不学,仅人看)" + tail)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="运行日历三视图(确定性、只记不学)")
    ap.add_argument("command", choices=["build", "note", "line"])
    ap.add_argument("rest", nargs="*", help="note 用:<date> <text>")
    ap.add_argument("--limit", type=int, default=None,
                    help="只物化最近 N 个交易 session(view 会被截短;夜间任务别传)")
    ap.add_argument("--now", default=None, help="_health.json 的时间戳(留痕用)")
    args = ap.parse_args(argv)
    if args.command == "line":
        print(line())
        return 0
    if args.command == "note":
        if len(args.rest) < 2:
            ap.error("note 需要 <date> <text>")
        path = add_note(args.rest[0], " ".join(args.rest[1:]), now=args.now)
        print(json.dumps({"ok": True, "notes": str(path)}, ensure_ascii=False))
        return 0
    health = build(limit=args.limit, now=args.now)
    print(json.dumps({"ok": True, **health}, ensure_ascii=False, sort_keys=True))
    print(line())
    # 非绿**不是**退出码非零:view 确实物化成功了,欠账是读数不是故障。退出码留给真正的
    # 失败(模块不在/根写不进),这样 launchd 的失败信号才不会被"湖今天缺一天"淹掉。
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
