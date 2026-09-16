#!/usr/bin/env python3
"""Find substantial exact code duplication without scanning student/private data."""

from __future__ import annotations

import ast
from collections import defaultdict
from pathlib import Path


def audit(roots: tuple[Path, ...]) -> list[tuple[str, ...]]:
    blocks: defaultdict[tuple[str, ...], set[str]] = defaultdict(set)
    bodies: defaultdict[str, set[str]] = defaultdict(set)
    for path in sorted({path for root in roots for path in root.rglob("*.py")}):
        source = path.read_text()
        lines = source.splitlines()
        for position in range(max(0, len(lines) - 7)):
            block = tuple(line.rstrip() for line in lines[position : position + 8])
            if all(line.strip() and not line.lstrip().startswith("#") for line in block):
                blocks[block].add(str(path))
        for node in ast.walk(ast.parse(source)):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and len(node.body) >= 5:
                body = ast.dump(
                    ast.Module(body=node.body, type_ignores=[]), include_attributes=False
                )
                bodies[body].add(str(path))
    return sorted(
        {tuple(sorted(paths)) for paths in (*blocks.values(), *bodies.values()) if len(paths) > 1}
    )


def main() -> None:
    duplicates = audit((Path("tools"), Path("tests")))
    for paths in duplicates:
        print("Repeated code: " + ", ".join(paths))
    print(f"Substantial exact cross-file duplication groups: {len(duplicates)}")
    raise SystemExit(bool(duplicates))


if __name__ == "__main__":
    main()
