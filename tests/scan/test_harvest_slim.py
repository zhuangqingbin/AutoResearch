import json

from autoresearch.scan.agents.l4_card import harvest_slim_batch


def _slim_body(pad: int = 0, close: str = "41.00", drop: str = "") -> str:
    """结构合格的 slim 正文(含四道结构锚 + 真 OHLCV Close)。

    `drop` = 要抠掉的锚(模拟 harvest 降级/NO_DATA);`pad` = 额外填充字节(只撑体积不加信息)。
    """
    blocks = {
        "snapshot": "## Verified market snapshot (source of truth)\n",
        "ohlcv": f"### Latest verified OHLCV row\n\n| Field | Value |\n|---|---:|\n| Close | {close} |\n",
        "market": "## Market context — A股 (主力/技术/筹码/北向 · 复用L1召回)\n**L1 召回复合分 36.7**\n",
        "fund": "## Fundamentals overview\n# Company Fundamentals\n",
    }
    blocks.pop(drop, None)
    return "\n".join(blocks.values()) + "\n" + "x" * pad


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
    """真垃圾(2KB,连结构锚都没有)仍要拦 —— 体积地板兜的是这个,不是"新闻少几条"。"""
    _setup(tmp_path, ["600584.SS", "000062.SZ"])

    def fake(t, dt):
        p = tmp_path / f"{t}_{dt}_slim.md"
        p.write_text(_slim_body(pad=20_000) if t == "600584.SS" else "x" * 2_000,
                     encoding="utf-8")
        return p

    res = harvest_slim_batch("2026-07-07", root=tmp_path, retries=0, harvest_fn=fake)
    assert res["ok"] is False
    assert [f["ticker"] for f in res["failures"]] == ["000062.SZ"]


def test_harvest_slim_all_ok(tmp_path):
    _setup(tmp_path, ["600584.SS"])

    def fake(t, dt):
        p = tmp_path / f"{t}_{dt}_slim.md"
        p.write_text(_slim_body(pad=20_000), encoding="utf-8")
        return p

    res = harvest_slim_batch("2026-07-07", root=tmp_path, retries=0, harvest_fn=fake)
    assert res["ok"] is True and res["failures"] == []


def test_harvest_slim_compact_but_complete_passes(tmp_path):
    """🚨 2026-07-14 生产回归:药石科技(300725)slim **8176B — 差 16 字节没够 8192B 体积门槛**,
    结构 24 节一个不缺、OHLCV/主力/筹码全真,只是当期新闻少几条 → 旧体积门槛误杀,
    整条流水线在 60min / 1.6M token / 33 agent 全完成之后被 GATE3 毙掉。

    体积门槛此前已被迫从 10_240B 下调到 8_192B(同类误杀),第三次下调不是修复是棘轮 ——
    **规模检查与结构检查必须分开**:能不能用由结构+内容决定,体积只兜真垃圾。
    """
    _setup(tmp_path, ["300725.SZ"])

    def fake(t, dt):
        p = tmp_path / f"{t}_{dt}_slim.md"
        body = _slim_body()
        p.write_text(body + "x" * (8_176 - len(body.encode())), encoding="utf-8")
        return p

    res = harvest_slim_batch("2026-07-07", root=tmp_path, retries=0, harvest_fn=fake)
    assert res["ok"] is True, f"完整但紧凑的 slim 不得被拦:{res['failures']}"


def test_harvest_slim_catches_structural_degrade(tmp_path):
    """体积够大但结构缺块(harvest 降级/NO_DATA)必须拦 —— 这才是 GATE3 真正要防的。

    对照 [[ashare-harvest-sh-ss-suffix-bug]]:.SS 后缀错 → 空 slim,光看体积可能蒙混过关。
    """
    _setup(tmp_path, ["600584.SS"])

    def fake(t, dt):
        p = tmp_path / f"{t}_{dt}_slim.md"
        p.write_text(_slim_body(pad=20_000, drop="ohlcv"), encoding="utf-8")   # 20KB 但没行情
        return p

    res = harvest_slim_batch("2026-07-07", root=tmp_path, retries=0, harvest_fn=fake)
    assert res["ok"] is False
    assert "结构" in res["failures"][0]["why"] or "OHLCV" in res["failures"][0]["why"]


def test_harvest_slim_catches_empty_ohlcv(tmp_path):
    """结构锚齐但 OHLCV 无数值(NO_DATA 占位)→ 拦。光查节标题不够,要查内容。"""
    _setup(tmp_path, ["600584.SS"])

    def fake(t, dt):
        p = tmp_path / f"{t}_{dt}_slim.md"
        p.write_text(_slim_body(pad=20_000, close="NO_DATA"), encoding="utf-8")
        return p

    res = harvest_slim_batch("2026-07-07", root=tmp_path, retries=0, harvest_fn=fake)
    assert res["ok"] is False


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
