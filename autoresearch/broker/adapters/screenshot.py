#!/usr/bin/env python3
"""截图路 adapter —— 读「Claude 在 session 内看截图后手写的标准 CSV」。

design: docs/specs/2026-08-27-broker-trades-ingest-design.md §9(读图契约)

读图不是自动,但它让「某家 App 导不出文件」不阻塞整条线。为了让识读错能被契约兜住,
表头**逐字锁死**(缺列留空,不许改名/换序),之后与其他来源走完全相同的归一/契约/合并,
只是来源优先级最低。文件名 `<account>_<起YYYYMMDD>-<止YYYYMMDD>.csv` 自带账户。
"""
from __future__ import annotations

import io
import re
from pathlib import Path

import pandas as pd

from autoresearch.broker import schema, sniff
from autoresearch.data.contracts import DataContractError

SCREENSHOT_HEADER = (
    "trade_date", "trade_time", "code", "name", "biz_type", "price", "qty", "amount",
    "commission", "stamp_tax", "transfer_fee", "other_fee", "net_amount", "balance_after",
)
_NAME_RE = re.compile(r"^(?P<account>[a-z0-9]+)_\d{8}-\d{8}\.csv$")


def parse(path: Path, *, account: str | None = None) -> pd.DataFrame:
    path = Path(path)
    kind = sniff.sniff(path)
    if kind != "text":
        raise DataContractError(f"{path.name}:截图路只收标准 CSV,真身是 {kind}")
    df = pd.read_csv(io.StringIO(sniff.read_text(path)), dtype=str, keep_default_na=False)
    header = tuple(str(c).strip() for c in df.columns)
    if header != SCREENSHOT_HEADER:
        raise DataContractError(
            f"{path.name}:表头必须逐字为\n  {','.join(SCREENSHOT_HEADER)}\n实际\n  {','.join(header)}")
    m = _NAME_RE.match(path.name)
    acct = account or (m.group("account") if m else None)
    if not acct:
        raise DataContractError(
            f"{path.name}:截图 CSV 需要 --account,或文件名形如 <account>_<起>-<止>.csv")
    df.columns = list(header)
    df.insert(0, "account", acct)
    df["trade_id"] = ""
    return df.loc[:, list(schema.RAW_COLUMNS)]
