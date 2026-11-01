# M0-S7 — tool-calling（pending-hardware）

```bash
llama-server -m Qwen3-VL-8B-Instruct-Q4_K_M.gguf --jinja --port 8080 -c 16384 -ngl 99
uv run python spikes/m0/S7/run.py --runs 20
uv run python spikes/m0/S7/run.py --runs 100 --id M0-S7-ext
# 另以 Qwen3.6-35B-A3B 重跑一次（--id M0-S7-35b）
```
通過：20 次中 ≥ 19 次可解析。不通過：v0.3 只開快速路徑（ASK-LOOP-003）。
