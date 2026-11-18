import { beforeAll, describe, expect, it } from "vitest";
import i18n from "../../lib/i18n";
import { modelName } from "./modelName";

beforeAll(async () => {
  await i18n.changeLanguage("zh-TW");
});

describe("model names", () => {
  it("[ORG-014] 回歸：準備狀態寫模型的名字，不是 qwen3-vl-8b-instruct@q4_k_m（驗證第 16 輪）", () => {
    const t = i18n.t.bind(i18n);
    expect(modelName(t, "qwen3-vl-8b-instruct@q4_k_m")).toBe("Qwen3-VL 8B（看圖說明與分類）");
    expect(modelName(t, "siglip2-so400m-384")).toBe("SigLIP 2（以文搜圖）");
    expect(modelName(t, "some-new-model@x")).toBe("some-new-model"); // unknown: the id, without the variant
  });
});
