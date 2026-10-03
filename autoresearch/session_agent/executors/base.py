"""Inference executor protocol — the runner ↔ executor boundary of session_v1.

The runner (``session_agent.runner``) claims one INFERENCE task attempt, renders a
complete :class:`DispatchRequest` and hands it to an executor.  The executor's only job
is to make a model write the declared output files and to report *who* did it
(:class:`DispatchResult`).  Everything else stays on the runner side:

- **Interpretation happens once, before the executor.**  ``agent_type`` / ``model`` /
  ``effort`` are resolved by the runner through ``scan.user_config.resolve_agent_bundle``
  (the single interpretation point); the prompt text and every input/output path are
  rendered by the runner.  Executors pass them through verbatim — they never re-resolve.
- **The driver trusts only files.**  An executor's ``ok=True`` is not evidence of an
  output: the runner re-hashes every ``output_paths`` file itself and the service
  validates the registered output contract before a result is accepted.
- **Idempotency per (run_id, task_id, attempt).**  ``dispatch`` may be called again for
  an attempt whose previous runner process died between claim and submit.  An executor
  that can re-attach to the in-flight/finished work of that attempt (instead of launching
  a second agent) sets ``supports_reattach = True``; otherwise the runner never calls
  ``dispatch`` twice for one attempt and reports the attempt as an orphan.

Error semantics of ``dispatch``:

- return ``DispatchResult(ok=True, ...)`` after the model finished writing;
- return ``DispatchResult(ok=False, error=..., error_class=...)`` for an observed failure;
- raise :class:`ExecutorTimeout` when no result arrived within
  ``request.timeout_seconds`` → the runner records ``TIMEOUT`` (a
  ``contracts.retry.TASK_ATTEMPT`` class: retried once with a *new* attempt, so a late
  result of the timed-out attempt can never be accepted);
- any other exception → ``AGENT_ERROR`` (classified by :func:`classify_error`).

On ``ExecutorTimeout`` the runner freezes an ``ABANDONED`` attempt record (the timeout
message is its reason): the evidence closure then does not demand a transcript / web
receipts that cannot exist.  An executor that can still observe a *late* result of such an
attempt may expose ``late_results() -> list[tuple[DispatchRequest, DispatchResult]]``
(mailbox does): the runner binds that transcript to the abandoned attempt as evidence only
(never a submission), each round and once more right before ``finish``.

Batch 4 (headless ``claude -p`` executor) implements the same protocol; see
``DispatchRequest.max_turns`` / ``tier`` and ``DispatchResult.usage``.
"""
from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass, field, fields
from types import MappingProxyType
from typing import Protocol, runtime_checkable

from autoresearch.contracts.retry import TASK_ATTEMPT
from autoresearch.session_agent.roles import codex_agent_names, dispatch_mapping

SCHEMA_VERSION = 1
#: Run-scoped mailbox / runner status directory: ``<staging>/_dispatch/``.
DISPATCH_DIR = "_dispatch"

# Compatibility views; role/config/host mappings are owned by roles.py.

ROLE_DISPATCH = MappingProxyType(dispatch_mapping())
CODEX_AGENT_NAMES = MappingProxyType(codex_agent_names())


def agent_type_for(role_id: str, engine: str) -> str:
    """The project agent a host of ``engine`` dispatches for one session role."""
    if engine not in {"claude", "codex"}:
        raise KeyError(f"unsupported engine: {engine}")
    agent_type, config_role = ROLE_DISPATCH[role_id]
    if engine == "claude":
        return agent_type
    if config_role is None or config_role not in CODEX_AGENT_NAMES:
        raise KeyError(f"no Codex project agent for session role {role_id}")
    return CODEX_AGENT_NAMES[config_role]


#: Seconds without a result before ``ExecutorTimeout``.  Host mode (mailbox): the clock
#: starts when the host *takes* the request; an untaken request waits
#: ``mailbox.NEVER_TAKEN_FACTOR`` × this.  Generous on purpose: a timed-out subagent is not
#: killed.  An executor that raises ``ExecutorTimeout`` must make that attempt's late
#: result unacceptable (mailbox: ``<stem>.abandoned``, written atomically with the decision).
DEFAULT_TIMEOUTS: Mapping[str, float] = MappingProxyType({
    "macro.brief": 900.0,
    "sector.brief": 600.0,
    "scan.l3": 2400.0,
    "scan.l3.repair": 600.0,
    "scan.l4.intel": 900.0,
    "scan.l4.card": 1800.0,
    "scan.l4.review": 1800.0,
})
FALLBACK_TIMEOUT = 1800.0


class ExecutorTimeout(TimeoutError):
    """No result arrived for one attempt within its timeout (retryable once)."""


class ExecutorUnavailable(RuntimeError):
    """The executor cannot serve this run at all (misconfiguration, not a task failure)."""


def _frozen_map(value) -> Mapping:
    return MappingProxyType(dict(value or {}))


@dataclass(frozen=True)
class DispatchRequest:
    """Everything an executor needs to run one inference attempt — nothing to resolve."""

    run_id: str
    engine: str                       # claude | codex (the run's engine root)
    task_id: str
    attempt: int                      # session attempt (retries get a new number)
    role: str                         # session role id (``roles.py``), e.g. ``scan.l4.card``
    agent_type: str                   # engine-specific: ``.claude/agents/<agent_type>.md`` (claude)
                                      # / ``.codex/agents/*.toml`` ``name`` (codex, e.g. "L4 card")
    config_role: str | None           # scan_config ``agents`` key, e.g. ``l4_card``
    model: str | None                 # resolved; ``None`` = the agent definition decides
    effort: str | None                # resolved effort (claude) / reasoning_effort (codex)
    agent_spec: Mapping               # full resolved spec for ``config_role``, verbatim
    tier: str | None                  # scan_config ``agents.<config_role>.tier``
    max_turns: int | None             # ``None`` = executor default (mailbox ignores it)
    prompt: str                       # the exact dispatch prompt (legacy wording for scan)
    instruction_refs: tuple[str, ...]
    input_paths: Mapping[str, str]    # artifact_id → path; frozen inputs, audit only
    output_paths: Mapping[str, str]   # artifact_id → path the agent must write
    subject: str | None
    independent_context: bool         # True → needs a host receipt with transcript evidence
    tool_policy: str
    timeout_seconds: float
    host_session_ref: str             # the host session (parent context of a subagent)
    resolution: str = "RESOLVED"      # how model/effort were obtained (see runner/dispatch)
    schema_version: int = SCHEMA_VERSION
    access_manifest_path: str | None = None  # absent in historical frozen requests

    def __post_init__(self):
        object.__setattr__(self, "agent_spec", _frozen_map(self.agent_spec))
        object.__setattr__(self, "input_paths", _frozen_map(self.input_paths))
        object.__setattr__(self, "output_paths", _frozen_map(self.output_paths))
        object.__setattr__(self, "instruction_refs", tuple(self.instruction_refs))
        if type(self.attempt) is not int or self.attempt < 1:
            raise ValueError("dispatch attempt must be a positive integer")
        if not self.task_id or not self.agent_type or not self.prompt:
            raise ValueError("dispatch request requires task_id, agent_type and prompt")

    def to_json(self) -> dict:
        value = {}
        for item in fields(self):
            current = getattr(self, item.name)
            if isinstance(current, Mapping):
                current = dict(current)
            elif isinstance(current, tuple):
                current = list(current)
            value[item.name] = current
        return value

    @classmethod
    def from_json(cls, value: Mapping) -> DispatchRequest:
        names = {item.name for item in fields(cls)}
        unknown = sorted(set(value) - names)
        if unknown:
            raise ValueError(f"unknown dispatch request fields: {unknown}")
        if value.get("schema_version", SCHEMA_VERSION) != SCHEMA_VERSION:
            raise ValueError("unsupported dispatch request schema_version")
        return cls(**{key: value[key] for key in names if key in value})


@dataclass(frozen=True)
class DispatchResult:
    """What an executor observed.  Never evidence of output content by itself."""

    ok: bool
    session_ref: str | None = None          # session that ran the agent (host / headless id)
    context_ref: str | None = None          # the agent's own context (subagent id / session id)
    parent_context_ref: str | None = None   # context that spawned it (host main session)
    transcript_path: str | None = None      # agent transcript, bound into the capsule by runner
    evidence_refs: tuple[str, ...] = field(default=())   # pre-bound ``host-binding:<sha256>``
    usage: Mapping | None = None            # optional usage block (headless result JSON)
    error: str | None = None
    error_class: str | None = None

    def __post_init__(self):
        object.__setattr__(self, "evidence_refs", tuple(self.evidence_refs or ()))
        if self.usage is not None:
            object.__setattr__(self, "usage", _frozen_map(self.usage))

    def to_json(self) -> dict:
        value = {item.name: getattr(self, item.name) for item in fields(self)}
        value["evidence_refs"] = list(self.evidence_refs)
        value["usage"] = None if self.usage is None else dict(self.usage)
        return value


@runtime_checkable
class InferenceExecutor(Protocol):
    """Transport one rendered request to a model and wait for the result (blocking)."""

    name: str

    def dispatch(self, request: DispatchRequest) -> DispatchResult:
        ...


def supports_reattach(executor) -> bool:
    """Whether re-calling ``dispatch`` for an already-claimed attempt re-attaches."""
    return getattr(executor, "supports_reattach", False) is True


_CLASSIFIERS = (
    ("RATE_LIMIT", re.compile(r"rate.?limit|\b429\b", re.I)),
    ("TIMEOUT", re.compile(r"timeout|timed out", re.I)),
    ("CONNECTION", re.compile(r"connect|socket|network|closed mid-response|enotfound", re.I)),
    ("SCHEMA_ERROR", re.compile(r"schema|contract|json", re.I)),
)


def classify_error(message: str | None, declared: str | None = None) -> str:
    """Mirror of ``l4-stock.js`` ``classifyFailure``; a declared known class wins."""
    if declared:
        upper = str(declared).strip().upper()
        if upper in TASK_ATTEMPT or upper in {"SCHEMA_ERROR", "AGENT_ERROR", "CONTRACT_ERROR"}:
            return upper
    text = str(message or "")
    for name, pattern in _CLASSIFIERS:
        if pattern.search(text):
            return name
    return "AGENT_ERROR"


__all__ = [
    "CODEX_AGENT_NAMES", "DEFAULT_TIMEOUTS", "DISPATCH_DIR", "DispatchRequest", "DispatchResult",
    "ExecutorTimeout", "ExecutorUnavailable", "FALLBACK_TIMEOUT", "InferenceExecutor",
    "ROLE_DISPATCH", "SCHEMA_VERSION", "agent_type_for", "classify_error", "supports_reattach",
]
