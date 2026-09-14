"""Captured source and runtime identity are sufficient for offline replay."""

from __future__ import annotations

import io
import json
import os
import subprocess
import sys
import tarfile
from pathlib import Path

import pytest
import zstandard

from autoresearch.trace.atomic import canonical_json, sha256_bytes
from autoresearch.trace.source_tree import (
    SourceTreeError,
    build_runtime_manifest,
    capture_source_tree,
    check_runtime_availability,
    import_portable_runtime,
    restore_source_tree,
)


def _git(repo: Path, *args: str) -> None:
    subprocess.run(
        ["git", *args],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
    )


def _repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir(parents=True)
    _git(repo, "init", "-q")
    _git(repo, "config", "user.email", "runtime@example.invalid")
    _git(repo, "config", "user.name", "Runtime Test")
    files = {
        "autoresearch/calculator_fixture.py": 'RESULT = "captured"\n',
        "autoresearch/config/calendar.json": '{"open_days":["2026-09-14"]}\n',
        ".claude/skills/stock-research/SKILL.md": "fixture skill\n",
        "pyproject.toml": "[project]\nname='fixture'\nversion='0'\n",
        "uv.lock": "version = 1\n",
        "AGENTS.md": "fixture agents\n",
        "CLAUDE.md": "fixture claude\n",
    }
    for relative, content in files.items():
        path = repo / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
    _git(repo, "add", ".")
    _git(repo, "commit", "-qm", "fixture")
    return repo


def _compressed_tar(rows: list[tuple[str, bytes, str]]) -> bytes:
    raw = io.BytesIO()
    with tarfile.open(fileobj=raw, mode="w", format=tarfile.GNU_FORMAT) as archive:
        for name, payload, kind in rows:
            member = tarfile.TarInfo(name)
            member.mtime = 0
            if kind == "file":
                member.size = len(payload)
                member.mode = 0o644
                archive.addfile(member, io.BytesIO(payload))
            elif kind == "symlink":
                member.type = tarfile.SYMTYPE
                member.linkname = payload.decode("utf-8")
                archive.addfile(member)
            else:  # pragma: no cover - test helper guard
                raise AssertionError(kind)
    return zstandard.ZstdCompressor().compress(raw.getvalue())


def _manifest(rows: list[tuple[str, bytes]]) -> dict:
    files = [
        {
            "path": name,
            "sha256": sha256_bytes(payload),
            "bytes": len(payload),
            "mode": 0o644,
        }
        for name, payload in rows
    ]
    return {
        "schema_version": 1,
        "files": files,
        "file_count": len(files),
        "total_bytes": sum(row["bytes"] for row in files),
        "code_tree_hash": sha256_bytes(canonical_json(files).encode("utf-8")),
    }


def test_replay_uses_captured_tree_not_current_checkout(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    (repo / "autoresearch/dirty.py").write_text('STATE = "dirty"\n', encoding="utf-8")
    output = tmp_path / "identity"

    captured = capture_source_tree(repo, output, environ={})
    (repo / "autoresearch/calculator_fixture.py").write_text(
        'RESULT = "changed"\n', encoding="utf-8"
    )
    restored = tmp_path / "restored"
    replay = restore_source_tree(
        output / "source_tree.tar.zst",
        restored,
        manifest_path=output / "source_tree_manifest.json",
    )
    command = [
        sys.executable,
        "-c",
        "from autoresearch.calculator_fixture import RESULT; print(RESULT)",
    ]
    value = subprocess.run(
        command,
        cwd=restored,
        check=True,
        capture_output=True,
        text=True,
        env={**os.environ, "PYTHONPATH": str(restored)},
    ).stdout.strip()

    assert value == "captured"
    assert (restored / "autoresearch/dirty.py").is_file()
    assert replay["code_tree_hash"] == captured["code_tree_hash"]
    assert replay["uses_current_checkout"] is False


def test_capture_is_deterministic_and_includes_code_resources(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    first = tmp_path / "first"
    second = tmp_path / "second"

    one = capture_source_tree(repo, first, environ={})
    two = capture_source_tree(repo, second, environ={})

    assert one == two
    assert (first / "source_tree.tar.zst").read_bytes() == (
        second / "source_tree.tar.zst"
    ).read_bytes()
    paths = {row["path"] for row in one["files"]}
    assert "autoresearch/calculator_fixture.py" in paths
    assert "autoresearch/config/calendar.json" in paths
    assert "uv.lock" in paths


@pytest.mark.parametrize("unsafe_name", ["../escape.py", "/absolute.py", "autoresearch/../x"])
def test_restore_rejects_path_traversal_before_writing(tmp_path: Path, unsafe_name: str) -> None:
    payload = b"unsafe\n"
    bundle = tmp_path / "malicious.tar.zst"
    bundle.write_bytes(_compressed_tar([(unsafe_name, payload, "file")]))
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text(canonical_json(_manifest([(unsafe_name, payload)])), encoding="utf-8")
    target = tmp_path / "target"

    with pytest.raises(SourceTreeError, match="unsafe source tree member"):
        restore_source_tree(bundle, target, manifest_path=manifest_path)

    assert not target.exists()
    assert not (tmp_path / "escape.py").exists()


def test_restore_rejects_duplicate_or_non_regular_members(tmp_path: Path) -> None:
    duplicate = tmp_path / "duplicate.tar.zst"
    duplicate.write_bytes(
        _compressed_tar(
            [
                ("autoresearch/a.py", b"one", "file"),
                ("autoresearch/a.py", b"two", "file"),
            ]
        )
    )
    link = tmp_path / "link.tar.zst"
    link.write_bytes(_compressed_tar([("autoresearch/a.py", b"target", "symlink")]))

    with pytest.raises(SourceTreeError, match="duplicate"):
        restore_source_tree(duplicate, tmp_path / "duplicate-target")
    with pytest.raises(SourceTreeError, match="regular files"):
        restore_source_tree(link, tmp_path / "link-target")


def test_restore_rejects_missing_or_tampered_source_without_checkout_fallback(
    tmp_path: Path,
) -> None:
    repo = _repo(tmp_path)
    output = tmp_path / "identity"
    capture_source_tree(repo, output, environ={})
    manifest = json.loads((output / "source_tree_manifest.json").read_text(encoding="utf-8"))
    manifest["files"].append(
        {
            "path": "autoresearch/missing.py",
            "sha256": "0" * 64,
            "bytes": 1,
            "mode": 0o644,
        }
    )
    manifest["file_count"] += 1
    manifest["total_bytes"] += 1
    manifest["code_tree_hash"] = sha256_bytes(canonical_json(manifest["files"]).encode("utf-8"))
    bad_manifest = tmp_path / "bad-manifest.json"
    bad_manifest.write_text(canonical_json(manifest), encoding="utf-8")

    with pytest.raises(SourceTreeError, match="membership mismatch"):
        restore_source_tree(
            output / "source_tree.tar.zst",
            tmp_path / "restored",
            manifest_path=bad_manifest,
        )

    assert not (tmp_path / "restored").exists()


def test_capture_rejects_secret_symlink_and_special_files(tmp_path: Path) -> None:
    for case in ("secret", "symlink", "special"):
        repo = _repo(tmp_path / case)
        if case == "secret":
            (repo / "autoresearch/credentials.py").write_text(
                'OPENAI_API_KEY = "sk_live_abcdefghijklmnopqrstuv"\n',
                encoding="utf-8",
            )
        elif case == "symlink":
            (repo / "autoresearch/linked.py").symlink_to("calculator_fixture.py")
        else:
            os.mkfifo(repo / "autoresearch/pipe")
        output = tmp_path / f"identity-{case}"

        with pytest.raises(SourceTreeError):
            capture_source_tree(repo, output, environ={})

        assert not (output / "source_tree.tar.zst").exists()
        assert not (output / "source_tree_manifest.json").exists()


def test_restore_enforces_member_and_total_size_limits(tmp_path: Path) -> None:
    bundle = tmp_path / "large.tar.zst"
    bundle.write_bytes(
        _compressed_tar(
            [
                ("autoresearch/a.py", b"1234", "file"),
                ("autoresearch/b.py", b"5678", "file"),
            ]
        )
    )

    with pytest.raises(SourceTreeError, match="member count"):
        restore_source_tree(bundle, tmp_path / "count", max_members=1)
    with pytest.raises(SourceTreeError, match="member size"):
        restore_source_tree(bundle, tmp_path / "member", max_member_bytes=3)
    with pytest.raises(SourceTreeError, match="total size"):
        restore_source_tree(bundle, tmp_path / "total", max_total_bytes=7)


def test_runtime_manifest_requires_exact_offline_environment(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    dependencies = b"fixture==1.0\n"
    manifest = build_runtime_manifest(repo, dependencies=dependencies)

    matched = check_runtime_availability(manifest, dependencies=dependencies)
    missing = check_runtime_availability(manifest, dependencies=None)
    changed = check_runtime_availability(manifest, dependencies=b"fixture==2.0\n")

    assert matched["status"] == "LOCAL_ENV_MATCHED"
    assert missing == {"status": "UNAVAILABLE", "reason": "DEPENDENCIES_UNAVAILABLE"}
    assert changed == {"status": "UNAVAILABLE", "reason": "DEPENDENCIES_MISMATCH"}
    assert "install" not in canonical_json(missing).lower()


def test_runtime_platform_mismatch_is_unavailable(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    dependencies = b"fixture==1.0\n"
    manifest = build_runtime_manifest(repo, dependencies=dependencies)
    manifest["platform"]["machine"] = "definitely-not-this-machine"

    result = check_runtime_availability(manifest, dependencies=dependencies)

    assert result == {"status": "UNAVAILABLE", "reason": "PLATFORM_MISMATCH"}


def test_portable_runtime_requires_authorized_path_and_exact_digest(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    authorized = tmp_path / "offline-packages"
    authorized.mkdir()
    package = authorized / "python-runtime.tar.zst"
    package.write_bytes(b"offline runtime fixture")
    digest = sha256_bytes(package.read_bytes())
    identity = tmp_path / "identity"

    imported = import_portable_runtime(
        package,
        identity,
        authorized_root=authorized,
        expected_sha256=digest,
    )
    manifest = build_runtime_manifest(
        repo,
        dependencies=b"fixture==1.0\n",
        portable_runtime_sha256=digest,
    )

    assert imported["sha256"] == digest
    assert check_runtime_availability(
        manifest,
        dependencies=b"fixture==2.0\n",
        portable_runtime=identity / imported["artifact"],
    ) == {"status": "PACKAGED", "reason": "VERIFIED_PORTABLE_RUNTIME"}
    with pytest.raises(SourceTreeError, match="authorized"):
        import_portable_runtime(
            repo / "uv.lock",
            tmp_path / "rejected",
            authorized_root=authorized,
            expected_sha256=sha256_bytes((repo / "uv.lock").read_bytes()),
        )
    with pytest.raises(SourceTreeError, match="digest mismatch"):
        import_portable_runtime(
            package,
            tmp_path / "rejected",
            authorized_root=authorized,
            expected_sha256="0" * 64,
        )
