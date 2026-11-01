# voicebox 設計研究：給 OpenPhotoAgent 的借鏡筆記

- 參考專案：`scratchpad/ref/voicebox`（commit `8af7efe`，2026-10-07）
- 以下路徑都相對於 repo 根目錄，格式是 `檔案:行號`。
- 只做靜態閱讀，沒有執行或安裝任何東西。

架構速覽：Tauri 2 殼（`tauri/`）＋共用 React 前端（`app/src`，同一份程式碼也做成純 Web build，放在 `web/`）＋ FastAPI 後端（`backend/`）。後端在桌面版是 PyInstaller sidecar，在 Docker 版則是 uvicorn。GPU 推論在後端同一個 process 內，用 asyncio 序列佇列處理，沒有獨立的 Worker process。

---

## 1. 模型選擇 UI

### voicebox 做法

**模型註冊表（後端）**：用宣告式的 `ModelConfig` dataclass，每個 backend 自己提供 `MODEL_CONFIGS`。
`backend/backends/__init__.py:53-69`
```python
@dataclass
class ModelConfig:
    model_name: str        # "qwen-tts-1.7B"
    display_name: str
    engine: str
    hf_repo_id: str
    aux_hf_repo_ids: tuple[str, ...] = ()   # 載入時額外抓的 repo（tokenizer 等）
    model_size: str = "default"
    size_mb: int = 0                         # 預估下載大小
    supports_instruct: bool = False
    languages: list[str] = ...
```
`get_all_model_configs()`（`__init__.py:568`）把 TTS、STT、LLM 三類合成一張清單。

**狀態 API**：`GET /models/status`（`backend/routes/models.py:288-456`）會掃 HF cache（`scan_cache_dir()`），對每個模型回傳下面這組欄位：
`backend/models.py:504-513`
```python
class ModelStatus(BaseModel):
    model_name; display_name; hf_repo_id
    downloaded: bool; downloading: bool
    size_mb: Optional[float]   # 只有已下載時才有（實際磁碟大小）
    loaded: bool
```
判定「已下載」的條件（`models.py:342-412`）：
- 有權重檔（`.safetensors/.bin/.pt/.pth/.npz`）。
- blobs 底下沒有孤兒 `.incomplete`（`backends/base.py:26-41`）。
- 所有 `aux_hf_repo_ids` 也都已快取。

**模型管理頁**：`app/src/components/ServerSettings/ModelManagement.tsx`
- 頁面分成三段：Voice Generation、Transcription、Language Models（`:413-433`）。分段靠 `model_name.startsWith(...)` 前綴硬分。
- 每一列（`:549-611`）有：
  - 左側狀態圖示：錯誤 `CircleX`、下載中 `Loader2` 轉圈、已載入用 accent 色勾、已下載用綠勾、未下載是淡色 `Download`。
  - 名稱，下載中時下方加一條 h-1 的 Progress 和 `x MB / y GB (z%)`。
  - 右側 Badge：`Error`、`Loaded`、磁碟大小。
- 點一列會開詳細 Dialog（`:682-932`），內容是：
  - HF repo 連結。
  - 前端寫死的描述 `MODEL_DESCRIPTIONS`（`:55-96`），內容包括參數量、語言數、適合的硬體，例如「ideal for lower-end hardware」「~2.5 GB quantized on Apple Silicon, ~8 GB at full precision」。
  - **瀏覽器直接打 `https://huggingface.co/api/models/{repo}`**（`:49-53, 184-190`）拿 pipeline_tag、library、作者、下載數、likes、license、語言清單（`:737-797`）。
  - 磁碟大小、錯誤訊息框。
  - 動作按鈕依狀態切換（`:817-927`）：Retry+X、Progress+X、Unload/Delete、Download。

**生成時切換模型**：`app/src/components/Generation/EngineModelSelector.tsx`
- 就是一個 shadcn `Select`。選項是前端寫死的 `ENGINE_OPTIONS`（`:19-32`），只顯示 label，沒有下載狀態、大小或 VRAM。
- 用 profile 類型過濾可用的 engine（`isProfileCompatibleWithEngine`，`:176-185`）。
- 切換時自動把語言修正到該 engine 支援的範圍（`applyEngineSelection`，`:74-122`）。
- 不存在「全域使用中模型」這回事。模型跟著每次生成請求走，後端在 `run_generation` 中按需 `load_engine_model`（`backend/services/generation.py:84-87`）。

### 可借鏡
- **宣告式模型註冊表＋`aux_repo_ids`**：VLM 常要額外抓 processor、tokenizer、vision tower 或 mmproj，這些也要算進「已下載」和「刪除」。
- **用 `.incomplete` 孤兒判斷下載未完成**（`base.py:26-41`）：如果同名的完成 blob 已經存在，就忽略殘留的 `.incomplete`。這招可以直接學。
- 列表加詳細 Dialog 的兩層結構，以及依狀態切換動作按鈕的模式，很適合 shadcn。
- engine 切換時自動修正不相容的參數（我們的對應情境：模型不支援某語言的 caption、某種 tag schema 或某個解析度）。

### 不足／我們的改進
- **未下載的模型不顯示預估大小**。`ModelConfig.size_mb` 有值，但 `/models/status` 沒回傳，只有 Dictation readiness 那條路線有用到（`backend/routes/captures.py:191`）。我們應該在 status 回傳 `expected_size_bytes`、`min_vram_mb`、`recommended_vram_mb`、`quantization`、`languages`、`license`。
- **沒有任何 VRAM 需求資訊**，連「是否放得下」的判斷都沒有（見第 3 點）。
- 描述、分段、選項都在前端寫死成三份（`MODEL_DESCRIPTIONS`、`ENGINE_OPTIONS`、`startsWith` 分段），和後端註冊表會脫鉤。我們應該讓後端註冊表成為唯一來源，前端全部由 API 驅動。
- 前端直接連 HF API，離線或內網部署時就失效，也會洩漏使用者在看哪個模型。我們應改由後端代理並快取 model card metadata，或者在註冊表內建這些資料。
- 選擇器不顯示「未下載」或「需下載 x GB」，使用者要到送出生成後才發現要下載。我們的 Select 項目應附狀態點、大小和 VRAM 適配徽章，未下載的項目旁直接給「下載」。

---

## 2. 模型下載管理

### voicebox 做法

**流程**：`POST /models/download`（`backend/routes/models.py:459-494`）
```python
load_func = get_model_load_func(config)
async def download_in_background():
    try:
        result = load_func(); ...
        task_manager.complete_download(request.model_name)
    except Exception as e:
        task_manager.error_download(request.model_name, str(e))
task_manager.start_download(request.model_name)
progress_manager.update_progress(..., filename="Connecting to HuggingFace...", status="downloading")
create_background_task(download_in_background())
```
注意：「下載」其實就是呼叫**載入模型**的函式（`from_pretrained` 時順便下載），所以下載完模型會直接佔用記憶體／VRAM。

**進度攔截**：`backend/utils/hf_progress.py`
- 把 `tqdm.tqdm`、`tqdm.auto.tqdm`、`sys.modules` 內 huggingface 模組的 tqdm 引用全部換掉（`:237-276`），並且 monkey-patch `huggingface_hub.utils.tqdm.update`（`:281-319`）。
- 按檔案分別累加 bytes，加總成整體進度（`:137-143`）。
- 排除兩種進度條：不是 byte 單位的「Fetching N files」（`:174-179`），以及總量小於 1MB 的設定檔進度（`:145-150`，用來避免「0MB 就 100%」）。
- 套件接在 `model_load_progress()` context manager 中（`backends/base.py:294-356`），已快取時再加上 `force_offline_if_cached`（`utils/hf_offline_patch.py:63`）。

**進度回報 = SSE（加上 polling 備援）**
- `ProgressManager`（`backend/utils/progress.py`）
  - 用 thread-safe 的 dict 存每個模型的最新進度，listener 是 `asyncio.Queue(maxsize=10)`（`:205`）。
  - 背景執行緒透過 `call_soon_threadsafe` 推送（`:53-56`）。
  - **節流**：每 0.5 秒推一次，或進度變化達 1% 時推，complete/error 一律推（`:20-21, 123-131`）。
  - 每秒沒資料就送 `: heartbeat`（`:245-248`），遇到 complete/error 自動關閉串流（`:242-244`）。
  - 新的訂閱者先拿到當前快照，但已結束的舊狀態不送（`:221-229`）。
- `GET /models/progress/{model_name}` 是 SSE（`routes/models.py:155-172`），header 有 `X-Accel-Buffering: no`（反向代理友善）。
- 前端 `useModelDownloadToast`（`app/src/lib/hooks/useModelDownloadToast.tsx:58-198`）：
  - 建一個 `duration: Infinity` 的 toast，用 `EventSource` 持續 `update()` 它。
  - 結束時改成 3–5 秒後自動消失。
  - 失敗時提示「See Problems panel for details.」（`:104`）。
- 同時 `GET /tasks/active`（`backend/routes/tasks.py:45-125`）合併 TaskManager 和 ProgressManager 的資料給 polling 用。模型頁在有下載時每 1 秒 poll 一次，平時每 5 秒（`ModelManagement.tsx:173-181`）。全域另有 `useRestoreActiveTasks` 每 30 秒 poll，頁面重整後可以恢復進行中的工作（`app/src/lib/hooks/useRestoreActiveTasks.tsx:24-72`）。

**呈現的數值**：只有 `current / total (pct%)` 和目前檔名（`ModelManagement.tsx:582-584`）。total 未知時 Progress 是 indeterminate，文字顯示 filename 或「Connecting...」。**沒有速度，也沒有 ETA**（全 repo 搜不到 speed/eta/remaining）。

**暫停／續傳／取消／重試**
- 暫停：沒有。
- 續傳：沒有自己實作，靠 HF Hub 對 `.incomplete` 的原生續傳。
- 取消：`POST /models/download/cancel`（`routes/models.py:497-513`）只把 task 從 dict 移除並刪掉進度紀錄，**背景下載的 coroutine 和執行緒不會真的停**。註解也寫明這是「Cancel or dismiss an errored/stale download task」。
- 重試：錯誤的 task 會留在 active list（`utils/tasks.py:71-82`，issue #925 修正後 `/models/status` 不會把它當成 downloading），UI 給 Retry 按鈕（`ModelManagement.tsx:818-840`），本質上就是重新 POST download。
- 「Problems」面板：VSCode 風格的深色 console，列出所有錯誤，有「Clear all」（`ModelManagement.tsx:619-676`）。

**完整性**：只有 CUDA/ROCm 後端 binary 的下載有 SHA-256 驗證、`.tmp` 暫存、`tar extractall(filter="data")` 防路徑穿越（`backend/services/cuda.py:167-264`），而且分「Downloading / Verifying / Extracting」三個階段標示在 filename 欄位。模型下載沒有額外驗證，信任 HF 的 etag。

**磁碟空間**
- 下載前**完全不檢查**。
- 只有 `GET /health/filesystem`（`backend/routes/health.py:195-260`）會回 `disk_free_mb`，低於 500MB 就標 unhealthy，並且用寫 probe 檔的方式測試目錄可寫。這支 API 只在 Generation/Captures 設定頁拿來顯示路徑。

**快取位置與搬移**
- 用 HF 預設的 `HF_HUB_CACHE`。`GET /models/cache-dir` 回傳路徑（`routes/models.py:175-180`），UI 顯示路徑，並有「Open」「Change」「Reset」（`ModelManagement.tsx:459-531`）。
- 「Change」會呼叫 `POST /models/migrate`（`routes/models.py:183-265`）：
  - 同一個檔案系統就直接 `shutil.move`，跨檔案系統則逐檔 copy，並以 byte 為單位回報進度。
  - 防呆：目標不能和來源相同，也不能在來源底下。
  - 進度一樣走 SSE（`:268-285`）。前端顯示全螢幕 overlay（`ModelManagement.tsx:1079-1105`），完成後重啟 server。

**刪除**：`DELETE /models/{name}`（`routes/models.py:516-551`）先 unload，再 `rmtree` 主 repo 和 aux repo。UI 要求「先 unload 才能刪」，載入中時 Delete 按鈕會停用並給 tooltip（`ModelManagement.tsx:905-910`）。確認框會寫出會釋放多少空間（`:945-950`）。

**按需下載（202）**：Transcription 和 LLM 路由遇到模型未快取時，會啟動背景下載並回 **HTTP 202**，附上 `{"downloading": true, "model_name": ...}`（`backend/routes/transcription.py:68-89`，`backend/routes/llm.py:47-66`），讓 client 知道要等或去訂閱進度。

### 可借鏡
- **攔截 tqdm 拿 HF 進度**的整套作法，以及兩條過濾規則（排除 non-byte 進度條、1MB 門檻）。我們可以先改用 `huggingface_hub` 新版的 `tqdm_class` 參數，不行時再退回 monkey-patch。
- `ProgressManager` 的設計（節流、heartbeat、快照回放、終態自動關閉、thread-safe 推送）可以幾乎原樣搬到我們的 FastAPI。我們的 Worker 是另一個 process，所以要把 in-memory dict 換成 Redis pub/sub 或 DB＋通知。
- **SSE 為主、polling `/tasks/active` 為備援**，加上頁面重整後恢復工作的雙軌設計。
- 下載用不會自動消失的 toast，能常駐並持續更新進度，錯誤則集中到 Problems 面板。
- cache 搬移功能，以及「刪除前必須先 unload」的守門。
- 按需下載回 202 加 `model_name`。
- binary 下載的 sha256、暫存檔、分階段標籤。

### 不足／我們的改進
- **下載和載入綁死**：下載時會把模型載進 VRAM，可能把 Worker 正在跑的工作擠爆。我們應該用 `snapshot_download()` 做純下載，和 Worker 載入完全分離。
- **取消是假的**：我們要做真取消。方法是把 `snapshot_download` 放在可中斷的子 process 或執行緒裡，或改成逐檔 `hf_hub_download` 並在檔案之間檢查 cancel flag。`.incomplete` 保留給續傳用，這樣「暫停」就等於取消後再續傳。
- **沒有速度和 ETA**：後端用 EWMA 平滑速度（例如 α=0.3，視窗 5 秒），推送 `bytes_per_sec`、`eta_seconds`，前端顯示「12.3 MB/s · 約 3 分 20 秒」。
- **下載前不檢查磁碟**：下載前用 `HfApi().model_info(files_metadata=True)` 算出總大小，和 `shutil.disk_usage(HF_HUB_CACHE)` 比較，不夠就回傳結構化錯誤 `INSUFFICIENT_DISK`，附上 `required_bytes`、`free_bytes`，UI 則給「清理其他模型」「更改快取位置」。
- **total 未知期間**：可以先用 model_info 拿到的精確總量當分母，就不用 indeterminate 和 1MB 門檻這類 hack。
- **自動重試**：網路錯誤用指數退避自動重試 N 次，UI 顯示「重試中（2/3）」。voicebox 只能手動重試。
- 進度 key 用 `model_name` 字串，同一模型併發下載時會互相覆蓋。我們應該用 `job_id`。

---

## 3. 使用者環境相容性

### voicebox 做法

**裝置偵測**：`get_torch_device()`（`backend/backends/base.py:105-161`）的順序是：
1. 環境變數 `VOICEBOX_FORCE_CPU=1` 最優先，在 import torch 之前就判斷，所以壞掉的 torch build 也能退回 CPU。
2. CUDA，ROCm 也走這條（因為 ROCm 用 torch 的 `cuda` 介面）。
3. XPU（IPEX）。
4. DirectML。
5. MPS。
6. CPU。

Apple Silicon 另外判斷 MLX 能不能用（`backend/utils/platform_detect.py:113-129`）。

**CUDA arch 相容性**：`check_cuda_compatibility()`（`base.py:164-204`）比對 `torch.cuda._get_arch_list()` 和 GPU 的 `sm_XX`。不相容時回傳可直接操作的訊息：
```python
f"{device_name} (compute capability {capability} / {sm_tag}) is not supported by this PyTorch build. "
f"Supported architectures: {', '.join(arch_list)}. "
f"Install PyTorch nightly (cu128) for newer GPU support: pip install torch --index-url ..."
```
ROCm 會跳過這項檢查（`:178-179`）。

**AMD/ROCm 能力偵測**：Linux 看 `/dev/kfd` 是否存在；Windows 用 PowerShell WMI 查 AMD 顯卡，失敗時退回 torch 裝置名稱（`platform_detect.py:22-97`）。`SUPPORTED_GPU_ASSET_PLATFORMS` 用來避免在沒有對應 release 的平台上提供下載（`:75`）。

**Health API**：`GET /health`（`backend/routes/health.py:56-192`）回傳：
- `gpu_available`
- `gpu_type`，例如 `"CUDA (RTX 4090)"`、`"ROCm (...)"`、`"Metal (Apple Silicon via MLX)"`
- `vram_used_mb`，取自 `torch.cuda.memory_allocated()`，**只是本 process 已配置的量，不是總量也不是剩餘量**
- `backend_variant`（cpu/cuda/rocm/xpu）
- `supports_rocm`
- `gpu_compatibility_warning`
- 預設模型是否已載入／已下載

另有 `GET /health/filesystem`（第 2 點提過）。

**ffmpeg**：不在啟動時檢查，而是在要用時才找（`backend/utils/ffmpeg.py:43-58`）。macOS GUI app 拿不到 Homebrew 的 PATH，所以會額外搜尋常見目錄。找不到時拋出可行動的訊息（`:28-32`）：
```python
"ffmpeg is required for MP3/M4B export but was not found. Install it "
"(macOS: brew install ffmpeg; Windows: winget install Gyan.FFmpeg; "
"Linux: apt install ffmpeg) and restart Voicebox, or export as WAV."
```

**GPU 設定頁**：`app/src/components/ServerTab/GpuPage.tsx`
- `GpuInfoCard`（`:43-110`）顯示 GPU 名稱、backend、variant、VRAM used，加上一個會呼吸的「Active」綠點。沒有 GPU 時顯示 CPU 圖示和「No acceleration」。
- 沒有原生 GPU 時，提供「下載 CUDA backend」（約數 GB 的 binary）或 ROCm backend。下載完可以「切換到 CUDA/ROCm」或「切回 CPU」，這些動作都會**重啟 server 並每秒 poll `/health`**，畫面分 `stopping → waiting → ready` 三個階段（`:258-289, 587-597`）。

**可用／不建議／不可用的判定**：**沒有**。模型層級沒有任何硬體適配判斷，只靠描述文字暗示（「CPU realtime」「lower-end hardware」）。
`gpu_compatibility_warning` 雖然在 health 中回傳，**前端完全沒用到**（`app/src` 內 grep 不到）。

### 可借鏡
- `FORCE_CPU` 在 import torch 前就判斷，是很好的逃生門。
- **CUDA arch list 比對**：VLM 常用的 flash-attn、bitsandbytes、新的 PyTorch 都會碰到 sm 不支援的情況，這個檢查很值得做。
- `/dev/kfd` 偵測 ROCm，以及 ROCm 跳過 sm 檢查。
- 外部依賴「用時才找＋給三平台安裝指令＋給替代方案」的錯誤訊息格式。
- 切換 backend 後重啟並 poll health，分階段顯示。

### 不足／我們的改進
- **我們應做完整的 `GET /system/check`（system report）**，內容包括：
  - OS、Python、torch、CUDA/ROCm runtime 版本，以及 driver 版本（可用 `nvidia-smi` 或 `pynvml`）。
  - **每張 GPU 的 total、free VRAM**（`torch.cuda.mem_get_info()`）和 compute capability。
  - 關鍵套件版本與能否 import（transformers、accelerate、flash-attn、bitsandbytes、vllm、llama-cpp）。
  - ffmpeg 和 exiftool（照片 metadata 會用到），以及 HEIC/RAW decoder（pillow-heif、rawpy）。
  - 磁碟剩餘空間、Worker 是否存活和心跳。
  - 每一項都標 `ok | warn | fail`，附 `fix_hint` 和 `doc_url`。
- **模型適配矩陣**：後端依「模型的 `min_vram`、`recommended_vram`、`requires`（例如 `sm>=80` 或 flash-attn）、支援的量化」對照「實際的 free VRAM 和 capability」，算出每個模型的
  - `compatible`：可用。
  - `degraded`：不建議，例如只能用 CPU、需要 offload、會很慢，要附原因。
  - `incompatible`：不可用，附原因和替代建議，例如「改用 4-bit 版本」「改用 2B 模型」。

  前端在卡片和選擇器上顯示綠、黃、紅徽章。voicebox 完全沒做這件事，是最大的改進空間。
- 不要把只反映本 process 配置量的 `memory_allocated` 當成 VRAM 指標，要顯示 used/total，並且要算進其他 process 的佔用。
- 計算出的 warning 一定要呈現給使用者（voicebox 算了卻沒顯示）。

---

## 4. 錯誤與阻擋情境的處理

### voicebox 做法

**Backend 未啟動**：`app/src/App.tsx:91-305`
- Tauri 啟動 sidecar 期間顯示全螢幕 logo 和輪播的趣味文案（`LOADING_MESSAGES`，`:55-61`，每 3 秒換一句）。
- 如果是 port 被占用（`isPortInUseError`），就退回成每 2 秒 poll `/health` 並驗證回應**確實是 voicebox 的格式**（`isVoiceboxHealthResponse`），最多等 2 分鐘（`:197-225`）。
- 其他失敗會直接顯示「Server startup failed」、錯誤原文和 **Retry**（`window.location.reload()`，`:272-288`）。
- Web 版預設 server 已經在跑（`:136-143`），沒有斷線提示。`useServerHealth` 每 30 秒 poll 一次（`app/src/lib/hooks/useServer.ts:5-13`），但沒有全域的離線 banner。

**模型未下載**
- 生成：沒有阻擋，送出後後端自動下載並載入，history 列顯示「Loading model...」（`app/src/components/History/HistoryTable.tsx:555-558`）。下載期間的位元進度**不會顯示在生成列上**。
- 轉錄／LLM：回 202，加上 `downloading:true`。
- **Dictation readiness checklist**（`app/src/lib/hooks/useDictationReadiness.ts`，`app/src/components/CapturesTab/DictationReadinessChecklist.tsx`）是全 repo 最完整的「阻擋＋CTA」設計：
  - 把多個 gate 統一成 `missing[]`、`canRecord`、`allReady`（`useDictationReadiness.ts:92-97`）。
  - 每個 gate 一列，列內有圖示、標題、說明（「缺少，約 1.5 GB」）和**行內 CTA**：
    - 模型類 gate 的 CTA 是「下載」，下載中會變成「下載中 37%」並停用（`Checklist.tsx:164-194`）。
    - macOS 權限類 gate 的 CTA 是「Open Settings」（`:255-260`）。
  - 有缺項時每 5 秒 poll 一次，全綠就停止，視窗 focus 時重新檢查（`useDictationReadiness.ts:74-82`）。
  - 下載完成會自動 invalidate readiness，toggle 自動解鎖（`Checklist.tsx:120-131`）。

**沒有 GPU / GPU 不相容**：GPU 頁提供「下載 CUDA backend」「切到 CPU」。但生成流程中不會主動提示。

**空間不足**：沒有專門處理，只能等底層 OSError 冒出來。

**版本不符**：CUDA binary 的版本和 app 版本對不上時會自動重新下載（`backend/services/cuda.py:146-156, 412`）。

**錯誤型別與錯誤碼**：**沒有錯誤碼系統**。一律是 `HTTPException(status, detail=str(e))`。前端偶爾用字串比對，例如 `msg.includes('already downloaded')`（`GpuPage.tsx:300`）。409 用於狀態衝突，例如「Cannot delete CUDA backend while it is active. Switch to CPU first.」（`backend/routes/cuda.py:56-60`），訊息裡寫有修復步驟但沒有按鈕。

**長錯誤處理**：`condenseError()`（`app/src/lib/utils/errorText.ts`）把超長的錯誤，例如 4.8KB 的 transformers 「Unrecognized model」，切到 400 字左右，並註明省略了多少字。toast 會附上「Copy」action，只有被截斷時才顯示（`app/src/lib/hooks/useGenerationProgress.tsx:136-171`），失敗時引導到「Settings → Logs」。

**Worker 孤兒保護**：
- `cancel` 時如果 worker 已經不追蹤該工作，就把 DB 列標成 failed（`backend/routes/generations.py:245-259`）。
- worker 例外時呼叫 `_force_fail_if_active`（`backend/services/task_queue.py:69-93`），避免永遠停在「generating」。

### 可借鏡
- **Readiness checklist 模式**：把「能不能執行某功能」拆成多個 gate，每個 gate 有狀態和行內 CTA，完成後自動解鎖。我們「開始批次 caption」前的阻擋頁可以直接套用，gate 包括 Worker 在線、GPU 可用、模型已下載、VRAM 足夠、照片目錄可讀、磁碟足夠。
- 啟動畫面在失敗時顯示錯誤原文和 Retry，並驗證 health 回應確實來自自己的服務。
- `condenseError` 加上 Copy action，以及 Logs 頁。
- 訊息本身就寫出修復步驟（ffmpeg、CUDA arch、「Switch to CPU first」）。
- 孤兒任務自動標 failed。

### 不足／我們的改進
- **結構化錯誤**：統一回傳 `{"error": {"code": "MODEL_NOT_DOWNLOADED", "message": ..., "details": {...}, "actions": [{"type": "download_model", "model_id": ...}, {"type": "open_settings", "section": "models"}]}}`。前端用 `code` 對應到 i18n 文案和 CTA 元件，不靠字串比對。建議的錯誤碼：`WORKER_OFFLINE`、`GPU_UNAVAILABLE`、`GPU_ARCH_UNSUPPORTED`、`INSUFFICIENT_VRAM`、`MODEL_NOT_DOWNLOADED`、`MODEL_DOWNLOADING`、`INSUFFICIENT_DISK`、`DEPENDENCY_MISSING`、`VERSION_MISMATCH`、`HF_AUTH_REQUIRED`（gated model）、`NETWORK_UNREACHABLE`。
- **送出前就阻擋**：按鈕 disabled，並在 tooltip 或 inline alert 說明原因、附 CTA，不要像 voicebox 那樣送出後才默默下載好幾 GB。
- **全域連線狀態 banner**：API 或 Worker 斷線時，頂部出現黃條和「重新連線中…」；恢復後自動 invalidate queries。
- **版本握手**：前端啟動時比對 `api_version` 和 worker 版本，不相容就顯示阻擋頁。

---

## 5. 長任務的時間呈現

### voicebox 做法
- **生成**
  - 狀態機是 `loading_model → generating → completed | failed`（`backend/services/generation.py:84-96`；前端型別見 `useGenerationProgress.tsx:11-17`）。
  - 每個工作開一條 SSE：`GET /generate/{id}/status`。後端**每秒查一次 DB** 再推送（`backend/routes/generations.py:275-315`）。
  - UI 只有文字「Loading model...」或「Generating...」（`HistoryTable.tsx:555-558`），加上一個音波動畫 mode。**沒有百分比、經過時間、ETA，也沒有 chunk 進度**（`utils/chunked_tts.py` 沒有回報進度）。
- **佇列**
  - 單一 `asyncio.Queue` 序列執行，避免 GPU 爭用（`backend/services/task_queue.py:23-66`）。
  - 可以取消排隊中或執行中的工作（`:105-117`）。排隊中的會被標記，worker 取出時直接 `coro.close()` 丟棄（`:44-47`）；執行中的直接 `task.cancel()`。
  - **UI 沒有顯示佇列位置**。
- **取消**：History 列上有取消按鈕（`HistoryTable.tsx:640`）。
- **下載**：bytes 加百分比的 Progress，沒有速度和 ETA。
- **經過時間**：只有錄音 pill 有 `formatElapsed`（`app/src/components/CapturePill/CapturePill.tsx:62`）。
- **進度元件**：shadcn `Progress`，兩種高度：列表內 `h-1`、Dialog 或 toast 內 `h-2`。`value=undefined` 時是 indeterminate。
- **重啟 server**：三階段文字加 spinner。

### 可借鏡
- 明確的階段狀態機（loading_model 和 generating 分開），讓使用者知道慢在哪一段。
- 序列 GPU 佇列，可取消排隊或執行中的工作。
- 頁面重整後透過 `/tasks/active` 恢復 pending 工作的訂閱。

### 不足／我們的改進
批次照片處理是「N 張照片 × 每張數秒」的長任務，需要比 voicebox 豐富得多的時間資訊：
- **批次層級**
  - `已完成/總數`、成功／失敗／略過數。
  - 吞吐量（張/分）、**ETA**，以近 N 張的移動平均計算，前 3 張先顯示「估算中」。
  - 經過時間。
- **單項層級**：目前處理的照片縮圖和當下階段，階段為 `queued → loading_model → preprocessing → inferring → postprocessing → saved`。
- **佇列**：顯示「排隊第 3 位，前面約 2 分鐘」，可重新排序或取消。
- 模型載入階段也回報進度（載入 shards 時 tqdm 的 `Loading checkpoint shards`），不要只轉圈。
- 推送改成 Worker 主動 publish 事件，不要每秒查 DB。前端用單一多工 SSE（`/events?job_ids=...`），不要每個工作各開一條 EventSource（瀏覽器 HTTP/1.1 每個來源只能有 6 條連線，voicebox 在 `ModelProgress.tsx:11` 的註解也提到過「connection exhaustion」問題）。
- 取消要有兩段語意：「取消剩餘」和「立刻中止目前這張」。另外支援暫停／繼續批次。

---

## 6. 其他 UX 細節與不足

### 值得借鏡
- **設定頁結構**（`app/src/components/ServerTab/`）：General、Generation、GPU、Captures、MCP、Logs、Changelog、About，以 `SettingSection` 和 `SettingRow`（title、description、action）統一排版（`SettingRow.tsx`）。我們可以做成 Models、GPU/System、Storage、Logs、About。
- **Logs 頁**：訂閱 server 的 stdout（`App.tsx:127-132`，`useLogStore`），錯誤 toast 會引導到這裡。自架 Web app 可以做成 `/logs` SSE tail。
- **連線設定**：可以指定遠端 server URL，切換 URL 時 invalidate 全部 query（`app/src/stores/serverStore.ts:26-28, 68-74`）。Web build 預設用 `window.location.origin`（`:30-47`），也就是同源部署。
- **Docker 部署**
  - 多階段 Dockerfile：bun 建前端、python 3.12-slim 建後端，`ARG PYTORCH_VARIANT=cpu|rocm`（`Dockerfile:11-72`）。
  - 有 `HEALTHCHECK curl /health`（`:121-122`）。
  - 用 named volume 保存 HF cache（`docker-compose.yml:25-26`），只綁 `127.0.0.1`（`:14`）。
  - **ROCm 用 overlay compose 疊加**（`docker-compose.rocm.yml`）：`/dev/kfd`、`/dev/dri`，加上 `HSA_OVERRIDE_GFX_VERSION` 依 GPU 世代對照的註解表，以及 MIOpen cache 和 FAST find mode 等實戰調校（`:18-48`）。
  - entrypoint 會自動加入裝置節點的 GID 群組，也會採用 bind-mount 目錄的 host uid，解決權限問題（`scripts/rocm-entrypoint.sh:5-41`）。
- **離線模式**：模型已快取時強制 `HF_HUB_OFFLINE`，避免沒有網路時 `from_pretrained` 卡住（`backend/utils/hf_offline_patch.py:63`）。對自架環境很重要。
- 刪除確認框寫出會釋放多少空間。cache 搬移有全螢幕 overlay。

### 不足（我們可以做得更好）
- **沒有首次啟動 onboarding**。啟動後直接進主畫面，使用者要自己找到 Models 頁下載。我們應該做一個 3 步驟 wizard：
  1. 系統檢查，就是第 3 點的 report，有紅黃綠標示。
  2. 依 VRAM 推薦模型，標「推薦」，並一鍵下載。
  3. 選照片目錄並試跑一張預覽。
- **沒有 NVIDIA 的 Docker 選項**（compose 只有 CPU 和 ROCm，CUDA 只提供桌面版 binary 下載）。我們是 GPU 優先的自架服務，應提供 `docker-compose.cuda.yml`（`deploy.resources.reservations.devices: [{driver: nvidia, capabilities: [gpu]}]`）、ROCm overlay，並且在 `HEALTHCHECK` 裡加入 Worker GPU 自檢。
- **狀態分散在多處**：同一件事在 `ModelManagement` 用 activeTasks polling，在 toast 用 SSE，在 readiness 用另一個 polling，而且有本地 `dismissedErrors`、`localErrors` 狀態要調和（`ModelManagement.tsx:151-213`），相當複雜。我們應該用單一事件流加一個 store（例如 Zustand 或 TanStack Query cache 由 SSE 寫入）。
- **殘留未使用的元件**：`ServerSettings/ServerStatus.tsx`、`GpuAcceleration.tsx`、`ModelProgress.tsx` 基本上已被 `ServerTab/*` 取代（grep 不到使用處）。可見演進中有重複。
- **i18n 不完整**：`useModelDownloadToast` 和 `useGenerationProgress` 的 toast 文案是寫死的英文（例如 `useModelDownloadToast.tsx:63, 100-104`），其他地方用 `t()`。我們要做繁中和英文的話，一開始就要全部走 i18n。
- 程式碼殘留大量 `console.log`（`useModelDownloadToast.tsx`）。
- 全域單例 `TaskManager` 和 `ProgressManager` 都是 in-memory，server 重啟就遺失，而且無法跨 process。我們有 API 和 Worker 兩個 process，必須把工作狀態持久化（DB 加上 Redis 或 SQLite WAL 加 LISTEN 類機制）。
- 沒有任何認證（假設只跑在 localhost）。自架 Web app 若可以從區網存取，至少要有 token 或密碼。

---

## 7. 技術棧與授權

### 技術棧
| 層 | voicebox | OpenPhotoAgent |
|---|---|---|
| 殼 | Tauri 2（Rust），sidecar 啟動 Python | 無（純 Web） |
| 前端 | React **18.3**、Vite 5、Tailwind 4、Radix/shadcn、TanStack Query 5、Zustand 4、react-i18next、lucide（`app/package.json`） | React 19、Vite、shadcn/ui |
| API client | 由 OpenAPI 產生（`app/src/lib/api/schemas/*`、`scripts/generate-api.sh`） | 可借鏡 |
| 後端 | FastAPI、uvicorn、SQLAlchemy 2、torch ≥2.5、transformers ≤4.57.6、huggingface_hub（`backend/requirements.txt`）；Apple 用 MLX | FastAPI＋獨立 GPU Worker |
| 推論併發 | 同 process 的 asyncio 單一佇列 | 獨立 Worker process |
| 部署 | 桌面安裝檔，CUDA/ROCm 另外下載 binary；Docker 有 CPU 和 ROCm | Docker 優先 |

shadcn 元件和 TanStack Query 的用法和我們相同，UI 片段很容易移植。React 18→19 的差異對這些元件幾乎沒有影響。

### 授權
- 根目錄 `LICENSE`：**MIT License，Copyright (c) 2026 Voicebox Contributors**（`LICENSE:1-3`），README 也聲明 MIT（`README.md:490`）。
- vendored 的 `backend/vendor/higgs_audio_v2_tokenizer/` 是 **Apache-2.0**（Boson AI 和 HuggingFace），和我們無關，也不需要借。
- **結論：可以直接借用程式碼。** MIT 和 Apache-2.0 相容，MIT 程式碼可以併入 Apache-2.0 專案。唯一的義務是保留 MIT 的著作權聲明和授權文字。建議做法：
  - 在被借用的檔案頂端加註「Portions adapted from voicebox (https://github.com/jamiepine/voicebox), MIT License, Copyright (c) 2026 Voicebox Contributors」。
  - 在 repo 的 `NOTICE` 或 `THIRD_PARTY_LICENSES` 附上 MIT 全文。
- 最值得直接移植的檔案：
  - `backend/utils/progress.py`（要改成跨 process）
  - `backend/utils/hf_progress.py`
  - `backend/backends/base.py` 的 `has_in_progress_download`、`check_cuda_compatibility`、`get_torch_device`
  - `backend/utils/hf_offline_patch.py`
  - `app/src/lib/utils/errorText.ts`
  - `DictationReadinessChecklist` 的 ChecklistRow 模式
  - `docker-compose.rocm.yml` 和 `scripts/rocm-entrypoint.sh` 的權限處理
- 注意：voicebox 支援的 **模型權重** 各自有授權（例如 OmniVoice 是 CC-BY-NC，UI 有特別標示，見 `EngineModelSelector.tsx:31, 43`）。這提醒我們：模型卡片也應該顯示權重授權，並標示「非商用」。
