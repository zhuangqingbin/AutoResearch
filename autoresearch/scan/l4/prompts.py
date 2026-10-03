"""L4 shared-instruction and dispatch-prompt rendering."""

from __future__ import annotations

import contextlib
import json
import re
from pathlib import Path

import pandas as pd

from autoresearch.agents.utils.rating import RATINGS_5_TIER
from autoresearch.common import workspace as ws
from autoresearch.scan.l4.context import compose_funnel_brief
from autoresearch.scan.l4.rubric import force_full_card

_WS_REPORTS_SCAN = (
    ws.reports_root() / "scan"
)  # B008 修法:默认值须为模块级单例(def 时求值,与旧字面量常量同语义)


def write_shared_instructions(scan_dir: Path | str) -> int:
    """落 `_l4_shared_instructions.md`(当日共享块,逐卡 byte-identical)。返回写入字节数。

    2026-08-21(用户裁定「整个 learning 层退役」)本文件退成**只有标头的稳定骨架**:原来
    往里塞的两样东西都是闭环回注 —— ① prelude 的 📐/🔁/🚪 当日校准锚(buy_ledger 触价校准 /
    cross_calib 翻案率 / 门柱),② T+1 快环校准块 —— 已随账本一并退役。骨架保留是**故意的**:
    消费侧(`build_l4_prompts`)按"文件在就读"接线,留一个 byte 稳定的空骨架比让每张卡的
    prompt 前缀随文件有无而变更安全(cache 前缀契约)。日后若有新的全卡共享块,往这里加。
    """
    scan_dir = Path(scan_dir)
    scan_dir.mkdir(parents=True, exist_ok=True)
    lines = ["## 当日共享块(全卡一致;确定性生成,勿逐卡改写)"]
    text = "\n".join(lines).strip() + "\n"
    p = scan_dir / "_l4_shared_instructions.md"
    p.write_text(text, encoding="utf-8")
    return len(text.encode("utf-8"))


_ECHO_RATING = re.compile(r"^\*\*Rating\*\*:\s*(.+)$", re.M)
# 回声只取五档词:历史已发布卡里有 141 张把模板提示抄进了 Rating 行
# (`Underweight ← 必须 = Rubric建议(一致,无偏离)`),整行照搬会把「必须」这类模板残留
# 注入次日任务包,诱使卡 agent 去翻源码核规则(2026-09-14 夜 688981 实况)。
_ECHO_TIER = re.compile(r"\b(" + "|".join(RATINGS_5_TIER) + r")\b")
_ECHO_LS = re.compile(r"^\*\*一行多空\*\*:\s*(.+)$", re.M)
_ECHO_WIRE = re.compile(r"^-?\s*\[价格线\][^\n]*$", re.M)


def configured_echo_lookback() -> int:
    """`scan_config.l4.brief.echo_lookback_days`:昨卡回声回看自然日(缺省 5)。"""
    from autoresearch.scan.user_config import knob
    return int((knob("l4", "brief", None, {}) or {}).get("echo_lookback_days", 5))


def slim_hint() -> str:
    """任务包里 slim 可信地板那句话,阈值来自 `l4.slim.min_bytes`(与 producers / l4_tasks 同源)。"""
    from autoresearch.scan.l4.producers import slim_min_bytes
    kb = slim_min_bytes() / 1024
    return f"**≥{kb:g}KB 才可信**,更小 = NO_DATA 须重拉"


def params_block() -> str:
    """任务包「本次参数」块:与代码同源的数字一次性告诉 l4-card(评分卡档位 / 强制满卡 / 引用行数 / 执行线 / slim 地板 / intel 软顶)。

    agent 定义文件里的对应句子只写缺省并注明「以任务包为准」,值只住 scan_config(2026-09-27 Q5)。
    """
    from autoresearch.contracts.agent_output import exec_line_thresholds
    from autoresearch.scan.l4.intel_guard import configured_soft_cap
    from autoresearch.scan.l4.rubric import rubric_cfg
    from autoresearch.scan.self_review import self_review_cfg
    rc = rubric_cfg()
    bands, ff = rc["rating_bands"], rc["force_full"]
    pct_max, pos_max = exec_line_thresholds()
    return "\n".join([
        "## 本次参数(来自 scan_config,与机检代码同源;定义文件里的缺省句以此为准)",
        f"- 评分卡档位:Buy≥{bands['Buy']:+g} / OW≥{bands['Overweight']:+g} / Hold≥{bands['Hold']:+g} / UW≥{bands['Underweight']:+g},其余 Sell".replace("≥+", "≥"),
        f"- 满卡强制线:conviction≥{ff['conviction_min']:g} ∧ 通道≥{ff['channels_min']}(保送票恒满卡)",
        f"- 满卡带日期引用 ≥{int(self_review_cfg()['citation_min'])} 行",
        f"- 执行线(照抄两行):`[执行线] pct_chg <= {pct_max:g} → 当日涨超 {pct_max:g}% 放弃本次尾盘入场`;"
        f"`[执行线] pos_in_range < {pos_max:g} → 收盘在当日区间上 {100 - pos_max * 100:g}% 放弃入场`",
        f"- slim 可信地板:{slim_hint()}",
        f"- intel 网查软顶 {configured_soft_cap()} 条",
    ])


def yesterday_echo(
    code6: str,
    name: str,
    analysis_date: str,
    *,
    lookback_days: int | None = None,
    reports_root=_WS_REPORTS_SCAN,
) -> str:
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
    lookback_days = configured_echo_lookback() if lookback_days is None else lookback_days
    from datetime import datetime, timedelta

    root = Path(reports_root)
    if not root.is_dir():
        return ""
    try:
        cut = datetime.strptime(analysis_date, "%Y-%m-%d") - timedelta(days=lookback_days)
    except ValueError:
        return ""

    best: tuple[str, str] | None = None  # (data_date, card_text)
    for run in sorted(root.iterdir(), reverse=True):
        if not run.is_dir():
            continue
        try:
            dd = str(
                json.loads((run / "manifest.json").read_text(encoding="utf-8")).get(
                    "analysis_date", ""
                )
            )
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
    rating_line = _ECHO_RATING.search(text)
    tier = _ECHO_TIER.search(rating_line.group(1)) if rating_line else None
    rating = tier.group(1) if tier else "—"
    ls = (_ECHO_LS.search(text) or [None, "—"])[1].strip()
    wires = _ECHO_WIRE.findall(text)[:2]
    lines = [f"## 昨卡回声(最近一次已发布判断 @ {dd})", f"- 评级:**{rating}**", f"- 一行多空:{ls}"]
    if wires:
        lines.append(f"- 盯梢线:{' ｜ '.join(w.strip('- ').strip() for w in wires)}")
    lines.append(
        "> 历史判断**非今日默认值**;若今日翻覆,必须在卡里写明触发翻覆的"
        "**增量证据**(新数字/新事件),不得只换措辞。"
    )
    return "\n".join(lines) + "\n"


DOSSIER_SNAPSHOT_DIR = "_dossier_snapshot"
DOSSIER_SNAPSHOT_INDEX = "_dossier_snapshot.json"


def _snapshot_dossiers(scan_dir: Path, codes: set[str]) -> dict:
    """把本次派发会读到的档案原文抄进 `<scan_dir>/_dossier_snapshot/<code>.md` + 索引 hash。

    见调用点注释:档案在 assemble 尾被 δ 原地改写,只有**落稿这一刻**抄的才是 agent 真读的
    那一版。索引记 sha256 与字节数 —— 事后可以直接回答「今天注入的档案跟上周是不是同一份」。
    """
    import hashlib

    from autoresearch.dossier.schema import dossier_path, read_dossier_text

    scan_dir = Path(scan_dir)
    out_dir = scan_dir / DOSSIER_SNAPSHOT_DIR
    index: dict[str, dict] = {}
    for code6 in sorted(codes):
        src = dossier_path(code6)
        text = read_dossier_text(code6)
        if text is None:
            continue
        raw = text.encode("utf-8")
        out_dir.mkdir(parents=True, exist_ok=True)
        (out_dir / f"{code6}.md").write_bytes(raw)
        index[code6] = {
            "sha256": hashlib.sha256(raw).hexdigest(),
            "bytes": len(raw),
            "source": str(src),
        }
    doc = {"schema_version": 1, "captured_at_stage": "l4_prompts", "dossiers": index}
    (scan_dir / DOSSIER_SNAPSHOT_INDEX).write_text(
        json.dumps(doc, ensure_ascii=False, sort_keys=True, indent=1), encoding="utf-8"
    )
    return doc


def write_dispatch_pack(scan_dir: Path | str) -> dict:
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
    input_dir = ws.scan_input_dir(date, scan_dir=scan_dir)
    resolved_scan_dir = scan_dir
    fp = scan_dir / "finalists.csv"
    if not fp.exists():
        return {"n_prompts": 0, "tickers": [], "pinned": []}
    from autoresearch.dataflows.symbol_utils import normalize_symbol  # lazy,保持模块轻量

    fin = pd.read_csv(fp, dtype={"code": str})
    shared = ""
    sp = scan_dir / "_l4_shared_instructions.md"
    if sp.exists():
        shared = sp.read_text(encoding="utf-8").strip()
    # 共享块的唯一事实源就是那个文件,消费侧不二次拼接(否则同一段会在每张卡里出现两遍)。
    # 2026-08-21:🔁 基率落稿与 📐 目标价锚(buy_ledger 派生)随 learning 层退役一并删除。

    # FN-1 第五修:`force_full_card`(早停安全网)自 2026-06-27 建成起**零生产调用点** ——
    # 高 conviction+多路共振的真龙头照样被表面 P1-P3 早停砍掉。这里接进真派发链。
    # n_channels / l2_lane_reserved 只在 L2 表里(finalists.csv 没这两列)→ 一次读入建索引。
    l2_priors: dict[str, dict] = {}
    l2p = scan_dir / "L2_gbdt_top200.csv"
    if l2p.exists():
        _l2 = pd.read_csv(l2p, dtype={"code": str})
        if "code" in _l2.columns:
            _l2["code"] = _l2["code"].astype(str).str.zfill(6)
            # 「一 code 一行」是本查表的语义前提,不是 L2 产物的保证:上游帧一旦有重复码
            # (2026-08-26 实跑:601665 在 L0 帧里两行 → L2 两行),`to_dict("index")` 直接
            # ValueError 炸掉整条 L4 派发 —— L3 已烧完的 token 全作废。重复行内容同源,
            # 按 L2 选择序保留第一条(确定性)。根因修在 `frame.build_market_frame` 出口。
            _l2 = _l2.drop_duplicates(subset="code", keep="first")
            l2_priors = _l2.set_index("code").to_dict("index")

    tickers: list[str] = []
    pinned: list[str] = []
    with_dossier: set[str] = set()  # Wave9 B-3:本次派发里"哪些票有档案可注入"(lint 探针 10 读)
    n_prompts = 0
    for _, r in fin.iterrows():
        raw = str(r.get("code", "") or "").strip()
        if not raw or raw == "nan":
            continue
        code6 = raw.split(".")[0].zfill(6)
        # Wave9 B-3:档案存在性判据是**无条件**的 —— 它曾只在(已退役的)stable_context
        # 分支里判,于是 `_dossier_present.json` 在默认配置下恒空。
        with contextlib.suppress(Exception):
            from autoresearch.dossier.schema import dossier_exists

            if dossier_exists(code6):
                with_dossier.add(code6)
        # Wave9 R5(TTL 复用退役)后不再因 `details/<code>.md` 已存在而跳过写 prompt——
        # dispatch_plan 已无条件全票派发,"卡已存在"不再是跳过写 prompt 的正当理由
        # (旧跳过 + dispatch_plan 新语义组合会炸出 PENDING-但-prompt-不存在,详见函数 docstring)。
        ticker = normalize_symbol(code6)  # 6 位码 → .SS/.SZ/.BJ(单一后缀口径)
        tickers.append(ticker)
        is_pinned = str(r.get("lane", "") or "").strip() == "pinned"
        body = [f"## L4 派发 — {code6} {r.get('name', '')}", ""]
        if is_pinned:  # 逐卡块内标记(共享前缀之后,不破 cache 契约)
            pinned.append(code6)
            note = str(r.get("pinned_note", "") or "").strip()
            body += [
                "**📌 保送票**(用户手工直通;已在 L1→L3 全程强留,不受漏斗取舍影响"
                + (f":{note}" if note else "")
                + ")——仍须按下方真实证据独立评判,不因『保送』降低尽调标准。",
                "",
                "**📌持仓管理要求**:本票为用户保送票(可能已持有)——满卡/早停卡都必须含"
                "『持仓管理』小节:D+1/D+2 卖出纪律(何价减/何价清)+加减仓触发位;若 "
                "pinned_note 含成本信息按其计算浮盈亏,无则按现价基准写纪律。",
                "",
            ]
        # 强制满卡(逐卡块内,共享前缀之后 → 不破 cache 契约):priors = finalists 行(conviction/
        # lane)+ L2 行(n_channels/l2_lane_reserved)。
        priors = {
            **l2_priors.get(code6, {}),
            "conviction": r.get("conviction"),
            "lane": r.get("lane"),
        }
        from autoresearch.scan.l4.rubric import rubric_cfg
        from autoresearch.scan.research_provenance import freeze_force_full
        ff_rule = rubric_cfg()['force_full']
        full_required = force_full_card(priors, conv_min=float(ff_rule['conviction_min']),
                                       channels_min=int(ff_rule['channels_min']))
        freeze_force_full(scan_dir/'_l4_force_full'/f'{code6}.json', code=code6,
                          decision=full_required, priors=priors, rule=ff_rule)
        if full_required:
            why = (
                "📌 保送持仓票"
                if is_pinned
                else f"强先验(conviction {r.get('conviction')} + 多路共振/配额救回)"
            )
            body += [
                f"**⛔ 强制满卡 — {why}:禁止早停。** 必须跑完 P4(陷阱核)+ P5(满卡):"
                "「盈利质量」与「偿付(爆雷)」两维**不得**标『未核』,必须 Read "
                "`_slim_deep.md` 取证后给分。评级仍由 rubric 三门定——强制满卡只保证"
                "**核得够深**,不保证结论向好(照样可以是 Underweight/Sell)。",
                "",
            ]
        # Wave10 B4:stable_context 分支已退役(离线 benchmark 收益 4.0% < 10% 门),
        # 只剩这一条 legacy 字节路 —— 原先的 if/else 二选一塌成无条件追加。
        body.append(compose_funnel_brief(code6, scan_dir).rstrip())
        # 昨卡回声(W9-B1b,逐卡块内、紧邻 dossier_content/差异段之后,共享前缀之后不破 cache
        # 契约):读已发布报告的最近一次判断,不并入上面已落盘的 differential context block
        # (该块的 source_paths 不含 reports/ 历史卡,回声混进去会让 hash 契约与实际来源脱节)。
        echo = yesterday_echo(code6, str(r.get("name", "") or ""), date)
        if echo:
            body.append(echo.rstrip())
        slim_path = input_dir / f"{ticker}_{date}_slim.md"
        deep_path = input_dir / f"{ticker}_{date}_slim_deep.md"
        intel_path = resolved_scan_dir / f"_l4_intel_{code6}.md"
        card_path = resolved_scan_dir / "details" / f"{code6}.md"
        prompt_parts = [
            # 固定标头(逐卡不变,≤300B)——cache 前缀契约(T8):共享块前不得出现逐卡可变内容,
            # 否则 30 卡并发前缀全断、cache 全 miss。逐卡专属标题(含 📌 保送标记)移到共享块**之后**。
            "# L4 派发 prompt(确定性落稿;编排以此为派发正文;先读共享块再读下方逐卡简报)",
            "",
            shared
            or "_(共享指令稿缺:`_l4_shared_instructions.md` 未落——按 stock-research lite-playbook 执行)_",
            "",
        ]
        prompt_parts += [
            "---",
            "",
            *body,
            "",
            "---",
            params_block(),
            "",
            f"- slim 数据:`{slim_path}`(P1–P3 表面块;{slim_hint()})",
            f"- deep 深核:`{deep_path}`(**survivor 进 P4 才 Read**;早停卡不读;缺文件=陷阱维标「未核」)",
            f"- 活体情报:`{intel_path}`(若存在:P3 先读它作催化/题材/机构主料、"
            f"自发网查降 ≤1 条验证;缺文件=回退卡内网查,cap 原规则)",
            f"- 决策卡写往:`{card_path}`",
            "",
        ]
        prompt = "\n".join(prompt_parts)
        (scan_dir / f"_l4_prompt_{code6}.md").write_text(prompt, encoding="utf-8")
        n_prompts += 1
    (scan_dir / "_harvest_list.txt").write_text(
        "\n".join(tickers) + ("\n" if tickers else ""), encoding="utf-8"
    )
    with contextlib.suppress(Exception):
        (scan_dir / "_dossier_present.json").write_text(
            json.dumps(sorted(with_dossier), ensure_ascii=False), encoding="utf-8"
        )
    # 档案 as-read 快照(2026-08-26 现场留存波 §4 R2)。**必须在这一刻抄** —— 档案是
    # `knowledge/dossiers/<code>.md`,assemble 收尾的 `dossier.delta.record_scan_deltas`
    # 会**原地改写**它(实测 300857 的 mtime = 读它那次 run 的收尾时刻),所以发布时再抄
    # 拿到的是 δ **之后**的文本,而 l4-card 读的是 δ **之前**那份。写进 staging(不是直接
    # 写 run 目录):staging 会被 `retention.mirror_staging` 整目录带走,这里不必知道 run 在哪。
    # `_dossier_present.json` 的形状**不动**(三个消费者按 list 读,post_run 还专门有
    # 「语法合法但形状不对」的测试)——新增独立文件,不改老契约。
    with contextlib.suppress(Exception):
        _snapshot_dossiers(scan_dir, with_dossier)
    return {
        "n_prompts": n_prompts,
        "tickers": tickers,
        "pinned": pinned,
        "context_mode": "legacy",  # Wave10 B4:stable_context 已退役,只剩这一条路
    }
