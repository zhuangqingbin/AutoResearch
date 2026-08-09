#!/usr/bin/env python3
"""统一新闻观测目录 news_catalog —— 回答「某阶段截止时系统实际看到了什么」(确定性,零 LLM)。

design: docs/specs/2026-08-03-scan-next-wave-brainstorm-design.md §1.1 D1

## 核心诊断(修正版)

问题**不是**「全仓无新闻存储」—— `stock_news_em` 已是 as-of lake(工作区约 1,891 个
parquet),公告另有 `anns_d` lake 与 cninfo fallback cache。问题是:

1. 三者之间**没有统一观测清单**,无法回答「某阶段截止时系统实际看到了什么」;
2. 选择性逐票采集**不能充当市场新闻量**(L2∪dossier∪pinned 是选出来的,不是市场);
3. claim→source 缺可核验链路;
4. 实时搜索缺 telemetry,复用与成本无法计量。

所以本模块**不重造平行湖**:原始内容仍留各自湖,目录只保存**身份、版本与可用性**。

## 三张表

    canonical_event      canonical_event_id, event_ts?, title_norm, codes[]
    source_observation   source_observation_id, canonical_event_id, source, url,
                         published_ts?, first_seen_ts, fetched_ts, content_hash,
                         revision, first_seen_basis, scan_run_id?, available_stage,
                         raw_artifact_path
    code_link            source_observation_id, code, method, confidence, rule_version

**身份纪律**:`url|title|date` 的 hash 只能当 observation 候选键,**不能同时承担事件身份**。
转载(同标题不同 url)→ 两个 observation、一个 canonical_event;更正(同 url 内容变了)→
同一 lineage 的**新 revision**,旧版本保留,不覆盖。

**PIT 纪律**:回放必须传 `decision_cutoff_ts + stage`,只允许
`first_seen_ts ≤ cutoff` ∧ `available_stage ≤ stage` 的观测。
`published_ts` 只是**来源陈述**,不能替代 first-seen —— 一条 08-01 发布、08-03 才被我们
抓到的新闻,在 08-02 的决策里不存在。D4 在 finalist 之后抓到的全文标 `L4`,
因此**不会**进入同日 L3 回放。

**既有资产优先**:历史分片没有真实抓取时间 → `first_seen_basis="snapshot_inferred"`,
用持久化快照时间,且**不得回放到该快照之前**;新抓取一律 `observed`。

**选择偏差**:逐票补充(L2∪dossier∪pinned)只能做**个股证据**,不能算市场「新闻热度」。
`market_heat_eligible()` 把这条写成一次会拒绝的检查。

  uv run --no-sync python -m autoresearch.news.catalog inventory
  uv run --no-sync python -m autoresearch.news.catalog manifest 2026-08-01
  uv run --no-sync python -m autoresearch.news.catalog replay 000651 --cutoff ... --stage L3
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

SCHEMA_VERSION = 1
DEFAULT_ROOT = Path("context/news_catalog")
RULE_VERSION = "catalog.v1"

# ── 阶段序:回放只允许 available_stage ≤ 请求 stage ────────────────────
# D4 的全文在 finalist 之后才抓到(L4),所以它进不了同日的 L3 回放 —— 这一条是 §1.1
# 「两个 cutoff 不互相污染」的全部实现。
STAGES = ("L0", "L1", "L2", "L3", "L4", "L5")
_STAGE_ORDER = {s: i for i, s in enumerate(STAGES)}

# first_seen 的两种来源。**不可混用**:snapshot_inferred 是我们对历史分片的推断,
# 它的时间是「这份快照落盘的时间」,不是「我们第一次看见这条新闻的时间」。
BASIS_OBSERVED = "observed"
BASIS_SNAPSHOT = "snapshot_inferred"
FIRST_SEEN_BASES = (BASIS_OBSERVED, BASIS_SNAPSHOT)

# 采集范围 —— 决定这条观测**能不能**用来算市场热度
SCOPE_MARKET_WIDE = "market_wide"      # 全市场公告 / 固定口径全局快讯
SCOPE_SELECTIVE = "selective"          # L2 ∪ dossier ∪ pinned 逐票补充(**有选择偏差**)
SCOPES = (SCOPE_MARKET_WIDE, SCOPE_SELECTIVE)

# code_link 的关联方式
METHOD_QUERY_CODE = "query_code"       # 逐票查回来的,代码是查询参数(置信最高)
METHOD_SOURCE_FIELD = "source_field"   # 源自带 ts_code/代码列
METHOD_TITLE_MATCH = "title_match"     # 标题里认出来的(最弱)
METHODS = (METHOD_QUERY_CODE, METHOD_SOURCE_FIELD, METHOD_TITLE_MATCH)
_METHOD_CONFIDENCE = {METHOD_QUERY_CODE: 1.0, METHOD_SOURCE_FIELD: 0.9,
                      METHOD_TITLE_MATCH: 0.5}


class CatalogError(ValueError):
    """目录契约违约 —— 写入时就该失败。"""


# ───────────────────────── 规范化 / 身份 ─────────────────────────

_PUNCT = re.compile(r"[\s　·、,。;:!?()()\[\]【】\"'“”‘’《》<>—\-_/\\|]+")


def normalize_title(title: str) -> str:
    """标题规范化 —— 只去空白/标点与常见前缀,**不做同义改写**。

    过度归一会把两件不同的事合并成一个 canonical_event(比如「关于回购的公告」与
    「关于回购的进展公告」);宁可多一个事件,也不要把两件事焊死。
    """
    text = str(title or "").strip()
    text = re.sub(r"^(关于|公司)?", "", text)
    text = _PUNCT.sub("", text)
    return text


def canonical_event_id(title: str, event_date: str | None) -> str:
    """事件身份 = 规范标题 + 事件日。**与 observation 的候选键分开推导**(§1.1 身份纪律)。"""
    blob = f"{normalize_title(title)}|{str(event_date or '')[:10]}"
    return "ce_" + hashlib.sha256(blob.encode("utf-8")).hexdigest()[:16]


def lineage_id(source: str, url: str, title: str) -> str:
    """观测**血统**键:同一 (source, url) 的历次抓取属于同一条血统。

    url 缺失时退回标题 —— 快讯源常常没有稳定 url,退回后同源同标题算一条血统,
    内容变了就是新 revision(正是「更正」该有的形态)。
    """
    key = f"{source}|{url}" if str(url or "").strip() else f"{source}|title:{normalize_title(title)}"
    return "ln_" + hashlib.sha256(key.encode("utf-8")).hexdigest()[:16]


def content_hash(*parts: object) -> str:
    blob = "|".join(str(p or "") for p in parts)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:32]


def _iso(value: object) -> str | None:
    """任意时间输入 → ISO8601(带时区)。解析不了 → None(**不猜**)。"""
    if value in (None, "", "nan"):
        return None
    if isinstance(value, datetime):
        dt = value
    else:
        text = str(value).strip().replace("/", "-")
        for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M",
                    "%Y-%m-%d", "%Y%m%d"):
            try:
                dt = datetime.strptime(text[:len(fmt) + 4], fmt)
                break
            except ValueError:
                continue
        else:
            try:
                dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
            except ValueError:
                return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.isoformat()


def _parse(value: object) -> datetime | None:
    text = _iso(value)
    return datetime.fromisoformat(text) if text else None


# ───────────────────────── 观测记录 ─────────────────────────


@dataclass
class Observation:
    """一次来源观测。`first_seen_ts` 是本表的灵魂 —— 缺它这行就不能进回放。"""
    source: str
    title: str
    url: str = ""
    published_ts: str | None = None
    first_seen_ts: str | None = None
    fetched_ts: str | None = None
    first_seen_basis: str = BASIS_OBSERVED
    available_stage: str = "L1"
    scope: str = SCOPE_MARKET_WIDE
    scan_run_id: str | None = None
    raw_artifact_path: str = ""
    event_date: str | None = None
    codes: tuple = ()
    code_method: str = METHOD_SOURCE_FIELD
    body_hash: str = ""

    def validate(self) -> list[str]:
        bad = []
        if not str(self.source or "").strip():
            bad.append("source 为空")
        if not str(self.title or "").strip():
            bad.append("title 为空")
        if self.first_seen_basis not in FIRST_SEEN_BASES:
            bad.append(f"first_seen_basis={self.first_seen_basis!r} 不在 {list(FIRST_SEEN_BASES)}")
        if self.available_stage not in _STAGE_ORDER:
            bad.append(f"available_stage={self.available_stage!r} 不在 {list(STAGES)}")
        if self.scope not in SCOPES:
            bad.append(f"scope={self.scope!r} 不在 {list(SCOPES)}")
        if self.code_method not in METHODS:
            bad.append(f"code_method={self.code_method!r} 不在 {list(METHODS)}")
        if _iso(self.first_seen_ts) is None:
            bad.append("first_seen_ts 缺失或不可解析 —— 缺它这行不能进回放(§1.1 探针:缺失率必须=0)")
        return bad


_OBS_COLUMNS = ["source_observation_id", "lineage_id", "canonical_event_id", "source",
                "url", "title", "title_norm", "published_ts", "first_seen_ts",
                "fetched_ts", "content_hash", "revision", "first_seen_basis",
                "available_stage", "scope", "scan_run_id", "raw_artifact_path",
                "event_date", "rule_version"]
_EVENT_COLUMNS = ["canonical_event_id", "event_ts", "title_norm", "codes", "rule_version"]
_LINK_COLUMNS = ["source_observation_id", "code", "method", "confidence", "rule_version"]


# ───────────────────────── 目录本体 ─────────────────────────


class NewsCatalog:
    """只增目录。三张表各一个 CSV —— 小而可审计,不与原始湖竞争存储。"""

    def __init__(self, root: Path | str | None = None):
        self.root = Path(root or DEFAULT_ROOT)

    # ── 读 ──
    def _path(self, name: str) -> Path:
        return self.root / f"{name}.csv"

    def _read(self, name: str, columns: list[str]) -> pd.DataFrame:
        path = self._path(name)
        if not path.exists():
            return pd.DataFrame(columns=columns)
        try:
            frame = pd.read_csv(path, dtype=str, keep_default_na=False)
        except Exception:  # noqa: BLE001
            return pd.DataFrame(columns=columns)
        for col in columns:
            if col not in frame.columns:
                frame[col] = ""
        return frame[columns]

    def observations(self) -> pd.DataFrame:
        return self._read("source_observation", _OBS_COLUMNS)

    def events(self) -> pd.DataFrame:
        return self._read("canonical_event", _EVENT_COLUMNS)

    def links(self) -> pd.DataFrame:
        return self._read("code_link", _LINK_COLUMNS)

    # ── 写(只增)──
    def ingest(self, items: list[Observation]) -> dict:
        """只增写入。返回 `{added, revised, unchanged, rejected}`。

        - 新血统 → revision 0;
        - 同血统但 `content_hash` 变了 → revision+1 **新增一行**(旧版本保留,不覆盖);
        - 同血统同 hash → `unchanged`(幂等,重跑不产生新行);
        - 校验不过 → `rejected`(带原因),**不写入**。
        """
        obs = self.observations()
        events = self.events()
        links = self.links()
        by_lineage: dict[str, int] = {}
        hashes: dict[str, set[str]] = {}
        for row in obs.itertuples(index=False):
            by_lineage[row.lineage_id] = max(by_lineage.get(row.lineage_id, -1),
                                             int(row.revision or 0))
            hashes.setdefault(row.lineage_id, set()).add(row.content_hash)

        added, revised, unchanged = [], [], 0
        rejected: list[dict] = []
        new_obs, new_events, new_links = [], [], []
        for item in items:
            problems = item.validate()
            if problems:
                rejected.append({"title": item.title, "problems": problems})
                continue
            lid = lineage_id(item.source, item.url, item.title)
            chash = content_hash(item.title, item.body_hash, item.published_ts)
            if chash in hashes.get(lid, set()):
                unchanged += 1
                continue
            revision = by_lineage.get(lid, -1) + 1
            oid = f"{lid}#r{revision}"
            cid = canonical_event_id(item.title, item.event_date or
                                     (item.published_ts or item.first_seen_ts))
            new_obs.append({
                "source_observation_id": oid, "lineage_id": lid,
                "canonical_event_id": cid, "source": item.source, "url": item.url,
                "title": item.title, "title_norm": normalize_title(item.title),
                "published_ts": _iso(item.published_ts) or "",
                "first_seen_ts": _iso(item.first_seen_ts) or "",
                "fetched_ts": _iso(item.fetched_ts) or _iso(item.first_seen_ts) or "",
                "content_hash": chash, "revision": str(revision),
                "first_seen_basis": item.first_seen_basis,
                "available_stage": item.available_stage, "scope": item.scope,
                "scan_run_id": item.scan_run_id or "",
                "raw_artifact_path": item.raw_artifact_path,
                "event_date": str(item.event_date or "")[:10],
                "rule_version": RULE_VERSION,
            })
            new_events.append({
                "canonical_event_id": cid,
                "event_ts": _iso(item.event_date or item.published_ts) or "",
                "title_norm": normalize_title(item.title),
                "codes": "|".join(sorted(str(c).zfill(6) for c in item.codes)),
                "rule_version": RULE_VERSION,
            })
            for code in item.codes:
                new_links.append({
                    "source_observation_id": oid, "code": str(code).zfill(6),
                    "method": item.code_method,
                    "confidence": str(_METHOD_CONFIDENCE[item.code_method]),
                    "rule_version": RULE_VERSION,
                })
            (revised if revision else added).append(oid)
            by_lineage[lid] = revision
            hashes.setdefault(lid, set()).add(chash)

        if new_obs:
            obs = pd.concat([obs, pd.DataFrame(new_obs)], ignore_index=True)
            # canonical_event 去重合并:同一事件的多条转载共享一行,codes 取并集
            events = pd.concat([events, pd.DataFrame(new_events)], ignore_index=True)
            events = (events.groupby("canonical_event_id", as_index=False)
                      .agg({"event_ts": "first", "title_norm": "first",
                            "codes": lambda s: "|".join(sorted(
                                {c for v in s for c in str(v).split("|") if c})),
                            "rule_version": "first"}))
            if new_links:
                links = pd.concat([links, pd.DataFrame(new_links)], ignore_index=True)
                links = links.drop_duplicates(
                    subset=["source_observation_id", "code"], keep="first")
            self._write(obs, events, links)
        return {"added": added, "revised": revised, "unchanged": unchanged,
                "rejected": rejected}

    def _write(self, obs, events, links) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        for name, frame, columns in (("source_observation", obs, _OBS_COLUMNS),
                                     ("canonical_event", events, _EVENT_COLUMNS),
                                     ("code_link", links, _LINK_COLUMNS)):
            path = self._path(name)
            tmp = path.with_name(path.name + ".tmp")
            frame.reindex(columns=columns).to_csv(tmp, index=False)
            tmp.replace(path)

    # ── PIT 回放 ──
    def replay(self, code: str | None, decision_cutoff_ts: str,
               stage: str) -> pd.DataFrame:
        """PIT 回放:`first_seen_ts ≤ cutoff` ∧ `available_stage ≤ stage`。

        `published_ts` **不参与过滤** —— 它是来源陈述。一条 08-01 发布、08-03 才被我们
        抓到的新闻,在 08-02 的决策里不存在,不管它自称什么时候发的。

        `snapshot_inferred` 的观测**不得回放到该快照之前**:它的 first_seen 已经是快照
        时间,过滤条件天然满足这一条,这里再单列一个标记让读的人知道这行的时间是推断的。
        """
        if stage not in _STAGE_ORDER:
            raise CatalogError(f"未知 stage {stage!r};合法:{list(STAGES)}")
        cutoff = _parse(decision_cutoff_ts)
        if cutoff is None:
            raise CatalogError(f"decision_cutoff_ts 不可解析:{decision_cutoff_ts!r}")
        obs = self.observations()
        if not len(obs):
            return obs
        seen = obs["first_seen_ts"].map(_parse)
        visible = seen.notna() & seen.map(lambda t: t is not None and t <= cutoff)
        stage_ok = obs["available_stage"].map(
            lambda s: _STAGE_ORDER.get(str(s), 99) <= _STAGE_ORDER[stage])
        out = obs[visible & stage_ok].copy()
        if code is not None:
            wanted = str(code).zfill(6)
            links = self.links()
            ids = set(links.loc[links["code"] == wanted, "source_observation_id"])
            out = out[out["source_observation_id"].isin(ids)]
        return out.sort_values(["first_seen_ts", "source_observation_id"]).reset_index(
            drop=True)

    # ── 选择偏差守卫 ──
    def market_heat_eligible(self) -> pd.DataFrame:
        """能用来算**市场热度**的观测 = 只有 `market_wide` 范围的固定 feed。

        §1.1 原话:「后者有选择偏差,只能做个股证据,不能计算市场『新闻热度』」。
        §4.5 温度 v2 也重复了一次。写成函数,免得下次有人直接 `len(observations())`。
        """
        obs = self.observations()
        if not len(obs):
            return obs
        return obs[obs["scope"] == SCOPE_MARKET_WIDE].reset_index(drop=True)

    def assert_not_selective(self, frame: pd.DataFrame, *, context: str) -> None:
        selective = frame[frame["scope"] == SCOPE_SELECTIVE] if len(frame) else frame
        if len(selective):
            raise CatalogError(
                f"{context} 用到了 {len(selective)} 条 selective 观测 —— "
                "逐票补充(L2∪dossier∪pinned)有选择偏差,只能做个股证据,"
                "不能计入市场新闻量/热度(§1.1 / §4.5)")

    # ── 健康 ──
    def health(self) -> dict:
        obs = self.observations()
        n = int(len(obs))
        if not n:
            return {"n_observations": 0, "first_seen_missing_rate": 0.0,
                    "n_events": 0, "n_links": 0, "by_source": {}, "by_scope": {},
                    "by_basis": {}, "revisions": 0}
        missing = int((obs["first_seen_ts"].astype(str).str.strip() == "").sum())
        return {
            "n_observations": n,
            # §1.1 验收探针:必须恒为 0
            "first_seen_missing_rate": round(missing / n, 6),
            "n_events": int(self.events()["canonical_event_id"].nunique()),
            "n_links": int(len(self.links())),
            "by_source": {str(k): int(v) for k, v in obs["source"].value_counts().items()},
            "by_scope": {str(k): int(v) for k, v in obs["scope"].value_counts().items()},
            "by_basis": {str(k): int(v)
                         for k, v in obs["first_seen_basis"].value_counts().items()},
            "revisions": int((pd.to_numeric(obs["revision"], errors="coerce")
                              .fillna(0) > 0).sum()),
        }


# ───────────────────────── 源契约(§1.1「函数存在 ≠ 数据可用」)─────────────────────────


@dataclass
class SourceContract:
    """一个来源的可用性契约。**HTTP 200 不算可用**,关键列非空 + 日期单调才算。"""
    source: str
    required_columns: tuple = ()
    date_column: str = ""
    min_nonnull_rate: float = 0.9
    rate_limit: str = "unknown"
    terms: str = "unknown"
    max_staleness_days: int | None = None
    schema_hash: str = ""

    def as_dict(self) -> dict:
        return asdict(self)


def check_source(contract: SourceContract, frame: pd.DataFrame | None,
                 *, as_of: str | None = None) -> dict:
    """逐源体检 → `{status, problems, nonnull_rate, freshness_days, schema_hash}`。

    `status`:`OK` | `DEGRADED`(B 级,记账不阻断)| `EMPTY`。
    **不抛异常** —— 新闻是 B 级增强面,缺了漏斗仍成立(数据契约裁定)。
    """
    problems: list[str] = []
    if frame is None or not len(frame):
        return {"source": contract.source, "status": "EMPTY", "n_rows": 0,
                "problems": ["空帧(0 行)"], "nonnull_rate": None,
                "freshness_days": None, "schema_hash": "",
                "rate_limit": contract.rate_limit, "terms": contract.terms}

    missing = [c for c in contract.required_columns if c not in frame.columns]
    if missing:
        problems.append(f"缺列 {missing}")
    present = [c for c in contract.required_columns if c in frame.columns]
    rates = {c: float(frame[c].notna().mean()) for c in present}
    worst = min(rates.values()) if rates else None
    if worst is not None and worst < contract.min_nonnull_rate:
        low = sorted(k for k, v in rates.items() if v < contract.min_nonnull_rate)
        problems.append(f"关键列非空率不足 {low}(最低 {worst:.3f} < "
                        f"{contract.min_nonnull_rate})")

    freshness = None
    if contract.date_column and contract.date_column in frame.columns:
        parsed = frame[contract.date_column].map(_parse)
        valid = [p for p in parsed if p is not None]
        if not valid:
            problems.append(f"日期列 {contract.date_column!r} 全不可解析")
        else:
            newest = max(valid)
            ref = _parse(as_of) or datetime.now(timezone.utc)
            freshness = (ref - newest).days
            if (contract.max_staleness_days is not None
                    and freshness > contract.max_staleness_days):
                problems.append(f"陈旧 {freshness} 天 > {contract.max_staleness_days}")
            # 日期单调性:降序/升序都行,乱序说明分页拼接出了问题
            order = [p for p in parsed if p is not None]
            if len(order) > 2 and order != sorted(order) and order != sorted(order, reverse=True):
                problems.append("日期非单调 —— 多半是分页拼接乱序")

    schema_hash = content_hash("|".join(sorted(map(str, frame.columns))))
    return {"source": contract.source,
            "status": "DEGRADED" if problems else "OK",
            "n_rows": int(len(frame)), "problems": problems,
            "nonnull_rate": None if worst is None else round(worst, 6),
            "freshness_days": freshness, "schema_hash": schema_hash,
            "schema_drift": bool(contract.schema_hash and
                                 contract.schema_hash != schema_hash),
            "rate_limit": contract.rate_limit, "terms": contract.terms}


# ───────────────────────── 既有资产盘点(最小证伪步)─────────────────────────

_KNOWN_LAKE_SOURCES = ("stock_news_em", "anns_d")

#: 已退役的源 —— 0 分片是**预期**,不是"数据没了"。`anns_d` 自 2026-07-18 起 tushare
#: 无权限(见 `data/contracts.py` 与 `scan/health.py:803`),公告面改走 cninfo 兜底。
#: 不标出来的话,盘点里那个 0 会被读成"该有却没有",下一个人又要重查一遍。
_RETIRED_LAKE_SOURCES = {"anns_d": "2026-07-18 起 tushare 无权限,已退役;公告面走 anns_fallback(cninfo)"}

#: 湖分片名形如 `<code>@<YYYYMMDD>`。**排序键是字符串**,所以 `first_key/last_key`
#: 是**代码序**的头尾,不是日期跨度 —— 首跑实测:first=`000012@20260625`、
#: last=`920982@20260624`,后者日期反而更早。日期跨度必须另算(见 `_shard_date_span`)。
_SHARD_DATE_RE = re.compile(r"@(\d{8})$")


def _shard_date_span(keys: list[str]) -> tuple[str | None, str | None]:
    """从分片名尾部的 `@YYYYMMDD` 解析真实日期跨度;解析不出 → `(None, None)`(**不猜**)。"""
    dates = sorted(m.group(1) for m in (_SHARD_DATE_RE.search(k) for k in keys) if m)
    return (dates[0], dates[-1]) if dates else (None, None)


def inventory(lake_root: Path | str | None = None,
              fallback_cache: Path | str | None = None) -> dict:
    """既有新闻/公告资产盘点 —— **先数清楚再决定要不要迁**(§1.1「既有资产优先」)。

    只读文件系统元数据(分片数、字节数、日期跨度),**不解析内容**:盘点本身不该是
    一次全量重算。字节数以实测为准 —— 设计稿明令删掉「容量忽略不计」的说法。

    **2026-08-09 首跑修出的两处真问题**(Wave12-T35,「建好的仓库没通电」第一次通电):

    1. `first_key/last_key` 是**代码序**头尾,却被 docstring 说成"日期跨度" —— 实测
       first=`000012@20260625`、last=`920982@20260624`,末尾那份日期反而更早。
       现在另出 `date_min/date_max`,并把两个 key 改名成 `first_key_by_code` 之意保留原名
       但注明含义,免得下一个人再读错一次。
    2. `anns_fallback` 的 glob 写的是 `*.json`,而真实布局是 `<date>/<code>.v1.json`
       (见 `data/sources/anns_fallback._cache_path`)—— 顶层一个 json 都没有,
       **这条腿从写下那天起就恒为 0**,而 0 长得和"没缓存"一模一样。改 `rglob`。
    """
    from autoresearch.data.cache import LAKE

    root = Path(lake_root) if lake_root else LAKE
    out: dict = {"lake_root": str(root), "sources": {}, "total_bytes": 0,
                 "total_shards": 0}
    for source in _KNOWN_LAKE_SOURCES:
        folder = root / source
        shards = sorted(folder.glob("*.parquet")) if folder.exists() else []
        size = sum(p.stat().st_size for p in shards)
        keys = [p.stem for p in shards]
        date_min, date_max = _shard_date_span(keys)
        entry = {
            "path": str(folder), "n_shards": len(shards), "bytes": size,
            # ⚠️ 这两个是**代码序**头尾(分片名按字符串排),不是最早/最晚
            "first_key": keys[0] if keys else None,
            "last_key": keys[-1] if keys else None,
            "date_min": date_min, "date_max": date_max,
        }
        if source in _RETIRED_LAKE_SOURCES:
            entry["retired"] = _RETIRED_LAKE_SOURCES[source]
        out["sources"][source] = entry
        out["total_bytes"] += size
        out["total_shards"] += len(shards)

    cache = Path(fallback_cache) if fallback_cache else Path("context/cache/anns_fallback")
    # rglob:真实布局是 `<date>/<code>.v{N}.json`,顶层 glob 恒空(2026-08-09 首跑发现)
    files = sorted(cache.rglob("*.json")) if cache.exists() else []
    size = sum(p.stat().st_size for p in files)
    out["sources"]["anns_fallback"] = {
        "path": str(cache), "n_shards": len(files), "bytes": size,
        "first_key": files[0].stem if files else None,
        "last_key": files[-1].stem if files else None,
        "layout": "<date>/<code>.v<N>.json(嵌套一层日期目录)",
        "note": ("`fetch_anns` 的 `cache` 形参默认 **False**(Wave10 A9 裁定),"
                 "所以这个目录多半根本不存在 —— 0 是预期,不是丢数据"),
    }
    out["total_bytes"] += size
    out["total_shards"] += len(files)
    return out


# ───────────────────────── 夜间快讯 ingest(B 级:降级不阻断,但必须记账)─────────────────────────
#
# Wave12-T35「通电」第二步。这三条是**全市场固定口径 feed**(不是逐票查回来的),
# 所以 scope=market_wide —— 它们是目前唯一有资格进"市场新闻量/热度"的观测。
#
# **消费者仍然全关**:intel 先读目录、L3 第二源、typed-event 进 prompt 都是 B 类,
# 要走 experiment_registry。本步只让目录里**有数据**,不让任何决策面读它。

FLASH_SOURCES: dict[str, dict] = {
    # 东财全球财经快讯:标题/摘要/发布时间/链接(2026-08-09 实测 200 行)
    "global_em": {"endpoint": "stock_info_global_em", "title": "标题",
                  "body": "摘要", "ts": "发布时间", "url": "链接"},
    # 新浪财经全球直播:**只有 时间/内容 两列**,没有独立标题也没有 url
    # (2026-08-09 实测 20 行)—— 标题只能从内容里取,url 缺失走 lineage 的标题退路。
    "global_sina": {"endpoint": "stock_info_global_sina", "title": None,
                    "body": "内容", "ts": "时间", "url": None},
    # 东财财经早餐:同 EM 四列(2026-08-09 实测 400 行)
    "cjzc_em": {"endpoint": "stock_info_cjzc_em", "title": "标题",
                "body": "摘要", "ts": "发布时间", "url": "链接"},
}

_BRACKET_TITLE = re.compile(r"^\s*【([^】]{2,80})】")


def flash_title(text: str, fallback_len: int = 60) -> str:
    """快讯正文 → 标题。带 `【…】` 头的取括号内(中文快讯的事实标题就在那里),否则截前 N 字。

    **不做同义改写**(同 `normalize_title` 的纪律):宁可多一个事件,也不要把两件事焊死。
    """
    text = str(text or "").strip()
    m = _BRACKET_TITLE.match(text)
    return m.group(1).strip() if m else text[:fallback_len]


def flash_observations(source: str, frame: pd.DataFrame, *, now: str | None = None,
                       stage: str = "L1") -> list[Observation]:
    """一份快讯 DataFrame → `Observation` 列表(纯函数,不碰网络也不碰目录)。

    `first_seen_ts = now`(=本次 ingest 的时刻),**不是** `published_ts`:
    一条 08-01 发布、今晚才被我们抓到的快讯,在 08-02 的决策里不存在(§1.1 PIT 纪律)。
    """
    spec = FLASH_SOURCES.get(source)
    if spec is None:
        raise CatalogError(f"未知快讯源 {source!r};合法:{sorted(FLASH_SOURCES)}")
    seen = _iso(now) or datetime.now(timezone.utc).isoformat()
    items: list[Observation] = []
    if frame is None or not len(frame):
        return items
    for row in frame.to_dict("records"):
        body = str(row.get(spec["body"]) or "").strip()
        raw_title = str(row.get(spec["title"]) or "").strip() if spec["title"] else ""
        title = raw_title or flash_title(body)
        if not title:
            continue                       # 无标题无正文 = 没有可记的观测,跳过(不造空行)
        published = _iso(row.get(spec["ts"]))
        items.append(Observation(
            source=source, title=title,
            url=str(row.get(spec["url"]) or "") if spec["url"] else "",
            published_ts=published,
            first_seen_ts=seen, fetched_ts=seen,
            first_seen_basis=BASIS_OBSERVED,      # 新抓取一律 observed(§1.1)
            available_stage=stage,
            scope=SCOPE_MARKET_WIDE,              # 固定口径全局 feed → 可算市场热度
            raw_artifact_path=f"akshare:{spec['endpoint']}",
            event_date=(published or seen)[:10],
            codes=(), code_method=METHOD_SOURCE_FIELD,
            body_hash=content_hash(body)))
    return items


def _fetch_flash(endpoint: str):
    """akshare 端点调用(单独一层,好让测试 patch 掉——测试禁挂真网)。"""
    import akshare as ak
    return getattr(ak, endpoint)()


def ingest_flash(sources: list[str] | None = None, *, catalog: NewsCatalog | None = None,
                 fetch=None, now: str | None = None, stage: str = "L1") -> dict:
    """三源快讯 → 目录(**B 级**:任一源挂了只降级记账,不抛、不连坐其它源)。

    B 级的两条纪律都要:①不阻断(新闻是增强面,缺了漏斗仍成立);②**降级必须留痕**
    ——「降级不留痕」才是真病,所以每个源都有一行 `status`,不管成没成。
    """
    cat = catalog or NewsCatalog()
    fetcher = fetch or _fetch_flash
    picked = list(sources or FLASH_SOURCES)
    per_source: list[dict] = []
    all_items: list[Observation] = []
    for source in picked:
        spec = FLASH_SOURCES.get(source)
        if spec is None:
            per_source.append({"source": source, "status": "UNKNOWN_SOURCE", "rows": 0})
            continue
        try:
            frame = fetcher(spec["endpoint"])
        except Exception as e:  # noqa: BLE001 — B 级:一个源挂了不该毁掉整晚
            per_source.append({"source": source, "status": "FETCH_FAILED", "rows": 0,
                               "error": f"{type(e).__name__}: {e}"[:200]})
            continue
        try:
            items = flash_observations(source, frame, now=now, stage=stage)
        except Exception as e:  # noqa: BLE001 — 列名变了(schema drift)也只降级
            per_source.append({"source": source, "status": "SHAPE_ERROR", "rows": 0,
                               "error": f"{type(e).__name__}: {e}"[:200]})
            continue
        all_items.extend(items)
        per_source.append({"source": source,
                           "status": "OK" if items else "EMPTY",
                           "rows": int(len(frame)), "observations": len(items)})
    result = cat.ingest(all_items)
    return {
        "schema_version": SCHEMA_VERSION,
        "per_source": per_source,
        "added": len(result["added"]), "revised": len(result["revised"]),
        "unchanged": result["unchanged"], "rejected": result["rejected"],
        # 「至少有一个源真出了数」——全挂时这里是 False,报表据此报警而不是显示"一切正常"
        "any_ok": any(s["status"] == "OK" for s in per_source),
        "health": cat.health(),
    }


def manifest_day(scan_dir: Path | str, *, catalog: NewsCatalog | None = None,
                 stage: str = "L3") -> dict:
    """单日 manifest(最小证伪步)—— 把该日 `L3_news/*.json` 收进目录并逐源对账。

    这些分片没有真实抓取时间 → `first_seen_basis=snapshot_inferred`,时间取**文件 mtime**
    (持久化快照时间),并且**不得回放到该时间之前**。
    """
    day = Path(scan_dir)
    cat = catalog or NewsCatalog()
    folder = day / "L3_news"
    files = sorted(folder.glob("*.json")) if folder.exists() else []
    items: list[Observation] = []
    per_file: list[dict] = []
    for path in files:
        try:
            rows = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            per_file.append({"file": path.name, "rows": 0, "error": "unreadable"})
            continue
        rows = rows if isinstance(rows, list) else []
        snapshot_ts = datetime.fromtimestamp(path.stat().st_mtime,
                                             tz=timezone.utc).isoformat()
        code = path.stem.zfill(6)
        for row in rows:
            if not isinstance(row, dict):
                continue
            items.append(Observation(
                source=str(row.get("source") or "anns_snapshot"),
                title=str(row.get("title") or ""),
                url=str(row.get("url") or ""),
                published_ts=row.get("ann_date") or row.get("date"),
                # 历史分片没有真实抓取时间 → 用快照时间并标明这是推断
                first_seen_ts=snapshot_ts, fetched_ts=snapshot_ts,
                first_seen_basis=BASIS_SNAPSHOT,
                available_stage=stage,
                # 逐票采集 = 选择性,只能做个股证据
                scope=SCOPE_SELECTIVE,
                scan_run_id=day.name,
                raw_artifact_path=str(path),
                event_date=str(row.get("ann_date") or row.get("date") or "")[:10],
                codes=(code,), code_method=METHOD_QUERY_CODE))
        per_file.append({"file": path.name, "rows": len(rows)})

    result = cat.ingest(items)
    return {
        "schema_version": SCHEMA_VERSION,
        "scan_date": day.name,
        "n_files": len(files),
        "n_rows_in_shards": sum(f.get("rows", 0) for f in per_file),
        "ingested": len(result["added"]) + len(result["revised"]),
        "added": len(result["added"]), "revised": len(result["revised"]),
        "unchanged": result["unchanged"], "rejected": result["rejected"],
        "per_file": per_file,
        "reconciled": (sum(f.get("rows", 0) for f in per_file)
                       == len(result["added"]) + len(result["revised"])
                       + result["unchanged"] + len(result["rejected"])),
        "health": cat.health(),
    }


# ───────────────────────── 渲染 / CLI ─────────────────────────


def render_health(health: dict) -> str:
    lines = ["# news_catalog 健康", "",
             f"- 观测 **{health['n_observations']}** · 事件 {health['n_events']} · "
             f"关联 {health['n_links']} · 修订版本 {health['revisions']}",
             f"- **first_seen 缺失率 {health['first_seen_missing_rate']:.4f}**"
             + ("(✅ 契约要求恒为 0)" if health["first_seen_missing_rate"] == 0
                else " —— 🚨 违约:缺 first_seen 的行不能进回放"),
             ""]
    for label, key in (("来源", "by_source"), ("范围", "by_scope"), ("时间来源", "by_basis")):
        rows = health.get(key) or {}
        lines += [f"## {label}", "", "| 值 | 观测数 |", "|---|---:|"]
        lines += [f"| `{k}` | {v} |" for k, v in sorted(rows.items())] or ["| — | — |"]
        lines.append("")
    lines += ["> `selective` 范围的观测只能做**个股证据**,不能计入市场新闻量/热度。", ""]
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="统一新闻观测目录(§1.1 D1)")
    ap.add_argument("--root", default=None)
    sub = ap.add_subparsers(dest="cmd", required=True)

    sub.add_parser("inventory", help="既有新闻/公告资产盘点(只读元数据)")
    sub.add_parser("health", help="目录健康(first_seen 缺失率必须为 0)")

    f = sub.add_parser("ingest-flash", help="三源快讯 ingest(夜间;B 级,降级不阻断)")
    f.add_argument("--sources", default=None,
                   help=f"逗号分隔,缺省全部:{','.join(FLASH_SOURCES)}")
    f.add_argument("--stage", default="L1", choices=list(STAGES))

    m = sub.add_parser("manifest", help="把某扫描日的 L3_news 分片收进目录并对账")
    m.add_argument("date")
    m.add_argument("--scan-root", default="context/scan")
    m.add_argument("--stage", default="L3", choices=list(STAGES))

    r = sub.add_parser("replay", help="PIT 回放:某票在某 cutoff/stage 时可见的新闻面")
    r.add_argument("code", nargs="?", default=None)
    r.add_argument("--cutoff", required=True)
    r.add_argument("--stage", default="L3", choices=list(STAGES))

    a = ap.parse_args(argv)
    cat = NewsCatalog(a.root)
    if a.cmd == "inventory":
        print(json.dumps(inventory(), ensure_ascii=False, indent=2))
        return 0
    if a.cmd == "health":
        health = cat.health()
        print(render_health(health))
        return 0 if health["first_seen_missing_rate"] == 0 else 1
    if a.cmd == "ingest-flash":
        picked = [s.strip() for s in a.sources.split(",")] if a.sources else None
        res = ingest_flash(picked, catalog=cat, stage=a.stage)
        print(json.dumps(res, ensure_ascii=False, indent=2))
        # exit 恒 0:B 级不毙人。全挂的信号在 `any_ok=false` 与 prelude 报表行里,
        # 不靠退出码 —— 夜间批任何一步非零都会连累后面的步骤(nightly_close 的教训)。
        return 0
    if a.cmd == "manifest":
        result = manifest_day(Path(a.scan_root) / a.date, catalog=cat, stage=a.stage)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0 if result["reconciled"] else 1
    frame = cat.replay(a.code, a.cutoff, a.stage)
    print(f"[catalog] {a.code or '(全部)'} @ {a.cutoff} stage≤{a.stage}:{len(frame)} 条")
    for row in frame.itertuples(index=False):
        print(f"  {row.first_seen_ts} [{row.first_seen_basis}/{row.available_stage}] "
              f"{row.source}:{row.title[:40]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
