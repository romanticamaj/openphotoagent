// Left navigation (ADR-0029): what you have, what you are doing with it, and the machinery.
export type NavItem = { to: string; label: string };
export type NavSection = { key: string; items: NavItem[] };

export function navSections(faces: boolean): NavSection[] {
  return [
    {
      key: "nav.groupLibrary",
      items: [
        { to: "/", label: "nav.photos" },
        { to: "/explore", label: "nav.explore" },
        ...(faces ? [{ to: "/people", label: "nav.people" }] : []), // appears only once faces are on (07 §4.1; FACE-GATE-001)
        { to: "/map", label: "nav.map" },
      ],
    },
    {
      key: "nav.groupWork",
      items: [
        { to: "/import", label: "nav.import" },
        { to: "/organize", label: "nav.organize" },
        { to: "/review", label: "nav.review" },
        { to: "/duplicates", label: "nav.duplicates" },
      ],
    },
    {
      key: "nav.groupSystem",
      items: [
        { to: "/models", label: "nav.models" },
        { to: "/system/doctor", label: "nav.doctor" },
        { to: "/settings", label: "nav.settings" },
      ],
    },
  ];
}
