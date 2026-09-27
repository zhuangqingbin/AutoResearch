#!/usr/bin/env python3
"""scan-market · 确定性前奏一键化(零 LLM)——开扫前全部确定性步骤一条命令跑完。

design: docs/specs/2026-07-03-scan-run-reliability-design.md §2

首航(07-02)人肉串前奏 ~10 分钟且有漏跑风险;本模块把它收编:
consensus 拉(限频容忍)→ 温度 → universe(regime-aware 默认开,含影子)→ 日历 → 催化
→ 菜单/预算/哨兵 → 覆盖池日检(进退复+待建档)→ 新闻目录体检。各步 try 包裹失败不阻断,
末尾汇总屏。
(观察单日检步骤已退役 —— 用户裁定 fb_20260714_002,别再加回。)
(2026-08-21 用户裁定「整个 learning 层退役」:attribution 刷新 / retro·t1 欠账列出 /
 学习环健康三查 / 十本账本刷新 / GATE0 preflight 六步随闭环一并删除,别再加回 ——
 GATE0 的唯一输入是 retro·t1 欠账,闭环一走它恒 PASS,是空转的门。)

  uv run --no-sync python -m autoresearch.scan.prelude 2026-07-03
  uv run --no-sync python -m autoresearch.scan.prelude 2026-07-03 --no-regime-aware --skip universe
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

from autoresearch.common import workspace as ws

#: prelude 步骤的**顺序与去留的单一事实源**(2026-08-26)。
#:
#: 为什么提成模块常量:此前步骤表内联在 `run_prelude` 里,而测试侧维护着**两份手写的
#: skip 清单**(`test_prelude.py` 与 `test_prelude_pool.py`)—— 加一步就得同改两处,漏一处
#: 就把「只验缺席」的用例弄红,而且单跑一份测试根本看不见另一份(记忆:
#: `prelude-step-two-skip-lists`)。现在两份测试都从这里派生 skip 集合,加步骤不再需要改它们;
#: **步骤清单本身**由 `tests/scan/test_prelude.py::test_step_names_inventory` 显式锁住 ——
#: 派生消灭的是「忘了同步」的红,不是「悄悄加了一步没人知道」的哑。
STEP_NAMES = (
    "consensus",
    "temperature",
    "universe",
    "calendar",
    "catalyst",
    "menu",
    "l4_rejection",  # 2026-08-22 批 (c):拒绝价值日读(读历史不读当日)
    "outcome_fill",  # 2026-08-26 §4.4:结果账本回填(只记不学;读历史不读当日)
    "ledger_views",  # 2026-08-28 §2.4 G2/G3:运行日历 + 市场行 + 逐级 KPI(同上,只记不学)
    "dossier_pool",
    "news_catalog",  # Wave12-T35:纯读目录健康,不喂任何决策面
    "overseas",  # 2026-08-29 D-2:隔夜窗海外事件日历(**风险可见性**,不喂判断层)
)


def lowturn_line(res: dict) -> str:
    """低位转强三段到货的汇总屏片段(2026-08-22)。三键缺 = 两把开关全关 → 空串(parity)。

    为什么印在汇总屏:2026-08-21 首跑「全帧 120 → L1 17 → **L2 0**」整天没人看见,L3 侧
    整套(旗列/pass1 强留/守卫⑥/硬约束 G)空转。到货数是这条特性唯一会变的量,人眼一秒判死活。
    (同族配方:「自动学习的腿必须有一个会变的量做断言,否则它死了也像活着」。)
    """
    keys = ("lowturn_full", "lowturn_l1", "lowturn_l2")
    if not all(k in res for k in keys):
        return ""
    f, l1, l2 = (res.get(k) for k in keys)

    def _fmt(v):
        return "?" if v is None else str(v)

    warn = " ⚠️L2 零到货" if (l2 == 0 and isinstance(f, int) and f > 0) else ""
    return f" · lowturn 全帧 {_fmt(f)} → L1 {_fmt(l1)} → L2 {_fmt(l2)}{warn}"


def universe_line(res: dict) -> str:
    """prelude 汇总屏的 universe 行(纯函数,便于测试与复用)。"""
    return (
        f"L0 {res['universe']} → 召回 {res['recall_n']} → L2 {res['l2_n']}({res['l2_engine']})"
    ) + lowturn_line(res)


def _run_steps(steps) -> list[dict]:
    """[(name, fn)] 顺序执行,单步异常不阻断 → [{'step','ok','note'}]。骨架可单测。"""
    out: list[dict] = []
    for name, fn in steps:
        try:
            note = fn()
            out.append({"step": name, "ok": True, "note": "" if note is None else str(note)})
        except Exception as e:  # noqa: BLE001
            out.append({"step": name, "ok": False, "note": f"{type(e).__name__}: {e}"})
            print(f"[prelude] ✗ {name}: {e}", file=sys.stderr)
    return out


def tripwire_advisory_lines(scan_root=None, date: str | None = None) -> list[str]:
    """当日件建议行 —— 现只剩 ⚡tripwire 持仓盯梢(纯读,可单测)。

    ⚡tripwire(Wave7 P2)是**当日风控提醒**(给人看):持仓在两次扫描之间原本无人盯梢,
    卡片里的「失效」条件写完就没有任何机器复核过。`date` 缺省则跳过该行(纯读接口不猜
    wall-clock)。

    2026-08-21 learning 层退役:原同居本函数的三条**校准先验**(📐 触价校准 / 🔁 L3 翻案率 /
    🚪 门柱)全是"从历史账本学到的东西再贴回给下游 agent 读"——正是被裁掉的闭环回注腿,
    随 `buy_ledger`/`cross_calib` 一并删除;函数因此从 `calib_suggestion_lines` 更名,
    L4 共享块那个消费点(原「当日校准锚」节)同批摘除,只剩人读的这一条。
    """
    lines: list[str] = []
    if date:
        import contextlib

        with contextlib.suppress(Exception):  # 盯梢是 advisory,不该有能力阻断 prelude
            from autoresearch.scan.tripwire_watch import check, render_line
            from autoresearch.scan.user_config import load_pinned

            n = len([e for e in (load_pinned(date).get("kept") or []) if e.get("code")])
            ln = render_line(check(date, scan_root=scan_root or ws.scan_root()), n)
            if ln:
                lines.append(ln)
    return lines


def _parse_ts(value):
    """ISO 时间串 → aware datetime;解析不了 → None(**不猜**,同 catalog 的纪律)。"""
    from datetime import datetime, timezone

    try:
        dt = datetime.fromisoformat(str(value))
    except (TypeError, ValueError):
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


# `<endpoint>[<symbol>]✓(1234行…)` —— prewarm 热度快照 note 的逐源片段
_HOT_SRC_RE = re.compile(r"([A-Za-z_][A-Za-z_0-9]*)(?:\[[^\]]*\])?([✓✗])\((\d+)行")


def _hot_rank_thin(note: str) -> list[str]:
    """从 note 的行数**现算**「0 行 / 行数腰斩」——不依赖上游写没写 ✗ 那个装饰字符。

    Wave12 复核 I4:只认 ✗ 的判法有两个漏口——整步抛异常时 note 是
    `f"{type(e).__name__}: {e}"`(**不含 ✗**),而半截返回在旧实现里写的是 `✓(3000行)`。
    行数是结构化事实,拿契约的 `min_rows` 一比就是独立的第二道判据(上游 ✓/✗ 逻辑若回归,
    这道仍然拦得住)。未登记的端点(历史 note 里的旧名字)只查 0 行,不猜下限。
    """
    from autoresearch.data.contracts import CONTRACTS

    out = []
    for ep, _mark, rows in _HOT_SRC_RE.findall(note):
        n = int(rows)
        con = CONTRACTS.get(ep)
        floor = con.min_rows if con else 0
        if n == 0 or (floor and n < floor):
            out.append(f"{ep} {n} 行" + (f"(<{floor})" if floor else ""))
    return out


def _hot_rank_snapshot_warning(p: Path) -> str:
    """`_prewarm.json` 的 `hot_rank_snapshot` 步骤若断采/空/半截 → 告警片段。

    Wave12 T3:`prewarm.py`(夜间)与本模块(次日开扫)是**两个独立进程**,
    `contracts._DEGRADED` 是进程内列表、穿不透进程边界——落盘的 `_prewarm.json` 是
    唯一穿透介质。presence-gated:文件读不了/无该 step/全源皆足量✓ → ""(不打扰)。

    三条判据(复核 I4:原来只认 note 里的 ✗ 一条,整步抛异常时反而静悄悄):
      ① `ok=False`(整步异常;`_step()` 写的 note 里没有 ✗)
      ② note 含 ✗(逐源断采)
      ③ 行数 0 / 低于契约下限(半截返回——它在旧实现里长得跟成功一模一样)
    """
    import json

    try:
        steps = json.loads(p.read_text(encoding="utf-8")).get("steps") or []
    except Exception:  # noqa: BLE001 — 附加提示可选,读不了不挡主行
        return ""
    hot = next((s for s in steps if s.get("step") == "hot_rank_snapshot"), None)
    if hot is None:
        return ""
    note = str(hot.get("note", ""))
    why = []
    if not hot.get("ok", True):
        why.append("整步异常")
    if "✗" in note:
        why.append("断采")
    if thin := _hot_rank_thin(note):
        why.append("半截/空(" + ", ".join(thin) + ")")
    return f" · ⚠️ 热度快照{'+'.join(why)}:{note}" if why else ""


def prewarm_line(date: str, scan_root: Path | str | None = None) -> str:
    """夜间预热是否真跑过(Wave5 ④B:写了没装的优化必须当天可见,不靠事后考古)。"""
    import datetime as _dt

    p = Path(scan_root or ws.scan_root()) / date / "_prewarm.json"
    if not p.is_file():
        return (
            "预热(夜间):✗ 未跑 —— L0/L1/L2 本次全额取数(~8-10m)。"
            "装载检查:`launchctl list | grep scan-prewarm`"
        )
    ts = _dt.datetime.fromtimestamp(p.stat().st_mtime).strftime("%m-%d %H:%M")
    return f"预热(夜间):✓ 已跑({ts})—— universe/evidence 应全湖命中{_hot_rank_snapshot_warning(p)}"


def macro_state_line(date: str) -> str:
    """宏观 full 档的机读摘要新不新鲜(Wave5 ③B)。

    `macro_state.json` 自 2026-06-22 起恒缺 → market_view 一个月开篇都写「无新鲜宏观视图」,
    而**没人看得见这件事**。放进汇总屏:过期/缺失当天就暴露,而不是攒一个月。
    """
    from autoresearch.macro.state import load_macro_state

    st, note = load_macro_state(date)
    return f"宏观 full 摘要:{'✓' if st else '✗'} {note}"


def render_summary(date: str, results: list[dict], scan_root: Path | str | None = None) -> str:
    """prelude 汇总屏文本(纯函数,可单测 + 可落盘)。

    此前这段是 run_prelude 里的一串 print —— 结果被 scan-market.js「只回报 stdout 末 15 行」
    结构性截断(12 步 ✓/✗ + 建议行 + 下一步 > 15 行)。提成纯函数后既能照常打印,也能落盘
    供 workflow 指路、主会话全量转播。
    """
    out = ["═" * 30 + f" prelude 汇总 · {date} " + "═" * 30]
    for r in results:
        mark = "✓" if r["ok"] else "✗"
        out.append(f"  {mark} {r['step']}: {r['note']}")
    # 📡 公告主源无权限提醒(Wave9 A-1;复核轮1 Critical 修复)。presence-gated,只在
    # 回看到的那一日确认是 blind 才出,pending/ok/fallback/无历史日都不打扰。
    #
    # 为什么不读当日:`L3_news/` 由 L3 阶段 `harvest_l3_news()` 生成,而这里(prelude,
    # L0→L2)跑在 L3 之前 —— 读当日 `anns_source_status` 必然撞见"目录还不存在"
    # (=pending),若把 pending 当 blind 处理就会天天无条件误报(复核轮1 实测:
    # `L2_gbdt_top200.csv` mtime 与 `L3_news/` mtime 相差 9 分 36 秒,后者严格晚于
    # prelude 收尾)。改为**回看最近一个已完成扫描日**(跳过仍是 pending 的日子),
    # 与既有的 dossier 对账提醒同款"看历史"套路。
    #
    # 沿用本函数其余可选行的记账约定(try/except + stderr,不用 contextlib.suppress)——
    # 本文件 write_summary 那段注释已有前车之鉴:静默吞异常曾让落盘失败在 workflow 侧
    # 显示成"已生成",静默降级比响亮失败危险得多。
    try:
        from autoresearch.scan.health import anns_source_status

        scan_root_dir = Path(scan_root or ws.scan_root())
        prior = (
            sorted(
                p
                for p in scan_root_dir.iterdir()
                if p.is_dir() and p.name[:2] == "20" and p.name < date
            )
            if scan_root_dir.is_dir()
            else []
        )
        for prev in reversed(prior[-5:]):  # 最近 5 日里找第一个跑到 L3 的
            st = anns_source_status(prev).get("status")
            if st == "pending":
                continue
            if st == "blind":
                # Wave9 final-fix C-1:兜底源已接线进 harvest_l3_news(不再是零生产调用点的
                # 死码)——这行文案曾断言"兜底源无料"却从未真正查过它,诊断信息在说谎;
                # 现在 blind 只会在兜底也**真的被查过**且仍无料时出现,措辞照实改。
                out.append(
                    f"  📡 公告双源皆空(最近已完成扫描日 {prev.name})—— 主源无权限,"
                    "兜底源已查但仍无料;查 `python -m autoresearch.data.sources.anns_fallback` 冒烟"
                )
            break
    except Exception as e:  # noqa: BLE001 — 提醒行可选(presence-gated),缺了不挡前奏
        print(f"[prelude] ⚠️ anns 提醒行跳过:{e!r}", file=sys.stderr)
    out.append(f"  {prewarm_line(date, scan_root)}")
    try:
        out.append(f"  {macro_state_line(date)}")
    except Exception as e:  # noqa: BLE001 — 状态行可选,缺了不挡前奏
        print(f"[prelude] ✗ macro_state_line: {e}", file=sys.stderr)
    try:
        # ⚡tripwire 持仓盯梢(Wave7 P2)——**仅人看,勿贴给任何 agent**:持仓风控提醒不该
        # 混进个股评级的输入(同 market_view §4-5 只进 L5 的防锚定纪律)。
        clines = tripwire_advisory_lines(date=date)
        if clines:
            out.append("  当日件建议行(⚡ 仅人看,勿贴进任何 agent 输入):")
            out += [f"    {ln}" for ln in clines]
    except Exception as e:  # noqa: BLE001 — 建议行可选,缺了不挡前奏
        print(f"[prelude] ✗ tripwire_lines: {e}", file=sys.stderr)
    out.append(
        "  下一步(LLM 段):哨兵档 → 直接 assemble(日历已跑);全扫 → 策略师 → L3 → L4(见 SKILL 流程)"
    )
    return "\n".join(out)


def write_summary(date: str, results: list[dict], scan_root: Path | str | None = None) -> Path:
    """汇总屏落 `context/scan/<date>/_prelude_summary.md`,返回路径(目录缺则建)。"""
    det = Path(scan_root or ws.scan_root()) / date
    det.mkdir(parents=True, exist_ok=True)
    p = det / "_prelude_summary.md"
    p.write_text(render_summary(date, results, scan_root=scan_root) + "\n", encoding="utf-8")
    return p


def dossier_reconcile_nag(date: str, *, pool_path=None) -> str:
    """池内已建档票缺「季度对账 <period>」痕迹 → 当日件建议行(纯读,可单测)。

    I-1(2026-07-24 终审):`autoresearch.dossier.reconcile` 是手工 CLI,全仓零调用点
    **零提醒** —— 8 月中报季会静默地什么都不发生(FN-1 家族:write_base_rates /
    force_full_card / 权重自动重标定 NO-OP 之后第 N 例)。这里给它一个能被看见的入口。

    period 用 `dossier.mainbz._recent_periods` 同款滞后逻辑取"最近应已披露的报告期"
    (年报 4/30、中报 8/31 披露截止后才算已披露)——该滞后判定本身就是"当前披露窗口"
    的判据。presence-gated:池空 / 无档案 / 未首覆 / 已对账 → ""(不打印)。

    Wave10 A10 起,判定本体搬到 `dossier.debt_slo.reconcile_overdue`(SLO 的②要用同一个数)
    —— 本函数只负责措辞。**不留两份实现**:同一条规则两处各写一套,迟早在某次改动后
    给出两个不同的欠账数,而那时没人知道该信哪个。
    """
    from autoresearch.dossier.debt_slo import reconcile_overdue
    from autoresearch.dossier.mainbz import _recent_periods

    period = _recent_periods(date, 1)[0]
    todo = reconcile_overdue(date, pool_path=pool_path)
    if not todo:
        return ""
    return (
        f"📐 季度对账待跑:{len(todo)} 只(period={period})"
        f"→ uv run --no-sync python -m autoresearch.dossier.reconcile {period}"
    )


def dossier_staleness_nag(date: str, *, pool_path=None) -> str:
    """池内已建档票 `last_refresh`(缺退 `initiated`)距 today 超 `schema.STALE_DAYS` 日
    → 当日件陈旧告警;`ref` 存在但格式畸形的票单独标「日期畸形」,不静默跳过(I-1)。

    Task 3(2026-07-24 终审 M-13 + spec 风险节):`last_refresh` 至今零写者零读者,档案
    陈旧目前没有任何探针。与 `dossier_reconcile_nag` 姊妹但判据不同——那个盯"有没有
    做过对账",这个盯"上次全量刷新距今多久"。天数与阈值统一取 `schema.staleness_age`/
    `schema.STALE_DAYS`,不再从 `staleness_issues` 的自然语言文案里反解(I-3,2026-07-24
    终审:反解在文案改动/阈值改动两种变异下都活体存活过,读出垃圾或自相矛盾读数)。
    presence-gated:池空/无档案/未首覆/未超期 → ""(不打印)。
    """
    from autoresearch.dossier import pool as _pool, schema as _schema

    stale: list[str] = []
    for code, st in sorted(_pool.load_pool(pool_path).get("stocks", {}).items()):
        if st.get("status") != "active":
            continue
        text = _schema.read_dossier_text(code)
        if text is None:  # 未建档 → 归 pending_init,不催陈旧
            continue
        if not _schema.parse_frontmatter(text).get("initiated"):
            continue
        age = _schema.staleness_age(text, date)
        if age is None:
            if _schema.staleness_issues(text, date):  # ref 存在但畸形,与"两者皆空"区分(I-1)
                stale.append(f"{code}(日期畸形)")
            continue
        if age > _schema.STALE_DAYS:
            stale.append(f"{code}({age}日)")
    if not stale:
        return ""
    return (
        f"🕰️ 档案陈旧 {len(stale)} 只(>{_schema.STALE_DAYS}日未全量刷新):"
        + "、".join(stale[:6])
        + ("…" if len(stale) > 6 else "")
    )


def _write_t0(scan_dir: Path) -> None:
    """墙钟 t0 标记:mtime 即 stage_timing 的起点锚,内容仅自述。
    已存在不覆盖(prelude-retry/重跑不重置起点);失败不挡 prelude。"""
    try:
        scan_dir.mkdir(parents=True, exist_ok=True)
        fp = scan_dir / "_t0.json"
        if not fp.exists():
            fp.write_text('{"purpose": "stage_timing 起点锚(mtime)"}', encoding="utf-8")
    except Exception:  # noqa: BLE001 — 计时锚可选
        pass


def run_prelude(
    date: str, regime_aware: bool | None = None, skip: tuple[str, ...] = ()
) -> list[dict]:
    # 新 run 开工前先冻结上一次被 SIGKILL/断电打断的 run:它的现场随时间只会更烂,
    # 而且不冻结就没人知道它停在哪。失败只警告,绝不挡住本次运行。
    from autoresearch.trace.capsule import recover_stale_runs_quietly

    recover_stale_runs_quietly()

    # 生产路(scan-market.js → prelude)的历史缺省 = True;scan_config funnel.regime_aware 可覆盖,
    # CLI --no-regime-aware 恒优先(2026-08-11 配置单一事实源波;universe 直调 CLI 的内建缺省
    # 仍是 False,两处 parity 各自成立,见 universe.run 同款注)。
    from autoresearch.scan.user_config import knob

    regime_aware = bool(knob("funnel", "regime_aware", regime_aware, True))
    scan_dir = ws.scan_root() / date
    _write_t0(scan_dir)

    def _consensus():
        from autoresearch.research.consensus import pull

        pull(date)
        return "一致预期已拉(或今日已有)"

    def _temperature():
        from autoresearch.scan.temperature import rollup

        out = rollup(date, date)
        if not len(out):
            return "无新增(空回填/取数失败·presence-gated,详见 stderr)"
        row = out.iloc[-1]
        return f"score={row['score']} phase={row['phase']}"

    def _universe():
        from autoresearch.scan.universe import run

        res = run(date, regime_aware=regime_aware)
        return universe_line(res)

    def _calendar():
        import pandas as pd

        from autoresearch.scan.calendar import harvest_calendar

        codes: set[str] = set()
        for fname in ("L2_gbdt_top200.csv", "finalists.csv"):
            p = scan_dir / fname
            if p.exists():
                df = pd.read_csv(p, dtype={"code": str})
                if "code" in df.columns:
                    codes |= set(df["code"].astype(str).str.zfill(6))
        if not codes:
            return "跳过(无 L2 staging)"
        df = harvest_calendar(date, codes)
        n_u = int((df["kind"] == "unlock").sum()) if len(df) else 0
        n_d = int((df["kind"] == "disclosure").sum()) if len(df) else 0
        n_i = int((df["kind"] == "index_rebalance").sum()) if len(df) else 0
        return f"解禁 {n_u} + 披露 {n_d}" + (f" + 调样 {n_i}" if n_i else "")

    def _catalyst():
        import pandas as pd

        from autoresearch.scan.agents.l3_catalyst import harvest_catalyst

        p = scan_dir / "L2_gbdt_top200.csv"
        if not p.exists():
            return "跳过(无 L2 staging)"
        codes = pd.read_csv(p, dtype={"code": str})["code"].astype(str).str.zfill(6).tolist()
        df = harvest_catalyst(date, codes)
        pos = [c for c in ("rep_impl", "rep_plan", "holder_in", "surv_n") if c in df.columns]
        n = int((df[pos].fillna(0).sum(axis=1) > 0).sum()) if len(df) and pos else 0
        return f"催化旗 {n}/{len(df)} 只(回购/增持/调研)"

    def _menu():
        from autoresearch.scan.menu import l4_budget, menu_health, sentinel_advice

        mh = menu_health(scan_dir)
        n, why = l4_budget(scan_dir)
        level, reason = sentinel_advice(scan_dir)
        print(mh or "(菜单体检:staging 缺)")
        return f"L4 预算 {n}({why});sentinel={level}({reason})"

    def _dossier_pool():
        import contextlib

        from autoresearch.dossier import pool

        out = pool.refresh(date)
        delta = f"进{len(out['entered'])}退{len(out['retired'])}复{len(out['revived'])}"
        pend = out["pending_init"]
        pend_txt = f"待建档 {len(pend)} 只({','.join(pend[:6])})" if pend else "待建档 0"
        moved = out["entered"] or out["retired"] or out["revived"]
        note = f"池 {out['n_active']} active · {delta if moved else '无变动'} · {pend_txt}"
        slo = ""
        with contextlib.suppress(Exception):  # A10:SLO 可选层,坏档不挡池日检
            from autoresearch.dossier.debt_slo import compute, render

            slo = render(compute(date))
        nag = ""
        with contextlib.suppress(Exception):  # 对账提醒可选,坏档不挡池日检
            nag = dossier_reconcile_nag(date)
        stale = ""
        with contextlib.suppress(Exception):  # 陈旧告警可选,坏档不挡池日检
            stale = dossier_staleness_nag(date)
        # SLO 与对账提醒并存、不重复:前者是**判据**(这条流水线稳不稳),后者是**动作**
        # (该敲哪条命令、哪个 period)。只留判据会让人知道欠账却不知道怎么还。
        extra = " · ".join(x for x in (slo, nag, stale) if x)
        return f"{note} · {extra}" if extra else note

    def _overseas():
        """D-2:隔夜窗海外事件日历(2026-08-29;`scan/overseas.py`)。

        主尺 `gap_c1_o2` 的持仓窗横跨整个美股 T+1 交易日(21:30–04:00 CST + 盘后财报到
        08:00 + FOMC 02:00),所以「T+1 尾盘买之前」与「隔夜持仓期间」有哪些**已知**外部
        事件,是这条流水线此前完全看不见的一面(08-26 真跑漏掉 NVDA 盘后财报即为实例)。

        ⚠️ **风险可见性,不是选股信号**:只进 summary 📅 / brief ⑤ / 📌 哨兵三个展示点,
        **不喂 L3/L4/策略师**,不自动否决入场、不改仓位、不改评级(设计稿 §0 边界最后一行)。
        判断层接入(B-2/B-3)受 09-中冻结,与本步无关。
        """
        from autoresearch.scan.overseas import run as _overseas_run

        got = _overseas_run(date, scan_dir)
        n = got.get("n", 0)
        if not n:
            return "无隔夜窗海外事件(或源不可用 → 已记降级)"
        by = got.get("by_window") or {}
        zh = {"pre_entry": "入场前", "holding_overnight": "持仓隔夜", "date_risk": "当日风险"}
        parts = "、".join(f"{zh.get(k, k)} {v}" for k, v in sorted(by.items()))
        return f"{n} 条({parts})→ overseas_calendar.csv"

    def _news_catalog():
        """Wave12-T35:news_catalog 覆盖 / freshness / 非空率报表行(**只看,不喂决策**)。

        通电三步的第三步。前两步(inventory、夜间 ingest)让目录里有东西,这一步让它
        **每天被人看见** —— 否则又是一个"跑过一次然后没人知道它死没死"的腿
        (recalibrate 连续 4 次 NO-OP 空转两周的家训:自动的腿必须有一个会变的量做断言)。

        ⚠️ 报的是**目录健康**,不是任何决策输入:三个 B 类消费接口(intel 先读目录 /
        L3 第二源 / typed-event 进 prompt)本波仍全关。
        """
        from autoresearch.news.catalog import NewsCatalog

        cat = NewsCatalog()
        h = cat.health()
        n = h["n_observations"]
        if not n:
            return "⚠️ 目录空(0 观测)—— 夜间 `news_flash` 步还没出过数"
        wide = cat.market_heat_eligible()
        # freshness:最近一条观测距今多久(first_seen 是我们**真的看到**的时刻)
        obs = cat.observations()
        seen = [t for t in (_parse_ts(v) for v in obs["first_seen_ts"]) if t is not None]
        fresh = "—"
        if seen:
            from datetime import datetime, timezone

            hours = (datetime.now(timezone.utc) - max(seen)).total_seconds() / 3600
            fresh = f"{hours:.1f}h"
        srcs = "/".join(f"{k}:{v}" for k, v in sorted(h["by_source"].items()))
        miss = h["first_seen_missing_rate"]
        flags = []
        if miss:
            flags.append(f"🚨 first_seen 缺失率 {miss:.4f}(契约要求恒 0)")
        # I6 活体探针:整源 published 系统性超前 = 时区标错(上线首日 global_sina +7.7h)
        if h.get("tz_suspect_sources"):
            flags.append(
                f"🚨 时区可疑源 {h['tz_suspect_sources']}"
                f"(published 中位超前 {h.get('published_ahead_hours_max')}h,"
                f"≈+8 就是把北京时间当 UTC)"
            )
        basis = h.get("by_basis") or {}
        # I5:历史分片走 snapshot_inferred、新抓取走 observed —— 两者**不可混用**,
        # 分开显示才看得出"历史腿到底入没入目录"(首版就是这条腿整条缺席)。
        basis_txt = "/".join(f"{k}:{v}" for k, v in sorted(basis.items())) or "—"
        return (
            f"{n} 观测 · 事件 {h['n_events']} · 市场口径 {len(wide)}"
            f"(其余为逐票 selective,不得计入市场热度)· 时间来源 {basis_txt}"
            f" · 最新 {fresh} 前 · {srcs}" + ("".join(" · " + f for f in flags))
        )

    def _l4_rejection():
        """L4 拒绝价值日读(2026-08-22 批 (c)):滚动 40 日评级 rank-IC / ≥OW 出现日数 / 三门 PASS−FAIL
        超额 / finalist 超额。零 LLM、只读湖与历史 staging、**不进 brief、不喂任何 agent**。
        立案:≥OW 卡 40 天只出 4 天,「门的价值」在现尺不可测,改用每天都量得到的读数。"""
        import json as _json

        from autoresearch.research.edge_census import rejection_line, rejection_readout

        d = rejection_readout(today=date)
        try:
            (scan_dir / "_l4_rejection_readout.json").write_text(
                _json.dumps(d, ensure_ascii=False, indent=1), encoding="utf-8"
            )
        except Exception as e:  # noqa: BLE001 — 落盘失败不挡前奏,但要响亮
            print(f"[prelude] ✗ _l4_rejection_readout.json 落盘失败: {e!r}", file=sys.stderr)
        return rejection_line(d)

    def _outcome_fill():
        """结果账本回填(2026-08-26 §4.4):把已发布 run 的推荐票逐只补上事后读数。

        **只记不学**:不回注 prompt、不改权重/门/评级、不产 proposal;读数只进汇总屏这一行
        与 `chain_view` 的 ⑩ 段,**不进 brief、不喂任何 agent**(同 l4_rejection 的边界)。
        增量幂等:已 `complete` 的 run 直接跳过,所以每天成本只与「昨天新出的 + 还没成熟的」
        成正比。`--skip outcome_fill` 可跳。
        """
        from autoresearch.scan.outcome import fill, ledger_line

        res = fill(now=date)
        return f"回填 {res['filled']} run / 跳过 {res['skipped']} · {ledger_line()}"

    def _ledger_views():
        """运行日历 / 市场行 / 逐级 KPI(2026-08-28 §2.4 G2+G3;**只记不学**)。

        与 `outcome_fill` 同一条边界:读数只进汇总屏这一行与 `chain_view`,**不进 brief、
        不喂任何 agent、不改任何参数**。放在 `outcome_fill` **之后**:三张视图与人口表
        都是结果账本的下游,先回填才有得物化。

        为什么必须挂在这里(而不是只挂夜间任务):`populations` 与 `ledger_views` 是两个
        **新生产者**,而本仓最常复发的缺陷正是「生产者没接线」——夜间 launchd 那条腿已经
        死过一次(`nightly_close` exec 一个被删的模块,每交易日白跑很久没人知道)。两处
        都跑、都幂等,任何一条腿死了另一条还在。`--skip ledger_views` 可跳。
        """
        import contextlib

        from autoresearch.scan import ledger_views as _views, populations as _pop, swing_seat as _seat

        try:
            res = _views.build()
            pop = _pop.build()
            with contextlib.suppress(Exception):  # KPI 表算不出来不该挡住前面两张视图
                _pop.write_stage_rulers()
        finally:
            # 复审 M4:§12 读数行冻结进本 run 的 staging(stage_rulers 刚重建之后;重建炸了也冻
            # 当时盘上那一份)。L5 只读这份副本 —— 夜间重建的活视图不在 L5 重放单元里。
            with contextlib.suppress(Exception):
                _seat.freeze_readout(scan_dir)
        return (
            f"视图 {len(res.get('views') or [])} 张 / 人口 {pop.get('built', 0)} run · "
            f"{_views.line()}"
        )

    impls = {
        "consensus": _consensus,
        "temperature": _temperature,
        "universe": _universe,
        "calendar": _calendar,
        "catalyst": _catalyst,
        "menu": _menu,
        "l4_rejection": _l4_rejection,
        "outcome_fill": _outcome_fill,
        "ledger_views": _ledger_views,
        "dossier_pool": _dossier_pool,
        "news_catalog": _news_catalog,
        "overseas": _overseas,
    }
    # 顺序与去留的**单一事实源**是模块常量 `STEP_NAMES`(见其旁注:两份手写 skip 清单的坑)。
    # 名字在 STEP_NAMES 里却没有实现 → 这里 KeyError 当场炸(响亮),不静默少跑一步。
    all_steps = [(n, impls[n]) for n in STEP_NAMES]
    results = _run_steps([(n, f) for n, f in all_steps if n not in skip])

    # 汇总屏:打印 + 落盘(Wave5 ①)。落盘是为了绕开 scan-market.js「只回报 stdout 末 15 行」
    # 的结构性截断 —— 12 步 ✓/✗ + 建议行 + 下一步放不进 15 行,workflow 改为指路该文件。
    print("\n" + render_summary(date, results))
    # 落盘可选(写不了不挡前奏,stdout 仍有全文)—— 但**失败必须响亮**。
    # 2026-07-28:这里原是 `contextlib.suppress(Exception)`,写盘异常被整个吞掉,
    # 而 workflow 的 `prelude && echo "SUMMARY_FILE=..."` 照常回显路径 → agent 报
    # 「Summary file generated」但文件根本不存在,CP1 转播落空,根因至今查不到。
    # 静默降级比响亮失败危险得多(同族:空 pickle 永不重拉 / 空 slim 默认 Hold)。
    try:
        print(f"  (汇总屏已落盘:{write_summary(date, results)})")
    except Exception as e:  # noqa: BLE001 — 不阻断前奏,但把根因摆到 stderr 上
        print(f"[prelude] ✗ 汇总屏落盘失败: {e!r}(前奏继续;stdout 上方有全文)", file=sys.stderr)
    from autoresearch.scan.stage_result import safe_record_stage_result

    failed = [r for r in results if not r["ok"]]
    artifact_candidates = (
        ("l1_full", "L1_scored_full.csv"),
        ("l1_recall", "L1_recall_top1000.csv"),
        ("l2", "L2_gbdt_top200.csv"),
    )
    safe_record_stage_result(
        scan_dir,
        stage="prelude",
        status="DEGRADED" if failed else "SUCCEEDED",
        artifacts=[
            name for name, filename in artifact_candidates if (scan_dir / filename).exists()
        ],
        metrics={"n_steps": len(results), "n_failed": len(failed)},
        warnings=[f"{r['step']}: {r['note']}" for r in failed],
        error=None,
    )
    return results


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="scan 确定性前奏一键化(零 LLM)")
    ap.add_argument("date", help="scan 日 YYYY-MM-DD")
    ap.add_argument("--no-regime-aware", action="store_true", help="关 regime 权重(默认开)")
    ap.add_argument("--skip", default="", help="跳过步骤(逗号分隔:universe,consensus,...)")
    args = ap.parse_args(argv)
    results = run_prelude(
        args.date,
        regime_aware=(False if args.no_regime_aware else None),
        skip=tuple(s for s in args.skip.split(",") if s),
    )
    return 0 if all(r["ok"] for r in results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
