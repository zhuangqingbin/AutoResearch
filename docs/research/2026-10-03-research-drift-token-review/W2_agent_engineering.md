# W2 — Agent engineering survey: drift, evals, token economics, equivalence testing

Date: 2026-10-03 · Web research only (no project files read) · ~46 searches/fetches

**Source tags.** IDs `F#` = opened in this session **[FETCHED]**; `M#` = from prior knowledge, not re-verified **[MEMORY]**; `N#` = seen only as a search-result snippet, page not opened; `O1` = observed directly in this session's tool definitions. Full citations are at the end. Vendor docs are "living" pages, accessed 2026-10-03.

**Facts that best explain the incidents.** (1) In Claude Code, `opus`/`sonnet` aliases "update over time" (F4). (2) Effort levels are **not portable across models**: "At a given level, Claude Opus 5.5 tends to think more per turn than Claude Opus 5, especially at `xhigh` and `max`" (F7). (3) A Claude Code upgrade "typically updates the system prompt or tool definitions" (F37). (4) At "deterministic" settings, accuracy still varies up to 15% across runs (F39), so any "decision changed" claim needs a measured noise floor first.

---

## 1. Agent/behavior drift: definitions, measurement, detection, prevention

### Evidence: what drifts and how much
- **Model-version drift.** Chen, Zaharia & Zou measured GPT-4 on prime vs composite numbers. Accuracy fell from 84% to 51% between the March and June 2023 versions, and "GPT-4's ability to follow user instructions has decreased over time." They conclude that LLMs need "continuous monitoring" (F14).
- **Silent swaps behind an API can be detected statistically.** Model Equality Testing uses an MMD two-sample test. It reached a median of 77.4% power "using an average of just 10 samples per prompt," and found that "11 out of 31 endpoints serve different distributions" than the reference Llama weights (F19).
- **Claude-specific mechanics.**
  - Claude Code: "Aliases point to the recommended version for your provider and update over time. To pin… use the full model name… or set… `ANTHROPIC_DEFAULT_OPUS_MODEL`." Today `opus` resolves to Opus 5.5 and `sonnet` to Sonnet 5.5 on the Anthropic API (F4).
  - API: "Every Claude model ID is a pinned snapshot, including the dateless IDs used from the 4.6 generation on" (F3).
  - Effort semantics change between generations:
    - Opus 5.5 at the same effort level thinks more than Opus 5, especially at xhigh and max. "If you keep the `effort` value you set for Claude Opus 5, expect longer turns and more output tokens" (F7).
    - Sonnet 5.5's "levels are recalibrated" (F5).
    - The Opus 5.5 default is `medium`, where Opus 5's was `high` (F5, F6).
  - Thinking tokens "are billed as output tokens even when the thinking text is not returned" (F6).
  - Models from 4.7 on use a tokenizer that produces "approximately 30% more tokens for the same text" (F2).
  - Prices: Opus 5.5 costs $4/$20 per MTok vs $5/$25 for Opus 5 (F2).
- **Run-to-run nondeterminism.**
  - Atil et al. tested 5 LLMs × 8 tasks × 10 runs at "deterministic" settings. Accuracy varied up to 15%, the worst-to-best gap reached 70%, and "none of the LLMs consistently delivers repeatable accuracy… much less identical output strings" (F39).
  - Anthropic: "If you were using `temperature = 0` for determinism, note that it never guaranteed identical outputs." Sampling parameters are rejected on Opus 4.7 and later (F6).
  - Batch-size–dependent kernels are one root cause (M4).
- **Long-context and long-horizon degradation.**
  - Chroma's "Context Rot" study tested 18 models. "Model performance degrades as input length increases, often in surprising and non-uniform ways." "Even a single distractor reduces performance." On LongMemEval (~113K tokens), focused prompts did significantly better than full prompts (F15).
  - Multi-turn conversation causes "an average drop of 39%" across six tasks. Models "make assumptions in early turns… on which they overly rely" (F20).
  - Goal drift: "all evaluated models exhibit some degree of goal drift," and it "correlates with… susceptibility to pattern-matching… as the context length grows." The best scaffolded model held its goal for more than 100K tokens (F18).
  - Anthropic describes the same effect as "context rot" and an "attention budget" (F10).
- **"Agent drift" as a named construct.** Rath (2026) proposes three kinds of drift: semantic, coordination and behavioral. The paper also defines a 12-dimension Agent Stability Index covering response consistency, tool-usage patterns and inter-agent agreement. The evidence is **simulation-based** (F16), so treat it as a checklist, not proof.
- **Contract and prompt sensitivity.** Formatting-only prompt changes moved accuracy by up to 76 points (M1). Multi-agent failures cluster into specification issues, inter-agent misalignment and missing task verification (M2).

### Detection practices (cited)
- **Regression sets.**
  - Regression evals "should have a nearly 100% pass rate." "20-50 simple tasks drawn from real failures is a great start" (F8).
  - Use **pass^k** ("probability that all k trials succeed") for consistency, not pass@k (F8).
  - "You won't know if your graders are working well unless you read the transcripts" (F8).
- **Distribution monitors.** Anthropic tracks "runtime of individual tool calls," "total number of tool calls," "token consumption," and "tool errors" (F11). Token usage alone explained about 80% of BrowseComp performance variance (F9). A token shift is therefore a behavior signal, not only a cost signal.
- **Fingerprinting and equality tests** on a fixed prompt set, about 10 samples per prompt (F19).
- **Invariance and metamorphic tests.**
  - CheckList-style invariance tests (M6).
  - Listwise LLM rankers show positional bias. Shuffling the list order and aggregating to a central ranking improved scores by 7–18% on GPT-3.5 (F40).
- **Cache and harness telemetry.** Claude Code's `/usage` line "Prompt cache (main)" names a likely cause of cache misses, but it "covers the main conversation only, not subagents" (F36).
- No primary source was found in this session that quantifies "prompt hashing/versioning." It is standard MLOps practice, not cited evidence.

### Prevention practices (cited)
- **Pin and freeze.** Use full model IDs or the `ANTHROPIC_DEFAULT_*_MODEL` environment variables (F4). Also set `availableModels`/`modelOverrides` (F4). Set effort explicitly: "Set effort explicitly" is listed first in the best practices (F5). "A new Claude Code version typically updates the system prompt or tool definitions… Set `DISABLE_AUTOUPDATER=1` to control when upgrades apply" (F37).
- **Least privilege for subagents** (F35):
  - `tools` allowlist and `disallowedTools`.
  - PreToolUse hooks that block calls with exit code 2.
  - `maxTurns`, after which output is "marked as partial".
  - `omitClaudeMd: true` launches the subagent without the CLAUDE.md files.
  - By default a subagent loads the full CLAUDE.md hierarchy and a git-status snapshot.
- **Structured outputs** guarantee "Always valid… Type safe… No retries needed for schema violations." The guarantee does not hold on refusals or when `max_tokens` cuts the output, and constraints such as min/max and string length are unsupported (F38). Schema validity does not imply the content is correct.
- **JSON over Markdown for state files:** "the model is less likely to inappropriately change or overwrite JSON files compared to Markdown files" (F12).
- **Deterministic orchestration.** Anthropic's guidance is "find the simplest solution possible." Prompt chaining with "programmatic checks (… 'gate')" (F13). Workflows (predefined code paths) are preferred over agents unless the extra flexibility pays off (F13).
- **Verification loops.** "Treat a text-only end of turn as a report rather than as proof the task is done." Have "a separate, smaller model check the conversation against [the completion condition]," and stop after 2–3 automatic continuations (F7). Use rainbow deployments for agent updates (F9).

### Implications for the system (hypotheses, not evidence)
- **(a) 钉住模型和 harness。** 把 subagent frontmatter 的 `opus`/`sonnet` 换成完整 ID,或在无人值守入口统一设 `ANTHROPIC_DEFAULT_OPUS_MODEL`/`…_SONNET_MODEL`。同时设 `DISABLE_AUTOUPDATER=1`,让「换模型 / 升级 Claude Code」各自成为一次显式变更,并必须走回归。
- **(a) 19K→50K 很可能就是官方说的现象。** 官方原话是「同档位 Opus 5.5 比 Opus 5 想得更多,尤其 max」。若确是 Opus 5→5.5:50/19≈2.6 倍思考 token,乘以单价 0.8,约 2.1 倍,与「成本翻倍」吻合。需从 transcript 里的 model 字段核实。
- **加「行为指纹」控制图。** 每类 agent 每晚记录:实际 model ID、effort、turns、各工具调用数、搜索数、思考/输出 token、hook 拒绝次数、契约一次通过率。model ID 一变就硬告警,其余按历史分位告警。
- **(c) 模板和 validator 用同一份 schema 生成。** 在 CI 中把模板示例送进 validator 做往返测试,并 lint 禁用短语。模板改动当成代码改动,跑回归。
- **(d) 先测噪声地板再谈漂移。** 同一冻结输入重跑 k 次,算评级一致性。listwise 排序要固定或随机化候选顺序,并记录顺序;可试「置换自一致」。

---

## 2. Eval-driven agent development

### Evidence
- **Demystifying evals (Anthropic, 2026-01)** (F8):
  - Vocabulary: task, trial, grader, transcript, outcome, harness.
  - Grader trade-offs. Code-based graders are "Fast, Cheap, Objective, Reproducible" but "Brittle." Model-based graders are "Flexible… Non-deterministic… Requires calibration." Human graders are the "Gold standard" but "Expensive, Slow."
  - Capability evals "start at a low pass rate"; regression evals sit near 100%.
  - "Grade what the agent produced, not the path it took."
  - For research agents, use groundedness checks plus coverage checks.
  - Test "both the cases where a behavior should occur and where it shouldn't."
  - LLM rubrics should be "frequently calibrated against expert human judgment."
  - On model upgrades: "teams without evals face weeks of testing."
- **Multi-agent research system (Anthropic, 2025-06)** (F9):
  - Token cost: agents use about 4× the tokens of chat, and multi-agent systems about 15×.
  - Results: multi-agent beat single-agent Opus 4 by 90.2% on an internal eval. Token usage explained about 80% of variance.
  - Poor fit: tasks where agents must "share the same context or involve many dependencies."
  - Delegations need "an objective, an output format, guidance on the tools and sources to use, and clear task boundaries."
  - Effort scaling: "1 agent with 3-10 tool calls" for fact-finding, and "2-4 subagents with 10-15 calls each" for comparisons.
  - Early failures included "spawning 50 subagents for simple queries" and "scouring the web endlessly."
  - Evaluation started with about 20 queries and an LLM judge in a single call that outputs 0.0–1.0 plus pass/fail.
  - Subagents write outputs to the filesystem, which avoids the lead agent's "game of telephone."
- **Context engineering (2025-09)** (F10):
  - "smallest possible set of high-signal tokens."
  - Write prompts at the "right altitude": neither brittle logic nor vague guidance.
  - Avoid "bloated tool sets" and "ambiguous decision points."
  - Use "diverse, canonical examples," not "a laundry list of edge cases."
  - Retrieve just in time via lightweight identifiers.
  - Use compaction and tool-result clearing.
  - Subagents return a condensed summary, "often 1,000-2,000 tokens."
- **Tools (2025-09)** (F11): Claude Code caps tool responses at 25,000 tokens by default. A concise response format used 72 tokens vs 206 for the detailed one. Keep held-out test sets.
- **Long-running harnesses (2025-11)** (F12): one feature at a time, progress files, and strongly worded invariants ("It is unacceptable to remove or edit tests").
- **Claude 5-family guidance** (F5–F7):
  - "Run an effort sweep on your own evals rather than carrying settings over."
  - Opus 5.5 at `medium` "matches or exceeds Claude Opus 5 at `high`" on coding and knowledge work. This is Anthropic's own claim.
  - Advisory time budgets made agent teams finish sooner with "answer quality comparable." Under time pressure the model "might search and verify a little less."
- **Other industry guidance.**
  - OpenAI's agent guide: set a baseline with the most capable model, then swap in smaller models where evals hold, and prefer a single agent first (M5).
  - Cognition argues that splitting work across agents loses implicit decisions and context (M3).
  - The MAST taxonomy (M2) puts "task verification" failures alongside specification failures.

### Implications for the system (hypotheses)
- **用真实事故建 20–50 条回归任务。** 例如:翻 validator 源码、模板禁用短语、早停判定、五档解析、引用落点。按 pass^3 计稳定性,不看单次通过。
- **grader 分层。** 格式、数字出处、读路径、禁用词全部用代码判;「判断质量」用 LLM-judge 判,再用人工小样本校准。避免用 LLM 判 LLM 的契约格式。
- **多 agent 只用于广度并行取证。** 适用于逐票 intel 这类任务;排序和决策卡这种强耦合判断留在单上下文。15 倍 token 只在价值足够时划算。
- **派发包写全四要素。** 目标、输出格式、工具与来源、边界,外加显式的搜索/调用次数预算(参照 3–10 次的事实采集规则)。
- **每次换模型或 harness 都在冻结输入上做 effort sweep。** 档位由第 4 节的等价门决定。

---

## 3. Token/cost reduction without quality loss

### (i) Effort and thinking vs quality
- **Vendor guidance.**
  - Effort "affects all tokens… Text… Tool calls… Thinking"; "Lower effort also means fewer and terser tool calls" (F5).
  - `low` is meant for "Simpler tasks… such as subagents"; "Effort is a behavioral signal, not a strict token budget" (F5).
  - Opus 4.7's table says `max`: "On most workloads `max` adds significant cost for relatively small quality gains, and on some structured-output or less intelligence-sensitive tasks it can lead to overthinking" (F5).
  - "Reserve `xhigh` and `max` for work where you've measured a quality gain." "Lowering effort reduces thinking… more reliably than prompt instructions do" (F7).
  - Claude Code: thinking "can be tens of thousands of tokens per request" (F36).
- **Research findings.**
  - Inverse scaling: longer reasoning can lower accuracy. Claude models become "increasingly distracted by irrelevant information" and shift "from reasonable priors to spurious correlations" (F21).
  - Agentic overthinking (4,018 SWE trajectories): picking low-overthinking runs gave "~30%" better performance and 43% lower compute cost (F22).
  - o1-style models spend excessive compute on simple problems "with minimal benefit" (F23).
- **Gap.** No primary source in this session measures effort vs quality separately for **extraction/fact-collection** vs **judgment** tasks on Claude 5-family models. Only the vendor heuristics above exist.

### (ii) Prompt caching on the Claude API (all [FETCHED], F1/F2/F37)
- **Pricing multipliers:**
  - 5-minute cache write: 1.25× the base input price.
  - 1-hour cache write: 2×.
  - Cache read: 0.1×; **0.05× on Opus 5.5** ($0.20/MTok); 0.025× on Fable/Mythos 5.1.
  - Caching "pays off after one cache read" at the 5-minute TTL, or after two reads at the 1-hour TTL.
  - Multipliers stack with the Batch discount (50%).
- **Limits and matching rules:**
  - Default TTL is 5 minutes; set the 1-hour TTL with `"ttl": "1h"`.
  - The cache is "refreshed for no additional cost each time the cached content is used."
  - The lifetime "is measured from the start of the request," so streaming time counts against it.
  - Up to 4 breakpoints; a 20-block lookback.
  - Minimum cacheable length is 512 tokens on 5.x models. Below that, caching silently does nothing ("no error is returned").
  - Hits require an exact match: "100% identical prompt segments."
- **What invalidates the cache.** The hierarchy runs tools → system → messages: "Changes at each level invalidate that level and all subsequent levels." Thinking or effort changes "always invalidate message blocks." Per-message effort (beta) keeps the cache on Opus 5.5.
- **Parallel fan-out:**
  - "A cache entry only becomes available after the first response begins. If you need cache hits for parallel requests, wait for the first response before sending subsequent requests."
  - So N truly simultaneous same-prefix requests get no hits. That each one is billed as a cache write is my inference; the docs do not say so explicitly.
  - Claude Code workflow fan-outs "hold all but the first for up to 5 seconds" so the rest can read the first agent's cache.
  - A subagent's first request "doesn't read the parent's cache."
  - On a subscription, subagents get a 5-minute TTL unless set via `subagentPromptCacheTtl`, `CLAUDE_CODE_SUBAGENT_PROMPT_CACHE_TTL`, or `experimental.cacheTtl`.
  - Parallel sessions in the same directory share prefixes. Sequential sessions share only if the git-status snapshot matches.

### (iii) Cascades and routing
- FrugalGPT matched GPT-4 "with up to 98% cost reduction" (F25).
- RouteLLM cut costs "by over 2 times in certain cases—without compromising the quality" (F26).
- Anthropic suggests routing easy cases to smaller models (F13), and a small-model completion checker (F7).
- **Caveat.** These papers validate **average quality**, not per-item decision identity. "No decision change" needs the paired tests in §4.

### (iv) Context engineering
- Context editing cut tokens by 84% in a 100-turn web-search eval. Memory plus context editing improved performance by 39%, and editing alone by 29% (F31).
- Programmatic tool calling reduced tokens 43,588 → 27,297 (−37%) and raised accuracy on two benchmarks (F32). Tool search cut tool-definition tokens by 85% (F32).
- Code execution with MCP took 150K → 2K tokens (−98.7%) (F34).
- Claude Code (F36):
  - Hooks can preprocess output, e.g. grep a log instead of reading it.
  - Keep CLAUDE.md "under 200 lines" and move workflow-specific instructions into skills.
  - "Agent teams use approximately 7x more tokens."

### (v) Output-side savings
- Chain of Draft matched CoT accuracy with "as little as only 7.6% of the tokens" (F27). That result is for visible reasoning.
- Strict format restrictions hurt reasoning more than classification (F24). There is a rebuttal disputing the size of the effect (M11).
- On Opus 5, effort "does not reliably shorten responses… prompt for length instead" (F5).
- On Opus 5.5, prompts that push the model "to reproduce its internal reasoning in the response text may be declined" with the `reasoning_extraction` refusal category (F7).

### (vi) Web-search-heavy agents
- **Cost:** $10 per 1,000 searches, plus result tokens, "counted as input tokens… in subsequent conversation turns" (F2, F33).
- **Bounding search count:**
  - `max_uses` is a hard cap (exceeding it returns the error `max_uses_exceeded`).
  - "Simple factual queries typically use 1–3 searches; comparative or multientity research can use 10 or more."
  - Dynamic filtering (`web_search_20260209+`) lets the model filter results in code before they reach context; that code execution is free (F33, F2).
- **Fetch costs:** a typical page is ~2,500 tokens and a PDF ~125K; cap fetches with `max_content_tokens` (F2).
- **Claude Code's WebFetch** answers against the page "using a small fast model" and caches responses "15 minutes per URL" (O1).

### Implications for the system (hypotheses)
- **最大杠杆很可能是 effort。** 卡片 agent 从 max 降到 high/medium;intel 事实采集降到 low/medium(低档调用更少更短)。但必须先过第 4 节等价门,不可直接换。
- **子 agent 默认 5 分钟 TTL,且同时起跑互相读不到缓存。** 若 L4 并行走 Workflow fan-out,已有 5 秒错峰;若走 Agent 工具直接并行,可显式错峰首请求。把共享静态前缀(定义+共享地形)放前、个股变量放后;Opus 5.5 读缓存仅 0.05 倍。
- **研究型子 agent 设 `omitClaudeMd: true`。** 省掉长 CLAUDE.md,也去掉其中指向源码/validator 的线索(可能是诱发「翻源码自验」的因素之一)。再配 `maxTurns` 封顶。
- **搜索预算交给 hook 硬执行。** 用 PreToolUse hook 对 WebSearch/WebFetch 按 agent 计数、超额拒绝(仿 `max_uses`)。intel 页面按 URL 去重落盘复用。
- **卡片只写决策字段+证据指针。** 不复述 slim/intel 原文,也不要求在正文写出完整推理(贵,且 Opus 5.5 可能拒答)。
- **主会话膨胀。** 子 agent 落盘、只回一行 VERDICT;在自然断点 /compact。或把编排整体交给确定性脚本,LLM 只在叶子判断。

---

## 4. Validating "same decisions at lower cost"

### Evidence and methods
- **Treat evals as experiments** (F28). Use CLT standard errors, **clustered** standard errors for related items, **paired differences** when two configurations see the same items, and **power analysis** before running.
- **Measure the noise floor.**
  - Total agreement rate on parsed answers over N reruns (TARa@N) (F39).
  - Repetition, rephrase and system-prompt variability in ChatGPT/Claude stock picks were "statistically significant." The remedy is to keep picks that recur across repeated and rephrased queries (N1).
  - Anthropic's pass^k for consistency (F8).
- **Equivalence, not "no significant difference."**
  - TOST with a pre-registered margin (M7).
  - A 2026 reasoning-compression paper used TOST with a ±2-percentage-point margin. Of 15 cells, only 2 passed equivalence and 11 were **inconclusive** (mostly at N=30); 2 showed real degradation (F29). An underpowered study yields "inconclusive," not "same."
- **Agreement statistics** (M8, M9):
  - Cohen's κ for two raters, Fleiss' κ for many, weighted κ or Krippendorff's α for **ordinal** 5-tier ratings. Common thresholds are α ≥ 0.80 for reliable and ≥ 0.667 for tentative conclusions.
  - Under skewed prevalence κ is deflated (the "kappa paradox"), so also report raw agreement and Gwet's AC1.
  - For paired binary decisions use McNemar's test. For finalist sets use Jaccard or rank-biased overlap (RBO).
- **Distribution-level tests.** MMD equality testing on outputs works with about 10 samples per prompt (F19).
- **Cost-saving designs.**
  - Adaptive stopping (optstop) dropped "57%-97% of planned trials" while keeping the same conclusions (F30).
  - Shadow or rainbow rollouts (F9).
  - Permutation self-consistency to remove order effects in listwise ranking (F40).

### Sample-size arithmetic (standard TOST formula for paired binary outcomes, M7; my own computation)
Formula: n ≈ (z₀.₉₅ + z₀.₉₀)² · p_disc / δ², assuming a true difference of 0, α = 0.05 and power 0.8. Here p_disc is the paired discordance rate and δ is the equivalence margin.

| Paired discordance rate | Margin δ | Pairs needed (n) |
|---|---|---|
| 20% | ±5 percentage points | ≈ 685 |
| 20% | ±10 percentage points | ≈ 171 |
| 10% | ±5 percentage points | ≈ 343 |

A night with about 10 cards cannot establish equivalence. You need historical replays on frozen inputs or weeks of shadow runs.

### Implications for the system (hypotheses)
- **协议。** 冻结输入(湖快照+intel/slim 文件+候选表)。基线配置重跑 3 次,得到自一致性(噪声地板);候选配置重跑 3 次,得到交叉一致性。预注册判据:交叉一致不低于自一致减边际;关键二元决策(是否 ≥OW/是否进 finalist/是否 BUY)做配对 McNemar+TOST。
- **评级五档用有序指标。** 用加权 κ 或 Krippendorff α;分布偏(多为 Hold/UW)时同时报原始一致率和 AC1。
- **两引擎 finalist 不重叠,先看各自同引擎重跑的重叠度(Jaccard/RBO)。** 若同引擎自己都不稳,问题在噪声而不是引擎差异。可用打乱候选顺序后聚合的办法降低位置偏差。
- **把 intel 网查与判断层分开验证。** 判断层在冻结 intel 上测;网查层单独看事实覆盖和引用落点。否则网页时变会混进「决策变化」。
- **用自适应停样省回放成本。** 不确定的票多跑、稳定的少跑。

---

## Sources

### [FETCHED]: opened this session
- F1 — Prompt caching (docs), Anthropic, 2026 — https://platform.claude.com/docs/en/build-with-claude/prompt-caching
- F2 — Pricing (docs), Anthropic, 2026 — https://platform.claude.com/docs/en/about-claude/pricing
- F3 — Models overview (docs), Anthropic, 2026 — https://platform.claude.com/docs/en/about-claude/models/overview
- F4 — Model configuration (Claude Code docs), Anthropic, 2026 — https://code.claude.com/docs/en/model-config
- F5 — Effort (docs), Anthropic, 2026 — https://platform.claude.com/docs/en/build-with-claude/effort
- F6 — Migrating to Claude Opus 5.5 (docs), Anthropic, 2026 — https://platform.claude.com/docs/en/models/opus-5-5/migration-guide
- F7 — Prompting Claude Opus 5.5 (docs), Anthropic, 2026 — https://platform.claude.com/docs/en/build-with-claude/prompt-engineering/prompting-claude-opus-5-5
- F8 — Demystifying evals for AI agents, Grace, Hadfield, Olivares, De Jonghe (Anthropic), 2026 — https://www.anthropic.com/engineering/demystifying-evals-for-ai-agents
- F9 — How we built our multi-agent research system, Hadfield et al. (Anthropic), 2025 — https://www.anthropic.com/engineering/multi-agent-research-system
- F10 — Effective context engineering for AI agents, Rajasekaran et al. (Anthropic), 2025 — https://www.anthropic.com/engineering/effective-context-engineering-for-ai-agents
- F11 — Writing effective tools for agents, K. Aizawa (Anthropic), 2025 — https://www.anthropic.com/engineering/writing-tools-for-agents
- F12 — Effective harnesses for long-running agents, J. Young (Anthropic), 2025 — https://www.anthropic.com/engineering/effective-harnesses-for-long-running-agents
- F13 — Building effective agents, E. Schluntz & B. Zhang (Anthropic), 2024 — https://www.anthropic.com/engineering/building-effective-agents
- F14 — How is ChatGPT's behavior changing over time?, Chen, Zaharia, Zou, 2023 — https://arxiv.org/abs/2307.09009
- F15 — Context Rot, Hong, Troynikov, Huber (Chroma), 2025 — https://www.trychroma.com/research/context-rot
- F16 — Agent Drift: Quantifying Behavioral Degradation in Multi-Agent LLM Systems, A. Rath, 2026 — https://arxiv.org/abs/2601.04170
- F17 — Quantifying non-deterministic drift in LLMs, C. Nicholson, 2026 — https://arxiv.org/abs/2601.19934 (temperature 0 does not remove variability; not cited above)
- F18 — Evaluating Goal Drift in Language Model Agents, Arike, Donoway, Bartsch, Hobbhahn, 2025 — https://arxiv.org/abs/2505.02709
- F19 — Model Equality Testing: Which Model Is This API Serving?, Gao, Liang, Guestrin, 2024 — https://arxiv.org/abs/2410.20247
- F20 — LLMs Get Lost In Multi-Turn Conversation, Laban et al., 2025 — https://arxiv.org/abs/2505.06120
- F21 — Inverse Scaling in Test-Time Compute, Gema et al., 2025 (TMLR) — https://arxiv.org/abs/2507.14417
- F22 — The Danger of Overthinking, Cuadron et al., 2025 — https://arxiv.org/abs/2502.08235
- F23 — Do NOT Think That Much for 2+3=?, X. Chen et al., 2024 — https://arxiv.org/abs/2412.21187
- F24 — Let Me Speak Freely?, Tam et al., 2024 — https://arxiv.org/abs/2408.02442
- F25 — FrugalGPT, Chen, Zaharia, Zou, 2023 — https://arxiv.org/abs/2305.05176
- F26 — RouteLLM, Ong et al., 2024 — https://arxiv.org/abs/2406.18665
- F27 — Chain of Draft, Xu et al., 2025 — https://arxiv.org/abs/2502.18600
- F28 — Adding Error Bars to Evals, E. Miller (Anthropic), 2024 — https://arxiv.org/abs/2411.00640
- F29 — Shorthand for Thought (TOST ±2pp), Zhao, Land, Bikel, Alshikh, 2026 — https://arxiv.org/abs/2604.26355
- F30 — Knowing When to Stop: Bayesian Optimal Stopping for LLM Evaluations, T. Pilditch, 2026 — https://arxiv.org/abs/2608.14425
- F31 — Managing context on the Claude Developer Platform, Anthropic, 2025 — https://claude.com/blog/context-management
- F32 — Introducing advanced tool use, B. Wu et al. (Anthropic), 2025 — https://www.anthropic.com/engineering/advanced-tool-use
- F33 — Web search tool (docs), Anthropic, 2026 — https://platform.claude.com/docs/en/agents-and-tools/tool-use/web-search-tool
- F34 — Code execution with MCP, A. Jones & C. Kelly (Anthropic), 2025 — https://www.anthropic.com/engineering/code-execution-with-mcp
- F35 — Subagents (Claude Code docs), Anthropic, 2026 — https://code.claude.com/docs/en/sub-agents
- F36 — Manage costs effectively (Claude Code docs), Anthropic, 2026 — https://code.claude.com/docs/en/costs
- F37 — How Claude Code uses prompt caching, Anthropic, 2026 — https://code.claude.com/docs/en/prompt-caching
- F38 — Structured outputs (docs), Anthropic, 2026 — https://platform.claude.com/docs/en/build-with-claude/structured-outputs
- F39 — Non-Determinism of "Deterministic" LLM Settings, Atil et al., 2024/2025 — https://arxiv.org/abs/2408.04667
- F40 — Found in the Middle: Permutation Self-Consistency…, Tang, Zhang, Ma, Lin, Ture, NAACL 2024 — https://arxiv.org/abs/2310.07712

### Search snippet only
- N1 — Multifaceted variability in LLM-driven stock recommendations, Finance Research Letters, 2025 — https://www.sciencedirect.com/science/article/abs/pii/S1544612325021762 (HTTP 403 on open; authors not verified)

### Observed in this session
- O1 — Claude Code WebFetch tool description (as presented to this agent), 2026.

### [MEMORY]: not re-verified
- M1 — Sclar et al., "Quantifying Language Models' Sensitivity to Spurious Features in Prompt Design," ICLR 2024 — https://arxiv.org/abs/2310.11324
- M2 — Cemri et al., "Why Do Multi-Agent LLM Systems Fail?" (MAST), 2025 — https://arxiv.org/abs/2503.13657
- M3 — W. Yan (Cognition), "Don't Build Multi-Agents," 2025 — https://cognition.ai/blog/dont-build-multi-agents
- M4 — H. He / Thinking Machines Lab, "Defeating Nondeterminism in LLM Inference," 2025 — https://thinkingmachines.ai/blog/defeating-nondeterminism-in-llm-inference/
- M5 — OpenAI, "A practical guide to building agents," 2025 — https://cdn.openai.com/business-guides-and-resources/a-practical-guide-to-building-agents.pdf
- M6 — Ribeiro et al., "Beyond Accuracy: Behavioral Testing of NLP Models with CheckList," ACL 2020 — https://aclanthology.org/2020.acl-main.442/
- M7 — Lakens, "Equivalence Tests: A Practical Primer…," SPPS 2017; Schuirmann 1987 (TOST)
- M8 — Krippendorff, *Content Analysis* (α thresholds), 2004; Landis & Koch 1977; Feinstein & Cicchetti 1990 (kappa paradox); Gwet 2008 (AC1)
- M9 — Webber, Moffat, Zobel, "A similarity measure for indefinite rankings" (RBO), ACM TOIS 2010
- M10 — Wang et al., "Self-Consistency Improves Chain of Thought Reasoning," ICLR 2023 (background, not cited above)
- M11 — .txt (dottxt), "Say What You Mean: A Response to 'Let Me Speak Freely'," 2024 — https://blog.dottxt.co/say-what-you-mean.html
