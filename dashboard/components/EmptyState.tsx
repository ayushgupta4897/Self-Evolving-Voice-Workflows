export function EmptyState({ title, body }: { title: string; body: string }) {
  return (
    <div
      className="card flex flex-col items-center justify-center px-8 text-center"
      style={{ minHeight: 420 }}
    >
      <div className="label">Waiting</div>
      <h3 className="heading" style={{ fontSize: 34, marginTop: 12 }}>
        {title}
      </h3>
      <p
        style={{
          fontSize: 18,
          lineHeight: 1.6,
          letterSpacing: "0.18px",
          color: "var(--color-ink-2)",
          maxWidth: 620,
          marginTop: 14,
        }}
      >
        {body}
      </p>
      <div className="mt-6 flex items-center gap-3">
        <span className="live-dot" />
        <span style={{ fontSize: 15, color: "var(--color-muted)" }}>polling every 2s</span>
      </div>
    </div>
  );
}
