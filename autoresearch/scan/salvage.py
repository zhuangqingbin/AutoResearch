#!/usr/bin/env python3
"""逐文件抢救 + 归属约束 —— scene-reconstruction Task 9。

design: `docs/superpowers/specs/2026-09-12-scene-reconstruction-transcript-
binding-design.md` §6.3(抢救件的字段与归属四态)、§6.1(离线索引的输入顺序,
本模块是它的上游供给方之一)、§2 不变量 3/9、§9 产物边界。

## 这解决什么

Task 1-5 把**新** run 的证据留存管好了;但历史上 13 个已发布 run 的 E6 决策
(`_relative_buy_decision.json`)与市场研判(`market_view.md`)**只**写进过按数据日
键的共享 staging(`context_<engine>/scan/<date>/`),同数据日重跑会原地覆盖 ——
run 目录本身对这两份文件一无所知(2026-08-26 之前,`retain()` 的 `mirror_staging`
还不存在)。本模块回答:对一个**已冻结**的 run,这两份文件、以及它当时的
transcript,今天还能不能找回;找回的东西**有多可信**;不可信的东西绝不能冒充
「本 run 当天的证据」进入 BUY / 收益 / 覆盖率结论。

## 归属四态(spec §6.3,唯一权威)

- ``VERIFIED_RUN``——有 run 身份(session_ref 精确匹配定位到的 transcript)或原有
  内容哈希(共享文件当前字节与本 run 已验证 transcript 自己记录的写入哈希一致)
  等强证据。**只有这一态可以充当本 run 的事实**(:func:`is_fact`)。
- ``TIME_WINDOW_ONLY``——只有 mtime 落在本 run 取证窗口附近这一条线索。mtime 只是
  线索,不是证明(controller ruling 1),**永远不能**支持 BUY / 收益 / 覆盖率结论,
  只作参考。
- ``OVERWRITTEN_BY_LATER_RUN``——有另一个**同数据日、更晚**发布的 run 自己已归档
  的副本(它自己的 `trace/staging/<file>`,2026-08-26 起才有),其内容哈希与共享
  staging **当前**内容一致 —— 证明共享位置现在装的是那个后续 run 的东西,不是
  本 run 的。
- ``UNKNOWN`` / ``ABSENT``——前者是"找到了东西但判不出来"(时区不明、窗口两端皆
  未知等),后者是"哪都没有"。两者都不可以充当事实。

## 逐文件,不是逐 run

一个 run 已经有 E6 决策不能让它跳过缺失的 market_view / transcript ——
:func:`salvage_run` 对每个文件种类(``e6_decision``/``market_view``/每份定位到
的 transcript)各自独立判定,任一文件已在 run 自己的证据里(`trace/staging/` 或
`trace/`)就直接标 ``VERIFIED_RUN``、无需抢救,不影响其它文件种类各自的判定。

## 复用,不重写

transcript 的读取/归一化/脱敏归档全部走 Task 2 的
`trace.transcripts.snapshot.capture_snapshot` + 对应引擎适配器的
``stats_from_rows`` —— 本模块不再解析一次 transcript、不再算一遍 usage、不再写
第二条脱敏/归档路径(spec §9 明令)。Claude 用
`trace.transcripts.claude.ClaudeTranscriptAdapter.locate`(要求 `session_ref`
精确匹配)定位;Codex 用 `trace.transcripts.codex.discover_rollout_candidates`
(session_ref 优先,退而 cwd 收窄)。

## 写在哪

一切落 `reports_<engine>/scan/_ledger/salvage/<report_run_id>/`
(`provenance.json` + `blobs/<sha256>`,均已在 `contracts/artifacts.py` 登记)——
**从不**写回被抢救的 run 目录本身(不变量 3:冻结 run 的文件集合/内容/MANIFEST
永不改变)。

  uv run --no-sync python -m autoresearch.scan.salvage <report_run_id 或路径>
  uv run --no-sync python -m autoresearch.scan.salvage --all
"""
from __future__ import annotations

import argparse
import json
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

from autoresearch.common import workspace as ws
from autoresearch.common.atomic import atomic_write_json, sha256_bytes
from autoresearch.contracts import artifacts as ca
from autoresearch.scan import run_naming
from autoresearch.scan.outcome import LEDGER_DIRNAME
from autoresearch.scan.relative_buy import DECISION_FILENAME
from autoresearch.trace import blobs as trace_blobs
from autoresearch.trace.transcripts.base import ObservedOperation, RunIdentity, TranscriptRef
from autoresearch.trace.transcripts.claude import ClaudeTranscriptAdapter
from autoresearch.trace.transcripts.codex import CodexTranscriptAdapter, discover_rollout_candidates
from autoresearch.trace.transcripts.snapshot import capture_snapshot

PROVENANCE_SCHEMA_VERSION = 1

#: spec §6.3,逐字复用 —— 四态之外不许再发明第五态。
ATTRIBUTIONS: tuple[str, ...] = (
    "VERIFIED_RUN",
    "TIME_WINDOW_ONLY",
    "OVERWRITTEN_BY_LATER_RUN",
    "UNKNOWN",
    "ABSENT",
)

#: 两份「共享 staging 独有」的机读产物(controller ruling 2:逐文件,不是逐 run)。
#: `market_view.md` 的文件名走登记表(`contracts/artifacts.by_name`),不再手写
#: 第二份字面量;`_relative_buy_decision.json` 走 `relative_buy.py` 自己的常量 ——
#: 两处都是"引用唯一真身",不是本模块重新决定这两个名字该叫什么。
_FLAT_FILE_KINDS: tuple[tuple[str, str], ...] = (
    ("e6_decision", DECISION_FILENAME),
    ("market_view", ca.by_name("market_view").path),
)

#: mtime 归入取证窗口的容差 —— 文档判断行为(写市场研判/决策文件)与
#: `run_contract.created_at`(帧阶段)、`manifest.generated_at`(发布收尾)两个锚点
#: 之间总有正常的流水线耗时。与 Task 3 的 `lookback_days=10` 同类:一个记录在案、
#: 无处可派生的判断常量,不是量出来的精确值。
_WINDOW_TOLERANCE = timedelta(minutes=20)

#: `manifest.generated_at` 是 naive 本机时间,默认(不传 `assume_local_tz`)时
#: 终点未知 —— 但"起点已知、终点未知"不能被当成"无上界,以后任何时刻都算在窗口
#: 内"(那样一份月份之后才被发现的文件也会被判成"落在窗口内"这种荒谬结论)。
#: 用一个保守的、记录在案的最长 run 时长顶替未知终点(同 `_WINDOW_TOLERANCE`/
#: Task 3 `lookback_days=10` 一类:判断常量,不是量出来的精确值)——历史实跑读数
#: (memory:51–74 分钟)远小于这个上限,留足够余量给未来更慢的 run。
_MAX_RUN_DURATION = timedelta(hours=8)


def is_fact(attribution: str) -> bool:
    """只有 ``VERIFIED_RUN`` 可以充当本 run 的事实(controller ruling 1)。

    唯一判据入口 —— provenance 写入、chain_view 的来源提升、mutation probe (a)
    都必须调用这一个函数,不允许在别处再写一次 ``== "VERIFIED_RUN"``。
    """
    return attribution == "VERIFIED_RUN"


# --------------------------------------------------------------- 时刻归一化


def _parse_instant(value: object, *, assume_tz: str | None = None) -> datetime | None:
    """把一个记录下来的时刻值转成 aware UTC ``datetime``;转不出来 → ``None``。

    controller ruling 3:「时区先转换，绝不剥离」。带时区信息的字符串直接转 UTC;
    不带时区信息(naive)且没有人显式声明它是哪个时区时,**不猜** —— 返回
    ``None``(未知),调用方据此把归属判成 ``UNKNOWN`` 而不是编一个假设。
    ``assume_tz`` 只在调用方确实知道那个历史 naive 值记的是哪个时区时才传
    (本模块生产路径从不传 —— `run_forensic_window` 默认 ``None``,不猜)。
    """
    if value is None:
        return None
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return datetime.fromtimestamp(value, tz=timezone.utc)
    if not isinstance(value, str) or not value.strip():
        return None
    text = value.strip()
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is not None and parsed.utcoffset() is not None:
        return parsed.astimezone(timezone.utc)
    if not assume_tz:
        return None
    try:
        from zoneinfo import ZoneInfo

        localized = parsed.replace(tzinfo=ZoneInfo(assume_tz))
    except Exception:  # noqa: BLE001 - 未知/非法 zone key 同样是"不知道",不是崩溃
        return None
    return localized.astimezone(timezone.utc)


def _mtime_instant(path: Path) -> datetime:
    """文件 mtime → aware UTC ``datetime``。`st_mtime` 本身就是 POSIX 纪元秒 ——
    与字符串时间戳不同,它从不存在"时区不明"的问题,不需要 ``assume_tz``。"""
    return datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc)


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _iso(value: datetime | None) -> str | None:
    return value.isoformat(timespec="microseconds") if value is not None else None


# --------------------------------------------------------------- 小工具读写


def _read_json_lenient(path: Path) -> object | None:
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def _read_run_contract(run_dir: Path) -> dict:
    """`run_contract.json` 宽松读 —— 只取字段,不做 `RunContract.from_dict` 的
    整份 hash 校验(那是给"这份契约本身可信"用的;抢救的职责是"能读出多少算多少",
    不该因为某个历史字段格式差异就整份拒读)。"""
    for rel in (Path("trace") / "run_contract.json", Path("run_contract.json")):
        doc = _read_json_lenient(run_dir / rel)
        if isinstance(doc, dict):
            return doc
    return {}


def _read_manifest(run_dir: Path) -> dict:
    doc = _read_json_lenient(run_dir / "manifest.json")
    return doc if isinstance(doc, dict) else {}


# --------------------------------------------------------------- 取证窗口


def run_forensic_window(run_dir: Path, *, assume_local_tz: str | None = None) -> dict:
    """这个 run 自己的取证窗口 —— 起点/终点均来自**这个 run 自己记录的**时刻,
    从不掺入调用者的当前时间(controller ruling 4)。

    - 起点:`trace/run_contract.json`(发布层"原九项白名单"之一,2026-08-26 之前
      的最老 run 也有)的 ``created_at`` —— `RunContract.build` 恒以 UTC + ``Z``
      结尾写入,从 schema 1 起就存在,aware,不需要猜时区。
    - 终点:`manifest.json` 的 ``generated_at`` —— 发布收尾时 ``datetime.now()``
      写入,**naive**(不带时区);默认(``assume_local_tz=None``)时按"时区不明"
      处理,终点留 ``None``,不假设它就是 UTC 或本机时区。
    """
    contract = _read_run_contract(run_dir)
    manifest = _read_manifest(run_dir)
    start = _parse_instant(contract.get("created_at"))
    end = _parse_instant(manifest.get("generated_at"), assume_tz=assume_local_tz)
    return {
        "start": _iso(start),
        "end": _iso(end),
        "start_source": "run_contract.created_at" if start is not None else None,
        "end_source": "manifest.generated_at" if end is not None else None,
    }


def _search_anchor(window: dict) -> datetime | None:
    """The instant to anchor a *historical* rollout search on -- never the real
    wall clock (controller ruling 4: "real window, no fabricated time"; also
    the reason B10's own `discover_rollout_candidates` anchors on ``now`` by
    default, which is correct for an *active* session but wrong for salvaging
    a run from weeks/months ago). Prefers the run's own recorded end, falling
    back to its start, with a one-day safety margin past either (a rollout
    file can be created slightly after the recorded instant -- e.g. seconds
    after `run_contract.created_at`, or crossing local midnight). ``None``
    only when the run's window is entirely unknown, in which case the caller
    falls back to `discover_rollout_candidates`'s own real-``now`` default --
    an honest "we don't know when this happened" degrade, not a guess.
    """
    end = _parse_instant(window.get("end"))
    start = _parse_instant(window.get("start"))
    anchor = end or start
    return anchor + timedelta(days=1) if anchor is not None else None


def _in_window(instant: datetime | None, window: dict) -> tuple[bool | None, str]:
    """*instant* 是否落在 *window*(±`_WINDOW_TOLERANCE`)内。

    两端都未知 → ``(None, ...)``(无法比较,不是"在"也不是"不在" —— 归属据此判
    ``UNKNOWN``,而不是默认判"在窗口内"这种偏乐观的猜测)。只有起点已知、终点
    未知(`manifest.generated_at` 默认按时区不明处理时的常态)不能被当成"无上界,
    以后任何时刻都算在窗口内"——用 `_MAX_RUN_DURATION` 顶替未知终点,一个记录在
    案的保守上限,不是编造的绝对时刻。
    """
    if instant is None:
        return None, "该时刻本身无法解析为可比较的 UTC 值"
    start = _parse_instant(window.get("start"))
    end = _parse_instant(window.get("end"))
    if start is None and end is None:
        return None, "run 取证窗口两端均未知(契约/清单缺失,或终点时区不明)"
    effective_end = end
    if effective_end is None and start is not None:
        effective_end = start + _MAX_RUN_DURATION
    if start is not None and instant < start - _WINDOW_TOLERANCE:
        return False, f"早于窗口起点 {window.get('start')}(容差 {_WINDOW_TOLERANCE})"
    if effective_end is not None and instant > effective_end + _WINDOW_TOLERANCE:
        if end is not None:
            return False, f"晚于窗口终点 {window.get('end')}(容差 {_WINDOW_TOLERANCE})"
        return False, (
            f"窗口终点未知,已超出起点 {window.get('start')} + "
            f"保守上限 {_MAX_RUN_DURATION}(容差 {_WINDOW_TOLERANCE})"
        )
    if end is not None:
        return True, "落在本 run 取证窗口内(含容差)"
    return True, f"落在起点 {window.get('start')} + 保守上限 {_MAX_RUN_DURATION} 内(真实终点未知)"


# --------------------------------------------------------------- 同日多 run


def _report_run_analysis_date(run_dir: Path) -> str | None:
    parsed = run_naming.parse_run_dir(run_dir.name)
    if parsed is not None and parsed.analysis_date:
        return parsed.analysis_date
    manifest = _read_manifest(run_dir)
    date = manifest.get("analysis_date")
    return str(date) if date else None


def _sibling_report_run_ids(
    scan_reports_root: Path, *, analysis_date: str | None, exclude: str
) -> tuple[str, ...]:
    """同一 `analysis_date` 下、除 *exclude* 外的其它已发布 report_run_id(排序)。

    controller ruling 3:「显式检测同日多 run」——本函数是那条检测,`salvage_run`
    把结果原样记进 provenance,不管有没有用到覆盖判定都留痕。
    """
    if not analysis_date or not scan_reports_root.is_dir():
        return ()
    out: list[str] = []
    for child in sorted(scan_reports_root.iterdir()):
        if not child.is_dir() or child.name in (exclude, LEDGER_DIRNAME):
            continue
        if not (child / "manifest.json").is_file():
            continue
        if _report_run_analysis_date(child) == analysis_date:
            out.append(child.name)
    return tuple(out)


def _published_order_key(run_dir: Path) -> tuple:
    """近似的发布先后序 —— 只用于同数据日 run 之间"谁更晚"的相对判断,不用于任何
    绝对时刻比较(那件事窗口/mtime 各自有自己的 tz-aware 路径)。同机器上前后两次
    发布调用的都是同一枚 naive `datetime.now()`,相对顺序在这个前提下是可信的;
    这是一个记录在案的简化,标记给复核者(见任务报告 concerns 一节)。"""
    manifest = _read_manifest(run_dir)
    generated = str(manifest.get("generated_at") or "")
    parsed = run_naming.parse_run_dir(run_dir.name)
    published = parsed.published_date() if parsed else None
    hhmm = parsed.hhmm if parsed else ""
    return (generated, published or "", hhmm, run_dir.name)


def _later_overwrite_row(
    scan_reports_root: Path,
    *,
    target_dir: Path,
    siblings: tuple[str, ...],
    filename: str,
    current_digest: str,
) -> str | None:
    """*filename* 在共享 staging 里**当前**的字节,是否已被证明属于某个更晚发布的
    同数据日 sibling —— 那个 sibling 自己的 `trace/staging/<filename>`
    (2026-08-26 起 `retain()` 才会写)内容哈希与 *current_digest* 相同,即为证据。
    找到 → 返回那个 report_run_id;找不到 → ``None``(不代表本 run 就是事实来源,
    只是没有"被后跑覆盖"的证据 —— 更弱的判定交给调用方继续走 mtime 分支)。
    """
    if not siblings:
        return None
    target_order = _published_order_key(target_dir)
    for sib_id in siblings:
        sib_dir = scan_reports_root / sib_id
        if _published_order_key(sib_dir) <= target_order:
            continue  # 只认"更晚发布"的 sibling,不猜比自己早的那些
        sib_copy = sib_dir / "trace" / "staging" / filename
        if not sib_copy.is_file():
            continue
        try:
            sib_digest = sha256_bytes(sib_copy.read_bytes())
        except OSError:
            continue
        if sib_digest == current_digest:
            return sib_id
    return None


# --------------------------------------------------------------- 身份 + 目录


def _require_current_engine_run(run_dir: Path) -> Path:
    """*run_dir* 必须在**当前引擎**自己的发布根之下 —— 不变量 1/§2:Codex 不得
    读写另一引擎的 reports、反之亦然。校验失败直接抛,不静默改道去别的引擎根。
    """
    resolved = Path(run_dir).resolve()
    scan_root = (ws.reports_root() / "scan").resolve()
    if not resolved.is_relative_to(scan_root):
        raise ValueError(
            f"salvage 只能处理当前引擎({ws.ENGINE})自己的已发布 run;"
            f"{resolved} 不在 {scan_root} 之下"
        )
    return resolved


def _salvage_dir(run_dir: Path, *, ledger_root: Path | None = None) -> Path:
    """`salvage/<report_run_id>/` —— 落 `reports_<engine>/scan/_ledger/salvage/`
    (`ledger_root` 覆盖仅供测试指向 tmp_path;不覆盖时用 *run_dir* 自己的
    grandparent,与 `outcome.ledger_root(reports_root)` 同一约定,不新引入
    第二个"_ledger 在哪"的判据)。"""
    base = Path(ledger_root) if ledger_root is not None else (run_dir.parent / LEDGER_DIRNAME)
    return base / "salvage" / run_dir.name


def _provenance_path(run_dir: Path, *, ledger_root: Path | None = None) -> Path:
    return _salvage_dir(run_dir, ledger_root=ledger_root) / "provenance.json"


def _blob_path(run_dir: Path, digest: str, *, ledger_root: Path | None = None) -> Path | None:
    """The on-disk location of an already-published salvage blob, via the
    **existing** content-addressed store (`trace.blobs.blob_path`) —
    fix-round-1, finding 1: this module never computes a blob's location by
    its own rule. Callers (`resolve_attributed_source`) treat ``None`` as
    "cannot resolve" — never a crash, since this is on a read path a broken
    render must degrade past, not die on (chain_view's own discipline).
    """
    root = _salvage_dir(run_dir, ledger_root=ledger_root)
    try:
        return trace_blobs.blob_path(root, digest)
    except (OSError, ValueError):
        return None


def _store_blob(run_dir: Path, data: bytes, *, ledger_root: Path | None = None) -> dict:
    """Persist *data* through the **existing** evidentiary content-addressed
    store (`trace.blobs.put_bytes`/`blob_path`) — never a second, parallel
    implementation (spec §9's reuse mandate; fix-round-1, finding 1, Critical).

    `put_bytes` computes its own digest from *data* (never trusts a
    caller-supplied one) and, when a file already sits at that digest's path,
    re-hashes and byte-compares it against *data* before ever treating it as
    already-published (`trace.blobs._verify_existing`) — the collision/
    corruption check this module's own first draft omitted entirely by
    trusting whatever bytes already occupied a digest-named path.
    """
    root = _salvage_dir(run_dir, ledger_root=ledger_root)
    root.mkdir(parents=True, exist_ok=True)  # trace.blobs requires an existing real directory
    digest = trace_blobs.put_bytes(root, data)
    rel = trace_blobs.blob_path(root, digest).relative_to(root).as_posix()
    return {"sha256": digest, "bytes": len(data), "path": rel}


# --------------------------------------------------------------- 单份 transcript


@dataclass(frozen=True)
class _TranscriptFind:
    """一份定位到的 transcript,连同能否作证据的判定与"写哈希核对"要用的原始
    operations(不进最终 provenance,只在同一次 `salvage_run` 调用内用于交叉核验
    共享文件,§见 `_flat_file_write_hash_match`)。"""

    logical_name: str
    row: dict
    operations: tuple[ObservedOperation, ...]


def _op_public_dict(op: ObservedOperation) -> dict:
    return {
        "kind": op.kind,
        "path": op.path,
        "artifact_sha256": op.artifact.sha256 if op.artifact is not None else None,
    }


def _transcript_row(
    *,
    logical_name: str,
    source: Path,
    snapshot,
    attribution: str,
    reason: str,
    run_dir: Path,
    engine: str,
    session_ref: str | None,
    captured_at: str,
    ledger_root: Path | None,
) -> dict:
    row: dict = {
        "file_kind": "transcript",
        "logical_name": logical_name,
        "source": str(source),
        "source_sha256": snapshot.source_prefix.sha256,
        "source_bytes": snapshot.source_prefix.byte_count,
        "source_mtime": _iso(_mtime_instant(source)),
        "captured_at": captured_at,
        "attribution": attribution,
        "reason": reason,
        "engine": engine,
        "session_ref": session_ref,
        "snapshot_id": snapshot.snapshot_id,
    }
    if is_fact(attribution):
        row["blob"] = _store_blob(run_dir, snapshot.archive_bytes, ledger_root=ledger_root)
        # Same bytes in -> `trace.blobs.put_bytes` computes the identical sha256
        # `capture_snapshot` already did; read the digest back off the blob record
        # itself rather than keeping two separately-computed copies of one fact.
        row["archive_sha256"] = row["blob"]["sha256"]
    else:
        row["blob"] = None
        row["archive_sha256"] = None
    return row


def _salvage_transcripts(
    run_dir: Path,
    *,
    engine: str,
    session_ref: str | None,
    cwd: Path,
    captured_at: str,
    sessions_root: Path | None,
    ledger_root: Path | None,
    window: dict,
) -> list[_TranscriptFind]:
    """定位 + 快照本 run 身份能证实的每一份 transcript(spec §6.1 输入顺序里
    "对应 harness 尚在的源文件"那一条腿的抢救版本)。

    Claude 与 Codex 各自复用既有定位器(`ClaudeTranscriptAdapter.locate` /
    `discover_rollout_candidates`)—— 两者都已经把"session_ref 精确匹配"当成
    唯一的身份确认路径,本函数不再发明第二套匹配规则。`session_ref` 未知时,
    Claude 侧结构性地无法定位(`locate()` 本身要求它),Codex 侧退化为 cwd 收窄
    (更弱的证据,封顶 `TIME_WINDOW_ONLY`,不是身份确认)。
    """
    run_identity = RunIdentity(
        run_id=run_dir.name, engine=engine, session_ref=session_ref, cwd=cwd
    )
    finds: list[_TranscriptFind] = []

    if engine == "claude":
        if session_ref is None:
            return [
                _TranscriptFind(
                    logical_name="transcript:(unlocatable)",
                    row={
                        "file_kind": "transcript", "logical_name": "transcript:(unlocatable)",
                        "source": None, "source_sha256": None, "source_bytes": None,
                        "source_mtime": None, "captured_at": captured_at,
                        "attribution": "UNKNOWN",
                        "reason": "run 未记录 session_ref;Claude transcript 按设计只能"
                                  "凭 session_ref 精确定位(mtime 不足以证明),不能猜。",
                        "engine": engine, "session_ref": None, "snapshot_id": None,
                        "blob": None, "archive_sha256": None,
                    },
                    operations=(),
                )
            ]
        adapter = ClaudeTranscriptAdapter(projects_root=sessions_root) if sessions_root else (
            ClaudeTranscriptAdapter()
        )
        refs = adapter.locate(run_identity)
        if not refs:
            return [
                _TranscriptFind(
                    logical_name="transcript:(absent)",
                    row={
                        "file_kind": "transcript", "logical_name": "transcript:(absent)",
                        "source": None, "source_sha256": None, "source_bytes": None,
                        "source_mtime": None, "captured_at": captured_at,
                        "attribution": "ABSENT",
                        "reason": f"session_ref={session_ref!r} 已知,但未找到任何候选文件",
                        "engine": engine, "session_ref": session_ref, "snapshot_id": None,
                        "blob": None, "archive_sha256": None,
                    },
                    operations=(),
                )
            ]
        for ref in refs:
            if ref.status != "PRESENT" or ref.path is None:
                continue
            try:
                snapshot = capture_snapshot(ref.path, engine="claude")
            except (FileNotFoundError, ValueError):
                continue
            stats = adapter.stats_from_rows(snapshot.rows, ref)
            name = f"transcript:{ref.role}:{ref.path.name}"
            row = _transcript_row(
                logical_name=name, source=ref.path, snapshot=snapshot,
                attribution="VERIFIED_RUN",
                reason="session_ref 精确匹配(ClaudeTranscriptAdapter.locate 只在会话"
                       "目录/文件名恰好等于 session_ref 时才返回它)",
                run_dir=run_dir, engine=engine, session_ref=session_ref,
                captured_at=captured_at, ledger_root=ledger_root,
            )
            finds.append(_TranscriptFind(name, row, stats.operations))
        return finds

    # codex -- anchor the historical search on *this run's own* recorded time,
    # never on the real wall clock (controller ruling 4; see `_search_anchor`).
    search = discover_rollout_candidates(
        run_identity, sessions_root=sessions_root, lookback_days=10,
        now=_search_anchor(window),
    )
    if not search.candidates:
        return [
            _TranscriptFind(
                logical_name="transcript:(absent)",
                row={
                    "file_kind": "transcript", "logical_name": "transcript:(absent)",
                    "source": None, "source_sha256": None, "source_bytes": None,
                    "source_mtime": None, "captured_at": captured_at,
                    "attribution": "ABSENT",
                    "reason": (
                        f"窗口[{search.searched_from},{search.searched_to}]内扫了 "
                        f"{search.scanned_files} 份 rollout,均不匹配"
                    ),
                    "engine": engine, "session_ref": session_ref, "snapshot_id": None,
                    "blob": None, "archive_sha256": None,
                },
                operations=(),
            )
        ]
    # session_ref 已知时,`discover_rollout_candidates` 的 phase 2 已经把结果精确
    # 收窄到 session_id 匹配的文件——它返回的每一份都已是身份确认,不是猜。
    # session_ref 未知时退化为 cwd 收窄(或完全不收窄),身份证据比 session_ref 弱,
    # 封顶只能是 TIME_WINDOW_ONLY(mtime 分支),不能升格成 VERIFIED_RUN。
    verified_by_identity = session_ref is not None
    adapter = CodexTranscriptAdapter()
    for path in search.candidates:
        try:
            snapshot = capture_snapshot(path, engine="codex")
        except (FileNotFoundError, ValueError):
            continue
        if snapshot.source_changed:
            continue
        ref = TranscriptRef(engine="codex", path=path, role="unbound", session_ref=session_ref)
        try:
            stats = adapter.stats_from_rows(snapshot.rows, ref)
        except Exception:  # noqa: BLE001 - 读不出来的候选就是不算数,不是崩溃
            continue
        name = f"transcript:{path.name}"
        if verified_by_identity:
            attribution, reason = (
                "VERIFIED_RUN",
                "discover_rollout_candidates 已按 session_ref 精确匹配收窄"
                "(该函数 phase 2 只保留 session_id 恰好相同的文件)",
            )
        elif len(search.candidates) == 1:
            in_window, why = _in_window(_mtime_instant(path), window)
            if in_window is True:
                attribution, reason = (
                    "TIME_WINDOW_ONLY",
                    "无 session_ref,仅按 cwd 收窄到唯一候选,且 mtime 落在取证窗口内"
                    "——不构成身份证据,只作参考",
                )
            elif in_window is False:
                attribution, reason = "UNKNOWN", f"cwd 收窄到唯一候选,但 mtime {why}"
            else:
                attribution, reason = "UNKNOWN", why
        else:
            attribution, reason = (
                "UNKNOWN",
                f"无 session_ref,{len(search.candidates)} 份候选互不能区分,不按时间猜",
            )
        row = _transcript_row(
            logical_name=name, source=path, snapshot=snapshot, attribution=attribution,
            reason=reason, run_dir=run_dir, engine=engine, session_ref=session_ref,
            captured_at=captured_at, ledger_root=ledger_root,
        )
        finds.append(_TranscriptFind(name, row, stats.operations))
    return finds


# --------------------------------------------------------------- 平文件(E6/市场研判)


def _flat_file_write_hash_match(
    transcript_finds: list[_TranscriptFind], filename: str, digest: str
) -> str | None:
    """本 run 已验证(VERIFIED_RUN)的某份 transcript,是否自己记录过一次成功写入
    *filename*、且写入哈希恰好等于共享文件**当前**内容的哈希 —— 这是"原有内容
    哈希"这条强证据的落地方式:身份已由 transcript 定位确认,内容哈希证明共享
    位置的字节今天仍是那次写入的原样。"""
    for find in transcript_finds:
        if find.row.get("attribution") != "VERIFIED_RUN":
            continue
        for op in find.operations:
            if op.kind != "WRITE_SUCCEEDED" or op.artifact is None:
                continue
            path = op.path or ""
            if path.endswith(filename) and op.artifact.sha256 == digest:
                return find.logical_name
    return None


def _flat_file_row(
    *,
    file_kind: str,
    filename: str,
    run_dir: Path,
    analysis_date: str | None,
    window: dict,
    siblings: tuple[str, ...],
    scan_reports_root: Path,
    captured_at: str,
    transcript_finds: list[_TranscriptFind],
    ledger_root: Path | None,
) -> dict:
    base = {
        "file_kind": file_kind,
        "logical_name": filename,
        "captured_at": captured_at,
        "same_date_multi_run": bool(siblings),
        "sibling_report_run_ids": list(siblings),
        "overwritten_by": None,
        "blob": None,
    }

    # 1) 已经在 run 自己的证据里(现代 run 的镜像,或 legacy 白名单直拷的 trace/)——
    #    这个文件种类根本不需要抢救,直接是 VERIFIED_RUN(它就是冻结现场本身)。
    for rel in (Path("trace") / "staging" / filename, Path("trace") / filename):
        p = run_dir / rel
        if p.is_file():
            data = p.read_bytes()
            digest = sha256_bytes(data)
            return {
                **base, "source": str(rel), "source_sha256": digest,
                "source_bytes": len(data), "source_mtime": _iso(_mtime_instant(p)),
                "attribution": "VERIFIED_RUN",
                "reason": "该文件已在本 run 自有证据内(无需抢救)",
            }

    if not analysis_date:
        return {
            **base, "source": None, "source_sha256": None, "source_bytes": None,
            "source_mtime": None, "attribution": "UNKNOWN",
            "reason": "本 run 无法确定 analysis_date,无法定位共享 staging",
        }

    shared_path = ws.scan_root() / analysis_date / filename
    if not shared_path.is_file():
        return {
            **base, "source": None, "source_sha256": None, "source_bytes": None,
            "source_mtime": None, "attribution": "ABSENT",
            "reason": "run 自有证据与共享 staging 均无此文件",
        }

    data = shared_path.read_bytes()
    digest = sha256_bytes(data)
    mtime_dt = _mtime_instant(shared_path)
    common = {
        **base, "source": str(shared_path), "source_sha256": digest,
        "source_bytes": len(data), "source_mtime": _iso(mtime_dt),
    }

    # 2) 有更晚发布的同数据日 sibling 自己已归档的副本,内容哈希与共享位置当前
    #    内容一致 —— 明确身份的后跑覆盖(controller ruling 1)。
    overwritten_by = _later_overwrite_row(
        scan_reports_root, target_dir=run_dir, siblings=siblings,
        filename=filename, current_digest=digest,
    )
    if overwritten_by is not None:
        row = {
            **common, "attribution": "OVERWRITTEN_BY_LATER_RUN", "overwritten_by": overwritten_by,
            "reason": f"共享 staging 当前内容的 sha256 与后续 run {overwritten_by} 自己"
                      "归档的副本一致 —— 本 run 的原始版本已不可从共享位置恢复",
        }
        row["blob"] = _store_blob(run_dir, data, ledger_root=ledger_root)
        return row

    # 3) 本 run 已验证的 transcript 自己记录过这次写入、哈希吻合 —— 原有内容哈希。
    write_match = _flat_file_write_hash_match(transcript_finds, filename, digest)
    if write_match is not None:
        row = {
            **common, "attribution": "VERIFIED_RUN",
            "reason": f"共享文件当前 sha256 与本 run 已验证 transcript({write_match})"
                      "自己记录的写入哈希一致",
        }
        row["blob"] = _store_blob(run_dir, data, ledger_root=ledger_root)
        return row

    # 4) 只剩 mtime 这条线索。
    in_window, why = _in_window(mtime_dt, window)
    if in_window is True:
        row = {
            **common, "attribution": "TIME_WINDOW_ONLY",
            "reason": f"仅 mtime 落在本 run 取证窗口内({why});无 run 身份或内容哈希佐证"
                      " —— 只作参考,不得充当 BUY / 收益 / 覆盖率事实",
        }
        row["blob"] = _store_blob(run_dir, data, ledger_root=ledger_root)
        return row
    reason = (
        f"mtime 落在本 run 取证窗口之外({why}),且无更强证据"
        if in_window is False else why
    )
    row = {**common, "attribution": "UNKNOWN", "reason": reason}
    row["blob"] = _store_blob(run_dir, data, ledger_root=ledger_root)
    return row


# --------------------------------------------------------------- provenance 读写


def _row_key(row: dict) -> tuple:
    return (row.get("file_kind"), row.get("logical_name"))


def _merge_rows(existing: dict | None, fresh_rows: list[dict]) -> list[dict]:
    """粘性合并(controller ruling 4:幂等 = 证据不重复、不覆盖,不是靠冻结时钟)。

    已经是 ``VERIFIED_RUN`` 的既有行永远保留原样,不因共享 staging 之后又变了
    就被这次重算的(可能更差的)结论顶替 —— spec §6.3「保留已验证快照，不因
    shared 后续变化覆盖旧件」。非事实的既有行(TIME_WINDOW_ONLY/OVERWRITTEN/
    UNKNOWN/ABSENT)可以被新算出的结果替换——它们本就不是被保护的事实。
    """
    existing_rows = {}
    if isinstance(existing, dict):
        for row in existing.get("files") or ():
            if isinstance(row, dict):
                existing_rows[_row_key(row)] = row
    merged: dict[tuple, dict] = dict(existing_rows)
    for row in fresh_rows:
        key = _row_key(row)
        prior = existing_rows.get(key)
        if prior is not None and prior.get("attribution") == "VERIFIED_RUN":
            merged[key] = prior
        else:
            merged[key] = row
    # 已验证过、这次没有重新算到(比如这次调用没有传 transcript 定位所需的东西)
    # 的旧行同样保留 —— 从不因为"这次没提到"就悄悄丢失一条已确认的事实。
    for key, row in existing_rows.items():
        if row.get("attribution") == "VERIFIED_RUN" and key not in {
            _row_key(r) for r in fresh_rows
        }:
            merged[key] = row
    return [merged[k] for k in sorted(merged, key=lambda k: (str(k[0]), str(k[1])))]


def _counts(rows: list[dict]) -> dict:
    tally = Counter()
    for row in rows:
        attribution = row.get("attribution")
        if attribution == "VERIFIED_RUN":
            tally["verified"] += 1
        elif attribution in ("TIME_WINDOW_ONLY", "OVERWRITTEN_BY_LATER_RUN"):
            tally["reference_only"] += 1
        elif attribution == "ABSENT":
            tally["absent"] += 1
        else:
            tally["unknown"] += 1
    return {
        "verified": tally["verified"], "reference_only": tally["reference_only"],
        "absent": tally["absent"], "unknown": tally["unknown"], "total": len(rows),
    }


def resolve_attributed_source(
    run_dir: Path | str, filename: str, *, ledger_root: Path | None = None
) -> Path | None:
    """给 `chain_view.Sources.find` 用的唯一读点:*filename* 在这个 run 的抢救
    provenance 里,是否有一条 ``VERIFIED_RUN`` 记录 —— 有就返回它对应的、已持久
    化的 blob 路径(**不是**共享 staging 的活文件);没有(不存在、TIME_WINDOW_ONLY、
    OVERWRITTEN_BY_LATER_RUN、UNKNOWN 等)一律 ``None``,调用方据此继续走它自己
    原有的、标"仅供参考"的兜底路径 —— 本函数从不、也不应该把非事实提升成来源。
    """
    run_dir = Path(run_dir)
    doc = _read_json_lenient(_provenance_path(run_dir, ledger_root=ledger_root))
    if not isinstance(doc, dict):
        return None
    for row in doc.get("files") or ():
        if not isinstance(row, dict) or row.get("logical_name") != filename:
            continue
        if not is_fact(row.get("attribution")):
            return None
        blob = row.get("blob")
        if not isinstance(blob, dict) or not blob.get("sha256"):
            return None
        path = _blob_path(run_dir, blob["sha256"], ledger_root=ledger_root)
        return path if path is not None and path.is_file() else None
    return None


# --------------------------------------------------------------- 主入口


def salvage_run(
    run_dir: Path | str, *, ledger_root: Path | None = None, sessions_root: Path | None = None
) -> dict:
    """对一个已冻结的 run,逐文件抢救 E6 决策 / 市场研判 / transcript,写
    provenance 到 ledger,**从不**写回 *run_dir* 自身(不变量 3)。

    ``ledger_root``:覆盖 `_ledger` 目录位置(仅测试用,指向 tmp_path)。
    ``sessions_root``:覆盖 Claude ``~/.claude/projects`` 或 Codex
    ``~/.codex/sessions``(引擎相关,由内部按当前引擎分派)——测试**必须**传,
    生产 CLI 从不传(用各自真实默认值)。
    """
    run_dir = _require_current_engine_run(Path(run_dir))
    engine = ws.ENGINE
    contract = _read_run_contract(run_dir)
    manifest = _read_manifest(run_dir)
    report_run_id = run_dir.name
    contract_run_id = contract.get("run_id") or manifest.get("run_id")
    session_ref = contract.get("session_ref")
    analysis_date = manifest.get("analysis_date") or _report_run_analysis_date(run_dir)
    window = run_forensic_window(run_dir)
    captured_at = _utc_now_iso()
    scan_reports_root = run_dir.parent
    siblings = _sibling_report_run_ids(
        scan_reports_root, analysis_date=analysis_date, exclude=report_run_id
    )

    errors: list[dict] = []
    transcript_finds: list[_TranscriptFind] = []
    try:
        transcript_finds = _salvage_transcripts(
            run_dir, engine=engine, session_ref=session_ref, cwd=Path.cwd(),
            captured_at=captured_at, sessions_root=sessions_root, ledger_root=ledger_root,
            window=window,
        )
    except Exception as exc:  # noqa: BLE001 - transcript 抢救失败不得拖垮平文件抢救
        errors.append({"file_kind": "transcript", "error": f"{type(exc).__name__}: {exc}"})

    fresh_rows: list[dict] = [find.row for find in transcript_finds]
    for file_kind, filename in _FLAT_FILE_KINDS:
        try:
            fresh_rows.append(
                _flat_file_row(
                    file_kind=file_kind, filename=filename, run_dir=run_dir,
                    analysis_date=analysis_date, window=window, siblings=siblings,
                    scan_reports_root=scan_reports_root, captured_at=captured_at,
                    transcript_finds=transcript_finds, ledger_root=ledger_root,
                )
            )
        except Exception as exc:  # noqa: BLE001 - 一个文件种类出错不得拖垮另一个
            errors.append({"file_kind": file_kind, "error": f"{type(exc).__name__}: {exc}"})

    prov_path = _provenance_path(run_dir, ledger_root=ledger_root)
    existing = _read_json_lenient(prov_path)
    merged_rows = _merge_rows(existing if isinstance(existing, dict) else None, fresh_rows)

    doc = {
        "schema_version": PROVENANCE_SCHEMA_VERSION,
        "report_run_id": report_run_id,
        "contract_run_id": contract_run_id,
        "engine": engine,
        "analysis_date": analysis_date,
        "run_window": window,
        "same_date_multi_run": bool(siblings),
        "sibling_report_run_ids": list(siblings),
        "captured_at": captured_at,
        "files": merged_rows,
    }

    wrote = False
    if not isinstance(existing, dict) or _comparable(existing) != _comparable(doc):
        atomic_write_json(prov_path, doc)
        wrote = True

    return {
        **doc,
        "path": str(prov_path),
        "wrote": wrote,
        "counts": _counts(merged_rows),
        "errors": errors,
    }


def _comparable(doc: dict) -> dict:
    """比较两份 provenance 是否"内容相同" —— 排除 ``captured_at``(顶层的、每次
    调用都会变的"这次抢救是什么时候跑的",不是内容的一部分;逐行的
    ``captured_at`` 同理排除)。"""
    out = {k: v for k, v in doc.items() if k != "captured_at"}
    out["files"] = [
        {k: v for k, v in row.items() if k != "captured_at"} for row in (doc.get("files") or ())
    ]
    return out


def _all_report_run_ids(scan_reports_root: Path) -> list[str]:
    if not scan_reports_root.is_dir():
        return []
    return sorted(
        child.name
        for child in scan_reports_root.iterdir()
        if child.is_dir() and child.name != LEDGER_DIRNAME and (child / "manifest.json").is_file()
    )


def salvage_all(*, ledger_root: Path | None = None, sessions_root: Path | None = None) -> dict:
    """`--all`:逐个已发布 report_run_id 各用**自己的**取证窗口抢救(controller
    ruling 4)——当前时间只出现在每条记录各自的 ``captured_at`` 里,从不作为
    某个统一的"抢救窗口"喂给多个 run。任一 run 抛出未处理异常都不阻断其它 run
    (controller ruling 2 的 run 级镜像:一场出错不能让整批消失)。"""
    scan_reports_root = ws.reports_root() / "scan"
    results: dict[str, dict] = {}
    run_errors: dict[str, str] = {}
    for report_run_id in _all_report_run_ids(scan_reports_root):
        run_dir = scan_reports_root / report_run_id
        try:
            results[report_run_id] = salvage_run(
                run_dir, ledger_root=ledger_root, sessions_root=sessions_root
            )
        except Exception as exc:  # noqa: BLE001 - 一个 run 坏掉不能让整批消失
            run_errors[report_run_id] = f"{type(exc).__name__}: {exc}"
    return {"runs": results, "run_errors": run_errors}


# --------------------------------------------------------------- CLI


def _resolve_run_arg(value: str) -> Path:
    p = Path(value)
    return p if p.exists() else (ws.reports_root() / "scan" / value)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="逐文件抢救 E6 决策/市场研判/transcript(零 LLM)")
    group = ap.add_mutually_exclusive_group(required=True)
    group.add_argument("run", nargs="?", help="发布目录或 report_run_id")
    group.add_argument("--all", action="store_true", help="逐个已发布 run,各用自己的取证窗口")
    args = ap.parse_args(argv)

    if args.all:
        result = salvage_all()
        print(json.dumps(result, ensure_ascii=False, sort_keys=True))
        # 非零退出 = 存在未处理错误;正常 ABSENT/UNKNOWN 不是错误(controller ruling 4)。
        return 1 if result["run_errors"] else 0

    if not args.run:
        ap.error("必须提供 run,或使用 --all")
    run_dir = _resolve_run_arg(args.run)
    if not run_dir.is_dir():
        print(json.dumps({"ok": False, "error": f"run 目录不存在:{run_dir}"}, ensure_ascii=False))
        return 1
    try:
        result = salvage_run(run_dir)
    except Exception as exc:  # noqa: BLE001 - CLI 边界:未处理错误才算失败
        print(json.dumps({"ok": False, "error": f"{type(exc).__name__}: {exc}"}, ensure_ascii=False))
        return 1
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 1 if result["errors"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
