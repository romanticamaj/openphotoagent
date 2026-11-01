# M0-S3 / M0-S3b — llama.cpp Qwen3-VL 結構化輸出（pending-hardware）

在每台參考機：5070 Ti（CUDA 13 pack）、RTX 4060（CUDA）、AMD／Intel（Vulkan）、M2（Metal，4B）。

1. 下載 llama.cpp 官方預編版（對應後端）與 Qwen3-VL GGUF + mmproj（8B Q4_K_M；M2 用 4B）。
2. 啟動：`llama-server -m <model.gguf> --mmproj <mmproj.gguf> --port 8080 -c 8192 -ngl 99 --parallel 2`
3. 準備 1,000 張測試圖庫子集，放 `labels.json`（12 類 key 見 `llama_client.CATEGORIES_12`）。
4. `OPAGENT_M0_MACHINE=rtx5070ti-win11 uv run python spikes/m0/S3/run.py --images D:\m0\subset1000`
5. S3b：`... --key-order category_first --id M0-S3b-category_first`，與預設（caption_first）比較 `accuracy`，差 > 2 pp 時更新 03 §3.0 與 INF-JSON-007。

通過：首次合法 ≥ 99%、修復後 ≥ 99.9%、`logprobs_available_rate` = 1（否則改用 embedding margin + 自我一致性，03 §3.3.3）。吞吐寫入 registry `perf_prior`。
