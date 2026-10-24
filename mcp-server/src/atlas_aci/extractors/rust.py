"""Rust syntax only: lexical declaration ownership and raw call qualifiers.

No type, crate, macro, cfg or dispatch resolution is performed here.
"""

from __future__ import annotations

KINDS = ("enum", "function", "method", "module", "struct", "trait")
LIMITATIONS = {
    "rust_macro_expansion": "unsupported_including_derive_and_proc_macros",
    "rust_generated_code": "unmaterialized_outputs_unavailable",
    "rust_cfg": "build_configuration_not_evaluated",
    "rust_dispatch": "unresolved_without_type_or_trait_inference",
}
QUERY = """
(function_item name: (identifier) @name) @def.function
(function_signature_item name: (identifier) @name) @def.method
(struct_item name: (type_identifier) @name) @def.struct
(enum_item name: (type_identifier) @name) @def.enum
(trait_item name: (type_identifier) @name) @def.trait
(mod_item name: (identifier) @name) @def.module
(call_expression function: (_) @callee) @ref.call
"""


def definition_kind(node, fallback: str) -> str:
    if node.type not in {"function_item", "function_signature_item"}:
        return fallback
    parent = node.parent
    return (
        "method"
        if parent is not None
        and parent.type == "declaration_list"
        and parent.parent is not None
        and parent.parent.type in {"impl_item", "trait_item"}
        else "function"
    )


def impl_header(node, source: bytes) -> str:
    """Exact header tokens, omitting whitespace/comments, never byte coordinates."""
    body = node.child_by_field_name("body")
    tokens = []
    pending = [child for child in reversed(node.children) if child != body]
    while pending:
        token = pending.pop()
        if token.type in {"line_comment", "block_comment"}:
            continue
        if token.type.endswith("_literal"):
            # Literal delimiters, escapes and internal whitespace are tokens,
            # not header trivia. Some Rust literal child lists omit delimiters.
            tokens.append(
                source[token.start_byte : token.end_byte].decode("utf-8", errors="replace")
            )
        elif token.children:
            pending.extend(reversed(token.children))
        else:
            text = source[token.start_byte : token.end_byte].decode("utf-8", errors="replace")
            if text.strip():
                tokens.append(text)
    return " ".join(tokens)


def scope_overrides(tree, source: bytes):
    result = {}
    pending = [tree.root_node]
    while pending:
        node = pending.pop()
        if node.type == "impl_item":
            result[node.id] = ("impl", impl_header(node, source))
        pending.extend(reversed(node.named_children))
    return result


def qualifier(node, source: bytes) -> str | None:
    if node.type == "generic_function":
        inner = node.child_by_field_name("function")
        return qualifier(inner, source) if inner is not None else None
    owner = (
        node.child_by_field_name("value")
        if node.type == "field_expression"
        else node.child_by_field_name("path")
        if node.type == "scoped_identifier"
        else None
    )
    return (
        source[owner.start_byte : owner.end_byte].decode("utf-8", errors="replace")
        if owner is not None
        else None
    )
