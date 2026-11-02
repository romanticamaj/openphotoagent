# caption 文字向量（R2）選型比較（2026-11-02）

> 以獨立 sub-agent 調查。huggingface.co 與 arxiv.org 在雲端 session 被擋，數字取自搜尋摘要；無法確認的標【未驗證】。決策見 ADR-0014。

| | Qwen3-Embedding-0.6B | BAAI bge-m3 | multilingual-e5-small | SigLIP2 文字塔（已內建） |
|---|---|---|---|---|
| 授權 | Apache-2.0 | MIT【未驗證】 | MIT【未驗證】 | Apache-2.0 |
| MMTEB 多語平均 | 64.33 | 59.56 | 【未驗證】 | 不是為文字對文字檢索設計 |
| MMTEB 檢索 | ≈ 64.6【未驗證】 | 54.60 | 【未驗證】 | — |
| C-MTEB 平均／檢索 | 66.33／71.03 | 【未驗證】 | 【未驗證】 | — |
| 大小 | 0.6B；GGUF Q8_0 ≈ 640 MB；ONNX int8 ≈ 0.5 GB | 568M；官方 ONNX fp32 2.27 GB，int8 只有社群版 | ≈ 118M；int8 ≈ 120 MB【未驗證】 | 0 |
| 短文字 CPU 延遲 | ≈ 30–80 ms【未驗證】 | ≈ 30–80 ms【未驗證】 | ≈ 5–10 ms【未驗證】 | ≈ R1 查詢成本 |
| 維度 | 1024（MRL 可截至 32） | 1024 | 384 | 1152【未驗證】 |
| 前綴 | 查詢需 `Instruct:…\nQuery:` | 無 | `query:`／`passage:` | 無 |
| 繁中斷詞 | byte-level BPE，無 UNK | XLM-R SentencePiece | XLM-R SentencePiece | Gemma SentencePiece |

- 三個文字模型都 ≤ 1024 維，pgvector 0.6 的 `vector` 就能存（HNSW 上限 2000 維），不需要 halfvec。
- caption ≤ 200 字，context 長度不是區分點。
- C-MTEB 是簡體中文；**沒有任何候選有公開的繁中檢索分數**，要在 nightly 自己量。

## nightly／真機要加的量測
1. 約 200 題的繁中／英文／跨語言查詢，對 VLM 產生的 caption 量 recall@10、nDCG@10（只有 R2，以及 RRF 融合後）。
2. 單一查詢延遲 p50／p95：參考 CPU、DirectML（5070 Ti）、CoreML（M2），對照 05 §9 的預算（GPU < 50 ms、CPU < 300 ms）。
3. 回填吞吐（每秒 caption 數），供 reindex Operation 的 ETA 使用。
4. 量化漂移：int8／Q8_0 對 fp32 的 cosine 與 recall，門檻例如 cosine ≥ 0.99、recall 降幅 ≤ 1 點。
5. GGUF 一致性：llama.cpp 的輸出要等於參考實作（EOS、last-token pooling）。
6. MRL 截斷：512、768、1024 維的 recall。
7. 記憶體：16 GB 機器上與 VLM 並載、Mac 8 GB 序列載入時的峰值。
