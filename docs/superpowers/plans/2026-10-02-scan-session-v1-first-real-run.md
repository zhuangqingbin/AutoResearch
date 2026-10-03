# session_v1 全扫首跑打通 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 2026-10-08(节后首个交易日)开盘前,让每日全 A 扫描在 session_v1 下真实跑通,并发布一份经 `verify-report --level full` 核验的报告;此后每晚照同一条路径跑。

**Architecture:** 不改任务图、评级、三门和 E6 规则。先修两处已复现的确定性缺陷,再按由便宜到贵的四级阶梯真跑:哨兵空档 → 哨兵持仓 → 强制全扫(缩卡)→ 生产全扫。前三级是演练,在隔离工作树里跑,产物不进生产账本;任一级失败就停下来修,修完起新 run,不在失败的 run 上补。

**Tech Stack:** Python 3.13 · uv · pytest;Claude Code 2.1.286(Agent 工具、PreToolUse hook);tushare 数据湖。

**Spec:** 没有单独设计稿。依据三份现行文档:`docs/session-agent/README.md`(宿主循环)、`docs/research/2026-09-26-session-plan-vs-workflow-audit.md`(session_v1 与旧 Workflow 的已知差异)、`docs/session-agent/acceptance.md`(验收口径)。配套稿:[Claude 边界重采与验收矩阵](2026-10-02-claude-boundary-recollect-and-acceptance-matrix.md)。

基准时间:2026-10-02 01:00(Asia/Shanghai)。主工作树 HEAD `431d5dc`,另有约 480 个未提交文件;本稿所有事实都是对这棵工作树实测的。

---

## 0. 现状与证据

| 事实 | 证据 |
|---|---|
| 旧 Workflow 起不来 | `.claude/workflows/scan-market.js:71` 无条件 `throw new Error('HOST_CAPABILITY_REQUIRED: …')`;`l4-stock.js`、`dossier-init.js` 同样带 `C4_LEGACY_GUARD` |
| session_v1 从没真跑过全扫 | `reports_claude/_acceptance/proofs/claude/` 下只有 `stock-research`;`acceptance-status` 的 `missing_real_sessions` 含 `scan-market:claude:*` 全部 4 项 |
| 最近三场全扫都没成功发布 | `context_claude/scan_runs/<run>/state.json`:09-27 `FAILED`、09-28 `INTERRUPTED`、09-29 `INTERRUPTED`,后两场 `last_reliable_checkpoint=gate4` |
| GATE4 的失败是检查器误判 | 对三场真实产物只读重跑 `self_review.brief_lint`,三场都只有一条 fail:`brief③相对BUY与决策文件不同源`,brief 侧代码 `['无']`,决策文件侧分别是 603558 / 300981 / 603893。三份决策文件都是 `mode=active`、`buys[0].tier=R` |
| 误判的根因 | `brief._buy_lines` 把 R 级行的前导字形从 ✅ 换成 🟥;`self_review.py:1789` 仍按「行里有 🕶 或 ✅」找那一行,找不到就当 brief 没印代码 |
| session_v1 的 GATE4 用的是同一个检查 | `session_agent/domain_ops.py:2520` `scan_gate4` 调 `scan.gates.gate4`;不通过即 `raise`,任务失败,run 停在 BLOCKED |
| R 级是常态不是偶发 | 三场真扫全部是 R 级强制相对 BUY;A 级结构性为 0 的原因见 `docs/research/scan-negative-results.md` |
| 无人值守请求停在旧版本 | `scan/scan_run.py:120` `build_headless_request` 产出 `schema_version: 1`;交互样例 `docs/session-agent/examples/scan.request.json` 是 v4。`decision_frame.py` 只在 v≥3 时写带日历来源证据的 frame |
| 一只票的终失败会停掉整场 | `domain_ops.scan_l4_complete` 要求任务簿全部 `SUCCEEDED`,否则 `raise`;`docs/session-agent/local-recovery.md` 写明「本批不拆除既有全局汇合点」。旧 Workflow 的盲卡兜底在 session_v1 没有,而旧 Workflow 现在也起不来 |
| 前向观察的冻结树不可用 | `.worktrees/research-quality-evidence-efficiency`:`self_review.py:1790` 同一行未修、`scan-market.js` 同样带守卫、`lake/` 为空、`context_codex/` 下没有 `scan_runs`(从没在里面跑过扫描) |

本稿的两处代码改动已在主工作树源码的临时副本上先红后绿跑通,并用修后的代码对上面三场真实产物重放过(三场 fail 清零)。补丁放在 `docs/superpowers/plans/2026-10-02-session-v1-patches/` 的 `01-*`、`02-*`,对当前工作树 `git apply --check` 通过。主工作树的源码一行未改。

## 1. 需要你先裁定的事

每项给了建议。不裁也不阻塞 Task 1–3;D2、D4 不裁则按建议走。

| # | 问题 | 选项 | 建议 |
|---|---|---|---|
| D1 | 60 日前向观察 `QUALITY_FORWARD_20261008_V1` 按原计划 10-08 起算吗? | a. 照原协议在冻结树里跑;b. 起算前中止 V1,等本稿 Task 8 通过后以修好的提交重登记 V2,起始日顺延;c. 暂不启动 | **b**。冻结树带着 🟥 误判,R 级 BUY 日会全部记成失败;树里从没跑过扫描,湖也是空的。协议属 Codex 引擎,登记状态我没有跨引擎去读,重登记由 Codex 会话执行 |
| D2 | 演练在哪跑? | a. 隔离工作树(本稿 Task 3);b. 直接在生产根 | **a**。发布到生产根的 run 会被夜间 `outcome` 收进 `recommendations.csv`,强制全扫演练还会改覆盖池;代码里没有「演练不入账」的开关 |
| D3 | 单票终失败 = 整场不发布,接受吗? | a. 首批真跑先接受,记录出现频次;b. 先开发「失败票降级为盲卡」再跑 | **a**。b 要改冻结计划的依赖形状和证据分母,没有真跑数据前不值得动。瞬时类失败 runner 已会自动重试一次 |
| D4 | 生产首场用哪天的数据? | a. 节中用 09-30 数据跑(买入日 T+1=10-08 收盘、卖出日 T+2=10-09 开盘,报告对节后首日可执行);b. 等 10-08 当晚 | **a**,10-08 当晚再照常跑当日 |
| D5 | 日常走哪种执行器? | a. 交互会话驱动(mailbox);b. `claude -p` 无人值守(headless) | 先把 **a** 跑通。b 属 09-26 稿 C 线,另排;本稿 Task 2 只把它的请求版本修齐,免得带着旧形状上线 |

## Global Constraints

- 每个新 shell 第一条命令:`export AUTORESEARCH_ENGINE=claude`。只读写 `context_claude/`、`reports_claude/`;不读 `context_codex/`、`reports_codex/`。
- 不修改、不删除、不切换 `.worktrees/research-quality-evidence-efficiency`。
- 不改评级阈值、三门、主尺 `gap_c1_o2`、E6 规则;不放宽 GATE4。Task 1 改的是检查器**找到那一行的方式**,判据(两边六位代码集合相等,含空对空)一字不动。
- 本稿不碰边界策略文件:`contracts/research_boundary.py` 的 `POLICY_FILES` 九个、`.claude/settings.json`、`.claude/agents/*`。
- begin 之后不改代码。run 中途改了代码,该 run 只能当 shakedown,修完起新 run。
- 子 agent 完成并 `mailbox complete` 之后,不再给它发消息。
- 演练只在 Task 3 建的隔离工作树里改配置;主工作树的 `scan_config.jsonc`、`pinned.jsonc` 不为演练改。
- 改 `scan_config.jsonc` 后跑 `uv run --no-sync python -m autoresearch.scan.config_standard`,必须 `0 条违规`。
- 测试两个引擎都跑。Claude 引擎下 `tests/session_agent`、`tests/forensics` 有既有基线红(夹具写死 codex),判据是不新增红。
- 主工作树禁止 `git reset --hard`、`git clean`、对真实索引 `git add -A`。提交要你点头。
- 交付只引用 `verify-report` 的机器结果:`report_covered`、`publication_ok`、`orchestration_verified`、`completeness_ok` 与 `missing`、`diffs`。

## Review Focus

1. **③ 行再出第四种前导字形。** 期望:谁加了新前导而没登记常量,测试先红。由 Task 1 的 `test_relative_buy_line_locator_is_same_source_as_renderer` 钉住(渲染器能产出的 18 种 ③ 行逐一过定位器)。
2. **修定位把判据修没了。** 期望:R 级行里的代码被换掉仍然 fail,并同时点出两边代码;GATE4 仍然拦。由 Task 1 的 `test_forced_tier_code_mismatch_is_still_fail`、`test_forced_tier_fail_reaches_gate4_only_when_codes_differ` 钉住。
3. **演练产物漏进生产账本。** 期望:演练前后生产根的报告目录、结果账本、覆盖池逐字节不变。由 Task 3 Step 6 取指纹、Task 7 Step 9 比对。
4. **子 agent 在绑定前的调用被拒,情报检索因此缩水。** 期望:被拒的调用不计入检索额度,agent 重试后拿到与预算相符的检索次数。由 Task 6 Step 5 数拒绝次数与实际检索次数,Step 6 给出换执行器的判定线。
5. **一只票终失败,整场停在 L5 之前。** 期望:操作者知道该怎么收场,不在同一个 run 上硬补。由 Task 9 的处置表给出唯一做法。

## File Structure

| 文件 | 动作 | 责任 |
|---|---|---|
| `autoresearch/scan/brief.py` | 改 | 新增 ③ 行三种前导常量与 `relative_buy_line()`;`_buy_lines` 改用常量(渲染逐字节不变) |
| `autoresearch/scan/self_review.py` | 改 | `brief_lint` ⑥ 改调 `brief.relative_buy_line` |
| `tests/scan/test_brief.py` | 改 | 定位器与渲染器同源的 19 个用例 |
| `tests/scan/test_self_review_brief.py` | 改 | 分级行的同源、篡改、到门三组用例 |
| `autoresearch/scan/scan_run.py` | 改 | `build_headless_request` 升到 schema v4 |
| `tests/scan/test_scan_run.py` | 改 | 无人值守请求与文档样例同源 |
| `.claude/skills/scan-market/SKILL.md` | 改 | 「入口与交付」节补 session_v1 起扫的三条纪律(Task 10) |
| `docs/ops/scan-ops.md` | 改 | 新增「session_v1 手动全扫」节(Task 10) |
| `docs/session-agent/acceptance.md` | 改 | Claude 行更新(Task 10) |
| `docs/research/2026-10-02-scan-session-v1-first-run-readout.md` | 新 | 每一级的 run_id、读数、缺陷与处置(Task 5 起逐级追加) |

---

### Task 1: ③ 行定位口径与渲染器同源(修 GATE4 误判)

**Files:**
- Modify: `autoresearch/scan/brief.py:626-673`
- Modify: `autoresearch/scan/self_review.py:1786-1796`
- Test: `tests/scan/test_self_review_brief.py`(文件末尾追加)
- Test: `tests/scan/test_brief.py`(文件末尾追加)

**Interfaces:**
- Consumes: 无。
- Produces: `brief.REL_TAG_SHADOW`、`brief.REL_TAG_ACTIVE`、`brief.REL_TAG_FORCED`(三个 `str` 常量);`brief.relative_buy_line(text: str) -> str | None`。

- [ ] **Step 1: 只读复现(三场真实产物)**

```bash
cd /Users/qingbin.zhuang/Personal/TradingAgents
AUTORESEARCH_ENGINE=claude uv run --no-sync python - <<'EOF'
from pathlib import Path
from autoresearch.scan import self_review

RUNS = [("20260927T130920309974Z", "20260924-0927_2232"),
        ("20260928T123508501162Z", "20260928-0928_2148"),
        ("20260929T130649377424Z", "20260929-0929_2213")]
for run_id, folder in RUNS:
    staging = Path("context_claude/scan_runs") / run_id / "staging"
    report = Path("reports_claude/scan") / folder
    for scan in sorted(p for p in staging.iterdir() if p.is_dir()):
        fails = [r for r in self_review.brief_lint(report, scan) if r["severity"] == "fail"]
        print(run_id, scan.name, "fail_checks=", [r["check"] for r in fails])
EOF
```

Expected(修复前):三行,每行 `fail_checks= ['brief③相对BUY与决策文件不同源']`。`brief_lint` 是纯读函数,这一步不写任何文件。

- [ ] **Step 2: 写失败测试**

优先用补丁:

```bash
git apply --check docs/superpowers/plans/2026-10-02-session-v1-patches/01-tests-relative-buy-locator.patch \
  && git apply docs/superpowers/plans/2026-10-02-session-v1-patches/01-tests-relative-buy-locator.patch
```

`--check` 不过(文件已被别人改动)时,手工把下面两段追加到对应文件末尾。

`tests/scan/test_self_review_brief.py` 末尾追加:

```python


# ───── ⑥ 续:E6 v4 分级行(2026-09-27/28/29 三场真扫的 GATE4 假 fail) ─────
#
# R 级把 ③ 行的前导字形从 ✅ 换成了 🟥(`brief._buy_lines`),而 ⑥ 仍按「行里有 🕶 或 ✅」找那一行
# → 找不到 → brief 侧代码集合恒空 → 与决策文件 `buys[].code` 对不上 → fail → GATE4 毙掉一场
# 已经出了 BUY 的扫描。定位口径改为渲染器导出的 `brief.relative_buy_line`(同一组前缀常量)。

def _tiered_decision(tier: str) -> dict:
    decision = _decision(mode="active", buys=1)
    decision["buys"][0].update(
        {"tier": tier, "basis": "relative_forced" if tier == "R" else "card_backed"})
    decision["tiering"] = True
    decision["tier_counts"] = {"A": int(tier == "A"), "R": int(tier == "R")}
    return decision


@pytest.mark.parametrize("tier", ["R", "A"])
def test_active_tiered_relative_buy_is_same_source(tmp_path, tier):
    scan = _scan(tmp_path, decision=_tiered_decision(tier))
    report = _publish(tmp_path, scan)
    text = (report / brief.BRIEF_FILENAME).read_text(encoding="utf-8")
    assert ("🟥" in text) == (tier == "R"), "夹具没渲染出本用例要测的前导字形"
    assert "600018" in text
    rows = self_review.brief_lint(report, scan)
    assert _E6_CHECK not in _checks(rows), rows
    assert not _fails(rows), _fails(rows)


def test_forced_tier_code_mismatch_is_still_fail(tmp_path):
    """修定位不能把判据修没:R 级行里的代码被换掉,仍然必须 fail 并同时点出两边的代码。"""
    scan = _scan(tmp_path, decision=_tiered_decision("R"))
    report = _publish(tmp_path, scan)
    path = report / brief.BRIEF_FILENAME
    text = path.read_text(encoding="utf-8")
    assert "🟥" in text and "600018" in text, "锚点没先出现,后面的篡改是空操作"
    path.write_text(text.replace("600018", "600188"), encoding="utf-8")
    hit = [r for r in self_review.brief_lint(report, scan) if r["check"] == _E6_CHECK]
    assert hit and hit[0]["severity"] == "fail"
    assert "600188" in hit[0]["detail"] and "600018" in hit[0]["detail"]


def test_forced_tier_fail_reaches_gate4_only_when_codes_differ(tmp_path):
    """端到端到门:R 级干净盘 GATE4 必过(09-29 真扫的形状);同一份盘改掉代码后 GATE4 必拦。"""
    scan = _scan(tmp_path, decision=_tiered_decision("R"))
    report = _publish(tmp_path, scan)
    _, gate, fails = _lint_then_gate4(report, scan)
    assert gate["ok"] is True, (gate, fails)
    path = report / brief.BRIEF_FILENAME
    path.write_text(path.read_text(encoding="utf-8").replace("600018", "600188"), encoding="utf-8")
    _, gate, fails = _lint_then_gate4(report, scan)
    assert gate["ok"] is False and any(r["check"] == _E6_CHECK for r in fails), (gate, fails)
```

`tests/scan/test_brief.py` 末尾追加:

```python


# ───────── ③ 行定位口径与渲染器同源(2026-10-02;09-27/28/29 GATE4 假 fail 的根因) ─────────
#
# `self_review.brief_lint` ⑥ 要在 brief 正文里找到「相对 BUY 那一行」。它过去按行内字形(🕶/✅)
# 自己找,渲染器把 R 级换成 🟥 之后就找不到了。现在定位函数 `brief.relative_buy_line` 与
# `_buy_lines` 共用同一组前缀常量;本组用例把「渲染器能产出的每一种 ③ 行」都过一遍定位器,
# 谁再给 ③ 行加第四种前导而不登记常量,这里先红。

def _relative_doc(scan, *, mode, tier, shape):
    path = scan / brief.DECISION_FILENAME
    if shape == "absent":
        path.unlink()
        return
    doc = _decision(mode=mode, blocked=(shape == "blocked"))
    if tier is not None and shape == "buy":
        doc["buys"][0].update(
            {"tier": tier, "basis": "relative_forced" if tier == "R" else "card_backed"})
        doc["tiering"], doc["tier_counts"] = True, {"A": int(tier == "A"), "R": int(tier == "R")}
    path.write_text(json.dumps(doc, ensure_ascii=False), encoding="utf-8")


@pytest.mark.parametrize("mode", ["shadow", "active"])
@pytest.mark.parametrize("tier", [None, "A", "R"])
@pytest.mark.parametrize("shape", ["buy", "blocked", "absent"])
def test_relative_buy_line_locator_is_same_source_as_renderer(scan, mode, tier, shape):
    _relative_doc(scan, mode=mode, tier=tier, shape=shape)
    md = brief.build(scan, run_folder=_RUN)["markdown"]
    rendered = [ln for ln in md.splitlines() if "relative BUY**" in ln or "relative BUY(" in ln]
    assert len(rendered) == 1, rendered
    assert brief.relative_buy_line(md) == rendered[0]
    codes = [c for c in ("600018",) if c in rendered[0]]
    assert bool(codes) == (shape == "buy"), rendered[0]


def test_relative_buy_line_returns_none_when_the_line_is_gone():
    assert brief.relative_buy_line("**③ 结论**\n- **研究评级分布**(…BUY 见下一行…)\n") is None
```

- [ ] **Step 3: 跑测试,确认红**

```bash
AUTORESEARCH_ENGINE=codex uv run --no-sync python -m pytest -q \
  tests/scan/test_self_review_brief.py tests/scan/test_brief.py -p no:cacheprovider
```

Expected: `22 failed, 103 passed`。红的是 3 个 `test_self_review_brief` 用例(`…same_source[R]`、`…code_mismatch_is_still_fail`、`…reaches_gate4…`)和 19 个 `test_brief` 用例(`AttributeError: … has no attribute 'relative_buy_line'`)。`…same_source[A]` 在旧代码上本来就绿,它是 A 级的回归锁。

- [ ] **Step 4: 实现**

```bash
git apply --check docs/superpowers/plans/2026-10-02-session-v1-patches/01-impl-relative-buy-locator.patch \
  && git apply docs/superpowers/plans/2026-10-02-session-v1-patches/01-impl-relative-buy-locator.patch
```

手工改时,三处:

`autoresearch/scan/brief.py`,在 `def _buy_lines(` 之前插入:

```python
#: ③ 段「相对 BUY 行」的三种前导标签 —— 渲染器 `_buy_lines` 与 `self_review.brief_lint` ⑥ 的
#: **唯一**定位口径。字形只在这里定义:R 级曾把 ✅ 换成 🟥 而 lint 仍按旧字形找行,于是
#: 2026-09-27/28/29 连续三场把「已出 BUY」判成「brief 没印代码」→ GATE4 假 fail。
#: 要加第四种前导,先加常量并进 `_REL_LINE_PREFIXES`,再改渲染。
REL_TAG_SHADOW = "🕶 **影子 relative BUY(非正式·不执行)**"
REL_TAG_ACTIVE = "✅ **relative BUY**"
REL_TAG_FORCED = "🟥 **relative BUY**"
_REL_LINE_PREFIXES = tuple(f"- {tag}" for tag in (REL_TAG_SHADOW, REL_TAG_ACTIVE, REL_TAG_FORCED))


def relative_buy_line(text: str) -> str | None:
    """brief 正文里 ③ 段的相对 BUY 行;没有这一行 → None。

    未生成 / BLOCKED / 正常三个出口渲染的都是这一行(只是冒号后的内容不同),所以调用方
    拿到行之后自己取代码:BLOCKED 与未生成行里没有六位代码,集合为空。
    """
    return next((line for line in str(text).splitlines()
                 if line.startswith(_REL_LINE_PREFIXES)), None)
```

同文件 `_buy_lines` 内,把

```python
    tag = ("🕶 **影子 relative BUY(非正式·不执行)**"
           if not active else "✅ **relative BUY**")
```

换成

```python
    tag = REL_TAG_ACTIVE if active else REL_TAG_SHADOW
```

把

```python
        tag = tag.replace("✅", "🟥") + " · **R 级·卡面无买点·强制相对(裁定①)**"
```

换成

```python
        tag = (REL_TAG_FORCED if active else tag) + " · **R 级·卡面无买点·强制相对(裁定①)**"
```

并把紧挨着的注释里「`str.replace` 找不到 ✅ 是无操作的 no-op」一句改成「所以只在 active 期换成 `REL_TAG_FORCED`」。渲染结果逐字节不变:active 期 `"✅ **relative BUY**".replace("✅","🟥")` 就是 `REL_TAG_FORCED`,影子期两种写法都保持 🕶。

`autoresearch/scan/self_review.py`,`brief_lint` 的 ⑥ 段,把

```python
        buy_line = next((ln for ln in text.splitlines()
                         if "relative BUY" in ln and ("🕶" in ln or "✅" in ln)), None)
```

换成

```python
        # 定位口径取渲染器自己的(`brief.relative_buy_line`),不在这里另认字形 ——
        # 2026-09-27/28/29:R 级行首是 🟥,本处只认 🕶/✅,三场已出 BUY 的扫描被判不同源。
        buy_line = _brief.relative_buy_line(text)
```

- [ ] **Step 5: 跑测试,确认绿**

```bash
AUTORESEARCH_ENGINE=codex uv run --no-sync python -m pytest -q \
  tests/scan/test_self_review_brief.py tests/scan/test_brief.py -p no:cacheprovider
```

Expected: `125 passed`。

- [ ] **Step 6: 真实三场重放**

重跑 Step 1 的脚本。Expected:三行都是 `fail_checks= []`。

- [ ] **Step 7: 回归与配置 lint**

```bash
set -o pipefail
AUTORESEARCH_ENGINE=codex uv run --no-sync python -m pytest -q tests/scan -p no:cacheprovider \
  -o faulthandler_timeout=120 | tail -3
uv run --no-sync python -m autoresearch.scan.config_standard | tail -1
```

Expected: `0 failed`(源码副本上实测 `3255 passed, 4 skipped`;主仓里依赖真实产物的 4 个用例会跑,passed 数只多不少);`scan_config 标准 lint:0 条违规`。

- [ ] **Step 8: Claude 引擎再跑一遍这两个文件**

```bash
AUTORESEARCH_ENGINE=claude uv run --no-sync python -m pytest -q \
  tests/scan/test_self_review_brief.py tests/scan/test_brief.py -p no:cacheprovider
```

Expected: `125 passed`。

- [ ] **Step 9: 提交(你点头后)**

```bash
git add autoresearch/scan/brief.py autoresearch/scan/self_review.py \
  tests/scan/test_brief.py tests/scan/test_self_review_brief.py
git commit -m "fix(scan): brief ③ 行定位口径与渲染器同源,R 级 🟥 行不再被 GATE4 误判"
```

---

### Task 2: 无人值守请求升到 schema v4

**Files:**
- Modify: `autoresearch/scan/scan_run.py:120-156`
- Test: `tests/scan/test_scan_run.py`(文件末尾追加)

**Interfaces:**
- Consumes: `docs/session-agent/examples/scan.request.json`(v4 交互样例,作为同源比对的另一端)。
- Produces: `scan_run.build_headless_request(date)` 返回 v4 请求,除 `analysis_date`、`host_profile`、`force_full` 外与样例逐键相等。

- [ ] **Step 1: 写失败测试**

```bash
git apply --check docs/superpowers/plans/2026-10-02-session-v1-patches/02-tests-headless-request-v4.patch \
  && git apply docs/superpowers/plans/2026-10-02-session-v1-patches/02-tests-headless-request-v4.patch
```

手工追加到 `tests/scan/test_scan_run.py` 末尾:

```python


def test_headless_request_matches_the_documented_scan_request_contract():
    """无人值守请求与文档样例必须是同一份契约(只有宿主与日期不同)。

    2026-09-30 请求升到 schema v4(research_context / card_research_profile / 宏观与行业 profile),
    交互样例跟着升了,这里的生成器还停在 v1 → 无人值守场的 DecisionFrame 没有日历来源证据。
    """
    example = json.loads(
        (Path(__file__).resolve().parents[2] / "docs/session-agent/examples/scan.request.json")
        .read_text(encoding="utf-8"))
    request = scan_run.build_headless_request(DATE)
    assert request["schema_version"] == example["schema_version"] == 4
    assert set(request) == set(example)
    varying = {"analysis_date", "host_profile", "force_full"}
    assert {k: v for k, v in request.items() if k not in varying} == {
        k: v for k, v in example.items() if k not in varying}
```

- [ ] **Step 2: 跑测试,确认红**

```bash
AUTORESEARCH_ENGINE=codex uv run --no-sync python -m pytest -q tests/scan/test_scan_run.py -p no:cacheprovider
```

Expected: `1 failed, 48 passed`(新用例在 `assert request["schema_version"] == … == 4` 处失败)。

- [ ] **Step 3: 实现**

```bash
git apply --check docs/superpowers/plans/2026-10-02-session-v1-patches/02-impl-headless-request-v4.patch \
  && git apply docs/superpowers/plans/2026-10-02-session-v1-patches/02-impl-headless-request-v4.patch
```

手工改 `build_headless_request` 的返回值:`"schema_version": 1` 改成 `4`;在 `"predecessor_run_id": None,` 之后加:

```python
        # 与交互样例 docs/session-agent/examples/scan.request.json 同一份契约(测试锁同源):
        # v3 起 DecisionFrame 带日历来源证据,v4 起宏观/行业候选 profile 显式取缺省。
        "card_research_profile": "single-stage-v1",
        "research_context": {"venue": "XSHG", "usage": "scan", "calendar_source_path": None},
        "macro_research_profile": "serial21",
        "macro_optional_products": [],
        "sector_brief_profile": "legacy",
```

- [ ] **Step 4: 跑测试,确认绿**

```bash
for eng in codex claude; do
  AUTORESEARCH_ENGINE=$eng uv run --no-sync python -m pytest -q tests/scan/test_scan_run.py \
    tests/session_agent/test_headless_claude.py tests/session_agent/test_docs_examples.py -p no:cacheprovider | tail -1
done
```

Expected: 两个引擎都是 `102 passed`。

- [ ] **Step 5: 提交(你点头后)**

```bash
git add autoresearch/scan/scan_run.py tests/scan/test_scan_run.py
git commit -m "fix(scan): 无人值守请求升到 schema v4,与交互样例同源"
```

---

### Task 3: 冻结基线,建隔离演练根

**Files:**
- Create: `.worktrees/claude-scan-drill/`(git worktree,已被 `.gitignore:246` 忽略)
- Create: `context_claude/development/20261002-scan-first-run/production-before.txt`

**Interfaces:**
- Consumes: Task 1、Task 2 的改动;配套稿 Task 1(`accept-run`)若已实施则一并进快照。
- Produces: 分支 `drill/scan-session-v1`;隔离根 `.worktrees/claude-scan-drill`(它的 `context_claude/`、`reports_claude/`、`lake/` 都是自己的);生产指纹文件。

为什么是 git worktree:`autoresearch/common/workspace.py` 的三个根都是相对当前目录的路径(`Path("context_claude")` 等),换个目录跑就是另一套产物根;而 `begin` 冻结源码树要求当前目录是 git 工作树(普通拷贝会报 `SourceTreeError: source tree capture requires a readable Git worktree`)。

- [ ] **Step 1: 全量回归(开工基线)**

```bash
cd /Users/qingbin.zhuang/Personal/TradingAgents
S=context_claude/development/20261002-scan-first-run; mkdir -p "$S"
run() { n=$1; shift; AUTORESEARCH_ENGINE=codex uv run --no-sync python -m pytest -q "$@" \
  -p no:cacheprovider -o faulthandler_timeout=120 > "$S/shard_$n.log" 2>&1; echo "exit=$?" >> "$S/shard_$n.log"; }
run A tests/session_agent &
run B tests/scan &
run C tests/forensics tests/trace tests/contracts &
run D tests/common tests/analyze tests/research tests/news tests/test_agent_input_boundary_hook.py tests/test_agent_defs.py &
wait
for n in A B C D; do printf '%s ' $n; tail -2 "$S/shard_$n.log" | tr '\n' ' '; echo; done
```

Expected: 新增的红为零。开工时主工作树在 codex 引擎下已有两条红(10-02 全量实测 `2 failed, 8929 passed, 12 skipped`),都不是本稿引入的,由配套稿修:D 片的 `tests/common/test_workspace.py::test_no_bare_root_literals_in_source`(配套稿 Task 5)和 C 片的 `tests/forensics/test_acceptance_claims.py::test_old_complete_proofs_cannot_bypass_c4_loaded_host_gate`(配套稿 Task 0)。配套稿这两个任务先做了的话,四片都应是 `exit=0`。出现这两条以外的红,先修再往下走。

- [ ] **Step 2: 用临时索引做快照提交(不动真实索引和工作树)**

如果你已经把工作树提交了(建议顺序第 1 项),跳过本步,`SNAP=$(git rev-parse HEAD)`。

```bash
BEFORE="$(git status --short | shasum | cut -c1-16)"
SNAP_INDEX="$(mktemp -u)"
GIT_INDEX_FILE="$SNAP_INDEX" git read-tree HEAD
GIT_INDEX_FILE="$SNAP_INDEX" git add -A -- .
TREE="$(GIT_INDEX_FILE="$SNAP_INDEX" git write-tree)"
SNAP="$(git commit-tree "$TREE" -p HEAD -m 'snapshot: working tree for isolated scan drills (2026-10-02)')"
rm -f "$SNAP_INDEX"
AFTER="$(git status --short | shasum | cut -c1-16)"
[ "$BEFORE" = "$AFTER" ] && echo "working tree and index untouched" || echo "STOP: status changed"
git ls-tree -r --name-only "$TREE" | grep -E '^(lake|context_|reports_|\.env$|\.venv|\.worktrees)' | head -3
```

Expected: 打印 `working tree and index untouched`;最后一条 `grep` 无输出(被忽略的根没有进快照)。这组命令 10-02 已在主仓试跑到 `write-tree` 为止:状态不变,树里 1416 个文件,等于已跟踪加未跟踪文件数。

- [ ] **Step 3: 建分支与工作树**

```bash
git branch drill/scan-session-v1 "$SNAP"
git worktree add .worktrees/claude-scan-drill drill/scan-session-v1
ln -s /Users/qingbin.zhuang/Personal/TradingAgents/.venv .worktrees/claude-scan-drill/.venv
cp -cR lake .worktrees/claude-scan-drill/lake
```

`cp -c` 在 APFS 上是克隆,不占额外空间;别用符号链接代替——`.gitignore` 里是 `lake/`,只匹配目录,链接会被当成未跟踪文件,而源码树冻结遇到符号链接会拒绝。

- [ ] **Step 4: 自检隔离根**

```bash
cd .worktrees/claude-scan-drill
git status --short | wc -l
AUTORESEARCH_ENGINE=claude uv run --no-sync python -c "
import autoresearch, os
print(autoresearch.__file__)
print('TUSHARE_TOKEN', bool(os.environ.get('TUSHARE_TOKEN')))"
ls -d context_claude reports_claude 2>/dev/null | wc -l
du -sh lake | cut -f1
cd /Users/qingbin.zhuang/Personal/TradingAgents
```

Expected: `0`;路径在 `.worktrees/claude-scan-drill/autoresearch/` 下;`TUSHARE_TOKEN True`(`.env` 由 `find_dotenv(usecwd=True)` 向上找到主仓的);`0`(隔离根还没有任何产物);`lake` 约 4.8G。

- [ ] **Step 5: 隔离根里再跑一次 Task 1 的两个测试文件**

```bash
cd .worktrees/claude-scan-drill
AUTORESEARCH_ENGINE=codex uv run --no-sync python -m pytest -q \
  tests/scan/test_self_review_brief.py tests/scan/test_brief.py -p no:cacheprovider | tail -1
cd /Users/qingbin.zhuang/Personal/TradingAgents
```

Expected: `125 passed`。不是这个数,说明快照里没带上 Task 1,回到 Step 2。

- [ ] **Step 6: 取生产根指纹**

```bash
cd /Users/qingbin.zhuang/Personal/TradingAgents
F=context_claude/development/20261002-scan-first-run/production-before.txt
{ ls reports_claude/scan | sort
  ls context_claude/scan_runs | sort
  shasum -a 256 reports_claude/scan/_ledger/recommendations.csv context_claude/knowledge/coverage_pool.json
} > "$F"; wc -l "$F"
```

Expected: 文件非空。Task 7 Step 9 拿它比对。

---

### Task 4: 宿主就绪检查(每个真跑会话的开头都做)

**Files:**
- Create: `context_claude/_acceptance/requests/scan-<标签>-<分析日>.request.json`(在当前会话所在的根里)

**Interfaces:**
- Consumes: 环境变量 `CLAUDE_CODE_SESSION_ID`、`CLAUDE_PID`(Claude Code 2.1.286 提供)。
- Produces: 一份 v4 请求文件;后续任务用变量 `REQ` 指它。

演练会话必须是在 `.worktrees/claude-scan-drill` 目录里**新开**的 Claude Code 会话(hook 和 agent 定义按会话所在项目目录装载);生产首场是在仓库根新开的会话。

- [ ] **Step 1: 会话比 hook、agent 定义新**

```bash
export AUTORESEARCH_ENGINE=claude
echo "session=$CLAUDE_CODE_SESSION_ID"
ps -o lstart= -p "$CLAUDE_PID"
stat -f '%Sm  %N' -t '%Y-%m-%d %H:%M:%S' .claude/settings.json .claude/agents/*.md \
  scripts/hooks/agent_input_boundary.py scripts/hooks/agent_input_boundary.sh \
  scripts/hooks/task_file_broker.py | sort | tail -1
```

Expected: 进程启动时刻晚于最后一行的文件修改时刻。否则整个退出 Claude Code 重开(`/clear` 不换进程)。

- [ ] **Step 2: 能力预检、扫描锁、凭证**

```bash
uv run --no-sync python -m autoresearch.session_agent.task_access preflight --orchestration session_v1
uv run --no-sync python -m autoresearch.scan.run_lock check; echo "lock_exit=$?"
uv run --no-sync python -c "import autoresearch, os; print('TUSHARE_TOKEN', bool(os.environ.get('TUSHARE_TOKEN')))"
```

Expected: JSON 里 `"status": "CONFIGURED_UNVERIFIED"`、`"runnable": true`;`lock_exit=0`;`TUSHARE_TOKEN True`。

- [ ] **Step 3: hook 加载探针(一次未绑定的受管角色调用必须被拒)**

预检的 `CONFIGURED_UNVERIFIED` 只说明配置文件在;hook 有没有被这个会话加载,要看一次真实调用。普通 run 里 hook 不留任何记录(`task_access.record_boundary_event` 只给专用 canary 演练记事件),所以在 begin 之前单独探一次。后台派一个不绑定任何任务的受管角色:

```text
Agent(subagent_type="l3-repair", prompt="Hook load check. Call the Read tool exactly once on README.md, then stop and reply with the raw tool result or the raw refusal text. Do not call any other tool.")
```

Expected: 它的回复里有 `AGENT_INPUT_BOUNDARY`(未绑定的受管角色,任何工具都会被 `scripts/hooks/agent_input_boundary.py` 拒)。它读出了 README 的内容,说明 hook 没有加载:不要 begin,整个退出 Claude Code 重开(新目录第一次开会话时接受目录信任确认),再从 Step 1 做起。这个探针不属于任何 run;选 `l3-repair` 是因为它是带 Read 的受管角色里最便宜的一个(effort medium)。10-02 在主仓会话里实测过一次:回复原文是 `PreToolUse:Read hook error: AGENT_INPUT_BOUNDARY: missing, invalid or stale task access binding (FileNotFoundError: …)`,一次工具调用,约 2 万 token、7 秒。

- [ ] **Step 4: 生成请求文件**

`LABEL`、`DATE`、`FORCE_FULL` 由调用它的任务给值。

```bash
mkdir -p context_claude/_acceptance/requests
REQ="context_claude/_acceptance/requests/scan-$LABEL-$DATE.request.json"
python3 - "$REQ" "$DATE" "$FORCE_FULL" <<'EOF'
import glob, json, os, sys
path, date, force = sys.argv[1], sys.argv[2], sys.argv[3] == "true"
session = os.environ["CLAUDE_CODE_SESSION_ID"]
transcripts = glob.glob(os.path.expanduser(f"~/.claude/projects/*/{session}.jsonl"))
assert len(transcripts) == 1, transcripts
request = {
    "schema_version": 4, "kind": "scan-market", "requested_mode": "AUTO",
    "analysis_date": date, "subject": None, "peers": [], "asset_type": None, "name": None,
    "force_full": force,
    "host_profile": {
        "schema_version": 1, "engine": "claude", "session_ref": session,
        "deterministic_exec": True, "capture_binding": True, "inference_handoff": True,
        "safe_resume": None, "independent_context": True, "native_dispatch": True,
        "web_search": True, "web_fetch": True,
        "observed_model": None, "observed_effort": os.environ.get("CLAUDE_EFFORT"),
        "evidence_refs": [
            f"transcript-file:{transcripts[0]}",
            "agent-definition:.claude/agents/l4-card.md#tools=Read,Grep,Glob,Write,WebSearch,WebFetch",
            "agent-definition:.claude/agents/l4-intel.md#tools=Write,WebSearch,WebFetch",
        ],
    },
    "predecessor_run_id": None,
    "card_research_profile": "single-stage-v1",
    "research_context": {"venue": "XSHG", "usage": "scan", "calendar_source_path": None},
    "macro_research_profile": "serial21", "macro_optional_products": [],
    "sector_brief_profile": "legacy",
}
json.dump(request, open(path, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
print(path)
EOF
```

`observed_model` 写 `None`:请求里只写本次真实观测到的值,主会话模型 id 环境里没有就留空,不从配置推断。`transcript-file:` 只能有一条,它决定主会话 transcript 的登记(`host_evidence.register_main_context`)。

---

### 宿主循环(Task 5–8 共用)

下面这段是每一级真跑的固定做法,对应 `docs/session-agent/README.md`「扫描 runner + mailbox 宿主循环」。

**begin 与起 runner:**

```bash
uv run --no-sync python -m autoresearch.session_agent begin --orchestration session_v1 \
  --request-file "$REQ" --kind scan-market --mode AUTO --date "$DATE"
# 从输出 JSON 取 run_id:
RUN_ID=<上一条输出的 run_id>
uv run --no-sync python -m autoresearch.trace.detach --run-id "$RUN_ID" --key session-runner \
  --wait-seconds 5 --shell "AUTORESEARCH_ENGINE=claude uv run --no-sync python -m autoresearch.session_agent run --run-id $RUN_ID --executor mailbox --max-parallel 8"
```

**循环(重复到 `RUNNER_EXITED`):**

```bash
uv run --no-sync python -m autoresearch.session_agent mailbox wait --run-id "$RUN_ID" --timeout 90
```

| `kind` | 做什么 |
|---|---|
| `REQUEST` | 用返回里的 `agent_tool` 和 `host_prompt` 原样后台派 `Agent(**<agent_tool>, prompt=<host_prompt>)`(`by_reference` 关着时 `host_prompt` 就是 `prompt` 全文);`agent_tool` 照抄,**不要**自己加 `model`,也不要把全 ID 换成 `opus`/`sonnet` 别名(2026-10-03 钉版后模型由 agent 定义 frontmatter 钉住,别名会随 Claude Code 升级漂移)。拿到 agentId **立刻**执行 `mailbox bind-access --run-id "$RUN_ID" --task-id <task_id> --attempt <attempt> --context-ref <agentId>`,然后继续 `wait` |
| 某个 Agent 返回 | 立刻 `mailbox complete --run-id "$RUN_ID" --task-id <task_id> --attempt <attempt> --context-ref <agentId>`。一个一个结,不攒批。Agent 报错改传 `--error "<原文>" --error-class TIMEOUT|CONNECTION|RATE_LIMIT` |
| `IDLE` | 再 `wait` |
| `complete` 返回 `ABANDONED` | 该 attempt 已超时作废,不手动重试,runner 自己会开新 attempt |
| `RUNNER_DEAD` | 同一条 detach 命令换新 key(`session-runner-2`…)重启 |
| `RUNNER_EXITED` | 读 `runner.outcome`:`finished`、`stop_reason`、`finish.canonical_path` |

四条纪律:不改 prompt、不代写研究;`bind-access` 之前 agent 的工具调用会被 hook 拒绝,这是设计行为,它会自己重试;`complete` 之后不再给那个 agent 发消息;`wait` 的 `--timeout` 不超过 100。

**收尾与核验(`finished=true` 时):**

```bash
CANONICAL=<runner.outcome.finish.canonical_path>
uv run --no-sync python -m autoresearch.session_agent verify-report \
  --report-path "$CANONICAL/report/brief.md" --expected-run-id "$RUN_ID" --level full
```

通过的判据:`report_covered`、`publication_ok`、`orchestration_verified`、`integrity_ok`、`completeness_ok` 五个都是 `true`,`compute_status` 是 `FULL`,`missing` 和 `diffs` 都是空数组。退出码 0 不算数,读 JSON。`--report-path` 给的是报告**文件**,`canonical_path` 本身是目录。

**没跑完(`stop_reason` 是 `BLOCKED` 或 `STALLED`)时:** 转 Task 9。

---

### Task 5: S1 哨兵空档演练(1 次推理)

**Files:**
- Modify(仅隔离根): `.worktrees/claude-scan-drill/.claude/skills/scan-market/scan_config.jsonc`、`…/pinned.jsonc`
- Create: `docs/research/2026-10-02-scan-session-v1-first-run-readout.md`(主仓,首次写入)

**Interfaces:**
- Consumes: Task 3 的隔离根;Task 4 的检查与请求生成。
- Produces: 一个 `run_mode=SENTINEL_EMPTY` 的已发布 run(`RUN_S1`);readout 的 S1 节。

这一级覆盖:begin 能力检查、源码树冻结、frame、prelude、market_view(一次 `macro-brief` 推理及其绑定)、GATE1 定模式、各 skip 任务、assemble、GATE4、usage、observe、finish、verify。没有 L3/L4。

- [ ] **Step 1: 在隔离根新开 Claude Code 会话,做 Task 4 Step 1–3**

这个目录是第一次开会话,弹出目录信任确认时接受。Task 4 Step 3 的探针在这里尤其要紧:新目录里 hook 没加载的话,后面的 run 会在没有边界约束的情况下跑完,而核验看不出来。

- [ ] **Step 2: 把隔离根配置改成「必进哨兵、无持仓」**

```bash
python3 - <<'EOF'
from pathlib import Path
p = Path(".claude/skills/scan-market/scan_config.jsonc"); s = p.read_text(encoding="utf-8")
old = '"auto_below": 0.03,'
assert s.count(old) == 1, s.count(old)
p.write_text(s.replace(old, '"auto_below": 1.0,'), encoding="utf-8")
Path(".claude/skills/scan-market/pinned.jsonc").write_text("[]\n", encoding="utf-8")
EOF
uv run --no-sync python -m autoresearch.scan.config_standard | tail -1
uv run --no-sync python -c "
from autoresearch.scan import user_config
print(user_config.load_pinned('2026-09-30')['kept'])"
pwd
```

Expected: `scan_config 标准 lint:0 条违规`;持仓列表打印 `[]`;`pwd` 以 `.worktrees/claude-scan-drill` 结尾。`pinned.jsonc` 整个文件被换成一个空数组(原文件的说明注释写在数组里面,演练根不需要它们;Task 7 Step 8 用 `git checkout --` 还原)。`sentinel.auto_below` 的类型是 fraction,1.0 合法,健康上涨占比不可能到 100%,所以哨兵判据必然成立;配置在 begin 时冻进 run 契约,报告里的 `config_hash` 与生产不同。

- [ ] **Step 3: 生成请求并起跑**

```bash
LABEL=sentinel-empty DATE=2026-09-30 FORCE_FULL=false
# 执行 Task 4 Step 4,然后执行「宿主循环」的 begin 与起 runner
```

- [ ] **Step 4: 等确定性前段过了再领推理请求**

```bash
python3 -c "
import json
tasks = json.load(open('context_claude/scan_runs/$RUN_ID/session/tasks.json'))['tasks']
print({k: tasks[k]['state'] for k in ('scan.frame', 'scan.prelude', 'scan.market_view', 'scan.gate1') if k in tasks})"
```

反复执行,直到 `scan.frame`、`scan.prelude` 都是 `SUCCEEDED`(前段约 10–20 分钟,零 LLM)。任一个变成 `FAILED` 或 `BLOCKED` 就在这里停,没有花任何推理额度,转 Task 9。未领取的请求要过角色预算的 4 倍(市场研判 60 分钟)才超时,等得起。

- [ ] **Step 5: 跑宿主循环**

预期只有一个 `REQUEST`:`task_id=scan.market_view`、`agent_type=macro-brief`。派发、绑定、完成,等 `RUNNER_EXITED`。

- [ ] **Step 6: 核验**

执行「宿主循环」的收尾与核验,再确认模式:

```bash
python3 -c "import json; print(json.load(open('$(find "$CANONICAL/capsule/products" -name run_mode.json | head -1)'))['mode'])"
```

Expected: 核验判据全过;模式 `SENTINEL_EMPTY`。

- [ ] **Step 7: 记 readout**

在主仓 `docs/research/2026-10-02-scan-session-v1-first-run-readout.md` 写 S1 节,字段固定:run_id、分析日、模式、墙钟、派发次数、每个子 agent 绑定前被拒次数、重试、`verify-report` 八个字段原值、`metering` 读数、遇到的缺陷与处置。被拒次数按 transcript 里的工具调用逐个数(不能用 `grep -c`:它数的是行,一次调用在 transcript 里不止一行,agent 复述拒绝原文也会多算):

```bash
SUB=$(ls -d ~/.claude/projects/*/"$CLAUDE_CODE_SESSION_ID"/subagents)
python3 - "$SUB" <<'EOF'
import json, sys
from collections import Counter
from pathlib import Path
for path in sorted(Path(sys.argv[1]).glob("agent-*.jsonl")):
    meta = path.with_suffix(".meta.json")
    role = json.loads(meta.read_text(encoding="utf-8")).get("agentType") if meta.is_file() else None
    names, calls, denied = {}, Counter(), Counter()
    for line in path.read_text(encoding="utf-8").splitlines():
        content = (json.loads(line).get("message") or {}).get("content")
        for block in content if isinstance(content, list) else []:
            if not isinstance(block, dict):
                continue
            if block.get("type") == "tool_use" and block.get("id") not in names:
                names[block["id"]] = block.get("name")
                calls[block.get("name")] += 1
            elif block.get("type") == "tool_result" and "AGENT_INPUT_BOUNDARY" in json.dumps(
                    block.get("content"), ensure_ascii=False):
                denied[names.get(block.get("tool_use_id"), "?")] += 1
    print(path.stem, role, "calls=", dict(calls), "denied=", dict(denied),
          "websearch_effective=", calls["WebSearch"] - denied["WebSearch"])
EOF
uv run --no-sync python -m autoresearch.session_agent metering --run-id "$RUN_ID" | head -c 600
```

每个子 agent 一行:角色、各工具的调用次数、被 hook 拒的次数、真正发出去的检索数。这段脚本 10-02 对 Task 4 Step 3 的探针实测过:`l3-repair calls= {'Read': 1} denied= {'Read': 1} websearch_effective= 0`。同一个会话里的子 agent 都会列出来(包括那个探针和前几级的 agent),按角色和先后认本级的那几行。

- [ ] **Step 8: 取证(配套稿 Task 1 已实施时)**

```bash
uv run --no-sync python -m autoresearch.session_agent accept-run --run-id "$RUN_ID" \
  --scenario sentinel-empty --evidence-kind REAL_SESSION_DRILL --report report/brief.md --write \
  --notes "isolated drill root .worktrees/claude-scan-drill; sentinel induced by sentinel.auto_below=1.0 frozen in run contract; pinned empty; not a natural market sample"
```

Expected: `"ready": true`、`"blockers": []`、`"written": true`,并给出 `proof_path`。直接带 `--write` 是安全的:它先重算核验和隔离重放,只有 `blockers` 为空才落证明、更新索引;证据不齐时退出码 2,错误信息列出全部 blockers,什么都不落。它拒写时,去掉 `--write` 再跑一次,能拿到完整 JSON 和 `replay_dir`,对着重放目录查差异(每次重放都用新目录:第一次 `<场景>/`,第二次 `<场景>.2/`,旧的不动)。全扫的重放含 prelude(从冻结来源重放),某个单元超过每单元缺省的 300 秒时加 `--replay-timeout 1800`。

`accept-run` 还没实施就跳过本步,run 保留,事后可补(核验和重放都是对冻结 capsule 的离线计算)。

---

### Task 6: S2 哨兵持仓演练(一只持仓走完整 L4 链)

**Files:**
- Modify(仅隔离根): `.claude/skills/scan-market/pinned.jsonc`

**Interfaces:**
- Consumes: Task 5 通过;隔离根里 `sentinel.auto_below` 仍是 1.0。
- Produces: 一个 `run_mode=SENTINEL_PINNED` 的已发布 run(`RUN_S2`);readout 的 S2 节;情报员绑定竞态的读数。

这一级新增覆盖:L4 任务簿、slim 取数、`l4-intel`(联网、第一个动作就是 WebSearch)、`l4-card`、复核计划;持仓卡若给出 SELL,还会走独立上下文的第二、第三意见。

- [ ] **Step 1: 接着用 S1 的会话(同一会话可以起新 run);只恢复一只持仓**

```bash
python3 - <<'EOF'
from pathlib import Path
Path(".claude/skills/scan-market/pinned.jsonc").write_text(
    '[\n  { "code": "300750", "note": "宁德时代 演练", "expires": "2026-10-31" }\n]\n', encoding="utf-8")
EOF
uv run --no-sync python -c "
from autoresearch.scan import user_config
print([row['code'] for row in user_config.load_pinned('2026-09-30')['kept']])"
```

Expected: `['300750']`。

- [ ] **Step 2: 生成请求并起跑**

```bash
LABEL=sentinel-pinned DATE=2026-09-30 FORCE_FULL=false
# Task 4 Step 2(锁检查)→ Task 4 Step 4 → 宿主循环
```

- [ ] **Step 3: 跑宿主循环**

预期请求:`scan.market_view`(macro-brief)、`l4.300750.a1.intel`(l4-intel)、`l4.300750.a1.card`(l4-card);卡面是 SELL 时还有 `…review2`,分歧时 `…review3`(agent_type 仍是 `l4-card`,`independent_context=true`)。任务名以实际 `REQUEST` 为准。

- [ ] **Step 4: 核验**

同 Task 5 Step 6;模式应为 `SENTINEL_PINNED`。

- [ ] **Step 5: 数情报员的绑定竞态**

重跑 Task 5 Step 7 的计数脚本,看 `l4-intel` 那一行。

- [ ] **Step 6: 判定执行器**

情报员那一行的 `websearch_effective` 是它真正发出去的检索数(调用数减去被拒数)。把它和派发 prompt 里的额度(`l4_intel.max_queries`)对比:

| 读数 | 结论 |
|---|---|
| 真实检索数 ≥ 额度的一半,且情报稿有「## 事件段」「## 声明行」 | 继续用 mailbox |
| 真实检索数 < 额度的一半,或情报稿写了「无法联网」之类 | 记为缺陷,S3、S4 改用 headless 执行器:runner 命令把 `--executor mailbox --max-parallel 8` 换成 `--executor headless --max-parallel 4`,宿主循环那一段整段不需要;请求的 `host_profile` 照 `scan_run.build_headless_request` 的写法填(`session_ref` 为 `headless-<uuid>`、`safe_resume` 为 `false`、`evidence_refs` 为 `scan_run.HEADLESS_EVIDENCE` 两条、不给 `transcript-file:`)。headless 路径同样没真跑过,换过去的第一场也按 shakedown 对待 |

headless 在启动 `claude -p` 之前就按预分配的会话 UUID 绑好了,没有这段空窗;代价是没有会话内的逐卡直播。

- [ ] **Step 7: 记 readout、取证**

同 Task 5 Step 7–8,场景名 `sentinel-pinned`,notes 把 `pinned empty` 换成 `pinned=300750`。

---

### Task 7: S3 强制全扫演练(缩到 5 张卡)

**Files:**
- Modify(仅隔离根): `.claude/skills/scan-market/scan_config.jsonc`、`pinned.jsonc`

**Interfaces:**
- Consumes: Task 6 通过,以及它定下的执行器。
- Produces: 一个 `run_mode=FORCED_FULL` 的已发布 run(`RUN_S3`);readout 的 S3 节;生产根未被污染的比对结果。

这一级新增覆盖:行业 pack 与 `sector-brief`、L3 准备、`l3-rank`(单次最长 40 分钟)、L3 lint 与可选修补、GATE2、5 张非持仓卡、E6 相对 BUY 决策。brief ③ 大概率是 🟥 R 级行——这是 Task 1 在真跑里的验收。

- [ ] **Step 1: 配置改成「5 张卡、无持仓」**

```bash
python3 - <<'EOF'
from pathlib import Path
p = Path(".claude/skills/scan-market/scan_config.jsonc"); s = p.read_text(encoding="utf-8")
old = '"max_cards": 10,'
assert s.count(old) == 1, s.count(old)
p.write_text(s.replace(old, '"max_cards": 5,'), encoding="utf-8")
Path(".claude/skills/scan-market/pinned.jsonc").write_text("[]\n", encoding="utf-8")
EOF
uv run --no-sync python -m autoresearch.scan.config_standard | tail -1
```

Expected: `0 条违规`。`max_cards=5` 时 `card_count.effective_caps` 给出席位 3、L3 名额 2(10-02 实算),与 09-26 的回放读数一致。

- [ ] **Step 2: 生成请求并起跑**

```bash
LABEL=forced-full DATE=2026-09-30 FORCE_FULL=true
# Task 4 Step 2 → Task 4 Step 4 → 宿主循环
```

- [ ] **Step 3: 跑宿主循环**

预期请求量:1 个市场研判、若干行业 brief(个数由 `sector.max_briefs` 与当日复用决定)、1 个 `l3-rank`、可能 1 个 L3 修补、5 只票各 1 个情报 + 1 张卡,≥OW 的卡再加复核。同一时刻最多 8 个在飞。

- [ ] **Step 4: 核验**

同 Task 5 Step 6;模式应为 `FORCED_FULL`。

- [ ] **Step 5: 确认 Task 1 在真跑里生效**

```bash
grep -n 'relative BUY' "$CANONICAL/report/brief.md" | cut -c1-120
DECISION=$(find "$CANONICAL/capsule/products" -name _relative_buy_decision.json | head -1)
FIRES=$(find "$CANONICAL/capsule/products" -name gate_fires.csv | head -1)
python3 -c "
import json; d=json.load(open('$DECISION'))
print(d['mode'], d.get('blocked'), [(b['code'], b.get('tier')) for b in d.get('buys') or []])"
python3 -c "
import csv; rows=list(csv.DictReader(open('$FIRES', encoding='utf-8')))
print([r['check'] for r in rows if r['severity']=='fail'])"
```

Expected: brief ③ 那一行里的六位代码等于决策文件 `buys[].code`;`gate_fires.csv` 没有 fail 行(最后一条打印 `[]`)。

- [ ] **Step 6: 记 readout、取证**

同 Task 5 Step 7–8,场景名 `forced-full`,notes 写 `force_full=true; l4.max_cards=5; pinned empty`。

- [ ] **Step 7: 三份演练证明导入主根(配套稿 Task 1 已实施时)**

在**主仓根**执行,每份证明一次:

```bash
cd /Users/qingbin.zhuang/Personal/TradingAgents
export AUTORESEARCH_ENGINE=claude
W=.worktrees/claude-scan-drill/reports_claude/_acceptance/proofs/claude/scan-market
for p in "$W"/*/sentinel-empty.json "$W"/*/sentinel-pinned.json "$W"/*/forced-full.json; do
  uv run --no-sync python -m autoresearch.session_agent acceptance-import --proof "$p"
done
uv run --no-sync python -m autoresearch.session_agent acceptance-status \
  --records-file reports_claude/_acceptance/records.json --evidence-root reports_claude/_acceptance/proofs \
  | python3 -c "import sys,json; d=json.load(sys.stdin); print(d['accepted_count'], d['workflows']['scan-market']['accepted_records'])"
```

Expected: `4 ['claude:forced-full', 'claude:sentinel-empty', 'claude:sentinel-pinned']`(加上 10-01 已有的单股 LITE 早停)。

- [ ] **Step 8: 隔离根配置复原**

```bash
cd .worktrees/claude-scan-drill && git checkout -- .claude/skills/scan-market/scan_config.jsonc .claude/skills/scan-market/pinned.jsonc \
  && git status --short | wc -l && cd /Users/qingbin.zhuang/Personal/TradingAgents
```

Expected: `0`。这里的 `git checkout --` 只作用于隔离根,且只针对本任务自己改的两个文件。

- [ ] **Step 9: 生产根没被动过**

```bash
F=context_claude/development/20261002-scan-first-run
{ ls reports_claude/scan | sort
  ls context_claude/scan_runs | sort
  shasum -a 256 reports_claude/scan/_ledger/recommendations.csv context_claude/knowledge/coverage_pool.json
} > "$F/production-after.txt"
diff "$F/production-before.txt" "$F/production-after.txt" && echo "production untouched"
```

Expected: `production untouched`。有差异且期间没有人在生产根跑过扫描或夜间补账,停下来查是谁写的。夜间补账(交易日 23:30)会改 `recommendations.csv`;节假日不跑。

---

### Task 8: S4 生产根首场真实全扫

**Files:**
- Create: `reports_claude/scan/runs/<RUN_ID>/p1/`(由 `finish` 生成)

**Interfaces:**
- Consumes: Task 7 通过;D4 的裁定(分析日)。
- Produces: 一份可执行的生产报告;`scan-market:claude:full` 的真实 run(`RUN_S4`)。

- [ ] **Step 1: 在仓库根新开 Claude Code 会话,做 Task 4 Step 1–3**

生产配置保持原样:`sentinel.auto_below=0.03`、`l4.max_cards=10`、两只持仓。确认没人改过:

```bash
git diff --stat -- .claude/skills/scan-market/scan_config.jsonc .claude/skills/scan-market/pinned.jsonc
```

Expected: 与 Task 3 快照时相同(本稿没有任何一步改主仓的这两个文件)。

- [ ] **Step 2: 生成请求并起跑**

```bash
LABEL=full DATE=2026-09-30 FORCE_FULL=false     # D4 选 b 时 DATE 换成当晚交易日
# Task 4 Step 4 → 宿主循环
```

- [ ] **Step 3: 跑宿主循环并按 SKILL 的直播契约播报**

CP0–CP7 的时机和素材不变;取材从「workflow 日志」换成 `status --run-id "$RUN_ID"`(`result.coverage` 给出主卡、深核、复核的 required / completed / missing)和 staging 里的同名文件。GATE1 之后先看模式:

```bash
python3 -c "import json; print(json.load(open('context_claude/scan_runs/$RUN_ID/staging/$DATE/run_mode.json'))['mode'])"
```

Expected: `FULL`。如果当日市场真的进了哨兵档(健康上涨占比 < 3%),这场就是一份天然的 `SENTINEL_PINNED` 样本,照常跑完,`full` 场景留到下一个交易日。

- [ ] **Step 4: 核验并交付**

「宿主循环」的收尾与核验全过之后,原文转播 `$CANONICAL/report/brief.md`,附 canonical 路径、评级分布、停因分桶。核验有任何一项不过,不交付报告,转 Task 9。

- [ ] **Step 5: 记 readout、取证**

同 Task 5 Step 7–8,在主仓根执行,`--scenario full --evidence-kind REAL_SESSION`,notes 写宿主版本、会话 id、执行器。`accept-run --write` 在主根直接落证明和索引,不需要导入。

---

### Task 9: 某一级没过时的处置

**Files:**
- Modify: 由缺陷决定;每个缺陷一个最小失败测试加一处修复。
- Modify: readout 对应小节。

**Interfaces:**
- Consumes: 失败 run 的 `runner.outcome`、`status`、capsule。
- Produces: 修复提交;隔离根快进到新快照;同一级的新 run。

- [ ] **Step 1: 先把现场读出来,不动它**

```bash
S=context_claude/development/20261002-scan-first-run; mkdir -p "$S"
uv run --no-sync python -m autoresearch.session_agent status --run-id "$RUN_ID" > "$S/status_$RUN_ID.json"
cat "$(find context_claude/scan_runs/$RUN_ID -name runner.json | head -1)"
```

在失败 run 所在的根里执行(演练 run 在隔离根,生产 run 在主根)。

- [ ] **Step 2: 按下表归类,只走对应那一条**

| 现象 | 归类 | 做法 |
|---|---|---|
| `mailbox complete` 返回 `ABANDONED`,或 runner 记 `TIMEOUT` | 瞬时 | 什么都不做,runner 已开新 attempt;同一任务第二次再超时才算缺陷 |
| orphan(runner 死在 claim 与 submit 之间) | 瞬时 | 照 outcome 里的 `hint` 执行 `fail --error-class STALE_TASK`,换新 detach key 重启 runner |
| `DOMAIN_VALIDATION_FAILED`(卡、情报、L3 判词不合契约) | 研究产物问题 | 不改校验器、不替 agent 改稿。该票终失败则整场停(D3):`capsule finalize FAILED`,起新 run。同一角色连续两场栽在同一条契约上,才按「指令与检查不一致」立缺陷 |
| 确定性任务失败两次(`scan.frame`、`scan.prelude`、`scan.assemble`、`scan.gate4`…) | 代码或数据缺陷 | 读该任务的 `capsule/logs/` 原始输出定位;数据源故障等恢复后起新 run;代码缺陷走 Step 3 |
| `verify-report` 的 `missing` 或 `diffs` 非空 | 证据缺口 | 逐条读原文。它们是「该有的证据没有」的精确清单,走 Step 3 |
| `HOST_CAPABILITY_REQUIRED` | 宿主声明或加载问题 | 回 Task 4 重做;不改能力门 |

- [ ] **Step 3: 修(只在主工作树)**

每个缺陷:在主工作树写一个能复现它的最小失败测试,确认红;改代码;确认绿;跑 Task 3 Step 1 的四片回归。不在隔离根里改源码,不改已发布的 capsule。

- [ ] **Step 4: 收掉失败的 run**

```bash
uv run --no-sync python -m autoresearch.trace.capsule finalize "$RUN_ID" --business-status FAILED
```

在失败 run 所在的根里执行。

- [ ] **Step 5: 隔离根换到新快照**

重做 Task 3 Step 2 得到新的 `$SNAP`,然后(顺序不能换:分支被工作树占着时 git 不让强改它,所以先让工作树离开分支;切换前先把演练改过的两个配置文件还原,否则切换会因本地改动被拒):

```bash
cd .worktrees/claude-scan-drill
git checkout -- .claude/skills/scan-market/scan_config.jsonc .claude/skills/scan-market/pinned.jsonc
git checkout --detach "$SNAP"
cd /Users/qingbin.zhuang/Personal/TradingAgents
git branch -f drill/scan-session-v1 "$SNAP"
```

重跑那一级之前,按该级的 Step 重新改一遍隔离根配置。

再在隔离根**新开会话**(源码和可能的 hook 都变了),从失败的那一级重跑。已经通过的级别不用重跑,除非修复动了它覆盖的代码。

- [ ] **Step 6: readout 追加一行**

缺陷编号、现象、根因、修复提交、复现测试名、重跑的 run_id。

---

### Task 10: 文档与入口同步

**Files:**
- Modify: `.claude/skills/scan-market/SKILL.md`(「入口与交付」节)
- Modify: `docs/ops/scan-ops.md`
- Modify: `docs/session-agent/acceptance.md`

**Interfaces:**
- Consumes: Task 8 的 run_id 与读数。
- Produces: 入口文档与实际跑法一致。

- [ ] **Step 1: SKILL.md「入口与交付」节末尾加一段**

```markdown
**session_v1 起扫(当前唯一可跑路径):** 新开会话 → 就绪检查(会话晚于 hook 与 agent 定义、`task_access preflight`、扫描锁、hook 加载探针)→ 生成 v4 请求(`session_ref` 取 `$CLAUDE_CODE_SESSION_ID`)→ `begin` → 后台起 `session_agent run --executor mailbox --max-parallel 8` → `mailbox wait` 循环。三条纪律:领到请求原样派发并立刻 `bind-access`;agent 返回立刻 `complete`,之后不再给它发消息;begin 之后不改代码。逐步命令见 `docs/ops/scan-ops.md`「session_v1 手动全扫」。
```

`.claude/skills/` 不在边界策略指纹里(指纹只含 `.claude/settings.json` 与 `.claude/agents/*`),改它不会让边界证明失效。

- [ ] **Step 2: scan-ops.md 新增「session_v1 手动全扫」节**

把本稿 Task 4、「宿主循环」、Task 9 的处置表原样搬过去(命令块一字不改),并在节首写明实测读数:墙钟、派发次数、Task 8 的 run_id。

- [ ] **Step 3: acceptance.md 的 Claude 行**

把「工作流 1/14」更新为机器查询的实际数,指向本稿 readout。数字来自:

```bash
uv run --no-sync python -m autoresearch.session_agent acceptance-status \
  --records-file reports_claude/_acceptance/records.json --evidence-root reports_claude/_acceptance/proofs \
  | python3 -c "import sys,json; d=json.load(sys.stdin); print(d['accepted_count'], d['default_status'])"
```

- [ ] **Step 4: 文档预算与配置 lint**

```bash
AUTORESEARCH_ENGINE=codex uv run --no-sync python -m pytest -q tests/test_doc_budgets.py -p no:cacheprovider | tail -1
uv run --no-sync python -m autoresearch.scan.config_standard | tail -1
```

Expected: 全绿;`0 条违规`。

- [ ] **Step 5: 提交(你点头后)**

```bash
git add .claude/skills/scan-market/SKILL.md docs/ops/scan-ops.md docs/session-agent/acceptance.md \
  docs/research/2026-10-02-scan-session-v1-first-run-readout.md
git commit -m "docs(scan): session_v1 手动全扫跑法与首跑读数"
```

---

### Task 11: 10-08 当晚例行全扫

**Files:** 无代码改动。

**Interfaces:**
- Consumes: Task 8 跑通的做法。
- Produces: 节后第一份当日报告。

- [ ] **Step 1: 等数据灌齐再开**

tushare `stk_factor_pro` 要到约 21:10 才齐。用项目自带的就绪探针(行数 ≥5300 且连续两次不变才算齐;它会一直轮询到就绪或 22:30,所以脱离会话跑,回头看退出码):

```bash
uv run --no-sync python -m autoresearch.trace.detach --run-id 20261008T000000000000Z --key readiness-20261008 \
  --wait-seconds 60 --shell "AUTORESEARCH_ENGINE=claude uv run --no-sync python -m autoresearch.scan.readiness 2026-10-08"
```

同一条命令再执行一次就是「只等不重跑」,用它查结果;终态也落在 `context_claude/detached/20261008T000000000000Z/readiness-20261008.json`。Expected: 探针退出码 0(就绪)。1 表示到截止仍没灌齐,当晚不开扫。`--run-id` 在这里只是 detach 记账用的键(它只校验格式,状态写在 `context_claude/detached/<键>/` 下),不对应任何 run。

- [ ] **Step 2: 照 Task 8 跑**

`LABEL=full DATE=2026-10-08 FORCE_FULL=false`。

- [ ] **Step 3: 读数进 readout**

与 Task 8 的那场并排:墙钟、派发次数、加权 token、主会话占比。这是 session_v1 相对旧 Workflow(09-17 场 $40.5、141 个命令壳)的第一组可比读数;少于 10 场不下结论。

---

## 验收标准

| 项 | 判据 |
|---|---|
| Task 1 | 两个测试文件 `125 passed`(两引擎);三场真实产物重放 `fail_checks= []`;`tests/scan` 0 failed |
| Task 2 | `tests/scan/test_scan_run.py` 等三个文件 `102 passed`(两引擎) |
| S1–S3 | 各有一个已发布 run,模式依次为 `SENTINEL_EMPTY`、`SENTINEL_PINNED`、`FORCED_FULL`;`verify-report --level full` 五个布尔量全真、`missing`/`diffs` 为空 |
| 隔离 | `production-before.txt` 与 `production-after.txt` 无差异 |
| S4 | 生产根一个 `FULL` 模式的已发布 run,核验全过,brief 已交付 |
| 10-08 | 当晚一份当日报告,核验全过 |

不作为验收条件:出不出 BUY、评级分布长什么样、token 比旧路省多少。

## 回滚

- Task 1、Task 2 各一个提交,`git revert` 即回。回滚 Task 1 等于恢复 GATE4 对 R 级行的误判。
- 隔离根整体可弃:`git worktree remove .worktrees/claude-scan-drill && git branch -D drill/scan-session-v1`。已导入主根的演练证明不受影响(证明自带全部哈希)。
- 生产 run 失败不回滚任何东西:`capsule finalize FAILED`,起新 run。

## 不在本稿范围

- 失败票降级为盲卡发布(D3 选 b 时另立项)。
- launchd 安装 `com.tradingagents.scan-run.plist`、送达渠道、五日无人值守验收(09-26 稿 C 线)。
- 前向观察协议的中止与重登记(Codex 引擎,D1)。
- 边界证明重采与其余 10 个工作流场景(配套稿)。
- brief ① 的 regime 显示「—(—)」、⑤「自检 fail 0」早于自检渲染、prelude 汇总屏「预热未跑」误报:三处展示问题,09-29 已记录,不阻断发布。
