from __future__ import annotations

import ast
from pathlib import Path


def test_cli_module_does_not_import_workspace_or_service_at_module_scope():
    path = Path("autoresearch/session_agent/__main__.py")
    tree = ast.parse(path.read_text(encoding="utf-8"))
    imports = []
    for node in tree.body:
        if isinstance(node, ast.ImportFrom):
            imports.append(node.module or "")
        elif isinstance(node, ast.Import):
            imports.extend(alias.name for alias in node.names)
    assert not any(name.startswith("autoresearch") for name in imports)
