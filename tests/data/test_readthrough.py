"""海外读透映射表(`autoresearch/data/readthrough.py` + `readthrough_map.yaml`)。

design §8 的四条规则逐条上钉:
  ① 只表示「值得观察的关系」,不表示方向 → 这条是**渲染纪律**,由 `sector-playbook` / pack 负责;
     本文件钉的是它的前提:`load_map` 只搬字段,不合成任何方向措辞(见 `test_load_map_only_carries_fields`)。
  ② 入库 lint 验 ticker 类型 / 交易所 / 币种 / 可交易 + 枚举 + 证据 URL + 有效期;
     **ETF / 指数不得伪装成公司或财报主体**。
  ③ 个股映射优先于行业;空名单 = 整块省略;单层 ≤4 项。
  ④ 过期项自动停止消费。

**测试禁止真网络**:本文件从不给 `lint_map` 传真解析器,并显式钉住「模块默认解析器是 None」
(`test_default_resolver_is_offline`)—— 一个「默认会联网」的 lint 会在没人注意时偷偷发请求。
"""

import pytest

from autoresearch.data import readthrough as rt

AS_OF = "2026-08-29"
URL = "https://www.sec.gov/Archives/edgar/data/320193/x.htm"


def _item(**kw) -> dict:
    base = {
        "symbol": "NVDA",
        "kind": "company",
        "relation": "customer",
        "direction": "downstream",
        "rationale": "终端需求与供应链披露的读透对象",
        "evidence_url": URL,
        "effective_from": "2026-01-01",
        "effective_to": None,
        "status": "active",
    }
    base.update(kw)
    return base


def _doc(industries=None, codes=None, **kw) -> dict:
    doc = {
        "version": 2,
        "reviewed_at": AS_OF,
        "owner": "research",
        "industries": industries if industries is not None else {},
        "codes": codes if codes is not None else {},
    }
    doc.update(kw)
    return doc


def _errors(doc, **kw):
    return rt.lint_errors(rt.lint_map(doc, as_of=AS_OF, **kw))


def _levels(doc, **kw):
    return {i.level for i in rt.lint_map(doc, as_of=AS_OF, **kw)}


# ───────────────────────────── ④ 有效期 ─────────────────────────────


def test_expired_item_is_not_consumable():
    """`effective_to` 已过 → 自动停止消费(§8 规则四),lint 用 WARN 提醒清理而不是报错。"""
    doc = _doc({"半导体": [_item(symbol="SMH", kind="etf", relation="theme", direction="peer",
                                effective_to="2026-08-28")]})
    assert rt.load_map(AS_OF, doc=doc)["industries"] == {}
    assert not _errors(doc)
    assert any("过期" in i.message for i in rt.lint_map(doc, as_of=AS_OF) if i.level == rt.LEVEL_WARN)
    # 过期**之前**的那一天仍然消费得到 —— 证明拒的是日期,不是这条本身长得不对。
    assert rt.load_map("2026-08-27", doc=doc)["industries"]["半导体"]


def test_not_yet_effective_item_is_not_consumable():
    doc = _doc({"半导体": [_item(effective_from="2026-09-01")]})
    assert rt.load_map(AS_OF, doc=doc)["industries"] == {}
    assert rt.load_map("2026-09-02", doc=doc)["industries"]["半导体"]


def test_missing_effective_from_rejected():
    doc = _doc({"半导体": [_item(effective_from=None)]})
    assert rt.load_map(AS_OF, doc=doc)["industries"] == {}
    assert any("effective_from" in e.message for e in _errors(doc))


def test_inverted_validity_range_is_error():
    doc = _doc({"半导体": [_item(effective_from="2026-08-01", effective_to="2026-07-01")]})
    assert any("倒挂" in e.message for e in _errors(doc))


# ───────────────────────── 证据:pending / 缺失 / 编造 ─────────────────────────


def test_pending_evidence_is_rejected_from_consumption_but_is_not_an_error():
    """「没有真凭证 → 写 null + 标 pending_evidence」是**合法待办**,不是坏文件。

    但它**永远进不了消费** —— 这正是纪律的全部意义:编造一个 URL 会让「查无实据的关系」
    长得和「有一手凭证的关系」一模一样,而那类错误没有任何自然告警。
    """
    doc = _doc({"半导体": [_item(evidence_url=None, status="pending_evidence")]})
    assert rt.load_map(AS_OF, doc=doc)["industries"] == {}
    assert not _errors(doc)
    assert rt.LEVEL_PENDING in _levels(doc)


def test_pending_evidence_with_a_url_is_self_contradictory():
    doc = _doc({"半导体": [_item(evidence_url=URL, status="pending_evidence")]})
    assert any("自相矛盾" in e.message for e in _errors(doc))
    assert rt.load_map(AS_OF, doc=doc)["industries"] == {}


def test_missing_evidence_url_without_pending_marker_is_an_error():
    """声称 active 却拿不出凭证 —— 比 pending 更坏,判 ERROR。"""
    doc = _doc({"半导体": [_item(evidence_url=None)]})
    assert rt.load_map(AS_OF, doc=doc)["industries"] == {}
    assert any("禁止编造" in e.message for e in _errors(doc))


@pytest.mark.parametrize("bad", [
    "file:///etc/passwd",
    "http://localhost/evidence",
    "http://192.168.1.7/evidence",
    "https://user:pw@example.com/x",
    "not-a-url",
])
def test_non_public_evidence_url_rejected(bad):
    doc = _doc({"半导体": [_item(evidence_url=bad)]})
    assert rt.load_map(AS_OF, doc=doc)["industries"] == {}
    assert any(e.level == rt.LEVEL_ERROR for e in _errors(doc))


def test_tracking_params_are_canonicalized_on_load_and_warned_in_lint():
    doc = _doc({"半导体": [_item(evidence_url="https://www.sec.gov/x?utm_source=q&id=3#sec")]})
    got = rt.load_map(AS_OF, doc=doc)["industries"]["半导体"][0]
    assert got["evidence_url"] == "https://www.sec.gov/x?id=3"
    assert any("canonicalize" in i.message for i in rt.lint_map(doc, as_of=AS_OF))


# ───────────────────────── 可交易状态 ─────────────────────────


@pytest.mark.parametrize("kw", [
    {"status": "delisted"},
    {"status": "suspended"},
    {"status": "halted"},
    {"tradable": False},
])
def test_untradable_ticker_rejected(kw):
    doc = _doc({"半导体": [_item(**kw)]})
    assert rt.load_map(AS_OF, doc=doc)["industries"] == {}


def test_tradable_false_is_a_lint_error():
    doc = _doc({"半导体": [_item(tradable=False)]})
    assert any("不可交易" in e.message for e in _errors(doc))


def test_resolver_reported_untradable_is_an_error():
    doc = _doc({"半导体": [_item()]})
    resolver = lambda sym: {"quote_type": "EQUITY", "exchange": "NMS",   # noqa: E731
                            "currency": "USD", "tradable": False}
    assert any("不可交易" in e.message for e in _errors(doc, resolver=resolver))


def test_unknown_status_is_an_error():
    doc = _doc({"半导体": [_item(status="probably_fine")]})
    assert any("status=" in e.message for e in _errors(doc))


# ───────────────── ② ETF / 指数不得伪装成公司或财报主体 ─────────────────


def test_etf_carrying_earnings_fields_is_rejected():
    """ETF 没有财报。给它挂 `next_earnings_date` = 把一篮子伪装成一家公司(§8 规则二)。"""
    doc = _doc({"半导体": [_item(symbol="SMH", kind="etf", relation="theme", direction="peer",
                                next_earnings_date="2026-09-10")]})
    assert rt.load_map(AS_OF, doc=doc)["industries"] == {}
    assert any("财报主体" in e.message for e in _errors(doc))


def test_index_carrying_implied_move_is_rejected():
    doc = _doc({"半导体": [_item(symbol="^SOX", kind="index", relation="theme", direction="peer",
                                implied_move_note="±4%")]})
    assert rt.load_map(AS_OF, doc=doc)["industries"] == {}
    assert any("财报主体" in e.message for e in _errors(doc))


def test_company_may_carry_earnings_fields():
    doc = _doc({"半导体": [_item(next_earnings_date="2026-09-10")]})
    assert rt.load_map(AS_OF, doc=doc)["industries"]["半导体"]
    assert not _errors(doc)


def test_resolver_catches_an_etf_declared_as_company():
    """真身是 ETF 却写 `kind: company` —— 只有解析器逮得住(文件本身自洽)。"""
    doc = _doc({"半导体": [_item(symbol="SMH")]})
    resolver = lambda sym: {"quote_type": "ETF", "exchange": "PCX", "currency": "USD"}  # noqa: E731
    errs = _errors(doc, resolver=resolver)
    assert any("quote_type" in e.message and "伪装" in e.message for e in errs)


def test_resolver_catches_wrong_currency_and_exchange():
    doc = _doc({"半导体": [_item(symbol="0700.HK")]})
    resolver = lambda sym: {"quote_type": "EQUITY", "exchange": "HKG", "currency": "HKD"}  # noqa: E731
    errs = _errors(doc, resolver=resolver)
    assert any("币种" in e.message for e in errs)
    assert any("交易所" in e.message for e in errs)


def test_resolver_miss_is_an_error_and_index_skips_exchange_check():
    doc = _doc({"半导体": [
        _item(symbol="^SOX", kind="index", relation="theme", direction="peer"),
        _item(symbol="ZZZZ"),
    ]})
    table = {"^SOX": {"quote_type": "INDEX", "exchange": "", "currency": "USD"}}
    errs = _errors(doc, resolver=table.get)
    assert [e.where for e in errs if "解析不到" in e.message] == ["industries.半导体[1] ZZZZ"]


# ───────────────────────── 枚举 ─────────────────────────


@pytest.mark.parametrize("kw", [
    {"kind": "fund"},
    {"relation": "competitor"},
    {"direction": "sideways"},
    {"symbol": ""},
])
def test_bad_enums_rejected(kw):
    doc = _doc({"半导体": [_item(**kw)]})
    assert rt.load_map(AS_OF, doc=doc)["industries"] == {}
    assert _errors(doc)


# ───────────────────────── ③ code 优先 / ≤4 / 空即省略 ─────────────────────────


def test_code_beats_industry():
    """命中 code 就**只用** code 名单 —— 不与行业名单合并、不追加。"""
    doc = _doc(
        industries={"消费电子": [_item(symbol="AAPL"), _item(symbol="QCOM")]},
        codes={"300857": [_item(symbol="NVDA", relation="supplier", direction="upstream")]},
    )
    assert [x["symbol"] for x in
            rt.mappings_for(AS_OF, code="300857", industry="消费电子", doc=doc)] == ["NVDA"]
    # code 没有名单时才回落到行业
    assert [x["symbol"] for x in
            rt.mappings_for(AS_OF, code="000001", industry="消费电子", doc=doc)] == ["AAPL", "QCOM"]
    # 短码 / 丢前导零也要命中(zfill 家训)
    doc2 = _doc(codes={"000001": [_item(symbol="AAPL", relation="peer", direction="peer")]})
    assert rt.mappings_for(AS_OF, code="1", doc=doc2)


def test_code_layer_falls_back_to_industry_when_its_items_all_expired():
    """code 名单存在但全过期 → 回落行业名单(过滤发生在「优先」之前)。"""
    doc = _doc(
        industries={"消费电子": [_item(symbol="AAPL")]},
        codes={"300857": [_item(symbol="NVDA", effective_to="2026-01-01")]},
    )
    assert [x["symbol"] for x in
            rt.mappings_for(AS_OF, code="300857", industry="消费电子", doc=doc)] == ["AAPL"]


def test_more_than_four_per_layer_is_an_error_and_load_truncates():
    five = [_item(symbol=s) for s in ("A", "B", "C", "D", "E")]
    doc = _doc({"半导体": five})
    assert any("单层上限" in e.message for e in _errors(doc))
    got = rt.load_map(AS_OF, doc=doc)["industries"]["半导体"]
    assert [x["symbol"] for x in got] == ["A", "B", "C", "D"]        # 保文件顺序,截前 4


def test_duplicate_symbol_in_one_layer_is_an_error():
    doc = _doc({"半导体": [_item(symbol="NVDA"), _item(symbol="nvda")]})
    assert any("重复 symbol" in e.message for e in _errors(doc))


def test_empty_layer_is_omitted_not_empty_list():
    """空名单 = 整块省略(pack 据「键在不在」决定渲不渲染,空 list 会渲成一张空表)。"""
    doc = _doc({"半导体": [_item(evidence_url=None, status="pending_evidence")], "电池": []})
    m = rt.load_map(AS_OF, doc=doc)
    assert m["industries"] == {} and m["codes"] == {}
    assert rt.mappings_for(AS_OF, industry="半导体", doc=doc) == []


def test_unquoted_code_key_is_an_error():
    """YAML 里不加引号的 `000001` 会被读成整数 1 —— 永远命中不了任何 A 股代码。"""
    doc = _doc(codes={1: [_item()]})
    assert any("带引号" in e.message for e in _errors(doc))


# ───────────────────────── 顶层 schema ─────────────────────────


def test_top_level_schema_checks():
    assert any("version" in e.where for e in _errors(_doc(version=1)))
    assert any("owner" in e.where for e in _errors(_doc(owner="")))
    assert any("reviewed_at" in e.where for e in _errors(_doc(reviewed_at="not-a-date")))
    stale = rt.lint_map(_doc(reviewed_at="2026-01-01"), as_of=AS_OF)
    assert any("季度复核" in i.message for i in stale if i.level == rt.LEVEL_WARN)


def test_unparseable_as_of_keeps_evidence_gate_but_skips_dates():
    """as_of 不是日期 → 无法判有效期,只保枚举 / 证据门(与 `sector/pack.py::_rt_valid` 同口径)。"""
    doc = _doc({"半导体": [_item(effective_to="2020-01-01"),
                          _item(symbol="X", evidence_url=None, status="pending_evidence")]})
    got = rt.load_map("", doc=doc)["industries"]["半导体"]
    assert [x["symbol"] for x in got] == ["NVDA"]        # 过期项漏过(已知),pending 仍被拒


def test_compact_as_of_is_accepted():
    doc = _doc({"半导体": [_item(effective_to="2026-08-28")]})
    assert rt.load_map("20260829", doc=doc)["industries"] == {}
    assert rt.load_map("20260827", doc=doc)["industries"]["半导体"]


# ───────────────────────── 离线保证 / 契约形状 ─────────────────────────


def test_default_resolver_is_offline():
    """模块级默认解析器必须是 None —— 否则 lint 会在单测里偷偷联网(测试禁止真网络)。"""
    assert rt.RESOLVER is None
    doc = _doc({"半导体": [_item()]})
    issues = rt.lint_map(doc, as_of=AS_OF)
    assert not rt.lint_errors(issues)
    assert any("未做 ticker 解析" in i.message for i in issues)


def test_resolver_is_actually_called_when_given():
    seen = []

    def resolver(sym):
        seen.append(sym)
        return {"quote_type": "EQUITY", "exchange": "NMS", "currency": "USD"}

    rt.lint_map(_doc({"半导体": [_item(symbol="NVDA"), _item(symbol="AMAT")]}),
                as_of=AS_OF, resolver=resolver)
    assert seen == ["NVDA", "AMAT"]


def test_resolver_exception_is_an_error_not_a_crash():
    def boom(sym):
        raise RuntimeError("network down")

    assert any("解析失败" in e.message for e in _errors(_doc({"半导体": [_item()]}), resolver=boom))


def test_load_map_only_carries_fields():
    """§8 规则一的前提:`load_map` 只搬字段,**不合成任何方向措辞**。"""
    doc = _doc({"半导体": [_item()]})
    got = rt.load_map(AS_OF, doc=doc)["industries"]["半导体"][0]
    assert got["symbol"] == "NVDA" and got["relation"] == "customer"
    assert got["layer"] == "industries" and got["layer_key"] == "半导体"
    blob = " ".join(str(v) for v in got.values())
    for banned in ("涨", "跌", "利好", "利空", "看多", "看空", "buy", "sell"):
        assert banned not in blob.lower()


def test_load_map_shape_matches_sector_pack_contract():
    """跨模块契约钉:`load_map` 放行的每一条,`sector/pack.py::_rt_valid` 也必须放行。

    pack 是**独立**再验一遍(它不信任上游筛过)。两边口径若漂移,pack 会静默地把合法条目
    全丢掉 —— 表现为「映射块永远不出现」,而不是任何报错。
    """
    pack = pytest.importorskip("autoresearch.sector.pack")
    doc = _doc(
        industries={"半导体": [_item(symbol="SMH", kind="etf", relation="theme", direction="peer"),
                              _item(symbol="NVDA")]},
        codes={"300857": [_item(symbol="NVDA", relation="supplier", direction="upstream")]},
    )
    m = rt.load_map(AS_OF, doc=doc)
    items = [it for layer in ("industries", "codes") for lst in m[layer].values() for it in lst]
    assert len(items) == 3
    for it in items:
        assert pack._rt_valid(it, AS_OF), f"pack 拒了 load_map 放行的条目:{it}"


# ───────────────────────── 入库文件本身 ─────────────────────────


def test_shipped_yaml_lints_clean():
    issues = rt.lint_map(as_of=AS_OF)
    assert not rt.lint_errors(issues), rt.format_issues(rt.lint_errors(issues))


def test_shipped_yaml_is_all_pending_so_nothing_is_consumable():
    """初版**每一条都没有真凭证** → 一条也不该进入消费。

    这条钉子的用途在**将来**:谁给某条填了 `evidence_url` 却忘了把 `status` 改掉(或反过来),
    本测试会红。它不是在锁「映射表必须永远为空」,而是锁「消费口与证据口必须同时翻」。
    """
    raw = rt.load_raw()
    items = [it for layer in ("industries", "codes")
             for lst in (raw.get(layer) or {}).values() for it in lst]
    assert items, "映射表空了?初版应有 10 个行业 + 持仓 code"
    assert all(it.get("status") == rt.STATUS_PENDING_EVIDENCE for it in items)
    assert all(it.get("evidence_url") is None for it in items)
    m = rt.load_map(AS_OF)
    assert m["industries"] == {} and m["codes"] == {}


def test_shipped_yaml_covers_the_pinned_holding_and_at_most_ten_industries():
    raw = rt.load_raw()
    assert "300857" in (raw.get("codes") or {}), "📌 持仓 300857 必须在 codes 层"
    assert 0 < len(raw.get("industries") or {}) <= 10, "初版覆盖 ≤10 个行业(§8)"
    assert raw.get("version") == rt.SCHEMA_VERSION and raw.get("owner")
    for key, items in (raw.get("industries") or {}).items():
        assert len(items) <= rt.MAX_PER_LAYER, f"{key} 超过单层上限"
