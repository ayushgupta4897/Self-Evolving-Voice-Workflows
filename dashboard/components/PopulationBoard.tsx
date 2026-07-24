"use client";

import type { Generation, StatePayload, WorkflowPatch } from "@/lib/types";
import { humanise } from "@/lib/signature";
import { EmptyState } from "./EmptyState";

function pct(x: number) {
  return `${Math.round(x * 100)}%`;
}

function StatusChip({ patch }: { patch: WorkflowPatch }) {
  if (patch.status === "promoted") {
    return (
      <span className="pill pill-stone pill-upper" style={{ padding: "5px 14px" }}>
        promoted
      </span>
    );
  }
  if (patch.status === "extinct") {
    return (
      <span
        className="pill pill-upper"
        style={{
          background: "rgba(224,122,122,0.14)",
          color: "var(--color-extinct)",
          padding: "5px 14px",
        }}
      >
        extinct
      </span>
    );
  }
  return (
    <span
      className="pill pill-upper"
      style={{ background: "var(--stone-dim)", color: "var(--color-ink-2)", padding: "5px 14px" }}
    >
      candidate
    </span>
  );
}

function ValidatorBlock({ patch }: { patch: WorkflowPatch }) {
  const v = patch.validation;
  if (!v) {
    return (
      <div style={{ fontSize: 15, color: "var(--color-muted)" }}>not yet validated</div>
    );
  }
  const regressed = v.regressions_introduced > 0;
  return (
    <div className="card-inset p-4">
      <div className="flex items-end justify-between gap-3">
        <div>
          <div
            className="mono"
            style={{
              fontSize: 13,
              letterSpacing: "0.5px",
              textTransform: "uppercase",
              color: "var(--color-muted)",
            }}
          >
            Validator pass rate
          </div>
          <div
            style={{
              fontSize: 38,
              fontWeight: 300,
              lineHeight: 1.1,
              letterSpacing: "-0.5px",
              marginTop: 2,
              color: patch.status === "promoted" ? "var(--color-ink)" : "var(--color-ink-2)",
            }}
          >
            {pct(v.confidence)}
          </div>
        </div>
        <div className="text-right" style={{ fontSize: 14, color: "var(--color-ink-2)" }}>
          <div className="mono">
            {v.historical_cases_passed}/{v.historical_cases_tested} historical cases
          </div>
          <div
            className="mono"
            style={{ color: regressed ? "var(--color-extinct)" : "var(--color-muted)" }}
          >
            {v.regressions_introduced} regression{v.regressions_introduced === 1 ? "" : "s"}
          </div>
          <div className="mono" style={{ color: "var(--color-muted)" }}>
            fixes new failure: {v.fixes_new_failure ? "yes" : "no"}
          </div>
        </div>
      </div>
      <div
        className="mono"
        style={{ fontSize: 13, color: "var(--color-muted)", marginTop: 10 }}
      >
        computed from replayed cases — passed/tested minus regression penalty. Not
        model-authored.
      </div>
    </div>
  );
}

function CandidateCard({ patch }: { patch: WorkflowPatch }) {
  const extinct = patch.status === "extinct";
  const promoted = patch.status === "promoted";
  return (
    <div
      className={`card flex min-w-0 flex-1 flex-col gap-4 p-5 ${extinct ? "extinct-card" : ""}`}
      style={
        promoted
          ? { boxShadow: "rgba(245,242,239,0.35) 0 0 0 1px, rgba(78,50,23,0.24) 0 6px 16px" }
          : undefined
      }
    >
      <div className="flex flex-wrap items-center gap-3">
        <StatusChip patch={patch} />
        <span className="mono" style={{ fontSize: 14, color: "var(--color-muted)" }}>
          {patch.patch_id}
        </span>
      </div>

      <div>
        <div
          className="mono"
          style={{ fontSize: 14, color: "var(--color-ink-2)", marginBottom: 6 }}
        >
          {humanise(patch.mutation.operation)} → {patch.mutation.target}
        </div>
        <p
          className={extinct ? "strike" : ""}
          style={{
            fontSize: 16,
            lineHeight: 1.5,
            letterSpacing: "0.16px",
            color: extinct ? "var(--color-ink-2)" : "var(--color-ink)",
          }}
        >
          {patch.mutation.diff}
        </p>
      </div>

      <ValidatorBlock patch={patch} />

      {patch.validation?.notes && (
        <div
          style={{
            fontSize: 16,
            lineHeight: 1.5,
            letterSpacing: "0.16px",
            color: extinct ? "var(--color-extinct)" : "var(--color-ink-2)",
            paddingLeft: 12,
            borderLeft: `2px solid ${
              extinct ? "rgba(224,122,122,0.45)" : "var(--line-subtle)"
            }`,
          }}
        >
          {extinct && (
            <span
              className="mono"
              style={{
                display: "block",
                fontSize: 13,
                textTransform: "uppercase",
                letterSpacing: "0.5px",
                marginBottom: 4,
              }}
            >
              cause of death
            </span>
          )}
          {patch.validation.notes}
        </div>
      )}

      {promoted && patch.reflection && (
        <div>
          <div className="label" style={{ fontSize: 13 }}>
            Reflection — model-authored explanation
          </div>
          <p
            style={{
              fontSize: 18,
              lineHeight: 1.55,
              letterSpacing: "0.18px",
              marginTop: 8,
              color: "var(--color-ink)",
            }}
          >
            {patch.reflection}
          </p>
        </div>
      )}

      <div
        className="mono"
        style={{
          fontSize: 13,
          color: "var(--color-muted)",
          marginTop: "auto",
          paddingTop: 6,
        }}
      >
        {patch.parent_id ? `parent ${patch.parent_id}` : "no parent — founder"} ·{" "}
        {patch.origin_vertical} · by {patch.authored_by}
      </div>
    </div>
  );
}

function GenerationRow({ gen }: { gen: Generation }) {
  const delta = gen.mean_fitness_after - gen.mean_fitness_before;
  const survivors = gen.candidates.filter((c) => c.status === "promoted").length;
  const dead = gen.candidates.filter((c) => c.status === "extinct").length;
  return (
    <section className="flex flex-col gap-4">
      <div
        className="flex flex-wrap items-baseline gap-x-6 gap-y-2 pb-3"
        style={{ borderBottom: "1px solid var(--color-line)" }}
      >
        <h3 className="heading" style={{ fontSize: 32 }}>
          Generation {gen.number}
        </h3>
        <span className="mono" style={{ fontSize: 15, color: "var(--color-muted)" }}>
          triggered by {gen.triggering_call_id}
        </span>
        <span style={{ flex: 1 }} />
        <span style={{ fontSize: 16, color: "var(--color-ink-2)" }}>
          {gen.candidates.length} candidates · {survivors} survived · {dead} extinct
        </span>
        <span
          className="mono"
          style={{
            fontSize: 16,
            color: delta >= 0 ? "var(--color-add)" : "var(--color-extinct)",
          }}
        >
          fitness {gen.mean_fitness_before.toFixed(3)} → {gen.mean_fitness_after.toFixed(3)}{" "}
          ({delta >= 0 ? "+" : ""}
          {delta.toFixed(3)})
        </span>
      </div>
      <div className="flex flex-wrap gap-5">
        {gen.candidates.map((c) => (
          <div key={c.patch_id} className="flex min-w-[340px] flex-1">
            <CandidateCard patch={c} />
          </div>
        ))}
      </div>
    </section>
  );
}

export function PopulationBoard({ state }: { state: StatePayload }) {
  if (!state.generations.length) {
    return (
      <EmptyState
        title="Population is empty"
        body="No generation has been checkpointed yet. Each generation writes three candidates; the ones the validator kills stay on this board permanently."
      />
    );
  }

  const total = state.generations.reduce((n, g) => n + g.candidates.length, 0);
  const killed = state.generations.reduce(
    (n, g) => n + g.candidates.filter((c) => c.status === "extinct").length,
    0,
  );

  return (
    <div className="flex flex-col gap-10">
      <div className="flex flex-wrap items-end justify-between gap-4">
        <div>
          <div className="label">Population board</div>
          <h2 className="heading" style={{ fontSize: 40, marginTop: 6 }}>
            {killed} of {total} candidates were killed by the validator
          </h2>
          <p
            style={{
              fontSize: 18,
              lineHeight: 1.6,
              letterSpacing: "0.18px",
              color: "var(--color-ink-2)",
              marginTop: 8,
              maxWidth: 860,
            }}
          >
            Extinct candidates are kept, never deleted. A pipeline applies one patch; this
            generates three, replays history against each, and keeps the one that breaks
            nothing.
          </p>
        </div>
      </div>
      {[...state.generations]
        .sort((a, b) => b.number - a.number)
        .map((g) => (
          <GenerationRow key={g.number} gen={g} />
        ))}
    </div>
  );
}
