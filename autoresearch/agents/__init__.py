"""Free data layer for the in-session ``analyze-ticker`` skill.

The paid-LLM multi-agent path (LangGraph orchestration + the analyst /
researcher / manager / risk / trader factories) was removed; the multi-agent
analysis now runs in-session with Claude as the engine, on top of the data
tools under ``autoresearch/dataflows`` and ``autoresearch/agents/utils``.
This package no longer re-exports agent factories or LangGraph state types.
"""
