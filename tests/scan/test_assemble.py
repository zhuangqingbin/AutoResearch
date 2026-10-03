"""L5 整合(assemble)回归 —— 端口自 assemble_scan._selftest()(Plan 4.1),覆盖逐项保留。

一个 module-scoped fixture 造与原 selftest **完全相同**的合成 scan dir(meta/L1/L2/finalists/L4 卡/
中间推理件/verify.csv),跑 `assemble.run(d, run_date=2026-06-21, hhmm=0930)`,各 test 对发布产物断言:
  - **决策层 `summary.md`**(## 候选 / ## 📌 保送持仓 / ## 为什么没有 BUY;节序见 report_sections 模块头)
  - **现场层 `appendix.md`**(A–G 七节:自检明细 / 漏斗现场 / 研究全文 / 门柱 / 运行观测 / 口径 / 局限)
  - **候选表新列集**(`# | 名称 | 板块 | 评级 | 目标(EV) | 一句依据 | L1→L2`;
    已删 代码 / R:R / 提案 / 置信度,`L3精排` 全文列下沉 appendix C,`L1召回`+`L2粗排` 合并成一列)
  - **耗时 & 落盘字节段 + token 计量说明**(下沉 appendix E)
  - Tier-3 多空辩论(徽标并进评级格 → summary;多/空/共识明细 → appendix C)
  - 降级折回(甲 OW→Hold 踢出买单;丁 OW 维持不改)
  - run-folder 目录名 = 数据日-发布时刻(20260620-0621_0930);manifest.analysis_date 同为数据日 d
  - reasoning 归档(l3/l4/verify)+ 决策卡按名称发布(300476→甲.md)

2026-08-28 summary 精简重构(design: docs/specs/2026-08-28-summary-slimdown-design.md §4.1/§5/§7)
把现场内容整体搬到 `appendix.md`。本文件的断言**跟着内容搬**:原来断言「在 summary 里」的
现场素材,现在断言「在 appendix 里」+「不在 summary 里」——搬家不减料,两侧 token 表相加
不少于重构前的 30 条。
NO network. 纯确定性。
"""
from __future__ import annotations

import csv
import json
from datetime import datetime, timezone

import pytest

from autoresearch.common import workspace as ws
from autoresearch.scan import assemble
from autoresearch.scan.artifacts import ARTIFACT_INDEX_SCHEMA_VERSION
from autoresearch.scan.run_contract import RunContract, write_run_contract
from pathlib import Path  # noqa: F401 — re-export/兼容面,勿删(ruff --fix 曾误删)

_DATA_DATE = "2026-06-20"
_RUN_DATE = "2026-06-21"
_HHMM = "0930"
_RUN_FOLDER = "20260620-0621_0930"   # 目录名 = 数据日-发布MMDD_HHMM(2026-08-28 用户裁定)


def _build_scan_dir(root):
    """造与原 assemble_scan._selftest() 等价的 staging scan dir。返回 scan_dir Path。"""
    scan = root / ws.scan_root() / _DATA_DATE
    (scan / "details").mkdir(parents=True)
    (scan / "meta.json").write_text(json.dumps({
        "universe": 5483, "recall_n": 1000, "l2_n": 200, "l2_engine": "gbdt", "source": "tushare",
        "weights_source": "factor_lab.calibrate"}), encoding="utf-8")
    # L1 召回(概览用)
    with (scan / "L1_recall_top1000.csv").open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["code", "name", "industry", "composite"])
        w.writeheader()
        for i in range(20):
            w.writerow({"code": f"{300000 + i:06d}", "name": f"光{i}", "industry": "电子",
                        "composite": 90 - i})
    # L1 全量打分(_l1_cell 查 rank/composite)+ L2 粗排(_l2_cell 查 l2_rank/gbdt)
    l1l2 = [("300476", 5, 80, 2, 0.54), ("600519", 50, 60, 40, 0.49),
            ("002384", 8, 77, 6, 0.52), ("301117", 3, 82, 1, 0.55)]
    with (scan / "L1_scored_full.csv").open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["rank", "recalled", "code", "name", "industry", "composite",
                                          "winner_rate", "pct_60d", "rsi6"])
        w.writeheader()
        for code, rk, comp, _lr, _g in l1l2:
            w.writerow({"rank": rk, "recalled": True, "code": code, "name": code, "industry": "电子",
                        "composite": comp, "winner_rate": 60, "pct_60d": 80, "rsi6": 65})
    _ch = {"300476": "growth|heat|momentum", "600519": "composite|value",
           "002384": "momentum", "301117": "composite|growth"}   # 命中队列(随 keep 流到 L2 表)
    with (scan / "L2_gbdt_top200.csv").open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["l2_rank", "gbdt_score", "code", "name", "industry",
                                          "composite", "recall_channels"])
        w.writeheader()
        for code, _rk, comp, lr, g in l1l2:
            w.writerow({"l2_rank": lr, "gbdt_score": g, "code": code, "name": code,
                        "industry": "电子", "composite": comp, "recall_channels": _ch.get(code, "")})
    # L3 精排 finalists(带 thesis/risk/catalyst)
    with (scan / "finalists.csv").open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["ticker", "code", "name", "sector", "lenses", "conviction",
                                          "triage_lean", "triage_reason", "thesis", "risk", "catalyst"])
        w.writeheader()
        w.writerow({"ticker": "300476", "code": "300476", "name": "甲", "sector": "光模块",
                    "lenses": "动量", "conviction": "203", "triage_lean": "看多", "triage_reason": "加速",
                    "thesis": "AI 光模块需求超预期", "risk": "估值高", "catalyst": "Q2 财报"})
        w.writerow({"ticker": "600519", "code": "600519", "name": "乙", "sector": "白酒",
                    "lenses": "价值", "conviction": "125", "triage_lean": "中性", "triage_reason": "低估",
                    "thesis": "现金牛低估", "risk": "需求弱", "catalyst": "中报"})
        w.writerow({"ticker": "002384", "code": "002384", "name": "丙", "sector": "光模块",
                    "lenses": "动量", "conviction": "118", "triage_lean": "回避", "triage_reason": "过热",
                    "thesis": "x", "risk": "y", "catalyst": "z"})
        w.writerow({"ticker": "301117", "code": "301117", "name": "丁", "sector": "光模块",
                    "lenses": "动量", "conviction": "150", "triage_lean": "看多", "triage_reason": "稳健",
                    "thesis": "维持OW对照票", "risk": "无硬伤", "catalyst": "无"})
    # L4 决策卡(002384 故意缺卡 → 测降级;301117 OW+维持 对照不折回)
    for tk, rating, prop, rub in [("300476", "Overweight", "BUY", "Overweight"),
                                  ("600519", "Hold", "HOLD", "Hold"),
                                  ("301117", "Overweight", "BUY", "Overweight")]:
        (scan / "details" / f"{tk}.md").write_text(
            "# 决策卡\n## 决策仪表盘\n| 评级 | 现价 | EV目标 | R:R | 置信度 |\n|---|---|---|---|---|\n"
            f"| **{rating}** | 100元 | 130元(+30%) | 2.1:1 | 中 |\n\n"
            f"**Rubric建议**: {rub}(净分 +2,OW门 3/3)\n\n**Rating**: {rating}\n\n"
            f"FINAL TRANSACTION PROPOSAL: **{prop}**\n", encoding="utf-8")
    # 中间推理件(应归档到 trace/reasoning/{l3,l4};L2 已确定性化,无 LLM 留痕)
    for fn in ("_l3_judged_0.csv", "_l4_prompt.md", "_l4_batch_0.md"):
        (scan / fn).write_text("x", encoding="utf-8")
    # Tier-3 多空辩论:买单 300476 降级(带 bull+consensus)+ 多空中间稿(→ reasoning/verify/)
    (scan / "verify.csv").write_text(
        "code,verdict,bull,bear,trigger,consensus\n"
        '300476,降级,"AI光模块需求真切","估值已透支PE160","跌破120元","降级2/3(估值/资金)"\n'
        '301117,维持,"龙头卡位稀缺","无硬伤","继续持有","维持3/3"\n', encoding="utf-8")
    (scan / "_v_bull_300476.md").write_text("多头研究员稿", encoding="utf-8")
    (scan / "_v_300476.md").write_text("空头研究员稿", encoding="utf-8")
    contract = RunContract.build(
        analysis_date=_DATA_DATE,
        user_config={},
        pinned={"kept": [], "expired": []},
        data_policy={"source": "tushare"},
        stage_budgets={"l3_finalist_max": 10},
        artifact_schema_versions={"market_pack": 1},
        git_sha="abc123",
        now=datetime(2026, 6, 21, 1, 2, 3, tzinfo=timezone.utc),
    )
    write_run_contract(scan / "run_contract.json", contract)
    return scan


@pytest.fixture(scope="module")
def published(tmp_path_factory):
    """跑 assemble.run 一次,返回发布包(summary + appendix)+ trace_dir。run_date≠数据日,验证解耦。

    `md` = 决策层 summary.md 全文;`appendix` = 现场层 appendix.md 全文 —— 两份是**一个发布包**
    (publisher `_write_report_bundle`),所以两份一起读、一起断言:内容搬家后,「哪一边有」
    本身就是被测行为。
    """
    root = tmp_path_factory.mktemp("scan_l5")
    scan = _build_scan_dir(root)
    summary_path = assemble.run(_DATA_DATE, scan_dir=scan, out_root=root / ws.reports_root() / "scan",
                                hhmm=_HHMM, run_date=_RUN_DATE)
    out_base = root / ws.reports_root() / "scan" / _RUN_FOLDER
    md = summary_path.read_text(encoding="utf-8")
    appendix_path = out_base / "appendix.md"
    return {
        "summary_path": summary_path,
        "appendix_path": appendix_path,
        "out_base": out_base,
        "md": md,
        "appendix": appendix_path.read_text(encoding="utf-8"),
        "trace": out_base / "trace",
        "scan_dir": scan,
    }


#: 候选表节锚(旧 `## 3. 投资建议` → 新 `## 候选(N 只)`;§4.1 节 4)
_CANDIDATES_ANCHOR = "## 候选("


def _candidate_header(md: str) -> str:
    """候选表表头行(从节锚往下找第一行 `| #`)。切片恒空 = 恒绿假灯,故先断言锚在。"""
    i = md.find(_CANDIDATES_ANCHOR)
    assert i >= 0, f"summary 缺候选表节锚 {_CANDIDATES_ANCHOR!r} —— 后续切片会恒空(假绿灯)"
    return next((ln for ln in md[i:].splitlines() if ln.lstrip().startswith("| #")), "")


# ───────────────────────── run-folder / manifest 解耦 ─────────────────────────


def test_run_folder_leads_with_the_data_date(published):
    """2026-08-28 用户裁定:目录名首段 = **研究的是哪天的行情**,尾段 = 什么时候写完的。

    旧格式首段是跑动日,于是 `20260826_2000` 这个名字对人说"08-26"、研究的却是 08-25
    (61 个已发布 run 里 19 个数据日 ≠ 跑动日)。
    """
    assert published["out_base"].name == _RUN_FOLDER
    assert published["out_base"].name.startswith(_DATA_DATE.replace("-", ""))
    assert not published["out_base"].name.startswith(_RUN_DATE.replace("-", ""))
    assert published["summary_path"].parent == published["out_base"]


def test_manifest_records_data_date(published):
    mpath = published["out_base"] / "manifest.json"
    assert mpath.exists(), "manifest.json 缺"
    assert json.loads(mpath.read_text(encoding="utf-8")).get("analysis_date") == _DATA_DATE, \
        "manifest.analysis_date 应为数据日"


def test_manifest_records_contract_identity(published):
    manifest = json.loads(
        (published["out_base"] / "manifest.json").read_text(encoding="utf-8")
    )
    contract = json.loads(
        (published["trace"] / "run_contract.json").read_text(encoding="utf-8")
    )
    assert manifest["run_id"] == contract["run_id"]
    assert manifest["contract_hash"] == contract["contract_hash"]
    from autoresearch.scan.run_contract import RUN_CONTRACT_SCHEMA_VERSION
    # 取常量:锁的是「manifest 记的版本 = 代码写的版本」,不是「版本永远是 1」
    assert manifest["run_contract_schema_version"] == RUN_CONTRACT_SCHEMA_VERSION
    assert manifest["artifact_index_schema_version"] == ARTIFACT_INDEX_SCHEMA_VERSION


def test_artifact_index_is_written_and_published(published):
    staging = published["scan_dir"]
    index_path = staging / "artifact_index.json"
    trace_path = published["trace"] / "artifact_index.json"
    assert index_path.exists()
    assert trace_path.exists()
    index = json.loads(index_path.read_text(encoding="utf-8"))
    rows = {row["name"]: row for row in index["artifacts"]}
    assert index["contract_hash"]
    for name in (
        "run_contract", "l1_full", "l2", "finalists", "l4_cards",
        "final_ratings", "decision_records", "outbox_events",
        "consumer_state", "gate_fires", "run_health", "budget_observation",
        "summary", "manifest",
    ):
        assert rows[name]["status"] == "PRESENT", name
    # `appendix` 是**契约门控**产物(artifacts.CONTRACT_GATED_ARTIFACTS):本夹具的
    # run_contract 只记了 `market_pack`,所以这一次 run 没认领 appendix 义务 → 整行不生成
    # (既不 PRESENT 也不 MISSING)。文件本身照发 —— 「没记进契约」≠「没产出」。
    assert "appendix" not in rows, "旧契约 run 不得凭今天的清单被倒灌 appendix 义务"
    assert (published["out_base"] / "appendix.md").exists(), "发布包缺 appendix.md"


def test_summary_is_explicit_when_current_run_cost_is_not_yet_measured(published):
    """未计量必须显式说出来。§5 行 20/21:成本明细整块下沉 appendix E,summary 只留紧凑一行。"""
    text = published["md"]
    assert "计量:UNMEASURED" in text, "summary 紧凑行仍须自报计量状态"
    assert "$0" not in text
    # 明细块(标题 + 「未计量」逐条)搬到 appendix E,一个字都没丢
    appendix = published["appendix"]
    assert "## 💸 成本与时延观测" in appendix
    assert "计量:UNMEASURED" in appendix
    assert "成本 JSON 未计量" in appendix
    assert "$0" not in appendix
    # summary 不再重复展开明细(事实归属:墙钟/成本 的唯一展开点 = appendix E)
    assert "成本 JSON 未计量" not in text


def test_trace_run_health_is_final_refresh(published):
    staging = published["scan_dir"]
    assert (
        published["trace"] / "run_health.json"
    ).read_bytes() == (staging / "run_health.json").read_bytes()


def test_assemble_records_final_stage_results(published):
    from autoresearch.scan.stage_result import load_stage_result

    stage_dir = published["scan_dir"] / "stage_results"
    assemble_result = load_stage_result(stage_dir / "assemble.json")
    gate4_result = load_stage_result(stage_dir / "gate4.json")
    assert assemble_result.status == "SUCCEEDED"
    # `appendix` 与 `summary` 是一个发布包:两文件未齐不得记 SUCCEEDED(§6.2)
    assert assemble_result.artifacts == [
        "final_ratings", "decision_records", "gate_fires", "run_health",
        "summary", "appendix", "manifest",
    ]
    assert assemble_result.metrics["n_cards"] == 3
    assert gate4_result.status == "FAILED"
    assert gate4_result.artifacts == ["gate_fires"]
    assert "覆盖率不足" in gate4_result.error


def test_stage_results_are_published_and_indexed(published):
    stage_trace = published["trace"] / "stage_results"
    assert (stage_trace / "assemble.json").exists()
    assert (stage_trace / "gate4.json").exists()
    index = json.loads(
        (published["scan_dir"] / "artifact_index.json").read_text(encoding="utf-8")
    )
    rows = {row["name"]: row for row in index["artifacts"]}
    assert rows["stage_results"]["status"] == "PRESENT"
    assert len(rows["stage_results"]["content_hash"]) == 64
    health = json.loads(
        (published["scan_dir"] / "run_health.json").read_text(encoding="utf-8")
    )
    assert health["stage_results"]["status"] == "OK"
    assert health["stage_results"]["counts"] == {
        "DEGRADED": 1,
        "FAILED": 1,
        "SUCCEEDED": 1,
    }
    assert health["stage_results"]["failed"] == ["gate4"]
    assert health["decision_records"]["status"] == "OK"
    assert health["decision_records"]["n_records"] == 4
    assert health["decision_records"]["contract_hash_match"] is True
    assert health["decision_records"]["final_ratings_match"] is True
    assert health["decision_records"]["early_stop_match"] is True
    assert health["post_run"]["status"] == "BACKLOG"
    assert health["post_run"]["n_events"] == 9
    # 2026-08-21 learning 层退役:原 11 = RUN_FINALIZED×8 学习账本 + DOSSIER_DELTA_READY×3。
    # 前 8 个 consumer 已随闭环删除,只剩 3 个档案 δ。
    assert health["post_run"]["expected"] == 3
    assert health["post_run"]["pending"] == 3
    assert health["post_run"]["failed_consumers"] == []


def test_decision_records_capture_fold_chain(published):
    from autoresearch.scan.decision_record import load_decision_records

    records = load_decision_records(
        published["scan_dir"] / "decision_records.json"
    )
    assert set(records) == {"300476", "600519", "002384", "301117"}

    downgraded = records["300476"]
    assert downgraded.source_rating == "Overweight"
    assert downgraded.rubric_rating == "Overweight"
    assert downgraded.final_rating == "Hold"
    assert downgraded.proposal == "HOLD"
    assert downgraded.first_rejection_stage == "VERIFY"
    assert downgraded.reason == "verify:降级"
    assert "verify.csv#300476" in downgraded.evidence_refs

    missing = records["002384"]
    assert missing.source_rating == "—" and missing.final_rating == "—"
    assert missing.first_rejection_stage == "L4_CARD_MISSING"

    maintained = records["301117"]
    assert maintained.source_rating == maintained.final_rating == "Overweight"
    assert maintained.first_rejection_stage is None
    assert maintained.reason == "qualified"


def test_decision_records_match_legacy_final_ratings(published):
    from autoresearch.scan.decision_record import load_decision_records

    records = load_decision_records(
        published["scan_dir"] / "decision_records.json"
    )
    legacy = json.loads(
        (published["scan_dir"] / "_final_ratings.json").read_text(
            encoding="utf-8"
        )
    )
    assert {code: record.final_rating for code, record in records.items()} == legacy


def test_decision_records_are_published(published):
    staging = published["scan_dir"] / "decision_records.json"
    traced = published["trace"] / "decision_records.json"
    assert traced.read_bytes() == staging.read_bytes()
    manifest = json.loads(
        (published["out_base"] / "manifest.json").read_text(encoding="utf-8")
    )
    from autoresearch.scan.decision_record import DECISION_RECORD_SCHEMA_VERSION

    assert manifest["decision_record_schema_version"] == DECISION_RECORD_SCHEMA_VERSION


def test_outbox_control_state_is_published(published):
    staging = published["scan_dir"] / "outbox"
    traced = published["trace"] / "outbox"
    for name in ("events.json", "consumer_state.json"):
        assert (traced / name).read_bytes() == (staging / name).read_bytes()


def test_decision_record_failure_does_not_block_summary(
    tmp_path, monkeypatch, capsys,
):
    from autoresearch.scan.decision_record import DecisionRecord

    root = tmp_path / "decision_shadow_failure"
    scan = _build_scan_dir(root)

    def _raise_dirty_rating(cls, **kwargs):
        raise ValueError("dirty rating")

    monkeypatch.setattr(
        DecisionRecord,
        "build",
        classmethod(_raise_dirty_rating),
    )
    md = assemble.build_summary(
        scan,
        _DATA_DATE,
        _HHMM,
        _RUN_FOLDER,
    )
    assert _CANDIDATES_ANCHOR in md and "## 候选(4 只)" in md
    assert "[decision_record] 写入失败: dirty rating" in capsys.readouterr().err


def test_trace_pipeline_artifacts_published(published):
    pdir = published["trace"]
    for fn in ("L1_recall_top1000.csv", "L2_gbdt_top200.csv", "L3_fine_finalists.csv", "funnel.md",
               "L0_universe_meta.json"):
        assert (pdir / fn).exists(), f"trace 缺 {fn}"


def test_summary_published_to_run_folder(published):
    assert (published["out_base"] / "summary.md").exists(), "summary.md 未发布到 <运行日_HHMM>/"


def test_details_published_by_stock_name(published):
    out_base = published["out_base"]
    assert (out_base / "details" / "甲.md").exists(), "决策卡未按名称发布(details/甲.md)"
    assert not (out_base / "details" / "300476.md").exists(), "发布层不应再用 ticker.md"


def test_reasoning_archived(published):
    rdir = published["trace"] / "reasoning"
    for stage, fn in [("l3", "_l3_judged_0.csv"), ("l4", "_l4_prompt.md"), ("l4", "_l4_batch_0.md"),
                      ("verify", "verify.csv"), ("verify", "_v_300476.md")]:
        assert (rdir / stage / fn).exists(), f"reasoning 归档缺 {stage}/{fn}"


# ───────────── 发布包内容清单:summary 侧(决策层)/ appendix 侧(现场层)─────────────
#
# 2026-08-28 B+ 重构把现场素材整体搬进 appendix.md。原来一张 30 条的 summary token 表现在
# 拆成两张(§7「拆成 summary token 表与 appendix token 表两组,总数不许变少」):
#   - 搬走的条目**跟着搬**到 appendix 表(不是删掉);
#   - 只是换了标题字面的(`## 3. 投资建议` → `## 候选(N 只)`)按新字面锁;
#   - 合并/删列造成字面消失的(`L1召回(#/` / `L3精排` / `列注`),
#     由下面的 `test_summary_does_not_leak_site_layer_content` 反向钉住「不得复辟」。


@pytest.mark.parametrize("token", [
    # 节标题(新节序;`## 候选(4 只)` 带只数 —— 只数错了也要红)
    "## 候选(4 只)", "## BUY 资格与约束", "## 运行事实", "## 诚实局限",
    # 漏斗计数的唯一展开点 = 🧭 仪表盘②(brief 逐字)
    "5483", "1000",
    # 候选表:评级 / 目标 / 缺卡占位 / 新列头 / 合并后的漏斗位次单元格
    "Overweight", "+30%", "⚠️卡片缺失", "一句依据", "L1→L2", "#5", "→ #2",
    # Tier-3 徽标并进评级格(明细去 appendix C)
    "⚠️降级", "✅维持",
    # 组合视角:`### 组合视角` 标题下沉进「## 行动」,但 managed 标记与集中度读数留在决策层
    "<!-- SCAN_PORTFOLIO_START -->", "板块集中度",
    # 运行事实紧凑一行(明细去 appendix E)
    "墙钟", "计量:UNMEASURED",
])
def test_summary_contains_token(published, token):
    assert token in published["md"], f"summary 缺 '{token}'"


@pytest.mark.parametrize("token", [
    # A–G 骨架 + 搬进来的现场素材
    "## B. 漏斗现场", "**漏斗数量**", "**各阶段卡点**",
    "5483", "1000", "选集", "召回", "粗排", "精排",
    # 旧 §3 表的 `L3精排` 全文列 → C 节逐票全文(论点/风险/催化)
    "## C. 研究全文", "AI 光模块需求超预期", "估值高", "Q2 财报",
    # 耗时/字节段(Wave6 T8:~token 估算列已退役 —— 2026-07-24 实测对加权真值低估 30 倍)
    "## 各阶段耗时 & 落盘字节", "effort", "墙钟", "token_usage.md",
    # Tier-3 多空辩论明细(徽标仍在 summary 的评级格)
    "🛡️ Tier-3 买单多空辩论", "⚠️降级", "估值已透支PE160", "AI光模块需求真切", "降级2/3",
    # 旧「列注」的注文去处(§4.3 规则 5:恒定模板文本只进附录)
    "### 一句依据", "### 漏斗口径", "遗留别名",
    # 恒定局限三条全文 + 六段漏斗免责行
    "## G. 诚实局限", "六段漏斗",
])
def test_appendix_contains_token(published, token):
    assert token in published["appendix"], f"appendix 缺 '{token}'"


def test_self_review_banner_aggregated_in_summary_full_in_appendix(published):
    """自检 banner:summary 顶是**聚合版**(同 key 一行 + 计数),appendix A 留**逐条原文**。

    §1 病灶:13 行只有 5 种,同一句话重复 8 遍。聚合不是删 —— 三条 `卡片契约·P4倾向缺失`
    在 appendix A 一条不少。把 `_render_banner(..., aggregate=True)` 改回 False,或者让
    appendix A 也印聚合版,这条都会红。
    """
    md, appendix = published["md"], published["appendix"]
    for key, n in (("卡片契约·P4倾向缺失", 3), ("citation_density", 3)):
        in_summary = [ln for ln in md.splitlines() if key in ln]
        in_appendix = [ln for ln in appendix.splitlines() if key in ln]
        assert len(in_summary) == 1, f"summary banner 未聚合 {key}: {in_summary}"
        assert f"×{n}" in in_summary[0], f"聚合行应带计数 ×{n}: {in_summary[0]}"
        assert len(in_appendix) == n, f"appendix A 应保留 {key} 逐条原文: {in_appendix}"
    assert "🛑 **覆盖率不足**:决策卡 3/4 < 80%" in md
    assert "🛑 **覆盖率不足**:决策卡 3/4 < 80%" in appendix


@pytest.mark.parametrize("token", [
    # 旧节号绝迹(节号 3→无号→1→2 的错乱正是本波要治的病)
    "## 1. 漏斗", "## 2. 各阶段", "## 3. 投资建议",
    # 现场层素材不得回流决策层(事实归属表右侧的东西)
    "AI 光模块需求超预期",              # L3 全文论点 → appendix C
    "## 各阶段耗时 & 落盘字节", "token_usage.md",   # 遥测 → appendix E
    "🛡️ Tier-3 买单多空辩论", "估值已透支PE160",     # 辩论明细 → appendix C
    "OW三门失守分布",                   # 自由文本口径直方图 → appendix D(两口径不同屏)
    # 已合并/删除的旧列字面不得复辟
    "L1召回(#/", "L2粗排(#/", "L3精排", "L4研究·结论", "🛡️红队", "列注",
])
def test_summary_does_not_leak_site_layer_content(published, token):
    """决策层的**反向**锁:上面两张表管「有没有搬到位」,这张管「搬完有没有偷偷留一份」。

    把 `render_summary` 里任一节改回去印现场素材,这条就红 —— 没有它,两张正向表在
    「summary 与 appendix 都印一遍」时会同时绿(重复展开正是本波要根治的病)。
    """
    assert token not in published["md"], f"summary 仍在展开现场层内容 '{token}'"


def test_token_table_intel_row(tmp_path):
    """阶段表『L4 输入·情报』行(l4-intel 盲搜落稿计数;镜像『L4 输入·slim』行元组形状)。"""
    (tmp_path / "_l4_intel_000001.md").write_text("# 活体情报\n", encoding="utf-8")
    md = "\n".join(assemble._stage_token_estimate(tmp_path))
    assert "L4 输入·情报" in md


def test_stage_table_has_no_fabricated_token_column(tmp_path):
    """~token 估算列必须消失(Wave6 T8)。

    2026-07-24 那份报告的估算表写 ~183,623;对同一次跑动做 transcript 追溯真计量得到
    **加权 5.49M / billed 22.4M / 输出 716.6k** —— 对加权低估 30 倍,且分布与旧假设相反
    (L3 真占 7.8% 而非 37%)。这张表是「第二刀砍哪里」的决策输入,错 30 倍比没有更糟。

    变异验证:把 ~token 列加回去,本测试变红。
    """
    lines = assemble._stage_token_estimate(tmp_path)
    head = next(ln for ln in lines if ln.startswith("| 阶段"))
    body = "\n".join(lines)

    assert "~token" not in head, "估算列复辟"
    assert "落盘字节" in head, "真实可测的字节列应保留"
    # 「口径:落盘字节÷2.8」这类**主张句**必须绝迹;脚注里作为退役理由**引用**旧公式是允许的
    # (读者需要知道为什么退役)。所以钉的是主张标记,不是「2.8」这三个字符。
    assert "落盘可测下界" not in body, "旧估算口径仍在作为有效口径陈述"
    assert not any(ln.startswith("| **合计**") and "~" in ln for ln in lines), "合计行仍在报估算 token"
    assert "token_usage.md" in body, "必须指向 CP7 真计量产物"
    assert "不等于用量小" in body, "真表缺席时必须显式说明「未计量」,不得让读者以为用量小"


def test_stage_table_effort_from_echo_and_new_rows(tmp_path):
    """P6b:effort/引擎列读 `user_config_echo.json`(presence-gated;有 echo → 真实值覆盖硬编码)+
    预热/L4 买单ensemble/整合 assemble 三新行(presence-gated:锚文件/目录存在才加行)。"""
    import json

    from autoresearch.scan.assemble import _stage_token_estimate
    det = tmp_path
    (det / "user_config_echo.json").write_text(json.dumps({"agents": {
        "l4_card": {"effort": "xhigh"}, "sector_brief": {"effort": "high", "model": "sonnet"},
        "strategist": {"effort": "high"}, "l3_rank": {"effort": "max"}}}), encoding="utf-8")
    (det / "_prewarm.json").write_text(json.dumps(
        {"date": "x", "started_at": 0.0, "ended_at": 60.0, "steps": []}), encoding="utf-8")
    (det / "ensemble").mkdir()
    (det / "ensemble" / "600000.run2.md").write_text("y" * 280, encoding="utf-8")
    text = "\n".join(_stage_token_estimate(det))
    l4_row = next(ln for ln in text.splitlines() if ln.startswith("| L4 研究"))
    assert "xhigh" in l4_row and "medium" not in l4_row
    brief_row = next(ln for ln in text.splitlines() if "行业brief" in ln)
    assert "Sonnet" in brief_row and "high" in brief_row
    assert "| 预热(夜间)" in text and "| L4 买单ensemble" in text and "| 整合 assemble" in text


def test_stage_table_no_echo_parity(tmp_path):
    """P6b parity:无 `user_config_echo.json` → effort/引擎回退现硬编码值;三新行 presence-gated 不加。"""
    from autoresearch.scan.assemble import _stage_token_estimate
    text = "\n".join(_stage_token_estimate(tmp_path))
    l4_row = next(ln for ln in text.splitlines() if ln.startswith("| L4 研究"))
    assert "medium" in l4_row                        # 无 echo → 旧现值(parity)
    assert "| 预热(夜间)" not in text               # presence-gated:无 _prewarm.json 不加行


def test_candidate_table_column_set(published):
    """候选表列集(§4.1 节 4)= `# | 名称 | 板块 | 评级 | 目标(EV) | 一句依据 | L1→L2`。

    老断言(代码/R:R/提案 已删)原样保留;新增三条删列锁:
    - `L3精排` 全文列(中位 292 字一格)→ appendix C,决策层只留同向一句依据;
    - `L4研究·结论` 列 → 改名「一句依据」(必须与终评级同向);
    - `L1召回`/`L2粗排` 两列 → 合并成 `L1→L2` 一列。
    删掉哪一条,`_candidate_table_lines` 相应改回去时这条都会红。
    """
    md = published["md"]
    header = _candidate_header(md)
    assert all(c in header for c in ("名称", "板块", "评级", "目标(EV)", "一句依据", "L1→L2")), \
        f"候选表头缺列: {header}"
    assert "R:R" not in header, f"候选表不应有 R:R 列: {header}"
    assert "提案" not in header, f"候选表不应有 提案 列: {header}"
    assert "代码" not in header, f"候选表不应有 代码 列: {header}"
    assert "L3精排" not in header, f"L3 全文列应已下沉 appendix C: {header}"
    assert "L4研究·结论" not in header, f"L4 结论列应改名「一句依据」: {header}"
    assert "L1召回" not in header and "L2粗排" not in header, f"两列应已合并成 L1→L2: {header}"
    # 删列不减料:L3 全文在 appendix C 逐票展开
    assert "AI 光模块需求超预期" in published["appendix"], "L3 论点全文没跟着搬进 appendix C"


# ───────────────────────── buy-list 排序 + Tier-3 折回评级 ─────────────────────────


def test_buylist_sorted_by_rating_then_conviction(published):
    """丁(OW维持)< 甲(Hold降级,conv203)< 乙(Hold,conv125)< 丙(缺卡)。"""
    md = published["md"]
    s3 = md.find(_CANDIDATES_ANCHOR)
    assert s3 >= 0, f"summary 缺候选表节锚 {_CANDIDATES_ANCHOR!r}"
    ords = [md.find(n, s3) for n in ("丁", "甲", "乙", "丙")]
    assert all(o >= 0 for o in ords), f"候选表缺票: {ords}"
    assert ords[0] < ords[1] < ords[2] < ords[3], f"候选表排序错(应 丁<甲<乙<丙): {ords}"


def test_downgrade_folds_back_rating(published):
    """300476(甲,Tier-3 降级)OW→Hold 踢出买单(按名称定位行,代码列已删)。"""
    md = published["md"]
    row476 = next((ln for ln in md.splitlines() if "甲" in ln and ln.lstrip().startswith("|")), "")
    assert "Overweight" not in row476 and "Hold" in row476, f"降级未折回(甲 应 OW→Hold): {row476}"


def test_maintained_keeps_rating(published):
    """301117(丁,Tier-3 维持)留 OW,不改评级。"""
    md = published["md"]
    row117 = next((ln for ln in md.splitlines() if "丁" in ln and ln.lstrip().startswith("|")), "")
    assert "Overweight" in row117, f"维持不应改评级(丁 应留 OW): {row117}"


def test_candidate_l1l2_cell_merges_rank_and_queue(published):
    """`L1→L2` 合并列:`#L1名次·命中队列 → #L2名次`(§4.1「表列」)。

    与旧两列的差别,逐条都是**被测行为**:
    - 分母 `(#/N)` 不再进列头 —— 全量 N 的唯一展开点是 🧭 仪表盘②(`L0 5483→L1 1000→L2 200`);
    - `g0.54` gbdt 分**删除**:它是遗留列名(值 = sn_composite 分层采样序,不是模型分),
      口径落 appendix F「漏斗口径」。这里钉成反向断言 —— 谁把它加回来谁变红;
    - 裸 composite(`·80`)仍不得出现(旧断言原样保留);
    - 「列注」整段下沉 appendix F(§4.3 规则 5),summary 不再每日重印。
    """
    md = published["md"]
    header = _candidate_header(md)
    assert "L1→L2" in header, f"缺合并列头 L1→L2: {header}"
    assert "L1召回(#/" not in header and "L2粗排(#/" not in header, f"旧两列复辟: {header}"
    row = next((ln for ln in md.splitlines() if "甲" in ln and ln.lstrip().startswith("|")), "")
    assert "#5" in row, f"甲 L1 名次应显示 #5: {row}"
    assert "成长" in row, f"L1 侧应显示命中队列(growth→成长): {row}"
    assert "→ #2" in row, f"L2 侧名次应保留在合并列右侧: {row}"
    assert "g0.5" not in row, f"gbdt 分是遗留列名,已删,不得复辟: {row}"
    assert "·80" not in row, f"L1 不应再有裸 composite: {row}"
    assert "列注" not in md, "列注是每日恒定模板文本,应下沉 appendix F"
    # 注文没丢:名次/队列/gbdt 别名的口径全在 appendix F,summary 只留语义链接
    appendix = published["appendix"]
    assert "### 漏斗口径" in appendix and "遗留别名" in appendix
    assert "### 一句依据" in appendix
    assert "appendix.md#method-evidence" in md, "候选表应有指向 appendix F 的稳定口径链接"


# ───────────────────────── 呈现层瘦身(2026-07-04) ─────────────────────────


def test_l4_brief_not_over_truncated():
    """L4 结论列 48→96:0买日『为何没买』是全部信息量,别腰斩。"""
    bear = "PB16极端加近4季连miss下共识未证·主力派发占比假象·破多头排列·震荡无垫·估值透支是核心杀点·中报八月下旬前无近端催化"
    text = f"**一行多空**: 多 xxx ｜ 空 {bear}\n"
    out = assemble._l4_brief(text, "Hold")
    assert bear.replace("、", "·") in out, f"96 字符内不应截断: {out}"


def test_buylist_header_drops_confidence(published):
    """置信度列 30 行全『中』零信息 → 删;置信度仍在 details 卡内。"""
    header = _candidate_header(published["md"])
    assert "置信度" not in header, f"候选表不应再有置信度列: {header}"


# ───────────────────────── 影子买单 CSV 污染防护 ─────────────────────────


def test_run_does_not_touch_real_shadow_csv(tmp_path):
    """assemble.run(tmp_scan_dir) 不应创建或修改真实 context/learning/shadow_buys.csv。"""

    # 记录真实 CSV 的初始状态
    real_csv = ws.learning_root() / "shadow_buys.csv"
    if real_csv.exists():
        original_mtime = real_csv.stat().st_mtime
        original_lines = len(real_csv.read_text(encoding="utf-8").splitlines())
    else:
        original_mtime = None
        original_lines = None

    # 在 tmp_path 运行 assemble
    root = tmp_path / "scan_l5"
    scan = _build_scan_dir(root)
    assemble.run(_DATA_DATE, scan_dir=scan, out_root=root / ws.reports_root() / "scan",
                 hhmm=_HHMM, run_date=_RUN_DATE)

    # 验证真实 CSV 未被修改
    if original_mtime is not None:
        # 文件存在过，检查未被修改
        assert real_csv.exists(), "真实 CSV 不应被删除"
        assert real_csv.stat().st_mtime == original_mtime, "真实 CSV 不应被修改"
        assert len(real_csv.read_text(encoding="utf-8").splitlines()) == original_lines, \
            "真实 CSV 行数不应改变"
    else:
        # 文件不存在过，检查仍不存在
        assert not real_csv.exists(), "真实 CSV 不应被创建"


def test_decision_text_zfills_short_ticker(tmp_path):
    """纵深防御:即便上游递来去零 ticker(2156),也要按 6 位零填找到 details/002156.md。"""
    from autoresearch.scan.assemble import _decision_text
    (tmp_path / "details").mkdir(parents=True)
    (tmp_path / "details" / "002156.md").write_text("# 决策卡 — 002156\n", encoding="utf-8")
    assert _decision_text(tmp_path, "2156") is not None
    assert _decision_text(tmp_path, "002156") is not None
    assert _decision_text(tmp_path, "999999") is None


# ─────────────────────── D8.3 ④:_finalist_row rating strict-with-warn ───────────────────────
#
# `_finalist_row` 此前直接调宽松 `parse_rating(text)`(两遍兜底)。D8.3 改成先 strict 后宽松:
# 找到行首 `**Rating**:` 标签 → 直接用(行为不变);找不到 → 仍走宽松兜底(**行为不变**,
# 读数不翻)但打印一行 stderr 留痕(可观测性 +1,硬切等 P2 D8.2)。


def test_finalist_row_falls_back_with_warning_when_rating_unkeyed(tmp_path, capsys):
    """卡面没有行首 `**Rating**:` 标签 → 仍按旧宽松兜底给出评级(读数不变),但打印警告。"""
    (tmp_path / "details").mkdir(parents=True)
    (tmp_path / "details" / "300308.md").write_text(
        "# 决策卡 — 300308 中际旭创\nRubric建议: Overweight\n"
        "FINAL TRANSACTION PROPOSAL: **HOLD**\n", encoding="utf-8")
    row = assemble._finalist_row(tmp_path, {"code": "300308", "ticker": "300308"})
    assert row["rating"] == "Overweight"          # 与改动前的宽松兜底读数逐字相同
    err = capsys.readouterr().err
    assert "300308" in err and "Rating 行缺失" in err and "全文兜底" in err


def test_finalist_row_strict_hit_prints_no_warning(tmp_path, capsys):
    """卡面有行首 `**Rating**:` 标签 → strict 直接命中,不打印兜底警告。"""
    (tmp_path / "details").mkdir(parents=True)
    (tmp_path / "details" / "300308.md").write_text(
        "# 决策卡 — 300308 中际旭创\n**Rating**: Overweight\n"
        "FINAL TRANSACTION PROPOSAL: **BUY**\n", encoding="utf-8")
    row = assemble._finalist_row(tmp_path, {"code": "300308", "ticker": "300308"})
    assert row["rating"] == "Overweight"
    assert capsys.readouterr().err == ""


# ───────────────────────── P0-2:_final_ratings.json(assemble.py 单写者 T2) ─────────────────────────
#
# design: docs/specs/2026-07-12-selflearning-optimization-brainstorm.md §4 P0-2
# STAGES: .claude/skills/scan-market/STAGES.md 开放线头 #6


def test_final_ratings_json_written_after_publish(tmp_path):
    root = tmp_path / "scan_l5_fr"
    scan = _build_scan_dir(root)
    assemble.run(_DATA_DATE, scan_dir=scan, out_root=root / ws.reports_root() / "scan",
                hhmm=_HHMM, run_date=_RUN_DATE)
    fp = scan / "_final_ratings.json"
    assert fp.exists()
    data = json.loads(fp.read_text(encoding="utf-8"))
    assert data == {"300476": "Hold", "600519": "Hold", "002384": "—", "301117": "Overweight"}, data


def test_final_ratings_json_reflects_verify_downgrade_not_card_face(tmp_path):
    """300476(甲)卡面 Overweight,但 verify.csv 判『降级』→ _final_ratings.json 必须落 Hold
    (终评级),不能是卡面残留的 Overweight —— 这正是坏账③要修的行为(STAGES 线头 #6)。"""
    root = tmp_path / "scan_l5_fr2"
    scan = _build_scan_dir(root)
    card_text = (scan / "details" / "300476.md").read_text(encoding="utf-8")
    assert "**Overweight**" in card_text, "夹具前提:甲卡面应仍是 Overweight(折回前)"
    assemble.run(_DATA_DATE, scan_dir=scan, out_root=root / ws.reports_root() / "scan",
                hhmm=_HHMM, run_date=_RUN_DATE)
    data = json.loads((scan / "_final_ratings.json").read_text(encoding="utf-8"))
    assert data["300476"] == "Hold"


def test_final_ratings_json_maintained_ow_keeps_overweight(tmp_path):
    """301117(丁)verify.csv 判『维持』→ 终评级仍是 Overweight,不误折。"""
    root = tmp_path / "scan_l5_fr3"
    scan = _build_scan_dir(root)
    assemble.run(_DATA_DATE, scan_dir=scan, out_root=root / ws.reports_root() / "scan",
                hhmm=_HHMM, run_date=_RUN_DATE)
    data = json.loads((scan / "_final_ratings.json").read_text(encoding="utf-8"))
    assert data["301117"] == "Overweight"


# ───────────────────────── P0-4:process_scores.csv 接线(assemble.run 侧) ─────────────────────────


# ───────────────────────── P0-1(c):precedents.build_index 挂 is_real 后处理 ─────────────────────────


def test_run_does_not_touch_real_precedents_db(tmp_path):
    """assemble.run(tmp_scan_dir) 不应创建或修改真实 context/knowledge/precedents.db
    (is_real=False 时不触发 precedents.build_index;镜像 test_run_does_not_touch_real_shadow_csv)。"""
    real_db = ws.knowledge_root() / "precedents.db"
    original_mtime = real_db.stat().st_mtime if real_db.exists() else None

    root = tmp_path / "scan_l5_prec"
    scan = _build_scan_dir(root)
    assemble.run(_DATA_DATE, scan_dir=scan, out_root=root / ws.reports_root() / "scan",
                hhmm=_HHMM, run_date=_RUN_DATE)

    if original_mtime is not None:
        assert real_db.stat().st_mtime == original_mtime, "真实 precedents.db 不应被修改"
    else:
        assert not real_db.exists(), "真实 precedents.db 不应被创建"


# ───────────────────────── Wave3.5 review I-2:sections_skipped 打印接线 ─────────────────────────


def test_is_real_publish_prints_dossier_sections_skipped(tmp_path, monkeypatch, capsys):
    """单票 dossier consumer 的 `sections_skipped` 必须出现在控制台——此前 assemble 尾
    只打印了批处理 `issues`,镜像行曾缺失(终审 I-2):控制端活体当天读数 = 3/4 份
    档案有跳过标签(生产常态,非边缘态),终端上一个字都看不见,而同一波给 L4 卡注入块
    新加的 tail 正明写「§4/§6 随每日 δ 刷新」——跳过静默 + 该断言并存会让卡片读者把
    陈旧素材当作今天已核事实。本条锁住:`sections_skipped` 非空时必须打印到 stdout。

    monkeypatch 单票 adapter(内部正确性由 tests/dossier/test_delta.py 单独锁),
    只验 outbox consumer 的打印接线。
    """
    monkeypatch.chdir(tmp_path)
    scan = _build_scan_dir(tmp_path)
    monkeypatch.setattr(
        "autoresearch.dossier.delta.record_scan_delta",
        lambda code, *a, **k: {
            "code": code,
            "updated": True,
            "issues": [],
            "sections_skipped": ["§4.seats", "§6"],
        } if code == "300476" else {"code": code, "skipped": "no_dossier"},
    )

    assemble.run(_DATA_DATE, scan_dir=scan, out_root=tmp_path / ws.reports_root() / "scan",
                hhmm=_HHMM, run_date=_RUN_DATE)

    out = capsys.readouterr().out
    assert "300476" in out and "§4.seats" in out and "§6" in out
    assert "跳过刷新" in out
