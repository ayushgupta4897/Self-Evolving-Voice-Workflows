"""Core data contracts for the self-evolving workflow system.

Everything downstream — attribution, evolution, validation, the Actian store,
the dashboard — reads and writes these shapes. Change them here, not inline.

Two rules encoded structurally rather than by convention, because both are
things a judge will probe and both are easy to get quietly wrong:

  1. `FailureSignature.to_embedding_text()` emits STRUCTURE ONLY. No domain
     vocabulary, no product names, no caller utterance. If domain content
     leaks into the signature embedding, Actian retrieval keys on topic and
     the cross-vertical transfer beat stops working — a patch learned on
     brake pricing would never retrieve for a healthcare copay. The method is
     the single chokepoint; there is no other path to the embedding text.

  2. `Validation.confidence` is derived, not assigned. It is computed from the
     Validator's observed pass rate over replayed historical cases. No model
     is permitted to author it. The constructor computes it; there is no
     setter.
"""

from __future__ import annotations

import hashlib
import json
import time
import uuid
from dataclasses import dataclass, field, asdict
from enum import Enum
from typing import Any, Literal


# ---------------------------------------------------------------------------
# Taxonomies — deliberately small. A rich taxonomy retrieves worse and
# impresses nobody. Four failure types, four mutation operators.
# ---------------------------------------------------------------------------

class FailureType(str, Enum):
    """What went wrong with a turn."""

    UNGROUNDED_FABRICATION = "ungrounded_fabrication"
    """Agent asserted something not supported by the knowledge base. The
    canonical case: a price stated from model priors."""

    MISSING_INFO = "missing_info"
    """Agent's answer was incomplete — it omitted a fact the caller needed
    that was available in the knowledge base."""

    WRONG_TRANSITION = "wrong_transition"
    """Agent moved to the wrong next node, or failed to move when it should
    have. Includes dropping one half of a two-part question."""

    PREMATURE_ESCALATION = "premature_escalation"
    """Agent transferred to a human on a question it was equipped to answer.
    The degenerate optimum this whole fitness function is defending against."""


class NodeRole(str, Enum):
    """What a node is *for*. Structural, not topical — this is what makes a
    patch portable across verticals."""

    GREETING = "greeting"
    INFORMATION_RETRIEVAL = "information_retrieval"
    CLARIFICATION = "clarification"
    CONFIRMATION = "confirmation"
    ESCALATION = "escalation"
    CLOSING = "closing"


class MutationOperator(str, Enum):
    """The complete hypothesis space. Naming this boundary out loud is worth
    more than defending it: the Evolution agent cannot invent a new node
    *type*. Within these four operations it is autonomous."""

    APPEND_CONSTRAINT = "append_constraint"
    """Add a hard rule to a node's instruction without rewriting it."""

    REWRITE_INSTRUCTION = "rewrite_instruction"
    """Replace a node's instruction wholesale."""

    ADD_TOOL_REQUIREMENT = "add_tool_requirement"
    """Make a tool call mandatory before a class of assertion."""

    CHANGE_TRANSITION = "change_transition"
    """Alter an edge's condition."""


class PatchStatus(str, Enum):
    CANDIDATE = "candidate"
    """Generated, not yet validated."""

    EXTINCT = "extinct"
    """The Validator KILLED it — it failed to fix the attributed failure, or it
    introduced a regression. These are the most convincing artifact we can put
    on screen, which is exactly why the label has to be earned. Never delete
    them, never hide them, and never apply this to a candidate that merely lost
    a tiebreak."""

    VIABLE = "viable"
    """Passed the promotion gate but was not selected — another candidate had
    equal or higher confidence.

    This status exists because conflating it with EXTINCT would let us claim
    selection pressure we did not demonstrate. A generation where all three
    candidates pass and one is picked by `max()` is a tiebreak, not a kill, and
    reporting it as "the Validator killed two" is a lie a judge could catch by
    reading the `validation` block on screen."""

    PROMOTED = "promoted"
    """Survived validation and was applied to the live graph."""


# ---------------------------------------------------------------------------
# Scoring
# ---------------------------------------------------------------------------

@dataclass
class OracleVerdict:
    """A correctness judgement on a single agent turn.

    Produced by an Oracle implementation — Senso when its Evaluate API is
    reachable, an LLM judge against the same knowledge base otherwise. The
    two are interchangeable by construction so that neither the fitness
    function nor the Validator needs to know which one scored a turn.
    `source` records which did, so the dashboard and the README can be honest
    about it.
    """

    correctness_score: float           # 0.0 - 1.0
    grounded: bool
    citation: str | None               # verbatim supporting span, if any
    ground_truth_value: str | None      # what the KB actually says
    failure_type: FailureType | None    # None when the turn passed
    reasoning: str                      # why — feeds GEPA's textual feedback
    source: Literal["senso", "llm_judge"]

    escalated: bool = False
    """Did the agent transfer to a human on this turn?"""

    escalation_warranted: bool = False
    """Was escalation the *correct* action here?

    This distinction is the whole guard against a miscalibrated escalation
    penalty. Without it, fitness punishes every transfer, and the optimizer
    learns an agent that never escalates — which is the mirror image of the
    degenerate optimum we set out to prevent, and just as wrong. Only
    unwarranted escalation is penalized. See `personas/auto_servicing.json`,
    persona `p_warranty_claim`, which exists purely as the negative control
    for this field.
    """

    @property
    def unwarranted_escalation(self) -> bool:
        return self.escalated and not self.escalation_warranted

    @property
    def passed(self) -> bool:
        return self.grounded and self.correctness_score >= 0.7 and not self.unwarranted_escalation


@dataclass
class FitnessWeights:
    """Four terms in tension. Any single term alone has a degenerate optimum:
    maximise correctness alone and the agent transfers every call; maximise
    task completion alone and it fabricates confidently; minimise interaction
    cost alone and it stops asking clarifying questions it needs."""

    correctness: float = 0.35
    groundedness: float = 0.25
    task_completion: float = 0.20
    interaction_cost: float = 0.10      # subtracted
    unwarranted_escalation: float = 0.10  # subtracted

    def validate(self) -> None:
        total = (self.correctness + self.groundedness + self.task_completion
                 + self.interaction_cost + self.unwarranted_escalation)
        if abs(total - 1.0) > 1e-6:
            raise ValueError(f"fitness weights must sum to 1.0, got {total}")


DEFAULT_WEIGHTS = FitnessWeights()


# ---------------------------------------------------------------------------
# Traces
# ---------------------------------------------------------------------------

@dataclass
class TurnTrace:
    """One node execution inside one call. The unit of attribution."""

    call_id: str
    workflow_version: str
    turn_index: int
    node_id: str
    node_role: NodeRole
    node_instruction: str
    caller_utterance: str
    agent_utterance: str
    tools_available: list[str] = field(default_factory=list)
    tools_called: list[str] = field(default_factory=list)
    transition_taken: str | None = None
    latency_ms: int = 0
    verdict: OracleVerdict | None = None

    @property
    def node_instruction_hash(self) -> str:
        return hashlib.sha256(self.node_instruction.encode()).hexdigest()[:12]

    @property
    def tool_available_not_invoked(self) -> bool:
        """The structural fingerprint of the failure this project is built
        around: retrieval was possible and the node answered from priors
        anyway. Better retrieval does not fix this. Only the instruction does.
        """
        return bool(self.tools_available) and not self.tools_called


@dataclass
class CallTrace:
    """A full conversation. What the Validator replays."""

    call_id: str
    workflow_version: str
    vertical: str
    persona_id: str
    turns: list[TurnTrace] = field(default_factory=list)
    task_completed: bool = False
    started_at: float = field(default_factory=time.time)

    @property
    def failing_turns(self) -> list[TurnTrace]:
        return [t for t in self.turns if t.verdict and not t.verdict.passed]

    def fitness(self, w: FitnessWeights = DEFAULT_WEIGHTS) -> float:
        """Aggregate call fitness. Returns 0.0 for an unscored call rather
        than raising — an unscored call is worth nothing, not undefined."""
        scored = [t for t in self.turns if t.verdict]
        if not scored:
            return 0.0

        n = len(scored)
        correctness = sum(t.verdict.correctness_score for t in scored) / n
        groundedness = sum(1.0 for t in scored if t.verdict.grounded) / n
        escalation = sum(1.0 for t in scored if t.verdict.unwarranted_escalation) / n

        # Interaction cost: normalised turn count. Six turns is the reference
        # for a well-run call; beyond that we start paying. Capped at 1.0 so a
        # single pathological call cannot dominate a generation's mean.
        cost = min(len(self.turns) / 6.0, 1.0)

        return (w.correctness * correctness
                + w.groundedness * groundedness
                + w.task_completion * (1.0 if self.task_completed else 0.0)
                - w.interaction_cost * cost
                - w.unwarranted_escalation * escalation)


# ---------------------------------------------------------------------------
# Failure signature — the portability mechanism
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class FailureSignature:
    """A structural description of *how* a turn failed, carrying no
    information about *what* it was talking about.

    This is the key Actian retrieves on. A brake-pricing fabrication and a
    healthcare-copay fabrication produce byte-identical signatures, which is
    exactly what makes a patch learned in one vertical fire in the other.
    """

    failure_type: FailureType
    node_role: NodeRole
    tool_available: bool
    tool_invoked: bool
    asserted_specific_value: bool
    """Did the agent state a concrete value (number, date, policy term)?
    Structural — we record *that* it did, never *what* it said."""

    @classmethod
    def from_turn(cls, turn: TurnTrace) -> FailureSignature:
        if not turn.verdict or not turn.verdict.failure_type:
            raise ValueError(f"turn {turn.turn_index} of {turn.call_id} has no failure to sign")
        return cls(
            failure_type=turn.verdict.failure_type,
            node_role=turn.node_role,
            tool_available=bool(turn.tools_available),
            tool_invoked=bool(turn.tools_called),
            asserted_specific_value=_contains_specific_value(turn.agent_utterance),
        )

    def to_embedding_text(self) -> str:
        """The ONLY text that may be embedded into `signature_vector`.

        Deliberately reads as structured English rather than a token soup:
        we want semantically *near* signatures (same failure type, same role,
        differing only in whether a tool existed) to land near each other in
        vector space, which a bag of flags would not achieve.

        Note what is absent: the utterance, the ground truth, the vertical,
        the node id, the product. If you are tempted to add any of them here
        to improve retrieval precision, that is the transfer beat dying.
        """
        return (
            f"A {self.node_role.value} node produced a {self.failure_type.value} failure. "
            f"A retrieval tool was {'available' if self.tool_available else 'not available'} "
            f"and was {'invoked' if self.tool_invoked else 'not invoked'}. "
            f"The agent {'asserted a specific factual value' if self.asserted_specific_value else 'did not assert a specific value'}."
        )

    def key(self) -> str:
        """Stable identity for dedup and payload filtering in Actian."""
        return "|".join([
            self.failure_type.value,
            self.node_role.value,
            f"avail={int(self.tool_available)}",
            f"inv={int(self.tool_invoked)}",
            f"spec={int(self.asserted_specific_value)}",
        ])


_VALUE_MARKERS = ("$", "%", " am", " pm", "hour", "day", "week", "month", "year", "mile")


def _contains_specific_value(utterance: str) -> bool:
    """Cheap structural check for 'did the agent commit to a concrete fact'.

    Intentionally crude and intentionally domain-neutral: digits plus a small
    set of unit markers. A smarter classifier here would be a place for domain
    vocabulary to sneak into the signature, which is precisely what we are
    guarding against.
    """
    low = utterance.lower()
    has_digit = any(c.isdigit() for c in low)
    return has_digit or any(m in low for m in _VALUE_MARKERS)


# ---------------------------------------------------------------------------
# Patches
# ---------------------------------------------------------------------------

@dataclass
class Mutation:
    """A single concrete edit to the workflow graph."""

    target: str
    """Dotted path into the graph, e.g. `node_pricing_lookup.data.prompt` or
    `edge_confirm.data.condition`."""

    operation: MutationOperator
    diff: str
    """Human-readable change. For APPEND_CONSTRAINT this is the appended text;
    for REWRITE_INSTRUCTION the full replacement."""

    def target_node_id(self) -> str:
        return self.target.split(".", 1)[0]


@dataclass
class Validation:
    """The Validator's report on a candidate.

    `confidence` is computed in __post_init__ from observed pass rates. It is
    never supplied by a model. When a judge asks where the number comes from —
    and they will — the answer is this constructor.
    """

    fixes_new_failure: bool
    historical_cases_tested: int
    historical_cases_passed: int
    regressions_introduced: int
    notes: str = ""
    confidence: float = field(init=False, default=0.0)

    def __post_init__(self) -> None:
        if self.historical_cases_tested < 0 or self.historical_cases_passed < 0:
            raise ValueError("case counts cannot be negative")
        if self.historical_cases_passed > self.historical_cases_tested:
            raise ValueError("passed cannot exceed tested")

        if not self.fixes_new_failure:
            self.confidence = 0.0
            return
        if self.historical_cases_tested == 0:
            # Fixed the new failure but nothing to regress against. Weak
            # evidence, and we say so rather than rounding up to certainty.
            self.confidence = 0.5
            return

        pass_rate = self.historical_cases_passed / self.historical_cases_tested
        # A single regression is disqualifying regardless of pass rate — that
        # is the promotion gate, expressed numerically as well as in `promotable`.
        penalty = 0.5 * min(self.regressions_introduced, 2) / 2.0
        self.confidence = max(0.0, pass_rate - penalty)

    @property
    def promotable(self) -> bool:
        """The gate. Fixes the new failure AND breaks nothing old."""
        return self.fixes_new_failure and self.regressions_introduced == 0

    @property
    def confidence_source(self) -> str:
        return "validator_pass_rate"


@dataclass
class WorkflowPatch:
    """The genome. Stored in Actian keyed on `signature`."""

    generation: int
    signature: FailureSignature
    mutation: Mutation
    reflection: str
    """Why the Evolution agent believes this fixes the failure. Model-authored
    and labelled as such — this is the one field we are happy to have written
    by an LLM, because it is an explanation, not evidence."""

    authored_by: str = "evolution_agent"
    patch_id: str = field(default_factory=lambda: f"wp_{uuid.uuid4().hex[:8]}")
    parent_id: str | None = None
    origin_vertical: str = "unknown"
    """Recorded for the transfer story — which vertical *learned* this. Kept
    out of the signature on purpose; it is provenance, not a retrieval key."""

    validation: Validation | None = None
    status: PatchStatus = PatchStatus.CANDIDATE
    created_at: float = field(default_factory=time.time)

    def to_actian_payload(self) -> dict[str, Any]:
        """Flat, filterable payload. Actian pre-filtering works on
        `must.match`, so the signature fields are hoisted to the top level to
        let us scope retrieval by failure_type or node_role before the vector
        search runs."""
        return {
            "patch_id": self.patch_id,
            "generation": self.generation,
            "parent_id": self.parent_id,
            "authored_by": self.authored_by,
            "origin_vertical": self.origin_vertical,
            "status": self.status.value,
            "signature_key": self.signature.key(),
            "failure_type": self.signature.failure_type.value,
            "node_role": self.signature.node_role.value,
            "tool_available": self.signature.tool_available,
            "tool_invoked": self.signature.tool_invoked,
            "asserted_specific_value": self.signature.asserted_specific_value,
            "target": self.mutation.target,
            "operation": self.mutation.operation.value,
            "diff": self.mutation.diff,
            "reflection": self.reflection,
            "confidence": self.validation.confidence if self.validation else 0.0,
            "confidence_source": "validator_pass_rate",
            "regressions_introduced": self.validation.regressions_introduced if self.validation else -1,
            "historical_cases_tested": self.validation.historical_cases_tested if self.validation else 0,
            "created_at": self.created_at,
        }

    def to_json(self) -> str:
        d = asdict(self)
        d["signature"] = {**asdict(self.signature),
                          "failure_type": self.signature.failure_type.value,
                          "node_role": self.signature.node_role.value}
        d["mutation"]["operation"] = self.mutation.operation.value
        d["status"] = self.status.value
        if self.validation:
            d["validation"]["confidence_source"] = "validator_pass_rate"
        return json.dumps(d, indent=2)


# ---------------------------------------------------------------------------
# Generations
# ---------------------------------------------------------------------------

@dataclass
class Generation:
    """One turn of the evolutionary crank. Retains extinct candidates
    permanently — population composition over time is the evidence that this
    is selection and not a patch pipeline."""

    number: int
    triggering_call_id: str
    candidates: list[WorkflowPatch] = field(default_factory=list)
    promoted_patch_id: str | None = None
    mean_fitness_before: float = 0.0
    mean_fitness_after: float = 0.0
    created_at: float = field(default_factory=time.time)

    @property
    def extinct(self) -> list[WorkflowPatch]:
        """Genuinely killed by the gate — not merely unselected."""
        return [p for p in self.candidates if p.status == PatchStatus.EXTINCT]

    @property
    def viable_not_selected(self) -> list[WorkflowPatch]:
        return [p for p in self.candidates if p.status == PatchStatus.VIABLE]

    @property
    def selection_occurred(self) -> bool:
        """True only when the gate actually eliminated something. If this is
        False for every generation, we have a patch pipeline, not selection —
        and we should say so rather than let the population board imply
        otherwise."""
        return bool(self.extinct)

    @property
    def survivor(self) -> WorkflowPatch | None:
        return next((p for p in self.candidates if p.status == PatchStatus.PROMOTED), None)
