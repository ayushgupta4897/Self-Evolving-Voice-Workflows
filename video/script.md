# Demo video script — Self-Evolving Voice Workflows

**Runtime ~2:56.** Voice: ElevenLabs `8WqHCYyrnUqoK70Px5EJ` — *Nitin, Indian Accent and
Professional* — `eleven_multilingual_v2`, speed 0.98.

Sponsors are introduced by the requirement they answer, never as an integration.
Every figure is traceable to a file in this repo; the source is noted under each beat.
Spoken text is `video/vo/narration.txt`; cut points come from the word-level alignment
in `video/vo/alignment.json`.

---

## 1 — The failure · 0:03

> A caller asks an auto shop what front brakes cost on a twenty twenty-one Highlander.
> The agent answers: around five hundred and fifty to seven hundred and fifty dollars.
> Fluent, confident, wrong. The verified price is three hundred and forty. A
> knowledge-base tool was sitting on that node. It never called it.

`state/traces.json` · `call_1784925735456` · persona `p_brake_price_suv` · turn 1 · node
`pricing_lookup`. Agent utterance verbatim. Ground truth from `kb/auto_servicing.md`:
SUV / light truck, front axle, pads and rotors — **$340**. (Standard sedan is $285;
this caller was an SUV.) `tool_available_not_invoked: true`.

## 2 — Requirement: know you were wrong → **Senso** · 0:21

> To evolve, an agent has to know it was wrong. Not implausible — verifiably wrong,
> against something you can cite. That is Senso. Every turn is scored against a verified
> knowledge base that returns a grounded answer and a verbatim citation. This turn:
> correctness zero point one zero. Grounded, false. Ungrounded fabrication. Retrieval
> tool available. Retrieval tool not invoked. Senso also holds the escalation policy, so
> editing the knowledge base changes when the agent may transfer a caller, with no code
> change.

`state/traces.json` verdict block, `source=senso`. Escalation-policy claim: `README.md`
sponsor table and `core/oracle.py`. **Senso is never called an "Evaluate API"** — it has
none; the correctness comparison is ours.

## 3 — Requirement: the failures must be yours → **Pioneer** · 0:55

> And adaptive inference only means anything if the failures are your own. Pioneer serves
> every call this agent makes, so the traces are genuinely its.

`core/executor.py`; `logs_evolution.txt` shows `POST api.pioneer.ai/v1/chat/completions`
on every generated turn. **No claim that the fine-tune finished** — it takes ~6h and is
submitted, not trained.

## 4 — Attribution, three candidates, the gate, and **Guild** · 1:04

> Attribution points at the node responsible. Pricing lookup. Three candidate mutations,
> three different operators. The validator replays history against each one: fix today's
> failure, break last hour's, and you die. Across eleven generations this loop produced
> thirty-three candidates and killed twenty-seven of them. A gate that decides what ships
> should be auditable, and ideally not alone, so the validator is also published on Guild
> as a versioned agent. On a real generation-one candidate our gate killed it on four
> regressions. Guild's, a different model family, never executing the graph, rejected it
> too, and named the persona that would break.

`logs_evolution.txt` + `state/gen_001..011.json` — 11 × 3 = 33 candidates, 27 extinct,
3 promoted, 3 viable-unpromoted, 0 errored. Dashboard headline reads the same.
Guild: `recon/guild_impl.md` §5 — candidate `wp_b4da9382`, local kill
`"replayed 4 previously-passing case(s); 0 still pass, 4 regressed"`, hosted verdict
`reject` naming `p_discount_hunt`. Trace `state/guild_trace_gen001.jsonl`.

## 5 — Requirement: read, write, publish, version → **Dograh** · 1:43

> You cannot evolve what you cannot read, write, publish and version. Dograh is the
> runtime, and the graph. Generation two promoted this. Add tool requirement, on pricing
> lookup dot data dot prompt, authored by the evolution agent. It goes straight back into
> the live workflow, and is published. Version five. The graph we mutate is the graph
> that runs.

`state/gen_002.json` → `wp_d46a3166`, `add_tool_requirement`,
`target: pricing_lookup.data.prompt`, `authored_by: evolution_agent`.
Dograh UI shows **v5 (Published)**. (An earlier push set the draft but the publish
did not carry forward, so anything captured from the Dograh UI before ~15:52 shows the
unevolved graph — that footage was discarded.)

## 6 — Requirement: a patch must find its next failure → **Actian** · 2:04

> And a patch is only worth keeping if it can find the next failure that looks like it.
> Structurally, not topically. Actian stores every survivor keyed on a signature holding
> failure type, node role, tool available, tool not invoked. No product, no vertical, not
> one domain word. So when a healthcare agent fabricates a forty dollar copay against a
> verified forty-seven, that signature retrieves the auto servicing brake patch at cosine
> one point zero zero zero, with healthcare excluded. The same text, byte for byte, into
> a clinic's node. Forty becomes forty-seven. Grounded.

`scripts/transfer_demo.py --vertical healthcare`, run 2026-07-24 14:25; full stdout in
`video/assets/transfer_demo_raw.txt`. Retrieved `wp_d46a3166`, `origin=auto_servicing`,
`authored_by=evolution_agent`, similarity `1.000000`, `exclude_vertical='healthcare'`.
Before 0.00 / no tools / "about 40 dollars"; after 1.00 / `retrieve_from_knowledge_base`
/ "about 47 dollars".

## 7 — Close · 2:38

> We wrote the fitness function. We did not write that rule.

---

## Not in the film

- **Replay.io** — could not be introduced by a requirement without sounding bolted on,
  so it was cut rather than shoehorned.
- The dashboard's failing/passing call panel — it labels the oracle "Senso Evaluate",
  which is not a real surface, and its example scores a *correct* $285 answer at 0.00.
- The README's `$500 / $350 / "$200–250"` gen-0 fabrications — not present in any
  on-disk artifact, so not asserted.
