#!/usr/bin/env python3
"""现场留存 —— 让一次已发布的 run 自带「它当时看到的东西」(确定性、零 LLM、零网络)。

design: docs/specs/2026-08-26-scene-retention-and-buy-owner-design.md §4

## 病灶(2026-08-26 审计,`docs/research/2026-08-26-retention-audit.md`)

发布层原先只搬 **9 项白名单 + 3 个前缀**(`publisher._publish_pipeline` 的 `mapping` 与
`_archive_reasoning`),于是 run 目录里留下的是漏斗的**结论**,不是它**看到的东西**:

- 逐票 `slim`/`slim_deep`(卡片每个数字的来源)住在 `context_<engine>/` 根目录,一个字节都没进 run;
- `market_view.md` / `strategist_pack.json` / `_candidate_passport.json` / `L1_channels.csv` /
  `gate_fires.csv` / `_relative_buy_decision.json` 等**在 staging 里**却不在白名单;
- `L3_evidence/` `L3_news/` `ensemble/` 是**子目录**,`_archive_reasoning` 的 `p.is_file()`
  过滤把它们整段静默跳过;
- agent def / playbook / scan_config 才是 prompt 本体,run 只记一个 `git_sha`,而
  `resolve_git_sha` 从不查脏树。

更狠的是 staging 按**数据日**键(`ws.scan_dir(date)`)而 run 按**时刻**键 —— 同一数据日
重跑原地覆盖(实测 64 个已发布 run 只剩 49 个 staging;2026-07-29 三跑同日,run1 的 7 个
prompt hash 已对不上盘上文件)。所以本模块的判据是:**run 目录必须自足到 staging 可弃**。

## 三件事

1. `mirror_staging` —— 整目录镜像 staging → `trace/staging/`(含子目录)。规则从「白名单」
   变成「staging 里有的都在」,以后新增产物**不用再改复制表**(同族坑:给 prelude 加步骤
   要同改两份手写 skip 清单)。原有 `trace/` 顶层与 `reasoning/` 一件不动 —— 本模块是
   **纯加法**,不改任何既有消费者的读法。
2. `snapshot_inputs` —— 把 staging **之外**的输入抄进 `trace/inputs/`:slim/深核、行业 pack、
   prompt 本体(agent def / playbook / config)、当日温度计行。
3. `write_manifest` / `verify_manifest` —— 全目录 sha256 清单 + 校验子命令。发布目录是普通
   权限(实测被无关工具写进过 `.omc/state/`、被回放覆盖过 `run_health.json`),清单让
   「事后被改过」这件事**可检出**。

  uv run --no-sync python -m autoresearch.scan.retention verify reports_claude/scan/20260825_2149
  uv run --no-sync python -m autoresearch.scan.retention manifest <run_dir>
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
import stat
import sys
from pathlib import Path

import pandas as pd

from autoresearch.common import workspace as ws
from autoresearch.trace.atomic import atomic_write_json, canonical_json
from autoresearch.trace.blobs import dataframe_bytes

MANIFEST_NAME = "MANIFEST.sha256"
#: 镜像时跳过的目录名(锁/缓存,不是现场)
SKIP_DIRS = frozenset({"_sem", "__pycache__", ".omc"})
#: 镜像时跳过的文件后缀(半截写入/锁文件)
SKIP_SUFFIXES = (".lock", ".tmp")
_DIGEST_RE = re.compile(r"^[0-9a-f]{64}$", re.ASCII)

#: prompt 本体 —— 它们才是 agent 真正执行的指令,run 里原先只有一个 `git_sha` 代表它们。
#: 仓内相对路径;缺文件静默跳过(不同引擎/精简 checkout 下可能不全)。
#: **加新 agent def 要同步加这里**,否则它的规则在 run 里没有留痕。
PROMPT_SOURCES = (
    ".claude/agents/l3-rank.md",
    ".claude/agents/l4-card.md",
    ".claude/agents/l4-intel.md",
    ".claude/agents/macro-brief.md",
    ".claude/agents/sector-brief.md",
    ".claude/agents/dossier-init.md",
    ".claude/skills/stock-research/lite-playbook.md",
    ".claude/skills/scan-market/SKILL.md",
    ".claude/skills/scan-market/STAGES.md",
    ".claude/skills/scan-market/scan_config.jsonc",
    ".claude/skills/scan-market/pinned.jsonc",
)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _skip(path: Path, base: Path) -> bool:
    rel = path.relative_to(base)
    if any(part in SKIP_DIRS for part in rel.parts):
        return True
    return path.name.endswith(SKIP_SUFFIXES)


def mirror_staging(scan_dir: Path | str, run_dir: Path | str) -> int:
    """整目录镜像 staging → `<run_dir>/trace/staging/`,返回复制的文件数。

    **幂等**:重复调用就是覆盖(`copy2` 保留 mtime),同输入结果一致。跳 `_sem/` 与
    `*.lock`/`*.tmp`(锁与半截写入不是现场)。

    ⚠️ **调用时机**:必须排在 `build_summary` / `publish_run_observation` / `brief` 之后 ——
    `_final_ratings.json`/`decision_records.json`/`gate_fires.csv`/`_relative_buy_decision.json`
    都是那三步内部才写的。早跑一次 = 镜像到半成品(FN-1 家族:探针读还没生成的产物)。
    """
    src = Path(scan_dir)
    dst = Path(run_dir) / "trace" / "staging"
    if not src.is_dir():
        return 0
    n = 0
    for p in sorted(src.rglob("*")):
        if not p.is_file() or _skip(p, src):
            continue
        target = dst / p.relative_to(src)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(p, target)
        n += 1
    return n


def _slim_tickers(scan_dir: Path) -> list[str]:
    """当日 slim 的 ticker 名单:`_harvest_list.txt` 优先(它就是 harvest 的输入清单);
    缺文件 → 从 finalists.csv 现算(`normalize_symbol`,与 prompts 落稿同一口径)。"""
    hp = scan_dir / "_harvest_list.txt"
    if hp.is_file():
        return [ln.strip() for ln in hp.read_text(encoding="utf-8").splitlines() if ln.strip()]
    fp = scan_dir / "finalists.csv"
    if not fp.is_file():
        return []
    try:
        import pandas as pd

        from autoresearch.dataflows.symbol_utils import normalize_symbol
        fin = pd.read_csv(fp, dtype={"code": str})
        return [normalize_symbol(str(c).split(".")[0].zfill(6))
                for c in fin.get("code", []) if str(c).strip() and str(c) != "nan"]
    except Exception:  # noqa: BLE001 — 名单推导失败 = 该类快照缺席,不挡发布
        return []


def _temperature_row(date: str) -> dict | None:
    """当日温度计行(brief 的 🌡 出处)。`temperature.csv` 是**共享追加** CSV 且可被
    `temperature_calib` 重算 —— 只抄本 run 用到的那一行,不抄整表。"""
    p = ws.learning_root() / "temperature.csv"
    if not p.is_file():
        return None
    try:
        import pandas as pd
        df = pd.read_csv(p, dtype=str)
        col = "date" if "date" in df.columns else df.columns[0]
        hit = df[df[col].astype(str).str.strip() == str(date)]
        if not len(hit):
            return None
        # `dtype=str` 读入 → 值本就是 str/NaN;NaN 落 None(不写 "nan" 字面量,那是
        # 「读数表印出字面 nan」同族)。不做数值化:这是留痕不是计算。
        return {str(k): (None if pd.isna(v) else str(v))
                for k, v in hit.iloc[-1].to_dict().items()}
    except Exception:  # noqa: BLE001
        return None


def snapshot_inputs(scan_dir: Path | str, run_dir: Path | str) -> dict:
    """把 staging **之外**的输入抄进 `<run_dir>/trace/inputs/`,返回逐类计数。

    四类 + 一行:
      - `slim/` —— 逐票 `<ticker>_<date>_slim.md` 与 `_slim_deep.md`(住 `context_<engine>/`
        根目录,不在 `scan/<date>/` 下)。卡片每个数字的来源,原先只有 sha256 活在任务簿里。
      - `sector_packs/` —— `context_<engine>/sector/<date>/*.json`,行业 brief 的唯一输入。
      - `prompts/` —— `PROMPT_SOURCES`(agent def / playbook / scan_config / pinned),扁平化
        命名(`agents__l4-card.md`),因为它们才是 agent 真正读的指令本体。
      - `temperature_row.json` —— 当日温度计行。
    档案(dossier)**不在这里**:它在 assemble 尾被 δ 原地改写,必须在 prompt 落稿那一刻
    抄,见 `l4/prompts.write_dispatch_pack` 的 `_dossier_snapshot/`(随 staging 镜像带走)。
    """
    scan = Path(scan_dir)
    date = scan.name
    base = Path(run_dir) / "trace" / "inputs"
    out = {"slim": 0, "sector_packs": 0, "prompts": 0, "temperature_row": 0}

    slim_dir = base / "slim"
    ctx = ws.context_root()
    for ticker in _slim_tickers(scan):
        for suffix in ("_slim.md", "_slim_deep.md"):
            p = ctx / f"{ticker}_{date}{suffix}"
            if p.is_file():
                slim_dir.mkdir(parents=True, exist_ok=True)
                shutil.copy2(p, slim_dir / p.name)
                out["slim"] += 1

    packs = ctx / "sector" / date
    if packs.is_dir():
        pack_dir = base / "sector_packs"
        for p in sorted(packs.glob("*.json")):
            pack_dir.mkdir(parents=True, exist_ok=True)
            shutil.copy2(p, pack_dir / p.name)
            out["sector_packs"] += 1

    prompt_dir = base / "prompts"
    for rel in PROMPT_SOURCES:
        p = Path(rel)
        if not p.is_file():
            continue
        prompt_dir.mkdir(parents=True, exist_ok=True)
        flat = "__".join(p.parts[1:]) if p.parts and p.parts[0] == ".claude" else p.name
        shutil.copy2(p, prompt_dir / flat)
        out["prompts"] += 1

    row = _temperature_row(date)
    if row is not None:
        base.mkdir(parents=True, exist_ok=True)
        (base / "temperature_row.json").write_text(
            json.dumps(row, ensure_ascii=False, sort_keys=True), encoding="utf-8")
        out["temperature_row"] = 1
    return out


#: 归档 transcript 的 agent 名单(判断腿)。名字取自 transcript 自报的 `agent` 字段
#: (**连字符**,如 `l4-card`;不是 `scan_config` 里下划线的 role 名)。
#:
#: 为什么只归档这几个:卡片是**结论**,transcript 是**怎么得出结论的**——复盘「这只票为什么
#: 被判 UW」只有后者答得了。`general-purpose`(命令壳,一次跑 47 份、零判断)与主会话
#: (session 级产物,可能混入与本次扫描无关的内容)都不收。
#: 实测体积(2026-08-25 真跑):五个 role 原始 2.65 MB,gzip ≈49% → **约 1.3 MB/run**
#: (设计稿 §4.3 R4 估的 10–20 MB 偏大一个量级,以此实测为准)。
ARCHIVE_TRANSCRIPT_AGENTS = ("l3-rank", "l4-card", "l4-intel", "macro-brief", "sector-brief")


def archive_transcripts(scan_dir: Path | str, run_dir: Path | str,
                        agents: tuple[str, ...] = ARCHIVE_TRANSCRIPT_AGENTS) -> dict:
    """把判断腿的 subagent transcript gzip 归档进 `<run_dir>/trace/transcripts/`。

    数据源是 staging 的 `_token_usage.json` —— `usage_harvest` 已经为计量把每份 transcript
    定位好了(逐行带 `agent` 与 `path`),这里不再自己去 `~/.claude/projects/` 里找一遍
    (同一件事两处各写一套定位逻辑,迟早对不上)。

    transcript 住在 harness 管的目录里、受其保留策略约束 —— **不归档就等于没有**。
    缺 `_token_usage.json`(计量还没跑 / 跑失败)→ `{"n": 0, "reason": "no-usage-ledger"}`,
    presence-gated,不报错。
    """
    import gzip

    scan = Path(scan_dir)
    out_dir = Path(run_dir) / "trace" / "transcripts"
    usage = scan / "_token_usage.json"
    if not usage.is_file():
        return {"n": 0, "bytes": 0, "reason": "no-usage-ledger"}
    try:
        rows = (json.loads(usage.read_text(encoding="utf-8")) or {}).get("rows") or []
    except (OSError, json.JSONDecodeError):
        return {"n": 0, "bytes": 0, "reason": "bad-usage-ledger"}
    wanted = set(agents)
    index: list[dict] = []
    total = 0
    for row in rows:
        agent = str(row.get("agent") or "")
        src = row.get("path")
        if agent not in wanted or not src:
            continue
        p = Path(src)
        if not p.is_file():
            index.append({"agent": agent, "file": p.name, "status": "GONE"})
            continue
        out_dir.mkdir(parents=True, exist_ok=True)
        target = out_dir / f"{agent}-{p.stem}.jsonl.gz"
        raw = p.read_bytes()
        # mtime=0:gzip 头里的时间戳会让同输入产出不同字节,清掉才幂等(MANIFEST 才不会天天变)
        target.write_bytes(gzip.compress(raw, compresslevel=6, mtime=0))
        index.append({"agent": agent, "file": p.name, "status": "PRESENT",
                      "raw_bytes": len(raw), "gz_bytes": target.stat().st_size,
                      "sha256": hashlib.sha256(raw).hexdigest()})
        total += target.stat().st_size
    if index:
        out_dir.mkdir(parents=True, exist_ok=True)
        (out_dir / "_index.json").write_text(
            json.dumps({"schema_version": 1, "agents": sorted(wanted),
                        "transcripts": index}, ensure_ascii=False, sort_keys=True, indent=1),
            encoding="utf-8")
    return {"n": sum(1 for r in index if r["status"] == "PRESENT"),
            "bytes": total, "gone": sum(1 for r in index if r["status"] == "GONE")}


#: 湖清单回看窗(交易日)。60 日面板(`frame._harvest_vol_series`)是最长的一条读腿,+10 余量。
LAKE_WINDOW_DAYS = 70


def lake_manifest(date: str, *, lake_root: Path | None = None,
                  window_days: int = LAKE_WINDOW_DAYS) -> dict:
    """本 run 窗口内**湖的样子** —— `{endpoint/key: "<sha256>:<bytes>"}`。

    ## 它证明什么、不证明什么

    证明:事后重跑时,同一批 parquet 是不是同一批。三种 key 的风险不同(`data/endpoints.py`):
      - `key=date`/`period` —— 首写冻结(`cache.py` 命中即返回),理论上不该变;但
        `contracts doctor --purge` 会**删** parquet,删完再取拿到的是**今天口径**的厂商数据
        (可能已被重述)。清单让这件事从「查不出」变成「一比就知道」。
      - `key=as_of` —— entity@取数日,PIT 安全。
      - **`key=static`(`stock_basic`/`trade_cal`)—— 明文「单文件,刷新即覆盖一份」**:
        行业分类、上市日、ST 标记会在过去的 run 底下**悄悄改变**。这是本清单最该盯的一类。
    不证明:这些文件**当天真的被读过**。真 lineage 要在 `cache.get_or_fetch` 逐次记账,
    那是热路径改动(且跨多个 CLI 进程),本波不做 —— 诚实标注,不冒充。

    成本实测(2026-08-25 窗口):2781 文件 / 126 MB / **sha256 全量 ~1.0 秒**,一次 run 一次。
    """
    root = Path(lake_root) if lake_root else ws.lake_root()
    if not root.is_dir():
        return {"schema_version": 1, "schema": "window_guess",
                "source_mode": "window_guess", "date": str(date),
                "window_days": window_days, "files": {}, "note": "lake 目录不存在"}
    daily = root / "daily"
    D = str(date).replace("-", "")
    days = sorted(p.stem for p in daily.glob("*.parquet")) if daily.is_dir() else []
    window = set(d for d in days if d <= D)
    window = set(sorted(window)[-window_days:]) | {D}
    files: dict[str, str] = {}
    for ep_dir in sorted(p for p in root.iterdir() if p.is_dir()):
        for p in sorted(ep_dir.glob("*.parquet")):
            stem = p.stem
            key = stem.split("@")[-1] if "@" in stem else stem
            if key not in window and stem != "static":
                continue
            files[f"{ep_dir.name}/{stem}"] = f"{sha256_file(p)}:{p.stat().st_size}"
    return {"schema_version": 1, "schema": "window_guess",
            "source_mode": "window_guess", "date": str(date), "window_days": window_days,
            "n_files": len(files),
            "note": ("window_guess 回退视图：窗口内湖文件的内容指纹。"
                     "**不等于「这些文件当天被读过」**；只有 reads.jsonl 派生的 exact_reads "
                     "才是逐次真实 lineage。"
                     "key=static(stock_basic/trade_cal)是唯一「刷新即覆盖」的一类,最该盯。"),
            "files": files}


def _lineage_path(scan_dir: Path) -> Path | None:
    candidates = (
        scan_dir.parent.parent / "capsule/lineage/reads.jsonl",
        scan_dir / "lineage/reads.jsonl",
        scan_dir / "trace/reads.jsonl",
    )
    return next((path for path in candidates if path.is_file()), None)


def _dataframe_bytes(frame: pd.DataFrame) -> bytes:
    return dataframe_bytes(frame)


def _frame_columns_hash(frame: pd.DataFrame) -> str:
    columns = [
        {"name": str(name), "dtype": str(dtype)}
        for name, dtype in zip(frame.columns, frame.dtypes, strict=True)
    ]
    return hashlib.sha256(canonical_json(columns).encode("utf-8")).hexdigest()


def _read_jsonl(path: Path) -> list[dict]:
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        row = json.loads(line)
        if not isinstance(row, dict):
            raise ValueError("jsonl row must be an object")
        rows.append(row)
    return rows


def _require_digest(value: object, *, field: str) -> str:
    if type(value) is not str or not _DIGEST_RE.fullmatch(value):
        raise ValueError(f"{field} must be a full lowercase SHA-256 digest")
    return value


def _validate_lineage_blob(capsule: Path, row: dict) -> None:
    digest = _require_digest(row.get("blob_hash"), field="blob_hash")
    size = row.get("bytes")
    rows = row.get("rows")
    columns_hash = _require_digest(row.get("columns_hash"), field="columns_hash")
    if type(size) is not int or size < 0:
        raise ValueError("bytes must be a non-negative integer")
    if type(rows) is not int or rows < 0:
        raise ValueError("rows must be a non-negative integer")
    target = capsule / "blobs" / "sha256" / digest[:2] / digest
    info = target.lstat()
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode):
        raise ValueError("blob must be a regular file")
    target.resolve(strict=True).relative_to(capsule.resolve(strict=True))
    if info.st_size != size or sha256_file(target) != digest:
        raise ValueError("blob size or digest mismatch")
    frame = pd.read_parquet(target)
    if len(frame) != rows or _frame_columns_hash(frame) != columns_hash:
        raise ValueError("blob dataframe semantics mismatch")


def _row_hash(row: dict) -> str:
    return hashlib.sha256(canonical_json(row).encode("utf-8")).hexdigest()


def _source_event_keys(events: list[dict]) -> set[tuple[str, str | None]]:
    keys: set[tuple[str, str | None]] = set()
    for event in events:
        if event.get("event_type") not in {"SOURCE_READ", "SOURCE_FETCHED", "SOURCE_FAILED"}:
            continue
        payload = event.get("payload")
        if not isinstance(payload, dict) or payload.get("lineage_persisted") is not True:
            continue
        digest = payload.get("lineage_row_hash")
        if type(digest) is not str or not _DIGEST_RE.fullmatch(digest):
            continue
        correlation = payload.get("correlation_id")
        keys.add((digest, correlation if type(correlation) is str and correlation else None))
    return keys


def _gap_keys(gaps: list[dict]) -> set[tuple[str | None, str | None]]:
    keys: set[tuple[str | None, str | None]] = set()
    for gap in gaps:
        digest = gap.get("lineage_row_hash")
        normalized_digest = (
            digest if type(digest) is str and _DIGEST_RE.fullmatch(digest) else None
        )
        correlation = gap.get("correlation_id")
        normalized_correlation = (
            correlation if type(correlation) is str and correlation else None
        )
        if normalized_digest is not None or normalized_correlation is not None:
            keys.add((normalized_digest, normalized_correlation))
    return keys


def _read_exact_manifest(scan_dir: Path) -> tuple[dict | None, str]:
    source = _lineage_path(scan_dir)
    if source is None:
        return None, "lineage_absent"
    try:
        rows = _read_jsonl(source)
        capsule = source.parent.parent
        event_path = capsule / "events/events.jsonl"
        gap_path = source.parent / "evidence_gaps.jsonl"
        events_present = event_path.is_file()
        events = _read_jsonl(event_path) if events_present else []
        gaps = _read_jsonl(gap_path) if gap_path.is_file() else []
        event_keys = _source_event_keys(events)
        gap_keys = _gap_keys(gaps)

        effective_complete: list[bool] = []
        missing_events = 0
        matching_gaps = 0
        for row in rows:
            status = row.get("status")
            if status not in {"SUCCEEDED", "FAILED"}:
                raise ValueError("invalid source status")
            endpoint = row.get("endpoint")
            if type(endpoint) is not str or not endpoint:
                raise ValueError("endpoint must be non-empty")
            if type(row.get("evidence_complete")) is not bool:
                raise ValueError("evidence_complete must be boolean")
            blob_hash = row.get("blob_hash")
            if status == "SUCCEEDED" and blob_hash is not None:
                _validate_lineage_blob(capsule, row)
            elif status == "SUCCEEDED" and row.get("bytes") is not None:
                raise ValueError("successful source row without blob cannot record bytes")
            elif status == "FAILED" and any(
                row.get(field) is not None
                for field in ("blob_hash", "bytes", "rows", "columns_hash")
            ):
                raise ValueError("failed source row cannot reference successful content")

            digest = _row_hash(row)
            correlation = row.get("correlation_id")
            if correlation is not None and (type(correlation) is not str or not correlation):
                raise ValueError("correlation_id must be a non-empty string")
            has_event = any(
                event_digest == digest
                and (correlation is None or event_correlation == correlation)
                for event_digest, event_correlation in event_keys
            )
            has_gap = any(
                gap_digest == digest
                or (correlation is not None and gap_correlation == correlation)
                for gap_digest, gap_correlation in gap_keys
            )
            if events_present and not has_event:
                missing_events += 1
            if has_gap:
                matching_gaps += 1
            complete = row["evidence_complete"] and not has_gap
            if events_present:
                complete = complete and has_event
            if status == "SUCCEEDED" and blob_hash is None:
                complete = False
            effective_complete.append(complete)
    except BaseException:
        return None, "lineage_unreadable"

    succeeded = [row for row in rows if row.get("status") == "SUCCEEDED"]
    failed = [row for row in rows if row.get("status") == "FAILED"]
    incomplete = [row for row, complete in zip(rows, effective_complete, strict=True) if not complete]
    no_blob = [row for row in succeeded if not row.get("blob_hash")]
    blobbed = [row for row in succeeded if row.get("blob_hash")]

    files: dict[str, str] = {}
    versions: dict[str, dict[str, str]] = {}
    for row in blobbed:
        raw_path = row.get("path")
        endpoint = str(row.get("endpoint") or "unknown")
        stem = Path(str(raw_path)).stem if raw_path else f"blob-{str(row['blob_hash'])[:16]}"
        base_key = f"{endpoint}/{stem}"
        value = f"{row['blob_hash']}:{int(row.get('bytes') or 0)}"
        known = versions.setdefault(base_key, {})
        if value in known:
            continue
        key = base_key if not known else f"{base_key}#v{len(known) + 1}"
        known[value] = key
        files[key] = value
    return {
        "schema_version": 2,
        "schema": "exact_reads",
        "source_mode": "exact_reads",
        "date": scan_dir.name,
        "n_files": len(files),
        "n_total_reads": len(rows),
        "n_successful_reads": len(succeeded),
        "n_failed_reads": len(failed),
        "n_incomplete_reads": len(incomplete),
        "n_no_blob_reads": len(no_blob),
        "n_missing_source_events": missing_events,
        "n_evidence_gaps": matching_gaps,
        "files": files,
        "note": "由本次实际成功 source reads 派生；不是窗口猜测。",
    }, ""


def write_lake_manifest(scan_dir: Path | str, run_dir: Path | str) -> int:
    """落 `<run_dir>/trace/lake_manifest.json`,返回文件数。"""
    target = Path(run_dir) / "trace" / "lake_manifest.json"
    if target.is_file():
        try:
            existing = json.loads(target.read_text(encoding="utf-8"))
            return int(existing.get("n_files") or 0)
        except (OSError, json.JSONDecodeError, AttributeError):
            return 0
    source = Path(scan_dir)
    doc, reason = _read_exact_manifest(source)
    if doc is None:
        doc = lake_manifest(source.name)
        doc.setdefault("schema", "window_guess")
        doc.setdefault("source_mode", "window_guess")
        doc["source_reason"] = reason
    atomic_write_json(target, doc)
    return int(doc.get("n_files") or 0)


def diff_lake_manifest(run_a: Path | str, run_b: Path | str) -> dict:
    """两个 run 的湖清单对比 → `{changed[], only_a[], only_b[]}`。

    用法:「上周那只票的行业怎么变了」——先比 `stock_basic/static`,一行就知道。
    任一侧缺清单(2026-08-26 之前的 run)→ `{"reason": "no-manifest"}`。
    """
    def _load(run: Path | str) -> dict | None:
        p = Path(run) / "trace" / "lake_manifest.json"
        if not p.is_file():
            return None
        try:
            return (json.loads(p.read_text(encoding="utf-8")) or {}).get("files") or {}
        except (OSError, json.JSONDecodeError):
            return None

    a, b = _load(run_a), _load(run_b)
    if a is None or b is None:
        return {"reason": "no-manifest", "changed": [], "only_a": [], "only_b": []}
    return {"reason": "",
            "changed": sorted(k for k in set(a) & set(b) if a[k] != b[k]),
            "only_a": sorted(set(a) - set(b)),
            "only_b": sorted(set(b) - set(a))}


def prompt_hashes(sources: tuple[str, ...] = PROMPT_SOURCES) -> dict[str, str]:
    """`{仓内相对路径: sha256}` —— 供 `RunContract.prompt_hashes` 记账。

    为什么 hash 而不是只靠 `git_sha`:agent def 是**未提交也会生效**的(会话启动装载的是
    工作树里那一份)。git_sha 干净、prompt 却被改过的 run,只有这份 hash 能看出来。
    缺文件不进表(诚实缺席,不写占位)。
    """
    return {rel: sha256_file(Path(rel)) for rel in sources if Path(rel).is_file()}


def resolve_recorded_path(recorded: str | Path) -> Path | None:
    """把产物里**记下来的**路径解析成盘上真实存在的路径;解析不到 → None。

    2026-08-11 引擎隔离(`context/` → `context_claude/`)之前落的 `_l4_tasks.json` 记的是
    裸 `context/…`(实测 113 处)。那个根今天不存在,**文件却还在** —— 照字面找会把 113 件
    在场的产物报成 MISSING。这里只做一次前缀重映射:`context/` → 当前引擎的 context 根。
    (**不改历史产物**,展示层现算 —— 与「账本列名断层」同一条家规。)
    """
    p = Path(recorded)
    if p.exists():
        return p
    parts = p.parts
    if parts and parts[0] == ws.LEGACY_CONTEXT_ROOT:
        remapped = ws.context_root().joinpath(*parts[1:])
        if remapped.exists():
            return remapped
    return None


def _manifest_rows(run_dir: Path) -> list[tuple[str, str]]:
    base = Path(run_dir)
    rows: list[tuple[str, str]] = []
    for p in sorted(base.rglob("*")):
        if not p.is_file():
            continue
        rel = p.relative_to(base)
        if rel.as_posix() == f"trace/{MANIFEST_NAME}":
            continue
        if any(part in SKIP_DIRS for part in rel.parts):
            continue
        rows.append((sha256_file(p), rel.as_posix()))
    return sorted(rows, key=lambda r: r[1])


def write_manifest(run_dir: Path | str) -> Path:
    """全 run 目录逐文件 sha256 → `trace/MANIFEST.sha256`(标准 `sha256sum` 格式,可
    `shasum -a 256 -c` 直接校验)。返回清单路径。

    自身不入清单(否则永远自相矛盾);`.omc/` 等工具状态目录不入(它们不是本 run 的产物,
    但**会**在 `verify` 的 `extra` 里被点名 —— 那正是我们要看见的东西)。
    """
    base = Path(run_dir)
    target = base / "trace" / MANIFEST_NAME
    target.parent.mkdir(parents=True, exist_ok=True)
    body = "".join(f"{sha}  {rel}\n" for sha, rel in _manifest_rows(base))
    tmp = target.with_name(f"{target.name}.tmp")
    tmp.write_text(body, encoding="utf-8")
    tmp.replace(target)
    return target


def read_manifest(run_dir: Path | str) -> dict[str, str]:
    p = Path(run_dir) / "trace" / MANIFEST_NAME
    if not p.is_file():
        return {}
    out: dict[str, str] = {}
    for line in p.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        sha, _, rel = line.partition("  ")
        if rel:
            out[rel.strip()] = sha.strip()
    return out


def verify_manifest(run_dir: Path | str) -> dict:
    """逐文件核对清单 → `{ok, n, missing[], changed[], extra[]}`。

    - `missing` 清单里有、盘上没了;`changed` 内容变了;`extra` 盘上多出来的(含无关工具
      写进来的状态文件)。三类**都算不 ok** —— 「发布后被动过」不分好坏都要看得见。
    - 清单缺失 → `{"ok": False, "reason": "no-manifest"}`(老 run 天然如此,不是故障)。
    """
    base = Path(run_dir)
    recorded = read_manifest(base)
    if not recorded:
        return {"ok": False, "reason": "no-manifest", "n": 0,
                "missing": [], "changed": [], "extra": []}
    actual = dict((rel, sha) for sha, rel in _manifest_rows(base))
    missing = sorted(set(recorded) - set(actual))
    extra = sorted(set(actual) - set(recorded))
    changed = sorted(rel for rel in set(recorded) & set(actual)
                     if recorded[rel] != actual[rel])
    return {"ok": not (missing or extra or changed), "reason": "", "n": len(recorded),
            "missing": missing, "changed": changed, "extra": extra}


def retain(scan_dir: Path | str, run_dir: Path | str) -> dict:
    """发布收尾的现场留存(镜像 + run 外输入 + 清单)。失败分类留痕,**不抛**:
    留存是加法,不该有能力毁掉一次已跑完的扫描(同 `brief.safe_publish` 口径)。"""
    res: dict = {"mirrored": 0, "inputs": {}, "transcripts": {}, "lake_files": 0,
                 "manifest": None, "errors": []}
    try:
        res["mirrored"] = mirror_staging(scan_dir, run_dir)
    except Exception as exc:  # noqa: BLE001
        res["errors"].append(f"mirror_staging: {type(exc).__name__}: {exc}")
    try:
        res["inputs"] = snapshot_inputs(scan_dir, run_dir)
    except Exception as exc:  # noqa: BLE001
        res["errors"].append(f"snapshot_inputs: {type(exc).__name__}: {exc}")
    try:
        # 发布那次通常还没有 `_token_usage.json`(计量在 CP7 才跑)→ 静默 0;
        # `post_run observe` 那次才真正归档。两次都幂等。
        res["transcripts"] = archive_transcripts(scan_dir, run_dir)
    except Exception as exc:  # noqa: BLE001
        res["errors"].append(f"archive_transcripts: {type(exc).__name__}: {exc}")
    try:
        res["lake_files"] = write_lake_manifest(scan_dir, run_dir)
    except Exception as exc:  # noqa: BLE001
        res["errors"].append(f"write_lake_manifest: {type(exc).__name__}: {exc}")
    try:
        res["manifest"] = str(write_manifest(run_dir))
    except Exception as exc:  # noqa: BLE001
        res["errors"].append(f"write_manifest: {type(exc).__name__}: {exc}")
    return res


def _resolve_run(value: str) -> Path:
    p = Path(value)
    return p if p.exists() else ws.reports_root() / "scan" / value


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="run 现场留存 / 完整性校验(零 LLM)")
    ap.add_argument("command", choices=["verify", "manifest"])
    ap.add_argument("run", help="run 目录或 run_id(如 20260825_2149)")
    args = ap.parse_args(argv)
    run_dir = _resolve_run(args.run)
    if not run_dir.is_dir():
        print(json.dumps({"ok": False, "error": f"run 目录不存在:{run_dir}"},
                         ensure_ascii=False))
        return 2
    if args.command == "manifest":
        p = write_manifest(run_dir)
        print(json.dumps({"ok": True, "manifest": str(p),
                          "n": len(read_manifest(run_dir))}, ensure_ascii=False))
        return 0
    res = verify_manifest(run_dir)
    print(json.dumps({"run": str(run_dir), **res}, ensure_ascii=False, sort_keys=True))
    if not res["ok"]:
        for kind in ("missing", "changed", "extra"):
            for rel in res[kind][:20]:
                print(f"  {kind:<8} {rel}", file=sys.stderr)
    return 0 if res["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
