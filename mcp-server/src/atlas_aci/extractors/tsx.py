"""TSX syntax only: declarations, JSX value tags, import specifiers, export status.

No module, type, JSX-runtime, React or dispatch resolution is performed here.
"""

from __future__ import annotations

from typing import Any

KINDS = ("class", "function", "method")
# Persisted relations, exactly: `call`/`construct`/`superclass` mirror the
# TypeScript structural binding, `import` rows are always unresolved, `jsx`
# rows are always unresolved, `rationale_for` comes from recognized comments.
RELATIONS = ("call", "construct", "import", "jsx", "rationale_for", "superclass")
MODULE_RESOLUTION_REASON = "tsx_module_resolution_unavailable"
JSX_REASON = "jsx_semantic_binding_unavailable"
EXPORT_STATUSES = frozenset({"named", "default"})
LIMITATIONS = {
    "tsx_module_resolution": "unavailable_all_imports_unresolved",
    "tsx_jsx_binding": "value_tags_recorded_unresolved_without_semantic_binding",
    "tsx_intrinsic_jsx": "lowercase_and_namespaced_tags_not_recorded",
    "tsx_export_specifiers": "export_lists_and_reexports_not_recorded",
    "tsx_default_export_identifier": "export_default_identifier_not_recorded",
    "tsx_anonymous_default_export": "unnamed_declarations_not_recorded",
    "tsx_dynamic_binding": "import_call_require_and_computed_member_unbound",
}
QUERY = """
(class_declaration name: (type_identifier) @name) @def.class
(function_declaration name: (identifier) @name) @def.function
(variable_declarator
    name: (identifier) @name
    value: [(arrow_function) (function_expression)]) @def.function
(method_definition name: (property_identifier) @name) @def.method
(method_definition name: (private_property_identifier) @name) @def.method
(call_expression function: (identifier) @callee) @ref.call
(call_expression
    function: (member_expression
        object: (_) @receiver
        property: (property_identifier) @callee)) @ref.call
(call_expression
    function: (member_expression
        object: (_) @receiver
        property: (private_property_identifier) @callee)) @ref.call
(new_expression constructor: (identifier) @callee) @ref.call
(class_declaration
    (class_heritage (extends_clause value: (identifier) @target_name))) @heritage.superclass
(jsx_opening_element name: (_) @jsx_tag)
(jsx_self_closing_element name: (_) @jsx_tag)
(variable_declarator name: (identifier) @assign.target)
(assignment_expression left: (identifier) @assign.target)
(comment) @comment.rationale
"""
_FUNCTION_VALUES = frozenset({"arrow_function", "function_expression"})
_LOWER = frozenset("abcdefghijklmnopqrstuvwxyz")


def _text(source: bytes, node) -> str:
    return source[node.start_byte : node.end_byte].decode("utf-8", errors="replace")


def is_function_declarator(node) -> bool:
    """A `const Name = () => ...` / `= function () {}` declarator owns its body."""
    if node is None or node.type != "variable_declarator":
        return False
    name = node.child_by_field_name("name")
    value = node.child_by_field_name("value")
    return (
        name is not None
        and name.type == "identifier"
        and value is not None
        and value.type in _FUNCTION_VALUES
    )


def component_tag(name_node, source: bytes) -> tuple[str, str | None] | None:
    """JSX's own rule: a value reference, never an intrinsic host element.

    Returns `(tag text, qualifier text)` for an identifier whose first
    character is not a lowercase ASCII letter, or a member expression; `None`
    for lowercase identifiers, namespaced names and anything else.
    """
    if name_node.type == "identifier":
        text = _text(source, name_node)
        return None if not text or text[0] in _LOWER else (text, None)
    if name_node.type == "member_expression":
        owner = name_node.child_by_field_name("object")
        return (
            _text(source, name_node).strip(),
            _text(source, owner).strip() if owner is not None else None,
        )
    return None


def export_status(node) -> str | None:
    """`named`/`default` when the declaration itself carries an export keyword."""
    parent = node.parent
    if node.type == "variable_declarator" and parent is not None:
        if parent.type not in {"lexical_declaration", "variable_declaration"}:
            return None
        parent = parent.parent
    if parent is None or parent.type != "export_statement":
        return None
    return (
        "default"
        if any(c.type == "default" and not c.is_named for c in parent.children)
        else "named"
    )


def _unquote(source: bytes, node) -> str:
    text = _text(source, node)
    return text[1:-1] if len(text) >= 2 and text[0] == text[-1] and text[0] in "\"'`" else text


def _keyword(node, word: str) -> bool:
    return any(c.type == word and not c.is_named for c in node.children)


def imports(tree, source: bytes):
    """Yield `(import statement, occurrence node, description)` per specifier.

    Imported name, preserved local alias, module source and specifier kind are
    syntax. `name` is `default` for a default import and `*` for a namespace.
    """
    pending = [tree.root_node]
    while pending:
        node = pending.pop()
        pending.extend(reversed(node.named_children))
        if node.type != "import_statement":
            continue
        clause = next((c for c in node.named_children if c.type == "import_clause"), None)
        module = node.child_by_field_name("source")
        if clause is None or module is None:
            continue  # side-effect import: no binding is introduced
        statement_type_only = _keyword(node, "type")

        def description(name, local, kind, type_only, module=module):
            return {
                "name": name,
                "local_name": local,
                "source": _unquote(source, module),
                "kind": kind,
                "type_only": type_only,
                "reason": MODULE_RESOLUTION_REASON,
            }

        for child in clause.named_children:
            if child.type == "identifier":
                local = _text(source, child)
                yield node, child, description("default", local, "default", statement_type_only)
            elif child.type == "namespace_import":
                target = next((c for c in child.named_children if c.type == "identifier"), None)
                if target is not None:
                    yield (
                        node,
                        target,
                        description("*", _text(source, target), "namespace", statement_type_only),
                    )
            elif child.type == "named_imports":
                for specifier in child.named_children:
                    if specifier.type != "import_specifier":
                        continue
                    name = specifier.child_by_field_name("name")
                    alias = specifier.child_by_field_name("alias")
                    if name is None:
                        continue
                    imported = (
                        _unquote(source, name) if name.type == "string" else _text(source, name)
                    )
                    local = _text(source, alias) if alias is not None else imported
                    yield (
                        node,
                        name,
                        description(
                            imported,
                            local,
                            "named",
                            statement_type_only or _keyword(specifier, "type"),
                        ),
                    )


def valid_import_description(description: Any, local_name: Any) -> bool:
    """Closed shape of every persisted TSX import description."""
    return (
        isinstance(description, dict)
        and set(description) == {"name", "local_name", "source", "kind", "type_only", "reason"}
        and isinstance(description["name"], str)
        and bool(description["name"])
        and description["local_name"] == local_name
        and isinstance(local_name, str)
        and bool(local_name)
        and isinstance(description["source"], str)
        and description["kind"] in {"default", "named", "namespace"}
        and type(description["type_only"]) is bool
        and description["reason"] == MODULE_RESOLUTION_REASON
    )
