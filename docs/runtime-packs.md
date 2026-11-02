# 進階 runtime pack（v0.4）

安裝包內建 llama.cpp（CPU／Metal）與 ONNX Runtime（CPU／DirectML／CoreML），零下載就能使用。下列 pack 是**選用**的，只在你需要更高吞吐或特定模型時安裝（08 §4.2）。

| Pack | 平台 | 用途 |
|---|---|---|
| `mlx` | macOS（Apple Silicon） | MLX／mlx-vlm 的 OpenAI 相容 server |
| `vllm-cu128` | Linux x64＋NVIDIA | 大批次吞吐（vLLM） |
| `torch-cu128`／`torch-mps`／`torch-cpu` | 依平台 | 只有 transformers 能跑的模型（JoyCaption、Florence-2） |

## vLLM on Windows or macOS — use Docker (docker / WSL2) {#vllm-docker}

vLLM 沒有官方的 Windows 原生版本（08 §3.1），所以 app 不提供 Windows 的 `vllm-cu128` pack。要在 Windows 使用 vLLM：

1. 安裝 WSL2 與 NVIDIA 的 WSL 驅動，或安裝 Docker Desktop 並啟用 GPU。
2. 執行 `docker run --gpus all -p 8000:8000 vllm/vllm-openai --model Qwen/Qwen3-VL-8B-Instruct`。
3. 在 app 的「模型 → 外部 OpenAI 相容 endpoint」填入 `http://127.0.0.1:8000/v1`。

macOS 請改用 `mlx` pack。
