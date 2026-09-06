#!/usr/bin/env python3
"""普查跑一次不许静默盖掉上一次的研究结论(验收 A05)。

治的病:`--out` 只管 markdown,JSON 走固定路径;而 markdown 的**默认值就是一份已提交的
读数文件**(`docs/research/2026-08-28-...-readout.md`)。于是 `--run` 一按,上一轮的读数
连同它的 JSON 一起没了 —— 「冻结 run 及历史报告不原地改写」这条不变量在这个入口是敞开的。

修法两件:①JSON 也可以指路(`--out-json`);②目标已存在则**拒绝**,除非显式 `--force`。
拒绝必须发生在建面板**之前** —— 面板要跑 40–90 分钟,烧完再报错等于没有守卫。
"""
from __future__ import annotations

import pytest

from autoresearch.research.overnight_census import __main__ as cli


def test_default_targets_are_the_historical_readout_and_engine_root_json():
    md, js = cli.resolve_outputs(force=True)
    assert md.name == "2026-08-28-overnight-concentrated-census-readout.md"
    assert md.parts[:2] == ("docs", "research")
    assert js.name == "_overnight_census.json"
    # JSON 落本引擎根,不跨引擎
    assert "research" in js.parts and "overnight_census" in js.parts


def test_existing_markdown_target_is_refused_without_force(tmp_path):
    md = tmp_path / "readout.md"
    md.write_text("上一轮的结论", encoding="utf-8")
    js = tmp_path / "out.json"
    with pytest.raises(FileExistsError) as err:
        cli.resolve_outputs(out=md, out_json=js)
    assert str(md) in str(err.value)


def test_existing_json_target_is_refused_without_force(tmp_path):
    md = tmp_path / "readout.md"
    js = tmp_path / "out.json"
    js.write_text("{}", encoding="utf-8")
    with pytest.raises(FileExistsError) as err:
        cli.resolve_outputs(out=md, out_json=js)
    assert str(js) in str(err.value)


def test_force_allows_overwrite_and_says_so_explicitly(tmp_path):
    md = tmp_path / "readout.md"
    js = tmp_path / "out.json"
    md.write_text("旧", encoding="utf-8")
    js.write_text("{}", encoding="utf-8")
    got_md, got_js = cli.resolve_outputs(out=md, out_json=js, force=True)
    assert (got_md, got_js) == (md, js)


def test_fresh_targets_need_no_force(tmp_path):
    md, js = cli.resolve_outputs(out=tmp_path / "a" / "r.md", out_json=tmp_path / "b" / "o.json")
    assert md.name == "r.md" and js.name == "o.json"


def test_refusal_happens_before_the_panel_is_built(tmp_path, monkeypatch):
    """守卫必须在建面板之前开火 —— 否则烧 40–90 分钟才报错,等于没有守卫。"""
    md = tmp_path / "readout.md"
    md.write_text("上一轮的结论", encoding="utf-8")

    def never(*args, **kwargs):
        raise AssertionError("目标已存在时不该走到建面板这一步")

    monkeypatch.setattr(cli.panel_mod, "build_panel", never)
    with pytest.raises(FileExistsError):
        cli.run_census(out=md, out_json=tmp_path / "o.json")


def test_cli_exposes_out_json_and_force_flags():
    """两个开关必须真接到 run_census 上,不能只在 argparse 里挂个名字。"""
    seen = {}

    def fake_run(**kwargs):
        seen.update(kwargs)
        return {}

    import autoresearch.research.overnight_census.__main__ as mod
    original = mod.run_census
    try:
        mod.run_census = fake_run
        mod.main(["--run", "--out", "x.md", "--out-json", "y.json", "--force"])
    finally:
        mod.run_census = original
    assert str(seen["out"]) == "x.md" and str(seen["out_json"]) == "y.json"
    assert seen["force"] is True
