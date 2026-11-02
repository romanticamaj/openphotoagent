"""Offline gazetteer (05 §1.2 `place`). Stub — see tests."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Place:
    geoname_id: int
    name: str
    country_code: str
    admin1: str
    population: int


class Gazetteer:
    @classmethod
    def fixture(cls) -> Gazetteer:
        return cls()

    def lookup(self, text: str) -> list[Place]:
        return []

    def country(self, text: str) -> str | None:
        return None
