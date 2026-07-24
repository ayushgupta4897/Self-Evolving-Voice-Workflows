# Dashboard

Presentation-grade read-only view over the evolution loop. Three views, in the
order they should be shown on stage.

## Run it

```bash
cd dashboard
npm install     # once
npm run dev     # http://localhost:3100
```

Port **3100** is hardcoded in `package.json` (`next dev -p 3100`). 3010 is
Dograh, 3000 is assumed busy. `.claude/launch.json` at the repo root has a
`dashboard` config for the preview tooling.

The dashboard is **read-only**. It never writes to `state/`, `graphs/`, or
anything else the loop owns.

## Views

Each is directly linkable — the demo can jump straight to one:

| URL | View |
|---|---|
| `http://localhost:3100/#diff` | **01 Graph diff** — gen 0 vs gen N side by side, mutated node ringed in both, before/after instruction diff with added lines marked, the added rule pulled out at 21px, and the structural retrieval key. |
| `http://localhost:3100/#population` | **02 Population** — every generation, all candidates. Extinct ones dimmed + struck through with `validation.notes` as "cause of death" and the regression count. Survivor's `reflection` shown prominently. `confidence` is labelled **validator pass rate** everywhere, with the derivation printed under it. Lineage via `parent_id`. |
| `http://localhost:3100/#fitness` | **03 Fitness** — `mean_fitness_before`/`after` per generation, plus a failing and a passing call side by side with full oracle verdicts (correctness, grounded, citation, ground truth, failure type, tools invoked, oracle source). |

Raw JSON and read warnings are behind the **raw json** toggle in the nav, never
on the main view.

## Data source — what is real and what is not

`GET /api/state` reads the filesystem on every request (`no-store`), and the
page polls it every 2s.

| Field | Live source | Fallback |
|---|---|---|
| `generations` | `state/gen_*.json` (written by `core/loop.py::_checkpoint`) | `dashboard/fixtures/sample_state.json` |
| `graphBase` | `graphs/gen_0.json` | fixture copy of the same file |
| `graphCurrent` | `state/graph_current.json` | replayed from gen 0 |
| `traces` | `state/traces.json` | fixture — **the loop does not write this today** |

**The SAMPLE DATA badge.** When `state/` has no `gen_*.json`, the page shows a
red pill in the nav *and* a full-width red bar naming the exact directory it
looked in. It is impossible to miss and it disappears on its own the moment
`gen_001.json` lands — no restart, no flag.

Call traces are a separate source with their own badge. `_checkpoint()` only
persists generations, so the failing/passing call panel is fixture-backed and
says so in red on the panel itself. If anyone starts writing an array of
`CallTrace` dicts to `state/traces.json`, that badge goes away automatically and
the panel renders the real calls. No dashboard change needed.

Verified rendering:
- 3 generations (fixture) — all three views, screenshots in `recon/screenshots/`
- 1 generation — the fitness chart switches to before/after-within-generation
  rather than plotting a lone dot
- 0 generations, no fixture — clean "No generations yet" empty state on all
  three views, no crash
- 1 live generation from a real loop run — rendered correctly before the run
  cleared `state/`

## Per-generation graphs are replayed, not read

The loop checkpoints only the *current* graph, so showing gen N-1 vs gen N needs
a replay. `lib/graph.ts::applyMutation` is a direct port of
`core/evolve.py::apply_patch` — same four operators, same append semantics
(`existing.rstrip() + "\n\n" + diff`), same edge-condition path. Replaying every
promoted mutation from `graphs/gen_0.json` reproduces
`state/graph_current.json`. **If `apply_patch` changes, this must change with
it.**

`lib/signature.ts::embeddingText` is likewise a faithful port of
`FailureSignature.to_embedding_text()`. It is on screen because what it *omits*
is the point.

## Design

ElevenLabs kit, dark variant — tokens from
`design-md/elevenlabs/DESIGN.md` + `preview-dark.html`. Inter 300/400/500/700
and Geist Mono, loaded by `<link>` (not `next/font`) so a venue with no network
degrades to the system stack instead of failing the build. Weight 300 for all
display headings, positive letter-spacing on body, pill buttons, 16–20px card
radii, multi-layer sub-0.1-opacity shadows inverted for dark. Contrast is nudged
up from the kit's dark preview because it is projected. Nothing on screen is
below 13px, body text is 16–18px.

The only chromatic values are three status tints (added / removed / extinct)
which carry meaning the achromatic palette cannot. No other brand colour is
introduced.

## Known rough edges

- **Graph legibility is width-dependent.** The React Flow panels are sized for
  a 1920-wide projector; at 1600 the nodes drop to ~14px effective. Present at
  1920 or wider. `lib/graph.ts` compresses the authored coordinate space
  (`COL_SPACING`, `ROW_COMPRESSION`) to make this work at all — the authored
  positions are laid out for Dograh's full-screen canvas.
- **Traces are fixture-only** until something writes `state/traces.json`.
- **`graph_current.json` is not shown directly.** The right-hand graph is the
  replay, so if a mutation ever lands outside the four operators the panels
  would drift from the file. The raw JSON drawer shows what was actually read.
- The mutated node is keyed off the promoted patch's `mutation.target`, not
  `selected: true`. That is deliberate — `selected` is only set on the Dograh
  push path (`push_to_dograh`), so an offline batch run would have no highlight
  if we relied on it.
- Adding/removing *nodes* is outside the mutation operator set, so the two
  graph panels always have identical topology. If that ever changes, the diff
  view will show it but nothing highlights added nodes as added.
- A generation where every candidate died renders a "promoted nothing" state in
  the diff view rather than a diff. That is correct, and the population board
  still shows all three corpses.
