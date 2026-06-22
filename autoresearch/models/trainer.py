#!/usr/bin/env python3
"""Trainer —— 所有粗排模型同一条训练/评估/晋升流水线(统一训练架构)。

design: docs/specs/2026-06-22-autoresearch-arch-redesign-design.md §C④/⑤。

把 factor_lab.train_gbdt 里散落的"物化面板 → 每日 rank-norm 标签 → 时序切(无前视)→ oos
rank-IC → champion 门"提成 **kind-agnostic** 的一条流水线:任何 Model 子类只要吐横截面分,就
自动走同口径评估 + champion–challenger 晋升。
  * label  : T+1 开到开(fwd_1_oo)每日横截面 rank(pct=True)(= factor_lab CSRankNorm 标签)。
  * 切分   : 末尾 valid_dates 个成型日 = oos/valid,其余 = train(时序、无前视)。
  * 评估   : evaluate = 每日 Spearman(predict, 实现 fwd) 跨日平均(rank-IC;镜像 _rank_ic_by_date)。
  * 晋升   : promote_if_better = challenger oos > champion(或无 champion)→ True(泛化 beats_linear)。
  * champion store: models/store/<name>/<version>.pkl + champion.json 指针。
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import pandas as pd

from autoresearch.data.handler import DataHandler
from autoresearch.models.base import Dataset, FitReport, Model
from autoresearch.models.registry import ModelConfig, build

STORE_ROOT = Path("models/store")


# ───────────────────────── rank-IC 原语(镜像 factor_lab) ─────────────────────────


def _spearman(a: pd.Series, b: pd.Series, min_n: int = 30) -> float:
    """横截面 Spearman(rank 相关);有效样本 < min_n → NaN。镜像 factor_lab._spearman。"""
    a = pd.Series(np.asarray(a, dtype=float))
    b = pd.Series(np.asarray(b, dtype=float))
    m = a.notna() & b.notna()
    if int(m.sum()) < min_n:
        return float("nan")
    return float(a[m].rank().corr(b[m].rank()))


def _rank_ic_by_date(score: pd.Series, fwd: pd.Series, date: pd.Series) -> float:
    """跨日平均 rank-IC(每日横截面 Spearman(score, 实现收益))。镜像 factor_lab._rank_ic_by_date。"""
    d = pd.DataFrame({"s": np.asarray(score), "f": np.asarray(fwd), "d": np.asarray(date)})
    ics = []
    for _, g in d.groupby("d"):
        ic = _spearman(g["s"], g["f"])
        if not np.isnan(ic):
            ics.append(ic)
    return float(np.mean(ics)) if ics else float("nan")


# ───────────────────────── 训练产物 ─────────────────────────


@dataclass
class TrainedModel:
    """一次 train 的产物:模型本体 + FitReport + oos rank-IC + 元信息。"""

    model: Model
    report: FitReport
    oos_rank_ic: float
    meta: dict = field(default_factory=dict)


# ───────────────────────── Trainer ─────────────────────────


class Trainer:
    """统一训练器:物化 → 切分 → fit → oos rank-IC → champion 门。kind-agnostic。"""

    def __init__(self, handler: DataHandler, label: str = "fwd_1_oo", valid_dates: int = 12):
        self.handler = handler
        self.label = label
        self.valid_dates = valid_dates

    # ---- 标签 ----
    @staticmethod
    def _cs_rank_label(panel: pd.DataFrame, label: str) -> pd.Series:
        """每个成型日横截面 rank(pct=True) 的 label 列(学相对排序,免 regime 水平位移)。"""
        return panel.groupby("date")[label].transform(lambda s: pd.to_numeric(s, errors="coerce").rank(pct=True))

    # ---- 训练 ----
    def train(self, cfg: ModelConfig, dates: list[str], *, price_dates: list[str] | None = None,
              cap_floor: float = 30.0, fwd: int = 10) -> TrainedModel:
        """物化面板 → 时序切 → fit(train) → oos rank-IC(valid)。返回 TrainedModel。"""
        panel = self.handler.materialize(dates, feature_set=cfg.feature_set, kind=cfg.feature_set,
                                         price_dates=price_dates, cap_floor=cap_floor, fwd=fwd)
        if panel.empty:
            raise ValueError("Trainer.train: materialized panel is empty (no usable formation dates)")
        # 只用标签非缺的行(无前瞻收益的行无法训练/评估)
        panel = panel[pd.to_numeric(panel[self.label], errors="coerce").notna()].reset_index(drop=True)
        if panel.empty:
            raise ValueError(f"Trainer.train: no rows with non-null label {self.label!r}")
        panel["__y"] = self._cs_rank_label(panel, self.label)

        udates = sorted(panel["date"].unique())
        n_val = min(self.valid_dates, max(1, len(udates) // 4)) if len(udates) > 1 else 0
        val_dates = set(udates[-n_val:]) if n_val else set()
        is_val = panel["date"].isin(val_dates).to_numpy()
        train_panel = panel[~is_val] if is_val.any() else panel
        valid_panel = panel[is_val] if is_val.any() else panel

        ds = Dataset(X=train_panel.reset_index(drop=True),
                     y=train_panel["__y"].reset_index(drop=True),
                     dates=train_panel["date"].reset_index(drop=True))
        model = build(cfg)
        report: FitReport = model.fit(ds)
        oos_ic = self.evaluate(model, valid_panel)
        meta = {"kind": cfg.kind, "feature_set": cfg.feature_set, "label": self.label,
                "n_dates": len(udates), "valid_dates": int(n_val),
                "val_date_range": [min(val_dates), max(val_dates)] if val_dates else None}
        return TrainedModel(model=model, report=report, oos_rank_ic=oos_ic, meta=meta)

    # ---- 评估 ----
    def evaluate(self, model: Model, valid_panel: pd.DataFrame) -> float:
        """oos rank-IC:每日 Spearman(model.predict(X), 实现 fwd) 跨日平均。镜像 factor_lab。"""
        if valid_panel.empty:
            return float("nan")
        scores = model.predict(valid_panel)
        return _rank_ic_by_date(scores, valid_panel[self.label], valid_panel["date"])

    # ---- champion 门 ----
    @staticmethod
    def promote_if_better(challenger: TrainedModel, champion_ic: float | None) -> bool:
        """challenger oos rank-IC 严格 > 现任 champion(或无 champion)→ True。泛化 beats_linear。

        challenger IC 为 NaN → 不晋升(无法证明更好)。
        """
        ic = challenger.oos_rank_ic
        if ic is None or (isinstance(ic, float) and np.isnan(ic)):
            return False
        if champion_ic is None or (isinstance(champion_ic, float) and np.isnan(champion_ic)):
            return True
        return ic > champion_ic


# ───────────────────────── champion store ─────────────────────────


def _name_dir(name: str, root: Path = STORE_ROOT) -> Path:
    return Path(root) / name


def save_champion(name: str, trained: TrainedModel, version: str, *,
                  root: Path = STORE_ROOT) -> Path:
    """把 trained.model 存为 models/store/<name>/<version>.pkl,并把 champion.json 指向它。

    champion.json = {"version", "oos_rank_ic", "kind", "feature_set", "meta"}。返回 pkl 路径。
    """
    d = _name_dir(name, root)
    d.mkdir(parents=True, exist_ok=True)
    pkl = d / f"{version}.pkl"
    trained.model.save(pkl)
    pointer = {"version": version, "oos_rank_ic": trained.oos_rank_ic,
               "kind": trained.meta.get("kind"), "feature_set": trained.meta.get("feature_set"),
               "meta": trained.meta}
    (d / "champion.json").write_text(json.dumps(pointer, ensure_ascii=False, indent=2),
                                     encoding="utf-8")
    return pkl


def _champion_pointer(name: str, root: Path = STORE_ROOT) -> dict | None:
    p = _name_dir(name, root) / "champion.json"
    if not p.exists():
        return None
    return json.loads(p.read_text(encoding="utf-8"))


def champion_ic(name: str, *, root: Path = STORE_ROOT) -> float | None:
    """现任 champion 的 oos rank-IC(无 champion → None)。"""
    ptr = _champion_pointer(name, root)
    if ptr is None:
        return None
    ic = ptr.get("oos_rank_ic")
    return None if ic is None else float(ic)


def load_champion(name: str, model_cls: type[Model], *, root: Path = STORE_ROOT) -> Model | None:
    """加载现任 champion 模型(用 model_cls.load 反序列化);无 champion → None。"""
    ptr = _champion_pointer(name, root)
    if ptr is None:
        return None
    pkl = _name_dir(name, root) / f"{ptr['version']}.pkl"
    if not pkl.exists():
        return None
    return model_cls.load(pkl)


def load_champion_any(name: str, *, root: Path | None = None) -> Model | None:
    """按 champion.json 的 `kind` 用 registry 解析模型类反序列化(支持任意 zoo kind)。

    无 champion / pkl 缺失 / kind 未注册 / 反序列化失败 → None(调用方回落线性)。
    与 load_champion 的区别:不需调用方预知模型类——zoo 晋升的可能是任意 kind(lgbm/mlp/…)。
    root=None → 动态读模块级 STORE_ROOT(测试可 monkeypatch 隔离 champion store)。
    """
    root = root if root is not None else STORE_ROOT
    ptr = _champion_pointer(name, root)
    if ptr is None:
        return None
    pkl = _name_dir(name, root) / f"{ptr['version']}.pkl"
    if not pkl.exists():
        return None
    from autoresearch.models.registry import _REGISTRY
    cls = _REGISTRY.get(ptr.get("kind") or "")
    if cls is None:
        return None
    try:
        return cls.load(pkl)
    except Exception:  # noqa: BLE001 — 反序列化失败 → 调用方回落线性
        return None
