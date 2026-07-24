# Guild.ai — Sponsor SDK Recon

**VERDICT: `GO-AFTER-HUMAN-AUTH` (scoped down)** — the CLI is real, actively maintained, and traces are cleanly retrievable, but **there is no Python SDK** (TypeScript-only) and agents **only execute on Guild's server**, so the "wrap our existing Python logic + use versions as generation numbers" plan does not survive contact and must be reduced to a narrower, still-honest integration.

---

## HUMAN TODO (do these yourself — I cannot)

Total: **~3–5 min**, plus ~2 min if you want a workspace.

1. `npm i -g @guildai/cli` — **already done by me.** Binary at `/opt/homebrew/bin/guild`, v0.17.0. Skip.
2. `guild auth login` — **~60–90s.** Opens a browser for OAuth. Complete the sign-in, pick/create the account that will own the agents.
3. `guild auth status` — **~2s.** Must print an authenticated identity, not `✗ Not authenticated`.
4. `guild agent owners` — **~5s.** Tells us which owner slug to pass to `--owner` (your personal account vs. an org). **Paste this output to me** — I'm blocked on it for `agent init`.
5. `guild workspace list` — **~5s.** We need a workspace with credentials for agent testing. If empty: `guild workspace create <name>`. **Paste this output to me too.**

After step 5 I can do everything else unattended (init, build, save, publish, trace pull).

> Note: `guild setup` was already run — it wrote `.claude/skills/{agent-dev,guild-cli-workflow,integrations}/skill.md` and **created `.mcp.json`** in the project root registering a `guild` MCP server. That file is a side effect of my recon; delete it if you don't want it committed.

---

## 1. Package verification

| Field | Value |
| --- | --- |
| Name | `@guildai/cli` |
| Latest | **0.17.0**, published **2026-07-21** |
| First publish | 0.3.10 on 2026-03-06 |
| Release count | 46 versions in ~4.5 months — **very actively developed** |
| Description | "Guild.ai CLI - Build, test, and deploy AI agents" |
| Homepage | https://docs.guild.ai |
| Install | Clean, 470 packages, 28s, exit 0 |

Notable deps: `commander`, `@modelcontextprotocol/sdk`, `ink`/`react` (TUI), `@napi-rs/keyring` (OS keychain for the auth token), `esbuild` (local agent bundling), `ws`, `zod`, `axios`.

The high release cadence cuts both ways: it's alive and sponsor-supported, but the surface may shift under us mid-hackathon. Pin `0.17.0`.

## 2. Command surface

`guild [--debug] [--mode interactive|json|jsonl] [--quiet] [--non-interactive] <command>`

The three global flags that matter to us: **`--mode json` / `--mode jsonl`** (machine-readable output on every command — this is what makes dashboard integration viable) and **`--non-interactive`** (explicitly documented "for CI, scripts, and coding agents").

Top-level: `version, chat, mcp, api, doctor, setup, auth, agent, integration, skill, workspace, trigger, credentials, llm, session, job, config, container-image`

```
auth     login [--return-url --return-label] | logout | status | token
agent    list get update versions capabilities save pull code chat init grep
         clone fork test publish unpublish revalidate logs search owners
         workspaces categories tags
session  list get events tasks create send interrupt
job      get | get-step
workspace list create get select current clear export import chat agent member context
trigger  list get create update activate deactivate sessions
skill    archive create get list search unarchive update version
llm      policy          credentials  list | policy | endpoint
config   list get set path
```

Key detailed signatures:

```
guild agent save   -A, --all | -m, --message <text> | --wait
                   --publish (implies --wait) | --bump [patch|minor|major] | --no-bump
guild agent init   --name --template <LLM|AUTO_MANAGED_STATE|BLANK>
                   --agent-type <GUILD_TYPESCRIPT|GUILD_NATIVE|GOOSE>
                   --category --tags --fork --owner --directory --force
guild agent versions [id] --limit <n> --offset <n>
guild agent publish  [id] --wait --timeout <seconds>   (default 300)
guild session events <session-id> --events <types> --limit --offset
guild api <GET|POST|PATCH|PUT|DELETE> <path> [--data <json>]
```

**`guild auth status` (current, verified):**
```
✗ Not authenticated
```

**`guild agent init --agent-type` accepts `GUILD_TYPESCRIPT`, `GUILD_NATIVE`, `GOOSE`. There is no Python option.** This is the first hard contradiction of the sponsor's pitch.

## 3. `guild setup` — works WITHOUT auth ✅

```
$ guild setup --non-interactive
✓ Created .claude/skills/agent-dev/skill.md          (1638 lines)
✓ Created .claude/skills/guild-cli-workflow/skill.md  (214 lines)
✓ Created .claude/skills/integrations/skill.md        (340 lines)
✓ Created .mcp.json with Guild MCP server
```

This is the only meaningful command that works unauthenticated (besides `--help` and `doctor`). The docs it writes are the real SDK reference and are the basis for everything below.

### Actual SDK API surface (from the generated docs)

Core package: **`@guildai/agents-sdk`** (TypeScript). Integrations are separate `@guildai-services/<owner>~<name>` packages.

An agent is **an npm TypeScript project**:
```
my-agent/
├── agent.ts        # your agent code
├── package.json    # deps + the version that gets bumped
├── tsconfig.json
└── guild.json      # agent ID — generated, gitignored, never hand-edit
```

Three agent patterns:
1. **`llmAgent()`** — prompt + tools, LLM is the logic. Simplest.
2. **`agent()` with auto-managed state** — coded agent.
3. **`agent()` with explicit state** (`task.save()` / `task.restore()`) — state machine; docs warn it's "difficult even for an expert programmer to maintain."

`Task` object: `task.sessionId`, `task.tools.*`, `task.llm.generateText({messages, system, tools})`, `task.console.{debug,info,warn,error}`, `task.save()`, `task.restore()`. Deprecated: `task.guild`, `task.ui`, `task.env`.

Version lifecycle: **Draft** (`save`) → **Validating** (`--publish`) → **Published** | **Failed**.

Hard rules from the docs: never `git push` (a pre-push hook blocks it — use `guild agent save`), never `git pull` (use `guild agent pull`), never hand-write `package.json`/`tsconfig.json`/`guild.json`.

## 4. Python SDK — DOES NOT EXIST ❌

This is the headline finding.

- `pip index versions guild-ai`, `guild-sdk`, `guildai-sdk`, `pyguild` → **all "No matching distribution found."**
- `guildai` **does** exist on PyPI and is a **red herring**:
  - version **0.9.0**, uploaded **2023-02-25** (3.5 years stale)
  - summary: *"Experiment tracking, ML developer tools"*
  - keywords: `guild guildai tensorflow keras pytorch mxnet xgboost scikit-learn`
  - homepage `https://guild.ai` — the domain was reused; this is the **legacy Guild AI ML experiment tracker**, an entirely unrelated product from the agent control plane.
- `@guildai/agents-sdk` is **not on public npm** either (404 on registry.npmjs.org). It lives on Guild's authenticated registry — the docs confirm: *"You must be logged in to guild to `npm install` dependencies."*

**Consequence: our Attribution / Evolution / Validator agents cannot be hosted on Guild as Python. They would need rewriting in TypeScript.** The sponsor's "Python + TypeScript SDK" claim is, as of 0.17.0, TypeScript-only.

## 5. Specific answers

### 5a. Local execution without cloud auth? — **NO. Auth is required even to build.**

Two independent gates:
1. **Build gate.** `@guildai/agents-sdk` is on Guild's private registry. `npm install` fails without `guild auth login`. You cannot even compile an agent offline.
2. **Execution gate.** There is no local runtime at all. The docs are explicit: *"An agent must be tested using the `guild` tool: this will upload the agent to the server runtime environment where the agent will operate."* `guild agent test --ephemeral` is still server-side — "ephemeral" means no persistent storage, not local.

`npm run bundle` + `guild agent test --bundle agent.js.gz` only moves the *bundling* step local to skip a server build; execution remains remote. Everything except `guild setup`, `--help`, and `doctor` requires auth.

### 5b. What does `guild agent save --message "v1" --publish` produce?

- **A semver version string derived from `package.json`**, auto-bumped by `--bump` (default `patch`; accepts `minor`/`major`; `--no-bump` to suppress).
- It commits + pushes to Guild's git server, creates a **version record**, runs **server-side validation** (TypeScript compile, deps, schema), and on pass marks it **Published**.
- `--publish` implies `--wait`; `guild agent publish --timeout <seconds>` defaults to **300s**.
- Versions are listed via `guild agent versions --limit --offset` (paginated, so ordered).
- I could not observe the literal JSON payload (auth-gated), so **whether it returns a URL and a distinct version UUID is unconfirmed** — but `guild agent logs [identifier] [version-id]` and `guild agent revalidate [identifier] [version-id]` both take a **`version-id` positional distinct from the semver**, which strongly implies each version has both a semver *and* an opaque ID.

**Is it monotonic? Yes, but unusably slow for generation numbering.** It's semver auto-incremented from `package.json`, so it is monotonic if you always bump. **But every increment costs a git push + server-side TypeScript build + validation round-trip with a 300s timeout.** An evolutionary loop doing dozens of generations cannot use `agent save --publish` as its generation counter — you'd spend the entire hackathon in build queues. Guild versioning is release-granularity, not iteration-granularity.

### 5c. Traces — **retrievable programmatically ✅ (best part of the platform)**

`guild session events <session-id> --events <types> --limit <n> --offset <n>`, combined with the global `--mode json|jsonl`, gives us a paginated, typed event stream we can pipe straight into our own dashboard. Typed event vocabulary:

- **User events:** `user_message`, `agent_notification_message`, `agent_notification_progress`, `agent_notification_error`, `credentials_request`, `agent_install_request`, `trigger_message`, `system_error`
- **System/debug events:** `agent_console`, `runtime_start`, `runtime_running`, `runtime_waiting`, `runtime_error`, `runtime_done`, **`llm_start`, `llm_done`**

`--events all` gets everything. `llm_start`/`llm_done` are exactly the spans a "full execution trace" demo needs.

Supporting surface: `guild session list --workspace --type chat|webhook|time|agent_test`, `guild session get`, `guild session tasks`, `guild job get` / `guild job get-step` for step-level detail, and **`guild api <METHOD> <path>`** as an authenticated escape hatch to any REST endpoint. There's also a **`guild mcp`** stdio MCP server (already wired into `.mcp.json`) that the docs recommend for read operations.

So: **not web-UI-only.** This is genuinely renderable in our dashboard. Caveat: it's CLI/REST pull-based, not a push/streaming subscription, so the dashboard polls.

### 5d. LLM credentials — **Guild supplies them; it does NOT wrap our existing calls.**

The docs on `task.llm.generateText`: *"call the LLM with automatic authentication and provider selection. **The runtime handles model selection and credential injection.**"* There is a whole `guild llm policy` command tree for governing model choice, plus `guild credentials list|policy|endpoint` for integration creds.

This is the opposite of the pitch. Guild does not govern *our* LLM calls — you rewrite your logic against Guild's runtime and Guild's inference. Our existing Anthropic calls don't get wrapped; they get replaced. Practically this is a *plus* (no key to provision) but it means "wraps your existing LLM logic" is false as stated.

## 6. Docs site

- `https://docs.guild.ai` — live, and is the package `homepage`.
- `https://docs.guild.ai/guide/sdk-introduction` — **live**, referenced from the generated skill docs. Confirms TypeScript-only (`@guildai/agents-sdk`), agents defined via `inputSchema`/`outputSchema`/`tools` with `inputSchema` required to be `z.object({...})` at root, `llmAgent` vs `agent`, and that Goose agents need "No TypeScript required." Mentions an "Ops Inspector" and a "platform API." **Does not document local execution or programmatic trace retrieval** — the CLI `--help` is a better source than the docs site.
- `https://docs.guild.ai/guide/getting-started` — **404.** No getting-started guide at the obvious path.

The generated `.claude/skills/agent-dev/skill.md` (1638 lines) is by a wide margin the best documentation available, and it self-declares the CLI's own `--help` as authoritative over itself where they disagree.

---

## Honest assessment

**If a human authenticates in 3 minutes, what do we actually get?**

Of the two things we wanted — versioning-as-generation-numbering and traces — **we get about one and a half, and the half is the one we cared more about.**

- ❌ **Versioning as generation numbering: not viable.** Not because versions aren't monotonic (they are — semver, auto-bumped), but because each increment is a git push + remote TypeScript build + validation with a 300s timeout. That's release cadence, not evolution-loop cadence. Wiring our generation counter to `guild agent save --publish` would make the swarm run at roughly one generation per minute at best, and every generation would be a chance for a build failure to kill the demo. This is the single biggest gap between the pitch and reality.
- ✅ **Traces: genuinely good and genuinely ours.** `guild session events --mode jsonl` with `llm_start`/`llm_done`/`runtime_*`/`agent_console` is a real, typed, paginated event stream we can render ourselves. This is the load-bearing part.
- ❌ **Hosting all three agents: costs a rewrite.** No Python. Attribution/Evolution/Validator are presumably Python; putting all three on Guild means porting all three to TypeScript against an SDK we've never used, on Guild's inference rather than our own. That is not a 6-hour side-quest — it's the whole hackathon.

**Minimum-effort integration that is still load-bearing, not logo-bolting:**

Port **one** agent — the **Validator** — to a Guild `llmAgent()`. Rationale: the Validator is the most prompt-shaped and least state-shaped of the three, so it's the cheapest honest port (`llmAgent({description, tools, systemPrompt})` is close to a prompt + a schema), and it's the one where "governed, versioned, auditable" is a real claim rather than a decorative one — a validator that can't be audited is worthless, so Guild is doing actual work in the story.

Then:
1. Our Python Evolution loop keeps its own fast in-memory generation counter. **Don't** couple generations to Guild versions.
2. Each time the swarm calls the Validator, it goes out to the hosted Guild agent, producing a real session.
3. The dashboard polls `guild session events <id> --events all --mode jsonl` and renders the actual trace — real spans, real `llm_start`/`llm_done`, real timings. **This is the demo moment**, and it's true.
4. Use `guild agent save --publish` at **milestone** granularity only — e.g. "Validator v1.0 → v1.1 after we tightened the fitness criteria." Show `guild agent versions` on stage as *provenance for the validator*, not as the generation counter. That framing is honest and still lands the "versioned, reversible" point.

Estimated cost: ~60–90 min for the Validator port and trace plumbing, assuming auth lands promptly and the workspace has credentials. Everything is blocked behind step 4/5 of the HUMAN TODO.

**Risks:** (1) 46 releases in 4.5 months — pin `0.17.0`. (2) Server-side-only execution means Guild's uptime and our conference wifi are both in the demo's critical path — **cache a known-good trace JSON as a fallback before going on stage.** (3) Sponsor's Python claim being wrong may mean other claims are aspirational; verify anything else they promise before building on it.
