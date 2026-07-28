"""Scan report/detail/trace publisher and L5 run orchestration."""
from __future__ import annotations

import contextlib
import json
import re
import shutil
from datetime import datetime
from pathlib import Path

from autoresearch.scan.l4.parsers import _load_json, _read_csv
from autoresearch.scan.report_sections import _funnel_rows, build_summary


def _safe_name(name: str) -> str:
    """股票名称 → 文件名安全(去 / \\ : * ? " < > | 与空白,*ST→ST);空则回退 未命名。"""
    return re.sub(r'[/\\:*?"<>|\s]', "", str(name)).strip() or "未命名"

def _publish_details(scan_dir: Path, detail_out: Path) -> int:
    """把 L4 staging 决策卡发布到 details/,文件名用**股票名称**(非 ticker);只发当前 finalists。

    staging 卡仍以 <code>.md 暂存(parse_rating/retro 内部按 code);发布层改名 <名称>.md 便于人读。
    发布时若有 `_l4_intel_<code>.md`(活体情报盲搜稿)→ 原文附在卡片尾部(fb_20260714_004:
    读者要在 details 里直接看到当日新闻依据,不用去翻 staging)。附录只加在**发布副本**,
    staging 卡不动;parse_rating 两遍法先认卡面 `Rating:` 标签行,intel 中文文本不干扰评级解析。
    """
    src = scan_dir / "details"
    if not src.is_dir():
        return 0
    n = 0
    for fr in _read_csv(scan_dir / "finalists.csv"):
        code = str(fr.get("code", "")).zfill(6)
        card = src / f"{code}.md"
        if not card.exists():
            continue
        name = _safe_name(fr.get("name", "")) or code
        dst = detail_out / f"{name}.md"
        if dst.exists():                       # 同名兜底:挂 code 避免覆盖
            dst = detail_out / f"{name}_{code}.md"
        shutil.copy2(card, dst)
        intel = scan_dir / f"_l4_intel_{code}.md"
        if intel.exists():
            try:
                body = intel.read_text(encoding="utf-8").strip()
            except OSError:
                body = ""
            if body:
                with dst.open("a", encoding="utf-8") as fh:
                    fh.write("\n\n---\n\n## 🕵️ 当日活体情报(盲搜原文·仅事实采集,评级不受此节影响)\n\n")
                    fh.write(body + "\n")
        # ── 价格断言对账(Wave1 ⑤-2,advisory;含 intel 附录一起对——pr_006 的捏造在 intel 侧)──
        with contextlib.suppress(Exception):
            from autoresearch.scan import price_claims
            card_txt = dst.read_text(encoding="utf-8")
            res = price_claims.audit_card_text(
                card_txt, name=str(fr.get("name", "") or ""), code6=code,
                date=scan_dir.name, bars_fn=price_claims.bars_for)
            if res["n_claims"]:
                bad = res["mismatches"]
                if bad:
                    det = ";".join(f"{b['date'][4:6]}-{b['date'][6:]} 称"
                                   f"{('涨停' if b['kind'] == 'limit' else str(b['claimed']) + '%')}"
                                   f" 实为{b['actual']}%" for b in bad[:3])
                    line = (f"\n\n---\n_🔎 价格断言对账(确定性·advisory):{res['n_claims']} 条可对账,"
                            f"**{len(bad)} 条不符** → {det}_\n")
                else:
                    line = (f"\n\n---\n_🔎 价格断言对账(确定性·advisory):{res['n_claims']} 条可对账,"
                            f"0 条不符_\n")
                with dst.open("a", encoding="utf-8") as fh:
                    fh.write(line)
        n += 1
    return n

def _funnel_md(scan_dir: Path, analysis_date: str) -> str:
    meta = _load_json(scan_dir / "meta.json")
    keep = _read_csv(scan_dir / "L2_gbdt_top200.csv")
    finals = _read_csv(scan_dir / "finalists.csv")
    n_pinned = sum(1 for r in finals if str(r.get("lane", "")).strip() == "pinned")   # 保送不占 L3 名额
    n_genuine = len(finals) - n_pinned
    lines = [f"# 漏斗溯源 — {analysis_date}\n", "六段:选集→召回→粗排(分层采样)→精排→研究→整合。\n"]
    lines += _funnel_rows(meta, len(keep) or "?", n_genuine, len(finals), n_pinned=n_pinned)
    lines += ["", f"权重来源:{meta.get('weights_source', '?')};L2 引擎:{meta.get('l2_engine', '?')};"
              f"universe 源:{meta.get('source', '?')}。",
              "各阶段明细见同目录 CSV(L1_recall_top1000 / L2_gbdt_top200 / L3_fine_finalists)。"]
    return "\n".join(lines)

def _archive_reasoning(scan_dir: Path, pdir: Path) -> int:
    """把各阶段 LLM 中间推理件(prompt/批表/keep-judged/calib)归档到
    trace/reasoning/{l2,l3,l4}/,让发布报告自带可追溯的 LLM 输入;缺失静默跳过。"""
    routes = [
        # L2 已下沉确定性(分层采样),无 LLM 推理件;L3 holistic 选股 + L4 级联 + Tier-3 验证留痕。
        ("l3", lambda n: n.startswith("_l3")),
        ("l4", lambda n: n.startswith("_l4")),       # 含 _l4_tier2_<code>.md(Tier-2 复核稿)
        ("verify", lambda n: n.startswith("_v_") or n == "verify.csv"),  # Tier-3 买单对抗验证
    ]
    n = 0
    for stage, match in routes:
        for p in sorted(scan_dir.glob("*")):
            if p.is_file() and match(p.name):
                dst = pdir / "reasoning" / stage
                dst.mkdir(parents=True, exist_ok=True)
                shutil.copy2(p, dst / p.name)
                n += 1
    return n

def _publish_pipeline(scan_dir: Path, out_base: Path, analysis_date: str) -> int:
    """把各阶段 staging 产物发布到 <YYYYMMDD_HHMM>/trace/(漏斗溯源 + reasoning 推理留痕)。"""
    pdir = out_base / "trace"
    pdir.mkdir(parents=True, exist_ok=True)
    mapping = {
        "meta.json": "L0_universe_meta.json",
        "run_contract.json": "run_contract.json",          # 运行身份契约(配置/保送/数据策略/hash)
        "run_health.json": "run_health.json",              # 运行体检(NaN 降级/churn/L4 阶段效能)
        "weights_used.json": "weights_used.json",          # 重放快照(当日实际权重)
        "L1_scored_full.csv": "L1_scored_full.csv",        # 全量打分(所有过门股 sorted + recalled 标记)
        "L1_recall_top1000.csv": "L1_recall_top1000.csv",  # 召回工作集(top N)
        "L2_gbdt_top200.csv": "L2_gbdt_top200.csv",        # 粗排:GBDT 学习重排 top N(确定性)
        "L3_judged_full.csv": "L3_judged_full.csv",        # 精排全量判断(holistic 通看 ~200,非仅 finalists)
        "finalists.csv": "L3_fine_finalists.csv",          # 精排最终入选(top N)
    }
    n = 0
    for src, dst in mapping.items():
        p = scan_dir / src
        if p.exists():
            shutil.copy2(p, pdir / dst)
            n += 1
    wp = Path("context/factor_lab/weights.json")
    if wp.exists():
        shutil.copy2(wp, pdir / "L1_weights.json")
        n += 1
    sb = scan_dir / "sector_briefs"
    if sb.is_dir():                                     # Phase 3:行业 brief 随 trace 归档(留痕)
        dst = pdir / "sector_briefs"
        dst.mkdir(parents=True, exist_ok=True)
        for p in sorted(sb.glob("*.md")):
            shutil.copy2(p, dst / p.name)
            n += 1
    (pdir / "funnel.md").write_text(_funnel_md(scan_dir, analysis_date), encoding="utf-8")
    n += _archive_reasoning(scan_dir, pdir)
    return n + 1

def run(analysis_date: str, scan_dir: Path | None = None, out_root: Path | None = None,
        hhmm: str | None = None, run_date: str | None = None,
        pinned_path: str | Path | None = None) -> Path:
    scan_dir = scan_dir or Path("context/scan") / analysis_date
    out_root = out_root or Path("reports/scan")
    is_real = Path(scan_dir).resolve() == (
        Path("context/scan") / analysis_date
    ).resolve()
    now = datetime.now()
    hhmm = hhmm or now.strftime("%H%M")
    # 发布目录时间戳 = **实际运行时刻**(run_date 仅自测注入);数据日 analysis_date 另记 manifest,与目录名解耦
    run_compact = (run_date or now.strftime("%Y-%m-%d")).replace("-", "")
    folder = f"{run_compact}_{hhmm}"
    out_base = out_root / folder                       # reports/scan/<运行日YYYYMMDD>_<HHMM>/
    detail_out = out_base / "details"
    detail_out.mkdir(parents=True, exist_ok=True)
    n_cards = _publish_details(scan_dir, detail_out)
    import contextlib

    from autoresearch.scan import health as _health  # lazy:体检失败不阻发布
    with contextlib.suppress(Exception):
        _health.write_run_health(scan_dir)             # 先写 staging,再随 trace mapping 带走
        # ⚠️ 这一次**必须**在 build_summary 之前:`product_shape_lint` 的 force_full 探针读
        # `run_health.l4_phases`,而它是 presence-gated —— run_health 缺席 = 该探针静默跳过
        # (FN-1 家族)。但 build_summary 内部才写 gate_fires.csv,所以此刻的 artifacts 列表
        # 必然把它记成 missing(07-24 实锤:run_health 13:16:21 / gate_fires 13:16:22)。
        # 故 build_summary 之后再刷一次(见下方),让落盘的那份 missing 列表说真话。
    with contextlib.suppress(Exception):               # P0-4:逐卡过程分 checklist(presence-gated,失败不阻发布)
        from autoresearch.learning.process_score import write_process_scores
        write_process_scores(scan_dir)
    n_pipe = _publish_pipeline(scan_dir, out_base, analysis_date)   # trace/ 挂 out_base(details 同级)
    from autoresearch.scan.artifacts import ARTIFACT_INDEX_SCHEMA_VERSION
    from autoresearch.scan.decision_record import DECISION_RECORD_SCHEMA_VERSION
    from autoresearch.scan.run_contract import load_run_contract

    manifest = {                                             # retro 按 analysis_date 定位(目录名≠数据日)
        "analysis_date": analysis_date,
        "generated_at": now.isoformat(timespec="seconds"),
        "hhmm": hhmm,
        "artifact_index_schema_version": ARTIFACT_INDEX_SCHEMA_VERSION,
        "decision_record_schema_version": DECISION_RECORD_SCHEMA_VERSION,
    }
    contract_path = scan_dir / "run_contract.json"
    if contract_path.exists():
        with contextlib.suppress(Exception):
            contract = load_run_contract(contract_path)
            manifest.update({
                "run_id": contract.run_id,
                "contract_hash": contract.contract_hash,
                "run_contract_schema_version": contract.schema_version,
            })
    (out_base / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False), encoding="utf-8")
    md = build_summary(scan_dir, analysis_date, hhmm, folder, pinned_path=pinned_path)
    try:
        # usage_harvest 通常在 GATE4 后才完成：此刻无 JSON 就明确落 UNMEASURED；
        # CP7 随后用 post_run observe 原位替换本 managed section。
        from autoresearch.scan.post_run import (
            inject_run_observation_section,
            publish_run_observation,
        )

        observation = publish_run_observation(scan_dir, real_scan=is_real)
        md = inject_run_observation_section(md, observation["markdown"])
    except Exception as exc:  # noqa: BLE001 — 观测控制面不能阻断报告
        from autoresearch.scan.post_run import inject_run_observation_section

        md = inject_run_observation_section(
            md,
            "## 💸 成本与时延观测\n\n"
            f"- 计量:UNMEASURED · 观测控制面异常:{type(exc).__name__}\n\n"
            "_未计量不等于零成本；该异常不改变任何评级或候选。_",
        )
    summary_path = out_base / "summary.md"
    summary_path.write_text(md, encoding="utf-8")
    from autoresearch.scan.stage_result import safe_record_stage_result

    safe_record_stage_result(
        scan_dir,
        stage="assemble",
        status="SUCCEEDED",
        artifacts=[
            "final_ratings", "decision_records", "gate_fires", "run_health",
            "summary", "manifest",
        ],
        metrics={"n_cards": n_cards, "n_trace_before_final": n_pipe},
        warnings=[],
        error=None,
    )
    with contextlib.suppress(Exception):
        # 正式 gate4 CLI 仍由主会话执行；此处只为最终 health/index 先写同一份影子事实。
        # StageResult 语义幂等，随后 CLI 对相同结果不会刷新时间或 hash。
        from autoresearch.scan.gates import gate4, record_gate_stage_result

        record_gate_stage_result(scan_dir, gate4(scan_dir))
    with contextlib.suppress(Exception):
        # 报告先落事实，再由可重放 consumer 独立刷新学习账本。测试/历史现场只
        # 初始化空回执，不触碰全局知识库；真实现场才实际消费。
        from autoresearch.scan.outbox import safe_emit_finalization_events
        from autoresearch.scan.post_run import (
            initialize_consumer_state,
            safe_run_consumers,
        )

        if safe_emit_finalization_events(scan_dir) is not None:
            initialize_consumer_state(scan_dir)
            if is_real:
                safe_run_consumers(scan_dir)
    with contextlib.suppress(Exception):
        # Wave6 Q6:build_summary 内部才落 gate_fires.csv —— 上面那次快照必然把它记成
        # missing(07-24 实锤)。这里刷一次让 artifacts/missing 说真话;函数是纯快照,幂等。
        _health.write_run_health(scan_dir)
    with contextlib.suppress(Exception):
        # 最终快照必须等 manifest/summary/gate_fires/第二次 health 全部落盘后再 hash。
        # 同时覆盖 trace 里 assemble 前发布的旧 health，保证 staging/trace 同一事实。
        from autoresearch.scan.artifacts import write_artifact_index

        decision_source = scan_dir / "decision_records.json"
        if decision_source.exists():
            shutil.copy2(
                decision_source,
                out_base / "trace" / "decision_records.json",
            )
            n_pipe += 1
        stage_source = scan_dir / "stage_results"
        stage_trace = out_base / "trace" / "stage_results"
        if stage_source.is_dir():
            stage_trace.mkdir(parents=True, exist_ok=True)
            for stage_file in sorted(stage_source.glob("*.json")):
                shutil.copy2(stage_file, stage_trace / stage_file.name)
                n_pipe += 1
        outbox_source = scan_dir / "outbox"
        if outbox_source.is_dir():
            outbox_trace = out_base / "trace" / "outbox"
            outbox_trace.mkdir(parents=True, exist_ok=True)
            for outbox_file in sorted(outbox_source.glob("*.json")):
                shutil.copy2(outbox_file, outbox_trace / outbox_file.name)
                n_pipe += 1
        budget_source = scan_dir / "_budget_observation.json"
        if budget_source.exists():
            shutil.copy2(
                budget_source,
                out_base / "trace" / "_budget_observation.json",
            )
            n_pipe += 1
        artifact_index_path = write_artifact_index(scan_dir, report_dir=out_base)
        trace_dir = out_base / "trace"
        trace_dir.mkdir(parents=True, exist_ok=True)
        shutil.copy2(artifact_index_path, trace_dir / "artifact_index.json")
        refreshed_health = scan_dir / "run_health.json"
        if refreshed_health.exists():
            shutil.copy2(refreshed_health, trace_dir / "run_health.json")
        n_pipe += 1
    with contextlib.suppress(Exception):               # 现场导航页(第二天复盘入口)
        (out_base / "index.md").write_text(_health.index_md(scan_dir, out_base), encoding="utf-8")
    # 记账/刷新副作用共享同一条真实现场判据(resolve() 防相对/绝对路径假阴性)——
    # 测试 tmp 目录一律不触发,堵同类测试泄漏口(此前 sector_ledger 无门,曾单独裸奔)。
    if is_real:
        with contextlib.suppress(Exception):           # Phase 4:行业方向记账(sector_ledger,失败不阻发布)
            from autoresearch.learning.sector_ledger import record_calls
            n_calls = record_calls(scan_dir, analysis_date)
            if n_calls:
                print(f"[sector_ledger] 记 {n_calls} 条行业方向 → context/knowledge/sector_calls.jsonl")
        with contextlib.suppress(Exception):       # P7:top3 看多记账(分账,失败不阻发布)
            from autoresearch.learning.sector_ledger import record_top3
            from autoresearch.scan.market import market_pack as _mp3
            inds3 = [r["industry"] for r in (_mp3(scan_dir).get("sector_healthy_top3") or [])]
            n3 = record_top3(analysis_date, inds3)      # date 变量名与相邻 record_calls 调用一致,以现场为准
            if n3:
                print(f"[sector_ledger] 记 top3 看多 {n3} 条(source=deterministic_top3)")
        with contextlib.suppress(Exception):           # 影子买单记账(spec 2026-07-05 wave §A2,失败不阻发布)
            from autoresearch.learning.shadow_buys import record as _shadow_record
            n_sh = _shadow_record(scan_dir)
            if n_sh:
                print(f"[shadow_buys] 记 {n_sh} 只影子买单 → context/learning/shadow_buys.csv")
    print(f"[L5 整合] summary → {summary_path}  (数据日 {analysis_date})")
    print(f"[L5 整合] details → {detail_out}  ({n_cards} 张卡 + trace/ {n_pipe} 件溯源)")
    return summary_path


publish_details = _publish_details
