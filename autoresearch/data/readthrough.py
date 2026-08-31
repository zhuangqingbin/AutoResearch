#!/usr/bin/env python3
"""海外读透映射表(`readthrough_map.yaml`)的加载器 + 入库 lint。

╔══════════════════════════════════════════════════════════════════════════════════════════╗
║ 映射表示「值得观察的关系」,**不表示因果方向、涨跌传导方向或评级方向**;                   ║
║ 渲染时禁止写「A 涨所以 B 应涨」。                                                         ║
╚══════════════════════════════════════════════════════════════════════════════════════════╝

design: `docs/specs/2026-08-28-external-evidence-expansion-design.md` §8(映射表 v2 schema +
四条规则)、§3.1(来源分级 T1–T4 与 URL 入账纪律)。

## 两个函数,两种职责

- `load_map(as_of)` —— **消费入口**。只返回「有效期内 + 证据齐 + 枚举合法 + 可交易」的条目;
  个股 `codes` 优先于 `industries`;单层 ≤4 项。它是**过滤器**,不是校验器:不合格的条目
  安静地不出现,不抛异常 —— 消费者(`sector/pack.py`)是 scan Stage 1 的前置件,炸在这里
  等于用一个展示增强把整条漏斗打死。
- `lint_map()` —— **入库校验**。把 `load_map` 安静丢掉的每一条都**说出来**,分三级:
  `ERROR`(文件坏了,PR 不该合)/ `PENDING`(合法待办:证据没找到,按纪律留 null)/
  `WARN`(该复核了 / 信息不全,但不阻断)。

## 为什么「没有凭证就写 null」是硬纪律

映射表的条目会被印进研究报告,当作**外部产业事实**用。一条编造的 `evidence_url` 会让
「查无实据的关系」长得和「有一手凭证的关系」一模一样 —— 而这正是**没有任何自然告警**的错误
形态(同族前科:2026-07-24「字段语义误读只有真数据能证伪」)。所以:

    没有真凭证 ⇒ `evidence_url: null` + `status: pending_evidence` ⇒ lint 判 PENDING
                ⇒ `load_map` 剔除 ⇒ 永远进不了任何报告。

反过来,`status` 声称已核实却拿不出公网 http(s) 凭证 = `ERROR`,比 pending 更坏。

## ticker 解析器:可注入,默认**离线**

§8 要求 lint 还要验 ticker 类型 / 交易所 / 币种 / 可交易状态 —— 那需要联网。本模块把它做成
**注入式**(`resolver(symbol) -> dict | None`),且模块级默认 `RESOLVER = None` =
**永不联网**。想在线校验要显式给:`lint_map(resolver=yfinance_resolver())`,或跑
`python -m autoresearch.data.readthrough lint --online`。理由:一个「默认会联网」的 lint
会让单测在没人注意的地方偷偷发请求(测试禁止真网络),也会让 CI 因为第三方限流而变红。
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path

from autoresearch.data.sources.official_event_calendar import (
    UnsafeURLError,
    canonicalize_url,
    is_public_http_url,
)

MAP_PATH = Path(__file__).resolve().parent / "readthrough_map.yaml"

SCHEMA_VERSION = 2
MAX_PER_LAYER = 4                 # §8:单层 ≤4 项,超出由 owner 人工取舍
REVIEW_MAX_AGE_DAYS = 100         # §8:季度复核(留 10 天余量)

KINDS: tuple[str, ...] = ("company", "etf", "index")
RELATIONS: tuple[str, ...] = ("customer", "supplier", "peer", "theme")
DIRECTIONS: tuple[str, ...] = ("downstream", "upstream", "peer")

STATUS_PENDING_EVIDENCE = "pending_evidence"
STATUS_ACTIVE = "active"
# 不可交易态 —— 与 `sector/pack.py::_RT_UNTRADABLE` 同一份清单(两边都拒 = 纵深防御)。
UNTRADABLE_STATUSES: tuple[str, ...] = ("delisted", "suspended", "halted", "unlisted", "expired")
STATUSES: tuple[str, ...] = (STATUS_PENDING_EVIDENCE, STATUS_ACTIVE) + UNTRADABLE_STATUSES

# 「财报主体」语义的键:只有 `kind == "company"` 才配拥有。ETF / 指数没有财报,给它们挂一个
# `next_earnings_date` 就是把一篮子伪装成一家公司(§8 规则二)。
EARNINGS_KEYS: tuple[str, ...] = (
    "next_earnings_date", "earnings_date", "earnings_time", "implied_move_note",
    "report_date", "fiscal_quarter",
)

ITEM_KEYS: tuple[str, ...] = (
    "symbol", "kind", "relation", "direction", "rationale", "evidence_url",
    "effective_from", "effective_to", "status", "tradable",
)
TOP_KEYS: tuple[str, ...] = ("version", "reviewed_at", "owner", "industries", "codes")

# 解析器回报的 quote_type ↔ 本表 kind 的合法配对(大小写不敏感)。
_QUOTE_TYPE_BY_KIND: dict[str, tuple[str, ...]] = {
    "company": ("EQUITY", "ADR"),
    "etf": ("ETF",),
    "index": ("INDEX",),
}
ALLOWED_CURRENCIES: tuple[str, ...] = ("USD",)
# 美国主要交易所代码(yfinance `exchange` 字段口径 + 常见全名)。指数不查交易所。
US_EXCHANGES: tuple[str, ...] = (
    "NMS", "NGM", "NCM", "NYQ", "NYS", "PCX", "ASE", "AMX", "BTS", "PNK",
    "NASDAQ", "NASDAQGS", "NASDAQGM", "NASDAQCM", "NYSE", "NYSEARCA", "ARCA", "AMEX", "BATS", "CBOE",
)

_CODE_RE = re.compile(r"^\d{6}$")

# 模块级解析器:**默认 None = 永不联网**。要在线校验请显式注入(见模块 docstring)。
RESOLVER = None


class ReadthroughMapError(RuntimeError):
    """映射表文件缺失 / 顶层形态不对(**不是**「某条不合格」—— 那个由 lint 报,由 load 静默剔除)。"""


# ───────────────────────────── 读文件 ─────────────────────────────


def _load_yaml(path: Path) -> dict:
    try:
        import yaml
    except ImportError as exc:                                      # pragma: no cover
        raise ReadthroughMapError("缺 PyYAML:`uv sync` 后重试") from exc
    if not path.exists():
        raise ReadthroughMapError(f"映射表不存在:{path}")
    doc = yaml.safe_load(path.read_text(encoding="utf-8"))
    if doc is None:
        return {}
    if not isinstance(doc, dict):
        raise ReadthroughMapError(f"{path} 顶层不是 mapping,而是 {type(doc).__name__}")
    return doc


def load_raw(path: Path | str | None = None) -> dict:
    """整份 yaml(原样,**未过滤**)。给 lint / 巡检用;消费一律走 `load_map`。"""
    return _load_yaml(Path(path) if path else MAP_PATH)


# ───────────────────────────── 小工具 ─────────────────────────────


def _as_iso_date(v) -> str | None:
    """`date` / `YYYY-MM-DD` 串 → ISO 串;其它(含 None / 空 / 垃圾)→ None。

    PyYAML 会把裸 `2026-08-29` 读成 `datetime.date`,把带引号的读成 str —— 两种都得认。
    """
    if v is None or v == "":
        return None
    if isinstance(v, datetime):
        return v.date().isoformat()
    if isinstance(v, date):
        return v.isoformat()
    s = str(v).strip()
    try:
        return date.fromisoformat(s[:10]).isoformat()
    except ValueError:
        return None


def _str(v) -> str:
    return str(v).strip() if v is not None else ""


def _tri_bool(v) -> bool | None:
    """三态布尔:True / False / 未知(None)。"""
    if v is None:
        return None
    if isinstance(v, bool):
        return v
    s = str(v).strip().lower()
    if s in ("true", "1", "yes"):
        return True
    if s in ("false", "0", "no"):
        return False
    return None


def _layers(doc: dict) -> tuple[dict, dict]:
    """(industries, codes);缺 / 形态不对 → 空 dict(由 lint 另行报错)。"""
    ind = doc.get("industries")
    codes = doc.get("codes")
    return (ind if isinstance(ind, dict) else {}), (codes if isinstance(codes, dict) else {})


# ───────────────────────────── 消费准入 ─────────────────────────────


def item_is_consumable(item, as_of_iso: str | None) -> bool:
    """单条是否可进入消费(`load_map` 的唯一判据;纯函数,零 IO)。

    五道门,任一不过即剔除:枚举合法 / 证据 URL 是公网 http(s) / 状态可消费 / `tradable`
    不为 False / 在有效期内。`as_of_iso` 为 None(调用方没给可解析的日期)时**跳过有效期门**
    —— 与 `sector/pack.py::_rt_valid` 同口径:无法判有效期时只保枚举与证据两道门。
    """
    if not isinstance(item, dict):
        return False
    if not _str(item.get("symbol")):
        return False
    if _str(item.get("kind")) not in KINDS:
        return False
    if _str(item.get("relation")) not in RELATIONS:
        return False
    if _str(item.get("direction")) not in DIRECTIONS:
        return False
    if not is_public_http_url(_str(item.get("evidence_url"))):
        return False                                   # 含 pending_evidence 的 null URL
    status = _str(item.get("status")).lower()
    if status in (STATUS_PENDING_EVIDENCE,) + UNTRADABLE_STATUSES:
        return False
    if _tri_bool(item.get("tradable")) is False:
        return False
    # ETF / 指数不得携带财报主体语义(§8 规则二;pack 侧还会再抹一次)
    if _str(item.get("kind")) != "company" and any(
            item.get(k) not in (None, "") for k in EARNINGS_KEYS):
        return False
    eff_from = _as_iso_date(item.get("effective_from"))
    if eff_from is None:
        return False                                   # 起效日必填
    eff_to = _as_iso_date(item.get("effective_to"))
    if as_of_iso is None:
        return True
    if eff_from > as_of_iso:
        return False                                   # 还没生效
    return not (eff_to is not None and eff_to < as_of_iso)


def _clean_item(item: dict, *, layer: str, layer_key: str) -> dict:
    """消费形态的条目:字段规整 + 出处标注。**只搬运,不合成任何方向措辞**。"""
    out = {k: item.get(k) for k in ITEM_KEYS if k in item}
    out["symbol"] = _str(item.get("symbol"))
    out["kind"] = _str(item.get("kind"))
    out["relation"] = _str(item.get("relation"))
    out["direction"] = _str(item.get("direction"))
    out["rationale"] = _str(item.get("rationale"))
    try:
        out["evidence_url"] = canonicalize_url(_str(item.get("evidence_url")))
    except UnsafeURLError:                              # item_is_consumable 已挡,双保险
        out["evidence_url"] = _str(item.get("evidence_url"))
    out["effective_from"] = _as_iso_date(item.get("effective_from"))
    out["effective_to"] = _as_iso_date(item.get("effective_to"))
    out["status"] = _str(item.get("status")) or STATUS_ACTIVE
    out["layer"] = layer
    out["layer_key"] = layer_key
    return out


def load_map(as_of=None, *, path: Path | str | None = None, doc: dict | None = None) -> dict:
    """→ `{"version", "reviewed_at", "owner", "industries": {行业: [条目]}, "codes": {code: [条目]}}`。

    **只返回可消费的条目**(`item_is_consumable`);某层一条都不剩 → 该层的键整个不出现
    (空名单 = 整块省略,不是空 list)。每层截到 `MAX_PER_LAYER`(4)项,保文件顺序。

    `as_of`:PIT 日期(`date` 或 `YYYY-MM-DD` / `YYYYMMDD` 串)。**不可解析 / 缺省 → 不做
    有效期过滤**(与 `sector/pack.py` 同口径),其余门照常。生产调用一律给日期。

    不抛异常的边界:文件缺失 / 顶层坏了仍会抛 `ReadthroughMapError` —— 那是「装配错了」,
    不是「今天没有映射」;消费者(pack)自己 try/except 记 B 级降级。
    """
    doc = load_raw(path) if doc is None else doc
    as_of_iso = _as_iso_date(as_of)
    if as_of_iso is None and as_of not in (None, ""):
        s = str(as_of).strip()
        if len(s) == 8 and s.isdigit():                 # 紧凑串 YYYYMMDD
            as_of_iso = f"{s[:4]}-{s[4:6]}-{s[6:]}"

    industries, codes = _layers(doc)
    out: dict = {
        "version": doc.get("version"),
        "reviewed_at": _as_iso_date(doc.get("reviewed_at")),
        "owner": _str(doc.get("owner")),
        "industries": {},
        "codes": {},
    }
    for layer, src in (("industries", industries), ("codes", codes)):
        for key, items in src.items():
            if not isinstance(items, (list, tuple)):
                continue
            kept = [_clean_item(it, layer=layer, layer_key=str(key))
                    for it in items if item_is_consumable(it, as_of_iso)]
            if kept:
                out[layer][str(key)] = kept[:MAX_PER_LAYER]
    return out


def mappings_for(
    as_of=None,
    *,
    code: str | None = None,
    industry: str | None = None,
    path: Path | str | None = None,
    doc: dict | None = None,
) -> list[dict]:
    """单个消费点要的名单 —— **个股 code 优先于 industry**(§8 规则三)。

    命中 code 就**只用** code 名单(不与行业名单合并):个股映射是人工为这只票挑的,行业名单
    是兜底;合并会让「为这只票精挑的 2 个名字」被 4 个行业通用名冲淡。都没有 → `[]`。
    """
    m = load_map(as_of, path=path, doc=doc)
    if code:
        c = str(code).strip().zfill(6)
        hit = m["codes"].get(c) or m["codes"].get(str(code).strip())
        if hit:
            return list(hit)
    if industry:
        return list(m["industries"].get(str(industry).strip()) or [])
    return []


# ───────────────────────────── lint ─────────────────────────────

LEVEL_ERROR = "ERROR"
LEVEL_PENDING = "PENDING"
LEVEL_WARN = "WARN"


@dataclass(frozen=True)
class LintIssue:
    """一条 lint 结论。`where` 是定位串(如 `industries.半导体[2] SMH`)。"""

    level: str
    where: str
    message: str

    def __str__(self) -> str:                            # pragma: no cover - 展示用
        return f"[{self.level}] {self.where}: {self.message}"


def yfinance_resolver():
    """在线 ticker 解析器(**显式注入才会用**)→ `resolve(symbol) -> dict | None`。

    返回 `{"quote_type", "exchange", "currency", "tradable"}`。**本函数只是工厂**:调它不联网,
    调它返回的那个 callable 才联网。默认 `RESOLVER = None`,所以单测永远碰不到它。
    """
    def _resolve(symbol: str) -> dict | None:
        import yfinance as yf

        t = yf.Ticker(str(symbol))
        info = {}
        try:
            info = dict(t.get_info() or {})
        except Exception:                                # noqa: BLE001 — 拿不到 info 就靠 fast_info
            info = {}
        fast = {}
        try:
            fast = dict(t.fast_info or {})
        except Exception:                                # noqa: BLE001
            fast = {}
        quote_type = info.get("quoteType") or fast.get("quoteType")
        currency = info.get("currency") or fast.get("currency")
        exchange = info.get("exchange") or info.get("fullExchangeName") or fast.get("exchange")
        if quote_type is None and currency is None and exchange is None:
            return None                                  # 解析不到 = 未知,交给 lint 报 WARN
        return {
            "quote_type": quote_type,
            "exchange": exchange,
            "currency": currency,
            # yfinance 没有干净的「可交易」布尔:退市票 info 里常见 0 报价 / 空 exchange。
            # 这里只在明确拿到 `tradeable` 时表态,其余交给人工。
            "tradable": info.get("tradeable"),
        }

    return _resolve


def _lint_item(item, where: str, *, as_of_iso: str | None, resolver) -> list[LintIssue]:
    out: list[LintIssue] = []
    if not isinstance(item, dict):
        return [LintIssue(LEVEL_ERROR, where, f"条目不是 mapping,而是 {type(item).__name__}")]

    sym = _str(item.get("symbol"))
    where = f"{where} {sym or '(无 symbol)'}"
    if not sym:
        out.append(LintIssue(LEVEL_ERROR, where, "缺 symbol"))
    for key, allowed in (("kind", KINDS), ("relation", RELATIONS), ("direction", DIRECTIONS)):
        v = _str(item.get(key))
        if v not in allowed:
            out.append(LintIssue(LEVEL_ERROR, where, f"{key}={v!r} 不在 {list(allowed)}"))
    unknown = [k for k in item if k not in ITEM_KEYS + EARNINGS_KEYS]
    if unknown:
        out.append(LintIssue(LEVEL_WARN, where, f"未知字段 {sorted(unknown)}(拼错的字段会被静默忽略)"))
    if not _str(item.get("rationale")):
        out.append(LintIssue(LEVEL_WARN, where, "缺 rationale —— 没人说得清为什么值得看"))

    kind = _str(item.get("kind"))
    earnings_keys = [k for k in EARNINGS_KEYS if item.get(k) not in (None, "")]
    if earnings_keys and kind != "company":
        out.append(LintIssue(
            LEVEL_ERROR, where,
            f"kind={kind!r} 却带财报主体字段 {earnings_keys} —— "
            f"ETF / 指数不得伪装成公司或财报主体(§8 规则二)"))

    # ── 证据 ──
    status = _str(item.get("status")).lower() or STATUS_ACTIVE
    url = _str(item.get("evidence_url"))
    if status and status not in STATUSES:
        out.append(LintIssue(LEVEL_ERROR, where, f"status={status!r} 不在 {list(STATUSES)}"))
    if status == STATUS_PENDING_EVIDENCE:
        if url:
            out.append(LintIssue(
                LEVEL_ERROR, where,
                f"status=pending_evidence 却带着 evidence_url={url!r} —— 自相矛盾:"
                f"有凭证就改 status,没凭证就写 null"))
        else:
            out.append(LintIssue(
                LEVEL_PENDING, where,
                "证据待补(evidence_url=null)—— 合法待办,但**不进入消费**;"
                "找到公开凭证后填 URL 并把 status 改 active"))
    elif not url:
        out.append(LintIssue(
            LEVEL_ERROR, where,
            f"status={status!r} 却没有 evidence_url —— 没有真凭证一律写 null 并标 "
            f"pending_evidence(禁止编造 URL)"))
    elif not is_public_http_url(url):
        out.append(LintIssue(
            LEVEL_ERROR, where,
            f"evidence_url 不是公网 http(s):{url!r}(私网 / localhost / file: 一律拒)"))
    elif url != canonicalize_url(url):
        out.append(LintIssue(
            LEVEL_WARN, where,
            f"evidence_url 未 canonicalize(追踪参数 / fragment):建议改成 {canonicalize_url(url)!r}"))
    if status in UNTRADABLE_STATUSES:
        out.append(LintIssue(LEVEL_WARN, where, f"status={status} → 不可交易,已停止消费"))
    if _tri_bool(item.get("tradable")) is False:
        out.append(LintIssue(LEVEL_ERROR, where, "tradable=false —— 不可交易的标的不该留在名单里"))

    # ── 有效期 ──
    eff_from = _as_iso_date(item.get("effective_from"))
    eff_to_raw = item.get("effective_to")
    eff_to = _as_iso_date(eff_to_raw)
    if eff_from is None:
        out.append(LintIssue(LEVEL_ERROR, where,
                             f"effective_from 缺失 / 不是 YYYY-MM-DD:{item.get('effective_from')!r}"))
    if eff_to_raw not in (None, "") and eff_to is None:
        out.append(LintIssue(LEVEL_ERROR, where,
                             f"effective_to 不是 YYYY-MM-DD:{eff_to_raw!r}(长期有效请写 null)"))
    if eff_from and eff_to and eff_to < eff_from:
        out.append(LintIssue(LEVEL_ERROR, where, f"有效期倒挂:{eff_from} → {eff_to}"))
    if as_of_iso and eff_to and eff_to < as_of_iso:
        out.append(LintIssue(LEVEL_WARN, where, f"已于 {eff_to} 过期 → 已停止消费(季度复核可删)"))
    if as_of_iso and eff_from and eff_from > as_of_iso:
        out.append(LintIssue(LEVEL_WARN, where, f"{eff_from} 才生效 → 当前不消费"))

    # ── ticker 解析(可注入;默认不联网) ──
    if resolver is not None and sym:
        try:
            meta = resolver(sym)
        except Exception as exc:                          # noqa: BLE001
            return out + [LintIssue(LEVEL_ERROR, where, f"ticker 解析失败:{type(exc).__name__}: {exc}")]
        if not meta:
            out.append(LintIssue(LEVEL_ERROR, where, "ticker 解析不到(退市 / 拼写错 / 非美股?)"))
        else:
            out.extend(_lint_resolved(meta, where, kind=kind))
    elif resolver is None and sym:
        out.append(LintIssue(LEVEL_WARN, where,
                             "未做 ticker 解析(离线 lint):类型 / 交易所 / 币种 / 可交易未验证"))
    return out


def _lint_resolved(meta: dict, where: str, *, kind: str) -> list[LintIssue]:
    out: list[LintIssue] = []
    qt = _str(meta.get("quote_type") or meta.get("quoteType") or meta.get("type")).upper()
    want = _QUOTE_TYPE_BY_KIND.get(kind, ())
    if qt and want and qt not in want:
        out.append(LintIssue(
            LEVEL_ERROR, where,
            f"kind={kind!r} 与解析到的 quote_type={qt!r} 不符(期望 {list(want)})—— "
            f"ETF / 指数不得伪装成公司或财报主体"))
    elif not qt:
        out.append(LintIssue(LEVEL_WARN, where, "解析器没给 quote_type,类型未验证"))

    cur = _str(meta.get("currency")).upper()
    if cur and cur not in ALLOWED_CURRENCIES:
        out.append(LintIssue(LEVEL_ERROR, where,
                             f"币种 {cur!r} 不在 {list(ALLOWED_CURRENCIES)} —— 本表只登记美股读透对象"))
    exch = _str(meta.get("exchange")).upper().replace(" ", "")
    if kind != "index":
        if not exch:
            out.append(LintIssue(LEVEL_WARN, where, "解析器没给 exchange,交易所未验证"))
        elif exch not in US_EXCHANGES:
            out.append(LintIssue(LEVEL_ERROR, where,
                                 f"交易所 {exch!r} 不在美国主板名单 {list(US_EXCHANGES)}"))
    if _tri_bool(meta.get("tradable")) is False:
        out.append(LintIssue(LEVEL_ERROR, where, "解析结果显示当前不可交易(退市 / 停牌)"))
    return out


def lint_map(
    doc: dict | None = None,
    *,
    path: Path | str | None = None,
    as_of=None,
    resolver=None,
) -> list[LintIssue]:
    """入库校验 → `list[LintIssue]`。**永不联网**,除非显式给 `resolver`。

    `resolver` 缺省时回落到模块级 `RESOLVER`(默认 `None` = 离线),此时每条会多一个 WARN
    说明「类型 / 交易所 / 币种 / 可交易未验证」—— 沉默的跳过会让人以为验过了。
    """
    if doc is None:
        try:
            doc = load_raw(path)
        except ReadthroughMapError as exc:
            return [LintIssue(LEVEL_ERROR, str(path or MAP_PATH), str(exc))]
    resolver = RESOLVER if resolver is None else resolver
    as_of_iso = _as_iso_date(as_of)
    out: list[LintIssue] = []

    # ── 顶层 ──
    if doc.get("version") != SCHEMA_VERSION:
        out.append(LintIssue(LEVEL_ERROR, "version",
                             f"version={doc.get('version')!r},期望 {SCHEMA_VERSION}(§8 schema v2)"))
    reviewed = _as_iso_date(doc.get("reviewed_at"))
    if reviewed is None:
        out.append(LintIssue(LEVEL_ERROR, "reviewed_at",
                             f"缺失 / 不是 YYYY-MM-DD:{doc.get('reviewed_at')!r}"))
    elif as_of_iso and (date.fromisoformat(as_of_iso) - date.fromisoformat(reviewed)).days > REVIEW_MAX_AGE_DAYS:
        out.append(LintIssue(LEVEL_WARN, "reviewed_at",
                             f"上次复核 {reviewed},距 {as_of_iso} 超过 {REVIEW_MAX_AGE_DAYS} 天(§8 季度复核)"))
    if not _str(doc.get("owner")):
        out.append(LintIssue(LEVEL_ERROR, "owner", "缺 owner —— 没有 owner 就没人做季度复核与取舍"))
    for k in doc:
        if k not in TOP_KEYS:
            out.append(LintIssue(LEVEL_WARN, "(top)", f"未知顶层键 {k!r}"))
    for k in ("industries", "codes"):
        if k in doc and not isinstance(doc[k], dict):
            out.append(LintIssue(LEVEL_ERROR, k,
                                 f"不是 mapping,而是 {type(doc[k]).__name__}"))

    industries, codes = _layers(doc)
    for layer, src in (("industries", industries), ("codes", codes)):
        for key, items in src.items():
            where = f"{layer}.{key}"
            if layer == "codes" and not (isinstance(key, str) and _CODE_RE.match(key)):
                out.append(LintIssue(
                    LEVEL_ERROR, where,
                    f"code 键必须是**带引号**的 6 位数字串(收到 {key!r}:{type(key).__name__})"
                    f" —— 不加引号的 000001 会被 YAML 读成整数 1"))
            if not isinstance(items, (list, tuple)):
                out.append(LintIssue(LEVEL_ERROR, where,
                                     f"名单不是 list,而是 {type(items).__name__}"))
                continue
            if len(items) > MAX_PER_LAYER:
                out.append(LintIssue(
                    LEVEL_ERROR, where,
                    f"{len(items)} 项 > 单层上限 {MAX_PER_LAYER}(§8)—— 由 owner 人工取舍,"
                    f"不是靠消费端截断"))
            seen: set[str] = set()
            for i, it in enumerate(items):
                sym = _str(it.get("symbol")).upper() if isinstance(it, dict) else ""
                if sym and sym in seen:
                    out.append(LintIssue(LEVEL_ERROR, f"{where}[{i}] {sym}", "同层重复 symbol"))
                seen.add(sym)
                out.extend(_lint_item(it, f"{where}[{i}]", as_of_iso=as_of_iso, resolver=resolver))
    return out


def lint_errors(issues) -> list[LintIssue]:
    """只留 ERROR(PR 门用:`assert not lint_errors(lint_map())`)。"""
    return [i for i in issues if i.level == LEVEL_ERROR]


def format_issues(issues) -> str:
    counts: dict[str, int] = {}
    for i in issues:
        counts[i.level] = counts.get(i.level, 0) + 1
    head = " / ".join(f"{lv}={counts.get(lv, 0)}" for lv in (LEVEL_ERROR, LEVEL_PENDING, LEVEL_WARN))
    body = "\n".join(f"  [{i.level}] {i.where}: {i.message}" for i in issues)
    return f"{head}\n{body}" if body else head


def _main(argv=None) -> int:                              # pragma: no cover - CLI 壳
    import argparse

    ap = argparse.ArgumentParser(description="海外读透映射表 lint / 预览")
    ap.add_argument("cmd", choices=["lint", "show"], nargs="?", default="lint")
    ap.add_argument("--as-of", default=None, help="PIT 日期 YYYY-MM-DD(判有效期 / 复核时效)")
    ap.add_argument("--path", default=None, help="映射表路径(默认包内 readthrough_map.yaml)")
    ap.add_argument("--online", action="store_true",
                    help="启用 yfinance ticker 解析(联网!默认离线)")
    ap.add_argument("--quiet", action="store_true", help="只打 ERROR / PENDING")
    a = ap.parse_args(argv)

    if a.cmd == "show":
        import json
        print(json.dumps(load_map(a.as_of, path=a.path), ensure_ascii=False, indent=2))
        return 0

    issues = lint_map(path=a.path, as_of=a.as_of,
                      resolver=yfinance_resolver() if a.online else None)
    shown = [i for i in issues if i.level in (LEVEL_ERROR, LEVEL_PENDING)] if a.quiet else issues
    # 计数永远按**全量**打(--quiet 只压正文)—— 否则「WARN=0」会是过滤出来的假象。
    print(format_issues(issues).splitlines()[0])
    for i in shown:
        print(f"  [{i.level}] {i.where}: {i.message}")
    return 1 if lint_errors(issues) else 0


__all__ = [
    "MAP_PATH", "SCHEMA_VERSION", "MAX_PER_LAYER", "REVIEW_MAX_AGE_DAYS",
    "KINDS", "RELATIONS", "DIRECTIONS", "STATUSES", "UNTRADABLE_STATUSES",
    "STATUS_PENDING_EVIDENCE", "STATUS_ACTIVE", "EARNINGS_KEYS",
    "ALLOWED_CURRENCIES", "US_EXCHANGES", "RESOLVER",
    "LEVEL_ERROR", "LEVEL_PENDING", "LEVEL_WARN", "LintIssue", "ReadthroughMapError",
    "load_raw", "load_map", "mappings_for", "item_is_consumable",
    "lint_map", "lint_errors", "format_issues", "yfinance_resolver",
]

if __name__ == "__main__":                                # pragma: no cover
    raise SystemExit(_main())
