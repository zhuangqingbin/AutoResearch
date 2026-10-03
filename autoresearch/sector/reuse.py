#!/usr/bin/env python3
"""sector-research · brief TTL 复用(确定性判定,零 LLM)。

⚠️ 与 **L4 卡片** 的 TTL 复用不是一回事:后者已于 2026-07-29 按用户裁定「不要任何复用」
退役,模块在 Wave10 B1 删除。本模块复用的是**行业 brief**,由 scan-market workflow 真调用。

design: docs/specs/2026-07-03-research-skills-altitude-refactor-design.md §5.3(Phase 3)。

判据(全部成立才复用,保守):① 近 ttl 天内某 scan 日已有该行业 brief;② 两日 `meta.regime`
同(任一缺 → 不复用);③ 行业中位 60 日动量位移 |Δ| ≤ mom_shift_pp(两日 L1_scored_full 对比;
spec 原定行业指数 |Δ| 待 sw_daily 权限核实,先以中位动量代理)。`--apply` → 拷贝 + ♻️banner。

用法:uv run --no-sync python -m autoresearch.sector.reuse <date> [--industries a,b] [--apply]
"""
from __future__ import annotations

import argparse
import json
from datetime import date as _date
from pathlib import Path

from autoresearch.common import workspace as ws
from autoresearch.sector.brief import brief_path
from autoresearch.sector.pack import _num, _read_csv

_WS_SCAN_ROOT = ws.scan_root()  # B008 修法:默认值须为模块级单例(def 时求值,与旧字面量常量同语义)


def _regime(scan_dir: Path) -> str | None:
    p = Path(scan_dir) / "meta.json"
    if not p.exists():
        return None
    try:
        reg = json.loads(p.read_text(encoding="utf-8")).get("regime")
    except Exception:  # noqa: BLE001
        return None
    if isinstance(reg, dict):
        reg = reg.get("label")
    return reg if isinstance(reg, str) else None


def _ind_mom(scan_dir: Path, industry: str) -> float | None:
    l1 = _read_csv(Path(scan_dir) / "L1_scored_full.csv")
    if l1 is None or "industry" not in l1.columns:
        return None
    g = l1[l1["industry"].astype(str) == str(industry)]
    m = _num(g, "pct_60d").dropna()
    return float(m.median()) if len(m) else None


def configured_mom_shift_pp() -> float:
    """`scan_config.sector.reuse_mom_shift_pp`:行业动量位移超此值不复用 brief(缺键 3.0)。"""
    from autoresearch.scan.user_config import knob
    return float(knob("sector", "reuse_mom_shift_pp", None, 3.0))


def find_reusable(date: str, industries, root: Path | str | None = None,
                  ttl_days: int = 5, mom_shift_pp: float | None = None) -> dict[str, dict]:
    """逐行业找最近可复用 brief → {行业: {src, prev, shift_pp}};判不中 → 不入结果。

    `root=None`(生产)→ 「昨天在哪」交给 `scan.published_days`(修 K4:run 分区下遍历
    `scan_root()` 兄弟目录只看得见本 run 自己的日期,TTL 复用永远落空 = 每天白付 6 个
    opus brief)。显式传 `root` → 仍是该目录下的兄弟枚举(测试注入面,行为逐字不变)。
    **判据一个都没动**:TTL 天数 / regime 同 / 中位动量位移容差全在下面,与从前逐字相同。
    """
    mom_shift_pp = configured_mom_shift_pp() if mom_shift_pp is None else mom_shift_pp
    if root is None:
        from autoresearch.scan.published_days import previous_staging_dirs
        today_dir = ws.scan_dir(date)
        prev_dirs = previous_staging_dirs(date, limit=max(30, int(ttl_days) * 2))
    else:
        root = Path(root)
        today_dir = root / date
        prev_dirs = (sorted((p for p in root.iterdir() if p.is_dir() and p.name < date),
                            key=lambda p: p.name, reverse=True) if root.exists() else [])
    reg_today = _regime(today_dir)
    out: dict[str, dict] = {}
    for ind in industries:
        for pdir in prev_dirs:
            try:
                age = (_date.fromisoformat(date) - _date.fromisoformat(pdir.name)).days
            except ValueError:  # 非日期目录名,跳过
                continue
            if age > ttl_days:
                break                                   # 目录按日期降序 → 后面只会更老
            src = brief_path(pdir, ind)
            if not src.exists():
                continue
            reg_prev = _regime(pdir)
            if not reg_today or not reg_prev or reg_today != reg_prev:
                continue                                # regime 判据缺任一侧 → 保守不复用
            m0, m1 = _ind_mom(pdir, ind), _ind_mom(today_dir, ind)
            if m0 is None or m1 is None or abs(m1 - m0) > mom_shift_pp:
                continue
            out[ind] = {"src": str(src), "prev": pdir.name, "shift_pp": round(abs(m1 - m0), 2)}
            break
    return out


def render_reused_brief(previous_date: str, shift_pp: float, body: str) -> str:
    """Render the immutable reuse banner around an already captured prior brief."""
    banner = (
        f"> ♻️ 复用自 {previous_date} 的行业 brief(regime 同 · 中位60日动量位移 "
        f"{shift_pp}pp，已通过本次配置阈值)。失效条件已检查:TTL / regime / 动量。"
        f"重大行业级公告未由该复用判定器核验，新闻时效仍需另核。\n\n"
    )
    return banner + body


def apply_reuse(date: str, found: dict[str, dict], root: Path | str = _WS_SCAN_ROOT) -> int:
    """把可复用 brief 拷到今日 sector_briefs/,顶部 ♻️banner(带失效条件)。返回份数。"""
    n = 0
    for ind, info in found.items():
        dst = brief_path(Path(root) / date, ind)
        dst.parent.mkdir(parents=True, exist_ok=True)
        body = Path(info["src"]).read_text(encoding="utf-8")
        dst.write_text(
            render_reused_brief(info["prev"], info["shift_pp"], body),
            encoding="utf-8",
        )
        n += 1
    return n


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="行业 brief TTL 复用(确定性,零 LLM)")
    ap.add_argument("date", help="scan 日 YYYY-MM-DD")
    ap.add_argument("--industries", default=None, help="逗号分隔;缺省 = 自动选(同 sector.pack)")
    ap.add_argument("--apply", action="store_true", help="真拷贝(缺省只打印判定)")
    ap.add_argument("--ttl", type=int, default=None,
                    help="回看天数;缺省=scan_config sector.reuse_ttl_days→5")
    args = ap.parse_args(argv)
    # 2026-08-11 配置单一事实源波:CLI 显式 --ttl > scan_config sector.reuse_ttl_days > 内建 5。
    from autoresearch.scan.user_config import knob
    ttl = int(knob("sector", "reuse_ttl_days", args.ttl, 5))
    root = ws.scan_root()
    if args.industries:
        inds = [s.strip() for s in args.industries.split(",") if s.strip()]
    else:
        from autoresearch.sector.pack import select_briefing_sectors
        inds, _ = select_briefing_sectors(root / args.date)
    # root 只喂「今天写哪」(apply_reuse);「昨天在哪」交给 published_days —— CLI 在 run 分区下
    # 显式传 root 就等于把 K4 回归又装回来一次。
    found = find_reusable(args.date, inds, ttl_days=ttl)
    for ind in inds:
        if ind in found:
            print(f"[sector.reuse] ♻️ {ind} ← {found[ind]['prev']}(动量位移 {found[ind]['shift_pp']}pp)")
        else:
            print(f"[sector.reuse] ✗ {ind} 需新 brief")
    if args.apply and found:
        print(f"[sector.reuse] 拷贝 {apply_reuse(args.date, found, root=root)} 份 → sector_briefs/")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


def stable_fact_snapshot(pack: dict, facts: list[dict]) -> dict:
    """Candidate cache contains stable source facts only, never prior Markdown/prices."""
    from autoresearch.sector.terrain import PROFILE, digest
    terrain = pack.get('terrain') or {}
    if terrain.get('profile') != PROFILE:
        raise ValueError('stable fact reuse requires candidate pack')
    return {'schema_version': 1, 'profile': PROFILE, 'industry': pack['industry'],
            'as_of': terrain['target_date'], 'pack_sha256': digest(pack),
            'fingerprints': dict(terrain['fingerprints']), 'stable_facts': list(facts)}


def reuse_stable_facts(pack: dict, previous: dict | None, *, ttl_days: int,
                      knowledge_cutoff: str, bind_claim=None) -> dict:
    """Revalidate cached stable facts against current inputs/source lifetimes.

    A trading-day change always rebuilds numeric terrain. Market, financial,
    event, correction and mapping changes invalidate the stable cache as well.
    Source checking belongs to the existing evidence owner, not model self-report.
    """
    import re
    from datetime import datetime

    from autoresearch.sector.terrain import DIRECTION, PROFILE, digest

    if type(ttl_days) is not int or ttl_days < 0:
        raise ValueError('stable fact TTL must be a nonnegative integer')
    current = pack.get('terrain') or {}
    if current.get('profile') != PROFILE:
        raise ValueError('stable fact reuse requires candidate pack')
    result = {'schema_version': 1, 'profile': PROFILE, 'reused': False,
              'numeric_rebuilt': True, 'stable_facts': [], 'invalidations': [],
              'previous_date': None, 'previous_pack_sha256': None}
    if previous is None:
        result['invalidations'].append('NO_STABLE_FACT_SNAPSHOT')
        return result
    required = {'schema_version', 'profile', 'industry', 'as_of', 'pack_sha256', 'fingerprints', 'stable_facts'}
    if set(previous) != required or previous['schema_version'] != 1 or previous['profile'] != PROFILE:
        raise ValueError('invalid stable fact snapshot')
    if not re.fullmatch('[0-9a-f]{64}', str(previous['pack_sha256'])):
        raise ValueError('invalid previous pack identity')
    result.update(previous_date=previous['as_of'], previous_pack_sha256=previous['pack_sha256'])
    today, before = _date.fromisoformat(current['target_date']), _date.fromisoformat(previous['as_of'])
    age = (today - before).days
    if age < 0 or age > ttl_days:
        result['invalidations'].append('TTL_OR_FUTURE_DATE')
    if previous['industry'] != pack['industry']:
        result['invalidations'].append('INDUSTRY_CHANGED')
    if set(previous['fingerprints']) != set(current['fingerprints']):
        result['invalidations'].append('FINGERPRINT_CONTRACT_CHANGED')
    else:
        for key, value in current['fingerprints'].items():
            if previous['fingerprints'][key] != value:
                result['invalidations'].append(key.upper() + '_CHANGED')
    if result['invalidations']:
        return result
    cutoff = datetime.fromisoformat(knowledge_cutoff.replace('Z', '+00:00'))
    if cutoff.tzinfo is None:
        raise ValueError('stable fact cutoff must have timezone')
    fields = {'kind', 'claim', 'as_of', 'valid_until', 'available_at', 'source_url', 'source_text_sha256', 'source_observation_id'}
    for fact in previous['stable_facts']:
        if not isinstance(fact, dict) or set(fact) != fields:
            raise ValueError('invalid stable fact contract')
        if not all(isinstance(item, str) and item for item in fact.values()):
            raise ValueError('stable fact fields required')
        if fact['kind'] not in {'industry_structure', 'policy_rule', 'supply_chain_relation'}:
            raise ValueError('only declared stable fact kinds may be reused')
        available = datetime.fromisoformat(fact['available_at'].replace('Z', '+00:00'))
        valid_until = _date.fromisoformat(fact['valid_until'])
        original_day = _date.fromisoformat(fact['as_of'])
        valid = (available.tzinfo is not None and available <= cutoff and original_day <= before
                 and valid_until >= today and fact['source_url'].startswith(('https://', 'http://'))
                 and re.fullmatch('[0-9a-f]{64}', fact['source_text_sha256'])
                 and not DIRECTION.search(fact['claim']))
        if not valid or bind_claim is None or bind_claim(fact, {'knowledge_cutoff': knowledge_cutoff,
                'industry': pack['industry'], 'pack_sha256': digest(pack)}).get('verdict') != 'PASS':
            result['invalidations'].append('STABLE_SOURCE_UNAVAILABLE:' + fact['source_observation_id'])
            continue
        result['stable_facts'].append(dict(fact))
    result['reused'] = bool(result['stable_facts'])
    return result


def find_stable_snapshot(date: str, industry: str, *, root: Path | str | None = None,
                         ttl_days: int) -> dict | None:
    """Locate source-dated candidate facts; legacy Markdown is never admitted."""
    from autoresearch.common.atomic import canonical_json, sha256_bytes
    from autoresearch.sector.pack import _safe
    if root is None:
        from autoresearch.scan.published_days import previous_staging_dirs
        days = previous_staging_dirs(date, limit=max(30, ttl_days * 2))
    else:
        base = Path(root)
        days = sorted(base.iterdir(), reverse=True) if base.is_dir() else []
    candidates = []
    for day in days:
        source = day / 'sector_briefs' / f'{_safe(industry)}.facts.json'
        if not source.is_file():
            continue
        raw = source.read_bytes()
        value = json.loads(raw)
        actual_date = value.get('as_of')
        age = (_date.fromisoformat(date) - _date.fromisoformat(actual_date)).days
        if value.get('industry') == industry and 0 < age <= ttl_days:
            candidates.append({'source': str(source), 'source_sha256': sha256_bytes(raw),
                'snapshot_sha256': sha256_bytes(canonical_json(value).encode()), 'snapshot': value})
    return max(candidates, key=lambda item: item['snapshot']['as_of']) if candidates else None
