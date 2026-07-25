#!/usr/bin/env python3
"""macro-research · macro_state —— full 档的机读摘要(宏观 lite / scan Stage 0 消费)。

design: docs/specs/2026-07-03-research-skills-altitude-refactor-design.md §5.2(Phase 2)。

`write_macro_state`:assemble 组装成功后确定性落 `context/macro/macro_state.json`——评级解析
**复用 `parse_allocation`**(与 assemble 校验同一解析,零新判断),其余字段 best-effort(缺段 →
None/{},描述性可缺)。`load_macro_state`:注入方读取 + **双失效**:① age > ttl_days;
② 当日 regime ≠ regime_at_run(regime 翻转日拿旧宏观叙事校准比没有更坏——与 lessons 的
regime 域同一教训)。缺/坏/过期 → (None, 原因),调用方回退"只用日频 pack"(presence-gated)。
"""
from __future__ import annotations

import json
import re
from datetime import date as _date
from pathlib import Path

STATE_NAME = "macro_state.json"
DEFAULT_ROOT = Path("context/macro")
DEFAULT_TTL_DAYS = 7

_RISK_MAP = {"Buy": "risk_on", "Overweight": "risk_on", "Hold": "neutral",
             "Underweight": "risk_off", "Sell": "risk_off"}


def _regime_from_scan_meta(as_of: str, scan_root: Path | str = Path("context/scan")) -> str | None:
    """当日 scan staging 的 meta.regime(universe 落的同一标签);缺 → None(失效判据只剩 age)。"""
    p = Path(scan_root) / as_of / "meta.json"
    if not p.exists():
        return None
    try:
        reg = json.loads(p.read_text(encoding="utf-8")).get("regime")
    except Exception:  # noqa: BLE001 — 坏 meta 不阻摘要
        return None
    if isinstance(reg, dict):
        reg = reg.get("label")
    return reg if isinstance(reg, str) else None


def _dashboard_line(decision_text: str) -> str | None:
    """decision.md 宏观仪表盘里含「象限」的那一行(raw 文本,描述性可缺)。"""
    for line in decision_text.splitlines():
        if "象限" in line:
            return line.strip()[:160]
    return None


def _key_risks(premortem_text: str | None, cap: int = 3) -> list[str]:
    """premortem 加粗 bullet(红队死因)→ 前 cap 条短句;无 → []。"""
    if not premortem_text:
        return []
    risks: list[str] = []
    for line in premortem_text.splitlines():
        s = line.strip()
        if s.startswith(("-", "*")) and "**" in s:
            body = re.sub(r"[*_`]", "", s.lstrip("-* ")).strip()
            if body:
                risks.append(body[:60])
        if len(risks) >= cap:
            break
    return risks


def write_macro_state(root: Path | str, report_path: Path | str | None = None,
                      out_dir: Path | str | None = None,
                      scan_root: Path | str = Path("context/scan")) -> dict:
    """从 macro context 目录(`context/macro/<date>`)抽机读摘要 → `<out_dir>/macro_state.json`。

    out_dir 缺省 = `context/macro`(assemble 传 root.parent,测试传 tmp);返回写入的 dict。
    """
    from autoresearch.macro.assemble import DECISION_REL, SECTOR_MAP_REL, parse_allocation
    root = Path(root)
    as_of = root.name

    def _txt(rel: str) -> str | None:
        p = root / rel
        return p.read_text(encoding="utf-8") if p.exists() else None

    decision = _txt(DECISION_REL) or ""
    cross = parse_allocation(decision)
    sectors_txt = _txt(SECTOR_MAP_REL)
    overall = next((v for k, v in cross.items()
                    if "风险档" in k or k.upper().startswith("OVERALL")), None)
    state = {
        "as_of": as_of,
        "run_report": str(report_path) if report_path else None,
        "regime_at_run": _regime_from_scan_meta(as_of, scan_root),
        "quadrant_raw": _dashboard_line(decision),
        "overall_rating": overall,
        "risk_stance": _RISK_MAP.get(overall or ""),
        "cross_asset": cross,
        "ashare_sectors": parse_allocation(sectors_txt) if sectors_txt else {},
        "key_risks": _key_risks(_txt("1_spine/premortem.md")),
        "ttl_days": DEFAULT_TTL_DAYS,
    }
    out = Path(out_dir) if out_dir else DEFAULT_ROOT
    out.mkdir(parents=True, exist_ok=True)
    (out / STATE_NAME).write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
    return state


def state_readiness(root: Path | str) -> dict:
    """`context/macro/<date>` 能不能出 macro_state → {ok, have, missing_optional}。

    Wave5 ③B 的根因诊断:`write_macro_state` 其实**只需要 `1_spine/decision.md`**
    (sector_map/premortem 都是 best-effort),但它此前只在 `assemble.main()` 里被调用,
    而 assemble 要求 **~20 个分段文件齐全**才肯往下走。于是"最便宜的机读产物(下游 lite
    真正消费的那个)"被"最贵的门(整份 20 节报告)"扣着 —— 这就是 macro_state 从
    2026-06-22 起恒为 None、market_view 一个月开篇都写「无新鲜宏观视图」的原因。
    不是 bug,是门设错了地方。
    """
    root = Path(root)
    from autoresearch.macro.assemble import DECISION_REL, SECTOR_MAP_REL
    have = [rel for rel in (DECISION_REL, SECTOR_MAP_REL, "1_spine/premortem.md")
            if (root / rel).exists()]
    return {"ok": (root / DECISION_REL).exists(), "have": have,
            "required": DECISION_REL,
            "missing_optional": [rel for rel in (SECTOR_MAP_REL, "1_spine/premortem.md")
                                 if rel not in have]}


def main(argv: list[str] | None = None) -> int:
    """CLI:从 spine 直接落 macro_state.json,**不要求整份 20 节报告**。

      uv run --no-sync python -m autoresearch.macro.state context/macro/2026-07-25
    """
    import argparse
    ap = argparse.ArgumentParser(description="macro_state 落盘(只需 decision.md;零 LLM)")
    ap.add_argument("root", help="macro context 目录,如 context/macro/2026-07-25")
    ap.add_argument("--out-dir", default=None, help="macro_state.json 落点(默认 root 的父目录)")
    a = ap.parse_args(argv)
    root = Path(a.root)
    rd = state_readiness(root)
    if not rd["ok"]:
        print(f"[MISSING] {root / rd['required']} 不存在 —— macro_state 的唯一硬依赖。\n"
              f"  先让 macro-research full 档写出 S1 决策(含每行 `- <KEY>: **Rating**: <档>`),"
              f"其余分段可后补。")
        return 1
    st = write_macro_state(root, out_dir=a.out_dir or root.parent)
    print(f"[macro_state] {Path(a.out_dir or root.parent) / STATE_NAME}(as_of {st['as_of']} · "
          f"跨资产 {len(st['cross_asset'])} 行 · A股行业 {len(st['ashare_sectors'])} 行 · "
          f"overall {st['overall_rating'] or '—'} · regime_at_run {st['regime_at_run'] or '未记'})")
    if rd["missing_optional"]:
        print(f"[note] 可选分段缺(不阻):{'、'.join(rd['missing_optional'])}")
    return 0


def load_macro_state(today: str, regime_today: str | None = None,
                     path: Path | str | None = None) -> tuple[dict | None, str]:
    """读 + 双失效判定 → (state|None, 原因一句)。

    ① age:today − as_of > ttl_days(或 as_of 晚于 today = 前视)→ 失效;
    ② regime:regime_today 与 regime_at_run **两侧都在**且不等 → 失效(任一侧缺 → 该判据跳过)。
    """
    p = Path(path) if path else DEFAULT_ROOT / STATE_NAME
    if not p.exists():
        return None, "无 macro_state.json → 只用日频 pack"
    try:
        state = json.loads(p.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return None, "macro_state.json 不可读 → 只用日频 pack"
    as_of = state.get("as_of")
    try:
        age = (_date.fromisoformat(today) - _date.fromisoformat(str(as_of))).days
    except Exception:  # noqa: BLE001
        return None, "macro_state as_of 不可解析 → 只用日频 pack"
    ttl = int(state.get("ttl_days") or DEFAULT_TTL_DAYS)
    if age < 0:
        return None, f"macro_state as_of {as_of} 晚于 {today}(前视)→ 只用日频 pack"
    if age > ttl:
        return None, f"macro_state 过期(as_of {as_of},{age}d>{ttl}d)→ 只用日频 pack"
    base = state.get("regime_at_run")
    if regime_today and base and regime_today != base:
        return None, f"regime 已翻转({base}→{regime_today})→ 宏观视图失效,只用日频 pack"
    return state, f"macro_state 新鲜(as_of {as_of},{age}d≤{ttl}d,regime_at_run {base or '未记'})"


if __name__ == "__main__":
    raise SystemExit(main())
