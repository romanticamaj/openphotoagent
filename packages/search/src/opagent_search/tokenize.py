from dataclasses import dataclass
from typing import Any

TOKENIZER_VERSION = ""


def normalize(s: str) -> str:
    return s


@dataclass
class Q:
    words: list[str]
    bigram: list[str] | None
    tag_keys: list[str]


class Tokenizer:
    def __init__(self, custom_words: list[str] | None = None) -> None:
        pass

    def words(self, text: str) -> list[str]:
        return []

    def document(self, **kw: Any) -> dict[str, list[str]]:
        return {"A": [], "B": [], "C": [], "D": []}

    def query(self, text: str) -> Q:
        return Q([], None, [])

    def tag_key(self, w: str) -> str | None:
        return None


def to_tsquery(words: list[str]) -> str:
    return ""
