"""买单 ensemble(B10 集成配方)—— ≥OW 追加 2 独立 run 取中位,只向下折回。NO network.

design: .superpowers/sdd/task-11-brief.md。workflow(scan-market.js L4 段)对 ≥OW 的**新派**卡各追加
2 个独立 l4-card run,连同卡面原评级取中位,写 `context/scan/<date>/_ensemble.json`
`[{"code","ratings":[...],"median":...,"spread":int}]`。assemble 读它:
  - `_load_ensemble` 解析 → {code: {...}};无文件 → {}(presence-gated,老路不破)。
  - `_apply_ensemble_fold` 只向下折回(median 档比卡面档更差才改;median 更好/持平 → 原样不动)。
  - spread≥2 → buy-list 表该行加 🎭复核分歧 badge + 组合视角节加一行人裁提示。
"""
from __future__ import annotations

import json

from autoresearch.scan import assemble

_DATE = "2026-07-11"


def test_ensemble_flag_degraded_one_tier_dissent():
    """N=2 退化(某复核 run 失败)+ 仅 1 档分歧 → 必须亮 🎭(T9-11 review Important#2:
    修复前该分歧被静默吞掉);无分歧退化不误报;正常 N=3 仍按 spread≥2。"""
    assert assemble._ensemble_flag({"spread": 1, "degraded": True}) is True
    assert assemble._ensemble_flag({"spread": 0, "degraded": True}) is False
    assert assemble._ensemble_flag({"spread": 1, "degraded": False}) is False
    assert assemble._ensemble_flag({"spread": 2}) is True
    assert assemble._ensemble_flag(None) is False


def test_load_and_fold(tmp_path):
    (tmp_path / "_ensemble.json").write_text(json.dumps([
        {"code": "688213", "ratings": ["Overweight", "Hold", "Hold"], "median": "Hold", "spread": 1}]),
        encoding="utf-8")
    ens = assemble._load_ensemble(tmp_path)
    assert assemble._apply_ensemble_fold("Overweight", ens.get("688213")) == "Hold"
    assert assemble._apply_ensemble_fold("Hold", ens.get("688213")) == "Hold"      # 不向上
    assert assemble._apply_ensemble_fold("Overweight", None) == "Overweight"      # 无记录 = 原样


def test_load_ensemble_merges_per_code_files(tmp_path):
    """fb_20260714_003(每股独立 l4-stock workflow):每股各写 `_ensemble_<code>.json`
    (单条 record,无共享文件写竞态),_load_ensemble 与旧批量 `_ensemble.json` 合并读,
    同 code 时 per-code 文件覆盖旧批量。坏 per-code json 跳过不挡其余。"""
    (tmp_path / "_ensemble.json").write_text(json.dumps([
        {"code": "688213", "ratings": ["Overweight", "Hold", "Hold"], "median": "Hold", "spread": 1}]),
        encoding="utf-8")
    (tmp_path / "_ensemble_000651.json").write_text(json.dumps(
        {"code": "000651", "ratings": ["Overweight", "Overweight", "Overweight"],
         "median": "Overweight", "spread": 0, "degraded": False}), encoding="utf-8")
    (tmp_path / "_ensemble_688213.json").write_text(json.dumps(
        {"code": "688213", "ratings": ["Overweight", "Sell", "Sell"], "median": "Sell", "spread": 3}),
        encoding="utf-8")
    (tmp_path / "_ensemble_bad.json").write_text("{oops", encoding="utf-8")
    ens = assemble._load_ensemble(tmp_path)
    assert ens["000651"]["median"] == "Overweight"          # per-code 新路
    assert ens["688213"]["median"] == "Sell"                # per-code 覆盖旧批量
    assert len(ens) == 2                                     # 坏 json 跳过


def test_load_ensemble_missing_file_is_empty(tmp_path):
    """无 _ensemble.json → {}(presence-gated,老路不破;非报错/非阻断)。"""
    assert assemble._load_ensemble(tmp_path) == {}


def test_load_ensemble_bad_json_is_empty(tmp_path):
    """坏 json → {}(可选层不挡整份报告发布,同 pinned.json 惯例)。"""
    (tmp_path / "_ensemble.json").write_text("{not json", encoding="utf-8")
    assert assemble._load_ensemble(tmp_path) == {}


def test_fold_never_upgrades():
    """median 比卡面档更好(更靠 Buy)→ 原样不动(只向下,与早停只向下同族)。"""
    rec = {"median": "Overweight", "ratings": ["Underweight", "Overweight", "Overweight"], "spread": 2}
    assert assemble._apply_ensemble_fold("Underweight", rec) == "Underweight"


def test_fold_ignores_unknown_ratings():
    """median/rating 不在五档词表(脏数据)→ 原样不动,不报错。"""
    assert assemble._apply_ensemble_fold("Overweight", {"median": "???"}) == "Overweight"


# ── Wave6 T2:同档早止(run1==run2 → 中位数学上已定,跳过 run3)────────────────


def test_early_stopped_ensemble_still_folds():
    """早止 ≠ degraded —— 这是 T2 最容易写反的一处,钉死它。

    `degraded` 语义 = **复核 run 失败**(→ 禁折回,交人裁);早止是**主动省跑**
    (两票已定中位 → 必须照常折回)。若把 earlyStopped 并进 degraded,SELL 复核会在
    最该救回误卖持仓的时候不折。
    """
    rec = {"code": "300857", "ratings": ["Sell", "Underweight"], "median": "Underweight",
           "spread": 1, "degraded": False, "trigger": "sell_review",
           "n_runs": 2, "early_stopped": True}
    assert assemble._apply_ensemble_fold("Sell", rec) == "Underweight"


def test_early_stopped_two_same_votes_raise_no_dissent():
    """早止的 spread=0 不触发人裁 —— 两票确实同档,没有分歧可报。

    记录这条推理:早止**不会**制造「三人一致」的假声称 —— dissent 行只在 flag 为真时
    渲染,且它打印 len(ratings) = 真实跑数(2),不会把 2 跑说成 3 跑。
    """
    early = {"code": "601869", "ratings": ["Underweight", "Underweight"],
             "median": "Underweight", "spread": 0, "degraded": False,
             "trigger": "sell_review", "n_runs": 2, "early_stopped": True}
    assert assemble._ensemble_flag(early) is False


def test_median_of_two_same_tier_equals_that_tier():
    """T2 的数学前提:两票同档时,第三票无论投什么,三票排序中位恒为该档。

    这条是「跳过 run3 结果逐字节相同」的依据 —— 前提塌了整个优化就不成立,所以钉死它。
    """
    rank = {"Sell": 0, "Underweight": 1, "Hold": 2, "Overweight": 3, "Buy": 4}
    for third in rank:
        trio = sorted([rank["Underweight"], rank["Underweight"], rank[third]])
        assert trio[len(trio) // 2] == rank["Underweight"], f"第三票 {third} 时中位变了"


# ───────────────────────── build_summary 集成:badge + 组合视角人裁行 ─────────────────────────


def _build_min_scan_dir(root, rating="Overweight"):
    """最小可跑 scan dir:1 只 finalist + 1 张决策卡(build_summary 所需的最小面)。"""
    scan = root / "context/scan" / _DATE
    (scan / "details").mkdir(parents=True)
    (scan / "meta.json").write_text(json.dumps({"universe": 100}), encoding="utf-8")
    import csv
    with (scan / "finalists.csv").open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["ticker", "code", "name", "sector", "conviction",
                                          "thesis", "risk", "catalyst"])
        w.writeheader()
        w.writerow({"ticker": "688213", "code": "688213", "name": "测试股", "sector": "半导体",
                    "conviction": "150", "thesis": "测试论点", "risk": "测试风险", "catalyst": "测试催化"})
    (scan / "details" / "688213.md").write_text(
        "# 决策卡\n## 决策仪表盘\n| 评级 | 现价 | EV目标 | R:R | 置信度 |\n|---|---|---|---|---|\n"
        f"| **{rating}** | 100元 | 130元(+30%) | 2.1:1 | 中 |\n\n"
        f"**Rating**: {rating}\n\nFINAL TRANSACTION PROPOSAL: **BUY**\n", encoding="utf-8")
    return scan


def test_summary_badge_and_dissent_line_on_high_spread(tmp_path):
    scan = _build_min_scan_dir(tmp_path, rating="Overweight")
    (scan / "_ensemble.json").write_text(json.dumps([
        {"code": "688213", "ratings": ["Overweight", "Hold", "Underweight"], "median": "Hold", "spread": 2}]),
        encoding="utf-8")
    md = assemble.build_summary(scan, _DATE, "0930", "20260711_0930")
    assert "🎭复核分歧" in md, "spread≥2 该行应有 🎭复核分歧 badge"
    assert "🎭 买单复核分歧:688213" in md, "组合视角节应有人裁提示行"
    assert "已按中位折回,建议人工复核" in md
    assert "**Hold** 🎭复核分歧" in md, "评级应已折回到中位 Hold(只向下)"


def test_summary_no_badge_on_low_spread(tmp_path):
    scan = _build_min_scan_dir(tmp_path, rating="Overweight")
    (scan / "_ensemble.json").write_text(json.dumps([
        {"code": "688213", "ratings": ["Overweight", "Overweight", "Hold"], "median": "Overweight", "spread": 1}]),
        encoding="utf-8")
    md = assemble.build_summary(scan, _DATE, "0930", "20260711_0930")
    assert "🎭复核分歧" not in md, "spread<2 不该有分歧 badge"
    assert "🎭 买单复核分歧" not in md
    assert "**Overweight**" in md, "median==卡面档(同为 Overweight tier)→ 不改"


def test_summary_parity_without_ensemble_file(tmp_path):
    """无 _ensemble.json → 一切照旧:无 🎭 badge/节,评级不变(parity)。"""
    scan = _build_min_scan_dir(tmp_path, rating="Overweight")
    md = assemble.build_summary(scan, _DATE, "0930", "20260711_0930")
    assert "🎭" not in md
    assert "**Overweight**" in md
