"""packages/i18n/checks.{zh-TW,en}.json: titles of the 37 doctor checks (06 §3.3). ``--check`` verifies."""

from __future__ import annotations

import json
import sys
from pathlib import Path

OUT = Path(__file__).resolve().parents[1] / "packages" / "i18n"
T = {
    "os.platform": ("作業系統與平台", "Operating system and platform"),
    "os.arch_support": ("CPU 架構支援", "CPU architecture support"),
    "gpu.enumerate": ("顯示卡偵測", "GPU detection"),
    "gpu.vram": ("顯示記憶體", "GPU memory"),
    "gpu.driver": ("顯示卡驅動版本", "GPU driver version"),
    "gpu.cuda_runtime": ("CUDA 執行套件相容性", "CUDA runtime compatibility"),
    "gpu.blackwell_torch": ("PyTorch 與新世代 GPU", "PyTorch and new-generation GPUs"),
    "gpu.rocm": ("AMD ROCm 權限", "AMD ROCm permissions"),
    "gpu.metal": ("Apple Metal", "Apple Metal"),
    "gpu.docker_passthrough": ("容器內的 GPU", "GPU inside the container"),
    "wsl.gpu": ("WSL2 中的 GPU", "GPU in WSL2"),
    "sys.ram": ("系統記憶體", "System memory"),
    "sys.power": ("電源狀態", "Power state"),
    "disk.models": ("模型目錄空間", "Model folder space"),
    "disk.thumbs": ("縮圖目錄空間", "Thumbnail folder space"),
    "disk.db": ("資料庫磁碟空間", "Database disk space"),
    "runtime.packs": ("執行套件自我測試", "Runtime pack self-test"),
    "runtime.vllm": ("vLLM 執行環境", "vLLM runtime"),
    "runtime.llamacpp": ("llama.cpp 執行環境", "llama.cpp runtime"),
    "runtime.ort_ep": ("ONNX Runtime 加速", "ONNX Runtime acceleration"),
    "runtime.ollama": ("Ollama 連線", "Ollama connection"),
    "decode.heic": ("HEIC 解碼", "HEIC decoding"),
    "decode.raw": ("RAW 解碼", "RAW decoding"),
    "tool.ffmpeg": ("影片縮圖工具（ffmpeg）", "Video thumbnail tool (ffmpeg)"),
    "tool.exiftool": ("EXIF 寫入工具（exiftool）", "EXIF writing tool (exiftool)"),
    "fs.roots": ("允許的資料夾", "Allowed folders"),
    "fs.long_paths": ("Windows 長路徑", "Windows long paths"),
    "net.hf": ("連線到 Hugging Face", "Connection to Hugging Face"),
    "net.hf_token": ("Hugging Face 登入 token", "Hugging Face token"),
    "net.ports": ("連接埠", "Ports"),
    "worker.link": ("本機引擎連線", "Local engine connection"),
    "worker.version": ("本機引擎版本", "Local engine version"),
    "time.skew": ("系統時間", "System clock"),
    "client.notifications": ("系統通知權限", "Notification permission"),
    "db.health": ("資料庫狀態", "Database health"),
    "search.index": ("搜尋索引", "Search index"),
    "mcp.server": ("MCP 伺服器", "MCP server"),
}


def main() -> int:
    bad = []
    for i, lang in enumerate(("zh-TW", "en")):
        data = {k: {"title": v[i]} for k, v in T.items()}
        text = json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
        p = OUT / f"checks.{lang}.json"
        if "--check" in sys.argv:
            if p.read_text(encoding="utf-8") != text:
                bad.append(str(p))
        else:
            p.write_text(text, encoding="utf-8")
    if bad:
        print("out of date:", *bad)
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
