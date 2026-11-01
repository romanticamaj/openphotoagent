from typing import Any

CATALOG: dict[str, Any] = {}


def problem(code: str, params: dict[str, Any], deployment: str = "desktop", os: str = "windows") -> dict[str, Any]:
    return {"code": code, "fixes": []}
