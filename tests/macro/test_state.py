"""Phase 2 —— macro_state 写入 + 双失效(海拔重构 §5.2)。NO network。

锚:write 端零新判断(评级复用 parse_allocation/parse_rating);load 端双失效
(age>ttl / regime 翻转),缺/坏/前视 → (None, 原因) = 调用方回退只用日频 pack。
"""
from __future__ import annotations

import json
from pathlib import Path

from autoresearch.macro.state import load_macro_state, write_macro_state
from autoresearch.macro.assemble import CROSS_ASSET_KEYS

AS_OF = "2026-07-01"

DECISION = """## S1 · 执行摘要
| regime 象限(增长×通胀) | 增长下×通胀下(衰退交易) | 风险偏好 neutral |

- OVERALL 风险档: **Rating**: Hold — 增长下行但政策托底
- 美股: **Rating**: Underweight — 估值高位盈利下修
- A股·港股: **Rating**: Overweight — 政策+估值双底
"""
DECISION += "\n".join(
    f"- {key}: **Rating**: Hold" for key in CROSS_ASSET_KEYS
    if key not in {"OVERALL 风险档", "美股", "A股·港股"}
)

SECTOR_MAP = """## M1
- 电子: **Rating**: Overweight — 景气上行
- 煤炭: **Rating**: Underweight — 价格下行
"""

PREMORTEM = """## S5
- **通胀再加速**:CPI 连续 2 月反弹 → 减配久期
- **中国地产失速**:销售同比 <-20% → A股降档
- 无加粗的普通行不算死因
- **地缘黑天鹅**:关税二次升级
- **第四条不该被收**:cap=3
"""


def _mk_macro_ctx(tmp_path: Path) -> Path:
    root = tmp_path / "macro" / AS_OF
    (root / "1_spine").mkdir(parents=True)
    (root / "2_meso").mkdir(parents=True)
    (root / "1_spine" / "decision.md").write_text(DECISION, encoding="utf-8")
    (root / "2_meso" / "sector_map.md").write_text(SECTOR_MAP, encoding="utf-8")
    (root / "1_spine" / "premortem.md").write_text(PREMORTEM, encoding="utf-8")
    (root / "data.md").write_text(
        "**行业资金净流入(tushare)**:\n| 行业 | 主力净流入(亿) | 领涨股 |\n"
        "|---|---:|---|\n| 电子 | 1 | 示例甲 |\n| 煤炭 | -1 | 示例乙 |\n",
        encoding="utf-8",
    )
    return root


def test_write_macro_state_fields(tmp_path):
    root = _mk_macro_ctx(tmp_path)
    scan_root = tmp_path / "scan"
    (scan_root / AS_OF).mkdir(parents=True)
    (scan_root / AS_OF / "meta.json").write_text(json.dumps({"regime": "range"}), encoding="utf-8")
    st = write_macro_state(root, report_path="reports/macro/x.md",
                           out_dir=root.parent, scan_root=scan_root)
    assert st["as_of"] == AS_OF
    assert st["regime_at_run"] == "range"                    # 读 scan meta 同一标签
    assert st["overall_rating"] == "Hold" and st["risk_stance"] == "neutral"
    assert st["cross_asset"]["美股"] == "Underweight"
    assert st["ashare_sectors"] == {"电子": "Overweight", "煤炭": "Underweight"}
    assert "象限" in (st["quadrant_raw"] or "")
    assert len(st["key_risks"]) == 3                          # cap=3,无加粗行不算
    assert st["key_risks"][0].startswith("通胀再加速")
    on_disk = json.loads((root.parent / "macro_state.json").read_text(encoding="utf-8"))
    assert on_disk == st                                      # 落盘即所得


def test_write_degrades_without_optional_parts(tmp_path):
    root = tmp_path / "macro" / AS_OF
    (root / "1_spine").mkdir(parents=True)
    (root / "1_spine" / "decision.md").write_text(DECISION, encoding="utf-8")
    st = write_macro_state(root, out_dir=root.parent, scan_root=tmp_path / "noscan")
    assert st["regime_at_run"] is None                        # 无 scan meta → 只剩 age 判据
    assert st["ashare_sectors"] == {} and st["key_risks"] == []


def test_load_fresh_expired_lookahead_missing(tmp_path):
    root = _mk_macro_ctx(tmp_path)
    write_macro_state(root, out_dir=root.parent, scan_root=tmp_path / "noscan")
    p = root.parent / "macro_state.json"
    st, note = load_macro_state("2026-07-03", regime_today="risk_off", path=p)
    assert st is not None and "新鲜" in note                  # regime_at_run 缺 → 该判据跳过
    st, note = load_macro_state("2026-07-09", path=p)         # age 8d > 7 → 过期
    assert st is None and "过期" in note
    st, note = load_macro_state("2026-06-30", path=p)         # as_of 晚于 today = 前视
    assert st is None and "前视" in note
    st, note = load_macro_state("2026-07-03", path=tmp_path / "nope.json")
    assert st is None and "无" in note


def test_load_regime_flip_invalidates(tmp_path):
    root = _mk_macro_ctx(tmp_path)
    scan_root = tmp_path / "scan"
    (scan_root / AS_OF).mkdir(parents=True)
    (scan_root / AS_OF / "meta.json").write_text(json.dumps({"regime": "range"}), encoding="utf-8")
    write_macro_state(root, out_dir=root.parent, scan_root=scan_root)
    p = root.parent / "macro_state.json"
    st, note = load_macro_state("2026-07-03", regime_today="risk_off", path=p)
    assert st is None and "翻转" in note                      # range → risk_off 立即失效
    st, note = load_macro_state("2026-07-03", regime_today="range", path=p)
    assert st is not None and "新鲜" in note


def test_default_reader_prefers_receipt_committed_state(monkeypatch, tmp_path):
    from autoresearch.macro import state as state_mod

    stale = {"as_of": "2026-06-01", "ttl_days": 7}
    live = tmp_path / "macro_state.json"
    live.write_text(json.dumps(stale), encoding="utf-8")
    monkeypatch.setattr(state_mod, "DEFAULT_ROOT", tmp_path)
    committed = {
        "as_of": AS_OF,
        "ttl_days": 7,
        "regime_at_run": None,
        "global_tape_asof": "must-be-hidden",
    }
    monkeypatch.setattr(
        state_mod.published_state,
        "read_committed_state",
        lambda *args, **kwargs: committed,
    )

    loaded, note = state_mod.load_macro_state("2026-07-03")

    assert loaded == {key: value for key, value in committed.items() if key != "global_tape_asof"}
    assert "新鲜" in note


def test_invalid_macro_state_preserves_previous_bytes_and_asof(tmp_path):
    import pytest

    root = _mk_macro_ctx(tmp_path)
    out = root.parent
    previous = write_macro_state(root, out_dir=out, scan_root=tmp_path / "noscan")
    before = (out / "macro_state.json").read_bytes()
    for body in ("", "prose without allocation", "- 美股: **Rating**: Invalid"):
        (root / "1_spine/decision.md").write_text(body)
        with pytest.raises(ValueError):
            write_macro_state(root, out_dir=out, scan_root=tmp_path / "noscan")
        assert (out / "macro_state.json").read_bytes() == before
        assert json.loads(before)["as_of"] == previous["as_of"]


def test_invalid_optional_sector_table_preserves_state(tmp_path):
    import pytest

    root = _mk_macro_ctx(tmp_path)
    write_macro_state(root, out_dir=root.parent, scan_root=tmp_path / "noscan")
    before = (root.parent / "macro_state.json").read_bytes()
    (root / "2_meso/sector_map.md").write_text("- 电子: **Rating**: Invalid")
    with pytest.raises(ValueError):
        write_macro_state(root, out_dir=root.parent, scan_root=tmp_path / "noscan")
    assert (root.parent / "macro_state.json").read_bytes() == before


def test_macro_state_validates_declared_keys_before_replacing(tmp_path):
    import pytest
    from autoresearch.macro.assemble import DECISION_REL, SECTOR_MAP_REL

    root = _mk_macro_ctx(tmp_path)
    write_macro_state(root, out_dir=root.parent, scan_root=tmp_path / "noscan")
    before = (root.parent / "macro_state.json").read_bytes()
    with pytest.raises(ValueError, match="missing"):
        write_macro_state(root, out_dir=root.parent, expected_keys={
            DECISION_REL: ["OVERALL 风险档", "美股", "A股·港股"],
            SECTOR_MAP_REL: ["电子", "煤炭", "银行"],
        })
    assert (root.parent / "macro_state.json").read_bytes() == before


def test_macro_state_atomic_replace_failure_preserves_previous_file(tmp_path, monkeypatch):
    import pytest
    from autoresearch.common import atomic

    root = _mk_macro_ctx(tmp_path)
    write_macro_state(root, out_dir=root.parent, scan_root=tmp_path / "noscan")
    state_path = root.parent / "macro_state.json"
    before = state_path.read_bytes()
    newer = root.with_name("2026-07-02")
    root.rename(newer)

    def fail_replace(source, destination):
        raise OSError("injected replacement failure")

    monkeypatch.setattr(atomic.os, "replace", fail_replace)
    with pytest.raises(OSError, match="replacement failure"):
        write_macro_state(newer, out_dir=newer.parent, scan_root=tmp_path / "noscan")
    assert state_path.read_bytes() == before
    assert json.loads(state_path.read_bytes())["as_of"] == AS_OF
    assert not list(newer.parent.glob(".macro_state.json.*.tmp"))
