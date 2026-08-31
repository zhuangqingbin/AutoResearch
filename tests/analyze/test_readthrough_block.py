"""A 股 full 的「海外映射」块 —— §8 渲染禁忌的逐字探针(设计稿 2026-08-28 §11 D-3 行)。

映射表示「值得观察的关系」,**不表示因果方向、涨跌传导方向或评级方向**。所以本块只搬运
事实(`relation` / `direction` / `rationale` / 证据 URL + 确定性行情),**不产生推论句**:
禁词「所以 / 传导 / 带动」由 `test_render_has_no_causal_words` 逐字锁死。

另三条:无映射整块省略(不写空表)/ 单层 ≤4 项 / 每行带 relation 与 rationale。
上游 `autoresearch.data.readthrough.load_map(as_of) -> dict` 由 D-1 提供,这里**按签名打桩**
(`test_load_map_contract_*`),全程零真网络。
"""
import sys
import types

import pytest

from autoresearch.analyze import harvest

AS_OF = "2026-08-28"
CAUSAL_WORDS = ("所以", "传导", "带动")


def _item(sym, *, kind="company", relation="customer", direction="downstream",
          rationale=None, url="https://example.com/evidence", eff_from="2026-01-01",
          eff_to=None, **extra):
    it = {"symbol": sym, "kind": kind, "relation": relation, "direction": direction,
          "rationale": rationale or f"{sym} 的公开供应链证据",
          "evidence_url": url, "effective_from": eff_from, "effective_to": eff_to}
    it.update(extra)
    return it


TAPE = {
    "NVDA": {"symbol": "NVDA", "pct_1d": 1.23, "pct_5d": 4.56, "session_complete": True},
    "AAPL": {"symbol": "AAPL", "pct_1d": -0.51, "pct_5d": 2.02, "session_complete": True},
    "SMH": {"symbol": "SMH", "pct_1d": 0.80, "pct_5d": 3.10, "session_complete": False},
}
IV = {"NVDA": {"status": "ok", "atm_iv_30d": 42.15},
      "AAPL": {"status": "UNMEASURED", "reason": "无包围 30D 的有效到期"},
      "SMH": {"status": "ok", "atm_iv_30d": 21.4}}
EARN = {"NVDA": {"date": "2026-11-19", "session": "AMC", "time_quality": "TIMED"},
        "AAPL": {"date": None, "session": None, "time_quality": "UNKNOWN"}}


def _render(items, **kw):
    kw.setdefault("tape", TAPE)
    kw.setdefault("iv", IV)
    kw.setdefault("earnings", EARN)
    return harvest.readthrough_block("300857.SZ", AS_OF, items=items, **kw)


# ───────────────────────── 禁忌:不表因果 ─────────────────────────


@pytest.mark.unit
def test_render_has_no_causal_words():
    md = _render([_item("NVDA"), _item("AAPL", relation="peer", direction="peer"),
                  _item("SMH", kind="etf", relation="theme", direction="peer")])
    for bad in CAUSAL_WORDS:
        assert bad not in md, f"海外映射块出现因果措辞「{bad}」—— §8 明令不表因果 / 传导方向"
    assert "不表示因果" in md and "不表示涨跌方向" in md and "不表示评级方向" in md


@pytest.mark.unit
def test_render_carries_no_rating_or_direction_verdict():
    md = _render([_item("NVDA")])
    for bad in ("受益", "利好", "利空", "看多", "看空", "买入", "卖出"):
        assert bad not in md, f"映射块出现判断措辞「{bad}」"


# ───────────────────────── presence-gated:无映射整块省略 ─────────────────────────


@pytest.mark.unit
def test_no_mapping_omits_the_whole_block(monkeypatch):
    assert _render([]) is None
    _stub_readthrough(monkeypatch, {"codes": {}, "industries": {}})   # 表在,但本票无映射
    assert harvest.readthrough_block("300857.SZ", AS_OF, items=None, tape={}, iv={},
                                     earnings={}) is None


@pytest.mark.unit
def test_invalid_items_are_rejected_and_can_empty_the_block():
    bad = [
        _item("NVDA", url="ftp://example.com/x"),                 # 证据 URL 不是 http(s)
        _item("AAPL", relation="rival"),                          # relation 枚举非法
        _item("SMH", kind="fund"),                                # kind 枚举非法
        _item("TSM", direction="sideways"),                       # direction 枚举非法
        _item("MU", eff_from="2026-01-01", eff_to="2026-06-30"),  # 有效期已过
        _item("AVGO", eff_from="2026-12-01"),                     # 有效期未到
        _item("INTC", status="delisted"),                         # 已退市
        _item("AMD", eff_from=None),                              # 缺有效期起点
    ]
    assert _render(bad) is None
    # 反面对照:同一批里混一个合法项 → 块出现,且只有它
    md = _render([*bad, _item("NVDA")])
    assert md is not None and "**NVDA**" in md and "**AAPL**" not in md


@pytest.mark.unit
def test_omitted_block_produces_no_section_header():
    assert harvest._opt_section(harvest._TITLE_READTHROUGH,
                                lambda: None) == ""
    assert harvest._TITLE_READTHROUGH in harvest._opt_section(
        harvest._TITLE_READTHROUGH, lambda: "x")


# ───────────────────────── ≤4 项 / 每行事实 ─────────────────────────


@pytest.mark.unit
def test_more_than_four_items_are_truncated():
    syms = ["NVDA", "AAPL", "SMH", "TSM", "MU", "AVGO"]
    md = _render([_item(s) for s in syms], tape=TAPE, iv={}, earnings={})
    kept = [s for s in syms if f"**{s}**" in md]
    assert kept == syms[:harvest._RT_MAX] == ["NVDA", "AAPL", "SMH", "TSM"]
    assert "MU" not in md and "AVGO" not in md


@pytest.mark.unit
def test_each_row_carries_relation_direction_and_verbatim_rationale():
    items = [_item("NVDA", relation="customer", direction="downstream",
                   rationale="以公开供应链证据为准"),
             _item("AAPL", relation="peer", direction="peer", rationale="终端需求读透对象")]
    md = _render(items)
    for it in items:
        line = [ln for ln in md.splitlines()
                if ln.startswith(f"- **{it['symbol']}**")]
        assert line, f"{it['symbol']} 缺 rationale 行"
        assert f"relation={it['relation']}" in line[0]
        assert f"direction={it['direction']}" in line[0]
        assert it["rationale"] in line[0]            # **原样**,不改写
        assert it["evidence_url"] in line[0]
        assert f"| {it['relation']} | {it['direction']} |" in md   # 表里也带


@pytest.mark.unit
def test_rows_show_prev_session_and_5d_pct_from_tape():
    md = _render([_item("NVDA")])
    assert "+1.23%" in md and "+4.56%" in md
    assert "2026-11-19(AMC)" in md
    assert "42.15%(年化)" in md


@pytest.mark.unit
def test_unmeasured_iv_is_shown_with_reason_not_zero():
    md = _render([_item("AAPL")])
    assert "UNMEASURED(无包围 30D 的有效到期)" in md
    assert "0.00%(年化)" not in md
    assert "未确认" in md                    # 没有确认的下次财报日 → 不猜


@pytest.mark.unit
def test_etf_is_never_dressed_up_as_an_earnings_subject():
    md = _render([_item("SMH", kind="etf", relation="theme", direction="peer")])
    assert "不适用(非公司主体)" in md
    assert "美股时段未收" in md              # session_complete=False 要标出来


@pytest.mark.unit
def test_missing_tape_row_is_marked_not_zero_filled():
    md = _render([_item("TSM")], tape={}, iv={}, earnings={})
    assert "tape 无 TSM 行" in md
    assert "| +0.00% |" not in md and "| 0.00% |" not in md


# ───────────────────────── 上游契约:load_map(as_of) -> dict ─────────────────────────


def _stub_readthrough(monkeypatch, payload):
    mod = types.ModuleType("autoresearch.data.readthrough")
    mod.load_map = lambda as_of: payload
    monkeypatch.setitem(sys.modules, "autoresearch.data.readthrough", mod)
    return mod


@pytest.mark.unit
def test_load_map_contract_code_mapping_wins_over_industry(monkeypatch):
    _stub_readthrough(monkeypatch, {
        "codes": {"300857": [_item("NVDA")]},
        "industries": {"消费电子": [_item("AAPL")]},
    })
    got = harvest._readthrough_items("300857.SZ", AS_OF, industry="消费电子")
    assert [g["symbol"] for g in got] == ["NVDA"]        # §8:个股映射优先于行业


@pytest.mark.unit
def test_load_map_contract_industry_fallback(monkeypatch):
    _stub_readthrough(monkeypatch, {"codes": {}, "industries": {"消费电子": [_item("AAPL")]}})
    assert [g["symbol"] for g in
            harvest._readthrough_items("300857.SZ", AS_OF, industry="消费电子")] == ["AAPL"]
    assert harvest._readthrough_items("300857.SZ", AS_OF) == []      # 不给行业 = 无名单


@pytest.mark.unit
def test_load_map_failure_degrades_to_empty_and_never_raises(monkeypatch):
    mod = types.ModuleType("autoresearch.data.readthrough")

    def _boom(as_of):
        raise RuntimeError("映射表坏了")

    mod.load_map = _boom
    monkeypatch.setitem(sys.modules, "autoresearch.data.readthrough", mod)
    assert harvest._readthrough_items("300857.SZ", AS_OF) == []
    assert harvest.readthrough_block("300857.SZ", AS_OF, tape={}, iv={}, earnings={}) is None
