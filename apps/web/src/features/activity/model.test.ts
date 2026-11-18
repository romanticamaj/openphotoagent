import { describe, expect, it } from "vitest";
import { opTypeKey } from "./model";
describe("what an operation is called", () => {
  it("[OP-020] 分析資料夾叫「AI 分析」，整理工作仍叫「整理」", () => {
    expect(opTypeKey({ type: "job.label", subject: { kind: "job", id: "j", job_kind: "label" } })).toBe("job.analyze");
    expect(opTypeKey({ type: "job.label", subject: { kind: "job", id: "j", job_kind: "organize" } })).toBe("job.label");
    expect(opTypeKey({ type: "scan" })).toBe("scan");
  });
});
