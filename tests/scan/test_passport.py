"""候选护照(F2-I):L1→L4 全轨迹的确定性派生视图。

护照是**纯派生**的:只读既有产物、不改任何上游。所以本文件的断言全部是「护照说的
话必须与源产物逐字对得上」,而不是「护照自己算得对」——后者会把源产物的错也一起
锁进测试里。
"""
from __future__ import annotations

import csv
import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from autoresearch.scan.decision_read_model import read_final_ratings
from autoresearch.scan.decision_record import DecisionRecord, write_decision_records
from autoresearch.scan.passport import (
    PASSPORT_FILENAME,
    PASSPORT_RULE,
    build_passport,
    write_passport,
)
from autoresearch.scan.run_contract import RunContract, write_run_contract

DATE = "2026-08-06"

# ── 合成 fixture:六只票各走一条不同的轨迹 ────────────────────────────────────
# 002345 双通道 → pass1 kept(lane)→ judged finalist → L4 早停卡
# 600188 单通道 → pass1 kept(conviction_guard)→ judged finalist → L4 满卡
# 300750 pinned → pass1 kept(pinned)→ finalist(pinned 注入)→ L4 满卡
# 000034 backfill(前导零 ×2)→ pass1 cut → 止步
# 601112 merit → pass1 cut → 止步
# 605088 lane(风格桶救回)→ pass1 kept(lane)→ judged bench(guard=cap)→ 止步
_CHANNEL_ROWS = [
    ("composite", "600188", 1, 72.6),
    ("composite", "601112", 2, 70.4),
    ("composite", "000034", 3, 69.4),
    ("composite", "605088", 4, 69.1),
    ("healthy", "002345", 1, 88.0),
    ("healthy", "600188", 2, 81.5),
    ("momentum", "002345", 7, 63.25),
    ("value", "605088", 11, 55.5),
    ("heat", "300750", 6, 91.0),
    ("growth", "600123", 9, 44.0),
]
_L2_ROWS = [
    # l2_rank, code, name, industry, composite, recall_channels, n_channels,
    # best_rank, pinned, l2_lane_reserved, selection_reason, selection_detail
    (1, "600188", "兖矿能源", "煤炭开采", 72.6, "composite|healthy", 2, 1,
     False, False, "merit", ""),
    (2, "002345", "潮宏基", "饰品", 68.1, "healthy|momentum", 2, 1,
     False, False, "merit", ""),
    (3, "601112", "振石股份", "玻璃玻纤", 63.5, "composite", 1, 2,
     False, False, "merit", ""),
    (4, "605088", "冠盛股份", "汽车零部件", 60.2, "composite|value", 2, 4,
     False, True, "lane", "价值"),
    (5, "000034", "神州数码", "IT服务Ⅱ", 58.9, "composite", 1, 3,
     False, True, "backfill", ""),
    (6, "300750", "宁德时代", "电池", 55.0, "heat", 1, 6,
     True, True, "pinned", ""),
    # 600123 = finalist(guard=ins75 被救**进**)+ L4 派发过但没出评级(任务失败)
    (7, "600123", "兰花科创", "煤炭开采", 52.4, "growth", 1, 9,
     False, True, "backfill", ""),
]
_KEPT = {  # code → (pass1 selection_reason, pass1 selection_detail)
    "600188": ("conviction_guard", "n_channels=3"),
    "002345": ("lane", "healthy"),
    "605088": ("lane", "value"),
    "300750": ("pinned", ""),
    "600123": ("lane", "growth"),
}
_CUT = ["601112", "000034"]
_JUDGED = {  # code → (conviction, mechanism, lane, triage_lean, finalist)
    "600188": (75, "板块轮动位:煤炭开采当日行业中位涨幅第一。", "healthy", "OW", True),
    "002345": (64, "题材梯队二线,D+1 买家是追热点的量能资金。", "healthy", "N", True),
    "605088": (48, "无接力资金,靠估值修复。", "value", "UW", False),
    "600123": (78, "煤价见底,焦化毛利修复。", "growth", "OW", False),
}
# `guard` 是双向标记:`ins75` 记在被救**进** finalist 的行上(`merge.py:72-75`),
# 它绝不是「为什么在 bench」。600123 就是这一侧。
_FINALIST_GUARD = {"600188": "", "002345": "", "300750": "", "600123": "ins75"}
_BENCH_GUARD = {"605088": "cap"}
_L4 = {  # code → (final_rating, proposal, gate_states, early_stop, intel_avail)
    "600188": ("Hold", "HOLD",
               {"业绩真兑现": "PASS", "主力真在": "FAIL", "估值不透支": "PASS"},
               None, "INTEL"),
    "002345": ("Underweight", "HOLD",
               {"业绩真兑现": "PASS", "主力真在": "FAIL", "估值不透支": "UNKNOWN"},
               {"phase": "P3", "reason": "资金流出"}, "CARD_FALLBACK"),
    "300750": ("Hold", "HOLD", {}, None, "NONE"),
}


def _write_csv(path: Path, header: list[str], rows: list[list]) -> None:
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(header)
        writer.writerows(rows)


def _build_scan(tmp_path: Path, *, with_selection_reason: bool = True) -> Path:
    scan = tmp_path / DATE
    scan.mkdir(parents=True)

    _write_csv(scan / "L1_channels.csv",
               ["channel", "code", "channel_rank", "channel_score"],
               [list(row) for row in _CHANNEL_ROWS])

    scored = []
    for rank, (_, code, name, industry, composite, *_rest) in enumerate(
            sorted(_L2_ROWS, key=lambda r: -r[4]), start=1):
        scored.append([rank, True, code, name, industry, composite])
    _write_csv(scan / "L1_scored_full.csv",
               ["rank", "recalled", "code", "name", "industry", "composite"],
               scored)

    l2_header = ["l2_rank", "gbdt_score", "l2_lane_reserved", "sector_mom", "code",
                 "name", "industry", "composite", "recall_channels", "n_channels",
                 "best_rank", "pinned", "pinned_note"]
    if with_selection_reason:
        l2_header += ["selection_reason", "selection_detail"]
    l2_rows = []
    for (rank, code, name, industry, composite, channels, n_ch, best,
         pinned, reserved, reason, detail) in _L2_ROWS:
        row = [rank, composite, reserved, 1.25, code, name, industry, composite,
               channels, n_ch, best, pinned, ""]
        if with_selection_reason:
            row += [reason, detail]
        l2_rows.append(row)
    _write_csv(scan / "L2_gbdt_top200.csv", l2_header, l2_rows)

    pass1_header = ["code", "name", "gbdt_score", "selection_reason", "selection_detail"]
    _write_csv(scan / "_l3_pass1_kept.csv", pass1_header,
               [[code, _name_of(code), _score_of(code), reason, detail]
                for code, (reason, detail) in _KEPT.items()])
    _write_csv(scan / "_l3_pass1_cut.csv", ["code", "name", "gbdt_score"],
               [[code, _name_of(code), _score_of(code)] for code in _CUT])

    # `_l3_judged.json` 是 **LLM 写的** —— code 完全可能落成 JSON 数字 `34` 而不是
    # 字符串 `"000034"`(`merge.py:111` 就是为这个设的 zfill 防线)。fixture 故意用
    # `int(code)` 写盘,让「护照读 JSON 时不 zfill」这种回归真的会红。
    (scan / "_l3_judged.json").write_text(json.dumps([
        {"code": int(code), "name": _name_of(code), "sector": _sector_of(code),
         "conviction": conviction, "mechanism": mechanism, "lane": lane,
         "triage_lean": lean, "sentiment": "看多", "finalist": finalist}
        for code, (conviction, mechanism, lane, lean, finalist) in _JUDGED.items()
    ], ensure_ascii=False), encoding="utf-8")

    _write_csv(scan / "finalists.csv",
               ["ticker", "code", "name", "sector", "conviction", "lane", "guard"],
               [[f"{code}.SZ", code, _name_of(code), _sector_of(code),
                 _JUDGED.get(code, (0,))[0], "pinned" if code == "300750" else "healthy",
                 guard] for code, guard in _FINALIST_GUARD.items()])
    _write_csv(scan / "_l3_bench.csv", ["code", "name", "conviction", "guard"],
               [[code, _name_of(code), _JUDGED[code][0], guard]
                for code, guard in _BENCH_GUARD.items()])

    contract = RunContract.build(
        analysis_date=DATE, user_config={}, pinned={"kept": [], "expired": []},
        data_policy={"source": "tushare"}, stage_budgets={},
        artifact_schema_versions={}, git_sha="deadbeef",
        now=datetime(2026, 8, 6, 14, 0, tzinfo=timezone.utc),
    )
    write_run_contract(scan / "run_contract.json", contract)
    write_decision_records(scan, [
        DecisionRecord.build(
            analysis_date=DATE, contract_hash=contract.contract_hash, code=code,
            source_rating=rating, rubric_rating=rating, gate_states=gates,
            early_stop=stop, ensemble_ratings=[], final_rating=rating,
            proposal=proposal,
            reason="early_stop" if stop else "rubric",
            evidence_refs=[f"finalists.csv#{code}"],
            first_rejection_stage="L4_P3_EARLY_STOP" if stop else "L4_RUBRIC",
        )
        for code, (rating, proposal, gates, stop, _avail) in _L4.items()
    ])
    # 同理:`_early_stop.json` 的 key 与 intel 状态的 `code` 也走 JSON 数字形态。
    (scan / "_early_stop.json").write_text(json.dumps(
        {int(code): stop for code, (_r, _p, _g, stop, _a) in _L4.items() if stop},
        ensure_ascii=False), encoding="utf-8")
    for code, (_r, _p, _g, _stop, avail) in _L4.items():
        (scan / f"_l4_intel_status_{code}.json").write_text(json.dumps({
            "schema_version": 1, "code": int(code), "acquisition": "FULL",
            "guard": "KEPT", "availability_for_card": avail,
        }, ensure_ascii=False), encoding="utf-8")

    # task book:三只出了评级 + `600123` 派发过但任务失败(无卡无评级)。后者是
    # `l4.dispatched` 唯一的用武之地 —— 它决定 `l4.research_rating` 进不进 `missing[]`。
    (scan / "_l4_tasks.json").write_text(json.dumps({
        "schema_version": 1, "date": DATE,
        "tasks": {**{code: {"code": code, "status": "SUCCEEDED"} for code in _L4},
                  "600123": {"code": "600123", "status": "FAILED"}},
    }, ensure_ascii=False), encoding="utf-8")
    return scan


def _rows_of(path: Path) -> list[dict]:
    with path.open(encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


def _name_of(code: str) -> str:
    return next(row[2] for row in _L2_ROWS if row[1] == code)


def _sector_of(code: str) -> str:
    return next(row[3] for row in _L2_ROWS if row[1] == code)


def _score_of(code: str) -> float:
    return next(row[4] for row in _L2_ROWS if row[1] == code)


# ── ① 每个 L2 票有护照行 ─────────────────────────────────────────────────────
def test_every_l2_row_gets_a_passport(tmp_path):
    scan = _build_scan(tmp_path)
    doc = build_passport(scan)
    l2_codes = {row[1] for row in _L2_ROWS}
    assert set(doc["candidates"]) == l2_codes
    assert doc["rule"] == PASSPORT_RULE
    assert doc["date"] == DATE
    assert doc["counts"]["candidates"] == len(l2_codes)


def test_leading_zero_codes_survive_the_json_round_trip(tmp_path):
    """`002345` → `2345` 是本仓库反复复发的坑,真实风险在 **JSON 侧**(LLM 写的
    `_l3_judged.json` 会把 code 落成数字,`merge.py:111` 专门为它设过 zfill 防线)。

    fixture 用 `int(code)` 写 judged / early_stop / intel_status 三处 —— 少一处 zfill,
    对应的 L3/L4 事实就挂不到 `002345` 这一行上,下面的断言立刻红。
    (旧版本这条测试只看 CSV 往返:两端都是 stdlib csv 读写字符串,零根本没机会丢,
    复核者实跑证实删掉 zfill 后 19 条一条不红。)
    """
    scan = _build_scan(tmp_path)
    doc = build_passport(scan)

    assert all(len(code) == 6 and code.isdigit() for code in doc["candidates"])
    assert {"000034", "002345"} <= set(doc["candidates"])
    assert doc["candidates"]["000034"]["name"] == "神州数码"

    # JSON 里写的是 2345,必须落到 002345 这一行,并且带上它的 L3/L4 事实
    zero_led = doc["candidates"]["002345"]
    assert zero_led["l3"]["judged"] is True, "judged JSON 的整数 code 没 zfill → 挂不上"
    assert zero_led["l3"]["conviction"] == 64
    assert zero_led["l4"]["earlystop_phase"] == "P3", "early_stop JSON 的整数 key 没 zfill"
    assert zero_led["l4"]["intel_avail"] == "CARD_FALLBACK", "intel status 的整数 code 没 zfill"
    # 反向:不许凭空冒出一个丢零的行
    assert "2345" not in doc["candidates"]


def test_pinned_rank_sentinel_matches_universe():
    """哨兵值在两个模块各写一份 —— 上游一改,这条立刻红,不许悄悄漂移。"""
    from autoresearch.scan import passport as mod
    from autoresearch.scan.universe import _PINNED_RANK_SENTINEL

    assert mod._PINNED_RANK_SENTINEL == _PINNED_RANK_SENTINEL


# ── ② per_channel 名次与 L1_channels.csv 对得上 ─────────────────────────────
def test_per_channel_ranks_match_l1_channels_csv(tmp_path):
    scan = _build_scan(tmp_path)
    doc = build_passport(scan)

    expected: dict[str, dict[str, tuple[int, float]]] = {}
    sizes: dict[str, int] = {}
    with (scan / "L1_channels.csv").open(encoding="utf-8", newline="") as handle:
        for row in csv.DictReader(handle):
            code = row["code"].zfill(6)
            sizes[row["channel"]] = sizes.get(row["channel"], 0) + 1
            expected.setdefault(code, {})[row["channel"]] = (
                int(row["channel_rank"]), float(row["channel_score"]))

    assert expected, "fixture 自身必须有逐路召回行,否则本测试无鉴别力"
    for code, per_channel in expected.items():
        got = doc["candidates"][code]["recall"]["per_channel"]
        assert set(got) == set(per_channel), code
        for channel, (rank, score) in per_channel.items():
            assert got[channel]["rank"] == rank, (code, channel)
            assert got[channel]["score"] == pytest.approx(score), (code, channel)
            n = sizes[channel]
            assert got[channel]["pctl"] == pytest.approx(
                round((n - rank + 1) / n, 6)), (code, channel)


def test_recall_channels_and_unique_flag_follow_l1_channels(tmp_path):
    scan = _build_scan(tmp_path)
    doc = build_passport(scan)
    recall_002345 = doc["candidates"]["002345"]["recall"]
    assert recall_002345["channels"] == ["healthy", "momentum"]
    assert recall_002345["n_channels"] == 2
    assert recall_002345["unique"] is False
    assert recall_002345["best_rank"] == 1

    recall_300750 = doc["candidates"]["300750"]["recall"]
    assert recall_300750["channels"] == ["heat"]
    assert recall_300750["unique"] is True


# ── ③ l4.research_rating 与 decision_read_model 一致 ────────────────────────
def test_l4_rating_matches_decision_read_model(tmp_path):
    scan = _build_scan(tmp_path)
    doc = build_passport(scan)
    authoritative = read_final_ratings(scan)
    assert authoritative, "fixture 必须有 decision_records,否则本测试无鉴别力"
    for code, rating in authoritative.items():
        assert doc["candidates"][code]["l4"]["research_rating"] == rating, code
    # 没走到 L4 的票只能是 null —— 不许拿卡片文本或默认值补
    for code in {row[1] for row in _L2_ROWS} - set(authoritative):
        l4 = doc["candidates"][code]["l4"]
        assert l4["carded"] is False
        assert l4["research_rating"] is None


def test_l4_rating_prefers_decision_records_over_legacy_json(tmp_path):
    """正门是 `read_final_ratings` 的优先级链,不是自己去正则解析卡片。"""
    scan = _build_scan(tmp_path)
    (scan / "_final_ratings.json").write_text(
        '{"600188":"Overweight"}', encoding="utf-8")
    doc = build_passport(scan)
    assert doc["candidates"]["600188"]["l4"]["research_rating"] == "Hold"


def test_card_kind_and_intel_come_from_structured_fields(tmp_path):
    scan = _build_scan(tmp_path)
    doc = build_passport(scan)
    stopped = doc["candidates"]["002345"]["l4"]
    assert stopped["card_kind"] == "earlystop"
    assert stopped["earlystop_phase"] == "P3"
    assert stopped["earlystop_reason"] == "资金流出"
    assert stopped["intel_avail"] == "CARD_FALLBACK"

    full = doc["candidates"]["600188"]["l4"]
    assert full["card_kind"] == "full"
    assert full["earlystop_reason"] is None
    assert full["intel_avail"] == "INTEL"


def test_risk_flags_are_derived_from_gate_states_and_early_stop(tmp_path):
    scan = _build_scan(tmp_path)
    doc = build_passport(scan)
    assert doc["candidates"]["600188"]["l4"]["risk_flags"] == ["gate_fail:主力真在"]
    assert doc["candidates"]["002345"]["l4"]["risk_flags"] == [
        "early_stop:P3", "gate_fail:主力真在", "gate_unknown:估值不透支",
        "intel_fallback",
    ]
    assert doc["candidates"]["300750"]["l4"]["risk_flags"] == ["intel_absent"]


# ── ④ backfill 票不冒充 lane ────────────────────────────────────────────────
def test_backfill_never_impersonates_lane(tmp_path):
    scan = _build_scan(tmp_path)
    doc = build_passport(scan)
    backfill = doc["candidates"]["000034"]["l2"]
    assert backfill["selection_reason"] == "backfill"
    assert backfill["selection_detail"] == ""
    # 回填票同样带 lane_reserved=True(merit 核之外的全部),两者语义不同不得互换
    assert backfill["lane_reserved"] is True

    lane = doc["candidates"]["605088"]["l2"]
    assert lane["selection_reason"] == "lane"
    assert lane["selection_detail"] == "价值"
    assert lane["lane_reserved"] is True

    merit = doc["candidates"]["600188"]["l2"]
    assert merit["selection_reason"] == "merit"
    assert merit["lane_reserved"] is False

    assert doc["candidates"]["300750"]["l2"]["selection_reason"] == "pinned"
    assert doc["candidates"]["300750"]["l2"]["pinned"] is True


def test_l2_selection_reason_is_never_taken_from_pass1(tmp_path):
    """`_l3_pass1_kept.csv` 也有一列叫 `selection_reason`,但它是 **pass1** 的词表
    (`conviction_guard` 只在 pass1 出现)。混读会让 L2 的「因何进菜单」变成 L3 的。"""
    scan = _build_scan(tmp_path)
    doc = build_passport(scan)
    entry = doc["candidates"]["600188"]
    assert entry["l2"]["selection_reason"] == "merit"
    assert entry["pass1"]["reason"] == "conviction_guard"
    assert entry["pass1"]["detail"] == "n_channels=3"


# ── ⑤ 重复构建 byte 稳定 ───────────────────────────────────────────────────
def test_repeated_build_is_byte_stable(tmp_path):
    scan = _build_scan(tmp_path)
    first = write_passport(scan).read_bytes()
    second = write_passport(scan).read_bytes()
    assert first == second
    assert first == json.dumps(build_passport(scan), ensure_ascii=False,
                               indent=2, sort_keys=True).encode("utf-8") + b"\n"


def test_duplicate_channel_rows_resolve_order_independently(tmp_path):
    """同 (channel, code) 重复出现是上游异常,但护照不能因为文件行序不同就产出两份
    不同的护照。「后者胜」正是行序敏感的写法;取名次更好的那条才是行序无关的。"""
    scan = _build_scan(tmp_path)
    lines = (scan / "L1_channels.csv").read_text(encoding="utf-8").splitlines()
    dup = ["healthy,002345,3,84.0"]              # 与既有 healthy,002345,1,88.0 重复
    forward = lines + dup
    backward = [lines[0], *dup, *lines[1:]]

    (scan / "L1_channels.csv").write_text("\n".join(forward) + "\n", encoding="utf-8")
    first = build_passport(scan)["candidates"]["002345"]["recall"]
    (scan / "L1_channels.csv").write_text("\n".join(backward) + "\n", encoding="utf-8")
    second = build_passport(scan)["candidates"]["002345"]["recall"]

    assert first == second, "重复行的解析结果依赖了文件行序"
    assert first["per_channel"]["healthy"]["rank"] == 1


def test_build_is_insensitive_to_source_row_order(tmp_path):
    """同一批事实换个行序仍是同一份护照 —— 确定性不能靠源文件恰好有序。"""
    scan = _build_scan(tmp_path)
    baseline = json.dumps(build_passport(scan), ensure_ascii=False, sort_keys=True)

    shuffled = tmp_path / "shuffled" / DATE
    shuffled.parent.mkdir()
    shuffled.mkdir()
    for path in scan.iterdir():
        if path.is_file():
            (shuffled / path.name).write_bytes(path.read_bytes())
    rows = (shuffled / "L1_channels.csv").read_text(encoding="utf-8").splitlines()
    (shuffled / "L1_channels.csv").write_text(
        "\n".join([rows[0], *reversed(rows[1:])]) + "\n", encoding="utf-8")
    assert json.dumps(build_passport(shuffled), ensure_ascii=False,
                      sort_keys=True) == baseline


# ── 缺源字段:写 null 并记 missing[],不猜 ───────────────────────────────────
def test_absent_selection_reason_column_is_null_and_recorded_missing(tmp_path):
    """T16 之前落盘的 `L2_gbdt_top200.csv` 没有这两列 —— 不许拿 lane_reserved 反推。"""
    scan = _build_scan(tmp_path, with_selection_reason=False)
    doc = build_passport(scan)
    for code in {row[1] for row in _L2_ROWS}:
        entry = doc["candidates"][code]
        assert entry["l2"]["selection_reason"] is None, code
        assert entry["l2"]["selection_detail"] is None, code
        assert "l2.selection_reason" in entry["missing"], code
        assert "l2.selection_detail" in entry["missing"], code
    assert doc["sources"]["l2_selection_reason"] == "ABSENT"


def test_stages_not_reached_are_null_but_not_missing(tmp_path):
    """没走到 L3/L4 ≠ 源字段缺失 —— missing[] 只记「到了这一站却读不到」。"""
    scan = _build_scan(tmp_path)
    doc = build_passport(scan)
    cut = doc["candidates"]["601112"]
    assert cut["pass1"]["kept"] is False
    assert cut["pass1"]["reason"] is None
    assert cut["l3"]["judged"] is False
    assert cut["l3"]["tier"] is None
    assert cut["l4"]["carded"] is False
    assert cut["missing"] == []


def test_l3_tier_and_bench_reason(tmp_path):
    scan = _build_scan(tmp_path)
    doc = build_passport(scan)
    finalist = doc["candidates"]["600188"]["l3"]
    assert finalist["judged"] is True
    assert finalist["finalist"] is True
    assert finalist["tier"] == "finalist"
    assert finalist["conviction"] == 75
    assert finalist["mechanism"].startswith("板块轮动位")
    assert finalist["bench_reason"] is None

    bench = doc["candidates"]["605088"]["l3"]
    assert bench["judged"] is True
    assert bench["finalist"] is False
    assert bench["tier"] == "bench"
    assert bench["bench_reason"] == "cap"

    pinned = doc["candidates"]["300750"]["l3"]
    assert pinned["finalist"] is True, "pinned 是 merge 后注入的 finalist,不在 judged 里"
    assert pinned["judged"] is False
    assert pinned["conviction"] is None


def test_empty_scan_dir_yields_empty_passport_without_raising(tmp_path):
    empty = tmp_path / "2026-08-07"
    empty.mkdir()
    doc = build_passport(empty)
    assert doc["candidates"] == {}
    assert doc["counts"]["candidates"] == 0
    assert doc["sources"]["l2"] == "ABSENT"


# ── best_rank 不许把哨兵当名次(实测 8 个交易日真实命中)────────────────────
def test_uncalled_pinned_gets_null_best_rank_not_the_sentinel(tmp_path):
    """没被任何通道召回的 pinned 持仓,上游 `best_rank` 写 `10**9` 哨兵。

    透出去的后果是 T23 拿它算横截面百分位 —— 而命中的恰恰是必须参与比较的持仓票
    (实测 `920179` 连续 6 个交易日)。同一个块里 `n_channels=0` 已经说了"零通道",
    `best_rank` 再给个可比大小的数就是自相矛盾。
    """
    from autoresearch.scan.universe import _PINNED_RANK_SENTINEL

    scan = _build_scan(tmp_path)
    # 300750 改成"保送但零通道":从 L1_channels 里抹掉它,L2 的 best_rank 换成哨兵
    rows = (scan / "L1_channels.csv").read_text(encoding="utf-8").splitlines()
    (scan / "L1_channels.csv").write_text(
        "\n".join(r for r in rows if ",300750," not in f",{r},") + "\n",
        encoding="utf-8")
    l2 = (scan / "L2_gbdt_top200.csv").read_text(encoding="utf-8")
    (scan / "L2_gbdt_top200.csv").write_text(
        l2.replace(",heat,1,6,True,", f",heat,1,{_PINNED_RANK_SENTINEL},True,"),
        encoding="utf-8")

    recall = build_passport(scan)["candidates"]["300750"]["recall"]
    assert recall["n_channels"] == 0
    assert recall["channels"] == []
    assert recall["best_rank"] is None, "哨兵 10**9 被当成真名次透出了"


def test_best_rank_falls_back_but_still_filters_sentinel(tmp_path):
    """`L1_channels.csv` 整个缺失时才回退 L2 列 —— 回退路径同样不许放哨兵过去。"""
    from autoresearch.scan.universe import _PINNED_RANK_SENTINEL

    scan = _build_scan(tmp_path)
    (scan / "L1_channels.csv").unlink()
    l2 = (scan / "L2_gbdt_top200.csv").read_text(encoding="utf-8")
    (scan / "L2_gbdt_top200.csv").write_text(
        l2.replace(",heat,1,6,True,", f",heat,1,{_PINNED_RANK_SENTINEL},True,"),
        encoding="utf-8")

    candidates = build_passport(scan)["candidates"]
    assert candidates["300750"]["recall"]["best_rank"] is None
    assert "recall.per_channel" in candidates["300750"]["missing"]
    # 同一次回退里,正常的名次仍要读出来(否则这条测试可以靠"一律 None"作弊过关)
    assert candidates["600188"]["recall"]["best_rank"] == 1


# ── guard 是双向标记,bench_reason 只在 bench 那一侧才成立 ───────────────────
def test_guard_on_a_finalist_is_not_a_bench_reason(tmp_path):
    """`ins75` 是被强行救**进** finalist(`merge.py:72-75`),不是"为什么在 bench"。

    `_swap_lane_quota` 更直接:给换进来的和被换出的**两边写同一个 guard 值**
    (`merge.py:51-53`)。所以 `bench_reason` 必须以 tier 为门,否则字段名承诺了
    它兑现不了的语义。
    """
    scan = _build_scan(tmp_path)
    l3 = build_passport(scan)["candidates"]["600123"]["l3"]
    assert l3["tier"] == "finalist"
    assert l3["guard"] == "ins75", "raw guard 仍要留着,信息不能丢"
    assert l3["bench_reason"] is None, "finalist 的 guard 被当成 bench 理由透出了"


def test_guard_on_a_bench_row_is_the_bench_reason(tmp_path):
    scan = _build_scan(tmp_path)
    l3 = build_passport(scan)["candidates"]["605088"]["l3"]
    assert l3["tier"] == "bench"
    assert l3["guard"] == "cap"
    assert l3["bench_reason"] == "cap"


# ── 候选集外的 finalist 必须有对账信号,不许静默吞掉 ────────────────────────
def test_finalists_outside_the_candidate_set_are_reported(tmp_path):
    """实测 06-22/06-26/07-01 三天共 45 只 finalist 不在当日 L2 里。行集合口径不改
    (spec 断言① 要求护照 = L2 全集),但差额必须报出来 —— 只读护照的下游否则会
    以为它们不存在。"""
    scan = _build_scan(tmp_path)
    with (scan / "finalists.csv").open("a", encoding="utf-8", newline="") as handle:
        csv.writer(handle).writerow(["999888.SZ", "999888", "幽灵", "测试", 70, "healthy", ""])

    doc = build_passport(scan)
    assert "999888" not in doc["candidates"], "行集合口径不该被改"
    assert doc["orphans"]["finalists"] == ["999888"]
    assert doc["counts"]["orphan_finalists"] == 1
    assert doc["counts"]["orphan_rated"] == 0


def test_no_orphans_on_a_consistent_run(tmp_path):
    doc = build_passport(_build_scan(tmp_path))
    assert doc["orphans"] == {"finalists": [], "rated": []}
    assert doc["counts"]["orphan_finalists"] == 0


# ── composite_pctl / dispatched:T23 的两个核心输入,必须有守卫 ──────────────
def test_composite_pctl_values_and_direction(tmp_path):
    """`composite_pctl` 是 T23 `target_align` 面的唯一来源。方向反了它整个决策就反了
    —— 所以既锁逐值,也锁"综合分高的票分位必须更高"这个方向。"""
    scan = _build_scan(tmp_path)
    doc = build_passport(scan)

    rows = _rows_of(scan / "L1_scored_full.csv")
    total = len(rows)
    assert total == len(_L2_ROWS)
    for row in rows:
        code, rank = row["code"].zfill(6), int(row["rank"])
        assert doc["candidates"][code]["recall"]["composite_pctl"] == pytest.approx(
            round((total - rank + 1) / total, 6)), code

    # 方向:composite 最高的票分位最高,最低的最低(纯逐值断言挡不住整体取反)
    ranked = sorted(_L2_ROWS, key=lambda r: -r[4])
    top, bottom = ranked[0][1], ranked[-1][1]
    assert doc["candidates"][top]["recall"]["composite_pctl"] == pytest.approx(1.0)
    assert (doc["candidates"][top]["recall"]["composite_pctl"]
            > doc["candidates"][bottom]["recall"]["composite_pctl"])


def test_composite_pctl_absent_source_is_null_and_missing(tmp_path):
    scan = _build_scan(tmp_path)
    (scan / "L1_scored_full.csv").unlink()
    doc = build_passport(scan)
    entry = doc["candidates"]["600188"]
    assert entry["recall"]["composite_pctl"] is None
    assert "recall.composite_pctl" in entry["missing"]
    assert doc["sources"]["l1_scored_full"] == "ABSENT"


def test_dispatched_drives_the_missing_rating_alarm(tmp_path):
    """`dispatched` 是 `l4.research_rating` 进不进 `missing[]` 的唯一开关,也是 T23 的
    候选集口径。它坏掉的表现就是"报警永不响",所以必须两侧都断言。"""
    scan = _build_scan(tmp_path)
    doc = build_passport(scan)

    # 派发过、任务失败没出评级 → 必须报警
    failed = doc["candidates"]["600123"]["l4"]
    assert failed["dispatched"] is True
    assert failed["carded"] is False
    assert failed["research_rating"] is None
    assert "l4.research_rating" in doc["candidates"]["600123"]["missing"]

    # 派发过且有评级 → 不报警
    ok = doc["candidates"]["600188"]["l4"]
    assert ok["dispatched"] is True and ok["carded"] is True
    assert "l4.research_rating" not in doc["candidates"]["600188"]["missing"]

    # 压根没派发 → 既不 dispatched 也不报警
    never = doc["candidates"]["601112"]["l4"]
    assert never["dispatched"] is False
    assert doc["candidates"]["601112"]["missing"] == []


def test_dispatched_reads_the_task_book_not_the_ratings(tmp_path):
    """任务簿在场时就以它为准:抹掉任务簿里的一只,它的 dispatched 必须跟着变。"""
    scan = _build_scan(tmp_path)
    book = json.loads((scan / "_l4_tasks.json").read_text(encoding="utf-8"))
    del book["tasks"]["600123"]
    (scan / "_l4_tasks.json").write_text(json.dumps(book, ensure_ascii=False),
                                         encoding="utf-8")
    doc = build_passport(scan)
    assert doc["candidates"]["600123"]["l4"]["dispatched"] is False
    assert doc["candidates"]["600123"]["missing"] == []
    assert doc["candidates"]["600188"]["l4"]["dispatched"] is True


# ── judged JSON 形状不对必须回退,不许静默说"全体没被判过" ──────────────────
def test_wrong_shaped_judged_json_falls_back_to_csv(tmp_path):
    """`_l3_judged.json` 是 LLM 写的。写成 `{"judged":[...]}` 时若只认 list、
    又不回退 CSV,结果是全体 `judged=false` 且 `missing[]` 全空 —— 静默说假话。"""
    scan = _build_scan(tmp_path)
    payload = json.loads((scan / "_l3_judged.json").read_text(encoding="utf-8"))
    (scan / "_l3_judged.json").write_text(
        json.dumps({"judged": payload}, ensure_ascii=False), encoding="utf-8")
    _write_csv(scan / "L3_judged_full.csv",
               ["code", "name", "conviction", "mechanism", "lane", "triage_lean"],
               [[code, _name_of(code), conv, mech, lane, lean]
                for code, (conv, mech, lane, lean, _fin) in _JUDGED.items()])

    doc = build_passport(scan)
    assert doc["candidates"]["600188"]["l3"]["judged"] is True
    assert doc["candidates"]["600188"]["l3"]["conviction"] == 75


# ── CLI + post_run 接线 ────────────────────────────────────────────────────
def test_cli_writes_passport_json(tmp_path):
    from autoresearch.scan import passport as mod

    scan = _build_scan(tmp_path)
    assert mod.main([str(scan)]) == 0
    target = scan / PASSPORT_FILENAME
    assert target.exists()
    doc = json.loads(target.read_text(encoding="utf-8"))
    assert set(doc["candidates"]) == {row[1] for row in _L2_ROWS}


def test_post_run_observe_builds_passport(tmp_path):
    from autoresearch.scan.post_run import publish_run_observation

    scan = _build_scan(tmp_path)
    publish_run_observation(scan, real_scan=False)
    assert (scan / PASSPORT_FILENAME).exists()


# ── 真实 run 活体验收(缺产物则跳过,不当强证据)────────────────────────────
_REAL = Path("context/scan/2026-08-06")


@pytest.mark.skipif(not (_REAL / "L2_gbdt_top200.csv").exists(),
                    reason="真实 run 产物不在工作树里")
def test_real_run_20260806_passport_matches_sources():
    doc = build_passport(_REAL)
    with (_REAL / "L2_gbdt_top200.csv").open(encoding="utf-8", newline="") as handle:
        l2_codes = {row["code"].zfill(6) for row in csv.DictReader(handle)}
    assert set(doc["candidates"]) == l2_codes
    for code, rating in read_final_ratings(_REAL).items():
        assert doc["candidates"][code]["l4"]["research_rating"] == rating, code
    with (_REAL / "L1_channels.csv").open(encoding="utf-8", newline="") as handle:
        for row in csv.DictReader(handle):
            code = row["code"].zfill(6)
            if code in doc["candidates"]:
                got = doc["candidates"][code]["recall"]["per_channel"]
                assert got[row["channel"]]["rank"] == int(row["channel_rank"])
