"""Local graph executor — the replay harness.

Why this exists alongside Dograh rather than instead of it:

Dograh runs the *live* graph and is the system of record. But the Validator
has to replay every historical failing call against every candidate patch —
with 3 candidates and a dozen historical cases that is ~40 conversations per
generation. Pushing 3 speculative graphs into Dograh and publishing each one
just to score it would be slow, would pollute the version history the
graph-diff view reads from, and would put a network round trip inside a loop
we want to run in seconds.

So: candidates are scored here, offline and in parallel. Only the survivor is
written to Dograh. This is the same split the spec calls for — "GEPA evolves
offline against replayed traces; live calls validate."

The executor reads the *same* React Flow JSON Dograh stores, from the same
fields (`node.data.prompt`, `edge.data.condition`, `node.data.tool_uuids`), so
a patch that improves a graph here improves the identical graph there. If
those field paths ever drift apart, replay stops predicting live behaviour and
the promotion gate becomes meaningless — so they are read through the single
accessor functions at the top of this module and nowhere else.
"""

from __future__ import annotations

import json
import os
import re
import time
from dataclasses import dataclass
from typing import Any, Callable

from openai import OpenAI

from core.schemas import CallTrace, NodeRole, TurnTrace

# ---------------------------------------------------------------------------
# Graph accessors — the ONLY place that knows Dograh's field layout.
# ---------------------------------------------------------------------------

def node_prompt(node: dict) -> str:
    return node.get("data", {}).get("prompt", "") or ""


def set_node_prompt(node: dict, text: str) -> None:
    node.setdefault("data", {})["prompt"] = text


KB_TOOL_NAME = "retrieve_from_knowledge_base"


def node_tools(node: dict) -> list[str]:
    """Which tools a node exposes.

    Dograh splits this across two fields and neither is obvious: attached
    knowledge-base documents live in `document_uuids` and are surfaced to the
    LLM as a single `retrieve_from_knowledge_base` function, while
    `tool_uuids` holds custom tools and is empty in our graphs. Reading only
    `tool_uuids` — the intuitive choice — reports zero tools available on
    every node, which makes `tool_available_not_invoked` permanently False and
    silently deletes the exact signal this project is built to detect.

    Must stay in agreement with `dograh_client.tools_available_for_node`; the
    replay harness and the live runtime disagreeing about what a node could
    have called would invalidate every regression result.
    """
    data = node.get("data") or {}
    tools: list[str] = []
    if data.get("document_uuids"):
        tools.append(KB_TOOL_NAME)
    tools.extend(f"tool:{u}" for u in (data.get("tool_uuids") or []))
    return tools


def edge_condition(edge: dict) -> str:
    return edge.get("data", {}).get("condition", "") or ""


def set_edge_condition(edge: dict, text: str) -> None:
    edge.setdefault("data", {})["condition"] = text


def node_role(node: dict) -> NodeRole:
    """Structural role, used for the failure signature.

    Prefers an explicit `data.role`; otherwise infers from node type and id.
    Inference is deliberately conservative — a node we cannot classify becomes
    INFORMATION_RETRIEVAL rather than guessing something specific, because a
    wrong role silently corrupts the signature and therefore retrieval.
    """
    explicit = node.get("data", {}).get("role")
    if explicit:
        try:
            return NodeRole(explicit)
        except ValueError:
            pass

    ntype = node.get("type", "")
    nid = node.get("id", "").lower()

    if ntype == "startCall" or "greet" in nid or "start" in nid:
        return NodeRole.GREETING
    if ntype == "endCall" or "end" in nid or "clos" in nid:
        return NodeRole.CLOSING
    if "escalat" in nid or "transfer" in nid or "human" in nid:
        return NodeRole.ESCALATION
    if "clarif" in nid or "ask" in nid or "collect" in nid:
        return NodeRole.CLARIFICATION
    if "confirm" in nid:
        return NodeRole.CONFIRMATION
    return NodeRole.INFORMATION_RETRIEVAL


def find_node(graph: dict, node_id: str) -> dict | None:
    return next((n for n in graph.get("nodes", []) if n.get("id") == node_id), None)


def start_node(graph: dict) -> dict:
    nodes = graph.get("nodes", [])
    explicit = next((n for n in nodes if n.get("type") == "startCall"), None)
    if explicit:
        return explicit
    targets = {e.get("target") for e in graph.get("edges", [])}
    orphans = [n for n in nodes if n.get("id") not in targets]
    if orphans:
        return orphans[0]
    if not nodes:
        raise ValueError("graph has no nodes")
    return nodes[0]


def outgoing(graph: dict, node_id: str) -> list[dict]:
    return [e for e in graph.get("edges", []) if e.get("source") == node_id]


# ---------------------------------------------------------------------------
# The knowledge-base tool
# ---------------------------------------------------------------------------

class KnowledgeBase:
    """Section-granular retrieval over the ground-truth document.

    Deliberately simple keyword-overlap scoring rather than embeddings. Two
    reasons: replay must be deterministic (an embedding service that reranks
    differently between runs would make regression testing unreliable), and
    the point of this project is that better retrieval does NOT fix the
    failure. If the tool were sophisticated it would muddy that claim. The
    tool works fine; the node just doesn't call it.
    """

    def __init__(self, text: str):
        self.text = text
        self.sections = self._split(text)

    @staticmethod
    def _split(text: str) -> list[tuple[str, str]]:
        parts: list[tuple[str, str]] = []
        current_title = "preamble"
        buf: list[str] = []
        for line in text.splitlines():
            if line.startswith("## "):
                if buf:
                    parts.append((current_title, "\n".join(buf).strip()))
                current_title = line[3:].strip()
                buf = [line]
            else:
                buf.append(line)
        if buf:
            parts.append((current_title, "\n".join(buf).strip()))
        return parts

    def lookup(self, query: str, top_k: int = 2) -> str:
        terms = {t for t in re.findall(r"[a-z0-9$]+", query.lower()) if len(t) > 2}
        if not terms:
            return "No matching information found."

        scored: list[tuple[int, str, str]] = []
        for title, body in self.sections:
            low = body.lower()
            score = sum(low.count(t) for t in terms)
            if score:
                scored.append((score, title, body))

        if not scored:
            return "No matching information found."

        scored.sort(key=lambda x: -x[0])
        return "\n\n---\n\n".join(f"[{t}]\n{b}" for _, t, b in scored[:top_k])


# ---------------------------------------------------------------------------
# Execution
# ---------------------------------------------------------------------------

KB_TOOL = {
    "type": "function",
    "function": {
        # Name must match what Dograh exposes for attached documents. A patch
        # that instructs the node to "always call retrieve_from_knowledge_base"
        # has to name a function that exists in replay too, or every
        # tool-requirement mutation scores as a failure here and passes live.
        "name": KB_TOOL_NAME,
        "description": "Look up verified company information: pricing, policies, hours, warranty, scheduling.",
        "parameters": {
            "type": "object",
            "properties": {"query": {"type": "string", "description": "What to look up."}},
            "required": ["query"],
        },
    },
}

_ESCALATION_MARKERS = (
    "transfer you", "connect you", "put you through", "have someone",
    "a representative", "service desk", "speak to", "colleague will",
    "someone from our team", "call you back",
)


def _looks_like_escalation(utterance: str) -> bool:
    low = utterance.lower()
    return any(m in low for m in _ESCALATION_MARKERS)


@dataclass
class ExecutorConfig:
    model: str = "gpt-5.4-mini"
    """Served by Pioneer. Note the tension this creates and why we accept it:
    a more capable model reaches for the knowledge-base tool more readily on
    its own, which makes the gen-0 failure RARER. That is the honest version of
    the story — the failure survives a current frontier-tier model precisely
    because it is caused by the node's instruction, not by the model being
    weak. If the demo failure stops reproducing, the fix is a more permissive
    gen-0 instruction, never a worse model."""
    temperature: float = 0.7
    """Non-zero on purpose. A deterministic agent produces one trace and one
    failure; a population needs variance to have anything to select over.
    Replay comparability is preserved by scoring every candidate on the SAME
    historical caller utterances, not by freezing the model."""
    max_turns: int = 8
    base_url: str | None = None
    """Defaults to Pioneer. Routing the *agent's own* inference through
    Pioneer is what makes the mined failure traces genuinely theirs — the
    adaptive-inference story is "it retrains on live production failures",
    which is only true if Pioneer served the calls that failed."""


class GraphExecutor:
    """Runs a React Flow workflow graph against a scripted caller."""

    def __init__(self, kb: KnowledgeBase, config: ExecutorConfig | None = None,
                 api_key: str | None = None):
        self.kb = kb
        self.config = config or ExecutorConfig()

        # The agent runs on Pioneer by default; the judge and the mutation
        # agent run on OpenAI (see oracle.py / evolve.py). That split is not
        # arbitrary. Pioneer's adaptive inference mines *live production
        # failures* — a claim that only holds if Pioneer served the calls that
        # failed. Meanwhile every Pioneer model reports
        # `structured_outputs: false`, so the components that depend on
        # reliable JSON stay on OpenAI. Falls back to OpenAI if Pioneer is
        # unconfigured, so a missing key degrades rather than crashes.
        base_url = self.config.base_url or os.environ.get("PIONEER_BASE_URL")
        key = api_key
        if key is None:
            key = os.environ.get("PIONEER_API_KEY") if base_url else None
        if key is None:
            base_url, key = None, os.environ.get("OPENAI_API_KEY")

        self.provider = "pioneer" if base_url else "openai"
        self.client = OpenAI(api_key=key, base_url=base_url)

    # -- single node -------------------------------------------------------

    def _run_node(self, node: dict, graph: dict, history: list[dict],
                  caller_utterance: str) -> tuple[str, list[str], int]:
        """Execute one node. Returns (agent_utterance, tools_called, latency_ms)."""
        tools_available = node_tools(node)
        instruction = node_prompt(node)
        global_instruction = "\n".join(
            node_prompt(n) for n in graph.get("nodes", []) if n.get("type") == "globalNode"
        )

        system = instruction
        if global_instruction:
            system = f"{global_instruction}\n\n{instruction}"

        messages = [{"role": "system", "content": system}, *history,
                    {"role": "user", "content": caller_utterance}]

        kwargs: dict[str, Any] = {
            "model": self.config.model,
            "messages": messages,
            "temperature": self.config.temperature,
        }
        if tools_available:
            kwargs["tools"] = [KB_TOOL]

        started = time.time()
        tools_called: list[str] = []

        resp = self.client.chat.completions.create(**kwargs)
        msg = resp.choices[0].message

        # One round of tool use. Deliberately not a loop — we want to observe
        # whether the node reaches for the tool at all, which is the signal.
        if getattr(msg, "tool_calls", None):
            messages.append(msg.model_dump(exclude_none=True))
            for call in msg.tool_calls:
                tools_called.append(call.function.name)
                try:
                    args = json.loads(call.function.arguments or "{}")
                except json.JSONDecodeError:
                    args = {}
                result = self.kb.lookup(args.get("query", caller_utterance))
                messages.append({
                    "role": "tool",
                    "tool_call_id": call.id,
                    "content": result,
                })
            resp = self.client.chat.completions.create(
                model=self.config.model, messages=messages,
                temperature=self.config.temperature,
            )
            msg = resp.choices[0].message

        latency_ms = int((time.time() - started) * 1000)
        return (msg.content or "").strip(), tools_called, latency_ms

    # -- transition --------------------------------------------------------

    def _choose_transition(self, graph: dict, node_id: str, history: list[dict],
                           agent_utterance: str) -> tuple[str | None, str | None]:
        """Pick the next node. Returns (edge_id, next_node_id)."""
        edges = outgoing(graph, node_id)
        if not edges:
            return None, None
        if len(edges) == 1:
            return edges[0].get("id"), edges[0].get("target")

        described = "\n".join(
            f"{i}. condition: {edge_condition(e) or '(none specified)'} -> {e.get('target')}"
            for i, e in enumerate(edges)
        )
        prompt = (
            "Given the agent's last message, which transition condition applies?\n\n"
            f"Agent said: {agent_utterance}\n\nOptions:\n{described}\n\n"
            "Reply with only the option number."
        )
        try:
            resp = self.client.chat.completions.create(
                model=self.config.model,
                messages=[{"role": "user", "content": prompt}],
                temperature=0,
            )
            idx = int(re.search(r"\d+", resp.choices[0].message.content or "0").group())
            idx = max(0, min(idx, len(edges) - 1))
        except Exception:
            idx = 0
        return edges[idx].get("id"), edges[idx].get("target")

    # -- full call ---------------------------------------------------------

    def run_call(self, graph: dict, caller_turns: list[str], *,
                 call_id: str, workflow_version: str, vertical: str,
                 persona_id: str) -> CallTrace:
        """Replay a scripted caller against a graph.

        `caller_turns` are fixed utterances rather than a live persona
        simulator. That is what makes regression testing honest: every
        candidate patch faces the identical caller, so a difference in outcome
        is attributable to the patch and not to a caller who happened to phrase
        things differently that run.
        """
        trace = CallTrace(call_id=call_id, workflow_version=workflow_version,
                          vertical=vertical, persona_id=persona_id)

        current = start_node(graph)
        history: list[dict] = []

        for i, caller_utterance in enumerate(caller_turns[: self.config.max_turns]):
            if current is None:
                break

            utterance, tools_called, latency = self._run_node(
                current, graph, history, caller_utterance)
            edge_id, next_id = self._choose_transition(
                graph, current.get("id"), history, utterance)

            trace.turns.append(TurnTrace(
                call_id=call_id,
                workflow_version=workflow_version,
                turn_index=i,
                node_id=current.get("id"),
                node_role=node_role(current),
                node_instruction=node_prompt(current),
                caller_utterance=caller_utterance,
                agent_utterance=utterance,
                tools_available=node_tools(current),
                tools_called=tools_called,
                transition_taken=edge_id,
                latency_ms=latency,
            ))

            history.append({"role": "user", "content": caller_utterance})
            history.append({"role": "assistant", "content": utterance})

            if next_id:
                nxt = find_node(graph, next_id)
                if nxt is not None and nxt.get("type") == "endCall":
                    break
                current = nxt
            else:
                break

        trace.task_completed = self._task_completed(trace)
        return trace

    @staticmethod
    def _task_completed(trace: CallTrace) -> bool:
        """A call completed its task if it produced a substantive final answer
        and did not end by punting to a human.

        Scored structurally rather than by the oracle on purpose: task
        completion and correctness must stay independent terms, or the fitness
        function collapses into one signal wearing two hats and the weights
        stop meaning anything.
        """
        if not trace.turns:
            return False
        last = trace.turns[-1]
        return len(last.agent_utterance) > 40 and not _looks_like_escalation(last.agent_utterance)


def load_kb(path: str = "kb/auto_servicing.md") -> KnowledgeBase:
    with open(path, encoding="utf-8") as fh:
        return KnowledgeBase(fh.read())
