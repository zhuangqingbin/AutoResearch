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


def test_scan_run_script_keeps_the_mac_awake_for_the_whole_run():
    """review I4c: on battery a MacBook idles to sleep a minute into a 60–180 min run."""
    text = SCRIPT.read_text(encoding="utf-8")
    assert "exec /usr/bin/caffeinate -i uv run --no-sync python -m autoresearch.scan.scan_run" \
        in text


def test_scan_run_plist_gives_the_abort_path_time_to_clean_up():
    """review M1d: launchd's default ExitTimeOut (20 s) SIGKILLs scan_run before it has
    stopped the runner group, finalized FAILED and pushed."""
    assert _plist(SCAN_PLIST)["ExitTimeOut"] >= 60


# ── nightly-close waits for the unattended scan (review M2) ─────────────────────────

NIGHTLY = REPO / "scripts" / "nightly_close.sh"


def _run_nightly(tmp_path, lock_answers: list[int], *, wait_min: int, poll_s: int = 0):
    fake = tmp_path / "bin" / "uv"
    fake.parent.mkdir()
    answers = tmp_path / "lock_answers"
    answers.write_text("".join(f"{code}\n" for code in lock_answers), encoding="utf-8")
    fake.write_text(
        "#!/bin/sh\n"
        'echo "$*" >> "$UV_LOG"\n'
        'case "$*" in *autoresearch.scan.run_lock*)\n'
        '  n=$(head -n 1 "$LOCK_ANSWERS"); tail -n +2 "$LOCK_ANSWERS" > "$LOCK_ANSWERS.t"\n'
        '  mv "$LOCK_ANSWERS.t" "$LOCK_ANSWERS"; exit "${n:-0}";;\n'
        "esac\nexit 0\n", encoding="utf-8")
    fake.chmod(fake.stat().st_mode | stat.S_IEXEC)
    log = tmp_path / "uv.log"
    env = {"PATH": f"{fake.parent}:/usr/bin:/bin", "HOME": str(tmp_path), "UV_LOG": str(log),
           "LOCK_ANSWERS": str(answers), "AUTORESEARCH_ENGINE": "claude",
           "NIGHTLY_SCAN_WAIT_MIN": str(wait_min), "NIGHTLY_SCAN_POLL_S": str(poll_s)}
    proc = subprocess.run(["/bin/zsh", "-f", str(NIGHTLY)], env=env, capture_output=True,
                          text=True, timeout=60)
    calls = log.read_text(encoding="utf-8").splitlines() if log.exists() else []
    return proc, calls


def _modules(calls: list[str]) -> list[str]:
    return [line.split(" -m ", 1)[1].split()[0] for line in calls if " -m " in line]


def test_nightly_close_runs_all_steps_when_no_scan_holds_the_lock(tmp_path):
    proc, calls = _run_nightly(tmp_path, [0], wait_min=90)
    assert proc.returncode == 0, proc.stderr
    assert _modules(calls) == [
        "autoresearch.scan.run_lock", "autoresearch.scan.outcome", "autoresearch.scan.ledger_views",
        "autoresearch.scan.populations", "autoresearch.scan.populations",
        "autoresearch.analyze.ledger", "autoresearch.news.catalog"]


def test_nightly_close_waits_for_a_running_scan_then_runs_everything(tmp_path):
    proc, calls = _run_nightly(tmp_path, [3, 3, 0], wait_min=90)
    assert proc.returncode == 0, proc.stderr
    modules = _modules(calls)
    assert modules.count("autoresearch.scan.run_lock") == 3
    assert "autoresearch.scan.outcome" in modules and "autoresearch.analyze.ledger" in modules
    assert modules.index("autoresearch.scan.outcome") > 2       # only after the lock went free


def test_nightly_close_skips_the_scan_ledger_steps_while_a_scan_still_runs(tmp_path):
    proc, calls = _run_nightly(tmp_path, [3] * 5, wait_min=0)
    assert proc.returncode == 0, proc.stderr
    modules = _modules(calls)
    assert not any(module.startswith(("autoresearch.scan.outcome", "autoresearch.scan.ledger",
                                      "autoresearch.scan.populations")) for module in modules)
    assert "autoresearch.analyze.ledger" in modules              # independent of the scan
    assert "autoresearch.news.catalog" in modules                # atomic catalog writes, no scan ledger
    assert "跳过" in proc.stdout + proc.stderr


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


@pytest.mark.parametrize("path", [NIGHTLY_PLIST, REPO / "docs" / "ops" / "scan-ops.md"])
def test_nightly_close_reinstall_retires_the_unsuffixed_legacy_label(path):
    """review M3: the job installed on this machine is `com.tradingagents.nightly-close`
    (no engine suffix, 20:45). Booting out only `…nightly-close.<engine>` leaves it running
    next to the new 23:30 jobs — nightly-close three times a night."""
    import re

    text = path.read_text(encoding="utf-8")
    assert re.search(r"bootout gui/\$\(id -u\)/com\.tradingagents\.nightly-close(?![.\w])", text)
    assert "rm -f ~/Library/LaunchAgents/com.tradingagents.nightly-close.plist" in text


@pytest.mark.parametrize("path", [SCAN_PLIST, NIGHTLY_PLIST])
def test_templates_carry_no_credentials(path):
    """Only the engine is pinned in launchd's environment; tokens stay in .env / profile."""
    env = _plist(path).get("EnvironmentVariables") or {}
    assert set(env) == {"AUTORESEARCH_ENGINE"}
