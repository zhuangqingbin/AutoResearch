#!/usr/bin/env python3
"""L4 卡三条影子机读行(2026-10-03 复盘稿 B5):只记账,不进任何门、不改评级与 BUY。

复盘稿 §3.6 的三个「看起来有、其实读不出」:
- 「兑现机制」裁决行的 ✓ 极性随前提表述翻转(40/41 个 ✓ 在确认「没有」机制)—— 不可机读;
- 卡片特有的价格类入场否决(如「收 < 1.88 放弃」)写在散文里 —— T+1 尾盘无法确定性核对;
- 三档情景的固定格式只解析出 78 张里的 10 张 —— 概率与收益没法计分。

三条新行(由任务包「本次参数」块下发,`l4.shadow_fields` 缺省开):

    **兑现机制**: 成立|不成立|未核                      (极性固定:成立 = 写得出 D2 开盘的买家或事件)
    [入场否决] close <op> <数> → <理由>                 (每条一行;与 [价格线] 同一 DSL)
    [情景] bull <概率>% <收益>% · base … · bear …       (满卡;概率合计 100)

解析结果按 run 落 `_card_shadow_fields.json`(observe 阶段,与不可买归因同一个时刻)。没写 = None,
写错 = `errors` 里一条,绝不猜。
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

SCHEMA_VERSION = 1
FILENAME = "_card_shadow_fields.json"
MECHANISM_STATES = ("成立", "不成立", "未核")
#: 任务包里给 l4-card 的样例,与解析器同源(测试逐字核对它们出现在任务包里且能解析回来)。
EXAMPLES = (
    "**兑现机制**: 成立",
    "[入场否决] close < 1.88 → 跌破突破位放弃",
    "[情景] bull 25% +4.5% · base 50% -0.5% · bear 25% -5.0%",
)

#: 行首可带列表符(卡里的 `[价格线]` / `[执行线]` 全是 `- ` 开头;复审 I-3)。
_LEAD = r"^[ \t]*(?:[-*+•][ \t]+|\d+[.)][ \t]+)?"
_MECHANISM_RE = re.compile(_LEAD + r"\*\*兑现机制\*\*\s*[:：]\s*(?P<value>.+?)\s*$", re.M)
_MECHANISM_VALUE_RE = re.compile(r"^(不成立|未核|成立)(?=$|[\s(（,，;；])")
_VETO_RE = re.compile(
    _LEAD + r"\[入场否决\]\s*(?P<raw>close\s*(?P<op><=|>=|<|>)\s*(?P<num>-?\d+(?:\.\d+)?).*?)\s*$",
    re.M)
_NUM = r"([+\-−]?\d+(?:\.\d+)?)"
_SCENARIO_RE = re.compile(
    _LEAD + rf"\[情景\]\s*bull\s*{_NUM}%\s*{_NUM}%\s*·\s*base\s*{_NUM}%\s*{_NUM}%\s*·\s*bear\s*{_NUM}%\s*{_NUM}%\s*$",
    re.M)


def _f(text: str) -> float:
    return float(text.replace("−", "-"))


def parse(text: str) -> dict:
    """一张卡 → `{mechanism, entry_vetoes, scenarios, ev, errors}`。"""
    errors: list[str] = []
    mechanism = None
    found = _MECHANISM_RE.search(text or "")
    if found:
        value = found.group("value")
        match = _MECHANISM_VALUE_RE.match(value)
        if match:
            mechanism = match.group(1)
        else:
            errors.append(f"兑现机制 不在 {'|'.join(MECHANISM_STATES)} 里:{value!r}")
    vetoes = [{"op": m.group("op"), "threshold": float(m.group("num")), "raw": m.group("raw")}
              for m in _VETO_RE.finditer(text or "")]
    scenarios = ev = None
    found = _SCENARIO_RE.search(text or "")
    if found:
        values = [_f(v) for v in found.groups()]
        cells = {name: {"p": values[2 * i], "ret": values[2 * i + 1]}
                 for i, name in enumerate(("bull", "base", "bear"))}
        total = sum(cell["p"] for cell in cells.values())
        if abs(total - 100.0) > 1.0:
            errors.append(f"[情景] 概率合计 {total:g} ≠ 100")
        else:
            scenarios = cells
            ev = sum(cell["p"] / 100.0 * cell["ret"] for cell in cells.values())
    elif "[情景]" in (text or ""):
        errors.append("[情景] 行格式不对(应为 bull <概率>% <收益>% · base … · bear …)")
    return {"mechanism": mechanism, "entry_vetoes": vetoes, "scenarios": scenarios, "ev": ev,
            "errors": errors}


def collect(scan_dir: Path | str) -> dict:
    """本 run 的全部卡(`details/*.md`)→ 影子字段记录。"""
    scan = Path(scan_dir)
    cards = {}
    for path in sorted((scan / "details").glob("*.md")) if (scan / "details").is_dir() else []:
        cards[path.stem] = parse(path.read_text(encoding="utf-8", errors="replace"))
    return {
        "schema_version": SCHEMA_VERSION,
        "n_cards": len(cards),
        "coverage": {"mechanism": sum(c["mechanism"] is not None for c in cards.values()),
                     "scenarios": sum(c["scenarios"] is not None for c in cards.values()),
                     "entry_vetoes": sum(bool(c["entry_vetoes"]) for c in cards.values())},
        "cards": cards,
    }


def write(scan_dir: Path | str) -> Path:
    path = Path(scan_dir) / FILENAME
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(collect(scan_dir), ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(path)
    return path


def safe_write(scan_dir: Path | str) -> Path | None:
    """观测腿:失败只打一行,不阻断发布(与 `buyability.safe_write_buyability` 同一纪律)。"""
    try:
        return write(scan_dir)
    except Exception as exc:  # noqa: BLE001 — 影子字段不得让 observe 失败,但必须留痕
        print(f"[card_shadow_fields] 写入失败: {exc!r}", file=sys.stderr)
        return None
