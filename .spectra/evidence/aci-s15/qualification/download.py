"""First checkpoint: locked official SDK download into an isolated task cache."""
import hashlib,importlib.metadata,json,platform,time
from pathlib import Path
import tree_sitter_language_pack as pack

out=Path(__file__).resolve().parent
cache=Path('/tmp/aci-s15-parser-cache')
before=str(pack.cache_dir())
pack.configure(cache_dir=str(cache))
started=time.monotonic()
print(json.dumps({'package':importlib.metadata.version('tree-sitter-language-pack'),'default_cache':before,'task_cache':str(pack.cache_dir()),'before_languages':pack.downloaded_languages()}),flush=True)
count=pack.download(['rust'])
language=pack.get_language('rust');parser=pack.get_parser('rust')
source=b'''struct Thing; enum Choice { A, B }
trait Action { fn run(&self); fn defaulted(&self) {} }
impl Thing { fn new() -> Self { Thing } fn run(&self) { fn nested() {} nested(); } }
impl Action for Thing { fn run(&self) { self.run(); Thing::new(); generic::<u8>(); } }
fn generic<T>() {} fn free() { generic::<u8>(); }
macro_rules! generated { () => { fn hidden() {} } }
generated!();
'''
tree=parser.parse(source)
assert not tree.root_node.has_error
query='''(function_item name: (identifier) @name) @def.function
(function_signature_item name: (identifier) @name) @def.signature
(struct_item name: (type_identifier) @name) @def.struct
(enum_item name: (type_identifier) @name) @def.enum
(trait_item name: (type_identifier) @name) @def.trait
(mod_item name: (identifier) @name) @def.module
(call_expression function: (_) @callee) @call'''
from tree_sitter import Query,QueryCursor
compiled=Query(language,query);matches=QueryCursor(compiled).matches(tree.root_node)
(out/'fixture.rs').write_bytes(source);(out/'query.scm').write_text(query);(out/'tree.txt').write_text(str(tree.root_node)+'\n')
shapes=[]
for _,captures in matches:
 shapes.append({key:[{'type':node.type,'start_byte':node.start_byte,'end_byte':node.end_byte,'text':source[node.start_byte:node.end_byte].decode(),'parent':node.parent.type if node.parent else None} for node in nodes] for key,nodes in captures.items()})
(out/'captures.json').write_text(json.dumps(shapes,indent=2)+'\n')
files=[{'path':str(p),'bytes':p.stat().st_size,'sha256':hashlib.sha256(p.read_bytes()).hexdigest()} for p in cache.rglob('*') if p.is_file()]
result={'new_downloads':count,'elapsed_seconds':time.monotonic()-started,'package':importlib.metadata.version('tree-sitter-language-pack'),'tree_sitter':importlib.metadata.version('tree-sitter'),'platform':platform.platform(),'architecture':platform.machine(),'cache':str(pack.cache_dir()),'downloaded_languages':pack.downloaded_languages(),'has_rust':pack.has_language('rust'),'abi_version':language.abi_version,'semantic_version':language.semantic_version,'language_name':language.name,'root_has_error':tree.root_node.has_error,'capture_match_count':len(matches),'files':files,'source_sha256':hashlib.sha256(source).hexdigest()}
(out/'qualification.json').write_text(json.dumps(result,indent=2)+'\n');print(json.dumps(result),flush=True)
