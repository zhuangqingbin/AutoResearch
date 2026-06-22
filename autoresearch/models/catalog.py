#!/usr/bin/env python3
"""模型目录 —— 全 Qlib zoo 在册 + 迁移状态(ported / pending-*)。

design: docs/specs/2026-06-22-autoresearch-arch-redesign-design.md §C⑥(全 zoo 迁移)。

每个条目 `{kind, feature_set, status, ref}`:
  * kind        : registry key(已 ported 的 = `@register(kind)` 注册名;pending 的 = 计划名)。
  * feature_set : 该模型吃哪份命名视图(core 横截面 / seq 滚动窗 / graph 关系图)。
  * status      : ported(本 phase 实交付,native→Model 接口,无 qlib 运行时)
                  / pending-torch(torch 表格,随 torch 依赖迁入)
                  / pending-seq(序列模型,需 seq DataHandler + torch)
                  / pending-graph(图模型,需 graph 层 + torch)。
  * ref         : Qlib 架构/超参参考(原生实现的蓝本)。

前置(feature_set 层 + torch)满足即逐个把 pending 迁为 ported,统一进 Trainer。
"""
from __future__ import annotations

MODELS: dict[str, dict] = {
    # ── Phase 1 实交付:core 横截面,无 torch(linear/lgbm/xgb/cat/dbl) ──
    "linear": {"kind": "linear", "feature_set": "core", "status": "ported",
               "ref": "qlib.contrib.model.linear.LinearModel"},
    "lgbm": {"kind": "lgbm", "feature_set": "core", "status": "ported",
             "ref": "qlib.contrib.model.gbdt.LGBModel"},
    "xgb": {"kind": "xgb", "feature_set": "core", "status": "ported",
            "ref": "qlib.contrib.model.xgboost.XGBModel"},
    "catboost": {"kind": "catboost", "feature_set": "core", "status": "ported",
                 "ref": "qlib.contrib.model.catboost_model.CatBoostModel"},
    "double_ensemble": {"kind": "double_ensemble", "feature_set": "core", "status": "ported",
                        "ref": "qlib.contrib.model.double_ensemble.DEnsembleModel"},

    # ── torch 表格(core,已迁:native torch → Model 接口,走统一 Trainer) ──
    "mlp": {"kind": "mlp", "feature_set": "core", "status": "ported",
            "ref": "qlib.contrib.model.pytorch_nn.DNNModelPytorch"},
    "tabnet": {"kind": "tabnet", "feature_set": "core", "status": "ported",
               "ref": "qlib.contrib.model.pytorch_tabnet.TabnetModel"},

    # ── 序列(seq 滚动窗 DataHandler + torch);全 8 个已迁(rnn/tcn/attn) ──
    "lstm": {"kind": "lstm", "feature_set": "seq", "status": "ported",
             "ref": "qlib.contrib.model.pytorch_lstm.LSTMModel"},
    "gru": {"kind": "gru", "feature_set": "seq", "status": "ported",
            "ref": "qlib.contrib.model.pytorch_gru.GRUModel"},
    "alstm": {"kind": "alstm", "feature_set": "seq", "status": "ported",
              "ref": "qlib.contrib.model.pytorch_alstm.ALSTMModel"},
    "tcn": {"kind": "tcn", "feature_set": "seq", "status": "ported",
            "ref": "qlib.contrib.model.pytorch_tcn.TCNModel"},
    "transformer": {"kind": "transformer", "feature_set": "seq", "status": "ported",
                    "ref": "qlib.contrib.model.pytorch_transformer.TransformerModel"},
    "localformer": {"kind": "localformer", "feature_set": "seq", "status": "ported",
                    "ref": "qlib.contrib.model.pytorch_localformer.LocalformerModel"},
    "tft": {"kind": "tft", "feature_set": "seq", "status": "ported",
            "ref": "qlib.contrib.model.pytorch_tft.TFTModel"},
    "tra": {"kind": "tra", "feature_set": "seq", "status": "ported",
            "ref": "qlib.contrib.model.pytorch_tra.TRAModel"},

    # ── 图(graph 行业邻接 + torch);全 3 个已迁(graph.py:GATs/HIST/IGMTF) ──
    "gats": {"kind": "gats", "feature_set": "graph", "status": "ported",
             "ref": "qlib.contrib.model.pytorch_gats.GATModel"},
    "hist": {"kind": "hist", "feature_set": "graph", "status": "ported",
             "ref": "qlib.contrib.model.pytorch_hist.HISTModel"},
    "igmtf": {"kind": "igmtf", "feature_set": "graph", "status": "ported",
              "ref": "qlib.contrib.model.pytorch_igmtf.IGMTFModel"},
    # SFM/KRNN 实为序列模型(状态-频率记忆 / CNN+RNN),非图 → 归 seq,已迁(rnn.py)。
    "sfm": {"kind": "sfm", "feature_set": "seq", "status": "ported",
            "ref": "qlib.contrib.model.pytorch_sfm.SFMModel"},
    "krnn": {"kind": "krnn", "feature_set": "seq", "status": "ported",
             "ref": "qlib.contrib.model.pytorch_krnn.KRNNModel"},
}


def by_status(status: str) -> list[str]:
    """某状态下的全部模型名(ported / pending-torch / pending-seq / pending-graph)。"""
    return sorted(k for k, v in MODELS.items() if v["status"] == status)


def ported() -> list[str]:
    """已 ported(走统一 Trainer)的模型名。"""
    return by_status("ported")
