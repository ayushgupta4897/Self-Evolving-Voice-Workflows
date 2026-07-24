"""Attribution, mutation, and the promotion gate.

This is where the claim "the agent rewrites itself" either is or isn't true,
so three things are deliberate:

  * Attribution looks for the ROOT node, not the loud one. A fabricated price
    at turn 4 is often caused by a clarification node at turn 2 that never
    collected the vehicle class. Patching turn 4 treats a symptom and the
    population drifts. See `AttributionAgent`.

  * Evolution emits three candidates using DIFFERENT operators, forced
    structurally rather than requested politely. Three paraphrases of the same
    constraint is not a population and there is nothing for selection to act
    on. See `_assign_operators`.

  * The Validator can only promote a patch that fixes the new failure AND
    introduces zero regressions on history. That gate is in `Validation`
    (schemas.py) and is re-checked here. A patch that fixes today's bug and
    breaks last hour's is worse than no patch, and without this gate the whole
    thing is a patch pipeline wearing evolution's clothes.
"""

from __future__ import annotations

import copy
import json
import logging
import os
import re
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass

log = logging.getLogger("evolve")

from openai import OpenAI

from core.executor import (
    GraphExecutor, edge_condition, find_node, node_prompt,
    set_edge_condition, set_node_prompt,
)
from core.schemas import (
    CallTrace, FailureSignature, Generation, Mutation, MutationOperator,
    OracleVerdict, PatchStatus, TurnTrace, Validation, WorkflowPatch,
    _contains_specific_value,
)

# ---------------------------------------------------------------------------
# Applying a mutation to a graph
# ---------------------------------------------------------------------------

def apply_patch(graph: dict, mutation: Mutation) -> dict:
    """Return a NEW graph with the mutation applied. Never mutates in place —
    the Validator scores three candidates against the same baseline and a
    shared-mutable graph would silently make candidate 3 inherit candidate 1's
    edits, quietly invalidating every comparison.
    """
    g = copy.deepcopy(graph)
    target_id = mutation.target_node_id()
    op = mutation.operation

    if op == MutationOperator.CHANGE_TRANSITION:
        edge = next((e for e in g.get("edges", []) if e.get("id") == target_id), None)
        if edge is None:
            raise ValueError(f"edge {target_id} not found")
        set_edge_condition(edge, mutation.diff)
        return g

    node = find_node(g, target_id)
    if node is None:
        raise ValueError(f"node {target_id} not found")

    if op == MutationOperator.REWRITE_INSTRUCTION:
        set_node_prompt(node, mutation.diff)
    elif op in (MutationOperator.APPEND_CONSTRAINT, MutationOperator.ADD_TOOL_REQUIREMENT):
        existing = node_prompt(node)
        set_node_prompt(node, f"{existing.rstrip()}\n\n{mutation.diff.strip()}")
    else:
        raise ValueError(f"unhandled operator {op}")

    return g


# ---------------------------------------------------------------------------
# Attribution
# ---------------------------------------------------------------------------

@dataclass
class Attribution:
    failing_turn: TurnTrace
    root_turn: TurnTrace
    """The turn whose node actually caused the failure. Often == failing_turn,
    but not always — and the cases where it differs are the ones worth
    demonstrating."""
    signature: FailureSignature
    reasoning: str

    @property
    def is_upstream(self) -> bool:
        return self.root_turn.turn_index != self.failing_turn.turn_index


class AttributionAgent:
    """Identifies which node produced an ungrounded turn — and whether the
    real fault lies upstream."""

    def __init__(self, client: OpenAI, model: str = "gpt-4o"):
        self.client = client
        self.model = model

    def attribute(self, trace: CallTrace) -> Attribution | None:
        failing = trace.failing_turns
        if not failing:
            return None

        # A turn can fail without a failure_type — the oracle marks it
        # not-passed on a low correctness score while still judging it grounded
        # and non-escalating. Those are real quality problems but they name no
        # mutation target, so prefer turns the oracle actually classified and
        # only fall back to an unclassified one if that is all there is.
        classified = [t for t in failing if t.verdict.failure_type is not None]
        if not classified:
            log.info("call %s failed but no turn carries a failure_type; "
                     "nothing specific to mutate", trace.call_id)
            return None

        # Worst turn by correctness. Ties break toward the earliest, because
        # an early failure tends to cause the later ones.
        failing_turn = min(classified, key=lambda t: (t.verdict.correctness_score, t.turn_index))

        preceding = [t for t in trace.turns if t.turn_index < failing_turn.turn_index]
        root_turn, reasoning = self._find_root(failing_turn, preceding)

        # The signature must describe the node we are about to PATCH, not the
        # turn where the failure surfaced. Those differ whenever attribution
        # walks upstream, and getting it wrong is silently corrosive: the patch
        # gets stored in Actian under a structure that does not describe what it
        # fixes, so retrieval for a structurally identical failure returns
        # something unrelated and the cross-vertical transfer stops working.
        #
        # The failure TYPE still comes from the failing turn's verdict — that is
        # what went wrong. Everything structural comes from the root node — that
        # is where it went wrong.
        signature = FailureSignature(
            failure_type=failing_turn.verdict.failure_type,
            node_role=root_turn.node_role,
            tool_available=bool(root_turn.tools_available),
            tool_invoked=bool(root_turn.tools_called),
            asserted_specific_value=_contains_specific_value(failing_turn.agent_utterance),
        )

        return Attribution(
            failing_turn=failing_turn,
            root_turn=root_turn,
            signature=signature,
            reasoning=reasoning,
        )

    def _find_root(self, failing: TurnTrace,
                   preceding: list[TurnTrace]) -> tuple[TurnTrace, str]:
        if not preceding:
            return failing, "First substantive turn in the call; no upstream node could be responsible."

        history = "\n\n".join(
            f"Turn {t.turn_index} — node `{t.node_id}` (role: {t.node_role.value})\n"
            f"  instruction: {t.node_instruction[:300]}\n"
            f"  caller: {t.caller_utterance}\n"
            f"  agent: {t.agent_utterance}\n"
            f"  tools available: {t.tools_available} | tools called: {t.tools_called}"
            for t in preceding
        )

        prompt = f"""A voice agent produced a bad answer. Identify the node truly responsible.

FAILING TURN {failing.turn_index} — node `{failing.node_id}` (role: {failing.node_role.value})
  instruction: {failing.node_instruction}
  caller: {failing.caller_utterance}
  agent: {failing.agent_utterance}
  tools available: {failing.tools_available} | tools called: {failing.tools_called}
  why it failed: {failing.verdict.reasoning}

PRECEDING TURNS
{history}

A failure often surfaces at one node but originates at an earlier one — for
example, a node that answered without a needed fact may be blameless if an
earlier node was supposed to collect that fact and did not.

Attribute to the EARLIER node only if its instruction genuinely failed to do
its job. If the failing node had everything it needed and still got it wrong,
attribute to the failing node.

Respond as JSON: {{"root_node_id": "<id>", "reasoning": "<two sentences>"}}"""

        try:
            resp = self.client.chat.completions.create(
                model=self.model,
                messages=[{"role": "user", "content": prompt}],
                response_format={"type": "json_object"},
                temperature=0,
            )
            data = json.loads(resp.choices[0].message.content)
            root_id = data.get("root_node_id", failing.node_id)
            reasoning = data.get("reasoning", "")
        except Exception as exc:
            return failing, f"Root-cause analysis unavailable ({exc}); attributed to the failing node directly."

        root = next((t for t in [*preceding, failing] if t.node_id == root_id), failing)
        return root, reasoning


# ---------------------------------------------------------------------------
# Evolution
# ---------------------------------------------------------------------------

_OPERATOR_GUIDANCE = {
    MutationOperator.APPEND_CONSTRAINT:
        "Append a short, hard rule to the node's existing instruction. Do not "
        "restate the instruction. The appended text must be an imperative "
        "constraint that would have prevented this specific failure.",
    MutationOperator.ADD_TOOL_REQUIREMENT:
        "Append text that makes calling the lookup tool MANDATORY before a "
        "specific class of claim, and state what to do when the tool returns "
        "nothing. Name the tool explicitly.",
    MutationOperator.REWRITE_INSTRUCTION:
        "Rewrite the node's entire instruction. Keep its purpose and tone, but "
        "restructure it so the failure mode is impossible. Output the complete "
        "replacement instruction.",
    MutationOperator.CHANGE_TRANSITION:
        "Rewrite one outgoing edge's condition so the conversation routes "
        "correctly. Output only the new condition text.",
}


def _assign_operators(signature: FailureSignature, has_edges: bool) -> list[MutationOperator]:
    """Pick three DISTINCT operators suited to the failure.

    Structural rather than model-chosen: asking one model for "three different
    approaches" reliably yields three rewordings of its first idea. Forcing
    genuinely different operators is what makes the candidates a population
    rather than a committee.
    """
    ops: list[MutationOperator] = []

    if signature.tool_available and not signature.tool_invoked:
        ops.append(MutationOperator.ADD_TOOL_REQUIREMENT)

    ops.append(MutationOperator.APPEND_CONSTRAINT)

    if signature.failure_type.value == "wrong_transition" and has_edges:
        ops.insert(0, MutationOperator.CHANGE_TRANSITION)

    ops.append(MutationOperator.REWRITE_INSTRUCTION)

    if has_edges and MutationOperator.CHANGE_TRANSITION not in ops:
        ops.append(MutationOperator.CHANGE_TRANSITION)

    seen, out = set(), []
    for o in ops:
        if o not in seen:
            seen.add(o)
            out.append(o)
    return out[:3]


class EvolutionAgent:
    """Generates candidate graph patches."""

    def __init__(self, client: OpenAI, model: str = "gpt-4o"):
        self.client = client
        self.model = model

    def generate(self, attribution: Attribution, graph: dict, generation: int,
                 prior_patches: list[dict] | None = None,
                 vertical: str = "auto_servicing") -> list[WorkflowPatch]:
        node = find_node(graph, attribution.root_turn.node_id)
        if node is None:
            return []

        edges = [e for e in graph.get("edges", []) if e.get("source") == node.get("id")]
        operators = _assign_operators(attribution.signature, bool(edges))

        prior_block = ""
        if prior_patches:
            rendered = "\n".join(
                f"- (from {p.get('origin_vertical', '?')}, confidence {p.get('confidence', 0):.2f}) "
                f"{p.get('operation')}: {p.get('diff', '')[:220]}"
                for p in prior_patches[:3]
            )
            prior_block = (
                "\n\nPATCHES THAT PREVIOUSLY FIXED STRUCTURALLY IDENTICAL FAILURES\n"
                "These were learned on other problems, possibly in other domains. "
                "Adapt the underlying idea; do not copy domain-specific wording.\n"
                f"{rendered}\n"
            )

        with ThreadPoolExecutor(max_workers=3) as pool:
            results = list(pool.map(
                lambda op: self._one(op, attribution, node, edges, prior_block),
                operators,
            ))

        patches: list[WorkflowPatch] = []
        for op, result in zip(operators, results):
            if result is None:
                continue
            target, diff, reflection = result
            patches.append(WorkflowPatch(
                generation=generation,
                signature=attribution.signature,
                mutation=Mutation(target=target, operation=op, diff=diff),
                reflection=reflection,
                origin_vertical=vertical,
            ))
        return patches

    def _one(self, op: MutationOperator, attribution: Attribution,
             node: dict, edges: list[dict], prior_block: str):
        turn = attribution.failing_turn
        root = attribution.root_turn

        edge_block = ""
        if op == MutationOperator.CHANGE_TRANSITION and edges:
            edge_block = "\n".join(
                f"- edge `{e.get('id')}` -> {e.get('target')}: {edge_condition(e) or '(no condition)'}"
                for e in edges
            )

        prompt = f"""You are improving one node of a voice agent's conversation graph.

THE FAILURE
  caller asked: {turn.caller_utterance}
  agent replied: {turn.agent_utterance}
  what was actually true: {turn.verdict.ground_truth_value}
  diagnosis: {turn.verdict.reasoning}
  failure type: {turn.verdict.failure_type.value}
  a lookup tool was {'available' if turn.tools_available else 'NOT available'} and was {'called' if turn.tools_called else 'NOT called'}

ATTRIBUTED NODE — `{root.node_id}` (role: {root.node_role.value})
  why this node: {attribution.reasoning}

  current instruction:
  \"\"\"{node_prompt(node)}\"\"\"
{f'  outgoing edges:{chr(10)}{edge_block}' if edge_block else ''}{prior_block}

YOUR MUTATION: {op.value}
{_OPERATOR_GUIDANCE[op]}

Constraints:
- Do not hardcode any specific fact, price, or value into the instruction.
  The fix must be a RULE about how to behave, not the answer to this one
  question. A patch that memorises "$285" fixes one call and nothing else.
- Keep it concise. Node instructions that sprawl degrade every other turn.
- The rule must generalise to structurally similar failures in other domains.

Respond as JSON:
{{"diff": "<the new text>", "reflection": "<2-3 sentences: what about the instruction permitted this failure, and why this change forecloses it>"}}"""

        try:
            resp = self.client.chat.completions.create(
                model=self.model,
                messages=[{"role": "user", "content": prompt}],
                response_format={"type": "json_object"},
                temperature=0.9,  # spread the candidates apart
            )
            data = json.loads(resp.choices[0].message.content)
            diff = (data.get("diff") or "").strip()
            reflection = (data.get("reflection") or "").strip()
            if not diff:
                return None
        except Exception:
            return None

        if op == MutationOperator.CHANGE_TRANSITION and edges:
            target = f"{edges[0].get('id')}.data.condition"
        else:
            target = f"{node.get('id')}.data.prompt"

        return target, diff, reflection


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------

@dataclass
class ReplayCase:
    """A historical call, kept for regression testing.

    `was_passing` is what makes regression detection meaningful: a case that
    was already failing before the patch cannot "regress", and counting it as
    one would make every patch look destructive.
    """

    call_id: str
    persona_id: str
    vertical: str
    caller_turns: list[str]
    was_passing: bool


class Validator:
    """Replays history against each candidate and applies the promotion gate."""

    FIX_SAMPLES = 3
    """How many times the triggering case is replayed before we believe a
    candidate fixed it.

    One replay against a stochastic agent is a coin flip, and a coin flip
    dressed as a promotion gate is worse than no gate: it promotes whichever
    candidate got lucky and kills the ones that didn't. The first real run of
    this loop promoted a transition-condition tweak — which cannot affect
    fabrication at all — while killing two patches that correctly said "consult
    the knowledge base before answering". Majority-of-three turns that from
    noise into evidence.
    """

    def __init__(self, executor: GraphExecutor, oracle, kb_text: str,
                 max_workers: int = 6):
        self.oracle = oracle
        self.kb_text = kb_text
        self.max_workers = max_workers

        # Validation runs its own low-temperature executor. Variance belongs in
        # LIVE calls, where it produces the diverse failures a population needs
        # to select over. It does not belong in measurement — here it is purely
        # a source of false regressions and false fixes.
        import copy as _copy

        cfg = _copy.copy(executor.config)
        cfg.temperature = 0.2
        self.executor = GraphExecutor(executor.kb, cfg)
        self._baseline_cache: dict[int, list[ReplayCase]] = {}

    def validate(self, patch: WorkflowPatch, base_graph: dict,
                 triggering_case: ReplayCase,
                 history: list[ReplayCase],
                 target_node_id: str | None = None) -> Validation:
        try:
            patched = apply_patch(base_graph, patch.mutation)
        except ValueError as exc:
            return Validation(fixes_new_failure=False, historical_cases_tested=0,
                              historical_cases_passed=0, regressions_introduced=0,
                              notes=f"patch could not be applied: {exc}")

        fixes_new = self._fix_confirmed(
            patched, triggering_case,
            node_id=target_node_id or patch.mutation.target_node_id(),
            failure_type=patch.signature.failure_type,
        )

        regression_pool = self._baseline_pool(base_graph, history)
        results: list[bool] = []
        if regression_pool:
            with ThreadPoolExecutor(max_workers=self.max_workers) as pool:
                # Majority-voted on both sides. An asymmetric comparison — a
                # stable baseline against a single-sample retest — would still
                # let one unlucky sample manufacture a regression and kill an
                # otherwise good patch.
                results = list(pool.map(
                    lambda c: self._case_passes_repeatedly(patched, c), regression_pool))

        passed = sum(1 for r in results if r)
        regressions = sum(1 for r in results if not r)

        notes = (f"replayed {len(regression_pool)} previously-passing case(s); "
                 f"{passed} still pass, {regressions} regressed. "
                 f"Triggering failure {'fixed' if fixes_new else 'NOT fixed'}.")

        return Validation(
            fixes_new_failure=fixes_new,
            historical_cases_tested=len(regression_pool),
            historical_cases_passed=passed,
            regressions_introduced=regressions,
            notes=notes,
        )

    def _fix_confirmed(self, graph: dict, case: ReplayCase, *,
                       node_id: str, failure_type) -> bool:
        """Did the SPECIFIC attributed failure stop happening?

        Not "is the whole call perfect now". That distinction is the difference
        between a gate that can be satisfied and one that cannot. A call has
        several turns; if any one of them fails for a reason unrelated to the
        patch — including reasons that are artefacts of the oracle, such as
        Senso returning "No results found" for a short follow-up like "just
        ballpark it" — then a whole-call criterion is permanently unsatisfiable
        and every candidate is marked NOT FIXED regardless of merit. That is
        exactly what happened: five consecutive generations, fifteen candidates,
        zero promotions, almost all reading "Triggering failure NOT fixed".

        So we ask the narrow question the patch is actually accountable for:
        does the attributed node still produce the attributed failure type?
        Majority-of-N, same as everywhere else.
        """
        def once() -> bool:
            try:
                trace = self.executor.run_call(
                    graph, case.caller_turns,
                    call_id=f"fixchk_{case.call_id}",
                    workflow_version="candidate",
                    vertical=case.vertical, persona_id=case.persona_id,
                )
                for turn in trace.turns:
                    turn.verdict = self.oracle.score_turn(turn, self.kb_text)
                # The node may not even be reached now — a patch that reroutes
                # away from a broken node has legitimately fixed the failure.
                for turn in trace.turns:
                    if turn.node_id != node_id or not turn.verdict:
                        continue
                    if turn.verdict.failure_type == failure_type:
                        return False
                return True
            except Exception:
                return False

        with ThreadPoolExecutor(max_workers=self.FIX_SAMPLES) as pool:
            votes = list(pool.map(lambda _: once(), range(self.FIX_SAMPLES)))
        return sum(votes) > self.FIX_SAMPLES // 2

    def _baseline_pool(self, base_graph: dict, history: list[ReplayCase]) -> list[ReplayCase]:
        """Cases that pass on the UNPATCHED graph, measured under validation
        conditions.

        `ReplayCase.was_passing` records how a call went live — temperature 0.7,
        through the live executor. Validation replays at temperature 0.2. Those
        are different conditions, so a case can be marked "was passing" and then
        fail its retest for reasons that have nothing to do with the candidate
        patch. Attributing that to the patch makes every candidate look
        destructive, which is exactly what happened: six consecutive candidates
        died having "regressed" 3-4 of 4 cases, and the promotion gate was
        measuring our own measurement inconsistency rather than the patches.

        So the baseline is re-established here, against the same executor and
        the same temperature the candidates will face. Cached per graph, because
        it costs a full replay pass and the base graph does not change within a
        generation.
        """
        key = id(base_graph)
        if key in self._baseline_cache:
            return self._baseline_cache[key]

        candidates = [c for c in history if c.was_passing]
        if not candidates:
            self._baseline_cache[key] = []
            return []

        with ThreadPoolExecutor(max_workers=self.max_workers) as pool:
            holds = list(pool.map(
                lambda c: self._case_passes_repeatedly(base_graph, c), candidates))

        pool_cases = [c for c, ok in zip(candidates, holds) if ok]
        dropped = len(candidates) - len(pool_cases)
        if dropped:
            log.info("baseline: %d/%d live-passing cases also pass under "
                     "validation conditions (%d dropped as unstable)",
                     len(pool_cases), len(candidates), dropped)
        self._baseline_cache[key] = pool_cases
        return pool_cases

    def _case_passes_repeatedly(self, graph: dict, case: ReplayCase) -> bool:
        """Majority-of-N. Samples run concurrently so this costs latency, not
        wall clock."""
        with ThreadPoolExecutor(max_workers=self.FIX_SAMPLES) as pool:
            votes = list(pool.map(
                lambda _: self._case_passes(graph, case),
                range(self.FIX_SAMPLES),
            ))
        return sum(votes) > self.FIX_SAMPLES // 2

    def _case_passes(self, graph: dict, case: ReplayCase) -> bool:
        try:
            trace = self.executor.run_call(
                graph, case.caller_turns,
                call_id=f"replay_{case.call_id}",
                workflow_version="candidate",
                vertical=case.vertical,
                persona_id=case.persona_id,
            )
            for turn in trace.turns:
                turn.verdict = self.oracle.score_turn(turn, self.kb_text)
            return not trace.failing_turns
        except Exception:
            # A candidate that crashes the executor is not a viable candidate.
            # Treat it as a failure rather than letting the exception kill the
            # generation — the loop must not be stoppable by one bad patch.
            return False

    def select(self, candidates: list[WorkflowPatch], base_graph: dict,
               triggering_case: ReplayCase,
               history: list[ReplayCase],
               target_node_id: str | None = None) -> tuple[WorkflowPatch | None, list[WorkflowPatch]]:
        """Validate all candidates, mark survivors and extinctions.

        Returns (promoted_or_None, all_candidates_with_status_set). Extinct
        candidates are returned, never discarded — the dead ones are the
        clearest evidence that selection happened.
        """
        for patch in candidates:
            patch.validation = self.validate(
                patch, base_graph, triggering_case, history,
                target_node_id=target_node_id)

        viable = [p for p in candidates if p.validation and p.validation.promotable]

        # EXTINCT is reserved for candidates the gate actually rejected.
        for patch in candidates:
            patch.status = (PatchStatus.VIABLE if patch in viable
                            else PatchStatus.EXTINCT)

        if not viable:
            log.info("all %d candidates killed by the gate", len(candidates))
            return None, candidates

        winner = max(viable, key=lambda p: p.validation.confidence)
        winner.status = PatchStatus.PROMOTED
        log.info("%d killed, %d viable, promoted %s",
                 len(candidates) - len(viable), len(viable), winner.patch_id)
        return winner, candidates
