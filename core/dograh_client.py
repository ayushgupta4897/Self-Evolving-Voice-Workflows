"""Thin, typed, synchronous client for the Dograh REST API.

Scope is deliberately narrow: read a workflow graph, write it, publish it, list
versions, run a headless text-chat session, and highlight a node. Everything the
evolution loop needs and nothing else.

Two things encoded here rather than left to callers, because both are easy to
get quietly wrong and both would be visible on stage:

  1. `PUT` saves a DRAFT. It does not publish. `apply_and_publish` does both and
     is the only function callers should use to change a live graph. There is no
     way to call it and end up with an unpublished mutation.

  2. Text-chat sessions run the DRAFT definition, not the published one
     (`prepare_workflow_run_inputs(..., use_draft=True)` in
     `api/routes/workflow_text_chat.py:178`). So a graph you PUT but never
     published still drives the conversation, while the UI's version history
     shows the old published version. That divergence is exactly how a demo
     shows the wrong thing; `apply_and_publish` closes it.

Config comes from `.env.local` at the project root. No async, no dependencies
beyond `requests`.
"""

from __future__ import annotations

import os
import re as _re
import time
from pathlib import Path
from typing import Any, Iterable

import requests

__all__ = [
    "DograhError",
    "DEFAULT_TIMEOUT",
    "KB_TOOL_NAME",
    "load_config",
    "get_workflow",
    "put_workflow",
    "publish_workflow",
    "apply_and_publish",
    "list_versions",
    "get_version_graph",
    "run_text_session",
    "highlight_node",
    "tools_available_for_node",
    "transition_function_names",
]


DEFAULT_TIMEOUT = 180
"""Seconds. A text-chat turn runs a full pipecat pipeline synchronously and can
take tens of seconds; the default `requests` timeout of None is worse, but 30
would flake."""

KB_TOOL_NAME = "retrieve_from_knowledge_base"
"""The LLM-facing function name Dograh generates for a node's `document_uuids`.
Defined at `api/services/workflow/tools/knowledge_base.py:350`. There is no
per-document tool name — every KB-enabled node exposes this one function."""

_ENV_FILE = ".env.local"


class DograhError(RuntimeError):
    """Any non-2xx response, carrying the status code and body."""

    def __init__(self, status: int, body: str, method: str, url: str) -> None:
        super().__init__(f"{method} {url} -> {status}: {body[:800]}")
        self.status = status
        self.body = body


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

def _project_root() -> Path:
    return Path(__file__).resolve().parent.parent


def load_config(env_file: str | Path | None = None) -> dict[str, str]:
    """Read `.env.local` from the project root. Real env vars win over the file
    so CI or a shell override does not require editing it."""
    path = Path(env_file) if env_file else _project_root() / _ENV_FILE
    cfg: dict[str, str] = {}
    if path.exists():
        for line in path.read_text().splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, _, v = line.partition("=")
            cfg[k.strip()] = v.strip()

    for key in ("DOGRAH_BASE_URL", "DOGRAH_TOKEN", "DOGRAH_API_PREFIX"):
        if os.environ.get(key):
            cfg[key] = os.environ[key]

    cfg.setdefault("DOGRAH_BASE_URL", "http://localhost:8000")
    cfg.setdefault("DOGRAH_API_PREFIX", "/api/v1")
    if not cfg.get("DOGRAH_TOKEN"):
        raise DograhError(0, f"DOGRAH_TOKEN missing from {path}", "CONFIG", str(path))
    return cfg


def _base(cfg: dict[str, str] | None = None) -> tuple[str, dict[str, str]]:
    cfg = cfg or load_config()
    root = cfg["DOGRAH_BASE_URL"].rstrip("/") + cfg["DOGRAH_API_PREFIX"]
    headers = {
        "Authorization": f"Bearer {cfg['DOGRAH_TOKEN']}",
        "Content-Type": "application/json",
    }
    return root, headers


def _request(
    method: str,
    path: str,
    *,
    json_body: Any = None,
    params: dict[str, Any] | None = None,
    cfg: dict[str, str] | None = None,
    timeout: int = DEFAULT_TIMEOUT,
) -> Any:
    root, headers = _base(cfg)
    url = f"{root}{path}"
    resp = requests.request(
        method, url, headers=headers, json=json_body, params=params, timeout=timeout
    )
    if not resp.ok:
        raise DograhError(resp.status_code, resp.text, method, url)
    if not resp.content:
        return None
    return resp.json()


# ---------------------------------------------------------------------------
# Graph read / write
# ---------------------------------------------------------------------------

def get_workflow(workflow_id: int, *, cfg: dict[str, str] | None = None) -> dict:
    """Return the React Flow graph `{nodes, edges}` for a workflow.

    Note the `/fetch/` segment — the path is not `/workflow/{id}`. The server
    returns the DRAFT if one exists, otherwise the published definition, which
    matches what a text-chat session will actually run.
    """
    data = _request("GET", f"/workflow/fetch/{workflow_id}", cfg=cfg)
    definition = data.get("workflow_definition") or {}
    return {"nodes": definition.get("nodes", []), "edges": definition.get("edges", [])}


def put_workflow(
    workflow_id: int, graph: dict, *, name: str | None = None,
    cfg: dict[str, str] | None = None,
) -> dict:
    """Save `graph` as a NEW DRAFT. This does not go live.

    Use `apply_and_publish` unless you specifically want an unpublished draft.
    """
    body: dict[str, Any] = {"workflow_definition": graph}
    if name:
        body["name"] = name
    return _request("PUT", f"/workflow/{workflow_id}", json_body=body, cfg=cfg)


def publish_workflow(workflow_id: int, *, cfg: dict[str, str] | None = None) -> dict:
    """Promote the current draft to published.

    Returns `{"status": "no_draft"}` rather than raising when there is nothing
    to publish. The API 400s with "No draft to publish" in that case, which is a
    benign no-op for us: it means the live version already matches the draft.
    """
    try:
        return _request("POST", f"/workflow/{workflow_id}/publish", cfg=cfg) or {}
    except DograhError as e:
        if e.status == 400 and "no draft" in e.body.lower():
            return {"status": "no_draft", "detail": e.body}
        raise


def apply_and_publish(
    workflow_id: int, graph: dict, *, name: str | None = None,
    cfg: dict[str, str] | None = None,
) -> dict:
    """Write `graph` and publish it. Publish is not optional.

    This is the guard against showing an unpublished graph on stage. Returns
    `{"draft": ..., "publish": ..., "published_version": int | None}`.
    """
    draft = put_workflow(workflow_id, graph, name=name, cfg=cfg)
    published = publish_workflow(workflow_id, cfg=cfg)

    version = None
    for v in list_versions(workflow_id, cfg=cfg):
        if str(v.get("status")) == "published":
            version = v.get("version_number")
            break
    return {"draft": draft, "publish": published, "published_version": version}


# ---------------------------------------------------------------------------
# Versions
# ---------------------------------------------------------------------------

def list_versions(
    workflow_id: int, *, limit: int = 50, offset: int = 0,
    cfg: dict[str, str] | None = None,
) -> list[dict]:
    """Every stored generation, newest first. Each row carries its full
    `workflow_json`, so a gen-0 vs gen-N diff needs no extra fetch."""
    data = _request(
        "GET", f"/workflow/{workflow_id}/versions",
        params={"limit": limit, "offset": offset}, cfg=cfg,
    )
    if isinstance(data, list):
        return data
    return data.get("versions") or data.get("items") or []


def get_version_graph(
    workflow_id: int, version: int, *, cfg: dict[str, str] | None = None
) -> dict:
    """The `{nodes, edges}` graph for a specific `version_number`. Powers the
    gen-0 vs gen-N diff view."""
    for v in list_versions(workflow_id, cfg=cfg):
        if v.get("version_number") == version:
            wf = v.get("workflow_json") or {}
            return {"nodes": wf.get("nodes", []), "edges": wf.get("edges", [])}
    raise DograhError(404, f"version {version} not found", "GET", f"/workflow/{workflow_id}/versions")


# ---------------------------------------------------------------------------
# Text chat
# ---------------------------------------------------------------------------

def tools_available_for_node(graph: dict, node_id: str) -> list[str]:
    """Which LLM tools a node exposes, derived from the graph.

    The transcript reports tools *called* but never tools *offered*, so
    `TurnTrace.tools_available` has to come from the graph. A node's
    `document_uuids` become the single `retrieve_from_knowledge_base` function;
    `tool_uuids` become custom tools whose function names are not resolvable
    without a second API call, so they are reported by uuid.
    """
    for n in graph.get("nodes", []):
        if n.get("id") != node_id:
            continue
        data = n.get("data") or {}
        tools: list[str] = []
        if data.get("document_uuids"):
            tools.append(KB_TOOL_NAME)
        tools.extend(f"tool:{u}" for u in (data.get("tool_uuids") or []))
        return tools
    return []


def transition_function_names(graph: dict) -> set[str]:
    """The LLM function names Dograh mints for edge transitions.

    Every outgoing edge becomes a callable function named
    `re.sub(r"[^a-z0-9]", "_", edge.data.label.lower())`
    (`api/services/workflow/workflow_graph.py:53`). These show up in the
    transcript as `tool_call_started` events indistinguishable from real tool
    calls, so they must be filtered out before deciding whether a node
    "used its tools" — otherwise every routed turn looks like a tool call and
    `TurnTrace.tool_available_not_invoked` silently reads False forever.
    """
    names: set[str] = set()
    for e in graph.get("edges", []):
        label = ((e.get("data") or {}).get("label") or "").lower()
        if label:
            names.add(_re.sub(r"[^a-z0-9]", "_", label))
    return names


def _flatten_turns(session_data: dict, graph: dict | None) -> list[dict]:
    """Turn Dograh's turn objects into flat, attributed records.

    Node attribution comes from each turn's `node_transition` event; when a turn
    has none (the agent stayed put) the node carries forward from the previous
    turn, which is what `checkpoint.current_node_id` would have told us anyway.
    """
    out: list[dict] = []
    current_node: str | None = None
    current_name: str | None = None
    transition_names = transition_function_names(graph) if graph else set()

    for idx, turn in enumerate(session_data.get("turns") or []):
        events = turn.get("events") or []
        transitions = [e for e in events if e.get("type") == "node_transition"]
        if transitions:
            payload = transitions[-1]["payload"]
            current_node = payload.get("node_id")
            current_name = payload.get("node_name")

        raw_called = [
            e["payload"].get("function_name")
            for e in events
            if e.get("type") == "tool_call_started"
        ]
        # Split routing from real retrieval. Only the latter counts as the node
        # having used a tool.
        routed = [c for c in raw_called if c in transition_names]
        called = [c for c in raw_called if c not in transition_names]
        available = (
            tools_available_for_node(graph, current_node)
            if graph and current_node else []
        )

        user_msg = turn.get("user_message") or {}
        asst_msg = turn.get("assistant_message") or {}
        out.append({
            "turn_index": idx,
            "turn_id": turn.get("id"),
            "status": turn.get("status"),
            "node_id": current_node,
            "node_name": current_name,
            "caller_utterance": user_msg.get("text"),
            "agent_utterance": asst_msg.get("text"),
            "tools_available": available,
            "tools_called": called,
            "transitions_called": routed,
            "kb_tool_called": KB_TOOL_NAME in called,
            "tool_available_not_invoked": bool(available) and not called,
            "node_transitions": [e["payload"] for e in transitions],
            "events": events,
        })
    return out


def run_text_session(
    workflow_id: int,
    persona_turns: Iterable[str],
    *,
    name: str | None = None,
    initial_context: dict | None = None,
    graph: dict | None = None,
    cfg: dict[str, str] | None = None,
    pause_s: float = 0.0,
) -> dict:
    """Run a headless text-chat session and return the transcript with per-turn
    node attribution.

    IMPORTANT: the session runs the workflow's DRAFT definition. Call
    `apply_and_publish` first so draft and published agree.

    Returns `{workflow_run_id, turns, final_node_id, nodes_visited,
    gathered_context, raw}`. Each entry in `turns` carries `node_id`,
    `tools_called` and `tools_available` — enough to build a `TurnTrace`.
    """
    if graph is None:
        graph = get_workflow(workflow_id, cfg=cfg)

    body: dict[str, Any] = {}
    if name:
        body["name"] = name
    if initial_context:
        body["initial_context"] = initial_context

    session = _request(
        "POST", f"/workflow/{workflow_id}/text-chat/sessions", json_body=body, cfg=cfg
    )
    run_id = session["workflow_run_id"]

    for utterance in persona_turns:
        if pause_s:
            time.sleep(pause_s)
        session = _request(
            "POST",
            f"/workflow/{workflow_id}/text-chat/sessions/{run_id}/messages",
            json_body={"text": utterance},
            cfg=cfg,
        )

    session_data = session.get("session_data") or {}
    checkpoint = session.get("checkpoint") or {}
    return {
        "workflow_run_id": run_id,
        "workflow_id": workflow_id,
        "turns": _flatten_turns(session_data, graph),
        "final_node_id": checkpoint.get("current_node_id"),
        "nodes_visited": (checkpoint.get("gathered_context") or {}).get("nodes_visited", []),
        "gathered_context": checkpoint.get("gathered_context") or {},
        "state": session.get("state"),
        "is_completed": session.get("is_completed"),
        "raw": session,
    }


# ---------------------------------------------------------------------------
# Rendering helper
# ---------------------------------------------------------------------------

def highlight_node(graph: dict, node_id: str) -> dict:
    """Return a copy of `graph` with `node_id` selected and every other node
    deselected.

    `selected` is a top-level React Flow key. Dograh's sanitizer filters only
    `node.data` and preserves top-level keys (`api/services/workflow/dto.py`),
    so this survives a PUT and renders as a highlight ring in their UI.
    """
    out = {
        "nodes": [{**n, "selected": n.get("id") == node_id} for n in graph.get("nodes", [])],
        "edges": list(graph.get("edges", [])),
    }
    return out
