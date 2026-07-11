import csv
import json

import pandas as pd

from autoresearch.scan.gates import gate1, gate2, gate4


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
    d = tmp_path / "context" / "scan" / "2026-07-07"
    d.mkdir(parents=True)
    pd.DataFrame({"code": ["62", "600584"]}).to_csv(d / "finalists.csv", index=False)
    monkeypatch.chdir(tmp_path)
    from autoresearch.scan.gates import main
    rc = main(["gate2", "2026-07-07", "--budget", "30"])
    assert rc == 1
    assert json.loads(capsys.readouterr().out)["ok"] is False


# ───────────────────────── L3.5 可插拔闸接线(GATE2 内,plan A2 Task 3/4) ─────────────────────────
#
# design: docs/specs/2026-07-11-recall-gate-pinned-config-design.md §3
# plan:   docs/plans/2026-07-11-l35-gate-backtest-plan.md Task 4
#
# user_config_path 显式注入(不 chdir):隔离真实 .claude/skills/scan-market/scan_config.json,
# 与 tests/scan/test_user_config.py 的 monkeypatch.chdir 风格互补,更贴近本文件"直传 tmp_path"惯例。


def test_gate2_default_passthrough_is_byte_identical_parity(tmp_path):
    """parity 铁律:无 scan_config.json(或未指定 l4_gate)→ passthrough,finalists.csv 逐字节
    不改、不落 _l35_cut.csv、回显 l4_gate='passthrough' + l35_cut_n=0。"""
    fp = tmp_path / "finalists.csv"
    pd.DataFrame({"code": ["000001", "000002"], "conviction": [10.0, 90.0],
                  "lane": ["trend", "value"]}).to_csv(fp, index=False)
    before = fp.read_bytes()
    r = gate2(tmp_path, budget=30, user_config_path=tmp_path / "no_such_config.json")
    assert r["ok"] is True
    assert r["l4_gate"] == "passthrough"
    assert r["l35_cut_n"] == 0
    assert r["finalists"] == ["000001", "000002"]
    assert fp.read_bytes() == before, "passthrough 默认应逐字节不改 finalists.csv"
    assert not (tmp_path / "_l35_cut.csv").exists(), "passthrough 默认不应落 _l35_cut.csv"


def test_gate2_configured_gate_narrows_finalists_and_writes_cut(tmp_path):
    """配了非默认闸(topk_simple k=1)→ finalists.csv 收窄落盘 + _l35_cut.csv 落盘(L4 派发读前者)。"""
    cfg = tmp_path / "scan_config.json"
    cfg.write_text(json.dumps({"l4_gate": {"name": "topk_simple", "params": {"k": 1}}}),
                   encoding="utf-8")
    fp = tmp_path / "finalists.csv"
    pd.DataFrame({"code": ["000001", "000002", "000003"],
                  "conviction": [90.0, 50.0, 10.0],
                  "lane": ["trend", "trend", "trend"]}).to_csv(fp, index=False)
    r = gate2(tmp_path, budget=30, user_config_path=cfg)
    assert r["ok"] is True
    assert r["l4_gate"] == "topk_simple"
    assert r["l35_cut_n"] == 2
    assert r["finalists"] == ["000001"] and r["n"] == 1
    after = pd.read_csv(fp, dtype={"code": str})
    assert after["code"].tolist() == ["000001"], "finalists.csv 应被收窄落盘(dispatch_plan 直接读此文件)"
    cut = pd.read_csv(tmp_path / "_l35_cut.csv", dtype={"code": str})
    assert set(cut["code"]) == {"000002", "000003"}
    assert set(cut["reason"]) == {"topk_cut"}


def test_gate2_exempt_pinned_survives_low_conviction_floor(tmp_path):
    """exempt 正确性(A2-T1 契约 + A3-T4 对齐):pinned 票即便 conviction 远低于 floor 也必须在
    picked、绝不进 _l35_cut.csv;同批非豁免的低分票正常被砍。"""
    cfg = tmp_path / "scan_config.json"
    cfg.write_text(json.dumps({"l4_gate": {"name": "conviction_floor_quota",
                                            "params": {"floor": 50}}}), encoding="utf-8")
    fp = tmp_path / "finalists.csv"
    pd.DataFrame({"code": ["000001", "000002", "000003"],
                  "conviction": [90.0, 1.0, 5.0],
                  "lane": ["trend", "pinned", "trend"]}).to_csv(fp, index=False)
    r = gate2(tmp_path, budget=30, user_config_path=cfg)
    assert r["ok"] is True
    assert set(r["finalists"]) == {"000001", "000002"}, \
        "000003(非豁免、conviction<floor)应被砍;000002(pinned)应豁免直通"
    cut = pd.read_csv(tmp_path / "_l35_cut.csv", dtype={"code": str})
    assert set(cut["code"]) == {"000003"}


def test_gate2_regime_read_from_meta_json(tmp_path):
    """regime 取 meta.json.regime(risk_off → 默认 cap=6),与 menu.l4_budget 同一读法。"""
    (tmp_path / "meta.json").write_text(json.dumps({"regime": "risk_off"}), encoding="utf-8")
    cfg = tmp_path / "scan_config.json"
    cfg.write_text(json.dumps({"l4_gate": {"name": "conviction_floor_quota", "params": {"floor": 0}}}),
                   encoding="utf-8")
    rows = [{"code": f"{i:06d}", "conviction": 100 - i, "lane": "trend"} for i in range(20)]
    pd.DataFrame(rows).to_csv(tmp_path / "finalists.csv", index=False)
    r = gate2(tmp_path, budget=30, user_config_path=cfg)
    assert r["n"] == 6, "risk_off 默认 cap=6"


def test_gate2_regime_defaults_to_range_without_meta_json(tmp_path):
    """无 meta.json(regime 拿不到)→ 'range' 兜底,默认 cap=8。"""
    cfg = tmp_path / "scan_config.json"
    cfg.write_text(json.dumps({"l4_gate": {"name": "conviction_floor_quota", "params": {"floor": 0}}}),
                   encoding="utf-8")
    rows = [{"code": f"{i:06d}", "conviction": 100 - i, "lane": "trend"} for i in range(20)]
    pd.DataFrame(rows).to_csv(tmp_path / "finalists.csv", index=False)
    r = gate2(tmp_path, budget=30, user_config_path=cfg)
    assert r["n"] == 8, "无 meta.json → regime 兜底 range,cap=8"


def test_gate2_injects_menu_l4_budget_into_gate_params(tmp_path, monkeypatch):
    """预算旗收编为输入(design §3):menu.l4_budget(scan_dir) 算出的数塞进 params['l4_budget'],
    比 regime cap 更紧时应生效收窄(min 语义,l35_gate.conviction_floor_quota 已单测覆盖 min 本身,
    这里只验证 gates.py 确有调用 + 传参)。"""
    import autoresearch.scan.menu as menu

    monkeypatch.setattr(menu, "l4_budget", lambda scan_dir, **kw: (2, "monkeypatched"))
    cfg = tmp_path / "scan_config.json"
    cfg.write_text(json.dumps({"l4_gate": {"name": "conviction_floor_quota", "params": {"floor": 0}}}),
                   encoding="utf-8")
    rows = [{"code": f"{i:06d}", "conviction": 100 - i, "lane": "trend"} for i in range(20)]
    pd.DataFrame(rows).to_csv(tmp_path / "finalists.csv", index=False)
    r = gate2(tmp_path, budget=30, user_config_path=cfg)
    assert r["n"] == 2, "regime cap(trend=10)应被更紧的 menu.l4_budget=2 压低"


def test_gate2_unknown_gate_name_raises_not_silent_fallback(tmp_path):
    """坏闸名(拼写错)不静默退化 passthrough——防拼写错静默失效,与 user_config 白名单哲学一致。"""
    import pytest

    cfg = tmp_path / "scan_config.json"
    cfg.write_text(json.dumps({"l4_gate": {"name": "totally_typoed_gate"}}), encoding="utf-8")
    pd.DataFrame({"code": ["000001"], "conviction": [90.0]}).to_csv(
        tmp_path / "finalists.csv", index=False)
    with pytest.raises(KeyError, match="unknown gate"):
        gate2(tmp_path, budget=30, user_config_path=cfg)
