#!/usr/bin/env python3
"""Google / Bing News RSS —— **只作发现源(T4)**,永远不能单独支持 material claim。

design: `docs/specs/2026-08-28-external-evidence-expansion-design.md` §3.1(来源分级 T1–T4)、
§9(数据源规格)、§12 Q6(「只作发现源;跟不到 canonical T1/T2 原文的条目保持 UNVERIFIED,
不靠回退搜索摘要升格」)。

╔════════════════════════════════════════════════════════════════════════════════════════╗
║ 铁律(§3.1 逐字,写进代码而不只是文档)                                                 ║
║                                                                                        ║
║ 1. 聚合标题 / snippet **不可单独支持 material claim**。必须跟到 canonical 原文。        ║
║    → 本模块返回的每一项恒带 `evidence_tier="T4_discovery"` 与                           ║
║      `verification_status="UNVERIFIED"`,且**没有参数能改它们**。想要 VERIFIED,        ║
║      只能由 `claim_ledger` 在抓到 canonical 页 / 官方结构化 blob 之后另行判定。         ║
║    → 「搜索结果 snippet 只能证明『搜到过』,不能令 content_supports=true」。            ║
║ 2. URL 入账前统一 canonicalize:去追踪参数 / fragment、跟随允许域重定向、               ║
║    仅接受公网 http(s),**拒绝 localhost / 私网 / `file:`**。                            ║
║ 3. 受版权约束:只存 URL / 元数据 / 短摘要(≤200 字)/ 内容 hash,**不存正文**。          ║
║    本模块不抓正文、不提供抓正文的入口。                                                ║
║ 4. 7 天 soak 未过前,RSS 只能是发现备源,不得称「确定性主源」(§9)。                    ║
╚════════════════════════════════════════════════════════════════════════════════════════╝

`canonicalize_url` 的实现在 `official_event_calendar`(事件 `source_url`、RSS 项 URL、映射表
`evidence_url` 三处共用同一把尺;散成三份 = 修一处永远漏两处),此处 re-export。
"""
from __future__ import annotations

import hashlib
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from urllib.parse import quote_plus

from autoresearch.data.sources.official_event_calendar import (
    UnsafeURLError,
    canonicalize_url,
    iso_z,
)

UTC = timezone.utc

# 恒定标记 —— **没有参数能改**(见文件头铁律 1)。
EVIDENCE_TIER = "T4_discovery"
VERIFICATION_STATUS = "UNVERIFIED"

SOURCE_GOOGLE = "gnews_rss"
SOURCE_BING = "bing_rss"

GOOGLE_RSS_BASE = "https://news.google.com/rss/search"
BING_RSS_BASE = "https://www.bing.com/news/search"

# 语言 → Google News 的 hl/gl/ceid 三件套。只开 zh / en 两档(§9 规格逐字)。
_LANG_PARAMS: dict[str, dict[str, str]] = {
    "zh": {"hl": "zh-CN", "gl": "CN", "ceid": "CN:zh-Hans"},
    "en": {"hl": "en-US", "gl": "US", "ceid": "US:en"},
}

DEFAULT_LIMIT = 100          # Google News RSS 每查询上限约 100 条(§9 探针实测 100)
SUMMARY_MAX_CHARS = 200      # 版权:短摘要,**不是正文**
REQUEST_TIMEOUT = 20
USER_AGENT = "autoresearch/0.2.5 (+news discovery; contact via repo owner)"


class NewsRssError(RuntimeError):
    """RSS 请求 / 解析失败(**不是**「这个查询没有新闻」)。"""


def google_news_rss_url(query: str, lang: str = "zh") -> str:
    """Google News RSS 查询 URL。`lang` 只接受 `zh` / `en`。"""
    p = _LANG_PARAMS.get(str(lang).lower())
    if p is None:
        raise ValueError(f"lang 只支持 {sorted(_LANG_PARAMS)},收到 {lang!r}")
    q = quote_plus(str(query).strip())
    return f"{GOOGLE_RSS_BASE}?q={q}&hl={p['hl']}&gl={p['gl']}&ceid={p['ceid']}"


def bing_news_rss_url(query: str, lang: str = "zh") -> str:
    """Bing News RSS 查询 URL(备源)。Bing 用 `setlang`/`cc` 而非 ceid。"""
    q = quote_plus(str(query).strip())
    lang = str(lang).lower()
    cc = "CN" if lang == "zh" else "US"
    setlang = "zh-hans" if lang == "zh" else "en"
    return f"{BING_RSS_BASE}?q={q}&format=rss&cc={cc}&setlang={setlang}"


def _default_fetch(url: str) -> str:
    import requests

    r = requests.get(url, timeout=REQUEST_TIMEOUT, headers={"User-Agent": USER_AGENT})
    r.raise_for_status()
    return r.text


def _text(node, tag: str) -> str:
    el = node.find(tag)
    return (el.text or "").strip() if el is not None and el.text else ""


def _short(s: str, limit: int = SUMMARY_MAX_CHARS) -> str:
    """短摘要:去标签、压空白、截断。**绝不**存正文(§3.1 版权约束)。"""
    import re

    flat = re.sub(r"<[^>]+>", " ", str(s or ""))
    flat = re.sub(r"\s+", " ", flat).strip()
    return flat[:limit]


def _published(raw: str) -> str | None:
    """RFC-2822 pubDate → ISO8601 Z。无时区 / 不可解析 → None(不猜时区)。

    naive 串按 UTC 认领会在跨日边界系统性错一天,而这个字段正是新闻时效窗(§6.4)的输入。
    宁可 None(消费侧当「时间未知」),也不要一个看起来很确定的错时刻。
    """
    s = str(raw or "").strip()
    if not s:
        return None
    try:
        dt = parsedate_to_datetime(s)
    except (TypeError, ValueError):
        return None
    if dt is None or dt.tzinfo is None:
        return None
    return iso_z(dt.astimezone(UTC))


def parse_rss(xml_text: str, *, discovered_via: str) -> list[dict]:
    """RSS 2.0 → 发现项列表。**纯函数,零网络**(单测直接喂 xml 串)。

    每项恒带 `evidence_tier=T4_discovery` / `verification_status=UNVERIFIED`。
    `url` 是 canonicalize 后的;canonicalize 失败(私网 / `file:` / 非 http)→ **整项丢弃**
    并计入 `rejected`(由 `search` 汇总记账),不是静默放行。
    """
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError as exc:
        raise NewsRssError(f"RSS 解析失败({discovered_via}):{exc}") from exc

    items = root.findall(".//item")
    out: list[dict] = []
    for it in items:
        link = _text(it, "link")
        if not link:
            continue
        try:
            url = canonicalize_url(link)
        except UnsafeURLError:
            continue                                   # 不可入账的 URL:整项丢弃
        title = _text(it, "title")
        src_el = it.find("source")
        source_name = ""
        if src_el is not None and src_el.text:
            source_name = src_el.text.strip()
        summary = _short(_text(it, "description"))
        digest = hashlib.sha256(
            "\n".join([title, url, _text(it, "pubDate"), summary]).encode("utf-8")
        ).hexdigest()
        out.append({
            "title": title,
            "url": url,
            "published_ts": _published(_text(it, "pubDate")),
            "published_raw": _text(it, "pubDate"),
            "source_name": source_name,
            "discovered_via": discovered_via,
            "summary": summary,               # ≤200 字短摘要;**不是正文**
            "content_hash": digest,
            "evidence_tier": EVIDENCE_TIER,           # ← 恒定
            "verification_status": VERIFICATION_STATUS,  # ← 恒定
        })
    return out


def search(
    query: str,
    lang: str = "zh",
    limit: int = DEFAULT_LIMIT,
    *,
    provider: str = "google",
    fetch=None,
    resolve=None,
    allowed_hosts=None,
) -> list[dict]:
    """新闻**发现**(不是证据):→ `[{title, url, published_ts, source_name, discovered_via, ...}]`。

    `provider`:`"google"`(默认)| `"bing"`(备源)| `"both"`(两源合并,按 canonical URL 去重,
    Google 优先)。§12 Q6:主 / 备顺序要等 7 天 soak 才裁,当前默认 google。

    `fetch`:可注入的 `fetch(url) -> str`,单测用(**测试禁止真网络**)。
    `resolve` / `allowed_hosts`:透传给 `canonicalize_url` 的重定向解析(聚合链接 → 原文);
    默认 None = 不联网,`url` 保持聚合域,`verification_status` 照旧 `UNVERIFIED` ——
    **跟不到 canonical 原文就永远不升格**(§12 Q6 逐字)。

    任一源炸了 → B 级降级记账,返回**另一源的结果**(both 时)或空列表,不抛。
    """
    from autoresearch.data.contracts import record_degradation

    f = fetch or _default_fetch
    plan: list[tuple[str, str]] = []
    p = str(provider).lower()
    if p in ("google", "both"):
        plan.append((SOURCE_GOOGLE, google_news_rss_url(query, lang)))
    if p in ("bing", "both"):
        plan.append((SOURCE_BING, bing_news_rss_url(query, lang)))
    if not plan:
        raise ValueError(f"provider 只支持 google / bing / both,收到 {provider!r}")

    rows: list[dict] = []
    for via, url in plan:
        try:
            rows.extend(parse_rss(f(url), discovered_via=via))
        except Exception as exc:                                   # noqa: BLE001
            record_degradation(via, f"发现源取数/解析失败:{type(exc).__name__}: {exc}",
                               key=str(query)[:60])

    # canonical URL 去重(重复调用仍计预算 —— 那是 web_budget 的事,不是这里的)。
    seen: set[str] = set()
    deduped: list[dict] = []
    for r in rows:
        if r["url"] in seen:
            continue
        seen.add(r["url"])
        if resolve is not None or allowed_hosts is not None:
            try:
                r = dict(r, url=canonicalize_url(r["url"], resolve=resolve,
                                                 allowed_hosts=allowed_hosts))
            except UnsafeURLError:
                continue
        deduped.append(r)
    return deduped[: int(limit)] if limit else deduped


def to_catalog_rows(items: list[dict], *, first_seen_ts: datetime) -> list[dict]:
    """发现项 → `news_catalog` 行的最小投影(§6.3:新闻 / 事件走 catalog,数值走 lake)。

    `first_seen_ts` 由调用方显式给(**不是** now()):PIT 的 `first_seen_ts <= decision_cutoff`
    是这批行唯一的准入判据,靠 now() 猜等于没有 PIT。
    """
    seen = iso_z(first_seen_ts)
    return [
        {
            "url": r["url"],
            "title": r["title"],
            "source": r["discovered_via"],
            "source_name": r["source_name"],
            "published_ts": r["published_ts"],
            "first_seen_ts": seen,
            "content_hash": r["content_hash"],
            "evidence_tier": r["evidence_tier"],
            "verification_status": r["verification_status"],
        }
        for r in items
    ]


__all__ = [
    "EVIDENCE_TIER", "VERIFICATION_STATUS", "SOURCE_GOOGLE", "SOURCE_BING",
    "GOOGLE_RSS_BASE", "BING_RSS_BASE", "SUMMARY_MAX_CHARS", "NewsRssError",
    "UnsafeURLError", "canonicalize_url",
    "google_news_rss_url", "bing_news_rss_url", "parse_rss", "search", "to_catalog_rows",
]
