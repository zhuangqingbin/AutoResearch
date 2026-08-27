"""slim 二段式:深核块分离到 *_slim_deep.md(spec 2026-07-08 T1;取代旧同文件重排)。"""
import sys

import pytest

from autoresearch.analyze import harvest
from autoresearch.analyze.harvest import _split_slim_for_progressive, _write_slim_files
from autoresearch.common import workspace as ws


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


def test_slim_files_are_isolated_between_same_date_runs(tmp_path, monkeypatch):
    monkeypatch.setattr(harvest, "ROOT", tmp_path)
    monkeypatch.setattr(ws, "ENGINE", "codex")
    date = "2026-08-27"

    monkeypatch.setenv("AUTORESEARCH_RUN_ID", "20260827T010203456789Z")
    first_dir = harvest._output_dir(date, slim=True)
    first = _write_slim_files(first_dir, "000062.SZ", date, _parts())
    first_text = first.read_text(encoding="utf-8")

    monkeypatch.setenv("AUTORESEARCH_RUN_ID", "20260827T020304567890Z")
    second_dir = harvest._output_dir(date, slim=True)
    second = _write_slim_files(second_dir, "000062.SZ", date, ["# second run"])

    assert first_dir != second_dir
    assert first.read_text(encoding="utf-8") == first_text
    assert second.read_text(encoding="utf-8") == "# second run"


def test_full_report_output_stays_at_engine_context_root(tmp_path, monkeypatch):
    monkeypatch.setattr(harvest, "ROOT", tmp_path)
    monkeypatch.setattr(ws, "ENGINE", "codex")
    monkeypatch.setenv("AUTORESEARCH_RUN_ID", "20260827T010203456789Z")

    output_dir = harvest._output_dir("2026-08-27", slim=False)

    assert output_dir == tmp_path / "context_codex"
    assert output_dir.is_dir()


def test_slim_cli_writes_to_explicit_output_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(harvest, "ROOT", tmp_path / "repo")
    monkeypatch.setattr(ws, "ENGINE", "codex")
    monkeypatch.setenv("AUTORESEARCH_RUN_ID", "20260827T010203456789Z")
    monkeypatch.setattr(harvest, "set_config", lambda _config: None)
    monkeypatch.setattr(harvest, "resolve_instrument_identity", lambda _ticker: None)
    monkeypatch.setattr(
        harvest,
        "build_instrument_context",
        lambda _ticker, _asset_type, _identity: "identity",
    )
    monkeypatch.setattr(
        harvest,
        "_section",
        lambda title, *_args, **_kwargs: f"\n## {title}\n\ntest data\n",
    )
    output_dir = tmp_path / "explicit" / "_external_inputs"
    monkeypatch.setattr(sys, "argv", [
        "harvest",
        "NVDA",
        "2026-08-27",
        "stock",
        "--slim",
        "--out-dir",
        str(output_dir),
    ])

    assert harvest.main() == 0
    assert (output_dir / "NVDA_2026-08-27_slim.md").is_file()


def test_full_report_rejects_explicit_output_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(harvest, "ROOT", tmp_path / "repo")
    output_dir = tmp_path / "must_not_be_created"

    with pytest.raises(ValueError, match="--out-dir.*--slim"):
        harvest._output_dir("2026-08-27", slim=False, explicit=output_dir)

    assert not output_dir.exists()


def test_full_cli_rejects_output_dir_before_harvest_setup(tmp_path, monkeypatch):
    monkeypatch.setattr(sys, "argv", [
        "harvest",
        "NVDA",
        "2026-08-27",
        "stock",
        "--out-dir",
        str(tmp_path / "forbidden"),
    ])
    monkeypatch.setattr(
        harvest,
        "set_config",
        lambda _config: (_ for _ in ()).throw(AssertionError("harvest setup started")),
    )

    with pytest.raises(ValueError, match="--out-dir.*--slim"):
        harvest.main()
