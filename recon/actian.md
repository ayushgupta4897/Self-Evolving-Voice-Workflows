# Actian VectorAI DB Community Edition — recon

**VERDICT: `GO-WITH-CAVEATS`** — native arm64 image, no auth, no EULA prompt, Qdrant-shaped API that worked first try; the one real trap is that collections do **not** auto-load after a container restart and you must `POST /collections/{name}/open` before any read/write or everything fails with `Collection not found`.

Verified on: macOS darwin/arm64 (Apple Silicon), Docker 28.3.2, image `actian/vectorai:latest` @ `sha256:d46d0891155b815e9ba30796d2bb6ac86a1c45b5807c21284b228991622f67a1`, server `Actian VectorAI DB 1.0.2 / VDE 1.0.2`.

---

## 1. Image facts

| Item | Value |
|---|---|
| Architecture | `arm64/linux` — **native, no Rosetta** |
| Multi-arch | Yes: manifest list ships `arm64` + `amd64` |
| Size on disk | **2.06 GB** |
| Entrypoint | `/usr/local/actian-vectorai/install/start-services.sh` |
| Runs as | non-root user `actian-vectorai` |

Pull takes a few minutes on hotel wifi — do it early.

## 2. Working `docker run` (corrected for macOS)

The sponsor's `-v ./local_data:...` relative bind mount is unreliable on macOS. Use an absolute path and create the dir first:

```bash
mkdir -p "$(pwd)/local_data"

docker run -d --name vectorai \
  -v "$(pwd)/local_data:/var/lib/actian-vectorai" \
  -p 6573-6575:6573-6575 \
  -e ACTIAN_VECTORAI_ACCEPT_EULA=YES \
  actian/vectorai:latest
```

Readiness check (comes up in ~5s):

```bash
until curl -s -m 2 -o /dev/null http://localhost:6573/healthz; do sleep 1; done && echo READY
```

Logs are clean — the only warnings are `No active licenses found` (expected for Community Edition) and a benign `counter.btr open miss/fail` on first boot while it creates the store.

## 3. Port map

| Port | Purpose |
|---|---|
| **6573** | **HTTP / REST API** — root-level paths, no `/v1` or `/api` prefix. Also `GET /healthz`. |
| **6574** | **gRPC** — this is what the Python SDK connects to. |
| 6575 | Next.js web dashboard (returns HTTP 200, browse it for demo eye-candy). |

## 4. Client story

Both work; **the Python SDK is the fastest to wire up** and is what the snippet below uses.

- **Python SDK:** `pip install actian-vectorai-client` (v1.0.2). Requires Python 3.10+. Connects to the **gRPC port 6574**, not 6573.
- **REST:** plain JSON on 6573. The API is **Qdrant-shaped** — `PUT /collections/{name}`, `PUT /collections/{name}/points`, `POST /collections/{name}/points/search`. If you already know Qdrant, you already know this. No OpenAPI served by the container (`/openapi.json` 404s); specs live at `https://docs.vectoraidb.actian.com/openapi_prepared/`.

## 5. Embeddings

**The server does not embed anything. You supply the vectors.** There is no text-in endpoint — every insert and every query takes a raw float array. Bring your own embedder (sentence-transformers `all-MiniLM-L6-v2` → 384 dims, or OpenAI `text-embedding-3-small` → 1536).

**Dimension used: 384**, distance `Cosine`. The dim is arbitrary — you declare it per collection via `vectors.size`. 384 is the cheaper choice for a hackathon; nothing in the API prefers one over the other.

## 6. VERIFIED end-to-end snippet

This is the exact file that was run successfully, including against a **freshly recreated container** reusing the bind mount. Copy-paste ready.

```python
"""Actian VectorAI DB - workflow-patch store. VERIFIED end to end.
pip install actian-vectorai-client
"""
import random
import urllib.request
from actian_vectorai import VectorAIClient, VectorParams, Distance, PointStruct

DIM = 384                       # must match your embedder
COLL = "workflow_patches"
GRPC = "localhost:6574"         # SDK speaks gRPC, not the REST port
REST = "http://localhost:6573"  # needed only for the /open call below


def fake_embed(seed: int):
    """Replace with your real embedder. The server does NOT embed - you supply vectors."""
    rng = random.Random(seed)
    return [rng.uniform(-1, 1) for _ in range(DIM)]


def ensure_collection(client, name, dim):
    """Idempotent. NOTE: after a container restart an existing collection is on disk
    but NOT loaded - every op fails 'Collection not found' until you open it.
    The Python SDK has no .open(), so we poke the REST port for that one call."""
    if client.collections.exists(name):
        urllib.request.urlopen(urllib.request.Request(
            f"{REST}/collections/{name}/open", data=b"{}",
            headers={"Content-Type": "application/json"}, method="POST")).read()
    else:
        client.collections.create(
            name, vectors_config=VectorParams(size=dim, distance=Distance.Cosine)
        )


PATCHES = [
    {"patch_id": "wp_001", "failure_type": "ungrounded_fabrication",
     "node_role": "information_retrieval", "tool_available": True, "tool_invoked": False,
     "diff": "--- a/agent.py\n+++ b/agent.py\n@@ force tool call before answering"},
    {"patch_id": "wp_002", "failure_type": "loop_stall", "node_role": "planner",
     "tool_available": False, "tool_invoked": False, "diff": "add max_iterations=5"},
    {"patch_id": "wp_003", "failure_type": "schema_violation", "node_role": "formatter",
     "tool_available": True, "tool_invoked": True, "diff": "validate against pydantic model"},
]

with VectorAIClient(GRPC) as client:
    ensure_collection(client, COLL, DIM)

    # --- insert (upsert = insert or overwrite by id) ---
    client.points.upsert(COLL, [
        PointStruct(id=i + 1, vector=fake_embed(i + 1), payload=p)
        for i, p in enumerate(PATCHES)
    ])

    # --- semantic search by vector ---
    hits = client.points.search(COLL, vector=fake_embed(1), limit=2, with_payload=True)
    for h in hits:
        print(f"id={h.id} score={h.score:.4f} patch={h.payload['patch_id']} "
              f"type={h.payload['failure_type']}")

    # --- metadata survives round trip exactly ---
    assert hits[0].payload == PATCHES[0], "metadata mismatch"
    print("OK: nearest neighbour + full metadata payload recovered")
```

Actual output:

```
id=1 score=1.0000 patch=wp_001 type=ungrounded_fabrication
id=3 score=0.0740 patch=wp_003 type=schema_violation
OK: nearest neighbour + full metadata payload recovered
```

The `assert` is the proof of task 4d: the returned payload dict is **exactly equal** to what went in — booleans stayed booleans (`tool_available: true`, `tool_invoked: false`, not stringified), and the multi-line `diff` string kept its newlines. Note payload **key order is not preserved** (it round-trips as a map), so compare dicts, never JSON strings.

### Same thing in curl, if you prefer REST

```bash
# create
curl -X PUT http://localhost:6573/collections/workflow_patches \
  -H 'Content-Type: application/json' \
  -d '{"vectors":{"size":384,"distance":"Cosine"}}'

# open (only needed if the collection already existed, e.g. after restart)
curl -X POST http://localhost:6573/collections/workflow_patches/open \
  -H 'Content-Type: application/json' -d '{}'

# insert  (points.json = {"points":[{"id":1,"vector":[...384 floats...],"payload":{...}}]})
curl -X PUT "http://localhost:6573/collections/workflow_patches/points?wait=true" \
  -H 'Content-Type: application/json' -d @points.json

# search  (query.json = {"vector":[...384 floats...],"limit":2,"with_payload":true})
curl -X POST http://localhost:6573/collections/workflow_patches/points/search \
  -H 'Content-Type: application/json' -d @query.json
```

### Bonus: metadata filtering works

Pre-filtering on payload fields is supported and verified — useful for scoping patch retrieval to a node role or failure class:

```json
{"vector": [...], "limit": 3, "with_payload": true,
 "filter": {"must": [{"key": "failure_type", "match": {"value": "schema_violation"}}]}}
```

Returned only `wp_003`, as expected.

## 7. Persistence — verified

Data survives **both** `docker restart` **and** full `docker rm -f` + re-`run` against the same bind mount. After recreating the container from scratch, `indexed_vectors_count: 3, points_count: 3` and the search returned identical results with payloads intact. `local_data/` was ~764 KB with 3 vectors. Sub-directories written: `collections/`, `auth/`, `MKDE/`, `data/`, `logs/`, plus `datastore.ini`, `license_cache`, `server_params.btr`.

**But** — see the `/open` gotcha below. Persistence is real; automatic re-loading is not.

## 8. Auth / EULA

- **No auth needed.** The server logs `auth_enabled not found in server_params.btr, defaulting to false`. Every command in this doc ran with zero credentials. (The `/auth/*` admin endpoints themselves demand an api-key, but you never need to touch them.)
- **No interactive EULA.** `-e ACTIAN_VECTORAI_ACCEPT_EULA=YES` is sufficient and headless-safe. Nothing blocks or prompts.
- `No active licenses found` is a warning, not an error — Community Edition runs fine without a licence file.

## 9. Gotchas

1. **`Collection not found` after every container restart — the big one.** The collection persists on disk and shows up in `GET /collections`, `GET /collections/{name}` reports `vectors_count: 3`, and `/exists` returns true — but **search, scroll, count, and retrieve all fail** with `Collection not found` until you call `POST /collections/{name}/open`. Symptoms are confusing because `get_info` also reports `points_count: 0` and `status: unknowncollectionstatus` while unloaded. The `ensure_collection()` helper above handles this; do not drop it.
2. **The Python SDK has no `.open()`.** `client.collections` exposes only `create, delete, exists, get_info, get_or_create, list, recreate, update`. The only way to open a collection is the REST endpoint on 6573 (or raw gRPC `actian_vectorai.CollectionsExt/OpenCollection`). So even on the "pure SDK" path you need REST reachable — expose both ports.
3. **Two ports, two protocols.** SDK → **6574** (gRPC). curl → **6573** (REST). Pointing the SDK at 6573 will just hang/fail.
4. **REST paths have no version prefix.** `/collections`, not `/api/v1/collections` — those 404. There is no `/health` either; it's `/healthz`.
5. **No server-side embedding.** Every write and query needs a float array you produced yourself.
6. **Community Edition caps at 5,000 vectors** (`[LicenseEnforcement] Started. allowed=5000`). Irrelevant at our scale, but do not plan a "index the whole corpus" demo on it.
7. **2.06 GB image.** Pull it before you need it.
8. **Payload key order is not preserved.** Compare parsed dicts, not serialized JSON.
9. Container also boots a Next.js UI on 6575, which is why the image is large and why boot logs interleave Node output with the C++ engine logs. Harmless.

## 10. Recommendation

Take the GO. It is genuinely load-bearing (real HNSW ANN index, real payload filtering, real persistence), it cost well under the time budget, and the Qdrant-shaped API means the fallback swap is cheap if it misbehaves. Keep `ensure_collection()` as the single entry point to the store so the `/open` quirk can never bite mid-demo — that one function is the entire difference between "works" and "mysteriously empty after we restarted the stack."
