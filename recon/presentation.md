# Presentation site — deployment

**Live URL: <https://swarm-evolution.vercel.app>**

Production alias, publicly accessible (HTTP 200, no Vercel deployment protection,
no login wall). Verified from outside the build with `curl`.

| | |
|---|---|
| Vercel project | `swarm-evolution` (scope `ayushgupta4897s-projects`) |
| Latest production deployment | `swarm-evolution-rfko0jcuf-ayushgupta4897s-projects.vercel.app` |
| Inspector | <https://vercel.com/ayushgupta4897s-projects/swarm-evolution> |
| Source | `presentation/` |
| Stack | single static `index.html`, no build step, no framework |

## What is deployed

A three-page scroll presentation, one section per page, sticky nav with
`01 / 02 / 03` pills.

1. **The problem.** The human fix loop as four steps. The Highlander failure
   verbatim — caller question, the gen-0 agent's "right around 550 to 750
   dollars", the verified $340 (SUV front axle, pads and rotors). Senso's verdict
   as chips: correctness 0.10, grounded false, `ungrounded_fabrication`, retrieval
   tool available, tool never invoked, node `pricing_lookup`. Thesis pull-quote —
   RAG fixes retrieval, we fix the instruction that failed to invoke retrieval.
   Scale framing: a fluent wrong answer is undetectable by ear.
2. **What we built.** The autonomous loop as an inline SVG, 8 nodes, serpentine
   with a green loop-closing return edge labelled "next call runs the evolved
   graph". Then 11 generations / 0 errored / 33 candidates / 27 killed / 3
   promoted / 10 of 11 generations eliminated something. The promoted patch
   `wp_d46a3166` with its diff text and `authored_by=evolution_agent`. The
   transfer beat — cosine 1.000, $40 → $47, Jaccard 0.176. The four-operator
   limitation. Link to the demo video.
3. **Why each product.** Six requirement-first cards. Every card leads with the
   requirement in quotes, then the product, then the concrete benefit. The phrase
   "we integrated" appears nowhere on the page (grep-verified). Includes the Senso
   escalation-policy bonus, Pioneer's unfinished ~6h fine-tune, Actian's
   signature-only embedding, Dograh's publish-not-optional detail, Guild's
   independent `reject` on `wp_b4da9382` from a different model family, and
   Replay's honest 18-of-21 harness-failure caveat. Ends with the full
   "real vs. warm-started" block from the README.

Every figure on the page traces to `README.md`, `recon/transfer.md`,
`recon/replay.md`, `recon/guild_impl.md`, `recon/senso_endpoints.md` or
`video/script.md`. Nothing was invented.

## Design

Dark ElevenLabs kit, tokens copied verbatim from `dashboard/app/globals.css` —
same `--canvas #0a0a0a`, `--surface #121212`, `--ink #f0f0f0`, `--add #8fd9a8`,
`--extinct #e07a7a`, same multi-layer sub-0.1-opacity shadow stack, same
`.diff-add` treatment, same pill and card radii, Inter + Geist Mono. Display type
is weight 300 throughout, per the kit's non-negotiable. It reads as the same
product as the dashboard.

Responsive down to mobile (grids collapse, transcript rows stack, nav labels drop
to numerals). The SVG diagram scrolls horizontally inside its own container rather
than shrinking below legibility.

## Assets

Copied into `presentation/assets/`:

| File | Source |
|---|---|
| `dashboard_population.png` | `recon/screenshots/` |
| `dashboard_diff.png` | `recon/screenshots/` |
| `dashboard_fitness.png` | `recon/screenshots/` (copied, not currently placed) |
| `dograh_v4.png` | `video/assets/04_dograh_workflow.png` |
| `demo.mp4` | `video/demo.mp4`, 42 MB |

The video is **linked, never embedded** — a pill CTA on page 2 opening
`/assets/demo.mp4` in a new tab. It is not in the critical render path and costs
nothing on page load. Images are `loading="lazy"`; `/assets/*` is served with a
one-year immutable cache header via `presentation/vercel.json`.

## The one bug found and fixed during verification

The SVG diagram animates in on scroll via `IntersectionObserver`. The page
degrades correctly without JS (no JS → the `js-anim` class is never added → every
node is visible), but if JS ran and the observer somehow never fired, the
load-bearing visual on page 2 would stay blank. Added a 2.5s `setTimeout` safety
net that reveals the diagram unconditionally. Also honours
`prefers-reduced-motion`.

Verified live in-browser that the reveal fires: the diagram element carries class
`card diagram js-anim in` and its `.step` children compute to `opacity: 1` at
1104×404.

## Not done

- **Dashboard static snapshot.** Not attempted. It was explicitly a nice-to-have
  contingent on spare time, and the only clean way to bake `state/*.json` into a
  build would have meant touching `dashboard/`, which is on the do-not-modify
  list. The presentation embeds dashboard screenshots instead, so the population
  board and graph diff are still on screen for judges.
- **Custom domain.** Left on the `.vercel.app` alias.
- **Insurance transfer numbers** ($612 / LLM-judge) are deliberately off the deck.
  The Senso insurance result is a retrieval-recall artifact documented in
  `recon/transfer.md` §6, and putting a 0/5-under-Senso figure on a slide invites
  a question whose honest answer needs two minutes. Healthcare carries the beat.
- **Provenance nuance on the transfer patch.** The deck states the transfer using
  `wp_d46a3166` (loop-authored), consistent with `video/script.md` §6 and the
  14:25 run in `video/assets/transfer_demo_raw.txt`. `recon/transfer.md` §5
  documents an earlier run where a hand-seeded patch (`wp_seed_auto_tool`) was the
  retrieved hit. Re-check with `transfer_demo.py --audit-store` before going on
  stage and read the `authored_by` line aloud — the deck's claim is only correct
  for the loop-authored hit.

## Redeploying

```bash
cd presentation && npx vercel deploy --prod --yes
```

Vercel CLI is already authenticated as `ayushgupta4897`. No `VERCEL_TOKEN` is set
or needed; credentials were established via the CLI device-login flow.
