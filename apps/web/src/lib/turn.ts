// A photo turned in the app (ROT-001/002): a view setting, never written to the file. The API already lays a quarter
// turn out in its new shape, so the image is drawn with its sides swapped, centred, and turned to fill that box.
import type { CSSProperties } from "react";

export function turnStyle(rot: number | null | undefined, boxW?: number, boxH?: number): CSSProperties {
  const r = ((rot ?? 0) % 360 + 360) % 360;
  if (!r) return {};
  if (r === 180 || boxW === undefined || boxH === undefined) return { transform: `rotate(${r}deg)` };
  return {
    position: "absolute",
    left: "50%",
    top: "50%",
    width: `${boxH}px`,
    height: `${boxW}px`,
    maxWidth: "none",
    transform: `translate(-50%, -50%) rotate(${r}deg)`,
  };
}
