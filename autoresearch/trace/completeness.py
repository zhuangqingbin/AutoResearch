"""Is the evidence complete? — answered separately from "was it tampered with".

`MANIFEST.sha256` answers only integrity: *the files I listed still hash the
same*.  It cannot answer completeness, because a file that was never written is
never listed.  This module expands a :class:`~autoresearch.contracts.profiles.RunProfile`
into every expected item, checks each one against the capsule, and reports
`REQUIRED / PRESENT / MISSING / NOT_EXPECTED / NOT_REACHED` with a reason.

It deliberately never calls MANIFEST verification: a run may be intact and
incomplete, or complete and tampered with, and collapsing the two is exactly
the false green this whole capsule exists to remove.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

# 阶段 / 角色词汇的**唯一**来源。此前这里从 `run_profile` 里函数级 import 一份 ROLE_STAGES,
# 于是「该有什么」的分母在这层又长出一个可以独立漂移的副本(spec 2026-08-29 §2.2 K3)。
from autoresearch.contracts.stages import ROLE_STAGES
# profile 工厂**按 kind 动态取**(`contracts.profiles.PROFILE_FACTORIES` 旁有设计意图):
# 静态 import `scan.run_profile` / `analyze.run_profile` 都是 `trace` 向上的边。
from autoresearch.contracts.profiles import RunProfile, profile_factory
from autoresearch.trace.atomic import atomic_write_json

#: 冻结的 `verification/profile.json` 没记 kind 时按谁展开。v1 profile.json(2026-08-31
#: kind 化之前的全部历史 capsule)只有一个 kind 存在过,所以这个落回不是猜。
_DEFAULT_RUN_KIND = "scan-market"

SCHEMA_VERSION = 1

REQUIRED = "REQUIRED"
PRESENT = "PRESENT"
MISSING = "MISSING"
NOT_EXPECTED = "NOT_EXPECTED"
NOT_REACHED = "NOT_REACHED"


@dataclass(frozen=True)
class ExpectedItem:
    key: str
    selector: str
    source: str
    disposition: str
    reason: str
    #: D6.5 — only fed from `ArtifactRule.evidence_level`.  Stage/role-derived items
    #: (the two loops below that never come from an `ArtifactRule`) keep the "L0"
    #: default: they are existence checks by construction, nothing more.
    evidence_level: str = "L0"

    def to_dict(self) -> dict:
        return {
            "key": self.key,
            "selector": self.selector,
            "source": self.source,
            "disposition": self.disposition,
            "reason": self.reason,
            "evidence_level": self.evidence_level,
        }


@dataclass(frozen=True)
class ExpectedEvidence:
    items: tuple[ExpectedItem, ...]

    def rule(self, selector: str) -> ExpectedItem:
        for item in self.items:
            if item.selector == selector:
                return item
        raise KeyError(f"no expected rule for selector: {selector!r}")

    def to_dict(self) -> dict:
        return {
            "schema_version": SCHEMA_VERSION,
            "items": [item.to_dict() for item in self.items],
        }


def _rule_applies(profile: RunProfile, required_when: str) -> tuple[bool, str]:
    if required_when == "always":
        return True, "required for every run"
    if required_when == "llm_run":
        applies = bool([r for r in profile.agent_roles if profile.role_expected(r)])
        return applies, "run dispatches business agents" if applies else (
            "no business agent leg in this mode"
        )
    if required_when == "failure":
        applies = profile.business_status in {"FAILED", "INTERRUPTED"}
        return applies, (
            "terminal failure owes failure evidence"
            if applies
            else "run did not fail"
        )
    if required_when == "replayable":
        applies = bool(profile.replayable_stages)
        return applies, (
            "run has deterministically replayable stages"
            if applies
            else "no replayable stage"
        )
    raise ValueError(f"unknown required_when: {required_when!r}")


def build_expected(profile: RunProfile) -> ExpectedEvidence:
    """Expand a profile into every expected item, before touching any file."""
    items: list[ExpectedItem] = []
    for rule in profile.artifact_rules:
        applies, reason = _rule_applies(profile, rule.required_when)
        items.append(
            ExpectedItem(
                key=rule.key,
                selector=rule.selector,
                source=rule.source,
                disposition=REQUIRED if applies else NOT_EXPECTED,
                reason=reason,
                evidence_level=rule.evidence_level,
            )
        )
    for stage in profile.expected_stages:
        if profile.stage_reached(stage):
            items.append(
                ExpectedItem(
                    key=f"stage:{stage}",
                    selector=f"stages/{stage}/*/result.json",
                    source="capsule",
                    disposition=REQUIRED,
                    reason="stage was reached and owes one attempt result",
                )
            )
            owes_logs = profile.owes_captured_logs(stage)
            for channel in ("stdout", "stderr"):
                items.append(
                    ExpectedItem(
                        key=f"log:{stage}:{channel}",
                        selector=f"logs/{stage}/*.{channel}.log.gz",
                        source="capsule",
                        disposition=REQUIRED if owes_logs else NOT_EXPECTED,
                        reason=(
                            "reached stage owes its captured command output"
                            if owes_logs
                            else "this run kind captures no command output (in-process "
                            "checkpoints, no traced shell)"
                        ),
                    )
                )
        else:
            items.append(
                ExpectedItem(
                    key=f"stage:{stage}",
                    selector=f"stages/{stage}/*",
                    source="capsule",
                    disposition=NOT_REACHED,
                    reason=f"run stopped at {profile.last_stage!r} before {stage!r}",
                )
            )
    for role in (*profile.agent_roles, *sorted(profile.conditional_roles)):
        expected = profile.role_expected(role)
        conditional = role in profile.conditional_roles
        stage = ROLE_STAGES.get(role)
        # 模式本身就没有这条腿 = NOT_EXPECTED;有这条腿但没跑到 = NOT_REACHED。
        unreached = (
            stage is not None
            and stage in profile.expected_stages
            and not profile.stage_reached(stage)
        )
        items.append(
            ExpectedItem(
                key=f"agent:{role}",
                selector=f"agents/{role}/*",
                source="agent_index",
                disposition=(
                    REQUIRED
                    if expected and not conditional
                    else (NOT_REACHED if unreached else NOT_EXPECTED)
                ),
                reason=(
                    "role is dispatched in this mode"
                    if expected and not conditional
                    else (
                        f"stage {stage!r} was never reached"
                        if unreached
                        else (
                            "conditional leg: only owed when triggered"
                            if conditional
                            else "role is not dispatched in this mode"
                        )
                    )
                ),
            )
        )
    return ExpectedEvidence(items=tuple(items))


def _agent_index(capsule: Path) -> dict:
    path = capsule / "agents/index.json"
    if not path.is_file():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def _capsule_hit(capsule: Path, selector: str) -> bool:
    if "*" in selector:
        return any(match.exists() for match in capsule.glob(selector))
    return (capsule / selector).exists()


def _agent_hit(index: dict, role: str) -> bool:
    return any(
        row.get("role") == role and row.get("status") == "PRESENT"
        for row in index.get("invocations", [])
    )


def _read_rows(capsule: Path) -> list[dict]:
    path = capsule / "lineage/reads.jsonl"
    if not path.is_file():
        return []
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def source_coverage(capsule: Path) -> dict:
    """How many recorded reads still have their exact bytes inside the capsule."""
    rows = _read_rows(capsule)
    covered = 0
    uncovered: list[str] = []
    for row in rows:
        digest = row.get("blob_hash") or row.get("normalized_blob_hash")
        if digest and (capsule / "blobs/sha256" / digest[:2] / digest).is_file():
            covered += 1
        else:
            uncovered.append(str(row.get("endpoint") or row.get("key") or "(unnamed)"))
    return {
        "reads": len(rows),
        "covered": covered,
        "uncovered": sorted(set(uncovered)),
        "ok": bool(rows) and not uncovered,
    }


def agent_coverage(capsule: Path) -> dict:
    index = _agent_index(capsule)
    coverage = index.get("coverage") or {}
    return {
        "expected": int(coverage.get("expected") or 0),
        "present": int(coverage.get("present") or 0),
        "missing": int(coverage.get("missing") or 0),
        "ok": int(coverage.get("missing") or 0) == 0,
    }


def _level_counts(results: list[dict]) -> dict[str, dict[str, int]]:
    """Bucket the REQUIRED tally by `evidence_level` (D6.5).

    Only levels a rule actually declares show up — a report saying "X% at L2"
    needs a denominator that counts just the rules that exist at that level, not
    three levels each padded to 0/0.  Disposition itself is untouched: this reads
    the same PRESENT/MISSING values `counts` already computed, it just re-groups
    them by an orthogonal key.
    """
    levels: dict[str, dict[str, int]] = {}
    for row in results:
        if row["disposition"] not in (PRESENT, MISSING):
            continue
        bucket = levels.setdefault(row["evidence_level"], {"required": 0, "hit": 0})
        bucket["required"] += 1
        if row["disposition"] == PRESENT:
            bucket["hit"] += 1
    return levels


def replay_state(capsule: Path) -> str:
    path = capsule / "verification/replay.json"
    if not path.is_file():
        return "NONE"
    payload = json.loads(path.read_text(encoding="utf-8"))
    return str(payload.get("replayability") or "NONE")


def evaluate(
    capsule: Path | str,
    profile: RunProfile | None = None,
    *,
    durability: str = "PENDING",
) -> dict:
    """Check every expected item against the capsule and report the counts.

    This never verifies MANIFEST: integrity and completeness are two different
    questions and must be able to disagree.
    """
    root = Path(capsule)
    resolved = profile if profile is not None else profile_from_capsule(root)
    expected = build_expected(resolved)
    index = _agent_index(root)

    results: list[dict] = []
    missing_required: list[str] = []
    for item in expected.items:
        row = item.to_dict()
        if item.disposition == REQUIRED:
            if item.source == "agent_index":
                hit = _agent_hit(index, item.key.split(":", 1)[1])
            else:
                hit = _capsule_hit(root, item.selector)
            row["disposition"] = PRESENT if hit else MISSING
            if not hit:
                missing_required.append(item.selector)
        results.append(row)

    counts = {
        "required": sum(
            1 for row in results if row["disposition"] in {PRESENT, MISSING}
        ),
        "present": sum(1 for row in results if row["disposition"] == PRESENT),
        "missing": len(missing_required),
        "not_expected": sum(
            1 for row in results if row["disposition"] == NOT_EXPECTED
        ),
        "not_reached": sum(1 for row in results if row["disposition"] == NOT_REACHED),
    }
    agents = agent_coverage(root)
    sources = source_coverage(root)
    # 一个被派发过、却没有 transcript 的 agent invocation 就是 GONE ——
    # 设计稿 §8.5 规则 4:LLM run 里出现 GONE/AMBIGUOUS,completeness_ok 必须为 false。
    # 只报覆盖率而不进结论,等于把这条规则写在文档里、不写在代码里。
    if not agents["ok"]:
        missing_required.append(
            f"agents/index.json: {agents['missing']} reached invocation(s) without a transcript"
        )
    return {
        "schema_version": SCHEMA_VERSION,
        "completeness_ok": not missing_required,
        "counts": counts,
        "levels": _level_counts(results),
        "missing_required": sorted(missing_required),
        "not_expected": sorted(
            row["selector"] for row in results if row["disposition"] == NOT_EXPECTED
        ),
        "not_reached": sorted(
            row["selector"] for row in results if row["disposition"] == NOT_REACHED
        ),
        "warnings": [],
        "coverage": {
            "agents": agents,
            "sources": sources,
            "replay": replay_state(root),
        },
        "durability": durability,
        "items": results,
    }


def profile_from_capsule(capsule: Path | str, *, kind: str | None = None) -> RunProfile:
    """Recover the run's declared profile, or rebuild it from the frozen state.

    kind 的来源按可信度排序:显式参数 → 冻结的 `profile.json` 里的 `kind` →
    `_DEFAULT_RUN_KIND`。**不**从别处猜:一份被展开成错 kind 的 expected 清单,
    每一行都是假的。
    """
    root = Path(capsule)
    stored = root / "verification/profile.json"
    if stored.is_file():
        payload = json.loads(stored.read_text(encoding="utf-8"))
        factory = profile_factory(
            str(kind or payload.get("kind") or _DEFAULT_RUN_KIND)
        )
        return factory(
            mode=str(payload.get("mode") or "FULL"),
            business_status=str(payload.get("business_status") or "SUCCEEDED"),
            last_stage=payload.get("last_stage"),
            agent_roles=tuple(payload["agent_roles"])
            if payload.get("agent_roles") is not None
            else None,
        )
    return profile_factory(str(kind or _DEFAULT_RUN_KIND))()


def write_expected(
    capsule: Path | str,
    profile: RunProfile,
) -> Path:
    """Freeze the expectation *and* the profile that produced it."""
    root = Path(capsule)
    atomic_write_json(
        root / "verification/profile.json",
        {
            "schema_version": SCHEMA_VERSION,
            "kind": profile.kind,
            "mode": profile.mode,
            "business_status": profile.business_status,
            "last_stage": profile.last_stage,
            "agent_roles": list(profile.agent_roles),
            "expected_stages": list(profile.expected_stages),
            "replayable_stages": list(profile.replayable_stages),
        },
    )
    return atomic_write_json(
        root / "verification/expected.json", build_expected(profile).to_dict()
    )


def write_completeness(
    capsule: Path | str,
    profile: RunProfile | None = None,
    *,
    durability: str = "PENDING",
) -> dict:
    root = Path(capsule)
    # coverage.json 必须在**评估之前**落盘:它自己就是 expected 清单里的一条
    # REQUIRED 规则,评估跑在它前面就会把「还没写」判成「缺失」——检查者跑在
    # 被检查者前面,永远差一拍。
    atomic_write_json(root / "lineage/coverage.json", source_coverage(root))
    result = evaluate(root, profile, durability=durability)
    atomic_write_json(root / "verification/completeness.json", result)
    return result


__all__ = [
    "ExpectedEvidence",
    "ExpectedItem",
    "agent_coverage",
    "build_expected",
    "evaluate",
    "profile_from_capsule",
    "replay_state",
    "source_coverage",
    "write_completeness",
    "write_expected",
]
