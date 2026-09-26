"""scripts/scan_run.sh + launchd templates (batch 4 Task 3, spec §6 C2).

The script is a thin zsh wrapper; the flow lives in ``autoresearch.scan.scan_run`` (tested
there). Here: syntax, the exact command it execs (fake ``uv`` under ``zsh -f`` — rc files are
never read, the real uv can never be reached), and the plist templates. Nothing is loaded
into launchd; the plists are parsed as files.
"""
from __future__ import annotations

import os
import plistlib
import stat
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
SCRIPT = REPO / "scripts" / "scan_run.sh"
SCAN_PLIST = REPO / "scripts" / "com.tradingagents.scan-run.plist"
NIGHTLY_PLIST = REPO / "scripts" / "com.tradingagents.nightly-close.plist"


def test_scan_run_script_parses_and_is_executable():
    assert os.access(SCRIPT, os.X_OK)
    proc = subprocess.run(["/bin/zsh", "-n", str(SCRIPT)], capture_output=True, text=True)
    assert proc.returncode == 0, proc.stderr
    assert SCRIPT.read_text(encoding="utf-8").startswith("#!/bin/zsh -l\n")


def test_scan_run_script_execs_the_python_orchestrator_as_claude(tmp_path):
    fake = tmp_path / "bin" / "uv"
    fake.parent.mkdir()
    fake.write_text('#!/bin/sh\nfor a in "$@"; do printf "%s\\n" "$a"; done > "$UV_LOG"\n'
                    'echo "engine=$AUTORESEARCH_ENGINE" >> "$UV_LOG"\n'
                    'echo "cwd=$(pwd)" >> "$UV_LOG"\n', encoding="utf-8")
    fake.chmod(fake.stat().st_mode | stat.S_IEXEC)
    log = tmp_path / "uv.log"
    env = {"PATH": f"{fake.parent}:/usr/bin:/bin", "HOME": str(tmp_path), "UV_LOG": str(log),
           "AUTORESEARCH_ENGINE": "codex"}
    proc = subprocess.run(["/bin/zsh", "-f", str(SCRIPT), "--date", "2026-09-28"],
                          env=env, capture_output=True, text=True, timeout=30)
    assert proc.returncode == 0, proc.stderr
    lines = log.read_text(encoding="utf-8").splitlines()
    assert lines[:5] == ["run", "--no-sync", "python", "-m", "autoresearch.scan.scan_run"]
    assert lines[5:7] == ["--date", "2026-09-28"]
    assert "engine=claude" in lines                       # launchd has no engine hints
    assert f"cwd={REPO}" in lines


def test_scan_run_script_never_uses_flock_timeout_or_skip_permissions():
    text = SCRIPT.read_text(encoding="utf-8")
    for banned in ("flock ", "timeout ", "--dangerously-skip-permissions"):
        assert banned not in text


def _plist(path: Path) -> dict:
    return plistlib.loads(path.read_text(encoding="utf-8").replace("__REPO__", "/r").encode())


def test_scan_run_plist_template_fires_weekdays_at_21_20_as_claude():
    doc = _plist(SCAN_PLIST)
    assert doc["Label"] == "com.tradingagents.scan-run"
    assert doc["ProgramArguments"] == ["/bin/zsh", "-lc", "/r/scripts/scan_run.sh"]
    assert doc["EnvironmentVariables"]["AUTORESEARCH_ENGINE"] == "claude"
    slots = doc["StartCalendarInterval"]
    assert sorted(slot["Weekday"] for slot in slots) == [1, 2, 3, 4, 5]
    assert {(slot["Hour"], slot["Minute"]) for slot in slots} == {(21, 20)}
    assert doc["StandardOutPath"] == "/tmp/scan-run.log"
    assert "KeepAlive" not in doc and "RunAtLoad" not in doc   # one bounded run per evening
    assert "__REPO__" in SCAN_PLIST.read_text(encoding="utf-8")


def test_nightly_close_moves_to_23_30_after_the_scan():
    slots = _plist(NIGHTLY_PLIST)["StartCalendarInterval"]
    assert sorted(slot["Weekday"] for slot in slots) == [1, 2, 3, 4, 5]
    assert {(slot["Hour"], slot["Minute"]) for slot in slots} == {(23, 30)}


@pytest.mark.parametrize("path", [SCAN_PLIST, NIGHTLY_PLIST])
def test_templates_carry_no_credentials(path):
    """Only the engine is pinned in launchd's environment; tokens stay in .env / profile."""
    env = _plist(path).get("EnvironmentVariables") or {}
    assert set(env) == {"AUTORESEARCH_ENGINE"}
