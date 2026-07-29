# Wave8 实施计划 —— 逐 task 拆解(subagent 可直接领)

> 上游 design:`docs/specs/2026-07-29-wave8-maiden-run-optimization-design.md`(机制/证据/红线,
> 本文不重抄)。调度权威仍是 07-28 总纲。
>
> **全局纪律**(每个 task 都适用,任务卡内不重复):
> - TDD:先写红测试再实现;每件带**变异探针**(把改动撤掉/守卫置空,测试必须变红——
>   「绿灯必须会变红」)。
> - workflow js 改动一律跑 **AsyncFunction 探针**(`node --check` 对 ESM+顶层 return 零鉴别力,
>   见 tests 里既有 18 绿探针,照该模式加);`.claude/agents/*.md` 或 SKILL 改动后跑
>   `tests/test_agent_defs.py` + doc-lint。
> - 命令一律 `uv run --no-sync`,仓库根目录;`pytest` 别接 `|tail`(吞退出码)。
> - 每 task 独立 commit、独立可回滚;commit message 风格照 `git log` 近例。
> - agent def / SKILL 落盘后**当会话派发会 not found**(会话启动装载)——涉及 def 的验收放
>   下一会话或 resume 后跑。

## 批 Ⅰ 止血(B 主题,无依赖,~1 天)

### W8-1 · B1 frame 原子写 `--json-out`

- **定位**:`autoresearch/scan/frame.py:154-166`(main;`--json` 现把构建段 stdout 圈进
  stderr,:166 注)+ `.claude/workflows/scan-market.js:63`(frame 命令行)与 :76(retry 行)。
- **Sketch**:
  ```python
  ap.add_argument("--json-out", metavar="PATH",
                  help="market_pack JSON 原子落盘(临时文件+os.replace;stdout 不再承载产物)")
  # main 尾部:
  if args.json_out:
      tmp = Path(args.json_out).with_suffix(".tmp")
      tmp.write_text(json.dumps(pack, ensure_ascii=False, indent=2), encoding="utf-8")
      os.replace(tmp, args.json_out)   # 崩溃 = 无文件/旧文件,永不半文件
  ```
  `--json`(打印到 stdout)保留兼容;两 flag 可共存。js 侧:
  `frame ${date} --json > ${SD}/market_pack.json` → `frame ${date} --json-out ${SD}/market_pack.json`
  (retry 行同改;shell 重定向删除)。
- **同族普查**:`grep -n '> \${' .claude/workflows/*.js` 列出全部产物重定向,逐条评估
  (只改"产物 = 结构化文件"的;日志类重定向不动)。普查结果写进 task 收尾 commit message。
- **测试**:`tests/scan/test_frame_json_out.py`——①正常路:`--json-out` 后文件为合法 JSON 且
  无 `.tmp` 残留;②崩溃路:mock 构建段抛异常 → 目标文件不存在(或保持旧内容);③stderr 混入
  模拟:向 stderr 写垃圾,产物仍纯 JSON。**变异探针**:把 `os.replace` 换直写 open(...).write
  并在写一半时抛异常 → 测试 ② 红。
- **验收**:`uv run --no-sync python -m autoresearch.scan.frame <date> --json-out /tmp/p.json 2>&1 | cat`
  (故意 2>&1)后 `json.load` 通过。
- **回滚**:js 行还原 + flag 留着无害。**预估**:2h。

### W8-2 · B2 pack-check 门判据升级(pr_20260728_001)

- **定位**:`.claude/workflows/scan-market.js:71-78`(pack-check / frame-retry / pack-recheck)。
- **Sketch**:两处 gate 命令
  `test -s ${SD}/market_pack.json && echo '{"ok":true}' || ...` →
  ```
  uv run --no-sync python -c "import json,sys;json.load(open('${SD}/market_pack.json'))" \
    && echo '{"ok":true}' || echo '{"ok":false,"reason":"market_pack 缺失或非合法 JSON"}'
  ```
- **测试**:AsyncFunction 探针;人工演练一次:放 1,776B 日志垃圾进 pack → 门必须
  `{'ok':false}` 并走 retry 分支(演练记录进 commit message,对应 design §6.2-2 变异探针)。
- **验收**:垃圾 pack 场景重放 → 门红 + retry 触发。**回滚**:还原 `test -s`。**预估**:1h。
- **收尾**:`fs.set_proposal_status("pr_20260728_001", ...)` 关账(resolution 注明 W8-1+W8-2
  双层落地)。

### W8-3 · B3 bash() 壳硬约束

- **定位**:`.claude/workflows/scan-market.js:~30`(bash() prompt)+
  `.claude/workflows/l4-stock.js:32,38,183`(三处 gp-haiku 壳 prompt)。
- **Sketch**:四处 prompt 统一追加一句:
  「命令逐字节原样执行,**不得添加 2>&1、tee、管道或改写任何重定向**;若命令的 stdout 已被
  重定向,回报退出码 + stderr 末 15 行。」
- **测试**:AsyncFunction 探针(js 语法);无法单测 LLM 行为——防线是 W8-1/W8-2 的机器层,
  本条是第一因的指令层(instruction-vs-check 排序:先补指令)。
- **验收**:四处 prompt 均含约束句(grep 断言可进 `tests/test_agent_defs.py` 同款文本锚)。
- **回滚**:删句。**预估**:0.5h。

### W8-4 · B4 全 CLI 入口冒烟测试

- **定位**:新 `tests/test_cli_entrypoints.py`。
- **Sketch**:
  ```python
  CLI_MODULES = [  # grep -rl 'argparse' autoresearch/ 里带 __main__ 的模块,首版手工核列
      "autoresearch.scan.universe", "autoresearch.scan.prelude", "autoresearch.scan.frame",
      "autoresearch.scan.menu", "autoresearch.scan.calendar", "autoresearch.scan.assemble",
      "autoresearch.scan.gates", "autoresearch.scan.render", "autoresearch.scan.post_run",
      "autoresearch.scan.l4_reuse", "autoresearch.scan.l4_tasks", "autoresearch.scan.temperature",
      "autoresearch.learning.retro", "autoresearch.learning.t1_review",
      "autoresearch.learning.zero_buy_ledger", "autoresearch.learning.experiment_registry",
      "autoresearch.learning.rollback_watch", "autoresearch.trace.usage_harvest",
      "autoresearch.research.consensus", "autoresearch.dossier.pool",
      "autoresearch.dossier.reconcile", "autoresearch.sector.reuse", "autoresearch.sector.pack",
      # …建列时以实际 grep 结果为准,目标全覆盖
  ]
  @pytest.mark.parametrize("mod", CLI_MODULES)
  def test_cli_help_exits_zero(mod):
      r = subprocess.run([sys.executable, "-m", mod, "--help"],
                         capture_output=True, timeout=60)
      assert r.returncode == 0, r.stderr.decode()[-500:]
  ```
  注意:个别 CLI `--help` 若有 import 期副作用(取数/写盘)→ 该模块先修成 lazy import 再入列,
  不豁免。
- **变异探针**:临时删 `assemble.py` 的 `import argparse` → 对应参数化用例红(今晚真实缺陷
  即测试原型)。
- **验收**:全列表绿;CI 常跑集不排除。**回滚**:删测试文件。**预估**:2-3h(含 lazy import
  清理)。

### W8-5 · B5 prelude 汇总屏诚实失败

- **定位**:`autoresearch/scan/prelude.py:386-389`(suppress 块)+
  `.claude/workflows/scan-market.js:90`(SUMMARY_FILE echo)。
- **Sketch**:
  ```python
  try:
      print(f"  (汇总屏已落盘:{write_summary(date, results)})")
  except Exception as e:  # noqa: BLE001 — 仍不阻断前奏,但失败必须响亮
      print(f"[prelude] ✗ 汇总屏落盘失败: {e!r}", file=sys.stderr)
  ```
  js 侧:`&& echo "SUMMARY_FILE=${SD}/_prelude_summary.md"` →
  `; test -s ${SD}/_prelude_summary.md && echo "SUMMARY_FILE=${SD}/_prelude_summary.md" || echo "SUMMARY_MISSING"`。
- **测试**:`tests/scan/test_prelude_summary_honest.py`——monkeypatch `write_summary` 抛异常 →
  stderr 含 `✗ 汇总屏落盘失败` 且 run_prelude 不抛;**变异探针**:还原 suppress → 测试红
  (断言 stderr 有内容)。AsyncFunction 探针(js)。
- **验收**:下次实跑 CP1 要么拿到文件、要么日志见真实失败原因(今晚被吞的根因自证)。
- **回滚**:还原 suppress。**预估**:1h。

### W8-6 · B6 `l4_tasks batches` RUNNING 可见性

- **定位**:`autoresearch/scan/l4_tasks.py:431-460`(dispatch_batches;:450 只选
  PENDING/FAILED)。
- **Sketch**:返回 JSON 增
  `"running": [{"code": c, "age_min": int((now-started_at)/60)} ...]`;stdout 表述同步。
  SKILL 派发契约(见 W8-8)明写「完成判据 = task_book 全 SUCCEEDED,非 batches 为空」。
- **测试**:`tests/scan/test_l4_tasks.py` 增用例:构造一 RUNNING 一 PENDING → 输出
  running 数组含龄期;**变异探针**:撤 running 字段 → 断言红。
- **验收**:CLI 输出可区分 在飞/待派/卡死(龄期>30min 由 l4_watch 报,见 W8-7)。
- **回滚**:删字段(消费方仅 SKILL 文字与 l4_watch,均容缺)。**预估**:1h。

## 批 Ⅱ 瘦身(A 主题;W8-7 先行,~1 天)

### W8-7 · A2 l4_watch 单一进度源

- **定位**:新 `autoresearch/scan/l4_watch.py`;参考 `_l4_tasks.json` 结构
  (status/started_at/artifacts.card.content_hash)与 `finalists.csv`(name 列)。
- **Sketch**:
  ```
  用法:uv run --no-sync python -m autoresearch.scan.l4_watch <date> --watch [--stale-min 30]
  行为:轮询 _l4_tasks.json(2s 间隔,mtime 变化才解析):
    - 某股 status→SUCCEEDED 且 artifacts.card.content_hash 非空:读卡 grep '**Rating**'
      → 打一行 "🃏 k/N <code> <name> → <rating>"(k=已终态数)
    - status→FAILED:打 "✗ <code> <last_error_class>"
    - RUNNING 龄期 > stale-min:打一次 "⏳ <code> 已跑 <m>min(未超时,仅提示)"
    - 全部终态(SUCCEEDED|FAILED):打汇总行后退出 0
  ```
  纯 stdlib(json/pathlib/time),零 LLM,零第三方。
- **测试**:`tests/scan/test_l4_watch.py`——tmp task_book 状态机推演(PENDING→RUNNING→
  SUCCEEDED 逐步写盘,断言输出序列);**草稿不读探针**:status=RUNNING 且卡文件已存在(写一半
  场景)→ 不产出评级行(今晚 601319 缺陷的回归测试);**变异探针**:把「hash 非空才读卡」守卫
  删掉 → 草稿探针红。
- **验收**:模拟 11 股推演输出与今晚实况等价、零草稿行。**回滚**:删模块(SKILL 换回旧 Monitor
  行)。**预估**:3h。

### W8-8 · A1+A4 滑窗派发 + 唤醒纪律(SKILL 派发契约改写)

- **定位**:`.claude/skills/scan-market/SKILL.md` 流程节步骤 4(「按 dispatch_batches 批次间
  顺序执行、批次内并行」段)+ 进度可视化节。
- **Sketch**(契约文字,要点):
  1. 初始并行派 `effective_cap` 只(取 `l4_tasks batches` 回显);**📌 pinned 与预计最长者
     排最前**(pinned 强制满卡+双复核 25-40min 实测);
  2. 此后**每收到一股完成通知 → 立即补派下一只 pending**(滑窗,始终 cap 只在飞);
  3. 完成判据 = task_book 全 SUCCEEDED(W8-6);单票失败只改本票状态,照旧不重跑成功票;
  4. 唤醒纪律:派发/收通知回合零播报;CP5 由 l4_watch Monitor 承担(W8-7);CP2/CP3 合并
     一次播报。
- **测试**:doc-lint + `tests/test_agent_defs.py`(若 SKILL 有锚测试);人工桌演:11 股×
  cap4 的滑窗时序表(pinned 先行)写进 commit message,对照今晚 3 批串行时序估算节省。
- **验收**:下次真实扫描唤醒序列零批间空等;L4 段墙钟(冷日)≤45m(design §6.2-1)。
- **回滚**:SKILL 段落还原(批次串行契约)。**预估**:1.5h。

### W8-9 · A2b progress.py 退役(E1 落地)

- **定位**:`autoresearch/scan/progress.py` + `tests/scan/test_progress.py` +
  `.claude/skills/scan-market/SKILL.md:45`(Monitor 行,改挂 l4_watch;注意该节其余文字随
  W8-10 下沉)。
- **Sketch**:删两文件;SKILL Monitor 命令换
  `uv run --no-sync python -m autoresearch.scan.l4_watch <date> --watch`。
  删前 `grep -rn "scan.progress"` 全仓确认消费者仅 SKILL 与自测(已核:SKILL.md:45、
  tests/scan/test_progress.py;deadcode 双职检查——test_progress 无「顺带锁别的契约」条目,
  可删)。
- **验收**:全仓零 `scan.progress` 残留引用;pytest 全绿。
- **回滚**:git revert。**预估**:0.5h。

### W8-10 · A3 SKILL 精简二期

- **定位**:`.claude/skills/scan-market/SKILL.md`(基线 `wc -l` 现值)→ 下沉目标
  `.claude/skills/scan-market/STAGES.md` 对应节。
- **下沉清单**(SKILL 各留一行指针):CP 表格逐条取数命令(CP0-CP7 详版)、Monitor 误报考古注
  (⚠️ 播报是反推段)、07-21 空 config 事故长注(0.5 节内)、prewarm 安装命令+实测注、
  CP5 滚动表做法(已被 l4_watch 取代,直接删)。**保留**:编排真身段、8 检查点表(壳)、
  铁律、常见坑短条。
- **红线**:契约锚字符串(`_CONTRACT_ANCHORS` 六锚)逐一 grep 确认仍在;`tests/test_agent_defs.py`
  + doc-lint 绿;**行数预算意识**(instruction-vs-check 归档:升格新契约必查行数/格式预算——
  本 task 是减法,同理确认没把活契约挤掉)。
- **验收**:SKILL 行数 −40%±10pp;下沉内容在 STAGES.md 可检索;两测试绿。
- **回滚**:git revert(单 commit)。**预估**:2h。

## 批 Ⅲ 尺子(C2/D 主题,无互依,~1 天)

### W8-11 · D1 弃权账本 v2(反事实换 shadow_buys)

- **定位**:`autoresearch/learning/abstention_ledger.py:228-247`(FALSE 判据:`opportunity`
  全市场口径)+ 输出 md 段(:355 附近 status 迭代)+ `autoresearch/scan/report_sections.py`
  0 买判词行。
- **Sketch**:
  ```python
  # 新判据:shadow_buys ∩ eligible ∩ excess_2 ≥ +0.02
  shadow = _load_shadow_buys(date)          # context/learning/shadow_buys.csv,列 date/code/...
  shadow_hit = rows["code"].isin(shadow) & eligible & (excess >= 0.02)
  status = "FALSE" if shadow_hit.any() else (旧 CORRECT/NEUTRAL 分支不动)
  reasons.append("shadow_buy_outperformed")
  # 旧全市场口径降级为诊断字段(不再进 status):
  verdict.recall_ceiling_n = int(old_opportunity.sum())
  # 过渡:verdict.status_legacy = 旧逻辑结果,md 双列并排 ≥10 个成熟日
  ```
  历史回算:重放 20260618 起有 shadow_buys 的日;缺 shadow 数据的日 **不新增枚举值**
  (status 枚举 CORRECT/FALSE/NEUTRAL/IMMATURE 在 :17-18/:355 有迭代消费方)——
  `status_v2=None`,md 渲染 `—(no_shadow)` 且不计入 v2 统计。
- **测试**:`tests/learning/test_abstention_v2.py`——①shadow 3 只全跑输 → CORRECT/NEUTRAL
  (即便全市场有 1,500 只 +2pp);②shadow 任一 +2pp 且可交易 → FALSE;③无 shadow 数据日 →
  `status_v2=None` 且不入 v2 统计;④双打印期两列并存;**变异探针**:把 `isin(shadow)` 撤回
  全市场 → 用例 ① 红。
- **验收**:07-15/07-21/07-24 三个历史 0 买日重放,新旧 verdict 并排落 md;headline 与
  paper_nav 方向一致性人工复核。
- **回滚**:`status_legacy` 升回主列(一行开关)。**预估**:3h。

### W8-12 · C2 cross_calib 增 OW-lean 确认率

- **定位**:`autoresearch/learning/cross_calib.py:27(_FLIP_COLS)/42-97(flip_stats)/
  191-203(suggestion_lines)/225-229(report 段)`。
- **Sketch**:
  ```python
  # flip_stats 返回每 lane 增列:
  #   lean_n(triage_lean∈{OW,Overweight} 数)· lean_confirm(L4 终评 ≥Overweight 数)
  #   · lean_confirm_rate(shrink 收缩,n<3 禁注——复用现 shrink 基建与 MIN_N_INJECT)
  # suggestion_lines:现「只挑 flip_rate 最差 lane」→ 两指标各挑最差一行,合计 ≤2 行:
  #   "🔁 L3校准:healthy lane OW-lean 确认率 0%(n=6)——该 lane 的 OW 倾向请先自证与 L4 分歧主因"
  # report 段表格增三列;md 渲染同步。
  ```
  数据源:`_l3_judged.json` 的 `triage_lean` × decision records 终评(现 join 已有,加列即可)。
- **测试**:`tests/learning/test_cross_calib.py` 增——构造 healthy lane 6 lean-OW 0 确认 →
  确认率行出现且带 shrink 值;n=2 → 禁注;**变异探针**:lean 列断供(旧 judged 无
  triage_lean)→ 优雅缺列不崩(向后兼容旧史)。
- **验收**:今晚数据重放出 `healthy 0/6` 行;注入行数 ≤2。**回滚**:注入行选择器还原单指标。
  **预估**:2h。

### W8-13 · D3 intel 限频升格(cap 20 + 硬顶 30 拒稿)

- **定位**:`.claude/skills/scan-market/scan_config.jsonc`(`l4_intel.max_queries` 15→20)+
  新 `autoresearch/scan/l4/intel_guard.py` + `.claude/workflows/l4-stock.js:81-102`
  ([slim∥intel] 屏障后、card 前插 guard 调用)。
- **Sketch**:
  ```
  intel_guard CLI:python -m autoresearch.scan.l4.intel_guard <date> <code> --hard-cap 30
    解析 _l4_intel_<code>.md 声明行自报条数(现 self_review 同款解析,抽公共函数复用):
    - 自报 ≤30:exit 0,{"ok":true,"n":N}
    - 自报 >30:mv → _l4_intel_<code>.rejected.md,exit 0,{"ok":false,"n":N,"action":"rejected"}
    - 自报缺失:exit 0,{"ok":true,"n":null,"warn":"unreported"}(照旧 warn,不拒)
  l4-stock.js:intel 成功后经 bash 壳跑 guard;rejected → log 一行,card 照常派
    (presence-gate 找不到 intel → 自动回退卡内网查,现有机制零改动;红线:只拒稿不拒票)。
  ```
- **测试**:`tests/scan/test_intel_guard.py`——自报 18/29/31/缺失 四例;31 → 文件改名且
  原名不存在;**变异探针**:守卫阈值置 None → 31 例红。AsyncFunction 探针(js)。
- **验收**:演练一次 >30 拒稿 + card 回退路径(design §6.2-5);下次实跑 warn 数只剩 >20 者。
- **回滚**:config 15 还原 + js 删 guard 调用(guard CLI 留着无副作用)。**预估**:2.5h。
- **收尾**:pr_20260714_007 关账(resolution:cap 对齐实测 + 硬顶拒稿落地)。

### W8-14 · D5 index_daily 入湖 + 指数断言对账

- **定位**:`autoresearch/dataflows/`(tushare source 增 `index_daily`;lake ns `index_daily`)
  + `autoresearch/scan/frame.py`(today_slice 增三大指数当日涨跌)+
  `autoresearch/scan/price_claims.py`(指数分支)+ prewarm 预拉清单。
- **Sketch**:
  - 六指数固定清单:000001.SH/399001.SZ/399006.SZ/000688.SH/000300.SH/000905.SH;
    B 级契约(空/缺 → 降级记账不阻断,`data-contracts` 惯例);
  - `price_claims`:断言文本命中指数名/代码词表 → 改对指数湖 `pct_chg` 裁真伪;词表与代码映射
    做成模块常量(上证指数/沪指→000001.SH 等);
  - `today_slice` 增 `index_pct_1d: {"上证": x, "深成": y, "创业板指": z}`(macro-playbook
    策略师小节 2 已有盘面句模板,数字从此可对账)。
- **测试**:`tests/scan/test_price_claims_index.py`——「创业板指 −7.35%」构造:湖值 −7.35 →
  通过;湖值 −1.2 → mismatch;湖缺 → `UNVERIFIABLE(index_lake_missing)` 独立态**不算 mismatch
  也不算通过**;**变异探针**:指数词表清空 → 断言退回个股路径误报,用例红。lake 侧走既有
  contracts 测试模式(剥 fields 全宽表,防窄表毒化——`lake-narrow-fields-poisoning` 教训)。
- **验收**:07-28 的两条 price_claim warn 重放:指数条可裁决(真伪其一),非指数条不受影响。
- **回滚**:price_claims 分支开关;湖数据无害留存。**预估**:3h。

## 批 Ⅳ 治理(C1/C3/D2/D4,~半天;W8-15 先于 W8-16)

### W8-15 · C1 硬约束 F 补丁起草(人批,不改文件)

- **定位**:`fs.add_prompt_patch`(feedback_store),target `.claude/agents/l3-rank.md:28`
  「选股硬约束」节。
- **Sketch**:patch 全文照 design §4.2(硬约束 F 原文);anchor_text=「选股硬约束(来自用户
  反馈,违反即失败)」;evidence 三条(601918 今晚 / bench 三券商同会话正确消费 /
  cross_calib 账本);add_prompt_patch 自校验契约锚存活。
- **验收**:proposals.jsonl 出一条 open prompt_patch(open 计数 ≤5 约束自查);**施工(实改
  l3-rank.md)在人批后另行 commit**,改后跑 `tests/test_agent_defs.py`。
- **预估**:0.5h。

### W8-16 · C3+D4 registry 实例化

- **定位**:`autoresearch/learning/experiment_registry.py:291(set_stable_baseline)/
  792(write_report)`;registry 落 `context/learning/experiments/registry.json`(首建)。
- **Sketch**:
  1. `write_report` 加 `--out PATH`(缺省保持现路径;演练传 `--out` 不再覆盖生产
     `reports/learning/experiments.md`)——先修锐边再实例化;
  2. `experiment_registry baseline`:pointer = 当前 `weights.json` sha + scan_config hash +
     `l3-rank.md` git sha(组合指针,格式按 CLI 现约定);
  3. C1 实验 spec(JSON):definition=补丁全文,family=`l3_prompt`,baseline pointer=上一步,
     rollback=revert 至 sha;`register` → 状态 PREREGISTERED;**approve/activate 留给人批日**
     (与 W8-15 施工同日),spec 字段显式记「影子不可行:prompt 无法双跑,守卫改事后
     rollback_watch ≥5 扫描日」。
- **测试**:`tests/learning/test_experiment_registry.py` 增 `--out` 用例(临时目录,生产
  报告文件 mtime 不变);**变异探针**:撤 `--out` 传参逻辑 → 用例红。
- **验收**:registry.json 存在含 baseline + 1 条 PREREGISTERED;`report --out /tmp/x.md`
  演练后生产报告未动。**回滚**:registry.json 删除即回到"未上膛"态(CLI 无副作用)。
  **预估**:2h。

### W8-17 · D2 裁决规则落账(零代码)

- **定位**:`autoresearch/learning/ensemble_ledger.py` 与 buy_ledger 的 docstring/md 头注。
- **Sketch**:把 design §5.2 三行预定义规则原文写进对应账本模块 docstring + md 报告头注
  (「开裁条件/裁决规则/谁拍板=人」);日历 v4 已在 design §6.3,不另建文件。
- **验收**:两账本 md 头部可见规则;07-30 后首次 refresh 时 ensemble 折回对错列开始积累。
- **预估**:0.5h。

## 验收总表(对照 design §6.2)

| # | 验收 | 由哪些 task 兑现 | 何时可测 |
|---|---|---|---|
| 1 | 主会话 ≤25% · L4 冷日 ≤45m · 零批间空等 | W8-7/8/9/10 | 下次真实扫描 CP7 |
| 2 | 垃圾 pack → 门红;删 argparse → 测试红 | W8-2 / W8-4 | 实施当日(变异演练) |
| 3 | CP5 零草稿误读;progress.py 无残留 | W8-7 / W8-9 | 实施当日 + 下次扫描 |
| 4 | 弃权 headline 基于 shadow_buys,与 paper_nav 同向 | W8-11 | 下次 0 买日 |
| 5 | intel warn 仅 >20;>30 拒稿演练 ✓ | W8-13 | 实施当日演练 + 下次扫描 |
| 6 | registry 非空;report --out 不污染生产 | W8-16 | 实施当日 |
| 7 | healthy lane lean 确认率进 🔁;C1 +5 扫描日复核 | W8-12 / W8-15 | C1 激活后 5 扫描日 |

> 实施顺序:批 Ⅰ(W8-1..6)→ 批 Ⅱ(W8-7 → W8-8/9/10)→ 批 Ⅲ(W8-11..14,可并行)→
> 批 Ⅳ(W8-15 → W8-16 → W8-17)。总预估 ~3.5 天。每 task 独立 commit;涉及 agent def/SKILL
> 的验收在下一会话跑(装载时机)。
