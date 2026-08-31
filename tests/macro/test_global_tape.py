"""宏观 full 的外源三段(D-4:波动率地形 / 隔夜 tape / 未来 7 日海外日历)。

spec: `docs/specs/2026-08-28-external-evidence-expansion-design.md` §5.3 + §11 D-4。

四条边界,每条都有一个会变红的探针:

1. **全 B 级**:任一源失败 → 该段整块省略 + 降级记账 + **永不抛**;
   「源成功但真空」与「请求 / 解析失败」在账上必须长得不一样。
2. **只写不读**:`global_tape.json` 与 `macro_state` 的新字段是研究 / 审计产物;
   `strategist_pack.ALLOWED_KEYS` 里**一个新键都不许有**(它是整块投影 —— 写进
   `macro_state.json` 就等于策略师看得见,那已经是受冻结的 B-1)。
3. **ZQ 只有一种读法**:`100−价` = 合约月平均有效利率。渲染层连「会议 / 降息 /
   隐含变动」这类字样都不许出现。
4. **不猜时刻 / 不猜分位**:`DATE_ONLY` 明写无时刻;VIX 分位样本不足只写「样本不足(n)」。

零网络:`fetch_global_tape` 注入合成历史,日历源全部 monkeypatch。
"""

from __future__ import annotations

import json

import pandas as pd
import pytest

from autoresearch.common import workspace as ws
from autoresearch.data import contracts
from autoresearch.data.sources import yf_tape
from autoresearch.macro import harvest, state

DATE = "2026-08-27"


# ───────────────────────── 合成 tape ─────────────────────────

_LEVELS = {
    "^VIX": 15.0, "^VIX3M": 18.0, "^SKEW": 140.0, "^MOVE": 90.0,
    "^GSPC": 6100.0, "^NDX": 22000.0, "^SOX": 5800.0, "^HSI": 25000.0,
    "000001.SS": 3400.0, "DX-Y.NYB": 98.0, "^TNX": 42.0, "CL=F": 63.0,
    "GC=F": 3400.0, "HG=F": 4.5, "USDCNH=X": 7.12, "KWEB": 40.0, "FXI": 39.0,
    "ASHR": 30.0, "SMH": 300.0, "XLK": 270.0, "ZQ=F": 96.22, "SR3=F": 96.0,
}


def _close(symbol: str, bars: int = 300) -> float:
    """合成历史的最后一根收盘 —— 断言拿它当真值,不写死数字。"""
    return _LEVELS[symbol] * (1 + 0.0004 * (bars - 1))


def _history(bars: int = 300):
    """注入型取数器:每个标的一条平滑的历史,`^VIX` 收在自己一年区间的高位。"""
    dates = pd.bdate_range("2025-06-02", periods=bars).strftime("%Y-%m-%d").tolist()

    def fetch(symbol: str) -> pd.DataFrame:
        base = _LEVELS.get(symbol, 100.0)
        closes = [base * (1 + 0.0004 * i) for i in range(bars)]
        return pd.DataFrame({"date": dates, "close": closes})

    return fetch


def _tape(monkeypatch, *, bars: int = 300, now=None):
    """让 `harvest.global_tape_payload` 走**真的** `fetch_global_tape`,只把取数注入掉。"""
    real = yf_tape.fetch_global_tape

    def patched(as_of=None, **kwargs):
        kwargs.setdefault("history", _history(bars))
        kwargs.setdefault("record", False)
        if now is not None:
            kwargs.setdefault("now", now)
        return real(as_of, **kwargs)

    monkeypatch.setattr(yf_tape, "fetch_global_tape", patched)


@pytest.fixture(autouse=True)
def _clean_degradations():
    contracts.clear_degradations()
    yield
    contracts.clear_degradations()


# ───────────────────────── 1. 三段落在 data.md,顺序稳定 ─────────────────────────


def test_the_three_sections_land_in_data_md_in_a_stable_order(monkeypatch, tmp_path):
    """`main()` 端到端(零网络):三段有序落在跨资产之后、A股中观之前。"""
    monkeypatch.setattr(ws, "context_root", lambda: tmp_path / "context_codex")
    monkeypatch.setattr(harvest, "set_config", lambda *_a, **_k: None)
    for name in ("us_macro_block", "china_macro_block", "global_macro_block",
                 "cross_asset_block", "meso_ashare_best"):
        monkeypatch.setattr(harvest, name, lambda _d, _n=name: f"_{_n} stub_")
    _tape(monkeypatch)
    monkeypatch.setattr(harvest, "overseas_calendar_payload",
                        lambda *_a, **_k: _CALENDAR_OK)
    monkeypatch.setattr("sys.argv", ["harvest", DATE])

    assert harvest.main() == 0

    body = (tmp_path / "context_codex" / "macro" / DATE / "data.md").read_text(encoding="utf-8")
    positions = [body.index(f"## {title}") for title in harvest.EXTERNAL_SECTION_TITLES]
    assert positions == sorted(positions), "三段顺序必须固定(playbook 与测试按标题定位)"
    assert body.index("Cross-asset") < positions[0] < body.index("A股中观")
    assert (tmp_path / "context_codex" / "macro" / DATE / harvest.GLOBAL_TAPE_JSON).is_file()


def test_external_sections_are_presence_gated_per_leg():
    assert harvest.external_sections({"ok": False}, {"ok": False}) == []
    only_tape = harvest.external_sections({"ok": True, "rows": [], "derived": {}}, {"ok": False})
    assert [t for t, _ in only_tape] == [harvest.SEC_VOL, harvest.SEC_TAPE]
    only_cal = harvest.external_sections({"ok": False}, _CALENDAR_OK)
    assert [t for t, _ in only_cal] == [harvest.SEC_CAL]


# ───────────────────────── 2. tape 派生量必须真的到达 payload ─────────────────────


def test_derived_numbers_reach_the_payload_from_the_real_source(monkeypatch):
    """接线锁(FN-1 家训):派生量放在 `df.attrs["derived"]` 里,别只看顶层 attrs。

    读不到 = `vix_term_ratio` / `zq_front_month_avg_rate` 恒 None、VIX 分位恒「样本不足」,
    而报告照常出、账上一声不吭 —— 死了也像活着。
    """
    _tape(monkeypatch)
    payload = harvest.global_tape_payload(DATE)

    assert payload["ok"] is True
    derived = payload["derived"]
    assert derived["vix_term_ratio"] == pytest.approx(
        _close("^VIX") / _close("^VIX3M"), rel=1e-3)
    assert derived["zq_front_month_avg_rate"] == pytest.approx(100.0 - _close("ZQ=F"), rel=1e-4)
    assert derived["vix_pct_1y"] is not None
    assert derived["vix_pct_1y_n"] and derived["vix_pct_1y_n"] >= yf_tape.MIN_PCTILE_OBS

    numbers = payload["numbers"]
    assert set(numbers) == set(harvest.MACRO_STATE_TAPE_KEYS)
    assert numbers["vix"] == pytest.approx(_close("^VIX"), rel=1e-4)
    assert numbers["vix_term_ratio"] == derived["vix_term_ratio"]
    assert numbers["zq_front_month_avg_rate"] == derived["zq_front_month_avg_rate"]
    # `^TNX` 原始报价原样搬,不做任何乘除(单位说明随 json 落盘)。
    assert numbers["ust10y"] == pytest.approx(_close("^TNX"), rel=1e-4)


def test_overnight_tape_table_follows_the_spec_order_and_keeps_extras(monkeypatch):
    _tape(monkeypatch)
    payload = harvest.global_tape_payload(DATE)
    table = harvest.overnight_tape_block(payload)

    listed = [line.split("|")[1].strip().strip("`") for line in table.splitlines()
              if line.startswith("| `")]
    spec = [s for s in harvest.GLOBAL_TAPE_SYMBOLS if s in {r["symbol"] for r in payload["rows"]}]
    assert listed[: len(spec)] == spec
    # 源多给的(ZQ=F / SR3=F / HG=F)不丢,按帧序附在表尾。
    assert set(listed) == {r["symbol"] for r in payload["rows"]}
    assert "3.5 = +3.5%" in table                       # Δ 单位写死,防「百分点 vs 百分比」


def test_vix_percentile_says_sample_insufficient_with_its_n(monkeypatch):
    """不足 60 观测就不给分位 —— 一个基于 12 个观测的「分位」比不给更坏。"""
    _tape(monkeypatch, bars=12)
    payload = harvest.global_tape_payload(DATE)
    block = harvest.vol_positioning_block(payload)

    assert payload["derived"]["vix_pct_1y"] is None
    assert payload["derived"]["vix_pct_1y_n"] == 12
    assert "样本不足(n=12" in block


# ───────────────────────── 3. ZQ 的口径禁忌 ─────────────────────────


FORBIDDEN_ZQ_WORDS = ("会议", "降息", "加息", "隐含变动")


def test_zq_rendering_never_speaks_of_a_meeting_or_a_rate_move(monkeypatch):
    _tape(monkeypatch)
    payload = harvest.global_tape_payload(DATE)
    block = harvest.vol_positioning_block(payload)

    assert harvest.ZQ_LABEL in block
    for word in FORBIDDEN_ZQ_WORDS:
        assert word not in block, word


def test_forbidden_words_stay_out_of_the_whole_external_output(monkeypatch, tmp_path):
    _tape(monkeypatch)
    tape = harvest.global_tape_payload(DATE)
    rendered = "\n".join(body for _, body in harvest.external_sections(tape, _CALENDAR_OK))
    path = harvest.write_global_tape_json(tmp_path, tape, _CALENDAR_OK)
    on_disk = path.read_text(encoding="utf-8")

    for word in FORBIDDEN_ZQ_WORDS:
        assert word not in rendered, f"渲染层出现禁忌措辞:{word}"
        assert word not in on_disk, f"机读产物出现禁忌措辞:{word}"


# ───────────────────────── 4. 日历:DATE_ONLY / 真空 / 失败 ─────────────────────────


class _Event:
    """`ExternalEvent` 的最小替身(harvest 只按属性名取字段)。"""

    def __init__(self, **kw):
        for key, value in kw.items():
            setattr(self, key, value)


_CALENDAR_OK = {
    "as_of": DATE, "ok": True, "status": "ok",
    "window": {"start": DATE, "end": "2026-09-03", "days": 7},
    "sources": {"fred_releases": "ok(1 条)", "fomc": "ok(1 条)"},
    "events": [
        {"event_id": "fred:10:2026-08-28", "revision": "r1", "local_date": "2026-08-28",
         "time_quality": "DATE_ONLY", "event_type": "macro_release", "subject": "CPI",
         "scheduled_at_utc": None, "timezone": "America/New_York", "status": "scheduled",
         "source_url": "https://fred.stlouisfed.org/release?rid=10"},
        {"event_id": "fomc:2026-09-02", "revision": "r1", "local_date": "2026-09-02",
         "time_quality": "TIMED", "event_type": "fomc", "subject": "FOMC 声明",
         "scheduled_at_utc": "2026-09-02T18:00:00+00:00", "timezone": "America/New_York",
         "status": "scheduled", "source_url": "https://www.federalreserve.gov/x"},
    ],
}


def test_date_only_rows_say_out_loud_that_they_have_no_clock():
    block = harvest.overseas_calendar_block(_CALENDAR_OK)

    assert "DATE_ONLY(仅日期,无时刻)" in block
    assert "TIMED(2026-09-02T18:00 UTC)" in block
    assert "不得当精确时点" in block and "BMO/AMC" in block


def _patch_calendar(monkeypatch, *, releases=None, fomc=None, fail_fred=False, fail_fomc=False):
    import types

    fred = types.ModuleType("autoresearch.data.sources.fred_calendar")

    class _Outcome:
        def __init__(self, status, reason=""):
            self.status, self.reason = status, reason
            self.rows = pd.DataFrame()

    def fetch_events(start, end, **_kw):
        if fail_fred:
            raise RuntimeError("FRED 限流")
        return list(releases or []), _Outcome("OK" if releases else "EMPTY")

    def fetch_releases(start, end, **_kw):
        if fail_fred:
            raise RuntimeError("FRED 限流")
        return pd.DataFrame(
            [{"release_id": "10", "release_name": "Consumer Price Index", "date": "2026-08-28"}]
        )

    fred.fetch_events = fetch_events
    fred.fetch_releases = fetch_releases
    monkeypatch.setitem(__import__("sys").modules,
                        "autoresearch.data.sources.fred_calendar", fred)

    cal = types.ModuleType("autoresearch.data.sources.fomc_calendar")

    def load_fomc(year, **_kw):
        if fail_fomc:
            raise RuntimeError("年表缺席")
        return [e for e in (fomc or []) if str(e.local_date)[:4] == str(year)]

    cal.load_fomc = load_fomc
    monkeypatch.setitem(__import__("sys").modules,
                        "autoresearch.data.sources.fomc_calendar", cal)


def _fred_event(day="2026-08-28"):
    return _Event(event_id=f"fred:10:{day}", revision="fred-releases", local_date=day,
                  time_quality="DATE_ONLY", event_type="macro_release",
                  subject="Consumer Price Index", scheduled_at_utc=None,
                  timezone="America/New_York", status="scheduled",
                  source_url="https://fred.stlouisfed.org/release?rid=10")


def _fomc_event(day="2026-09-02"):
    return _Event(event_id=f"fomc:{day}", revision="initial", local_date=day,
                  time_quality="DATE_ONLY", event_type="fomc", subject="FOMC 会期",
                  scheduled_at_utc=None, timezone="America/New_York", status="scheduled",
                  source_url="https://www.federalreserve.gov/x")


def test_the_fred_leg_yields_real_events_not_column_names(monkeypatch):
    """FRED 那条腿必须产出**事件**。

    走原始帧的话 `list(df)` 拿到的是**列名**:每一条都过不了日期正则被静默丢掉,
    于是日历里永远没有 FRED,而 `sources` 那行还理直气壮地写「ok(3 条)」——
    「数行数 ≠ 数事件」的教科书版本,且数的还是列。
    """
    _patch_calendar(monkeypatch, releases=[_fred_event()], fomc=[_fomc_event()])

    payload = harvest.overseas_calendar_payload(DATE, days=7)

    assert payload["ok"] is True
    subjects = [e["subject"] for e in payload["events"]]
    assert "Consumer Price Index" in subjects and "FOMC 会期" in subjects
    assert payload["sources"]["fred_releases"].startswith("ok(1")
    assert all(e["time_quality"] in {"TIMED", "DATE_ONLY", "UNKNOWN"} for e in payload["events"])


def test_events_outside_the_window_are_dropped_and_rows_are_deduped(monkeypatch):
    _patch_calendar(
        monkeypatch,
        releases=[_fred_event(), _fred_event(), _fred_event("2026-10-01")],
        fomc=[],
    )
    payload = harvest.overseas_calendar_payload(DATE, days=7)

    assert [e["local_date"] for e in payload["events"]] == ["2026-08-28"]


def test_empty_window_is_a_legit_empty_not_a_failure(monkeypatch):
    _patch_calendar(monkeypatch, releases=[], fomc=[])
    payload = harvest.overseas_calendar_payload(DATE, days=7)

    assert payload["ok"] is True and payload["status"] == "empty"
    kinds = {r["kind"] for r in contracts.degradations() if r["endpoint"] == "overseas_calendar"}
    assert kinds == {"legit_empty"}                      # 合法空留痕但不进告警面
    assert "源成功但真空" in harvest.overseas_calendar_block(payload)


def test_both_calendar_sources_failing_omits_the_section_and_books_the_degradation(monkeypatch):
    _patch_calendar(monkeypatch, fail_fred=True, fail_fomc=True)

    payload = harvest.overseas_calendar_payload(DATE, days=7)          # 不抛

    assert payload["ok"] is False and payload["events"] == []
    assert harvest.external_sections({"ok": False}, payload) == []
    endpoints = {r["endpoint"] for r in contracts.degradations()}
    assert {"fred_calendar", "fomc_calendar"} <= endpoints


def test_one_source_failing_still_produces_the_section(monkeypatch):
    _patch_calendar(monkeypatch, releases=[_fred_event()], fail_fomc=True)
    payload = harvest.overseas_calendar_payload(DATE, days=7)

    assert payload["ok"] is True and len(payload["events"]) == 1
    assert payload["sources"]["fomc"].startswith("失败")


def test_unparsable_as_of_never_raises():
    payload = harvest.overseas_calendar_payload("not-a-date")
    assert payload["ok"] is False and "不可解析" in payload["reason"]


# ───────────────────────── 5. tape 取数失败 = B 级 ─────────────────────────


def test_tape_failure_omits_both_sections_books_it_and_never_raises(monkeypatch):
    def boom(*_a, **_k):
        raise RuntimeError("yfinance 挂了")

    monkeypatch.setattr(yf_tape, "fetch_global_tape", boom)

    payload = harvest.global_tape_payload(DATE)                        # 不抛

    assert payload["ok"] is False and payload["status"] == "failure"
    assert "取数失败" in payload["reason"]
    assert harvest.external_sections(payload, {"ok": False}) == []
    assert any(r["endpoint"] == "global_tape" for r in contracts.degradations())


def test_empty_frame_is_recorded_apart_from_a_hard_failure(monkeypatch):
    monkeypatch.setattr(yf_tape, "fetch_global_tape", lambda *_a, **_k: pd.DataFrame())

    payload = harvest.global_tape_payload(DATE)

    assert payload["ok"] is False and payload["status"] == "empty"     # ≠ "failure"
    assert "空帧" in payload["reason"]


def test_failed_tape_still_lands_on_disk_with_its_reason(monkeypatch, tmp_path):
    monkeypatch.setattr(yf_tape, "fetch_global_tape",
                        lambda *_a, **_k: (_ for _ in ()).throw(RuntimeError("boom")))
    tape = harvest.global_tape_payload(DATE)
    path = harvest.write_global_tape_json(tmp_path, tape, {"ok": False})

    saved = json.loads(path.read_text(encoding="utf-8"))
    assert saved["ok"] is False
    assert saved["tape"]["reason"] and saved["macro_state_numbers"] == {}


# ───────────────────────── 6. as_of 对齐 + 只写不读 ─────────────────────────


def test_global_tape_json_and_macro_state_share_one_as_of(monkeypatch, tmp_path):
    _tape(monkeypatch)
    root = tmp_path / "macro" / DATE
    (root / "1_spine").mkdir(parents=True)
    (root / "1_spine" / "decision.md").write_text(
        "- 权益: **Rating**: Hold\n", encoding="utf-8")
    monkeypatch.setattr(state, "_regime_from_scan_meta", lambda *_a, **_k: "range")

    tape = harvest.global_tape_payload(DATE)
    harvest.write_global_tape_json(root, tape, {"ok": False})
    written = state.write_macro_state(root, out_dir=tmp_path / "out")

    on_disk = json.loads((root / harvest.GLOBAL_TAPE_JSON).read_text(encoding="utf-8"))
    assert written["global_tape_asof"] == on_disk["as_of"] == DATE
    assert set(written["global_tape"]) == set(state.TAPE_NUMBER_KEYS)
    assert written["global_tape"]["vix"] == on_disk["macro_state_numbers"]["vix"]
    assert set(state.TAPE_NUMBER_KEYS) == set(harvest.MACRO_STATE_TAPE_KEYS)


def test_a_failed_tape_never_masquerades_as_a_reading(monkeypatch, tmp_path):
    root = tmp_path / "macro" / DATE
    (root / "1_spine").mkdir(parents=True)
    (root / "1_spine" / "decision.md").write_text("- 权益: **Rating**: Hold\n", encoding="utf-8")
    monkeypatch.setattr(state, "_regime_from_scan_meta", lambda *_a, **_k: "range")
    harvest.write_global_tape_json(root, {"as_of": DATE, "ok": False, "numbers": {}},
                                   {"ok": False})

    written = state.write_macro_state(root, out_dir=tmp_path / "out")

    assert written["global_tape_asof"] is None and written["global_tape"] == {}


def test_strategist_allowlist_contains_no_external_key():
    """B 类泄漏守卫:`ALLOWED_KEYS` 是整块投影,加一个键就等于解冻 B-1。"""
    from autoresearch.scan import strategist_pack

    forbidden = {"global_tape", "global_tape_asof", "overseas_calendar", "readthrough",
                 "us_evidence", "vix", "derivatives", *state.TAPE_NUMBER_KEYS}
    assert not (forbidden & set(strategist_pack.ALLOWED_KEYS))


def test_load_macro_state_strips_the_write_only_tape(monkeypatch, tmp_path):
    """写进 `macro_state.json` == 策略师看得见(frame 把返回值整块塞进 market_pack)。

    所以「只写不读」必须在返回值上做掉,而不是靠谁记得别读。
    """
    out = tmp_path / "out"
    out.mkdir()
    (out / state.STATE_NAME).write_text(json.dumps({
        "as_of": DATE, "ttl_days": 7, "regime_at_run": "range",
        "cross_asset": {}, "ashare_sectors": {},
        "global_tape_asof": DATE, "global_tape": {"vix": 15.0},
    }, ensure_ascii=False), encoding="utf-8")

    loaded, why = state.load_macro_state("2026-08-28", "range", path=out / state.STATE_NAME)

    assert loaded is not None and "新鲜" in why
    assert "global_tape" not in loaded and "global_tape_asof" not in loaded
    # 落盘那份一个字节没动 —— full 档作者与审计仍读得到。
    raw = json.loads((out / state.STATE_NAME).read_text(encoding="utf-8"))
    assert raw["global_tape"] == {"vix": 15.0}


def test_the_json_states_its_units_and_consumption_boundary(monkeypatch, tmp_path):
    _tape(monkeypatch)
    tape = harvest.global_tape_payload(DATE)
    saved = json.loads(
        harvest.write_global_tape_json(tmp_path, tape, {"ok": False}).read_text(encoding="utf-8"))

    assert saved["units"]["ust10y"].startswith("`^TNX` 原始报价")
    assert "不进 scan 判断层" in saved["consumption_boundary"]
    assert saved["pct_unit"] == "percent"
