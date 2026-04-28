# SPECTRA Spec — `atlas-aci-codegraph-build-test`

> Add a regression test that exercises `CodeGraph.build()` end-to-end against a
> minimal in-test fixture, so that an import or wheel regression in
> `tree_sitter_language_pack` (or any other build-time dep) breaks the test
> suite *before* it reaches release.

| Field            | Value                                                                 |
| ---------------- | --------------------------------------------------------------------- |
| Spec ID          | `atlas-aci-codegraph-build-test`                                      |
| Repo             | `Rynaro/atlas-aci`                                                    |
| Working tree     | `/tmp/eidolons-fix/atlas-aci`                                         |
| Target package   | `mcp-server/` (Python, pytest, `asyncio_mode = auto`)                 |
| Status           | `ready-for-implementation`                                            |
| Methodology      | SPECTRA (S→P→E→C→T→R→A)                                                |
| Upstream scout   | atlas-aci#1 (deferred-import bug; ships with no `build()` test)       |
| Downstream sibling | `atlas-aci-ci-hardening` (Docker/CI workflow — explicitly out-of-scope) |

---

## S — Scope / Frame

### Purpose

`CodeGraph.build()` (in `mcp-server/src/atlas_aci/codegraph.py`) is the core
indexing path of the MCP server. It performs a **deferred import** of
`tree_sitter_language_pack.get_parser` *inside the method body* (lines
142–148). Because of that placement:

- `atlas-aci --help`, `atlas-aci tools`, and the existing
  `tests/test_enforcement.py` suite all pass cleanly even when the
  language-pack wheel is broken.
- The failure surface is `atlas-aci index --repo <real-repo>` at runtime,
  i.e. only when a user actually hits production.

A recent release shipped exactly this failure: upstream
`tree-sitter-language-pack 1.6.3` removed the top-level module, and the
production Dockerfile pulled it in via transitive-dep drift. The bug landed
because **no test ever calls `build()`**.

This spec adds that missing test.

### What "in-scope" means here

We are adding a single new regression test file —
`mcp-server/tests/test_codegraph_build.py` — that:

1. Stands up a minimal source fixture (one Python file with a function and
   a class).
2. Instantiates `CodeGraph(repo=<fixture_root>)`.
3. Calls `.build()` and lets the deferred import execute against the **real**
   `tree_sitter_language_pack` resolved by `uv.lock` / `pyproject.toml`.
4. Asserts on the returned stats dict and on the persisted `graph.db`
   contents.

The test must not mock `tree_sitter_language_pack`, must not patch the
deferred import, must not stub out the parser. The **point** is to detect
import/wheel regressions, which means we have to run the import for real.

### Out-of-scope (call out & defer)

| Item | Why out | Where it goes |
| ---- | ------- | ------------- |
| Container/CI workflow that runs the test inside the production Docker image | Different concern (CI plumbing, not test authorship) | Sibling spec `atlas-aci-ci-hardening` |
| Refactoring `codegraph.py` to make the deferred import unit-testable in isolation (e.g. dependency injection of `get_parser`) | We **want** an honest end-to-end exercise; isolating the import would re-introduce the original failure mode | N/A — explicit non-goal |
| Test coverage for `serve` / MCP transport plumbing | Separate concern | Future spec |
| Test coverage for `enforcement.py` | Already exists in `tests/test_enforcement.py` | Done |
| Performance / scale benchmarks for `build()` | Not a regression class we've seen | Future, only if observed |
| Incremental indexing (`since=...` branch of `build()`) | The bug we're guarding against doesn't depend on it; adding it widens scope | Future spec — `atlas-aci-incremental-build-test` |

---

## P — Problem statement

### What broke

`tree-sitter-language-pack 1.6.3` shipped a wheel with the top-level
`tree_sitter_language_pack` module removed. The production Dockerfile had
a transitive-dep-drift hole that pulled it in. Result:

```
$ atlas-aci index --repo /workspace/some-repo
ModuleNotFoundError: No module named 'tree_sitter_language_pack'
```

### Why it slipped past CI

`CodeGraph.build()` defers the import:

```python
# mcp-server/src/atlas_aci/codegraph.py, lines 142–148
def build(self, since: str | None = None) -> dict[str, Any]:
    """Index the repo. Returns stats."""
    try:
        from tree_sitter_language_pack import get_parser
    except ImportError as e:
        log.error("tree_sitter_unavailable", error=str(e))
        raise
    ...
```

That import is never executed by:

- module load (`from atlas_aci.codegraph import CodeGraph` works fine — the
  symbol is referenced only inside `TYPE_CHECKING` guards at module top)
- `__init__` (constructor only sets paths, opens nothing)
- the existing test suite (`tests/test_enforcement.py` exclusively covers
  the read-only enforcement layer)

So the test suite was **structurally incapable** of catching a broken
language-pack wheel.

### Class of bug we are guarding against

Any failure that requires `CodeGraph.build()` to actually parse at least
one source file:

1. Top-level `tree_sitter_language_pack` import broken (the original bug).
2. `get_parser(<lang>)` raising for a language we ostensibly support.
3. `tree_sitter.QueryCursor` API drift (`_extract` also imports it lazily,
   line 213).
4. `get_language(<lang>).query(...)` API drift (line 217).
5. SQLite schema regressions that surface only on first `INSERT`.

A single Python-file fixture exercises all five.

### What "fixed" looks like

- A new test file lands at `mcp-server/tests/test_codegraph_build.py`.
- `pytest mcp-server/tests/` from a clean checkout invokes `build()` for
  real, including the deferred imports.
- If the language-pack wheel breaks again, the test fails at the
  `from tree_sitter_language_pack import get_parser` line with the same
  `ModuleNotFoundError` users would see — but in CI, before release.

---

## E — Examples (acceptance criteria, GIVEN/WHEN/THEN)

> Naming follows pytest conventions; test functions in the spec are
> prescriptive — implementer may rename for clarity but must preserve the
> assertion content.

### AC-1 — `build()` runs end-to-end against a Python fixture

**GIVEN** a `tmp_path` directory containing a single file `sample.py`:

```python
class Greeter:
    def hello(self):
        return greet("world")

def greet(name):
    return f"hi {name}"
```

**AND** a `CodeGraph` instance constructed with `repo=tmp_path` and
`langs=["python"]`

**WHEN** `graph.build()` is called

**THEN** it must return a dict with:
- `stats["files_indexed"] >= 1`
- `stats["symbols"] >= 1`
- `stats["refs"] >= 0` (calls may or may not be captured depending on
  query precision; we only assert the field exists and is an int)

**AND** the file `tmp_path / ".atlas" / "graph.db"` must exist on disk
after the call returns.

### AC-2 — The deferred import actually executes

**GIVEN** the same fixture as AC-1

**WHEN** `graph.build()` is called

**THEN** if `tree_sitter_language_pack` is broken or absent at runtime,
the test must fail with `ImportError` / `ModuleNotFoundError` propagating
out of `build()` — not be skipped, not be xfail'd, not be wrapped.

> This is the load-bearing assertion. The whole reason the test exists is
> to surface that exception in CI. Resist any urge to wrap it in
> `pytest.importorskip` or `try/except` at the test level.

### AC-3 — Persisted symbols are queryable via `search_symbol`

**GIVEN** the fixture and a successful `build()`

**WHEN** `graph.search_symbol("Greeter", kind="class")` is called

**THEN** the result must contain at least one definition whose `path`
ends with `sample.py` and whose `kind == "class"`.

**AND** `graph.search_symbol("greet", kind="function")` must contain at
least one definition with `kind == "function"`.

### AC-4 — Direct SQLite read confirms the schema is populated

**GIVEN** the fixture and a successful `build()`

**WHEN** the test opens `tmp_path / ".atlas" / "graph.db"` with
`sqlite3.connect(...)` directly

**THEN**:
- `SELECT COUNT(*) FROM symbols` must return `>= 1`
- `SELECT COUNT(*) FROM manifest WHERE key='version'` must return `1`
- The schema tables `symbols`, `refs`, `manifest` must all exist
  (`SELECT name FROM sqlite_master WHERE type='table'`)

> AC-3 covers the public query surface; AC-4 covers the persistence
> contract. Both matter — a future refactor could break one without the
> other.

### AC-5 — Unsupported file extensions are silently skipped

**GIVEN** a fixture with `sample.py` *and* `notes.txt` *and* `data.json`

**WHEN** `graph.build()` is called with `langs=["python"]`

**THEN** `stats["files_indexed"] == 1` (only `sample.py` was parsed)

**AND** no parser error is raised for the unsupported files.

> Guards against a regression where `_iter_source_files` over-reports or
> the lang-filter logic in `build()` is inverted.

### AC-6 — Skip patterns are honored

**GIVEN** a fixture with `sample.py` at the root *and* a copy at
`.git/sample.py` *and* a copy at `node_modules/sample.py`

**WHEN** `graph.build()` is called

**THEN** `stats["files_indexed"] == 1` — only the root copy was indexed;
files under `DEFAULT_SKIP_PATTERNS` directories were excluded.

> Guards `_iter_source_files`'s skip-list integration with
> `atlas_aci.config.DEFAULT_SKIP_PATTERNS`.

---

## C — Choices (decision points)

### D-1 — Fixture: in-test `tmp_path` vs checked-in `tests/fixtures/sample-repo/`

**Decision:** **In-test `tmp_path`**, written from string literals at test
start.

**Rationale:**

| Dimension | `tmp_path` (chosen) | Checked-in fixture |
| --------- | ------------------ | ------------------ |
| Locality of intent | Source code of the parsed file lives next to the assertions that depend on it. Reader sees `class Greeter: ...` and `assert "Greeter"` in the same screen. | Reader has to open a separate file to know what the test is checking. |
| Fixture drift risk | Zero — Python literal | Real risk: someone "cleans up" the fixture and silently breaks assertions |
| Setup cost | One `tmp_path / "sample.py"`.write_text(...)` per test | Whole subtree to track in git; LFS questions if it grows |
| Matches existing style | `test_enforcement.py` already uses `tmp_path` (`config` fixture, line 18) and `write_text` (line 21) | Would introduce a new pattern |
| Multi-language parametrization | Trivial — write a `.rb` or `.ts` file from a string | Multiplies the fixture tree |

The checked-in approach only wins if the fixture grows to >~10 files or
needs binary content. We are nowhere near that. **Use `tmp_path`.**

### D-2 — Language coverage: Python-only vs parametrized over multiple languages

**Decision:** **Python is the required floor; Ruby is added as a second
parametrized case; JS/TS are deferred** to a follow-up unless they cost
nothing extra.

**Rationale:**

- The original bug was a top-level `tree_sitter_language_pack` import
  failure — *any* language exercises that path. Python alone is
  sufficient for the primary regression class.
- However, each language binding ships as a **separate compiled
  artifact** inside the language-pack wheel. A future regression could
  break Ruby's binding while Python's keeps working (or vice versa).
  Atlas-aci is explicitly Ruby-friendly (see `codegraph.py` module
  docstring, line 9).
- Adding Ruby as a second parametrized case costs ~5 extra lines (one
  `.rb` fixture string, one parametrize entry) and meaningfully widens
  coverage.
- JS/TS bindings are heavier (the language-pack wraps a JS+TS bundle)
  and the queries in `QUERIES` are fairly thin — adding them now risks
  flakes from Tree-sitter API drift that aren't representative of
  user-facing breakage. Defer.

**Implementation shape:**

```python
@pytest.mark.parametrize(
    "lang, filename, source, expected_symbol",
    [
        ("python", "sample.py",
         "class Greeter:\n    def hello(self):\n        return greet('x')\n\ndef greet(n):\n    return n\n",
         "Greeter"),
        ("ruby", "sample.rb",
         "class Greeter\n  def hello\n    greet('x')\n  end\nend\n\ndef greet(n)\n  n\nend\n",
         "Greeter"),
    ],
)
def test_build_indexes_minimal_fixture(tmp_path, lang, filename, source, expected_symbol):
    ...
```

The Python case must always be present; the Ruby case may be removed by
the implementer if the Ruby `tree-sitter-ruby` binding proves flaky on
CI hardware, **but** the implementer must then open a follow-up issue to
restore it.

### D-3 — Scope discipline: regression test vs de-facto integration test

**Decision:** **Regression test, scoped narrowly.**

The temptation here is to grow this test file into "the integration test
for the indexer" — exercising `search_symbol`, `callers_of`, `query()`'s
DSL, the `since=...` incremental branch, error-path logging, etc. We are
**explicitly not doing that.**

What this test owns:
- `build()` runs end-to-end against a real fixture (AC-1, AC-2)
- `build()`'s output reaches both the public query surface (AC-3) and the
  underlying persistence layer (AC-4)
- `build()`'s file-filtering logic doesn't regress (AC-5, AC-6)

What this test does **not** own:
- `search_symbol`'s edge cases (no fixture, kind filtering edge cases,
  refs-only matches, etc.)
- `query()`'s DSL parsing
- `callers_of` beyond what AC-3 incidentally covers
- The `since=...` branch

If a future regression hits one of those, it gets its own test file.
Naming convention: `tests/test_codegraph_<surface>.py`.

### D-4 — Test file location

**Decision:** `mcp-server/tests/test_codegraph_build.py`.

Matches the existing `mcp-server/tests/test_enforcement.py` placement.
`pyproject.toml`'s `testpaths = ["tests"]` already discovers this path
when pytest is invoked from `mcp-server/`.

### D-5 — Whether to also assert log output

**Decision:** **No** — do not assert on `structlog` output in this spec.

The deferred-import code path logs `tree_sitter_unavailable` on failure,
but asserting on it requires capturing structlog's output, which is
fiddly and orthogonal to "did `build()` work?". If the import fails, the
exception itself is the assertion (AC-2). Log assertion can be added in
a follow-up if telemetry-level guarantees become a contract.

### D-6 — Use of `monkeypatch` / mocking

**Decision:** **No mocking of `tree_sitter_language_pack`,
`tree_sitter`, or any indexing internal.** `monkeypatch` is permitted
**only** for adjusting `cwd` or environment variables if the test needs
them (it shouldn't, given `CodeGraph` takes an explicit `repo` path).

Rationale: see D-3 and the explicit out-of-scope item "Refactoring
`codegraph.py` to make the deferred import testable in isolation." The
whole value of this test is that it's not isolated.

---

## T — Tests / Validation gates

### Gate-1 — Local pytest run, clean checkout

```bash
cd mcp-server
uv sync
uv run pytest tests/test_codegraph_build.py -v
```

**Pass condition:** all parametrized cases green, no `SKIPPED`, no
`XFAILED`, no `XPASSED`. Total runtime < 5s on a developer laptop.

### Gate-2 — Full pytest suite still green

```bash
cd mcp-server
uv run pytest tests/ -v
```

**Pass condition:** existing `tests/test_enforcement.py` is unaffected
(no shared fixtures, no `conftest.py` collisions). The new file adds at
minimum 6 tests (AC-1 through AC-6, possibly multiplied by lang
parametrization).

### Gate-3 — Negative gate: simulate the original bug

This is a manual one-time verification, not part of the committed test
suite. It demonstrates the test would have caught the original failure.

```bash
cd mcp-server
uv run pip uninstall -y tree-sitter-language-pack
uv run pytest tests/test_codegraph_build.py -v
# expected: AC-1 / AC-2 fail with ModuleNotFoundError
uv sync  # restore
```

**Pass condition:** with the dependency removed, the test fails — and it
fails *loudly*, not silently skipped. Document the result in the PR
description.

### Gate-4 — Linting / typing

```bash
cd mcp-server
uv run ruff check tests/test_codegraph_build.py
uv run mypy tests/test_codegraph_build.py   # if mypy is wired into the project
```

**Pass condition:** clean. The test file should follow `from __future__
import annotations` and `pathlib.Path` types like
`test_enforcement.py:7,9` does.

### Gate-5 — `graph.db` byte-level cleanup

The test creates `tmp_path / ".atlas" / "graph.db"`. `tmp_path` is
auto-cleaned by pytest, but the test must **not** leave a connection
open (sqlite locks on Windows would surface here even though we don't
target Windows officially).

**Pass condition:** the test calls `graph.db.close()` (or relies on
`CodeGraph` exposing a teardown — it currently does not, so explicit
close from the test using `graph._db.close()` is acceptable; flag this
in code review as a follow-up to give `CodeGraph` a proper context
manager).

---

## R — Risks / Open questions

### Risk-1 — Tree-sitter binding flakiness in CI

**Risk:** `tree-sitter-language-pack` ships compiled bindings; some CI
runners (musl-libc Alpine, ARM Mac M-series with rosetta gaps) have
historically had wheel availability gaps.

**Mitigation:** the `pyproject.toml` constraint
`tree-sitter-language-pack>=0.3,<1.6.3` (from atlas-aci#1) pins to known
working versions. If the test flakes on a specific runner, that runner is
broken — that's exactly the signal we want.

**Acceptance:** flakiness is a feature here, not a bug. Don't hide it
with retries.

### Risk-2 — Tree-sitter query produces zero symbols

**Risk:** the Python query in `QUERIES["python"]` (lines 52–57) is a
specific s-expression. If Tree-sitter's Python grammar evolves
incompatibly, the query could match nothing → `symbols_added == 0` →
AC-1's `symbols >= 1` fails even though `build()` itself "worked."

**Mitigation:** that's still a real regression we want to catch — the
indexer would be silently producing empty graphs in production. Don't
weaken the assertion.

**Acceptance:** if the query needs to evolve, it's a `codegraph.py`
change (in scope for that file's owner) and the test naturally guards
against an empty-result regression.

### Risk-3 — Cross-language test ordering / state bleed

**Risk:** parametrized tests share a `tmp_path` factory but each
invocation gets its own directory; however, `CodeGraph` writes to
`<repo>/.atlas/graph.db` which is a *file* not a process-global. No
known leak path, but worth noting.

**Mitigation:** each parametrize instance constructs a fresh
`CodeGraph` against its own `tmp_path`. No shared state.

### Open-Q-1 — Should the test also assert on `refs`?

`build()` returns `refs` count and `_extract` populates a `refs` table.
The Python query does capture `(call function: ...)` patterns. We
intentionally only assert `refs >= 0` in AC-1.

**Question to defer to implementer:** is the Python query's call-capture
reliable enough that we can assert `refs >= 1` for the `greet("x")`
inside `Greeter.hello`?

**Recommended default:** keep `refs >= 0` as the spec'd assertion;
implementer is free to **add** a stronger assertion if local runs prove
it stable. Don't downgrade.

### Open-Q-2 — Should `_iter_source_files` be tested directly?

It's currently tested transitively via AC-5 and AC-6. A direct unit test
would tighten the coverage but is a unit-test concern, not a regression
concern.

**Disposition:** out-of-scope for this spec. Capture as a follow-up
ticket if direct coverage becomes valuable.

### Open-Q-3 — Should we add a test for the `since=` branch?

The deferred-import bug doesn't depend on it; testing it widens scope
(see D-3).

**Disposition:** explicit out-of-scope. Future spec
`atlas-aci-incremental-build-test`.

---

## A — Artifacts (deliverables checklist)

The implementer (typically APIVR-Δ or IDG downstream) must produce:

- [ ] **New file:** `mcp-server/tests/test_codegraph_build.py`
  - Implements AC-1 through AC-6
  - Parametrized over at least `python`; preferably also `ruby`
  - No mocking of `tree_sitter_language_pack` / `tree_sitter`
  - Uses `tmp_path` (no checked-in fixture tree)
  - Closes `graph._db` in test teardown
- [ ] **No changes to** `mcp-server/src/atlas_aci/codegraph.py`
- [ ] **No changes to** `mcp-server/pyproject.toml` (the dep constraint
  from atlas-aci#1 is already correct)
- [ ] **No changes to** `mcp-server/tests/test_enforcement.py`
- [ ] **PR description must include:** the manual Gate-3 verification
  result (uninstall language-pack → test fails → reinstall → test
  passes)
- [ ] **PR description must reference:** atlas-aci#1 (the original bug)
  and this spec ID (`atlas-aci-codegraph-build-test`)

### Definition of Done

1. All five validation gates pass (Gate-1, Gate-2, Gate-3, Gate-4,
   Gate-5).
2. The PR description carries the Gate-3 evidence inline.
3. The test file follows the style of `test_enforcement.py`
   (`from __future__ import annotations`, `pathlib.Path` typing,
   pytest fixtures over class-based tests, no `unittest.TestCase`).
4. `bats` / shellcheck not applicable (this is Python).
5. CI on the resulting PR is green.

### Handoff

- **Upstream:** Scout report on the `tree-sitter-language-pack 1.6.3`
  failure (atlas-aci#1).
- **Downstream:** Implementer (APIVR-Δ for test-first execution, or IDG
  for direct composition). Implementer should treat this spec as
  the contract — every AC is a checkbox, every Gate is a CI signal,
  every Decision is locked unless they file an explicit deviation.
- **Sibling:** `atlas-aci-ci-hardening` will pick up Gate-3 and bake it
  into the production Docker build job so the regression class is
  caught even if dev-environment dep resolution diverges from the
  shipped image.
