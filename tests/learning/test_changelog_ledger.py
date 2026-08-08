"""重标定效果 ledger:前后日度 IC 对比 + 薄样本旗。合成,无网络。

spec: docs/specs/2026-07-02-scan-observability-design.md §3
"""
from __future__ import annotations

import json

import pandas as pd

from autoresearch.common.ruler import MAIN_RULER
from autoresearch.learning.changelog_ledger import _day_ic, day_ics, render, roll


def _attr_day(root, date, ic_pos: bool):
    d = root / date / "retro"
    d.mkdir(parents=True)
    comp = list(range(20))
    fwd = [c * 0.001 for c in comp] if ic_pos else [-c * 0.001 for c in comp]
    # Wave12-T7:_day_ic 已切主尺(MAIN_RULER),该 fixture 因此写 gap_c1_o2 列(不是
    # 比旧主尺还旧一代的 fwd_1_oo)——列名字面量固定为当前 MAIN_RULER 的值,与生产代码的
    # 动态 MAIN_RULER 引用是两回事(fixture 只需保证与"当下主尺"同名即可)。
    pd.DataFrame({"code": [f"{i:06d}" for i in comp], "composite": comp,
                  MAIN_RULER: fwd}).to_csv(d / "attribution.csv", index=False)


def _changelog(root, retro_date):
    p = root / "changelog.jsonl"
    rec = {"id": "cl_x", "ts": "t", "kind": "recalibrate", "retro_date": retro_date,
           "before_sha": "a", "after_sha": "b", "top_changes": [], "panel_dates_n": 9}
    p.write_text(json.dumps(rec) + "\n", encoding="utf-8")


def test_delta_and_render(tmp_path):
    scan = tmp_path / "scan"
    for d in ("2026-06-24", "2026-06-25", "2026-06-26"):
        _attr_day(scan, d, ic_pos=False)                    # 采纳前 IC = −1
    for d in ("2026-06-29", "2026-06-30", "2026-07-01"):
        _attr_day(scan, d, ic_pos=True)                     # 采纳后 IC = +1
    kn = tmp_path / "kn"
    kn.mkdir()
    _changelog(kn, "2026-06-29")
    ics = day_ics(scan)
    assert len(ics) == 6 and ics["2026-06-24"] < 0 < ics["2026-07-01"]
    df = roll(kn, scan)
    r = df.iloc[0]
    assert r["n_before"] == 3 and r["n_after"] == 3 and not r["thin"]
    assert r["delta"] == 2.0                                # −1 → +1
    assert "汇总" in "\n".join(render(df))


def test_thin_flag_and_empty(tmp_path):
    scan = tmp_path / "scan"
    _attr_day(scan, "2026-06-26", ic_pos=False)
    _attr_day(scan, "2026-06-29", ic_pos=True)
    kn = tmp_path / "kn"
    kn.mkdir()
    _changelog(kn, "2026-06-29")
    df = roll(kn, scan)
    assert df.iloc[0]["thin"]                               # 前后各 1 日 <3
    assert "⚠样本少" in "\n".join(render(df))
    assert not len(roll(tmp_path / "nope", scan))           # 无 changelog → 空


def test_trial_count_and_dsr_lines(tmp_path):
    """P0-6:trial 按 retro_date 升序 1-based;渲染含多重检验行;最近 Δ≤0 亮 C18 红灯。"""
    import pandas as pd

    from autoresearch.learning.changelog_ledger import render, roll  # noqa: F401
    df = pd.DataFrame([
        {"id": "a", "retro_date": "2026-07-01", "trial": 1, "n_before": 3, "n_after": 3,
         "ic_before": 0.01, "ic_after": 0.02, "delta": 0.01, "thin": False},
        {"id": "b", "retro_date": "2026-07-05", "trial": 2, "n_before": 3, "n_after": 3,
         "ic_before": 0.02, "ic_after": 0.01, "delta": -0.01, "thin": False},
    ])
    text = "\n".join(render(df))
    assert "已试 **2** 版" in text and "多重检验" in text
    assert "C18 红灯" in text and "停止调参信号" in text


# ───────────────────── 心跳探针(pr_20260716_001 复发病监测) ─────────────────────


def _hb_rec(sha_b, sha_a, ts="2026-07-16T20:00:00", nd=107):
    return {"kind": "recalibrate", "ts": ts, "retro_date": "2026-07-14",
            "before_sha": sha_b, "after_sha": sha_a, "n_dates": nd}


def test_heartbeat_flags_consecutive_noop(tmp_path):
    """连续 k 次 before==after 且 sha 全同 → 🚨(会变的量没变=死了也像活着)。"""
    import json as _json

    from autoresearch.learning.changelog_ledger import heartbeat
    p = tmp_path / "changelog.jsonl"
    p.write_text("".join(_json.dumps(_hb_rec("72b3d0af", "72b3d0af")) + "\n" for _ in range(3)),
                 encoding="utf-8")
    line = heartbeat(knowledge_dir=tmp_path, k=3)
    assert "🚨" in line and "NO-OP" in line and "72b3d0af" in line


def test_heartbeat_ok_when_sha_changes(tmp_path):
    import json as _json

    from autoresearch.learning.changelog_ledger import heartbeat
    p = tmp_path / "changelog.jsonl"
    recs = [_hb_rec("aaa", "aaa"), _hb_rec("aaa", "aaa"), _hb_rec("aaa", "bbb")]
    p.write_text("".join(_json.dumps(r) + "\n" for r in recs), encoding="utf-8")
    line = heartbeat(knowledge_dir=tmp_path, k=3)
    assert "✓" in line and "bbb" in line


def test_heartbeat_empty_ledger(tmp_path):
    from autoresearch.learning.changelog_ledger import heartbeat
    assert "无 recalibrate 记录" in heartbeat(knowledge_dir=tmp_path)


# ───────────────────── Wave12-T7:心跳 IC 切主尺(A4) ─────────────────────


def test_day_ic_reads_main_ruler_column_not_fwd_1_oo():
    """T7:_day_ic() 曾用 fwd_1_oo(比旧主尺 fwd_2_oc 还旧一代)评价重标定排序质量,而权重
    已按 gap_c1_o2 校准——尺完全错配。改 MAIN_RULER 后:①只给 MAIN_RULER 列(无 fwd_1_oo)
    仍应出 IC;②只给 fwd_1_oo(无 MAIN_RULER)应返回 None(旧列不再是判据来源)。"""
    comp = list(range(20))
    only_main_ruler = pd.DataFrame({
        "code": [f"{i:06d}" for i in comp], "composite": comp,
        MAIN_RULER: [c * 0.001 for c in comp],
    })
    assert "fwd_1_oo" not in only_main_ruler.columns
    ic = _day_ic(only_main_ruler)
    assert ic is not None and ic > 0.99   # 完全同序,rank correlation ≈ 1

    only_legacy = pd.DataFrame({
        "code": [f"{i:06d}" for i in comp], "composite": comp,
        "fwd_1_oo": [c * 0.001 for c in comp],
    })
    assert _day_ic(only_legacy) is None    # 旧列不再被读


def test_render_title_carries_main_ruler_name():
    """心跳/IC 文案带尺名(会变的量断言,07-16 家训)——静态标题曾对哪把尺评的 IC 只字不提。"""
    text = "\n".join(render(pd.DataFrame(columns=[
        "id", "retro_date", "trial", "n_before", "n_after",
        "ic_before", "ic_after", "delta", "thin",
    ])))
    assert MAIN_RULER in text
