"""Correctness tests for the Actian patch store.

Four claims, in order of how much they matter to the demo:

  2. TRANSFER — a patch stored under `auto_servicing` is retrieved by a
     *healthcare* failure signature while healthcare is explicitly excluded.
     This is the whole pitch. It only works because
     `FailureSignature.to_embedding_text()` carries zero domain vocabulary; if
     domain content ever leaks into the signature the two vectors drift apart
     and this test is the thing that catches it.

  3. NEGATIVE CONTROL — a genuinely different signature must rank *below* the
     matching one. Without this, test 2 could pass because everything
     retrieves everything, and retrieval would be theatre.

  4. RESTART RESILIENCE — Actian persists collections but does not load them
     on boot. `ensure_ready()` must survive `docker restart vectorai`.

  1. ROUND TRIP — payload in equals payload out.

Run: .venv/bin/python tests/test_patch_store.py
"""

from __future__ import annotations

import subprocess
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from core.patch_store import PatchStore, embedder_name  # noqa: E402
from core.schemas import (  # noqa: E402
    FailureSignature,
    FailureType,
    Mutation,
    MutationOperator,
    NodeRole,
    PatchStatus,
    Validation,
    WorkflowPatch,
)

TEST_COLLECTION = "workflow_patches_test"
CONTAINER = "vectorai"


# ---------------------------------------------------------------------------
# Fixtures — the signatures are the point. Read these carefully.
# ---------------------------------------------------------------------------

def fabrication_signature() -> FailureSignature:
    """The canonical failure: a retrieval node had a tool, didn't call it, and
    stated a concrete number anyway. Produced *identically* by a brake-pricing
    call and a copay call — that is the mechanism under test."""
    return FailureSignature(
        failure_type=FailureType.UNGROUNDED_FABRICATION,
        node_role=NodeRole.INFORMATION_RETRIEVAL,
        tool_available=True,
        tool_invoked=False,
        asserted_specific_value=True,
    )


def escalation_signature() -> FailureSignature:
    """Negative control. Different failure type, different node role, tool was
    actually invoked, no specific value asserted. Nothing in common with the
    fabrication signature except being a failure."""
    return FailureSignature(
        failure_type=FailureType.PREMATURE_ESCALATION,
        node_role=NodeRole.ESCALATION,
        tool_available=True,
        tool_invoked=True,
        asserted_specific_value=False,
    )


def _validation() -> Validation:
    return Validation(
        fixes_new_failure=True,
        historical_cases_tested=8,
        historical_cases_passed=8,
        regressions_introduced=0,
        notes="replayed 8 historical calls, no regressions",
    )


def auto_servicing_patch() -> WorkflowPatch:
    """Learned on an auto-servicing call. Domain content is heavily
    auto-specific — brake pads, rotors, labour rates."""
    return WorkflowPatch(
        generation=1,
        signature=fabrication_signature(),
        mutation=Mutation(
            target="node_pricing_lookup.data.prompt",
            operation=MutationOperator.ADD_TOOL_REQUIREMENT,
            diff=(
                "You MUST call lookup_service_price before quoting any brake pad, "
                "rotor, or caliper price. Never state a labour rate or parts cost "
                "for a brake job from memory. If the parts catalog lookup returns "
                "nothing for the customer's vehicle make and model, say you need "
                "to check with the service advisor rather than estimating."
            ),
        ),
        reflection=(
            "The agent quoted $340 for a front brake pad and rotor replacement on a "
            "2019 Camry without querying the parts catalog. Brake pricing varies by "
            "vehicle trim and regional labour rate, so a memorised figure is close to "
            "guaranteed wrong. Forcing the service-price tool call before any quote "
            "removes the opportunity to answer from priors."
        ),
        origin_vertical="auto_servicing",
        validation=_validation(),
        status=PatchStatus.PROMOTED,
    )


def healthcare_patch() -> WorkflowPatch:
    """IDENTICAL signature, wildly different domain content. Never stored — it
    exists to prove the signature is domain-free, and to supply the healthcare
    query signature for the transfer test."""
    return WorkflowPatch(
        generation=1,
        signature=fabrication_signature(),
        mutation=Mutation(
            target="node_benefits_lookup.data.prompt",
            operation=MutationOperator.ADD_TOOL_REQUIREMENT,
            diff=(
                "You MUST call lookup_member_benefits before quoting any copay, "
                "coinsurance percentage, or deductible amount. Never state an "
                "out-of-pocket maximum or specialist visit copay from memory. If the "
                "member's plan is not found in the eligibility system, offer to "
                "connect them to a benefits coordinator."
            ),
        ),
        reflection=(
            "The agent told the member their specialist copay was $45 without "
            "querying the eligibility system. Copays vary by plan tier, network "
            "status, and whether the deductible has been met, so a remembered figure "
            "is a compliance exposure as well as a wrong answer. Requiring the "
            "benefits lookup before any dollar figure closes the gap."
        ),
        origin_vertical="healthcare",
        validation=_validation(),
        status=PatchStatus.PROMOTED,
    )


def escalation_patch() -> WorkflowPatch:
    """Negative control patch — genuinely different signature."""
    return WorkflowPatch(
        generation=1,
        signature=escalation_signature(),
        mutation=Mutation(
            target="node_escalation.data.condition",
            operation=MutationOperator.CHANGE_TRANSITION,
            diff=(
                "Only transfer to a human when the knowledge base lookup returned no "
                "answer AND the caller has asked twice. Do not transfer merely "
                "because the caller sounds frustrated."
            ),
        ),
        reflection=(
            "The agent transferred on a question it had already retrieved the answer "
            "for. Tightening the transition condition keeps warranted escalations "
            "intact while removing the reflexive ones."
        ),
        origin_vertical="auto_servicing",
        validation=_validation(),
        status=PatchStatus.PROMOTED,
    )


def extinct_patch() -> WorkflowPatch:
    """Killed by the Validator. Must still appear on the population board and
    must NEVER come back from `retrieve(promoted_only=True)`."""
    return WorkflowPatch(
        generation=2,
        signature=fabrication_signature(),
        mutation=Mutation(
            target="node_pricing_lookup.data.prompt",
            operation=MutationOperator.REWRITE_INSTRUCTION,
            diff="Never state any price under any circumstances. Always transfer.",
        ),
        reflection="Overcorrected into the degenerate optimum. Validator caught it.",
        origin_vertical="auto_servicing",
        validation=Validation(
            fixes_new_failure=True,
            historical_cases_tested=8,
            historical_cases_passed=3,
            regressions_introduced=4,
            notes="fixed the fabrication by refusing to answer anything",
        ),
        status=PatchStatus.EXTINCT,
    )


# ---------------------------------------------------------------------------
# Harness
# ---------------------------------------------------------------------------

FAILURES: list[str] = []
FINDINGS: list[str] = []


def check(cond: bool, msg: str) -> bool:
    print(f"  {'PASS' if cond else 'FAIL'}  {msg}")
    if not cond:
        FAILURES.append(msg)
    return cond


def banner(text: str) -> None:
    print(f"\n{'=' * 74}\n{text}\n{'=' * 74}")


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

def test_1_round_trip(store: PatchStore) -> None:
    banner("TEST 1 — ROUND TRIP")
    patch = auto_servicing_patch()
    point_id = store.store(patch)
    print(f"  stored patch_id={patch.patch_id} point_id={point_id}")

    hits = store.retrieve(patch.signature, limit=3, promoted_only=True)
    check(len(hits) >= 1, f"retrieve returned {len(hits)} hit(s)")
    if not hits:
        return

    top = hits[0]
    check(top["patch_id"] == patch.patch_id, f"top hit is the stored patch ({top['patch_id']})")
    check(top["_point_id"] == point_id, "point id round-tripped")
    check(top["_score"] > 0.99, f"self-similarity {top['_score']:.6f} > 0.99")

    expected = patch.to_actian_payload()
    actual = {k: v for k, v in top.items() if not k.startswith("_")}
    check(actual == expected, "payload matches EXACTLY (dict compare, key order ignored)")
    if actual != expected:
        for k in sorted(set(expected) | set(actual)):
            if expected.get(k) != actual.get(k):
                print(f"      MISMATCH {k}: sent={expected.get(k)!r} got={actual.get(k)!r}")
    else:
        print(f"      {len(expected)} payload fields identical; "
              f"bools stayed bools (tool_available={actual['tool_available']!r}), "
              f"floats stayed floats (confidence={actual['confidence']!r})")

    check(store.count() >= 1, f"count() = {store.count()}")


def test_2_transfer_property(store: PatchStore) -> None:
    banner("TEST 2 — THE TRANSFER PROPERTY  (the demo lives or dies here)")
    auto = auto_servicing_patch()
    hc = healthcare_patch()

    # Store ONLY the auto-servicing patch.
    store.store(auto)
    print(f"  stored ONLY auto patch {auto.patch_id} (origin_vertical=auto_servicing)")
    print(f"  healthcare patch {hc.patch_id} was NEVER stored")

    # Precondition: identical signatures, wildly different domain content.
    check(auto.signature == hc.signature, "signatures are structurally identical")
    auto_text = auto.signature.to_embedding_text()
    hc_text = hc.signature.to_embedding_text()
    check(auto_text == hc_text, "to_embedding_text() is byte-identical across verticals")
    print(f"      embedding text: {auto_text!r}")

    leak_terms = ["brake", "rotor", "caliper", "copay", "deductible", "camry",
                  "coinsurance", "auto", "health", "$", "340", "45"]
    leaked = [t for t in leak_terms if t in hc_text.lower()]
    check(not leaked, f"no domain vocabulary in signature text (leaked: {leaked or 'none'})")

    # THE QUERY: healthcare signature, healthcare origin excluded.
    hits = store.retrieve(
        hc.signature, limit=3, promoted_only=True, exclude_vertical="healthcare"
    )
    print(f"  retrieve(healthcare_signature, exclude_vertical='healthcare') -> {len(hits)} hit(s)")
    for h in hits:
        print(f"      score={h['_score']:.6f}  {h['patch_id']}  "
              f"origin={h['origin_vertical']}  op={h['operation']}")

    if not check(bool(hits), "a patch was retrieved despite excluding healthcare"):
        FINDINGS.append(
            "TRANSFER BROKEN: nothing retrieved for the healthcare signature with "
            "healthcare excluded. The cross-vertical beat does not work."
        )
        return

    top = hits[0]
    ok_origin = check(
        top["origin_vertical"] == "auto_servicing",
        f"retrieved patch was LEARNED ON AUTO SERVICING (origin={top['origin_vertical']})",
    )
    ok_id = check(top["patch_id"] == auto.patch_id, "it is the exact auto patch we stored")
    ok_score = check(top["_score"] > 0.99, f"similarity {top['_score']:.6f} > 0.99 (high)")
    ok_content = check(
        "brake" in top["diff"].lower(),
        "the returned diff is auto-domain content, applied to a healthcare failure",
    )
    check(
        "copay" not in top["diff"].lower(),
        "no healthcare content in the hit (it could not have been learned in-domain)",
    )

    if not (ok_origin and ok_id and ok_score and ok_content):
        FINDINGS.append(
            "TRANSFER DEGRADED: the auto patch did not come back cleanly for the "
            "healthcare signature. Suspect domain contamination in the signature "
            "embedding text."
        )


def test_3_negative_control(store: PatchStore) -> None:
    banner("TEST 3 — NEGATIVE CONTROL  (is retrieval discriminating at all?)")
    auto = auto_servicing_patch()
    esc = escalation_patch()
    store.store(auto)
    store.store(esc)
    print(f"  stored matching-signature patch  {auto.patch_id}  "
          f"({auto.signature.key()})")
    print(f"  stored different-signature patch {esc.patch_id}  "
          f"({esc.signature.key()})")

    hits = store.retrieve(fabrication_signature(), limit=10, promoted_only=True)
    ranked = [(h["patch_id"], h["_score"]) for h in hits]
    for i, (pid, score) in enumerate(ranked):
        print(f"      #{i + 1}  score={score:.6f}  {pid}")

    ids = [pid for pid, _ in ranked]
    if not check(auto.patch_id in ids and esc.patch_id in ids, "both patches retrievable"):
        return

    auto_rank = ids.index(auto.patch_id)
    esc_rank = ids.index(esc.patch_id)
    auto_score = dict(ranked)[auto.patch_id]
    esc_score = dict(ranked)[esc.patch_id]

    ok_rank = check(
        auto_rank < esc_rank,
        f"matching signature ranks ABOVE different signature (#{auto_rank + 1} vs #{esc_rank + 1})",
    )
    gap = auto_score - esc_score
    ok_gap = check(
        gap > 0.05,
        f"score gap {gap:.4f} > 0.05 (matching {auto_score:.4f} vs other {esc_score:.4f})",
    )

    # And the reverse query must flip the order — otherwise we have a fixed
    # ranking, not a similarity search.
    rev = store.retrieve(escalation_signature(), limit=10, promoted_only=True)
    rev_ids = [h["patch_id"] for h in rev]
    ok_rev = check(
        rev_ids and rev_ids[0] == esc.patch_id,
        f"reverse query ranks the escalation patch first (top={rev_ids[0] if rev_ids else None})",
    )

    if not (ok_rank and ok_gap and ok_rev):
        FINDINGS.append(
            "RETRIEVAL NOT DISCRIMINATING: signatures with nothing in common score "
            "comparably. Vector search is decorative — treat any retrieval result "
            "in the demo as unearned."
        )


def test_3b_status_filter(store: PatchStore) -> None:
    banner("TEST 3b — STATUS FILTER AND POPULATION BOARD")
    dead = extinct_patch()
    store.store(dead)
    print(f"  stored EXTINCT patch {dead.patch_id}")

    promoted = store.retrieve(fabrication_signature(), limit=10, promoted_only=True)
    check(
        dead.patch_id not in [h["patch_id"] for h in promoted],
        "extinct patch is NOT returned by promoted_only=True",
    )

    everything = store.retrieve(fabrication_signature(), limit=10, promoted_only=False)
    check(
        dead.patch_id in [h["patch_id"] for h in everything],
        "extinct patch IS returned by promoted_only=False",
    )

    board = store.all_patches()
    statuses = sorted({p["status"] for p in board})
    check(
        dead.patch_id in [p["patch_id"] for p in board],
        f"all_patches() includes the extinct patch ({len(board)} total, statuses={statuses})",
    )
    check(
        board == sorted(board, key=lambda d: (d["generation"], d["created_at"])),
        "all_patches() is ordered by generation then time (reads as a lineage)",
    )
    check(store.count() == len(board), f"count() == len(all_patches()) == {store.count()}")


def test_4_restart_resilience(store: PatchStore) -> None:
    banner("TEST 4 — RESTART RESILIENCE  (the /open gotcha)")
    before = store.count()
    auto = auto_servicing_patch()
    store.store(auto)
    print(f"  {before} point(s) before restart; stored {auto.patch_id}")

    print(f"  $ docker restart {CONTAINER}")
    t0 = time.time()
    res = subprocess.run(
        ["docker", "restart", CONTAINER], capture_output=True, text=True, timeout=180
    )
    if not check(res.returncode == 0, f"docker restart succeeded ({res.stderr.strip()[:120]})"):
        return
    print(f"      restarted in {time.time() - t0:.1f}s")

    # Prove the gotcha is real before proving we handle it: raw search on the
    # unopened collection must fail.
    fresh = PatchStore(collection=TEST_COLLECTION)
    fresh.wait_for_server(timeout=120)
    raw_failed = False
    try:
        fresh.client.points.count(TEST_COLLECTION)
    except Exception as exc:  # noqa: BLE001
        raw_failed = True
        print(f"      (gotcha confirmed: raw count() before open -> "
              f"{type(exc).__name__}: {str(exc)[:90]})")
    if not raw_failed:
        print("      (note: collection auto-loaded this time; ensure_ready() is still required "
              "in general — the recon doc observed it closed)")

    # Now the thing under test.
    fresh.ensure_ready()
    check(True, "ensure_ready() completed on a freshly restarted container")

    hits = fresh.retrieve(fabrication_signature(), limit=5, promoted_only=True)
    check(bool(hits), f"retrieve() works after restart ({len(hits)} hit(s))")
    check(
        auto.patch_id in [h["patch_id"] for h in hits],
        "the patch stored before the restart survived and is retrievable",
    )
    check(fresh.count() >= before, f"count() = {fresh.count()} (was {before} + new stores)")
    fresh.close()

    # The already-constructed store had its gRPC channel killed by the
    # restart. ensure_ready() must recover it in place, not just in a new
    # process — the demo runs in one long-lived process.
    store.ensure_ready()
    check(bool(store.retrieve(fabrication_signature(), limit=1, promoted_only=True)),
          "the pre-existing store instance recovered its stale channel via ensure_ready()")


# ---------------------------------------------------------------------------

def main() -> int:
    banner("SETUP")
    print(f"  embedder: {embedder_name()}")
    store = PatchStore(collection=TEST_COLLECTION)
    store.ensure_ready()
    store.reset()
    print(f"  collection {TEST_COLLECTION!r} ready, count={store.count()}")

    test_1_round_trip(store)

    store.reset()
    test_2_transfer_property(store)

    store.reset()
    test_3_negative_control(store)
    test_3b_status_filter(store)

    test_4_restart_resilience(store)

    banner("RESULT")
    if FAILURES:
        print(f"  {len(FAILURES)} CHECK(S) FAILED:")
        for f in FAILURES:
            print(f"    - {f}")
    else:
        print("  ALL CHECKS PASSED")
    if FINDINGS:
        print("\n  ARCHITECTURAL FINDINGS (report these, do not paper over):")
        for f in FINDINGS:
            print(f"    ! {f}")

    # Be a good citizen: leave the real demo collection open for other agents.
    demo = PatchStore()
    demo.ensure_ready()
    print(f"\n  demo collection 'workflow_patches' left open, count={demo.count()}")
    demo.close()
    store.close()
    return 1 if FAILURES else 0


if __name__ == "__main__":
    raise SystemExit(main())
