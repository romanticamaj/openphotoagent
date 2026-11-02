# Windows ML 取代 DirectML 的評估（WINML-001，2026-11-02）

> 背景：08 §3.1 記錄 ONNX Runtime 的 DirectML EP 官方狀態是「sustained engineering」（仍支援，但新功能轉往 Windows ML）。本文件評估是否在 Windows 版把小模型（SigLIP2、人臉、caption 文字向量）的執行從 ORT＋DirectML EP 換成 Windows ML。需要 Windows 實機的項目標 **pending-hardware**；【未驗證】是撰寫時無法在雲端確認的資訊。

| 面向 | ORT＋DirectML EP（目前） | Windows ML |
|---|---|---|
| 散布方式 | `onnxruntime-directml` wheel 隨安裝包（約 20–30 MB，08 §4.1） | 系統元件（Windows App SDK 的 Windows ML），由作業系統下載與更新 EP【未驗證：最低版本與是否需要 Windows App SDK runtime】 |
| EP 選擇 | 固定 DirectML（GPU 廠商無關） | 自動挑選（NPU、GPU 廠商 EP 如 TensorRT-RTX／OpenVINO／QNN，或 DirectML 退路）【未驗證：Python 綁定的可用性】 |
| 安裝包大小 | 內建 | 可能減少 20–30 MB（EP 由系統提供） |
| 最低 Windows 版本 | Windows 10 1903+ | Windows 11 24H2【未驗證】；Windows 10 不支援 → 仍需 DirectML 退路 |
| 數值一致性 | 已有 INF-CONF 與快取鍵（numerics_epoch） | EP 由系統更新，**數值可能在 app 不知情時改變** → 需要把系統 EP 版本納入 fingerprint，否則違反不變量 7 的快取前提 |
| Python 支援 | 成熟（onnxruntime） | 主要面向 C++/C#（WinRT）；我們的 worker 是 Python【未驗證：官方 Python 套件】 |

## 建議
- **v0.4 不切換**。原因：Python 支援不確定；最低 Windows 版本會排除 Windows 10；系統更新 EP 會讓推論快取鍵不穩定（不變量 7）。
- 保留觀察：當 Windows ML 提供穩定的 Python API，且可以查詢 EP 版本放進 fingerprint 時，再以「進階選項」加入；DirectML 維持預設與退路。
- **pending-hardware**（在使用者的 Windows 11＋RTX 5070 Ti 上）：
  1. 以同一個 SigLIP2 ONNX 比較 DirectML 與 Windows ML 自動選擇 EP 的延遲與吞吐。
  2. 比較兩者輸出向量的 cosine（目標 ≥ 0.999）。
  3. 記錄系統 EP 版本是否可程式化取得。
