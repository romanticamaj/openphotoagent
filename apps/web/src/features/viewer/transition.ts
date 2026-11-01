// Shared-element transition grid → Lightbox (04 §4.5, 10 §3.1). View Transitions API when present; otherwise a
// FLIP animation with the Web Animations API (WebKitGTK on Linux desktop lacks startViewTransition, VIEW-007).
type VT = { startViewTransition?: (cb: () => void) => unknown };

export function reducedMotion(): boolean {
  return typeof matchMedia === "function" && matchMedia("(prefers-reduced-motion: reduce)").matches;
}

export function withTransition(update: () => void, from?: Element | null, toSelector = "[data-testid=lightbox-stage]") {
  const doc = document as unknown as VT;
  if (!reducedMotion() && typeof doc.startViewTransition === "function") {
    doc.startViewTransition(update);
    return;
  }
  const first = from?.getBoundingClientRect();
  update();
  if (!first || reducedMotion()) return;
  requestAnimationFrame(() => {
    const to = document.querySelector<HTMLElement>(toSelector);
    if (!to || typeof to.animate !== "function") return;
    const last = to.getBoundingClientRect();
    const sx = first.width / Math.max(1, last.width);
    const sy = first.height / Math.max(1, last.height);
    to.animate(
      [
        { transformOrigin: "top left", transform: `translate(${first.left - last.left}px, ${first.top - last.top}px) scale(${sx}, ${sy})` },
        { transformOrigin: "top left", transform: "none" },
      ],
      { duration: 280, easing: "cubic-bezier(0.2, 0, 0, 1)" },
    );
  });
}
