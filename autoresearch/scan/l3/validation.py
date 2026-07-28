"""L3 numeric-citation validation and narrow repair."""
from __future__ import annotations

import contextlib
import json
import re
from pathlib import Path

import pandas as pd

_NUM_RE = re.compile(r"-?\d+(?:\.\d+)?")
_DATE_TOKEN_RE = re.compile(r"\d{4}-\d{1,2}-\d{1,2}|\d{1,2}-\d{1,2}")
_YEAR_TOKEN_RE = re.compile(r"(?<!\d)(?:19|20)\d{2}(?!\d)")
_CODE_TOKEN_RE = re.compile(r"(?<!\d)\d{6}(?!\d)")
_PERIOD_SUFFIX = ("年", "月", "日", "周", "季")
_COUNT_SUFFIX = ("只", "家", "次", "条", "名", "位", "个", "档", "笔", "轮")
_IDENT_CHAR_RE = re.compile(r"[A-Za-z_]")
_FRACTION_RE = re.compile(
    r"(?<!\d)(\d+(?:\.\d+)?)\s*/\s*(\d+(?:\.\d+)?)(?!\d)"
)
_MARKET_CTX = (
    "全市场", "全表", "全A", "全 A", "大盘", "市场中位", "行业", "板块",
    "同业", "簇", "指数", "两融", "北向",
)
_MARKET_CTX_BACK = 16


def _fraction_exempt_values(text: str) -> set[float]:
    """thesis 里 `a/b` 分数的操作数与其百分比值 —— 这些数字表里根本不存在对应列。"""
    out: set[float] = set()
    for m in _FRACTION_RE.finditer(text or ""):
        a, b = float(m.group(1)), float(m.group(2))
        out.update({a, b})
        if b:
            out.add(round(a / b * 100.0, 2))
    return out

def _market_pool(scan_dir: Path) -> list[float]:
    """市场/行业级数值池(`market_pack.json` 的全部数值,~126 个)。

    Wave7 B′-c:lint 原本只拿**本票 L2 行**当参照,于是 thesis 里一切合法的跨文件引用
    ——「在全市场 60 日中位 -17.68% 的对照下」「半导体今日行业资金流 +489 亿」——都被记成
    「引用数字与表不符」。可这些数字正是我们**要求** L3 读的地形段(market_view/行业 brief
    都由 market_pack 派生),把它们判成违规等于惩罚 agent 按指令办事。
    用结构化 pack 而不是几份 md 的自由文本当池:后者上千个数字会把 lint 稀释成橡皮图章,
    前者边界清楚(126 个)且与地形段同源。失败一律返回空池(lint 是 advisory,不阻断)。
    """
    out: list[float] = []

    def _walk(o) -> None:
        if isinstance(o, dict):
            for v in o.values():
                _walk(v)
        elif isinstance(o, list):
            for v in o:
                _walk(v)
        elif isinstance(o, (int, float)) and not isinstance(o, bool):
            out.append(float(o))

    with contextlib.suppress(Exception):
        _walk(json.loads((scan_dir / "market_pack.json").read_text(encoding="utf-8")))
    return out

def _thesis_number_tokens_pos(text: str) -> list[tuple[str, int]]:
    r"""thesis 里「待核实」的数字 token:过滤 4 位年份 / 6 位代码 / `07-15`(或 `2026-07-15`)形
    日期 / `N年|月|日|周|季` 窗口标签(如"60日涨12%"的 60 是窗口)/ 紧贴字母或下划线的数字
    (如引用列名 `pct_60d`/`rsi6`/`cmf_20` 时嵌在标识符里的窗口数——07-08 真实数据冒烟逮到
    "pct_60d +21.95" 的 60、"rsi6 52.51" 的 6 被错当数字核对)后,
    `re.findall(r"-?\d+(?:\.\d+)?", ...)` 逐个取出。"""
    if not text:
        return []
    text = text.replace("−", "-")           # U+2212 全角负号归一(pf 列"主力−"同字形,防丢符号误报)
    # 掩码保长(Wave7 B′-c):把匹配段替换成**等长**空格而不是单个空格,token 在 t 里的位置
    # 才等于它在原文里的位置 —— 语境闸(下面 _has_market_context)要按位置取左窗,位置一偏
    # 就会取错窗口。对原有过滤行为无影响(前后字符判据看到的仍是空白)。
    _blank = lambda m: " " * len(m.group())      # noqa: E731
    t = _DATE_TOKEN_RE.sub(_blank, text)
    t = _YEAR_TOKEN_RE.sub(_blank, t)
    t = _CODE_TOKEN_RE.sub(_blank, t)
    out: list[tuple[str, int]] = []
    for m in _NUM_RE.finditer(t):
        # 后缀判定要跨过空格:agent 写的是「60 日中位」「近 10 日回购」「49 只」——中文数字与
        # 量词间加空格是它一贯的排版习惯,而原判据只看紧邻的下一个字符,于是**窗口标签过滤对
        # 带空格的写法整体失效**(2026-07-27 的 15 处告警里 5 处是这一个 off-by-one)。
        after = t[m.end():m.end() + 4].lstrip()[:1]
        if after in _PERIOD_SUFFIX or after in _COUNT_SUFFIX:
            continue
        before = t[m.start() - 1:m.start()] if m.start() > 0 else ""
        after = t[m.end():m.end() + 1]
        if _IDENT_CHAR_RE.match(before) or _IDENT_CHAR_RE.match(after):
            continue
        out.append((m.group(), m.start()))
    return out

def _thesis_number_tokens(text: str) -> list[str]:
    """同上,只要 token(既有调用点/测试的签名不变)。"""
    return [tok for tok, _ in _thesis_number_tokens_pos(text)]

def _has_market_context(text: str, pos: int) -> bool:
    return any(w in text[max(0, pos - _MARKET_CTX_BACK):pos] for w in _MARKET_CTX)

def _complement_pool(l2_row) -> list[float]:
    """`100 - winner_rate` —— 唯一放行的口算派生式(「winner_rate 73.92 未满(尚有 26%
    套牢盘)」)。只对这一列开:实测对全池开 `100-v` 会把误放行率再抬 7pp,而 rubric 里
    需要口算补数的只有筹码空间这一处。"""
    try:
        wr = float(l2_row["winner_rate"]) if l2_row is not None else float("nan")
    except (TypeError, ValueError, KeyError):
        return []
    return [100.0 - wr] if wr == wr else []

def _row_numeric_pool(pick: dict, l2_row) -> list[float]:
    """该票行的数值集合:L2 csv 该码全部数值列(`code` 本身除外)+ judged 行自身
    pct_60d/conviction(容差匹配用)。坏值/非数容错跳过。"""
    pool: list[float] = []
    if l2_row is not None:
        for k, v in l2_row.items():
            if k == "code":
                continue
            try:
                fv = float(v)
            except (TypeError, ValueError):
                continue
            if fv == fv:
                pool.append(fv)
    for key in ("pct_60d", "conviction"):
        try:
            fv = float(pick.get(key))
        except (TypeError, ValueError):
            continue
        if fv == fv:
            pool.append(fv)
    return pool

def _approx_in_pool(token_val: float, pool: list[float]) -> bool:
    """±1% 相对或 ±0.1 绝对容差;百分数与小数互认(×100/÷100 都试一遍)。

    另认 `100 - v` 一种派生式(Wave7 B′-c):「winner_rate 73.92 未满(尚有 26% 套牢盘)」
    里的 26 是 100−73.92 的口算,不是另一个待核数字 —— 这是 rubric 明确鼓励的「筹码空间」
    表述,却每次都被记违规。只放行这一种口算,不做通用算术求解(那会把 lint 稀释掉)。
    """
    for v in pool:
        for cv in (v, v * 100.0, v * 0.01):
            if abs(token_val - cv) <= 0.1:
                return True
            if cv and abs(token_val - cv) / abs(cv) <= 0.01:
                return True
    return False

def _lint_failures(picks: list[dict], scan_dir: Path) -> list[dict]:
    """结构化 lint 失败；`lint_judged` 和 repair merge 共用同一谓词。"""
    l2_by_code: dict[str, object] = {}
    l2p = scan_dir / "L2_gbdt_top200.csv"
    if l2p.exists():
        l2 = pd.read_csv(l2p, dtype={"code": str})
        l2["code"] = l2["code"].astype(str).str.zfill(6)
        l2_by_code = {r["code"]: r for _, r in l2.iterrows()}
    market_pool = _market_pool(scan_dir)
    bad: list[dict] = []
    for pick in picks:
        code = str(pick.get("code", "")).zfill(6)
        thesis = str(pick.get("thesis") or "")
        catalyst = str(pick.get("catalyst") or "")
        l2_row = l2_by_code.get(code)
        pool = _row_numeric_pool(pick, l2_row) + _complement_pool(l2_row)
        frac = _fraction_exempt_values(thesis)
        norm_thesis = thesis.replace("−", "-")
        for tok, pos in _thesis_number_tokens_pos(thesis):
            try:
                tv = float(tok)
            except ValueError:
                continue
            if _approx_in_pool(tv, pool) or tok in catalyst:
                continue
            if any(abs(tv - f) <= 0.1 for f in frac):
                continue
            if _has_market_context(norm_thesis, pos) and _approx_in_pool(tv, market_pool):
                continue
            bad.append({"code": code, "token": tok, "position": pos})
    return bad

def lint_judged(date: str, root: Path | None = None) -> dict:
    """thesis 数字机检(确定性 lint,零 LLM):每条 thesis 的数字 token(过滤年份/代码/日期/
    N日窗口标签)须能在该票 L2 数值列(±1% 相对或 ±0.1 绝对容差,百分数/小数互认)或
    `catalyst` 字段里找到,否则记 `code:数字` 入 reason → `ok=False`。workflow 据此打回一次
    自修(`gate('l3-lint', ...)`,一次打回上限,修复后不再二检)。

    CLI(GATE 惯例):`python -m autoresearch.scan.agents.l3_select lint <date>` 打一行 JSON。
    """
    base = Path(root) if root else Path("context/scan")
    scan_dir = base / date
    judged_path = scan_dir / "_l3_judged.json"
    if not judged_path.exists():
        return {"ok": False, "reason": f"{judged_path} 缺失"}
    picks = json.loads(judged_path.read_text(encoding="utf-8"))
    failures = _lint_failures(picks, scan_dir)
    bad = [f"{row['code']}:{row['token']}" for row in failures]
    if bad:
        return {"ok": False, "reason": "; ".join(bad), "failures": failures}
    return {"ok": True, "reason": "ok", "failures": []}

def _atomic_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(f"{path.name}.tmp")
    temp.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    temp.replace(path)

def _json_safe_row(row: object) -> dict:
    if row is None:
        return {}
    if isinstance(row, pd.Series):
        values = row.to_dict()
    else:
        values = dict(row)
    out = {}
    for key, value in values.items():
        if pd.isna(value):
            out[str(key)] = None
        elif hasattr(value, "item"):
            out[str(key)] = value.item()
        else:
            out[str(key)] = value
    return out

def build_repair_pack(date: str, root: Path | None = None) -> dict:
    """只把 lint 失败行和本票合法证据写入局部修复包。"""
    base = Path(root) if root else Path("context/scan")
    scan_dir = base / date
    judged_path = scan_dir / "_l3_judged.json"
    picks = json.loads(judged_path.read_text(encoding="utf-8"))
    if not isinstance(picks, list):
        raise ValueError("_l3_judged.json root must be a list")
    failures = _lint_failures(picks, scan_dir)
    codes = list(dict.fromkeys(row["code"] for row in failures))
    picks_by_code = {
        str(row.get("code", "")).zfill(6): row for row in picks
    }
    l2_by_code: dict[str, object] = {}
    l2p = scan_dir / "L2_gbdt_top200.csv"
    if l2p.exists():
        l2 = pd.read_csv(l2p, dtype={"code": str})
        l2["code"] = l2["code"].astype(str).str.zfill(6)
        l2_by_code = {r["code"]: r for _, r in l2.iterrows()}
    rows = []
    for code in codes:
        original = picks_by_code[code]
        rows.append({
            "code": code,
            "original": {
                "code": code,
                "thesis": original.get("thesis"),
                "catalyst": original.get("catalyst"),
            },
            "invalid_tokens": [
                row["token"] for row in failures if row["code"] == code
            ],
            "evidence": _json_safe_row(l2_by_code.get(code)),
        })
    pack = {
        "schema_version": 1,
        "date": date,
        "codes": codes,
        "rows": rows,
        "patch_schema": {
            "type": "array",
            "items": {
                "type": "object",
                "required": ["code", "thesis"],
                "additionalProperties": False,
            },
        },
    }
    _atomic_json(scan_dir / "_l3_repair_pack.json", pack)
    prompt = "\n".join([
        "# L3 thesis 局部修复包",
        "",
        "只修下列 JSON 中列出的失败行。不得读取或重写整份 `_l3_table.md` / "
        "`_l3_judged.json`，不得改 conviction、finalist、risk、catalyst 或任何其它字段。",
        "数字只能取自本行 `evidence`；拿不准就删掉具体数字改成定性措辞。",
        f"输出必须写到 `context/scan/{date}/_l3_repair_patch.json`，根为数组，"
        "每项严格只有 `code` 与 `thesis`。",
        "",
        "```json",
        json.dumps({"codes": codes, "rows": rows}, ensure_ascii=False, indent=2),
        "```",
        "",
    ])
    (scan_dir / "_l3_repair_prompt.md").write_text(prompt, encoding="utf-8")
    return {**pack, "prompt": prompt}

def apply_repair_patch(date: str, root: Path | None = None) -> dict:
    """验证局部 patch 后原子 merge；未请求行对象原样保留。"""
    base = Path(root) if root else Path("context/scan")
    scan_dir = base / date
    pack = json.loads((scan_dir / "_l3_repair_pack.json").read_text(encoding="utf-8"))
    patch = json.loads((scan_dir / "_l3_repair_patch.json").read_text(encoding="utf-8"))
    if not isinstance(patch, list):
        raise ValueError("repair patch root must be a list")
    requested = {str(code).zfill(6) for code in pack.get("codes") or []}
    seen: set[str] = set()
    by_code: dict[str, str] = {}
    for row in patch:
        if not isinstance(row, dict) or set(row) != {"code", "thesis"}:
            raise ValueError("repair patch fields must be exactly code/thesis")
        code = str(row["code"]).zfill(6)
        if code not in requested:
            raise ValueError(f"unrequested repair code:{code}")
        if code in seen:
            raise ValueError(f"duplicate repair code:{code}")
        if not isinstance(row["thesis"], str) or not row["thesis"].strip():
            raise ValueError(f"empty thesis:{code}")
        seen.add(code)
        by_code[code] = row["thesis"].strip()
    missing = requested - seen
    if missing:
        raise ValueError(f"missing requested repair codes:{sorted(missing)}")

    judged_path = scan_dir / "_l3_judged.json"
    original = json.loads(judged_path.read_text(encoding="utf-8"))
    candidate = []
    for row in original:
        code = str(row.get("code", "")).zfill(6)
        if code in by_code:
            updated = dict(row)
            updated["thesis"] = by_code[code]
            candidate.append(updated)
        else:
            candidate.append(row)
    remaining = [
        row for row in _lint_failures(candidate, scan_dir)
        if row["code"] in requested
    ]
    if remaining:
        reason = "; ".join(f"{row['code']}:{row['token']}" for row in remaining)
        raise ValueError(f"repair still fails lint:{reason}")
    _atomic_json(judged_path, candidate)
    return {
        "patched": len(by_code),
        "preserved": len(candidate) - len(by_code),
        "codes": sorted(by_code),
    }
