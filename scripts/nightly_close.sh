#!/bin/zsh -l
# 夜间收盘后确定性欠账补跑(launchd 交易日 20:45 调;手动同命令)。
# -l 载入用户 profile 拿 TUSHARE_TOKEN;20:45 错开 19:30 的 prewarm,也避开人工扫描窗口。
# 只跑确定性段(归因/记分卡/账本/盯梢);LLM 诊断段(scan-retro / t1-review)仍人工。
cd "$(dirname "$0:A")/.." || exit 1
exec uv run --no-sync python -m autoresearch.learning.nightly_close "$@"
