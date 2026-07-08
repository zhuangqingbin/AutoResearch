"""slim 二段式:深核块分离到 *_slim_deep.md(spec 2026-07-08 T1;取代旧同文件重排)。"""
from autoresearch.analyze.harvest import _split_slim_for_progressive, _write_slim_files


def _parts():
    return [
        "# Data context — X\n",
        "\n## Instrument identity\n\nA\n",
        "\n## Verified market snapshot (source of truth)\n\nB\n",
        "\n## Income statement (quarterly)\n\nDEEP1\n",
        "\n## Ticker news 2026-07-01 → 2026-07-08\n\nC\n",
        "\n## Earnings quality / forensics (v3)\n\nDEEP2\n",
        "\n## Solvency & refinancing (v4)\n\nDEEP3\n",
    ]


def test_split_separates_three_deep_blocks():
    surface, deep = _split_slim_for_progressive(_parts())
    assert len(deep) == 3 and all("DEEP" in p for p in deep)
    assert all("DEEP" not in p for p in surface)


def test_split_surface_order_preserved():
    surface, _ = _split_slim_for_progressive(_parts())
    assert "Instrument identity" in surface[1]
    assert "Ticker news" in surface[3]


def test_split_no_deep_passthrough():
    only_surface = [p for p in _parts() if "DEEP" not in p]
    surface, deep = _split_slim_for_progressive(only_surface)
    assert surface == only_surface and deep == []


def test_write_slim_files_two_files_and_pointer(tmp_path):
    out = _write_slim_files(tmp_path, "000062.SZ", "2026-07-08", _parts())
    deep_f = tmp_path / "000062.SZ_2026-07-08_slim_deep.md"
    assert out == tmp_path / "000062.SZ_2026-07-08_slim.md" and deep_f.exists()
    surface_txt = out.read_text(encoding="utf-8")
    assert "DEEP" not in surface_txt                       # 深核不在表面文件
    assert "000062.SZ_2026-07-08_slim_deep.md" in surface_txt  # 尾指针指向 deep
    assert "DEEP1" in deep_f.read_text(encoding="utf-8")


def test_write_slim_files_no_deep_single_file(tmp_path):
    only_surface = [p for p in _parts() if "DEEP" not in p]
    out = _write_slim_files(tmp_path, "600519.SS", "2026-07-08", only_surface)
    assert not (tmp_path / "600519.SS_2026-07-08_slim_deep.md").exists()
    assert "深核分界" not in out.read_text(encoding="utf-8")   # 老路不插指针
