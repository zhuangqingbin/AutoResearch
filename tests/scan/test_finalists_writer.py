import csv
import json

import pandas as pd

from autoresearch.scan.agents.l3_select import write_finalists


def _judged():
    # agent 的 JSON 可能把 code 写成 int 62 或 str "000063",两种都要能救回
    return [
        {"code": 62, "name": "华东电脑", "sector": "计算机", "lane": "value",
         "conviction": 72, "fragility": 40, "triage_lean": "标配",
         "thesis": "t", "risk": "r", "catalyst": "c", "lenses": "价值", "sentiment": "中性"},
        {"code": "000063", "name": "中兴通讯", "sector": "通信", "lane": "trend",
         "conviction": 66, "fragility": 30, "triage_lean": "标配",
         "thesis": "t", "risk": "r", "catalyst": "c", "lenses": "趋势", "sentiment": "偏多"},
    ]


def test_write_finalists_preserves_leading_zeros(tmp_path):
    # 默认 pinned 路径由 conftest autouse fixture 隔离(本测不涉 pinned)。
    base = tmp_path / "context" / "scan"
    d = base / "2026-07-07"
    d.mkdir(parents=True)
    (d / "_l3_judged.json").write_text(json.dumps(_judged()), encoding="utf-8")
    pd.DataFrame({"code": ["000062", "000063"], "pct_60d": [12.0, 8.0]}).to_csv(
        d / "L2_gbdt_top200.csv", index=False)

    res = write_finalists("2026-07-07", budget=5, root=base)

    assert res["finalists_n"] == 2
    rows = list(csv.DictReader((d / "finalists.csv").open(encoding="utf-8")))
    assert {r["code"] for r in rows} == {"000062", "000063"}      # 前导零存活
    assert all(r["ticker"] == r["code"] for r in rows)             # ticker 与 code 同 6 位


# ══════════════════════════ pinned 强留(design §4.1;plan Task 4) ══════════════════════════
#
# 全部沿用 _judged() 的 000062(lane=value)/000063(lane=trend)两只自然入选票;
# pinned.json 用独立 tmp_path 文件(pinned_path=),不碰真实 `.claude/skills/scan-market/pinned.json`。


def test_write_finalists_pinned_already_selected_gets_lane_pinned(tmp_path):
    """pinned 码本就在 L3 judged 里(自然入选)→ 只把 lane 改判 pinned,不重复行、不动其余 L3 真判字段。"""
    base = tmp_path / "context" / "scan"
    d = base / "2026-07-11"
    d.mkdir(parents=True)
    (d / "_l3_judged.json").write_text(json.dumps(_judged()), encoding="utf-8")
    pd.DataFrame({"code": ["000062", "000063"], "pct_60d": [12.0, 8.0]}).to_csv(
        d / "L2_gbdt_top200.csv", index=False)
    pin = tmp_path / "pinned.json"
    pin.write_text(json.dumps([{"code": "62", "note": "老用户票", "expires": "2099-01-01"}]),
                   encoding="utf-8")

    res = write_finalists("2026-07-11", budget=5, root=base, pinned_path=pin)
    assert res["finalists_n"] == 2                      # 没多行(本就在)
    rows = {r["code"]: r for r in csv.DictReader((d / "finalists.csv").open(encoding="utf-8"))}
    assert rows["000062"]["lane"] == "pinned"
    assert rows["000062"]["pinned_note"] == "老用户票"
    assert rows["000062"]["thesis"] == "t"               # L3 真判字段原样保留
    assert rows["000063"]["lane"] == "trend"             # 未被 pin 的票 lane 不受影响


def test_write_finalists_pinned_not_selected_gets_injected_beyond_budget(tmp_path):
    """pinned 码不在 L3 judged 里 → 从 L2 取真实行强留,lane=pinned,不占/不挤 budget 名额。"""
    base = tmp_path / "context" / "scan"
    d = base / "2026-07-11"
    d.mkdir(parents=True)
    (d / "_l3_judged.json").write_text(json.dumps(_judged()), encoding="utf-8")
    pd.DataFrame({"code": ["000062", "000063", "000099"],
                 "name": ["华东电脑", "中兴通讯", "手工票"],
                 "industry": ["计算机", "通信", "军工"],
                 "pct_60d": [12.0, 8.0, 3.0]}).to_csv(d / "L2_gbdt_top200.csv", index=False)
    pin = tmp_path / "pinned.json"
    pin.write_text(json.dumps([{"code": "000099", "note": "手工保送", "expires": "2099-01-01"}]),
                   encoding="utf-8")

    res = write_finalists("2026-07-11", budget=2, root=base, pinned_path=pin)   # budget=2 挤满都不挤 pinned
    assert res["finalists_n"] == 3                       # 2(budget 满额)+ 1(pinned 额外追加)
    rows = {r["code"]: r for r in csv.DictReader((d / "finalists.csv").open(encoding="utf-8"))}
    assert set(rows) == {"000062", "000063", "000099"}
    assert rows["000099"]["lane"] == "pinned"
    assert rows["000099"]["name"] == "手工票"
    assert rows["000099"]["sector"] == "军工"            # L2 industry → finalists sector
    assert rows["000099"]["pinned_note"] == "手工保送"
    assert rows["000099"]["data_missing"] == "False"     # 有 L2 数据 → 非缺失


def test_write_finalists_pinned_missing_from_lake_gets_placeholder(tmp_path):
    """pinned 码湖里/L2 都无数据 → 占位行强留,不编数(data_missing=True)。"""
    base = tmp_path / "context" / "scan"
    d = base / "2026-07-11"
    d.mkdir(parents=True)
    (d / "_l3_judged.json").write_text(json.dumps(_judged()), encoding="utf-8")
    pd.DataFrame({"code": ["000062", "000063"], "pct_60d": [12.0, 8.0]}).to_csv(
        d / "L2_gbdt_top200.csv", index=False)
    pin = tmp_path / "pinned.json"
    pin.write_text(json.dumps([{"code": "999999", "note": "湖里没有", "expires": "2099-01-01"}]),
                   encoding="utf-8")

    res = write_finalists("2026-07-11", budget=5, root=base, pinned_path=pin)
    assert res["finalists_n"] == 3
    rows = {r["code"]: r for r in csv.DictReader((d / "finalists.csv").open(encoding="utf-8"))}
    assert rows["999999"]["lane"] == "pinned"
    assert rows["999999"]["data_missing"] == "True"
    assert rows["999999"]["pinned_note"] == "湖里没有"


def test_write_finalists_expired_pinned_not_injected(tmp_path):
    """pinned 条目已过期(today > expires)→ load_pinned 归 expired,不参与强留。"""
    base = tmp_path / "context" / "scan"
    d = base / "2026-07-11"
    d.mkdir(parents=True)
    (d / "_l3_judged.json").write_text(json.dumps(_judged()), encoding="utf-8")
    pd.DataFrame({"code": ["000062", "000063"], "pct_60d": [12.0, 8.0]}).to_csv(
        d / "L2_gbdt_top200.csv", index=False)
    pin = tmp_path / "pinned.json"
    pin.write_text(json.dumps([
        {"code": "888888", "added": "2026-06-01", "expires": "2026-07-01"},
    ]), encoding="utf-8")

    res = write_finalists("2026-07-11", budget=5, root=base, pinned_path=pin)
    assert res["finalists_n"] == 2
    text = (d / "finalists.csv").read_text(encoding="utf-8")
    assert "888888" not in text


def test_write_finalists_no_pinned_json_is_parity(tmp_path):
    """无 pinned.json(显式指向不存在文件,等价生产默认路径缺文件)→ finalists.csv 与不传
    pinned_path 时逐字节一致;lane 值不受任何改判(presence-gated parity)。

    默认 pinned 路径由 conftest autouse fixture 隔离到不存在路径,证「默认路径缺文件 == 显式缺文件」。"""
    base = tmp_path / "context" / "scan"
    d = base / "2026-07-11"
    d.mkdir(parents=True)
    (d / "_l3_judged.json").write_text(json.dumps(_judged()), encoding="utf-8")
    pd.DataFrame({"code": ["000062", "000063"], "pct_60d": [12.0, 8.0]}).to_csv(
        d / "L2_gbdt_top200.csv", index=False)

    res_default = write_finalists("2026-07-11", budget=5, root=base)   # 无 pinned_path 参数(真实生产签名)
    text_default = (d / "finalists.csv").read_text(encoding="utf-8")

    res_explicit = write_finalists("2026-07-11", budget=5, root=base,
                                   pinned_path=tmp_path / "nope.json")
    text_explicit = (d / "finalists.csv").read_text(encoding="utf-8")

    # Task 2(l3-merge-plan)加了 finalist_n/bench_n 两个新键(v3 merge 的产物计数),
    # 不再是旧的 2 键 dict——parity 的实质是"default == explicit(不存在路径)"这一相等性,
    # 而非某个写死的旧 shape,故拆成两条断言。
    assert res_default == res_explicit
    assert res_explicit["judged_n"] == 2 and res_explicit["finalists_n"] == 2
    assert text_default == text_explicit
    rows = list(csv.DictReader(text_explicit.splitlines()))
    assert {r["lane"] for r in rows} == {"value", "trend"}   # 不受任何 pinned 改判
    assert "pinned_note" not in rows[0]


def test_write_finalists_pinned_judged_but_benched_keeps_l3_fields(tmp_path):
    """🐛 回归(2026-07-12 生产实测):pinned 被 L3 判过但未入选(finalist=false → bench)
    → 强留时必须带上 L3 的 thesis/risk/catalyst/conviction,不得退化成 L2 空行。

    实测:4 只保送持仓全部被 l3-rank 判过(conviction 50–55、写了完整 thesis/risk/catalyst),
    但 `_inject_pinned_finalists` 只在 `fin`(=finalist:true)里找,找不到就退回
    L2_gbdt_top200.csv(**该表没有这些列**)→ finalists.csv 的 thesis/risk/catalyst 全空
    → summary 渲染成「风险:;催化:」→ L4 prompt 告诉卡片「pinned 无 L3 前提清单」。
    L3 对持仓票的整段判断被丢弃。lookup 优先级须是 fin → judged → L2 → 占位。
    """
    base = tmp_path / "context" / "scan"
    d = base / "2026-07-12"
    d.mkdir(parents=True)
    judged = _judged() + [
        {"code": "000099", "name": "保送票", "sector": "军工", "lane": "value",
         "conviction": 52, "fragility": 60, "triage_lean": "低配", "finalist": False,
         "thesis": "📌 pinned 保送票,基本面尚可但无真吸筹",
         "risk": "硬约束 B:main_net −0.11 且 cmf/obv 未转正 = 无真吸筹",
         "catalyst": "中报窗口;无带日期硬催化。", "lenses": "价值", "sentiment": "中性"},
    ]
    (d / "_l3_judged.json").write_text(json.dumps(judged), encoding="utf-8")
    pd.DataFrame({"code": ["000062", "000063", "000099"],
                  "name": ["华东电脑", "中兴通讯", "保送票"],
                  "industry": ["计算机", "通信", "军工"],
                  "pct_60d": [12.0, 8.0, -18.0]}).to_csv(d / "L2_gbdt_top200.csv", index=False)
    pin = tmp_path / "pinned.json"
    pin.write_text(json.dumps([{"code": "000099", "note": "持仓", "expires": "2099-01-01"}]),
                   encoding="utf-8")

    write_finalists("2026-07-12", budget=5, root=base, pinned_path=pin)
    rows = {r["code"]: r for r in csv.DictReader((d / "finalists.csv").open(encoding="utf-8"))}

    r = rows["000099"]
    assert r["lane"] == "pinned"                      # 保送标记仍在(单一识别信号)
    assert r["pinned_note"] == "持仓"
    # ↓ 核心:L3 的真判字段必须活着,不能被 L2 空行覆盖
    assert "无真吸筹" in r["risk"], f"L3 risk 被丢弃了:{r['risk']!r}"
    assert "中报窗口" in r["catalyst"], f"L3 catalyst 被丢弃了:{r['catalyst']!r}"
    assert "保送票" in r["thesis"]
    assert str(r["conviction"]) in ("52", "52.0")     # L3 的 conviction 保留
    assert r["data_missing"] == "False"
