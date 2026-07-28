"""L4 rating rubric and progressive-depth guard."""
from __future__ import annotations

from autoresearch.agents.utils.rating import RATINGS_5_TIER

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
