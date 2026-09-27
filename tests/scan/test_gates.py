import csv
import json

import pandas as pd

from autoresearch.common import workspace as ws
from autoresearch.scan.gates import gate1, gate1_decide, gate2, gate4


def test_gate1_flags_bad_codes(tmp_path):
    # 前导零已丢(62 而非 000062)→ 必须拦
    pd.DataFrame({"code": [62, 63], "pct_60d": [1.0, 2.0], "main_net_ratio": [0.0, 0.0],
                  "cmf_20": [0.0, 0.0]}).to_csv(tmp_path / "L2_gbdt_top200.csv", index=False)
    r = gate1(tmp_path)
    assert r["ok"] is False
    assert "非 6 位" in r["reason"]                    # 锁定失败原因,非仅失败与否


def test_gate1_missing_l2(tmp_path):
    assert gate1(tmp_path)["ok"] is False


def test_gate1_happy_path(tmp_path):
    # L1_scored_full 齐全 → sentinel_advice 走真计算(非"无 L1"降级路径);L2 只需 6 位 code 过校验
    n, k = 200, 12                                     # 6% 健康占比 → full(07-02 口径,同 test_sentinel_tokens)
    rows = [{"code": f"{i:06d}", "pct_60d": 15.0, "main_net_ratio": 0.05, "cmf_20": 0.1}
            for i in range(k)]
    rows += [{"code": f"{i:06d}", "pct_60d": -30.0, "main_net_ratio": -0.01, "cmf_20": -0.1}
             for i in range(k, n)]
    pd.DataFrame(rows).to_csv(tmp_path / "L1_scored_full.csv", index=False)
    pd.DataFrame({"code": ["000001", "000002", "000003"]}).to_csv(
        tmp_path / "L2_gbdt_top200.csv", index=False)
    r = gate1(tmp_path)
    assert r["ok"] is True
    assert isinstance(r["sentinel_level"], str)
    assert isinstance(r["l4_budget"], int)


def test_gate1_decide_writes_full_mode_and_preserves_budget(tmp_path, monkeypatch):
    from autoresearch.scan import gates, run_mode

    monkeypatch.setattr(gates, "gate1", lambda _scan: {
        "ok": True, "gate": "gate1", "reason": "ok", "sentinel_level": "full",
        "sentinel_reason": "healthy", "l4_budget": 7, "l2_n": 200,
    })
    monkeypatch.setattr(run_mode, "pinned_from_contract", lambda _scan: ([], "h" * 64))
    result = gate1_decide(tmp_path)
    assert result["ok"] is True and result["l4_budget"] == 7
    assert result["run_mode"]["mode"] == "FULL"
    assert json.loads((tmp_path / "run_mode.json").read_text())["mode"] == "FULL"


def test_gate1_decide_force_full_is_explicit(tmp_path, monkeypatch):
    from autoresearch.scan import gates, run_mode

    monkeypatch.setattr(gates, "gate1", lambda _scan: {
        "ok": True, "gate": "gate1", "reason": "ok", "sentinel_level": "sentinel",
        "sentinel_reason": "thin menu", "l4_budget": 3, "l2_n": 200,
    })
    monkeypatch.setattr(run_mode, "pinned_from_contract", lambda _scan: ([], None))
    result = gate1_decide(tmp_path, force_full=True)
    assert result["run_mode"]["mode"] == "FORCED_FULL"
    assert result["run_mode"]["sentinel_reason"] == "thin menu"


def test_gate1_decide_does_not_run_mode_after_gate_failure(tmp_path, monkeypatch):
    from autoresearch.scan import gates, run_mode

    monkeypatch.setattr(gates, "gate1", lambda _scan: {
        "ok": False, "gate": "gate1", "reason": "bad L2",
    })
    monkeypatch.setattr(run_mode, "decide", lambda **_kw: (_ for _ in ()).throw(
        AssertionError("run mode must not be decided after a failed gate")))
    result = gate1_decide(tmp_path)
    assert result == {"ok": False, "gate": "gate1", "reason": "bad L2"}
    assert not (tmp_path / "run_mode.json").exists()


def test_gate1_decide_fails_the_whole_gate_when_run_mode_cannot_be_written(
    tmp_path, monkeypatch, capsys,
):
    """模式判定/落盘失败 = 整道 GATE1 FAILED(rc=1),不是"门过了、模式待定"。

    合并后模式是 GATE1 这笔事务的一部分。若这里放行,workflow 会拿着一个**没落盘的**模式
    往下跑:run_mode.json 缺席时 `gate4`/`assemble`/报告横幅一律按"不知道"处理,而流水线
    却已经按某个猜测的模式跑完了 L3/L4 —— 账上与现场两张皮。
    """
    d = tmp_path / ws.scan_root() / "2026-07-28"
    d.mkdir(parents=True)
    pd.DataFrame({"code": ["000001", "000002"]}).to_csv(d / "L2_gbdt_top200.csv", index=False)
    monkeypatch.chdir(tmp_path)
    from autoresearch.scan import run_mode
    from autoresearch.scan.gates import main
    from autoresearch.scan.stage_result import load_stage_result

    def _boom(*_a, **_kw):
        raise OSError("Read-only file system")

    monkeypatch.setattr(run_mode, "write", _boom)
    rc = main(["gate1", "2026-07-28", "--decide-run-mode"])
    legacy = json.loads(capsys.readouterr().out)
    result = load_stage_result(d / "stage_results" / "gate1.json")

    assert rc == 1 and legacy["ok"] is False
    assert "run_mode" in legacy["reason"] and "Read-only file system" in legacy["reason"]
    assert result.status == "FAILED" and result.error == legacy["reason"]
    assert not (d / "run_mode.json").exists()


def test_gate1_decide_cli_hands_run_mode_to_the_workflow_through_stage_metrics(
    tmp_path, monkeypatch, capsys,
):
    """stage metrics 是 workflow 读模式的**唯一**路径(合并后不再有独立 run-mode 壳)。

    这条线断了,scan-market.js 的 `RUN_MODES.includes(runMode)` 守卫会在每次真跑的 GATE1
    之后硬停 —— 几十分钟 + 真金 token 之后才发现。
    """
    d = tmp_path / ws.scan_root() / "2026-07-28"
    d.mkdir(parents=True)
    pd.DataFrame({"code": ["000001", "000002"]}).to_csv(d / "L2_gbdt_top200.csv", index=False)
    monkeypatch.chdir(tmp_path)
    from autoresearch.scan.gates import main
    from autoresearch.scan.run_mode import MODES
    from autoresearch.scan.stage_result import load_stage_result

    rc = main(["gate1", "2026-07-28", "--decide-run-mode"])
    result = load_stage_result(d / "stage_results" / "gate1.json")

    assert rc == 0 and result.status == "SUCCEEDED"
    assert result.metrics["run_mode"]["mode"] in MODES
    assert result.metrics["run_mode"]["mode"] == json.loads(
        (d / "run_mode.json").read_text(encoding="utf-8"))["mode"]
    # 判据随模式一起带回 —— 旧路径没传 `--sentinel-reason`,横幅一直印「判据:—」
    assert "sentinel_reason" in result.metrics


def test_gate1_cli_without_the_flag_does_not_decide_run_mode(tmp_path, monkeypatch, capsys):
    """不带 `--decide-run-mode` 的 gate1 保持原样:只校验,不写模式(Codex 会话内仍这么调)。"""
    d = tmp_path / ws.scan_root() / "2026-07-28"
    d.mkdir(parents=True)
    pd.DataFrame({"code": ["000001", "000002"]}).to_csv(d / "L2_gbdt_top200.csv", index=False)
    monkeypatch.chdir(tmp_path)
    from autoresearch.scan.gates import main
    from autoresearch.scan.stage_result import load_stage_result

    rc = main(["gate1", "2026-07-28"])
    legacy = json.loads(capsys.readouterr().out)
    assert rc == 0 and "run_mode" not in legacy
    assert not (d / "run_mode.json").exists()
    assert "run_mode" not in load_stage_result(d / "stage_results" / "gate1.json").metrics


def test_run_mode_flags_are_refused_outside_gate1(tmp_path, monkeypatch):
    """`--decide-run-mode` 只属于 gate1;`--force-full` 离开它就没有意义。

    静默忽略比报错坏:调用方会以为模式已经判了。这两条 argparse 守卫没人测就会被顺手删掉。
    """
    import pytest

    monkeypatch.chdir(tmp_path)
    from autoresearch.scan.gates import main

    for argv in (["gate2", "2026-07-28", "--decide-run-mode"],
                 ["gate1", "2026-07-28", "--force-full"]):
        with pytest.raises(SystemExit) as exc:
            main(argv)
        assert exc.value.code == 2                     # argparse 用法错误,不是门失败(rc=1)


def test_gate2_ok_returns_finalists(tmp_path):
    pd.DataFrame({"code": ["000062", "600584"], "ticker": ["000062", "600584.SS"]}).to_csv(
        tmp_path / "finalists.csv", index=False)
    r = gate2(tmp_path, budget=30)
    assert r["ok"] is True and r["finalists"] == ["000062", "600584"] and r["n"] == 2


def test_gate2_over_budget(tmp_path):
    pd.DataFrame({"code": [f"{i:06d}" for i in range(5)]}).to_csv(
        tmp_path / "finalists.csv", index=False)
    assert gate2(tmp_path, budget=3)["ok"] is False


def test_gate2_flags_bad_codes(tmp_path):
    # 回归测试(Task 3 复审 #1):gate2 曾先 zfill(6) 再校验,"62" 被悄悄补成 "000062"
    # → 前导零丢失坑永远拦不住。改为先校验原始码(zfill 之前),与 gate1 同口径。
    pd.DataFrame({"code": ["62", "600584"]}).to_csv(tmp_path / "finalists.csv", index=False)
    assert gate2(tmp_path, budget=30)["ok"] is False


def _gate_fires(tmp_path, rows):
    with (tmp_path / "gate_fires.csv").open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["date", "code", "check", "severity", "detail"])
        w.writeheader()
        for r in rows:
            w.writerow(r)


def test_gate4_passes_when_no_fail(tmp_path):
    _gate_fires(tmp_path, [])                                    # 空表 = 自检通过
    assert gate4(tmp_path)["ok"] is True


def test_gate4_passes_when_only_warn_rows(tmp_path):
    # warn 不是 fail —— self_review 常见输出(见 self_review.py 的 severity="warn" 行),不应挡门
    _gate_fires(tmp_path, [{"date": "2026-07-07", "code": "000001", "check": "PE偏高",
                            "severity": "warn", "detail": "PE 80"}])
    assert gate4(tmp_path)["ok"] is True


def test_gate4_fails_on_fail_row(tmp_path):
    _gate_fires(tmp_path, [{"date": "2026-07-07", "code": "", "check": "覆盖率不足",
                            "severity": "fail", "detail": "卡片 5/20"}])
    assert gate4(tmp_path)["ok"] is False


def test_gate4_missing_file(tmp_path):
    assert gate4(tmp_path)["ok"] is False                        # assemble 没跑


def test_gate2_cli_flags_bad_codes(tmp_path, monkeypatch, capsys):
    # workflow 经 Bash-agent 调 main() 读 JSON+退出码;坏码必须 rc=1 且 JSON.ok=False
    d = tmp_path / ws.scan_root() / "2026-07-07"
    d.mkdir(parents=True)
    pd.DataFrame({"code": ["62", "600584"]}).to_csv(d / "finalists.csv", index=False)
    monkeypatch.chdir(tmp_path)
    from autoresearch.scan.gates import main
    rc = main(["gate2", "2026-07-07", "--budget", "30"])
    assert rc == 1
    assert json.loads(capsys.readouterr().out)["ok"] is False


def test_gate_cli_writes_success_stage_result_without_changing_stdout(
    tmp_path, monkeypatch, capsys,
):
    d = tmp_path / ws.scan_root() / "2026-07-28"
    d.mkdir(parents=True)
    pd.DataFrame({"code": ["000001"]}).to_csv(d / "finalists.csv", index=False)
    monkeypatch.chdir(tmp_path)
    from autoresearch.scan.gates import main
    from autoresearch.scan.stage_result import load_stage_result

    rc = main(["gate2", "2026-07-28", "--budget", "10"])
    legacy = json.loads(capsys.readouterr().out)
    result = load_stage_result(d / "stage_results" / "gate2.json")

    assert rc == 0
    assert legacy["ok"] is True and legacy["finalists"] == ["000001"]
    assert "status" not in legacy
    assert result.status == "SUCCEEDED"
    assert result.artifacts == ["finalists"]
    assert result.metrics == {
        "budget": 10,
        "finalists": ["000001"],
        "meta": {"000001": {"name": "", "sector": ""}},
        "n": 1,
    }
    assert result.error is None


def test_gate_cli_writes_failed_stage_result_and_keeps_rc_one(tmp_path, monkeypatch, capsys):
    d = tmp_path / ws.scan_root() / "2026-07-28"
    d.mkdir(parents=True)
    monkeypatch.chdir(tmp_path)
    from autoresearch.scan.gates import main
    from autoresearch.scan.stage_result import load_stage_result

    rc = main(["gate1", "2026-07-28"])
    legacy = json.loads(capsys.readouterr().out)
    result = load_stage_result(d / "stage_results" / "gate1.json")

    assert rc == 1 and legacy["ok"] is False
    assert result.status == "FAILED"
    assert result.artifacts == []
    assert result.error == legacy["reason"]


# ───────────────────────── L3.5 完全移除(2026-07-12 用户裁定):GATE2 只读校验 ─────────────────────────
#
# design: docs/specs/2026-07-12-funnel-replay-l35-removal-design.md §1
# L3 finalist tier 即 L4 入选集,GATE2 不再收窄、不写任何文件;闸回显键(l4_gate/l35_cut_n)随闸移除。


def test_gate2_is_read_only_and_has_no_gate_echo_keys(tmp_path):
    """L3.5 移除后的行为锁:GATE2 逐字节不改 finalists.csv、不落 _l35_cut.csv,
    返回 JSON 不含 l4_gate/l35_cut_n 键(workflow GATE2 schema 已同步删键)。"""
    fp = tmp_path / "finalists.csv"
    pd.DataFrame({"code": ["000001", "000002"], "conviction": [10.0, 90.0],
                  "lane": ["trend", "value"]}).to_csv(fp, index=False)
    before = fp.read_bytes()
    r = gate2(tmp_path, budget=30)
    assert r["ok"] is True
    assert r["finalists"] == ["000001", "000002"]
    assert "l4_gate" not in r and "l35_cut_n" not in r
    assert fp.read_bytes() == before, "GATE2 只读:不得改写 finalists.csv"
    assert not (tmp_path / "_l35_cut.csv").exists()


# ───────────────────────── C-1 回归:GATE2 预算计数排除 exempt lane ─────────────────────────
#
# final-review-l3-merge.md Critical-1:pinned 强留行注入在 v3 cap 之后(不占 finalist tier
# 名额)——但 GATE2 原实现数的是 finalists.csv 全行数,满员日(cap=10)+1 只 pinned 即
# 11>10 硬失败。修复:GATE2 计数排除 `lane` 命中 `_EXEMPT_LANES`
# ({"pinned","watchlist_trigger"})的行。
# 注:`carryover` 曾是第三个 exempt lane,随该机制 2026-07-16 退役移出(pr_20260716_006)。


def test_gate2_pinned_row_does_not_count_against_budget(tmp_path):
    """真值复现(终审报告实证场景):10 只普通 finalist(满 cap)+ 1 只 pinned 强留行
    → budget=10 时不应再挂,ok 必须为 True,且 pinned 行仍完整出现在 codes/n 里
    (它确实要送 L4,只是不占『门』的坑)。"""
    rows = [{"code": f"{i:06d}", "conviction": 90 - i, "lane": "trend"} for i in range(10)]
    rows.append({"code": "600519", "conviction": 10.0, "lane": "pinned"})
    pd.DataFrame(rows).to_csv(tmp_path / "finalists.csv", index=False)
    r = gate2(tmp_path, budget=10)
    assert r["ok"] is True, f"pinned 行不应占 GATE2 名额,实际:{r}"
    assert r["n"] == 11                                   # 全量行数(含 pinned)如实回显
    assert set(r["finalists"]) == {f"{i:06d}" for i in range(10)} | {"600519"}


def test_gate2_pinned_row_still_fails_when_non_exempt_rows_alone_exceed_budget(tmp_path):
    """反向:即便排除 pinned,非豁免行本身已超预算 → 仍应失败(exempt 只是不占名额,
    不是把预算变大)。"""
    rows = [{"code": f"{i:06d}", "conviction": 90 - i, "lane": "trend"} for i in range(11)]
    rows.append({"code": "600519", "conviction": 10.0, "lane": "pinned"})
    pd.DataFrame(rows).to_csv(tmp_path / "finalists.csv", index=False)
    r = gate2(tmp_path, budget=10)
    assert r["ok"] is False
    assert "11" in r["reason"]                            # 失败原因数的是排除 pinned 后的 11,非 12


def test_gate2_watchlist_trigger_row_also_exempt_from_budget(tmp_path):
    """纵深防御:即便 watchlist_trigger 行在 GATE2 之前就已出现在 finalists.csv 里
    (当前生产时序下不会,见 gates.py 核查笔记),也应同样不计入预算——`_EXEMPT_LANES`
    两 lane 统一语义。"""
    rows = [{"code": f"{i:06d}", "conviction": 90 - i, "lane": "trend"} for i in range(10)]
    rows.append({"code": "000901", "conviction": 5.0, "lane": "watchlist_trigger"})
    pd.DataFrame(rows).to_csv(tmp_path / "finalists.csv", index=False)
    r = gate2(tmp_path, budget=10)
    assert r["ok"] is True
    assert r["n"] == 11


def test_gate2_no_lane_column_counts_all_rows_unaffected_by_exempt_logic(tmp_path):
    """无 `lane` 列(退化态,如旧 finalists.csv)→ exempt 判据整体跳过,行为与修复前一致
    (全行数与 budget 比较)。"""
    pd.DataFrame({"code": [f"{i:06d}" for i in range(11)]}).to_csv(
        tmp_path / "finalists.csv", index=False)
    assert gate2(tmp_path, budget=10)["ok"] is False


# ───────────────────────── P4a: GATE2 返回 meta{code:{name,sector}} ─────────────────────────
#
# task-7-brief.md:GATE2 成功 JSON 增 meta 字段(每只 finalist 的 name/sector),
# workflow(Task 9)据此在 GATE2 后立即派发 l4-intel,不必让每个 intel subagent 自己
# 回查 finalists.csv。契约:只加在成功分支,失败分支 JSON 不变(仍只 ok/gate/reason)。


def test_gate2_returns_meta(tmp_path):
    import pandas as pd

    from autoresearch.scan.gates import gate2
    scan_dir = tmp_path
    pd.DataFrame({"code": ["603259", "000567"], "ticker": ["603259", "000567"],
                  "name": ["药明康德", "海德股份"], "sector": ["医疗服务", "多元金融"],
                  "lane": ["trend", "value"]}).to_csv(scan_dir / "finalists.csv", index=False)
    res = gate2(scan_dir, budget=10)
    assert res["ok"]
    assert res["meta"]["603259"] == {"name": "药明康德", "sector": "医疗服务"}
    assert set(res["meta"]) == {"603259", "000567"}


def test_gate2_meta_missing_cols_empty_strings(tmp_path):
    # name/sector 整列缺失(旧格式 finalists.csv)→ _s helper 靠 Series.get 兜底 None → 空串,
    # 不抛 KeyError(区别于 r["name"] 直接下标访问在列缺失时会抛异常)。
    import pandas as pd

    from autoresearch.scan.gates import gate2
    pd.DataFrame({"code": ["603259"], "ticker": ["603259"], "lane": ["trend"]}
                 ).to_csv(tmp_path / "finalists.csv", index=False)
    res = gate2(tmp_path, budget=10)
    assert res["ok"] and res["meta"]["603259"] == {"name": "", "sector": ""}


# ───────── L4 卡数旋钮(2026-09-26):GATE1 回显 effective_caps,消费方只读 ─────────

def _gate1_dir(tmp_path, codes):
    d = tmp_path / "2026-09-17"
    d.mkdir()
    pd.DataFrame({"code": codes}).to_csv(d / "L2_gbdt_top200.csv", index=False)
    return d


def test_gate1_echoes_l3cap_from_effective_caps(tmp_path, monkeypatch):
    d = _gate1_dir(tmp_path, ["000001", "600000"])
    monkeypatch.setattr("autoresearch.scan.user_config.load_user_config",
                        lambda path=None: {"l4": {"max_cards": 5},
                                           "l3": {"composite_seat": {"enabled": True, "m": 3}}})
    monkeypatch.setattr("autoresearch.scan.menu.l4_budget", lambda scan_dir, **kw: (30, "菜单健康"))
    monkeypatch.setattr("autoresearch.scan.menu.sentinel_advice", lambda scan_dir, **kw: ("full", "ok"))
    res = gate1(d)
    assert res["ok"] and res["l4_budget"] == 30
    assert res["l3cap"] == 2 and res["max_cards"] == 5 and res["budget_flags"] is True


def test_gate1_budget_flags_false_keeps_budget_for_display_only(tmp_path, monkeypatch):
    d = _gate1_dir(tmp_path, ["000001"])
    monkeypatch.setattr("autoresearch.scan.user_config.load_user_config",
                        lambda path=None: {"l4": {"max_cards": 20, "budget_flags": False},
                                           "l3": {"composite_seat": {"enabled": False, "m": 3}}})
    monkeypatch.setattr("autoresearch.scan.menu.l4_budget", lambda scan_dir, **kw: (15, "⚠️ 两旗"))
    monkeypatch.setattr("autoresearch.scan.menu.sentinel_advice", lambda scan_dir, **kw: ("full", "ok"))
    res = gate1(d)
    assert res["l4_budget"] == 15 and res["l3cap"] == 20      # 旗只留痕,不压 l3cap


def test_gate1_stage_result_metrics_carry_card_caps(tmp_path, monkeypatch):
    """GATE1 的 STAGE_RESULT.metrics 是 Workflow 读 g1m 的来源:键白名单必须带出 l3cap/max_cards。"""
    from autoresearch.scan.gates import record_gate_stage_result

    seen = {}
    monkeypatch.setattr("autoresearch.scan.stage_result.safe_record_stage_result",
                        lambda scan_dir, **kw: seen.update(kw))
    record_gate_stage_result(tmp_path, {"ok": True, "gate": "gate1", "l4_budget": 30, "l2_n": 200,
                                        "l3cap": 10, "max_cards": 13, "budget_flags": True})
    assert {"l3cap": 10, "max_cards": 13, "budget_flags": True}.items() <= seen["metrics"].items()
