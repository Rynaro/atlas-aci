# S18 CI runtime qualification

Published commit `cd709b7673e2d9ff8a9cda3e8bd2a20ca1d1d0d2` failed
Linux CI dependency setup before tests. The four complete decoded GitHub job
logs are preserved without filtering in gzip files with mtime zero;
`failed-ci-provenance.json` records their source, byte lengths and hashes.
The original COMPLETE receipt and post-output evidence are preserved separately.

All four jobs selected CPython 3.15.1 through `setup-python` reading the open
`>=3.11` project requirement. uv used that same interpreter. The frozen
`rpds-py==0.30.0` build rejected Python 3.15 because its PyO3 0.27.2 supports
through Python 3.14. The container build on Python 3.12 succeeded; its later
host-client dependency setup encountered the same Python 3.15 failure.

Root authorized a separate execution amendment before editing. Only four
`setup-python` inputs changed to `python-version: "3.14"`. The project Python
requirement, lock, dependency versions, container runtime, matrices, conditions,
commands, permissions and product sources are unchanged. `workflow-structure.json`
records decoded YAML equality after exactly those substitutions;
`unchanged-applicability.json` binds all 47 runtime files and 13 producer paths.
The frozen plan, criteria, envelope and DONE planning state are unchanged.

`qualify_python314.py` records actual argv, environment, working directory,
exit, elapsed time and lossless raw logs in `python314-qualification.json`.
The isolated environment uses CPython 3.14.6 on macOS arm64. Frozen sync installed
44 packages; the lock check and real native imports passed. The 20 context
contract and actual SDK stdio tests passed, including old text-only, dual and
negotiated protocol versions. This host qualification does not claim a fresh
Linux run; the successor commit still requires all published CI jobs to pass.

The first local frozen-sync attempt was blocked by sandbox DNS and is retained
as `python314-frozen-sync-network-blocked.*`. Authorized network access populated
the unchanged locked cache, followed by the captured fresh isolated qualification.
The original full 827-test suite, protected 44-test pass² and native image proof
remain applicable to unchanged product inputs; they were not repeated here.
