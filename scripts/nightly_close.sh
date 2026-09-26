#!/bin/zsh -l
# 夜间收盘后确定性欠账补跑(launchd 交易日 23:30 调;手动同命令)。
# -l 载入用户 profile 拿 TUSHARE_TOKEN(`ledger_views` 的交易日历第一级走 trade_cal)。
# 23:30(2026-09-26 批 4,原 20:45):排在 21:20 无人值守扫描(scripts/scan_run.sh)之后,
# 避免与扫描读写同一账本;也错开 19:30/21:00 的 prewarm。
#
# 只跑**确定性、只记不学**的五步(零 LLM、零回注;LLM 复盘已于 2026-08-21 整体退役)。
# 下面这张清单与真实的 `step "…"` 行逐条对齐(注释说「两步」却列三条 = 本仓反复复发的漂移,
# `tests/scan/test_web_budget_wiring.py::test_nightly_close_header_comment_matches_the_real_steps`
# 把两边钉死):
#   1. outcome fill       —— 已发布 run 的推荐票事后读数(D+2 成熟才算)
#   2. ledger_views build —— runs / session_calendar / market 三张视图 + _health.json
#   3. populations build  —— 反事实人口两层 parquet(L0 universe / L1 路径表)
#   4. populations rulers —— 四把阶段尺 views/stage_rulers.csv(build **不写**它,写它的是
#      `rulers` 子命令;此前只有 prelude 里一个包在 contextlib.suppress 的生产者,于是
#      没扫描的日子四把尺永不更新)
#   5. analyze ledger     —— stock-research 独立产物(full 报告/lite 决策卡)结果账本:
#      ingest(发现新产物,记评级/proposal)+ fill(D+2 成熟后补 gap_c1_o2;full 报告
#      另补 fwd_5/10/20)连跑(D5.1;只记不学,不回注任何 prompt/权重)
#
# 病灶:本脚本此前 `exec` 的是 `autoresearch.learning.nightly_close` —— 那个模块随
# 2026-08-21 learning 层退役被真删了,于是 launchd 每个交易日 20:45 忠实地启动一次、
# 拿到 ModuleNotFoundError、把错误写进一个没人看的 /tmp 日志,**任务死了很久没人知道**。
# 所以现在:每一步都打带时刻的开始/结束行,失败落非零退出码,并且 `ledger_views` 自己
# 还会写 `_health.json`(last_attempt_at / last_success_at)—— 三层里任意一层都能看出它死没死。
set -u
cd "$(dirname "$0:A")/.." || exit 1

# 引擎**显式**声明,不吃默认值:launchd 的环境里既没有 CLAUDECODE 也没有 CODEX_*,
# `workspace.detect_engine` 会落到第 4 级「在位者 claude」——那是兜底不是决定。
# 两个引擎各装一份 plist(各自的 Label + 各自的 AUTORESEARCH_ENGINE),这里只兜住
# 「手动跑忘了 export」的情况。写错引擎名会被 detect_engine 当场拒掉,不会静默同根。
: "${AUTORESEARCH_ENGINE:=claude}"
export AUTORESEARCH_ENGINE

rc=0

step() {
  local name="$1"; shift
  print -r -- "[nightly-close] $(date '+%F %T') · start · ${name} · engine=${AUTORESEARCH_ENGINE}"
  uv run --no-sync python -m "$@"
  local code=$?
  if [[ ${code} -eq 0 ]]; then
    print -r -- "[nightly-close] $(date '+%F %T') · ok    · ${name}"
  else
    print -r -- "[nightly-close] $(date '+%F %T') · FAIL  · ${name} · exit=${code}" >&2
    rc=1
  fi
  return 0
}

# 五步都跑,**不因为前一步挂了就跳过后面的**:它们补的是五笔互不相干的欠账,
# 连坐只会让"账本坏了"顺手把"日历也没了"藏起来(catalog.py:1012 同一条教训)。
step "outcome fill"        autoresearch.scan.outcome      fill --today "$(date '+%FT%T%z')"
step "ledger_views build"  autoresearch.scan.ledger_views build
step "populations build"   autoresearch.scan.populations   build
step "populations rulers"  autoresearch.scan.populations   rulers
step "analyze ledger"      autoresearch.analyze.ledger     nightly --today "$(date '+%FT%T%z')"

if [[ ${rc} -ne 0 ]]; then
  print -r -- "[nightly-close] $(date '+%F %T') · 有步骤失败,退出码 1" >&2
fi
exit ${rc}
