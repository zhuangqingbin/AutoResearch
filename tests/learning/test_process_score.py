"""P0-4 过程分机检 checklist 单测 —— 6 项确定性布尔项 + process_score 汇总,零 LLM/无网络。

design: docs/specs/2026-07-12-selflearning-optimization-brainstorm.md §4 P0-4;
plan:   docs/plans/2026-07-12-selflearning-p0-plan.md T2。

覆盖:
  - presence-gated 空表(无 finalists.csv / 卡片缺失)
  - 6 项 checklist 逐项(数字机检回环 / 盲读微pass / 基率或目标锚 / 卡片契约lint /
    评级rubric自洽 / slim体积)+ process_score 汇总
  - write_process_scores 的落盘/不落盘(presence-gated)
"""
from __future__ import annotations

from pathlib import Path

import pandas as pd

from autoresearch.learning import process_score as ps

_DATE = "2026-07-08"

_CARD_TMPL = """# 决策卡 — {code} {name} @ {date}

## 决策仪表盘
| 评级 | 现价 | EV目标 | R:R | 置信度 |
|---|---|---|---|---|
| **{rating}** | 10.00 | 11.00(+10%) | 2:1 | 中 |

{p4_line}
{extra}
**Rubric建议**: {rubric}(净分 +2,OW三门 3/3)
{dev_line}
**Rating**: {rating}

FINAL TRANSACTION PROPOSAL: **{prop}**
"""


def _scan_dir(root: Path) -> Path:
    d = root / "context" / "scan" / _DATE
    (d / "details").mkdir(parents=True)
    return d


def _write_finalists(scan: Path, codes: list[str]) -> None:
    pd.DataFrame([{"ticker": c, "code": c, "name": f"票{c}", "sector": "电子"} for c in codes]
                ).to_csv(scan / "finalists.csv", index=False)


def _write_card(scan: Path, code: str, *, rating: str = "Hold", rubric: str | None = None,
                dev: bool = False, extra: str = "", no_p4_marker: bool = False) -> None:
    """写一张满足『卡片契约 lint 通过』基线的卡(含 进入P4倾向 行,规避 P4 warn);
    no_p4_marker=True → 故意不写该行,触发『卡片契约·P4倾向缺失』lint。"""
    rubric = rubric if rubric is not None else rating
    prop = "BUY" if rating in ("Buy", "Overweight") else ("SELL" if rating in ("Underweight", "Sell") else "HOLD")
    text = _CARD_TMPL.format(
        code=code, name=f"票{code}", date=_DATE, rating=rating, rubric=rubric,
        dev_line=("**偏离**:测试硬理由" if dev else ""), extra=extra, prop=prop,
        p4_line=("" if no_p4_marker else f"进入P4倾向: {rating}"))
    (scan / "details" / f"{code}.md").write_text(text, encoding="utf-8")


# ───────────────────────── presence-gated 空表 ─────────────────────────


def test_no_finalists_csv_returns_empty_frame(tmp_path):
    scan = _scan_dir(tmp_path)
    df = ps.compute_process_scores(scan)
    assert df.empty
    assert list(df.columns) == ["code", *ps._CHECKS, "process_score"]


def test_missing_card_all_checks_false_zero_score(tmp_path):
    """finalists.csv 引用的票没有对应 details/<code>.md → 6 项全 False,process_score=0。"""
    scan = _scan_dir(tmp_path)
    _write_finalists(scan, ["300476"])
    df = ps.compute_process_scores(scan)
    assert len(df) == 1
    row = df.iloc[0]
    assert row["code"] == "300476"
    assert not any(row[c] for c in ps._CHECKS)
    assert row["process_score"] == 0


# ───────────────────────── checklist⑤:评级=rubric建议自洽 ─────────────────────────


def test_chk_rubric_consistent_true_when_rating_matches_rubric(tmp_path):
    scan = _scan_dir(tmp_path)
    _write_finalists(scan, ["300476"])
    _write_card(scan, "300476", rating="Overweight", rubric="Overweight")
    df = ps.compute_process_scores(scan)
    assert df.iloc[0]["chk_rubric_consistent"]


def test_chk_rubric_consistent_false_when_mismatch_without_deviation_note(tmp_path):
    scan = _scan_dir(tmp_path)
    _write_finalists(scan, ["300476"])
    _write_card(scan, "300476", rating="Overweight", rubric="Hold", dev=False)
    df = ps.compute_process_scores(scan)
    assert not df.iloc[0]["chk_rubric_consistent"]


def test_chk_rubric_consistent_true_when_mismatch_but_deviation_noted(tmp_path):
    """评级 ≠ rubric 建议,但卡片写了 **偏离** 硬理由(卡片模板契约允许的合规偏离)。"""
    scan = _scan_dir(tmp_path)
    _write_finalists(scan, ["300476"])
    _write_card(scan, "300476", rating="Overweight", rubric="Hold", dev=True)
    df = ps.compute_process_scores(scan)
    assert df.iloc[0]["chk_rubric_consistent"]


# ───────────────────────── checklist②:盲读微pass 节存在 ─────────────────────────


def test_chk_blind_pass_true_when_marker_present(tmp_path):
    scan = _scan_dir(tmp_path)
    _write_finalists(scan, ["300476"])
    _write_card(scan, "300476", extra="**独立初判**:资金净流入,技术多头排列,估值不贵。")
    df = ps.compute_process_scores(scan)
    assert df.iloc[0]["chk_blind_pass"]


def test_chk_blind_pass_false_when_marker_absent(tmp_path):
    scan = _scan_dir(tmp_path)
    _write_finalists(scan, ["300476"])
    _write_card(scan, "300476")
    df = ps.compute_process_scores(scan)
    assert not df.iloc[0]["chk_blind_pass"]


# ───────────────────────── checklist③:基率或目标锚行已渲染 ─────────────────────────


def test_chk_base_rate_or_target_true_when_prompt_has_base_rate_marker(tmp_path):
    scan = _scan_dir(tmp_path)
    _write_finalists(scan, ["300476"])
    _write_card(scan, "300476")
    (scan / "_l4_prompt_300476.md").write_text("共享块...\n\n🔁 基率:趋势 lane 高确信历史被 L4 翻案 30%(n=12)",
                                               encoding="utf-8")
    df = ps.compute_process_scores(scan)
    assert df.iloc[0]["chk_base_rate_or_target"]


def test_chk_base_rate_or_target_true_when_prompt_has_target_calib_marker(tmp_path):
    scan = _scan_dir(tmp_path)
    _write_finalists(scan, ["300476"])
    _write_card(scan, "300476")
    (scan / "_l4_prompt_300476.md").write_text("📐 目标校准:全体 2 日 MFE p60=+3.7%(n=200)", encoding="utf-8")
    df = ps.compute_process_scores(scan)
    assert df.iloc[0]["chk_base_rate_or_target"]


def test_chk_base_rate_or_target_false_when_prompt_missing(tmp_path):
    scan = _scan_dir(tmp_path)
    _write_finalists(scan, ["300476"])
    _write_card(scan, "300476")
    df = ps.compute_process_scores(scan)
    assert not df.iloc[0]["chk_base_rate_or_target"]


def test_chk_base_rate_or_target_false_when_prompt_present_but_no_marker(tmp_path):
    scan = _scan_dir(tmp_path)
    _write_finalists(scan, ["300476"])
    _write_card(scan, "300476")
    (scan / "_l4_prompt_300476.md").write_text("共享块,无基率/目标锚。", encoding="utf-8")
    df = ps.compute_process_scores(scan)
    assert not df.iloc[0]["chk_base_rate_or_target"]


# ───────────────────────── checklist④:卡片契约 lint 通过 ─────────────────────────


def test_chk_card_contract_true_when_no_lint_hit(tmp_path):
    scan = _scan_dir(tmp_path)
    _write_finalists(scan, ["300476"])
    _write_card(scan, "300476")   # 默认带 进入P4倾向 行 → 规避 lint
    df = ps.compute_process_scores(scan)
    assert df.iloc[0]["chk_card_contract"]


def test_chk_card_contract_false_when_p4_marker_missing(tmp_path):
    scan = _scan_dir(tmp_path)
    _write_finalists(scan, ["300476"])
    _write_card(scan, "300476", no_p4_marker=True)
    df = ps.compute_process_scores(scan)
    assert not df.iloc[0]["chk_card_contract"]


# ───────────────────────── checklist①:数字机检回环通过 ─────────────────────────


def test_chk_numeric_loop_false_when_judged_json_absent(tmp_path):
    scan = _scan_dir(tmp_path)
    _write_finalists(scan, ["300476"])
    _write_card(scan, "300476")
    df = ps.compute_process_scores(scan)
    assert not df.iloc[0]["chk_numeric_loop"]


def test_chk_numeric_loop_true_when_thesis_number_matches_row(tmp_path):
    scan = _scan_dir(tmp_path)
    _write_finalists(scan, ["300476"])
    _write_card(scan, "300476")
    (scan / "_l3_judged.json").write_text(
        '[{"code": "300476", "thesis": "60日涨幅35%远超预期", "catalyst": "Q2财报"}]', encoding="utf-8")
    pd.DataFrame([{"code": "300476", "pct_60d": 35.0}]).to_csv(scan / "L2_gbdt_top200.csv", index=False)
    df = ps.compute_process_scores(scan)
    assert df.iloc[0]["chk_numeric_loop"]


def test_chk_numeric_loop_false_when_thesis_number_unmatched(tmp_path):
    scan = _scan_dir(tmp_path)
    _write_finalists(scan, ["300476"])
    _write_card(scan, "300476")
    (scan / "_l3_judged.json").write_text(
        '[{"code": "300476", "thesis": "预计涨幅58%空间巨大", "catalyst": "无"}]', encoding="utf-8")
    pd.DataFrame([{"code": "300476", "pct_60d": 12.0}]).to_csv(scan / "L2_gbdt_top200.csv", index=False)
    df = ps.compute_process_scores(scan)
    assert not df.iloc[0]["chk_numeric_loop"]


# ────────────── checklist⑥:slim 可用性(结构+内容,体积只兜真垃圾)──────────────
# Wave6 Q6-a:本检查此前自持 `_SLIM_MIN_BYTES = 10*1024` 纯体积门槛 —— 而 GATE3 侧早在
# 2026-07-14 就把体积判据退役了(`l4_card._slim_defect` docstring:药石科技差 16 字节被误杀,
# 「体积只兜真垃圾,不参与数据够不够的判断」)。process_score 留着的是那条已废判据的孤儿副本:
# 07-24 实测 11 份 slim 全在 8.7–10.1KB(表瘦身后新常态)被判 11/11 假阳。
# 改为复用 `_slim_defect` 单一事实源 —— 判据同 GATE3:四个结构锚齐 ∧ Close 是真数值。


def _valid_slim(size_pad: int = 0) -> str:
    """最小可用 slim(四个结构锚 + 真 Close 数值);size_pad 用于凑体积测试。"""
    return ("## Verified market snapshot\n"
            "### Latest verified OHLCV row\n"
            "| Close | 42.15 |\n"
            "## Market context\n"
            "## Fundamentals overview\n" + "x" * size_pad)


def test_chk_slim_size_true_for_structurally_complete_slim(tmp_path):
    """结构齐 + Close 有真值 → 可用。**8.7KB 这种「表瘦身后的正常体积」必须通过**
    (旧 10KB 门槛把 07-24 的 11/11 全判假阳)。"""
    scan = _scan_dir(tmp_path)
    _write_finalists(scan, ["300476"])
    _write_card(scan, "300476")
    (scan.parent.parent / f"300476_{_DATE}_slim.md").write_text(
        _valid_slim(8_700), encoding="utf-8")
    df = ps.compute_process_scores(scan)
    assert df.iloc[0]["chk_slim_size"]


def test_chk_slim_size_false_when_garbage_or_absent(tmp_path):
    """真垃圾仍要逮住:①体积地板以下的空/截断稿 ②无 slim 文件。"""
    scan = _scan_dir(tmp_path)
    _write_finalists(scan, ["300476", "600519"])
    _write_card(scan, "300476")
    _write_card(scan, "600519")
    (scan.parent.parent / f"300476_{_DATE}_slim.md").write_text("x" * 500, encoding="utf-8")
    # 600519 无 slim 文件
    df = ps.compute_process_scores(scan)
    by_code = df.set_index("code")
    assert not by_code.loc["300476", "chk_slim_size"]
    assert not by_code.loc["600519", "chk_slim_size"]


def test_chk_slim_size_false_when_structure_complete_but_no_data(tmp_path):
    """体积够大、结构锚也齐,但 Close 是 NO_DATA 占位 → 不可用。

    这是纯体积门槛**永远逮不到**的一类:降级稿可以很大。改判据后才拦得住。
    """
    scan = _scan_dir(tmp_path)
    _write_finalists(scan, ["300476"])
    _write_card(scan, "300476")
    degraded = ("## Verified market snapshot\n### Latest verified OHLCV row\n"
                "| Close | NO_DATA |\n## Market context\n## Fundamentals overview\n" + "x" * 12000)
    (scan.parent.parent / f"300476_{_DATE}_slim.md").write_text(degraded, encoding="utf-8")
    df = ps.compute_process_scores(scan)
    assert not df.iloc[0]["chk_slim_size"]


def test_chk_slim_size_matches_suffixed_filename(tmp_path):
    """slim 文件名可能带 .SZ/.SH/.SS 后缀(harvest 归一化前遗留),code 前缀匹配须兜住。"""
    scan = _scan_dir(tmp_path)
    _write_finalists(scan, ["300476"])
    _write_card(scan, "300476")
    (scan.parent.parent / f"300476.SZ_{_DATE}_slim.md").write_text(
        _valid_slim(9_000), encoding="utf-8")
    df = ps.compute_process_scores(scan)
    assert df.iloc[0]["chk_slim_size"]


# ───────────────────────── process_score 汇总 + write_process_scores ─────────────────────────


def test_process_score_is_sum_of_true_checks(tmp_path):
    scan = _scan_dir(tmp_path)
    _write_finalists(scan, ["300476"])
    # 默认卡带 进入P4倾向 行(卡片契约 lint 过)+ rubric 一致 + 此处再加 盲读pass 标记 → 3 项 True
    _write_card(scan, "300476", extra="**独立初判**:多头。")
    df = ps.compute_process_scores(scan)
    row = df.iloc[0]
    assert row["process_score"] == sum(1 for c in ps._CHECKS if row[c])
    assert row["process_score"] == 3
    assert row["chk_card_contract"] and row["chk_rubric_consistent"] and row["chk_blind_pass"]


def test_write_process_scores_returns_none_when_empty(tmp_path):
    scan = _scan_dir(tmp_path)
    assert ps.write_process_scores(scan) is None
    assert not (scan / "process_scores.csv").exists()


def test_write_process_scores_writes_csv(tmp_path):
    scan = _scan_dir(tmp_path)
    _write_finalists(scan, ["300476"])
    _write_card(scan, "300476")
    p = ps.write_process_scores(scan)
    assert p == scan / "process_scores.csv"
    assert p.exists()
    df = pd.read_csv(p, dtype={"code": str})
    assert list(df["code"]) == ["300476"]
    assert "process_score" in df.columns
