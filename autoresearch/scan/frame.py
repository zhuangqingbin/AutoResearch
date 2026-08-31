#!/usr/bin/env python3
"""scan-market · 全市场因子帧(L0 取数 + 轻门 + 多日量价,零打分零召回)+ 盘前哨兵预告 CLI。

design: docs/specs/2026-07-03-research-skills-altitude-refactor-design.md §5.1(Phase 0)。

`build_market_frame` = `universe.run` 前半段的抽取(**单一代码路径**:run / L1Recall stage /
本 CLI 三处共用同一 `_harvest_vol_series`;测试 patch 锚点也统一在本模块)。产出的帧就是
`classify_regime` / `market_pack_from_frame` / `sentinel_advice_from_frame` / `healthy_riser_mask`
的输入 → 宏观 lite(Stage 0)与盘前 cron 不再依赖 universe 产物(L1_scored_full)。

用法(盘前预告,零 LLM):
  uv run --no-sync python -m autoresearch.scan.frame 2026-07-03            # regime + 哨兵预告
  uv run --no-sync python -m autoresearch.scan.frame 2026-07-03 --json     # 另打印 market_pack JSON
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd

from autoresearch.common import workspace as ws
from autoresearch.data.contracts import check_market_frame


def _recall_gate_a(df: pd.DataFrame, min_amount_yi: float = 0.0, min_list_days: int = 0) -> pd.Series:
    """L1 召回轻门:只去真正不可交易/无核心数据的尾部(召回优先,尽量不误杀)。

    `min_list_days`>0 且帧有 `list_days` 列 → 剔次新(上市<阈值日,量价/IC 因子无意义);缺列降级不剔。
    默认两门 =0 → 与改动前逐值一致(parity)。
    """
    keep = df["amount_yi"].fillna(0) > min_amount_yi       # 有流动性/非停牌
    keep &= df["close"].notna()                            # 有价
    keep &= df["pct_60d"].notna() | df["pct_ytd"].notna()  # 有动量价(打分核心)
    if min_list_days > 0 and "list_days" in df.columns:    # 次新过滤(有 list_days 才生效,缺则降级)
        keep &= pd.to_numeric(df["list_days"], errors="coerce").fillna(1e9) >= min_list_days
    return keep


_VOL_MIN_DAYS = 10          # 少于 10 个交易日算不出 20 日 CMF/OBV —— 与原 `len(days) < 10` 同阈值
_PANEL_LOOKBACK = 60        # 2026-08-21 低位转强波:20→60。20 日组**仍只喂最后 20 列**(等值锁见 tests/scan/test_frame.py)
_TURNUP_MIN_DAYS = 40       # 低位转强面板列(turnup.PANEL_COLS)的 B 级底线:不足 → 整列 NaN + record_degradation,不阻断


def _harvest_vol_series(codes, analysis_date: str, lookback: int = _PANEL_LOOKBACK) -> pd.DataFrame:
    """拉近 ~lookback 交易日 daily(high/low/close/amount)→ vol_series 算多日量价因子 per code。

    供 L1 召回的 **volprice 组**(快照层本来无序列)。tushare bulk by date(~lookback 次)→ pivot。

    **2026-08-21 起 lookback 60**(低位转强波 §4.2):四个 20 日因子(cmf_20/obv_mom_20/
    price_vs_vwap_20/breakout_vol_20)窗口**仍是最后 20 列**、逐元素不变(等值锁在
    `tests/scan/test_frame.py::test_harvest_vol_series_lookback60_keeps_20d_factors_identical`);
    多出来的历史只喂 `turnup.PANEL_COLS`(dist_low/high_60、days_no_new_low、vol_ma*_prev、
    pct_5d/20d、above_ma20、ma5_gt_ma10)。已结算日全部湖命中零网络。**面板列是 B 级**:
    不足 `_TURNUP_MIN_DAYS` 个交易日 → 整列 NaN + `record_degradation`,**不抛**(A 级只有
    20 日 volprice 组那一道,见下)。

    **失败即抛 `DataContractError`,不再静默返回空帧**(2026-07-12 用户裁定 + 事故复盘)。
    原实现三层吞异常 → 任何失败都退化成空帧 → cmf_20/obv_mom_20 **整列不落盘** →
    `composite_score` 把 volprice 组从分母剔除、放大其余组权重 → 打分照样输出 0–100、漏斗照样
    跑完、退出码 0。实测代价:全市场打分失真 **98.8%**、L2 名单 jaccard **0.36**,而唯一的信号
    是一行淹没在日志里的 warn(根因是 lake 的 daily 被窄表毒化,见 `cache._lake_params`)。

    volprice 是 A 级地基(10 组因子之一,且是唯一的多日序列组)——**它死了这次扫描就不该发布**。
    """
    from datetime import datetime, timedelta

    import autoresearch.common.vol_series as vol_series
    from autoresearch.data.cache import get_or_fetch  # 已结算日湖命中零网络(policy: daily=eod)
    from autoresearch.data.contracts import DataContractError
    from autoresearch.data.tushare_source import (
        _code6,
        _pro,
        _trade_days,
        _ts_call,
        resolve_momentum_dates,
    )

    try:
        pro = _pro()
        last = resolve_momentum_dates(pro, analysis_date)[0]
        start = (datetime.strptime(last, "%Y%m%d") - timedelta(days=lookback * 2 + 15)).strftime("%Y%m%d")
        days = _trade_days(pro, start, last)[-lookback:]
        want = {str(c).zfill(6) for c in codes}
        recs = []
        for d in days:
            try:
                df = get_or_fetch("daily", {"trade_date": d}, today=analysis_date)
            except DataContractError:
                raise                       # 契约违约必须炸穿:回退直拉只会掩盖湖里的毒源
            except Exception:  # noqa: BLE001 — 湖/policy 其它异常(如未登记端点)→ 回退直拉
                df = _ts_call(lambda d=d: pro.daily(trade_date=d, fields="ts_code,high,low,close,amount"))
            if df is None or not len(df):
                continue
            df = df.assign(code=_code6(df["ts_code"]), date=d)
            recs.append(df[df["code"].isin(want)][["code", "date", "high", "low", "close", "amount"]])
    except DataContractError:
        raise
    except Exception as e:  # noqa: BLE001 — 其余任何失败都升级为契约违约(不再静默降级)
        raise DataContractError(
            f"[数据契约·A级] volprice 组(多日量价序列)取数失败:{e!r}\n"
            f"  为什么阻断:cmf_20/obv_mom_20 整列缺失 → composite_score 会把 volprice 组从分母\n"
            f"           剔除、放大其余组权重 → 打分照样输出 0–100,**残废得看不出来**。\n"
            f"  怎么办:若是湖里的 daily 损坏 → `python -m autoresearch.data.contracts doctor --purge`") from e

    if len(recs) < _VOL_MIN_DAYS:
        raise DataContractError(
            f"[数据契约·A级] volprice 组只取到 {len(recs)} 个交易日(需 ≥{_VOL_MIN_DAYS})"
            f" —— 20 日 CMF/OBV 算不出来,拒绝用残缺序列打分。\n"
            f"  多半是 lake 里这段日期的 daily 缺失/损坏 → "
            f"`python -m autoresearch.data.contracts doctor --purge` 后重跑。")

    long = pd.concat(recs, ignore_index=True)
    piv = {f: long.pivot_table(index="code", columns="date", values=f)
           for f in ("high", "low", "close", "amount")}
    win = sorted(piv["close"].columns)
    win20 = win[-20:]                                   # 20 日组窗口不变(byte-identical 契约)
    H, L, C, A = (piv[f][win20] for f in ("high", "low", "close", "amount"))
    out = pd.DataFrame({"code": list(C.index)})
    out["cmf_20"] = vol_series.cmf(H, L, C, A, win20).to_numpy()
    out["obv_mom_20"] = vol_series.obv_momentum(C, A, win20).to_numpy()
    out["price_vs_vwap_20"] = vol_series.price_vs_vwap(H, L, C, A, win20).to_numpy()
    out["breakout_vol_20"] = vol_series.breakout_on_volume(C, A, win20).to_numpy()
    from autoresearch.common import turnup
    if len(win) >= _TURNUP_MIN_DAYS:                    # 低位转强面板列(B 级增强,不进 A 级出帧契约)
        tp = turnup.panel_factors(piv, win)
        out = out.merge(tp, left_on="code", right_index=True, how="left")
    else:
        from autoresearch.data.contracts import record_degradation
        record_degradation("daily", f"低位转强面板仅 {len(win)} 个交易日(<{_TURNUP_MIN_DAYS}),"
                           f"{'/'.join(turnup.PANEL_COLS[:3])}… 整列缺省(B 级,不阻断)",
                           key="turnup_panel")
        for c in turnup.PANEL_COLS:
            out[c] = np.nan
    return out


def build_market_frame(analysis_date: str, *, cap_floor_yi: float | None = None,
                       include_bj: bool | None = None,
                       source: str | None = None, l0_min_amount_yi: float | None = None,
                       l0_min_list_days: int | None = None, vol_series: bool = True,
                       ) -> tuple[pd.DataFrame, dict]:
    """L0 取数 + L1 轻门 + 多日量价富化 → (全市场因子帧, 计数)。零打分零召回零 LLM。

    与 `universe.run` 前半段逐值一致(run 调本函数;golden parity 由 tests/scan/test_parity.py 锁)。
    counts:`universe_raw`(源头全量)/ `universe`(L0 硬门后)/ `after_gate_a`(轻门后=帧行数)。
    `vol_series=False` 跳过多日量价拉取(盘前只要 regime/哨兵、healthy 谓词缺 cmf 会降级时可省时)。

    **L0 旋钮解析(2026-08-11 配置单一事实源波)**:形参 `None` → 从 scan_config `l0` 块补
    (`knob()`,显式恒优先)→ 缺文件/缺键 = 内建值(30 亿 / 纳北交所 / tushare / 门关,parity)。
    本函数是 L0 的**单一代码路径**(universe.run 与 frame CLI 共用),旋钮收在这里,
    market_pack 的 regime/宽度统计与漏斗的市值地板才不会各吃各的。
    """
    from autoresearch.scan.user_config import knob, load_user_config as _luc
    try:
        _ucfg = _luc() or {}
    except Exception as e:  # noqa: BLE001 — 配置层故障不挡确定性扫描,但必须留痕
        print(f"[warn] scan_config 读取失败({e!r})→ L0 旋钮用内建默认", file=sys.stderr)
        _ucfg = {}
    cap_floor_yi = float(knob("l0", "cap_floor_yi", cap_floor_yi, 30.0, cfg=_ucfg))
    include_bj = bool(knob("l0", "include_bj", include_bj, True, cfg=_ucfg))
    source = str(knob("l0", "source", source, "tushare", cfg=_ucfg))
    l0_min_amount_yi = float(knob("l0", "min_amount_yi", l0_min_amount_yi, 0.0, cfg=_ucfg))
    l0_min_list_days = int(knob("l0", "min_list_days", l0_min_list_days, 0, cfg=_ucfg))
    if source == "tushare":
        from autoresearch.data.tushare_source import (  # 默认源(东财 push2 常被封)
            _RAW_COUNT,
            fetch_universe_tushare,
        )
        uni = fetch_universe_tushare(analysis_date, cap_floor_yi=cap_floor_yi, include_bj=include_bj)
        n_raw = _RAW_COUNT.get("n", len(uni))
    else:
        from autoresearch.data.akshare_universe import _GATE_INFO, fetch_universe
        uni = fetch_universe(analysis_date, cap_floor_yi=cap_floor_yi, include_bj=include_bj)
        n_raw = _GATE_INFO.get("n_raw", len(uni))   # em 路径同模块,可靠
    n_l0 = len(uni)
    uni = uni[_recall_gate_a(uni, min_amount_yi=l0_min_amount_yi,
                             min_list_days=l0_min_list_days)].reset_index(drop=True)
    uni["code"] = uni["code"].astype(str).str.zfill(6)
    if vol_series:
        vps = _harvest_vol_series(uni["code"], analysis_date)      # 多日量价序列(CMF/OBV/...)→ volprice 组
        uni = uni.merge(vps, on="code", how="left")                # 失败已在上面抛 DataContractError
    # 「一 code 一行」是这张帧的出口契约之一,却从没人守过它:上面九次 `merge(..., on="code")`
    # 里任何一个右表有重复码就会静默扇出一行。2026-08-26 实跑代价 —— 601665 齐鲁银行在帧里
    # 两行 → L1 召回两行 → L2 两行 → `l4/prompts.py` 的 `set_index("code").to_dict("index")`
    # 抛 ValueError,**在 L3 已经烧完 1.16M token 之后**把整条派发炸掉。
    # 修在这里而不是修某一个 merge:帧是 L0 的单一代码路径,谁扇出的都在这道门内收口。
    # 同源重复行取第一条(确定性);**去重必须留痕** —— 静默去重会把上游的真问题一起抹掉。
    dup_mask = uni["code"].duplicated(keep="first")
    if bool(dup_mask.any()):
        dup_codes = sorted(uni.loc[dup_mask, "code"].astype(str).unique())
        from autoresearch.data.contracts import record_degradation
        record_degradation(
            "market_frame",
            f"帧内重复 code {len(dup_codes)} 个({','.join(dup_codes[:8])}"
            f"{'…' if len(dup_codes) > 8 else ''})→ 各保留第一行。"
            f"根因在 fetch_universe 的某个 left-merge 右表有重复码(B 级,不阻断)",
            key=f"dup_code_{analysis_date}")
        print(f"[frame] ⚠️ 帧内重复 code {len(dup_codes)} 个 → 去重保留第一行:"
              f"{','.join(dup_codes[:8])}", file=sys.stderr)
        uni = uni[~dup_mask].reset_index(drop=True)
    # 出口契约(漏斗地基的最后一道门):喂给 composite_score 的帧到底全不全,与它从哪来无关。
    check_market_frame(uni, with_vol_series=vol_series)
    return uni, {"universe_raw": int(n_raw), "universe": n_l0, "after_gate_a": len(uni)}


# ───────────────────────── CLI:盘前哨兵预告(零 LLM) ─────────────────────────


def _atomic_write_json(path: Path | str, payload: dict) -> Path:
    """JSON 原子落盘:先写 `.tmp` 再 `os.replace`(同目录换名,POSIX 原子)。

    产物文件从此由 writer 持有,不再由 shell 重定向 + 进程 stdout 决定内容 ——
    2026-07-28 事故里执行壳多加一个 `2>&1` 就把日志灌进了 market_pack.json,
    而 `>` 在进程半途崩时留下的半截文件同样非空、同样骗过 `test -s` 门。

    失败语义:序列化抛异常 → 目标文件保持原样(或仍不存在),临时文件清掉,
    绝不产生"非空但无效"的中间态。
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    try:
        tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(tmp, path)          # 原子:要么旧内容,要么新内容,没有中间态
    except BaseException:
        tmp.unlink(missing_ok=True)    # 失败路径不留残渣
        raise
    return path


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="盘前市场帧:regime + 哨兵预告(确定性,零 LLM,不依赖 scan staging)")
    ap.add_argument("date", nargs="?", help="分析日 YYYY-MM-DD(缺省=今天)")
    ap.add_argument("--cap-floor", type=float, default=None,
                    help="市值地板(亿);缺省=scan_config l0.cap_floor_yi→30(与 universe 同)")
    ap.add_argument("--exclude-bj", action="store_true", help="排除北交所(缺省=scan_config l0.include_bj→纳入)")
    ap.add_argument("--source", choices=["em", "tushare"], default=None,
                    help="缺省=scan_config l0.source→tushare")
    ap.add_argument("--json", action="store_true", help="另打印 market_pack JSON(宏观 lite / Stage 0 输入)")
    ap.add_argument("--json-out", metavar="PATH",
                    help="market_pack JSON 原子落盘到 PATH(先写 .tmp 再 os.replace)。"
                         "**推荐替代 `--json > file`** —— 产物不再经 shell 重定向,"
                         "壳误加 2>&1 或取数半途崩都污染不了它(2026-07-28 事故)")
    args = ap.parse_args(argv)
    analysis_date = args.date or date.today().isoformat()
    want_pack = bool(args.json or args.json_out)

    # A forensic run already owns its identity before any business read.  Validate
    # and reuse that exact v3 contract here; the frame must never mint a second ID.
    contract = None
    if ws.active_run_id() is not None:
        from autoresearch.scan.run_bootstrap import resolve_active_scan_contract

        contract = resolve_active_scan_contract(
            analysis_date,
            cap_floor_yi=args.cap_floor,
            include_bj=False if args.exclude_bj else None,
            source=args.source,
        )
    elif want_pack:
        from autoresearch.scan.run_bootstrap import prepare_scan_run

        contract = prepare_scan_run(
            analysis_date,
            cap_floor_yi=args.cap_floor,
            include_bj=False if args.exclude_bj else None,
            source=args.source,
        )

    import contextlib

    # 产 pack 时把整个构建段的 stdout 圈进 stderr:湖冷时取数层(tushare_source 等)会 print 进度行,
    # 只改本函数三行 info 挡不住(2026-07-09 market_pack 污染的完整根因)。JSON 是 stdout 唯一产出。
    # (--json-out 下产物已不走 stdout,这层仍留着:让 --json/--json-out 的 stdout 语义保持一致。)
    with contextlib.redirect_stdout(sys.stderr) if want_pack else contextlib.nullcontext():
        # 运行旋钮在 CLI 层就解析成**具体值**(2026-08-11):run_contract.data_policy 是可复现
        # 凭据,必须记实际生效值,不能记 None(「配置生效对账」对的就是这份)。
        if contract is not None:
            cap_floor = float(contract.data_policy["cap_floor_yi"])
            include_bj = bool(contract.data_policy["include_bj"])
            source = str(contract.data_policy["source"])
        else:
            from autoresearch.scan.user_config import knob

            cap_floor = float(knob("l0", "cap_floor_yi", args.cap_floor, 30.0))
            include_bj = bool(knob("l0", "include_bj",
                                   (False if args.exclude_bj else None), True))
            source = str(knob("l0", "source", args.source, "tushare"))
        frame, counts = build_market_frame(analysis_date, cap_floor_yi=cap_floor,
                                           include_bj=include_bj, source=source)
        # Wave5 ③A:资金面/指数估值取数落 `_macro_cn.json` —— 必须在 market_pack 之前跑,
        # pack 读的就是这份文件(prewarm 跑过则本次多半是湖/缓存命中)。失败只降级不阻断。
        try:
            from autoresearch.data.macro_cn import write_macro_cn
            mp = write_macro_cn(analysis_date)
            print(f"[macro_cn] {mp}", file=sys.stderr)
        except Exception as e:  # noqa: BLE001 — 取数全挂也只让 pack 少两块,不毁整帧
            print(f"[warn] macro_cn 取数失败(pack 缺 cross_money/index_val): {e}", file=sys.stderr)
        from autoresearch.scan.market import market_pack_from_frame
        from autoresearch.scan.menu import sentinel_advice_from_frame
        pack = market_pack_from_frame(frame, date=analysis_date)
        level, reason = sentinel_advice_from_frame(frame)
        reg = pack.get("regime") or {}
        print(f"[frame] {analysis_date} 帧 {counts['after_gate_a']} 只(L0 {counts['universe']})｜"
              f"regime={reg.get('label', '—')} breadth={reg.get('breadth', '—')} "
              f"med_mom={reg.get('med_mom', '—')}", file=sys.stderr)
        print(f"[sentinel·盘前预告] {level} —— {reason}(正式判据以 scan 内 L1_scored_full 口径为准)",
              file=sys.stderr)
        from autoresearch.macro.state import load_macro_state  # Phase 2:宏观 lite 的输入捆绑
        mstate, mnote = load_macro_state(analysis_date, regime_today=reg.get("label"))
        print(f"[macro_state] {mnote}", file=sys.stderr)
    if want_pack:
        from autoresearch.scan.run_contract import write_run_contract
        from autoresearch.scan.user_config import materialize_agent_config

        if contract is None:  # pragma: no cover - want_pack always prepares above
            raise RuntimeError("missing prepared RunContract")
        user_cfg = contract.user_config
        echo_dir = ws.scan_dir(analysis_date)
        echo_dir.mkdir(parents=True, exist_ok=True)
        if ws.active_run_id() is None:
            write_run_contract(echo_dir / "run_contract.json", contract)
        (echo_dir / "user_config_echo.json").write_text(
            json.dumps(user_cfg, ensure_ascii=False, indent=2), encoding="utf-8")
        if user_cfg.get("resolved_agents"):
            # 落盘的与 echo/pack 里那份是**同一个对象**(不重算)——`usage_reconcile` 读文件、
            # workflow 读 echo,两边必须是同一张表,否则对账本身就是假的(修复轮 1)。
            mp_resolved = materialize_agent_config(
                analysis_date, user_cfg, root=Path("."),
                resolved=user_cfg["resolved_agents"])
            print(f"[frame] resolved agent config → {mp_resolved}", file=sys.stderr)
        from autoresearch.scan.stage_result import safe_record_stage_result
        safe_record_stage_result(
            echo_dir,
            stage="frame",
            status="SUCCEEDED",
            artifacts=["run_contract"],
            metrics={
                "frame_rows": int(counts["after_gate_a"]),
                "l0_rows": int(counts["universe"]),
                "regime": reg.get("label"),
                "sentinel_level": level,
            },
            warnings=[],
            error=None,
        )
        # D-2:隔夜 tape 进 **full market_pack**(L5 展示与人读),**不进 strategist 投影** ——
        # 策略师读它属 B-1(改判断层输入),受 09-中冻结。`strategist_pack.ALLOWED_KEYS` 是
        # 默认拒绝的白名单,所以这里加键**不会**泄漏过去;`tests/scan/test_frame*.py` 与
        # macro 侧各有一条断言钉死这件事(防有人"顺手"把它加进 allowlist)。
        tape = None
        try:
            from autoresearch.data.sources.yf_tape import global_tape_pack
            tape = global_tape_pack(analysis_date)
            if not tape.get("usable"):     # 源在场但一条都没取到 → 不塞半截块进 pack
                tape = None
        except Exception as exc:  # noqa: BLE001 — B 级:海外 tape 取不到不挡帧
            print(f"[frame] global_tape 跳过:{type(exc).__name__}", file=sys.stderr)
        payload = {
            **pack,
            "macro_state": mstate,
            "macro_state_note": mnote,
            "user_config": user_cfg,
            "run_contract": contract.short_ref(),
        }
        if tape:
            payload["global_tape"] = tape
        if args.json_out:
            out = _atomic_write_json(args.json_out, payload)
            print(f"[frame] market_pack → {out}(原子落盘)", file=sys.stderr)
            # A4:同时投影出策略师那份。full pack 仍是 L5/L3 validator 的事实源,
            # 策略师只拿投影 —— 防锚定从"叮嘱它忽略 sector_healthy_top3"变成"它看不见"。
            try:
                from autoresearch.scan.strategist_pack import write as write_strategist
                sp = write_strategist(payload, Path(out).with_name("strategist_pack.json"))
                print(f"[frame] strategist_pack → {sp}(单向投影)", file=sys.stderr)
            except Exception as e:  # noqa: BLE001 — 投影失败只让策略师少一份输入,不毁整帧
                print(f"[warn] strategist_pack 投影失败(策略师将无输入): {e}",
                      file=sys.stderr)
        if args.json:
            print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
