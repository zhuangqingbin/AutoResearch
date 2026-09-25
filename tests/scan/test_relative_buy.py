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
import hashlib
import json
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from pathlib import Path

import pytest

from autoresearch.common.failclass import fail_class
from autoresearch.common.ruler import MAIN_RULER, entry_flag_for
from autoresearch.scan.decision_record import DecisionRecord, write_decision_records
from autoresearch.scan.relative_buy import (
    DECISION_FILENAME,
    EXPECTED_ABS_GAP_MIN_N,
    MISMATCH_CHECK_NAME,
    MISMATCH_FILENAME,
    MODE_ACTIVE,
    RULE_VERSION,
    SCHEMA_VERSION,
    _data_contract_ok,
    build_decision,
    activate_date,
    configured_relative_buy,
    configured_tiering,
    is_active,
    load_decision,
    main,
    safe_verify_decision,
    tradable_universe,
    verify_decision,
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
    seat: bool = False                   # v3.0:L3 守卫⑨ 的 composite 证据席(finalists.guard)
    proposal: str | None = None          # 卡面 `FINAL TRANSACTION PROPOSAL`(v3.0 硬门④ 第二腿)


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
    _write_csv(scan / "finalists.csv",
               ["ticker", "code", "name", "sector", "guard", "lane"],
               [[f"{c.code}.SZ", c.code, c.name, c.industry,
                 "composite_seat" if c.seat else "", "composite" if c.seat else ""]
                for c in cands])

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
                ensemble_ratings=[], final_rating=c.rating,
                proposal=c.proposal or "HOLD",
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

    v1.1 = v1 + 两道硬门的 ABSENT 收紧;v1.2 = v1.1 + data_a 第 4 判改读
    `stage_results.failed_data`(E1a)。v2.0(task-2.2,2026-08-19)= `mode` 形参开放接受
    `"active"` + 新增 `exclude_pinned` 过滤(生产默认仍 shadow/False,翻 active 是独立的
    裁决表批准动作)。**v1→v2.0 打分与选择语义逐字相同**(8 日回放零变化)。

    **v3.0(2026-08-26 §3 路A)是第一次真的改规则**,两件事:① 硬门④ 扩集(UW/Sell 卡与
    `FINAL PROPOSAL: SELL` 一律否决 —— 08-20/08-25 两次把提议 SELL 的卡发成 BUY);
    ② 新增 `pool` 形参:`composite` 时候选池 = L3 守卫⑨ 的证据席,排序改按 composite 分。

    v4.0(2026-09-24 可买性对齐 §2.6,controller Ruling P21)新增 `tiering` 开关:开时
    卡面 `entry_stance=PROHIBITED` 进 `no_redflag` 硬门否决,`buys[0]` 按 `entry_stance
    =ALLOWED` 分 A/R 两级;关(默认)= v3.0 逐字。
    """
    assert RULE_VERSION == "e6.v4.1"
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


# ══ E1a(2026-08-18 设计稿 §3):data_a 改读 stage_results.failed_data ════════════
#
# `_data_contract_ok` 的第 4 判此前直接看 `stage_results.failed`(非空即拒,不分青红皂
# 白)。`failed_data` 是 `run_health.stage_results_health` 新增的子集(task 1.2):gate4
# FAILED 时按 fail_class 分类,hygiene/metering 类不连坐。本节验证 `_data_contract_ok`
# 改读这个新键,且对**没有**这个键的历史 run_health(v1.1 之前生成)回退旧口径——历史
# 判定不改写。


def _health(tmp_path, stages):
    (tmp_path / "run_health.json").write_text(json.dumps({
        "core_missing": [],
        "run_contract": {"status": "OK"},
        "stage_results": stages,
        "decision_records": {"status": "OK"},
    }), encoding="utf-8")


def test_hygiene_only_gate4_passes_data_a(tmp_path):
    _health(tmp_path, {"status": "OK", "failed": ["gate4"], "failed_data": []})
    ok, why, _per_ticker = _data_contract_ok(tmp_path)
    assert ok, why


def test_data_fail_blocks(tmp_path):
    _health(tmp_path, {"status": "OK", "failed": ["gate4"], "failed_data": ["gate4"]})
    assert not _data_contract_ok(tmp_path)[0]


def test_legacy_health_without_failed_data_keeps_old_semantics(tmp_path):
    _health(tmp_path, {"status": "OK", "failed": ["gate4"]})
    assert not _data_contract_ok(tmp_path)[0]  # 回退旧口径:failed 非空即拒


# ══ Task 1(2026-09-24 §2.6-2):data_a 细分票级 —— l4_<code> 只否决该票 ══════════
#
# `stage_results.failed_data` 里形如 `l4_<code>` 的项是**单票** slim/卡失败,只应否决
# 该票的 data_a,不该连坐当日其他候选(两次生产扫描全天 0 买的根因之一 —— 研究质量
# 本身没问题,只是一只票取数失败)。非 `l4_<code>` 形状的项(如 `gate2`)仍是日级连坐。
# 历史 run_health(无 `failed_data` 键)保持 v1.1 旧口径:`failed` 任一项全天否决,不
# 改写历史判定。


def _health_doc(**stage_results):
    return {"date": DATE, "core_missing": [], "run_contract": {"status": "OK"},
            "stage_results": {"status": "OK", **stage_results},
            "decision_records": {"status": "OK"}}


def test_l4_stage_failure_vetoes_only_that_ticker(tmp_path):
    """票级 data_a(2026-09-24 §2.6-2):`l4_<code>` 失败只否决该票,其余候选照常。"""
    scan = _build_scan(tmp_path, _RANK_CANDS,
                       run_health=_health_doc(failed=["l4_600188"], failed_data=["l4_600188"]))
    doc = build_decision(scan)
    by = _by_code(doc)
    assert by["600188"]["hard_gate"]["data_a"] is False
    assert all(by[c]["hard_gate"]["data_a"] for c in _RANK_ORDER if c != "600188")
    assert doc["buys"] and doc["buys"][0]["code"] == "002345"
    detail = [e for e in doc["excluded"] if e["code"] == "600188" and e["reason"] == "hard_gate.data_a"]
    assert detail and "l4_600188" in detail[0]["detail"]


def test_day_level_data_failure_still_vetoes_everyone(tmp_path):
    scan = _build_scan(tmp_path, _RANK_CANDS,
                       run_health=_health_doc(failed=["gate2"], failed_data=["gate2"]))
    doc = build_decision(scan)
    assert all(not row["hard_gate"]["data_a"] for row in doc["candidates"])
    assert doc["blocked"] is True


def test_legacy_health_without_failed_data_keeps_v11_day_level_semantics(tmp_path):
    """历史 run_health 无 failed_data 键 → 旧口径:任一 failed(含 l4_*)全天否决,不改写历史判定。"""
    scan = _build_scan(tmp_path, _RANK_CANDS, run_health=_health_doc(failed=["l4_600188"]))
    doc = build_decision(scan)
    assert all(not row["hard_gate"]["data_a"] for row in doc["candidates"])


# ══ P0-2(`docs/research/2026-08-19-decision-file-two-writers-and-taskbook-hash.md`
# §4):writer-2(`post_run observe`)改成幂等校验,不再无条件覆盖 ═══════════════════
#
# 三件事逐条锁:①两次现算一致 → verify 绝对安静(无侧车/无新 gate_fires 行/原文件
# 字节不变);②盘上还没有已发布答案 → 照写(不是「不一致」,是「还没发布过」);
# ③两次现算真的不一样(mutation) → 绝不覆盖 + 并排证据侧车 + gate_fires data 类
# fail 行,三者缺一都算测试失败。


def _gate_fires_rows(scan: Path) -> list[dict]:
    path = scan / "gate_fires.csv"
    if not path.exists():
        return []
    with path.open(encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


def test_verify_is_silent_when_two_computations_agree(tmp_path):
    """反向测试:两次现算一致 → verify 不重写、不留痕(正常路径必须是安静的)。"""
    scan = _build_scan(tmp_path, _RANK_CANDS)
    written = write_decision(scan)                     # writer-1 时刻
    before_bytes = written.read_bytes()
    before_mtime_ns = written.stat().st_mtime_ns

    result = verify_decision(scan)                      # writer-2 时刻:输入没变

    assert result == {"match": True, "action": "noop", "path": str(written)}
    assert written.read_bytes() == before_bytes
    assert written.stat().st_mtime_ns == before_mtime_ns   # 真没被重写,不只是内容凑巧相同
    assert not (scan / MISMATCH_FILENAME).exists()
    assert not (scan / "gate_fires.csv").exists()


def test_verify_writes_when_nothing_was_published_yet(tmp_path):
    """盘上没有已发布答案:不是「两次不一致」,是「还没发布过」——照写,等价于 write 模式。"""
    scan = _build_scan(tmp_path, _RANK_CANDS)
    target = scan / DECISION_FILENAME
    assert not target.exists()

    result = verify_decision(scan)

    assert result["match"] is True
    assert result["action"] == "write_absent"
    assert target.exists()
    assert json.loads(target.read_text(encoding="utf-8"))["blocked"] is False
    assert not (scan / MISMATCH_FILENAME).exists()
    assert not (scan / "gate_fires.csv").exists()


def test_verify_never_overwrites_on_mismatch_and_leaves_evidence(tmp_path):
    """核心变异探针:writer-1 写完之后,输入被换成会翻转 blocked 的状态,verify 必须
    (1) 盘上原文件字节丝毫不变;(2) 落一份并排证据侧车,内容含两份的差异;
    (3) `gate_fires.csv` 出现一条 data 类 fail 行(`fail_class(check) == "data"`)。
    """
    scan = _build_scan(tmp_path, _RANK_CANDS)
    written = write_decision(scan)                      # writer-1 时刻:BUY 002345
    original_bytes = written.read_bytes()
    original_doc = json.loads(original_bytes)
    assert original_doc["blocked"] is False
    assert [b["code"] for b in original_doc["buys"]] == ["002345"]
    assert not (scan / "gate_fires.csv").exists()

    # writer-2 时刻之前,run_health 被换成数据契约异常(与
    # test_data_contract_anomaly_fails_data_a_for_every_candidate 同一造法)——
    # 现算会把 blocked 从 False 现算成 True,candidates 全灭。
    (scan / "run_health.json").write_text(json.dumps({
        "core_missing": ["L1_scored_full.csv"],
        "run_contract": {"status": "OK"},
        "stage_results": {"status": "OK", "failed": []},
        "decision_records": {"status": "OK"},
    }, ensure_ascii=False), encoding="utf-8")
    assert build_decision(scan)["blocked"] is True      # 造夹具的前提条件先自证

    result = verify_decision(scan)

    # ① 盘上原决策文件字节未变(绝不覆盖)
    assert written.read_bytes() == original_bytes
    assert result["match"] is False
    assert result["action"] == "mismatch"

    # ② 并排证据侧车出现,内容含两份的关键字段差异 + 两份的 sha256
    mismatch_path = scan / MISMATCH_FILENAME
    assert mismatch_path.exists()
    mismatch = json.loads(mismatch_path.read_text(encoding="utf-8"))
    assert mismatch["on_disk"]["blocked"] is False
    assert mismatch["on_disk"]["buys"] == original_doc["buys"]
    assert mismatch["on_disk"]["counts"] == original_doc["counts"]
    assert mismatch["on_disk"]["blocked_reasons"] == original_doc["blocked_reasons"]
    assert mismatch["recomputed"]["blocked"] is True
    assert mismatch["recomputed"]["buys"] == []
    assert mismatch["on_disk"]["sha256"] == hashlib.sha256(original_bytes).hexdigest()
    assert mismatch["recomputed"]["sha256"] != mismatch["on_disk"]["sha256"]

    # ③ gate_fires.csv 出现且恰一条 data 类 fail 行
    rows = _gate_fires_rows(scan)
    assert len(rows) == 1
    row = rows[0]
    assert row["check"] == MISMATCH_CHECK_NAME
    assert row["severity"] == "fail"
    assert fail_class(row["check"]) == "data"
    assert MISMATCH_FILENAME in row["detail"]


def test_safe_verify_decision_never_raises_and_still_leaves_evidence(tmp_path):
    """`safe_verify_decision` 是生产调用点用的失败纪律版:同一变异下行为与
    `verify_decision` 一致(内部真「不一致」不算异常,是正常返回路径),只是外壳吞异常。"""
    scan = _build_scan(tmp_path, _RANK_CANDS)
    write_decision(scan)
    (scan / "run_health.json").write_text(json.dumps({
        "core_missing": ["L1_scored_full.csv"],
        "run_contract": {"status": "OK"},
        "stage_results": {"status": "OK", "failed": []},
        "decision_records": {"status": "OK"},
    }, ensure_ascii=False), encoding="utf-8")

    result = safe_verify_decision(scan)

    assert result is not None and result["match"] is False
    assert (scan / MISMATCH_FILENAME).exists()
    assert len(_gate_fires_rows(scan)) == 1


def test_safe_verify_decision_survives_build_failure(tmp_path, monkeypatch, capsys):
    """影子件失败纪律:verify 内部出异常(如 `build_decision` 炸了)不得向上抛,只打一行
    stderr——与 `safe_write_decision` 同一姿势(纯影子件失败不连累主链)。"""
    import autoresearch.scan.relative_buy as relative_buy_mod

    scan = _build_scan(tmp_path, _RANK_CANDS)
    write_decision(scan)

    def _boom(*_args, **_kwargs):
        raise RuntimeError("build exploded")

    monkeypatch.setattr(relative_buy_mod, "build_decision", _boom)

    assert safe_verify_decision(scan) is None
    assert "verify 失败" in capsys.readouterr().err


# ══ E2(task-2.2,2026-08-19):build_decision 支持 active + exclude_pinned,RULE_VERSION
# e6.v2.0 ═══════════════════════════════════════════════════════════════════════════
#
# 三条锁住的行为:①mode 白名单从 {shadow} 扩到 {shadow, active},非法值(如历史 "live")
# 仍拒;②exclude_pinned=True 时 BUY 只在非📌 eligible 里选,被跳过的📌 eligible 票记入
# excluded(reason=pinned_holding)但仍留在候选表/排名内(rank 字段照旧按全体 eligible 排,
# 不因排除而重排);③当日非📌合格为 0 → 诚实 blocked=True,不退回去选📌票。
# 这一整节仍是**装开关**,不是翻开关:生产 `scan_config.jsonc` 默认仍 mode=shadow。


def test_active_mode_accepted(tmp_path):
    doc = build_decision(_build_scan(tmp_path, _RANK_CANDS), mode=MODE_ACTIVE)
    assert doc["mode"] == "active" and doc["rule_version"] == RULE_VERSION


def test_mode_still_rejects_illegal_values(tmp_path):
    """扩容不是敞开:{shadow,active} 之外的值(历史 "live"、拼写错)仍必须拒。"""
    scan = _build_scan(tmp_path, _RANK_CANDS)
    with pytest.raises(ValueError):
        build_decision(scan, mode="live")


# 变异探针 ④:exclude_pinned 跳过📌选下一只(两只合格,rank1 pinned / rank2 非 pinned)
def test_exclude_pinned_picks_first_nonpinned(tmp_path):
    """`exclude_pinned=True` 时 BUY 跳到 rank2;rank1(📌)仍留在候选表/排名内,只是记进
    `excluded`(reason=pinned_holding),不参与 BUY 选择。"""
    top = replace(_RANK_CANDS[0], pinned=True)     # 002345——不排除时的 rank1
    second = _RANK_CANDS[1]                         # 000034——不排除时的 rank2
    scan = _build_scan(tmp_path, [top, second])
    baseline = build_decision(scan)
    assert baseline["buys"][0]["code"] == "002345"  # 前提自证:不排除时确是 rank1

    doc = build_decision(scan, exclude_pinned=True)
    assert doc["exclude_pinned"] is True
    assert doc["buys"][0]["code"] == "000034"
    assert any(row["code"] == "002345" and row["reason"] == "pinned_holding"
               for row in doc["excluded"])

    by_code = _by_code(doc)
    assert by_code["002345"]["eligible"] is True    # 仍合格,只是不当 BUY
    assert by_code["002345"]["rank"] == 1            # rank 照旧按全体 eligible 排,不因排除重排
    assert by_code["000034"]["rank"] == 2


# 变异探针 ⑤:全部合格票都是📌 → 诚实 blocked,不退回去选📌票
def test_exclude_pinned_all_pinned_blocks(tmp_path):
    top = replace(_RANK_CANDS[0], pinned=True)
    second = replace(_RANK_CANDS[1], pinned=True)
    scan = _build_scan(tmp_path, [top, second])
    doc = build_decision(scan, exclude_pinned=True)
    assert doc["blocked"] is True
    assert doc["buys"] == []
    pinned_excluded = {row["code"] for row in doc["excluded"]
                       if row["reason"] == "pinned_holding"}
    assert pinned_excluded == {"002345", "000034"}
    buckets = {row["reason"]: row["n"] for row in doc["blocked_reasons"]}
    assert buckets.get("pinned_holding") == 2


def test_exclude_pinned_false_is_parity_default(tmp_path):
    """`exclude_pinned` 缺省 `False` = 现行为(parity):哪怕最高分是📌票也照常当 BUY,
    `excluded` 里不出现 pinned_holding 行。"""
    top = replace(_RANK_CANDS[0], pinned=True)
    scan = _build_scan(tmp_path, [top, _RANK_CANDS[1]])
    doc = build_decision(scan)
    assert doc["exclude_pinned"] is False
    assert doc["buys"][0]["code"] == "002345"
    assert not any(row["reason"] == "pinned_holding" for row in doc["excluded"])


def test_write_and_verify_decision_propagate_mode_and_exclude_pinned(tmp_path):
    """`write_decision`/`verify_decision` 真的透传两参,不是只有 `build_decision` 认它们。"""
    top = replace(_RANK_CANDS[0], pinned=True)
    scan = _build_scan(tmp_path, [top, _RANK_CANDS[1]])
    target = write_decision(scan, mode=MODE_ACTIVE, exclude_pinned=True)
    doc = json.loads(target.read_text(encoding="utf-8"))
    assert doc["mode"] == "active" and doc["exclude_pinned"] is True
    assert doc["buys"][0]["code"] == "000034"

    result = verify_decision(scan, mode=MODE_ACTIVE, exclude_pinned=True)
    assert result == {"match": True, "action": "noop", "path": str(target)}


# ── E3b 消费侧盘读 helper(task-2.4)────────────────────────────────────────────
#
# 这组测试锁的是「消费者不许自己现算」:`load_decision` 是纯盘读,过期文件一律判 None。
# 变异校验:把 `load_decision` 里的日期判据删掉,`test_load_decision_rejects_a_stale_file`
# 立刻变红(它造的正是「昨天的文件躺在今天的目录里」)。


def _write_decision_file(scan: Path, payload: dict) -> Path:
    scan.mkdir(parents=True, exist_ok=True)
    target = scan / DECISION_FILENAME
    target.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return target


def test_load_decision_reads_the_file_on_disk(tmp_path):
    scan = tmp_path / "2026-08-18"
    _write_decision_file(scan, {"date": "2026-08-18", "mode": "active",
                                "buys": [{"code": "600000"}]})

    doc = load_decision(scan)

    assert doc is not None
    assert [row["code"] for row in doc["buys"]] == ["600000"]


def test_load_decision_rejects_a_stale_file(tmp_path):
    """**本 task 的核心判据**:scan 目录里躺着前一日的决策文件 → 判 None,不许当今天的。

    E3b 的三个消费点都跑在 writer-1 之前,那一刻盘上那份多半是昨天的;采信它 =
    把昨天的 BUY 印成今天的。"""
    scan = tmp_path / "2026-08-19"
    _write_decision_file(scan, {"date": "2026-08-18", "mode": "active",
                                "buys": [{"code": "600000"}]})

    assert load_decision(scan) is None


def test_load_decision_survives_absent_and_corrupt_files(tmp_path):
    scan = tmp_path / "2026-08-19"
    scan.mkdir()
    assert load_decision(scan) is None                       # 缺席
    (scan / DECISION_FILENAME).write_text("{不是 json", encoding="utf-8")
    assert load_decision(scan) is None                       # 坏 JSON
    (scan / DECISION_FILENAME).write_text("[1, 2]", encoding="utf-8")
    assert load_decision(scan) is None                       # 不是 dict


def test_load_decision_accepts_an_explicit_expected_date(tmp_path):
    """目录名与数据日解耦的场景(回放/自测)可显式传期望日。"""
    scan = tmp_path / "whatever"
    _write_decision_file(scan, {"date": "2026-08-18", "buys": []})

    assert load_decision(scan, date="2026-08-18") is not None
    assert load_decision(scan, date="2026-08-17") is None


def _write_config(tmp_path, block: dict, monkeypatch) -> None:
    cfg = tmp_path / "scan_config.jsonc"
    cfg.write_text(json.dumps({"relative_buy": block}), encoding="utf-8")
    monkeypatch.setattr("autoresearch.scan.user_config.DEFAULT_PATH", cfg)


def test_configured_relative_buy_defaults_to_shadow_without_config(tmp_path, monkeypatch):
    """缺配置 → shadow/False/None = 内建默认 = 现行为(parity)。`tiering`(v4.0)第五元素
    同样缺键 = False。"""
    monkeypatch.setattr("autoresearch.scan.user_config.DEFAULT_PATH", tmp_path / "nope.jsonc")

    assert configured_relative_buy() == ("shadow", False, None, "finalists", False)
    assert is_active() is False
    assert activate_date() is None
    assert configured_tiering() is False


def test_configured_relative_buy_reads_all_three_knobs(tmp_path, monkeypatch):
    _write_config(tmp_path, {"mode": "active", "exclude_pinned": True,
                             "activate_date": "2026-08-20"}, monkeypatch)

    assert configured_relative_buy() == ("active", True, "2026-08-20", "finalists", False)
    assert is_active() is True
    assert activate_date() == "2026-08-20"


def test_configured_tiering_reads_config_and_defaults(tmp_path, monkeypatch):
    """回滚杆读得到、缺键回内建默认(= v3.0 逐字,parity)——同 `configured_pool` 的既有
    测试手法(两键是同一个裁定的产物,见 scan_config.jsonc 该块注)。"""
    _write_config(tmp_path, {"pool": "finalists", "tiering": True}, monkeypatch)
    assert configured_tiering() is True
    _write_config(tmp_path, {"mode": "active"}, monkeypatch)
    assert configured_tiering() is False         # 缺键 = 内建默认(parity)


def test_configured_relative_buy_degrades_loudly_on_broken_config(tmp_path, monkeypatch, capsys):
    """配置层故障不挡发布,但降级必须留痕(同 user_config.knob 纪律)。"""
    cfg = tmp_path / "scan_config.jsonc"
    cfg.write_text(json.dumps({"relative_buy": {"mode": "nonsense"}}), encoding="utf-8")
    monkeypatch.setattr("autoresearch.scan.user_config.DEFAULT_PATH", cfg)

    assert configured_relative_buy() == ("shadow", False, None, "finalists", False)
    assert "scan_config 读取失败" in capsys.readouterr().err


# ═══════════════════════ v3.0(2026-08-26 §3 路A)═══════════════════════════
#
# 两件事各自可单独回滚,所以各自单独锁:
#   A2 硬门扩集 —— UW/Sell 卡与 `FINAL PROPOSAL: SELL` 不得当 BUY(对两个池都生效);
#   A1/A3 池切换 —— `pool="composite"` 时只在 L3 守卫⑨ 的证据席里选、按 composite 分排。

from autoresearch.scan.relative_buy import (  # noqa: E402
    POOL_COMPOSITE,
    POOL_FINALISTS,
    REDFLAG_EARLY_STOP_REASONS,
    REDFLAG_RATINGS,
    configured_pool,
)


def test_underweight_card_can_no_longer_be_buy(tmp_path):
    """**本波最核心的一条**:2026-08-20 金螳螂、2026-08-25 天味食品两次把卡面 UW、
    `FINAL TRANSACTION PROPOSAL: SELL` 的票发成当日 BUY(v1 已知问题 #6 原话:
    「提议卖出的票可以当相对 BUY 出这条通路是敞开的」)。v3.0 把这条通路焊死。"""
    cands = [
        Cand(code="603317", name="天味食品", composite_rank=1, amount_yi=9.0,
             rating="Underweight", proposal="SELL",
             early_stop={"phase": "P3", "reason": "资金流出"}),
        Cand(code="601766", name="中国中车", composite_rank=50, amount_yi=8.0,
             rating="Hold", early_stop={"phase": "P3", "reason": "其他"}),
    ]
    doc = build_decision(_build_scan(tmp_path, cands), mode=MODE_ACTIVE)
    assert [b["code"] for b in doc["buys"]] == ["601766"]
    veto = next(c for c in doc["candidates"] if c["code"] == "603317")
    assert veto["hard_gate"]["no_redflag"] is False and veto["eligible"] is False


def test_underweight_alone_vetoes_without_a_sell_proposal(tmp_path):
    """**变异探针 M1**:上一条用例的票同时是 UW **且**提案 SELL —— 两条防线各自都能拦住它,
    所以把 `rating in REDFLAG_RATINGS` 改回 `rating == "Sell"`,那条用例照样绿(实测)。
    这里给一只「UW 但提案 HOLD」的票,单独锁住**评级**那条腿。"""
    cands = [
        Cand(code="603317", name="只是UW", composite_rank=1, amount_yi=9.0,
             rating="Underweight", proposal="HOLD"),
        Cand(code="601766", name="干净票", composite_rank=50, amount_yi=8.0, rating="Hold"),
    ]
    doc = build_decision(_build_scan(tmp_path, cands), mode=MODE_ACTIVE)
    assert [b["code"] for b in doc["buys"]] == ["601766"]
    veto = next(c for c in doc["candidates"] if c["code"] == "603317")
    assert veto["hard_gate"]["no_redflag"] is False
    detail = next(e["detail"] for e in doc["excluded"] if e["code"] == "603317")
    assert "Underweight" in detail          # 理由必须点名评级,不是借了提案那条腿


def test_sell_proposal_vetoes_even_when_rating_unreadable(tmp_path):
    """两条独立防线:评级解析可能失手,而 `FINAL TRANSACTION PROPOSAL` 是卡的机读契约行。"""
    cands = [
        Cand(code="603317", name="提案卖出", composite_rank=1, amount_yi=9.0,
             rating="Hold", proposal="SELL"),
        Cand(code="601766", name="干净票", composite_rank=50, amount_yi=8.0, rating="Hold"),
    ]
    doc = build_decision(_build_scan(tmp_path, cands), mode=MODE_ACTIVE)
    assert [b["code"] for b in doc["buys"]] == ["601766"]


def test_hold_with_neutral_earlystop_is_not_vetoed(tmp_path):
    """**反向锁**:{其他, 题材透支, 资金流出} 三档故意留在红灯集之外 —— 它们在隔夜尺上
    对 Hold/UW 无区分力(L4·Hold −0.20 vs UW −0.35 不显著)。把它们也当红灯,就是拿没有
    证据的判断去否决有证据的候选,当天会直接 BLOCKED。"""
    for reason in ("其他", "题材透支", "资金流出"):
        cands = [Cand(code="601766", name="干净票", composite_rank=1, amount_yi=9.0,
                      rating="Hold", early_stop={"phase": "P3", "reason": reason})]
        doc = build_decision(_build_scan(tmp_path / reason, cands), mode=MODE_ACTIVE)
        assert [b["code"] for b in doc["buys"]] == ["601766"], reason


@pytest.mark.parametrize("reason", sorted(REDFLAG_EARLY_STOP_REASONS))  # D8.3 ②:死条目已删,不必再减
def test_redflag_earlystop_reasons_are_vetoed(tmp_path, reason):
    cands = [Cand(code="601766", name="红灯票", composite_rank=1, amount_yi=9.0,
                  rating="Hold", early_stop={"phase": "P3", "reason": reason})]
    doc = build_decision(_build_scan(tmp_path / reason.replace("/", "_"), cands),
                         mode=MODE_ACTIVE)
    assert doc["blocked"] is True


def test_redflag_ratings_content_is_pinned():
    """扩集是规则改动,必须显式露面(同 rule_version 那条哨兵的用意)。

    D8.3 ②(2026-08-31):`"监管/审计红灯"` 从集合里删除——它不在 `l4/parsers.py` 的
    七词早停表内,规则逐字实现下永不命中,是纯死码(ratchet 见
    `tests/scan/test_early_stop_parse.py::test_redflag_reasons_subset_of_closed_set`)。
    """
    assert REDFLAG_RATINGS == frozenset({"Sell", "Underweight"})
    assert REDFLAG_EARLY_STOP_REASONS == frozenset({
        "基本面恶化", "估值透支", "涨停追高", "数据不足"})


def test_composite_pool_only_picks_from_seats(tmp_path):
    """A1/A3:BUY 只在证据席里选。**四面综合分最高的那只如果不是席位,就不该当 BUY** ——
    这正是把 BUY 所有权从判断层搬到证据层的那一步。"""
    cands = [
        # 综合分最高(四面全好)但不是席位
        Cand(code="000034", name="判断层最爱", composite_rank=20, amount_yi=9.0,
             n_channels=4, best_channel_rank=1, rating="Hold", intel="INTEL",
             dossier=True, price_claim="CLEAN"),
        # 席位:composite 最高(rank 越小分位越高),其余面平平
        Cand(code="600188", name="证据席甲", composite_rank=1, amount_yi=3.0,
             n_channels=1, best_channel_rank=30, rating="Hold", intel="NONE", seat=True),
        Cand(code="601699", name="证据席乙", composite_rank=5, amount_yi=2.0,
             n_channels=1, best_channel_rank=40, rating="Hold", intel="NONE", seat=True),
    ]
    scan = _build_scan(tmp_path, cands)
    fin = build_decision(scan, mode=MODE_ACTIVE, pool=POOL_FINALISTS)
    comp = build_decision(scan, mode=MODE_ACTIVE, pool=POOL_COMPOSITE)
    assert [b["code"] for b in fin["buys"]] == ["000034"]      # v2 池:判断层最爱
    assert [b["code"] for b in comp["buys"]] == ["600188"]     # v3 池:composite 最高的席位
    assert comp["pool"] == "composite" and comp["pool_members"] == ["600188", "601699"]
    assert comp["counts"]["in_pool"] == 2
    dropped = {e["code"] for e in comp["excluded"] if e["reason"] == "not_in_pool"}
    assert "000034" in dropped                                # 落选留痕,不静默消失


def test_composite_pool_excludes_a_higher_composite_non_seat(tmp_path):
    """**变异探针 M2**:上一条用例里非席位票的 composite 低于席位,所以就算把池过滤整段
    删掉,「按 composite 排」也会给出同一个答案 —— 那条用例对池过滤零鉴别力(实测)。

    这里让**非席位票的 composite 全场最高**(现实里它可能因 📌/追高/ST 被剔出席位):
    只有池过滤真的在,BUY 才会落到席位上。"""
    cands = [
        Cand(code="000034", name="composite 全场最高但不是席位", composite_rank=1,
             amount_yi=9.0, rating="Hold"),
        Cand(code="600188", name="证据席", composite_rank=40, amount_yi=3.0,
             rating="Hold", seat=True),
    ]
    doc = build_decision(_build_scan(tmp_path, cands), mode=MODE_ACTIVE,
                         pool=POOL_COMPOSITE)
    assert [b["code"] for b in doc["buys"]] == ["600188"]
    by = {c["code"]: c for c in doc["candidates"]}
    assert by["000034"]["faces"]["target_align"] > by["600188"]["faces"]["target_align"]


def test_composite_pool_ranks_by_composite_not_borda(tmp_path):
    """排序改按 `target_align`(composite 分位)——另三面在隔夜尺上无证据,降为记录列。
    变异探针:若排序仍用 Borda 平均,下面这只 evidence/recall 更好的席位会夺冠。"""
    cands = [
        Cand(code="600188", name="composite 更高", composite_rank=1, amount_yi=2.0,
             n_channels=1, best_channel_rank=40, rating="Hold", intel="NONE", seat=True),
        Cand(code="601699", name="四面更好", composite_rank=30, amount_yi=9.0,
             n_channels=4, best_channel_rank=1, rating="Hold", intel="INTEL",
             dossier=True, price_claim="CLEAN", seat=True),
    ]
    doc = build_decision(_build_scan(tmp_path, cands), mode=MODE_ACTIVE, pool=POOL_COMPOSITE)
    assert [b["code"] for b in doc["buys"]] == ["600188"]
    by = {c["code"]: c for c in doc["candidates"]}
    assert by["601699"]["relative_decision_score"] > by["600188"]["relative_decision_score"]


def test_composite_pool_blocks_honestly_when_no_seats(tmp_path):
    """席位为空(守卫⑨ 关了 / 当日 L2 表缺 composite 列)→ 诚实 BLOCKED,
    **不悄悄退回全体池** —— 静默回退等于当天的 BUY 换了一套规则却没人知道。"""
    cands = [Cand(code="601766", name="非席位", composite_rank=1, amount_yi=9.0,
                  rating="Hold")]
    doc = build_decision(_build_scan(tmp_path, cands), mode=MODE_ACTIVE, pool=POOL_COMPOSITE)
    assert doc["blocked"] is True and doc["pool_members"] == []
    assert any(r["reason"] == "not_in_pool" for r in doc["blocked_reasons"])


def test_pool_finalists_keeps_candidate_table_intact(tmp_path):
    """池只决定**谁能当 BUY**,不裁剪候选表(观测语义不变,同 exclude_pinned 的既定纪律)。"""
    cands = [Cand(code=c, name=f"票{c}", composite_rank=i + 1, amount_yi=9.0 - i,
                  rating="Hold", seat=(i == 2)) for i, c in enumerate(
                      ["000034", "600188", "601699"])]
    scan = _build_scan(tmp_path, cands)
    fin = build_decision(scan, mode=MODE_ACTIVE, pool=POOL_FINALISTS)
    comp = build_decision(scan, mode=MODE_ACTIVE, pool=POOL_COMPOSITE)
    assert len(fin["candidates"]) == len(comp["candidates"]) == 3
    assert all(c["in_pool"] for c in fin["candidates"])


def test_illegal_pool_raises(tmp_path):
    with pytest.raises(ValueError, match="pool 只接受"):
        build_decision(_build_scan(tmp_path, _RANK_CANDS), pool="whatever")


def test_configured_pool_reads_config_and_defaults(tmp_path, monkeypatch):
    """回滚杆读得到、缺键回内建默认(= v2 候选池,parity)。
    走本文件既有的 `_write_config`(patch `DEFAULT_PATH`)—— `tests/scan/conftest.py` 的
    autouse fixture 把 `DEFAULT_PATH` 钉在一个不存在的路径上(防真配置渗进 tmp_path 测试),
    所以靠 chdir + 造 `.claude/` 目录那套在本目录里永远读不到。"""
    _write_config(tmp_path, {"pool": "composite"}, monkeypatch)
    assert configured_pool() == POOL_COMPOSITE
    _write_config(tmp_path, {"mode": "active"}, monkeypatch)
    assert configured_pool() == POOL_FINALISTS          # 缺键 = 内建默认(parity)


def test_illegal_pool_in_config_degrades_loudly(tmp_path, monkeypatch, capsys):
    """错型不静默生效(同 knob 纪律):回落 finalists + stderr 留痕。"""
    _write_config(tmp_path, {"pool": "whatever"}, monkeypatch)
    assert configured_pool() == POOL_FINALISTS
    assert "relative_buy.pool" in capsys.readouterr().err


# ═══════════════════════ schema 2(Task 7,2026-09-12 scene-reconstruction §7.2)═══════
#
# `_relative_buy_decision.json` 升 schema 2:候选新增 `card_context`(卡面原文保守解析)/
# `observation_rank`(= 旧 `rank` 的显式别名);顶层新增 `selection`(实际池/实际顺序/
# 排序依据/池内名次)、`veto_accounting`(否决股票去重计数 + 逐门命中数)、`field_usage`
# (字段真实角色表)、`conflicts`(选中票卡面与选择之间的展示性冲突)、`why`(固定渲染的
# 人读解释)。**schema 1 的每一个键与取值逐字不变**——第一条测试就是这件事的 golden 锁,
# 其余测试都建立在"投影不变"这同一份契约之上。

from autoresearch.scan.relative_buy import (  # noqa: E402
    _FACES,
    _HARD_GATES,
    FIELD_USAGE,
    _field_usage,
    safe_write_decision,
)
from autoresearch.trace.blobs import blob_path  # noqa: E402


def _write_card(scan: Path, code: str, text: str) -> Path:
    details = scan / "details"
    details.mkdir(parents=True, exist_ok=True)
    path = details / f"{code}.md"
    path.write_text(text, encoding="utf-8")
    return path


# 真实卡面形状,直接借用 task-6-brief 给定的字面样本族(与
# `tests/scan/test_parsers_card_context.py` 同一形状,证据来源同一份契约)。
_CARD_ALLOWED_A = "\n".join([
    "# 决策卡 — 002345 示例票 @ 2026-08-06",
    "| 评级 | 现价 | EV目标(T+2 开盘预期带) | R:R | 仓位 | 触发位 |",
    "|---|---|---|---|---|---|",
    "| Hold | 10 | +3%~-1% | 1.2 : 1 | 明确允许新开仓，仓位 10% | 跌破 9 清 |",
    "- [执行线] pct_chg <= 3.0 → 当日涨超 3% 放弃本次尾盘入场",
    "- [执行线] pos_in_range < 0.7 → 收在当日区间上 30% 放弃",
    "FINAL TRANSACTION PROPOSAL: **HOLD**",
])
#: 同一票,EV/仓位/执行线数值全部换掉,entry_stance 仍是 ALLOWED——E05 display_only 不变性
#: 测试专用:只有数值变了,规则读的字段(hard_gate/faces)一个没碰。
_CARD_ALLOWED_B = "\n".join([
    "# 决策卡 — 002345 示例票 @ 2026-08-06",
    "| 评级 | 现价 | EV目标(T+2 开盘预期带) | R:R | 仓位 | 触发位 |",
    "|---|---|---|---|---|---|",
    "| Hold | 10 | +8%~-4% | 2.5 : 1 | 明确允许新开仓，仓位 30% | 跌破 7 清 |",
    "- [执行线] pct_chg <= 5.0 → 当日涨超 5% 放弃本次尾盘入场",
    "- [执行线] pos_in_range < 0.4 → 收在当日区间上 60% 放弃",
    "FINAL TRANSACTION PROPOSAL: **HOLD**",
])
_CARD_PROHIBITED = "\n".join([
    "# 决策卡 — 002345 示例票 @ 2026-08-06",
    "| 评级 | 现价 | EV目标(T+2 开盘预期带) | R:R | 仓位 | 触发位 |",
    "|---|---|---|---|---|---|",
    "| Hold | 10 | +3%~-1% | 1.2 : 1 | 0%(不新建仓) | 跌破 9 清 |",
    "FINAL TRANSACTION PROPOSAL: **HOLD**",
])
_CARD_CONDITIONAL = "\n".join([
    "# 决策卡 — 002345 示例票 @ 2026-08-06",
    "| 评级 | 现价 | EV目标(T+2 开盘预期带) | R:R | 仓位 | 触发位 |",
    "|---|---|---|---|---|---|",
    "| Hold | 10 | +3%~-1% | 1.2 : 1 | 待突破确认 | 跌破 9 清 |",
    "FINAL TRANSACTION PROPOSAL: **HOLD**",
])
_CARD_CORRUPTED = "\n".join([
    "# 决策卡 — 002345 示例票 @ 2026-08-06",
    "| 评级 | 现价 | EV目标(T+2 开盘预期带) | R:R | 仓位 | 触发位 |",
    "|---|---|---|---|---|---|",
    "| Hold | 10 | 坏数据??? | N/A : 1 | 10% | — |",
    "- [执行线] pct_chg <= abc → 放弃",
    "FINAL TRANSACTION PROPOSAL: **HOLD**",
])


def _projection(doc: dict) -> dict:
    """brief bullet 1/11 的「决策投影」子集:buys/blocked/pool/second_buy/excluded +
    每票 eligibility/hard_gate/rank/observation_rank/relative_decision_score/faces/
    faces_missing/in_pool。**不含** card_context/selection/veto_accounting/conflicts/
    why/field_usage——那些是本任务新增的观测层,display_only 不变性只保证这个子集不变,
    新增观测层本身当然会随卡面输入变化(否则它就没在记录任何东西)。
    """
    return {
        "buys": doc["buys"], "blocked": doc["blocked"],
        "blocked_reasons": doc["blocked_reasons"], "pool": doc["pool"],
        "second_buy": doc["second_buy"], "excluded": doc["excluded"],
        "candidates": [
            {k: row[k] for k in (
                "code", "eligible", "hard_gate", "rank", "observation_rank",
                "relative_decision_score", "faces", "faces_missing", "in_pool",
            )}
            for row in doc["candidates"]
        ],
    }


# ── bullet 1:golden 投影 —— 本任务全部新增字段的安全网 ──────────────────────
def test_schema_2_golden_projection_is_unchanged_by_new_fields(tmp_path):
    """用 `_RANK_CANDS`(docstring 里那组"等权平均 vs 乘积会给出相反结论"的主用例)锁死
    schema 1 的每一个既有字段——数值取自本任务改动**之前**对同一 fixture 的真实运行
    (`build_decision` 逐字节 dump,未做任何人工调整)。只要这条测试还绿,后面加的任何
    新字段都不可能悄悄改掉一天的 BUY/排名/分数/合格性。
    """
    doc = build_decision(_build_scan(tmp_path, _RANK_CANDS))

    assert doc["schema_version"] == SCHEMA_VERSION == 2
    assert doc["rule_version"] == RULE_VERSION == "e6.v4.1"
    assert doc["blocked"] is False
    assert doc["blocked_reasons"] == []
    assert doc["buys"] == [{"basis": "relative", "code": "002345", "rank": 1}]
    assert doc["second_buy"] == {"fired": False, "reason": "v1 影子期无已验证阈值",
                                 "threshold": None}
    assert doc["pool"] == "finalists"
    assert doc["excluded"] == []
    assert doc["counts"] == {
        "buys": 1, "candidates": 4, "eligible": 4, "excluded_rows": 0, "in_pool": 4,
        "orphan_finalists": 0, "orphan_rated": 0, "with_missing_face": 0,
    }

    by = _by_code(doc)
    expected = {
        "000034": {"rank": 2, "observation_rank": 2, "relative_decision_score": 0.625,
                   "eligible": True, "in_pool": True,
                   "faces": {"target_align": 0.625, "recall_strength": 0.625,
                             "evidence": 0.625, "risk_safety": 0.625}},
        "002345": {"rank": 1, "observation_rank": 1, "relative_decision_score": 0.6875,
                   "eligible": True, "in_pool": True,
                   "faces": {"target_align": 0.125, "recall_strength": 0.875,
                             "evidence": 0.875, "risk_safety": 0.875}},
        "600188": {"rank": 3, "observation_rank": 3, "relative_decision_score": 0.5,
                   "eligible": True, "in_pool": True,
                   "faces": {"target_align": 0.875, "recall_strength": 0.375,
                             "evidence": 0.375, "risk_safety": 0.375}},
        "601699": {"rank": 4, "observation_rank": 4, "relative_decision_score": 0.1875,
                   "eligible": True, "in_pool": True,
                   "faces": {"target_align": 0.375, "recall_strength": 0.125,
                             "evidence": 0.125, "risk_safety": 0.125}},
    }
    for code, want in expected.items():
        row = by[code]
        assert row["rank"] == want["rank"], code
        assert row["observation_rank"] == want["observation_rank"], code
        assert row["relative_decision_score"] == want["relative_decision_score"], code
        assert row["eligible"] == want["eligible"], code
        assert row["in_pool"] == want["in_pool"], code
        assert row["faces"] == want["faces"], code
        assert row["faces_missing"] == [], code
        assert row["hard_gate"] == {"tradable": True, "data_a": True,
                                    "contract": True, "no_redflag": True}, code

    # 新字段确实新增了(不是名字换了旧字段),旧字段没有一个消失
    for row in doc["candidates"]:
        assert "card_context" in row
        assert "observation_rank" in row
    for key in ("selection", "veto_accounting", "field_usage", "conflicts", "why"):
        assert key in doc


def test_display_only_invariance_helper_covers_exactly_the_old_schema_1_fields(tmp_path):
    """`_projection` 本身的自检:同一 fixture 反复 `build_decision` 两次(不碰任何卡),
    投影必须逐字相等——这是后面几条"改了 display_only 输入,投影不变"测试能成立的地基。
    """
    scan = _build_scan(tmp_path, _RANK_CANDS)
    assert _projection(build_decision(scan)) == _projection(build_decision(scan))


# ── E03:composite 池实际顺序与全体观察 rank 不同 ─────────────────────────────
def test_composite_pool_selection_uses_pool_rank_not_observation_rank_in_why(tmp_path):
    """600188 在 composite 池里排第 1(target_align 更高)当选 BUY,但它在全体 eligible
    的观察排名(四面 Borda 更低)里只排第 2——`selection.winner.pool_rank` 与 `why` 都必须
    用池内第 1 名,绝不能把"全体观察第 2 名"读成"池内第 2 名"(controller 描述的那个
    具体 bug 场景)。数值取自对同一 fixture 的真实运行(与
    `test_composite_pool_ranks_by_composite_not_borda` 同一 fixture)。
    """
    cands = [
        Cand(code="600188", name="composite 更高", composite_rank=1, amount_yi=2.0,
             n_channels=1, best_channel_rank=40, rating="Hold", intel="NONE", seat=True),
        Cand(code="601699", name="四面更好", composite_rank=30, amount_yi=9.0,
             n_channels=4, best_channel_rank=1, rating="Hold", intel="INTEL",
             dossier=True, price_claim="CLEAN", seat=True),
    ]
    doc = build_decision(_build_scan(tmp_path, cands), mode=MODE_ACTIVE, pool=POOL_COMPOSITE)
    by = _by_code(doc)

    # 前提自证:两个 rank 真的不同,这条用例才有鉴别力
    assert by["600188"]["rank"] == 2
    assert by["600188"]["observation_rank"] == 2
    assert by["601699"]["rank"] == 1
    assert doc["buys"][0]["code"] == "600188"

    selection = doc["selection"]
    assert selection["pool"] == "composite"
    assert selection["codes"] == ["600188", "601699"]
    assert selection["winner"] == {"code": "600188", "pool_rank": 1}
    assert selection["sort_keys"]["names"] == ["target_align", "amount", "code"]
    assert selection["sort_keys"]["directions"] == ["desc", "desc", "asc"]
    assert selection["sort_keys"]["values"]["600188"] == [0.75, 2.0, "600188"]
    assert selection["sort_keys"]["values"]["601699"] == [0.25, 9.0, "601699"]
    assert selection["population"] == {
        "candidates": 2, "passed_hard_gates": 2,
        "after_pinned_exclusion": 2, "final_pool": 2,
    }

    # why 必须点名池内第 1 名,绝不能印成观察第 2 名
    assert "第 1 名" in doc["why"]
    assert "第 2 名" not in doc["why"]
    assert "观察 rank=2" in doc["why"]


# ── E04:一票触发多硬门 ────────────────────────────────────────────────────
def test_veto_accounting_dedupes_stocks_but_lists_each_gate_hit_separately(tmp_path):
    double_veto = Cand(code="000998", name="双门否决票", in_universe=False, rating="Sell",
                       composite_rank=1, amount_yi=9.0)
    clean = Cand(code="000034", name="正常票", composite_rank=2, amount_yi=8.0, rating="Hold")
    doc = build_decision(_build_scan(tmp_path, [double_veto, clean]))

    by = _by_code(doc)
    assert by["000998"]["eligible"] is False
    assert by["000998"]["hard_gate"]["tradable"] is False
    assert by["000998"]["hard_gate"]["no_redflag"] is False
    assert by["000998"]["hard_gate"]["data_a"] is True
    assert by["000998"]["hard_gate"]["contract"] is True

    veto = doc["veto_accounting"]
    assert veto["vetoed_stocks"] == 1            # 按 code 去重:恒一只
    assert veto["vetoed_codes"] == ["000998"]
    assert veto["by_gate"] == {"tradable": 1, "data_a": 0, "contract": 0, "no_redflag": 1}
    assert veto["population"] == {
        "candidates": 2, "passed_hard_gates": 1,
        "after_pinned_exclusion": 1, "final_pool": 1,
    }
    # "合格"不得混用:候选数/过硬门数/final_pool 三个不同的数,键名各自独立
    assert veto["population"]["candidates"] != veto["population"]["passed_hard_gates"]


@pytest.mark.parametrize("tiering", [False, True])
def test_field_usage_derives_from_hard_gates_and_faces_without_inventing_a_new_gate(
    tmp_path, tiering,
):
    """v4.0(controller Ruling P21 (a)/(c)):`field_usage` 是 `tiering` 的函数,不是恒定
    常量——`FIELD_USAGE` 模块常量只是 tiering=False 那一份的快照。hard_gate/ranking 两列
    不随 tiering 变(veto 走的是既有 `no_redflag` 门名,A/R 分级不是排序键)——「不发明
    新门」这条约束在两个 tiering 值下都要成立,不是只在关的时候成立。
    """
    doc = build_decision(_build_scan(tmp_path, _RANK_CANDS), tiering=tiering)
    fu = doc["field_usage"]
    assert fu == _field_usage(tiering)                    # 每天同一份派生,byte 稳定
    if not tiering:
        assert fu == FIELD_USAGE                          # v3.0 快照 = tiering=False 那份
    assert fu["hard_gate"]["fields"] == list(_HARD_GATES)          # 不发明新门
    assert fu["ranking"]["fields"] == [*_FACES, "amount", "code"]  # A/R 分级不是排序键
    display_only = set(fu["display_only"]["fields"])
    assert display_only & set(fu["hard_gate"]["fields"]) == set()
    assert display_only & set(fu["ranking"]["fields"]) == set()
    assert all(f.startswith("card_context.") for f in display_only)
    assert "research_rating" in fu and "hard_gate" in fu["research_rating"]["role"]
    assert "l4_proposal" in fu and "hard_gate" in fu["l4_proposal"]["role"]

    if tiering:
        # entry_stance 离开 display_only,搬进它自己的新键,点名两个真实角色。
        assert "card_context.entry_stance" not in display_only
        assert "entry_stance" in fu
        assert "no_redflag" in fu["entry_stance"]["role"]
        assert "ALLOWED" in fu["entry_stance"]["role"]
    else:
        # 关掉时逐字 v3.0:entry_stance 仍是纯 display_only,没有 entry_stance 顶层键。
        assert "card_context.entry_stance" in display_only
        assert "entry_stance" not in fu


# ── 卡缺失:card_context 诚实降级,选择不受影响 ────────────────────────────
def test_missing_card_is_absent_source_and_unknown_card_context_without_blocking_buy(tmp_path):
    doc = build_decision(_build_scan(tmp_path, _RANK_CANDS))
    by = _by_code(doc)
    card = by["002345"]["card_context"]
    assert card["card_kind"] == "unknown"
    assert card["parse_status"] == "ERROR"
    assert card["entry_stance"] == "UNKNOWN"
    assert card["source"] == {
        "relative_path": None, "card_sha256": None,
        "snapshot_quality": "unarchived", "blob_digest": None,
        "archive_error": None, "post_hoc": False,
    }
    assert doc["buys"][0]["code"] == "002345"          # 卡缺失不影响选择
    assert doc["conflicts"] == [{
        "code": "002345", "type": "card_missing_or_unparseable",
        "detail": f"card_context.parse_errors={card['parse_errors']}",
    }]


# ── E05:display_only 输入改动/损坏 → 决策投影不变,错误有账 ──────────────────
def test_corrupted_card_leaves_decision_projection_unchanged_and_accounts_the_parse_error(
    tmp_path,
):
    scan = _build_scan(tmp_path, _RANK_CANDS)
    baseline = _projection(build_decision(scan))

    _write_card(scan, "002345", _CARD_CORRUPTED)
    doc = json.loads(write_decision(scan).read_text(encoding="utf-8"))

    assert _projection(doc) == baseline
    card = _by_code(doc)["002345"]["card_context"]
    assert card["parse_status"] == "PARTIAL"
    assert any("pct_chg" in e for e in card["parse_errors"])
    assert card["exec_lines"]["pct_chg"]["presence"] is True
    assert card["exec_lines"]["pct_chg"]["threshold"] is None
    assert card["ev_target"] == "坏数据???"


def test_display_only_card_fields_never_change_the_decision_projection(tmp_path):
    scan = _build_scan(tmp_path, _RANK_CANDS)
    _write_card(scan, "002345", _CARD_ALLOWED_A)
    doc_a = json.loads(write_decision(scan).read_text(encoding="utf-8"))

    _write_card(scan, "002345", _CARD_ALLOWED_B)       # EV/仓位/执行线数值全部换掉
    doc_b = json.loads(write_decision(scan).read_text(encoding="utf-8"))

    assert _projection(doc_a) == _projection(doc_b)
    card_a = _by_code(doc_a)["002345"]["card_context"]
    card_b = _by_code(doc_b)["002345"]["card_context"]
    assert card_a["ev_target"] != card_b["ev_target"]
    assert card_a["position_raw"] != card_b["position_raw"]
    assert card_a["entry_stance"] == card_b["entry_stance"] == "ALLOWED"


# ── E05 的 tiering=True 对照(addendum P21 (b)):关掉的那半仍然成立,entry_stance
# 自己legitimately 移动投影这另一半也要有测试,不能只测「幸存的 8 个不动」那半 ──────
def test_display_only_card_fields_never_change_the_decision_projection_under_tiering(tmp_path):
    """tiering=True 时,entry_stance **之外**的 8 个 display_only 字段照样不改投影——
    开关本身不得把别的字段也顺带拖进决策路径(那会一次改两件事,鉴别力就丢了)。"""
    scan = _build_scan(tmp_path, _RANK_CANDS)
    _write_card(scan, "002345", _CARD_ALLOWED_A)
    doc_a = json.loads(write_decision(scan, tiering=True).read_text(encoding="utf-8"))

    _write_card(scan, "002345", _CARD_ALLOWED_B)       # EV/仓位/执行线数值全部换掉,entry_stance 仍 ALLOWED
    doc_b = json.loads(write_decision(scan, tiering=True).read_text(encoding="utf-8"))

    assert _projection(doc_a) == _projection(doc_b)
    assert doc_a["buys"][0] == {"code": "002345", "basis": "card_backed", "rank": 1, "tier": "A"}


def test_entry_stance_legitimately_changes_the_decision_projection_under_tiering(tmp_path):
    """E05 的另一半:entry_stance 是这九个字段里**唯一**真的改投影的那个——tiering=True
    下从 ALLOWED 换成 PROHIBITED 必须真的否决候选、真的换 BUY,不然"A/R 分级是真实约束"
    这句话就是空的(只测"别的字段不动"而不测"这个字段真的动",会漏掉同一个 bug 一格)。
    """
    scan = _build_scan(tmp_path, _RANK_CANDS)
    _write_card(scan, "002345", _CARD_ALLOWED_A)
    allowed = json.loads(write_decision(scan, tiering=True).read_text(encoding="utf-8"))

    _write_card(scan, "002345", _CARD_PROHIBITED)
    prohibited = json.loads(write_decision(scan, tiering=True).read_text(encoding="utf-8"))

    assert _projection(allowed) != _projection(prohibited)
    assert allowed["buys"][0]["code"] == "002345" and allowed["buys"][0]["tier"] == "A"
    assert _by_code(allowed)["002345"]["hard_gate"]["no_redflag"] is True
    assert _by_code(prohibited)["002345"]["hard_gate"]["no_redflag"] is False
    assert prohibited["buys"][0]["code"] == "000034" and prohibited["buys"][0]["tier"] == "R"


def test_prohibited_card_does_not_block_buy_but_surfaces_as_conflict(tmp_path):
    scan = _build_scan(tmp_path, _RANK_CANDS)
    baseline = _projection(build_decision(scan))

    _write_card(scan, "002345", _CARD_PROHIBITED)      # 冠军自己的卡写"0%(不新建仓)"
    doc = json.loads(write_decision(scan).read_text(encoding="utf-8"))

    assert _projection(doc) == baseline                # 决策投影分毫不变(不新增门)
    assert doc["blocked"] is False
    assert doc["buys"][0]["code"] == "002345"
    card = _by_code(doc)["002345"]["card_context"]
    assert card["entry_stance"] == "PROHIBITED"
    assert doc["conflicts"] == [{
        "code": "002345", "type": "card_says_prohibited",
        "detail": (f"卡面 entry_stance=PROHIBITED(position_raw="
                  f"{card['position_raw']!r}, trigger_raw={card['trigger_raw']!r})"
                  f",E6 仍选中为 BUY"),
    }]


def test_conditional_card_surfaces_as_condition_not_shown_met_conflict(tmp_path):
    scan = _build_scan(tmp_path, _RANK_CANDS)
    baseline = _projection(build_decision(scan))

    _write_card(scan, "002345", _CARD_CONDITIONAL)
    doc = json.loads(write_decision(scan).read_text(encoding="utf-8"))

    assert _projection(doc) == baseline
    assert doc["buys"][0]["code"] == "002345"
    assert doc["conflicts"][0]["type"] == "condition_not_shown_met"
    assert doc["conflicts"][0]["code"] == "002345"


def test_selection_and_why_are_well_formed_when_blocked(tmp_path):
    cands = [Cand(code="30075" + str(i), name=f"票{i}", rating="Sell",
                  composite_rank=i + 1, n_channels=2, best_channel_rank=i + 1)
             for i in range(3)]
    doc = build_decision(_build_scan(tmp_path, cands))
    assert doc["blocked"] is True

    selection = doc["selection"]
    assert selection["codes"] == []
    assert selection["winner"] == {"code": None, "pool_rank": None}
    assert selection["buys_count"] == 0
    assert doc["conflicts"] == []
    assert doc["why"] == "当日 BLOCKED,无 BUY 可解释(候选/硬门/池过滤后无入选)。"

    veto = doc["veto_accounting"]
    assert veto["vetoed_stocks"] == 3
    assert veto["vetoed_codes"] == ["300750", "300751", "300752"]
    assert veto["by_gate"]["no_redflag"] == 3


# ── 卡快照归档(spec §7.2:active run 复用 trace.blobs;无 active run → unarchived)──
def test_write_decision_archives_card_snapshot_under_active_capsule(tmp_path):
    run_root = tmp_path / "run"
    scan = _build_scan(run_root / "staging", _RANK_CANDS)
    capsule = run_root / "capsule"
    (capsule / "identity").mkdir(parents=True)
    _write_card(scan, "002345", _CARD_ALLOWED_A)

    target = write_decision(scan)
    doc = json.loads(target.read_text(encoding="utf-8"))
    source = _by_code(doc)["002345"]["card_context"]["source"]

    assert source["snapshot_quality"] == "archived"
    assert source["relative_path"] == "details/002345.md"
    assert source["card_sha256"] == hashlib.sha256(
        _CARD_ALLOWED_A.encode("utf-8")).hexdigest()
    # 无密钥的普通卡:脱敏是 no-op,归档字节等于原文,digest 因此等于 card_sha256
    assert source["blob_digest"] == source["card_sha256"]
    blob = blob_path(capsule, source["blob_digest"])
    assert blob.is_file()
    assert blob.read_text(encoding="utf-8") == _CARD_ALLOWED_A
    assert source["post_hoc"] is False

    # 归档成功与否不改变选择
    assert doc["buys"][0]["code"] == "002345"


def test_write_decision_marks_card_unarchived_without_active_capsule(tmp_path):
    scan = _build_scan(tmp_path, _RANK_CANDS)      # 没有 run/capsule/identity 那棵树
    _write_card(scan, "002345", _CARD_ALLOWED_A)

    doc = json.loads(write_decision(scan).read_text(encoding="utf-8"))
    source = _by_code(doc)["002345"]["card_context"]["source"]

    assert source["snapshot_quality"] == "unarchived"
    assert source["blob_digest"] is None
    assert source["card_sha256"] == hashlib.sha256(
        _CARD_ALLOWED_A.encode("utf-8")).hexdigest()
    assert doc["buys"][0]["code"] == "002345"      # 归档失败/跳过不影响选择


# ── E06:writer parity + 卡中途变化走 mismatch 通道 ───────────────────────────
def test_write_then_verify_with_unchanged_real_card_is_still_byte_parity(tmp_path):
    scan = _build_scan(tmp_path, _RANK_CANDS)
    _write_card(scan, "002345", _CARD_ALLOWED_A)
    written = write_decision(scan)
    before = written.read_bytes()

    result = verify_decision(scan)

    assert result == {"match": True, "action": "noop", "path": str(written)}
    assert written.read_bytes() == before
    assert not (scan / MISMATCH_FILENAME).exists()
    assert not (scan / "gate_fires.csv").exists()


def test_verify_detects_card_changed_mid_flight_and_routes_through_mismatch_not_overwrite(
    tmp_path,
):
    scan = _build_scan(tmp_path, _RANK_CANDS)
    _write_card(scan, "002345", _CARD_ALLOWED_A)
    written = write_decision(scan)
    original_bytes = written.read_bytes()
    original_doc = json.loads(original_bytes)
    assert original_doc["buys"][0]["code"] == "002345"

    _write_card(scan, "002345", _CARD_PROHIBITED)   # 中途卡内容改变(entry_stance 变了)

    result = verify_decision(scan)

    assert written.read_bytes() == original_bytes    # 已发布答案绝不覆盖
    assert result["match"] is False
    assert result["action"] == "mismatch"
    assert (scan / MISMATCH_FILENAME).exists()
    rows = _gate_fires_rows(scan)
    assert len(rows) == 1
    assert rows[0]["check"] == MISMATCH_CHECK_NAME
    assert fail_class(rows[0]["check"]) == "data"


def test_safe_write_decision_also_archives_card_snapshot(tmp_path):
    """`safe_write_decision` 只是失败纪律外壳,正常路径行为必须与 `write_decision` 一致
    ——包括 schema 2 的卡快照/归档。"""
    scan = _build_scan(tmp_path, _RANK_CANDS)
    _write_card(scan, "002345", _CARD_ALLOWED_A)
    target = safe_write_decision(scan)
    assert target is not None
    doc = json.loads(target.read_text(encoding="utf-8"))
    assert _by_code(doc)["002345"]["card_context"]["parse_status"] == "OK"


# ═══════════════════════ fix round 1(review 2026-09-13,6 findings + "ALSO")═══════════
#
# 首轮评审:Spec ❌,行为逐条正确,缺口全在测试覆盖(finding 1/2)与两处生产代码的留痕
# 纪律(finding 3/4)、一处防漂移闭环(finding 5)、一处重复计算(finding 6),外加评审
# 主动指出的 concern 1 该关掉(ALSO)。

from autoresearch.contracts.agent_output import (  # noqa: E402
    EXEC_LINE_MAX_PCT_1D,
    EXEC_LINE_MAX_POS_IN_RANGE,
)


# ── finding 1:finalists(默认池)的 why/selection 从未被断言过实际数值 ────────────
def test_finalists_pool_why_and_sort_keys_match_the_actual_selected_order(tmp_path):
    """brief bullet 7:"composite **与 finalists** 的解释都与实际选中顺序一致"——round 1
    只对 composite 池与 blocked 边界断言过 `why`/`selection` 的内容,finalists(默认池)
    只测过键存在。用 `_RANK_CANDS`(docstring 自带的 Borda 主用例,四面顺序刻意与乘积
    顺序相反)锁死实际数值:排序依据、每只的排序键值、`why` 的渲染文本。
    """
    doc = build_decision(_build_scan(tmp_path, _RANK_CANDS))
    selection = doc["selection"]

    assert selection["pool"] == "finalists"
    assert selection["codes"] == ["002345", "000034", "600188", "601699"]
    assert selection["sort_keys"]["names"] == [
        "relative_decision_score", "target_align", "amount", "code"]
    assert selection["sort_keys"]["directions"] == ["desc", "desc", "desc", "asc"]
    assert selection["sort_keys"]["values"] == {
        "002345": [0.6875, 0.125, 9.0, "002345"],
        "000034": [0.625, 0.625, 8.0, "000034"],
        "600188": [0.5, 0.875, 7.0, "600188"],
        "601699": [0.1875, 0.375, 6.0, "601699"],
    }
    assert selection["winner"] == {"code": "002345", "pool_rank": 1}

    assert "002345 是 finalists 池(共 4 只)第 1 名" in doc["why"]
    assert ("排序依据:relative_decision_score=0.6875、target_align=0.125、amount=9.0、"
           "code=002345。") in doc["why"]


# ── finding 2:exclude_pinned=True 与 composite not_in_pool 从未在 schema 2 层被断言 ──
def test_selection_exclusions_and_after_pinned_exclusion_reflect_a_real_pinned_holding(
    tmp_path,
):
    """`exclude_pinned=True` 且 rank1 是📌持仓:`selection.exclusions.pinned_holding`
    非空,`after_pinned_exclusion` 真的比 `passed_hard_gates` 少 1——round 1 的 schema 2
    测试没有一条传过 `exclude_pinned=True`。"""
    top = replace(_RANK_CANDS[0], pinned=True)          # 002345——不排除时的 rank1
    second = _RANK_CANDS[1]                              # 000034——不排除时的 rank2
    doc = build_decision(_build_scan(tmp_path, [top, second]), exclude_pinned=True)

    assert doc["buys"][0]["code"] == "000034"
    selection = doc["selection"]
    assert selection["exclusions"]["pinned_holding"] == ["002345"]
    assert selection["exclusions"]["not_in_pool"] == []
    assert selection["population"] == {
        "candidates": 2, "passed_hard_gates": 2,
        "after_pinned_exclusion": 1, "final_pool": 1,
    }
    veto = doc["veto_accounting"]
    assert veto["population"]["after_pinned_exclusion"] == 1
    assert veto["vetoed_stocks"] == 0            # 📌排除不是否决,两个流水口径不同


def test_selection_exclusions_records_a_composite_day_not_in_pool_candidate(tmp_path):
    """composite 池且存在至少一只不在证据席的候选:`selection.exclusions.not_in_pool`
    非空——round 1 的 composite 测试都恰好全体候选皆席位或恰好只测了 `excluded` 顶层
    数组,从未在 schema 2 的 `selection` 视角断言过这个字段有值。"""
    cands = [
        Cand(code="000034", name="非席位", composite_rank=20, amount_yi=9.0,
             n_channels=4, best_channel_rank=1, rating="Hold", intel="INTEL",
             dossier=True, price_claim="CLEAN"),
        Cand(code="600188", name="证据席", composite_rank=1, amount_yi=3.0,
             n_channels=1, best_channel_rank=30, rating="Hold", intel="NONE", seat=True),
    ]
    doc = build_decision(_build_scan(tmp_path, cands), mode=MODE_ACTIVE, pool=POOL_COMPOSITE)

    assert doc["buys"][0]["code"] == "600188"
    selection = doc["selection"]
    assert selection["exclusions"]["not_in_pool"] == ["000034"]
    assert selection["exclusions"]["pinned_holding"] == []
    assert selection["population"] == {
        "candidates": 2, "passed_hard_gates": 2,
        "after_pinned_exclusion": 2, "final_pool": 1,
    }


# ── finding 3:卡缺席与读取失败必须分开留痕 ───────────────────────────────────────
def test_unreadable_card_is_recorded_distinctly_from_a_missing_card(tmp_path):
    """一只票的 `details/<code>.md` 是个**目录**(名字对、glob 会找到,但 `.read_text()`
    必然抛 `IsADirectoryError`——`OSError` 子类,不靠 chmod,跨平台稳定可复现)。它必须被
    记成"读取失败"而不是"卡缺失":`parse_errors` 点名读取失败与具体异常,不能印
    "卡片正文为空或缺失"这句不实的话;`source.relative_path` 是已知的(glob 找到过这个
    路径),`source.card_sha256` 是 `None`(没读到内容,算不出 hash)——这对字段组合与
    "压根没有这张卡"(两个都是 `None`)结构上可辨。同一份 fixture 里另一只票压根没有
    `details/*.md`,用作"缺席"对照组。
    """
    scan = _build_scan(tmp_path, _RANK_CANDS)
    details = scan / "details"
    details.mkdir(parents=True, exist_ok=True)
    (details / "002345.md").mkdir()          # 名字对,是个目录——读取必炸

    doc = json.loads(write_decision(scan).read_text(encoding="utf-8"))
    unreadable = _by_code(doc)["002345"]["card_context"]

    assert unreadable["parse_status"] == "ERROR"
    assert unreadable["card_kind"] == "unknown"
    assert any("读取失败" in e for e in unreadable["parse_errors"])
    assert not any("正文为空或缺失" in e for e in unreadable["parse_errors"])
    assert unreadable["source"]["relative_path"] == "details/002345.md"
    assert unreadable["source"]["card_sha256"] is None

    missing = _by_code(doc)["000034"]["card_context"]          # 对照组:真的没有这张卡
    assert missing["source"]["relative_path"] is None
    assert missing["source"]["card_sha256"] is None
    assert any("正文为空或缺失" in e for e in missing["parse_errors"])

    assert doc["buys"][0]["code"] == "002345"      # 读取失败是 display_only,不影响选择


# ── finding 4:归档失败的原因不能被 `except Exception` 吞掉 ───────────────────────
def test_archive_failure_records_the_cause_and_leaves_selection_unchanged(tmp_path, capsys):
    """`capsule/blobs` 提前放一个**普通文件**(不是目录)——`trace.blobs._secure_directory`
    走到这一级会发现"该组件存在但不是目录",抛 `ValueError`,是"活跃 run 在场但归档真的
    失败"的确定性复现路径(不靠 chmod)。归档失败必须:①`snapshot_quality` 仍是
    `unarchived`(不阻断);②原因记进 `source.archive_error`(不是 `None`);③原因也打进
    stderr(与 `verify_decision` 既有的不一致留痕纪律一致);④决策投影分毫不受影响。
    """
    run_root = tmp_path / "run"
    scan = _build_scan(run_root / "staging", _RANK_CANDS)
    baseline = _projection(build_decision(scan))

    capsule = run_root / "capsule"
    (capsule / "identity").mkdir(parents=True)
    (capsule / "blobs").write_text("occupied by a plain file, not a directory",
                                   encoding="utf-8")
    _write_card(scan, "002345", _CARD_ALLOWED_A)

    doc = json.loads(write_decision(scan).read_text(encoding="utf-8"))

    assert _projection(doc) == baseline
    source = _by_code(doc)["002345"]["card_context"]["source"]
    assert source["snapshot_quality"] == "unarchived"
    assert source["blob_digest"] is None
    assert source["archive_error"]                       # 非空、非 None——原因被记录了
    assert "not a directory" in source["archive_error"]
    assert doc["buys"][0]["code"] == "002345"

    err = capsys.readouterr().err
    assert "卡快照归档失败" in err
    assert "not a directory" in err


# ── finding 5:两处真实 `.sort()` 与 `_selection_sort_spec` 的复述必须有硬 guard ──────
#: 四只票四面(target_align/recall_strength/evidence/risk_safety)与 composite_rank/
#: n_channels/best_channel_rank/intel/dossier/price_claim/gate_states/early_stop
#: 逐项相同(→ relative_decision_score 与 target_align 两级主键全部打平),只留
#: amount_yi/code 可分胜负——逼真实排序用上 tiebreak 的每一级。`seat=True` 是为了同一份
#: fixture 也能喂 composite 池(只靠 target_align 排,这里同样打平,一样落到 amount/code)。
_TIE_BASE = Cand(code="900001", name="并列基准", composite_rank=1, amount_yi=5.0,
                 n_channels=2, best_channel_rank=1, rating="Hold",
                 early_stop={"phase": "P3", "reason": "其他"}, intel="INTEL",
                 dossier=True, price_claim="CLEAN",
                 gate_states={"主力真在": "PASS"}, seat=True)


def test_finalists_and_composite_sort_key_specs_match_the_real_tiebreak_order(tmp_path):
    """`_selection_sort_spec` 是对 `build_decision` 里两处真实 `.sort()` key 元组的**手动
    复述**,没有结构性关联——本测试是唯一的防线:独立算出(不调用 `_selection_sort_spec`
    本身)"前面主键全部打平,只剩 amount desc / code asc 能分胜负"场景下的期望顺序,断言
    两个池的 `selection["codes"]` 都等于它。mutation probe(见 fix 报告)证实过它真的会
    因为某一侧 `.sort()` 的 tiebreak 顺序被改动而变红。
    """
    tied = [
        replace(_TIE_BASE, code="600300", amount_yi=9.0),   # amount 最高,应最先
        replace(_TIE_BASE, code="600200", amount_yi=7.0),
        replace(_TIE_BASE, code="600100", amount_yi=5.0),   # 与下面这只 amount 也打平
        replace(_TIE_BASE, code="600050", amount_yi=5.0),   # code 更小,该排在 600100 前面
    ]
    scan = _build_scan(tmp_path, tied)

    fin = build_decision(scan)
    scores = {row["code"]: row["relative_decision_score"] for row in fin["candidates"]}
    aligns = {row["code"]: row["faces"]["target_align"] for row in fin["candidates"]}
    assert len(set(scores.values())) == 1        # 前提自证:relative_decision_score 真打平
    assert len(set(aligns.values())) == 1         # 前提自证:target_align 真打平

    expected_order = ["600300", "600200", "600050", "600100"]   # amount desc,同额 code asc
    assert fin["selection"]["codes"] == expected_order

    comp = build_decision(scan, mode=MODE_ACTIVE, pool=POOL_COMPOSITE)
    assert comp["selection"]["codes"] == expected_order          # composite 只用 target_align,这里也打平


# ── finding 6 的回归覆盖已经是既有测试的一部分(`build_decision` 每次只跑一遍,重算
# 只在源码层面被移除,没有独立行为可测;上面 finding 1/2 的测试同时也在跑这条路径)。


# ── ALSO:contract=_LIVE_EXEC_LINE_CONTRACT 让 contract_match 在活体卡上真正可读 ──────
def _card_with_exec_lines(pct_chg, pos_in_range) -> str:
    return "\n".join([
        "# 决策卡 — 002345 示例票 @ 2026-08-06",
        "| 评级 | 现价 | 仓位 |", "|---|---|---|", "| Hold | 10 | 10% |",
        f"- [执行线] pct_chg <= {pct_chg} → 当日涨超放弃本次尾盘入场",
        f"- [执行线] pos_in_range < {pos_in_range} → 收在当日区间上沿放弃",
        "FINAL TRANSACTION PROPOSAL: **HOLD**",
    ])


def test_live_contract_reports_match_when_exec_lines_equal_the_in_force_thresholds(tmp_path):
    scan = _build_scan(tmp_path, _RANK_CANDS)
    _write_card(scan, "002345",
               _card_with_exec_lines(EXEC_LINE_MAX_PCT_1D, EXEC_LINE_MAX_POS_IN_RANGE))

    doc = json.loads(write_decision(scan).read_text(encoding="utf-8"))
    exec_lines = _by_code(doc)["002345"]["card_context"]["exec_lines"]

    assert exec_lines["pct_chg"]["contract_match"] == "MATCH"
    assert exec_lines["pos_in_range"]["contract_match"] == "MATCH"
    assert exec_lines["pct_chg"]["contract_version"] is None      # 不带 version(ALSO 条款)
    assert exec_lines["pos_in_range"]["contract_version"] is None


def test_live_contract_reports_drifted_when_exec_lines_differ_from_the_in_force_thresholds(
    tmp_path,
):
    drifted_pct, drifted_pos = 5.0, 0.4
    assert drifted_pct != EXEC_LINE_MAX_PCT_1D                    # 前提自证:真的不同
    assert drifted_pos != EXEC_LINE_MAX_POS_IN_RANGE
    scan = _build_scan(tmp_path, _RANK_CANDS)
    _write_card(scan, "002345", _card_with_exec_lines(drifted_pct, drifted_pos))

    doc = json.loads(write_decision(scan).read_text(encoding="utf-8"))
    exec_lines = _by_code(doc)["002345"]["card_context"]["exec_lines"]

    assert exec_lines["pct_chg"]["contract_match"] == "DRIFTED"
    assert exec_lines["pos_in_range"]["contract_match"] == "DRIFTED"


# ═══════════════════════ v4.0(2026-09-24 可买性对齐 §2.6,controller Ruling P21)══════
#
# `tiering` 开关:入场硬门(卡面 entry_stance=PROHIBITED → no_redflag 否决)+ A/R 分级
# (entry_stance=ALLOWED → A 级/card_backed,其余 eligible → R 级/relative_forced)。
# 关(默认)= v3.0 逐字,golden `test_tiering_off_is_v3_verbatim_even_with_prohibited_card`
# 钉死这条 parity。**A 级在全部 8 个真实历史扫描日恒为 0**(没有卡带过入场线)——golden
# 测试因此断言 R 级选出,不拿 A 级练手(任务书附注 P25:调参数去凑 A 级出现是在作弊)。


def _card(entry_line: str | None, rating: str = "Hold") -> str:
    lines = [f"# 决策卡 — 000000 x @ {DATE}", "| 评级 | 现价 | EV目标 | R:R | 仓位 | 触发位 |",
             "|---|---|---|---|---|---|", f"| {rating} | 10 | +0.5% | 1.2 | 5% | 涨>3% 放弃 |"]
    if entry_line:
        lines.append(entry_line)
    lines.append("FINAL TRANSACTION PROPOSAL: **HOLD**")
    return "\n".join(lines)


def _snapshot(**cards: str) -> dict:
    return {code: {"text": text} for code, text in cards.items()}


def test_tiering_off_is_v3_verbatim_even_with_prohibited_card(tmp_path):
    scan = _build_scan(tmp_path, _RANK_CANDS)
    snap = _snapshot(**{"002345": _card("**入场**: 禁止")})
    doc = build_decision(scan, card_snapshot=snap)                      # tiering 默认 False
    assert doc["buys"][0]["code"] == "002345" and "tier" not in doc["buys"][0]
    assert doc["tiering"] is False and doc["tier_counts"] is None
    assert any(c["type"] == "card_says_prohibited" for c in doc["conflicts"])


def test_tiering_prohibited_card_is_hard_gated_out(tmp_path):
    scan = _build_scan(tmp_path, _RANK_CANDS)
    snap = _snapshot(**{"002345": _card("**入场**: 禁止")})
    doc = build_decision(scan, card_snapshot=snap, tiering=True)
    by = _by_code(doc)
    assert by["002345"]["hard_gate"]["no_redflag"] is False
    assert doc["buys"][0]["code"] == "000034" and doc["buys"][0]["tier"] == "R"
    assert doc["buys"][0]["basis"] == "relative_forced"
    # task-19-brief 原文在这里断言 `doc["conflicts"] == []`——技术性不成立,与本任务无关:
    # `_snapshot` 这个夹具只给 002345 一张卡,000034/600188/601699 压根没有卡。002345 被
    # 否决后新晋 R 级冠军 000034 自己就是"卡缺失",`_selection_conflicts`(既有 schema 2
    # 机制,本任务未改一字)对"冠军卡缺失"本就会记一条 card_missing_or_unparseable——
    # 这和 PROHIBITED 否决是两件独立的事(参见
    # test_missing_card_is_absent_source_and_unknown_card_context_without_blocking_buy
    # 同一机制)。已在 task-19-report.md 里点名此处与 brief 字面值的分歧,留给用户裁定。
    new_winner_card = by["000034"]["card_context"]
    assert new_winner_card["parse_status"] == "ERROR"
    assert doc["conflicts"] == [{
        "code": "000034", "type": "card_missing_or_unparseable",
        "detail": f"card_context.parse_errors={new_winner_card['parse_errors']}",
    }]


def test_tiering_allowed_card_wins_as_A_tier_even_if_lower_scored(tmp_path):
    scan = _build_scan(tmp_path, _RANK_CANDS)
    snap = _snapshot(**{"601699": _card("**入场**: 允许"), "002345": _card("**入场**: 条件(收复均线)")})
    doc = build_decision(scan, card_snapshot=snap, tiering=True)
    assert doc["buys"][0] == {"code": "601699", "basis": "card_backed", "rank": 1, "tier": "A"}
    assert doc["tier_counts"] == {"A": 1, "R": 3}
    assert "A 级 1 只" in doc["why"]


def test_tiering_all_prohibited_is_blocked_not_forced(tmp_path):
    scan = _build_scan(tmp_path, _RANK_CANDS)
    snap = _snapshot(**{c: _card("**入场**: 禁止") for c in _RANK_ORDER})
    doc = build_decision(scan, card_snapshot=snap, tiering=True)
    assert doc["blocked"] is True and doc["buys"] == []
    assert {r["reason"] for r in doc["blocked_reasons"]} == {"hard_gate.no_redflag"}


def test_rule_version_is_v4():
    from autoresearch.scan.relative_buy import RULE_VERSION
    assert RULE_VERSION == "e6.v4.1"


def test_tiering_conditional_card_is_not_vetoed_and_lands_in_r_tier(tmp_path):
    """CONDITIONAL 是第三个真值,不是 ALLOWED/PROHIBITED 两值的化简(addendum P21 附注
    3:生产 Codex 引擎 4 次命中里 2 笔 BUY 就压在它上面)。它不触发 no_redflag 否决
    (veto 只认 PROHIBITED),也进不了 A 级(A 级只认 ALLOWED),必须落在 R 级。
    """
    scan = _build_scan(tmp_path, _RANK_CANDS)
    snap = _snapshot(**{"002345": _card("**入场**: 条件(待突破)")})
    doc = build_decision(scan, card_snapshot=snap, tiering=True)
    by = _by_code(doc)
    assert by["002345"]["card_context"]["entry_stance"] == "CONDITIONAL"
    assert by["002345"]["hard_gate"]["no_redflag"] is True     # CONDITIONAL 不是 PROHIBITED,不否决
    assert doc["buys"][0]["code"] == "002345" and doc["buys"][0]["tier"] == "R"
    assert doc["tier_counts"] == {"A": 0, "R": 4}


# ───────────────────────── v4.1:第五门 rebalance_close(2026-09-25 指数调样事件 §2.4) ─────────────────────────
from autoresearch.scan.relative_buy import REBALANCE_GATE, _hard_gates  # noqa: E402


def _events(code: str, phase: str, *, index_name: str = "中证500", side: str = "add",
            eff: str = "20261211") -> dict:
    return {code: [{"code": code, "index_code": "000905", "index_name": index_name, "side": side,
                    "ann_date": "20261127", "eff_close_date": eff, "phase": phase,
                    "source": "csindex", "flow_adv_days": None}]}


def test_hard_gates_tuple_grows_only_when_the_knob_is_on():
    assert _hard_gates(False) == ("tradable", "data_a", "contract", "no_redflag")
    assert _hard_gates(True) == ("tradable", "data_a", "contract", "no_redflag", REBALANCE_GATE)
    assert RULE_VERSION == "e6.v4.1"


def test_rebalance_gate_off_is_v40_verbatim_even_with_passive_close_eve_row(tmp_path):
    scan = _build_scan(tmp_path, _RANK_CANDS)
    base = build_decision(scan)
    # fix round 1:`index_events_error=True` 同 `index_events` 一样,门关时必须被忽略——旋钮才是
    # 唯一开关,不是"给了任何一个 index_events* 形参就多少生效一点"。
    doc = build_decision(scan, index_events=_events("002345", "passive_close_eve"),
                         index_events_error=True)                                    # rebalance_gate 默认 False
    assert doc == base
    assert "index_events" not in doc
    assert "rebalance_close" not in doc["candidates"][0]["hard_gate"]
    assert doc["field_usage"]["hard_gate"]["fields"] == ["tradable", "data_a", "contract", "no_redflag"]
    # fix round 1(item 2,coordinator review):`doc == base` 与既有 `test_field_usage_derives_
    # from_hard_gates_and_faces_without_inventing_a_new_gate` 都拿两侧共享 rebalance_gate=False 的
    # 值互相比较——`_field_usage` 里的 `if rebalance_gate:` 守卫被删掉也会两侧同时漂移、什么都不动。
    # 建议的 `set(doc["field_usage"]) == set(FIELD_USAGE)` 我先按变异测试验证过一遍,再落这里:
    # `FIELD_USAGE = _field_usage(tiering=False)` 的 `rebalance_gate` 同样吃的是形参默认值——对着
    # 它比,和对着另一次 `build_decision()` 调用比是**同一个盲点**,守卫被删时两侧一起多出
    # `"index_events"` 键、比较仍然相等、断言仍然绿(实测确认,不是猜的)。真正独立于本函数的
    # 参照只能是硬编码的字面量键集——`_field_usage` 关双开关时的完整契约就是这五个,一个不多。
    assert set(doc["field_usage"]) == {
        "hard_gate", "ranking", "display_only", "research_rating", "l4_proposal"}


def test_rebalance_gate_vetoes_the_passive_close_eve_row_and_says_where(tmp_path):
    scan = _build_scan(tmp_path, _RANK_CANDS)
    doc = build_decision(scan, index_events=_events("002345", "passive_close_eve"), rebalance_gate=True)
    by = _by_code(doc)
    assert by["002345"]["hard_gate"][REBALANCE_GATE] is False and by["002345"]["eligible"] is False
    assert by["000034"]["hard_gate"][REBALANCE_GATE] is True
    assert doc["buys"][0]["code"] == "000034"                                # 原冠军被门否决,亚军上
    assert doc["index_events"] == {"source": "ok", "gate_evaluated": True, "n_rows": 1,
                                   "n_candidates_in_events": 1, "hits": ["002345"]}
    veto = [r for r in doc["excluded"] if r["reason"] == f"hard_gate.{REBALANCE_GATE}"]
    assert len(veto) == 1 and "中证500 add E=20261211" in veto[0]["detail"]
    assert doc["field_usage"]["hard_gate"]["fields"][-1] == REBALANCE_GATE
    assert "index_events" in doc["field_usage"]
    assert doc["veto_accounting"]["by_gate"][REBALANCE_GATE] == 1
    assert by["002345"]["hard_gate"]["no_redflag"] is True                  # 独立计数:不并入 no_redflag


def test_rebalance_gate_ignores_other_phases_and_also_vetoes_drops(tmp_path):
    scan = _build_scan(tmp_path, _RANK_CANDS)
    ev = {**_events("002345", "announced_runup"), **_events("000034", "passive_close_eve", side="drop")}
    doc = build_decision(scan, index_events=ev, rebalance_gate=True)
    by = _by_code(doc)
    assert by["002345"]["hard_gate"][REBALANCE_GATE] is True                # 跑道段:事实,不否决
    assert by["000034"]["hard_gate"][REBALANCE_GATE] is False               # E2 裁定:调出票同样一刀
    assert doc["index_events"]["hits"] == ["000034"] and doc["index_events"]["n_candidates_in_events"] == 2


def test_rebalance_gate_source_absent_passes_everyone_and_says_so(tmp_path):
    scan = _build_scan(tmp_path, _RANK_CANDS)
    doc = build_decision(scan, index_events=None, rebalance_gate=True)
    assert all(row["hard_gate"][REBALANCE_GATE] is True for row in doc["candidates"])
    assert doc["index_events"] == {"source": "absent", "gate_evaluated": False, "n_rows": 0,
                                   "n_candidates_in_events": 0, "hits": []}


def test_rebalance_gate_source_error_passes_everyone_and_says_so(tmp_path):
    """fix round 1(item 3):第三个 source 值——`index_events_error=True` 时门后果与 `absent`
    完全相同(全员放行),但产物必须记 `source="error"` 不是 `"absent"`(「读不出来」与「压根
    没有」是两个不同的因,即使门后果相同;consumer 端 `brief._rebalance_line` 已在 `f302cc8`
    读这第三个值)。"""
    scan = _build_scan(tmp_path, _RANK_CANDS)
    doc = build_decision(scan, index_events_error=True, rebalance_gate=True)
    assert all(row["hard_gate"][REBALANCE_GATE] is True for row in doc["candidates"])
    assert doc["index_events"] == {"source": "error", "gate_evaluated": False, "n_rows": 0,
                                   "n_candidates_in_events": 0, "hits": []}


def test_rebalance_gate_source_ok_but_empty_is_evaluated_not_absent(tmp_path):
    scan = _build_scan(tmp_path, _RANK_CANDS)
    doc = build_decision(scan, index_events={}, rebalance_gate=True)
    assert doc["index_events"]["source"] == "ok" and doc["index_events"]["gate_evaluated"] is True
    assert doc["buys"][0]["code"] == "002345"                                # 无事件 → 与门关同一冠军


def test_rebalance_gate_can_block_the_whole_day_honestly(tmp_path):
    scan = _build_scan(tmp_path, _RANK_CANDS)
    ev = {}
    for c in _RANK_ORDER:
        ev.update(_events(c, "passive_close_eve"))
    doc = build_decision(scan, index_events=ev, rebalance_gate=True)
    assert doc["blocked"] is True and doc["buys"] == []
    assert {r["reason"] for r in doc["blocked_reasons"]} == {f"hard_gate.{REBALANCE_GATE}"}
    assert doc["index_events"]["hits"] == sorted(_RANK_ORDER)


def test_rebalance_gate_and_tiering_compose(tmp_path):
    scan = _build_scan(tmp_path, _RANK_CANDS)
    snap = _snapshot(**{"601699": _card("**入场**: 允许")})
    doc = build_decision(scan, card_snapshot=snap, tiering=True,
                         index_events=_events("601699", "passive_close_eve"), rebalance_gate=True)
    assert _by_code(doc)["601699"]["eligible"] is False                     # A 级卡也挡:门在分级之前
    assert doc["buys"][0]["tier"] == "R"


def _events_csv(scan: Path, code: str, phase: str) -> None:
    import pandas as pd

    from autoresearch.scan import index_events as ie
    ie.write_index_events(scan, pd.DataFrame([{
        "code": code, "index_code": "000905", "index_name": "中证500", "side": "add", "ann_date": "20261127",
        "eff_close_date": "20261211", "phase": phase, "source": "csindex", "flow_adv_days": None,
    }], columns=ie.EVENT_COLS))


def test_write_decision_reads_index_events_from_disk_when_gate_on(tmp_path):
    scan = _build_scan(tmp_path, _RANK_CANDS)
    _events_csv(scan, "002345", "passive_close_eve")
    doc = json.loads(write_decision(scan, rebalance_gate=True).read_text(encoding="utf-8"))
    assert doc["index_events"]["hits"] == ["002345"] and doc["buys"][0]["code"] == "000034"


def test_write_decision_ignores_index_events_file_when_gate_off(tmp_path):
    scan = _build_scan(tmp_path, _RANK_CANDS)
    _events_csv(scan, "002345", "passive_close_eve")
    doc = json.loads(write_decision(scan).read_text(encoding="utf-8"))
    assert "index_events" not in doc and doc["buys"][0]["code"] == "002345"


def test_write_decision_gate_on_without_file_records_absent(tmp_path):
    scan = _build_scan(tmp_path, _RANK_CANDS)
    doc = json.loads(write_decision(scan, rebalance_gate=True).read_text(encoding="utf-8"))
    assert doc["index_events"] == {"source": "absent", "gate_evaluated": False, "n_rows": 0,
                                   "n_candidates_in_events": 0, "hits": []}


def test_write_decision_gate_on_with_unreadable_file_records_error_and_still_writes(tmp_path, capsys):
    """fix round 1(item 3,coordinator review):`index_events.csv` 的写者是非原子写,中断的一次
    会留下一个读不出来的半成品(此处用零字节文件模拟)。旧代码此处会让 `pd.read_csv` 的异常穿透
    `_index_events_input` → `write_decision` → 调用方(`safe_write_decision` 的兜底 catch 才接得住,
    但那意味着当天**完全不产出**决策文件,比任何一道门单独否决都坏)。修复后 `write_decision`
    本身(不是 `safe_write_decision`)必须稳态返回一份 source="error" 的决策文件——门在读失败时
    与 source 缺席同一后果(全员放行),但产物必须留痕这是「读不出来」不是「压根没有」。
    """
    from autoresearch.scan.index_events import INDEX_EVENTS_FILENAME

    scan = _build_scan(tmp_path, _RANK_CANDS)
    (scan / INDEX_EVENTS_FILENAME).write_bytes(b"")           # 零字节:中断写留下的半成品
    doc = json.loads(write_decision(scan, rebalance_gate=True).read_text(encoding="utf-8"))
    assert doc["index_events"] == {"source": "error", "gate_evaluated": False, "n_rows": 0,
                                   "n_candidates_in_events": 0, "hits": []}
    assert doc["buys"][0]["code"] == "002345"                 # 当天照样产出决策文件,冠军照选
    assert "index_events" in capsys.readouterr().err          # 降级必须可见(同 configured_rebalance_gate 姿势)


def test_verify_decision_must_use_the_same_gate_switch_as_the_writer(tmp_path):
    from autoresearch.scan.relative_buy import verify_decision
    scan = _build_scan(tmp_path, _RANK_CANDS)
    _events_csv(scan, "002345", "passive_close_eve")
    write_decision(scan, rebalance_gate=True)
    verify_decision(scan, rebalance_gate=True)
    assert not (scan / "_relative_buy_decision.mismatch.json").exists()      # 同开关 → 安静
    verify_decision(scan, rebalance_gate=False)
    assert (scan / "_relative_buy_decision.mismatch.json").exists()          # 不同开关 → 留证据、不覆盖
    on_disk = json.loads((scan / "_relative_buy_decision.json").read_text(encoding="utf-8"))
    assert on_disk["index_events"]["hits"] == ["002345"]                     # 盘上那份没被改写
