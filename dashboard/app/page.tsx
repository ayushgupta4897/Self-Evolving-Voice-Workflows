"use client";

import { useEffect, useState } from "react";
import { useSwarmState } from "@/lib/useSwarmState";
import { GraphDiff } from "@/components/GraphDiff";
import { PopulationBoard } from "@/components/PopulationBoard";
import { FitnessTrend } from "@/components/FitnessTrend";
import { RawJsonDrawer } from "@/components/RawJsonDrawer";

type View = "diff" | "population" | "fitness";

const VIEWS: { id: View; label: string; n: string }[] = [
  { id: "diff", label: "Graph diff", n: "01" },
  { id: "population", label: "Population", n: "02" },
  { id: "fitness", label: "Fitness", n: "03" },
];

export default function Page() {
  const { data, stale, loading, error } = useSwarmState(2000);
  const [view, setView] = useState<View>("diff");

  // Hash routing so each view is directly linkable — #diff / #population /
  // #fitness. Cheap insurance for a live demo: if a click misses, the URL
  // still gets you to the right panel.
  useEffect(() => {
    const apply = () => {
      const h = window.location.hash.replace("#", "");
      if (h === "diff" || h === "population" || h === "fitness") setView(h);
    };
    apply();
    window.addEventListener("hashchange", apply);
    return () => window.removeEventListener("hashchange", apply);
  }, []);

  const [selectedGen, setSelectedGen] = useState<number>(0);
  const [rawOpen, setRawOpen] = useState(false);

  // Follow the head of the run unless the operator has pinned a generation.
  //
  // "Head" means the most recent generation that actually PROMOTED something,
  // not simply the highest-numbered one. Most generations kill all three
  // candidates — that is the gate working — but a graph-diff view has nothing
  // to diff for those, so defaulting to the numeric head lands on an empty
  // state most of the time. Falls back to the true latest so a run with zero
  // promotions still renders (and still says so).
  const [pinned, setPinned] = useState(false);
  const promotedGens = data?.generations.filter((g) => g.promoted_patch_id) ?? [];
  const latest = promotedGens.length
    ? promotedGens[promotedGens.length - 1].number
    : data?.generations.length
      ? data.generations[data.generations.length - 1].number
      : 0;
  useEffect(() => {
    if (!pinned) setSelectedGen(latest);
  }, [latest, pinned]);

  const sample = data?.source === "fixture";

  return (
    <div className="min-h-screen">
      {/* nav */}
      <nav
        className="sticky top-0 z-40 flex flex-wrap items-center justify-between gap-4 px-8 py-4"
        style={{
          background: "rgba(10,10,10,0.94)",
          backdropFilter: "blur(12px)",
          borderBottom: "1px solid var(--line-subtle)",
        }}
      >
        <div className="flex items-baseline gap-4">
          <span style={{ fontSize: 17, fontWeight: 500, letterSpacing: "0.17px" }}>
            Swarm Evolution
          </span>
          <span className="mono" style={{ fontSize: 14, color: "var(--color-muted)" }}>
            self-evolving voice workflow
          </span>
        </div>

        <div className="flex items-center gap-2">
          {VIEWS.map((v) => (
            <button
              key={v.id}
              onClick={() => {
                setView(v.id);
                window.history.replaceState(null, "", `#${v.id}`);
              }}
              className={`pill ${view === v.id ? "pill-stone" : "pill-ghost"}`}
              style={{ fontSize: 15, cursor: "pointer" }}
            >
              <span className="mono" style={{ fontSize: 13, opacity: 0.65 }}>
                {v.n}
              </span>
              {v.label}
            </button>
          ))}
        </div>

        <div className="flex items-center gap-3">
          <span
            className="pill"
            style={{
              background: sample ? "rgba(224,122,122,0.16)" : "var(--stone-dim)",
              color: sample ? "var(--color-extinct)" : "var(--color-ink-2)",
              fontSize: 14,
            }}
          >
            {!sample && !stale && <span className="live-dot" />}
            {loading
              ? "reading state…"
              : sample
                ? "SAMPLE DATA"
                : stale
                  ? "reconnecting…"
                  : `live · ${data?.generations.length ?? 0} gen${
                      (data?.generations.length ?? 0) === 1 ? "" : "s"
                    }`}
          </span>
          <button
            onClick={() => setRawOpen((o) => !o)}
            className="pill pill-ghost"
            style={{ fontSize: 14, cursor: "pointer" }}
          >
            raw json
          </button>
        </div>
      </nav>

      {/* the badge that must be impossible to miss */}
      {sample && (
        <div
          className="flex flex-wrap items-center gap-4 px-8 py-3"
          style={{
            background: "rgba(224,122,122,0.14)",
            borderBottom: "1px solid rgba(224,122,122,0.4)",
          }}
        >
          <span
            className="pill pill-upper"
            style={{
              background: "var(--color-extinct)",
              color: "#0a0a0a",
              padding: "6px 16px",
            }}
          >
            Sample data
          </span>
          <span style={{ fontSize: 17, letterSpacing: "0.17px", color: "#f0d4d4" }}>
            No generations found in{" "}
            <span className="mono">{data?.stateDir ?? "state/"}</span> — every number and
            candidate on screen is fabricated fixture data, not a run. It disappears the
            moment the loop checkpoints <span className="mono">gen_001.json</span>.
          </span>
        </div>
      )}

      {error && !data && (
        <div
          className="px-8 py-3"
          style={{ background: "rgba(224,122,122,0.14)", fontSize: 16 }}
        >
          could not read /api/state: {error}
        </div>
      )}

      <main className="px-8 py-8" style={{ paddingBottom: rawOpen ? "64vh" : 48 }}>
        {!data ? (
          <div
            className="card flex items-center justify-center"
            style={{ minHeight: 420, fontSize: 18, color: "var(--color-muted)" }}
          >
            reading state…
          </div>
        ) : view === "diff" ? (
          // Replay.io filed this as a high-severity bug and its own judge rejected
          // it; the judge was wrong. We write `#diff` / `#population` / `#fitness`
          // into the URL for deep-linking, but shipped no element carrying those
          // ids — so the hash promised an anchor target that did not exist. It
          // happened to work because a hashchange effect swaps the view, which is
          // why a human never noticed and why our own first investigation (which
          // only tested the nav buttons) cleared it. Giving each view its real id
          // makes the anchor honest and the page navigable without JS.
          <div id="diff">
            <GraphDiff
              state={data}
              selected={selectedGen}
              onSelect={(n) => {
                setPinned(true);
                setSelectedGen(n);
              }}
            />
          </div>
        ) : view === "population" ? (
          <div id="population">
            <PopulationBoard state={data} />
          </div>
        ) : (
          <div id="fitness">
            <FitnessTrend state={data} />
          </div>
        )}
      </main>

      {data && (
        <RawJsonDrawer state={data} open={rawOpen} onClose={() => setRawOpen(false)} />
      )}
    </div>
  );
}
