"""slim 二段式:深核块分离到 *_slim_deep.md(spec 2026-07-08 T1;取代旧同文件重排)。"""
import sys
from pathlib import Path

import pytest

from autoresearch.analyze import harvest
from autoresearch.analyze.harvest import _split_slim_for_progressive, _write_slim_files
from autoresearch.common import workspace as ws
from tests.forensic_fixtures import FIXTURE_DATE, begin_fixture_run


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


def test_standalone_slim_output_ignores_ambient_run_id(tmp_path, monkeypatch):
    """独立 slim(无 `--out-dir`)必须一律落 `ws.context_root()`(D8.5)。

    此前这里的行为是「按 `AUTORESEARCH_RUN_ID` 隔离」——同一日期、不同 run_id 会
    各自拿到不同目录。那个隔离手法正是要修的病本身:一次真正独立的 slim 调用不该
    因为 shell 里恰好留着一个无关 run 的 `AUTORESEARCH_RUN_ID` 就被悄悄路由进
    那趟 run 的 `_external_inputs/`——两次调用现在必须落在同一个目录。
    """
    monkeypatch.setattr(harvest, "ROOT", tmp_path)
    monkeypatch.setattr(ws, "ENGINE", "codex")
    date = "2026-08-27"

    monkeypatch.setenv("AUTORESEARCH_RUN_ID", "20260827T010203456789Z")
    first_dir = harvest._output_dir(date, slim=True)

    monkeypatch.setenv("AUTORESEARCH_RUN_ID", "20260827T020304567890Z")
    second_dir = harvest._output_dir(date, slim=True)

    assert first_dir == second_dir == tmp_path / "context_codex"


def test_standalone_slim_never_writes_into_run(tmp_path, monkeypatch):
    """设了 RUN_ID 时 `_output_dir(...)` 结果不含 `scan_runs`(D8.5)。

    `AUTORESEARCH_RUN_ID` 指向一趟**真实存在**的 scan run(目录已建)时,
    `active_run_kind()` 会稳稳判成 `"scan-market"`——这是最贴近真实污染场景的
    构造(不是靠 `active_run_kind()` 的兜底默认值凑出来的)。
    """
    monkeypatch.setattr(harvest, "ROOT", tmp_path)
    monkeypatch.setattr(ws, "ENGINE", "codex")
    run_id = "20260827T010203456789Z"
    (tmp_path / "context_codex" / "scan_runs" / run_id).mkdir(parents=True)
    monkeypatch.setenv("AUTORESEARCH_RUN_ID", run_id)
    assert ws.active_run_kind() == "scan-market"  # premise: 真的命中了一趟活跃 run

    out_dir = harvest._output_dir("2026-08-27", slim=True)

    assert "scan_runs" not in str(out_dir)
    assert out_dir == tmp_path / "context_codex"


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
    # This is an explicit scratch-output CLI test, not a tracked run.  A made-up
    # run id is no longer a harmless decoration: tracked identities fail closed.
    monkeypatch.delenv("AUTORESEARCH_RUN_ID", raising=False)
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


def test_slim_cli_invalid_date_does_not_create_explicit_output_dir(tmp_path, monkeypatch):
    output_dir = tmp_path / "must_not_be_created"
    monkeypatch.setattr(sys, "argv", [
        "harvest",
        "NVDA",
        "2026-02-30",
        "stock",
        "--slim",
        "--out-dir",
        str(output_dir),
    ])

    with pytest.raises(ValueError):
        harvest.main()

    assert not output_dir.exists()


def _hermetic_harvest_in_active_scan_run(tmp_path, monkeypatch, argv_tail: list[str]):
    """一趟真实活跃的 scan run + `AUTORESEARCH_RUN_ID` 指向它 —— 与生产里 exec_capture
    给 `l4_tasks prepare` 子进程注入的环境同形;取数面全部换成桩,只留写窗守卫是真的。"""
    handle = begin_fixture_run(tmp_path, monkeypatch)
    monkeypatch.setenv("AUTORESEARCH_RUN_ID", handle.run_id)
    monkeypatch.setattr(harvest, "ROOT", tmp_path / "repo")
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
    monkeypatch.setattr(sys, "argv", ["harvest", "NVDA", FIXTURE_DATE, "stock", *argv_tail])
    return handle


def test_scan_l4_slim_writes_into_its_own_active_scan_run(tmp_path, monkeypatch):
    """scan L4 的 slim 生产者(`scan/l4/producers._default_harvest_slim`)在活跃 scan run 里
    显式 `--out-dir <staging>/_external_inputs` 调 harvest —— 那是 scan 自己的写。

    2026-09-14 首跑:harvest 的写窗一律报 `stock.harvest`,守卫判「不属于 scan-market」,
    7 只 finalist 的 slim 全部没写出来,整层 L4 BLOCKED。
    """
    handle = _hermetic_harvest_in_active_scan_run(tmp_path, monkeypatch, ["--slim"])
    output_dir = Path(handle.staging) / "_external_inputs"
    monkeypatch.setattr(sys, "argv", [*sys.argv, "--out-dir", str(output_dir)])

    assert harvest.main() == 0
    assert (output_dir / f"NVDA_{FIXTURE_DATE}_slim.md").is_file()


def test_scan_run_slim_refuses_output_outside_its_own_staging(tmp_path, monkeypatch):
    """scan 的写窗只覆盖本 run 的 staging:显式 `--out-dir` 指向别处 = 拒写,且目录不被创建。"""
    outside = tmp_path / "elsewhere" / "_external_inputs"
    _hermetic_harvest_in_active_scan_run(
        tmp_path, monkeypatch, ["--slim", "--out-dir", str(outside)])

    with pytest.raises(RuntimeError, match="OUTPUT_ROOT_MISMATCH"):
        harvest.main()

    assert not outside.exists()


def test_standalone_harvest_under_ambient_scan_run_stays_refused(tmp_path, monkeypatch):
    """不带 `--out-dir` 的 harvest 是 stock-research 自己的写;shell 里恰好留着一个活跃
    scan run 的 `AUTORESEARCH_RUN_ID` 时仍须拒绝(262f058 要挡的正是这种跨 workflow 写)。"""
    _hermetic_harvest_in_active_scan_run(tmp_path, monkeypatch, ["--slim"])

    with pytest.raises(RuntimeError, match="RUN_OPERATION_NOT_OWNED"):
        harvest.main()

    assert not (tmp_path / "context_codex" / f"NVDA_{FIXTURE_DATE}_slim.md").exists()
