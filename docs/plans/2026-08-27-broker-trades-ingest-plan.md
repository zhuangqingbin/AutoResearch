# 券商成交取数层(里程碑 1 · 格式无关核心)实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 建起 `autoresearch/broker/` 包:标准化成交表 + 两级契约 + 幂等 raw/merge 落盘 + 嗅探 + 截图 adapter + `ingest`/`reconcile` 两个 CLI,全部零 LLM、零网络、零新依赖;三个券商格式 adapter(chinaclear/gtht/tpy)**不在本计划**,等真样本探针后另起任务。

**Architecture:** adapter 把任一来源文件读成字符串帧(`RAW_COLUMNS`)→ `schema.normalize` 归一(补零/ts_code/side/数值化/同价分笔 seq/row_hash/trade_id)→ `schema.validate` 两级契约(A 抛 `DataContractError` 整文件拒收,B `record_degradation` 记账)→ `store.upsert_raw` 按 row_hash 幂等追加 `raw/<src>.csv` → `store.merge` 每次全量确定性重建 `trades.csv`(自然键按计数匹配、来源优先级补缺)→ 摘要屏 + `ingest_log.jsonl`。`reconcile` 只读 raw 报三桶差异,不裁决。

**Tech Stack:** Python 3.13 / pandas 2.3(仓内已有)/ argparse / hashlib;测试 pytest;lint ruff(line-length 100, target py310)。

**Spec:** `docs/specs/2026-08-27-broker-trades-ingest-design.md`(§5 架构 · §6 列 · §7 契约 · §8 幂等合并 · §9 嗅探与截图契约 · §11 CLI · §12 测试 · §13 隐私)

## Global Constraints

- **零 LLM、零网络、零新依赖**:`pyproject` 现只有 pandas;xlsx/xls/pdf 库**探针证明真实存在才加**(§9)。
- **产物根只许走 `autoresearch.common.workspace`**:生产代码不得出现裸 `context…`/`reports…` 字面量(`tests/common/test_workspace.py::test_no_bare_root_literals_in_source` 会扫 `autoresearch/` 全部 `.py`)。产物落 `context_<engine>/broker/`,**不进 `lake/`**。
- **`DataContractError` 不得被吞**(仓内既有原则):可以按文件捕获后记「拒收」并继续下一个文件,但必须打印、写日志、CLI 退出码非 0。
- **隐私(§13)**:代码、测试、日志、提交信息里**不出现真实账号/身份证位/真实成交**;测试夹具全部合成;`ingest_log` 只记哈希、期间、计数。
- 命令一律从仓库根目录跑:`uv run --no-sync python -m pytest …` / `uv run --no-sync ruff check …`。
- 行宽 100;`from __future__ import annotations`;模块 docstring 写「为什么」(仓内风格);新 CLI 用 argparse 且必须能 `--help`(`tests/test_cli_entrypoints.py` 会对每个带 `__main__` 的模块跑 `--help`)。
- 每个测试先问「把实现这段删掉它会红吗」(Wave35 变异教训);不写只锁形状不锁行为的测试。

**与设计稿的三处细化**(实施时决定,已在此明示):
1. 持久化与合并从 `ingest.py` 抽成 `store.py`,嗅探从 `adapters/__init__.py` 抽成 `sniff.py`(避免 adapter ↔ 包 `__init__` 循环 import;文件职责单一)。
2. §7「`code` 6 位数字」细化为:BUY/SELL 行必须;**OTHER 行允许空 code**(利息归本/资金类行本来就没有代码),非空则也须 6 位。
3. `ingest_log` 同时记「拒收」条目(审计);「已导入跳过」只看 `status == "ok"` 的哈希。
4. 摘要屏账户行按**账户**而非来源分行;末行为「多源匹配 N 行 · 单源 M 行」,逐对配对明细归 `reconcile`;跳过的文件逐个印「↷ 已导入 <file>」。
5. (复核后)`natural_key` 抽成 `schema` 共享定义(merge/reconcile 同源;数值 4 位小数;OTHER 行加 amount);`--account` 与文件名账户冲突拒收;坏文件不中止批次;日志不记逐笔;B 降级按账户拆;数值不可解析 = A 级。

---

## File Structure

| 文件 | 职责 |
|---|---|
| `autoresearch/common/workspace.py`(改) | 新增 `broker_root()` |
| `autoresearch/broker/__init__.py`(新) | 包 docstring |
| `autoresearch/broker/schema.py`(新) | 列常量 / `to_side` / `normalize` / `validate` / `ValidationReport` / `is_blank` / `fmt_num` |
| `autoresearch/broker/store.py`(新) | 路径 / `sha256_of` / `read_raw` / `upsert_raw` / `append_log` / `ingested_shas` / `merge` |
| `autoresearch/broker/sniff.py`(新) | `sniff` / `read_text` |
| `autoresearch/broker/adapters/__init__.py`(新) | `REGISTRY` / `detect_source_kind` / `parse` |
| `autoresearch/broker/adapters/screenshot.py`(新) | 固定表头 CSV → RAW 帧 |
| `autoresearch/broker/ingest.py`(新) | CLI:文件遍历 → 解析 → 契约 → 落盘 → 合并 → 摘要屏 |
| `autoresearch/broker/reconcile.py`(新) | CLI:跨源三桶核对,只报不裁 |
| `tests/common/test_workspace.py`(改) | `broker_root` 断言 |
| `tests/broker/{__init__,conftest,test_schema,test_validate,test_store,test_merge,test_sniff,test_screenshot,test_ingest_cli,test_reconcile}.py`(新) | 见各任务 |
| `CLAUDE.md`、设计稿状态行(改) | 包结构一行 + 状态 |

---

### Task 1: `workspace.broker_root()` + 包骨架

**Files:**
- Modify: `autoresearch/common/workspace.py`(在 `factor_lab_root` 之后追加)
- Create: `autoresearch/broker/__init__.py`、`autoresearch/broker/adapters/__init__.py`(先空,Task 6 填)、`tests/broker/__init__.py`
- Test: `tests/common/test_workspace.py::test_roots_follow_engine`

**Interfaces:**
- Produces: `ws.broker_root() -> Path`(= `context_root() / "broker"`,按进程引擎)

- [ ] **Step 1: 写失败测试** —— 在 `tests/common/test_workspace.py::test_roots_follow_engine` 的 claude 段(`assert ws.scan_dir(...)` 之后)加一行,codex 段再加一行:

```python
    assert ws.broker_root() == Path("context_claude/broker")
```
```python
    assert ws.broker_root() == Path("context_codex/broker")
```

- [ ] **Step 2: 跑,确认红**

Run: `uv run --no-sync python -m pytest tests/common/test_workspace.py::test_roots_follow_engine -q`
Expected: FAIL `AttributeError: module ... has no attribute 'broker_root'`

- [ ] **Step 3: 实现** —— `autoresearch/common/workspace.py` 末尾追加:

```python
def broker_root() -> Path:
    """`context_<engine>/broker/` —— 券商成交取数层产物根(2026-08-27 设计稿 §5)。

    个人财务数据:**不进 lake/**(lake 是跨引擎共享的行情湖),按引擎分根、gitignore。
    """
    return context_root() / "broker"
```

并建三个文件:

`autoresearch/broker/__init__.py`:
```python
"""券商成交取数层 —— 把手机 App 导出的交割单/对账单/截图表解析成标准化成交表(零 LLM、零网络)。

design: docs/specs/2026-08-27-broker-trades-ingest-design.md

模块:schema(列与两级契约)· sniff(真身嗅探)· adapters(各来源 → RAW 帧)· store(幂等落盘与合并)
· ingest / reconcile(CLI)。只记不学:不回注 prompt、不改门/权重/评级。
"""
```

`autoresearch/broker/adapters/__init__.py`:
```python
"""来源 adapter 注册表(Task 6 填充)。"""
```

`tests/broker/__init__.py`:空文件。

- [ ] **Step 4: 跑,确认绿**

Run: `uv run --no-sync python -m pytest tests/common/test_workspace.py -q`
Expected: 全绿(含裸根 grep 探针)

- [ ] **Step 5: 提交**

```bash
git add autoresearch/common/workspace.py autoresearch/broker/__init__.py autoresearch/broker/adapters/__init__.py tests/broker/__init__.py tests/common/test_workspace.py
git commit -m "feat(broker): workspace.broker_root + 包骨架(2026-08-27 设计稿 §5)"
```

---

### Task 2: `schema.normalize` —— 归一(补零/ts_code/side/数值/seq/row_hash/trade_id)

**Files:**
- Create: `autoresearch/broker/schema.py`
- Create: `tests/broker/conftest.py`、`tests/broker/test_schema.py`

**Interfaces:**
- Produces: 常量 `ACCOUNTS=("tpy","gtht")`、`SOURCE_KINDS=("chinaclear","gtht","tpy","screenshot")`、`RAW_COLUMNS`(16 列)、`FEE_COLUMNS`、`NUMERIC_COLUMNS`、`TRADES_COLUMNS`(22 列,§6)、`RAW_STORE_COLUMNS`(= TRADES 去 `sources` + `seq`,`row_hash`)、`NATURAL_KEY`、`FILLABLE_COLUMNS`;函数 `to_side(biz_type) -> str`、`is_blank(v) -> bool`、`fmt_num(v) -> str`、`normalize(df_raw, *, source_kind, source_file, ingested_at=None) -> pd.DataFrame`(列序 = `RAW_STORE_COLUMNS`)

- [ ] **Step 1: 写夹具与失败测试**

`tests/broker/conftest.py`:
```python
"""broker 测试共用夹具:一行合成 RAW 帧(数值全假,账户只用别名)。"""
from __future__ import annotations

import pandas as pd
import pytest


def _raw_row(**over) -> dict:
    base = {
        "account": "gtht", "trade_date": "20260826", "trade_time": "09:31:05", "code": "1",
        "name": "平安银行", "biz_type": "证券买入", "price": "12.34", "qty": "100",
        "amount": "1,234.00", "commission": "5", "stamp_tax": "0", "transfer_fee": "0.01",
        "other_fee": "", "net_amount": "-1239.01", "balance_after": "100", "trade_id": "",
    }
    base.update(over)
    return base


@pytest.fixture
def raw():
    """`raw(**over)` → 单行 RAW 帧;`raw.rows(a, b)` → 多行。"""
    def make(**over) -> pd.DataFrame:
        return pd.DataFrame([_raw_row(**over)])
    make.rows = lambda *dicts: pd.DataFrame([_raw_row(**d) for d in dicts])
    return make
```

`tests/broker/test_schema.py`:
```python
"""schema.normalize:补零/ts_code/side/数值化/同价分笔 seq/row_hash/trade_id 的行为锁。"""
from __future__ import annotations

import pandas as pd
import pytest

from autoresearch.broker import schema
from autoresearch.data.contracts import DataContractError

AT = "2026-08-27T10:00:00"


def _norm(df, kind="gtht", file="a.xlsx"):
    return schema.normalize(df, source_kind=kind, source_file=file, ingested_at=AT)


def test_normalize_code_zfill_ts_code_side_and_numbers(raw):
    out = _norm(raw())
    r = out.iloc[0]
    assert r.code == "000001" and r.ts_code == "000001.SZ" and r.side == "BUY"
    assert r.trade_date == "2026-08-26" and r.trade_time == "09:31:05"
    assert r.amount == 1234.0 and r.commission == 5.0 and pd.isna(r.other_fee)
    assert r.trade_id == "h:" + r.row_hash[:16]
    assert r.source_kind == "gtht" and r.source_file == "a.xlsx" and r.ingested_at == AT
    assert list(out.columns) == list(schema.RAW_STORE_COLUMNS)


def test_code_with_suffix_and_sh_board(raw):
    assert _norm(raw(code="600519.SS")).iloc[0].ts_code == "600519.SH"
    assert _norm(raw(code="300857")).iloc[0].ts_code == "300857.SZ"


def test_to_side_keywords():
    assert schema.to_side("证券买入") == "BUY"
    assert schema.to_side("担保品卖出") == "SELL"
    assert schema.to_side("红利入账") == "OTHER"
    assert schema.to_side("买入撤单") == "OTHER"
    assert schema.to_side("") == "OTHER"


def test_same_price_split_fills_keep_both_rows(raw):
    out = _norm(raw.rows({}, {}))
    assert out.seq.tolist() == [0, 1]
    assert out.row_hash.nunique() == 2


def test_normalize_is_deterministic(raw):
    assert _norm(raw()).row_hash.tolist() == _norm(raw()).row_hash.tolist()


def test_hash_ignores_ingested_at_but_not_price(raw):
    a = schema.normalize(raw(), source_kind="gtht", source_file="a", ingested_at="2026-01-01T00:00:00")
    b = schema.normalize(raw(), source_kind="gtht", source_file="a", ingested_at="2026-02-02T00:00:00")
    c = _norm(raw(price="12.35"))
    assert a.row_hash.iloc[0] == b.row_hash.iloc[0] != c.row_hash.iloc[0]


def test_source_trade_id_wins_over_hash(raw):
    assert _norm(raw(trade_id="  A1B2 ")).iloc[0].trade_id == "A1B2"


def test_other_row_without_code_or_price(raw):
    r = _norm(raw(code="", biz_type="利息归本", price="", qty="", amount="12.3")).iloc[0]
    assert r.side == "OTHER" and r.code == "" and r.ts_code == "" and pd.isna(r.price)


def test_missing_raw_column_raises(raw):
    with pytest.raises(DataContractError, match="缺列"):
        _norm(raw().drop(columns=["qty"]))


def test_empty_frame_normalizes_to_zero_rows(raw):
    out = _norm(raw().iloc[0:0])
    assert len(out) == 0 and list(out.columns) == list(schema.RAW_STORE_COLUMNS)
```

- [ ] **Step 2: 跑,确认红**

Run: `uv run --no-sync python -m pytest tests/broker/test_schema.py -q`
Expected: FAIL `ModuleNotFoundError: No module named 'autoresearch.broker.schema'`

- [ ] **Step 3: 实现 `autoresearch/broker/schema.py`**(本任务只到 `normalize`;`validate` 在 Task 3 追加到同文件)

```python
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
    s = _s(raw).replace(",", "").replace("元", "")
    if not s:
        return math.nan
    try:
        return float(s)
    except ValueError:
        return math.nan


def _norm_code(raw) -> str:
    s = _s(raw).upper().split(".")[0]
    return s.zfill(6) if s.isdigit() else s


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
    for col in NUMERIC_COLUMNS:
        df[col] = df[col].map(_to_num).astype(float)
    df["trade_id"] = df["trade_id"].map(_s)
    df["seq"] = df.groupby(["trade_date", "code", "side", "price", "qty"], dropna=False).cumcount()
    df["row_hash"] = [
        hashlib.sha1("|".join([r.account, r.trade_date, r.trade_time, r.code, r.side,
                               fmt_num(r.price), fmt_num(r.qty), fmt_num(r.amount),
                               str(r.seq)]).encode("utf-8")).hexdigest()
        for r in df.itertuples(index=False)
    ]
    df["trade_id"] = [tid or f"h:{h[:16]}"
                      for tid, h in zip(df["trade_id"], df["row_hash"], strict=True)]
    df["source_kind"] = source_kind
    df["source_file"] = source_file
    df["ingested_at"] = ingested_at or datetime.now().isoformat(timespec="seconds")
    return df.loc[:, list(RAW_STORE_COLUMNS)]
```

- [ ] **Step 4: 跑,确认绿**

Run: `uv run --no-sync python -m pytest tests/broker/test_schema.py -q && uv run --no-sync ruff check autoresearch/broker tests/broker`
Expected: 10 passed;ruff 无报错(`date`/`field`/`re`/`record_degradation` 此时未用会被 F401 报 —— 先只 import Task 3 用到的再补,或本步就把 Task 3 的 `validate` 一起写上;推荐**先删未用 import,Task 3 再加回**)

- [ ] **Step 5: 提交**

```bash
git add autoresearch/broker/schema.py tests/broker/conftest.py tests/broker/test_schema.py
git commit -m "feat(broker): schema.normalize —— 补零/ts_code/side/数值化/同价分笔 seq/row_hash"
```

---

### Task 3: `schema.validate` —— A 级抛 / B 级记账 / warn

**Files:**
- Modify: `autoresearch/broker/schema.py`(追加 `ValidationReport` + `validate`)
- Create: `tests/broker/test_validate.py`

**Interfaces:**
- Produces: `ValidationReport(source_kind, source_file, rows, accounts: tuple[str,...], period: tuple[str,str] | None, b_degradations: dict[str,int], warnings: list[str])`;`validate(df, *, today: date | None = None) -> ValidationReport`(A 级 → raise `DataContractError`)

- [ ] **Step 1: 写失败测试** `tests/broker/test_validate.py`:

```python
"""schema.validate:A 级整文件拒收 / B 级记账 / 零股只 warn。"""
from __future__ import annotations

from datetime import date

import pytest

from autoresearch.broker import schema
from autoresearch.data import contracts
from autoresearch.data.contracts import DataContractError

TODAY = date(2026, 8, 27)


def _v(df_raw, **kw):
    df = schema.normalize(df_raw, source_kind="gtht", source_file="a.xlsx",
                          ingested_at="2026-08-27T10:00:00")
    return schema.validate(df, today=TODAY, **kw)


def test_empty_file_is_a_level(raw):
    with pytest.raises(DataContractError, match="0 行"):
        _v(raw().iloc[0:0])


def test_unknown_account_is_a_level(raw):
    with pytest.raises(DataContractError, match="账户"):
        _v(raw(account="xx"))


def test_future_or_garbage_date_is_a_level(raw):
    with pytest.raises(DataContractError, match="在未来"):
        _v(raw(trade_date="2099-01-01"))
    with pytest.raises(DataContractError, match="不可解析"):
        _v(raw(trade_date="昨天"))


def test_amount_identity_violation_is_a_level(raw):
    with pytest.raises(DataContractError, match="amount"):
        _v(raw(amount="1300"))          # 12.34×100 = 1234,差 66 元


def test_amount_tolerance_accepts_rounding(raw):
    rep = _v(raw(amount="1234.5"))      # 差 0.5 ≤ 1 元
    assert rep.rows == 1 and rep.accounts == ("gtht",)
    assert rep.period == ("2026-08-26", "2026-08-26")


def test_buy_without_code_or_qty_is_a_level(raw):
    with pytest.raises(DataContractError, match="code"):
        _v(raw(code=""))
    with pytest.raises(DataContractError, match="qty"):
        _v(raw(qty="0"))


def test_other_row_may_lack_code_and_price(raw):
    rep = _v(raw(code="", biz_type="利息归本", price="", qty="", amount="12.3"))
    assert rep.rows == 1 and rep.b_degradations == {}


def test_a_level_rejects_whole_file_even_if_one_row_bad(raw):
    with pytest.raises(DataContractError, match="第2行"):
        _v(raw.rows({}, {"amount": "9999"}))


def test_fee_missing_is_b_level_and_recorded(raw):
    contracts.clear_degradations()
    rep = _v(raw(commission="", other_fee="0"))
    assert rep.b_degradations == {"commission 缺": 1}
    recs = [d for d in contracts.degradations() if d["endpoint"] == "broker/gtht"]
    assert recs and recs[0]["key"] == "a.xlsx" and "commission 缺 ×1" in recs[0]["reasons"]


def test_net_amount_mismatch_is_b_level(raw):
    rep = _v(raw(net_amount="-1000", other_fee="0"))
    assert rep.b_degradations == {"net_amount 与 amount±费用 偏差>1元": 1}


def test_net_amount_consistent_sell(raw):
    rep = _v(raw(biz_type="证券卖出", price="12.5", qty="100", amount="1250",
                 commission="5", stamp_tax="0.63", transfer_fee="0.01", other_fee="0",
                 net_amount="1244.36"))
    assert rep.b_degradations == {}


def test_odd_lot_buy_is_warning_only(raw):
    rep = _v(raw(qty="150", amount="1851", other_fee="0", net_amount="-1856.01"))
    assert rep.warnings == ["BUY 非 100 股整数倍 ×1"] and rep.b_degradations == {}
```

- [ ] **Step 2: 跑,确认红**

Run: `uv run --no-sync python -m pytest tests/broker/test_validate.py -q`
Expected: FAIL `AttributeError: ... has no attribute 'validate'`

- [ ] **Step 3: 实现** —— 追加到 `autoresearch/broker/schema.py` 末尾(并确保顶部 import 有 `re`、`date`、`dataclass, field`、`record_degradation`):

```python
@dataclass
class ValidationReport:
    source_kind: str
    source_file: str
    rows: int
    accounts: tuple[str, ...] = ()
    period: tuple[str, str] | None = None
    b_degradations: dict[str, int] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)


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
    for reason, n in b.items():
        record_degradation(f"broker/{src}", f"{reason} ×{n}", key=fname)

    warnings: list[str] = []
    odd = trades[(trades["side"] == "BUY") & ((trades["qty"] % 100) != 0)]
    if len(odd):
        warnings.append(f"BUY 非 100 股整数倍 ×{len(odd)}")
    return ValidationReport(
        src, fname, len(df), tuple(sorted(set(df["account"]))),
        (str(df["trade_date"].min()), str(df["trade_date"].max())), b, warnings)
```

- [ ] **Step 4: 跑,确认绿**

Run: `uv run --no-sync python -m pytest tests/broker -q && uv run --no-sync ruff check autoresearch/broker tests/broker`
Expected: 22 passed;ruff 干净

- [ ] **Step 5: 提交**

```bash
git add autoresearch/broker/schema.py tests/broker/test_validate.py
git commit -m "feat(broker): schema.validate —— A 级整文件拒收 / B 级记账 / 零股 warn"
```

---

### Task 4: `store` —— raw 幂等 upsert + ingest_log

**Files:**
- Create: `autoresearch/broker/store.py`
- Create: `tests/broker/test_store.py`

**Interfaces:**
- Produces: `PRIORITY = {"gtht": 0, "tpy": 0, "chinaclear": 1, "screenshot": 2}`;`root_or_default(root) -> Path`;`raw_path(root, source_kind) -> Path`;`trades_path(root) -> Path`;`log_path(root) -> Path`;`sha256_of(path) -> str`;`read_raw(path) -> pd.DataFrame`;`upsert_raw(df, root=None) -> tuple[int, int]`(新增, 重复);`append_log(entry: dict, root=None) -> None`;`ingested_shas(root=None) -> set[str]`(只含 `status == "ok"`)
- Consumes: `schema.RAW_STORE_COLUMNS / NUMERIC_COLUMNS`、`ws.broker_root()`

- [ ] **Step 1: 写失败测试** `tests/broker/test_store.py`:

```python
"""store:raw/<src>.csv 按 row_hash 幂等、日志只认 ok 的哈希、根路径走 workspace。"""
from __future__ import annotations

from pathlib import Path

import pandas as pd

from autoresearch.broker import schema, store
from autoresearch.common import workspace as ws

AT = "2026-08-27T10:00:00"


def _norm(df, kind="gtht"):
    return schema.normalize(df, source_kind=kind, source_file="a.xlsx", ingested_at=AT)


def test_upsert_raw_is_idempotent_by_row_hash(tmp_path, raw):
    df = _norm(raw.rows({}, {"trade_time": "09:32:00"}))
    assert store.upsert_raw(df, tmp_path) == (2, 0)
    assert store.upsert_raw(df, tmp_path) == (0, 2)
    back = store.read_raw(store.raw_path(tmp_path, "gtht"))
    assert len(back) == 2 and list(back.columns) == list(schema.RAW_STORE_COLUMNS)
    assert back.price.dtype == float and back.seq.tolist() == [0, 1]   # seq 组键不含 trade_time
    assert pd.isna(back.other_fee).all()


def test_upsert_appends_only_new_rows(tmp_path, raw):
    store.upsert_raw(_norm(raw()), tmp_path)
    assert store.upsert_raw(_norm(raw.rows({}, {"price": "12.35", "amount": "1235"})), tmp_path) == (1, 1)
    assert len(store.read_raw(store.raw_path(tmp_path, "gtht"))) == 2


def test_raw_file_is_stable_when_nothing_new(tmp_path, raw):
    df = _norm(raw())
    store.upsert_raw(df, tmp_path)
    p = store.raw_path(tmp_path, "gtht")
    before = p.read_bytes()
    store.upsert_raw(df, tmp_path)
    assert p.read_bytes() == before


def test_log_roundtrip_only_ok_counts_as_ingested(tmp_path):
    store.append_log({"sha256": "abc", "status": "ok"}, tmp_path)
    store.append_log({"sha256": "def", "status": "rejected"}, tmp_path)
    assert store.ingested_shas(tmp_path) == {"abc"}
    assert store.ingested_shas(tmp_path / "nowhere") == set()


def test_sha256_of(tmp_path):
    p = tmp_path / "x.csv"
    p.write_bytes(b"abc")
    assert store.sha256_of(p) == "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad"


def test_roots_follow_workspace_engine(monkeypatch):
    monkeypatch.setattr(ws, "ENGINE", "codex")
    assert store.raw_path(None, "gtht") == Path("context_codex/broker/raw/gtht.csv")
    assert store.trades_path(None) == Path("context_codex/broker/trades.csv")
    assert store.log_path(None) == Path("context_codex/broker/ingest_log.jsonl")
```

- [ ] **Step 2: 跑,确认红**

Run: `uv run --no-sync python -m pytest tests/broker/test_store.py -q`
Expected: FAIL `ModuleNotFoundError: ... 'autoresearch.broker.store'`

- [ ] **Step 3: 实现 `autoresearch/broker/store.py`**(本任务不含 `merge`,Task 5 追加)

```python
#!/usr/bin/env python3
"""券商成交落盘 —— raw/<src>.csv 幂等 upsert、ingest_log、跨源合并重建 trades.csv。

design: docs/specs/2026-08-27-broker-trades-ingest-design.md §5/§8

两层幂等:文件级(sha256 在 ingest_log 且 status=ok → 跳过)、行级(row_hash upsert)。
`trades.csv` 不是追加出来的,是每次由 raw/* **全量确定性重建** —— 合并规则改了重跑即生效,
不会留下旧规则的残留行。
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pandas as pd

from autoresearch.broker import schema
from autoresearch.common import workspace as ws

#: 来源优先级(小 = 高):券商交割单 > 中国结算 > 截图;未知来源垫底
PRIORITY = {"gtht": 0, "tpy": 0, "chinaclear": 1, "screenshot": 2}
RAW_DIRNAME = "raw"
TRADES_CSV = "trades.csv"
LOG_NAME = "ingest_log.jsonl"


def root_or_default(root) -> Path:
    return Path(root) if root else ws.broker_root()


def raw_path(root, source_kind: str) -> Path:
    return root_or_default(root) / RAW_DIRNAME / f"{source_kind}.csv"


def trades_path(root) -> Path:
    return root_or_default(root) / TRADES_CSV


def log_path(root) -> Path:
    return root_or_default(root) / LOG_NAME


def sha256_of(path: Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def read_raw(path: Path) -> pd.DataFrame:
    """读 raw/<src>.csv:文本列保持字符串(空 → ""),数值列转 float(空 → NaN),seq → int。"""
    df = pd.read_csv(path, dtype=str, keep_default_na=False, encoding="utf-8")
    for col in schema.NUMERIC_COLUMNS:
        df[col] = pd.to_numeric(df[col], errors="coerce").astype(float)
    df["seq"] = pd.to_numeric(df["seq"], errors="coerce").fillna(0).astype(int)
    return df


def upsert_raw(df: pd.DataFrame, root=None) -> tuple[int, int]:
    """按 row_hash 追加新行;返回 (新增, 重复)。无新增时**不改写文件**(字节稳定)。"""
    kinds = set(df["source_kind"])
    if len(kinds) != 1:
        raise ValueError(f"一次 upsert 只能一个来源,收到 {sorted(kinds)}")
    path = raw_path(root, kinds.pop())
    existing = read_raw(path) if path.exists() else pd.DataFrame(columns=list(schema.RAW_STORE_COLUMNS))
    new = df.loc[~df["row_hash"].isin(set(existing["row_hash"])), list(schema.RAW_STORE_COLUMNS)]
    if len(new):
        path.parent.mkdir(parents=True, exist_ok=True)
        out = pd.concat([existing, new], ignore_index=True) if len(existing) else new
        out.to_csv(path, index=False, encoding="utf-8")
    return len(new), len(df) - len(new)


def append_log(entry: dict, root=None) -> None:
    path = log_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(entry, ensure_ascii=False) + "\n")


def ingested_shas(root=None) -> set[str]:
    path = log_path(root)
    if not path.exists():
        return set()
    out: set[str] = set()
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        rec = json.loads(line)
        if rec.get("status") == "ok" and rec.get("sha256"):
            out.add(rec["sha256"])
    return out
```

- [ ] **Step 4: 跑,确认绿**

Run: `uv run --no-sync python -m pytest tests/broker/test_store.py -q && uv run --no-sync ruff check autoresearch/broker tests/broker`
Expected: 6 passed

- [ ] **Step 5: 提交**

```bash
git add autoresearch/broker/store.py tests/broker/test_store.py
git commit -m "feat(broker): store —— raw/<src>.csv 按 row_hash 幂等 upsert + ingest_log"
```

---

### Task 5: `store.merge` —— 自然键按计数匹配 + 优先级补缺 → trades.csv

**Files:**
- Modify: `autoresearch/broker/store.py`(追加 `merge`)
- Create: `tests/broker/test_merge.py`

**Interfaces:**
- Produces: `merge(root=None) -> pd.DataFrame`(列 = `schema.TRADES_COLUMNS`,同时写 `trades.csv`;无 raw → 只有表头)
- Consumes: `schema.NATURAL_KEY / FILLABLE_COLUMNS / is_blank`、`PRIORITY`

- [ ] **Step 1: 写失败测试** `tests/broker/test_merge.py`:

```python
"""store.merge:同价分笔按计数匹配、优先级只补缺、单源直通、trades.csv 列与排序。"""
from __future__ import annotations

import pandas as pd

from autoresearch.broker import schema, store

AT = "2026-08-27T10:00:00"


def _put(tmp_path, df_raw, kind, file="f"):
    df = schema.normalize(df_raw, source_kind=kind, source_file=file, ingested_at=AT)
    store.upsert_raw(df, tmp_path)
    return df


def test_count_matching_two_fills_vs_one(tmp_path, raw):
    _put(tmp_path, raw.rows({}, {}), "gtht")                       # 同价两笔
    _put(tmp_path, raw(commission="", name=""), "chinaclear")      # 同键一笔
    t = store.merge(tmp_path)
    assert len(t) == 2
    assert sorted(t.sources) == ["gtht", "gtht+chinaclear"]
    assert list(t.columns) == list(schema.TRADES_COLUMNS)
    assert store.trades_path(tmp_path).exists()


def test_lower_priority_fills_blank_only(tmp_path, raw):
    g = _put(tmp_path, raw(commission=""), "gtht")
    _put(tmp_path, raw(commission="4.5", name="别名", trade_id="CC1"), "chinaclear")
    r = store.merge(tmp_path).iloc[0]
    assert r.commission == 4.5                 # 主源缺 → 补
    assert r["name"] == "平安银行"              # 主源有 → 不动(r.name 是 Series 索引标签,不能用)
    assert r.trade_id == g.iloc[0].trade_id    # trade_id 归主源
    assert r.sources == "gtht+chinaclear" and r.source_kind == "gtht"


def test_priority_screenshot_is_lowest(tmp_path, raw):
    _put(tmp_path, raw(), "screenshot")
    _put(tmp_path, raw(), "chinaclear")
    r = store.merge(tmp_path).iloc[0]
    assert r.source_kind == "chinaclear" and r.sources == "chinaclear+screenshot"


def test_single_source_rows_pass_through_sorted(tmp_path, raw):
    _put(tmp_path, raw.rows({"trade_date": "20260827", "biz_type": "证券卖出"},
                            {"trade_date": "20260826"}), "gtht")
    t = store.merge(tmp_path)
    assert t.trade_date.tolist() == ["2026-08-26", "2026-08-27"]
    assert set(t.sources) == {"gtht"}


def test_other_rows_without_code_survive_merge(tmp_path, raw):
    _put(tmp_path, raw(code="", biz_type="利息归本", price="", qty="", amount="1.5"), "gtht")
    t = store.merge(tmp_path)
    assert len(t) == 1 and t.iloc[0].side == "OTHER" and pd.isna(t.iloc[0].price)


def test_merge_without_raw_writes_header_only(tmp_path):
    t = store.merge(tmp_path)
    assert len(t) == 0 and list(t.columns) == list(schema.TRADES_COLUMNS)
    assert store.trades_path(tmp_path).read_text(encoding="utf-8").strip() == ",".join(schema.TRADES_COLUMNS)


def test_merge_is_rebuilt_not_appended(tmp_path, raw):
    _put(tmp_path, raw(), "gtht")
    store.merge(tmp_path)
    store.merge(tmp_path)
    assert len(store.merge(tmp_path)) == 1
```

- [ ] **Step 2: 跑,确认红**

Run: `uv run --no-sync python -m pytest tests/broker/test_merge.py -q`
Expected: FAIL `AttributeError: module ... has no attribute 'merge'`

- [ ] **Step 3: 实现** —— 追加到 `autoresearch/broker/store.py`:

```python
_SORT_KEYS = ["account", "trade_date", "trade_time", "code", "side", "trade_id"]


def merge(root=None) -> pd.DataFrame:
    """raw/* → trades.csv,全量确定性重建。

    自然键 `schema.NATURAL_KEY` 分组;组内各源按 seq 排序后**按位次配对**(multiset:
    gtht 2 笔 vs chinaclear 1 笔 → 2 行,第 1 行双源、第 2 行单源);位次上的主源 = 优先级最高者,
    其余源只对 `FILLABLE_COLUMNS` 补缺;`sources` = 参与源按优先级 `+` 连。
    """
    base = root_or_default(root)
    raw_dir = base / RAW_DIRNAME
    frames = [read_raw(p) for p in sorted(raw_dir.glob("*.csv"))] if raw_dir.exists() else []
    frames = [f for f in frames if len(f)]
    if not frames:
        trades = pd.DataFrame(columns=list(schema.TRADES_COLUMNS))
    else:
        allr = pd.concat(frames, ignore_index=True)
        rows: list[dict] = []
        for _key, g in allr.groupby(list(schema.NATURAL_KEY), dropna=False, sort=True):
            by_src = {s: sg.sort_values("seq", kind="stable").to_dict("records")
                      for s, sg in g.groupby("source_kind")}
            order = sorted(by_src, key=lambda s: (PRIORITY.get(s, 3), s))
            for i in range(max(len(v) for v in by_src.values())):
                present = [s for s in order if i < len(by_src[s])]
                primary = dict(by_src[present[0]][i])
                for s in present[1:]:
                    other = by_src[s][i]
                    for col in schema.FILLABLE_COLUMNS:
                        if schema.is_blank(primary.get(col)) and not schema.is_blank(other.get(col)):
                            primary[col] = other[col]
                primary["sources"] = "+".join(present)
                rows.append(primary)
        trades = (pd.DataFrame(rows).reindex(columns=list(schema.TRADES_COLUMNS))
                  .sort_values(_SORT_KEYS, kind="stable").reset_index(drop=True))
    path = trades_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    trades.to_csv(path, index=False, encoding="utf-8")
    return trades
```

- [ ] **Step 4: 跑,确认绿**

Run: `uv run --no-sync python -m pytest tests/broker -q && uv run --no-sync ruff check autoresearch/broker tests/broker`
Expected: 35 passed

- [ ] **Step 5: 提交**

```bash
git add autoresearch/broker/store.py tests/broker/test_merge.py
git commit -m "feat(broker): store.merge —— 自然键按计数匹配 + 来源优先级补缺,全量重建 trades.csv"
```

---

### Task 6: `sniff` + adapter 注册表

**Files:**
- Create: `autoresearch/broker/sniff.py`
- Modify: `autoresearch/broker/adapters/__init__.py`
- Create: `tests/broker/test_sniff.py`

**Interfaces:**
- Produces: `sniff.sniff(path) -> str`(`xlsx|xls|pdf|image|html|text`);`sniff.read_text(path) -> str`(utf-8-sig → gb18030,都失败 raise `ValueError`);`adapters.detect_source_kind(path) -> str | None`(父目录名 ∈ `SOURCE_KINDS`);`adapters.parse(path, source_kind, *, account=None) -> pd.DataFrame`(无 adapter → `ValueError`);`adapters.REGISTRY: dict[str, Callable]`
- 注意:本任务 `REGISTRY` 暂为空 dict,Task 7 注册 `screenshot`

- [ ] **Step 1: 写失败测试** `tests/broker/test_sniff.py`:

```python
"""sniff:不信扩展名只信 magic bytes;GBK/BOM 文本都能读;来源按父目录识别。"""
from __future__ import annotations

import pytest

from autoresearch.broker import adapters, sniff


def _w(tmp_path, name, data: bytes):
    p = tmp_path / name
    p.write_bytes(data)
    return p


def test_magic_bytes_beat_extension(tmp_path):
    assert sniff.sniff(_w(tmp_path, "a.xls", b"PK\x03\x04rest")) == "xlsx"
    assert sniff.sniff(_w(tmp_path, "b.txt", b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1x")) == "xls"
    assert sniff.sniff(_w(tmp_path, "c.xlsx", b"%PDF-1.4\n")) == "pdf"
    assert sniff.sniff(_w(tmp_path, "d.csv", b"\x89PNG\r\n")) == "image"
    assert sniff.sniff(_w(tmp_path, "e.csv", b"\xff\xd8\xff\xe0")) == "image"


def test_gbk_html_disguised_as_xls(tmp_path):
    p = _w(tmp_path, "交割单.xls", "<html><table><tr><td>成交日期</td></tr></table></html>".encode("gbk"))
    assert sniff.sniff(p) == "html"


def test_gbk_tab_text_reads_back(tmp_path):
    p = _w(tmp_path, "交割单.xls", "成交日期\t证券代码\n20260826\t000001\n".encode("gbk"))
    assert sniff.sniff(p) == "text"
    assert sniff.read_text(p).splitlines()[0] == "成交日期\t证券代码"


def test_utf8_bom_is_stripped(tmp_path):
    p = _w(tmp_path, "s.csv", "\ufefftrade_date,code\n".encode())
    assert sniff.read_text(p).startswith("trade_date")


def test_undecodable_raises(tmp_path):
    p = _w(tmp_path, "bin.csv", b"\xff\xfe\x00\x00\x81\x81\xff\xff")
    with pytest.raises(ValueError, match="gb18030"):
        sniff.read_text(p)


def test_detect_source_kind_by_parent_dir(tmp_path):
    for kind in ("chinaclear", "gtht", "tpy", "screenshot"):
        d = tmp_path / kind
        d.mkdir()
        assert adapters.detect_source_kind(d / "x.csv") == kind
    assert adapters.detect_source_kind(tmp_path / "misc" / "x.csv") is None


def test_parse_unknown_source_raises_listing_known(tmp_path):
    with pytest.raises(ValueError, match="尚无 adapter"):
        adapters.parse(tmp_path / "x", "chinaclear")
```

- [ ] **Step 2: 跑,确认红**

Run: `uv run --no-sync python -m pytest tests/broker/test_sniff.py -q`
Expected: FAIL `ImportError: cannot import name 'sniff'`

- [ ] **Step 3: 实现**

`autoresearch/broker/sniff.py`:
```python
#!/usr/bin/env python3
"""文件真身嗅探 —— 按 magic bytes + 编码试探定类型,**不信扩展名**。

design: docs/specs/2026-08-27-broker-trades-ingest-design.md §9

已知坑:券商「xls」常是 GBK 制表符文本或 HTML 表(通达信系客户端「输出」就是这么干的),
按扩展名走 xlrd 会直接炸。编码依次试 utf-8-sig → gb18030(GBK 超集)。
"""
from __future__ import annotations

from pathlib import Path

_ENCODINGS = ("utf-8-sig", "gb18030")
_HEAD = 2048


def _decode(raw: bytes, *, strict: bool = True) -> str:
    for enc in _ENCODINGS:
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    if strict:
        raise ValueError(f"无法按 {'/'.join(_ENCODINGS)} 解码(gb18030 也失败)")
    return raw.decode("utf-8", errors="replace")


def sniff(path: Path) -> str:
    """→ 'xlsx' | 'xls' | 'pdf' | 'image' | 'html' | 'text'。"""
    head = Path(path).read_bytes()[:_HEAD]
    if head.startswith(b"PK\x03\x04"):
        return "xlsx"
    if head.startswith(b"\xd0\xcf\x11\xe0"):
        return "xls"
    if head.startswith(b"%PDF"):
        return "pdf"
    if head.startswith((b"\x89PNG", b"\xff\xd8")):
        return "image"
    low = _decode(head, strict=False).lower()
    if "<html" in low or "<table" in low:
        return "html"
    return "text"


def read_text(path: Path) -> str:
    return _decode(Path(path).read_bytes())
```

`autoresearch/broker/adapters/__init__.py`(整文件替换):
```python
"""来源 adapter 注册表 —— `parse(path, source_kind)` 分发到各家解析器,产出 `schema.RAW_COLUMNS` 字符串帧。

design: docs/specs/2026-08-27-broker-trades-ingest-design.md §9

只有探针证实过格式的来源才有 adapter;没有的 → ValueError 明说(不静默跳过、不猜格式)。
"""
from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import pandas as pd

from autoresearch.broker.schema import SOURCE_KINDS

REGISTRY: dict[str, Callable[..., pd.DataFrame]] = {}


def detect_source_kind(path: Path) -> str | None:
    """按父目录名识别来源(inbox/<src>/文件);不在 `SOURCE_KINDS` → None。"""
    name = Path(path).parent.name
    return name if name in SOURCE_KINDS else None


def parse(path: Path, source_kind: str, *, account: str | None = None) -> pd.DataFrame:
    fn = REGISTRY.get(source_kind)
    if fn is None:
        raise ValueError(f"source_kind={source_kind!r} 尚无 adapter(已有 {sorted(REGISTRY)};"
                         "chinaclear/gtht/tpy 等探针证实格式后再建)")
    return fn(Path(path), account=account)
```

- [ ] **Step 4: 跑,确认绿**

Run: `uv run --no-sync python -m pytest tests/broker/test_sniff.py -q && uv run --no-sync ruff check autoresearch/broker tests/broker`
Expected: 8 passed

- [ ] **Step 5: 提交**

```bash
git add autoresearch/broker/sniff.py autoresearch/broker/adapters/__init__.py tests/broker/test_sniff.py
git commit -m "feat(broker): sniff(magic bytes + utf-8/gb18030)与 adapter 注册表"
```

---

### Task 7: `adapters/screenshot` —— 固定表头 CSV → RAW 帧

**Files:**
- Create: `autoresearch/broker/adapters/screenshot.py`
- Modify: `autoresearch/broker/adapters/__init__.py`(注册)
- Create: `tests/broker/test_screenshot.py`

**Interfaces:**
- Produces: `screenshot.SCREENSHOT_HEADER`(14 列,§9 逐字)、`screenshot.parse(path, *, account=None) -> pd.DataFrame`(列 = `schema.RAW_COLUMNS`,全字符串);账户来自 `account` 参数或文件名 `<account>_<YYYYMMDD>-<YYYYMMDD>.csv` 前缀
- 错误全部 `DataContractError`(真身非文本 / 表头不符 / 账户缺)

- [ ] **Step 1: 写失败测试** `tests/broker/test_screenshot.py`:

```python
"""截图 adapter:表头逐字锁死、账户来自参数或文件名、真身必须是文本。"""
from __future__ import annotations

import pytest

from autoresearch.broker import adapters, schema
from autoresearch.broker.adapters import screenshot
from autoresearch.data.contracts import DataContractError

HEADER = ",".join(screenshot.SCREENSHOT_HEADER)
ROW = "2026-08-25,09:31:05,000001,平安银行,证券买入,12.34,100,1234.00,5.00,0.00,0.01,0,-1239.01,100"


def _csv(tmp_path, name, text):
    p = tmp_path / name
    p.write_text(text, encoding="utf-8")
    return p


def test_header_is_verbatim_spec():
    assert screenshot.SCREENSHOT_HEADER == (
        "trade_date", "trade_time", "code", "name", "biz_type", "price", "qty", "amount",
        "commission", "stamp_tax", "transfer_fee", "other_fee", "net_amount", "balance_after")


def test_parse_account_from_filename(tmp_path):
    df = screenshot.parse(_csv(tmp_path, "gtht_20260825-20260826.csv", f"{HEADER}\n{ROW}\n"))
    assert list(df.columns) == list(schema.RAW_COLUMNS)
    r = df.iloc[0]
    assert r.account == "gtht" and r.code == "000001" and r.amount == "1234.00" and r.trade_id == ""


def test_account_param_overrides_filename(tmp_path):
    p = _csv(tmp_path, "gtht_20260825-20260826.csv", f"{HEADER}\n{ROW}\n")
    assert screenshot.parse(p, account="tpy").iloc[0].account == "tpy"


def test_missing_account_raises(tmp_path):
    with pytest.raises(DataContractError, match="--account"):
        screenshot.parse(_csv(tmp_path, "shot.csv", f"{HEADER}\n{ROW}\n"))


def test_wrong_header_raises(tmp_path):
    bad = HEADER.replace("qty", "quantity")
    with pytest.raises(DataContractError, match="表头"):
        screenshot.parse(_csv(tmp_path, "gtht_20260825-20260826.csv", f"{bad}\n{ROW}\n"))


def test_non_text_body_raises(tmp_path):
    p = tmp_path / "gtht_20260825-20260826.csv"
    p.write_bytes(b"PK\x03\x04junk")
    with pytest.raises(DataContractError, match="真身"):
        screenshot.parse(p)


def test_registered_in_adapters(tmp_path):
    p = _csv(tmp_path, "gtht_20260825-20260826.csv", f"{HEADER}\n{ROW}\n")
    assert len(adapters.parse(p, "screenshot")) == 1


def test_blank_cells_stay_empty_strings(tmp_path):
    row = ROW.replace(",5.00,0.00,0.01,0,", ",,,,,")
    df = screenshot.parse(_csv(tmp_path, "gtht_20260825-20260826.csv", f"{HEADER}\n{row}\n"))
    assert df.iloc[0].commission == "" and df.iloc[0].other_fee == ""
```

- [ ] **Step 2: 跑,确认红**

Run: `uv run --no-sync python -m pytest tests/broker/test_screenshot.py -q`
Expected: FAIL `ImportError: cannot import name 'screenshot'`

- [ ] **Step 3: 实现**

`autoresearch/broker/adapters/screenshot.py`:
```python
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
```

`autoresearch/broker/adapters/__init__.py`:在 `from autoresearch.broker.schema import SOURCE_KINDS` 之后加
```python
from autoresearch.broker.adapters import screenshot
```
并把 `REGISTRY` 改为
```python
REGISTRY: dict[str, Callable[..., pd.DataFrame]] = {"screenshot": screenshot.parse}
```

- [ ] **Step 4: 跑,确认绿**

Run: `uv run --no-sync python -m pytest tests/broker -q && uv run --no-sync ruff check autoresearch/broker tests/broker`
Expected: 50 passed(Task 6 的 `test_parse_unknown_source_raises_listing_known` 仍绿:chinaclear 仍无 adapter)

- [ ] **Step 5: 提交**

```bash
git add autoresearch/broker/adapters/screenshot.py autoresearch/broker/adapters/__init__.py tests/broker/test_screenshot.py
git commit -m "feat(broker): screenshot adapter —— 固定表头 CSV → RAW 帧,账户取参数或文件名"
```

---

### Task 8: `ingest` CLI —— 遍历 → 解析 → 契约 → 落盘 → 合并 → 摘要屏

**Files:**
- Create: `autoresearch/broker/ingest.py`
- Create: `tests/broker/test_ingest_cli.py`

**Interfaces:**
- Produces: `iter_files(paths) -> list[Path]`;`ingest_file(path, *, source_kind, account, root, force, dry_run, today=None, now=None) -> dict`(entry:`status ∈ ok|skipped|rejected|dry-run`);`render_summary(trades: pd.DataFrame | None, entries: list[dict]) -> str`;`main(argv=None) -> int`(0 全成功 / 1 有拒收 / 2 参数或来源错)
- Consumes: Task 2–7 全部

- [ ] **Step 1: 写失败测试** `tests/broker/test_ingest_cli.py`:

```python
"""ingest CLI:真跑一条截图路样本 —— 落盘/幂等/拒收不落盘/试跑不写/摘要屏/退出码。"""
from __future__ import annotations

import json

import pytest

from autoresearch.broker import ingest, store
from autoresearch.broker.adapters import screenshot

HEADER = ",".join(screenshot.SCREENSHOT_HEADER)
BUY = "2026-08-25,09:31:05,000001,平安银行,证券买入,12.34,100,1234.00,5.00,0.00,0.01,0,-1239.01,100"
SELL = "2026-08-26,14:55:10,000001,平安银行,证券卖出,12.50,100,1250.00,5.00,0.63,0.01,0,1244.36,0"


@pytest.fixture
def inbox(tmp_path):
    d = tmp_path / "inbox" / "screenshot"
    d.mkdir(parents=True)
    (d / "gtht_20260825-20260826.csv").write_text(f"{HEADER}\n{BUY}\n{SELL}\n", encoding="utf-8")
    return tmp_path / "inbox"


def _run(inbox, root, *extra):
    return ingest.main([str(inbox), "--root", str(root), *extra])


def test_ingest_writes_raw_trades_and_log(inbox, tmp_path, capsys):
    root = tmp_path / "root"
    assert _run(inbox, root) == 0
    trades = store.read_raw(store.raw_path(root, "screenshot"))
    assert len(trades) == 2 and store.trades_path(root).exists()
    log = [json.loads(x) for x in store.log_path(root).read_text(encoding="utf-8").splitlines()]
    assert len(log) == 1 and log[0]["status"] == "ok" and log[0]["new"] == 2
    assert "sha256" in log[0] and "period" in log[0] and "rows" in log[0]
    out = capsys.readouterr().out
    assert "gtht  2026-08-25..2026-08-26  BUY 1 / SELL 1 / OTHER 0" in out
    assert "成交额 2,484" in out and "费用 11" in out and "A违规 0" in out
    assert "trades.csv 重建:2 行" in out


def test_second_run_skips_and_is_byte_stable(inbox, tmp_path, capsys):
    root = tmp_path / "root"
    _run(inbox, root)
    before = store.trades_path(root).read_bytes()
    assert _run(inbox, root) == 0
    assert store.trades_path(root).read_bytes() == before
    assert "已导入跳过 1" in capsys.readouterr().out
    assert len(store.log_path(root).read_text(encoding="utf-8").splitlines()) == 1


def test_force_reparses_but_adds_nothing(inbox, tmp_path, capsys):
    root = tmp_path / "root"
    _run(inbox, root)
    before = store.trades_path(root).read_bytes()
    assert _run(inbox, root, "--force") == 0
    assert store.trades_path(root).read_bytes() == before
    log = [json.loads(x) for x in store.log_path(root).read_text(encoding="utf-8").splitlines()]
    assert log[-1]["new"] == 0 and log[-1]["dup"] == 2


def test_rejected_file_writes_nothing_but_log_and_exit_1(inbox, tmp_path, capsys):
    root = tmp_path / "root"
    bad = inbox / "screenshot" / "tpy_20260801-20260826.csv"
    bad.write_text(f"{HEADER}\n{BUY.replace('1234.00', '9999')}\n", encoding="utf-8")
    assert _run(inbox, root) == 1
    assert not store.raw_path(root, "tpy").exists()
    log = [json.loads(x) for x in store.log_path(root).read_text(encoding="utf-8").splitlines()]
    assert {e["status"] for e in log} == {"ok", "rejected"}
    assert "拒收" in capsys.readouterr().out
    assert len(store.read_raw(store.raw_path(root, "screenshot"))) == 2   # 好文件照常入


def test_dry_run_writes_nothing(inbox, tmp_path, capsys):
    root = tmp_path / "root"
    assert _run(inbox, root, "--dry-run") == 0
    assert not root.exists()
    assert "试跑" in capsys.readouterr().out


def test_unknown_source_dir_exits_2(tmp_path):
    d = tmp_path / "inbox" / "misc"
    d.mkdir(parents=True)
    (d / "x.csv").write_text("a\n", encoding="utf-8")
    assert ingest.main([str(d), "--root", str(tmp_path / "root")]) == 2


def test_source_without_adapter_exits_2(tmp_path):
    d = tmp_path / "inbox" / "chinaclear"
    d.mkdir(parents=True)
    (d / "x.pdf").write_bytes(b"%PDF-1.4")
    assert ingest.main([str(d), "--root", str(tmp_path / "root")]) == 2


def test_iter_files_skips_hidden_and_recurses(tmp_path):
    (tmp_path / "a").mkdir()
    (tmp_path / "a" / ".DS_Store").write_bytes(b"")
    (tmp_path / "a" / "x.csv").write_text("x", encoding="utf-8")
    (tmp_path / "a" / "b").mkdir()
    (tmp_path / "a" / "b" / "y.csv").write_text("y", encoding="utf-8")
    assert {p.name for p in ingest.iter_files([tmp_path / "a"])} == {"x.csv", "y.csv"}
    with pytest.raises(FileNotFoundError):
        ingest.iter_files([tmp_path / "nope"])


def test_help_works():
    with pytest.raises(SystemExit) as e:
        ingest.main(["--help"])
    assert e.value.code == 0
```

- [ ] **Step 2: 跑,确认红**

Run: `uv run --no-sync python -m pytest tests/broker/test_ingest_cli.py -q`
Expected: FAIL `ModuleNotFoundError: ... 'autoresearch.broker.ingest'`

- [ ] **Step 3: 实现 `autoresearch/broker/ingest.py`**

```python
#!/usr/bin/env python3
"""券商成交导入 CLI —— 文件 → adapter → 归一 → 两级契约 → raw 幂等落盘 → 重建 trades.csv → 摘要屏。

design: docs/specs/2026-08-27-broker-trades-ingest-design.md §11

    uv run --no-sync python -m autoresearch.broker.ingest <文件|目录>... [--account tpy|gtht]
        [--source chinaclear|gtht|tpy|screenshot] [--force] [--dry-run] [--root DIR]

来源缺省按父目录名识别(inbox/<src>/…)。A 级违约的文件**整份拒收、不落 raw**,但记进 ingest_log
(status=rejected)并继续处理其余文件;退出码 1。`DataContractError` 只在这里被按文件捕获,
从不静默:打印 + 日志 + 非零退出。
"""
from __future__ import annotations

import argparse
import sys
from datetime import date, datetime
from pathlib import Path

import pandas as pd

from autoresearch.broker import adapters, schema, store
from autoresearch.data.contracts import DataContractError

_SKIP_NAMES = {".DS_Store"}


def iter_files(paths) -> list[Path]:
    out: list[Path] = []
    for p in paths:
        p = Path(p)
        if p.is_dir():
            out.extend(q for q in sorted(p.rglob("*"))
                       if q.is_file() and not q.name.startswith(".") and q.name not in _SKIP_NAMES)
        elif p.is_file():
            out.append(p)
        else:
            raise FileNotFoundError(f"{p} 不存在")
    return out


def ingest_file(path: Path, *, source_kind: str, account: str | None, root, force: bool,
                dry_run: bool, today: date | None = None, now: str | None = None) -> dict:
    now = now or datetime.now().isoformat(timespec="seconds")
    sha = store.sha256_of(path)
    entry: dict = {"at": now, "file": path.name, "sha256": sha, "source_kind": source_kind,
                   "status": "ok"}
    if not force and sha in store.ingested_shas(root):
        entry["status"] = "skipped"
        return entry
    try:
        raw = adapters.parse(path, source_kind, account=account)
        df = schema.normalize(raw, source_kind=source_kind, source_file=path.name, ingested_at=now)
        rep = schema.validate(df, today=today)
    except DataContractError as e:
        entry.update(status="rejected", a_error=str(e))
        print(f"[broker] ✗ 拒收 {path.name}:{e}", file=sys.stderr)
        if not dry_run:
            store.append_log(entry, root)
        return entry
    entry.update(accounts=list(rep.accounts), period=list(rep.period or ()), rows=rep.rows,
                 b_degradations=rep.b_degradations, warnings=rep.warnings)
    if dry_run:
        entry["status"] = "dry-run"
        return entry
    n_new, n_dup = store.upsert_raw(df, root)
    entry.update(new=n_new, dup=n_dup)
    store.append_log(entry, root)
    return entry


def _fmt_money(v: float) -> str:
    return f"{v:,.0f}"


def render_summary(trades: pd.DataFrame | None, entries: list[dict]) -> str:
    n = {s: sum(1 for e in entries if e["status"] == s) for s in ("ok", "skipped", "rejected", "dry-run")}
    lines = [f"[broker] 文件 {len(entries)} · 新导入 {n['ok']} · 已导入跳过 {n['skipped']}"
             f" · 拒收 {n['rejected']} · 试跑 {n['dry-run']}"]
    for e in entries:
        if e["status"] == "rejected":
            lines.append(f"  ✗ 拒收 {e['file']}:{str(e.get('a_error', '')).splitlines()[0]}")
        elif e["status"] == "dry-run":
            lines.append(f"  ○ 试跑 {e['file']}:{e.get('rows', 0)} 行 · 账户 {'/'.join(e.get('accounts', []))}"
                         f" · B降级 {e.get('b_degradations') or '无'}(未写盘)")
    if trades is None:
        return "\n".join(lines)
    degr: dict[str, dict[str, int]] = {}
    for e in entries:
        if e["status"] == "ok":
            for acct in e.get("accounts", []):
                for reason, k in (e.get("b_degradations") or {}).items():
                    degr.setdefault(acct, {})[reason] = degr.get(acct, {}).get(reason, 0) + k
    for acct, g in trades.groupby("account", sort=True):
        is_trade = g["side"].isin(("BUY", "SELL"))
        counts = {s: int((g["side"] == s).sum()) for s in schema.SIDES}
        turnover = float(g.loc[is_trade, "amount"].fillna(0).sum())
        fees = float(g.loc[is_trade, list(schema.FEE_COLUMNS)].fillna(0).sum().sum())
        d = degr.get(acct, {})
        dtxt = f"{sum(d.values())}(" + ", ".join(f"{r} ×{k}" for r, k in d.items()) + ")" if d else "0"
        lines.append(f"  {acct:<5} {g['trade_date'].min()}..{g['trade_date'].max()}  "
                     f"BUY {counts['BUY']} / SELL {counts['SELL']} / OTHER {counts['OTHER']}   "
                     f"成交额 {_fmt_money(turnover)}  费用 {_fmt_money(fees)}  A违规 0  B降级 {dtxt}")
    multi = int(trades["sources"].astype(str).str.contains(r"\+").sum()) if len(trades) else 0
    lines.append(f"trades.csv 重建:{len(trades)} 行 · 多源匹配 {multi} 行 · 单源 {len(trades) - multi} 行")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="券商成交导入(确定性、零 LLM;设计稿 2026-08-27)")
    ap.add_argument("paths", nargs="+", help="文件或目录(目录递归;来源按父目录名 inbox/<src>/ 识别)")
    ap.add_argument("--account", choices=schema.ACCOUNTS, help="券商源/截图源的账户别名")
    ap.add_argument("--source", choices=schema.SOURCE_KINDS, help="强制指定来源类型")
    ap.add_argument("--force", action="store_true", help="已导入过的文件也重新解析(仍按 row_hash 去重)")
    ap.add_argument("--dry-run", action="store_true", help="只解析+契约,不写任何文件")
    ap.add_argument("--root", help="产物根(缺省 workspace.broker_root())")
    args = ap.parse_args(argv)
    try:
        files = iter_files(args.paths)
    except FileNotFoundError as e:
        print(f"[broker] {e}", file=sys.stderr)
        return 2
    entries: list[dict] = []
    for f in files:
        kind = args.source or adapters.detect_source_kind(f)
        if kind is None:
            print(f"[broker] {f}:无法识别来源(父目录须为 {'/'.join(schema.SOURCE_KINDS)} 之一,"
                  "或传 --source)", file=sys.stderr)
            return 2
        try:
            entries.append(ingest_file(f, source_kind=kind, account=args.account, root=args.root,
                                       force=args.force, dry_run=args.dry_run))
        except ValueError as e:
            print(f"[broker] {f}:{e}", file=sys.stderr)
            return 2
    trades = None if args.dry_run else store.merge(args.root)
    print(render_summary(trades, entries))
    return 1 if any(e["status"] == "rejected" for e in entries) else 0


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 4: 跑,确认绿**

Run: `uv run --no-sync python -m pytest tests/broker -q && uv run --no-sync ruff check autoresearch/broker tests/broker && uv run --no-sync python -m autoresearch.broker.ingest --help`
Expected: 59 passed;`--help` 打印用法退出 0

- [ ] **Step 5: 提交**

```bash
git add autoresearch/broker/ingest.py tests/broker/test_ingest_cli.py
git commit -m "feat(broker): ingest CLI —— 遍历/解析/契约/幂等落盘/重建/摘要屏,拒收不落盘退出 1"
```

---

### Task 9: `reconcile` CLI —— 跨源三桶核对(只报不裁)

**Files:**
- Create: `autoresearch/broker/reconcile.py`
- Create: `tests/broker/test_reconcile.py`

**Interfaces:**
- Produces: `compare(a: pd.DataFrame, b: pd.DataFrame) -> dict`(`both/only_a/only_b/amount_a/amount_b/only_a_keys/only_b_keys`);`report(root=None, *, account=None, since=None) -> str`;`main(argv=None) -> int`(恒 0)
- Consumes: `store.read_raw / raw_path / root_or_default / RAW_DIRNAME`、`schema.fmt_num`

- [ ] **Step 1: 写失败测试** `tests/broker/test_reconcile.py`:

```python
"""reconcile:重叠期间三桶计数 + 金额差;无重叠/单源明说;只报不裁(退出码恒 0)。"""
from __future__ import annotations

from autoresearch.broker import reconcile, schema, store

AT = "2026-08-27T10:00:00"


def _put(tmp_path, df_raw, kind):
    store.upsert_raw(schema.normalize(df_raw, source_kind=kind, source_file=kind, ingested_at=AT), tmp_path)


def test_three_buckets_and_amount_diff(tmp_path, raw):
    _put(tmp_path, raw.rows({}, {}, {"trade_date": "20260827", "price": "13", "amount": "1300"}), "gtht")
    _put(tmp_path, raw.rows({}, {"trade_date": "20260827", "price": "13.5", "amount": "1350"}), "chinaclear")
    out = reconcile.report(tmp_path)
    assert "gtht chinaclear↔gtht 2026-08-26..2026-08-27" in out
    assert "两边都有 1" in out and "仅 chinaclear 1" in out and "仅 gtht 2" in out
    assert "成交额 2,584 vs 3,768" in out


def test_no_overlap_is_said_not_hidden(tmp_path, raw):
    _put(tmp_path, raw(trade_date="20260801"), "gtht")
    _put(tmp_path, raw(trade_date="20260826"), "chinaclear")
    assert "无重叠期间" in reconcile.report(tmp_path)


def test_single_source_account_is_said(tmp_path, raw):
    _put(tmp_path, raw(), "gtht")
    _put(tmp_path, raw(account="tpy"), "screenshot")
    out = reconcile.report(tmp_path)
    assert "gtht:只有 ['gtht'] 一个来源" in out and "tpy:只有 ['screenshot'] 一个来源" in out


def test_since_and_account_filters(tmp_path, raw):
    _put(tmp_path, raw.rows({"trade_date": "20260801"}, {}), "gtht")
    _put(tmp_path, raw.rows({"trade_date": "20260801"}, {}), "chinaclear")
    out = reconcile.report(tmp_path, account="gtht", since="2026-08-20")
    assert "2026-08-26..2026-08-26" in out and "两边都有 1" in out


def test_main_prints_and_returns_zero(tmp_path, raw, capsys):
    _put(tmp_path, raw(), "gtht")
    assert reconcile.main(["--root", str(tmp_path)]) == 0
    assert "只报不裁" in capsys.readouterr().out


def test_empty_root(tmp_path):
    assert "无 raw" in reconcile.report(tmp_path / "nothing")
```

- [ ] **Step 2: 跑,确认红**

Run: `uv run --no-sync python -m pytest tests/broker/test_reconcile.py -q`
Expected: FAIL `ImportError: cannot import name 'reconcile'`

- [ ] **Step 3: 实现 `autoresearch/broker/reconcile.py`**

```python
#!/usr/bin/env python3
"""跨源核对 CLI —— 同账户、两来源共同覆盖期间内,按自然键三桶对账;**只报不裁**。

design: docs/specs/2026-08-27-broker-trades-ingest-design.md §8

这是「中国结算主干内容未证实」的长期保险丝:每期都跑,一致率掉了立刻可见。有差异不自动裁决,
列出来给人看(哪一边多了什么)。

    uv run --no-sync python -m autoresearch.broker.reconcile [--account tpy|gtht] [--since YYYY-MM-DD] [--root DIR]
"""
from __future__ import annotations

import argparse
import sys
from collections import Counter
from itertools import combinations

import pandas as pd

from autoresearch.broker import schema, store

_MAX_KEYS = 20


def _keys(df: pd.DataFrame) -> Counter:
    return Counter((r.trade_date, r.code, r.side, schema.fmt_num(r.price), schema.fmt_num(r.qty))
                   for r in df.itertuples(index=False))


def _turnover(df: pd.DataFrame) -> float:
    return float(df.loc[df["side"].isin(("BUY", "SELL")), "amount"].fillna(0).sum())


def compare(a: pd.DataFrame, b: pd.DataFrame) -> dict:
    ka, kb = _keys(a), _keys(b)
    return {
        "both": sum((ka & kb).values()),
        "only_a": sum((ka - kb).values()),
        "only_b": sum((kb - ka).values()),
        "amount_a": _turnover(a),
        "amount_b": _turnover(b),
        "only_a_keys": sorted((ka - kb).elements())[:_MAX_KEYS],
        "only_b_keys": sorted((kb - ka).elements())[:_MAX_KEYS],
    }


def report(root=None, *, account: str | None = None, since: str | None = None) -> str:
    raw_dir = store.root_or_default(root) / store.RAW_DIRNAME
    frames = {p.stem: store.read_raw(p) for p in sorted(raw_dir.glob("*.csv"))} if raw_dir.exists() else {}
    frames = {s: f for s, f in frames.items() if len(f)}
    lines = ["[broker·reconcile] 只报不裁"]
    if not frames:
        lines.append("  无 raw 数据")
        return "\n".join(lines)
    if since:
        frames = {s: f[f["trade_date"] >= since] for s, f in frames.items()}
    accounts = [account] if account else sorted({a for f in frames.values() for a in f["account"]})
    for acct in accounts:
        per = {s: f[f["account"] == acct] for s, f in frames.items()}
        per = {s: f for s, f in per.items() if len(f)}
        if len(per) < 2:
            lines.append(f"  {acct}:只有 {sorted(per)} 一个来源,无从核对")
            continue
        for sa, sb in combinations(sorted(per), 2):
            lo = max(per[sa]["trade_date"].min(), per[sb]["trade_date"].min())
            hi = min(per[sa]["trade_date"].max(), per[sb]["trade_date"].max())
            if lo > hi:
                lines.append(f"  {acct} {sa}↔{sb}:无重叠期间")
                continue
            a = per[sa][(per[sa]["trade_date"] >= lo) & (per[sa]["trade_date"] <= hi)]
            b = per[sb][(per[sb]["trade_date"] >= lo) & (per[sb]["trade_date"] <= hi)]
            r = compare(a, b)
            lines.append(f"  {acct} {sa}↔{sb} {lo}..{hi}:两边都有 {r['both']} · 仅 {sa} {r['only_a']}"
                         f" · 仅 {sb} {r['only_b']} · 成交额 {r['amount_a']:,.0f} vs {r['amount_b']:,.0f}"
                         f"(差 {r['amount_a'] - r['amount_b']:,.0f})")
            for k in r["only_a_keys"]:
                lines.append(f"      仅 {sa}:{' '.join(k)}")
            for k in r["only_b_keys"]:
                lines.append(f"      仅 {sb}:{' '.join(k)}")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="券商成交跨源核对(只报不裁;设计稿 2026-08-27 §8)")
    ap.add_argument("--account", choices=schema.ACCOUNTS)
    ap.add_argument("--since", help="只看该日(含)之后")
    ap.add_argument("--root", help="产物根(缺省 workspace.broker_root())")
    args = ap.parse_args(argv)
    print(report(args.root, account=args.account, since=args.since))
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 4: 跑,确认绿**

Run: `uv run --no-sync python -m pytest tests/broker -q && uv run --no-sync ruff check autoresearch/broker tests/broker && uv run --no-sync python -m autoresearch.broker.reconcile --help`
Expected: 65 passed

- [ ] **Step 5: 提交**

```bash
git add autoresearch/broker/reconcile.py tests/broker/test_reconcile.py
git commit -m "feat(broker): reconcile CLI —— 重叠期间三桶核对 + 成交额差,只报不裁"
```

---

### Task 10: 文档 + 全仓验证

**Files:**
- Modify: `CLAUDE.md`(「包结构」节加一行)
- Modify: `docs/specs/2026-08-27-broker-trades-ingest-design.md`(状态行)

- [ ] **Step 1: 全仓测试 + lint**

Run: `uv run --no-sync python -m pytest -q -x -p no:cacheprovider 2>&1 | tail -5 && uv run --no-sync ruff check autoresearch tests`
Expected: 全绿(基线 2974 + 本波 ≈66);`tests/test_cli_entrypoints.py` 对 `broker.ingest`/`broker.reconcile` 的 `--help` 通过;`tests/common/test_workspace.py` 裸根探针通过

- [ ] **Step 2: CLAUDE.md** —— 在「包结构」节 `autoresearch/scan、autoresearch/analyze、autoresearch/macro` 那条之后加:

```markdown
- `autoresearch/broker` —— 券商成交取数层(2026-08-27 设计稿):手机 App 导出的交割单/对账单/截图表 → 标准化成交表 `context_<engine>/broker/trades.csv`(零 LLM;A/B 两级契约;幂等;`ingest`/`reconcile` 两个 CLI)。只记不学,不进 lake/。券商格式 adapter 等真样本探针后再建。
```

- [ ] **Step 3: 设计稿状态行** —— 把第 3 行 `状态:**设计待评审 → 探针 → 实施计划**` 改为 `状态:**格式无关核心已实施(见 docs/plans/2026-08-27-broker-trades-ingest-plan.md)→ 待探针建券商 adapter**`

- [ ] **Step 4: 提交**

```bash
git add CLAUDE.md docs/specs/2026-08-27-broker-trades-ingest-design.md docs/plans/2026-08-27-broker-trades-ingest-plan.md
git commit -m "docs(broker): CLAUDE.md 包结构 + 设计稿状态 + 实施计划归档"
```

---

## 计划自检(已做)

- **Spec 覆盖**:§5 架构(T1/T2/T4/T6/T8/T9)· §6 列(T2)· §7 契约(T3)· §8 幂等合并核对(T4/T5/T9)· §9 嗅探/依赖/截图契约(T6/T7;PDF 密码逻辑随 PDF adapter 一起在探针后做,本计划不含加密 PDF)· §11 CLI 与摘要屏(T8)· §12 测试(逐条有对应用例)· §13 隐私(夹具全合成)· §14 验收 ①②④⑤(③ 需两源真样本)。
- **占位扫描**:无 TBD/TODO;每步有代码。
- **类型一致性**:`normalize(df_raw, *, source_kind, source_file, ingested_at=None)`、`validate(df, *, today=None)`、`upsert_raw(df, root=None) -> (int, int)`、`merge(root=None)`、`parse(path, source_kind, *, account=None)`、`screenshot.parse(path, *, account=None)`、`ingest_file(path, *, source_kind, account, root, force, dry_run, today=None, now=None)`、`report(root=None, *, account=None, since=None)` 在各任务间一致。
- **未做(明示)**:chinaclear/gtht/tpy adapter、加密 PDF、`accounts.jsonc` 归户(它只被中国结算 adapter 消费,随其一起建)。

## 执行记录(2026-08-27,inline 执行)

按任务顺序落 9 个 commit(worktree `.worktrees/broker-ingest`,分支 `feature/broker-ingest`)。**逮到的全是计划自带的
测试夹具缺陷,实现零改**(与 memory「plan 缺陷多于实施手滑」一致),已在上文原地修正:
① `test_fee_missing` 夹具默认 `other_fee=""` 多记一条降级;② 零股用例没同步 `net_amount`;③ `seq` 组键不含 `trade_time`,
期望应为 `[0, 1]`;④ `r.name` 撞 pandas `Series.name`;⑤ ruff UP012;⑥ `iter_files` 断言改集合比较。
另一个流程坑:`pytest | tail` 吞退出码导致一次带红提交(已 amend)——之后一律 `> file; rc=$?` 再判。
基线:全仓 1998 绿 + 1 条环境依赖红(`test_production_path_guard::…real_production_files` 断言主 checkout 的真实产物存在,
worktree 无 `reports_claude/` 必红,主 checkout 绿),与本波无关。

### 复核轮(reviewer 独立复核 a71894f..6869cf6,17 个对抗探针)

结论「With fixes」。1 Critical + 7 Important + 若干 Minor,**全部已修**并各配用例(65 → 90 绿):
- Critical:`--account` 覆盖文件名账户 → 另一户成交被改名后 row_hash 去重吞掉(静默丢数据)。修:冲突拒收。
- Important:坏文件(空/解析失败)是 `ValueError` 子类,被 CLI 当「无 adapter」中止整批且 `trades.csv` 不重建、状态粘住
  → 修:adapter 包成 `DataContractError`,整批先定来源再动文件,merge/摘要必跑;OTHER 行跨源按 (date, code) 并成一笔吞现金流
  → 修:`natural_key` 对 OTHER 加 amount(规格缺陷);日志记逐笔明细违反 §13 → 修:首行 + 条数;merge 用原始浮点、reconcile 用
  4 位小数,对「同一笔」定义不一致 → 修:共享 `natural_key`;摘要屏把一份文件的 B 降级算到每个账户 + 硬编码 `A违规 0`
  → 修:`validate` 按账户拆、去掉硬编码;变异逃逸(相对容差归零/后缀剥离/三项 B 级/反向计数/混源 upsert/截断/`--source`
  `--account` 透传/账户名进消息)→ 全部补用例;reconcile 把 OTHER 行算进桶造永久噪音 → 修:只计 BUY/SELL,OTHER 另起一行,
  窗口口径在首行明写。
- Minor 已修:空 `a_error` 的 `[0]` 越界、`ingested_at` 取最早、seq 组键加 account 且与行序无关、`--since` 校验、数值不可解析
  = A 级而非「缺」、全角数字 NFKC、文件名账户大小写不敏感、sniff 宽松兜底改 gb18030、主源哈希 id 让位真成交编号。
- 未做(记账):adapter 声明导出覆盖期(reconcile 窗口现按成交日交集推定,首行明写);待 chinaclear/gtht/tpy adapter 时一并设计。
