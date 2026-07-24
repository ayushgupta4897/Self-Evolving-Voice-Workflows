import { NextResponse } from "next/server";
import fs from "node:fs";
import path from "node:path";
import type {
  CallTrace,
  Generation,
  StatePayload,
  WorkflowGraph,
} from "@/lib/types";
import { graphAtGeneration } from "@/lib/graph";

export const dynamic = "force-dynamic";
export const revalidate = 0;

/**
 * Filesystem read of the loop's checkpoint directory.
 *
 * Everything here is defensive on purpose. `core/loop.py::_checkpoint` writes
 * with a plain `write_text`, so a 2s poll can land mid-write and see truncated
 * JSON. A file that does not parse is skipped with a warning rather than
 * failing the request — one bad read must never blank the stage.
 */

const REPO_ROOT = process.env.SWARM_REPO_ROOT
  ? path.resolve(process.env.SWARM_REPO_ROOT)
  : path.resolve(process.cwd(), "..");

const STATE_DIR = process.env.SWARM_STATE_DIR
  ? path.resolve(process.env.SWARM_STATE_DIR)
  : path.join(REPO_ROOT, "state");

const BASE_GRAPH = path.join(REPO_ROOT, "graphs", "gen_0.json");
const FIXTURE = path.join(process.cwd(), "fixtures", "sample_state.json");

function readJson<T>(file: string, warnings: string[]): T | null {
  try {
    if (!fs.existsSync(file)) return null;
    const raw = fs.readFileSync(file, "utf8");
    if (!raw.trim()) return null;
    return JSON.parse(raw) as T;
  } catch (err) {
    warnings.push(`could not parse ${path.basename(file)}: ${(err as Error).message}`);
    return null;
  }
}

const EMPTY_GRAPH: WorkflowGraph = { nodes: [], edges: [] };

export async function GET() {
  const warnings: string[] = [];

  const baseGraph = readJson<WorkflowGraph>(BASE_GRAPH, warnings);

  let generations: Generation[] = [];
  if (fs.existsSync(STATE_DIR)) {
    let files: string[] = [];
    try {
      files = fs
        .readdirSync(STATE_DIR)
        .filter((f) => /^gen_\d+\.json$/.test(f))
        .sort();
    } catch (err) {
      warnings.push(`could not list state dir: ${(err as Error).message}`);
    }
    for (const f of files) {
      const gen = readJson<Generation>(path.join(STATE_DIR, f), warnings);
      if (gen && typeof gen.number === "number" && Array.isArray(gen.candidates)) {
        generations.push(gen);
      }
    }
    generations.sort((a, b) => a.number - b.number);
  } else {
    warnings.push(`state dir not found at ${STATE_DIR}`);
  }

  // Traces are not checkpointed by the loop today. If a sibling agent starts
  // writing them, we pick them up automatically; until then this stays fixture
  // and is badged as such in the UI.
  let traces: CallTrace[] =
    readJson<CallTrace[]>(path.join(STATE_DIR, "traces.json"), warnings) ?? [];
  let tracesSource: StatePayload["tracesSource"] = traces.length ? "live" : "fixture";

  const live = generations.length > 0;
  let source: StatePayload["source"] = live ? "live" : "fixture";

  let graphBase: WorkflowGraph = baseGraph ?? EMPTY_GRAPH;
  let graphCurrent: WorkflowGraph =
    readJson<WorkflowGraph>(path.join(STATE_DIR, "graph_current.json"), warnings) ??
    (baseGraph ? graphAtGeneration(baseGraph, generations, Number.MAX_SAFE_INTEGER) : EMPTY_GRAPH);

  if (!live || !baseGraph || tracesSource === "fixture") {
    const fx = readJson<{
      generations: Generation[];
      graphBase: WorkflowGraph;
      graphCurrent: WorkflowGraph;
      traces: CallTrace[];
    }>(FIXTURE, warnings);

    if (fx) {
      if (!live) {
        generations = fx.generations ?? [];
        source = "fixture";
      }
      if (!baseGraph) {
        graphBase = fx.graphBase ?? EMPTY_GRAPH;
        graphCurrent = fx.graphCurrent ?? EMPTY_GRAPH;
      } else if (!live) {
        // Real baseline graph, fabricated generations: replay so the diff is
        // internally consistent with the graph the judge is looking at.
        graphCurrent = graphAtGeneration(graphBase, generations, Number.MAX_SAFE_INTEGER);
      }
      if (tracesSource === "fixture") {
        traces = fx.traces ?? [];
      }
    } else if (!live) {
      warnings.push("no live state and no fixture — rendering empty state");
    }
  }

  const payload: StatePayload = {
    source,
    tracesSource,
    stateDir: STATE_DIR,
    generations,
    graphBase,
    graphCurrent,
    traces,
    readAt: Date.now(),
    warnings,
  };

  return NextResponse.json(payload, {
    headers: { "Cache-Control": "no-store, max-age=0" },
  });
}
