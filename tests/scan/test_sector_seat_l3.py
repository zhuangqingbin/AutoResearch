"""行业席位在 L3 侧的三处可见性(2026-09-24 §2.3)。"""
from __future__ import annotations

import json

import pandas as pd


def _l2(n=30):
    df = pd.DataFrame({"code": [f"{600000 + i:06d}" for i in range(n)], "name": [f"票{i}" for i in range(n)],
                       "industry": "电力", "composite": list(range(n, 0, -1)), "gbdt_score": list(range(n, 0, -1)),
                       "recall_channels": "value", "n_channels": 1, "pct_1d": 0.5})
    df["sector_seat"] = False
    df.loc[df.index[-2:], "sector_seat"] = True          # composite 最低的两只 = 席位(不靠分进表)
    return df


def test_triage_keeps_sector_seats_as_protected_mandatory():
    from autoresearch.scan.l3.triage import triage_l2_for_l3
    kept, cut = triage_l2_for_l3(_l2(), target=5)
    seat_codes = {"600028", "600029"}
    assert seat_codes <= set(kept["code"])
    marks = kept[kept["code"].isin(seat_codes)]
    assert (marks["selection_reason"] == "conviction_guard").all()
    assert (marks["selection_detail"] == "sector_seat").all()


def test_write_finalists_marks_sector_seat_guard(tmp_path):
    from autoresearch.scan.l3.merge import write_finalists
    scan = tmp_path / "2026-09-17"
    scan.mkdir()
    judged = [{"code": "600001", "name": "甲", "sector": "电力", "conviction": 70, "finalist": True,
               "thesis": "t", "mechanism": "m", "risk": "r"},
              {"code": "600002", "name": "乙", "sector": "电力", "conviction": 66, "finalist": True,
               "thesis": "t", "mechanism": "m", "risk": "r"}]
    (scan / "_l3_judged.json").write_text(json.dumps(judged, ensure_ascii=False), encoding="utf-8")
    (scan / "_sector_seats.json").write_text(json.dumps({"schema_version": 1, "date": "2026-09-17",
                                                          "seats": [{"code": "600002", "industry": "电力"}]}),
                                             encoding="utf-8")
    write_finalists("2026-09-17", root=tmp_path)
    fin = pd.read_csv(scan / "finalists.csv", dtype={"code": str})
    assert fin.set_index("code").loc["600002", "guard"] == "sector_seat"
    assert fin.set_index("code").loc["600001", "guard"] != "sector_seat"


def test_run_facts_role_sector_seat(tmp_path):
    from autoresearch.scan.outcome import run_facts
    run = tmp_path / "20260917-0917_2152"
    (run / "trace" / "staging").mkdir(parents=True)
    (run / "manifest.json").write_text(json.dumps({"analysis_date": "2026-09-17"}), encoding="utf-8")
    (run / "trace" / "staging" / "finalists.csv").write_text(
        "code,name,sector,guard,lane\n600002,乙,电力,sector_seat,value\n", encoding="utf-8")
    facts = run_facts(run)
    assert facts["rows"]["600002"]["role"] == "sector_seat"


# ── L3 表渲染层(fix round 1:上面三个测试都没碰过实际渲染的 l3_table_md/prepare_l3_table,
# 🏭 列与图例——直接进判断模型 prompt 的那部分——此前无任何回归覆盖)。镜像
# `tests/scan/test_l3_pinned_flag.py` 的 fixture 套路(`_mk`/`_row` → 落盘 L2_gbdt_top200.csv
# → 调用真渲染函数)与断言粒度(doc 级图例锚 + 逐行 marker 精度 + presence-gated 双侧)。

_SEAT_TABLE_DATE = "2026-09-17"


def _seat_row(code, name="甲", sector_seat=None):
    """镜像 test_l3_pinned_flag.py 的 `_row`(同款最小字段集)。`sector_seat=None` → 该键
    整个不写进 dict,DataFrame 里就**没有这一列**(不是"这一列全 False")——OFF 测试要的
    是生产默认(`l2.sector_seats` 未开启)那个真实形状:列压根不存在,不是列存在但假。"""
    row = {"code": code, "name": name, "industry": "电子", "composite": 80.0,
          "main_net_ratio": 0.05, "pct_60d": 10.0, "pe": 30.0}
    if sector_seat is not None:
        row["sector_seat"] = sector_seat
    return row


def _mk_seat_l2(root, rows):
    d = root / _SEAT_TABLE_DATE
    d.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(rows).to_csv(d / "L2_gbdt_top200.csv", index=False)
    return d


def test_l3_table_seat_column_and_legend_render_when_on(tmp_path):
    """Feature ON:🏭 marks only the seat row; legend renders with three anchors —
    `🏭(seat列)`(结构锚,头部标识符,镜像 pinned 图例的 `📌(pinned列)` 锚——删掉整行图例
    三个都会红)+ `不因席位抬评级`(内容锚①,钉住「不构成推荐/无评级偏好」这条裁定本身)+
    `B 条照常适用`(内容锚②,fix round 2:这个 legend family 里「B 条」是活开关,不是套话
    ——lowturn 图例同一函数渲染,写的是相反的「硬约束 B 不适用」;seat 图例若被误删或误copy
    成 lowturn 那句,判断模型可能会认为席位票豁免下跌趋势硬排除,而前两个锚都不会发现)。
    图例措辞可以重写,但这三条保证被删除或改成相反意思必须让测试知道。"""
    from autoresearch.scan.agents.l3_select import l3_table_md
    _mk_seat_l2(tmp_path, [_seat_row("000001", sector_seat=False),
                          _seat_row("000002", name="乙", sector_seat=True)])
    md = l3_table_md(_SEAT_TABLE_DATE, root=tmp_path)
    assert "| seat |" in md                    # 列头真的加进表,不是只有图例文字
    assert "🏭(seat列)" in md                   # 图例结构锚(镜像 📌(pinned列))
    assert "不因席位抬评级" in md                 # 图例内容锚①(「不构成推荐」裁定)
    assert "B 条照常适用" in md                  # 图例内容锚②(与 lowturn 的「B 不适用」对称相反)
    lines = [ln for ln in md.splitlines() if ln.startswith("|") and ("000001" in ln or "000002" in ln)]
    row1 = next(ln for ln in lines if "000001" in ln)
    row2 = next(ln for ln in lines if "000002" in ln)
    assert "🏭" not in row1
    assert "🏭" in row2


def test_l3_table_seat_column_and_legend_absent_when_off(tmp_path):
    """Feature OFF(presence-gating 半场,镜像
    `test_l3_table_pinned_flag_no_file_column_absent`):L2 表压根没有 `sector_seat` 列
    (生产默认——`l2.sector_seats` 未在 scan_config.jsonc 出现时的真实形状)→ 列与图例都
    不出现。这半是将来有人不小心把 🏭 列改成恒渲染时才会变红的那一半。"""
    from autoresearch.scan.agents.l3_select import l3_table_md
    _mk_seat_l2(tmp_path, [_seat_row("000001")])         # 无 sector_seat 列
    md = l3_table_md(_SEAT_TABLE_DATE, root=tmp_path)
    assert "🏭(seat列)" not in md
    assert "🏭" not in md
    assert "| seat |" not in md
