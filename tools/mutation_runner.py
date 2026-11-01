"""Small AST mutation-testing engine (docs/09 §5.6). Each mutant is a copy of the target package with one
mutated file, shadowed onto PYTHONPATH (the working tree is never modified), so mutants run in parallel.

Operators: comparison swaps, and/or swap, `not` removal, numeric constant +1, boolean constant flip,
string constant emptying (non-docstring), `return x` -> `return None`.
"""

from __future__ import annotations

import ast
import contextlib
import os
import shutil
import signal
import subprocess
import sys
import tempfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SWAP = {
    ast.Eq: ast.NotEq,
    ast.NotEq: ast.Eq,
    ast.Lt: ast.LtE,
    ast.LtE: ast.Lt,
    ast.Gt: ast.GtE,
    ast.GtE: ast.Gt,
    ast.In: ast.NotIn,
    ast.NotIn: ast.In,
    ast.Is: ast.IsNot,
    ast.IsNot: ast.Is,
}
PACKAGES = {  # file prefix -> (package dir, import name, test paths)
    "packages/core/src/opagent_core/": ("packages/core/src/opagent_core", "opagent_core", ["packages/core/tests"]),
    "packages/fs-sandbox/src/opagent_fs/": (
        "packages/fs-sandbox/src/opagent_fs",
        "opagent_fs",
        ["packages/fs-sandbox/tests"],
    ),
}


def _docstring_ids(tree: ast.AST) -> set[int]:
    ids = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Module | ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef) and node.body:
            first = node.body[0]
            if isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant):
                ids.add(id(first.value))
    return ids


def _excluded_ids(tree: ast.AST) -> set[int]:
    """Nodes whose mutation cannot change observable behaviour on this runner (reviewed rules, see
    docs/mutation-equivalents.md): f-string pieces and exception messages, decorator keyword arguments
    (``@dataclass(frozen=True)``), type annotations (``Literal[...]``) and ``sys.platform`` comparisons (those
    branches are exercised by the CI OS matrix)."""
    ids: set[int] = set()

    def all_ids(n: ast.AST) -> None:
        for x in ast.walk(n):
            ids.add(id(x))

    for node in ast.walk(tree):
        if isinstance(node, ast.JoinedStr):
            all_ids(node)
        elif isinstance(node, ast.Raise) and isinstance(node.exc, ast.Call):
            for a in (
                node.exc.args[1:]
                if node.exc.args
                and isinstance(node.exc.args[0], ast.Constant)
                and node.exc.args[0].value.__class__ is str
                and len(node.exc.args) > 1
                else node.exc.args
            ):
                if isinstance(a, ast.Constant) and isinstance(a.value, str) and " " in a.value:
                    ids.add(id(a))
        elif isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef):
            for d in node.decorator_list:
                if isinstance(d, ast.Call):
                    for kw in d.keywords:
                        all_ids(kw)
            if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
                for a in [*node.args.args, *node.args.kwonlyargs]:
                    if a.annotation is not None:
                        all_ids(a.annotation)
                if node.returns is not None:
                    all_ids(node.returns)
        elif isinstance(node, ast.AnnAssign):
            all_ids(node.annotation)
        elif (
            isinstance(node, ast.Assign)
            and isinstance(node.value, ast.Subscript)
            and isinstance(node.value.value, ast.Name)
            and node.value.value.id == "Literal"
        ):
            all_ids(node.value)
        elif isinstance(node, ast.Compare) and any(
            isinstance(x, ast.Attribute) and x.attr == "platform" for x in ast.walk(node)
        ):
            all_ids(node)
    return ids


def _sites(tree: ast.AST) -> list[tuple[int, str]]:
    doc = _docstring_ids(tree) | _excluded_ids(tree)
    out: list[tuple[int, str]] = []
    for i, node in enumerate(ast.walk(tree)):
        if id(node) in doc:
            continue
        if isinstance(node, ast.Compare):
            for j, op in enumerate(node.ops):
                if type(op) in SWAP:
                    out.append((i, f"cmp{j}"))
        elif isinstance(node, ast.BoolOp):
            out.append((i, "boolop"))
        elif isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.Not):
            out.append((i, "not"))
        elif isinstance(node, ast.Constant) and id(node) not in doc:
            if isinstance(node.value, bool):
                out.append((i, "bool"))
            elif isinstance(node.value, int | float):
                out.append((i, "num"))
            elif isinstance(node.value, str) and node.value:
                out.append((i, "str"))
        elif isinstance(node, ast.Return) and node.value is not None:
            out.append((i, "return"))
    return out


def _mutate(src: str, site: tuple[int, str]) -> tuple[str, int] | None:
    tree = ast.parse(src)
    idx, kind = site
    for i, node in enumerate(ast.walk(tree)):
        if i != idx:
            continue
        if kind.startswith("cmp"):
            j = int(kind[3:])
            node.ops[j] = SWAP[type(node.ops[j])]()  # type: ignore[attr-defined]
        elif kind == "boolop":
            node.op = ast.Or() if isinstance(node.op, ast.And) else ast.And()  # type: ignore[attr-defined]
        elif kind == "not":
            node.op = ast.UAdd()  # type: ignore[attr-defined]  # +x keeps truthiness of x -> removes "not"
        elif kind == "bool":
            node.value = not node.value  # type: ignore[attr-defined]
        elif kind == "num":
            node.value = node.value + 1  # type: ignore[attr-defined]
        elif kind == "str":
            node.value = "XX" + node.value  # type: ignore[attr-defined]
        elif kind == "return":
            node.value = ast.Constant(None)  # type: ignore[attr-defined]
        line = getattr(node, "lineno", 0)
        try:
            return ast.unparse(ast.fix_missing_locations(tree)), line
        except Exception:
            return None
    return None


def run(files: list[str], workers: int = 4, timeout: int = 300) -> tuple[float, list[str]]:
    killed = total = 0
    survivors: list[str] = []
    equivalents = set()
    eq_file = ROOT / "mutmut-equivalents.txt"
    if eq_file.exists():
        equivalents = {ln.strip() for ln in eq_file.read_text().splitlines() if ln.strip() and not ln.startswith("#")}
    jobs = []
    for f in files:
        prefix = next(p for p in PACKAGES if f.startswith(p))
        pkg_dir, pkg_name, tests = PACKAGES[prefix]
        src = (ROOT / f).read_text()
        for site in _sites(ast.parse(src)):
            m = _mutate(src, site)
            if m is not None and ast.unparse(ast.parse(src)) != m[0]:
                jobs.append((f, pkg_dir, pkg_name, tests, m[0], m[1], site))

    def one(job: tuple) -> tuple[str, bool]:  # type: ignore[type-arg]
        f, pkg_dir, pkg_name, tests, mutated, line, site = job
        tmp = Path(tempfile.mkdtemp(prefix="mut-"))
        try:
            shutil.copytree(ROOT / pkg_dir, tmp / pkg_name)
            (tmp / pkg_name / Path(f).name).write_text(mutated)
            env = {
                **os.environ,
                "PYTHONPATH": str(tmp),
                "OPAGENT_HYPOTHESIS_PROFILE": "ci",
                "OPAGENT_TC_RESULTS": str(tmp / "tc.json"),
            }
            # own process group: a timed-out mutant (often an infinite loop) is killed together with every
            # subprocess its tests spawned (chaos runners), so nothing is left spinning
            p = subprocess.Popen(
                [sys.executable, "-m", "pytest", "-x", "-q", "-p", "no:randomly", *tests],
                cwd=ROOT,
                env=env,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                start_new_session=True,
            )
            try:
                rc = p.wait(timeout=timeout)
            except subprocess.TimeoutExpired:
                os.killpg(p.pid, signal.SIGKILL)
                p.wait()
                return f"{f}:{line}:{site[1]}", True
            with contextlib.suppress(ProcessLookupError):
                os.killpg(p.pid, signal.SIGKILL)
            return f"{f}:{line}:{site[1]}", rc != 0
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    with ThreadPoolExecutor(workers) as ex:
        for name, dead in ex.map(one, jobs):
            if name in equivalents:
                continue
            total += 1
            killed += dead
            if not dead:
                survivors.append(name)
    return (killed / total if total else 1.0), sorted(survivors)


if __name__ == "__main__":
    score, surv = run(sys.argv[1:])
    print(f"score={score:.3f}")
    for s in surv:
        print("survived", s)
