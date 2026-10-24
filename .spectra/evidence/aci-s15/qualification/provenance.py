"""Independently match official release archive bytes and provision normal SDK cache."""
import hashlib,importlib.metadata,json,subprocess
from pathlib import Path
import tree_sitter_language_pack as pack

out=Path(__file__).resolve().parent
manifest_path=out/'parsers.json';manifest=json.loads(manifest_path.read_text());entry=manifest['platforms']['macos-arm64'];archive=Path('/tmp/aci-s15-parsers-macos-arm64-v1.6.0.tar.zst');sha=lambda b:hashlib.sha256(b).hexdigest()
assert manifest['version']=='1.6.0' and archive.stat().st_size==entry['size'] and sha(archive.read_bytes())==entry['sha256']
member='./libtree_sitter_rust.dylib';raw=subprocess.run(['tar','-xOf',str(archive),member],capture_output=True,check=True,timeout=30).stdout
qualified=Path('/tmp/aci-s15-parser-cache/libtree_sitter_rust.dylib').read_bytes();assert raw==qualified
pack.configure(cache_dir=None);default=Path(pack.cache_dir());before={p.name:sha(p.read_bytes()) for p in default.glob('*') if p.is_file()}
count=pack.download(['rust']);library=default/'libtree_sitter_rust.dylib';assert library.read_bytes()==qualified
for name,value in before.items():assert sha((default/name).read_bytes())==value
language=pack.get_language('rust');assert not pack.get_parser('rust').parse(b'fn actual_rust() {}').root_node.has_error
native=Path(pack.__file__).parent/'_native.abi3.so';lock=out.parents[3]/'mcp-server/uv.lock'
result={'qualified':True,'manifest_uri':'https://github.com/kreuzberg-dev/tree-sitter-language-pack/releases/download/v1.6.0/parsers.json','manifest_sha256':sha(manifest_path.read_bytes()),'platform_asset_uri':entry['url'],'archive_bytes':archive.stat().st_size,'archive_sha256':sha(archive.read_bytes()),'archive_matches_published_manifest':True,'archive_member':member,'sdk_task_cache_bytes_match_official_member':True,'rust_library_bytes':len(raw),'rust_library_sha256':sha(raw),'grammar_semantic_version':language.semantic_version,'abi_version':language.abi_version,'package':importlib.metadata.version('tree-sitter-language-pack'),'tree_sitter':importlib.metadata.version('tree-sitter'),'sdk_native_sha256':sha(native.read_bytes()),'uv_lock_sha256':sha(lock.read_bytes()),'normal_cache':str(default),'normal_cache_new_downloads':count,'previous_cache_files_unchanged':True,'normal_cache_rust_sha256':sha(library.read_bytes()),'normal_cache_languages':pack.downloaded_languages(),'source_commit':None,'source_commit_note':'Official versioned binary identity independently verified; source commit is not asserted by this manifest.'}
(out/'provenance.json').write_text(json.dumps(result,indent=2)+'\n');print(json.dumps(result),flush=True)
