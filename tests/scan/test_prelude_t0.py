"""prelude t0 锚契约:入口即写、重跑不覆盖(retry 不重置计时起点)。全 skip 跑法零网络。"""
import time

from autoresearch.scan.prelude import run_prelude

_ALL = ("retro_refresh", "retro_pending", "consensus", "universe", "calendar",
        "watchlist", "catalyst", "menu", "ledgers")


def test_t0_written_once(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    run_prelude("2026-07-10", skip=_ALL)
    fp = tmp_path / "context" / "scan" / "2026-07-10" / "_t0.json"
    assert fp.exists()
    m1 = fp.stat().st_mtime
    time.sleep(0.05)
    run_prelude("2026-07-10", skip=_ALL)
    assert fp.stat().st_mtime == m1               # 不覆盖
