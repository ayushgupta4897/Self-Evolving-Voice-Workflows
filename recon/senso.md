# Senso Recon

**VERDICT: BLOCKED** — Base URL and auth header are confirmed correct, but the provided API key `tgr_REDACTED_DEAD_KEY` is rejected as `{"status":401,"message":"Invalid API key"}` on EVERY authenticated endpoint, so no ingest/evaluate/search call could be proven. We need a valid key before the project's correctness oracle can be used.

---

## What is confirmed (verified by live HTTP)

- **Base URL: `https://sdk.senso.ai/api/v1`** (the developer SDK gateway — a Cloudflare-fronted Go service). This is NOT `api.senso.ai` (that host is a separate Django app for the web app/session-auth) and NOT `apiv2.senso.ai` (a different GEO/AI-visibility product).
- **Auth header: `X-API-Key: <key>`** (verified: this header name produces the clean SDK error `{"status":401,"message":"Invalid API key"}`, whereas wrong hosts/headers give generic Django/Go 404s). `Authorization: Bearer` and raw `Authorization` are NOT used by the SDK.
- **Content type: `application/json`** for JSON bodies; `multipart/form-data` only for file upload (`/content/file`).

### The blocker, precisely
The key we were given is invalid. Reproduce:
```bash
curl -sS -w '\nHTTP %{http_code}\n' \
  -H "X-API-Key: tgr_REDACTED_DEAD_KEY" \
  https://sdk.senso.ai/api/v1/categories
# -> {"status":401,"message":"Invalid API key"}
# HTTP 401
```
Every scheme was tried (`X-API-Key`, `x-api-key`, `Authorization: Bearer`, `Authorization: <raw>`, `Api-Key`, `Token`, `X-Senso-Key`, ...) against `categories`, `content/raw`, `search`, `generate`, `orgs/me`, `users/me` — all return 401 Invalid API key.

**Likely cause:** Senso's documented keys carry an environment infix — `tgr_live_...` (production) and `tgr_test_...` (test). Our key is `tgr_<blob>_fc` with **no `live`/`test` segment**, so it is malformed, revoked, or from an incompatible environment. **Action needed: get a fresh `tgr_live_...` or `tgr_test_...` key from the Senso dashboard / their team (they hand these out, sometimes via email).**

---

## Endpoint surface (discovered, but UNVERIFIED for 200 because the key is invalid)

The Go gateway returns **`401 Invalid API key` for real paths** (auth runs before routing/body checks) and **`404 page not found` for non-existent paths**. Using that oracle I confirmed these paths EXIST:

| Method | Path | Purpose | Proof |
|---|---|---|---|
| POST | `/api/v1/content/raw` | ingest raw text | returns 401 (real), not 404 |
| POST | `/api/v1/categories` | create category (KB grouping) | returns 401 (real) |
| POST | `/api/v1/search` | semantic search/query over KB | returns 401 (real) |
| POST | `/api/v1/generate` | RAG generate answer from KB | returns 401 (real) |
| GET  | `/api/v1/categories`, `/content`, `/content/file`, `/topics` | list/read | returns 401 (real) |

Paths that 404'd (so NOT the route): `content`, `content/raw/text`, `content/text` (as POST), `evaluate`, `content/evaluate`, `content/search`, `content/generate`, `evaluate/conversation`, `diagnostics`, `evals`.

**Evaluate endpoint: NOT located.** None of the guessed evaluate/diagnostics paths under `sdk.senso.ai/api/v1` resolved (all 404). The docs describe an "Evaluate" step that "scores content against verified ground truth and checks citation accuracy" and "runs conversation-level diagnostics to surface missing information," but the exact path/shape is behind the sign-in-gated docs (`docs.senso.ai/api-reference/*`, all 307 -> sign-in; `llms.txt` gated; `openapi.json`/Mintlify S3 spec return 403/404). **This must be resolved once a valid key + docs access exist — it is the single field our fitness function keys on and I could not confirm its request/response shape.**

### Corroborating source notes (from docs/search index, treat as UNVERIFIED claims, not proven)
- Docs state the SDK exposes **ingest, search (query), generate** endpoints plus an **Evaluate** step to "score your content and flag gaps."
- File ingest is `/content/file` (multipart). Raw text is `/content/raw` (JSON).
- There is a separate `apiv2.senso.ai/evaluate` that takes `{query, brand, network}` — this is the **GEO / AI-visibility product** (share-of-voice across ChatGPT/Gemini/Perplexity), **NOT** the KB-grounded correctness oracle we want. Do not confuse the two.

---

## Copy-pasteable curls (ready to run the moment a valid key exists)

Auth is confirmed; only the key value is bad. Swap `$KEY` for a valid `tgr_live_...`/`tgr_test_...`.

```bash
KEY="tgr_live_REPLACE_ME"
B="https://sdk.senso.ai/api/v1"

# --- whoami / smoke test (any GET that isn't 401 = key is valid) ---
curl -sS -w '\nHTTP %{http_code}\n' -H "X-API-Key: $KEY" "$B/categories"

# --- create a category (KB grouping) ---   [BODY SHAPE UNVERIFIED - guess]
curl -sS -w '\nHTTP %{http_code}\n' -X POST "$B/categories" \
  -H "X-API-Key: $KEY" -H "Content-Type: application/json" \
  -d '{"name":"auto-service-pricing"}'

# --- ingest raw text ---                    [BODY SHAPE UNVERIFIED - guess]
curl -sS -w '\nHTTP %{http_code}\n' -X POST "$B/content/raw" \
  -H "X-API-Key: $KEY" -H "Content-Type: application/json" \
  -d '{"title":"Service Pricing","text":"Brake service is $285. Oil change is $70.","categories":["<category_id>"]}'

# --- check ingestion status ---             [PATH UNVERIFIED: likely GET /content/{id}]
curl -sS -w '\nHTTP %{http_code}\n' -H "X-API-Key: $KEY" "$B/content/<content_id>"

# --- search / query the KB ---              [BODY SHAPE UNVERIFIED - guess]
curl -sS -w '\nHTTP %{http_code}\n' -X POST "$B/search" \
  -H "X-API-Key: $KEY" -H "Content-Type: application/json" \
  -d '{"query":"how much is a brake service?","max_results":5}'

# --- generate (RAG answer) ---              [BODY SHAPE UNVERIFIED - guess]
curl -sS -w '\nHTTP %{http_code}\n' -X POST "$B/generate" \
  -H "X-API-Key: $KEY" -H "Content-Type: application/json" \
  -d '{"query":"how much is a brake service?"}'

# --- EVALUATE: PATH UNKNOWN. Not found under /api/v1. Get exact path from docs.senso.ai
#     (sign-in required) once you have a valid key. This is the critical unknown.
```

---

## The things the caller specifically asked for, and their status

- **Correctness score field path** — UNKNOWN (evaluate endpoint not reachable/located).
- **Grounded boolean field path** — UNKNOWN.
- **Citation field path** — UNKNOWN.
- **Missing-info / failure reason string** — UNKNOWN (docs claim evaluate "surfaces missing information," but shape unconfirmed).
- **Wrong-answer vs right-answer eval JSON, side by side** — COULD NOT PRODUCE. Both require (a) a valid key and (b) the evaluate endpoint path, neither of which we have.
- **Ingestion latency / async behavior** — UNKNOWN (could not ingest). Docs imply ingest is compiled "automatically" into the KB; async vs sync unconfirmed.
- **Rate limits / quotas** — Cloudflare in front of `sdk.senso.ai`. After ~40 rapid requests, GETs briefly returned `403` (transient CF throttle; recovered to normal `401` on retry). No `X-RateLimit-*` headers exposed. Expect Cloudflare-level throttling under burst; add backoff.

---

## Recommended immediate unblock path (for a 6-hour build)
1. **Get a valid key NOW** — a `tgr_live_...` or `tgr_test_...` from the Senso dashboard or their team. This is the whole blocker.
2. With that key, sign into `docs.senso.ai` and grab the exact **Evaluate** endpoint path + request/response schema (and confirm `content/raw`, `search`, `generate` body shapes). Budget: ~15 min.
3. Re-run the curls above; the auth layer is already proven, so a valid key should immediately return 200s on `categories`/`content/raw`.
4. If Senso's own `evaluate` proves flaky or absent, we have a fallback: `POST /search` (retrieve grounded snippets) + our own scoring, since `search`/`generate` are confirmed real endpoints.
