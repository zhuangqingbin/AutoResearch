"""0买日市场对照 ledger:roll 字段/render 对照/空目录优雅。合成 fixture。

spec: docs/specs/2026-07-02-scan-watchlist-and-health-metrics-design.md §2.3
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from autoresearch.learning.zero_buy_ledger import bought_mask, render, roll


def _mk_day(root, date, bought_flags, fwd1, fwd5, fwd2=None, gap=None):
    d = root / date / "retro"
    d.mkdir(parents=True)
    data = {"code": [f"{i:06d}" for i in range(len(bought_flags))],
            "bought": bought_flags, "fwd_1_oo": fwd1, "fwd_5_oc": fwd5}
    if fwd2 is not None:
        data["fwd_2_oc"] = fwd2
    if gap is not None:
        data["gap_c1_o2"] = gap
    pd.DataFrame(data).to_csv(d / "attribution.csv", index=False)


def test_roll_and_render(tmp_path):
    _mk_day(tmp_path, "2026-06-24", [False, False, False], [0.01, -0.03, -0.01], [0.02, -0.06, np.nan],
            fwd2=[0.02, -0.06, -0.02])
    _mk_day(tmp_path, "2026-06-18", [True, False], [0.02, 0.04], [0.05, 0.07], fwd2=[0.03, 0.05])
    df = roll(tmp_path)
    assert list(df["date"]) == ["2026-06-18", "2026-06-24"]
    r24 = df.set_index("date").loc["2026-06-24"]
    assert r24["n_bought"] == 0 and abs(r24["mkt_fwd1"] - (-0.01)) < 1e-9
    assert abs(r24["mkt_fwd5"] - (-0.02)) < 1e-9          # NaN 容忍
    assert abs(r24["mkt_fwd2"] - (-0.02)) < 1e-9
    md = "\n".join(render(df))
    assert "0买日" in md and "2026-06-24" in md and "有买日" in md


def test_verdict_falls_back_to_fwd2_when_gap_absent(tmp_path):
    """M-3 修复(review 2026-08-08):T6 后 gap 才是主判据,本测试没传 gap 列——测的是
    "gap 整列缺失 → 回退到 fwd_2" 这条路径,不再是"fwd_2 是主尺"(旧名字/旧注释在 T6
    之后已成假话,原名 test_verdict_uses_fwd2 已改)。
    fwd_2(gap 缺失时的一级回退)全为负、fwd_5(参考)为正 的 0 买日 → verdict 仍应判
    「空仓方向正确」。"""
    _mk_day(tmp_path, "2026-06-24", [False, False], [0.01, -0.02], [0.03, 0.05], fwd2=[-0.01, -0.02])
    led = roll(tmp_path)
    lines = render(led)
    assert any("mkt_fwd2" in c for c in led.columns) or "fwd_2" in "\n".join(lines)
    assert "空仓方向正确" in "\n".join(lines)


def test_verdict_falls_back_without_fwd2_column(tmp_path):
    """旧 attribution(无 fwd_2_oc 列,当前 100% 真实生产形态)→ roll/render 不炸,
    verdict 回退按 fwd_1 正常出(fwd_2 全列缺,非单行 NaN)。"""
    _mk_day(tmp_path, "2026-06-24", [False, False, False],
            [-0.01, -0.02, 0.01], [0.03, -0.02, 0.01])   # 不传 fwd2 → 无 fwd_2_oc 列
    led = roll(tmp_path)
    assert "mkt_fwd2" in led.columns and pd.isna(led.iloc[0]["mkt_fwd2"])
    md = "\n".join(render(led))
    assert "空仓方向正确" in md          # v1 均值 −0.00667 < 0 → 回退判定生效,不炸


def test_empty_root_graceful(tmp_path):
    df = roll(tmp_path)
    assert len(df) == 0
    assert any("无" in ln for ln in render(df))


def test_render_can_include_causal_verdict_summary():
    legacy = pd.DataFrame(
        [
            {
                "date": "2026-07-28",
                "n_bought": 0,
                "n_stocks": 2,
                "mkt_fwd1": -0.01,
                "mkt_fwd2": -0.02,
                "mkt_fwd5": None,
            }
        ]
    )
    causal = pd.DataFrame(
        [
            {
                "date": "2026-07-28",
                "status": "FALSE",
                "n_opportunities": 1,
            }
        ]
    )
    text = "\n".join(render(legacy, causal=causal))
    assert "因果裁决" in text
    assert "FALSE" in text


def test_verdict_follows_gap_not_fwd2_when_they_disagree(tmp_path):
    """T6(A3):gap(隔夜主尺)为负、fwd_2(降参考)为正的 0 买日 —— verdict 必须按 gap 出
    「空仓方向正确」,不能再被 fwd_2 带偏(镜像§1.2 07-29 类日按 gap 翻转的现象)。"""
    _mk_day(tmp_path, "2026-06-24", [False, False], [0.01, 0.02], [0.03, 0.05],
            fwd2=[0.03, 0.04], gap=[-0.02, -0.01])
    led = roll(tmp_path)
    assert "mkt_gap" in led.columns
    assert abs(led.iloc[0]["mkt_gap"] - (-0.015)) < 1e-9
    lines = "\n".join(render(led))
    assert "空仓方向正确" in lines
    # M-1 修复(review 2026-08-08,假绿灯):旧版渲染串同样含 "(主尺)" 子串(挂在 fwd_2
    # 上,修复前恒真、零鉴别力)。改为定位到 "(主尺)" 实际挂的是哪个字段——必须是 gap,
    # 不能是 fwd_2(用 "、" 分段,单独判断含"主尺"字样的那一段)。
    zero_line = next(ln for ln in lines.split("\n") if ln.startswith("- **0买日**"))
    main_ruler_segment = next(seg for seg in zero_line.split("、") if "主尺" in seg)
    assert "gap" in main_ruler_segment
    assert "fwd_2" not in main_ruler_segment


def test_zero_buy_summary_labels_each_column_with_its_own_n(tmp_path):
    """I-4 修复(review 2026-08-08):gap 与 fwd_1/2/5 的非空天数可能不同(gap_c1_o2 有
    历史空洞,如实测 context/scan 里的 2026-07-07 缺该列)——四个均值不能共用一个
    "(N 日)" 标签,分母必须各自标注(本仓库铁律:比率同时写分子/分母)。"""
    _mk_day(tmp_path, "2026-06-24", [False, False], [0.01, -0.02], [0.03, 0.05],
            fwd2=[-0.01, -0.02], gap=[-0.03, -0.01])         # 有 gap 列
    _mk_day(tmp_path, "2026-06-25", [False], [0.02], [0.04], fwd2=[0.01])   # 无 gap 列
    led = roll(tmp_path)
    assert len(led) == 2 and (led["n_bought"] == 0).all()
    assert pd.isna(led.iloc[1]["mkt_gap"])          # 06-25 无 gap_c1_o2 列 → mkt_gap 缺
    lines = "\n".join(render(led))
    zero_line = next(ln for ln in lines.split("\n") if ln.startswith("- **0买日**"))
    assert "n=1" in zero_line       # gap 只有 06-24 这 1 天非空
    assert "n=2" in zero_line       # fwd_1/fwd_2 两天都非空


def test_bought_mask_is_public_and_reused_by_journal(tmp_path):
    """D5 单一事实源:`bought_mask` 抽成可复用原语(journal.py 会同口径导入,见 test_journal.py)。

    兼容字符串 True/False、1/0;缺列 → 全 False(不炸,现行为不变)。
    """
    df = pd.DataFrame({"bought": [True, False, "True", "false", 1, 0]})
    m = bought_mask(df)
    assert list(m) == [True, False, True, False, True, False]
    assert list(bought_mask(pd.DataFrame({"code": ["000001"]}))) == [False]


# ── legacy 冻结(E6 转正,task-2.4;本模块 docstring 自 2026-08-08 预告的那件事)──


def test_roll_is_unfrozen_without_an_activate_date(tmp_path, monkeypatch):
    """未配置冻结日 → 全量(parity:与冻结逻辑落地之前逐字相同)。"""
    from autoresearch.learning import legacy_freeze
    monkeypatch.setattr(legacy_freeze, "cutoff", lambda: None)
    _mk_day(tmp_path, "2026-08-18", [True], [0.01], [0.02], fwd2=[0.02])
    _mk_day(tmp_path, "2026-08-20", [False], [0.03], [0.04], fwd2=[0.04])

    assert list(roll(tmp_path)["date"]) == ["2026-08-18", "2026-08-20"]


def test_roll_stops_at_the_activate_date_inclusive(tmp_path, monkeypatch):
    """冻结日**当天**已由 E6 拥有 BUY → 该日起不再记新行(边界含等号)。"""
    from autoresearch.learning import legacy_freeze
    monkeypatch.setattr(legacy_freeze, "cutoff", lambda: "2026-08-20")
    _mk_day(tmp_path, "2026-08-18", [True], [0.01], [0.02], fwd2=[0.02])
    _mk_day(tmp_path, "2026-08-20", [False], [0.03], [0.04], fwd2=[0.04])
    _mk_day(tmp_path, "2026-08-21", [False], [0.05], [0.06], fwd2=[0.06])

    assert list(roll(tmp_path)["date"]) == ["2026-08-18"]     # 历史行照旧在场


def test_render_carries_a_legacy_banner_when_frozen(tmp_path, monkeypatch):
    """冻结了却不说 = 读者拿一本停止更新的账当活账读。横幅由 render 自己读 config,
    不靠调用方"记得传"。"""
    from autoresearch.learning import legacy_freeze
    monkeypatch.setattr(legacy_freeze, "cutoff", lambda: "2026-08-20")
    _mk_day(tmp_path, "2026-08-18", [True], [0.01], [0.02], fwd2=[0.02])

    md = "\n".join(render(roll(tmp_path)))

    assert "legacy 冻结" in md and "2026-08-20" in md
    assert "不得接成一条曲线读" in md
    monkeypatch.setattr(legacy_freeze, "cutoff", lambda: None)
    assert "legacy 冻结" not in "\n".join(render(roll(tmp_path)))   # 未冻结 → 报表逐字不变


def test_cutoff_really_reads_scan_config(tmp_path, monkeypatch):
    """接线回归:`legacy_freeze.cutoff()` 的事实源是 `scan_config.jsonc` 的
    `relative_buy.activate_date`(2026-08-11 裁定:config = 全流程唯一参数事实源)。
    上面几条都 monkeypatch 掉了 `cutoff`,这条锁的是 `cutoff` 自己没坏。"""
    import json

    from autoresearch.learning import legacy_freeze

    cfg = tmp_path / "scan_config.jsonc"
    cfg.write_text(json.dumps({"relative_buy": {"activate_date": "2026-08-20"}}),
                   encoding="utf-8")
    monkeypatch.setattr("autoresearch.scan.user_config.DEFAULT_PATH", cfg)
    assert legacy_freeze.cutoff() == "2026-08-20"

    monkeypatch.setattr("autoresearch.scan.user_config.DEFAULT_PATH", tmp_path / "nope.jsonc")
    assert legacy_freeze.cutoff() is None                    # 缺配置 → 不冻结(parity)
