# Senso API map — what is real, and how we proved it

**STATUS: UNBLOCKED.** Senso is the active scoring oracle. `core.oracle.SensoOracle`
runs against it live and all oracle tests pass.

```
BASE   = https://apiv2.senso.ai/api/v1        <-- NOT sdk.senso.ai
HEADER = X-API-Key: <SENSO_API_KEY>
PREFIX = /org/                                <-- the piece everything hinged on
```

`kb/auto_servicing.md` is already ingested:
`content_id = 70f18cbc-9bf6-4bd9-bbc3-3d1de87c6883`.

---

## Headline: there is no Evaluate API

Senso's marketing describes an "Evaluate" step that scores content against
verified ground truth. **It does not exist as an API.** We established this
before the correct base URL was known, and re-confirmed after. The code path for
it has been removed rather than left as dead scaffolding.

This turned out not to matter, because `POST /org/search` is a *better* oracle
than a scoring endpoint would have been: it returns the grounded answer **and**
the passages it came from, so the verdict arrives with its own citation attached.

---

## Live endpoints (verified HTTP 200)

| Method | Path | Purpose |
|---|---|---|
| POST | `/api/v1/org/search` | retrieval **+ AI-generated grounded answer** — this is the oracle |
| POST | `/api/v1/org/search/context` | same retrieval, raw chunks only, no answer. Cheaper/faster |
| POST | `/api/v1/org/kb/raw` | ingest raw text: `{"title", "text"}` |

**`POST /org/search`**

```jsonc
// request
{"query": "...", "max_results": 6,
 "content_ids": ["70f18cbc-..."],   // scope! see caveat below
 "require_scoped_ids": false}

// response
{"query": "...",
 "answer": "The price of **brake service — front axle, pads and rotors** for a **standard sedan** is **$285**.",
 "results": [{"chunk_text": "...", "score": 0.655, "title": "auto_servicing.md",
              "content_id": "...", "chunk_index": 3, "vector_id": "..."}],
 "total_results": 4, "max_results": 6, "processing_time_ms": 1190}
```

Errors: `401` bad key · `402` insufficient credits (free tier — watch for this
mid-demo) · `404` wrong path/base · `409` conflict · `422` malformed body.

### Caveat that will bite you: always scope `content_ids`

The org (`Celest Labs`, free tier) is a **real shared workspace with unrelated
documents in it**. An unscoped query retrieves other content and poisons the
grounding. `SensoOracle` scopes to `AUTO_SERVICING_CONTENT_ID` by default.

### Caveat two: retrieval recall is the real ceiling

`max_results` above ~6 changes nothing — this KB returns at most 4 chunks per
query, and raising the cap does not widen recall. Measured consequence: for
*"how much is a front brake job"*, Senso never surfaces the section-1 preamble
(*"All prices are out-the-door and include parts, labour, shop supplies…"*), so
an agent that correctly volunteers that detail gets scored as a fabrication.

`SensoOracle` mitigates this with a **second retrieval pass keyed on the agent's
own utterance**, not just the caller's question — you cannot verify an assertion
you never retrieved evidence for. That lifts 4 chunks to 6 and fixes most cases.
It does not fix all of them; the preamble case above still misses. This is a
Senso index limitation, not a judging bug, and it is the main residual source of
false `ungrounded_fabrication` verdicts. If verdicts look harsh, look here first.

---

## The old map (sdk.senso.ai) — wrong host, but the method is worth keeping

Before the correct base was known, we mapped `sdk.senso.ai/api/v1` by abusing the
gateway's ordering: **it authenticates before it routes**, so with a deliberately
invalid key, `401` means the route exists and `404` means it does not. 83 paths ×
{GET, POST, OPTIONS}.

**The trap in that method — and it produced ten false positives.** `GET
/content/{id}`, `/categories/{id}`, `/orgs/{id}`, `/users/{id}` are
path-parameter routes, so *any* `GET /content/<anything>` returns 401. That made
`/content/evaluate`, `/content/score`, `/content/verify`, `/content/grade`,
`/content/groundedness` and five more look real. They are not.

**Only POST is a sound oracle here**, because POST has no wildcard children.
Control, which must be run before trusting any result:

```bash
K="anything_invalid"; B="https://sdk.senso.ai/api/v1"
curl -s -X POST -H "X-API-Key: $K" -H 'Content-Type: application/json' -d '{}' \
  "$B/content/zzzz_not_real_9f3a"    # -> 404  POST has no wildcard: oracle sound
curl -s      -H "X-API-Key: $K" "$B/content/zzzz_not_real_9f3a"
                                     # -> 401  GET DOES wildcard: oracle unsound
```

Under POST, every evaluate-family path 404'd — 40+ names including all twelve
originally suggested (`/evaluate`, `/evaluations`, `/eval`, `/evals`,
`/content/evaluate`, `/conversations/evaluate`, `/diagnostics`, `/analyze`,
`/grade`, `/score`, `/verify`, `/groundedness`) plus `/judge`, `/critique`,
`/citations`, `/grounding`, `/fact-check`, `/hallucination`, `/assess`,
`/quality`, `/audit`, `/scoring`, `/benchmarks`, `/experiments`, and more.

Also corrected: `/topics` does **not** exist (an earlier note claimed it did —
that was the GET wildcard firing).

### OPTIONS carries zero route information

Preflight returns `204` for every path including nonexistent ones, with a static
global header set. No `Allow` header. `/evaluate` (404) and `/search` (real)
return byte-identical headers. Do not use OPTIONS for discovery here.

```
access-control-allow-methods: GET, POST, PUT, DELETE, OPTIONS
access-control-allow-headers: Content-Type, Authorization, X-Request-ID
```

### No spec is exposed

All 404 on both hosts: `/openapi.json`, `/swagger.json`, `/docs/openapi.json`,
`/api/v1/openapi.json`, `/api/openapi.json`, `/openapi.yaml`,
`/.well-known/openapi.json`, `/docs`, `/llms.txt`, `/api-reference`, `/swagger`,
`/redoc`, `/spec`. `docs.senso.ai` is client-side rendered and sign-in gated, so
WebFetch cannot read it either.

---

## How this shapes the oracle

`SensoOracle` divides labour so that Senso is genuinely load-bearing rather than
decorative — this is the honest framing, and the one to use on stage:

- Senso owns the **verified knowledge**. The KB lives in Senso, not our process.
- Senso owns the **retrieval**. `/org/search` decides which passages bear on the
  question. We do not choose the evidence.
- Senso owns the **ground truth**. Its `answer` becomes
  `OracleVerdict.ground_truth_value`; its top chunk becomes `citation`.
- Senso owns the **escalation policy**, fetched from the same KB via
  `/org/search` and cached per process — so editing the KB changes the oracle's
  escalation behaviour with no code change.
- An LLM does **one** step: deciding whether the agent's utterance agrees with
  the answer Senso returned. It never sees the raw KB file, and the prompt tells
  it Senso's answer outranks its own knowledge.
