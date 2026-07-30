"""L4 per-stock dispatch plan assembly."""
from __future__ import annotations

from pathlib import Path

import pandas as pd

from autoresearch.scan.l4.context import _dossier_summary_text


def dispatch_plan(date: str, root: Path | str | None = None) -> dict:
    """L4 派发计划(确定性,零 LLM)。

    Wave9 R5(2026-07-29 用户裁定「不要任何复用」):TTL 复用整体退役 —— 复用票不跑
    intel、新闻冻在源卡日(07-29 实测 601211),且复用门的"无新公告"判据在 anns 无
    权限日是盲的。评级稳定性改由**昨卡回声**(见 l4/prompts.py)承接。

    全部 finalists 无条件进 `dispatch`(不再按 `_l4_prompt_<code>.md` /
    `details/<code>.md` 是否存在把票分 dispatch/reused 两路——这两个文件的存在性此刻
    与"要不要派 Opus"再无关系)。返回
    `{"dispatch": [code6...], "meta": {code6: {"name","sector","pinned","dossier_summary"}}}`
    ——`meta` 覆盖**全部** dispatch 码(L4 情报站 plan Task 2:供并行情报 agent 派发 prompt
    用名称/行业,不查 finalists.csv 即可读到),直取 finalists.csv 的 `name`/`sector` 列,
    缺列容错为 `""`。
    """
    base = Path(root) if root else Path("context/scan")
    scan_dir = base / date
    fp = scan_dir / "finalists.csv"
    dispatch: list[str] = []
    meta: dict[str, dict] = {}
    if not fp.exists():
        return {"dispatch": dispatch, "meta": meta}
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
        dispatch.append(code6)
        meta[code6] = {"name": _cell(r, "name"), "sector": _cell(r, "sector"),
                       "pinned": _cell(r, "lane").strip() == "pinned",
                       # Wave3.5:intel 已知底「内嵌代替授权」——摘要文本随 meta 走,
                       # workflow 内嵌进 intel prompt,agent 因此无需 Read 权限(结构性盲回工具级)。
                       "dossier_summary": _dossier_summary_text(code6)}
    return {"dispatch": dispatch, "meta": meta}
