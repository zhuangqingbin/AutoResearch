#!/usr/bin/env python3
"""intel 稿件硬顶守卫(确定性,零 LLM)—— Wave8 W8-13 起 / Wave9 W9-B2 改判。

**为什么需要**:`l4-intel` 的查询上限一直是**指令级**约束(prompt 里写 `≤N 条`),
agent 想超就超,`pr_20260714_007` 挂了 15 天没人裁。2026-07-28 实测 11 稿自报
16–29 条(cap 15),**全体超限** —— 探针天天报警、天天无视,狼来了效应把 P0 探针的
公信力磨没了。

修法两腿:
1. **cap 15 → 20**(`scan_config.jsonc`):对齐实测中位 ~18,消掉常态化警报;
2. **硬顶 30**(本模块):超硬顶必有后果 —— 内容照样丢,只是丢弃顺序按时效价值排。

**Wave9 W9-B2:拒稿 → 裁稿**。硬顶原本的后果是"整稿拒"(改名 `.rejected.md`,
card 侧 presence-gate 找不到 intel 就自动回退卡内网查)。问题是整稿拒把 **T0 增量**
(收盘后到跑报这段时间的新信息 = 明天开盘唯一还没被定价的东西)一并扔了 ——
2026-07-29 实测 002546/603893 两票中招(自报 36/34 条超硬顶 30,但事件段本身只有
5/1 行、结构完好),整稿拒把这种精简却新鲜的稿也一并否了,新闻面无谓变薄。现在
超硬顶触发的是 `trim_by_recency`:按 `T0 → 24h → 催化挂 → 背景 → >1周` 的时效
价值排序,只砍最不新鲜的事件行直到 ≤keep;T0/24h 永远最后被砍。`REJECTED` 收窄
为只留给**事件段一行都解析不出**的稿(结构不可信,裁无可裁);能解析的超顶稿——
不管裁后是否真的丢了行——一律 `TRIMMED`。

红线不变:**只拒稿不拒票**。情报是辅助面,不得反噬决策主链 —— 无论 TRIMMED 还是
REJECTED,进程退出码都是 0,本票照常出卡。自报缺失只 warn 不拒(无法对账 ≠ 违规,
弱证据不当强证据用)。

**复核回归修复(W9-B2-fix)**:TRIMMED 原地覆写会真删掉被砍的事件行,而
`autoresearch/learning/self_review.py` 的两条 intel 质量 lint
(`intel_future_dates_lint`/`intel_recency_lint`)靠裸读 `_l4_intel_*.md` 审计
——旧 REJECTED 靠改名整稿保留全文,这两条 lint 一直能看到完整原稿;TRIMMED 真删行后
它们会对被砍行永久失明,且被砍的(背景/>1周)恰是这两条 lint 最想抓的对象,审计
覆盖率下降方向与探针敏感度负相关。修法:真发生裁剪时(`cut>0`)裁前把全文单独留档到
`_l4_intel_<code>.pretrim`(**不带 `.md`**,故意让它对全仓所有 `_l4_intel_*.md`/
`*.md` 裸 glob 不可见,不会被当成第二份独立情报稿重复计数/审计),两条 lint 改为
存在留档时优先读留档。

**D13/A9(2026-08-19,用户裁决:intel 可信度硬化)**:情报编造已复发 3 次——
07-14 涨停捏造(P0 事故,据此打了 +2 净分)、07-16 日期焊接(原子数字全真但组合为假)、
08-13 五条断言三条编造——此前每次都靠事后人工发现。`lint_claims` 补一道**确定性、
零 LLM** 的对账:他票涨停/连板断言缺原文 URL,或带 URL 但与湖收盘数据不符,行尾标
`〔未核·…〕`。串接在 `guard_intel` 的 KEPT/TRIMMED 落盘路径上(REJECTED 稿件已
整体不可信、ABSENT 无稿可标,两者都不需要再标);标注前若改动了正文,覆写前把原文
存 `_l4_intel_<code>.orig` 侧车(同 `.pretrim` 惯例,不带 `.md`,对 glob 不可见)。
红线不变:**只标注、不拒稿**——机检抓到的是"未核",不是"假",举证责任从"读者
肉眼核对"挪到"机器标注",不越权替代下游判断。
"""
from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

import pandas as pd

from autoresearch.common import workspace as ws
from autoresearch.dataflows.symbol_utils import to_ts_code

HARD_CAP_DEFAULT = 30
_CLAIM_RE = re.compile(r"网查\s*(\d+)\s*条")

# 事件行时效窗优先级(数值越小越新鲜,裁剪时最后被砍)。认不出的窗按"背景"降权 ——
# 不把不明来源的行伪装成最新增量,是有意的保守选择。
_WINDOW_RANK = {"T0": 0, "24h": 1, "催化挂": 2, "背景": 3, ">1周": 4}
_EVENT_ROW = re.compile(r"^\|\s*\d{4}-\d{2}-\d{2}\s*\|")


def intel_path(scan_dir: Path | str, code: str) -> Path:
    return Path(scan_dir) / f"_l4_intel_{code}.md"


def claimed_queries(text: str) -> int | None:
    """声明行自报的网查条数;没写 → None(不猜)。"""
    m = _CLAIM_RE.search(text)
    return int(m.group(1)) if m else None


def _window_of(row: str) -> int:
    cells = [c.strip() for c in row.strip().strip("|").split("|")]
    win = cells[1] if len(cells) > 1 else ""
    return _WINDOW_RANK.get(win, 3)          # 认不出的窗按"背景"降权,不当 T0


def _event_rows(text: str) -> list[int]:
    """事件段表格行的行号(0-based)。空列表 = 一行事件都解析不出(稿件结构不可信)。"""
    return [i for i, ln in enumerate(text.splitlines()) if _EVENT_ROW.match(ln)]


def trim_by_recency(text: str, *, keep: int = 10) -> tuple[str, int]:
    """按时效窗优先级把事件段裁到 ≤keep 行 → (新全文, 被砍行数)。

    Wave9 W9-B2:超硬顶从"整稿拒"改"按时效裁"——整稿拒会把 **T0 增量**(明天开盘
    唯一还没被定价的东西)一并扔掉,2026-07-29 实测 002546/603893 两票新闻面因此
    变薄。硬顶的牙还在(超帽内容照样丢),只是丢弃顺序按时效价值排。

    行数已在 keep 以内(含事件行数为 0,即无法识别事件表)时是 no-op,原文原样
    返回、cut=0 —— "裁不动"和"没什么可裁"用同一个信号面(cut),`guard_intel` 侧
    另用 `_event_rows` 区分"结构不可信"与"稿子本来就精简"两种 cut=0。
    """
    lines = text.splitlines()
    idx = _event_rows(text)
    if len(idx) <= keep:
        return text, 0
    ranked = sorted(idx, key=lambda i: (_window_of(lines[i]), i))
    drop = set(ranked[keep:])
    out = [ln for i, ln in enumerate(lines) if i not in drop]
    return "\n".join(out) + ("\n" if text.endswith("\n") else ""), len(drop)


# ── D13/A9:他票涨停/连板断言确定性对账(用户裁决,2026-08-19)───────────────────
#
# 他票走势编不出来会被本票 slim 拆穿(price_claim_mismatch 已挡本票),但"XX票今天
# 涨停"这种关于**他票**的描述性断言,agent 自己是唯一的事实来源——这正是三次情报
# 编造复发共同的可乘之隙。lint_claims 用湖里已结算的 daily 收盘数据把这道口子钉上。

_CLAIM_WORDS = ("涨停", "连板")
# 不用 `\b`:Python 3 的 `\w`(进而 `\b`)按 Unicode 语义把中文视为"词字符",代码紧贴中文
# 时(常见写法,如「隆基绿能601012今日涨停」无分隔符)`\b\d{6}\b` 会**匹配不到**(已实测
# 验证)。改用数字邻接负向环视:只要求"恰好 6 位、前后都不是数字",与中文/括号/空格/
# 标点相邻都算数,同时仍拒绝匹配更长数字串(日期/金额)中间的 6 位子串。
_CODE_RE = re.compile(r"(?<!\d)\d{6}(?!\d)")
_MISMATCH_PCT_FLOOR = 9.0    # 真涨停(含 ST 5%/主板 10%/创业板科创板 20%)必然 ≥ 此值
_TAG_NO_URL = "〔未核·缺URL〕"
_TAG_MISMATCH = "〔未核·与湖不符〕"


def _lake_pct_chg(code: str, trade_date: str) -> float | None:
    """`lint_claims` 缺省的湖查询:`lake/daily/<trade_date>.parquet` 取 `ts_code`/`pct_chg`。

    缺分区/缺列/缺该票 → `None`(无从对账,不当"不符"用——弱证据不当强证据,
    与 `guard_intel` 对「缺自报」的一贯立场同一原则)。
    """
    path = ws.lake_root() / "daily" / f"{trade_date}.parquet"
    if not path.exists():
        return None
    try:
        df = pd.read_parquet(path, columns=["ts_code", "pct_chg"])
    except Exception:  # noqa: BLE001 — 坏分区/缺列 = 无从对账,不是"不符"
        return None
    hit = df.loc[df["ts_code"] == to_ts_code(code), "pct_chg"]
    if hit.empty or pd.isna(hit.iloc[0]):
        return None
    return float(hit.iloc[0])


def lint_claims(text: str, *, self_code: str, trade_date: str,
                pct_lookup=None) -> tuple[str, dict]:
    """他票涨停/连板断言确定性对账 —— 只标注、不拒稿(红线:不 raise,不改变退出码)。

    逐行扫描:含「涨停」或「连板」**且**行内出现 ≠ `self_code` 的六位代码(判定为
    在断言"他票")才处理——**本票**的行情断言不归本函数管(`l4-intel.md` 另有硬要求
    本票不自报行情数字,那是 `price_claim_mismatch` 的地盘)。

    - 行内没有 `http://`/`https://` → 行尾追加 `〔未核·缺URL〕`,`no_url` 计数 +1。
    - 有 URL 但湖里该他票当日 `pct_chg < 9`(不是真涨停)→ 行尾追加
      `〔未核·与湖不符〕`,`mismatch` 计数 +1。一行提到多个他票代码时,任一对不上
      即标(聚合断言里有一个查不实,整行举证就不成立)。
    - 有 URL 但湖查不到(缺分区/缺该票,`pct_lookup` 返回 `None`)→ 无从对账,
      不标——弱证据不当强证据。
    - 有 URL 且湖数据吻合(`pct_chg >= 9`)→ 原样保留,不标。

    `pct_lookup(code, trade_date) -> float | None` 可注入(测试用,绕开真实湖 I/O);
    缺省用 `_lake_pct_chg` 读 `lake/daily/<trade_date>.parquet`。

    **幂等**:`l4-stock.js` 会对同一份稿跑两趟 `guard_intel`(先直调 `intel_guard`
    CLI,`intel_status --normalize` 内部又调一次)——已带标记的行原样透传、不再重标,
    否则第二趟会在行尾再叠一层〔未核〕,且 `_apply_claims_lint` 会把"已标一次"的
    文本误当新原文存进 `.orig`,真原文永久丢失。

    返回 `(标注后的全文, {"no_url": 缺URL行数, "mismatch": 与湖不符行数})`(计数按
    **当前**文本里的标记行数算,不是本次新增行数——重跑幂等场景下两者才会不同)。
    """
    lookup = pct_lookup or _lake_pct_chg
    self6 = str(self_code).strip().zfill(6)
    no_url = mismatch = 0
    out: list[str] = []
    for line in text.splitlines():
        if line.endswith(_TAG_NO_URL):        # 幂等:已标过,原样透传,不再重标/重数
            out.append(line)
            no_url += 1
            continue
        if line.endswith(_TAG_MISMATCH):
            out.append(line)
            mismatch += 1
            continue
        if not any(w in line for w in _CLAIM_WORDS):
            out.append(line)
            continue
        others = [c for c in _CODE_RE.findall(line) if c != self6]
        if not others:                    # 没提他票(或只提了本票)→ 不归本函数管
            out.append(line)
            continue
        if "http://" not in line and "https://" not in line:
            out.append(line + _TAG_NO_URL)
            no_url += 1
            continue
        pcts = [lookup(c, trade_date) for c in others]
        if any(p is not None and p < _MISMATCH_PCT_FLOOR for p in pcts):
            out.append(line + _TAG_MISMATCH)
            mismatch += 1
        else:
            out.append(line)              # 湖吻合,或查不到(无从对账)→ 不标
    new_text = "\n".join(out) + ("\n" if text.endswith("\n") else "")
    return new_text, {"no_url": no_url, "mismatch": mismatch}


def _apply_claims_lint(src: Path, text: str, *, self_code: str, trade_date: str) -> dict:
    """对 `src` 当前落盘内容跑 `lint_claims`;真发生标注时,覆写前把原文存 `.orig` 侧车。

    与 `guard_intel` TRIMMED 分支的 `pretrim` 同一惯例(见上方模块 docstring D13/A9
    段):侧车文件名刻意不以 `.md` 结尾(`_l4_intel_<code>.orig`),对全仓
    `glob("_l4_intel_*.md")`/`glob("*.md")` 天然不可见,不会被误当成第二份独立情报稿
    重复计数/审计。未标注(标注前后文本逐字节相同)时不写侧车、不覆写 —— 没有变化
    就没有审计价值(同 `guard_intel` 对 `cut==0` 时不留 `.pretrim` 的立场)。
    """
    linted, meta = lint_claims(text, self_code=self_code, trade_date=trade_date)
    if linted == text:
        return {**meta, "orig_as": None}
    orig = src.with_name(f"_l4_intel_{self_code}.orig")
    orig.write_text(text, encoding="utf-8")
    src.write_text(linted, encoding="utf-8")
    return {**meta, "orig_as": orig.name}


def _extract_claim_events(src: Path, text: str, *, self_code: str, trade_date: str) -> dict:
    """B4(Q-B ③,2026-09-07)**影子**:把稿里关于**本票**的回购/增持/减持/中标行抽成
    ClaimEvidence v2 事件,经绑定器得结论后写侧车 `_l4_claims_<code>.json`。

    影子的含义:① 新增产物,不改稿件正文、不改 `claims_lint`、不改 `action`;② 没有任何
    门读它;③ 现阶段没有绑定来源(观测/blob 为空),所以每条结论都是 `SOURCE_NOT_BOUND`
    —— 它证明的是「抽取器在真稿上抽出了什么」,不是「断言被核实了」。B5 的 80 条人工标注
    就在这些侧车上做;绑定真来源后同一条管线不用改。

    「本票」= 行内不含**他票**六位代码(与 `lint_claims` 管的「他票」互补,两边不重叠)。
    一行事件都没有时不写侧车(与 `.orig` 同一立场:没有变化就没有审计价值)。
    """
    from autoresearch.news.claim_binding import support_bound_claim
    from autoresearch.news.claim_extract import PREDICATES, bundle_from_extraction, extract_event

    self6 = str(self_code).strip().zfill(6)
    rows, errors = [], []
    for line_no, line in enumerate(text.splitlines(), start=1):
        if not any(w in line for w in PREDICATES):
            continue
        if any(c != self6 for c in _CODE_RE.findall(line)):
            continue                                        # 他票的事归 lint_claims
        claim_id = f"cl_{self6}_{trade_date}_{line_no}"
        try:
            extracted = extract_event(line, subject_code=self6)
            if extracted is None:
                continue
            bundle = bundle_from_extraction(extracted, claim_id=claim_id)
            verdict = support_bound_claim(bundle["event"], bundle, observations={}, texts={},
                                          trusted_fields=(), decision_at=None)
            rows.append({"line_no": line_no, "line": line.strip(), "bundle": bundle,
                         "extraction_notes": extracted["notes"],
                         "verdict": verdict["verdict"], "reason": verdict["reason"]})
        except Exception as exc:  # shadow instrumentation must not break the production guard
            errors.append({"line_no": line_no, "reason": "EXTRACTION_FAILED",
                           "detail": f"{type(exc).__name__}: {exc}"})
    if not rows:
        result = {"n": 0, "sidecar": None}
        if errors:
            result["errors"] = errors
        return result
    sidecar = src.with_name(f"_l4_claims_{self6}.json")
    try:
        sidecar.write_text(json.dumps({
            "schema_version": 1, "code": self6, "trade_date": trade_date,
            "extraction": "regex_v1", "binding": "none", "events": rows,
        }, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    except Exception as exc:  # output is shadow-only; preserve guard action/verdict
        errors.append({"reason": "SIDECAR_WRITE_FAILED", "detail": str(exc)})
        return {"n": len(rows), "sidecar": None, "errors": errors}
    result = {"n": len(rows), "sidecar": sidecar.name}
    if errors:
        result["errors"] = errors
    return result


def guard_intel(scan_dir: Path | str, code: str, *,
                hard_cap: int = HARD_CAP_DEFAULT) -> dict:
    """检查一份 intel 稿;超硬顶则按时效裁剪,裁无可裁才整拒。返回可直接 JSON 序列化的裁决。

    `action` ∈ `ABSENT`(无稿,presence-gated 安静通过)/ `KEPT`(未超顶)/
    `TRIMMED`(超顶但事件段可解析,已按时效窗裁到 ≤10 行,T0/24h 增量保留;
    `dropped_rows>0` 时裁前原文留档于 `pretrim_as` 指名的 `<code>.pretrim`,
    供 intel 质量 lint 审计全稿用)/
    `REJECTED`(超顶且事件段一行都解析不出,结构不可信,裁无可裁,照旧整稿拒)。

    KEPT/TRIMMED 的返回额外带 `claims_lint`(D13/A9,见模块 docstring):
    `lint_claims` 的 `{no_url, mismatch}` 计数 + 真发生标注时的 `.orig` 侧车名
    (`orig_as`,未标注则 `None`)。REJECTED(稿件已整体不可信)与 ABSENT(无稿)
    不再需要标注,没有此字段。
    """
    src = intel_path(scan_dir, code)
    trade_date = Path(scan_dir).name.replace("-", "")   # 'YYYY-MM-DD' → 'YYYYMMDD'
    if not src.exists():
        return {"ok": True, "code": code, "action": "ABSENT", "claimed": None}
    try:
        text = src.read_text(encoding="utf-8")
    except Exception as e:  # noqa: BLE001 — 读不动不等于违规,放行并留痕
        return {"ok": True, "code": code, "action": "KEPT", "claimed": None,
                "warn": f"unreadable: {e!r}"}

    claimed = claimed_queries(text)
    if claimed is None:
        # 缺自报 = 无法对账,照旧只 warn。以"缺"推断"违规"是把弱证据当强证据。
        claims_lint = _apply_claims_lint(src, text, self_code=code, trade_date=trade_date)
        return {"ok": True, "code": code, "action": "KEPT", "claimed": None,
                "warn": "unreported", "claims_lint": claims_lint,
                "claim_events": _extract_claim_events(src, text, self_code=code, trade_date=trade_date)}
    if claimed > hard_cap:
        trimmed, cut = trim_by_recency(text)
        if not _event_rows(text):
            # 事件段一行都解析不出 → 稿件结构不可信,裁无可裁,照旧整拒
            # (REJECTED 的新语义)。注意:不能用 `cut == 0` 判"解析不出" ——
            # 精简但结构完好的稿(事件行数本就 ≤keep)也会 cut=0,那不是不可信,
            # 是没什么可裁(002546/603893 实测两票正是这种:36/34 条自报超顶,
            # 事件段却只有 5/1 行)。误把它当 REJECTED 会重犯本任务要修的老毛病。
            dst = src.with_name(f"_l4_intel_{code}.rejected.md")
            src.replace(dst)          # 改名不删除:证据留档,便于事后对账
            return {"ok": False, "code": code, "action": "REJECTED",
                    "claimed": claimed, "hard_cap": hard_cap, "kept_as": dst.name,
                    "note": "事件段不可解析,整稿拒;card 回退卡内网查"}
        pretrim_as = None
        if cut:
            # 复核发现的回归(W9-B2-fix):原地覆写会**真删掉**被砍的事件行 ——
            # 不像旧 REJECTED 靠改名整稿保留全文,`autoresearch/learning/self_review.py`
            # 的 intel_future_dates_lint / intel_recency_lint 两条质量 lint 若只读
            # canonical 文件,会对被砍行永久失明。而 trim_by_recency 优先砍的(背景/
            # >1周)恰好是这两条 lint 最想抓的对象(净分未衰减/前视穿越)——审计覆盖率
            # 下降方向与探针敏感度负相关,不是正交。裁前把全文单独留档,供 lint 审计。
            #
            # 留档命名故意**不以 `.md` 结尾**(`_l4_intel_<code>.pretrim`,没有第二段
            # `.md`)—— 已用真实 glob 验证:`_l4_intel_<code>.pretrim.md` 这种双后缀
            # 命名会被 `glob("_l4_intel_*.md")` 顺带命中(`*` 吃得下中间的 `.pretrim`),
            # 导致 self_review.py 的 intel_query_cap_lint / product_shape_lint 的
            # 「intel零URL」探针、report_sections.py 的字节经济表把它当成第二份独立
            # 情报稿重复计数/重复审计。不带 `.md` 的名字对上述所有裸 glob 天然不可见
            # (已用 tests/learning/test_product_shape_lint.py::test_intel_pretrim_archive_not_counted
            # 钉住),无需在每个消费方逐一打补丁。cut==0 时不留档:trimmed 与原文逐字节
            # 相同,留一份内容相同的旁路文件没有审计价值。
            archive = src.with_name(f"_l4_intel_{code}.pretrim")
            archive.write_text(text, encoding="utf-8")
            pretrim_as = archive.name
        stamp = (f"〔已裁剪·自报 {claimed} 超硬顶 {hard_cap}·"
                 f"按时效窗保留 T0/24h/催化挂,砍 {cut} 行〕\n")
        final_text = stamp + trimmed
        src.write_text(final_text, encoding="utf-8")
        # D13/A9:对裁剪后**最终**落盘的正文跑他票断言对账(不是裁前原文——已被砍掉
        # 的行不再是稿件的一部分,没有再标注的意义)。
        claims_lint = _apply_claims_lint(src, final_text, self_code=code, trade_date=trade_date)
        return {"ok": True, "code": code, "action": "TRIMMED",
                "claimed": claimed, "hard_cap": hard_cap, "dropped_rows": cut,
                "pretrim_as": pretrim_as, "claims_lint": claims_lint,
                "claim_events": _extract_claim_events(src, final_text, self_code=code, trade_date=trade_date),
                "note": "T0/24h 增量保留;card 照常读 intel;"
                        + (f"裁前原文留档 {pretrim_as}(lint 审计用)" if pretrim_as
                           else "未真丢行,无需留档")}
    claims_lint = _apply_claims_lint(src, text, self_code=code, trade_date=trade_date)
    return {"ok": True, "code": code, "action": "KEPT", "claimed": claimed,
            "hard_cap": hard_cap, "claims_lint": claims_lint,
            "claim_events": _extract_claim_events(src, text, self_code=code, trade_date=trade_date)}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description="intel 稿件硬顶守卫:自报网查数超硬顶则按时效裁稿,裁无可裁才整拒(只拒稿不拒票)")
    ap.add_argument("date", help="分析日 YYYY-MM-DD")
    ap.add_argument("code", help="6 位股票代码")
    ap.add_argument("--hard-cap", type=int, default=HARD_CAP_DEFAULT,
                    help=f"自报网查条数硬顶,超过即按时效裁剪(默认 {HARD_CAP_DEFAULT})")
    ap.add_argument("--scan-dir", default=None, help="覆盖 context/scan/<date>")
    args = ap.parse_args(argv)

    scan_dir = Path(args.scan_dir) if args.scan_dir else ws.scan_root() / args.date
    print(json.dumps(guard_intel(scan_dir, args.code, hard_cap=args.hard_cap),
                     ensure_ascii=False))
    return 0          # 拒稿不是进程失败 —— 只拒稿不拒票


if __name__ == "__main__":
    raise SystemExit(main())
