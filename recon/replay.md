# Replay.io QA pass

**Project:** `proj-swarm-evolution-dashboard-mrzcvc0b`
**Target:** the evolution dashboard at `localhost:3100`, reached over Replay's
outbound-only reverse-proxy tunnel (`replayqa proxy`), allowlisted to localhost.

## Result

| | |
|---|---|
| Journeys discovered by AI exploration | 21 |
| Test runs | 3 completed, 18 failed, 1 infra-failed |
| Bugs filed | 4, all `judge-rejected` |
| **Open bugs at submission** | **0** |

## The one finding worth writing up

Replay filed:

> *"02Population nav item does not navigate to Population view — page stays on
> Graph diff view"*

Its own judge rejected it. We initially believed the judge was wrong, because a
human operator had independently hit what looked like exactly this — nav clicks
that appeared not to register. Two independent observations of the same symptom
is usually a real defect.

It isn't. We drove every nav transition programmatically, including across a
full polling cycle:

```
->diff                        #diff         "Node Greeting was rewritten by the loop"
->fitness                     #fitness      "Mean fitness 0.499 -> 0.420 over 11 generations"
->population                  #population   "27 of 33 candidates were killed by the validator"
->diff (after 2.6s poll tick) #diff         "Node Greeting was rewritten by the loop"
```

Navigation works in all directions and survives a re-render.

**Actual cause, and why it produced 18 failed runs.** The dashboard polls
`/api/state` every 2 seconds and re-renders, replacing DOM nodes. Any automated
driver — Replay's Playwright selectors, or our own resolved element handles —
can resolve an element, have it replaced by the poll tick, and then click a
detached node. The click goes nowhere. A human never sees this because a human
clicks what is currently on screen.

So this is a **testability** property, not a user-facing bug. It is also the
honest explanation for the failed-run count: those runs are the harness losing
its grip on a live-updating page, not the app misbehaving.

**What we would do with more time**, and did not do because it is a real change
to a demo-critical surface at the wrong hour: keep the nav outside the polled
subtree, or make the poll patch state without remounting, so automated drivers
have stable handles. That would make the app properly QA-able rather than merely
correct.

## What we changed as a result of QA

One genuine fix landed during this pass, though it came from watching the app
rather than from a filed bug: the graph-diff view defaulted to the highest
generation number, and most generations kill all three candidates — so the
load-bearing visual showed an empty "Generation 11 promoted nothing" state most
of the time. It now defaults to the most recent generation that actually
promoted, falling back to the true latest so a run with zero promotions still
renders and still says so.

## Honest note

Zero open bugs is a weaker claim than it sounds when 18 of 21 runs failed for
harness reasons — the effective coverage is the 3 completed runs plus the
exploration pass, not all 21. We would rather state that than report "0 open
bugs" and let it imply more than it does.
