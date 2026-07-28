#!/usr/bin/env python3
"""scan-market · L4 研究的确定性 helper(漏斗简报 + 选择器 + 评级评分卡 rubric)。

design: docs/specs/2026-06-24-l4-progressive-depth-design.md。

零 LLM。L4 = 一只 finalist = 一个 Opus subagent 跑 analyze-ticker-lite(渐进深度 + 早停);
本模块只做**确定性件**:P0 漏斗简报组装(compose_funnel_brief)、卡片评级解析、机会成本红队
名单(pick_opportunity_candidates)、LLM-as-judge 评分卡(净分定档 + OW 硬门压 Hold,防过度多报)。
selftest 已迁 pytest(tests/scan/test_agents.py)。
"""
from __future__ import annotations

import re
from pathlib import Path

import pandas as pd

# ───────────────────────── L4:选择器(评级解析 + 买单 skeptic 名单;单 Opus subagent 渐进深度) ─────────────────────────


def parse_ratings_from_details(details_dir: Path | str) -> dict[str, str]:
    """读 details/*.md 决策卡,复用项目 `parse_rating` 提五档评级 → {code: rating}。

    code = 文件名 stem(6 位代码);读不到卡/无评级 → `parse_rating` 回退 'Hold'。
    """
    from autoresearch.agents.utils.rating import parse_rating  # 延迟导入,保持本模块轻量
    out: dict[str, str] = {}
    base = Path(details_dir)
    if not base.exists():
        return out
    for p in sorted(base.glob("*.md")):
        code = p.stem
        out[code.zfill(6) if code.isdigit() else code] = parse_rating(p.read_text(encoding="utf-8"))
    return out


def pick_opportunity_candidates(ratings: dict[str, str], scan_dir, k: int = 2) -> list[str]:
    """**机会成本红队名单**(0买日;spec 2026-07-02 任务E):rubric 分最高的 Hold top-k。

    对称性修复:买单有 skeptic 红队,空仓从来没有——连续 0 买后系统无法自证"门太紧还是
    市场真没货"。每只派一个独立 Opus **bull 方**立论、PM 三透镜裁判;产出**只进观察单
    (结构化 conds)与校准数据,不改评级**(门的松紧不动)。排序键 = finalists.csv 的
    L3 conviction(确定性、现成);缺 finalists → []。
    """
    from pathlib import Path

    import pandas as pd
    f = Path(scan_dir) / "finalists.csv"
    holds = {str(c).zfill(6) for c, r in ratings.items() if r == "Hold"}
    if not holds or not f.exists():
        return []
    df = pd.read_csv(f, dtype={"code": str})
    if "code" not in df.columns:
        return []
    df["code"] = df["code"].astype(str).str.zfill(6)
    df["_cv"] = pd.to_numeric(df.get("conviction"), errors="coerce").fillna(0)
    df = df[df["code"].isin(holds)].sort_values("_cv", ascending=False, kind="stable")
    return df["code"].head(k).tolist()


# ───────────────────────── L4 · P0:漏斗简报(定向,确定性组装) ─────────────────────────


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


_BASE_RATE_THIN_N = 10   # ⚠ 薄样本阈值(既有 cross_calib/rating_base_rates min_n 惯例,镜像不新拍)


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


# ───────────────────────── L4 · C:评级评分卡(LLM-as-judge rubric,确定性锚) ─────────────────────────

_RUBRIC_DIMS = ("基本面", "估值", "技术资金", "盈利质量", "偿付", "催化")
_DIM_SCORE = {"强": 1, "中": 0, "弱": -1}
_OW_GATES = ("主力真在", "业绩真兑现", "估值不透支")


def _norm_dim(k: str) -> str:
    """维度名归一:技术·资金→技术资金、偿付(爆雷)→偿付,去修饰/空白对齐锚键。"""
    s = str(k)
    for ch in "·()（）爆雷 　":
        s = s.replace(ch, "")
    return s


def rubric_rating(dims: dict, gates: dict) -> tuple[str, str]:
    """C·LLM-as-judge 评分卡:6 维(强+1/中0/弱−1)净分定档 + 3 道 OW 硬门 → 确定性建议评级 + 约束因。

    动机:Sonnet 凭 gestalt 过度多报(实测 6-18:10 OW vs Opus 3 OW),撑大 Tier-2 复核量。把评级
    **派生**自评分卡——净分映射档位,但**任一 OW 门未过则 ≥Overweight 一律压到 Hold**(对齐 Tier-1
    『三条全中才 OW』)。卡片据此自检:`**Rating**` 必须 = 建议,否则显式写 `**偏离**:<硬理由>`。

    dims: {维度: 强|中|弱}(缺/不识别按 中=0;键名容错 技术·资金 / 偿付(爆雷));
    gates: {主力真在|业绩真兑现|估值不透支: bool}(缺按 False 保守)。
    返回 (建议评级, 约束因)。
    """
    from autoresearch.agents.utils.rating import RATINGS_5_TIER  # Buy>OW>Hold>UW>Sell
    nd = {_norm_dim(k): v for k, v in (dims or {}).items()}
    net = sum(_DIM_SCORE.get(str(nd.get(d, "中")).strip(), 0) for d in _RUBRIC_DIMS)
    if net >= 4:
        base = "Buy"
    elif net >= 2:
        base = "Overweight"
    elif net >= -1:
        base = "Hold"
    elif net >= -3:
        base = "Underweight"
    else:
        base = "Sell"
    order = {r: i for i, r in enumerate(RATINGS_5_TIER)}
    failed = [g for g in _OW_GATES if not (gates or {}).get(g, False)]
    if order[base] < order["Hold"] and failed:        # 想给 ≥OW 但有门没过 → 压 Hold(防过度多报)
        return "Hold", f"净分{net:+d}→{base},OW门未过({'、'.join(failed)})→压Hold"
    suffix = "(OW门3/3)" if order[base] < order["Hold"] else ""
    return base, f"净分{net:+d}→{base}{suffix}"


# ───────────────────────── L4 · 早停安全网(强先验强制满卡) ─────────────────────────


def force_full_card(priors: dict, *, conv_min: float = 70.0, channels_min: int = 4) -> bool:
    """**强先验白名单**:P0 先验极强者强制跑满卡(P4+P5),不被表面 P1-P3 早停误杀真龙头。

    两条独立通路,任一成立即强制满卡:

    ① **📌 保送票**(`lane == "pinned"`)—— 恒 True,不看 conviction/通道。你真金白银持有的票,
       「盈利质量」「偿付(爆雷)」两维**不允许**标『未核』。pinned 走的是 finalist tier 之外的
       通路(`finalist=false`,conviction 常 50–55),按下面 ② 的 conv_min=70 判据必然落进早停
       —— 2026-07-12 实测 4/4 持仓卡全部早停在 P3、爆雷维未核,正是这个原因。
    ② **强先验**:conviction≥conv_min **且**(多路共振 n_channels≥channels_min **或** L2 配额
       救回 lane_reserved)。高 conviction 但孤路无 lane → 不强制(可能是单因子虚高,照常早停)。

    priors 缺键按弱处理。

    ⚠️ FN-1 史:本函数 2026-06-27 建成后**零生产调用点**(只有单测 + 一个从未勾选的 plan 复
    选框 T12),即这道早停安全网**从未在生产跑过**。2026-07-12 接进 `write_dispatch_pack`。
    改本函数请一并 grep 调用链,别再让它变回死码。
    """
    if str(priors.get("lane", "") or "").strip() == "pinned":
        return True
    conv = priors.get("conviction")
    try:
        conv = float(conv)
    except (TypeError, ValueError):
        return False
    if conv < conv_min:
        return False
    n_ch = priors.get("n_channels") or 0
    try:
        n_ch = int(n_ch)
    except (TypeError, ValueError):
        n_ch = 0
    return n_ch >= channels_min or bool(priors.get("l2_lane_reserved"))


# ───────────────────────── L4 · 派发包确定性落稿(harvest 清单 + prompt 稿) ─────────────────────────


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


def write_shared_instructions(scan_dir: Path | str) -> int:
    """落 `_l4_shared_instructions.md`(当日共享块,逐卡 byte-identical)。返回写入字节数。

    Wave5 ④B:该文件此前**全仓无生产者**(只有读者 + 测试写者),07-17/07-21 实测均不存在
    —— prelude 每天算出的 📐/🔁/🚪 当日校准行从未到达任何一张决策卡。这里把 STAGES.md:215
    描述的手工步骤变成确定性生产。

    纪律:prelude 建议行里**含「禁注」的行不贴**(样本不足的自我标注,贴进 prompt = 用坏
    先验污染判断);两个来源任一异常都不阻断(写出只含标头的稳定文件,好过没有文件)。
    """
    import contextlib

    scan_dir = Path(scan_dir)
    scan_dir.mkdir(parents=True, exist_ok=True)
    lines = ["## 当日共享块(全卡一致;确定性生成,勿逐卡改写)"]
    calib: list[str] = []
    with contextlib.suppress(Exception):
        from autoresearch.scan.prelude import calib_suggestion_lines
        calib = [ln for ln in calib_suggestion_lines() if "禁注" not in ln]
    if calib:
        lines += ["", "### 当日校准锚(据实调用,不作评级指令)"] + [f"- {ln}" for ln in calib]
    t1_blk = ""
    with contextlib.suppress(Exception):
        from autoresearch.learning.t1_review import render_t1_calibration_block
        t1_blk = render_t1_calibration_block(stage="L4")
    if t1_blk:
        lines += ["", t1_blk.strip()]
    text = "\n".join(lines).strip() + "\n"
    p = scan_dir / "_l4_shared_instructions.md"
    p.write_text(text, encoding="utf-8")
    return len(text.encode("utf-8"))


def write_dispatch_pack(
    scan_dir: Path | str,
    *,
    stable_context: bool = False,
) -> dict:
    """L4 派发包确定性落稿(零 LLM):`_harvest_list.txt`(yfinance 归一后缀,`.SH` 绝迹)
    + 每卡 `_l4_prompt_<code>.md`(共享指令 + 漏斗简报 + slim/卡路径指针)。

    已有 `details/<code>.md`(♻️ 复用/已出卡)跳过。落稿契约从人肉变确定性:
    ① token 表输入侧从此可计(assemble 估算器认 `_l4_prompt_*`);② 编排以 prompt 稿为
    派发正文(共享块在前 = prompt cache 前缀命中);③ 07-03 `.SH` 空 slim 双跑从清单源头消灭。

    pinned 票(finalists 行 `lane == "pinned"`,design 2026-07-11 §4.1;plan Task 4):
    ♻️ 复用规则照常适用(已有卡照样跳过,不强制重派)——**只在确实要写新 prompt 时**,逐卡块
    (共享前缀**之后**,不碰 cache 契约)插一行 📌 标记 + note,让 L4 subagent 知道这是用户
    手工直通票、仍须真判不可走过场;跳过(♻️)的 pinned 票不计入本次 `pinned` 名单——它的
    📌 可见性在 L5 assemble 独立的「📌 保送」节(读 pinned.json + 已有卡评级),不需要本函数
    额外动作。返回 {n_prompts, n_skipped, tickers, pinned}(pinned = 本次新派发的 pinned 码)。
    """
    scan_dir = Path(scan_dir)
    date = scan_dir.name
    fp = scan_dir / "finalists.csv"
    if not fp.exists():
        return {"n_prompts": 0, "n_skipped": 0, "tickers": [], "pinned": []}
    from autoresearch.dataflows.symbol_utils import normalize_symbol  # lazy,保持模块轻量
    fin = pd.read_csv(fp, dtype={"code": str})
    import contextlib
    # FN-1 第四修:🔁 基率 json 此前无生产调用点(真实跑动恒空)——派发前日级落稿,幂等;
    # 失败不挡派发(消费方 _base_rate_mark presence-gated,缺文件即无此行)。
    with contextlib.suppress(Exception):
        write_base_rates(scan_dir)
    with contextlib.suppress(Exception):   # 终审 I-3:📐 锚随派发日刷新(与基率同节奏),不冻结在首算日分布
        from autoresearch.learning.buy_ledger import write_target_calib
        write_target_calib()
    shared = ""
    sp = scan_dir / "_l4_shared_instructions.md"
    if sp.exists():
        shared = sp.read_text(encoding="utf-8").strip()
    # (Wave5 ④B)t1 校准块已并入 `write_shared_instructions` 写的文件 —— 共享块的唯一事实源
    # 就是那个文件,消费侧不再二次拼接(否则同一段会在每张卡里出现两遍)。
    calib_line = _target_calib_mark(scan_dir)        # 📐 目标价基率锚(日级,算一次逐卡复用)

    # FN-1 第五修:`force_full_card`(早停安全网)自 2026-06-27 建成起**零生产调用点** ——
    # 高 conviction+多路共振的真龙头照样被表面 P1-P3 早停砍掉。这里接进真派发链。
    # n_channels / l2_lane_reserved 只在 L2 表里(finalists.csv 没这两列)→ 一次读入建索引。
    l2_priors: dict[str, dict] = {}
    l2p = scan_dir / "L2_gbdt_top200.csv"
    if l2p.exists():
        _l2 = pd.read_csv(l2p, dtype={"code": str})
        if "code" in _l2.columns:
            _l2["code"] = _l2["code"].astype(str).str.zfill(6)
            l2_priors = _l2.set_index("code").to_dict("index")

    tickers: list[str] = []
    pinned: list[str] = []
    n_prompts = n_skipped = 0
    prompt_manifest = {
        "schema_version": 1,
        "mode": "stable_context" if stable_context else "legacy",
        "prompts": {},
    }
    market_pack_data: dict = {}
    common_market = ""
    common_market_written = None
    if stable_context:
        from autoresearch.scan.context_blocks import write_context_block
        from autoresearch.scan.market import market_context_parts, market_pack

        try:
            market_pack_data = market_pack(scan_dir)
            if market_pack_data.get("regime"):
                common_market = market_context_parts(market_pack_data)[0]
        except Exception:  # noqa: BLE001 — 稳定块可选,与旧 _market_ctx 同降级语义
            market_pack_data = {}
            common_market = ""
        market_sources = [
            p for p in (
                scan_dir / "market_pack.json",
                scan_dir / "L1_scored_full.csv",
                scan_dir / "sectors.csv",
            ) if p.is_file()
        ]
        common_market_written = write_context_block(
            scan_dir,
            kind="market",
            scope="all",
            content=common_market,
            source_paths=market_sources,
        )
    for _, r in fin.iterrows():
        raw = str(r.get("code", "") or "").strip()
        if not raw or raw == "nan":
            continue
        code6 = raw.split(".")[0].zfill(6)
        if (scan_dir / "details" / f"{code6}.md").exists():
            n_skipped += 1                          # ♻️ 复用卡已就位:不重拉不派发(pinned 不例外)
            continue
        ticker = normalize_symbol(code6)            # 6 位码 → .SS/.SZ/.BJ(单一后缀口径)
        tickers.append(ticker)
        is_pinned = str(r.get("lane", "") or "").strip() == "pinned"
        body = [f"## L4 派发 — {code6} {r.get('name', '')}", ""]
        if is_pinned:                                # 逐卡块内标记(共享前缀之后,不破 cache 契约)
            pinned.append(code6)
            note = str(r.get("pinned_note", "") or "").strip()
            body += ["**📌 保送票**(用户手工直通;已在 L1→L3 全程强留,不受漏斗取舍影响"
                    + (f":{note}" if note else "") +
                    ")——仍须按下方真实证据独立评判,不因『保送』降低尽调标准。", "",
                    "**📌持仓管理要求**:本票为用户保送票(可能已持有)——满卡/早停卡都必须含"
                    "『持仓管理』小节:D+1/D+2 卖出纪律(何价减/何价清)+加减仓触发位;若 "
                    "pinned_note 含成本信息按其计算浮盈亏,无则按现价基准写纪律。", ""]
        # 强制满卡(逐卡块内,共享前缀之后 → 不破 cache 契约):priors = finalists 行(conviction/
        # lane)+ L2 行(n_channels/l2_lane_reserved)。
        priors = {**l2_priors.get(code6, {}),
                  "conviction": r.get("conviction"),
                  "lane": r.get("lane")}
        if force_full_card(priors):
            why = ("📌 保送持仓票" if is_pinned else
                   f"强先验(conviction {r.get('conviction')} + 多路共振/配额救回)")
            body += [f"**⛔ 强制满卡 — {why}:禁止早停。** 必须跑完 P4(陷阱核)+ P5(满卡):"
                     "「盈利质量」与「偿付(爆雷)」两维**不得**标『未核』,必须 Read "
                     "`_slim_deep.md` 取证后给分。评级仍由 rubric 三门定——强制满卡只保证"
                     "**核得够深**,不保证结论向好(照样可以是 Underweight/Sell)。", ""]
        legacy_brief = compose_funnel_brief(code6, scan_dir)
        stable_blocks = None
        if stable_context:
            from autoresearch.scan.context_blocks import (
                manifest_ref,
                write_context_block,
            )
            from autoresearch.scan.market import market_context_parts

            industry = r.get("industry") or r.get("sector")
            if pd.isna(industry):
                industry = ""
            industry = str(industry or "")
            market_sector = ""
            if market_pack_data.get("regime"):
                market_sector = market_context_parts(
                    market_pack_data, industry=industry
                )[1]

            dossier_parts: list[str] = []
            dsum = _dossier_summary_mark(code6)
            if dsum:
                dossier_parts.append(dsum)
            try:
                from autoresearch.scan.dossier import render_dossier

                history = render_dossier(
                    code6, scan_root=scan_dir.parent, exclude=scan_dir.name
                )
            except Exception:  # noqa: BLE001
                history = ""
            if history:
                dossier_parts.append(history)
            dossier_content = "\n".join(dossier_parts)

            terrain = ""
            try:
                from autoresearch.sector.brief import render_terrain_block

                terrain = render_terrain_block(industry, scan_dir)
            except Exception:  # noqa: BLE001
                terrain = ""
            sector_content = "\n".join(
                part.rstrip() for part in (market_sector, terrain) if part
            )
            if sector_content:
                sector_content += "\n"

            # 从 legacy 全量简报中精确摘掉已提升为 stable block 的前导/独立块；
            # 余下就是逐股 differential。旧模式完全不走此分支，字节契约不变。
            differential = legacy_brief
            full_market = common_market + market_sector
            if full_market and differential.startswith(full_market):
                differential = differential[len(full_market):].lstrip("\n")
            if dossier_content and differential.startswith(dossier_content):
                differential = differential[len(dossier_content):].lstrip("\n")
            if terrain and terrain in differential:
                differential = differential.replace(terrain, "", 1)
            differential = differential.strip() + "\n"

            sector_sources = [
                p for p in (
                    scan_dir / "sectors.csv",
                    scan_dir / "sector_briefs" / f"{industry}.md",
                    Path("context/sector") / date / f"{industry}.json",
                ) if p.is_file()
            ]
            dossier_sources: list[Path] = []
            try:
                from autoresearch.dossier.schema import dossier_path

                dp = dossier_path(code6)
                if dp.is_file():
                    dossier_sources.append(dp)
            except Exception:  # noqa: BLE001
                pass
            differential_sources = [
                p for p in (
                    fp,
                    scan_dir / "L1_recall_top1000.csv",
                    scan_dir / "L2_gbdt_top200.csv",
                ) if p.is_file()
            ]
            sector_written = write_context_block(
                scan_dir,
                kind="sector",
                scope=industry or "unknown",
                content=sector_content,
                source_paths=sector_sources,
            )
            dossier_written = write_context_block(
                scan_dir,
                kind="dossier",
                scope=code6,
                content=dossier_content,
                source_paths=dossier_sources,
            )
            differential_written = write_context_block(
                scan_dir,
                kind="differential",
                scope=code6,
                content=differential,
                source_paths=differential_sources,
            )
            stable_blocks = {
                "market": manifest_ref(common_market_written),
                "sector": manifest_ref(sector_written),
                "dossier": manifest_ref(dossier_written),
                "differential": manifest_ref(differential_written),
            }
            body.extend([
                sector_content.rstrip(),
                dossier_content.rstrip(),
                differential.rstrip(),
            ])
        else:
            body.append(legacy_brief.rstrip())
        if calib_line:                               # 逐卡块内(共享前缀之后,不破 cache 契约)
            body += ["", calib_line]
        prompt_parts = [
            # 固定标头(逐卡不变,≤300B)——cache 前缀契约(T8):共享块前不得出现逐卡可变内容,
            # 否则 30 卡并发前缀全断、cache 全 miss。逐卡专属标题(含 📌 保送标记)移到共享块**之后**。
            "# L4 派发 prompt(确定性落稿;编排以此为派发正文;先读共享块再读下方逐卡简报)",
            "",
            shared or "_(共享指令稿缺:`_l4_shared_instructions.md` 未落——按 stock-research lite-playbook 执行)_",
            "",
        ]
        if stable_context and common_market:
            prompt_parts += [common_market.rstrip(), ""]
        prompt_parts += [
            "---",
            "",
            *body,
            "",
            "---",
            f"- slim 数据:`context/{ticker}_{date}_slim.md`(P1–P3 表面块;**>8KB 才可信**,≈4.8KB=NO_DATA 须重拉)",
            f"- deep 深核:`context/{ticker}_{date}_slim_deep.md`(**survivor 进 P4 才 Read**;早停卡不读;缺文件=陷阱维标「未核」)",
            f"- 活体情报:`context/scan/{date}/_l4_intel_{code6}.md`(若存在:P3 先读它作催化/题材/机构主料、"
            f"自发网查降 ≤1 条验证;缺文件=回退卡内网查,cap 原规则)",
            f"- 决策卡写往:`context/scan/{date}/details/{code6}.md`",
            ""]
        prompt = "\n".join(prompt_parts)
        (scan_dir / f"_l4_prompt_{code6}.md").write_text(prompt, encoding="utf-8")
        if stable_blocks is not None:
            prompt_manifest["prompts"][code6] = {
                "path": str(scan_dir / f"_l4_prompt_{code6}.md"),
                "blocks": stable_blocks,
            }
        n_prompts += 1
    (scan_dir / "_harvest_list.txt").write_text(
        "\n".join(tickers) + ("\n" if tickers else ""), encoding="utf-8")
    if stable_context:
        import json

        target = scan_dir / "_l4_prompt_manifest.json"
        temp = target.with_name(f"{target.name}.tmp")
        temp.write_text(
            json.dumps(prompt_manifest, ensure_ascii=False, sort_keys=True, indent=2)
            + "\n",
            encoding="utf-8",
        )
        temp.replace(target)
    return {
        "n_prompts": n_prompts,
        "n_skipped": n_skipped,
        "tickers": tickers,
        "pinned": pinned,
        "context_mode": prompt_manifest["mode"],
    }


def dispatch_plan(date: str, root: Path | str | None = None) -> dict:
    """L4 派发感知 TTL 复用(确定性,零 LLM;复审 task-4-review.md Important #1 修复)。

    `write_dispatch_pack` 对已有 `details/<code>.md` 的复用码 skip(不写
    `_l4_prompt_<code>.md`),但工作流原先对**全部** finalists 无条件派卡 —— 复用码那份
    prompt 文件根本不存在,派了个读空文件的 Opus(抵消 TTL 复用省下的成本,复用卡评级也
    没并回 `cards`)。本函数按同一判据(`_l4_prompt_<code>.md` 是否存在)把 finalists
    分两路:`dispatch`(需新派 Opus)与 `reused`(已就位卡,直接 `parse_rating` 解评级
    并回,不再派 subagent)。两个标志都缺(异常态)→ 归 `dispatch`,兜底走正常派发。

    返回 `{"dispatch": [code6...], "reused": [{"code","rating"}...], "meta": {code6: {"name","sector"}}}`
    ——`meta` 仅含 `dispatch` 码(L4 情报站 plan Task 2:供并行情报 agent 派发 prompt 用名称/行业,
    不查 finalists.csv 即可读到),直取 finalists.csv 的 `name`/`sector` 列,缺列容错为 `""`。
    """
    base = Path(root) if root else Path("context/scan")
    scan_dir = base / date
    fp = scan_dir / "finalists.csv"
    dispatch: list[str] = []
    reused: list[dict] = []
    meta: dict[str, dict] = {}
    if not fp.exists():
        return {"dispatch": dispatch, "reused": reused, "meta": meta}
    from autoresearch.agents.utils.rating import parse_rating  # 延迟导入,保持本模块轻量
    fin = pd.read_csv(fp, dtype={"code": str})

    def _cell(row, k):
        # 空单元格 pandas 读成 NaN(truthy float)→ str() 会产字面 "nan" 注入盲搜 prompt(终审 I-1)
        v = row.get(k, "")
        return "" if pd.isna(v) else str(v)

    for _, r in fin.iterrows():
        raw = str(r.get("code", "") or "").strip()
        if not raw or raw == "nan":
            continue
        code6 = raw.split(".")[0].zfill(6)
        if (scan_dir / f"_l4_prompt_{code6}.md").exists():
            dispatch.append(code6)
            meta[code6] = {"name": _cell(r, "name"), "sector": _cell(r, "sector"),
                           "pinned": _cell(r, "lane").strip() == "pinned",
                           # Wave3.5:intel 已知底「内嵌代替授权」——摘要文本随 meta 走,
                           # workflow 内嵌进 intel prompt,agent 因此无需 Read 权限(结构性盲回工具级)。
                           "dossier_summary": _dossier_summary_text(code6)}
            continue
        details = scan_dir / "details" / f"{code6}.md"
        if details.exists():
            reused.append({"code": code6, "rating": parse_rating(details.read_text(encoding="utf-8"))})
            import contextlib
            with contextlib.suppress(Exception):
                from autoresearch.scan.stock_stage import record_l4_result

                record_l4_result(scan_dir, code6, reused=True)
        else:
            dispatch.append(code6)   # 两者皆无(异常):兜底走正常派发,不静默丢票
            meta[code6] = {"name": _cell(r, "name"), "sector": _cell(r, "sector"),
                           "pinned": _cell(r, "lane").strip() == "pinned",
                           # Wave3.5:intel 已知底「内嵌代替授权」——摘要文本随 meta 走,
                           # workflow 内嵌进 intel prompt,agent 因此无需 Read 权限(结构性盲回工具级)。
                           "dossier_summary": _dossier_summary_text(code6)}
    return {"dispatch": dispatch, "reused": reused, "meta": meta}


def _tushare_pledge(code6: str) -> tuple[float, str] | None:
    """默认取数器:tushare pledge_stat 最新一期 → (ratio, end_date);失败/空 → None。"""
    from autoresearch.data.tushare_enrich import _pro, _ts_call, _tscode
    pro = _pro()
    pl = _ts_call(lambda: pro.pledge_stat(ts_code=_tscode(code6)))
    if pl is None or not len(pl):
        return None
    row = pl.sort_values("end_date").tail(1).iloc[0]
    r = pd.to_numeric(pd.Series([row["pledge_ratio"]]), errors="coerce").iloc[0]
    return None if pd.isna(r) else (float(r), str(row["end_date"]))


def fetch_pledge(scan_dir: Path | str, codes=None, fetch_fn=None,
                 reuse_days: int = 7) -> pd.DataFrame:
    """finalists 级质押取数 → `pledge.csv`(code,pledge_ratio,end_date)。零 LLM。

    近 reuse_days 内其他 scan 日已拉过的 code 直接复用(周频数据,不重拉);缺的走
    fetch_fn(默认 tushare pledge_stat,~30 calls/日远离限频),单票失败降级跳过。
    spec 2026-07-05 §5.2。
    """
    from datetime import datetime
    scan_dir = Path(scan_dir)
    if codes is None:
        fp = scan_dir / "finalists.csv"
        if not fp.exists():
            return pd.DataFrame(columns=["code", "pledge_ratio", "end_date"])
        codes = pd.read_csv(fp, dtype={"code": str})["code"].tolist()
    want = [str(c).split(".")[0].zfill(6) for c in codes]

    def _d(name: str):
        try:
            return datetime.strptime(name, "%Y-%m-%d")
        except ValueError:
            return None

    today = _d(scan_dir.name)
    rows: dict[str, dict] = {}
    if today is not None and scan_dir.parent.exists():
        for sib in sorted((p for p in scan_dir.parent.iterdir() if p.is_dir()), reverse=True):
            sd = _d(sib.name)
            if sd is None or sib == scan_dir or not 0 <= (today - sd).days <= reuse_days:
                continue
            pp = sib / "pledge.csv"
            if not pp.exists():
                continue
            try:
                prev = pd.read_csv(pp, dtype={"code": str})
            except Exception:  # noqa: BLE001
                continue
            prev["code"] = prev["code"].astype(str).str.zfill(6)
            for _, r in prev.iterrows():
                c = r["code"]
                if c in want and c not in rows:
                    rows[c] = {"code": c, "pledge_ratio": r.get("pledge_ratio"),
                               "end_date": r.get("end_date")}
    fetch_fn = fetch_fn or _tushare_pledge
    for c in want:
        if c in rows:
            continue
        try:
            got = fetch_fn(c)
        except Exception:  # noqa: BLE001 — 单票降级隔离
            got = None
        if got is not None:
            rows[c] = {"code": c, "pledge_ratio": got[0], "end_date": got[1]}
    out = pd.DataFrame([rows[c] for c in want if c in rows],
                       columns=["code", "pledge_ratio", "end_date"])
    out.to_csv(scan_dir / "pledge.csv", index=False)
    return out


def _tushare_seats_by_date(dates: list[str]) -> dict[str, pd.DataFrame]:
    """按 trade_date bulk 龙虎榜机构明细(一天一调,非逐票)。date=YYYYMMDD。"""
    from autoresearch.data.tushare_source import _pro, _ts_call
    pro = _pro()
    out: dict[str, pd.DataFrame] = {}
    for d in dates:
        try:
            df = _ts_call(lambda d=d: pro.top_inst(trade_date=d))
        except Exception:  # noqa: BLE001 — 单日降级隔离
            df = None
        if df is not None and len(df):
            out[d] = df
    return out


def fetch_seats(scan_dir: Path | str, codes=None, bulk_fn=None, reuse_days: int = 7,
                window_days: int = 20) -> pd.DataFrame:
    """finalists 龙虎榜机构 vs 游资席位聚合 → `seats.csv`(code,inst_net_wan,retail_net_wan,n_appear)。

    成本控制:`top_inst` 按日 bulk **一次**再对全 finalists 过滤聚合(非 lhb_seats 逐票×15);
    近 reuse_days 内其他 scan 日已算的 code 直接复用。mirror `fetch_pledge`。零 LLM。
    """
    from datetime import datetime, timedelta

    from autoresearch.data.tushare_source import _code6, _pro, _trade_days, resolve_momentum_dates
    scan_dir = Path(scan_dir)
    cols = ["code", "inst_net_wan", "retail_net_wan", "n_appear"]
    if codes is None:
        fp = scan_dir / "finalists.csv"
        if not fp.exists():
            return pd.DataFrame(columns=cols)
        codes = pd.read_csv(fp, dtype={"code": str})["code"].tolist()
    want = [str(c).split(".")[0].zfill(6) for c in codes]

    def _d(name: str):
        try:
            return datetime.strptime(name, "%Y-%m-%d")
        except ValueError:
            return None

    today = _d(scan_dir.name)
    rows: dict[str, dict] = {}
    # 1) 跨 scan 日复用(mirror fetch_pledge)
    if today is not None and scan_dir.parent.exists():
        for sib in sorted((p for p in scan_dir.parent.iterdir() if p.is_dir()), reverse=True):
            sd = _d(sib.name)
            if sd is None or sib == scan_dir or not 0 <= (today - sd).days <= reuse_days:
                continue
            pp = sib / "seats.csv"
            if not pp.exists():
                continue
            try:
                prev = pd.read_csv(pp, dtype={"code": str})
            except Exception:  # noqa: BLE001
                continue
            prev["code"] = prev["code"].astype(str).str.zfill(6)
            for _, r in prev.iterrows():
                c = r["code"]
                if c in want and c not in rows:
                    rows[c] = {k: r.get(k) for k in cols}
    missing = [c for c in want if c not in rows]
    # 2) 缺的:按日 bulk 一次,聚合全 missing
    if missing:
        try:
            pro = _pro()
            last = resolve_momentum_dates(pro, scan_dir.name)[0]
            start = (datetime.strptime(last, "%Y%m%d") - timedelta(days=window_days)).strftime("%Y%m%d")
            dates = _trade_days(pro, start, last)[-15:]
        except Exception:  # noqa: BLE001
            dates = []
        frames = (bulk_fn or _tushare_seats_by_date)(dates) if dates else {}
        agg = {c: {"inst": 0.0, "retail": 0.0, "n": 0} for c in missing}
        for df in frames.values():
            if df is None or not len(df):
                continue
            c6 = _code6(df["ts_code"])
            for c in missing:
                sub = df[c6 == c]
                if not len(sub):
                    continue
                agg[c]["n"] += 1
                for _, r in sub.iterrows():
                    _nb = r.get("net_buy")                       # NaN 守卫:`nan or 0 → nan` 会毒化整 code 聚合
                    net = 0.0 if (_nb is None or pd.isna(_nb)) else float(_nb)
                    if "机构专用" in str(r.get("exalter", "")):
                        agg[c]["inst"] += net
                    else:
                        agg[c]["retail"] += net
        for c in missing:
            a = agg[c]
            rows[c] = {"code": c, "inst_net_wan": round(a["inst"] / 1e4, 0),
                       "retail_net_wan": round(a["retail"] / 1e4, 0), "n_appear": a["n"]}
    out = pd.DataFrame([rows[c] for c in want if c in rows], columns=cols)
    out.to_csv(scan_dir / "seats.csv", index=False)
    return out


def fetch_consensus(scan_dir: Path | str, codes=None, window: int = 30,
                    cache_root: Path | None = None) -> pd.DataFrame:
    """finalists 卖方一致预期修正 → `consensus.csv`(code,n_reports,eps_delta_pct)。零 LLM。

    从 report_rc 缓存(consensus.pull/backfill 积累)取分析日前最近 `window` 个缓存日,
    前后对半为两窗,算 FY 一致 EPS 中位修正(research/consensus.consensus_delta)。
    缓存日 <10 → 空表不落盘(样本太薄禁注,presence-gated)。advisory:不进分、不设门。
    """
    scan_dir = Path(scan_dir)
    date = scan_dir.name
    from autoresearch.research.consensus import _dir, _load_span, consensus_delta
    stems = sorted(p.stem for p in _dir(cache_root).glob("*.pkl")
                   if p.stem <= date.replace("-", ""))[-window:]
    if len(stems) < 10:
        return pd.DataFrame(columns=["code", "n_reports", "eps_delta_pct"])
    half = len(stems) // 2
    old_span, new_span = (stems[0], stems[half - 1]), (stems[half], stems[-1])
    fy = date[:4]
    delta = consensus_delta(date, old_span, new_span, fy, cache_root=cache_root)
    recent = _load_span(new_span, cache_root)
    recent["code"] = recent["ts_code"].astype(str).str[:6]
    n_rep = recent.groupby("code").size().rename("n_reports")
    out = delta.merge(n_rep, on="code", how="left")
    out["n_reports"] = out["n_reports"].fillna(0).astype(int)
    if codes is not None:
        want = {str(c).split(".")[0].zfill(6) for c in codes}
        out = out[out["code"].isin(want)]
    out = out[["code", "n_reports", "eps_delta_pct"]].reset_index(drop=True)
    if len(out):
        out.to_csv(scan_dir / "consensus.csv", index=False)
    return out


def _tushare_fund_hold(period: str) -> pd.DataFrame | None:
    """默认取数器:tushare `fund_portfolio`(翻页取数在 `tushare_source._fetch_fund_portfolio`)。"""
    from autoresearch.data.tushare_source import _fetch_fund_portfolio, _pro
    return _fetch_fund_portfolio(_pro(), period)


def fetch_fund_hold(scan_dir: Path | str, codes=None, fetch_fn=None) -> pd.DataFrame:
    """finalists 基金重仓聚合 → `fund_hold.csv`(code,n_funds,mkv_yi,n_funds_delta)。零 LLM。

    Plan 1 Task 6 探针裁决"可用"(context/factor_lab/cache/probes/fund_portfolio_20260710.json):
    该端点不支持按个股直查(ts_code/ann_date/period 三选一起效,个股不在其中)→ 反查姿势 =
    按最近季度末(`common.scoring.latest_reported_quarter`)批量拉取(翻页,默认走
    `tushare_source._fetch_fund_portfolio`)再本地按 symbol(重仓股代码)过滤聚合。
    `n_funds_delta` = 与上一季度末(`prev_quarter`)对比家数环比。**季度滞后**
    (定期报告披露制,非实时)——advisory,不进分不设门。缺权限/端点异常/无覆盖 → 空表
    不落盘(presence-gated,mirror `fetch_pledge`)。
    """
    from autoresearch.common.scoring import latest_reported_quarter, prev_quarter
    scan_dir = Path(scan_dir)
    cols = ["code", "n_funds", "mkv_yi", "n_funds_delta"]
    if codes is None:
        fp = scan_dir / "finalists.csv"
        if not fp.exists():
            return pd.DataFrame(columns=cols)
        codes = pd.read_csv(fp, dtype={"code": str})["code"].tolist()
    want = {str(c).split(".")[0].zfill(6) for c in codes}
    if not want:
        return pd.DataFrame(columns=cols)

    def _agg(df: pd.DataFrame | None) -> pd.DataFrame:
        empty = pd.DataFrame(columns=["code", "n_funds", "mkv_yi"])
        if df is None or not len(df) or "symbol" not in df.columns or "ts_code" not in df.columns:
            return empty
        d = df.copy()
        d["code"] = d["symbol"].astype(str).str.split(".").str[0].str.zfill(6)
        d = d[d["code"].isin(want)]
        if not len(d):
            return empty
        n_funds = d.groupby("code")["ts_code"].nunique().rename("n_funds")
        mkv_src = pd.to_numeric(d["mkv"], errors="coerce") if "mkv" in d.columns else pd.Series(0.0, index=d.index)
        mkv_yi = (mkv_src.groupby(d["code"]).sum() / 1e8).rename("mkv_yi")
        return pd.concat([n_funds, mkv_yi], axis=1).reset_index()

    fetch_fn = fetch_fn or _tushare_fund_hold
    period = latest_reported_quarter(scan_dir.name)
    try:
        cur = fetch_fn(period)
    except Exception:  # noqa: BLE001 — 端点/权限降级隔离,行可选
        cur = None
    out = _agg(cur)
    if not len(out):
        return pd.DataFrame(columns=cols)

    try:
        prev_raw = fetch_fn(prev_quarter(period))
    except Exception:  # noqa: BLE001
        prev_raw = None
    prev_agg = _agg(prev_raw)
    if len(prev_agg):
        out = out.merge(prev_agg[["code", "n_funds"]].rename(columns={"n_funds": "n_funds_prev"}),
                        on="code", how="left")
        out["n_funds_delta"] = out["n_funds"] - out["n_funds_prev"].fillna(0)
        out = out.drop(columns=["n_funds_prev"])
    else:
        out["n_funds_delta"] = float("nan")
    out = out[cols].reset_index(drop=True)
    out.to_csv(scan_dir / "fund_hold.csv", index=False)
    return out


def _default_harvest_slim(ticker: str, date: str, ctx_root: Path) -> Path:
    import subprocess
    import sys

    subprocess.run(
        [sys.executable, "-m", "autoresearch.analyze.harvest", ticker, date, "stock", "--slim"],
        check=False)
    return ctx_root / f"{ticker}_{date}_slim.md"


# GATE3 结构锚:harvest 真跑通的 slim 必有这四块(表面块口径)。缺任一 = 降级/NO_DATA。
_SLIM_ANCHORS = (
    "## Verified market snapshot",          # 行情真身
    "### Latest verified OHLCV row",        # 最新 K 线
    "## Market context",                    # L1 复用块(主力/技术/筹码)
    "## Fundamentals overview",             # 基本面
)
# OHLCV 的 Close 必须是真数值 —— 结构锚齐但填 NO_DATA 占位的降级稿要在这里落网。
_SLIM_CLOSE_RE = re.compile(r"\|\s*Close\s*\|\s*([0-9]+(?:\.[0-9]+)?)\s*\|")


def _slim_defect(path: Path | None, min_bytes: int) -> tuple[int, str | None]:
    """判一份 slim 能不能用。返回 (bytes, 缺陷描述);缺陷 None = 合格。

    **规模检查与结构检查分开**(2026-07-14 生产回归 + [[data-contracts-fail-fast]] 教训):
    · 结构+内容 = 能不能用的真判据(锚齐 ∧ OHLCV Close 是真数值)
    · 体积 = 只兜真垃圾(空文件/截断),**不参与"数据够不够"的判断**

    为什么不能再用体积当主判据:2026-07-14 药石科技(300725)slim **8176B,差 16 字节**没够
    8192B 门槛 —— 24 节一个不缺、行情/主力/筹码全真,只是当期新闻少几条 → 被 GATE3 误杀,
    整条流水线在 60min / 1.6M token / 33 agent 全完成后被毙。该门槛此前已因同类误杀从
    10_240B 降到 8_192B;再降一次只是把棘轮往下拧,治不了"拿体积当结构用"这个病根。
    """
    if path is None or not Path(path).exists():
        return 0, "文件不存在"
    p = Path(path)
    size = p.stat().st_size
    if size < min_bytes:                                  # 真垃圾地板(空/截断)
        return size, f"<{min_bytes}B(疑空稿/截断)"
    try:
        text = p.read_text(encoding="utf-8", errors="replace")
    except OSError as e:                                  # noqa: BLE001
        return size, f"读取失败:{e}"
    missing = [a for a in _SLIM_ANCHORS if a not in text]
    if missing:
        return size, f"结构缺块:{', '.join(missing)}"
    if not _SLIM_CLOSE_RE.search(text):
        return size, "结构齐但 OHLCV Close 无数值(NO_DATA 占位)"
    return size, None


def harvest_slim_batch(date: str, root: Path | None = None, min_bytes: int = 4_096,
                       retries: int = 1, harvest_fn=None, ctx_root: Path | None = None,
                       workers: int = 4) -> dict:
    """按 _harvest_list.txt 批量 harvest slim,**失败响亮**(修 603799 静默失败坑 = GATE 3)。

    合格判据见 `_slim_defect`:**结构+内容**决定能不能用,体积只兜真垃圾(地板 4KB)。
    offender 重试 `retries` 次仍有缺陷/含 .SH → 记失败。harvest_fn(ticker, date)->Path
    可注入(测试用),默认 shell 到 analyze.harvest --slim。

    workers=4 默认并发(spec §P3);subprocess 取数为 I/O 密集,限频靠 per-ticker retries
    串行重试承担。workers<=1 退化原串行 for 循环(兼容旧行为/便于对串行时序敏感的测试)。
    """
    base = Path(root) if root else Path("context/scan")
    scan_dir = base / date
    ctx = ctx_root or Path("context")
    tickers = [t for t in (scan_dir / "_harvest_list.txt").read_text(encoding="utf-8").split() if t]
    hv = harvest_fn or (lambda t, dt: _default_harvest_slim(t, dt, ctx))

    def _one(t: str) -> dict | None:
        """单票 harvest+判定;返回失败记录或 None(成功)。"""
        if ".SH" in t:                                    # 归一漏网(GATE 3 防线)
            return {"ticker": t, "bytes": -1, "why": ".SH 未归一"}
        size, why = 0, "未 harvest"
        for _ in range(retries + 1):
            try:
                size, why = _slim_defect(hv(t, date), min_bytes)
            except Exception as e:                        # noqa: BLE001
                size, why = 0, f"harvest 异常:{e}"
            if why is None:
                return None
        return {"ticker": t, "bytes": int(size), "why": why}

    if workers <= 1:
        results = [_one(t) for t in tickers]
    else:
        from concurrent.futures import ThreadPoolExecutor
        with ThreadPoolExecutor(max_workers=workers) as ex:
            results = list(ex.map(_one, tickers))       # map 保序 → failures 原序
    failures = [r for r in results if r]
    return {"ok": not failures, "n": len(tickers), "failures": failures}


def main(argv: list[str] | None = None) -> int:
    import argparse
    ap = argparse.ArgumentParser(description="L4 确定性件 CLI(派发包落稿/质押预旗,零 LLM)")
    ap.add_argument("cmd", choices=["shared", "prompts", "pledge", "seats", "consensus",
                                    "harvest-slim", "dispatch-plan"],
                    help="shared = 写 _l4_shared_instructions.md 当日共享块(prompts 之前跑);"
                         "prompts = 写 _harvest_list.txt + _l4_prompt_<code>.md;"
                         "pledge = finalists 批量质押 → pledge.csv(简报自动带 ⚠质押旗);"
                         "seats = finalists 龙虎榜席位聚合 → seats.csv(_seat_mark 注简报);"
                         "consensus = finalists 卖方一致预期修正 → consensus.csv"
                         "(+条件 fund_hold.csv 基金重仓;_inst_mark/_fund_mark 注简报);"
                         "harvest-slim = 按 _harvest_list.txt 批量 harvest slim;"
                         "dispatch-plan = 派发感知 TTL 复用(dispatch/reused 分流)")
    ap.add_argument("date", help="scan 日 YYYY-MM-DD")
    ap.add_argument("--root", default=None, help="scan 根目录(默认 context/scan)")
    ap.add_argument("--workers", type=int, default=4, help="slim 批量并发数(1=串行)")
    ap.add_argument(
        "--stable-context",
        action="store_true",
        help="共享市场/行业/档案块置前并写 hash manifest(缺省 legacy 字节路径)",
    )
    args = ap.parse_args(argv)
    if args.cmd == "shared":
        import json
        base = Path(args.root) if args.root else Path("context/scan")
        n = write_shared_instructions(base / args.date)
        print(json.dumps({"ok": True, "bytes": n}, ensure_ascii=False))
        return 0
    if args.cmd == "dispatch-plan":
        import json
        print(json.dumps(dispatch_plan(args.date, root=args.root), ensure_ascii=False))
        return 0
    if args.cmd == "harvest-slim":
        import json
        res = harvest_slim_batch(args.date, root=args.root, workers=args.workers)
        print(json.dumps({"ok": res["ok"],
                          "reason": ("ok" if res["ok"]
                                     else f"{len(res['failures'])}/{res['n']} slim 失败:"
                                          + ", ".join(f"{f['ticker']}({f['bytes']}B)"
                                                      for f in res["failures"])),
                          "failures": res["failures"]}, ensure_ascii=False))
        return 0 if res["ok"] else 1
    if args.cmd == "pledge":
        base = Path(args.root) if args.root else Path("context/scan")
        df = fetch_pledge(base / args.date)
        n_flag = int((pd.to_numeric(df.get("pledge_ratio"), errors="coerce") > 20).sum()) if len(df) else 0
        print(f"[l4_card pledge] {len(df)} 票落 pledge.csv(>20% 偏高/红旗 {n_flag} 票);"
              f"派发前跑,简报自动注 ⚠质押旗")
        return 0
    if args.cmd == "seats":
        base = Path(args.root) if args.root else Path("context/scan")
        df = fetch_seats(base / args.date)
        n_inst = int((df["inst_net_wan"] > 0).sum()) if len(df) else 0
        print(f"[l4_card seats] {len(df)} 票落 seats.csv(机构净买>0 {n_inst} 票=Phase A 反指候选)")
        return 0
    if args.cmd == "consensus":
        base = Path(args.root) if args.root else Path("context/scan")
        sd = base / args.date
        fp = sd / "finalists.csv"
        codes = pd.read_csv(fp, dtype={"code": str})["code"].tolist() if fp.exists() else None
        df = fetch_consensus(sd, codes=codes)
        try:
            fh = fetch_fund_hold(sd, codes=codes)
        except Exception:  # noqa: BLE001 — 基金行独立分支,不拖累卖方修正主线
            fh = pd.DataFrame()
        extra = f";{len(fh)} 票落 fund_hold.csv(基金重仓,季度滞后)" if len(fh) else ""
        print(f"[l4_card consensus] {len(df)} 票落 consensus.csv(卖方一致预期修正,advisory){extra}")
        return 0
    res = write_dispatch_pack(
        (Path(args.root) if args.root else Path("context/scan")) / args.date,
        stable_context=args.stable_context,
    )
    print(f"[l4_card prompts] {res['n_prompts']} 份 prompt + _harvest_list({len(res['tickers'])} 票,"
          f"已归一 yfinance 后缀);跳过已有卡 {res['n_skipped']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
