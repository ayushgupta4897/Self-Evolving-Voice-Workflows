"""The scoring oracle — the correctness signal the whole evolutionary loop runs on.

Every fitness number, every failure signature, every promotion decision traces
back to an `OracleVerdict` produced here. Two implementations sit behind one
`Oracle` protocol so that nothing downstream needs to know which one scored a
turn:

  * `SensoOracle`    — source="senso".     PRIMARY. Senso owns the verified
    knowledge, the retrieval, the citation and the escalation policy; an LLM is
    used for one step only, deciding whether the agent agreed with Senso.
  * `LLMJudgeOracle` — source="llm_judge". FALLBACK, for when Senso is erroring,
    slow, or out of credits (free tier — 402). Judges against the KB text
    directly. Interchangeable by construction: `tests/test_oracle.py` runs the
    same four assertions against both.

`get_oracle()` picks one and says so, loudly, in a banner. We state on stage and
in the README which oracle scored a given verdict; it must never be ambiguous.

Three things in here are load-bearing and easy to get quietly wrong:

  1. `escalation_warranted` is judged from what the CALLER asked, using the
     enumerated list in section 8 of the KB — never from whether the agent
     happened to escalate. Without that separation the escalation penalty
     punishes every transfer, and the optimizer learns an agent that never
     escalates: the mirror image of the degenerate optimum we set out to stop.
     See `personas/auto_servicing.json` -> `p_warranty_claim`, the negative
     control (warranty CLAIM => escalation correct) against `p_warranty_transfer`
     (warranty DURATION => escalation is a failure).

  2. Citations are verified in Python, not trusted from the model — against the
     KB file for the LLM judge, and against the passages Senso actually
     retrieved for `SensoOracle`, which may only cite what its retrieval
     surfaced. `_verify_citation` re-derives the span from the source so it is
     verbatim by construction, recovers reflowed near-misses, and demotes
     `grounded` when the cited text is nowhere to be found.

  3. `reasoning` is GEPA's textual feedback signal downstream. "The answer was
     wrong" is worthless. The prompt demands the diagnostic form:
     stated-X / KB-says-Y / here-is-the-confusion.

A malformed model response degrades to a low-confidence verdict. It never
raises into the evolution loop — a crashed generation is worse than a bad score.
"""

from __future__ import annotations

import difflib
import json
import logging
import os
import re
import textwrap
import time
from dataclasses import dataclass
from typing import Any, Protocol, runtime_checkable

from core.schemas import FailureType, OracleVerdict, TurnTrace

log = logging.getLogger("oracle")

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

DEFAULT_MODEL = "gpt-5.1"
"""Overridable with ORACLE_MODEL / OPENAI_MODEL. If the configured model is not
available to the key, we walk `_MODEL_FALLBACKS` rather than dying mid-demo."""

_MODEL_FALLBACKS = ["gpt-5.1", "gpt-5", "gpt-4.1", "gpt-4o"]

PIONEER_MODELS = ["gpt-4o", "gpt-5-mini", "gpt-4o-mini"]
"""Pioneer is an OpenAI-compatible gateway. Its /v1/models advertises
`structured_outputs: false`, but strict json_schema was verified working against
all three of these — so we try the schema anyway and fall back if refused."""

SENSO_DEFAULT_BASE_URL = "https://apiv2.senso.ai/api/v1"
SENSO_KEY_ENV = "SENSO_API_KEY"

_MAX_KB_CHARS = 60_000
_JUDGE_RETRIES = 3


def _load_dotenv() -> None:
    """Best-effort .env.local load, from cwd and from the repo root so this
    module behaves the same however it is imported.

    Keys live in the environment. We never hardcode one and we never log one.
    A real environment variable always wins over a file (`setdefault`).
    """
    repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    for root in dict.fromkeys([os.getcwd(), repo_root]):
        for name in (".env.local", ".env"):
            path = os.path.join(root, name)
            if not os.path.exists(path):
                continue
            try:
                with open(path, encoding="utf-8") as fh:
                    for line in fh:
                        line = line.strip()
                        if not line or line.startswith("#") or "=" not in line:
                            continue
                        k, v = line.split("=", 1)
                        os.environ.setdefault(k.strip(), v.strip().strip("'\""))
            except OSError:
                pass


_load_dotenv()


# ---------------------------------------------------------------------------
# Interface
# ---------------------------------------------------------------------------

@runtime_checkable
class Oracle(Protocol):
    """The correctness signal. Two implementations, one contract."""

    def score_turn(self, turn: TurnTrace, kb_text: str) -> OracleVerdict:
        ...

    def health(self) -> tuple[bool, str]:
        """(usable, human-readable reason). Cheap. Never raises."""
        ...


# ---------------------------------------------------------------------------
# Shared judging prompt
# ---------------------------------------------------------------------------

_VERDICT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": [
        "correctness_score", "grounded", "citation", "ground_truth_value",
        "failure_type", "reasoning", "escalated", "caller_request_category",
        "escalation_warranted",
    ],
    "properties": {
        "correctness_score": {
            "type": "number",
            "description": "0.0-1.0. 1.0 = every assertion matches the KB exactly. "
                           "<=0.2 = a confident specific claim that contradicts the KB. "
                           "A correct refusal to answer an unlisted item scores high, not low.",
        },
        "grounded": {
            "type": "boolean",
            "description": "True only if every factual assertion in the agent utterance is "
                           "supported by a span you can quote verbatim from the KB. An agent "
                           "that asserts nothing factual (pure clarifying question, or a "
                           "correct handoff) is grounded=true by default.",
        },
        "citation": {
            "type": ["string", "null"],
            "description": "The VERBATIM supporting span copied character-for-character out of "
                           "the KB text. Do not paraphrase, do not reformat, do not add "
                           "ellipses. Null if not grounded in any specific span.",
        },
        "ground_truth_value": {
            "type": ["string", "null"],
            "description": "What the KB actually says on the point at issue, stated plainly "
                           "(e.g. '$285 for a standard sedan front axle'). Null if the turn "
                           "asserted nothing checkable.",
        },
        "failure_type": {
            # A plain string enum with an explicit "none" member rather than a
            # nullable enum: null-inside-enum is the one construct strict mode is
            # inconsistent about, and this costs nothing to normalise below.
            "type": "string",
            "enum": ["ungrounded_fabrication", "missing_info", "wrong_transition",
                     "premature_escalation", "none"],
            "description": "\"none\" when the turn passed. ungrounded_fabrication: asserted "
                           "something the KB does not support. missing_info: true but "
                           "incomplete, omitted a KB fact the caller needed. wrong_transition: "
                           "wrong next node, or dropped one half of a two-part question. "
                           "premature_escalation: transferred on a question section 8 says "
                           "must be answered directly.",
        },
        "reasoning": {
            "type": "string",
            "description": "2-4 sentences of ACTIONABLE diagnosis for an optimizer that will "
                           "rewrite the node instruction. Required form: what the agent stated, "
                           "what the KB says instead, and the specific confusion that produced "
                           "the gap. Bad: 'the answer was wrong'. Good: 'stated $340 for a "
                           "sedan front brake job; KB section 1 gives $285 for standard sedan "
                           "and $340 is the SUV price - the node conflated vehicle classes.'",
        },
        "escalated": {
            "type": "boolean",
            "description": "Did the AGENT UTTERANCE transfer, hand off, or route this caller to "
                           "a human/manager/service advisor/specialist, or promise a callback "
                           "from one? Offering to book an in-person appointment is NOT "
                           "escalation. Saying 'you'll need to bring the vehicle in' is NOT "
                           "escalation. Only a handoff to another person is.",
        },
        "caller_request_category": {
            "type": "string",
            "enum": [
                "warranty_claim_decision", "invoice_dispute", "safety_incident",
                "financing_eligibility", "service_not_listed_in_section_1",
                "answerable_from_kb",
            ],
            "description": "Classify what the CALLER asked for, independent of what the agent "
                           "did. The first five are exactly section 8's escalation list. "
                           "Everything else - price, hours, location, policy, WARRANTY "
                           "DURATION or transferability, appointment availability, payment "
                           "methods - is 'answerable_from_kb'.",
        },
        "escalation_warranted": {
            "type": "boolean",
            "description": "True if and only if caller_request_category is one of the five "
                           "section 8 categories. Derive it from that field. Judge the "
                           "CALLER's request, never the agent's behaviour.",
        },
    },
}

_SYSTEM_PROMPT = """\
You are the correctness oracle for an automated phone agent. You score ONE agent turn \
against a knowledge base that is the sole authority. You are not a helpful assistant \
here; you are a strict grader whose output drives an optimizer.

THE KNOWLEDGE BASE IS THE ONLY TRUTH.
Anything not stated in the KB is not company policy. If the agent asserts a figure, \
rule, or availability that is not in the KB, that is ungrounded_fabrication no matter \
how plausible it sounds. Your own world knowledge about car servicing is inadmissible.

ESCALATION - READ SECTION 8 OF THE KB AND APPLY IT LITERALLY.
This is the single most important judgement you make. Two separate questions:

  (a) escalated: did the AGENT hand this caller to a human? Look only at the agent
      utterance. Transferring, "let me get you my manager", "I'll have a service
      advisor call you back" = escalated. Booking an appointment, asking the caller
      to bring the vehicle in, or answering the question = NOT escalated.

  (b) escalation_warranted: would escalation be CORRECT for what the CALLER asked?
      Section 8 enumerates exactly five warranting cases:
        1. a warranty CLAIM DECISION (caller wants a repair honoured / paid under
           warranty; also see section 7: never agree to a warranty claim on the phone)
        2. a dispute over a charge on a past invoice
        3. a safety incident, injury, or vehicle damage at a Meridian location
        4. in-house financing eligibility
        5. a service not listed in section 1
      Section 8 also states explicitly that escalation is NOT appropriate for price,
      hours, location, policy, WARRANTY DURATION, appointment availability, or payment
      methods - those must be answered directly, and transferring such a caller is a
      failure rather than a safe default.

      The distinction that matters most: "how long is my brake warranty / does it
      transfer to a buyer" is warranty DURATION and is answerable => warranted=false.
      "My brakes squeak four months on, honour the warranty and fix it free" is a
      warranty CLAIM DECISION => warranted=true.

  Judge (b) from the caller's request ALONE. Set escalation_warranted correctly even
  when the agent did not escalate - it describes what the right action was, not what
  happened.

CITATION.
Copy the supporting span out of the KB character-for-character, including currency
symbols, table pipes and dashes if it is a table row. It is machine-checked for
verbatim presence in the source. A paraphrase fails that check and costs the turn its
groundedness.

SCORING NOTES.
- Refusing to quote an unlisted service, or asking for the vehicle class before
  quoting a class-dependent price, is CORRECT behaviour per section 7. Score it high.
- A correct, well-handled escalation on one of the five section 8 cases is a PASS:
  high correctness, grounded, failure_type null.
- An answer that is true but omits a KB fact the caller explicitly asked for is
  missing_info, not a pass.

Return only the structured object.\
"""


def _build_user_prompt(turn: TurnTrace, kb_text: str) -> str:
    kb = kb_text if len(kb_text) <= _MAX_KB_CHARS else kb_text[:_MAX_KB_CHARS] + "\n[...KB truncated...]"
    tools_avail = ", ".join(turn.tools_available) or "(none)"
    tools_called = ", ".join(turn.tools_called) or "(none)"
    retrieval_note = ""
    if turn.tool_available_not_invoked:
        retrieval_note = (
            "\nNOTE FOR YOUR REASONING: a retrieval tool was available to this node and "
            "was NOT invoked. If the agent asserted a specific value anyway, say so "
            "explicitly - that is the structural failure the optimizer needs named.\n"
        )

    return f"""\
=== KNOWLEDGE BASE (the only admissible source of truth) ===
{kb}
=== END KNOWLEDGE BASE ===

=== TURN UNDER JUDGEMENT ===
Node id:          {turn.node_id}
Node role:        {turn.node_role.value}
Node instruction: {turn.node_instruction}
Tools available:  {tools_avail}
Tools called:     {tools_called}
Transition taken: {turn.transition_taken or "(none)"}

CALLER SAID:
{turn.caller_utterance}

AGENT SAID:
{turn.agent_utterance}
=== END TURN ==={retrieval_note}

Score this turn. Classify the caller's request against section 8 before deciding
escalation_warranted.\
"""


# ---------------------------------------------------------------------------
# Verification helpers — the parts we refuse to take a model's word for
# ---------------------------------------------------------------------------

def _norm(s: str) -> str:
    """Whitespace-insensitive normalisation for containment checks."""
    return re.sub(r"\s+", " ", s).strip().lower()


def _norm_with_map(s: str) -> tuple[str, list[int]]:
    """Normalise like `_norm` while keeping, for each output character, the index
    it came from in the original string. That is what lets us hand back a span
    that is verbatim in the *original* KB after matching in normalised space."""
    out: list[str] = []
    idx: list[int] = []
    prev_space = True
    for i, ch in enumerate(s):
        if ch.isspace():
            if prev_space:
                continue
            out.append(" ")
            idx.append(i)
            prev_space = True
        else:
            out.append(ch.lower())
            idx.append(i)
            prev_space = False
    while out and out[-1] == " ":
        out.pop()
        idx.pop()
    return "".join(out), idx


def _verify_citation(citation: str | None, kb_text: str) -> tuple[str | None, bool, str]:
    """Confirm a cited span actually exists in the KB.

    Returns (citation, verified, note).

    Judges reliably mangle citations in two harmless ways: they reflow a wrapped
    line, and they staple a fragment from elsewhere onto a real span. Neither is
    fabrication, and rejecting them would make groundedness a measure of the
    judge's copy-paste fidelity instead of the agent's honesty. So we look for
    the longest contiguous region of the KB the citation actually overlaps, and
    if it is substantial we return *that region, verbatim from the KB*.

    What we still refuse: a citation with no substantial contiguous overlap
    anywhere in the KB. That is a model pointing at evidence that does not
    exist, and it demotes `grounded`.
    """
    if not citation or not citation.strip():
        return None, False, ""

    cit = citation.strip()
    ncit = _norm(cit)
    nkb, kbmap = _norm_with_map(kb_text)
    if not ncit or not nkb:
        return None, False, " [CITATION UNVERIFIED: empty after normalisation]"

    def _kb_slice(n_start: int, n_len: int, whole_lines: bool) -> str:
        """Map a match in normalised space back to the original KB text, so what
        we return is verbatim by construction rather than by the model's care."""
        start, end = kbmap[n_start], kbmap[n_start + n_len - 1]
        if whole_lines:
            start = kb_text.rfind("\n", 0, start) + 1
            nl = kb_text.find("\n", end)
            end = (len(kb_text) if nl == -1 else nl) - 1
        return kb_text[start:end + 1].strip()

    # 1. Clean hit modulo whitespace. Still re-derive the span from the KB: the
    #    model's copy may differ from the source in line breaks, which would make
    #    `citation in kb_text` false downstream.
    pos = nkb.find(ncit)
    if pos != -1:
        exact = _kb_slice(pos, len(ncit), whole_lines=False)
        note = "" if exact == cit else " [citation re-derived verbatim from the KB source]"
        return exact, True, note

    # 2. Longest contiguous overlap. Catches the judge stapling a fragment from
    #    elsewhere onto an otherwise real span.
    block = difflib.SequenceMatcher(None, nkb, ncit, autojunk=False).find_longest_match(
        0, len(nkb), 0, len(ncit))
    if block.size >= max(30, int(0.5 * len(ncit))):
        snapped = _kb_slice(block.a, block.size, whole_lines=True)
        if snapped:
            return snapped, True, (
                f" [citation snapped to verbatim KB span, {block.size / len(ncit):.0%} of "
                f"the judge's quoted text matched contiguously]"
            )

    # 3. Whole-line fuzzy match. Catches a reflowed table row, where pipes and
    #    em-dashes are dropped and no single run of characters survives intact.
    best, best_ratio = None, 0.0
    for ln in kb_text.splitlines():
        if not ln.strip():
            continue
        r = difflib.SequenceMatcher(None, ncit, _norm(ln), autojunk=False).ratio()
        if r > best_ratio:
            best, best_ratio = ln.strip(), r
    if best is not None and best_ratio >= 0.72:
        return best, True, (
            f" [citation snapped to verbatim KB line, similarity {best_ratio:.2f}]"
        )

    return None, False, (
        f" [CITATION UNVERIFIED: the judge's cited span '{cit[:80]}' does not appear "
        f"in the KB; groundedness demoted]"
    )


_ESCALATION_CATEGORIES = {
    "warranty_claim_decision", "invoice_dispute", "safety_incident",
    "financing_eligibility", "service_not_listed_in_section_1",
}


def _coerce_verdict(raw: dict[str, Any], kb_text: str, source: str) -> OracleVerdict:
    """Turn a parsed model object into a consistent, schema-valid verdict.

    Everything here is a deterministic guard applied *after* the model, because
    these invariants are ones the fitness function depends on and we would rather
    enforce them in Python than hope for them in a prompt.
    """
    try:
        score = float(raw.get("correctness_score", 0.0))
    except (TypeError, ValueError):
        score = 0.0
    score = max(0.0, min(1.0, score))

    grounded = bool(raw.get("grounded", False))
    reasoning = str(raw.get("reasoning") or "").strip() or "(judge returned no reasoning)"

    citation, verified, note = _verify_citation(raw.get("citation"), kb_text)
    if grounded and raw.get("citation") and not verified:
        grounded = False
        reasoning += note
    elif note:
        reasoning += note

    ft_raw = raw.get("failure_type")
    failure_type: FailureType | None = None
    if isinstance(ft_raw, str) and ft_raw.lower() not in ("", "none", "null"):
        try:
            failure_type = FailureType(ft_raw.lower())
        except ValueError:
            failure_type = FailureType.UNGROUNDED_FABRICATION
            reasoning += f" [unrecognised failure_type '{ft_raw}' from judge, defaulted]"

    escalated = bool(raw.get("escalated", False))

    # escalation_warranted is derived from the caller-request category when the
    # judge supplied one. The category is the auditable artifact; the boolean is
    # a projection of it. If the two disagree, the category wins.
    category = raw.get("caller_request_category")
    if isinstance(category, str) and category:
        warranted = category in _ESCALATION_CATEGORIES
        if bool(raw.get("escalation_warranted", False)) != warranted:
            reasoning += (
                f" [escalation_warranted reconciled to {warranted} from "
                f"caller_request_category='{category}' per KB section 8]"
            )
    else:
        warranted = bool(raw.get("escalation_warranted", False))
        category = "unclassified"

    # An unwarranted transfer is premature_escalation by definition. This is the
    # guard the whole escalation penalty rests on; do not let the judge omit it.
    if escalated and not warranted and failure_type is None:
        failure_type = FailureType.PREMATURE_ESCALATION
        reasoning += (
            " [failure_type set to premature_escalation: agent transferred a caller whose "
            "request KB section 8 says must be answered directly]"
        )
    # ...and a correct escalation must not be scored as a failure of any kind.
    if escalated and warranted and failure_type is FailureType.PREMATURE_ESCALATION:
        failure_type = None
        reasoning += (
            " [premature_escalation cleared: this is one of section 8's warranting cases, "
            "so the transfer was the correct action]"
        )

    gt = raw.get("ground_truth_value")
    gt = str(gt) if gt is not None and str(gt).strip() else None

    if not reasoning.lower().startswith("[caller request:"):
        reasoning = f"[caller request: {category}] {reasoning}"

    return OracleVerdict(
        correctness_score=score,
        grounded=grounded,
        citation=citation,
        ground_truth_value=gt,
        failure_type=failure_type,
        reasoning=reasoning,
        source=source,  # type: ignore[arg-type]
        escalated=escalated,
        escalation_warranted=warranted,
    )


def _degraded_verdict(reason: str, source: str) -> OracleVerdict:
    """What we return when the judge is unusable for this turn.

    Deliberately conservative: correctness 0, ungrounded, no failure type. It
    marks the turn as unscoreable without inventing a failure signature that
    would seed the evolution loop with noise.
    """
    return OracleVerdict(
        correctness_score=0.0,
        grounded=False,
        citation=None,
        ground_truth_value=None,
        failure_type=None,
        reasoning=f"ORACLE ERROR - turn not scored: {reason}",
        source=source,  # type: ignore[arg-type]
        escalated=False,
        escalation_warranted=False,
    )


# ---------------------------------------------------------------------------
# Part A — the LLM judge. The unblocked path, and the one we default to.
# ---------------------------------------------------------------------------

@dataclass
class _Provider:
    """An OpenAI-wire-compatible backend for the judge."""

    name: str
    api_key: str
    base_url: str | None
    models: list[str]


def _providers() -> list[_Provider]:
    """Candidate backends, in preference order.

    Direct OpenAI first when a key is present; Pioneer (an OpenAI-compatible
    gateway) second. `health(deep=True)` picks the first that actually
    authenticates, so a present-but-revoked key demotes rather than dead-ends —
    which is exactly the situation this project shipped in.
    """
    out: list[_Provider] = []
    env_model = os.environ.get("ORACLE_MODEL") or os.environ.get("OPENAI_MODEL")

    if os.environ.get("OPENAI_API_KEY"):
        out.append(_Provider(
            "openai", os.environ["OPENAI_API_KEY"],
            os.environ.get("OPENAI_BASE_URL"),
            [env_model] if env_model else _MODEL_FALLBACKS,
        ))
    if os.environ.get("PIONEER_API_KEY"):
        out.append(_Provider(
            "pioneer", os.environ["PIONEER_API_KEY"],
            os.environ.get("PIONEER_BASE_URL", "https://api.pioneer.ai/v1"),
            [env_model] if env_model else PIONEER_MODELS,
        ))
    return out


class LLMJudgeOracle:
    """LLM judge over the KB text. source="llm_judge".

    Speaks the OpenAI wire protocol. Uses strict JSON-schema structured output
    where the backend supports it and degrades to plain JSON mode where it does
    not; either way `_parse_json_object` + `_coerce_verdict` are total, so a
    malformed response produces an unscored verdict rather than an exception.
    """

    source = "llm_judge"

    def __init__(
        self,
        model: str | None = None,
        api_key: str | None = None,
        base_url: str | None = None,
    ) -> None:
        if api_key:
            self._providers = [_Provider("explicit", api_key, base_url,
                                         [model] if model else _MODEL_FALLBACKS)]
        else:
            self._providers = _providers()
        self._provider: _Provider | None = self._providers[0] if self._providers else None
        self.model = (model or os.environ.get("ORACLE_MODEL")
                      or os.environ.get("OPENAI_MODEL")
                      or (self._provider.models[0] if self._provider else DEFAULT_MODEL))
        self._client: Any = None
        self._model_confirmed = False
        self._deep_health: tuple[bool, str] | None = None
        self._response_format: dict[str, Any] | None = None
        """None means "use strict json_schema". Latched to plain json_object only
        if the backend rejects the schema."""

    @property
    def provider(self) -> str:
        return self._provider.name if self._provider else "(none)"

    # -- plumbing ---------------------------------------------------------

    def _get_client(self) -> Any:
        if self._client is not None:
            return self._client
        from openai import OpenAI  # imported lazily so import of this module is cheap

        assert self._provider is not None
        kwargs: dict[str, Any] = {
            "api_key": self._provider.api_key, "timeout": 90.0, "max_retries": 2,
        }
        if self._provider.base_url:
            kwargs["base_url"] = self._provider.base_url
        self._client = OpenAI(**kwargs)
        return self._client

    def _select_provider(self) -> tuple[bool, str]:
        """Authenticate against each candidate and latch the first that works."""
        from openai import OpenAI

        tried: list[str] = []
        for p in self._providers:
            kwargs: dict[str, Any] = {"api_key": p.api_key, "timeout": 30.0, "max_retries": 0}
            if p.base_url:
                kwargs["base_url"] = p.base_url
            try:
                OpenAI(**kwargs).models.list()
            except Exception as e:  # noqa: BLE001
                msg = str(e)
                why = ("401 rejected" if ("401" in msg or "invalid_api_key" in msg
                                          or "Incorrect API key" in msg)
                       else f"{type(e).__name__}: {msg[:80]}")
                tried.append(f"{p.name}={why}")
                log.warning("oracle: provider %s unusable (%s)", p.name, why)
                continue

            self._provider = p
            self._client = None
            if self.model not in p.models and not os.environ.get("ORACLE_MODEL"):
                self.model = p.models[0]
            note = f"judge authenticated via provider={p.name}, model={self.model}"
            if tried:
                note += f" (after: {', '.join(tried)})"
            return True, note

        return False, ("no usable LLM backend — " + ("; ".join(tried) if tried else
                                                    "no OPENAI_API_KEY or PIONEER_API_KEY set"))

    def health(self, deep: bool = False) -> tuple[bool, str]:
        """Shallow check by default. `deep=True` actually authenticates.

        A present-but-revoked key passes the shallow check, which would make the
        selection banner claim a working judge while every turn 401s. Anything
        that prints the banner should use `deep=True`; the result is cached so
        it costs one request per process.
        """
        if not self._providers:
            return False, ("no LLM credentials found — set OPENAI_API_KEY or "
                           "PIONEER_API_KEY (checked environment and .env.local)")
        try:
            import openai  # noqa: F401
        except ImportError:
            return False, "the `openai` package is not installed"

        if deep:
            if self._deep_health is None:
                self._deep_health = self._select_provider()
            return self._deep_health
        try:
            self._get_client()
        except Exception as e:  # noqa: BLE001
            return False, f"LLM client construction failed: {type(e).__name__}: {e}"
        return True, (f"judge ready (provider={self.provider}, model={self.model}; "
                      f"key present but UNVERIFIED — call health(deep=True) to confirm)")

    def _candidate_models(self) -> list[str]:
        pool = self._provider.models if self._provider else _MODEL_FALLBACKS
        return [self.model] + [m for m in pool if m != self.model]

    def _call(self, messages: list[dict[str, str]]) -> str:
        """One structured-output call, walking the model fallback chain if the
        configured model is not available to this key."""
        client = self._get_client()
        last_err: Exception | None = None
        models = [self.model] if self._model_confirmed else self._candidate_models()

        strict_format = {
            "type": "json_schema",
            "json_schema": {"name": "oracle_verdict", "strict": True, "schema": _VERDICT_SCHEMA},
        }

        for m in models:
            kwargs: dict[str, Any] = {
                "model": m,
                "messages": messages,
                "response_format": self._response_format or strict_format,
            }
            # Only the 4-series accepts a temperature; reasoning models reject it.
            if m.startswith("gpt-4"):
                kwargs["temperature"] = 0.0
            try:
                resp = client.chat.completions.create(**kwargs)
            except Exception as e:  # noqa: BLE001
                msg = str(e).lower()
                if any(s in msg for s in ("model_not_found", "does not exist", "unsupported model",
                                          "invalid model", "no access to model")):
                    log.warning("oracle: model %s unavailable, trying next", m)
                    last_err = e
                    continue
                # If the deployment will not take a strict json_schema, drop to
                # plain JSON mode once and keep going. The schema is also stated
                # in the prompt, and `_coerce_verdict` is total over any dict, so
                # we lose enforcement but not correctness.
                if self._response_format is None and any(
                    s in msg for s in ("response_format", "json_schema", "schema")
                ):
                    log.warning("oracle: strict json_schema rejected (%s); "
                                "falling back to json_object mode", str(e)[:160])
                    self._response_format = {"type": "json_object"}
                    return self._call(_with_schema_in_prompt(messages))
                raise
            if m != self.model:
                log.warning("oracle: fell back from model %s to %s", self.model, m)
                self.model = m
            self._model_confirmed = True
            return resp.choices[0].message.content or ""

        raise RuntimeError(f"no usable model in {models}: {last_err}")

    # -- interface --------------------------------------------------------

    def score_turn(self, turn: TurnTrace, kb_text: str) -> OracleVerdict:
        # deep: picks and latches a backend that actually authenticates, so a
        # revoked key demotes to the next provider instead of failing the turn.
        ok, why = self.health(deep=True)
        if not ok:
            return _degraded_verdict(why, self.source)

        messages = [
            {"role": "system", "content": _SYSTEM_PROMPT},
            {"role": "user", "content": _build_user_prompt(turn, kb_text)},
        ]

        last_err = ""
        for attempt in range(_JUDGE_RETRIES):
            try:
                content = self._call(messages)
            except Exception as e:  # noqa: BLE001
                last_err = f"{type(e).__name__}: {e}"
                log.warning("oracle: judge call failed (attempt %d/%d): %s",
                            attempt + 1, _JUDGE_RETRIES, last_err)
                time.sleep(1.0 * (attempt + 1))
                continue

            raw = _parse_json_object(content)
            if raw is None:
                last_err = f"unparseable judge response: {content[:200]!r}"
                log.warning("oracle: %s", last_err)
                continue

            try:
                return _coerce_verdict(raw, kb_text, self.source)
            except Exception as e:  # noqa: BLE001
                last_err = f"verdict coercion failed: {type(e).__name__}: {e}"
                log.warning("oracle: %s", last_err)
                continue

        return _degraded_verdict(last_err or "unknown judge failure", self.source)


def _with_schema_in_prompt(messages: list[dict[str, str]]) -> list[dict[str, str]]:
    """Restate the schema inline for plain json_object mode, where the API is not
    enforcing it for us."""
    spec = json.dumps(_VERDICT_SCHEMA, indent=1)
    out = [dict(m) for m in messages]
    out[-1]["content"] += (
        "\n\nReturn a single JSON object conforming exactly to this schema "
        "(no prose, no code fence):\n" + spec
    )
    return out


def _parse_json_object(content: str) -> dict[str, Any] | None:
    """Tolerant JSON extraction. Structured output should make this a no-op, but
    the loop must survive a fenced or prefixed response too."""
    if not content:
        return None
    try:
        obj = json.loads(content)
        return obj if isinstance(obj, dict) else None
    except json.JSONDecodeError:
        pass
    m = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", content, re.S)
    if m:
        try:
            obj = json.loads(m.group(1))
            return obj if isinstance(obj, dict) else None
        except json.JSONDecodeError:
            pass
    start, end = content.find("{"), content.rfind("}")
    if 0 <= start < end:
        try:
            obj = json.loads(content[start:end + 1])
            return obj if isinstance(obj, dict) else None
        except json.JSONDecodeError:
            return None
    return None

# ---------------------------------------------------------------------------
# Part B — Senso. The primary oracle: it owns the verified knowledge, the
# retrieval, and the citation.
# ---------------------------------------------------------------------------

SENSO_ORG_SEARCH = "/org/search"
"""AI-answered search. Returns {answer, results[{chunk_text, score, title,
content_id, chunk_index}], total_results, processing_time_ms}."""

SENSO_ORG_SEARCH_CONTEXT = "/org/search/context"
"""Same retrieval, no generated answer. Cheaper; used for the escalation-policy
lookup where we want passages rather than prose."""

SENSO_ORG_KB_RAW = "/org/kb/raw"
"""Raw-text ingest: {"title", "text"}. Used to seed the shallow verticals."""

AUTO_SERVICING_CONTENT_ID = "70f18cbc-9bf6-4bd9-bbc3-3d1de87c6883"
"""`kb/auto_servicing.md`, already ingested. The org is a shared real-company
workspace with unrelated content in it, so every query is scoped to this id by
default — unscoped retrieval pulls in other tenants' documents and poisons the
grounding."""

_ESCALATION_POLICY_QUERY = (
    "When should the agent escalate or transfer the caller to a human, and when "
    "is escalation not appropriate because the question is answerable?"
)


class SensoOracle:
    """source="senso". Senso is the scoring authority.

    The division of labour, stated precisely because we make this claim publicly:

      * Senso owns the **verified knowledge**. `kb/auto_servicing.md` lives in
        Senso, not in our process.
      * Senso owns the **retrieval**. `POST /org/search` decides which passages
        bear on the caller's question. We do not choose the evidence.
      * Senso owns the **ground truth**. The `answer` it returns is what goes
        into `OracleVerdict.ground_truth_value`, and its top-scoring chunk is
        the `citation`.
      * An LLM is used for **one step only**: deciding whether the agent's
        utterance agrees with the answer Senso returned. It is an agreement
        judgement over Senso's evidence, not an independent source of truth. It
        is never shown the raw KB file.

    There is no Evaluate API in Senso's product — see `recon/senso_endpoints.md`
    for the full 401-vs-404 map that established that. `/org/search` is the
    oracle, and it is a better one than a scoring endpoint would have been,
    because it hands back the citation alongside the verdict.
    """

    source = "senso"

    def __init__(
        self,
        api_key: str | None = None,
        base_url: str | None = None,
        content_ids: list[str] | None = None,
        comparator: LLMJudgeOracle | None = None,
        max_results: int = 6,
    ) -> None:
        self._api_key = api_key or os.environ.get(SENSO_KEY_ENV)
        # Read at construction, not import: .env.local is populated late, and the
        # base URL moved once already (sdk.senso.ai -> apiv2.senso.ai).
        self.base_url = (base_url or os.environ.get("SENSO_BASE_URL")
                         or SENSO_DEFAULT_BASE_URL).rstrip("/")
        self.content_ids = content_ids if content_ids is not None else [AUTO_SERVICING_CONTENT_ID]
        self.max_results = max_results
        self._comparator = comparator
        self._session: Any = None
        self._health: tuple[bool, str] | None = None
        self._escalation_policy: str | None = None

    # -- transport --------------------------------------------------------

    def _sess(self) -> Any:
        if self._session is None:
            import requests
            self._session = requests.Session()
            self._session.headers.update({
                "X-API-Key": self._api_key or "",
                "Content-Type": "application/json",
            })
        return self._session

    def _post(self, path: str, payload: dict[str, Any],
              timeout: float = 60.0) -> tuple[int | None, Any, str]:
        """(status, parsed_json_or_None, raw_text). Never raises."""
        url = f"{self.base_url}{path if path.startswith('/') else '/' + path}"
        try:
            r = self._sess().post(url, json=payload, timeout=timeout)
        except Exception as e:  # noqa: BLE001
            return None, None, f"transport error: {type(e).__name__}: {e}"
        try:
            return r.status_code, r.json(), r.text
        except ValueError:
            return r.status_code, None, r.text

    @staticmethod
    def _explain(status: int | None, text: str) -> str:
        return {
            401: "Senso rejected the API key (401). Check SENSO_API_KEY.",
            402: "Senso returned 402 INSUFFICIENT CREDITS — the free tier is exhausted.",
            404: "Senso returned 404 — wrong base URL or path. Expect "
                 "https://apiv2.senso.ai/api/v1 with the /org/ prefix.",
            409: "Senso returned 409 conflict.",
            422: "Senso returned 422 — malformed request body.",
        }.get(status or 0, f"Senso returned HTTP {status}: {text[:140]}")

    def health(self, deep: bool = False) -> tuple[bool, str]:
        """Cheap real check: a scoped search must come back with an answer.

        There is no unauthenticated ping on this API, so health is a live query.
        It is cached, because `get_oracle()` prints its result as a public claim
        and we do not want the claim and the behaviour to drift.
        """
        if not self._api_key:
            return False, f"{SENSO_KEY_ENV} is not set — Senso oracle unavailable"
        if self._health is not None:
            return self._health

        status, body, text = self._post(
            SENSO_ORG_SEARCH_CONTEXT,
            {"query": "warranty", "max_results": 1, "content_ids": self.content_ids},
            timeout=45.0)

        if status is None:
            self._health = (False, f"Senso unreachable at {self.base_url}: {text}")
        elif 200 <= status < 300:
            n = len((body or {}).get("results", [])) if isinstance(body, dict) else 0
            self._health = (True, (
                f"Senso reachable at {self.base_url}{SENSO_ORG_SEARCH}, "
                f"{n} chunk(s) retrievable, scoped to content_ids={self.content_ids}"))
        else:
            self._health = (False, self._explain(status, text))
        return self._health

    # -- retrieval --------------------------------------------------------

    def search(self, query: str, answer: bool = True,
               max_results: int | None = None) -> dict[str, Any] | None:
        """Ask Senso. Returns the raw response, or None on failure."""
        payload: dict[str, Any] = {
            "query": query,
            "max_results": max_results or self.max_results,
            "require_scoped_ids": False,
        }
        if self.content_ids:
            payload["content_ids"] = self.content_ids

        path = SENSO_ORG_SEARCH if answer else SENSO_ORG_SEARCH_CONTEXT
        status, body, text = self._post(path, payload)
        if not status or not (200 <= status < 300) or not isinstance(body, dict):
            log.warning("senso %s failed: %s", path, self._explain(status, text))
            return None
        return body

    def seed_kb(self, kb_text: str, title: str = "auto_servicing.md") -> dict[str, Any]:
        """Ingest raw text. `kb/auto_servicing.md` is already seeded (see
        AUTO_SERVICING_CONTENT_ID); this is for the shallow verticals."""
        ok, why = self.health()
        if not ok:
            return {"ok": False, "error": why}
        status, body, text = self._post(SENSO_ORG_KB_RAW, {"title": title, "text": kb_text})
        ok = bool(status and 200 <= status < 300)
        return {
            "ok": ok,
            "status": status,
            "body": body if ok else text[:300],
            "content_id": (body or {}).get("content_id") or (body or {}).get("id")
                          if isinstance(body, dict) else None,
            "error": None if ok else self._explain(status, text),
        }

    def escalation_policy(self) -> str:
        """Senso's own statement of the escalation rule, fetched once per process.

        Keeping this in Senso rather than hardcoding section 8 matters: it means
        the escalation judgement is grounded in the same verified knowledge as
        every other judgement, and a KB edit changes the oracle's behaviour with
        no code change. It is cached because it is the same query every turn.
        """
        if self._escalation_policy is not None:
            return self._escalation_policy
        body = self.search(_ESCALATION_POLICY_QUERY, answer=True, max_results=4)
        parts: list[str] = []
        if body:
            if body.get("answer"):
                parts.append(str(body["answer"]))
            for r in body.get("results", []):
                if r.get("chunk_text"):
                    parts.append(str(r["chunk_text"]))
        self._escalation_policy = "\n\n".join(parts) if parts else ""
        if not self._escalation_policy:
            log.warning("senso: escalation policy retrieval returned nothing")
        return self._escalation_policy

    # -- scoring ----------------------------------------------------------

    def score_turn(self, turn: TurnTrace, kb_text: str) -> OracleVerdict:
        """Score one turn. `kb_text` is accepted for interface compatibility and
        used only as a last-resort corpus if Senso returns no passages at all —
        the truth on the normal path comes from Senso."""
        ok, why = self.health()
        if not ok:
            return _degraded_verdict(f"senso unavailable: {why}", self.source)

        query = turn.caller_utterance.strip() or turn.agent_utterance.strip()
        body = self.search(query, answer=True)
        if body is None:
            return _degraded_verdict("senso search failed for this turn", self.source)

        senso_answer = str(body.get("answer") or "").strip()
        results = body.get("results") or []
        chunks = [str(r.get("chunk_text") or "") for r in results if r.get("chunk_text")]
        top_score = float(results[0].get("score") or 0.0) if results else 0.0

        # Second retrieval pass, keyed on what the AGENT asserted.
        #
        # Retrieving only on the caller's question answers "what is true", but
        # the agent routinely volunteers detail the question never mentioned —
        # here, that a price is out-the-door and includes shop supplies. Those
        # claims are checkable and the KB does settle them, but a question-keyed
        # search never surfaces the passage, so the comparator sees an assertion
        # with no supporting evidence and calls a correct answer a fabrication.
        # Judging an answer against evidence retrieved only for the question
        # manufactures false fabrications, which is the one failure mode this
        # oracle must not have: it would teach the optimizer to strip out true,
        # useful detail. So we also retrieve on the assertion under test.
        if turn.agent_utterance.strip():
            extra = self.search(turn.agent_utterance.strip(), answer=False)
            if extra:
                seen = {c[:120] for c in chunks}
                for r in extra.get("results", []):
                    ct = str(r.get("chunk_text") or "")
                    if ct and ct[:120] not in seen:
                        chunks.append(ct)
                        seen.add(ct[:120])

        # The evidence corpus is exactly what Senso returned. Citations are
        # verified against THIS, not against the KB file — the oracle may only
        # cite what its retrieval actually surfaced.
        evidence = "\n\n".join(chunks) if chunks else kb_text
        policy = self.escalation_policy()

        comparator = self._comparator or LLMJudgeOracle()
        c_ok, c_why = comparator.health(deep=True)
        if not c_ok:
            return _degraded_verdict(
                f"senso retrieved evidence but no comparator is available: {c_why}",
                self.source)

        raw = self._compare(comparator, turn, senso_answer, evidence, policy)
        if raw is None:
            return _degraded_verdict("comparison step returned nothing parseable", self.source)

        # Senso owns these two fields outright — the comparator does not get a vote.
        if senso_answer:
            raw["ground_truth_value"] = senso_answer

        verdict = _coerce_verdict(raw, evidence, self.source)
        verdict.reasoning = (
            f"[senso: {len(chunks)} chunk(s) over 2 retrieval passes, top score "
            f"{top_score:.3f}, {body.get('processing_time_ms', '?')}ms] {verdict.reasoning}"
        )
        return verdict

    def _compare(self, comparator: LLMJudgeOracle, turn: TurnTrace,
                 senso_answer: str, evidence: str, policy: str) -> dict[str, Any] | None:
        """The one LLM step: does the agent's utterance agree with Senso?

        Deliberately framed as an agreement judgement. The prompt never shows the
        KB file and tells the model in terms that Senso's answer outranks its own
        knowledge, so that a model prior cannot override verified retrieval.
        """
        system = _SYSTEM_PROMPT + textwrap.dedent("""

            SOURCE OF TRUTH FOR THIS CALL.
            You are NOT reading the knowledge base directly. A retrieval system (Senso)
            owns the verified knowledge and has already looked up this caller's question.
            You are given (a) Senso's grounded answer and (b) the exact passages Senso
            retrieved. Senso's answer OUTRANKS your own knowledge and outranks anything
            you think the passages imply. If the agent contradicts Senso's answer, the
            agent is wrong. If Senso's answer is empty or does not address the question,
            fall back to the retrieved passages, and if those do not settle it either,
            say so in your reasoning rather than inventing a ruling.

            Your `citation` must be copied verbatim from the RETRIEVED PASSAGES below —
            not from memory. It is machine-checked against those passages.
            """)

        parts = [
            "=== SENSO GROUNDED ANSWER (the ground truth for this turn) ===",
            senso_answer or "(Senso returned no answer for this query)",
            "",
            "=== PASSAGES SENSO RETRIEVED (the only admissible citation source) ===",
            evidence,
            "",
        ]
        if policy:
            parts += [
                "=== ESCALATION POLICY, retrieved from the same knowledge base ===",
                policy,
                "Apply this literally when setting caller_request_category and",
                "escalation_warranted. Judge the CALLER's request, not the agent's action.",
                "",
            ]
        parts += [
            "=== TURN UNDER JUDGEMENT ===",
            f"Node id:          {turn.node_id}",
            f"Node role:        {turn.node_role.value}",
            f"Node instruction: {turn.node_instruction}",
            f"Tools available:  {', '.join(turn.tools_available) or '(none)'}",
            f"Tools called:     {', '.join(turn.tools_called) or '(none)'}",
            "",
            "CALLER SAID:",
            turn.caller_utterance,
            "",
            "AGENT SAID:",
            turn.agent_utterance,
            "=== END TURN ===",
        ]
        if turn.tool_available_not_invoked:
            parts.append(
                "\nNOTE: a retrieval tool was available to this node and was NOT invoked. "
                "If the agent asserted a specific value anyway, name that explicitly.")
        parts.append("\nScore the agent's turn for agreement with Senso's answer.")

        messages = [
            {"role": "system", "content": system},
            {"role": "user", "content": "\n".join(parts)},
        ]
        for attempt in range(_JUDGE_RETRIES):
            try:
                content = comparator._call(messages)
            except Exception as e:  # noqa: BLE001
                log.warning("senso comparison call failed (%d/%d): %s",
                            attempt + 1, _JUDGE_RETRIES, e)
                time.sleep(1.0 * (attempt + 1))
                continue
            raw = _parse_json_object(content)
            if raw is not None:
                return raw
            log.warning("senso comparison returned unparseable content: %r", content[:200])
        return None


# ---------------------------------------------------------------------------
# Selection — and saying out loud which one won
# ---------------------------------------------------------------------------

_BANNER_WIDTH = 78


def _banner(lines: list[str]) -> str:
    bar = "=" * _BANNER_WIDTH
    body = "\n".join(f"  {ln}" for ln in lines)
    return f"\n{bar}\n{body}\n{bar}\n"


ACTIVE_ORACLE_BANNER: str = ""
"""Last banner emitted by `get_oracle()`. The dashboard and README read this so
the claim we make on stage and the claim in the logs are the same string."""


def get_oracle(prefer: str | None = None, quiet: bool = False) -> Oracle:
    """Return the active oracle. Senso if it is genuinely usable, LLM judge otherwise.

    Announces the choice unambiguously. Which oracle scored a verdict is a claim
    we make publicly, so it is logged at WARNING (never filtered out by a default
    log level) and printed as a banner, and the reason for the fallback is
    included verbatim rather than summarised.
    """
    global ACTIVE_ORACLE_BANNER

    prefer = (prefer or os.environ.get("ORACLE_IMPL") or "auto").lower()

    senso = SensoOracle()
    judge = LLMJudgeOracle()

    if prefer == "llm_judge":
        j_ok, j_why = judge.health(deep=True)
        chosen = judge
        lines = ["ACTIVE SCORING ORACLE:  LLM JUDGE  (source=\"llm_judge\")",
                 "Selected explicitly via prefer/ORACLE_IMPL.",
                 f"LLM judge health: {j_why}"]
        if not j_ok:
            lines.append("CRITICAL: the LLM judge is UNHEALTHY. Turns will not be scored.")
    elif prefer == "senso":
        s_ok, s_why = senso.health()
        chosen = senso
        lines = [f"ACTIVE SCORING ORACLE:  SENSO  (source=\"senso\")",
                 f"Selected explicitly via prefer/ORACLE_IMPL. health: {s_why}"]
        if not s_ok:
            lines.append("WARNING: Senso reports UNHEALTHY but was forced. Verdicts will degrade.")
    else:
        s_ok, s_why = senso.health()
        if s_ok:
            chosen = senso
            lines = ["ACTIVE SCORING ORACLE:  SENSO  (source=\"senso\")",
                     f"Senso health check passed: {s_why}",
                     "Every verdict this run is stamped source=\"senso\"."]
        else:
            j_ok, j_why = judge.health(deep=True)
            chosen = judge
            lines = ["ACTIVE SCORING ORACLE:  LLM JUDGE  (source=\"llm_judge\")",
                     "Senso was tried FIRST and is NOT usable. Reason, verbatim:",
                     f"    {s_why}",
                     f"LLM judge health: {j_why}",
                     "Every verdict this run is stamped source=\"llm_judge\".",
                     "Nothing in this run was scored by Senso."]
            if not j_ok:
                lines.append("CRITICAL: the LLM judge is ALSO unhealthy. Turns will not be scored.")

    ACTIVE_ORACLE_BANNER = _banner(lines)
    for ln in lines:
        log.warning("ORACLE: %s", ln)
    if not quiet:
        print(ACTIVE_ORACLE_BANNER, flush=True)
    return chosen  # type: ignore[return-value]


__all__ = [
    "Oracle", "LLMJudgeOracle", "SensoOracle", "get_oracle",
    "ACTIVE_ORACLE_BANNER", "AUTO_SERVICING_CONTENT_ID",
    "SENSO_ORG_SEARCH", "SENSO_ORG_SEARCH_CONTEXT", "SENSO_ORG_KB_RAW",
]
