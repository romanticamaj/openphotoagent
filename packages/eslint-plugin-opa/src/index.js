// eslint-plugin-opa — project rules (docs/06 §4, CLAUDE.md invariant 8)
const noBareDisabled = {
  meta: {
    type: "problem",
    docs: { description: "A disabled control must carry a reason (disabledReason) — invariant 8" },
    schema: [],
    messages: {
      bare: "Disabled control without a reason. Pass a disabledReason prop (error code + fixes) so users see why and how to fix it (invariant 8).",
    },
  },
  create(context) {
    return {
      JSXOpeningElement(node) {
        const attrs = node.attributes.filter((a) => a.type === "JSXAttribute");
        const disabled = attrs.find((a) => a.name.name === "disabled");
        if (!disabled) return;
        if (attrs.some((a) => a.name.name === "disabledReason")) return;
        // disabled={false} is never disabled
        const v = disabled.value;
        if (v && v.type === "JSXExpressionContainer" && v.expression.type === "Literal" && v.expression.value === false) return;
        context.report({ node: disabled, messageId: "bare" });
      },
    };
  },
};

export default { rules: { "no-bare-disabled": noBareDisabled } };
