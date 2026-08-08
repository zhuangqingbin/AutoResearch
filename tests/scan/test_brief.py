"""确定性 brief.md 生成器回归(Wave12 T25 / 批C C1)。

契约五条(任务书 Step 1):
  ① 同输入重复生成 hash 一致(零时间戳/零随机/无序遍历)
  ② 字节 ≤ `brief.MAX_BYTES`(3,000)
  ③ 相对 BUY 行禁词(不得出现「预计上涨/看涨」);绝对 gap 为负必含「弱市相对最优」
  ④ 每个渲染出的数字都能在**白名单输入文件**里找到 —— 生成器附 `sources` 边表,
     逐行 `file ∈ INPUT_WHITELIST` ∧ `text` 真在 brief 正文里(供 T27 lint 逐项对账)
  ⑤ 影子期 BUY 区**双行**:旧生产结论 + 影子 relative 行,且影子行带「非正式」标

零网络、零 LLM;所有产物写 tmp_path(2026-08-08 覆写真实报告事故的家训)。
"""
from __future__ import annotations

import csv
import hashlib
import json
from pathlib import Path

import pytest

from autoresearch.scan import brief
from autoresearch.scan.decision_record import DecisionRecord, write_decision_records

_DATE = "2026-08-06"
_RUN = "20260806_2308"


def _decision(*, mode="shadow", blocked=False, abs_gap=None, buy_code="600018") -> dict:
    """`_relative_buy_decision.json` 的最小同构件(字段名照抄 T23 产物,不发明)。"""
    gap = {"value": None, "status": "UNMEASURED", "n": 0} if abs_gap is None else abs_gap
    cands = [
        {"code": "600018", "name": "上港集团", "sector": "航运港口", "pinned": False,
         "eligible": True, "rank": 1, "relative_decision_score": 0.795,
         "faces": {"target_align": 0.77, "recall_strength": 0.77,
                   "evidence": 0.77, "risk_safety": 0.86},
         "faces_missing": [], "research_rating": "Hold",
         "hard_gate": {"tradable": True, "data_a": True, "contract": True, "no_redflag": True},
         "expected_abs_gap": gap},
        {"code": "600285", "name": "羚锐制药", "sector": "中药Ⅱ", "pinned": False,
         "eligible": True, "rank": 2, "relative_decision_score": 0.773,
         "faces": {"target_align": 0.7, "recall_strength": 0.7,
                   "evidence": 0.7, "risk_safety": 0.8},
         "faces_missing": [], "research_rating": "Hold",
         "hard_gate": {"tradable": True, "data_a": True, "contract": True, "no_redflag": True},
         "expected_abs_gap": {"value": None, "status": "UNMEASURED", "n": 0}},
    ]
    buys = [] if blocked else [{"code": buy_code, "basis": "relative", "rank": 1}]
    return {
        "schema_version": 1, "rule_version": "e6.v1", "mode": mode, "date": _DATE,
        "ruler": "gap_c1_o2",
        "benchmark": {
            "ruler": "gap_c1_o2", "entry_flag": "buyable_c1", "entry_flag_present": False,
            "market": {"definition": "决策层分位/流动性门的分母 = 当日 L0 可交易全集等权",
                       "column": "rel_gap_market", "n": 4237, "members_sha256": "deadbeef"},
            "sector": {"definition": "申万一级可交易等权", "column": "rel_gap_sector",
                       "source_column": "industry", "n_sectors": 129},
        },
        "counts": {"candidates": 11, "eligible": 2, "excluded_rows": 1, "buys": len(buys)},
        "candidates": cands,
        "buys": buys,
        "second_buy": {"fired": False, "reason": "v1 影子期无已验证阈值", "threshold": None},
        "blocked": blocked,
        "blocked_reasons": ([{"reason": "hard_gate.no_redflag", "n": 11}] if blocked else []),
        "excluded": [{"code": "603127", "reason": "hard_gate.no_redflag",
                      "detail": "research_rating=Sell"}],
    }


def _scan_dir(root: Path, *, with_decision=True, decision=None) -> Path:
    scan = root / "context" / "scan" / _DATE
    (scan / "details").mkdir(parents=True)
    (scan / "meta.json").write_text(json.dumps({
        "analysis_date": _DATE, "universe_raw": 5496, "universe": 4237, "after_gate_a": 4237,
        "recall_n": 1000, "l2_n": 203, "l2_engine": "stratified(sn_composite)",
        "regime": "range", "source": "tushare"}, ensure_ascii=False), encoding="utf-8")
    rows = [
        # code,   name,     sector,   lane
        ("600018", "上港集团", "航运港口", ""),
        ("600285", "羚锐制药", "中药Ⅱ", ""),
        ("603127", "昭衍新药", "医疗服务", ""),
        ("601688", "华泰证券", "证券Ⅱ", "pinned"),
        ("300750", "宁德时代", "电池", "pinned"),
        ("300857", "协创数据", "消费电子", "pinned"),
    ]
    with (scan / "finalists.csv").open("w", encoding="utf-8", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=["ticker", "code", "name", "sector", "lane",
                                           "conviction", "thesis", "risk", "catalyst"])
        w.writeheader()
        for code, name, sector, lane in rows:
            w.writerow({"ticker": code, "code": code, "name": name, "sector": sector,
                        "lane": lane, "conviction": 60, "thesis": "t", "risk": "r",
                        "catalyst": "c"})
    ratings = {"600018": "Hold", "600285": "Hold", "603127": "Underweight",
               "601688": "Hold", "300750": "Hold", "300857": "Underweight"}
    (scan / "_final_ratings.json").write_text(json.dumps(ratings, ensure_ascii=False),
                                              encoding="utf-8")
    # 决策书走**生产写手**造(hash 自洽)——手搭 records_hash 会让
    # `health.final_ratings` 抛 "records_hash mismatch",进而静默掐掉 buy_ledger 那条腿。
    write_decision_records(scan, [
        DecisionRecord.build(
            analysis_date=_DATE, contract_hash=None, code=c, source_rating=r,
            rubric_rating=r, gate_states={"主力真在": "FAIL", "业绩真兑现": "PASS",
                                          "估值不透支": "PASS"},
            early_stop=None, ensemble_ratings=[], final_rating=r,
            proposal={"Hold": "HOLD", "Underweight": "SELL", "Sell": "SELL"}[r],
            reason="synthetic", evidence_refs=[f"finalists.csv#{c}"],
            first_rejection_stage=None)
        for c, r in sorted(ratings.items())])
    (scan / "run_mode.json").write_text(json.dumps({
        "schema_version": 1, "mode": "FORCED_FULL", "sentinel_reason": None,
        "pinned_codes": ["300750", "300857", "601688"]}, ensure_ascii=False), encoding="utf-8")
    (scan / "run_health.json").write_text(json.dumps({
        "date": _DATE,
        "counts": {"l1_full": 4237, "recall": 1000, "l2": 203, "finalists": 6,
                   "cards": 6, "buys": 0},
        "churn": {"prev_date": "2026-08-05", "n_prev": 10, "n_today": 6,
                  "n_repeat": 4, "repeat_rate": 0.667},
        "degraded_fields": ["hk_hold"], "missing": ["verify.csv"]},
        ensure_ascii=False), encoding="utf-8")
    with (scan / "gate_fires.csv").open("w", encoding="utf-8", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=["date", "code", "check", "severity", "detail"])
        w.writeheader()
        w.writerow({"date": _DATE, "code": "", "check": "空泛话术", "severity": "warn",
                    "detail": "x"})
        w.writerow({"date": _DATE, "code": "600018", "check": "覆盖率不足",
                    "severity": "fail", "detail": "y"})
    if with_decision:
        (scan / brief.DECISION_FILENAME).write_text(
            json.dumps(decision or _decision(), ensure_ascii=False, indent=2, sort_keys=True),
            encoding="utf-8")
    return scan


@pytest.fixture
def scan(tmp_path):
    return _scan_dir(tmp_path)


# ───────────────────────────── ① 确定性 ─────────────────────────────

def test_same_input_same_bytes(scan):
    a = brief.build(scan, run_folder=_RUN)
    b = brief.build(scan, run_folder=_RUN)
    assert a["markdown"] == b["markdown"]
    assert hashlib.sha256(a["markdown"].encode("utf-8")).hexdigest() == \
        hashlib.sha256(b["markdown"].encode("utf-8")).hexdigest()
    assert a["sources"] == b["sources"]


def test_no_timestamp_or_random_leak(scan):
    md = brief.build(scan, run_folder=_RUN)["markdown"]
    # 生成时刻不得渗进产物(只允许数据日与 run 目录名两个「时间」)
    import re
    stamps = set(re.findall(r"\d{4}-\d{2}-\d{2}", md))
    assert stamps <= {_DATE, "2026-08-05"}, f"意外时间戳:{stamps}"
    assert "T" not in md.split("\n")[0].replace("速读", "")   # 无 ISO 时刻头


# ───────────────────────────── ② 字节预算 ─────────────────────────────

def test_byte_budget(scan):
    out = brief.build(scan, run_folder=_RUN)
    assert out["n_bytes"] == len(out["markdown"].encode("utf-8"))
    assert out["n_bytes"] <= brief.MAX_BYTES, \
        f"brief {out['n_bytes']}B > 硬预算 {brief.MAX_BYTES}B"


def _flood_pinned(scan: Path, n: int) -> None:
    """把 ④ 节灌到会撑破预算的规模(④ 是唯一随票数线性增长的块)。"""
    fp = scan / "finalists.csv"
    rows = list(csv.DictReader(fp.open(encoding="utf-8")))
    extra = [dict(rows[-1], code=f"{300000 + i:06d}", name=f"测试保送持仓{i:03d}",
                  lane="pinned") for i in range(n)]
    with fp.open("w", encoding="utf-8", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows + extra)
    ratings = json.loads((scan / "_final_ratings.json").read_text(encoding="utf-8"))
    ratings.update({r["code"]: "Sell" for r in extra})
    (scan / "_final_ratings.json").write_text(json.dumps(ratings, ensure_ascii=False),
                                              encoding="utf-8")


def test_byte_budget_holds_when_raw_render_would_overflow(tmp_path):
    """**有鉴别力的预算断言**:先证明未裁剪的原始渲染确实 >3,000B(否则这条测试是恒绿的
    假灯),再断言 `build()` 出来的成品 ≤3,000B 且把裁剪痕迹显式写出来。"""
    scan = _scan_dir(tmp_path)
    _flood_pinned(scan, 80)
    facts = brief.collect_facts(scan, run_folder=_RUN)
    raw_lines, _ = brief._sections(facts, pinned_cap=99, delta_cap=6)
    raw = len(("\n".join(raw_lines) + "\n").encode("utf-8"))
    assert raw > brief.MAX_BYTES, f"探针失效:未裁剪只有 {raw}B,压不到预算线"
    out = brief.build(scan, run_folder=_RUN)
    assert out["n_bytes"] <= brief.MAX_BYTES, f"多持仓下溢出:{out['n_bytes']}B"
    assert "…(+" in out["markdown"], "裁剪必须留痕(否则读者以为持仓就这几只)"


def test_trimmed_sources_stay_consistent_with_body(tmp_path):
    """裁剪后 `sources` 必须同步瘦身 —— 边表里留着一条正文已经没有的 text = 假对账。"""
    scan = _scan_dir(tmp_path)
    _flood_pinned(scan, 80)
    out = brief.build(scan, run_folder=_RUN)
    for row in out["sources"]:
        assert row["text"] in out["markdown"], f"裁剪后 sources 残留 {row['field']}"


# ───────────────────────────── ③ 语义纪律 ─────────────────────────────

def test_relative_buy_has_no_bullish_promise(scan):
    md = brief.build(scan, run_folder=_RUN)["markdown"]
    for banned in ("预计上涨", "预计绝对上涨", "预期上涨", "看涨", "必涨", "稳赚"):
        assert banned not in md, f"相对 BUY 区出现禁词「{banned}」"
    assert "不承诺绝对" in md, "相对 BUY 必须自带「不承诺绝对收益」限定"


def test_semantic_constants_are_pinned_literals():
    """措辞常量本身钉死字面量 —— 否则下面几条断言会跟着常量一起漂,变成永不变红的绿灯
    (2026-07-24 变异测试家训:`node --check` 型零鉴别力)。"""
    assert brief.WEAK_MARKET_PHRASE == "弱市相对最优"
    assert "预计上涨" in brief.BANNED_RELATIVE_PHRASES
    assert "看涨" in brief.BANNED_RELATIVE_PHRASES
    assert brief.MAX_BYTES == 3000


def test_negative_abs_gap_forces_weak_market_phrase(tmp_path):
    dec = _decision(abs_gap={"value": -0.0233, "status": "MEASURED", "n": 24})
    scan = _scan_dir(tmp_path, decision=dec)
    md = brief.build(scan, run_folder=_RUN)["markdown"]
    assert "弱市相对最优" in md              # 字面量,不引常量(防同漂)
    for banned in ("预计上涨", "看涨", "必涨"):
        assert banned not in md


def test_positive_abs_gap_does_not_claim_upside(tmp_path):
    dec = _decision(abs_gap={"value": 0.0142, "status": "MEASURED", "n": 24})
    scan = _scan_dir(tmp_path, decision=dec)
    md = brief.build(scan, run_folder=_RUN)["markdown"]
    assert "弱市相对最优" not in md
    for banned in ("预计上涨", "看涨", "必涨"):
        assert banned not in md


# ───────────────────────────── ④ sources 边表 ─────────────────────────────

def test_sources_cover_every_number_and_stay_in_whitelist(scan):
    out = brief.build(scan, run_folder=_RUN)
    md, sources = out["markdown"], out["sources"]
    assert sources, "sources 边表不得为空"
    for row in sources:
        assert set(row) >= {"field", "value", "file", "locator", "text"}
        assert row["file"] in brief.INPUT_WHITELIST, \
            f"{row['field']} 取自白名单外的 {row['file']}"
        assert row["text"] in md, f"sources 行 {row['field']} 的 text 不在 brief 正文里"


def test_sources_expose_key_decision_fields(scan):
    fields = {r["field"] for r in brief.build(scan, run_folder=_RUN)["sources"]}
    for must in ("funnel.universe", "buys.production_n", "relative.code",
                 "relative.rank", "ow_base.n", "ow_base.win2"):
        assert must in fields, f"sources 缺关键字段 {must}"


def test_no_details_or_trace_in_whitelist():
    """禁读 details 全文与 trace 大文件(任务书 Interfaces 硬约束)。"""
    for name in brief.INPUT_WHITELIST:
        assert not name.startswith("details/")
        assert not name.startswith("trace/")
        assert "attribution.csv" not in name or name.startswith("retro/")


# ───────────────────────────── ⑤ 影子期双行 ─────────────────────────────

def test_shadow_mode_renders_two_lines_with_informal_mark(scan):
    md = brief.build(scan, run_folder=_RUN)["markdown"]
    prod = [ln for ln in md.splitlines() if "生产 BUY" in ln]
    shadow = [ln for ln in md.splitlines() if "影子 relative BUY" in ln]
    assert len(prod) == 1, "旧生产结论行必须常驻(影子期也要)"
    assert len(shadow) == 1, "影子 relative BUY 行必须常驻"
    assert "非正式" in shadow[0], "影子行必须显式标注非正式"
    assert "不执行" in shadow[0]
    assert "0 只" in prod[0]


def test_zero_buy_day_explains_why_with_split_buckets(scan):
    """0 买日必须自带「为什么」——早停分桶与 OW 三门柱**分列**(CP7 播报纪律同源:
    早停卡按定义不写三门段,混算 = 复辟旧不实判词「无一过 ≥OW 三门」)。"""
    md = brief.build(scan, run_folder=_RUN)["markdown"]
    line = next(ln for ln in md.splitlines() if "为什么没买" in ln)
    assert "主力真在 6" in line, "OW 三门失守计数必须来自 decision_records.gate_states"
    assert "两类不混算" in line


def test_has_buy_day_drops_why_line(tmp_path):
    scan = _scan_dir(tmp_path)
    ratings = json.loads((scan / "_final_ratings.json").read_text(encoding="utf-8"))
    ratings["600018"] = "Overweight"
    (scan / "_final_ratings.json").write_text(json.dumps(ratings, ensure_ascii=False),
                                              encoding="utf-8")
    md = brief.build(scan, run_folder=_RUN)["markdown"]
    assert "为什么没买" not in md
    assert "生产 BUY 1 只" in md


def test_ow_base_rate_is_split_account_not_trend(scan):
    md = brief.build(scan, run_folder=_RUN)["markdown"]
    line = next(ln for ln in md.splitlines() if "旧 OW 基率" in ln)
    assert "定义断层" in line and "不连线" in line
    # 样本数随 buy_ledger 走,不写死:合成 scan_root 无买单 → n=0
    assert "9 笔" not in line


def test_ow_base_rate_reads_buy_ledger(scan, monkeypatch):
    monkeypatch.setattr(brief, "_ow_base_rate", lambda _root: {
        "n": 9, "n_realized": 3, "win2": 0.0, "mean2": -0.007})
    line = next(ln for ln in brief.build(scan, run_folder=_RUN)["markdown"].splitlines()
                if "旧 OW 基率" in ln)
    assert "9 笔" in line and "0%" in line


def test_blocked_run_is_not_rendered_as_success(tmp_path):
    scan = _scan_dir(tmp_path, decision=_decision(blocked=True))
    md = brief.build(scan, run_folder=_RUN)["markdown"]
    shadow = next(ln for ln in md.splitlines() if "影子 relative BUY" in ln)
    assert "BLOCKED" in shadow
    assert "hard_gate.no_redflag" in shadow


def test_missing_decision_file_is_explicit(tmp_path):
    scan = _scan_dir(tmp_path, with_decision=False)
    md = brief.build(scan, run_folder=_RUN)["markdown"]
    shadow = next(ln for ln in md.splitlines() if "影子 relative BUY" in ln)
    assert "未生成" in shadow, "决策文件缺席必须显式说,不得静默省行"


# ───────────────────────────── 七节骨架 + 落盘 ─────────────────────────────

def test_seven_sections_present(scan):
    md = brief.build(scan, run_folder=_RUN)["markdown"]
    for mark in ("① 市场", "② 漏斗", "③ 结论", "④ 持仓", "⑤ 风险哨",
                 "⑥ 昨日 delta", "⑦ 欠账"):
        assert mark in md, f"brief 缺第 {mark} 节"


def test_write_lands_brief_and_sources(scan, tmp_path):
    out_dir = tmp_path / "reports" / "scan" / _RUN
    path = brief.write(scan, out_dir, run_folder=_RUN)
    assert path == out_dir / brief.BRIEF_FILENAME
    assert path.read_bytes() == brief.build(scan, run_folder=_RUN)["markdown"].encode("utf-8")
    sources = json.loads((scan / brief.SOURCES_FILENAME).read_text(encoding="utf-8"))
    assert sources["schema_version"] == brief.SCHEMA_VERSION
    assert sources["n_bytes"] <= brief.MAX_BYTES
    assert sources["rows"] == brief.build(scan, run_folder=_RUN)["sources"]


def test_write_is_idempotent(scan, tmp_path):
    out_dir = tmp_path / "reports" / "scan" / _RUN
    first = brief.write(scan, out_dir, run_folder=_RUN).read_bytes()
    second = brief.write(scan, out_dir, run_folder=_RUN).read_bytes()
    assert first == second


def test_funnel_line_matches_meta(scan):
    md = brief.build(scan, run_folder=_RUN)["markdown"]
    line = next(ln for ln in md.splitlines() if "② 漏斗" in ln)
    for n in ("5496", "4237", "1000", "203", "6"):
        assert n in line


def test_publisher_lands_brief_next_to_summary(tmp_path, monkeypatch):
    """接线真身断言(FN-1 家训:生产者没接线 = 死码)。走 `assemble.run` 全链,
    brief.md 必须与 summary.md 同目录落盘,且 ≤ 预算。"""
    from autoresearch.scan import assemble
    scan = _scan_dir(tmp_path)
    out_root = tmp_path / "reports" / "scan"
    summary = assemble.run(_DATE, scan_dir=scan, out_root=out_root,
                           hhmm="2308", run_date="2026-08-06")
    brief_path = summary.parent / brief.BRIEF_FILENAME
    assert brief_path.exists(), "publisher 未落 brief.md(接线断了)"
    assert len(brief_path.read_bytes()) <= brief.MAX_BYTES
    assert "③ 结论" in brief_path.read_text(encoding="utf-8")
    assert (scan / brief.SOURCES_FILENAME).exists(), "sources 边表未落 staging"


def test_pinned_section_lists_every_pinned_holding(scan):
    md = brief.build(scan, run_folder=_RUN)["markdown"]
    line = next(ln for ln in md.splitlines() if "④ 持仓" in ln)
    for name in ("华泰证券", "宁德时代", "协创数据"):
        assert name in line
    assert "上港集团" not in line, "非保送票不进持仓动作区"
