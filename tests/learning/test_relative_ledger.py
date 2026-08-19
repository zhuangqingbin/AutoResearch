"""relative BUY 影子账本(Wave12 T24 / E6-2)。

本文件锁五件事,顺序即重要性:

1. **每个决策日恰 1 只影子 BUY 或 BLOCKED** —— v1 finalizer 的对外契约(第 2 只恒不出),
   账本这一侧必须把它当契约校验,而不是照单全收(`test_two_buys_is_a_contract_error`
   是这条的变异探针)。
2. **三尺同屏**:绝对 `gap_c1_o2` + `rel_gap_market` + `rel_gap_sector` 成熟后回填,
   报表分别写分子/分母/as-of/ruler。
3. **绝对 gap 为负的行必须渲染「弱市相对最优」** —— 相对 BUY 不承诺绝对上涨,报表
   一旦把负 gap 写成绝对看涨就是撒谎(`test_negative_gap_renders_weak_market_note`
   是这条的变异探针)。
4. **不与旧账混算**:旧 OW 基率与新 relative 账分列并置、显式标定义断层。
5. **会变的量**:跑一天 → 观测计数 +1。这条腿死了也像活着的唯一解药是断言它会变
   (本仓库权重自动腿曾连续 4 次 NO-OP 空转两周无人察觉)。

测试产物一律落 `tmp_path`;`tests/learning/conftest.py` 的 autouse 护栏会把任何写向真实
`reports/` / `context/scan/` 的调用直接拦成 `PermissionError`(2026-08-08 覆写已发布报告
的事故),所以每个用例都显式传 `ledger_path=` / `report_path=` / `scan_root=`。
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from autoresearch.common import workspace as ws
from autoresearch.common.ruler import REL_GAP_RULER, REL_MARKET, REL_SECTOR
from autoresearch.learning import relative_ledger as rl

DATE_A = "2026-08-04"
DATE_B = "2026-08-05"


# ── fixture 语言:一份决策文档 = finalizer 那天的产物 ────────────────────────
def _doc(date: str, *, buy: str | None = "688766", candidates: int = 3,
         mode: str = "shadow", extra_buys: list[str] | None = None,
         name: str = "普冉股份", sector: str = "半导体", pinned: bool = False,
         rating: str = "Hold", score: float = 0.79,
         task_book: str = "PRESENT", rule_version: str = "e6.v1") -> dict:
    """真 `_relative_buy_decision.json` 的最小同构件(字段名照抄 T23 产物,不发明)。"""
    codes = [buy] if buy else []
    codes += list(extra_buys or [])
    rows = []
    for index in range(max(candidates, len(codes))):
        code = codes[index] if index < len(codes) else f"00000{index}".zfill(6)
        rows.append({
            "code": code, "name": name if index == 0 else f"候选{index}",
            "sector": sector, "pinned": pinned if index == 0 else False,
            "eligible": True, "hard_gate": {},
            "faces": {"target_align": 0.8, "recall_strength": 0.7,
                      "evidence": 0.75, "risk_safety": 0.9},
            "faces_missing": [], "relative_decision_score": score - index * 0.1,
            "rank": index + 1, "research_rating": rating if index == 0 else "Hold",
            "amount_pctl": 0.9, "price_claim": "UNMEASURED",
            "expected_abs_gap": {"value": None, "status": "UNMEASURED", "n": 0},
        })
    return {
        "schema_version": 1, "rule_version": rule_version, "mode": mode, "date": date,
        "ruler": REL_GAP_RULER,
        "benchmark": {"ruler": REL_GAP_RULER, "entry_flag": "buyable_c1",
                      "entry_flag_present": False,
                      "market": {"definition": "L0 可交易全集等权 gap_c1_o2",
                                 "column": REL_MARKET, "n": 4193,
                                 "members_sha256": "0" * 64},
                      "sector": {"definition": "申万一级可交易等权", "column": REL_SECTOR,
                                 "source_column": "industry", "n_sectors": 129},
                      "excluded_from_benchmark": {}},
        # 真 2026-08-07 产物:当日没跑 scan → 五个 inputs 全 ABSENT(不是只缺 task_book)
        "inputs": {"l1_scored_full": task_book, "task_book": task_book,
                   "price_claim_status": "ABSENT", "dossier_present": task_book,
                   "run_health": task_book},
        "counts": {"candidates": len(rows), "eligible": len(rows),
                   "excluded_rows": 0, "buys": len(codes),
                   "with_missing_face": 0},
        "candidates": rows,
        "buys": [{"code": code, "basis": "relative", "rank": index + 1}
                 for index, code in enumerate(codes)],
        "second_buy": {"fired": False, "reason": "v1 影子期无已验证阈值",
                       "threshold": None},
        "blocked": not codes, "blocked_reasons": [] if codes else
        [{"reason": "hard_gate.no_redflag", "n": 3}],
        "excluded": [],
    }


def _put_decision(scan_root: Path, doc: dict) -> Path:
    day = scan_root / doc["date"]
    day.mkdir(parents=True, exist_ok=True)
    target = day / "_relative_buy_decision.json"
    target.write_text(json.dumps(doc, ensure_ascii=False, sort_keys=True), encoding="utf-8")
    return target


def _put_attribution(scan_root: Path, date: str, rows: list[dict],
                     *, with_rel: bool = False) -> Path:
    """最小 attribution.csv:算 rel 两列所需的原料 = code/industry/gap_c1_o2/buyable_c1。"""
    import pandas as pd

    frame = pd.DataFrame(rows)
    if with_rel:
        from autoresearch.learning.retro import _rel_gap_cols
        frame[REL_MARKET], frame[REL_SECTOR] = _rel_gap_cols(frame)
    target = scan_root / date / "retro" / "attribution.csv"
    target.parent.mkdir(parents=True, exist_ok=True)
    frame.to_csv(target, index=False)
    return target


def _market(gap: float = 0.01) -> list[dict]:
    """一个可交易的市场底噪(20 只),让分母不是 1 只票自己。"""
    return [{"code": f"3000{index:02d}", "name": f"底噪{index}", "industry": "半导体",
             REL_GAP_RULER: gap, "buyable_c1": True} for index in range(20)]


@pytest.fixture
def paths(tmp_path: Path) -> dict:
    return {"scan_root": tmp_path / "scan",
            "ledger_path": tmp_path / "learning" / "relative_buy.jsonl",
            "report_path": tmp_path / "learning" / "relative_buy.md"}


# ── 1. 恰 1 只 BUY 或 BLOCKED ───────────────────────────────────────────────
def test_buy_day_yields_exactly_one_row_with_one_buy(paths):
    _put_decision(paths["scan_root"], _doc(DATE_A))
    rows = rl.roll(scan_root=paths["scan_root"], ledger_path=paths["ledger_path"])
    assert len(rows) == 1
    row = rows[0]
    assert row["status"] == "BUY"
    assert row["code"] == "688766"
    assert row["n_buys"] == 1
    assert row["contract_errors"] == []


def test_blocked_day_is_recorded_with_reasons(paths):
    _put_decision(paths["scan_root"], _doc(DATE_A, buy=None))
    rows = rl.roll(scan_root=paths["scan_root"], ledger_path=paths["ledger_path"])
    assert rows[0]["status"] == "BLOCKED"
    assert rows[0]["code"] is None
    assert rows[0]["blocked_reasons"][0]["reason"] == "hard_gate.no_redflag"
    assert rows[0]["outcome"]["status"] == "NA"


def test_day_without_any_scan_artifact_is_no_run_not_blocked(paths):
    """08-07 实况:目录在、但没跑过 scan。把它记成 BLOCKED 会污染 action coverage 分母。"""
    _put_decision(paths["scan_root"],
                  _doc(DATE_A, buy=None, candidates=0, task_book="ABSENT"))
    rows = rl.roll(scan_root=paths["scan_root"], ledger_path=paths["ledger_path"])
    assert rows[0]["status"] == "NO_RUN"
    summary = rl.summarize(rows)
    assert summary["n_decision_days"] == 0
    assert summary["action_coverage"] is None


def test_two_buys_is_a_contract_error(paths):
    """v1 第 2 只恒不出。账本照单全收 = 契约校验形同虚设(变异探针①)。"""
    _put_decision(paths["scan_root"], _doc(DATE_A, extra_buys=["000776"]))
    rows = rl.roll(scan_root=paths["scan_root"], ledger_path=paths["ledger_path"])
    assert rows[0]["n_buys"] == 2
    assert any("恰 1 只" in err for err in rows[0]["contract_errors"])
    assert rl.summarize(rows)["n_contract_errors"] == 1


def test_non_shadow_mode_is_a_contract_error(paths):
    _put_decision(paths["scan_root"], _doc(DATE_A, mode="live"))
    rows = rl.roll(scan_root=paths["scan_root"], ledger_path=paths["ledger_path"])
    assert any("shadow" in err for err in rows[0]["contract_errors"])


# ── 2. basis / 幂等整替 ─────────────────────────────────────────────────────
def test_every_row_carries_basis_relative_and_the_ruler(paths):
    _put_decision(paths["scan_root"], _doc(DATE_A))
    _put_decision(paths["scan_root"], _doc(DATE_B, buy=None))
    rows = rl.roll(scan_root=paths["scan_root"], ledger_path=paths["ledger_path"])
    assert [row["basis"] for row in rows] == ["relative", "relative"]
    assert {row["ruler"] for row in rows} == {REL_GAP_RULER}


def test_roll_is_idempotent_byte_stable_replace_not_append(paths):
    _put_decision(paths["scan_root"], _doc(DATE_A))
    rl.roll(scan_root=paths["scan_root"], ledger_path=paths["ledger_path"])
    first = paths["ledger_path"].read_bytes()
    rl.roll(scan_root=paths["scan_root"], ledger_path=paths["ledger_path"])
    assert paths["ledger_path"].read_bytes() == first
    assert len(rl.load_ledger(paths["ledger_path"])) == 1


# ── I-6:已登记的观测不可被重算静默改写 ────────────────────────────────────
def test_rule_version_change_does_not_rewrite_a_recorded_observation(paths):
    """规则一升版,每晚 `roll()` 的回放会把预注册期观测整体替换 —— 已实测发生过一次
    (底片产出于 `e6.v1`,真账本 8 行事后全变成 `e6.v1.1`)。

    这本账**唯一**的存在理由是给 `exp_relative_buy_owner` 攒可信观测;「已汇集的观测
    不可被悄悄改写」是 registry 那条「definition hash 变了就得换实验 id」的同一条铁律。
    结果一样时没人发现,结果不一样时已经晚了 —— 所以拒绝覆写,不看新旧值是否相等。
    """
    _put_decision(paths["scan_root"], _doc(DATE_A, rule_version="e6.v1"))
    first = rl.roll(scan_root=paths["scan_root"], ledger_path=paths["ledger_path"])
    assert first[0]["rule_version"] == "e6.v1"
    assert "superseded" not in first[0]

    # 规则升版 + 换了个人:重算结果与已登记的那条不是同一个决策
    _put_decision(paths["scan_root"],
                  _doc(DATE_A, rule_version="e6.v2", buy="000776", name="广发证券"))
    rows = rl.roll(scan_root=paths["scan_root"], ledger_path=paths["ledger_path"])

    assert rows[0]["rule_version"] == "e6.v1", "已登记观测被重算结果覆写了"
    assert rows[0]["code"] == "688766"
    assert rows[0]["superseded"]["recomputed_rule_version"] == "e6.v2"
    assert rows[0]["superseded"]["recomputed_code"] == "000776"
    assert any(err.startswith(rl.FROZEN_ERROR_PREFIX)
               for err in rows[0]["contract_errors"])
    summary = rl.summarize(rows)
    assert summary["n_frozen_observations"] == 1
    assert summary["n_contract_errors"] == 1   # 契约错 → registry 守卫立刻不可晋升


def test_rule_version_only_drift_is_still_frozen(paths):
    """**真实事故的形状**:v1 → v1.1 语义等价、选的还是同一只票,只有版本串变了。

    正因为"结果一样"才最危险 —— 没有任何人会发现预注册期观测被换了版本标签,而
    registry 的样本外承诺是按 `rule_version` 分代的。所以判据里必须含 `rule_version`,
    不能只比选了谁。
    """
    _put_decision(paths["scan_root"], _doc(DATE_A, rule_version="e6.v1"))
    rl.roll(scan_root=paths["scan_root"], ledger_path=paths["ledger_path"])
    _put_decision(paths["scan_root"], _doc(DATE_A, rule_version="e6.v1.1"))
    row = rl.roll(scan_root=paths["scan_root"], ledger_path=paths["ledger_path"])[0]
    assert row["rule_version"] == "e6.v1"
    assert row["code"] == "688766"                      # 票没变
    assert row["superseded"]["recomputed_rule_version"] == "e6.v1.1"


def test_frozen_row_still_backfills_its_own_outcome(paths):
    """冻结的是**决策**(选了谁、什么规则),不是**前向读数**。

    outcome 从 PENDING 变 MATURE 是这条观测在按预期成熟,不是改写;把它一起冻住会让
    冻结那天之后的所有 gap 永远读不到,等于把观测腿掐死。
    """
    _put_decision(paths["scan_root"], _doc(DATE_A, rule_version="e6.v1"))
    rl.roll(scan_root=paths["scan_root"], ledger_path=paths["ledger_path"])
    _put_decision(paths["scan_root"], _doc(DATE_A, rule_version="e6.v2", buy="000776"))
    _put_attribution(paths["scan_root"], DATE_A, [
        {"code": "688766", "name": "普冉股份", "industry": "半导体",
         REL_GAP_RULER: -0.0638, "buyable_c1": True},
        *_market(gap=0.01),
    ])
    row = rl.roll(scan_root=paths["scan_root"], ledger_path=paths["ledger_path"])[0]
    assert row["code"] == "688766"                       # 决策冻住
    assert row["outcome"]["status"] == "MATURE"          # 读数继续回填
    assert row["outcome"]["gap_c1_o2"] == pytest.approx(-0.0638)


def test_freeze_is_idempotent_and_does_not_pile_up_contract_errors(paths):
    _put_decision(paths["scan_root"], _doc(DATE_A, rule_version="e6.v1"))
    rl.roll(scan_root=paths["scan_root"], ledger_path=paths["ledger_path"])
    _put_decision(paths["scan_root"], _doc(DATE_A, rule_version="e6.v2", buy="000776"))
    rl.roll(scan_root=paths["scan_root"], ledger_path=paths["ledger_path"])
    once = paths["ledger_path"].read_bytes()
    rl.roll(scan_root=paths["scan_root"], ledger_path=paths["ledger_path"])
    rows = rl.roll(scan_root=paths["scan_root"], ledger_path=paths["ledger_path"])
    assert paths["ledger_path"].read_bytes() == once
    assert len([e for e in rows[0]["contract_errors"]
                if e.startswith(rl.FROZEN_ERROR_PREFIX)]) == 1


def test_identical_recompute_is_not_flagged_as_a_rewrite(paths):
    """反向锁:同规则同结果重跑不许报冻结(否则守卫天天红 = 没人再看它)。"""
    _put_decision(paths["scan_root"], _doc(DATE_A))
    rl.roll(scan_root=paths["scan_root"], ledger_path=paths["ledger_path"])
    rows = rl.roll(scan_root=paths["scan_root"], ledger_path=paths["ledger_path"])
    assert "superseded" not in rows[0]
    assert rows[0]["contract_errors"] == []
    assert rl.summarize(rows)["n_frozen_observations"] == 0


def test_frozen_rows_are_named_in_the_report(paths):
    _put_decision(paths["scan_root"], _doc(DATE_A, rule_version="e6.v1"))
    rl.roll(scan_root=paths["scan_root"], ledger_path=paths["ledger_path"])
    _put_decision(paths["scan_root"], _doc(DATE_A, rule_version="e6.v2", buy="000776"))
    text = rl.render(rl.roll(scan_root=paths["scan_root"],
                             ledger_path=paths["ledger_path"]))
    assert rl.FROZEN_ERROR_PREFIX in text
    assert "e6.v2" in text


def test_roll_keeps_history_whose_decision_file_disappeared(paths):
    """账本追加列不清零 —— 决策文件被清掉不等于那天没发生过。"""
    _put_decision(paths["scan_root"], _doc(DATE_A))
    rl.roll(scan_root=paths["scan_root"], ledger_path=paths["ledger_path"])
    (paths["scan_root"] / DATE_A / "_relative_buy_decision.json").unlink()
    _put_decision(paths["scan_root"], _doc(DATE_B))
    rows = rl.roll(scan_root=paths["scan_root"], ledger_path=paths["ledger_path"])
    assert [row["date"] for row in rows] == [DATE_A, DATE_B]


# ── 3. 成熟回填(三尺)────────────────────────────────────────────────────
def test_pending_until_attribution_exists(paths):
    _put_decision(paths["scan_root"], _doc(DATE_A))
    rows = rl.roll(scan_root=paths["scan_root"], ledger_path=paths["ledger_path"])
    outcome = rows[0]["outcome"]
    assert outcome["status"] == "PENDING"
    assert outcome["gap_c1_o2"] is None
    assert outcome["rel_gap_market"] is None
    assert outcome["rel_source"] == "ABSENT"


def test_maturity_backfills_three_rulers_computing_rel_when_absent(paths):
    """rel 两列不在盘上时现算(复用 `retro._rel_gap_cols`,不另写一份算法)。"""
    _put_decision(paths["scan_root"], _doc(DATE_A))
    _put_attribution(paths["scan_root"], DATE_A, [
        {"code": "688766", "name": "普冉股份", "industry": "半导体",
         REL_GAP_RULER: -0.0638, "buyable_c1": True},
        *_market(gap=0.01),
    ])
    rows = rl.roll(scan_root=paths["scan_root"], ledger_path=paths["ledger_path"])
    outcome = rows[0]["outcome"]
    assert outcome["status"] == "MATURE"
    assert outcome["rel_source"] == "COMPUTED"
    assert outcome["gap_c1_o2"] == pytest.approx(-0.0638)
    # 21 只:1 只 −6.38% + 20 只 +1% → 均值 = 0.1362/21 = +0.648571%;超额 = −7.02857%
    assert outcome["market_mean_gap"] == pytest.approx(0.006486, abs=1e-6)
    assert outcome["benchmark_n"] == 21
    assert outcome["rel_gap_market"] == pytest.approx(-0.070286, abs=1e-6)
    # 同为「半导体」→ 行业均值 = 市场均值(本 fixture 里全体同业)
    assert outcome["rel_gap_sector"] == pytest.approx(-0.070286, abs=1e-6)
    assert outcome["as_of"]["ruler"] == REL_GAP_RULER
    assert outcome["as_of"]["window"] == "open[D+2]/close[D+1]-1"


def test_rel_columns_on_disk_are_read_not_recomputed(paths):
    _put_decision(paths["scan_root"], _doc(DATE_A))
    _put_attribution(paths["scan_root"], DATE_A, [
        {"code": "688766", "name": "普冉股份", "industry": "半导体",
         REL_GAP_RULER: 0.02, "buyable_c1": True},
        *_market(gap=0.01),
    ], with_rel=True)
    rows = rl.roll(scan_root=paths["scan_root"], ledger_path=paths["ledger_path"])
    assert rows[0]["outcome"]["rel_source"] == "ON_DISK"
    assert rows[0]["outcome"]["rel_gap_market"] == pytest.approx(0.009524, abs=1e-5)


def test_gap_nan_stays_pending_even_though_attribution_exists(paths):
    _put_decision(paths["scan_root"], _doc(DATE_A))
    _put_attribution(paths["scan_root"], DATE_A, [
        {"code": "688766", "name": "普冉股份", "industry": "半导体",
         REL_GAP_RULER: None, "buyable_c1": True},
        *_market(gap=0.01),
    ])
    assert rl.roll(scan_root=paths["scan_root"],
                   ledger_path=paths["ledger_path"])[0]["outcome"]["status"] == "PENDING"


def test_missing_industry_column_keeps_the_absolute_gap(paths):
    """M-13:缺 `industry` 只让**相对两列**算不出来,绝对主尺照样成熟。

    修复前整行返回 PENDING —— 已经在盘上的 `gap_c1_o2` 被一起丢掉,外观与「还没到
    T+2」逐字节相同。把「行业列缺失」伪装成「数据未成熟」正是本仓库最忌讳的名实不符
    (读者会以为再等一天就有,其实永远不会有)。
    """
    _put_decision(paths["scan_root"], _doc(DATE_A))
    _put_attribution(paths["scan_root"], DATE_A, [
        {"code": "688766", "name": "普冉股份", REL_GAP_RULER: -0.0638,
         "buyable_c1": True},
        *[{k: v for k, v in row.items() if k != "industry"} for row in _market()],
    ])
    outcome = rl.roll(scan_root=paths["scan_root"],
                      ledger_path=paths["ledger_path"])[0]["outcome"]
    assert outcome["status"] == "MATURE"
    assert outcome["gap_c1_o2"] == pytest.approx(-0.0638)
    assert outcome["rel_source"] == "NO_INDUSTRY_COLUMN"   # 说清为什么没有,不装 PENDING
    assert outcome["rel_gap_market"] is None
    assert outcome["rel_gap_sector"] is None
    assert outcome["benchmark_n"] == 21                    # 分母仍数得出来


def test_leading_zero_code_survives_the_attribution_join(paths):
    """`002345` 在 pandas 里会被读成 `2345`;join 处丢前导零 = 静默 PENDING。"""
    _put_decision(paths["scan_root"], _doc(DATE_A, buy="002345", name="潮宏基"))
    _put_attribution(paths["scan_root"], DATE_A, [
        {"code": "002345", "name": "潮宏基", "industry": "饰品",
         REL_GAP_RULER: 0.03, "buyable_c1": True},
        *_market(gap=0.01),
    ])
    row = rl.roll(scan_root=paths["scan_root"], ledger_path=paths["ledger_path"])[0]
    assert row["code"] == "002345"
    assert row["outcome"]["status"] == "MATURE"
    assert row["outcome"]["gap_c1_o2"] == pytest.approx(0.03)


# ── 4. 报表:弱市文案 / 分子分母 as-of / 旧账分列 ───────────────────────────
def _mature_rows(paths, gap: float) -> list[dict]:
    _put_decision(paths["scan_root"], _doc(DATE_A))
    _put_attribution(paths["scan_root"], DATE_A, [
        {"code": "688766", "name": "普冉股份", "industry": "半导体",
         REL_GAP_RULER: gap, "buyable_c1": True},
        *_market(gap=0.0),
    ])
    return rl.roll(scan_root=paths["scan_root"], ledger_path=paths["ledger_path"])


def test_semantic_constants_are_pinned_literals():
    """措辞常量本身钉死字面量(I-1 修复,2026-08-09 复核)。

    原先 `test_negative_gap_renders_weak_market_note` 写的是
    `assert rl.WEAK_MARKET_NOTE in text` —— **引常量的断言会跟着常量一起漂**:把
    `WEAK_MARKET_NOTE` 改成「短期波动」,27 例全绿。而模块第 66 行自称「改这句必须有
    测试跟着变红」,那个承诺当时是假的。这是本仓库「`node --check` 型永不变红的绿灯」
    家族(2026-07-24 变异测试家训),同波 T25 的 `WEAK_MARKET_PHRASE` 已按同款修法处理。

    「绝对 gap 为负时必须写『弱市相对最优』」是用户 2026-08-08 裁定的语义纪律
    (相对 BUY 不承诺绝对上涨),不是文风偏好 —— 所以钉的是**字面量**。
    """
    assert rl.WEAK_MARKET_NOTE == "弱市相对最优(绝对 gap 为负;相对 BUY 从不承诺绝对收益为正)"
    assert rl.RULER_WINDOW == "open[D+2]/close[D+1]-1"
    assert rl.MATURE_MIN_OBSERVATIONS == 20
    assert rl.FROZEN_ERROR_PREFIX == "已登记观测被重算结果改写(已拒绝)"
    assert rl.BASIS == "relative"


def test_negative_gap_renders_weak_market_note(paths):
    """绝对 gap 为负 = 「弱市相对最优」,不得被改写成绝对看涨(变异探针②)。

    断言**字面量**不引 `rl.WEAK_MARKET_NOTE`(I-1):引常量 = 改常量时断言跟着漂。
    """
    text = rl.render(_mature_rows(paths, -0.0638))
    assert "弱市相对最优" in text
    assert "绝对 gap 为负" in text
    for forbidden in ("绝对看涨", "预计上涨", "看多", "必涨", "短期波动"):
        assert forbidden not in text


def test_positive_gap_does_not_claim_weak_market(paths):
    text = rl.render(_mature_rows(paths, 0.0212))
    assert "弱市相对最优" not in text
    for forbidden in ("绝对看涨", "预计上涨", "看多", "必涨"):
        assert forbidden not in text


def test_report_always_states_no_absolute_promise(paths):
    """这一句是**无条件**渲染的(不随 gap 正负变),所以单独立一条 —— 把它混在
    上面那条负 gap 用例里会伪装成"负 gap 才有的行为",是零鉴别力的假断言(I-1 同族)。
    """
    for gap in (-0.0638, 0.0212):
        assert "不承诺绝对上涨" in rl.render(_mature_rows(paths, gap))


def test_report_states_numerator_denominator_asof_and_ruler(paths):
    text = rl.render(_mature_rows(paths, -0.0638))
    for token in ("分子", "分母", "as-of", REL_GAP_RULER, REL_MARKET, REL_SECTOR,
                  "open[D+2]/close[D+1]-1",
                  # 两个「基准 n」是两个人口(L0 全集 vs 全市场可交易),不可互换引用
                  "不可互换"):
        assert token in text


def test_legacy_ow_is_listed_separately_with_the_definition_break(paths):
    text = rl.render(_mature_rows(paths, -0.0638),
                     legacy_ow={"rating": "Overweight", "n": 9, "n_realized": 3,
                                "win2": 0.0, "mean2": -0.007})
    assert "定义断层" in text
    assert "不连成一条趋势线" in text
    assert "n=9" in text
    # 旧账不得进新账的统计:成熟观测只有本文件写进去的那 1 条
    assert "成熟观测 n=1" in text


def test_immature_is_stated_and_not_extrapolated(paths):
    text = rl.render(_mature_rows(paths, -0.0638))
    assert "IMMATURE" in text
    # 不能只断言 "20" —— 日期里就有 "20",那是一条永远为真的空断言
    assert f"< {rl.MATURE_MIN_OBSERVATIONS} 个决策日" in text
    assert "自**首条观测**起算,不是从预注册日起算" in text
    assert "不外推" in text


def test_action_coverage_counts_blocked_in_the_denominator(paths):
    _put_decision(paths["scan_root"], _doc(DATE_A))
    _put_decision(paths["scan_root"], _doc(DATE_B, buy=None))
    summary = rl.summarize(rl.roll(scan_root=paths["scan_root"],
                                   ledger_path=paths["ledger_path"]))
    assert summary["n_buy_days"] == 1
    assert summary["n_blocked_days"] == 1
    assert summary["n_decision_days"] == 2
    assert summary["action_coverage"] == pytest.approx(0.5)


# ── 5. 会变的量:跑一天 → 观测 +1 ──────────────────────────────────────────
def test_one_more_day_increments_observation_count_by_exactly_one(paths):
    _put_decision(paths["scan_root"], _doc(DATE_A))
    before = rl.summarize(rl.roll(scan_root=paths["scan_root"],
                                  ledger_path=paths["ledger_path"]))
    _put_decision(paths["scan_root"], _doc(DATE_B, buy="000776", name="广发证券"))
    after = rl.summarize(rl.roll(scan_root=paths["scan_root"],
                                 ledger_path=paths["ledger_path"]))
    assert after["n_observations"] - before["n_observations"] == 1
    assert after["n_decision_days"] - before["n_decision_days"] == 1
    assert len(rl.load_ledger(paths["ledger_path"])) == 2


# ── 6. 回放只读 / 写盘接线 ─────────────────────────────────────────────────
def test_replay_never_writes_a_decision_file(paths):
    """历史回放是只读的:它不得在生产 scan 目录里留下 `_relative_buy_decision.json`。"""
    day = paths["scan_root"] / DATE_B
    day.mkdir(parents=True)
    rows = rl.replay(scan_root=paths["scan_root"], days=[DATE_B])
    assert len(rows) == 1
    assert rows[0]["status"] == "NO_RUN"
    assert not (day / "_relative_buy_decision.json").exists()


def test_roll_replays_days_that_never_got_a_decision_file(paths):
    """前向接线只覆盖"以后跑的 run";历史五日的决策文件从来没写过 —— 不回放的话账本
    从第一天起就是空的(EXP-1/EXP-2 预注册后 observations 空转 6 天,同族)。"""
    (paths["scan_root"] / DATE_A).mkdir(parents=True)
    (paths["scan_root"] / DATE_A / "_l4_tasks.json").write_text(
        json.dumps({"tasks": {}}), encoding="utf-8")
    rows = rl.roll(scan_root=paths["scan_root"], ledger_path=paths["ledger_path"])
    assert [row["date"] for row in rows] == [DATE_A]
    assert not (paths["scan_root"] / DATE_A / "_relative_buy_decision.json").exists()


def test_write_report_and_ledger_go_where_told(paths):
    _put_decision(paths["scan_root"], _doc(DATE_A))
    rows = rl.roll(scan_root=paths["scan_root"], ledger_path=paths["ledger_path"])
    target = rl.write_report(rows, path=paths["report_path"])
    assert target == paths["report_path"]
    assert paths["report_path"].read_text(encoding="utf-8").startswith("# 相对 BUY 影子账本")
    assert paths["ledger_path"].exists()


def test_main_is_zero_arg_callable_for_the_nightly_registry(paths, monkeypatch):
    """`nightly_close._ledgers` / `post_run._module_main` 都以 `module.main()` 零参调用。"""
    monkeypatch.setattr(rl, "LEDGER_PATH", paths["ledger_path"])
    monkeypatch.setattr(rl, "REPORT_PATH", paths["report_path"])
    monkeypatch.setattr(rl, "SCAN_ROOT", paths["scan_root"])
    _put_decision(paths["scan_root"], _doc(DATE_A))
    assert rl.main() == 0
    assert paths["report_path"].exists()


def test_post_run_observe_writes_the_decision_file(tmp_path):
    """前向接线:`post_run observe` 是 STAGES 步骤 5 的最后一条命令 —— 每次成功 scan 都
    在那一刻产护照 + 决策文件(T19 Step3 同点)。没有这一挂点,本账本永远只有回放的历史。
    """
    from autoresearch.scan.post_run import publish_run_observation
    from autoresearch.scan.relative_buy import DECISION_FILENAME

    scan = tmp_path / "scan" / DATE_A
    scan.mkdir(parents=True)
    publish_run_observation(scan, real_scan=False)
    assert (scan / DECISION_FILENAME).exists()
    doc = json.loads((scan / DECISION_FILENAME).read_text(encoding="utf-8"))
    assert doc["mode"] == "shadow"           # 影子:不写 buy ledger / 不碰 publisher


def test_default_paths_are_the_shadow_pair():
    """默认落点写死成契约:影子账本 + 影子报表,**不是**生产 buy ledger。"""
    assert ws.learning_root() / "relative_buy.jsonl" == rl.LEDGER_PATH
    assert ws.reports_root() / "learning" / "relative_buy.md" == rl.REPORT_PATH
    assert ws.scan_root() == rl.SCAN_ROOT


def test_nightly_names_table_contains_relative_ledger():
    """动态调用面:`_ledgers` 以字符串拼名 import —— 名字掉了不会有任何静态报错。"""
    import inspect

    from autoresearch.learning import nightly_close

    source = inspect.getsource(nightly_close._ledgers if hasattr(nightly_close, "_ledgers")
                               else nightly_close.run)
    assert '"relative_ledger"' in source
    assert source.index('"relative_ledger"') < source.index('"evidence_manifest"')
