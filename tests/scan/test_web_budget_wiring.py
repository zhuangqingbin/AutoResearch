"""真值路接线回归(P0·T4)—— 网查预算 lint 必须自己找到本 run 的 capsule 真身。

病灶两条,都是「建好了但没人接线」这一族:

1. `self_review.intel_query_cap_lint(scan_dir, cap, web_budget_path=None)` 早就写好了真值路
   (一行 tool call ≠ 一次查询,capsule 的 `web_budget.json` 才是权威),但**生产从不传路径**
   → 至今仍走稿件自报路,而自报正是 2026-08-26 八稿超限(22–37 条 vs cap 20)没被发现的原因。
2. `stage_rulers.csv` 只有 prelude 里一个包在 `contextlib.suppress` 的生产者;
   `scripts/nightly_close.sh` 跑的是 `populations build`(**不写** stage_rulers,写它的是
   `rulers` 子命令)→ 没扫描的日子四把阶段尺永不更新。

本文件零 LLM 零网络:手搭 run 目录布局(不 import capsule,免得和同批在改 capsule 的人抢),
断言 lint 在**没人给它路径**时自己命中 `capsule/usage/web_budget.json`。
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from autoresearch.scan import self_review

DATE = "2026-08-29"
RUN_ID = "20260829T010203456789Z"
OTHER_RUN_ID = "20260828T010203456789Z"
NIGHTLY = Path(__file__).resolve().parents[2] / "scripts" / "nightly_close.sh"


def _run_layout(tmp_path: Path, run_id: str = RUN_ID) -> tuple[Path, Path]:
    """生产布局:`context_<engine>/scan_runs/<run_id>/{staging/<date>,capsule}`。"""
    run_root = tmp_path / "context_claude" / "scan_runs" / run_id
    scan_dir = run_root / "staging" / DATE
    scan_dir.mkdir(parents=True, exist_ok=True)
    return run_root, scan_dir


def _intel(scan_dir: Path, claims: dict[str, int]) -> Path:
    for code, n in claims.items():
        (scan_dir / f"_l4_intel_{code}.md").write_text(
            f"# {code} 情报\n\n声明:网查 {n} 条 · https://example.com/{code}\n",
            encoding="utf-8")
    return scan_dir


def _echo(scan_dir: Path, cap: int = 20) -> Path:
    """`product_shape_lint` 的 cap 取当日 echo(缺则回落 agent def 默认 15)。"""
    p = scan_dir / "user_config_echo.json"
    p.write_text(json.dumps({"l4_intel": {"max_queries": cap}}), encoding="utf-8")
    return p


def _budget(path: Path, **over) -> Path:
    obj = {"measurement": "MEASURED", "tool_calls": 9, "search_queries": 26,
           "fetched_urls": 14, "failed_units": 0, "duplicate_urls": 2, "wall_s": 41.2,
           "cap_unit": "search_queries", "cap": 20,
           "cap_enforcement": "OBSERVED_ONLY", "self_report_delta": None}
    obj.update(over)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, ensure_ascii=False), encoding="utf-8")
    return path


# ────────────────────────── 半 1:真值路自动定位 ──────────────────────────

def test_default_path_is_exactly_capsule_usage_web_budget(tmp_path):
    """路径逐字钉死:T4 消费的就是 T1 生产的那个名字,谁改名谁当场红。"""
    run_root, scan_dir = _run_layout(tmp_path)
    written = _budget(run_root / "capsule" / "usage" / "web_budget.json")
    got = self_review._default_web_budget_path(scan_dir)
    assert got is not None
    assert Path(got) == written
    assert Path(got).relative_to(run_root).as_posix() == "capsule/usage/web_budget.json"


def test_lint_prefers_capsule_truth_when_no_path_is_handed_in(tmp_path):
    """有 capsule 真值就不许再信稿件自报 —— 自报是 08-26 超限没被发现的原因。

    自报两稿各 9 条(都不超 cap 20),真值 26 条(超)。走自报路 → `[]`(病灶原状);
    走真值路 → 一条 run 级 `measured`。
    """
    run_root, scan_dir = _run_layout(tmp_path)
    _intel(scan_dir, {"000001": 9, "000002": 9})
    _budget(run_root / "capsule" / "usage" / "web_budget.json")

    rows = self_review.intel_query_cap_lint(scan_dir, cap=20)     # 注意:不显式传 path
    assert [r["kind"] for r in rows] == ["measured"]
    assert rows[0]["measurement"] == "MEASURED"
    assert rows[0]["used"] == 26 and rows[0]["cap"] == 20
    assert rows[0]["unit"] == "search_queries"
    assert rows[0]["code"] is None                                 # run 级,不栽给任何一只
    assert rows[0]["self_report_total"] == 18                      # 自报降级为诊断项


def test_lint_falls_back_to_self_report_without_capsule(tmp_path):
    """没有 capsule → 旧自报路**逐字节**不变:三键行,一个字段都不多。"""
    _, scan_dir = _run_layout(tmp_path)
    _intel(scan_dir, {"000001": 22, "000002": 9})
    assert self_review._default_web_budget_path(scan_dir) is None
    assert self_review.intel_query_cap_lint(scan_dir, cap=20) == [
        {"code": "000001", "claimed": 22, "cap": 20}]


def test_explicit_path_still_wins_over_the_default(tmp_path):
    """签名不变:显式传路径仍优先(capsule 说超限,显式的说没超 → 听显式的)。"""
    run_root, scan_dir = _run_layout(tmp_path)
    _intel(scan_dir, {"000001": 31})
    _budget(run_root / "capsule" / "usage" / "web_budget.json")     # 真值 26 > 20
    explicit = _budget(tmp_path / "explicit_budget.json", search_queries=3)
    assert self_review.intel_query_cap_lint(
        scan_dir, cap=20, web_budget_path=explicit) == []


def test_default_path_never_cross_reads_another_run(tmp_path, monkeypatch):
    """只认入参 scan_dir 反解出的那趟 —— 环境里挂着别的 run 也不许串读它的预算。"""
    _, scan_dir = _run_layout(tmp_path)
    _intel(scan_dir, {"000001": 22})
    other_root = tmp_path / "context_claude" / "scan_runs" / OTHER_RUN_ID
    _budget(other_root / "capsule" / "usage" / "web_budget.json")
    monkeypatch.setenv("AUTORESEARCH_RUN_ID", OTHER_RUN_ID)

    assert self_review._default_web_budget_path(scan_dir) is None
    assert self_review.intel_query_cap_lint(scan_dir, cap=20) == [
        {"code": "000001", "claimed": 22, "cap": 20}]


def test_default_path_also_finds_the_materializer_lineage_layout(tmp_path):
    """`web_budget.materialize_web_budget` 今天落的是 `capsule/lineage/`(BUDGET_NAME)。

    探错目录 = 真值路又变成「接好了但永远走不到」,那正是本次要修的病,所以两个都探。
    """
    run_root, scan_dir = _run_layout(tmp_path)
    _intel(scan_dir, {"000001": 9})
    _budget(run_root / "capsule" / "lineage" / "web_budget.json")
    rows = self_review.intel_query_cap_lint(scan_dir, cap=20)
    assert [r["kind"] for r in rows] == ["measured"] and rows[0]["used"] == 26


def test_product_shape_lint_reaches_the_truth_path_unassisted(tmp_path):
    """生产真身:`report_sections` 调 `product_shape_lint(scan_dir, scan_dir.name)`,不传路径。

    接线断在这一层就等于没修(FN-1 家训:生产者没接线 = 死了也像活着)。
    """
    run_root, scan_dir = _run_layout(tmp_path)
    _intel(scan_dir, {"000001": 9, "000002": 9})                   # 自报都不超
    _echo(scan_dir, 20)
    _budget(run_root / "capsule" / "usage" / "web_budget.json")     # 真值超
    hits = [r for r in self_review.product_shape_lint(scan_dir, DATE)
            if r["check"] == "产物形状·intel限频"]
    assert len(hits) == 1
    assert "真值 26 search_queries > cap 20" in hits[0]["detail"]
    assert "web_budget.json" in hits[0]["detail"]


def test_product_shape_lint_without_capsule_keeps_the_self_report_wording(tmp_path):
    """对照臂:没有 capsule 时逐字保留旧措辞(历史 run 复盘文案不突变)。"""
    _, scan_dir = _run_layout(tmp_path)
    _intel(scan_dir, {"000001": 31})
    _echo(scan_dir, 20)
    hits = [r for r in self_review.product_shape_lint(scan_dir, DATE)
            if r["check"] == "产物形状·intel限频"]
    assert len(hits) == 1 and hits[0]["code"] == "000001"
    assert "自报 31 条 > cap 20" in hits[0]["detail"]


# ────────────────────────── 半 2:stage_rulers 进夜跑 ──────────────────────────

def _nightly_text() -> str:
    return NIGHTLY.read_text(encoding="utf-8")


def _nightly_steps(text: str) -> list[str]:
    return re.findall(r'^step "([^"]+)"', text, re.M)


def test_nightly_close_refreshes_stage_rulers(tmp_path):
    """`populations build` 不写 stage_rulers.csv —— 写它的是 `rulers` 子命令。"""
    text = _nightly_text()
    assert _nightly_steps(text) == [
        "outcome fill", "ledger_views build", "populations build",
        "populations rulers", "analyze ledger"]
    assert re.search(
        r'^step "populations rulers"\s+autoresearch\.scan\.populations\s+rulers\s*$',
        text, re.M)


def test_nightly_close_step5_invokes_analyze_ledger_nightly():
    """第 5 步(D5.1)= `analyze.ledger nightly`(stock-research 独立产物结果账本,
    ingest + fill 连跑;只记不学,不回注任何 prompt/权重)。"""
    text = _nightly_text()
    m = re.search(r'^step "analyze ledger"\s+(\S+)\s+(\S+)\s+(\S.*)$', text, re.M)
    assert m is not None
    assert m.group(1) == "autoresearch.analyze.ledger"
    assert m.group(2) == "nightly"
    assert "--today" in m.group(3)


def test_nightly_close_header_comment_matches_the_real_steps():
    """注释说「两步」却列三条 = 本仓反复复发的漂移;把注释与真实 step 行钉在一起。"""
    text = _nightly_text()
    steps = _nightly_steps(text)
    header = text.split("set -u", 1)[0]
    listed = re.findall(r"^#\s+\d+\.\s+(.+?)\s+——", header, re.M)
    assert listed == steps
    assert f"{'一两三四五六'[len(steps) - 1]}步" in header
