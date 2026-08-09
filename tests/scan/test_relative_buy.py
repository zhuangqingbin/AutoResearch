"""统一相对决策层 finalizer v1(Wave12 T23 / E6-1)。

本文件锁三件事,顺序即重要性:

1. **v1 规则是观察前锁定的**——四类硬资格、四面 Borda 等权平均、并列决胜、第 2 只的门、
   `expected_abs_gap` 的 UNMEASURED 规则。断言逐条对着规则写,不对着实现写。
2. **两条「看起来像 bug 的恒定行为」**:第 2 只 BUY 恒不出、`expected_abs_gap` 恒
   `UNMEASURED`。它们是设计(影子期无已验证阈值 / 样本 <20),测试把它们钉死,免得后人
   当故障去"修"。
3. **变异鉴别力**:`test_score_is_borda_mean_not_product` / `test_sell_is_hard_gated_out` /
   `test_benchmark_population_excludes_untradable` 三条分别对应任务书 Step 2 的三个探针
   (Borda→乘积 / 硬门放行 Sell / 基准含不可交易票)。改错了它们必须变红。

测试产物一律落 `tmp_path`,绝不写 `context/scan/<date>/` 或 `context/learning/`。
"""
from __future__ import annotations

import csv
import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

import pytest

from autoresearch.common.ruler import MAIN_RULER, entry_flag_for
from autoresearch.scan.decision_record import DecisionRecord, write_decision_records
from autoresearch.scan.relative_buy import (
    DECISION_FILENAME,
    EXPECTED_ABS_GAP_MIN_N,
    RULE_VERSION,
    SCHEMA_VERSION,
    build_decision,
    main,
    tradable_universe,
    write_decision,
)
from autoresearch.scan.run_contract import RunContract, write_run_contract

DATE = "2026-08-06"


# ── fixture 语言:一只票 = 一条轨迹 ───────────────────────────────────────────
@dataclass
class Cand:
    """一只 L4 候选。字段名跟着真产物走,不发明。"""

    code: str
    name: str = "测试股"
    industry: str = "煤炭开采"
    composite_rank: int = 1              # L1_scored_full.rank(越小 composite 分位越高)
    amount_yi: float = 5.0
    n_channels: int = 2
    best_channel_rank: int = 1           # 该票在其最好那条召回路里的名次
    rating: str = "Hold"
    gate_states: dict = field(default_factory=lambda: {"主力真在": "PASS"})
    early_stop: dict | None = None
    intel: str = "INTEL"                 # INTEL / CARD_FALLBACK / NONE
    dossier: bool = False
    price_claim: str | None = None       # CLEAN / MISMATCH / None(= 未度量)
    task_status: str = "SUCCEEDED"
    slim_status: str = "PRESENT"
    card_status: str = "PRESENT"
    carded: bool = True                  # False = 派发了但没写出卡(无终评级)
    pinned: bool = False
    tradable: bool = True                # 写进 L1_scored_full 的入场旗列
    tripwire_hits: int = 0
    in_universe: bool = True             # False = 根本不在 L0 可交易全集里


# 排名主用例:A/B/C/D 四只,面分刻意造成「等权平均」与「乘积」结论相反(见
# test_score_is_borda_mean_not_product 的算式注)。
#   target_align 序 C>B>D>A、其余三面序 A>B>C>D
#   → 平均 A .6875 > B .625 > C .5 > D .1875(BUY = A)
#   → 乘积 B .1526 > A .0838 > C .0461 > D .0007(BUY = B)
_RANK_CANDS = [
    Cand(code="002345", name="潮宏基", industry="饰品",
         composite_rank=40, amount_yi=9.0, n_channels=4, best_channel_rank=1,
         early_stop={"phase": "P3", "reason": "其他"}, intel="INTEL",
         dossier=True, price_claim="CLEAN",
         gate_states={"业绩真兑现": "PASS", "主力真在": "PASS", "估值不透支": "PASS"}),
    Cand(code="000034", name="神州数码", industry="IT服务Ⅱ",
         composite_rank=20, amount_yi=8.0, n_channels=3, best_channel_rank=4,
         early_stop={"phase": "P3", "reason": "其他"}, intel="INTEL",
         dossier=True, price_claim=None,
         gate_states={"业绩真兑现": "PASS", "主力真在": "FAIL", "估值不透支": "PASS"}),
    Cand(code="600188", name="兖矿能源", industry="煤炭开采",
         composite_rank=10, amount_yi=7.0, n_channels=2, best_channel_rank=9,
         early_stop={"phase": "P3", "reason": "资金流出"}, intel="INTEL",
         dossier=False, price_claim=None,
         gate_states={"业绩真兑现": "PASS", "主力真在": "FAIL", "估值不透支": "PASS"}),
    Cand(code="601699", name="潞安环能", industry="煤炭开采",
         composite_rank=30, amount_yi=6.0, n_channels=1, best_channel_rank=20,
         early_stop={"phase": "P3", "reason": "资金流出"}, intel="NONE",
         dossier=False, price_claim=None,
         gate_states={"业绩真兑现": "PASS", "主力真在": "FAIL", "估值不透支": "FAIL"}),
]
_RANK_ORDER = ["002345", "000034", "600188", "601699"]

# 非候选的 universe 填充行:分位/基准的分母得像真的(真产物 4237 行)
_FILLER = 40


def _write_csv(path: Path, header: list[str], rows: list[list]) -> None:
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(header)
        writer.writerows(rows)


def _build_scan(tmp_path: Path, cands: list[Cand], *,
                run_health: dict | None = None,
                with_run_health: bool = True,
                with_decision_records: bool = True,
                orphan_rated: dict[str, str] | None = None,
                with_task_book: bool = True,
                st_filler: bool = False,
                untradable_filler: bool = False,
                date: str = DATE) -> Path:
    """合成一个 scan 目录。只写 T23 真正读的产物,其余留空(缺源必须是可测状态)。"""
    scan = tmp_path / date
    scan.mkdir(parents=True)
    entry_col = entry_flag_for()

    # ── L0 可交易全集(L1_scored_full.csv)──
    header = ["rank", "recalled", "code", "name", "industry", "composite",
              "amount_yi", entry_col]
    rows = []
    for cand in cands:
        if not cand.in_universe:
            continue
        rows.append([cand.composite_rank, True, cand.code, cand.name, cand.industry,
                     round(100 - cand.composite_rank, 4), cand.amount_yi,
                     cand.tradable])
    used_ranks = {c.composite_rank for c in cands}
    rank = 1
    for i in range(_FILLER):
        while rank in used_ranks:
            rank += 1
        used_ranks.add(rank)
        rows.append([rank, False, f"9{i:05d}", f"填充{i}", "填充业",
                     round(100 - rank, 4), round(0.5 + i * 0.1, 2), True])
    if st_filler:
        rows.append([900, False, "000998", "*ST测试", "填充业", 1.0, 4.0, True])
        rows.append([901, False, "000997", "退市测试", "填充业", 1.0, 4.0, True])
    if untradable_filler:
        rows.append([902, False, "000996", "封板测试", "填充业", 1.0, 4.0, False])
    rows.sort(key=lambda r: r[0])
    _write_csv(scan / "L1_scored_full.csv", header, rows)

    # ── L1 逐路名次 ──
    channel_rows = []
    channel_size = 60
    for cand in cands:
        for idx in range(cand.n_channels):
            channel_rows.append([f"ch{idx}", cand.code,
                                 cand.best_channel_rank + idx, 90 - idx])
    for i in range(channel_size):                    # 每条路的分母
        for idx in range(4):
            channel_rows.append([f"ch{idx}", f"9{i:05d}", 30 + i, 10.0])
    _write_csv(scan / "L1_channels.csv",
               ["channel", "code", "channel_rank", "channel_score"], channel_rows)

    # ── L2 菜单(= 护照候选集)──
    _write_csv(scan / "L2_gbdt_top200.csv",
               ["l2_rank", "code", "name", "industry", "composite",
                "recall_channels", "n_channels", "best_rank", "pinned",
                "l2_lane_reserved", "selection_reason", "selection_detail"],
               [[i + 1, c.code, c.name, c.industry, round(100 - c.composite_rank, 4),
                 "|".join(f"ch{k}" for k in range(c.n_channels)), c.n_channels,
                 c.best_channel_rank, c.pinned, False, "merit", ""]
                for i, c in enumerate(cands)])

    _write_csv(scan / "_l3_pass1_kept.csv",
               ["code", "name", "selection_reason", "selection_detail"],
               [[c.code, c.name, "merit", ""] for c in cands])
    _write_csv(scan / "_l3_pass1_cut.csv", ["code", "name"], [])
    (scan / "_l3_judged.json").write_text(json.dumps(
        [{"code": c.code, "conviction": 60, "mechanism": "x", "lane": "healthy",
          "triage_lean": "OW", "finalist": True} for c in cands],
        ensure_ascii=False), encoding="utf-8")
    _write_csv(scan / "finalists.csv", ["ticker", "code", "name", "sector", "guard"],
               [[f"{c.code}.SZ", c.code, c.name, c.industry, ""] for c in cands])

    # ── run 契约 + 决策事实本 ──
    contract = RunContract.build(
        analysis_date=date, user_config={}, pinned={"kept": [], "expired": []},
        data_policy={"source": "tushare"}, stage_budgets={},
        artifact_schema_versions={}, git_sha="deadbeef",
        now=datetime(2026, 8, 6, 14, 0, tzinfo=timezone.utc),
    )
    write_run_contract(scan / "run_contract.json", contract)
    if with_decision_records:
        records = [
            DecisionRecord.build(
                analysis_date=date, contract_hash=contract.contract_hash, code=c.code,
                source_rating=c.rating, rubric_rating=c.rating,
                gate_states=c.gate_states, early_stop=c.early_stop,
                ensemble_ratings=[], final_rating=c.rating, proposal="HOLD",
                reason="rubric", evidence_refs=[f"finalists.csv#{c.code}"],
                first_rejection_stage="L4_RUBRIC",
            ) for c in cands if c.carded]
        # 评过级但**不在 L2 菜单**里的票 —— 护照的 `orphans.rated` 就是为它们造的
        records += [
            DecisionRecord.build(
                analysis_date=date, contract_hash=contract.contract_hash, code=code,
                source_rating=rating, rubric_rating=rating, gate_states={},
                early_stop=None, ensemble_ratings=[], final_rating=rating,
                proposal="HOLD", reason="rubric", evidence_refs=[],
                first_rejection_stage="L4_RUBRIC",
            ) for code, rating in (orphan_rated or {}).items()]
        write_decision_records(scan, records)
    (scan / "_early_stop.json").write_text(json.dumps(
        {c.code: c.early_stop for c in cands if c.carded and c.early_stop},
        ensure_ascii=False), encoding="utf-8")
    for cand in cands:
        (scan / f"_l4_intel_status_{cand.code}.json").write_text(json.dumps(
            {"schema_version": 1, "code": cand.code,
             "availability_for_card": cand.intel}, ensure_ascii=False),
            encoding="utf-8")

    if with_task_book:
        (scan / "_l4_tasks.json").write_text(json.dumps({
            "schema_version": 1, "date": date,
            "tasks": {c.code: {
                "code": c.code, "status": c.task_status,
                "artifacts": {"slim": {"status": c.slim_status},
                              "card": {"status": c.card_status},
                              "prompt": {"status": "PRESENT"}},
            } for c in cands}}, ensure_ascii=False), encoding="utf-8")

    (scan / "_dossier_present.json").write_text(json.dumps(
        [c.code for c in cands if c.dossier]), encoding="utf-8")
    claims = {c.code: {"n_claims": 2, "n_mismatch": 1 if c.price_claim == "MISMATCH"
                       else 0}
              for c in cands if c.price_claim}
    if claims:
        (scan / "_price_claim_status.json").write_text(
            json.dumps(claims, ensure_ascii=False), encoding="utf-8")
    (scan / "_tripwire_conflicts.json").write_text(json.dumps(
        {c.code: {"rating": c.rating,
                  "all_hits": [{"detail": "跌破 1.0 清仓", "raw": "", "card_date": ""}
                               for _ in range(c.tripwire_hits)]}
         for c in cands if c.tripwire_hits}, ensure_ascii=False), encoding="utf-8")

    if with_run_health:
        health = run_health if run_health is not None else {
            "date": date, "core_missing": [],
            "run_contract": {"status": "OK"},
            "stage_results": {"status": "OK", "failed": []},
            "decision_records": {"status": "OK"},
        }
        (scan / "run_health.json").write_text(json.dumps(health, ensure_ascii=False),
                                              encoding="utf-8")
    return scan


def _by_code(doc: dict) -> dict[str, dict]:
    return {row["code"]: row for row in doc["candidates"]}


# ── ① 普通日:恰 1 只 BUY,且是 eligible 里的最高分 ──────────────────────────
def test_normal_day_emits_exactly_one_relative_buy(tmp_path):
    doc = build_decision(_build_scan(tmp_path, _RANK_CANDS))
    assert doc["schema_version"] == SCHEMA_VERSION
    assert doc["rule_version"] == RULE_VERSION
    assert doc["mode"] == "shadow"
    assert doc["blocked"] is False
    assert len(doc["buys"]) == 1
    buy = doc["buys"][0]
    assert buy["basis"] == "relative"
    assert buy["rank"] == 1

    rows = _by_code(doc)
    eligible = {c: r for c, r in rows.items() if r["eligible"]}
    assert len(eligible) == 4
    best = max(eligible.values(), key=lambda r: r["relative_decision_score"])
    assert buy["code"] == best["code"]


def test_rule_version_is_pinned_and_reaches_the_written_product(tmp_path):
    """产物 `rule_version` == 模块常量,**且**常量本身被钉住(两个漂移方向各锁一边)。

    - 字面量断言挡"常量被人悄悄改了却没走治理流程";
    - 落盘文件(不只是内存 dict)断言挡"常量改了但产物没跟"——下游 `relative_ledger`
      与 `brief` 都是 `doc.get("rule_version")` 直取,产物漂了它们会静默记下错版本。

    v1.1 = v1 + 两道硬门的 ABSENT 收紧;打分/选择语义与 v1 逐字相同(8 日回放零变化)。
    """
    assert RULE_VERSION == "e6.v1.1"
    scan = _build_scan(tmp_path, _RANK_CANDS)
    assert build_decision(scan)["rule_version"] == RULE_VERSION
    written = json.loads(write_decision(scan).read_text(encoding="utf-8"))
    assert written["rule_version"] == RULE_VERSION


def test_ruler_is_bound_explicitly(tmp_path):
    """所有新读数列显式绑主尺 —— 报告里读到的相对分不能来路不明。"""
    doc = build_decision(_build_scan(tmp_path, _RANK_CANDS))
    assert doc["ruler"] == MAIN_RULER
    assert doc["benchmark"]["market"]["column"] == "rel_gap_market"
    assert doc["benchmark"]["sector"]["column"] == "rel_gap_sector"


# ── 变异探针 ①:Borda 等权平均,不是乘积 ────────────────────────────────────
def test_score_is_borda_mean_not_product(tmp_path):
    """四面等权平均。fixture 刻意让「平均」与「乘积」给出**相反**的第一名。

    四面(候选内中位分位,4 只 → {0.125,0.375,0.625,0.875}):
      002345 (.125,.875,.875,.875) 平均 .6875 乘积 .0838
      000034 (.625,.625,.625,.625) 平均 .625  乘积 .1526
      600188 (.875,.375,.375,.375) 平均 .5    乘积 .0461
      601699 (.375,.125,.125,.125) 平均 .1875 乘积 .0007
    平均 → 002345 第一;乘积 → 000034 第一。把 mean 改成 prod,本测试必红。
    """
    doc = build_decision(_build_scan(tmp_path, _RANK_CANDS))
    rows = _by_code(doc)
    for code, row in rows.items():
        faces = row["faces"]
        assert set(faces) == {"target_align", "recall_strength", "evidence",
                              "risk_safety"}
        assert row["relative_decision_score"] == pytest.approx(
            round(sum(faces.values()) / 4, 6)), code

    ordered = sorted((r for r in rows.values() if r["eligible"]),
                     key=lambda r: r["rank"])
    assert [r["code"] for r in ordered] == _RANK_ORDER
    assert doc["buys"][0]["code"] == "002345"

    scores = {r["code"]: r["relative_decision_score"] for r in rows.values()}
    assert scores["002345"] == pytest.approx(0.6875)
    assert scores["000034"] == pytest.approx(0.625)
    assert scores["600188"] == pytest.approx(0.5)
    assert scores["601699"] == pytest.approx(0.1875)


# ── ② 硬门:Sell / ST / 早停-基本面恶化 被排除且 excluded 有行 ───────────────
def test_sell_is_hard_gated_out(tmp_path):
    """变异探针 ②:硬门放行 Sell → 本测试红。

    Sell 票刻意造成**分数最高**(通道最多、composite 最好),所以门一放行它就会顶掉
    真正该出的那只——`buys` 与 `excluded` 两侧同时变红。
    """
    sell = Cand(code="300857", name="协创数据", industry="消费电子",
                composite_rank=1, amount_yi=50.0, n_channels=4,
                best_channel_rank=1, rating="Sell", dossier=True,
                price_claim="CLEAN",
                gate_states={"主力真在": "PASS"})
    doc = build_decision(_build_scan(tmp_path, [sell, *_RANK_CANDS]))
    rows = _by_code(doc)
    assert rows["300857"]["eligible"] is False
    assert rows["300857"]["hard_gate"]["no_redflag"] is False
    assert rows["300857"]["rank"] is None
    assert {"code": "300857", "reason": "hard_gate.no_redflag"} in [
        {"code": e["code"], "reason": e["reason"]} for e in doc["excluded"]]
    assert [b["code"] for b in doc["buys"]] == ["002345"]


def test_st_name_is_hard_gated_out(tmp_path):
    st = Cand(code="000998", name="*ST测试", composite_rank=1, amount_yi=50.0,
              n_channels=4, best_channel_rank=1)
    doc = build_decision(_build_scan(tmp_path, [st, *_RANK_CANDS]))
    row = _by_code(doc)["000998"]
    assert row["hard_gate"]["no_redflag"] is False
    assert any(e["code"] == "000998" and e["reason"] == "hard_gate.no_redflag"
               for e in doc["excluded"])
    assert [b["code"] for b in doc["buys"]] == ["002345"]


def test_fundamental_deterioration_early_stop_is_hard_gated_out(tmp_path):
    bad = Cand(code="603127", name="昭衍新药", composite_rank=1, amount_yi=50.0,
               n_channels=4, best_channel_rank=1,
               early_stop={"phase": "P3", "reason": "基本面恶化"})
    doc = build_decision(_build_scan(tmp_path, [bad, *_RANK_CANDS]))
    row = _by_code(doc)["603127"]
    assert row["hard_gate"]["no_redflag"] is False
    assert any(e["code"] == "603127" and "基本面恶化" in e["detail"]
               for e in doc["excluded"])


def test_illiquid_below_p10_is_hard_gated_out(tmp_path):
    """成交额分位 <P10(L0 可交易内)→ no_redflag 否决。"""
    thin = Cand(code="600123", name="兰花科创", composite_rank=1, amount_yi=0.01,
                n_channels=4, best_channel_rank=1)
    doc = build_decision(_build_scan(tmp_path, [thin, *_RANK_CANDS]))
    row = _by_code(doc)["600123"]
    assert row["amount_pctl"] < 0.10
    assert row["hard_gate"]["no_redflag"] is False


def test_task_book_failure_breaks_the_contract_gate(tmp_path):
    broken = Cand(code="601328", name="交通银行", composite_rank=1, amount_yi=50.0,
                  n_channels=4, best_channel_rank=1, task_status="FAILED")
    doc = build_decision(_build_scan(tmp_path, [broken, *_RANK_CANDS]))
    row = _by_code(doc)["601328"]
    assert row["hard_gate"]["contract"] is False
    assert any(e["code"] == "601328" and e["reason"] == "hard_gate.contract"
               for e in doc["excluded"])


def test_succeeded_task_with_absent_artifacts_still_breaks_the_contract_gate(tmp_path):
    """task-book 写着 SUCCEEDED 但 slim/卡产物不在场 —— 契约门必须看**产物**,不只看状态字。"""
    hollow = Cand(code="601688", name="华泰证券", composite_rank=1, amount_yi=50.0,
                  n_channels=4, best_channel_rank=1, card_status="ABSENT")
    doc = build_decision(_build_scan(tmp_path, [hollow, *_RANK_CANDS]))
    row = _by_code(doc)["601688"]
    assert row["hard_gate"]["contract"] is False
    assert any(e["code"] == "601688" and "card" in e["detail"]
               for e in doc["excluded"])

    thin_slim = Cand(code="601001", name="晋控煤业", composite_rank=1, amount_yi=50.0,
                     n_channels=4, best_channel_rank=1, slim_status="ABSENT")
    doc2 = build_decision(_build_scan(tmp_path / "b", [thin_slim, *_RANK_CANDS]))
    assert _by_code(doc2)["601001"]["hard_gate"]["contract"] is False


def test_price_claim_mismatch_breaks_the_contract_gate(tmp_path):
    bad = Cand(code="600285", name="羚锐制药", composite_rank=1, amount_yi=50.0,
               n_channels=4, best_channel_rank=1, price_claim="MISMATCH")
    doc = build_decision(_build_scan(tmp_path, [bad, *_RANK_CANDS]))
    row = _by_code(doc)["600285"]
    assert row["hard_gate"]["contract"] is False
    assert row["price_claim"] == "MISMATCH"


def test_absent_price_claim_status_is_unmeasured_not_a_failure(tmp_path):
    """缺源写状态、不猜:没有逐票价格断言产物 → UNMEASURED,**不**当 fail 用。

    (2026-08-08 premise-check:逐票 price_claim 状态在 `context/scan/<date>/` 里
     根本没有生产者,只有发布层往中文名卡片里追一行文字。缺证据不等于有罪。)
    """
    doc = build_decision(_build_scan(tmp_path, _RANK_CANDS))
    row = _by_code(doc)["600188"]
    assert row["price_claim"] == "UNMEASURED"
    assert row["hard_gate"]["contract"] is True


def test_untradable_candidate_fails_the_tradable_gate(tmp_path):
    off = Cand(code="601001", name="晋控煤业", composite_rank=1, amount_yi=50.0,
               n_channels=4, best_channel_rank=1, tradable=False)
    doc = build_decision(_build_scan(tmp_path, [off, *_RANK_CANDS]))
    row = _by_code(doc)["601001"]
    assert row["hard_gate"]["tradable"] is False
    assert any(e["code"] == "601001" and e["reason"] == "hard_gate.tradable"
               for e in doc["excluded"])


def test_data_contract_anomaly_fails_data_a_for_every_candidate(tmp_path):
    scan = _build_scan(tmp_path, _RANK_CANDS, run_health={
        "core_missing": ["L1_scored_full.csv"],
        "run_contract": {"status": "OK"},
        "stage_results": {"status": "OK", "failed": []},
        "decision_records": {"status": "OK"},
    })
    doc = build_decision(scan)
    assert all(not row["hard_gate"]["data_a"] for row in doc["candidates"])
    assert doc["blocked"] is True
    assert doc["buys"] == []


# ── ③ 全否决日 → blocked=true 且零 BUY ──────────────────────────────────────
# ── 修复轮 I-1:产物「在场且 OK」与「缺席」必须走两条路 ──────────────────────
@pytest.mark.parametrize(("status", "expect_pass"), [
    ("OK", True),
    ("ABSENT", False),
    ("INVALID", False),
])
def test_stage_results_absent_is_not_the_same_as_ok(tmp_path, status, expect_pass):
    """`ABSENT` ≠ 通过。产物缺席时"A 级契约无未解决异常"这句话根本无从断言。"""
    scan = _build_scan(tmp_path / status, _RANK_CANDS, run_health={
        "core_missing": [],
        "run_contract": {"status": "OK"},
        "stage_results": {"status": status, "failed": []},
        "decision_records": {"status": "OK"},
    })
    doc = build_decision(scan)
    assert all(row["hard_gate"]["data_a"] is expect_pass
               for row in doc["candidates"])
    assert (doc["buys"] != []) is expect_pass


@pytest.mark.parametrize(("status", "expect_pass"), [
    ("OK", True),
    ("ABSENT", False),
    ("MISMATCH", False),
    ("INVALID", False),
])
def test_decision_records_absent_is_not_the_same_as_ok(tmp_path, status, expect_pass):
    scan = _build_scan(tmp_path / status, _RANK_CANDS, run_health={
        "core_missing": [],
        "run_contract": {"status": "OK"},
        "stage_results": {"status": "OK", "failed": []},
        "decision_records": {"status": status},
    })
    doc = build_decision(scan)
    assert all(row["hard_gate"]["data_a"] is expect_pass
               for row in doc["candidates"])
    assert (doc["buys"] != []) is expect_pass


def test_rating_absent_breaks_the_contract_gate(tmp_path):
    """复核 I-1 的原始失败场景:assemble 半途崩 —— task-book 已 SUCCEEDED、卡产物在盘、
    `run_contract.json` OK,但 `decision_records.json` 没写出来 → 全体 `research_rating`
    为 `None`。旧口径下四门全过、`blocked=false`、`excluded_rows=0`,把一只**没有评级**的
    票发成当日唯一 BUY。护照专门为这件事写了 `missing:["l4.research_rating"]`,决策层必须消费它。
    """
    scan = _build_scan(tmp_path, _RANK_CANDS, with_decision_records=False)
    doc = build_decision(scan)
    assert all(row["research_rating"] is None for row in doc["candidates"])
    assert all(row["hard_gate"]["contract"] is False for row in doc["candidates"])
    assert all(not row["eligible"] for row in doc["candidates"])
    assert doc["buys"] == []
    assert doc["blocked"] is True
    assert {row["reason"] for row in doc["blocked_reasons"]} == {"hard_gate.contract"}
    assert all("l4.research_rating" in e["detail"]
               for e in doc["excluded"] if e["reason"] == "hard_gate.contract")


def test_data_a_can_be_fooled_by_stale_health_but_contract_still_catches_it(tmp_path):
    """两道门是独立防线:run_health 说一切 OK(写得早/写得乐观)时 `data_a` 会被骗过,
    但 `contract` 仍然拦得住 —— 这正是 I-1 要求的"两条路径"。"""
    scan = _build_scan(tmp_path, _RANK_CANDS, with_decision_records=False)
    doc = build_decision(scan)
    assert all(row["hard_gate"]["data_a"] is True for row in doc["candidates"])
    assert doc["buys"] == []


# ── 修复轮 I-3:护照 orphans 透传(静默丢票必须有对账计数)──────────────────
def test_orphan_rated_tickers_are_counted_not_silently_dropped(tmp_path):
    """有评级但不在 L2 菜单的票永远当不成相对 BUY —— 那就必须在产物里留一个数,
    否则「候选池静默缩水」在"每个成功日至少一只"的裁定下是看不见的病。"""
    scan = _build_scan(tmp_path, _RANK_CANDS,
                       orphan_rated={"000001": "Hold", "688271": "Overweight"})
    doc = build_decision(scan)
    assert doc["orphans"]["rated"] == ["000001", "688271"]
    assert doc["counts"]["orphan_rated"] == 2
    assert {row["code"] for row in doc["candidates"]}.isdisjoint({"000001", "688271"})


def test_orphans_are_empty_on_a_clean_day(tmp_path):
    doc = build_decision(_build_scan(tmp_path, _RANK_CANDS))
    assert doc["orphans"] == {"finalists": [], "rated": []}
    assert doc["counts"]["orphan_rated"] == 0
    assert doc["counts"]["orphan_finalists"] == 0


def test_all_rejected_day_is_blocked_with_zero_buys(tmp_path):
    cands = [Cand(code="30075" + str(i), name=f"票{i}", rating="Sell",
                  composite_rank=i + 1, n_channels=2, best_channel_rank=i + 1)
             for i in range(3)]
    doc = build_decision(_build_scan(tmp_path, cands))
    assert doc["buys"] == []
    assert doc["blocked"] is True
    buckets = {row["reason"]: row["n"] for row in doc["blocked_reasons"]}
    assert buckets["hard_gate.no_redflag"] == 3
    assert len(doc["excluded"]) == 3


def test_empty_candidate_set_is_blocked_not_crashing(tmp_path):
    doc = build_decision(_build_scan(tmp_path, []))
    assert doc["candidates"] == []
    assert doc["buys"] == []
    assert doc["blocked"] is True
    assert [row["reason"] for row in doc["blocked_reasons"]] == ["no_candidates"]


# ── ④ 同输入重跑 byte 稳定 ──────────────────────────────────────────────────
def test_repeated_build_is_byte_stable(tmp_path):
    scan = _build_scan(tmp_path, _RANK_CANDS)
    first = write_decision(scan).read_bytes()
    second = write_decision(scan).read_bytes()
    assert first == second
    assert json.dumps(build_decision(scan), sort_keys=True) == json.dumps(
        build_decision(scan), sort_keys=True)


def test_decision_is_identical_with_or_without_the_passport_artifact(tmp_path):
    """护照是纯派生视图 —— E6 现算,不被一份过期的 `_candidate_passport.json` 摆布。"""
    from autoresearch.scan.passport import write_passport

    scan = _build_scan(tmp_path, _RANK_CANDS)
    before = build_decision(scan)
    write_passport(scan)
    assert build_decision(scan) == before


# ── ⑤ 面内缺失 = 0.5 并记 missing ──────────────────────────────────────────
def test_face_without_any_input_falls_back_to_half_and_records_missing(tmp_path):
    """没写卡的票:evidence / risk_safety 两面无从算 → 0.5 + missing。"""
    blind = Cand(code="600015", name="华夏银行", composite_rank=5, n_channels=2,
                 best_channel_rank=3, task_status="RUNNING", card_status="ABSENT",
                 carded=False)
    scan = _build_scan(tmp_path, [blind, *_RANK_CANDS])
    row = _by_code(build_decision(scan))["600015"]
    assert row["faces"]["evidence"] == 0.5
    assert row["faces"]["risk_safety"] == 0.5
    assert set(row["faces_missing"]) >= {"evidence", "risk_safety"}
    assert row["eligible"] is False        # 卡契约不完整 → contract 门否决


def test_missing_face_does_not_pollute_the_percentile_population(tmp_path):
    """缺失票取 0.5,但**不进**该面的分位分母 —— 否则它会把别人的分位一起挪走。"""
    blind = Cand(code="600015", name="华夏银行", composite_rank=5, n_channels=2,
                 best_channel_rank=3, task_status="RUNNING", card_status="ABSENT",
                 carded=False)
    scan = _build_scan(tmp_path, [blind, *_RANK_CANDS])
    rows = _by_code(build_decision(scan))
    evidence = sorted(rows[code]["faces"]["evidence"] for code in _RANK_ORDER)
    assert evidence == [0.125, 0.375, 0.625, 0.875]


# ── ⑥ v1 第 2 只恒不出(设计,不是 bug)────────────────────────────────────
def test_second_buy_never_fires_in_v1(tmp_path):
    """影子期无已验证阈值 → 第 2 只**恒不出**。别把这条当故障去"修"。"""
    many = [Cand(code=f"60000{i}", name=f"强票{i}", composite_rank=i + 1,
                 amount_yi=50.0 - i, n_channels=4, best_channel_rank=i + 1,
                 dossier=True, price_claim="CLEAN",
                 gate_states={"业绩真兑现": "PASS", "主力真在": "PASS",
                              "估值不透支": "PASS"})
            for i in range(6)]
    doc = build_decision(_build_scan(tmp_path, many))
    assert all(row["eligible"] for row in doc["candidates"])
    assert len(doc["buys"]) == 1
    assert doc["second_buy"] == {"fired": False,
                                 "reason": "v1 影子期无已验证阈值",
                                 "threshold": None}


def test_expected_abs_gap_is_always_unmeasured_in_v1(tmp_path):
    """样本 <20 → 恒 UNMEASURED;禁止拍数。同上,是设计不是坏。"""
    doc = build_decision(_build_scan(tmp_path, _RANK_CANDS))
    assert EXPECTED_ABS_GAP_MIN_N == 20
    for row in doc["candidates"]:
        assert row["expected_abs_gap"] == {"value": None, "status": "UNMEASURED",
                                           "n": 0}


def test_shadow_mode_is_the_only_mode_v1_accepts(tmp_path):
    """「默认不启用连副作用一起不启用」:活体切换是 GATED task,不在本模块里开后门。"""
    scan = _build_scan(tmp_path, _RANK_CANDS)
    with pytest.raises(ValueError):
        build_decision(scan, mode="live")


# ── 变异探针 ③:基准人口只含可交易票 ────────────────────────────────────────
def test_benchmark_population_excludes_untradable(tmp_path):
    """基准 = L0 **可交易**全集等权。含 ST / 入场旗为假的票 → 本测试红。"""
    scan = _build_scan(tmp_path, _RANK_CANDS, st_filler=True,
                       untradable_filler=True)
    members = tradable_universe(scan)
    assert "000998" not in members          # *ST
    assert "000997" not in members          # 退市
    assert "000996" not in members          # 入场旗为假
    assert "002345" in members
    assert members == sorted(members)

    doc = build_decision(scan)
    assert doc["benchmark"]["market"]["n"] == len(members)
    assert doc["benchmark"]["excluded_from_benchmark"]["st_or_delisting"] == 2
    assert doc["benchmark"]["excluded_from_benchmark"]["entry_flag_false"] == 1


def test_benchmark_falls_back_to_all_rows_when_entry_flag_column_is_absent(tmp_path):
    """活体扫描日没有 `buyable_c1` 列(它要 T+1 收盘才算得出)→ 按 ruler 的
    `default=True` 语义全员在场,而不是把整个市场判成不可交易。"""
    scan = _build_scan(tmp_path, _RANK_CANDS)
    path = scan / "L1_scored_full.csv"
    with path.open(encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle))
    entry_col = entry_flag_for()
    fields = [f for f in rows[0] if f != entry_col]
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows({k: r[k] for k in fields} for r in rows)
    assert len(tradable_universe(scan)) == len(rows)


# ── 前导零 / IO / CLI ───────────────────────────────────────────────────────
def test_leading_zero_codes_survive_the_csv_round_trip(tmp_path):
    doc = build_decision(_build_scan(tmp_path, _RANK_CANDS))
    assert "000034" in _by_code(doc)
    assert all(len(row["code"]) == 6 for row in doc["candidates"])


def test_side_input_keys_are_normalised_before_joining(tmp_path):
    """旁路 JSON 的 key 形态千奇百怪(丢前导零的 `34`、带后缀的 `002345.SZ`)——join 前
    一律过 `_code`。丢掉 `zfill(6)`/去后缀,本测试必红(`002345` → `2345` 是本仓库反复
    复发的老坑)。"""
    scan = _build_scan(tmp_path, _RANK_CANDS)
    (scan / "_price_claim_status.json").write_text(json.dumps({
        "34": {"n_claims": 2, "n_mismatch": 1},          # 丢了前导零
        "002345.SZ": {"n_claims": 1, "n_mismatch": 0},   # 带交易所后缀
    }), encoding="utf-8")
    (scan / "_dossier_present.json").write_text(json.dumps(["601699.SS"]),
                                                encoding="utf-8")
    rows = _by_code(build_decision(scan))
    assert rows["000034"]["price_claim"] == "MISMATCH"
    assert rows["000034"]["hard_gate"]["contract"] is False
    assert rows["002345"]["price_claim"] == "CLEAN"
    # 档案在场只喂 evidence 面:601699 因此从 0.4 抬到 0.6,与 600188 打平;
    # 后缀没剥掉的话它会独自留在 0.4,分位随之掉下去。
    assert rows["601699"]["faces"]["evidence"] == rows["600188"]["faces"]["evidence"]


def test_cli_writes_the_shadow_decision_json(tmp_path, capsys):
    scan = _build_scan(tmp_path, _RANK_CANDS)
    assert main([str(scan)]) == 0
    target = scan / DECISION_FILENAME
    assert target.exists()
    doc = json.loads(target.read_text(encoding="utf-8"))
    assert doc["mode"] == "shadow"
    assert json.loads(capsys.readouterr().out)["ok"] is True


def test_writer_touches_nothing_but_its_own_file(tmp_path):
    """零生产副作用:除自己那一份影子产物外,scan 目录 byte 不变。"""
    scan = _build_scan(tmp_path, _RANK_CANDS)
    before = {p.name: p.read_bytes() for p in sorted(scan.iterdir()) if p.is_file()}
    write_decision(scan)
    after = {p.name: p.read_bytes() for p in sorted(scan.iterdir()) if p.is_file()}
    assert set(after) - set(before) == {DECISION_FILENAME}
    assert {k: v for k, v in after.items() if k in before} == before


def test_tie_break_falls_through_to_code_when_everything_ties(tmp_path):
    """并列决胜最后一档:全同分 → 代码字典序。"""
    twins = [Cand(code=code, name=f"孪生{code}", composite_rank=7, amount_yi=5.0,
                  n_channels=2, best_channel_rank=3, dossier=False,
                  gate_states={"主力真在": "PASS"})
             for code in ("600999", "600111", "600555")]
    doc = build_decision(_build_scan(tmp_path, twins))
    ordered = [row["code"] for row in sorted(
        (r for r in doc["candidates"] if r["eligible"]), key=lambda r: r["rank"])]
    assert ordered == ["600111", "600555", "600999"]
    assert doc["buys"][0]["code"] == "600111"


def test_tie_break_prefers_target_align_over_code_order(tmp_path):
    """同分时 **target_align 先决**:两只总分并列 0.5,高 target_align 的赢,即便代码更大。

    600999 target .75 / recall .25;600111 target .25 / recall .75;evidence 与
    risk_safety 两面完全并列 → 两只总分都是 0.5。去掉 target_align 这一档决胜,答案会
    退到代码字典序(600111),本测试必红。
    """
    high_target = Cand(code="600999", name="高分位", composite_rank=1, amount_yi=5.0,
                       n_channels=1, best_channel_rank=20,
                       gate_states={"主力真在": "PASS"})
    high_recall = Cand(code="600111", name="强召回", composite_rank=50, amount_yi=5.0,
                       n_channels=2, best_channel_rank=1,
                       gate_states={"主力真在": "PASS"})
    doc = build_decision(_build_scan(tmp_path, [high_target, high_recall]))
    rows = _by_code(doc)
    assert rows["600999"]["relative_decision_score"] == pytest.approx(
        rows["600111"]["relative_decision_score"])
    assert rows["600999"]["faces"]["target_align"] > rows["600111"]["faces"]["target_align"]
    assert doc["buys"][0]["code"] == "600999"


def test_tie_break_prefers_liquidity_over_code_order(tmp_path):
    """target_align 也并列时,**流动性先决**:成交额大的赢,即便代码更大。"""
    twins = [Cand(code="600999", name="厚流动性", composite_rank=7, amount_yi=50.0,
                  n_channels=2, best_channel_rank=3,
                  gate_states={"主力真在": "PASS"}),
             Cand(code="600111", name="薄流动性", composite_rank=7, amount_yi=6.0,
                  n_channels=2, best_channel_rank=3,
                  gate_states={"主力真在": "PASS"})]
    doc = build_decision(_build_scan(tmp_path, twins))
    rows = _by_code(doc)
    assert rows["600999"]["faces"] == rows["600111"]["faces"]
    assert doc["buys"][0]["code"] == "600999"


def test_absent_run_health_blocks_the_day_instead_of_waving_it_through(tmp_path):
    """A 级契约无从判定 → 按阻断处理(不是"没查到就算过")。"""
    scan = _build_scan(tmp_path, _RANK_CANDS, with_run_health=False)
    doc = build_decision(scan)
    assert doc["inputs"]["run_health"] == "ABSENT"
    assert all(not row["hard_gate"]["data_a"] for row in doc["candidates"])
    assert doc["buys"] == []
    assert doc["blocked"] is True
    assert {row["reason"] for row in doc["blocked_reasons"]} == {"hard_gate.data_a"}


def test_date_argument_overrides_directory_name(tmp_path):
    scan = _build_scan(tmp_path, _RANK_CANDS)
    assert build_decision(scan)["date"] == DATE
    assert build_decision(scan, "2026-08-07")["date"] == "2026-08-07"


# ── 真实 run 活体验收(缺产物则跳过,不当强证据)────────────────────────────
_REAL = Path("context/scan/2026-08-06")


@pytest.mark.skipif(not (_REAL / "decision_records.json").exists(),
                    reason="真实 run 产物不在工作树里")
def test_real_run_20260806_is_deterministic_and_side_effect_free():
    # I-4(修复轮):想断言的是「`build_decision` 只读不落盘」,**不是**「生产目录里
    # 不存在这个文件」——后者会被合法操作打破(报告 §8 给的 CLI 命令、以及 T24 把
    # `safe_write_decision` 挂进 `post_run.observe` 之后的每一次正常发布),跑一次文档
    # 命令就赔进一轮排障。改成对目录取前后 diff(与 `test_writer_touches_nothing_but_
    # its_own_file` 同一配方),观测量换成真正想守的那个。
    before = sorted(p.name for p in _REAL.iterdir())
    doc = build_decision(_REAL)
    assert doc["date"] == "2026-08-06"
    assert doc["rule_version"] == RULE_VERSION
    assert len(doc["candidates"]) == 11              # 当日 11 张卡
    assert doc == build_decision(_REAL)
    assert sorted(p.name for p in _REAL.iterdir()) == before
    # 当日 4 只 Underweight,评级不是 Sell,故 no_redflag 不因评级否决
    assert all(row["hard_gate"]["data_a"] for row in doc["candidates"])
