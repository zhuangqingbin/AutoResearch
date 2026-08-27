"""Executable-identity snapshots are deterministic, scoped, and secret-safe."""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest
import zstandard

from autoresearch.trace import identity as identity_mod
from autoresearch.trace.atomic import canonical_json
from autoresearch.trace.identity import redact_value, scan_for_secrets, snapshot_identity


def _git(repo: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", *args],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
    )


def _repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q")
    _git(repo, "config", "user.email", "identity@example.invalid")
    _git(repo, "config", "user.name", "Identity Test")
    for relative, content in {
        "autoresearch/rule.py": "VALUE = 1\n",
        ".claude/agents/analyst.md": "tracked agent\n",
        ".claude/skills/scan-market/SKILL.md": "tracked skill\n",
        ".claude/workflows/scan-market.js": "export const run = true;\n",
        "pyproject.toml": "[project]\nname='fixture'\nversion='0'\n",
        "uv.lock": "version = 1\n",
        "AGENTS.md": "fixture agents\n",
        "CLAUDE.md": "fixture claude\n",
    }.items():
        path = repo / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
    _git(repo, "add", ".")
    _git(repo, "commit", "-qm", "fixture")
    return repo


def _all_artifact_bytes(root: Path) -> bytes:
    return b"\n".join(
        path.read_bytes()
        for path in sorted(root.rglob("*"))
        if path.is_file() and not path.is_symlink()
    )


def _tar_names(path: Path) -> list[str]:
    import io
    import tarfile

    raw = zstandard.ZstdDecompressor().decompress(path.read_bytes())
    with tarfile.open(fileobj=io.BytesIO(raw), mode="r:") as archive:
        return archive.getnames()


def _tar_contents(path: Path) -> dict[str, bytes]:
    import io
    import tarfile

    raw = zstandard.ZstdDecompressor().decompress(path.read_bytes())
    with tarfile.open(fileobj=io.BytesIO(raw), mode="r:") as archive:
        return {member.name: archive.extractfile(member).read() for member in archive.getmembers()}


def test_identity_contains_dirty_patch_untracked_sources_and_environment(tmp_path, monkeypatch):
    repo = _repo(tmp_path)
    (repo / "autoresearch/rule.py").write_text("VALUE = 2\n", encoding="utf-8")
    (repo / "autoresearch/new_rule.py").write_text("NEW = True\n", encoding="utf-8")
    monkeypatch.setenv("TUSHARE_TOKEN", "fixture-secret-value")
    out = tmp_path / "identity"

    result = snapshot_identity(repo, out, engine="codex", model="gpt-test")

    assert result["ok"] is True
    assert (out / "code.patch").read_text(encoding="utf-8")
    assert (out / "untracked_sources.tar.zst").stat().st_size > 0
    manifest = json.loads((out / "source_manifest.json").read_text())
    assert "autoresearch/new_rule.py" in manifest["untracked"]
    environment = json.loads((out / "environment.json").read_text())
    assert environment["engine"] == "codex"
    assert environment["model"] == "gpt-test"
    assert environment["secret_environment"]["TUSHARE_TOKEN"] == {"present": True}
    assert "fixture-secret-value" not in canonical_json(environment)
    assert (out / "dependencies.txt").is_file()


def test_untracked_tar_and_prompt_snapshot_are_byte_deterministic(tmp_path):
    repo = _repo(tmp_path)
    executable = repo / "autoresearch/new_tool.py"
    executable.write_text("#!/usr/bin/env python3\n", encoding="utf-8")
    executable.chmod(0o755)
    untracked_prompt = repo / ".claude/skills/new/SKILL.md"
    untracked_prompt.parent.mkdir(parents=True)
    untracked_prompt.write_text("new skill\n", encoding="utf-8")

    first = tmp_path / "first"
    second = tmp_path / "second"
    assert snapshot_identity(repo, first, engine="codex")["ok"] is True
    assert snapshot_identity(repo, second, engine="codex")["ok"] is True

    assert (first / "untracked_sources.tar.zst").read_bytes() == (
        second / "untracked_sources.tar.zst"
    ).read_bytes()
    assert _tar_names(first / "untracked_sources.tar.zst") == [
        ".claude/skills/new/SKILL.md",
        "autoresearch/new_tool.py",
    ]
    assert (first / "prompts/skills/new/SKILL.md").read_bytes() == b"new skill\n"
    first_manifest = json.loads((first / "source_manifest.json").read_text())
    prompt_rows = {row["source"]: row for row in first_manifest["prompts"]}
    assert prompt_rows[".claude/agents/analyst.md"]["classification"] == "TRACKED"
    assert prompt_rows[".claude/skills/new/SKILL.md"]["classification"] == "UNTRACKED"


def test_real_repository_safe_prompt_corpus_snapshots_completely(tmp_path):
    repo = Path(__file__).resolve().parents[2]
    out = tmp_path / "identity"

    result = snapshot_identity(repo, out, engine="codex")

    assert result["components"]["prompts"]["status"] == "SUCCESS"
    expected = {
        path.relative_to(repo).as_posix()
        for root in (".claude/agents", ".claude/skills", ".claude/workflows")
        for path in (repo / root).rglob("*")
        if path.is_file() and not path.is_symlink()
    }
    manifest = json.loads((out / "source_manifest.json").read_text(encoding="utf-8"))
    represented = {row["source"] for row in manifest["prompts"]}
    assert represented == expected


def test_snapshot_excludes_out_of_scope_symlinks_and_special_files(tmp_path):
    repo = _repo(tmp_path)
    outside = tmp_path / "outside.py"
    outside.write_text("DO_NOT_COPY = True\n", encoding="utf-8")
    (repo / "autoresearch/escape.py").symlink_to(outside)
    (repo / ".claude/skills/escape.md").symlink_to(outside)
    (repo / ".env").write_text("PASSWORD=do-not-copy\n", encoding="utf-8")
    (repo / "lake").mkdir()
    (repo / "lake/raw.bin").write_bytes(b"do-not-copy")
    if hasattr(os, "mkfifo"):
        os.mkfifo(repo / "autoresearch/special.pipe")

    out = tmp_path / "identity"
    result = snapshot_identity(repo, out, engine="codex")

    assert result["ok"] is True
    names = _tar_names(out / "untracked_sources.tar.zst")
    assert names == []
    combined = _all_artifact_bytes(out)
    assert b"DO_NOT_COPY" not in combined
    assert b"do-not-copy" not in combined
    assert not (out / "prompts/skills/escape.md").exists()


def test_snapshot_rejects_symlinked_output_parent_without_outside_writes(tmp_path):
    repo = _repo(tmp_path)
    outside = tmp_path / "outside-output"
    outside.mkdir()
    link = tmp_path / "linked-output"
    link.symlink_to(outside, target_is_directory=True)

    with pytest.raises(ValueError, match="symlink"):
        snapshot_identity(repo, link / "identity", engine="codex")

    assert list(outside.iterdir()) == []


def test_redact_value_recurses_and_removes_known_embedded_secret(monkeypatch):
    secret = "sk-live-abcdefghijklmnopqrstuvwxyz123456"
    monkeypatch.setenv("TUSHARE_TOKEN", secret)
    payload = {
        "authorization": f"Bearer {secret}",
        "nested": [{"api_key": secret}, f"prefix::{secret}::suffix"],
        "safe": "public",
    }

    result = redact_value(payload)

    rendered = canonical_json(result.value)
    assert secret not in rendered
    assert "Bearer" not in rendered
    assert result.hits >= 3
    assert result.value["safe"] == "public"
    assert scan_for_secrets(rendered.encode())["ok"] is True


@pytest.mark.parametrize(
    "prefix,numeric_segments",
    [
        ("xoxb", 2),
        ("xoxp", 3),
        ("xoxa", 2),
        ("xoxr", 2),
    ],
)
def test_slack_tokens_are_detected_without_returning_the_value(prefix, numeric_segments):
    token = "-".join([prefix, *(["123456789012"] * numeric_segments), "abcdefghijklmnopqrstuvwx"])

    result = scan_for_secrets(token.encode())

    assert result["ok"] is False
    assert result["findings"] == [{"kind": "slack_token", "offset": 0, "length": len(token)}]
    assert token not in canonical_json(result)


def test_redact_value_removes_slack_token_under_arbitrary_safe_key():
    token = "xoxb-123456789012-123456789012-abcdefghijklmnopqrstuvwx"

    result = redact_value({"comment": f"diagnostic::{token}::end"}, environ={})

    assert result.hits == 1
    assert token not in canonical_json(result.value)
    assert result.value == {"comment": "diagnostic::[REDACTED]::end"}


@pytest.mark.parametrize(
    "payload,ok",
    [
        (b"Authorization: Bearer abcdefghijklmnopqrstuvwxyz123456", False),
        (b"api_key=sk-live-abcdefghijklmnopqrstuvwxyz123456", False),
        (b"opaque=QWxhZGRpbjpvcGVuIHNlc2FtZTIwMjYwODI3IQ==", False),
        (("sha256=" + "a" * 64).encode(), True),
        (b"run_id=20260827T010203456789Z", True),
        (b"session_ref=01a03dbe-7173-76a3-ac96-919ae6936e71", True),
        (b"contract_hash=0123456789abcdef" * 4, True),
    ],
)
def test_secret_scanner_has_useful_high_entropy_boundaries(payload, ok):
    result = scan_for_secrets(payload)
    assert result["ok"] is ok
    assert result["hits"] == len(result["findings"])
    if not ok:
        assert all("value" not in finding for finding in result["findings"])


@pytest.mark.parametrize(
    "payload",
    [
        b"opaque=M7pQ2xV9nK4rT8wL6cD3sF1hJ5uB0yE7aG9mN2qR",
        (
            b"jwt=eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9."
            b"eyJzdWIiOiIxMjM0NTY3ODkwIiwibmFtZSI6IkphbmUgRG9lIn0."
            b"SflKxwRJSMeKKF2QT4fwpMeJf36POk6yJV_adQssw5c"
        ),
    ],
)
def test_secret_scanner_detects_random_and_jwt_material(payload):
    result = scan_for_secrets(payload)
    assert result["ok"] is False
    assert result["hits"] >= 1


@pytest.mark.parametrize(
    "payload",
    [
        b"context_codex/scan_runs/20260827T010203456789Z/staging/2026-08-27",
        b"gap_c1_o2_reversal_candidate_column_name",
        b"scan-market-forensic-run-capsule-source-manifest",
        b"xoxb-token-format-documentation",
        b"prefix-xoxp-short-slug",
    ],
)
def test_secret_scanner_ignores_paths_columns_and_slugs(payload):
    assert scan_for_secrets(payload)["ok"] is True


def test_secret_in_dirty_patch_is_not_persisted_and_marks_component_missing(tmp_path, monkeypatch):
    repo = _repo(tmp_path)
    secret = "plain-secret-value"
    monkeypatch.setenv("OPENAI_API_KEY", secret)
    (repo / "autoresearch/rule.py").write_text(f'VALUE = "{secret}"\n', encoding="utf-8")
    out = tmp_path / "identity"

    result = snapshot_identity(repo, out, engine="codex")

    assert result["ok"] is False
    assert result["components"]["git_patch"]["status"] == "MISSING"
    assert not (out / "code.patch").exists()
    assert secret.encode() not in _all_artifact_bytes(out)
    assert result["missing"] == ["git_patch"]


def test_secret_prompt_and_untracked_source_are_never_archived(tmp_path, monkeypatch):
    repo = _repo(tmp_path)
    secret = "plain-secret-value"
    monkeypatch.setenv("OPENAI_API_KEY", secret)
    prompt = repo / ".claude/skills/scan-market/SECRET.md"
    prompt.write_text(f"use {secret}\n", encoding="utf-8")
    source = repo / "autoresearch/new_secret.py"
    source.write_text(f'SECRET = "{secret}"\n', encoding="utf-8")
    out = tmp_path / "identity"

    result = snapshot_identity(repo, out, engine="codex")

    assert result["ok"] is False
    assert result["components"]["prompts"]["status"] == "PARTIAL"
    assert result["components"]["untracked_sources"]["status"] == "PARTIAL"
    assert not (out / "prompts/skills/scan-market/SECRET.md").exists()
    assert _tar_names(out / "untracked_sources.tar.zst") == []
    assert secret.encode() not in _all_artifact_bytes(out)


def test_slack_token_in_prompt_and_untracked_source_is_never_persisted(tmp_path):
    repo = _repo(tmp_path)
    token = "xoxb-123456789012-123456789012-abcdefghijklmnopqrstuvwx"
    prompt = repo / ".claude/skills/new/SLACK.md"
    prompt.parent.mkdir(parents=True)
    prompt.write_text(f"diagnostic channel: {token}\n", encoding="utf-8")
    source = repo / "autoresearch/slack_fixture.py"
    source.write_text(f'COMMENT = "diagnostic::{token}"\n', encoding="utf-8")
    out = tmp_path / "identity"

    result = snapshot_identity(repo, out, engine="codex")

    assert result["ok"] is False
    assert result["components"]["prompts"]["status"] == "PARTIAL"
    assert result["components"]["untracked_sources"]["status"] == "PARTIAL"
    assert not (out / "prompts/skills/new/SLACK.md").exists()
    assert _tar_names(out / "untracked_sources.tar.zst") == []
    assert token.encode() not in _all_artifact_bytes(out)


def test_secret_value_in_source_path_is_not_written_to_tar_or_manifest(tmp_path, monkeypatch):
    repo = _repo(tmp_path)
    secret = "plain-secret-value"
    monkeypatch.setenv("OPENAI_API_KEY", secret)
    (repo / "autoresearch" / f"{secret}.py").write_text("SAFE = True\n", encoding="utf-8")
    out = tmp_path / "identity"

    result = snapshot_identity(repo, out, engine="codex")

    assert result["ok"] is False
    assert result["components"]["untracked_sources"]["status"] == "PARTIAL"
    assert _tar_names(out / "untracked_sources.tar.zst") == []
    assert secret.encode() not in _all_artifact_bytes(out)


def test_partial_untracked_archive_keeps_all_safe_members_deterministically(tmp_path, monkeypatch):
    repo = _repo(tmp_path)
    secret = "plain-secret-value"
    monkeypatch.setenv("OPENAI_API_KEY", secret)
    (repo / "autoresearch/a_safe.py").write_bytes(b"A = 1\n")
    (repo / "autoresearch/b_safe.py").write_bytes(b"B = 2\n")
    (repo / "autoresearch/blocked.py").write_text(f'VALUE = "{secret}"\n', encoding="utf-8")
    first = tmp_path / "identity-first"
    second = tmp_path / "identity-second"

    first_result = snapshot_identity(repo, first, engine="codex")
    second_result = snapshot_identity(repo, second, engine="codex")

    archive = first / "untracked_sources.tar.zst"
    assert archive.read_bytes() == (second / archive.name).read_bytes()
    assert _tar_contents(archive) == {
        "autoresearch/a_safe.py": b"A = 1\n",
        "autoresearch/b_safe.py": b"B = 2\n",
    }
    component = first_result["components"]["untracked_sources"]
    assert component["status"] == "PARTIAL"
    assert component["artifacts"] == ["untracked_sources.tar.zst"]
    assert any("autoresearch/blocked.py" in item for item in component["missing"])
    manifest = json.loads((first / "source_manifest.json").read_text(encoding="utf-8"))
    assert manifest["untracked_included"] == [
        "autoresearch/a_safe.py",
        "autoresearch/b_safe.py",
    ]
    assert [row["path"] for row in manifest["untracked_rejected"]] == ["autoresearch/blocked.py"]
    assert second_result["components"]["untracked_sources"]["status"] == "PARTIAL"


def test_partial_untracked_archive_records_unreadable_member_and_keeps_safe_one(
    tmp_path, monkeypatch
):
    repo = _repo(tmp_path)
    safe = repo / "autoresearch/safe.py"
    unreadable = repo / "autoresearch/unreadable.py"
    safe.write_bytes(b"SAFE = 1\n")
    unreadable.write_bytes(b"UNREADABLE = 1\n")
    original = identity_mod._read_regular

    def fail_one(root, relative):
        if relative.as_posix() == "autoresearch/unreadable.py":
            raise PermissionError("permission denied")
        return original(root, relative)

    monkeypatch.setattr(identity_mod, "_read_regular", fail_one)
    out = tmp_path / "identity"

    result = snapshot_identity(repo, out, engine="codex")

    component = result["components"]["untracked_sources"]
    assert component["status"] == "PARTIAL"
    assert _tar_contents(out / "untracked_sources.tar.zst") == {
        "autoresearch/safe.py": b"SAFE = 1\n"
    }
    assert any("autoresearch/unreadable.py" in item for item in component["missing"])
