import { RuleTester } from "eslint";
import { describe, it } from "vitest";
import plugin from "../src/index.js";

RuleTester.describe = describe;
RuleTester.it = it;

const tester = new RuleTester({
  languageOptions: { parserOptions: { ecmaFeatures: { jsx: true } } },
});

describe("[PF-079] opa/no-bare-disabled", () => {
  tester.run("no-bare-disabled", plugin.rules["no-bare-disabled"], {
    valid: [
      { code: "<Button disabledReason={{ code: 'MODEL_NOT_INSTALLED' }}>Go</Button>" },
      { code: "<Button disabled={x} disabledReason={reason}>Go</Button>" },
      { code: "<Button onClick={go}>Go</Button>" },
      { code: "<input disabled={false} />" },
    ],
    invalid: [
      { code: "<Button disabled={x}>Go</Button>", errors: [{ messageId: "bare" }] },
      { code: "<Button disabled>Go</Button>", errors: [{ messageId: "bare" }] },
      { code: "<button disabled={busy}>Go</button>", errors: [{ messageId: "bare" }] },
    ],
  });
});
