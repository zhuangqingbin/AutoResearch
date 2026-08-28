"""RunContract: canonical identity, integrity validation, and atomic persistence."""
from __future__ import annotations

import json
from datetime import datetime, timezone

import pytest

from autoresearch.common import workspace as ws
from autoresearch.scan.run_contract import (
    RunContract,
    load_run_contract,
    sha256_json,
    write_run_contract,
)

DATE = "2026-07-28"
NOW = datetime(2026, 7, 28, 12, 34, 56, 123456, tzinfo=timezone.utc)
CAPSULE_RUN_ID = "20260827T010203456789Z"
# capsule 契约的引擎必须等于**当前**引擎(引擎隔离守卫)。夹具因此不能写死 codex ——
# 写死会让整份文件只在 `AUTORESEARCH_ENGINE=codex` 下为真,换 Claude 会话跑就集体变红,
# 而红的是夹具、不是守卫。`OTHER_ENGINE` 专供「故意不匹配」的负例。
ENGINE = ws.ENGINE
CTX = f"context_{ENGINE}"
OTHER_ENGINE = next(name for name in ws.ENGINES if name != ENGINE)


def _build(user_config: dict | None = None, **overrides) -> RunContract:
    analysis_date = overrides.pop("analysis_date", DATE)
    return RunContract.build(
        analysis_date=analysis_date,
        user_config=user_config or {},
        pinned={"kept": [], "expired": []},
        data_policy={"source": "tushare", "cap_floor_yi": 30.0, "include_bj": True},
        stage_budgets={"l3_finalist_max": 10, "pinned_cap": 5, "pinned_ttl_days": 10},
        artifact_schema_versions={"market_pack": 1, "finalists": 1},
        git_sha="abc1234",
        now=NOW,
        **overrides,
    )


def _build_capsule(**overrides) -> RunContract:
    values = {
        "run_id": CAPSULE_RUN_ID,
        "workspace_path": ws.scan_run_root(CAPSULE_RUN_ID),
        "engine": ENGINE,
    }
    values.update(overrides)
    return _build(**values)


def test_config_hash_is_canonical_but_contract_identity_is_explicit():
    left = _build({"agents": {"l4_card": {"effort": "high"}}, "pinned": {"cap": 1}})
    right = _build({"pinned": {"cap": 1}, "agents": {"l4_card": {"effort": "high"}}})
    assert left.config_hash == right.config_hash
    assert left.contract_hash == right.contract_hash
    assert left.run_id == "20260728T123456123456Z"


def test_v3_contract_carries_engine_kind_workspace_and_session_ref(tmp_path):
    contract = _build_capsule(
        run_kind="scan-market",
        session_ref="01a03dbe-7173-76a3-ac96-919ae6936e71",
    )

    assert contract.schema_version == 3
    assert contract.run_id == "20260827T010203456789Z"
    assert contract.run_kind == "scan-market"
    assert contract.engine == ENGINE
    assert contract.workspace_path.endswith(contract.run_id)
    assert contract.session_ref.startswith("01a03dbe")
    path = write_run_contract(tmp_path / "run_contract.json", contract)
    assert load_run_contract(path) == contract


@pytest.mark.parametrize("run_id", ["", "../run", "20260827T01020345678Z"])
def test_build_rejects_invalid_injected_run_id(run_id):
    with pytest.raises(ValueError, match="run_id|AUTORESEARCH_RUN_ID"):
        _build(run_id=run_id)


def test_build_rejects_invalid_analysis_date_even_with_explicit_workspace(tmp_path):
    with pytest.raises(ValueError, match="scan date"):
        _build(analysis_date="../escape", workspace_path=tmp_path)


def test_injected_run_id_requires_explicit_allocated_workspace(monkeypatch):
    monkeypatch.delenv("AUTORESEARCH_RUN_ID", raising=False)
    with pytest.raises(ValueError, match="workspace_path.*required"):
        _build(run_id="20260827T010203456790Z")


def test_injected_run_id_cannot_borrow_ambient_workspace(monkeypatch):
    monkeypatch.setenv("AUTORESEARCH_RUN_ID", "20260827T010203456789Z")
    with pytest.raises(ValueError, match="workspace_path.*required"):
        _build(run_id="20260827T010203456790Z")


def test_ambient_run_cannot_mint_implicit_contract_identity(monkeypatch):
    monkeypatch.setenv("AUTORESEARCH_RUN_ID", "20260827T010203456789Z")
    with pytest.raises(ValueError, match="active AUTORESEARCH_RUN_ID"):
        _build()


def test_ambient_run_cannot_mint_identity_with_explicit_legacy_workspace(monkeypatch):
    monkeypatch.setenv("AUTORESEARCH_RUN_ID", CAPSULE_RUN_ID)
    with pytest.raises(ValueError, match="active AUTORESEARCH_RUN_ID"):
        _build(workspace_path=ws.context_root() / "scan" / DATE)


@pytest.mark.parametrize(
    "workspace_path",
    [
        "",
        "../../escape",
        f"{CTX}/scan_runs/{CAPSULE_RUN_ID}/staging/2026-07-28",
        f"{CTX}/scan_runs/20260827T010203456790Z",
    ],
)
def test_capsule_build_rejects_unsafe_or_mismatched_workspace(workspace_path):
    with pytest.raises(ValueError, match="workspace_path"):
        _build_capsule(workspace_path=workspace_path)


@pytest.mark.parametrize(
    "workspace_path",
    [
        f"{CTX}/scan_runs/wrong/../{CAPSULE_RUN_ID}",
        f"./{CTX}/scan_runs/{CAPSULE_RUN_ID}",
    ],
)
def test_capsule_build_rejects_noncanonical_workspace_alias(workspace_path):
    with pytest.raises(ValueError, match="workspace_path"):
        _build_capsule(workspace_path=workspace_path)


def test_legacy_build_rejects_predicted_capsule_workspace(monkeypatch):
    monkeypatch.delenv("AUTORESEARCH_RUN_ID", raising=False)
    predicted = NOW.strftime("%Y%m%dT%H%M%S%fZ")
    with pytest.raises(ValueError, match="workspace_path"):
        _build(workspace_path=ws.scan_run_root(predicted))


def test_capsule_build_preserves_safe_absolute_workspace_string():
    run_id = "20260827T010203456790Z"
    absolute = ws.scan_run_root(run_id).resolve()
    contract = _build_capsule(run_id=run_id, workspace_path=absolute)

    assert contract.workspace_path == str(absolute)


@pytest.mark.parametrize("run_kind", (1, "", "stock-research"))
def test_build_rejects_invalid_run_kind(run_kind):
    with pytest.raises(ValueError, match="run_kind"):
        _build(run_kind=run_kind)


@pytest.mark.parametrize("engine", (1, "codex", "claude", "unknown"))
def test_legacy_build_rejects_nonempty_or_nonstring_engine(engine):
    with pytest.raises(ValueError, match="engine"):
        _build(engine=engine)


@pytest.mark.parametrize("engine", ("", OTHER_ENGINE, 1))
def test_capsule_build_requires_matching_active_engine(engine):
    with pytest.raises(ValueError, match="engine"):
        _build_capsule(engine=engine)


@pytest.mark.parametrize("session_ref", ({}, [], 1, True, ""))
def test_build_rejects_invalid_session_ref(session_ref):
    with pytest.raises(ValueError, match="session_ref"):
        _build(session_ref=session_ref)


def test_legacy_build_defaults_to_existing_scan_workspace(monkeypatch):
    monkeypatch.delenv("AUTORESEARCH_RUN_ID", raising=False)
    contract = _build()
    assert contract.workspace_path == str(ws.context_root() / "scan" / DATE)


def test_contract_hash_covers_pinned_and_data_policy():
    base = _build()
    changed = RunContract.build(
        analysis_date=DATE,
        user_config={},
        pinned={"kept": [{"code": "000001"}], "expired": []},
        data_policy={"source": "tushare", "cap_floor_yi": 30.0, "include_bj": True},
        stage_budgets={"l3_finalist_max": 10, "pinned_cap": 5, "pinned_ttl_days": 10},
        artifact_schema_versions={"market_pack": 1, "finalists": 1},
        git_sha="abc1234",
        now=NOW,
    )
    assert changed.contract_hash != base.contract_hash


def test_write_and_load_round_trip(tmp_path):
    path = tmp_path / "run_contract.json"
    written = write_run_contract(path, _build())
    assert written == path
    loaded = load_run_contract(path)
    assert loaded.to_dict() == _build().to_dict()
    assert not (tmp_path / "run_contract.json.tmp").exists()


def test_load_rejects_tampered_contract(tmp_path):
    path = write_run_contract(tmp_path / "run_contract.json", _build())
    raw = json.loads(path.read_text(encoding="utf-8"))
    raw["data_policy"]["source"] = "em"
    path.write_text(json.dumps(raw), encoding="utf-8")
    with pytest.raises(ValueError, match="contract_hash"):
        load_run_contract(path)


# ── v2(2026-08-26 现场留存波):脏树 + prompt 指纹,且 v1 历史契约必须继续可读 ──

def test_v1_contract_still_loads_and_verifies(tmp_path):
    """全部历史 run 的契约都是 v1。读不了 = 把它们的 run_id/身份弄丢(publisher 的
    manifest、run_mode 的冻结快照都按它定位)。v2 只是加字段,v1 的 hash 按当年 payload 现算。"""
    # Golden hashes were produced by the pre-v3 implementation at 2193849.
    v1 = {
        "schema_version": 1,
        "analysis_date": DATE,
        "run_id": "20260728T123456123456Z",
        "created_at": "2026-07-28T12:34:56.123456Z",
        "git_sha": "abc1234",
        "user_config": {},
        "config_hash": "44136fa355b3678a1146ad16f7e8649e94fb4fc21fe77e8310c060f61caaff8a",
        "agents": {},
        "pinned": {"kept": [], "expired": []},
        "data_policy": {},
        "stage_budgets": {},
        "artifact_schema_versions": {},
        "contract_hash": "b6872447fd7e3c1ec0ceb5fe34ff5cf7722c26f78183ade279a2dc3eb51b81da",
    }

    p = tmp_path / "run_contract.json"
    p.write_text(json.dumps(v1, ensure_ascii=False), encoding="utf-8")
    loaded = load_run_contract(p)
    assert loaded.schema_version == 1
    assert loaded.git_dirty is False and loaded.dirty_paths == ()
    assert loaded.prompt_hashes == {}
    assert loaded.run_kind == "scan-market"
    assert loaded.engine == ""
    assert loaded.workspace_path == ""
    assert loaded.session_ref is None


def test_pre_v3_v2_golden_digest_still_loads(tmp_path):
    # Fixed pre-v3 digest: this test intentionally does not call sha256_json.
    v2 = {
        "schema_version": 2,
        "analysis_date": "2026-08-26",
        "run_id": "20260826T101112131415Z",
        "created_at": "2026-08-26T10:11:12.131415Z",
        "git_sha": "def5678",
        "user_config": {
            "agents": {"l4_card": {"effort": "high"}},
            "force_full": False,
        },
        "config_hash": "50b8530e33a49b0e787fbd48965a78cbb736db48b8216805c7d0006b9a4707f0",
        "agents": {"l4_card": {"effort": "high"}},
        "pinned": {"kept": [{"code": "600000"}], "expired": []},
        "data_policy": {"source": "tushare", "include_bj": True},
        "stage_budgets": {"l3_finalist_max": 10},
        "artifact_schema_versions": {"market_pack": 1},
        "git_dirty": True,
        "dirty_paths": [".claude/agents/l4-card.md"],
        "prompt_hashes": {
            ".claude/agents/l4-card.md": "0123456789abcdef"
        },
        "contract_hash": "29757cd81fa2435d51fb40aa1baabc8a9651d5f540e26b1e625ee792f38c1816",
    }
    path = tmp_path / "run_contract.json"
    path.write_text(json.dumps(v2), encoding="utf-8")

    loaded = load_run_contract(path)

    assert loaded.schema_version == 2
    assert loaded.contract_hash == v2["contract_hash"]
    assert loaded.engine == ""


def test_v2_contract_still_verifies_without_v3_fields(tmp_path):
    raw = _build(
        git_dirty=True,
        dirty_paths=["prompt.md"],
        prompt_hashes={"prompt.md": "abc"},
    ).to_dict()
    raw["schema_version"] = 2
    for key in ("run_kind", "engine", "workspace_path", "session_ref"):
        raw.pop(key)
    raw["contract_hash"] = sha256_json(
        {key: value for key, value in raw.items() if key != "contract_hash"}
    )

    path = tmp_path / "run_contract.json"
    path.write_text(json.dumps(raw), encoding="utf-8")
    loaded = load_run_contract(path)

    assert loaded.schema_version == 2
    assert loaded.git_dirty is True
    assert loaded.prompt_hashes == {"prompt.md": "abc"}
    assert loaded.run_kind == "scan-market"
    assert loaded.engine == ""
    assert loaded.workspace_path == ""
    assert loaded.session_ref is None


def test_v2_contract_cannot_smuggle_unhashed_v3_identity(tmp_path):
    raw = _build(git_dirty=False, dirty_paths=[]).to_dict()
    raw["schema_version"] = 2
    for key in ("run_kind", "engine", "workspace_path", "session_ref"):
        raw.pop(key)
    raw["contract_hash"] = sha256_json(
        {key: value for key, value in raw.items() if key != "contract_hash"}
    )
    raw.update(
        engine=ENGINE,
        workspace_path=f"{CTX}/scan_runs/unhashed",
        session_ref="unhashed-session",
    )
    path = tmp_path / "run_contract.json"
    path.write_text(json.dumps(raw), encoding="utf-8")

    loaded = load_run_contract(path)

    assert loaded.engine == ""
    assert loaded.workspace_path == ""
    assert loaded.session_ref is None


def test_load_rejects_boolean_schema_version_before_hash_validation(tmp_path):
    raw = _build(git_dirty=False, dirty_paths=[]).to_dict()
    raw["schema_version"] = True
    path = tmp_path / "run_contract.json"
    path.write_text(json.dumps(raw), encoding="utf-8")

    with pytest.raises(ValueError, match="schema_version"):
        load_run_contract(path)


@pytest.mark.parametrize("missing", ("run_kind", "engine", "workspace_path", "session_ref"))
def test_v3_load_requires_every_physical_v3_field(tmp_path, missing):
    raw = _build().to_dict()
    raw.pop(missing)
    raw["contract_hash"] = sha256_json(
        {key: value for key, value in raw.items() if key != "contract_hash"}
    )
    path = tmp_path / f"missing-{missing}.json"
    path.write_text(json.dumps(raw), encoding="utf-8")

    with pytest.raises(ValueError, match=f"missing v3 field.*{missing}"):
        load_run_contract(path)


@pytest.mark.parametrize(
    "workspace_path",
    [
        "",
        "../../escape",
        f"{CTX}/scan/2026-07-28/child",
        f"{CTX}/scan_runs/20260827T999999999999Z",
    ],
)
def test_load_rejects_rehashed_v3_with_invalid_workspace(
    tmp_path, workspace_path
):
    raw = _build().to_dict()
    raw["workspace_path"] = workspace_path
    raw["contract_hash"] = sha256_json(
        {key: value for key, value in raw.items() if key != "contract_hash"}
    )
    path = tmp_path / "run_contract.json"
    path.write_text(json.dumps(raw), encoding="utf-8")

    with pytest.raises(ValueError, match="workspace_path"):
        load_run_contract(path)


def test_load_rejects_rehashed_v3_with_lexical_workspace_escape(tmp_path):
    raw = _build_capsule().to_dict()
    raw["workspace_path"] = (
        f"{CTX}/scan_runs/wrong/../{CAPSULE_RUN_ID}"
    )
    raw["contract_hash"] = sha256_json(
        {key: value for key, value in raw.items() if key != "contract_hash"}
    )
    path = tmp_path / "run_contract.json"
    path.write_text(json.dumps(raw), encoding="utf-8")

    with pytest.raises(ValueError, match="workspace_path"):
        load_run_contract(path)


@pytest.mark.parametrize(
    ("field", "invalid"),
    [
        ("run_kind", "stock-research"),
        ("run_kind", 1),
        ("engine", OTHER_ENGINE),
        ("engine", 1),
        ("session_ref", ""),
        ("session_ref", {"id": "session"}),
    ],
)
def test_load_rejects_rehashed_v3_with_invalid_identity_field(
    tmp_path, field, invalid
):
    raw = _build_capsule(session_ref="session-1").to_dict()
    raw[field] = invalid
    raw["contract_hash"] = sha256_json(
        {key: value for key, value in raw.items() if key != "contract_hash"}
    )
    path = tmp_path / f"invalid-{field}.json"
    path.write_text(json.dumps(raw), encoding="utf-8")

    with pytest.raises(ValueError, match=field):
        load_run_contract(path)


def test_load_rejects_rehashed_legacy_v3_with_nonempty_engine(tmp_path):
    raw = _build().to_dict()
    raw["engine"] = ENGINE
    raw["contract_hash"] = sha256_json(
        {key: value for key, value in raw.items() if key != "contract_hash"}
    )
    path = tmp_path / "run_contract.json"
    path.write_text(json.dumps(raw), encoding="utf-8")

    with pytest.raises(ValueError, match="engine"):
        load_run_contract(path)


@pytest.mark.parametrize(
    ("field", "unsafe"),
    [("run_id", "../escape"), ("analysis_date", "../../escape")],
)
def test_load_rejects_rehashed_v3_with_unsafe_path_identity(tmp_path, field, unsafe):
    raw = _build().to_dict()
    raw[field] = unsafe
    raw["contract_hash"] = sha256_json(
        {key: value for key, value in raw.items() if key != "contract_hash"}
    )
    path = tmp_path / "run_contract.json"
    path.write_text(json.dumps(raw), encoding="utf-8")

    with pytest.raises(ValueError, match="run_id|scan date"):
        load_run_contract(path)


def test_v1_hash_payload_excludes_v2_fields():
    """变异探针:若 `_hash_payload` 不再按 schema_version 排除 v2 三键,上面那份 v1 契约
    立刻报 hash mismatch —— 全部历史 run 一起读不出来。"""
    from autoresearch.scan.run_contract import _V2_FIELDS
    c = _build()
    assert c.schema_version == 3
    assert all(f in c._hash_payload() for f in _V2_FIELDS)
    v1_like = RunContract(**{**c.to_dict(), "schema_version": 1})
    assert not any(f in v1_like._hash_payload() for f in _V2_FIELDS)


def test_v2_hash_payload_excludes_v3_fields():
    from autoresearch.scan.run_contract import _V3_FIELDS

    contract = _build_capsule(session_ref="session-1")
    assert all(field in contract._hash_payload() for field in _V3_FIELDS)
    v2_like = RunContract(**{**contract.to_dict(), "schema_version": 2})
    assert not any(field in v2_like._hash_payload() for field in _V3_FIELDS)


def test_prompt_hashes_and_dirty_enter_contract_hash():
    """prompt 换了内容 → 契约身份必须跟着变。否则「本地改过 agent def 跑的那趟」
    与「干净树跑的那趟」在账面上一模一样。"""
    base = RunContract.build(
        analysis_date=DATE, user_config={}, pinned={}, data_policy={},
        stage_budgets={}, artifact_schema_versions={}, git_sha="abc", now=NOW,
        git_dirty=False, dirty_paths=[], prompt_hashes={"a.md": "11"})
    changed = RunContract.build(
        analysis_date=DATE, user_config={}, pinned={}, data_policy={},
        stage_budgets={}, artifact_schema_versions={}, git_sha="abc", now=NOW,
        git_dirty=False, dirty_paths=[], prompt_hashes={"a.md": "22"})
    dirty = RunContract.build(
        analysis_date=DATE, user_config={}, pinned={}, data_policy={},
        stage_budgets={}, artifact_schema_versions={}, git_sha="abc", now=NOW,
        git_dirty=True, dirty_paths=["x.py"], prompt_hashes={"a.md": "11"})
    assert base.contract_hash != changed.contract_hash
    assert base.contract_hash != dirty.contract_hash
    assert base.git_sha == changed.git_sha == dirty.git_sha   # 同一个 HEAD 却三种身份


def test_resolve_git_dirty_reports_paths(tmp_path, monkeypatch):
    from autoresearch.scan import run_contract as rc

    class _Proc:
        stdout = " M a.py\n?? b/c.md\n\n"

    monkeypatch.setattr(rc.subprocess, "run", lambda *a, **k: _Proc())
    dirty, paths = rc.resolve_git_dirty(tmp_path)
    assert dirty is True and paths == ["a.py", "b/c.md"]


def test_resolve_git_dirty_degrades_when_not_a_repo(tmp_path, monkeypatch):
    """git 不可用 → (False, []) 而不是抛。此时 `git_sha` 已经是 "unknown",两条合读不误判。"""
    from autoresearch.scan import run_contract as rc

    def _boom(*a, **k):
        raise OSError("no git")

    monkeypatch.setattr(rc.subprocess, "run", _boom)
    assert rc.resolve_git_dirty(tmp_path) == (False, [])


def test_dirty_paths_are_capped():
    from autoresearch.scan import run_contract as rc

    class _Proc:
        stdout = "".join(f" M f{i}.py\n" for i in range(rc.DIRTY_PATHS_CAP + 10))

    import unittest.mock as mock
    with mock.patch.object(rc.subprocess, "run", lambda *a, **k: _Proc()):
        dirty, paths = rc.resolve_git_dirty(".")
    assert dirty is True and len(paths) == rc.DIRTY_PATHS_CAP


def test_unsupported_schema_still_rejected(tmp_path):
    p = tmp_path / "c.json"
    p.write_text(json.dumps({**_build().to_dict(), "schema_version": 99}), encoding="utf-8")
    with pytest.raises(ValueError, match="unsupported run contract schema_version"):
        load_run_contract(p)
