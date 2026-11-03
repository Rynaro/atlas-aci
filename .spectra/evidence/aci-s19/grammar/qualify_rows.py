import sys
sys.path.insert(0, "/ws/.spectra/evidence/aci-s19/grammar")
from dump_grammar import SAMPLES
from tree_sitter_language_pack import get_parser
from atlas_aci.query import diff_syntax as ds

def entities(lang, src):
    root = get_parser(lang).parse(src).root_node
    out = []; stack=[root]
    while stack:
        n = stack.pop(); stack.extend(n.children)
        row = ds.ROWS.get(lang, {}).get(n.type)
        if row is None: continue
        name = n.child_by_field_name("name")
        if name is None: continue
        kind = sorted(row.kinds)[0]
        if lang=="rust" and n.type=="function_item":
            p=n.parent
            kind = "method" if p is not None and p.type=="declaration_list" and p.parent is not None and p.parent.type in {"impl_item","trait_item"} else "function"
        if n.type=="variable_declarator":
            v=n.child_by_field_name("value")
            if v is None or v.type not in ("arrow_function","function_expression"): continue
        out.append(ds.EntityInput(f"{n.type}:{n.start_byte}", kind, src[name.start_byte:name.end_byte].decode(), n.start_byte, n.end_byte))
    return out

for lang, groups in SAMPLES.items():
    if lang not in ds.ROWS: continue
    for gname, text in groups.items():
        src = text.encode()
        if "error" in gname: continue
        ents = entities(lang, src)
        fa = ds.FileAnalysis(lang, src, ents)
        for e in ents:
            try:
                f = fa.facts(e.entity_id)
                print(lang, gname, e.kind, e.name, "OK", f.basis, f.signature_tokens, f.body_tokens)
            except ds.Unknown as u:
                print(lang, gname, e.kind, e.name, "UNKNOWN", u.reason)
