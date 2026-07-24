"use client";

import type { StatePayload } from "@/lib/types";

export function RawJsonDrawer({
  state,
  open,
  onClose,
}: {
  state: StatePayload;
  open: boolean;
  onClose: () => void;
}) {
  if (!open) return null;
  return (
    <div
      className="fixed inset-x-0 bottom-0 z-50 flex flex-col"
      style={{
        height: "62vh",
        background: "var(--color-surface)",
        borderTop: "1px solid var(--color-line)",
        boxShadow: "rgba(0,0,0,0.6) 0 -12px 32px",
      }}
    >
      <div
        className="flex items-center justify-between px-6 py-3"
        style={{ borderBottom: "1px solid var(--line-subtle)" }}
      >
        <div className="flex items-baseline gap-4">
          <span className="label" style={{ fontSize: 14 }}>
            Raw state
          </span>
          <span className="mono" style={{ fontSize: 14, color: "var(--color-muted)" }}>
            {state.stateDir}
          </span>
          {state.warnings.length > 0 && (
            <span className="mono" style={{ fontSize: 14, color: "var(--color-extinct)" }}>
              {state.warnings.length} warning{state.warnings.length === 1 ? "" : "s"}
            </span>
          )}
        </div>
        <button onClick={onClose} className="pill pill-ghost" style={{ cursor: "pointer" }}>
          close
        </button>
      </div>
      <div className="scroll-y flex-1 px-6 py-4">
        {state.warnings.length > 0 && (
          <ul className="mb-4">
            {state.warnings.map((w, i) => (
              <li
                key={i}
                className="mono"
                style={{ fontSize: 14, color: "var(--color-extinct)", lineHeight: 1.7 }}
              >
                ! {w}
              </li>
            ))}
          </ul>
        )}
        <pre
          className="mono"
          style={{
            fontSize: 14,
            lineHeight: 1.7,
            color: "var(--color-ink-2)",
            whiteSpace: "pre-wrap",
            wordBreak: "break-word",
          }}
        >
          {JSON.stringify(state, null, 2)}
        </pre>
      </div>
    </div>
  );
}
