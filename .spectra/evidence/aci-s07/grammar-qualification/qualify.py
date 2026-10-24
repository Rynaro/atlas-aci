"""Scratch-only pinned native grammar qualification; no product registration."""
import ctypes
import hashlib
import importlib.metadata
import json
import sys
from pathlib import Path

from tree_sitter import Language, Parser, Query
from tree_sitter_language_pack import get_language, get_parser
from atlas_aci.codegraph import CodeGraph, DEFAULT_LANGS, LANG_BY_EXT, QUERIES

ROOT=Path(__file__).resolve().parents[4]
OUT=Path(__file__).resolve().parent
SCRATCH=Path('/tmp/aci-s07-scss-qualification')
library=ctypes.CDLL(str(SCRATCH/'scss.dylib'))
library.tree_sitter_scss.restype=ctypes.c_void_p
capsule=ctypes.pythonapi.PyCapsule_New
capsule.restype=ctypes.py_object
capsule.argtypes=[ctypes.c_void_p,ctypes.c_char_p,ctypes.c_void_p]
language=Language(capsule(library.tree_sitter_scss(),b'tree_sitter.Language',None))
parser=Parser(language)
query=Query(language,QUERIES['scss'])

def errors(tree):
    pending=[tree.root_node];result=[]
    while pending:
        node=pending.pop()
        if node.type=='ERROR' or node.is_missing:
            result.append({'type':node.type,'missing':node.is_missing,'start_byte':node.start_byte,'end_byte':node.end_byte,'start_point':list(node.start_point),'end_point':list(node.end_point)})
        pending.extend(reversed(node.children))
    return result

if sys.argv[1]=='controls':
    import tree_sitter_language_pack as pack
    failed=Path('/tmp/aci-s07-probe-inputs/solidus/backend/app/assets/stylesheets/spree/backend/_bootstrap_custom.scss')
    cases={'exact_failed_source':failed.read_bytes(),'valid_flags':b'$x: 1 !default; $y: 2 !global; .a { color: red !important; }\n','valid_relations':b'@mixin rounded { border-radius: 2px; } .a { @include rounded; }\n','invalid_declaration':b'.a { color: ; @@@ }\n','invalid_unclosed':b'@mixin broken( {\n'}
    results={}
    for name,source in cases.items():
        old=get_parser('scss').parse(source);new=parser.parse(source)
        results[name]={'source_sha256':hashlib.sha256(source).hexdigest(),'bytes':len(source),'original_has_error':old.root_node.has_error,'candidate_has_error':new.root_node.has_error,'candidate_errors':errors(new)}
    # Actual existing extraction, with only the scratch process's grammar
    # accessor changed for scss; original QUERIES and product code unchanged.
    original=pack.get_language
    graph=CodeGraph(SCRATCH,read_only=True)
    original_tree=get_parser('scss').parse(cases['valid_relations'])
    baseline_symbols,baseline_refs,_,_=graph._extract(original_tree,cases['valid_relations'],'control.scss','scss')
    pack.get_language=lambda name: language if name=='scss' else original(name)
    symbols,refs,bindings,rationale=graph._extract(parser.parse(cases['valid_relations']),cases['valid_relations'],'control.scss','scss')
    results['facts']={'definitions':[(s.name,s.kind) for s in symbols],'references':[(r.callee_name,r.relation) for r in refs],'bindings':bindings,'rationale_count':len(rationale)}
    results['abi_version']=language.abi_version
    results['tree_sitter_version']=importlib.metadata.version('tree-sitter')
    results['query_compiled']=True
    (OUT/'controls.json').write_text(json.dumps(results,indent=2)+'\n')
    print(json.dumps(results),flush=True)
    assert not results['exact_failed_source']['candidate_has_error']
    assert not results['valid_flags']['candidate_has_error']
    assert not results['valid_relations']['candidate_has_error']
    assert results['invalid_declaration']['candidate_has_error'] and results['invalid_unclosed']['candidate_has_error']
    assert results['facts']['definitions']==[(s.name,s.kind) for s in baseline_symbols]
    assert results['facts']['references']==[(r.callee_name,r.relation) for r in baseline_refs]
    assert results['facts']['references']==[('rounded','call')]
else:
    name=sys.argv[1];repo=(Path('/tmp/aci-s07-probe-inputs')/name).resolve()
    graph=CodeGraph(repo,read_only=True);parsers={'scss':parser};files=[];counts={}
    for path in graph._iter_source_files():
        lang=LANG_BY_EXT.get(path.suffix)
        if lang not in DEFAULT_LANGS:continue
        source=path.read_bytes();selected=parsers.setdefault(lang,get_parser(lang))
        tree=selected.parse(source)
        item={'path':str(path.relative_to(repo)),'language':lang,'source_sha256':hashlib.sha256(source).hexdigest(),'bytes':len(source),'has_error':tree.root_node.has_error}
        if tree.root_node.has_error:item['errors']=errors(tree)
        files.append(item);counts[lang]=counts.get(lang,0)+1
    result={'repo':name,'languages':list(DEFAULT_LANGS),'counts':counts,'eligible_file_count':len(files),'failed_file_count':sum(f['has_error'] for f in files),'files':files}
    (OUT/(name+'-all-eligible.json')).write_text(json.dumps(result,indent=2)+'\n')
    print(json.dumps({k:v for k,v in result.items() if k!='files'}),flush=True)
