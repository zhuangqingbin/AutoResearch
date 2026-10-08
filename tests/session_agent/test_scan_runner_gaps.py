"""Batch 2–3 Task 5: blocking gaps found by the session-plan vs workflow audit.

Audit: docs/research/2026-09-26-session-plan-vs-workflow-audit.md (§3 validator replay).
"""
from __future__ import annotations

from pathlib import Path

import pytest

from autoresearch.session_agent import runner, store
from autoresearch.session_agent.executors.base import DispatchResult

from ._runner_support import begin_synthetic_run, inf

REPO = Path(__file__).resolve().parents[2]

#: Shape of the macro-brief agent template (`.claude/agents/macro-brief.md` 模板节):
#: sections 1–5 carry a bold title, section 6 is the plain disclaimer line.
TEMPLATE_MARKET_VIEW = """# 市场研判 — 2026-09-24

1. **一句话定调**:区间震荡·外进内出。
2. **市场结构**:regime=range;站上MA60 49.8%。
3. **板块红黑榜**:强:种植业;弱:电子化学品Ⅱ。
4. **操作基调**:中性偏低仓位。
5. **关注**:中报窗口已过,10月进入三季报披露期。
6. 仅供研究,非投资建议。
"""


class _WritesMarketView:
    name = "fake"
    from autoresearch.session_agent.roles import EXECUTOR_CAPABILITIES
    capabilities = EXECUTOR_CAPABILITIES["mailbox"]

    def __init__(self, text: str):
        self.text = text

    def dispatch(self, request):
        for path in request.output_paths.values():
            Path(path).parent.mkdir(parents=True, exist_ok=True)
            Path(path).write_text(self.text, encoding="utf-8")
        return DispatchResult(ok=True, session_ref="session-main", context_ref="agent-mv",
                              parent_context_ref="session-main")


def _market_view_run(tmp_path, monkeypatch):
    return begin_synthetic_run(tmp_path, monkeypatch, [
        inf("scan.market_view", role="macro.brief", contract="macro.brief.v1",
            outputs=["scan.market.view"]),
    ])


def test_agent_template_marks_section_six_without_bold():
    """The fixture above mirrors the live agent contract, not a guess."""
    agent = (REPO / ".claude/agents/macro-brief.md").read_text(encoding="utf-8")
    assert "6. 仅供研究,非投资建议。" in agent
    assert "5. **关注**" in agent


def test_template_shaped_market_view_is_accepted_by_the_production_validator(
        tmp_path, monkeypatch):
    run = _market_view_run(tmp_path, monkeypatch)
    final = runner.run_loop(run.run_id, _WritesMarketView(TEMPLATE_MARKET_VIEW),
                            poll_seconds=0.01, max_rounds=50,
                            hooks=run.hooks(validator=None))     # production contract check
    assert final["finished"] is True, final["errors"]


@pytest.mark.parametrize("drop", ["3. **板块红黑榜**", "6. 仅供研究"])
def test_market_view_missing_a_section_is_still_rejected(tmp_path, monkeypatch, drop):
    run = _market_view_run(tmp_path, monkeypatch)
    text = "\n".join(line for line in TEMPLATE_MARKET_VIEW.splitlines() if drop not in line)
    final = runner.run_loop(run.run_id, _WritesMarketView(text), poll_seconds=0.01,
                            max_rounds=50, hooks=run.hooks(validator=None))
    assert final["status"] == "BLOCKED"
    entry = store.read_entry(Path(run.handle.workspace) / "session/tasks.json",
                             "scan.market_view")
    # 2026-10-08:领域校验拒绝 = DOMAIN_VALIDATION,带校验原话重做一次;第二次仍缺节 → BLOCKED。
    assert entry["error"]["code"] == "DOMAIN_VALIDATION" and entry["attempt"] == 2
    assert "six sections" in entry["error"]["message"]
