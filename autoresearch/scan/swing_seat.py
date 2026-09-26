#!/usr/bin/env python3
"""10 日观察席(影子)—— summary §12 + brief ⑦ 的唯一事实源(零 LLM、零网络、只展示)。

design: docs/superpowers/specs/2026-09-26-daily-engine-consolidation-design.md §5 B3

`common.ruler.SWING_RULER`(fwd_10_oc:D+1 开盘进 → D+10 收盘出)的**影子产品面**:当日
finalist 里 非 📌 ∩ 卡评级 ≥Hold ∩ 卡面入场 ≠ 禁止 的票,按 conviction 降序,附一行
`stage_rulers.csv` 的 10 日尺历史读数(L3 finalist−bench 在 fwd_10 上的 ALL 行)。

它**不是决策**:不进任何门、不进账本 role、不喂任何 prompt,不改评级 / BUY / E6 / 主尺
(冻结窗内可上,spec §3.4)。文案因此不许出现「BUY/买入/可买」(`BANNED_WORDS`,
`self_review.brief_lint` 的「观察席·措辞」读同一张表)。

一次算、两处读:`report_sections.prepare_report_model` 在终评级落盘后调 `build_swing_seat`
并落 `_swing_seat.json`;summary §12 用同一份 dict 渲染,brief ⑦ 只读这份文件的 `n`
(brief 禁读 `details/`,入场行由这里读卡)。每个数字都记 `_src`(field/value/file/locator,
同 brief 边表),值能在对应文件里原样找到。
"""
from __future__ import annotations

import contextlib
import csv
import json
import math
from pathlib import Path

from autoresearch.common import ruler as _ruler
from autoresearch.contracts import artifacts as _artifacts

SCHEMA_VERSION = 1
SWING = _ruler.SWING_RULER
SEAT_FILENAME = _artifacts.by_name("swing_seat").path
SECTION_TITLE = "## 10 日观察席(影子)"
#: 卡评级 ≥Hold(五档里 Hold 及以上)。
SEAT_RATINGS = ("Buy", "Overweight", "Hold")
#: 读数行读 `stage_rulers.csv` 的哪一格(`populations.stage_rulers` 的 L3 finalist−bench,fwd_10 版)。
READOUT_METRIC = "l3_finalist_minus_bench_fwd10"
#: 读数样本门 —— 与 B4「影子 ≥40 交易日」同一个数(spec §5 B4;普查登记的 scan_days >= 40)。
READOUT_MIN_DAYS = 40
#: 观察席文案禁词(它是 10 日尺影子,不是决策)。lint 与渲染器共用这一张表。
BANNED_WORDS = ("BUY", "买入", "可买")
_STANCE_ZH = {"ALLOWED": "允许", "CONDITIONAL": "条件", "PROHIBITED": "禁止"}


def _s(value: object) -> str:
    return "" if value is None else str(value).strip()


def _code6(value: object) -> str:
    return _s(value).split(".")[0].zfill(6)


def _conviction_key(value: str) -> float:
    try:
        number = float(value)
    except ValueError:
        return -math.inf
    return number if math.isfinite(number) else -math.inf


def _is_pinned(row: dict) -> bool:
    """📌 两代标记都认(同 `research.edge_census.pinned_codes`):lane=pinned 或 pinned_note 非空。"""
    return _s(row.get("lane")) == "pinned" or bool(_s(row.get("pinned_note")))


def _default_stage_rulers_path() -> Path:
    from autoresearch.scan import outcome as _outcome

    return _outcome.ledger_root() / _artifacts.by_name("ledger_stage_rulers").path


def _entry_stance(scan: Path, code: str) -> str | None:
    """卡面入场立场(与 E6 的 `card_context.entry_stance` 同一个解析器);没卡 → None。"""
    from autoresearch.scan.l4.parsers import parse_card_context, read_card_text

    text = read_card_text(scan, code)
    if text is None:
        return None
    return parse_card_context(text).get("entry_stance")


def ruler_readout(path: Path | None = None) -> dict:
    """`stage_rulers.csv` 里 `READOUT_METRIC` 的 ALL 行 → 读数行的事实(缺 → ABSENT,不猜)。"""
    target = Path(path) if path is not None else _default_stage_rulers_path()
    row = None
    with contextlib.suppress(OSError, csv.Error), target.open(encoding="utf-8", newline="") as fh:
        row = next((r for r in csv.DictReader(fh)
                    if r.get("session") == "ALL" and r.get("metric") == READOUT_METRIC), None)
    locator = f"session=ALL,metric={READOUT_METRIC}"
    if row is None:
        return {"status": "ABSENT", "metric": READOUT_METRIC, "file": target.name,
                "locator": locator}
    try:
        n_days = int(float(row.get("n_days") or 0))
    except ValueError:
        n_days = 0
    value = row.get("value") or ""
    out = {"metric": READOUT_METRIC, "file": target.name, "locator": locator,
           "n_days": n_days, "value": value, "ci_low": row.get("ci_low") or "",
           "ci_high": row.get("ci_high") or "", "row_status": row.get("status") or ""}
    thin = n_days < READOUT_MIN_DAYS or out["row_status"] != "MATURE" or not value
    return {**out, "status": "THIN" if thin else "OK"}


def build_swing_seat(scan_dir: Path | str, *, stage_rulers_path: Path | None = None) -> dict:
    """finalists.csv × _final_ratings.json × 卡面入场行 → 席位;+ stage_rulers 读数行。"""
    scan = Path(scan_dir)
    with (scan / "finalists.csv").open(encoding="utf-8-sig", newline="") as fh:
        finalists = [r for r in csv.DictReader(fh) if _s(r.get("code"))]
    ratings = {_code6(k): _s(v) for k, v in json.loads(
        (scan / "_final_ratings.json").read_text(encoding="utf-8")).items()}
    rows, src = [], []
    for fin in finalists:
        code = _code6(fin.get("code"))
        rating = ratings.get(code, "")
        if _is_pinned(fin) or rating not in SEAT_RATINGS:
            continue
        stance = _entry_stance(scan, code)
        if stance == "PROHIBITED":
            continue
        rows.append({"code": code, "name": _s(fin.get("name")), "rating": rating,
                     "entry": _STANCE_ZH.get(str(stance), "未写"), "entry_stance": stance,
                     "conviction": _s(fin.get("conviction"))})
    rows.sort(key=lambda r: (-_conviction_key(r["conviction"]), r["code"]))
    for r in rows:
        src += [
            {"field": f"seat.{r['code']}.rating", "value": r["rating"],
             "file": "_final_ratings.json", "locator": r["code"]},
            {"field": f"seat.{r['code']}.conviction", "value": r["conviction"],
             "file": "finalists.csv", "locator": f"code={r['code']}:conviction"},
            {"field": f"seat.{r['code']}.entry", "value": str(r["entry_stance"]),
             "file": f"details/{r['code']}.md", "locator": "**入场** 行(parse_card_context)"},
        ]
    readout = ruler_readout(stage_rulers_path)
    src.append({"field": "seat.readout", "value": readout.get("value", "—"),
                "file": readout["file"], "locator": readout["locator"]})
    return {"schema_version": SCHEMA_VERSION, "date": scan.name, "ruler": SWING,
            "rows": rows, "n": len(rows), "readout": readout, "_src": src}


def write_swing_seat(scan_dir: Path | str, seat: dict) -> Path:
    path = Path(scan_dir) / SEAT_FILENAME
    tmp = path.with_name(f"{path.name}.tmp")
    tmp.write_text(json.dumps(seat, ensure_ascii=False, indent=1, sort_keys=True) + "\n",
                   encoding="utf-8")
    tmp.replace(path)
    return path


def load_swing_seat(scan_dir: Path | str) -> dict | None:
    """落盘的席位;缺/坏 → None(「未生成」,不是「没有」)。"""
    with contextlib.suppress(OSError, ValueError):
        doc = json.loads((Path(scan_dir) / SEAT_FILENAME).read_text(encoding="utf-8"))
        if isinstance(doc, dict) and isinstance(doc.get("rows"), list):
            return doc
    return None


def _readout_line(readout: dict | None) -> str:
    head = "10 日尺历史读数(stage_rulers `" + READOUT_METRIC + "` ALL 行):"
    if not readout or readout.get("status") == "ABSENT":
        return head + "stage_rulers 暂无这一格(视图未重建或特性未上线),不猜。"
    if readout.get("status") == "THIN":
        return head + f"样本不足(n_days {readout.get('n_days')} < {READOUT_MIN_DAYS})。"
    try:
        pp = 100.0 * float(readout["value"])
        lo, hi = 100.0 * float(readout["ci_low"]), 100.0 * float(readout["ci_high"])
        ci = f",块 bootstrap [{lo:+.2f}, {hi:+.2f}]pp"
    except (KeyError, TypeError, ValueError):
        return head + "读数格式无法解析,不猜。"
    return head + f"{pp:+.2f}pp(n_days {readout.get('n_days')}{ci})。"


def render_section(seat: dict | None) -> list[str]:
    """summary §12 固定模板:标题 + 一行说明 + 表(或「无」/「未生成」)+ 读数行。"""
    out = [SECTION_TITLE,
           f"_影子面:10 日尺 `{SWING}`(D+1 开盘进 → D+10 收盘出);非 📌 finalist ∩ 评级 ≥Hold"
           " ∩ 入场≠禁止,按 conviction 排;只展示,不进任何门、账本或 prompt_"]
    if not seat or not isinstance(seat.get("rows"), list):
        out.append("未生成(观察席今天没有产出 —— 这不是「没有票」,见 `_swing_seat.json`)")
        out.append(_readout_line(None))
        return out
    if not seat["rows"]:
        out.append("无(今天的 finalist 里没有 非 📌 ∩ ≥Hold ∩ 入场≠禁止 的票)")
    else:
        out += ["| 代码 | 名称 | 评级 | 入场 | conviction |", "|---|---|---|---|---|"]
        out += [f"| {r['code']} | {r['name']} | {r['rating']} | {r['entry']} | "
                f"{r['conviction'] or '—'} |" for r in seat["rows"]]
    out.append(_readout_line(seat.get("readout")))
    return out


def brief_text(seat: dict | None, *, compact: bool = False) -> str:
    """brief ⑦ 冒号后的正文(也是 brief 边表的 `text` 锚)。"""
    if compact:
        return "见 summary §12"
    if not seat or not isinstance(seat.get("rows"), list):
        return "未生成 → summary §12"
    return f"{len(seat['rows'])} 只 → summary §12"


def banned_words(text: str) -> list[str]:
    return [w for w in BANNED_WORDS if w in text]


def section_text(summary: str) -> str | None:
    """从 summary 全文切出 §12(到下一个 `## ` 为止);不在 → None。"""
    start = summary.find(SECTION_TITLE)
    if start < 0:
        return None
    end = summary.find("\n## ", start + len(SECTION_TITLE))
    return summary[start:] if end < 0 else summary[start:end]
