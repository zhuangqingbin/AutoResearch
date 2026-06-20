# scan-market v2 · Phase 3 — 整合(L5)+ A_pipeline 溯源 + 全量重命名 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: superpowers:subagent-driven-development / executing-plans. Steps use `- [ ]`.

**Goal:** L5 整合阶段:`assemble_scan.py` 产出三段式 summary(漏斗数量 → 各卡点原因+股票概览 → L4 汇总投资建议),并把各阶段产物发布到 `<HHMM>_detail/A_pipeline/`;全量把 L 编号/命名统一到 选集/召回/粗排/精排/研究/整合。

**Architecture:** `assemble_scan.py` 读 staging 的漏斗产物(meta/L1_recall/L2_keep/finalists/details)→ 三段 summary + parse_rating buy-list;`_publish_pipeline` 复制阶段产物到 A_pipeline/。命名为纯文档 + 字符串改动。

**依赖:** P1+P2 完成。**Spec:** §7 整合、§10 工程。

**Phase 3 完成 = 可工作可测:** `assemble_scan.py --selftest` 绿(三段 + A_pipeline 发布 + 缺卡降级);全仓 grep 无旧 `L3a/L3b` 残留;端到端 `screen_market → (L2/L3/L4 skill) → assemble_scan` 出完整报告。

---

## 文件结构

| 文件 | 责任 | 动作 |
|---|---|---|
| `scripts/assemble_scan.py` | L5 三段 summary + A_pipeline 发布 | 改(扩 build_summary + 新 _publish_pipeline) |
| `.claude/skills/scan-market/{SKILL,screening-playbook}.md` | 命名/编号统一 | 改 |
| `.claude/skills/analyze-ticker-lite/{SKILL,lite-playbook}.md` | "scan L3b"→"scan L4 研究" | 改 |
| `docs/specs/2026-06-20-scan-market*.md` | 编号注解一致 | 改 |

---

## Task 1: assemble_scan — 读漏斗产物 + 三段 summary

**Files:** Modify `scripts/assemble_scan.py`

- [ ] **Step 1: 改 `build_summary` 为三段**(失败 selftest 先断言三段标题 + 漏斗数 + buy-list)

扩 `_selftest`:在 staging 造 `meta.json`(universe/recall_n/L2/L3 计数)、`L1_recall_top1000.csv`、`L2_coarse_keep200.csv`、`finalists.csv`(含 thesis/risk/catalyst)、`details/*.md`(决策卡);断言 summary 含:
- `## 1. 漏斗` + 数字链(如 `5400`→`1000`→`200`→`30`);
- `## 2. 各阶段` + 每段(选集/召回/粗排/精排)标题 + 代表股;
- `## 3. 投资建议` + buy-list 表 + 评级(parse_rating)。

- [ ] **Step 2: 实现三段**(关键新逻辑)

```python
def build_summary(scan_dir, analysis_date, hhmm, compact) -> str:
    meta = _load_json(scan_dir / "meta.json")
    recall = _read_csv_df(scan_dir / "L1_recall_top1000.csv")
    keep = _read_csv_df(scan_dir / "L2_coarse_keep200.csv")
    finals = _read_csv(scan_dir / "finalists.csv")
    rows = [_finalist_row(scan_dir, fr) for fr in finals]; rows.sort(key=_sortkey)

    out = [f"# A股扫描 v2 · {analysis_date} {hhmm[:2]}:{hhmm[2:]}\n",
           "_六段漏斗:选集→召回→粗排→精排→研究→整合。**仅供研究,非投资建议。**_\n"]

    # ── 1. 漏斗数量 ──
    out += ["## 1. 漏斗", "| 阶段 | 名称 | 出量 | 卡点标准 |", "|---|---|---:|---|",
            f"| L0 | 选集 | {meta.get('universe','?')} | 硬门(剔ST/退/停牌/次新, 市值地板, 含北交所) |",
            f"| L1 | 召回 | {meta.get('recall_n', len(recall))} | 轻门 + 行业条件化复合分(T+1 IC 校准) top |",
            f"| L2 | 粗排 | {meta.get('l2_keep', len(keep))} | AI 资深投资师 keep/cut(信号共振/排陷阱) |",
            f"| L3 | 精排 | {meta.get('l3_finalists', len(finals))} | 增量真证据 + 论点/红队(确信度−脆弱度) |",
            f"| L4 | 研究 | {len(rows)} 卡 | analyze-ticker-lite 决策卡 |", ""]

    # ── 2. 各阶段原因 + 股票概览 ──
    out += ["## 2. 各阶段卡点 & 股票概览"]
    out += _stage_overview("召回(L1)", recall, "composite",
                           "复合分 top;快因子(动量/资金结构/技术)主导排序,慢因子带下游判断。")
    out += _stage_overview("粗排(L2)", keep, "l2_score" if "l2_score" in keep.columns else None,
                           "资深投资师粗筛,剔信号矛盾/明显陷阱。")
    # 精排:finalists 带 thesis,逐条概览
    out += ["", "**精排(L3)入选(带论点/风险/催化)**:"]
    for fr in finals[:12]:
        out.append(f"- **{fr.get('name','')}({fr.get('code','')})** · {fr.get('sector','')} — "
                   f"多头:{_strip(fr.get('thesis',''))};风险:{_strip(fr.get('risk',''))};"
                   f"催化:{_strip(fr.get('catalyst',''))}")
    out.append("")

    # ── 3. L4 汇总投资建议 ──
    out += [f"## 3. 投资建议(buy-list, {len(rows)} 只)\n",
            "| # | 代码 | 名称 | 板块 | 评级 | 目标(EV) | R:R | 提案 | 置信度 | 论点一句 |",
            "|---|---|---|---|---|---|---|---|---|---|"]
    for i, r in enumerate(rows, 1):
        out.append(f"| {i} | {r.get('code','')} | {r.get('name','')} | {r.get('sector') or r.get('industry','')} "
                   f"| **{r.get('rating','—')}** | {r.get('target','—')} | {r.get('rr','—')} | {r.get('proposal','—')} "
                   f"| {r.get('conf','—')} | {_strip(r.get('thesis') or r.get('triage_reason',''))} |")
    out += ["", "### 组合视角",
            _portfolio_note(rows, finals),
            "", "## 诚实局限",
            "- 召回为启发式 + T+1 单 horizon IC 校准,随 regime 漂移;L2/L3 为 Claude 推理产出。",
            "- 业绩/龙虎榜/预告有披露滞后;无权限端点降级标注。",
            "- A股涨跌停/停牌使名义止损未必可执行(见各决策卡)。",
            f"\n_明细 + 漏斗溯源:`reports/scan/{compact}/{hhmm}_detail/`(决策卡 + A_pipeline/)_"]
    return "\n".join(out)


def _stage_overview(label, df, score_col, reason) -> list[str]:
    if df is None or not len(df):
        return [f"", f"**{label}**:_无 staging,跳过_"]
    top_inds = df["industry"].value_counts().head(5) if "industry" in df.columns else {}
    reps = ", ".join(df.head(6)["name"].astype(str)) if "name" in df.columns else ""
    inds = "、".join(f"{k}({v})" for k, v in top_inds.items())
    return ["", f"**{label}** — {reason}",
            f"- 行业分布 top5:{inds}", f"- 代表股:{reps}"]


def _portfolio_note(rows, finals) -> str:
    secs = {}
    for r in rows:
        secs[r.get("sector") or r.get("industry", "?")] = secs.get(r.get("sector") or r.get("industry", "?"), 0) + 1
    top = "、".join(f"{k}×{v}" for k, v in sorted(secs.items(), key=lambda x: -x[1])[:5])
    buys = sum(1 for r in rows if r.get("rating") in ("Buy", "Overweight"))
    return (f"买入/超配 **{buys}** 只;板块集中度:{top}。"
            "注意单板块过度集中的相关性风险;按评级×置信度分配仓位,留催化日历做节奏。")
```
(`_load_json`/`_read_csv_df`/`_read_csv` 小 helper;`_finalist_row`/`_sortkey`/`_strip` 沿用现有。)

- [ ] **Step 3/4: selftest + ruff**

Run: `uv run --no-sync python scripts/assemble_scan.py --selftest 2>&1 | tail -1`
Expected: `SELFTEST ✅`(三段 + buy-list + 漏斗)。

---

## Task 2: assemble_scan — A_pipeline 发布

**Files:** Modify `scripts/assemble_scan.py`(`_publish_pipeline`,`run` 调用)

- [ ] **Step 1: 实现 + selftest 断言文件落位**

```python
def _publish_pipeline(scan_dir: Path, detail_out: Path) -> int:
    """把各阶段 staging 产物发布到 <HHMM>_detail/A_pipeline/(漏斗溯源)。"""
    import shutil
    pdir = detail_out / "A_pipeline"; pdir.mkdir(parents=True, exist_ok=True)
    mapping = {
        "meta.json": "L0_universe_meta.json",
        "L1_recall_top1000.csv": "L1_recall_top1000.csv",
        "L2_coarse_keep200.csv": "L2_coarse_keep200.csv",
        "finalists.csv": "L3_fine_finalists.csv",
    }
    n = 0
    for src, dst in mapping.items():
        p = scan_dir / src
        if p.exists(): shutil.copy2(p, pdir / dst); n += 1
    # weights.json(校准产物,provenance)
    wp = Path("context/factor_lab/weights.json")
    if wp.exists(): shutil.copy2(wp, pdir / "L1_weights.json"); n += 1
    # funnel.md(人读)
    (pdir / "funnel.md").write_text(_funnel_md(scan_dir), encoding="utf-8"); n += 1
    return n
```
`run()`:`detail_out` 建好后调 `_publish_pipeline(scan_dir, detail_out)`,print 发布数。`_funnel_md` 复用 build_summary 的漏斗表片段。

- [ ] **Step 2: selftest 扩**:断言 `A_pipeline/L1_recall_top1000.csv`、`L3_fine_finalists.csv`、`funnel.md` 存在。
- [ ] **Step 3/4: selftest + ruff** 绿。

---

## Task 3: 全量重命名(选集/召回/粗排/精排/研究/整合)

**Files:** `.claude/skills/scan-market/{SKILL,screening-playbook}.md`、`.claude/skills/analyze-ticker-lite/{SKILL,lite-playbook}.md`、`docs/specs/2026-06-20-scan-market*.md`

- [ ] **Step 1: 建映射并逐文件改**
  - `L3a`→`L3(精排)`、`L3b`→`L4(研究)`、旧 `L4 综合`→`L5(整合)`;引入 `L0 选集 / L1 召回 / L2 粗排` 名。
  - lite 的"scan-market L3b 调用"→"scan-market L4 研究阶段调用"。

- [ ] **Step 2: grep 确认无残留**

Run: `grep -rnE "L3a|L3b|旧 L4|scan_summary" .claude/skills/ docs/specs/2026-06-20-scan-market-v2-design.md`
Expected: 空(或仅 v1 历史 spec 的说明性引用)。

- [ ] **Step 3: 重读 SKILL/playbook 一致性**(L0–L5 命名、命令、staging 路径、产物路径全一致)。

---

## Task 4: Phase 3 集成验收

- [ ] **Step 1:** `assemble_scan --selftest` ✅、ruff ✅。
- [ ] **Step 2:** grep 无旧编号残留。
- [ ] **Step 3:** 端到端(P1+P2+P3 合):见全局验收(运行 README)。

## Phase 3 Self-Review(vs spec §7/§10)
- ✅ 三段 summary(漏斗数量 / 各卡点原因+概览 / L4 汇总建议)(Task 1)。
- ✅ A_pipeline 发布 + weights provenance + funnel.md(Task 2)。
- ✅ 全量重命名 L0–L5(Task 3)。
- ✅ parse_rating buy-list 保留(Task 1)。
