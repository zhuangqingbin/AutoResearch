"""Six evidence facts about a published run, reported separately on purpose.

The false green this replaces was one line: *「现场完整性 ✓」* rendered from a
MANIFEST that had merely re-hashed the files it happened to list.  It could not
see the 557 staging files nobody archived, the zero transcripts, or the
`$0.0000` cost of an unmeasured run — because none of those were ever listed.

So nothing here collapses into a single tick.  A run can be intact and
incomplete; complete and unreplayable; replayable and not durably archived.
Each of those is a different decision for the reader, so each gets its own line.
"""

from __future__ import annotations

import json
from pathlib import Path

CAPSULE_DIR = "capsule"
UNKNOWN = "UNKNOWN"


def _load(path: Path) -> dict:
    if not path.is_file():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def capsule_root(run_dir: Path | str) -> Path:
    return Path(run_dir) / CAPSULE_DIR


def has_capsule(run_dir: Path | str) -> bool:
    return (capsule_root(run_dir) / "capsule.json").is_file()


def evidence_facts(run_dir: Path | str) -> dict:
    """Read the frozen facts; never recompute, never infer a missing one as OK."""
    root = Path(run_dir)
    capsule = capsule_root(root)
    if not has_capsule(root):
        # Pre-capsule runs: the legacy MANIFEST answers integrity only, and says
        # nothing at all about completeness.  It must not be promoted.
        from autoresearch.scan.retention import verify_manifest as legacy_verify

        legacy = legacy_verify(root)
        return {
            "capsule": False,
            "business_status": UNKNOWN,
            "evidence_status": "LEGACY_PARTIAL",
            "integrity_ok": bool(legacy.get("ok")),
            "integrity_detail": legacy,
            "completeness_ok": False,
            "completeness_known": False,
            "missing_required": [],
            "replayability": "NONE",
            "durability": UNKNOWN,
            "dirty_patch": None,
            "coverage": {},
            "root_hash": None,
            "green": False,
            "reason": "本 run 早于 forensic capsule,只有 MANIFEST 能回答完好性",
        }

    manifest = _load(capsule / "capsule.json")
    evidence = _load(capsule / "verification/completeness.json")
    root_doc = _load(capsule / "verification/ROOT.json")
    contract = _load(capsule / "identity/run_contract.json")

    from autoresearch.trace.capsule import verify_manifest

    integrity = verify_manifest(root)
    current_manifest = capsule / "verification/MANIFEST.sha256"
    root_anchored = False
    if current_manifest.is_file() and root_doc.get("root_hash"):
        from autoresearch.trace.atomic import sha256_bytes

        root_anchored = (
            sha256_bytes(current_manifest.read_bytes()) == root_doc["root_hash"]
        )
    integrity_ok = bool(integrity.get("ok")) and root_anchored

    facts = {
        "capsule": True,
        "business_status": str(manifest.get("business_status") or UNKNOWN),
        "evidence_status": str(manifest.get("evidence_status") or UNKNOWN),
        "integrity_ok": integrity_ok,
        "integrity_detail": {**integrity, "root_anchored": root_anchored},
        "completeness_ok": bool(evidence.get("completeness_ok")),
        "completeness_known": bool(evidence),
        "missing_required": list(evidence.get("missing_required") or []),
        "replayability": str(manifest.get("replayability") or "NONE"),
        "durability": str(root_doc.get("durability") or UNKNOWN),
        "dirty_patch": bool(contract.get("git_dirty")) if contract else None,
        "coverage": evidence.get("coverage") or {},
        "root_hash": root_doc.get("root_hash"),
        "reason": None,
    }
    facts["green"] = bool(
        facts["business_status"] == "SUCCEEDED"
        and facts["evidence_status"] == "COMPLETE"
        and facts["integrity_ok"]
        and facts["completeness_ok"]
        and facts["replayability"] != "NONE"
    )
    return facts


def _tick(value: bool | None) -> str:
    if value is None:
        return "未记录"
    return "✓" if value else "✗"


def render_evidence_lines(facts: dict) -> list[str]:
    """Render the facts as separate lines; the green claim needs all of them."""
    if not facts.get("capsule"):
        return [
            f"- **业务状态**:{facts.get('business_status', UNKNOWN)}",
            f"- **完好性(MANIFEST)**:{_tick(facts.get('integrity_ok'))}"
            f"(只回答「已列文件有没有被改」)",
            "- **完整性**:未知 —— 本 run 早于 forensic capsule,没有 expected 清单可比",
            "- **可重放**:未知 · **归档**:未知",
        ]
    coverage = facts.get("coverage") or {}
    agents = coverage.get("agents") or {}
    sources = coverage.get("sources") or {}
    missing = facts.get("missing_required") or []
    lines = [
        f"- **业务状态**:{facts['business_status']} · "
        f"**证据状态**:{facts['evidence_status']}",
        f"- **完好性**:{_tick(facts['integrity_ok'])}"
        f"(变 {len(facts['integrity_detail'].get('changed') or [])} · "
        f"缺 {len(facts['integrity_detail'].get('missing') or [])} · "
        f"多 {len(facts['integrity_detail'].get('extra') or [])} · "
        f"root 锚定 {_tick(facts['integrity_detail'].get('root_anchored'))})",
        f"- **完整性**:{_tick(facts['completeness_ok'])}"
        + (f"(缺 {len(missing)} 项:{', '.join(missing[:3])}"
           + ("…)" if len(missing) > 3 else ")") if missing else ""),
        f"- **可重放**:{facts['replayability']} · "
        f"**归档**:{facts['durability']}",
        f"- **覆盖**:agent {agents.get('present', 0)}/{agents.get('expected', 0)} · "
        f"取数 {sources.get('covered', 0)}/{sources.get('reads', 0)}",
        f"- **工作树**:{'脏树(见 identity/code.patch)' if facts.get('dirty_patch') else ('干净' if facts.get('dirty_patch') is False else '未记录')}",
        f"- **现场可复盘**:{'✓' if facts['green'] else '✗ —— 上面任一项不成立即不成立'}",
    ]
    return lines


__all__ = [
    "capsule_root",
    "evidence_facts",
    "has_capsule",
    "render_evidence_lines",
]
