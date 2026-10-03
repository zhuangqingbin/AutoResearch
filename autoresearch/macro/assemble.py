"""Assemble macro-research per-agent markdown into one reports/macro/<YYYYMMDD>/<HHMM>_summary.md.

Two-tier like autoresearch.analyze.assemble, plus a 中观 tier:
  ▸ 决策主线   decision / variant / crossfire / calendar / premortem (+debate)
  ▸ 中观落地   sector_map / flows / sentiment / themes
  ▸ 证据附录   regional(us/china/global) · crossasset(rates/fx/equities/commodities/crypto[/credit])
               · sino-us(divergence/desync/geopolitics/relative) · meso_evidence(industry_cycle)

The decision (cross-asset) and sector_map (A股行业) tables each carry one keyed
`- <KEY>: **Rating**: <band>` line per row; parse_allocation validates each
extracted value against the five-tier vocabulary before publication.

Usage:
    python -m autoresearch.macro.assemble context/macro/<YYYY-MM-DD>
    # → reports/macro/<YYYYMMDD>/<HHMM>_summary.md   (HHMM = 组装时本地时间)
"""
import argparse
import re
from collections.abc import Collection, Mapping
from datetime import datetime
from pathlib import Path

from autoresearch.agents.utils.rating import RATINGS_5_TIER
from autoresearch.common import workspace as ws

DECISION_REL = "1_spine/decision.md"
SECTOR_MAP_REL = "2_meso/sector_map.md"

SPINE = [
    ("S2 · 投资逻辑 & 预期差", [("Variant View", "1_spine/variant.md", False)]),
    ("S3 · 中美对撞 & 情景矩阵", [("Crossfire & Scenarios", "1_spine/crossfire.md", False)]),
    ("S4 · 催化剂日历 & 触发位", [("Catalyst Calendar", "1_spine/calendar.md", False)]),
    ("S5 · 风险 · 认错 · 监控", [
        ("Pre-Mortem & Monitoring", "1_spine/premortem.md", False),
        ("Risk Debate", "1_spine/debate.md", True),
    ]),
]
MESO = [
    ("M1 · A股行业配置图", [("Sector Allocation Map", "2_meso/sector_map.md", False)]),
    ("M2 · 资金 & 游资", [("Flows & Hot Money", "2_meso/flows.md", False)]),
    ("M3 · 情绪周期 & 涨停结构", [("Sentiment Cycle", "2_meso/sentiment.md", False)]),
    ("M4 · 题材 & 风格轮动", [("Themes & Style", "2_meso/themes.md", False)]),
]
APPENDIX = [
    ("A · 区域宏观", [
        ("United States", "3_regional/us.md", False),
        ("China", "3_regional/china.md", False),
        ("Global (EU/Japan/EM)", "3_regional/global.md", False),
    ]),
    ("B · 跨资产 & 传导", [
        ("Rates & Central Banks", "4_crossasset/rates.md", False),
        ("FX (USD/CNY/JPY)", "4_crossasset/fx.md", False),
        ("Equities (US vs A/H)", "4_crossasset/equities.md", False),
        ("Commodities & Gold", "4_crossasset/commodities.md", False),
        ("Crypto", "4_crossasset/crypto.md", False),
        ("Credit & Liquidity", "4_crossasset/credit.md", True),
    ]),
    ("C · 中美专题", [
        ("Monetary Divergence", "5_sinous/divergence.md", False),
        ("Growth/Inflation Desync", "5_sinous/desync.md", False),
        ("Trade / Tariff / Geopolitics", "5_sinous/geopolitics.md", False),
        ("Relative Assets & Flows", "5_sinous/relative.md", False),
    ]),
    ("D · 中观明细", [
        ("Industry Cycle Bridge", "6_meso_evidence/industry_cycle.md", True),
    ]),
]

SPINE_BANNER = "**═══════════ 决策主线 · Decision Spine(读它就能配置)═══════════**"
MESO_BANNER = "**═══════════ 中观落地 · A股行业/资金/情绪═══════════**"
APPENDIX_BANNER = "**═══════════ 证据附录 · Evidence Appendix═══════════**"


def _allocation_key(value: str) -> bool:
    return isinstance(value, str) and bool(re.fullmatch(r"[\w][\w ()（）·/&.+-]*", value))


# The FULL playbook's cross-asset scope; a credit evidence section is optional,
# but the allocation table still declares its stance on credit.
CROSS_ASSET_KEYS = (
    "OVERALL 风险档", "美债", "美股", "A股·港股", "USD", "CNY", "JPY", "黄金", "大宗", "加密(BTC)", "信用",
)


def allocation_scope(data_text: str, *, include_sectors: bool = True) -> dict[str, tuple[str, ...]]:
    """Derive the FULL keyset from the playbook and this run's deterministic data.

    Industry scope is the rendered fund-flow population, not all SW industries
    and never the model's sector_map output. Missing scope fails closed.
    """
    scope = {DECISION_REL: CROSS_ASSET_KEYS}
    if not include_sectors:
        return scope
    sectors: list[str] = []
    in_flow = False
    in_table = False
    for line in data_text.splitlines():
        stripped = line.strip()
        if stripped.startswith("**行业资金净流入("):
            in_flow, in_table = True, False
            continue
        if not in_flow:
            continue
        if not stripped.startswith("|"):
            if in_table or stripped:
                in_flow = False
            continue
        cells = [cell.strip() for cell in stripped.strip("|").split("|")]
        if cells[0] == "行业" and any("净流入" in cell for cell in cells[1:]):
            in_table = True
            continue
        if not in_table or cells[0] in {"…", "..."} or re.fullmatch(r"[-: ]+", cells[0]):
            continue
        if len(cells) != 3 or not _allocation_key(cells[0]):
            raise ValueError("allocation scope has malformed industry flow row")
        if cells[0] not in sectors:
            sectors.append(cells[0])
    if not sectors:
        raise ValueError("allocation scope unavailable: deterministic industry flow table missing or empty")
    scope[SECTOR_MAP_REL] = tuple(sectors)
    return scope


def resolve_allocation_scope(
    root: Path,
    expected_keys: Mapping[str, Collection[str]] | None = None,
    *,
    include_sectors: bool = True,
) -> dict[str, tuple[str, ...]]:
    """Publishing default is deterministic scope; explicit callers may supply a subset.

    Unscoped ``parse_allocation`` remains a historical-text reader. It is not a
    publishing fallback. A spine-only state still needs no other report sections.
    """
    required = {DECISION_REL, SECTOR_MAP_REL} if include_sectors else {DECISION_REL}
    if expected_keys is None:
        data_path = Path(root) / "data.md"
        if include_sectors and not data_path.is_file():
            raise ValueError("allocation scope unavailable: data.md is required for sector keys")
        data = data_path.read_text(encoding="utf-8") if include_sectors else ""
        return allocation_scope(data, include_sectors=include_sectors)
    if not isinstance(expected_keys, Mapping) or not required <= set(expected_keys):
        raise ValueError("allocation scope is missing required table keys")
    if set(expected_keys) - {DECISION_REL, SECTOR_MAP_REL}:
        raise ValueError("allocation scope contains unknown table keys")
    scope = {}
    for relative, keys in expected_keys.items():
        if not isinstance(keys, Collection) or isinstance(keys, str):
            raise ValueError("invalid expected allocation keys")
        if any(not _allocation_key(key) for key in keys) or len(set(keys)) != len(keys):
            raise ValueError("invalid or duplicate expected allocation keys")
        scope[relative] = tuple(keys)
    return scope


def parse_allocation(text: str, expected_keys: Collection[str] | None = None) -> dict[str, str]:
    """Validate keyed rows; a declared keyset comes from the deterministic caller.

    Empty prose returns an empty table. Publishing callers require nonempty tables.
    No global industry universe is inferred when ``expected_keys`` is absent.
    """
    if expected_keys is not None:
        if isinstance(expected_keys, str) or any(not _allocation_key(key) for key in expected_keys):
            raise ValueError("invalid expected allocation keys")
        if len(set(expected_keys)) != len(expected_keys):
            raise ValueError("duplicate expected allocation keys")
    out = {}
    for line in text.splitlines():
        if not re.search(r"\bRating\b", line, re.IGNORECASE):
            continue
        match = re.fullmatch(
            r"\s*[-*]\s+(.+?)\s*[:：]\s*(?:\*\*)?Rating(?:\*\*)?\s*[:：]\s*"
            r"(?:\*\*)?([A-Za-z]+)(?:\*\*)?\s*(?:[—–-]\s+.+)?", line, re.IGNORECASE,
        )
        if not match:
            raise ValueError(f"malformed allocation rating line: {line}")
        key, raw_rating = match.groups()
        key = key.strip()
        if not _allocation_key(key):
            raise ValueError(f"invalid allocation key: {key!r}")
        if key in out:
            raise ValueError(f"duplicate allocation key: {key}")
        rating = next((item for item in RATINGS_5_TIER if item.lower() == raw_rating.lower()), None)
        if rating is None:
            raise ValueError(f"invalid allocation Rating: {raw_rating}")
        out[key] = rating
    if expected_keys is not None and set(out) != set(expected_keys):
        raise ValueError(f"allocation keys differ: missing={sorted(set(expected_keys) - set(out))}, "
                         f"unexpected={sorted(set(out) - set(expected_keys))}")
    return out


def _slug(title: str) -> str:
    s = re.sub(r"[^\w\s-]", "", title.strip().lower())
    return re.sub(r"\s+", "-", s)


def _anchored(tag: str, title: str, body: str = "") -> str:
    block = f'\n<a id="{_slug(title)}"></a>\n\n{tag} {title}\n'
    return f"{block}\n{body}\n" if body else block


def _present(root: Path, items):
    return [(name, rel) for name, rel, _ in items if (root / rel).exists()]


def _read(root: Path, rel: str) -> str:
    return (root / rel).read_text(encoding="utf-8").strip()


def _main_unlocked(
    argv: list[str] | None = None,
    *,
    clock: datetime | None = None,
    scan_root: Path | str | None = None,
    expected_keys: Mapping[str, Collection[str]] | None = None,
) -> int:
    parser = argparse.ArgumentParser(description="组装宏观分节报告")
    parser.add_argument("root", help="宏观分节草稿目录")
    parser.add_argument("--output-dir", default=None)
    parser.add_argument("--state-out-dir", default=None)
    args = parser.parse_args(argv)
    root = Path(args.root)

    required = [DECISION_REL] + [
        rel for _, items in (SPINE + MESO + APPENDIX) for _, rel, opt in items if not opt
    ]
    missing = [rel for rel in required if not (root / rel).exists()]
    if missing:
        print("[MISSING] 必需分段文件不存在,请先写齐核心 agent 文件再组装:")
        for rel in missing:
            print(f"  - {root / rel}")
        return 1

    try:
        expected_keys = resolve_allocation_scope(root, expected_keys)
        allocations = {}
        for relative in (DECISION_REL, SECTOR_MAP_REL):
            table = parse_allocation(
                _read(root, relative),
                expected_keys=expected_keys.get(relative) if expected_keys is not None else None,
            )
            if not table:
                raise ValueError(f"empty allocation table: {relative}")
            allocations[relative] = table
    except ValueError as exc:
        print(f"[CONTRACT] {exc}")
        return 1

    skipped = [rel for _, items in (SPINE + MESO + APPENDIX)
               for _, rel, opt in items if opt and not (root / rel).exists()]

    now = clock or datetime.now().astimezone()
    out = [f"# Macro Research Report: {root.name}\n",
           f"Generated: {now.strftime('%Y-%m-%d %H:%M:%S')}  ",
           f"_Engine: {ws.ENGINE.title()} subscription session, zero paid LLM API. "
           "Data: FRED + akshare + yfinance._\n"]

    out.append("\n---\n\n" + SPINE_BANNER + "\n")
    out.append(_anchored("##", "S1 · 执行摘要 · 配置决策", _read(root, DECISION_REL)))
    for title, present in [(t, p) for t, items in SPINE if (p := _present(root, items))]:
        if len(present) == 1:
            out.append(_anchored("##", title, _read(root, present[0][1])))
        else:
            out.append(_anchored("##", title))
            for name, rel in present:
                out.append(_anchored("###", name, _read(root, rel)))

    out.append("\n---\n\n" + MESO_BANNER + "\n")
    for title, present in [(t, p) for t, items in MESO if (p := _present(root, items))]:
        out.append(_anchored("##", title, _read(root, present[0][1])))

    out.append("\n---\n\n" + APPENDIX_BANNER + "\n")
    for title, present in [(t, p) for t, items in APPENDIX if (p := _present(root, items))]:
        out.append(_anchored("##", title))
        for name, rel in present:
            out.append(_anchored("###", name, _read(root, rel)))

    hhmm = now.strftime("%H%M")
    out_dir = (
        Path(args.output_dir)
        if args.output_dir is not None
        else ws.reports_root() / "macro" / root.name.replace("-", "")
    )
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"{hhmm}_summary.md"
    out_path.write_text("\n".join(out), encoding="utf-8")
    print(f"[assembled] {out_path}")

    try:   # Phase 2:full 档机读摘要 macro_state.json(宏观 lite / scan Stage 0 消费;失败不阻报告)
        from autoresearch.macro.state import write_macro_state
        state_out = Path(args.state_out_dir) if args.state_out_dir else root.parent
        kwargs = {"scan_root": scan_root} if scan_root is not None else {}
        st = write_macro_state(
            root, report_path=out_path, out_dir=state_out, expected_keys=expected_keys, **kwargs,
        )
        print(f"[macro_state] {state_out / 'macro_state.json'}(as_of {st['as_of']} · "
              f"跨资产 {len(st['cross_asset'])} 行 · A股行业 {len(st['ashare_sectors'])} 行 · "
              f"regime_at_run {st['regime_at_run'] or '未记'})")
    except Exception as e:  # noqa: BLE001
        print(f"[warn] macro_state 落盘失败(不阻报告): {e}")

    alloc = allocations[DECISION_REL]
    print(f"[parse_rating → cross-asset ({len(alloc)})] {alloc}")
    if (root / SECTOR_MAP_REL).exists():
        sectors = allocations[SECTOR_MAP_REL]
        print(f"[parse_rating → A股 sectors ({len(sectors)})] {sectors}")
    if skipped:
        print("[note] 跳过未提供的可选分段: " + ", ".join(skipped))
    return 0


def main(
    argv: list[str] | None = None,
    *,
    clock: datetime | None = None,
    scan_root: Path | str | None = None,
    expected_keys: Mapping[str, Collection[str]] | None = None,
) -> int:
    from autoresearch.trace.write_guard import guarded_ambient_write

    with guarded_ambient_write("macro.assemble"):
        return _main_unlocked(argv, clock=clock, scan_root=scan_root, expected_keys=expected_keys)


if __name__ == "__main__":
    raise SystemExit(main())
