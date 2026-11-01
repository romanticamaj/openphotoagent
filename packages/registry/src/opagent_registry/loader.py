"""Load and merge the registry: built-in catalog + pins.lock + user YAML (03 §1.1, 06 §1.1, ADR-0002).

Built-in problems raise :class:`RegistryLoadError` (CI fails). User-file problems never break the rest of the
registry: they are reported with file name and line (INF-REG-004) and the model is marked ``invalid``.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import ValidationError

from .schema import FileRef, GpuClass, GpuClasses, ModelSpec, PinsLock, RuntimePack

BUILTIN_DIR = Path(__file__).resolve().parents[4] / "registry"
PinState = Literal["pinned", "unpinned"]


class RegistryLoadError(ValueError):
    pass


@dataclass(frozen=True)
class RegistryIssue:
    file: str
    line: int
    message: str
    model_id: str | None = None


@dataclass
class LoadedModel:
    spec: ModelSpec
    source: Literal["builtin", "user"]
    status: Literal["valid", "invalid"] = "valid"
    errors: list[RegistryIssue] = field(default_factory=list)
    pin_state: dict[str, PinState] = field(default_factory=dict)
    pins: dict[str, dict[str, Any]] = field(default_factory=dict)  # variant -> {revision, files{path: PinnedFile}}
    file: str = ""

    def pinned_files(self, variant: str) -> list[FileRef]:
        """Files of ``variant`` with size/sha256 merged from the lock (built-in) or the YAML itself (user)."""
        v = self.spec.variant(variant)
        if self.source == "user":
            return list(v.files)
        pin = self.pins.get(variant)
        if pin is None:
            return list(v.files)
        return [
            f.model_copy(update={"size": pin["files"][f.path].size, "sha256": pin["files"][f.path].sha256})
            for f in v.files
        ]

    def revision(self, variant: str) -> str:
        pin = self.pins.get(variant)
        return str(pin["revision"]) if pin else self.spec.variant(variant).revision

    @property
    def key(self) -> str:
        return self.spec.id


@dataclass
class Registry:
    models: dict[str, LoadedModel]
    runtimes: dict[str, RuntimePack]
    gpu_classes: list[GpuClass]
    errors: list[RegistryIssue]
    version: str = ""

    def variant_keys(self) -> list[str]:
        return [f"{m.spec.id}@{v.id}" for m in self.models.values() if m.status == "valid" for v in m.spec.variants]


def _line_of(node: yaml.Node | None, loc: tuple[Any, ...]) -> int:
    """1-based line of the YAML node at pydantic error location ``loc``."""
    cur = node
    line = (cur.start_mark.line + 1) if cur is not None else 1
    for part in loc:
        if isinstance(cur, yaml.MappingNode):
            nxt = next((v for k, v in cur.value if isinstance(k, yaml.ScalarNode) and k.value == part), None)
            if nxt is None:
                break
            key = next(k for k, v in cur.value if v is nxt)
            line = key.start_mark.line + 1
            cur = nxt
        elif isinstance(cur, yaml.SequenceNode) and isinstance(part, int) and part < len(cur.value):
            cur = cur.value[part]
            line = cur.start_mark.line + 1
        else:
            break
    return line


def _parse_yaml(path: Path) -> tuple[Any, yaml.Node | None]:
    text = path.read_text(encoding="utf-8")
    node = yaml.compose(text, Loader=yaml.SafeLoader)
    return yaml.safe_load(text), node


def _validate_model(path: Path, data: Any, node: yaml.Node | None) -> tuple[ModelSpec | None, list[RegistryIssue]]:
    try:
        return ModelSpec.model_validate(data), []
    except ValidationError as e:
        mid = data.get("id") if isinstance(data, dict) else None
        return None, [
            RegistryIssue(
                str(path), _line_of(node, tuple(err["loc"])), f"{'.'.join(map(str, err['loc']))}: {err['msg']}", mid
            )
            for err in e.errors()
        ]


def _load_lock(path: Path) -> PinsLock:
    if not path.exists():
        return PinsLock()
    text = "\n".join(ln for ln in path.read_text().splitlines() if not ln.lstrip().startswith("#"))
    try:
        return PinsLock.model_validate(json.loads(text or "{}"))
    except (ValidationError, json.JSONDecodeError) as e:
        raise RegistryLoadError(f"{path}: invalid pins.lock: {e}") from None


def _apply_pins(models: dict[str, LoadedModel], lock: PinsLock, lock_path: Path) -> None:
    for lm in models.values():
        lm.pin_state = {v.id: "unpinned" for v in lm.spec.variants}
    for pin in lock.pins:
        m = models.get(pin.model)
        if m is None or m.source != "builtin":
            raise RegistryLoadError(f"{lock_path}: pin for unknown model {pin.model}")
        try:
            v = m.spec.variant(pin.variant)
        except KeyError:
            raise RegistryLoadError(f"{lock_path}: pin for unknown variant {pin.model}@{pin.variant}") from None
        catalog = {f.path for f in v.files}
        locked = {f.path: f for f in pin.files}
        extra = set(locked) - catalog
        if extra:
            raise RegistryLoadError(
                f"{lock_path}: {pin.model}@{pin.variant} pins files not in the catalog: {sorted(extra)}"
            )
        if catalog - set(locked):
            raise RegistryLoadError(
                f"{lock_path}: {pin.model}@{pin.variant} is partially pinned (missing {sorted(catalog - set(locked))})"
            )
        m.pins[pin.variant] = {"revision": pin.revision, "files": locked}
        m.pin_state[pin.variant] = "pinned"


def _user_issues(spec: ModelSpec, path: Path) -> list[RegistryIssue]:
    out = []
    for v in spec.variants:
        for f in v.files:
            if not f.sha256 or not f.size:
                out.append(
                    RegistryIssue(str(path), 1, f"{v.id}/{f.path}: user models must declare size and sha256", spec.id)
                )
        if v.trust_remote_code:
            out.append(
                RegistryIssue(str(path), 1, f"{v.id}: trust_remote_code requires explicit confirmation", spec.id)
            )
    return out


def load_registry(root: Path | None = None, user_dirs: list[Path] | None = None) -> Registry:
    root = root or BUILTIN_DIR
    models: dict[str, LoadedModel] = {}
    for path in sorted((root / "models").glob("*.yaml")):
        try:
            data, node = _parse_yaml(path)
        except yaml.YAMLError as e:
            raise RegistryLoadError(f"{path}: {e}") from None
        spec, issues = _validate_model(path, data, node)
        if spec is None:
            raise RegistryLoadError("; ".join(f"{i.file}:{i.line}: {i.message}" for i in issues))
        for v in spec.variants:
            for f in v.files:
                if f.size is not None or f.sha256 is not None:
                    raise RegistryLoadError(
                        f"{path}: catalog must not carry size/sha256 ({v.id}/{f.path}); use pins.lock"
                    )
        if spec.id in models:
            raise RegistryLoadError(f"{path}: duplicate model id {spec.id}")
        models[spec.id] = LoadedModel(spec=spec, source="builtin", file=str(path))
    runtimes: dict[str, RuntimePack] = {}
    for path in sorted((root / "runtimes").glob("*.yaml")):
        try:
            rt = RuntimePack.model_validate(yaml.safe_load(path.read_text()))
        except (ValidationError, yaml.YAMLError) as e:
            raise RegistryLoadError(f"{path}: {e}") from None
        runtimes[rt.id] = rt
    gpu_path = root / "gpu_classes.yaml"
    try:
        gpu = GpuClasses.model_validate(yaml.safe_load(gpu_path.read_text())).classes if gpu_path.exists() else []
    except ValidationError as e:
        raise RegistryLoadError(f"{gpu_path}: {e}") from None
    lock_path = root / "pins.lock"
    _apply_pins(models, _load_lock(lock_path), lock_path)

    errors: list[RegistryIssue] = []
    for d in user_dirs or []:
        for path in sorted(Path(d).glob("*.yaml")):
            try:
                data, node = _parse_yaml(path)
            except yaml.MarkedYAMLError as e:
                mark = e.problem_mark or e.context_mark
                errors.append(RegistryIssue(str(path), (mark.line + 1) if mark else 1, f"YAML: {e.problem or e}"))
                continue
            except yaml.YAMLError as e:
                errors.append(RegistryIssue(str(path), 1, f"YAML: {e}"))
                continue
            spec, issues = _validate_model(path, data, node)
            if spec is None:
                errors.extend(issues)
                continue
            issues = _user_issues(spec, path)
            lm = LoadedModel(
                spec=spec, source="user", status="invalid" if issues else "valid", errors=issues, file=str(path)
            )
            lm.pin_state = {v.id: ("unpinned" if issues else "pinned") for v in spec.variants}
            errors.extend(issues)
            models[spec.id] = lm  # same id: the user's file overrides the built-in one (03 §1.1)
    h = hashlib.sha256()
    for p in sorted(root.rglob("*")):
        if p.is_file() and p.suffix in {".yaml", ".lock"}:
            h.update(p.relative_to(root).as_posix().encode() + b"\0" + p.read_bytes())
    version = h.hexdigest()[:16]  # registry version: part of compat inputs_hash (06 §1.2)
    return Registry(models=models, runtimes=runtimes, gpu_classes=list(gpu), errors=errors, version=version)
