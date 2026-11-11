"""OpenAI-compatible chat adapter (03 §2.4): llama-server, Ollama /v1, LM Studio, vLLM, external endpoints.

Images arrive as in-memory bytes (``ImageInput.data``) and are sent as base64 data URLs; the adapter never sees
file paths (INF-CONF-011). Output goes through the fused parser (json-repair → one temperature-0 retry).
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
from typing import Any

import httpx
from opagent_core.operation import NullReporter, OperationReporter
from opagent_inference.fused import CategoryMap, OutputParseError, category_probability, fused_schema, parse_fused
from opagent_inference.prompts import CaptionConfig, render_answer_prompt, render_fused_prompt
from opagent_inference.types import (
    BackendInfo,
    BatchRequest,
    HealthStatus,
    ImageInput,
    InferenceError,
    ItemOutput,
    LoadOptions,
    ModelUnavailable,
    PreferredInput,
)

OOM_MARKERS = ("out of memory", "failed to allocate", "cudamalloc", "oom")
CONF = {"high": 0.9, "medium": 0.7, "low": 0.5}


def map_http_error(status: int, message: str) -> InferenceError:
    low = message.lower()
    if any(m in low for m in OOM_MARKERS):
        return InferenceError("OOM", True, message)
    if status in (408, 504) or "timed out" in low:
        return InferenceError("TIMEOUT", True, message)
    if 400 <= status < 500:
        return InferenceError("BAD_INPUT", False, message)
    return InferenceError("RUNTIME", True, message)


def check_items(items: list[Any]) -> list[ImageInput]:
    for it in items:
        if not isinstance(it, ImageInput) or not isinstance(it.data, bytes | bytearray):
            raise TypeError("adapters accept ImageInput with in-memory bytes only (never paths)")
    return items


class OpenAICompatAdapter:
    adapter_key = "openai-compatible"
    # the model's high/medium/low said "high" for 135 photos out of 135: the fallback when a runtime has no logprobs
    confidence_source = "self_report"

    def __init__(
        self,
        base_url: str,
        model: str = "local",
        fingerprint: str | None = None,
        max_concurrency: int = 4,
        structured_output: str = "json_schema",
        categories: list[str] | None = None,
        caption: CaptionConfig | None = None,
        has_gpu: bool = False,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.fingerprint = fingerprint or "oai-" + hashlib.sha256(f"{base_url}|{model}".encode()).hexdigest()[:12]
        self.max_concurrency = max_concurrency
        self.structured_output = structured_output
        self.categories = categories or ["other"]
        self.caption = caption or CaptionConfig()
        self.has_gpu = has_gpu
        self.loaded = False

    # ---- lifecycle ----
    def describe(self) -> BackendInfo:
        return BackendInfo(
            adapter_key=self.adapter_key,
            model_fingerprint=self.fingerprint,
            task_types=("classify", "caption", "tag"),
            preferred_input=PreferredInput(max_side=1024, format="jpeg"),
            max_batch=self.max_concurrency,
            est_vram_mb=0,
            supports_structured_output=self.structured_output != "none",
        )

    async def load(self, opts: LoadOptions, reporter: OperationReporter | None = None) -> None:
        rep = reporter or NullReporter()
        rep.start("model.load", 1, "steps")
        rep.phase("connect")
        if not await self._ping():
            rep.finish("failed")
            raise ModelUnavailable(f"endpoint not reachable: {self.base_url}")
        self.loaded = True
        rep.progress(1, 1)
        rep.finish("succeeded")

    async def unload(self) -> None:
        self.loaded = False

    async def health(self) -> HealthStatus:
        return HealthStatus(ok=self.loaded and await self._ping(), vram_used_mb=0)

    async def _ping(self) -> bool:
        try:
            async with httpx.AsyncClient(timeout=2.0) as c:
                r = await c.get(self.base_url + "/health")
                return r.status_code == 200
        except httpx.HTTPError:
            return False

    # ---- inference ----
    def _payload(self, req: BatchRequest, item: ImageInput, cats: CategoryMap, retry: bool) -> dict[str, Any]:
        prompt = render_fused_prompt(
            cats,
            self.caption,
            descriptions=req.variables.get("descriptions"),
            question=req.variables.get("question") if req.task_type == "classify" else None,
        )
        url = "data:image/jpeg;base64," + base64.b64encode(item.data).decode()
        gp = dict(req.generation_params)
        body: dict[str, Any] = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": prompt.system},
                {
                    "role": "user",
                    "content": [
                        {"type": "image_url", "image_url": {"url": url}},
                        {"type": "text", "text": prompt.user},
                    ],
                },
            ],
            "temperature": 0 if retry else gp.get("temperature", 0.2),
            "seed": gp.get("seed", 0),
            "max_tokens": gp.get("max_tokens", 384),
            "logprobs": True,
            "top_logprobs": 20,
        }
        if retry:
            body["messages"].append({"role": "user", "content": "只輸出 JSON。"})
        schema = req.output_schema or fused_schema(cats)
        if self.structured_output == "json_schema":
            body["response_format"] = {"type": "json_schema", "json_schema": {"name": "fused", "schema": schema}}
        elif self.structured_output == "json_object":
            body["response_format"] = {"type": "json_object"}
        elif self.structured_output == "guided_json":  # vLLM's own structured-output parameter
            body["guided_json"] = schema
        return body

    async def _post(self, client: httpx.AsyncClient, body: dict[str, Any]) -> tuple[str, list[dict[str, Any]] | None]:
        try:
            r = await client.post(self.base_url + "/v1/chat/completions", json=body)
        except (httpx.ConnectError, httpx.RemoteProtocolError, httpx.ReadError) as e:
            self._on_connection_lost()
            raise InferenceError("RUNTIME", True, f"runtime connection lost: {e}") from None
        if r.status_code != 200:
            try:
                msg = str(r.json().get("error", {}).get("message", r.text))
            except ValueError:
                msg = r.text
            raise map_http_error(r.status_code, msg)
        choice = r.json()["choices"][0]
        return str(choice["message"]["content"]), (choice.get("logprobs") or {}).get("content")

    def _on_connection_lost(self) -> None:
        return None

    def _answer_payload(self, req: BatchRequest, item: ImageInput) -> dict[str, Any]:
        prompt = render_answer_prompt(str(req.variables["question"]))
        url = "data:image/jpeg;base64," + base64.b64encode(item.data).decode()
        gp = dict(req.generation_params)
        return {
            "model": self.model,
            "messages": [
                {"role": "system", "content": prompt.system},
                {
                    "role": "user",
                    "content": [
                        {"type": "image_url", "image_url": {"url": url}},
                        {"type": "text", "text": prompt.user},
                    ],
                },
            ],
            "temperature": gp.get("temperature", 0.2),
            "seed": gp.get("seed", 0),
            "max_tokens": gp.get("max_tokens", 256),
        }

    async def run_batch(self, req: BatchRequest) -> list[ItemOutput]:
        items = check_items(list(req.items))
        if not items:
            return []
        if not self.loaded:
            raise ModelUnavailable(f"{self.adapter_key} not loaded")
        if req.task_type == "caption" and req.variables.get("question"):
            # a question about the photo: answered in words, not the caption JSON (INF-ASK-001)
            async with httpx.AsyncClient(timeout=httpx.Timeout(req.deadline_s + 5)) as client:
                outs = []
                for it in items:
                    text, _ = await self._post(client, self._answer_payload(req, it))
                    outs.append(ItemOutput(item_id=it.item_id, caption=text.strip(), raw={"content": text}, usage={}))
                return outs
        cats = CategoryMap(keys=list(req.variables.get("categories") or self.categories))
        if "other" not in cats.keys:
            cats.keys.append("other")
        sem = asyncio.Semaphore(self.max_concurrency)

        async with httpx.AsyncClient(timeout=httpx.Timeout(req.deadline_s + 5)) as client:

            async def one(it: ImageInput) -> ItemOutput:
                async with sem:
                    raw, logprobs = await self._post(client, self._payload(req, it, cats, retry=False))
                    retry_raw: list[str] = []

                    def retry() -> str:
                        raise _NeedRetry()

                    try:
                        res = parse_fused(raw, cats, retry=retry)
                    except _NeedRetry:
                        again, logprobs = await self._post(client, self._payload(req, it, cats, retry=True))
                        retry_raw.append(again)
                        try:
                            res = parse_fused(retry_raw[0], cats)
                            res.retried = True
                        except OutputParseError as e:
                            raise InferenceError("BAD_INPUT", False, f"OutputParseError: {e}") from None
                    measured = category_probability(logprobs, res.category or "", cats.keys)
                    return ItemOutput(
                        item_id=it.item_id,
                        caption=res.caption,
                        tags=[(t, 1.0) for t in res.tags],
                        category=res.category,
                        confidence=measured if measured is not None else CONF.get(res.confidence or ""),
                        raw={
                            "content": raw,
                            "repaired": res.repaired,
                            "retried": res.retried,
                            "flags": res.flags,
                            "contains_image_text": res.contains_image_text,
                            "confidence_source": "logprobs" if measured is not None else self.confidence_source,
                        },
                        usage={},
                    )

            tasks = [asyncio.create_task(one(it)) for it in items]
            try:
                return list(await asyncio.wait_for(asyncio.gather(*tasks), timeout=req.deadline_s))
            except TimeoutError:
                for t in tasks:
                    t.cancel()
                await asyncio.gather(*tasks, return_exceptions=True)
                raise InferenceError("TIMEOUT", True, f"deadline {req.deadline_s}s exceeded") from None
            except BaseException:
                for t in tasks:
                    t.cancel()
                await asyncio.gather(*tasks, return_exceptions=True)
                raise


class _NeedRetry(Exception):
    pass
