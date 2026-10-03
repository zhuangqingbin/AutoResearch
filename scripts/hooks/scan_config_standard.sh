#!/bin/sh
# PostToolUse —— scan_config 标准守卫(Claude Code:Edit|Write|MultiEdit;Codex:Bash;两引擎共用)。
# 负载里出现受管路径(contracts/scan_config.GUARDED_PATHS 同一份清单)才起 Python 跑
#   python -m autoresearch.scan.config_standard --hook
# 违规 → 报告到 stderr + exit 2(编辑者必须改到零违规);零违规 → 一行回执 exit 0;
# 无关路径 → 不起 Python、无输出、exit 0。lint 自身崩溃(rc≥2)→ 放行,不打断工具调用。
# 测试注入:SCAN_CONFIG_LINT_PATH=<file> 覆盖被 lint 的 jsonc 路径(生产不设)。
payload=$(cat) || exit 0
case $payload in
  *'.claude/skills/scan-market/'*|*'autoresearch/contracts/scan_config.py'*|*'autoresearch/scan/user_config.py'*) ;;
  *) exit 0 ;;
esac
root=${CLAUDE_PROJECT_DIR:-$(pwd)}
cd "$root" || exit 0
if [ -n "$SCAN_CONFIG_LINT_PATH" ]; then
  out=$(uv run --no-sync python -m autoresearch.scan.config_standard --hook --path "$SCAN_CONFIG_LINT_PATH" 2>&1)
else
  out=$(uv run --no-sync python -m autoresearch.scan.config_standard --hook 2>&1)
fi
rc=$?
if [ "$rc" -eq 0 ]; then
  printf '%s\n' "$out"
  exit 0
fi
if [ "$rc" -eq 1 ]; then
  printf '%s\n' "$out" >&2
  exit 2
fi
exit 0
