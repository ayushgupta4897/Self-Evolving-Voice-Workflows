# Actian vector store — implementation notes

**STATUS: GREEN.** `core/patch_store.py` is done and `tests/test_patch_store.py` runs 30/30 green against the live `vectorai` container, twice in a row, including a real `docker restart` mid-test.

**The two tests that mattered both pass:**
- **Test 2 (transfer property): PASS** — a patch stored under `origin_vertical="auto_servicing"` is retrieved at cosine **1.000000** by a *healthcare* failure signature with `exclude_vertical="healthcare"`. The signature embedding is not contaminated by domain content.
- **Test 3 (negative control): PASS** — matching signature scores **1.0000**, a genuinely different signature scores **0.5724**. Gap **0.4276**. The reverse query flips the ranking, so this is a real similarity search and not a fixed order.

---

## 1. How to use it

```python
from core.patch_store import PatchStore

store = PatchStore()            # localhost, 6573 REST / 6574 gRPC, "workflow_patches"
store.ensure_ready()            # ALWAYS call this first. Idempotent. Restart-proof.

point_id = store.store(patch)                     # WorkflowPatch -> point id

hits = store.retrieve(signature,                  # FailureSignature
                      limit=3,
                      promoted_only=True,
                      exclude_vertical="healthcare")

board = store.all_patches()     # everything, extinct included, ordered as a lineage
n     = store.count()
```

**Return shape of `retrieve()` / `all_patches()`:** flat payload dicts — exactly what `WorkflowPatch.to_actian_payload()` produced — plus two underscore-prefixed extras so they can never collide with a payload key:

```python
{"patch_id": "wp_5b032898", "diff": "...", "origin_vertical": "auto_servicing", ...,
 "_score": 1.0,                                  # cosine similarity, 1.0 = identical
 "_point_id": "400698bc-33a8-5e25-9aa8-2d2a3a1144cd"}
```

So downstream code reads `hit["diff"]` directly, and `hit["_score"]` for the confidence display.

Extras beyond the required API: `store_many()` (batch, one embedder pass), `reset()` (drop + recreate — fixtures and demo resets only), `close()`, `wait_for_server()`, and `PatchStore` works as a context manager.

### Environment

Interpreter is **`.venv/bin/python`** (CPython 3.12.11, created with `uv`). It has `actian-vectorai-client==1.0.2` and `sentence-transformers==5.6.1`. Run anything against it, not the system `python3` (which is 3.14 and has neither):

```bash
.venv/bin/python tests/test_patch_store.py
```

Repo root must be on `sys.path` for `from core.patch_store import PatchStore` (the test file inserts it; `core/` has no `__init__.py` — namespace packages handle it).

---

## 2. Embedder: sentence-transformers `all-MiniLM-L6-v2`

**Chosen because it installed fast and there is no OpenAI key on this machine.**

- `uv pip install sentence-transformers` (pulling torch 2.13.0) took **81 seconds** — nowhere near the 5-minute timebox. First model load downloads ~90 MB from HF and takes ~80 s; every load after that is cached and takes ~2 s.
- 384 dims native, so it matches the collection's `vectors.size=384` with no truncation, and it returns L2-normalised vectors (verified: `sum(x*x) == 1.0`), which is what Cosine wants.
- The OpenAI fallback was **not usable**: there is no `OPENAI_API_KEY` in the environment and no `.env.local` / `.env` anywhere in the repo (only `vendor/dograh/.env`, which does not contain one). If a key appears later, the fallback is already wired and needs no code change.

Everything sits behind a module-level `embed(text) -> list[float]`. Backend selection order is `sentence-transformers` → `openai` (text-embedding-3-small, `dimensions=384`) → `hash`, overridable with `PATCH_STORE_EMBEDDER=openai`. `embedder_name()` reports which is live — put it on the dashboard, it is a fair thing to be asked about.

The `hash` tier is a deliberate last resort and it **prints a warning to stderr**: it is exact-match only, not semantic, so the transfer beat would be a lookup table rather than a retrieval. It exists so the pipeline still runs offline, not so the demo can quietly degrade onto it. Keys are read from `.env.local`/`.env` at call time; nothing is hardcoded.

---

## 3. What the recon doc didn't cover (all verified live)

1. **Point ids must be an unsigned int or a UUID.** `patch_id` is `wp_<hex8>`, which is neither. `store()` maps it through `uuid.uuid5(NS, patch_id)`, which makes the write a true idempotent upsert — re-storing a patch after validation flips it from `candidate` to `promoted` in place instead of duplicating it. String UUIDs are accepted by the server; confirmed round-tripping.
2. **`FieldCondition` cannot go straight into a `Filter`.** It must be wrapped: `Filter(must=[Condition(field=FieldCondition(key=..., match=Match(keyword=...)))])`. Passing a bare `FieldCondition` raises a pydantic `model_type` error.
3. **`Filter`'s list fields reject `None`.** `Filter(must=[...], must_not=None)` raises `list_type`. Build the kwargs dict and pass only the clauses you have.
4. **`must_not` works** and is what `exclude_vertical` uses — verified it excludes by `origin_vertical` while still returning the other points. Bool matches need `Match(boolean=True)`, not `Match(keyword="true")`; ints need `Match(integer=...)`. `_match_for()` dispatches on type (bool before int — `bool` is an `int` subclass).
5. **No payload index needed.** Pre-filtering on `status` / `origin_vertical` worked with no `create_payload_index` call.
6. **`scroll()` returns `(points, next_offset)`** and gives back `None` as the offset when exhausted. `all_patches()` pages at 256.
7. **The gRPC channel does not survive `docker restart`.** This is a second-order version of the recon gotcha: even after the collection is re-opened, a `PatchStore` constructed *before* the restart holds a dead channel. `ensure_ready()` catches the failure, drops and rebuilds the client, and retries once. It matters because the demo runs in one long-lived process — a fresh process would have hidden this.
8. **The `/open` gotcha is real and reproduced every single run.** Test 4 asserts it before asserting the fix: a raw `points.count()` on the restarted container returns `CollectionNotFoundError: ... code=404` until `POST /collections/{name}/open` fires. `ensure_ready()` also *verifies* — it does a `count()` probe after opening and re-opens once if that probe fails, so it never returns a false ready.

Cosmetic only: gRPC logs `Other threads are currently calling into gRPC, skipping fork() handlers` on stderr when the test shells out to `docker`. Harmless.

---

## 4. Test output (verbatim, exit 0)

```
==========================================================================
SETUP
==========================================================================
  embedder: sentence-transformers
  collection 'workflow_patches_test' ready, count=0

==========================================================================
TEST 1 — ROUND TRIP
==========================================================================
  stored patch_id=wp_829483bf point_id=400698bc-33a8-5e25-9aa8-2d2a3a1144cd
  PASS  retrieve returned 1 hit(s)
  PASS  top hit is the stored patch (wp_829483bf)
  PASS  point id round-tripped
  PASS  self-similarity 1.000000 > 0.99
  PASS  payload matches EXACTLY (dict compare, key order ignored)
      21 payload fields identical; bools stayed bools (tool_available=True), floats stayed floats (confidence=1.0)
  PASS  count() = 1

==========================================================================
TEST 2 — THE TRANSFER PROPERTY  (the demo lives or dies here)
==========================================================================
  stored ONLY auto patch wp_5b032898 (origin_vertical=auto_servicing)
  healthcare patch wp_6c325f51 was NEVER stored
  PASS  signatures are structurally identical
  PASS  to_embedding_text() is byte-identical across verticals
      embedding text: 'A information_retrieval node produced a ungrounded_fabrication failure. A retrieval tool was available and was not invoked. The agent asserted a specific factual value.'
  PASS  no domain vocabulary in signature text (leaked: none)
  retrieve(healthcare_signature, exclude_vertical='healthcare') -> 1 hit(s)
      score=1.000000  wp_5b032898  origin=auto_servicing  op=add_tool_requirement
  PASS  a patch was retrieved despite excluding healthcare
  PASS  retrieved patch was LEARNED ON AUTO SERVICING (origin=auto_servicing)
  PASS  it is the exact auto patch we stored
  PASS  similarity 1.000000 > 0.99 (high)
  PASS  the returned diff is auto-domain content, applied to a healthcare failure
  PASS  no healthcare content in the hit (it could not have been learned in-domain)

==========================================================================
TEST 3 — NEGATIVE CONTROL  (is retrieval discriminating at all?)
==========================================================================
  stored matching-signature patch  wp_54d0f468  (ungrounded_fabrication|information_retrieval|avail=1|inv=0|spec=1)
  stored different-signature patch wp_957109b1  (premature_escalation|escalation|avail=1|inv=1|spec=0)
      #1  score=1.000000  wp_54d0f468
      #2  score=0.572427  wp_957109b1
  PASS  both patches retrievable
  PASS  matching signature ranks ABOVE different signature (#1 vs #2)
  PASS  score gap 0.4276 > 0.05 (matching 1.0000 vs other 0.5724)
  PASS  reverse query ranks the escalation patch first (top=wp_957109b1)

==========================================================================
TEST 3b — STATUS FILTER AND POPULATION BOARD
==========================================================================
  stored EXTINCT patch wp_2ef0c3bd
  PASS  extinct patch is NOT returned by promoted_only=True
  PASS  extinct patch IS returned by promoted_only=False
  PASS  all_patches() includes the extinct patch (3 total, statuses=['extinct', 'promoted'])
  PASS  all_patches() is ordered by generation then time (reads as a lineage)
  PASS  count() == len(all_patches()) == 3

==========================================================================
TEST 4 — RESTART RESILIENCE  (the /open gotcha)
==========================================================================
  3 point(s) before restart; stored wp_1358f705
  $ docker restart vectorai
      restarted in 2.3s
      (gotcha confirmed: raw count() before open -> CollectionNotFoundError: Collection 'workflow_patches_test' does not exist | code=404 (NOT_FOUND))
  PASS  ensure_ready() completed on a freshly restarted container
  PASS  retrieve() works after restart (3 hit(s))
  PASS  the patch stored before the restart survived and is retrievable
  PASS  count() = 4 (was 3 + new stores)
  PASS  the pre-existing store instance recovered its stale channel via ensure_ready()

==========================================================================
RESULT
==========================================================================
  ALL CHECKS PASSED

  demo collection 'workflow_patches' left open, count=0
```

---

## 5. Reading the transfer result honestly

Similarity is **exactly 1.000000**, not merely "high". That is the correct and expected number, and it is worth being able to explain it on stage rather than being caught by it:

`FailureSignature.to_embedding_text()` is a pure function of five structural fields. The healthcare failure and the auto-servicing failure have the same five values, so they produce **byte-identical text**, so they produce the identical vector. Test 2 asserts that byte-identity explicitly. The retrieval is a genuine ANN search over a real HNSW index — it just happens that an exact structural match is an exact vector match.

The embedding is doing real work at the *margins*, which is what test 3 measures: 0.5724 for a different signature, and near-neighbour signatures (same failure type, differing only in whether a tool existed) land in between rather than at 0 or 1. That is the property that makes this retrieval rather than a dictionary lookup on `signature.key()` — and if anyone asks why we don't just use `signature.key()` as a dict key, the answer is that a dict would return nothing for a signature it had never seen exactly, whereas this returns the nearest structural relative.

**What test 2 does not prove:** that the retrieved auto-servicing patch is *useful* for the healthcare failure. It proves the store hands back the right patch across a vertical boundary. Whether applying that diff actually fixes the healthcare call is the Validator's claim, not the vector store's.

---

## 6. State left behind

- Demo collection **`workflow_patches` is empty and open** (`count=0`). The recon doc's three scratch points `wp_001/002/003` and a probe point were cleared so the pipeline starts from a clean population board. If you wanted that scratch data, re-run the recon snippet.
- Test collection **`workflow_patches_test`** exists and holds the last run's fixtures. It is `reset()` at the start of each test phase; harmless to leave.
- **`.venv/` was created at the repo root** — shared, add to `.gitignore` if anyone is committing.
- The container was restarted twice by the tests. Anything else holding a gRPC channel to `vectorai` from before that needs to reconnect; `ensure_ready()` does it automatically.
