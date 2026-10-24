"""Isolated held-producer measurements; fresh process, then same-process repeat."""
import asyncio, hashlib, json, os, resource, sqlite3, sys, time
from pathlib import Path
ROOT = Path(__file__).resolve().parents[5]
repo = Path(sys.argv[2]).resolve()
mode = sys.argv[1]
if mode == 'server':
    import structlog
    structlog.configure(logger_factory=structlog.PrintLoggerFactory(file=sys.stderr))
    from atlas_aci.config import Config
    from atlas_aci.server import run_stdio
    try:
        asyncio.run(run_stdio(Config(repo=repo, memex_root=Path(sys.argv[3]))))
    finally:
        print('MEASURE_SERVER_RSS_BYTES=' + str(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss), file=sys.stderr, flush=True)
    raise SystemExit
from atlas_aci.codegraph import CodeGraph
held = json.loads((Path(__file__).resolve().parent/'environment.json').read_text())['sources']
assert all(hashlib.sha256((ROOT/path).read_bytes()).hexdigest() == sha for path, sha in held.items())
rows = []
if mode in ('graph', 'source'):
    factory = CodeGraph(repo, read_only=True)
    with sqlite3.connect(factory.db_path.as_uri()+'?mode=ro', uri=True) as conn:
        inputs = json.loads(conn.execute("SELECT value FROM manifest WHERE key='generation_inputs'").fetchone()[0])
    token = inputs['snapshot']['id']
    for ordinal in range(2):
        started = time.perf_counter()
        if mode == 'graph':
            with factory.reader_lease() as reader:
                actual = reader.snapshot_id
        else:
            with factory.source_lease(token) as source:
                actual = source.snapshot_id
        elapsed = time.perf_counter()-started
        assert actual == token
        rows.append({'ordinal':ordinal, 'label':'fresh_process_first_acquisition' if ordinal == 0 else 'same_process_repeat_not_cold_filesystem', 'seconds':elapsed, 'snapshot_id':actual})
    factory.close()
else:
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client
    async def measure():
        parameters = StdioServerParameters(command=sys.executable, args=[str(Path(__file__).resolve()), 'server', str(repo), '/tmp/aci-s08-footprint-memex-'+repo.name], env=dict(os.environ))
        async with stdio_client(parameters) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                for ordinal in range(2):
                    started=time.perf_counter()
                    result=await session.call_tool('search_symbol', {'name':'ACI_S08_ABSENT_MEASUREMENT_SYMBOL'})
                    elapsed=time.perf_counter()-started
                    payload=json.loads(result.content[0].text)
                    assert not result.isError and 'error' not in payload and payload['definitions']==[]
                    rows.append({'ordinal':ordinal, 'label':'first_call_after_initialization' if ordinal == 0 else 'same_server_process_repeat', 'seconds':elapsed, 'result_bytes':len(result.content[0].text.encode()), 'snapshot_id':payload['snapshot_id']})
    asyncio.run(measure())
print(json.dumps({'mode':mode,'repo':repo.name,'measurements':rows,'client_or_direct_process_peak_rss_bytes':resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,'peak_rss_unit':'bytes_on_this_macOS_host','sources':held,'notes':['No uncached-filesystem claim.','Intervals exclude factory setup and stdio initialization. Outer job includes process startup/teardown.','MCP server peak RSS is emitted separately on stderr.']}), flush=True)
