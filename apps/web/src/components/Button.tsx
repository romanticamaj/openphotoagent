import type { ButtonHTMLAttributes } from "react";

/** Reason a control is unavailable — rendered next to it (invariant 8: never a bare disabled button). */
export type DisabledReason = { code: string; message: string };

type Props = Omit<ButtonHTMLAttributes<HTMLButtonElement>, "disabled"> & {
  disabledReason?: DisabledReason | null;
  variant?: "primary" | "ghost";
};

export function Button({ disabledReason, variant = "primary", className = "", children, ...rest }: Props) {
  const off = Boolean(disabledReason);
  const base =
    variant === "primary"
      ? "bg-[var(--color-accent)] text-black hover:brightness-110"
      : "border border-white/15 hover:bg-white/5";
  const id = off ? `why-${rest.id ?? Math.random().toString(36).slice(2)}` : undefined;
  return (
    <span className="inline-flex flex-col gap-1">
      <button
        {...rest}
        // eslint-disable-next-line opa/no-bare-disabled -- the reason is rendered below and linked by aria-describedby
        disabled={off}
        aria-describedby={id}
        className={`rounded-md px-4 py-2 text-sm font-medium disabled:opacity-50 ${base} ${className}`}
      >
        {children}
      </button>
      {off && (
        <span id={id} className="text-xs text-[var(--color-muted)]">
          {disabledReason!.message}
        </span>
      )}
    </span>
  );
}
