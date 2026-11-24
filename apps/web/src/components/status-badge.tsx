type Tone = "success" | "danger" | "warning" | "neutral";

const toneClasses: Record<Tone, string> = {
  success: "border-success/30 bg-success/10 text-success",
  danger: "border-danger/30 bg-danger/10 text-danger",
  warning: "border-warning/30 bg-warning/10 text-warning",
  neutral: "border-border-strong bg-surface-raised text-muted",
};

const dotClasses: Record<Tone, string> = {
  success: "bg-success",
  danger: "bg-danger",
  warning: "bg-warning",
  neutral: "bg-muted",
};

export function StatusBadge({ tone, label }: { tone: Tone; label: string }) {
  return (
    <span
      className={`inline-flex items-center gap-1.5 rounded-full border px-2.5 py-0.5 font-mono text-xs uppercase tracking-wider ${toneClasses[tone]}`}
    >
      <span className={`size-1.5 rounded-full ${dotClasses[tone]}`} aria-hidden="true" />
      {label}
    </span>
  );
}
