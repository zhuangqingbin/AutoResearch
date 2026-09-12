"""Single-source, one-time transcript snapshot (Task 2).

Spec: docs/superpowers/specs/2026-09-12-scene-reconstruction-transcript-
binding-design.md §5.1.  Covers acceptance-matrix row S01 ("活源追加/尾半行/替换
→ 同一稳定前缀；错误不混合") plus the snapshot-level half of S02 (two refs
sharing one captured snapshot).  O01/O02 and the rest of S02 (raw dedup, usage
overlap de-dup) live in tests/trace/test_transcript_adapters.py and
tests/trace/test_capsule.py -- see the Task 2 report for the full mapping.
"""

from __future__ import annotations

import gzip
import json
import os
from pathlib import Path

import pytest

from autoresearch.trace.transcripts.base import ArchiveDigest, SourcePrefixDigest

# --------------------------------------------------------------------------
# "Before the fix" characterization: repeated live reads disagree.
#
# Brief bullet 1 explicitly requires demonstrating this *before* implementing
# capture_snapshot.  This test exercises only pre-existing (Task 1) code --
# no import from snapshot.py -- and documents the defect capture_snapshot
# exists to close.  Kept as a permanent regression guard: if some future
# change reintroduces a live re-read into a snapshot-based consumer, this
# still proves why that is a bug.
# --------------------------------------------------------------------------


def test_naive_repeated_reads_of_a_live_appended_transcript_disagree(tmp_path):
    from autoresearch.trace.transcripts.base import TranscriptRef
    from autoresearch.trace.transcripts.claude import ClaudeTranscriptAdapter

    path = tmp_path / "live.jsonl"
    path.write_text(
        json.dumps({"type": "user", "message": {"content": "first"}}) + "\n",
        encoding="utf-8",
    )
    ref = TranscriptRef(engine="claude", path=path, role="subagent")
    adapter = ClaudeTranscriptAdapter()

    first_read = adapter.normalize(ref)
    # A live writer appends a second row between two calls that both believe
    # they are reading "the transcript" -- exactly what an un-snapshotted
    # archive step + usage step + hash step do today (capsule.py before this
    # task: adapter.normalize(ref), adapter.usage(ref), _raw_archive_bytes(source),
    # sha256_file(source) -- four separate live reads of the same path).
    with path.open("a", encoding="utf-8") as handle:
        handle.write(
            json.dumps({"type": "user", "message": {"content": "second"}}) + "\n"
        )
    second_read = adapter.normalize(ref)

    assert len(first_read.items) != len(second_read.items)


# --------------------------------------------------------------------------
# S01: capture_snapshot fixes one stable, complete-lines-only prefix.
# --------------------------------------------------------------------------


def _write_jsonl(path: Path, rows: list[dict]) -> None:
    path.write_text(
        "\n".join(json.dumps(row) for row in rows) + "\n", encoding="utf-8"
    )


def test_capture_snapshot_ignores_bytes_appended_after_capture(tmp_path):
    from autoresearch.trace.transcripts.snapshot import capture_snapshot

    path = tmp_path / "t.jsonl"
    _write_jsonl(path, [{"type": "a", "n": 1}, {"type": "a", "n": 2}])

    snapshot = capture_snapshot(path, engine="claude")
    assert len(snapshot.rows) == 2

    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps({"type": "a", "n": 3}) + "\n")

    # The already-captured snapshot is immutable: the later append is simply
    # not part of it.
    assert len(snapshot.rows) == 2
    assert snapshot.source_changed is False

    # A *fresh* capture legitimately sees the growth -- a new, independent
    # snapshot, never a silent mutation of the old one.
    later = capture_snapshot(path, engine="claude")
    assert len(later.rows) == 3
    assert later.snapshot_id != snapshot.snapshot_id


def test_capture_snapshot_excludes_trailing_half_line(tmp_path):
    from autoresearch.trace.transcripts.snapshot import capture_snapshot

    path = tmp_path / "t.jsonl"
    complete = json.dumps({"type": "a", "n": 1}) + "\n"
    half = '{"type": "a", "n": 2, "unfinishe'
    path.write_bytes((complete + half).encode("utf-8"))

    snapshot = capture_snapshot(path, engine="claude")

    assert len(snapshot.rows) == 1
    assert snapshot.rows[0]["n"] == 1
    assert snapshot.trailing_partial_bytes == len(half.encode("utf-8"))
    assert snapshot.bad_lines == 0
    assert snapshot.cutoff_bytes == len(complete.encode("utf-8"))
    assert snapshot.source_prefix.byte_count == snapshot.cutoff_bytes


def test_capture_snapshot_recovers_a_final_row_missing_only_its_newline(tmp_path):
    """A statically-written file that simply omits its final newline is NOT
    a half line: the tail is judged by whether it parses, not by newline
    presence (regression: a file written via ``"\\n".join(rows)`` with no
    trailing separator -- exactly tests/trace/test_usage_harvest.py's own
    fixture convention -- must not silently lose its last row)."""
    from autoresearch.trace.transcripts.snapshot import capture_snapshot

    path = tmp_path / "t.jsonl"
    rows = [{"type": "a", "n": 1}, {"type": "a", "n": 2}]
    path.write_text("\n".join(json.dumps(row) for row in rows), encoding="utf-8")
    assert not path.read_bytes().endswith(b"\n")

    snapshot = capture_snapshot(path, engine="claude")

    assert [row["n"] for row in snapshot.rows] == [1, 2]
    assert snapshot.trailing_partial_bytes == 0
    assert snapshot.bad_lines == 0
    assert snapshot.cutoff_bytes == len(path.read_bytes())
    assert snapshot.source_prefix.byte_count == snapshot.cutoff_bytes


def test_capture_snapshot_counts_a_complete_but_unparseable_line_as_bad(tmp_path):
    from autoresearch.trace.transcripts.snapshot import capture_snapshot

    path = tmp_path / "t.jsonl"
    path.write_text(
        json.dumps({"type": "a", "n": 1}) + "\n" + "not-json-at-all\n",
        encoding="utf-8",
    )

    snapshot = capture_snapshot(path, engine="claude")

    assert len(snapshot.rows) == 1
    assert snapshot.bad_lines == 1
    assert snapshot.trailing_partial_bytes == 0


def test_capture_snapshot_rejects_a_json_array_line_as_bad_not_a_row(tmp_path):
    """A syntactically complete, valid-JSON line that is not an object must
    not silently become a row -- rows are always JSONL *objects*."""
    from autoresearch.trace.transcripts.snapshot import capture_snapshot

    path = tmp_path / "t.jsonl"
    path.write_text(json.dumps([1, 2, 3]) + "\n", encoding="utf-8")

    snapshot = capture_snapshot(path, engine="claude")

    assert snapshot.rows == ()
    assert snapshot.bad_lines == 1


def test_capture_snapshot_flags_source_changed_when_identity_shifts_mid_capture(
    tmp_path, monkeypatch
):
    """A path whose filesystem identity differs between the pre-open stat and
    the opened-fd stat cannot be trusted as one coherent read -- the two-stat
    technique this test drives deterministically via a monkeypatched seam
    (no real concurrency needed: the fake identity always disagrees with the
    real fd)."""
    import autoresearch.trace.transcripts.snapshot as snapshot_mod

    path = tmp_path / "t.jsonl"
    _write_jsonl(path, [{"type": "a", "n": 1}])
    real_stat = os.stat(path)

    class _FakeStat:
        st_ino = real_stat.st_ino + 1
        st_dev = real_stat.st_dev

    monkeypatch.setattr(snapshot_mod, "_stat", lambda p: _FakeStat())

    snapshot = snapshot_mod.capture_snapshot(path, engine="claude")

    assert snapshot.source_changed is True
    # Still a self-consistent read of whatever the (single) open fd saw --
    # source_changed is a diagnostic, not a corrupted result.
    assert len(snapshot.rows) == 1


def test_capture_snapshot_source_changed_is_false_in_the_ordinary_case(tmp_path):
    from autoresearch.trace.transcripts.snapshot import capture_snapshot

    path = tmp_path / "t.jsonl"
    _write_jsonl(path, [{"type": "a", "n": 1}])

    snapshot = capture_snapshot(path, engine="claude")

    assert snapshot.source_changed is False


def test_capture_snapshot_missing_file_propagates_file_not_found(tmp_path):
    from autoresearch.trace.transcripts.snapshot import capture_snapshot

    with pytest.raises(FileNotFoundError):
        capture_snapshot(tmp_path / "does-not-exist.jsonl", engine="claude")


def test_capture_snapshot_rejects_a_symlink_source(tmp_path):
    from autoresearch.trace.transcripts.snapshot import capture_snapshot

    real = tmp_path / "real.jsonl"
    _write_jsonl(real, [{"type": "a", "n": 1}])
    link = tmp_path / "link.jsonl"
    link.symlink_to(real)

    with pytest.raises(ValueError, match="symlink"):
        capture_snapshot(link, engine="claude")


def test_capture_snapshot_empty_file(tmp_path):
    from autoresearch.trace.transcripts.snapshot import capture_snapshot

    path = tmp_path / "empty.jsonl"
    path.write_bytes(b"")

    snapshot = capture_snapshot(path, engine="claude")

    assert snapshot.rows == ()
    assert snapshot.cutoff_bytes == 0
    assert snapshot.bad_lines == 0
    assert snapshot.trailing_partial_bytes == 0
    assert snapshot.last_ordinal is None
    assert snapshot.source_prefix.byte_count == 0


# --------------------------------------------------------------------------
# Hash provenance: source_prefix / archive stay the two distinct §3.2
# families, both genuinely derived from the one captured prefix.
# --------------------------------------------------------------------------


def test_capture_snapshot_source_prefix_hash_matches_the_read_bytes(tmp_path):
    import hashlib

    from autoresearch.trace.transcripts.snapshot import capture_snapshot

    path = tmp_path / "t.jsonl"
    rows = [{"type": "a", "n": 1}, {"type": "a", "n": 2}]
    _write_jsonl(path, rows)
    raw_bytes = path.read_bytes()

    snapshot = capture_snapshot(path, engine="claude")

    assert isinstance(snapshot.source_prefix, SourcePrefixDigest)
    assert snapshot.source_prefix.sha256 == hashlib.sha256(raw_bytes).hexdigest()
    assert snapshot.snapshot_id == snapshot.source_prefix.sha256


def test_capture_snapshot_archive_is_redacted_deterministic_gzip(tmp_path):
    from autoresearch.trace.transcripts.snapshot import capture_snapshot

    path = tmp_path / "t.jsonl"
    _write_jsonl(
        path,
        [{"type": "a", "secret": "sk-ant-FIXTUREONLYNOTREAL00000000000000000000"}],
    )

    snapshot = capture_snapshot(path, engine="claude")

    assert isinstance(snapshot.archive, ArchiveDigest)
    body = gzip.decompress(snapshot.archive_bytes).decode("utf-8")
    assert "FIXTUREONLYNOTREAL" not in body
    # mtime=0 gzip header bytes 4:8, matching capsule.py's existing archive
    # discipline -- a re-capture of byte-identical content is byte-identical.
    assert snapshot.archive_bytes[4:8] == b"\x00\x00\x00\x00"
    again = capture_snapshot(path, engine="claude")
    assert again.archive_bytes == snapshot.archive_bytes
    assert again.archive.sha256 == snapshot.archive.sha256


def test_capture_snapshot_archive_bytes_never_written_unredacted_to_disk(tmp_path):
    """capture_snapshot must not leave an extra, unredacted copy of the
    source on disk -- it only ever returns bytes in memory; persistence is
    the caller's job (capsule.py), using atomic_write_bytes."""
    from autoresearch.trace.transcripts.snapshot import capture_snapshot

    path = tmp_path / "t.jsonl"
    _write_jsonl(path, [{"type": "a", "secret": "sk-ant-FIXTUREONLYNOTREAL0000"}])

    capture_snapshot(path, engine="claude")

    for candidate in tmp_path.rglob("*"):
        if candidate == path:
            continue
        assert candidate.is_dir(), f"unexpected extra file left behind: {candidate}"


# --------------------------------------------------------------------------
# Codex-shaped rows: last_ordinal tracking (needed for the segment/overlap
# machinery downstream in codex.py/usage_harvest.py).
# --------------------------------------------------------------------------


def test_capture_snapshot_tracks_the_maximum_ordinal_seen(tmp_path):
    from autoresearch.trace.transcripts.snapshot import capture_snapshot

    path = tmp_path / "rollout.jsonl"
    _write_jsonl(
        path,
        [
            {"type": "session_meta", "ordinal": 0},
            {"type": "turn_context", "ordinal": 1},
            {"type": "response_item", "ordinal": 2},
        ],
    )

    snapshot = capture_snapshot(path, engine="codex")

    assert snapshot.last_ordinal == 2


def test_capture_snapshot_last_ordinal_is_none_without_ordinal_fields(tmp_path):
    from autoresearch.trace.transcripts.snapshot import capture_snapshot

    path = tmp_path / "t.jsonl"
    _write_jsonl(path, [{"type": "user", "message": {"content": "hi"}}])

    snapshot = capture_snapshot(path, engine="claude")

    assert snapshot.last_ordinal is None


# --------------------------------------------------------------------------
# Two invocations sharing one source (S01/S02 seam): one capture, two
# distinct rows-derived views, no second read.  Full raw-dedup + usage
# no-double-count coverage lives in test_transcript_adapters.py/test_capsule.py.
# --------------------------------------------------------------------------


def test_one_snapshot_serves_two_disjoint_segments_without_rereading(tmp_path, monkeypatch):
    import autoresearch.trace.transcripts.snapshot as snapshot_mod

    path = tmp_path / "rollout.jsonl"
    _write_jsonl(
        path,
        [
            {"type": "session_meta", "ordinal": 0},
            {"type": "response_item", "ordinal": 1, "payload": {"role": "a"}},
            {"type": "response_item", "ordinal": 2, "payload": {"role": "b"}},
        ],
    )
    calls = {"n": 0}
    real_read_bytes = snapshot_mod._read_prefix

    def counting_read(*args, **kwargs):
        calls["n"] += 1
        return real_read_bytes(*args, **kwargs)

    monkeypatch.setattr(snapshot_mod, "_read_prefix", counting_read)

    snapshot = snapshot_mod.capture_snapshot(path, engine="codex")
    # Two independent "segments" of interest, sliced in memory from the one
    # already-captured rows tuple -- no adapter call here re-reads the file.
    segment_a = [row for row in snapshot.rows if row.get("ordinal", -1) <= 1]
    segment_b = [row for row in snapshot.rows if row.get("ordinal", -1) >= 2]

    assert calls["n"] == 1
    assert len(segment_a) == 2  # session_meta + ordinal 1
    assert len(segment_b) == 1
