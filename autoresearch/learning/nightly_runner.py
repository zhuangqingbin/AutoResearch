#!/usr/bin/env python3
"""夜间跑批加固 —— 锁 / 心跳 / 幂等 / catch-up / run ledger(确定性,零 LLM)。

design: docs/specs/2026-08-03-scan-next-wave-brainstorm-design.md §4.2

## 设计修正:加固既有 runner,不是从零新建

设计稿原话:「**复用 `autoresearch.learning.nightly_close`,安装/加固 launchd**」。
所以本模块**不重写**夜间任务的内容,它只在 `nightly_close.run()` 外面套一层运行安全:

    互斥锁(带 stale 回收)· 原子心跳 · 幂等键 · 交易日历/catch-up · 超时 · 重试退避
    · cwd/env/TUSHARE_TOKEN 校验 · run ledger(输入债务/period/起止/状态/产出数/error hash)

## 两条容易写错的判活

1. **`0 rows` 可能是合法 NOOP**(§4.2-1)。「今晚没有待归因日」和「归因跑挂了」在行数上
   一模一样。所以 ledger 记的是 `input_debt`(开工前的欠账)与 `outputs`(产出),
   **两者一起**才说得清:债 0 产 0 = NOOP;债 5 产 0 = 真失败。
2. **心跳必须原子写**。半截心跳文件比没有心跳更糟 —— 它会让下一个实例以为有人在跑。

## 债务分级(§4.2-4)

数据完整性 / 成熟标签污染 → 可考虑 hard block;研究 / LLM 诊断债 → **只告警或降级**。
真要「启动前阻断」必须在 frame/universe/LLM 之前另设 GATE0(见 `scan/gate0.py`)——
**当前 GATE1 在 L2 之后,不能声称「不开始扫描」**。

  uv run --no-sync python -m autoresearch.learning.nightly_runner run
  uv run --no-sync python -m autoresearch.learning.nightly_runner plist
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import socket
import time
from contextlib import contextmanager
from dataclasses import asdict, dataclass, field
from datetime import date as _date, datetime, timezone
from pathlib import Path

from autoresearch.common import workspace as ws

SCHEMA_VERSION = 1
DEFAULT_STATE = ws.context_root() / "learning/nightly"
LOCK_NAME = "nightly.lock"
HEARTBEAT_NAME = "nightly.heartbeat.json"
LEDGER_NAME = "nightly_runs.jsonl"

STALE_LOCK_SECONDS = 3 * 3600        # 心跳超过 3h 未更新 = 上一个实例已死
DEFAULT_TIMEOUT_SECONDS = 45 * 60
DEFAULT_RETRIES = 2
BACKOFF_BASE_SECONDS = 5.0
CATCH_UP_MAX_DAYS = 7                # 停机后最多回补几天(超出需人工决定)

# §4.2 运行安全:与当日 scan/retro 抢写这三样最危险
CONTENDED_ARTIFACTS = ("task book", "T1 快环", "dossier")

REQUIRED_ENV = ("TUSHARE_TOKEN",)


class NightlyError(RuntimeError):
    """运行前置条件不满足(env/cwd/锁)—— 宁可不跑,不要跑残。"""


class LockBusy(NightlyError):
    """另一个实例正在跑且心跳新鲜。"""


# ───────────────────────── 环境校验 ─────────────────────────


def check_environment(*, cwd: Path | str | None = None,
                      require_env: tuple = REQUIRED_ENV) -> dict:
    """cwd / env 校验。**launchd 的 cwd 不是你以为的那个** —— 它默认在 `/`,
    所有相对路径(`context/`、`reports/`)会落到根目录下,而任务照样"成功"。"""
    root = Path(cwd or Path.cwd())
    problems: list[str] = []
    if not (root / "autoresearch").is_dir():
        problems.append(f"cwd={root} 不是仓库根(缺 autoresearch/)—— "
                        "launchd 默认 cwd 是 /,相对路径会落到根目录下")
    missing = [name for name in require_env if not os.environ.get(name)]
    if missing:
        problems.append(f"缺环境变量 {missing}")
    return {"ok": not problems, "cwd": str(root), "problems": problems,
            "env_present": {n: bool(os.environ.get(n)) for n in require_env}}


# ───────────────────────── 锁 + 心跳 ─────────────────────────


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _atomic_write(path: Path, payload: dict) -> None:
    """原子写 —— 半截心跳比没有心跳更糟(会让下一个实例以为有人在跑)。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
                   encoding="utf-8")
    tmp.replace(path)


def read_heartbeat(state_dir: Path | str | None = None) -> dict | None:
    path = Path(state_dir or DEFAULT_STATE) / HEARTBEAT_NAME
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def lock_age_seconds(state_dir: Path | str | None = None,
                     *, now: datetime | None = None) -> float | None:
    beat = read_heartbeat(state_dir)
    if not beat or not beat.get("at"):
        return None
    try:
        at = datetime.fromisoformat(str(beat["at"]))
    except ValueError:
        return None
    return ((now or _now()) - at).total_seconds()


@contextmanager
def exclusive(state_dir: Path | str | None = None, *,
              stale_seconds: float = STALE_LOCK_SECONDS,
              now: datetime | None = None, run_id: str = ""):
    """互斥锁 + 心跳。锁存在但心跳陈旧 → **回收**并记一笔(不是永久卡死)。

    夜间任务被 kill -9 的概率不低;没有 stale 回收的锁会让第二天起所有夜跑静默跳过,
    而 prelude 只会说「欠账 N 天」,不会说「锁没释放」。
    """
    root = Path(state_dir or DEFAULT_STATE)
    root.mkdir(parents=True, exist_ok=True)
    lock = root / LOCK_NAME
    stamp = now or _now()
    recovered = False
    if lock.exists():
        age = lock_age_seconds(root, now=stamp)
        if age is not None and age < stale_seconds:
            raise LockBusy(
                f"另一个实例正在跑(心跳 {age:.0f}s 前,阈值 {stale_seconds:.0f}s)")
        recovered = True

    _atomic_write(root / HEARTBEAT_NAME, {
        "at": stamp.isoformat(), "pid": os.getpid(), "host": socket.gethostname(),
        "run_id": run_id, "recovered_stale_lock": recovered})
    lock.write_text(f"{os.getpid()}@{socket.gethostname()}\n", encoding="utf-8")
    try:
        yield {"recovered_stale_lock": recovered, "lock": str(lock)}
    finally:
        lock.unlink(missing_ok=True)


def beat(state_dir: Path | str | None = None, *, run_id: str = "",
         step: str = "", now: datetime | None = None) -> None:
    _atomic_write(Path(state_dir or DEFAULT_STATE) / HEARTBEAT_NAME, {
        "at": (now or _now()).isoformat(), "pid": os.getpid(),
        "host": socket.gethostname(), "run_id": run_id, "step": step})


# ───────────────────────── 幂等键 + catch-up ─────────────────────────


def idempotency_key(day: str, debts: dict) -> str:
    """幂等键 = 日期 + **输入债务快照**。

    只用日期做键是不够的:同一天里债务可能变(白天又跑了一次 scan)。把债务快照并进 key,
    「同一天第二次跑」与「同一天但债务变了」就分得开。
    """
    blob = json.dumps({"day": day, "debts": debts}, ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:16]


def catch_up_days(last_run: str | None, today: str, *,
                  trade_days: list[str] | None = None,
                  max_days: int = CATCH_UP_MAX_DAYS) -> dict:
    """停机后要补哪几天。**按交易日历**,不是自然日 —— 周末停机不算欠账。

    超过 `max_days` → 只补最近 `max_days` 天并**显式列出被放弃的**(§5-3「不得静默截断」)。
    """
    if trade_days is None:
        return {"days": [today], "skipped": [], "basis": "no_calendar",
                "note": "无交易日历 → 只跑当天(不臆造历史欠账)"}
    if last_run is None:
        return {"days": [today] if today in trade_days else [], "skipped": [],
                "basis": "first_run"}
    pending = [d for d in trade_days if last_run < d <= today]
    kept, skipped = pending[-max_days:], pending[:-max_days] if len(pending) > max_days else []
    return {"days": kept, "skipped": skipped, "basis": "trade_calendar",
            "note": (f"⚠️ 放弃 {len(skipped)} 天(超过 max_days={max_days},需人工决定)"
                     if skipped else "")}


# ───────────────────────── 债务分级 ─────────────────────────

TIER_DATA_INTEGRITY = "data_integrity"     # 可考虑 hard block
TIER_MATURITY_TAINT = "maturity_taint"     # 可考虑 hard block
TIER_RESEARCH = "research"                 # 只告警
TIER_LLM_DIAGNOSIS = "llm_diagnosis"       # 只告警 / degraded policy

BLOCKING_TIERS = (TIER_DATA_INTEGRITY, TIER_MATURITY_TAINT)
DEBT_TIERS = {
    "retro_pending": TIER_RESEARCH,
    "retro_stalled": TIER_LLM_DIAGNOSIS,
    "t1_pending": TIER_RESEARCH,
    "dossier_reconcile": TIER_RESEARCH,
    "dossier_pending_init": TIER_RESEARCH,
    "attribution_missing": TIER_DATA_INTEGRITY,
    "maturity_mislabeled": TIER_MATURITY_TAINT,
}

DEBT_POLICY_NOTE = (
    "数据完整性 / 成熟标签污染可考虑 hard block;研究 / LLM 诊断债只告警或 degraded。"
    "「债务导致权重停在旧教训、扫得越多错得越贵」**目前无因果证据**,已从设计依据删除")


def classify_debts(debts: dict) -> dict:
    """债务 → 分级。未登记的债一律按**研究债**(最宽),不擅自升格成阻断项。"""
    by_tier: dict[str, dict] = {}
    for name, count in (debts or {}).items():
        tier = DEBT_TIERS.get(name, TIER_RESEARCH)
        by_tier.setdefault(tier, {})[name] = int(count or 0)
    blocking = {t: v for t, v in by_tier.items()
                if t in BLOCKING_TIERS and any(v.values())}
    return {"by_tier": by_tier, "blocking_tiers": sorted(blocking),
            "would_hard_block": bool(blocking),
            "policy": DEBT_POLICY_NOTE,
            "enforcement": "ADVISORY",     # 当前只告警 —— 硬闸是 B 类,见 scan/gate0.py
            "contended_artifacts": list(CONTENDED_ARTIFACTS)}


# ───────────────────────── run ledger ─────────────────────────


@dataclass
class RunRecord:
    run_id: str
    day: str
    idempotency_key: str
    started_at: str
    finished_at: str | None = None
    status: str = "RUNNING"
    input_debt: dict = field(default_factory=dict)
    outputs: dict = field(default_factory=dict)
    steps: list = field(default_factory=list)
    recovered_stale_lock: bool = False
    error_hash: str | None = None
    error_head: str | None = None
    schema_version: int = SCHEMA_VERSION

    def as_dict(self) -> dict:
        return asdict(self)


def append_run(record: RunRecord, state_dir: Path | str | None = None) -> Path:
    root = Path(state_dir or DEFAULT_STATE)
    root.mkdir(parents=True, exist_ok=True)
    path = root / LEDGER_NAME
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(record.as_dict(), ensure_ascii=False) + "\n")
    return path


def read_runs(state_dir: Path | str | None = None) -> list[dict]:
    path = Path(state_dir or DEFAULT_STATE) / LEDGER_NAME
    if not path.exists():
        return []
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return rows


def error_fingerprint(exc: BaseException) -> tuple[str, str]:
    head = f"{type(exc).__name__}: {exc}"[:200]
    return hashlib.sha256(head.encode("utf-8")).hexdigest()[:16], head


def classify_outcome(input_debt: dict, outputs: dict, *, failed: bool,
                     remaining_debt: dict | None = None) -> str:
    """`0 rows` 到底是 NOOP 还是失败 —— **债与产一起看**(§4.2-1)。

    只比**有无**,不比大小:`input_debt` 的单位是「待办日/对」,`outputs` 的单位是
    「跑成功的步骤数」,两者量纲不同,拿 `produced < debt` 判 PARTIAL 会把「2 天欠账、
    1 个步骤成功」误判成半成品(单测逮到)。

    真要判 PARTIAL,调用方得给出**同量纲**的 `remaining_debt`(跑完之后重新数一遍的
    欠账)—— 那才是「还剩多少没还」的可比读数。
    """
    if failed:
        return "FAILED"
    debt = sum(int(v or 0) for v in (input_debt or {}).values() if int(v or 0) > 0)
    produced = sum(int(v or 0) for v in (outputs or {}).values())
    if debt == 0:
        return "NOOP"                     # 没有欠账 → 产 0 是对的
    if produced == 0:
        return "STALLED"                  # 有债却一件没产 → 这不是 NOOP
    if remaining_debt is not None:
        left = sum(int(v or 0) for v in remaining_debt.values() if int(v or 0) > 0)
        return "PARTIAL" if left > 0 else "OK"
    return "OK"


# ───────────────────────── 重试 / 超时 ─────────────────────────


def with_retries(fn, *, retries: int = DEFAULT_RETRIES,
                 backoff_base: float = BACKOFF_BASE_SECONDS,
                 sleep=time.sleep) -> dict:
    """指数退避重试 → `{ok, attempts, result, error}`。**限频类失败重试才有意义**,
    所以退避而不是立刻重来。"""
    last: BaseException | None = None
    for attempt in range(1, retries + 2):
        try:
            return {"ok": True, "attempts": attempt, "result": fn(), "error": None}
        except Exception as exc:  # noqa: BLE001 — 夜间任务:记录后继续
            last = exc
            if attempt <= retries:
                sleep(backoff_base * (2 ** (attempt - 1)))
    digest, head = error_fingerprint(last)
    return {"ok": False, "attempts": retries + 1, "result": None,
            "error": {"hash": digest, "head": head}}


# ───────────────────────── 主入口 ─────────────────────────


def collect_debts(today: str) -> dict:
    """开工前的输入债务快照 —— ledger 的 `input_debt`。各步独立 suppress。"""
    debts: dict[str, int] = {}
    try:
        from autoresearch.learning.retro import pending_days
        debts["retro_pending"] = len(pending_days(today) or [])
    except Exception:  # noqa: BLE001
        debts["retro_pending"] = -1        # -1 = 读不出来,**不是** 0
    try:
        from autoresearch.learning.t1_review import pending_pairs
        debts["t1_pending"] = len(pending_pairs(today) or [])
    except Exception:  # noqa: BLE001
        debts["t1_pending"] = -1
    return debts


def run_once(today: str | None = None, *, state_dir: Path | str | None = None,
             timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
             runner=None, now: datetime | None = None) -> dict:
    """跑一次夜间批(加固版)。**不改 nightly_close 的内容**,只包运行安全。"""
    day = today or _date.today().isoformat()
    started = now or _now()
    run_id = f"nr_{started.strftime('%Y%m%dT%H%M%S')}_{os.getpid()}"

    env = check_environment()
    if not env["ok"]:
        record = RunRecord(run_id=run_id, day=day, idempotency_key="",
                           started_at=started.isoformat(),
                           finished_at=_now().isoformat(), status="ENV_FAIL",
                           error_head="; ".join(env["problems"]))
        append_run(record, state_dir)
        return {"status": "ENV_FAIL", "env": env, "record": record.as_dict()}

    debts = collect_debts(day)
    key = idempotency_key(day, debts)
    prior = [r for r in read_runs(state_dir)
             if r.get("idempotency_key") == key and r.get("status") in
             ("OK", "NOOP", "PARTIAL")]
    if prior:
        return {"status": "SKIPPED_IDEMPOTENT", "idempotency_key": key,
                "prior_run_id": prior[-1]["run_id"],
                "note": "同日同债务快照已跑过 —— 幂等跳过"}

    try:
        with exclusive(state_dir, now=started, run_id=run_id) as lock_info:
            record = RunRecord(run_id=run_id, day=day, idempotency_key=key,
                               started_at=started.isoformat(), input_debt=debts,
                               recovered_stale_lock=lock_info["recovered_stale_lock"])
            beat(state_dir, run_id=run_id, step="start")
            work = runner or _default_runner
            deadline = time.monotonic() + timeout_seconds
            attempt = with_retries(lambda: work(day, state_dir, run_id, deadline))
            if attempt["ok"]:
                steps = attempt["result"] or []
                record.steps = [{"step": n, "ok": ok, "note": note}
                                for n, ok, note in steps]
                record.outputs = {n: (1 if ok else 0) for n, ok, _ in steps}
                record.status = classify_outcome(debts, record.outputs, failed=False)
            else:
                record.status = "FAILED"
                record.error_hash = attempt["error"]["hash"]
                record.error_head = attempt["error"]["head"]
            record.finished_at = _now().isoformat()
            append_run(record, state_dir)
            return {"status": record.status, "record": record.as_dict(),
                    "debts": classify_debts(debts)}
    except LockBusy as exc:
        return {"status": "LOCK_BUSY", "reason": str(exc)}


def _default_runner(day: str, state_dir, run_id: str, deadline: float):
    """默认腿 = 既有 `nightly_close.run`(**不重写它的内容**)。"""
    from autoresearch.learning.nightly_close import run as close_run

    beat(state_dir, run_id=run_id, step="nightly_close")
    if time.monotonic() > deadline:
        raise TimeoutError(f"超时:{day} 的夜间批未在预算内开始")
    return close_run(day)


# ───────────────────────── launchd ─────────────────────────


def launchd_plist(*, label: str = "com.autoresearch.nightly",
                  hour: int = 20, minute: int = 30,
                  repo: Path | str | None = None) -> str:
    """launchd plist —— **显式设 WorkingDirectory**。

    launchd 默认 cwd 是 `/`,而本仓库全是相对路径(`context/`、`reports/`)。
    不设 WorkingDirectory 的话任务会"成功"地把产物写到根目录下,而 prelude 依旧报欠账。
    """
    root = str(Path(repo or Path.cwd()).resolve())
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
  "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>{label}</string>
  <key>ProgramArguments</key>
  <array>
    <string>/bin/zsh</string><string>-lc</string>
    <string>cd {root} &amp;&amp; uv run --no-sync python -m autoresearch.learning.nightly_runner run</string>
  </array>
  <!-- launchd 默认 cwd 是 / —— 不设这一行,产物会落到根目录下而任务照样"成功" -->
  <key>WorkingDirectory</key><string>{root}</string>
  <key>StartCalendarInterval</key>
  <dict><key>Hour</key><integer>{hour}</integer><key>Minute</key><integer>{minute}</integer></dict>
  <key>StandardOutPath</key><string>{root}/context/learning/nightly/stdout.log</string>
  <key>StandardErrorPath</key><string>{root}/context/learning/nightly/stderr.log</string>
  <key>RunAtLoad</key><false/>
</dict>
</plist>
"""


def render_status(state_dir: Path | str | None = None) -> str:
    runs = read_runs(state_dir)[-10:]
    beat_now = read_heartbeat(state_dir)
    age = lock_age_seconds(state_dir)
    lines = ["# 夜间跑批状态", "",
             f"- 心跳:{'无' if not beat_now else beat_now.get('at')}"
             + ("" if age is None else f"({age:.0f}s 前)"),
             f"- 近 {len(runs)} 次运行", "",
             "| run_id | 日期 | 状态 | 输入债务 | 产出 | 回收陈旧锁 | error |",
             "|---|---|---|---|---|---|---|"]
    for row in runs:
        debt = sum(int(v or 0) for v in (row.get("input_debt") or {}).values()
                   if int(v or 0) > 0)
        produced = sum(int(v or 0) for v in (row.get("outputs") or {}).values())
        lines.append(f"| `{row['run_id']}` | {row['day']} | {row['status']} | {debt} "
                     f"| {produced} | {'是' if row.get('recovered_stale_lock') else '否'} "
                     f"| {row.get('error_hash') or '—'} |")
    if not runs:
        lines.append("| — | — | — | — | — | — | — |")
    lines += ["", "> `NOOP`(债 0 产 0)与 `STALLED`(有债却一件没产)必须分开读 —— "
              "两者的行数都是 0。", ""]
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="夜间跑批加固(§4.2)")
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run", help="跑一次(加固版)")
    r.add_argument("date", nargs="?", default=None)
    r.add_argument("--state-dir", default=None)
    s = sub.add_parser("status", help="心跳 + 近期运行")
    s.add_argument("--state-dir", default=None)
    p = sub.add_parser("plist", help="打印 launchd plist")
    p.add_argument("--hour", type=int, default=20)
    p.add_argument("--minute", type=int, default=30)

    a = ap.parse_args(argv)
    if a.cmd == "plist":
        print(launchd_plist(hour=a.hour, minute=a.minute))
        return 0
    if a.cmd == "status":
        print(render_status(a.state_dir))
        return 0
    result = run_once(a.date, state_dir=a.state_dir)
    print(json.dumps({k: v for k, v in result.items() if k != "record"},
                     ensure_ascii=False, indent=2))
    # 夜间任务失败不该让 launchd 红灯常亮 —— 状态看 ledger(沿用 nightly_close 的立场)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
