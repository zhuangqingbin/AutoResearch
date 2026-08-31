#!/usr/bin/env python3
"""scan · 「上一个 scan 日在哪」解析器(确定性,零 LLM,零网络)。

**病灶**(spec `docs/specs/2026-08-29-full-coverage-research-system-brainstorm.md` §2.2 K4):
法证 run capsule 波(2026-08-28)把 staging 改成 **run 分区**
`context_<engine>/scan_runs/<run_id>/staging/<date>/`,分区与否由**进程环境变量**
`AUTORESEARCH_RUN_ID` 决定(`common/workspace.py:105-107,121-122`)。而三个**跨日读者**
仍在遍历 `ws.scan_root()` 的**兄弟目录**:

  · `sector/reuse.find_reusable`  —— 行业 brief 的 ≤5 日 TTL 复用
  · `scan/l3/prompt._prev_l3_day` —— L3 Δ 模式
  · `scan/menu.zero_buy_streak`   —— 0 买连败(L4 预算五面旗之一)

在 run 分区下,`scan_root()` 的兄弟目录里**只有本 run 自己的日期**,于是:TTL 复用永远
找不到昨天的 brief(每天白付 6 个 opus brief)、Δ 模式永远静默退化成全量表、0 买连败永远
数成 0。三处**都不报错**——这正是它能活到今天的原因。

**三级解析**(逐级回落;`resolve()` 回报是哪一级答的,便于 prelude 端口行显示):

  ① `reports_<engine>/scan/_ledger/views/runs.csv` 的 `analysis_date` 列(G2 已产);
  ② 已发布 run 目录 `reports_<engine>/scan/<run_dir>/manifest.json` 的 `analysis_date`;
  ③ 历史根 `context_<engine>/scan/<date>/` 的兄弟目录(= 今天的行为,兼容老树)。

`previous_staging_dirs` 在此之上**只增不减**:物理存在的日期目录一律入选(本 run 分区优先,
再回落日期键历史根)——这保证在**无 run** 时结果与今天的兄弟目录枚举逐字相同——再补上
账本/manifest 给出、但两个主根下已经没有的日子(按账本 `capsule_run_id` 定位到别的 run 分区)。

⚠️ 本模块**只回答「去哪找昨天」,一个判据都不碰**:TTL 天数、动量位移容差、Δ 容差、连败
回看深度、`L3_judged_full.csv` + `L2_gbdt_top200.csv` 的存在性判据,全部留在各自的消费者里。
"""
from __future__ import annotations

import csv
import json
import re
from pathlib import Path
from typing import NamedTuple

from autoresearch.common import workspace as ws

#: `resolve()` 的来源标签(哪一级答的)。
SOURCE_LEDGER = "ledger"
SOURCE_MANIFEST = "manifest"
SOURCE_LEGACY = "legacy"
SOURCE_NONE = "none"

_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


class Resolution(NamedTuple):
    """`days` = 比请求日早的数据日(新→旧);`source` ∈ 上面四个标签之一。"""

    days: list[str]
    source: str


# ── 根定位(全部惰性:测试 monkeypatch `ws.context_root`/`ws.reports_root` 后立即生效)──

def _ledger_view() -> Path:
    return ws.reports_root() / "scan" / "_ledger" / "views" / "runs.csv"


def _published_root() -> Path:
    return ws.reports_root() / "scan"


def _legacy_scan_root() -> Path:
    """日期键历史根 —— 它**不跟 run 走**(`scan_root()` 才跟),所以 run 分区下仍看得见历史。"""
    return ws.context_root() / "scan"


def _staging_roots() -> list[Path]:
    """按优先序:本 run 分区 staging → 日期键历史根。无 run 时二者是同一个目录(去重)。"""
    roots: list[Path] = []
    for r in (ws.scan_root(), _legacy_scan_root()):
        if r not in roots:
            roots.append(r)
    return roots


# ── 三级取日 ────────────────────────────────────────────────────────────────

def _sorted_before(values, before: str) -> list[str]:
    days = {str(v) for v in values if v and _DATE_RE.fullmatch(str(v)) and str(v) < before}
    return sorted(days, reverse=True)


def _ledger_rows() -> list[dict]:
    p = _ledger_view()
    if not p.exists():
        return []
    try:
        with p.open(encoding="utf-8", newline="") as fh:
            return list(csv.DictReader(fh))
    except (OSError, UnicodeDecodeError, csv.Error):
        return []                      # 账本读坏 → 回落下一级(不是数据契约面,不阻断扫描)


def _days_from_ledger(before: str) -> list[str]:
    return _sorted_before([r.get("analysis_date") for r in _ledger_rows()], before)


def _days_from_manifests(before: str) -> list[str]:
    root = _published_root()
    if not root.exists():
        return []
    dates: list[str] = []
    for d in root.iterdir():
        if not d.is_dir() or d.name.startswith("_"):
            continue
        mf = d / "manifest.json"
        if not mf.exists():
            continue
        try:
            dates.append(json.loads(mf.read_text(encoding="utf-8")).get("analysis_date"))
        except (OSError, UnicodeDecodeError, ValueError):
            continue
    return _sorted_before(dates, before)


def _days_from_legacy(before: str) -> list[str]:
    names: list[str] = []
    for root in _staging_roots():
        if not root.exists():
            continue
        names += [p.name for p in root.iterdir() if p.is_dir()]
    return _sorted_before(names, before)


def resolve(date: str, *, limit: int = 30) -> Resolution:
    """三级解析比 `date` 早的数据日 + 回报哪一级答的(见模块 docstring)。"""
    before = str(date)
    if not _DATE_RE.fullmatch(before):
        return Resolution([], SOURCE_NONE)
    cap = max(0, int(limit))
    for source, fn in ((SOURCE_LEDGER, _days_from_ledger),
                       (SOURCE_MANIFEST, _days_from_manifests),
                       (SOURCE_LEGACY, _days_from_legacy)):
        days = fn(before)
        if days:
            return Resolution(days[:cap], source)
    return Resolution([], SOURCE_NONE)


def previous_scan_days(date: str, *, limit: int = 30) -> list[str]:
    """比 `date` 严格早的 scan 数据日,**新→旧**。哪一级答的用 `resolve()` 看。"""
    return resolve(date, limit=limit).days


# ── 日 → staging 目录 ───────────────────────────────────────────────────────

def _ledger_run_index() -> dict[str, str]:
    """`analysis_date` → `capsule_run_id`(同日多 run 取账本最后一行 = 最新)。"""
    idx: dict[str, str] = {}
    for row in _ledger_rows():
        day = str(row.get("analysis_date") or "")
        run_id = str(row.get("capsule_run_id") or "").strip()
        if _DATE_RE.fullmatch(day) and run_id:
            idx[day] = run_id
    return idx


def _locate(day: str, run_index: dict[str, str]) -> Path | None:
    for root in _staging_roots():
        p = root / day
        if p.is_dir():
            return p
    run_id = run_index.get(day)
    if run_id:
        try:
            p = ws.scan_run_root(run_id) / "staging" / day
        except ValueError:
            return None                # 账本里的 run_id 不合法 → 定位不到,不抛
        if p.is_dir():
            return p
    return None


def previous_staging_dirs(date: str, *, limit: int = 30) -> list[Path]:
    """这些日子的 staging 目录,**新→旧**;定位不到目录的日子跳过。

    每日一条:本 run 分区优先 → 日期键历史根 → 账本给出 `capsule_run_id` 的那个 run 分区。
    物理存在的日期目录一律入选(无 run 时 = 今天兄弟目录枚举的逐字结果)。
    """
    before = str(date)
    if not _DATE_RE.fullmatch(before):
        return []
    cap = max(0, int(limit))
    found: dict[str, Path] = {}
    for root in _staging_roots():
        if not root.exists():
            continue
        for p in root.iterdir():
            if p.is_dir() and _DATE_RE.fullmatch(p.name) and p.name < before:
                found.setdefault(p.name, p)
    run_index = _ledger_run_index()
    for day in previous_scan_days(before, limit=limit):
        if day in found:
            continue
        located = _locate(day, run_index)
        if located is not None:
            found[day] = located
    return [found[name] for name in sorted(found, reverse=True)[:cap]]
