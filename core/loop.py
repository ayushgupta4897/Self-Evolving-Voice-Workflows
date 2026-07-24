"""The autonomous loop — call in, evolved graph out, zero human clicks.

The chain, none of which requires a human:

    run call -> score every turn -> detect failure -> attribute to a node
      -> retrieve structurally-similar prior patches (Actian)
      -> generate 3 candidate mutations
      -> replay history against each, kill the ones that regress
      -> promote the survivor to Actian AND to the live Dograh graph
      -> next call runs the evolved graph

Two deliberate choices worth defending:

`_history` accumulates every call ever run, and the Validator regresses only
against cases that were PASSING before the patch. A case that was already
failing cannot regress, and counting it as one would make every candidate look
destructive and stall the loop permanently at generation 1.

Promotion writes to Dograh through `apply_and_publish`, never a bare PUT.
Dograh's text-chat runs the *draft* while its UI shows the *published*
version — so a bare PUT gives you a demo where the graph on screen is
provably not the graph that ran. Publishing is not a step anyone can forget
because it is not a separate step.
"""

from __future__ import annotations

import json
import logging
import os
import time
from dataclasses import dataclass, field
from pathlib import Path

from openai import OpenAI

from core.evolve import (
    Attribution, AttributionAgent, EvolutionAgent, ReplayCase, Validator,
    apply_patch,
)
from core.executor import GraphExecutor, KnowledgeBase, find_node
from core.schemas import (
    CallTrace, Generation, PatchStatus, WorkflowPatch,
)

log = logging.getLogger("evolve")


@dataclass
class LoopConfig:
    workflow_id: int | None = None
    vertical: str = "auto_servicing"
    kb_path: str = "kb/auto_servicing.md"
    push_to_dograh: bool = True
    """Off only for offline batch runs that build generation depth without
    touching the live graph."""
    reasoning_model: str = "gpt-4o"
    """Attribution and mutation run on OpenAI: both depend on reliable JSON,
    and Pioneer reports structured_outputs unsupported on every model. The
    *agent* still runs on Pioneer — see executor.py."""
    state_dir: str = "state"


class EvolutionLoop:
    def __init__(self, graph: dict, kb: KnowledgeBase, oracle,
                 patch_store=None, config: LoopConfig | None = None):
        self.config = config or LoopConfig()
        self.graph = graph
        self.kb = kb
        self.kb_text = kb.text
        self.oracle = oracle
        self.patch_store = patch_store

        self.executor = GraphExecutor(kb)
        reasoning_client = OpenAI(api_key=os.environ.get("OPENAI_API_KEY"))
        self.attributor = AttributionAgent(reasoning_client, self.config.reasoning_model)
        self.evolver = EvolutionAgent(reasoning_client, self.config.reasoning_model)
        self.validator = Validator(self.executor, oracle, self.kb_text)

        self.generation = 0
        self.failed_generations = 0
        self.generations: list[Generation] = []
        self.traces: list[CallTrace] = []
        self._history: list[ReplayCase] = []

        Path(self.config.state_dir).mkdir(exist_ok=True)

    # -- one call ----------------------------------------------------------

    def run_and_score(self, caller_turns: list[str], persona_id: str,
                      call_id: str | None = None) -> CallTrace:
        call_id = call_id or f"call_{int(time.time() * 1000)}"
        trace = self.executor.run_call(
            self.graph, caller_turns, call_id=call_id,
            workflow_version=f"gen_{self.generation}",
            vertical=self.config.vertical, persona_id=persona_id,
        )
        for turn in trace.turns:
            turn.verdict = self.oracle.score_turn(turn, self.kb_text)

        self.traces.append(trace)
        self._history.append(ReplayCase(
            call_id=call_id, persona_id=persona_id,
            vertical=self.config.vertical, caller_turns=caller_turns,
            was_passing=not trace.failing_turns,
        ))
        return trace

    # -- the loop ----------------------------------------------------------

    def process(self, trace: CallTrace) -> Generation | None:
        """Evolve in response to one call. Returns None if it passed.

        Never raises. A generation that dies on an API hiccup must not take
        the loop down with it — the autonomy claim is that this runs
        unattended, and unattended means surviving a bad minute.
        """
        if not trace.failing_turns:
            log.info("call %s passed; nothing to evolve", trace.call_id)
            return None

        try:
            return self._process(trace)
        except Exception as exc:  # noqa: BLE001
            log.exception("generation failed for %s: %s", trace.call_id, exc)
            self.failed_generations += 1
            return None

    def _process(self, trace: CallTrace) -> Generation | None:
        attribution = self.attributor.attribute(trace)
        if attribution is None:
            return None

        log.info("attributed to node=%s (upstream=%s): %s",
                 attribution.root_turn.node_id, attribution.is_upstream,
                 attribution.reasoning)

        prior = []
        if self.patch_store:
            try:
                self.patch_store.ensure_ready()
                prior = self.patch_store.retrieve(
                    attribution.signature, limit=3, promoted_only=True)
            except Exception as exc:  # noqa: BLE001
                log.warning("Actian retrieval unavailable: %s", exc)

        self.generation += 1
        candidates = self.evolver.generate(
            attribution, self.graph, self.generation,
            prior_patches=prior, vertical=self.config.vertical,
        )
        if not candidates:
            log.warning("no candidates generated for %s", trace.call_id)
            self.generation -= 1
            return None

        triggering = ReplayCase(
            call_id=trace.call_id, persona_id=trace.persona_id,
            vertical=trace.vertical,
            caller_turns=[t.caller_utterance for t in trace.turns],
            was_passing=False,
        )
        history = [c for c in self._history if c.call_id != trace.call_id]

        winner, all_candidates = self.validator.select(
            candidates, self.graph, triggering, history,
            target_node_id=attribution.root_turn.node_id)

        gen = Generation(
            number=self.generation,
            triggering_call_id=trace.call_id,
            candidates=all_candidates,
            mean_fitness_before=self._mean_fitness(),
        )

        for patch in all_candidates:
            if patch.parent_id is None and prior:
                patch.parent_id = prior[0].get("patch_id")
            if self.patch_store:
                try:
                    self.patch_store.store(patch)
                except Exception as exc:  # noqa: BLE001
                    log.warning("could not store %s: %s", patch.patch_id, exc)

        if winner is None:
            log.info("gen %d: all %d candidates died on regression",
                     self.generation, len(all_candidates))
        else:
            gen.promoted_patch_id = winner.patch_id
            self.graph = apply_patch(self.graph, winner.mutation)
            log.info("gen %d: promoted %s (%s on %s, confidence %.2f)",
                     self.generation, winner.patch_id,
                     winner.mutation.operation.value, winner.mutation.target,
                     winner.validation.confidence)
            if self.config.push_to_dograh and self.config.workflow_id:
                self._push(winner)

        gen.mean_fitness_after = self._mean_fitness()
        self.generations.append(gen)
        self._checkpoint(gen)
        return gen

    # -- side effects ------------------------------------------------------

    def _push(self, winner: WorkflowPatch) -> None:
        """Write the evolved graph back to the live runtime.

        Highlighting the mutated node is done here rather than in the
        dashboard because `selected: true` is persisted graph state — it makes
        Dograh's own UI show the ring, which is what a judge sees when we
        switch to the real product rather than our own render.
        """
        try:
            from core import dograh_client

            graph = dograh_client.highlight_node(
                self.graph, winner.mutation.target_node_id())
            dograh_client.apply_and_publish(self.config.workflow_id, graph)
            log.info("pushed gen %d to Dograh workflow %s",
                     self.generation, self.config.workflow_id)
        except Exception as exc:  # noqa: BLE001
            log.warning("Dograh push failed (loop continues): %s", exc)

    def _mean_fitness(self) -> float:
        if not self.traces:
            return 0.0
        recent = self.traces[-8:]
        return sum(t.fitness() for t in recent) / len(recent)

    def _checkpoint(self, gen: Generation) -> None:
        """Persist each generation as it completes.

        Written incrementally rather than at the end because the demo reads
        this directory live, and because a crash at generation 6 should not
        cost us generations 1-5.
        """
        path = Path(self.config.state_dir) / f"gen_{gen.number:03d}.json"
        payload = {
            "number": gen.number,
            "triggering_call_id": gen.triggering_call_id,
            "promoted_patch_id": gen.promoted_patch_id,
            "mean_fitness_before": gen.mean_fitness_before,
            "mean_fitness_after": gen.mean_fitness_after,
            "candidates": [json.loads(p.to_json()) for p in gen.candidates],
        }
        path.write_text(json.dumps(payload, indent=2))
        (Path(self.config.state_dir) / "graph_current.json").write_text(
            json.dumps(self.graph, indent=2))
        self._write_traces()

    def _write_traces(self) -> None:
        """Persist scored call traces for the dashboard's failing-vs-passing panel.

        Without this the panel falls back to fixtures, which means the one view
        showing an actual oracle verdict — correctness, grounded flag, citation,
        ground truth — would be the one view showing invented numbers. That is
        precisely backwards.
        """
        out = []
        for trace in self.traces:
            out.append({
                "call_id": trace.call_id,
                "workflow_version": trace.workflow_version,
                "vertical": trace.vertical,
                "persona_id": trace.persona_id,
                "task_completed": trace.task_completed,
                "fitness": trace.fitness(),
                "passed": not trace.failing_turns,
                "turns": [{
                    "turn_index": t.turn_index,
                    "node_id": t.node_id,
                    "node_role": t.node_role.value,
                    "caller_utterance": t.caller_utterance,
                    "agent_utterance": t.agent_utterance,
                    "tools_available": t.tools_available,
                    "tools_called": t.tools_called,
                    "tool_available_not_invoked": t.tool_available_not_invoked,
                    "latency_ms": t.latency_ms,
                    "verdict": None if not t.verdict else {
                        "correctness_score": t.verdict.correctness_score,
                        "grounded": t.verdict.grounded,
                        "citation": t.verdict.citation,
                        "ground_truth_value": t.verdict.ground_truth_value,
                        "failure_type": t.verdict.failure_type.value if t.verdict.failure_type else None,
                        "reasoning": t.verdict.reasoning,
                        "source": t.verdict.source,
                        "escalated": t.verdict.escalated,
                        "escalation_warranted": t.verdict.escalation_warranted,
                    },
                } for t in trace.turns],
            })
        (Path(self.config.state_dir) / "traces.json").write_text(json.dumps(out, indent=2))

    # -- batch -------------------------------------------------------------

    def warmup(self, personas: list[dict]) -> None:
        """Score every persona without evolving, to build a regression corpus.

        Without this, generation 1 has zero history and the promotion gate
        degenerates: "introduced no regressions" is trivially true when there
        is nothing to regress, so any candidate that fixes the trigger gets
        promoted at the floor confidence of 0.5. Running the population once
        first means the very first patch already has to survive real history.
        """
        log.info("warmup: scoring %d personas to build regression corpus", len(personas))
        for persona in personas:
            turns = persona.get("caller_turns") or [persona.get("goal", "")]
            try:
                trace = self.run_and_score(turns, persona["id"])
                log.info("  warmup %-26s %s (fitness %.3f)", persona["id"],
                         "PASS" if not trace.failing_turns else "FAIL", trace.fitness())
            except Exception as exc:  # noqa: BLE001
                log.warning("  warmup failed for %s: %s", persona["id"], exc)
        passing = sum(1 for c in self._history if c.was_passing)
        log.info("warmup complete: %d/%d passing — that is the regression corpus",
                 passing, len(self._history))

    def run_batch(self, personas: list[dict], rounds: int = 1) -> None:
        """Drive generation depth. Every failing call gets a full generation.

        This is what "warm-started since 10am" means concretely — it is the
        same code path a live call takes, not a separate offline mode.
        """
        for r in range(rounds):
            for persona in personas:
                turns = persona.get("caller_turns") or [persona.get("goal", "")]
                try:
                    trace = self.run_and_score(turns, persona["id"])
                except Exception as exc:  # noqa: BLE001
                    log.warning("call failed for %s: %s", persona["id"], exc)
                    continue
                before = self.failed_generations
                gen = self.process(trace)
                if gen:
                    status = f"gen {gen.number}"
                elif self.failed_generations > before:
                    # Must not read as "passed". A crashed generation and a
                    # clean call both return None, and reporting them
                    # identically is how a run with zero evolution looks like a
                    # run where nothing needed evolving.
                    status = "GENERATION ERRORED"
                elif trace.failing_turns:
                    status = "failed, no viable candidate"
                else:
                    status = "passed"
                log.info("round %d | %-26s -> %s", r + 1, persona["id"], status)
