"""P0-1(2026-08-19 取证):`_run_publish` 必须在 `_relative_buy_decision.json` 的
writer-1(`publish_run_observation` → `relative_buy.safe_write_decision`)读
`run_health.json` 之前,补拍一次快照。

病灶:`publisher.py:273` 的 `write_run_health` 快照 #1 拍在 `build_summary`(:305,
`decision_records.json` 在这里才写)**之前**。当日首次 assemble 时,快照 #1 报
`decision_records.status="ABSENT"` → `relative_buy._data_contract_ok` 的 `data_a`
硬门对**全部**候选团灭 → `blocked=True`,即便当天数据契约本应成立。事后
`post_run observe`(writer-2)重算才得到正确答案 —— 但发布给用户看的 brief 读的
永远是 writer-1。2026-08-13 实证:8 份 brief 里 4 份与决策文件不一致。

详见 `docs/research/2026-08-19-decision-file-two-writers-and-taskbook-hash.md` §2、
`autoresearch/scan/publisher.py` 新增的 `write_run_health` 调用旁注。

夹具沿用两处既有 idiom(而非逐字复用,原因见 task-1.9 report):
- `tests/scan/test_assemble.py::_build_scan_dir` 的 `assemble.run(...)` 端到端调法;
- 但**不**复用它的 4-finalist/3-card 形状 —— 那个形状故意让覆盖率 3/4<80% 触发
  self_review `覆盖率不足` fail(见该文件 `test_assemble_records_final_stage_results`),
  gate4 因此恒 FAILED,`stage_results.failed_data` 恒非空,会把本测试要单独隔离的
  `decision_records.status` 变量淹没掉。本文件改用 1-finalist/1-card(覆盖率 100%),
  让 gate4 干净通过,`data_a` 团灭与否只取决于 `decision_records.status` 一个变量。
"""
from __future__ import annotations

import csv
import json
from datetime import datetime, timezone
from pathlib import Path

from autoresearch.common import workspace as ws
from autoresearch.scan import assemble
from autoresearch.scan.run_contract import RunContract, write_run_contract
from autoresearch.scan.stage_result import safe_record_stage_result

DATE = "2026-07-01"
CODE = "300001"


def _build_clean_single_buy_scan(root: Path) -> Path:
    """1 只 finalist、1 张满卡(Overweight),覆盖率 100% → gate4 零 fail。

    task-book 标它 SUCCEEDED + slim/card PRESENT,让 relative_buy 的 `contract` 硬门
    也过;L1_scored_full.csv 无入场旗列 → `tradable` 默认真;无 `amount_yi` 列 →
    流动性门读到 None,不拦。四道硬门里剩下**只有** `data_a` 会随 run_health 快照的
    新旧而翻转 —— 这正是本测试要隔离的那一个变量。
    """
    scan = root / ws.scan_root() / DATE
    (scan / "details").mkdir(parents=True)
    (scan / "meta.json").write_text(json.dumps({
        "universe": 100, "recall_n": 50, "l2_n": 10, "l2_engine": "gbdt",
        "source": "tushare", "weights_source": "factor_lab.calibrate",
    }), encoding="utf-8")

    with (scan / "L1_recall_top1000.csv").open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["code", "name", "industry", "composite"])
        w.writeheader()
        w.writerow({"code": CODE, "name": "测试股", "industry": "测试行业", "composite": 80})

    with (scan / "L1_scored_full.csv").open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["rank", "recalled", "code", "name", "industry",
                                          "composite", "winner_rate", "pct_60d", "rsi6"])
        w.writeheader()
        w.writerow({"rank": 1, "recalled": True, "code": CODE, "name": "测试股",
                   "industry": "测试行业", "composite": 80, "winner_rate": 60,
                   "pct_60d": 40, "rsi6": 50})

    with (scan / "L2_gbdt_top200.csv").open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["l2_rank", "gbdt_score", "code", "name",
                                          "industry", "composite", "recall_channels"])
        w.writeheader()
        w.writerow({"l2_rank": 1, "gbdt_score": 0.9, "code": CODE, "name": "测试股",
                   "industry": "测试行业", "composite": 80, "recall_channels": "momentum"})

    with (scan / "finalists.csv").open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["ticker", "code", "name", "sector", "lenses",
                                          "conviction", "triage_lean", "triage_reason",
                                          "thesis", "risk", "catalyst"])
        w.writeheader()
        w.writerow({"ticker": CODE, "code": CODE, "name": "测试股", "sector": "测试行业",
                   "lenses": "动量", "conviction": "150", "triage_lean": "看多",
                   "triage_reason": "稳健", "thesis": "测试论点", "risk": "测试风险",
                   "catalyst": "测试催化剂"})

    (scan / "details" / f"{CODE}.md").write_text(
        "# 决策卡\n## 决策仪表盘\n| 评级 | 现价 | EV目标 | R:R | 置信度 |\n|---|---|---|---|---|\n"
        "| **Overweight** | 100元 | 130元(+30%) | 2.1:1 | 中 |\n\n"
        "**Rubric建议**: Overweight(净分 +2,OW门 3/3)\n\n**Rating**: Overweight\n\n"
        "FINAL TRANSACTION PROPOSAL: **BUY**\n", encoding="utf-8")

    contract = RunContract.build(
        analysis_date=DATE, user_config={}, pinned={"kept": [], "expired": []},
        data_policy={"source": "tushare"}, stage_budgets={},
        artifact_schema_versions={}, git_sha="deadbeef",
        now=datetime(2026, 7, 1, 1, 2, 3, tzinfo=timezone.utc),
    )
    write_run_contract(scan / "run_contract.json", contract)

    # relative_buy 的 `contract` 硬门直读这份 task-book(不经 l4_tasks.initialize,
    # 手写最小形状即可 —— 该门只看 status/artifacts.{slim,card}.status)。
    (scan / "_l4_tasks.json").write_text(json.dumps({
        "schema_version": 1, "date": DATE,
        "tasks": {CODE: {
            "code": CODE, "status": "SUCCEEDED",
            "artifacts": {"prompt": {"status": "PRESENT"},
                         "slim": {"status": "PRESENT"},
                         "card": {"status": "PRESENT"}},
        }},
    }, ensure_ascii=False), encoding="utf-8")

    # 生产现场里,`stage_results/` 到 assemble/L5 跑动时早就非空(gate1/gate2 等更早的
    # 流水线步骤已经落过盘)。本夹具只跑 assemble 这一段,必须自己补一条**更早**的
    # stage-result,否则 `stage_results_health()` 在 assemble 写自己那份之前会读到
    # 目录整个不存在 → `status="ABSENT"` → `data_a` 被这个与 P0-1 无关的原因团灭,
    # 测不出本测试真正要隔离的那个变量(decision_records 的 ABSENT→OK)。
    safe_record_stage_result(scan, stage="gate2", status="SUCCEEDED",
                             artifacts=["finalists"], metrics={}, warnings=[], error=None)
    return scan


def test_first_run_of_the_day_is_not_blocked_by_a_stale_health_snapshot(tmp_path):
    """核心断言(P0-1):数据契约本应成立的场景下,当日**首次** assemble 的
    `_relative_buy_decision.json` 必须 `blocked is False`。

    变异校验(task-1.9 report 里贴了完整命令与输出):把新增的
    `_health.write_run_health(scan_dir)` 删掉,本测试必红 —— `blocked` 会变回 `True`,
    `blocked_reasons` 会出现 `hard_gate.data_a`,detail 里能读到
    `decision_records.status='ABSENT'`。
    """
    root = tmp_path / "scan_l5_health_snapshot"
    scan = _build_clean_single_buy_scan(root)

    assemble.run(DATE, scan_dir=scan, out_root=root / ws.reports_root() / "scan",
                hhmm="0930", run_date=DATE)

    decision_path = scan / "_relative_buy_decision.json"
    assert decision_path.exists(), "writer-1 应已落盘 _relative_buy_decision.json"
    doc = json.loads(decision_path.read_text(encoding="utf-8"))

    # 先证明前置条件干净:gate4 没有因覆盖率不足等无关原因团灭 data_a —— 否则下面
    # `blocked is False` 的断言即便通过也可能是巧合,不是本修复在起作用。
    health = json.loads((scan / "run_health.json").read_text(encoding="utf-8"))
    assert health["stage_results"]["failed_data"] == [], (
        f"夹具前提被破坏:stage_results.failed_data 非空({health['stage_results']}),"
        "data_a 会被无关原因团灭,本测试就测不出 P0-1 这一个变量了")

    assert doc["blocked"] is False, (
        f"blocked={doc['blocked']!r},blocked_reasons={doc.get('blocked_reasons')} —— "
        "首跑 brief 又被 run_health 早拍的快照误判 BLOCKED 了")
    assert doc["buys"] and doc["buys"][0]["code"] == CODE
    by_code = {row["code"]: row for row in doc["candidates"]}
    assert by_code[CODE]["hard_gate"]["data_a"] is True
