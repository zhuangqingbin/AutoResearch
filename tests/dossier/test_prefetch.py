import json

from autoresearch.dossier import prefetch, schema


def test_prefetch_one_all_legs(tmp_path, monkeypatch):
    monkeypatch.setattr(schema, "DOSSIER_DIR", tmp_path)
    monkeypatch.setattr(prefetch, "PREFETCH_DIR", tmp_path / "_prefetch")
    out = prefetch.prefetch_one(
        "300857", "2026-07-23",
        fetch=lambda e, p: (_ for _ in ()).throw(RuntimeError("no net")),  # mainbz 腿挂
        ths_fn=lambda code6, today: {"fwd_eps_2026": 5.0, "asof": today},
        band_fn=lambda code6, today: {"pe_p25": 30.0, "pe_p50": 45.0, "pe_p75": 70.0,
                                      "pe_now": 59.9, "pb_p50": 8.0})
    assert out["mainbz"] == [] and "mainbz" in " ".join(out["notes"])   # 降级留痕
    assert out["fwd_eps"]["fwd_eps_2026"] == 5.0
    assert out["val_band"]["pe_p50"] == 45.0
    saved = json.loads((tmp_path / "_prefetch" / "300857.json").read_text())
    assert saved["code"] == "300857" and saved["asof"] == "2026-07-23"


def test_prefetch_pool_uses_active(tmp_path, monkeypatch):
    monkeypatch.setattr(prefetch, "PREFETCH_DIR", tmp_path / "_prefetch")
    monkeypatch.setattr("autoresearch.dossier.pool.load_pool",
                        lambda path=None: {"stocks": {"600350": {"status": "active"},
                                                      "601869": {"status": "retired"}}})
    called = []
    monkeypatch.setattr(prefetch, "prefetch_one",
                        lambda c, t, **kw: called.append(c) or {"code": c})
    prefetch.prefetch_pool("2026-07-23")
    assert called == ["600350"]


def test_main_single_code_calls_prefetch_one(monkeypatch):
    """CLI: `prefetch <code> <today>` 单码路径 → 调 prefetch_one(code, today),不落真实网络/文件。"""
    called = {}
    monkeypatch.setattr(prefetch, "prefetch_one",
                        lambda c, t, **kw: called.setdefault("args", (c, t)) or {"code": c})
    rc = prefetch.main(["300857", "2026-07-23"])
    assert rc == 0
    assert called["args"] == ("300857", "2026-07-23")
