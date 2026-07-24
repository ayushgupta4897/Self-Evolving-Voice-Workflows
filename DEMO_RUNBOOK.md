# Demo runbook

Five minutes. Everything below is real and was verified at ~15:00 on the day.
Where something is warm-started, the script says so out loud.

---

## Before you stand up

```bash
docker ps   # expect: dograh-api, dograh-ui, dograh-postgres, dograh-redis, minio, vectorai
```

```bash
open http://localhost:3100 && open http://localhost:3010/workflow/1
```

Checklist:
- Dashboard badge reads **`live · 11 gens`**. If it reads **SAMPLE DATA**, `state/` is empty — stop and say so rather than presenting fixtures.
- Dograh workflow 1 is on **version 4**, `pricing_lookup` ringed.
- On the graph-diff view, click generation **2**. That is the patch the story is about.
- Have `video/demo.mp4` open in a background tab as insurance.

---

## Beat 1 — the felt failure (0:40)

Ask the agent, live or from the recording:

> "What do front brakes run on a 2021 Toyota Highlander? Pads and rotors both."

Gen-0 said **"right around 550 to 750 dollars."** Verified answer is **$340**
(SUV front axle). Senso scored it **0.10, ungrounded**, tool never called.

This is the real turn that triggered generation 2 — the promotion this demo is about.

> *"That transcript reads perfectly. Fluent, confident, and wrong. Nobody catches
> this by ear. This is the failure that keeps voice agents out of production."*

**If the live call grounds correctly instead of fabricating:** say so, and move to
the recording. The gen-0 failure is stochastic — a capable model sometimes reaches
for the tool on its own. Do not re-roll it on stage.

---

## Beat 2 — the invisible cause (0:50)

Dashboard → **03 Fitness** → the failing/passing call panel.

- correctness **0.10**, **ungrounded**
- ground truth **$340** (SUV front axle), with the citation Senso returned
- `tools_available: ['retrieve_from_knowledge_base']`, `tools_called: []`

$285 is the *sedan* front-axle price. The Highlander is an SUV. Don't mix them on
stage — the vehicle-class distinction is exactly the kind of thing the gen-0 node
was flattening, so getting it wrong ourselves would be an unfortunate irony.

> *"The tool was attached to that node. The node never called it. This is not a
> retrieval problem — better retrieval cannot fix a node whose instruction permits
> answering from memory."*

Senso framing, if asked: it holds the verified knowledge, returns the grounded
answer and the verbatim citation. **It has no Evaluate API** — we checked
exhaustively. The correctness comparison is ours.

---

## Beat 3 — attribution and selection (1:20)

Dashboard → **02 Population**. Headline: **"27 of 33 candidates were killed by the validator."**

Point at one dead candidate and read its **CAUSE OF DEATH** aloud:

> *"replayed 1 previously-passing case; 0 still pass, 1 regressed."*

Then point at **VALIDATOR PASS RATE** and its caption — *computed from replayed
cases, not model-authored.*

> *"Every failure produces three candidates using three structurally different
> operators. The validator replays history against each and keeps only the one
> that fixes the new failure and breaks nothing old. Twenty-seven died. Ten of
> eleven generations eliminated something — that is selection, not a pipeline."*

**Guild moment.** One of those killed candidates was independently reviewed by our
validator hosted on Guild — different model family — which rejected it too and
named the mechanism: forcing a mandatory price lookup on a discount that doesn't
exist returns nothing and trips the follow-up fallback. Two gates, two models, one
verdict, with a causal explanation.

---

## Beat 4 — the graph rewrites itself (1:10)

Dashboard → **01 Graph diff** → generation **2**.

> Note: generation 2 itself promoted 1 and left 2 viable — it killed nothing. Do NOT
> say "two of three were killed" here. The kill number is run-wide: 27 of 33. Beat 3
> is where selection is shown; this beat is where the rewrite is shown.

- gen-0 beside gen-N, `pricing_lookup` ringed in both
- BEFORE / AFTER instruction diff, added lines marked
- the rule pulled out large

The patch: `wp_d46a3166`, `add_tool_requirement`, `authored_by=evolution_agent`.

> *"I wrote the fitness function. I did not write that rule."*

Switch to **Dograh** (`localhost:3010/workflow/1`) — the same evolved graph, in the
real voice runtime, published as version 4. Gen-0 through gen-4 coexist as real
versions.

Live proof:

```
[pricing_lookup] tools_called=['retrieve_from_knowledge_base']
"...the price is $285. This includes parts, labor, shop supplies and disposal fees."
```

---

## Beat 5 — transfer to a different domain (1:00)

```bash
.venv/bin/python scripts/transfer_demo.py --vertical healthcare
```

A healthcare agent fabricates a specialist copay — **$40**, correctness 0.10,
tool not called. Its failure signature:

```
ungrounded_fabrication | information_retrieval | avail=1 | inv=0 | spec=1
```

Nothing in that key is about healthcare, or about cars.

Actian returns **`wp_d46a3166`** at **cosine 1.000** — `origin_vertical=auto_servicing`,
with healthcare explicitly excluded. Applied verbatim, the healthcare agent answers
**$47**, grounded, tool invoked.

> *"That rule was learned on brake pricing. It fired on an insurance copay. The
> signature is structural, not topical — measured, not asserted: lexical overlap
> between the two knowledge bases is 0.18, and every shared word is boilerplate."*

**Demo healthcare, not insurance.** Insurance produces correct grounded answers but
scores 0/5 under Senso because a short follow-up returns a no-results sentinel that
outranks the real passages. Known, understood, not hidden.

---

## Answers to the questions you will get

**"How is this different from DSPy with a good metric?"**
We evolve a graph — node instructions, transitions, tool requirements — not a
prompt string. Fitness comes from live conversational ground truth with citations,
not a static benchmark. And promotion is regression-gated: a patch that fixes the
new failure but breaks an old one dies. Twenty-seven did.

**"Where is the evolution? This looks like a patch pipeline."**
Show the dead candidates. Population composition changes across generations, and
10 of 11 eliminated something. Concede the boundary first: the hypothesis space is
four mutation operators. It cannot invent a new node type. Within that space,
hypothesis generation is autonomous.

**"Isn't this RAG with extra steps?"**
RAG fixes retrieval. We fix the instruction that failed to invoke retrieval. The
tool was there. The node didn't call it.

**"Where does `confidence` come from?"**
A constructor — `Validation.__post_init__`, from the validator's observed pass rate
over replayed cases. There is no setter. No model authors it.

---

## Say these before you are asked

- **Generation depth is warm-started.** 11 generations were run in batch before the
  demo, through the same code path a live call takes.
- **Pioneer's fine-tune is submitted, not finished.** ~6 hours. The weight layer is
  training, not trained.
- **Guild hosts the validator only.** Not our generation counter — a publish is ~45s,
  which is release cadence, not evolution cadence.
- **Early generations have thin confidence.** Gen 2 is 0.50 over *zero* historical
  cases, because the corpus did not exist yet. The number says so.
- **Replay: 0 open bugs, but 18 of 21 runs failed for harness reasons** — a live-polling
  page detaches the DOM nodes an automated driver is holding. Effective coverage is
  the 3 completed runs plus exploration, not all 21.

---

## If something breaks

| Failure | Do this |
|---|---|
| Live call grounds instead of fabricating | Say it's stochastic, cut to `video/demo.mp4` |
| Dashboard shows SAMPLE DATA | Say so. Do not present fixtures as a run. |
| Dograh unreachable | Dashboard alone carries beats 2–5 |
| Actian down | `docker restart vectorai`, then `PatchStore.ensure_ready()` reopens the collection |
| Senso 402 | Free tier credits. Oracle falls back to the LLM judge and banners it. |
| Anything else | Play the video. It is the full arc. |
