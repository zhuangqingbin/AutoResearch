#!/usr/bin/env python3
"""intel 状态的**结构化事实**(确定性,零 LLM)—— A5/A6/A7 共用的单一契约。

design: docs/specs/2026-08-01-wave10-report-ops-slimdown-zerobuy-design.md §A5/A6/A7

**为什么必须三条正交字段**:07-31 复核时把「取到了稿」「稿被守卫裁过」「卡最终读了什么」
混成一个枚举,于是没人说得清 `TRIMMED` 到底意味着卡片变没变薄。三件事互不蕴含 ——
取数成功的稿可能被裁、被裁的稿卡照样读、取数失败的卡也能靠卡内网查活下来:

    acquisition           取数腿的结局      FULL / RETRIED_FULL / DEGRADED / DISABLED
    guard                 硬顶守卫的裁决    NOT_RUN / KEPT / TRIMMED / REJECTED / ABSENT
    availability_for_card 卡最终有什么可读  INTEL / CARD_FALLBACK / NONE

**DISABLED ≠ DEGRADED**(§A6):主动关掉一个功能不是事故。混在一起会让「今天 intel 关了」
和「今天 intel 挂了」在报告上长得一样,而后者需要有人看一眼。

**cap 的单一事实源**(§A5):冻结的 `user_config_echo` / `run_contract` 是唯一权威,
角色定义与 workflow prompt 都只**运输**这个值、不得另设默认真值。此前角色里硬写 `≤15`、
workflow 又注入配置 cap(当日 20),两个数同时存在而没人知道以哪个为准。

  uv run --no-sync python -m autoresearch.scan.l4.intel_status <date> <code>
"""
from __future__ import annotations

import contextlib
import hashlib
import json
import re
from dataclasses import asdict, dataclass, field
from datetime import date as _date
from pathlib import Path

from autoresearch.common import workspace as ws
from autoresearch.contracts import retry as _retry

SCHEMA_VERSION = 1

ACQUISITION = ("FULL", "RETRIED_FULL", "DEGRADED", "DISABLED")
GUARD = ("NOT_RUN", "KEPT", "TRIMMED", "REJECTED", "ABSENT")
AVAILABILITY = ("INTEL", "CARD_FALLBACK", "NONE")

# §A6:只有**瞬时**错才重试。SCHEMA_ERROR 这类结构错重试多少次都是同一个结果,
# 重试它只是把一次失败变成三次失败 + 三倍延迟。
#: 情报再搜口径的单一真身在 `contracts/retry.py`(那里并排解释了它与任务簿口径
#: 为什么**故意不同**);这里只做转出,勿在本文件另写一份。
TRANSIENT_ERRORS = _retry.INTEL_RESEARCH
MAX_ATTEMPTS = 3            # 首发 + 最多 2 次重试(§A6)

UNMEASURED = "UNMEASURED"


class IntelStatusError(ValueError):
    """状态不合契约 —— 构造时就该失败,不能等报告去解析自然语言反推。"""


@dataclass
class IntelStatus:
    schema_version: int = SCHEMA_VERSION
    code: str = ""
    acquisition: str = "DISABLED"
    guard: str = "NOT_RUN"
    availability_for_card: str = "NONE"
    claimed_queries: int | None = None
    runtime_cap: int | None = None
    hard_cap: int | None = None
    dropped_rows: int | None = None
    pretrim_ref: str | None = None
    attempts: int = 0
    error_class: str | None = None
    note: str = ""
    resumed: bool = False   # C3:同日 crash-resume 消费过本稿(披露用,不改任何判定)

    def __post_init__(self) -> None:
        for value, allowed, label in (
            (self.acquisition, ACQUISITION, "acquisition"),
            (self.guard, GUARD, "guard"),
            (self.availability_for_card, AVAILABILITY, "availability_for_card"),
        ):
            if value not in allowed:
                raise IntelStatusError(f"{label}={value!r} 不在 {allowed}")

    def to_dict(self) -> dict:
        return asdict(self)

    @property
    def degraded(self) -> bool:
        """真出事了(≠ 主动关闭)。报告的 🕳️ 降级标记只认这个。"""
        return self.acquisition == "DEGRADED"

    def headline(self) -> str:
        """卡头/直播用的一行 —— **由状态渲染,不让 LLM 自己写**(§A6)。"""
        if self.acquisition == "DISABLED":
            return "🔕 情报面已关闭(配置 l4_intel.enabled=false,非事故)"
        if self.degraded:
            return (f"🕳️ 情报面降级:{self.attempts} 次尝试仍失败"
                    f"({self.error_class or '未记错误类别'})→ 回退卡内网查")
        if self.guard == "REJECTED":
            return "🕳️ 情报稿结构不可信被整拒 → 回退卡内网查"
        if self.guard == "TRIMMED":
            return f"✂️ 情报稿{trimmed_note(self.dropped_rows)}"
        retried = "(重试后成功)" if self.acquisition == "RETRIED_FULL" else ""
        return f"🕵️ 情报面正常{retried}"


def trimmed_note(dropped_rows: int | None) -> str:
    """§A5:`TRIMMED, dropped_rows=0` 不得伪装成"真裁了内容"。

    07-31 的 000651 就是这种:自报 39 条触发超硬顶审计,但可解析事件行本就 ≤10,
    一行都没删。写成「已裁剪」会让读者以为卡片变薄了,而它没有。
    """
    if dropped_rows is None:
        return f"裁剪行数 {UNMEASURED}"
    if dropped_rows == 0:
        return "触发超硬顶审计,事件表已在上限内,未删事件行"
    return f"按时效窗裁掉 {dropped_rows} 行(T0/24h 保留)"


def runtime_cap(scan_dir: Path | str) -> int | None:
    """当日 cap 的**唯一**权威:冻结的 `user_config_echo.json`。取不到 → None(不猜默认值)。

    不读 `scan_config.jsonc` 现值:那份文件跑后可能被改,而报告必须忠实于**那次运行**。
    """
    path = Path(scan_dir) / "user_config_echo.json"
    if not path.exists():
        return None
    with contextlib.suppress(Exception):
        cfg = json.loads(path.read_text(encoding="utf-8"))
        value = (cfg.get("l4_intel") or {}).get("max_queries")
        if value is not None:
            return int(value)
    return None


def from_guard(result: dict | None, *, code: str, scan_dir: Path | str,
               enabled: bool = True, attempts: int = 1,
               error_class: str | None = None) -> IntelStatus:
    """`intel_guard.guard_intel` 的裁决 + 取数腿结局 → 结构化状态。

    这是**唯一**的构造入口:报告/直播/T1 都读它,谁也不许再去解析稿头猜状态。
    """
    cap = runtime_cap(scan_dir)
    if not enabled:
        return IntelStatus(code=code, acquisition="DISABLED", guard="NOT_RUN",
                           availability_for_card="CARD_FALLBACK", runtime_cap=cap,
                           attempts=0, note="配置关闭,非事故")
    if result is None:
        # 取数腿终失败(重试用尽)—— §A6:DEGRADED + 卡回退,记 attempts/error_class
        return IntelStatus(code=code, acquisition="DEGRADED", guard="NOT_RUN",
                           availability_for_card="CARD_FALLBACK", runtime_cap=cap,
                           attempts=attempts, error_class=error_class,
                           note="fallback=card_websearch")

    action = str(result.get("action") or "ABSENT")
    if action not in GUARD:
        raise IntelStatusError(f"未知 guard action {action!r}")
    acquisition = "RETRIED_FULL" if attempts > 1 else "FULL"
    availability = {
        "KEPT": "INTEL", "TRIMMED": "INTEL",
        "REJECTED": "CARD_FALLBACK", "ABSENT": "CARD_FALLBACK",
        "NOT_RUN": "CARD_FALLBACK",
    }[action]
    return IntelStatus(
        code=code, acquisition=acquisition, guard=action,
        availability_for_card=availability, runtime_cap=cap,
        claimed_queries=result.get("claimed"), hard_cap=result.get("hard_cap"),
        dropped_rows=result.get("dropped_rows"), pretrim_ref=result.get("pretrim_as"),
        attempts=attempts, error_class=error_class,
        note="；".join(part for part in (
            str(result.get("note") or result.get("warn") or ""),
            str((result.get("claim_events") or {}).get("diagnostic_note") or ""),
        ) if part))


def is_transient(error_class: str | None) -> bool:
    """§A6:非瞬时错不重试 —— 重试 SCHEMA_ERROR 只是把一次失败变三次。"""
    return str(error_class or "").upper() in TRANSIENT_ERRORS


# ────────────────────── A7:旧事件净分确定性归一化 ──────────────────────

_EVENT_ROW = re.compile(r"^\|\s*(\d{4}-\d{2}-\d{2})\s*\|")
_STALE_GAP_DAYS = 7


def status_cfg(cfg: dict | None = None) -> dict:
    """`scan_config.l4_intel.{stale_gap_days, max_attempts, resume_max_age_s}`(缺键 = 模块常量)。"""
    from autoresearch.scan.user_config import knob
    return {"stale_gap_days": int(knob("l4_intel", "stale_gap_days", None, _STALE_GAP_DAYS, cfg)),
            "max_attempts": int(knob("l4_intel", "max_attempts", None, MAX_ATTEMPTS, cfg)),
            "resume_max_age_s": int(knob("l4_intel", "resume_max_age_s", None, RESUME_MAX_AGE_S, cfg))}
_CATALYST_WINDOW = "催化挂"
_KNOWN_WINDOWS = ("T0", "24h", _CATALYST_WINDOW, "背景", ">1周")

# 事件表**有两代 schema**(87 份真稿实测):
#     新(36 份):日期 | 时效窗 | 事件 | 源 | 净分
#     旧(51 份):日期 | 事件 | 源 | 2日内可发酵? | 净分     ← **根本没有时效窗列**
# 所以列位置不可依赖,必须**按表头定位**。旧稿没有时效窗 → 按 §A7 就该 UNMEASURED
# (催化挂只认结构化字段,不从正文猜),但理由要写对:是「旧稿无该列」,不是「窗未知:<一整段事件正文>」
# —— 后者会让人以为 agent 把窗写乱了,去修一个不存在的问题。
_HDR_DATE = ("日期",)
_HDR_WINDOW = ("时效窗",)
_HDR_SCORE = ("净分",)


def _norm_hdr(cell: str) -> str:
    """表头单元格归一:全角括号/逗号与半角等价(真稿两种都有)。"""
    return (cell.strip().replace("（", "(").replace("）", ")")
            .replace("，", ",").replace("？", "?"))


def _header_index(lines: list[str]) -> tuple[int, dict[str, int]] | None:
    """找事件表表头 → (行号, {角色: 列下标});找不到 → None。"""
    for i, ln in enumerate(lines):
        if not ln.strip().startswith("|"):
            continue
        cells = [_norm_hdr(c) for c in ln.strip().strip("|").split("|")]
        if not any(c in _HDR_DATE for c in cells):
            continue
        idx: dict[str, int] = {}
        for j, cell in enumerate(cells):
            if cell in _HDR_DATE:
                idx["date"] = j
            elif cell in _HDR_WINDOW:
                idx["window"] = j
            elif cell in _HDR_SCORE:
                idx["score"] = j
        if "date" in idx and "score" in idx:
            return i, idx
    return None


@dataclass
class Normalization:
    code: str
    changed: list[dict] = field(default_factory=list)
    unmeasured: list[dict] = field(default_factory=list)
    source_hash: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


# 真稿实测格式(2026-07-31):净分是**小数**且可带括号注释 —— `+1.0`、`0.0`、
# `+0.0(原始+1,逾1周衰减×0)`;时效窗也带括号后缀 —— `催化挂(Q3放量未兑现)`、
# `背景(>1周,已price in)`。按"纯整数""精确等于催化挂"去匹配会把**真的催化挂**判成
# 「窗未知」而免于衰减——而免于衰减恰好是这条规则要防的事(先按真数据校准,再写规则)。
# 三种真格式并存(07-31 全量实测):`+1.0`、`+0.0(原始+1,逾1周衰减×0)`、
# `+1→0.0(>1周衰减)`。第三种是 agent **已自行衰减**并写成「原值→衰减后」——
# 生效值是箭头**之后**那个数;取箭头之前的会把一条已经衰减好的行再判成"未衰减"。
_SCORE_RE = re.compile(
    r"^\s*(?:[+-]?\d+(?:\.\d+)?\s*[→>-]+\s*)*(?P<num>[+-]?\d+(?:\.\d+)?)\s*(?:[((].*)?$")
_WINDOW_HEAD = re.compile(r"^\s*([^((]+)")


def _window_head(cell: str) -> str:
    """时效窗的**主名**(剥掉括号注释)。"""
    m = _WINDOW_HEAD.match(cell)
    return (m.group(1).strip() if m else cell.strip())


def _score_cell(cells: list[str]) -> tuple[int, float] | None:
    """事件行里的净分格 → (格下标, 数值);找不到 → None。"""
    for i in range(len(cells) - 1, -1, -1):
        m = _SCORE_RE.match(cells[i])
        if m:
            return i, float(m.group("num"))
    return None


def normalize_stale_scores(text: str, analysis_date: str, *,
                           code: str = "") -> tuple[str, Normalization]:
    """`gap>7 自然日 ∧ window≠催化挂 ∧ score≠0` → 净分改 0(§A7)。

    这不是新增判断规则 —— 角色契约**本来就要求**旧事件衰减。本函数只是把它从「lint 事后
    报警」变成「card 消费前的确定性归一化」:报警靠人改,归一化不靠人。

    `催化挂` **只认结构化时效窗字段**,不从正文猜「中报/投产」等词 —— 猜词会把一条普通
    背景新闻误当催化而免于衰减,而免于衰减恰好是这条规则要防的事。
    日期坏 / 无时效窗列 / 窗未知 / 净分不可解析 → **不改分**,记 `UNMEASURED`
    (不判 ≠ 判它合规)。
    """
    norm = Normalization(code=code,
                         source_hash=hashlib.sha256(text.encode("utf-8")).hexdigest())
    try:
        today = _date.fromisoformat(str(analysis_date)[:10])
    except (TypeError, ValueError):
        norm.unmeasured.append({"reason": "analysis_date 不可解析"})
        return text, norm

    lines = text.splitlines()
    header = _header_index(lines)
    if header is None:
        norm.unmeasured.append({"reason": "事件表表头缺失(日期/净分列定位不到)"})
        return text, norm
    _, idx = header
    has_window = "window" in idx

    out_lines = []
    for line in lines:
        if not _EVENT_ROW.match(line):
            out_lines.append(line)
            continue
        cells = line.strip().strip("|").split("|")
        if idx["score"] >= len(cells) or idx["date"] >= len(cells):
            norm.unmeasured.append({"row": line.strip()[:80], "reason": "列数与表头不符"})
            out_lines.append(line)
            continue
        raw_date = cells[idx["date"]].strip()
        try:
            gap = (today - _date.fromisoformat(raw_date)).days
        except ValueError:
            norm.unmeasured.append({"row": line.strip()[:80], "reason": "日期不可解析"})
            out_lines.append(line)
            continue
        if not has_window:
            norm.unmeasured.append({"row": line.strip()[:80],
                                    "reason": "旧稿 schema 无时效窗列,不猜催化挂"})
            out_lines.append(line)
            continue
        window = _window_head(cells[idx["window"]])
        if window not in _KNOWN_WINDOWS:
            norm.unmeasured.append({"row": line.strip()[:80],
                                    "reason": f"窗未知:{window[:24]!r}"})
            out_lines.append(line)
            continue
        m = _SCORE_RE.match(cells[idx["score"]])
        if m is None:
            norm.unmeasured.append({"row": line.strip()[:80], "reason": "净分格不可解析"})
            out_lines.append(line)
            continue
        score = float(m.group("num"))
        stale_gap = status_cfg()["stale_gap_days"]
        if gap > stale_gap and window != _CATALYST_WINDOW and score != 0:
            cells[idx["score"]] = f" 0.0(A7 归一:{score:+g} → 0,gap {gap}d) "
            norm.changed.append({"row": line.strip()[:80], "date": raw_date,
                                 "window": window, "gap_days": gap,
                                 "before": score, "after": 0.0,
                                 "reason": f"gap {gap}d > {stale_gap}d 且窗非催化挂"})
            line = "|" + "|".join(cells) + "|"
        out_lines.append(line)
    return "\n".join(out_lines) + ("\n" if text.endswith("\n") else ""), norm


def write_normalization(scan_dir: Path | str, norm: Normalization) -> Path | None:
    """归一化留痕 → `shadow/intel_normalization_<code>.json`;无改动无未测 → 不写。"""
    if not norm.changed and not norm.unmeasured:
        return None
    target = Path(scan_dir) / "shadow" / f"intel_normalization_{norm.code}.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(norm.to_dict(), ensure_ascii=False, indent=2) + "\n",
                      encoding="utf-8")
    return target


def status_path(scan_dir: Path | str, code: str) -> Path:
    return Path(scan_dir) / f"_l4_intel_status_{code}.json"


def write_status(scan_dir: Path | str, status: IntelStatus) -> Path:
    target = status_path(scan_dir, status.code)
    target.parent.mkdir(parents=True, exist_ok=True)
    temp = target.with_name(f"{target.name}.tmp")
    temp.write_text(json.dumps(status.to_dict(), ensure_ascii=False, indent=2) + "\n",
                    encoding="utf-8")
    temp.replace(target)
    return target


def load_status(scan_dir: Path | str, code: str) -> IntelStatus | None:
    path = status_path(scan_dir, code)
    if not path.exists():
        return None
    with contextlib.suppress(Exception):
        return IntelStatus(**json.loads(path.read_text(encoding="utf-8")))
    return None


# ────────────────────── C3:同日断点续传(design 2026-08-10)──────────────────────
RESUME_MAX_AGE_S = 24 * 3600   # 同 analysis_date 且 ≤24h;跨日/重放历史日 → 必须重盲搜


def _draft_path(scan_dir: Path | str, code: str) -> Path:
    code6 = str(code).split(".")[0].zfill(6)
    return Path(scan_dir) / f"_l4_intel_{code6}.md"


def resumable(scan_dir: Path | str, code: str, *, now: float | None = None) -> bool:
    """同日 crash-resume 判定:稿在 + status 说这稿可用 + 两者都不陈旧。

    与 R5(跨日卡 TTL 复用退役)的边界:这里只认「本 analysis_date 目录里、24h 内、
    guard 未拒」的稿 —— 语义与任务簿对卡的 VERIFIED_SUCCESS 跳过同族(crash-resume),
    不是跨日新鲜度妥协。条件不满足一律 False(重盲搜),失败闭合。
    """
    st = load_status(scan_dir, code)
    if st is None or st.acquisition != "FULL":
        return False
    if st.availability_for_card != "INTEL" or st.error_class:
        return False
    draft = _draft_path(scan_dir, code)
    if not draft.is_file() or draft.stat().st_size == 0:
        return False
    import time as _time
    ref = _time.time() if now is None else now
    for p in (draft, status_path(scan_dir, code)):
        if ref - p.stat().st_mtime > status_cfg()["resume_max_age_s"]:
            return False
    return True


def mark_resumed(scan_dir: Path | str, code: str) -> None:
    """把「这稿被续传消费过」落进 status(报告/T1 可见,不伪装成新鲜盲搜)。"""
    st = load_status(scan_dir, code)
    if st is None:
        return
    st.resumed = True
    write_status(scan_dir, st)


def main(argv: list[str] | None = None) -> int:
    import argparse

    ap = argparse.ArgumentParser(
        description="intel 结构化状态(guard 裁决 + 取数腿结局 + A7 旧事件归一化)")
    ap.add_argument("date")
    ap.add_argument("code")
    ap.add_argument("--scan-dir", default=None)
    ap.add_argument("--disabled", action="store_true", help="配置关闭(非事故)")
    ap.add_argument("--attempts", type=int, default=1)
    ap.add_argument("--error-class", default=None)
    ap.add_argument("--normalize", action="store_true", help="同时跑 A7 旧事件净分归一化")
    args = ap.parse_args(argv)

    scan_dir = Path(args.scan_dir) if args.scan_dir else ws.scan_root() / args.date
    result = None
    if not args.disabled and args.error_class is None:
        from autoresearch.scan.l4.intel_guard import configured_soft_cap, guard_intel
        result = guard_intel(scan_dir, args.code, soft_cap=configured_soft_cap())

    if args.normalize and result is not None and result.get("action") in ("KEPT", "TRIMMED"):
        from autoresearch.scan.l4.intel_guard import intel_path
        src = intel_path(scan_dir, args.code)
        with contextlib.suppress(Exception):
            body = src.read_text(encoding="utf-8")
            fixed, norm = normalize_stale_scores(body, args.date, code=args.code)
            if fixed != body:
                src.write_text(fixed, encoding="utf-8")
            write_normalization(scan_dir, norm)

    status = from_guard(result, code=args.code, scan_dir=scan_dir,
                        enabled=not args.disabled, attempts=args.attempts,
                        error_class=args.error_class)
    write_status(scan_dir, status)
    print(json.dumps(status.to_dict(), ensure_ascii=False))
    return 0          # 情报是辅助面:状态再差也不拒票


if __name__ == "__main__":
    raise SystemExit(main())
