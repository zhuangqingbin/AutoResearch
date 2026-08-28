from __future__ import annotations

import os
from concurrent.futures import ThreadPoolExecutor
from multiprocessing import get_context
from pathlib import Path

import pandas as pd
import pytest

from autoresearch.trace.blobs import (
    blob_path,
    dataframe_bytes,
    put_bytes,
    put_dataframe,
    put_file,
)


def _put_blob_process(args: tuple[str, bytes]) -> str:
    root, payload = args
    return put_bytes(Path(root), payload)


def _dataframe_bytes_process(frame: pd.DataFrame) -> bytes:
    return dataframe_bytes(frame)


def test_blob_store_deduplicates_identical_content_and_sets_private_mode(tmp_path):
    first = put_bytes(tmp_path, b"same")
    second = put_bytes(tmp_path, b"same")

    assert first == second
    assert blob_path(tmp_path, first).read_bytes() == b"same"
    assert blob_path(tmp_path, first).stat().st_mode & 0o777 == 0o600
    assert len(list((tmp_path / "blobs/sha256").rglob("*"))) == 2


def test_blob_store_detects_existing_corruption(tmp_path):
    digest = put_bytes(tmp_path, b"original")
    blob_path(tmp_path, digest).write_bytes(b"corrupt")

    with pytest.raises(RuntimeError, match="corrupt|collision"):
        put_bytes(tmp_path, b"original")


def test_blob_store_rejects_symlinked_components_and_blob(tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    (tmp_path / "blobs").symlink_to(outside, target_is_directory=True)
    with pytest.raises(ValueError, match="symlink"):
        put_bytes(tmp_path, b"secret")

    (tmp_path / "blobs").unlink()
    digest = put_bytes(tmp_path, b"safe")
    target = blob_path(tmp_path, digest)
    target.unlink()
    target.symlink_to(tmp_path / "outside-file")
    with pytest.raises(ValueError, match="symlink"):
        blob_path(tmp_path, digest)
    with pytest.raises(ValueError, match="symlink"):
        put_bytes(tmp_path, b"safe")


def test_concurrent_blob_publication_is_atomic_and_leaves_no_temps(tmp_path):
    payload = b"same concurrent payload" * 1000
    with ThreadPoolExecutor(max_workers=12) as pool:
        hashes = list(pool.map(lambda _: put_bytes(tmp_path, payload), range(48)))

    assert len(set(hashes)) == 1
    assert blob_path(tmp_path, hashes[0]).read_bytes() == payload
    assert not list((tmp_path / "blobs").rglob("*.tmp"))
    assert not list((tmp_path / "blobs").rglob(".*.tmp"))


def test_multiprocess_blob_publication_keeps_one_verified_blob(tmp_path):
    payload = b"cross-process" * 1000
    with get_context("fork").Pool(6) as pool:
        hashes = pool.map(_put_blob_process, [(str(tmp_path), payload)] * 24)
    assert len(set(hashes)) == 1
    assert blob_path(tmp_path, hashes[0]).read_bytes() == payload
    assert not list((tmp_path / "blobs").rglob("*.tmp"))


def test_put_file_copies_exact_bytes_without_following_source_symlink(tmp_path):
    capsule = tmp_path / "capsule"
    capsule.mkdir()
    source = tmp_path / "source.bin"
    source.write_bytes(b"\x00exact\xff")
    digest = put_file(capsule, source)
    assert blob_path(capsule, digest).read_bytes() == source.read_bytes()

    link = tmp_path / "source-link"
    link.symlink_to(source)
    with pytest.raises(ValueError, match="symlink"):
        put_file(capsule, link)


def test_dataframe_parquet_serialization_is_stable_and_preserves_schema(tmp_path):
    frame = pd.DataFrame(
        {
            "code": pd.Series(["000001", "000002"], dtype="string"),
            "qty": pd.Series([1, 2], dtype="int64"),
            "price": pd.Series([1.25, 2.5], dtype="float64"),
            "when": pd.to_datetime(["2026-08-27", "2026-08-28"]),
        }
    )

    assert dataframe_bytes(frame) == dataframe_bytes(frame.copy())
    digest = put_dataframe(tmp_path, frame)
    restored = pd.read_parquet(blob_path(tmp_path, digest))
    pd.testing.assert_frame_equal(restored, frame)


@pytest.mark.parametrize(
    "frame",
    [
        pd.DataFrame(
            {"value": [1, 2]},
            index=pd.DatetimeIndex(
                ["2026-08-27T01:00:00Z", "2026-08-28T01:00:00Z"],
                name="observed_at",
            ),
        ),
        pd.DataFrame(
            {"value": [1, 2]},
            index=pd.MultiIndex.from_arrays(
                [["600000.SH", "000001.SZ"], [1, 2]],
                names=["ts_code", "rank"],
            ),
        ),
    ],
)
def test_dataframe_parquet_preserves_named_index_cross_process(tmp_path, frame):
    local = dataframe_bytes(frame)
    with get_context("spawn").Pool(1) as pool:
        remote = pool.apply(_dataframe_bytes_process, (frame,))

    assert remote == local
    digest = put_dataframe(tmp_path, frame)
    restored = pd.read_parquet(blob_path(tmp_path, digest))
    pd.testing.assert_frame_equal(restored, frame)


def test_blob_creation_fsyncs_every_new_directory_parent(tmp_path, monkeypatch):
    import autoresearch.trace.blobs as blobs

    synced = []
    real_fsync = blobs._fsync_directory

    def record(path):
        synced.append(Path(path))
        real_fsync(path)

    monkeypatch.setattr(blobs, "_fsync_directory", record)
    put_bytes(tmp_path, b"durable")

    assert tmp_path in synced
    assert tmp_path / "blobs" in synced
    assert tmp_path / "blobs/sha256" in synced


def test_blob_directory_fsync_failure_is_explicit(tmp_path, monkeypatch):
    import autoresearch.trace.blobs as blobs

    monkeypatch.setattr(
        blobs,
        "_fsync_directory",
        lambda *_: (_ for _ in ()).throw(OSError("fsync failed")),
    )
    with pytest.raises(OSError, match="fsync failed"):
        put_bytes(tmp_path, b"not claimed durable")


def test_blob_path_rejects_non_digest_and_capsule_root_symlink(tmp_path):
    with pytest.raises(ValueError, match="digest"):
        blob_path(tmp_path, "../escape")
    real = tmp_path / "real"
    real.mkdir()
    linked = tmp_path / "linked"
    linked.symlink_to(real, target_is_directory=True)
    with pytest.raises(ValueError, match="symlink"):
        put_bytes(linked, os.urandom(10))
