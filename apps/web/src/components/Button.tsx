import { useId, type ButtonHTMLAttributes, type MouseEvent } from "react";

/** Reason a control is unavailable — rendered next to it (invariant 8: never a bare disabled button). */
export type DisabledReason = { code: string; message: string };

type Props = Omit<ButtonHTMLAttributes<HTMLButtonElement>, "disabled"> & {
  disabledReason?: DisabledReason | null;
  variant?: "primary" | "ghost";
};

/**
 * GatedButton (06 §6.7): an unavailable button stays focusable (`aria-disabled`, not `disabled`) so keyboard and
 * screen-reader users reach it and hear why; the reason is shown next to it and linked by aria-describedby
 * (PF-080). Clicks are ignored while gated.
 */
export function Button({ disabledReason, variant = "primary", className = "", children, onClick, ...rest }: Props) {
  const off = Boolean(disabledReason);
  const uid = useId();
  const base =
    variant === "primary"
      ? "bg-[var(--accent)] text-[var(--accent-fg)] hover:brightness-110"
      : "border border-[var(--border)] hover:bg-[var(--surface-2)]";
  const id = off ? `why-${rest.id ?? uid.replace(/:/g, "")}` : undefined;
  return (
    <span className="inline-flex flex-col gap-1">
      <button
        {...rest}
        aria-disabled={off || undefined}
        aria-describedby={id}
        data-reason={disabledReason?.code}
        onClick={(e: MouseEvent<HTMLButtonElement>) => {
          if (off) {
            e.preventDefault();
            return;
          }
          onClick?.(e);
        }}
        className={`rounded-[var(--radius-md)] px-4 py-2 text-sm font-medium aria-disabled:cursor-not-allowed aria-disabled:opacity-50 aria-disabled:grayscale ${base} ${className}`}
      >
        {children}
      </button>
      {off && (
        <span id={id} role="note" className="text-xs text-[var(--text-muted)]">
          {disabledReason!.message}
        </span>
      )}
    </span>
  );
}
