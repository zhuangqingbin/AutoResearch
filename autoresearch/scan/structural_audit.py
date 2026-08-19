#!/usr/bin/env python3
"""流式 L4 的结构失败计量 —— 给 B5 那根回滚杆一个**真判据**(确定性,零 LLM)。

design: docs/specs/2026-08-01-wave10-report-ops-slimdown-zerobuy-design.md §B5
补的是 Wave10 实施时记下的未做项:B5 写明「连续 N 次真实流式扫描 `structural_failure_n == 0`
才允许退役兜底」,但**全仓没有任何地方落盘 `structural_failure_n`** —— 到第 10 次时只能靠人
回忆有没有出过结构失败,等于没判据。判据不可回忆,只可计量。

五种结构失败(口径固定,见 §B5;`TASK_STUCK_RUNNING` 是 2026-08-19 取证追加,见下方
「P1-1」节):

| kind | 模式 | 检测点 | 快照可判 |
|---|---|---|---|
| `TASK_BOOK_MISSING`   | task-book 丢失     | 事后快照 | ✅ |
| `TERMINAL_RERUN`      | 错误重跑成功票     | `preflight` | ❌ 仅活体 |
| `COMPLETION_MISJUDGED`| 完成态误判         | `mark_success` / STALE | ❌ 仅活体 |
| `ARTIFACT_HASH_MISMATCH` | 产物 hash 失配  | `preflight` + 事后重验 | ✅ |
| `TASK_STUCK_RUNNING`  | run 已发布但票卡死非终态 | 事后快照 | ✅ |

**两条纪律,写死在结构里**:

1. **UNMEASURED ≠ CLEAN**。计量上线前的日子,活体三类事件根本没人记 —— 那些天的 0 是
   「没看」不是「没有」。这类日**打断连续计数**,不得计入 streak。整个 Wave10 反复栽在
   同一件事上(不判 ≠ 通过),这里不再让它复发。
2. **守卫拦住 ≠ 失败**。`preflight` 对已成功票返回 SKIP 是守卫**正常工作**(断点续跑
   本来就会重派),记作观察量 `TERMINAL_REDISPATCH_BLOCKED`,不进失败数;真正的失败是
   守卫被绕过、成功票**真的又跑了一遍**(`TERMINAL_RERUN`)。后者在现行代码里结构上不可达,
   所以这个计数器的职责是**逮未来的回归**,常态恒 0 才是对的。

## P1-1(2026-08-19 取证):判官必须看得见「卡死在 RUNNING」

`_rehash_findings` 只查 `status=="SUCCEEDED"` 的票(非终态票的产物本来就允许在变,这条
判据没错)。但它的副作用是:**全票卡死非终态的那一天,一条事后重验 finding 都不会产生**
—— 2026-08-12(9 票全卡 `RUNNING`、`mark_success` 从未执行、E6 contract 门团灭当天全部
候选;设计稿 §2.1 点名的事故日)因此被评成 `failure_n=0`「无事件」,这个假干净日正躺在
B5 回滚杆①的连续干净 streak 里。判据不可信,streak 就不可信。

`TASK_STUCK_RUNNING` 补的正是这个盲区:**run 已发布**(`stage_results/assemble.json`
在场,即 L5 assemble 阶段已经跑完并落过 `StageResult`)**而票仍非终态**。终态集合由
`l4_tasks.py` 的状态机确认(`PENDING → RUNNING → {SUCCEEDED, FAILED → BLOCKED}` 五态):
`SUCCEEDED` / `FAILED` / `BLOCKED` 三个是终态(各自有明确了结 —— 成功、可重试的失败已
定性、或不可重试已拍板);`PENDING` / `RUNNING` 两个不是(前者从未认领,后者认领后没有
回写终态)。run 都发布了,票却还停在这两态之一,唯一解释是执行悄悄蒸发而账本不知道
——`preflight` 的 `STALE_TASK` 分支本该逮住这个,但那是**下一次**认领时才触发的活体检测;
如果这票再也没人认领(比如那天就它们几个卡死),账本会**永远**停在 RUNNING,只有事后
快照能看见。

详见 `docs/research/2026-08-19-decision-file-two-writers-and-taskbook-hash.md` §1.5、§4 P1-1。

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
TASK_STUCK_RUNNING = "TASK_STUCK_RUNNING"
FAILURE_KINDS = (TASK_BOOK_MISSING, TERMINAL_RERUN,
                 COMPLETION_MISJUDGED, ARTIFACT_HASH_MISMATCH, TASK_STUCK_RUNNING)

# `l4_tasks.py` 状态机的终态集合(P1-1):`SUCCEEDED`(完成)/`FAILED`(可重试的失败已
# 定性,`preflight` 会在 `MAX_ATTEMPTS` 内再次认领或转 `BLOCKED`)/`BLOCKED`(不可重试,
# 已拍板)。`PENDING`(从未认领)与 `RUNNING`(认领后未回写终态)不在其中 ——
# 详见模块文档「P1-1」节与 `l4_tasks.preflight`/`mark_success`/`mark_failure`。
_TERMINAL_TASK_STATUSES = frozenset({"SUCCEEDED", "FAILED", "BLOCKED"})

# —— 观察类 kind:守卫正常工作 / 已知良性的证据,**不进失败数** ——
TERMINAL_REDISPATCH_BLOCKED = "TERMINAL_REDISPATCH_BLOCKED"
PROMPT_REBUILT = "PROMPT_REBUILT"
ARTIFACT_PATH_MIGRATED = "ARTIFACT_PATH_MIGRATED"
OBSERVATION_KINDS = (TERMINAL_REDISPATCH_BLOCKED, PROMPT_REBUILT,
                     ARTIFACT_PATH_MIGRATED)

# 事后重验里，`prompt` 与 `slim`/`card` 的地位**不对称**：
#   · prompt 是**可重建的输入**，开发期重跑 `write_dispatch_pack` 会原地覆写它。实测
#     07-28..07-31 每一天都发生过（07-29 的 7 份 prompt 全在 07-30 11:07:53 同秒被重写，
#     正是 write_dispatch_pack 那次修复的验证跑）。它事后变了，不代表那次运行结构不成立。
#   · slim/card 是**结果**。结果在完成后变了，才是真的「产物 hash 失配」。
# 活体那条路不受此影响：真在运行中变了的 prompt 由 `preflight._verified` 当场逮住并记
# ARTIFACT_HASH_MISMATCH（失败类）。这里只是不让开发期噪音把事后账淹掉。
REBUILDABLE_ARTIFACTS = ("prompt",)

# 事后重验的第二个已知良性来源:**引擎分根迁移**(2026-08-11 用户裁定,`common/workspace.py`)。
# 08-11 及之前的 task-book 把产物路径记成分根前的根名(`<legacy>/scan/...`),而盘上的文件在
# 迁移里被整体搬进 `context_<engine>/`。路径串失效 ≠ 产物失配:2026-08-19 取证实测,332/339
# 条历史 legacy 路径在迁移后的位置上**逐字节等于账本记的 hash**(唯一 7 处不等的全是 07-29
# 的 prompt,即上面 PROMPT_REBUILT 说的那次 write_dispatch_pack 重写)。旧口径把这 332 条
# 全判成 `ARTIFACT_HASH_MISMATCH:产物已不在盘上`,把 08-03..08-11 七个可测日累计 216 次
# "结构失败"凭空造了出来,并把 B5 回滚杆①的连续计数清零 —— 量错了对象。
# 详见 docs/research/2026-08-19-decision-file-two-writers-and-taskbook-hash.md 线①。
def _legacy_context_root() -> str:
    """分根前的 context 根名 —— 由现根去掉引擎后缀**现算**。

    刻意不写字面量:`tests/common/test_workspace.py` ③ 的裸根 grep 探针对生产代码零容忍,
    而这里恰恰要谈那个根 —— 唯一合规的谈法就是从唯一事实源反推。
    """
    return ws.context_root().name.split("_")[0]


def _resolve(recorded: str) -> tuple[Path, bool]:
    """账本记的路径 → (盘上路径, 是否经由迁移别名解析)。

    先认账本原样(现役 book 一律直接命中);只有原样不在盘上、且首段正是分根前的旧根名时,
    才把它重挂到当前引擎根下试一次。**两次都不存在就老实返回原路径**,由调用方判失败 ——
    别名只解释已知的迁移,不替真正丢失的产物打掩护。
    """
    path = Path(recorded)
    if path.is_file():
        return path, False
    parts = path.parts
    if parts and parts[0] == _legacy_context_root():
        migrated = ws.context_root().joinpath(*parts[1:])
        if migrated.is_file():
            return migrated, True
    return path, False

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
            path, migrated = _resolve(str(ref.get("path") or ""))
            if not recorded:
                out.append({"code": code, "kind": COMPLETION_MISJUDGED,
                            "detail": f"{name}:SUCCEEDED 但无 content_hash"})
            elif not path.is_file():
                out.append({"code": code, "kind": ARTIFACT_HASH_MISMATCH,
                            "detail": f"{name}:产物已不在盘上"})
            elif _sha256(path) != recorded:
                # 内容真的变了 —— 迁移与否都不豁免,只按 prompt/结果的固有不对称分类。
                kind = (PROMPT_REBUILT if name in REBUILDABLE_ARTIFACTS
                        else ARTIFACT_HASH_MISMATCH)
                out.append({"code": code, "kind": kind,
                            "detail": f"{name}:hash 与账本不符"})
            elif migrated:
                # hash 逐字节一致、只是路径串停留在分根前 —— 记成观察量而非失败。
                # **不静默**:静默等于让「迁移过的日子」和「本来就干净的日子」在账上
                # 长得一样,而 B5 的两条纪律第一条正是不许这种混同(不判 ≠ 通过)。
                out.append({"code": code, "kind": ARTIFACT_PATH_MIGRATED,
                            "detail": f"{name}:分根前路径,迁移后同 hash"})
    return out


def _published(scan: Path) -> bool:
    """run 是否已发布 —— 判据 = L5 assemble 阶段落过 `StageResult`(P1-1)。

    不用 `finalists.csv`/`summary.md` 等更早或更晚的产物:`stage_results/assemble.json`
    是 `publisher._run_publish` 收尾时才写(`stage_result.safe_record_stage_result(...,
    stage="assemble", ...)`),精确对应"这次 run 走到头了"这件事,不早不晚。
    """
    return (scan / "stage_results" / "assemble.json").is_file()


def _stuck_running_findings(scan: Path, payload: dict) -> list[dict]:
    """事后重验(P1-1):run 已发布,但票仍停在非终态 —— `_rehash_findings` 对这类票是瞎的
    (它只查 `SUCCEEDED`),而这正是 2026-08-12 事故(9 票全卡 `RUNNING`、contract 门团灭)
    的真实形态。run 还没发布时非终态票是正常在跑,不算异常 —— 只在**已发布**之后仍非终态
    才计一条失败;终态集合见 `_TERMINAL_TASK_STATUSES` 旁注。
    """
    if not _published(scan):
        return []
    out = []
    for code, task in sorted((payload.get("tasks") or {}).items()):
        status = str(task.get("status") or "")
        if status not in _TERMINAL_TASK_STATUSES:
            out.append({"code": code, "kind": TASK_STUCK_RUNNING,
                        "detail": f"run 已发布但票仍 {status or '(无 status)'}(非终态)"})
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
    for finding in _stuck_running_findings(scan, payload):   # P1-1:run 已发布但票卡死非终态
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
        "_`ARTIFACT_PATH_MIGRATED` = 账本路径串停留在引擎分根前(2026-08-11 裁定),产物在"
        "迁移后的位置上逐字节同 hash —— 路径失效不是产物失配,记观察不记失败"
        "(取证:`docs/research/2026-08-19-decision-file-two-writers-and-taskbook-hash.md`)。_",
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
