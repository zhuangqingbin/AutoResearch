"""Hermetic incident fixtures shared by the forensic hardening tests.

The fixture deliberately prepares a second, valid publication candidate before
the run is sealed.  Publishing that candidate after ``finalize`` reproduces the
10:40/10:41 failure without mutating the frozen staging tree as part of the
assertion itself.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timezone
from hashlib import sha256
from pathlib import Path

from autoresearch.common import workspace as ws
from autoresearch.common.atomic import sha256_bytes
from autoresearch.common.run_identity import RunContract
from autoresearch.contracts.profiles import profile_factory
from autoresearch.session_agent import artifacts
from autoresearch.session_agent.workflows.dossier import publish_dossier
from autoresearch.session_agent.workflows.macro import publish_macro
from autoresearch.session_agent.workflows.scan import directory_manifest, publish_scan
from autoresearch.session_agent.workflows.sector import publish_sector
from autoresearch.session_agent.workflows.stock import publish_stock
from autoresearch.trace import capsule as capsule_mod

ANALYSIS_DATE = "2026-09-14"
RUN_NOW = datetime(2026, 9, 14, 2, 40, 0, 123456, tzinfo=timezone.utc)
KINDS = (
    "scan-market",
    "stock-research",
    "macro-research",
    "sector-research",
    "dossier-init",
)
MODES = {
    "scan-market": "SENTINEL_EMPTY",
    "stock-research": "LITE",
    "macro-research": "LITE",
    "sector-research": "LITE",
    "dossier-init": "INIT",
}


def _bootstrap(
    analysis_date,
    *,
    config=None,
    run_id=None,
    engine=None,
    workspace_path=None,
    session_ref=None,
    now=None,
):
    config = dict(config or {})
    return RunContract.build(
        analysis_date=analysis_date,
        user_config=config,
        pinned={},
        data_policy={},
        stage_budgets={},
        artifact_schema_versions={},
        git_sha="forensic-fixture",
        git_dirty=False,
        dirty_paths=[],
        run_kind=config["kind"],
        engine=engine,
        workspace_path=workspace_path,
        session_ref=session_ref,
        run_id=run_id,
        now=now,
    )


def _write_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, sort_keys=True), encoding="utf-8")


def _bind(handle, artifact_id: str, path: Path, payload: bytes) -> str:
    artifacts.register_artifact(handle, artifact_id, path, "WRITE")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(payload)
    return artifacts.bind_artifact_hash(handle, artifact_id)["sha256"]


def _business_text(kind: str, variant: str) -> tuple[str, str]:
    base = f"# {kind}\n\nprice=100\nrating=Hold\ngenerated_at=2026-09-14T10:40:00+08:00\n"
    if variant == "generated_at":
        changed = base.replace("10:40:00", "10:41:00")
    elif variant == "business":
        changed = base.replace("price=100", "price=101").replace("rating=Hold", "rating=Buy")
    else:
        raise ValueError(f"unknown report variant: {variant}")
    return base, changed


@dataclass
class ForensicCase:
    kind: str
    variant: str
    root: Path
    handle: object
    frozen_report_dir: Path
    publish: Callable[[], Path]
    _finalization: object | None = None

    def finish(self):
        if self._finalization is None:
            self._finalization = capsule_mod.finalize(
                self.handle.run_id,
                capsule_mod.BusinessStatus.SUCCEEDED,
                self.frozen_report_dir,
                profile=profile_factory(self.kind)(
                    mode=MODES[self.kind],
                    business_status="SUCCEEDED",
                    agent_roles=(),
                ),
                now=RUN_NOW,
            )
        return self._finalization

    def produce_changed_report(self) -> Path:
        """Call the real domain publisher; no test rule is implemented here."""
        return self.publish()

    def verify_report(self, path: Path) -> dict:
        candidate = path if path.is_dir() else path.parent
        return capsule_mod.verify(
            self.handle.run_id,
            final_path=candidate,
            kind=self.kind,
        )

    def snapshot_persistent_tree(self) -> dict[str, str]:
        snapshot: dict[str, str] = {}
        for top in (ws.context_root(), ws.reports_root(), ws.lake_root()):
            if not top.exists():
                continue
            for path in sorted(top.rglob("*")):
                if not path.is_file() or path.name.endswith(".lock"):
                    continue
                relative = f"{top.name}/{path.relative_to(top).as_posix()}"
                snapshot[relative] = sha256(path.read_bytes()).hexdigest()
        return snapshot


def make_case(kind: str, variant: str, root: Path) -> ForensicCase:
    mode = MODES[kind]
    handle = capsule_mod.begin_run(
        kind,
        ANALYSIS_DATE,
        ws.ENGINE,
        {"kind": kind, "mode": mode, "ticker": "600519.SS"},
        now=RUN_NOW,
        bootstrap=_bootstrap,
    )
    base, changed = _business_text(kind, variant)
    frozen = ws.run_reports_root(kind) / "_frozen" / handle.run_id
    frozen.mkdir(parents=True, exist_ok=False)
    (frozen / "report.md").write_text(base, encoding="utf-8")
    publish = _prepare_publisher(handle, kind, changed.encode("utf-8"))
    return ForensicCase(kind, variant, root, handle, frozen, publish)


def _prepare_publisher(handle, kind: str, payload: bytes) -> Callable[[], Path]:
    output = Path(handle.staging) / "session_outputs"
    output.mkdir(parents=True, exist_ok=True)
    publication_root = ws.run_reports_root(kind) / "_late_publication"

    if kind == "stock-research":
        report = output / "stock.card.md"
        report_hash = _bind(handle, "stock.card.output", report, payload)
        bundle = {
            "mode": "LITE",
            "ticker": "600519.SS",
            "name": "贵州茅台",
            "analysis_date": ANALYSIS_DATE,
            "rating": "Buy",
            "proposal": "BUY",
            "card_sha256": report_hash,
            "output_name": "late-stock.md",
        }
        _bind(
            handle,
            "stock.publication.bundle",
            output / "stock.publication.json",
            json.dumps(bundle, sort_keys=True).encode("utf-8"),
        )
        return lambda: publish_stock(handle, reports_root=publication_root)

    if kind == "macro-research":
        _write_json(Path(handle.workspace) / "session/request.json", {
            "analysis_date": ANALYSIS_DATE,
        })
        report = output / "market_view.md"
        report.write_bytes(payload)
        _write_json(output / "macro.publication.json", {
            "mode": "LITE",
            "report_sha256": sha256_bytes(payload),
            "output_name": "late-macro.md",
        })
        return lambda: publish_macro(handle, reports_root=publication_root)

    if kind == "sector-research":
        report = output / "sector.md"
        report.write_bytes(payload)
        _write_json(output / "sector.publication.json", {
            "analysis_date": ANALYSIS_DATE,
            "industry": "半导体",
            "report_sha256": sha256_bytes(payload),
        })
        return lambda: publish_sector(handle, reports_root=publication_root)

    if kind == "dossier-init":
        _write_json(Path(handle.workspace) / "session/request.json", {
            "subject": "600519",
            "name": "贵州茅台",
        })
        candidate = output / "dossier.candidate.md"
        candidate.write_bytes(payload)
        _write_json(output / "dossier.permissions.json", {
            "opening_target_sha256": None,
        })
        _write_json(output / "dossier.publication.json", {
            "candidate_sha256": sha256_bytes(payload),
        })
        target = publication_root / "600519.md"
        pool = publication_root / "pool.json"
        return lambda: publish_dossier(handle, target_path=target, pool_path=pool)

    if kind == "scan-market":
        candidate = Path(handle.workspace) / "publication_candidate"
        candidate.mkdir(parents=True)
        (candidate / "scan.md").write_bytes(payload)
        bundle = {
            "engine": handle.engine,
            "run_id": handle.run_id,
            "folder": "late-scan",
            "candidate_relative": candidate.relative_to(handle.workspace).as_posix(),
            "files": directory_manifest(candidate),
        }
        _bind(
            handle,
            "scan.publication.bundle",
            output / "scan.publication.json",
            json.dumps(bundle, sort_keys=True).encode("utf-8"),
        )
        return lambda: publish_scan(handle, reports_root=publication_root)

    raise ValueError(f"unsupported kind: {kind}")
