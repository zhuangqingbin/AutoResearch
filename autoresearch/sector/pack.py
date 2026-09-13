#!/usr/bin/env python3
"""sector-research · 确定性行业层(零 LLM):数据包 + 行业选择器 + L3 全行业地形。

design: docs/specs/2026-07-03-research-skills-altitude-refactor-design.md §5.3/§5.5(Phase 3)。

三件事全部只读 scan staging 既有产物(L1_scored_full / L2_gbdt_top200 / sectors / calendar +
全局 watchlist)——**零新增取数端点**(行业指数 sw_daily 待权限核实〔spec 开放问题 1〕,缺不影响
本包);缺文件/缺列逐字段降级(None/[]/''),presence-gated 不抛。

2026-08-28 外源扩面 D-6:pack 多一个 **presence-gated** 的 `readthrough` 键(海外读透映射;
design `docs/specs/2026-08-28-external-evidence-expansion-design.md` §8 + §4「中观 full」行)。
它是本模块唯一一个不出自 scan staging 的块 —— 名单来自人工维护的 `readthrough_map.yaml`
(`autoresearch.data.readthrough.load_map`),隔夜涨跌来自 `data.sources.yf_tape.fetch_global_tape`
(湖派生,B 级)。**两条腿缺失都只降级不阻断**:无有效映射 → 整键省略(不是空 list);tape 拿不到
→ `pct_*` 置 None + 整块标 `stale_reason` + `record_degradation` 记账(绝不抛)。

用法:
  uv run --no-sync python -m autoresearch.sector.pack <date>                     # 自动选行业
  uv run --no-sync python -m autoresearch.sector.pack <date> --industries 电子,煤炭
→ `context/sector/<date>/<行业>.json`(喂 sector-research lite brief);stdout 打印选择理由。
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

import pandas as pd

from autoresearch.common import workspace as ws

PACK_ROOT = ws.context_root() / "sector"
_WS_WATCHLIST = ws.context_root() / "watchlist.csv"  # B008:默认值须为模块级单例


def _read_csv(p: Path) -> pd.DataFrame | None:
    if not p.exists():
        return None
    try:
        df = pd.read_csv(p, dtype={"code": str})
    except Exception:  # noqa: BLE001 — 坏文件当缺(降级不抛)
        return None
    if not len(df):
        return None
    if "code" in df.columns:
        df["code"] = df["code"].astype(str).str.zfill(6)
    return df


def _num(df: pd.DataFrame, col: str) -> pd.Series:
    if col not in df.columns:
        return pd.Series([], dtype=float)
    return pd.to_numeric(df[col], errors="coerce")


def _med(s: pd.Series, nd: int = 2) -> float | None:
    s = s.dropna()
    return round(float(s.median()), nd) if len(s) else None


def _safe(industry) -> str:
    """行业名 → 文件名安全(同 assemble._safe_name 口径)。"""
    return re.sub(r'[/\\:*?"<>|\s]', "", str(industry)) or "未分类"


# ─────────────── 海外读透映射块(D-6 · presence-gated · **只进 full 档**) ───────────────
#
# 边界(2026-08-28 设计稿 §4 核心表 + §10 排期,**写死在这里防漂移**):
#   · 中观 **full**(sector-research standalone 深研)= 本块的**唯一**消费者 —— I 类,现在可做。
#   · 中观 **lite**(scan-market Stage 1 的行业 brief)= **一个字不加**。lite 地形段多一行
#     「海外映射(事实)」是设计稿的 **B-4**,受 08-26 A0 判断层冻结,要等 09-中攒够 20 结果日、
#     带 `external.sector_readthrough` 开关才做。brief 会喂 L3/L4 —— 往它里塞新事实 = 改判断层
#     输入,不是展示层改动。所以:pack JSON 里有这个键,`sector-brief.md` / playbook lite 模板
#     里**不许出现**它(`tests/sector/test_readthrough_pack.py` 有越界探针逐字对账)。
#   · 映射只表示「值得观察的关系」,**不表示因果方向 / 涨跌传导方向 / 评级方向**(§8 规则一)。
#     渲染禁忌写在 `sector-playbook.md` full 节;本模块只搬事实字段,不合成任何方向措辞。
#
# 两条腿都是 B 级:名单腿(`readthrough.load_map`)缺 → 整键省略;行情腿(`yf_tape`)缺 → pct 置
# None + 标 `stale_reason` + 记账。**任何一条都不许抛**(sector pack 是 scan Stage 1 的前置件,
# 炸在这里等于用一个展示增强把整条漏斗打死)。

_RT_MAX = 4                     # §8:单层 ≤4 项(load_map 已截,这里再截一次 = 纵深防御)
_RT_KINDS = ("company", "etf", "index")
_RT_RELATIONS = ("customer", "supplier", "peer", "theme")
_RT_DIRECTIONS = ("downstream", "upstream", "peer")
_RT_UNTRADABLE = ("delisted", "suspended", "halted", "unlisted", "expired")
_RT_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def _rt_degrade(endpoint: str, reason: str, key: str = "") -> None:
    """B 级降级记账(懒导入:避免 sector 包在 import 期就拖上 data 契约层)。"""
    try:
        from autoresearch.data.contracts import record_degradation
        record_degradation(endpoint, reason, key=key)
    except Exception:  # noqa: BLE001 — 记账本身失败也不许影响 pack
        print(f"[sector.pack] {endpoint}:{reason}(记账失败)", file=sys.stderr)


def _rt_date(v) -> str | None:
    s = str(v or "").strip()[:10]
    return s if _RT_DATE_RE.match(s) else None


def _rt_num(row: dict | None, col: str, nd: int = 2) -> float | None:
    if not row or col not in row:
        return None
    try:
        f = float(row[col])
    except (TypeError, ValueError):
        return None
    return None if pd.isna(f) else round(f, nd)


def _rt_bool(row: dict | None, col: str) -> bool | None:
    """三态布尔(True/False/未知)。**不能用 `is False` 直接判** —— pandas 取出来的是
    `numpy.bool_`,`np.False_ is False` 为假,那样写 session_complete 探针永远不亮。"""
    if not row or col not in row:
        return None
    v = row[col]
    try:
        if v is None or pd.isna(v):
            return None
    except (TypeError, ValueError):
        return None
    if isinstance(v, str):
        s = v.strip().lower()
        return {"true": True, "1": True, "yes": True,
                "false": False, "0": False, "no": False}.get(s)
    return bool(v)


def _rt_str(row: dict | None, col: str) -> str | None:
    if not row:
        return None
    v = row.get(col)
    try:
        if v is None or pd.isna(v):
            return None
    except (TypeError, ValueError):      # 非标量(list/dict)→ 按缺处理
        return None
    return str(v).strip() or None


def _rt_valid(item, as_of: str) -> bool:
    """pack 侧的独立准入(**不信任 load_map 已经筛过** —— 消费者自己也要能拒)。

    拒的四类:枚举不合法 / 证据 URL 缺或不是 http(s) / 不在有效期内 / 标了不可交易。
    2026-07-28 教训「立案时写的诊断,动工一查 4/4 全错」的同族推论:上游声称筛过 ≠ 真筛过,
    而这块是要印进研究报告的**外部事实**,错一条就是一条查无实据的产业证据。
    """
    if not isinstance(item, dict):
        return False
    if not str(item.get("symbol") or "").strip():
        return False
    if str(item.get("kind") or "") not in _RT_KINDS:
        return False
    if str(item.get("relation") or "") not in _RT_RELATIONS:
        return False
    if str(item.get("direction") or "") not in _RT_DIRECTIONS:
        return False
    url = str(item.get("evidence_url") or "").strip()
    if not (url.startswith("http://") or url.startswith("https://")):
        return False
    if item.get("tradable") is False:
        return False
    if str(item.get("status") or "").strip().lower() in _RT_UNTRADABLE:
        return False
    eff_from, eff_to, today = _rt_date(item.get("effective_from")), _rt_date(item.get("effective_to")), _rt_date(as_of)
    if eff_from is None:                      # 有效期起点必填(§8:入库 lint 验有效期)
        return False
    if today is None:                         # as_of 不是日期 → 无法判有效期,只保枚举/证据门
        return True
    if eff_from > today:
        return False
    return not (eff_to is not None and eff_to < today)


def _rt_map_items(industry, as_of: str) -> list[dict]:
    """人工映射表 → 该行业的原始名单;模块未接线 / 无该行业 → [](合法空,不记账)。"""
    try:
        from autoresearch.data.readthrough import load_map
    except Exception:  # noqa: BLE001 — D-1 source soak 前该模块可以整个不存在
        return []
    try:
        m = load_map(as_of) or {}
    except Exception as e:  # noqa: BLE001
        _rt_degrade("readthrough_map", f"load_map 失败({type(e).__name__}: {e})", key=str(as_of))
        return []
    if not isinstance(m, dict):
        return []
    by_ind = m.get("industries") if isinstance(m.get("industries"), dict) else m
    items = by_ind.get(str(industry)) if isinstance(by_ind, dict) else None
    return list(items) if isinstance(items, (list, tuple)) else []


def _rt_tape(as_of: str) -> tuple[dict[str, dict], str | None]:
    """隔夜 tape → {symbol: 行};取数失败/空 → ({}, stale_reason) 且**记账**,不抛。"""
    try:
        from autoresearch.data.sources.yf_tape import fetch_global_tape
    except Exception as e:  # noqa: BLE001
        reason = f"tape 源未接线({type(e).__name__})"
        _rt_degrade("global_tape", reason, key=str(as_of))
        return {}, reason
    try:
        df = fetch_global_tape(as_of=as_of)
    except Exception as e:  # noqa: BLE001
        reason = f"tape 取数失败({type(e).__name__}: {e})"
        _rt_degrade("global_tape", reason, key=str(as_of))
        return {}, reason
    if df is None or not len(df) or "symbol" not in getattr(df, "columns", []):
        reason = "tape 空返回(无 symbol 行)"
        _rt_degrade("global_tape", reason, key=str(as_of))
        return {}, reason
    rows: dict[str, dict] = {}
    for _, r in df.iterrows():
        sym = str(r.get("symbol") or "").strip().upper()
        if sym:
            rows.setdefault(sym, dict(r))
    return rows, None


def _rt_render(item: dict, tape: dict[str, dict], stale: str | None) -> dict:
    """单条映射 → pack 行(纯搬运:字段照抄 + tape 数字,**零方向措辞**)。"""
    sym = str(item["symbol"]).strip()
    kind = str(item["kind"])
    row = tape.get(sym.upper())
    reason = stale
    if reason is None and row is None:
        reason = f"tape 无 {sym} 行"
    # 必须走 `_rt_bool`:`is False` 只对 Python bool 成立,而帧里取出来的可能是
    # `numpy.bool_`(`np.False_ is False` 为假)——那样探针永远不亮,一个还没收盘、
    # 随时会变的数字就会被当成已定读数印进报告。
    elif reason is None and _rt_bool(row, "session_complete") is False:
        reason = "美股时段未收(session_complete=False)"
    # 财报主体键只对 kind == "company" 开放:§8「ETF / 指数不得伪装成公司或财报主体」——
    # 上游即使误塞 next_earnings_date 给一只 ETF,这里也一律抹成 None(硬门,不是提示)。
    is_company = kind == "company"
    return {
        "symbol": sym,
        "kind": kind,                         # 原样带出:下游据此决定能不能当财报主体讲
        "relation": str(item["relation"]),
        "direction": str(item["direction"]),
        "rationale": str(item.get("rationale") or "").strip(),
        "evidence_url": str(item["evidence_url"]).strip(),
        "pct_1d": _rt_num(row, "pct_1d"),
        "pct_5d": _rt_num(row, "pct_5d"),
        "next_earnings_date": (_rt_str(row, "next_earnings_date")
                               or _rt_str(item, "next_earnings_date")) if is_company else None,
        "implied_move_note": (_rt_str(row, "implied_move_note")
                              or _rt_str(item, "implied_move_note")) if is_company else None,
        "stale_reason": reason,
    }


def readthrough_block(industry, as_of: str) -> list[dict] | None:
    """行业 → 海外读透映射块;**无有效映射返回 None**(调用方据此整键省略,而不是写空 list)。"""
    raw = _rt_map_items(industry, as_of)
    if not raw:
        return None
    valid = [it for it in raw if _rt_valid(it, as_of)][:_RT_MAX]
    if not valid:
        return None
    tape, stale = _rt_tape(as_of)
    return [_rt_render(it, tape, stale) for it in valid]


# ───────────────────────── 单行业数据包 ─────────────────────────


def sector_pack(industry: str, scan_dir: Path | str) -> dict:
    """单行业确定性数据包:成分截面聚合 + L2 入选数 + 日历事件计数 + (有则)海外读透映射。

    字段可缺(None/[]);`readthrough` 是 **presence-gated** 的 —— 无有效映射时整键不存在
    (不是空 list),下游据「键在不在」决定渲不渲染那一节。
    """
    pack = _sector_pack_staging(industry, scan_dir)
    rt = readthrough_block(industry, pack.get("as_of") or "")
    if rt:
        pack["readthrough"] = rt
    return pack


def _sector_pack_staging(industry: str, scan_dir: Path | str) -> dict:
    """pack 的 staging 腿(全部只读 scan 既有产物,零外源;缺文件/缺列逐字段降级)。"""
    scan_dir = Path(scan_dir)
    pack: dict = {"industry": str(industry), "as_of": scan_dir.name,
                  "n_market": 0, "n_l2": 0,
                  "median_pct_60d": None, "median_pe": None, "pe_p25": None, "pe_p75": None,
                  "median_pb": None, "median_np_yoy": None, "median_roe": None,
                  "main_pos_frac": None, "main_net_sum_yi": None,
                  "healthy_n": None, "median_winner": None,
                  "leaders": [], "calendar": None}
    l1 = _read_csv(scan_dir / "L1_scored_full.csv")
    if l1 is None or "industry" not in l1.columns:
        return pack
    g = l1[l1["industry"].astype(str) == str(industry)]
    if not len(g):
        return pack
    pack["n_market"] = int(len(g))
    pack["median_pct_60d"] = _med(_num(g, "pct_60d"))
    pe = _num(g, "pe")
    pe = pe[pe > 0]
    if len(pe):
        pack["median_pe"] = round(float(pe.median()), 2)
        pack["pe_p25"] = round(float(pe.quantile(0.25)), 2)
        pack["pe_p75"] = round(float(pe.quantile(0.75)), 2)
    pack["median_pb"] = _med(_num(g, "pb"))
    pack["median_np_yoy"] = _med(_num(g, "np_yoy"))
    pack["median_roe"] = _med(_num(g, "roe"))
    mnr = _num(g, "main_net_ratio").dropna()
    if len(mnr):
        pack["main_pos_frac"] = round(float((mnr > 0).mean()), 4)
    inflow = _num(g, "main_inflow_yi").dropna()
    if len(inflow):
        pack["main_net_sum_yi"] = round(float(inflow.sum()), 2)
    pack["median_winner"] = _med(_num(g, "winner_rate"))
    try:  # 健康上涨(与菜单体检/healthy 通道同一谓词——单一事实源)
        from autoresearch.common.scoring import healthy_riser_mask
        m = healthy_riser_mask(g)
        pack["healthy_n"] = int(m.sum()) if m is not None else None
    except Exception:  # noqa: BLE001
        pack["healthy_n"] = None
    if "mktcap_yi" in g.columns:  # 龙头映射素材(市值 top5,事实非方向)
        top = g.assign(_cap=_num(g, "mktcap_yi")).nlargest(5, "_cap")
        pack["leaders"] = [
            {"code": r["code"], "name": r.get("name"),
             "mktcap_yi": (round(float(r["_cap"]), 1) if pd.notna(r["_cap"]) else None),
             "pe": (round(float(r["pe"]), 1) if "pe" in top.columns and pd.notna(r.get("pe")) else None),
             "pct_60d": (round(float(r["pct_60d"]), 1)
                         if "pct_60d" in top.columns and pd.notna(r.get("pct_60d")) else None)}
            for _, r in top.iterrows()]
    l2 = _read_csv(scan_dir / "L2_gbdt_top200.csv")
    if l2 is not None and "industry" in l2.columns:
        pack["n_l2"] = int((l2["industry"].astype(str) == str(industry)).sum())
    cal = _read_csv(scan_dir / "calendar.csv")
    if cal is not None and {"code", "event_date"} <= set(cal.columns):
        c = cal[cal["code"].isin(set(g["code"]))]
        if len(c):
            pack["calendar"] = {
                "n_events": int(len(c)),
                "next_date": str(c["event_date"].astype(str).min()),
                "by_kind": (c["kind"].astype(str).value_counts().to_dict()
                            if "kind" in c.columns else {})}
    return pack


# ───────────────────────── 行业选择器(红榜∪集中度∪观察单) ─────────────────────────


def select_briefing_sectors(scan_dir: Path | str, k: int = 6,
                            wl_path: Path | str = _WS_WATCHLIST,
                            ) -> tuple[list[str], dict[str, str]]:
    """brief 该给哪几个行业:红榜 top3 ∪ L2 集中度 top3 ∪ 观察单行业,保序去重 cap=k。

    返回 (industries, provenance{行业: 来源});任一来源缺文件 → 该来源为空(降级不抛)。
    行业选择需要"当日菜单"做锚 —— 这是中观 lite 挂 L2 后而非更早的原因(design §4 D6)。
    P7:第四来源 = market_pack.json 的 sector_healthy_top3(确定性看多榜);**top3看多 追加不占 k**
    ——基础三来源仍 cap=k,top3 只在基础集之外补(不挤占红榜/集中度/观察单名额)。
    """
    scan_dir = Path(scan_dir)
    prov: dict[str, str] = {}

    def _add(ind, tag: str) -> None:
        ind = str(ind).strip()
        if ind and ind not in ("未分类", "nan") and ind not in prov:
            prov[ind] = tag

    sec = _read_csv(scan_dir / "sectors.csv")
    if sec is not None and {"industry", "median_pct_60d"} <= set(sec.columns):
        s = sec.assign(_m=pd.to_numeric(sec["median_pct_60d"], errors="coerce")).dropna(subset=["_m"])
        for ind in s.sort_values("_m", ascending=False)["industry"].head(3):
            _add(ind, "红榜top3")
    l2 = _read_csv(scan_dir / "L2_gbdt_top200.csv")
    if l2 is not None and "industry" in l2.columns:
        for ind in l2["industry"].value_counts().head(3).index:
            _add(ind, "L2集中度top3")
    wl = _read_csv(Path(wl_path))
    l1 = _read_csv(scan_dir / "L1_scored_full.csv")
    if wl is not None and "code" in wl.columns and l1 is not None and "industry" in l1.columns:
        imap = dict(zip(l1["code"], l1["industry"], strict=False))
        for c in wl["code"]:
            ind = imap.get(str(c).zfill(6))
            if ind:
                _add(ind, "观察单")
    mp = scan_dir / "market_pack.json"                       # P7:确定性看多 top3(追加,不占 k)
    if mp.exists():
        try:
            for r in (json.loads(mp.read_text(encoding="utf-8")).get("sector_healthy_top3") or [])[:3]:
                _add(r.get("industry"), "top3看多")
        except Exception:  # noqa: BLE001 — 新增来源,坏 pack 不挡行业选择
            pass
    base = [i for i, tag in prov.items() if tag != "top3看多"][:k]
    extra = [i for i in prov if prov[i] == "top3看多" and i not in base]
    inds = base + extra
    return inds, {i: prov[i] for i in inds}


# ───────────────────────── L3 全行业确定性地形(对称覆盖) ─────────────────────────


def sector_terrain_md(scan_dir: Path | str, max_rows: int = 40, top200_only: bool = False) -> str:
    """L3 紧凑表前置的**全行业地形段**:每申万一级一行,全行业对称——防"有 brief 的行业被系统性
    高看"(design §5.5-4)。数字全出 staging;缺 staging → ''(l3_table_md 默认关 = parity)。
    top200_only=True:只渲染 L2 top200 出现过的申万一级行业(~110→30-50 行,L3 表最大块瘦身);
    默认 False = 逐字 parity。"""
    scan_dir = Path(scan_dir)
    l1 = _read_csv(scan_dir / "L1_scored_full.csv")
    if l1 is None or "industry" not in l1.columns:
        return ""
    l2 = _read_csv(scan_dir / "L2_gbdt_top200.csv")
    l2n = (l2["industry"].astype(str).value_counts().to_dict()
           if l2 is not None and "industry" in l2.columns else {})
    if top200_only and l2n:
        l1 = l1[l1["industry"].astype(str).isin(l2n)]   # 只渲染 top200 覆盖行业(≈110→30-50 行)
    try:
        from autoresearch.common.scoring import healthy_riser_mask
        hm = healthy_riser_mask(l1)
    except Exception:  # noqa: BLE001
        hm = None
    rows = []
    for ind, g in l1.groupby("industry"):
        ind = str(ind)
        pe = _num(g, "pe")
        pe = pe[pe > 0]
        mnr = _num(g, "main_net_ratio").dropna()
        rows.append({
            "industry": ind, "n": int(len(g)), "l2": int(l2n.get(ind, 0)),
            "mom": _med(_num(g, "pct_60d"), 1),
            "pe": round(float(pe.median()), 1) if len(pe) else None,
            "mainpos": round(float((mnr > 0).mean()), 2) if len(mnr) else None,
            "healthy": int(hm.loc[g.index].sum()) if hm is not None else None})
    rows.sort(key=lambda r: (-r["l2"], -(r["mom"] if r["mom"] is not None else float("-inf"))))

    def _f(v):
        return "—" if v is None else v

    lines = ["## 全行业地形(确定性 · 申万一级对称覆盖 · 背景校准,非选股指令)",
             "_数字出自 L1_scored_full/L2 staging;健康 = 0<pct60<40∧主力+∧cmf+(与菜单体检同谓词);"
             "无行业 brief 的行业照常按 rubric 判断,不因信息薄降权。_",
             "",
             "| 行业 | 全市场n | 入L2 | 中位60日% | 中位PE | 主力+占比 | 健康数 |",
             "|---|---|---|---|---|---|---|"]
    for r in rows[:max_rows]:
        lines.append(f"| {r['industry']} | {r['n']} | {r['l2']} | {_f(r['mom'])} | "
                     f"{_f(r['pe'])} | {_f(r['mainpos'])} | {_f(r['healthy'])} |")
    return "\n".join(lines)


# ───────────────────────── CLI ─────────────────────────


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="行业确定性数据包(零 LLM;喂 sector-research lite brief)")
    ap.add_argument("date", help="scan 日 YYYY-MM-DD(staging 需已就绪 = L2 后)")
    ap.add_argument("--industries", default=None, help="逗号分隔;缺省 = 自动选(红榜∪集中度∪观察单)")
    ap.add_argument("--scan-dir", default=None, help="缺省 context/scan/<date>")
    ap.add_argument(
        "--output-dir",
        default=None,
        help="显式工作目录(session adapter 使用);缺省保持历史 sector 目录",
    )
    ap.add_argument("--k", type=int, default=None,
                    help="自动选行业数上限;缺省=scan_config sector.max_briefs→6")
    args = ap.parse_args(argv)
    # 2026-08-11 配置单一事实源波:CLI 显式 --k > scan_config sector.max_briefs > 内建 6。
    from autoresearch.scan.user_config import knob
    k = int(knob("sector", "max_briefs", args.k, 6))
    scan_dir = Path(args.scan_dir) if args.scan_dir else ws.scan_root() / args.date
    if args.industries:
        inds = [s.strip() for s in args.industries.split(",") if s.strip()]
        prov: dict[str, str] = {}
    else:
        inds, prov = select_briefing_sectors(scan_dir, k=k)
    if not inds:
        print("[sector.pack] 无可选行业(staging 缺/空)—— presence-gated 跳过", file=sys.stderr)
        return 0
    outdir = Path(args.output_dir) if args.output_dir else PACK_ROOT / args.date
    outdir.mkdir(parents=True, exist_ok=True)
    for ind in inds:
        p = sector_pack(ind, scan_dir)
        out = outdir / f"{_safe(ind)}.json"
        out.write_text(json.dumps(p, ensure_ascii=False, indent=2), encoding="utf-8")
        tag = f"({prov[ind]})" if ind in prov else ""
        print(f"[sector.pack] {ind}{tag} → {out}(全市场 {p['n_market']} 只·入L2 {p['n_l2']})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
