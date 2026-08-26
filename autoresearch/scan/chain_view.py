#!/usr/bin/env python3
"""推荐链路视图 —— 一条命令拉出「这只票当天是怎么被推上来的」(确定性、零 LLM、零网络)。

design: docs/specs/2026-08-26-scene-retention-and-buy-owner-design.md §4.3 R7

## 为什么要它

复盘一只推荐票效果不好,要回答的是**在哪一段出的问题**:召回线放它进来的理由是什么、
L2 凭什么进菜单、pass1 留没留、L3 写的兑现机制是什么、L4 读到的数字长什么样、E6 为什么
挑中它、事后到底涨没涨。这些事实散在十来个产物里(护照/CSV/JSON/卡片/决策文件),
人肉拼一次要开十个文件,而且**跨 run 的口径不一样**(旧 run 缺哪几件全靠记)。

本模块只做一件事:按顺序把这些片段接起来,**缺的明写「缺席」**。它不解释、不判断、
不打分 —— 那是人的活。

## 读哪里(优先级)

`trace/staging/`(2026-08-26 起随发布镜像)→ `trace/`(老 run 的白名单副本)→
`context_<engine>/scan/<date>/`(最后兜底,**并标注**「读的是共享 staging,同数据日重跑
会覆盖,可能已不是本 run 当时那份」——实测 64 个已发布 run 只剩 49 个 staging)。

  uv run --no-sync python -m autoresearch.scan.chain_view 20260825_2149 603317
  uv run --no-sync python -m autoresearch.scan.chain_view <run_dir> 603317 --out chain.md
"""
from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path

from autoresearch.common import workspace as ws

ABSENT = "_缺席_"


# ───────────────────────── 定位:run 目录 → 各段产物 ─────────────────────────

class Sources:
    """一次 run 的产物定位器。三级回退,并记录每次命中来自哪一级(渲染时如实标注)。"""

    def __init__(self, run_dir: Path):
        self.run = Path(run_dir)
        self.trace = self.run / "trace"
        self.mirror = self.trace / "staging"
        self.inputs = self.trace / "inputs"
        self.analysis_date = self._analysis_date()
        self.shared = (ws.scan_root() / self.analysis_date) if self.analysis_date else None
        self.used_shared = False

    def _analysis_date(self) -> str:
        for p in (self.run / "manifest.json",):
            if p.is_file():
                try:
                    return str(json.loads(p.read_text(encoding="utf-8")).get("analysis_date", ""))
                except (OSError, json.JSONDecodeError):
                    return ""
        return ""

    def find(self, *names: str) -> Path | None:
        """按 mirror → trace → shared 找第一个存在的文件(names 是同一件东西的别名)。"""
        for base in (self.mirror, self.trace):
            for name in names:
                p = base / name
                if p.is_file():
                    return p
        if self.shared is not None:
            for name in names:
                p = self.shared / name
                if p.is_file():
                    self.used_shared = True
                    return p
        return None

    def rows(self, *names: str) -> list[dict]:
        p = self.find(*names)
        if p is None:
            return []
        try:
            with p.open(encoding="utf-8-sig", newline="") as fh:
                return list(csv.DictReader(fh))
        except OSError:
            return []

    def doc(self, *names: str) -> object | None:
        p = self.find(*names)
        if p is None:
            return None
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None

    def rel(self, path: Path | None) -> str:
        """路径按 run 目录相对化 —— 视图是给人读的,绝对路径会把一行撑到 200 字符。"""
        if path is None:
            return ABSENT
        try:
            return str(Path(path).relative_to(self.run))
        except ValueError:
            return str(path)

    def text(self, *names: str) -> str | None:
        p = self.find(*names)
        if p is None:
            return None
        try:
            return p.read_text(encoding="utf-8")
        except OSError:
            return None


def _z6(value: object) -> str:
    return str(value or "").split(".")[0].strip().zfill(6)


def _row_for(rows: list[dict], code6: str) -> dict | None:
    for r in rows:
        if _z6(r.get("code") or r.get("ticker")) == code6:
            return r
    return None


def _round_floats(text: str) -> str:
    """把 `-10.100334448160531` 这种 CSV 往返出来的 17 位浮点压到 4 位 —— 视图是给人读的,
    多余的位数只会挤掉真正要看的字。**只动显示**,不动任何产物。"""
    import re

    def _sub(m: "re.Match[str]") -> str:
        return f"{float(m.group(0)):.4f}".rstrip("0").rstrip(".")

    return re.sub(r"-?\d+\.\d{6,}", _sub, text)


def _fmt(value: object, cap: int = 200) -> str:
    if value is None or value == "":
        return "—"
    s = _round_floats(str(value).replace("\n", " ").strip())
    return s if len(s) <= cap else s[: cap - 1] + "…"


def _kv(pairs: list[tuple[str, object]]) -> list[str]:
    return [f"- **{k}**:{_fmt(v)}" for k, v in pairs]


# ───────────────────────── 各段渲染 ─────────────────────────

def _sec_identity(src: Sources, code6: str) -> list[str]:
    out = ["## ① run 身份"]
    man: dict = {}
    mp = src.run / "manifest.json"
    if mp.is_file():
        try:
            man = json.loads(mp.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            man = {}
    contract = src.doc("run_contract.json") or {}
    if not contract:
        out.append(f"- run_contract:{ABSENT}")
    else:
        dirty = contract.get("git_dirty")
        dirty_txt = ("未记录(v1 契约)" if "git_dirty" not in contract
                     else ("**脏树**:" + ", ".join(contract.get("dirty_paths") or [])[:160]
                           if dirty else "干净"))
        out += _kv([
            ("run_id", contract.get("run_id") or man.get("run_id")),
            ("数据日", contract.get("analysis_date") or man.get("analysis_date")),
            ("git_sha", contract.get("git_sha")),
            ("工作树", dirty_txt),
            ("prompt 指纹", f"{len(contract.get('prompt_hashes') or {})} 份"
                            + ("(v1 契约未记)" if "prompt_hashes" not in contract else "")),
            ("contract_hash", contract.get("contract_hash")),
        ])
    from autoresearch.scan.retention import verify_manifest
    v = verify_manifest(src.run)
    out.append(f"- **现场完整性**:" + (
        "MANIFEST 缺席(2026-08-26 之前的 run 天然如此)" if v.get("reason") == "no-manifest"
        else ("✓ 全部 %d 件对得上" % v["n"] if v["ok"] else
              f"⚠️ 变 {len(v['changed'])} · 缺 {len(v['missing'])} · 多 {len(v['extra'])}")))
    return out


def _sec_passport(src: Sources, code6: str) -> list[str]:
    doc = src.doc("_candidate_passport.json")
    out = ["", "## ② 候选护照(全漏斗轨迹)"]
    if not isinstance(doc, dict):
        return out + [f"- {ABSENT}(`_candidate_passport.json` 未随本 run 留存)"]
    entries = doc.get("candidates") or doc.get("passports") or doc
    row = None
    if isinstance(entries, dict):
        row = entries.get(code6)
    elif isinstance(entries, list):
        row = next((e for e in entries if _z6(e.get("code")) == code6), None)
    if row is None:
        return out + [f"- 护照里没有这只票(未进 L1 打分集,或护照当日未生成)"]
    out.append("```json")
    out.append(json.dumps(row, ensure_ascii=False, indent=1, sort_keys=True))
    out.append("```")
    return out


def _sec_l1(src: Sources, code6: str) -> list[str]:
    out = ["", "## ③ L1 召回"]
    row = _row_for(src.rows("L1_scored_full.csv"), code6)
    if row is None:
        return out + [f"- L1 打分行 {ABSENT}(该票未过 L0 硬门,或产物未留存)"]
    # provenance 三列(recall_channels/n_channels/best_rank)只在**召回集**表里,
    # `L1_scored_full.csv` 是全量打分表、没有这三列 —— 未入召回的票天然没有"命中通道"。
    recall = _row_for(src.rows("L1_recall_top1000.csv"), code6) or {}
    chan = (f"{recall.get('recall_channels')}(n={recall.get('n_channels')}, "
            f"best_rank={recall.get('best_rank')})" if recall else "未进 top1000 召回集")
    out += _kv([
        ("名称/行业", f"{row.get('name')} / {row.get('industry')}"),
        ("composite", row.get("composite")),
        ("命中通道", chan),
        ("当日/60日涨幅", f"{row.get('pct_1d')}% / {row.get('pct_60d')}%"),
        ("距60日高", row.get("dist_high_60")),
        ("主力净占比 / cmf20 / obv20", f"{row.get('main_net_ratio')} / {row.get('cmf_20')} / {row.get('obv_mom_20')}"),
        ("pe / pb / np_yoy / roe", f"{row.get('pe')} / {row.get('pb')} / {row.get('np_yoy')} / {row.get('roe')}"),
    ])
    ch = [r for r in src.rows("L1_channels.csv") if _z6(r.get("code")) == code6]
    out.append("- **逐路名次**:" + (
        "、".join(f"{r.get('channel')} #{r.get('channel_rank')}({r.get('channel_score')})"
                  for r in sorted(ch, key=lambda r: str(r.get("channel"))))
        if ch else f"{ABSENT}(`L1_channels.csv` 未留存 —— 逐路名次的唯一来源)"))
    return out


def _sec_l2(src: Sources, code6: str) -> list[str]:
    out = ["", "## ④ L2 菜单"]
    row = _row_for(src.rows("L2_gbdt_top200.csv"), code6)
    if row is None:
        return out + ["- 未进 L2 菜单(被召回线或分层采样挡在外面)"]
    out += _kv([
        ("l2_rank / gbdt_score", f"{row.get('l2_rank')} / {row.get('gbdt_score')}"),
        ("进菜单的理由", f"{row.get('selection_reason')} · {row.get('selection_detail') or '—'}"),
        ("配额救回", row.get("l2_lane_reserved")),
    ])
    return out


def _sec_pass1(src: Sources, code6: str) -> list[str]:
    out = ["", "## ⑤ L3 pass1 分诊"]
    kept = _row_for(src.rows("_l3_pass1_kept.csv"), code6)
    cut = _row_for(src.rows("_l3_pass1_cut.csv"), code6)
    if kept is not None:
        out.append(f"- **留下**(`{kept.get('selection_reason')}` · {kept.get('selection_detail') or '—'})")
    elif cut is not None:
        out.append("- **被切**(落 `_l3_pass1_cut.csv` 影子;不代表判死)")
    else:
        out.append(f"- {ABSENT}(pass1 产物未留存)")
    meta = src.doc("_l3_pass1_meta.json")
    if isinstance(meta, dict):
        out.append(f"- 当日分诊:{meta.get('n_in')} → {meta.get('n_kept')}"
                   f"(target {meta.get('target')};强制补入 {meta.get('forced_in')})")
    return out


def _sec_l3(src: Sources, code6: str) -> list[str]:
    out = ["", "## ⑥ L3 精排判断"]
    judged = _row_for(src.rows("L3_judged_full.csv"), code6)
    if judged is None:
        out.append(f"- judged 行 {ABSENT}(l3-rank 未判它 / 产物未留存)")
    else:
        out += _kv([
            ("conviction / lane / 倾向", f"{judged.get('conviction')} / {judged.get('lane')} / {judged.get('triage_lean')}"),
            ("finalist", judged.get("finalist")),
            ("thesis", _fmt(judged.get("thesis"), 400)),
            ("mechanism(两日内兑现)", _fmt(judged.get("mechanism"), 300)),
            ("risk", _fmt(judged.get("risk"), 300)),
            ("catalyst", _fmt(judged.get("catalyst"), 200)),
            ("fragility", _fmt(judged.get("fragility"), 200)),
        ])
    fin = _row_for(src.rows("L3_fine_finalists.csv", "finalists.csv"), code6)
    if fin is None:
        out.append("- **未入 finalist**")
    else:
        out.append(f"- **入 finalist** · guard=`{fin.get('guard') or '—'}`"
                   f" · lane=`{fin.get('lane')}`"
                   + (f" · 📌{fin.get('pinned_note')}" if fin.get("pinned_note") else ""))
    return out


def _sec_l4(src: Sources, code6: str) -> list[str]:
    out = ["", "## ⑦ L4 研究"]
    prompt = src.find(f"reasoning/l4/_l4_prompt_{code6}.md", f"_l4_prompt_{code6}.md")
    intel = src.find(f"reasoning/l4/_l4_intel_{code6}.md", f"_l4_intel_{code6}.md")
    slim = next((p for p in sorted((src.inputs / "slim").glob(f"*{code6}*_slim.md"))), None) \
        if (src.inputs / "slim").is_dir() else None
    deep = next((p for p in sorted((src.inputs / "slim").glob(f"*{code6}*_slim_deep.md"))), None) \
        if (src.inputs / "slim").is_dir() else None
    out += [
        f"- 派发 prompt:{src.rel(prompt)}",
        f"- slim(P1–P3 表面块):{src.rel(slim)}"
        + (f" · {slim.stat().st_size} B(>8KB 才可信)" if slim else "(2026-08-26 前未留存)"),
        f"- deep(P4 深核):{src.rel(deep)}",
        f"- 活体情报:{src.rel(intel)}",
    ]
    tasks = src.doc("reasoning/l4/_l4_tasks.json", "_l4_tasks.json")
    if isinstance(tasks, dict):
        entry = (tasks.get("tasks") or {}).get(code6) if isinstance(tasks.get("tasks"), dict) else None
        if entry:
            arts = entry.get("artifacts") or {}
            slim_art = arts.get("slim") or {}
            out.append(f"- 任务簿:status={entry.get('status')} · attempt={entry.get('attempt')}"
                       + (f" · slim_hash={str(slim_art.get('content_hash'))[:12]}" if arts else ""))
            rec_path = slim_art.get("path")
            if rec_path:
                from autoresearch.scan.retention import resolve_recorded_path
                real = resolve_recorded_path(rec_path)
                note = ("原路径已解析不到(2026-08-11 引擎隔离前记的裸 `context/`)"
                        if real is None else
                        ("" if str(real) == str(rec_path) else f" → 现址 `{real}`"))
                out.append(f"- 任务簿记的 slim 路径:`{rec_path}`{note}")
    stop = src.doc("_early_stop.json")
    if isinstance(stop, dict) and code6 in stop:
        s = stop[code6]
        out.append(f"- **早停**:停于 {s.get('phase')} · 停因「{s.get('reason')}」")
    ratings = src.doc("_final_ratings.json")
    if isinstance(ratings, dict) and code6 in ratings:
        out.append(f"- **终评级**:{ratings[code6]}")
    recs = src.doc("decision_records.json")
    rec = None
    if isinstance(recs, dict):
        # 真实形状:`{"records": [ {...}, ... ]}`(列表,不是按 code 的字典)
        items = recs.get("records")
        if isinstance(items, list):
            rec = next((r for r in items if _z6(r.get("code")) == code6), None)
        elif isinstance(items, dict):
            rec = items.get(code6)
    if isinstance(rec, dict):
        gates = rec.get("gate_states") or {}
        gate_txt = "、".join(f"{k}={v}" for k, v in sorted(gates.items())) or "—"
        out.append(f"- 决策记录:source={rec.get('source_rating')} · rubric={rec.get('rubric_rating')}"
                   f" · final={rec.get('final_rating')} · proposal={rec.get('proposal')}")
        out.append(f"- OW 三门:{gate_txt}(UNKNOWN = 早停卡不写三门段)")
        out.append(f"- 记录理由:{_fmt(rec.get('reason'), 160)}")
    ens = src.doc(f"_ensemble_{code6}.json")
    if isinstance(ens, dict):
        out.append(f"- 双复核:{_fmt(json.dumps(ens, ensure_ascii=False), 300)}")
    card = None
    for cand in sorted((src.run / "details").glob("*.md")) if (src.run / "details").is_dir() else []:
        txt = cand.read_text(encoding="utf-8", errors="ignore")[:400]
        if code6 in txt:
            card = cand
            break
    out.append(f"- 发布卡:{src.rel(card)}")
    return out


def _sec_e6(src: Sources, code6: str) -> list[str]:
    out = ["", "## ⑧ E6 相对 BUY 决策"]
    doc = src.doc("_relative_buy_decision.json")
    if not isinstance(doc, dict):
        return out + [f"- {ABSENT}(决策文件未留存 —— 2026-08-26 前它不在 run 目录里)"]
    buys = [b.get("code") for b in (doc.get("buys") or [])]
    row = next((c for c in (doc.get("candidates") or []) if _z6(c.get("code")) == code6), None)
    out.append(f"- 当日 mode={doc.get('mode')} · rule={doc.get('rule_version')} · "
               f"blocked={doc.get('blocked')} · BUY={buys or '—'}")
    if row is None:
        out.append("- 本票不在 E6 候选(当日未派 L4 / 不在候选池)")
        return out
    out += _kv([
        ("eligible / rank", f"{row.get('eligible')} / {row.get('rank')}"),
        ("四面", json.dumps(row.get("faces") or {}, ensure_ascii=False, sort_keys=True)),
        ("硬门", json.dumps(row.get("hard_gate") or {}, ensure_ascii=False, sort_keys=True)),
        ("决策分", row.get("relative_decision_score")),
        ("卡面评级", row.get("research_rating")),
    ])
    excl = [e for e in (doc.get("excluded") or []) if _z6(e.get("code")) == code6]
    for e in excl:
        out.append(f"- 被排除:`{e.get('reason')}` · {e.get('detail')}")
    if code6 in [_z6(b) for b in buys]:
        out.append("- ✅ **本票就是当日 BUY**")
    return out


def _sec_brief(src: Sources, code6: str) -> list[str]:
    out = ["", "## ⑨ 报告口径(brief)"]
    p = src.run / "brief.md"
    if not p.is_file():
        return out + [f"- {ABSENT}"]
    hits = [ln.strip() for ln in p.read_text(encoding="utf-8").splitlines()
            if code6 in ln or "relative BUY" in ln]
    return out + ([f"- {ln}" for ln in hits] if hits else ["- brief 未点名本票"])


def _sec_outcome(src: Sources, code6: str) -> list[str]:
    out = ["", "## ⑩ 结果(事后)"]
    # 结果落 `_ledger/outcome/<run_id>.json`(**不在 run 目录内**)—— run 目录有「发布后
    # 不再变」的 MANIFEST 不变量,事后往里写会让每个 run 的 `verify` 永远报一条 `extra`。
    from autoresearch.scan.outcome import outcome_path
    doc: object | None = None
    p = outcome_path(src.run.name, src.run.parent)
    if p.is_file():
        try:
            doc = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            doc = None
    if not isinstance(doc, dict):
        return out + [f"- {ABSENT}(结果账本尚未回填 —— "
                      f"`python -m autoresearch.scan.outcome fill`)"]
    out.append(f"- 口径:主尺 {doc.get('ruler')} · T+1 {doc.get('t1')} → T+2 {doc.get('t2')}"
               f" · 决策 mode={doc.get('decision_mode') or '?'}"
               + ("(读自共享 staging,未必是本 run 那份)"
                  if doc.get("read_from_shared_staging") else ""))
    row = (doc.get("rows") or {}).get(code6)
    if not isinstance(row, dict):
        return out + ["- 本票不在结果账本(当日未被评级/未入 finalist)"]
    out += _kv([
        ("角色", row.get("role")),
        ("T+1 收 / 当日涨幅", f"{row.get('t1_close')} / {row.get('t1_pct_chg')}%"),
        ("T+1 收盘区间位置", row.get("t1_pos_in_range")),
        ("执行线", f"exec_ok={row.get('exec_ok')}(追强否决口径)"),
        ("T+2 开", row.get("t2_open")),
        ("主尺 gap_c1_o2", f"{row.get('gap_c1_o2')}"),
        ("相对全市场 / 行业", f"{row.get('rel_gap_market')} / {row.get('rel_gap_sector')}"),
        ("fwd_5 / fwd_10(参考尺)", f"{row.get('fwd_5_oc')} / {row.get('fwd_10_oc')}"),
    ])
    return out


def render(run_dir: Path | str, code: str) -> str:
    src = Sources(Path(run_dir))
    code6 = _z6(code)
    head = [f"# 推荐链路 — {code6} @ run `{Path(run_dir).name}`(数据日 {src.analysis_date or '?'})",
            "",
            "_确定性生成(零 LLM);每段的「缺席」都是事实,不是渲染失败。仅供研究,非投资建议。_"]
    body: list[str] = []
    for fn in (_sec_identity, _sec_passport, _sec_l1, _sec_l2, _sec_pass1,
               _sec_l3, _sec_l4, _sec_e6, _sec_brief, _sec_outcome):
        try:
            body += fn(src, code6)
        except Exception as exc:  # noqa: BLE001 — 一段读坏不该让整张视图消失
            body += ["", f"## {fn.__name__} 渲染失败:{type(exc).__name__}: {exc}"]
    # 「读了共享 staging」这条警示只能**最后**判:`used_shared` 是各段读盘时才置位的,
    # 放进 ① 会永远为假(① 跑在所有读盘之前)——写在开头的探针读不到还没发生的事实,
    # 与「brief 读了 relative_buy 的半成品」同一族的时序坑。
    if src.used_shared:
        body += ["", "---", "",
                 "⚠️ **本视图有片段读自共享 staging**(`context_*/scan/<date>/`)—— 同数据日"
                 "重跑会原地覆盖它,那些片段**未必是本 run 当时那份**"
                 "(2026-08-26 之前的 run 全部如此)。"]
    return "\n".join(head + body) + "\n"


def _resolve_run(value: str) -> Path:
    p = Path(value)
    return p if p.exists() else ws.reports_root() / "scan" / value


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="推荐链路视图(零 LLM)")
    ap.add_argument("run", help="run 目录或 run_id(如 20260825_2149)")
    ap.add_argument("code", help="6 位股票代码")
    ap.add_argument("--out", default=None, help="落盘路径(默认打到 stdout)")
    args = ap.parse_args(argv)
    run_dir = _resolve_run(args.run)
    if not run_dir.is_dir():
        print(f"run 目录不存在:{run_dir}")
        return 2
    md = render(run_dir, args.code)
    if args.out:
        Path(args.out).write_text(md, encoding="utf-8")
        print(f"[chain_view] → {args.out}")
    else:
        print(md)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
