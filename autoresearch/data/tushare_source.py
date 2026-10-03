#!/usr/bin/env python3
"""scan-market · L0 备用取数源 —— tushare(替代被网络封锁的东财 push2)。

design: docs/specs/2026-06-20-scan-market-design.md(§4.4 坑③ 的"切换 universe 源")

背景:东财实时快照 `stock_zh_a_spot_em` / 资金流 `stock_individual_fund_flow_rank`
都在 `push2.eastmoney.com` 上,该主机常被中国大陆以外/部分 ISP 网络级封锁。tushare
(`api.tushare.pro`)是另一条链路,且 `daily_basic` 一把覆盖 市值/PE/PB/量比/换手/
**股息率**,`daily` 给价/量、算 60日·YTD 动量,`moneyflow` 给主动买卖单净流入;高权限 token
还能拿 `stk_factor_pro`(MA多头排列/RSI/MACD)与 `cyq_perf`(筹码获利比例)——比原
push2 设计更厚。

本模块只负责"取数 + 富化成 canonical 列",**打分/板块/输出全部复用 scan 编排**:
canonical 列与 `autoresearch.data.akshare_universe.fetch_universe`(东财路径)完全一致,外加
可选增强列(`dv_ratio` / `ma_bull` / `above_ma60` / `rsi6` / `winner_rate` / `cost_50pct`),
供价值/动量/反转透镜(列存在才用,缺则降级)与 L3a/L3b 使用。

基本面(营收/净利/YoY/ROE/毛利/CFO/所处行业)仍走能跑通的东财 datacenter 端点
`stock_yjbb_em`(akshare,**非** push2,未被封)——与东财路径同源,口径一致。

用法(被 autoresearch.scan.universe 调用):
  uv run --no-sync python -m autoresearch.scan.universe 2026-06-20 --source tushare
"""
from __future__ import annotations

import contextlib
import os
import time
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd

# 纯打分原语走包内(数值化 / 报告期推算)。
from autoresearch.common.scoring import _num, latest_reported_quarter, prev_quarter

# I/O·网络 helper(防御取列 / akshare 重试 / 硬门)走同 data 层的 akshare_universe(无 scripts/ 桥)。
from autoresearch.data.akshare_universe import _ak_call, _apply_universe_gates, _col

# ───────────────────────── token / pro 句柄 ─────────────────────────


def _load_env_token() -> str:
    """从环境或项目 .env 读 TUSHARE_TOKEN(不打印值)。"""
    tok = os.environ.get("TUSHARE_TOKEN")
    if not tok:
        envp = Path(".env")
        if envp.exists():
            for ln in envp.read_text(encoding="utf-8").splitlines():
                ln = ln.strip()
                if ln and not ln.startswith("#") and "=" in ln:
                    k, v = ln.split("=", 1)
                    if k.strip() == "TUSHARE_TOKEN":
                        tok = v.strip().strip('"').strip("'")
                        break
    if not tok:
        raise RuntimeError("TUSHARE_TOKEN 未配置(环境变量或项目根目录 .env)")
    return tok


def _pro():
    import tushare as ts

    return ts.pro_api(_load_env_token())


def _ts_call(fn, tries: int = 4, backoff: float = 1.5):
    """tushare 调用重试:限频(每分钟上限)→ 长睡;其它网络错 → 线性退避。"""
    last = None
    for i in range(tries):
        try:
            return fn()
        except Exception as e:  # noqa: BLE001
            last = e
            msg = str(e)
            if any(s in msg for s in ("每分钟", "频繁", "抱歉,您")):
                time.sleep(max(backoff * (i + 1), 8.0))
            else:
                time.sleep(backoff * (i + 1))
    raise last


def _code6(ts_code: pd.Series) -> pd.Series:
    """'600519.SH' → '600519'。"""
    return ts_code.astype(str).str.split(".").str[0].str.zfill(6)


# ───────────────────────── 湖取数门面(生产帧唯一的取数口) ─────────────────────────


def _lake_fetch(endpoint: str, params: dict, analysis_date: str) -> pd.DataFrame:
    """经数据湖取一个端点 —— **生产帧的每条 tushare 取数都必须走这里**。

    为什么:`cache.get_or_fetch` 是 A 级数据契约(`contracts.check`)唯一的挂载点 —— 空帧 /
    行数腰斩 / 缺关键列 → `DataContractError` 阻断**且拒绝入湖**;B 级缺失 → 自动记一笔降级。
    2026-08-29 之前 `fetch_universe_tushare` 是裸调 `pro.X()`(只有 60 日 `daily` 面板走湖),
    于是这套契约**在生产路径上一次都没执行过**,它只在 prewarm / backfill / doctor 里跑过。
    三条实测后果(spec 2026-08-29 §1.3):`north`/`rz` 两组自 07-13 起每次扫描非空率 0.0;
    08-26 与 07-29 的 `chip`/`tech` 组非空率 0.547(21:xx 的半载快照);hk_hold 的"空返回"
    其实是发布滞后 —— 三者没有任何一条在账本上留过痕。

    **不传 `fields`**:湖里一个 key 只有一个 parquet,而 `cache._cache_key` 不含 fields ——
    带窄 `fields` 的查询一旦成为某 key 的首个写入者,就把窄表钉成了这一天的湖快照
    (2026-07-12 M1 对拍实证:`daily` 被钉成两列 → volprice 组整组 NaN → 全市场打分失真
    98.8%、L2 名单 jaccard 0.36)。**多几列无害,少一列是灾难。**

    `analysis_date` 作为 `today=` 传下去:它决定"已结算(可入湖)/ 盘中未结算(拉新不写)"
    的分支,漏传会把当天的帧误判成可永久钉死的历史。
    """
    from autoresearch.data import cache

    return cache.get_or_fetch(endpoint, dict(params), today=analysis_date)


# ───────────────────────── 交易日历 ─────────────────────────


def _trade_days(pro, start: str, end: str) -> list[str]:
    cal = _ts_call(lambda: pro.trade_cal(exchange="SSE", start_date=start, end_date=end, is_open="1"))
    return sorted(cal["cal_date"].astype(str).tolist())


def resolve_momentum_dates(pro, analysis_date: str) -> tuple[str, str, str]:
    """返回(最近交易日 last, 60交易日前 d60, 年初首个交易日 dys)。

    A股以分析日所在自然日为界,取 ≤ 分析日 的最近开市日做"现价"基准;动量用真实
    交易日间隔(非自然日),避免节假日扭曲。
    """
    yyyymmdd = analysis_date.replace("-", "")
    year = int(yyyymmdd[:4])
    days = _trade_days(pro, f"{year - 1}0101", yyyymmdd)
    if not days:
        raise RuntimeError(f"无交易日(start={year - 1}0101 end={yyyymmdd})")
    last = days[-1]
    idx = days.index(last)
    d60 = days[max(0, idx - 60)]
    ys_days = [d for d in days if d[:4] == str(year)]
    dys = ys_days[0] if ys_days else days[max(0, idx - 120)]
    return last, d60, dys


def assert_tushare_ready(pro, last: str) -> None:
    """盘后数据就绪硬门:探 daily/moneyflow/cyq_perf 是否已落 `last` 日。

    A股是 EOD 系统,跑"今天"必须等今天收盘后数据全部发布(筹码/主力常 ≥19:00 才落)。
    **空结果**(端点已发布但当日无数据=跑太早)→ 抛 RuntimeError **中止整条流程**,不静默跑残缺;
    **异常**(权限/网络)→ 跳过不据此 hard-gate(沿用富因子缺权限自动降级语义)。
    """
    not_ready = []
    for label, ep in (("daily 日线", "daily"), ("moneyflow 主力资金", "moneyflow"), ("cyq_perf 筹码", "cyq_perf")):
        try:
            df = getattr(pro, ep)(trade_date=last, fields="ts_code")
        except Exception:  # noqa: BLE001 — 权限/网络:不据此 hard-gate
            continue
        if df is None or len(df) == 0:
            not_ready.append(label)
    if not_ready:
        raise RuntimeError(
            f"[数据未就绪] tushare 尚未落 {last} 的盘后数据:缺 {' / '.join(not_ready)}。\n"
            f"  A股盘后通常 ≥19:00(筹码/主力)才齐 → 晚点再跑,或直接跑前一交易日(数据必全)。\n"
            f"  整条扫描流程已中止(拒绝静默跑残缺数据)。")


# ───────────────────────── 基本面(东财 datacenter,非 push2) ─────────────────────────


def fetch_fundamentals_yjbb(analysis_date: str) -> pd.DataFrame:
    """业绩(当期+上期 YoY 算加速度)+ 所处行业,经 akshare `stock_yjbb_em`。

    与 akshare_universe.fetch_universe 的东财路径同源、同口径(同样的列)。该端点在
    datacenter-web.eastmoney.com,**不在被封的 push2**。
    """
    import akshare as ak

    q = latest_reported_quarter(analysis_date)
    yj = _ak_call(lambda: ak.stock_yjbb_em(date=q))
    yj_code = _col(yj, "股票代码", required=True)
    fin = pd.DataFrame(
        {
            "code": yj[yj_code].astype(str).str.zfill(6),
            "rev": _num(yj[_col(yj, "营业总收入-营业总收入", "营业总收入")]),
            "rev_yoy": _num(yj[_col(yj, "营业总收入-同比增长")]),
            "np_": _num(yj[_col(yj, "净利润-净利润", "净利润")]),
            "np_yoy": _num(yj[_col(yj, "净利润-同比增长")]),
            "np_qoq": _num(yj[_col(yj, "净利润-季度环比增长")]),
            "roe": _num(yj[_col(yj, "净资产收益率")]),
            "gross_margin": _num(yj[_col(yj, "销售毛利率")]),
            "cfo_ps": _num(yj[_col(yj, "每股经营现金流量")]),
            "industry": yj[_col(yj, "所处行业")].astype("object") if _col(yj, "所处行业") else "未分类",
        }
    )
    try:
        yjp = _ak_call(lambda: ak.stock_yjbb_em(date=prev_quarter(q)))
        prev = pd.DataFrame(
            {
                "code": yjp[_col(yjp, "股票代码", required=True)].astype(str).str.zfill(6),
                "np_yoy_prev": _num(yjp[_col(yjp, "净利润-同比增长")]),
                "rev_yoy_prev": _num(yjp[_col(yjp, "营业总收入-同比增长")]),
            }
        )
        fin = fin.merge(prev, on="code", how="left")
    except Exception as e:  # noqa: BLE001
        print(f"[warn] 上期业绩取数失败({e!r})→ 成长加速度降级", flush=True)
        fin["np_yoy_prev"] = np.nan
        fin["rev_yoy_prev"] = np.nan
    return fin


# ───────────────────────── tushare 增强因子(可选,高权限 token) ─────────────────────────


def _fetch_factors(last: str, analysis_date: str) -> pd.DataFrame | None:
    """stk_factor_pro(MA多头排列/RSI)+ cyq_perf(筹码获利比例)——**两条腿都经湖**。失败 → None。

    两个端点在 `contracts.CONTRACTS` 里都是 **A 级**(tech 组 / chip 组的地基)。于是这里的
    `except Exception` 有两层职责必须分开:

    - `DataContractError`(空 / 行数腰斩 / 缺关键列)**必须炸穿**,不得被吞回"降级 → return
      None"。08-26 与 07-29 的 chip/tech 组非空率 0.547(21:xx 的 tushare 半载快照)就是从
      "阻断退化成 None"这条路走过去的:组整组半残 → `composite_score` 把它从分母剔除、
      放大其余组权重 → 打分照样输出 0–100,残废得看不出来。
    - 权限 / 网络类失败仍是合法降级(低权限 token 本就没有这两个端点),但**必须记账** ——
      此前只有一行 `print`,没人看得见,账本上也没有任何一行记得这件事。
    """
    from autoresearch.data.contracts import DataContractError, record_degradation

    try:
        sf = _lake_fetch("stk_factor_pro", {"trade_date": last}, analysis_date)
        c = _num(sf["close"])
        m5, m10, m20, m60 = (_num(sf[f"ma_qfq_{n}"]) for n in (5, 10, 20, 60))
        fac = pd.DataFrame(
            {
                "code": _code6(sf["ts_code"]),
                "ma_bull": ((m5 > m10) & (m10 > m20) & (m20 > m60)).astype(float),  # 多头排列
                "above_ma60": (c > m60).astype(float),
                "rsi6": _num(sf["rsi_qfq_6"]),
                "rsi12": _num(sf["rsi_qfq_12"]),
                "macd": _num(sf["macd_qfq"]),
            }
        )
    except DataContractError:
        raise                       # A 级契约违约:阻断整条流程,不许退化成"降级"
    except Exception as e:  # noqa: BLE001 — 权限/网络:合法降级,但必须留痕
        print(f"[warn] stk_factor_pro 取数失败({e!r})→ 趋势结构降级为代理", flush=True)
        record_degradation("stk_factor_pro", f"取数失败({e!r})→ tech 组(rsi6/rsi12)整组置空",
                           key=last)
        return None
    try:
        cy = _lake_fetch("cyq_perf", {"trade_date": last}, analysis_date)
        c50 = _num(cy["cost_50pct"])
        cyf = pd.DataFrame(
            {
                "code": _code6(cy["ts_code"]),
                "winner_rate": _num(cy["winner_rate"]),  # 获利比例(0–100)
                "cost_50pct": c50,                        # 筹码平均成本(中位)
                # 筹码集中度 = (cost_85-cost_15)/cost_50;越小越集中(主力控盘),越大越分散
                "chip_concentration": (_num(cy["cost_85pct"]) - _num(cy["cost_15pct"])) / c50,
            }
        )
        fac = fac.merge(cyf, on="code", how="left")
    except DataContractError:
        raise                       # 同上:chip 组的地基,阻断不得被吞
    except Exception as e:  # noqa: BLE001 — 权限/网络:合法降级,但必须留痕
        print(f"[warn] cyq_perf 取数失败({e!r})→ 筹码因子缺省", flush=True)
        record_degradation("cyq_perf", f"取数失败({e!r})→ chip 组(筹码集中度/浮盈)整组置空",
                           key=last)
    return fac


def _moneyflow_struct_cols(mf: pd.DataFrame) -> pd.DataFrame:
    """moneyflow 全单结构 → 大单+特大单净额(主力)+ 小单净额(散户),单位亿。纯函数,

    便于离线测且与 factor_lab 共用同一口径。万元 /1e4=亿;主力净占比(/成交额)在调用处算
    (moneyflow 端点未必带 amount,用 daily 的成交额更稳)。
    """
    def g(c):
        return _num(mf[c]) if c in mf.columns else 0.0

    main_net = g("buy_lg_amount") + g("buy_elg_amount") - g("sell_lg_amount") - g("sell_elg_amount")
    retail_net = g("buy_sm_amount") - g("sell_sm_amount")
    return pd.DataFrame({
        "code": _code6(mf["ts_code"]),
        "main_net_yi": main_net / 1e4,        # 大单+特大单净(亿)，非机构身份
        "retail_net_yi": retail_net / 1e4,    # 小单净(亿)，非散户身份
    })


def _fetch_moneyflow_struct(last: str, analysis_date: str) -> pd.DataFrame | None:
    """moneyflow 结构(大单/小单净额 + 主动买卖单净流入)——**经湖**。失败 → None(降级)。

    main_inflow_yi/main_net_yi/retail_net_yi 保留兼容列名；指标语义见 metric_semantics。
    交易规模与主动方向是代理，不能确认机构或散户身份。

    `moneyflow` 是 A 级(fund_main 组 + `main_net_ratio`)→ `DataContractError` 必须炸穿;
    权限/网络失败仍降级,但记账。
    """
    from autoresearch.data.contracts import DataContractError, record_degradation

    try:
        mf = _lake_fetch("moneyflow", {"trade_date": last}, analysis_date)
        out = _moneyflow_struct_cols(mf)
        out["main_inflow_yi"] = _num(mf["net_mf_amount"]) / 1e4   # 沿用原 canonical 列
        return out
    except DataContractError:
        raise
    except Exception as e:  # noqa: BLE001 — 权限/网络:合法降级,但必须留痕
        print(f"[warn] moneyflow 结构取数失败({e!r})→ 资金结构因子降级", flush=True)
        record_degradation("moneyflow", f"取数失败({e!r})→ fund_main/fund_retail 两组置空",
                           key=last)
        return None


def _fetch_hk_hold(last: str, analysis_date: str) -> pd.DataFrame | None:
    """北向持股占比(hk_hold;ratio = 占流通股比 %)。B 级(增强)→ 失败/空 = 降级,但**必须记账**。

    2026-07-09 实证:该日 `hk_ratio` 全表为空(north 组整组失效),当时唯一的痕迹是一行
    `[warn] hk_hold 取数失败`,没人看见、也没有任何账本记得——直到回放器对拍时才被发现。
    降级本身是合法的(北向确有空档期,且 northbound 通道已停用),**但它必须是可见的**
    (design 2026-07-12-data-contracts-design.md)。

    经湖(2026-08-29 T3):取数日**不变**(改成 T−1 是待裁的 Q8)—— 这里只保证失败/空留痕。
    08-28 普查回填对同样的 `hk_hold 20260825/26/27` 拿到 958 行,而生产当晚拿到空 → 生产的
    "空"多半是 T 晚取数撞上发布滞后(滞后时长 UNVERIFIED),不是真的没有北向。

    ⚠️ **未裁的口径问题(2026-08-29 实测,只留痕不改)**:湖里 `hk_hold/20260826` 的 958 行
    `exchange` **全是 HK**(港股通**南向**持股,`ts_code` 形如 `00001.HK`)。`_code6` 会把
    `00001.HK` 补成 `'000001'`,与 A 股代码**撞号** —— 实测 297 个 A 股代码因此拿到了港股的
    持股比例。改口径(按 `exchange ∈ {SH,SZ}` 过滤)会动打分输入,超出本任务"只改数据从哪来"
    的边界,故**本次不改**:只在"一行北向都没有"时记一笔降级,把这个现场摆到账本上。
    """
    from autoresearch.data.contracts import DataContractError, record_degradation

    try:
        hk = _lake_fetch("hk_hold", {"trade_date": last}, analysis_date)
    except DataContractError:
        raise                       # 湖里那份是毒源(空/坏)时照样炸穿,别静默吃掉
    except Exception as e:  # noqa: BLE001 — B 级:降级合法,但必须记账
        record_degradation("hk_hold", f"取数失败({e!r})→ north 组置空", key=last)
        return None
    if hk is None or not len(hk):
        record_degradation("hk_hold", "空返回(该日无北向数据)→ north 组置空", key=last)
        return None
    # ── 代码域硬门(2026-08-30 裁定):只认**北向**(沪/深股通持有 A 股),南向一行不要 ──
    #
    # `hk_hold` 一个端点装了两个方向:北向(exchange=SH/SZ,`ts_code` 是 A 股)与南向
    # (exchange=HK,`ts_code` 形如 `00001.HK`)。而 `hk_ratio` 的语义是**北向持股占比**。
    #
    # 不过滤的后果是**串号**,不是缺数:`_code6("00001.HK")` → `"000001"`,那是平安银行;
    # `00002.HK`(中电控股)→ `000002` 万科A。2026-08-30 实测当日 958 行南向数据里
    # **297 个**落进了当日 L0 池的真实 A 股代码上 —— 于是平安银行拿着长和的持股比例进了
    # composite 的 `north` 组。这类错**比缺数难发现得多**:字段有值、量级也像,只是属于别人。
    #
    # 为什么过滤完就空了(实测 `exchange=SH` / `SZ` 各返回 **0 行**):**北向个股持股明细
    # 自 2024-08 起停止披露**(macro-playbook 数据坑 #3 早记过这条,中观北向因此改用
    # `moneyflow_hsgt` 官方日频汇总)。也就是说 `hk_ratio` 这一列**没有合法数据源**了,
    # 而不是"今天恰好没有"。
    #
    # 所以这里的正确行为是**诚实缺席**:北向为空 → 返回 None → `north` 组 NaN → 打分时
    # 该组被重新归一剔除(既有的 B 级降级语义)。这与生产在扫描夜实际拿到的结果一致
    # (那些夜里 live 调用因发布滞后返回空,`north` 本来就是 NaN)—— 换句话说本改动
    # **不改扫描夜的既有行为,只拿掉数据补齐后才会显形的那份污染**。
    if "exchange" in hk.columns:
        north = hk[hk["exchange"].astype(str).isin(("SH", "SZ"))]
        if not len(north):
            record_degradation(
                "hk_hold",
                f"{len(hk)} 行全是港股通**南向**(exchange=HK)、北向 0 行 → hk_ratio 诚实置空。"
                f"北向个股持股明细 2024-08 起停止披露(实测 exchange=SH/SZ 各返回 0 行),"
                f"该列**无合法数据源**;不过滤会让 `_code6('00001.HK')='000001'` 把港股比例"
                f"串到平安银行等 A 股上(当日实测撞号 297 只)",
                key=last)
            return None
        hk = north
    return pd.DataFrame({"code": _code6(hk["ts_code"]), "hk_ratio": _num(hk["ratio"])})


def _fetch_fund_portfolio(pro, period: str, page_size: int = 8000,
                          max_pages: int = 60) -> pd.DataFrame | None:
    """基金重仓(fund_portfolio;按 period=季度末 YYYYMMDD 批量,单页上限 8000 行需翻页——
    探针 2026-07-10 实证,见 context/factor_lab/cache/probes/fund_portfolio_20260710.json)。

    该端点不支持按个股直查(ts_code/ann_date/period 三选一起效,个股不在其中)→ 反查姿势 =
    本函数按 period 批量拉全量、调用方(`scan.agents.l4_card.fetch_fund_hold`)按 symbol
    (重仓股代码)本地过滤。失败/空 → None(降级)。"""
    frames: list[pd.DataFrame] = []
    offset = 0
    try:
        while True:
            if offset // page_size >= max_pages:   # offset 语义若被上游破坏,防同页空转撞限频
                print(f"[warn] fund_portfolio 翻页达上限 {max_pages} 页→截断", flush=True)
                break
            page = _ts_call(lambda offset=offset: pro.fund_portfolio(
                period=period, offset=offset, limit=page_size))
            if page is None or not len(page):
                break
            frames.append(page)
            if len(page) < page_size:
                break
            offset += page_size
    except Exception as e:  # noqa: BLE001
        print(f"[warn] fund_portfolio 取数失败({e!r})→ 机构面基金行降级", flush=True)
        return None
    return pd.concat(frames, ignore_index=True) if frames else None


# ───────────────────────── S1 情绪温度计数据(lake-backed;consumer=autoresearch.scan.temperature) ─────────────────────────


def fetch_limit_list_d(trade_date: str) -> pd.DataFrame:
    """涨跌停明细单日快照(`limit`∈U涨停/D跌停/Z炸板,`limit_times`=连板数——07-11 真数据探针
    确认仅 U 行有值,Z/D 行为 NaN)。

    走 `cache.get_or_fetch`(policy 已登记 `limit_list_d`: key=date/settle=eod/source=tushare,
    见 `autoresearch.data.endpoints`)——已结算日湖命中零网络,重复调用不二次取数;缺省路由
    经 `sources.fetch`→`_fetch_tushare`,内部仍是本模块的 `_pro()` + `_ts_call` 限频重试。

    权限/网络异常原样向上抛(不在此吞错)——presence-gated 的降级责任在调用方(如
    `autoresearch.scan.temperature.rollup`,按天捕获、跳过、记 warn),与
    `l3_catalyst.harvest_catalyst` 既有惯例一致。
    """
    from autoresearch.data.cache import get_or_fetch

    params = {
        "trade_date": trade_date,
        "fields": "ts_code,trade_date,limit,limit_times,open_times",
    }
    return get_or_fetch("limit_list_d", params)


# ───────────────────────── L0 universe(tushare) ─────────────────────────

_RAW_COUNT: dict = {}   # 全A(硬门前)原始数;放本模块(单次 import)避开 __main__/scan.universe 双模块陷阱


# `stock_basic` 的湖副本"多旧算旧"—— **换了自然日就重拉**。
_STOCK_BASIC_PARAMS = {"list_status": "L"}


def _lake_day(epoch: float) -> str:
    return datetime.fromtimestamp(epoch).strftime("%Y-%m-%d")


def _stock_basic_stale(path: Path, now: float | None = None) -> bool:
    """湖副本是否**不是今天取的**(不存在 = 陈旧)。

    2026-08-29 复核把粒度从 ISO 周收紧到自然日 —— 周粒度带进了一个**真实的门衰减**:
    周中新上市的票在本周内不会被重取,于是它在 `stock_basic` 里根本没有行 →
    `list_date` 是 NaN → 剔次新硬门 `ld > int(d60)` 恒 False(**它混进候选池**),
    同时 `name` 也是 NaN → ST/退 门在名字里找不到 "ST" 就**也放它过**。
    两道 L0 硬门同时失灵最长 6 天,周界才自愈。

    硬门失灵的代价不对称:多拉一次 `stock_basic` 是每趟一次小取数(5.5k 行、一列表),
    而漏掉一次次新/ST 剔除会把一只不该研究的票一路送进 L3/L4。所以按日刷新 ——
    这正好也是接湖之前的老行为(生产帧每趟裸调 `pro.stock_basic` 拿最新名单),
    于是这条改动对硬门是**逐字节 parity**,只是多了 A 级契约与湖留痕。
    """
    if not path.exists():
        return True
    return _lake_day(path.stat().st_mtime) != _lake_day(now if now is not None else time.time())


def _fetch_stock_basic(analysis_date: str) -> pd.DataFrame:
    """证券基础(名称 + 上市日)经湖 + **每日刷新**。

    `stock_basic` 的 policy 键是 `static`(湖里只有一份 `static.parquet`),而 static 的语义是
    "存在即命中、永不重取" —— 工作树里那份的 mtime 停在 **2026-06-22**。生产帧此前是裸调
    `pro.stock_basic` 拿最新名单所以没中招;把它接进湖之后若沿用 static 语义,新上市的票会
    永远没有 `name` / `list_date`,于是剔次新硬门(`list_date > d60`,NaN 比较恒 False)与 ST
    门(名字里找 "ST"/"退")都看不见它们 —— 名单会随时间慢慢腐坏。

    故按**自然日**刷新:湖副本不是今天的 → 先把它挪开(不是删:取数失败要放回去)再经
    `get_or_fetch` 重新入湖,一周一份、既不每天重拉也不会两个月不动。
    (为什么不直接给 policy 加一个 `week` 键:那要同时改 `data/endpoints.py` 与 `data/cache.py`
    的 `_cache_key`,两份都不在本任务的文件所有权内 —— 见 plan Wave1 文件所有权表。)

    失败语义分两级:`DataContractError`(A 级:空/腰斩/缺列)**照样炸穿**,只是先把旧副本放
    回去别把湖弄丢;权限/网络类失败 → 放回旧副本、记一笔降级并沿用它(宁可用上周的名单,也
    不要没有名单;内容契约仍会在读旧副本时再跑一遍)。
    """
    from autoresearch.data import cache
    from autoresearch.data.contracts import DataContractError, record_degradation

    params = dict(_STOCK_BASIC_PARAMS)
    path = cache.lake_path("stock_basic", params)
    parked: Path | None = None
    if _stock_basic_stale(path):
        if path.exists():
            parked = path.with_suffix(path.suffix + ".prev")
            with contextlib.suppress(OSError):
                os.replace(path, parked)
            if path.exists():                       # 挪失败 → 当作没挪(下面按命中处理)
                parked = None
        print(f"[L0·tushare] stock_basic 湖副本非今日({_lake_day(time.time())})→ 重新入湖",
              flush=True)
    try:
        sb = cache.get_or_fetch("stock_basic", params, today=analysis_date)
    except DataContractError:
        if parked is not None:                      # 契约违约的那份本就没入湖,把旧的放回去
            with contextlib.suppress(OSError):
                os.replace(parked, path)
        raise
    except Exception as e:  # noqa: BLE001 — 权限/网络:退回上一份名单,但必须留痕
        if parked is None:
            raise
        os.replace(parked, path)
        record_degradation(
            "stock_basic",
            f"日刷新取数失败({e!r})→ 沿用上一份湖副本({_lake_day(path.stat().st_mtime)});"
            f"该周内新上市的票会缺 name/list_date(剔次新门与 ST 门看不见它们)",
            key=_lake_day(time.time()))
        return cache.get_or_fetch("stock_basic", params, today=analysis_date)
    if parked is not None:
        with contextlib.suppress(OSError):
            parked.unlink()
    return sb


def _margin_rz_cols(mg: pd.DataFrame) -> pd.DataFrame:
    """margin_detail → code + rzmre_yuan(融资买入额,元)。纯函数无网络,selftest 可测。"""
    return pd.DataFrame({"code": _code6(mg["ts_code"]), "rzmre_yuan": _num(mg["rzmre"])})


def _fetch_margin_rz(last: str, analysis_date: str) -> pd.DataFrame | None:
    """margin_detail(两融明细)→ rz 原料。经湖;失败/空 → None(rz 组 NaN,composite 重归一跳过)。

    B 级降级本身合法,但此前**完全无声**:空返回那条分支连 `print` 都没有,只有异常分支打一行
    warn。于是 `rz` 组自 07-13 起每次扫描非空率 **0.0**,账本上一行记录都没有 —— 直到 08-29
    的接线审计才被发现(spec 2026-08-29 §1.3 ①)。两条路现在都 `record_degradation`。
    """
    from autoresearch.data.contracts import DataContractError, record_degradation

    try:
        mg = _lake_fetch("margin_detail", {"trade_date": last}, analysis_date)
        if mg is None or mg.empty:
            print("[warn] margin_detail 空返回 → rz_buy_intensity 降级", flush=True)
            record_degradation("margin_detail", "空返回(该日无两融明细)→ rz 组置空", key=last)
            return None
        return _margin_rz_cols(mg)
    except DataContractError:
        raise
    except Exception as e:  # noqa: BLE001 — B 级:降级合法,但必须记账
        print(f"[warn] margin_detail 取数失败({e!r})→ rz_buy_intensity 降级", flush=True)
        record_degradation("margin_detail", f"取数失败({e!r})→ rz 组置空", key=last)
        return None


def fetch_universe_tushare(
    analysis_date: str,
    cap_floor_yi: float = 30.0,
    include_bj: bool = False,
    with_factors: bool = True,
) -> pd.DataFrame:
    """tushare 版 L0:daily_basic + daily(×3) + moneyflow + yjbb(基本面) → canonical df。

    单位换算:total_mv/net_mf_amount 为**万元**(/1e4→亿);daily.amount 为**千元**
    (/1e5→亿)。动量用原始收盘价 60日/YTD 涨跌(高召回粗筛代理,除权噪声留 L3b 核)。

    **取数一律经湖**(2026-08-29 T3,spec §1.3 第一行):九条 tushare 取数
    (`daily_basic` / `daily`×3 / `stock_basic` / `moneyflow` / `margin_detail` /
    `stk_factor_pro` / `cyq_perf` / `hk_hold`)此前全是裸 `pro.X()`,只有 60 日 `daily` 面板
    走湖 —— A 级数据契约因此**在生产路径上一次都没执行过**。现在全部经 `_lake_fetch`
    (= `cache.get_or_fetch`,**不传 fields**),契约回到生产路径上。
    """
    pro = _pro()
    last, d60, dys = resolve_momentum_dates(pro, analysis_date)
    assert_tushare_ready(pro, last)   # 盘后就绪硬门:今天数据没落全 → 抛错中止,别静默跑残缺
    print(f"[L0·tushare] as-of 交易日={last}  60日前={d60}  年初={dys}", flush=True)

    # 每日指标:市值/PE/PB/量比/换手/股息率(A 级)
    db = _lake_fetch("daily_basic", {"trade_date": last}, analysis_date)
    # 日线:价/涨跌/成交额(+ 60日前、年初 收盘价算动量)(A 级)
    dl = _lake_fetch("daily", {"trade_date": last}, analysis_date)
    dl60 = _lake_fetch("daily", {"trade_date": d60}, analysis_date)
    dlys = _lake_fetch("daily", {"trade_date": dys}, analysis_date)
    # 名称 + 上市日(剔次新):static 键 + 周刷新,见 `_fetch_stock_basic`
    sb = _fetch_stock_basic(analysis_date)

    df = pd.DataFrame(
        {
            "code": _code6(db["ts_code"]),
            "close": _num(db["close"]),
            "turnover": _num(db["turnover_rate"]),
            "vol_ratio": _num(db["volume_ratio"]),
            "pe": _num(db["pe_ttm"]),
            "pb": _num(db["pb"]),
            "dv_ratio": _num(db["dv_ratio"]),
            "mktcap_yi": _num(db["total_mv"]) / 1e4,   # 万元 → 亿元
        }
    )
    # 日线价/涨跌/成交额
    dl_ = pd.DataFrame(
        {
            "code": _code6(dl["ts_code"]),
            "pct_1d": _num(dl["pct_chg"]),
            "amount_yi": _num(dl["amount"]) / 1e5,     # 千元 → 亿元
            "close_now": _num(dl["close"]),
        }
    )
    df = df.merge(dl_, on="code", how="left")
    df = df.merge(pd.DataFrame({"code": _code6(dl60["ts_code"]), "c60": _num(dl60["close"])}), on="code", how="left")
    df = df.merge(pd.DataFrame({"code": _code6(dlys["ts_code"]), "cys": _num(dlys["close"])}), on="code", how="left")
    df["pct_60d"] = (df["close_now"] / df["c60"] - 1) * 100
    df["pct_ytd"] = (df["close_now"] / df["cys"] - 1) * 100
    # 资金结构(主动买卖单净流入 + 大单/特大单净 + 小单净；canonical 列名保持兼容)
    mfs = _fetch_moneyflow_struct(last, analysis_date)
    if mfs is not None:
        df = df.merge(mfs, on="code", how="left")
        df["main_net_ratio"] = df["main_net_yi"] / df["amount_yi"].replace(0, np.nan)
    else:
        for c in ("main_inflow_yi", "main_net_yi", "retail_net_yi", "main_net_ratio"):
            df[c] = np.nan
    # 融资买入强度(FN-1 第五修:pr_20260710_001 rz 入组后,生产帧此前无此列 → 组恒 NaN = no-op)。
    # 口径同 factor_lab.py:rzmre(元)/当日成交额(元);amount_yi 亿 → ×1e8。
    mg = _fetch_margin_rz(last, analysis_date)
    if mg is not None:
        df = df.merge(mg, on="code", how="left")
        df["rz_buy_intensity"] = df["rzmre_yuan"] / (df["amount_yi"] * 1e8).replace(0, np.nan)
        df = df.drop(columns=["rzmre_yuan"])
    else:
        df["rz_buy_intensity"] = np.nan
    # 名称 + 上市日
    sb_ = pd.DataFrame({"code": _code6(sb["ts_code"]), "name": sb["name"].astype(str), "list_date": sb["list_date"].astype(str)})
    df = df.merge(sb_, on="code", how="left")

    # 增强因子(可选):技术/筹码 + 北向 + 现价相对筹码成本
    if with_factors:
        fac = _fetch_factors(last, analysis_date)
        if fac is not None:
            df = df.merge(fac, on="code", how="left")
        hk = _fetch_hk_hold(last, analysis_date)
        if hk is not None:
            df = df.merge(hk, on="code", how="left")
        if "cost_50pct" in df.columns:
            df["price_to_cost"] = df["close"] / df["cost_50pct"]   # >1 浮盈、<1 套牢

    # 基本面(yjbb,东财 datacenter)
    fin = fetch_fundamentals_yjbb(analysis_date)
    df = df.merge(fin, on="code", how="left")
    df["industry"] = df["industry"].fillna("未分类").replace("", "未分类")

    # 剔次新:上市不足 60 交易日(list_date > d60);缺失上市日 → 保留(NaN>x=False)
    before = len(df)
    ld = pd.to_numeric(df["list_date"], errors="coerce")   # 'YYYYMMDD'→int;缺/“nan”→NaN
    df = df[~(ld > int(d60))].reset_index(drop=True)
    if before - len(df):
        print(f"[L0·tushare] 剔次新(上市>{d60}): -{before - len(df)}", flush=True)

    _RAW_COUNT["n"] = len(df)   # 全A(剔次新后、硬门前)→ 供漏斗显示 全A → 硬门
    # 复用东财路径同一套硬门(ST/市值地板/停牌代理/北交所)
    return _apply_universe_gates(df, cap_floor_yi=cap_floor_yi, include_bj=include_bj)


# ───────────────────────── 离线自测(无网络) ─────────────────────────


def _selftest_struct() -> int:
    """验 moneyflow 结构纯函数(主力/散户净额单位)。无网络。"""
    mf = pd.DataFrame({
        "ts_code": ["600000.SH"], "buy_lg_amount": [5000.0], "buy_elg_amount": [3000.0],
        "sell_lg_amount": [2000.0], "sell_elg_amount": [1000.0],
        "buy_sm_amount": [800.0], "sell_sm_amount": [1500.0],
    })
    s = _moneyflow_struct_cols(mf).iloc[0]
    fails = []
    if abs(s["main_net_yi"] - 0.5) > 1e-9:        # (5000+3000-2000-1000)万 = 5000万 = 0.5亿
        fails.append(f"main_net_yi={s['main_net_yi']}")
    if abs(s["retail_net_yi"] - (-0.07)) > 1e-9:  # (800-1500)万 = -700万 = -0.07亿
        fails.append(f"retail_net_yi={s['retail_net_yi']}")
    if fails:
        print("SELFTEST ❌")
        for f in fails:
            print(" -", f)
        return 1
    print("SELFTEST ✅  moneyflow 结构(主力净/散户净 单位亿)正确")
    return 0


if __name__ == "__main__":
    import sys

    if "--selftest" in sys.argv:
        raise SystemExit(_selftest_struct())
