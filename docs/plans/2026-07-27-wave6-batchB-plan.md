# Wave6 批 B 实施计划(小刀 + intel 契约 + 修缮包 + 退役清理)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把 Wave6 spec §3.1/§4/§5 里「无触发条件、可立即动」的 13 件落地——情报可信度硬契约、三把零风险省钱刀、六项契约修缮、两项退役清理——每件独立可回滚、各带会变红的探针。

**Architecture:** 全部是既有管道上的局部改动,不新增子系统。四类改法:①`.claude/agents/*.md` + `self_review.py` lint 成对改(指令侧提要求、机检侧对账,单改一侧=FN-1);②`.claude/workflows/*.js` 的 `agent()` opts 微调(纯壳降档、同档早止);③`autoresearch/` 内既有函数改写(估算表纠偏、阈值单一事实源、切面块);④删模块 + 改文档锚。

**Tech Stack:** Python 3.12 / pandas / pytest(`uv run --no-sync python -m pytest -q`)· Claude Code workflow JS(`.claude/workflows/*.js`,无文件系统访问,靠 `args` 注入)· agent def markdown(frontmatter model/effort)。

## Global Constraints

**基线**:分支 `wave6-batch-b`(已建),起点 commit `2d99b74`,全量 **1602 passed**(93s,exit 0)。每个 task 结束时全量必须仍绿且计数 ≥1602。

**spec**:`docs/specs/2026-07-27-wave6-unified-roadmap-design.md`(§0.2 红线表逐条生效)。

**红线(任何 task 不得触碰)**:
- 不放松买入门:`assemble.py` 的 ≥OW 唯一门槛、OW 三门判据一字不动。
- 不动早停机制与「早停只向下」;不动 L4 拒绝侧逻辑(`ic_rating_t2 +0.318` 是全系统最好信号)。
- 不动 L2 打分/权重/召回配额(本批零因子改动)。
- 不改任何评级判据。lint 只观测不改评级。
- 新 lint 一律 **warn 起步**,不得直接 fail(会挡发布);升 fail 需 ≥3 跑无误报,属后续批次。

**纪律(踩过的坑,逐条实测过)**:
1. **变异探针**:每写一个守卫,先问「把被守的内容删掉/改坏,这个测试会红吗」——不会红就是假绿灯,重写。workflow js 的 `node --check` 对本仓文件零鉴别力,已有 `tests/test_workflow_js_syntax.py` 的 AsyncFunction 探针代劳。
2. **契约锚必须落在承重行**:锚串若同时出现在上方注释里,删掉真代码测试照绿(`test_scan_market_workflow_pinned_roster_log` 的血)。
3. **agent def 改动下 session 才生效**(会话启动装载)——本批所有 `.claude/agents/*.md` 改动的**活体**验收只能在 07-28 那次扫描,本批只验静态契约测试。
4. **SKILL.md / playbook 会被外部改**:每次编辑前**重读**该文件,不要凭本计划里的行号盲改。
5. **`pytest | tail` 吞退出码**:跑全量用 `set -o pipefail` 或重定向到文件再看 tail。
6. **写湖一律剥 fields**(窄表毒化);本批 Q3 若碰 lake 缓存同此纪律。
7. **降级必记账**:B 级降级要留 `_degraded` 痕,不得静默。

**测试命令**:
```bash
uv run --no-sync python -m pytest -q                       # 全量(93s,基线 1602)
uv run --no-sync python -m pytest tests/<path> -q           # 单文件
uv run --no-sync ruff check autoresearch tests              # lint 净
```

**提交**:每个 task 一个 commit,信息用中文祈使句 + 一行「为什么」。commit 尾行:
```
Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>
```

**本批不做(有意排除,勿顺手做)**:
- **T7 prewarm**:已诊断=**无 bug**。`launchctl print` 显示 plist 已装载但 `runs = 0`、`last exit code = (never exited)`,日志文件不存在;plist 安装于 07-25(周六)12:55,`StartCalendarInterval` = 周一~周五 19:30 → **首个合法档期是 2026-07-27(今晚)19:30**。07-25 那 959s 是 run 窗口内的手工 kickstart,不是排程失败。动作改为批 A 的运维验证:今晚 19:30 后查 `/tmp/scan-prewarm.log` 与 `context/scan/<date>/_prewarm.json`。**不要改 plist、不要改 prewarm.py。**
- **`scan/progress.py` 退役**:spec §5 E1 明写「直播首验收(R3)通过后删」,R3 = 07-28 那次扫描,尚未发生。本批只删 `trace/telemetry.py`。
- **三大指数当日涨跌**(需新端点 `index_daily`):Q3 只做零新端点的部分(见 Task 12),指数当日涨跌另立提案。
- L3 降档/拆分、intel 降默认、双复核降档、SKILL.md 大瘦身:全部挂读数触发(spec §3.3),等 07-28 的 CP7 首读。

---

## File Structure

| 文件 | 责任 | 涉及 task |
|---|---|---|
| `.claude/agents/l4-intel.md` | 情报员人设 + 六面契约 + **新增**:URL 要求、查询数自报口径、行情数字禁区 | 1,2,3 |
| `.claude/agents/l4-card.md` | 决策卡人设 + 卡机器契约 + **新增**:独立初判标签、conviction 标度 | 10,11 |
| `autoresearch/learning/self_review.py` | 全部 lint(gate_fires.csv 生产者)+ **新增** `intel_query_cap_lint` | 1,3 |
| `autoresearch/learning/process_score.py` | 6 项确定性 checklist + slim 阈值消费方 | 9,10 |
| `autoresearch/scan/agents/l4_card.py` | slim 阈值真值源、prompt 组装、`_slim_defect` | 9 |
| `autoresearch/scan/assemble.py` | L5 组装;`_stage_token_estimate` 纠偏;run_health 时序 | 6,11 |
| `autoresearch/trace/usage_harvest.py` | 真计量;**新增**分模型计价列 + `--transcripts` 追溯模式 | 7 |
| `autoresearch/trace/telemetry.py` | **删除**(OTEL 路退役) | 8 |
| `autoresearch/scan/market.py` | market_pack 两入口;**新增** `today_slice` 块 | 12 |
| `autoresearch/scan/universe.py` | `L1_scored_full.csv` 投影列表(补 `pct_1d`) | 12 |
| `autoresearch/scan/l3_select.py` | L3 证据预取(空 news 不落盘) | 11 |
| `.claude/workflows/scan-market.js` | `bash()`/`gate()` 壳降档 | 4 |
| `.claude/workflows/l4-stock.js` | `ens-dump` 壳降档 + ensemble 同档早止 | 4,5 |
| `.claude/skills/scan-market/SKILL.md` `STAGES.md` | 文档面:OTEL 段改写、估算表叙事、收尾合并 | 13 |
| `context/knowledge/proposals.jsonl` | 关账(经 `set_proposal_status`) | 14 |

---

## Task 1: intel 查询限频对账 lint(`intel_query_cap_lint`)

**为什么先做它**:`l4-intel.md` 要求「六面全查(≤15 条)」,声明行也**已经自报**了查询数——但**全仓没有任何消费者读它**。07-24 实测 11 只票自报 `网查 18/18/17/15/20/26/23/16/17/21/25 条`,**10/11 超限**,最高 26 条(cap 的 173%),无人察觉。这是本仓 FN-1 家族的教科书案例:生产者写了数字,没有消费者。

**Files:**
- Modify: `autoresearch/learning/self_review.py`(模块级新函数 + `product_shape_lint` 内新增 probe 10)
- Test: `tests/learning/test_self_review.py`(新增 4 个测试)

**⚠️ 接线事实(侦察已确认,勿按直觉写)**:
- `review(ctx: dict) -> dict` 吃的是 **ctx 字典**(finalists/n_cards_*/summary_text/lessons),**不是 scan_dir** —— 所有读盘类探针都在 `product_shape_lint(scan_dir, date_str) -> list[dict]` 里(九探针,`intel零URL` 是其中的 probe 6)。
- `product_shape_lint` **已经**接线在 `assemble.py:869-873`,结果合并进 `res["failures"]` 后由 `dump_gate_fires` 落 `gate_fires.csv`。**所以新探针不需要任何新接线**——加进 `product_shape_lint` 即自动上表。
- `product_shape_lint` 内部有自己的 `add(check, sev, detail, code=None)` 闭包(:266-267),往局部 `out` 追加。
- severity 只能是 `warn`/`info`(该函数契约);`res["n_fail"]`/`res["ok"]` 在 :858 就定格了,任何探针升 fail 都要另改 assemble,本批不做。

**Interfaces:**
- Produces: `intel_query_cap_lint(scan_dir, cap: int = 15) -> list[dict]` —— 返回 `[{"code": "600236", "claimed": 20, "cap": 15}, ...]`,`claimed=None` 表示稿里没自报;`cap` 由 `user_config_echo.json` 的 `l4_intel.max_queries` 提供,缺则 15。

- [ ] **Step 1: 写失败测试**

在 `tests/learning/test_self_review.py` 末尾追加:

```python
def test_intel_query_cap_lint_flags_over_cap(tmp_path):
    """声明行自报「网查 N 条」超过 cap → 逐码 flag。

    07-24 实测 11 稿 10 只超限(最高 26/15),而全仓无人读这个数 —— 生产者写了、
    没有消费者的 FN-1 家族。
    """
    from autoresearch.learning.self_review import intel_query_cap_lint

    d = tmp_path / "context" / "scan" / "2026-07-24"
    d.mkdir(parents=True)
    (d / "_l4_intel_600236.md").write_text(
        "## 声明行\n网查 20 条 ｜ 六面覆盖:公告=有料 ｜ as-of ≤ 2026-07-24\n", encoding="utf-8")
    (d / "_l4_intel_600018.md").write_text(
        "## 声明行\n网查 15 条 ｜ 六面覆盖:公告=无 ｜ as-of ≤ 2026-07-24\n", encoding="utf-8")

    hits = intel_query_cap_lint(d, cap=15)

    assert [h["code"] for h in hits] == ["600236"], "只有超限的才该 flag(=15 不算超)"
    assert hits[0]["claimed"] == 20
    assert hits[0]["cap"] == 15


def test_intel_query_cap_lint_missing_declaration_is_flagged(tmp_path):
    """声明行没有「网查 N 条」= 无法对账,必须显式 flag 而不是当作合规。

    「文件不存在/字段缺失是弱证据」——不得以缺推断零。
    """
    from autoresearch.learning.self_review import intel_query_cap_lint

    d = tmp_path / "context" / "scan" / "2026-07-24"
    d.mkdir(parents=True)
    (d / "_l4_intel_600236.md").write_text("## 声明行\n六面覆盖:公告=有料\n", encoding="utf-8")

    hits = intel_query_cap_lint(d, cap=15)

    assert len(hits) == 1
    assert hits[0]["claimed"] is None, "未自报应记 None,而非静默通过"


def test_intel_query_cap_lint_no_intel_files_is_empty(tmp_path):
    """intel 未启用/无稿 → 空列表(presence-gated,不造告警)。"""
    from autoresearch.learning.self_review import intel_query_cap_lint

    d = tmp_path / "context" / "scan" / "2026-07-24"
    d.mkdir(parents=True)

    assert intel_query_cap_lint(d, cap=15) == []
```

- [ ] **Step 2: 跑测试确认失败**

```bash
uv run --no-sync python -m pytest tests/learning/test_self_review.py -k intel_query_cap -q
```
Expected: FAIL — `ImportError: cannot import name 'intel_query_cap_lint'`

- [ ] **Step 3: 实现 lint 函数**

在 `autoresearch/learning/self_review.py` 模块级(与 `card_contract_lint` 同层)新增:

```python
_INTEL_QUERY_RE = re.compile(r"网查\s*(\d+)\s*条")


def intel_query_cap_lint(scan_dir: Path | str, cap: int = 15) -> list[dict]:
    """情报稿自报查询数 vs 配置 cap 对账(warn 级素材)。

    `l4-intel` 的声明行本来就写「网查 N 条」,但全仓此前**没有任何消费者**读它 ——
    2026-07-24 实测 11 稿里 10 稿超限(18/18/17/20/26/23/16/17/21/25 vs cap 15),
    限频形同虚设而无人察觉(pr_20260714_007)。

    返回逐码 dict:`{"code", "claimed", "cap"}`;`claimed=None` = 稿里根本没自报
    (**不当作合规**——缺字段是弱证据,不得以缺推断零)。无 intel 稿 → 空列表。
    """
    d = Path(scan_dir)
    out: list[dict] = []
    for p in sorted(d.glob("_l4_intel_*.md")):
        code = p.stem.replace("_l4_intel_", "")
        try:
            text = p.read_text(encoding="utf-8")
        except Exception:  # noqa: BLE001 — 读不了的稿按未自报处理,不静默跳过
            out.append({"code": code, "claimed": None, "cap": cap})
            continue
        m = _INTEL_QUERY_RE.search(text)
        if m is None:
            out.append({"code": code, "claimed": None, "cap": cap})
        elif int(m.group(1)) > cap:
            out.append({"code": code, "claimed": int(m.group(1)), "cap": cap})
    return out
```

- [ ] **Step 4: 跑测试确认通过**

```bash
uv run --no-sync python -m pytest tests/learning/test_self_review.py -k intel_query_cap -q
```
Expected: 3 passed

- [ ] **Step 5: 接进 `product_shape_lint`(probe 10)**

先重读 `self_review.py` 的 probe 6 段(`intel零URL`,约 :390-399),在其**紧后**插入:

```python
    # ── 10. 情报查询限频对账(Wave6 Q1-②):声明行自报数 vs config cap ──
    # 07-24 实测 10/11 超限、最高 26/15 —— 声明行一直在写「网查 N 条」,只是全仓没有
    # 任何消费者读它(pr_20260714_007)。cap 取当日 echo,缺则 15。
    _cap = 15
    with contextlib.suppress(Exception):
        _cap = int((json.loads((scan_dir / "user_config_echo.json").read_text(encoding="utf-8"))
                    .get("l4_intel") or {}).get("max_queries") or 15)
    for h in intel_query_cap_lint(scan_dir, cap=_cap):
        _claimed = "未自报" if h["claimed"] is None else f"自报 {h['claimed']} 条"
        add("产物形状·intel限频", "warn",
            f"{_claimed} > cap {h['cap']} —— 限频是指令级无强制力(pr_20260714_007);"
            f"未自报 = 无法对账,不等于合规",
            code=h["code"])
```

> `product_shape_lint` 已在 `assemble.py:869-873` 接线,结果自动进 `gate_fires.csv` —— **本探针不需要任何新接线**。

- [ ] **Step 6: 写接线测试(变异探针:删掉 Step 5 这段必须变红)**

追加到 `tests/learning/test_self_review.py`:

```python
def test_product_shape_lint_emits_intel_query_cap_rows(tmp_path):
    """接线探针:probe 10 必须出现在 product_shape_lint 的返回里。

    变异验证:删掉 Step 5 那段(只留 lint 函数),本测试必须变红 —— 否则又是一个
    「生产者建好没有消费者」的假绿灯(product_shape_lint 已接线 assemble,
    所以进了这个函数就等于进了 gate_fires.csv)。
    """
    from autoresearch.learning import self_review

    d = tmp_path / "context" / "scan" / "2026-07-24"
    d.mkdir(parents=True)
    (d / "_l4_intel_600236.md").write_text(
        "## 声明行\n网查 26 条 ｜ as-of ≤ 2026-07-24\nhttps://example.com/x\n", encoding="utf-8")
    (d / "user_config_echo.json").write_text(
        '{"l4_intel": {"enabled": true, "max_queries": 15}}', encoding="utf-8")

    rows = self_review.product_shape_lint(d, "2026-07-24")
    hits = [r for r in rows if "intel限频" in str(r.get("check", ""))]

    assert hits, "product_shape_lint 未产出 intel 限频行(probe 10 没接线)"
    assert hits[0]["severity"] == "warn"
    assert hits[0]["code"] == "600236"
    assert "26" in str(hits[0]["detail"]) and "15" in str(hits[0]["detail"])
```

> `product_shape_lint` 全部 presence-gated、绝不抛异常,所以 fixture 只需要它真正读的文件;不用造 finalists.csv。

- [ ] **Step 7: 跑全量 + ruff**

```bash
set -o pipefail
uv run --no-sync python -m pytest -q > /tmp/w6_t1.txt 2>&1; echo "EXIT=$?"; tail -3 /tmp/w6_t1.txt
uv run --no-sync ruff check autoresearch tests
```
Expected: `EXIT=0`,计数 ≥1606

- [ ] **Step 8: 提交**

```bash
git add autoresearch/learning/self_review.py tests/learning/test_self_review.py
git commit -m "feat(intel): 查询限频对账 lint —— 声明行自报数一直没有消费者

07-24 实测 11 稿 10 只超 cap(最高 26/15),限频形同虚设(pr_20260714_007)。
未自报记 None 并同样 flag:缺字段是弱证据,不得以缺推断合规。

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

## Task 2: intel URL 契约(指令侧)

**为什么**:07-24 **11/11 情报稿零 http URL**,`intel零URL` lint 已经天天 warn 11 行,但 agent def 从没要求过带链接 —— 只罚不教。稿里写的是「证券之星、新浪财经」这种**源名**,不可回溯核查。

**Files:**
- Modify: `.claude/agents/l4-intel.md`
- Test: `tests/test_agent_defs.py::test_l4_intel_def`(扩锚)

**Interfaces:**
- Consumes: 无(纯人设文本)。
- Produces: agent def 里新增契约锚串 `来源URL`,供 `test_l4_intel_def` 断言;`self_review` 的 `intel零URL` lint(已存在)是它的机检对侧。

- [ ] **Step 1: 重读 agent def(纪律 4)**

```bash
cat .claude/agents/l4-intel.md
```
确认现有六面段落结构与声明行格式,**以实际文件为准**再改。

- [ ] **Step 2: 扩测试锚(先红)**

在 `tests/test_agent_defs.py::test_l4_intel_def` 的锚元组里追加两个锚:

```python
    for a in ("事件段", "题材段", "机构段", "互动段", "负面增量段", "声明行",
              "as-of", "六面全查", "≤15", "净分", "只报本票事实", "只攒料不判断", "不编", "盲",
              "已知底", "来源URL", "行情数字不自报"):
        assert a in text, f"l4-intel 缺契约锚「{a}」"
```

- [ ] **Step 3: 跑测试确认失败**

```bash
uv run --no-sync python -m pytest tests/test_agent_defs.py::test_l4_intel_def -q
```
Expected: FAIL — `l4-intel 缺契约锚「来源URL」`

- [ ] **Step 4: 改 agent def**

在 `.claude/agents/l4-intel.md` 的输出契约段(六面表之后、声明行说明之前)插入:

```markdown
### 来源URL(硬要求)

- 每条事件/机构/负面断言的「源」列**必须**带可点击 http(s) URL;拿不到 URL 的条目改写成「未核实:<一句话>」放段末,**不占净分**。
- 只写媒体名(如「证券之星」「金融界」)= 不可审计,机检 `intel零URL` 会逐稿 warn。
- 声明行照旧写「网查 N 条」——该数会与 config `max_queries` 逐稿对账(超限 warn),**如实写,不要凑数也不要瞒报**。
```

- [ ] **Step 5: 跑测试确认通过**

```bash
uv run --no-sync python -m pytest tests/test_agent_defs.py -q
```
Expected: PASS(注意 Step 2 已同时加了 `行情数字不自报` 锚 → 本 task 会红,由 Task 3 补齐;若想逐 task 绿,先只加 `来源URL` 锚,Task 3 再加第二个)

> 实施提示:为保持「每 task 结束全绿」,**Step 2 只加 `来源URL` 一个锚**,`行情数字不自报` 放到 Task 3 的 Step 2 再加。

- [ ] **Step 6: 提交**

```bash
git add .claude/agents/l4-intel.md tests/test_agent_defs.py
git commit -m "feat(intel): 来源 URL 硬契约 —— 只罚不教的 lint 补上教的一侧

11/11 稿零 URL 已 warn 多日,但人设从没要求带链接;拿不到 URL 的改写「未核实」不占净分。
注意 agent def 下 session 才生效,活体验收在下次扫描。

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

## Task 3: intel 行情数字禁区(价格断言从源头消灭)

**为什么**:07-24 `price_claim_mismatch` 两条(601869 称「涨停」实 +6.31%、601918 称「涨停」实 +5.98%)。查证发现卡里那句是**转引媒体标题**(《业绩暴涨超7倍!601869,涨停!》)。根因不是「谁在撒谎」,而是**行情数字有两个来源**:确定性 slim(真)与网查转述(可能是标题党/盘中口径)。修法是划禁区——行情数字只出自 slim,intel 只做定性增量;必须转引标题时显式标注为引用。

**Files:**
- Modify: `.claude/agents/l4-intel.md`(禁区条款)
- Modify: `.claude/agents/l4-card.md`(转引标注要求)
- Test: `tests/test_agent_defs.py::test_l4_intel_def` + 新增 `test_l4_card_quote_marking`

**Interfaces:**
- Consumes: Task 2 建立的 agent def 编辑惯例。
- Produces: 锚 `行情数字不自报`(l4-intel)、`转引标题`(l4-card);`price_claim_mismatch` lint(已存在)是机检对侧。

- [ ] **Step 1: 扩测试锚(先红)**

`tests/test_agent_defs.py::test_l4_intel_def` 锚元组追加 `"行情数字不自报"`;新增:

```python
def test_l4_card_quote_marking():
    """卡片转引媒体标题里的行情词必须显式标注为引用(Wave6 Q1-③)。

    07-24 两条 price_claim_mismatch 的真身是转引标题《…601869,涨停!》——
    机检按本票 OHLCV 对账当然不符。禁区划清后,标注过的引用才可与自陈断言区分。
    """
    text = _agent_text("l4-card")
    for a in ("转引标题", "行情数字以 slim 为准"):
        assert a in text, f"l4-card 缺契约锚「{a}」"
```

- [ ] **Step 2: 跑测试确认失败**

```bash
uv run --no-sync python -m pytest tests/test_agent_defs.py -k "l4_intel_def or quote_marking" -q
```
Expected: 2 FAILED

- [ ] **Step 3: 改两个 agent def**

`.claude/agents/l4-intel.md` 的「来源URL」节后追加:

```markdown
### 行情数字不自报(禁区)

- **涨跌幅 / 涨停跌停 / 成交额 / 换手 / 均线位置等行情数字一律不写**——这些由确定性 slim 供给,卡片自己有;情报只做**定性增量**(发生了什么事、谁在说、题材位置)。
- 必须提到某日异动时,只写方向与语境(「7/22 大幅回调,未见公司层面公告」),**不写具体百分比**;若引用的媒体标题本身含「涨停」等行情词,原样带引号并标 `〔转引标题〕`,不作为事实断言。
```

`.claude/agents/l4-card.md` 的卡契约段追加(**改前重读该文件**,勿破坏既有 `卡契约 v3` 等契约锚——`feedback_store._CONTRACT_ANCHORS` 有防删保护):

```markdown
- **行情数字以 slim 为准**:卡内任何涨跌幅/涨停断言只能取自 slim 的确定性行情块;转述媒体标题时必须写成 `〔转引标题〕《…》`,机检对账会跳过标注过的转引,未标注的按自陈断言对账(`price_claim_mismatch`)。
```

- [ ] **Step 4: 跑测试确认通过**

```bash
uv run --no-sync python -m pytest tests/test_agent_defs.py -q
```
Expected: PASS

- [ ] **Step 5: 提交**

```bash
git add .claude/agents/l4-intel.md .claude/agents/l4-card.md tests/test_agent_defs.py
git commit -m "feat(intel/card): 行情数字禁区 + 转引标题标注 —— 从源头消灭 price_claim 族

07-24 两条不符的真身是转引媒体标题(《…601869,涨停!》),不是捏造。
行情数字只出自确定性 slim;情报做定性增量;转引须标注。

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

## Task 4: general-purpose 壳 agent 降 haiku(T1)

**为什么**:07-24 真计量里 general-purpose 桶 13 个 agent 吃掉 **798k 加权 / 2.66M billed**,占全场加权 14.5%,其中 7 个是 **2 条消息**的纯壳(跑一条 Bash / 回显一行 JSON),各背 ~60k 的 opus 系统前缀,合计约 287k 加权纯过路费。这些壳**零判断**:`bash()` 只回报退出码、`gate()` 只把 CLI 打印的 JSON 原样带回、`ens-dump` 只写一个 heredoc 文件。

**Files:**
- Modify: `.claude/workflows/scan-market.js:25-47`(`bash()` / `gate()` 两个 helper)
- Modify: `.claude/workflows/l4-stock.js:83-85`(`ens-dump`)
- Test: `tests/test_agent_defs.py`(新增壳降档锚测试)

**Interfaces:**
- Consumes: 既有 opts 惯例 `{ agentType, effort, label, phase, schema }`。
- Produces: 两个 helper 的 opts 里出现 `model: 'haiku'`;不改任何 prompt 文本、不改 schema、不改控制流。

- [ ] **Step 1: 写失败测试**

追加到 `tests/test_agent_defs.py`:

```python
def test_workflow_shell_wrappers_use_haiku():
    """纯壳 agent(跑命令/回显 JSON/写文件)必须降到 haiku(Wave6 T1)。

    07-24 真计量:13 个 general-purpose 吃掉 798k 加权(全场 14.5%),其中 7 个是
    2 消息的纯壳,各背 ~60k opus 系统前缀。壳本身零判断 —— 门的判据在确定性 CLI 里。

    变异验证:把任一 `model: 'haiku'` 删掉,本测试必须变红。
    """
    sm = (ROOT / ".claude" / "workflows" / "scan-market.js").read_text(encoding="utf-8")
    ls = (ROOT / ".claude" / "workflows" / "l4-stock.js").read_text(encoding="utf-8")

    # 锚取**承重行**:agentType 与 model 必须同现在一个 opts 里,注释里写了不算
    assert sm.count("agentType: 'general-purpose', model: 'haiku'") == 2, \
        "scan-market.js 的 bash()/gate() 两个壳 helper 必须都降 haiku"
    assert "agentType: 'general-purpose', model: 'haiku'" in ls, \
        "l4-stock.js 的 ens-dump 壳必须降 haiku"
    # 真判断 agent 不得被误降
    for real in ("l3-rank", "l4-card", "l4-intel", "macro-brief", "sector-brief"):
        assert f"agentType: '{real}', model: 'haiku'" not in sm + ls, \
            f"{real} 是判断 agent,不得降 haiku"
```

- [ ] **Step 2: 跑测试确认失败**

```bash
uv run --no-sync python -m pytest tests/test_agent_defs.py::test_workflow_shell_wrappers_use_haiku -q
```
Expected: FAIL — count 0 != 2

- [ ] **Step 3: 改两个 workflow**

`.claude/workflows/scan-market.js` 的 `bash()`(:28-30)改为:

```js
function bash(cmd, label, phaseName) {   // 形参勿叫 phase:会遮蔽全局 phase() 分组函数
  return agent(
    `在仓库根目录精确执行下面这条命令,然后只回报:退出码 + stdout 末 15 行。不要做别的、不要判断、不要解释。\n\n\`\`\`\n${cmd}\n\`\`\``,
    // Wave6 T1:纯壳零判断(跑命令+回报退出码),此前背 opus 系统前缀 ~60k billed/次。
    { agentType: 'general-purpose', model: 'haiku', effort: 'low', label, ...(phaseName ? { phase: phaseName } : {}) })
}
```

`gate()`(:45-47)改为:

```js
function gate(label, cmd, schema, phaseName) {   // 同上:避免遮蔽全局 phase()
  return agent(
    `执行:\`${cmd}\`\n它会向 stdout 打印 JSON。把它打印的最后一行 JSON 原样作为你的结构化返回(字段不改、不增删)。`,
    // Wave6 T1:门的判据全在确定性 CLI 里,agent 只做 JSON 转述 → haiku 足够;
    // effort 从 high 降 low(转述不需要思考;schema 校验仍在,格式错会被 harness 拒)。
    { agentType: 'general-purpose', model: 'haiku', effort: 'low', label, schema, ...(phaseName ? { phase: phaseName } : {}) })
}
```

`.claude/workflows/l4-stock.js` 的 `ens-dump`(:83-85)改为:

```js
  await agent(
    `在仓库根目录精确执行下面这条命令,然后只回报退出码。不要做别的、不要判断。\n\n\`\`\`\ncat > ${SD}/_ensemble_${code}.json << 'EOF'\n${JSON.stringify(rec)}\nEOF\n\`\`\``,
    // Wave6 T1:heredoc 写文件,零判断
    { agentType: 'general-purpose', model: 'haiku', effort: 'low', label: `ens-dump:${code}`, phase: 'Verify' })
```

- [ ] **Step 4: 跑测试确认通过**

```bash
uv run --no-sync python -m pytest tests/test_agent_defs.py tests/test_workflow_js_syntax.py -q
```
Expected: PASS(js 语法探针也必须绿)

- [ ] **Step 5: 提交**

```bash
git add .claude/workflows/scan-market.js .claude/workflows/l4-stock.js tests/test_agent_defs.py
git commit -m "perf(workflow): 纯壳 agent 降 haiku —— 13 个 gp 占全场加权 14.5%

07-24 真计量:7 个 2-消息壳各背 ~60k opus 前缀 ≈287k 加权过路费。
壳零判断(跑命令/转述 JSON/写文件),判据在确定性 CLI 里。gate 的 effort 一并 high→low。
验收:CP7 分模型列里 gp 全部落 haiku 行且 GATE 行为不变。

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

## Task 5: ensemble 同档早止(T2)

**为什么**:SELL/OW 双复核固定跑 2 个额外满卡。07-24 的 601869 三票全 UW(spread=0),第三跑纯属白烧(每张复核卡 ~250-400k billed)。**数学事实**:三票取中位时,若 run1 与 run2 同档 X,则无论 run3 是什么,排序后的中位恒为 X —— 跳过 run3 **结果逐字节相同**。

**风险与诚实记账**:跳过后 `spread` 只在 2 个样本上算。所幸既有下游语义**天然正确**(侦察确认):`_ensemble_flag(rec)` 判 `spread>=2 or (degraded and spread>0)` —— 早止时两票同档 ⇒ spread=0 ⇒ 不触发人裁,**本来就没有「三人一致」的声称**;`_ensemble_dissent_lines` 也只在 flag 为真时渲染,且它打印的是 `len(ratings)` run,天然如实。所以 **Python 侧无需改判据**,只需产物记 `n_runs`/`early_stopped` 供后续「SELL 复核降为 1 跑」的账本分析。

**最容易写错的一处**:`degraded` 语义 = **复核跑失败**(→ `_apply_ensemble_fold` 禁折回);早止是**主动省跑**(→ 必须照常折回)。若把 `earlyStopped` 也算进 `degraded`,SELL 复核会在最该折回时不折。

**Files:**
- Modify: `.claude/workflows/l4-stock.js:58-91`(Verify 段)—— 本 task 唯一的行为改动
- Test: `tests/test_agent_defs.py`(workflow 锚)+ `tests/scan/test_assemble.py`(折回/flag 语义回归钉)

**Interfaces:**
- Consumes: 既有 `RANK` / `tier()` / `names[]` / `rec` 结构;`_apply_ensemble_fold(rating: str, rec: dict|None) -> str`、`_ensemble_flag(rec: dict|None) -> bool`(**两者签名已核实,不改**)。
- Produces: `_ensemble_<code>.json` 新增两键 `n_runs: 2|3`、`early_stopped: bool`;`degraded` 语义不变。

- [ ] **Step 1: 写失败测试(Python 侧语义钉,防未来把早止误并进 degraded)**

追加到 `tests/scan/test_assemble.py`:

```python
def test_early_stopped_ensemble_still_folds(tmp_path):
    """早止 ≠ degraded:degraded 表示复核跑失败(禁折回),早止是主动省跑(照常折回)。

    Wave6 T2:run1==run2 时三票中位数学上已定 → 跳过 run3。若把 earlyStopped 并进
    degraded,SELL 复核会在最该折回时不折 —— 这是本改动最容易写反的一处,钉死它。
    """
    from autoresearch.scan.assemble import _apply_ensemble_fold

    rec = {"code": "300857", "ratings": ["Sell", "Underweight"], "median": "Underweight",
           "spread": 1, "degraded": False, "trigger": "sell_review",
           "n_runs": 2, "early_stopped": True}

    assert _apply_ensemble_fold("Sell", rec) == "Underweight", "早止记录必须照常折回"


def test_early_stopped_two_same_votes_raise_no_dissent(tmp_path):
    """早止的 spread=0 不触发人裁 —— 因为两票确实同档,没有分歧可报。

    记录这条推理:早止**不会**制造「三人一致」的假声称(dissent 行只在 flag 为真时渲染,
    且打印 len(ratings) 即真实跑数)。
    """
    from autoresearch.scan.assemble import _ensemble_flag

    early = {"code": "601869", "ratings": ["Underweight", "Underweight"],
             "median": "Underweight", "spread": 0, "degraded": False,
             "trigger": "sell_review", "n_runs": 2, "early_stopped": True}

    assert _ensemble_flag(early) is False
```

- [ ] **Step 2: 跑测试确认失败**

```bash
uv run --no-sync python -m pytest tests/scan/test_assemble.py -k ensemble_early -q
```
Expected: FAIL

- [ ] **Step 3: 改 workflow(串行化 run2 → 判同档 → 决定 run3)**

`.claude/workflows/l4-stock.js` 的 Verify 段,把 `parallel([2,3])` 换成「先 run2,同档则止」:

```js
  const RANK = { 'sell': 0, 'underweight': 1, 'hold': 2, 'overweight': 3, 'buy': 4 }
  const tier = (r) => RANK[String(r || '').toLowerCase()] ?? 2
  const rerun = (i) => agent(
    `独立复核 run${i}(不知道其它 run 结论):执行 ${SD}/_l4_prompt_${code}.md 的任务包,按人设走渐进深度 DD,决策卡写到 ${SD}/ensemble/${code}.run${i}.md(先自行创建 ensemble/ 目录),返回 code/rating/conviction/proposal。`,
    { agentType: 'l4-card', effort: cfg.agents?.l4_card?.effort ?? 'xhigh',
      label: `ens${i}:${code}`, phase: 'Verify', schema: CARD })
  // Wave6 T2 同档早止:run1==run2 时三票中位**数学上已定**(两票同档 → 排序中位恒为该档),
  // run3 改变不了结论 → 跳过省一张满卡。分歧则照常跑 run3 当裁决票。
  const r2 = await rerun(2)
  const sameTier = r2 && tier(r2.rating) === tier(card.rating)
  const r3 = sameTier ? null : await rerun(3)
  const earlyStopped = !!sameTier
  const reruns = [r2, r3].filter(Boolean)
  if (earlyStopped) log(`🎭 同档早止:${code} run2 与 run1 同为 ${card.rating} —— 中位已定,跳过 run3`)
  const ratings = [card.rating, ...reruns.map((r) => r.rating)]
  const sorted = ratings.map(tier).sort((a, b) => a - b)
  // degraded 只表示「复核跑失败」(禁折回)。早止是主动省跑,**不是** degraded ——
  // 写反会让 SELL 复核在最该折回时不折。
  const degraded = earlyStopped ? false : ratings.length < 3
  const medianTier = sorted[Math.floor(sorted.length / 2)]
  const names = ['Sell', 'Underweight', 'Hold', 'Overweight', 'Buy']
  const rec = { code, ratings, median: names[medianTier],
    spread: sorted[sorted.length - 1] - sorted[0], degraded, trigger,
    n_runs: ratings.length, early_stopped: earlyStopped }
```

其余(ens-dump、折回、log)不动。

- [ ] **Step 4: 加 workflow 契约锚测试**

追加到 `tests/test_agent_defs.py`:

```python
def test_l4_stock_ensemble_early_stop_anchors():
    """同档早止的承重锚(Wave6 T2)。

    锚取真代码行:`earlyStopped ? false :` 这一段是「早止不算 degraded」的判据本体,
    删掉它(把早止并进 degraded)本测试必须变红 —— 那是最贵的写反方式。
    """
    js = (ROOT / ".claude" / "workflows" / "l4-stock.js").read_text(encoding="utf-8")
    assert "const r2 = await rerun(2)" in js, "run2 未串行化,无法同档早止"
    assert "earlyStopped ? false :" in js, "早止被并进 degraded = SELL 复核该折不折"
    assert "early_stopped: earlyStopped" in js, "产物未记早止标记(账本无法区分 2 跑/3 跑)"
```

- [ ] **Step 5: 跑测试确认通过 + 全量**

```bash
uv run --no-sync python -m pytest tests/scan/test_assemble.py tests/test_agent_defs.py tests/test_workflow_js_syntax.py -q
set -o pipefail; uv run --no-sync python -m pytest -q > /tmp/w6_t5.txt 2>&1; echo "EXIT=$?"; tail -3 /tmp/w6_t5.txt
```
Expected: 全绿

- [ ] **Step 6: 提交**

```bash
git add .claude/workflows/l4-stock.js tests/
git commit -m "perf(ensemble): 同档早止 —— run1==run2 时中位数学上已定,省一张满卡

07-24 601869 三票全 UW(spread 0)白烧第三跑。两票同档时排序中位恒为该档,
跳过 run3 结果逐字节相同。早止**不是** degraded(后者=复核失败禁折回),
产物记 n_runs/early_stopped,spread=0 不得被读成三人一致。

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

## Task 6: 报告 token 估算表纠偏(T8-a)

**为什么**:`_stage_token_estimate` 用「落盘字节 ÷2.8」估 token,07-24 那份报告写 **~183,623**,而追溯真计量是 **加权 5.49M / billed 22.4M / 输出 716.6k** —— 对加权低估 **30 倍**。这张表是决策输入(第二刀砍哪里全靠它),错 30 倍比没有更糟。

**改法**:保留表里**真实可测**的部分(分段墙钟、LLM 调用数、落盘字节 —— 这些都是硬事实),**删掉 `~token` 列与 `合计 ~token`**,改为指向 CP7 的真表;真表缺席时显式写「本 run 无真计量」而不是回落到估算。

**Files:**
- Modify: `autoresearch/scan/assemble.py:479-583`(`_stage_token_estimate`)
- Test: `tests/scan/test_assemble.py:165,174-209` + `tests/scan/test_sentinel_tokens.py:46-77`(改写既有断言)

**Interfaces:**
- Consumes: 既有 rows 结构 `(name, eng, eff, tkey, calls, bytes, note)`。
- Produces: 函数名与返回类型不变(`list[str]`);表头改为 `## 各阶段耗时 & 落盘字节`;新增末尾指路块。**`ensure_stage_timing(det)` 的调用必须保留**(它顺带写回 `_stage_timing.json`,是别处的依赖)。

- [ ] **Step 1: 改测试(先红)**

`tests/scan/test_assemble.py` 里 `test_summary_contains_token` 的断言改为:

```python
    assert "## 各阶段耗时 & 落盘字节" in md
    assert "落盘可测下界" not in md, "旧 ÷2.8 估算口径必须绝迹(07-24 实测低估 30 倍)"
    assert "token_usage.md" in md, "必须指向 CP7 真计量产物"
    assert "effort" in md and "墙钟" in md
```

`tests/scan/test_sentinel_tokens.py::test_token_estimate_rows` 里 `"落稿契约"`/`"未计而非为零"` 相关断言同步改为新文案锚;新增:

```python
def test_token_table_has_no_fabricated_token_column(tmp_path):
    """~token 列必须消失:字节÷2.8 对加权真值低估 30 倍(2026-07-24 追溯计量)。

    变异验证:把 ~token 列加回去,本测试必须变红。
    """
    from autoresearch.scan.assemble import _stage_token_estimate

    lines = _stage_token_estimate(tmp_path)
    head = next(ln for ln in lines if ln.startswith("| 阶段"))

    assert "~token" not in head, "估算列复辟"
    assert "落盘字节" in head, "真实可测的字节列应保留"
```

- [ ] **Step 2: 跑测试确认失败**

```bash
uv run --no-sync python -m pytest tests/scan/test_assemble.py tests/scan/test_sentinel_tokens.py -q
```
Expected: FAILED

- [ ] **Step 3: 改实现**

`assemble.py`:删除 `_BYTES_PER_TOK` 常量与所有 `tok = int(b / _BYTES_PER_TOK)` 计算;函数改名保持不变但 docstring 与渲染改写:

```python
def _stage_token_estimate(scan_dir: Path) -> list[str]:
    """分阶段耗时 + 落盘字节(确定性,无 LLM)。**不再估算 token**。

    历史:本表曾用「落盘字节 ÷2.8」估 token,2026-07-24 那份报告因此写 ~183.6k,而对同一次
    跑动做 transcript 追溯真计量得到 **加权 5.49M / billed 22.4M / 输出 716.6k** —— 对加权
    低估 30 倍,且分布与旧假设相反(L3 真占 7.8% 而非 37%)。决策输入错 30 倍比没有更糟,
    故估算列整列退役,token 一律以 CP7 的 `token_usage.md`(`trace.usage_harvest`)为准。

    保留的都是硬事实:分段墙钟(mtime 推导)、LLM 调用数、落盘字节。
    """
```

rows 构造不变;渲染段改为:

```python
    lines = ["## 各阶段耗时 & 落盘字节",
             "| 阶段 | 引擎 | effort | 墙钟 | LLM 调用 | 落盘字节 | 说明 |",
             "|---|---|---|---:|---:|---:|---|"]
    tot_calls = 0
    for name, eng, eff, tkey, calls, b, note in rows:
        tot_calls += 0 if name.startswith("L4 输入") else calls
        lines.append(f"| {name} | {eng} | {eff} | {_wall(tkey)} | {calls or '—'} | {b or '—'} | {note} |")
    lines.append(f"| **合计** | — | — | {_wall('总计')} | **{tot_calls}** | — | 墙钟 = mtime 推导下界(stage_timing.py) |")
```

末尾指路块替换旧口径脚注:

```python
    usage_md = Path("reports/scan") / scan_dir.name / "token_usage.md"
    lines += ["", "> **token 计量**:本表不估 token(旧「落盘字节 ÷2.8」口径 2026-07-24 实测对加权真值"
              "低估 30 倍,已整列退役)。真实用量见 CP7 产出的 `token_usage.md`"
              "(`python -m autoresearch.trace.usage_harvest --session <sessionId> --out …`),"
              "按计价倍率加权;**若该文件不存在 = 本次未计量,不等于用量小**。", ""]
```

> 注意:`usage_md` 变量若最终没用到就别留(ruff 会报未使用)。上面文案里不插路径也可以——**以 ruff 净为准**。

- [ ] **Step 4: 跑测试确认通过**

```bash
uv run --no-sync python -m pytest tests/scan/ -q
uv run --no-sync ruff check autoresearch tests
```

- [ ] **Step 5: 检查 `_BYTES_PER_TOK` 的镜像副本**

`tests/scan/test_l4_precedent_mark.py:27` 有一份**独立副本**(注释写 mirror),它不 import,删常量不会红。决定:**保留该副本**(它量的是 per-card prompt 预算,与本表口径无关),但把注释里的 "mirror autoresearch.scan.assemble._BYTES_PER_TOK" 改成 "本文件自持(assemble 侧已退役)",防后人以为还有个真值源。

- [ ] **Step 6: 提交**

```bash
git add autoresearch/scan/assemble.py tests/
git commit -m "fix(report): token 估算列退役 —— 字节÷2.8 对加权真值低估 30 倍

07-24 那份报告写 ~183.6k,同一次跑动 transcript 追溯真计量 = 加权 5.49M/billed 22.4M。
决策输入错 30 倍比没有更糟。保留墙钟/调用数/落盘字节(硬事实),token 指向 CP7 真表;
真表缺席时显式写「未计量」,不回落估算。

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

## Task 7: usage_harvest 分模型计价列 + 追溯模式(T8-b)

**为什么**:①T1 把壳降到 haiku 后,**加权 token 数几乎不变**(加权只含 cache 倍率),必须有分模型列才能看出省了钱——否则 T1 无法验收;②本次能拿到真数据靠的是手写驱动脚本,应固化成官方入口。

**Files:**
- Modify: `autoresearch/trace/usage_harvest.py`
- Test: `tests/trace/test_usage_harvest.py`(新增 3 个)

**Interfaces:**
- Consumes: 既有 `usage_of()` 返回 dict(含 `model` 键)。
- Produces: ①`render()` 的汇总表新增 `model` 维度分组;②新 CLI 参数 `--transcripts <glob>`(与 `--dir`/`--session` 三选一);③`MODEL_PRICE` 常量表(相对 opus 输入价的倍率,仅用于展示排序,不冒充账单)。

- [ ] **Step 1: 写失败测试**

```python
def test_rollup_splits_by_model(tmp_path):
    """汇总必须按 model 分列:T1 把壳降 haiku 后加权 token 数几乎不变,
    只有分模型列能看出真实成本变化(加权口径只含 cache 倍率,不含模型价差)。"""
    from autoresearch.trace import usage_harvest as U

    rows = [{"agent": "general-purpose", "model": "claude-haiku-4-5-20251001", "effort": "low",
             "messages": 2, "input": 100, "output": 500, "cache_read": 60000,
             "cache_create": 1000, "cache_create_1h": 0, "billed_in": 61100,
             "weighted_in": 7350, "file": "a.jsonl"},
            {"agent": "l4-card", "model": "claude-opus-5", "effort": "xhigh",
             "messages": 20, "input": 0, "output": 40000, "cache_read": 400000,
             "cache_create": 50000, "cache_create_1h": 0, "billed_in": 450000,
             "weighted_in": 102500, "file": "b.jsonl"}]

    md = U.render(rows)

    assert "按模型汇总" in md
    assert "haiku" in md.lower() and "opus" in md.lower()


def test_model_bucket_is_normalized_not_raw_id(tmp_path):
    """model 桶取家族名(haiku/sonnet/opus),不是带日期的完整 id —— 否则同族跨版本分裂成多行。"""
    from autoresearch.trace.usage_harvest import model_family

    assert model_family("claude-haiku-4-5-20251001") == "haiku"
    assert model_family("claude-opus-5") == "opus"
    assert model_family("claude-sonnet-5") == "sonnet"
    assert model_family("—") == "(未标注)"


def test_transcripts_glob_mode(tmp_path):
    """--transcripts <glob> 追溯模式:run 结束后计量代码才落地时,仍能从存活 transcript 补账。"""
    from autoresearch.trace.usage_harvest import collect_glob

    d = tmp_path / "wf_x"
    d.mkdir()
    (d / "agent-1.jsonl").write_text(
        '{"attributionAgent":"l4-card","message":{"id":"m1","model":"claude-opus-5",'
        '"usage":{"input_tokens":10,"output_tokens":20,"cache_read_input_tokens":30}}}\n',
        encoding="utf-8")

    rows = collect_glob(str(tmp_path / "*" / "agent-*.jsonl"))

    assert len(rows) == 1 and rows[0]["agent"] == "l4-card"
```

- [ ] **Step 2: 跑测试确认失败**

```bash
uv run --no-sync python -m pytest tests/trace/test_usage_harvest.py -k "model or glob" -q
```
Expected: FAILED

- [ ] **Step 3: 实现**

在 `usage_harvest.py` 加:

```python
# 模型价差(相对 opus 输入价的**倍率**,仅供「贵在哪」排序,不冒充账单)。
# 加权口径只含 cache 倍率 —— 把壳从 opus 降到 haiku,加权 token 数几乎不变而真实成本降一个量级,
# 所以必须有这一维,否则 T1 那类降档改动在表上完全看不出来。
_MODEL_MULT = {"haiku": 0.1, "sonnet": 0.33, "opus": 1.0}


def model_family(model: str | None) -> str:
    """完整 model id → 家族名(haiku/sonnet/opus);认不出 → `(未标注)`。

    取家族而非原始 id:否则同族跨版本(claude-haiku-4-5-2025…)会分裂成多行,汇总失去意义。
    """
    m = str(model or "").lower()
    for fam in ("haiku", "sonnet", "opus"):
        if fam in m:
            return fam
    return "(未标注)"


def collect_glob(pattern: str) -> list[dict]:
    """按 glob 收 transcript(追溯模式)。

    计量代码晚于某次 run 落地时(Wave6 附录 A 的处境),transcript 仍存活 —— 这里让补账
    成为官方入口,而不是每次手写驱动脚本。
    """
    import glob as _glob
    rows = [usage_of(Path(p)) for p in sorted(_glob.glob(pattern, recursive=True))]
    return sorted(rows, key=lambda r: -r["weighted_in"])
```

`render()` 在 agent 类型汇总之后追加分模型汇总:

```python
    bym: dict[str, dict] = {}
    for r in rows:
        fam = model_family(r.get("model"))
        b = bym.setdefault(fam, {"n": 0, "w": 0, "out": 0})
        b["n"] += 1
        b["w"] += r["weighted_in"]
        b["out"] += r["output"]
    out += ["", "**按模型汇总**(加权 × 模型价差 ≈ 真实成本方向;加权口径本身不含模型价差):", "",
            "| 模型 | 个数 | 加权输入 | 价差倍率 | 折算(相对 opus) | 输出 |", "|---|---:|---:|---:|---:|---:|"]
    for fam, b in sorted(bym.items(), key=lambda kv: -kv[1]["w"]):
        mult = _MODEL_MULT.get(fam)
        adj = f"{_k(int(b['w'] * mult))}" if mult else "—"
        out.append(f"| {fam} | {b['n']} | {_k(b['w'])} | {mult if mult else '—'} | {adj} | {_k(b['out'])} |")
```

`main()` 加参数:

```python
    ap.add_argument("--transcripts", default=None,
                    help="transcript glob(追溯模式,与 --dir/--session 三选一)")
```
并在 `sub` 解析处优先处理 glob:

```python
    if a.transcripts:
        md = render(collect_glob(a.transcripts), sub_dir=a.transcripts)
    else:
        sub = Path(a.dir) if a.dir else (find_session_dir(a.session) if a.session else None)
        if sub is None:
            print("[usage_harvest] 需要 --dir / --session / --transcripts")
            return 1
        md = render(collect(sub), sub_dir=str(sub))
```

- [ ] **Step 4: 跑测试 + 全量**

```bash
uv run --no-sync python -m pytest tests/trace/ -q
set -o pipefail; uv run --no-sync python -m pytest -q > /tmp/w6_t7.txt 2>&1; echo "EXIT=$?"; tail -3 /tmp/w6_t7.txt
```

- [ ] **Step 5: 真数据冒烟(不是合成)**

```bash
uv run --no-sync python -m autoresearch.trace.usage_harvest \
  --transcripts "$HOME/.claude/projects/-Users-qingbin-zhuang-Personal-TradingAgents/*/subagents/workflows/wf_*/agent-*.jsonl" \
  | head -40
```
Expected: 表里出现「按模型汇总」段且有真实行数(**必须真跑一次,不能只靠合成 fixture**——操作建议要先跑通再写进文档)。

- [ ] **Step 6: 提交**

```bash
git add autoresearch/trace/usage_harvest.py tests/trace/test_usage_harvest.py
git commit -m "feat(trace): usage_harvest 分模型汇总 + --transcripts 追溯模式

加权口径只含 cache 倍率,壳从 opus 降 haiku 后加权几乎不变 —— 没有分模型列 T1 无法验收。
追溯模式把「计量代码晚于 run 落地」时的补账固化成官方入口(Wave6 附录 A 的手写驱动退役)。

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

## Task 8: OTEL telemetry 退役(E1)

**为什么**:`trace/telemetry.py` 自 2026-07-05 建成起**零生产调用点**,全仓没有一个 `token_telemetry.md`,`STAGES.md:263` 自述「未实跑」;usage_harvest 已在批2 spike 对拍胜出(transcript 自带 usage 且能给 cache 命中率)。留着 = 两套计量、两处文档,后人不知道该信哪个。

**删除影响(已 grep 全仓)**:唯一 Python 消费者是 `tests/trace/test_telemetry.py`(5 个测试);另需改 3 处文档锚(`SKILL.md:36`、`STAGES.md:263-267`、`CLAUDE.md:25`)。文档改动在 Task 13 统一做,本 task 只删代码+测试并改 `CLAUDE.md` 一行。

**Files:**
- Delete: `autoresearch/trace/telemetry.py`
- Delete: `tests/trace/test_telemetry.py`
- Modify: `CLAUDE.md:25`

- [ ] **Step 1: 删除前再 grep 一次(纪律:删 test 先读 docstring 查双职)**

```bash
grep -rn "telemetry" autoresearch tests .claude *.md | grep -v "usage_harvest\|docs/specs"
```
确认除自身 + 5 个测试 + 3 处文档外无其它消费者。**若出现新消费者,停下来先报告,不要硬删。**

- [ ] **Step 2: 读 `tests/trace/test_telemetry.py` 的 docstring 查双职**

```bash
head -20 tests/trace/test_telemetry.py
```
确认它只测 OTEL 解析(`"""OTEL 遥测解析器:多形态容错 + 累计/增量自动判别 + cache 命中率表。合成,无网络。"""`),**没有顺带锁别的 live 契约**(deadcode-cleanup 那一波的教训)。

- [ ] **Step 3: 删除**

```bash
git rm autoresearch/trace/telemetry.py tests/trace/test_telemetry.py
```

- [ ] **Step 4: 改 CLAUDE.md:25**

把 `OTEL 计量（telemetry）` 改为 `token 真计量（usage_harvest）`。

- [ ] **Step 5: 跑全量确认少了 5 个且无 ImportError**

```bash
set -o pipefail; uv run --no-sync python -m pytest -q > /tmp/w6_t8.txt 2>&1; echo "EXIT=$?"; tail -3 /tmp/w6_t8.txt
```
Expected: EXIT=0,计数比上一 task 少 5

- [ ] **Step 6: 提交**

```bash
git add -A
git commit -m "chore(trace): OTEL telemetry 退役 —— 建成 3 周零生产调用点,已被 usage_harvest 对拍胜出

全仓无一个 token_telemetry.md,STAGES.md 自述未实跑。两套计量并存 = 后人不知信谁。
唯一消费者是它自己的 5 个测试(docstring 确认无双职)。文档锚改写见后续 task。

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

## Task 9: slim 尺寸阈值单一事实源(Q6-a)

**为什么**:同一件事有**两个不一致的阈值**:`process_score.py:40` 用 `10*1024`(注释自称「按 spec 从严」),而派发 prompt(`l4_card.py:764`)告诉 agent「>8KB 才可信」。07-24 实测 11 份 slim 全在 8,690–10,055 B —— **11/11 被 `chk_slim_size` 判 fail**,而它们内容完好(表瘦身后的新常态),真正的坏签名是 ≈4.8KB 的 NO_DATA 空稿。这不是「把阈值调松」,是**消除两个真值源**并对齐真实故障签名。

**Files:**
- Modify: `autoresearch/scan/agents/l4_card.py`(定义常量 `SLIM_MIN_BYTES` 并在 prompt 文案里引用)
- Modify: `autoresearch/learning/process_score.py`(import 该常量,删本地副本)
- Test: `tests/learning/test_process_score.py` + `tests/scan/test_l4_dispatch_pack.py`

**Interfaces:**
- Produces: `autoresearch.scan.agents.l4_card.SLIM_MIN_BYTES: int = 8 * 1024` —— 全仓唯一 slim 可信下界;`process_score` 与 prompt 文案都消费它。

- [ ] **Step 1: 写失败测试**

```python
def test_slim_threshold_single_source_of_truth():
    """slim 可信下界只能有一个真值源(Wave6 Q6-a)。

    此前 process_score 用 10KB、派发 prompt 告诉 agent 8KB —— 07-24 实测 11 份 slim
    全在 8.7–10.1KB(表瘦身后新常态),被 10KB 线判 11/11 假阳,而真正的坏签名是
    ≈4.8KB 的 NO_DATA 空稿。变异验证:把任一侧改回硬编码,本测试变红。
    """
    from autoresearch.learning import process_score
    from autoresearch.scan.agents.l4_card import SLIM_MIN_BYTES

    assert SLIM_MIN_BYTES == 8 * 1024
    assert process_score._SLIM_MIN_BYTES is SLIM_MIN_BYTES, "process_score 必须复用同一常量,不得自持副本"


def test_slim_size_check_passes_on_current_norm(tmp_path):
    """8.7KB 的正常 slim 必须通过;4.8KB 的 NO_DATA 空稿必须不通过。"""
    from autoresearch.scan.agents.l4_card import SLIM_MIN_BYTES

    assert 8_700 > SLIM_MIN_BYTES, "表瘦身后的正常 slim 不该被判空"
    assert 4_800 < SLIM_MIN_BYTES, "4.8KB NO_DATA 签名必须仍被逮住"
```

- [ ] **Step 2: 跑测试确认失败**

```bash
uv run --no-sync python -m pytest tests/learning/test_process_score.py -k slim_threshold -q
```
Expected: FAIL — ImportError / 10240 != 8192

- [ ] **Step 3: 实现**

`l4_card.py` 模块级加常量(放在既有常量区):

```python
SLIM_MIN_BYTES = 8 * 1024
"""slim 可信下界(全仓唯一真值源)。

历史:`process_score` 曾自持 10KB、派发 prompt 文案写 8KB —— 两个真值源不一致,
2026-07-24 实测 11 份 slim 全在 8.7–10.1KB(表瘦身后的新常态)被 10KB 线判 11/11 假阳。
真正的坏签名是 ≈4.8KB 的 NO_DATA 空稿(见 `_slim_defect` 的结构+内容判据),体积只兜真垃圾。
"""
```

`l4_card.py:764` 的 prompt 文案改成引用常量:

```python
            f"- slim 数据:`context/{ticker}_{date}_slim.md`(P1–P3 表面块;**>{SLIM_MIN_BYTES // 1024}KB 才可信**,≈4.8KB=NO_DATA 须重拉)",
```

`process_score.py`:删掉本地 `_SLIM_MIN_BYTES = 10 * 1024`,改为

```python
from autoresearch.scan.agents.l4_card import SLIM_MIN_BYTES as _SLIM_MIN_BYTES
```
(保留旧私名以免动其它引用;若模块顶部有循环 import 风险,改成函数内 lazy import——**以真跑为准**。)

- [ ] **Step 4: 跑测试 + 回填历史读数**

```bash
uv run --no-sync python -m pytest tests/learning/ tests/scan/test_l4_dispatch_pack.py -q
uv run --no-sync python -c "
from autoresearch.learning.process_score import compute_process_scores
df = compute_process_scores('context/scan/2026-07-24')
print(df[['code','chk_slim_size','chk_blind_pass','process_score']].to_string(index=False))
"
```
Expected: `chk_slim_size` 从 11/11 False 变 11/11 True;`process_score` 从 4 升到 5(`chk_blind_pass` 仍 False,由 Task 10 处理)。

- [ ] **Step 5: 提交**

```bash
git add autoresearch/scan/agents/l4_card.py autoresearch/learning/process_score.py tests/
git commit -m "fix(slim): 尺寸阈值收敛到单一事实源 8KB —— 两个真值源判了 11/11 假阳

process_score 自持 10KB、派发 prompt 写 8KB;07-24 实测 11 份 slim 全在 8.7-10.1KB
(表瘦身后新常态)被判空。真坏签名是 4.8KB 的 NO_DATA,体积只兜真垃圾。

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

## Task 10: `chk_blind_pass` 契约化(Q6-b)

**诊断先行(已完成,结论写在这里)**:`chk_blind_pass` = 卡文含字面串「独立初判」。07-24 **0/11 卡命中**。`l4-card.md:16` 与 `lite-playbook.md:9` 确实都写着「P1 先读 slim 数字块写 3 行独立初判」——**指令在、检查在,但那句话埋在散文「铁律」里,不是卡的机器契约元素**,agent 照做了却不会用这四个字打标签。所以这是**契约缺位**,不是检查坏 → 修法是把它升格为卡片结构元素,而**不是**放宽检查。

**Files:**
- Modify: `.claude/agents/l4-card.md`(卡契约段加结构元素)
- Modify: `.claude/skills/stock-research/lite-playbook.md`(真值源同步)
- Test: `tests/test_agent_defs.py`(契约同步锚)

**Interfaces:**
- Consumes: Task 3 已建立的 l4-card 编辑惯例。
- Produces: 卡片结构元素 `**独立初判**:`(带粗体冒号的标签行),`process_score.chk_blind_pass` 的字面串检查不变(仍找「独立初判」)。

- [ ] **Step 1: 重读两个文件(纪律 4)**

```bash
sed -n '1,40p' .claude/agents/l4-card.md
sed -n '1,20p' .claude/skills/stock-research/lite-playbook.md
```
确认 `卡契约 v3` 等契约锚位置(`feedback_store._CONTRACT_ANCHORS` 保护这些串,**不得删**)。

- [ ] **Step 2: 写失败测试**

```python
def test_card_blind_pass_is_a_contract_element_not_prose():
    """独立初判必须是卡的**结构元素**而非散文铁律(Wave6 Q6-b)。

    诊断:07-24 0/11 卡含「独立初判」四字,而 agent def 与 playbook 都写了这条铁律 ——
    指令在、检查在,缺的是「必须以这个标签落在卡里」的机器契约。放宽检查是错解。
    """
    agent = _agent_text("l4-card")
    playbook = (SKILLS / "stock-research" / "lite-playbook.md").read_text(encoding="utf-8")

    assert "**独立初判**:" in agent, "l4-card 未把独立初判定为卡片结构元素(带标签)"
    assert "**独立初判**:" in playbook, "lite-playbook(真值源)未同步该结构元素"
```

- [ ] **Step 3: 跑测试确认失败**

```bash
uv run --no-sync python -m pytest tests/test_agent_defs.py -k blind_pass -q
```

- [ ] **Step 4: 改两个文件**

在 l4-card.md 的**卡结构/机器契约**段(不是「铁律」散文段)加一行:

```markdown
- **`**独立初判**:` 行(P1 必写,一行三句)**:读完 slim 数字块、**尚未**读 L3 前提清单时写下资金/技术/估值各一句。这是卡的结构元素,机检 `chk_blind_pass` 按此标签核在场;写了判断却不打标签 = 不计。
```

lite-playbook.md 同步同一行(两处文案必须一致,`test_agent_defs` 有同步锚)。

- [ ] **Step 5: 跑测试确认通过 + 全量**

```bash
uv run --no-sync python -m pytest tests/test_agent_defs.py -q
set -o pipefail; uv run --no-sync python -m pytest -q > /tmp/w6_t10.txt 2>&1; echo "EXIT=$?"; tail -3 /tmp/w6_t10.txt
```

- [ ] **Step 6: 提交**

```bash
git add .claude/agents/l4-card.md .claude/skills/stock-research/lite-playbook.md tests/
git commit -m "fix(card): 独立初判升格为卡结构元素 —— 0/11 命中的是契约缺位不是检查坏

指令埋在散文铁律里,agent 照做但不打标签,chk_blind_pass 全 fail。
放宽检查是错解;正解是给它一个机器可核的标签行。agent def 下 session 生效。

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

## Task 11: 三个小契约修缮(Q6-c)

三件互不相干的小修,合成一个 task(各自独立可回滚,一起测更省时间)。

**11a · conviction 标度**(pr_20260717_005):07-14 实测某只票 l4-stock 回传 `conviction: 0.62`,其余 8 只是 0–100 整数 —— 同一字段两种标度,下游 `force_full_card`(判据 conviction≥70)会把 0.62 当成极低确信。修法:workflow 的 `CARD` schema 已声明 `conviction: number`,加一个**回传后归一**:`<=1` 视为比例 → ×100。

**11b · run_health 时序**:`run_health.json` 记 `missing: [verify.csv, gate_fires.csv]`,而 `gate_fires.csv` 同一秒就存在 —— 健康快照在 gate 账本写盘**之前**取。修法:把快照调用挪到 gate 账本写盘之后。

**11c · L3_news 空文件**:203 个 2 字节空 JSON(anns 退役后的残渣),纯文件系统开销且让 `ls` 噪声化。修法:producer 侧空结果不落盘。

**Files:**
- Modify: `.claude/workflows/l4-stock.js`(11a)
- Modify: `autoresearch/scan/assemble.py`(11b)
- Modify: `autoresearch/scan/agents/l3_select.py`(11c,`harvest_l3_news`)
- Test: `tests/test_agent_defs.py`(11a 锚)、`tests/scan/test_assemble.py`(11b)、`tests/scan/test_l3_*.py`(11c)

- [ ] **Step 1: 写三个失败测试**

```python
# tests/test_agent_defs.py
def test_l4_stock_normalizes_conviction_scale():
    """conviction 必须归一到 0-100(pr_20260717_005:实测 0.62 混在 60-78 里)。

    下游 force_full_card 判据是 conviction>=70 —— 0.62 会被当成极低确信,静默失效。
    """
    js = (ROOT / ".claude" / "workflows" / "l4-stock.js").read_text(encoding="utf-8")
    assert "normConviction" in js, "l4-stock.js 未做 conviction 标度归一"


# tests/scan/test_assemble.py
def test_run_health_snapshot_taken_after_gate_ledger(tmp_path):
    """run_health 的 missing 列表不得把同一次 assemble 稍后才写的 gate_fires.csv 记成缺失。

    07-24 实锤:missing 里有 gate_fires.csv,而该文件与 run_health 同一秒存在 —— 快照取早了。
    """
    ...  # 建 scan_dir fixture 后跑 assemble.run,断言 run_health["missing"] 不含 "gate_fires.csv"


# tests/scan/test_l3_evidence.py(或 news harvest 所在测试文件)
def test_empty_news_result_is_not_written(tmp_path):
    """空 news 结果不落盘(07-24:203 个 2 字节空 JSON = anns 退役残渣)。"""
    ...
```

> 三个测试的 fixture 形状**以既有测试文件为准**——先读同文件里最接近的一个测试,照它的建法写。

- [ ] **Step 2: 跑确认三红**

- [ ] **Step 3: 实现 11a**

`l4-stock.js` 在 `const card = await agent(...)` 之后、`log` 之前:

```js
// pr_20260717_005:同一字段两种标度(实测一只回 0.62,其余 60-78)。下游 force_full_card
// 判据是 conviction>=70 —— 0.62 会被当成极低确信而静默失效。<=1 视为比例,归一到 0-100。
const normConviction = (v) => (typeof v === 'number' && v > 0 && v <= 1 ? v * 100 : v)
if (card && card.conviction != null) card.conviction = normConviction(card.conviction)
```

- [ ] **Step 4: 实现 11b**

读 `assemble.py` 里 `run_health` 快照与 gate 账本写盘的两处调用点,把快照调用移到写盘之后(**若两者之间有依赖,改为写盘后重算 missing 列表**)。

- [ ] **Step 5: 实现 11c**

`l3_select.py` 的 news harvest 落盘处加空判:

```python
        if not payload:            # 空结果不落盘(anns 退役后 203 个 2 字节空 JSON 是纯开销)
            continue
```

- [ ] **Step 6: 跑全量 + 提交**

```bash
set -o pipefail; uv run --no-sync python -m pytest -q > /tmp/w6_t11.txt 2>&1; echo "EXIT=$?"; tail -3 /tmp/w6_t11.txt
git add -A && git commit -m "fix: conviction 标度归一 / run_health 时序 / 空 news 不落盘

三件小契约修缮,各有 07-24 实锤:0.62 混在 60-78 里让 force_full_card 静默失效;
run_health 把同秒存在的 gate_fires.csv 记成缺失;203 个 2 字节空 JSON 是 anns 退役残渣。

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

## Task 12: market_pack 当日切面块(Q3,零新端点)

**为什么**:market_pack 有 60 日动量、有估值分位、有资金面,**唯独没有「今天盘面怎么样」**(pr_20260721_002)。而 `pct_1d`(当日涨跌幅)**本来就在 frame 上**(`tushare_source.py:398`),零新端点即可算涨停家数/全市场当日中位/板块当日中位 top3·bottom3。

**⚠️ 半接线陷阱(必须一次做完两侧)**:`market_pack_from_frame`(帧入口,Stage 0 用)能拿到 `pct_1d`,但 `market_pack(scan_dir)`(staging 入口,**L4 的 `market_context_block` 用**)读的是 `L1_scored_full.csv`,而 `universe.py:434-440` 的投影列表**丢掉了 `pct_1d`** → 只改 market.py = 两个入口不一致,正是本仓 FN-1 家族(`test_market_pack_macro_cn.py` 就专门守这个)。所以必须**同时**给 `universe.py` 的 `keep` 补 `pct_1d`。

**Files:**
- Modify: `autoresearch/scan/universe.py:434-440`(`keep` 补列)
- Modify: `autoresearch/scan/market.py`(新增 `_today_slice` 块 + 两个入口都挂)
- Test: `tests/scan/test_market_pack.py`(照 `test_market_pack_macro_cn.py` 的**两入口 parity** 模式写)

**Interfaces:**
- Produces: pack 新键 `today_slice = {"n_up_limit": int, "n_down_limit": int, "median_pct_1d": float, "sector_top3": [{"industry","median_pct_1d"}], "sector_bottom3": [...]}`;`pct_1d` 缺失 → 该块 `None`(presence-gated,与 `macro_cn` 同款)。

- [ ] **Step 1: 写失败测试(两入口 parity 是重点)**

```python
def test_today_slice_present_in_both_entries(tmp_path):
    """当日切面必须两个入口都有(FN-1 防线)。

    帧入口能拿 pct_1d,staging 入口读 L1_scored_full.csv —— 若只改 market.py 不补
    universe.py 的投影列,L4 侧永远拿不到这块,就是「生产者接线了、消费者拿不到」。
    """
    ...  # 造含 pct_1d 的 frame → market_pack_from_frame;造含 pct_1d 列的 L1_scored_full.csv → market_pack
    assert pack_frame["today_slice"]["n_up_limit"] == 2
    assert pack_staging["today_slice"]["n_up_limit"] == 2


def test_today_slice_none_without_pct_1d(tmp_path):
    """无 pct_1d 列 → 块为 None(presence-gated,不编 0)。"""


def test_universe_projection_keeps_pct_1d():
    """L1_scored_full.csv 的投影列表必须含 pct_1d,否则 staging 入口恒空。"""
    from autoresearch.scan import universe
    src = Path(universe.__file__).read_text(encoding="utf-8")
    assert '"pct_1d"' in src, "universe 投影丢了 pct_1d → 当日切面在 L4 侧恒空"
```

- [ ] **Step 2: 跑确认失败**

- [ ] **Step 3: 实现**

`universe.py:436` 的 keep 列表里 `"pct_60d", "pct_ytd",` 改为 `"pct_1d", "pct_60d", "pct_ytd",`。

`market.py` 新增(照 `_sectors_from_frame` 的 groupby 模板):

```python
def _today_slice(d: "pd.DataFrame") -> dict | None:
    """当日切面(pr_20260721_002):涨跌停家数 / 全市场当日中位 / 板块当日中位 top3·bottom3。

    零新端点 —— `pct_1d` 本来就在帧上(tushare daily 的 pct_chg)。缺列 → None(不编 0)。
    涨停判据用 ≥9.5%(主板 10% 含 ST/北交所差异,取略松阈值只作盘面温度描述,不做交易判据)。
    """
    if d is None or "pct_1d" not in d.columns or not len(d):
        return None
    s = pd.to_numeric(d["pct_1d"], errors="coerce")
    out = {"n_up_limit": int((s >= 9.5).sum()), "n_down_limit": int((s <= -9.5).sum()),
           "median_pct_1d": _f(s.median()), "sector_top3": [], "sector_bottom3": []}
    if "industry" in d.columns:
        g = d.assign(_p=s).groupby(d["industry"].astype(str))["_p"].median().dropna()
        g = g.sort_values(ascending=False)
        out["sector_top3"] = [{"industry": k, "median_pct_1d": _f(v)} for k, v in g.head(3).items()]
        out["sector_bottom3"] = [{"industry": k, "median_pct_1d": _f(v)} for k, v in g.tail(3).items()]
    return out
```

两个入口(`market_pack_from_frame` 与 `market_pack`)各挂一行 `pack["today_slice"] = _today_slice(df)`。

- [ ] **Step 4: 真数据冒烟**

```bash
uv run --no-sync python -c "
import pandas as pd, json
from autoresearch.scan.market import _today_slice
df = pd.read_csv('context/scan/2026-07-24/L1_scored_full.csv')
print('pct_1d 在表里吗:', 'pct_1d' in df.columns)
"
```
(07-24 的旧 CSV **不会**有 pct_1d —— 这是预期的,新列从下次 prelude 起才有。冒烟只验证代码路径与 None 分支。)

- [ ] **Step 5: 跑全量 + 提交**

```bash
set -o pipefail; uv run --no-sync python -m pytest -q > /tmp/w6_t12.txt 2>&1; echo "EXIT=$?"; tail -3 /tmp/w6_t12.txt
git add autoresearch/scan/market.py autoresearch/scan/universe.py tests/
git commit -m "feat(pack): 当日切面块(涨跌停家数/当日中位/板块 top3) —— 零新端点

pack 有 60 日动量和估值分位,唯独没有「今天盘面怎么样」(pr_20260721_002)。
pct_1d 本就在帧上,但 L1_scored_full 投影丢了它 → 必须同时补 universe.keep,
否则 staging 入口(L4 消费侧)恒空 = FN-1 半接线。

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

## Task 13: 文档面统一改写(E1 文档腿 + T8 叙事 + T3① 收尾合并)

**为什么合成一个 task**:三处都改 `SKILL.md`/`STAGES.md`。skill 文档会被外部改动,**每次编辑前重读**;一次改完比分三次安全。

**Files:**
- Modify: `.claude/skills/scan-market/SKILL.md`(:36 OTEL 前置删除;:125-131 收尾合并;CP7 段措辞)
- Modify: `.claude/skills/scan-market/STAGES.md`(:263-267 计量节改写)
- Test: `tests/test_agent_defs.py::test_usage_harvest_wired_in_skill`(已有,不得变红)+ 新增反向锚

- [ ] **Step 1: 重读两份文档的相关段(纪律 4)**

```bash
sed -n '30,40p;50,80p;120,135p' .claude/skills/scan-market/SKILL.md
sed -n '258,272p' .claude/skills/scan-market/STAGES.md
```

- [ ] **Step 2: 写失败测试(反向锚 —— OTEL 必须绝迹)**

```python
def test_otel_path_is_retired_from_skill_docs():
    """OTEL 那条路已退役(Wave6 E1):文档里不得再教用户配那五件 env。

    留着 = 两套计量并存,后人不知信谁;而 telemetry.py 已删,照文档跑会直接 ImportError。
    """
    skill = (SKILLS / "scan-market" / "SKILL.md").read_text(encoding="utf-8")
    stages = (SKILLS / "scan-market" / "STAGES.md").read_text(encoding="utf-8")
    for doc, nm in ((skill, "SKILL.md"), (stages, "STAGES.md")):
        assert "trace.telemetry" not in doc, f"{nm} 仍在教已删除的 telemetry CLI"
        assert "CLAUDE_CODE_ENABLE_TELEMETRY" not in doc, f"{nm} 仍在教 OTEL env"
    assert "usage_harvest" in stages, "STAGES 计量节必须改记 usage_harvest 为唯一正典"
```

- [ ] **Step 3: 改 SKILL.md**

①删掉 `:36` 整条 OTEL 前置 bullet。
②`:125-131` 的收尾步骤合并为**一条命令链**(T3① 第一期,省主会话往返):

```bash
uv run --no-sync python -m autoresearch.scan.assemble <date> && \
uv run --no-sync python -m autoresearch.scan.gates gate4 <date> && \
uv run --no-sync python -m autoresearch.trace.usage_harvest --session <sessionId> --out reports/scan/<run_id>/token_usage.md
```
并在说明里点明:**三条一次跑完再播 CP7**,不要一条一个来回。

③CP7 段把「token 真计量」的产物路径与「真表缺席=未计量」的口径写清(与 Task 6 的报告文案一致)。

- [ ] **Step 4: 改 STAGES.md:263-267**

整节改写为:

```markdown
## 计量与跨层校准(usage_harvest 已实跑;OTEL 路已退役)

- **token 真计量(唯一正典)**:`python -m autoresearch.trace.usage_harvest --session <sessionId> --out reports/scan/<run>/token_usage.md`。逐 subagent 真 usage,按 message.id 去重(流式会重复落行,实测不去重虚报一倍),按计价倍率加权(cache读 ×0.1 / 5m写 ×1.25 / 1h写 ×2)+ 分模型汇总。追溯补账用 `--transcripts <glob>`。
  - **覆盖声明**:只覆盖 subagent,**主会话自身不在内**;表里没有的不等于没花钱。
  - 2026-07-24 追溯首读:billed 22.4M / 加权 5.49M / 输出 716.6k / 50 agent;同一份报告的旧「字节÷2.8」估算写 ~183.6k = **低估 30 倍**,且分布相反(L3 真占 7.8% 非 37%)。
- **OTEL 遥测**:`trace/telemetry.py` 建成 3 周零生产调用点,已于 2026-07-27 删除;不要再配那五件 env。
```
(其余「跨层校准 / 触价校准 / 注入分层」三条原样保留。)

- [ ] **Step 5: 跑测试 + 全量**

```bash
uv run --no-sync python -m pytest tests/test_agent_defs.py tests/test_skill_docs_refs.py -q
set -o pipefail; uv run --no-sync python -m pytest -q > /tmp/w6_t13.txt 2>&1; echo "EXIT=$?"; tail -3 /tmp/w6_t13.txt
```

- [ ] **Step 6: 提交**

```bash
git add .claude/skills/scan-market/ tests/
git commit -m "docs(skill): OTEL 段退役 + 计量节改记 usage_harvest + 收尾三命令合并

telemetry.py 已删,文档还在教配五件 env = 照做直接 ImportError。
STAGES 计量节记 07-24 追溯首读(30× 低估)。收尾 assemble/gate4/usage_harvest
合成一条链,省主会话往返(T3① 第一期)。

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

## Task 14: open proposals 关账(E2)

**为什么**:19 条 open 提案里有一批**已经有裁决只是没记状态**,看板堆成摆设就没人看了。只关「裁决已明确」的,取证中的一律不动。

**Files:**
- Modify: `context/knowledge/proposals.jsonl`(**只经 `feedback_store.set_proposal_status`,不手改文件**)

**关账清单(逐条附依据)**:

| pid | 处置 | 依据 |
|---|---|---|
| `pr_20260624_001` | rejected | cap_floor 30→20 亿,`pr_20260714_003`/`pr_20260717_002` 两次影子回放均证收益微弱 |
| `pr_20260714_003` | resolved | 它本身就是 `_001` 的裁决建议(拒绝),裁决已执行 |
| `pr_20260717_002` | resolved | 同上,第二个证据点 |
| `pr_20260712_001` | rejected | OW 三门疑过紧 → `pr_20260714_002` 已裁「不松门」;门价值 +3.3pp 实证 |
| `pr_20260714_002` | resolved | 裁决本身已完成(不松门,改左尾口径论证) |
| `pr_20260717_004` | resolved | progress.py 误报 → 批1 直播已用「产物即完成」语义替代(模块删除待 R3 验收后) |
| `pr_20260721_001` | resolved | config-echo 探针 → 07-24 实测 `user_config_echo.json` 非空且 7 项 effort 齐全,07-21 事故未复发 |
| `fb_20260704_001` | resolved | 「token 太大」→ Wave6 §3 真计量 + 三把刀即答案 |

**保持 open(取证中,勿动)**:`pr_20260714_006/007`、`pr_20260716_003`(intel 三连——本批 Task 1-3 是**修法**,活体验收在 07-28,验完再关)、`pr_20260725_001`(event 路,~08-08 裁决)、`pr_20260725_002/003`(guard 退休提名)、`pr_20260625_001/002`、`pr_20260702_002`、`pr_20260712_002`、`pr_20260717_003/005`(005 由 Task 11a 修,同样等活体)。

- [ ] **Step 1: 确认 status 取值合法**

```bash
uv run --no-sync python -c "
from autoresearch.learning import feedback_store as fs
import inspect; print(inspect.getdoc(fs.set_proposal_status))
"
```
docstring 写 `{open,approved,rejected,applied}`,但**实际数据里已有 6 条 `resolved`** —— 以数据既有惯例为准用 `resolved`;若 CLI/校验拒绝,改用 `rejected`/`applied` 中语义最近的一个,**不要绕过 API 手改 jsonl**。

- [ ] **Step 2: 执行关账**

```bash
uv run --no-sync python -c "
from autoresearch.learning.feedback_store import set_proposal_status as s
for pid, st in [('pr_20260624_001','rejected'), ('pr_20260714_003','resolved'),
                ('pr_20260717_002','resolved'), ('pr_20260712_001','rejected'),
                ('pr_20260714_002','resolved'), ('pr_20260717_004','resolved'),
                ('pr_20260721_001','resolved')]:
    print(pid, st, s(pid, st))
"
```

- [ ] **Step 3: 核对 open 计数**

```bash
uv run --no-sync python -c "
import json
from collections import Counter
c = Counter(json.loads(l).get('status') for l in open('context/knowledge/proposals.jsonl'))
print(c); print('open:', c['open'])
"
```
Expected: open 从 19 降到 ≤13(fb_20260704_001 在 feedback.jsonl 里,另处理或留待 retro)

- [ ] **Step 4: 提交**

```bash
git add context/knowledge/proposals.jsonl
git commit -m "chore(register): 7 条已裁决提案关账 —— 看板堆成摆设就没人看

cap_floor 家族三条(两次影子回放证收益微弱)、OW 三门两条(已裁不松门)、
progress 误报(批1 直播已替代)、config-echo(07-24 实测未复发)。
取证中的一律不动:intel 三连等 07-28 活体验收、event 路等 08-08。

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

## 收尾:批 B 验收

- [ ] **全量绿**:`uv run --no-sync python -m pytest -q` exit 0,计数 ≥ 1602 + 新增。
- [ ] **ruff 净**:`uv run --no-sync ruff check autoresearch tests`。
- [ ] **workflow 语法探针绿**:`uv run --no-sync python -m pytest tests/test_workflow_js_syntax.py -q`。
- [ ] **变异抽查(至少抽 3 个新守卫)**:分别把被守内容删掉/改坏,确认对应测试**真的变红**,再恢复。抽查对象建议:Task 1 的 review 接线段、Task 4 的 `model: 'haiku'`、Task 9 的常量复用。
- [ ] **写实施记录**:`docs/plans/2026-07-27-wave6-batchB-record.md`,格式照 `2026-07-25-wave5-batch2-record.md`(commit 表 + 每项一段「实际发现与 spec 的偏差」)。
- [ ] **07-28 活体验收清单**(交给批 A):intel 零 URL 应降为 0、intel 限频行应出现、`chk_slim_size` 应 11/11 True、`chk_blind_pass` 应转 True、CP7 分模型列里 gp 全 haiku、ensemble 若触发应见「同档早止」、pack 出现 `today_slice`。

## 附录:实施顺序与依赖

无强依赖,建议顺序 = 上面的 1→14(价值密度递减,且 Task 2/3 共享 agent def 编辑、Task 6/7/8/13 共享计量主题)。

**唯一软依赖**:Task 8(删 telemetry)必须在 Task 13(改文档)之前或同批 —— 否则文档会有一段时间教用户跑一个已删除的 CLI。
