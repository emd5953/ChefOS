"""Module boundaries (docs/specs/backend-module-boundaries.md).

Platform modules import no domain code; only composition roots import one; a domain reaches
another domain only through the submodules listed in that domain's `PUBLIC`; and the
package-level graph stays acyclic once `ALLOWED_BACK_EDGES` is removed.
"""

import ast
import importlib
from collections.abc import Iterator
from pathlib import Path

APP_DIR = Path(__file__).resolve().parents[1] / "app"

PLATFORM = {"business_date", "config", "db", "errors", "ids", "context", "formatting", "telemetry"}
ROOTS = {"main", "cli", "health", "models"}
DOMAINS = {
    "audit",
    "security",
    "identity",
    "commerce",
    "integrations",
    "inventory",
    "sous",
    "team",
    "workflows",
}
ROUTERS = {"api", "push"}  # route modules; only `app.main` mounts them

# Known cycles, tolerated and ratcheted: delete an entry once its edge is gone (the test
# fails on a stale entry). Never add one; break the cycle instead.
ALLOWED_BACK_EDGES = {
    ("security", "identity"),  # sessions load users and memberships
    # Integration freshness and read models shown on commerce and team pages.
    ("commerce", "integrations"),
    ("team", "integrations"),
    ("integrations", "workflows"),  # connections start sync workflows
}


def _imports() -> Iterator[tuple[str, str, str, list[str]]]:
    """(source module, source file, imported dotted path, imported names) for every app import."""
    for path in APP_DIR.rglob("*.py"):
        rel = path.relative_to(APP_DIR)
        src = rel.parts[0].removesuffix(".py")
        package = ["app", *rel.parent.parts]
        for node in ast.walk(ast.parse(path.read_text())):
            if isinstance(node, ast.ImportFrom):
                # Resolve relative imports (`from ..team import api`) against the file's package.
                base = package[: len(package) - node.level + 1] if node.level else []
                module = ".".join([*base, *([node.module] if node.module else [])])
                if module == "app" or module.startswith("app."):
                    yield src, str(rel), module, [a.name for a in node.names]
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name.startswith("app."):
                        yield src, str(rel), alias.name, []


def _targets(module: str, names: list[str]) -> Iterator[list[str]]:
    """Each concrete target path below `app`; `from app[.x] import y` may name a submodule."""
    parts = module.split(".")[1:]
    if len(parts) <= 1 and names:
        for name in names:
            yield [*parts, name]
    else:
        yield parts


def _public(domain: str) -> set[str]:
    return set(getattr(importlib.import_module(f"app.{domain}"), "PUBLIC", ()))


def test_every_app_module_is_classified() -> None:
    top = {p.name.removesuffix(".py") for p in APP_DIR.iterdir() if p.name != "__pycache__"}
    top -= {"__init__"}
    assert top == PLATFORM | ROOTS | DOMAINS


def test_platform_imports_only_platform() -> None:
    offenders = [
        f"{file} -> app.{'.'.join(target)}"
        for src, file, mod, names in _imports()
        for target in _targets(mod, names)
        if src in PLATFORM and target[0] not in PLATFORM
    ]
    assert not offenders, offenders


def test_only_roots_import_a_root() -> None:
    offenders = [
        f"{file} -> app.{'.'.join(target)}"
        for src, file, mod, names in _imports()
        for target in _targets(mod, names)
        if target[0] in ROOTS and src not in ROOTS
    ]
    assert not offenders, offenders


def test_domains_use_only_public_surfaces() -> None:
    public = {d: _public(d) for d in DOMAINS}
    offenders = []
    for src, file, mod, names in _imports():
        if src not in DOMAINS:
            continue
        for target in _targets(mod, names):
            dst = target[0]
            if dst == src or dst not in DOMAINS:
                continue
            if len(target) > 1 and (target[1] in ROUTERS or target[1] not in public[dst]):
                offenders.append(f"{file} -> app.{'.'.join(target)}")
            offenders += [f"{file} -> {mod}.{n}" for n in names if n.startswith("_")]
    assert not offenders, f"not in the target's PUBLIC (app/<module>/__init__.py): {offenders}"


def test_public_surfaces_exist() -> None:
    missing = [
        f"{d}.{name}"
        for d in DOMAINS
        for name in _public(d)
        if not (APP_DIR / d / f"{name}.py").exists() and not (APP_DIR / d / name).is_dir()
    ]
    assert not missing, missing


def _domain_edges() -> set[tuple[str, str]]:
    edges = set()
    for src, _, mod, names in _imports():
        for target in _targets(mod, names):
            if src in DOMAINS and target[0] in DOMAINS and target[0] != src:
                edges.add((src, target[0]))
    return edges


def test_domain_graph_is_acyclic_outside_the_allowlist() -> None:
    edges = _domain_edges()
    stale = ALLOWED_BACK_EDGES - edges
    assert not stale, f"edge gone, drop it from ALLOWED_BACK_EDGES: {sorted(stale)}"

    graph: dict[str, set[str]] = {d: set() for d in DOMAINS}
    for src, dst in edges - ALLOWED_BACK_EDGES:
        graph[src].add(dst)
    state: dict[str, int] = {}  # 1 = on the stack, 2 = done
    stack: list[str] = []

    def visit(node: str) -> None:
        state[node] = 1
        stack.append(node)
        for nxt in sorted(graph[node]):
            if state.get(nxt) == 1:
                cycle = [*stack[stack.index(nxt) :], nxt]
                raise AssertionError(f"new module cycle: {' -> '.join(cycle)}")
            if nxt not in state:
                visit(nxt)
        stack.pop()
        state[node] = 2

    for node in sorted(graph):
        if node not in state:
            visit(node)
