"""universe.run 的 L2 落盘投影 —— l2_cols 是否忠实转录 select_l2 已经算出的全部列。

T16(Wave12 F1-3):`autoresearch.scan.recall.l2_stratify.select_l2` 早就产出
`selection_reason`/`selection_detail`(每票「因何进菜单」:merit 核 / 风格桶救回 / 行业
cap / 保送 / 回填,见 l2_stratify.py 的 `L2_SELECTION_REASONS`),但 `universe.run` 写
`L2_gbdt_top200.csv` 时用的 `l2_cols` 白名单(:459)漏投影这两列 —— 内存里的 `l2`
DataFrame 有这两列,CSV 却没有。后果:`l2_slo._guards` 里 `if "selection_reason" in
l2_frame.columns` 那条分布 guard 分支,在**真实产物**上 31 天从未触发过 —— guards 自己
的单测(test_l2_slo.py)靠手搭 fixture 直接把这两列焊进 DataFrame,绕过了 universe.run
真实的落盘投影,盖不住这个洞。活转断言见 test_l2_slo.py 新增的
test_guards_selection_reason_fires_on_real_universe_run_output。

纯新增列:不改 `select_l2` 的选择逻辑分毫,零名单影响(行数/code 集合不变,只是多两列)。
"""
from __future__ import annotations

import pandas as pd

from tests.scan._synth_universe import synth_universe

DATE = "2026-07-24"
# 已知安全组合(tests/scan/test_recall_channels.py::_CHANNELS 同款,synth_universe 文档自称
# 覆盖 "composite_score + 8 channel 所需全列"):避开 healthy/event 等需要额外挂载列的路,
# 专注本测试真正要练的东西(l2_cols 白名单),不引入无关的 channel 列契约风险。
RECALL_CHANNELS = ["composite", "momentum", "value"]


def run_universe(monkeypatch, tmp_path, *, l2_n=20, recall_n=60, n_uni=300, seed=3):
    """跑 universe.run 到落盘为止,零网络:

    - `build_market_frame` mock 成返回合成 post-gate universe(NO network)。
    - `market_event_counts` mock 成空事件帧(attach_event_cols 对空帧只填 0 列,确定性)。
    - `weights_path` 指向 tmp_path 下必不存在的文件 → `pick_weights` 确定性回落内置
      `_PRIOR_WEIGHTS`,不读开发机可能存在的 `context/factor_lab/weights.json`
      (那是 gitignored、随本地跑动漂移的产物,测试不该依赖它是否存在)。
    - `shadow=False`:本测试只关心主 L2 落盘,不需要影子变体那一整段。

    返回 outdir(含 L2_gbdt_top200.csv 等产物)。
    """
    from autoresearch.scan import events as ev_mod, universe as U

    uni = synth_universe(n=n_uni, seed=seed)
    monkeypatch.setattr(U, "build_market_frame",
                        lambda *a, **k: (uni.copy(), {"universe_raw": len(uni), "universe": len(uni)}))
    monkeypatch.setattr(ev_mod, "market_event_counts", lambda *a, **k: pd.DataFrame({"code": []}))
    outdir = tmp_path / DATE
    U.run(DATE, outdir=outdir, recall_n=recall_n, l2_n=l2_n, recall_mode="multi",
         recall_channels=list(RECALL_CHANNELS),
         weights_path=str(tmp_path / "no-such-weights.json"), shadow=False)
    return outdir


def test_l2_csv_header_contains_selection_reason_and_detail(monkeypatch, tmp_path):
    outdir = run_universe(monkeypatch, tmp_path)
    header = pd.read_csv(outdir / "L2_gbdt_top200.csv", nrows=0).columns
    assert {"selection_reason", "selection_detail"}.issubset(header), (
        "select_l2 已经产出这两列,universe.run 的 l2_cols 白名单必须投影它们,"
        "否则 l2_slo._guards 的分布 guard 恒收不到该列(31 天死分支)")


def test_l2_csv_row_count_is_unaffected_by_the_two_new_columns(monkeypatch, tmp_path):
    """纯新增列:零名单影响 —— 行数恰是请求的 l2_n,不因多投影两列而增删任何一行。

    M-2 修复(final-review 2026-08-08/09):旧版只断言 `len==l2_n` + `code` 唯一——这两条
    哪怕把整个 L2 选择算法换掉也照样绿(任何产出 l2_n 个不重复码的实现都能通过),对本测试
    名字承诺的"零名单影响"没有鉴别力(实施者自己的变异探针这条也确实没变红)。这里改成
    真的比码集 + 顺序:跑两遍 `universe.run`(同 seed/参数)——一遍是当前真实代码(`select_l2`
    产出的 `l2` 帧带 `selection_reason`/`selection_detail`);另一遍把 `select_l2` 的返回值
    原地砍掉这两列,模拟"T16 这次投影改动从未发生"时的内存形状。`l2_cols` 白名单那行是
    纯列投影(`l2[[c for c in l2_cols if c in l2.columns]]`),结构上不可能倒过来改变选中
    哪些行/什么顺序,所以两遍的 code 列表必须逐项相等——如果不等,说明有人把这条投影线
    改成了非纯列操作(例如夹带了排序/去重/过滤),这条测试就是拦这个的探针。
    """
    from autoresearch.scan.recall import l2_stratify

    l2_n = 20
    outdir_with = run_universe(monkeypatch, tmp_path, l2_n=l2_n)
    l2 = pd.read_csv(outdir_with / "L2_gbdt_top200.csv", dtype={"code": str})
    assert len(l2) == l2_n
    assert l2["code"].is_unique
    with_codes = l2["code"].tolist()

    real_select_l2 = l2_stratify.select_l2

    def _select_l2_without_new_cols(*a, **k):
        frame, engine = real_select_l2(*a, **k)
        return frame.drop(columns=["selection_reason", "selection_detail"], errors="ignore"), engine

    monkeypatch.setattr(l2_stratify, "select_l2", _select_l2_without_new_cols)
    outdir_without = run_universe(monkeypatch, tmp_path / "without-new-cols", l2_n=l2_n)
    without_header = pd.read_csv(outdir_without / "L2_gbdt_top200.csv", nrows=0).columns
    assert not {"selection_reason", "selection_detail"} & set(without_header)   # 前提:确实模拟成功
    without_codes = pd.read_csv(outdir_without / "L2_gbdt_top200.csv",
                                dtype={"code": str})["code"].tolist()

    assert with_codes == without_codes    # 码集 + 顺序逐项相等,不只是"行数/唯一性"


def test_l2_csv_still_has_the_pre_existing_columns(monkeypatch, tmp_path):
    """回归锁:新增列不能是靠替换 l2_cols(而非追加)实现的 —— 既有列必须原样都在。"""
    outdir = run_universe(monkeypatch, tmp_path)
    header = set(pd.read_csv(outdir / "L2_gbdt_top200.csv", nrows=0).columns)
    assert {"l2_rank", "gbdt_score", "l2_lane_reserved", "sector_mom", "code", "industry"}.issubset(header)


def test_l2_csv_selection_reason_values_are_from_the_known_vocabulary(monkeypatch, tmp_path):
    from autoresearch.scan.recall.l2_stratify import L2_SELECTION_REASONS

    outdir = run_universe(monkeypatch, tmp_path)
    l2 = pd.read_csv(outdir / "L2_gbdt_top200.csv", dtype={"code": str})
    assert set(l2["selection_reason"].dropna().unique()).issubset(set(L2_SELECTION_REASONS))
