"""Actian VectorAI DB — the workflow-patch store.

This is the only module that talks to Actian. Everything upstream (Evolution
agent, Validator, dashboard) goes through `PatchStore`.

Two things in here are load-bearing and easy to break:

  1. `ensure_ready()` handles the container-restart gotcha. Actian persists
     collections to disk but does **not** load them on boot — every read and
     write fails with `Collection not found` until you `POST
     /collections/{name}/open`. The Python SDK has no `.open()`, so we reach
     for the REST port for that one call. `ensure_ready()` is idempotent and
     is the single entry point to the store; call it before anything else and
     the quirk can never bite mid-demo.

  2. `embed()` is the only place text becomes a vector, and the only text
     that is ever fed to it is `FailureSignature.to_embedding_text()` —
     structure, never domain vocabulary. That is what makes a patch learned
     on brake pricing retrieve for a healthcare copay. See
     `tests/test_patch_store.py::test_transfer_property`, which fails loudly
     if domain content ever leaks in.

The retrieval contract: hits come back as **flat payload dicts** (exactly what
`WorkflowPatch.to_actian_payload()` put in) with two extra keys, `_score` and
`_point_id`, prefixed so they can never collide with a payload field.
"""

from __future__ import annotations

import os
import sys
import threading
import urllib.error
import urllib.request
import uuid
from pathlib import Path
from typing import Any

from actian_vectorai import (
    Condition,
    Distance,
    FieldCondition,
    Filter,
    Match,
    PointStruct,
    VectorAIClient,
    VectorParams,
)

try:  # running as a package from the repo root
    from core.schemas import FailureSignature, PatchStatus, WorkflowPatch
except ImportError:  # running with core/ itself on sys.path
    from schemas import FailureSignature, PatchStatus, WorkflowPatch  # type: ignore


__all__ = ["PatchStore", "embed", "EMBED_DIM", "embedder_name"]


# ---------------------------------------------------------------------------
# Embedding — isolated behind one function so the backend can be swapped
# without touching a single line of store logic.
# ---------------------------------------------------------------------------

EMBED_DIM = 384
"""Must match the collection's `vectors.size`. 384 is the native width of
all-MiniLM-L6-v2 and a supported truncation of text-embedding-3-small, so both
backends are interchangeable without recreating the collection."""

_REPO_ROOT = Path(__file__).resolve().parent.parent

_embed_lock = threading.Lock()
_backend: str | None = None
_impl = None


def _load_dotenv() -> None:
    """Populate os.environ from .env.local / .env without clobbering real env
    vars. Keys live in files, never in source."""
    for name in (".env.local", ".env"):
        path = _REPO_ROOT / name
        if not path.exists():
            continue
        for raw in path.read_text().splitlines():
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, val = line.partition("=")
            key = key.strip()
            val = val.strip().strip('"').strip("'")
            if key and key not in os.environ:
                os.environ[key] = val


def _try_sentence_transformers():
    from sentence_transformers import SentenceTransformer  # noqa: PLC0415

    model = SentenceTransformer("all-MiniLM-L6-v2")
    _dim_fn = getattr(model, "get_embedding_dimension", None) or model.get_sentence_embedding_dimension
    dim = _dim_fn()
    if dim != EMBED_DIM:
        raise RuntimeError(f"all-MiniLM-L6-v2 gave {dim} dims, expected {EMBED_DIM}")

    def _embed(texts: list[str]) -> list[list[float]]:
        vecs = model.encode(texts, normalize_embeddings=True)
        return [[float(x) for x in v] for v in vecs]

    return _embed


def _try_openai():
    _load_dotenv()
    key = os.environ.get("OPENAI_API_KEY")
    if not key:
        raise RuntimeError("OPENAI_API_KEY not set (looked in env, .env.local, .env)")
    from openai import OpenAI  # noqa: PLC0415

    client = OpenAI(api_key=key)

    def _embed(texts: list[str]) -> list[list[float]]:
        resp = client.embeddings.create(
            model="text-embedding-3-small", input=texts, dimensions=EMBED_DIM
        )
        return [list(d.embedding) for d in resp.data]

    return _embed


def _try_hash():
    """Last-resort deterministic backend. NOT semantic — identical strings map
    to identical vectors and everything else is near-orthogonal. It keeps the
    pipeline runnable offline but it makes retrieval a lookup table, not a
    similarity search. We shout about it rather than degrade quietly."""
    import hashlib  # noqa: PLC0415
    import math  # noqa: PLC0415

    print(
        "PatchStore WARNING: falling back to the hash embedder. Retrieval is "
        "exact-match only, NOT semantic. Do not demo transfer on this.",
        file=sys.stderr,
    )

    def _embed(texts: list[str]) -> list[list[float]]:
        out = []
        for t in texts:
            acc = [0.0] * EMBED_DIM
            for token in t.lower().split():
                h = hashlib.sha256(token.encode()).digest()
                for i in range(0, len(h) - 1, 2):
                    idx = (h[i] << 8 | h[i + 1]) % EMBED_DIM
                    acc[idx] += 1.0 if h[i] % 2 else -1.0
            norm = math.sqrt(sum(x * x for x in acc)) or 1.0
            out.append([x / norm for x in acc])
        return out

    return _embed


_BACKENDS = {
    "sentence-transformers": _try_sentence_transformers,
    "openai": _try_openai,
    "hash": _try_hash,
}

_PREFERENCE = ("sentence-transformers", "openai", "hash")


def _ensure_backend() -> None:
    global _backend, _impl
    if _impl is not None:
        return
    with _embed_lock:
        if _impl is not None:
            return
        forced = os.environ.get("PATCH_STORE_EMBEDDER")
        order = (forced,) if forced else _PREFERENCE
        errors = []
        for name in order:
            factory = _BACKENDS.get(name)
            if factory is None:
                errors.append(f"{name}: unknown backend")
                continue
            try:
                _impl = factory()
                _backend = name
                return
            except Exception as exc:  # noqa: BLE001 — we want the next backend
                errors.append(f"{name}: {type(exc).__name__}: {exc}")
        raise RuntimeError("no embedding backend available:\n  " + "\n  ".join(errors))


def embedder_name() -> str:
    """Which backend is live. Report it — the dashboard and the README should
    both be honest about what produced the vectors."""
    _ensure_backend()
    assert _backend is not None
    return _backend


def embed(text: str) -> list[float]:
    """Text -> 384-dim vector. The single embedding chokepoint.

    The only argument this should ever receive in production is
    `FailureSignature.to_embedding_text()`.
    """
    return embed_batch([text])[0]


def embed_batch(texts: list[str]) -> list[list[float]]:
    _ensure_backend()
    assert _impl is not None
    vecs = _impl(texts)
    for v in vecs:
        if len(v) != EMBED_DIM:
            raise RuntimeError(f"embedder returned {len(v)} dims, expected {EMBED_DIM}")
    return vecs


# ---------------------------------------------------------------------------
# Point ids
# ---------------------------------------------------------------------------

_POINT_NS = uuid.UUID("6f1a2b3c-4d5e-5f70-8192-a3b4c5d6e7f8")


def point_id_for(patch_id: str) -> str:
    """Deterministic uuid5 from the patch id.

    Actian point ids must be an unsigned int or a UUID; `wp_1a2b3c4d` is
    neither. Deriving it makes `store()` an idempotent upsert — restoring the
    same patch twice overwrites rather than duplicating.
    """
    return str(uuid.uuid5(_POINT_NS, patch_id))


# ---------------------------------------------------------------------------
# Store
# ---------------------------------------------------------------------------

def _match_for(value: Any) -> Match:
    if isinstance(value, bool):
        return Match(boolean=value)
    if isinstance(value, int):
        return Match(integer=value)
    return Match(keyword=str(value))


def _cond(key: str, value: Any) -> Condition:
    return Condition(field=FieldCondition(key=key, match=_match_for(value)))


class PatchStore:
    """The genome bank. Vectors are failure *structure*; payloads are the
    patch itself plus its provenance and validation record."""

    def __init__(
        self,
        host: str = "localhost",
        rest_port: int = 6573,
        grpc_port: int = 6574,
        collection: str = "workflow_patches",
    ) -> None:
        self.host = host
        self.rest_port = rest_port
        self.grpc_port = grpc_port
        self.collection = collection
        self.rest_url = f"http://{host}:{rest_port}"
        self.grpc_url = f"{host}:{grpc_port}"
        self._client: VectorAIClient | None = None
        self._ready = False

    # -- connection ---------------------------------------------------------

    @property
    def client(self) -> VectorAIClient:
        if self._client is None:
            c = VectorAIClient(self.grpc_url)
            c.connect()
            self._client = c
        return self._client

    def _reset_client(self) -> None:
        """Drop the gRPC channel. Needed after a container restart — the old
        channel survives the process but not the server."""
        if self._client is not None:
            try:
                self._client.close()
            except Exception:  # noqa: BLE001
                pass
        self._client = None

    def close(self) -> None:
        self._reset_client()
        self._ready = False

    def __enter__(self) -> PatchStore:
        self.ensure_ready()
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    # -- readiness ----------------------------------------------------------

    def _healthz(self, timeout: float = 2.0) -> bool:
        try:
            with urllib.request.urlopen(f"{self.rest_url}/healthz", timeout=timeout):
                return True
        except Exception:  # noqa: BLE001
            return False

    def wait_for_server(self, timeout: float = 60.0) -> None:
        import time  # noqa: PLC0415

        deadline = time.time() + timeout
        while time.time() < deadline:
            if self._healthz():
                return
            time.sleep(0.5)
        raise RuntimeError(f"Actian did not become healthy at {self.rest_url} in {timeout}s")

    def _rest_open(self) -> None:
        """THE GOTCHA. After any container restart the collection is on disk,
        `exists()` returns true, but every read/write fails `Collection not
        found` until this fires. The SDK exposes no `.open()`, so this is a
        REST call on 6573 — which is why both ports must be published."""
        req = urllib.request.Request(
            f"{self.rest_url}/collections/{self.collection}/open",
            data=b"{}",
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            urllib.request.urlopen(req, timeout=30).read()
        except urllib.error.HTTPError as exc:
            # Already-open is not an error condition for us.
            body = exc.read().decode("utf-8", "replace")[:300]
            if exc.code not in (400, 409):
                raise RuntimeError(f"open {self.collection} failed: {exc.code} {body}") from exc

    def ensure_ready(self, timeout: float = 60.0) -> None:
        """Create the collection if absent; OPEN it if it exists but is closed.

        Idempotent and restart-proof. This is the single entry point to the
        store — nothing else in this module assumes a live collection.
        """
        self.wait_for_server(timeout)

        last: Exception | None = None
        for attempt in range(2):
            try:
                if self.client.collections.exists(self.collection):
                    self._rest_open()
                else:
                    try:
                        self.client.collections.create(
                            self.collection,
                            vectors_config=VectorParams(
                                size=EMBED_DIM, distance=Distance.Cosine
                            ),
                        )
                    except Exception:  # noqa: BLE001 — lost a create race
                        self._rest_open()

                # Prove it: a closed collection fails this, an open one does not.
                try:
                    self.client.points.count(self.collection)
                except Exception:  # noqa: BLE001
                    self._rest_open()
                    self.client.points.count(self.collection)

                self._ready = True
                return
            except Exception as exc:  # noqa: BLE001
                last = exc
                self._reset_client()  # stale channel after a restart
                if attempt == 0:
                    self.wait_for_server(timeout)

        raise RuntimeError(f"ensure_ready failed for {self.collection}: {last}") from last

    def _require_ready(self) -> None:
        if not self._ready:
            self.ensure_ready()

    # -- writes -------------------------------------------------------------

    def store(self, patch: WorkflowPatch) -> str:
        """Embed the patch's failure signature, upsert with its flat payload.

        Returns the Actian point id (a uuid5 of `patch.patch_id`, so this is a
        true upsert — storing the same patch twice after validation flips it
        from candidate to promoted in place rather than duplicating it).
        """
        self._require_ready()
        vector = embed(patch.signature.to_embedding_text())
        pid = point_id_for(patch.patch_id)
        self.client.points.upsert(
            self.collection,
            [PointStruct(id=pid, vector=vector, payload=patch.to_actian_payload())],
        )
        return pid

    def store_many(self, patches: list[WorkflowPatch]) -> list[str]:
        self._require_ready()
        if not patches:
            return []
        vectors = embed_batch([p.signature.to_embedding_text() for p in patches])
        ids = [point_id_for(p.patch_id) for p in patches]
        self.client.points.upsert(
            self.collection,
            [
                PointStruct(id=pid, vector=v, payload=p.to_actian_payload())
                for pid, v, p in zip(ids, vectors, patches, strict=True)
            ],
        )
        return ids

    # -- reads --------------------------------------------------------------

    def retrieve(
        self,
        signature: FailureSignature,
        limit: int = 3,
        promoted_only: bool = True,
        exclude_vertical: str | None = None,
    ) -> list[dict[str, Any]]:
        """Vector search on the signature embedding, best first.

        `promoted_only` pre-filters to `status=promoted` — candidates and
        extinct patches stay in the collection (the population board needs
        them) but must never be handed back as advice.

        `exclude_vertical` drops patches whose `origin_vertical` matches. This
        is the transfer demo: excluding `healthcare` and still getting a hit
        proves the returned patch was learned somewhere else — it cannot be a
        patch trivially learned in-domain.

        Returns flat payload dicts plus `_score` (cosine similarity, 1.0 is
        identical) and `_point_id`.
        """
        self._require_ready()
        vector = embed(signature.to_embedding_text())

        must: list[Condition] = []
        must_not: list[Condition] = []
        if promoted_only:
            must.append(_cond("status", PatchStatus.PROMOTED.value))
        if exclude_vertical:
            must_not.append(_cond("origin_vertical", exclude_vertical))

        # Filter's list fields reject None — pass only the clauses we actually have.
        clauses: dict[str, list[Condition]] = {}
        if must:
            clauses["must"] = must
        if must_not:
            clauses["must_not"] = must_not
        flt = Filter(**clauses) if clauses else None

        hits = self.client.points.search(
            self.collection, vector=vector, limit=limit, filter=flt, with_payload=True
        )
        return [
            {**(h.payload or {}), "_score": float(h.score), "_point_id": str(h.id)}
            for h in hits
        ]

    def all_patches(self) -> list[dict[str, Any]]:
        """Every patch in the store, extinct ones included — the population
        board is only evidence of selection if the deaths are on it too.

        Sorted by generation then creation time so the board reads as a
        lineage rather than an unordered bag.
        """
        self._require_ready()
        out: list[dict[str, Any]] = []
        offset: Any = None
        while True:
            points, offset = self.client.points.scroll(
                self.collection, limit=256, offset=offset, with_payload=True
            )
            for p in points:
                out.append({**(p.payload or {}), "_point_id": str(p.id)})
            if not offset or not points:
                break
        out.sort(key=lambda d: (d.get("generation", 0), d.get("created_at", 0.0)))
        return out

    def count(self) -> int:
        self._require_ready()
        return int(self.client.points.count(self.collection))

    # -- maintenance --------------------------------------------------------

    def reset(self) -> None:
        """Drop and recreate the collection. Test fixtures and demo resets
        only — never call this on a populated store you care about."""
        self.wait_for_server()
        try:
            self.client.collections.delete(self.collection)
        except Exception:  # noqa: BLE001 — nothing to delete
            pass
        self._ready = False
        self.ensure_ready()
