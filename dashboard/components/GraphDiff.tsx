"use client";

import { useMemo } from "react";
import type { Generation, StatePayload } from "@/lib/types";
import { survivorOf } from "@/lib/types";
import { diffText, graphAtGeneration, nodePrompt, targetNodeId } from "@/lib/graph";
import { embeddingText, humanise, signatureKey } from "@/lib/signature";
import { GraphPanel } from "./GraphPanel";
import { EmptyState } from "./EmptyState";

function DiffColumn({
  title,
  sub,
  segments,
  show,
}: {
  title: string;
  sub: string;
  segments: { op: string; text: string }[];
  show: "old" | "new";
}) {
  const visible = segments.filter((s) =>
    show === "old" ? s.op !== "add" : s.op !== "remove",
  );
  return (
    <div className="card-inset flex min-w-0 flex-1 flex-col">
      <div
        className="flex items-baseline gap-3 px-5 py-3"
        style={{ borderBottom: "1px solid var(--line-subtle)" }}
      >
        <span className="mono" style={{ fontSize: 15, fontWeight: 500, letterSpacing: "0.5px" }}>
          {title}
        </span>
        <span style={{ fontSize: 14, color: "var(--color-muted)" }}>{sub}</span>
      </div>
      <div className="scroll-y flex flex-col gap-1 p-3" style={{ maxHeight: 480 }}>
        {visible.map((s, i) => (
          <div
            key={i}
            className={`diff-line ${
              s.op === "add" ? "diff-add" : s.op === "remove" ? "diff-remove" : "diff-equal"
            }`}
          >
            <span className="diff-marker">
              {s.op === "add" ? "+" : s.op === "remove" ? "−" : ""}
            </span>
            <span>{s.text}</span>
          </div>
        ))}
      </div>
    </div>
  );
}

export function GraphDiff({
  state,
  selected,
  onSelect,
}: {
  state: StatePayload;
  selected: number;
  onSelect: (n: number) => void;
}) {
  const gens = state.generations;
  const gen: Generation | undefined =
    gens.find((g) => g.number === selected) ?? gens[gens.length - 1];

  const view = useMemo(() => {
    if (!gen) return null;
    const patch = survivorOf(gen);
    if (!patch) return null;

    const before = graphAtGeneration(state.graphBase, gens, gen.number - 1);
    const after = graphAtGeneration(state.graphBase, gens, gen.number);
    const nodeId = targetNodeId(patch.mutation);
    const isEdge = patch.mutation.operation === "change_transition";

    let oldText: string;
    let newText: string;
    if (isEdge) {
      oldText = (before.edges.find((e) => e.id === nodeId)?.data?.condition as string) ?? "";
      newText = (after.edges.find((e) => e.id === nodeId)?.data?.condition as string) ?? "";
    } else {
      oldText = nodePrompt(before.nodes.find((n) => n.id === nodeId));
      newText = nodePrompt(after.nodes.find((n) => n.id === nodeId));
    }

    const edgeNodeId = isEdge
      ? (after.edges.find((e) => e.id === nodeId)?.target ?? null)
      : nodeId;

    return {
      patch,
      after,
      segments: diffText(oldText, newText),
      nodeId,
      isEdge,
      edgeNodeId,
      nodeName:
        (after.nodes.find((n) => n.id === nodeId)?.data?.name as string) ?? nodeId,
    };
  }, [gen, gens, state.graphBase]);

  if (!gens.length || !gen) {
    return (
      <EmptyState
        title="No generations yet"
        body="The loop has not checkpointed a generation. This view populates the moment state/gen_001.json lands."
      />
    );
  }

  if (!view) {
    return (
      <EmptyState
        title={`Generation ${gen.number} promoted nothing`}
        body="Every candidate in this generation was killed on regression. The graph is unchanged — see the population board for the reasons."
      />
    );
  }

  const { patch, after, segments, nodeId, isEdge, edgeNodeId, nodeName } = view;
  const added = segments.filter((s) => s.op === "add");

  return (
    <div className="flex flex-col gap-6">
      {/* headline */}
      <div className="flex flex-wrap items-end justify-between gap-4">
        <div>
          <div className="label">Graph diff</div>
          <h2 className="heading" style={{ fontSize: 40, marginTop: 6 }}>
            {isEdge ? "Transition" : "Node"}{" "}
            <span style={{ color: "var(--color-add)" }}>{nodeName}</span> was rewritten by
            the loop
          </h2>
        </div>
        {gens.length > 1 && (
          <div className="flex items-center gap-2">
            <span style={{ fontSize: 14, color: "var(--color-muted)" }}>generation</span>
            {gens.map((g) => (
              <button
                key={g.number}
                onClick={() => onSelect(g.number)}
                className={`pill ${g.number === gen.number ? "pill-stone" : "pill-ghost"}`}
                style={{ fontSize: 15, cursor: "pointer" }}
              >
                {g.number}
              </button>
            ))}
          </div>
        )}
      </div>

      {/* graphs */}
      <div className="flex flex-wrap gap-5">
        <GraphPanel
          graph={state.graphBase}
          mutatedNodeId={edgeNodeId}
          changedEdgeId={isEdge ? nodeId : null}
          heading="GEN 0"
          subheading="baseline graph"
        />
        <GraphPanel
          graph={after}
          mutatedNodeId={edgeNodeId}
          changedEdgeId={isEdge ? nodeId : null}
          heading={`GEN ${gen.number}`}
          subheading="after promotion"
          accent
        />
      </div>

      {/* instruction diff */}
      <div className="card p-6">
        <div className="mb-4 flex flex-wrap items-center gap-3">
          <span className="pill pill-stone pill-upper">
            {humanise(patch.mutation.operation)}
          </span>
          <span className="mono" style={{ fontSize: 16, color: "var(--color-ink-2)" }}>
            {patch.mutation.target}
          </span>
          <span style={{ flex: 1 }} />
          <span style={{ fontSize: 15, color: "var(--color-muted)" }}>
            {added.length} line{added.length === 1 ? "" : "s"} added
          </span>
        </div>

        <div className="flex flex-wrap gap-4">
          <DiffColumn
            title={`BEFORE`}
            sub={gen.number === 1 ? "gen 0 baseline" : `after gen ${gen.number - 1}`}
            segments={segments}
            show="old"
          />
          <DiffColumn
            title={`AFTER`}
            sub={`gen ${gen.number}`}
            segments={segments}
            show="new"
          />
        </div>
      </div>

      {/* the rule, and the reason it is portable */}
      <div className="flex flex-wrap gap-5">
        <div className="card min-w-0 flex-[2] p-6">
          <div className="label">The rule that was added</div>
          <p
            style={{
              fontSize: 21,
              lineHeight: 1.5,
              letterSpacing: "0.18px",
              marginTop: 12,
              color: "#dff3e6",
            }}
          >
            {added.map((s) => s.text).join(" ") || patch.mutation.diff}
          </p>
          <p
            style={{
              fontSize: 15,
              lineHeight: 1.5,
              color: "var(--color-muted)",
              marginTop: 14,
            }}
          >
            Stated over a class of assertions, not over any one fact. Nothing in it names a
            product, a price or a vertical.
          </p>
        </div>

        <div className="card min-w-0 flex-1 p-6">
          <div className="label">Retrieval key &mdash; structure only</div>
          <p
            className="mono"
            style={{
              fontSize: 15,
              lineHeight: 1.75,
              color: "var(--color-ink-2)",
              marginTop: 12,
            }}
          >
            {embeddingText(patch.signature)}
          </p>
          <div className="mt-4 flex flex-wrap gap-2">
            {[
              humanise(patch.signature.failure_type),
              humanise(patch.signature.node_role),
              `tool ${patch.signature.tool_available ? "available" : "absent"}`,
              `tool ${patch.signature.tool_invoked ? "invoked" : "not invoked"}`,
              patch.signature.asserted_specific_value
                ? "asserted a value"
                : "no value asserted",
            ].map((t) => (
              <span
                key={t}
                className="pill"
                style={{
                  background: "var(--stone-dim)",
                  color: "var(--color-ink-2)",
                  fontSize: 13,
                  padding: "4px 12px",
                }}
              >
                {t}
              </span>
            ))}
          </div>
          <div
            className="mono"
            style={{ fontSize: 13, color: "var(--color-muted)", marginTop: 14 }}
          >
            {signatureKey(patch.signature)}
          </div>
        </div>
      </div>
    </div>
  );
}
