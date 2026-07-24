"""影子漏斗:变体 L2 落盘(经 universe run 的 shadow 块逻辑)+ retro 对照捕获数。合成,无网络。

spec: docs/specs/2026-07-02-scan-calendar-shadow-design.md §2
universe.run 需网络,shadow 块逻辑经 parity fixture 间接覆盖;此处直接测 select_l2 变体语义
与 retro.shadow_compare 的读盘/求交。
"""
from __future__ import annotations

import pandas as pd

from autoresearch.learning.retro import shadow_compare


def _recall(n=40):
    rows = []
    for i in range(n):
        rows.append({"code": f"{i:06d}", "name": f"N{i}", "industry": "半导体" if i % 2 else "电力",
                     "composite": 100 - i, "pct_60d": i, "main_net_ratio": 0.01,
                     "cmf_20": 0.01, "amount_yi": 5.0})
    return pd.DataFrame(rows)


def test_variants_semantics(tmp_path):
    """nostrat = 纯 composite 序;nocap = 分层但无行业上限(与 universe shadow 块同构)。"""
    from autoresearch.scan.recall.l2_stratify import select_l2
    rc = _recall()
    nostrat = rc.sort_values("composite", ascending=False).head(10)
    assert list(nostrat["code"])[:3] == ["000000", "000001", "000002"]
    nocap, eng = select_l2(rc, 10, sector_cap_frac=1.0)
    capped, _ = select_l2(rc, 10, sector_cap_frac=0.20)
    assert len(nocap) == 10 and len(capped) == 10
    top_share = nocap["industry"].value_counts(normalize=True).iloc[0]
    cap_share = capped["industry"].value_counts(normalize=True).iloc[0]
    assert cap_share <= 0.5 and top_share >= cap_share      # cap 关后行业可更集中


def test_shadow_compare_capture(tmp_path):
    d = tmp_path / "2026-07-02"
    (d / "shadow").mkdir(parents=True)
    pd.DataFrame({"code": ["000001", "000002"]}).to_csv(d / "L2_gbdt_top200.csv", index=False)
    pd.DataFrame({"code": ["000001", "000003"]}).to_csv(d / "shadow" / "L2_nostrat.csv", index=False)
    attr = pd.DataFrame([
        {"code": "000001", "winner": True, "winner_5": False},
        {"code": "000002", "winner": False, "winner_5": True},
        {"code": "000003", "winner": True, "winner_5": True},
    ])
    rows = shadow_compare(attr, d)
    assert len(rows) == 1 and rows[0]["variant"] == "nostrat"
    r = rows[0]
    assert r["cap1"] == 2 and r["cap1_main"] == 1           # 影子抓到 1+3,主只抓 1
    assert r["cap5"] == 1 and r["cap5_main"] == 1
    assert shadow_compare(attr, tmp_path / "nope") == []    # 无影子 → []


# ───────────────────────── Wave4 Task4:影子仪器修复(per_channel 落盘 + plus_event) ─────────────────────────


def test_shadow_variants_persist_per_channel(tmp_path, monkeypatch):
    """仪器修复:影子变体必须落逐路长表,否则 unique_excess_t2 无从算起
    (accumulation 2026-07-11 被裁用的就是该指标)。

    capfloor20 变体真重取数(`build_market_frame`),与本仓"直调 write_shadow_variants 的测试
    不许碰网络"既有契约冲突(见 test_healthy_channel.py::test_shadow_pre_healthy_counterfactual
    的同款说明 + test_channel_quota_override.py 的同款 mock 姿势)——本测试不关心 capfloor20
    本身产出,mock 成快速失败即可,它有自己的 try/except 兜底,不影响其余零成本变体落盘。
    """
    import pandas as pd

    from autoresearch.scan import universe as U

    monkeypatch.setattr(U, "build_market_frame",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("no network in test")))
    scored = pd.DataFrame({
        "code": [f"{i:06d}" for i in range(30)],
        "name": [f"n{i}" for i in range(30)],
        "composite": [float(90 - i) for i in range(30)],
        "industry": ["电子"] * 30,
        "ev_pos": [3.0 if i % 5 == 0 else 0.0 for i in range(30)],
        "main_net_ratio": [0.1] * 30, "cmf_20": [0.1] * 30, "pct_60d": [5.0] * 30,
        "amount_yi": [5.0] * 30, "mktcap_yi": [80.0] * 30,
    })
    recall, per = U.recall_select(scored, "2026-07-24", 20, "multi", ["composite"])
    out = tmp_path / "2026-07-24"
    out.mkdir(parents=True)
    U.write_shadow_variants(out, scored, recall, "2026-07-24", 20, 10, None, 1.0,
                            list(scored.columns), recall_channels=["composite"])
    sh = out / "shadow"
    names = {p.name for p in sh.glob("L1_channels_*.csv")}
    assert names, "影子变体未落逐路长表(per_channel 被丢弃)"
    one = pd.read_csv(sorted(sh.glob("L1_channels_*.csv"))[0])
    assert {"channel", "code", "channel_rank", "channel_score"}.issubset(one.columns)


def test_shadow_plus_event_variant(tmp_path, monkeypatch):
    """新增 plus_event 变体 = 现启用路 + event(少一路的镜像:pre_healthy 是多一路的反面)。

    `event` 通道自 Task2 review round 1 后要求同时具备 `ev_pos`(入池门槛)与 `ev_hard`
    (排序键 + "门内非纯调研"校验)两列才不降级为空帧(见 `recall/channels.py::event`)——
    brief 原始 fixture 只给了 `ev_pos`,是 review 前的旧口径;这里按当前实现补齐 `ev_hard`,
    否则 event 通道会因缺列而静默退化成空帧,测试断言的两只事件票永远进不了 L2。

    同上一测试:mock 掉 capfloor20 的真取数入口,保持本文件"NO network"契约。

    **2026-07-25(Review Round 1 C-1)改口径**:原断言"事件票应被 plus_event 变体捞进 L2"
    其实是**靠 `DEFAULT_FLOORS["事件"]=10` 这个 bug 才成立的**——事件票的 composite 排名
    故意靠后(那正是这条路的价值),L2 采样按 sector-neutral composite 排,只有事件桶 floor
    才把它们捞得进来。C-1 把该 floor 改回 0(未启用通道不得改生产 L2)后,**production 默认
    下事件票本来就不该进 L2**,而 `unique_excess_t2` 读的也从来不是 L2 名单,是逐路长表。
    故本测试拆成两断言:①production 默认(floors=None)——只要求长表里有 event 行(仪器口径);
    ②显式传事件 floor(= 将来真启用 event 路时的配置)——才要求事件票进 L2(锁住 floor 生效)。
    """
    import pandas as pd

    from autoresearch.scan import universe as U
    monkeypatch.setattr(U, "build_market_frame",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("no network in test")))
    scored = pd.DataFrame({
        "code": [f"{i:06d}" for i in range(30)],
        "name": [f"n{i}" for i in range(30)],
        "composite": [float(90 - i) for i in range(30)],
        "industry": ["电子"] * 30,
        "ev_pos": [3.0 if i > 25 else 0.0 for i in range(30)],   # 事件票 composite 排名靠后
        "ev_hard": [3.0 if i > 25 else 0.0 for i in range(30)],  # 真实公司行为(非纯调研)= event 排序键
        "main_net_ratio": [0.1] * 30, "cmf_20": [0.1] * 30, "pct_60d": [5.0] * 30,
        "amount_yi": [5.0] * 30, "mktcap_yi": [80.0] * 30,
    })
    recall, _ = U.recall_select(scored, "2026-07-24", 20, "multi", ["composite"])
    out = tmp_path / "2026-07-24"
    out.mkdir(parents=True)
    made = U.write_shadow_variants(out, scored, recall, "2026-07-24", 20, 10, None, 1.0,
                                   list(scored.columns), recall_channels=["composite"])
    assert "plus_event" in made
    # ① plus_event 自己的逐路长表必须落盘(不能只靠 pre_healthy 的落盘掩盖 plus_event 没落的
    # 事实)——这就是 unique_excess_t2 真正要算的那张表,里面必须能看到 "event" 这个 channel。
    per = pd.read_csv(out / "shadow" / "L1_channels_plus_event.csv", dtype={"code": str})
    assert "event" in set(per["channel"])
    assert {"000026", "000027"}.issubset(set(per.loc[per["channel"] == "event", "code"].str.zfill(6)))

    # ② 显式给事件桶 floor(启用 event 路时的配置)→ 事件票才该被捞进 L2;production 默认
    # (DEFAULT_FLOORS["事件"]=0)下不该,见 docstring 的 C-1 说明。
    out2 = tmp_path / "with-floor"
    out2.mkdir(parents=True)
    U.write_shadow_variants(out2, scored, recall, "2026-07-24", 20, 10, {"事件": 10}, 1.0,
                            list(scored.columns), recall_channels=["composite"])
    codes2 = set(pd.read_csv(out2 / "shadow" / "L2_plus_event.csv",
                             dtype={"code": str})["code"].str.zfill(6))
    assert {"000026", "000027"}.issubset(codes2), "给了事件 floor 就该把事件票捞进 L2"
    codes = set(pd.read_csv(out / "shadow" / "L2_plus_event.csv",
                            dtype={"code": str})["code"].str.zfill(6))
    assert not ({"000026", "000027"} & codes), \
        "production 默认(事件 floor=0)下事件票不进 L2 —— 这正是 C-1 要的 parity"


def test_pre_healthy_uses_actual_enabled_channels_not_full_registry(tmp_path, monkeypatch):
    """bug 修复(顺手修):pre_healthy 反事实必须用调用方传入的 `recall_channels`(当日实际启用
    的路),不能恒取 `registered_channels()`(全部已注册路)——旧实现会把已停用的
    accumulation/northbound 混进"去掉 healthy,其余路照旧"的反事实,拿着两条早已不在生产
    召回里跑的路给 healthy 通道的捕获增量陪跑,读数失真。

    这两个新测试(本测试 + 前两个)都用 `recall_channels=["composite", ...]` 这种"生产里只
    启用一小撮路"的真实场景(scan_config.jsonc 当前 9 路,远少于全部 12 路注册路)——用
    `["composite","heat"]`(而非单一 "composite")是为了让落盘的长表里能看到两个不同
    channel 值,断言才有意义(单路时 `old==["composite"]` 这件事本身不足以证明"排除了未传入
    的路"和"排除了 registered_channels() 的其余路"是同一件事)。
    """
    import pandas as pd

    from autoresearch.scan import universe as U

    monkeypatch.setattr(U, "build_market_frame",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("no network in test")))
    scored = pd.DataFrame({
        "code": [f"{i:06d}" for i in range(30)],
        "name": [f"n{i}" for i in range(30)],
        "composite": [float(90 - i) for i in range(30)],
        "industry": ["电子"] * 30,
        "main_net_ratio": [0.1] * 30, "cmf_20": [0.1] * 30, "pct_60d": [5.0] * 30,
        "amount_yi": [5.0] * 30, "mktcap_yi": [80.0] * 30,
    })
    active = ["composite", "heat"]   # 当日实际启用路:故意不含 healthy,也不含 accumulation/northbound
    recall, _ = U.recall_select(scored, "2026-07-24", 20, "multi", active)
    out = tmp_path / "2026-07-24"
    out.mkdir(parents=True)
    U.write_shadow_variants(out, scored, recall, "2026-07-24", 20, 10, None, 1.0,
                            list(scored.columns), recall_channels=active)
    ch = pd.read_csv(out / "shadow" / "L1_channels_pre_healthy.csv")
    assert set(ch["channel"]) == {"composite", "heat"}, \
        "pre_healthy 反事实必须恰是 recall_channels 去掉 healthy,不多不少(不得混入未启用的注册路)"


# ───────────────────────── Review Round 1 m-3:同日重跑陈旧长表清理 ─────────────────────────
# `plus_event`/`capfloor20` 各自独立 try/except 兜底(不牵连其余零成本变体)。若同一 outdir
# 被重跑第二次且这次失败,`except` 分支不会重新落盘——若不清理,shadow/ 下停留的是上一次
# 成功时留下的文件,会被 `channel_audit --variant` 当成"今天的证据"读进十日账本,而此时
# stderr 的告警说的却是"这次失败"(证据与告警互相矛盾)。修法:重跑前(试算之前)先清掉
# 这两个变体各自的逐路长表——试算成功会立刻重新写出全新文件,试算失败则保持清空状态。


def test_shadow_plus_event_stale_file_cleared_on_rerun_failure(tmp_path, monkeypatch):
    """m-3:plus_event 这次失败(event 通道触发异常)→ shadow/ 下预置的"上一次成功"残留
    必须被清理,不能让 channel_audit --variant 读到陈旧数据。"""
    import pandas as pd

    from autoresearch.scan import universe as U

    monkeypatch.setattr(U, "build_market_frame",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("no network in test")))
    real_recall_select = U.recall_select

    def _fail_only_for_event(scored_df, date, n, mode, channels, **kw):
        if channels is not None and "event" in channels:
            raise RuntimeError("forced plus_event failure (m-3 rerun test)")
        return real_recall_select(scored_df, date, n, mode, channels, **kw)

    scored = pd.DataFrame({
        "code": [f"{i:06d}" for i in range(30)],
        "name": [f"n{i}" for i in range(30)],
        "composite": [float(90 - i) for i in range(30)],
        "industry": ["电子"] * 30,
        "main_net_ratio": [0.1] * 30, "cmf_20": [0.1] * 30, "pct_60d": [5.0] * 30,
        "amount_yi": [5.0] * 30, "mktcap_yi": [80.0] * 30,
    })
    recall, _ = U.recall_select(scored, "2026-07-24", 20, "multi", ["composite"])
    out = tmp_path / "2026-07-24"
    out.mkdir(parents=True)
    (out / "shadow").mkdir()
    stale = out / "shadow" / "L1_channels_plus_event.csv"
    stale.write_text("channel,code,channel_rank,channel_score\ncomposite,000099,1,9.0\n", encoding="utf-8")

    monkeypatch.setattr(U, "recall_select", _fail_only_for_event)
    U.write_shadow_variants(out, scored, recall, "2026-07-24", 20, 10, None, 1.0,
                            list(scored.columns), recall_channels=["composite"])

    assert not stale.exists(), \
        "m-3:plus_event 本次失败,shadow/ 下的陈旧逐路长表必须被清理,不能被 channel_audit --variant 读到"


def test_shadow_capfloor20_stale_file_cleared_on_rerun_failure(tmp_path, monkeypatch):
    """m-3:capfloor20 分支同形(见上一测试)——它是唯一重取数变体,本仓既有测试一律 mock
    `build_market_frame` 令其失败(隔离网络),这正好天然是"本次失败"场景:预置一份上次
    成功的残留,调用后必须被清理。"""
    import pandas as pd

    from autoresearch.scan import universe as U

    monkeypatch.setattr(U, "build_market_frame",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("no network in test")))
    scored = pd.DataFrame({
        "code": [f"{i:06d}" for i in range(30)],
        "name": [f"n{i}" for i in range(30)],
        "composite": [float(90 - i) for i in range(30)],
        "industry": ["电子"] * 30,
        "main_net_ratio": [0.1] * 30, "cmf_20": [0.1] * 30, "pct_60d": [5.0] * 30,
        "amount_yi": [5.0] * 30, "mktcap_yi": [80.0] * 30,
    })
    recall, _ = U.recall_select(scored, "2026-07-24", 20, "multi", ["composite"])
    out = tmp_path / "2026-07-24"
    out.mkdir(parents=True)
    (out / "shadow").mkdir()
    stale = out / "shadow" / "L1_channels_capfloor20.csv"
    stale.write_text("channel,code,channel_rank,channel_score\ncomposite,000099,1,9.0\n", encoding="utf-8")

    U.write_shadow_variants(out, scored, recall, "2026-07-24", 20, 10, None, 1.0,
                            list(scored.columns), recall_channels=["composite"])

    assert not stale.exists(), \
        "m-3:capfloor20 本次失败(mock 断网),shadow/ 下的陈旧逐路长表必须被清理"
