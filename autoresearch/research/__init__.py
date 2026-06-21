"""autoresearch.research —— 实证研究层(因子 IC 校准 / GBDT 训练)。

`factor_lab`:scan-market L1 打分逻辑的点对点实证验证 + 权重校准(weights.json)+
L2 GBDT 横截面排序训练(gbdt_model.pkl)。供 scan 编排(`autoresearch.scan.universe`)
与闭环复盘(`autoresearch.learning.retro`)调用。
"""
