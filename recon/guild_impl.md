# Guild.ai — Implementation

**Status: SHIPPED.** The Validator is live on Guild as a published, semver-versioned
agent. It has been invoked with real payloads drawn from our own graph and personas,
it returns a typed verdict, and its execution traces have been pulled back and saved.
Two real traces are checked in — one `promote`, one `reject`.

Companion doc: `recon/guild.md` (the pre-build recon). This file supersedes its
"estimated cost" section and confirms or corrects its findings where they were guesses.

---

## 1. Identifiers

| Thing | Value |
| --- | --- |
| Agent name | `ayushgupta4897~swarm-validator` |
| **Agent id** | **`019f95a6-021f-726e-0000-3f814dd14685`** |
| Owner | `ayushgupta4897` (`019f9573-2a84-0175-0000-3c170f4e8887`) |
| Workspace | `ayushgupta4897/swarm-evolution` (`019f9583-ecc3-3bb9-0000-40f419c2e23b`) |
| Agent type | `GUILD_TYPESCRIPT` |
| Category / tags | `development` / `testing`, `observability` |
| Local source | `guild_agents/swarm-validator/` |
| Git remote | `https://app.guild.ai/git/019f95a6-021f-726e-0000-3f814dd14685` |
| CLI | `@guildai/cli` 0.17.0 |
| Runtime model (server-selected) | `GEMINI / gemini-3.5-flash` |

### Versions

`guild agent versions` (run from `guild_agents/swarm-validator/`):

| Version id | Semver | Status | Summary |
| --- | --- | --- | --- |
| `019f95ad-6ca6-cf83-0000-a97a966c97da` | **1.1.0** | PUBLISHED | v1.1 validator: declare consoleTools so every gate decision emits agent_console trace events |
| `019f95a9-3999-cf83-0000-855dddbc7084` | **1.0.0** | PUBLISHED | v1 validator: promotion gate with typed verdict schema |

Plus several `DRAFT` rows — those are the working-copy uploads that `guild agent test`
creates on each invocation. They are noise; only the two `PUBLISHED` rows carry a semver.

**1.0.0 → 1.1.0 was a real bug fix, not a cosmetic bump.** `task.console.*` is
dispatched by the runtime as a `console_log` tool call, so an agent that logs without
declaring `consoleTools` in its tool set fails the whole run with
`does not have a tool named "console_log" in its tool set`. 1.0.0 has that bug; 1.1.0
declares the toolset. This is exactly the provenance story we want on screen: two
published versions, a stated reason for the bump, and the older one still addressable.

`core/guild_validator.py` **pins `GUILD_AGENT_VERSION` to 1.1.0** rather than tracking
HEAD. A verdict that promoted a patch should be attributable to an immutable build.

---

## 2. What the agent is

`guild_agents/swarm-validator/agent.ts` + `system-prompt.md`.

**Deviation from the plan, and why.** The plan said use the `LLM` template
(`llmAgent()`). We initialised from that template and then rewrote it as a
self-managed-state `agent()`. Reason: `llmAgent()`'s output type is hard-coded to
`{ type: "text", text: string }` — inspect
`node_modules/@guildai/agents-sdk/dist/llm-agent.d.ts`, its `Params` type has
`inputSchema` but no `outputSchema`. A promotion gate whose verdict is an unvalidated
blob of prose is not a gate. `agent()` accepts an arbitrary `z.object` `outputSchema`,
so the verdict is parsed and schema-validated *server-side* before it is ever returned.

This cost nothing: with a single LLM turn and no tool delegation, `start()` returns
`output(verdict)` directly. There is no `"use agent"` directive, so no Babel compiler
step and no change to the template's `package.json` build scripts. The only dependency
change was `npm uninstall @guildai-services/guildai~github` (the template's example
integration), done via npm, not by hand-editing `package.json`.

### Contract

Input (`z.object` at root, as the SDK requires):

```ts
{
  candidate:          { operation, target, diff, reflection },          // all string
  triggering_failure: { caller_utterance, agent_utterance,
                        ground_truth, failure_type },                   // all string
  historical_cases:   [{ persona_id, caller_utterance, expected_behaviour }]
}
```

Output:

```ts
{
  fixes_new_failure: boolean,
  regression_risk:   [{ persona_id: string, would_regress: boolean, reasoning: string }],
  verdict:           "promote" | "reject",
  reasoning:         string
}
```

### The gate is enforced three times, on purpose

`promote` iff `fixes_new_failure && no entry has would_regress` — the same rule as
`Validation.promotable` in `core/schemas.py`. It is stated in the system prompt, then
re-derived in TypeScript in `enforceGate()` (which rewrites the verdict and appends a
`[gate override]` note if the model disagreed with its own evidence), then re-derived
again in Python in `_normalise()`. The rule is arithmetic; arithmetic does not belong
in a prompt. A future prompt edit cannot quietly widen the gate.

It also **fails closed**: if the model returns something that does not parse or does not
match `outputSchema`, the agent returns `verdict: "reject"` with the raw output attached,
rather than erroring. A gate that opens when it malfunctions is not a gate.

---

## 3. How to invoke it

### From Python (the way our system uses it)

```python
from core.guild_validator import validate_via_guild, fetch_trace, summarise_trace

verdict = validate_via_guild(candidate, triggering, history, timeout=60)
if verdict is None:
    ...  # Guild had nothing to say. Carry on with the local Validator.
else:
    trace = fetch_trace(verdict["session_id"])
    tile  = summarise_trace(trace)
```

Smoke test, end to end, with the checked-in real payload:

```bash
.venv/bin/python -m core.guild_validator
```

### From the CLI directly

```bash
cd guild_agents/swarm-validator

# Against the pinned published version (~23-38s round trip)
guild --mode json agent test \
  --workspace ayushgupta4897/swarm-evolution \
  --agent-version 019f95ad-6ca6-cf83-0000-a97a966c97da \
  --timeout 120 < examples/request.json

# Against the local working copy (~67s: uploads and validates a bundle each call)
npm install && npm run bundle
guild --mode json agent test --workspace ayushgupta4897/swarm-evolution \
  --bundle agent.js.gz < examples/request.json
```

Input is fed as JSON on **stdin**. The CLI wraps it in a fenced code block as the first
user message and the runtime coerces it against `inputSchema`. Output appears on stdout
as a line prefixed `< `, and the session id on a `✓ Session:` line — both are what
`_parse_test_output()` scans for.

### Publishing a new version

```bash
cd guild_agents/swarm-validator
git add <any new files>            # -A only commits tracked files
guild agent save --all --message "v1.2 ..." --bump minor --publish
```

Never `git push` (a pre-push hook blocks it) and never `git pull` (use `guild agent pull`).
A publish is a git push + remote `npm install` + `tsc` + bundle + validate; measured at
**~45s** end to end, with a 300s timeout.

---

## 4. Trace format

```bash
guild --mode json session events <session-id> --events all --limit 200
```

**Correction to `recon/guild.md`:** `--mode jsonl` is advertised but CLI 0.17.0
pretty-prints a single JSON document for `session events` regardless. The real shape is
`{"items": [...], "pagination": {has_more, limit, offset, total_count}}`.
`core/guild_validator.py::_parse_events` parses the document and keeps a line-oriented
fallback in case a later release starts honouring the flag. The `.jsonl` files we ship
are that `items` array, one compact event per line, sorted by `created_at`.

A full validator run is exactly **10 events**:

| # | `type` | `entity_type` | Carries |
| --- | --- | --- | --- |
| 1 | `user_message` | `EntEventUserMessage` | our JSON payload, `author`, `task` |
| 2 | `runtime_start` | `EntEventRuntimeStart` | |
| 3 | `runtime_start` | `EntEventRuntimeStart` | |
| 4 | `llm_start` | `EntEventLlmStart` | **`provider`, `model`, `headers`, full `payload` (system prompt + messages)** |
| 5 | `agent_console` | `EntEventAgentConsole` | `level`, `content` — our "validating <op> on <target> against N historical case(s)" |
| 6 | `runtime_done` | `EntEventRuntimeDone` | |
| 7 | `llm_done` | `EntEventLlmDone` | **`status_code`, `body` (raw completion), `llm_event_id` linking back to event 4** |
| 8 | `runtime_done` | `EntEventRuntimeDone` | |
| 9 | `runtime_start` | `EntEventRuntimeStart` | |
| 10 | `agent_console` | `EntEventAgentConsole` | our "verdict: promote" / "verdict: reject" |

Every event has `id`, `type`, `entity_type`, `created_at`, `updated_at`, and a `task`
blob (agent metadata, category, workspace — most of the file size). `llm_start.created_at`
→ `llm_done.created_at` is the real inference latency; measured 8.9–10.0s.

`summarise_trace()` in `core/guild_validator.py` reduces this to a dashboard tile:
event counts by type, provider, model, `llm_latency_ms`, and the console lines.

### Captured artifacts (all real, all pulled from live sessions)

| File | Session | Verdict |
| --- | --- | --- |
| `state/guild_trace_sample.jsonl` | `019f95b2-078e-f268-0000-47fd2411ee44` | **promote** |
| `state/guild_trace_reject_sample.jsonl` | `019f95b2-9e5d-f268-0000-3c3763aea1c0` | **reject** (synthetic negative control) |
| `state/guild_trace_gen001.jsonl` | `019f95b4-f5ef-f268-0000-98f022316e2b` | **reject** — real candidate `wp_b4da9382` from `state/gen_001.json` |
| `state/guild_trace_latest.jsonl` | rolling cache, rewritten on each successful `fetch_trace` | — |
| `state/guild_verdict_latest.json` | rolling cache, rewritten on each successful verdict | — |

Both sample sessions ran against published **1.1.0**.

---

## 5. The demo payloads

All three are built from our own artifacts — `graphs/gen_0.json` (the `pricing_lookup`
node), `personas/auto_servicing.json`, `kb/auto_servicing.md`, and `state/gen_001.json`.

### The one that matters: `examples/request_gen001.json` → `reject`

**A real candidate from a real generation, and the two validators agree.**

`wp_b4da9382` is an `add_tool_requirement` patch on `pricing_lookup.data.prompt`,
authored by our Evolution agent in generation 1 and read verbatim out of
`state/gen_001.json` — operation, target, diff and reflection are the model's own,
untouched. The local `Validator` in `core/evolve.py` executed it and killed it:

```
"replayed 4 previously-passing case(s); 0 still pass, 4 regressed."
confidence 0.0, status extinct
```

The hosted Guild validator, which *never runs the graph* and sees only the diff and the
persona descriptions, independently returned `reject` — and named a mechanism the
execution counts alone do not give you:

> "Since discounts and price matching are pricing-related queries, a tool lookup for
> these non-existent database items will return no data, triggering the fallback
> instruction to 'offer to follow up' instead of directly stating that no such discounts
> are offered."

That is `p_discount_hunt` regressing, correctly identified from first principles. Two
independent gates, two different mechanisms, two different model families, same verdict.
Trace: `state/guild_trace_gen001.jsonl`.

Caveat, stated plainly: the *candidate* is real and unmodified, but no per-call trace of
the triggering conversation is persisted anywhere on disk, so the four
`triggering_failure` strings in this payload were reconstructed from the patch's failure
signature, its reflection, and `kb/auto_servicing.md` (the rotors-inclusion claim). The
candidate, the historical personas and the verdict are real; those four strings are a
faithful reconstruction, not a capture.

### `examples/request.json` → `promote` An `append_constraint` on
`pricing_lookup.data.prompt` forbidding unlooked-up dollar figures, against the
`p_brake_price_sedan` fabrication ("just ballpark it for me" → "$400 to $450"; KB says
$285), replayed against five historical personas.

### `examples/request_reject.json` → `reject`

The synthetic negative control. A `change_transition` patch that *sounds* plausible — route to
Vehicle Clarification before Pricing. The agent rejected it, unprompted, on two correct
grounds:

> "the caller already explicitly provided both the vehicle (2019 Accord) and the service
> (front brakes), meaning the agent would still have transitioned to the pricing node and
> fabricated the price"

and it flagged `p_discount_hunt` and `p_warranty_claim` as regressions — forcing vehicle
clarification would block a general policy question and would intercept a warranty
escalation that is *supposed* to escalate. `p_warranty_claim` is our negative control for
the escalation penalty; the hosted validator found it without being told it was special.

That is the whole story in one screenshot: a Guild-hosted, versioned agent killing a
plausible mutation for a mechanistic reason, with a pullable trace proving it happened.

**Round-trip latency, measured:** 23s and 38s against the published version; 67s against
a locally-uploaded bundle. Inference itself is 8.9–10.0s; the rest is session setup and
dispatch.

---

## 6. What Guild is and is not doing for us — honestly

**Is doing:**

1. **Provenance for the Validator.** Two published semver versions, server-validated
   builds, immutable version ids, a stated reason for the bump. Our Python code pins a
   version rather than tracking HEAD, so "which validator promoted this patch" has an
   exact answer.
2. **Auditability.** Every verdict leaves a typed trace containing the exact system
   prompt, the exact model, the raw completion, timings, and our own console lines. That
   is a real answer to "show me what decided this", and it is the reason this integration
   is load-bearing rather than decorative — a validator nobody can audit is worth little.
3. **A genuine second opinion.** The hosted validator reasons on the *same* evidence as
   the local one but by a different mechanism (prediction from the diff vs. execution of
   the patched graph), on a different model family (Gemini vs. our OpenAI-compatible
   endpoint). Disagreement between the two is information.

**Is not doing:**

1. **Not the generation counter.** Confirmed as predicted: a version bump is a git push
   plus a remote TypeScript build, measured ~45s with a 300s timeout. That is release
   cadence. Our generation counter stays in Python, in memory, fast. We deliberately did
   *not* wire `guild agent save --publish` into the loop.
2. **Not authoritative.** `Validator` in `core/evolve.py` remains the gate of record. It
   *executes* candidates against replay cases and derives `Validation.confidence` from
   observed pass rates (`Validation.__post_init__`). The Guild agent *predicts* — it
   never runs the patched graph. We do not let a prediction overwrite a measurement.
3. **Not in the critical path.** Every function in `core/guild_validator.py` returns
   `None` / `[]` on any failure — timeout, network, missing CLI, non-zero exit,
   unparseable output — and never raises. A ~23s server round trip with conference wifi
   in it must not be able to stall an autonomy loop.
4. **Not hosting the other two agents.** No Python SDK exists (see `recon/guild.md` §4).
   Attribution and Evolution stay in Python. Porting all three was never a 90-minute job.
5. **Not our LLM.** Guild injects its own credentials and picks its own model
   (`gemini-3.5-flash` here). It does not wrap our Anthropic/OpenAI calls; the hosted
   validator's inference is Guild's, and its verdict reflects Guild's model, not ours.

### Demo-time failure modes

| Failure | Behaviour |
| --- | --- |
| Wifi down / Guild unreachable | `validate_via_guild` → `None`. Loop continues on the local Validator. `fetch_trace` serves `state/guild_trace_latest.jsonl`, then `state/guild_trace_sample.jsonl`. |
| Trace served from cache | Every event tagged `_guild_source: "cached"` or `"sample"` (vs `"live"`). The dashboard can and should label it. We do not pass cached data off as live. |
| Guild CLI missing / logged out | `_run` detects a missing binary and returns `None` without raising. |
| Model returns garbage | Agent returns `verdict: "reject"` with the raw output in `reasoning`. Fails closed. |

### Environment overrides

`GUILD_BIN`, `GUILD_AGENT_DIR`, `GUILD_WORKSPACE`, `GUILD_AGENT_ID`,
`GUILD_AGENT_VERSION` (empty string = use the local bundle), `GUILD_STATE_DIR`.

---

## 7. Files

| Path | What |
| --- | --- |
| `core/guild_validator.py` | Python wrapper: `validate_via_guild`, `fetch_trace`, `summarise_trace`. Never raises. |
| `guild_agents/swarm-validator/agent.ts` | The agent: schemas, gate enforcement, JSON extraction, fail-closed path |
| `guild_agents/swarm-validator/system-prompt.md` | The validator's reasoning instructions |
| `guild_agents/swarm-validator/examples/request.json` | Real promote payload |
| `guild_agents/swarm-validator/examples/request_reject.json` | Synthetic negative-control reject payload |
| `guild_agents/swarm-validator/examples/request_gen001.json` | Real candidate `wp_b4da9382` lifted from `state/gen_001.json` |
| `state/guild_trace_sample.jsonl` | Real captured trace, promote verdict |
| `state/guild_trace_reject_sample.jsonl` | Real captured trace, reject verdict |
| `state/guild_trace_gen001.jsonl` | Real captured trace, reject verdict on a real generation-1 candidate |

`guild_agents/swarm-validator/guild.json` holds the agent id and is gitignored by Guild's
own template — the id is recorded above so it survives a clean checkout.
