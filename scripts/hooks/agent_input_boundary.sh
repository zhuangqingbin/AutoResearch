#!/bin/sh
# PreToolUse —— 项目研究 agent 输入边界的零成本前置过滤(Claude Code 与 Codex 共用)。
# 用法:agent_input_boundary.sh <claude|codex>
# 主线程的 hook 输入不带 agent_type 键:不起 Python,直接放行。带了才交给同目录的
# agent_input_boundary.py 判(它只管研究角色)。任何失败都放行。
payload=$(cat) || exit 0
case $payload in
  *'"agent_type"'*) ;;
  *) exit 0 ;;
esac
printf '%s' "$payload" | python3 "$(dirname "$0")/agent_input_boundary.py" "${1:-claude}" || exit 0
exit 0
