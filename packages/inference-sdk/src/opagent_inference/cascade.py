"""Classification cascade: rules → embedding (SigLIP2 zero-shot + prototypes) → VLM review → decision
(03 §3.3, ADR-0001). The worker runs this; the API only re-decides from the stored ``stages``.

Caches: image embeddings by content hash, text embeddings by prompt text, VLM answers by (content hash,
prompt hash) — so editing one class description only re-embeds that text and re-asks the VLM for photos whose
candidate list contains that class (Q-025).
"""

from __future__ import annotations

import hashlib
import inspect
import json
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

import numpy as np
from opagent_core.decide import Outcome, Policy, Stage, VlmResult, decide, route_embedding, temperature_softmax
from opagent_core.rules import RuleError, compile_when

from .prototypes import class_scores
from .types import BatchRequest, ImageInput, InferenceBackend, InferenceError, LoadOptions


@dataclass
class ClassDef:
    id: str
    name: str
    description: str = ""
    examples: list[Any] = field(default_factory=list)  # embedding vectors of example photos
    negatives: list[Any] = field(default_factory=list)

    @property
    def prompt(self) -> str:
        return f"{self.name}. {self.description}".strip() if self.description else self.name


@dataclass
class Taxonomy:
    classes: list[ClassDef]
    rules: list[dict[str, Any]] = field(default_factory=list)  # [{"if": "<when DSL>", "then": class_id}]
    temperature: float = 0.05
    top_k: int = 3

    @property
    def ids(self) -> list[str]:
        return [c.id for c in self.classes]


@dataclass
class Item:
    image: ImageInput
    exif: dict[str, Any] = field(default_factory=dict)


@dataclass
class ClassifyResult:
    item_id: str
    outcome: Outcome
    stages: dict[str, Any]
    model_fingerprint: str
    provenance: list[dict[str, Any]] = field(default_factory=list)
    caption: str | None = None
    tags: list[str] | None = None
    error: str | None = None


@dataclass
class Estimate:
    counts: dict[str, int]
    routed_to_vlm: int
    vlm_calls: int = 0


def _scores(vec: np.ndarray, tax: Taxonomy, text_vecs: dict[str, np.ndarray]) -> dict[str, float]:
    classes = [c for c in tax.classes if c.id != "other"]
    raw = class_scores(
        vec,
        {c.id: text_vecs[c.id] for c in classes},
        {c.id: np.asarray(c.examples) for c in classes if c.examples},
        {c.id: len(c.examples) for c in classes},
        negatives={c.id: np.asarray(c.negatives) for c in classes if c.negatives},
    )
    ids = list(raw)
    probs = temperature_softmax([raw[i] for i in ids], tax.temperature)
    return dict(zip(ids, probs, strict=True))


def estimate_distribution(
    embs: np.ndarray, tax: Taxonomy, text_vecs: dict[str, np.ndarray], policy: Policy = Policy()
) -> Estimate:
    """Library-wide forecast from cached embeddings only — no VLM calls (INF-CAS-010, ORG-007)."""
    classes = [c for c in tax.classes if c.id != "other"]
    m = np.asarray(embs, dtype=float)
    m = m / np.linalg.norm(m, axis=1, keepdims=True)
    from .prototypes import alpha_for, prototypes

    cols = []
    for c in classes:
        t = np.asarray(text_vecs[c.id], dtype=float)
        t = t / (np.linalg.norm(t) or 1.0)
        txt = m @ t
        if c.examples:
            a = alpha_for(len(c.examples))
            pro = (m @ prototypes(np.asarray(c.examples)).T).max(1)
            cols.append(a * txt + (1 - a) * pro)
        else:
            cols.append(txt)
    s = np.stack(cols, 1) / tax.temperature
    s = np.exp(s - s.max(1, keepdims=True))
    p = s / s.sum(1, keepdims=True)
    top = p.argmax(1)
    srt = np.sort(p, 1)
    p1, p2 = srt[:, -1], srt[:, -2] if p.shape[1] > 1 else np.zeros(len(p))
    n_ex = {c.id: len(c.examples) for c in classes}
    counts = {c.id: 0 for c in tax.classes}
    routed = 0
    for i, k in enumerate(top):
        cid = classes[int(k)].id
        tau = policy.tau_high if n_ex[cid] > 0 else policy.tau_high_no_examples
        if p1[i] >= tau and p1[i] - p2[i] >= policy.margin and p1[i] >= policy.accept:
            counts[cid] += 1
        else:
            routed += 1
            counts[cid if p1[i] >= policy.min_confidence else ("other" if "other" in counts else cid)] += 1
    return Estimate(counts=counts, routed_to_vlm=routed)


async def _maybe(v: Any) -> Any:
    return await v if inspect.isawaitable(v) else v


class Cascade:
    def __init__(
        self,
        embedder: Any,  # InferenceBackend with embed_texts (ORT adapter, FakeBackend)
        vlm: InferenceBackend | None = None,
        policy: Policy = Policy(),
        mode: str = "concurrent",
        on_event: Callable[[tuple[str, str]], None] | None = None,
        on_stage: Callable[[str], None] | None = None,
    ) -> None:
        self.on_stage = on_stage or (lambda _s: None)
        self.embedder, self.vlm, self.policy, self.mode = embedder, vlm, policy, mode
        self.events: list[tuple[str, str]] = []
        self.on_event = on_event
        self.image_cache: dict[str, np.ndarray] = {}
        self.text_cache: dict[str, np.ndarray] = {}
        self.vlm_cache: dict[tuple[str, str], tuple[str, float, str | None, list[str] | None]] = {}

    def _ev(self, kind: str, what: str) -> None:
        self.events.append((kind, what))
        if self.on_event:
            self.on_event((kind, what))

    async def _text_vectors(self, tax: Taxonomy) -> dict[str, np.ndarray]:
        missing = [c.prompt for c in tax.classes if c.id != "other" and c.prompt not in self.text_cache]
        if missing:
            vecs = await _maybe(self.embedder.embed_texts(missing))
            for t, v in zip(missing, vecs, strict=True):
                self.text_cache[t] = np.asarray(v, dtype=float)
        return {c.id: self.text_cache[c.prompt] for c in tax.classes if c.id != "other"}

    async def _embed(self, items: list[Item]) -> None:
        todo = [it.image for it in items if it.image.content_hash not in self.image_cache]
        if not todo:
            return
        out = await self.embedder.run_batch(BatchRequest(task_type="embed", items=todo))
        by_id = {o.item_id: o for o in out}
        for img in todo:
            self.image_cache[img.content_hash] = np.asarray(by_id[img.item_id].embedding, dtype=float)
        self._ev("embed", str(len(todo)))

    async def estimate(self, images: list[ImageInput], tax: Taxonomy) -> Estimate:
        """Embedding fast path only (no VLM): forecast for ORG-007 / INF-CAS-010."""
        await self.embedder.load(LoadOptions())
        text_vecs = await self._text_vectors(tax)
        await self._embed([Item(i) for i in images])
        if not images:
            return Estimate(counts={c.id: 0 for c in tax.classes}, routed_to_vlm=0)
        m = np.stack([self.image_cache[i.content_hash] for i in images])
        return estimate_distribution(m, tax, text_vecs, self.policy)

    @staticmethod
    def _rule(tax: Taxonomy, exif: dict[str, Any]) -> str | None:
        for i, r in enumerate(tax.rules):
            try:
                if compile_when(str(r["if"]), f"/rules/{i}/if")({"exif": exif}):
                    return str(r["then"])
            except RuleError:
                continue
        return None

    def _prompt_key(self, tax: Taxonomy, hints: list[str]) -> str:
        defs = {c.id: (c.name, c.description) for c in tax.classes}
        return hashlib.sha256(json.dumps([[h, *defs.get(h, ("", ""))] for h in hints]).encode()).hexdigest()[:16]

    async def run(self, items: list[Item], tax: Taxonomy) -> list[ClassifyResult]:
        results: dict[str, ClassifyResult] = {}
        pending: list[Item] = []
        for it in items:  # 0. rules: no inference at all
            cat = self._rule(tax, it.exif)
            if cat is not None:
                st = {"rule": cat, "route": "rule", "n_examples": 0}
                results[it.image.item_id] = ClassifyResult(
                    it.image.item_id, decide(rule_category=cat, policy=self.policy), st, "rule"
                )
            else:
                pending.append(it)
        if not pending:
            return [results[it.image.item_id] for it in items]

        # 1. embedding stage
        self.on_stage("embedding")
        emb_fp = self.embedder.describe().model_fingerprint
        self._ev("load", "embed")
        await self.embedder.load(LoadOptions())
        text_vecs = await self._text_vectors(tax)
        await self._embed(pending)
        if self.mode == "sequential" and self.vlm is not None:
            await self.embedder.unload()
            self._ev("unload", "embed")
        n_examples = sum(len(c.examples) for c in tax.classes)
        routes: dict[str, Any] = {}
        for it in pending:
            sc = _scores(self.image_cache[it.image.content_hash], tax, text_vecs)
            r = route_embedding(Stage(sc), n_examples, self.policy)
            k = max(3, min(5, tax.top_k))
            hints = [c for c, _ in sorted(sc.items(), key=lambda kv: (-kv[1], kv[0]))[:k]]
            routes[it.image.item_id] = (sc, r, hints)

        # 2. VLM review for low-confidence items only
        to_vlm = [it for it in pending if routes[it.image.item_id][1].route == "vlm"] if self.vlm else []
        vlm_out: dict[str, tuple[str, float, str | None, list[str] | None]] = {}
        errors: dict[str, str] = {}
        vlm_fp = ""
        if to_vlm and self.vlm is not None:
            self.on_stage("vlm")
            vlm_fp = self.vlm.describe().model_fingerprint
            self._ev("load", "vlm")
            await self.vlm.load(LoadOptions())
            need: dict[str, list[Item]] = {}
            for it in to_vlm:
                hints = routes[it.image.item_id][2]
                key = (it.image.content_hash, self._prompt_key(tax, hints))
                if key in self.vlm_cache:
                    vlm_out[it.image.item_id] = self.vlm_cache[key]
                else:
                    need.setdefault(json.dumps(hints), []).append(it)
            asked = sum(len(v) for v in need.values())
            if asked:
                self._ev("vlm", str(asked))
            for hints_json, group in need.items():
                hints = json.loads(hints_json)
                req = BatchRequest(
                    task_type="classify",
                    items=[g.image for g in group],
                    variables={"categories": [*hints, "other"], "hints": hints},
                )
                try:
                    outs = await self.vlm.run_batch(req)
                except InferenceError as e:
                    for g in group:
                        errors[g.image.item_id] = e.error_code
                    continue
                by_id = {o.item_id: o for o in outs}
                for g in group:
                    o = by_id[g.image.item_id]
                    val = (
                        str(o.category or "other"),
                        float(o.confidence or 0.0),
                        o.caption,
                        [t for t, _ in (o.tags or [])],
                    )
                    vlm_out[g.image.item_id] = val
                    self.vlm_cache[(g.image.content_hash, self._prompt_key(tax, hints))] = val

        # 3. decision (stages are stored so thresholds can be re-applied without inference)
        for it in pending:
            iid = it.image.item_id
            sc, r, hints = routes[iid]
            v = vlm_out.get(iid)
            stages: dict[str, Any] = {
                "emb": sc,
                "margin": r.margin,
                "n_examples": n_examples,
                "route": r.route,
                "hints": hints,
                "vlm": {"category": v[0], "p": v[1]} if v else None,
            }
            out = decide(
                emb=Stage(sc), vlm=VlmResult(v[0], v[1]) if v else None, n_examples=n_examples, policy=self.policy
            )
            prov = [{"stage": "embedding", "model_fingerprint": emb_fp}]
            if v:
                prov.append({"stage": "vlm", "model_fingerprint": vlm_fp})
            results[iid] = ClassifyResult(
                iid,
                out,
                stages,
                vlm_fp if v else emb_fp,
                prov,
                caption=v[2] if v else None,
                tags=v[3] if v else None,
                error=errors.get(iid),
            )
        return [results[it.image.item_id] for it in items]
