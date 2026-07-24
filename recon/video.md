# Demo video — what was filmed, what it says, what it does not

Deliverables: `video/demo.mp4`, `video/script.md`, `video/voiceover.mp3`,
`video/assets/*`. Build is reproducible: `video/shots.py`, `video/shot_dograh.py`,
`video/cards.py`, `video/diagram.py`, `video/build.py`.

Nothing in `core/`, `state/`, `graphs/`, `kb/`, `personas/`, `dashboard/` or `vendor/`
was modified. Everything created lives under `video/` plus this file.

---

## 1. Voice

| | |
|---|---|
| Provider | ElevenLabs, `eleven_multilingual_v2` |
| **voice_id** | **`8WqHCYyrnUqoK70Px5EJ`** |
| Voice name | *Nitin — Indian Accent and Professional voice — By Omkar* |
| labels.accent | `indian` (already present in the account; no shared-library add needed) |
| Settings | stability 0.45, similarity_boost 0.75, style 0.15, **speed 0.98** |
| Endpoint | `/v1/text-to-speech/{id}/with-timestamps` |

The with-timestamps variant was used deliberately: `video/vo/alignment.json` holds
character-level start times, and **every cut in the film is scheduled off those
timestamps**, so shots land on the narration beat rather than near it.

Voiceover is 158.6s. With a 3s title card and a 4.4s tail the film is **166s (2:46)**.

## 2. The story is not the one in the original brief — the brief was wrong

The brief described the triggering failure as a *2019 Honda Accord, ground truth $285,
gen-0 answering $500 / $350 / $340*. That is the README's account of an earlier run. The
generation-2 promotion the film is about (`wp_d46a3166`) was triggered by a **different
call**, and I narrated the real one:

```
state/traces.json  →  call_1784925735456   persona p_brake_price_suv
turn 1  node pricing_lookup  role information_retrieval
caller : "Hey, I've got a 2021 Toyota Highlander, it's an SUV. What do front brakes run?"
         "And that's pads and rotors both?"
agent  : "...On a 2021 Toyota Highlander, front brakes usually run right around
          550 to 750 dollars..."
verdict: correctness 0.10 · grounded false · ungrounded_fabrication · source=senso
tools  : retrieve_from_knowledge_base AVAILABLE, tools_called = []
ground truth (kb/auto_servicing.md): SUV / light truck front axle, pads+rotors = $340
```

Same thesis, verifiable end to end. `$285` is the standard-sedan price in the same KB
table; the caller here was an SUV, so the film says **$340**. I could not find `$500`,
`$350` or `"$200–250"` anywhere in `state/traces.json` or `logs_evolution.txt`, so those
figures are **not spoken and not shown**.

The brief also said the validator "killed 2 of 3" in that generation. It did not —
generation 2 is the one generation with **zero** kills (1 promoted, 2 viable). The film
says the true thing instead: **27 of 33 candidates killed across 11 generations**, which
is also the dashboard's own headline.

## 3. Sponsor framing

Per the direction change, every sponsor is introduced by the requirement it answers, in
the narration, in this order — never "we integrated X":

| Requirement stated first | Then named |
|---|---|
| "an agent has to know it was wrong — verifiably, against something you can cite" | **Senso** (+ the escalation policy lives in the KB, so editing the KB changes transfer behaviour with no code change) |
| "adaptive inference only means anything if the failures are your own" | **Pioneer** |
| "a gate that decides what ships should be auditable, and ideally not alone" | **Guild** |
| "you cannot evolve what you cannot read, write, publish and version" | **Dograh** |
| "a patch is only worth keeping if it can find the next failure that looks like it — structurally, not topically" | **Actian** |

Replay.io is **not narrated**. It could not be introduced by a requirement without
sounding bolted on, so per instruction it was cut from the script rather than shoehorned
in. It still appears in the closing credit line alongside the others — a credit, not a
claim.

The Guild beat is verified in `recon/guild_impl.md` §5: candidate `wp_b4da9382` from
`state/gen_001.json`; our local validator killed it on 4 regressions; the hosted Guild
validator (Gemini 3.5 Flash, never executes the graph) independently returned `reject`
and named `p_discount_hunt` as the persona that would break. Trace:
`state/guild_trace_gen001.jsonl`.

## 4. Shot list

24 shots, 0.5s crossfade between every one, continuous Ken Burns on all of them.

**Real product footage (verbatim, never fabricated):**

| Asset | What it is |
|---|---|
| `01_diff_gen2_top.png` | dashboard `#diff`, **generation 2 pinned** — gen 0 vs gen 2 graphs, `Pricing Lookup` ringed in both |
| `01_diff_gen2_lower.png` | the same view scrolled: `ADD TOOL REQUIREMENT · pricing_lookup.data.prompt`, 3 added lines, plus the structural retrieval key panel |
| `02_population_top.png` | population board headline **"27 of 33 candidates were killed by the validator"**, generation 11 all-extinct |
| `02_population_gen2.png` | generation 2 — `PROMOTED wp_d46a3166` next to its two surviving-but-unpromoted siblings |
| `04_dograh_workflow.png` | Dograh at `localhost:3010/workflow/1`, **v4 (Published)**, `Pricing Lookup` selected |
| `04c_dograh_node.png` | same, zoomed on the `#pricing_lookup` agent node with `auto_servicing.md` attached |

All captured at a true 1920×1080 viewport via Playwright driving the user's installed
Chrome. **The dashboard showed `live · 11 gens` with no SAMPLE DATA banner in every
frame used.**

**Diagram (`d1..d6_flow.png`)** — an architecture flow that builds up in six stages on a
fixed layout, so crossfading between stages reads as elements arriving. Authored as
HTML, screenshotted, animated by ffmpeg — not generated by a model.

**Typographic cards** (`c*`, `n*`) — each carries a monospace source line naming the file
its content came from. Content is verbatim: `c03_selection.png` is the literal
`logs_evolution.txt` promotion/kill block; `c05_signature.png` and `c06_transfer.png` are
literal `scripts/transfer_demo.py` stdout.

**Higgsfield** produced the opening title card only
(`generate_image`, `nano_banana_pro`, job `df43f1e5-863f-4a8b-a892-9425f58aac25`,
`assets/hf_title.png`). It was not allowed anywhere near product footage.

## 5. Audio

- Voiceover normalised with `loudnorm=I=-16:TP=-1.5:LRA=11`.
- Ambient bed is **synthesised in ffmpeg** (55 Hz + 82 Hz drones + brown noise, lowpassed
  at 520 Hz, at `volume=0.05` ≈ 26 dB under the voice, 5s fade in / 6s fade out).
  Higgsfield's `generate_audio` explicitly refuses standalone music and SFX, so it was
  not used for this.
- Final mix through `alimiter` so nothing clips.

## 6. Deliberately not claimed

- **Pioneer's fine-tune is not claimed to have finished.** The narration says only that
  Pioneer serves the calls, so the traces are the agent's own.
- **Senso is never called an "Evaluate API".** It is described as verified knowledge, a
  grounded answer and a verbatim citation. The comparison is ours.
- The transfer patch shown is `wp_d46a3166`, `authored_by=evolution_agent` — the
  loop-authored one. The hand-seeded `wp_seed_auto_tool` described in `recon/transfer.md`
  §5 is **no longer what the demo retrieves**; I re-ran the beat at 14:25 and Actian
  returned the loop-authored patch at 1.000000 with healthcare excluded. Full stdout is
  in `video/assets/transfer_demo_raw.txt`.

## 7. Could not / did not capture

| Thing | Why |
|---|---|
| Dashboard **03 Fitness** before/after call panel | It renders a trace whose oracle label reads **"Senso Evaluate"** — a surface the README states does not exist — and its failing-call example shows a *correct* `$285` answer scored 0.00 because Senso returned no results for that query. Filming it would have put a false product name and a confusing verdict on screen. Cut. The fitness *chart* was captured (`03_fitness_chart.png`) but is not in the final cut for time. |
| Guild UI / trace screenshot | The Guild verdict is narrated and carried by a source-attributed card (`n5_guild.png`); no Guild web UI was captured — there was not time to authenticate and film it. |
| Live voice call audio in Dograh | Not attempted; the film uses the graph UI and the recorded traces instead. |
| `$500 / $350 / "$200–250"` gen-0 fabrications from the README | Not present in any on-disk artifact I could find. Omitted rather than asserted. |
