from __future__ import annotations

import socket
import sys

import pytest

from autoresearch.trace.offline import (
    create_offline_layout,
    run_isolated,
    strict_isolation_available,
)


def _require_isolation() -> None:
    if not strict_isolation_available():
        pytest.skip("INCOMPLETE: no supported system replay sandbox is available")


def _attempt(layout, code: str, *, denied_reads=(), denied_writes=()):
    result = run_isolated(
        [sys.executable, "-c", code],
        layout,
        env={},
        denied_read_roots=denied_reads,
        denied_write_roots=denied_writes,
    )
    assert result.isolation_status == "ENFORCED"
    return result


def test_strict_isolation_blocks_network(tmp_path):
    _require_isolation()
    layout = create_offline_layout(tmp_path / "replay")
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    listener.listen(1)
    port = listener.getsockname()[1]
    try:
        code = (
            "import socket,sys; s=socket.socket(); "
            f"\ntry: s.connect(('127.0.0.1',{port}))"
            "\nexcept OSError: sys.exit(0)"
            "\nsys.exit(9)"
        )
        assert _attempt(layout, code).exit_code == 0
    finally:
        listener.close()


@pytest.mark.parametrize("target_kind", ["original_lake", "expected"])
def test_strict_isolation_blocks_original_and_expected_reads(tmp_path, target_kind):
    _require_isolation()
    layout = create_offline_layout(tmp_path / "replay")
    root = tmp_path / target_kind if target_kind == "original_lake" else layout.expected
    root.mkdir(parents=True, exist_ok=True)
    target = root / "secret.txt"
    target.write_text("must-not-be-visible", encoding="utf-8")
    code = (
        "from pathlib import Path; import sys\n"
        f"p=Path({str(target)!r})\n"
        "try: p.read_bytes()\n"
        "except OSError: sys.exit(0)\n"
        "sys.exit(9)\n"
    )

    result = _attempt(layout, code, denied_reads=(root,))

    assert result.exit_code == 0


def test_strict_isolation_blocks_original_state_writes(tmp_path):
    _require_isolation()
    layout = create_offline_layout(tmp_path / "replay")
    original_state = tmp_path / "context_codex"
    original_state.mkdir()
    target = original_state / "state.json"
    target.write_text("{}", encoding="utf-8")
    code = (
        "from pathlib import Path; import sys\n"
        f"p=Path({str(target)!r})\n"
        "try: p.write_text('changed')\n"
        "except OSError: sys.exit(0)\n"
        "sys.exit(9)\n"
    )

    result = _attempt(layout, code, denied_writes=(original_state,))

    assert result.exit_code == 0
    assert target.read_text(encoding="utf-8") == "{}"


def test_strict_isolation_allows_declared_output_writes(tmp_path):
    _require_isolation()
    layout = create_offline_layout(tmp_path / "replay")
    target = layout.outputs / "result.txt"
    code = f"from pathlib import Path; Path({str(target)!r}).write_text('ok')"

    result = _attempt(layout, code)

    assert result.exit_code == 0
    assert target.read_text(encoding="utf-8") == "ok"
