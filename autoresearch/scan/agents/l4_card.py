#!/usr/bin/env python3
"""scan-market · L4 研究的确定性 helper(漏斗简报 + 选择器 + 评级评分卡 rubric)。

design: docs/specs/2026-06-24-l4-progressive-depth-design.md。

零 LLM。L4 = 一只 finalist = 一个 Opus subagent 跑 analyze-ticker-lite(渐进深度 + 早停);
本模块只做**确定性件**:P0 漏斗简报组装(compose_funnel_brief)、卡片评级解析、买单 skeptic
名单(pick_buy_candidates / pick_buylist)、LLM-as-judge 评分卡(净分定档 + OW 硬门压 Hold,防过度多报)。
selftest 已迁 pytest(tests/scan/test_agents.py)。
"""
from __future__ import annotations

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


def pick_buy_candidates(ratings: dict[str, str],
                        include: tuple[str, ...] = ("Buy", "Overweight")) -> list[str]:
    """L4 **买单独立 skeptic 名单**:最终评级 ∈ include(Buy/OW)的发布买单,每只派一个
    独立 Opus skeptic 证伪(发布前红队)。早停只向下、买点必走 P4+P5 后才可能 ≥OW 到此。"""
    keep = set(include)
    return [c for c, r in ratings.items() if r in keep]


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


def pick_sentinel_candidates(scan_dir, k: int = 2) -> list[str]:
    """哨兵日红队对象:L2 `gbdt_score` top-k(哨兵档跳过 L3,无 conviction 可用)。缺 L2 → []。

    design: 2026-07-03-scan-sentinel-economy §1。产出与机会成本红队同规:只进观察单,不发评级。
    """
    from pathlib import Path

    import pandas as pd
    p = Path(scan_dir) / "L2_gbdt_top200.csv"
    if not p.exists():
        return []
    df = pd.read_csv(p, dtype={"code": str})
    if "code" not in df.columns:
        return []
    df["_s"] = pd.to_numeric(df.get("gbdt_score"), errors="coerce").fillna(-1e18)
    return df.sort_values("_s", ascending=False)["code"].astype(str).str.zfill(6).head(k).tolist()


def pick_earlystop_audit(scan_dir, k: int = 2, seed: int | None = None) -> list[str]:
    """早停抽检对象(opt-in;spec 2026-07-05 wave §A3):当日早停卡里确定性抽 k 张,
    派独立复核 agent 只读「深核分界后块 + 早停卡 + 简报」判误杀;产出进 proposals 不改评级。

    seed 缺省 = 数据日整数(同日重跑同名单);复用卡(♻️)与满卡(进入P4倾向)不抽。
    """
    import random
    from pathlib import Path
    scan_dir = Path(scan_dir)
    base = scan_dir / "details"
    if not base.is_dir():
        return []
    stops = []
    for p in sorted(base.glob("*.md")):
        text = p.read_text(encoding="utf-8")
        if "♻️" in text or "进入P4倾向" in text:
            continue
        if "早停因" in text:
            stops.append(p.stem)
    if not stops:
        return []
    if seed is None:
        digits = "".join(ch for ch in scan_dir.name if ch.isdigit())
        seed = int(digits or "0")
    rng = random.Random(seed)
    return sorted(rng.sample(stops, min(k, len(stops))))


def pick_buylist(ratings: dict[str, str], floor: str = "Overweight") -> list[str]:
    """评级 ≥ floor 的发布买单(floor=Overweight 时等价 pick_buy_candidates〔Buy/OW〕)。

    Tier-3 辩论输入用 `pick_buy_candidates`;本函数留作"最终买单"口径(Tier-3 折回后仍 ≥floor)。"""
    from autoresearch.agents.utils.rating import (
        RATINGS_5_TIER,  # Buy>Overweight>Hold>Underweight>Sell
    )
    order = {r: i for i, r in enumerate(RATINGS_5_TIER)}
    cap = order.get(floor, 1)
    return [c for c, r in ratings.items() if order.get(r, 99) <= cap]


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
        f"现价/成本 {_g(l1,'price_to_cost')}·北向占比 {_g(l1,'hk_ratio')}",
        f"- **L2**:gbdt_score {_g(l2,'gbdt_score')}(rank {_g(l2,'l2_rank')})",
        f"- **L3 入选**:conviction {_g(l3,'conviction')}·lane {_g(l3,'lane')}·情感 {_g(l3,'sentiment')}",
        f"  - 多头论点:{_g(l3,'thesis')}",
        f"  - 最大风险:{_g(l3,'risk')}",
        f"  - 催化:{_g(l3,'catalyst')}",
    ]
    try:                                     # 日历旗:解禁风险窗/预约披露日(事实日期非方向)
        from autoresearch.scan.calendar import calendar_flags
        lines += calendar_flags(base, code6)
    except Exception:  # noqa: BLE001 — 日历可选,缺了不挡简报
        pass
    pm = _pledge_mark(base, code6)           # 质押旗:确定性预旗(pledge.csv 在才注,presence-gated)
    if pm:
        lines.append(pm)
    cm = _cat_mark(base, code6)              # 催化行:三端点事件计数(L3_catalyst.csv 在才注)
    if cm:
        lines.append(cm)
    ind = l3.get("industry") or l3.get("sector") or l1.get("industry")
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
    doss = ""
    try:                                     # R5·前科卡(历史事实,增量研究;异常吞掉老 brief 不破)
        from autoresearch.scan.dossier import render_dossier
        doss = render_dossier(code6, scan_root=base.parent, exclude=base.name)
    except Exception:  # noqa: BLE001
        doss = ""
    parts = [p for p in (ctx, doss, brief) if p]
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


# ───────────────────────── L4 · 早停安全网 + 自评一致性抽检 ─────────────────────────


def force_full_card(priors: dict, *, conv_min: float = 70.0, channels_min: int = 4) -> bool:
    """**强先验白名单**:P0 先验极强者强制跑满卡(P4+P5),不被表面 P1-P3 早停误杀真龙头。

    判据:conviction≥conv_min **且**(多路共振 n_channels≥channels_min **或** L2 配额救回 lane_reserved)。
    高 conviction 但孤路无 lane → 不强制(可能是单因子虚高,照常走早停)。priors 缺键按弱处理。
    """
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


# 卡片自评 gate=True 时,正文若含这些词即矛盾(疑自评 gaming)。
_GATE_CONTRA = {
    "主力真在": ["净流出", "主力流出", "资金流出", "主力撤", "主力出逃"],
    "业绩真兑现": ["业绩下滑", "预亏", "预减", "净利下降", "增收不增利", "亏损扩大"],
    "估值不透支": ["估值偏高", "高估", "估值透支", "泡沫", "PE 偏高", "估值贵"],
}


def audit_rubric_gates(card_text: str, gates: dict) -> list[str]:
    """**自评一致性抽检**:卡片自报 OW gate=True,但正文出现反向措辞 → flag(防自评 gaming)。

    只查被声明为 True 的门;返回矛盾说明 list(空=无矛盾)。供 L4 回卡后抽检 / self_review 用。
    """
    t = str(card_text)
    flags: list[str] = []
    for gate, contras in _GATE_CONTRA.items():
        if not (gates or {}).get(gate):
            continue
        hit = next((c for c in contras if c in t), None)
        if hit:
            flags.append(f"{gate}=True 但正文含「{hit}」(自评与正文矛盾,疑 gaming)")
    return flags


# ───────────────────────── L4 · 派发包确定性落稿(harvest 清单 + prompt 稿) ─────────────────────────


def write_dispatch_pack(scan_dir: Path | str) -> dict:
    """L4 派发包确定性落稿(零 LLM):`_harvest_list.txt`(yfinance 归一后缀,`.SH` 绝迹)
    + 每卡 `_l4_prompt_<code>.md`(共享指令 + 漏斗简报 + slim/卡路径指针)。

    已有 `details/<code>.md`(♻️ 复用/已出卡)跳过。落稿契约从人肉变确定性:
    ① token 表输入侧从此可计(assemble 估算器认 `_l4_prompt_*`);② 编排以 prompt 稿为
    派发正文(共享块在前 = prompt cache 前缀命中);③ 07-03 `.SH` 空 slim 双跑从清单源头消灭。
    返回 {n_prompts, n_skipped, tickers}。
    """
    scan_dir = Path(scan_dir)
    date = scan_dir.name
    fp = scan_dir / "finalists.csv"
    if not fp.exists():
        return {"n_prompts": 0, "n_skipped": 0, "tickers": []}
    from autoresearch.dataflows.symbol_utils import normalize_symbol  # lazy,保持模块轻量
    fin = pd.read_csv(fp, dtype={"code": str})
    shared = ""
    sp = scan_dir / "_l4_shared_instructions.md"
    if sp.exists():
        shared = sp.read_text(encoding="utf-8").strip()
    tickers: list[str] = []
    n_prompts = n_skipped = 0
    for _, r in fin.iterrows():
        raw = str(r.get("code", "") or "").strip()
        if not raw or raw == "nan":
            continue
        code6 = raw.split(".")[0].zfill(6)
        if (scan_dir / "details" / f"{code6}.md").exists():
            n_skipped += 1                          # ♻️ 复用卡已就位:不重拉不派发
            continue
        ticker = normalize_symbol(code6)            # 6 位码 → .SS/.SZ/.BJ(单一后缀口径)
        tickers.append(ticker)
        prompt = "\n".join([
            f"# L4 派发 prompt — {code6} {r.get('name', '')}(确定性落稿;编排以此为派发正文)",
            "",
            shared or "_(共享指令稿缺:`_l4_shared_instructions.md` 未落——按 stock-research lite-playbook 执行)_",
            "",
            "---",
            "",
            compose_funnel_brief(code6, scan_dir).rstrip(),
            "",
            "---",
            f"- slim 数据:`context/{ticker}_{date}_slim.md`(P1+ 逐段读;**>10KB 才可信**,≈4.8KB=NO_DATA 须重拉)",
            f"- 决策卡写往:`context/scan/{date}/details/{code6}.md`",
            ""])
        (scan_dir / f"_l4_prompt_{code6}.md").write_text(prompt, encoding="utf-8")
        n_prompts += 1
    (scan_dir / "_harvest_list.txt").write_text(
        "\n".join(tickers) + ("\n" if tickers else ""), encoding="utf-8")
    return {"n_prompts": n_prompts, "n_skipped": n_skipped, "tickers": tickers}


def dispatch_plan(date: str, root: Path | str | None = None) -> dict:
    """L4 派发感知 TTL 复用/carryover(确定性,零 LLM;复审 task-4-review.md Important #1 修复)。

    `write_dispatch_pack` 对已有 `details/<code>.md` 的复用/carryover 码 skip(不写
    `_l4_prompt_<code>.md`),但工作流原先对**全部** finalists 无条件派卡 —— 复用码那份
    prompt 文件根本不存在,派了个读空文件的 Opus(抵消 TTL 复用省下的成本,复用卡评级也
    没并回 `cards`)。本函数按同一判据(`_l4_prompt_<code>.md` 是否存在)把 finalists
    分两路:`dispatch`(需新派 Opus)与 `reused`(已就位卡,直接 `parse_rating` 解评级
    并回,不再派 subagent)。两个标志都缺(异常态)→ 归 `dispatch`,兜底走正常派发。

    返回 `{"dispatch": [code6...], "reused": [{"code","rating"}...]}`。
    """
    base = Path(root) if root else Path("context/scan")
    scan_dir = base / date
    fp = scan_dir / "finalists.csv"
    dispatch: list[str] = []
    reused: list[dict] = []
    if not fp.exists():
        return {"dispatch": dispatch, "reused": reused}
    from autoresearch.agents.utils.rating import parse_rating  # 延迟导入,保持本模块轻量
    fin = pd.read_csv(fp, dtype={"code": str})
    for _, r in fin.iterrows():
        raw = str(r.get("code", "") or "").strip()
        if not raw or raw == "nan":
            continue
        code6 = raw.split(".")[0].zfill(6)
        if (scan_dir / f"_l4_prompt_{code6}.md").exists():
            dispatch.append(code6)
            continue
        details = scan_dir / "details" / f"{code6}.md"
        if details.exists():
            reused.append({"code": code6, "rating": parse_rating(details.read_text(encoding="utf-8"))})
        else:
            dispatch.append(code6)   # 两者皆无(异常):兜底走正常派发,不静默丢票
    return {"dispatch": dispatch, "reused": reused}


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
                    net = float(r.get("net_buy") or 0)
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


def _default_harvest_slim(ticker: str, date: str, ctx_root: Path) -> Path:
    import subprocess
    import sys

    subprocess.run(
        [sys.executable, "-m", "autoresearch.analyze.harvest", ticker, date, "stock", "--slim"],
        check=False)
    return ctx_root / f"{ticker}_{date}_slim.md"


def harvest_slim_batch(date: str, root: Path | None = None, min_bytes: int = 10_240,
                       retries: int = 1, harvest_fn=None, ctx_root: Path | None = None) -> dict:
    """按 _harvest_list.txt 批量 harvest slim,**失败响亮**(修 603799 静默失败坑 = GATE 3)。

    07-06 教训:slim >10KB 才可信。offender 重试 `retries` 次仍小/异常/含 .SH → 记失败。
    harvest_fn(ticker, date)->Path 可注入(测试用),默认 shell 到 analyze.harvest --slim。
    """
    base = Path(root) if root else Path("context/scan")
    scan_dir = base / date
    ctx = ctx_root or Path("context")
    tickers = [t for t in (scan_dir / "_harvest_list.txt").read_text(encoding="utf-8").split() if t]
    hv = harvest_fn or (lambda t, dt: _default_harvest_slim(t, dt, ctx))
    failures = []
    for t in tickers:
        if ".SH" in t:                                    # 归一漏网(GATE 3 防线)
            failures.append({"ticker": t, "bytes": -1, "why": ".SH 未归一"})
            continue
        size = 0
        for _ in range(retries + 1):
            try:
                p = hv(t, date)
                size = p.stat().st_size if p and Path(p).exists() else 0
            except Exception:                             # noqa: BLE001
                size = 0
            if size >= min_bytes:
                break
        if size < min_bytes:
            failures.append({"ticker": t, "bytes": int(size), "why": f"<{min_bytes}B"})
    return {"ok": not failures, "n": len(tickers), "failures": failures}


def main(argv: list[str] | None = None) -> int:
    import argparse
    ap = argparse.ArgumentParser(description="L4 确定性件 CLI(派发包落稿/质押预旗,零 LLM)")
    ap.add_argument("cmd", choices=["prompts", "pledge", "seats", "harvest-slim", "dispatch-plan"],
                    help="prompts = 写 _harvest_list.txt + _l4_prompt_<code>.md;"
                         "pledge = finalists 批量质押 → pledge.csv(简报自动带 ⚠质押旗);"
                         "seats = finalists 龙虎榜席位聚合 → seats.csv(_seat_mark 注简报);"
                         "harvest-slim = 按 _harvest_list.txt 批量 harvest slim;"
                         "dispatch-plan = 派发感知 TTL 复用/carryover(dispatch/reused 分流)")
    ap.add_argument("date", help="scan 日 YYYY-MM-DD")
    ap.add_argument("--root", default=None, help="scan 根目录(默认 context/scan)")
    args = ap.parse_args(argv)
    if args.cmd == "dispatch-plan":
        import json
        print(json.dumps(dispatch_plan(args.date, root=args.root), ensure_ascii=False))
        return 0
    if args.cmd == "harvest-slim":
        import json
        res = harvest_slim_batch(args.date)
        print(json.dumps({"ok": res["ok"],
                          "reason": ("ok" if res["ok"]
                                     else f"{len(res['failures'])}/{res['n']} slim 失败:"
                                          + ", ".join(f"{f['ticker']}({f['bytes']}B)"
                                                      for f in res["failures"])),
                          "failures": res["failures"]}, ensure_ascii=False))
        return 0 if res["ok"] else 1
    if args.cmd == "pledge":
        df = fetch_pledge(Path("context/scan") / args.date)
        n_flag = int((pd.to_numeric(df.get("pledge_ratio"), errors="coerce") > 20).sum()) if len(df) else 0
        print(f"[l4_card pledge] {len(df)} 票落 pledge.csv(>20% 偏高/红旗 {n_flag} 票);"
              f"派发前跑,简报自动注 ⚠质押旗")
        return 0
    if args.cmd == "seats":
        df = fetch_seats(Path("context/scan") / args.date)
        n_inst = int((df["inst_net_wan"] > 0).sum()) if len(df) else 0
        print(f"[l4_card seats] {len(df)} 票落 seats.csv(机构净买>0 {n_inst} 票=Phase A 反指候选)")
        return 0
    res = write_dispatch_pack(Path("context/scan") / args.date)
    print(f"[l4_card prompts] {res['n_prompts']} 份 prompt + _harvest_list({len(res['tickers'])} 票,"
          f"已归一 yfinance 后缀);跳过已有卡 {res['n_skipped']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
