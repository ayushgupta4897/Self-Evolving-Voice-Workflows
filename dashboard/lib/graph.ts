import type { Generation, GraphNode, Mutation, WorkflowGraph } from "./types";
import { survivorOf } from "./types";

/* -- mutation replay ------------------------------------------------------
 * A direct port of `core/evolve.apply_patch`. We replay rather than read a
 * per-generation graph file because the loop only checkpoints the *current*
 * graph — replaying from gen 0 is the only way to show gen N-1 vs gen N.
 * Same rules, same order, so the replayed head equals `state/graph_current.json`.
 */

export function targetNodeId(m: Mutation): string {
  return m.target.split(".", 1)[0];
}

export function nodePrompt(node: GraphNode | undefined): string {
  return (node?.data?.prompt as string) ?? "";
}

export function applyMutation(graph: WorkflowGraph, m: Mutation): WorkflowGraph {
  const g: WorkflowGraph = JSON.parse(JSON.stringify(graph));
  const id = targetNodeId(m);

  if (m.operation === "change_transition") {
    const edge = g.edges.find((e) => e.id === id);
    if (!edge) return g;
    edge.data = { ...(edge.data ?? {}), condition: m.diff };
    return g;
  }

  const node = g.nodes.find((n) => n.id === id);
  if (!node) return g;

  if (m.operation === "rewrite_instruction") {
    node.data = { ...node.data, prompt: m.diff };
  } else {
    const existing = nodePrompt(node).replace(/\s+$/, "");
    node.data = { ...node.data, prompt: `${existing}\n\n${m.diff.trim()}` };
  }
  return g;
}

/** Graph state after generation `n` has been promoted (n = 0 -> baseline). */
export function graphAtGeneration(
  base: WorkflowGraph,
  generations: Generation[],
  n: number,
): WorkflowGraph {
  let g = base;
  for (const gen of [...generations].sort((a, b) => a.number - b.number)) {
    if (gen.number > n) break;
    const s = survivorOf(gen);
    if (s) g = applyMutation(g, s.mutation);
  }
  return g;
}

/* -- text diff ------------------------------------------------------------
 * Sentence-level LCS. Coarse on purpose: a judge reads whole clauses in three
 * seconds, not intraword character runs.
 */

export type DiffOp = "equal" | "add" | "remove";
export interface DiffSegment {
  op: DiffOp;
  text: string;
}

function segment(text: string): string[] {
  return text
    .split(/\n+/)
    .flatMap((line) => line.split(/(?<=[.!?])\s+/))
    .map((s) => s.trim())
    .filter(Boolean);
}

export function diffText(oldText: string, newText: string): DiffSegment[] {
  const a = segment(oldText);
  const b = segment(newText);
  const n = a.length;
  const m = b.length;

  // LCS table. Instruction prompts are a handful of sentences; O(n*m) is free.
  const dp: number[][] = Array.from({ length: n + 1 }, () => new Array(m + 1).fill(0));
  for (let i = n - 1; i >= 0; i--) {
    for (let j = m - 1; j >= 0; j--) {
      dp[i][j] = a[i] === b[j] ? dp[i + 1][j + 1] + 1 : Math.max(dp[i + 1][j], dp[i][j + 1]);
    }
  }

  const out: DiffSegment[] = [];
  let i = 0;
  let j = 0;
  while (i < n && j < m) {
    if (a[i] === b[j]) {
      out.push({ op: "equal", text: a[i] });
      i++;
      j++;
    } else if (dp[i + 1][j] >= dp[i][j + 1]) {
      out.push({ op: "remove", text: a[i] });
      i++;
    } else {
      out.push({ op: "add", text: b[j] });
      j++;
    }
  }
  while (i < n) out.push({ op: "remove", text: a[i++] });
  while (j < m) out.push({ op: "add", text: b[j++] });
  return out;
}

export function diffStats(segs: DiffSegment[]) {
  return {
    added: segs.filter((s) => s.op === "add").length,
    removed: segs.filter((s) => s.op === "remove").length,
    kept: segs.filter((s) => s.op === "equal").length,
  };
}

/* -- layout ---------------------------------------------------------------
 * The graphs carry authored positions laid out for Dograh's full-screen canvas
 * (x spans 1420, y spans 560). Dropped into a half-width panel, fitView zooms
 * to ~0.5 and the node labels fall under 10px — unreadable from the back of a
 * room. We keep the authored *ordering* and compress the coordinate space so
 * the same layout survives at a zoom near 1.0.
 *
 * Both panels run through this, so gen 0 and gen N stay pixel-aligned and the
 * eye compares like for like.
 */
const COL_SPACING = 210;
const ROW_COMPRESSION = 0.38;

export function normalisePositions(graph: WorkflowGraph): WorkflowGraph {
  const nodes = graph.nodes.filter((n) => n.type !== "globalNode");
  const edges = graph.edges.filter(
    (e) => nodes.some((n) => n.id === e.source) && nodes.some((n) => n.id === e.target),
  );

  const columns = [...new Set(nodes.map((n) => n.position?.x ?? 0))].sort((a, b) => a - b);
  const laidOut = nodes.map((n) => ({
    ...n,
    position: {
      x: columns.indexOf(n.position?.x ?? 0) * COL_SPACING,
      y: (n.position?.y ?? 0) * ROW_COMPRESSION,
    },
  }));

  return { nodes: laidOut, edges };
}
