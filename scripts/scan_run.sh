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
#
# caffeinate -i(批 4 复审 I4):笔记本用电池时 1 分钟就闲置睡眠,60–180 分钟的一场会在
# claude -p 中途睡死 → 角色超时 → 整场 FAILED。-i 只挡「闲置睡眠」;合盖(无外接显示器)
# 照样睡 —— 插电 + 开盖见 docs/ops/scan-ops.md。caffeinate 与 uv 都把 SIGTERM 转给子进程
# (本机实测),launchctl bootout 仍能让 scan_run 收口。
set -u
cd "$(dirname "$0:A")/.." || exit 1

# 引擎显式钉死:launchd 环境里没有 CLAUDECODE。--engine claude|codex(缺省 claude;交互会话里
# 由宿主按自己的引擎传)—— claude 场每个推理任务一个 `claude -p`,codex 场一个 `codex exec`。
engine=claude
args=()
while (( $# )); do
  case "$1" in
    --engine) engine="$2"; shift 2 ;;
    --engine=*) engine="${1#--engine=}"; shift ;;
    *) args+=("$1"); shift ;;
  esac
done
case "$engine" in
  claude|codex) ;;
  *) echo "[scan-run] --engine 只认 claude|codex,收到 '$engine'" >&2; exit 2 ;;
esac
export AUTORESEARCH_ENGINE="$engine"
set -- "${args[@]}"

if [[ -x /usr/bin/caffeinate ]]; then
  exec /usr/bin/caffeinate -i uv run --no-sync python -m autoresearch.scan.scan_run "$@"
fi
exec uv run --no-sync python -m autoresearch.scan.scan_run "$@"
