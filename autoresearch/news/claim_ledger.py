#!/usr/bin/env python3
"""情报治理 v2 —— claim ledger + 分层 lint + 日期焊接检测(确定性,零 LLM)。

design: docs/specs/2026-08-03-scan-next-wave-brainstorm-design.md §1.3 D3

## 现状与病

`l4-intel` 的稿件里每句话都带 `[source|date|url]`。问题是:**那只是展示格式**。
它长得像引用,但没有任何东西核对过 url 可访问、date 对得上、内容真的支撑这句话。
`pr_20260714_006`(intel 捏造涨停)正是在这个缝里发生的。

## 五条(§1.3 逐条落地)

1. **先建 claim ledger**:每个原子 claim 有
   `claim_id / subject / predicate / value / effective_date / status /
   citation_observation_ids[]`。自然语言 `[source|date|url]` **只能作为展示格式**,
   不能充当真实性校验。
2. **lint 分层**:先验字段与抓取 artifact/hash,再记录三个 verdict ——
   `accessible`(能不能取到)/ `fields_match`(字段对不对得上)/ `content_supports`
   (内容支不支撑)。缺引用/错引**只拒 intel 稿、不拒票**;回退路径独立可测。
3. **日期焊接检测**:claim 日期同时和 `published_ts / first_seen_ts / effective_date`
   对账;对不上先标 `UNVERIFIED`,**覆盖不足时不自动判假**。
4. **源/模型责任分离**:retro 回写 verdict 与原因,区分**来源原文错误 / LLM 错引 /
   过期事实**。样本稀疏或选择性证伪时**不发布**伪精确的 source precision。
5. **cap 改为可观测预算**:当前 guard 是 trim 而非「超 30 必拒」;用**真实工具
   telemetry** 约束 search/fetch 次数与耗时,**自报字段只作诊断**。

## 边界

价格类断言由 `scan/price_claims.py` 核验(它只管价格/日期/涨跌幅)。
**公告事实另建 excerpt/page/hash verifier,不得复用价格对账器**(§1.4 核验分工)。

  uv run --no-sync python -m autoresearch.news.claim_ledger lint <scan_dir>
"""
from __future__ import annotations

import argparse
import hashlib
import re
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta
from pathlib import Path

import pandas as pd

SCHEMA_VERSION = 1
RULE_VERSION = "claim_ledger.v1"
LEDGER_NAME = "_claim_ledger.csv"

# ── claim 状态 ──────────────────────────────────────────────────
VERIFIED = "VERIFIED"
UNVERIFIED = "UNVERIFIED"       # 证据不足 —— **不是**判假
REFUTED = "REFUTED"             # 有反证
UNPARSED = "UNPARSED"           # 解析不出结构 → 不拒票
STATUSES = (VERIFIED, UNVERIFIED, REFUTED, UNPARSED)

# ── lint 三段 verdict(§1.3-2)────────────────────────────────────
ACCESSIBLE = "accessible"
FIELDS_MATCH = "fields_match"
CONTENT_SUPPORTS = "content_supports"
LINT_LAYERS = (ACCESSIBLE, FIELDS_MATCH, CONTENT_SUPPORTS)
PASS, FAIL, UNKNOWN = "PASS", "FAIL", "UNKNOWN"

# ── §1.3-4 责任归属 ─────────────────────────────────────────────
BLAME_SOURCE = "source_error"       # 来源原文就是错的
BLAME_MODEL = "model_miscitation"   # LLM 错引(来源没这么说)
BLAME_STALE = "stale_fact"          # 事实过期(当时对,现在不对)
BLAME_UNKNOWN = "undetermined"
BLAMES = (BLAME_SOURCE, BLAME_MODEL, BLAME_STALE, BLAME_UNKNOWN)

# 日期焊接容忍窗:公告日与我们首见日天然有滞后(盘后发/周末发)。
# 超出这个窗才算「对不上」,而且**只标 UNVERIFIED**,不判假。
DATE_WELD_TOLERANCE_DAYS = 3

# §1.3-5:预算按**真实 tool telemetry**,自报只作诊断。
DEFAULT_SEARCH_BUDGET = 30
DEFAULT_FETCH_BUDGET = 40
DEFAULT_WALL_SECONDS = 600.0

# 展示格式 `[source|date|url]` —— 只用来**定位** claim,不作为真实性证据。
_CITATION = re.compile(r"\[([^\]|]+)\|([^\]|]*)\|([^\]]*)\]")
_DATE = re.compile(r"(20\d{2})[-/年](\d{1,2})[-/月](\d{1,2})")


class ClaimError(ValueError):
    """账本契约违约。"""


@dataclass
class Claim:
    """一个**原子** claim。粒度到「主语-谓语-值-生效日」,不是一整句话。"""
    claim_id: str
    subject: str
    predicate: str
    value: str
    effective_date: str | None
    citation_observation_ids: list = field(default_factory=list)
    raw_citation: str = ""
    status: str = UNVERIFIED
    blame: str = BLAME_UNKNOWN
    lint: dict = field(default_factory=dict)
    note: str = ""
    rule_version: str = RULE_VERSION

    def as_dict(self) -> dict:
        return asdict(self)


def claim_id(subject: str, predicate: str, value: str, effective_date: str | None) -> str:
    blob = f"{subject}|{predicate}|{value}|{effective_date or ''}"
    return "cl_" + hashlib.sha256(blob.encode("utf-8")).hexdigest()[:16]


def _date_in(text: str) -> str | None:
    m = _DATE.search(str(text or ""))
    if not m:
        return None
    return f"{m.group(1)}-{int(m.group(2)):02d}-{int(m.group(3)):02d}"


def parse_claims(text: str, *, subject: str) -> list[Claim]:
    """稿件 → 原子 claim 列表。

    **解析不出结构的行 → `UNPARSED`,不拒票**(§1.3-2 红线:只拒稿不拒票)。
    每行取第一个 `[source|date|url]` 作为展示引用;**引用存在 ≠ 已验证**,
    状态一律从 `UNVERIFIED` 起步。
    """
    claims: list[Claim] = []
    for raw in str(text or "").splitlines():
        line = raw.strip().lstrip("-*·").strip()
        if not line or line.startswith("#"):
            continue
        citation = _CITATION.search(line)
        body = _CITATION.sub("", line).strip(" |")
        if not body:
            continue
        if citation is None:
            claims.append(Claim(
                claim_id=claim_id(subject, "unparsed", body[:80], None),
                subject=subject, predicate="unparsed", value=body[:200],
                effective_date=None, status=UNPARSED,
                note="无引用格式 —— 只拒稿不拒票"))
            continue
        source, date_text, url = (g.strip() for g in citation.groups())
        effective = _date_in(date_text) or _date_in(body)
        predicate, value = _split_predicate(body)
        claims.append(Claim(
            claim_id=claim_id(subject, predicate, value, effective),
            subject=subject, predicate=predicate, value=value,
            effective_date=effective,
            raw_citation=f"[{source}|{date_text}|{url}]",
            status=UNVERIFIED,
            note="引用是**展示格式**,不是真实性证据"))
    return claims


_PREDICATE_WORDS = ("回购", "增持", "减持", "中标", "订单", "立案", "问询", "重组",
                    "收购", "获批", "涨停", "跌停", "定增", "质押", "解禁", "预增",
                    "预减", "亏损", "扭亏")


def _split_predicate(body: str) -> tuple[str, str]:
    for word in _PREDICATE_WORDS:
        if word in body:
            return word, body[:200]
    return "statement", body[:200]


# ───────────────────────── 分层 lint ─────────────────────────


def lint_claim(claim: Claim, *, catalog=None, artifacts: dict | None = None,
               as_of: str | None = None) -> Claim:
    """三段 verdict + 日期焊接检测。**先验字段与 artifact,再谈内容支撑**(§1.3-2)。

    `artifacts`:`{observation_id: {"hash": ..., "text": ...}}` —— 抓取留档。
    缺留档 → `accessible=UNKNOWN`(**不是 FAIL**:没抓过不等于抓不到)。
    """
    verdicts = dict.fromkeys(LINT_LAYERS, UNKNOWN)
    reasons: list[str] = []
    artifacts = artifacts or {}

    ids = list(claim.citation_observation_ids or [])
    if not ids:
        reasons.append("无 source_observation_id —— 自然语言引用不能充当真实性校验")
    else:
        found = [i for i in ids if i in artifacts]
        verdicts[ACCESSIBLE] = PASS if found else UNKNOWN
        if found:
            texts = [str(artifacts[i].get("text") or "") for i in found]
            verdicts[CONTENT_SUPPORTS], reason = _supports(claim, texts)
            if reason:
                reasons.append(reason)

    weld = detect_date_weld(claim, catalog=catalog, as_of=as_of)
    verdicts[FIELDS_MATCH] = weld["verdict"]
    reasons.extend(weld["reasons"])

    claim.lint = {"verdicts": verdicts, "reasons": reasons,
                  "date_weld": weld, "rule_version": RULE_VERSION}
    claim.status = _status_from(verdicts, claim.status)
    claim.blame = _blame_from(verdicts, claim.status)
    return claim


def _supports(claim: Claim, texts: list[str]) -> tuple[str, str]:
    """留档正文支不支撑这条 claim → (verdict, reason)。

    **默认 UNKNOWN,只在有正证据时才 PASS/FAIL** —— 这是 `price_claims` Wave10 A3 那一课的
    移植:旧版「默认认领 + 列举排除」词表漏一个词就多一条「分析师捏造」的自信误指控。
    这里同样先定**可判定的东西**:

    - `predicate` 是受控词表里的事件词(回购/重组/立案…):
        · 正文里有它 → `PASS`(来源确实在说这件事);
        · 正文里没有 → `FAIL`(来源通篇不提这件事 = 正证据的错引);
    - `predicate == "statement"`(没落进受控词表)→ `UNKNOWN`,不猜。

    **不做**朴素子串匹配:claim 是自然语言复述,「公司回购股份」不会逐字出现在
    「关于回购股份的公告」里,拿子串判假会把正确引用判成捏造。
    """
    if claim.predicate not in _PREDICATE_WORDS:
        return UNKNOWN, ("claim 谓语不在受控词表 —— 无法从正文判定支撑与否,"
                         "按 UNKNOWN 处理(不猜)")
    if any(claim.predicate in text for text in texts):
        return PASS, ""
    return FAIL, (f"留档正文通篇不提「{claim.predicate}」 —— 来源不支撑该 claim")


def _status_from(verdicts: dict, current: str) -> str:
    if current == UNPARSED:
        return UNPARSED
    if FAIL in verdicts.values():
        return REFUTED
    if all(v == PASS for v in verdicts.values()):
        return VERIFIED
    return UNVERIFIED           # 证据不足 —— **不是**判假


def _blame_from(verdicts: dict, status: str) -> str:
    """责任分离(§1.3-4)。分不清 → `undetermined`,**不硬扣给模型**。"""
    if status != REFUTED:
        return BLAME_UNKNOWN
    if verdicts.get(CONTENT_SUPPORTS) == FAIL:
        return BLAME_MODEL      # 留档在,但正文不支撑 → LLM 错引
    if verdicts.get(FIELDS_MATCH) == FAIL:
        return BLAME_STALE      # 字段对不上多半是拿了过期/错位的事实
    return BLAME_UNKNOWN


def detect_date_weld(claim: Claim, *, catalog=None,
                     as_of: str | None = None) -> dict:
    """日期焊接检测 —— claim 日期同时与 `published_ts / first_seen_ts / effective_date` 对账。

    **覆盖不足时不自动判假**(§1.3-3):目录里查不到这条观测 → `UNKNOWN`,
    因为「我们没存」和「它不存在」是两件事。
    """
    if not claim.effective_date:
        return {"verdict": UNKNOWN, "reasons": ["claim 无可解析日期"],
                "observed": {}}
    if catalog is None or not claim.citation_observation_ids:
        return {"verdict": UNKNOWN,
                "reasons": ["无目录或无 observation 引用 —— 覆盖不足,不判假"],
                "observed": {}}
    obs = catalog.observations()
    rows = obs[obs["source_observation_id"].isin(claim.citation_observation_ids)] \
        if len(obs) else obs
    if not len(rows):
        return {"verdict": UNKNOWN,
                "reasons": ["目录查无此观测 —— 「我们没存」不等于「它不存在」"],
                "observed": {}}

    claimed = datetime.fromisoformat(claim.effective_date)
    observed: dict[str, str] = {}
    within = False
    for row in rows.itertuples(index=False):
        for label, value in (("published_ts", row.published_ts),
                             ("first_seen_ts", row.first_seen_ts),
                             ("event_date", row.event_date)):
            if not str(value or "").strip():
                continue
            observed.setdefault(label, str(value))
            try:
                actual = datetime.fromisoformat(str(value)).replace(tzinfo=None)
            except ValueError:
                continue
            if abs(actual - claimed) <= timedelta(days=DATE_WELD_TOLERANCE_DAYS):
                within = True
    if not observed:
        return {"verdict": UNKNOWN, "reasons": ["观测无任何时间字段"], "observed": {}}
    if within:
        return {"verdict": PASS, "reasons": [], "observed": observed}
    return {"verdict": FAIL,
            "reasons": [f"claim 日期 {claim.effective_date} 与观测时间 {observed} "
                        f"相差超过 {DATE_WELD_TOLERANCE_DAYS} 天 —— 疑似日期焊接"],
            "observed": observed}


# ───────────────────────── 稿件级裁决(只拒稿不拒票)─────────────────────────


def review_draft(text: str, *, subject: str, catalog=None,
                 artifacts: dict | None = None,
                 telemetry: dict | None = None) -> dict:
    """整稿裁决。**红线:只拒稿不拒票** —— 无论什么结果,本票照常出卡。"""
    claims = [lint_claim(c, catalog=catalog, artifacts=artifacts)
              for c in parse_claims(text, subject=subject)]
    counts = {s: sum(1 for c in claims if c.status == s) for s in STATUSES}
    budget = check_budget(telemetry)
    reject = counts[REFUTED] > 0 or budget["over_budget"]
    return {
        "schema_version": SCHEMA_VERSION,
        "subject": subject,
        "n_claims": len(claims),
        "counts": counts,
        "blame": {b: sum(1 for c in claims if c.blame == b and c.status == REFUTED)
                  for b in BLAMES},
        "budget": budget,
        "draft_verdict": "REJECT_DRAFT" if reject else "ACCEPT_DRAFT",
        # 红线写进产物本身,免得下游读着读着就把拒稿当成拒票
        "ticket_verdict": "KEEP",
        "red_line": "只拒稿不拒票 —— 情报是辅助面,不得反噬决策主链",
        "claims": [c.as_dict() for c in claims],
    }


def check_budget(telemetry: dict | None) -> dict:
    """预算按**真实 tool telemetry**;自报字段只作诊断(§1.3-5)。

    没有 telemetry → `over_budget=False` 且 `basis="none"` —— 缺计量不等于超预算,
    这正是「cap 改为可观测预算」与旧的「自报超 30 必拒」的分界。
    """
    tel = telemetry or {}
    searches = tel.get("tool_search_calls")
    fetches = tel.get("tool_fetch_calls")
    seconds = tel.get("wall_seconds")
    claimed = tel.get("self_reported_queries")
    if searches is None and fetches is None and seconds is None:
        return {"basis": "none", "over_budget": False,
                "self_reported_queries": claimed,
                "note": "无真实 tool telemetry —— 缺计量不等于超预算;"
                        "自报字段只作诊断,不作判据"}
    breaches = []
    if searches is not None and searches > DEFAULT_SEARCH_BUDGET:
        breaches.append(f"search {searches} > {DEFAULT_SEARCH_BUDGET}")
    if fetches is not None and fetches > DEFAULT_FETCH_BUDGET:
        breaches.append(f"fetch {fetches} > {DEFAULT_FETCH_BUDGET}")
    if seconds is not None and seconds > DEFAULT_WALL_SECONDS:
        breaches.append(f"wall {seconds:.0f}s > {DEFAULT_WALL_SECONDS:.0f}s")
    drift = (None if claimed is None or searches is None
             else int(claimed) - int(searches))
    return {"basis": "tool_telemetry", "over_budget": bool(breaches),
            "breaches": breaches, "tool_search_calls": searches,
            "tool_fetch_calls": fetches, "wall_seconds": seconds,
            "self_reported_queries": claimed, "self_report_drift": drift,
            "note": "自报与真实调用数并列存;两者背离即告警,但判据只看真实 telemetry"}


# ───────────────────────── 落盘 / 源精度(不发布伪精确)─────────────────────────

_LEDGER_COLUMNS = ["claim_id", "subject", "predicate", "value", "effective_date",
                   "status", "blame", "citation_observation_ids", "raw_citation",
                   "note", "rule_version"]


def write_ledger(scan_dir: Path | str, claims: list[Claim]) -> Path:
    day = Path(scan_dir)
    day.mkdir(parents=True, exist_ok=True)
    rows = []
    for claim in claims:
        record = claim.as_dict()
        record["citation_observation_ids"] = "|".join(
            record.get("citation_observation_ids") or [])
        record.pop("lint", None)
        rows.append(record)
    path = day / LEDGER_NAME
    pd.DataFrame(rows, columns=_LEDGER_COLUMNS).to_csv(path, index=False)
    return path


MIN_SOURCE_PRECISION_N = 20


def source_precision(claims: list[Claim] | pd.DataFrame) -> dict:
    """按来源统计 verdict —— **样本稀疏时不发布比例**(§1.3-4)。

    「3 条里错了 1 条 → 该源 33% 错引率」是典型的伪精确;而且我们只对**被质疑的**
    claim 做深核(选择性证伪),分母本身就不是随机样本。所以 n < 20 只报计数。
    """
    frame = (claims if isinstance(claims, pd.DataFrame)
             else pd.DataFrame([c.as_dict() for c in claims]))
    if not len(frame):
        return {"sources": {}, "policy": _PRECISION_POLICY}
    out: dict[str, dict] = {}
    for source, group in frame.groupby(frame["raw_citation"].fillna("").map(
            lambda s: s.strip("[]").split("|")[0] if s else "(无引用)")):
        n = int(len(group))
        refuted = int((group["status"] == REFUTED).sum())
        entry = {"n": n, "refuted": refuted,
                 "verified": int((group["status"] == VERIFIED).sum()),
                 "unverified": int((group["status"] == UNVERIFIED).sum())}
        entry["precision"] = (round(1 - refuted / n, 4)
                              if n >= MIN_SOURCE_PRECISION_N else None)
        entry["precision_suppressed_reason"] = (
            None if entry["precision"] is not None
            else f"n={n} < {MIN_SOURCE_PRECISION_N},且深核是选择性的 —— 不发布伪精确比例")
        out[str(source)] = entry
    return {"sources": out, "policy": _PRECISION_POLICY}


_PRECISION_POLICY = ("样本稀疏或选择性证伪时不发布 source precision;"
                     "计数照常给,比例留空并说明原因")


def render(review: dict) -> str:
    lines = [f"# intel claim 账本 —— {review['subject']}", "",
             f"> 🚨 {review['red_line']}", "",
             f"- claim **{review['n_claims']}** 条 · 稿件裁决 "
             f"**{review['draft_verdict']}** · 本票 **{review['ticket_verdict']}**",
             "",
             "| 状态 | 数 |", "|---|---:|"]
    lines += [f"| {k} | {v} |" for k, v in review["counts"].items()]
    lines += ["", "## 责任归属(仅对 REFUTED)", "", "| 归属 | 数 |", "|---|---:|"]
    lines += [f"| `{k}` | {v} |" for k, v in review["blame"].items()]
    budget = review["budget"]
    lines += ["", "## 预算(判据只看真实 tool telemetry)", "",
              f"- basis `{budget['basis']}` · 超预算 "
              f"**{'是' if budget['over_budget'] else '否'}**",
              f"- {budget['note']}", ""]
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="情报治理 v2:claim ledger(§1.3 D3)")
    sub = ap.add_subparsers(dest="cmd", required=True)
    lint = sub.add_parser("lint", help="对一份 intel 稿做分层 lint")
    lint.add_argument("draft", help="_l4_intel_<code>.md 路径")
    lint.add_argument("--subject", default="")
    lint.add_argument("--catalog-root", default=None)
    lint.add_argument("--out-dir", default=None)

    a = ap.parse_args(argv)
    path = Path(a.draft)
    subject = a.subject or path.stem.replace("_l4_intel_", "")
    from autoresearch.news.catalog import NewsCatalog

    review = review_draft(path.read_text(encoding="utf-8"), subject=subject,
                          catalog=NewsCatalog(a.catalog_root))
    if a.out_dir:
        write_ledger(a.out_dir, [Claim(**{k: v for k, v in c.items() if k != "lint"})
                                 for c in review["claims"]])
    print(render(review))
    # 只拒稿不拒票 → 退出码恒 0(拒稿由 draft_verdict 字段承载)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
