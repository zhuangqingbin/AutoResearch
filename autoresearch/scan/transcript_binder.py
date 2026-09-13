#!/usr/bin/env python3
"""scan-market 期望合并 + 归属分配（scene-reconstruction Task 3）。

design: `docs/superpowers/specs/2026-09-12-scene-reconstruction-transcript-
binding-design.md` §4（身份/期望/session与路径定位/区段/状态与不变量）。

本模块只做两件事，且只做这两件事（spec §9："scan/transcript_binder.py 负责
scan 期望和绑定编排"）：

1. :func:`agent_expectations` —— 这一趟 run **该有**哪些 invocation（按角色、
   subject、attempt 合并 AGENT 事件 + TASK 事件 + 产物回退，spec §4.2）。
2. :func:`assign` —— 把已定位好的候选 transcript 段分配给这些期望（spec
   §4.3–§4.5：session/路径定位由 :func:`build_claude_candidates` /
   :func:`build_codex_candidates` 完成，纯归属决策在 :func:`assign` 里）。

不在这里：写 `_transcript_bindings.json`、调用 `capsule.bind_transcript` 落盘、
CLI、`post_run` 接线 —— 全部 Task 4。不建离线索引/抢救 —— Task 8/9。
"""
from __future__ import annotations

import argparse
import contextlib
import json
import os
import shutil
import sys
import tempfile
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from autoresearch.common import workspace as ws
from autoresearch.contracts import artifacts
from autoresearch.contracts.profiles import profile_factory
from autoresearch.trace import capsule as capsule_mod
from autoresearch.trace.atomic import atomic_write_json, canonical_json, sha256_bytes
from autoresearch.trace.capsule_models import RunHandle
from autoresearch.trace.transcripts.base import (
    CURRENT_TRANSCRIPT_SCHEMA_VERSION,
    RunIdentity,
    TranscriptRef,
    TranscriptStats,
)
from autoresearch.trace.transcripts.claude import ClaudeTranscriptAdapter
from autoresearch.trace.transcripts.codex import (
    CodexTranscriptAdapter,
    discover_rollout_candidates,
    ordinal_window_for_timestamps,
)
from autoresearch.trace.transcripts.snapshot import (
    TranscriptSnapshot,
    capture_snapshot,
    snapshot_from_archive_bytes,
)

# ---------------------------------------------------------------- vocabulary

#: How an expectation row came to exist (spec §4.2). Two event sources merge
#: additively (``"agent_events+task_events"``); a role with neither kind of
#: event falls back to a run's own products, which is a known *lower bound*
#: -- it can prove a result exists, never that no failed attempt preceded it.
EXPECTATION_SOURCES: tuple[str, ...] = (
    "agent_events",
    "task_events",
    "agent_events+task_events",
    "products",
)

#: Spec §4.2: "产物只能证明某项结果存在,不能证明没有失败的前置 attempt;这时
#: denominator_quality=lower_bound"。Event-derived rows (either kind) are the
#: full, known-complete count for that invocation; product-derived rows are
#: not.
DENOMINATOR_QUALITIES: tuple[str, ...] = ("full", "lower_bound")

#: Whether this expectation's own start/terminal facts are known at all
#: (independent of whether a *transcript* for it has been found -- that is
#: `binding_status`/`segment_quality`, decided later by :func:`assign`).
#: "known" = a dispatch/claim + terminal fact exists (from either event
#: source); "unknown" = derived purely from a product's existence, spec
#: §4.2's explicit "但 terminal=null、boundary_quality=unknown,不伪造完成事件".
BOUNDARY_QUALITIES: tuple[str, ...] = ("known", "unknown")

#: Role -> the `contracts.artifacts` registry entry naming its product
#: selector (spec §4.1's verbatim table). Reused, never re-hand-written:
#: resolving `*` against a subject is the only extra step this module adds.
_PRODUCT_ARTIFACT_BY_ROLE: dict[str, str] = {
    "l4-card": "l4_cards",
    "l4-intel": "l4_intel",
    "sector-brief": "sector_briefs",
    "l3-rank": "l3_judged_raw",
    "l3-repair": "l3_repair_patch",
    "strategist": "market_view",
}

#: `contracts/stages.py`'s ROLE_STAGES has no product entry for l4-ensemble --
#: it merges via agent/task events only, same as any other role AGENT events
#: can cover; it simply has no product-fallback source.


def _expected_product_path(role: str, subject: str | None) -> str | None:
    """Resolve this role's registered product selector against *subject*.

    Never a hand-duplicated path: the literal string comes from
    `contracts.artifacts.by_name`, matching spec §4.1's table by
    construction (change the registry, this follows automatically). A
    wildcarded selector with no subject to substitute cannot be resolved --
    returns ``None`` rather than guessing.
    """
    art_name = _PRODUCT_ARTIFACT_BY_ROLE.get(role)
    if art_name is None:
        return None
    art = artifacts.by_name(art_name)
    if "*" not in art.path:
        return art.path
    if subject is None:
        return None
    return art.path.replace("*", subject)


def _iter_product_subjects(handle: RunHandle, role: str) -> list[tuple[str | None, str]]:
    """Every existing product file for *role*, as ``(subject, relative_path)``.

    Pure filesystem discovery of what the run *already produced* -- this is
    the "无事件的历史角色... 由 run 自有产物推导" leg (spec §4.2), never an
    inference about what *should* exist. A singleton artifact (no ``*``)
    yields at most one row; a wildcarded one yields one row per matched file,
    with ``subject`` recovered by stripping the selector's own prefix/suffix
    (never by re-parsing file content).
    """
    art_name = _PRODUCT_ARTIFACT_BY_ROLE.get(role)
    if art_name is None:
        return []
    art = artifacts.by_name(art_name)
    if art.root != "staging":
        return []
    if "*" not in art.path:
        target = handle.staging / art.path
        return [(None, art.path)] if target.is_file() else []
    _dir_part, _, name_pattern = art.path.rpartition("/")
    prefix, _, suffix = name_pattern.partition("*")
    results: list[tuple[str, str]] = []
    for match in sorted(handle.staging.glob(art.path)):
        if not match.is_file():
            continue
        subject = match.name
        if suffix and subject.endswith(suffix):
            subject = subject[: -len(suffix)]
        if prefix and subject.startswith(prefix):
            subject = subject[len(prefix) :]
        results.append((subject, match.relative_to(handle.staging).as_posix()))
    return results


def _subject_key_for(subject: str | None) -> str | None:
    """The same ASCII key `capsule.record_agent_boundary` would derive.

    ASCII subjects (stock codes) are their own key -- no hashing. A non-ASCII
    display name (industry names) is hashed via `capsule.subject_key`, the
    single existing implementation (never a second one here).
    """
    if subject is None:
        return None
    if subject.isascii():
        return subject
    return capsule_mod.subject_key(subject)


# ------------------------------------------------------------- expectations


def agent_expectations(handle: RunHandle) -> dict[str, dict]:
    """This run's known expectation set: one row per invocation.

    Merge order (spec §4.2, "同一调用的强事实优先" -- the stronger fact wins,
    never suppresses another role's fallback):

    1. AGENT_* events (`capsule._expectations_from_agent_events`) -- any
       role, whenever the harness actually dispatched a sub-agent (Claude
       only; Codex never emits these, AGENTS.md §3).
    2. TASK_* events (`capsule._task_expectations`) -- l4-card only, emitted
       by `l4_tasks.py`'s own preflight/mark_success/mark_failure
       transitions under *either* engine. Merged into an AGENT-derived row
       for the same (role, subject, attempt) when one exists (union of
       facts, invocation_id stays the AGENT one); otherwise becomes its own
       row keyed by a canonical ``f"{role}-{subject}-{attempt}"`` id -- the
       one place this module invents an id, and only because the task
       book's own event ids (``l4-preflight-<code>-attempt-<n>`` etc.) never
       match the AGENT dispatch convention (``l4-card-<code>-<n>``) by
       construction (two independently-named event families for one
       research attempt); flagged for the reviewer in the task report.
    3. Product-fallback -- only for the 6 roles spec §4.1 gives a product
       selector for, only for subjects *not already* covered by (1)/(2),
       and gated by `RunProfile.role_expected` (never invented for a mode
       that structurally never reaches that role, e.g. SENTINEL_EMPTY's L4
       leg). `denominator_quality="lower_bound"`, `terminal=None`,
       `boundary_quality="unknown"` -- spec §4.2's explicit "不伪造完成事件".

    A candidate reached by *none* of these never appears here at all -- that
    is `assign`'s ``unexpected`` bucket, spec §4.2's "不能借此提高 expected
    覆盖率".
    """
    agent_rows = capsule_mod._expectations_from_agent_events(handle)
    task_rows = capsule_mod._task_expectations(handle)
    # Deliberately permissive on `business_status`/`last_stage` (both left at
    # `RunProfile`'s own defaults, "SUCCEEDED" and `None`) -- Finding 3,
    # 2026-09-13 fix round 1. Unlike `finalize()`'s own `profile_factory(...)`
    # call in capsule.py, which populates both from the run's actual
    # terminal state, this loop only ever consults `profile.role_expected`,
    # and `role_expected`'s SENTINEL_EMPTY mode-skip check -- the one thing
    # that matters here -- returns before ever reaching `stage_reached()`
    # (`contracts/profiles.py`: `if vocab.skips_l4(self.mode) and role in
    # SENTINEL_SKIPPED_ROLES: return False`, checked first). A permissive
    # `stage_reached()` therefore changes nothing this loop decides on its
    # own: the real, load-bearing gate for "did this role's stage actually
    # run" is `_iter_product_subjects`'s own file-existence check just below
    # -- a stage genuinely never reached has no product file regardless of
    # what this profile claims about "reached". Computing a real mid-run
    # `last_stage` would mean reading checkpoints from a run still in
    # flight, and risks a false "not yet reached" for a stage that
    # completes moments later -- a worse failure mode than the permissive
    # default, which never manufactures a false exclusion.
    profile = profile_factory(handle.contract.run_kind)(
        mode=capsule_mod.resolve_run_mode(handle)
    )

    task_by_key: dict[tuple, list[dict]] = {}
    for trow in task_rows.values():
        task_by_key.setdefault((trow["role"], trow["subject"], trow["attempt"]), []).append(
            trow
        )

    merged: dict[str, dict] = {}
    consumed_task_ids: set[str] = set()
    handled_keys: set[tuple] = set()

    for inv_id, arow in agent_rows.items():
        subj_key = arow.get("subject_key")
        matches = task_by_key.get((arow["role"], subj_key, arow["attempt"]), [])
        row = {
            "invocation_id": inv_id,
            "role": arow["role"],
            "subject": arow.get("subject"),
            "subject_key": subj_key,
            "attempt": arow.get("attempt"),
            "dispatched": bool(arow.get("dispatched")),
            "dispatched_at": arow.get("dispatched_at"),
            "terminal": arow.get("terminal"),
            "terminal_at": arow.get("terminal_at"),
            "session_ref": None,
            "boundary_quality": "known",
            "denominator_quality": "full",
        }
        if matches:
            row["source"] = "agent_events+task_events"
            row["task_invocation_ids"] = tuple(
                sorted(m["invocation_id"] for m in matches)
            )
            if row["terminal"] is None:
                for m in matches:
                    if m.get("terminal"):
                        row["terminal"] = m["terminal"]
                        row["terminal_at"] = m.get("terminal_at")
                        break
            if not row["dispatched"]:
                row["dispatched"] = any(m.get("dispatched") for m in matches)
            for m in matches:
                if m.get("session_ref"):
                    row["session_ref"] = m["session_ref"]
                consumed_task_ids.add(m["invocation_id"])
        else:
            row["source"] = "agent_events"
            row["task_invocation_ids"] = ()
        row["expected_product"] = _expected_product_path(row["role"], subj_key or row["subject"])
        merged[inv_id] = row
        handled_keys.add((row["role"], subj_key, row["attempt"]))

    for trow in task_rows.values():
        key = (trow["role"], trow["subject"], trow["attempt"])
        if trow["invocation_id"] in consumed_task_ids or key in handled_keys:
            continue
        canonical_id = f"{trow['role']}-{trow['subject']}-{trow['attempt']}"
        if canonical_id in merged:
            existing = merged[canonical_id]
            existing["dispatched"] = existing.get("dispatched") or bool(trow.get("dispatched"))
            if trow.get("terminal"):
                existing["terminal"] = trow["terminal"]
                existing["terminal_at"] = trow.get("terminal_at")
            existing["task_invocation_ids"] = tuple(
                sorted(set(existing.get("task_invocation_ids", ())) | {trow["invocation_id"]})
            )
            if trow.get("session_ref") and not existing.get("session_ref"):
                existing["session_ref"] = trow["session_ref"]
            continue
        merged[canonical_id] = {
            "invocation_id": canonical_id,
            "role": trow["role"],
            "subject": trow["subject"],
            "subject_key": trow["subject"],
            "attempt": trow["attempt"],
            "source": "task_events",
            "dispatched": bool(trow.get("dispatched")),
            "dispatched_at": trow.get("claimed_at"),
            "terminal": trow.get("terminal"),
            "terminal_at": trow.get("terminal_at"),
            "session_ref": trow.get("session_ref"),
            "boundary_quality": "known",
            "denominator_quality": "full",
            "task_invocation_ids": (trow["invocation_id"],),
            "expected_product": _expected_product_path(trow["role"], trow["subject"]),
        }
        handled_keys.add(key)

    for role in _PRODUCT_ARTIFACT_BY_ROLE:
        if not profile.role_expected(role):
            continue
        for subject, rel_path in _iter_product_subjects(handle, role):
            subj_key = _subject_key_for(subject)
            key = (role, subj_key, 1)
            if key in handled_keys:
                continue
            canonical_id = f"{role}-{subj_key or 'market'}-1"
            if canonical_id in merged:
                continue
            merged[canonical_id] = {
                "invocation_id": canonical_id,
                "role": role,
                "subject": subject,
                "subject_key": subj_key,
                "attempt": 1,
                "source": "products",
                "dispatched": None,
                "dispatched_at": None,
                "terminal": None,
                "terminal_at": None,
                "session_ref": None,
                "boundary_quality": "unknown",
                "denominator_quality": "lower_bound",
                "task_invocation_ids": (),
                "expected_product": rel_path,
            }
            handled_keys.add(key)

    return merged


# --------------------------------------------------------------- candidates


@dataclass(frozen=True)
class TranscriptCandidate:
    """One located, already-snapshotted transcript segment competing for
    attribution in :func:`assign`.

    For Claude, one candidate == one whole subagent (or main) file -- Claude
    transcripts are already role-segmented by construction (each dispatch
    gets its own harness file), so no sub-file segmenting is attempted here
    (Task 3 ruling: Claude's own `locate()`/`stats()` are reused unchanged).
    For Codex, one candidate is one snapshot of a rollout file: an unwindowed
    whole-file candidate (``segment_quality="unknown"``, the only option when
    no per-invocation boundary is knowable -- brief bullet 8's intel case)
    plus, when a caller supplied a known wall-clock window (today: l4-card
    via task events), one additional narrower windowed candidate per such
    invocation.
    """

    engine: str
    path: Path
    ref: TranscriptRef
    stats: TranscriptStats
    segment_quality: str
    session_ref: str | None = None


def build_claude_candidates(
    run_identity: RunIdentity,
    *,
    adapter: ClaudeTranscriptAdapter | None = None,
) -> tuple[TranscriptCandidate, ...]:
    """Locate + snapshot every Claude transcript this run's session owns.

    Reuses `ClaudeTranscriptAdapter.locate` unchanged (spec §4.3: session_ref
    is the priority key, and that adapter already filters to the *one*
    matching session directory -- two ordinary parallel Claude sessions in
    the same repo never produce more than one session's worth of candidates,
    satisfying B09 by construction rather than by a second filter here). A
    ref that fails to read (`TranscriptUnreadable`, a missing file between
    locate and stats) is skipped, not fabricated into a degraded candidate --
    `assign` sees one fewer candidate, never a fake one.
    """
    resolved_adapter = adapter or ClaudeTranscriptAdapter()
    refs = resolved_adapter.locate(run_identity)
    candidates: list[TranscriptCandidate] = []
    for ref in refs:
        if ref.status != "PRESENT" or ref.path is None:
            continue
        try:
            stats = resolved_adapter.stats(ref)
        except Exception:  # noqa: BLE001 - an unreadable candidate is simply absent
            continue
        candidates.append(
            TranscriptCandidate(
                engine="claude",
                path=ref.path,
                ref=ref,
                stats=stats,
                segment_quality="complete",
                session_ref=ref.session_ref,
            )
        )
    return tuple(candidates)


def build_codex_candidates(
    run_identity: RunIdentity,
    expectations: Mapping[str, Mapping[str, object]],
    *,
    adapter: CodexTranscriptAdapter | None = None,
    sessions_root: Path | str | None = None,
    lookback_days: int = 10,
    now: datetime | None = None,
) -> tuple[TranscriptCandidate, ...]:
    """Discover + snapshot every rollout this run's session could own.

    One whole-file, ``segment_quality="unknown"`` candidate per discovered
    rollout (spec §4.3's location narrows to a *file*; nothing here claims a
    boundary within it) plus, for every l4-card expectation carrying a known
    dispatch/terminal window (task events -- the only source of a real
    per-attempt window under Codex, AGENTS.md §3: Codex never emits AGENT_*
    events), one additional windowed candidate via
    `codex.ordinal_window_for_timestamps` -- purely additive, the whole-file
    candidate is never removed (brief bullet 7: "区段外的异步返回只有 call_id
    能归属才补入;补入后保留边界扩展证据" -- keeping the whole-file candidate
    around is how a call_id-correlated return outside the narrow window can
    still be found).

    Finding 6 (2026-09-13 fix round 1): "complete"/"partial" is supposed to
    mean the segment provably belongs to *that* invocation alone. Two
    purpose-built windows sharing one rollout are cross-checked pairwise for
    ordinal overlap before either candidate is built; an overlapping pair is
    downgraded to ``segment_quality="interleaved"`` (spec §4.4's own term
    for exactly this -- "交错区段...只有可唯一归属的才进入本票确定统计") rather
    than letting both independently claim exclusivity `ordinal_window_for_timestamps`
    (which only ever sees one invocation's own window at a time) cannot see.
    """
    resolved_adapter = adapter or CodexTranscriptAdapter()
    search = discover_rollout_candidates(
        run_identity,
        sessions_root=sessions_root,
        lookback_days=lookback_days,
        now=now,
    )
    candidates: list[TranscriptCandidate] = []
    for path in search.candidates:
        try:
            snapshot = capture_snapshot(path, engine="codex")
        except (FileNotFoundError, ValueError):
            continue
        if snapshot.source_changed:
            continue
        whole_ref = TranscriptRef(
            engine="codex", path=path, role="unbound", session_ref=run_identity.session_ref
        )
        try:
            whole_stats = resolved_adapter.stats_from_rows(snapshot.rows, whole_ref)
        except Exception:  # noqa: BLE001 - an unreadable rollout is simply absent
            continue
        candidates.append(
            TranscriptCandidate(
                engine="codex",
                path=path,
                ref=whole_ref,
                stats=whole_stats,
                segment_quality="unknown",
                session_ref=run_identity.session_ref,
            )
        )

        # Precompute every l4-card invocation's own window over *this*
        # snapshot before building any windowed candidate -- overlap can
        # only be judged once every window for this shared source is known.
        windows: dict[str, tuple[int | None, int | None, str]] = {}
        for inv_id, exp in expectations.items():
            if exp.get("role") != "l4-card":
                continue
            start_ts = exp.get("dispatched_at")
            end_ts = exp.get("terminal_at")
            if not start_ts and not end_ts:
                continue
            start_ord, end_ord, quality = ordinal_window_for_timestamps(
                snapshot.rows, start_ts=start_ts, end_ts=end_ts
            )
            if quality == "unknown":
                continue
            windows[inv_id] = (start_ord, end_ord, quality)

        overlapping = _overlapping_invocations(windows)

        for inv_id, (start_ord, end_ord, quality) in windows.items():
            exp = expectations[inv_id]
            effective_quality = "interleaved" if inv_id in overlapping else quality
            windowed_ref = TranscriptRef(
                engine="codex",
                path=path,
                role="l4-card",
                subject=exp.get("subject_key") or exp.get("subject"),
                invocation_id=inv_id,
                session_ref=run_identity.session_ref,
                start_ordinal=start_ord,
                end_ordinal=end_ord,
            )
            try:
                windowed_stats = resolved_adapter.stats_from_rows(
                    snapshot.rows, windowed_ref
                )
            except Exception:  # noqa: BLE001 - a bad window is simply not offered
                continue
            candidates.append(
                TranscriptCandidate(
                    engine="codex",
                    path=path,
                    ref=windowed_ref,
                    stats=windowed_stats,
                    segment_quality=effective_quality,
                    session_ref=run_identity.session_ref,
                )
            )
    return tuple(candidates)


def _overlapping_invocations(
    windows: Mapping[str, tuple[int | None, int | None, str]],
) -> set[str]:
    """Which invocation_ids' own ordinal windows overlap at least one other
    invocation's, out of a set of windows over the *same* shared source.

    An unbounded edge (``None``, from a partial window whose attempt has not
    yet terminated) is treated conservatively as extending to cover
    anything on that side -- never assumed non-overlapping just because one
    edge is unknown, matching `_within`'s own conservative-on-``None``
    convention elsewhere in this module.
    """
    ids = list(windows)
    overlapping: set[str] = set()
    for i, inv_a in enumerate(ids):
        a_start, a_end, _ = windows[inv_a]
        for inv_b in ids[i + 1 :]:
            b_start, b_end, _ = windows[inv_b]
            if a_end is not None and b_start is not None and a_end < b_start:
                continue
            if b_end is not None and a_start is not None and b_end < a_start:
                continue
            overlapping.add(inv_a)
            overlapping.add(inv_b)
    return overlapping


# ------------------------------------------------------------- path safety


def _staging_root(workspace: Path) -> Path | None:
    """The one `staging/<date>/` directory under *workspace*, or ``None``.

    `RunIdentity` (unlike `RunHandle`) carries no `analysis_date`, so this
    walks the one level `handle.staging` would otherwise name directly. A
    run always has exactly one date subdirectory; zero or more than one is
    an unresolvable ownership root -- callers must treat that as "cannot
    verify", never guess which one.
    """
    parent = workspace / "staging"
    if not parent.is_dir():
        return None
    dated = [item for item in sorted(parent.iterdir()) if item.is_dir()]
    return dated[0] if len(dated) == 1 else None


def _other_run_id_in_path(path: Path, *, this_run_id: str) -> str | None:
    """A different, validly-shaped run_id appearing anywhere in *path*'s
    components, or ``None``. Used only to make a rejection reason *specific*
    ("this write belongs to run X") rather than a bare "escaped somewhere" --
    never used to accept or attribute anything; a hit here always leads to a
    rejection, one reason string richer than the generic one.
    """
    for part in path.parts:
        if part == this_run_id:
            continue
        try:
            ws.validate_run_id(part)
        except ValueError:
            continue
        return part
    return None


def _normalize_operation_path(
    raw: str | None,
    *,
    cwd: Path | None,
    staging: Path,
    run_id: str,
    engine: str,
) -> tuple[str | None, str | None]:
    """Resolve *raw* against the call's own cwd, verify it against *staging*,
    and return it already made relative to *staging* (posix) -- the single
    decision point for path safety in this task (spec §4.3; Controller
    ruling 1: the only path work this task performs at all).

    2026-09-13 fix round 1 (Findings 1+2): this function used to check
    containment against the wider run *workspace* and return an absolute
    path, while its one caller (`assign`) independently re-checked
    containment against the narrower *staging* directory via a second
    `.relative_to()` call. Two decision points for one question -- and the
    workspace-level one was provably dead: *staging* is always a
    subdirectory of *workspace*, so nothing could ever pass the caller's
    staging check while failing this function's workspace check, and the
    reviewer confirmed removing the internal check failed no test. Staging
    is now the *only* boundary checked, here, and the caller trusts this
    return value completely -- no second containment check anywhere else.

    The *specific* rejection reason (previously computed and discarded by
    the caller) is now the return value itself, not swallowed into a generic
    "no evidence" message downstream: cross-engine, cross-run (naming the
    other run), and generic directory escape are three distinguishable
    strings, checked in that order so each is independently reachable (a
    path that is both cross-engine *and* not cross-run reports "crosses
    into engine", never falls through to the generic message).
    """
    if not raw:
        return None, "operation carries no path"
    candidate = Path(raw)
    base = candidate if candidate.is_absolute() else (cwd or Path.cwd()) / candidate
    try:
        resolved = base.resolve(strict=False)
    except (OSError, RuntimeError) as exc:  # pragma: no cover - platform-dependent
        return None, f"path could not be resolved: {exc}"
    try:
        staging_resolved = staging.resolve(strict=False)
    except (OSError, RuntimeError) as exc:  # pragma: no cover - platform-dependent
        return None, f"staging root could not be resolved: {exc}"
    if not resolved.is_relative_to(staging_resolved):
        for other in ws.ENGINES:
            if other == engine:
                continue
            if f"context_{other}" in resolved.parts or f"reports_{other}" in resolved.parts:
                return None, f"path crosses into engine {other!r}: {resolved}"
        other_run = _other_run_id_in_path(resolved, this_run_id=run_id)
        if other_run is not None:
            return None, (
                f"path belongs to a different run {other_run!r}, not this "
                f"run's staging directory: {resolved}"
            )
        return None, (
            f"path escapes this run's staging directory (directory "
            f"traversal): {resolved}"
        )
    return resolved.relative_to(staging_resolved).as_posix(), None


# --------------------------------------------------------------- attribution


def _parse_iso(value: object) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _within(inner: tuple, outer: tuple) -> bool:
    """True when *inner* (a candidate's own [start, end]) sits inside *outer*
    (an expectation's authoritative [dispatched_at, terminal_at]).

    Never a tie-break by ordering: this only ever checks one specific,
    already-identified pair's timestamps against each other (Controller
    ruling 2) -- it is never used to sort a pool of candidates and hand them
    out by position. Requires at least one real timestamp on the inner side;
    two unknown values are never treated as "matches everything".
    """
    istart, iend = inner
    ostart, oend = outer
    if istart is None and iend is None:
        return False
    if ostart is not None and istart is not None and istart < ostart:
        return False
    return not (oend is not None and iend is not None and iend > oend)


def _base_row(exp: Mapping[str, object]) -> dict:
    return {
        "invocation_id": exp["invocation_id"],
        "role": exp.get("role"),
        "subject": exp.get("subject"),
        "subject_key": exp.get("subject_key"),
        "attempt": exp.get("attempt"),
        "source": exp.get("source"),
        "denominator_quality": exp.get("denominator_quality"),
        "expected_product": exp.get("expected_product"),
    }


_STATUS_BUCKET = {
    "BOUND": "bound",
    "UNVERIFIED_BY_PRODUCT": "unverified",
    "AMBIGUOUS": "ambiguous",
    "GONE": "gone",
    "ERROR": "errors",
}

#: Distinguishes "`staging_root` not supplied at all" (every *active*-run
#: caller -- fall back to `ws.find_run_root`, the historical default
#: behavior) from "`staging_root=None` supplied explicitly" (offline
#: reconstruction's honest "no verifiable containment root exists" --
#: `_load_frozen_run` returns `None` when a historical contract carries no
#: `workspace_path`). Plain `None` cannot serve as that default itself: a
#: caller that *means* "I explicitly have no root" must never be silently
#: routed into the live `ws.find_run_root` fallback, which could coincidentally
#: resolve to an unrelated directory that happens to share this run_id.
_STAGING_ROOT_UNSET = object()


def assign(
    candidates: Sequence[TranscriptCandidate],
    expectations: Mapping[str, Mapping[str, object]],
    run_identity: RunIdentity,
    *,
    staging_root: Path | str | None = _STAGING_ROOT_UNSET,  # type: ignore[assignment]
) -> dict:
    """Attribute *candidates* to *expectations*; never guess, never shrink E.

    Two-tier matching, per expectation *family* (same role + subject_key,
    every attempt of it -- spec §4.1's identity tuple minus engine/run/session,
    which are already fixed by *run_identity*):

    - **Tier 1 (product evidence)**: a candidate carrying a verified
      ``WRITE_SUCCEEDED`` at the family's `expected_product` path.  A
      family with exactly one member and exactly one tier-1 hit is `BOUND`
      outright.  A family with more than one member (retries) disambiguates
      each tier-1 hit by comparing *that candidate's own* [started_at,
      ended_at] against *each member's own* authoritative
      [dispatched_at/claimed_at, terminal_at] window (never by position or
      by a global timestamp sort -- Controller ruling 2); a hit that falls
      inside exactly one member's window binds to that member, one row,
      one candidate.
    - **Tier 2 (window-only)**: for a member tier 1 left unresolved, a
      *still-unclaimed* candidate whose own window uniquely falls inside
      the member's window becomes `UNVERIFIED_BY_PRODUCT` -- call identity
      verified, product write unproven.
    - Neither tier resolves it -> `GONE`.
    - A tier that finds more than one candidate and cannot disambiguate ->
      `AMBIGUOUS`, every contending candidate path listed, no guess made.

    Conservation (spec §4.5, hard invariant): every key in *expectations*
    gets exactly one output row, ``coverage["accounted"] ==
    coverage["expected"]``, and the five status counts sum to ``expected``.
    A candidate whose engine/session does not match *run_identity* is
    dropped into ``unmatched`` before either tier runs, never silently
    merged into scoring. A candidate with verified write evidence at a path
    no expectation names goes to ``unexpected`` -- it is *never* added to
    ``expected``/``accounted`` (spec §4.2's explicit prohibition).

    ``staging_root`` (2026-09-12 scene-reconstruction Task 8): an explicit
    override for the staging directory path-normalization is checked
    against, bypassing ``ws.find_run_root(run_identity.run_id)`` entirely.
    Every *active*-run caller (``bind_run``/``safe_bind_run``, and every
    B01-B10/R01 test above) omits this keyword and gets the exact prior
    behavior -- the *active* workspace genuinely exists on disk and is the
    right root to ask. Offline reconstruction of a *frozen* run cannot rely
    on that: the run's original active workspace
    (`context_<engine>/scan_runs/<id>/`) may no longer exist by the time a
    frozen run is reconstructed months later (the whole reason Task 8
    exists), and `ws.find_run_root` would then silently return ``None`` --
    collapsing every tier-1 product check to "no staging root, nothing
    verifiable" for a reason that has nothing to do with the evidence
    itself. `transcript_binder.offline_index` instead supplies this run's
    own *original* workspace-derived staging path, reconstructed from the
    run's own recorded `run_contract.workspace_path` (Controller ruling 6:
    "离线根映射来自 contract/归档元信息" -- never the frozen `trace/staging/`
    mirror, and never a live-filesystem guess); `_normalize_operation_path`'s
    containment check works identically either way, since it only ever needs
    a `Path` to resolve against, not proof the directory currently exists.

    Passing ``staging_root=None`` *explicitly* (as opposed to omitting the
    keyword) means "verified: no containment root exists for this run" --
    every tier-1 product check then honestly resolves to "cannot verify",
    never a live-lookup fallback that could coincidentally match an
    unrelated directory sharing this run_id.
    """
    if staging_root is _STAGING_ROOT_UNSET:
        workspace = ws.find_run_root(run_identity.run_id)
        staging: Path | None = _staging_root(workspace) if workspace is not None else None
    elif staging_root is None:
        staging = None
    else:
        staging = Path(staging_root)

    accepted: list[TranscriptCandidate] = []
    unmatched: list[dict] = []
    for candidate in candidates:
        reasons: list[str] = []
        if candidate.engine != run_identity.engine:
            reasons.append(
                f"candidate engine {candidate.engine!r} != run engine "
                f"{run_identity.engine!r}"
            )
        if (
            run_identity.session_ref is not None
            and candidate.session_ref is not None
            and candidate.session_ref != run_identity.session_ref
        ):
            reasons.append("candidate session_ref does not match this run's session_ref")
        if reasons:
            unmatched.append({"path": str(candidate.path), "reasons": tuple(reasons)})
            continue
        accepted.append(candidate)

    # `candidate_products[i]` / `candidate_rejections[i]` are parallel to
    # `accepted[i]`. Fix round 1, Finding 2: `_normalize_operation_path` is
    # now the *only* place that decides containment (against `staging`
    # directly) -- there is no second `.relative_to()` check here anymore;
    # its return value is trusted completely. Fix round 1, Finding 1: the
    # *specific* reason a write was rejected (cross-engine / cross-run /
    # directory escape) is kept per candidate rather than discarded, so a
    # row that falls back to tier 2 or GONE can say what actually happened
    # to that candidate's own write attempt(s), not just "no evidence".
    candidate_products: list[dict[str, list]] = []
    candidate_rejections: list[list[str]] = []
    for candidate in accepted:
        hits: dict[str, list] = {}
        rejections: list[str] = []
        if staging is not None:
            for op in candidate.stats.operations:
                if op.kind != "WRITE_SUCCEEDED":
                    continue
                rel, reason = _normalize_operation_path(
                    op.path,
                    cwd=run_identity.cwd,
                    staging=staging,
                    run_id=run_identity.run_id,
                    engine=run_identity.engine,
                )
                if rel is None:
                    if reason is not None:
                        rejections.append(reason)
                    continue
                hits.setdefault(rel, []).append(op)
        candidate_products.append(hits)
        candidate_rejections.append(rejections)

    def candidate_window(candidate: TranscriptCandidate) -> tuple:
        return _parse_iso(candidate.stats.started_at), _parse_iso(candidate.stats.ended_at)

    def expectation_window(exp: Mapping[str, object]) -> tuple:
        start = exp.get("dispatched_at")
        end = exp.get("terminal_at")
        return _parse_iso(start), _parse_iso(end)

    families: dict[tuple, list[str]] = {}
    for inv_id, exp in expectations.items():
        key = (exp.get("role"), exp.get("subject_key"))
        families.setdefault(key, []).append(inv_id)

    rows: dict[str, dict] = {}
    claimed_idx: set[int] = set()

    for key in sorted(families, key=lambda item: (str(item[0]), str(item[1]))):
        members = families[key]
        member_exps = {inv_id: expectations[inv_id] for inv_id in members}
        expected_path = next(
            (exp.get("expected_product") for exp in member_exps.values()), None
        )

        tier1_hits = (
            [idx for idx, hits in enumerate(candidate_products) if expected_path in hits]
            if expected_path is not None
            else []
        )

        bound_for: dict[str, int] = {}
        ambiguous_for: dict[str, list[int]] = {}

        # Pass 1: a candidate *purpose-built* for one specific member's own
        # invocation_id (a Codex windowed candidate `build_codex_candidates`
        # constructed from that exact invocation's own authoritative
        # dispatch/terminal timestamps, spec §4.4) is not a guess to be
        # weighed against other evidence -- its builder already did the
        # window comparison once; re-litigating it here by *also* comparing
        # a whole-file candidate's much wider window would only add noise.
        # A generic candidate never carries a `ref.invocation_id` that
        # equals a real expectation key (Claude's is always ``None``;
        # Codex's whole-file candidate's is ``"unbound"``), so this split
        # never accidentally reclassifies genuine evidence.
        purpose_built: dict[str, list[int]] = {}
        generic_hits: list[int] = []
        for idx in tier1_hits:
            owner = accepted[idx].ref.invocation_id
            if owner in member_exps:
                purpose_built.setdefault(owner, []).append(idx)
            else:
                generic_hits.append(idx)

        unresolved: list[str] = []
        for inv_id in members:
            own = purpose_built.get(inv_id, [])
            if len(own) == 1:
                bound_for[inv_id] = own[0]
            elif len(own) > 1:
                ambiguous_for[inv_id] = own
            else:
                unresolved.append(inv_id)

        # Pass 2: whatever generic (non-purpose-built) evidence is left,
        # disambiguated by window -- exactly the retry case B04 exercises
        # (Claude candidates never carry a purpose-built invocation_id, so
        # every family goes through this pass). Two-pass within the pass
        # itself for the same reason as before: a hit matching nobody's
        # window (e.g. a Codex whole-file candidate spanning every attempt)
        # must never contaminate a member some *other* hit cleanly resolves
        # -- a single fold that marks the first such member seen ambiguous
        # would let candidate order decide the outcome (Controller ruling 2).
        if len(unresolved) == 1 and len(members) == 1:
            only = unresolved[0]
            if len(generic_hits) == 1:
                bound_for[only] = generic_hits[0]
            elif len(generic_hits) > 1:
                ambiguous_for[only] = list(generic_hits)
        elif unresolved and generic_hits:
            exclusive: dict[str, list[int]] = {}
            noisy: dict[str, list[int]] = {}
            for idx in generic_hits:
                cand_win = candidate_window(accepted[idx])
                matches = [
                    inv_id
                    for inv_id in unresolved
                    if _within(cand_win, expectation_window(member_exps[inv_id]))
                ]
                if len(matches) == 1:
                    exclusive.setdefault(matches[0], []).append(idx)
                elif len(matches) > 1:
                    for inv_id in matches:
                        noisy.setdefault(inv_id, []).append(idx)
            for inv_id in unresolved:
                exclusive_hits = exclusive.get(inv_id, [])
                noisy_hits = noisy.get(inv_id, [])
                if exclusive_hits and len(exclusive_hits) == 1 and not noisy_hits:
                    bound_for[inv_id] = exclusive_hits[0]
                elif exclusive_hits or noisy_hits:
                    ambiguous_for[inv_id] = exclusive_hits + noisy_hits

        for inv_id in members:
            if inv_id in bound_for or inv_id in ambiguous_for:
                continue
            exp_win = expectation_window(member_exps[inv_id])
            if exp_win == (None, None):
                continue
            hits = [
                idx
                for idx, candidate in enumerate(accepted)
                if idx not in claimed_idx and _within(candidate_window(candidate), exp_win)
            ]
            if len(hits) == 1:
                bound_for[inv_id] = hits[0]
            elif len(hits) > 1:
                ambiguous_for[inv_id] = hits

        claimed_idx.update(bound_for.values())

        for inv_id in members:
            exp = member_exps[inv_id]
            row = _base_row(exp)
            if inv_id in bound_for:
                idx = bound_for[inv_id]
                candidate = accepted[idx]
                has_product = (
                    expected_path is not None and expected_path in candidate_products[idx]
                )
                row["binding_status"] = "BOUND" if has_product else "UNVERIFIED_BY_PRODUCT"
                row["segment_quality"] = candidate.segment_quality
                row["candidate_path"] = str(candidate.path)
                row["candidate_paths"] = (str(candidate.path),)
                # Task 4's own source reference (spec's "来源引用"): the winning
                # candidate's *own* ref fields, verbatim -- never re-derived. Two
                # Codex candidates (whole-file + windowed) can share the exact same
                # `candidate_path` (B05), so the path string alone cannot recover
                # which one actually won; `bind_run` needs these to call
                # `capsule.bind_transcript` with the identity `assign()` -- not a
                # second, independent lookup -- actually chose.
                row["candidate_engine"] = candidate.engine
                row["candidate_start_ordinal"] = candidate.ref.start_ordinal
                row["candidate_end_ordinal"] = candidate.ref.end_ordinal
                row["candidate_session_ref"] = candidate.session_ref
                row["search_count"] = (
                    sum(
                        1
                        for op in candidate.stats.operations
                        if op.kind.startswith("SEARCH_")
                    )
                    if candidate.segment_quality == "complete"
                    else None
                )
                if has_product:
                    row["reason"] = None
                else:
                    reason = (
                        "call identity verified by dispatch window; "
                        "no verified product write found in the bound segment"
                    )
                    # Finding 1 (2026-09-13 fix round 1): surface the
                    # *specific* reason(s) this exact, already-identified
                    # candidate's own rejected write(s) failed -- cross-run,
                    # cross-engine, directory escape -- rather than only the
                    # generic "no verified product write" message. Scoped to
                    # this one candidate (`idx`), never guessed onto another.
                    own_rejections = candidate_rejections[idx]
                    if own_rejections:
                        reason += "; rejected write path(s): " + "; ".join(own_rejections)
                    row["reason"] = reason
            elif inv_id in ambiguous_for:
                idxs = sorted(set(ambiguous_for[inv_id]))
                row["binding_status"] = "AMBIGUOUS"
                row["segment_quality"] = "unknown"
                row["candidate_path"] = None
                row["candidate_paths"] = tuple(str(accepted[i].path) for i in idxs)
                row["search_count"] = None
                row["reason"] = (
                    "multiple competing candidates and no product/window evidence "
                    "disambiguates them"
                )
            else:
                row["binding_status"] = "GONE"
                row["segment_quality"] = "unknown"
                row["candidate_path"] = None
                row["candidate_paths"] = ()
                row["search_count"] = None
                reason = "no candidate transcript evidences this invocation"
                # Finding 1: a GONE row can still name a *specific* rejection
                # when a candidate whose own window genuinely overlaps this
                # expectation's had a write rejected -- scoped by window so
                # an unrelated candidate elsewhere never gets blamed on this
                # invocation's behalf (no product-path signal survives to
                # scope by here, since nothing bound).
                exp_win = expectation_window(exp)
                if exp_win != (None, None):
                    relevant = [
                        reason_text
                        for cidx, candidate in enumerate(accepted)
                        if _within(candidate_window(candidate), exp_win)
                        for reason_text in candidate_rejections[cidx]
                    ]
                    if relevant:
                        reason += (
                            "; a window-matching candidate had rejected write "
                            "path(s): " + "; ".join(relevant)
                        )
                row["reason"] = reason
            rows[inv_id] = row

    known_products = {
        exp.get("expected_product") for exp in expectations.values() if exp.get("expected_product")
    }
    unexpected: list[dict] = []
    for idx, candidate in enumerate(accepted):
        for rel in sorted(set(candidate_products[idx]) - known_products):
            unexpected.append({"path": str(candidate.path), "product": rel})

    denom = (
        "lower_bound"
        if any(exp.get("denominator_quality") == "lower_bound" for exp in expectations.values())
        else "full"
    )
    counts = dict.fromkeys(("bound", "unverified", "ambiguous", "gone", "errors"), 0)
    for row in rows.values():
        counts[_STATUS_BUCKET[row["binding_status"]]] += 1

    coverage = {
        "expected": len(expectations),
        "accounted": len(rows),
        **counts,
        "unexpected": len(unexpected),
        "denominator_quality": denom,
    }
    return {
        "rows": rows,
        "unexpected": tuple(unexpected),
        "unmatched": tuple(unmatched),
        "coverage": coverage,
    }


# -------------------------------------------------------------- active wiring
#
# Task 4 (design §5.2 生产接线). Everything below turns Task 3's pure
# `agent_expectations`/`assign` into one active-run side effect: a set of
# authoritative `capsule.bind_transcript` calls, plus the one report
# `presence="always"` demands whenever `observe` runs
# (`contracts/artifacts.py`'s `transcript_bindings_report`, registered by
# Task 1 -- not re-registered here). Nothing here builds an offline index or
# a salvage path (Tasks 8/9); nothing here is called unless
# `post_run.publish_run_observation` calls `safe_bind_run` (wired in this
# same task).

#: The report's own two top-level facts, independent of any one row's
#: `binding_status` (Controller ruling 5: coverage / binding status / the
#: materialized carrier status `materialize_agent_index` computes later are
#: three separate facts -- this module only ever speaks to the first two).
REPORT_STATUSES: tuple[str, ...] = ("OK", "DISABLED", "ERROR")
TRANSCRIPT_BINDING_REPORT_SCHEMA_VERSION = 1

#: Single source for the report's on-disk name/root -- resolved from the
#: registry Task 1 already populated, never a second hand-written literal.
_BINDINGS_REPORT_ARTIFACT = artifacts.by_name("transcript_bindings_report")


def _report_path(scan_dir: Path | str) -> Path:
    return Path(scan_dir) / _BINDINGS_REPORT_ARTIFACT.path


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


def _empty_coverage(*, denominator_quality: str = "full") -> dict:
    return {
        "expected": 0,
        "accounted": 0,
        "bound": 0,
        "unverified": 0,
        "ambiguous": 0,
        "gone": 0,
        "errors": 0,
        "unexpected": 0,
        "denominator_quality": denominator_quality,
    }


def _base_report(
    *,
    run_id: str | None,
    engine: str | None,
    enabled: bool,
    status: str,
    reason: str | None,
) -> dict:
    """The report shape every path (OK / DISABLED / ERROR) shares.

    `rows`/`unexpected`/`unmatched` stay empty and `coverage` stays the
    all-zero shape unless the caller (only :func:`_bind_and_report`) fills
    them in from a real `assign()` result -- a disabled or whole-run-failure
    report never fabricates a row it never computed (spec §4.5: `accounted ==
    expected` holds trivially at 0 == 0, never "looks complete").
    """
    return {
        "schema_version": TRANSCRIPT_BINDING_REPORT_SCHEMA_VERSION,
        "run_id": run_id,
        "engine": engine,
        "generated_at": _now_iso(),
        "enabled": enabled,
        "status": status,
        "reason": reason,
        "rows": {},
        "unexpected": [],
        "unmatched": [],
        "coverage": _empty_coverage(),
    }


def _report_without_timestamp(report: Mapping[str, object]) -> dict:
    # Round-tripped through JSON so a tuple-valued field (e.g. a row's own
    # `candidate_paths`) compares equal to the list form the *on-disk* report
    # already carries -- never a real content difference, just JSON's own
    # tuple/list distinction.
    return json.loads(
        json.dumps({k: v for k, v in report.items() if k != "generated_at"}, sort_keys=True)
    )


def _write_report(scan_dir: Path | str, report: dict) -> dict:
    """Write the staging report, but only when it actually changed.

    Re-running with unchanged bindings must not even touch this file's bytes
    (ruling 6's "重跑相同绑定...不重复追加", generalized from `bindings.jsonl`
    to the report that summarizes it) -- this repo's own `_atomic_json`
    (post_run.py) already applies the identical "skip if unchanged" rule to
    every other observe-time artifact; a `generated_at` timestamp that
    changed on every call despite nothing else differing would make this the
    one artifact that silently broke that convention, and did (caught by
    `tests/scan/test_wave3_observation.py`'s byte-idempotence check on a
    second real `publish_run_observation` call).
    """
    path = _report_path(scan_dir)
    try:
        existing = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        existing = None
    if isinstance(existing, dict) and _report_without_timestamp(
        existing
    ) == _report_without_timestamp(report):
        return existing
    atomic_write_json(path, report)
    return report


def _tally_coverage(
    rows: Mapping[str, Mapping[str, object]],
    *,
    unexpected: int,
    denominator_quality: str,
) -> dict:
    """Recompute coverage from the *final* per-row statuses this report is
    about to publish -- never a copy of `assign()`'s own coverage, because a
    row `assign()` called BOUND can still end up ERROR here (a persistence
    conflict, §ruling 2) and the two counts would then disagree with the rows
    they are supposed to summarize."""
    counts = dict.fromkeys(("bound", "unverified", "ambiguous", "gone", "errors"), 0)
    for row in rows.values():
        counts[_STATUS_BUCKET[row["binding_status"]]] += 1
    return {
        "expected": len(rows),
        "accounted": len(rows),
        **counts,
        "unexpected": unexpected,
        "denominator_quality": denominator_quality,
    }


def _persist_binding(handle: RunHandle, inv_id: str, row: Mapping[str, object]) -> dict:
    """Turn one BOUND/UNVERIFIED_BY_PRODUCT attribution into an authoritative
    capsule binding, or an explicit `ERROR` row -- never an exception that
    reaches the caller (Controller ruling 2: a binding conflict, an
    unreadable source, or any other persistence failure is caught *for this
    invocation* and recorded with its cause; the batch's other invocations
    are a separate concern this function knows nothing about, by
    construction -- the loop in :func:`_bind_and_report` calls this once per
    row, so nothing here can abort another invocation's turn).

    Binds against the *exact* candidate `assign()` already chose (`path`,
    `start_ordinal`, `end_ordinal` read straight off the row's own
    `candidate_*` fields -- never re-derived, never re-scored here).
    """
    out = dict(row)
    try:
        capsule_mod.bind_transcript(
            handle.run_id,
            row["candidate_path"],
            role=row["role"],
            invocation_id=inv_id,
            subject=row.get("subject_key") or row.get("subject"),
            engine=row.get("candidate_engine") or handle.engine,
            start_ordinal=row.get("candidate_start_ordinal"),
            end_ordinal=row.get("candidate_end_ordinal"),
        )
    except Exception as exc:  # noqa: BLE001 - isolated per invocation, batch continues
        out["binding_status"] = "ERROR"
        out["reason"] = (
            f"transcript located ({row.get('candidate_path')}) but the binding could "
            f"not be persisted: {type(exc).__name__}: {exc}"
        )
    return out


def _bind_and_report(handle: RunHandle, scan_dir: Path | str) -> dict:
    """The real work: build this run's expectations, locate candidates, attribute
    them, persist every attributable one, and write the complete report.

    Raises on a *whole-run* failure only (an unreadable expectation set, an
    unsupported engine, or the report itself being unwritable) -- per
    invocation, `_persist_binding` never raises. Callers that must never fail
    (the production wiring in `post_run.py`) go through :func:`safe_bind_run`
    instead, which wraps this call and degrades rather than propagating.
    """
    run_identity = RunIdentity(
        run_id=handle.run_id, engine=handle.engine, session_ref=handle.contract.session_ref,
    )
    if run_identity.engine != ws.ENGINE:
        # `assign()` resolves this run's own workspace via
        # `ws.find_run_root(run_identity.run_id)`, which reads the *ambient*
        # `ws.ENGINE` -- if that ever disagreed with the run's own recorded
        # engine, `find_run_root` would silently return `None` and every
        # product-derived expectation would attribute zero evidence with no
        # error raised (review finding on Task 3: "safe by convention, not
        # enforced"). `handle.engine` is `require_active_run`'s own validated
        # `contract.engine`, already checked against `ws.ENGINE` inside
        # `capsule.load_run` -- so this can only fire if that invariant is
        # ever broken elsewhere, and it must fail loudly rather than
        # silently zero every product-derived row when it does.
        raise RuntimeError(
            "transcript_binder: run engine "
            f"{run_identity.engine!r} does not match ambient ws.ENGINE {ws.ENGINE!r}; "
            "refusing to attribute product evidence under a mismatched engine root"
        )
    expectations = agent_expectations(handle)
    if handle.engine == "claude":
        candidates: tuple[TranscriptCandidate, ...] = build_claude_candidates(run_identity)
    elif handle.engine == "codex":
        candidates = build_codex_candidates(run_identity, expectations)
    else:
        raise ValueError(f"unsupported engine for transcript binding: {handle.engine!r}")

    result = assign(candidates, expectations, run_identity)

    rows: dict[str, dict] = {}
    for inv_id, row in result["rows"].items():
        if row["binding_status"] in ("BOUND", "UNVERIFIED_BY_PRODUCT"):
            rows[inv_id] = _persist_binding(handle, inv_id, row)
        else:
            rows[inv_id] = dict(row)

    report = {
        "schema_version": TRANSCRIPT_BINDING_REPORT_SCHEMA_VERSION,
        "run_id": handle.run_id,
        "engine": handle.engine,
        "generated_at": _now_iso(),
        "enabled": True,
        "status": "OK",
        "reason": None,
        "rows": rows,
        "unexpected": list(result["unexpected"]),
        "unmatched": list(result["unmatched"]),
        "coverage": _tally_coverage(
            rows,
            unexpected=len(result["unexpected"]),
            denominator_quality=result["coverage"]["denominator_quality"],
        ),
    }
    return _write_report(scan_dir, report)


def bind_run(run_id: str, scan_dir: Path | str) -> dict:
    """Bind *run_id*'s transcripts against its own expectation set, persist every
    attributable one, and write the staging report -- the active CLI's and
    `safe_bind_run`'s shared entry point.

    Requires the run to still be ACTIVE (ruling 1: binding happens before
    `retain` mirrors staging and before the capsule freezes). Raises on a
    whole-run failure; use :func:`safe_bind_run` where binding must never
    break a publish.
    """
    handle = capsule_mod.require_active_run(run_id)
    return _bind_and_report(handle, scan_dir)


def _configured_bind_transcripts() -> bool:
    """`scan_config.jsonc`'s `retention.bind_transcripts` (default ``True``).

    Config-layer failure (bad file / whitelist violation) degrades to the
    documented default -- the same discipline as
    `relative_buy.configured_relative_buy()`: a broken config must not
    silently *turn off* evidence collection, so the safe fallback direction
    is "still try", with the failure itself printed to stderr.
    """
    from autoresearch.scan.user_config import load_user_config

    try:
        block = load_user_config().get("retention") or {}
    except Exception as exc:  # noqa: BLE001 - config failure must not silently disable binding
        print(
            f"[transcript_binder] scan_config 读取失败({exc!r})→ "
            "retention.bind_transcripts 用内建默认 True",
            file=sys.stderr,
        )
        return True
    return bool(block.get("bind_transcripts", True))


def _safe_write(scan_dir: Path | str, report: dict) -> dict | None:
    try:
        return _write_report(scan_dir, report)
    except Exception as exc:  # noqa: BLE001 - even the fallback write must never raise outward
        print(
            f"[transcript_binder] 报告落盘失败({type(exc).__name__}: {exc})→ 本次无法留痕",
            file=sys.stderr,
        )
        return None


def safe_bind_run(scan_dir: Path | str) -> dict | None:
    """Never raises. The only call site `post_run.publish_run_observation` uses.

    Three outcomes, each still writing the ``presence="always"`` staging
    report (design §9) so a consumer never has to guess why it is missing:

    - the switch is off, or there is no active run -> ``enabled=False``
      report with a reason; no existing evidence (`agents/bindings.jsonl`)
      is touched or cleared (ruling 4).
    - the switch is on, an active run exists, and binding completes ->
      the real report from :func:`bind_run` (individual rows may still be
      ``ERROR``/``AMBIGUOUS``/``GONE`` -- that is a legitimate, accounted
      outcome, not a failure of this function).
    - the switch is on, an active run exists, but something breaks before a
      report can be computed (an unreadable expectation set, an unsupported
      engine) -> ``enabled=True, status="ERROR"`` report, plus the existing
      evidence-degradation channel and stderr (ruling 3: a publish must
      never fail because evidence collection failed, and never silently
      claim completeness when it did not).
    """
    scan = Path(scan_dir)
    if not _configured_bind_transcripts():
        return _safe_write(
            scan,
            _base_report(
                run_id=None,
                engine=None,
                enabled=False,
                status="DISABLED",
                reason="retention.bind_transcripts=false in scan_config",
            ),
        )
    try:
        run_id = ws.active_run_id()
    except ValueError as exc:
        return _safe_write(
            scan,
            _base_report(
                run_id=None,
                engine=None,
                enabled=False,
                status="DISABLED",
                reason=f"invalid AUTORESEARCH_RUN_ID: {exc}",
            ),
        )
    if not run_id:
        return _safe_write(
            scan,
            _base_report(
                run_id=None,
                engine=None,
                enabled=False,
                status="DISABLED",
                reason="no active forensic run",
            ),
        )
    try:
        handle = capsule_mod.require_active_run(run_id)
    except Exception as exc:  # noqa: BLE001 - "not active" is a normal disabled state, not a failure
        return _safe_write(
            scan,
            _base_report(
                run_id=run_id,
                engine=None,
                enabled=False,
                status="DISABLED",
                reason=f"run is not an active forensic run: {type(exc).__name__}: {exc}",
            ),
        )
    try:
        return _bind_and_report(handle, scan)
    except Exception as exc:  # noqa: BLE001 - whole-run failure: degrade + stderr, never raise
        message = f"{type(exc).__name__}: {exc}"
        with contextlib.suppress(Exception):  # degradation bookkeeping must not itself raise here
            capsule_mod._degrade_evidence(handle, "capsule.transcript_binding", message)
        print(
            "[transcript_binder] 整场绑定失败"
            f"({message})→ 报告标 ERROR;证据降级不等于发布失败",
            file=sys.stderr,
        )
        return _safe_write(
            scan,
            _base_report(
                run_id=handle.run_id,
                engine=handle.engine,
                enabled=True,
                status="ERROR",
                reason=message,
            ),
        )


# -------------------------------------------------------------- offline index
#
# Task 8 (design §6.1 离线索引). A *frozen*, already-published run's capsule
# may show every invocation GONE even when real evidence still exists -- in
# the run's own retention-archived transcript (`trace/transcripts/*.jsonl.gz`,
# a pre-existing, pre-Task-2 mechanism -- `autoresearch.scan.retention.
# archive_transcripts`), in the current engine's `scan.salvage` ledger, or in
# the harness's own still-live session directory. This section rebuilds the
# same per-invocation table `agent_expectations`/`assign` already build for
# an *active* run, purely offline, writing only under the current engine's
# `_ledger/agents_index/` -- never into the frozen run directory itself
# (ruling 8; verified end to end by the H01 tests above and the real-run
# check in the task report).

#: Bumped whenever this *reconstruction algorithm* changes in a way that
#: could produce different attribution from the exact same source snapshots
#: -- distinct from `CURRENT_TRANSCRIPT_SCHEMA_VERSION` (the per-invocation
#: normalized-item shape, owned by Task 1/2). Feeds `revision_id` (ruling 4).
OFFLINE_INDEX_PARSER_VERSION = 1

#: The three input sources, in the exact priority spec §6.1 states ("run 自有
#: 已归档证据 → 本引擎 ledger 中已核验的抢救快照 → 对应 harness 尚在的源文件").
#: A lower-priority source is only ever consulted to fill in invocations a
#: higher-priority source left `GONE` -- never to override an already-found
#: `BOUND`/`UNVERIFIED_BY_PRODUCT`/`AMBIGUOUS` verdict, and never mixed into
#: the *same* `assign()` candidate pool as a higher source (that would let
#: two independent, redundant copies of one file's evidence -- e.g. an
#: archived transcript and a salvage blob of the very same original session
#: -- manufacture a spurious cross-source AMBIGUOUS for a single-attempt
#: family that either source alone would have cleanly resolved).
OFFLINE_SOURCES: tuple[str, ...] = (
    "archived_transcript",
    "salvage_verified_run",
    "harness_live",
)

#: Same charset `capsule._validate_agent_identifier` already enforces on
#: every *live* dispatch's `invocation_id`/`role`/`subject` -- reused, not
#: re-derived, so this module's own path-safety gate is exactly as strict as
#: the production writer, never stricter or looser by coincidence. A frozen
#: run's `events.jsonl` may predate that validation (or be hand-corrupted),
#: so this offline *reader* must still defend itself against a value that
#: was never actually checked at write time.
_SAFE_ID_RE = capsule_mod._AGENT_ID_RE


def _safe_ledger_component(value: object, *, what: str) -> str:
    """*value* is safe to use verbatim as one path *segment* under
    ``agents_index/`` (a directory name or a bare filename stem) -- never
    containing ``/``, ``..``, or any character outside the same conservative
    ASCII charset a live dispatch's own id must already satisfy.

    Raises ``ValueError`` (never returns an unsafe string) so every caller is
    forced to handle rejection explicitly -- this is the *one* decision point
    fixture 4 (an index reference that would otherwise point outside its
    directory) is tested against; there is no second, independent path-safety
    check anywhere else in this module's write path.
    """
    text = str(value)
    if not _SAFE_ID_RE.fullmatch(text):
        raise ValueError(f"{what} is not safe for a ledger path component: {text!r}")
    return text


@dataclass(frozen=True)
class _FrozenContract:
    """A minimal, read-only stand-in for `common.run_identity.RunContract`,
    built from a *lenient* dict read of a frozen run's own
    `trace/run_contract.json` (`salvage._read_run_contract` -- Task 9's own
    tolerant reader, reused rather than re-implemented: it already exists
    precisely because a historical contract's exact byte/field shape cannot
    always be re-validated the way `RunContract.from_dict`'s full
    `contract_hash` recomputation demands).

    Carries only the fields `agent_expectations`/`resolve_run_mode` actually
    consult for a `scan-market` run rebuilt offline. This is *not* a
    substitute for `capsule.load_run`'s full, hash-verified reconstruction --
    that function requires the run's own *active* workspace to still exist on
    disk, which offline reconstruction exists precisely because it may not
    (spec §6.1's "harness 消失后").
    """

    run_kind: str
    session_ref: str | None
    user_config: Mapping[str, object]


def _archived_transcripts_dir(run_dir: Path) -> Path:
    return Path(run_dir) / "trace" / "transcripts"


def _ledger_agents_index_root(run_dir: Path, *, ledger_root: Path | str | None = None) -> Path:
    """`agents_index/` under `_ledger/` -- the exact same "override for
    tests, else *run_dir*'s own grandparent" convention `scan.salvage.
    _salvage_dir` already established (not a second, independently-invented
    "where is `_ledger`" rule)."""
    from autoresearch.scan.outcome import LEDGER_DIRNAME

    base = Path(ledger_root) if ledger_root is not None else (Path(run_dir).parent / LEDGER_DIRNAME)
    return base / "agents_index"


def _load_frozen_run(run_dir: Path) -> tuple[RunHandle, dict, Path | None]:
    """Load a *frozen*, published run directory as a read-only `RunHandle` --
    never `capsule.load_run`/`require_active_run`, both of which require the
    run's *original* active workspace (`context_<engine>/scan_runs/<id>/`) to
    still physically exist. Offline reconstruction exists precisely because
    it may not any more; everything here reads only *this run's own
    published directory*, never a live-workspace guess (ruling 6).

    Identity verification (Controller ruling 5), each a hard, loud failure,
    none silently downgraded or guessed past:

    1. `run_dir` resolves under the *current* engine's own `reports_<engine>/
       scan/` root -- `scan.salvage._require_current_engine_run`, reused
       rather than a second copy of the same check; never widened to accept
       another engine's tree.
    2. `run_dir.name` parses as a real publish directory name (either naming
       generation) -- `run_naming.is_run_dir` -- so an arbitrary directory
       is never mistaken for a published run's `report_run_id`.
    3. `manifest.json` exists and parses; its own `run_id` is the
       `contract_run_id` -- a fact distinct from `report_run_id` (the
       directory name), never conflated or swapped for one another.
    4. `trace/run_contract.json` exists, parses (`salvage._read_run_contract`
       -- reused), and its own `run_id`/`engine` agree with the manifest and
       the current process's engine -- a run_contract that internally claims
       a *different* engine than the directory it lives under is rejected
       outright, never silently trusted.
    5. `capsule/events/events.jsonl` exists -- `agent_expectations`'s one
       required input; without it there is nothing to rebuild expectations
       from at all.

    Returns ``(handle, identity, original_staging)``. ``handle.staging`` is
    the *frozen* `trace/staging/` mirror -- the only place a published run's
    product files (`details/*.md`, `run_mode.json`, ...) still reliably live
    today, and what `agent_expectations`'s product-fallback/`resolve_run_mode`
    must read. ``original_staging`` is a *different* path: the run's own
    recorded `run_contract.workspace_path` + `/staging/` + `analysis_date` --
    the absolute path every one of this run's *own* transcripts actually
    recorded its writes under, at the time it ran, whether or not that
    directory still physically exists. `assign()`'s path-containment check
    (via its own `staging_root` override) must be verified against *that*
    root, never the frozen mirror -- an archived transcript's own recorded
    write path is `context_<engine>/scan_runs/<contract_run_id>/staging/
    <date>/details/<code>.md`, not `reports_<engine>/scan/<report_run_id>/
    trace/staging/details/<code>.md`; checking containment against the wrong
    one would silently fail every real historical write's tier-1 match
    (ruling 6: "离线根映射来自 contract/归档元信息", read here from the
    contract's own `workspace_path`, never guessed or substituted with the
    frozen mirror). ``None`` when the contract carries no `workspace_path` at
    all -- callers then have no verifiable containment root and must not
    invent one.
    """
    from autoresearch.scan import run_naming, salvage as salvage_mod

    resolved = salvage_mod._require_current_engine_run(Path(run_dir))
    report_run_id = resolved.name
    if not run_naming.is_run_dir(report_run_id):
        raise ValueError(
            f"{report_run_id!r} is not a recognized published run directory name"
        )

    manifest_path = resolved / "manifest.json"
    try:
        manifest_bytes = manifest_path.read_bytes()
    except FileNotFoundError as exc:
        raise ValueError(f"{manifest_path}: manifest.json is missing") from exc
    manifest = json.loads(manifest_bytes)
    if not isinstance(manifest, dict) or not manifest.get("run_id"):
        raise ValueError(f"{manifest_path}: manifest.json has no run_id (contract_run_id)")
    contract_run_id = str(manifest["run_id"])
    run_manifest_sha256 = sha256_bytes(manifest_bytes)

    contract_dict = salvage_mod._read_run_contract(resolved)
    if not contract_dict:
        raise ValueError(f"{resolved}: trace/run_contract.json is missing or unreadable")
    if str(contract_dict.get("run_id")) != contract_run_id:
        raise ValueError(
            f"identity mismatch: manifest.run_id={contract_run_id!r} != "
            f"run_contract.run_id={contract_dict.get('run_id')!r}"
        )
    contract_engine = str(contract_dict.get("engine"))
    if contract_engine != ws.ENGINE:
        raise ValueError(
            f"identity mismatch: run_contract.engine={contract_engine!r} != current "
            f"ws.ENGINE={ws.ENGINE!r} -- refusing to guess another engine's root"
        )
    analysis_date = str(
        contract_dict.get("analysis_date") or manifest.get("analysis_date") or ""
    )

    capsule_dir = resolved / "capsule"
    events_path = capsule_dir / "events" / "events.jsonl"
    if not events_path.is_file():
        raise ValueError(f"{events_path}: missing -- cannot rebuild expectations without it")

    contract = _FrozenContract(
        run_kind=str(contract_dict.get("run_kind") or "scan-market"),
        session_ref=(contract_dict.get("session_ref") or None),
        user_config=contract_dict.get("user_config") or {},
    )
    handle = RunHandle(
        run_id=contract_run_id,
        analysis_date=analysis_date,
        engine=contract_engine,
        workspace=resolved,
        staging=resolved / "trace" / "staging",
        capsule=capsule_dir,
        contract=contract,  # type: ignore[arg-type]  -- see _FrozenContract's own docstring
    )
    identity = {
        "report_run_id": report_run_id,
        "contract_run_id": contract_run_id,
        "engine": contract_engine,
        "run_manifest_sha256": run_manifest_sha256,
        "analysis_date": analysis_date,
    }
    workspace_path = contract_dict.get("workspace_path")
    original_staging = (
        Path(str(workspace_path)) / "staging" / analysis_date
        if workspace_path and analysis_date
        else None
    )
    return handle, identity, original_staging


def _read_archived_transcript_rows(run_dir: Path) -> tuple[list[dict], str | None]:
    """`trace/transcripts/_index.json`'s own ``transcripts`` rows, or ``(([],
    reason)`` for a genuinely absent/unreadable/schema-mismatched index --
    ruling 7's "unknown schema version is not an empty success": a schema
    this module does not understand is reported as a *skip reason*, never
    silently treated the same as "the index file does not exist"."""
    path = _archived_transcripts_dir(run_dir) / "_index.json"
    if not path.is_file():
        return [], None
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        return [], f"{path}: unreadable ({type(exc).__name__}: {exc})"
    if not isinstance(doc, dict):
        return [], f"{path}: not a JSON object"
    if doc.get("schema_version") != 1:
        return [], f"{path}: unrecognized schema_version={doc.get('schema_version')!r}"
    rows = doc.get("transcripts")
    if not isinstance(rows, list):
        return [], f"{path}: 'transcripts' is not a list"
    return [r for r in rows if isinstance(r, dict)], None


def _archived_transcript_targets(run_dir: Path) -> list[Path]:
    """Distinct archived gz paths (`retention.archive_transcripts`'s own
    naming: ``<agent>-<original stem>.jsonl.gz``) this run's own retention
    step already wrote -- deduped by resolved output path, since several
    usage rows can legitimately name the *same* underlying source (e.g.
    Codex sharing one rollout across several role dispatches collapses onto
    one archived target by that naming rule)."""
    seen: dict[Path, None] = {}
    for row in _read_archived_transcript_rows(run_dir)[0]:
        if row.get("status") != "PRESENT":
            continue
        agent, file_name = row.get("agent"), row.get("file")
        if not agent or not file_name:
            continue
        stem = Path(str(file_name)).stem
        seen.setdefault(_archived_transcripts_dir(run_dir) / f"{agent}-{stem}.jsonl.gz", None)
    return list(seen)


def _candidate_from_archive_bytes(
    data: bytes,
    *,
    engine: str,
    path: Path,
    segment_quality: str,
    session_ref: str | None,
) -> tuple[TranscriptCandidate, TranscriptSnapshot]:
    """One whole-file candidate from *already-read* archive bytes -- a
    retention-archived gz or a `scan.salvage` blob -- via
    `snapshot.snapshot_from_archive_bytes` (Task 2's own line parser, never a
    second one) and the same per-engine adapter the live candidate builders
    above use. Raises (never fabricates a degraded candidate) for content
    that is not valid gzip or does not classify -- the caller records that as
    a per-item error and simply does not add this candidate to the pool,
    exactly how `build_claude_candidates`/`build_codex_candidates` already
    treat an unreadable live ref."""
    snapshot = snapshot_from_archive_bytes(data, engine=engine, path=path)
    role = "subagent" if engine == "claude" else "unbound"
    ref = TranscriptRef(engine=engine, path=path, role=role, session_ref=session_ref)
    adapter = ClaudeTranscriptAdapter() if engine == "claude" else CodexTranscriptAdapter()
    stats = adapter.stats_from_rows(snapshot.rows, ref)
    candidate = TranscriptCandidate(
        engine=engine, path=path, ref=ref, stats=stats,
        segment_quality=segment_quality, session_ref=session_ref,
    )
    return candidate, snapshot


def _source1_archived_candidates(
    run_dir: Path, *, engine: str,
) -> tuple[tuple[TranscriptCandidate, ...], dict[str, TranscriptSnapshot], list[dict]]:
    """Source 1 (highest priority): this run's own retention-archived
    transcripts. Independently present per file: one bad archive never hides
    the others (ruling 7)."""
    _, schema_error = _read_archived_transcript_rows(run_dir)
    errors: list[dict] = []
    if schema_error is not None:
        errors.append({"source": "archived_transcript", "path": None, "error": schema_error})
    candidates: list[TranscriptCandidate] = []
    snapshots: dict[str, TranscriptSnapshot] = {}
    segment_quality = "complete" if engine == "claude" else "unknown"
    for target in _archived_transcript_targets(run_dir):
        try:
            data = target.read_bytes()
            candidate, snapshot = _candidate_from_archive_bytes(
                data, engine=engine, path=target, segment_quality=segment_quality,
                session_ref=None,
            )
        except Exception as exc:  # noqa: BLE001 - one unreadable archive is a fact, not a crash
            errors.append(
                {"source": "archived_transcript", "path": str(target),
                 "error": f"{type(exc).__name__}: {exc}"}
            )
            continue
        candidates.append(candidate)
        snapshots[str(candidate.path)] = snapshot
    return tuple(candidates), snapshots, errors


def _source2_salvage_candidates(
    run_dir: Path, *, engine: str, ledger_root: Path | str | None,
) -> tuple[tuple[TranscriptCandidate, ...], dict[str, TranscriptSnapshot], list[dict]]:
    """Source 2: this engine's own `scan.salvage` ledger -- only a row whose
    ``attribution`` is a *fact* (`salvage.is_fact`, the single decision point
    Task 9 established; never a second, independently-written
    ``== "VERIFIED_RUN"`` check here) may serve as an index input. A
    `TIME_WINDOW_ONLY`/`OVERWRITTEN_BY_LATER_RUN`/`UNKNOWN`/`ABSENT` row is
    reference material only -- skipped, never contributed as a candidate
    (mutation probe (b) in the task report proves this is load-bearing).

    A blob's on-disk location is resolved via `salvage.resolve_blob_path` --
    the **public accessor** Task 9's own fix round 2 built for exactly this
    reader (2026-09-13, commit `fbc1a8f`): a row's recorded ``blob["path"]``
    is trusted as-written (a real, pre-existing row may still name the flat
    ``blobs/<digest>`` layout `_store_blob` used before it switched to
    `trace.blobs`' two-level fan-out -- `_merge_rows`'s sticky rule never
    rewrites an already-`VERIFIED_RUN` row, so that old path is still where
    those 723 real rows' bytes actually are), falling back to a fresh
    digest-derived location only when no path was recorded at all. Calling
    `trace_blobs.blob_path` directly here -- as an earlier version of this
    function did -- recomputes only the *current* layout and silently drops
    every pre-fan-out row's evidence; this is exactly the "two modules
    computing the same content address independently" duplication spec §9
    forbids, now avoided by calling salvage's own accessor instead.
    """
    from autoresearch.scan import salvage as salvage_mod

    provenance_path = salvage_mod._provenance_path(run_dir, ledger_root=ledger_root)
    doc = salvage_mod._read_json_lenient(provenance_path)
    if not isinstance(doc, dict):
        return (), {}, []
    if doc.get("schema_version") != salvage_mod.PROVENANCE_SCHEMA_VERSION:
        return (), {}, [
            {"source": "salvage_verified_run", "path": str(provenance_path),
             "error": f"unrecognized schema_version={doc.get('schema_version')!r}"}
        ]
    rows = doc.get("files")
    if not isinstance(rows, list):
        return (), {}, []

    candidates: list[TranscriptCandidate] = []
    snapshots: dict[str, TranscriptSnapshot] = {}
    errors: list[dict] = []
    for row in rows:
        if not isinstance(row, dict) or row.get("file_kind") != "transcript":
            continue
        if not salvage_mod.is_fact(str(row.get("attribution"))):
            continue  # TIME_WINDOW_ONLY/OVERWRITTEN_BY_LATER_RUN/UNKNOWN/ABSENT: reference only
        blob = row.get("blob")
        if not isinstance(blob, dict):
            continue
        blob_path = salvage_mod.resolve_blob_path(run_dir, blob, ledger_root=ledger_root)
        if blob_path is None:
            errors.append(
                {"source": "salvage_verified_run", "path": None,
                 "error": f"cannot resolve blob location for {row.get('logical_name')!r} "
                          f"(recorded blob={blob!r})"}
            )
            continue
        segment_quality = "complete" if row.get("engine") == "claude" else "unknown"
        try:
            data = blob_path.read_bytes()
            candidate, snapshot = _candidate_from_archive_bytes(
                data, engine=str(row.get("engine") or engine), path=blob_path,
                segment_quality=segment_quality, session_ref=row.get("session_ref"),
            )
        except Exception as exc:  # noqa: BLE001 - one unreadable blob is a fact, not a crash
            errors.append(
                {"source": "salvage_verified_run", "path": str(blob_path),
                 "error": f"{type(exc).__name__}: {exc}"}
            )
            continue
        candidates.append(candidate)
        snapshots[str(candidate.path)] = snapshot
    return tuple(candidates), snapshots, errors


def _source3_harness_live_candidates(
    run_identity: RunIdentity,
    expectations: Mapping[str, Mapping[str, object]],
    *,
    engine: str,
    sessions_root: Path | str | None,
) -> tuple[tuple[TranscriptCandidate, ...], list[dict]]:
    """Source 3 (lowest priority): the harness's own still-live session
    directory -- built via the exact same, unmodified functions the active
    path uses (`build_claude_candidates`/`build_codex_candidates`); the only
    new behavior here is threading *sessions_root* through, so a test (or an
    operator pointing at a non-default location) never has to touch the real
    ``~/.claude``/``~/.codex``."""
    try:
        if engine == "claude":
            adapter = ClaudeTranscriptAdapter(projects_root=sessions_root) if sessions_root else None
            return build_claude_candidates(run_identity, adapter=adapter), []
        if engine == "codex":
            return (
                build_codex_candidates(run_identity, expectations, sessions_root=sessions_root),
                [],
            )
    except Exception as exc:  # noqa: BLE001 - a broken live search is a fact, not a crash
        return (), [{"source": "harness_live", "path": None, "error": f"{type(exc).__name__}: {exc}"}]
    return (), []


def _compute_revision_id(
    *, engine: str, contract_run_id: str, report_run_id: str,
    run_manifest_sha256: str, snapshot_ids: Sequence[str], parser_version: int,
) -> str:
    """Content identity only -- the target identity, every distinct source
    snapshot actually used, and the parser version, canonicalized (ruling 4).
    The real UTC ``computed_at`` timestamp deliberately never enters this
    computation: two calls against the exact same sources and the exact same
    parser version must produce the *same* revision_id no matter when either
    call happened, or a clock-driven rewrite would silently manufacture a new
    "version" of evidence that never actually changed (proven by mutation
    probe (a) in the task report)."""
    payload = {
        "engine": engine,
        "contract_run_id": contract_run_id,
        "report_run_id": report_run_id,
        "run_manifest_sha256": run_manifest_sha256,
        "snapshot_ids": sorted(set(snapshot_ids)),
        "parser_version": parser_version,
    }
    return sha256_bytes(canonical_json(payload).encode("utf-8"))


def _gone_ledger_row(inv_id: str, exp: Mapping[str, object], row: Mapping[str, object]) -> dict:
    return {
        "engine": None,
        "invocation_id": inv_id,
        "role": exp.get("role"),
        "subject": exp.get("subject"),
        "subject_key": exp.get("subject_key"),
        "stage": None,
        "source_path": None,
        "status": "GONE",
        "reason": row.get("reason"),
        "raw": None,
        "normalized": None,
        "snapshot_id": None,
        "source_sha256": None,
        "source_bytes": None,
        "rows": None,
        "unparsed_rows": None,
        "items": None,
        "model": None,
        "effort": None,
        "usage": None,
        "attempt": exp.get("attempt"),
        "expected": True,
        "binding_status": row.get("binding_status", "GONE"),
        "segment_quality": row.get("segment_quality"),
        "offline_source": None,
        "search_count": None,
    }


def _present_ledger_row(
    inv_id: str, exp: Mapping[str, object], row: Mapping[str, object], *,
    candidate: TranscriptCandidate, snapshot: TranscriptSnapshot, source_label: str,
    normalized_rel: str, raw_rel: str,
) -> tuple[dict, dict]:
    """One (index_row, normalized_doc) pair for a BOUND/UNVERIFIED_BY_PRODUCT
    invocation -- the index_row's field set is deliberately the *same* one
    `capsule.materialize_agent_index`'s own rows carry (spec: "shape 与
    capsule 的公共字段一致"), plus the offline-only extras (source/version/
    binding_status/segment_quality) the brief's own bullet allows ("额外含
    来源/版本/补录时间"). The normalized_doc mirrors `capsule._operation_row`'s
    exact per-operation shape -- reused via a direct call, not re-derived."""
    stats = candidate.stats
    normalized = stats.normalized
    usage = stats.usage
    index_row = {
        "engine": candidate.engine,
        "invocation_id": inv_id,
        "role": exp.get("role"),
        "subject": exp.get("subject"),
        "subject_key": exp.get("subject_key"),
        "stage": None,
        "source_path": str(candidate.path),
        "status": "PRESENT",
        "reason": row.get("reason"),
        "raw": raw_rel,
        "normalized": normalized_rel,
        "snapshot_id": snapshot.snapshot_id,
        "source_sha256": snapshot.source_prefix.sha256,
        "source_bytes": snapshot.source_prefix.byte_count,
        "rows": len(snapshot.rows),
        "unparsed_rows": snapshot.bad_lines,
        "items": len(normalized.items),
        "model": normalized.model,
        "effort": normalized.effort,
        "usage": {
            "messages": usage.messages, "input": usage.input, "output": usage.output,
            "cache_read": usage.cache_read, "cache_create": usage.cache_create,
            "reasoning_output": usage.reasoning_output, "status": usage.status,
        },
        "attempt": exp.get("attempt"),
        "expected": True,
        "binding_status": row["binding_status"],
        "segment_quality": row.get("segment_quality"),
        "offline_source": source_label,
        "search_count": row.get("search_count"),
    }
    doc = {
        "schema_version": CURRENT_TRANSCRIPT_SCHEMA_VERSION,
        "engine": candidate.engine,
        "invocation_id": inv_id,
        "role": normalized.ref.role,
        "subject": normalized.ref.subject,
        "status": normalized.status,
        "model": normalized.model,
        "effort": normalized.effort,
        "snapshot_id": snapshot.snapshot_id,
        "items": [
            {
                "index": item.index, "kind": item.kind,
                "payload": json.loads(canonical_json(dict(item.payload))),
                "timestamp": item.timestamp,
            }
            for item in normalized.items
        ],
        "operations": [capsule_mod._operation_row(op) for op in stats.operations],
    }
    return index_row, doc


def _unsupported_ledger_row(
    inv_id: str, exp: Mapping[str, object], *, reason: str,
) -> dict:
    row = _gone_ledger_row(inv_id, exp, {"reason": reason, "binding_status": "ERROR"})
    row["status"] = "UNSUPPORTED"
    return row


def offline_index(
    run_dir: Path | str, *, ledger_root: Path | str | None = None,
    sessions_root: Path | str | None = None,
) -> dict:
    """Rebuild, purely offline, the same per-invocation evidence table
    `agent_expectations`/`assign` already build for an active run -- for a
    *frozen*, already-published run whose capsule may show every invocation
    `GONE`. Writes only under the current engine's `_ledger/agents_index/`;
    the frozen run directory named by *run_dir* is never opened for writing
    (ruling 8).

    Identity is verified before anything else is attempted (`_load_frozen_
    run`, ruling 5); every one of its failures propagates -- there is no
    "whole run" try/except here the way `safe_bind_run` has for the *active*
    path, because a caller who explicitly asked to reconstruct one specific
    run is entitled to know precisely why that failed, not a silently
    degraded, empty result.

    Input order (spec §6.1, ruling 3's `OFFLINE_SOURCES`): a lower-priority
    source only ever fills in an invocation a higher-priority source left
    `GONE` -- see `OFFLINE_SOURCES`'s own docstring for why sources are never
    pooled together in one `assign()` call.

    `revision_id` is content-derived (`_compute_revision_id`); re-running
    against the exact same sources and the exact same
    `OFFLINE_INDEX_PARSER_VERSION` reuses the existing revision directory
    verbatim -- no new files, no rewritten evidence (ruling 4). The top-level
    pointer file is rewritten every call (`computed_at` is the one field that
    always reflects the real, current UTC time), but it is only switched to
    reference a *complete* revision directory, built atomically underneath a
    temporary name and renamed into place only once every `normalized`/`raw`
    reference inside it is on disk (ruling 4's "normalized/source 引用齐全后,
    最后原子发布顶层 index").
    """
    handle, identity, original_staging = _load_frozen_run(run_dir)
    run_dir_resolved = handle.workspace
    expectations = agent_expectations(handle)
    run_identity = RunIdentity(
        run_id=handle.run_id, engine=handle.engine, session_ref=handle.contract.session_ref,
    )
    # `assign()`'s path-containment check is verified against this run's own
    # *original* workspace staging path (from `run_contract.workspace_path`),
    # never the frozen `trace/staging/` mirror `handle.staging` points at --
    # see `_load_frozen_run`'s own docstring for why these two are different
    # roots serving two different questions. `None` (no recorded
    # `workspace_path`) means every write-path containment check degrades to
    # "cannot verify" (`assign()`'s own behavior when `staging` is `None`),
    # never a guessed substitute root.
    staging_root = original_staging

    all_errors: list[dict] = []
    all_unexpected: list[dict] = []
    all_unmatched: list[dict] = []
    snapshot_by_path: dict[str, TranscriptSnapshot] = {}
    candidate_by_key: dict[tuple, TranscriptCandidate] = {}

    def _register(
        candidates: Sequence[TranscriptCandidate], snapshots: Mapping[str, TranscriptSnapshot],
    ) -> None:
        snapshot_by_path.update(snapshots)
        for candidate in candidates:
            candidate_by_key[
                (str(candidate.path), candidate.ref.start_ordinal, candidate.ref.end_ordinal)
            ] = candidate

    merged_rows: dict[str, dict] = {}
    source_of: dict[str, str] = {}

    def _absorb(result: dict, *, source_label: str, only: set[str] | None = None) -> None:
        for inv_id, row in result["rows"].items():
            if only is not None and inv_id not in only:
                continue
            current = merged_rows.get(inv_id)
            if current is not None and current["binding_status"] != "GONE":
                continue
            merged_rows[inv_id] = row
            if row["binding_status"] != "GONE":
                source_of[inv_id] = source_label
        all_unexpected.extend(result["unexpected"])
        all_unmatched.extend(result["unmatched"])

    # Source 1: this run's own retention-archived transcripts.
    archived_candidates, archived_snapshots, archived_errors = _source1_archived_candidates(
        run_dir_resolved, engine=handle.engine
    )
    all_errors.extend(archived_errors)
    _register(archived_candidates, archived_snapshots)
    _absorb(
        assign(archived_candidates, expectations, run_identity, staging_root=staging_root),
        source_label="archived_transcript",
    )

    still_gone = {inv for inv, row in merged_rows.items() if row["binding_status"] == "GONE"}

    # Source 2: this engine's own salvage ledger (VERIFIED_RUN only).
    if still_gone:
        salvage_candidates, salvage_snapshots, salvage_errors = _source2_salvage_candidates(
            run_dir_resolved, engine=handle.engine, ledger_root=ledger_root,
        )
        all_errors.extend(salvage_errors)
        _register(salvage_candidates, salvage_snapshots)
        if salvage_candidates:
            _absorb(
                assign(salvage_candidates, expectations, run_identity, staging_root=staging_root),
                source_label="salvage_verified_run", only=still_gone,
            )

    still_gone = {inv for inv, row in merged_rows.items() if row["binding_status"] == "GONE"}

    # Source 3: the harness's own still-live session (reused, unmodified).
    if still_gone:
        live_candidates, live_errors = _source3_harness_live_candidates(
            run_identity, expectations, engine=handle.engine, sessions_root=sessions_root,
        )
        all_errors.extend(live_errors)
        _register(live_candidates, {})  # source 3 keeps no snapshot -- re-derived per-row below
        if live_candidates:
            _absorb(
                assign(live_candidates, expectations, run_identity, staging_root=staging_root),
                source_label="harness_live", only=still_gone,
            )

    # ---------------------------------------------------- build ledger rows
    ledger_rows: list[dict] = []
    normalized_docs: dict[str, dict] = {}  # invocation_id -> doc
    raw_by_snapshot: dict[str, bytes] = {}
    snapshot_ids_used: list[str] = []

    for inv_id in sorted(expectations):
        exp = expectations[inv_id]
        row = merged_rows.get(inv_id)
        if row is None or row["binding_status"] not in ("BOUND", "UNVERIFIED_BY_PRODUCT"):
            ledger_rows.append(_gone_ledger_row(inv_id, exp, row or {"reason": "not attempted"}))
            continue
        try:
            safe_inv = _safe_ledger_component(inv_id, what="invocation_id")
        except ValueError as exc:
            all_errors.append({"source": source_of.get(inv_id), "invocation_id": inv_id,
                                "error": str(exc)})
            ledger_rows.append(_unsupported_ledger_row(inv_id, exp, reason=str(exc)))
            continue
        key = (row["candidate_path"], row.get("candidate_start_ordinal"),
               row.get("candidate_end_ordinal"))
        candidate = candidate_by_key.get(key)
        if candidate is None:
            reason = "attributed candidate could not be re-resolved for normalization"
            all_errors.append({"source": source_of.get(inv_id), "invocation_id": inv_id,
                                "error": reason})
            ledger_rows.append(_unsupported_ledger_row(inv_id, exp, reason=reason))
            continue
        snapshot = snapshot_by_path.get(str(candidate.path))
        if snapshot is None:
            # Source 3 (harness live) never hands back its internal snapshot
            # -- a single, honest re-read of this one already-closed source,
            # only for the rare invocation that fell all the way through to
            # it (sources 1/2 always populate snapshot_by_path themselves).
            try:
                snapshot = capture_snapshot(candidate.path, engine=candidate.engine)
                if snapshot.source_changed:
                    raise RuntimeError(
                        "source changed identity between attribution and normalization"
                    )
            except Exception as exc:  # noqa: BLE001 - one item's failure, not the whole run's
                reason = f"could not re-derive snapshot for normalization: {exc}"
                all_errors.append({"source": source_of.get(inv_id), "invocation_id": inv_id,
                                    "error": reason})
                ledger_rows.append(_unsupported_ledger_row(inv_id, exp, reason=reason))
                continue
        normalized_rel = f"{identity['report_run_id']}/{{revision}}/normalized/{safe_inv}.json"
        raw_rel = f"{identity['report_run_id']}/{{revision}}/raw/{snapshot.snapshot_id}.jsonl.gz"
        index_row, doc = _present_ledger_row(
            inv_id, exp, row, candidate=candidate, snapshot=snapshot,
            source_label=source_of.get(inv_id, "unknown"),
            normalized_rel=normalized_rel, raw_rel=raw_rel,
        )
        ledger_rows.append(index_row)
        normalized_docs[inv_id] = doc
        raw_by_snapshot[snapshot.snapshot_id] = snapshot.archive_bytes
        snapshot_ids_used.append(snapshot.snapshot_id)

    revision_id = _compute_revision_id(
        engine=identity["engine"], contract_run_id=identity["contract_run_id"],
        report_run_id=identity["report_run_id"], run_manifest_sha256=identity["run_manifest_sha256"],
        snapshot_ids=snapshot_ids_used, parser_version=OFFLINE_INDEX_PARSER_VERSION,
    )
    for row in ledger_rows:
        if row.get("normalized"):
            row["normalized"] = row["normalized"].replace("{revision}", revision_id)
        if row.get("raw"):
            row["raw"] = row["raw"].replace("{revision}", revision_id)

    coverage = _tally_coverage(
        {row["invocation_id"]: row for row in ledger_rows},
        unexpected=len(all_unexpected), denominator_quality=(
            "lower_bound"
            if any(exp.get("denominator_quality") == "lower_bound" for exp in expectations.values())
            else "full"
        ),
    )

    index_root = _ledger_agents_index_root(run_dir_resolved, ledger_root=ledger_root)
    _publish_offline_revision(
        index_root, report_run_id=identity["report_run_id"], revision_id=revision_id,
        normalized_docs=normalized_docs, raw_by_snapshot=raw_by_snapshot,
    )

    top_level = {
        "schema_version": CURRENT_TRANSCRIPT_SCHEMA_VERSION,
        "report_run_id": identity["report_run_id"],
        "contract_run_id": identity["contract_run_id"],
        "engine": identity["engine"],
        "run_manifest_sha256": identity["run_manifest_sha256"],
        "current_revision_id": revision_id,
        "parser_version": OFFLINE_INDEX_PARSER_VERSION,
        "computed_at": _now_iso(),
        "invocations": ledger_rows,
        "coverage": coverage,
        "unexpected": all_unexpected,
        "unmatched": all_unmatched,
        "errors": all_errors,
    }
    atomic_write_json(index_root / f"{identity['report_run_id']}.json", top_level)
    return top_level


def _publish_offline_revision(
    index_root: Path, *, report_run_id: str, revision_id: str,
    normalized_docs: Mapping[str, dict], raw_by_snapshot: Mapping[str, bytes],
) -> None:
    """Write `agents_index/<report_run_id>/<revision_id>/{normalized,raw}/`
    -- reusing this exact same content whenever it already exists (ruling 4:
    "相同源快照与解析器版本重跑不改证据"), since `revision_id` is itself a hash
    of every source that fed it: an existing directory of that exact name is
    guaranteed byte-identical to what this call would produce again. Nothing
    under an *older* revision_id is ever touched or removed (ruling 4's
    "保留上一版" -- decided here as: never delete, a new revision is always a
    new sibling directory, and the only thing this run's own `os.replace`
    call ever retargets is the top-level pointer file, not any revision
    directory's own name).

    Builds the new revision under a temporary sibling name first and
    `os.replace`s it into place only once fully populated -- so a reader can
    never observe a half-written revision directory under its real name, and
    a crash mid-build leaves only an orphaned temp directory, never a
    corrupted "complete" one.
    """
    revision_dir = index_root / report_run_id / _safe_ledger_component(revision_id, what="revision_id")
    if revision_dir.is_dir():
        return  # ruling 4: identical revision_id => identical content, never rewritten
    run_root = index_root / report_run_id
    run_root.mkdir(parents=True, exist_ok=True)
    tmp_dir = Path(tempfile.mkdtemp(prefix=f".{revision_id}.", dir=run_root))
    try:
        normalized_dir = tmp_dir / "normalized"
        raw_dir = tmp_dir / "raw"
        normalized_dir.mkdir()
        raw_dir.mkdir()
        for inv_id, doc in normalized_docs.items():
            safe_inv = _safe_ledger_component(inv_id, what="invocation_id")
            (normalized_dir / f"{safe_inv}.json").write_text(
                canonical_json(doc), encoding="utf-8"
            )
        for snapshot_id, archive_bytes in raw_by_snapshot.items():
            safe_snapshot = _safe_ledger_component(snapshot_id, what="snapshot_id")
            (raw_dir / f"{safe_snapshot}.jsonl.gz").write_bytes(archive_bytes)
        if revision_dir.is_dir():
            return  # another process/call won the race; identical content by revision_id
        os.replace(tmp_dir, revision_dir)
    finally:
        if tmp_dir.exists():
            shutil.rmtree(tmp_dir, ignore_errors=True)


def _cli_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="autoresearch.scan.transcript_binder")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument(
        "--run-id",
        help=(
            "contract_run_id (capsule identity) of an *active* run. Staging is taken "
            "from this run's own handle -- never guessed as the newest directory in a "
            "date folder."
        ),
    )
    group.add_argument(
        "--offline",
        metavar="RUN_DIR",
        help=(
            "Path to an already-published, frozen run directory "
            "(reports_<engine>/scan/<report_run_id>/). Rebuilds the ledger index "
            "offline; never writes into this directory."
        ),
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _cli_parser().parse_args(argv)
    try:
        if args.offline is not None:
            report = offline_index(args.offline)
        else:
            handle = capsule_mod.require_active_run(args.run_id)
            report = bind_run(handle.run_id, handle.staging)
    except Exception as exc:  # noqa: BLE001 - CLI surfaces one structured error, non-zero exit
        print(
            json.dumps(
                {"ok": False, "error": f"{type(exc).__name__}: {exc}"}, ensure_ascii=False
            ),
            file=sys.stderr,
        )
        return 2
    print(json.dumps(report, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = [
    "BOUNDARY_QUALITIES",
    "DENOMINATOR_QUALITIES",
    "EXPECTATION_SOURCES",
    "OFFLINE_INDEX_PARSER_VERSION",
    "OFFLINE_SOURCES",
    "REPORT_STATUSES",
    "TranscriptCandidate",
    "agent_expectations",
    "assign",
    "bind_run",
    "build_claude_candidates",
    "build_codex_candidates",
    "offline_index",
    "safe_bind_run",
]
