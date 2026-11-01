# 01 — Prior Art 調查與命名評估

> 撰寫日期：2026-11-01｜角色：Prior-art 研究員
> 資料來源：GitHub Search API（`gh api search/repositories`，2026-11-01 查詢）、PyPI / npm registry、官方文件（WebSearch）。
> Stars、授權、最後 push 日期皆由 GitHub Search API 直接取得；**功能欄位**是依專案描述、官方文件與既有知識整理，未逐一實測的地方標「未驗證」。
>
> **後續決定（審查補充，見 [00 §8](00-master-plan.md)）**：正式名稱採 **OpenPhotoAgent**（不是本文推薦的 Open Photo Labeler）；CLI 定為 **`opagent`**（不是本文的 `opl`），環境變數前綴 `OPAGENT_`；授權 Apache-2.0。本文保留 2026-11-01 調查當下的原始內容，作為紀錄。

---

## 0. TL;DR

- 市面上**沒有**同時具備「精美 Web 控制台 + 可換模型（VLM / tagger / embedding）+ 三種任務類型（Tagging / Classification / Caption）+ 來源 → 分類規則 → 輸出資料夾搬移 + 雲端控制台 / 地端 GPU worker 分離」的開源專案。
- 最接近的有四類：
  1. **自架相簿**（Immich、PhotoPrism）：AI 很強，但它們「管理圖庫」，不「整理檔案到資料夾」。
  2. **資料集 caption 工具**（TagGUI、qapyq、PixlStash）：可選模型、品質好，但都是桌面 / 單機工具，沒有資料夾分類搬移。
  3. **AI 檔案整理器**（ai-file-sorter、Local-File-Organizer、LlamaFS、a-eye）：會搬檔，但模型與任務是寫死的，介面也很簡單。
  4. **標註平台**（Label Studio、CVAT、X-AnyLabeling）：適合人工審核與匯出，對「整理個人照片」來說太重。
- 建議：**自建（build）核心**，不要 fork 任何大型專案；**整合**的部分做 Immich（External Library + API 回寫 tag/description）、PhotoPrism（共用 Ollama 設定）、Label Studio（匯出 JSON / 接 ML backend）。
- 命名：推薦 **Open Photo Labeler**（repo `open-photo-labeler`、套件 `open-photo-labeler`，CLI `opl`）。GitHub、PyPI、npm 目前都沒人用；但短名 `opl` 在 PyPI / npm 已被佔用，CLI 指令可以叫 `opl`，套件不能用這個名字。

---

## 1. 相似專案總表

圖例：✅ 支援｜🟡 部分 / 有限｜❌ 不支援｜？ 未驗證

### 1.1 自架相簿（含 AI）

| 專案 | Stars | 授權 | 最後活躍 | 技術棧 | 選模型 | 任務類型 | 資料夾搬移 | GUI | 主要缺口（對我們） |
|---|---|---|---|---|---|---|---|---|---|
| [immich-app/immich](https://github.com/immich-app/immich) | ~117.9k | AGPL-3.0 | 2026-11 | TypeScript (NestJS) + Svelte + Python ML | 🟡 CLIP / 臉部模型可換（HF 上的 Immich 模型清單） | 🟡 CLIP 語意搜尋、臉部；沒有 VLM caption / 自訂分類 | ❌（Storage Template 只依日期 / 相簿重新命名） | ✅ 精美 | 不做 VLM caption；不能用自訂類別分類；workflow / plugin 系統還在開發（預計 v3） |
| [photoprism/photoprism](https://github.com/photoprism/photoprism) | ~40.4k | AGPL-3.0（另有商業版） | 2026-10 | Go + Vue + TensorFlow | ✅ `vision.yml` 可接 Ollama / OpenAI（Gemma 4、Qwen3-VL） | ✅ labels、caption、face、nsfw | ❌（索引原地檔案，不搬檔） | ✅ | 定位是「看照片」；沒有自訂 enum 分類 → 輸出資料夾；部分功能在付費版 |
| [LibrePhotos/librephotos](https://github.com/LibrePhotos/librephotos) | ~8.1k | MIT | 2026-11 | Python (Django) + React | 🟡（caption 模型可選，細節未驗證） | 🟡 場景分類、caption、臉部 | ❌ | ✅ | 模型比較舊；沒有規則式分類搬移 |
| [ente/ente](https://github.com/ente/ente) | ~29.8k | AGPL-3.0 | 2026-10 | Dart (Flutter) + Go | ❌（裝置端固定的 CLIP / 臉部模型） | 🟡 語意搜尋、臉部 | ❌ | ✅ | E2EE 雲端相簿，模型與任務都不能設定 |
| [Webreaper/Damselfly](https://github.com/Webreaper/Damselfly) | ~1.8k | GPL-3.0 | 2026-09 | C# (.NET / Blazor) | ❌ | 🟡 物件偵測、臉部、關鍵字寫回 EXIF | ❌ | ✅ | .NET 生態；沒有 VLM |
| [bpatrik/pigallery2](https://github.com/bpatrik/pigallery2) | ~2.3k | MIT | 2026-11 | TypeScript | ❌ | ❌（讀 EXIF/XMP 關鍵字） | ❌ | ✅ | 目錄優先的相簿，可以當我們輸出資料夾的瀏覽前端；有 extension 機制 |
| [xemle/home-gallery](https://github.com/xemle/home-gallery) | ~1.2k | MIT | 2026-10 | JavaScript | ❌ | 🟡 物件 / 臉部 / 相似度 | ❌ | ✅ | 同上 |

### 1.2 資料集 Caption / Tag 工具

| 專案 | Stars | 授權 | 最後活躍 | 技術棧 | 選模型 | 任務類型 | 資料夾搬移 | GUI | 主要缺口 |
|---|---|---|---|---|---|---|---|---|---|
| [jhc13/taggui](https://github.com/jhc13/taggui) | ~1.4k | GPL-3.0 | 2025-10 | Python + PySide6 + transformers | ✅ 很多 VLM / WD tagger | Caption、Tag | ❌ | ✅ 桌面 | 2025-10 之後沒有 push（活躍度下降）；只能單機；輸出 `.txt` sidecar |
| [FennelFetish/qapyq](https://github.com/FennelFetish/qapyq) | ~160 | AGPL-3.0 | 2026-10 | Python + Qt + llama.cpp / transformers | ✅ GGUF / VLM / WD | Caption、Tag、裁切、遮罩、排序 | 🟡 有 batch 檔案操作（範圍未驗證） | ✅ 桌面 | 偏向 LoRA 訓練資料集；不是 Web；沒有遠端 worker |
| [Pikselkroken/pixlstash](https://github.com/Pikselkroken/pixlstash) | ~96 | GPL-3.0 | 2026-11 | Python | ✅ caption plugin 架構（[pixlstash-plugins](https://github.com/Pikselkroken/pixlstash-plugins)） | Caption、搜尋 | ？ | ✅（Web，未驗證） | 新專案、社群小；重點是「在大型圖庫裡找圖」；有 [ComfyUI 節點](https://github.com/Pikselkroken/ComfyUI-PixlStash) 與 LM Studio plugin |
| [fpgaminer/joycaption](https://github.com/fpgaminer/joycaption) | ~1.3k | Apache-2.0 | 2026-02 | Python / notebook | —（本身就是模型） | Caption | ❌ | 🟡 HF Space / ComfyUI 節點 | 是模型不是產品；可以當我們的一個 backend |
| [1038lab/ComfyUI-JoyCaption](https://github.com/1038lab/ComfyUI-JoyCaption) | ~330 | GPL-3.0 | 2025-12 | Python (ComfyUI node) | 🟡 | Caption | ❌ | 🟡 節點圖 | 要依附 ComfyUI；不適合一般使用者 |
| [pythongosssss/ComfyUI-WD14-Tagger](https://github.com/pythongosssss/ComfyUI-WD14-Tagger) | ~1.3k | MIT | 2025-07 | Python (ONNX) | ✅ WD 系列 | Tag（booru） | ❌ | 🟡 節點圖 | 只有動漫 tag |
| [MieMieeeee/ComfyUI-CaptionThis](https://github.com/MieMieeeee/ComfyUI-CaptionThis) | ~135 | GPL-3.0 | 2026-08 | Python | ✅ Janus Pro / Florence-2 等 | Caption（可整個目錄） | ❌ | 🟡 節點圖 | 同上 |
| [Nenotriple/img-txt_viewer](https://github.com/Nenotriple/img-txt_viewer) | ~63 | 未標示 | 2025-10 | Python (Tk) | 🟡 | 手動 + 批次 tag 編輯 | ❌ | ✅ 只支援 Windows | 授權不明 |

### 1.3 AI 檔案 / 照片整理器（最接近「搬到資料夾」）

| 專案 | Stars | 授權 | 最後活躍 | 技術棧 | 選模型 | 任務類型 | 資料夾搬移 | GUI | 主要缺口 |
|---|---|---|---|---|---|---|---|---|---|
| [hyperfield/ai-file-sorter](https://github.com/hyperfield/ai-file-sorter) | ~1.9k | AGPL-3.0 | 2026-11 | C++ (Qt) + llama.cpp / OpenAI | ✅ 本地 LLM 或 API | 分類 + 重新命名（以文字為主；圖片的處理深度未驗證） | ✅ | ✅ 桌面 | 通用檔案，不是照片專用；沒有 caption / tag 輸出；沒有 Web / 遠端 worker |
| [QiuYannnn/Local-File-Organizer](https://github.com/QiuYannnn/Local-File-Organizer) | ~3.4k | MIT | **2024-10（停滯）** | Python + Nexa SDK（LLaVA / Llama） | 🟡 寫死 | 依內容 / 日期 / 類型分類 | ✅ | ❌ CLI | 停止維護；模型舊；沒有 GUI |
| [iyaja/llama-fs](https://github.com/iyaja/llama-fs) | ~5.8k | MIT | 2025-08 | TypeScript (Electron) + Python + Groq / Ollama | 🟡 | 依內容自動建立資料夾結構 | ✅ | ✅ 簡易 | 偏 demo；不能自訂類別；以文件為主 |
| [tfeldmann/organize](https://github.com/tfeldmann/organize) | ~3.2k | MIT | 2026-10 | Python（YAML 規則） | ❌ 沒有 AI | 規則式（EXIF、副檔名、正則） | ✅ | ❌ CLI | 「Hazel-like」規則引擎，沒有 AI；**它的規則語法很值得參考** |
| [SpaceinvaderOne/a-eye](https://github.com/SpaceinvaderOne/a-eye) | ~114 | 未標示 | 2026-09 | Python + Ollama | ✅ Ollama 視覺模型 | 描述、tag（寫入 metadata） | 🟡（未驗證） | ✅ Web（自架） | **概念最接近**；但授權未標示、規模小，沒有分類 enum 或輸出資料夾規則（未驗證） |
| [ashesbloom/LocalLens](https://github.com/ashesbloom/LocalLens) | ~171 | AGPL-3.0 | 2026-10 | Python | ❌ | 臉部分群、依人 / 日期分類 | ✅ | ✅ 桌面 | 以臉部為主，沒有 VLM |
| [cdzombak/lychee-ai-organizer](https://github.com/cdzombak/lychee-ai-organizer) | ~13 | GPL-3.0 | 2026-04 | Go + Ollama | ✅ | 把照片分進 Lychee 相簿 | 🟡（Lychee 相簿） | ❌ | 綁定 Lychee |
| [TeunvandeBerg/sort_photos_lmstudio](https://github.com/TeunvandeBerg/sort_photos_lmstudio) | 1 | MIT | 2026-08 | Python + LM Studio | ✅ | 用 JSON 定義類別 → 分類 | ✅ | ❌ | 驗證了「使用者定義類別 + VLM + 搬檔」這個需求確實存在，只是做成小腳本 |
| [tychenjiajun/exif-ai](https://github.com/tychenjiajun/exif-ai) | ~28 | GPL-2.0 | 2026-10 | TypeScript CLI | ✅ OpenAI / Ollama / Gemini 等 | 把描述 / tag 寫進 EXIF | ❌ | ❌ | 可以借鏡 EXIF/XMP 寫回的做法 |
| Lightroom 外掛（[LrGeniusTagAI](https://github.com/LrGenius/LrGeniusTagAI)、[ai-keywords-lightroom](https://github.com/gibbonsr4/ai-keywords-lightroom)、[LrLexicon](https://github.com/jdpjamesp/LrLexicon)） | <10 | 各異 | 2026 | Lua | ✅ BYO AI | 關鍵字、caption | ❌ | 🟡 LR 內 | 要有 Adobe 授權；說明攝影師對 XMP keyword 的需求很強 |
| [cobanov/tasnif](https://github.com/cobanov/tasnif) | ~38 | MIT | 2026-10 | Python | ✅ ConvNeXt / DINOv2 / CLIP | 非監督分群 → 資料夾 | ✅ | ❌ | 可借用「embedding 分群」當作「探索類別」的輔助功能 |

> 「Sparrow」：GitHub 上找不到同名的照片整理開源專案（搜尋 `sparrow photo` 沒有相關結果）；市面上 Hazel 類工具多是商業軟體（Hazel、DropIt、File Juggler），未列入。

### 1.4 標註平台

| 專案 | Stars | 授權 | 最後活躍 | 技術棧 | 選模型 | 任務類型 | 資料夾搬移 | GUI | 主要缺口 |
|---|---|---|---|---|---|---|---|---|---|
| [HumanSignal/label-studio](https://github.com/HumanSignal/label-studio) | ~28.6k | Apache-2.0 | 2026-10 | Python (Django) + React | ✅ 透過 [ML backend](https://github.com/HumanSignal/label-studio-ml-backend)（~1.1k★） | 分類、caption（TextArea）、bbox 等任何任務 | ❌ | ✅ | 專業人工標註流程，對個人照片整理太重；沒有搬檔 |
| [cvat-ai/cvat](https://github.com/cvat-ai/cvat) | ~17.0k | MIT | 2026-10 | Python + React | ✅ Nuclio serverless 模型 | 偵測 / 分割 / tag | ❌ | ✅ | 以 CV 偵測 / 分割為主；部署複雜 |
| [CVHub520/X-AnyLabeling](https://github.com/CVHub520/X-AnyLabeling) | ~10.9k | GPL-3.0 | 2026-10 | Python + Qt + ONNX | ✅ 很多（YOLO、SAM、Florence-2、VLM） | 偵測、分割、caption、分類 | ❌ | ✅ 桌面 | 桌面單機；以 bbox / mask 為主 |
| [autodistill/autodistill](https://github.com/autodistill/autodistill) | ~2.8k | Apache-2.0 | 2026-09 | Python | ✅ 基礎模型 → 小模型 | 自動標註 + 蒸餾 | ❌ | ❌ | 函式庫；**「用大模型標、再蒸餾成小模型」的流程可以列入 roadmap** |

### 1.5 其他相關（參考）

| 專案 | Stars | 授權 | 說明 |
|---|---|---|---|
| [karakeep-app/karakeep](https://github.com/karakeep-app/karakeep) | ~30.2k | AGPL-3.0 | 書籤 / 圖片 AI 自動 tag（Ollama / OpenAI）；**「可換 AI provider + inference 設定」的 UX 很值得參考** |
| [stashapp/stash](https://github.com/stashapp/stash) | ~13.3k | AGPL-3.0 | 成人媒體整理；它的 plugin / scraper 架構常被拿來做 AI tagger |
| [jakedev796/immich-booru-tagger](https://github.com/jakedev796/immich-booru-tagger) | 16 | 未標示 | 用 WD-14 幫 Immich 照片打 tag，**證明「外部 worker + Immich API 回寫」可行** |
| [DIVISIO-AI/stag](https://github.com/DIVISIO-AI/stag) | ~159 | Apache-2.0 | AI 自動 tag 圖片（寫 XMP） |

共列出 30+ 個專案，其中核心比較對象 19 個。

---

## 2. 結論

### 2.1 缺口矩陣（核心需求 × 代表專案）

| 需求 | Immich | PhotoPrism | TagGUI / qapyq | ai-file-sorter | a-eye | Label Studio | **我們** |
|---|---|---|---|---|---|---|---|
| 精美 Web 控制台 | ✅ | ✅ | ❌ 桌面 | ❌ 桌面 | 🟡 | ✅ | ✅ |
| 使用者可選模型（VLM / tagger / embedding / API） | 🟡 | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ |
| 任務類型 Tag / Classify / Caption 可設定 | ❌ | 🟡 | 🟡 | 🟡 | 🟡 | ✅ | ✅ |
| 自訂類別（enum、structured output） | ❌ | ❌ | ❌ | 🟡 | ❌ | ✅ | ✅ |
| 來源 → 分類 → 輸出資料夾（copy/move/symlink、dry-run、undo） | ❌ | ❌ | ❌ | ✅ | ？ | ❌ | ✅ |
| 雲端控制台 + 地端 GPU worker | 🟡（可以分離 ML container） | 🟡（vision service） | ❌ | ❌ | ❌ | 🟡（ML backend） | ✅ |
| 人工審核低信心樣本 | ❌ | ❌ | ✅ | 🟡 預覽 | ？ | ✅ | ✅ |

### 2.2 差異化定位（一句話）

> **「照片版的 Hazel + Label Studio」：一個可自架的 Web 控制台，讓你挑模型、定義 Tag / Classification / Caption 任務與類別，在自己的 GPU 上批次跑完，再依結果把照片安全地整理進資料夾（附 sidecar / XMP 與可回復的搬移紀錄）。**

具體賣點：
1. **任務即設定（Task-as-config）**：任務 = 模型 + prompt/schema + 類別 enum + 路由規則。存成 YAML，可以分享、版本化。
2. **安全的檔案操作**：預設 dry-run 預覽 → copy/move/hardlink → 寫 manifest，可以一鍵 undo。這是相簿類專案刻意不做的事。
3. **混合 pipeline**：SigLIP2 粗分 → VLM 複核低信心樣本 → 人工審核佇列（參考 Label Studio 的 review 流程）。
4. **控制台 / worker 分離**：同一套程式可以 all-in-one，也可以拆成雲端控制台 + 地端 agent（照片不離開本機）。
5. **生態整合，不取代**：結果可回寫 Immich / PhotoPrism / XMP，也能匯出 Label Studio。

### 2.3 Build / Fork / 整合 建議

| 選項 | 評估 | 建議 |
|---|---|---|
| Fork Immich | AGPL、程式碼龐大（TS monorepo），核心理念是「圖庫」而不是「整理檔案」；plugin / workflow 系統（WASM manifest）還在開發，預計 v3 才發佈（[March 2026 recap](https://immich.app/blog/2026-march-recap)） | ❌ 不 fork。等 workflow API 穩定後再評估做 Immich plugin；短期改用 **REST API 整合**：讀 asset、把 tag / description 回寫（有 [immich-booru-tagger](https://github.com/jakedev796/immich-booru-tagger) 可參考），或把輸出資料夾掛成 External Library |
| Fork PhotoPrism | Go 寫成、AGPL 加上商業版雙授權，`vision.yml` 已支援 Ollama caption / labels（[文件](https://docs.photoprism.app/user-guide/ai/)） | ❌ 不 fork。可以借用它的 `vision.yml` 設計（Type / Model / Engine / Threshold）當作我們模型設定的 schema 參考 |
| Fork TagGUI / qapyq | GPL / AGPL 的 Qt 桌面程式，架構和 Web SaaS 不一樣 | ❌ 不 fork。可以參考它們的「模型 adapter 清單」與 prompt 預設 |
| Fork ai-file-sorter | AGPL、C++/Qt | ❌ 不 fork。可以參考它的「預覽 → 確認 → 搬移 → undo」UX |
| 整合 Label Studio | Apache-2.0，匯出格式穩定 | ✅ 做兩件事：(1) 匯出 Label Studio JSON（`choices` / `textarea` / `taxonomy`）；(2) 選配：把我們的 worker 包成 LS ML backend |
| **自建核心** | 沒有現成專案能滿足四個硬性需求 | ✅ **Build**：FastAPI + 任務佇列 + Web UI；inference adapter 層可以接 vLLM / llama.cpp / Ollama / transformers / OpenAI-compatible API |

授權建議：核心用 **Apache-2.0**（方便 Immich / Label Studio 生態採用，也不會和 AGPL 專案的 API 整合衝突）。只要不複製 AGPL / GPL 專案的程式碼，透過 API 互通就沒有授權問題。

### 2.4 可借用的元件 / repo

| 用途 | 元件 | 授權 | 說明 |
|---|---|---|---|
| 規則引擎語法 | [tfeldmann/organize](https://github.com/tfeldmann/organize) | MIT | YAML filters / actions（exif、regex、move、copy、rename）；可以參考語法，甚至直接當作函式庫依賴 |
| Label Studio 匯出 / ML backend | [label-studio-ml-backend](https://github.com/HumanSignal/label-studio-ml-backend) | Apache-2.0 | 提供 backend 範本 |
| Embedding 分群 / 類別探索 | [cobanov/tasnif](https://github.com/cobanov/tasnif) | MIT | 「還不知道要分哪幾類」時，先分群再命名 |
| WD tagger 推論 | [ComfyUI-WD14-Tagger](https://github.com/pythongosssss/ComfyUI-WD14-Tagger) | MIT | ONNX 推論與 tag 閾值處理的程式碼 |
| JoyCaption 推論 | [fpgaminer/joycaption](https://github.com/fpgaminer/joycaption) | Apache-2.0 | 官方推論範例與 prompt 模板 |
| 模型設定 schema | PhotoPrism `vision.yml` | （參考設計，不複製程式碼） | Type / Engine / Model / Threshold / Think |
| 自動標註 → 蒸餾 | [autodistill](https://github.com/autodistill/autodistill) | Apache-2.0 | 長期 roadmap：從使用者確認過的標註訓練小型分類器 |
| AI provider UX | [karakeep](https://github.com/karakeep-app/karakeep) | AGPL-3.0 | 只參考 UX，不複製程式碼 |
| 輸出瀏覽 | [pigallery2](https://github.com/bpatrik/pigallery2) | MIT | 目錄優先的相簿，可以當「輸出資料夾」的輕量瀏覽器 |
| EXIF/XMP 寫回 | exiftool / [exif-ai](https://github.com/tychenjiajun/exif-ai) | GPL-2.0（exif-ai） | 寫回建議直接呼叫 exiftool（Perl Artistic / GPL，以外部程式呼叫） |

---

## 3. 命名評估

檢查方式（2026-11-01）：
- GitHub repo：`gh api -X GET search/repositories -f q='<name> in:name'`
- GitHub user/org：`search/users q='<name> in:login'`，比對是否完全相同
- PyPI：`curl https://pypi.org/pypi/<name>/json`（404 = 沒人用；PyPI 會把 `-`、`_`、`.` 視為相同）
- npm：`curl https://registry.npmjs.org/<name>`（404 = 沒人用）

| 候選名 | GitHub 同名 repo | GH org/user 完全相同 | PyPI | npm | 可搜尋性 / 辨識度 | 評語 |
|---|---|---|---|---|---|---|
| **Open Photo Labeler** (`open-photo-labeler` / `openphotolabeler`) | 0 | 沒人用 | ✅ 可用 | ✅ 可用 | 高：名字直接說明用途，搜尋 "photo labeler" 只有 65 個零星 repo | **完全可用**；名字偏長，但很清楚。`labeler` 也呼應 Label Studio / AnyLabeling 生態 |
| Open Photo Organizer (`open-photo-organizer`) | 2（2017 年的空專案、無 star） | 沒人用 | ✅ | ✅ | 中：「organizer」很通用，容易和相簿管理軟體混淆 | 可用，但「organizer」會被誤認為 Immich 類產品 |
| OpenPhotoSort (`openphotosort`) | 2（BitfinityAB/OpenPhotoSort 0★、openPhotoSorter 0★） | 沒人用 | ✅ | ✅ | 中 | 已有同名 repo（雖然沒有 star），容易撞名 |
| PhotoLabeler (`photolabeler`) | 5（皆 0★） | 沒人用 | ✅ | ✅ | 中低：太泛用 | 可用，但沒有 "Open" 前綴，撞名機率高 |
| PhotoMind (`photomind`) | 23（多為 2026 年的 AI 照片專案） | **已被使用** | ✅ | ✅ | 低：已有多個 AI 照片專案用同名 | ❌ 不建議 |
| Lumen (`lumen`) | 22k+（Laravel Lumen 7.5k★ 等） | **已被使用** | ❌ 已被佔用 | ❌ 已被佔用 | 極低 | ❌ 不建議（`lumen-photos` 可用，但仍會被 Laravel Lumen 淹沒） |
| PhotoSieve（新提）(`photosieve`) | 4（含一個中文篩圖軟體，0★） | 沒人用 | ✅ | ✅ | 中：「sieve 篩選」意象好，但和既有中文篩圖工具同名 | 備選 |
| TagFold（新提）(`tagfold`) | 7（obsidian-tagfolder 352★ 名稱相近） | 沒人用 | ✅ | ✅ | 中：Tag + Folder 意象清楚 | 備選，和 Obsidian 外掛名稱接近 |
| SortLens（新提） | 5（皆 0★） | 沒人用 | ✅ | ❌ 已被佔用 | 中 | npm 已被佔用，不建議 |
| CLI 短名 `opl` | 6.6k+ | 已被使用 | ❌ | ❌ | — | 只能當 CLI 指令或縮寫，不能當套件名 |

### 推薦

**主名：Open Photo Labeler**
- repo：`open-photo-labeler`；PyPI：`open-photo-labeler`（import 名 `open_photo_labeler` 或 `opl`）；npm（前端 / SDK 用）：`open-photo-labeler` 或 scope `@openphotolabeler/*`；GitHub org：`openphotolabeler`。這些名稱目前**全部沒人使用**。
- 理由：
  1. 名稱直接描述用途（照片 + 標註），新使用者看名字就懂，SEO 好。
  2. `Labeler` 讓它落在「標註工具」的語意範圍，和 Immich / PhotoPrism 這類「相簿」區隔，符合我們「做標註與整理、不做圖庫」的定位。
  3. "Open" 前綴表明是開源、可自架。
  4. 三個平台都沒人用，也沒有知名專案同名，撞名風險低。
- 缺點與對策：名字長，口語上用縮寫 **OPL**、CLI 指令用 `opl`（只當執行檔名稱，不發佈同名套件）。
- 備選：**PhotoSieve**（意象強、比較短，但 GitHub 上有一個同名的中文篩圖工具）。

> 注意：以上可用性只是 2026-11-01 的快照；GitHub org、PyPI、npm 名稱都是先搶先贏，**決定後應盡快註冊**（建立 GitHub org、PyPI 先發一個 0.0.1 佔位版、npm scope）。商標沒有查（未驗證）。

---

## 4. 來源

- GitHub Search API 查詢結果（2026-11-01）：上表各 repo 連結
- Immich workflow / plugin 開發狀態：[Immich March 2026 recap](https://immich.app/blog/2026-march-recap)、[November 2025 recap](https://immich.app/blog/2025-november-recap)、[awesome-immich](https://github.com/tlwhittaker/awesome-immich)
- PhotoPrism AI / Ollama：[Using AI Models](https://docs.photoprism.app/user-guide/ai/)、[Ollama models](https://docs.photoprism.app/user-guide/ai/ollama-models/)、[Label generation](https://docs.photoprism.app/developer-guide/vision/label-generation)、[Vision service setup](https://docs.photoprism.app/developer-guide/vision/service/setup/)
- PyPI JSON API：`https://pypi.org/pypi/<name>/json`；npm registry：`https://registry.npmjs.org/<name>`
