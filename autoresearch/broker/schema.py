#!/usr/bin/env python3
"""券商成交 · 标准化成交表 —— 列定义、归一、两级契约(确定性、零 LLM、零网络)。

design: docs/specs/2026-08-27-broker-trades-ingest-design.md §6/§7

adapter 只负责把各家文件读成 `RAW_COLUMNS` 的**字符串帧**(不做任何解释);本模块把它归一成
`RAW_STORE_COLUMNS`(code 补零 / ts_code / side / 数值化 / 同价分笔 seq / row_hash / trade_id),
再按两级契约校验(`validate`):

- **A 级**(违反即整文件拒收,不落 raw):0 行、账户不在 `ACCOUNTS`、日期不可解析或在未来、
  BUY/SELL 行 code 非 6 位 / price·qty 非正 / **|amount − price×qty| > max(1 元, 0.5%)**
  —— 最后这条专门兜截图识读错位/丢位,也兜 PDF 抽表串列。
- **B 级**(降级 + `data.contracts.record_degradation` 记账,摘要屏必印):费用四项缺、
  时刻缺、名称缺、剩余持仓缺、net_amount 与 amount±费用 偏差 > 1 元。
- BUY 非 100 股整数倍只 warn 不拦(科创板/北交所允许 1 股递增)。

同价分笔成交是合法的两行:`seq` = 同文件内 (date, code, side, price, qty) 组内序号,进 row_hash,
去重不会把它吃掉。
"""
from __future__ import annotations

import hashlib
import math
import re
import unicodedata
from dataclasses import dataclass, field
from datetime import date, datetime

import pandas as pd

from autoresearch.data.contracts import DataContractError, record_degradation
from autoresearch.dataflows.symbol_utils import to_ts_code

ACCOUNTS = ("tpy", "gtht")
SOURCE_KINDS = ("chinaclear", "gtht", "tpy", "screenshot")
SIDES = ("BUY", "SELL", "OTHER")

#: adapter 产出的原始列(全字符串;trade_id 可空)
RAW_COLUMNS = (
    "account", "trade_date", "trade_time", "code", "name", "biz_type",
    "price", "qty", "amount", "commission", "stamp_tax", "transfer_fee", "other_fee",
    "net_amount", "balance_after", "trade_id",
)
FEE_COLUMNS = ("commission", "stamp_tax", "transfer_fee", "other_fee")
NUMERIC_COLUMNS = ("price", "qty", "amount", *FEE_COLUMNS, "net_amount", "balance_after")
#: trades.csv 列(设计稿 §6)
TRADES_COLUMNS = (
    "account", "trade_date", "trade_time", "code", "ts_code", "name", "side", "biz_type",
    "price", "qty", "amount", "commission", "stamp_tax", "transfer_fee", "other_fee",
    "net_amount", "balance_after", "trade_id", "source_kind", "source_file", "sources",
    "ingested_at",
)
#: raw/<src>.csv 列 = trades 去掉 sources,加 seq/row_hash
RAW_STORE_COLUMNS = tuple(c for c in TRADES_COLUMNS if c != "sources") + ("seq", "row_hash")
#: 跨源自然键(§8,按计数匹配)
NATURAL_KEY = ("account", "trade_date", "code", "side", "price", "qty")
#: 合并时低优先源只补缺的列
FILLABLE_COLUMNS = ("trade_time", "name", *FEE_COLUMNS, "net_amount", "balance_after")
ECONOMIC_IDENTITY_FIELDS = ("account", "trade_date", "trade_time", "code", "side",
                            "price", "qty", "amount")

AMOUNT_TOL_ABS = 1.0
AMOUNT_TOL_REL = 0.005
NET_TOL_ABS = 1.0
_MAX_PROBLEM_LINES = 10
_DATE_PATTERNS = ("%Y-%m-%d", "%Y%m%d", "%Y/%m/%d", "%Y.%m.%d")
_TIME_PATTERNS = ("%H:%M:%S", "%H%M%S", "%H:%M")


def is_blank(v) -> bool:
    if v is None:
        return True
    if isinstance(v, float) and math.isnan(v):
        return True
    return str(v).strip() == ""


def _s(v) -> str:
    return "" if is_blank(v) else str(v).strip()


def fmt_num(v) -> str:
    """数值 → 稳定字符串(hash / 自然键用);NaN → ""。"""
    return "" if is_blank(v) else f"{float(v):.4f}"


def to_side(biz_type) -> str:
    s = _s(biz_type).upper()
    if not s or "撤" in s:
        return "OTHER"
    if s in ("BUY", "B") or "买" in s:
        return "BUY"
    if s in ("SELL", "S") or "卖" in s:
        return "SELL"
    return "OTHER"


def _parse_date(raw) -> str | None:
    s = _s(raw)
    for pat in _DATE_PATTERNS:
        try:
            return datetime.strptime(s, pat).date().isoformat()
        except ValueError:
            continue
    return None


def _parse_time(raw) -> str:
    s = _s(raw)
    if not s:
        return ""
    for pat in _TIME_PATTERNS:
        try:
            return datetime.strptime(s, pat).time().isoformat()
        except ValueError:
            continue
    return s


def _to_num(raw) -> float:
    """空 → NaN;非空但不可解析 → raise ValueError(不许把「解析失败」伪装成「缺」)。"""
    s = _s(raw).replace(",", "").replace("元", "")
    if not s:
        return math.nan
    return float(s)


def _norm_code(raw) -> str:
    s = unicodedata.normalize("NFKC", _s(raw)).upper().split(".")[0]
    return s.zfill(6) if s.isdigit() else s


def parse_date(raw) -> str | None:
    """任意常见写法 → ISO 日期;解析失败 → None(CLI 参数校验也用它)。"""
    return _parse_date(raw)


def natural_key(r) -> tuple[str, ...]:
    """跨源「同一笔」的唯一定义(§8):merge / reconcile 共用,不许各写各的。

    数值经 `fmt_num` 折到 4 位小数(adapter 若用 amount/qty 反推 price 不会因浮点噪音错配);
    OTHER 行(红利/税/利息/转账)没有 price/qty,再加 amount 区分 —— 否则同日两笔现金流会被并成一笔。
    """
    g = r.__getitem__ if isinstance(r, dict) else (lambda k: getattr(r, k))
    side = g("side")
    return (str(g("account")), str(g("trade_date")), str(g("code")), str(side),
            fmt_num(g("price")), fmt_num(g("qty")), fmt_num(g("amount")) if side == "OTHER" else "")


def natural_key_strings(df: pd.DataFrame) -> list[str]:
    return ["|".join(natural_key(r)) for r in df.itertuples(index=False)]


def economic_signature(r) -> tuple[str, ...]:
    """成交身份发生冲突时用于比较不可静默变化的经济字段。"""
    g = r.__getitem__ if isinstance(r, dict) else (lambda k: getattr(r, k))
    return tuple(fmt_num(g(key)) if key in {"price", "qty", "amount"} else _s(g(key))
                 for key in ECONOMIC_IDENTITY_FIELDS)


def normalize(df_raw: pd.DataFrame, *, source_kind: str, source_file: str,
              ingested_at: str | None = None) -> pd.DataFrame:
    """RAW 字符串帧 → `RAW_STORE_COLUMNS` 帧(确定性:同输入同 row_hash;ingested_at 不进 hash)。"""
    missing = [c for c in RAW_COLUMNS if c not in df_raw.columns]
    if missing:
        raise DataContractError(
            f"adapter 输出缺列 {missing}(source={source_kind} file={source_file})")
    df = df_raw.loc[:, list(RAW_COLUMNS)].copy().reset_index(drop=True)
    df["account"] = df["account"].map(lambda v: _s(v).lower())
    df["code"] = df["code"].map(_norm_code)
    df["ts_code"] = df["code"].map(lambda c: to_ts_code(c) if c else "")
    df["name"] = df["name"].map(_s)
    df["biz_type"] = df["biz_type"].map(_s)
    df["side"] = df["biz_type"].map(to_side)
    df["trade_date"] = df["trade_date"].map(lambda v: _parse_date(v) or _s(v))
    df["trade_time"] = df["trade_time"].map(_parse_time)
    bad: list[str] = []
    for col in NUMERIC_COLUMNS:
        vals: list[float] = []
        for i, v in enumerate(df[col]):
            try:
                vals.append(_to_num(v))
            except ValueError:
                bad.append(f"第{i + 1}行:{col} {_s(v)!r} 不可解析")
                vals.append(math.nan)
        df[col] = pd.Series(vals, index=df.index, dtype=float)
    if bad:
        more = f"\n…共 {len(bad)} 处" if len(bad) > _MAX_PROBLEM_LINES else ""
        raise DataContractError(f"{source_file}:A 级违约,整文件拒收(数值不可解析):\n"
                                + "\n".join(bad[:_MAX_PROBLEM_LINES]) + more)
    df["trade_id"] = df["trade_id"].map(_s)
    # 同价分笔序号:组键含 account;先按 (时刻, 成交编号) 稳定排序再编号,与文件行序无关
    ordered = df.sort_values(["trade_time", "trade_id"], kind="stable")
    df["seq"] = ordered.groupby(["account", "trade_date", "code", "side", "price", "qty"],
                                dropna=False).cumcount()
    hashes = []
    for r in df.itertuples(index=False):
        identity = ([source_kind, r.account, r.trade_date, r.trade_id]
                    if r.trade_id else
                    [r.account, r.trade_date, r.trade_time, r.code, r.side,
                     fmt_num(r.price), fmt_num(r.qty), fmt_num(r.amount), str(r.seq)])
        hashes.append(hashlib.sha1("|".join(identity).encode("utf-8")).hexdigest())
    df["row_hash"] = hashes
    df["trade_id"] = [tid or f"h:{h[:16]}"
                      for tid, h in zip(df["trade_id"], df["row_hash"], strict=True)]
    df["source_kind"] = source_kind
    df["source_file"] = source_file
    df["ingested_at"] = ingested_at or datetime.now().isoformat(timespec="seconds")
    return df.loc[:, list(RAW_STORE_COLUMNS)]


@dataclass
class ValidationReport:
    source_kind: str
    source_file: str
    rows: int
    accounts: tuple[str, ...] = ()
    period: tuple[str, str] | None = None
    b_degradations: dict[str, int] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)
    #: 按账户拆的 B 级计数(中国结算一份文件含两户时,摘要屏才不会把一份的降级算到每个账户头上)
    b_by_account: dict[str, dict[str, int]] = field(default_factory=dict)


def validate(df: pd.DataFrame, *, today: date | None = None) -> ValidationReport:
    """两级契约。A 级 → raise `DataContractError`(整文件拒收);B 级 → 记账并进报告。"""
    src = str(df["source_kind"].iloc[0]) if len(df) else "?"
    fname = str(df["source_file"].iloc[0]) if len(df) else "?"
    if len(df) == 0:
        raise DataContractError(f"{fname}:0 行 —— 空文件拒收(source={src})")
    today = today or date.today()
    bad_accounts = sorted(set(df["account"]) - set(ACCOUNTS))
    if bad_accounts:
        raise DataContractError(
            f"{fname}:账户 {bad_accounts} 不在 {ACCOUNTS};截图/券商源需 --account,"
            "中国结算源需 accounts.jsonc 归户")
    problems: list[str] = []
    for i, r in enumerate(df.itertuples(index=False)):
        line = f"第{i + 1}行"
        d = _parse_date(r.trade_date)
        if d is None:
            problems.append(f"{line}:trade_date {r.trade_date!r} 不可解析")
        elif d > today.isoformat():
            problems.append(f"{line}:trade_date {d} 在未来")
        code_ok = bool(re.fullmatch(r"\d{6}", r.code or ""))
        if r.side in ("BUY", "SELL"):
            if not code_ok:
                problems.append(f"{line}:code {r.code!r} 非 6 位数字")
            if not r.price > 0:
                problems.append(f"{line}:price {r.price} 非正")
            if not r.qty > 0:
                problems.append(f"{line}:qty {r.qty} 非正")
            if math.isnan(r.amount):
                problems.append(f"{line}:amount 缺")
            elif r.price > 0 and r.qty > 0:
                diff = abs(r.amount - r.price * r.qty)
                if diff > max(AMOUNT_TOL_ABS, AMOUNT_TOL_REL * abs(r.amount)):
                    problems.append(f"{line}:amount {r.amount} ≠ price×qty "
                                    f"{r.price * r.qty:.2f}(差 {diff:.2f})")
        elif r.code and not code_ok:
            problems.append(f"{line}:code {r.code!r} 非 6 位数字")
    if problems:
        more = f"\n…共 {len(problems)} 处" if len(problems) > _MAX_PROBLEM_LINES else ""
        raise DataContractError(f"{fname}:A 级违约,整文件拒收:\n"
                                + "\n".join(problems[:_MAX_PROBLEM_LINES]) + more)

    trades = df[df["side"].isin(("BUY", "SELL"))]
    b_by_account = {acct: _b_checks(g) for acct, g in trades.groupby("account", sort=True)}
    b: dict[str, int] = {}
    for d in b_by_account.values():
        for reason, n in d.items():
            b[reason] = b.get(reason, 0) + n
    for reason, n in b.items():
        record_degradation(f"broker/{src}", f"{reason} ×{n}", key=fname)

    warnings: list[str] = []
    odd = trades[(trades["side"] == "BUY") & ((trades["qty"] % 100) != 0)]
    if len(odd):
        warnings.append(f"BUY 非 100 股整数倍 ×{len(odd)}")
    return ValidationReport(
        src, fname, len(df), tuple(sorted(set(df["account"]))),
        (str(df["trade_date"].min()), str(df["trade_date"].max())), b, warnings,
        {a: d for a, d in b_by_account.items() if d})


def _b_checks(trades: pd.DataFrame) -> dict[str, int]:
    """B 级计数(只看 BUY/SELL 行):费用四项缺 / 时刻缺 / 名称缺 / 剩余持仓缺 / net_amount 不符。"""
    b: dict[str, int] = {}
    for col in FEE_COLUMNS:
        n = int(trades[col].isna().sum())
        if n:
            b[f"{col} 缺"] = n
    for col in ("trade_time", "name"):
        n = int((trades[col] == "").sum())
        if n:
            b[f"{col} 缺"] = n
    n = int(trades["balance_after"].isna().sum())
    if n:
        b["balance_after 缺"] = n
    known = trades[list(FEE_COLUMNS)].notna().all(axis=1) & trades["net_amount"].notna()
    if known.any():
        t = trades[known]
        fees = t[list(FEE_COLUMNS)].sum(axis=1)
        expected = (t["amount"] - fees).where(t["side"] == "SELL", -(t["amount"] + fees))
        n = int(((t["net_amount"] - expected).abs() > NET_TOL_ABS).sum())
        if n:
            b["net_amount 与 amount±费用 偏差>1元"] = n
    return b
