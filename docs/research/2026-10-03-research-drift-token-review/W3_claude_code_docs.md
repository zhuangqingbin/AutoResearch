# W3: Claude Code and Claude API documentation answers (fetched 2026-10-03)

Method. I downloaded the raw markdown of each official page (code.claude.com/docs/en/<page>.md and platform.claude.com/docs/en/<page>.md) with curl, grepped it, and read the relevant sections. Quotes below are verbatim from those pages (markdown link markup stripped, "..." marks an elision). The Claude Code docs describe the product up to v2.1.287 (changelog entry dated 2026-10-01). Anything I could not find stated in the docs is marked UNCONFIRMED. Items marked INFERENCE are my reading of documented facts, not something a doc says.

Short URL key (all under https://code.claude.com/docs/en/ unless noted):
- SA = sub-agents, MC = model-config, ST = settings, SR = settings-reference, EV = env-vars, CLI = cli-reference, HL = headless, CO = costs, HK = hooks, PC = prompt-caching (Claude Code's page), WF = workflows, AD = advisor, SU = setup, CL = changelog
- Platform (https://platform.claude.com/docs/en/): PCa = build-with-claude/prompt-caching, PR = about-claude/pricing, MO = about-claude/models/overview, EF = build-with-claude/effort

---------------------------------------------------------------------------

## 0. Findings that matter most for the incident (details and quotes in the sections below)

A. The alias flip is a Claude Code client release effect, and it is dated in the docs.
- MC "Version history": "This table lists the Claude Code version at which each model alias changed the model it resolves to, newest first." Rows: "v2.1.284 | `sonnet` resolves to Sonnet 5.5 on the Anthropic API"; "v2.1.280 | `opus` resolves to Opus 5.5 on the Anthropic API, ..."; "v2.1.219 | `opus` resolves to Opus 5 ..."; "v2.1.197 | `sonnet` resolves to Sonnet 5 on the Anthropic API".
- CL v2.1.280 (September 22, 2026): "Added Claude Opus 5.5 (`claude-opus-5-5`), now the default Opus model — 1M context, $4/$20 per Mtok with $0.20/Mtok cache reads". CL v2.1.284 (September 28, 2026): "Added Claude Sonnet 5.5 (`claude-sonnet-5-5`), now the default Sonnet model on the Anthropic API — 1M context, $2/$10 per Mtok with $0.20/Mtok cache reads".
- SU "Auto-updates": "Claude Code checks for updates on startup and periodically while running. Updates download and install in the background, then take effect the next time you start Claude Code." Native installs auto-update by default.
- INFERENCE: two runs about 10 days apart that straddle Sep 22 / Sep 28, launched from launchd on an auto-updating native install, reproduce exactly "opus: 5 -> 5.5, sonnet: 5 -> 5.5 with no project change". To confirm, read the Claude Code version at each run (`claude --version`; `claude doctor` shows the last update attempt). Version at each past run: UNCONFIRMED (not in the docs, depends on your machine).

B. List price did not go up, so price alone cannot explain a 2x cost per run (PR / MO):
- Opus 5.5 is cheaper than Opus 5: $4 / $20 vs $5 / $25 per MTok input/output, cache read $0.20 vs $0.50, 5m write $5 vs $6.25, 1h write $8 vs $10.
- Sonnet 5.5 has the same prices as Sonnet 5 ($2 / $10; cache read $0.20). Sonnet 5.5 migration guide: "Claude Sonnet 5.5 has the same prices as Claude Sonnet 5, including prompt caching and batch processing rates."
- INFERENCE: a doubling therefore has to come from token volume (more thinking/output, more or longer turns, more spawned agents) or a different model mix, not from per-token price.

C. Documented token-volume drivers when moving to the 5.5 models at an unchanged effort setting:
- Opus 5.5 prompting guide (platform.claude.com/docs/en/build-with-claude/prompt-engineering/prompting-claude-opus-5-5, "Calibrate effort"): "At a given level, Claude Opus 5.5 tends to think more per turn than Claude Opus 5, especially at `xhigh` and `max`. If you keep the `effort` value you set for Claude Opus 5, expect longer turns and more output tokens."
- Opus 5.5 what's-new (platform.claude.com/docs/en/models/opus-5-5/whats-new-opus-5-5): "More thinking per turn at a given effort level. At the same effort setting the model tends to think more per turn than Claude Opus 5, most of all at `xhigh` and `max`. Re-run your effort sweep rather than carrying a setting over, and leave room in `max_tokens` for the thinking."
- Sonnet 5.5 prompting guide: "Its levels are recalibrated: a level doesn't produce the same amount of thinking as the same level on Claude Sonnet 5." and "Thoroughness at `xhigh` and `max` effort. At these levels the model is especially thorough. After it finishes a task, it can start its own rounds of review and verification, sometimes with subagents if your harness provides them. ... This takes more time and tokens, so run routine work at `high` or below, where it's rare." (It also says a system-prompt line that stops self-started review "cut session cost by about a third" on `max`.)
- Thinking cannot be turned off on the 5.5 models (MC "Extended thinking"): "You can't turn thinking off on Opus 5.5, Sonnet 5.5, or the Fable models. ... a saved `alwaysThinkingEnabled: false` or `MAX_THINKING_TOKENS=0` has no effect there." Opus 5 accepted `thinking: disabled` at `high` or below (Opus 5.5 what's-new: "On Claude Opus 5, thinking is on by default and `thinking: {"type": "disabled"}` is accepted at effort `high` or below. On Claude Opus 5.5, thinking is always on"), so any setup that had thinking off on Opus 5 now thinks on 5.5. Whether your setup had thinking off: UNCONFIRMED (not knowable from the docs).
- Claude Code default effort differs by model: MC "Adjust effort level": the model's default effort is "`high` on every model that supports effort, except that Opus 5.5 and Sonnet 5.5 default to `medium`, Opus 4.7 defaults to `xhigh`". Your agents set `effort` explicitly, so this only matters for agents without `effort:` and for the main session. CL v2.1.280 also: "Changed an effort level saved before `/effort` became per-model to no longer apply to newly released models such as Opus 5.5; they start at their default until you pick a level".
- Main-session default changed too: CL v2.1.280: "Changed the default model on Pro and Team Standard plans from Sonnet to Opus, matching Max, Team Premium, and Enterprise". If the main session runs on "Default" (not a saved explicit model) on such a plan, it moved from Sonnet 5 to Opus 5.5 at that version. Whether this applies to you: UNCONFIRMED (depends on plan and on what `/model` saved).

D. The one documented way to make a subagent's model immune to alias movement is a full model ID in the frontmatter (`model: claude-opus-5-5`, `model: claude-sonnet-5`). Model IDs are pinned snapshots (platform.claude.com/docs/en/about-claude/models/model-ids-and-versions): "Anthropic does not update the weights or configuration of an existing model ID. When an updated version is available, it ships under a new model ID." Caveat for aliases: a family alias in frontmatter resolves to the MAIN conversation's exact model when the main model is in the same family (Q1).

E. Prompt caching and parallel subagents: each subagent builds its own cache; subagents get a 5-minute TTL by default even on a subscription; the API says a cache entry "only becomes available after the first response begins", so parallel requests sent together cannot read each other's cache. Claude Code documents an automatic stagger only for Workflow fan-outs (Q5).

---------------------------------------------------------------------------

## Q1. Subagent frontmatter `model:`: accepted values, alias resolution, pinning

Source: SA (https://code.claude.com/docs/en/sub-agents), sections "Choose a model" and "Frontmatter reference"; MC (https://code.claude.com/docs/en/model-config) "Model aliases", "Version history"; platform model-ids page.

Accepted values (SA "Choose a model"):
> The `model` field controls which model the subagent uses:
> * **Model alias**: use one of the available aliases: `sonnet`, `opus`, `haiku`, or `fable`
> * **Full model ID**: use a full model ID such as `claude-opus-5-5` or `claude-sonnet-5`. Accepts the same values as the `--model` flag
> * **inherit**: use the same model as the main conversation

Frontmatter table row (SA): "`model` | No | Model to use: `sonnet`, `opus`, `haiku`, `fable`, a full model ID such as `claude-opus-5-5`, or `inherit`. When you omit it, Claude Code picks the model in the subagent model order".

Resolution order (SA "Choose a model"):
> When Claude invokes a subagent, it can also pass a `model` parameter for that specific invocation. Claude Code resolves the subagent's model in this order:
> 1. The per-invocation `model` parameter
> 2. The subagent definition's `model` frontmatter, where `inherit` selects the main conversation's model
> 3. The `CLAUDE_CODE_SUBAGENT_MODEL` environment variable, when you set it to a model alias or model ID
> 4. The main conversation's model

Order changed in v2.1.251 (SA): "Before v2.1.251, `CLAUDE_CODE_SUBAGENT_MODEL` came first in this order and overrode both the per-invocation parameter and the frontmatter, including `model: inherit`."

The family-alias rule that matters for you (SA "Choose a model"):
> In two cases, a family alias such as `opus` in the per-invocation parameter or the frontmatter resolves to the main conversation's model instead of the version the alias points to:
> * **The main conversation's model belongs to that family**: the subagent runs on the main conversation's exact model, including any `[1m]` suffix, so it gets the same extended context window as the main conversation.
> * **Claude Code can't tell the main conversation's model family, on a provider other than the Anthropic API**: ... This case covers only the `opus` alias, and doesn't apply when you set `ANTHROPIC_DEFAULT_OPUS_MODEL`, since `opus` then resolves to the model you set.
Also: "An alias in `CLAUDE_CODE_SUBAGENT_MODEL` always resolves to the version the alias points to, even when it names the main conversation's family."
- Reading: with `model: opus` in a subagent and a main session whose model is any Opus, the subagent runs on the main session's exact Opus, not on whatever `opus` points to. The first case has no stated exception for `ANTHROPIC_DEFAULT_OPUS_MODEL` (the exception quoted applies only to the second case). With the main session on a different family (Sonnet, Fable), `opus` resolves through the alias. So with a full model ID in frontmatter you remove this dependency on the main model.

The main session's `/model` choice also reaches subagents (MC "Setting your model"): "If you switch models with `/model`, the switch also reaches subagents that inherit the main conversation's model, because Claude Code resolves their model from the one your session is using when Claude starts them. Switch to Opus before Claude delegates research or test runs to one of them, and that work runs on Opus too. To keep a custom subagent on a smaller model, set `model` in its definition."

How aliases resolve and when they move (MC "Model aliases"):
> | **`sonnet`** | Uses the latest Sonnet model for daily coding tasks |
> | **`opus`** | Uses the latest Opus model for complex reasoning tasks |
> | **`haiku`** | Uses the fast and efficient Haiku model for simple tasks |
> The version that the `opus` and `sonnet` aliases resolve to depends on the provider: ... Anthropic API | Opus 5.5 | Sonnet 5.5 ...
> Aliases point to the recommended version for your provider and update over time. To pin to a specific version, use the full model name, for example `claude-opus-5-5`, or set the corresponding environment variable like `ANTHROPIC_DEFAULT_OPUS_MODEL`.
> Sonnet 5.5 requires Claude Code v2.1.284 or later, and Opus 5.5 requires v2.1.280 or later.
Version history table and changelog dates: see section 0.A. The alias is resolved by the Claude Code binary; it moves when the installed Claude Code version changes, not on a server-side clock.
- Related changelog item (CL v2.1.286, Sept 30): "Fixed every turn failing when the Anthropic API refuses the model your default or a model alias resolves to: Claude Code now retries once on the previous model of the same tier". So an alias can also silently land on the previous model of the same tier in that failure case. Exact trigger conditions: UNCONFIRMED (only the changelog line).

Documented ways to pin an exact model for a subagent:
1. Full model ID in the subagent file: `model: claude-opus-5-5` (SA quote above). Pinned snapshot per platform docs: "Each Claude model ID identifies a pinned version of the model. When you use a model ID in an API request, the underlying model remains constant for the lifetime of that ID."
2. `ANTHROPIC_DEFAULT_OPUS_MODEL` / `ANTHROPIC_DEFAULT_SONNET_MODEL` / `ANTHROPIC_DEFAULT_HAIKU_MODEL` pin what the alias means. MC: "With the `[1m]` suffix, the 1M context window applies to all usage of the pinned alias, including the plan-mode Opus phase of `opusplan` and subagents whose `model` frontmatter names the alias." (so these variables do govern subagent frontmatter aliases). Subject to the family rule above when the main model is in the same family.
3. `CLAUDE_CODE_SUBAGENT_MODEL` sets a default only for agents with no per-invocation model and no frontmatter model. To force one model onto all subagents (SA "Run every subagent on one model"): "To apply one model to every subagent, teammate, and workflow agent, also set `CLAUDE_CODE_SUBAGENT_MODEL_FORCE` to `1`. Requires Claude Code v2.1.257 or later." and "While `CLAUDE_CODE_SUBAGENT_MODEL_FORCE` is on, Claude Code ignores the `model` field in subagent definitions, and Claude can't pass a model when it starts a subagent." This is all-or-nothing, so it cannot keep an opus agent and a sonnet agent apart.
4. The main session's orchestrator can override frontmatter per call: the Agent tool has an optional `model` field (HK PreToolUse "Agent": "`model` | string | `"sonnet"` | Optional model alias to override the default"). The documented setting that removes this is `_FORCE=1` (above). INFERENCE: a PreToolUse hook on the Agent tool could also deny a call whose `tool_input.model` is set (HK documents blocking tool calls with exit code 2 and shows the Agent input schema), but no Agent/model example is documented. Whether a hook's `updatedInput` can rewrite that field: UNCONFIRMED (HK documents `updatedInput` generically: "Modifies the tool's input parameters before execution. Replaces the entire input object").

How to verify which model a subagent actually used:
- SA: "To check which model a subagent is running on, run `/tasks`. Claude Code names the model on the subagent's row, and adds the effort level when the subagent's definition, or the skill it forked from, sets `effort`. Requires Claude Code v2.1.242 or later."
- HK PostToolUse on the Agent tool, `tool_response.resolvedModel`: "Model the subagent started on, which may differ from the requested model"; `modelsUsed`: "Models used in order, with consecutive repeats collapsed; set only when the model was swapped mid-run. Requires Claude Code v2.1.212 or later".
- Headless: MC "Setting your model": "The stderr warning is suppressed for `--output-format json` and `stream-json`; read the actual model from the `modelUsage` field of the result message instead." HL: "The `system/init` event reports session metadata including the model, tools, MCP servers, and loaded plugins." Agent SDK cost-tracking: `modelUsage` "Counts subagent requests alongside the top-level loop, broken down by model" while `usage` excludes subagents.
- Hook common field `effort.level` (also `$CLAUDE_EFFORT`) shows the effort in effect inside hooks, including subagent tool events (HK "Common input fields"). There is no `$CLAUDE_MODEL` variable (HK: "There is no `$CLAUDE_MODEL` environment variable.").

Retirement floors if you pin old IDs (platform.claude.com/docs/en/about-claude/model-deprecations, "Model status"): claude-opus-5-5 "Not sooner than September 22, 2027"; claude-opus-5 "Not sooner than July 24, 2027"; claude-sonnet-5-5 "Not sooner than September 28, 2027"; claude-sonnet-5 "Not sooner than June 30, 2027"; claude-haiku-4-5-20251001 "Not sooner than October 15, 2026"; claude-fable-5-1 "Not sooner than September 1, 2027".

UNCONFIRMED for Q1:
- Whether the `/model` picker saves a family alias (`opus`) or a full ID for Opus/Sonnet. Only evidence: MC says a Fable pick made before v2.1.257 was saved as `claude-fable-5` and then auto-converted to the `fable` alias. Check `model` in `~/.claude/settings.json` to see what you actually saved.
- What Claude Code does with a mistyped frontmatter `model:`. MC states the "There's an issue with the selected model" first-request error only for `--model`, `ANTHROPIC_MODEL` and the `model` setting.

---------------------------------------------------------------------------

## Q2. Environment variables and settings that pin models; precedence; per-project `env`

Sources: MC "Environment variables", "Setting your model", "Restrict model selection"; SA "Choose a model"; EV rows; ST "Settings precedence"; SR `env`, `model`, `availableModels`.

Variable-by-variable (all verbatim):
- `ANTHROPIC_DEFAULT_OPUS_MODEL` (MC): "The model to use for `opus`, or for `opusplan` when Plan Mode is active." EV: "Model ID that the `opus` alias resolves to, and that `opusplan` uses while Plan Mode is active."
- `ANTHROPIC_DEFAULT_SONNET_MODEL` (MC): "The model to use for `sonnet`, or for `opusplan` when Plan Mode is not active."
- `ANTHROPIC_DEFAULT_HAIKU_MODEL` (MC): "The model to use for `haiku`, or background functionality". Also: "Note: `ANTHROPIC_SMALL_FAST_MODEL` is deprecated in favor of `ANTHROPIC_DEFAULT_HAIKU_MODEL`."
- `ANTHROPIC_DEFAULT_FABLE_MODEL` (MC): "The model to use for `fable`, ..." (not asked, but exists).
- `CLAUDE_CODE_SUBAGENT_MODEL` (EV): "The default model for subagents, agent team teammates, and workflow agents that aren't assigned a model another way. Accepts an alias such as `haiku` or a full model name. Two sources take precedence over it: a model Claude passes when it spawns the agent, and a `model` field in the agent's definition, including `inherit`. To change that, set `CLAUDE_CODE_SUBAGENT_MODEL_FORCE`. ... Setting it to `inherit` is the same as leaving it unset. Before v2.1.251, this variable overrode both the per-invocation model and the definition's `model` field". Also SA: "Setting `CLAUDE_CODE_SUBAGENT_MODEL` by itself doesn't change the model the built-in Explore and Plan subagents run on."
- `CLAUDE_CODE_SUBAGENT_MODEL_FORCE` (EV): "Set to `1` to force one model onto subagents, teammates, and workflow agents. ... Requires Claude Code v2.1.257 or later".
- `ANTHROPIC_MODEL` (EV): "Name of the model setting to use". (Main session model.)
- `ANTHROPIC_DEFAULT_MODEL` (EV): "Model that new sessions start on by default. Requires Claude Code v2.1.236 or later."
- `model` in settings (SR): "Set the model every new session uses, so you don't have to pick one with `/model` each time. Setting it here doesn't stop you from switching mid-session." Per-session overrides: "`--model` takes precedence over `ANTHROPIC_MODEL`, and both take precedence over this key for one session, including over a managed `model`; an `availableModels` list still applies to the pick". And "A value here outranks `ANTHROPIC_DEFAULT_MODEL`".
- `availableModels` in settings (SR): "Restrict which models people can select for the main session, subagents, skills, and the advisor." Scope "Any file" (but see below). SA: "Claude Code checks the per-invocation parameter, frontmatter, and environment variable values against your organization's `availableModels` allowlist. For a blocked value, it substitutes another model: When the blocked value is a family alias such as `opus`, Claude Code runs the subagent on the newest version of that family the allowlist permits ... For any other blocked value ... Claude Code runs the subagent on the inherited model instead."
  - As a version freeze it is weak: MC/SR: "A model ID entry such as `claude-opus-5` also permits later versions that extend it, such as Opus 5.5." The exact-match switch is managed-only: `availableModelsMatch` "Scope: Managed. Claude Code ignores the key in user, project, and local settings and in `--settings`, with a warning"; same for `deniedModels`.

Precedence for the MAIN model (MC "Setting your model"):
> You can configure your model in several ways, listed in order of priority:
> 1. During session: use `/model <alias|name>` to switch immediately ...
> 2. At startup: launch with `claude --model <alias|name>`
> 3. Environment variable: set `ANTHROPIC_MODEL=<alias|name>`
> 4. Settings: configure permanently in your settings file using the `model` field
> 5. Default for new sessions: set `ANTHROPIC_DEFAULT_MODEL=<alias|name>`
`/model` writes the saved default: "`/model` saves your choice as the default for new sessions by writing the `model` field in your user settings."

Precedence relative to subagent frontmatter, in one place:
- Frontmatter `model` outranks `CLAUDE_CODE_SUBAGENT_MODEL` (v2.1.251+), and is outranked by Claude's per-invocation `model` parameter and by `_FORCE=1`.
- `ANTHROPIC_DEFAULT_*_MODEL` do not compete with frontmatter; they define what a frontmatter alias (`opus`, `sonnet`, `haiku`) resolves to (quote under Q1 item 2), except when the main model is in the same family (family rule, Q1).
- `ANTHROPIC_MODEL`, `--model` and the `model` setting set the main model, which matters for `inherit`, for agents with no `model`, and for the family rule.
- `availableModels` can substitute any of those values.

Can these be set per project in `.claude/settings.json` under `env`? Yes, documented:
- ST "Settings files and who they affect": "Shared project | `.claude/settings.json` | Everyone working in the folder that contains it ... | Team permissions, hooks, plugins, and the environment variables the project needs".
- SR `env`: "Set environment variables for every session and for the subprocesses Claude Code starts from it. Most variables in the environment variables reference can go here, which is how you apply one to every session or roll it out to your team. Project and local settings can't set some of them." Scope: "Any file". The listed exclusions (config dirs, OS dir vars, OTEL exporter vars, process-wrapper vars) do not include model variables.
- When applied: "From project and local settings: after you trust the workspace, or at startup in `-p` mode, which never shows the trust dialog" and "Variables Claude Code classifies as safe, such as model selection, timeouts and limits, and feature toggles: at startup from every settings file, apart from the variables project and local settings can't set."
- Overwrite behavior: "A value here overwrites the same variable exported in your shell, and when more than one settings file sets a variable, the highest-precedence one applies."
- The docs themselves show the pattern: SA shows `{"env": {"CLAUDE_CODE_SUBAGENT_MODEL": "haiku", "CLAUDE_CODE_SUBAGENT_MODEL_FORCE": "1"}}` "in the `env` block of a settings file"; MC shows `"env": {"ANTHROPIC_DEFAULT_SONNET_MODEL": "claude-sonnet-4-5"}`.
- Settings file order (ST "Settings precedence"): managed, then command-line (`--settings`), then `.claude/settings.local.json`, then `.claude/settings.json`, then `~/.claude/settings.json`. "Environment variables aren't a level in this stack. ... `ANTHROPIC_MODEL` exported in your shell applies over the `model` key from any file, while `ANTHROPIC_DEFAULT_MODEL` applies only when no file sets `model`. ... An `env` block inside a settings file is an ordinary key and follows the levels above."
- A project `model` outranks the user's saved `/model` (MC "A new session starts on a different model than you picked"): "Something with higher priority sets the model. A `model` value in project or managed settings, `ANTHROPIC_MODEL` in your shell, or an organization default your admin set to override user choices applies again at every launch. Your `/model` choice is still saved; it's outranked."
- Whether `env.ANTHROPIC_MODEL` inside a project settings file outranks a `model` key in the user file is not stated in one sentence. It follows from "Claude Code writes each `env` entry into the process environment" (EV "Precedence") plus "Claude Code reads the variable first and uses the `model` ... setting only when the variable is unset", so treat it as UNCONFIRMED as an explicit statement.

---------------------------------------------------------------------------

## Q3. Subagent frontmatter `effort:`

Documented: yes. SA frontmatter table:
> | `effort` | No | Effort level when this subagent is active. Overrides the session effort level. Default: inherits from session. Options: `low`, `medium`, `high`, `xhigh`, `max`; available levels depend on the model |
Also accepted in `--agents` JSON (SA: "Frontmatter fields: `description`, `tools`, `disallowedTools`, `model`, `permissionMode`, `mcpServers`, `hooks`, `maxTurns`, `skills`, `initialPrompt`, `memory`, `effort`, `background`, `omitClaudeMd`, and `isolation`."). SA warning: field names "must match the table exactly: Claude Code ignores a field it doesn't recognize without reporting an error."

What overrides the frontmatter (MC "Set the effort level"):
> Skill and subagent frontmatter: set `effort` in a skill or subagent markdown file to override the effort level when that skill or subagent runs
> Frontmatter effort applies when that skill or subagent is active, overriding the session level but not the environment variable. A `maxEffortLevel` or organization effort cap still limits the level the skill or subagent runs at.
- EV `CLAUDE_CODE_EFFORT_LEVEL`: "Values: `low`, `medium`, `high`, `xhigh`, `max`, or `auto` to use the model default. ... Takes precedence over `--effort`, `/effort`, and the `modelSettings` and `effortLevel` settings. A `maxEffortLevel` cap still applies."
- SR `maxEffortLevel`: "Cap the effort level a session can use, leaving lower levels available. Any higher level runs at the cap instead, including one from `/effort`, the `/model` picker, `--effort`, `CLAUDE_CODE_EFFORT_LEVEL`, a skill's or subagent's `effort` frontmatter, or the model's own default. ... Requires Claude Code v2.1.267 or later." Scope "Any file" (lowest cap across scopes wins); per-model caps via `modelSettings`.

Valid levels and which models support `xhigh` / `max`:
- MC "Adjust effort level":
  > | Fable 5.1 and Fable 5 | `low`, `medium`, `high`, `xhigh`, `max` |
  > | Opus 5.5, Sonnet 5.5, Opus 5, Sonnet 5, Opus 4.8, and Opus 4.7 | `low`, `medium`, `high`, `xhigh`, `max` |
  > | Opus 4.6 and Sonnet 4.6 | `low`, `medium`, `high`, `max` |
  > Models not listed here do not support effort.
  > If you set a level the active model does not support, Claude Code falls back to the highest supported level at or below the one you set. For example, `xhigh` runs as `high` on Opus 4.6.
- EF (API) agrees: `max` on Fable 5.1/5, Mythos, Opus 5.5/5/4.8/4.7/4.6, Sonnet 5.5/5/4.6; `xhigh` on Fable 5.1/5, Mythos 5.1/5, Opus 5.5/5/4.8/4.7, Sonnet 5.5/5; "Not every model that supports `max` supports `xhigh`." Haiku 4.5: MO lists Default effort "Not supported". So `effort:` on a `model: haiku` agent has no supported effect (what Claude Code does with the value then: UNCONFIRMED).

Default when omitted:
- Subagent: "Default: inherits from session." Whether an effort-less subagent running on a different model than the main session uses the session's level or that model's own default: UNCONFIRMED (the table says only "inherits from session").
- Session (MC): "Claude Code resolves the session's effort level in this order, taking the first that applies: 1. An explicit choice: the `CLAUDE_CODE_EFFORT_LEVEL` environment variable, launching with `--effort`, or `/effort` in the session 2. Your settings: the level you saved for the model or an `effortLevel` key ... 3. The model's default effort: `high` on every model that supports effort, except that Opus 5.5 and Sonnet 5.5 default to `medium`, Opus 4.7 defaults to `xhigh` ..."
- Discrepancy between pages for Sonnet 5.5: the platform pages say its default on the Claude API is `high` (MO "Default effort" `high`; Sonnet 5.5 what's-new: "Its default on the Claude API is `high`"), while MC says Claude Code defaults Sonnet 5.5 to `medium`. EF: "Claude Sonnet 5 defaults to `high` effort on the Claude API and Claude Code." Opus 5.5 is `medium` on both.

How effort maps to thinking:
- MC: "Effort levels control adaptive reasoning, which lets the model decide whether and how much to think on each step based on task complexity. Lower effort is faster and cheaper for straightforward tasks, while higher effort provides deeper reasoning for complex problems."
- MC "Adaptive reasoning and fixed thinking budgets": "Fable models, Sonnet 5 and later, and Opus 4.7 and later always use adaptive reasoning. The fixed thinking budget mode and `CLAUDE_CODE_DISABLE_ADAPTIVE_THINKING` don't apply to them. On Opus 4.6 and Sonnet 4.6, you can set `CLAUDE_CODE_DISABLE_ADAPTIVE_THINKING=1` to revert to the previous fixed thinking budget controlled by `MAX_THINKING_TOKENS`."
- EV `MAX_THINKING_TOKENS`: "Claude Code ignores nonzero values on adaptive reasoning models, except on the models where `CLAUDE_CODE_DISABLE_ADAPTIVE_THINKING` turns adaptive reasoning off". CO: "Adaptive-reasoning models ignore nonzero budgets, so use effort levels there instead."
- No per-subagent thinking switch (SA): "As of v2.1.198, subagents also inherit the main conversation's extended thinking configuration: if thinking is on in your session, it's on for the subagent, and if it's off, it stays off. There is no per-subagent thinking setting."
- API semantics (EF): "The effort parameter affects all tokens in the response, including: Text responses and explanations; Tool calls and function arguments; Thinking (when active)". "Effort is a behavioral signal, not a strict token budget." Thinking tokens are billed as output tokens (platform thinking-steering-and-cost "Pricing": "Tokens Claude uses while thinking (billed as output tokens)").
- Cache effect: changing effort invalidates caches on most models; in Claude Code on Opus 5.5, Sonnet 5.5 and Fable 5.1 with an API key or subscription "changing effort keeps the cache" (PC "Changing effort level").

Guidance on choosing effort by task type:
- MC "Choose an effort level":
  > | `low` | Quick exchanges where you review each result, such as brainstorming, a first sketch, or a small change like a rename |
  > | `medium` | The default on Opus 5.5 and Sonnet 5.5, where it fits day-to-day engineering work with a clear scope, such as implementing a new feature. On other models, reduces token usage for cost-sensitive work that can trade off some intelligence |
  > | `high` | Work where verification matters or edge cases are likely, such as fixing a bug in an existing codebase. ... |
  > | `xhigh` | Deeper reasoning at higher token spend. The default on Opus 4.7 |
  > | `max` | Hard problems you want Claude to work through without you, such as finding security vulnerabilities. `max` may show diminishing returns and is prone to overthinking, so test before adopting it broadly |
  > The effort scale is calibrated per model, so the same level name does not represent the same underlying value across models.
  > ... When you move from Opus 5 to Opus 5.5, start at `medium` rather than carrying over the level you used on Opus 5.
- EF effort table: "`low` ... Simpler tasks that need the best speed and lowest costs, such as subagents"; "`xhigh` ... Long-running agentic and coding tasks (over 30 minutes) with token budgets in the millions"; "`max` | Absolute maximum capability with no constraints on token spending."
- EF Opus 5.5: "Run an effort sweep on your own evals rather than carrying settings over from an earlier model". EF Sonnet 5.5: "For agentic coding and multistep tool use, start with `medium` for well-specified tasks and move to `high` for harder or longer ones. ... Use `xhigh` or `max` only where your evals show a quality gain."
- Opus 5.5 prompting guide: "Reserve `xhigh` and `max` for work where you've measured a quality gain." and "To get less thinking, lower the effort level first. Lowering effort reduces thinking, and with it cost and latency, more reliably than prompt instructions do."
- Measured tradeoffs (platform.claude.com/docs/en/about-claude/models/optimizing-for-cost-and-intelligence, "Tune effort"; Anthropic-internal, "directional, not guarantees"): "Long-horizon coding is where effort genuinely buys accuracy. On SWE-bench Pro, measured against `high`, Claude Opus 5.5 scored about 2.5 points lower at its default, `medium`, for about 70% of the cost, and about 8 points lower at `low` for about a third of the cost; `xhigh` scored about 1.4 points higher for 2.5 times the cost of `high`". For research and knowledge-work benchmarks (measured with Fable 5): "`low` gave up 1 to 3 points for a third to a half off the cost per task, `medium` matched the default's accuracy at about 70% to 87% of its cost, and the default bought nothing measurable over `medium` on any of the four." Same page: "Hard work does not automatically need high effort."
- Per-model API guidance for the older model, for context: EF Opus 4.7 `max`: "Reserve for frontier problems. On most workloads `max` adds significant cost for relatively small quality gains, and on some structured-output or less intelligence-sensitive tasks it can lead to overthinking."

UNCONFIRMED for Q3: whether Claude Code applies frontmatter `effort` to a subagent that runs on a model without effort support (Haiku 4.5); the exact value Claude Code sends to the API for each level (docs only say levels are "calibrated per model").

---------------------------------------------------------------------------

## Q4. Headless (`claude -p`): pinning model and effort; does the saved `/model` apply?

Sources: CLI (https://code.claude.com/docs/en/cli-reference), HL (https://code.claude.com/docs/en/headless), MC, EV, ST.

Flags and settings (verbatim rows):
- `--model`: "Sets the model for the current session with a model alias such as `sonnet`, `opus`, `haiku`, or `fable`, or a model's full name. Overrides the `model` setting and `ANTHROPIC_MODEL`".
- `--effort`: "Set the effort level for the current session. Options: `low`, `medium`, `high`, `xhigh`, `max`, or `ultracode`. Available levels depend on the model. ... Overrides the `modelSettings` and `effortLevel` settings for this session and does not persist". It sets the session (main) level; subagent frontmatter `effort` still overrides it for that subagent; `CLAUDE_CODE_EFFORT_LEVEL` overrides both (quotes in Q3).
- `--settings`: "Path to a settings JSON file or an inline JSON string. Values you set here override the same keys in your `settings.json` files for this session. Keys you omit keep their file-based values." ST: "Claude Code applies it above your user, project, and local files and below managed settings. It can set any key your user settings file can set". Since `env` and `model` are "Any file" keys, `--settings '{"model":"claude-opus-5-5","env":{...}}'` is documented usable (ST example: `claude --settings '{"model": "claude-opus-5-5"}'`).
- `--setting-sources`: "Comma-separated list of setting sources to load (`user`, `project`, `local`)." The CLI page does not say more. The Agent SDK page for the same concept says omitting it means all three and that it also gates CLAUDE.md, skills, agents and commands by scope ("Omitting `settingSources` is equivalent to `["user", "project", "local"]`"). INFERENCE: `--setting-sources project,local` would exclude `~/.claude/settings.json` (where `/model` saves) from a headless run. Exact CLI semantics: UNCONFIRMED.
- Env vars: `ANTHROPIC_MODEL`, `CLAUDE_CODE_EFFORT_LEVEL`, `ANTHROPIC_DEFAULT_*_MODEL`, `CLAUDE_CODE_SUBAGENT_MODEL(_FORCE)`, and the `env` block of any settings file (Q2). EV: "Claude Code reads shell environment variables at startup".
- `--agents`: JSON object (or, with `-p`, a path to a JSON file, v2.1.281+) defining subagents including `model`, `effort`, `maxTurns`, `omitClaudeMd`; priority 2 in scope order, above `.claude/agents/` (SA "Choose the subagent scope").
- `--fallback-model`: "Enable automatic fallback to the specified model(s) when the primary model is overloaded or not available ... Accepts a comma-separated list tried in order." MC: "Claude Code also applies the chain to subagents. When a subagent's request fails over, Claude Code tries your configured fallback models in order, and the subagent continues on the model that accepts the request."
- `--max-budget-usd`, `--max-turns`: see Q6.
- `--bare`: see below (it matters for `.claude/agents`).

Does the user's saved `/model` default apply to headless runs? Documented pieces:
- "`/model` saves your choice as the default for new sessions by writing the `model` field in your user settings." (MC)
- SR `model`: "Set the model every new session uses". MC: "A `model` value in any settings file, including the choice you save with `/model`".
- HL: "Without it [--bare], `claude -p` loads the same context an interactive session would, including anything configured in the working directory or `~/.claude`."
- So by these documented statements, a non-bare `claude -p` with no `--model`, no `ANTHROPIC_MODEL`, no project/local `model` uses the `model` saved in `~/.claude/settings.json`. No single sentence says "saved `/model` applies to headless" (derived, so call it a documented-by-composition answer).
- A `/model` typed inside a `-p` run does not save: MC: "If you set a model with `/model` in non-interactive mode, with the `-p` flag, your choice applies to the current session only and isn't saved as your default; `/model` in that mode requires Claude Code v2.1.205 or later. Project and managed settings still take precedence and reapply on the next launch." Same for effort: "When you set a level with `/effort` in a `-p` run, Claude Code applies it to that session only and doesn't save it as your default."
- To make a headless run independent of whatever was last saved with `/model`: put `model` in project `.claude/settings.json` (outranks user settings), pass `--model`, set `ANTHROPIC_MODEL`, or use `--settings`.
- Resumed sessions keep their transcript model (MC): "Resumed sessions started with `claude --resume`, `--continue`, or the `/resume` picker keep the model they were using when the transcript was saved, regardless of the current `model` setting." (relevant only if your launchd job resumes sessions).
- `--bare` caveat: CLI/HL: "Minimal mode: skip auto-discovery of hooks, skills, custom commands, subagents, installed plugins, MCP servers, auto memory, and CLAUDE.md". It also says "In bare mode, Claude Code never reads OAuth credentials or the system keychain" (needs `ANTHROPIC_API_KEY`) and lists `--settings <file-or-json>` and `--agents <json>` as the way to supply settings and custom agents. So `.claude/agents/*.md` are NOT discovered under `--bare`. Whether `--bare` still reads `~/.claude/settings.json` `model`: UNCONFIRMED (not stated; the "To load" table implies settings must be passed with `--settings`).
- Other headless facts: "Claude Code charges by API token consumption" (CO); `total_cost_usd` and "a per-model cost breakdown" in `--output-format json` are "client-side estimates and can differ from your actual bill" (HL). Background subagents in `-p`: "If Claude starts a background subagent or workflow, `claude -p` instead stays open until that work completes ... By default the wait ends after 10 minutes of continuous idle waiting" (`CLAUDE_CODE_PRINT_BG_WAIT_CEILING_MS`). SA: "Fork mode is off in non-interactive mode with `-p` and in the Agent SDK unless you turn it on."
- Auto-update affects headless runs: see section 0.A; `DISABLE_AUTOUPDATER` "Set to `1` to disable automatic background updates. Manual `claude update` still works. Use `DISABLE_UPDATES` to block both"; SU: "Set `DISABLE_AUTOUPDATER` to `"1"` in the `env` key of your `settings.json` file"; `autoUpdatesChannel` "stable" is "typically about one week old"; `minimumVersion` only sets a floor.

---------------------------------------------------------------------------

## Q5. Prompt caching

### 5a. API: multipliers, TTL, minimum length, invalidation, concurrency
Sources: PCa (https://platform.claude.com/docs/en/build-with-claude/prompt-caching), PR (https://platform.claude.com/docs/en/about-claude/pricing), platform optimizing-for-cost-and-intelligence.

Multipliers (PR "Prompt caching"):
> | 5-minute cache write | 1.25x base input price | Cache valid for 5 minutes |
> | 1-hour cache write | 2x base input price | Cache valid for 1 hour |
> | Cache read (hit) | 0.1x base input price (0.025x on Claude Fable 5.1 and Claude Mythos 5.1; 0.05x on Claude Opus 5.5) | Same duration as the preceding write |
> A cache hit costs 10% of the standard input price, which means caching pays off after one cache read for the 5-minute duration (1.25x write), or after two cache reads for the 1-hour duration (2x write).

TTL (PCa "How prompt caching works"): "By default, the cache has a 5-minute lifetime. The cache is refreshed for no additional cost each time the cached content is used. The lifetime is measured from the start of the request that writes or reads the cache entry, not from the end of its response." 1-hour: `"cache_control": {"type": "ephemeral", "ttl": "1h"}`. Guidance (platform optimizing page): "Turns arrive seconds apart: stay on the 5-minute default. When nothing paused, it cost 15% less than the 1-hour setting on Claude Sonnet 5 and about 15% to 18% less on Claude Opus 5.5."

Minimum cacheable prompt length (PCa "Cache limitations"):
> * 512 tokens for Claude Fable 5.1, Claude Mythos 5.1, Claude Opus 5.5, Claude Opus 5, Claude Sonnet 5.5, Claude Fable 5, and Claude Mythos 5
> * 2,048 tokens for Claude Mythos Preview and Claude Opus 4.7
> * 4,096 tokens for Claude Opus 4.6 and Claude Opus 4.5
> * 1,024 tokens for Claude Opus 4.8, Claude Sonnet 5, Claude Sonnet 4.6, Claude Sonnet 4.5, ...
> * 4,096 tokens for Claude Haiku 4.5
"Shorter prompts cannot be cached, even if marked with `cache_control`. ... no error is returned."

What invalidates the cache (PCa "What invalidates the cache"): "the cache follows the hierarchy: `tools` -> `system` -> `messages`. Changes at each level invalidate that level and all subsequent levels." Rows: tool definitions (invalidates the entire cache); web search toggle, citations toggle (system + messages); speed setting (system + messages); tool_choice and images (messages); thinking parameters and effort setting ("Changing the `output_config.effort` value always invalidates message blocks, with the same model-specific effect on tool and system caches as thinking parameters. Setting effort explicitly to the model's default is equivalent to omitting it and does not invalidate."). "Exact matching: Cache hits require 100% identical prompt segments". Isolation: "Caches are isolated between organizations ... Caches are also isolated per workspace within an organization on the Claude API, Claude Platform on AWS, and Microsoft Foundry".

Concurrent requests sharing a prefix (PCa "Cache limitations") - the note you asked about, verbatim:
> For concurrent requests, note that a cache entry only becomes available after the first response begins. If you need cache hits for parallel requests, wait for the first response before sending subsequent requests.
Also (PCa "How prompt caching works"): "Otherwise, it processes the full prompt and caches the prefix once the response begins."
- Reading: N requests with the same prefix that are all sent before any response has begun cannot read each other's entry; each processes its prefix itself. The page does not literally say "each pays the cache write" (that wording: UNCONFIRMED), but Claude Code's workflow doc describes the consequence in other words: agents released together "read the shared prefix instead of each processing it uncached" (see 5b).
- Not stated: whether concurrent requests that each carry their own cache breakpoint all bill a write; UNCONFIRMED.
- API-only feature, not exposed in Claude Code docs: pre-warming with `max_tokens: 0` (PCa "Pre-warming the cache"). Whether Claude Code uses it for subagents: UNCONFIRMED.

### 5b. Claude Code: subagent caching, TTL, parallel subagent cost
Sources: PC (https://code.claude.com/docs/en/prompt-caching), WF (https://code.claude.com/docs/en/workflows), SA, CO.

- PC "Subagents and the cache": "A subagent starts its own conversation with its own system prompt and tool set, separate from the parent's. Its first request doesn't read the parent's cache, because the two prefixes differ, and it warms a cache of its own across its turns. Subagents fall outside the main-conversation TTL bucket, so they get five minutes even on a subscription until you choose a longer one." A fork "inherits the parent's system prompt, tools, and conversation history exactly, so its first request reads the parent's cache." "Resumed subagents: when Claude resumes a subagent, the resumed run's first request can read the cache the original run warmed."
- PC "Which TTL each request gets": two buckets. "Main conversation: your interactive turns, non-interactive `-p` runs, and Agent SDK turns, plus the helpers Claude Code runs inline with them"; "Everything else: ... subagents, workflows, in-process teammates, forks, compaction, and session titles". Table: subscription within plan usage: main = one hour, everything else = five minutes; usage credits / API key / cloud provider: five minutes for both.
- Choosing a TTL (PC "Choose the TTL yourself"): main: `promptCacheTtl` setting or `CLAUDE_CODE_PROMPT_CACHE_TTL`; "Everything else": `subagentPromptCacheTtl` setting or `CLAUDE_CODE_SUBAGENT_PROMPT_CACHE_TTL`; per subagent via frontmatter `experimental: {cacheTtl: 1h}` (SA: "Set its `cacheTtl` key to `5m` or `1h` ... Claude Code ignores any other value, ignores `1h` while your Claude subscription is using usage credits, and reads the field only from subagent files. Requires Claude Code v2.1.248 or later"). Order: "1. `FORCE_PROMPT_CACHING_5M=1` ... 2. The bucket's environment variable 3. The bucket's setting 4. For a subagent's requests, the `cacheTtl` value in the subagent's `experimental` frontmatter field ... 5. `ENABLE_PROMPT_CACHING_1H=1` ... 6. The default for the request's bucket". Both settings need v2.1.242+. 1-hour writes cost 2x input vs 1.25x (5a).
- Cost-model facts for many parallel subagents:
  - SA: "Running many subagents that each return detailed results can consume significant context, and each subagent spends tokens of its own while it runs."
  - SA intro: "It also sends its own requests, which count toward the same usage limits as your main conversation."
  - CO "Why usage climbs": "Subagents and workflows: every subagent, and every agent a dynamic workflow spawns, sends its own requests on top of the main conversation's."
  - CO: the `/usage` "Prompt cache (main)" line "covers the main conversation only, not subagents."
  - PC "Cache scope": "Within those boundaries, any two requests with the same model and prefix read the same cache." and "Sessions you run in parallel in the same directory build matching prefixes and read each other's cache. Sequential sessions share the prefix only when the git status snapshot taken at startup matches, since each conversation also carries the branch and recent commits from that snapshot." Subagent startup context includes a git status snapshot "a snapshot Claude Code reads from your repository when the subagent starts" (SA "What loads at startup"), so a changing working tree may split sibling subagents' prefixes: INFERENCE, not stated.
  - PC: "Model: each model has its own cache." and "Effort level: on most models, each effort level has its own cache". So agents with different model/effort do not share caches (INFERENCE from those lines for subagents; the workflow page states it for workflow agents).
- Workflow fan-out (WF "Prompt caching in a fan-out"), the documented stagger:
  > Agents in the same run can read each other's prompt cache. Two agents that run with the same model, effort level, agent type, tools, output schema, and working directory build the same tools-and-system-prompt prefix, so an agent that starts after a matching sibling's response has begun reads that sibling's cache on its first request.
  > A workflow agent's requests fall outside the main conversation's cache TTL bucket, so its cache holds for five minutes by default, including on a Claude subscription. To keep it for an hour, set `subagentPromptCacheTtl` to `1h`. The API bills 1-hour cache writes at a higher rate.
  > When a fan-out starts several matching agents at once, Claude Code holds all but the first until the first agent's response begins, then releases the held agents together so their first requests read the shared prefix instead of each processing it uncached. Claude Code caps the hold at `CLAUDE_CODE_WORKFLOW_PREFIX_STAGGER_MS` milliseconds, `5000` by default. Set it to `0` to disable the hold.
  EV: "When `DISABLE_PROMPT_CACHING` is set, agents never wait. Requires Claude Code v2.1.229 or later".
- Not documented: any equivalent stagger for plain Agent-tool parallel subagents (UNCONFIRMED; the docs describe it only for workflow agents). Also workflows limits (WF): "Up to 16 concurrent agents by default"; "1,000 agents total per run"; "Large workflow" warning when "a workflow schedules more than 25 agents, or its projected token total passes 1.5 million".
- Plain subagent concurrency (SA "Concurrent subagent limit"): "By default, when 20 subagents are running in a session, spawning another with the Agent tool fails with `Concurrent subagent limit reached`"; `CLAUDE_CODE_MAX_CONCURRENT_SUBAGENTS` changes it (v2.1.217+).
- Cache-miss triggers specific to Claude Code (PC "Actions that invalidate the cache"): switching models (including the `opusplan` plan-mode toggle), changing effort on most models, fast mode, connecting/removing an MCP server, enabling/disabling a plugin, denying a whole tool, compaction, many images, upgrading Claude Code. Actions that keep the cache include editing CLAUDE.md mid-session, changing permission mode, invoking skills, editing repo files. Disable switches: `DISABLE_PROMPT_CACHING`, `DISABLE_PROMPT_CACHING_OPUS`/`_SONNET`/`_HAIKU`/`_FABLE`.
- Verify TTL: "run `claude -p "hello" --output-format json` and read `usage.cache_creation` in the result. Claude Code reports one-hour cache writes under `ephemeral_1h_input_tokens` and five-minute cache writes under `ephemeral_5m_input_tokens`."

---------------------------------------------------------------------------

## Q6. Capping a subagent's turns/tokens; restricting its tools

Turns:
- `maxTurns` (SA table): "Maximum number of agentic turns before the subagent stops. When the subagent reaches the limit, Claude Code returns its output marked as partial, and Claude can resume it to continue. The partial marking requires Claude Code v2.1.246 or later". Tools reference: "To cap how many turns a subagent runs, set `maxTurns` in the subagent definition." Agent SDK agent-loop: `max_turns` "counts tool-use turns only".
- CLI `--max-turns`: "Limit the number of agentic turns (print mode only). Exits with an error when the limit is reached. No limit by default." Whether it applies inside subagents: UNCONFIRMED.

Tokens / spend:
- No per-subagent token cap field exists in the frontmatter table (SA lists none). UNCONFIRMED that none exists elsewhere, but the documented controls are the ones below.
- `--max-budget-usd` (CLI): "Maximum dollar amount to spend on API calls before stopping (print mode only). Spend from subagents counts toward the cap. ... Once spend reaches the cap, spawning another subagent fails with `Budget limit reached`, and Claude Code stops background subagents that are still running; the cap-enforcement behaviors require Claude Code v2.1.217 or later". The dollar figures it counts are Claude Code's own client-side estimates (HL: "Both figures are client-side estimates and can differ from your actual bill"; CO: the 1.1x data-residency figure "also counts toward `--max-budget-usd`").
- `CLAUDE_CODE_MAX_SUBAGENT_SPAWN_DEPTH` (EV): "Number of subagent layers allowed below the main conversation (default: 3). ... set `1` to turn nesting off." SA: "To keep one subagent from spawning while nesting is on ... omit `Agent` from its `tools` list or add it to `disallowedTools`."
- `CLAUDE_CODE_MAX_CONCURRENT_SUBAGENTS` (default 20), `CLAUDE_CODE_MAX_OUTPUT_TOKENS` ("Set the maximum number of output tokens for most requests. Defaults and caps vary by model"), `maxEffortLevel` (Q3).
- API-level token controls (not exposed as Claude Code settings in the docs I read; UNCONFIRMED for Claude Code): `max_tokens` is "a hard cap on total output for the request, thinking and response text combined" and "In a tool-use loop, each request in the turn has its own `max_tokens`, so it doesn't bound the whole turn's spend." (platform thinking-steering-and-cost "Cost control"); task budgets (beta) at platform.claude.com/docs/en/build-with-claude/task-budgets.
- Agent SDK subagent doc (https://code.claude.com/docs/en/agent-sdk/subagents): depth, concurrency and spend limits table; "Claude Opus 5 delegates to subagents more readily than earlier models, so the depth, concurrency, and spend limits matter most on queries that run Opus 5." Opus 5 prompting guide: "Delegation ... multiplies cost and time when applied to small tasks. ... give explicit guidance on which scenarios warrant delegation, or set deterministic caps on how many agents can be launched." Whether Opus 5.5 delegates more or less than Opus 5: UNCONFIRMED (no 5.5 statement found); SDK doc says that with the `claude_code` preset "when the model is Opus 5, Claude Code adds a line to its system prompt telling Claude not to call the Agent tool unless it's asked to"; whether that line applies to 5.5 or to the CLI: UNCONFIRMED.
- Auto-compaction applies to subagents (SA): "Subagents support automatic compaction using the same logic as the main conversation."

Restricting tools and behavior:
- `tools` (allowlist) / `disallowedTools` (denylist): SA: "Inherits every tool available to subagents if omitted." "If both are set, `disallowedTools` is applied first, then `tools` is resolved against the remaining pool. A tool listed in both is removed." A `disallowedTools` entry with a specifier (e.g. `Bash(git push *)`) "still removes the whole tool"; for command-level denial use `permissions.deny` (applies to main and subagents). MCP patterns: "`mcp__<server>` or `mcp__<server>__*` grants or removes every tool from the named server"; `mcp__*` in `disallowedTools` removes every MCP tool.
- Tools never available to subagents (SA "Available tools"): `AskUserQuestion`, `EndConversation`, `EnterPlanMode`, `ExitPlanMode` (unless `permissionMode: plan`), `ScheduleWakeup`, `WaitForMcpServers`, `Workflow`, and `Agent` at the depth limit. Background subagents (the default) get a reduced built-in set: "Read, Grep, Glob, LSP, Bash, PowerShell, Edit, Write, NotebookEdit, WebFetch, WebSearch, TodoWrite, Skill, ToolSearch, EnterWorktree, ExitWorktree, Monitor, TaskStop, SendMessage, and Artifact" plus every MCP tool.
- `permissionMode` (SA): values `default`, `acceptEdits`, `auto`, `dontAsk`, `bypassPermissions`, `plan`, `manual`; "When the main conversation is in `bypassPermissions`, `acceptEdits`, or auto mode, the subagent runs in that same mode and Claude Code ignores the `permissionMode` you set." Under `default`/`dontAsk`/`plan` main modes the subagent's value applies except `bypassPermissions`.
- Hooks: SA: frontmatter `hooks` run only while that subagent is active; "Hooks from settings files, managed policy settings, and plugins all apply inside subagents, so a `PreToolUse` hook in `settings.json` also runs before every tool a subagent uses." Exit code 2 blocks. `SubagentStart` "can't block subagent creation, but they can inject context into the subagent" (`additionalContext`); `SubagentStop` with `decision: "block"` "keeps the subagent running". Frontmatter hooks need workspace trust and "a `-p` session doesn't count as trusted" (SA); hooks in settings files do run in `-p` (HL).
- Spawn control: `permissions.deny: ["Agent(my-custom-agent)"]` (SA "Disable specific subagents"); for an agent running as the main thread with `--agent`, `tools: Agent(worker, researcher)` is an allowlist of spawnable types (ignored inside a subagent definition).
- Scoping MCP to one subagent: `mcpServers` inline definitions: "The subagent gets the tools; the parent conversation doesn't."
- CLI `--tools` / `--disallowedTools` apply to the session; `--allowedTools` only pre-approves.

---------------------------------------------------------------------------

## Q7. What every subagent receives automatically; keeping context minimal

SA "Manage subagent context" -> "What loads at startup" (https://code.claude.com/docs/en/sub-agents):
> Each subagent starts with a fresh, isolated context window. It doesn't see your conversation history, the skills you've already invoked, or the files Claude has already read. ...
> A non-fork subagent's initial context contains:
> * **System prompt**: the agent's own prompt plus environment details that Claude Code appends, not the Claude Code system prompt. ...
> * **Task message**: the delegation prompt Claude writes when it hands off the work.
> * **CLAUDE.md files**: every level of the CLAUDE.md hierarchy the main conversation loads, including `~/.claude/CLAUDE.md`, project rules, `CLAUDE.local.md`, managed policy files, and any `AGENTS.md` files loaded as project instructions. The built-in Explore and Plan agents skip this. A subagent whose definition sets `omitClaudeMd` loads only the managed policy files, or none at all when the definition comes from managed settings.
> * **Git status**: a snapshot Claude Code reads from your repository when the subagent starts. Absent outside a Git repository or whenever the snapshot is turned off; see `includeGitInstructions`. Explore and Plan skip it regardless.
> * **Preloaded skills**: full content of any skill named in the agent's `skills` field. Built-in agents don't preload skills.
> * **Sibling roster**: a system reminder listing `main` and every other named agent in the session ... The roster appears only when the subagent's tools include `SendMessage` and at least one other agent has a name ...
Not carried over (SA): "Output style: a subagent runs its own system prompt, so your output style doesn't shape its responses"; "Auto memory: the main conversation's auto memory isn't loaded. To give a subagent persistent memory of its own, use the `memory` field."; "Context window size: a subagent's context window is sized by its own model, not the parent's."
- Tools / MCP / skills: SA "Available tools": "Subagents inherit the built-in tools and MCP tools available in the main conversation, narrowed by two filters". Context-window page (https://code.claude.com/docs/en/context-window), subagent walkthrough: "It loads CLAUDE.md and the same MCP and skill setup, but starts without your conversation history or the main session's auto memory." and "The subagent has access to the same MCP servers and skills. It gets most of the parent's tools, minus several that don't apply in a nested context". SA "Preload skills": "without it, the subagent can still discover and invoke project, user, and plugin skills through the Skill tool during execution. To prevent a subagent from invoking skills entirely, omit `Skill` from the `tools` list or add it to `disallowedTools`." Whether the skill description listing is injected into a subagent's context: UNCONFIRMED (not in the startup list; the context-window walkthrough says "same ... skill setup").
- MCP definitions are deferred by default (CO): "MCP tool definitions are deferred by default, so only tool names and server instructions enter context until Claude uses a specific tool."
- Subagent description cost sits in the MAIN context: "When the combined descriptions of your subagents, except the built-in ones, exceed 15,000 tokens, Claude Code shows a warning at startup with the total token count."

Documented ways to keep subagent context minimal:
- `omitClaudeMd: true` in frontmatter or `--agents` JSON (SA table): "Set to `true` to launch this subagent without the user, project, and local CLAUDE.md files; managed policy files still load, except for managed subagents. Use it for subagents that take everything they need from the delegation prompt. ... Requires Claude Code v2.1.271 or later." SA: "The main conversation still has your full CLAUDE.md when it reads these subagents' results, so most rules don't need to reach the subagent itself. If a rule must ... restate it in the prompt you give Claude when delegating."
- Narrow `tools` / `disallowedTools`; do not list `skills`; do not set `memory`; scope `mcpServers` to only the agents that need them.
- Git status: "You can't change which subagents receive git status. Only Explore and Plan skip it." The global switch is `includeGitInstructions: false` (SR: "Set this key to `false` to leave both out") or `CLAUDE_CODE_DISABLE_GIT_INSTRUCTIONS=1`; it removes the snapshot and the built-in commit/PR instructions from Claude's context everywhere.
- Explore and Plan skip CLAUDE.md and git status "to keep research fast and inexpensive" (SA).
- Headless: `--append-subagent-system-prompt` / `-file` add to every subagent's prompt (`-p` only; v2.1.205 / v2.1.261). `--bare` skips CLAUDE.md, auto memory, hooks, skills, and also subagent discovery; use `--agents` to supply agents.
- CO "Move instructions from CLAUDE.md to skills": "Aim to keep CLAUDE.md under 200 lines by including only essentials." (applies to every subagent too, since each loads it).

---------------------------------------------------------------------------

## Q8. Current list prices (USD per million tokens)

Source: https://platform.claude.com/docs/en/about-claude/pricing "Model pricing" (and the identical table on the prompt-caching page). The pricing page names models by display name, not by API ID. IDs below are from https://platform.claude.com/docs/en/models/overview (and model-ids-and-versions for Opus 5 / Sonnet 5).

| Model (page name) | API ID | Input | Output | 5m cache write | 1h cache write | Cache read (hit/refresh) |
| :-- | :-- | --: | --: | --: | --: | --: |
| Claude Opus 5.5 | `claude-opus-5-5` | $4 | $20 | $5 | $8 | $0.20 (0.05x, footnote 2) |
| Claude Opus 5 | `claude-opus-5` | $5 | $25 | $6.25 | $10 | $0.50 |
| Claude Sonnet 5.5 | `claude-sonnet-5-5` | $2 | $10 | $2.50 | $4 | $0.20 |
| Claude Sonnet 5 | `claude-sonnet-5` | $2 (footnote 3) | $10 (footnote 3) | $2.50 | $4 | $0.20 |
| Claude Haiku 4.5 | `claude-haiku-4-5-20251001` (alias `claude-haiku-4-5`) | $1 | $5 | $1.25 | $2 | $0.10 |
| Claude Fable 5.1 | `claude-fable-5-1` | $10 | $50 | $12.50 | $20 | $0.25 (0.025x, footnote 1) |
| Claude Fable 5 (legacy) | `claude-fable-5` | $10 | $50 | $12.50 | $20 | $1 |
(Claude Mythos 5.1 / 5 "limited availability" carry the Fable prices.)

Verbatim footnotes: "1 Cache hits and refreshes on Claude Fable 5.1 and Claude Mythos 5.1 are priced at 0.025x the base input price." "2 Cache hits and refreshes on Claude Opus 5.5 are priced at 0.05x the base input price." "All other models use the standard 0.1x multiplier." "3 The $2/$10 per million input/output token pricing for Claude Sonnet 5, announced at launch as introductory pricing through August 31, 2026, is now the standard price. The previously scheduled increase to $3/$15 per million input/output tokens on September 1, 2026 will not occur."
Other notes on the page: "Claude 4.7 and later models and Claude Mythos Preview use a newer tokenizer ... approximately 30% more tokens for the same text" (Sonnet 5.5 what's-new: "The tokenizer is the same as Claude Sonnet 5's, so the same text produces the same token counts."); batch is 50% off ("Claude Opus 5.5 | $2 / MTok | $10 / MTok", "Claude Sonnet 5.5 | $1 / MTok | $5 / MTok"); fast mode (research preview, API only) Opus 5.5 $8 / $40 and Opus 5 $10 / $50; US-only inference via `inference_geo` is 1.1x on all token categories. Claude Code's own `/usage` dollar figure is computed locally at list price (CO: "Claude Code computes the dollar figure locally from token counts at list price").
Relative change Opus 5 -> Opus 5.5 (arithmetic on the table): input -20%, output -20%, 5m and 1h writes -20%, cache read -60%. Sonnet 5 -> 5.5: unchanged.
Default effort per model for context (MO): Fable 5.1 `high`, Opus 5.5 `medium`, Sonnet 5.5 `high` (Claude API default), Haiku 4.5 not supported.

---------------------------------------------------------------------------

## Q9. Cheaper orchestrating main session with stronger subagents

What the docs actually say. No page recommends "cheap orchestrator, strong subagents" as a pattern. UNCONFIRMED as a documented recommendation. What is documented:

1. Per-subagent model is the documented routing mechanism, in both directions:
   - SA intro: "Control costs by routing tasks to faster, cheaper models like Haiku".
   - SA: "A user or project subagent named `Explore` overrides the built-in and keeps its own `model` field, so define one with `model: haiku` to run exploration on a lower-cost model."
   - CO "Choose the right model": "Sonnet handles most coding tasks well and costs less than Opus. Reserve Opus for complex architectural decisions or multi-step reasoning. Use `/model` to switch models mid-session, or set a default in `/config`. A switch to Opus also applies to the subagents that inherit your session's model. For simple subagent tasks, specify `model: haiku` in your subagent configuration."
   - CO "Delegate verbose operations to subagents": "The subagent's own requests still draw on your usage. To spend less on them, choose a smaller model for a subagent or run every subagent on one model."
   - AD "Compare with related features" table: "Subagents with `model` set | For the entire delegated subtask | Claude delegates, or you invoke the subagent".
   - Agent SDK subagents page example comment: "Key insight: use a more capable model for high-stakes reviews" (`model="opus" if is_strict else "sonnet"`).
2. `--agent <name>` / `agent` setting runs the main thread as a named agent: SA: "Pass `--agent <name>` to start a session where the main thread itself takes on that subagent's tool restrictions and model". `tools: Agent(worker, researcher), Read, Bash` restricts which subagent types that main-thread agent may spawn. Together this is the documented way to give the orchestrating session a different model (e.g., `model: sonnet`) than the subagents it spawns, in headless too (`claude -p --agent <name>`). Precedence of that agent's `model` versus `--model` / `ANTHROPIC_MODEL` / settings `model`: UNCONFIRMED (not stated on the pages read). Remember the family rule (Q1): a `model: opus` subagent under a main session that is also an Opus runs on the main's exact model.
3. `opusplan` is the opposite direction and is about plan vs execution phases of one session, not subagents: MC: "In plan mode: uses `opus` for complex reasoning and architecture decisions. In execution mode: automatically switches to `sonnet` for code generation and implementation. This pairs Opus's reasoning for planning with Sonnet's efficiency for execution." PC: "The `opusplan` model setting resolves to Opus during plan mode and Sonnet during execution, so each plan-mode toggle is a model switch and starts a fresh cache." (`ANTHROPIC_DEFAULT_OPUS_MODEL` / `_SONNET_MODEL` control its two models.)
4. Advisor tool (cheaper main model + stronger consulted model) is the documented "cheap main, strong second model" mechanism: AD: "The advisor tool lets Claude consult a second, typically stronger model at key moments during a task ... The advisor receives the full conversation". "Claude calls the advisor at decision points rather than on every turn, so pairing a faster main model with a stronger advisor typically costs less than running the stronger model throughout." Pairings table: "Sonnet main + Opus advisor | Sonnet handles routine work and escalates planning, ambiguous failures, and completion checks to Opus". Limits: "The advisor tool is experimental and requires the Anthropic API"; "Subagents inherit the configured advisor and apply the same pairing check against their own model." "The advisor model's own read of the conversation is not cached. Each advisor call processes the full transcript anew". Settings/flags: `advisorModel`, `/advisor`, `--advisor` (works with `-p` from v2.1.260). Sonnet 5.5 main accepts advisors "Fable, Opus 5 or later, Sonnet 5.5".
5. API-level guidance (platform.claude.com/docs/en/about-claude/models/optimizing-for-cost-and-intelligence, "Combine models"): two strategies, "Advisor | Smaller model runs the loop, escalates on demand" and "Orchestrator | Frontier model runs the loop, delegates the bulk work". The documented orchestrator pattern has the frontier model as orchestrator and lower-cost workers: "the frontier model holds the loop. It decomposes the task, dispatches subtasks to lower-cost worker models, and merges their results." and "When the work is one dependent chain, or fits in a single context, the orchestrator pays for a plan, a handoff, and a merge that a single model gets for free. In every such case measured, the coordinator's model alone at lower effort came out ahead." Also: "draw this curve for your own workload before you add a second model: in these internal measurements, a multi-model configuration that looked cheaper than the default single model cost more than that same model at lower effort." and "If you are unsure, don't build anything yet: 1. Sweep effort on your current model first. It is the cheapest experiment on this page".
6. Workflows (WF "Cost"): "Claude Code picks each workflow agent's model in the same order it uses for subagents. A model the script names for a stage counts as the per-invocation model in that order. When nothing else assigns one, the agent runs on your session's model." "To control the model cost: Check `/model` before a large run if you usually switch to a smaller model for routine work; Ask Claude to use a smaller model for stages that don't need the strongest one".
7. Agent teams guidance (CO): "Use Sonnet for teammates. It balances capability and cost for coordination tasks."

Cache consequence of mixing models (PC): "Each model has its own cache." A subagent on a different model than the main session never shares the parent's cache (and a subagent never reads the parent's cache anyway, per "Subagents and the cache").

---------------------------------------------------------------------------

## Appendix A. Consolidated UNCONFIRMED list
1. Which Claude Code version each of your two runs used (not in docs; check `claude --version` / `claude doctor` / transcripts).
2. Whether the `/model` picker stores `opus` or a full ID for Opus/Sonnet.
3. Whether Claude Code applies frontmatter `effort` on a Haiku 4.5 subagent; what level an effort-less subagent on a different model than the main session uses.
4. Whether `--bare` still reads `~/.claude/settings.json`; exact `--setting-sources` semantics on the CLI.
5. Whether `--max-turns` also bounds subagent turns.
6. Whether a PreToolUse `updatedInput` can rewrite the Agent tool's `model`.
7. Any stagger/prefix-sharing hold for plain Agent-tool parallel subagents (documented only for workflow fan-outs); literal statement that each concurrent request "pays the write" (API doc says only that the entry is unavailable until the first response begins).
8. Whether a changing git-status snapshot splits sibling subagents' cache prefixes (inference only).
9. Opus 5.5 vs Opus 5 delegation rate and whether the Opus-5-specific "don't call Agent unless asked" system-prompt line (SDK doc, `claude_code` preset) applies to 5.5 or to the CLI.
10. Precedence of a `--agent` main-thread agent's `model` versus `--model` / `ANTHROPIC_MODEL` / settings `model`.
11. A per-subagent token budget field (none found in the frontmatter table).
12. Whether Claude Code uses API `max_tokens: 0` pre-warming or task budgets (API features documented on the platform pages, not in Claude Code docs).
13. The trigger conditions for CL v2.1.286's "retries once on the previous model of the same tier" alias fallback (changelog line only).
14. Whether your plan's "Default" model moved (CL v2.1.280 changed Pro/Team Standard defaults from Sonnet to Opus).

## Appendix B. Version gates mentioned (Claude Code)
v2.1.197 sonnet->Sonnet 5; v2.1.198 subagents inherit thinking config; v2.1.205 `--append-subagent-system-prompt`, `/model` and `/effort` in `-p`; v2.1.212 `resolvedModel`/`modelsUsed` in hooks; v2.1.217 concurrent-subagent limit, spawn-depth var, budget enforcement on subagents; v2.1.219 opus->Opus 5; v2.1.229 workflow prefix stagger; v2.1.236 `ANTHROPIC_DEFAULT_MODEL`; v2.1.242 `subagentPromptCacheTtl`, `/tasks` model+effort; v2.1.246 `maxTurns` partial marking; v2.1.248 frontmatter `experimental.cacheTtl`; v2.1.251 subagent model order change (env var no longer first), per-model effort saved; v2.1.257 `CLAUDE_CODE_SUBAGENT_MODEL_FORCE`; v2.1.267 `maxEffortLevel`; v2.1.271 `omitClaudeMd`; v2.1.274 OTEL `effort` attribute; v2.1.280 opus->Opus 5.5 (Sep 22, 2026); v2.1.281 `--agents` file form; v2.1.284 sonnet->Sonnet 5.5 (Sep 28, 2026); v2.1.287 latest in the changelog (Oct 1, 2026).

## Appendix C. Documentation pages fetched (raw .md)
Claude Code: sub-agents, model-config, settings, settings-reference, cli-reference, headless, costs, hooks, env-vars, prompt-caching, workflows, advisor, setup, context-window, tools-reference, skills, agent-sdk/subagents, agent-sdk/cost-tracking, agent-sdk/claude-code-features, agent-sdk/agent-loop, monitoring-usage, changelog (all https://code.claude.com/docs/en/<page>). Platform: build-with-claude/prompt-caching, about-claude/pricing, about-claude/models/overview, about-claude/models/model-ids-and-versions, about-claude/model-deprecations, build-with-claude/effort, build-with-claude/thinking-steering-and-cost, about-claude/models/optimizing-for-cost-and-intelligence, about-claude/models/choosing-a-model, models/opus-5-5/overview, models/opus-5-5/whats-new-opus-5-5, models/opus-5-5/migration-guide, models/sonnet-5-5/whats-new-sonnet-5-5, models/sonnet-5-5/migration-guide, build-with-claude/prompt-engineering/prompting-claude-opus-5-5, prompting-claude-sonnet-5-5, prompting-claude-opus-5 (all https://platform.claude.com/docs/en/<page>). Local copies of the raw pages: /private/tmp/claude-503/-Users-qingbin-zhuang-Personal-TradingAgents/1ca36413-579e-4359-8c8a-818a893292d2/scratchpad/docs/
