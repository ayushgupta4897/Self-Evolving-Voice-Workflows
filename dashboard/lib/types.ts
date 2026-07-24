/**
 * Wire types. These mirror `core/schemas.py` exactly — the dashboard renders
 * what the loop writes and never reshapes it. Anything derived (promotable,
 * survivor, extinct) is computed here, never invented.
 */

export type PatchStatus = "candidate" | "extinct" | "promoted";

export type MutationOperator =
  | "append_constraint"
  | "rewrite_instruction"
  | "add_tool_requirement"
  | "change_transition";

export interface FailureSignature {
  failure_type: string;
  node_role: string;
  tool_available: boolean;
  tool_invoked: boolean;
  asserted_specific_value: boolean;
}

export interface Mutation {
  target: string;
  operation: MutationOperator;
  diff: string;
}

export interface Validation {
  fixes_new_failure: boolean;
  historical_cases_tested: number;
  historical_cases_passed: number;
  regressions_introduced: number;
  notes: string;
  confidence: number;
  confidence_source?: string;
}

export interface WorkflowPatch {
  generation: number;
  signature: FailureSignature;
  mutation: Mutation;
  reflection: string;
  authored_by: string;
  patch_id: string;
  parent_id: string | null;
  origin_vertical: string;
  validation: Validation | null;
  status: PatchStatus;
  created_at: number;
}

export interface Generation {
  number: number;
  triggering_call_id: string;
  promoted_patch_id: string | null;
  mean_fitness_before: number;
  mean_fitness_after: number;
  candidates: WorkflowPatch[];
}

export interface OracleVerdict {
  correctness_score: number;
  grounded: boolean;
  citation: string | null;
  ground_truth_value: string | null;
  failure_type: string | null;
  reasoning: string;
  source: "senso" | "llm_judge";
  escalated?: boolean;
  escalation_warranted?: boolean;
}

export interface TurnTrace {
  call_id: string;
  workflow_version: string;
  turn_index: number;
  node_id: string;
  node_role: string;
  node_instruction: string;
  caller_utterance: string;
  agent_utterance: string;
  tools_available: string[];
  tools_called: string[];
  transition_taken: string | null;
  latency_ms: number;
  verdict: OracleVerdict | null;
}

export interface CallTrace {
  call_id: string;
  workflow_version: string;
  vertical: string;
  persona_id: string;
  turns: TurnTrace[];
  task_completed: boolean;
  started_at: number;
}

/* -- React Flow graph ---------------------------------------------------- */

export interface GraphNode {
  id: string;
  type?: string;
  position: { x: number; y: number };
  selected?: boolean;
  data: {
    name?: string;
    prompt?: string;
    greeting?: string;
    [k: string]: unknown;
  };
}

export interface GraphEdge {
  id: string;
  source: string;
  target: string;
  data?: { label?: string; condition?: string; [k: string]: unknown };
}

export interface WorkflowGraph {
  nodes: GraphNode[];
  edges: GraphEdge[];
}

/* -- What /api/state returns --------------------------------------------- */

export type DataSource = "live" | "fixture";

export interface StatePayload {
  /** "live" = read from `state/`. "fixture" = fabricated sample, must be badged. */
  source: DataSource;
  /** Traces have their own source: the loop does not checkpoint CallTraces yet. */
  tracesSource: DataSource;
  stateDir: string;
  generations: Generation[];
  /** Baseline, always from `graphs/gen_0.json` when live. */
  graphBase: WorkflowGraph;
  /** `state/graph_current.json` when live, else replayed from gen 0. */
  graphCurrent: WorkflowGraph;
  traces: CallTrace[];
  readAt: number;
  warnings: string[];
}

/* -- Derived helpers ----------------------------------------------------- */

export function isPromotable(v: Validation | null): boolean {
  return !!v && v.fixes_new_failure && v.regressions_introduced === 0;
}

export function survivorOf(gen: Generation): WorkflowPatch | undefined {
  const byId = gen.promoted_patch_id
    ? gen.candidates.find((c) => c.patch_id === gen.promoted_patch_id)
    : undefined;
  return byId ?? gen.candidates.find((c) => c.status === "promoted");
}

export function extinctOf(gen: Generation): WorkflowPatch[] {
  return gen.candidates.filter((c) => c.status === "extinct");
}
