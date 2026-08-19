# feedback playbook — 5 步把反馈记住并蒸馏成经验

> **路径约定**:`$CTX`/`$RPT` = 本引擎工作区根(Claude→`context_claude`/`reports_claude`,Codex→`context_codex`/`reports_codex`;shell 里 `CTX=context_${AUTORESEARCH_ENGINE:-claude}`,`RPT=reports_${AUTORESEARCH_ENGINE:-claude}`)。数据湖 `lake/` 两引擎共享。Read/Write 工具调用时把 `$CTX`/`$RPT` 代入具体目录名。

> **本文 + `autoresearch/learning/feedback_store.py` 自足,无需 `docs/specs/`。** 真值源:`feedback_store.py`(四个 jsonl store + 注回渲染);本文是 5 步操作手册。

## 5 步

**1. 定位被评报告**
用户没指明 → 取本 session 最近一份(`$RPT/scan/<YYYYMMDD_HHMM>/summary.md` 或 `$RPT/analyze/<YYYYMMDD_HHMM>/<名称|TICKER>.md` 或 retro 报告)。记下 `report` 相对路径。

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
    skill="scan-market",                       # 或 stock-research / macro-research
    scope=("global", "*"),                     # 见第 2 步
    report="reports_claude/scan/20260619_1553/summary.md",
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

- **已知反复教训**(同一 slug 曾升过)→ 直接用**同一稳定 slug** `upsert_lesson`,自动强化(count++/conf↑),跳过下面的裁决。
- **新洞见**(要起新 slug)→ **先跑 M2 写入裁决**,防和已有条重复/矛盾:`similar_lessons` 结构化召回相似旧条(零 embedding)→ 你判 op → `adjudicate` 执行。

```bash
uv run --no-sync python - <<'PY'
import autoresearch.learning.feedback_store as fs
cand = dict(slug="winner_rate_topping", scope=("global","*"),
            rule="""winner_rate>90=抛压/见顶,非筹码健康;低 winner_rate=有上行空间。""",
            evidence=["fb_20260619_001", "factor_lab T+1 IC -42bps"], confidence=0.6)

sim = fs.similar_lessons(cand["rule"], cand["scope"])          # 召回相似旧条(看 rule/id/conf)
for s in sim[:3]:
    print("  近似:", s["id"], "|", s["rule"][:40])
# ↑ 你据此判 op:无强相似→ADD;精化某条→UPDATE(改写并保 id/MTM);与某条矛盾→DELETE(新条取代旧条);已表达→NOOP
lsn = fs.adjudicate("ADD", cand)                               # target_id=... 传给 UPDATE/DELETE/NOOP

fs.record_feedback(skill="scan-market", scope=("global","*"),
                   report="reports_claude/scan/20260619_1553/summary.md",
                   note="(已升经验)", verdict="wrong_rating",
                   root_cause="", corrective_rule="", lesson_id=lsn["id"])  # 回填 lesson_id
print("lesson:", lsn["id"], "conf", lsn["confidence"], "x", lsn["reinforce_count"])
PY
```
> 拿不准就用 ADD(新条)——裁决 UPDATE/DELETE 会改/失效旧条,只在**确有**近似/矛盾条时用。裁决全落 changelog 可回滚。

- **毕业提名(第五出口,不是 `adjudicate` 的 op)**:同一条 lesson 反复强化 + MTM **support 明显压过
refute**(如 `reinforce_count` 持续走高、`mtm.support≥3` 且 support>refute)→ 这条知识已从
『待验证的动态经验』长成『确定的固定流程』,该进 playbook 正文了。**但本会话不施工,只起草提名**:

```bash
uv run --no-sync python - <<'PY'
import autoresearch.learning.feedback_store as fs
pr = fs.add_graduation_nomination("winner_rate_topping",
                                  target_hint=".claude/skills/feedback/feedback-playbook.md")
print("毕业提名:", pr["id"], pr["summary"])
PY
```
提名落 `proposals.jsonl`(`kind=graduation`,同 slug 已有 open 提名则原样返回、不堆重复),进现有
待裁决看板。人批通过后,由**用户在显式发起的开发会话**里把 rule 落进目标 playbook 正文,并在同一
会话 `fs.retire_lesson(slug)` 收尾。**提名期间该经验保持 active、继续注入**——不许出现「既不在
playbook 也不在注入名单」的空窗。

> **复盘不动刀**:复盘/反馈流程一律不得编辑 .claude/ 与 CLAUDE.md/AGENTS.md;skill/prompt/agent/workflow 文本只在用户显式发起的开发会话中修改。(2026-08-13 用户裁定,spec `docs/specs/2026-08-13-retro-skill-selfmodify-removal-design.md`)

**回执用户**:记了哪条 feedback、是否升成经验(及 slug/confidence)、下次哪个 skill/阶段会自动用上(scan 的 L2/L3 校准块 / 报告骨架)。

## 注回怎么生效(无需手动)
- scan-market 构造 L2/L3 subagent prompt 前,会调 `fs.render_calibration_block(本批申万行业 scopes)` —— 命中经验**叠加在 IC 基线之上**;store 空时逐字回退基线(老路径不破)。
- `autoresearch.scan.assemble` 报告骨架对覆盖标的浮出"📌 经验 / 未决反馈"。
- 验证某经验会被用上:`uv run --no-sync python -c "import autoresearch.learning.feedback_store as fs;print(fs.render_calibration_block([('global','*')]))"`。

## 经验卫生
- **slug 要稳定**:同一教训用同一 slug,反复反馈→自动强化(confidence 升、reinforce_count++),别每次新建。
- **能 global 就 global**:通则放 global 威力最大;只有真的行业/个股特异才下沉。
- 退休(regime 翻转/不再成立)→ `fs.retire_lesson(slug)`(或交给 retro 的自动退休)。
- **毕业退休**(区别于上一条的失效退休):规则依然成立,只是已进了 playbook 正文,`retire_lesson` 只是清理动态 store 的注入名额,不代表这条经验错了——**只在人批毕业提名、并把 rule 落进 playbook 的那个开发会话内执行**,见第 5 步「毕业提名」。

---
> 设计沿革(可选背景,删除不影响运行):`docs/specs/2026-06-20-closed-loop-learning-design.md` §3.1(知识库底座)/ §3.3(注回机制)。
