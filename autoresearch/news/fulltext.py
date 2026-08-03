#!/usr/bin/env python3
"""finalist 公告正文(≤10 只/日)—— L4 证据增强,**不是门直接读全文**(确定性,零 LLM)。

design: docs/specs/2026-08-03-scan-next-wave-brainstorm-design.md §1.4 D4

## 四条边界(设计稿逐条,全部做成代码)

1. **预算按模型输入计量**。限额用**格式化后的字符/token**,不用「2,000 字节≈2KB token」
   这种换算 —— 中文一个字往往就是 1 个 token 甚至更多,按字节估会低估两三倍。
   本模块用 `measure_prompt_delta()` 直接量**注入前后的 prompt 差**;超限按证据优先级
   截断并**显式标记**(不是静默丢)。
2. **假设边界**:D4 是「L4 证据增强」,不是门直接读取全文。
   **不能继续引用错误的「错杀 60%」**(那个数来自 cross_calib 的另一分组,见 §4.1 勘误)。
3. **实验**:同一候选、同一门版本做有/无全文 paired shadow 或盲双卡;比较 claim 正确率、
   评级/门分歧及成熟 T+2。**避免用上线前后两个时期作因果比较**。
4. **核验分工**:`price_claims` 只核验价格/日期/涨跌幅;公告事实**另建**
   excerpt/page/hash verifier,**不得复用价格对账器**。本模块的 `verify_excerpt`
   就是那个独立件。

## 注入契约

只注入带 `source_observation_id + page + excerpt_hash` 的限额摘录。缺任一 → 拒注
(`assert_injectable`)。可用阶段固定为 `L4` —— 因此它**进不了同日 L3 回放**(catalog PIT)。

  uv run --no-sync python -m autoresearch.news.fulltext budget --scan-dir context/scan/2026-08-01
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
from dataclasses import asdict, dataclass
from pathlib import Path

SCHEMA_VERSION = 1
RULE_VERSION = "fulltext.v1"
EXTRACT_VERSION = "extract.v1"

MAX_FINALISTS_PER_DAY = 10        # §1.4 标题里的 ≤10
LOOKBACK_DAYS = 10                # 近 10 日关键公告
DEFAULT_TOKEN_BUDGET = 1200       # 每票摘录的 **token** 预算(实测口径,不是字节)
AVAILABLE_STAGE = "L4"            # finalist 之后才抓到 —— 进不了同日 L3 回放

# 证据优先级:超预算时**从低往高砍**,并显式标记砍了什么。
PRIORITY = ("financial_delivery", "risk_disclosure", "governance", "boilerplate")
_PRIORITY_RANK = {name: i for i, name in enumerate(PRIORITY)}

# 中文 token 估算:CJK 字符按 1 token,ASCII 词按 ~0.3 token/字符。
# 这是**估算器**,`measure_prompt_delta` 才是判据 —— 真要卡预算就量真 prompt。
_CJK = re.compile(r"[一-鿿　-〿＀-￯]")


class FulltextError(ValueError):
    """摘录违反注入契约 —— 拒注,不是静默降级。"""


def estimate_tokens(text: str) -> int:
    """中文优先的 token 估算。**明确是估算**:预算判据走 `measure_prompt_delta`。"""
    s = str(text or "")
    cjk = len(_CJK.findall(s))
    other = len(s) - cjk
    return int(cjk + round(other * 0.3))


def excerpt_hash(text: str) -> str:
    return hashlib.sha256(str(text or "").encode("utf-8")).hexdigest()[:16]


@dataclass
class Excerpt:
    """一段可注入的公告摘录。三件套缺一不可:observation / page / hash。"""
    source_observation_id: str
    code: str
    page: int
    text: str
    category: str = "boilerplate"
    doc_hash: str = ""                 # 原始 PDF/HTML 的 hash
    extract_version: str = EXTRACT_VERSION
    available_stage: str = AVAILABLE_STAGE
    excerpt_hash: str = ""

    def __post_init__(self):
        if not self.excerpt_hash:
            self.excerpt_hash = excerpt_hash(self.text)

    @property
    def tokens(self) -> int:
        return estimate_tokens(self.text)

    def as_dict(self) -> dict:
        return {**asdict(self), "tokens": self.tokens}


def assert_injectable(excerpt: Excerpt) -> None:
    """注入契约:`source_observation_id + page + excerpt_hash` 缺一即拒(§1.4)。"""
    problems = []
    if not str(excerpt.source_observation_id or "").strip():
        problems.append("缺 source_observation_id")
    if excerpt.page is None or int(excerpt.page) < 1:
        problems.append("缺 page(页码是可复核的最小定位单位)")
    if not str(excerpt.excerpt_hash or "").strip():
        problems.append("缺 excerpt_hash")
    if excerpt.excerpt_hash != excerpt_hash(excerpt.text):
        problems.append("excerpt_hash 与正文不符 —— 摘录被改过")
    if excerpt.category not in _PRIORITY_RANK:
        problems.append(f"未知证据类别 {excerpt.category!r}")
    if excerpt.available_stage != AVAILABLE_STAGE:
        problems.append(f"available_stage 必须是 {AVAILABLE_STAGE}(D4 在 finalist 之后抓到)")
    if problems:
        raise FulltextError(f"摘录不可注入:{'; '.join(problems)}")


# ───────────────────────── 预算(按模型输入,不按字节)─────────────────────────


def measure_prompt_delta(base_prompt: str, excerpts: list[Excerpt], *,
                         render=None) -> dict:
    """**真·预算判据**:量注入前后的 prompt 差,而不是数字节。

    §1.4 原话:「限额使用格式化后的字符/token,不使用『2,000 字节≈2KB token』;
    先测中文 PDF 抽取后的实际 prompt delta」。
    """
    rendered = (render or render_excerpts)(excerpts)
    after = f"{base_prompt}\n\n{rendered}" if rendered else base_prompt
    return {
        "base_chars": len(base_prompt),
        "after_chars": len(after),
        "delta_chars": len(after) - len(base_prompt),
        "base_tokens": estimate_tokens(base_prompt),
        "after_tokens": estimate_tokens(after),
        "delta_tokens": estimate_tokens(after) - estimate_tokens(base_prompt),
        "n_excerpts": len(excerpts),
        "basis": "formatted_prompt_delta",
        "note": "按格式化后的 prompt 实测;**不用**字节≈token 的换算",
    }


def fit_budget(excerpts: list[Excerpt], *, token_budget: int = DEFAULT_TOKEN_BUDGET,
               base_prompt: str = "") -> dict:
    """按证据优先级装箱 → `{kept, dropped, truncated, measured}`。

    超限**从低优先级往高砍**,并把砍掉的**显式列出来**(§1.4「超限按证据优先级截断
    并显式标记」)—— 静默丢弃会让读的人以为这票就这点材料。
    """
    ordered = sorted(excerpts,
                     key=lambda e: (_PRIORITY_RANK.get(e.category, 99), -e.tokens))
    kept: list[Excerpt] = []
    dropped: list[dict] = []
    used = 0
    for item in ordered:
        if used + item.tokens <= token_budget:
            kept.append(item)
            used += item.tokens
            continue
        dropped.append({"source_observation_id": item.source_observation_id,
                        "page": item.page, "category": item.category,
                        "tokens": item.tokens,
                        "reason": f"超 token 预算({used}+{item.tokens} > {token_budget})"})
    return {
        "kept": kept, "dropped": dropped,
        "tokens_used": used, "token_budget": token_budget,
        "truncated": bool(dropped),
        # 显式标记:注入块自己会带这句,读的人不会误以为这票只有这点材料
        "truncation_notice": (
            f"⚠️ 因 token 预算截断:丢弃 {len(dropped)} 段("
            + "、".join(sorted({d['category'] for d in dropped})) + ")"
            if dropped else ""),
        "measured": measure_prompt_delta(base_prompt, kept),
    }


def render_excerpts(excerpts: list[Excerpt]) -> str:
    """注入块 —— 每段都带 observation/page/hash,读的人可以逐段回溯。"""
    if not excerpts:
        return ""
    lines = ["#### 公告正文摘录(D4;L4 阶段可用)", ""]
    for item in excerpts:
        assert_injectable(item)
        lines.append(f"- [{item.source_observation_id} p{item.page} "
                     f"#{item.excerpt_hash}] ({item.category}) {item.text}")
    return "\n".join(lines)


# ───────────────────────── 独立 verifier(不复用价格对账器)─────────────────────────


def verify_excerpt(excerpt: Excerpt, documents: dict) -> dict:
    """公告事实核验器 —— **与 `price_claims` 完全独立**(§1.4 核验分工)。

    `documents`:`{doc_hash: {"pages": {page: text}}}`。三件事各自给 verdict:
    页码在不在、原文含不含这段、doc_hash 对不对得上。任何一项拿不到 → `UNKNOWN`,
    不是 `FAIL`(「我们没留档」不等于「它是假的」)。
    """
    # 边界声明跟着**每一条**返回走 —— 只在成功路径上声明,等于在最需要它的失败路径上沉默。
    stamp = {"verifier": "fulltext.verify_excerpt",
             "boundary": "公告事实专用;价格/日期/涨跌幅仍走 scan.price_claims"}
    doc = documents.get(excerpt.doc_hash)
    if doc is None:
        return {**stamp, "verdict": "UNKNOWN", "reasons": ["无原文留档 —— 不判假"],
                "checks": {"doc_present": "UNKNOWN", "page_present": "UNKNOWN",
                           "text_present": "UNKNOWN"}}
    pages = doc.get("pages") or {}
    page_text = pages.get(excerpt.page) or pages.get(str(excerpt.page))
    checks = {"doc_present": "PASS",
              "page_present": "PASS" if page_text is not None else "FAIL",
              "text_present": "UNKNOWN"}
    reasons = []
    if page_text is None:
        reasons.append(f"原文无第 {excerpt.page} 页")
    else:
        present = excerpt.text.strip() and excerpt.text.strip() in str(page_text)
        checks["text_present"] = "PASS" if present else "FAIL"
        if not present:
            reasons.append("该页原文不含此摘录 —— 摘录与出处对不上")
    verdict = ("FAIL" if "FAIL" in checks.values()
               else ("PASS" if all(v == "PASS" for v in checks.values()) else "UNKNOWN"))
    return {**stamp, "verdict": verdict, "reasons": reasons, "checks": checks}


# ───────────────────────── 计划(≤10 只/日)─────────────────────────


def plan_fetch(finalists: list[str], *, max_per_day: int = MAX_FINALISTS_PER_DAY,
               lookback_days: int = LOOKBACK_DAYS) -> dict:
    """当日抓取计划。超过 `max_per_day` → **显式列出被排除的票**,不静默截断。"""
    codes = [str(c).zfill(6) for c in finalists]
    kept, excluded = codes[:max_per_day], codes[max_per_day:]
    return {
        "schema_version": SCHEMA_VERSION,
        "n_finalists": len(codes),
        "codes": kept,
        "excluded": excluded,
        "excluded_reason": (f"超过每日上限 {max_per_day}" if excluded else ""),
        "lookback_days": lookback_days,
        "available_stage": AVAILABLE_STAGE,
        "scope_note": ("finalist 逐票抓取 = **选择性**采集,只能做个股证据,"
                       "不得计入市场新闻量(catalog scope=selective)"),
    }


ASSUMPTION_BOUNDARY = (
    "D4 是 **L4 证据增强**,不是门直接读取全文;"
    "不得继续引用错误的「错杀 60%」——那个数来自 cross_calib 的另一分组,"
    "A11 v3 单门口径见 §4.1 勘误")

EXPERIMENT_DESIGN = (
    "同一候选、同一门版本做**有/无全文 paired shadow** 或盲双卡;"
    "比较 claim 正确率、评级/门分歧及成熟 T+2。"
    "**不得**用上线前后两个时期作因果比较(那是时间趋势,不是处理效应)")


def render_report(plan: dict, budget: dict) -> str:
    lines = ["# D4 finalist 公告正文", "",
             f"> {ASSUMPTION_BOUNDARY}", "",
             f"> 实验设计:{EXPERIMENT_DESIGN}", "",
             f"- 计划抓取 **{len(plan['codes'])}/{plan['n_finalists']}** 只 · "
             f"近 {plan['lookback_days']} 日 · 可用阶段 `{plan['available_stage']}`"]
    if plan["excluded"]:
        lines.append(f"- ⚠️ 排除 {len(plan['excluded'])} 只:{plan['excluded_reason']} "
                     f"({'、'.join(plan['excluded'])})")
    measured = budget["measured"]
    lines += ["", "## 预算(按格式化后的 prompt 实测)", "",
              f"- 保留 {len(budget['kept'])} 段 · 用 {budget['tokens_used']}/"
              f"{budget['token_budget']} token",
              f"- prompt delta:{measured['delta_chars']} 字符 / "
              f"{measured['delta_tokens']} token(`{measured['basis']}`)",
              f"- {measured['note']}"]
    if budget["truncation_notice"]:
        lines.append(f"- {budget['truncation_notice']}")
    lines += ["", f"- {plan['scope_note']}", ""]
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="D4 finalist 公告正文(§1.4)")
    sub = ap.add_subparsers(dest="cmd", required=True)
    b = sub.add_parser("plan", help="从 finalists.csv 生成抓取计划")
    b.add_argument("--scan-dir", required=True)
    b.add_argument("--json-out", default=None)

    a = ap.parse_args(argv)
    import pandas as pd

    path = Path(a.scan_dir) / "finalists.csv"
    codes: list[str] = []
    if path.exists():
        frame = pd.read_csv(path, dtype={"code": str})
        if "code" in frame.columns:
            codes = frame["code"].astype(str).str.zfill(6).tolist()
    plan = plan_fetch(codes)
    budget = fit_budget([])
    if a.json_out:
        out = Path(a.json_out)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps({"plan": plan, "budget": {
            k: v for k, v in budget.items() if k != "kept"}},
            ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(render_report(plan, budget))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
