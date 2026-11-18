# M0-S5 — 人臉模型授權審查（文件審查，不是程式）

審查日期：2026-11-01（雲端 session，透過 git 讀取上游 repo；Hugging Face 被網路政策擋住）。

| 模型 | 用途 | 上游 | 授權（上游檔案） | 版本／sha256（LFS oid） | 可隨 Apache-2.0 安裝包散布？ |
|---|---|---|---|---|---|
| YuNet | 人臉偵測 | `opencv/opencv_zoo` `models/face_detection_yunet`（commit `48b10fa5`） | **MIT**（`LICENSE`，Copyright 2020 Shiqi Yu；README：目錄內所有檔案 MIT） | `face_detection_yunet_2023mar.onnx` `8f2383e4…2552fa4`（232,589 B）；新版 `2026may` `ebafce4e…22f0f0` | ✅ 可（附 MIT 授權文字於 NOTICE） |
| SFace | CPU fallback 辨識 | 同上 `models/face_recognition_sface` | **Apache-2.0** | `face_recognition_sface_2021dec.onnx` `0ba9fbfa…c34e79`（38.7 MB） | ✅ 可（38.7 MB，建議做成 face pack 而非放進安裝包） |
| eDifFIQA(T) | 人臉品質 | 同上 `models/face_image_quality_assessment_ediffiqa`；原始 repo `LSIbabnikz/eDifFIQA`（commit `26019315`，程式碼 MIT） | **CC-BY-4.0**（opencv_zoo 目錄） | `ediffiqa_tiny_jun2024.onnx` `9426c899…bfc6a`（7.3 MB） | ✅ 可，但**必須署名**（NOTICE + About 頁） |
| AuraFace v1 | 預設辨識 | Hugging Face `fal/AuraFace-v1`（commit `af6d057c`） | **Apache-2.0**（`LICENSE.md` 是未填寫的範本：`Copyright [yyyy] [name of copyright owner]`；model card `license: apache-2.0`） | `glintr100.onnx` `a7933ea5…5c60`（260,694,151 B，float32，輸入 `data` [N,3,112,112]，輸出 512 維） | ⚠️ **只有 `glintr100.onnx`**，且 248.6 MiB 放不進 200 MB 安裝包；同 repo 其他 4 個檔案不可用（見下） |
| InsightFace buffalo_l 等 | 進階（選用） | `deepinsight/insightface` | 預訓練權重**非商用** | — | ❌ 不得打包，只能使用者同意後下載（CLAUDE.md §3） |

## AuraFace 審查（2026-11-05，可連 HF 的參考機）

- repo 有 5 個 ONNX。`glintr100.onnx` 是 AuraFace 本體；其餘 4 個與 InsightFace 的 `antelopev2`／`buffalo_l` **逐位元相同**（sha256 與公開鏡像比對）：`scrfd_10g_bnkps.onnx`（= `det_10g.onnx`）、`genderage.onnx`、`2d106det.onnx`、`1k3d68.onnx`。InsightFace 預訓練權重是非商用，repo 的 Apache-2.0 標示無法涵蓋這 4 個檔案，**不得使用或隨產品散布**。
- `glintr100.onnx` 檔名與架構和 InsightFace 的 `glintr100.onnx` 相同，但權重不同（sha256 `4ab1d643…` 不同；同形狀 tensor 最佳相關係數平均 0.016、最大 0.10；相同輸入的 embedding cosine 約 0）。沒有看到由 InsightFace 權重微調的跡象。這只排除「微調」，不證明訓練資料的來源。
- model card 只說訓練資料是「commercially and publicly available data sources」，沒有列出資料集，也沒有署名的版權人。是否足以散布仍屬法律判斷（BLOCKERS）。

## 未完成部分

- **TAR@FAR=0.1%**：需要授權允許的人臉資料集（09 Q-T1）→ 待使用者決定資料來源（BLOCKERS）。
- **訓練資料授權的法律意見**：模型權重以 MIT／Apache 釋出，但訓練資料集（例如 MS-Celeb 衍生集）的授權是否影響散布，需要有經驗的人審閱（BLOCKERS，法律意見）。
