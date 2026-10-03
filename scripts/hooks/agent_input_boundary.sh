#!/bin/sh
# All tools reach Python. Parse/load failures deny; only parsed explicit root/relay bypasses.
payload=$(cat) || exit 2
result=$(printf '%s' "$payload" | python3 "$(dirname "$0")/agent_input_boundary.py" "${1:-claude}")
status=$?
if [ "$status" -ne 0 ]; then
  printf '%s' '{"hookSpecificOutput":{"hookEventName":"PreToolUse","permissionDecision":"deny","permissionDecisionReason":"AGENT_INPUT_BOUNDARY: hook failed to load; no access authorized"}}'
else
  printf '%s' "$result"
fi
