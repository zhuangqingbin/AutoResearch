"""Scan report/detail/trace publisher and L5 run orchestration."""
from __future__ import annotations

import contextlib
import json
import os
import re
import shutil
from datetime import datetime
from pathlib import Path

from autoresearch.common import workspace as ws
from autoresearch.contracts import artifacts as _contract_artifacts
from autoresearch.scan.l4.parsers import _load_json, _read_csv
from autoresearch.scan.report_model import APPENDIX_FILENAME
from autoresearch.scan.report_sections import _funnel_rows
from autoresearch.scan.run_naming import format_run_dir

# ── 「发布时该搬什么」的两张表(2026-08-29 Task 9c / spec §2.4 A1)────────────────
#
# 两张表原来是两处硬编码的产物名。名字的真身现在只有一个:`contracts.artifacts` 的登记表
# —— 这里只写**登记名**,路径由登记表给;打错字或登记表改名而这里没跟上 = 导入即
# `KeyError`(K1 的病:`finalists.csv` 今天散在 40 个生产文件里,改名要改 40 处)。

#: staging → `<run>/trace/` 的搬运路线:`(登记名, trace 里的目的文件名 | None = 同名)`。
#: 两个**改名**项是发布期的历史命名契约(第二天复盘的人按这两个名字找文件),不是某个登记名
#: 的投影 —— 所以目的名留字面量,不去引别的产物的 path(那样它会跟着别人一起漂)。
_TRACE_ROUTES: tuple[tuple[str, str | None], ...] = (
    ("funnel_meta", "L0_universe_meta.json"),  # meta.json 在 trace 里叫 L0 元数据(历史名)
    ("run_contract", None),          # 运行身份契约(配置/保送/数据策略/hash)
    ("run_health", None),            # 运行体检(NaN 降级/churn/L4 阶段效能)
    ("weights_used", None),          # 重放快照(当日实际权重)
    ("l1_full", None),               # 全量打分(所有过门股 sorted + recalled 标记)
    ("l1_recall", None),             # 召回工作集(top N)
    ("l2", None),                    # 粗排:GBDT 学习重排 top N(确定性)
    ("l3_judged", None),             # 精排全量判断(holistic 通看 ~200,非仅 finalists)
    ("finalists", "L3_fine_finalists.csv"),    # 精排最终入选(top N;发布期旧名)
)

#: 被搬进 trace 的 staging 产物的**登记名**(顺序即拷贝顺序)。
TRACE_SOURCE_NAMES: tuple[str, ...] = tuple(name for name, _ in _TRACE_ROUTES)

#: staging 文件名 → trace 目的文件名(`_publish_pipeline` 逐项拷贝)。
TRACE_MAPPING: dict[str, str] = {
    _contract_artifacts.by_name(name).path:
        (dst or _contract_artifacts.by_name(name).path)
    for name, dst in _TRACE_ROUTES
}

#: assemble 阶段在 `StageResult` 里**认领**的产物 —— 全是登记名(不是随手编的标签):
#: 这串名字会被完整性结论当「这一阶段该有什么」的证据读,拼错一个 = 少认领一件而无人报警。
ASSEMBLE_STAGE_ARTIFACTS: tuple[str, ...] = tuple(
    _contract_artifacts.by_name(name).name for name in (
        "final_ratings", "decision_records", "gate_fires", "run_health",
        "summary", "appendix", "manifest",
    )
)


class ReportBundleError(RuntimeError):
    """发布包不完整:summary / appendix 任一份渲染为空或写入失败。

    这是**发布完整性**故障,不是研究结论故障 —— 抛出后不写成功 StageResult、不落
    manifest、不建 artifact index / MANIFEST,staging 原样留着,只重跑 L5 就能补齐。
    尤其**不回写评级 / BUY**:展示层坏掉不许伪装成研究结论变了。
    """


def _write_report_bundle(out_base: Path, summary_text: object,
                         appendix_text: object) -> tuple[Path, Path]:
    """summary + appendix 作为**一个发布包**落盘(§6.2)。

    两份都非空才动盘:先各写 `.tmp`,再各 `os.replace` —— 第二份写不出来时第一份也还没
    顶替旧文件,不会留下「summary 是新的、appendix 是上一次的」这种半真半假现场。
    「appendix 缺席但 summary 成功」不算发布完成(Q8)。
    """
    pairs = (("summary.md", summary_text), (APPENDIX_FILENAME, appendix_text))
    for name, text in pairs:
        if not isinstance(text, str) or not text.strip():
            raise ReportBundleError(
                f"发布包不完整:{name} 渲染结果为空({type(text).__name__});"
                "已保留 staging,重跑 L5 即可补齐(评级/BUY 未回写)")
    staged: list[tuple[Path, Path]] = []
    try:
        for name, text in pairs:
            target = out_base / name
            tmp = target.with_name(f"{target.name}.tmp")
            tmp.write_text(str(text), encoding="utf-8")
            staged.append((tmp, target))
    except OSError as exc:
        for tmp, _ in staged:
            tmp.unlink(missing_ok=True)
        raise ReportBundleError(f"发布包写入失败:{type(exc).__name__}: {exc}") from exc
    for tmp, target in staged:
        os.replace(tmp, target)
    return out_base / "summary.md", out_base / APPENDIX_FILENAME


def _safe_name(name: str) -> str:
    """股票名称 → 文件名安全(去 / \\ : * ? " < > | 与空白,*ST→ST);空则回退 未命名。"""
    return re.sub(r'[/\\:*?"<>|\s]', "", str(name)).strip() or "未命名"

# 事件表格行:`| YYYY-MM-DD | <时效窗> | ...`。`re.MULTILINE` 必须带 —— 少了它 `^` 只
# 锚定整段文本的开头(位置 0),对多行表格逐行 findall 会静默返回 [],T0/24h 永远数成 0
# (已用真实 DRAFT 夹具验证过这个坑,不是理论顾虑)。
_INTEL_ROW = re.compile(r"^\|\s*\d{4}-\d{2}-\d{2}\s*\|\s*([^|]+?)\s*\|", re.MULTILINE)


def _news_headline(intel_path: Path) -> str:
    """卡头 📰 导航行(Wave9 B-4):情报稿全文只附在卡**尾部**,读者投诉"detail 里看不到
    新闻"其实是没翻到那节 —— 头部先给一句"今天有没有新料"的摘要,指向文末附录。

    T0=收盘后到跑报这段时间的新信息(明天开盘唯一还没被定价的东西);24h 只数
    `时效窗==24h` 的行,不含 T0(两者在事件表里是并列的两档,不叠加)。intel 稿
    不存在(未启用 intel / 硬顶超限被整稿拒 `.rejected.md`)→ 明确说"情报缺席",
    不伪装成"今天没消息"。
    """
    try:
        text = intel_path.read_text(encoding="utf-8")
    except OSError:
        return "📰 情报缺席(未启用或已拒稿)—— 本卡新闻依据见卡内网查段"
    wins = _INTEL_ROW.findall(text)
    t0 = sum(1 for w in wins if w.strip() == "T0")
    h24 = sum(1 for w in wins if w.strip() == "24h")
    head = f"T0 增量:{t0} 条" if t0 else "T0 盘后无增量"
    return f"📰 {head} · 24h {h24} 条 · 详见文末情报附录"


def _dissent_head(record: dict) -> str:
    """结构化分歧记录 → 卡头一行。三个评级字段全取自记录,渲染层不重判(A1)。"""
    from autoresearch.scan.decision_finalize import DissentRecord, dissent_line

    return dissent_line(DissentRecord(
        schema_version=int(record.get("schema_version", 1)),
        code=str(record.get("code", "")),
        lane=str(record.get("lane", "")),
        trigger=str(record.get("trigger", "")),
        card_rating=str(record.get("card_rating", "—")),
        median_rating=str(record.get("median_rating", "—")),
        final_rating=str(record.get("final_rating", "—")),
        ratings=tuple(record.get("ratings") or []),
        spread=int(record.get("spread", 0)),
        degraded=bool(record.get("degraded")),
        kind=str(record.get("kind", "")),
    ))


def _inject_news_headline(body: str, head_line: str) -> str:
    """把 📰 头行插在卡片"标题行"之后。

    标题行定位用**文中第一条 `# ` 开头的行**,不假设它在 line 0 —— ♻️ 复用卡(TTL
    复用已于 W9-B1a 退役,但历史 staging 卡如 601211 仍可能带这层壳)真正的标题前面
    还顶着一段复用横幅 + 分隔线。若标题行下一行是契约版本戳(`〔卡契约 v3…〕`),
    连同跳过再插入 —— 不拆散"标题+契约戳"这一对(现场核验:全部真实卡的契约戳都
    紧跟标题行,插进两者中间既别扭也可能被误读成契约内容的一部分)。

    找不到标题行(异常卡形)→ 原样返回,不猜测插入点,不破坏卡片。
    """
    lines = body.split("\n")
    title_idx = next((i for i, ln in enumerate(lines) if ln.startswith("# ")), None)
    if title_idx is None:
        return body
    insert_idx = title_idx + 1
    if insert_idx < len(lines) and lines[insert_idx].startswith("〔"):
        insert_idx += 1
    lines[insert_idx:insert_idx] = ["", head_line]
    return "\n".join(lines)


def _publish_details(scan_dir: Path, detail_out: Path) -> int:
    """把 L4 staging 决策卡发布到 details/,文件名用**股票名称**(非 ticker);只发当前 finalists。

    staging 卡仍以 <code>.md 暂存(机器侧按 code);发布层改名 <名称>.md 便于人读。
    发布时若有 `_l4_intel_<code>.md`(活体情报盲搜稿)→ 原文附在卡片尾部(fb_20260714_004:
    读者要在 details 里直接看到当日新闻依据,不用去翻 staging)。附录只加在**发布副本**,
    staging 卡不动;parse_rating 两遍法先认卡面 `Rating:` 标签行,intel 中文文本不干扰评级解析。
    """
    src = scan_dir / "details"
    if not src.is_dir():
        return 0
    # A1:decision_finalize 落的结构化分歧事实;缺文件 → {}(presence-gated,老路不破)
    from autoresearch.scan.decision_finalize import load_dissent_records
    dissent_map = load_dissent_records(scan_dir)
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
        # 📰 头行(Wave9 B-4):插在标题行后,T0/24h 增量条数上浮到卡头,指向文末
        # 情报附录 —— 见 _news_headline/_inject_news_headline 顶部注释。
        with contextlib.suppress(Exception):
            card_text = dst.read_text(encoding="utf-8")
            dst.write_text(_inject_news_headline(card_text, _news_headline(intel)),
                            encoding="utf-8")
        # A1:复核分歧行(结构化事实只渲染,不在这里重判)——单向阀吃掉的持仓分歧
        # 此前在卡上零痕迹,读者看不到"三次复核有两次说 Sell"。
        rec = dissent_map.get(code)
        if rec:
            with contextlib.suppress(Exception):
                dst.write_text(
                    _inject_news_headline(dst.read_text(encoding="utf-8"),
                                          _dissent_head(rec)),
                    encoding="utf-8")
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
                # A3:主语分布同屏 —— 「只对了 N 条」与「另外 M 条被判成别人的主语」是两件事,
                # 读者必须能看见分母,否则 0 条不符既可能是真干净、也可能是抽取器瞎了
                if res.get("n_candidate"):
                    line = line.rstrip("\n") + (
                        f"\n_(候选 {res['n_candidate']} = 本票股价 {res['n_own']} + "
                        f"他类主语 {res['n_excluded']} + 主语未定 {res['n_unknown']})_\n")
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
    n = 0
    for src, dst in TRACE_MAPPING.items():
        p = scan_dir / src
        if p.exists():
            shutil.copy2(p, pdir / dst)
            n += 1
    frozen_inputs = scan_dir / "_session_inputs"
    frozen_manifest = frozen_inputs / "manifest.json"
    if frozen_manifest.is_file():
        manifest = _load_json(frozen_manifest)
        wp = frozen_inputs / "L1_weights.json"
        expected = bool((manifest.get("present") or {}).get("L1_weights.json"))
        if expected and not wp.is_file():
            raise RuntimeError("frozen L1 weights declared present but are missing")
    else:
        wp = ws.factor_lab_root() / "weights.json"
        expected = wp.exists()
    if expected:
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
        pinned_path: str | Path | None = None,
        clock: datetime | None = None) -> Path:
    """L5 发布入口。薄壳,真身在 `_run_publish`。

    (2026-08-21 learning 层退役:原来这层壳存在的唯一理由是开一个「单次发布」的
     `buy_ledger.roll()` 缓存窗〔M-10〕—— 账本删了,窗也就没有了。壳保留是为了不动
     所有调用方的入口名。)
    """
    from autoresearch.trace.write_guard import guarded_ambient_write

    with guarded_ambient_write("scan.assemble"):
        return _run_publish(analysis_date, scan_dir=scan_dir, out_root=out_root,
                            hhmm=hhmm, run_date=run_date, pinned_path=pinned_path,
                            clock=clock)


def _run_publish(analysis_date: str, scan_dir: Path | None = None,
                 out_root: Path | None = None, hhmm: str | None = None,
                 run_date: str | None = None,
                 pinned_path: str | Path | None = None,
                 clock: datetime | None = None) -> Path:
    scan_dir = scan_dir or ws.scan_root() / analysis_date
    out_root = out_root or ws.reports_root() / "scan"
    is_real = Path(scan_dir).resolve() == (
        ws.scan_root() / analysis_date
    ).resolve()
    if clock is None:
        from autoresearch.trace.operation_clock import operation_clock

        clock = operation_clock()
    now = clock
    hhmm = hhmm or now.strftime("%H%M")
    # 目录名 = **数据日在前,发布时刻在后**(2026-08-28 用户裁定;真身见 `run_naming`)。
    # 旧格式首段是跑动日,于是 `20260826_2000` 这个名字对人说"08-26"、研究的却是 08-25
    # (61 个 run 里 19 个数据日 ≠ 跑动日)。`run_date`/`hhmm` 仍只供自测注入发布时刻。
    published_at = now
    if run_date:                       # 自测注入:把发布时刻钉死,目录名才可断言
        try:
            published_at = datetime.strptime(
                f"{str(run_date).replace('-', '')}{hhmm}", "%Y%m%d%H%M")
        except ValueError:
            published_at = now
    folder = format_run_dir(analysis_date, published_at)
    out_base = out_root / folder                       # reports/scan/<数据日>-<发布MMDD_HHMM>/
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
    n_pipe = _publish_pipeline(scan_dir, out_base, analysis_date)   # trace/ 挂 out_base(details 同级)
    from autoresearch.scan.artifacts import ARTIFACT_INDEX_SCHEMA_VERSION
    from autoresearch.scan.decision_record import DECISION_RECORD_SCHEMA_VERSION
    from autoresearch.scan.run_contract import load_run_contract

    manifest = {                                             # 按 analysis_date 定位(目录名≠数据日)
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
    # 发布时业务已成功、证据尚未冻结:先写 PENDING,CP7 的 finalize 再改写成终态。
    # 这三个字段让「报告」和「现场」的状态永远各自可见,不互相冒充。
    from autoresearch.scan.evidence import evidence_facts, has_capsule

    # 时间锚(2026-08-28 §2.4 G1):**这份报告什么时候才能真的下单**。
    # 此刻 GATE4 还没跑,所以批准时刻先用发布时刻并如实标 `publish_time`/`estimated`;
    # CP7 的 `post_run observe` 在 GATE4 过之后用实测时刻覆盖它(`gate4_approved`/`measured`)。
    # 写在这里而不是等 CP7:发布路径可能不经 post_run,那时有个诚实的估算好过什么都没有。
    with contextlib.suppress(Exception):
        from autoresearch.scan.exec_anchor import build_execution_block

        manifest["execution"] = build_execution_block(
            analysis_date, approved_at=now, brief_written_at=now,
            ready_source="publish_time", ready_quality="estimated")
    manifest.update({
        "capsule_schema_version": 1,
        "business_status": "SUCCEEDED",
        "evidence_status": (
            evidence_facts(out_base)["evidence_status"]
            if has_capsule(out_base)
            else "PENDING"
        ),
    })
    # ── 发布包(§6.2):一次整形 → 两处纯渲染 → 两份文本都成了才落盘 ────────────────
    # 顺序是冻结的(2026-08-28 定稿),每一步都有它必须在那个位置的理由:
    #   prepare_report_model  唯一一次读盘 + 既有 finalize / 决策落盘副作用
    #   render_summary        纯;含聚合 banner
    #   attach_review         纯;把 review 结果/全文 banner 回填进不可变模型
    #   render_appendix       纯;A 节读 model 里的 banner 全文
    #   finalize_review_artifacts  **不纯**:落 gate_fires.csv —— 那是 GATE4 的判据源。
    #       旧路径里这个副作用藏在 `build_summary` 内部,prepare/render 拆开后**必须显式调**,
    #       漏掉 = GATE4 没有输入(「拆半个特性没人接线」同族)。故它排在写文件之前。
    # 这里**不再调 `build_summary`** —— 那个兼容壳内部会再跑一遍 prepare,等于白算一次
    # 并且制造第二个写者。
    from autoresearch.scan.report_appendix import render_appendix
    from autoresearch.scan.report_sections import (
        attach_review,
        finalize_review_artifacts,
        prepare_report_model,
        render_summary,
    )

    model = prepare_report_model(scan_dir, analysis_date, hhmm, folder,
                                 pinned_path=pinned_path)
    md = render_summary(model)
    model = attach_review(model, md)
    appendix_md = render_appendix(model)
    finalize_review_artifacts(scan_dir, model)
    try:
        # usage_harvest 通常在 GATE4 后才完成：此刻无 JSON 就明确落 UNMEASURED；
        # CP7 随后用 post_run observe 原位替换本 managed section。
        from autoresearch.scan.post_run import (
            publish_run_observation,
            refresh_run_observation,
        )

        # 决策文件(writer-1)读的是 run_health 里的 decision_records.status,而 :273 那份快照拍
        # 于报告整形之前 —— 当日首跑时它报 ABSENT,导致 data_a 团灭、brief 被印成
        # BLOCKED,而事后 `post_run observe` 重算又得到 BUY(2026-08-13 实证:4/8 份 brief 与
        # 决策文件不一致)。这里补拍一次,让 writer-1 读到与 writer-2 同样的事实。
        # 详见 docs/research/2026-08-19-decision-file-two-writers-and-taskbook-hash.md §2
        with contextlib.suppress(Exception):
            _health.write_run_health(scan_dir)

        # P0-2(同文档 §4):`decision_write="write"` 显式声明 —— 这里是 writer-1,brief
        # 渲染前的原子写,现行为不变;显式传参而非依赖默认值,是这道护栏本身要求的
        # 「意图必须写在调用点、不能靠猜」。
        observation = publish_run_observation(
            scan_dir, real_scan=is_real, decision_write="write")
    except Exception as exc:  # noqa: BLE001 — 观测控制面不能阻断报告
        from autoresearch.scan.post_run import (
            observation_after_failure,
            refresh_run_observation,
        )

        observation = observation_after_failure(exc)
    # summary 紧凑一行 + appendix E 完整块,**同一个 observation**(§6.5)。
    md, appendix_md, _obs_warns = refresh_run_observation(md, appendix_md, observation)
    for _warn in _obs_warns:
        print(f"[L5 整合] ⚠️ 运行观测注入:{_warn}")
    # 两份都非空才落盘、才记 SUCCEEDED;任一份失败 → 抛,不产出半真半假的发布包。
    summary_path, appendix_path = _write_report_bundle(out_base, md, appendix_md)
    # manifest 排在双写**之后**:它写着 `business_status: SUCCEEDED`,发布包没成之前
    # 那句话就是假的。发布失败时 staging 原样保留,只重跑 L5 即可补齐(不重跑 L3/L4)。
    (out_base / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False), encoding="utf-8")
    from autoresearch.scan.stage_result import safe_record_stage_result

    safe_record_stage_result(
        scan_dir,
        stage="assemble",
        status="SUCCEEDED",
        artifacts=list(ASSEMBLE_STAGE_ARTIFACTS),
        metrics={"n_cards": n_cards, "n_trace_before_final": n_pipe},
        warnings=[],
        error=None,
        report_dir=out_base,
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
    # ── brief.md(Wave12 T25 批C:报告双层的核心速读层;确定性、零 LLM)──
    # **位置有讲究**,三个前置事实必须已经定稿才落 brief:
    #   ① `_final_ratings.json` / `decision_records.json` / `gate_fires.csv` —— build_summary 内部才写;
    #   ② `_relative_buy_decision.json` —— 上面 `publish_run_observation` 里 `safe_write_decision` 才写;
    #   ③ `run_health.json` 的 counts/churn —— 紧邻上一行刚刷成终值。
    # 放在 assemble 早段 = 读到半成品(FN-1 家族:探针读还没生成的产物)。
    # 失败不阻断发布(summary 仍是完整产物);缺 brief 由 self_review 的 brief lint 报 fail。
    # 同一份成品两处用:落 brief.md + 把 ①②③④ 注回 summary 的 🧭 managed 块(T26)——
    # 各渲染一次等于给「两边不一致」开口子,而 T27 正要 lint 这件事。
    from autoresearch.scan.brief import safe_publish as _publish_brief

    _publish_brief(scan_dir, out_base, summary_path,
                   analysis_date=analysis_date, run_folder=folder)
    # ── brief 一致性 lint(Wave12 T27)——**必须在 brief 落盘之后**跑,而 `_self_review_banner`
    # 跑在 build_summary 内部(那时 brief 与决策文档都还不存在),所以这条独立接在这里。
    # 结果追加进 `gate_fires.csv`(与 R3 门审计同一本账),并打一行给 CP7 播报。
    #
    # ⚠️ **这条追加会喂进 GATE4**(`gates.gate4` = gate_fires 里有任意 `severity=="fail"`
    # 就不过),所以 severity 的选择就是「要不要毙掉这一整趟约 60 分钟的扫描」。B-2
    # (2026-08-09 控制方裁定)据此把九条判据二分(E4 08-18 补第九条同属 fail),单一事实源在
    # `self_review.BRIEF_LINT_SEVERITY`:报告**说假话**才 fail(硬门该拦),报告**畸形或
    # 缺失**只 warn(排版超限/没落盘是展示层问题,不该毁掉一次已跑完的扫描)。这也才与
    # 上面「失败不阻断发布」和 `brief.safe_publish` 刻意吞异常的口径自洽 —— 否则一边为了
    # 不阻断而吞,另一边把吞下去的结果变成门失败(「GATE3 差 16 字节毙 60min 流水线」同族)。
    # 播报走 `brief_lint_banner`:fail 与 warn **都播**,降级不等于消音。
    with contextlib.suppress(Exception):
        from autoresearch.scan.self_review import (
            append_gate_fires,
            brief_lint,
            brief_lint_banner,
        )
        _lint = brief_lint(out_base, scan_dir)
        append_gate_fires(scan_dir, _lint, analysis_date)
        print(brief_lint_banner(_lint))
    # ── 版式预算(§6.10)——**所有 managed 注入完成后**才计量最终文件 ────────────────
    # 位置紧跟 brief 回填(仪表盘/组合/overlay 是发布期最后一次注入)。这是展示层读数:
    # 只 warn + 进 run_health,**不截断、不改评级、不毙 GATE4**。真正的验收读数在
    # `post_run observe` 刷完终值那次(此刻 token 计量通常还没到),两处用同一个函数。
    with contextlib.suppress(Exception):
        _health.measure_report_budget(scan_dir, out_base)
        _health.write_run_health(scan_dir)     # 让预算读数进入落盘的那份体检
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
    # 现场导航页(第二天复盘入口)。**位置有讲究**(Wave12-T28):必须排在上面的
    # `_publish_brief` 之后 —— `index_md` 的首行「读我」按 `brief.md` 是否在盘上分两种写法,
    # 提前跑会永远写成「未生成」(FN-1 家族:探针读还没生成的产物)。
    with contextlib.suppress(Exception):
        (out_base / "index.md").write_text(_health.index_md(scan_dir, out_base), encoding="utf-8")
    # ── 现场留存(2026-08-26 设计稿 §4;R1 镜像 + R2 run 外输入 + R5 清单)────────────
    # **必须是本函数的最后一步**:上面每一段都还在往 staging 写(build_summary 落
    # `_final_ratings`/`decision_records`/`gate_fires`、publish_run_observation 落
    # `_relative_buy_decision`、brief 落 `_brief_sources`、health 刷 `run_health`)。
    # 早一行跑 = 镜像到半成品,而那正是本波要修的病(同族:「brief 读了 relative_buy 的
    # 半成品」)。CP7 的 `post_run observe` 会再跑一次(那时 `_token_usage.json` 才有),
    # 两次都幂等。`retain` 自己吞异常并分类留痕 —— 留存是加法,不该有能力毁掉一次扫描。
    from autoresearch.scan.retention import retain
    _ret = retain(scan_dir, out_base)
    print(f"[L5 整合] 现场 → trace/staging {_ret['mirrored']} 件 · "
          f"inputs {_ret['inputs']} · MANIFEST {'✓' if _ret['manifest'] else '✗'}"
          + (f" · ⚠️ {'; '.join(_ret['errors'])}" if _ret["errors"] else ""))
    print(f"[L5 整合] summary → {summary_path}  (数据日 {analysis_date})")
    print(f"[L5 整合] appendix → {appendix_path}  (现场 / 口径 / 遥测)")
    print(f"[L5 整合] details → {detail_out}  ({n_cards} 张卡 + trace/ {n_pipe} 件溯源)")
    return summary_path


publish_details = _publish_details
