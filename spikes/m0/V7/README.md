# M0-V7 — ETA 涵蓋率（pending-hardware，需要切片 6 的 Operation／ETA）

在參考機 1（5070 Ti）與 3（M2）：對「掃描 1,000 張」「依內容分類 1,000 張」「下載 SigLIP2」各跑 3 次，之後
`opagent ops export-calibration > cal.json`，再 `uv run python spikes/m0/V7/run.py --calibration cal.json --machine <名稱>`。
通過：80% 區間涵蓋率 ≥ 70%。不通過：調整 06 §5.5.5 的 δ 與 n0。雲端的模擬涵蓋率另由 ETA-016（FakeClock + lognormal）驗證。
