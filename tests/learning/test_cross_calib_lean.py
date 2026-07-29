"""cross_calib 的 lean 口径:双词表 + OW-lean 确认率(Wave8 W8-12)。

**动机(2026-07-28 首航)**:L3 给 6 只 OW-lean,L4 深核后 **0 只确认**(4 降一档、
2 降两档),而 6 只全在 `healthy` lane。但注入给 L3 的 🔁 校准行只报 `flip_rate`
(高确信 conviction≥70 被压 ≤UW 的比例)—— 今晚只有 601288(conv 72)进那个分母,
且它拿到 Hold 不算 flip。**L3 看不到自己 lean 层面的前科。**

**顺带逮到的潜伏 bug**:`triage_lean` 历史上有两套词表 ——
- 中文期(06-18 ~ 07-03,12 日):看多 / 中性 / 回避 / 中性偏多 / 看多偏谨慎
- 英文期(07-06 起,13 日):OW / Hold / UW

而 `triage_hit` 列只匹配 `== "回避"`,对**整个英文期全瞎**(07-28 的 healthy lane
`triage_n=0`,尽管当天有 6 个 OW)。30 日窗口现在以英文期为主 —— 这条读数早就废了,
只是没人对账。同族:「旗在但消费方不认识它」。
"""

from __future__ import annotations

import pandas as pd
import pytest

from autoresearch.learning.cross_calib import flip_stats, suggestion_lines


def _day(root, name: str, rows: list[dict]):
    d = root / name
    d.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(rows).to_csv(d / "L3_judged_full.csv", index=False)
    return d


def _ratings(monkeypatch, mapping: dict[str, dict[str, str]]):
    """按日 stub `final_ratings`(cross_calib 内 lazy import,patch 到源模块)。"""
    import autoresearch.scan.health as health
    monkeypatch.setattr(health, "final_ratings", lambda d: mapping.get(d.name, {}))


def test_avoid_lean_recognises_both_vocabularies(tmp_path, monkeypatch):
    """`回避`(中文期)与 `UW`(英文期)都要算进「回避 lean」分母。"""
    _day(tmp_path, "2026-06-20", [
        {"code": "000001", "lane": "trend", "conviction": 50, "triage_lean": "回避"},
    ])
    _day(tmp_path, "2026-07-20", [
        {"code": "000002", "lane": "trend", "conviction": 50, "triage_lean": "UW"},
    ])
    _ratings(monkeypatch, {"2026-06-20": {"000001": "Sell"},
                           "2026-07-20": {"000002": "Sell"}})

    row = flip_stats(tmp_path).set_index("lane").loc["trend"]

    assert row["triage_n"] == 2, (
        "英文期的 UP 被漏掉了 —— triage_hit 只认『回避』,对 07-06 起的全部日子瞎"
    )
    assert row["triage_hit"] == 1.0


def test_bullish_lean_confirm_rate(tmp_path, monkeypatch):
    """OW-lean 的确认率:L4 终评 ≥Overweight 才算确认。"""
    _day(tmp_path, "2026-07-28", [
        {"code": "601288", "lane": "healthy", "conviction": 72, "triage_lean": "OW"},
        {"code": "600018", "lane": "healthy", "conviction": 69, "triage_lean": "OW"},
        {"code": "600267", "lane": "healthy", "conviction": 66, "triage_lean": "OW"},
        {"code": "600377", "lane": "value", "conviction": 58, "triage_lean": "Hold"},
    ])
    _ratings(monkeypatch, {"2026-07-28": {
        "601288": "Hold", "600018": "Hold", "600267": "Underweight", "600377": "Hold"}})

    df = flip_stats(tmp_path, shrink=False).set_index("lane")

    assert df.loc["healthy", "lean_n"] == 3
    assert df.loc["healthy", "lean_confirm"] == 0
    assert df.loc["healthy", "lean_confirm_rate"] == 0.0, "07-28 实况:healthy OW-lean 0/3 确认"
    assert pd.isna(df.loc["value", "lean_confirm_rate"]), "无 OW-lean 的 lane 不渲染该列"


def test_bullish_lean_counts_chinese_vocabulary(tmp_path, monkeypatch):
    _day(tmp_path, "2026-06-20", [
        {"code": "000001", "lane": "trend", "conviction": 60, "triage_lean": "看多"},
        {"code": "000002", "lane": "trend", "conviction": 60, "triage_lean": "OW"},
    ])
    _ratings(monkeypatch, {"2026-06-20": {"000001": "Overweight", "000002": "Hold"}})

    row = flip_stats(tmp_path, shrink=False).set_index("lane").loc["trend"]

    assert row["lean_n"] == 2 and row["lean_confirm"] == 1


def test_lean_rate_respects_min_n_inject(tmp_path, monkeypatch):
    """n<3 绝对禁注 —— 与 flip_rate 同一条底线。"""
    _day(tmp_path, "2026-07-28", [
        {"code": "601288", "lane": "healthy", "conviction": 72, "triage_lean": "OW"},
        {"code": "600018", "lane": "healthy", "conviction": 69, "triage_lean": "OW"},
    ])
    _ratings(monkeypatch, {"2026-07-28": {"601288": "Hold", "600018": "Hold"}})

    row = flip_stats(tmp_path).set_index("lane").loc["healthy"]

    assert row["lean_n"] == 2
    assert pd.isna(row["lean_confirm_rate"]), "n=2 <3 必须禁注"


def test_suggestion_emits_lean_line_when_base_discriminates(tmp_path, monkeypatch):
    """基准够高时才出指控行,且必须把基准并排写上。"""
    rows, ratings = [], {}
    for i in range(6):                       # healthy:6 只 OW-lean,0 确认
        rows.append({"code": f"60000{i}", "lane": "healthy", "conviction": 60, "triage_lean": "OW"})
        ratings[f"60000{i}"] = "Hold"
    for i in range(6):                       # trend:6 只 OW-lean,4 确认 → 池化基准 4/12=33%
        rows.append({"code": f"30000{i}", "lane": "trend", "conviction": 60, "triage_lean": "OW"})
        ratings[f"30000{i}"] = "Overweight" if i < 4 else "Hold"
    _day(tmp_path, "2026-07-28", rows)
    _ratings(monkeypatch, {"2026-07-28": ratings})

    lines = suggestion_lines(flip_stats(tmp_path, shrink=False), pd.DataFrame())

    lean = [ln for ln in lines if "OW-lean" in ln]
    assert len(lean) == 1, f"应恰有一条 lean 行:{lines}"
    assert "healthy" in lean[0], "该挑最差的 lane"
    assert "基准" in lean[0], "必须并排给出全 lane 基准,否则读者会误读成 lane 特有缺陷"
    assert len([ln for ln in lines if ln.startswith("🔁")]) <= 2, "🔁 最多两行"


def test_suggestion_suppresses_lean_indictment_when_base_is_degenerate(tmp_path, monkeypatch):
    """**核心保护**:≥Overweight 本身极稀缺时,低确认率不构成对某条 lane 的指控。

    2026-07-29 实测:全库 496 个终评里 ≥Overweight 仅 9 个(1.8%)。此时「healthy
    确认率 0%」几乎只是在复述「L4 极少给 Overweight」——照它注入会推着 L3 别报 OW,
    方向正好反了(买入侧本就稀缺)。承接教训:先问「这把尺子量的是不是他做的事」。
    """
    rows, ratings = [], {}
    for i in range(20):                      # 20 只 OW-lean,全 Hold → 基准 0%
        rows.append({"code": f"60{i:04d}", "lane": "healthy", "conviction": 60, "triage_lean": "OW"})
        ratings[f"60{i:04d}"] = "Hold"
    _day(tmp_path, "2026-07-28", rows)
    _ratings(monkeypatch, {"2026-07-28": ratings})

    lines = suggestion_lines(flip_stats(tmp_path, shrink=False), pd.DataFrame())

    lean = [ln for ln in lines if "OW-lean" in ln]
    assert len(lean) == 1
    assert "暂无鉴别力" in lean[0], f"基准退化时不得出指控行:{lean[0]}"
    assert "先自证" not in lean[0], "不得要求 L3 为一个系统性稀缺自证"
