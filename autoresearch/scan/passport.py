#!/usr/bin/env python3
"""候选护照(F2-I)—— 一只票从 L1 召回到 L4 决策卡的全轨迹,收敛成一行稳定的结构。

design: Wave12 F2-I。产物 `context/scan/<date>/_candidate_passport.json`;
E6(T22)/F3(T21)/brief(T25)**只读它**,不再各自去拼上游 CSV。

## 病灶

「因何而来」在 L2 被抹平:一只票是 healthy 通道第 3 名进来的、还是 composite 第 189 名
勉强够线,进了 `L2_gbdt_top200.csv` 之后就只剩一个 `recall_channels` 字符串;到了 L3/L4
更是连这个也不再有人读。护照把 L1 逐路名次 → L2 因何进菜单 → pass1 因何留下 → L3 tier
→ L4 终评级这条链**逐票接起来**,让下游能问「这只票在哪一段被降级了」。

## 三条铁律

1. **纯派生视图**:只读既有产物,不改任何上游、不改任何 prompt(prompt byte diff = 0)。
   护照错了就改护照,不许回头去动 L1/L2/L3/L4。
2. **确定性**:同一份输入重复构建 **byte 稳定**。所有由数据决定的 key 集合显式排序、
   落盘走 `sort_keys=True`,**不写时间戳、不写随机序**。护照进不了任何 hash 链的前提
   就是它自己得先是个常量函数。
3. **缺源写 `null` 并记 `missing[]`,不猜**。尤其不许拿 `l2_lane_reserved` 反推
   `selection_reason`——前者是「merit 核之外的全部」二值旗,后者把那一团拆成五种进场
   方式,两者语义不同不得互换(见 `recall/l2_stratify.py` 的词表注释)。

## `missing[]` 的语义(最容易写错的一条)

`missing[]` 只记「**到了这一站、却读不到那个字段**」;**没走到某一站不是 missing**。
190 只在 pass1 被切掉的票没有 L4 评级是漏斗的正常形态,不是数据缺失——把它记进
missing 会让这个数组每天都有 190 条噪音,真的缺列反而被淹掉。所以每个 stage 块都带
一个 `reached` 语义的布尔(`pass1.kept` / `l3.judged` / `l4.carded`),`null` 只表示
「这一站没到」或「这一站到了但字段读不到」,由 `missing[]` 区分这两种。

## 两个同名不同义的 `selection_reason`

`L2_gbdt_top200.csv`(T16 起投影落盘)与 `_l3_pass1_kept.csv` **都有**一列叫
`selection_reason`,但词表不同:L2 侧是 `merit|lane|sector|pinned|backfill`,pass1 侧是
`pinned|conviction_guard|lane|backfill`。`_l3_pass1_kept.csv` 是从 L2 帧派生的,triage
把自己的理由**覆写**在同名列上——所以 pass1 文件里的那一列**不是** L2 的理由。护照两者
分开读、分开放(`l2.selection_reason` vs `pass1.reason`),永不互相顶替。

## `risk_flags` / `bench_reason` 是本模块新造的派生名

上游没有这两个字段(实测 `grep risk_flags` 全仓零命中)。护照把它们**确定性地**从既有
结构化事实合成,并在这里写死口径:

- `risk_flags` ← `decision_records.gate_states`(非 PASS 的门)+ `early_stop`(停在哪相)
  + `IntelStatus.availability_for_card`(情报到底在不在场)。排序后输出。
- `guard` ← `finalists.csv` / `_l3_bench.csv` 的 `guard` 列原值
  (`ins75|lt55|cap|healthy_quota|trend_quota|dup`,见 `l3/merge.py`)。它是**双向**标记:
  `ins75` 是被救**进** finalist,`healthy_quota`/`trend_quota` 由 `_swap_lane_quota` 给
  换进来的和被换出的**两边写同一个值**。
- `bench_reason` = `guard`,但**只在 `tier=="bench"` 时给值**,finalist 行恒 `null`。
  否则一个 `guard="ins75"` 的 finalist 会拿到 `bench_reason:"ins75"` —— 字段名承诺了
  它兑现不了的语义。想看 finalist 被哪条守卫动过,读 `guard`。

## 两个「不许拿数值冒充未知」的地方

- `recall.best_rank`:没被任何通道召回的 pinned 持仓,上游写 `10**9` 哨兵
  (`universe._PINNED_RANK_SENTINEL`)。护照一律转 `None` —— 实测 8 个交易日命中,
  其中 `920179` 连续 6 日,恰恰是必须参与横截面比较的持仓票。
- 根层 `orphans` / `counts.orphan_*`:行集合恒等于 L2 全集,但下游偶有 L2 之外的
  finalist/评级(实测 3 天共 45 只)。不改行集合,但把差额显式报出来,不静默吞掉。

## 前导零

A 股代码在 CSV 往返里会丢前导零(`002345` → `2345`,pandas 实测把 `001283` 读成
`1283`)。本模块**一律走 stdlib csv 读字符串 + `zfill(6)`**,不用 pandas 的类型推断。

  uv run --no-sync python -m autoresearch.scan.passport <date>
"""
from __future__ import annotations

import argparse
import csv
import json
import sys
from pathlib import Path

from autoresearch.common import workspace as ws
from autoresearch.scan.decision_read_model import read_decisions, read_final_ratings

SCHEMA_VERSION = 1
PASSPORT_RULE = "passport.v1"
PASSPORT_FILENAME = "_candidate_passport.json"

# `recall_channels` 里的哨兵 token —— 不是通道名(见 `l3/triage.py` 规则④的同款排除)
_CHANNEL_SENTINELS = frozenset({"", "(backfill)", "pinned"})

# `universe._inject_pinned_l1` 给「没被任何通道召回的 pinned 持仓」写的 best_rank 哨兵。
# 它是「无名次」的编码,**不是**一个可比大小的名次:直接透出去,下游拿它算百分位会被
# 10**9 整个拽偏。实测 8 个交易日命中(`688271` ×2、`920179` 连续 6 日),命中的恰是
# 必须参与横截面比较的持仓票。这里只**认**它、不产生它;数值漂移由
# `test_pinned_rank_sentinel_matches_universe` 盯着(那条测试直接 import 上游常量比对)。
_PINNED_RANK_SENTINEL = 10**9


# ── 读取原语(全部容忍缺文件;不容忍猜) ─────────────────────────────────────
def _rows(path: Path) -> list[dict] | None:
    """CSV → 行字典列表;文件不在返回 `None`(与「存在但空」区分开)。"""
    if not path.exists():
        return None
    with path.open(encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


def _json(path: Path) -> object | None:
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def _code(value: object) -> str:
    """`600188.SS` / `2345` / `002345` → `002345`。"""
    return str(value or "").strip().split(".")[0].zfill(6)


def _text(value: object) -> str | None:
    if value is None:
        return None
    return str(value)


def _int(value: object) -> int | None:
    try:
        return int(float(str(value).strip()))
    except (TypeError, ValueError):
        return None


def _float(value: object) -> float | None:
    try:
        got = float(str(value).strip())
    except (TypeError, ValueError):
        return None
    return None if got != got else got          # NaN → null(JSON 里没有 NaN)


def _flag(value: object) -> bool:
    return str(value).strip().lower() in {"1", "true", "yes"}


def _pctl(rank: int | None, total: int) -> float | None:
    """名次 → 头部分位(rank 1 = 1.0,越大越靠前)。总数为 0 时无从定义 → null。"""
    if rank is None or total <= 0:
        return None
    return round((total - rank + 1) / total, 6)


# ── 各阶段的事实抽取 ────────────────────────────────────────────────────────
def _recall_index(scan: Path) -> tuple[dict[str, dict[str, dict]], bool]:
    """`L1_channels.csv` → {code: {channel: {score, rank, pctl}}}(逐路名次的唯一来源)。"""
    rows = _rows(scan / "L1_channels.csv")
    if rows is None:
        return {}, False
    sizes: dict[str, int] = {}
    for row in rows:
        channel = str(row.get("channel") or "")
        sizes[channel] = sizes.get(channel, 0) + 1
    index: dict[str, dict[str, dict]] = {}
    for row in rows:
        channel = str(row.get("channel") or "")
        rank = _int(row.get("channel_rank"))
        entry = {
            "score": _float(row.get("channel_score")),
            "rank": rank,
            "pctl": _pctl(rank, sizes.get(channel, 0)),
        }
        slot = index.setdefault(_code(row.get("code")), {})
        prior = slot.get(channel)
        # 同 (channel, code) 重复出现时取**名次更好的那条**,不是"后者胜"——后者胜让结果
        # 依赖文件行序,与本模块的确定性承诺矛盾(重复行本身是上游异常,但护照不能因为
        # 上游抖一下就产出两份不同的护照)。
        if prior is None or (rank is not None
                             and (prior["rank"] is None or rank < prior["rank"])):
            slot[channel] = entry
    return index, True


def _best_rank(ranks: list[int], row: dict, has_channels: bool) -> int | None:
    """逐路名次里最好的一个;没有真名次就是 `None`,**不许拿哨兵冒充**。

    三种情形:
    ① 有逐路行 → `min(rank)`(真名次)。
    ② `L1_channels.csv` 在、但这只票一行都没有 → 它**根本没被任何通道召回**
       (pinned 直注 / `(backfill)` 补位)→ `None`。同一个 `recall` 块里
       `n_channels=0`、`channels=[]` 已把这件事说清楚,`best_rank` 再给个数就是自相矛盾。
       这不算 `missing[]`:没走过通道召回 ≠ 走了却读不到(见模块 docstring 的 missing 语义)。
    ③ `L1_channels.csv` 整个缺失 → 退回 L2 的 `best_rank` 列,但**过滤哨兵**。
    """
    if ranks:
        return min(ranks)
    if has_channels:
        return None
    fallback = _int(row.get("best_rank"))
    if fallback is None or fallback >= _PINNED_RANK_SENTINEL:
        return None
    return fallback


def _composite_pctl_index(scan: Path) -> tuple[dict[str, float | None], bool]:
    """`L1_scored_full.csv` 的 `rank` 是全体过门股按 composite 降序的名次 → 头部分位。"""
    rows = _rows(scan / "L1_scored_full.csv")
    if rows is None:
        return {}, False
    total = len(rows)
    return {_code(row.get("code")): _pctl(_int(row.get("rank")), total)
            for row in rows}, True


def _pass1_index(scan: Path) -> tuple[dict[str, dict], set[str], bool]:
    """kept(带 pass1 词表的理由)/ cut(按定义没有「为什么被选中」)。"""
    kept_rows = _rows(scan / "_l3_pass1_kept.csv")
    cut_rows = _rows(scan / "_l3_pass1_cut.csv")
    if kept_rows is None and cut_rows is None:
        return {}, set(), False
    kept = {
        _code(row.get("code")): {
            "reason": _text(row.get("selection_reason")),
            "detail": _text(row.get("selection_detail")),
        }
        for row in (kept_rows or [])
    }
    cut = {_code(row.get("code")) for row in (cut_rows or [])}
    return kept, cut, True


def _l3_index(scan: Path) -> tuple[dict[str, dict], dict[str, str], dict[str, str], bool]:
    """judged 判断 + finalists/bench 的 `guard` 列。

    `_l3_judged.json` 是 **LLM 写的**:形状不合预期(如写成 `{"judged":[...]}`)必须落到
    CSV 回退,而不是「认得出是 JSON 就当空名单收下」——后者会让全体 `judged=false` 且
    `missing[]` 全空,静默说假话。故 `else` 而非 `elif judged_raw is None`。
    同理 code 可能被 LLM 写成 JSON **数字** `34`,`_code()` 的 zfill 是唯一防线。
    """
    judged_raw = _json(scan / "_l3_judged.json")
    judged: dict[str, dict] = {}
    if isinstance(judged_raw, list):
        for row in judged_raw:
            if isinstance(row, dict) and row.get("code") is not None:
                judged[_code(row["code"])] = row
    else:
        for row in _rows(scan / "L3_judged_full.csv") or []:
            judged[_code(row.get("code"))] = row

    finalists = {_code(row.get("code")): str(row.get("guard") or "")
                 for row in _rows(scan / "finalists.csv") or []}
    bench = {_code(row.get("code")): str(row.get("guard") or "")
             for row in _rows(scan / "_l3_bench.csv") or []}
    present = bool(judged) or (scan / "finalists.csv").exists()
    return judged, finalists, bench, present


def _l4_index(scan: Path) -> dict:
    """L4 的结构化事实 —— 评级只走 `decision_read_model` 正门,不解析卡片自然语言。"""
    ratings = read_final_ratings(scan)
    records = {}
    if (scan / "decision_records.json").exists():
        records = {_code(code): record.to_dict()
                   for code, record in read_decisions(scan).items()}
    legacy_stop = _json(scan / "_early_stop.json")
    early_stop = {_code(code): value
                  for code, value in (legacy_stop or {}).items()
                  if isinstance(value, dict)} if isinstance(legacy_stop, dict) else {}

    task_book = _json(scan / "_l4_tasks.json")
    dispatched: set[str] = set()
    if isinstance(task_book, dict) and isinstance(task_book.get("tasks"), dict):
        dispatched = {_code(code) for code in task_book["tasks"]}

    intel: dict[str, str] = {}
    for path in sorted(scan.glob("_l4_intel_status_*.json")):
        status = _json(path)
        if isinstance(status, dict) and status.get("availability_for_card"):
            code = _code(status.get("code") or path.stem.rsplit("_", 1)[-1])
            intel[code] = str(status["availability_for_card"])
    return {"ratings": {_code(code): rating for code, rating in ratings.items()},
            "records": records, "early_stop": early_stop,
            "dispatched": dispatched, "intel": intel}


def _risk_flags(gate_states: dict, stopped: dict | None, intel: str | None) -> list[str]:
    """结构化事实 → 排序后的旗标列表(上游无此字段,口径由本模块定义)。"""
    flags = []
    for gate, state in (gate_states or {}).items():
        if state == "FAIL":
            flags.append(f"gate_fail:{gate}")
        elif state != "PASS":
            flags.append(f"gate_unknown:{gate}")
    if stopped:
        flags.append(f"early_stop:{stopped.get('phase', '?')}")
    if intel == "NONE":
        flags.append("intel_absent")
    elif intel == "CARD_FALLBACK":
        flags.append("intel_fallback")
    return sorted(flags)


# ── 主构建 ─────────────────────────────────────────────────────────────────
def build_passport(scan_dir: Path | str) -> dict:
    """`context/scan/<date>` → 候选护照文档(确定性、零 LLM、零联网)。

    候选集 = `L2_gbdt_top200.csv` 的全部行(**含 pinned 保送行**)。L2 之前的票不进护照:
    它们没有下游轨迹可言,而 L1 全量已经有 `L1_scored_full.csv` 自己在留底。
    """
    scan = Path(scan_dir)
    date = scan.name

    l2_rows = _rows(scan / "L2_gbdt_top200.csv")
    channels, has_channels = _recall_index(scan)
    composite_pctl, has_scored = _composite_pctl_index(scan)
    pass1_kept, pass1_cut, has_pass1 = _pass1_index(scan)
    judged, finalists, bench, has_l3 = _l3_index(scan)
    l4 = _l4_index(scan)

    has_selection_reason = bool(
        l2_rows and "selection_reason" in (l2_rows[0].keys() if l2_rows else ()))

    candidates: dict[str, dict] = {}
    for row in l2_rows or []:
        code = _code(row.get("code"))
        missing: list[str] = []

        # ── L1 召回 ──
        per_channel = channels.get(code, {})
        if has_channels:
            names = sorted(per_channel)
        else:
            missing.append("recall.per_channel")
            names = sorted(set(str(row.get("recall_channels") or "").split("|"))
                           - _CHANNEL_SENTINELS)
        ranks = [entry["rank"] for entry in per_channel.values()
                 if entry["rank"] is not None]
        if not has_scored or code not in composite_pctl:
            missing.append("recall.composite_pctl")
        recall = {
            "channels": names,
            "per_channel": {name: per_channel[name] for name in sorted(per_channel)},
            "unique": len(names) == 1,
            "n_channels": len(names),
            "best_rank": _best_rank(ranks, row, has_channels),
            "composite_pctl": composite_pctl.get(code),
        }

        # ── L2 粗排 ──
        if not has_selection_reason:
            missing += ["l2.selection_reason", "l2.selection_detail"]
        l2 = {
            "l2_rank": _int(row.get("l2_rank")),
            "selection_reason": (_text(row.get("selection_reason"))
                                 if has_selection_reason else None),
            "selection_detail": (_text(row.get("selection_detail"))
                                 if has_selection_reason else None),
            "lane_reserved": _flag(row.get("l2_lane_reserved")),
            "pinned": _flag(row.get("pinned")),
        }

        # ── L3 pass1 分诊 ──
        if not has_pass1:
            missing.append("pass1.kept")
        kept = pass1_kept.get(code)
        pass1 = {
            "kept": (None if not has_pass1
                     else (True if kept is not None
                           else (False if code in pass1_cut else None))),
            "reason": kept["reason"] if kept else None,
            "detail": kept["detail"] if kept else None,
        }
        if has_pass1 and pass1["kept"] is None:
            missing.append("pass1.kept")

        # ── L3 精排 ──
        verdict = judged.get(code)
        is_finalist = code in finalists
        guard = finalists.get(code) or bench.get(code) or ""
        tier = ("finalist" if is_finalist
                else ("bench" if verdict is not None else None))
        l3 = {
            "judged": (None if not has_l3 else verdict is not None),
            "finalist": (None if not has_l3 else is_finalist),
            "tier": tier,
            "conviction": _int(verdict.get("conviction")) if verdict else None,
            "mechanism": _text(verdict.get("mechanism")) if verdict else None,
            "lane": _text(verdict.get("lane")) if verdict else None,
            "triage_lean": _text(verdict.get("triage_lean")) if verdict else None,
            "guard": guard or None,
            # `guard` 是**双向**守卫标记,不是「为什么在 bench」:`ins75` 是被强行救**进**
            # finalist;`_swap_lane_quota` 对**换进来的和被换出的两边写同一个值**
            # (`merge.py:51-53`)。所以只有落在 bench 那一侧时它才真是"落选理由"
            # ——`bench_reason` 因此以 `tier=="bench"` 为门,finalist 行恒 null。
            # 全部信息仍在 `guard` 里,想看 finalist 被哪条守卫动过就读它。
            "bench_reason": (guard or None) if tier == "bench" else None,
        }

        # ── L4 决策卡 ──
        rating = l4["ratings"].get(code)
        record = l4["records"].get(code, {})
        stopped = record.get("early_stop") or l4["early_stop"].get(code)
        intel_avail = l4["intel"].get(code)
        dispatched = code in l4["dispatched"] if l4["dispatched"] else rating is not None
        if dispatched and rating is None:
            missing.append("l4.research_rating")
        card = {
            "dispatched": dispatched,
            "carded": rating is not None,
            "research_rating": rating,
            "card_kind": (None if rating is None
                          else ("earlystop" if stopped else "full")),
            "earlystop_phase": _text(stopped.get("phase")) if stopped else None,
            "earlystop_reason": _text(stopped.get("reason")) if stopped else None,
            "intel_avail": intel_avail,
            "risk_flags": (_risk_flags(record.get("gate_states") or {}, stopped,
                                       intel_avail) if rating is not None else []),
            "proposal": _text(record.get("proposal")) or None,
            "first_rejection_stage": _text(record.get("first_rejection_stage")) or None,
        }

        candidates[code] = {
            "code": code,
            "name": _text(row.get("name")),
            "sector": _text(row.get("industry")),
            "recall": recall,
            "l2": l2,
            "pass1": pass1,
            "l3": l3,
            "l4": card,
            "missing": sorted(set(missing)),
            "versions": {"rule": PASSPORT_RULE, "date": date},
        }

    # 行集合 = L2 全集(spec 断言①)。下游更靠后的产物**偶尔会带 L2 之外的票**:实测
    # 06-22 / 06-26 / 07-01 三天共 45 只 finalist 不在当日 L2 里(早期 run 的漏斗形态)。
    # 不改行集合口径(那会破坏断言①),但**必须留一个对账信号**——静默少 45 只 finalist
    # 而 counts 一切正常,下游(只读护照的 T21/T22/T25)会以为它们不存在。
    orphan_finalists = sorted(set(finalists) - set(candidates))
    orphan_rated = sorted(set(l4["ratings"]) - set(candidates))
    return {
        "schema_version": SCHEMA_VERSION,
        "rule": PASSPORT_RULE,
        "date": date,
        "orphans": {"finalists": orphan_finalists, "rated": orphan_rated},
        "sources": {
            "l1_channels": "PRESENT" if has_channels else "ABSENT",
            "l1_scored_full": "PRESENT" if has_scored else "ABSENT",
            "l2": "PRESENT" if l2_rows is not None else "ABSENT",
            "l2_selection_reason": "PRESENT" if has_selection_reason else "ABSENT",
            "pass1": "PRESENT" if has_pass1 else "ABSENT",
            "l3_judged": "PRESENT" if has_l3 else "ABSENT",
            "decision_records": ("PRESENT" if (scan / "decision_records.json").exists()
                                 else "ABSENT"),
        },
        "counts": {
            "candidates": len(candidates),
            "pass1_kept": sum(1 for e in candidates.values() if e["pass1"]["kept"]),
            "l3_judged": sum(1 for e in candidates.values() if e["l3"]["judged"]),
            "l3_finalists": sum(1 for e in candidates.values() if e["l3"]["finalist"]),
            "l4_carded": sum(1 for e in candidates.values() if e["l4"]["carded"]),
            "with_missing": sum(1 for e in candidates.values() if e["missing"]),
            "orphan_finalists": len(orphan_finalists),
            "orphan_rated": len(orphan_rated),
        },
        "candidates": {code: candidates[code] for code in sorted(candidates)},
    }


def write_passport(scan_dir: Path | str) -> Path:
    """构建并原子落盘。`sort_keys=True` 是 byte 稳定契约的一半,另一半是构建本身无时序量。"""
    scan = Path(scan_dir)
    target = scan / PASSPORT_FILENAME
    payload = json.dumps(build_passport(scan), ensure_ascii=False, indent=2,
                         sort_keys=True) + "\n"
    target.parent.mkdir(parents=True, exist_ok=True)
    temp = target.with_name(f"{target.name}.tmp")
    temp.write_text(payload, encoding="utf-8")
    temp.replace(target)
    return target


def safe_write_passport(scan_dir: Path | str) -> Path | None:
    """派生视图失败不得阻断发布(护照没有任何上游依赖它)。"""
    try:
        return write_passport(scan_dir)
    except Exception as exc:  # noqa: BLE001 — 纯派生件失败只记一行,不连累主链
        print(f"[passport] 构建失败: {type(exc).__name__}: {exc}", file=sys.stderr)
        return None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="候选护照(L1→L4 派生视图)")
    parser.add_argument("scan", help="分析日(YYYY-MM-DD)或 scan 目录")
    args = parser.parse_args(argv)
    explicit = Path(args.scan)
    scan = explicit if explicit.exists() else ws.scan_root() / args.scan
    target = write_passport(scan)
    doc = json.loads(target.read_text(encoding="utf-8"))
    print(json.dumps({"ok": True, "path": str(target), "counts": doc["counts"],
                      "sources": doc["sources"]}, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
