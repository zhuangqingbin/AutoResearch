#!/bin/zsh -l
# macro-research full 档周度取数(launchd 周日 20:00 调;手动同命令)。-l 载入用户 profile 拿 TUSHARE_TOKEN/FRED_API_KEY。
# 只跑**确定性取数**(data.md);LLM 节(decision.md → macro_state.json)由人/scan Stage 0 另派 —— cron 不无人跑 LLM。
cd "$(dirname "$0:A")/.." || exit 1
exec uv run --no-sync python -m autoresearch.macro.harvest "$@"
