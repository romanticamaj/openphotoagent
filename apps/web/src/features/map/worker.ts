// MapLibre looks for its worker next to its own module by default; Vite does not emit that file, so the installed
// app's map stayed blank ("Worker failed to load", MAP-010). Bundle the worker as an asset of this app instead: same
// origin, so the desktop CSP (default-src 'self') allows it without opening blob: or anything else.
import { setWorkerUrl } from "maplibre-gl";
import workerUrl from "maplibre-gl/dist/maplibre-gl-worker.mjs?url";

setWorkerUrl(workerUrl);
