"""海外读透映射块(D-6:`autoresearch/sector/pack.py` 的 `readthrough` 键)。

spec: `docs/specs/2026-08-28-external-evidence-expansion-design.md` §8 + §4「中观 full」+ §11 D-6。

四条边界:

1. **只读有效期内的映射**,且 pack 自己也要能拒 —— 上游声称筛过 ≠ 真筛过,而这块是要
   印进研究报告的**外部产业事实**,错一条就是一条查无实据的证据。
2. **`kind` 决定它能被写成什么**:`etf` / `index` 没有 `next_earnings_date`,
   上游误塞也一律抹成 None(硬门,不是提示)。
3. **两条腿都是 B 级**:名单缺 → 整键省略(**不是空 list**);行情缺 → `stale_reason`
   + 记账,**绝不抛**(sector pack 是 scan Stage 1 的前置件)。
4. **只进 full 档**:lite brief(喂 L3/L4)一个字都不许提它 —— 那是受冻结的 B-4。

零网络:映射表与 tape 全部注入。
"""

from __future__ import annotations

import sys
import types

import pandas as pd
import pytest

from autoresearch.data import contracts
from autoresearch.sector import pack as sector_pack

DATE = "2026-08-28"
IND = "消费电子"

_BASE = {"symbol": "AAPL", "kind": "company", "relation": "customer",
         "direction": "downstream", "rationale": "终端需求与供应链披露的读透对象",
         "evidence_url": "https://example.com/evidence", "effective_from": "2026-01-01",
         "effective_to": None}


def _item(**over) -> dict:
    return {**_BASE, **over}


def _install_map(monkeypatch, items, *, raises=None, shape=None):
    """注入 `autoresearch.data.readthrough`(工作树里可以整个不存在 —— B 级)。"""
    module = types.ModuleType("autoresearch.data.readthrough")

    def load_map(as_of=None, **_kw):
        if raises is not None:
            raise raises
        if shape is not None:
            return shape
        return {"version": 2, "industries": {IND: list(items)}}

    module.load_map = load_map
    monkeypatch.setitem(sys.modules, "autoresearch.data.readthrough", module)


_TAPE_ROWS = [
    {"symbol": "AAPL", "pct_1d": 1.234, "pct_5d": -2.345, "session_complete": True,
     "next_earnings_date": "2026-10-29", "implied_move_note": "跨式隐含 ±4.1%"},
    {"symbol": "SMH", "pct_1d": 0.5, "pct_5d": 3.0, "session_complete": True,
     "next_earnings_date": "2026-11-19", "implied_move_note": "不该出现在 ETF 行"},
    {"symbol": "^SOX", "pct_1d": -0.25, "pct_5d": 1.5, "session_complete": False},
]


def _install_tape(monkeypatch, rows=None, *, raises=None, frame=None):
    from autoresearch.data.sources import yf_tape

    def fetch_global_tape(as_of=None, **_kw):
        if raises is not None:
            raise raises
        if frame is not None:
            return frame
        return pd.DataFrame(rows if rows is not None else _TAPE_ROWS)

    monkeypatch.setattr(yf_tape, "fetch_global_tape", fetch_global_tape)


@pytest.fixture(autouse=True)
def _clean_degradations():
    contracts.clear_degradations()
    yield
    contracts.clear_degradations()


# ───────────────────────── 1. presence-gated:无有效映射 = 整键省略 ─────────────────


def test_no_mapping_module_means_no_key_at_all(monkeypatch, tmp_path):
    """名单腿整个不存在(D-1 前的常态)→ pack 里连键都没有。"""
    monkeypatch.setitem(sys.modules, "autoresearch.data.readthrough", None)
    scan_dir = tmp_path / DATE
    scan_dir.mkdir()

    result = sector_pack.sector_pack(IND, scan_dir)

    assert "readthrough" not in result
    assert result["industry"] == IND and result["as_of"] == DATE


def test_an_industry_with_no_valid_item_omits_the_key_rather_than_writing_an_empty_list(
    monkeypatch, tmp_path
):
    """空 list 会让下游渲染出一张空表 —— 空表在报告里长得像「查过了,没有」。"""
    _install_map(monkeypatch, [_item(evidence_url="")])      # 唯一一条被证据门拒掉
    _install_tape(monkeypatch)
    scan_dir = tmp_path / DATE
    scan_dir.mkdir()

    assert sector_pack.readthrough_block(IND, DATE) is None
    assert "readthrough" not in sector_pack.sector_pack(IND, scan_dir)


def test_other_industries_are_not_borrowed(monkeypatch):
    _install_map(monkeypatch, [_item()])
    _install_tape(monkeypatch)

    assert sector_pack.readthrough_block("煤炭", DATE) is None


def test_a_broken_map_is_booked_and_never_raises(monkeypatch):
    _install_map(monkeypatch, [], raises=RuntimeError("yaml 坏了"))

    assert sector_pack.readthrough_block(IND, DATE) is None
    assert any(r["endpoint"] == "readthrough_map" for r in contracts.degradations())


def test_a_map_of_the_wrong_shape_is_treated_as_absent(monkeypatch):
    _install_map(monkeypatch, [], shape=["not", "a", "dict"])
    assert sector_pack.readthrough_block(IND, DATE) is None


# ───────────────────────── 2. 准入:只读有效期内 + 证据齐 + 可交易 ─────────────────


@pytest.mark.parametrize(
    ("label", "over"),
    [
        ("尚未生效", {"effective_from": "2026-09-01"}),
        ("已过期", {"effective_to": "2026-08-01"}),
        ("无有效期起点", {"effective_from": None}),
        ("证据 URL 缺失", {"evidence_url": ""}),
        ("证据 URL 不是 http(s)", {"evidence_url": "file:///tmp/x"}),
        ("kind 不合法", {"kind": "adr"}),
        ("relation 不合法", {"relation": "competitorish"}),
        ("direction 不合法", {"direction": "sideways"}),
        ("显式不可交易", {"tradable": False}),
        ("已退市", {"status": "delisted"}),
        ("symbol 为空", {"symbol": "  "}),
    ],
)
def test_invalid_items_are_rejected_by_the_pack_itself(monkeypatch, label, over):
    """消费者自己也要能拒 —— 不信任 `load_map` 已经筛过。"""
    _install_map(monkeypatch, [_item(**over)])
    _install_tape(monkeypatch)

    assert sector_pack.readthrough_block(IND, DATE) is None, label


def test_boundary_dates_are_inclusive(monkeypatch):
    _install_map(monkeypatch, [_item(effective_from=DATE, effective_to=DATE)])
    _install_tape(monkeypatch)

    rows = sector_pack.readthrough_block(IND, DATE)
    assert rows and rows[0]["symbol"] == "AAPL"


def test_an_unparsable_as_of_keeps_the_enum_and_evidence_gates(monkeypatch):
    """as_of 不是日期 → 判不了有效期,但枚举 / 证据两道门照关。"""
    _install_map(monkeypatch, [_item(), _item(symbol="BAD", evidence_url="")])
    _install_tape(monkeypatch)

    rows = sector_pack.readthrough_block(IND, "not-a-date")
    assert [r["symbol"] for r in rows] == ["AAPL"]


def test_at_most_four_items_survive(monkeypatch):
    _install_map(monkeypatch, [_item(symbol=f"T{i}") for i in range(9)])
    _install_tape(monkeypatch, rows=[])

    rows = sector_pack.readthrough_block(IND, DATE)
    assert len(rows) == sector_pack._RT_MAX == 4


# ───────────────────────── 3. 三类渲染 ─────────────────────────


def test_company_etf_and_index_render_with_the_right_fields(monkeypatch):
    _install_map(monkeypatch, [
        _item(symbol="AAPL", kind="company"),
        _item(symbol="SMH", kind="etf", relation="theme", direction="peer"),
        _item(symbol="^SOX", kind="index", relation="peer", direction="peer"),
    ])
    _install_tape(monkeypatch)

    rows = {r["symbol"]: r for r in sector_pack.readthrough_block(IND, DATE)}

    assert set(rows) == {"AAPL", "SMH", "^SOX"}
    company = rows["AAPL"]
    assert (company["kind"], company["relation"], company["direction"]) == (
        "company", "customer", "downstream")
    assert (company["pct_1d"], company["pct_5d"]) == (1.23, -2.35)     # 两位小数
    assert company["next_earnings_date"] == "2026-10-29"
    assert company["implied_move_note"] == "跨式隐含 ±4.1%"
    assert company["stale_reason"] is None
    assert company["evidence_url"].startswith("https://")


def test_an_etf_never_gets_an_earnings_date_even_when_upstream_supplies_one(monkeypatch):
    """§8:ETF / 指数不得伪装成公司或财报主体 —— 硬门,不是提示。"""
    _install_map(monkeypatch, [
        _item(symbol="SMH", kind="etf", relation="theme", direction="peer",
              next_earnings_date="2026-11-19", implied_move_note="上游硬塞的"),
        _item(symbol="^SOX", kind="index", relation="peer", direction="peer"),
    ])
    _install_tape(monkeypatch)

    rows = {r["symbol"]: r for r in sector_pack.readthrough_block(IND, DATE)}

    for symbol in ("SMH", "^SOX"):
        assert rows[symbol]["next_earnings_date"] is None, symbol
        assert rows[symbol]["implied_move_note"] is None, symbol


def test_rows_carry_facts_only_and_synthesize_no_direction(monkeypatch):
    """映射只表示「值得观察的关系」;本模块只搬字段,不合成任何传导措辞。"""
    _install_map(monkeypatch, [_item()])
    _install_tape(monkeypatch)

    row = sector_pack.readthrough_block(IND, DATE)[0]

    assert set(row) == {"symbol", "kind", "relation", "direction", "rationale",
                        "evidence_url", "pct_1d", "pct_5d", "next_earnings_date",
                        "implied_move_note", "stale_reason"}
    blob = " ".join(str(v) for v in row.values())
    for word in ("所以", "受益", "印证", "带动", "看多", "看空", "利好", "利空"):
        assert word not in blob, word


# ───────────────────────── 4. 行情腿降级 ─────────────────────────


def test_tape_failure_sets_stale_reason_books_it_and_never_raises(monkeypatch):
    _install_map(monkeypatch, [_item()])
    _install_tape(monkeypatch, raises=RuntimeError("yfinance 挂了"))

    rows = sector_pack.readthrough_block(IND, DATE)                  # 不抛

    assert len(rows) == 1
    assert rows[0]["pct_1d"] is None and rows[0]["pct_5d"] is None
    assert "tape 取数失败" in rows[0]["stale_reason"]
    assert rows[0]["symbol"] == "AAPL" and rows[0]["relation"] == "customer"
    assert any(r["endpoint"] == "global_tape" for r in contracts.degradations())


def test_empty_tape_frame_is_also_a_stale_reason(monkeypatch):
    _install_map(monkeypatch, [_item()])
    _install_tape(monkeypatch, frame=pd.DataFrame())

    rows = sector_pack.readthrough_block(IND, DATE)
    assert rows[0]["stale_reason"] == "tape 空返回(无 symbol 行)"


def test_a_symbol_missing_from_the_tape_says_so_per_row(monkeypatch):
    _install_map(monkeypatch, [_item(), _item(symbol="MSFT")])
    _install_tape(monkeypatch)                                        # tape 里没有 MSFT

    rows = {r["symbol"]: r for r in sector_pack.readthrough_block(IND, DATE)}

    assert rows["AAPL"]["stale_reason"] is None
    assert rows["MSFT"]["stale_reason"] == "tape 无 MSFT 行"
    assert rows["MSFT"]["pct_1d"] is None


def test_an_unfinished_us_session_is_flagged(monkeypatch):
    _install_map(monkeypatch, [_item()])
    _install_tape(monkeypatch, frame=pd.DataFrame(
        [{"symbol": "AAPL", "pct_1d": 1.0, "pct_5d": 2.0, "session_complete": False}]))

    row = sector_pack.readthrough_block(IND, DATE)[0]

    assert row["stale_reason"] == "美股时段未收(session_complete=False)"


@pytest.mark.parametrize("value", [False, __import__("numpy").False_, "False", "0"])
def test_the_session_probe_survives_a_non_python_bool(value):
    """三态判定不能靠 `is False`。

    pandas 从**同质列**里取出来的是 `numpy.bool_`,而 `np.False_ is False` 为假;
    那样写探针永远不亮,报告会把一个还没收盘、随时会变的数字当成已定读数印出去。
    今天 `_rt_tape` 的帧恰好总有 `symbol` 这一列字符串(于是行是 object dtype、
    布尔被还原成 Python bool)—— 这条**巧合**不该是守卫成立的理由。
    """
    row = sector_pack._rt_render(_item(), {"AAPL": {"session_complete": value}}, None)

    assert row["stale_reason"] == "美股时段未收(session_complete=False)"


def test_a_complete_session_is_not_flagged(monkeypatch):
    _install_map(monkeypatch, [_item()])
    _install_tape(monkeypatch, frame=pd.DataFrame({"symbol": ["AAPL"],
                                                   "session_complete": [True]}))

    assert sector_pack.readthrough_block(IND, DATE)[0]["stale_reason"] is None


# ───────────────────────── 5. 端到端 + lite 越界探针 ─────────────────────────


def test_sector_pack_carries_the_key_when_a_valid_mapping_exists(monkeypatch, tmp_path):
    _install_map(monkeypatch, [_item()])
    _install_tape(monkeypatch)
    scan_dir = tmp_path / DATE
    scan_dir.mkdir()

    result = sector_pack.sector_pack(IND, scan_dir)

    assert [r["symbol"] for r in result["readthrough"]] == ["AAPL"]
    # staging 腿一个字段都没被外源改动。
    assert result["n_market"] == 0 and result["leaders"] == []


def test_the_staging_leg_alone_never_has_the_key(monkeypatch, tmp_path):
    _install_map(monkeypatch, [_item()])
    _install_tape(monkeypatch)
    scan_dir = tmp_path / DATE
    scan_dir.mkdir()

    assert "readthrough" not in sector_pack._sector_pack_staging(IND, scan_dir)


_LITE_MARKERS = ("readthrough", "读透", "readthrough_map", "yf_tape")


def test_the_lite_brief_contract_never_mentions_readthrough():
    """越界探针(逐字对账):lite brief 会喂 L3/L4 —— 往它里塞新事实就是改判断层输入。

    那是设计稿的 **B-4**,受 08-26 A0 冻结;pack JSON 里有这个键,写手的契约里不许有。
    """
    from pathlib import Path

    root = Path(__file__).resolve().parents[2]
    agent = (root / ".claude/agents/sector-brief.md").read_text(encoding="utf-8")
    for marker in _LITE_MARKERS:
        assert marker not in agent, f"sector-brief.md 出现 lite 越界字样:{marker}"

    playbook = (root / ".claude/skills/sector-research/sector-playbook.md").read_text(
        encoding="utf-8")
    start = playbook.index("## lite brief 模板")
    end = playbook.index("## full 深研")
    lite_section = playbook[start:end]
    for marker in _LITE_MARKERS:
        assert marker not in lite_section, f"playbook lite 模板出现越界字样:{marker}"
    # 对照臂:full 节确实讲了它(否则这条探针会因为「哪儿都没有」而永远为真)。
    assert "readthrough" in playbook[end:]
