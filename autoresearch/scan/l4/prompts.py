"""L4 shared-instruction and dispatch-prompt rendering."""
from __future__ import annotations

import contextlib
import json
import re
from pathlib import Path

import pandas as pd

from autoresearch.scan.l4.context import (
    _dossier_summary_mark,
    _target_calib_mark,
    compose_funnel_brief,
    write_base_rates,
)
from autoresearch.scan.l4.rubric import force_full_card


def write_shared_instructions(scan_dir: Path | str) -> int:
    """落 `_l4_shared_instructions.md`(当日共享块,逐卡 byte-identical)。返回写入字节数。

    Wave5 ④B:该文件此前**全仓无生产者**(只有读者 + 测试写者),07-17/07-21 实测均不存在
    —— prelude 每天算出的 📐/🔁/🚪 当日校准行从未到达任何一张决策卡。这里把 STAGES.md:215
    描述的手工步骤变成确定性生产。

    纪律:prelude 建议行里**含「禁注」的行不贴**(样本不足的自我标注,贴进 prompt = 用坏
    先验污染判断);两个来源任一异常都不阻断(写出只含标头的稳定文件,好过没有文件)。
    """
    import contextlib

    scan_dir = Path(scan_dir)
    scan_dir.mkdir(parents=True, exist_ok=True)
    lines = ["## 当日共享块(全卡一致;确定性生成,勿逐卡改写)"]
    calib: list[str] = []
    with contextlib.suppress(Exception):
        from autoresearch.scan.prelude import calib_suggestion_lines
        calib = [ln for ln in calib_suggestion_lines() if "禁注" not in ln]
    if calib:
        lines += ["", "### 当日校准锚(据实调用,不作评级指令)"] + [f"- {ln}" for ln in calib]
    t1_blk = ""
    with contextlib.suppress(Exception):
        from autoresearch.learning.t1_review import render_t1_calibration_block
        t1_blk = render_t1_calibration_block(stage="L4")
    if t1_blk:
        lines += ["", t1_blk.strip()]
    text = "\n".join(lines).strip() + "\n"
    p = scan_dir / "_l4_shared_instructions.md"
    p.write_text(text, encoding="utf-8")
    return len(text.encode("utf-8"))


_ECHO_RATING = re.compile(r"^\*\*Rating\*\*:\s*(.+)$", re.M)
_ECHO_LS = re.compile(r"^\*\*一行多空\*\*:\s*(.+)$", re.M)
_ECHO_WIRE = re.compile(r"^-?\s*\[价格线\][^\n]*$", re.M)


def yesterday_echo(code6: str, name: str, analysis_date: str, *,
                   lookback_days: int = 5, reports_root="reports/scan") -> str:
    """昨卡回声(Wave9 B-1b):最近 ≤N 日已发布卡的 3 行摘要,注入任务包逐票段。

    R5 退役 TTL 复用后,"评级稳定性"不再靠**跳过研究**获得,而靠**记忆**:研究员知道
    昨天怎么判,今天写增量。**防锚定**:回声是历史判断,不是今日默认值 —— 翻覆合法,
    但必须写明触发翻覆的增量证据。

    读**已发布**报告(`reports/<run>/manifest.json` 的 `analysis_date` + `details/<名称>.md`,
    发布层用股票名称做文件名,同名冲突时 `<名称>_<code>.md`),不是当日 staging
    `context/scan/`——昨天的判断只有发布过才算数。`lookback_days` 数的是**自然日**不是
    交易日:长假后(如国庆/春节)窗口内可能没有任何已发布交易日,此时静默回退空串
    (总比拿一个跨越长假、语境已过期的旧判断当"昨天"强)。
    """
    from datetime import datetime, timedelta
    root = Path(reports_root)
    if not root.is_dir():
        return ""
    try:
        cut = datetime.strptime(analysis_date, "%Y-%m-%d") - timedelta(days=lookback_days)
    except ValueError:
        return ""

    best: tuple[str, str] | None = None      # (data_date, card_text)
    for run in sorted(root.iterdir(), reverse=True):
        if not run.is_dir():
            continue
        try:
            dd = str(json.loads((run / "manifest.json").read_text(
                encoding="utf-8")).get("analysis_date", ""))
            when = datetime.strptime(dd, "%Y-%m-%d")
        except Exception:  # noqa: BLE001
            continue
        if when < cut or dd >= analysis_date:
            continue
        card = run / "details" / f"{name}.md"
        if not card.exists():
            card = run / "details" / f"{name}_{code6}.md"
        if not card.exists():
            continue
        with contextlib.suppress(OSError):
            text = card.read_text(encoding="utf-8")
            if best is None or dd > best[0]:
                best = (dd, text)
    if best is None:
        return ""

    dd, text = best
    rating = (_ECHO_RATING.search(text) or [None, "—"])[1].strip()
    ls = (_ECHO_LS.search(text) or [None, "—"])[1].strip()
    wires = _ECHO_WIRE.findall(text)[:2]
    lines = [f"## 昨卡回声(最近一次已发布判断 @ {dd})",
             f"- 评级:**{rating}**",
             f"- 一行多空:{ls}"]
    if wires:
        lines.append(f"- 盯梢线:{' ｜ '.join(w.strip('- ').strip() for w in wires)}")
    lines.append("> 历史判断**非今日默认值**;若今日翻覆,必须在卡里写明触发翻覆的"
                 "**增量证据**(新数字/新事件),不得只换措辞。")
    return "\n".join(lines) + "\n"


def write_dispatch_pack(
    scan_dir: Path | str,
    *,
    stable_context: bool = False,
) -> dict:
    """L4 派发包确定性落稿(零 LLM):`_harvest_list.txt`(yfinance 归一后缀,`.SH` 绝迹)
    + 每卡 `_l4_prompt_<code>.md`(共享指令 + 漏斗简报 + slim/卡路径指针)。

    Wave9 R5(TTL 复用退役,`l4/dispatch.py` 的 `dispatch_plan` 已改无条件把全部 finalists
    排进 `dispatch`)后,**本函数不再因 `details/<code>.md` 已存在而跳过写 prompt**——旧的
    "♻️ 已有卡跳过不重拉不派发"规则若继续存在,会与 dispatch_plan(不再检查文件存在性)
    + `l4_tasks.initialize()`(未追踪码一律标 PENDING,不查磁盘)组合出真实故障:SKILL.md
    背书的"单步重跑入口"场景下(`details/` 有残留卡、当日 `_l4_tasks.json` 是新建的),
    某个已有卡的码被标 PENDING,但它的 prompt 因跳过从未写出,`l4-stock.js` 指示 Opus 去
    读一个不存在的 `_l4_prompt_<code>.md` 必炸(Task5→Task6 转固定 Important 缺陷)。评级
    稳定性改由**昨卡回声**(`yesterday_echo`,读已发布报告的最近判断,注入每票 prompt 的
    差异段)承接,不再靠跳过研究省成本。落稿契约从人肉变确定性:
    ① token 表输入侧从此可计(assemble 估算器认 `_l4_prompt_*`);② 编排以 prompt 稿为
    派发正文(共享块在前 = prompt cache 前缀命中);③ 07-03 `.SH` 空 slim 双跑从清单源头消灭。

    pinned 票(finalists 行 `lane == "pinned"`,design 2026-07-11 §4.1;plan Task 4):
    逐卡块(共享前缀**之后**,不碰 cache 契约)插一行 📌 标记 + note,让 L4 subagent 知道这是
    用户手工直通票、仍须真判不可走过场。R5 后 pinned 票与普通票同规则同走全量派发,**全部**
    进 `pinned` 名单(不再有"已有卡跳过、不计入本次名单"的例外)。返回
    {n_prompts, tickers, pinned}(pinned = 本次派发的全部 pinned 码)。
    """
    scan_dir = Path(scan_dir)
    date = scan_dir.name
    fp = scan_dir / "finalists.csv"
    if not fp.exists():
        return {"n_prompts": 0, "tickers": [], "pinned": []}
    from autoresearch.dataflows.symbol_utils import normalize_symbol  # lazy,保持模块轻量
    fin = pd.read_csv(fp, dtype={"code": str})
    import contextlib
    # FN-1 第四修:🔁 基率 json 此前无生产调用点(真实跑动恒空)——派发前日级落稿,幂等;
    # 失败不挡派发(消费方 _base_rate_mark presence-gated,缺文件即无此行)。
    with contextlib.suppress(Exception):
        write_base_rates(scan_dir)
    with contextlib.suppress(Exception):   # 终审 I-3:📐 锚随派发日刷新(与基率同节奏),不冻结在首算日分布
        from autoresearch.learning.buy_ledger import write_target_calib
        write_target_calib()
    shared = ""
    sp = scan_dir / "_l4_shared_instructions.md"
    if sp.exists():
        shared = sp.read_text(encoding="utf-8").strip()
    # (Wave5 ④B)t1 校准块已并入 `write_shared_instructions` 写的文件 —— 共享块的唯一事实源
    # 就是那个文件,消费侧不再二次拼接(否则同一段会在每张卡里出现两遍)。
    calib_line = _target_calib_mark(scan_dir)        # 📐 目标价基率锚(日级,算一次逐卡复用)

    # FN-1 第五修:`force_full_card`(早停安全网)自 2026-06-27 建成起**零生产调用点** ——
    # 高 conviction+多路共振的真龙头照样被表面 P1-P3 早停砍掉。这里接进真派发链。
    # n_channels / l2_lane_reserved 只在 L2 表里(finalists.csv 没这两列)→ 一次读入建索引。
    l2_priors: dict[str, dict] = {}
    l2p = scan_dir / "L2_gbdt_top200.csv"
    if l2p.exists():
        _l2 = pd.read_csv(l2p, dtype={"code": str})
        if "code" in _l2.columns:
            _l2["code"] = _l2["code"].astype(str).str.zfill(6)
            l2_priors = _l2.set_index("code").to_dict("index")

    tickers: list[str] = []
    pinned: list[str] = []
    with_dossier: set[str] = set()   # Wave9 B-3:本次派发里"哪些票有档案可注入"(lint 探针 10 读)
    n_prompts = 0
    prompt_manifest = {
        "schema_version": 1,
        "mode": "stable_context" if stable_context else "legacy",
        "prompts": {},
    }
    market_pack_data: dict = {}
    common_market = ""
    common_market_written = None
    if stable_context:
        from autoresearch.scan.context_blocks import write_context_block
        from autoresearch.scan.market import market_context_parts, market_pack

        try:
            market_pack_data = market_pack(scan_dir)
            if market_pack_data.get("regime"):
                common_market = market_context_parts(market_pack_data)[0]
        except Exception:  # noqa: BLE001 — 稳定块可选,与旧 _market_ctx 同降级语义
            market_pack_data = {}
            common_market = ""
        market_sources = [
            p for p in (
                scan_dir / "market_pack.json",
                scan_dir / "L1_scored_full.csv",
                scan_dir / "sectors.csv",
            ) if p.is_file()
        ]
        common_market_written = write_context_block(
            scan_dir,
            kind="market",
            scope="all",
            content=common_market,
            source_paths=market_sources,
        )
    for _, r in fin.iterrows():
        raw = str(r.get("code", "") or "").strip()
        if not raw or raw == "nan":
            continue
        code6 = raw.split(".")[0].zfill(6)
        # Wave9 B-3:档案存在性判据独立于 stable_context 分支(生产默认走 legacy,若只在
        # stable_context 分支里判会让 _dossier_present.json 在默认配置下恒空)。
        with contextlib.suppress(Exception):
            from autoresearch.dossier.schema import dossier_path

            if dossier_path(code6).is_file():
                with_dossier.add(code6)
        # Wave9 R5(TTL 复用退役)后不再因 `details/<code>.md` 已存在而跳过写 prompt——
        # dispatch_plan 已无条件全票派发,"卡已存在"不再是跳过写 prompt 的正当理由
        # (旧跳过 + dispatch_plan 新语义组合会炸出 PENDING-但-prompt-不存在,详见函数 docstring)。
        ticker = normalize_symbol(code6)            # 6 位码 → .SS/.SZ/.BJ(单一后缀口径)
        tickers.append(ticker)
        is_pinned = str(r.get("lane", "") or "").strip() == "pinned"
        body = [f"## L4 派发 — {code6} {r.get('name', '')}", ""]
        if is_pinned:                                # 逐卡块内标记(共享前缀之后,不破 cache 契约)
            pinned.append(code6)
            note = str(r.get("pinned_note", "") or "").strip()
            body += ["**📌 保送票**(用户手工直通;已在 L1→L3 全程强留,不受漏斗取舍影响"
                    + (f":{note}" if note else "") +
                    ")——仍须按下方真实证据独立评判,不因『保送』降低尽调标准。", "",
                    "**📌持仓管理要求**:本票为用户保送票(可能已持有)——满卡/早停卡都必须含"
                    "『持仓管理』小节:D+1/D+2 卖出纪律(何价减/何价清)+加减仓触发位;若 "
                    "pinned_note 含成本信息按其计算浮盈亏,无则按现价基准写纪律。", ""]
        # 强制满卡(逐卡块内,共享前缀之后 → 不破 cache 契约):priors = finalists 行(conviction/
        # lane)+ L2 行(n_channels/l2_lane_reserved)。
        priors = {**l2_priors.get(code6, {}),
                  "conviction": r.get("conviction"),
                  "lane": r.get("lane")}
        if force_full_card(priors):
            why = ("📌 保送持仓票" if is_pinned else
                   f"强先验(conviction {r.get('conviction')} + 多路共振/配额救回)")
            body += [f"**⛔ 强制满卡 — {why}:禁止早停。** 必须跑完 P4(陷阱核)+ P5(满卡):"
                     "「盈利质量」与「偿付(爆雷)」两维**不得**标『未核』,必须 Read "
                     "`_slim_deep.md` 取证后给分。评级仍由 rubric 三门定——强制满卡只保证"
                     "**核得够深**,不保证结论向好(照样可以是 Underweight/Sell)。", ""]
        legacy_brief = compose_funnel_brief(code6, scan_dir)
        stable_blocks = None
        if stable_context:
            from autoresearch.scan.context_blocks import (
                manifest_ref,
                write_context_block,
            )
            from autoresearch.scan.market import market_context_parts

            industry = r.get("industry") or r.get("sector")
            if pd.isna(industry):
                industry = ""
            industry = str(industry or "")
            market_sector = ""
            if market_pack_data.get("regime"):
                market_sector = market_context_parts(
                    market_pack_data, industry=industry
                )[1]

            dossier_parts: list[str] = []
            dsum = _dossier_summary_mark(code6)
            if dsum:
                dossier_parts.append(dsum)
            try:
                from autoresearch.scan.dossier import render_dossier

                history = render_dossier(
                    code6, scan_root=scan_dir.parent, exclude=scan_dir.name
                )
            except Exception:  # noqa: BLE001
                history = ""
            if history:
                dossier_parts.append(history)
            # Wave9 B-3:研报体的素材侧 —— 摘要 600B 撑不起研报体,内联四节全文
            # (业务模型/盈利驱动/估值带/风险矩阵;复核 2026-07-30 实测 31 份真实档案
            # 该四节合计 6.8–15.1KB/票、中位 ≈13.0KB,相对 170KB slim 可忽略;
            # `dossier_sections` 自带 `RESEARCH_BODY_CAP` token 硬帽,超限截断非静默丢);
            # 选内联而非让 agent 自己 Read,读盘边界与工具调用方差都不动。
            with contextlib.suppress(Exception):
                from autoresearch.dossier.schema import dossier_sections

                secs = dossier_sections(code6, keys=("§1", "§2", "§3", "§5"))
                if secs:
                    dossier_parts.append("### 档案节选(研报体素材)\n" + secs)
            dossier_content = "\n".join(dossier_parts)

            terrain = ""
            try:
                from autoresearch.sector.brief import render_terrain_block

                terrain = render_terrain_block(industry, scan_dir)
            except Exception:  # noqa: BLE001
                terrain = ""
            sector_content = "\n".join(
                part.rstrip() for part in (market_sector, terrain) if part
            )
            if sector_content:
                sector_content += "\n"

            # 从 legacy 全量简报中精确摘掉已提升为 stable block 的前导/独立块；
            # 余下就是逐股 differential。旧模式完全不走此分支，字节契约不变。
            differential = legacy_brief
            full_market = common_market + market_sector
            if full_market and differential.startswith(full_market):
                differential = differential[len(full_market):].lstrip("\n")
            if dossier_content and differential.startswith(dossier_content):
                differential = differential[len(dossier_content):].lstrip("\n")
            if terrain and terrain in differential:
                differential = differential.replace(terrain, "", 1)
            differential = differential.strip() + "\n"

            sector_sources = [
                p for p in (
                    scan_dir / "sectors.csv",
                    scan_dir / "sector_briefs" / f"{industry}.md",
                    Path("context/sector") / date / f"{industry}.json",
                ) if p.is_file()
            ]
            dossier_sources: list[Path] = []
            try:
                from autoresearch.dossier.schema import dossier_path

                dp = dossier_path(code6)
                if dp.is_file():
                    dossier_sources.append(dp)
            except Exception:  # noqa: BLE001
                pass
            differential_sources = [
                p for p in (
                    fp,
                    scan_dir / "L1_recall_top1000.csv",
                    scan_dir / "L2_gbdt_top200.csv",
                ) if p.is_file()
            ]
            sector_written = write_context_block(
                scan_dir,
                kind="sector",
                scope=industry or "unknown",
                content=sector_content,
                source_paths=sector_sources,
            )
            dossier_written = write_context_block(
                scan_dir,
                kind="dossier",
                scope=code6,
                content=dossier_content,
                source_paths=dossier_sources,
            )
            differential_written = write_context_block(
                scan_dir,
                kind="differential",
                scope=code6,
                content=differential,
                source_paths=differential_sources,
            )
            stable_blocks = {
                "market": manifest_ref(common_market_written),
                "sector": manifest_ref(sector_written),
                "dossier": manifest_ref(dossier_written),
                "differential": manifest_ref(differential_written),
            }
            body.extend([
                sector_content.rstrip(),
                dossier_content.rstrip(),
                differential.rstrip(),
            ])
        else:
            body.append(legacy_brief.rstrip())
        # 昨卡回声(W9-B1b,逐卡块内、紧邻 dossier_content/差异段之后,共享前缀之后不破 cache
        # 契约):读已发布报告的最近一次判断,不并入上面已落盘的 differential context block
        # (该块的 source_paths 不含 reports/ 历史卡,回声混进去会让 hash 契约与实际来源脱节)。
        echo = yesterday_echo(code6, str(r.get("name", "") or ""), date)
        if echo:
            body.append(echo.rstrip())
        if calib_line:                               # 逐卡块内(共享前缀之后,不破 cache 契约)
            body += ["", calib_line]
        prompt_parts = [
            # 固定标头(逐卡不变,≤300B)——cache 前缀契约(T8):共享块前不得出现逐卡可变内容,
            # 否则 30 卡并发前缀全断、cache 全 miss。逐卡专属标题(含 📌 保送标记)移到共享块**之后**。
            "# L4 派发 prompt(确定性落稿;编排以此为派发正文;先读共享块再读下方逐卡简报)",
            "",
            shared or "_(共享指令稿缺:`_l4_shared_instructions.md` 未落——按 stock-research lite-playbook 执行)_",
            "",
        ]
        if stable_context and common_market:
            prompt_parts += [common_market.rstrip(), ""]
        prompt_parts += [
            "---",
            "",
            *body,
            "",
            "---",
            f"- slim 数据:`context/{ticker}_{date}_slim.md`(P1–P3 表面块;**>8KB 才可信**,≈4.8KB=NO_DATA 须重拉)",
            f"- deep 深核:`context/{ticker}_{date}_slim_deep.md`(**survivor 进 P4 才 Read**;早停卡不读;缺文件=陷阱维标「未核」)",
            f"- 活体情报:`context/scan/{date}/_l4_intel_{code6}.md`(若存在:P3 先读它作催化/题材/机构主料、"
            f"自发网查降 ≤1 条验证;缺文件=回退卡内网查,cap 原规则)",
            f"- 决策卡写往:`context/scan/{date}/details/{code6}.md`",
            ""]
        prompt = "\n".join(prompt_parts)
        (scan_dir / f"_l4_prompt_{code6}.md").write_text(prompt, encoding="utf-8")
        if stable_blocks is not None:
            prompt_manifest["prompts"][code6] = {
                "path": str(scan_dir / f"_l4_prompt_{code6}.md"),
                "blocks": stable_blocks,
            }
        n_prompts += 1
    (scan_dir / "_harvest_list.txt").write_text(
        "\n".join(tickers) + ("\n" if tickers else ""), encoding="utf-8")
    with contextlib.suppress(Exception):
        (scan_dir / "_dossier_present.json").write_text(
            json.dumps(sorted(with_dossier), ensure_ascii=False), encoding="utf-8")
    if stable_context:
        # `json` 是模块顶部 `import json`(line 5)——此处不得再 `import json`(哪怕只在这个
        # if 分支里):Python 一旦在函数体任意处见到 `import json`/赋值,就把 `json` 判定为
        # **整个函数**的局部名,连带炸穿上面那句更早执行的 `json.dumps(...)`(复核 2026-07-30
        # Important 1 的生产侧集成测试实测揪出:`_dossier_present.json` 在默认
        # `stable_context=False` 路径下 100% 抛 UnboundLocalError,又被下面缺失的
        # `contextlib.suppress` 静默吞掉,2214 条回归里没有一条真正调用过
        # `write_dispatch_pack` 再读文件内容,故此前从未被发现)。
        target = scan_dir / "_l4_prompt_manifest.json"
        temp = target.with_name(f"{target.name}.tmp")
        temp.write_text(
            json.dumps(prompt_manifest, ensure_ascii=False, sort_keys=True, indent=2)
            + "\n",
            encoding="utf-8",
        )
        temp.replace(target)
    return {
        "n_prompts": n_prompts,
        "tickers": tickers,
        "pinned": pinned,
        "context_mode": prompt_manifest["mode"],
    }
