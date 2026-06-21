"""一次性迁移:factor_lab 的 pkl 缓存 → parquet 数据湖(值一致、幂等)。

design: docs/specs/2026-06-22-autoresearch-arch-redesign-design.md §B / 计划 Task 2.3。

现状:`context/factor_lab/cache/<endpoint>/<day>.pkl`(`_cache` 落盘,84 天历史)。
迁移:逐个 `read_pickle → to_parquet(zstd)` 写进 `context/lake/<endpoint>/<key>.parquet`,
**值/dtype/行数一致**(pandas round-trip)。文件名即键(`<day>.pkl`→`<day>.parquet`,
`static.pkl`→`static.parquet`),无需经 policy 重推——pkl 已按取数日/static 命名。

幂等:目标 parquet 已存在 → 跳过(不重写)。返回新写入的文件数。

用法(代码调用,不在本 phase 跑真迁移):
  from autoresearch.data.migrate_cache import migrate
  migrate()   # 默认 context/factor_lab/cache → context/lake
"""
from __future__ import annotations

from pathlib import Path

import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

_DEFAULT_CACHE = Path("context/factor_lab/cache")
_DEFAULT_LAKE = Path("context/lake")
_COMPRESSION = "zstd"


def migrate(cache_root: Path | None = None, lake_root: Path | None = None) -> int:
    """把 cache_root 下所有 <endpoint>/<key>.pkl 迁成 lake_root 下的 .parquet。

    幂等:目标存在则跳过。返回**本次新写入**的文件数。
    """
    cache_root = Path(cache_root) if cache_root else _DEFAULT_CACHE
    lake_root = Path(lake_root) if lake_root else _DEFAULT_LAKE

    if not cache_root.exists():
        print(f"[migrate] cache root 不存在,跳过: {cache_root}")
        return 0

    written = 0
    skipped = 0
    for pkl in sorted(cache_root.rglob("*.pkl")):
        endpoint = pkl.parent.name
        target = lake_root / endpoint / f"{pkl.stem}.parquet"
        if target.exists():
            skipped += 1
            continue
        df = pd.read_pickle(pkl)
        if df is None:
            df = pd.DataFrame()
        target.parent.mkdir(parents=True, exist_ok=True)
        table = pa.Table.from_pandas(df, preserve_index=False)
        pq.write_table(table, target, compression=_COMPRESSION)
        written += 1

    print(f"[migrate] {cache_root} → {lake_root}: 新写 {written} · 跳过(已存在) {skipped}")
    return written


if __name__ == "__main__":
    migrate()
