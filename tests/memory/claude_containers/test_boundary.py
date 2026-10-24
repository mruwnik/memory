import ast
from pathlib import Path

import memory

PACKAGE = "memory.claude_containers"
SRC = Path(memory.__file__).parent.parent


def module_name(path: Path) -> str:
    parts = path.relative_to(SRC).with_suffix("").parts
    return ".".join(parts[:-1] if parts[-1] == "__init__" else parts)


def imported_modules(path: Path) -> set[str]:
    package = module_name(path).split(".")
    package = package if path.name == "__init__.py" else package[:-1]
    found = set()
    for node in ast.walk(ast.parse(path.read_text())):
        if isinstance(node, ast.Import):
            found |= {alias.name for alias in node.names}
        if isinstance(node, ast.ImportFrom):
            base = package[: len(package) - node.level + 1] if node.level else []
            target = ".".join([*base, *filter(None, [node.module])])
            found |= {target} | {f"{target}.{alias.name}" for alias in node.names}
    return found


def in_package(name: str) -> bool:
    return name == PACKAGE or name.startswith(f"{PACKAGE}.")


def imports_package(path: Path) -> bool:
    return any(in_package(name) for name in imported_modules(path))


def test_only_the_composition_points_import_claude_containers():
    importers = {
        module_name(path)
        for path in (SRC / "memory").rglob("*.py")
        if not in_package(module_name(path)) and imports_package(path)
    }

    # The claude MCP server's file-transfer tools still live in core until they
    # move into the package as their own server.
    assert importers == {"memory.api.app", "memory.api.MCP.servers.claude"}
