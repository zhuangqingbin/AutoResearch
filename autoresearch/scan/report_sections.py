"""Pure scan summary sections and report composition."""
from __future__ import annotations

import json
from collections import Counter
from pathlib import Path

from autoresearch.scan.decision_finalize import (
    TIER_RANK,
    _PROPOSAL_BY_RATING,
    _VERDICT_BADGE,
    _apply_ensemble_fold,
    _apply_verify_downgrade,
    _dump_decision_records,
    _dump_final_ratings,
    _ensemble_dissent_lines,
    _ensemble_flag,
    _load_ensemble,
    _load_verify,
    _verify_badge,
)
from autoresearch.scan.l4.parsers import (
    _GATES3,
    _decision_text,
    _finalist_row,
    _load_json,
    _read_csv,
    _strip,
    gate_status,
)

_CH_ZH = {
    "composite": "复合",
    "momentum": "动量",
    "reversal": "反转",
    "growth": "成长",
    "value": "价值",
    "main_fund": "主力",
    "northbound": "北向",
    "accumulation": "吸筹",
    "heat": "热度",
}


def _verify_detail(vmap: dict[str, dict]) -> list[str]:
    """Tier-3 多空辩论明细块:降级/否决 摊开 多/空/触发/共识(维持的不赘述);vmap 空 → [](老路不破)。"""
    if not vmap:
        return []
    n = {k: sum(1 for v in vmap.values() if v["verdict"] == k) for k in ("维持", "降级", "否决")}
    lines = ["", f"### 🛡️ Tier-3 买单多空辩论({len(vmap)} 只:{n['维持']} 维持 / {n['降级']} 降级 / {n['否决']} 否决)"]
    hits = [(c, v) for c, v in vmap.items() if v["verdict"] in ("降级", "否决")]
    if hits:
        for c, v in hits:
            bull = f"多:{v['bull']};" if v.get("bull") else ""
            trig = f" ｜ 触发:{v['trigger']}" if v.get("trigger") else ""
            cons = f" ｜ 共识:{v['consensus']}" if v.get("consensus") else ""
            lines.append(f"- **{c}** {_VERDICT_BADGE.get(v['verdict'], v['verdict'])}:{bull}空:{v['bear']}{trig}{cons}")
    else:
        lines.append("- 全部维持:多空辩论后空头未拿出证伪买点的硬证据。")
    return lines

def gate_histogram(scan_dir: Path, rows: list[dict]) -> str:
    """OW三门失守分布一行(确定性,逐卡数 `OW三门 …` 段的 ✗)。0买日一行看懂"今天为什么没买"
    ——胜过读 30 格被截断的结论;有买日同样给出门柱形状。无可解析卡 → ''。"""
    cnt = dict.fromkeys(_GATES3, 0)
    parsed = 0
    for r in rows:
        text = _decision_text(scan_dir, str(r.get("code", "")).zfill(6)) or ""
        st = gate_status(text)
        if st is None:
            continue
        parsed += 1
        for g, failed in st.items():
            if failed:
                cnt[g] += 1
    if not parsed:
        return ""
    parts = " · ".join(f"{g}✗ {cnt[g]}" for g in _GATES3)
    return (f"**OW三门失守分布**({parsed} 卡可解析):{parts}"
            f"(任一门✗ 即压 ≤Hold;门柱即当日 0买/有买的结构性原因)")

_gate_histogram = gate_histogram

def _sortkey(r: dict):
    tier = TIER_RANK.get(r.get("rating", ""), 99)
    try:
        conv = float(r.get("conviction") or 0)
    except ValueError:
        conv = 0.0
    return (tier, -conv)

def _funnel_rows(meta: dict, n_l2, n_l3, n_cards, n_pinned: int = 0) -> list[str]:
    l2_eng = meta.get("l2_engine", "stratified")
    l3_out = f"{n_l3} (+{n_pinned} 保送直通)" if n_pinned else f"{n_l3}"   # 保送不占 L3 名额,单列标注
    return [
        "| 阶段 | 名称 | 出量 | 引擎 | 卡点标准 |", "|---|---|---:|---|---|",
        f"| L0 | 选集 | {meta.get('universe', '?')} | 确定性 | 全A {meta.get('universe_raw', '?')} → 硬门(剔ST/退/停牌/次新, 市值地板, 含北交所) |",
        f"| L1 | 召回 | {meta.get('recall_n', '?')} | 确定性 | 轻门 + 行业条件化复合分(fwd_2_oc 超短主尺 IC 校准) top |",
        f"| L2 | 粗排 | {n_l2} | 分层采样/{l2_eng} | 确定性分层采样(sn_composite 排序+风格桶 floor+sector cap;零模型零 LLM;文件名/列名 gbdt 为遗留别名) |",
        f"| L3 | 精排 | {l3_out} | Opus·max·holistic | 1 agent 通看 ~200 比较选 + 增量证据/论点/红队(保送票不占名额) |",
        f"| L4 | 研究 | {n_cards} 卡 | Opus·medium | 一只=一个 Opus subagent 渐进深度 DD + 早停 |",
    ]

def _channels_zh(s) -> str:
    """recall_channels 串('growth|heat')→ 中文短标('成长/热度');回填/空 → 标注。

    注:分隔符用 '/' 而非 '|' —— '|' 会被 markdown 表格当成列分隔符、把单元格劈成两列(列错位)。
    """
    if not isinstance(s, str) or not s:
        return "—"
    if s == "(backfill)":
        return "回填"
    return "/".join(_CH_ZH.get(c, c) for c in s.split("|"))

def _l1_cell(code: str, l1_full: dict[str, dict], ch_map: dict[str, str]) -> str:
    """L1 召回结论:#复合分名次(分母在列头)· 命中队列(哪几路召回)。"""
    r = l1_full.get(str(code).zfill(6))
    if not r:
        return "—"
    return f"#{r.get('rank', '?')}·{_channels_zh(ch_map.get(str(code).zfill(6), ''))}"

def _l2_cell(code: str, l2_top: dict[str, dict]) -> str:
    """L2 粗排结论:#L2 重排名次(分母在列头)· gbdt 分(列名为遗留别名,值 = sn_composite 分层采样序)。"""
    r = l2_top.get(str(code).zfill(6))
    if not r:
        return "—"
    g = r.get("gbdt_score")
    try:
        gtxt = f"·g{float(g):.2f}" if g not in (None, "", "nan") else ""
    except (TypeError, ValueError):
        gtxt = ""
    return f"#{r.get('l2_rank', '?')}{gtxt}"

def _stage_token_estimate(scan_dir: Path) -> list[str]:
    """分阶段耗时 + LLM 调用数 + 落盘字节(确定性,无 LLM)。**本表不再估算 token**。

    历史与退役理由(Wave6 T8):本表曾用「落盘字节 ÷ 2.8」估 token,2026-07-24 那份报告
    因此写 ~183,623;对**同一次跑动**做 transcript 追溯真计量得到 **加权 5.49M /
    billed 22.4M / 输出 716.6k** —— 对加权低估 **30 倍**,且分布与旧假设相反(L3 真占
    7.8% 而非 37%,大头在主会话编排 27% 与 L4 卡 23%)。这张表是「第二刀砍哪里」的决策
    输入,错 30 倍比没有更糟,故估算列整列退役。

    保留的都是硬事实:分段墙钟(mtime 推导)、LLM 调用数、落盘字节。真实用量以 CP7 的
    `token_usage.md`(`trace.usage_harvest`,按计价倍率加权)为唯一正典。
    """
    det = scan_dir

    def _b(files) -> int:
        return sum(p.stat().st_size for p in files if p.is_file())

    cards = sorted((det / "details").glob("*.md")) if (det / "details").is_dir() else []
    strat = ([det / "market_view.md"] if (det / "market_view.md").is_file() else []) \
        + list(det.glob("_strategist*"))
    sbriefs = (sorted((det / "sector_briefs").glob("*.md"))
               if (det / "sector_briefs").is_dir() else [])
    l3 = list(det.glob("_l3*")) \
        + ([det / "L3_judged_full.csv"] if (det / "L3_judged_full.csv").is_file() else [])
    l4t1 = list(det.glob("_l4_batch*")) + list(det.glob("_l4_prompt*"))
    # L4 输入侧最大件 = slim(context/<ticker>_<date>_slim.md,scan_dir 通常是 context/scan/<date>)
    slim_root = det.parent.parent
    slims = sorted(slim_root.glob(f"*_{det.name}_slim.md")) if slim_root.exists() else []
    intels = sorted(det.glob("_l4_intel_*.md"))   # 活体情报(l4-intel 盲搜落稿;config 未启用/未派 → 空)
    from autoresearch.scan.stage_timing import ensure_stage_timing
    _tmap: dict = ensure_stage_timing(det)   # mtime 推导补缺 + 写回;编排写过的 key 优先

    def _wall(key: str) -> str:
        v = _tmap.get(key)
        if isinstance(v, dict):
            v = v.get("wall_s")
        if not v:
            return "—"
        v = int(v)
        return f"{v // 60}m{v % 60:02d}s" if v >= 60 else f"{v}s"

    # P6b:effort/引擎列改读 `user_config_echo.json`(frame --json 落的当日实际调用配置)——
    # 此前是硬编码现值,和真实调用(如 L4 xhigh/sonnet·high)脱节,表面 medium 掩盖了真实档位。
    # presence-gated:无 echo 文件/字段缺失 → 回退旧硬编码值(parity,原表不变)。
    echo_agents: dict = {}
    try:
        import json as _json
        echo_agents = (_json.loads((det / "user_config_echo.json").read_text(encoding="utf-8"))
                       .get("agents") or {})
    except Exception:  # noqa: BLE001 — 无 echo = 旧硬编码现值(parity)
        echo_agents = {}

    def _eff(key: str, default: str) -> str:
        v = (echo_agents.get(key) or {}).get("effort")
        return str(v) if v else default

    def _eng(key: str, default: str) -> str:
        m = (echo_agents.get(key) or {}).get("model")
        return {"sonnet": "Sonnet", "opus": "Opus", "haiku": "Haiku"}.get(str(m).lower(), str(m)) \
            if m else default

    ens_files = sorted((det / "ensemble").glob("*.md")) if (det / "ensemble").is_dir() else []
    has_prewarm = (det / "_prewarm.json").is_file()

    # (阶段名, 引擎, effort, 计时键, LLM调用, 落盘字节, 说明)
    rows = [
        *([("预热(夜间)", "确定性", "—", "预热", 0, 0, "lake/evidence/温度预拉(_prewarm.json)")]
          if has_prewarm else []),
        ("L0/L1/L2", "确定性", "—", "L0L1L2", 0, 0, "纯 pandas,零 LLM"),
        ("旁路 策略师", _eng("strategist", "Opus"), _eff("strategist", "session"), "策略师",
         1 if strat else 0, _b(strat), "market_pack → market_view.md"),
        ("旁路 行业brief", _eng("sector_brief", "Opus"), _eff("sector_brief", "low"), "行业brief",
         len(sbriefs), _b(sbriefs), "sector pack → sector_briefs/*.md(♻️TTL 复用亦计字节)"),
        ("L3 精排", _eng("l3_rank", "Opus·holistic"), _eff("l3_rank", "max"), "L3精排",
         1 if l3 else 0, _b(l3), "通看全表选 finalists(输入表落 `_l3_table.md` 才计入)"),
        ("L4 研究", _eng("l4_card", "Opus"), _eff("l4_card", "medium"), "L4研究", len(cards),
         _b(cards) + _b(l4t1), f"{len(cards)} 张卡(早停/满卡/复用;每卡 prompt 落 `_l4_prompt_*` 才计入)"),
        *([("L4 买单ensemble", _eng("l4_card", "Opus"), _eff("l4_card", "medium"), "ensemble",
            len(ens_files), _b(ens_files), "≥OW 追加 run2/3 取中位(仅有买日)")] if ens_files else []),
        ("L4 输入·slim", "—(输入侧)", "—", "L4slim", len(slims), _b(slims),
         "harvest --slim 落稿(每卡 subagent 读入;≈4.8KB 空稿=NO_DATA 亦计=真实浪费)"),
        ("L4 输入·情报", _eng("l4_intel", "Sonnet"), _eff("l4_intel", "max"), "L4intel",
         len(intels), _b(intels),
         "l4-intel 盲搜落稿(1 文件=1 sonnet 会话;网查用量见 `token_usage.md`;未启用=0;不计入 LLM 调用合计)"),
        ("L4 新闻网查", "WebSearch", "—", "L4news", 0, 0,
         "P3 有界活体新闻(≤3/卡)+ sector/macro 网查(≤2)——无落盘 artifact,用量见 `token_usage.md`"),
        ("整合 assemble", "确定性", "—", "assemble", 0, 0, "L5 组装 + self_review(截至本表渲染)"),
    ]
    lines = ["## 各阶段耗时 & 落盘字节",
             "| 阶段 | 引擎 | effort | 墙钟 | LLM 调用 | 落盘字节 | 说明 |",
             "|---|---|---|---:|---:|---:|---|"]
    tot_calls = 0
    for name, eng, eff, tkey, calls, b, note in rows:
        tot_calls += 0 if name.startswith("L4 输入") else calls
        lines.append(f"| {name} | {eng} | {eff} | {_wall(tkey)} | {calls or '—'} | {b or '—'} | {note} |")
    lines.append(f"| **合计** | — | — | {_wall('总计')} | **{tot_calls}** | — | 墙钟 = mtime 推导下界(stage_timing.py) |")
    if cards and not list(det.glob("_l4_prompt*")):
        lines += ["", "> ⚠️ L4 输入 prompt 未落稿(`_l4_prompt_*` 缺)——上表 L4 行仅计输出;派发前先 "
                  "`uv run --no-sync python -m autoresearch.scan.agents.l4_card prompts <date>` "
                  "落稿,输入侧才可计。"]
    lines += ["", "> **token 计量**:本表**不估 token** —— 旧「落盘字节 ÷ 2.8」口径于 2026-07-24 "
              "被同一次跑动的 transcript 追溯真计量证伪(估 ~183.6k vs 加权真值 5.49M,**低估 30 倍**,"
              "且把「贵在哪」排反)。真实用量见 CP7 产出的 `token_usage.md`"
              "(`python -m autoresearch.trace.usage_harvest --session <sessionId> --out …`,按计价倍率加权)。"
              "**该文件不存在 = 本次未计量,不等于用量小。**"
              "上表三列(墙钟/调用数/落盘字节)是确定性硬事实,可直接引用。", ""]
    return lines

def _stage_overview(label: str, rows: list[dict], reason: str) -> list[str]:
    if not rows:
        return [f"\n**{label}** — _无 staging,跳过_"]
    inds = Counter(r.get("industry", "") for r in rows if r.get("industry"))
    top = "、".join(f"{k}({v})" for k, v in inds.most_common(5)) or "—"
    reps = ", ".join(str(r.get("name", "")) for r in rows[:6])
    return [f"\n**{label}** — {reason}", f"- 行业分布 top5:{top}", f"- 代表股:{reps}"]

def _portfolio_note(rows: list[dict]) -> str:
    secs = Counter((r.get("sector") or r.get("industry") or "?") for r in rows)
    top = "、".join(f"{k}×{v}" for k, v in secs.most_common(5))
    buys = [r for r in rows if r.get("rating") in ("Buy", "Overweight")]
    note = (f"买入/超配 **{len(buys)}** 只;板块集中度:{top or '—'}。"
            "注意单板块过度集中的相关性风险;按评级×置信度分配仓位,催化日历做节奏。")
    if len(buys) >= 2:                       # 买单同板块 = 1 个 bet 不是 N 个(组合视角告警)
        bsec = Counter((r.get("sector") or r.get("industry") or "?") for r in buys)
        k, v = bsec.most_common(1)[0]
        if v >= 2:
            note += f" **⚠️ {v}/{len(buys)} 只买单同属{k} = 相关性上是 1 个 bet,仓位按 1 个算。**"
    return note

def _position_overlay(scan_dir: Path, rows: list[dict]) -> str:
    """仓位建议(组合 overlay,确定性):regime 档位 + 菜单病取下沿 + 0 买一致性。缺 regime → ""。

    只作用于总仓位,不改单票评级(与策略师"方向只进 L5"同一铁律)。
    """
    try:
        meta = _load_json(scan_dir / "meta.json")
        regime = meta.get("regime")
    except Exception:  # noqa: BLE001
        return ""
    band = {"risk_off": "0–2 成", "range": "3–5 成", "trend": "5–8 成"}.get(regime or "")
    if not band:
        return ""
    n_buys = sum(1 for r in rows if r.get("rating") in ("Buy", "Overweight"))
    sick = ""
    try:
        from autoresearch.scan.menu import l4_budget
        n, _why = l4_budget(scan_dir)
        if n < 30:
            sick = "(菜单病 → 取区间下沿)"
    except Exception:  # noqa: BLE001
        pass
    tail = ("今日 0 买 → 空仓/底仓与系统读数一致,别为凑单加仓。" if n_buys == 0
            else f"{n_buys} 只买单在区间内按评级×置信度分配。")
    return (f"**仓位建议(overlay,非个股)**:regime={regime} → 总仓位基准 **{band}**{sick};{tail}")

def _knowledge_note(rows: list[dict]) -> str:
    """浮出与 buy-list 标的/行业相关的 active 经验 + 未决反馈(闭环记忆注回报告骨架)。

    store 空 / feedback_store 不可用 → 返回空串(向后兼容,老路径不破)。
    """
    try:
        import autoresearch.learning.feedback_store as fs
    except Exception:  # noqa: BLE001 — 知识库是可选层,缺了不影响出报告
        return ""
    codes = {str(r.get("code")) for r in rows if r.get("code")}
    scopes: list = [("global", "*")]
    for r in rows:
        if r.get("code"):
            scopes.append(("ticker", str(r["code"])))
        ind = r.get("sector") or r.get("industry")
        if ind:
            scopes.append(("industry", ind))
    try:
        lessons = fs.lessons_for(scopes)
        open_fb = [f for f in fs._read_jsonl(fs._FEEDBACK)
                   if f.get("status") == "open"
                   and (f.get("scope", {}).get("kind") == "global"
                        or f.get("scope", {}).get("value") in codes)]
    except Exception:  # noqa: BLE001
        return ""
    if not lessons and not open_fb:
        return ""
    lines = ["## 📌 经验 / 未决反馈(闭环记忆)"]
    if lessons:
        lines.append("**生效经验**(已注入 L2/L3 校准 + 本次研判):")
        for lsn in lessons[:8]:
            sc = lsn.get("scope", {})
            tag = "" if sc.get("kind") == "global" else f"[{sc.get('value')}] "
            lines.append(f"- {tag}{lsn['rule']}  _(conf {lsn.get('confidence', 0):.2f})_")
    if open_fb:
        lines.append("**未决反馈**(待 retro / 后续消化):")
        for f in open_fb[:6]:
            lines.append(f"- ({f.get('verdict')}) {str(f.get('note', ''))[:50]} — `{f.get('id')}`")
    return "\n".join(lines) + "\n"

def _pinned_section(scan_dir: Path, analysis_date: str, pinned_rows: list[dict],
                    l1_full: dict, l2_top: dict, ch_map: dict, vmap: dict, n_l1, n_l2,
                    pinned_path: str | Path | None = None) -> str:
    """📌 保送持仓节(design 2026-07-11 §4.1;feedback fb_20260714_001 改结构):保送持仓与真实
    精选**分列**——运行期 `lane=="pinned"` 行(`pinned_rows`,`finalists.csv` 烤入的那次跑的事实)
    渲染成与 §3 buy-list 同结构的完整表 + 「保送理由」列(不占 L3 名额、也不混进 buy-list)。

    **run-time-truth,不再挂 `load_pinned()`**:presence-gate = `pinned_rows` 非空 **或** config
    有 expired。旧设计把 gate 挂在 `load_pinned()` 的 kept 上,`pinned.jsonc` 跑后一改,旧保送票
    就从这份报告凭空消失——而 §3 又已把 `lane==pinned` 剔除,两头都没有 → 保送票蒸发。改挂
    finalists.csv 的 lane 后,报告永远忠实于它自己那次运行。config 只再供 **expired** 尾注
    (过期条目不会出现在 finalists 里,只能从 config 读)。
    """
    expired: list[dict] = []
    try:
        from autoresearch.scan.user_config import load_pinned
        expired = load_pinned(analysis_date, path=pinned_path).get("expired") or []
    except Exception:  # noqa: BLE001 — 可选层,坏 pinned.json 不挡整份报告发布
        expired = []
    if not pinned_rows and not expired:
        return ""
    lines = ["## 📌 保送持仓(用户手工直通;L1→L5 全程强留,**不占 L3 名额**,与真实精选分列)", "",
             "_这些是你手工保送的持仓/关注票,不是漏斗筛出来的买入候选;评级仍按各自 rubric 独立"
             "判定,不因『保送』放松尽调。_", ""]
    if pinned_rows:
        lines += _buylist_table_lines(pinned_rows, l1_full, l2_top, ch_map, vmap,
                                      n_l1, n_l2, note_col=True)
    else:
        lines += ["_本次运行内无强留的保送持仓(finalists 无 lane=pinned 行)。_"]
    if expired:
        lines += ["", "_已过期(不再强留,续期请更新 pinned.jsonc):_"]
        for e in expired:
            note = f" ——{e['note']}" if e.get("note") else ""
            lines.append(f"- {e['code']}{note}(已于 {e.get('expires', '—')} 过期)")
    return "\n".join(lines)

def _sector_view_section(scan_dir: Path) -> str:
    """行业 brief 研判段汇总(Phase 3;方向性内容只在整合层——地形段已注 L3/L4)。无 briefs → ''。"""
    d = scan_dir / "sector_briefs"
    if not d.is_dir():
        return ""
    try:
        from autoresearch.sector.brief import extract_view, parse_direction
    except Exception:  # noqa: BLE001
        return ""
    parts: list[str] = []
    for p in sorted(d.glob("*.md")):
        try:
            view = extract_view(p.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            continue
        if view:
            parts.append(f"**{p.stem}**(方向:{parse_direction(view) or '—'})\n\n{view}")
    if not parts:
        return ""
    return "## 🏭 行业研判(sector-research lite · 仅整合层)\n\n" + "\n\n".join(parts)

def _same_chain_block(rows) -> str:
    """同申万一级 ≥2 只 finalist → 并排一行(择链上最佳表达,同链多买=1 个 bet)。<2 → ''。"""
    by_sec: dict[str, list[dict]] = {}
    for r in rows:
        sec = str(r.get("sector") or r.get("industry") or "").strip()
        if sec and sec != "nan":
            by_sec.setdefault(sec, []).append(r)
    multi = {s: rs for s, rs in by_sec.items() if len(rs) >= 2}
    if not multi:
        return ""
    lines = ["#### 🔗 同链对比(同申万一级 ≥2 只 → 择链上最佳表达,同链多买=1 个 bet)",
             "| 行业 | 同链 finalists(评级 · 目标) |", "|---|---|"]
    for sec in sorted(multi, key=lambda s: -len(multi[s])):
        cell = "、".join(f"{r.get('name', '')}(**{r.get('rating', '—')}** · {r.get('target', '—')})"
                         for r in sorted(multi[sec], key=_sortkey))
        lines.append(f"| {sec}({len(multi[sec])}只) | {cell} |")
    return "\n".join(lines)

def _load_market_view(scan_dir: Path) -> str:
    """读 L2 后策略师写的 market_view.md staging(缺 → '')。assemble 仍零-LLM(只读文件)。

    嵌入前剥样板:自带 H1(报告已有 H1,双标题是噪声)+ 免责节(报告已有诚实局限)。"""
    p = scan_dir / "market_view.md"
    if not p.exists():
        return ""
    lines = p.read_text(encoding="utf-8").strip().splitlines()
    if lines and lines[0].lstrip().startswith("# "):
        lines = lines[1:]
    for i, ln in enumerate(lines):
        if ln.lstrip().startswith("#") and "免责" in ln:
            lines = lines[:i]
            break
    return "\n".join(lines).strip()

def regime_and_drift(scan_dir: Path) -> tuple[str, str]:
    """从 L1 帧算今日 regime + 与 weights.meta.regime_calib 比对 → (summary 行, drift reason|'')。

    给 summary 一句 regime 定性(哑铃/落刀的关键上下文)+ 把 drift 喂 self_review warn。
    缺 L1/regime 依赖 → ('', '')(老路不破)。
    """
    try:
        import pandas as pd

        from autoresearch.common.regime import classify_regime, detect_drift
        from autoresearch.common.scoring import _load_weights
    except Exception:  # noqa: BLE001
        return "", ""
    src = scan_dir / "L1_scored_full.csv"
    if not src.exists():
        src = scan_dir / "L1_recall_top1000.csv"
    if not src.exists():
        return "", ""
    try:
        df = pd.read_csv(src)
    except Exception:  # noqa: BLE001
        return "", ""
    st = classify_regime(df)
    drifted, reason = detect_drift(st, _load_weights().get("meta", {}))
    zh = {"trend": "趋势", "range": "震荡", "risk_off": "避险"}.get(st.label, st.label)
    line = (f"**市场 regime**:{zh}(breadth {st.breadth:.0%}·中位动量 {st.med_mom:+.1f}%"
            + (f";⚠️ {reason}" if drifted else "") + ")")
    return line, (reason if drifted else "")

def _degraded_line(scan_dir: Path) -> str:
    """B 级数据降级一行(`degraded.json`,由 `universe.run` 落)。presence-gated:无文件 → ""。

    A 级(地基)违约在取数处就抛异常阻断了,报告压根不会生成;所以这里显示的一定是 B 级增强端点
    缺失(北向/质押/席位/公告…)。**降级必须可见**:否则读报告的人无从分辨某面旗是"真没有"
    还是"没取到"(design 2026-07-12-data-contracts-design.md §1:系统有降级能力,曾经没有
    「我降级了」的传达能力)。
    """
    p = Path(scan_dir) / "degraded.json"
    if not p.exists():
        return ""
    try:
        recs = json.loads(p.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return ""
    if not recs:
        return ""
    from autoresearch.data.contracts import render

    return render(recs)

def _self_review_banner(scan_dir: Path, rows: list[dict], summary_text: str,
                        regime_drift: str = "") -> str:
    """发布前机械自检(self_review 硬门)→ 报告顶部 banner。缺依赖/无问题 → 空串(老路不破)。"""
    try:
        import autoresearch.learning.self_review as self_review
    except Exception:  # noqa: BLE001
        return ""
    l1 = {}
    if (scan_dir / "L1_scored_full.csv").exists():
        l1 = {str(r.get("code", "")).zfill(6): r for r in _read_csv(scan_dir / "L1_scored_full.csv")}
    finals = []
    for r in rows:
        lf = l1.get(str(r.get("code", "")).zfill(6), {})
        finals.append({"code": str(r.get("code", "")).zfill(6), "rating": r.get("rating"),
                       "sector": r.get("sector") or r.get("industry"),
                       "composite": lf.get("composite"), "winner_rate": lf.get("winner_rate"),
                       "pct_60d": lf.get("pct_60d"), "rsi6": lf.get("rsi6"),
                       "main_net_ratio": lf.get("main_net_ratio"),
                       "rubric_suggest": r.get("rubric_suggest"), "rubric_dev": r.get("rubric_dev")})
    n_present = sum(1 for r in rows if r.get("target") != "⚠️卡片缺失")
    lessons = []
    try:
        import autoresearch.learning.feedback_store as fs
        lessons = fs.lessons_for([("global", "*")])
    except Exception:  # noqa: BLE001
        pass
    ctx = {"finalists": finals, "n_cards_expected": len(rows), "n_cards_present": n_present,
           "summary_text": summary_text, "lessons": lessons, "regime_drift": regime_drift,
           "flow": {                                       # 编排完备性 lint(LLM 段可能被静默跳过)
               "buys_n": sum(1 for r in rows if r.get("rating") in ("Buy", "Overweight")),
               "verify_n": len(_load_verify(scan_dir)),
               "has_market_view": (scan_dir / "market_view.md").exists(),
               "finalists_n": len(rows)}}
    import contextlib
    res = self_review.review(ctx)
    with contextlib.suppress(Exception):                            # 卡片契约 lint(在 dump 前合并 → 留痕)
        extra = self_review.card_contract_lint(scan_dir)
        if extra:
            res["failures"].extend(extra)
            res["n_warn"] = res.get("n_warn", 0) + len(extra)
    with contextlib.suppress(Exception):                            # intel as-of 前视机检(advisory)
        intel_extra = self_review.intel_future_dates_lint(scan_dir, scan_dir.name)
        if intel_extra:
            res["failures"].extend(intel_extra)
            res["n_warn"] = res.get("n_warn", 0) + len(intel_extra)
    with contextlib.suppress(Exception):                            # intel 时效三窗机检(Wave7 批 N,advisory)
        recency_extra = self_review.intel_recency_lint(scan_dir, scan_dir.name)
        if recency_extra:
            res["failures"].extend(recency_extra)
            res["n_warn"] = res.get("n_warn", 0) + len(recency_extra)
    with contextlib.suppress(Exception):                            # 产物形状 lint(线C 2026-07-18;全 warn/info 起步)
        shape_extra = self_review.product_shape_lint(scan_dir, scan_dir.name)
        if shape_extra:
            res["failures"].extend(shape_extra)
            res["n_warn"] = res.get("n_warn", 0) + sum(1 for x in shape_extra if x.get("severity") == "warn")
    with contextlib.suppress(Exception):
        self_review.dump_gate_fires(scan_dir, res, scan_dir.name)   # R3 留痕;IO 失败不阻发布
    with contextlib.suppress(Exception):
        self_review.dump_ow_gate_fires(scan_dir)          # OW三门失守 binding 行;IO 失败不阻发布
    return self_review.render_banner(res)

def _buylist_table_lines(rows: list[dict], l1_full: dict, l2_top: dict, ch_map: dict,
                         vmap: dict, n_l1, n_l2, *, note_col: bool = False) -> list[str]:
    """逐阶段结论表(§3 buy-list 与 📌 保送持仓共用):header + sep + 每行。

    `note_col=True` 末尾追加「保送理由」列(取 pinned_note)。**genuine 场景(note_col=False)
    输出与历史 §3 渲染逐字节一致**(test_assemble buy-list 契约:L1召回/L2粗排/L3精排/L4研究/
    评级/目标 列 + 可选 🛡️红队 列)。"""
    vcol, vsep = (" 🛡️红队 |", "---|") if vmap else ("", "")
    ncol, nsep = (" 保送理由 |", "---|") if note_col else ("", "")
    lines = [
        f"| # | 名称 | 板块 | L1召回(#/{n_l1}) | L2粗排(#/{n_l2}) | L3精排 | L4研究·结论 | 评级 | 目标(EV) |"
        + vcol + ncol,
        "|---|---|---|---|---|---|---|---|---|" + vsep + nsep]
    for i, r in enumerate(rows, 1):
        code = str(r.get("code", "")).zfill(6)
        vcell = f" {_verify_badge(code, vmap)} |" if vmap else ""
        l3txt = _strip(r.get("thesis") or r.get("triage_reason", ""))
        conv = r.get("conviction")
        l3cell = l3txt + (f"·conv{conv}" if conv else "")
        rating_cell = f"**{r.get('rating', '—')}**" + (" 🎭复核分歧" if r.get("ens_flag") else "")
        ncell = f" {_strip(r.get('pinned_note', '') or '—')} |" if note_col else ""
        lines.append(
            f"| {i} | {r.get('name', '')} | {r.get('sector') or r.get('industry', '')} "
            f"| {_l1_cell(code, l1_full, ch_map)} | {_l2_cell(code, l2_top)} | {l3cell} "
            f"| {_strip(r.get('l4', '—'))} "
            f"| {rating_cell} | {r.get('target', '—')} |" + vcell + ncell)
    return lines

def build_summary(scan_dir: Path, analysis_date: str, hhmm: str, folder: str,
                  pinned_path: str | Path | None = None) -> str:
    meta = _load_json(scan_dir / "meta.json")
    recall = _read_csv(scan_dir / "L1_recall_top1000.csv")
    keep = _read_csv(scan_dir / "L2_gbdt_top200.csv")
    finals = _read_csv(scan_dir / "finalists.csv")
    l1_full = {str(r.get("code", "")).zfill(6): r for r in _read_csv(scan_dir / "L1_scored_full.csv")}
    l2_top = {str(r.get("code", "")).zfill(6): r for r in keep}
    rows = [_finalist_row(scan_dir, fr) for fr in finals]
    for r in rows:
        r["_source_rating"] = r.get("rating", "—")
    vmap = _load_verify(scan_dir)   # Tier-3 对抗验证;降级/否决折回评级(踢出买单),无 verify.csv 则空(老路不破)
    for r in rows:
        v = vmap.get(str(r.get("code", "")).zfill(6))
        if v and v["verdict"] in ("降级", "否决"):
            r["rating"] = _apply_verify_downgrade(r.get("rating", "Hold"), v["verdict"])
            r["proposal"] = _PROPOSAL_BY_RATING.get(r["rating"], r.get("proposal", "—"))
    for r in rows:
        r["_post_verify_rating"] = r.get("rating", "—")
    emap = _load_ensemble(scan_dir)  # 买单复核 ensemble(B10 集成配方,≥OW 追加 2 独立 run 取中位);无 _ensemble.json 则空(老路不破)
    for r in rows:
        e = emap.get(str(r.get("code", "")).zfill(6))
        r["_ensemble_ratings"] = list((e or {}).get("ratings") or [])
        if not e:
            continue
        folded = _apply_ensemble_fold(r.get("rating", "Hold"), e)
        if folded != r.get("rating"):
            r["rating"] = folded
            r["proposal"] = _PROPOSAL_BY_RATING.get(folded, r.get("proposal", "—"))
        if _ensemble_flag(e):
            r["ens_flag"] = True                # 🎭复核分歧:spread≥2 → 行 badge + 组合视角人裁提示
    _dump_final_ratings(scan_dir, rows)   # P0-2:两个 fold 循环已跑完 → rows["rating"] 即终评级,落盘供 retro 优先 join(含保送,retro 口径不变)
    _dump_decision_records(scan_dir, rows, vmap, emap)
    rows.sort(key=_sortkey)
    # ── feedback fb_20260714_001:保送(lane==pinned)与真实精选分列 ──
    # lane 是运行期烤进 finalists.csv 的事实(_inject_pinned_finalists 强改判),比当前 pinned.jsonc
    # 更可靠(config 跑后可能被改)。genuine=真实精选(进 §3 buy-list);pinned=保送持仓(进 📌 表)。
    pinned_rows = [r for r in rows if str(r.get("lane", "")).strip() == "pinned"]
    genuine_rows = [r for r in rows if str(r.get("lane", "")).strip() != "pinned"]
    # 表头分母 + 命中队列 map(§3 与 📌 表共用;上移到两处渲染之前)
    n_l1 = meta.get("after_gate_a") or meta.get("universe") or len(l1_full) or "?"
    n_l2 = meta.get("l2_n") or len(l2_top) or "?"
    ch_map = {c: (r.get("recall_channels") or "") for c, r in l2_top.items()}   # 命中队列(随 keep 流到 L2 表)
    regime_line, regime_drift = regime_and_drift(scan_dir)

    out = [f"# A股扫描 v2 · Buy-List & 漏斗 — {analysis_date} {hhmm[:2]}:{hhmm[2:]}\n",
           "_六段漏斗:选集→召回→粗排(分层采样)→精排→研究→整合。L0/L1/L2 确定性,L3/L4 Claude 为引擎,"
           "**仅供研究,非投资建议。**_\n"]
    if regime_line:
        out.append(regime_line + "\n")

    # ── S1 情绪温度计一行(regime 行旁,presence-gated:temperature.csv 无当日读数 → 不加)──
    from autoresearch.scan.market import render_temperature_line  # lazy:避免 import cycle
    temp_line = render_temperature_line(analysis_date)
    if temp_line:
        out.append(temp_line + "\n")

    # ── 市场研判(首席策略师视角;策略师未写 → 回退确定性脉搏)──
    mv = _load_market_view(scan_dir)
    if mv:
        from autoresearch.scan.market import render_funnel_readout  # lazy:避免 import cycle
        out += ["## 📈 今日 A 股市场(首席策略师视角)\n", mv, ""]
        readout = render_funnel_readout(scan_dir)
        if readout:
            out += [readout]
    else:
        from autoresearch.scan.market import market_pack, render_fallback_pulse
        pulse = render_fallback_pulse(market_pack(scan_dir))
        if pulse:
            out += ["## 📈 今日 A 股市场\n", pulse, ""]

    # ── 影子组合成绩单一行(spec 2026-07-05 wave §A1;presence-gated:文件缺 → 不加)──
    # 只在真实现场注入(与 run() 的 is_real 判据同姿势):tmp 测试目录从此不受开发机全局
    # reports/learning/paper_nav_summary.txt 污染(该文件由真实 prelude 跑动落盘,与 tmp scan_dir 无关)。
    if scan_dir == Path("context/scan") / analysis_date:
        pn = Path("reports/learning/paper_nav_summary.txt")
        if pn.exists():
            try:
                nav_line = pn.read_text(encoding="utf-8").strip()
            except Exception:  # noqa: BLE001
                nav_line = ""
            if nav_line:
                out += [nav_line, ""]

    # (观察单日检节已退役 —— 用户裁定 fb_20260714_002:即便 watchlist_status.csv 在也不渲染。)

    # ── 📌 保送(pinned 直通;presence-gated:无 pinned.json/kept+expired 皆空 → 跳过)──
    pin_sec = _pinned_section(scan_dir, analysis_date, pinned_rows, l1_full, l2_top,
                              ch_map, vmap, n_l1, n_l2, pinned_path=pinned_path)
    if pin_sec:
        out += [pin_sec, ""]

    # ── 🎯 看多行业 top3(P7:确定性零 LLM;presence-gated,失败不挡发布)──
    try:
        from autoresearch.scan.market import market_pack as _mp2, render_sector_top3
        top3_sec = render_sector_top3(_mp2(scan_dir))
    except Exception:  # noqa: BLE001
        top3_sec = ""
    if top3_sec:
        out += [top3_sec, ""]

    sect = _sector_view_section(scan_dir)   # Phase 3:行业研判(briefs 研判段,方向性只在整合层)
    if sect:
        out += [sect, ""]

    # ── 1. 漏斗数量 ──
    out += ["## 1. 漏斗(数量)"] + _funnel_rows(meta, len(keep) or "?", len(genuine_rows),
                                              len(rows), n_pinned=len(pinned_rows)) + [""]

    # ── 数据降级(presence-gated:无 degraded.json → 不加行)──
    # A 级(地基)违约会在取数处直接抛异常阻断,能出报告说明地基是全的;这里显示的是 B 级增强端点
    # 缺失(北向/质押/席位/公告…)。**降级必须可见** —— 否则读报告的人无从知道哪些旗是"真没有"、
    # 哪些是"没取到"(design 2026-07-12-data-contracts-design.md)。
    dline = _degraded_line(scan_dir)
    if dline:
        out += [dline, ""]

    # ── 2. 各阶段卡点 + 概览 ──
    out += ["## 2. 各阶段卡点 & 股票概览"]
    out += _stage_overview("召回(L1)", recall, "复合分 top;快因子(动量/资金结构/技术)主导排序,慢因子带下游判断。")
    out += _stage_overview("粗排(L2)", keep, f"确定性分层采样({meta.get('l2_engine', 'stratified')});sn_composite 排序+风格桶 floor+sector cap,零模型零 LLM。")
    from autoresearch.scan.menu import menu_health  # lazy:确定性菜单体检,缺 staging 自 ""
    mh = menu_health(scan_dir)
    if mh:
        out += ["", mh]
    out += ["", "**精排(L3)入选(风险/催化;论点见 buy-list 表 L3精排 列,不重复两遍)**:"]
    if finals:
        for fr in finals:
            out.append(f"- **{fr.get('name', '')}({fr.get('code', '')})** · {fr.get('sector', '')} — "
                       f"风险:{_strip(fr.get('risk', ''))};催化:{_strip(fr.get('catalyst', ''))}")
    else:
        out.append("_无 finalists.csv_")
    out.append("")

    # ── 3. 投资建议 ──(vmap 已在上方加载并折回评级;保送持仓已分列进「📌 保送持仓」节,这里只含真实精选)
    xref = ";保送持仓见「📌 保送持仓」节" if pinned_rows else ""   # 无保送时不留悬空引用
    out += [f"## 3. 投资建议(buy-list, {len(genuine_rows)} 只真实精选,按 评级 → 确信度 排序;"
            f"逐阶段结论{xref})\n"]
    out += _buylist_table_lines(genuine_rows, l1_full, l2_top, ch_map, vmap, n_l1, n_l2)
    out.append(f"\n_列注:**L1召回** #复合分名次/{n_l1}·命中队列(越小越强;低复合分票靠某条队列召回→名次很大);"
               f"**L2粗排** #分层重排名次/{n_l2}·gbdt分(遗留列名);**L3精排** = Opus holistic 论点 + conviction;"
               f"**L4研究·结论** = 决策卡深核后的关键定级依据(≥OW 取多头驱动,否则取空头/早停因);"
               f"置信度见各决策卡(30 行全『中』的列已删)。_")
    gh = _gate_histogram(scan_dir, genuine_rows)
    if gh:
        out += ["", gh]
    out += _verify_detail(vmap)
    from autoresearch.scan.calendar import calendar_section  # lazy:日历块,缺 staging 自 ""
    cal = calendar_section(scan_dir)
    if cal:
        out += ["", cal]
    out += ["", "### 组合视角", _portfolio_note(genuine_rows)]
    ens_lines = _ensemble_dissent_lines(emap)   # 🎭 spread≥2 人裁提示(presence-gated:无分歧 → [])
    if ens_lines:
        out += [""] + ens_lines
    chain = _same_chain_block(genuine_rows)  # Phase 3:同链 ≥2 卡并排(择链上最佳表达素材;保送不计)
    if chain:
        out += ["", chain]
    pos = _position_overlay(scan_dir, genuine_rows)
    if pos:
        out += ["", pos]
    out += [""]
    kn = _knowledge_note(rows)
    if kn:
        out += [kn]
    out += _stage_token_estimate(scan_dir)
    # ── ⏳ 待裁决提案 nag(presence-gated;仅真实现场注入,镜像 paper_nav 成绩单守卫防 tmp 测试污染)──
    if scan_dir == Path("context/scan") / analysis_date:
        nag = _proposals_nag()
        if nag:
            out += [nag, ""]
    out += ["## 诚实局限",
            "- 召回/粗排为启发式 + fwd_2_oc 超短主尺 IC 校准(L1 复合分、L2 sn_composite 同口径;T+1/T+5 参考),随 regime 漂移;L3/L4 为 Claude 推理产出。",
            "- 业绩/龙虎榜/预告有披露滞后;无权限端点降级标注。",
            "- A股涨跌停/停牌使名义止损未必可执行(见各决策卡执行段)。",
            f"\n_明细 + 漏斗溯源:`reports/scan/{folder}/`(summary.md + details/〈名称〉.md + trace/;目录名=运行时刻,数据日见 manifest.json)_"]
    body = "\n".join(out)
    banner = _self_review_banner(scan_dir, rows, body, regime_drift=regime_drift)   # UZI self-review 硬门:fail 顶到最前
    return f"{banner}\n{body}" if banner else body

def _proposals_nag() -> str:
    """## ⏳ 待裁决提案(open 看板;presence-gated:缺文件/无 open/坏行 → "")。

    运营节奏 nag:proposals 攒着不裁 = 闭环学习卡死("过度建设跑动不足"的解药是节奏不是机制)。
    行渲染/排序/标注(龄·配对·疑失效)委托 feedback_store.proposals_nag_lines(看板自清洁,
    机器只整理不裁决);账本缺/空/坏行 → ""(原行为,parity 不破)。
    """
    try:
        from autoresearch.learning.feedback_store import proposals_nag_lines  # lazy 接线
        lines = proposals_nag_lines()
    except Exception:  # noqa: BLE001 — IO/渲染失败当无提案,不阻发布
        return ""
    if not lines:
        return ""
    return ("## ⏳ 待裁决提案\n" + "\n".join(lines)
            + "\n\n_提案满 20 交易日未裁将持续在此提醒;裁决走 feedback / scan-retro 流程,别攒。_")
