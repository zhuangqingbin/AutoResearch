import json

from autoresearch.scan.agents.l4_card import harvest_slim_batch


def _setup(tmp_path, tickers):
    d = tmp_path / "2026-07-07"
    d.mkdir(parents=True)
    (d / "_harvest_list.txt").write_text("\n".join(tickers), encoding="utf-8")
    return d


def _setup_cli(tmp_path, tickers):
    """CLI 测试用:建 context/scan/<date>/_harvest_list.txt"""
    d = tmp_path / "context" / "scan" / "2026-07-07"
    d.mkdir(parents=True)
    (d / "_harvest_list.txt").write_text("\n".join(tickers), encoding="utf-8")
    return d


def test_harvest_slim_flags_undersized(tmp_path):
    _setup(tmp_path, ["600584.SS", "000062.SZ"])
    sizes = {"600584.SS": 20_000, "000062.SZ": 2_000}      # 000062 太小 = 失败

    def fake(t, dt):
        p = tmp_path / f"{t}_{dt}_slim.md"
        p.write_text("x" * sizes[t], encoding="utf-8")
        return p

    res = harvest_slim_batch("2026-07-07", root=tmp_path, retries=0, harvest_fn=fake)
    assert res["ok"] is False
    assert [f["ticker"] for f in res["failures"]] == ["000062.SZ"]


def test_harvest_slim_all_ok(tmp_path):
    _setup(tmp_path, ["600584.SS"])

    def fake(t, dt):
        p = tmp_path / f"{t}_{dt}_slim.md"
        p.write_text("x" * 20_000, encoding="utf-8")
        return p

    res = harvest_slim_batch("2026-07-07", root=tmp_path, retries=0, harvest_fn=fake)
    assert res["ok"] is True and res["failures"] == []


def test_harvest_slim_9kb_passes_new_floor(tmp_path):
    """地板 10K→8K(T1 二段式):9KB 表面块在旧地板(10_240B)下会判失败,新地板(8_192B)下应通过。"""
    _setup(tmp_path, ["600584.SS"])

    def fake(t, dt):
        p = tmp_path / f"{t}_{dt}_slim.md"
        p.write_text("x" * 9 * 1024, encoding="utf-8")
        return p

    res = harvest_slim_batch("2026-07-07", root=tmp_path, retries=0, harvest_fn=fake)
    assert res["ok"] is True and res["failures"] == []


def test_harvest_slim_catches_sh_suffix(tmp_path):
    _setup(tmp_path, ["600584.SH"])                        # 归一漏网 → 直接判失败
    res = harvest_slim_batch("2026-07-07", root=tmp_path, retries=0,
                             harvest_fn=lambda t, dt: None)
    assert res["ok"] is False and ".SH" in res["failures"][0]["why"]


def test_harvest_slim_cli_sh_fail(tmp_path, monkeypatch, capsys):
    """CLI 级测试:.SH 后缀失败路径 → main 返回 1 + JSON ok:false"""
    _setup_cli(tmp_path, ["600584.SH"])
    monkeypatch.chdir(tmp_path)
    from autoresearch.scan.agents.l4_card import main
    rc = main(["harvest-slim", "2026-07-07"])
    assert rc == 1
    out = capsys.readouterr().out
    data = json.loads(out)
    assert data["ok"] is False
    assert any("600584.SH" in f.get("ticker", "") for f in data["failures"])


def test_harvest_slim_cli_empty_ok(tmp_path, monkeypatch, capsys):
    """CLI 级测试:空清单成功路径 → main 返回 0 + JSON ok:true"""
    _setup_cli(tmp_path, [])
    monkeypatch.chdir(tmp_path)
    from autoresearch.scan.agents.l4_card import main
    rc = main(["harvest-slim", "2026-07-07"])
    assert rc == 0
    out = capsys.readouterr().out
    data = json.loads(out)
    assert data["ok"] is True
