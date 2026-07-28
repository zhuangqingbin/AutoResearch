"""dossier δ 增量回写(spec ④⑦;确定性,零 LLM)。

design: docs/specs/2026-07-22-research-depth-dossier-design.md ④;
plan: docs/plans/2026-07-24-wave3-dossier-wiring-plan.md Task 1。

节切片一律以 schema.SECTIONS 锚定位(与 builder 同源);§8 append-only 近 20 条滚动,
同日同事件 key 整行替换(幂等);写盘走「读全文→切片改→整写回」。presence-gated:
档案缺 / 未首覆(initiated 空)→ skip 不建骨架(建档归 dossier-init 链,职责不混)。
异常上抛,由调用方决定兜底(assemble 挂钩 suppress,CLI 直接报)。
"""
from __future__ import annotations

from pathlib import Path

from autoresearch.dossier import builder, schema

_DELTA_KEEP = 20      # §8 滚动窗(spec ①:append-only,近 20 条滚动)
_ANCHOR_BAND = schema.SUMMARY_ANCHORS[2]   # "带位:"(SUMMARY_ANCHORS=业务/驱动/带位/风险/催化/判例)
_ANCHOR_PREC = schema.SUMMARY_ANCHORS[5]   # "判例:"


def _section_span(text: str, idx: int) -> tuple[int, int]:
    """§idx 正文区间 [start, end)(不含节头行);节锚缺 → (-1, -1)。

    已知边界(M-4,只记不修):节正文内若出现 `## ` 顶级标题会被当成下一节起点
    提前截断(真实档案的 LLM 节固定用 `### ` 三级标题,当前不触发);后续按节
    读取的消费方(如 L4 注入器)需知这条边界。
    """
    head = schema.SECTIONS[idx]
    i = text.find(head)
    if i < 0:
        return (-1, -1)
    start = text.find("\n", i) + 1
    j = text.find("\n## ", i + len(head))
    return (start, j if j > 0 else len(text))


def section_body(text: str, idx: int) -> str:
    """§idx 正文(不含节头行);与 `_section_span` 共享同一条已知边界(`## ` 顶级标题会提前截断)。"""
    start, end = _section_span(text, idx)
    return text[start:end] if start >= 0 else ""


def replace_section(text: str, idx: int, body: str) -> str:
    """整替 §idx 正文(节头不动);body 末尾自动补换行。节锚缺 → 原文返回(交 lint 报)。"""
    start, end = _section_span(text, idx)
    if start < 0:
        return text
    if not body.endswith("\n"):
        body += "\n"
    return text[:start] + body + text[end:]


def append_delta_line(text: str, date: str, line: str, *, key: str | None = None) -> str:
    """§8 追加 `- {date} {line}`;同日同 key 已有 → 整行替换(幂等);滚动保近 _DELTA_KEEP 条。

    去重匹配:`key` 给出时按 `- {date} {key}:` 前缀(全角冒号闭合,防 "入围"/"入围候补"
    这类共享前缀的 key 互相误删);`key` 为 None 时退化为整行精确匹配。
    """
    if _section_span(text, 7)[0] < 0:
        return text
    rows = [ln for ln in section_body(text, 7).splitlines() if ln.strip()]
    if key is not None:
        prefix = f"- {date} {key}:"
        rows = [r for r in rows if not r.startswith(prefix)]
    else:
        exact = f"- {date} {line}"
        rows = [r for r in rows if r != exact]
    rows.append(f"- {date} {line}")
    return replace_section(text, 7, "\n".join(rows[-_DELTA_KEEP:]) + "\n")


def refresh_summary_line(text: str, anchor: str, value: str) -> str:
    """摘要块内单锚行重写为 `- {anchor} {value}`;锚行缺 → 原文返回(lint 另报)。

    块切片用 `str.split("\\n")` 而非 `splitlines()` 再拆行:两者对同一分隔符
    互为逆操作(`"\\n".join(s.split("\\n")) == s`),重写单行后 rejoin 能原样保留
    块内空行与块尾换行(splitlines 会丢尾随换行信息,曾导致摘要块与下一节间
    空行/文件尾换行被吞)。
    """
    i = text.find(schema.SUMMARY_HEAD)
    if i < 0:
        return text
    j = text.find("\n## ", i)
    j = j if j > 0 else len(text)
    lines = text[i:j].split("\n")
    for k, ln in enumerate(lines):
        if ln.strip().startswith(f"- {anchor}"):
            lines[k] = f"- {anchor} {value}"
            return text[:i] + "\n".join(lines) + text[j:]
    return text


def set_frontmatter_key(text: str, key: str, value: str) -> str:
    """frontmatter 单键改写;键缺/无 frontmatter → 原文返回。"""
    if not text.startswith("---"):
        return text
    end = text.find("\n---", 3)
    if end < 0:
        return text
    lines = text[:end].splitlines()
    for k, ln in enumerate(lines):
        if ln.split(":", 1)[0].strip() == key:
            lines[k] = f"{key}: {value}"
            return "\n".join(lines) + text[end:]
    return text


def _refresh_band(text: str, pf: dict | None) -> str:
    """§3 估值带整节重算(prefetch val_band 在才动;与 builder 同一份纯函数)。"""
    band = (pf or {}).get("val_band")
    if not band:
        return text
    return replace_section(
        text, 2, builder._val_band_table(band) + "\n\n" + builder._band_position_text(band))


def _append_eps_snapshot(text: str, pf: dict | None) -> str:
    """§2 尾追加一致预期快照行(逐次留档,spec ①§2);同 as-of 已录 → 幂等跳过。"""
    fwd = (pf or {}).get("fwd_eps") or {}
    asof = fwd.get("asof")
    if not (asof and isinstance(fwd, dict)
            and any(str(k).startswith("fwd_eps_") for k in fwd)):
        return text
    line = f"- 快照 {asof}:{builder._fwd_eps_line(fwd)}"
    body = section_body(text, 1)
    if line in body:
        return text
    return replace_section(text, 1, body.rstrip("\n") + "\n" + line + "\n")


def _staging_dir_for(scan_root: str | Path, date: str) -> Path | None:
    """δ 用的 staging 目录:**当日优先**(δ 跑在 assemble 尾,当日素材就在手),
    当日缺 → 回退「不晚于 δ 日、最近有素材的日」;都无 → None。

    为什么不直接复用 builder 的 `_latest_staging_dir`:①建档跑在任意时点、只能取最近;
    δ 跑在当日收尾,拿当日才是正解——否则会用旧快照冒充今天(spec ① §4/§6「每次 δ」)。
    ②回退多一道 `p.name <= date` 上界过滤(Wave3.5 review M-1):`_latest_staging_dir`
    不做日期上界,重跑历史日 δ(补录/回放)会拿到**晚于**该历史日的素材,把未来数据
    写进过去的档案;建档场景本就要"当下最新"、不受此约束,故不改 `_latest_staging_dir`
    本体,只在这里的回退分支加过滤。
    """
    root = Path(scan_root)
    d = root / date
    if any((d / f).exists() for f in builder._STAGING_FILES):
        return d
    if not root.exists():
        return None
    days = sorted((p for p in root.iterdir()
                   if p.is_dir() and p.name[:2] == "20" and p.name <= date),
                  key=lambda p: p.name, reverse=True)
    for cand in days:
        if any((cand / f).exists() for f in builder._STAGING_FILES):
            return cand
    return None


_PLEDGE_PREFIX = "- **质押**"          # builder._pledge_row 行首锚(§4 腿级守卫,C-1)
_SEATS_PREFIX = "- **龙虎榜席位**"      # builder._seats_row 行首锚


def _leg_line(body: str, prefix: str) -> str:
    """从正文里按行首前缀取一行(§4 腿级守卫用;找不到 → ""）。"""
    for ln in body.splitlines():
        if ln.startswith(prefix):
            return ln
    return ""


def _refresh_section4_legs(text: str, staging: Path | None, code6: str) -> tuple[str, list[str]]:
    """§4 筹码与资金结构史 —— **腿级** upsert(Wave3.5 review C-1)。

    `_section4_body` 由质押(`_pledge_row`)/席位(`_seats_row`)两条独立腿拼成;旧实现
    只在**整节**判缺才保留旧值,只要一条腿有新值就整段覆盖 → 另一条腿的旧真内容被
    静默删除(真档案 002371 复现:质押腿几乎恒在、席位腿常缺,整节覆盖等于席位史
    天天被删)。这里改成腿级独立判断:某腿新值为空 → 保留该腿的旧行(从旧正文按
    行首前缀 `_leg_line` 提取,不是重算);两腿新值都空 → 整节不动、不写 as-of 戳
    (等价旧的整节守卫)。返回 (新文本, 跳过的腿标签列表,如 `["§4.seats"]`)。
    """
    old_body = section_body(text, 3)
    new_pledge = builder._pledge_row(staging, code6)
    new_seats = builder._seats_row(staging, code6)
    skipped = [lbl for lbl, new in (("§4.pledge", new_pledge), ("§4.seats", new_seats))
               if not new]
    if not new_pledge and not new_seats:
        return text, skipped
    pledge_line = new_pledge or _leg_line(old_body, _PLEDGE_PREFIX)
    seats_line = new_seats or _leg_line(old_body, _SEATS_PREFIX)
    body = "\n".join(r for r in (pledge_line, seats_line) if r)
    return replace_section(text, 3, f"_素材 as-of {staging.name}_\n\n{body}"), skipped


def _refresh_section6(text: str, staging: Path | None, code6: str,
                      date: str) -> tuple[str, list[str]]:
    """§6 催化剂日历 —— 单腿(`calendar_flags`),沿用整节守卫:新素材算不出真内容
    (含当日 staging 在但该票无行/无事件)→ 保留旧值,不覆盖(与 `_refresh_band` 同款;
    Wave3 T1 review 教训:半更新会制造节间自相矛盾)。返回 (新文本, 跳过标签,空或 `["§6"]`)。
    """
    body = builder._section6_body(staging, code6, date)
    if not body or body == builder._missing(date):
        return text, ["§6"]
    return replace_section(text, 5, f"_素材 as-of {staging.name}_\n\n{body}"), []


def intel_dossier_gaps(staging: Path | None, code6: str, max_lines: int = 2) -> list[str]:
    """当期情报稿里的 `档案缺口:` 行(Wave7 P4;确定性,零 LLM)。

    由来:2026-07-27 实跑,300857 的情报查到「2026-04 以 5.1 亿增资控股光为科技 51% 切入
    光模块」,**并且它自己注意到**注入的档案摘要里没有这第六项业务 —— 可那条发现只活在
    当天的情报稿里,没有任何机制把它带回档案,下次覆盖照样缺。我们本来就把档案摘要注入
    intel 让它去重,等于免费得到一个"档案哪儿旧了"的探测器,却一直没接收端。

    只捡**结构性事实缺口**(契约规定当期新闻事件不写这里,它们进事件段);合并进 §1
    业务模型叙事仍是 LLM 的活(首覆/季度对账时),本函数只负责让它**留下痕迹**。
    """
    if staging is None:
        return []
    p = Path(staging) / f"_l4_intel_{code6}.md"
    if not p.exists():
        return []
    try:
        lines = p.read_text(encoding="utf-8").splitlines()
    except Exception:  # noqa: BLE001
        return []
    out: list[str] = []
    for ln in lines:
        s = ln.strip().lstrip("-").strip()
        if s.startswith("档案缺口"):
            # 只剥「档案缺口」标签与紧随的一个冒号 —— 正文里还有「｜ 源: http(s)…」,
            # 逐个 split 冒号会把整条事实吃掉只剩 URL(首版就是这么写错的)。
            body = s[len("档案缺口"):].lstrip(":: ").strip()
            if body:
                out.append(body[:180])
        if len(out) >= max_lines:
            break
    return out


def _refresh_staging_sections(text: str, code6: str, date: str,
                              scan_root: str | Path) -> tuple[str, list[str]]:
    """§4 筹码资金史(腿级)/ §6 催化剂日历(节级)就地刷新(spec ① 表:每次 δ)。

    返回 (新文本, 跳过的节/腿标签列表)。跳过不静默(Wave3.5 review I-2):调用方
    (`record_scan_delta`/`record_scan_deltas`)把这份列表原样放进返回 dict,不再是
    "跳过了但外界看不见"。
    """
    staging = _staging_dir_for(scan_root, date)
    text, skip4 = _refresh_section4_legs(text, staging, code6)
    text, skip6 = _refresh_section6(text, staging, code6, date)
    for gap in intel_dossier_gaps(staging, code6):    # Wave7 P4:情报侧发现的档案缺口留痕
        text = append_delta_line(text, date, f"档案缺口(情报侧发现,待首覆/对账吸收):{gap}",
                                 key=f"档案缺口:{gap[:24]}")
    return text, skip4 + skip6


def record_scan_delta(code6: str, date: str, *, rating: str, conviction=None,
                      scan_root: str | Path = "context/scan") -> dict:
    """单票 δ 回写:§8 入围行 + §3 带位刷新 + §2 快照 + §4/§6 staging 刷新 + 摘要机算行 + last_delta。

    返回 dict 的 `sections_skipped`(Wave3.5 review I-2):本次因素材缺而跳过刷新的
    §4 腿/§6 节标签(如 `["§4.seats", "§6"]`),健康路径为 `[]`(不是缺键)——降级
    留痕不静默;`record_scan_deltas` 批量层同款收进 `out["sections_skipped"][code]`
    (非空才收,镜像 `issues` 记账口径)。
    """
    code6 = str(code6).split(".")[0].zfill(6)
    path = schema.dossier_path(code6)
    if not path.exists():
        return {"code": code6, "skipped": "no_dossier"}
    text = path.read_text(encoding="utf-8")
    if not schema.parse_frontmatter(text).get("initiated"):
        return {"code": code6, "skipped": "not_initiated"}

    bad_conv = conviction is None or conviction == "" or (
        isinstance(conviction, float) and conviction != conviction)
    conv = "" if bad_conv else f"(conv {conviction})"
    text = append_delta_line(text, date, f"入围:评级 {rating}{conv}", key="入围")

    pf = builder._load_prefetch(code6)
    text = _refresh_band(text, pf)
    text = _append_eps_snapshot(text, pf)
    text, sections_skipped = _refresh_staging_sections(text, code6, date, scan_root)

    from autoresearch.scan import dossier as scan_dossier  # lazy 防环(scan↔dossier,builder 同款)
    entries = scan_dossier.stock_dossier(code6, scan_root=scan_root,
                                         max_days=builder._PRECEDENT_WINDOW)
    prec_text = scan_dossier.render_dossier(code6, scan_root=scan_root,
                                            max_days=builder._PRECEDENT_WINDOW)
    from autoresearch.dossier import ledger as dledger
    rec = dledger.code_track_record(code6)
    track = dledger.render_track_block(code6, scan_root=scan_root)
    body7 = "\n\n".join(p for p in ((prec_text or builder._NO_PRECEDENT), track) if p)
    text = replace_section(text, 6, body7)

    calc = builder.render_summary_calc(pf, len(entries))
    # 摘要「带位:」与 §3(_refresh_band)同守卫:val_band 缺 → 跳过,保留摘要旧值,
    # 不用「数据缺(待预取)」覆盖一个原本的好值(与 §3 对称,要么都更新要么都保留)。
    if (pf or {}).get("val_band"):
        text = refresh_summary_line(text, _ANCHOR_BAND, calc["带位"])
    text = refresh_summary_line(text, _ANCHOR_PREC,
                                dledger.render_precedent_value(len(entries), rec))

    text = set_frontmatter_key(text, "last_delta", date)
    path.write_text(text, encoding="utf-8")
    return {"code": code6, "updated": True, "issues": schema.lint_dossier(text),
            "sections_skipped": sections_skipped}


def record_scan_deltas(scan_dir: Path | str, date: str) -> dict:
    """整日批量 δ:finalists × 终评级(_final_ratings.json,ensemble/verify 折回后)。

    终评级缺(文件缺/该票无卡「—」)→ 该票不记(防「无卡」污染 §8;卡面评级不可靠,
    P0-2 教训:折回只改 rows 不回写卡面)。单票失败不断链。

    返回 `{"updated": n, "issues": {code: [lint...]}, "sections_skipped": {code: [...]}}`。
    **返回 dict 而非 int 是有意的**(I-4,2026-07-24 终审):旧版只返回计数,
    `record_scan_delta` 尽责给出的 `issues` 从此消失 —— 摘要被写爆 3k 帽或锚行被写没 →
    `injectable_summary` 从此对该票返回 ""、注入无声停摆,而 lint 门与注入门同源(连假警
    都不会有)。本 plan 的 Global Constraint 写死「降级留痕…不空写不吞」,故让调用方
    **必须**看得见 issues。`sections_skipped` 同款(Wave3.5 review I-2):`record_scan_delta`
    的 §4/§6 跳过标签逐票收进来,非空才落码,不被这层批量 suppress 吞掉。
    """
    import contextlib
    import json as _json

    import pandas as pd
    out: dict = {"updated": 0, "issues": {}, "sections_skipped": {}}
    scan_dir = Path(scan_dir)
    fp = scan_dir / "finalists.csv"
    if not fp.exists():
        return out
    try:
        fin = pd.read_csv(fp, dtype={"code": str})
    except Exception:  # noqa: BLE001 — 坏 csv 当无处理
        return out
    if "code" not in fin.columns:
        return out
    ratings: dict = {}
    with contextlib.suppress(Exception):
        ratings = _json.loads((scan_dir / "_final_ratings.json").read_text(encoding="utf-8"))
    for _, r in fin.iterrows():
        code6 = str(r.get("code", "") or "").split(".")[0].zfill(6)
        rating = ratings.get(code6)
        if not code6.strip("0") or not rating or rating == "—":
            continue
        with contextlib.suppress(Exception):    # 单票坏档不断链(δ 是记账,不是发布门)
            res = record_scan_delta(code6, date, rating=rating,
                                    conviction=r.get("conviction"),
                                    scan_root=scan_dir.parent)
            out["updated"] += bool(res.get("updated"))
            if res.get("issues"):               # 非空才收(留痕给调用方打印)
                out["issues"][code6] = res["issues"]
            if res.get("sections_skipped"):     # 非空才收(镜像 issues 记账口径,I-2)
                out["sections_skipped"][code6] = res["sections_skipped"]
    return out
