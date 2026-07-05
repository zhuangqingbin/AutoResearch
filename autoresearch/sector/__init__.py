"""sector-research · 中观(行业)层 —— 确定性 pack/选择器/地形 + brief 契约 + TTL 复用。

design: docs/specs/2026-07-03-research-skills-altitude-refactor-design.md §5.3/§5.5(Phase 3)。
分工:`pack`(数据包+选择器+L3 全行业地形,零 LLM)/ `brief`(两段契约与抽取)/ `reuse`(TTL 复用)。
判断层(lite brief / full 深研)在 .claude/skills/sector-research/,本包只做确定性件。
"""
