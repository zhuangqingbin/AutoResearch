import json
from pathlib import Path

from autoresearch.dossier import pool


def _mk_scan(root: Path, dates_with: dict[str, list[tuple[str, str]]]):
    """dates_with: {date: [(code, lane), ...]} → 造 finalists.csv。"""
    for d, rows in dates_with.items():
        sd = root / d
        sd.mkdir(parents=True, exist_ok=True)
        body = "code,name,sector,lane\n" + "\n".join(
            f"{c},N{c},X,{lane}" for c, lane in rows)
        (sd / "finalists.csv").write_text(body, encoding="utf-8")


def _pinned_file(p: Path, codes: list[str]):
    p.write_text(json.dumps([{"code": c} for c in codes]), encoding="utf-8")
    return p


def test_pinned_and_finalist2x_enter(tmp_path):
    scan = tmp_path / "scan"
    _mk_scan(scan, {"2026-07-21": [("002926", "healthy")],
                    "2026-07-22": [("002926", "momentum"), ("300857", "pinned")]})
    pp = _pinned_file(tmp_path / "pinned.jsonc", ["300857"])
    out = pool.refresh("2026-07-23", scan_root=scan, pool_path=tmp_path / "pool.json",
                       pinned_path=pp)
    assert set(out["entered"]) == {"002926", "300857"}   # 002926 真选×2;300857 pinned 即入
    saved = json.loads((tmp_path / "pool.json").read_text())
    assert saved["stocks"]["002926"]["entry_reason"] == "finalist_2x"
    assert saved["stocks"]["300857"]["entry_reason"] == "pinned"


def test_single_selection_not_enough(tmp_path):
    scan = tmp_path / "scan"
    _mk_scan(scan, {"2026-07-22": [("600350", "healthy")]})
    out = pool.refresh("2026-07-23", scan_root=scan, pool_path=tmp_path / "pool.json",
                       pinned_path=_pinned_file(tmp_path / "p.jsonc", []))
    assert out["entered"] == [] and out["n_active"] == 0


def test_retire_after_window(tmp_path):
    scan = tmp_path / "scan"
    # 21 个交易日:码 600188 只在最早一天真选过 → 已滑出 20 日窗 → retire
    dates = {f"2026-06-{d:02d}": [("999999", "healthy")] for d in range(1, 22)}
    dates["2026-05-30"] = [("600188", "healthy"), ("600188x", "x")]
    _mk_scan(scan, dates)
    pp = _pinned_file(tmp_path / "p.jsonc", [])
    pool_path = tmp_path / "pool.json"
    pool_path.write_text(json.dumps({"cap": 30, "stocks": {"600188": {
        "name": "兖矿", "status": "active", "entered": "2026-05-30",
        "entry_reason": "manual", "last_selected": "2026-05-30", "note": ""}}}),
        encoding="utf-8")
    out = pool.refresh("2026-06-30", scan_root=scan, pool_path=pool_path, pinned_path=pp)
    assert out["retired"] == ["600188"]


def test_pending_init_lists_active_without_dossier(tmp_path, monkeypatch):
    monkeypatch.setattr("autoresearch.dossier.schema.DOSSIER_DIR", tmp_path / "dossiers")
    p = {"cap": 30, "stocks": {"300857": {"status": "active"}, "601869": {"status": "retired"}}}
    assert pool.pending_init(p) == ["300857"]
