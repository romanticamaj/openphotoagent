"""Caption text vectors on the llama.cpp the app already ships (R2, ADR-0014): ``llama-server --embedding --pooling
last`` serving Qwen3-Embedding. Captions are embedded as they are; queries carry the model's instruction prefix, as
its card prescribes for retrieval. Vectors come back L2-normalised, so cosine is a dot product."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

import httpx
import numpy as np

from .llamacpp import LlamaCppServerAdapter

BATCH = 32


class LlamaCppEmbedAdapter(LlamaCppServerAdapter):
    adapter_key = "llama.cpp-embed"

    def __init__(
        self,
        command: list[str],
        model_file: str,
        *,
        query_prefix: str = "",
        fingerprint: str | None = None,
        has_gpu: bool = False,
        extra_args: list[str] | None = None,
    ) -> None:
        super().__init__(
            command,
            model_file,
            None,
            parallel=4,
            ctx_per_slot=512,  # captions are a sentence or two
            fingerprint=fingerprint,
            has_gpu=has_gpu,
            extra_args=["--embedding", "--pooling", "last", "-ub", "2048", *(extra_args or [])],
        )
        self.query_prefix = query_prefix

    async def _embeddings(self, texts: Sequence[str]) -> np.ndarray:
        out: list[list[float]] = []
        async with httpx.AsyncClient(timeout=120.0) as c:
            for i in range(0, len(texts), BATCH):
                r = await c.post(self.base_url + "/v1/embeddings", json={"input": list(texts[i : i + BATCH])})
                r.raise_for_status()
                data: list[dict[str, Any]] = sorted(r.json()["data"], key=lambda d: d["index"])
                out += [d["embedding"] for d in data]
        v = np.asarray(out, dtype=np.float32)
        n = np.linalg.norm(v, axis=1, keepdims=True)
        return v / np.where(n == 0, 1.0, n)

    async def embed_texts(self, texts: Sequence[str]) -> np.ndarray:
        """Documents: captions as they are."""
        return await self._embeddings(texts)

    async def embed_queries(self, texts: Sequence[str]) -> np.ndarray:
        """What someone typed into search, with the retrieval instruction."""
        return await self._embeddings([self.query_prefix + t for t in texts])
