"""L4 per-stock dispatch plan assembly."""
from __future__ import annotations

from pathlib import Path

import pandas as pd

from autoresearch.scan.l4.context import _dossier_summary_text


def dispatch_plan(date: str, root: Path | str | None = None) -> dict:
    """L4 派发感知 TTL 复用(确定性,零 LLM;复审 task-4-review.md Important #1 修复)。

    `write_dispatch_pack` 对已有 `details/<code>.md` 的复用码 skip(不写
    `_l4_prompt_<code>.md`),但工作流原先对**全部** finalists 无条件派卡 —— 复用码那份
    prompt 文件根本不存在,派了个读空文件的 Opus(抵消 TTL 复用省下的成本,复用卡评级也
    没并回 `cards`)。本函数按同一判据(`_l4_prompt_<code>.md` 是否存在)把 finalists
    分两路:`dispatch`(需新派 Opus)与 `reused`(已就位卡,直接 `parse_rating` 解评级
    并回,不再派 subagent)。两个标志都缺(异常态)→ 归 `dispatch`,兜底走正常派发。

    返回 `{"dispatch": [code6...], "reused": [{"code","rating"}...], "meta": {code6: {"name","sector"}}}`
    ——`meta` 仅含 `dispatch` 码(L4 情报站 plan Task 2:供并行情报 agent 派发 prompt 用名称/行业,
    不查 finalists.csv 即可读到),直取 finalists.csv 的 `name`/`sector` 列,缺列容错为 `""`。
    """
    base = Path(root) if root else Path("context/scan")
    scan_dir = base / date
    fp = scan_dir / "finalists.csv"
    dispatch: list[str] = []
    reused: list[dict] = []
    meta: dict[str, dict] = {}
    if not fp.exists():
        return {"dispatch": dispatch, "reused": reused, "meta": meta}
    from autoresearch.agents.utils.rating import parse_rating  # 延迟导入,保持本模块轻量
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
        if (scan_dir / f"_l4_prompt_{code6}.md").exists():
            dispatch.append(code6)
            meta[code6] = {"name": _cell(r, "name"), "sector": _cell(r, "sector"),
                           "pinned": _cell(r, "lane").strip() == "pinned",
                           # Wave3.5:intel 已知底「内嵌代替授权」——摘要文本随 meta 走,
                           # workflow 内嵌进 intel prompt,agent 因此无需 Read 权限(结构性盲回工具级)。
                           "dossier_summary": _dossier_summary_text(code6)}
            continue
        details = scan_dir / "details" / f"{code6}.md"
        if details.exists():
            reused.append({"code": code6, "rating": parse_rating(details.read_text(encoding="utf-8"))})
            import contextlib
            with contextlib.suppress(Exception):
                from autoresearch.scan.stock_stage import record_l4_result

                record_l4_result(scan_dir, code6, reused=True)
        else:
            dispatch.append(code6)   # 两者皆无(异常):兜底走正常派发,不静默丢票
            meta[code6] = {"name": _cell(r, "name"), "sector": _cell(r, "sector"),
                           "pinned": _cell(r, "lane").strip() == "pinned",
                           # Wave3.5:intel 已知底「内嵌代替授权」——摘要文本随 meta 走,
                           # workflow 内嵌进 intel prompt,agent 因此无需 Read 权限(结构性盲回工具级)。
                           "dossier_summary": _dossier_summary_text(code6)}
    return {"dispatch": dispatch, "reused": reused, "meta": meta}
