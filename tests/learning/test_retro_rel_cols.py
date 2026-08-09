"""T22(Wave12 E6-0):相对标签两列 rel_gap_market / rel_gap_sector 入账。

design: .superpowers/sdd/2026-08-08-wave12-implementation-plan/task-22-brief.md

用户 2026-08-08 追加裁定的地基:系统对外只有一种 BUY——"今日可交易全集里相对最值得买"
(不承诺绝对上涨)。相对基准 = 全市场可交易等权为主、行业中性超额为辅,主评价尺仍是
`gap_c1_o2`。

premise-check 结论(报告 task-16-22-report.md 有完整记录):
- 行业列名实测确为 `industry`(`L1_scored_full.csv` 的 `keep` 白名单、`l2_stratify.
  industry_col` 默认值、`_synth_universe`/`retro._selftest` 合成帧三处一致印证),
  brief 的预期成立,不需要改口径。
- 基准分母必须只含 `ruler.entry_tradable()` 折叠后的可交易票 —— **不是全市场所有行**
  (含停牌/涨停封死买不进的票会把"市场平均"算成不可执行的幻觉基准),这是本任务书点名
  最容易做错的地方,见下方"分母口径"一节的显式手算 + 变异探针记录(报告里的实跑证据)。

两列都是 gap_c1_o2 的超额:
  rel_gap_market = gap_c1_o2 − 当日可交易全集等权均值
  rel_gap_sector = gap_c1_o2 − 同申万一级可交易等权均值(票本身缺行业 / 该行业当日
                   无可交易成员 → NaN,不猜)
"""
from __future__ import annotations

import pandas as pd
import pytest

from autoresearch.common import ruler
from autoresearch.learning import retro

# ───────────────────────── ruler 常量存在性 ─────────────────────────


def test_ruler_exposes_rel_gap_constants():
    assert ruler.REL_MARKET == "rel_gap_market"
    assert ruler.REL_SECTOR == "rel_gap_sector"


# ───────────────────────── attribute_frame:合成两行业×四票帧,手算逐票核对 ─────────────────────────


def _l1(codes, industries):
    return pd.DataFrame({"code": codes, "name": codes, "industry": industries,
                         "composite": 50.0, "recalled": True})


def _realized(codes, gaps, buyable_c1):
    return pd.DataFrame({"code": codes, "fwd_1_oo": gaps, ruler.MAIN_RULER: gaps,
                         "buyable": True, "buyable_c1": buyable_c1})


def test_rel_gap_cols_hand_computed_two_industries_four_tickers():
    """2 行业 × 4 票,全部可交易 —— 手算市场均值与行业均值,逐票核对相等。"""
    codes = ["000001", "000002", "000003", "000004"]
    industries = ["电子", "电子", "医药", "医药"]
    gaps = [0.10, 0.02, -0.04, 0.00]
    l1 = _l1(codes, industries)
    realized = _realized(codes, gaps, buyable_c1=[True, True, True, True])
    attr = retro.attribute_frame(l1, realized, buylist={})

    market_mean = sum(gaps) / len(gaps)                 # 4 票全可交易 → 全体入分母
    elec_mean = (0.10 + 0.02) / 2
    pharma_mean = (-0.04 + 0.00) / 2
    want_market = dict(zip(codes, [g - market_mean for g in gaps], strict=True))
    want_sector = {"000001": gaps[0] - elec_mean, "000002": gaps[1] - elec_mean,
                   "000003": gaps[2] - pharma_mean, "000004": gaps[3] - pharma_mean}

    by_code = attr.set_index("code")
    for c in codes:
        assert by_code.loc[c, ruler.REL_MARKET] == pytest.approx(want_market[c]), c
        assert by_code.loc[c, ruler.REL_SECTOR] == pytest.approx(want_sector[c]), c


def test_missing_industry_makes_rel_gap_sector_nan_but_not_rel_gap_market():
    """缺行业票:`rel_gap_sector` = NaN,不猜;`rel_gap_market` 与行业无关,仍照算。"""
    codes = ["000001", "000002", "000003"]
    l1 = pd.DataFrame({"code": ["000001", "000002"], "name": ["a", "b"],
                       "industry": ["电子", "电子"], "composite": 50.0, "recalled": True})
    # 000003 不在 l1 里(漏在 L0)→ merge 后 industry 为 NaN
    gaps = [0.10, 0.02, 0.30]
    realized = _realized(codes, gaps, buyable_c1=[True, True, True])
    attr = retro.attribute_frame(l1, realized, buylist={})
    by_code = attr.set_index("code")

    assert pd.isna(by_code.loc["000003", "industry"])              # 前提断言:合并后确实缺行业
    assert pd.isna(by_code.loc["000003", ruler.REL_SECTOR])
    assert not pd.isna(by_code.loc["000003", ruler.REL_MARKET])    # market 基准与行业无关


def test_rel_gap_market_population_includes_tickers_missing_from_l0_not_just_l0_passed():
    """I-1(final-review 2026-08-08/09,人口裁定并留痕):基准人口 = **全市场**可交易票,
    不是仅 L0 过门的子集 —— 000003 漏在 L0(不在 l1 里)但可交易,它的 gap 必须真的拉动
    市场均值,不能被静默排除出分母。这条断言就是该项裁定的可执行记录(见 ruler.py 里
    REL_MARKET/REL_SECTOR 旁的 I-1 长注释:任务书 Interfaces 一度写「L0 可交易全集」,
    与用户裁定「全市场可交易等权」冲突,以用户裁定为准)。
    """
    codes = ["000001", "000002", "000003"]
    l1 = pd.DataFrame({"code": ["000001", "000002"], "name": ["a", "b"],
                       "industry": ["电子", "电子"], "composite": 50.0, "recalled": True})
    gaps = [0.10, 0.02, 0.30]                          # 000003(漏在 L0)gap 明显偏离,便于判别
    realized = _realized(codes, gaps, buyable_c1=[True, True, True])
    attr = retro.attribute_frame(l1, realized, buylist={})
    by_code = attr.set_index("code")

    full_market_mean = sum(gaps) / len(gaps)            # 正确:三票全可交易,全部入分母
    l0_only_mean = (gaps[0] + gaps[1]) / 2               # 错腿:若误采 L0-only 人口(漏 000003)
    assert by_code.loc["000001", ruler.REL_MARKET] == pytest.approx(gaps[0] - full_market_mean)
    assert by_code.loc["000001", ruler.REL_MARKET] != pytest.approx(gaps[0] - l0_only_mean)


def test_rel_gap_cols_pinned_to_gap_c1_o2_literal_survives_main_ruler_rollback(monkeypatch):
    """I-4(final-review 2026-08-08/09,口径钉尺):`rel_gap_market`/`rel_gap_sector` 必须
    按字面量 `ruler.REL_GAP_RULER`("gap_c1_o2")计算,即便批A 回滚杆把 `MAIN_RULER` 改回
    `fwd_2_oc` 也不能跟着变 —— 否则同一列名下新旧行会静默混两把尺(动机见 ruler.py 里
    REL_GAP_RULER 旁的长注释)。fixture 让 gap_c1_o2 与 fwd_2_oc 取明显不同的值,若实现
    误读了(回滚后的)MAIN_RULER,断言会直接对不上。
    """
    import autoresearch.learning.retro as retro_mod
    from autoresearch.common import ruler as ruler_mod

    codes = ["000001", "000002"]
    gap_values = [0.10, -0.05]          # REL_GAP_RULER 钉死要读的列
    fwd2_values = [0.50, 0.50]          # 假装 MAIN_RULER 被回滚指向这一列(取显著不同的数)
    l1 = _l1(codes, ["电子", "电子"])
    realized = pd.DataFrame({
        "code": codes, "name": codes, "gap_c1_o2": gap_values, "fwd_2_oc": fwd2_values,
        "buyable": True, "buyable_c1": [True, True],
    })
    monkeypatch.setattr(retro_mod, "MAIN_RULER", "fwd_2_oc")   # 模拟回滚杆(retro.py 是 from-import)
    monkeypatch.setattr(ruler_mod, "MAIN_RULER", "fwd_2_oc")   # ruler.entry_tradable 内部兜底同源

    attr = retro.attribute_frame(l1, realized, buylist={})
    by_code = attr.set_index("code")
    market_mean = sum(gap_values) / len(gap_values)             # 仍应按 gap_c1_o2 计算
    wrong_mean = sum(fwd2_values) / len(fwd2_values)             # 错腿:误随回滚后的 MAIN_RULER
    for c, g in zip(codes, gap_values, strict=True):
        assert by_code.loc[c, ruler.REL_MARKET] == pytest.approx(g - market_mean), c
        assert by_code.loc[c, ruler.REL_MARKET] != pytest.approx(g - wrong_mean), c


def test_industry_with_zero_tradable_members_is_nan_not_a_guess():
    """某行业当日全体不可交易(涨停封死)—— 该行业分母为空,不得回退成猜一个数。"""
    codes = ["000001", "000002", "000003"]
    industries = ["电子", "电子", "医药"]     # 医药只有一票,且不可交易
    gaps = [0.10, 0.02, 0.30]
    l1 = _l1(codes, industries)
    realized = _realized(codes, gaps, buyable_c1=[True, True, False])
    attr = retro.attribute_frame(l1, realized, buylist={})
    by_code = attr.set_index("code")
    assert pd.isna(by_code.loc["000003", ruler.REL_SECTOR])


# ───────────────────────── 分母口径:只含可交易票(brief 点名最容易做错的地方) ─────────────────────────


def test_benchmark_excludes_non_tradable_rows_from_the_denominator():
    """C1 同款:一只涨停封死买不进的票(buyable_c1=False)哪怕 gap 极端,也不得进分母 ——
    否则「市场平均」被一个买不进的幻觉数字污染。变异探针(报告里有实跑红/绿记录):把
    `_rel_gap_cols` 的分母改成「不筛 entry_tradable」,本测试断言的期望值会直接对不上。
    """
    codes = ["000001", "000002", "000003"]
    industries = ["电子", "电子", "电子"]
    # 000003 涨停封死(不可交易),gap 极端 +0.50;若被错误纳入分母,市场均值会被显著拉高
    gaps = [0.00, 0.02, 0.50]
    l1 = _l1(codes, industries)
    realized = _realized(codes, gaps, buyable_c1=[True, True, False])
    attr = retro.attribute_frame(l1, realized, buylist={})

    correct_mean = (0.00 + 0.02) / 2      # 只有前两票可交易,分母 = 2
    wrong_mean = sum(gaps) / 3            # 错腿:分母误含不可交易票 = 全体 3 票

    by_code = attr.set_index("code")
    got_market = by_code.loc["000001", ruler.REL_MARKET]
    got_sector = by_code.loc["000001", ruler.REL_SECTOR]      # 同一行业只有这一组可交易成员,行业均值=市场均值
    assert got_market == pytest.approx(0.00 - correct_mean)
    assert got_sector == pytest.approx(0.00 - correct_mean)
    assert got_market != pytest.approx(0.00 - wrong_mean)
    # 不可交易票本身仍然拿到一个值(分子照算,只是不得进分母)—— 语义是"这票相对可执行
    # 市场基准的超额",不是"这票是否可执行"。
    assert by_code.loc["000003", ruler.REL_MARKET] == pytest.approx(0.50 - correct_mean)


def test_all_rows_untradable_yields_nan_not_zero_or_crash():
    """分母为空(全市场当日无可交易票,极端退化场景)→ NaN,不是 0、不崩溃。"""
    codes = ["000001", "000002"]
    industries = ["电子", "电子"]
    gaps = [0.10, -0.05]
    l1 = _l1(codes, industries)
    realized = _realized(codes, gaps, buyable_c1=[False, False])
    attr = retro.attribute_frame(l1, realized, buylist={})
    assert attr[ruler.REL_MARKET].isna().all()
    assert attr[ruler.REL_SECTOR].isna().all()


# ───────────────────────── _KEEP 白名单落列(与 T16 同一类洞:算出来不代表落盘) ─────────────────────────


def test_keep_whitelist_contains_rel_gap_cols():
    assert ruler.REL_MARKET in retro._KEEP
    assert ruler.REL_SECTOR in retro._KEEP


def test_attribute_writes_rel_gap_cols_to_csv(tmp_path, monkeypatch):
    """attribute() 的落盘产物(attribution.csv)必须真的带这两列 —— 内存里 attr 有不算数,
    `_KEEP` 白名单必须投影它们(T16 同款教训:漏了白名单,guard/消费方永远读不到)。

    fixture 镜像 test_retro.py::_retro_event_fixture(同款 100 码,绕开 `attribute()` 自己的
    `n_ruler >= 100` 生产门槛;不足 100 会被判"D+2 收盘多半未发布"直接 RuntimeError)。

    `report_root=tmp_path` 必传(final-review 2026-08-08 I-3 事故修复):`attribute()` 漏传
    此参数会回落内联字面量默认值 `Path("reports/scan")`——若日期恰好命中某个真实已发布
    报告的 `analysis_date`("2026-07-24" 当时就命中了 `reports/scan/20260725_1316`),
    `_publish_retro_control_state` 会把本测试的合成 fixture 真的 `shutil.copy2` 进那份
    报告的 `trace/`,覆写生产数据。conftest.py 的 `_forbid_production_report_writes` 现在
    会在任何测试漏传时直接抛 `PermissionError` 拦下来,但显式传参仍是第一道防线。
    """
    sdir = tmp_path / "2026-07-24"
    sdir.mkdir(parents=True)
    codes = [f"{i:06d}" for i in range(100)]
    industries = ["电子" if i % 2 else "医药" for i in range(100)]
    pd.DataFrame({"code": codes, "industry": industries, "composite": range(100),
                 "recalled": [False] * 100}).to_csv(sdir / "L1_scored_full.csv", index=False)
    realized = pd.DataFrame({
        "code": codes, "fwd_1_oo": [0.0] * 100,
        ruler.MAIN_RULER: [i / 10000 for i in range(100)],
        "fwd_5_oc": [float("nan")] * 100, "buyable": [True] * 100,
        "buyable_c1": [True] * 100,
    })
    monkeypatch.setattr(retro, "realized_returns", lambda *a, **k: realized)

    retro.attribute("2026-07-24", scan_root=tmp_path, report_root=tmp_path)
    got = pd.read_csv(sdir / "retro" / "attribution.csv", dtype={"code": str})
    assert {ruler.REL_MARKET, ruler.REL_SECTOR}.issubset(got.columns)


# ───────────────────────── 历史回填(A3/T6/T9 同手法:自包含,不必重取 realized_returns) ─────────────────────────
#
# `attribution.csv` 的 `_KEEP` 早就收编了 gap_c1_o2/industry/buyable_c1/tradable(Wave11 批A),
# 算 rel_gap_market/rel_gap_sector 所需的全部原料历史文件里已经都有了 —— 不像
# `_backfill_gap_columns` 那样要回退去重取 `realized_returns()`,纯粹从已有列现算现追加。


def _mk_attr_with_gap(root, date, *, codes, industries, gaps, buyable_c1):
    d = root / date / "retro"
    d.mkdir(parents=True)
    (d / "done.json").write_text("{}", encoding="utf-8")
    df = pd.DataFrame({
        "code": codes, "industry": industries,
        "fwd_5_oc": [0.0] * len(codes), "fwd_10_oc": [0.0] * len(codes), "hi_10_oc": [0.0] * len(codes),
        ruler.MAIN_RULER: gaps, "buyable_c1": buyable_c1,
    })
    df.to_csv(d / "attribution.csv", index=False)
    return d / "attribution.csv"


def test_backfill_rel_gap_columns_is_idempotent_and_skips_when_already_present(tmp_path):
    path = _mk_attr_with_gap(tmp_path, "2026-06-01", codes=["000001"], industries=["电子"],
                              gaps=[0.05], buyable_c1=[True])
    attr = pd.read_csv(path, dtype={"code": str})
    assert retro._backfill_rel_gap_columns(path, attr) is True
    got = pd.read_csv(path, dtype={"code": str})
    assert {ruler.REL_MARKET, ruler.REL_SECTOR}.issubset(got.columns)

    attr2 = pd.read_csv(path, dtype={"code": str})
    assert retro._backfill_rel_gap_columns(path, attr2) is False   # 已有列 → 跳过,幂等


def test_backfill_rel_gap_columns_returns_false_when_source_cols_missing(tmp_path):
    """历史太老连 gap_c1_o2/industry 都没有(pre-Wave11-A3)→ 无从回填,诚实返回 False,
    不touch 文件(判定发生在读盘之后、写盘之前,path 存不存在都不重要,只测判定本身)。
    """
    attr = pd.DataFrame({"code": ["000001"], "bucket": ["caught"]})
    assert retro._backfill_rel_gap_columns(tmp_path / "attribution.csv", attr) is False
    assert not (tmp_path / "attribution.csv").exists()


def test_backfill_rel_gap_columns_stays_honest_when_main_ruler_rolled_back_but_gap_col_missing(
    tmp_path, monkeypatch,
):
    """I-4 存在性判据修复(final-review 2026-08-08/09):批A 回滚杆把 `MAIN_RULER` 改回
    `fwd_2_oc` 后,一份只有 `fwd_2_oc`(没有 `gap_c1_o2`)的老文件必须仍然诚实返回 False——
    判据要读字面量 `REL_GAP_RULER`,不能读动态 `MAIN_RULER`(否则会被"fwd_2_oc 列在场"
    误判成"源列齐全",调用 `_rel_gap_cols` 时 `frame['gap_c1_o2']` 直接 KeyError)。
    """
    import autoresearch.learning.retro as retro_mod

    monkeypatch.setattr(retro_mod, "MAIN_RULER", "fwd_2_oc")   # 模拟回滚杆
    attr = pd.DataFrame({"code": ["000001"], "industry": ["电子"], "fwd_2_oc": [0.05]})
    assert retro._backfill_rel_gap_columns(tmp_path / "attribution.csv", attr) is False
    assert not (tmp_path / "attribution.csv").exists()


def test_refresh_backfills_rel_gap_cols_without_touching_old_values(tmp_path):
    path = _mk_attr_with_gap(tmp_path, "2026-07-31", codes=["000001", "000002"],
                              industries=["电子", "电子"], gaps=[0.10, -0.02],
                              buyable_c1=[True, True])
    done = retro.refresh_attributions(scan_root=tmp_path)
    assert done == ["2026-07-31"]

    got = pd.read_csv(path, dtype={"code": str})
    assert {ruler.REL_MARKET, ruler.REL_SECTOR} <= set(got.columns)
    assert got[ruler.MAIN_RULER].tolist() == [0.10, -0.02]     # 旧列旧值原样不动
    assert len(got) == 2                                       # 不改行数(n 不清零)

    before = path.read_bytes()
    done2 = retro.refresh_attributions(scan_root=tmp_path)     # 幂等:第二跑零改动
    assert done2 == []
    assert path.read_bytes() == before


def test_refresh_backfills_both_gap_and_rel_gap_in_same_pass(tmp_path, monkeypatch):
    """同一天既缺 gap_c1_o2 又缺 rel_gap_* —— 一次 refresh 调用两段都该补上(gap 回填后
    就地刷新,rel 回填才看得到新落的 gap 列),不必等下一次夜间批才把 rel 列补齐。"""
    d = tmp_path / "2026-07-20" / "retro"
    d.mkdir(parents=True)
    (d / "done.json").write_text("{}", encoding="utf-8")
    old = pd.DataFrame({
        "code": ["000001", "000002"], "industry": ["电子", "电子"],
        "fwd_2_oc": [0.02, -0.01], "fwd_5_oc": [0.03, -0.02],
        "fwd_10_oc": [0.04, -0.03], "hi_10_oc": [0.05, 0.01],
    })
    old.to_csv(d / "attribution.csv", index=False)
    fake = pd.DataFrame({
        "code": ["000001", "000002"], "fwd_2_oc": [0.02, -0.01],
        "gap_c1_o2": [0.10, 0.02], "buyable_c1": [True, True],
        "unsellable_o2": [False, False],
    })
    monkeypatch.setattr(retro, "realized_returns", lambda *a, **k: fake)

    done = retro.refresh_attributions(scan_root=tmp_path)
    assert done == ["2026-07-20"]
    got = pd.read_csv(d / "attribution.csv", dtype={"code": str})
    assert {"gap_c1_o2", ruler.REL_MARKET, ruler.REL_SECTOR}.issubset(got.columns)
    assert got["fwd_2_oc"].tolist() == [0.02, -0.01]     # 旧列不动
