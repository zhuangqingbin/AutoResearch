#!/usr/bin/env python3
"""L4 上下文块：schema 绑定、内容寻址、原子写和响亮校验。"""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import asdict, dataclass
from pathlib import Path

CONTEXT_BLOCK_SCHEMA_VERSION = 1


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _scope_file(scope: str) -> str:
    readable = re.sub(r"[^0-9A-Za-z._-]+", "-", str(scope)).strip("-")[:40]
    return f"{readable or 'scope'}-{_sha256(str(scope).encode())[:12]}.json"


@dataclass(frozen=True)
class ContextBlock:
    schema_version: int
    kind: str
    scope: str
    created_for_date: str
    content: str
    content_sha256: str
    source_hashes: dict[str, str]

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, raw: dict) -> "ContextBlock":
        block = cls(**raw)
        if block.schema_version != CONTEXT_BLOCK_SCHEMA_VERSION:
            raise ValueError(
                f"unsupported context block schema_version={block.schema_version}"
            )
        if block.content_sha256 != _sha256(block.content.encode("utf-8")):
            raise ValueError("context block content_sha256 mismatch")
        if block.kind not in {"market", "sector", "dossier", "differential"}:
            raise ValueError(f"unsupported context block kind={block.kind!r}")
        return block


@dataclass(frozen=True)
class WrittenContextBlock:
    path: Path
    block: ContextBlock


def context_block_path(scan_dir: Path | str, kind: str, scope: str) -> Path:
    return Path(scan_dir) / "_context_blocks" / kind / _scope_file(scope)


def _source_key(scan: Path, source: Path) -> str:
    try:
        return str(source.relative_to(scan))
    except ValueError:
        return source.name


def write_context_block(
    scan_dir: Path | str,
    *,
    kind: str,
    scope: str,
    content: str,
    source_paths: list[Path | str],
) -> WrittenContextBlock:
    """同输入同字节；无 wall-clock 字段，重跑不会让 cache key 漂移。"""
    scan = Path(scan_dir)
    sources: dict[str, str] = {}
    for raw in sorted((Path(p) for p in source_paths), key=lambda p: str(p)):
        if not raw.is_file():
            raise FileNotFoundError(raw)
        sources[_source_key(scan, raw)] = _sha256(raw.read_bytes())
    block = ContextBlock(
        schema_version=CONTEXT_BLOCK_SCHEMA_VERSION,
        kind=str(kind),
        scope=str(scope),
        created_for_date=scan.name,
        content=str(content),
        content_sha256=_sha256(str(content).encode("utf-8")),
        source_hashes=dict(sorted(sources.items())),
    )
    # 构造时也走一次读取校验逻辑，防 kind/schema 漏校。
    ContextBlock.from_dict(block.to_dict())
    target = context_block_path(scan, kind, scope)
    target.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(
        block.to_dict(), ensure_ascii=False, sort_keys=True, indent=2
    ) + "\n"
    if not target.exists() or target.read_text(encoding="utf-8") != payload:
        temp = target.with_name(f"{target.name}.tmp")
        temp.write_text(payload, encoding="utf-8")
        temp.replace(target)
    return WrittenContextBlock(path=target, block=block)


def read_context_block(path: Path | str) -> ContextBlock:
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError("context block root must be an object")
    return ContextBlock.from_dict(raw)


def manifest_ref(written: WrittenContextBlock) -> dict:
    """prompt manifest 的定长引用；content 留在块文件，不在 manifest 复制。"""
    return {
        "path": str(written.path),
        "schema_version": written.block.schema_version,
        "content_sha256": written.block.content_sha256,
        "source_hashes": written.block.source_hashes,
    }
