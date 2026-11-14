# 03 — 推論層（Inference Layer）規格

> 狀態：Draft v0.1（2026-11）｜負責：ML / 推論層架構
> 範圍：Model Registry、Adapter 介面、Caption / Tagging / Classification 策略、資源管理、效能、品質評估、模型下載。
> 不在範圍：佇列、DB schema、REST API（見其他文件）。本文件只定義**推論層對 worker 暴露的介面**（`InferenceService`）。
> **（審查補充）桌面優先後的主軸**：預設推論 runtime 為 **llama.cpp（GGUF）+ ONNX Runtime**，三平台都能跑；vLLM 降為 **Linux／Docker 的進階批次選項**（v0.4 pack），transformers／torch 改為選用 pack。各 OS 的預設組合以 [08 §3.3](08-desktop-packaging-and-final-review.md) 為準，本文 §1.3 的 vLLM FP8 組合只代表「Linux 進階批次」。路徑 `~/.opagent/...` 只是簡寫：實際位置依 08 §1.5 的各 OS 慣例（`OPAGENT_HOME` 可整包覆寫）。完整整合清單見 §10。
> 標示慣例：**【估算】**＝依模型參數量 / 量化位元推算或社群經驗值，未在參考硬體實測；**【未驗證】**＝規格細節需在實作時對照上游文件確認。

---

## 0. 總覽

```
            ┌───────────────────────── Worker（佇列消費者，由其他文件定義）──────────────────────────┐
            │  job = {task: caption|tag|classify|embed, images[], task_config, model_pref}            │
            └───────────────────────────────┬────────────────────────────────────────────────────────┘
                                            │  InferenceService.run(TaskRequest) -> TaskResult (async)
┌───────────────────────────────────────────▼────────────────────────────────────────────────────────┐
│ InferenceService（推論層門面）                                                                       │
│  ├─ TaskPlanner：依 task_config 決定 pipeline（單段 VLM / embedding / cascade）                     │
│  ├─ PromptBuilder：模板 + 語言 + 風格 + 類別定義 → messages + JSON schema                           │
│  ├─ PostProcessor：JSON 修復、tag 正規化、同義詞合併、信心校準、「其他/不確定」判定                 │
│  ├─ ResourceManager：VRAM 預算、LRU、熱切換、CPU fallback                                           │
│  ├─ EmbeddingCache：image sha256 × model_id → 向量（磁碟 + 記憶體）                                 │
│  └─ ModelRegistry：讀取 models/*.yaml（內建 + 使用者自訂）                                           │
└──────┬───────────────┬──────────────────┬──────────────────┬──────────────────┬────────────────────┘
       │ Adapter       │ Adapter          │ Adapter          │ Adapter          │ Adapter
   vLLM(本機子行程)  llama.cpp server   Ollama            transformers      OpenAI-compatible(外部)
                     (llama-server)                        (in-proc，含 tagger / embedding / Florence-2)
```

設計原則：

1. **所有 VLM 盡量走「OpenAI-compatible HTTP」**：vLLM、llama-server、Ollama、LM Studio 都提供 `/v1/chat/completions`，因此共用一個 `OpenAICompatChatAdapter` 核心；差異只在「如何啟動/停止 server」與「structured output 參數方言」。
2. **小模型（embedding、tagger、Florence-2）走 in-process transformers / ONNX Runtime**：延遲低、可長駐。
3. **任務邏輯與 runtime 解耦**：任務策略（prompt、schema、後處理）寫在 Task 層，Adapter 只負責「圖 + messages (+ schema) → 文字/JSON/logprobs」或「圖/文 → 向量」或「圖 → tag 機率」。
4. **每筆結果都保留 raw output 與 provenance**（model_id、revision、量化、prompt 版本 hash），以便重跑與比較。

---

## 1. Model Registry

### 1.1 描述檔格式

每個模型一個 YAML（`models/builtin/*.yaml`，使用者自訂放 `~/.opagent/models/*.yaml`，同 id 時使用者覆蓋內建）。一個「模型」可有多個 `variants`（量化 / runtime 組合），ResourceManager 依 VRAM 自動挑選。

```yaml
# models/builtin/qwen3-vl-8b-instruct.yaml
schema_version: 1
id: qwen3-vl-8b-instruct
display_name: "Qwen3-VL 8B Instruct"
family: qwen3-vl            # 決定 PromptBuilder 的方言 / 影像前處理（min/max pixels）
kind: vlm                   # vlm | embedding | tagger | captioner
capabilities: [caption, tag, classify]   # 另有 embed（embedding 類）、ocr、grounding
languages: [en, zh-Hant, zh-Hans, ja]
license:
  spdx: Apache-2.0
  commercial_use: true
  requires_acceptance: false   # true 時下載前顯示授權並要求勾選（如 Gemma / Llama 系）
  url: https://huggingface.co/Qwen/Qwen3-VL-8B-Instruct
source:
  hf_repo: Qwen/Qwen3-VL-8B-Instruct
  revision: main               # 釘選時填 commit sha
structured_output: json_schema # none | json_object | json_schema | grammar(GBNF)
supports_logprobs: true
max_images_per_request: 4      # 本系統用途建議 1；batch 靠並發
context_length: 32768          # 實際啟動時設定；本用途 8k 足夠
image:
  min_pixels: 200704           # 256*28*28 → 視 family patch 大小換算，見 §5.2
  max_pixels: 1003520          # ≈1MP，標註用途足夠
  default_pixels: 602112       # 預設上限（≈0.6MP）兼顧速度
variants:
  - id: fp8
    runtime: vllm
    hf_repo: Qwen/Qwen3-VL-8B-Instruct-FP8
    quant: fp8
    vram_gb: { weights: 9.5, runtime_overhead: 1.5, kv_per_1k_tokens: 0.14 }   # 【估算】
    recommended_batch: 16        # vLLM 並發請求數（max_num_seqs）
    launch:
      args: ["--max-model-len", "8192", "--gpu-memory-utilization", "0.80",
             "--limit-mm-per-prompt", '{"image":1}', "--max-num-seqs", "16"]
    min_compute_capability: "8.9"   # FP8 kernel；sm_120 需 vLLM ≥ 支援 Blackwell 的版本【未驗證】
  - id: q4_k_m
    runtime: llama.cpp
    gguf: { repo: Qwen/Qwen3-VL-8B-Instruct-GGUF, file: "*Q4_K_M.gguf", mmproj: "mmproj-*F16.gguf" }  # 檔名【未驗證】
    quant: Q4_K_M
    vram_gb: { weights: 5.0, mmproj: 1.2, runtime_overhead: 0.8 }               # 【估算】
    recommended_batch: 4         # llama-server --parallel
  - id: ollama
    runtime: ollama
    ollama_tag: "qwen3-vl:8b"    # 【未驗證】
    vram_gb: { weights: 6.5 }    # 【估算】
    recommended_batch: 2
defaults:
  temperature: 0.2
  max_tokens: { caption_short: 96, caption_long: 512, tag: 256, classify: 128 }
  prompt_profile: qwen-default    # 指向 prompts/ 下的模板組
tiers: [8gb, 16gb, 24gb]          # 出現在哪些硬體檔的推薦清單
notes: "16GB 批次主力。FP8 需 vLLM；GGUF 可用 llama.cpp/Ollama。"
```

Embedding / tagger 範例：

```yaml
# models/builtin/siglip2-so400m.yaml
schema_version: 1
id: siglip2-so400m-384
family: siglip2
kind: embedding
capabilities: [embed, classify]      # classify = zero-shot / prototype
license: { spdx: Apache-2.0, commercial_use: true }
source: { hf_repo: google/siglip2-so400m-patch14-384 }
embedding:
  dim: 1152
  normalize: true
  similarity: sigmoid               # SigLIP 用 sigmoid(logit_scale*cos + bias)；CLIP 類用 softmax
  text_template: "This is a photo of {label}."   # SigLIP2 官方建議的模板風格
  multilingual: true                # SigLIP2 有多語訓練，中文 label 可用但英文較穩【未驗證程度】
variants:
  - { id: fp16, runtime: transformers, quant: fp16, vram_gb: { weights: 1.0, runtime_overhead: 0.5 }, recommended_batch: 64 }  # 【估算】
  - { id: onnx-cpu, runtime: onnx, quant: fp32, device: cpu, recommended_batch: 8 }
tiers: [8gb, 16gb, 24gb]
---
# models/builtin/wd-eva02-large-v3.yaml
schema_version: 1
id: wd-eva02-large-tagger-v3
family: wd-tagger
kind: tagger
capabilities: [tag]
license: { spdx: Apache-2.0, commercial_use: true }
source: { hf_repo: SmilingWolf/wd-eva02-large-tagger-v3 }
tagger:
  vocabulary_file: selected_tags.csv     # Danbooru 風格 tag + category(general/character/rating)
  default_thresholds: { general: 0.35, character: 0.85 }
  domain: anime                           # UI 提示：寫實照片效果差
variants:
  - { id: onnx-gpu, runtime: onnx, vram_gb: { weights: 1.3 }, recommended_batch: 32 }   # 【估算】
  - { id: onnx-cpu, runtime: onnx, device: cpu, recommended_batch: 4 }
```

外部 endpoint（使用者自行提供）：

```yaml
# ~/.opagent/models/my-lmstudio.yaml
schema_version: 1
id: my-lmstudio-gemma
family: gemma4
kind: vlm
capabilities: [caption, tag, classify]
variants:
  - id: remote
    runtime: openai-compatible
    endpoint:
      base_url: http://192.168.1.20:1234/v1
      model: "gemma-4-26b-a4b"
      api_key_env: OPAGENT_LMSTUDIO_KEY       # 只存環境變數名，不存明文
      structured_output: json_schema       # 由「探測」自動填入，可手動覆寫
      timeout_s: 120
      max_concurrency: 2
    vram_gb: { weights: 0 }               # 不佔本機 VRAM
privacy: { sends_images_offsite: false }  # 雲端 API 設 true → UI 顯示警告並需任務層級同意
```

### 1.2 欄位說明（摘要）

| 欄位 | 必填 | 說明 |
|---|---|---|
| `id` | ✓ | 全域唯一、slug；結果 provenance 會記錄 `id@variant@revision` |
| `family` | ✓ | 決定 prompt 方言、影像前處理、structured output 細節 |
| `kind` | ✓ | `vlm` / `embedding` / `tagger` / `captioner`（Florence-2、JoyCaption 這類專用模型） |
| `capabilities` | ✓ | `caption` `tag` `classify` `embed`（+ 可選 `ocr` `grounding`） |
| `variants[].runtime` | ✓ | `vllm` `llama.cpp` `ollama` `transformers` `onnx` `openai-compatible` |
| `variants[].quant` | | `bf16` `fp8` `awq` `gptq` `Q4_K_M` `Q5_K_M` `Q8_0`… |
| `variants[].vram_gb` | ✓（本機） | `weights` + `mmproj` + `runtime_overhead` + `kv_per_1k_tokens`；用於預算檢查 |
| `variants[].cpu_moe_layers` | | llama.cpp MoE 專用：預設 `--n-cpu-moe N`，配合 RAM 需求 `ram_gb` |
| `structured_output` | ✓ | `none`/`json_object`/`json_schema`/`grammar`；決定 classify 是否可走 enum |
| `supports_logprobs` | | 決定能否用 token logprob 算信心 |
| `license.requires_acceptance` | | 下載前強制顯示授權（Gemma、Llama 衍生如 JoyCaption） |
| `recommended_batch` | | adapter 預設並發 / batch size |
| `tiers` | | 推薦清單的硬體檔 |

Registry 啟動時以 JSON Schema（pydantic model）驗證，錯誤的 YAML 不載入並在 UI「模型」頁顯示原因。

### 1.3 內建模型目錄

VRAM 皆為**【估算】**（權重 + mmproj + 約 1–2GB runtime/KV，以 8k context、單圖 ≤1MP 計）。

**A. 16GB（參考硬體 RTX 5070 Ti，預設檔）**

| 角色 | 模型 / variant | Runtime | VRAM | 用途 / 備註 |
|---|---|---|---|---|
| **預設 VLM**（審查補充：依 08 C-02 改為預設） | Qwen3-VL-8B Q5_K_M / Q4_K_M + mmproj | llama.cpp | ~6.5–8 | 三平台桌面與 Docker 的預設；caption+tag+classify 一次 JSON；GBNF/JSON schema |
| 進階批次（Linux／Docker，v0.4 vLLM pack） | Qwen3-VL-8B-Instruct FP8 | vLLM | ~11–12 | structured output、logprobs、高吞吐；Windows／macOS 不提供 |
| 高品質（慢） | Qwen3.6-35B-A3B Q4 + mmproj | llama.cpp（`--n-cpu-moe`） | GPU ~12–14 + RAM ~12 | 難例複核、cascade 第二段；需 ≥32GB 系統 RAM |
| 替代 | Gemma 4 26B-A4B Q4 | llama.cpp | ~15（很緊） | 建議只跑 VLM 不並載 embedding；需授權同意 |
| 替代 | Qwen3.5-9B（原生多模態） | vLLM / llama.cpp | ~10–12（FP8）| 多語 caption 備選 |
| 替代 | InternVL3.5-8B | vLLM / transformers | ~10–12 | 細節辨識備選 |
| 詳細 caption | JoyCaption Beta One（Q8/NF4） | transformers / vLLM | ~9–10（NF4 ~6）| 長描述、訓練資料 caption；英文為主；Llama 3.1 授權 |
| **預設 embedding** | SigLIP2 so400m-patch14-384 | **ONNX Runtime**（審查補充，08 C-12；transformers 為 torch pack 選項） | ~1.5 | zero-shot、prototype、去重、以文搜圖 |
| 快速 embedding | PE-Core B/L | transformers | ~0.6–1.5 | 超大量圖庫粗篩 |
| 快速 caption | Florence-2-large（0.77B） | transformers | ~2 | 極快短 caption、OCR；英文 |
| 動漫 tagger | WD EVA02-Large v3 / Camie | ONNX | ~1.5 | 閾值式 tag |
| 通用 tagger | JoyTagger | transformers | ~1.5 | 閾值式 tag（偏 booru 詞彙）|

**B. 8GB（入門檔，例：RTX 4060 / 3070 / 筆電）**

| 角色 | 模型 | Runtime | VRAM |
|---|---|---|---|
| 預設 VLM | Qwen3-VL-8B Q4_K_M（或 Qwen3-VL-4B）| llama.cpp / Ollama | ~6–6.5（4B ~3.5）|
| 輕量 VLM | Gemma 4 E4B（Q4/Q8）| llama.cpp / Ollama | ~4–6 |
| 超輕量 | Gemma 4 E2B | llama.cpp / Ollama | ~2.5–3.5 |
| 替代 | MiniCPM-V（4.x，Q4）| llama.cpp / Ollama | ~5–6 |
| embedding | SigLIP2 so400m（fp16）或 PE-Core B | transformers | ~1–1.5 |
| caption | Florence-2-base/large | transformers | ~1–2 |
| tagger | WD v3 / JoyTagger | ONNX | ~1–1.5 |

8GB 預設組合：Qwen3-VL-8B Q4（~6.5）+ SigLIP2（~1.5）**會頂到上限**，ResourceManager 預設改為「SigLIP2 先跑完全部 embedding → 卸載 → 再載 VLM」的序列模式（§4.3）。

**C. 24GB+（RTX 3090/4090/5090、A6000…）**

| 角色 | 模型 | Runtime | VRAM |
|---|---|---|---|
| 預設 VLM | Qwen3.6-35B-A3B Q4/Q5（全 GPU）| llama.cpp | ~22–24（5090 32GB 較舒適）|
| 批次主力 | Qwen3-VL-8B BF16/FP8 | vLLM | 17–20 / 11–12 |
| 替代 | Gemma 4 26B-A4B Q4–Q6 | llama.cpp / vLLM(AWQ) | ~16–20 |
| 替代 | InternVL3.5-8B BF16、Qwen3.5-9B BF16 | vLLM | ~18–20 |
| 詳細 caption | JoyCaption Beta One BF16 | vLLM | ~17–18 |
| embedding | SigLIP2 so400m + PE-Core L（並載）| transformers | ~3 |

> 「24GB+」檔允許 VLM + embedding + tagger 三者常駐。

---

## 2. Adapter 介面

### 2.1 共用資料結構（pydantic v2）

```python
# opagent/inference/types.py
from __future__ import annotations
from enum import Enum
from typing import Any, Literal
from pydantic import BaseModel, Field

class Capability(str, Enum):
    CAPTION = "caption"; TAG = "tag"; CLASSIFY = "classify"; EMBED = "embed"

class ImageInput(BaseModel):
    image_id: str                 # 上層給的穩定 id（通常是 asset id）
    sha256: str                   # 內容 hash，embedding 快取 key
    path: str | None = None       # 本機路徑（worker 與推論同機時）
    data: bytes | None = None     # 或已解碼/縮圖後的 bytes（遠端 worker 傳輸時）
    exif: dict[str, Any] = {}     # 日期、GPS、相機；可注入 prompt 作為提示

class GenParams(BaseModel):
    temperature: float = 0.2
    top_p: float = 0.9
    max_tokens: int = 512
    seed: int | None = None
    logprobs: bool = False
    top_logprobs: int = 0
    n: int = 1                    # self-consistency 用
    max_pixels: int | None = None # 覆寫 registry 預設
    stop: list[str] = []

class ChatRequest(BaseModel):
    image: ImageInput
    system: str | None = None
    user: str                     # 已渲染的 prompt（含類別清單等）
    json_schema: dict | None = None   # 有則要求 structured output
    params: GenParams = GenParams()

class TokenLogprob(BaseModel):
    token: str; logprob: float
    top: list[tuple[str, float]] = []

class ChatResult(BaseModel):
    text: str                                 # raw output
    parsed: dict | None = None                # JSON 解析成功時
    parse_error: str | None = None
    logprobs: list[TokenLogprob] | None = None
    choices: list[str] = []                   # n>1 時其餘樣本
    usage: dict[str, int] = {}                # prompt_tokens / completion_tokens / image_tokens
    latency_ms: float = 0
    finish_reason: str | None = None

class TagScore(BaseModel):
    tag: str; score: float; category: str | None = None

class EmbedResult(BaseModel):
    image_id: str; vector: list[float]; model_id: str; cached: bool = False

class Provenance(BaseModel):
    model_id: str; variant: str; revision: str | None
    runtime: str; quant: str | None
    prompt_id: str | None = None; prompt_hash: str | None = None
    params: dict = {}
```

### 2.2 Adapter Protocol

```python
# opagent/inference/adapter.py
from typing import Protocol, AsyncIterator, Sequence, runtime_checkable

class LoadState(str, Enum):
    UNLOADED="unloaded"; LOADING="loading"; READY="ready"; UNLOADING="unloading"; ERROR="error"

@runtime_checkable
class ModelAdapter(Protocol):
    spec: "ModelSpec"            # registry 解析後的 model + variant
    state: LoadState

    # ---- 生命週期 ----
    async def load(self, device: "DeviceAlloc") -> None: ...
    async def unload(self) -> None: ...
    async def health(self) -> bool: ...
    def vram_estimate_gb(self, concurrency: int | None = None) -> float: ...
    def capabilities(self) -> set[Capability]: ...

class ChatAdapter(ModelAdapter, Protocol):
    """VLM：caption / tag / classify 都用這個，差異在 prompt+schema。"""
    async def chat(self, req: ChatRequest) -> ChatResult: ...
    async def chat_batch(self, reqs: Sequence[ChatRequest]) -> list[ChatResult]: ...
    def chat_stream(self, req: ChatRequest) -> AsyncIterator[str]: ...   # UI 預覽單張用
    def structured_output_mode(self) -> Literal["none","json_object","json_schema","grammar"]: ...

class EmbedAdapter(ModelAdapter, Protocol):
    async def embed_images(self, images: Sequence[ImageInput]) -> list[list[float]]: ...
    async def embed_texts(self, texts: Sequence[str]) -> list[list[float]]: ...
    def similarity(self, img: "np.ndarray", txt: "np.ndarray") -> "np.ndarray": ...  # 處理 sigmoid/softmax 差異

class TaggerAdapter(ModelAdapter, Protocol):
    async def tag_batch(self, images: Sequence[ImageInput]) -> list[list[TagScore]]: ...  # 回傳全部 >0.05 的分數
    def vocabulary(self) -> list[str]: ...

class CaptionerAdapter(ModelAdapter, Protocol):
    """Florence-2 / JoyCaption 等「專用」模型，prompt 受限於模型自己的 task token。"""
    async def caption_batch(self, images: Sequence[ImageInput], mode: str) -> list[ChatResult]: ...
```

### 2.3 對 worker 暴露的唯一門面：`InferenceService`

Worker 不直接碰 adapter。

```python
# opagent/inference/service.py
class TaskRequest(BaseModel):
    task: Capability
    images: list[ImageInput]                 # 一個 job 的一批（建議 16–256 張）
    config: "CaptionConfig | TagConfig | ClassifyConfig | EmbedConfig"
    model_pref: list[str] = []               # 使用者選的 model_id（依序 fallback）；空 = tier 預設
    priority: Literal["interactive","batch"] = "batch"

class ItemResult(BaseModel):
    image_id: str
    status: Literal["ok","failed","skipped"]
    caption: "CaptionOut | None" = None
    tags: list["TagOut"] | None = None
    classification: "ClassifyOut | None" = None
    embedding_ref: str | None = None         # 向量存快取，不塞進結果
    raw: list[ChatResult] = []               # 所有階段的 raw output（可設定是否保存）
    provenance: list[Provenance] = []
    error: str | None = None
    timings_ms: dict[str, float] = {}

class InferenceService:
    async def run(self, req: TaskRequest,
                  on_item: Callable[[ItemResult], Awaitable[None]] | None = None
                  ) -> list[ItemResult]: ...      # on_item：逐張回報進度，worker 寫 DB
    async def preview(self, req: TaskRequest) -> AsyncIterator[dict]: ...  # 單張串流（UI 試跑 prompt）
    async def list_models(self) -> list["ModelInfo"]: ...                 # 含可用性、是否已下載、VRAM 是否夠
    async def ensure_ready(self, model_ids: list[str]) -> "ReadyReport": ...  # job 開始前預熱
    async def resource_status(self) -> "ResourceStatus": ...               # 已載入模型、VRAM 使用
    async def benchmark(self, spec: "BenchmarkSpec") -> AsyncIterator["BenchmarkProgress"]: ...
    async def cancel(self, job_token: str) -> None: ...
```

錯誤語意（worker 據此決定重試）：

| 例外 | 意義 | worker 建議行為 |
|---|---|---|
| `ModelUnavailable` | 未下載 / 授權未接受 / endpoint 不可達 | job 失敗，提示使用者 |
| `InsufficientVRAM` | 預算不足且無可降級 variant | 改排序列模式或失敗 |
| `TransientInferenceError` | OOM 後已自動重啟、timeout、5xx | 單張重試（指數退避，≤3 次） |
| `OutputParseError` | JSON 修復後仍失敗 | 已內部重試 1 次；標記 `failed`，保存 raw |

### 2.4 各 runtime adapter 實作要點

| Runtime | 啟動方式 | Structured output | Logprobs | 批次 | 要點 |
|---|---|---|---|---|---|
| **vLLM** | 子行程 `vllm serve`（或 Docker），推論層管理 port 與生命週期 | `response_format: {type: json_schema}`（後端 xgrammar/guidance）；或 `extra_body.guided_choice` 做 enum【參數名依版本，未驗證】 | ✓ `logprobs/top_logprobs` | continuous batching：並發送 N 個請求即可 | 啟動慢（30–90s【估算】）→ 載入後盡量長駐；`--gpu-memory-utilization` 由 ResourceManager 依預算計算；`--limit-mm-per-prompt image=1`；`mm_processor_kwargs` 傳 max_pixels；需 Blackwell 版 wheel（§4.5） |
| **llama.cpp** | 子行程 `llama-server -m x.gguf --mmproj y.gguf -ngl 99 --parallel N -c (N*ctx)` | `response_format` json_schema（內部轉 GBNF）或直接 `grammar` | ✓（`n_probs` / OpenAI 相容 logprobs，版本差異【未驗證】） | `--parallel` slot | MoE 用 `--n-cpu-moe K` 把 expert 放 CPU；`--ctx-size` 是所有 slot 總和；`--flash-attn`；`-ub/-b` 影響影像編碼尖峰 VRAM |
| **Ollama** | 連線既有 daemon（不由我們啟停）；`/api/chat` 或 `/v1` | `format: <json schema>`（原生 API） | 新版支援，舊版無 → 降級 | `OLLAMA_NUM_PARALLEL` | 載入/卸載透過 `keep_alive: 0`；VRAM 由 Ollama 自管 → ResourceManager 視為「外部預留」並讀 `/api/ps` |
| **transformers** | in-process（獨立 GPU worker 行程，避免主行程 CUDA 汙染） | 用 `outlines`/`xgrammar` logits processor（可選）；否則 prompt + JSON 修復 | ✓（直接取 logits） | 手動 padding batch | embedding / tagger / Florence-2 / JoyCaption 的主路徑；`torch.compile` 可選；`attn_implementation="sdpa"` |
| **onnx** | in-process onnxruntime-gpu / CPU | n/a | n/a | 靜態 batch | WD tagger、CPU fallback embedding |
| **openai-compatible（外部）** | 不啟停；僅 health 探測 | 啟用時「能力探測」：送小 schema 測 `json_schema` → `json_object` → `none` | 探測 | `max_concurrency` 限流 | 使用者自己的 Ollama/vLLM/LM Studio 或雲端 API；圖片以 base64 data URL 傳，先在本地縮圖到 max_pixels；`sends_images_offsite` 警告；token / 費用計數 |

共用實作：`OpenAICompatChatAdapter` 處理 messages 組裝（`[{type:image_url, image_url:{url:"data:image/jpeg;base64,..."}}, {type:text}]`）、重試、JSON 修復（`json-repair` → 失敗再以 `temperature=0` 重問一次「只輸出 JSON」）；vLLM / llama.cpp / Ollama / External 只覆寫 `_launch()`、`_schema_kwargs()`、`_logprob_kwargs()`。

能力探測結果寫回 `~/.opagent/cache/probe/<model_id>.json`，避免每次啟動重測。

---

## 3. 三種任務的實作策略

### 3.0 共同：一次推論多任務（Fused）

若同一 job 同時要 caption + tag + classify，且選的是 VLM，預設**合併成一次呼叫**輸出 `{category, caption, tags}`（省 2/3 影像編碼成本）。使用者可在進階設定關閉（各任務獨立 prompt 品質略好）。

```json
{
  "$id": "opagent.fused.v1",
  "type": "object",
  "properties": {
    "category": { "type": "string", "enum": ["<由類別定義產生>", "other"] },
    "confidence": { "type": "string", "enum": ["high", "medium", "low"] },
    "caption": { "type": "string", "maxLength": 400 },
    "tags": { "type": "array", "items": { "type": "string", "maxLength": 40 }, "maxItems": 20 }
  },
  "required": ["category", "caption", "tags"],
  "additionalProperties": false
}
```

> JSON key 順序很重要：把 `caption` 放在 `category` **之前**可讓模型「先描述再分類」（類 CoT），通常略提升分類準確，但 category 的 logprob 會受 caption 影響。預設採「caption → tags → category」順序【建議於 benchmark 驗證】。

### 3.1 Caption

**設定（CaptionConfig）**

```python
class CaptionConfig(BaseModel):
    length: Literal["short","medium","long"] = "short"   # ≈ 1 句 / 2–3 句 / 一段（≤150 字）
    languages: list[Literal["zh-Hant","en"]] = ["zh-Hant"]  # 多選 → 一次輸出多語欄位
    style: str = "neutral"          # 模板 id：neutral / alt_text / social / archival / training_data / custom
    custom_instructions: str | None = None
    use_exif_hints: bool = True     # 把拍攝日期/地點（反查城市）放入 prompt
    forbid: list[str] = ["推測人物身分", "主觀評價"]
```

**策略**

| 情境 | 預設模型 | 作法 |
|---|---|---|
| 短 caption、大量 | Qwen3-VL-8B（vLLM） | 一次輸出 `{caption_zh, caption_en}`；max_tokens 96–160 |
| 極速英文短句 | Florence-2-large | `<CAPTION>` / `<DETAILED_CAPTION>`；中文需求時再丟 VLM 翻譯（不建議，另加成本）|
| 長詳細描述 | JoyCaption Beta One / Qwen3.6-35B-A3B | JoyCaption 英文最佳；需繁中時改用 Qwen 系直接生成，不做翻譯 |
| 多語 | Qwen 系 | 一次呼叫產生兩語，避免兩次影像編碼；繁中要求「台灣用語、繁體字」並在後處理用 OpenCC `s2twp` 保險轉換 |

**風格模板**（`prompts/caption/*.yaml`，Jinja2）

```yaml
id: caption.neutral.v1
system: |
  你是專業的照片描述員。只描述畫面中看得見的內容，不臆測人物身分、不加入主觀評價。
user: |
  請描述這張照片。
  {% if length == "short" %}用一句話（中文 15–30 字）。{% elif length == "medium" %}用 2–3 句。{% else %}用一段完整描述（120–200 字），依序涵蓋：主體、動作、場景、光線與色調、構圖。{% endif %}
  {% if exif.date %}拍攝時間：{{ exif.date }}。{% endif %}{% if exif.place %}拍攝地點附近：{{ exif.place }}（僅在畫面相符時使用）。{% endif %}
  {% if "zh-Hant" in languages %}caption_zh 使用台灣繁體中文。{% endif %}{% if "en" in languages %}caption_en 使用自然英文。{% endif %}
  {{ custom_instructions or "" }}
  只輸出 JSON。
```

其他 style：`alt_text`（無障礙，≤125 字元，先講重點）、`social`（社群貼文語氣，可帶 1–2 個 hashtag）、`archival`（檔案管理：人事時地物欄位化）、`training_data`（擴散模型訓練用，英文、逗號分隔、從主體到背景）。

**Caption JSON schema**

```json
{
  "$id": "opagent.caption.v1",
  "type": "object",
  "properties": {
    "caption_zh": { "type": "string" },
    "caption_en": { "type": "string" }
  },
  "required": ["caption_zh"],
  "additionalProperties": false
}
```
（`required` 依 `languages` 動態產生。）

**輸出**：`CaptionOut{ text_by_lang: {zh-Hant, en}, style, length, model }`。Caption 本身無可靠信心分數；若需要，以平均 token logprob 作「流暢度」指標僅供排序，不作品質保證。

### 3.2 Tagging

**設定（TagConfig）**

```python
class TagConfig(BaseModel):
    mode: Literal["open","controlled","hybrid","threshold"] = "hybrid"
    vocabulary_id: str | None = None       # 受控詞彙表（使用者建立或內建「一般照片 300 詞」）
    max_tags: int = 15
    min_tags: int = 3
    language: Literal["zh-Hant","en","both"] = "zh-Hant"
    thresholds: dict[str, float] | None = None   # threshold 模式（WD/JoyTagger）
    allow_new_tags: bool = True            # hybrid：可提新詞，進「待審詞」區
    tag_groups: list[str] | None = None    # 例：["場景","物件","活動","色彩","氛圍"]，要求每組至少 1
```

**四種模式**

| 模式 | 作法 | 優點 | 缺點 | 預設模型 |
|---|---|---|---|---|
| `open` 開放式 | VLM 自由產生 tag | 豐富、可發現新概念 | 詞彙發散（「狗」「小狗」「犬」）| Qwen3-VL-8B |
| `controlled` 受控 | schema `items.enum = vocabulary`（≤~500 詞可行，更大用 embedding 預篩）| 一致、可做 facet | 錯過詞外概念 | Qwen3-VL-8B + structured output |
| `hybrid`（**預設**） | 受控優先 + 允許 `new_tags`（另欄位，上限 3）；新詞經正規化後若與詞彙相似度 >0.85 自動映射，否則進待審 | 兼顧一致與擴充 | 稍複雜 | 同上 |
| `threshold` 閾值式 | WD v3 / Camie / JoyTagger 輸出每個 tag 的 sigmoid 機率，> 閾值即採用 | 極快、有真機率、可調 precision/recall | 詞彙固定（booru），寫實照片弱 | WD EVA02 v3 |

**大詞彙表（>500）的受控 tag**：先用 SigLIP2 計算圖片 vs 每個詞的文字 embedding（`"a photo of {tag}"`）相似度，取 top-60 候選 → 再放進 VLM 的 enum 讓它挑 ≤max_tags。這同時降低 prompt token。

**Tag 正規化管線（PostProcessor）**

```
raw tags
 → trim / 小寫（英文）/ 全形半形統一 / OpenCC s2twp（中文）
 → 去標點、去冠詞（a/the）、英文詞形還原（lemmatize：dogs→dog）
 → 同義詞表映射（vocabulary.synonyms：{"小狗":"狗","犬":"狗","puppy":"dog"}）
 → 跨語對照（vocabulary 每個 term 有 {zh, en}；language=both 時輸出兩語）
 → embedding 去重：同張圖 tag 間 text-embedding 相似度 >0.9 合併（保留詞彙中的 canonical）
 → 黑名單（photo、image、picture、照片、圖片 等無資訊詞）
 → 排序（VLM 輸出順序 or threshold 分數）→ 截斷 max_tags
```

受控詞彙表格式：

```yaml
# vocabularies/general-photo.zh-Hant.yaml
id: general-photo-v1
terms:
  - { id: dog, zh: 狗, en: dog, group: 物件, synonyms: [小狗, 犬, 狗狗, puppy, doggy] }
  - { id: beach, zh: 海灘, en: beach, group: 場景, synonyms: [沙灘, 海邊, seaside] }
  - { id: sunset, zh: 夕陽, en: sunset, group: 氛圍, synonyms: [日落, 黃昏, dusk] }
blacklist: [照片, 圖片, photo, image]
```

**Tagging prompt（hybrid）**

```text
[system] 你是照片標註員，為圖庫產生可搜尋的標籤。
[user]
請為這張照片挑選 {{min_tags}}–{{max_tags}} 個標籤。
規則：
1. 優先從「候選詞彙」中挑選，只挑畫面中明確可見或明顯成立的。
2. 若有重要概念不在詞彙中，可放入 new_tags（最多 3 個，繁體中文名詞，2–6 字）。
3. 依重要性排序；不要輸出「照片」「圖片」等泛詞。
{% if tag_groups %}4. 每個分組至少一個：{{ tag_groups | join("、") }}。{% endif %}
候選詞彙：{{ candidates | join("、") }}
只輸出 JSON。
```

```json
{
  "$id": "opagent.tags.hybrid.v1",
  "type": "object",
  "properties": {
    "tags": { "type": "array", "items": { "type": "string", "enum": ["<candidates>"] },
              "minItems": 3, "maxItems": 15, "uniqueItems": true },
    "new_tags": { "type": "array", "items": { "type": "string", "maxLength": 12 }, "maxItems": 3 }
  },
  "required": ["tags"],
  "additionalProperties": false
}
```

> 部分 grammar 後端對 `uniqueItems`/`minItems` 支援不完整【未驗證】→ 後處理仍做去重與數量檢查。

**Tag 信心**：VLM 模式以「該 tag 首 token 的 logprob」近似（僅在 enum 模式有意義）；或對 hybrid 結果再以 SigLIP2 圖文相似度作第二訊號，`score = 0.5*vlm_rank_score + 0.5*siglip_sigmoid`；threshold 模式直接用模型機率。輸出 `TagOut{tag, canonical_id, score, source: vlm|tagger|embedding, is_new}`。

### 3.3 Classification

**類別定義（使用者在 UI 建立，推論層讀取）**

```yaml
# 由 API 層存 DB，傳入推論層時為此結構
taxonomy:
  id: family-album-2026
  multi_label: false              # 單標籤（搬到一個資料夾）；true 時可多選（建 symlink / 複製）
  other_policy: { enabled: true, label: "other", min_confidence: 0.55 }   # 低於門檻 → other / 待人工
  classes:
    - id: food
      name: 美食
      description: 以食物或飲料為主體的照片，包括餐廳擺盤、自己煮的料理。不含只是背景有餐桌的合照。
      examples: [asset_123, asset_456, asset_789]     # 範例圖（建議 3–10 張）
      negative_examples: [asset_999]                  # 可選：容易混淆的反例
    - id: pets
      name: 動物
      description: 貓狗等寵物與野生動物。
      examples: [...]
    - id: documents
      name: 文件與截圖
      description: 收據、文件翻拍、螢幕截圖、白板。
  rules:                                              # 可選：EXIF 規則先於模型
    - { if: "exif.is_screenshot == true", then: documents }
```

**三種策略**

| 策略 | 流程 | 信心來源 | 速度【估算，16GB】 | 適合 |
|---|---|---|---|---|
| **E：Embedding zero-shot / prototype** | SigLIP2 影像向量 vs（類別文字向量 ⊕ 範例圖 prototype）| 相似度、top1-top2 margin | 數百張/秒 | 類別視覺上明確（美食/風景/文件）、超大量 |
| **V：VLM enum structured output** | VLM 看圖 + 類別名稱與描述 → `category ∈ enum` | category token logprob；自我一致性 | 3–8 張/秒（Qwen3-VL-8B FP8 vLLM，短輸出）| 語義型類別（「旅行 vs 日常」、「工作相關」）|
| **C：Cascade（預設）** | E 先分 → 高信心直接採用；低信心（或 margin 小）送 V 複核，V 看到全部類別與說明（ADR-0030） | 綜合 | 依送審比例（通常 20–40%）| 大多數使用者 |

**C：Cascade 詳細流程**

```
           image
             │
   ┌─────────▼──────────┐   EXIF rules 命中 → 直接決定（confidence=1, source=rule）
   │ 0. Rules            │
   └─────────┬──────────┘
   ┌─────────▼──────────┐
   │ 1. SigLIP2 embed    │──(快取)
   │   s_c = α·txt_sim + (1-α)·proto_sim   (α 依範例數：0 張→1.0，≥5 張→0.4)
   │   p = calibrate(s)  │  （溫度縮放，§3.3.3）
   └─────────┬──────────┘
      p_top1 ≥ τ_high 且 margin ≥ m ?  ──是──► 採用（source=embedding）
             │否
   ┌─────────▼──────────┐
   │ 2. VLM 複核          │  enum = 全部類別 + other（超過 16 類取分數最高 16 類）；附描述（ADR-0030）
   │   取 logprob → p_vlm │
   └─────────┬──────────┘
      p_vlm ≥ other_policy.min_confidence ? ──是──► 採用（source=vlm）
             │否
   ┌─────────▼──────────┐
   │ 3. (可選) 大模型複核  │  Qwen3.6-35B-A3B，或直接 → other/needs_review
   └────────────────────┘
```

預設門檻（可在 benchmark 後自動調整）：`τ_high=0.80`、`m=0.15`；若使用者沒有任何範例圖，`τ_high` 提高到 0.90（純文字 zero-shot 校準較差）。

#### 3.3.1 Prototype（few-shot）

```python
def build_prototypes(classes, embed_cache) -> dict[str, np.ndarray]:
    protos = {}
    for c in classes:
        vecs = np.stack([embed_cache.get(a) for a in c.examples])     # L2-normalized
        p = vecs.mean(0); protos[c.id] = p / np.linalg.norm(p)
        # 範例 ≥8 時改用 k-medoids(k=2) 多原型，取 max 相似度，處理類內多樣性（例：「旅行」含海邊與城市）
    return protos

def class_scores(img_vec, txt_vecs, protos, alpha):
    txt = img_vec @ txt_vecs.T                 # 每類可有多個描述 prompt（名稱、描述、英文翻譯）→ 取平均
    pro = np.array([img_vec @ protos[c] for c in class_ids])
    return alpha * txt + (1 - alpha) * pro
```

- 類別文字向量：`name`、`description`、自動英譯（由 VLM 一次性翻譯並快取）各做一條 prompt，取平均（prompt ensemble）。
- `negative_examples`：計算與反例的相似度，若高於正例 prototype → 降權。
- 範例圖數 ≥ 20/類 時，提供「訓練線性探針（logistic regression on embeddings）」選項，CPU 秒級完成，通常優於 prototype【經驗值】。

#### 3.3.2 VLM enum prompt

```text
[system] 你是照片分類器。只能從給定類別中選一個；若都不符合或無法判斷，選 "other"。
[user]
請將照片分到下列其中一類：
{% for c in classes %}- {{ c.id }}：{{ c.name }}。{{ c.description }}
{% endfor %}- other：以上皆不符合，或無法確定。
（ADR-0030：不再把快速模型的候選寫進 prompt，以免誤導 VLM。）
{% if exif.date %}拍攝時間：{{ exif.date }}{% endif %}
先用一句話說明判斷依據（reason），再給出 category。只輸出 JSON。
```

```json
{
  "$id": "opagent.classify.single.v1",
  "type": "object",
  "properties": {
    "reason":   { "type": "string", "maxLength": 120 },
    "category": { "type": "string", "enum": ["food", "pets", "documents", "other"] }
  },
  "required": ["reason", "category"],
  "additionalProperties": false
}
```

多標籤版本：

```json
{
  "$id": "opagent.classify.multi.v1",
  "type": "object",
  "properties": {
    "reason": { "type": "string", "maxLength": 160 },
    "categories": { "type": "array", "items": { "type": "string", "enum": ["food","pets","documents"] },
                    "maxItems": 3, "uniqueItems": true },
    "is_other": { "type": "boolean" }
  },
  "required": ["categories", "is_other"],
  "additionalProperties": false
}
```

> **enum 值用短 ASCII id**（`food` 而非「美食」）：tokenizer 切分更短、logprob 更好讀；且 id 首 token 應互不相同（Registry 建 taxonomy 時檢查，衝突則自動改 id 前綴如 `c1_food`），以便用「首 token 機率」算信心。

#### 3.3.3 信心分數

| 來源 | 計算 | 適用 |
|---|---|---|
| **logprobs**（首選） | 在 `category` 值的第一個 token 位置取 `top_logprobs`（k=10–20），把各候選 id 首 token 的機率正規化：`p_c = exp(lp_c)/Σ exp(lp_j)` | vLLM / llama.cpp / 支援 logprobs 的外部 API |
| **embedding margin** | `p = softmax(s / T)`；信心 = `p_top1`，另記 `margin = p1 - p2` | E 段 |
| **自我一致性** | 無 logprobs 時：`n=5, temperature=0.7`，信心 = 多數票比例；成本 ×5 → 只用於低信心樣本 | Ollama 舊版、雲端無 logprobs |
| **口述信心** | schema 加 `confidence: high/medium/low` | 僅作輔助，校準差，不單獨使用 |
| **一致性加成** | E 與 V 同意 → `p = 1 - (1-p_E)(1-p_V)`；不同意 → 採 V，信心 `p_V × 0.8` | Cascade |

**校準**：有 golden set（§6）時對 E 段擬合溫度 `T`、對 V 段擬合 Platt scaling，存在 taxonomy 的 `calibration` 欄位；無 golden set 時用預設 `T=0.05`（SigLIP 原始 logit 尺度）【需實測】。

**（ADR-0001）門檻分工**：`τ_high`、margin `m` 只決定**路由**（是否送 VLM，屬於 config_hash）；`task_config.thresholds.accept` 是所有有分數之判定的**最終閘門**（embedding 快速路徑也要 p ≥ accept 才 accepted）；VLM-only 以 `min_confidence` 判定；conflict 低於 accept 一律 needs_review（reason=conflict）。下文的 `τ_review` 即 `thresholds.accept`；`floor = max(review, min_confidence)`。只改 accept／review／min_confidence 時是純重算，不需推論。完整表格見 [DECISIONS.md ADR-0001](DECISIONS.md)。

**「其他 / 不確定」**：兩種不同輸出，分開處理：

- `other`：模型明確選 other，或所有類別 `p < other_policy.min_confidence`。
- `needs_review`：top1 是某類但信心介於 `[min_confidence, τ_review)`，或 E/V 不一致 → 檔案整理階段放「待確認」資料夾，UI 提供快速人工審核（審核結果可回饋成範例圖）。

**輸出**：

```python
class ClassifyOut(BaseModel):
    labels: list[str]                   # 單標籤時 len=1
    scores: dict[str, float]            # 校準後各類機率
    confidence: float
    margin: float | None
    decision: Literal["accepted","other","needs_review"]
    source: Literal["rule","embedding","vlm","vlm_large","consensus"]
    reason: str | None                  # VLM 理由
    stages: list[dict]                  # 每段的 top-k 與分數（除錯/benchmark 用）
```

---

## 4. 資源管理（ResourceManager）

### 4.1 VRAM 預算模型

```
budget = total_vram × safety (0.92) − reserved_external (Ollama/其他程式, 由 nvidia-smi/NVML 讀) − desktop_reserve (Windows 預設 0.8GB)
need(model) = weights + mmproj + runtime_overhead + kv_per_1k × ctx_k × concurrency + activation_peak(max_pixels)
```

- 以 NVML 讀實際 `used/free`，載入後回寫「實測佔用」到 `~/.opagent/cache/vram_observed.json`，下次以實測值取代估算（自我修正）。
- vLLM 會預先吃掉 `gpu_memory_utilization × total`，所以傳入的 utilization = `need(vlm) / total`，而不是預設 0.9，否則無法並載 embedding。

### 4.2 載入策略

```
Slots（單 GPU）:
  ┌─ small slot（常駐）: embedding（SigLIP2 ~1.5GB）[+ tagger ~1.5GB]
  └─ large slot（一次一個）: VLM / captioner
```

1. **常駐小模型 + 一個 VLM** 是 16GB 預設：SigLIP2(1.5) + Qwen3-VL-8B FP8 vLLM(~11.5) ≈ 13GB < 14.7 預算 ✓【估算】。
2. **LRU 卸載**：需要載入新模型且預算不足時，依「最後使用時間」卸載，順序 `large slot` 優先於 `small slot`；正在服務請求的模型不可卸載（引用計數）。
3. **熱切換**：使用者換模型 → 新 job 進入「waiting_model」；舊模型處理完 in-flight 請求後卸載 → 載入新模型。vLLM 冷啟動慢，故 job 排程器（其他文件）應**依模型分組排序 job**，推論層提供 `ensure_ready()` 與 `resource_status()` 供其決策。
4. **閒置卸載**：large slot 閒置 > 10 分鐘（可設）自動卸載，讓使用者 GPU 回到遊戲/其他工作。
5. **序列模式**（預算不足並載時，8GB 預設）：job 拆兩階段——Phase A 全批 embedding（寫快取）→ 卸載 → Phase B VLM 只處理需要的圖。

### 4.3 OOM 處理

- transformers：捕捉 `torch.cuda.OutOfMemoryError` → `empty_cache()` → batch 減半重試，最低到 1；仍失敗 → 降 `max_pixels` 一檔。
- vLLM / llama-server 子行程崩潰：health 失敗 → 重啟一次，並將 `max_num_seqs`/`--parallel` 減半；連續兩次失敗 → 標記 variant 不可用，嘗試下一個 variant（例如 FP8 vLLM → Q4 llama.cpp）。
- 所有 fallback 都寫入 provenance，benchmark 與結果頁可見。

### 4.4 多 GPU / 遠端 GPU

- 多 GPU：`DeviceAlloc` 可指定 `cuda:N`；預設 embedding 放顯存小的卡、VLM 放大卡。tensor parallel 僅 vLLM 支援，屬進階設定。
- 遠端 GPU worker（「雲端控制台 + 地端 agent」模式）：推論層整包跑在地端 agent 行程內，對外仍是 `InferenceService`；影像不出本機。

### 4.5 Blackwell（sm_120）/ CUDA 12.8 注意事項

| 項目 | 要求 / 注意 |
|---|---|
| PyTorch | 必須 cu128（或更新）wheel，`torch.cuda.get_device_capability()==(12,0)` 自檢；cu121/cu124 wheel 會報 `no kernel image` |
| vLLM | 使用含 sm_120 kernel 的版本（官方 wheel 或 Docker image，需 CUDA 12.8 build）；FP8 / FlashInfer / attention backend 在消費級 Blackwell 的支援程度依版本而異【未驗證，啟動時自檢並 fallback 到 llama.cpp】|
| llama.cpp | 以 `-DGGML_CUDA=ON -DCMAKE_CUDA_ARCHITECTURES=120` 編譯，或使用 CUDA 12.8 的官方 release |
| onnxruntime-gpu | 需對應 CUDA 12.x + cuDNN 9 的版本 |
| bitsandbytes / flash-attn | 預編譯 wheel 可能不含 sm_120 → JoyCaption NF4 等路徑提供「不使用 bnb」的替代（GGUF 或 FP8）|
| 驅動 | NVIDIA ≥ 570 系列（CUDA 12.8 需求）|
| 自檢 | 推論層啟動時跑 `opagent doctor`：列出 GPU、驅動、CUDA、torch build、各 runtime 版本與一次 1 張圖 smoke test |

官方 Docker image 分 `opagent-gpu-cu128`（預設）與 `opagent-gpu-cu124`（舊卡），避免使用者自行處理。

### 4.6 CPU fallback

| 能力 | CPU 方案 | 預估速度【估算，8 核桌機】 |
|---|---|---|
| embedding | SigLIP2 / PE-Core ONNX fp32 / int8 | 2–8 張/秒 |
| tagger | WD v3 ONNX | 2–5 張/秒 |
| caption | Florence-2-base | 0.5–1 張/秒 |
| VLM | llama.cpp CPU（Gemma 4 E2B/E4B、Qwen3-VL-4B Q4）| 0.05–0.2 張/秒（僅小量 / 試用）|
| 外部 | 建議改用 openai-compatible endpoint | — |

無 GPU 時 UI 在模型選擇處顯示預估時間並建議外部 endpoint。

---

## 5. 效能

### 5.1 批次策略

| Runtime | 策略 |
|---|---|
| vLLM | 不做「請求內多圖」，而是**並發單圖請求**（asyncio semaphore = `max_num_seqs`），讓 continuous batching 自動合批；共用前綴（system + 類別清單）開 `--enable-prefix-caching` |
| llama.cpp | `--parallel N`（N=2–4），semaphore=N；影像編碼（mmproj）是序列瓶頸 |
| transformers embedding | 固定 batch（64，OOM 減半）；影像解碼/縮放在 DataLoader 多 worker（CPU）進行，GPU 端 pinned memory |
| 前處理 | 統一在推論層前置一個 `ImagePrep`：EXIF 轉正、HEIC（pillow-heif）/RAW 轉 RGB、縮到該模型 `max_pixels`、JPEG q=90 編碼（給 HTTP adapter）；用 process pool，結果以 `(sha256, model_family, max_pixels)` 快取 |

管線化：Prep（CPU）→ Embed（GPU small）→ VLM（GPU large）三段以 bounded queue 串接，維持 GPU 不空轉。

### 5.2 解析度 / 影像 token 控制

Qwen-VL 系的影像 token 數 ≈ `pixels / (patch × merge)²`：

| 系列 | 每 token 對應像素 | 1MP 約幾個 token |
|---|---|---|
| Qwen2.5-VL | 28×28 | ~1,280 |
| Qwen3-VL / Qwen3.5 / 3.6（patch 16, merge 2）| 32×32【未驗證】| ~980 |

- Registry 的 `image.min_pixels / max_pixels` 會以 `mm_processor_kwargs`（vLLM）或前處理縮圖（llama.cpp/外部）方式套用。
- 任務預設上限：classify **0.25–0.4MP**（~256–400 token，足以分類）、tag **0.6MP**、caption long **1MP**、OCR/文件類 **1.5MP**。
- Gemma / InternVL 等固定解析度或 tile 式模型：以 `max_tiles` 控制（InternVL 預設 1–6 tile；分類用 1–2）。
- 經驗法則：影像 token 從 1,000 降到 300，單張預填時間約降 2–3 倍，分類準確率影響通常很小【需 benchmark 確認】。

### 5.3 預估吞吐（**全部為估算**，RTX 5070 Ti 16GB，單圖 ≤0.6MP）

| 任務 / 模型 | Runtime | 估算吞吐 | 1 萬張約需 |
|---|---|---|---|
| SigLIP2 so400m embedding | transformers fp16 | 150–300 張/秒 | 1 分鐘內（不含解碼）|
| PE-Core B embedding | transformers fp16 | 400–800 張/秒 | <30 秒 |
| WD v3 tagger | onnx GPU | 80–150 張/秒 | 1–2 分鐘 |
| Florence-2-large caption | transformers | 10–20 張/秒 | 10–15 分鐘 |
| Qwen3-VL-8B FP8 classify（短輸出 ~40 tok） | vLLM 並發 16 | 4–8 張/秒 | 20–40 分鐘 |
| Qwen3-VL-8B FP8 fused JSON（~150 tok） | vLLM 並發 16 | 2–4 張/秒 | 40–80 分鐘 |
| Qwen3-VL-8B Q4 fused | llama.cpp parallel 4 | 0.7–1.5 張/秒 | 2–4 小時 |
| Qwen3.6-35B-A3B Q4（--n-cpu-moe）fused | llama.cpp | 0.15–0.4 張/秒 | 7–18 小時 → 只做複核 |
| JoyCaption long caption（~300 tok） | vLLM / transformers | 0.5–1.5 張/秒 | 2–5 小時 |
| Cascade（30% 送 VLM） | SigLIP2 + Qwen3-VL-8B | 有效 12–25 張/秒 | 7–15 分鐘 |

UI 的「預估完成時間」先用此表，執行 50 張後改用實測移動平均。

### 5.4 Embedding 快取

- Key：`(image_sha256, model_id, model_revision, preprocess_version)`；Value：fp16 向量。
- 儲存：每個模型一個 append-only 檔（`~/.opagent/cache/emb/<model>/shard-*.npy` + SQLite/LMDB 索引），或若系統 DB 為 Postgres 則可同步寫 pgvector（由資料層文件決定，推論層只提供 `EmbeddingCache` 介面）。
- 記憶體：最近 N 萬筆 LRU；prototype、類別文字向量也快取（key 含描述文字 hash）。
- 用途：分類 E 段、受控 tag 候選預篩、去重（cos>0.97 視為近似重複，可在整理時標記）、以文搜圖、benchmark 重跑免重算。

```python
class EmbeddingCache(Protocol):
    def get_many(self, keys: list[str], model_id: str) -> dict[str, np.ndarray]: ...
    def put_many(self, items: dict[str, np.ndarray], model_id: str) -> None: ...
    def invalidate_model(self, model_id: str) -> None: ...
```

---

## 6. 品質評估：內建 Mini Benchmark

目的：讓使用者用**自己的照片**判斷「哪個模型組合最適合我的類別 / 我的硬體」。

### 6.1 Golden set

- 從來源資料夾**分層抽樣** 50–100 張（每類至少 5 張；加入 10% 疑似 other），UI 提供快速標註（鍵盤 1–9 選類、空白鍵下一張、tag 勾選）。
- 存成 `golden_set{ id, taxonomy_id, items: [{asset_id, labels[], tags[], reference_caption?}] }`，可匯出/匯入 JSONL。
- 標註過的 golden 圖片**自動可作為 few-shot 範例**，但 benchmark 時以 k-fold（k=5）切開，避免範例與測試重疊（洩漏）。

### 6.2 BenchmarkSpec

```yaml
benchmark:
  golden_set: gs-family-2026
  task: classify            # classify | tag | caption | fused
  candidates:
    - { pipeline: embedding, model: siglip2-so400m-384 }
    - { pipeline: embedding, model: pe-core-l }
    - { pipeline: vlm_enum, model: qwen3-vl-8b-instruct@fp8 }
    - { pipeline: vlm_enum, model: gemma4-e4b@q4 }
    - { pipeline: cascade, embed: siglip2-so400m-384, vlm: qwen3-vl-8b-instruct@fp8 }
    - { pipeline: vlm_enum, model: my-lmstudio-gemma }    # 外部 endpoint 也可比
  repeats: 1
  max_pixels: [262144, 602112]   # 可同時比較解析度
```

### 6.3 指標

| 任務 | 指標 |
|---|---|
| Classification | Accuracy、Macro-F1、每類 P/R、混淆矩陣、**覆蓋率–準確率曲線**（依信心門檻，顯示「自動採用 X% 時準確率 Y%」）、ECE（校準誤差）、other 召回 |
| Tagging | Precision@k、Recall、F1（以 canonical id 比對，同義詞已合併）、平均 tag 數、新詞率 |
| Caption | 無參考：VLM-as-judge（另一模型依 rubric 打 1–5：正確性、幻覺、完整度、語言品質）+ 人工 A/B 盲評（UI 並排兩個 caption 選較佳）；有參考 caption：CLIPScore/SigLIP 圖文相似度作輔助 |
| 共同 | 每張延遲 p50/p95、吞吐、峰值 VRAM（NVML 取樣）、JSON 解析失敗率、外部 API token/費用 |

### 6.4 輸出與自動建議

- 結果表 + 「推薦」：在使用者設定的約束（例如「1 萬張要在 1 小時內」「VRAM ≤ 16GB」）下選 Macro-F1 最高者；同時給出 cascade 的建議門檻 `τ_high`（使覆蓋率最大且準確率 ≥ 目標，例如 95%）。
- 一鍵「套用為此 taxonomy 的預設」，並寫入校準參數（§3.3.3）。
- Benchmark 報告存成 JSON，含所有 provenance，可分享到社群（不含圖片）作為模型比較資料。

---

## 7. 模型下載與管理

### 7.1 下載流程

```
UI 選模型 → 檢查 license.requires_acceptance
   → 顯示授權摘要 + 連結，使用者勾選同意（記錄 user/time/license 版本）
   → gated repo：需 HF token（存 OS keyring / secret store，不寫入 YAML）
→ huggingface_hub.snapshot_download(repo, revision, allow_patterns=variant 所需檔案)
   - GGUF 只下載指定量化檔 + mmproj（避免整個 repo 數十 GB）
   - 支援 HF_ENDPOINT 鏡像、hf_transfer / Xet 加速、續傳
→ 驗證：
   - LFS 檔以 HF API 提供的 sha256 比對（snapshot 下載後再算一次）
   - 內建模型的 `revision: <commit>` 與 size／sha256 清單一律放在 CI 產生的 `opagent_registry/builtin/pins.lock`（ADR-0002）；未釘選的 variant 不推薦、不可下載（`MODEL_NOT_PINNED`）
→ 註冊為 installed，寫入 ~/.opagent/models/installed.json（路徑、大小、sha、revision、下載時間）
→ 自動跑 smoke test（1 張內建測試圖）
```

- 下載作為推論層的長任務，透過 `on_progress` callback 回報（bytes / total / speed），由 worker/API 層顯示。
- 磁碟空間預檢：下載前檢查可用空間 ≥ 檔案大小 × 1.1。
- Ollama 模型：呼叫 `ollama pull`（由 Ollama 管理儲存），推論層只記錄 tag。
- 安全：只允許 `safetensors` / `gguf` / `onnx`；拒絕 `.bin`/pickle 權重，除非使用者在進階設定明確允許（`trust_remote_code` 同理，Florence-2 / InternVL 等需要時列出要執行的檔案並要求確認，或改用已整合進 transformers 主線的版本）。

### 7.2 離線模式

- `OPAGENT_OFFLINE=1` → 設 `HF_HUB_OFFLINE=1`、`TRANSFORMERS_OFFLINE=1`；Registry 只列出 installed 模型。
- 提供 `opagent models export <id> → .tar` / `opagent models import` 供離線機器（air-gapped）搬移，匯入時重驗 sha256。
- 共用模型目錄：可指向既有 `HF_HOME`、LM Studio / Ollama 的 models 目錄（唯讀掃描 GGUF），避免重複下載。

### 7.3 模型管理 UI 需要的資料（`list_models()` 回傳）

```json
{
  "id": "qwen3-vl-8b-instruct",
  "variants": [
    { "id": "fp8", "runtime": "vllm", "installed": true, "size_gb": 10.6,
      "vram_need_gb": 11.5, "fits_now": true, "fits_with_embedding": true,
      "observed": { "vram_gb": 11.2, "imgs_per_s": 3.1 } }
  ],
  "capabilities": ["caption", "tag", "classify"],
  "license": { "spdx": "Apache-2.0", "accepted": true },
  "tiers": ["8gb", "16gb", "24gb"],
  "recommended_for": ["classify", "fused"]
}
```

---

## 8. 16GB 預設組合（總結）

| 項目 | 預設 |
|---|---|
| 常駐 small slot | **SigLIP2 so400m-384（fp16 ONNX, ~1.5GB）**；v0.2 起人臉 YuNet + AuraFace（ONNX, ~0.8GB，07 §7.1） |
| large slot | **Qwen3-VL-8B Q5_K_M／Q4_K_M on llama.cpp（~6.5–8GB）**（審查補充：預設改為 llama.cpp，08 C-02）；Linux／Docker 進階可選 Qwen3-VL-8B-Instruct FP8 on vLLM（~11.5GB） |
| 難例複核（選用，序列切換） | Qwen3.6-35B-A3B Q4（llama.cpp `--n-cpu-moe`，需 ≥32GB RAM）|
| 長 caption（選用） | JoyCaption Beta One（英文）/ Qwen3.6-35B-A3B（繁中）|
| Caption 預設 | 短、繁中（+英文可選）、neutral 模板；與 tag/classify 合併成一次 fused JSON |
| Tagging 預設 | hybrid：受控詞彙（內建一般照片詞彙）+ ≤3 新詞，max 15，正規化 + 同義詞合併 |
| Classification 預設 | Cascade：rules → SigLIP2（文字 + 範例 prototype）→ 低信心送 Qwen3-VL enum（logprob 信心）→ other / needs_review |

## 9. 待驗證清單（實作前 spike）

1. Qwen3-VL-8B FP8 在 vLLM + RTX 5070 Ti（sm_120）上的可用性、實際 VRAM 與吞吐。
2. llama.cpp 對 Qwen3-VL / Qwen3.6 mmproj 的支援狀態與 logprobs 介面。
3. Qwen3-VL 系 patch/merge 尺寸與 `min/max_pixels` 參數名稱。
4. SigLIP2 對繁中類別名稱的 zero-shot 品質（vs 自動英譯）。
5. 各 grammar 後端對 `enum`/`uniqueItems`/`minItems` 的支援，以及 key 順序（reason 先於 category）對準確率影響。
6. Gemma 4 26B-A4B Q4 在 16GB 的實際可用 context。

---

## 10. 介面整合：06 / 07 / 08 / 10 的變更（審查補充）

> 集中整理 06 §7.2、07 §11、08 §7（C-02、C-12–C-15、C-30）、10 §C 對推論層的需求。細節以原文件為準；版本依 08 §9。

| 主題 | 變更 | 來源 | 版本 |
|------|------|------|------|
| 內建整理方式（審查補充，08 §8.1） | `opagent_api/presets/builtin/`：**`by-date`**（不跑模型，只用 EXIF／mtime，樣板 `{taken_at:%Y}/{taken_at:%Y-%m}/{stem}{ext}`，選完資料夾就能用）；**`by-content-12`**（內建類別集：人物、風景、美食、寵物、文件與截圖、旅行、活動、建築、植物、交通、藝術、其他；每類附 zh-Hant／en 名稱與一句描述，供 SigLIP2 zero-shot 與 VLM enum 使用；「其他」為 fallback）；**`custom`**（使用者類別 = 名稱 + 描述 + 範例照片 prototype，§3.3.1）。三者都是一般 Pipeline preset，可在進階模式檢視 YAML | 08 §8.1 | v0.1 |
| Runtime 主軸 | llama.cpp（CPU／Metal 內建，`llamacpp-cuda13`、`llamacpp-vulkan` pack）+ ONNX Runtime（DirectML／CoreML／CPU，CUDA EP 為 pack）；vLLM（僅 Linux）、MLX（僅 Mac）、torch 為 v0.4 選用 pack | 08 §3、D5 | v0.1／v0.4 |
| 小模型路徑 | SigLIP2、tagger、人臉改走 ORT；新增 SigLIP2 patch14-384 ONNX 匯出與數值對齊測試（與 PyTorch cosine ≥ 0.999） | 08 C-12 | v0.1 |
| Registry 欄位 | `variants[].requires`、`variants[].files[]`（size + sha256 釘選）、`perf_prior`、`tasks_default_rank`；`kind` 增加 `face_detector`／`face_embedder`／`face_quality`；`license` 拆成 `code`／`weights`，新增 `redistributable`、`biometric`、`training_data`、`remote_allowed` | 06 §1.1、07 §11 | v0.1／v0.2 |
| 新 registry | `runtimes/*.yaml`（runtime pack）、`gpu_classes.yaml`（GPU 係數，給 ETA 冷啟動先驗） | 06 §1.1 | v0.1 |
| `list_models()` | 回傳 compat 物件 `level: ok\|degraded\|blocked`、`reasons[]`、`prep[]`（取代 `fits_now` 布林值，舊欄位保留） | 06 §1.2 | v0.1 |
| 下載 | `snapshot_download` 改為 06 §2.2 自寫 Range 下載器（真取消、暫停、續傳、sha256、鏡像）；`on_progress` 改為 `OperationReporter.phase()/advance()/warn()` | 06 §2 | v0.1 |
| 載入進度 | `InferenceBackend.load(opts, reporter)` 回報載入子階段；下載不等於載入（不佔 VRAM、不打斷 Job） | 06 §2.8 | v0.1 |
| 錯誤 | `InferenceError.code` 對應 06 §4.7 錯誤碼目錄（含 `PACK_MISSING`、`DRIVER_TOO_OLD`、`GPU_CONTENDED`、`ON_BATTERY_PAUSED` 等） | 06 §4.7、08 §10 | v0.1 |
| Doctor | §4.5 的 `opagent doctor` 由 06 §3 的 Check registry 實作；**不需要 torch** 也能偵測 GPU（NVML、DXGI、Vulkan、Metal）並輸出 `recommended_packs` 與檔位 | 06 §3、08 §10 | v0.1 |
| VRAM 預算 | 新增 `DxgiBudget`、`VulkanBudget`、`UnifiedMemoryBudget`（Mac）provider，與 06 doctor 共用；`vram_observed.json` 移到 DB 的 `perf_observation` | 08 C-13、06 §7.2 | v0.1 |
| Blackwell | 預設用 `llamacpp-cuda13` pack（或自建 12.8+），doctor 依 compute capability 選 pack | 08 C-15 | v0.1 |
| fingerprint | runtime 部分改用 pack 的 `numerics_epoch`，pack 小版更新不讓快取失效（取代 00 R2 的「runtime 版本」） | 08 C-14、§4.3 | v0.1 |
| 資源策略 | `pause_on_battery`、`pause_on_contention`（free VRAM 不足時改用小 variant 或排隊，不搶資源）、`only_when_idle`；OOM 時 batch 減半 → 暫停並通知 | 08 §6、C-30 | v0.1 |
| 人臉 | `Capability.FACE`、`FaceConfig`、`FaceOut`、三個 Face adapter Protocol、`embed_query_faces`；small slot 加入人臉模型；8 GB 序列模式加入 Phase F；benchmark `face_verify`、`face_cluster`；非商用權重的同意寫入 `face_consent`、禁止第三方鏡像 | 07 §8.1–8.2、§11 | v0.2 |
| 搜尋需求 | `embed_texts`、`query.plan`、`rerank`、`describe`、`contains_image_text`（00 R3） | 05 | v0.2 |
| Ask | `InferenceService.chat(messages, tools?, response_format?, images?, priority="interactive")` 串流；能力探測新增 `tools`、`parallel_tools`、`multi_image`；llama.cpp `--jinja` 並保留 1 個 interactive slot；新任務 `verify`（yes/no/unsure + score + ≤ 30 字理由）；`describe` 支援多圖（≤ 4） | 10 §C | v0.3 |
| Benchmark | 加入 Ask 評估集與 tool-calling 可靠度指標（JSON 合法率、工具選擇正確率）；mini benchmark UI 在 v0.3 | 10 §C、08 §9.2 | v0.3 |
