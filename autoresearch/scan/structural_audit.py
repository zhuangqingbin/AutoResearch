#!/usr/bin/env python3
"""流式 L4 的结构失败计量 —— 给 B5 那根回滚杆一个**真判据**(确定性,零 LLM)。

design: docs/specs/2026-08-01-wave10-report-ops-slimdown-zerobuy-design.md §B5
补的是 Wave10 实施时记下的未做项:B5 写明「连续 N 次真实流式扫描 `structural_failure_n == 0`
才允许退役兜底」,但**全仓没有任何地方落盘 `structural_failure_n`** —— 到第 10 次时只能靠人
回忆有没有出过结构失败,等于没判据。判据不可回忆,只可计量。

四种结构失败(口径固定,见 §B5):

| kind | 模式 | 检测点 | 快照可判 |
|---|---|---|---|
| `TASK_BOOK_MISSING`   | task-book 丢失     | 事后快照 | ✅ |
| `TERMINAL_RERUN`      | 错误重跑成功票     | `preflight` | ❌ 仅活体 |
| `COMPLETION_MISJUDGED`| 完成态误判         | `mark_success` / STALE | ❌ 仅活体 |
| `ARTIFACT_HASH_MISMATCH` | 产物 hash 失配  | `preflight` + 事后重验 | ✅ |

**两条纪律,写死在结构里**:

1. **UNMEASURED ≠ CLEAN**。计量上线前的日子,活体三类事件根本没人记 —— 那些天的 0 是
   「没看」不是「没有」。这类日**打断连续计数**,不得计入 streak。整个 Wave10 反复栽在
   同一件事上(不判 ≠ 通过),这里不再让它复发。
2. **守卫拦住 ≠ 失败**。`preflight` 对已成功票返回 SKIP 是守卫**正常工作**(断点续跑
   本来就会重派),记作观察量 `TERMINAL_REDISPATCH_BLOCKED`,不进失败数;真正的失败是
   守卫被绕过、成功票**真的又跑了一遍**(`TERMINAL_RERUN`)。后者在现行代码里结构上不可达,
   所以这个计数器的职责是**逮未来的回归**,常态恒 0 才是对的。

  uv run --no-sync python -m autoresearch.scan.structural_audit [--days N]
"""
from __future__ import annotations

import contextlib
import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from autoresearch.common import workspace as ws

SCHEMA_VERSION = 1

# —— 失败类 kind:计入 structural_failure_n ——
TASK_BOOK_MISSING = "TASK_BOOK_MISSING"
TERMINAL_RERUN = "TERMINAL_RERUN"
COMPLETION_MISJUDGED = "COMPLETION_MISJUDGED"
ARTIFACT_HASH_MISMATCH = "ARTIFACT_HASH_MISMATCH"
FAILURE_KINDS = (TASK_BOOK_MISSING, TERMINAL_RERUN,
                 COMPLETION_MISJUDGED, ARTIFACT_HASH_MISMATCH)

# —— 观察类 kind:守卫正常工作 / 已知良性的证据,**不进失败数** ——
TERMINAL_REDISPATCH_BLOCKED = "TERMINAL_REDISPATCH_BLOCKED"
PROMPT_REBUILT = "PROMPT_REBUILT"
OBSERVATION_KINDS = (TERMINAL_REDISPATCH_BLOCKED, PROMPT_REBUILT)

# 事后重验里，`prompt` 与 `slim`/`card` 的地位**不对称**：
#   · prompt 是**可重建的输入**，开发期重跑 `write_dispatch_pack` 会原地覆写它。实测
#     07-28..07-31 每一天都发生过（07-29 的 7 份 prompt 全在 07-30 11:07:53 同秒被重写，
#     正是 write_dispatch_pack 那次修复的验证跑）。它事后变了，不代表那次运行结构不成立。
#   · slim/card 是**结果**。结果在完成后变了，才是真的「产物 hash 失配」。
# 活体那条路不受此影响：真在运行中变了的 prompt 由 `preflight._verified` 当场逮住并记
# ARTIFACT_HASH_MISMATCH（失败类）。这里只是不让开发期噪音把事后账淹掉。
REBUILDABLE_ARTIFACTS = ("prompt",)

EVENTS_KEY = "structural_events"      # 无此键 = 计量上线前的账本 = 活体类不可测
UNMEASURED = "UNMEASURED"


def new_log() -> list:
    """`initialize()` 写进账本的空事件表。

    **必须显式写空表**:靠首次事件懒建键的话,「干净的已计量日」和「计量前的日子」
    在盘上长得一模一样 —— 而这两者的语义正好相反。
    """
    return []


def record(payload: dict, code: str, kind: str, detail: str = "",
           *, now: datetime | None = None) -> dict | None:
    """就地把一条结构事件追加进 task-book payload;调用方负责落盘(已在锁内)。

    计量上线前的账本(无 `EVENTS_KEY`)**不补建** —— 补了就等于给那天伪造了「已计量」
    的身份,而它的历史事件早已丢失。返回 None 表示这天本就不可测。
    """
    if kind not in FAILURE_KINDS and kind not in OBSERVATION_KINDS:
        raise ValueError(f"unknown structural event kind:{kind}")
    log = payload.get(EVENTS_KEY)
    if log is None:
        return None
    stamp = (now or datetime.now(timezone.utc)).strftime("%Y-%m-%dT%H:%M:%SZ")
    event = {"ts": stamp, "code": str(code).split(".")[0].zfill(6),
             "kind": kind, "detail": str(detail)[:200]}
    log.append(event)
    return event


@dataclass(frozen=True)
class DayAudit:
    date: str
    measured: bool                 # 活体三类是否可测(账本带 EVENTS_KEY)
    failure_n: int | None          # 不可测日为 None,**不是 0**
    observation_n: int
    kinds: dict                    # {kind: n}
    detail: str

    @property
    def clean(self) -> bool | None:
        return None if self.failure_n is None else self.failure_n == 0


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _rehash_findings(payload: dict) -> list[dict]:
    """事后重验:账本记的 hash 与盘上现值是否仍一致(快照可判的那半)。

    只查 SUCCEEDED 票 —— 非终态票的产物本来就允许在变。
    """
    out = []
    for code, task in sorted((payload.get("tasks") or {}).items()):
        if task.get("status") != "SUCCEEDED":
            continue
        for name in ("prompt", "slim", "card"):
            ref = (task.get("artifacts") or {}).get(name) or {}
            recorded = ref.get("content_hash")
            path = Path(str(ref.get("path") or ""))
            if not recorded:
                out.append({"code": code, "kind": COMPLETION_MISJUDGED,
                            "detail": f"{name}:SUCCEEDED 但无 content_hash"})
            elif not path.is_file():
                out.append({"code": code, "kind": ARTIFACT_HASH_MISMATCH,
                            "detail": f"{name}:产物已不在盘上"})
            elif _sha256(path) != recorded:
                kind = (PROMPT_REBUILT if name in REBUILDABLE_ARTIFACTS
                        else ARTIFACT_HASH_MISMATCH)
                out.append({"code": code, "kind": kind,
                            "detail": f"{name}:hash 与账本不符"})
    return out


def audit_day(scan_dir: Path | str, *, expect_book: bool = False) -> DayAudit | None:
    """单日结构审计。非流式日(无 task-book 且不强求)→ None,不入账。"""
    scan = Path(scan_dir)
    book = scan / "_l4_tasks.json"
    if not book.is_file():
        if not expect_book:
            return None
        return DayAudit(scan.name, True, 1, 0, {TASK_BOOK_MISSING: 1},
                        "task-book 丢失(该日应有流式 L4)")
    try:
        payload = json.loads(book.read_text(encoding="utf-8"))
    except Exception as e:                            # noqa: BLE001
        return DayAudit(scan.name, True, 1, 0, {TASK_BOOK_MISSING: 1},
                        f"task-book 不可解析:{type(e).__name__}")

    live = payload.get(EVENTS_KEY)
    kinds: dict[str, int] = {}
    for event in (live or []):
        kind = str(event.get("kind") or "")
        kinds[kind] = kinds.get(kind, 0) + 1
    for finding in _rehash_findings(payload):         # 事后重验,与活体事件合并去重前先分类
        kinds[finding["kind"]] = kinds.get(finding["kind"], 0) + 1

    observation_n = sum(n for k, n in kinds.items() if k in OBSERVATION_KINDS)
    failure_n = sum(n for k, n in kinds.items() if k in FAILURE_KINDS)
    measured = live is not None
    if not measured:
        # 快照那半仍然算得出,但活体三类这天根本没人记 —— 整日判为不可测。
        detail = f"计量上线前的账本(无 {EVENTS_KEY});快照重验发现 {failure_n} 处"
        return DayAudit(scan.name, False, None, observation_n, kinds, detail)
    return DayAudit(scan.name, True, failure_n, observation_n, kinds,
                    "、".join(f"{k}×{n}" for k, n in sorted(kinds.items())) or "无事件")


def roll(scan_root: Path | str | None = None) -> list[DayAudit]:
    root = Path(scan_root or ws.scan_root())
    if not root.exists():
        return []
    out = []
    for day in sorted(p for p in root.iterdir() if p.is_dir() and p.name[:2] == "20"):
        audit = audit_day(day)
        if audit is not None:
            out.append(audit)
    return out


def streak(audits: list[DayAudit]) -> dict:
    """B5 判据:**最近一段连续可测且干净**的流式扫描次数。

    不可测日与失败日**同样打断** —— 「没看」不能冒充「没有」。
    """
    n = 0
    broken_by = None
    for audit in reversed(audits):
        if audit.clean:
            n += 1
            continue
        broken_by = f"{audit.date}({'不可测' if not audit.measured else audit.detail})"
        break
    measured = [a for a in audits if a.measured]
    return {
        "streak": n,
        "streak_broken_by": broken_by,
        "runs_total": len(audits),
        "runs_measured": len(measured),
        "runs_unmeasured": len(audits) - len(measured),
        "failure_n_total": sum(a.failure_n or 0 for a in measured),
        "as_of": audits[-1].date if audits else None,
    }


def render(audits: list[DayAudit], *, required: int = 10) -> list[str]:
    out = ["# 流式 L4 结构失败账本(B5 回滚杆的判据)", ""]
    if not audits:
        return out + ["_无 `_l4_tasks.json`:尚无流式扫描日_"]
    s = streak(audits)
    ok = s["streak"] >= required
    out += [
        f"- **连续干净 {s['streak']}/{required} 次**"
        + ("" if ok else " → 未到期,**不得退役兜底**")
        + (f";被 {s['streak_broken_by']} 打断" if s["streak_broken_by"] else ""),
        f"- 分母同屏:{s['runs_total']} 个流式日,其中**可测 {s['runs_measured']}**、"
        f"不可测 {s['runs_unmeasured']}(计量上线前);可测日累计结构失败 "
        f"**{s['failure_n_total']}** 次 · as-of {s['as_of']}",
        "",
        "| 日期 | 可测 | 结构失败 | 守卫拦截 | 明细 |",
        "|---|---|---:|---:|---|",
    ]
    for a in audits[-15:]:
        out.append(f"| {a.date} | {'✅' if a.measured else '—'} "
                   f"| {UNMEASURED if a.failure_n is None else a.failure_n} "
                   f"| {a.observation_n} | {a.detail} |")
    out += [
        "",
        "_**不可测 ≠ 干净**:计量上线前的日子没人记活体事件,那些天的 0 是「没看」不是"
        "「没有」,故与失败日同样打断连续计数。_",
        "_守卫拦截(`TERMINAL_REDISPATCH_BLOCKED`)是断点续跑的正常现象,**不进失败数**;"
        "真失败是守卫被绕过、成功票又跑一遍(`TERMINAL_RERUN`,现行代码结构上不可达,"
        "该计数器只为逮未来回归)。_",
    ]
    return out


def main(argv: list[str] | None = None) -> int:
    import argparse
    ap = argparse.ArgumentParser(description="流式 L4 结构失败账本(零 LLM)")
    ap.add_argument("--required", type=int, default=10)
    a = ap.parse_args(argv)
    audits = roll()
    target = ws.reports_root() / "learning/structural_audit.md"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("\n".join(render(audits, required=a.required)) + "\n",
                      encoding="utf-8")
    s = streak(audits)
    print(f"[structural_audit] {s['runs_total']} 流式日(可测 {s['runs_measured']})"
          f" · 连续干净 {s['streak']}/{a.required} → {target}")
    return 0


if __name__ == "__main__":
    with contextlib.suppress(BrokenPipeError):
        raise SystemExit(main())
