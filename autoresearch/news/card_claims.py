"""Decision uses of existing material claims; no model-authored support verdicts.

The Markdown mapping is a declaration. This owner derives the card/frame/attempt
binding and effective inputs from frozen source evidence. Raw research text is
never rewritten, and declared coverage is not semantic completeness.
"""
from __future__ import annotations

import copy
import json
import re
from pathlib import Path

from autoresearch.common.atomic import atomic_write_json, canonical_json, sha256_bytes
from autoresearch.contracts.agent_output import OW_GATES, RUBRIC_DIMENSIONS
from autoresearch.news.material_claims import evaluate_material_claim, verify_material_claims
from autoresearch.trace.source_receipts import read_receipts

_FENCE = re.compile(r"```decision-claim-uses-v1\s*\n(.*?)\n```", re.S)
_FIELDS = {"claim_id", "statement_sha256", "target", "support_group", "relation", "rationale"}
_TARGETS = {"entry", "BACKGROUND"} | {f"dimensions.{key}" for key in RUBRIC_DIMENSIONS} | {f"gates.{key}" for key in OW_GATES}


def _mapping(text):
    matches = _FENCE.findall(text)
    if len(matches) > 1:
        raise ValueError("one decision-claim-uses-v1 block permitted")
    if not matches:
        return {"schema_version": 1, "declarations": [], "uses": []}
    value = json.loads(matches[0])
    if (not isinstance(value, dict) or set(value) != {"schema_version", "declarations", "uses"}
            or type(value["schema_version"]) is not int or value["schema_version"] != 1
            or not isinstance(value["declarations"], list) or not isinstance(value["uses"], list)):
        raise ValueError("invalid decision claim mapping")
    seen = set()
    for row in value["declarations"]:
        if (not isinstance(row, dict) or set(row) != {"claim_id", "statement"}
                or any(not isinstance(item, str) or not item.strip() for item in row.values())
                or row["claim_id"] in seen):
            raise ValueError("invalid or duplicate claim declaration")
        seen.add(row["claim_id"])
    seen = set()
    for row in value["uses"]:
        if (not isinstance(row, dict) or set(row) != _FIELDS
                or any(not isinstance(item, str) or not item.strip() for key, item in row.items() if key != "statement_sha256")
                or row["target"] not in _TARGETS or row["relation"] not in {"REQUIRED", "ALTERNATIVE", "BACKGROUND"}
                or (row["statement_sha256"] is not None and (not isinstance(row["statement_sha256"], str)
                    or not re.fullmatch("[0-9a-f]{64}", row["statement_sha256"])))
                or (row["target"] == "BACKGROUND") != (row["relation"] == "BACKGROUND")):
            raise ValueError("invalid decision claim use")
        key = (row["claim_id"], row["target"])
        if key in seen:
            raise ValueError("duplicate decision claim use")
        seen.add(key)
    return value


def _subject_key(value):
    value = str(value or "")
    return value.split(".", 1)[0] if re.fullmatch(r"[0-9]{6}(?:\.(?:SS|SZ|BJ))?", value) else value


def _source_origins(value):
    """Conservative publisher identity; bytes changing does not create a source."""
    from urllib.parse import urlsplit

    origins = set()
    def visit(item):
        if isinstance(item, dict):
            for child in item.values():
                visit(child)
        elif isinstance(item, list):
            for child in item:
                visit(child)
        elif isinstance(item, str) and item.startswith(("https://", "http://")):
            host = urlsplit(item).hostname
            if host:
                # Deliberately conservative for multi-label public suffixes.
                origins.add(".".join(host.lower().rstrip(".").split(".")[-2:]))
    visit(value)
    return origins


def claim_population(capsule, *, identity, accepted_attempts, frame, subject, task_subjects=None):
    """Accepted ancestors plus this exact attempt, including rejected intel facts."""
    root = Path(capsule)
    # Reuse the existing run-level integrity/coverage owner; its historical
    # aggregation is not a substitute for the accepted-attempt filter below.
    audit = verify_material_claims(root, decision_frame=frame)
    receipts = {row["receipt_id"]: row for row in read_receipts(root)}
    versions = {}
    for path in sorted((root / "evidence/material_claims").glob("*.json")):
        if identity.get("task_id") is None:
            raise ValueError("material population requires a root-bound card producer")
        value = json.loads(path.read_text())
        if value.get("engine") != identity["engine"] or value.get("run_id") != identity["run_id"]:
            raise ValueError("material claim engine/run identity mismatch")
        allowed = dict(accepted_attempts)
        if identity.get("task_id") is not None:
            allowed[identity["task_id"]] = identity["attempt"]
        if allowed.get(value.get("task_id")) != value.get("attempt"):
            continue  # Failed, late and other-stock attempts keep their history only.
        if path.stem != value.get("sidecar_id"):
            raise ValueError("material claim sidecar filename mismatch")
        producer_subject = (task_subjects or {}).get(value.get("task_id"))
        event_subject = (value.get("claim_event") or {}).get("subject_code")
        mismatch = any(item is not None and _subject_key(item) != _subject_key(subject)
                       for item in (producer_subject, event_subject))
        if mismatch and value.get("task_id") != identity["task_id"]:
            continue
        checked = evaluate_material_claim(root, value, decision_frame=frame)
        if mismatch:
            checked = dict(checked, verdict="UNKNOWN", semantic="UNKNOWN", reason="SUBJECT_MISMATCH")
        origins, related = set(), set()
        pending = list(value["source_receipt_ids"])
        lineage_complete = bool(pending)
        while pending:
            rid = pending.pop()
            if rid in related:
                continue
            related.add(rid)
            if rid not in receipts:
                lineage_complete = False
                continue
            origins.update(_source_origins(receipts[rid]["normalized_params"]))
            pending.extend(receipts[rid].get("supersedes_receipt_ids", []))
        fingerprints = {receipts[rid]["payload_hash"] for rid in value["source_receipt_ids"] if rid in receipts}
        versions.setdefault(value["claim_id"], []).append({
            **checked, "statement_sha256": value["statement_sha256"],
            "event_identity": sha256_bytes(canonical_json(value.get("claim_event")).encode()),
            "source_hashes": sorted(fingerprints), "source_origins": sorted(origins),
            "source_receipt_ids": sorted(related), "source_lineage_complete": lineage_complete, "sidecar_ids": [value["sidecar_id"]],
            "task_id": value["task_id"], "attempt": value["attempt"],
        })
    population = {}
    for key, rows in versions.items():
        hashes = {row["statement_sha256"] for row in rows}
        if len(hashes) != 1:
            raise ValueError(f"accepted statement versions conflict: {key}")
        row = dict(rows[0])
        event_conflict = len({item["event_identity"] for item in rows}) != 1
        if event_conflict:
            row["reason"] = "CURRENT_VERSION_AMBIGUOUS"
        for field in ("verdict", "source", "semantic", "timing", "conflict", "received_by_cutoff"):
            states = {item.get(field, "UNKNOWN") for item in rows}
            row[field] = next(iter(states)) if len(states) == 1 and not event_conflict else "UNKNOWN"
        row["source_hashes"] = sorted({item for version in rows for item in version["source_hashes"]})
        for field in ("source_origins", "source_receipt_ids"):
            row[field] = sorted({item for version in rows for item in version[field]})
        row["source_lineage_complete"] = all(version["source_lineage_complete"] for version in rows)
        row["sidecar_ids"] = sorted({item for version in rows for item in version["sidecar_ids"]})
        population[key] = row
    return population, audit


def evaluate_card_claims(text, *, card, frame, capsule, identity, accepted_attempts, frame_hash,
                         expected_card_sha256=None, task_subjects=None):
    from autoresearch.contracts.execution import validate_decision_frame
    validate_decision_frame(frame)
    if expected_card_sha256 is not None and sha256_bytes(text.encode()) != expected_card_sha256:
        raise ValueError("card bytes differ from accepted producer")
    if (set(identity) != {"engine", "run_id", "task_id", "attempt"}
            or identity["engine"] not in {"codex", "claude"}
            or not isinstance(identity["run_id"], str) or not identity["run_id"]
            or (identity["task_id"] is not None and (not isinstance(identity["task_id"], str)
                or type(identity["attempt"]) is not int or identity["attempt"] < 1))
            or not re.fullmatch("[0-9a-f]{64}", frame_hash)):
        raise ValueError("invalid root claim-use identity")
    mapping = _mapping(text)
    population, _audit = claim_population(capsule, identity=identity, accepted_attempts=accepted_attempts, frame=frame,
        subject=card.get("subject", card.get("code")), task_subjects=task_subjects)
    new_ids = set()
    for declaration in mapping["declarations"]:
        key = declaration["claim_id"]
        statement_hash = sha256_bytes(declaration["statement"].encode())
        if key in population and population[key]["statement_sha256"] != statement_hash:
            raise ValueError("declaration differs from accepted statement")
        if key not in population:
            new_ids.add(key)
        population.setdefault(key, {"claim_id": key, "statement_sha256": statement_hash,
            "verdict": "UNKNOWN", "source": "UNKNOWN", "semantic": "UNKNOWN", "timing": "UNKNOWN",
            "conflict": "UNKNOWN", "received_by_cutoff": "UNKNOWN", "reason": "SOURCE_NOT_BOUND",
            "source_hashes": [], "source_origins": [], "source_receipt_ids": [], "source_lineage_complete": False, "sidecar_ids": []})
    if (population or mapping["uses"]) and identity["task_id"] is None:
        raise ValueError("claim usage requires root-bound card producer")
    groups, covered = {}, set()
    for use in mapping["uses"]:
        claim = population.get(use["claim_id"])
        if use["statement_sha256"] is None and use["claim_id"] in new_ids:
            use["statement_sha256"] = claim["statement_sha256"]
        if claim is None or claim["statement_sha256"] != use["statement_sha256"]:
            raise ValueError("claim use has unknown identity or statement hash")
        covered.add(use["claim_id"])
        if use["target"] != "BACKGROUND":
            groups.setdefault((use["target"], use["support_group"]), []).append(use)
    effective, reviewed, gaps = copy.deepcopy(card), copy.deepcopy(card), []
    for (target, group), uses in groups.items():
        required = [use for use in uses if use["relation"] == "REQUIRED"]
        alternatives = [use for use in uses if use["relation"] == "ALTERNATIVE"]
        # Alternatives must replace an explicit premise, with identical statement
        # semantics and independent source bytes. A different rationale is not proof.
        unsatisfied = []
        for use in required:
            claim = population[use["claim_id"]]
            if claim["verdict"] == "PASS":
                continue
            replacement = any(
                alt["claim_id"] != use["claim_id"]
                and population[alt["claim_id"]]["verdict"] == "PASS"
                and population[alt["claim_id"]]["statement_sha256"] == claim["statement_sha256"]
                and bool(population[alt["claim_id"]]["source_hashes"])
                and claim["source_lineage_complete"] and population[alt["claim_id"]]["source_lineage_complete"]
                and bool(claim["source_origins"]) and bool(population[alt["claim_id"]]["source_origins"])
                and not any(a == b or a.endswith("." + b) or b.endswith("." + a)
                            for a in population[alt["claim_id"]]["source_origins"] for b in claim["source_origins"])
                and not set(population[alt["claim_id"]]["source_receipt_ids"]) & set(claim["source_receipt_ids"])
                and not set(population[alt["claim_id"]]["source_hashes"]) & set(claim["source_hashes"])
                for alt in alternatives)
            if not replacement:
                unsatisfied.append(use)
        if not required or unsatisfied:
            gaps.append({"target": target, "support_group": group, "status": "UNKNOWN",
                         "claim_ids": sorted(use["claim_id"] for use in uses)})
            if target != "entry":
                section, key = target.split(".", 1)
                demoted = "未核" if section == "dimensions" else "UNKNOWN"
                effective[section][key] = demoted
                # Review view: a premise this card declared itself (read from its frozen inputs; no
                # root source binding exists yet) is unverified, not unreviewed. Only when every
                # missing premise is such an own declaration does the model's reviewed value stand
                # for the review checks; any unverified upstream premise still removes it.
                if not required or any(use["claim_id"] not in new_ids for use in unsatisfied):
                    reviewed[section][key] = demoted
    missing = sorted(set(population) - covered)
    usage = {"schema_version": 1, "identity": dict(identity), "card_sha256": sha256_bytes(text.encode()),
        "frame_sha256": frame_hash, "claims": [population[key] for key in sorted(population)],
        "uses": mapping["uses"], "gaps": gaps,
        "coverage": {"known_claims": len(population), "mapped_claims": len(covered), "unmapped": missing,
                     "fraction": len(covered) / len(population) if population else None},
        "semantic_completeness": "UNKNOWN", "semantic_scope": "DECLARED_USES_ONLY_PROSE_NOT_VERIFIED",
        "entry_status": "PENDING_EVIDENCE" if missing or any(row["target"] == "entry" for row in gaps) else "UNCHANGED"}
    return {"effective_card": effective, "reviewed_card": reviewed, "usage": usage}


def bound_claim_context(handle, *, task=None, artifact_id=None):
    """Read root-owned task states; model fields never select accepted attempts."""
    owner = Path(handle.workspace) / "session/tasks.json"
    entries = {}
    if owner.is_file():
        document = json.loads(owner.read_text())
        if document["engine"] != handle.engine or document["run_id"] != handle.run_id:
            raise ValueError("claim task owner identity mismatch")
        entries = document["tasks"]
    if task is None or task.get("task_id") not in entries:
        candidates = [entry["spec"] for entry in entries.values()
                      if artifact_id in entry["spec"].get("output_artifact_ids", [])]
        if len(candidates) > 1:
            raise ValueError("ambiguous card producer")
        task = candidates[0] if candidates else None
    elif task is not None:
        task = entries[task["task_id"]]["spec"]
    task_id = task["task_id"] if task else None
    entry = entries.get(task_id, {})
    expected_hash = None
    if entry.get("state") == "SUCCEEDED":
        outputs = (task or {}).get("output_artifact_ids", [])
        selected = artifact_id or (outputs[0] if len(outputs) == 1 else None)
        expected_hash = entry.get("accepted_artifacts", {}).get(selected, {}).get("sha256")
        if expected_hash is None:
            expected_hash = next((row["sha256"] for row in entry.get("outputs", [])
                                  if row["artifact_id"] == selected), None)
        if expected_hash is None:
            raise ValueError("accepted card producer lacks output hash")
    accepted, pending = {}, list((task or {}).get("dependencies", []))
    seen = set()
    while pending:
        key = pending.pop()
        if key in seen:
            continue
        seen.add(key)
        ancestor = entries.get(key)
        if ancestor is None:
            continue
        pending.extend(ancestor["spec"].get("dependencies", []))
        if ancestor["state"] == "SUCCEEDED":
            accepted[key] = ancestor["attempt"]
    return {"identity": {"engine": handle.engine, "run_id": handle.run_id,
                         "task_id": task_id, "attempt": entry.get("attempt")},
            "accepted_attempts": accepted, "capsule": handle.capsule,
            "task_subjects": {key: value["spec"].get("subject") for key, value in entries.items()},
            "expected_card_sha256": expected_hash}


def validate_claimed_decision(text, *, subject, frame, frame_hash, context, holding=None, bands=None):
    from autoresearch.common.card_decision import card_from_decision_text, validate_decision_text
    venue = frame["venue"]
    if frame["usage"] == "scan" and re.fullmatch(r"[0-9]{6}", subject):
        from autoresearch.dataflows.symbol_utils import normalize_symbol
        venue = {"SS": "XSHG", "SZ": "XSHE", "BJ": "XBSE"}[normalize_symbol(subject).rsplit(".", 1)[-1]]
    card = card_from_decision_text(text, subject=subject, venue=venue,
        analysis_date=frame["analysis_session"], holding=frame["usage"] == "holding_review" if holding is None else holding)
    result = evaluate_card_claims(text, card=card, frame=frame, frame_hash=frame_hash, **context)
    usage = result["usage"]
    if usage["identity"]["task_id"] is not None:
        # A rejected candidate retains the same audit record as an accepted one.
        # Card bytes name the attachment; retries never overwrite an older use.
        from autoresearch.common.atomic import canonical_json
        binding_hash = sha256_bytes(canonical_json({key: usage[key] for key in
            ("identity", "card_sha256", "frame_sha256")}).encode())
        attachment = Path(context["capsule"]) / "evidence/card_claim_uses" / (binding_hash + ".json")
        if attachment.exists():
            if json.loads(attachment.read_text()) != usage:
                raise ValueError("card claim-use attachment identity conflict")
        else:
            atomic_write_json(attachment, usage)
    if usage["coverage"]["unmapped"]:
        raise ValueError("unmapped material claims: " + ",".join(usage["coverage"]["unmapped"]))
    if card["initial_rating"] in {"Buy", "Overweight"} and any(row["target"] != "entry" for row in usage["gaps"]):
        raise ValueError("Buy/Overweight lacks required material support; new attempt required")
    semantics = validate_decision_text(text, subject=subject, frame=frame, holding=holding, bands=bands,
                                       effective_card=result["effective_card"],
                                       reviewed_card=result["reviewed_card"], venue=venue)
    if usage["entry_status"] == "PENDING_EVIDENCE":
        semantics["execution"]["actionability_status"] = "PENDING_EVIDENCE"
    return {**semantics, "claim_usage": usage}


def registered_card_semantics(handle, text, *, subject, frame, frame_hash, task=None,
                              artifact_id=None, holding=None, bands=None):
    return validate_claimed_decision(text, subject=subject, frame=frame, frame_hash=frame_hash,
        context=bound_claim_context(handle, task=task, artifact_id=artifact_id), holding=holding, bands=bands)


def usage_note(usage):
    coverage = usage["coverage"]
    unknown = sum(row["verdict"] == "UNKNOWN" for row in usage["claims"])
    refuted = sum(row["verdict"] == "FAIL" for row in usage["claims"])
    return ("\n## 决策断言使用核验\n"
            f"已声明用途覆盖 {coverage['mapped_claims']}/{coverage['known_claims']}；UNKNOWN {unknown}，FAIL {refuted}。"
            "覆盖仅指已知声明人口，正文语义完整性 UNKNOWN；未证实不等于已证伪。\n"
            + "\n".join(f"- {row['claim_id']}: 来源 {row['source']} / 语义 {row['semantic']} / 时效 {row['timing']} / 冲突 {row['conflict']} / 截止前收到 {row['received_by_cutoff']}" for row in usage["claims"])
            + "\n" + "\n".join(f"- {row['target']} / {row['support_group']}: UNKNOWN（必要事实资格不足）" for row in usage["gaps"]) + "\n")


def constrain_claim_execution(block, pending_codes, selected_codes):
    """Retain E6 selection while withholding execution of its unsupported entries."""
    value = dict(block, claim_entry_pending=sorted(set(pending_codes)),
                 claim_selected_codes=sorted(set(selected_codes)))
    if set(pending_codes) & set(selected_codes):
        value.update(actionability_status="PENDING_EVIDENCE", first_available_session=None,
                     exec_lag=None, staleness_sessions=None)
    return value


def claim_usage_instruction():
    return ("\n## 决策断言用途（decision-claim-uses-v1）\n"
        "已知上游断言（包括被删/未核/失败断言）及本卡新事实均须映射；不得用空数组删除人口。"
        "输出唯一 JSON fence，schema_version=1，declarations=[{claim_id,statement}] 仅列新事实，"
        "uses=[{claim_id,statement_sha256,target,support_group,relation,rationale}]。"
        "已有claim复制下列冻结哈希；本卡新declarations的用途可填statement_sha256=null，由root对声明原文计算。"
        "target=dimensions.<六维原名>|gates.<三门原名>|entry|BACKGROUND；relation=REQUIRED|ALTERNATIVE|BACKGROUND。"
        "BACKGROUND 只能 target=BACKGROUND，不能作为评级或入场前提。ALTERNATIVE 必须同组另一个同义声明且有独立有效来源。"
        "机检只移除无支持事实资格，不把 UNKNOWN 写成 FAIL；必要证据不足须重新判断评级，不能借偏离理由保留不兼容 Buy。"
        "不填写支持 verdict；root 绑定实际卡字节、frame 与 task/attempt。无已知事实的可执行示例：\n"
        '```decision-claim-uses-v1\n{"schema_version":1,"declarations":[],"uses":[]}\n```\n')
