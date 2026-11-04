# Importing per-test Python coverage observations

`atlas-aci import-coverage` records **which tests were observed executing which
source lines** of the exact snapshot Atlas indexed, from one coverage.py run. It
is an operator CLI command only: nothing is added to the seven read-only MCP
tools, nothing runs a test, a coverage tool or any interpreter during import, and
the observations are **not** reachable through any MCP tool, `graph_query` op or
traversal enum.

> **Observed execution is not an assertion about behavior.** A recorded line means
> "this test's process executed this line"; it says nothing about pass or fail,
> about what was checked, or about what a change would break. Every item the CLI
> shows is labelled `observed_execution`, and no field uses the words covers,
> covered, verified, asserts, passed or proves.

## Qualified producer

Exactly one producer pair is accepted. Widening it needs a newly qualified
fixture (`mcp-server/tests/fixtures/coverage/`).

| Tool | Version | Notes |
| ---- | ------- | ----- |
| coverage.py | 7.16.2 | `coverage json --show-contexts`, JSON `meta.format == 3` |
| pytest-cov | 7.1.0 | `--cov-context=test`, contexts spelled `<nodeid>\|<phase>` |
| pytest | 9.1.1 | phases `setup`, `run`, `teardown` |

Qualified on CPython 3.12 and 3.13 with coverage's default core. `COVERAGE_CORE=sysmon`
is not qualified (coverage itself warns that dynamic contexts may be incomplete).
Both interpreters record the same lines and contexts for the fixture project.

## Workflow

```bash
# 1. Index the repository as usual (structural graph, `build` generation).
atlas-aci index --repo /path/to/repo

# 2. In the TEST environment, from the repository root, with pytest's rootdir equal
#    to the repository root. [run] relative_files = true is required.
pytest --rootdir=. --cov=. --cov-context=test --cov-config=.coveragerc
coverage json --show-contexts -o coverage.json

# 3. Attest the environment. Atlas never spawns your interpreter: pipe the probe to it.
atlas-aci coverage-probe | <venv>/bin/python -I - > environment.json

# 4. Right after the run, record producer-time source identity (outside the repository).
atlas-aci coverage-manifest --repo /path/to/repo --environment environment.json \
    --output atlas-coverage-manifest.json coverage.json

# 5. Import. Validation completes before the writer lock; failure changes nothing.
atlas-aci import-coverage --repo /path/to/repo --manifest atlas-coverage-manifest.json coverage.json
```

`--graph-root` works on `import-coverage` exactly as on `index`. A minimal
`.coveragerc`:

```ini
[run]
relative_files = true
source = .
```

Do **not** set `[run] context =`: a static context prefixes every dynamic context
(`ci|tests/test_x.py::test_a|run`) and is refused. A reproducible, digest-pinned
producer run (Docker `python:3.12-slim` and `python:3.13-slim`, hash-pinned
requirements) lives in `mcp-server/tests/fixtures/coverage/generate.sh`.

### Pytest rootdir

Pytest nodeids are relative to its **rootdir**. If the rootdir is not the
repository root the nodeids name files that do not exist in the repository, and
`coverage-manifest` / `import-coverage` fail with `manifest_incomplete` and a hint
to run pytest with `--rootdir` equal to the repository root.

## The sidecar: `atlas-coverage-manifest.v1`

A coverage report records neither source hashes nor the environment. The sidecar
(canonical JSON) carries: the report's SHA-256, the tool and version, the report
format and context mode, the probe's closed environment attestation
(`atlas-coverage-environment.v1`: Python version/implementation/cache tag,
platform system/machine, coverage/pytest/pytest-cov versions), a fixed
`commit: {"state": "unavailable"}`, the SHA-256 of every measured file and the
SHA-256 of every test file named by a context.

The environment is **attested by the producer, not verified** by Atlas; source
identity is verified by hashes. `coverage-manifest` reads only, writes only its
`--output`, refuses an output inside `--repo`, and refuses symlinks and paths that
leave the repository. The sidecar is byte-deterministic for the same inputs.

## What is recorded

One import is one **run**. `run_id = sha256(canonical({format, artifact_sha256,
manifest_sha256}))` is derived from content, never supplied. Per run there is one
producer record (`fingerprint.coverage`: run id, tool, report format, context
mode, the report's timestamp verbatim, both digests, the environment, the importer
digest). Per observed (test, file, line):

- a `coverage_test` entity named by the pytest nodeid (each parametrized variant
  is a distinct test; the phase is split at the **last** `|`);
- an `execution` occurrence over the exact bytes of that line (terminator
  excluded; lines split only on `\n`, `\r\n`, `\r`, never on form feed or U+2028),
  role `observed_execution:<phases>` with the phases sorted and joined by `+`;
- evidence naming the run's producer;
- one `test_executes` assertion (EXTRACTED) from the test to the file entity.

A line executed in several phases of one test is one assertion. The same test and
line in another run is a second assertion distinct by its evidence and producer.
No `symbols`, `refs` or `edges` rows are written, so `search_symbol`, `callers_of`,
retrieval and the MCP `status` facts are unaffected.

The report's `contexts` keys are the raw recorded lines (real reports record
docstring and continuation lines that `executed_lines` omits); `executed_lines` is
used for counts only.

## Generations, runs and re-import

`import-coverage` publishes a derived `coverage_import` generation over the
current `build` (or an earlier `coverage_import`) generation. Runs accumulate: a
different run adds a second producer and its assertions; re-importing a run that
is already present is a no-op (`status: unchanged`); importing a run whose content
identity is present but whose importer build differs is refused (`run_conflict`,
rebuild first). The result does not depend on import order. `atlas-aci index`
supersedes the generation and drops every coverage record. A JSONL export keeps
the records and drops the run block (the re-imported graph is kind `import`).
Coverage never combines with `import-scip` in either direction.

## Status

`atlas-aci status` gains top-level `execution_observations`: `[]`, or one block
with `format: atlas-execution-observations.v1`, `semantics: observed_execution`,
`assertion_inference: not_performed`, `test_outcome: not_recorded`, the base
generation id, each run (ids, producer id, tool, environment, counts) and counts
derived from published rows. The MCP `status` op is unchanged.

## Rejections

All-or-nothing: the whole artifact is rejected, with a stable code, before the
writer lock and before any staging file exists; a rejection leaves the published
graph byte-identical. No quarantine: a run is one execution of one code state, and
mixing verified and mismatched files would publish observations of code that is not
the snapshot. Scope the measurement with coverage's own `[run] source/omit`.

| Code | Meaning |
| ---- | ------- |
| `report_invalid`, `report_too_large`, `manifest_invalid`, `manifest_too_large`, `environment_invalid` | Not the closed qualified shape (duplicate keys, NaN, malformed fields, bounds) |
| `unsupported_report_format`, `unknown_producer_version`, `producer_mismatch`, `environment_tool_mismatch`, `artifact_hash_mismatch` | Not the qualified producer, or the sidecar does not describe this report |
| `contexts_absent`, `no_test_contexts`, `unsupported_context` | No per-test `<nodeid>\|<phase>` contexts (or a static prefix) |
| `absolute_path`, `path_not_normalized` | Report paths must be normalized repository-relative POSIX |
| `manifest_incomplete`, `manifest_extra` | Sidecar file sets differ from the report (incl. wrong pytest rootdir) |
| `source_mismatch`, `test_source_mismatch`, `document_not_indexed`, `line_out_of_range` | Report does not match the published snapshot |
| `base_kind_unsupported`, `run_conflict`, `run_limit`, `generation_limit`, `too_many_files`, `too_many_tests`, `nodeid_too_long` | Base generation or bounds |

## Bounds

Coverage rows enter every reader lease's snapshot binding and retrieval closure,
so volume is bounded per run and per published generation: at most 50,000
`test_executes` per run, 100,000 per generation, 16 runs, 100,000 tests, 20,000
files, a 64 MiB report and 4,096 bytes per test id. The total of coverage
occurrences, evidence, assertions and tests is bounded too (400,000). Exceeding a
bound is a rejection, not a truncation, and a generation above the bound is
refused at lease time as well.

Measured on a real 150-file repository (slice evidence `cost.json`): a reader
lease on the build took 4.9 s with 152 MB peak RSS; with 99,960 observations in two
runs it took 8.6 s (1.76x) with 396 MB peak RSS, about 2.4 KB of resident memory
per observation. Import time is dominated by full validation (about 2 minutes for
two 50,000-observation runs). Import coverage for a scoped part of the suite
(`[run] source/omit`) rather than everything when a suite is large.

## Limitations

Reported verbatim in the status block: pass/fail is not recorded; execution is not
an assertion about behavior; absence of a line means nothing; lines without a test
context are not attributed; import-time lines are attributed to the active
context; only statement start lines; branch arcs are not imported;
`missing_lines`/`excluded_lines` and coverage's own function/class regions are
ignored; only the qualified coverage.py/pytest-cov pair; the environment is
producer-attested; not traversable and not exposed over MCP; does not combine with
`scip_import`; the commit is not captured; a JSONL round trip keeps the records but
drops the run block.

Symbol diff (`atlas-aci diff`) and change impact (`atlas-aci impact`) ignore
coverage observations: they are not code entities, and a large import never counts
toward the diff entity/occurrence limits.
