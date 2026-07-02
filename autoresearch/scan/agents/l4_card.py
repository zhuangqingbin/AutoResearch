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
        f"- **资金/技术(先验)**:主力净占比 {_g(l1,'main_net_ratio')}·cmf20 {_g(l1,'cmf_20')}·"
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
    brief = "\n".join(lines) + "\n"
    ctx = _market_ctx(base, l3.get("industry") or l3.get("sector") or l1.get("industry"))
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
