import sys, json
sys.path.insert(0, "/x/gen")
import scip_pb2
from google.protobuf.json_format import MessageToDict
idx = scip_pb2.Index(); idx.ParseFromString(open(sys.argv[1], "rb").read())
d = MessageToDict(idx, preserving_proto_field_name=True)
print(json.dumps(d["metadata"], indent=1))
for doc in d["documents"]:
    print("DOC", doc["relative_path"], doc.get("language"), "enc=", doc.get("position_encoding"), "occ=", len(doc.get("occurrences", [])), "sym=", len(doc.get("symbols", [])))
    for o in doc.get("occurrences", []):
        print("  ", o.get("range"), o.get("symbol"), "roles=", o.get("symbol_roles", 0), o.get("syntax_kind"), "enc_range=", o.get("enclosing_range"))
    for s in doc.get("symbols", [])[:40]:
        print("  SYM", s.get("symbol"), s.get("kind"), s.get("display_name"), [r for r in s.get("relationships", [])])
print("external", len(d.get("external_symbols", [])))
