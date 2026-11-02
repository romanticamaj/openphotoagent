"""LLM seam for the assistant (10 §8.2). The real one is llama-server with ``--jinja`` tool calling or an external
OpenAI-compatible endpoint; tests use ``FakeLLM``, which replays a script of tool calls and answers and can be told
to misbehave (cite photos it never saw, invent numbers, claim results when there were none, loop forever)."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Protocol


@dataclass
class Reply:
    tool: str | None = None  # a tool call …
    args: dict[str, Any] = field(default_factory=dict)
    text: str | None = None  # … or the final answer


class LLM(Protocol):
    name: str
    tool_calling_ok: bool

    def next(self, messages: list[dict[str, Any]]) -> Reply: ...


class FakeLLM:
    """``script``: replies in order; a callable gets the conversation so far (to answer from tool results)."""

    name = "fake-llm"

    def __init__(self, script: list[Reply | Callable[[list[dict[str, Any]]], Reply]] | None = None,
                 tool_calling_ok: bool = True, endless_tool: str | None = None) -> None:
        self.script = list(script or [])
        self.tool_calling_ok = tool_calling_ok
        self.endless_tool = endless_tool
        self.seen: list[list[dict[str, Any]]] = []

    def next(self, messages: list[dict[str, Any]]) -> Reply:
        self.seen.append([dict(m) for m in messages])
        if self.endless_tool:
            return Reply(tool=self.endless_tool, args={})
        if not self.script:
            return Reply(text="")
        r = self.script.pop(0)
        return r(messages) if callable(r) else r
