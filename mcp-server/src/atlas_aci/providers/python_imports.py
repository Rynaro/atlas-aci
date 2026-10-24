"""Only package-local, unaliased Python relative imports; no sys.path lookup."""

from __future__ import annotations

from pathlib import PurePosixPath
from typing import Any

CONTROL = frozenset(
    {
        "if_statement",
        "for_statement",
        "while_statement",
        "try_statement",
        "except_clause",
        "with_statement",
        "match_statement",
        "case_clause",
    }
)


def unconditional(node) -> bool:
    parent = node.parent
    while parent is not None:
        if parent.type in CONTROL:
            return False
        parent = parent.parent
    return True


def extract(tree, source: bytes):
    """Yield actual import/target nodes and serializable binding descriptions."""
    pending = [tree.root_node]
    while pending:
        node = pending.pop()
        pending.extend(reversed(node.named_children))
        if node.type not in {"import_statement", "import_from_statement"}:
            continue
        module = node.child_by_field_name("module_name")
        text = source[module.start_byte : module.end_byte].decode() if module is not None else ""
        level = len(text) - len(text.lstrip("."))
        parts = text[level:].split(".") if text[level:] else []
        for child in node.named_children:
            if child == module:
                continue
            reason = None
            name_node = child
            if child.type == "aliased_import":
                name_node = child.child_by_field_name("name")
                alias = child.child_by_field_name("alias")
                local = source[alias.start_byte : alias.end_byte].decode() if alias else ""
                reason = "unsupported_import_alias"
            elif child.type == "wildcard_import":
                local = "*"
                reason = "unsupported_import_star"
            else:
                local = source[child.start_byte : child.end_byte].decode().split(".")[0]
            if name_node is None:
                continue
            raw = source[name_node.start_byte : name_node.end_byte].decode()
            if not reason and (node.type != "import_from_statement" or level == 0):
                reason = "unsupported_absolute_import"
            if not reason and (
                not local.isidentifier() or any(not part.isidentifier() for part in parts)
            ):
                reason = "unsupported_import_syntax"
            if not reason and not unconditional(node):
                reason = "unsupported_import_control"
            yield (
                node,
                name_node,
                {
                    "level": level,
                    "module": parts,
                    "name": raw,
                    "local_name": local,
                    "reason": reason,
                },
            )


def resolve(path: str, description: dict[str, Any], indexed_paths: set[str], symbols, blockers):
    """Resolve a single exact module and root declaration, or return a reason."""
    if description.get("reason"):
        return [], description["reason"]
    package = list(PurePosixPath(path).parent.parts)
    if not package or any(
        "/".join(package[:i]) + "/__init__.py" not in indexed_paths
        for i in range(1, len(package) + 1)
    ):
        return [], "relative_import_no_package"
    level = description["level"]
    if type(level) is not int or not 1 <= level <= len(package):
        return [], "relative_import_outside_package"
    base = package[: len(package) - level + 1]
    module = [*base, *description["module"]]
    if any(
        "/".join(module[:i]) + "/__init__.py" not in indexed_paths for i in range(1, len(module))
    ):
        return [], "relative_import_no_package"
    candidates = (
        ["/".join(module) + ".py", "/".join(module) + "/__init__.py"]
        if description["module"]
        else ["/".join(base) + "/__init__.py"]
    )
    modules = [candidate for candidate in candidates if candidate in indexed_paths]
    if len(modules) != 1:
        return (
            [],
            "relative_import_ambiguous_module" if modules else "relative_import_missing_module",
        )
    target = modules[0]
    scope = f"python:{target}:root"
    if blockers.get((target, scope, description["name"])) or blockers.get((target, scope, "*")):
        return [], "relative_import_blocked_export"
    found = [
        symbol
        for symbol in symbols
        if symbol["path"] == target
        and symbol["lang"] == "python"
        and symbol["name"] == description["name"]
        and symbol["context"].get("scope") == f"python:{target}:root"
    ]
    if any(symbol["context"].get("import_export_safe") is not True for symbol in found):
        return [], "relative_import_conditional_export"
    if len(found) != 1:
        return [], "relative_import_ambiguous_target" if found else "relative_import_missing_target"
    return found, None
