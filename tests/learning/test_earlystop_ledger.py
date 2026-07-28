"""早停账本:按停因桶累计 fwd_2_oc,为「强势票早停是不是误杀」攒裁决样本(Wave5 ②C)。"""
from __future__ import annotations

import json

from autoresearch.learning import earlystop_ledger as el


def _day(root, date, stops: dict, attribution: str):
    d = root / date
    (d / "retro").mkdir(parents=True)
    (d / "_early_stop.json").write_text(json.dumps(stops, ensure_ascii=False), encoding="utf-8")
    (d / "retro" / "attribution.csv").write_text(attribution, encoding="utf-8")


def test_roll_joins_stops_to_forward_returns(tmp_path):
    _day(tmp_path, "2026-07-21",
         {"000651": {"phase": "P3", "reason": "涨停追高"},
          "300857": {"phase": "P3", "reason": "资金流出"}},
         "code,fwd_2_oc\n000651,0.05\n300857,-0.02\n")
    df = el.roll(scan_root=tmp_path)
    assert len(df) == 2
    row = df[df["code"] == "000651"].iloc[0]
    assert row["reason"] == "涨停追高"
    assert abs(float(row["fwd_2_oc"]) - 0.05) < 1e-9


def test_render_buckets_by_reason(tmp_path):
    _day(tmp_path, "2026-07-21",
         {"000651": {"phase": "P3", "reason": "涨停追高"},
          "000002": {"phase": "P3", "reason": "涨停追高"}},
         "code,fwd_2_oc\n000651,0.05\n000002,0.03\n")
    md = "\n".join(el.render(el.roll(scan_root=tmp_path)))
    assert "涨停追高" in md
    assert "n=2" in md
    assert "样本不足" in md          # n<10 必须自标禁裁决


def test_empty_root_renders_placeholder(tmp_path):
    md = "\n".join(el.render(el.roll(scan_root=tmp_path)))
    assert "无早停记录" in md


# ══ Wave7 B′-d:账本必须看得见「刚跑完那一天」════════════════════════════════
#
# 2026-07-27 实锤:那天跑出 4 张早停卡、`_early_stop.json` 也正确落了盘,而
# `reports/learning/earlystop_ledger.md` 仍写着「无早停记录」——因为账本刷新挂在 prelude
# (跑前 20:11),输入却由 assemble 在跑后 21:40 才写。解析没坏,是**时序**:账本恒定
# 落后一个 run。Wave6 R3 验收④ 因此记 ✗。


def test_roll_reads_same_day_early_stop(tmp_path):
    """当天的 `_early_stop.json` 一旦落盘,roll() 立刻数得到 —— 解析腿本身是好的。"""
    import json as _json
    d = tmp_path / "2026-07-27"
    d.mkdir(parents=True)
    (d / "_early_stop.json").write_text(_json.dumps({
        "600030": {"phase": "P3", "reason": "其他"},
        "600529": {"phase": "P3", "reason": "基本面恶化"},
        "601211": {"phase": "P3", "reason": "题材透支"},
        "601728": {"phase": "P3", "reason": "基本面恶化"},
    }), encoding="utf-8")

    df = el.roll(scan_root=tmp_path)

    assert len(df) == 4
    assert sorted(df["reason"]) == ["其他", "基本面恶化", "基本面恶化", "题材透支"]
    assert "无早停记录" not in "\n".join(el.render(df))


def test_assemble_refreshes_ledgers_whose_input_it_just_wrote():
    """契约:assemble 收尾必须再刷一次「输入是它自己刚写的」那两个账本。

    这条锁的是**调用点存在性**,不是渲染结果 —— 07-27 的病正是「产物对、账本没人重刷」。
    删掉 assemble 里那段 for 循环,本测试必须变红。
    """
    from pathlib import Path as _P
    src = _P("autoresearch/scan/assemble.py").read_text(encoding="utf-8")
    head, _, tail = src.partition("_journal.main()")
    assert tail, "assemble 的 journal 刷新锚点不见了(本测试的定位假设已失效,请重写)"
    assert "earlystop_ledger" in tail, "assemble 收尾没刷 earlystop_ledger → 账本恒落后一个 run"
    assert "gate_ledger" in tail, "assemble 收尾没刷 gate_ledger(gate_fires.csv 同族时序问题)"
    assert "pinned_ledger" in tail, "assemble 收尾没刷 pinned_ledger(持仓判断当天必须进表)"
