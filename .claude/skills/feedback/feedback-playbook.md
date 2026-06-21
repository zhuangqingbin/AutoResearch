# feedback playbook — 5 步把反馈记住并蒸馏成经验

> **本文 + `scripts/feedback_store.py` 自足,无需 `docs/specs/`。** 真值源:`feedback_store.py`(四个 jsonl store + 注回渲染);本文是 5 步操作手册。

## 5 步

**1. 定位被评报告**
用户没指明 → 取本 session 最近一份(`reports/scan/<YYYYMMDD_HHMM>/summary.md` 或 `reports/analyze/<YYYYMMDD_HHMM>/<名称|TICKER>.md` 或 retro 报告)。记下 `report` 相对路径。

**2. 判 verdict + scope**
- `verdict` ∈ `wrong_rating`(评级看反/错)| `missed`(漏选了涨的)| `false_positive`(选了跌的)| `good_call`(认可,要保持)| `process`(流程/格式类)。
- `scope`:这条反馈管多大范围?`("global","*")`(打分/流程通则)| `("sector","周期资源")` | `("industry","电子")`(申万一级)| `("ticker","600519")`。**能泛化就往上提**——单票教训若本质是通则(如"获利盘满=见顶"),记 `global`,威力最大。

**3. 蒸馏 root_cause + corrective_rule**
- `root_cause`:为什么会错/为什么是好判断(落到因子/证据,≤30 字)。
- `corrective_rule`:**下次该怎么做**的可执行规则(≤40 字,能直接进 subagent prompt)。例:"winner_rate>90 视为抛压/见顶,不计入'筹码健康'加分"。

**4. 落 feedback(情节)**

```bash
uv run --no-sync python - <<'PY'
import autoresearch.learning.feedback_store as fs
fb = fs.record_feedback(
    skill="scan-market",                       # 或 analyze-ticker / macro-research
    scope=("global", "*"),                     # 见第 2 步
    report="reports/scan/20260619_1553/summary.md",
    note="""<用户原话,可多行>""",
    verdict="wrong_rating",
    root_cause="""<≤30 字>""",
    corrective_rule="""<≤40 字,可执行>""",
)
print("feedback:", fb["id"])
PY
```
> 用 `<<'PY'` heredoc + 三引号串,free-form 中文/引号/换行都安全。

**5. 决定是否升语义经验(lesson)**
**可泛化、会反复用** → 升;**纯一次性事实订正** → 只留 feedback、不升。升的话:

```bash
uv run --no-sync python - <<'PY'
import autoresearch.learning.feedback_store as fs
lsn = fs.upsert_lesson(
    "winner_rate_topping",                     # 稳定 slug:同一教训反复反馈会自动强化(count++/conf↑)
    ("global", "*"),
    rule="""winner_rate>90=抛压/见顶,非筹码健康;低 winner_rate=有上行空间。""",
    evidence=["fb_20260619_001", "factor_lab T+1 IC -42bps"],
    confidence=0.6,
)
fs.record_feedback(skill="scan-market", scope=("global","*"),
                   report="reports/scan/20260619_1553/summary.md",
                   note="(已升经验)", verdict="wrong_rating",
                   root_cause="", corrective_rule="", lesson_id=lsn["id"])  # 回填 lesson_id
print("lesson:", lsn["id"], "conf", lsn["confidence"], "x", lsn["reinforce_count"])
PY
```

**回执用户**:记了哪条 feedback、是否升成经验(及 slug/confidence)、下次哪个 skill/阶段会自动用上(scan 的 L2/L3 校准块 / 报告骨架)。

## 注回怎么生效(无需手动)
- scan-market 构造 L2/L3 subagent prompt 前,会调 `fs.render_calibration_block(本批申万行业 scopes)` —— 命中经验**叠加在 IC 基线之上**;store 空时逐字回退基线(老路径不破)。
- `autoresearch.scan.assemble` 报告骨架对覆盖标的浮出"📌 经验 / 未决反馈"。
- 验证某经验会被用上:`uv run --no-sync python -c "import autoresearch.learning.feedback_store as fs;print(fs.render_calibration_block([('global','*')]))"`。

## 经验卫生
- **slug 要稳定**:同一教训用同一 slug,反复反馈→自动强化(confidence 升、reinforce_count++),别每次新建。
- **能 global 就 global**:通则放 global 威力最大;只有真的行业/个股特异才下沉。
- 退休(regime 翻转/不再成立)→ `fs.retire_lesson(slug)`(或交给 retro 的自动退休)。

---
> 设计沿革(可选背景,删除不影响运行):`docs/specs/2026-06-20-closed-loop-learning-design.md` §3.1(知识库底座)/ §3.3(注回机制)。
