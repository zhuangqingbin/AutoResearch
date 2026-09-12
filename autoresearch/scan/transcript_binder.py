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

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from autoresearch.common import workspace as ws
from autoresearch.contracts import artifacts
from autoresearch.contracts.profiles import profile_factory
from autoresearch.trace import capsule as capsule_mod
from autoresearch.trace.capsule_models import RunHandle
from autoresearch.trace.transcripts.base import RunIdentity, TranscriptRef, TranscriptStats
from autoresearch.trace.transcripts.claude import ClaudeTranscriptAdapter
from autoresearch.trace.transcripts.codex import (
    CodexTranscriptAdapter,
    discover_rollout_candidates,
    ordinal_window_for_timestamps,
)
from autoresearch.trace.transcripts.snapshot import capture_snapshot

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
                    segment_quality=quality,
                    session_ref=run_identity.session_ref,
                )
            )
    return tuple(candidates)


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


def _normalize_operation_path(
    raw: str | None,
    *,
    cwd: Path | None,
    workspace: Path,
    engine: str,
) -> tuple[Path | None, str | None]:
    """Resolve *raw* against the call's own cwd, then verify it (spec §4.3).

    The only path work this whole task performs (Controller ruling 1): given
    an already-classified operation's structured path, join it to *cwd* when
    relative, then reject anything that (after following any symlinks --
    `Path.resolve()` chases them, so a symlink-based escape is caught the
    same way a literal ``..`` is, never trusted as "unverified") lands
    outside *workspace* -- which is this run's own root, so this single
    check rejects directory escape *and* cross-run at once (a different
    run's workspace is a sibling directory, never a sub-path of this one)
    -- or that crosses into another engine's root.
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
        workspace_resolved = workspace.resolve(strict=False)
    except (OSError, RuntimeError) as exc:  # pragma: no cover - platform-dependent
        return None, f"workspace root could not be resolved: {exc}"
    if not resolved.is_relative_to(workspace_resolved):
        return None, f"path escapes this run's workspace: {resolved}"
    for other in ws.ENGINES:
        if other == engine:
            continue
        if f"context_{other}" in resolved.parts or f"reports_{other}" in resolved.parts:
            return None, f"path crosses into engine {other!r}: {resolved}"
    return resolved, None


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


def assign(
    candidates: Sequence[TranscriptCandidate],
    expectations: Mapping[str, Mapping[str, object]],
    run_identity: RunIdentity,
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
    """
    workspace = ws.find_run_root(run_identity.run_id)
    staging = _staging_root(workspace) if workspace is not None else None
    staging_resolved = staging.resolve(strict=False) if staging is not None else None

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

    candidate_products: list[dict[str, list]] = []
    for candidate in accepted:
        hits: dict[str, list] = {}
        if staging_resolved is not None and workspace is not None:
            for op in candidate.stats.operations:
                if op.kind != "WRITE_SUCCEEDED":
                    continue
                resolved, _reason = _normalize_operation_path(
                    op.path,
                    cwd=run_identity.cwd,
                    workspace=workspace,
                    engine=run_identity.engine,
                )
                if resolved is None:
                    continue
                try:
                    rel = resolved.relative_to(staging_resolved).as_posix()
                except ValueError:
                    continue
                hits.setdefault(rel, []).append(op)
        candidate_products.append(hits)

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
                row["search_count"] = (
                    sum(
                        1
                        for op in candidate.stats.operations
                        if op.kind.startswith("SEARCH_")
                    )
                    if candidate.segment_quality == "complete"
                    else None
                )
                row["reason"] = (
                    None
                    if has_product
                    else "call identity verified by dispatch window; "
                    "no verified product write found in the bound segment"
                )
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
                row["reason"] = "no candidate transcript evidences this invocation"
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


__all__ = [
    "BOUNDARY_QUALITIES",
    "DENOMINATOR_QUALITIES",
    "EXPECTATION_SOURCES",
    "TranscriptCandidate",
    "agent_expectations",
    "assign",
    "build_claude_candidates",
    "build_codex_candidates",
]
