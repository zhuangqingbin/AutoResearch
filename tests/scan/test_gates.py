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
