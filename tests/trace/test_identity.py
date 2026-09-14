"""Executable-identity snapshots are deterministic, scoped, and secret-safe."""

from __future__ import annotations

import json
import os
import subprocess
import warnings
from concurrent.futures import ThreadPoolExecutor
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
    source_tree = json.loads((out / "source_tree_manifest.json").read_text())
    source_rows = {row["path"]: row for row in source_tree["files"]}
    assert source_rows["autoresearch/rule.py"]["classification"] == "TRACKED_DIRTY"
    assert source_rows["autoresearch/new_rule.py"]["classification"] == "UNTRACKED"
    assert (out / "source_tree.tar.zst").is_file()
    runtime = json.loads((out / "runtime_manifest.json").read_text())
    assert runtime["availability"]["status"] == "LOCAL_ENV_MATCHED"
    assert runtime["network_install_allowed"] is False


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

    result = snapshot_identity(repo, out, engine="codex", environ={})

    assert result["components"]["prompts"]["status"] == "SUCCESS"
    assert result["components"]["source_tree"]["status"] == "SUCCESS"
    expected = {
        path.relative_to(repo).as_posix()
        for root in (".claude/agents", ".claude/skills", ".claude/workflows")
        for path in (repo / root).rglob("*")
        if path.is_file() and not path.is_symlink()
    }
    manifest = json.loads((out / "source_manifest.json").read_text(encoding="utf-8"))
    represented = {row["source"] for row in manifest["prompts"]}
    assert represented == expected


def test_real_repository_has_no_generic_literal_assignment_false_positives():
    repo = Path(__file__).resolve().parents[2]
    offenders = []
    for relative_root in (
        Path("autoresearch"),
        Path(".claude/agents"),
        Path(".claude/skills"),
        Path(".claude/workflows"),
    ):
        for path in sorted((repo / relative_root).rglob("*")):
            if not path.is_file() or path.is_symlink():
                continue
            findings = scan_for_secrets(path.read_bytes(), environ={})["findings"]
            if any(
                finding["kind"] in {"secret_assignment", "contextual_credential"}
                for finding in findings
            ):
                offenders.append(path.relative_to(repo).as_posix())

    assert offenders == []


def test_unrelated_dirty_runtime_expression_preserves_code_patch(tmp_path):
    repo = _repo(tmp_path)
    (repo / "autoresearch/rule.py").write_text(
        'VALUE = os.environ["PUBLIC_VALUE"]\n', encoding="utf-8"
    )
    out = tmp_path / "identity"

    result = snapshot_identity(repo, out, engine="codex", environ={})

    assert result["components"]["git_patch"]["status"] == "SUCCESS"
    assert b'os.environ["PUBLIC_VALUE"]' in (out / "code.patch").read_bytes()


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

    assert result["ok"] is False
    assert result["components"]["prompts"]["status"] == "PARTIAL"
    assert result["components"]["untracked_sources"]["status"] == "PARTIAL"
    names = _tar_names(out / "untracked_sources.tar.zst")
    assert names == []
    manifest = json.loads((out / "source_manifest.json").read_text(encoding="utf-8"))
    assert [row["path"] for row in manifest["prompt_rejected"]] == [".claude/skills/escape.md"]
    assert [row["path"] for row in manifest["untracked_rejected"]] == [
        ".claude/skills/escape.md",
        "autoresearch/escape.py",
        "autoresearch/special.pipe",
    ]
    combined = _all_artifact_bytes(out)
    assert b"DO_NOT_COPY" not in combined
    assert b"do-not-copy" not in combined
    assert not (out / "prompts/skills/escape.md").exists()


def test_safe_in_repo_prompt_symlink_captures_target_and_exact_bytes(tmp_path):
    repo = _repo(tmp_path)
    link = repo / ".claude/skills/linked.md"
    link.symlink_to("../agents/analyst.md")
    out = tmp_path / "identity"

    result = snapshot_identity(repo, out, engine="codex")

    assert result["components"]["prompts"]["status"] == "SUCCESS"
    assert (out / "prompts/skills/linked.md").read_bytes() == b"tracked agent\n"
    manifest = json.loads((out / "source_manifest.json").read_text(encoding="utf-8"))
    row = next(row for row in manifest["prompts"] if row["source"].endswith("linked.md"))
    assert row["link_target"] == "../agents/analyst.md"
    assert bytes.fromhex(row["link_target_hex"]) == b"../agents/analyst.md"
    assert row["resolved_source"] == ".claude/agents/analyst.md"
    assert bytes.fromhex(row["resolved_source_hex"]) == b".claude/agents/analyst.md"
    assert row["sha256"] == row["resolved_sha256"]


def test_prompt_parent_replacement_with_symlink_is_partial_and_never_followed(
    tmp_path, monkeypatch
):
    repo = _repo(tmp_path)
    relative = Path(".claude/skills/scan-market/SKILL.md")
    source_parent = repo / relative.parent
    held_parent = repo / ".claude/skills/held"
    outside_parent = tmp_path / "outside-skills"
    outside_parent.mkdir()
    (outside_parent / "SKILL.md").write_bytes(b"OUTSIDE_BYTES\n")
    original = identity_mod._read_regular
    replaced = False

    def replace_parent(root, item):
        nonlocal replaced
        if item == relative and not replaced:
            source_parent.rename(held_parent)
            source_parent.symlink_to(outside_parent, target_is_directory=True)
            replaced = True
        return original(root, item)

    monkeypatch.setattr(identity_mod, "_read_regular", replace_parent)
    out = tmp_path / "identity"

    result = snapshot_identity(repo, out, engine="codex", environ={})

    assert result["ok"] is False
    assert result["components"]["prompts"]["status"] == "PARTIAL"
    assert not (out / "prompts/skills/scan-market/SKILL.md").exists()
    assert b"OUTSIDE_BYTES" not in _all_artifact_bytes(out)


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


def test_uri_private_key_and_provider_credentials_are_detected_and_redacted():
    candidates = {
        "uri_userinfo": "postgresql://runner:correct-horse-battery@db.internal:5432/app",
        "private_key": (
            "-----BEGIN PRIVATE KEY-----\n"
            "MIIEvQIBADANBgkqhkiG9w0BAQEFAASCBKcwggSjAgEAAoIBAQC7\n"
            "-----END PRIVATE KEY-----"
        ),
        "github": "ghp_ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghij",
        "gitlab": "glpat-AbCdEfGhIjKlMnOpQrSt",
        "google": "AIzaSyA1234567890bcdefghijklmnopqrstuv",
        "stripe": "sk_live_abcdefghijklmnopqrstuvwx",
        "twilio": "SK0123456789abcdef0123456789abcdef",
        "sendgrid": "SG.abcdefghijklmnopqrstuv.ABCDEFGHIJKLMNOPQRSTUVWXYZabcdef",
        "npm": "npm_ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghij",
        "contextual_dsn": "analytics_dsn=host=db.internal",
        "driver_uri": "mssql+pyodbc://runner:correct-horse@db.internal/app",
        "non_db_uri": "amqps://worker:correct-horse@mq.internal/vhost",
    }

    for kind, candidate in candidates.items():
        scan = scan_for_secrets(candidate.encode(), environ={})
        assert scan["ok"] is False, kind
        assert all("value" not in finding for finding in scan["findings"])
        redaction = redact_value({"comment": candidate}, environ={})
        assert candidate not in canonical_json(redaction.value)
        assert redaction.hits >= 1


def test_uri_credentials_with_empty_username_are_detected_and_redacted():
    connection = "mssql+pyodbc://:correct-horse@service.internal/app"

    scan = scan_for_secrets(connection.encode(), environ={})
    redaction = redact_value({"comment": connection}, environ={})

    assert scan["ok"] is False
    assert scan["findings"] == [{"kind": "uri_userinfo", "offset": 0, "length": len(connection)}]
    assert connection not in canonical_json(scan)
    assert connection not in canonical_json(redaction.value)
    assert redaction.hits == 1


@pytest.mark.parametrize(
    "payload",
    [
        b"https://example.com/public/docs?q=identity",
        b"ftp://anonymous@example.com/public/file.txt",
        b"postgresql://db.internal:5432/public",
        b"redis://cache.internal:6379/0",
        b"PEM-private-key-format-documentation",
        b"provider-token-detection-source-text",
    ],
)
def test_secret_scanner_allows_public_urls_and_source_text(payload):
    assert scan_for_secrets(payload, environ={})["ok"] is True


@pytest.mark.parametrize(
    "payload",
    [
        b'api_key = os.getenv("PROVIDER_API_KEY")',
        b'api_key = os.environ["PROVIDER_API_KEY"]',
        b'api_key = getenv("PROVIDER_API_KEY")',
        b"api_key = get_api_key()",
        b"password = getpass.getpass()",
        b"credentials = load_credentials()",
        b"credentials = provider.credentials",
        b'client_secret = vault.read("service")',
        b"database_url = settings.database_url",
        b"database_url = config.database_url",
        b"api_key = None",
        b"token = null",
        b"password = False",
        b'api_key = "YOUR_API_KEY"',
        b"token = <TOKEN>",
        b"secret = ${SECRET}",
        b"password = CHANGEME",
        b'# api_key = "literal-credential-value"',
        b'// token = "literal-credential-value"',
    ],
    ids=(
        "os-getenv",
        "os-environ",
        "getenv",
        "getter-call",
        "getpass-call",
        "loader-call",
        "provider-attribute",
        "vault-call",
        "settings-attribute",
        "config-attribute",
        "none",
        "null",
        "false",
        "quoted-placeholder",
        "angle-placeholder",
        "environment-placeholder",
        "changeme-placeholder",
        "python-comment",
        "slash-comment",
    ),
)
def test_secret_assignment_scanner_allows_runtime_expressions(payload):
    assert scan_for_secrets(payload, environ={})["ok"] is True
    assert redact_value({"source": payload.decode()}, environ={}).hits == 0


@pytest.mark.parametrize(
    "payload",
    [
        b"api_key: str = provider_api_key",
        b"api_key = settings.database_url",
        b"api_key = get_api_key()",
        b'client_secret = vault.read("service")',
        b'api_key = os.getenv("PROVIDER_API_KEY")  # runtime only',
        b'api_key = os.environ.get("PROVIDER_API_KEY")',
    ],
    ids=(
        "annotated-name",
        "attribute",
        "getter-call",
        "vault-call",
        "getenv-no-default",
        "environ-get-no-default",
    ),
)
def test_structural_assignment_scanner_allows_runtime_only_rhs(payload):
    assert scan_for_secrets(payload, environ={})["ok"] is True


@pytest.mark.parametrize(
    "payload",
    [
        b'api_key = "literal-credential-value"',
        b"api_key = literal-credential-value",
        b'credentials = "literal-credential-value"',
        b'database_url = "driver://runner:password@service.internal/app"',
    ],
    ids=("quoted-secret", "bare-secret", "quoted-credential", "credential-uri"),
)
def test_secret_assignment_scanner_rejects_literal_credentials(payload):
    assert scan_for_secrets(payload, environ={})["ok"] is False
    redacted = redact_value({"source": payload.decode()}, environ={})
    assert redacted.hits >= 1
    assert payload.decode() not in canonical_json(redacted.value)


@pytest.mark.parametrize(
    "payload",
    [
        b'api_key: str = "literal-credential-value"',
        b'api_key = f"literal-{suffix}"',
        b'credentials = {"primary": ["literal-credential-value"]}',
        b'api_key = "literal-" + "credential-value"',
        b'api_key = os.getenv("PROVIDER_API_KEY", "literal-credential-value")',
        b'{"api_key": {"fallback": "literal-credential-value"}}',
        b"api_key: literal-credential-value",
        b'+api_key = ["literal-credential-value"]',
    ],
    ids=(
        "annotated",
        "fstring",
        "container",
        "concatenated",
        "getenv-default",
        "json",
        "yaml-bare",
        "diff-added-container",
    ),
)
def test_structural_assignment_scanner_rejects_recursive_literal_rhs(payload):
    result = scan_for_secrets(payload, environ={})

    assert result["ok"] is False
    assert any(
        finding["kind"] in {"secret_assignment", "contextual_credential"}
        for finding in result["findings"]
    )
    assert all("value" not in finding for finding in result["findings"])


def test_dirty_patch_rejects_structural_literals_but_keeps_runtime_only_rhs(tmp_path):
    literal_root = tmp_path / "literal"
    literal_root.mkdir()
    literal_repo = _repo(literal_root)
    (literal_repo / "autoresearch/rule.py").write_text(
        "\n".join(
            (
                'api_key: str = "literal-credential-value"',
                'fallback = f"literal-{suffix}"',
                'credentials = {"primary": ["literal-credential-value"]}',
                'client_secret = os.getenv("CLIENT_SECRET", "literal-credential-value")',
            )
        )
        + "\n",
        encoding="utf-8",
    )
    literal_out = tmp_path / "literal-identity"

    rejected = snapshot_identity(literal_repo, literal_out, engine="codex", environ={})

    assert rejected["components"]["git_patch"]["status"] == "MISSING"
    assert not (literal_out / "code.patch").exists()

    runtime_root = tmp_path / "runtime"
    runtime_root.mkdir()
    runtime_repo = _repo(runtime_root)
    (runtime_repo / "autoresearch/rule.py").write_text(
        "\n".join(
            (
                "api_key: str = provider_api_key",
                "credentials = provider.credentials",
                "database_url = settings.database_url",
                "client_secret = get_api_key()",
                'password = os.getenv("SERVICE_PASSWORD")  # lookup key only',
            )
        )
        + "\n",
        encoding="utf-8",
    )
    runtime_out = tmp_path / "runtime-identity"

    kept = snapshot_identity(runtime_repo, runtime_out, engine="codex", environ={})

    assert kept["components"]["git_patch"]["status"] == "SUCCESS"
    assert (runtime_out / "code.patch").is_file()


def test_dirty_patch_scans_complete_multiline_worktree_and_head_blobs(tmp_path):
    added_root = tmp_path / "added"
    added_root.mkdir()
    added_repo = _repo(added_root)
    (added_repo / "autoresearch/rule.py").write_text(
        """api_key: str = (
    "literal-"
    "credential-value"
)
credentials = {
    "primary": [
        "literal-credential-value",
    ],
}
client_secret = os.getenv(
    "CLIENT_SECRET",
    "literal-credential-value",
)
""",
        encoding="utf-8",
    )
    added_out = tmp_path / "added-identity"

    added = snapshot_identity(added_repo, added_out, engine="codex", environ={})

    assert added["components"]["git_patch"]["status"] == "MISSING"
    assert not (added_out / "code.patch").exists()

    removed_root = tmp_path / "removed"
    removed_root.mkdir()
    removed_repo = _repo(removed_root)
    tracked = removed_repo / "autoresearch/removed_secret.py"
    tracked.write_text(
        """api_key: str = (
    "literal-"
    "credential-value"
)
""",
        encoding="utf-8",
    )
    _git(removed_repo, "add", "autoresearch/removed_secret.py")
    _git(removed_repo, "commit", "-qm", "credential fixture")
    tracked.write_text("api_key = provider_api_key\n", encoding="utf-8")
    removed_out = tmp_path / "removed-identity"

    removed = snapshot_identity(removed_repo, removed_out, engine="codex", environ={})

    assert removed["components"]["git_patch"]["status"] == "MISSING"
    assert not (removed_out / "code.patch").exists()


def test_redact_value_sanitizes_credentials_in_dict_keys_without_losing_collisions():
    first = "ghp_ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghij"
    second = "npm_ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghij"
    known = "database-password-value"
    payload = {
        f"header::{first}": "first",
        f"header::{second}": "second",
        "header::[REDACTED]": "literal",
        f"prefix::{known}": "third",
        "authorization": "plain credential value",
    }

    result = redact_value(payload, environ={"DATABASE_URL": known})
    rendered = canonical_json(result.value)

    assert first not in rendered
    assert second not in rendered
    assert known not in rendered
    assert result.value["authorization"] == "[REDACTED]"
    assert len(result.value) == len(payload)
    assert scan_for_secrets(rendered.encode(), environ={"DATABASE_URL": known})["ok"] is True


def test_database_env_is_presence_only_and_known_value_never_persists(tmp_path):
    repo = _repo(tmp_path)
    connection = "postgresql://runner:correct-horse-battery@db.internal:5432/app"
    out = tmp_path / "identity"

    result = snapshot_identity(
        repo,
        out,
        engine="codex",
        environ={"DATABASE_URL": connection, "PUBLIC_URL": "https://example.com"},
    )

    assert result["components"]["environment"]["status"] == "SUCCESS"
    environment = json.loads((out / "environment.json").read_text(encoding="utf-8"))
    assert environment["secret_environment"]["DATABASE_URL"] == {"present": True}
    assert "PUBLIC_URL" not in environment["secret_environment"]
    assert connection.encode() not in _all_artifact_bytes(out)


def test_empty_username_uri_is_rejected_from_patch_prompt_env_and_untracked(tmp_path):
    repo = _repo(tmp_path)
    connection = "custom+driver://:correct-horse@service.internal/app"
    (repo / "autoresearch/rule.py").write_text(f'CONNECTION = "{connection}"\n', encoding="utf-8")
    (repo / ".claude/agents/analyst.md").write_text(
        f"connection note: {connection}\n", encoding="utf-8"
    )
    source = repo / "autoresearch/new_connection.py"
    source.write_text(f'VALUE = "{connection}"\n', encoding="utf-8")
    out = tmp_path / "identity"

    result = snapshot_identity(
        repo,
        out,
        engine="codex",
        environ={"DATABASE_URL": connection},
    )

    assert result["components"]["git_patch"]["status"] == "MISSING"
    assert result["components"]["prompts"]["status"] == "PARTIAL"
    assert result["components"]["untracked_sources"]["status"] == "PARTIAL"
    assert result["components"]["environment"]["status"] == "SUCCESS"
    assert not (out / "code.patch").exists()
    assert not (out / "prompts/agents/analyst.md").exists()
    assert _tar_names(out / "untracked_sources.tar.zst") == []
    environment = json.loads((out / "environment.json").read_text(encoding="utf-8"))
    assert environment["secret_environment"]["DATABASE_URL"] == {"present": True}
    assert connection.encode() not in _all_artifact_bytes(out)


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


@pytest.mark.parametrize(
    "payload",
    [
        b"UPPERCASE_SCHEMA_CONSTANT_WITH_UNDERSCORES",
        b"ordinary_long_identifier_for_schema_column",
        b"LongDescriptiveSchemaIdentifierWithoutDigits",
    ],
)
def test_secret_scanner_ignores_long_source_identifiers(payload):
    assert scan_for_secrets(payload, environ={})["ok"] is True


@pytest.mark.parametrize(
    "payload",
    [
        b"schema_version=DISSENT_SCHEMA_VERSION",
        b"current_schema=ordinary_lowercase_schema_identifier",
        b"EXPECTED_SCHEMA=UPPERCASE_SCHEMA_CONSTANT_WITH_UNDERSCORES",
    ],
    ids=("equals-boundary", "lower-identifier", "upper-constant"),
)
def test_opaque_scanner_splits_assignments_from_source_identifiers(payload):
    assert scan_for_secrets(payload, environ={})["ok"] is True


@pytest.mark.parametrize(
    "payload",
    [
        b'api_key = f"{prefix}-{suffix}"',
        b'api_key = prefix + "-" + suffix',
        b'api_key = prefix + "_/" + suffix',
    ],
    ids=("fstring-separator", "concat-hyphen", "concat-punctuation"),
)
def test_runtime_formatting_with_separator_literals_is_not_a_credential(payload):
    assert scan_for_secrets(payload, environ={})["ok"] is True


def test_speculative_python_parse_stays_silent_on_user_text():
    """脱敏对任意文本做投机 `ast.parse`,它的语法牢骚不许泄到调用方。

    这些行**能编译**(所以 except SyntaxError 接不住),只是转义可疑 —— 而它们是被脱敏的
    用户文本(grep 的正则、Windows 路径),不是本仓代码。2026-09-04 实测:不消音时一次
    `usage_panorama` 往 stderr 泼 5456 行 `<unknown>:1: SyntaxWarning`,把真读数冲没。
    """
    noisy = {
        "cmd": 'pattern = "a\\|b"',           # grep 交替:`\|`
        "path": 'root = "C:\\Users\\x"',      # Windows 路径:`\U`
        "re": 'spacing = "\\s+\\d*"',         # 空白/数字类:`\s` `\d`(变量名中性,不该被脱敏)
    }

    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        redacted = redact_value(noisy).value

    assert [w for w in caught if issubclass(w.category, SyntaxWarning)] == []
    # 消音不等于跳过扫描:内容仍旧原样过了脱敏,没有被"整段丢弃"这种偷懒实现替掉。
    assert redacted == noisy


def test_all_literal_formatting_is_still_a_credential():
    payloads = (
        b'api_key = f"literal-credential-value"',
        b'api_key = "literal" + "-" + "credential-value"',
    )

    assert all(scan_for_secrets(payload, environ={})["ok"] is False for payload in payloads)


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
    assert result["missing"] == ["git_patch", "source_tree"]


def test_uri_credentials_in_dirty_patch_are_never_persisted(tmp_path):
    repo = _repo(tmp_path)
    connection = "postgresql://runner:correct-horse-battery@db.internal:5432/app"
    (repo / "autoresearch/rule.py").write_text(f'DATABASE = "{connection}"\n', encoding="utf-8")
    out = tmp_path / "identity"

    result = snapshot_identity(repo, out, engine="codex", environ={})

    assert result["ok"] is False
    assert result["components"]["git_patch"]["status"] == "MISSING"
    assert not (out / "code.patch").exists()
    assert connection.encode() not in _all_artifact_bytes(out)


@pytest.mark.parametrize("scheme", ["mssql+pyodbc", "amqps"])
def test_generic_uri_credentials_in_dirty_patch_are_never_persisted(tmp_path, scheme):
    repo = _repo(tmp_path)
    connection = f"{scheme}://runner:correct-horse@service.internal/app"
    (repo / "autoresearch/rule.py").write_text(f'CONNECTION = "{connection}"\n', encoding="utf-8")
    out = tmp_path / "identity"

    result = snapshot_identity(repo, out, engine="codex", environ={})

    assert result["components"]["git_patch"]["status"] == "MISSING"
    assert not (out / "code.patch").exists()
    assert connection.encode() not in _all_artifact_bytes(out)


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
    artifacts = _all_artifact_bytes(out)
    assert secret.encode() not in artifacts
    assert secret.encode().hex().encode() not in artifacts


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


@pytest.mark.skipif(os.name == "nt", reason="byte paths require POSIX")
def test_non_utf8_untracked_path_is_lossless_and_replayable(tmp_path):
    repo = _repo(tmp_path)
    raw_relative = b"autoresearch/nonutf-\xff.py"
    try:
        fd = os.open(
            os.fsencode(repo) + b"/" + raw_relative,
            os.O_WRONLY | os.O_CREAT,
            0o644,
        )
    except OSError as exc:
        pytest.skip(f"filesystem rejects byte paths: errno={exc.errno}")
    try:
        os.write(fd, b"VALUE = 7\n")
    finally:
        os.close(fd)
    expected_status = subprocess.run(
        ["git", "status", "--porcelain=v1", "-z", "--untracked-files=all"],
        cwd=os.fsencode(repo),
        check=True,
        capture_output=True,
    ).stdout
    out = tmp_path / "identity"

    result = snapshot_identity(repo, out, engine="codex", environ={})

    assert result["components"]["untracked_sources"]["status"] == "SUCCESS"
    manifest = json.loads((out / "source_manifest.json").read_text(encoding="utf-8"))
    assert bytes.fromhex(manifest["git"]["porcelain_status_hex"]) == expected_status
    path_row = next(
        row
        for row in manifest["untracked_paths"]
        if bytes.fromhex(row["raw_path_hex"]) == raw_relative
    )
    assert path_row["archive_member"].startswith("__raw_path__/")
    assert _tar_contents(out / "untracked_sources.tar.zst")[path_row["archive_member"]] == (
        b"VALUE = 7\n"
    )


def test_repository_epoch_drift_is_explicitly_partial(tmp_path, monkeypatch):
    repo = _repo(tmp_path)
    out = tmp_path / "identity"
    stable = identity_mod._repository_epoch(repo)
    changed = {**stable, "status_sha256": "f" * 64}
    epochs = iter((stable, changed))
    monkeypatch.setattr(identity_mod, "_repository_epoch", lambda *_, **__: next(epochs))

    result = snapshot_identity(repo, out, engine="codex", environ={})

    assert result["ok"] is False
    assert result["components"]["repository_epoch"]["status"] == "PARTIAL"
    assert "repository_epoch" in result["missing"]


def test_repository_epoch_detects_patch_and_prompt_content_race(tmp_path, monkeypatch):
    repo = _repo(tmp_path)
    (repo / "autoresearch/rule.py").write_text("VALUE = 2\n", encoding="utf-8")
    prompt = repo / ".claude/agents/analyst.md"
    prompt.write_text("prompt v2\n", encoding="utf-8")
    out = tmp_path / "identity"
    original = identity_mod._write_scanned
    mutated = False

    def mutate_after_patch(path, payload, **kwargs):
        nonlocal mutated
        written = original(path, payload, **kwargs)
        if path.name == "code.patch" and not mutated:
            mutated = True
            (repo / "autoresearch/rule.py").write_text("VALUE = 3\n", encoding="utf-8")
            prompt.write_text("prompt v3\n", encoding="utf-8")
        return written

    monkeypatch.setattr(identity_mod, "_write_scanned", mutate_after_patch)

    result = snapshot_identity(repo, out, engine="codex", environ={})

    assert b"VALUE = 2" in (out / "code.patch").read_bytes()
    assert (out / "prompts/agents/analyst.md").read_bytes() == b"prompt v3\n"
    assert result["components"]["repository_epoch"]["status"] == "PARTIAL"


def test_missing_gitlink_checkout_is_an_explicit_submodule_gap(tmp_path):
    repo = _repo(tmp_path)
    child = tmp_path / "child"
    child.mkdir()
    _git(child, "init", "-q")
    _git(child, "config", "user.email", "identity@example.invalid")
    _git(child, "config", "user.name", "Identity Test")
    (child / "module.py").write_text("VALUE = 1\n", encoding="utf-8")
    _git(child, "add", ".")
    _git(child, "commit", "-qm", "child")
    child_head = _git(child, "rev-parse", "HEAD").stdout.strip()
    _git(repo, "update-index", "--add", "--cacheinfo", f"160000,{child_head},vendor/sub")
    out = tmp_path / "identity"

    result = snapshot_identity(repo, out, engine="codex", environ={})

    assert result["ok"] is False
    assert result["components"]["submodules"]["status"] == "PARTIAL"
    submodules = json.loads((out / "submodules.json").read_text(encoding="utf-8"))
    assert submodules["submodules"] == [
        {
            "gitlink_sha": child_head,
            "path": "vendor/sub",
            "raw_path_hex": "76656e646f722f737562",
            "status": "MISSING_CHECKOUT",
        }
    ]


def test_checked_out_submodule_records_head_status_and_diff(tmp_path):
    repo = _repo(tmp_path)
    child = tmp_path / "child-checkout"
    child.mkdir()
    _git(child, "init", "-q")
    _git(child, "config", "user.email", "identity@example.invalid")
    _git(child, "config", "user.name", "Identity Test")
    (child / "module.py").write_text("VALUE = 1\n", encoding="utf-8")
    _git(child, "add", ".")
    _git(child, "commit", "-qm", "child")
    child_head = _git(child, "rev-parse", "HEAD").stdout.strip()
    _git(
        repo,
        "-c",
        "protocol.file.allow=always",
        "submodule",
        "add",
        "-q",
        str(child),
        "vendor/sub",
    )
    _git(repo, "commit", "-qam", "add submodule")
    out = tmp_path / "identity"

    result = snapshot_identity(repo, out, engine="codex", environ={})

    assert result["components"]["submodules"]["status"] == "SUCCESS"
    row = json.loads((out / "submodules.json").read_text(encoding="utf-8"))["submodules"][0]
    assert row["gitlink_sha"] == child_head
    assert row["checked_out_head"] == child_head
    assert row["status"] == "CAPTURED"
    assert bytes.fromhex(row["status_hex"]) == b""
    assert bytes.fromhex(row["diff_hex"]) == b""


def test_clean_submodule_head_mismatch_is_explicitly_partial(tmp_path):
    repo = _repo(tmp_path)
    child = tmp_path / "child-mismatch"
    child.mkdir()
    _git(child, "init", "-q")
    _git(child, "config", "user.email", "identity@example.invalid")
    _git(child, "config", "user.name", "Identity Test")
    (child / "module.py").write_text("VALUE = 1\n", encoding="utf-8")
    _git(child, "add", ".")
    _git(child, "commit", "-qm", "child")
    parent_gitlink = _git(child, "rev-parse", "HEAD").stdout.strip()
    _git(
        repo,
        "-c",
        "protocol.file.allow=always",
        "submodule",
        "add",
        "-q",
        str(child),
        "vendor/sub",
    )
    _git(repo, "commit", "-qam", "add submodule")
    checkout = repo / "vendor/sub"
    _git(checkout, "config", "user.email", "identity@example.invalid")
    _git(checkout, "config", "user.name", "Identity Test")
    (checkout / "module.py").write_text("VALUE = 2\n", encoding="utf-8")
    _git(checkout, "commit", "-qam", "local checkout commit")
    checked_out_head = _git(checkout, "rev-parse", "HEAD").stdout.strip()
    out = tmp_path / "identity"

    result = snapshot_identity(repo, out, engine="codex", environ={})

    assert result["components"]["submodules"]["status"] == "PARTIAL"
    row = json.loads((out / "submodules.json").read_text(encoding="utf-8"))["submodules"][0]
    assert row["gitlink_sha"] == parent_gitlink
    assert row["checked_out_head"] == checked_out_head
    assert row["checked_out_head"] != row["gitlink_sha"]
    assert bytes.fromhex(row["status_hex"]) == b""
    assert bytes.fromhex(row["diff_hex"]) == b""
    assert row["recursive_state"] == "HEAD_MISMATCH"


def test_dirty_submodule_is_partial_when_nested_bytes_are_not_archived(tmp_path):
    repo = _repo(tmp_path)
    child = tmp_path / "child-dirty"
    child.mkdir()
    _git(child, "init", "-q")
    _git(child, "config", "user.email", "identity@example.invalid")
    _git(child, "config", "user.name", "Identity Test")
    (child / "module.py").write_text("VALUE = 1\n", encoding="utf-8")
    _git(child, "add", ".")
    _git(child, "commit", "-qm", "child")
    _git(
        repo,
        "-c",
        "protocol.file.allow=always",
        "submodule",
        "add",
        "-q",
        str(child),
        "vendor/sub",
    )
    _git(repo, "commit", "-qam", "add submodule")
    (repo / "vendor/sub/untracked.txt").write_text("nested bytes\n", encoding="utf-8")
    out = tmp_path / "identity"

    result = snapshot_identity(repo, out, engine="codex", environ={})

    assert result["components"]["submodules"]["status"] == "PARTIAL"
    row = json.loads((out / "submodules.json").read_text(encoding="utf-8"))["submodules"][0]
    assert bytes.fromhex(row["status_hex"])
    assert row["recursive_state"] == "UNARCHIVED_DIRTY_STATE"


def test_clean_submodule_with_nested_gitlink_is_explicitly_partial(tmp_path):
    repo = _repo(tmp_path)
    leaf = tmp_path / "leaf"
    leaf.mkdir()
    _git(leaf, "init", "-q")
    _git(leaf, "config", "user.email", "identity@example.invalid")
    _git(leaf, "config", "user.name", "Identity Test")
    (leaf / "leaf.py").write_text("LEAF = 1\n", encoding="utf-8")
    _git(leaf, "add", ".")
    _git(leaf, "commit", "-qm", "leaf")
    child = tmp_path / "child-nested"
    child.mkdir()
    _git(child, "init", "-q")
    _git(child, "config", "user.email", "identity@example.invalid")
    _git(child, "config", "user.name", "Identity Test")
    (child / "module.py").write_text("VALUE = 1\n", encoding="utf-8")
    _git(child, "add", ".")
    _git(child, "commit", "-qm", "child")
    _git(
        child,
        "-c",
        "protocol.file.allow=always",
        "submodule",
        "add",
        "-q",
        str(leaf),
        "nested/leaf",
    )
    _git(child, "commit", "-qam", "nested leaf")
    _git(
        repo,
        "-c",
        "protocol.file.allow=always",
        "submodule",
        "add",
        "-q",
        str(child),
        "vendor/sub",
    )
    _git(repo, "commit", "-qam", "outer submodule")
    out = tmp_path / "identity"

    result = snapshot_identity(repo, out, engine="codex", environ={})

    assert result["components"]["submodules"]["status"] == "PARTIAL"
    row = json.loads((out / "submodules.json").read_text(encoding="utf-8"))["submodules"][0]
    assert row["recursive_state"] == "UNARCHIVED_NESTED_SUBMODULES"
    assert len(row["nested_gitlinks"]) == 1


def test_tracked_behavioral_symlinks_are_audited_losslessly(tmp_path):
    repo = _repo(tmp_path)
    outside = tmp_path / "outside.py"
    outside.write_bytes(b"OUTSIDE_LINK_BYTES\n")
    safe = repo / "autoresearch/linked_rule.py"
    unsafe = repo / "autoresearch/external_rule.py"
    safe.symlink_to("rule.py")
    unsafe.symlink_to(outside)
    _git(repo, "add", "autoresearch/linked_rule.py", "autoresearch/external_rule.py")
    _git(repo, "commit", "-qm", "behavioral links")
    out = tmp_path / "identity"

    result = snapshot_identity(repo, out, engine="codex", environ={})

    assert result["components"]["source_links"]["status"] == "PARTIAL"
    rows = json.loads((out / "source_links.json").read_text(encoding="utf-8"))["source_links"]
    safe_row = next(row for row in rows if row["path"].endswith("linked_rule.py"))
    unsafe_row = next(row for row in rows if row["path"].endswith("external_rule.py"))
    assert bytes.fromhex(safe_row["link_target_hex"]) == b"rule.py"
    assert bytes.fromhex(safe_row["resolved_source_hex"]) == b"autoresearch/rule.py"
    assert safe_row["sha256"] == safe_row["resolved_sha256"]
    assert safe_row["mode"] == 0o644
    assert (out / safe_row["snapshot"]).read_bytes() == b"VALUE = 1\n"
    assert unsafe_row["status"] == "UNSAFE_TARGET"
    assert b"OUTSIDE_LINK_BYTES" not in _all_artifact_bytes(out)


def test_retry_removes_only_stale_owned_prompts_and_preserves_contract(tmp_path):
    repo = _repo(tmp_path)
    out = tmp_path / "identity"
    out.mkdir()
    contract = out / "run_contract.json"
    contract.write_bytes(b"contract-bytes\n")
    prompt = repo / ".claude/skills/new/SKILL.md"
    prompt.parent.mkdir(parents=True)
    prompt.write_text("temporary prompt\n", encoding="utf-8")
    assert snapshot_identity(repo, out, engine="codex", environ={})["ok"] is True
    copied = out / "prompts/skills/new/SKILL.md"
    assert copied.is_file()

    prompt.unlink()
    result = snapshot_identity(repo, out, engine="codex", environ={})

    assert result["ok"] is True
    assert not copied.exists()
    assert contract.read_bytes() == b"contract-bytes\n"


def _owned_snapshot_bytes(out: Path) -> dict[str, bytes]:
    return {
        path.relative_to(out).as_posix(): path.read_bytes()
        for path in sorted(out.rglob("*"))
        if path.is_file() and path.name != "run_contract.json"
    }


def test_capture_failure_preserves_previous_snapshot_generation(tmp_path, monkeypatch):
    repo = _repo(tmp_path)
    out = tmp_path / "identity"
    assert snapshot_identity(repo, out, engine="codex", environ={})["ok"] is True
    before = _owned_snapshot_bytes(out)
    (repo / "autoresearch/rule.py").write_text("VALUE = 2\n", encoding="utf-8")
    (repo / ".claude/agents/analyst.md").write_text("new prompt\n", encoding="utf-8")
    original = identity_mod._write_scanned

    def fail_result(path, payload, **kwargs):
        if path.name == "snapshot_result.json":
            raise OSError("capture fault")
        return original(path, payload, **kwargs)

    monkeypatch.setattr(identity_mod, "_write_scanned", fail_result)

    with pytest.raises(OSError, match="capture fault"):
        snapshot_identity(repo, out, engine="codex", environ={})

    assert _owned_snapshot_bytes(out) == before


def test_promotion_failure_rolls_back_previous_snapshot_generation(tmp_path, monkeypatch):
    repo = _repo(tmp_path)
    out = tmp_path / "identity"
    assert snapshot_identity(repo, out, engine="codex", environ={})["ok"] is True
    before = _owned_snapshot_bytes(out)
    (repo / "autoresearch/rule.py").write_text("VALUE = 2\n", encoding="utf-8")
    original = identity_mod._replace_promoted_path
    calls = 0

    def fail_second(source, destination):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise OSError("promotion fault")
        return original(source, destination)

    monkeypatch.setattr(identity_mod, "_replace_promoted_path", fail_second)

    with pytest.raises(OSError, match="promotion fault"):
        snapshot_identity(repo, out, engine="codex", environ={})

    assert _owned_snapshot_bytes(out) == before


def test_live_snapshot_validation_rejects_missing_claimed_artifact(tmp_path):
    repo = _repo(tmp_path)
    (repo / "autoresearch/rule.py").write_text("VALUE = 2\n", encoding="utf-8")
    out = tmp_path / "identity"
    assert snapshot_identity(repo, out, engine="codex", environ={})["ok"] is True
    (out / "code.patch").unlink()

    assert identity_mod._has_valid_live_snapshot(out) is False


def test_crash_after_old_patch_move_restores_only_valid_rollback_copy(tmp_path, monkeypatch):
    repo = _repo(tmp_path)
    (repo / "autoresearch/rule.py").write_text("VALUE = 2\n", encoding="utf-8")
    out = tmp_path / "identity"
    assert snapshot_identity(repo, out, engine="codex", environ={})["ok"] is True
    before = _owned_snapshot_bytes(out)
    (repo / "autoresearch/rule.py").write_text("VALUE = 3\n", encoding="utf-8")
    original_replace = identity_mod._replace_promoted_path

    def crash_after_old_patch(source, destination):
        original_replace(source, destination)
        if Path(destination).name == "code.patch" and Path(destination).parent.name.startswith(
            ".identity.backup-"
        ):
            raise KeyboardInterrupt

    monkeypatch.setattr(identity_mod, "_replace_promoted_path", crash_after_old_patch)

    with pytest.raises(KeyboardInterrupt):
        snapshot_identity(repo, out, engine="codex", environ={})

    assert not (out / "code.patch").exists()
    assert list(out.parent.glob(".identity.backup-*"))
    monkeypatch.setattr(identity_mod, "_replace_promoted_path", original_replace)
    monkeypatch.setattr(
        identity_mod,
        "_snapshot_identity_locked",
        lambda *args, **kwargs: (_ for _ in ()).throw(OSError("new capture fault")),
    )

    with pytest.raises(OSError, match="new capture fault"):
        snapshot_identity(repo, out, engine="codex", environ={})

    assert _owned_snapshot_bytes(out) == before
    assert identity_mod._has_valid_live_snapshot(out) is True
    assert list(out.parent.glob(".identity.backup-*")) == []
    assert list(out.parent.glob(".identity.generation-*")) == []


def test_transaction_backs_up_complete_old_inventory_before_promoting_environment(
    tmp_path, monkeypatch
):
    repo = _repo(tmp_path)
    (repo / "autoresearch/rule.py").write_text("VALUE = 2\n", encoding="utf-8")
    out = tmp_path / "identity"
    assert snapshot_identity(repo, out, engine="codex", model="old-model", environ={})["ok"] is True
    before = _owned_snapshot_bytes(out)
    (repo / "autoresearch/rule.py").write_text("VALUE = 3\n", encoding="utf-8")
    original_replace = identity_mod._replace_promoted_path

    def crash_after_new_environment(source, destination):
        original_replace(source, destination)
        if Path(destination) == out / "environment.json":
            raise KeyboardInterrupt

    monkeypatch.setattr(identity_mod, "_replace_promoted_path", crash_after_new_environment)

    with pytest.raises(KeyboardInterrupt):
        snapshot_identity(repo, out, engine="codex", model="new-model", environ={})

    backup = next(iter(out.parent.glob(".identity.backup-*")))
    backed_up = {
        path.relative_to(backup).as_posix()
        for path in backup.rglob("*")
        if path.is_file() and path.name != "snapshot_transaction.json"
    }
    assert backed_up == set(before)
    journal = json.loads((backup / "snapshot_transaction.json").read_text(encoding="utf-8"))
    assert {row["path"] for row in journal["old_artifacts"] if row["type"] == "file"} == set(before)

    monkeypatch.setattr(identity_mod, "_replace_promoted_path", original_replace)
    monkeypatch.setattr(
        identity_mod,
        "_snapshot_identity_locked",
        lambda *args, **kwargs: (_ for _ in ()).throw(OSError("retry capture fault")),
    )

    with pytest.raises(OSError, match="retry capture fault"):
        snapshot_identity(repo, out, engine="codex", environ={})

    assert _owned_snapshot_bytes(out) == before
    assert identity_mod.load_snapshot_result(out)["ok"] is True


@pytest.mark.parametrize("artifact", ["environment.json", "dependencies.txt"])
def test_snapshot_loader_rejects_mutated_owned_artifact(tmp_path, artifact):
    repo = _repo(tmp_path)
    out = tmp_path / "identity"
    assert snapshot_identity(repo, out, engine="codex", environ={})["ok"] is True
    (out / artifact).write_bytes((out / artifact).read_bytes() + b"mutated\n")

    with pytest.raises(ValueError, match="inventory"):
        identity_mod.load_snapshot_result(out)


def test_snapshot_loader_rejects_extra_owned_prompt_artifact(tmp_path):
    repo = _repo(tmp_path)
    out = tmp_path / "identity"
    assert snapshot_identity(repo, out, engine="codex", environ={})["ok"] is True
    extra = out / "prompts/agents/extra.md"
    extra.parent.mkdir(parents=True, exist_ok=True)
    extra.write_text("extra\n", encoding="utf-8")

    with pytest.raises(ValueError, match="inventory"):
        identity_mod.load_snapshot_result(out)


@pytest.mark.skipif(os.name == "nt", reason="directory fsync is POSIX-specific")
def test_commit_fsync_failure_rolls_back_previous_snapshot_generation(tmp_path, monkeypatch):
    repo = _repo(tmp_path)
    out = tmp_path / "identity"
    assert snapshot_identity(repo, out, engine="codex", environ={})["ok"] is True
    before = _owned_snapshot_bytes(out)
    (repo / "autoresearch/rule.py").write_text("VALUE = 2\n", encoding="utf-8")
    original = identity_mod._fsync_directory
    failed = False

    def fail_commit(path):
        nonlocal failed
        if Path(path) == out and not failed:
            failed = True
            raise OSError("commit sync fault")
        return original(path)

    monkeypatch.setattr(identity_mod, "_fsync_directory", fail_commit)

    with pytest.raises(OSError, match="commit sync fault"):
        snapshot_identity(repo, out, engine="codex", environ={})

    assert _owned_snapshot_bytes(out) == before


@pytest.mark.skipif(os.name == "nt", reason="directory fsync is POSIX-specific")
def test_post_commit_cleanup_fsync_failure_keeps_new_generation_and_is_explicit(
    tmp_path, monkeypatch, capsys
):
    repo = _repo(tmp_path)
    out = tmp_path / "identity"
    assert snapshot_identity(repo, out, engine="codex", environ={})["ok"] is True
    (repo / "autoresearch/rule.py").write_text("VALUE = 2\n", encoding="utf-8")
    original = identity_mod._fsync_directory
    parent_calls = 0

    def fail_post_commit(path):
        nonlocal parent_calls
        if Path(path) == out.parent:
            parent_calls += 1
            if parent_calls == 3:
                raise OSError("cleanup sync fault")
        return original(path)

    monkeypatch.setattr(identity_mod, "_fsync_directory", fail_post_commit)

    result = snapshot_identity(repo, out, engine="codex", environ={})

    assert b"VALUE = 2" in (out / "code.patch").read_bytes()
    assert result["ok"] is False
    assert result["components"]["snapshot_cleanup"]["status"] == "PARTIAL"
    persisted = identity_mod.load_snapshot_result(out)
    assert persisted["components"]["snapshot_cleanup"]["status"] == "PARTIAL"
    assert (out / "snapshot_cleanup_warning.json").is_file()
    assert capsys.readouterr().err == "identity snapshot cleanup degraded\n"


def test_cleanup_warning_is_authoritative_when_base_result_stays_successful(tmp_path):
    repo = _repo(tmp_path)
    out = tmp_path / "identity"
    assert snapshot_identity(repo, out, engine="codex", environ={})["ok"] is True
    base = json.loads((out / "snapshot_result.json").read_text(encoding="utf-8"))
    assert base["ok"] is True
    warning = {
        "schema_version": 1,
        "status": "PARTIAL",
        "failures": [{"phase": "cleanup_fsync", "error_type": "OSError"}],
        "stale_backup_count": 1,
        "stale_generation_count": 0,
    }
    (out / "snapshot_cleanup_warning.json").write_text(
        canonical_json(warning) + "\n", encoding="utf-8"
    )

    loaded = identity_mod.load_snapshot_result(out)

    assert loaded["ok"] is False
    assert loaded["components"]["snapshot_cleanup"]["status"] == "PARTIAL"
    assert "snapshot_cleanup" in loaded["missing"]


@pytest.mark.skipif(os.name == "nt", reason="directory fsync is POSIX-specific")
def test_cleanup_warning_remains_authoritative_when_result_update_fails(
    tmp_path, monkeypatch, capsys
):
    repo = _repo(tmp_path)
    out = tmp_path / "identity"
    assert snapshot_identity(repo, out, engine="codex", environ={})["ok"] is True
    (repo / "autoresearch/rule.py").write_text("VALUE = 2\n", encoding="utf-8")
    original_fsync = identity_mod._fsync_directory
    original_write = identity_mod._write_scanned
    parent_calls = 0

    def fail_cleanup_fsync(path):
        nonlocal parent_calls
        if Path(path) == out.parent:
            parent_calls += 1
            if parent_calls == 3:
                raise OSError("cleanup sync fault")
        return original_fsync(path)

    def fail_live_result(path, payload, **kwargs):
        if Path(path) == out / "snapshot_result.json":
            raise OSError("result update fault")
        return original_write(path, payload, **kwargs)

    monkeypatch.setattr(identity_mod, "_fsync_directory", fail_cleanup_fsync)
    monkeypatch.setattr(identity_mod, "_write_scanned", fail_live_result)

    result = snapshot_identity(repo, out, engine="codex", environ={})

    assert json.loads((out / "snapshot_result.json").read_text(encoding="utf-8"))["ok"] is True
    assert (out / "snapshot_cleanup_warning.json").is_file()
    assert result["ok"] is False
    assert identity_mod.load_snapshot_result(out)["ok"] is False
    assert capsys.readouterr().err == "identity snapshot cleanup degraded\n"


def test_stale_owned_backup_is_marked_then_scavenged_on_retry(tmp_path, monkeypatch, capsys):
    repo = _repo(tmp_path)
    out = tmp_path / "identity"
    assert snapshot_identity(repo, out, engine="codex", environ={})["ok"] is True
    (repo / "autoresearch/rule.py").write_text("VALUE = 2\n", encoding="utf-8")
    unrelated = out.parent / ".identity.backup-manual-keep"
    unrelated.mkdir()
    original = identity_mod.shutil.rmtree

    def fail_backup(path, *args, **kwargs):
        if Path(path).name.startswith(".identity.backup-"):
            raise OSError("backup cleanup fault")
        return original(path, *args, **kwargs)

    monkeypatch.setattr(identity_mod.shutil, "rmtree", fail_backup)

    degraded = snapshot_identity(repo, out, engine="codex", environ={})

    assert degraded["components"]["snapshot_cleanup"]["status"] == "PARTIAL"
    assert (out / "snapshot_cleanup_warning.json").is_file()
    assert any(
        path.name.startswith(".identity.backup-")
        for path in identity_mod._owned_snapshot_siblings(out)
    )
    assert capsys.readouterr().err == "identity snapshot cleanup degraded\n"

    monkeypatch.setattr(identity_mod.shutil, "rmtree", original)
    recovered = snapshot_identity(repo, out, engine="codex", environ={})

    assert recovered["ok"] is True
    assert not (out / "snapshot_cleanup_warning.json").exists()
    assert list(out.parent.glob(".identity.backup-*")) == [unrelated]
    assert list(out.parent.glob(".identity.generation-*")) == []
    assert unrelated.is_dir()


def test_concurrent_snapshots_serialize_without_interleaved_artifacts(tmp_path):
    repo = _repo(tmp_path)
    out = tmp_path / "identity"

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(
            pool.map(
                lambda _: snapshot_identity(repo, out, engine="codex", environ={}),
                range(2),
            )
        )

    assert all(result["ok"] for result in results)
    assert json.loads((out / "snapshot_result.json").read_text(encoding="utf-8"))["ok"]
    assert _tar_names(out / "untracked_sources.tar.zst") == []


@pytest.mark.skipif(os.name == "nt", reason="directory fsync is POSIX-specific")
def test_identity_atomic_write_fsyncs_parent_and_propagates_failure(tmp_path, monkeypatch):
    target = tmp_path / "identity/artifact.bin"
    original = identity_mod.os.fsync
    directory_calls = 0

    def observe(fd):
        nonlocal directory_calls
        if os.path.isdir(f"/dev/fd/{fd}"):
            directory_calls += 1
        return original(fd)

    monkeypatch.setattr(identity_mod.os, "fsync", observe)
    identity_mod._atomic_write_bytes(target, b"safe")
    assert directory_calls == 1

    def fail_directory(fd):
        if os.path.isdir(f"/dev/fd/{fd}"):
            raise OSError("directory sync fault")
        return original(fd)

    monkeypatch.setattr(identity_mod.os, "fsync", fail_directory)
    with pytest.raises(OSError, match="directory sync fault"):
        identity_mod._atomic_write_bytes(target, b"replacement")


# ───────────────────────────────────────────────── D6.5: codex escape hatch


def test_detect_codex_web_search_mode_reads_the_configured_value(tmp_path):
    config = tmp_path / "config.toml"
    config.write_text('model = "gpt-5.6-sol"\nweb_search = "cached"\n', encoding="utf-8")

    assert identity_mod.detect_codex_web_search_mode(config) == "cached"


def test_detect_codex_web_search_mode_unquoted_value(tmp_path):
    config = tmp_path / "config.toml"
    config.write_text("web_search = live\n", encoding="utf-8")

    assert identity_mod.detect_codex_web_search_mode(config) == "live"


def test_detect_codex_web_search_mode_missing_file_is_unknown_not_a_crash(tmp_path):
    assert identity_mod.detect_codex_web_search_mode(tmp_path / "nope.toml") == "UNKNOWN"


def test_detect_codex_web_search_mode_missing_key_is_unknown(tmp_path):
    config = tmp_path / "config.toml"
    config.write_text('model = "gpt-5.6-sol"\n', encoding="utf-8")

    assert identity_mod.detect_codex_web_search_mode(config) == "UNKNOWN"


def test_detect_codex_web_search_mode_never_writes_the_config(tmp_path):
    config = tmp_path / "config.toml"
    original = 'web_search = "cached"\n'
    config.write_text(original, encoding="utf-8")

    identity_mod.detect_codex_web_search_mode(config)

    assert config.read_text(encoding="utf-8") == original
