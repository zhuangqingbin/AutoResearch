#!/usr/bin/env python3
"""scan-market · 运行健康 + 现场索引(确定性,零 LLM)。

design: docs/specs/2026-07-02-scan-observability-design.md §1

一次 scan 的"体检报告 + 导航页":关键产物在位表、因子 NaN 降级、finalist 逐日重叠
(churn,卡片复用的前置读数)、L4 阶段效能(早停/满卡/复用/P4 翻盘)、买单计数。
index.md 是第二天回看的入口。

2026-08-21(用户裁定「整个 learning 层退役」):`ledger_freshness`(账本新鲜度)与
`retro_health`(复盘事实/consumer 积压)两节随闭环删除,`run_health` 相应少两个键。
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import pandas as pd

# 关键产物在位表(缺 = 流程段没跑/失败;market_view 是可选段,缺了只提示不报错。
# watchlist_status.csv 随观察单日检退役摘除,fb_20260714_002)
_ARTIFACTS = ["L1_scored_full.csv", "L1_recall_top1000.csv", "L2_gbdt_top200.csv",
              "finalists.csv", "market_view.md", "verify.csv",
              "gate_fires.csv", "weights_used.json", "L3_judged_full.csv"]
_CORE = {"L1_scored_full.csv", "L1_recall_top1000.csv", "L2_gbdt_top200.csv", "finalists.csv"}

# NaN 体检的关键因子列(L1_recall 口径;降级 = 该组权限缺/端点挂,IC 读数打折扣)
_FACTOR_COLS = ["composite", "main_net_ratio", "winner_rate", "chip_concentration",
                "hk_ratio", "rsi6", "cmf_20", "pe", "np_yoy", "pct_60d",
                "vol_ratio_20"]   # 2026-08-21:低位转强面板腿的体检代表列(整组 NaN = 60 日面板没算出来)

_P4_RE = re.compile(r"进入P4倾向[:：]\s*\**(Buy|Overweight|Hold|Underweight|Sell)", re.IGNORECASE)


def _read(p: Path) -> pd.DataFrame | None:
    try:
        return pd.read_csv(p, dtype={"code": str}) if p.exists() else None
    except Exception:  # noqa: BLE001
        return None


def nan_report(scan_dir: Path, thresh: float = 0.30) -> tuple[dict, list[str]]:
    """L1_recall 关键因子 NaN 率 → ({col: rate}, 降级列表)。缺文件 → ({}, [])。"""
    df = _read(Path(scan_dir) / "L1_recall_top1000.csv")
    if df is None or not len(df):
        return {}, []
    rates, degraded = {}, []
    for c in _FACTOR_COLS:
        if c not in df.columns:
            continue
        r = float(pd.to_numeric(df[c], errors="coerce").isna().mean())
        rates[c] = round(r, 3)
        if r > thresh:
            degraded.append(c)
    return rates, degraded


def anns_empty_rate(scan_dir: Path) -> float | None:
    """L3_news 空稿率(旧口径)。无目录 → None。

    ⚠️ Wave9 A-1 复核轮1:"=1.0 为 expected 非告警"是**已被推翻的旧判据**——本函数只
    回答"空不空",答不了"为什么空"(主源无权限与当日故障在此长得一样)。**当前权威判据
    以 `anns_source_status()` 的四态(ok/fallback/blind/pending)为准**:`blind`(有稿但
    双源皆空)是 warn,不是 expected(详见该函数 docstring)。本键与并列布尔 `anns_expected`
    仍保留,只服务两处遗留消费者:(a) `product_shape_lint` 在旧 run(无
    `anns_source_status` 键)时的回落判定;(b) 下面 `index_md` 沿用的既有渲染行(未随
    本次改动迁移判据,见该处注释)。
    Wave4 Task1:`index_md` 现会据此(`anns_expected`)渲染一行「公告标题流不可用」——
    此前只在 `run_health.json` 里挂 `anns_expected=True`,报告正文完全无感,断链留痕。"""
    d = Path(scan_dir) / "L3_news"
    files = sorted(d.glob("*.json")) if d.is_dir() else []
    if not files:
        return None
    def _n(p: Path) -> int:
        try:
            v = json.loads(p.read_text(encoding="utf-8"))
            return len(v) if isinstance(v, list) else 0
        except Exception:  # noqa: BLE001 — 坏 JSON 记空
            return 0
    empty = sum(1 for p in files if _n(p) == 0)
    return round(empty / len(files), 3)


def anns_source_status(scan_dir: Path) -> dict:
    """公告流双源状态(Wave9 A-1 + 复核轮1 Critical 修复)。

    `anns_empty_rate` 只回答"空不空",回答不了"为什么空"——主源无权限与兜底也挂在产物
    上长得一样。本函数按行内 `source` 标签拆源,四态:
      ok       = 有非兜底来源的行(主源活着)
      fallback = 主源无料但兜底源(`source==SOURCE_TAG`,见 anns_fallback.SOURCE_TAG)扛住了
      blind    = **有稿件但双源皆空** → **这是 warn,不是 expected**
      pending  = `L3_news/` 目录/稿件根本不存在 —— **L3 阶段还没跑到,不是"双源皆空"的
                 证据**(复核轮1 Critical:`L3_news/` 由 L3 阶段 `harvest_l3_news()` 生成,
                 prelude 在 L0→L2 末尾就调用本函数时该目录结构性地必然还不存在;若把这种
                 "还没发生"误判成 blind,会天天无条件误报——本 wave 的主题"存在≠有效"在
                 我们自己新加的调用点上重演了一次)。
    """
    from autoresearch.data.sources.anns_fallback import SOURCE_TAG as _FALLBACK_TAG

    d = Path(scan_dir) / "L3_news"
    files = sorted(d.glob("*.json")) if d.is_dir() else []
    if not files:
        return {"primary_empty_rate": None, "fallback_rows": 0, "status": "pending"}

    primary_rows = fallback_rows = 0
    empty_files = 0
    for p in files:
        try:
            v = json.loads(p.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001 — 坏 JSON 记空
            v = []
        rows = v if isinstance(v, list) else []
        if not rows:
            empty_files += 1
        for r in rows:
            if isinstance(r, dict) and str(r.get("source", "")) == _FALLBACK_TAG:
                fallback_rows += 1
            else:
                primary_rows += 1

    if primary_rows:
        status = "ok"
    elif fallback_rows:
        status = "fallback"
    else:
        status = "blind"
    return {"primary_empty_rate": round(empty_files / len(files), 3),
            "fallback_rows": fallback_rows, "status": status}


def northbound_probe(scan_dir: Path) -> dict | None:
    """northbound 召回通道空转读数:该路召回票数 + 其 hk_ratio NaN 率(=1.0 → quota 白占)。

    只取证不动结构(spec 2026-07-05 wave §顺带修);坐实后另走 proposal 人拍板。
    """
    df = _read(Path(scan_dir) / "L1_recall_top1000.csv")
    if df is None or "recall_channels" not in df.columns:
        return None
    sub = df[df["recall_channels"].astype(str).str.contains("northbound", na=False)]
    if not len(sub):
        return {"n": 0, "hk_nan": None}
    nanr = (round(float(pd.to_numeric(sub["hk_ratio"], errors="coerce").isna().mean()), 3)
            if "hk_ratio" in sub.columns else None)
    return {"n": int(len(sub)), "hk_nan": nanr}


def finalist_churn(scan_dir: Path) -> dict | None:
    """与上一 scan 日 finalists 的重叠(卡片 TTL 复用的前置测量)。无前日 → None。"""
    scan_dir = Path(scan_dir)
    today = _read(scan_dir / "finalists.csv")
    if today is None or "code" not in today.columns:
        return None
    prevs = sorted((p for p in scan_dir.parent.iterdir()
                    if p.is_dir() and p.name[:2] == "20" and p.name < scan_dir.name
                    and (p / "finalists.csv").exists()), reverse=True)
    if not prevs:
        return None
    prev = _read(prevs[0] / "finalists.csv")
    if prev is None or "code" not in prev.columns:
        return None
    t = set(today["code"].astype(str).str.zfill(6))
    y = set(prev["code"].astype(str).str.zfill(6))
    rep = t & y
    return {"prev_date": prevs[0].name, "n_prev": len(y), "n_today": len(t),
            "n_repeat": len(rep), "repeat_rate": round(len(rep) / len(t), 3) if t else 0.0}


def l4_phase_stats(scan_dir: Path) -> dict | None:
    """L4 阶段效能:早停/满卡/复用分布 + P4 翻盘率(卡片契约 `进入P4倾向: <Rating>`)。

    早停率低 = 早停没在省;P4 翻盘率≈0 = 陷阱核可条件化(先测量后动刀)。无卡 → None。
    """
    base = Path(scan_dir) / "details"
    cards = sorted(base.glob("*.md")) if base.is_dir() else []
    if not cards:
        return None
    from autoresearch.agents.utils.rating import parse_rating  # lazy 防环
    n_stop = n_reuse = p4_seen = p4_flips = 0
    for p in cards:
        text = p.read_text(encoding="utf-8")
        if "♻️" in text and "复用" in text:
            n_reuse += 1
            continue
        if "早停因" in text:
            n_stop += 1
        m = _P4_RE.search(text)
        if m:
            p4_seen += 1
            if m.group(1).title() != parse_rating(text):
                p4_flips += 1
    return {"n_cards": len(cards), "n_earlystop": n_stop, "n_reused": n_reuse,
            "n_full": len(cards) - n_stop - n_reuse, "p4_seen": p4_seen, "p4_flips": p4_flips}


def _legacy_final_ratings(scan_dir: Path) -> dict[str, str]:
    """{code: 最终评级}(finalists → parse_rating(卡)→ verify 降级折回 → **ensemble 复核折回**)。

    与 assemble 同口径 —— assemble 里跑的是**两个** fold 循环,这里此前只跑了 verify 那个,
    ensemble(≥OW 买单复核 / pinned SELL 复核)那条腿漏了(Wave7 P1 发现)。后果:
    · sell_review 把 Sell 折回 Underweight 后,本函数仍报 Sell —— 07-24/07-27 的 300857 实锤,
      与同目录 `_final_ratings.json`(assemble 落的权威值)直接打架;
    · ow_review 只向下折,一旦发生,`count_buys` 会把一张已被折成 Hold 的卡继续算作买单
      = 幽灵买单。历史上尚未发生(全量重放 0 例),所以没被污染 —— 但这是运气,不是设计。
    权威值是同目录的 `_final_ratings.json`(assemble 落盘);本函数与它保持同源。
    """
    scan_dir = Path(scan_dir)
    fin = _read(scan_dir / "finalists.csv")
    if fin is None or "code" not in fin.columns:
        return {}
    from autoresearch.agents.utils.rating import parse_rating  # lazy 防环
    from autoresearch.scan.decision_finalize import (
        _apply_ensemble_fold,
        _apply_verify_downgrade,
        _load_ensemble,
        _load_verify,
    )
    vmap = _load_verify(scan_dir)
    emap = _load_ensemble(scan_dir)
    out: dict[str, str] = {}
    for code in fin["code"].astype(str).str.zfill(6):
        p = scan_dir / "details" / f"{code}.md"
        if not p.exists():
            continue
        rating = parse_rating(p.read_text(encoding="utf-8"))
        v = vmap.get(code)
        if v and v["verdict"] in ("降级", "否决"):
            rating = _apply_verify_downgrade(rating, v["verdict"])
        rating = _apply_ensemble_fold(rating, emap.get(code))   # 顺序与 assemble 一致:verify → ensemble
        out[code] = rating
    return out


def final_ratings(scan_dir: Path) -> dict[str, str]:
    """Read authoritative final ratings, with the historical card fold fallback."""
    from autoresearch.scan.decision_read_model import read_final_ratings

    return read_final_ratings(
        scan_dir,
        card_fallback=_legacy_final_ratings,
    )


#: `count_buys` 的口径标签(进 `run_health.counts.buys_source`,只在 active 期出现)。
BUYS_SOURCE_DECISION = "decision_file.buys"
BUYS_SOURCE_FALLBACK = "rating≥OW(回退:决策文件缺席/日期不符)"


def count_buys_with_source(scan_dir: Path) -> tuple[int, str | None]:
    """最终买单数 + **口径来源**(E3b 裁定 3,task-2.4)。

    - shadow 期 → `(≥OW 张数, None)` = 现行为逐字不变(source 为 None = 不进 run_health);
    - active 期 → 决策文件在场且**日期相符**就读它的 `buys[]`;缺席/过期则回退 ≥OW 计数
      并**显式标记回退**(不静默,否则「这个数今天到底算的什么」不可查)。

    时序诚实(E3b 原文):`run_health.json` 在一次发布里被写多次,`publisher.py:273`/`:320`
    那两次都早于/紧贴 writer-1,那时盘上要么没有当日决策文件、要么还是前一日的 → 本函数
    如实走回退分支;**最后一次 `:375` 在决策文件已在盘之后**,所以落盘的终版快照是对的,
    早期快照的该字段本就只是中间态。这是刻意接受的,不是漏网。
    """
    from autoresearch.scan.relative_buy import is_active, load_decision

    legacy = sum(1 for r in final_ratings(scan_dir).values()
                 if r in ("Buy", "Overweight"))
    if not is_active():
        return legacy, None
    doc = load_decision(scan_dir)
    if not isinstance(doc, dict):
        return legacy, BUYS_SOURCE_FALLBACK
    return len(doc.get("buys") or []), BUYS_SOURCE_DECISION


def count_buys(scan_dir: Path) -> int:
    """最终买单数。shadow 期 = ≥OW(verify 折回后);active 期见 `count_buys_with_source`。"""
    return count_buys_with_source(scan_dir)[0]


def _json_object(path: Path) -> dict | None:
    if not path.exists():
        return None
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else None
    except (OSError, json.JSONDecodeError):
        return None


def run_contract_health(scan_dir: Path) -> dict:
    """影子校验运行身份与两份配置回显；本批只记账，不在此阻断。"""
    from autoresearch.scan.run_contract import load_run_contract, sha256_json

    scan = Path(scan_dir)
    path = scan / "run_contract.json"
    empty = {
        "status": "ABSENT",
        "run_id": None,
        "contract_hash": None,
        "errors": [],
        "echo_config_match": None,
        "market_pack_config_match": None,
    }
    if not path.exists():
        return empty
    try:
        contract = load_run_contract(path)
    except (OSError, json.JSONDecodeError, TypeError, ValueError) as exc:
        return {
            **empty,
            "status": "INVALID",
            "errors": [f"run_contract unreadable: {exc}"],
        }

    errors: list[str] = []
    if contract.analysis_date != scan.name:
        errors.append(
            f"analysis_date mismatch: contract={contract.analysis_date} dir={scan.name}"
        )
    echo = _json_object(scan / "user_config_echo.json")
    pack = _json_object(scan / "market_pack.json")
    echo_match = None if echo is None else sha256_json(echo) == contract.config_hash
    pack_cfg = pack.get("user_config") if pack is not None else None
    pack_match = (
        None
        if not isinstance(pack_cfg, dict)
        else sha256_json(pack_cfg) == contract.config_hash
    )
    if echo_match is False:
        errors.append("user_config_echo config_hash mismatch")
    if pack_match is False:
        errors.append("market_pack user_config config_hash mismatch")
    return {
        "status": "INVALID" if errors else "OK",
        "run_id": contract.run_id,
        "contract_hash": contract.contract_hash,
        "errors": errors,
        "echo_config_match": echo_match,
        "market_pack_config_match": pack_match,
    }


def _gate4_has_data_fail(scan: Path) -> bool | None:
    """gate4 FAILED 时查 gate_fires.csv 是否有 data 类 fail 行;查不到 → None(按 data 处理)。"""
    import csv as _csv

    from autoresearch.common.failclass import fail_class

    path = Path(scan) / "gate_fires.csv"
    if not path.is_file():
        return None
    try:
        with path.open(encoding="utf-8", newline="") as fh:
            rows = list(_csv.DictReader(fh))
    except (OSError, _csv.Error):
        return None
    fails = [r for r in rows if str(r.get("severity") or "") == "fail"]
    if not fails:
        return None
    return any(fail_class(r.get("check")) == "data" for r in fails)


def stage_results_health(scan_dir: Path) -> dict:
    """汇总 StageResult 完整性与业务状态；FAILED 本身不是文件损坏。

    `failed_data` 是 `failed` 的子集(E1a,2026-08-18 设计稿 §3):gate4 FAILED 时再查
    `gate_fires.csv` 的 fail 行分类,hygiene/metering 类不连坐当日 `relative_buy` 的
    data_a 硬门;非 gate4 stage 的 FAILED 一律原样计入(那本账只归 gate4 一家写)。
    """
    from collections import Counter

    from autoresearch.scan.stage_result import load_stage_result

    scan = Path(scan_dir)
    directory = scan / "stage_results"
    empty = {
        "status": "ABSENT",
        "counts": {},
        "failed": [],
        "failed_data": [],
        "degraded": [],
        "skipped": [],
        "invalid_files": [],
        "contract_hash_mismatches": [],
    }
    paths = sorted(directory.glob("*.json")) if directory.is_dir() else []
    if not paths:
        return empty
    contract = run_contract_health(scan)
    expected_hash = (
        contract["contract_hash"]
        if contract["status"] in {"OK", "INVALID"} and contract["contract_hash"]
        else None
    )
    results = []
    invalid = []
    mismatches = []
    for path in paths:
        try:
            result = load_stage_result(path)
            results.append(result)
            if expected_hash is not None and result.contract_hash != expected_hash:
                mismatches.append(result.stage)
        except (OSError, TypeError, ValueError, json.JSONDecodeError):
            invalid.append(path.name)
    counts = Counter(result.status for result in results)
    failed = sorted(r.stage for r in results if r.status == "FAILED")
    failed_data = []
    for stage in failed:
        if stage == "gate4" and _gate4_has_data_fail(scan) is False:
            continue
        failed_data.append(stage)
    return {
        "status": "INVALID" if invalid or mismatches else "OK",
        "counts": dict(sorted(counts.items())),
        "failed": failed,
        "failed_data": failed_data,
        "degraded": sorted(r.stage for r in results if r.status == "DEGRADED"),
        "skipped": sorted(r.stage for r in results if r.status == "SKIPPED"),
        "invalid_files": sorted(invalid),
        "contract_hash_mismatches": sorted(mismatches),
    }


def decision_records_health(scan_dir: Path) -> dict:
    """校验影子决策事实本身，以及与旧终评级/早停入口的 parity。"""
    from autoresearch.scan.decision_record import load_decision_records

    scan = Path(scan_dir)
    path = scan / "decision_records.json"
    empty = {
        "status": "ABSENT",
        "n_records": 0,
        "contract_hash_match": None,
        "final_ratings_match": None,
        "rating_mismatches": [],
        "early_stop_match": None,
        "early_stop_mismatches": [],
        "error": None,
    }
    if not path.exists():
        return empty
    try:
        records = load_decision_records(path)
    except (OSError, TypeError, ValueError, json.JSONDecodeError) as exc:
        return {**empty, "status": "INVALID", "error": str(exc)}

    contract = run_contract_health(scan)
    expected_hash = contract.get("contract_hash")
    contract_match = (
        None
        if expected_hash is None
        else all(
            record.contract_hash == expected_hash for record in records.values()
        )
    )
    legacy_ratings = _json_object(scan / "_final_ratings.json")
    ratings = {
        code: record.final_rating for code, record in records.items()
    }
    rating_mismatches = []
    ratings_match = None
    if legacy_ratings is not None:
        rating_mismatches = sorted(
            code
            for code in set(ratings) | set(legacy_ratings)
            if ratings.get(code) != legacy_ratings.get(code)
        )
        ratings_match = not rating_mismatches

    legacy_early = _json_object(scan / "_early_stop.json")
    early = {
        code: record.early_stop
        for code, record in records.items()
        if record.early_stop is not None
    }
    early_mismatches = []
    early_match = None
    if legacy_early is not None:
        early_mismatches = sorted(
            code
            for code in set(early) | set(legacy_early)
            if early.get(code) != legacy_early.get(code)
        )
        early_match = not early_mismatches

    mismatch = (
        contract_match is False
        or ratings_match is False
        or early_match is False
    )
    return {
        "status": "MISMATCH" if mismatch else "OK",
        "n_records": len(records),
        "contract_hash_match": contract_match,
        "final_ratings_match": ratings_match,
        "rating_mismatches": rating_mismatches,
        "early_stop_match": early_match,
        "early_stop_mismatches": early_mismatches,
        "error": None,
    }


def post_run_health(scan_dir: Path) -> dict:
    """只读汇总 outbox/consumer receipts；缺席是兼容旧现场的 advisory。"""
    empty = {
        "status": "ABSENT",
        "n_events": 0,
        "expected": 0,
        "succeeded": 0,
        "pending": 0,
        "pending_consumers": [],
        "failed_consumers": [],
        "error": None,
    }
    scan = Path(scan_dir)
    from autoresearch.scan.outbox import outbox_path

    if not outbox_path(scan).exists():
        return empty
    try:
        from autoresearch.scan.post_run import consumer_status

        return {**consumer_status(scan), "error": None}
    except (OSError, TypeError, ValueError, json.JSONDecodeError) as exc:
        return {
            **empty,
            "status": "INVALID",
            "error": f"{type(exc).__name__}: {exc}",
        }


def run_health(scan_dir: Path) -> dict:
    """一次 scan 的体检 dict(artifacts/counts/NaN 降级/churn/L4 阶段/meta 回显)。"""
    scan_dir = Path(scan_dir)
    arts = {a: (scan_dir / a).exists() for a in _ARTIFACTS}
    missing = [a for a, ok in arts.items() if not ok]
    meta = {}
    mp = scan_dir / "meta.json"
    if mp.exists():
        try:
            meta = json.loads(mp.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            meta = {}

    def _n(name: str) -> int:
        df = _read(scan_dir / name)
        return len(df) if df is not None else 0

    cards = len(list((scan_dir / "details").glob("*.md"))) if (scan_dir / "details").is_dir() else 0
    rates, degraded = nan_report(scan_dir)
    anns_rate = anns_empty_rate(scan_dir)
    buys_source = None
    try:
        buys, buys_source = count_buys_with_source(scan_dir)
    except (OSError, TypeError, ValueError, json.JSONDecodeError):
        buys = None
    counts = {"l1_full": _n("L1_scored_full.csv"), "recall": _n("L1_recall_top1000.csv"),
              "l2": _n("L2_gbdt_top200.csv"), "finalists": _n("finalists.csv"),
              "cards": cards, "buys": buys}
    if buys_source is not None:      # active 期才有这个键 —— shadow 期 run_health 逐字节不变
        counts["buys_source"] = buys_source
    return {"date": scan_dir.name, "artifacts": arts, "missing": missing,
            "core_missing": sorted(_CORE & set(missing)),
            "counts": counts,
            "nan_rates": rates, "degraded_fields": degraded,
            # 旧口径(Wave9 A-1 前):"=1.0 是 expected 非告警"已被推翻,当前权威判据是下面
            # anns_source_status 的四态(blind=warn,pending=L3 还没跑到;详见该函数
            # docstring)。键名/数值原样保留仅供下游兼容(product_shape_lint 旧 run 回落 +
            # index_md 既有渲染行),不代表这仍是当前判据。
            "anns_empty_rate": anns_rate,
            "anns_source_status": anns_source_status(scan_dir),
            "anns_expected": anns_rate is None or anns_rate >= 1.0,
            "northbound": northbound_probe(scan_dir),
            "regime": meta.get("regime"), "l2_engine": meta.get("l2_engine"),
            "weights_source": meta.get("weights_source"),
            "churn": finalist_churn(scan_dir), "l4_phases": l4_phase_stats(scan_dir),
            "run_contract": run_contract_health(scan_dir),
            "stage_results": stage_results_health(scan_dir),
            "decision_records": decision_records_health(scan_dir),
            "post_run": post_run_health(scan_dir)}


def write_run_health(scan_dir: Path) -> Path:
    p = Path(scan_dir) / "run_health.json"
    p.write_text(json.dumps(run_health(scan_dir), ensure_ascii=False, indent=2), encoding="utf-8")
    return p


def index_md(scan_dir: Path, report_dir: Path) -> str:
    """报告目录导航页:summary/决策卡/trace 链接 + staging 在位 + 上一 run + 健康一行。"""
    scan_dir, report_dir = Path(scan_dir), Path(report_dir)
    h = run_health(scan_dir)
    # Wave12-T28:入口切 brief —— 报告分两层,先读 ≤3KB 的确定性速读层,再按需下钻详细版。
    # brief 缺席时**明说**(不静默回落到 summary):静默会让「brief 没生成」这件事不可见,
    # 而 self_review 的 brief lint 正把它当 fail 处理。
    brief_ok = (report_dir / "brief.md").exists()
    lines = [f"# 扫描现场索引 — 数据日 {h['date']}(run `{report_dir.name}`)\n",
             ("- **读我(30 秒)**:[brief.md](brief.md)(≤3KB 确定性速读:市场/漏斗/BUY 结论"
              "/持仓/风险哨/昨日 delta/欠账)" if brief_ok else
              "- **读我(30 秒)**:`brief.md` **未生成** —— 速读层缺席(见 self_review 的 "
              "`brief·缺失` 条目),先读详细版"),
             "- **详细版**:[summary.md](summary.md)(buy-list + 漏斗 + 各阶段概览)"]
    cards = sorted((report_dir / "details").glob("*.md")) if (report_dir / "details").is_dir() else []
    if cards:
        lines.append(f"- **决策卡**({len(cards)} 张):" + "、".join(
            f"[{p.stem}](details/{p.name})" for p in cards[:40]))
    tr = sorted((report_dir / "trace").glob("*")) if (report_dir / "trace").is_dir() else []
    if tr:
        lines.append("- **溯源 trace/**:" + "、".join(f"[{p.name}](trace/{p.name})"
                                                      for p in tr if p.is_file()))
    ok = [a for a, v in h["artifacts"].items() if v]
    lines.append(f"- **staging(中间结果)**:`{scan_dir}/` — 在位:{('、'.join(ok)) or '—'}"
                 + (f";**缺**:{'、'.join(h['missing'])}" if h["missing"] else ""))
    # 六个证据事实分行(设计稿 §8.1):业务/证据/完好/完整/可重放/归档 各自成立或不成立,
    # 任何一项都不得代表其余五项。
    from autoresearch.scan.evidence import evidence_facts, render_evidence_lines

    lines.append("- **现场证据**:")
    lines += [f"  {row}" for row in render_evidence_lines(evidence_facts(report_dir))]
    prevs = sorted((p.name for p in report_dir.parent.iterdir()
                    if p.is_dir() and p.name < report_dir.name and (p / "summary.md").exists()),
                   reverse=True)
    if prevs:
        lines.append(f"- **上一 run**:[`{prevs[0]}`](../{prevs[0]}/summary.md)")
    c, ch = h["counts"], h["churn"]
    hl = (f"- **健康一行**:L1 {c['recall']} → L2 {c['l2']} → finalists {c['finalists']} → "
          f"卡 {c['cards']} → 买 {c['buys']}")
    if ch:
        hl += f";finalist 重叠 {ch['n_repeat']}/{ch['n_today']}(vs {ch['prev_date']})"
    if h["degraded_fields"]:
        hl += f";⚠️ 降级字段:{'、'.join(h['degraded_fields'])}"
    lines.append(hl)
    if h["anns_expected"]:
        # anns_d 已退役(2026-07-18):此前只在 run_health.json 里挂 anns_expected=True,报告
        # 正文完全无感——这正是 news_n/news_sent/news_head 三个扫描日全为 0 却无人察觉的成因
        # 之一(线 D 退役配套,断链必须留痕)。
        # ⚠️ Wave9 A-1 复核轮1:"expected 语义不变"已过时——这行沿用的是 anns_empty_rate
        # 旧口径(本次改动未迁移这里的判据,范围外)。**当前**权威判据是 anns_source_status
        # 四态(blind=有稿双源皆空=warn,不是 expected;pending=L3 还没跑到)。这行文案不
        # 代表 blind 仍算 expected,判"双源健康与否"请读 anns_source_status,不要读这里。
        lines.append("- **公告标题流**:不可用(anns_d 已退役,详见 `run_health.json` "
                     "`anns_empty_rate`)")
    return "\n".join(lines) + "\n"
