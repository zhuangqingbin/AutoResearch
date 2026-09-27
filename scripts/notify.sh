#!/bin/zsh -l
# 一句话通知(未开 / FAILED)—— 与 brief 同一送达渠道(scan_config.jsonc 的 delivery.channel)。
#   scripts/notify.sh "扫描 2026-09-28 FAILED · 阶段 scan.l3 · TIMEOUT · 日志 reports_claude/_ops/…"
# 凭证(BARK_TOKEN / DELIVERY_MAIL_TO)只从环境 / .env 读,本脚本不碰也不打印。
# 送达失败只打一行 JSON、退出码 1,不影响调用方的 run 状态。
cd "$(dirname "$0:A")/.." || exit 1
: "${AUTORESEARCH_ENGINE:=claude}"
export AUTORESEARCH_ENGINE
exec uv run --no-sync python -m autoresearch.scan.delivery notify "$*"
