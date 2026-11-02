import { describe, expect, it } from "vitest";
import { notificationFor } from "./notify";

describe("OS notifications (08 §6, DESK-022)", () => {
  it("[DESK-022] finished long work notifies; routine steps and other states do not", () => {
    expect(notificationFor({ id: "j1", type: "job.label", status: "succeeded" })).toEqual({ key: "notify.labelDone", route: "/organize" });
    expect(notificationFor({ id: "a1", type: "output.apply", status: "succeeded" })).toEqual({ key: "notify.applyDone", route: "/organize" });
    expect(notificationFor({ id: "d1", type: "model.download", status: "succeeded" })).toEqual({ key: "notify.modelReady", route: "/models" });
    expect(notificationFor({ id: "j2", type: "job.label", status: "failed" })).toEqual({ key: "notify.failed", route: "/" });
    expect(notificationFor({ id: "t1", type: "thumbnail", status: "succeeded" })).toBeNull();
    expect(notificationFor({ id: "j3", type: "job.label", status: "running" })).toBeNull();
    expect(notificationFor({ id: "j4", status: "succeeded" })).toBeNull(); // older events without a type
  });
});
