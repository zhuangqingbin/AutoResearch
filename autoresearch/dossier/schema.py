"""dossier 档案格式契约(八节锚+frontmatter+摘要 lint;确定性,零 LLM)。

spec: docs/specs/2026-07-22-research-depth-dossier-design.md ①。八节标题与摘要锚是
机器契约:builder 写、lint 校、L4 注入器(Wave 3)按锚裁剪——改动须同步三方。
"""
from __future__ import annotations

from pathlib import Path

DOSSIER_DIR = Path("context/knowledge/dossiers")

SECTIONS: tuple[str, ...] = (
    "## 1. 业务模型", "## 2. 盈利驱动与预测留档", "## 3. 估值带",
    "## 4. 筹码与资金结构史", "## 5. 风险矩阵", "## 6. 催化剂日历",
    "## 7. 判例账本", "## 8. 变化项日志",
)
SUMMARY_HEAD = "## 摘要(注入用)"
SUMMARY_ANCHORS: tuple[str, ...] = ("业务:", "驱动:", "带位:", "风险:", "催化:", "判例:")
SUMMARY_CAP = 3000    # 注入摘要 token 硬帽(spec ①;lint 与注入器同源引用)

_META_KEYS = ("code", "name", "sector", "pool_status", "entered", "entry_reason",
              "initiated", "last_refresh", "last_delta")


def dossier_path(code6: str) -> Path:
    return DOSSIER_DIR / f"{str(code6).zfill(6)}.md"


def est_tokens(text: str) -> int:
    return int(len(text.encode("utf-8")) / 2.8)


def render_frontmatter(meta: dict) -> str:
    lines = ["---"]
    for k in _META_KEYS:
        v = meta.get(k)
        lines.append(f"{k}: {'null' if v is None else v}")
    lines.append("---")
    return "\n".join(lines) + "\n"


def parse_frontmatter(text: str) -> dict:
    if not text.startswith("---"):
        return {}
    end = text.find("\n---", 3)
    if end < 0:
        return {}
    out: dict = {}
    for ln in text[3:end].strip().splitlines():
        if ":" not in ln:
            continue
        k, v = ln.split(":", 1)
        v = v.strip()
        out[k.strip()] = None if v in ("null", "") else v
    return out


def _summary_block(text: str) -> str:
    i = text.find(SUMMARY_HEAD)
    if i < 0:
        return ""
    j = text.find("\n## ", i + len(SUMMARY_HEAD))
    return text[i:j] if j > 0 else text[i:]


def injectable_summary(code6: str) -> str:
    """可注入的摘要块;不可注入(缺档案/未首覆/摘要缺/超帽)→ ""。

    L4 注入器与卡契约 lint 的**单一分档事实源**(生产者/消费者必须同门,
    防「没注入却照查」的 FN-1 族缝)。异常吞成 ""(坏档不挡派发/lint)。
    """
    try:
        p = dossier_path(code6)
        if not p.exists():
            return ""
        text = p.read_text(encoding="utf-8")
        if not parse_frontmatter(text).get("initiated"):
            return ""
        block = _summary_block(text)
        if not block or est_tokens(block) > SUMMARY_CAP:
            return ""
        return block
    except Exception:  # noqa: BLE001 — 坏档=不可注入,不抛
        return ""


def lint_dossier(text: str, cap: int = SUMMARY_CAP) -> list[str]:
    issues = [f"缺节锚:{s}" for s in SECTIONS if s not in text]
    if SUMMARY_HEAD not in text:
        issues.append(f"缺节锚:{SUMMARY_HEAD}")
        return issues
    block = _summary_block(text)
    if est_tokens(block) > cap:
        issues.append(f"summary>cap({est_tokens(block)}>{cap})")
    issues += [f"摘要缺锚:{a}" for a in SUMMARY_ANCHORS if a not in block]
    return issues


STALE_DAYS = 90     # 档案陈旧告警阈值(spec 风险节:last_refresh 超 90 日 → warn)


def staleness_issues(text: str, today: str, *, cap_days: int = STALE_DAYS) -> list[str]:
    """档案陈旧度探针:`last_refresh`(缺则退 `initiated`)距 today 超 cap_days → 一条 issue。

    与 `lint_dossier`(结构契约)分开:结构对但内容陈旧是另一类病,且需要"今天"这个
    外部输入才能判——不塞进纯结构 lint(规模检查与结构检查分开,repo 既有惯例)。
    两个日期都空 = 骨架未首覆,归 pending_init 管,不在此报。
    """
    from datetime import date as _date
    meta = parse_frontmatter(text)
    ref = meta.get("last_refresh") or meta.get("initiated")
    if not ref:
        return []
    try:
        y, m, d = (int(x) for x in str(ref).split("-"))
        ty, tm, td = (int(x) for x in str(today).split("-"))
        age = (_date(ty, tm, td) - _date(y, m, d)).days
    except Exception:  # noqa: BLE001 — 日期畸形不报陈旧(结构 lint 的事)
        return []
    if age > cap_days:
        return [f"档案陈旧:last_refresh {ref} 距今 {age} 日(>{cap_days})"]
    return []
