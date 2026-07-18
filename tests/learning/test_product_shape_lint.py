"""product_shape_lint(线 C·产物形状六探针)——合成 staging fixture,零 LLM 零网络。

覆盖:六条各自触发 + 干净目录零输出 + 空/坏目录不炸(presence-gated,绝不抛异常)。
判据镜像生产:pinned 身份只认 finalists.csv 的 lane(judged 存原召回 lane);
force_full 走 l4_card.force_full_card 真身(finalists conviction/lane + L2 n_channels)。
"""
from __future__ import annotations

import json

import pandas as pd

from autoresearch.learning.self_review import product_shape_lint

DATE = "2026-07-17"


def _by(rows: list[dict], check: str) -> list[dict]:
    return [r for r in rows if r["check"] == check]


def _write_health(d, *, n_full=1, anns=0.2):
    (d / "run_health.json").write_text(json.dumps(
        {"anns_empty_rate": anns, "l4_phases": {"n_cards": 2, "n_earlystop": 1, "n_full": n_full}}),
        encoding="utf-8")


def _mk_clean(tmp_path):
    """干净全量 staging:1 真选 + 1 保送;intel 2 份(=全 finalist 行 2 − 复用 0;保送也派)带 URL;judged 全字段非空。"""
    d = tmp_path / DATE
    (d / "details").mkdir(parents=True)
    pd.DataFrame([
        {"code": "600285", "name": "羚锐制药", "conviction": 70, "lane": "healthy"},
        {"code": "600519", "name": "保送票", "conviction": 50, "lane": "pinned"},
    ]).to_csv(d / "finalists.csv", index=False)
    (d / "_l3_judged.json").write_text(json.dumps([
        # 保送票在 judged 里存的是原召回 lane(trend)——pinned 身份只在 finalists.csv
        {"code": "600519", "name": "保送票", "lane": "trend", "finalist": False,
         "thesis": "持仓否决理由完整", "risk": "有风险段", "catalyst": "有催化段"},
        {"code": "600285", "name": "羚锐制药", "lane": "healthy", "finalist": True,
         "thesis": "真选论点", "risk": "r", "catalyst": "c"},
    ], ensure_ascii=False), encoding="utf-8")
    _write_health(d)
    (d / "_l4_intel_600285.md").write_text(
        "# 活体情报\n## 事件段\n| 2026-07-16 | 事件 | 源 https://example.com/a | 是 | +1 |\n",
        encoding="utf-8")
    (d / "_l4_intel_600519.md").write_text(          # 保送票同走 l4-stock 链 → 也有 intel 稿
        "# 活体情报\n## 事件段\n(近 14 天无重大事件)源 https://example.com/b\n",
        encoding="utf-8")
    (d / "market_pack.json").write_text(json.dumps(
        {"sector_healthy_top3": [{"industry": "动物保健Ⅱ"}, {"industry": "商用车"}]},
        ensure_ascii=False), encoding="utf-8")
    (d / "market_view.md").write_text("# 市场研判\n宽度极窄,防守为主。\n", encoding="utf-8")
    (d / "details" / "600285.md").write_text("〔卡契约 v3〕满卡", encoding="utf-8")
    (d / "details" / "600519.md").write_text("〔卡契约 v3〕保送满卡", encoding="utf-8")
    return d


# ── 基线:干净零输出 / 空目录不炸 ──────────────────────────────────────────

def test_clean_dir_zero_output(tmp_path):
    assert product_shape_lint(_mk_clean(tmp_path), DATE) == []


def test_empty_and_missing_dir_no_crash(tmp_path):
    assert product_shape_lint(tmp_path, DATE) == []                      # 空目录
    assert product_shape_lint(tmp_path / "nope" / DATE, DATE) == []      # 不存在的目录


def test_corrupt_files_silent(tmp_path):
    d = tmp_path / DATE
    d.mkdir()
    (d / "finalists.csv").write_text("\x00\x01garbage", encoding="utf-8")
    (d / "_l3_judged.json").write_text("{not json", encoding="utf-8")
    (d / "run_health.json").write_text("[]", encoding="utf-8")           # 非 dict
    (d / "market_pack.json").write_text("{bad", encoding="utf-8")
    (d / "market_view.md").write_text("x", encoding="utf-8")
    out = product_shape_lint(d, DATE)                                    # 绝不抛异常
    assert isinstance(out, list) and out == []


# ── 1) 保送§2非空 ──────────────────────────────────────────────────────────

def test_pinned_empty_thesis_warn(tmp_path):
    d = _mk_clean(tmp_path)
    (d / "_l3_judged.json").write_text(json.dumps([
        {"code": "600519", "lane": "trend", "thesis": "", "risk": " ", "catalyst": "有"},
        {"code": "600285", "thesis": "真选论点", "risk": "r", "catalyst": "c"},
    ], ensure_ascii=False), encoding="utf-8")
    rows = _by(product_shape_lint(d, DATE), "产物形状·保送§2空")
    assert len(rows) == 1 and rows[0]["code"] == "600519"
    assert rows[0]["severity"] == "warn"
    assert "thesis" in rows[0]["detail"] and "risk" in rows[0]["detail"]
    assert "catalyst" not in rows[0]["detail"]                           # 非空键不点名


def test_pinned_missing_entry_warn_and_truepick_exempt(tmp_path):
    d = _mk_clean(tmp_path)
    # judged 只剩真选(且真选 thesis 空也不查——本条只盯保送);保送票整条缺 → warn
    (d / "_l3_judged.json").write_text(json.dumps(
        [{"code": "600285", "thesis": "", "risk": "", "catalyst": ""}]), encoding="utf-8")
    rows = _by(product_shape_lint(d, DATE), "产物形状·保送§2空")
    assert len(rows) == 1 and rows[0]["code"] == "600519" and "无条目" in rows[0]["detail"]
    # judged 文件整个缺 → presence-gated,本条静默
    (d / "_l3_judged.json").unlink()
    assert _by(product_shape_lint(d, DATE), "产物形状·保送§2空") == []


# ── 2) force_full 探针 ────────────────────────────────────────────────────

def test_force_full_silent_warn(tmp_path):
    d = _mk_clean(tmp_path)
    _write_health(d, n_full=0)                       # 保送票恒命中 force_full,但 n_full=0
    rows = _by(product_shape_lint(d, DATE), "产物形状·force_full未生效")
    assert len(rows) == 1 and rows[0]["severity"] == "warn"
    assert "600519" in rows[0]["detail"] and "n_full=0" in rows[0]["detail"]


def test_force_full_channels_path_via_l2(tmp_path):
    d = _mk_clean(tmp_path)                          # 去掉保送票 → 只剩强先验通路②
    pd.DataFrame([{"code": "600285", "name": "羚锐制药", "conviction": 72, "lane": "healthy"}],
                 ).to_csv(d / "finalists.csv", index=False)
    pd.DataFrame([{"code": "600285", "n_channels": 5, "l2_lane_reserved": False}],
                 ).to_csv(d / "L2_gbdt_top200.csv", index=False)
    _write_health(d, n_full=0)
    rows = _by(product_shape_lint(d, DATE), "产物形状·force_full未生效")
    assert len(rows) == 1 and "600285" in rows[0]["detail"]


def test_force_full_zero_hits_info(tmp_path):
    d = _mk_clean(tmp_path)                          # 无保送、conviction<70 → 0 命中
    pd.DataFrame([{"code": "600285", "name": "羚锐制药", "conviction": 55, "lane": "healthy"}],
                 ).to_csv(d / "finalists.csv", index=False)
    out = product_shape_lint(d, DATE)
    rows = _by(out, "产物形状·force_full零命中")
    assert len(rows) == 1 and rows[0]["severity"] == "info" and DATE in rows[0]["detail"]
    assert _by(out, "产物形状·force_full未生效") == []


def test_force_full_reused_card_not_counted(tmp_path):
    d = _mk_clean(tmp_path)                          # 唯一命中票(保送)是 ♻️ 复用卡 → 派发时
    (d / "details" / "600519.md").write_text(        # 已跳过,不算命中 → 0 命中走 info
        "♻️ **复用卡**(源 2026-07-15)\n〔卡契约 v3〕", encoding="utf-8")
    _write_health(d, n_full=0)
    out = product_shape_lint(d, DATE)
    assert _by(out, "产物形状·force_full未生效") == []
    assert len(_by(out, "产物形状·force_full零命中")) == 1


# ── 3) intel 稿数 ─────────────────────────────────────────────────────────

def test_intel_count_mismatch_warn(tmp_path):
    d = _mk_clean(tmp_path)                          # 全 finalist 行 2 − 复用 0 = 期望 2,实际 3(多出非名单稿)
    (d / "_l4_intel_000002.md").write_text("不在 finalists 的稿 https://x.com/1\n", encoding="utf-8")
    rows = _by(product_shape_lint(d, DATE), "产物形状·intel稿数不符")
    assert len(rows) == 1 and rows[0]["severity"] == "warn"
    assert "3 份" in rows[0]["detail"] and "期望 2" in rows[0]["detail"]
    # 少派同样逮:删掉保送稿 → 1 份 ≠ 期望 2
    (d / "_l4_intel_000002.md").unlink()
    (d / "_l4_intel_600519.md").unlink()
    rows2 = _by(product_shape_lint(d, DATE), "产物形状·intel稿数不符")
    assert len(rows2) == 1 and "1 份" in rows2[0]["detail"] and "期望 2" in rows2[0]["detail"]


def test_intel_count_reuse_subtracted(tmp_path):
    d = _mk_clean(tmp_path)                          # 3 行(含保送)、其一 ♻️ 复用 → 期望 2 = 实际 2
    pd.DataFrame([
        {"code": "600285", "name": "甲", "conviction": 70, "lane": "healthy"},
        {"code": "000001", "name": "乙", "conviction": 60, "lane": "value"},
        {"code": "600519", "name": "保", "conviction": 50, "lane": "pinned"},
    ]).to_csv(d / "finalists.csv", index=False)
    (d / "details" / "000001.md").write_text("♻️ **复用卡**(源 2026-07-16)", encoding="utf-8")
    assert _by(product_shape_lint(d, DATE), "产物形状·intel稿数不符") == []
    # `_probe` 变体不计入稿数(600285 正稿 + probe 变体 → 仍 1 份)
    (d / "_l4_intel_600285_probe.md").write_text("变体稿 https://x.com/p", encoding="utf-8")
    assert _by(product_shape_lint(d, DATE), "产物形状·intel稿数不符") == []


def test_intel_disabled_no_output(tmp_path):
    d = _mk_clean(tmp_path)
    (d / "_l4_intel_600285.md").unlink()             # 0 份 intel = 未启用 → 本条不出
    (d / "_l4_intel_600519.md").unlink()
    assert _by(product_shape_lint(d, DATE), "产物形状·intel稿数不符") == []


# ── 4) anns 去伪 ──────────────────────────────────────────────────────────

def test_anns_expected_info(tmp_path):
    d = _mk_clean(tmp_path)
    _write_health(d, anns=1.0)
    rows = _by(product_shape_lint(d, DATE), "产物形状·anns去伪")
    assert len(rows) == 1 and rows[0]["severity"] == "info"
    assert "no-permission" in rows[0]["detail"]


# ── 5) market_view 防锚定 ─────────────────────────────────────────────────

def test_market_view_anchor_leak_warn(tmp_path):
    d = _mk_clean(tmp_path)
    (d / "market_view.md").write_text("# 市场研判\n看多**动物保健Ⅱ**,逢低配置。\n",
                                      encoding="utf-8")
    rows = _by(product_shape_lint(d, DATE), "产物形状·market_view防锚定")
    assert len(rows) == 1 and rows[0]["severity"] == "warn"
    assert "动物保健Ⅱ" in rows[0]["detail"] and "商用车" not in rows[0]["detail"]


# ── 6) intel 零URL ────────────────────────────────────────────────────────

def test_intel_zero_url_warn(tmp_path):
    d = _mk_clean(tmp_path)
    (d / "_l4_intel_600285.md").write_text("# 活体情报\n## 事件段\n(无链接)\n", encoding="utf-8")
    rows = _by(product_shape_lint(d, DATE), "产物形状·intel零URL")
    assert len(rows) == 1 and rows[0]["severity"] == "warn" and rows[0]["code"] == "600285"
    # 修回带 URL → 清零
    (d / "_l4_intel_600285.md").write_text("源 http://example.com/a\n", encoding="utf-8")
    assert _by(product_shape_lint(d, DATE), "产物形状·intel零URL") == []


def test_return_row_shape(tmp_path):
    d = _mk_clean(tmp_path)
    _write_health(d, anns=1.0)
    rows = product_shape_lint(d, DATE)
    assert rows and all(set(r) == {"check", "severity", "detail", "code"} for r in rows)
