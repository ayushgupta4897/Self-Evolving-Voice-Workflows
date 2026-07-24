"""Entry point for the autonomous evolution loop.

    .venv/bin/python scripts/run_evolution.py --rounds 2
    .venv/bin/python scripts/run_evolution.py --persona p_brake_price_sedan
    .venv/bin/python scripts/run_evolution.py --rounds 3 --no-dograh   # depth only

Every failing call triggers a full generation with no human in the chain.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv

load_dotenv(ROOT / ".env.local")

from core.executor import load_kb  # noqa: E402
from core.loop import EvolutionLoop, LoopConfig  # noqa: E402

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-7s %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("run")


def build_personas(only: str | None) -> list[dict]:
    scripts = json.loads((ROOT / "personas/caller_scripts.json").read_text())["scripts"]
    meta = {p["id"]: p for p in
            json.loads((ROOT / "personas/auto_servicing.json").read_text())["personas"]}

    personas = [
        {"id": pid, "caller_turns": turns, "targets": meta.get(pid, {}).get("targets", "")}
        for pid, turns in scripts.items()
    ]
    if only:
        personas = [p for p in personas if p["id"] == only]
        if not personas:
            raise SystemExit(f"no persona named {only}")
    return personas


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--rounds", type=int, default=1)
    ap.add_argument("--persona", default=None)
    ap.add_argument("--workflow-id", type=int,
                    default=int(os.environ.get("DOGRAH_WORKFLOW_ID", 0)) or None)
    ap.add_argument("--no-dograh", action="store_true",
                    help="build generation depth without touching the live graph")
    ap.add_argument("--graph", default="graphs/gen_0.json")
    ap.add_argument("--warmup", action="store_true",
                    help="score the full population first so generation 1 has "
                         "real history to regress against")
    args = ap.parse_args()

    graph = json.loads((ROOT / args.graph).read_text())
    kb = load_kb(str(ROOT / "kb/auto_servicing.md"))

    from core.oracle import get_oracle

    oracle = get_oracle()
    usable, why = oracle.health()
    log.info("ORACLE: %s — %s", getattr(oracle, "source", "?"), why)
    if not usable:
        log.error("oracle unusable; refusing to run — every fitness number would be meaningless")
        return 1

    patch_store = None
    try:
        from core.patch_store import PatchStore

        patch_store = PatchStore()
        patch_store.ensure_ready()
        log.info("ACTIAN: ready, %d patches stored", patch_store.count())
    except Exception as exc:  # noqa: BLE001
        log.warning("ACTIAN unavailable, running without patch memory: %s", exc)

    loop = EvolutionLoop(
        graph=graph, kb=kb, oracle=oracle, patch_store=patch_store,
        config=LoopConfig(
            workflow_id=args.workflow_id,
            push_to_dograh=not args.no_dograh and bool(args.workflow_id),
        ),
    )
    log.info("agent inference provider: %s", loop.executor.provider)

    personas = build_personas(args.persona)
    log.info("running %d persona(s) x %d round(s)", len(personas), args.rounds)

    if args.warmup:
        loop.warmup(build_personas(None))

    loop.run_batch(personas, rounds=args.rounds)

    promoted = [g for g in loop.generations if g.promoted_patch_id]
    dead = sum(len(g.extinct) for g in loop.generations)
    unselected = sum(len(g.viable_not_selected) for g in loop.generations)
    real_selection = sum(1 for g in loop.generations if g.selection_occurred)

    print("\n" + "=" * 68)
    print(f"generations run     : {len(loop.generations)}")
    print(f"generations ERRORED : {loop.failed_generations}")
    print(f"patches promoted    : {len(promoted)}")
    print(f"candidates KILLED   : {dead}   <- gate rejected these")
    print(f"viable, not chosen  : {unselected}   <- passed gate, lost tiebreak")
    print(f"gens w/ real select : {real_selection}/{len(loop.generations)}")
    print(f"calls scored        : {len(loop.traces)}")
    if loop.traces:
        print(f"mean fitness (last8): {loop._mean_fitness():.3f}")
    print("=" * 68)

    for gen in loop.generations:
        survivor = gen.survivor
        print(f"\ngen {gen.number}  trigger={gen.triggering_call_id}"
              f"  fitness {gen.mean_fitness_before:.3f} -> {gen.mean_fitness_after:.3f}")
        for patch in gen.candidates:
            v = patch.validation
            mark = {"promoted": "PROMOTED", "viable": "viable  ", "extinct": "KILLED  "}[patch.status.value]
            detail = (f"fixes={v.fixes_new_failure} regressions={v.regressions_introduced}"
                      f" conf={v.confidence:.2f}") if v else "not validated"
            print(f"  [{mark}] {patch.mutation.operation.value:<22} {detail}")
        if survivor:
            print(f"  reflection: {survivor.reflection[:180]}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
