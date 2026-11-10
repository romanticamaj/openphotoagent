// A model as people read it (ORG-014): 「Qwen3-VL 8B（看圖說明與分類）」, not qwen3-vl-8b-instruct@q4_k_m.
type T = (k: string, o?: Record<string, unknown>) => string;

export function modelName(t: T, key: string): string {
  const id = key.split("@")[0];
  return t(`models.names.${id}`, { defaultValue: id });
}
