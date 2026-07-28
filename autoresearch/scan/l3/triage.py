"""L3 deterministic pass-1 triage."""
from __future__ import annotations

import pandas as pd


def triage_l2_for_l3(df: pd.DataFrame, target: int = 60) -> tuple[pd.DataFrame, pd.DataFrame]:
    """pass1 确定性分诊(零 LLM):L2 ~200 行 → kept(进 pass2/l3-rank 深比较,~target 行)+
    cut(影子,写 `_l3_pass1_cut.csv`,供 attribution 证明分诊没吃掉赢家)。design: plan
    2026-07-12-l3-merge-plan.md Task 1。

    `df` 是 `load_l3_input(date)` 的输出(L2_gbdt_top200.csv + 证据/情感 merge 后的帧),
    **不是** `merge_l3_finalists_v2` 消费的 L3 judged 输出——后者才有 `lane` 列(L3 agent
    判断后才产生的字段),L2 输入帧本身没有 `lane`,故下面①③的判据全部改用 L2 真实
    provenance 列(`recall_channels`/`n_channels`,均在真实 `L2_gbdt_top200.csv` 里实测
    存在;`lenses` 是 L3 judged 输出列,L2 输入没有,不适用,略过)。

    kept 规则(按序 union、天然去重;结果按 `gbdt_score` 降序——缺列退化 `composite`——
    决定谁留谁走,超 target 截尾,截掉的并入 cut):

    ① pinned 全入:`pinned` 布尔列(`universe._inject_pinned_l1` 全程带下来,presence-gated,
       多数无保送的日子该列不存在)为真;该列不存在时退化检查 `recall_channels` 字面等于
       `"pinned"`(L1 强注新增行的哨兵值,见 `_inject_pinned_l1`)。两者都缺列 → 该规则
       贡献 0 行,不报错。
    ② 多路共振全入:`n_channels >= 3`(真实列,直接可用)。列缺失(如 `recall_mode="composite"`
       的 L2,无 provenance 列)→ 跳过本规则,不报错。
    ③ healthy lane 全入:`recall_channels` 按 `"|"` 拆分后的集合包含 `"healthy"`(已注册召回
       通道名,见 `recall/channels.py`;集合 membership 判定——镜像 `l2_stratify._style_masks`
       的写法,**不是** `_row_lane` 的"仅取首通道"渲染判据,那是防重复渲染用的、语义不同)。
       `recall_channels` 列缺失 → 跳过,不报错。
    ④ 剩余名额(target 减①②③后的 kept 数,若已 ≥target 则本规则不运行)按各召回通道内部
       名次(`gbdt_score` 降序,缺列退化 `composite`)轮询填满:每轮依次(通道名升序,确定性)
       从每个通道取队列里名次最高且未被选中的一行,直至填满 target 或全部通道队列耗尽——
       "K 自适应":不是固定分配每通道几个名额,是排到没有再转下一个通道。通道队列只统计
       **真实召回通道名**(`recall_channels` 按 `"|"` 拆分后排除 `""`/`"(backfill)"`/`"pinned"`
       三个哨兵 token,那两个不是通道名);轮询耗尽仍未填满 target 且还有剩余行(如
       `(backfill)` 补位票、或整列缺失 `recall_channels`)→ 末尾直接按 `gbdt_score`/`composite`
       分再补满,直至 target 或行数耗尽——这是"填满到 target"字面要求的延伸,不是独立的
       第 5 条规则。

    `cut = df − kept`(按原始行序稳定输出,不重排;`kept`/`cut` 都保留 `df` 的**全部原始列**,
    不裁列——`_l3_pass1_cut.csv`/下游 attribution 都可能要用到里面的列)。

    边界:`df` 为空 → 两个都空。`target >= len(df)` → kept=全量,cut=空。
    """
    if df.empty:
        return df.copy(), df.copy()

    d = df.reset_index(drop=True).copy()
    if "code" in d.columns:
        d["code"] = d["code"].astype(str).str.zfill(6)

    score_col = "gbdt_score" if "gbdt_score" in d.columns else ("composite" if "composite" in d.columns else None)
    order = (pd.to_numeric(d[score_col], errors="coerce").fillna(-1e18)
            if score_col else pd.Series(0.0, index=d.index))

    is_pinned = pd.Series(False, index=d.index)                          # ① pinned 全入
    if "pinned" in d.columns:
        is_pinned |= d["pinned"].map(lambda v: bool(v) if pd.notna(v) else False)
    elif "recall_channels" in d.columns:
        is_pinned |= d["recall_channels"].astype(str) == "pinned"
    mandatory = is_pinned.copy()

    if "n_channels" in d.columns:                                        # ② 多路共振全入
        mandatory |= pd.to_numeric(d["n_channels"], errors="coerce").fillna(0) >= 3

    chan_sets = None
    if "recall_channels" in d.columns:                                   # ③ healthy lane 全入
        chan_sets = d["recall_channels"].fillna("").astype(str).map(lambda s: set(s.split("|")) - {""})
        mandatory |= chan_sets.map(lambda s: "healthy" in s)

    mandatory_idx = list(d.index[mandatory])
    if len(mandatory_idx) > target:
        # M-2 修复(final-review-l3-merge.md):截尾只对**非 pinned**行进行——pinned 恒
        # kept(design 2026-07-11-recall-gate-pinned-config-design.md §4.1"L1→L5 全程
        # 强留"),不因 mandatory(pinned∪共振∪healthy)超 target 被误切进
        # `_l3_pass1_cut.csv`、丢失 L3 真判机会("L3 真判但不可淘汰"失守)。pinned 行数
        # 本身就超过 target 的极端情形(理论上用户 pinned 名单很小,不会发生)→ 全部保留,
        # kept 允许略超 target(强留优先级高于 target 硬性配额)。
        pinned_idx = [i for i in mandatory_idx if is_pinned.loc[i]]
        other_idx = [i for i in mandatory_idx if not is_pinned.loc[i]]
        ranked = sorted(other_idx, key=lambda i: order.loc[i], reverse=True)
        remaining_target = max(0, target - len(pinned_idx))
        kept_set: set[int] = set(pinned_idx) | set(ranked[:remaining_target])
    else:
        kept_set = set(mandatory_idx)

    remaining = target - len(kept_set)
    if remaining > 0 and chan_sets is not None:                          # ④ 每通道内名次轮询填满
        queues: dict[str, list[int]] = {}
        for i in d.index:
            if i in kept_set:
                continue
            for c in (chan_sets.loc[i] - {"(backfill)", "pinned"}):
                queues.setdefault(c, []).append(i)
        for c in queues:
            queues[c].sort(key=lambda i: order.loc[i], reverse=True)
        ptrs = dict.fromkeys(queues, 0)
        chan_names = sorted(queues)
        progressed = True
        while remaining > 0 and progressed:
            progressed = False
            for c in chan_names:
                if remaining <= 0:
                    break
                q, p = queues[c], ptrs[c]
                while p < len(q) and q[p] in kept_set:
                    p += 1
                if p < len(q):
                    kept_set.add(q[p])
                    remaining -= 1
                    progressed = True
                    p += 1
                ptrs[c] = p
    if remaining > 0:                                                     # 填满收尾(无通道/耗尽的剩余票)
        leftover = [i for i in d.index if i not in kept_set]
        leftover.sort(key=lambda i: order.loc[i], reverse=True)
        kept_set.update(leftover[:remaining])

    kept_idx = sorted(kept_set)
    cut_idx = [i for i in d.index if i not in kept_set]
    kept = d.loc[kept_idx].reset_index(drop=True)
    cut = d.loc[cut_idx].reset_index(drop=True)
    return kept, cut
