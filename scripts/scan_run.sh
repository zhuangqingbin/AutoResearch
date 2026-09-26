#!/bin/zsh -l
# 无人值守一场全 A 扫描(launchd 交易日 21:20 调;手动同命令)。-l 载入用户 profile 拿
# TUSHARE_TOKEN 与 PATH(uv、claude 都在 ~/.local/bin)。
#
# 本脚本只做三件事:切到仓库根、钉死 claude 引擎、把参数原样交给 Python 编排
# `autoresearch.scan.scan_run`。流程(锁 → 交易日 → 人工场 → 湖灌齐 → session_v1 begin →
# headless runner → verify-report → 送达 / FAILED 通知)全在 Python 里,便于测试:
# macOS 没有 flock(1)/timeout(1),互斥锁是 fcntl($CTX/.scan_run.lock),子进程墙钟由
# Python 杀整个进程组。
#   scripts/scan_run.sh                                   # 等 stk_factor_pro 灌齐再开
#   scripts/scan_run.sh --date 2026-09-28 --skip-readiness   # 补跑(显式日期须为交易日)
# 日志:reports_claude/_ops/scan_run_<日>.log(launchd 的 /tmp/scan-run.log 只兜启动前的错)。
set -u
cd "$(dirname "$0:A")/.." || exit 1

# 引擎显式钉死:launchd 环境里没有 CLAUDECODE,headless 执行器只跑 claude(Codex 不在范围)。
export AUTORESEARCH_ENGINE=claude

exec uv run --no-sync python -m autoresearch.scan.scan_run "$@"
