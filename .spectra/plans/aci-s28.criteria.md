### AC-S28-001 (event-driven)
GIVEN one selected uv.lock package entry matches the declared target environment
WHEN the local wheel is accepted
THEN the importer SHALL identify the package by its ecosystem, normalized name, locked version, source identity and verified artifact digest.
VERIFY: test: mcp-server/tests/slices/test_s28_python_dependency.py::test_python_dependency_exact_coordinate

### AC-S28-002 (event-driven)
GIVEN a hash-matching local wheel contains the fixture's supported .py declaration forms
WHEN the dependency surface is indexed
THEN the importer SHALL attach the supported Python declarations to the verified package snapshot.
VERIFY: test: mcp-server/tests/slices/test_s28_python_dependency.py::test_python_dependency_declarations_are_package_scoped

### AC-S28-003 (unwanted-behavior)
GIVEN the supplied wheel digest does not match the selected lock entry
WHEN artifact validation runs
THEN the importer SHALL reject the wheel before publishing package facts.
VERIFY: test: mcp-server/tests/slices/test_s28_python_dependency.py::test_python_dependency_hash_mismatch_rejected

### AC-S28-004 (unwanted-behavior)
GIVEN the declared environment does not select exactly one supported package variant
WHEN lockfile selection runs
THEN the importer SHALL return unresolved_lock_variant.
VERIFY: test: mcp-server/tests/slices/test_s28_python_dependency.py::test_python_dependency_ambiguous_variant_rejected

### AC-S28-005 (event-driven)
GIVEN the same lockfile, target environment, wheel digest and importer version
WHEN the dependency is imported twice
THEN the canonical dependency fact set SHALL remain identical after repeat import.
VERIFY: test: mcp-server/tests/slices/test_s28_python_dependency.py::test_python_dependency_reimport_is_idempotent

### AC-S28-006 (unwanted-behavior)
GIVEN no matching local wheel is supplied
WHEN the operator requests that dependency's surface
THEN the importer SHALL return missing_dependency_source for the selected locked coordinate.
VERIFY: test: mcp-server/tests/slices/test_s28_python_dependency.py::test_python_dependency_missing_source_is_explicit

### AC-S28-007 (ubiquitous)
THEN the dependency importer SHALL obtain source facts exclusively by parsing the supplied local artifacts.
VERIFY: test: mcp-server/tests/slices/test_s28_python_dependency.py::test_python_dependency_import_is_offline_nonexecuting

### AC-S28-RUN-01 (event-driven)
GIVEN the product criteria pass on a recorded output commit and the declared implementation inputs are unchanged
WHEN an executor re-enters this Slice with its verified completion receipt
THEN the tracked implementation content digest SHALL remain unchanged.
VERIFY: gate: capture a SHA-256 manifest of declared tracked implementation files before and after clean-worktree re-entry; require exact manifest equality and git diff --exit-code against the recorded output commit for those paths.

### AC-S28-RUN-02 (event-driven)
GIVEN a partial execution receipt exists for this Slice and the frozen criteria digest still matches
WHEN an executor resumes the interrupted Slice
THEN the slice_id plus criteria_sha256 execution identity SHALL remain unchanged.
VERIFY: gate: compare slice_id and criteria_sha256 in the interrupted and resumed receipts; require one authoritative receipt path for that identity.
