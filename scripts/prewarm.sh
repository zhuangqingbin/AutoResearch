#!/bin/zsh -l
# scan-market 夜间预热(launchd 交易日 19:30 调;手动同命令)。-l 载入用户 profile 拿 TUSHARE_TOKEN。
cd "$(dirname "$0:A")/.." || exit 1
exec uv run --no-sync python -m autoresearch.scan.prewarm "$@"
