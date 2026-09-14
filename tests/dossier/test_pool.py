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


def test_default_pool_reader_prefers_committed_version(monkeypatch, tmp_path):
    legacy = {"stocks": {"old": {"status": "active"}}, "cap": 30}
    (tmp_path / "pool.json").write_text(json.dumps(legacy), encoding="utf-8")
    monkeypatch.setattr(pool, "POOL_PATH", tmp_path / "pool.json")
    committed = {"stocks": {"new": {"status": "active"}}}
    monkeypatch.setattr(pool, "read_committed_state", lambda *args, **kwargs: committed)

    assert pool.load_pool() == {"stocks": committed["stocks"], "cap": 30}


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


def test_pending_init_prioritizes_finalist_priority(tmp_path, monkeypatch):
    """Wave9 R6:当日无档案 finalist 插队——priority=finalist 的条目排到最前,同级(都无
    标注)按 code 字典序不变(逐字节等价于 Wave9 前的行为)。"""
    monkeypatch.setattr("autoresearch.dossier.schema.DOSSIER_DIR", tmp_path / "dossiers")
    p = {
        "cap": 30,
        "stocks": {
            "300857": {"status": "active"},
            "000651": {"status": "active"},
            "920179": {"status": "active"},
        },
        "pending_init": [
            {"code": "920179", "priority": "finalist", "last_seen": "2026-07-29"},
        ],
    }
    assert pool.pending_init(p) == ["920179", "000651", "300857"]


def test_pending_init_breaks_finalist_ties_by_last_seen_ascending(tmp_path, monkeypatch):
    """同为 priority=finalist 时,`last_seen` 更旧(等得更久)的先建——「先来先建」。"""
    monkeypatch.setattr("autoresearch.dossier.schema.DOSSIER_DIR", tmp_path / "dossiers")
    p = {
        "cap": 30,
        "stocks": {"600018": {"status": "active"}, "600267": {"status": "active"}},
        "pending_init": [
            {"code": "600018", "priority": "finalist", "last_seen": "2026-07-29"},
            {"code": "600267", "priority": "finalist", "last_seen": "2026-07-27"},
        ],
    }
    assert pool.pending_init(p) == ["600267", "600018"]


def test_pending_init_tolerates_legacy_string_entries(tmp_path, monkeypatch):
    """`pending_init` 数组里的既有条目可能是纯字符串(非 dict)——不得崩溃,视同未标注。"""
    monkeypatch.setattr("autoresearch.dossier.schema.DOSSIER_DIR", tmp_path / "dossiers")
    p = {
        "cap": 30,
        "stocks": {"600018": {"status": "active"}},
        "pending_init": ["600018"],
    }
    assert pool.pending_init(p) == ["600018"]
