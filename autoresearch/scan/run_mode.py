#!/usr/bin/env python3
"""运行模式的**结构化事实**(确定性,零 LLM)—— 四态,不再从产物空否反推。

design: docs/specs/2026-08-01-wave10-report-ops-slimdown-zerobuy-design.md §A2

此前只有「哨兵 / 非哨兵」两态,而哨兵档一律**整条跳过 L3/L4** —— 包括持仓票。
于是材料枯竭的日子里,持仓复核也一起没了(07-31 靠 `force_full` 手工拉满才跑出 10 张卡,
代价是把全市场选股也一起跑了)。中间档 `SENTINEL_PINNED` 就是这条缺口:

    FULL             正常全扫
    FORCED_FULL      哨兵判材料枯竭,但人工 override 拉满(诚实标注,不假装是 FULL)
    SENTINEL_EMPTY   哨兵 + 无持仓 → 真的什么都不跑
    SENTINEL_PINNED  哨兵 + 有持仓 → **只跑持仓 L4 全链**,不出全市场选股结论

**为什么必须是文件而不是推断**(§R9):「finalists 为空」既可能是哨兵档没选,也可能是
L3 选空了,还可能是产物写盘失败。三种情形对读者的意义完全不同,而从空否反推分不出来。
assemble / GATE4 / report **只读这份文件**。

`SENTINEL_PINNED` 的候选**只来自冻结的 run_contract 快照**,不读 `pinned.jsonc` ——
那份配置跑后可能被改,而报告必须忠实于**那次运行**(同 `report_sections._pinned_section`
的 run-time-truth 教训)。

  uv run --no-sync python -m autoresearch.scan.run_mode <date> --decide
"""
from __future__ import annotations

import contextlib
import json
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path

SCHEMA_VERSION = 1

FULL = "FULL"
FORCED_FULL = "FORCED_FULL"
SENTINEL_EMPTY = "SENTINEL_EMPTY"
SENTINEL_PINNED = "SENTINEL_PINNED"
MODES = (FULL, FORCED_FULL, SENTINEL_EMPTY, SENTINEL_PINNED)

# 哨兵档下 GATE2 的正当缺席理由 —— 不是"通过",是"不适用"。伪造通过会让
# 「L3 跑过且选空」和「L3 根本没跑」在账上长得一样。
GATE2_SKIP_REASON = "sentinel_pinned_no_l3"


class RunModeError(ValueError):
    """模式不合契约 —— 写入时就该失败。"""


@dataclass
class RunMode:
    schema_version: int = SCHEMA_VERSION
    mode: str = FULL
    sentinel_reason: str | None = None
    pinned_snapshot_hash: str | None = None
    pinned_codes: list[str] = field(default_factory=list)
    has_selection_conclusion: bool = True
    has_l3_judgment: bool = True
    created_at: str = ""
    contract_hash: str | None = None

    def __post_init__(self) -> None:
        if self.mode not in MODES:
            raise RunModeError(f"mode={self.mode!r} 不在 {MODES}")

    def to_dict(self) -> dict:
        return asdict(self)

    @property
    def is_sentinel(self) -> bool:
        return self.mode in (SENTINEL_EMPTY, SENTINEL_PINNED)

    def banner(self) -> str:
        """报告醒目标注 —— 由状态渲染,不让人从 finalists 数量猜。"""
        if self.mode == SENTINEL_PINNED:
            return (f"🛡️ **哨兵档:仅持仓复核,无全市场选股结论**"
                    f"({len(self.pinned_codes)} 只持仓走完整 L4 链;"
                    f"判据:{self.sentinel_reason or '—'})")
        if self.mode == SENTINEL_EMPTY:
            return (f"🛡️ **哨兵档:本次不跑选股也无持仓可复核**"
                    f"(判据:{self.sentinel_reason or '—'})")
        if self.mode == FORCED_FULL:
            return (f"⚠️ **人工 override(force_full)**:确定性判据判「材料枯竭」"
                    f"({self.sentinel_reason or '—'}),买单侧期望低 —— 本次仍按全扫执行")
        return ""


def decide(*, sentinel_level: str, force_full: bool, pinned_codes: list[str],
           sentinel_reason: str | None = None,
           pinned_snapshot_hash: str | None = None,
           contract_hash: str | None = None,
           now: datetime | None = None) -> RunMode:
    """四态判定。`SENTINEL_PINNED` = sentinel ∧ 冻结快照 pinned 非空 ∧ 未 force_full。"""
    stamp = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    codes = sorted({str(c).strip().split(".")[0].zfill(6) for c in pinned_codes if c})
    common = {
        "sentinel_reason": sentinel_reason,
        "pinned_snapshot_hash": pinned_snapshot_hash,
        "pinned_codes": codes,
        "created_at": stamp.isoformat(timespec="seconds").replace("+00:00", "Z"),
        "contract_hash": contract_hash,
    }
    is_sentinel = str(sentinel_level) == "sentinel"
    if not is_sentinel:
        return RunMode(mode=FULL, has_selection_conclusion=True,
                       has_l3_judgment=True, **common)
    if force_full:
        # 诚实标注:它**不是** FULL —— 确定性判据说了材料枯竭,是人推翻的。
        return RunMode(mode=FORCED_FULL, has_selection_conclusion=True,
                       has_l3_judgment=True, **common)
    if codes:
        # 中间档:跑持仓 L4 全链,但**没有**全市场选股结论、**没有**L3 判断。
        return RunMode(mode=SENTINEL_PINNED, has_selection_conclusion=False,
                       has_l3_judgment=False, **common)
    return RunMode(mode=SENTINEL_EMPTY, has_selection_conclusion=False,
                   has_l3_judgment=False, **common)


def path_for(scan_dir: Path | str) -> Path:
    return Path(scan_dir) / "run_mode.json"


def write(scan_dir: Path | str, mode: RunMode) -> Path:
    target = path_for(scan_dir)
    target.parent.mkdir(parents=True, exist_ok=True)
    temp = target.with_name(f"{target.name}.tmp")
    temp.write_text(json.dumps(mode.to_dict(), ensure_ascii=False, indent=2) + "\n",
                    encoding="utf-8")
    temp.replace(target)
    return target


def load(scan_dir: Path | str) -> RunMode | None:
    """缺文件 → None。**调用方不得因此推断模式** —— 缺文件就是"不知道",不是 FULL。"""
    target = path_for(scan_dir)
    if not target.exists():
        return None
    with contextlib.suppress(Exception):
        return RunMode(**json.loads(target.read_text(encoding="utf-8")))
    return None


# ────────────────── 从冻结快照造 pinned-only finalists ──────────────────

_FINALIST_COLS = ["code", "name", "sector", "lane", "selection_source",
                  "l3_judged", "conviction", "data_missing"]


def pinned_from_contract(scan_dir: Path | str) -> tuple[list[str], str | None]:
    """冻结 `run_contract.json` 里的 pinned.kept 代码 + 快照 hash。

    **不读 `pinned.jsonc`**:那份配置跑后可能被改,报告必须忠实于那次运行
    (`report_sections._pinned_section` 的 run-time-truth 同款教训)。
    """
    path = Path(scan_dir) / "run_contract.json"
    if not path.exists():
        return [], None
    with contextlib.suppress(Exception):
        payload = json.loads(path.read_text(encoding="utf-8"))
        pinned = payload.get("pinned") or {}
        kept = pinned.get("kept") or []
        codes = [str(k.get("code") if isinstance(k, dict) else k) for k in kept]
        return ([c.split(".")[0].zfill(6) for c in codes if c],
                payload.get("contract_hash") or payload.get("config_hash"))
    return [], None


def build_pinned_finalists(scan_dir: Path | str, codes: list[str]):
    """pinned-only finalists 帧:用当日 L2 行补 name/sector;缺行 → 占位 + `data_missing=True`。

    缺行不是"跳过这只票" —— 持仓复核照跑,只是把"我们对它的确定性字段一无所知"写在脸上。
    """
    import pandas as pd

    scan = Path(scan_dir)
    l2: dict[str, dict] = {}
    with contextlib.suppress(Exception):
        frame = pd.read_csv(scan / "L2_gbdt_top200.csv", dtype={"code": str})
        frame["code"] = frame["code"].astype(str).str.zfill(6)
        l2 = {r["code"]: r for r in frame.to_dict("records")}

    rows = []
    for code in codes:
        row = l2.get(code)
        rows.append({
            "code": code,
            "name": (row or {}).get("name", ""),
            "sector": (row or {}).get("sector", (row or {}).get("industry", "")),
            "lane": "pinned",
            "selection_source": "sentinel_pinned",
            "l3_judged": False,
            "conviction": (row or {}).get("gbdt_score", ""),
            "data_missing": row is None,
        })
    return pd.DataFrame(rows, columns=_FINALIST_COLS)


def write_pinned_finalists(scan_dir: Path | str, codes: list[str]) -> Path:
    frame = build_pinned_finalists(scan_dir, codes)
    target = Path(scan_dir) / "finalists.csv"
    temp = target.with_name(f"{target.name}.tmp")
    frame.to_csv(temp, index=False)
    temp.replace(target)
    return target


def main(argv: list[str] | None = None) -> int:
    import argparse

    ap = argparse.ArgumentParser(description="运行模式四态判定(确定性)")
    ap.add_argument("date")
    ap.add_argument("--scan-root", default="context/scan")
    ap.add_argument("--sentinel-level", default="full")
    ap.add_argument("--sentinel-reason", default=None)
    ap.add_argument("--force-full", action="store_true")
    ap.add_argument("--decide", action="store_true",
                    help="判定并落 run_mode.json;SENTINEL_PINNED 时同时写 pinned-only finalists")
    args = ap.parse_args(argv)

    scan_dir = Path(args.scan_root) / args.date
    codes, contract_hash = pinned_from_contract(scan_dir)
    mode = decide(sentinel_level=args.sentinel_level, force_full=args.force_full,
                  pinned_codes=codes, sentinel_reason=args.sentinel_reason,
                  pinned_snapshot_hash=contract_hash, contract_hash=contract_hash)
    if args.decide:
        write(scan_dir, mode)
        if mode.mode == SENTINEL_PINNED:
            write_pinned_finalists(scan_dir, mode.pinned_codes)
    print(json.dumps(mode.to_dict(), ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
