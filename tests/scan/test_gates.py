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
