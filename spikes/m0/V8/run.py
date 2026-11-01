"""M0-V8 — WebKitGTK capability probe (docs/09 §6, 11 V-8).

Loads probe.html in WebKit2GTK 4.1 (the engine Tauri 2 uses on Linux) under Xvfb
and records which viewer-relevant APIs are available. No pass/fail threshold:
the result decides whether VIEW-007 (View Transitions FLIP fallback) and
I18N-006 (Intl.DurationFormat fallback) are needed.

    sudo apt install gir1.2-webkit2-4.1 xvfb python3-gi
    xvfb-run -a /usr/bin/python3.12 spikes/m0/V8/run.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import gi  # noqa: E402

gi.require_version("Gtk", "3.0")
gi.require_version("WebKit2", "4.1")
from gi.repository import GLib, Gtk, WebKit2  # noqa: E402

from common import write_result  # noqa: E402

result: dict[str, object] = {}


def on_title(view: WebKit2.WebView, _p: object) -> None:
    t = view.get_title() or ""
    if t.startswith("RESULT:"):
        result.update(json.loads(t[len("RESULT:"):]))
        Gtk.main_quit()


def main() -> None:
    win = Gtk.OffscreenWindow()
    view = WebKit2.WebView()
    win.add(view)
    view.connect("notify::title", on_title)
    view.load_uri((Path(__file__).with_name("probe.html")).resolve().as_uri())
    win.show_all()
    GLib.timeout_add_seconds(30, Gtk.main_quit)
    Gtk.main()
    if not result:
        raise SystemExit("probe did not report")
    version = f"{WebKit2.get_major_version()}.{WebKit2.get_minor_version()}.{WebKit2.get_micro_version()}"
    result["webkitgtk_version"] = version
    needs = {
        "VIEW-007_flip_fallback_needed": not result.get("viewTransitions"),
        "I18N-006_duration_fallback_needed": not result.get("intlDurationFormat"),
    }
    result["decisions"] = needs
    write_result("M0-V8", status="measured", metrics=result, passed=None,
                 notes="Record-only experiment (no pass threshold). Ubuntu 24.04 WebKitGTK; 09 §6 names Ubuntu 22.04 — "
                       "22.04 ships an older WebKitGTK, so re-run there before relying on features absent in older versions.")
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
