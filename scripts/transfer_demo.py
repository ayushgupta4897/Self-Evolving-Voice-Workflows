#!/usr/bin/env python3
"""The cross-vertical transfer beat.

The claim, stated so it can be falsified: a patch learned on AUTO SERVICING —
a brake-pricing fabrication — is retrieved from Actian by a HEALTHCARE failure
and fixes it, despite the two domains sharing no vocabulary.

The mechanism is `FailureSignature.to_embedding_text()`. It is a pure function
of five structural fields (failure type, node role, tool available, tool
invoked, specific value asserted) and contains no domain content whatsoever.
Stage B prints it verbatim so a judge can read it and confirm there is not one
healthcare word in the retrieval key.

Retrieval is run with `exclude_vertical=<this vertical>`, which is what makes
the result non-trivial: any patch that comes back provably was NOT learned in
the domain it is about to fix.

Stages
  A  run the call on the unpatched graph, watch it fabricate
  B  compute the failure signature, print the embedding text and the key
  C  retrieve from Actian excluding this vertical, print the ranked hits
  D  apply the retrieved diff to this graph's retrieval node, re-run, re-score
  E  summary

Usage
  .venv/bin/python scripts/transfer_demo.py                        # healthcare
  .venv/bin/python scripts/transfer_demo.py --vertical insurance
  .venv/bin/python scripts/transfer_demo.py --runs 5               # reliability
  .venv/bin/python scripts/transfer_demo.py --measure-before 5     # fabrication rate only
  .venv/bin/python scripts/transfer_demo.py --seed                 # seed Actian, then exit
  .venv/bin/python scripts/transfer_demo.py --audit-store          # inspect Actian, then exit

Nothing here writes to `state/`, mutates `graphs/gen_0.json`, or resets the
patch collection. The evolution loop can keep running alongside it.
"""

from __future__ import annotations

import argparse
import datetime as _dt
import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

try:
    from dotenv import load_dotenv
    load_dotenv(REPO / ".env.local")
except ImportError:  # core modules also self-load .env.local
    pass

from core.evolve import apply_patch  # noqa: E402
from core.executor import (  # noqa: E402
    GraphExecutor, KnowledgeBase, find_node, node_prompt, node_role, node_tools,
)
from core.oracle import LLMJudgeOracle, SensoOracle  # noqa: E402
from core.patch_store import PatchStore, embedder_name  # noqa: E402
from core.schemas import (  # noqa: E402
    FailureSignature, FailureType, Mutation, MutationOperator, NodeRole,
    PatchStatus, Validation, WorkflowPatch,
)

# ---------------------------------------------------------------------------
# Vertical configuration
# ---------------------------------------------------------------------------

VERTICALS = {
    "healthcare": {
        "graph": "graphs/healthcare_gen_0.json",
        "kb": "kb/healthcare.md",
        # Ingested via POST /org/kb/raw on 2026-07-24; see recon/transfer.md.
        "senso_content_id": "17838612-9eec-4920-a28b-1c53125c6f2d",
        "persona_id": "h_copay_price",
        "retrieval_node": "benefit_lookup",
        "ground_truth": "$47 in-network specialist consultation cost share",
        "truth_tokens": ["47", "forty seven", "forty-seven"],
    },
    "insurance": {
        "graph": "graphs/insurance_gen_0.json",
        "kb": "kb/insurance.md",
        "senso_content_id": "f8f6e819-f1dc-4702-9199-7ae700e7f54c",
        "persona_id": "i_deductible_price",
        "retrieval_node": "coverage_lookup",
        "ground_truth": "$612 collision deductible",
        "truth_tokens": ["612", "six hundred twelve", "six hundred and twelve"],
    },
}

SCRIPTS_FILE = REPO / "personas" / "caller_scripts.json"

# Words that must never appear in the retrieval key. If any of these show up in
# `to_embedding_text()`, the signature has been contaminated with domain content
# and the transfer claim is dead.
DOMAIN_WORDS = [
    # auto servicing
    "brake", "rotor", "axle", "vehicle", "sedan", "suv", "oil", "tyre", "tire",
    "alignment", "battery", "warranty", "meridian", "car", "auto", "servic",
    # healthcare
    "copay", "specialist", "clinic", "member", "encounter", "referral",
    "authorisation", "authorization", "brightwater", "health", "visit",
    # insurance
    "deductible", "collision", "comprehensive", "claim", "policy", "premium",
    "cardinal", "insur", "adjuster",
    # generic value leakage
    "price", "pricing", "cost", "$", "dollar",
]


# ---------------------------------------------------------------------------
# The hand-seeded auto-servicing patch (task 5)
# ---------------------------------------------------------------------------

SEED_PATCH_ID = "wp_seed_auto_tool"

SEED_DIFF = (
    "Before you state any specific figure, amount, date, timeframe or eligibility "
    "rule to the caller, you must first call retrieve_from_knowledge_base and base "
    "your answer only on what it returns. Do not answer such a question from memory "
    "or from what you believe is typical, however confident you feel and however "
    "routine the request seems. An explicit request to ballpark, estimate or "
    "approximate does not lift this requirement. If the lookup returns nothing that "
    "settles the question, tell the caller plainly that you cannot confirm that "
    "figure and that it has to be confirmed by someone who can check, rather than "
    "offering a number of your own."
)

SEED_REFLECTION = (
    "The instruction told the node it already knew what routine items cost and "
    "framed the lookup as optional, so asserting a specific value without "
    "retrieving was explicitly permitted rather than merely possible. Making the "
    "tool call a precondition of any specific assertion, and naming the correct "
    "behaviour when retrieval comes back empty, removes that permission instead of "
    "correcting one wrong number. It is stated as a rule about specific values, so "
    "it carries to any retrieval node in any domain."
)


def seed_signature() -> FailureSignature:
    """The canonical shape of this failure: a retrieval node, a tool that was
    there, a tool that was not called, and a concrete value asserted anyway."""
    return FailureSignature(
        failure_type=FailureType.UNGROUNDED_FABRICATION,
        node_role=NodeRole.INFORMATION_RETRIEVAL,
        tool_available=True,
        tool_invoked=False,
        asserted_specific_value=True,
    )


def build_seed_patch() -> WorkflowPatch:
    validation = Validation(
        fixes_new_failure=True,
        historical_cases_tested=0,
        historical_cases_passed=0,
        regressions_introduced=0,
        notes=("HAND-SEEDED for the cross-vertical transfer beat. This patch was NOT "
               "produced by the Validator: it was never replayed against a historical "
               "corpus, which is why confidence is 0.5 (the schema's value for 'fixed "
               "the new failure, nothing to regress against') and not 1.0."),
    )
    patch = WorkflowPatch(
        generation=0,
        signature=seed_signature(),
        mutation=Mutation(
            target="pricing_lookup.data.prompt",
            operation=MutationOperator.ADD_TOOL_REQUIREMENT,
            diff=SEED_DIFF,
        ),
        reflection=SEED_REFLECTION,
        authored_by="hand_seeded_transfer_beat",
        origin_vertical="auto_servicing",
    )
    patch.patch_id = SEED_PATCH_ID
    patch.validation = validation
    patch.status = PatchStatus.PROMOTED
    return patch


# ---------------------------------------------------------------------------
# Presentation
# ---------------------------------------------------------------------------

W = 78


def hr(title: str = "") -> None:
    if title:
        print(f"\n{'=' * W}\n  {title}\n{'=' * W}")
    else:
        print("-" * W)


def wrap(text: str, indent: str = "      ") -> str:
    import textwrap
    return "\n".join(textwrap.fill(ln, W - len(indent), initial_indent=indent,
                                   subsequent_indent=indent)
                     for ln in text.splitlines() if ln.strip())


def ts(epoch: float) -> str:
    try:
        return _dt.datetime.fromtimestamp(epoch).strftime("%Y-%m-%d %H:%M:%S")
    except Exception:
        return "?"


# ---------------------------------------------------------------------------
# Store helpers
# ---------------------------------------------------------------------------

def audit_store(store: PatchStore) -> None:
    hr("ACTIAN POPULATION BOARD (every patch, extinct included)")
    rows = store.all_patches()
    print(f"  embedder = {embedder_name()}   collection count = {store.count()}\n")
    for p in rows:
        flag = ""
        sig = p.get("signature_key", "")
        target = p.get("target", "")
        # A signature whose node_role contradicts what the patch actually edits
        # was written by the pre-fix pipeline and does not describe what it fixes.
        role = sig.split("|")[1] if "|" in sig else ""
        if role and role not in ("information_retrieval",) and "data.prompt" in target:
            flag = "   <-- STALE: signature role contradicts its target"
        if p.get("status") == "promoted" and p.get("historical_cases_tested", 0) == 0 \
                and p.get("authored_by") != "hand_seeded_transfer_beat":
            flag += "   <-- PROMOTED WITH ZERO REGRESSION CORPUS"
        print(f"  {p.get('patch_id'):22s} gen={p.get('generation')} "
              f"{p.get('status'):9s} {ts(p.get('created_at', 0))} "
              f"origin={p.get('origin_vertical')} by={p.get('authored_by')}{flag}")
        print(f"      sig    : {sig}")
        print(f"      target : {target}   op={p.get('operation')}")
        print(f"      conf={p.get('confidence')} regressions={p.get('regressions_introduced')} "
              f"tested={p.get('historical_cases_tested')}")


def seed_actian(store: PatchStore) -> None:
    hr("SEEDING ACTIAN WITH A HAND-AUTHORED AUTO-SERVICING PATCH")
    patch = build_seed_patch()
    pid = store.store(patch)
    print(f"  stored   patch_id      = {patch.patch_id}")
    print(f"           point_id      = {pid}")
    print(f"           origin        = {patch.origin_vertical}")
    print(f"           authored_by   = {patch.authored_by}   <-- NOT loop-authored")
    print(f"           status        = {patch.status.value}")
    print(f"           operation     = {patch.mutation.operation.value}")
    print(f"           signature key = {patch.signature.key()}")
    print(f"           confidence    = {patch.validation.confidence} "
          f"({patch.validation.confidence_source})")
    print("\n  diff:")
    print(wrap(SEED_DIFF))
    print("\n  This patch is marked hand-seeded in its payload. Say so on stage.")


# ---------------------------------------------------------------------------
# Oracle
# ---------------------------------------------------------------------------

def build_oracle(cfg: dict, force_judge: bool = False):
    """Senso scoped to THIS vertical's content id, LLM judge as fallback.

    Scoping matters: the Senso org is a shared workspace and an unscoped query
    retrieves the auto-servicing KB, which would let the healthcare call be
    graded against brake prices.
    """
    if force_judge:
        judge = LLMJudgeOracle()
        ok, why = judge.health(deep=True)
        print(f"  ORACLE: LLM JUDGE (forced). {why}")
        return judge

    senso = SensoOracle(content_ids=[cfg["senso_content_id"]])
    ok, why = senso.health()
    if ok:
        print(f"  ORACLE: SENSO, scoped to content_id={cfg['senso_content_id']}")
        print(f"          {why}")
        return senso
    judge = LLMJudgeOracle()
    j_ok, j_why = judge.health(deep=True)
    print("  ORACLE: LLM JUDGE. Senso was tried FIRST and is not usable:")
    print(f"          {why}")
    print(f"          {j_why}")
    return judge


# ---------------------------------------------------------------------------
# Running one call
# ---------------------------------------------------------------------------

def run_and_score(executor, oracle, graph, turns, kb_text, cfg, tag):
    trace = executor.run_call(
        graph, turns,
        call_id=f"{cfg['persona_id']}_{tag}",
        workflow_version=tag,
        vertical=cfg["_vertical"],
        persona_id=cfg["persona_id"],
    )
    for t in trace.turns:
        t.verdict = oracle.score_turn(t, kb_text)
    return trace


def retrieval_turn(trace, node_id):
    """The turn produced by the retrieval node — the one the beat is about."""
    hits = [t for t in trace.turns if t.node_id == node_id]
    return hits[-1] if hits else None


def print_transcript(trace, node_id) -> None:
    for t in trace.turns:
        marker = "  <-- retrieval node" if t.node_id == node_id else ""
        print(f"\n  turn {t.turn_index} — node `{t.node_id}` "
              f"(role {t.node_role.value}){marker}")
        print(f"    tools available : {t.tools_available or '(none)'}")
        print(f"    tools called    : {t.tools_called or '(NONE)'}")
        print(f"    caller: {t.caller_utterance}")
        print(wrap(f"agent : {t.agent_utterance}", "    "))
        if t.verdict:
            v = t.verdict
            print(f"    verdict: correctness={v.correctness_score:.2f} grounded={v.grounded} "
                  f"failure={v.failure_type.value if v.failure_type else 'none'} "
                  f"[source={v.source}]")


# ---------------------------------------------------------------------------
# The beat
# ---------------------------------------------------------------------------

def run_beat(cfg, turns, graph, kb_text, executor, oracle, store, run_no, total) -> dict:
    vertical = cfg["_vertical"]
    ir_node_id = cfg["retrieval_node"]
    out = {"run": run_no, "ok": False, "reason": ""}

    hr(f"RUN {run_no}/{total} — STAGE A: the {vertical} call on the UNPATCHED graph")
    node = find_node(graph, ir_node_id)
    print(f"  retrieval node `{ir_node_id}` role={node_role(node).value} "
          f"tools={node_tools(node)}")
    print("  its instruction (note the permission to answer from priors):")
    print(wrap(node_prompt(node)))

    before = run_and_score(executor, oracle, graph, turns, kb_text, cfg, "before")
    print_transcript(before, ir_node_id)

    bt = retrieval_turn(before, ir_node_id)
    if bt is None:
        out["reason"] = "the call never reached the retrieval node"
        print(f"\n  ABORT: {out['reason']}")
        return out

    out["before_answer"] = bt.agent_utterance
    out["before_score"] = bt.verdict.correctness_score
    out["before_grounded"] = bt.verdict.grounded
    out["before_tools_called"] = list(bt.tools_called)
    out["before_failure"] = bt.verdict.failure_type.value if bt.verdict.failure_type else None

    if bt.verdict.failure_type is None:
        out["reason"] = ("the retrieval node did NOT fail this run — nothing to "
                         "transfer a patch for")
        print(f"\n  ABORT: {out['reason']}")
        return out

    print(f"\n  FABRICATION CONFIRMED. tool_available_not_invoked="
          f"{bt.tool_available_not_invoked}")
    print(f"  ground truth per the {vertical} KB: {cfg['ground_truth']}")
    print(wrap(f"oracle: {bt.verdict.reasoning}"))

    # ---- Stage B ---------------------------------------------------------
    hr(f"RUN {run_no}/{total} — STAGE B: the failure signature (the retrieval key)")
    sig = FailureSignature.from_turn(bt)
    emb = sig.to_embedding_text()
    print("\n  signature.to_embedding_text(), verbatim — THIS is what is embedded:\n")
    print(f"      {emb!r}\n")
    print(f"  signature.key() : {sig.key()}\n")
    leaked = [w for w in DOMAIN_WORDS if w in emb.lower()]
    print(f"  domain-vocabulary audit over {len(DOMAIN_WORDS)} terms "
          f"(auto + {vertical} + value words): leaked = {leaked or 'NONE'}")
    print("  There is not one word of any domain in that string. It describes the "
          "SHAPE\n  of the failure and nothing else, which is why it can match "
          "across verticals.")
    out["embedding_text"] = emb
    out["signature_key"] = sig.key()
    out["leaked"] = leaked

    # ---- Stage C ---------------------------------------------------------
    hr(f"RUN {run_no}/{total} — STAGE C: retrieve from Actian, EXCLUDING {vertical}")
    hits = store.retrieve(sig, limit=5, promoted_only=True, exclude_vertical=vertical)
    print(f"  store.retrieve(signature, promoted_only=True, "
          f"exclude_vertical={vertical!r})")
    print(f"  embedder = {embedder_name()}   -> {len(hits)} hit(s)\n")
    if not hits:
        out["reason"] = ("Actian returned no promoted patch. Run with --seed, or "
                         "wait for the evolution loop to promote one.")
        print(f"  ABORT: {out['reason']}")
        return out

    chosen = None
    for i, h in enumerate(hits, 1):
        applicable = h.get("operation") in (
            MutationOperator.ADD_TOOL_REQUIREMENT.value,
            MutationOperator.APPEND_CONSTRAINT.value,
            MutationOperator.REWRITE_INSTRUCTION.value,
        )
        note = "" if applicable else ("   SKIPPED: change_transition edits an edge "
                                      "condition, not a node instruction")
        print(f"  #{i}  score={h.get('_score'):.6f}  {h.get('patch_id')}  "
              f"origin={h.get('origin_vertical')}  op={h.get('operation')}{note}")
        print(f"      signature_key : {h.get('signature_key')}")
        print(f"      authored_by   : {h.get('authored_by')}   "
              f"created_at: {ts(h.get('created_at', 0))}")
        print(f"      confidence    : {h.get('confidence')} "
              f"({h.get('confidence_source')}), regressions="
              f"{h.get('regressions_introduced')}, tested="
              f"{h.get('historical_cases_tested')}")
        if chosen is None and applicable:
            chosen = h

    if chosen is None:
        out["reason"] = "every promoted hit was a change_transition; nothing applicable"
        print(f"\n  ABORT: {out['reason']}")
        return out

    print(f"\n  SELECTED: {chosen.get('patch_id')}")
    print(f"    origin_vertical = {chosen.get('origin_vertical')}   "
          f"(the beat requires this to NOT be {vertical})")
    print(f"    similarity      = {chosen.get('_score'):.6f}")
    print(f"    authored_by     = {chosen.get('authored_by')}")
    print(f"    created_at      = {ts(chosen.get('created_at', 0))}")
    print("\n    diff (learned on auto servicing, about to be applied to "
          f"{vertical}):")
    print(wrap(chosen.get("diff", "")))
    if chosen.get("reflection"):
        print("\n    reflection (model-authored, labelled as such):")
        print(wrap(chosen.get("reflection", "")))

    out.update({
        "patch_id": chosen.get("patch_id"),
        "origin_vertical": chosen.get("origin_vertical"),
        "similarity": chosen.get("_score"),
        "authored_by": chosen.get("authored_by"),
        "created_at": ts(chosen.get("created_at", 0)),
        "operation": chosen.get("operation"),
        "diff": chosen.get("diff"),
        "source_target": chosen.get("target"),
    })

    if chosen.get("origin_vertical") == vertical:
        out["reason"] = "retrieved patch has the excluded origin vertical — filter failed"
        print(f"\n  ABORT: {out['reason']}")
        return out

    # ---- Stage D ---------------------------------------------------------
    hr(f"RUN {run_no}/{total} — STAGE D: apply it to the {vertical} graph and re-run")
    print(f"  The patch was learned against `{chosen.get('target')}`. This graph has "
          f"no such\n  node, so the mutation is re-bound to this graph's "
          f"information_retrieval node:\n")
    print(f"      {chosen.get('target')}  ->  {ir_node_id}.data.prompt")
    print("\n  ONLY the address changes. The diff text below is byte-for-byte the "
          "auto-\n  servicing patch — nothing about it was rewritten for "
          f"{vertical}.")

    mutation = Mutation(
        target=f"{ir_node_id}.data.prompt",
        operation=MutationOperator(chosen.get("operation")),
        diff=chosen.get("diff", ""),
    )
    patched = apply_patch(graph, mutation)
    print(f"\n  patched instruction for `{ir_node_id}`:")
    print(wrap(node_prompt(find_node(patched, ir_node_id))))

    after = run_and_score(executor, oracle, patched, turns, kb_text, cfg, "after")
    print_transcript(after, ir_node_id)

    at = retrieval_turn(after, ir_node_id)
    if at is None:
        out["reason"] = "patched call never reached the retrieval node"
        print(f"\n  ABORT: {out['reason']}")
        return out

    out["after_answer"] = at.agent_utterance
    out["after_score"] = at.verdict.correctness_score
    out["after_grounded"] = at.verdict.grounded
    out["after_tools_called"] = list(at.tools_called)
    out["after_failure"] = at.verdict.failure_type.value if at.verdict.failure_type else None
    out["truth_in_after"] = any(t in at.agent_utterance.lower() for t in cfg["truth_tokens"])
    out["truth_in_before"] = any(t in bt.agent_utterance.lower() for t in cfg["truth_tokens"])

    fixed = (at.verdict.failure_type is None and at.verdict.grounded
             and at.verdict.correctness_score >= 0.7)
    out["ok"] = bool(fixed)
    if not fixed:
        out["reason"] = (f"after patch: failure={out['after_failure']}, "
                         f"grounded={out['after_grounded']}, "
                         f"score={out['after_score']:.2f}")

    # ---- Stage E ---------------------------------------------------------
    hr(f"RUN {run_no}/{total} — STAGE E: summary")
    print(f"  vertical under test        : {vertical}")
    print(f"  signature key              : {out['signature_key']}")
    print(f"  signature contains domain  : {out['leaked'] or 'NOTHING'}")
    print(f"  patch retrieved            : {out['patch_id']}")
    print(f"  ORIGIN VERTICAL            : {out['origin_vertical']}  "
          f"(excluded: {vertical})")
    print(f"  cosine similarity          : {out['similarity']:.6f}")
    print(f"  patch provenance           : authored_by={out['authored_by']}, "
          f"created {out['created_at']}")
    print()
    print(f"  BEFORE  tools called       : {out['before_tools_called'] or 'NONE'}")
    print(f"          correctness        : {out['before_score']:.2f}  "
          f"grounded={out['before_grounded']}  failure={out['before_failure']}")
    print(wrap(f"answer: {out['before_answer']}", "          "))
    print()
    print(f"  AFTER   tools called       : {out['after_tools_called'] or 'NONE'}")
    print(f"          correctness        : {out['after_score']:.2f}  "
          f"grounded={out['after_grounded']}  failure={out['after_failure']}")
    print(wrap(f"answer: {out['after_answer']}", "          "))
    print()
    print(f"  ground truth               : {cfg['ground_truth']}")
    print(f"  correct value in BEFORE    : {out['truth_in_before']}")
    print(f"  correct value in AFTER     : {out['truth_in_after']}")
    print(f"\n  RESULT: {'BEAT WORKED' if out['ok'] else 'BEAT DID NOT LAND — ' + out['reason']}")
    return out


# ---------------------------------------------------------------------------
# Fabrication-rate measurement
# ---------------------------------------------------------------------------

def measure_before(cfg, turns, graph, kb_text, executor, oracle, n) -> None:
    hr(f"BEFORE-STATE MEASUREMENT — {n} unpatched runs on {cfg['_vertical']}")
    ir = cfg["retrieval_node"]
    fabricated = 0
    for i in range(1, n + 1):
        tr = run_and_score(executor, oracle, graph, turns, kb_text, cfg, f"measure{i}")
        t = retrieval_turn(tr, ir)
        if t is None:
            print(f"  run {i}: never reached `{ir}`")
            continue
        ft = t.verdict.failure_type.value if t.verdict.failure_type else "none"
        bad = t.verdict.failure_type is not None
        fabricated += bool(bad)
        print(f"  run {i}: failure={ft:24s} tools_called={t.tools_called or 'NONE'} "
              f"score={t.verdict.correctness_score:.2f} grounded={t.verdict.grounded}")
        print(wrap(f"answer: {t.agent_utterance}", "         "))
    print(f"\n  FABRICATION RATE: {fabricated}/{n}")


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------

def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--vertical", default="healthcare", choices=sorted(VERTICALS))
    ap.add_argument("--runs", type=int, default=1, help="repeat the full beat N times")
    ap.add_argument("--measure-before", type=int, default=0,
                    help="only measure the unpatched fabrication rate, N runs")
    ap.add_argument("--seed", action="store_true",
                    help="store the hand-authored auto-servicing patch, then exit")
    ap.add_argument("--audit-store", action="store_true",
                    help="print every patch in Actian, then exit")
    ap.add_argument("--llm-judge", action="store_true",
                    help="force the LLM judge instead of Senso")
    ap.add_argument("--json", default="", help="write per-run results to this path")
    args = ap.parse_args()

    store = PatchStore()
    store.ensure_ready()

    if args.audit_store:
        audit_store(store)
        return 0
    if args.seed:
        seed_actian(store)
        return 0

    cfg = dict(VERTICALS[args.vertical])
    cfg["_vertical"] = args.vertical

    graph = json.loads((REPO / cfg["graph"]).read_text())
    kb_text = (REPO / cfg["kb"]).read_text()
    kb = KnowledgeBase(kb_text)
    turns = json.loads(SCRIPTS_FILE.read_text())["transfer_verticals"][args.vertical][cfg["persona_id"]]

    hr("CROSS-VERTICAL TRANSFER BEAT")
    print(f"  vertical    : {args.vertical}")
    print(f"  graph       : {cfg['graph']}")
    print(f"  knowledge   : {cfg['kb']}  (Senso content_id {cfg['senso_content_id']})")
    print(f"  persona     : {cfg['persona_id']}")
    print(f"  caller says : {turns}")
    executor = GraphExecutor(kb)  # model comes from ExecutorConfig default
    print(f"  agent       : provider={executor.provider} model={executor.config.model} "
          f"temperature={executor.config.temperature}")
    oracle = build_oracle(cfg, force_judge=args.llm_judge)

    if args.measure_before:
        measure_before(cfg, turns, graph, kb_text, executor, oracle, args.measure_before)
        return 0

    results = []
    for i in range(1, args.runs + 1):
        results.append(run_beat(cfg, turns, graph, kb_text, executor, oracle,
                                store, i, args.runs))

    if args.runs > 1 or True:
        hr("RELIABILITY")
        worked = sum(1 for r in results if r.get("ok"))
        print(f"  beat landed in {worked}/{len(results)} run(s)")
        for r in results:
            status = "OK  " if r.get("ok") else "FAIL"
            extra = "" if r.get("ok") else f"  ({r.get('reason')})"
            print(f"    run {r['run']}: {status} "
                  f"patch={r.get('patch_id', '-')} "
                  f"origin={r.get('origin_vertical', '-')} "
                  f"sim={r.get('similarity', 0):.4f} "
                  f"before={r.get('before_score', 0):.2f} -> "
                  f"after={r.get('after_score', 0):.2f}{extra}")

    if args.json:
        Path(args.json).write_text(json.dumps(results, indent=2, default=str))
        print(f"\n  wrote {args.json}")

    return 0 if all(r.get("ok") for r in results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
