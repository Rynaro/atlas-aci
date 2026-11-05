"""Day-1 (b): refs rows, owners, binding and definition spans for the S26 fixture (base code;
scratch evidence, not product code).

    python qual_refs.py <fixture-project-dir>
"""
import json
import shutil
import sys
import tempfile
from pathlib import Path

import tree_sitter_language_pack as pack

from atlas_aci.codegraph import CodeGraph
from atlas_aci.storage import evidence as ev

SITE_METHODS = {"perform_async", "perform_later", "perform_in", "perform_at"}
fixture = Path(sys.argv[1])
work = Path(tempfile.mkdtemp())
repo = work / "repo"
shutil.copytree(fixture, repo)
graph = CodeGraph(repo, graph_root=work / "g")
graph.build()
parser = pack.get_parser("ruby")
result = {"sites": [], "problems": [], "definitions": [], "extra": {}}


def text(source, node):
    return source[node.start_byte : node.end_byte].decode()


def walk(root):
    stack = [root]
    while stack:
        node = stack.pop()
        yield node
        stack.extend(reversed(node.named_children))


def refs_rows(db):
    rows = db.execute(
        "SELECT r.path, r.callee_name, r.relation, r.binding, r.source_entity_id, r.occurrence_id, "
        "r.line, r.col, json_extract(o.payload,'$.start_byte'), json_extract(o.payload,'$.end_byte'),"
        " json_extract(o.payload,'$.kind'), json_extract(o.payload,'$.role') "
        "FROM refs r JOIN occurrences o ON o.id=r.occurrence_id WHERE r.lang='ruby' "
        "AND r.callee_name IN ('perform_async','perform_later','perform_in','perform_at') "
        "ORDER BY r.path, 9, 10"
    ).fetchall()
    return [tuple(r) for r in rows]


with graph.reader_lease() as lease:
    db = lease.db
    rows = refs_rows(db)
    by_key = {}
    for row in rows:
        by_key.setdefault((row[0], row[8], row[9]), []).append(row)
    entity_by_span = {}
    for path, kind, entity_id, binding, start, end, name in db.execute(
        "SELECT s.path, s.kind, s.entity_id, s.binding, json_extract(o.payload,'$.start_byte'), "
        "json_extract(o.payload,'$.end_byte'), s.name FROM symbols s JOIN occurrences o "
        "ON o.id=s.occurrence_id WHERE s.lang='ruby' AND s.kind IN ('class','module','method')"
    ):
        entity_by_span.setdefault((path, start, end), []).append((kind, entity_id, binding, name))
    files = sorted(p for p in repo.rglob("*.rb"))
    seen_keys = set()
    for p in files:
        rel = str(p.relative_to(repo))
        source = p.read_bytes()
        tree = parser.parse(source)
        calls = [
            n
            for n in walk(tree.root_node)
            if n.type == "call"
            and n.child_by_field_name("method") is not None
            and text(source, n.child_by_field_name("method")) in SITE_METHODS
        ]
        for call in calls:
            method = call.child_by_field_name("method")
            key = (rel, method.start_byte, method.end_byte)
            seen_keys.add(key)
            matched = by_key.get(key, [])
            site = {
                "path": rel,
                "line": method.start_point[0] + 1,
                "method": text(source, method),
                "refs_rows": len(matched),
                "skip_path": rel.startswith("tmp/"),
            }
            if rel.startswith("tmp/"):
                site["indexed_refs"] = len(matched)
                result["sites"].append(site)
                continue
            if len(matched) != 1:
                result["problems"].append({"site": site, "problem": "refs row count"})
                result["sites"].append(site)
                continue
            row = matched[0]
            binding = json.loads(row[3])
            found = tree.root_node.descendant_for_byte_range(row[8], row[9])
            site["occ_kind_role"] = [row[10], row[11]]
            site["located"] = (
                found.type == "identifier"
                and found.parent is not None
                and found.parent.type == "call"
                and (found.parent.child_by_field_name("method").start_byte,
                     found.parent.child_by_field_name("method").end_byte) == (row[8], row[9])
                and found.parent.start_byte == call.start_byte
            )
            site["owner_non_null"] = row[4] is not None
            owner = ev.get(db, "entity", row[4]) if row[4] else None
            site["owner"] = None if owner is None else [owner["kind"], owner["name"], owner["path"]]
            # independent expected owner: nearest enclosing method/class/module node that has an
            # entity at exactly that span; scope_resolution-named class/module that lacks one -> file
            expected = "file"
            node = call.parent
            uncaptured = False
            chain = []
            while node is not None:
                if node.type in {"class", "module", "method", "singleton_method"}:
                    chain.append(node.type)
                    name = node.child_by_field_name("name")
                    spans = entity_by_span.get((rel, node.start_byte, node.end_byte), [])
                    if node.type in {"class", "module"} and name is not None and name.type == "scope_resolution" and not spans:
                        uncaptured = True
                node = node.parent
            site["ancestor_chain"] = chain
            site["uncaptured_scope_resolution_ancestor"] = uncaptured
            site["binding_namespace"] = binding.get("namespace")
            site["binding_owner"] = binding.get("owner")
            site["binding_category"] = binding.get("category")
            site["binding_byte"] = binding.get("byte")
            site["unsupported_source_owner"] = binding.get("unsupported_source_owner", False)
            names = []
            node = call.parent
            while node is not None:
                if node.type in {"class", "module"}:
                    nm = node.child_by_field_name("name")
                    names.append(text(source, nm) if nm is not None else None)
                node = node.parent
            site["nesting_join"] = "::".join(n for n in reversed(names) if n)
            site["namespace_equals_nesting_join"] = site["nesting_join"] == (binding.get("namespace") or "")
            result["sites"].append(site)
            if not site["located"] or row[4] is None:
                result["problems"].append({"site": site, "problem": "locate/owner"})
    extra = [k for k in by_key if k not in seen_keys]
    result["extra"]["refs_rows_without_a_call_node"] = [list(k) for k in extra]
    # definitions: every perform method and class/module node maps 1:1 to a symbols row
    for p in files:
        rel = str(p.relative_to(repo))
        if rel.startswith("tmp/"):
            continue
        source = p.read_bytes()
        tree = parser.parse(source)
        for node in walk(tree.root_node):
            if node.type not in {"class", "module", "method", "singleton_method"}:
                continue
            name = node.child_by_field_name("name")
            label = text(source, name) if name is not None else ""
            if node.type in {"method", "singleton_method"} and label != "perform":
                continue
            rows = entity_by_span.get((rel, node.start_byte, node.end_byte), [])
            entry = {
                "path": rel,
                "node": node.type,
                "name": label,
                "line": node.start_point[0] + 1,
                "rows": [
                    {"kind": r[0], "name": r[3], "binding": json.loads(r[2])} for r in rows
                ],
                "starts_with": text(source, node)[:12],
            }
            result["definitions"].append(entry)
    result["extra"]["producer_count"] = db.execute("SELECT count(*) FROM producers").fetchone()[0]
    before = refs_rows(db)
    snap = lease.snapshot_id
    full_refs = [
        tuple(r)
        for r in db.execute(
            "SELECT path, callee_name, relation, binding, source_entity_id, occurrence_id, line, col "
            "FROM refs WHERE lang='ruby' ORDER BY path, line, col, callee_name"
        )
    ]
# unchanged incremental build: refs rows of unchanged files persist with the same values
graph2 = CodeGraph(repo, graph_root=work / "g")
graph2.build(since="x")
with graph2.reader_lease() as lease:
    db = lease.db
    after = refs_rows(db)
    full_after = [
        tuple(r)
        for r in db.execute(
            "SELECT path, callee_name, relation, binding, source_entity_id, occurrence_id, line, col "
            "FROM refs WHERE lang='ruby' ORDER BY path, line, col, callee_name"
        )
    ]
result["extra"]["incremental_site_refs_equal"] = before == after and len(before) > 0
result["extra"]["incremental_all_ruby_refs_equal"] = full_refs == full_after and len(full_refs) > 0
result["extra"]["site_rows"] = len(before)
# an incremental build after editing one unrelated file keeps the other files' site rows
(repo / "app/services/plain_task.rb").write_text(
    (repo / "app/services/plain_task.rb").read_text() + "\n# edit\n"
)
graph3 = CodeGraph(repo, graph_root=work / "g")
graph3.build(since="x")
with graph3.reader_lease() as lease:
    result["extra"]["incremental_after_edit_site_rows_equal"] = refs_rows(lease.db) == before
print(json.dumps(result, indent=1, sort_keys=True, default=str))
