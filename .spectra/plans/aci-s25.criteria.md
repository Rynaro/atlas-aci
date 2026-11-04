### AC-S25-001 (event-driven)
GIVEN a supported association declares a literal class_name matching one model
WHEN the model adapter indexes the declaration
THEN the adapter SHALL emit an association relation of the declared kind to the unique explicitly named model.
VERIFY: test: mcp-server/tests/slices/test_s25_rails_models.py::test_rails_explicit_association_target

### AC-S25-002 (event-driven)
GIVEN a model supplies a literal self.table_name or one unambiguous operator mapping
WHEN the model adapter binds its table
THEN the adapter SHALL emit a model_table relation to the explicitly named table.
VERIFY: test: mcp-server/tests/slices/test_s25_rails_models.py::test_rails_explicit_model_table

### AC-S25-003 (event-driven)
GIVEN a migration contains literal create_table, add_column or remove_column statements
WHEN the migration adapter indexes the file
THEN the adapter SHALL emit a migration_mentions fact for each supported literal table operation.
VERIFY: test: mcp-server/tests/slices/test_s25_rails_models.py::test_rails_migration_table_mentions

### AC-S25-004 (unwanted-behavior)
GIVEN an association uses through, polymorphic or a computed class_name
WHEN the adapter encounters that unsupported declaration
THEN the adapter SHALL report unsupported_association for the declaration.
VERIFY: test: mcp-server/tests/slices/test_s25_rails_models.py::test_rails_unsupported_association_is_visible

### AC-S25-005 (ubiquitous)
THEN every Rails model/table relation SHALL retain the producing declaration's source span and snapshot identity.
VERIFY: test: mcp-server/tests/slices/test_s25_rails_models.py::test_rails_model_relation_provenance

### AC-S25-006 (event-driven)
GIVEN identical source snapshot, explicit mapping artifact and adapter version
WHEN the model adapter indexes the same inputs twice
THEN the canonical model/table fact set SHALL remain identical after the repeat index.
VERIFY: test: mcp-server/tests/slices/test_s25_rails_models.py::test_rails_model_reindex_is_idempotent

### AC-S25-007 (ubiquitous)
THEN the model adapter SHALL derive its facts without executing repository or database code.
VERIFY: test: mcp-server/tests/slices/test_s25_rails_models.py::test_rails_models_never_execute_database_code

### AC-S25-RUN-01 (event-driven)
GIVEN the product criteria pass on a recorded output commit and the declared implementation inputs are unchanged
WHEN an executor re-enters this Slice with its verified completion receipt
THEN the tracked implementation content digest SHALL remain unchanged.
VERIFY: gate: capture a SHA-256 manifest of declared tracked implementation files before and after clean-worktree re-entry; require exact manifest equality and git diff --exit-code against the recorded output commit for those paths.

### AC-S25-RUN-02 (event-driven)
GIVEN a partial execution receipt exists for this Slice and the frozen criteria digest still matches
WHEN an executor resumes the interrupted Slice
THEN the slice_id plus criteria_sha256 execution identity SHALL remain unchanged.
VERIFY: gate: compare slice_id and criteria_sha256 in the interrupted and resumed receipts; require one authoritative receipt path for that identity.
