#!/usr/bin/env python3
"""TraceStore —— typed 现场存储(每段产物 parquet + run manifest)。

design: docs/specs/2026-06-22-autoresearch-arch-redesign-design.md §D。

布局(run-folder 与 analysis_date 解耦,镜像 screen_market 的 run≠交易日):

  <root>/<run_id>/
    manifest.json          # analysis_date · config · 各段 status+rows · generated_at
    stages/<stage>.parquet # 每段 typed 结果(表格;ZSTD)

- `run_id` = 运行时刻(<YYYYMMDD>_<HHMM>);`analysis_date` 落 manifest(同一交易日可多次重跑)。
- `put_df` 写前过 `schema.coerce`(缺 required 列 warn+补位),不强转 dtype、不删多余列。
- manifest 增量更新:put_df 自动登记该段 {status:"done", rows, generated_at};put_meta 合并顶层字段。
"""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

from autoresearch.trace import schema

DEFAULT_ROOT = Path("reports/scan")
_COMPRESSION = "zstd"


def new_run_id(today: datetime | None = None) -> str:
    """生成 run_id = <YYYYMMDD>_<HHMM>(运行时刻;与 analysis_date 解耦)。"""
    t = today or datetime.now()
    return t.strftime("%Y%m%d_%H%M")


class TraceStore:
    """run 级 typed 产物存储(parquet 段产物 + manifest.json)。"""

    def __init__(self, root: str | Path = DEFAULT_ROOT):
        self.root = Path(root)

    # ── 路径 ──
    def run_dir(self, run_id: str) -> Path:
        return self.root / run_id

    def stages_dir(self, run_id: str) -> Path:
        return self.run_dir(run_id) / "stages"

    def stage_path(self, run_id: str, stage_name: str) -> Path:
        return self.stages_dir(run_id) / f"{stage_name}.parquet"

    def manifest_path(self, run_id: str) -> Path:
        return self.run_dir(run_id) / "manifest.json"

    # ── 段产物 ──
    def put_df(self, run_id: str, stage_name: str, df: pd.DataFrame) -> Path:
        """把一段结果写 stages/<stage>.parquet(过 schema.coerce),并登记进 manifest。"""
        out = schema.coerce(df, stage_name)
        path = self.stage_path(run_id, stage_name)
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(path.suffix + ".tmp")
        pq.write_table(pa.Table.from_pandas(out, preserve_index=False), tmp, compression=_COMPRESSION)
        tmp.replace(path)
        self._record_stage(run_id, stage_name, status="done", rows=int(len(out)))
        return path

    def get_df(self, run_id: str, stage_name: str) -> pd.DataFrame:
        """读回某段产物;文件不存在 → FileNotFoundError(调用方据 has_stage 先判)。"""
        path = self.stage_path(run_id, stage_name)
        if not path.exists():
            raise FileNotFoundError(f"trace stage not found: {path}")
        return pq.read_table(path).to_pandas()

    def has_stage(self, run_id: str, stage_name: str) -> bool:
        """该段产物是否已物化(断点续跑判定的一半;另一半看 manifest status=done)。"""
        return self.stage_path(run_id, stage_name).exists()

    # ── manifest ──
    def get_meta(self, run_id: str) -> dict:
        """读 manifest.json;不存在 → 空骨架(stages={})。"""
        p = self.manifest_path(run_id)
        if not p.exists():
            return {"run_id": run_id, "stages": {}}
        return json.loads(p.read_text(encoding="utf-8"))

    def put_meta(self, run_id: str, meta: dict) -> Path:
        """把 meta 顶层字段合并进 manifest(深合并 stages 子表),写回。"""
        cur = self.get_meta(run_id)
        stages = {**cur.get("stages", {}), **meta.get("stages", {})}
        merged = {**cur, **meta, "stages": stages, "run_id": run_id}
        merged.setdefault("generated_at", datetime.now().isoformat(timespec="seconds"))
        return self._write_meta(run_id, merged)

    def stage_done(self, run_id: str, stage_name: str) -> bool:
        """manifest 里该段 status 是否 == "done"(配合 has_stage 做续跑跳过判定)。"""
        return self.get_meta(run_id).get("stages", {}).get(stage_name, {}).get("status") == "done"

    # ── 内部 ──
    def _record_stage(self, run_id: str, stage_name: str, *, status: str, rows: int) -> None:
        meta = self.get_meta(run_id)
        meta.setdefault("stages", {})[stage_name] = {
            "status": status, "rows": rows,
            "generated_at": datetime.now().isoformat(timespec="seconds"),
        }
        self._write_meta(run_id, meta)

    def _write_meta(self, run_id: str, meta: dict) -> Path:
        p = self.manifest_path(run_id)
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(p)
        return p
