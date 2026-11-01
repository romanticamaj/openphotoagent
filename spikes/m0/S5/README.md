# M0-S5 — 人臉模型授權審查（文件審查，不是程式）

審查日期：2026-11-01（雲端 session，透過 git 讀取上游 repo；Hugging Face 被網路政策擋住）。

| 模型 | 用途 | 上游 | 授權（上游檔案） | 版本／sha256（LFS oid） | 可隨 Apache-2.0 安裝包散布？ |
|---|---|---|---|---|---|
| YuNet | 人臉偵測 | `opencv/opencv_zoo` `models/face_detection_yunet`（commit `48b10fa5`） | **MIT**（`LICENSE`，Copyright 2020 Shiqi Yu；README：目錄內所有檔案 MIT） | `face_detection_yunet_2023mar.onnx` `8f2383e4…2552fa4`（232,589 B）；新版 `2026may` `ebafce4e…22f0f0` | ✅ 可（附 MIT 授權文字於 NOTICE） |
| SFace | CPU fallback 辨識 | 同上 `models/face_recognition_sface` | **Apache-2.0** | `face_recognition_sface_2021dec.onnx` `0ba9fbfa…c34e79`（38.7 MB） | ✅ 可（38.7 MB，建議做成 face pack 而非放進安裝包） |
| eDifFIQA(T) | 人臉品質 | 同上 `models/face_image_quality_assessment_ediffiqa`；原始 repo `LSIbabnikz/eDifFIQA`（commit `26019315`，程式碼 MIT） | **CC-BY-4.0**（opencv_zoo 目錄） | `ediffiqa_tiny_jun2024.onnx` `9426c899…bfc6a`（7.3 MB） | ✅ 可，但**必須署名**（NOTICE + About 頁） |
| AuraFace v1 | 預設辨識 | Hugging Face `fal/AuraFace-v1` | 規格記載 Apache-2.0 —— **本次無法驗證**（HF 403） | 未取得 | ⏳ pending：在可連 HF 的機器確認 LICENSE 與檔案 sha |
| InsightFace buffalo_l 等 | 進階（選用） | `deepinsight/insightface` | 預訓練權重**非商用** | — | ❌ 不得打包，只能使用者同意後下載（CLAUDE.md §3） |

## 未完成部分

- **TAR@FAR=0.1%**：需要授權允許的人臉資料集（09 Q-T1）→ 待使用者決定資料來源（BLOCKERS）。
- **訓練資料授權的法律意見**：模型權重以 MIT／Apache 釋出，但訓練資料集（例如 MS-Celeb 衍生集）的授權是否影響散布，需要有經驗的人審閱（BLOCKERS，法律意見）。
