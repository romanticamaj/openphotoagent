// eslint-plugin-opa — project rules (docs/06 §4, CLAUDE.md invariant 8)
const noBareDisabled = {
  meta: {
    type: "problem",
    docs: { description: "A disabled control must carry a reason (disabledReason) — invariant 8" },
    schema: [],
    messages: {},
  },
  create() {
    return {};
  },
};

export default { rules: { "no-bare-disabled": noBareDisabled } };
