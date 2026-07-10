#!/usr/bin/env python3
"""L3.5 可插拔闸 registry —— `@gate(name)` 注册 + `build(name)` 取函数 + `registered_gates()`。

design: docs/specs/2026-07-11-recall-gate-pinned-config-design.md §3
(docs/plans/2026-07-11-l35-gate-backtest-plan.md Task 1)。

L3 精排出 ~20-28 只 judged 候选后、L4 出卡前插一层**确定性**闸,把「L4 出量 6~10」从
拍脑袋改成可插拔、可回测的策略。镜像 `recall/registry.py` 的注册模式:一个闸 = 一个纯函数
(无 I/O,只认 judged 帧 + regime + exempt + params),加一路闸 = 写函数 + `@gate(...)`,
不动上游 merge / 下游 L4 dispatch。

统一签名:`gate_fn(judged: pd.DataFrame, *, regime: str, exempt: set[str], params: dict) -> GateResult`。

**exempt 契约**(与 A3-T4 pinned/carryover/watchlist 直通车对齐,三策略统一遵守):exempt 集
**恒直通且不占配额**——conviction 再低、cap 再满,exempt 里的票必进 `picked`,也绝不出现在
`cut` 里、不挤占其它票的名额。

**预算旗收编为输入**:旧 `menu.l4_budget(scan_dir, ...)` 读 scan_dir 算出一个整数(带 I/O,
纯函数层不能直接调)。本层不引入对 menu 的依赖——调用方(Task 4 接线)自己算好
`n, _ = l4_budget(scan_dir)` 再把 `n` 塞进 `params["l4_budget"]`;`conviction_floor_quota`
只需要 `min(regime_cap, params.l4_budget)`,两套机制收敛成一个。
"""
from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

_REGISTRY: dict[str, object] = {}

# regime 分档上限默认值(design §3):trend 最松、risk_off 最紧。regime 未知/缺失时按
# classify_regime 自身"安全退化 range"的同一惯例落到 range 档。
_DEFAULT_CAPS: dict[str, int] = {"trend": 10, "range": 8, "risk_off": 6}


@dataclass(frozen=True)
class GateResult:
    """闸的返回值。`picked`:入选 code 列表(含 exempt)。
    `cut`:未入选诊断行,每行 `{code, rank, conviction, lane, reason}`(exempt 不入此列表)。
    """

    picked: list[str]
    cut: list[dict]


def gate(name: str):
    """函数装饰器:把一个闸函数登记进 registry(重名报错,不静默顶替)。"""

    def deco(fn):
        if name in _REGISTRY:
            raise KeyError(f"gate {name!r} already registered to {_REGISTRY[name]!r}")
        _REGISTRY[name] = fn
        return fn

    return deco


def build(name: str):
    """取一个闸函数(签名见模块 docstring)。"""
    try:
        return _REGISTRY[name]
    except KeyError:
        raise KeyError(f"unknown gate {name!r}: registered={sorted(_REGISTRY)}") from None


def registered_gates() -> list[str]:
    """已注册的全部闸名(排序,确定性)。"""
    return sorted(_REGISTRY)


def _prepare(judged: pd.DataFrame, exempt: set[str] | None) -> pd.DataFrame:
    """judged → 补 `_conviction`(数值化)/`_lane`(缺列降级为"")/`_exempt`(code 命中)/
    `_rank`(conviction 降序 1-based,唯一的"L3 排名"定义,三策略共用)。"""
    df = judged.reset_index(drop=True).copy()
    df["code"] = df["code"].astype(str) if "code" in df.columns else ""
    df["_conviction"] = (pd.to_numeric(df["conviction"], errors="coerce")
                         if "conviction" in df.columns else float("nan"))
    df["_lane"] = df["lane"].fillna("").astype(str) if "lane" in df.columns else ""
    df["_exempt"] = df["code"].isin(exempt or set())
    order = df.sort_values("_conviction", ascending=False, kind="stable", na_position="last").index
    df["_rank"] = pd.Series(range(1, len(df) + 1), index=order).reindex(df.index)
    return df


def _cut_row(row: pd.Series, reason: str) -> dict:
    return {"code": row["code"], "rank": int(row["_rank"]), "conviction": row.get("conviction"),
            "lane": row.get("lane"), "reason": reason}


@gate("passthrough")
def passthrough(judged: pd.DataFrame, *, regime: str, exempt: set[str], params: dict) -> GateResult:
    """默认闸 = parity:全放行,`cut` 恒空。配置文件缺失/未指定闸名时的现状行为。"""
    if not len(judged):
        return GateResult(picked=[], cut=[])
    codes = judged["code"].astype(str).tolist() if "code" in judged.columns else []
    return GateResult(picked=codes, cut=[])


@gate("topk_simple")
def topk_simple(judged: pd.DataFrame, *, regime: str, exempt: set[str], params: dict) -> GateResult:
    """按 conviction 排名(=『L3 排名』)截 `params['k']`(缺省=不截,全放行)。

    对照基线:无 lane 多样性、无 regime 感知。exempt 恒入且不占 k 名额、不进 cut——
    与 `conviction_floor_quota` 同一契约。`k` 强制夹到 ≥0:`iloc[:k]` 直喂负 k 是
    Python 反向切片语义(去尾非清零),不是本函数想要的语义。
    """
    if not len(judged):
        return GateResult(picked=[], cut=[])
    df = _prepare(judged, exempt)
    k = max(int(params.get("k", len(df))), 0)
    exempt_df = df[df["_exempt"]]
    rest = df[~df["_exempt"]].sort_values("_rank")
    keep, dropped = rest.iloc[:k], rest.iloc[k:]
    picked = exempt_df["code"].tolist() + keep["code"].tolist()
    cut = [_cut_row(r, "topk_cut") for _, r in dropped.iterrows()]
    return GateResult(picked=picked, cut=cut)


def _select_with_lane_floor(eligible: pd.DataFrame, cap: int, lane_min: int) -> list[int]:
    """cap 内先按 lane 多样性预留(每 lane 前 `lane_min` 名),再按 conviction 排名回填补满。

    预留顺序:lane 按各自最强候选的 `_rank` 从小到大处理(`_rank` 越小 = conviction 越高
    = 越强;即"最强代表"的 lane 先预留)——cap 挤不下全部 lane×lane_min 时,优先保住
    "确有强代表"的 lane,而非任意/字母序(挤压场景下这条顺序规则本身就是产品判断,非纯
    技术细节)。回填阶段无视 lane,纯按 conviction 排名补满,直到凑够 cap 或吃光 eligible。
    """
    have_lane = eligible[eligible["_lane"] != ""]
    lane_order = have_lane.groupby("_lane")["_rank"].min().sort_values().index.tolist()

    selected: list[int] = []
    selected_set: set[int] = set()

    def _take(idx: int) -> None:
        selected.append(idx)
        selected_set.add(idx)

    for ln in lane_order:                                   # ① lane 多样性预留
        if len(selected) >= cap:
            break
        sub = have_lane[have_lane["_lane"] == ln].sort_values("_rank")
        for idx in sub.index[:lane_min]:
            if len(selected) >= cap:
                break
            if idx not in selected_set:
                _take(idx)

    for idx in eligible.sort_values("_rank").index:          # ② merit 回填(补满 cap)
        if len(selected) >= cap:
            break
        if idx not in selected_set:
            _take(idx)

    return selected


@gate("conviction_floor_quota")
def conviction_floor_quota(judged: pd.DataFrame, *, regime: str, exempt: set[str],
                           params: dict) -> GateResult:
    """conviction 地板 + lane 多样性配额(每主 lane ≥`lane_min`)+ regime 分档上限,
    再与 `params['l4_budget']`(旧 `menu.l4_budget(scan_dir)` 算出的数,由调用方传入——
    本层是纯函数,不读 scan_dir)取 min。exempt 恒直通,不占此处的地板/配额/cap。

    参数(均可缺省,`params={}` 时:floor=0/lane_min=1/caps=默认三档/l4_budget=不限):
      - `floor`:conviction 地板(数值域 0-100;未给 → 0 = 不筛,与"缺 conviction 列
        视为并列"的降级口径自洽,不臆造业务阈值);
      - `lane_min`:每主 lane 至少保留几只(默认 1);
      - `caps`:`{trend,range,risk_off} -> int`(默认 §3 数值);regime 未命中 → 落
        `caps["range"]`(不存在则硬编码 8) —— 与 `classify_regime` 的"安全退化 range"惯例一致;
      - `l4_budget`:外部算好的预算数(可选);给了就 `min(regime_cap, l4_budget)`。
    """
    if not len(judged):
        return GateResult(picked=[], cut=[])
    df = _prepare(judged, exempt)
    floor = float(params.get("floor", 0))
    lane_min = int(params.get("lane_min", 1))
    caps = params.get("caps", _DEFAULT_CAPS)
    cap = caps.get(regime, caps.get("range", 8))
    budget = params.get("l4_budget")
    if budget is not None:
        cap = min(cap, int(budget))

    exempt_df = df[df["_exempt"]]
    rest = df[~df["_exempt"]]
    below_mask = rest["_conviction"].fillna(0.0) < floor      # 缺列/缺值 → 视作 0(域内最弱,非 -inf 惩罚)
    below, eligible = rest[below_mask], rest[~below_mask].sort_values("_rank")

    cut = [_cut_row(r, f"conviction<floor({floor:g})") for _, r in below.iterrows()]

    if len(eligible) <= cap:
        picked_rows = eligible
    else:
        keep_idx = _select_with_lane_floor(eligible, cap, lane_min)
        picked_rows = eligible.loc[keep_idx]
        for idx in eligible.index.difference(keep_idx):
            cut.append(_cut_row(eligible.loc[idx], "cap_quota"))

    picked = exempt_df["code"].tolist() + picked_rows["code"].tolist()
    return GateResult(picked=picked, cut=cut)
