"""dossier δ 增量回写(spec ④⑦;确定性,零 LLM)。

design: docs/specs/2026-07-22-research-depth-dossier-design.md ④;
plan: docs/plans/2026-07-24-wave3-dossier-wiring-plan.md Task 1。

节切片一律以 schema.SECTIONS 锚定位(与 builder 同源);§8 append-only 近 20 条滚动,
同日同事件 key 整行替换(幂等);写盘走「读全文→切片改→整写回」。presence-gated:
档案缺 / 未首覆(initiated 空)→ skip 不建骨架(建档归 dossier-init 链,职责不混)。
异常上抛,由调用方决定兜底(assemble 挂钩 suppress,CLI 直接报)。
"""
from __future__ import annotations

from pathlib import Path

from autoresearch.dossier import builder, schema

_DELTA_KEEP = 20      # §8 滚动窗(spec ①:append-only,近 20 条滚动)


def _section_span(text: str, idx: int) -> tuple[int, int]:
    """§idx 正文区间 [start, end)(不含节头行);节锚缺 → (-1, -1)。"""
    head = schema.SECTIONS[idx]
    i = text.find(head)
    if i < 0:
        return (-1, -1)
    start = text.find("\n", i) + 1
    j = text.find("\n## ", i + len(head))
    return (start, j if j > 0 else len(text))


def section_body(text: str, idx: int) -> str:
    start, end = _section_span(text, idx)
    return text[start:end] if start >= 0 else ""


def replace_section(text: str, idx: int, body: str) -> str:
    """整替 §idx 正文(节头不动);body 末尾自动补换行。节锚缺 → 原文返回(交 lint 报)。"""
    start, end = _section_span(text, idx)
    if start < 0:
        return text
    if not body.endswith("\n"):
        body += "\n"
    return text[:start] + body + text[end:]


def append_delta_line(text: str, date: str, line: str, *, key: str | None = None) -> str:
    """§8 追加 `- {date} {line}`;同日同 key 已有 → 整行替换(幂等);滚动保近 _DELTA_KEEP 条。"""
    if _section_span(text, 7)[0] < 0:
        return text
    rows = [ln for ln in section_body(text, 7).splitlines() if ln.strip()]
    prefix = f"- {date} {key or line}"
    rows = [r for r in rows if not r.startswith(prefix)]
    rows.append(f"- {date} {line}")
    return replace_section(text, 7, "\n".join(rows[-_DELTA_KEEP:]) + "\n")


def refresh_summary_line(text: str, anchor: str, value: str) -> str:
    """摘要块内单锚行重写为 `- {anchor} {value}`;锚行缺 → 原文返回(lint 另报)。"""
    i = text.find(schema.SUMMARY_HEAD)
    if i < 0:
        return text
    j = text.find("\n## ", i)
    j = j if j > 0 else len(text)
    lines = text[i:j].splitlines()
    for k, ln in enumerate(lines):
        if ln.strip().startswith(f"- {anchor}"):
            lines[k] = f"- {anchor} {value}"
            return text[:i] + "\n".join(lines) + text[j:]
    return text


def set_frontmatter_key(text: str, key: str, value: str) -> str:
    """frontmatter 单键改写;键缺/无 frontmatter → 原文返回。"""
    if not text.startswith("---"):
        return text
    end = text.find("\n---", 3)
    if end < 0:
        return text
    lines = text[:end].splitlines()
    for k, ln in enumerate(lines):
        if ln.split(":", 1)[0].strip() == key:
            lines[k] = f"{key}: {value}"
            return "\n".join(lines) + text[end:]
    return text


def _refresh_band(text: str, pf: dict | None) -> str:
    """§3 估值带整节重算(prefetch val_band 在才动;与 builder 同一份纯函数)。"""
    band = (pf or {}).get("val_band")
    if not band:
        return text
    return replace_section(
        text, 2, builder._val_band_table(band) + "\n\n" + builder._band_position_text(band))


def _append_eps_snapshot(text: str, pf: dict | None) -> str:
    """§2 尾追加一致预期快照行(逐次留档,spec ①§2);同 as-of 已录 → 幂等跳过。"""
    fwd = (pf or {}).get("fwd_eps") or {}
    asof = fwd.get("asof")
    if not (asof and isinstance(fwd, dict)
            and any(str(k).startswith("fwd_eps_") for k in fwd)):
        return text
    line = f"- 快照 {asof}:{builder._fwd_eps_line(fwd)}"
    body = section_body(text, 1)
    if line in body:
        return text
    return replace_section(text, 1, body.rstrip("\n") + "\n" + line + "\n")


def record_scan_delta(code6: str, date: str, *, rating: str, conviction=None,
                      scan_root: str | Path = "context/scan") -> dict:
    """单票 δ 回写:§8 入围行 + §3 带位刷新 + §2 快照 + 摘要机算行 + last_delta。"""
    code6 = str(code6).split(".")[0].zfill(6)
    path = schema.dossier_path(code6)
    if not path.exists():
        return {"code": code6, "skipped": "no_dossier"}
    text = path.read_text(encoding="utf-8")
    if not schema.parse_frontmatter(text).get("initiated"):
        return {"code": code6, "skipped": "not_initiated"}

    bad_conv = conviction is None or conviction == "" or (
        isinstance(conviction, float) and conviction != conviction)
    conv = "" if bad_conv else f"(conv {conviction})"
    text = append_delta_line(text, date, f"入围:评级 {rating}{conv}", key="入围")

    pf = builder._load_prefetch(code6)
    text = _refresh_band(text, pf)
    text = _append_eps_snapshot(text, pf)

    from autoresearch.scan import dossier as scan_dossier  # lazy 防环(scan↔dossier,builder 同款)
    entries = scan_dossier.stock_dossier(code6, scan_root=scan_root,
                                         max_days=builder._PRECEDENT_WINDOW)
    calc = builder.render_summary_calc(pf, len(entries))
    text = refresh_summary_line(text, "带位:", calc["带位"])
    text = refresh_summary_line(text, "判例:", calc["判例"])

    text = set_frontmatter_key(text, "last_delta", date)
    path.write_text(text, encoding="utf-8")
    return {"code": code6, "updated": True, "issues": schema.lint_dossier(text)}
