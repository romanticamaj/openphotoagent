# M0-S4 — SigLIP2 ONNX 一致性

```bash
# 參考機（Windows 5070 Ti：CPU,DML；Mac：CPU,COREML）
uv run --with torch,transformers,onnx,onnxruntime-directml,pillow python spikes/m0/S4/run.py \
  --model google/siglip2-so400m-patch14-384 --images <500 張> --texts spikes/m0/S4/texts.txt --eps CPU,DML
```
通過：每個向量 cosine ≥ 0.999。不通過：改用 onnx-community 的 patch16 匯出並重新評估。
雲端只跑 `--tiny-random`（隨機權重的小模型）驗證匯出／比對流程；**不代表真權重結果**。
