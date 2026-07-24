# Cross-vertical transfer beat — verified results

**STATUS: WORKS.** A patch whose `origin_vertical` is `auto_servicing` is retrieved
from Actian by a **healthcare** failure signature at cosine **1.000000**, with
`exclude_vertical="healthcare"` applied, and applying its diff verbatim to the
healthcare graph fixes the fabrication.

**The patch that transfers is HAND-SEEDED, not loop-authored.** Say this on stage.
Details in section 5 — it is not ambiguous and must not be presented as ambiguous.

Run it:

```bash
.venv/bin/python scripts/transfer_demo.py --vertical healthcare --runs 5
.venv/bin/python scripts/transfer_demo.py --vertical insurance --runs 5 --llm-judge
.venv/bin/python scripts/transfer_demo.py --audit-store     # population board
.venv/bin/python scripts/transfer_demo.py --seed            # re-seed if the collection is cleared
```

---

## 1. Headline reliability

| Vertical | Oracle | Beat landed | Notes |
|---|---|---|---|
| healthcare | **Senso** (scoped) | **4/5** | the 1 miss is Stage A: the agent spontaneously called the tool, so there was no fabrication to fix |
| healthcare | Senso | **4/4** of runs that actually fabricated | retrieval and fix never failed once the failure occurred |
| insurance | Senso (scoped) | **0/5** | **oracle artifact, not a beat failure** — see section 6 |
| insurance | LLM judge | **5/5** | the same patch, the same graph, the same $612 answer |

Pre-patch fabrication rate, measured separately on the unpatched healthcare graph
against `gpt-5.4-mini` on Pioneer (`--measure-before 5`): **4/5**. Run 4 of that
measurement called `retrieve_from_knowledge_base` unprompted and answered $47
correctly. That is the honest before-state: this model is capable enough to
sometimes do the right thing despite the instruction, roughly one call in five.

**What this means for the stage.** Budget for the possibility that the live
healthcare call does not fabricate. It is a ~20% event. If it happens, the script
says so explicitly (`ABORT: the retrieval node did NOT fail this run`) rather than
faking a failure — re-run it. Do not present this as deterministic.

The failure mode is entirely confined to Stage A. Stages B–D (signature,
retrieval, fix) landed **9/9** across both verticals in every run where a
fabrication actually occurred.

---

## 2. The retrieval key — verbatim

This is the string that is embedded and searched on. Printed by Stage B of the
demo, copied verbatim from run 1:

```
'A information_retrieval node produced a ungrounded_fabrication failure. A retrieval tool was available and was not invoked. The agent asserted a specific factual value.'
```

```
signature.key() : ungrounded_fabrication|information_retrieval|avail=1|inv=0|spec=1
```

The demo audits this string against 41 domain terms (auto servicing + the vertical
under test + generic value words such as `$`, `cost`, `price`):

```
domain-vocabulary audit over 41 terms (auto + healthcare + value words): leaked = NONE
```

The healthcare failure and the auto-servicing failure produce **byte-identical**
embedding text, which is why similarity is exactly `1.000000` rather than merely
high. That is expected and explainable: `to_embedding_text()` is a pure function of
five structural fields, and both failures have the same five values.

An occasional insurance run scored **0.9621** instead of 1.0. That is the real
retrieval working at the margin: on those runs the oracle judged
`asserted_specific_value=False` (`spec=0`), so the signature genuinely differed by
one field and the nearest structural relative was returned rather than an exact
match. A dict keyed on `signature.key()` would have returned nothing there. This is
a good detail to volunteer if asked whether the vector search is doing real work.

---

## 3. Senso ingestion — verified responses

Both KBs ingested via `POST https://apiv2.senso.ai/api/v1/org/kb/raw` with
`X-API-Key`. Both returned **202 processing** and were queryable within ~3 minutes.

```
healthcare.md 202 {"id":"17838612-9eec-4920-a28b-1c53125c6f2d","org_id":"68ee4fdd-01c1-4bea-ab2c-533d7bd8c4f2","type":"raw","title":"healthcare.md","latest_content_version_id":"9e5cdf52-b02e-4aa1-9bbc-3407e0cca035","version_num":1,"processing_status":"processing","content_type":"text/markdown","created_at":"2026-07-24T20:05:01.472793189Z"}

insurance.md  202 {"id":"f8f6e819-f1dc-4702-9199-7ae700e7f54c","org_id":"68ee4fdd-01c1-4bea-ab2c-533d7bd8c4f2","type":"raw","title":"insurance.md","latest_content_version_id":"65716918-f9f6-406f-ad42-efffe8870716","version_num":1,"processing_status":"processing","content_type":"text/markdown","created_at":"2026-07-24T20:05:02.214452777Z"}
```

**Content ids** (now wired into `scripts/transfer_demo.py`):

| Vertical | content_id |
|---|---|
| healthcare | `17838612-9eec-4920-a28b-1c53125c6f2d` |
| insurance | `f8f6e819-f1dc-4702-9199-7ae700e7f54c` |

Verification via `POST /org/search` scoped to each id — verbatim `answer` fields:

```
query: "What is the copay for a specialist consultation in network?"
scope: content_ids=["17838612-9eec-4920-a28b-1c53125c6f2d"]
200  answer: "The **in-network copay for a specialist consultation** is **$47 per encounter**."
     n_results: 4

query: "What is the collision deductible?"
scope: content_ids=["f8f6e819-f1dc-4702-9199-7ae700e7f54c"]
200  answer: "The **collision deductible** is **$612** per loss occurrence."
     n_results: 4
```

Both return the correct non-round value. Scoping is mandatory — the org is a shared
workspace and an unscoped query pulls in `auto_servicing.md`, which would grade the
healthcare call against brake prices.

---

## 4. Lexical disjointness — measured, not asserted

Content-word Jaccard similarity against `kb/auto_servicing.md` (words >3 chars,
stopwords removed):

| Pair | Jaccard | Shared content words |
|---|---|---|
| auto ∩ healthcare | **0.176** | 84 |
| auto ∩ insurance | **0.169** | 77 |

Every shared word is structural boilerplate — `escalation`, `escalate`, `section`,
`verified`, `authority`, `answerable`, `document`, `representative`, `binding`,
`estimate`, `figure`. **Not one subject-matter noun crosses over.** No `brake`,
`rotor`, `axle`, `vehicle`, `sedan`, `SUV`, `oil`, `tyre`, `warranty` appears in
either new KB; no `copay`, `deductible`, `collision`, `referral` appears in the auto
KB. The residual ~17% is the shared *shape* of an operations manual, which is the
point rather than a contaminant.

---

## 5. Which patch transferred — provenance, unambiguously

```
patch_id      wp_seed_auto_tool
created_at    2026-07-24 13:10:11
origin_vertical  auto_servicing
authored_by   hand_seeded_transfer_beat        <-- NOT the evolution agent
status        promoted
operation     add_tool_requirement
signature_key ungrounded_fabrication|information_retrieval|avail=1|inv=0|spec=1
confidence    0.5  (validator_pass_rate; tested=0, regressions=0)
```

**This patch was hand-seeded by `scripts/transfer_demo.py --seed`. It was not
authored by the evolution loop and was never validated against a regression
corpus.** Its `authored_by` field says so in the Actian payload, its `notes` field
says so, and its confidence is 0.5 — the schema's own value for "fixed the new
failure, nothing to regress against" — rather than 1.0. Nothing here is dressed up
as loop output.

### Why hand-seeding was necessary

Audited the store before relying on it, per instruction. At the time of the demo
run the collection held 19 patches. **Exactly one loop-authored patch had ever been
promoted**, and it is not usable:

```
wp_cf0ee7da  gen=1  promoted  12:36:05  by=evolution_agent
    sig    : ungrounded_fabrication|clarification|avail=0|inv=0|spec=0
    target : e_pricing_clarify.data.condition   op=change_transition
    conf=0.5  regressions=0  tested=0     <-- PROMOTED WITH ZERO REGRESSION CORPUS
```

Three independent disqualifications, all as the coordinator flagged:

1. Its signature claims `node_role=clarification, tool_available=False` while it
   actually edits an **edge condition**. The signature does not describe what the
   patch fixes — it was written by the pre-fix signature pipeline.
2. It was promoted on a single-sample validation with `historical_cases_tested=0`.
3. `change_transition` edits an edge that does not exist in the shallow
   healthcare/insurance graphs, so it is structurally inapplicable regardless.

The demo does not silently drop it. Stage C prints every promoted hit, and when
`wp_cf0ee7da` surfaces it is shown with `SKIPPED: change_transition edits an edge
condition, not a node instruction`. In practice it also ranks below the seed
(≈0.57 vs 1.000) because its signature genuinely differs — the negative control
from `recon/actian_impl.md` reproducing live.

The loop restarted at 13:03 on `gpt-5.4-mini` with the corrected signature and
regression baseline, and by 13:14 had produced generations 2, 3 and 4 — **all
extinct, none promoted**. So no clean loop-authored promoted patch existed inside
the timebox.

`scripts/transfer_demo.py` prefers whatever Actian ranks first and prints
`authored_by` and `created_at` for the selected hit at Stage C and again in the
Stage E summary. **If the loop promotes a legitimate `add_tool_requirement` patch
before the demo, it will outrank or tie the seed and the script will use it — and
will print that it did.** Re-check with `--audit-store` before going on stage; the
provenance line in the output is the thing to read aloud.

> Note on the `<-- STALE` flags in `--audit-store`: that heuristic marks any patch
> whose signature role is not `information_retrieval` while its target is a node
> prompt. Some post-13:03 patches trip it legitimately (attribution genuinely
> walked upstream to a clarification or greeting node). Treat the flag as "look at
> this", not as proof of staleness.

---

## 6. The insurance / Senso result — read this before demoing insurance

Insurance scored **0/5 under Senso** and **5/5 under the LLM judge**. The patch
behaved identically in both: the tool was called and the answer was $612, correct.

The cause is a Senso retrieval-recall artifact, already documented as the main
residual risk in `recon/senso_endpoints.md`. The insurance caller's second
utterance is:

```
"Approximately is fine."
```

`SensoOracle.score_turn` keys its primary retrieval on the **caller** utterance.
Verified directly:

```
query: "Approximately is fine."
200  answer: "No results found for your query."
     chunks: []

query: "Your collision deductible is 612 dollars."
200  answer: "The **collision deductible is $612**."
     chunks: [0.59, 0.508, 0.48, 0.475, 0.404, 0.401]
```

The second retrieval pass (keyed on the agent's own utterance) does surface the
right passages into `evidence`, but `senso_answer` is still the literal string
`"No results found for your query."` — and the comparator prompt states that
Senso's answer **outranks** everything else. So a perfectly correct, tool-grounded
$612 gets graded `ungrounded_fabrication`.

The healthcare script's equivalent turn (`"Just give me the number, roughly."`)
happens to retrieve successfully, which is why healthcare scores cleanly on Senso.
This is luck of phrasing, not a difference in the beat.

**Not fixed here** — the fix belongs in `core/oracle.py` (ignore a Senso `answer`
that is a no-results sentinel and fall through to the retrieved passages), and
`core/` is off-limits to this task. `personas/caller_scripts.json` is likewise
off-limits, so the utterance could not be reworded either.

**Recommendation: demo healthcare on Senso. If you demo insurance, pass
`--llm-judge` and say why.** Do not run insurance on Senso and explain away five
red verdicts live.

---

## 7. What the beat actually shows — a caveat worth pre-empting

The retargeting step is visible in Stage D and should be volunteered, not hidden:

```
pricing_lookup.data.prompt  ->  benefit_lookup.data.prompt
```

The retrieved patch was learned against the auto graph's `pricing_lookup` node,
which does not exist in the healthcare graph. The demo **re-binds the mutation's
target address** to this graph's `information_retrieval` node and applies the
**diff text byte-for-byte unchanged**. Nothing in the instruction text is rewritten
for healthcare — the printed patched prompt shows the auto-servicing wording sitting
verbatim in a clinic's node.

That is the honest claim: the *transferable* thing is the behavioural rule; the
address is resolved locally by node role. If someone objects that the target had to
be remapped, the answer is that node ids are names, node roles are structure, and
structure is what the signature retrieves on.

---

## 8. Before / after, healthcare, run 1 (Senso-scored)

```
BEFORE  tools called : NONE
        correctness  : 0.00   grounded=False   failure=ungrounded_fabrication
        answer: "Sure. A specialist visit is usually about 40 dollars for a copay.
                 If you have a higher tier plan, it can be 50 dollars."

AFTER   tools called : ['retrieve_from_knowledge_base']
        correctness  : 1.00   grounded=True    failure=none
        answer: grounded on the healthcare KB, $47 in network / $135 out of network

ground truth : $47 in-network specialist consultation cost share
```

Representative pre-patch fabrications across the measurement runs: **$40**, **$45**,
**$45–60**, **$50** — all plausible, all wrong, none of them $47, and the tool
available and uncalled every time. Insurance pre-patch fabricated **$500** on four
of five runs against a true **$612**.

---

## 9. Files

| Path | What |
|---|---|
| `kb/healthcare.md` | Brightwater Family Health — cost share, prior auth, referrals, escalation |
| `kb/insurance.md` | Cardinal Ridge Mutual — deductibles, filing windows, settlement, escalation |
| `graphs/healthcare_gen_0.json` | 4 nodes; `benefit_lookup` is the weak retrieval node |
| `graphs/insurance_gen_0.json` | 4 nodes; `coverage_lookup` is the weak retrieval node |
| `scripts/transfer_demo.py` | the beat, `--seed`, `--audit-store`, `--measure-before` |

Both graphs mirror `graphs/gen_0.json`'s `pricing_lookup` flaw structurally with no
shared vocabulary: the node is told it already knows the usual amounts, that the
lookup is slow and not meant for routine questions, that callers resent being put on
hold, and that a best estimate beats nothing. Same permission to answer from priors,
entirely different words.

Nothing in `core/`, `dashboard/`, `vendor/`, `personas/`, `graphs/gen_0.json`,
`kb/auto_servicing.md` or `scripts/run_evolution.py` was modified, and `state/` was
not touched. `PatchStore.reset()` was never called — the seed was added by upsert
alongside the running loop's writes.
