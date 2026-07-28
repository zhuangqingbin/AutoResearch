"""L4 descriptive per-stock context and base-rate rendering."""
from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

_BASE_RATE_THIN_N = 10


def _market_ctx(scan_dir, industry) -> str:
    """本股所在市场地形块(有 L1_scored_full 才注入;失败静默降级空串)。lazy import 避免 cycle。"""
    try:
        from autoresearch.scan.market import market_context_block, market_pack
        pack = market_pack(scan_dir)
        if not pack.get("regime"):
            return ""
        return market_context_block(pack, industry=industry)
    except Exception:   # noqa: BLE001 —— 市场层可选,缺了不挡简报
        return ""

def _dist_mark(l1: dict) -> str:
    """主力占比失真标注(谓词=`scoring.main_net_distortion_label` 单一事实源;缺值 → "")。

    07-03 病灶:占比失真票 L4 每卡都要自己重新发现"绝对净出/微盘放大"——确定性标进 P0 简报,
    subagent 直接从矛盾核查起步,不再重复还债。
    """
    try:
        from autoresearch.common.scoring import main_net_distortion_label
        lbl = main_net_distortion_label(l1.get("main_net_ratio"), l1.get("main_inflow_yi"))
    except Exception:  # noqa: BLE001 — 标注可选,缺了不挡简报
        return ""
    return f"·⚠主力占比失真({lbl}:勿单独作多头论点,须绝对净额+cmf/obv 同向确认)" if lbl else ""

def _pledge_mark(base: Path, code6: str) -> str:
    """质押旗行(presence-gated:`pledge.csv` 在且比例过阈才注;阈值 =
    `scoring.pledge_flag_label` 单一事实源,与深核 slim 质押段同源)。低比例/缺档 → ""
    (不加噪)。spec 2026-07-05 §5.2。"""
    p = base / "pledge.csv"
    if not p.exists():
        return ""
    try:
        from autoresearch.common.scoring import pledge_flag_label
        df = pd.read_csv(p, dtype={"code": str})
        df["code"] = df["code"].astype(str).str.zfill(6)
        sub = df[df["code"] == code6]
        if not len(sub):
            return ""
        r = float(pd.to_numeric(pd.Series([sub.iloc[0]["pledge_ratio"]]), errors="coerce").iloc[0])
        lbl = pledge_flag_label(r)
    except Exception:  # noqa: BLE001 — 旗可选,缺了不挡简报
        return ""
    if not lbl:
        return ""
    return (f"- **质押旗(先验)**:质押比例 {r:.1f}%,⚠高质押({lbl})——P4 必核平仓线"
            f"与补充质押公告(截至 {sub.iloc[0].get('end_date', '—')})")

def _seat_mark(base: Path, code6: str) -> str:
    """龙虎榜席位行(presence-gated:seats.csv 在且该票近窗口上榜才注)。
    机构净买>0 标 Phase A 反指(机构上榜买后续偏弱);游资净买作接力信号。缺档/未上榜 → ""。"""
    p = Path(base) / "seats.csv"
    if not p.exists():
        return ""
    try:
        df = pd.read_csv(p, dtype={"code": str})
        df["code"] = df["code"].astype(str).str.zfill(6)
        sub = df[df["code"] == code6]
        if not len(sub):
            return ""
        r = sub.iloc[0]
        n = float(r.get("n_appear") or 0)
        inst = float(r.get("inst_net_wan") or 0)
        retail = float(r.get("retail_net_wan") or 0)
        if n <= 0 or any(pd.isna(x) for x in (n, inst, retail)):
            return ""
    except Exception:  # noqa: BLE001 — 行可选,缺了不挡简报
        return ""
    contra = "（⚠️Phase A:机构上榜净买后续 T+1~10 偏弱=反指,勿当强利好）" if inst > 0 else ""
    return (f"·龙虎榜近窗口上榜 {int(n)} 次:机构净买 {inst:+.0f}万{contra}、"
            f"游资/营业部净买 {retail:+.0f}万")

def _cat_mark(base: Path, code6: str) -> str:
    """催化事件行(presence-gated:`L3_catalyst.csv` 在且有非零计数才注)。
    spec 2026-07-05 §B2。"""
    p = base / "L3_catalyst.csv"
    if not p.exists():
        return ""
    try:
        from autoresearch.scan.agents.l3_catalyst import cat_label
        df = pd.read_csv(p, dtype={"code": str})
        df["code"] = df["code"].astype(str).str.zfill(6)
        sub = df[df["code"] == code6]
        lbl = cat_label(sub.iloc[0].to_dict()) if len(sub) else ""
    except Exception:  # noqa: BLE001 — 行可选,缺了不挡简报
        return ""
    if not lbl:
        return ""
    return (f"- **📣催化事件(近10日,事实)**:{lbl}(存在性≠方向确认;"
            f"与资金/基本面共振才可作论点支柱)")

def _misread_mark(l2: dict) -> str:
    """误读三预警徽标(谓词=`scoring.l3_misread_flags` 单一事实源;presence-gated:
    复用 compose 已读的 L2 行,空行/缺键/NaN → "";never raises,缺了不挡简报)。"""
    try:
        from autoresearch.common.scoring import l3_misread_flags
        m = l3_misread_flags(l2)
    except Exception:  # noqa: BLE001 — 行可选,缺了不挡简报
        return ""
    return f"⚠️误读预警: {m} —— 该论点若为 L3 选票理由,P1-P3 优先证伪" if m else ""

def _inst_mark(base: Path, code6: str) -> str:
    """机构面行(presence-gated:consensus.csv 在且该票有行才注)。advisory 事实,非方向。"""
    p = Path(base) / "consensus.csv"
    if not p.exists():
        return ""
    try:
        df = pd.read_csv(p, dtype={"code": str})
        df["code"] = df["code"].astype(str).str.zfill(6)
        sub = df[df["code"] == code6]
        if not len(sub):
            return ""
        r = sub.iloc[0]
        d = float(pd.to_numeric(pd.Series([r.get("eps_delta_pct")]), errors="coerce").iloc[0])
        n = int(r.get("n_reports") or 0)
    except Exception:  # noqa: BLE001 — 行可选,缺了不挡简报
        return ""
    if pd.isna(d) or n <= 0:
        return ""
    arrow = "上修" if d > 0 else ("下修" if d < 0 else "持平")
    return (f"- **机构面(卖方,近窗)**:研报 {n} 篇;FY 一致 EPS 修正:{arrow} {d:+.1f}%"
            f"(窗口对比,advisory 存在性≠方向;与资金/基本面共振才可作论点支柱)")

def _fund_mark(base: Path, code6: str) -> str:
    """机构面第二行(条件分支):基金重仓(presence-gated:`fund_hold.csv` 在且该票有行才注)。

    Plan 1 Task 6 探针裁决"可用"(fund_portfolio 按 period 批量+本地按 symbol 反查)后接线;
    **季度滞后**(定期报告披露制,非实时,与卖方修正的"近窗"时效不同)——advisory,不进分
    不设门,与 `_inst_mark` 相互独立(各自 presence-gate,互不影响对方能否输出)。
    """
    p = Path(base) / "fund_hold.csv"
    if not p.exists():
        return ""
    try:
        df = pd.read_csv(p, dtype={"code": str})
        df["code"] = df["code"].astype(str).str.zfill(6)
        sub = df[df["code"] == code6]
        if not len(sub):
            return ""
        r = sub.iloc[0]
        n = int(pd.to_numeric(pd.Series([r.get("n_funds")]), errors="coerce").iloc[0])
        mkv = float(pd.to_numeric(pd.Series([r.get("mkv_yi")]), errors="coerce").iloc[0])
        delta = pd.to_numeric(pd.Series([r.get("n_funds_delta")]), errors="coerce").iloc[0]
    except Exception:  # noqa: BLE001 — 行可选,缺了不挡简报
        return ""
    if n <= 0:
        return ""
    d_txt = f"{delta:+.0f}家" if pd.notna(delta) else "—"
    mkv_txt = f"{mkv:.1f} 亿" if pd.notna(mkv) else "—"
    return (f"- **机构面(基金重仓,季度滞后)**:{n} 只基金持有,市值 {mkv_txt}"
            f"(环比 {d_txt};定期报告口径,滞后于当前,advisory 存在性≠方向)")

def _precedent_mark(base: Path, code6: str, sector, gate_hint: str | None = None) -> str:
    """跨票同型判例块(presence-gated:`precedents.db` 不存在 → "";异常降级空串,风格同
    `_inst_mark`/`_seat_mark` 家族)。plan: 2026-07-11-hermes-selfimprove-plan.md Plan B Task 4;
    design: 2026-07-11-recall-gate-pinned-config-design.md §5.2。

    db 路径按 `base`(scan_dir,生产态 = `context/scan/<date>`)反推兄弟目录
    `base.parent.parent/knowledge/precedents.db`——镜像 `learning.precedents` 模块自身
    `context/{scan,knowledge}` 兄弟约定(同函数内 `render_dossier(scan_root=base.parent, ...)`
    也是同一手法);零硬编码路径,tmp_path 天然隔离,不需 monkeypatch。

    查 `learning.precedents.query`(近90日,按 sector + 可选 gate_hint AND 过滤)找跨票同型
    历史判例,渲染「📚 判例(跨票同型,advisory)」块,每条一行(日期/代码/名称/结局摘要/fwd_2)。
    用 `code6` 剔除同票命中——同票历史已由 `dossier`(R5 前科卡)覆盖,本块只负责"其它票"的
    跨票同型旁证,两者并存不重复(design §5.2「与个股档案分工」)。advisory:不进分不设门;
    token 预算 ≤400/卡(k≤3 + 单行短摘要天然封顶)。

    gate_hint:P0 简报组装时尚无门型判定(OW三门是 subagent 读完深核才判的),多数调用点
    传 None——拿不到就只按 sector 查,不强凑一个不可靠的门型猜测。
    """
    db_path = Path(base).parent.parent / "knowledge" / "precedents.db"
    if not db_path.exists():
        return ""
    try:
        from autoresearch.learning.precedents import query
        sector_s = None if sector is None or pd.isna(sector) else (str(sector).strip() or None)
        # k 留缓冲:剔除同票命中后仍够凑 top-3(跨票池通常够,凑不满也不报错,只是更短)。
        rows = query(sector=sector_s, gate=gate_hint, k=8, days=90, db_path=db_path)
        rows = [r for r in rows if str(r.get("code") or "").zfill(6) != code6][:3]
    except Exception:  # noqa: BLE001 — 判例可选,缺了不挡简报
        return ""
    if not rows:
        return ""
    out = ["- **📚 判例(跨票同型,advisory)**:近90日同型 top-3(仅供旁证,不进分不设门)"]
    for r in rows:
        fwd = r.get("fwd_2")
        fwd_txt = f"{fwd * 100:+.2f}%" if fwd is not None else "—"
        out.append(f"  - {r.get('date') or '—'} {r.get('code') or '—'} {r.get('name') or '—'}"
                   f" | {r.get('verdict_line') or '—'} | fwd_2 {fwd_txt}")
    return "\n".join(out)

def _dossier_summary_text(code6: str) -> str:
    """给 intel 内嵌用的档案摘要纯文本;不可注入 → ""(与卡注入同一事实源)。"""
    try:
        from autoresearch.dossier import schema as dschema
        return dschema.injectable_summary(code6).strip()
    except Exception:  # noqa: BLE001 — 档案层可选
        return ""

def _dossier_summary_mark(code6: str) -> str:
    """Wave3 ④:覆盖档案摘要注入(presence-gated)。

    可注入判定单一事实源 = `dossier.schema.injectable_summary`(缺档案/未首覆/摘要
    缺/超帽 → ""),与卡契约 lint 的 `has_cov` 同门——防「没注入却照查」的缝
    (review R1 important)。进逐卡 body,天然在共享前缀之后(cache 契约安全)。
    """
    try:
        from autoresearch.dossier import schema as dschema
        block = dschema.injectable_summary(code6)
        if not block:
            return ""
        p = dschema.dossier_path(code6)
        meta = dschema.parse_frontmatter(p.read_text(encoding="utf-8"))
        asof = meta.get("last_delta") or meta.get("initiated")
        head = (f"### 📚 覆盖档案摘要(常备模型 as-of {asof};**增量研究**:"
                "已覆盖项只核对不重写,深度花在变化上)")
        tail = (f"_档案全文按需 Read:`{p}`(§4 筹码/§6 催化随每日 δ 刷新,"
                "其余节为首覆/中报季全量);本卡必须含「**档案对账**」节:"
                "驱动变量哪个动了/风险矩阵哪条触发或解除/判例账本一行。_")
        return "\n".join([head, block.strip(), tail])
    except Exception:  # noqa: BLE001 — 档案层可选,坏档不挡派发
        return ""

def _base_rate_mark(base: Path, lane) -> str:
    """🔁 基率行(presence-gated:`_l4_base_rates.json`〔`write_base_rates` 产〕缺 → ""）。

    逐卡块内,拼 ≤3 项频率锚(brainstorm §5.2):该票 lane 的 L3→L4 高确信翻案率
    (`by_lane`,cross_calib.flip_stats)+ OW 评级历史 T+2 胜率/均值(`by_rating["Overweight"]`,
    buy_ledger 全库买单账;"过三门票" 的代理——rubric_rating 定义 OW 即三门皆过)。三项各自
    独立 presence-gate(有则加,没有就跳),互不挡对方;n<3 已在 `write_base_rates` 写盘时
    过滤掉(绝对禁注 floor,design 2026-07-12-selflearning-optimization-brainstorm.md §4
    P0-3),这里只管"有没有条目"。数值本身是收缩估计,`n_tag` 按既有 ⚠ 阈值(=10)标薄样本
    (双轨语义:门槛用硬 n,这里注入锚用收缩值+⚠提示,不再二值断供)。全部缺 → 整行不注。
    """
    p = Path(base) / "_l4_base_rates.json"
    if not p.exists():
        return ""
    try:
        import json
        data = json.loads(p.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001 — 基率可选,缺了不挡简报
        return ""
    from autoresearch.learning.shrink import n_tag
    parts: list[str] = []
    lane_s = lane.strip() if isinstance(lane, str) and lane.strip() else None
    if lane_s:
        bl = (data.get("by_lane") or {}).get(lane_s)
        if bl and bl.get("flip_rate") is not None:
            parts.append(f"{lane_s} lane 高确信历史被 L4 翻案 "
                        f"{bl['flip_rate']:.0%}{n_tag(bl.get('n'), _BASE_RATE_THIN_N)}")
    ow = (data.get("by_rating") or {}).get("Overweight")
    if ow:
        if ow.get("win") is not None:
            parts.append(f"OW 卡历史 T+2 胜率 {ow['win']:.0%}{n_tag(ow.get('n'), _BASE_RATE_THIN_N)}")
        if ow.get("mean_fwd2") is not None:
            parts.append(f"OW 历史 T+2 均值 {ow['mean_fwd2']:+.1%}")
    if not parts:
        return ""
    return "🔁 基率:" + "｜".join(parts[:3])

def write_base_rates(scan_dir: Path | str, min_n: int = 10) -> Path | None:
    """L4 逐卡 🔁 基率锚落稿(presence-gated 消费方:`_base_rate_mark` 读此文件注入简报)。

    从 `cross_calib.flip_stats`(近30 scan日,per lane 高确信翻案率**收缩估计**,列 lane/
    n_hiconv/flip_rate/thin)+ `buy_ledger.roll` → `rating_base_rates`(全库 ≥OW 买单 T+2
    胜率/均值,per rating,本函数对 `win2` 再收缩一次——`rating_base_rates` 本身仍回原始值,
    供 `buy_ledger.md` 审计表按既有口径展示)聚 `_l4_base_rates.json`:
    `{"by_lane": {lane: {"n", "flip_rate"}}, "by_rating": {rating: {"n", "mean_fwd2", "win"}}}`。

    收缩公式 p̂=(n·p_桶+k·p_全局)/(n+k)(design 2026-07-12-selflearning-optimization-
    brainstorm.md §4 P0-3,C9-C12);n<3(`shrink.MIN_N_INJECT`)绝对禁注——`flip_stats` 已把
    这条 floor 烤进它自己的 `flip_rate` 列(本函数只需再检查是否 NaN);`by_rating` 侧的
    floor 在本函数内独立判(`n_realized<3` 剔除)。`min_n`(默认10)不再是排除门槛,只是
    `rating_base_rates` 自己的 `thin` 标记阈值(供 `_base_rate_mark` 的 `n_tag` 沿用同一惯例)。
    任一数据源缺失/异常 → 该侧降级空字典,不挡另一侧、不挡落稿(mirror 本文件其余 `_xxx_mark`
    presence-gated 风格)。

    `scan_dir` = 当日 scan 目录(如 `context/scan/<date>`,与 `_l4_prompt_*.md` 同级);
    跨日统计的 `scan_root` 由 `scan_dir.parent` 反推(mirror `_target_calib_mark` 的
    `Path(base).parent.parent` 兄弟目录约定,这里只需上一级,因为 `flip_stats`/`roll` 的
    `scan_root` 本身就是"逐日子目录的容器",不是再上一层的 `context/`)。两侧都空手(无
    现场)→ 不写垃圾空骨架,返回 None;presence-gated 消费方按文件是否存在处理,行为一致。
    """
    from autoresearch.learning.shrink import MIN_N_INJECT, shrink as _shrink_fn, shrink_config

    scan_dir = Path(scan_dir)
    scan_root = scan_dir.parent
    shrink_on, k = shrink_config()

    by_lane: dict = {}
    try:
        from autoresearch.learning import cross_calib
        flips = cross_calib.flip_stats(scan_root=scan_root, window=30)
        for r in flips.itertuples(index=False):
            if pd.isna(r.flip_rate):
                continue
            by_lane[str(r.lane)] = {"n": int(r.n_hiconv), "flip_rate": float(r.flip_rate)}
    except Exception:  # noqa: BLE001 — L3 校准可选,缺了不挡落稿
        pass

    by_rating: dict = {}
    try:
        from autoresearch.learning import buy_ledger
        ledger = buy_ledger.roll(scan_root=scan_root)
        f2_all = (pd.to_numeric(ledger["fwd_2"], errors="coerce").dropna()
                 if "fwd_2" in ledger.columns else pd.Series(dtype=float))
        p_global_win = float((f2_all > 0).mean()) if len(f2_all) else None
        for b in buy_ledger.rating_base_rates(ledger, min_n=min_n):
            n = b["n_realized"]
            if n < MIN_N_INJECT or b["mean2"] is None or b["win2"] is None:
                continue
            if shrink_on:
                shrunk = _shrink_fn(b["win2"], n, p_global_win, k)
                win = round(float(shrunk), 4) if shrunk is not None else b["win2"]
            else:
                win = b["win2"]
            by_rating[b["rating"]] = {"n": n, "mean_fwd2": b["mean2"], "win": win}
    except Exception:  # noqa: BLE001 — 评级基率可选,缺了不挡落稿
        pass

    if not by_lane and not by_rating:
        return None
    scan_dir.mkdir(parents=True, exist_ok=True)
    out = scan_dir / "_l4_base_rates.json"
    import json
    out.write_text(json.dumps({"by_lane": by_lane, "by_rating": by_rating},
                              ensure_ascii=False, indent=2), encoding="utf-8")
    return out

def compose_funnel_brief(code: str, scan_dir: Path | str) -> str:
    """L4 **P0 定向**:从漏斗产物(L1_recall/L2/finalists)拼该票紧凑简报 markdown。

    **只定向 + 给评分卡先验,不作早停依据**(信息薄,据此判=误杀)。subagent 据此知道
    「该重点核哪条」,判定来自 P1–P5 读到的 slim 真数据。缺产物/列降级占位(`—`),不抛。
    """
    base = Path(scan_dir)
    code6 = str(code).split(".")[0].zfill(6)

    def _row(fname: str) -> dict:
        p = base / fname
        if not p.exists():
            return {}
        df = pd.read_csv(p, dtype={"code": str})
        if "code" not in df.columns:
            return {}
        df["code"] = df["code"].astype(str).str.zfill(6)
        sub = df[df["code"] == code6]
        return sub.iloc[0].to_dict() if len(sub) else {}

    l1, l2, l3 = _row("L1_recall_top1000.csv"), _row("L2_gbdt_top200.csv"), _row("finalists.csv")

    def _g(d: dict, k: str, dflt: str = "—"):
        v = d.get(k, dflt)
        return dflt if v is None or (isinstance(v, float) and v != v) else v

    name = _g(l3, "name") if l3 else _g(l1, "name")
    ind = l3.get("industry") or l3.get("sector") or l1.get("industry")
    mech = l3.get("mechanism")
    mech_ok = isinstance(mech, str) and mech.strip()
    lines = [
        f"## 漏斗简报 — {code6} {name}(L1/L2/L3 评价·定向用,**判定须读下方真数据**)",
        "",
        f"- **L1 召回**:命中 {_g(l1,'n_channels')} 路({_g(l1,'recall_channels')})｜"
        f"best_rank {_g(l1,'best_rank')}｜composite {_g(l1,'composite')}",
        f"- **L1 子分**:动量{_g(l1,'score_momentum')}·主力{_g(l1,'score_fund_main')}·"
        f"成长{_g(l1,'score_growth')}·价值{_g(l1,'score_value')}·量价{_g(l1,'score_volprice')}·"
        f"筹码{_g(l1,'score_chip')}·北向{_g(l1,'score_north')}·技术{_g(l1,'score_tech')}",
        f"- **基本面(先验)**:np_yoy {_g(l1,'np_yoy')}·rev_yoy {_g(l1,'rev_yoy')}·roe {_g(l1,'roe')}",
        f"- **估值(先验)**:pe {_g(l1,'pe')}·pb {_g(l1,'pb')}·股息 {_g(l1,'dv_ratio')}",
        f"- **资金/技术(先验)**:主力净占比 {_g(l1,'main_net_ratio')}·主力绝对 {_g(l1,'main_inflow_yi')}亿"
        f"{_dist_mark(l1)}·cmf20 {_g(l1,'cmf_20')}·"
        f"obv20 {_g(l1,'obv_mom_20')}·rsi6 {_g(l1,'rsi6')}·多头排列 {_g(l1,'ma_bull')}·pct60d {_g(l1,'pct_60d')}",
        f"- **筹码(先验)**:winner {_g(l1,'winner_rate')}·集中度 {_g(l1,'chip_concentration')}·"
        f"现价/成本 {_g(l1,'price_to_cost')}·北向占比 {_g(l1,'hk_ratio')}" + _seat_mark(base, code6),
        f"- **L2**:gbdt_score {_g(l2,'gbdt_score')}(rank {_g(l2,'l2_rank')})",
        "- **L3 前提清单(中性措辞,逐条核真)**:",         # 防锚定(B2):中性前提替代方向性"多头论点"
        f"  - 前提1:{_g(l3,'thesis')}",
        *([f"  - 前提2(兑现机制):{mech.strip()}"] if mech_ok else []),   # 缺 mechanism 整行省略,不占位符
        f"- **L3 元数据(读完 P1 数字后再看)**:conviction {_g(l3,'conviction')}·lane {_g(l3,'lane')}·情感 {_g(l3,'sentiment')}",
        f"  - 最大风险:{_g(l3,'risk')}",
        f"  - 催化:{_g(l3,'catalyst')}",
    ]
    br = _base_rate_mark(base, l3.get("lane"))
    if br:
        lines.append(br)
    try:                                     # 日历旗:解禁风险窗/预约披露日(事实日期非方向)
        from autoresearch.scan.calendar import calendar_flags
        lines += calendar_flags(base, code6)
    except Exception:  # noqa: BLE001 — 日历可选,缺了不挡简报
        pass
    pm = _pledge_mark(base, code6)           # 质押旗:确定性预旗(pledge.csv 在才注,presence-gated)
    if pm:
        lines.append(pm)
    mm = _misread_mark(l2)                   # 误读预警:低基/背离/套牢(复用已读 L2 行,presence-gated)
    if mm:
        lines.append(mm)
    cm = _cat_mark(base, code6)              # 催化行:三端点事件计数(L3_catalyst.csv 在才注)
    if cm:
        lines.append(cm)
    im = _inst_mark(base, code6)             # 机构面:卖方修正(consensus.csv 在才注,presence-gated)
    if im:
        lines.append(im)
    fm = _fund_mark(base, code6)             # 机构面第二行:基金重仓(fund_hold.csv 在才注,presence-gated)
    if fm:
        lines.append(fm)
    pcm = _precedent_mark(base, code6, ind, None)   # 跨票同型判例:precedents.db 在才注(presence-gated)
    if pcm:
        lines.append(pcm)
    sector_block = ""
    try:                                     # Phase 3:行业 brief 地形段(同链摊销;无 brief → memo 行回退)
        from autoresearch.sector.brief import render_terrain_block
        sector_block = render_terrain_block(ind, base)
    except Exception:  # noqa: BLE001
        sector_block = ""
    if sector_block:
        lines.append(sector_block)
    else:
        try:                                 # 行业备忘录(记忆中层:行业级历史事实,非方向)
            from autoresearch.learning.sector_memo import render_memo_line
            ml = render_memo_line(ind)
            if ml:
                lines.append(ml)
        except Exception:  # noqa: BLE001
            pass
    brief = "\n".join(lines) + "\n"
    ctx = _market_ctx(base, ind)
    dsum = _dossier_summary_mark(code6)      # Wave3 ④:覆盖档案摘要(presence-gated,缺="")
    doss = ""
    try:                                     # R5·前科卡(历史事实,增量研究;异常吞掉老 brief 不破)
        from autoresearch.scan.dossier import render_dossier
        doss = render_dossier(code6, scan_root=base.parent, exclude=base.name)
    except Exception:  # noqa: BLE001
        doss = ""
    parts = [p for p in (ctx, dsum, doss, brief) if p]
    return "\n".join(parts)

def _target_calib_mark(base: Path | str) -> str:
    """📐 目标价基率锚行(presence-gated:`target_calib.json` 缺 → ""）。日级(非逐票),
    整次派发只算一次,逐卡块内原样复用。

    路径按 `base`(scan_dir,生产态 = `context/scan/<date>`)反推兄弟目录
    `base.parent.parent/learning/target_calib.json`——镜像 `_precedent_mark` 的
    `context/{scan,learning}` 兄弟约定,tmp_path 天然隔离,不需 monkeypatch。当日 regime
    读 `base` 自己的 `meta.json`(缺文件/缺键 → 只报全体,同 regime 段跳过)。
    """
    p = Path(base).parent.parent / "learning" / "target_calib.json"
    if not p.exists():
        return ""
    try:
        import json

        from autoresearch.learning.buy_ledger import target_calib_line
        calib = json.loads(p.read_text(encoding="utf-8"))
        regime = None
        mp = Path(base) / "meta.json"
        if mp.exists():
            regime = json.loads(mp.read_text(encoding="utf-8")).get("regime")
        return target_calib_line(calib, regime) or ""
    except Exception:  # noqa: BLE001 — 锚可选,缺了不挡简报
        return ""
