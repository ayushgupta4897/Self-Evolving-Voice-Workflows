# Self-Evolving Voice Workflows

**Our voice agent rewrites its own conversation graph when customers get wrong answers.**

Voice agents hallucinate prices, misstate policies, and cite outdated procedures. Today the fix
loop is: customer complains → engineer reads transcript → engineer patches prompt → redeploy.
The organization learns. **The agent doesn't.**

We close that loop automatically. Every call is scored against verified ground truth in Senso.
Failures are attributed to a specific node in the workflow graph. Three candidate mutations are
generated, regression-tested against historical failures, and one is promoted **only if it fixes
the new failure without breaking old ones.** The survivor is written back to the live Dograh
graph. The next call runs the evolved workflow.

**[Live presentation →](https://swarm-evolution.vercel.app)** · **[3-minute demo video →](video/demo.mp4)** · **[Demo runbook →](DEMO_RUNBOOK.md)**

> Built at the Self-Evolving Agents Hackathon, Tokens& — San Francisco, 24 July 2026.

---

## The failure we fix

A caller asks what front brakes cost on a 2021 Toyota Highlander. The verified answer is
**$340** — SUV front axle, pads and rotors. Our gen-0 agent said:

> *"On a 2021 Toyota Highlander, front brakes usually run right around **550 to 750 dollars**,
> depending on…"*

Senso scored it **0.10, ungrounded**. It never called the knowledge-base tool that was attached
to that node and would have given it the right number.

That exact turn is what triggered generation 2 — the promotion the demo turns on. Other
personas fabricated the same way in the same run: a timing belt quoted at "800 to 1,100
dollars" for a service that isn't in the price list at all (correctness 0.00).

That is the whole thesis in one turn: **the tool was available and the node's instruction
permitted answering without it.** No amount of better retrieval fixes that. RAG fixes the
retrieval. We fix the instruction that failed to invoke retrieval.

## What actually happened

One batch run, 32 calls, every failure evolved with no human in the chain:

| | |
|---|---|
| Generations | **11** (0 errored) |
| Candidates generated | 33 |
| **Candidates killed by the validator** | **27** |
| Patches promoted | 3 |
| Generations where selection actually eliminated something | **10 of 11** |

The patch the demo turns on is **`wp_d46a3166`** — `add_tool_requirement` on
`pricing_lookup`, `authored_by=evolution_agent`:

> *Before providing any specific pricing figures or vehicle-specific details, you must
> first use the lookup tool to retrieve information from the knowledge base.*

It was written by the system, applied to the live Dograh graph, and published as version 4.
The live runtime now answers **$285** and calls the tool.

Then the same patch — retrieved from Actian at **cosine 1.000** by a *healthcare* failure
signature, with healthcare explicitly excluded from the search — fixed a specialist-copay
fabrication in a domain sharing no vocabulary with car servicing: **$40 guessed → $47
grounded**, tool invoked.

Lexical overlap between the two knowledge bases was measured, not assumed: Jaccard **0.176**,
and every shared word is boilerplate (`section`, `verified`, `escalation`).

---

## Architecture

```
      caller (WebRTC / text persona)
                │
                ▼
   ┌────────────────────────────────┐
   │  DOGRAH — voice runtime         │   workflow graph: nodes, edges,
   │  React Flow graph, versioned    │   instructions, tools, transitions
   │  LLM served by PIONEER          │
   └───────────────┬────────────────┘
                   │  per-node execution trace
                   ▼
   ┌────────────────────────────────┐
   │  SENSO — verified knowledge     │   grounded answer + citation + score
   │  POST /org/search               │   ← THE CORRECTNESS ORACLE
   └───────────────┬────────────────┘
                   │
          [ FAILURE DETECTED ] ── automatic, zero clicks ──►
                   │
   ┌───────────────▼────────────────────────────────────────┐
   │  ATTRIBUTION  → which node is responsible (root, not    │
   │                 merely the loudest)                     │
   │  EVOLUTION    → 3 candidates, 3 different operators      │
   │  VALIDATOR    → replay history; promote only on          │
   │                 fixes-new AND zero-regression            │
   └───────────────┬────────────────────────────────────────┘
                   │
        ┌──────────▼──────────┐        ┌────────────────────┐
        │ ACTIAN VectorAI      │        │ PIONEER adaptive    │
        │ survivors keyed on   │        │ inference — failure │
        │ FAILURE SIGNATURE    │        │ traces mined from   │
        │ (structure only)     │        │ calls it served     │
        └──────────┬───────────┘        └────────────────────┘
                   │
                   ▼
      graph updated + published → next call runs the evolved workflow
```

---

## Sponsor integrations

Every row is load-bearing. Remove any one and something specific stops working.

| Sponsor | What it does here | Why it's load-bearing | Where |
|---|---|---|---|
| **Senso** | Verified knowledge base. `POST /org/search` returns a grounded answer, a verbatim citation, and a relevance score. Also stores the **escalation policy**, read from the KB and cached. | The correctness oracle. Every fitness number traces to a Senso answer. Editing the KB changes escalation behaviour with **no code change**. Without Senso there is no fitness signal and no project. | `core/oracle.py` |
| **Pioneer** | OpenAI-compatible endpoint serving the **agent's own inference** — the calls that fail. | Adaptive inference mines *live production failures*. That claim is only true if Pioneer served the calls that failed. It does. | `core/executor.py` |
| **Actian VectorAI** | Stores every surviving patch keyed on a **failure-signature embedding that contains no domain content**. | Enables cross-vertical transfer: a patch learned on brake pricing retrieves for a healthcare copay failure, because the signature is structural, not topical. | `core/patch_store.py` |
| **Dograh** | Voice runtime and the workflow graph we mutate. Versioned graphs, REST read/write, headless text sessions. | The graph we evolve is the graph that runs. Gen-0 and gen-N coexist as real versions — that's the diff view. | `core/dograh_client.py` |
| **Guild.ai** | Hosts the Validator as a governed, versioned agent; execution traces pulled via `guild session events --mode jsonl`. | A validator that cannot be audited is worthless. Guild makes the promotion decision inspectable. | see *Honest scope* |
| **Replay.io** | QA pass over the dashboard. | Bugs found and fixed are documented below. | `recon/replay.md` |

---

## What makes this different

**We evolve a graph, not a string.** Mutations target node instructions, edge conditions, and
tool requirements inside a React Flow workflow — not a single prompt.

**Fitness comes from live conversational ground truth.** Self-evolving agents (GEPA, AlphaEvolve,
Darwin Gödel Machine, EvoAgentX) live in code and text *because fitness there is cheap, parallel
and deterministic.* Voice was skipped because the signal is slow and noisy. A verified knowledge
base with citations is what closes that gap.

**Promotion is regression-gated.** A patch that fixes today's failure and breaks last hour's
dies. That gate is what separates selection from a patch pipeline.

### Honest limitation, stated before you find it

The hypothesis space is bounded by four mutation operators — `append_constraint`,
`add_tool_requirement`, `rewrite_instruction`, `change_transition`. **The system cannot invent a
new node type.** Within that space, hypothesis generation is autonomous: we wrote the fitness
function, not the rules it discovered.

---

## Design decisions worth defending

**The failure signature contains no domain content.** `FailureSignature.to_embedding_text()` is
the single chokepoint for what gets embedded, and it emits only: failure type, node role, whether
a tool was available, whether it was invoked, and whether a specific value was asserted. No
utterance, no product, no vertical. If domain text leaked in, Actian retrieval would key on topic
and cross-vertical transfer would silently stop working while still *looking* fine.

**`confidence` is computed, never authored.** It is derived in `Validation.__post_init__` from the
Validator's observed pass rate over replayed cases. There is no setter. When you ask where the
number comes from, the answer is a constructor.

**The escalation penalty distinguishes legitimate from cop-out escalation.** Penalising every
transfer teaches an agent that never escalates — the mirror image of the degenerate optimum, and
just as wrong. `personas/auto_servicing.json` contains `p_warranty_claim` purely as the negative
control: a caller where transferring *is* correct. The oracle scores it `escalated=True,
escalation_warranted=True`, and fitness does not punish it. Verified by test.

**Validation samples the triggering case three times.** One replay against a stochastic agent is a
coin flip. Our first real run promoted a transition-condition tweak — which cannot affect
fabrication at all — while killing two patches that correctly said "consult the knowledge base
before answering." Majority-of-three turned that from noise into evidence. Validation also runs at
a lower temperature than live calls: variance belongs in call generation, not in measurement.

**Attribution finds the root node, not the loud one.** A fabricated price at turn 4 is often
caused by a clarification node at turn 2 that never collected the vehicle class. The signature is
built from the node being *patched*, not the turn that *failed* — those differ whenever
attribution walks upstream, and conflating them stores every patch under a structure that doesn't
describe what it fixes.

---

## What's real vs. what's warm-started

Stated plainly, because the alternative is being caught.

**Real, running, verified:**
- Senso scoring against a real ingested knowledge base, with citations
- Node-level attribution from Dograh's own per-turn `node_transition` events
- 3 candidates per generation, generated by 3 structurally different operators
- Regression-gated promotion — candidates genuinely die
- Actian writes and semantic retrieval on failure signature
- Live graph update: `PUT` + **publish** (Dograh's text-chat runs the *draft* while the UI shows
  the *published* version, so publishing is not optional — it's inside `apply_and_publish`)
- The full chain runs with zero human clicks

**Warm-started or scoped down — and why:**
- **Generation depth** is warm-started. Personas were run in batch before the demo. It is the same
  code path a live call takes, not a separate offline mode.
- **Pioneer's adaptive fine-tune** takes ~6 hours and ~$35. It cannot complete inside the event.
  Traces are submitted and the job is kicked; the weight layer is training, not trained.
- **Guild** hosts the Validator only. The spec called for versioning-as-generation-numbering; each
  Guild version is a git push plus a remote TypeScript build with a 300s timeout, which is release
  cadence, not evolution cadence. Guild versions are provenance for the Validator instead. Guild's
  SDK is also TypeScript-only despite the "Python + TypeScript" claim.
- **Senso has no Evaluate API.** The brief assumed one. It does not exist — proven by exhaustive
  endpoint mapping, documented in `recon/senso_endpoints.md`. Senso supplies verified knowledge,
  retrieval and citations; the correctness comparison is ours.
- **Two shallow verticals** (healthcare, insurance) exist only for the transfer beat.
- **Voice** is WebRTC/in-browser. PSTN is not wired.

---

## Running it

```bash
docker compose -f vendor/dograh/docker-compose.yaml up -d
```

```bash
.venv/bin/python scripts/run_evolution.py --warmup --rounds 1
```

```bash
cd dashboard && npm run dev
```

Requires `.env.local` with `SENSO_API_KEY`, `PIONEER_API_KEY`, `OPENAI_API_KEY`, and Dograh
credentials. Actian runs locally on 6573–6575.

---

## Repository map

| Path | What |
|---|---|
| `core/schemas.py` | Data contracts. The two invariants above are enforced here, structurally. |
| `core/oracle.py` | Senso oracle + LLM-judge fallback. `get_oracle()` banners which is active. |
| `core/executor.py` | Local replay harness. Reads the same graph fields Dograh does. |
| `core/evolve.py` | Attribution, mutation, validation, promotion gate. |
| `core/loop.py` | The autonomous loop. |
| `core/patch_store.py` | Actian. |
| `core/dograh_client.py` | Graph read/write/publish, text sessions, node highlight. |
| `kb/auto_servicing.md` | Verified ground truth. |
| `personas/` | 16 caller personas + fixed replay scripts. |
| `recon/` | Verified findings on every sponsor API, including the negative results. |
| `dashboard/` | Graph diff, population board, fitness trend. |

---

## Replay.io QA

21 journeys explored against the dashboard over an outbound-only tunnel. Replay found
a real defect — broken deep-link anchors — that both its own judge and our first
investigation dismissed.
**0 open bugs at submission.**

The one finding worth reading is written up in [`recon/replay.md`](recon/replay.md),
including the part where we thought Replay's judge was wrong and it turned out we were.
18 of 21 runs show `failed`, and the honest reason is a testability property of our own
app rather than a defect: the dashboard polls every 2 seconds and replaces DOM nodes, so
an automated driver can resolve an element and then click a detached node. A human never
sees it. Effective coverage is therefore the 3 completed runs plus the exploration pass,
not all 21 — we would rather say that than let "0 open bugs" imply more than it does.

One genuine fix landed during the QA pass: the graph-diff view defaulted to the highest
generation number, and since most generations correctly kill all three candidates, the
load-bearing visual showed an empty state most of the time. It now defaults to the most
recent generation that actually promoted.

## Demo

[`DEMO_RUNBOOK.md`](DEMO_RUNBOOK.md) — beat-by-beat script, the questions to expect,
what to concede before being asked, and what to do when something breaks.

## Demo video

[`video/demo.mp4`](video/demo.mp4) — 2:46. Narration script and the source for every
claim in it: [`video/script.md`](video/script.md). Build is reproducible from
`video/*.py`.

Two corrections the video surfaced, worth recording because they were errors in *our*
account rather than in the system:

- An earlier draft of this README quoted gen-0 fabricating "$500 / $350 / $200–250".
  Those were real observations from earlier exploratory runs, but they are not in this
  run's traces, and citing them as evidence for this run was sloppy. The verified
  fabrication that actually triggered generation 2 is the Highlander quote above.
- Generation 2 promoted one candidate and left two **viable** — it killed nothing. The
  kill number is run-wide (27 of 33), not per-generation. The video says the true,
  larger number.

Replay.io is credited on the end card but not narrated. Every other sponsor is
introduced by the requirement it answered; Replay had no such requirement in the film's
argument, and bolting it on would have been the exact thing we were trying to avoid.
