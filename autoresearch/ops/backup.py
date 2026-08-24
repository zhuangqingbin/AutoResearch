#!/usr/bin/env python3
"""D11 · 知识资产 nightly 备份 —— 本波唯一新增的自动步(2026-08-19)。

`context_<engine>/knowledge/`(dossier 常备档案 + 遗留 precedents.db)、
`context_<engine>/learning/*.jsonl`(**历史账本,只读**——生产者已随 2026-08-21
「整个 learning 层退役」删除;唯一还在写的是 `usage_reconcile.jsonl`)、
`context_<engine>/factor_lab/weights.json`(排序权重)是**不可重算资产**——丢了就永久
没了,且全在 `.gitignore` 外、当前零备份零版本。本模块只做打包 + 轮转,不做恢复/校验
(资产量小,恢复即"解压覆盖回去")。

见 docs/specs/2026-08-18-e6-activation-learning-slimdown-design.md §4 D11。

⚠️ **无自动挂点**:原计划挂在 `nightly_close` 末位,该模块已随闭环退役删除。本模块只提供
函数 + CLI,靠人手动或 launchd 跑。
"""
from __future__ import annotations

import tarfile
from datetime import date
from pathlib import Path

from autoresearch.common import workspace as ws

DEFAULT_KEEP = 14


def run_backup(root: Path | str | None = None, keep: int = DEFAULT_KEEP) -> dict:
    """打包不可重算知识资产 → ``<root>/backups/knowledge_<YYYY-MM-DD>.tar.gz``。

    含三件:
    - ``context_<engine>/knowledge/`` 整目录(判例库 + dossier 档案),arcname 前缀 ``knowledge/``；
    - ``context_<engine>/learning/*.jsonl``(仅 jsonl,不含 csv/其他 staging),
      arcname 前缀 ``learning/``；
    - ``context_<engine>/factor_lab/weights.json``(单文件),arcname ``factor_lab/weights.json``。

    源目录/文件缺失时跳过而非报错(测试用轻量 fixture 常常只造部分资产)。

    保留最近 ``keep`` 份(按文件名里的 ISO 日期排序,ISO 格式字典序=时间序),
    超出的从最旧开始删。同日重跑覆盖当日份(nightly 语义:一天一份快照,非一天一份历史)。

    Args:
        root: 仓库根(``context_<engine>/`` 与 ``backups/`` 的公共父目录)。
            ``None`` 时用 ``Path.cwd()``(生产用法:CLI 从仓库根调用)。
        keep: 保留的最近份数,默认 14。

    Returns:
        ``{"ok": True, "path": str, "bytes": int, "kept": int}``——
        ``kept`` 是本次落盘后 ``backups/`` 目录下实际剩余的份数。
    """
    base = Path(root) if root is not None else Path.cwd()
    knowledge_dir = base / ws.knowledge_root()
    learning_dir = base / ws.learning_root()
    weights_path = base / ws.factor_lab_root() / "weights.json"
    backups_dir = base / "backups"
    backups_dir.mkdir(parents=True, exist_ok=True)

    stamp = date.today().isoformat()
    target = backups_dir / f"knowledge_{stamp}.tar.gz"
    temp = target.with_name(f"{target.name}.tmp")

    with tarfile.open(temp, "w:gz") as tar:
        if knowledge_dir.is_dir():
            tar.add(knowledge_dir, arcname="knowledge")
        if learning_dir.is_dir():
            for jsonl in sorted(learning_dir.glob("*.jsonl")):
                tar.add(jsonl, arcname=f"learning/{jsonl.name}")
        if weights_path.is_file():
            tar.add(weights_path, arcname="factor_lab/weights.json")
    temp.replace(target)

    kept = _purge_old(backups_dir, keep=keep)

    return {
        "ok": True,
        "path": str(target),
        "bytes": target.stat().st_size,
        "kept": kept,
    }


def _purge_old(backups_dir: Path, *, keep: int) -> int:
    """只留最近 ``keep`` 份 ``knowledge_*.tar.gz``,多余的从最旧开始删。

    返回删除后 ``backups_dir`` 下剩余的份数。
    """
    files = sorted(backups_dir.glob("knowledge_*.tar.gz"))
    excess = len(files) - keep
    if excess > 0:
        for stale in files[:excess]:
            stale.unlink()
    return len(sorted(backups_dir.glob("knowledge_*.tar.gz")))


def main() -> int:
    result = run_backup()
    print(
        f"[backup] {result['path']} ({result['bytes']:,} bytes) — "
        f"kept {result['kept']} snapshot(s)"
    )
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
