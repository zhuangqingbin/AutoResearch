"""确定性 brief.md 生成器回归(Wave12 T25 / 批C C1)。

契约五条(任务书 Step 1):
  ① 同输入重复生成 hash 一致(零时间戳/零随机/无序遍历)
  ② 字节 ≤ `brief.MAX_BYTES`(3,000)
  ③ 相对 BUY 行禁词(不得出现「预计上涨/看涨」);账本 BUY 实测均值为负必含「弱市相对最优」
  ④ 每个渲染出的数字都能在**白名单输入文件**里找到 —— 生成器附 `sources` 边表,
     逐行 `file ∈ INPUT_WHITELIST` ∧ `text` 真在 brief 正文里(供 T27 lint 逐项对账)
  ⑤ 影子期 BUY 区**双行**:旧生产结论 + 影子 relative 行,且影子行带「非正式」标

零网络、零 LLM;所有产物写 tmp_path(2026-08-08 覆写真实报告事故的家训)。
"""
from __future__ import annotations

import csv
import hashlib
import json
import shutil
from pathlib import Path

import pytest

from autoresearch.scan import brief
from autoresearch.scan.decision_record import DecisionRecord, write_decision_records

_DATE = "2026-08-06"
_RUN = "20260806_2308"


def _decision(*, mode="shadow", blocked=False, abs_gap=None, buy_code="600018",
              index_events=None) -> dict:
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
    doc = {
        "schema_version": 1, "rule_version": "e6.v1", "mode": mode, "date": _DATE,
        "ruler": "gap_c1_o2",
        "benchmark": {
            "ruler": "gap_c1_o2", "entry_flag": "buyable_c1", "entry_flag_present": False,
            "market": {"definition": "决策层分位/流动性门的分母 = 当日 L0 可交易全集等权",
                       "column": "rel_gap_market", "n": 4237, "members_sha256": "deadbeef",
                       "eval_population": ("全市场可交易(entry_tradable,含漏在 L0 的票)"
                                           "—— 与本块 n 不同,评分时由 relative_ledger 另算")},
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
    if index_events is not None:
        doc["index_events"] = index_events
    return doc


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
    # 真实 run 必有(策略师 Stage 0 写);brief ① 的定调句读它 —— 白名单 ⊆ 不变量靠它落地
    (scan / "market_view.md").write_text(
        "# 市场研判 — 2026-08-06\n\n1. **一句话定调**:**range 区间市 · 哑铃分化**"
        "——涨停 101 家。\n", encoding="utf-8")
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


@pytest.fixture(autouse=True)
def _isolate_ledger_root(monkeypatch):
    """Task 4:`brief.collect_facts` 无条件调用 `_e6_realized_stats()` → `outcome.load_ledger(None)`
    → `ws.reports_root()/scan/_ledger/recommendations.csv`。那份账本是真实项目产物(gitignored、
    随每场发布扫描增长,本机实测 300KB+),不隔离的话本文件里没有显式 monkeypatch 的用例会读到
    开发机当天的真实战绩,`test_publisher_artifact_map.py` 的 SHA 字节钉会跟着真账本内容漂移。

    **只在本模块 autouse**(不放 `conftest.py`):`ws.reports_root()` 是被 `tests/scan/` 里其他
    文件当**可组合相对路径**用的基础设施(如 `test_retention.py` 的 `tmp_path / ws.reports_root()
    / ...`),那里改成不存在的绝对路径会让 `tmp_path` 组合直接逃逸到真文件系统根——已经踩过一次
    (`FileNotFoundError: /nonexistent/...`),教训是隔离必须按「谁真的读账本」精确圈定,不能图省事
    挂到共享 conftest 上。要测账本读数的用例自行在测试体内 monkeypatch `ws.reports_root`
    (测试体内的 setattr 后执行,优先生效,`test_whitelist_has_no_dead_entry` 等用例已经这样做)。
    """
    from autoresearch.common import workspace as ws
    monkeypatch.setattr(ws, "reports_root", lambda: Path("/nonexistent/tests-no-real-ledger"))


# ─────────── Task 3:跨 run 昨日 delta 的夹具助手(_prev_published 专用) ───────────

def _publish_prev_run(reports_dir: Path, *, date: str, run_name: str, ratings: dict) -> Path:
    """搭一场**已发布**的上一场 run —— `outcome.published_runs` 的最小骨架:
    `manifest.json`(`analysis_date` 定位)+ `trace/staging/` 下的终评级与 finalist 名单
    (`_prev_published` 的两个读点)。"""
    run = reports_dir / "scan" / run_name
    staging = run / "trace" / "staging"
    staging.mkdir(parents=True)
    (run / "manifest.json").write_text(json.dumps({"analysis_date": date}), encoding="utf-8")
    (staging / "_final_ratings.json").write_text(json.dumps(ratings, ensure_ascii=False),
                                                 encoding="utf-8")
    with (staging / "finalists.csv").open("w", encoding="utf-8", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=["code", "name"])
        w.writeheader()
        for code in ratings:
            w.writerow({"code": code, "name": ""})
    return run


def _strip_churn(scan: Path) -> None:
    """拆掉 `_scan_dir` 硬编的 `run_health.churn` —— 留着它老分支先接管,新分支
    (`_prev_published`)永远摸不到(2026-09-24 controller 校正②)。"""
    path = scan / "run_health.json"
    health = json.loads(path.read_text(encoding="utf-8"))
    health.pop("churn", None)
    path.write_text(json.dumps(health, ensure_ascii=False), encoding="utf-8")


def _live_copy(scan: Path, tmp_path: Path) -> Path:
    """把 `scan` 挪进「活体扫描」路径形状(`scan_runs/.../staging/<date>`)—— `_is_live_scan`
    只认这个形状,原地 `context/scan/<date>` 摸不到新分支(2026-09-24 controller 校正⑤)。"""
    live = tmp_path / "context" / "scan_runs" / "r1" / "staging" / scan.name
    shutil.copytree(scan, live)
    return live


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


def _ledger_csv(root: Path, rows: list[dict]) -> None:
    """`recommendations.csv` 的最小同构件 —— 只写 `_e6_realized_stats` 实际读的那 8 列
    (`load_ledger` 是裸 `csv.DictReader`,列子集不影响解析)。"""
    p = root / "scan" / "_ledger" / "recommendations.csv"
    p.parent.mkdir(parents=True, exist_ok=True)
    cols = ["run_id", "analysis_date", "mode", "role", "e6_buy", "outcome_status",
            "actionability", "gap_c1_o2"]
    with p.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=cols)
        w.writeheader()
        for r in rows:
            w.writerow({c: r.get(c, "") for c in cols})


# Task 4(2026-09-24):`expected_abs_gap` 是写死的 UNMEASURED stub(`relative_buy.py:1145`
# 永远 `{"value": None, "status": "UNMEASURED", "n": 0}`,见 `test_relative_buy.py::
# test_expected_abs_gap_is_always_unmeasured_in_v1`)——brief ③ 从前直接印它,于是「样本不足
# 禁止拍数」这句话每天不变,**看起来像度量、其实是常量**。改口径后 BUY 行印账本
# `recommendations.csv` 里 e6_buy 行的真实现的均值;下面两条把「负 → 弱市相对最优 / 正 →
# 不许声称上涨」这条不变量**原样保留**,只把驱动它的输入从决策文件换成账本
# (controller 2026-09-24 裁定 P5:同一条不变量,只换数字来源,字面断言不许变)。

def test_negative_abs_gap_forces_weak_market_phrase(tmp_path, monkeypatch, scan):
    """账本 ≥20 笔已核验 active BUY 均值为负 → 固定含「弱市相对最优」(语义纪律②)。"""
    from autoresearch.common import workspace as ws

    monkeypatch.setattr(ws, "reports_root", lambda: tmp_path / "reports")
    _ledger_csv(tmp_path / "reports", [
        {"run_id": f"r{i}", "analysis_date": "2026-08-06", "mode": "active", "role": "BUY",
         "e6_buy": "True", "outcome_status": "MATURE", "actionability": "ACTIONABLE",
         "gap_c1_o2": "-0.0233"} for i in range(20)])
    md = brief.build(scan, run_folder=_RUN)["markdown"]
    assert "弱市相对最优" in md              # 字面量,不引常量(防同漂)
    for banned in ("预计上涨", "看涨", "必涨"):
        assert banned not in md


def test_positive_abs_gap_does_not_claim_upside(tmp_path, monkeypatch, scan):
    """账本 ≥20 笔已核验 active BUY 均值为正 → 不含「弱市相对最优」,也不得新增看涨措辞。"""
    from autoresearch.common import workspace as ws

    monkeypatch.setattr(ws, "reports_root", lambda: tmp_path / "reports")
    _ledger_csv(tmp_path / "reports", [
        {"run_id": f"r{i}", "analysis_date": "2026-08-06", "mode": "active", "role": "BUY",
         "e6_buy": "True", "outcome_status": "MATURE", "actionability": "ACTIONABLE",
         "gap_c1_o2": "0.0142"} for i in range(20)])
    md = brief.build(scan, run_folder=_RUN)["markdown"]
    assert "弱市相对最优" not in md
    for banned in ("预计上涨", "看涨", "必涨"):
        assert banned not in md


def test_buy_line_reports_realized_ledger_stats_not_a_stub(tmp_path, monkeypatch, scan):
    """n<20 只报 n,不印固定的「样本不足禁止拍数」stub 措辞,也不印 composite 池那句固定的
    42 扫描日证据句(那句现在只在 `pool==composite` 且经由 `_realized_text` 时才可能出现,
    且措辞已经不再是这个 42 日字面量——见下方 composite 测试)。"""
    from autoresearch.common import workspace as ws

    monkeypatch.setattr(ws, "reports_root", lambda: tmp_path / "reports")
    _ledger_csv(tmp_path / "reports", [
        {"run_id": f"r{i}", "analysis_date": "2026-08-06", "mode": "active", "role": "BUY",
         "e6_buy": "True", "outcome_status": "MATURE", "actionability": "ACTIONABLE",
         "gap_c1_o2": "-0.005"} for i in range(7)])
    text = brief.build(scan, run_folder=_RUN)["markdown"]
    assert "账本 BUY 实测 n=7,不足 20 不给区间" in text
    assert "样本不足禁止拍数" not in text
    assert "+0.14/+0.17pp" not in text


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
                 "relative.rank"):
        assert must in fields, f"sources 缺关键字段 {must}"


# ────── 相对基准的两个人口不可互换(B-1,2026-08-09 全支终审)──────
#
# **病灶**:brief ③ 曾把 `benchmark.market.n` —— **决策层**四面分位/P10 流动性门的分母
# (= 当日 L0 过门票)—— 直接挂在 `rel_gap_market` 名下,写成
# 「基准 rel_gap_market(L0 可交易 4237 等权)」。可 `rel_gap_market` 这一列的真实分母是
# **全市场可交易**(`ruler.py` I-1 人口裁定,含漏在 L0 的票),两者常年不等
# (2026-08-04 实测 4193 vs 5426)。`relative_ledger` 早写明「两个『基准 n』**不可互换**
# ……引用时必须点名是哪一个」,`relative_buy` 的 I-2 也专门把 `definition`(L0)与
# `eval_population`(全市场)拆成两个字段 —— T25 又把它们合回去了,而且合在**用户每天读的
# brief ③ + summary 🧭 仪表盘**里。
#
# **守卫为什么必须写在这一层**:这行文本同时是 `sources` 边表 `relative.*` 的锚,T27 的
# `brief_lint` 比对的是同一个错源 —— 错得再离谱它也永远开绿灯(「探针死了也像活着」同族)。

def _benchmark_cell(md: str) -> str:
    """③ 相对 BUY 行里 `基准 …` 那一格(到下一个 ` · ` 为止)。"""
    assert "基准 " in md, "brief ③ 没有基准格 —— 断言的靶子不在了"
    return md.split("基准 ", 1)[1].split(" · ", 1)[0]


def test_l0_gate_n_is_not_labelled_as_rel_gap_market_population(scan):
    """**变异探针**:把这格改回 `基准 {col}(L0 可交易 {n} 等权)` 必红。"""
    cell = _benchmark_cell(brief.build(scan, run_folder=_RUN)["markdown"])
    assert "rel_gap_market" in cell
    assert "全市场可交易" in cell, \
        f"没点名 rel_gap_market 的真人口(全市场可交易):{cell!r}"
    assert "4237" not in cell or "决策层" in cell, \
        f"L0 过门票的 n 被当成了 rel_gap_market 的人口:{cell!r}"


def test_sources_distinguish_the_two_benchmark_populations(scan):
    """验收:边表里这两个 n **能分得开** —— 字段名 + locator 各自点名是哪一个。"""
    rows = {r["field"]: r for r in brief.build(scan, run_folder=_RUN)["sources"]}
    assert "relative.market_n" not in rows, \
        "含糊字段名 `relative.market_n` 复活了 —— 读的人无从知道是哪个 n"
    pool = rows["relative.decision_pool_n"]
    assert pool["value"] == "4237"
    assert pool["locator"].startswith("benchmark.market.n")
    assert "L0" in pool["locator"] and "决策层" in pool["locator"]
    pop = rows["relative.eval_population"]
    assert pop["locator"].startswith("benchmark.market.eval_population")
    assert "全市场可交易" in pop["value"]
    assert pool["value"] != pop["value"], "两行取到了同一个值 = 又合回去了"


def test_eval_population_falls_back_when_decision_doc_predates_the_split(tmp_path):
    """老决策文档(I-2 拆字段之前)没有 `eval_population` → 回落到常量,**不许**回落成
    那个 L0 的 n(回落错就是把病换个地方复发)。"""
    doc = _decision()
    doc["benchmark"]["market"].pop("eval_population")
    scan = _scan_dir(tmp_path, decision=doc)
    out = brief.build(scan, run_folder=_RUN)
    cell = _benchmark_cell(out["markdown"])
    assert "全市场可交易" in cell
    rows = {r["field"]: r for r in out["sources"]}
    assert "全市场可交易" in rows["relative.eval_population"]["value"]
    assert rows["relative.eval_population"]["value"] != rows["relative.decision_pool_n"]["value"]


def test_no_details_or_trace_in_whitelist():
    """禁读 details 全文与 trace 大文件(任务书 Interfaces 硬约束)。"""
    for name in brief.INPUT_WHITELIST:
        assert not name.startswith("details/")
        assert not name.startswith("trace/")
        assert "attribution.csv" not in name or name.startswith("retro/")


# ─────────────── 白名单双向不变量(fix-1,复核 I-2/M-2) ───────────────
#
# 复核 I-2 的病根不是「漏了一个文件」,而是**没有任何东西守着「白名单 = 实际读取集」**:
#   - 缺 ⊇ 方向 → 可以偷偷读白名单外的文件(`_tripwire_conflicts.json` 就这么读了一轮);
#   - 缺 ⊆ 方向 → 可以往表里挂一条从没接线的「许愿项」冒充契约(`market_view.md` 躺了一轮)。
# 下面两条把双向都焊住。⊇ 用 AST 抽本模块的文件名字面量,不靠人肉 grep。

def _file_literals_in_brief() -> set[str]:
    """brief.py 里所有以 .json/.csv/.md 结尾的字符串字面量 → **basename**。

    取 basename 而不是原串:路径常由 `a / "b" / "c.csv"` 拼出、或嵌在 f-string 的散文里
    (`"…均无 retro/attribution.csv"`),原串比对会把这些误判成越权读取。
    """
    import ast
    tree = ast.parse(Path(brief.__file__).read_text(encoding="utf-8"))
    return {Path(n.value).name for n in ast.walk(tree)
            if isinstance(n, ast.Constant) and isinstance(n.value, str)
            and n.value.endswith((".json", ".csv", ".md"))}


def _whitelisted(name: str) -> bool:
    return (name in brief.INPUT_WHITELIST
            or any(w.endswith("/" + name) for w in brief.INPUT_WHITELIST)
            or name in brief.OUTPUT_FILENAMES)


def test_whitelist_covers_every_file_read():
    """⊇:模块里出现的每个文件名字面量都必须在白名单(或是本模块的产出)。"""
    stray = sorted(n for n in _file_literals_in_brief() if not _whitelisted(n))
    assert not stray, f"这些文件被 brief 碰到却不在 INPUT_WHITELIST:{stray}"


def test_no_details_or_trace_path_literal_in_brief():
    """禁读 `details/` 全文与 `trace/` 大文件 —— 这条查的是**源码里有没有这种路径**,
    与只查白名单内容的 `test_no_details_or_trace_in_whitelist` 是互补的两条。

    唯一豁免:`_prev_published`(Task 3)按行号圈定,读的是**另一场已发布 run**的
    `trace/staging/` 小型结构化镜像(`_final_ratings.json`/`finalists.csv`,与
    `outcome.run_facts` 的读盘优先级同源约定),不是本场 scan 自己的 `trace/` 大文件。
    豁免只按函数名圈定行号,不是把 `"trace"` 整体摘出黑名单 —— 该函数之外任何地方
    再出现裸 `"trace"`/`"details"` 仍然要红。
    """
    import ast
    tree = ast.parse(Path(brief.__file__).read_text(encoding="utf-8"))
    exempt_fn = next((n for n in ast.walk(tree)
                      if isinstance(n, ast.FunctionDef) and n.name == "_prev_published"), None)
    exempt_lines = (set(range(exempt_fn.lineno, exempt_fn.end_lineno + 1))
                   if exempt_fn else set())
    bad = [n.value for n in ast.walk(tree)
           if isinstance(n, ast.Constant) and isinstance(n.value, str)
           and (n.value.startswith(("details/", "trace/")) or n.value in ("details", "trace"))
           and n.lineno not in exempt_lines]
    assert not bad, f"brief 出现 details/trace 路径字面量:{bad}"


def test_whitelist_has_no_dead_entry(tmp_path, monkeypatch):
    """⊆:表内每一项都必须真被读过。判据不是「代码里提过」而是「**sources 边表用过它**」
    —— 前者可以靠一句死注释满足,后者必须真的渲染出一个数。

    三个虚拟项(`buy_ledger`/`menu_health`/`feedback_store`)与 `temperature.csv` 同理:
    它们也各自出边表行。`retro/attribution.csv` 的 R-X1 行是三态常驻的,必然在场。

    `manifest.json`(Task 3:跨 run 昨日 delta 的读点)只在**新分支**——run 分区活体扫描
    ∧ `run_health.churn` 缺失 ∧ 存在已发布的上一场 run——下才会被引用,普通 `scan` 夹具的
    churn 恒在,永远摸不到新分支。这里搭一份满足新分支的夹具,让它真的读到、真的出边表行,
    而不是把这一项从 ⊆ 断言里悄悄摘掉(controller 2026-09-24 裁定:宁可搭夹具也不许摘条目)。
    """
    from autoresearch.common import workspace as ws
    from autoresearch.scan.buyability import write_buyability

    scan = _scan_dir(tmp_path)
    _strip_churn(scan)
    write_buyability(scan)  # Task 21:白名单新条目「buyability」也要真被读到,不能靠豁免
    reports = tmp_path / "reports"
    _publish_prev_run(reports, date="2026-08-05", run_name="20260805-0805_2200",
                      ratings={"600018": "Underweight"})
    monkeypatch.setattr(ws, "reports_root", lambda: reports)
    live = _live_copy(scan, tmp_path)

    files = {r["file"] for r in brief.build(live, run_folder=_RUN)["sources"]}
    dead = sorted(set(brief.INPUT_WHITELIST) - files)
    # 两个 presence-gated 项在夹具里天然不出边表行,**不是死条目**:
    #   `temperature.csv`        —— tests/scan/conftest 把它隔离成不存在;
    #   `overseas_calendar.csv`  —— D-2 日历,夹具没有它(无事件 → ⑤ 不加那句)。
    # 判据仍是「必须能真渲染出一个数」:下面那条用例给了日历就要求它出现在边表里,
    # 所以这里的豁免不会把「接了白名单却永远读不到」放行(FN-1 家族防线)。
    assert dead == ["overseas_calendar.csv", "temperature.csv"], \
        f"白名单死条目(从没被读过):{dead}"


def test_overseas_line_is_sourced_when_the_calendar_exists(tmp_path):
    """D-2:日历在场 → ⑤ 风险哨多一句 **且** sources 边表有 `risk.overseas` 行。

    这条是上面那个豁免的对手方:白名单里加一项而它永远读不到,就是「生产者没接线」的
    同族(FN-1)。给了日历还不出行 = 接线断了,这里会红。
    """
    from autoresearch.scan import overseas as _ov

    scan = _scan_dir(tmp_path)
    ev_csv = scan / _ov.OVERSEAS_CSV
    ev_csv.write_text(
        "event_id,event_type,subject,window,time_quality,local_date,scheduled_at_utc,"
        "mapped_symbols,source_url,first_seen_ts,revision,status\n"
        "e1,earnings,NVDA 财报,holding_overnight,TIMED,2026-08-26,2026-08-26T20:00:00+00:00,"
        "NVDA,https://example.com/x,2026-08-26T01:00:00+00:00,r1,scheduled\n",
        encoding="utf-8")
    out = brief.build(scan, run_folder=_RUN)
    assert "海外窗" in out["markdown"]
    assert any(r["field"] == "risk.overseas" for r in out["sources"]), \
        "日历在场却没出边表行 —— 白名单接了个读不到的输入"


def test_tripwire_number_is_sourced_to_its_own_file(tmp_path):
    """I-2 本体:④ 里的 `⚠️tripwire N` 来自 `_tripwire_conflicts.json`,
    **不得**被标成 `_final_ratings.json`(sources 误标会让 T27 对着错误来源比对)。"""
    scan = _scan_dir(tmp_path)
    (scan / "_tripwire_conflicts.json").write_text(json.dumps({
        "601688": {"all_hits": [{"detail": "破线"}, {"detail": "破量"}],
                   "rating": "Hold"}}, ensure_ascii=False), encoding="utf-8")
    out = brief.build(scan, run_folder=_RUN)
    assert "⚠️tripwire 2" in out["markdown"]
    row = next(r for r in out["sources"] if r["field"] == "pinned.601688.tripwire")
    assert row["file"] == "_tripwire_conflicts.json", f"tripwire 来源被误标成 {row['file']}"
    assert row["value"] == "2"
    assert row["text"] in out["markdown"]


def test_delta_detail_is_fully_anchored(tmp_path):
    """M-1:⑥ 的**整行**(含逐只评级变动)进边表 —— 只锚 head 前缀 = 半条对账。"""
    scan = _scan_dir(tmp_path)
    prev = scan.parent / "2026-08-05"
    prev.mkdir(parents=True, exist_ok=True)
    (prev / "_final_ratings.json").write_text(
        json.dumps({"600018": "Underweight", "601688": "Hold"}), encoding="utf-8")
    out = brief.build(scan, run_folder=_RUN)
    row = next(r for r in out["sources"] if r["field"] == "delta.changes")
    assert "600018 Underweight→Hold" in row["text"], "逐只变动明细不在锚里"
    assert row["text"] in out["markdown"]


def test_market_tone_and_menu_flag_are_wired(tmp_path):
    """M-2:`market_view.md`(定调句)与 `menu_health`(菜单病旗)必须真接线,不是白名单许愿项。"""
    scan = _scan_dir(tmp_path)
    (scan / "market_view.md").write_text(
        "# 市场研判 — 2026-08-06\n\n1. **一句话定调**:**range 区间市 · 哑铃分化**"
        "——涨停 101 家,全市场中位 −0.13%。\n", encoding="utf-8")
    out = brief.build(scan, run_folder=_RUN)
    md = out["markdown"]
    assert "定调「" in md and "区间市" in md
    assert "涨停 101 家" not in md, "定调句必须截断,不许把整段倒进 brief"
    tone = next(r for r in out["sources"] if r["field"] == "market.tone")
    assert tone["file"] == "market_view.md" and tone["text"] in md
    menu = next(r for r in out["sources"] if r["field"] == "risk.menu_sick")
    assert menu["file"] == "menu_health" and menu["text"] in md


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


# ─────────────── v4.1(2026-09-25 §2.4)③ 附加行:第五门 rebalance_close 留痕 ───────────────
#
# `index_events` 块只有旋钮开时才存在;缺块必须读成「没开这道门」,不是「没有事件」——
# 三种结果(命中/源缺席/块缺席或无命中)必须互相区分得开,任何两个混同都是回归。

def _hits(codes, *, source="ok", n_unresolved_eff=0):
    return {"source": source, "gate_evaluated": source == "ok", "n_rows": len(codes),
            "n_candidates_in_events": len(codes), "n_unresolved_eff": n_unresolved_eff,
            "hits": list(codes)}


def test_buy_line_prints_rebalance_eve_vetoes_with_sources(tmp_path):
    scan = _scan_dir(tmp_path, decision=_decision(mode="active", index_events=_hits(["603127"])))
    out = brief.build(scan, run_folder=_RUN)
    assert "⛔ 指数调样生效前夜否决 1 只:603127(hard_gate.rebalance_close)" in out["markdown"]
    dumped = json.dumps(out["sources"], ensure_ascii=False)
    assert "relative.rebalance_hits" in dumped and "relative.rebalance_hit_codes" in dumped


def test_buy_line_prints_rebalance_source_absent_honestly(tmp_path):
    scan = _scan_dir(tmp_path, decision=_decision(mode="active", index_events=_hits([], source="absent")))
    md = brief.build(scan, run_folder=_RUN)["markdown"]
    assert "⛔ 指数调样门:源不可达,本日未评估" in md


def test_buy_line_prints_rebalance_source_error_honestly(tmp_path):
    """第三个 source 值(2026-09-25 §2.4 追加裁定):文件存在但读不出来(零字节/手改坏)≠ 缺席
    ——旧渲染只认 "absent" 一个非-ok 值,"error" 会落进 hits 空判断悄悄不出行(否决存在于
    该表被静默吞掉,变成三种结果里最坏的"什么都不说")。必须像 absent 一样有专属一行,
    只是把"源不可达"换成"源存在但读取失败"——同一句"门放行了,不等于无事件"。"""
    scan = _scan_dir(tmp_path, decision=_decision(mode="active", index_events=_hits([], source="error")))
    md = brief.build(scan, run_folder=_RUN)["markdown"]
    assert "⛔ 指数调样门:源存在但读取失败,本日未评估" in md
    assert "(hard_gate.rebalance_close 放行,不等于无事件)" in md


def test_buy_line_prints_rebalance_source_disabled_honestly(tmp_path):
    """minor-1(final whole-branch review):`source="absent"` 曾横跨两个世界——日历腿本身关着
    (文档化的单杆回滚:只关 `calendar.index_rebalance`,留着 E6 门)与该旋钮开着但源真的不可达。
    门后果相同(全员放行),但这句话不同:「源不可达」对前者是假话——回滚只是关了一条产它的腿,
    不是这条腿想产却产不出来。"""
    scan = _scan_dir(tmp_path, decision=_decision(mode="active", index_events=_hits([], source="disabled")))
    md = brief.build(scan, run_folder=_RUN)["markdown"]
    assert "指数调样门" in md and "日历腿" in md and "关闭" in md
    assert "源不可达" not in md
    assert "(hard_gate.rebalance_close 放行,不等于无事件)" in md


def test_buy_line_prints_unresolved_eff_candidates_line(tmp_path):
    """I3(final whole-branch review):门沉默的五种因里唯一不可读的一种——生效日解析不出/
    临时调整不套规则(`phase="unknown_eff"`)。这类候选门照样放行、不进 `hits`,一个"门本该判
    但判不了"的夜晚与"压根没什么可判"在 brief 上此前长得一模一样。"""
    scan = _scan_dir(tmp_path, decision=_decision(
        mode="active", index_events=_hits([], n_unresolved_eff=2)))
    md = brief.build(scan, run_folder=_RUN)["markdown"]
    assert "2 只候选" in md and "unknown_eff" in md and "不计入 hits" in md


def test_buy_line_prints_both_hits_and_unresolved_when_both_present(tmp_path):
    """命中与「读不清」不是互斥的两件事——同一夜可能既有票被门否决,又有另一票的生效日还
    没解析出来。两条各自出行,互不覆盖。"""
    scan = _scan_dir(tmp_path, decision=_decision(
        mode="active", index_events=_hits(["600018"], n_unresolved_eff=1)))
    md = brief.build(scan, run_folder=_RUN)["markdown"]
    assert "⛔ 指数调样生效前夜否决 1 只:600018" in md
    assert "1 只候选" in md and "unknown_eff" in md


def test_buy_line_is_silent_without_index_events_block_or_hits(tmp_path):
    scan = _scan_dir(tmp_path, decision=_decision(mode="active"))
    assert "指数调样" not in brief.build(scan, run_folder=_RUN)["markdown"]           # 旧 schema:逐字 parity
    scan2 = _scan_dir(tmp_path / "b", decision=_decision(mode="active", index_events=_hits([])))
    assert "指数调样" not in brief.build(scan2, run_folder=_RUN)["markdown"]          # 门开无命中:不出行


def test_blocked_day_also_prints_rebalance_vetoes(tmp_path):
    dec = _decision(mode="active", blocked=True, index_events=_hits(["600018", "600285"]))
    scan = _scan_dir(tmp_path, decision=dec)
    assert "⛔ 指数调样生效前夜否决 2 只:600018、600285" in brief.build(scan, run_folder=_RUN)["markdown"]


# ─────────────── R-X1 两尺分歧日提示(fix-1,复核 I-4) ───────────────
#
# 设计稿点名要进 brief 的一条,第一版**零测试**。而且第一版「同向」与「读不到」都返回 None、
# 都渲染成什么都不显示 —— 探针死了也像活着(本仓最常复发的一类病)。现在三态各有用例,
# 且 ALIGNED 与 UNMEASURED 的措辞必须不同。

def _attr(root: Path, day: str, gap: float, oc: float, *, cols=("gap_c1_o2", "fwd_2_oc")):
    d = root / day / "retro"
    d.mkdir(parents=True, exist_ok=True)
    with (d / "attribution.csv").open("w", encoding="utf-8", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=["code", *cols])
        w.writeheader()
        w.writerow({"code": "000001", cols[0]: gap, cols[1]: oc})
        w.writerow({"code": "000002", cols[0]: gap, cols[1]: oc})
    return d / "attribution.csv"


# ─────────────── M-10:buy_ledger.roll 单次发布只跑一次 ───────────────
#
# 病灶:一次 `publisher.run` 里 `roll(context/scan)` 被跑三次(build_summary 的 _ow_base_line /
# brief 生成 / T27 lint 的边表重算),各 ~0.28s,而 registry 的 speed 守卫是 `wall_delta_s ≤ 5`。
# 共享的前提「三次之间 ledger 输入没被写过」已用 tmp 拷贝实跑确认(见 `ow_base_cache` docstring)。
# 硬约束:**只在单次发布生命周期内复用,绝不跨发布**——下面三条把两个方向都焊住。

def _count_rolls(monkeypatch) -> list:
    from autoresearch.learning import buy_ledger
    calls: list = []
    real = buy_ledger.roll

    def traced(scan_root=None):
        calls.append(str(scan_root))
        return real(scan_root)

    monkeypatch.setattr(buy_ledger, "roll", traced)
    return calls


# ───────────────────────────── 七节骨架 + 落盘 ─────────────────────────────

def test_six_sections_present(scan):
    md = brief.build(scan, run_folder=_RUN)["markdown"]
    # 2026-08-21 learning 层退役:原 ⑦ 欠账(待裁决提案/未决反馈)随 feedback_store 删除。
    for mark in ("① 市场", "② 漏斗", "③ 结论", "④ 持仓", "⑤ 风险哨",
                 "⑥ 昨日 delta"):
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
    # T26:同一份成品的 ①②③④ 必须已注回 summary 的 🧭 managed 块(两边同源)
    md = summary.read_text(encoding="utf-8")
    assert "此处为占位" not in md, "仪表盘注入未跑(summary 还留着占位文案)"
    for line in brief.dashboard_block(brief.build(scan, run_folder="20260806_2308")).splitlines():
        assert line in md, f"仪表盘与 brief 不同源:{line[:40]}"


def test_pinned_section_lists_every_pinned_holding(scan):
    md = brief.build(scan, run_folder=_RUN)["markdown"]
    line = next(ln for ln in md.splitlines() if "④ 持仓" in ln)
    for name in ("华泰证券", "宁德时代", "协创数据"):
        assert name in line
    assert "上港集团" not in line, "非保送票不进持仓动作区"


# ── E3(task-2.4):active 期 ③ 段的旧 OW 行降为「研究评级分布」────────────────


def test_active_mode_demotes_the_legacy_ow_line_to_a_rating_distribution(tmp_path):
    """active 期「生产 BUY N 只」这个名字必须消失 —— 它会把研究评级张数读成买入建议。

    变异校验:把 `_buy_lines` 里的 active 分支删掉,本条立刻变红。
    """
    scan = _scan_dir(tmp_path, decision=_decision(mode="active"))
    md = brief.build(scan, run_folder=_RUN)["markdown"]

    assert "生产 BUY" not in md
    assert "**研究评级分布**" in md
    assert "证据不是决策" in md
    assert "✅ **relative BUY**" in md                      # 两行同源:一个正式另一个也正式
    assert "🕶" not in md


def test_active_mode_renames_the_why_line(tmp_path):
    """active 期「为什么没买」改叫「为什么没有 ≥OW 卡」—— BUY 在场的日子不能同屏
    出现「✅ relative BUY 600018」和「为什么没买」。"""
    scan = _scan_dir(tmp_path, decision=_decision(mode="active"))
    md = brief.build(scan, run_folder=_RUN)["markdown"]

    assert "为什么没买" not in md
    line = next(ln for ln in md.splitlines() if "为什么没有 ≥OW 卡" in ln)
    assert "两类不混算" in line


def test_shadow_mode_keeps_the_legacy_wording(tmp_path):
    """parity:影子期两行措辞逐字不变。"""
    scan = _scan_dir(tmp_path)
    md = brief.build(scan, run_folder=_RUN)["markdown"]

    assert "**生产 BUY 0 只**(旧绝对门 ≥Overweight;" in md
    assert "└ 为什么没买:" in md
    assert "研究评级分布" not in md


# ── v3.0 composite 池的诚实呈现(2026-08-26 §3 路A · A5)────────────────────

#: composite 池三条测试手搭 `facts`(不经 `collect_facts`),要补上 `_buy_lines` 现在
#: 无条件要读的 `e6_realized` 键 —— 等价于「账本缺 → n=0」(`_e6_realized_stats` 的空账本分支)。
_NO_LEDGER_STATS = {"buy": {"n": 0, "mean_pp": None, "win": None},
                    "seat": {"n": 0, "mean_pp": None, "win": None}}


def _composite_decision():
    return {
        "mode": "active", "rule_version": "e6.v3.0", "blocked": False,
        "pool": "composite", "pool_members": ["600188", "601699"],
        "date": "2026-08-25", "ruler": "gap_c1_o2",
        "buys": [{"code": "600188", "basis": "relative", "rank": 1}],
        "counts": {"candidates": 5, "eligible": 2, "in_pool": 2},
        "candidates": [{"code": "600188", "name": "兖矿能源", "rank": 1,
                        "research_rating": "Hold", "eligible": True,
                        "relative_decision_score": 0.5,
                        "expected_abs_gap": {"status": "UNMEASURED", "n": 0}}],
        "benchmark": {"market": {"column": "rel_gap_market", "n": 4312},
                      "sector": {"column": "rel_gap_sector"}},
    }


def test_composite_pool_brief_states_pool_evidence_and_exec_line():
    """A5 诚实呈现:BUY 行必须说清**从哪个池选的**,并在下面写证据量级与执行线 ——
    否则读者会把「相对最优」读成「明天会涨」。"""
    from autoresearch.scan.brief import _buy_lines
    from autoresearch.scan.relative_facts import relative_facts

    facts = {"buys": {"production_n": 0, "dist": {"Hold": 2}, "run_mode": "FULL",
                      "n_early": 0},
             "relative": relative_facts(_composite_decision()),
             "e6_realized": _NO_LEDGER_STATS}
    text = "\n".join(_buy_lines(facts, []))
    assert "池=composite 证据席(2 只)" in text
    assert "不承诺绝对收益为正" in text          # 期望的上限措辞
    assert "执行线" in text and "≤3%" in text and "上 30%" in text
    assert "四年逐年为负" in text                # 方向与直觉相反,必须连证据一起说


def test_finalists_pool_brief_has_no_composite_addendum():
    """v2 池不该长出 v3 的两行 —— 两条规则不同名、不同承诺,读数也不该被连成一条线。"""
    from autoresearch.scan.brief import _buy_lines
    from autoresearch.scan.relative_facts import relative_facts

    doc = {**_composite_decision(), "pool": "finalists", "pool_members": []}
    facts = {"buys": {"production_n": 0, "dist": {"Hold": 2}, "run_mode": "FULL",
                      "n_early": 0},
             "relative": relative_facts(doc),
             "e6_realized": _NO_LEDGER_STATS}
    text = "\n".join(_buy_lines(facts, []))
    assert "池=L3 finalist 全体" in text
    assert "执行线" not in text


def test_composite_brief_lines_stay_within_budget():
    """brief 是 ≤3KB 硬预算的速读层;新增两行不得把它顶出预算(超了只 warn,但那也是噪音)。"""
    from autoresearch.scan.brief import MAX_BYTES, _buy_lines
    from autoresearch.scan.relative_facts import relative_facts

    facts = {"buys": {"production_n": 0, "dist": {"Hold": 2}, "run_mode": "FULL",
                      "n_early": 0},
             "relative": relative_facts(_composite_decision()),
             "e6_realized": _NO_LEDGER_STATS}
    added = len("\n".join(_buy_lines(facts, [])).encode("utf-8"))
    assert added < MAX_BYTES // 2       # ③ 一节远小于半个预算


def test_worst_case_ledger_text_still_fits_budget_and_buy_line_survives(tmp_path, monkeypatch):
    """字节最坏情形(fix round 1):③ 现在有两处可变长度文本 —— BUY 行的账本实测、
    composite 证据席的账本实测。两处都用**最长形态**(n≥20 且均值为负,双双要追加
    `WEAK_MARKET_PHRASE` 限定句),再叠加既有 `test_byte_budget_holds_when_raw_render_
    would_overflow` 那招(`_flood_pinned` 把④灌到必然溢出),证明:
      ① 未裁剪的原始渲染确实 >MAX_BYTES(探针有鉴别力,不是空放绿灯);
      ② `build()` 的裁剪梯度(只砍④/⑥,`_sections`/`_FIT_LADDER`)仍能把成品**压回预算内**;
      ③ ③ 段(BUY 行 + composite 证据席两条账本实测)**原样留在成品里**——它从不在
         `_FIT_LADDER` 的裁剪范围内,这条测试把「不会被裁掉」从隐含假设钉成断言,防止
         以后有人为了压预算把裁剪范围悄悄扩到 ③。
    """
    from autoresearch.common import workspace as ws

    monkeypatch.setattr(ws, "reports_root", lambda: tmp_path / "reports")
    rows = ([{"run_id": f"b{i}", "analysis_date": "2026-08-06", "mode": "active", "role": "BUY",
              "e6_buy": "True", "outcome_status": "MATURE", "actionability": "ACTIONABLE",
              "gap_c1_o2": "-0.0233"} for i in range(20)]
            + [{"run_id": f"s{i}", "analysis_date": "2026-08-06", "mode": "active",
                "role": "composite_seat", "e6_buy": "False", "outcome_status": "MATURE",
                "actionability": "ACTIONABLE", "gap_c1_o2": "-0.0187"} for i in range(20)])
    _ledger_csv(tmp_path / "reports", rows)

    scan = _scan_dir(tmp_path, decision=_composite_decision())
    _flood_pinned(scan, 80)
    facts = brief.collect_facts(scan, run_folder=_RUN)
    raw_lines, _ = brief._sections(facts, pinned_cap=99, delta_cap=6)
    raw = len(("\n".join(raw_lines) + "\n").encode("utf-8"))
    assert raw > brief.MAX_BYTES, f"探针失效:未裁剪只有 {raw}B,压不到预算线"

    out = brief.build(scan, run_folder=_RUN)
    assert out["n_bytes"] <= brief.MAX_BYTES, (
        f"账本实测两行(BUY + composite 证据席)把 brief 顶出预算:{out['n_bytes']}B "
        f"> {brief.MAX_BYTES}B —— 缩短 _realized_text 的措辞,不许调高 MAX_BYTES")
    md = out["markdown"]
    assert "账本 BUY 实测" in md and "弱市相对最优" in md, "BUY 行的账本实测被裁剪路径吞掉了"
    assert "账本 席位 实测" in md, "composite 证据席的账本实测被裁剪路径吞掉了"


def test_worst_case_ledger_text_and_buyability_line_together_still_fit_budget(tmp_path, monkeypatch):
    """字节最坏情形,复核轮二(2026-09-25 I2/I4/I5)追加:③ 段现在还多一条可变长度
    文本——不可买归因行,本轮给它加了 `tiering`、两处人口标注(`卡(非📌候选N)`/
    `门(候选N含📌)`)和 `entry_line` 计数,行本身变长了。把它也推到最长形态(`wall=
    "cards_refused"` 的最长 gloss、`tiering=True`、两位数计数),叠加既有账本实测双行 +
    `_flood_pinned` 灌爆④,证明裁剪梯度(只砍④/⑥,`_buyability_line` 所在的③从不在
    `_FIT_LADDER` 范围内)仍能把成品压回预算内。"""
    from autoresearch.common import workspace as ws

    monkeypatch.setattr(ws, "reports_root", lambda: tmp_path / "reports")
    rows = ([{"run_id": f"b{i}", "analysis_date": "2026-08-06", "mode": "active", "role": "BUY",
              "e6_buy": "True", "outcome_status": "MATURE", "actionability": "ACTIONABLE",
              "gap_c1_o2": "-0.0233"} for i in range(20)]
            + [{"run_id": f"s{i}", "analysis_date": "2026-08-06", "mode": "active",
                "role": "composite_seat", "e6_buy": "False", "outcome_status": "MATURE",
                "actionability": "ACTIONABLE", "gap_c1_o2": "-0.0187"} for i in range(20)])
    _ledger_csv(tmp_path / "reports", rows)

    scan = _scan_dir(tmp_path, decision=_composite_decision())
    _flood_pinned(scan, 80)
    _write_buyability(
        scan, wall="cards_refused", tiering=True,
        menu={"l2_knife": 0.99, "l0_knife": 0.01, "l2_healthy": 0.01, "l0_healthy": 0.99,
             "sector_seats": 88, "composite_seats": 77},
        cards={"n": 99, "allowed": 11, "conditional": 22, "prohibited": 33, "unknown": 33,
              "entry_line": 11, "blind": 55, "parse_failed": 44, "earlystop": 66, "full": 33},
        gates={"n": 199, "data_a_day": 66, "data_a_ticker": 77, "contract": 88,
              "no_redflag": 99, "no_redflag_card_prohibited": 44})
    facts = brief.collect_facts(scan, run_folder=_RUN)
    raw_lines, _ = brief._sections(facts, pinned_cap=99, delta_cap=6)
    raw = len(("\n".join(raw_lines) + "\n").encode("utf-8"))
    assert raw > brief.MAX_BYTES, f"探针失效:未裁剪只有 {raw}B,压不到预算线"

    out = brief.build(scan, run_folder=_RUN)
    assert out["n_bytes"] <= brief.MAX_BYTES, (
        f"账本实测两行 + 不可买归因行(复核轮二最长形态)一起把 brief 顶出预算:"
        f"{out['n_bytes']}B > {brief.MAX_BYTES}B")
    md = out["markdown"]
    assert "不可买归因:**cards_refused**(卡写了,不允许) · tiering 开" in md, \
        "归因行(含本轮新增字段)被裁剪路径吞掉了"
    assert "门(候选199含📌)" in md and "卡(非📌候选99)" in md


# ───────────────────────────── ⑥ 跨 run 昨日 delta(Task 3) ─────────────────────────────

def test_delta_line_reads_previous_published_run_under_run_partition(tmp_path, monkeypatch, scan):
    """run 分区下 `scan.parent` 只装本场日期(`run_health.churn` 恒 None)→ 改从已发布 run
    目录(`outcome.published_runs`)里找上一场,不再对着「无上一扫描日」交白卷。"""
    from autoresearch.common import workspace as ws

    _strip_churn(scan)
    reports = tmp_path / "reports"
    _publish_prev_run(reports, date="2026-08-05", run_name="20260805-0805_2200",
                      ratings={"600018": "Underweight"})
    monkeypatch.setattr(ws, "reports_root", lambda: reports)
    live = _live_copy(scan, tmp_path)

    out = brief.build(live, run_folder=_RUN)
    md = out["markdown"]
    assert "vs 2026-08-05" in md
    assert "600018 Underweight→Hold" in md


# ───────────────────── Task 20:relative BUY 行的 E6 A/R 分级标签 ─────────────────────
#
# v4.0(`relative_buy.tiering`)给 `buys[0]` 挂 `tier`(A=卡面自己允许入场 / R=卡面没给
# 买点、靠「每天至少一只」的相对硬规则强出)。brief ③ 必须把这个分级念出来 —— 否则读者
# 拿到一行「relative BUY」,分不清这次是研究真的认可了,还是规则替它凑的数。
#
# fix round 1(reviewer minor,唯一一轮):R 级最初只在 ✅ 后面追加文字说明 —— brief 是
# 被快速略读的,先入眼的字形才是真正落地的信号,追加在后面读者仍先看见绿勾。改为**替换**
# 前导字形(active 期 ✅→🟥,行首即转红,标签里原来的 🟥 随之去重);A 级维持 ✅ 不变。

def test_buy_line_prints_tier_label(scan):
    """active 期是这次修复真正生效的地方:前导字形必须从 ✅ 换成 🟥,不是追加在后面。"""
    doc = json.loads((scan / brief.DECISION_FILENAME).read_text(encoding="utf-8"))
    doc["mode"] = "active"
    doc["buys"][0].update({"tier": "R", "basis": "relative_forced"})
    doc["tiering"], doc["tier_counts"] = True, {"A": 0, "R": 2}
    (scan / brief.DECISION_FILENAME).write_text(json.dumps(doc, ensure_ascii=False),
                                                encoding="utf-8")
    md = brief.build(scan, run_folder=_RUN)["markdown"]
    line = next(ln for ln in md.splitlines() if "relative BUY" in ln)
    assert line.startswith(
        "- 🟥 **relative BUY** · **R 级·卡面无买点·强制相对(裁定①)**:"), line
    assert "✅" not in md, "R 级必须换掉前导 ✅,不能只在后面追加说明——略读时先看见的还是绿勾"
    assert md.count("🟥") == 1, "替换 + 追加不能让红色标记出现两次"

    doc["buys"][0].update({"tier": "A", "basis": "card_backed"})
    (scan / brief.DECISION_FILENAME).write_text(json.dumps(doc, ensure_ascii=False),
                                                encoding="utf-8")
    md = brief.build(scan, run_folder=_RUN)["markdown"]
    line = next(ln for ln in md.splitlines() if "relative BUY" in ln)
    assert line.startswith("- ✅ **relative BUY** · **A 级·卡面允许入场**:"), line


def test_shadow_r_tier_replace_is_a_harmless_noop(scan):
    """影子期前导字形是 🕶 不是 ✅,`tag.replace("✅", "🟥")` 找不到目标 —— 必须**确认**
    这确实是无操作,而不是想当然:不能留下两个标记(🕶+🟥),也不能一个都不剩。"""
    doc = json.loads((scan / brief.DECISION_FILENAME).read_text(encoding="utf-8"))
    doc["buys"][0].update({"tier": "R", "basis": "relative_forced"})
    doc["tiering"], doc["tier_counts"] = True, {"A": 0, "R": 2}
    (scan / brief.DECISION_FILENAME).write_text(json.dumps(doc, ensure_ascii=False),
                                                encoding="utf-8")
    md = brief.build(scan, run_folder=_RUN)["markdown"]
    line = next(ln for ln in md.splitlines() if "relative BUY" in ln)
    assert line.startswith(
        "- 🕶 **影子 relative BUY(非正式·不执行)** · **R 级·卡面无买点·强制相对(裁定①)**:"
    ), line
    assert "✅" not in md
    assert "🟥" not in md, "影子期不该补红——非正式已经是限定语,不能凭空多出一个标记"
    assert md.count("🕶") == 1, "不能变成两个标记"


# ───────────────── Task 21(2026-09-24 §2.7 + 2026-09-25 controller 追加裁定):
# 不可买归因 ③ 附加行 ─────────────────

def _write_buyability(scan: Path, **overrides) -> None:
    """`_buyability.json` 的最小同构件(字段名照抄 `buyability.build_buyability` 的产物
    形状,不发明)。"""
    doc = {
        "schema_version": 1, "date": _DATE, "wall": "cards_silent",
        "menu": {"l2_knife": 0.6, "l0_knife": 0.55, "l2_healthy": 0.03, "l0_healthy": 0.02,
                "sector_seats": 12, "composite_seats": 1},
        "cards": {"n": 9, "allowed": 0, "conditional": 2, "prohibited": 4, "unknown": 3,
                 "blind": 0, "parse_failed": 0, "earlystop": 6, "full": 3},
        "gates": {"data_a_day": 0, "data_a_ticker": 0, "contract": 0, "no_redflag": 0,
                 "no_redflag_card_prohibited": 0},
        "buy": {"tier": None, "code": None, "blocked": False},
    }
    doc.update(overrides)
    (scan / "_buyability.json").write_text(json.dumps(doc, ensure_ascii=False), encoding="utf-8")


def test_buyability_line_renders_on_a_blocked_day(tmp_path):
    """coordinator 2026-09-25 追加裁定:blocked 日是这一行存在的理由(真实 8 个 Claude
    扫描日 6 个 blocked)—— 只测有买日会让「功能在最要紧的地方缺席」这件事亮绿灯。归因行
    必须**紧跟**在 BLOCKED 桶那行后面:那行把 `hard_gate.no_redflag` 六个否决因揉成一个
    数,归因行才是把「卡面入场=禁止」从中拆出来的地方。"""
    scan = _scan_dir(tmp_path, decision=_decision(blocked=True))
    _write_buyability(scan, wall="cards_refused",
                      gates={"data_a_day": 0, "data_a_ticker": 0, "contract": 0,
                             "no_redflag": 7, "no_redflag_card_prohibited": 5})
    md = brief.build(scan, run_folder=_RUN)["markdown"]
    assert "**BLOCKED**" in md
    lines = md.splitlines()
    idx_blocked = next(i for i, ln in enumerate(lines) if "**BLOCKED**" in ln)
    idx_ba = next(i for i, ln in enumerate(lines) if "不可买归因" in ln)
    assert idx_ba == idx_blocked + 1, "归因行必须紧跟在 BLOCKED 行后面,不能被夹在别处"
    assert "不可买归因:**cards_refused**(卡写了,不允许)" in lines[idx_ba]
    assert "redflag 7(卡禁 5)" in lines[idx_ba]


def test_buyability_line_renders_on_the_normal_buy_path_with_gloss(scan):
    """有买日(正常出口)一样要出这一行,且两个新值各自带一句短注 —— 计数(允许/条件/
    禁止/未知/盲)本身分不清 silent 和 refused(两支世界能落在同一套计数上,entry_source
    是正交维度),所以 gloss 不是可省的装饰。"""
    _write_buyability(scan, wall="cards_silent")
    md = brief.build(scan, run_folder=_RUN)["markdown"]
    assert "不可买归因:**cards_silent**(没有卡写入场行)" in md
    assert "早停 6/满卡 3" in md
    assert len(md.encode("utf-8")) <= brief.MAX_BYTES


def test_buyability_line_absent_when_artifact_missing(scan):
    """presence-gating,不是规则:`_buyability.json` 不存在(如决策产物本就没生成的
    present=False 场景,或旧 run)时,三个出口都不必特判 —— 内部 `if not ba.get("wall")`
    guard 天然不出线。"""
    md = brief.build(scan, run_folder=_RUN)["markdown"]
    assert "不可买归因" not in md


def test_buyability_facts_key_is_in_the_whitelist():
    assert ("artifact", "buyability") in brief._WHITELIST_SPEC
    assert "_buyability.json" in brief.INPUT_WHITELIST


def test_buyability_line_still_renders_on_the_present_false_path(tmp_path):
    """fix round 1(reviewer mutation finding):the present=False exit calls
    `_buyability_line`/appends `ba_line` on its own literal line — deleting that append is
    invisible to every other test in this suite (the `scan` fixture always has a decision
    document, so it never takes this branch; `test_buyability_line_absent_when_artifact_
    missing` only proves the internal guard no-ops, not that this exit calls the helper at
    all). Build the one combination that pins it directly: no decision document (present=
    False) but a `_buyability.json` that already has a real `wall` — an artificial pairing
    post-fix (`build_buyability` itself would now emit `wall=None` for a missing decision
    document; see `tests/scan/test_buyability.py::test_wall_is_none_when_decision_document_
    is_missing`), constructed here purely to catch a regression on this exit's own append
    line, independent of what upstream would ever actually produce."""
    scan = _scan_dir(tmp_path, with_decision=False)
    _write_buyability(scan, wall="cards_silent")
    md = brief.build(scan, run_folder=_RUN)["markdown"]
    assert "不可买归因:**cards_silent**" in md


def test_buyability_line_shows_dash_for_missing_seat_counts(scan):
    """fix round 1 finding ②:`sector_seats`/`composite_seats` 现在缺源是 `None`,brief 渲染
    必须显式印「—」,不能让 f-string 吐出字面量 "None"。"""
    _write_buyability(scan, wall="cards_silent",
                      menu={"l2_knife": 0.6, "l0_knife": 0.55, "l2_healthy": 0.03, "l0_healthy": 0.02,
                            "sector_seats": None, "composite_seats": None})
    md = brief.build(scan, run_folder=_RUN)["markdown"]
    assert "席位 行业 —/证据 —" in md
    assert "None" not in md


# ───────── 复核轮二(2026-09-25)I2/I4/I5 在 brief 渲染层的落点 ─────────

def test_buyability_line_shows_dash_for_missing_earlystop_full_counts(scan):
    """I4(a):`cards.earlystop`/`cards.full` 现在缺 schema 支持时是 `None`,brief 渲染必须
    显式印「—」,不能让 f-string 吐出字面量 "None"(同 finding ②对 seat 计数的处置)。"""
    _write_buyability(scan, wall="cards_silent",
                      cards={"n": 9, "allowed": 0, "conditional": 2, "prohibited": 4, "unknown": 3,
                            "entry_line": 0, "blind": 0, "parse_failed": 0,
                            "earlystop": None, "full": None})
    md = brief.build(scan, run_folder=_RUN)["markdown"]
    assert "早停 —/满卡 —" in md
    assert "None" not in md


def test_buyability_line_renders_tiering_and_the_two_population_labels(scan):
    """I4(b)/I4(c):`gates.n`(全体候选,含 pinned)与 `cards.n`(非📌候选)并排标注两个不同
    人口,不让读者拿 `data_a_day` 之类的门计数去对错分母;`tiering` 紧跟 wall/gloss 渲染,
    三态(开/关/—)不折叠成布尔。`entry_line`(I2)挨着其它卡计数一起印。"""
    _write_buyability(scan, wall="cards_refused", tiering=True,
                      cards={"n": 9, "allowed": 0, "conditional": 2, "prohibited": 4, "unknown": 3,
                            "entry_line": 5, "blind": 0, "parse_failed": 0, "earlystop": 6, "full": 3},
                      gates={"n": 11, "data_a_day": 0, "data_a_ticker": 0, "contract": 0,
                             "no_redflag": 0, "no_redflag_card_prohibited": 0})
    md = brief.build(scan, run_folder=_RUN)["markdown"]
    assert "tiering 开" in md
    assert "卡(非📌候选9)" in md
    assert "入场行 5" in md
    assert "门(候选11含📌)" in md


def test_buyability_line_shows_dash_for_tiering_when_the_decision_document_predates_the_key(scan):
    """I4(c):决策文档没有 `tiering` 这个键(schema 1 / 更早的 schema 2)时,产物读 `None`
    (`buyability.py` 已锁死),brief 必须显式印「—」,不得默认渲染成"关"。"""
    _write_buyability(scan, wall="cards_silent")   # 默认 doc 没有 "tiering" 键
    md = brief.build(scan, run_folder=_RUN)["markdown"]
    assert "tiering —" in md
