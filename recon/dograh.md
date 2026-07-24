# Dograh Recon — graph mutation feasibility

**VERDICT: `GRAPH-API-CONFIRMED`** — the full workflow graph is readable and writable as plain React Flow JSON over authenticated REST (`GET /api/v1/workflow/fetch/{id}` + `PUT /api/v1/workflow/{id}`), with a built-in version history so gen-0 and gen-N are both retrievable and both renderable in their own UI.

Clone: `vendor/dograh`, 66 MB total (25 MB `.git`), shallow. Uses one git submodule (`pipecat` → `github.com/dograh-hq/pipecat.git`, `.gitmodules:1-3`). **It is NOT initialized and we do NOT need it** — the default compose stack pulls prebuilt images. Only build-from-source needs it (see Risks).

---

## Fastest path to a mutated graph on screen

Total ~25-35 min, most of it image pull.

| # | Step | Command / call | Est. |
|---|---|---|---|
| 1 | Write `.env` with the one blocking var, then bring the stack up | `echo "OSS_JWT_SECRET=$(openssl rand -hex 32)" > vendor/dograh/.env && docker compose up -d` (from `vendor/dograh`) | 15-20 min (~1.3-1.8 GB pull, no compile; Alembic's 93 migrations run automatically inside the api container) |
| 2 | Create a user + grab a JWT (no seed script exists; signup is open by default) | `POST /api/v1/auth/signup {"email","password"(≥8),"name"}` → `.token` | 1 min |
| 3 | Create a gen-0 agent from a template so the canvas is non-trivial | `GET /api/v1/workflow/templates` then `POST /api/v1/workflow/templates/duplicate`, **or** `POST /api/v1/workflow/create/definition` with your own `{nodes,edges}` | 3 min |
| 4 | Read → mutate → write. Change one `node.data.prompt`, set top-level `"selected": true` on that node, PUT it back, then publish | `GET /api/v1/workflow/fetch/{id}` → edit dict → `PUT /api/v1/workflow/{id}` → `POST /api/v1/workflow/{id}/publish` | 5 min |
| 5 | Open `http://localhost:3010/workflow/{id}` and flip between versions in the built-in Version History panel | UI | 2 min |

Step 4 already exists as working sample code we can lift verbatim: `vendor/dograh/examples/python/load_and_edit_workflow.py:47-65` loads a workflow, walks `definition["nodes"]`, appends to `node["data"]["prompt"]`, and PUTs it back. That is our mutation loop in 15 lines.

**The highlight trick (zero code changes to their UI):** `sanitize_workflow_definition` strips unknown keys only from `node.data` and explicitly preserves top-level node keys — `api/services/workflow/dto.py:1085-1093` ("Only `.data` is filtered — top-level keys on nodes/edges/definition ... are preserved as-is"). React Flow's own `selected` is a top-level node key, and `GenericNode` passes it straight through to `BaseNode` (`ui/src/components/flow/nodes/GenericNode.tsx:466,614`), which renders a blue ring + glow: `selected ? "border-primary ring-2 ring-primary/40 shadow-[0_0_20px_rgba(59,130,246,0.5)]"` (`ui/src/components/flow/nodes/BaseNode.tsx:24`). So writing `"selected": true` on the mutated node persists through the PUT and renders highlighted. There is also an `invalid` red-glow state (`BaseNode.tsx:26`) but it reads `data.invalid`, which **is** stripped as UI-runtime-only — don't rely on it.

---

## (a) How the graph is persisted

Two tables, both storing the graph as a raw JSON column:

- `WorkflowModel` — `api/db/models.py:422-479`. `workflow_definition = Column(JSON, ...)` at `:452` (legacy/inline copy), plus `released_definition_id` FK at `:462-466` pointing at the live version.
- `WorkflowDefinitionModel` — `api/db/models.py:351-393`. **This is the real store.** `workflow_json = Column(JSON, nullable=False, default=dict)` at `:355`. Versioning columns at `:363-372`: `status` (`draft` | `published` | `archived`), `version_number`, `published_at`, `is_current`. Also `workflow_configurations` (`:375`) and `template_context_variables` (`:378`) snapshotted per version.

The versioning is the single most useful thing here for us: **every generation is a row**, so gen-0 and gen-N coexist and are both fetchable.

### Schema (Pydantic, `api/services/workflow/dto.py`)

```
ReactFlowDTO   :1031-1033   { nodes: List[RFNodeDTO], edges: List[RFEdgeDTO] }
RFNodeDTO      :985-1008    { id, type, position, data }   # type validated against node registry
RFEdgeDTO      :1024-1028   { id, source, target, data }
EdgeDataDTO    :1016-1021   { label*, condition*, transition_speech?, transition_speech_type?, transition_speech_recording_id? }
```

`ReactFlowDTO._referential_integrity` (`:1035-1059`) rejects any edge whose `source`/`target` is not an existing node id — our mutator must keep referential integrity.

### Real trimmed example

From the test fixture at `api/tests/conftest.py:36-70`:

```json
{
  "nodes": [
    {
      "id": "1",
      "type": "startCall",
      "position": { "x": 0, "y": 0 },
      "data": {
        "name": "Start",
        "prompt": "<START_CALL_SYSTEM_PROMPT>",
        "is_start": true,
        "allow_interrupt": false,
        "add_global_prompt": false
      }
    },
    {
      "id": "2",
      "type": "endCall",
      "position": { "x": 0, "y": 200 },
      "data": {
        "name": "End",
        "prompt": "<END_CALL_SYSTEM_PROMPT>",
        "is_end": true,
        "allow_interrupt": false,
        "add_global_prompt": false
      }
    }
  ],
  "edges": [
    {
      "id": "1-2",
      "source": "1",
      "target": "2",
      "data": { "label": "End", "condition": "End the call" }
    }
  ]
}
```

A richer `agentNode` carries extraction + tools (fields from `dto.py:120-173`):

```json
{
  "id": "qualify",
  "type": "agentNode",
  "position": { "x": 0, "y": 100 },
  "selected": true,
  "data": {
    "name": "Qualify",
    "prompt": "Ask for budget and timeline. Supports {{template_variables}}.",
    "allow_interrupt": true,
    "add_global_prompt": true,
    "extraction_enabled": true,
    "extraction_prompt": "Extract qualification info.",
    "extraction_variables": [
      { "name": "budget", "type": "number", "prompt": "Stated budget" }
    ],
    "tool_uuids": ["<tool-uuid>"],
    "document_uuids": ["<doc-uuid>"]
  }
}
```

---

## (b) REST API for read/write — CONFIRMED

Prefix chain: `api/app.py:48` `API_PREFIX = "/api/v1"` + `:135`; `api/routes/workflow.py:78` `APIRouter(prefix="/workflow")`. All verified against the committed `docs/api-reference/openapi.json`.

| Method | Path | Handler | Purpose |
|---|---|---|---|
| GET | `/api/v1/workflow/fetch/{workflow_id}` | `workflow.py:698-749` | **Read graph.** Returns draft if one exists, else published. Note the `/fetch/` segment — it is *not* `/workflow/{id}`. |
| PUT | `/api/v1/workflow/{workflow_id}` | `workflow.py:993-1004` | **Write graph.** Body `UpdateWorkflowRequest` (`:290-297`): `{name?, workflow_definition?, template_context_variables?, workflow_configurations?}`. Saves as a **new draft**. |
| POST | `/api/v1/workflow/{workflow_id}/publish` | `workflow.py:793-797` | Promote draft → published (validates strictly; drafts may be incomplete). |
| POST | `/api/v1/workflow/{workflow_id}/create-draft` | `workflow.py:845-848` | Fork current published into a fresh draft. |
| GET | `/api/v1/workflow/{workflow_id}/versions` | `workflow.py:752-790` | **Every generation, newest first**, each row carrying its full `workflow_json`. Supports `limit`/`offset`. |
| POST | `/api/v1/workflow/create/definition` | `workflow.py:398-406` | Create. Body `CreateWorkflowRequest` `{name, workflow_definition}` (`:280-282`). |
| POST | `/api/v1/workflow/create/template` | `workflow.py:478-481` | Create from a template. |
| POST | `/api/v1/workflow/{workflow_id}/validate` | `workflow.py:336-339` | Dry-run validation before committing a mutation. |
| POST | `/api/v1/workflow/{workflow_id}/duplicate` | `workflow.py:1261-1264` | Cheap way to snapshot a gen-0 baseline. |
| GET | `/api/v1/node-types` | `api/routes/node_types.py:25,33,52` | Machine-readable node spec catalog — feed this to the mutator LLM. |

**Auth on all of the above:** `user: UserModel = Depends(get_user)`. `get_user` (`api/services/auth/depends.py:37-51`) accepts **either** `X-API-Key: dgr_...` (checked first, `:44-45`) **or** `Authorization: Bearer <jwt>` (`:50-51`, and `_handle_oss_auth` at `:287-314` also tolerates the bare token without the `Bearer` prefix). Org scoping is enforced — every read passes `organization_id=user.selected_organization_id`.

Secondary write path: an MCP server is mounted at `/api/v1/mcp` over Streamable HTTP with the same `X-API-Key` auth (`api/app.py:137-141`), exposing `save_workflow(workflow_id, code)` (`api/mcp_server/tools/save_workflow.py:65`). It takes a **TypeScript-ish code DSL, not JSON**, so it is the wrong surface for us — use REST.

Also relevant: `api/services/workflow/layout.py` is literally "Position reconciliation for LLM-edited workflows" — `reconcile_positions` (`:32-39`) carries positions over from the previous graph and `_place_new_nodes` (`:79-83`) auto-places nodes left at `(0,0)`. So our mutator can add nodes without computing coordinates.

---

## (c) Node types, prompt text, edge conditions, tools

Node type enum — `api/services/workflow/dto.py:25-32`, registry at `:1062-1070`:

| `type` value | Data class | Notes |
|---|---|---|
| `startCall` | `StartCallNodeData` (`:319`) | exactly one required; has greeting fields |
| `agentNode` | `AgentNodeData` (`:425-431`) | the main conversational node; prompt + extraction + tools |
| `endCall` | `EndCallNodeData` | terminal; `max_outgoing=0` (`:455`) |
| `globalNode` | `GlobalNodeData` (`:576`) | global instructions, prompted |
| `trigger` | `TriggerNodeData` | needs a `trigger_path` (auto-minted on PUT, `workflow.py:1026`) |
| `webhook` | `WebhookNodeData` | |
| `qa` | `QANodeData` (`:873`) | |

Plugin/integration node types are merged in at runtime via `get_node_data_model` (`:1073-1076`) / `all_node_type_names` (`:1079-1082`).

- **Node instruction/prompt text → `node.data.prompt`** (string, `min_length=1`, supports `{{template_variables}}`). Defined once on `_PromptedNodeDataMixin` at `dto.py:99-107` and inherited by startCall/agentNode/endCall/globalNode. This is the field our evolution loop mutates.
- **Edge/transition condition → `edge.data.condition`** (string, required, `min_length=1`) with a human `edge.data.label`. `EdgeDataDTO` at `dto.py:1016-1021`. Optional `transition_speech` for what the agent says while transitioning. This is the second, arguably more interesting mutation target — routing changes, not just wording.
- **Tool attachment → `node.data.tool_uuids`** (list of tool UUID strings), alongside `document_uuids` for knowledge base and `mcp_tool_filters`. `_ToolDocumentRefsMixin` at `dto.py:155-173`.
- **Extraction variables → `node.data.extraction_variables[]`** (`{name, type, prompt}`), gated on `extraction_enabled` (`dto.py:144-152`, `ExtractionVariableDTO` at `:45-71`).

---

## (d) LLM provider — pointing at an OpenAI-compatible base URL

**There is no `OPENAI_API_KEY` or `OPENAI_BASE_URL` env var anywhere in the repo.** Config is 100% database-driven. Precedence ladder in `get_effective_ai_model_configuration_for_workflow`, `api/services/configuration/ai_model_configuration.py:88-108`:

1. **Per-workflow override** — `workflow_configurations["model_configuration_v2_override"]` (key constant `:44`). If present, used whole; org config never consulted. Written by the same `PUT /api/v1/workflow/{id}` we already use.
2. **Per-organization row** — `organization_configurations` table, key `MODEL_CONFIGURATION_V2` (`api/enums.py:116-117`). Written by `PUT /api/v1/organizations/model-configurations/v2` (`api/routes/organization.py:400-429`) or the `/model-configurations` UI page.
3. Legacy per-workflow `model_overrides`, shallow-merged (`api/services/configuration/resolve.py:76-127`).
4. Nothing → empty config. **No env fallback.**

Custom base URL is first-class. `registry.py:342-354`:

```python
class OpenAILLMService(BaseLLMConfiguration):
    provider: Literal[ServiceProviders.OPENAI] = ServiceProviders.OPENAI
    model: str = Field(default="gpt-4.1", ..., json_schema_extra={... "allow_custom_input": True})
    base_url: str = Field(default="https://api.openai.com/v1",
        description="Override only if using an OpenAI-compatible API (e.g. local LLM, proxy).")
```

Consumed in `create_llm_service_from_provider`, `api/services/pipecat/service_factory.py:849,875-888` — `kwargs["base_url"] = base_url` passed into `OpenAILLMService`. `allow_custom_input: True` means arbitrary model name strings are accepted.

Two gotchas:
- **Saving the config makes a live call.** `PUT /organizations/model-configurations/v2` runs `_check_openai_api_key` (`api/services/configuration/check_validity.py:235-286`) which does `openai.OpenAI(...).models.list()`. **Our endpoint must implement `GET /v1/models` and be reachable from inside the api container**, or the save 422s.
- **`byok.pipeline` requires all three of `llm` + `tts` + `stt`** (non-optional on `BYOKPipelineAIModelConfiguration`, `api/schemas/ai_model_configuration.py:59-63`), each live-validated. Swapping only the LLM means supplying plausible tts/stt blocks too. You must be in `mode: "byok"`, not `"dograh"` (`_reject_dograh_provider`, `:64-71`).
- SSRF guard is a no-op in OSS mode — `api/utils/url_security.py:21-22` returns early when `DEPLOYMENT_MODE == "oss"` (the default, `api/constants.py:44`), so `http://host.docker.internal:...` works. Leave `DEPLOYMENT_MODE` unset.

---

## (e) LoopTalk — REMOVED from this codebase

**LoopTalk does not exist in the current tree.** An exhaustive case-insensitive sweep finds it in exactly three Alembic migrations and nowhere else — no routes, no services, no models, no UI, no docs.

- Created: `api/alembic/versions/e0d1a9b9f6c4_add_looptalk_testing_tables_without_.py`
- **Dropped: `api/alembic/versions/4c1f1e3e8ef2_drop_looptalk_tables.py`**, and the drop is in the live chain (`api/alembic/versions/0a1b2c3d4e5f_add_mcp_in_toolcategory.py:16` has `down_revision = "4c1f1e3e8ef2"`). The tables do not exist at head.

For the record, the dropped schema paired `actor_workflow_id` + `adversary_workflow_id` and stored `actor_recording_url`/`adversary_recording_url` — i.e. it ran the **full audio pipeline** on both sides, which we would not have wanted anyway.

### The surviving replacement: text-chat sessions (this is better for us)

Headless, text-only, no STT/TTS/telephony. `api/routes/workflow_text_chat.py`, prefix `/workflow` at `:30`:

| Method | Path | Line |
|---|---|---|
| POST | `/api/v1/workflow/{workflow_id}/text-chat/sessions` | `:157-161` |
| GET | `/api/v1/workflow/{workflow_id}/text-chat/sessions/{run_id}` | `:229-233` |
| POST | `/api/v1/workflow/{workflow_id}/text-chat/sessions/{run_id}/messages` | `:242-246` |
| POST | `/api/v1/workflow/{workflow_id}/text-chat/sessions/{run_id}/rewind` | `:272-276` |

Bodies (`:33-46`): `CreateTextChatSessionRequest {name?, initial_context?, annotations?}`; `AppendTextChatMessageRequest {text, expected_revision?}`. Response `WorkflowRunTextSessionResponse` (`:49-63`) returns `workflow_run_id`, `revision`, `state`, `is_completed`, `gathered_context`, `session_data` (transcript) **synchronously on every POST — no polling**. Runs are tagged `mode=WorkflowRunMode.TEXTCHAT` (`:183`). Auth is `Depends(get_user_with_selected_organization)`.

There is no adversary agent — **we supply the simulated user ourselves** by looping our own LLM against `/messages`. That is exactly the failure-signal generator our evolution loop needs, and it costs no voice infrastructure.

Failure signals also available: `api/services/workflow/qa/` (`analysis.py`, `metrics.py` — latency/TTFB/turn counts from run logs) and the `qa` node type.

---

## (f) Auth — fastest path

`AUTH_PROVIDER` defaults to `local` (`api/constants.py:48`); `ENABLE_SIGNUP` defaults to `true` (`:49`). Compose never sets `AUTH_PROVIDER`, so a fresh `docker compose up` is local email/password + HS256 JWT (`api/utils/auth.py:17-28`, 30-day expiry).

**There is no no-auth/bypass mode and no seed script.** `create_user_with_email` is called from exactly one place — the signup route (`api/routes/auth.py:42`). The recipe is two calls:

```bash
# password must be >= 8 chars (api/schemas/auth.py:9-13)
TOKEN=$(curl -s -X POST http://localhost:8000/api/v1/auth/signup \
  -H 'Content-Type: application/json' \
  -d '{"email":"dev@example.com","password":"password123","name":"dev"}' | jq -r .token)

curl http://localhost:8000/api/v1/workflow/fetch/1 -H "Authorization: Bearer $TOKEN"
```

Signup auto-creates the org and sets `selected_organization_id` (`api/routes/auth.py:49-56`), so org-scoped routes work immediately. Signup tries to reach `MPS_API_URL` (`https://services.dograh.com`) to mint a managed model key but it is wrapped in try/except (`api/routes/auth.py:71-74`) — **signup succeeds offline**, you just get no default model config.

Optional long-lived key: `POST /api/v1/user/api-keys` with `{"name":"local"}` (`api/routes/user.py:352`) returns a raw `dgr_...` key **once**; send it as `X-API-Key`. Format `f"dgr_{secrets.token_urlsafe(32)}"` (`api/utils/api_key.py:15`); keys are org-scoped. (Docs say `dg_` at `docs/api-reference/authentication.mdx:12` — the code says `dgr_`.)

Unauthenticated sanity check: `GET /api/v1/health` returns `auth_provider` and `signup_enabled` (`api/routes/main.py:118-133`).

---

## Startup requirements

Default `docker compose up` (no profile) starts **5 services. There is no `build:` anywhere in `docker-compose.yaml` — 100% prebuilt image pulls.**

| Service | Image | Host port | Required env | Blocking? |
|---|---|---|---|---|
| `api` | `${REGISTRY:-dograhai}/dograh-api:latest` (`:144`) | 8000 (`:241-242`) | **`OSS_JWT_SECRET`** (`:234`, `${VAR:?}` guard) | **YES — compose aborts before pulling** |
| `ui` | `${REGISTRY:-dograhai}/dograh-ui:latest` (`:264`) | 3010 (`:279-280`) | `BACKEND_URL` (default `http://api:8000`) | no |
| `postgres` | `pgvector/pgvector:pg17` (`:17`) | 5432 (`:31-32`) | `POSTGRES_PASSWORD` (default `postgres`) | no |
| `redis` | `redis:7` (`:44`) | 6379 (`:45-46`) | `REDIS_PASSWORD` (default `redissecret`) | no |
| `minio` | `minio/minio` (`:60`) | 127.0.0.1:9000, :9001 (`:67-69`) | `MINIO_ROOT_USER`/`PASSWORD` (default `minioadmin`) | no |
| `nginx` | `nginx:alpine` (`:104`) | 80, 443 | — | profile `remote` only (`:106`) |
| `coturn` | `coturn/coturn:4.8.0` (`:122`) | 3478, 5349 | — | profile `remote`/`local-turn` (`:125`) |
| `cloudflared` | `cloudflare/cloudflared:latest` (`:308`) | 2000 | — | profile `tunnel` (`:310`) |
| `dograh-init` | `bash:5.2` (`:81`) | — | — | profile `remote`/`local-turn` (`:83`) |

`OSS_JWT_SECRET` is **the only hard blocker**, and it is a compose-level `:?` guard, not a Python one (Python defaults it to `"change-me-in-production"` at `api/constants.py:204`). `scripts/start_docker.sh:145-151` generates it into `.env` for you.

`DATABASE_URL` and `REDIS_URL` are the only vars read via `os.environ[...]` (KeyError → crash) at `api/constants.py:41-42`, but compose fully constructs both from defaults (`:167`, `:170`), so they never block.

**No third-party inference API key is read from the environment at all** — zero env reads for OPENAI/DEEPGRAM/ELEVENLABS/CARTESIA/GROQ/TWILIO. Provider keys live per-organization in Postgres, entered via UI or the org config API. **The stack boots to a usable login page with zero API keys.** Keys only matter when you actually run an agent turn.

- **Cold start:** ~1.3-1.8 GB compressed pull (api image ~800 MB-1.2 GB: python:3.13-slim + pipecat with 17 extras + static ffmpeg + node binary). No torch — `api/requirements.txt` is 22 lines with zero ML packages. `docs/deployment/docker.mdx:65` claims 2-3 min first startup; realistically 15-20 min on hackathon wifi, dominated by the pull. Disk: 10 GB recommended (`docs/getting-started/prerequisites.mdx:14`).
- **Migrations are automatic.** Container CMD is `./scripts/start_services_docker.sh` (`api/Dockerfile:180`), which runs `alembic upgrade head` at line 30 before starting anything. 93 revision files — this is most of the api's 60 s health `start_period` (`docker-compose.yaml:250-259`). The UI waits on api *healthy* (`:281-283`).
- **MinIO bucket self-creates** in Python on first use (`api/services/filesystem/minio.py:59-60,86`). No `mc mb` step.
- `docker-compose-local.yaml` is a **separate infra-only stack** (postgres/redis/minio, hardcoded creds, no env vars, no api/ui) used by the devcontainer — not an override of the main file. Only useful if we run the API from source.

---

## (4) Rendering the graph ourselves — easy fallback

**Yes, the JSON is a standard React Flow `{nodes:[], edges:[]}` shape.** `ReactFlowDTO` (`dto.py:1031-1033`) is literally that; nodes carry `{id, type, position:{x,y}, data}` and edges `{id, source, target, data}`. Their own UI uses `@xyflow/react ^12.10.2` (`ui/package.json:37`) and feeds the stored JSON in directly.

Rendering it ourselves is genuinely low-risk because their UI is **not** coupled to per-type React components: `RenderWorkflow.tsx:134-144` maps *every* node type name to a single `GenericNode`, which is spec-driven from `GET /api/v1/node-types`. So a fallback renderer is ~50 lines: install `@xyflow/react`, map every `type` to one custom node component that renders `data.name` + `data.prompt`, render `data.label`/`data.condition` as edge labels, done. Positions are already in the JSON; where they aren't, their `reconcile_positions` fills them server-side.

Estimated cost of the fallback: **30-45 min**. Recommendation: use their UI for the demo (the Version History panel is free gen-0/gen-N switching) and keep a bare React Flow page as insurance — the JSON contract is identical either way, so building the fallback is never wasted work.

---

## Honest risk list for a live demo

1. **The `selected: true` highlight is a persistence trick, not a feature.** It survives the PUT (`dto.py:1085-1093`) and renders a glow (`BaseNode.tsx:24`), but React Flow owns selection state at runtime — a stray canvas click will clear it, and a page reload is what restores it. Rehearse: load page, don't touch the canvas. If it proves flaky, the fallback renderer removes the risk entirely.
2. **PUT saves a draft, not a live version.** `workflow.py:993-1004` explicitly "Saves as a new draft". Forgetting `POST /{id}/publish` means the version list won't show a new published generation. GET returns the draft preferentially (`:722-733`), so it *looks* applied while not being published — an easy way to confuse ourselves on stage.
3. **Draft vs published validation asymmetry.** Drafts may be incomplete; publish is the strict gate (`:798-801`). A mutation the LLM produces can PUT fine and then fail at publish. Mitigate by calling `POST /{id}/validate` (`:336`) on every candidate before publishing.
4. **Mutations can fail validation in three separate ways** — unknown node `type` (`dto.py:989-994`), edges referencing missing nodes (`:1035-1059`), and prompt-required nodes with an empty prompt (`:1004-1006`). Plus trigger-path checks and 409 node-instance errors on PUT (`workflow.py:1027-1032`). Our mutator must be constrained, ideally with `GET /api/v1/node-types` fed in as the schema.
5. **The image pull is the single biggest schedule risk.** ~1.3-1.8 GB on shared hackathon wifi. Start `docker compose pull` *now*, in the background, before writing any code.
6. **`OSS_JWT_SECRET` fails closed and fails early** — without it `docker compose up` and even `docker compose config` abort before pulling anything. One line in `.env` avoids a confusing dead end.
7. **Pointing at a custom OpenAI-compatible endpoint is more friction than it looks.** Save-time validation makes a live `GET /v1/models` call from inside the container (`check_validity.py:235-286`), and `byok.pipeline` demands valid `tts` + `stt` blocks alongside `llm` (`ai_model_configuration.py:59-63`). If we only need the graph mutated and text-chat run, consider using the Dograh-managed mode or plain OpenAI to dodge this entirely.
8. **LoopTalk is gone** — if anyone on the team planned around it, replan on text-chat sessions now. Text-chat has no adversary; we write the simulated user.
9. **Do not `docker compose build`.** The `pipecat` submodule is uninitialized and `api/Dockerfile:38-40` bind-mounts it and pip-installs from it — the build dies immediately. Same for "Reopen in Container" (`.devcontainer/Dockerfile:50-52`). If we ever need a build: `git submodule update --init --recursive`, and expect many minutes.
10. **Two registry defaults disagree.** `docker-compose.yaml:144,264` default `REGISTRY=dograhai` (Docker Hub) while `scripts/start_docker.sh:5` defaults `ghcr.io/dograh-hq`. If a pull 404s, that mismatch is why.
11. **`minio/minio` is untagged (`:latest`)** — a moving target, and MinIO has shipped breaking console changes. Low probability, but if MinIO fails to start it blocks `api` via `depends_on: service_healthy` (`:243-249`), which blocks `ui`. The whole stack is a health-check chain.
12. **Org scoping is enforced everywhere.** Every workflow read passes `organization_id=user.selected_organization_id`. If we ever mix an API key from one org with a JWT from another, we get silent 404s rather than permission errors.
