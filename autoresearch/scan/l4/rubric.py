"""L4 rating rubric and progressive-depth guard."""
from __future__ import annotations

from autoresearch.common.card_decision import _norm_dim as _norm_dim
from autoresearch.contracts.agent_output import OW_GATES, RUBRIC_DIMENSIONS

# 2026-09-07(D1):六维与三门词表下沉 `contracts.agent_output`,这里是同对象引用;评分公式不动。
_RUBRIC_DIMS = RUBRIC_DIMENSIONS
_DIM_SCORE = {"强": 1, "中": 0, "弱": -1}
_OW_GATES = OW_GATES


def rubric_rating(dims: dict, gates: dict, *, bands: dict | None = None) -> tuple[str, str]:
    from autoresearch.common.card_decision import rubric_rating as shared_rating
    return shared_rating(dims, gates, bands=rubric_cfg()["rating_bands"] if bands is None else bands)


def validate_card_decision(card: dict, *, bands: dict | None = None) -> tuple[str, str]:
    from autoresearch.common.card_decision import validate_card_decision as shared_validation
    return shared_validation(card, bands=rubric_cfg()["rating_bands"] if bands is None else bands)

RATING_BANDS_DEFAULT: dict = {"Buy": 4, "Overweight": 2, "Hold": -1, "Underweight": -3}
FORCE_FULL_DEFAULT: dict = {"conviction_min": 70.0, "channels_min": 4}


def rubric_cfg(cfg: dict | None = None) -> dict:
    """`scan_config.l4.rubric` → 净分档位 / 强制满卡阈(缺键 = 上面两个缺省字典)。"""
    from autoresearch.scan.user_config import knob
    user = knob("l4", "rubric", None, {}, cfg) or {}
    if not isinstance(user, dict):
        user = {}
    return {"rating_bands": {**RATING_BANDS_DEFAULT, **(user.get("rating_bands") or {})},
            "force_full": {**FORCE_FULL_DEFAULT, **(user.get("force_full") or {})}}


def force_full_card(priors: dict, *, conv_min: float | None = None, channels_min: int | None = None) -> bool:
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
    ff = rubric_cfg()["force_full"]
    conv_min = float(ff["conviction_min"]) if conv_min is None else conv_min
    channels_min = int(ff["channels_min"]) if channels_min is None else channels_min
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
