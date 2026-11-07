"""ACI-S26 invariance, validation, invalidation and cap gates: the opt-in jobs adapter changes
nothing it does not own, and its records are validated shape by shape.

G-OFF, G-ONOFF, G-ENVELOPE, G-MCP, G-NOLEAK and G-QUERY over real builds of the jobs fixture,
plus the three-adapter combinations. The base-image comparison (G-IMAGE, G-ROUTES, G-MODELS)
runs outside pytest through ``.spectra/evidence/aci-s26/{compare_graphs.py,g_image.sh}``.
"""

import asyncio
import hashlib
import importlib.util
import json
import re
import shutil
from pathlib import Path

import pytest
import tree_sitter_language_pack as pack
from click.testing import CliRunner
from test_s24_support import PROJECT as ROUTES_PROJECT
from test_s24_support import rails_lines as route_lines
from test_s25_support import MAP_BYTES, model_lines
from test_s25_support import PROJECT as MODELS_PROJECT
from test_s26_support import (
    DAILY,
    ENQUEUER,
    JOBS,
    NIGHTLY,
    PROJECT,
    PROVENANCE,
    REPORT_JOB,
    SPEC,
    arguments_of,
    built,
    copy_db,
    edit,
    enqueues,
    facts,
    file_id,
    job_lines,
    job_records,
    manifest_value,
    method_id,
    one,
    perform_ids,
    planted,
    rebuilt,
    records,
    table_counts,
    workspace,
)

from atlas_aci.__main__ import cli
from atlas_aci.codegraph import KNOWN_EDGE_RELATIONS, CodeGraph
from atlas_aci.config import Config
from atlas_aci.enforcement import READ_ONLY_TOOLS
from atlas_aci.frameworks import REGISTRY, normalize, rails_jobs
from atlas_aci.protocol import results
from atlas_aci.query import admission, impact, impact_admission, paging
from atlas_aci.query.diff import Side
from atlas_aci.server import build_server_state, call_tool_result
from atlas_aci.storage import evidence as ev

BASE_TOOLS_SHA256 = "94f9567a8b00ecaa52d308e52c4c9fa3a4647de260fa63013435a56a4642ac56"
BASE_TOOLS_BYTES = 1631472
COMPARE = Path(__file__).parents[3] / ".spectra/evidence/aci-s26/compare_graphs.py"
RELATIONS2 = {"async_enqueue", "async_argument"}
EXPECTED = PROVENANCE["expected"]
OWNED_RECORDS = EXPECTED["records"]


def inputs_of(graph):
    with graph.reader_lease() as lease:
        return json.loads(
            lease.db.execute("SELECT value FROM manifest WHERE key='generation_inputs'").fetchone()[
                0
            ]
        )


def pair(tmp_path, repo=None):
    repo = repo or workspace(tmp_path)
    _r, on = built(tmp_path, root="on", repo=repo)
    _r, off = built(tmp_path, root="off", repo=repo, frameworks=())
    return repo, on, off


def ids_by_type(rows):
    result: dict[str, set[str]] = {}
    for item in rows:
        result.setdefault(item["type"], set()).add(item["id"])
    return result


def without_snapshot(inputs):
    return {k: v for k, v in inputs.items() if k != "snapshot"}


def parse(text):
    source = text.encode()
    return pack.get_parser("ruby").parse(source), source


def first_node(tree, kind):
    pending = [tree.root_node]
    while pending:
        node = pending.pop()
        if node.type == kind:
            return node
        pending.extend(reversed(node.named_children))
    raise AssertionError(kind)


# ---- vocabularies, limits and regexes ----
def test_vocabularies_are_the_closed_sets():
    m = rails_jobs
    assert m.ENQUEUE_METHODS == {"perform_async": "sidekiq", "perform_later": "active_job"}
    assert {"perform_in", "perform_at"} == m.DELAYED_METHODS
    assert set(m.ENQUEUE_METHODS) | m.DELAYED_METHODS == m.SITE_METHODS and len(m.SITE_METHODS) == 4
    assert {"sidekiq", "active_job"} == m.MECHANISMS
    assert {b"Sidekiq::Worker", b"Sidekiq::Job", b"::Sidekiq::Worker", b"::Sidekiq::Job"} == (
        m.SIDEKIQ_MIXINS
    )
    assert {b"ApplicationJob", b"ActiveJob::Base", b"::ActiveJob::Base"} == m.ACTIVE_JOB_BASES
    assert {
        "dynamic_receiver",
        "set_chain",
        "delayed_enqueue",
        "unbound_namespace",
        "site_limit",
    } == m.ENQUEUE_CONSTRUCTS
    assert m.CALL_ARGUMENT_CONSTRUCTS == (
        "forwarded_arguments",
        "splat",
        "hash_splat",
        "keyword_arguments",
        "block_argument",
        "block",
    )
    assert (
        set(m.CALL_ARGUMENT_CONSTRUCTS)
        | {
            "argument_limit",
            "unsupported_signature",
            "arity_mismatch",
        }
        == m.ARGUMENT_CONSTRUCTS
    )
    assert len(m.ARGUMENT_CONSTRUCTS) == 9
    assert {
        "job_class_not_found",
        "perform_not_found",
        "target_not_recognized_job",
        "ambiguous_job_target",
        "unsupported_argument_mapping",
    } == m.REASONS
    assert {"mapped", "none", "unsupported", "unbound"} == m.ARGUMENT_STATES
    assert {"rails_job_receiver.v1", "rails_job_positional.v1", "unexpanded"} == m.LABELS
    assert (len(m.SITE_METHODS), len(m.MECHANISMS), len(m.ENQUEUE_CONSTRUCTS)) == (4, 2, 5)
    assert (len(m.CALL_ARGUMENT_CONSTRUCTS), len(m.REASONS), len(m.ARGUMENT_STATES)) == (6, 5, 4)
    assert len(m.LABELS) == 3
    assert (m.MAX_FILES, m.MAX_FILE_BYTES, m.MAX_CAPTURE_BYTES) == (10000, 1048576, 67108864)
    assert (m.MAX_SITES, m.MAX_ARGUMENTS, m.MAX_LIMITED_PATHS) == (5000, 16, 10000)
    assert (m.MAX_RECEIVER_BYTES, m.MAX_PARAM_NAME_BYTES) == (256, 128)
    assert m.MAX_ASSERTIONS == (m.MAX_SITES + 1) * (1 + m.MAX_ARGUMENTS)
    assert m.CLASS_NAME_RE.pattern == r"(::)?[A-Z][A-Za-z0-9_]*(::[A-Z][A-Za-z0-9_]*)*"
    assert m.JOB_KEY_RE.pattern == r"[A-Z][A-Za-z0-9_]*(::[A-Z][A-Za-z0-9_]*)*"
    assert m.PARAM_RE.pattern == r"[a-z_][A-Za-z0-9_]*"
    assert m.CAPTURE_RE.search(b"x.perform_now(1)") is None
    assert m.CAPTURE_RE.search(b"x.perform_inline(1)") is not None  # harmless over-capture
    assert m.CAPTURE_RE.search(b"def  perform(a)") is not None
    assert m.CAPTURE_RE.search(b"def perform_now") is not None  # harmless over-capture
    assert m.CAPTURE_RE.search(b"class A; end") is None
    assert m.MANIFEST_FORMAT == "atlas-rails-jobs.v1" and m.ADAPTER == "rails-jobs.v1"
    assert (ev.RAILS_JOB_KIND, ev.RAILS_JOB_ROLE_PREFIX) == ("rails_job", "rails_job:")
    assert ev.RAILS_JOBS_ADAPTER == "rails-jobs.v1"
    assert ev.RAILS_JOB_RELATIONS == RELATIONS2
    assert set(REGISTRY) == {"rails", "rails_jobs", "rails_models"}
    assert normalize("rails_jobs", ["ruby"]) == ("rails_jobs",)
    with pytest.raises(ValueError, match="requires language ruby"):
        normalize(["rails_jobs"], ["python"])
    with pytest.raises(ValueError, match="Unknown framework adapter"):
        normalize("django", ["ruby"])


# ---- pure units ----
def parameters(text):
    tree, source = parse(text)
    return rails_jobs.signature(source, first_node(tree, "method"))


def test_signature_of_every_parameter_shape():
    sig = rails_jobs.Signature
    assert parameters("def perform(a, b)\nend") == sig(True, ("a", "b"), 2, 0)
    assert parameters("def perform a, b\nend") == sig(True, ("a", "b"), 2, 0)  # parenless
    assert parameters("def perform(a, b = 1)\nend") == sig(True, ("a", "b"), 1, 1)
    assert parameters("def perform(a = 1, b = 2)\nend") == sig(True, ("a", "b"), 0, 2)
    assert parameters("def perform\nend") == sig(True, (), 0, 0)
    assert parameters("def perform()\nend") == sig(True, (), 0, 0)
    # Tolerated and ignored: a call that maps never passes keywords or a block.
    assert parameters("def perform(a, k: 1)\nend") == sig(True, ("a",), 1, 0)
    assert parameters("def perform(a, &blk)\nend") == sig(True, ("a",), 1, 0)
    assert parameters("def perform(a, **opts)\nend") == sig(True, ("a",), 1, 0)
    assert parameters("def perform(a, **nil)\nend") == sig(True, ("a",), 1, 0)  # hash_splat_nil
    unsupported = rails_jobs.UNSUPPORTED_SIGNATURE
    for text in (
        "def perform(a, b = 1, c)\nend",  # required after optional
        "def perform(*args)\nend",
        "def perform(a, *rest)\nend",
        "def perform((a, b), c)\nend",  # destructured_parameter
        "def perform(...)\nend",  # forward_parameter
        "def perform(a, b:)\nend",  # required keyword
        "def perform(a, b = 1, *c, d:, e: 2, **f, &g)\nend",
    ):
        assert parameters(text) == unsupported, text
    assert not unsupported.supported
    assert parameters("def perform(" + "a" * 129 + ")\nend") == unsupported
    assert parameters("def perform(é)\nend") == unsupported  # not a plain ASCII name


def call_of(text):
    tree, source = parse(text)
    return first_node(tree, "call"), source


def test_call_arguments_precedence_and_spans():
    arguments = rails_jobs.call_arguments
    call, _ = call_of("Foo.perform_async(1, 2)")
    found = arguments(call)
    assert found.count == 2 and found.construct is None and len(found.positional) == 2
    call, _ = call_of("Foo.perform_async")
    found = arguments(call)
    assert found.count == 0 and found.positional == () and found.construct is None
    assert (found.list_span.start_byte, found.list_span.end_byte) == (0, 17)  # the call itself
    cases = {
        "Foo.perform_async(*a, **b, k: 1)": "splat",
        "Foo.perform_async(**b, k: 1)": "hash_splat",
        "Foo.perform_async(1, k: 1)": "keyword_arguments",
        "Foo.perform_async(1, :k => 1)": "keyword_arguments",
        "Foo.perform_async(1, &c)": "block_argument",
        "Foo.perform_async(1) { |x| x }": "block",
        "Foo.perform_async(*a) { |x| x }": "splat",
        "Foo.perform_async(&c, *a)": "splat",
    }
    for text, construct in cases.items():
        call, _ = call_of(text)
        assert arguments(call).construct == construct, text
    call, _ = call_of("def f(...)\n  Foo.perform_async(...)\nend")
    tree, source = parse("def f(...)\n  Foo.perform_async(...)\nend")
    assert arguments(first_node(tree, "call")).construct == "forwarded_arguments"
    # A braced hash and a heredoc opener are positional; comments are not arguments.
    tree, source = parse("Foo.perform_async({ a: 1 }, <<~X, 3) # c\n  body\nX\n")
    found = arguments(first_node(tree, "call"))
    assert found.count == 3 and found.construct is None
    assert [source[s.start_byte : s.end_byte] for s in found.positional] == [
        b"{ a: 1 }",
        b"<<~X",
        b"3",
    ]
    tree, source = parse("Foo.perform_async(1, # c\n  2)")
    assert arguments(first_node(tree, "call")).count == 2
    # Only the first MAX_ARGUMENTS positional spans are kept, the count is complete.
    many = "Foo.perform_async(" + ", ".join(str(i) for i in range(20)) + ")"
    found = arguments(call_of(many)[0])
    assert found.count == 20 and len(found.positional) == rails_jobs.MAX_ARGUMENTS


def test_classification_of_receivers():
    def classified(text, method):
        call, source = call_of(text)
        return rails_jobs.classify(source, call, method)

    assert classified("Foo.perform_async(1)", "perform_async") == (None, "Foo")
    assert classified("A::B.perform_later(1)", "perform_later") == (None, "A::B")
    assert classified("::A.perform_later(1)", "perform_later") == (None, "::A")
    assert classified("Foo.perform_in(3, 1)", "perform_in") == ("delayed_enqueue", None)
    assert classified("perform_async(1)", "perform_async") == ("dynamic_receiver", None)
    assert classified("foo.perform_async(1)", "perform_async") == ("dynamic_receiver", None)
    assert classified("self.class.perform_async(1)", "perform_async") == ("dynamic_receiver", None)
    assert classified("foo::Bar.perform_async(1)", "perform_async")[0] == "dynamic_receiver"
    assert classified('"Foo".constantize.perform_later(1)', "perform_later")[0] == (
        "dynamic_receiver"
    )
    assert classified("Foo.set(wait: 1).perform_later(1)", "perform_later") == ("set_chain", None)
    assert classified("foo.set(wait: 1).perform_later(1)", "perform_later")[0] == (
        "dynamic_receiver"
    )
    long = "A" + "b" * rails_jobs.MAX_RECEIVER_BYTES
    assert classified(long + ".perform_async(1)", "perform_async")[0] == "dynamic_receiver"


def nesting_of(text, namespace):
    tree, source = parse(text)
    pending = [tree.root_node]
    target = None
    while pending:
        node = pending.pop()
        if node.type == "call":
            method = node.child_by_field_name("method")
            if (
                method is not None
                and source[method.start_byte : method.end_byte] == b"perform_async"
            ):
                target = node
        pending.extend(node.named_children)
    assert target is not None
    return rails_jobs.nesting(source, target, namespace)


def test_lexical_nesting_and_candidates():
    site = "    Foo.perform_async(1)\n"
    assert nesting_of("Foo.perform_async(1)\n", "") == ()
    assert nesting_of(
        "module Billing\n  class Nightly\n" + site + "  end\nend\n", "Billing::Nightly"
    ) == (
        "Billing::Nightly",
        "Billing",
    )
    # A top-level compact name is bound; a compact name with an enclosing scope is not.
    assert nesting_of("class A::B\n" + site + "end\n", "A::B") == ("A::B",)
    assert (
        nesting_of(
            "module Outer\n  class Inner::Thing\n" + site + "  end\nend\n", "Outer::Inner::Thing"
        )
        is None
    )
    assert nesting_of("module A\n  module B::C\n" + site + "  end\nend\n", "A::B::C") is None
    assert nesting_of("class ::Rooted\n" + site + "end\n", "::Rooted") is None
    assert nesting_of("class A::B\n  module C\n" + site + "  end\nend\n", "A::B::C") == (
        "A::B::C",
        "A::B",
    )
    assert nesting_of("class A\n" + site + "end\n", "Other") is None  # the namespace differs
    assert nesting_of("class A\n" + site + "end\n", "") is None
    assert nesting_of("Foo.perform_async(1)\n", "A") is None
    assert nesting_of(
        "class A\n  class << self\n    def x\n" + site + "    end\n  end\nend\n", "A"
    ) == ("A",)
    candidates = rails_jobs.candidates
    assert candidates((), "R") == ["R"]  # an empty nesting: the receiver itself, never `::R`
    assert candidates(("A::B", "A"), "R") == ["A::B::R", "A::R", "R"]
    assert candidates(("A::B", "A"), "::R") == ["R"]
    assert candidates(("R",), "R") == ["R::R", "R"]
    assert candidates(("A",), "B::C") == ["A::B::C", "B::C"]


def test_class_scan_reads_only_direct_statements():
    text = (
        "class A < ApplicationJob\n  def perform(x); end\n  def self.perform(y); end\n"
        "  class << self\n    def perform(z); end\n  end\nend\n"
        "class B\n  include Sidekiq::Job\n  def perform; end\n  def perform(a); end\nend\n"
        "class C\n  include(::Sidekiq::Worker)\nend\n"
        "class D\n  include Comparable\n  if true\n    include Sidekiq::Job\n"
        "    def perform; end\n  end\nend\n"
        "class E < ::ActiveJob::Base\nend\n"
        "class F < ReportJob\nend\n"
        "module M\n  class G::H < ApplicationJob\n  end\nend\n"
    )
    tree, source = parse(text)
    found = rails_jobs.scan_classes(tree, source)
    by_name = {}
    for item in found:
        name = source[item.span.start_byte : item.span.end_byte].split(b"\n")[0].decode()
        by_name[name] = item
    a, b, c, d, e, f, g = (
        by_name["class A < ApplicationJob"],
        by_name["class B"],
        by_name["class C"],
        by_name["class D"],
        by_name["class E < ::ActiveJob::Base"],
        by_name["class F < ReportJob"],
        by_name["  class G::H < ApplicationJob".strip()],
    )
    assert a.mechanisms == {"active_job"} and len(a.performs) == 1  # singleton forms never count
    assert b.mechanisms == {"sidekiq"} and len(b.performs) == 2
    assert c.mechanisms == {"sidekiq"} and c.performs == []
    assert d.mechanisms == set() and d.performs == []  # nothing inside a conditional is read
    assert e.mechanisms == {"active_job"} and f.mechanisms == set()
    assert g.compact and g.mechanisms == {"active_job"}
    assert not a.compact


# ---- G-ENVELOPE ----
def test_envelope_minimums_and_relation_sets_are_the_base_values():
    assert admission.MINIMUM_BUDGET == 3436
    assert paging.COLLECTION_MINIMUM_BUDGET == 3448
    assert results.FIXED_ERROR_ENVELOPE_CAP == 797
    assert impact_admission.IMPACT_MINIMUM_BUDGET == 7730
    base = {"call", "construct", "import", "superclass", "include", "extend", "prepend"}
    assert base | {"rationale_for"} == ev.RELATIONS and len(ev.RELATIONS) == 8
    assert ev.RELATIONS | {"jsx", "reference"} == ev.REFERENCE_RELATIONS
    assert (ev.RELATIONS - {"rationale_for"}) | {"reference"} == ev.EDGE_RELATIONS
    for name in RELATIONS2:
        for relations in (ev.RELATIONS, ev.REFERENCE_RELATIONS, ev.EDGE_RELATIONS):
            assert name not in relations
        assert name not in KNOWN_EDGE_RELATIONS
        assert name in ev.ASSERTION_RELATIONS
    assert (
        ev.REFERENCE_RELATIONS
        | {"test_executes", "routes_to", "association", "model_table", "migration_mentions"}
        | RELATIONS2
        == ev.ASSERTION_RELATIONS
    )
    # There is no target-less carve-out for the new relations.
    assert not RELATIONS2 & ev.RAILS_TARGETLESS_RELATIONS


@pytest.mark.parametrize("relation", sorted(RELATIONS2))
def test_resolved_job_relations_always_name_their_target(relation):
    source, proof, raw = "a" * 64, "b" * 64, ev.canonical({"mapping": "unexpanded"})
    for resolution in ("EXTRACTED", "INFERRED"):
        with pytest.raises(ValueError):
            ev.validate_record(ev.assertion(source, None, relation, proof, resolution, raw))
    ev.validate_record(ev.assertion(source, "c" * 64, relation, proof, "INFERRED", raw))
    with pytest.raises(ValueError, match=relation + " assertion"):
        ev.validate_record(ev.assertion(source, "c" * 64, relation, proof, "EXTRACTED", raw))
    ambiguous = ev.assertion(
        source,
        None,
        relation,
        proof,
        "AMBIGUOUS",
        raw,
        ["c" * 64, "d" * 64],
        "ambiguous_job_target",
    )
    if relation == "async_enqueue":
        ev.validate_record(ambiguous)
    else:
        with pytest.raises(ValueError, match="async_argument assertion"):
            ev.validate_record(ambiguous)
    ev.validate_record(
        ev.assertion(source, None, relation, proof, "UNRESOLVED", raw, (), "job_class_not_found")
    )


# ---- G-OFF ----
def test_a_build_without_the_flag_has_no_job_trace(tmp_path):
    repo, _on, off = pair(tmp_path)
    assert records(off) and job_records(off) == []
    for item in records(off):
        assert item.get("kind") != "rails_job" and item.get("relation") not in RELATIONS2
        assert "rails_jobs" not in (item.get("fingerprint") or {})
    assert manifest_value(off) is None
    assert "rails_jobs" not in json.dumps(inputs_of(off))
    for frameworks in (("rails",), ("rails_models",)):
        _r, other = built(tmp_path, root="other-" + frameworks[0], repo=repo, frameworks=frameworks)
        assert job_records(other) == [] and manifest_value(other) is None


def test_a_repository_without_jobs_builds_identically_on_and_off(tmp_path):
    repo = workspace(tmp_path)
    for path in sorted(repo.rglob("*.rb")):
        text = path.read_text()
        for word in ("perform_async", "perform_later", "perform_in", "perform_at"):
            text = text.replace(word, "x_" + word)
        path.write_text(text)
    for path in sorted(repo.rglob("*.rb")):
        if "ApplicationJob" in path.read_text() or "Sidekiq" in path.read_text():
            path.write_text(
                path.read_text()
                .replace("Sidekiq", "Wrapped")
                .replace("ApplicationJob", "ApplicationJobx")
            )
    _repo, on, off = pair(tmp_path, repo)
    assert inputs_of(on) == inputs_of(off) and inputs_of(on)["snapshot"]["state"] == "available"
    assert [r["id"] for r in records(on)] == [r["id"] for r in records(off)]
    summary = json.loads(manifest_value(on))
    assert summary["producer_id"] is None and summary["sites"]["located"] == 0
    assert summary["facts"] == {
        "async_argument": {"INFERRED": 0, "UNRESOLVED": 0},
        "async_enqueue": {"AMBIGUOUS": 0, "INFERRED": 0, "UNRESOLVED": 0},
    }
    assert manifest_value(off) is None


# ---- G-ONOFF ----
def test_on_minus_job_owned_equals_off_for_every_record_type(tmp_path):
    _repo, on, off = pair(tmp_path)
    owned = rails_jobs.record_ids(records(on))
    assert len(owned) == 3 * OWNED_RECORDS["assertion"] + OWNED_RECORDS["producer"]
    on_ids, off_ids = ids_by_type(records(on)), ids_by_type(records(off))
    assert set(on_ids) == set(off_ids)
    for kind, found in on_ids.items():
        assert found - owned == off_ids[kind], kind
    assert {k: len(v & owned) for k, v in on_ids.items() if v & owned} == OWNED_RECORDS
    with on.reader_lease() as a, off.reader_lease() as b:
        projections_on = ids_by_type(on._projection_records(a.db))
        projections_off = ids_by_type(off._projection_records(b.db))
    assert projections_on == projections_off and projections_on["projection"]
    assert table_counts(on) == table_counts(off) and any(table_counts(on).values())
    on_inputs, off_inputs = inputs_of(on), inputs_of(off)
    assert without_snapshot(on_inputs) == without_snapshot(off_inputs)
    assert on_inputs["snapshot"] != off_inputs["snapshot"]  # the snapshot binds the new records
    assert on_inputs["retrieval"]["document_digest"] == off_inputs["retrieval"]["document_digest"]
    assert "rails_jobs" not in json.dumps(on_inputs)
    on_producers = [r for r in records(on) if r["type"] == "producer"]
    off_producers = [r for r in records(off) if r["type"] == "producer"]
    assert len(on_producers) == len(off_producers) + 1


def test_incremental_toggle_stays_affected_and_matches_fresh_builds(tmp_path):
    repo, on, off = pair(tmp_path)
    graph, _stats = rebuilt(repo, tmp_path, "toggle", since="x")  # first build in this root
    assert job_records(graph)
    for frameworks in ((), JOBS, (), JOBS):
        graph, stats = rebuilt(repo, tmp_path, "toggle", frameworks=frameworks, since="x")
        resolution = stats["binding_resolution"]
        assert resolution["mode"] == "affected", (frameworks, resolution)
        assert resolution["reason"] == "complete_dependency_match", (frameworks, resolution)
        assert resolution["affected_units"] == 0, (frameworks, resolution)
        expected = on if frameworks else off
        assert [r["id"] for r in records(graph)] == [r["id"] for r in records(expected)]
        assert (manifest_value(graph) is not None) == bool(frameworks)


# ---- three adapters together ----
def merged(tmp_path):
    repo = workspace(tmp_path, "merged")
    shutil.copytree(ROUTES_PROJECT, repo, dirs_exist_ok=True)
    shutil.copytree(MODELS_PROJECT, repo, dirs_exist_ok=True)
    return repo


EVERY = ("rails", "rails_jobs", "rails_models")


def adapters(repo, tmp_path, root, frameworks, since=None, primer=None):
    graph = CodeGraph(
        repo,
        graph_root=tmp_path / root,
        frameworks=frameworks,
        rails_table_map=MAP_BYTES if "rails_models" in frameworks else None,
    )
    if primer is not None and not (graph.atlas_dir / "extraction-cache").exists():
        shutil.copytree(primer.atlas_dir / "extraction-cache", graph.atlas_dir / "extraction-cache")
    stats = graph.build(since=since)
    return graph, stats


@pytest.fixture(scope="module")
def combined(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("s26-merged")
    repo = merged(tmp)
    everything, _stats = adapters(repo, tmp, "all", EVERY)
    result = {"repo": repo, "tmp": tmp, "all": everything}
    for key in EVERY:
        result[key], _stats = adapters(repo, tmp, key, (key,), primer=everything)
    result["off"], _stats = adapters(repo, tmp, "off", (), primer=everything)
    return result


def test_three_adapters_coexist_and_each_owns_only_its_records(combined):
    everything = combined["all"]
    assert job_lines(everything) == job_lines(combined["rails_jobs"]) and job_lines(everything)
    assert route_lines(everything) == route_lines(combined["rails"]) and route_lines(everything)
    assert model_lines(everything) == model_lines(combined["rails_models"])
    assert model_lines(everything)
    for key, manifest in (
        ("rails_jobs", "rails_jobs"),
        ("rails", "rails_routes"),
        ("rails_models", "rails_models"),
    ):
        assert manifest_value(everything, manifest) == manifest_value(combined[key], manifest)
    # Each adapter alone leaves no key of the others.
    assert manifest_value(combined["rails_jobs"], "rails_routes") is None
    assert manifest_value(combined["rails_jobs"], "rails_models") is None
    assert (
        manifest_value(combined["rails"]) is None
        and manifest_value(combined["rails_models"]) is None
    )
    from atlas_aci.frameworks import rails_models, rails_routes

    everything_records = records(everything)
    owned = [
        rails_routes.record_ids(everything_records),
        rails_jobs.record_ids(everything_records),
        rails_models.record_ids(everything_records),
    ]
    assert all(owned) and not (owned[0] & owned[1] or owned[0] & owned[2] or owned[1] & owned[2])
    producers = [r for r in everything_records if r["type"] == "producer"]
    keys = [set(p["fingerprint"]) for p in producers if p["id"] in set().union(*owned)]
    assert sorted(keys, key=sorted) == [{"rails"}, {"rails_jobs"}, {"rails_models"}]
    # The union is the only addition over a build without adapters.
    assert (
        ids_by_type(everything_records)["assertion"] - set().union(*owned)
        == ids_by_type(records(combined["off"]))["assertion"]
    )


def test_toggling_the_jobs_adapter_leaves_the_others_byte_identical(combined):
    repo, tmp = combined["repo"], combined["tmp"]
    everything = combined["all"]
    routes, models, jobs = (
        route_lines(everything),
        model_lines(everything),
        job_lines(everything),
    )
    assert routes and models and jobs
    for frameworks in (("rails", "rails_models"), EVERY, ("rails_jobs",), EVERY):
        graph, stats = adapters(repo, tmp, "swap", frameworks, since="x", primer=everything)
        assert stats["binding_resolution"]["mode"] in {"affected", "full"}
        assert route_lines(graph) == (routes if "rails" in frameworks else [])
        assert model_lines(graph) == (models if "rails_models" in frameworks else [])
        assert job_lines(graph) == (jobs if "rails_jobs" in frameworks else [])
        assert (manifest_value(graph) is not None) == ("rails_jobs" in frameworks)
    final, stats = adapters(repo, tmp, "swap", EVERY, since="x", primer=everything)
    assert stats["binding_resolution"]["mode"] == "affected"
    assert job_lines(final) == jobs and route_lines(final) == routes
    assert model_lines(final) == models


def load_compare():
    spec = importlib.util.spec_from_file_location("compare_graphs_s26", COMPARE)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_compare_graphs_separates_the_three_adapters_and_excludes_their_union(combined):
    module = load_compare()
    loaded = {key: module.load(str(combined[key].db_path)) for key in (*EVERY, "all", "off")}
    data, info = loaded["all"]
    adapters_info = info["adapters"]
    assert set(adapters_info) == {"rails", "rails_models", "rails_jobs"}
    for key in adapters_info:
        assert adapters_info[key]["records"]["occurrences"] > 0, key
        assert (
            adapters_info[key]["manifest_present"] and len(adapters_info[key]["producer_ids"]) == 1
        )
    assert adapters_info["rails_jobs"]["records"]["occurrences"] == OWNED_RECORDS["occurrence"]
    sets = [set(adapters_info[k]["occurrence_ids"]) for k in sorted(adapters_info)]
    assert not (sets[0] & sets[1] or sets[0] & sets[2] or sets[1] & sets[2])
    plain, _plain_info = loaded["off"]
    assert data["occurrence_ids"] == plain["occurrence_ids"]
    assert data["assertions"] == plain["assertions"]
    pairs, checks = module.jobs_pair([loaded["rails_jobs"], loaded["all"]])
    assert all(p["ok"] for p in pairs) and all(checks.values()), checks
    # The G-ROUTES and G-MODELS pairs also demand that rails_jobs is absent on both sides.
    pairs, checks = module.routes_pair([loaded["rails"], loaded["rails"]])
    assert checks["candidate_has_no_rails_jobs"] and checks["reference_has_no_rails_jobs"]
    assert all(checks.values()) and all(p["ok"] for p in pairs)
    pairs, checks = module.models_pair([loaded["rails_models"], loaded["rails_models"]])
    assert checks["candidate_has_no_rails_jobs"] and all(checks.values())
    three = module.three_way([loaded["off"], loaded["off"], loaded["all"]])
    assert all(three[1][f"{key}_exactly_one_producer"] for key in adapters_info)
    # A jobs-carrying database is not a valid G-ROUTES reference.
    assert not module.routes_pair([loaded["rails_jobs"], loaded["rails_jobs"]])[1][
        "reference_has_no_rails_jobs"
    ]


# ---- G-MCP ----
def test_the_tool_surface_is_byte_identical_and_read_only(tmp_path):
    assert len(READ_ONLY_TOOLS) == 7
    assert not any("rails" in name or "job" in name for name in READ_ONLY_TOOLS)
    out = CliRunner().invoke(
        cli, ["tools", "--repo", str(workspace(tmp_path))], catch_exceptions=False
    )
    assert out.exit_code == 0
    raw = out.stdout.encode()
    assert len(raw) == BASE_TOOLS_BYTES and hashlib.sha256(raw).hexdigest() == BASE_TOOLS_SHA256
    for name in ("rails_jobs", "async_enqueue", "async_argument", "rails_job"):
        assert name not in out.stdout
    assert "rails_jobs" in next(
        p.help for p in cli.commands["index"].params if p.name == "frameworks"
    )
    assert "frameworks" not in {p.name for p in cli.commands["serve"].params}
    assert "frameworks" not in {p.name for p in cli.commands["tools"].params}


# ---- G-NOLEAK ----
GENERATION_DERIVED = {
    "generation_id",
    "snapshot_id",
    "id",
    "manifest_sha256",
    "uri",
    "query_digest",
    "retrieval_record_id",
}


def scrub(value):
    """Drop generation/snapshot-derived identities, which legitimately differ between graphs
    whose snapshots bind different record sets. Entity ids and every content field stay."""
    if isinstance(value, dict):
        return {
            k: len(v) if k == "source_resource_ids" else scrub(v)
            for k, v in value.items()
            if k not in GENERATION_DERIVED
        }
    if isinstance(value, list):
        return [scrub(v) for v in value]
    return value


def first_difference(left, right, path="$"):
    """Iterative diagnostic: the first JSON path where two documents differ."""
    pending = [(left, right, path)]
    while pending:
        a, b, where = pending.pop()
        if isinstance(a, dict) and isinstance(b, dict):
            if set(a) != set(b):
                return f"{where}: keys {sorted(set(a) ^ set(b))}"
            pending.extend((a[k], b[k], f"{where}.{k}") for k in sorted(a, reverse=True))
        elif isinstance(a, list) and isinstance(b, list) and len(a) == len(b):
            pending.extend(
                (x, y, f"{where}[{i}]") for i, (x, y) in enumerate(zip(a, b, strict=True))
            )
        elif a != b:
            return f"{where}: {str(a)[:120]} != {str(b)[:120]}"
    return "equal"


def server(tmp_path, repo, root):
    config = Config(
        repo=repo, memex_root=tmp_path / "memex", max_calls_per_minute=0, graph_root=root
    )
    enforcement, memex, factory = build_server_state(config)
    return config, enforcement, memex, factory


async def graph_call(state, **arguments):
    config, enforcement, memex, factory = state
    return await call_tool_result(
        "graph_query",
        {"schema_version": "atlas.graph.v1", **arguments},
        config,
        enforcement,
        memex,
        factory,
    )


def test_mcp_neighbors_and_context_are_equal_on_and_off(tmp_path):
    repo, on, _off = pair(tmp_path)
    seeds = [
        method_id(on, ENQUEUER, "run"),
        perform_ids(on, "ReportJob")[0],
        perform_ids(on, "HardWorker")[0],
        file_id(SPEC),
    ]
    states = [server(tmp_path, repo, tmp_path / "on"), server(tmp_path, repo, tmp_path / "off")]

    async def run():
        collected: list[list] = [[], []]
        try:
            for slot, state in enumerate(states):
                for seed in seeds:
                    for direction in ("incoming", "outgoing"):
                        result = await graph_call(
                            state,
                            op="neighborhood",
                            seed={"entity_id": seed},
                            direction=direction,
                            max_depth=2,
                            deadline_ms=2000,
                        )
                        assert not result.isError, result
                        collected[slot].append(result.content[0].text)
                for text in ("ReportJob", "perform_async", "HardWorker"):
                    retrieved = await graph_call(state, op="retrieve", text=text)
                    assert not retrieved.isError, retrieved
                    collected[slot].append(json.dumps(retrieved.structuredContent))
                    context = await graph_call(
                        state, op="context", text=text, source_policy="references"
                    )
                    assert not context.isError, context
                    collected[slot].append(context.content[0].text)
                found = await call_tool_result(
                    "search_symbol", {"name": "perform"}, state[0], state[1], state[2], state[3]
                )
                collected[slot].append(found.content[0].text)
        finally:
            for state in states:
                state[3].close()
        return collected

    with_jobs, without = asyncio.run(run())
    assert len(with_jobs) == len(without) > 8
    for a, b in zip(with_jobs, without, strict=True):
        for name in ("rails_job", "async_enqueue", "async_argument", "rails_job_receiver"):
            assert name not in a
        assert scrub(json.loads(a)) == scrub(json.loads(b)), first_difference(
            scrub(json.loads(a)), scrub(json.loads(b))
        )
    assert any("ReportJob" in text for text in with_jobs)
    # Traversal from the enqueuing method never reaches a perform through the new records.
    for text, other in zip(with_jobs[:2], without[:2], strict=True):
        assert text == other or scrub(json.loads(text)) == scrub(json.loads(other))


# ---- G-QUERY ----
def test_change_set_and_admission_arithmetic(tmp_path):
    repo = workspace(tmp_path)
    _r, on_before = built(tmp_path, root="on-before", repo=repo)
    _r, off_before = built(tmp_path, root="off-before", repo=repo, frameworks=())
    edit(repo, "app/services/plain_task.rb", "    x\n", "    x + 1\n")
    _r, on_after = built(tmp_path, root="on-after", repo=repo)
    _r, off_after = built(tmp_path, root="off-after", repo=repo, frameworks=())
    from atlas_aci.query import diff as symbol_diff

    on_set = symbol_diff.compare(
        Side(repo, tmp_path / "on-before"), Side(repo, tmp_path / "on-after")
    )
    off_set = symbol_diff.compare(
        Side(repo, tmp_path / "off-before"), Side(repo, tmp_path / "off-after")
    )

    def projected(change_set):
        return [(c["category"], c.get("entity_id"), c.get("path")) for c in change_set["changes"]]

    assert projected(on_set) == projected(off_set)
    with (
        on_before.reader_lease() as a,
        on_after.reader_lease() as b,
        off_before.reader_lease() as c,
        off_after.reader_lease() as d,
    ):
        limits = {"max_entities": 10**6, "max_occurrences": 10**6, "max_changed_files": 100}
        with_jobs = impact._admit_diff(a, b, limits)
        plain = impact._admit_diff(c, d, limits)
    assert with_jobs["before_entities"] == plain["before_entities"]
    assert with_jobs["after_entities"] == plain["after_entities"]
    # Job occurrences are counted by the diff admission (documented): exactly the owned ones.
    count = OWNED_RECORDS["occurrence"]
    assert with_jobs["before_occurrences"] == plain["before_occurrences"] + count
    assert with_jobs["after_occurrences"] == plain["after_occurrences"] + count
    # The bound is derived from the same constants as the validator's cap.
    assert count <= rails_jobs.MAX_ASSERTIONS


# ---- validation ----
def where(relation=None, raw=None, resolution=None, role=None):
    clauses = []
    if relation:
        clauses.append(f"json_extract(payload,'$.relation')='{relation}'")
    if raw:
        clauses.append(f"json_extract(payload,'$.raw_target') LIKE '%{raw}%'")
    if resolution:
        clauses.append(f"json_extract(payload,'$.resolution')='{resolution}'")
    return " AND ".join(clauses)


def raw(value):
    return lambda p: p.update(raw_target=value)


def expect_invalid(conn, text="Invalid evidence: rails_jobs", label=""):
    try:
        rails_jobs.validate_records(conn)
    except ValueError as exc:
        assert re.search(text, str(exc)), (label, str(exc))
        return
    raise AssertionError(f"planted corruption {label!r} was accepted")


def amend(**changes):
    """A mutation that rewrites the decoded raw_target (canonically) and other fields."""

    def mutate(payload):
        value = json.loads(payload["raw_target"])
        for key, new in changes.items():
            if key.startswith("raw_"):
                if new is None:
                    value.pop(key[4:], None)
                else:
                    value[key[4:]] = new
            else:
                payload[key] = new
        payload["raw_target"] = ev.canonical(value)

    return mutate


def flip(key, first, second):
    """Swap one raw_target value for the other member of a pair (whichever the row holds)."""

    def mutate(payload):
        value = json.loads(payload["raw_target"])
        value[key] = second if value[key] == first else first
        payload["raw_target"] = ev.canonical(value)

    return mutate


def delete_triple(conn, assertion_select):
    """Delete one assertion with its evidence and occurrence (keeps the record graph closed)."""
    row = conn.execute(
        "SELECT payload FROM assertions WHERE " + assertion_select + " LIMIT 1"
    ).fetchone()
    assertion = json.loads(row[0])
    proof = json.loads(
        conn.execute(
            "SELECT payload FROM evidence WHERE id=?", (assertion["evidence_id"],)
        ).fetchone()[0]
    )
    conn.execute("DELETE FROM assertions WHERE id=?", (assertion["id"],))
    conn.execute("DELETE FROM evidence WHERE id=?", (proof["id"],))
    conn.execute("DELETE FROM occurrences WHERE id=?", (proof["occurrence_id"],))
    return assertion


def test_planted_corruptions_each_raise(tmp_path):
    repo, graph = built(tmp_path)
    enq = where("async_enqueue", raw="mapped", resolution="INFERRED")
    unsupported = where("async_enqueue", raw="unsupported", resolution="INFERRED")
    diag_enq = where("async_enqueue", raw="dynamic_receiver")
    ambiguous = where("async_enqueue", resolution="AMBIGUOUS")
    unresolved = where("async_enqueue", resolution="UNRESOLVED", raw="GhostJob")
    mapped = where("async_argument", resolution="INFERRED")
    diag = where("async_argument", resolution="UNRESOLVED")
    unresolved = where("async_enqueue", resolution="UNRESOLVED") + (
        " AND json_extract(payload,'$.unresolved_reason')='job_class_not_found'"
    )
    shapes = {
        "list": raw("[1]"),
        "scalar": raw("3"),
        "not_json": raw("{"),
        "not_canonical": raw(
            '{"arity": 1, "arguments": "mapped", "kind": "perform_async", '
            '"mapping": "rails_job_receiver.v1", "mechanism": "sidekiq", "receiver": "HardWorker"}'
        ),
        "unknown_key": amend(raw_extra=1),
        "missing_key": amend(raw_arity=None),
        "bad_receiver": amend(raw_receiver="hardWorker"),
        "bad_label": amend(raw_mapping="x"),
        "bad_state": amend(raw_arguments="partial"),
        "bad_arity": amend(raw_arity="1"),
        "negative_arity": amend(raw_arity=-1),
        "mechanism_mismatch": flip("mechanism", "sidekiq", "active_job"),
        "kind_mismatch": flip("kind", "perform_async", "perform_later"),
        "construct_without_unsupported": amend(raw_construct="splat"),
        "empty_state_with_mapped_zero_arity": amend(raw_arity=0),
        "reason_on_inferred": amend(unresolved_reason="job_class_not_found"),
        "ambiguous_on_inferred": amend(
            resolution="AMBIGUOUS", unresolved_reason="ambiguous_job_target"
        ),
    }
    for name, mutate in shapes.items():
        expect_invalid(planted(graph, tmp_path, name, mutate, select=enq), label=name)
    # Constructs outside the closed vocabularies, and the unsupported shapes.
    expect_invalid(
        planted(graph, tmp_path, "badconstruct", amend(raw_construct="zzz"), select=unsupported)
    )
    expect_invalid(
        planted(
            graph,
            tmp_path,
            "unsupportedwithoutconstruct",
            amend(raw_construct=None),
            select=unsupported,
        )
    )
    expect_invalid(planted(graph, tmp_path, "diagvocab", amend(raw_construct="zzz"), select=diag))
    expect_invalid(
        planted(graph, tmp_path, "diagkind", amend(raw_kind="perform_now"), select=diag_enq)
    )
    expect_invalid(
        planted(graph, tmp_path, "diagconstruct", amend(raw_construct="splat"), select=diag_enq)
    )
    expect_invalid(
        planted(
            graph,
            tmp_path,
            "diagreason",
            amend(unresolved_reason="job_class_not_found"),
            select=diag_enq,
        )
    )
    expect_invalid(
        planted(
            graph, tmp_path, "unresolved_reason", amend(unresolved_reason="x"), select=unresolved
        )
    )
    expect_invalid(
        planted(
            graph,
            tmp_path,
            "unresolved_reason_ambiguous",
            amend(unresolved_reason="ambiguous_job_target"),
            select=unresolved,
        )
    )
    expect_invalid(
        planted(
            graph, tmp_path, "unresolved_state", amend(raw_arguments="mapped"), select=unresolved
        )
    )
    # Source and role discrimination.
    expect_invalid(
        planted(
            graph, tmp_path, "source", lambda p: p.update(source_entity_id="0" * 64), select=enq
        ),
        "rails_jobs source",
    )
    expect_invalid(
        planted(graph, tmp_path, "area", lambda p: p.update(relation="async_argument"), select=enq),
        "role/relation area",
    )
    expect_invalid(
        planted(
            graph, tmp_path, "area2", lambda p: p.update(relation="async_enqueue"), select=mapped
        ),
        "role/relation area",
    )
    # INFERRED to a class or to a non-perform method; AMBIGUOUS candidates that are not performs.
    with graph.reader_lease() as lease:
        class_id = lease.db.execute(
            "SELECT entity_id FROM symbols WHERE kind='class' LIMIT 1"
        ).fetchone()[0]
    run = method_id(graph, ENQUEUER, "run")
    for name, wrong in (("toclass", class_id), ("tomethod", run), ("tofile", file_id(SPEC))):
        expect_invalid(
            planted(
                graph, tmp_path, name, lambda p, w=wrong: p.update(target_entity_id=w), select=enq
            ),
            "perform method entity",
        )
    expect_invalid(
        planted(
            graph,
            tmp_path,
            "badcandidates",
            lambda p: p.update(candidate_ids=sorted([run, class_id])),
            select=ambiguous,
        ),
        "perform method entity",
    )
    expect_invalid(
        planted(
            graph,
            tmp_path,
            "onecandidate",
            lambda p: p.update(candidate_ids=p["candidate_ids"][:1]),
            select=ambiguous,
        ),
        "ambiguous",
    )
    # async_argument shapes.
    expect_invalid(
        planted(
            graph,
            tmp_path,
            "argambiguous",
            lambda p: p.update(
                resolution="AMBIGUOUS",
                target_entity_id=None,
                candidate_ids=sorted([run, class_id]),
                unresolved_reason="ambiguous_job_target",
            ),
            select=mapped,
        ),
        "async_argument",
    )
    expect_invalid(
        planted(
            graph,
            tmp_path,
            "paramarg",
            amend(raw_param=1),
            select=mapped + " AND json_extract(payload,'$.raw_target') LIKE '%\"arg\":0%'",
        )
    )
    expect_invalid(planted(graph, tmp_path, "badname", amend(raw_param_name="User"), select=mapped))
    expect_invalid(planted(graph, tmp_path, "argkey", amend(raw_extra=1), select=mapped))
    expect_invalid(
        planted(graph, tmp_path, "argdangling", amend(raw_enqueue="0" * 64), select=mapped),
        "no regular enqueue",
    )
    expect_invalid(planted(graph, tmp_path, "argbadref", amend(raw_enqueue="xyz"), select=mapped))
    expect_invalid(
        planted(
            graph,
            tmp_path,
            "diagvocab2",
            amend(raw_construct="splat"),
            select=diag + " AND json_extract(payload,'$.raw_target') LIKE '%block%'",
        )
    )
    expect_invalid(
        planted(
            graph,
            tmp_path,
            "argtarget",
            lambda p: p.update(target_entity_id=perform_ids(graph, "SyncJob")[0]),
            select=mapped,
        ),
        "disagrees",
    )
    expect_invalid(
        planted(
            graph, tmp_path, "argreason", lambda p: p.update(unresolved_reason="x"), select=mapped
        )
    )
    # An argument that names a diagnostic enqueue (or an argument) as its enqueue.
    conn = copy_db(graph, tmp_path, "pointsatdiag.db")
    diag_occurrence = conn.execute(
        "SELECT o.id FROM occurrences o WHERE json_extract(o.payload,'$.role') LIKE "
        "'rails_job:enqueue:unsupported:%' LIMIT 1"
    ).fetchone()[0]
    row = conn.execute("SELECT id, payload FROM assertions WHERE " + mapped + " LIMIT 1").fetchone()
    payload = json.loads(row[1])
    value = json.loads(payload["raw_target"])
    value["enqueue"] = diag_occurrence
    payload["raw_target"] = ev.canonical(value)
    conn.execute("UPDATE assertions SET payload=? WHERE id=?", (ev.canonical(payload), row[0]))
    expect_invalid(conn, "no regular enqueue")
    # An argument span outside its enqueue span.
    conn = copy_db(graph, tmp_path, "outside.db")
    arg = conn.execute("SELECT payload FROM assertions WHERE " + mapped + " LIMIT 1").fetchone()
    proof = json.loads(
        conn.execute(
            "SELECT payload FROM evidence WHERE id=?", (json.loads(arg[0])["evidence_id"],)
        ).fetchone()[0]
    )
    occ = json.loads(
        conn.execute(
            "SELECT payload FROM occurrences WHERE id=?", (proof["occurrence_id"],)
        ).fetchone()[0]
    )
    occ.update(start_byte=0, end_byte=1)
    conn.execute(
        "UPDATE occurrences SET payload=? WHERE id=?", (ev.canonical(occ), proof["occurrence_id"])
    )
    expect_invalid(conn, "outside its enqueue")
    # Mapped count differs from the arity; a missing diagnostic; an extra diagnostic.
    conn = copy_db(graph, tmp_path, "count.db")
    delete_triple(conn, mapped)
    expect_invalid(conn, "ordinals differ")
    conn = copy_db(graph, tmp_path, "nodiag.db")
    delete_triple(conn, diag)
    expect_invalid(conn, "exactly one diagnostic")
    conn = copy_db(graph, tmp_path, "twodiag.db")
    row = conn.execute("SELECT payload FROM assertions WHERE " + diag + " LIMIT 1").fetchone()
    twin = json.loads(row[0])
    proof = json.loads(
        conn.execute("SELECT payload FROM evidence WHERE id=?", (twin["evidence_id"],)).fetchone()[
            0
        ]
    )
    occurrence = json.loads(
        conn.execute(
            "SELECT payload FROM occurrences WHERE id=?", (proof["occurrence_id"],)
        ).fetchone()[0]
    )
    body = {k: v for k, v in occurrence.items() if k != "id"}
    # The same construct on a second, shorter span: another occurrence, evidence and assertion.
    second = ev.record("occurrence", **dict(body, end_byte=body["end_byte"] - 1))
    second_proof = ev.provenance(second, proof["producer_id"])
    second_assertion = ev.assertion(
        twin["source_entity_id"],
        None,
        "async_argument",
        second_proof["id"],
        "UNRESOLVED",
        twin["raw_target"],
        (),
        "unsupported_argument_mapping",
    )
    path = body["path"]
    for item in (second, second_proof, second_assertion):
        ev.put(conn, item, path)
    expect_invalid(conn, "exactly one diagnostic")
    # A second producer; an orphan occurrence; evidence without an assertion.
    conn = copy_db(graph, tmp_path, "second.db")
    row = conn.execute(
        "SELECT payload FROM producers "
        "WHERE json_extract(payload,'$.fingerprint.rails_jobs') IS NOT NULL"
    ).fetchone()
    fingerprint = dict(json.loads(row[0])["fingerprint"]["rails_jobs"], adapter_sha256="7" * 64)
    ev.put(conn, ev.producer({"rails_jobs": fingerprint}))
    expect_invalid(conn, "more than one producer")
    conn = copy_db(graph, tmp_path, "orphan.db")
    row = conn.execute(
        "SELECT payload FROM occurrences WHERE json_extract(payload,'$.kind')='rails_job' LIMIT 1"
    ).fetchone()
    body = {k: v for k, v in json.loads(row[0]).items() if k != "id"}
    body.update(start_byte=0, end_byte=1, line=1, column=0, end_line=1, end_column=1)
    ev.put(conn, ev.record("occurrence", **body), body["path"])
    expect_invalid(conn, "without exactly one evidence")
    conn = copy_db(graph, tmp_path, "unused.db")
    conn.execute(
        "DELETE FROM assertions WHERE json_extract(payload,'$.relation') IN "
        "('async_enqueue','async_argument')"
    )
    expect_invalid(conn)
    conn = copy_db(graph, tmp_path, "missing-graph-producer.db")
    conn.execute(
        "DELETE FROM producers WHERE json_extract(payload,'$.fingerprint.rails_jobs') IS NULL"
    )
    expect_invalid(conn, "graph producer is missing")
    del repo


def test_record_layer_rejections(tmp_path):
    _repo, graph = built(tmp_path)
    good = next(r for r in job_records(graph) if r["type"] == "occurrence")
    body = {k: v for k, v in good.items() if k != "id"}
    with pytest.raises(ValueError, match="rails job occurrence role"):
        ev.validate_record(ev.record("occurrence", **dict(body, role="definition:class")))
    with pytest.raises(ValueError, match="rails job occurrence role"):
        ev.validate_record(ev.record("occurrence", **dict(body, kind="reference")))
    with pytest.raises(ValueError, match="rails job occurrence shape"):
        ev.validate_record(ev.record("occurrence", **dict(body, language="python")))
    with pytest.raises(ValueError, match="occurrence role"):
        ev.validate_record(ev.record("occurrence", **dict(body, kind="rails_model")))
    with pytest.raises(ValueError, match="occurrence role"):
        ev.validate_record(ev.record("occurrence", **dict(body, kind="rails_route")))
    producer = next(r for r in job_records(graph) if r["type"] == "producer")
    fingerprint = dict(producer["fingerprint"]["rails_jobs"])
    ev.validate_record(ev.producer({"rails_jobs": fingerprint}))
    with pytest.raises(ValueError, match="rails_jobs producer fields"):
        ev.validate_record(ev.producer({"rails_jobs": dict(fingerprint, extra="x")}))
    with pytest.raises(ValueError, match="producer fields"):
        ev.validate_record(ev.producer({"rails_jobs": fingerprint, "rails": {}}))
    with pytest.raises(ValueError, match="producer fields"):
        ev.validate_record(ev.producer({"rails_jobs": fingerprint, "rails_models": {}}))
    # A different valid adapter hash is accepted (never pinned to the running adapter).
    ev.validate_record(ev.producer({"rails_jobs": dict(fingerprint, adapter_sha256="7" * 64)}))
    with pytest.raises(ValueError, match="rails_jobs producer hashes"):
        ev.validate_record(ev.producer({"rails_jobs": dict(fingerprint, adapter_sha256="XYZ")}))
    with pytest.raises(ValueError, match="rails_jobs producer hashes"):
        ev.validate_record(ev.producer({"rails_jobs": dict(fingerprint, graph_producer_id="no")}))
    with pytest.raises(ValueError, match="rails_jobs producer values"):
        ev.validate_record(ev.producer({"rails_jobs": dict(fingerprint, adapter="other.v1")}))


def test_validate_records_accepts_a_different_adapter_hash(tmp_path):
    _repo, graph = built(tmp_path)
    conn = copy_db(graph, tmp_path)
    row = conn.execute(
        "SELECT id, payload FROM producers "
        "WHERE json_extract(payload,'$.fingerprint.rails_jobs') IS NOT NULL"
    ).fetchone()
    old = json.loads(row[1])
    fingerprint = dict(old["fingerprint"]["rails_jobs"], adapter_sha256="7" * 64)
    rewritten = ev.producer({"rails_jobs": fingerprint})
    conn.execute(
        "UPDATE producers SET id=?, payload=? WHERE id=?",
        (rewritten["id"], ev.canonical(rewritten), row[0]),
    )
    conn.execute(
        "UPDATE evidence SET payload=json_set(payload,'$.producer_id',?) "
        "WHERE json_extract(payload,'$.producer_id')=?",
        (rewritten["id"], row[0]),
    )
    rails_jobs.validate_records(conn)  # format only: the running adapter hash is never compared
    assert rewritten["fingerprint"]["rails_jobs"]["adapter_sha256"] != rails_jobs.adapter_sha256()


def test_projection_validation_passes_on_a_copy_of_an_on_database(tmp_path):
    _repo, graph = built(tmp_path)
    conn = copy_db(graph, tmp_path)
    graph._validate_evidence_projections(conn)
    ev.validate_database(conn)
    rails_jobs.validate_records(conn)
    assert {f["assertion"]["relation"] for f in facts(graph)} == RELATIONS2
    # Without CG6/CG7 the projection check would demand edges for these assertions: a copy
    # holding them but pretending to be an edge-less projection set is still accepted here.
    assert (
        conn.execute("SELECT COUNT(*) FROM edges WHERE relation LIKE 'async%'").fetchone()[0] == 0
    )


# ---- S10 / invalidation ----
def fresh_equal(graph, repo, tmp_path, name):
    full, _stats = rebuilt(repo, tmp_path, name)
    assert job_lines(graph) == job_lines(full)
    with graph.reader_lease() as lease:
        graph._validate_evidence_projections(lease.db)
        rails_jobs.validate_records(lease.db)
        known = {json.loads(p)["id"] for (p,) in lease.db.execute("SELECT payload FROM entities")}
    for fact in facts(graph):
        linked = [fact["assertion"]["target_entity_id"], *fact["assertion"]["candidate_ids"]]
        assert all(i in known for i in linked if i is not None)
    return full


def affected_mode(stats):
    resolution = stats["binding_resolution"]
    assert resolution["mode"] == "affected", resolution
    return resolution


def test_s10_i_deleting_a_job_class_retracts_its_links(tmp_path):
    repo, graph = built(tmp_path)
    before = job_lines(graph)
    found = facts(graph, repo)
    e3 = one(found, ENQUEUER, "ReportJob.perform_later(report, :pdf)")
    assert e3["assertion"]["resolution"] == "INFERRED" and arguments_of(found, e3)
    (repo / REPORT_JOB).unlink()
    graph, stats = rebuilt(repo, tmp_path, "g", since="x")
    affected_mode(stats)
    found = facts(graph, repo)
    e3 = one(found, ENQUEUER, "ReportJob.perform_later(report, :pdf)")
    assert e3["assertion"]["resolution"] == "UNRESOLVED"
    assert e3["assertion"]["unresolved_reason"] == "job_class_not_found"
    assert e3["raw"]["arguments"] == "unbound" and arguments_of(found, e3) == []
    assert job_lines(graph) != before
    fresh_equal(graph, repo, tmp_path, "fresh-delete")


def test_s10_ii_editing_the_enqueue_file_matches_a_fresh_build(tmp_path):
    repo, _graph = built(tmp_path)
    edit(repo, ENQUEUER, '(user.id, "fast")', '(user.id, "fast", 1)')
    graph, stats = rebuilt(repo, tmp_path, "g", since="x")
    affected_mode(stats)
    assert ENQUEUER in stats["binding_resolution"]["affected_paths"]
    found = facts(graph, repo)
    site = one(found, ENQUEUER, 'HardWorker.perform_async(user.id, "fast", 1)')
    assert site["raw"]["arguments"] == "unsupported"
    assert site["raw"]["construct"] == "arity_mismatch" and site["raw"]["arity"] == 3
    assert [c["raw"]["construct"] for c in arguments_of(found, site)] == ["arity_mismatch"]
    fresh_equal(graph, repo, tmp_path, "fresh-enqueuer")


def test_s10_iii_editing_only_the_perform_definition_recomputes_the_enqueue(tmp_path):
    repo, _graph = built(tmp_path)
    edit(repo, REPORT_JOB, "def perform(report, format)", "def perform(report, format, extra)")
    graph, stats = rebuilt(repo, tmp_path, "g", since="x")
    # The enqueue file is unchanged: the facts are rebuilt globally by the adapter on every
    # enabled build, not republished through the S10 plan. No claim is made about whether the
    # enqueue file is in `affected_paths`.
    affected_mode(stats)
    found = facts(graph, repo)
    e3 = one(found, ENQUEUER, "ReportJob.perform_later(report, :pdf)")
    assert e3["raw"]["arguments"] == "unsupported" and e3["raw"]["construct"] == "arity_mismatch"
    assert e3["assertion"]["resolution"] == "INFERRED"
    assert [c["raw"]["construct"] for c in arguments_of(found, e3)] == ["arity_mismatch"]
    assert not [
        f for f in arguments_of(found, e3) if f["occurrence"]["role"] == "rails_job:argument:mapped"
    ]
    fresh_equal(graph, repo, tmp_path, "fresh-perform")
    # A variant that shifts `symbols` rows (a line above the definition) is also exact.
    edit(
        repo,
        REPORT_JOB,
        "def perform(report, format, extra)",
        "# note\n  def perform(report, format)",
    )
    graph, stats = rebuilt(repo, tmp_path, "g", since="x")
    affected_mode(stats)
    found = facts(graph, repo)
    e3 = one(found, ENQUEUER, "ReportJob.perform_later(report, :pdf)")
    assert e3["raw"]["arguments"] == "mapped"
    fresh_equal(graph, repo, tmp_path, "fresh-shift")


def test_s10_iv_a_syntax_error_retracts_that_files_facts_and_leaves_no_history(tmp_path):
    repo, graph = built(tmp_path)
    assert any(f["occurrence"]["path"] == ENQUEUER for f in facts(graph))
    (repo / ENQUEUER).write_text("class Enqueuer\n  def run\n    HardWorker.perform_async((((1\n")
    graph, _stats = rebuilt(repo, tmp_path, "g", since="x")
    assert json.loads(manifest_value(graph))["files"]["failed"] == 1
    assert not any(f["occurrence"]["path"] == ENQUEUER for f in facts(graph))
    with graph.reader_lease() as lease:
        observed = json.loads(
            lease.db.execute(
                "SELECT value FROM manifest WHERE key='source_observations'"
            ).fetchone()[0]
        )
        graph._validate_evidence_projections(lease.db)
    history = [h for h in observed if h["type"] == "history" and h["path"] == ENQUEUER]
    assert history, "the failed file keeps its native history"
    assert not rails_jobs.record_ids(history[0]["records"])
    full, _stats = rebuilt(repo, tmp_path, "fresh-broken")
    assert job_lines(graph) == job_lines(full) and job_lines(graph)
    # Repairing the file restores the facts.
    (repo / ENQUEUER).write_text((PROJECT / ENQUEUER).read_text())
    graph, _stats = rebuilt(repo, tmp_path, "g", since="x")
    assert json.loads(manifest_value(graph))["files"]["failed"] == 0
    assert any(f["occurrence"]["path"] == ENQUEUER for f in facts(graph))


def test_s10_v_vi_on_to_off_purges_and_off_to_on_restores(tmp_path):
    repo, graph = built(tmp_path)
    expected = job_lines(graph)
    assert len(job_records(graph)) == 3 * OWNED_RECORDS["assertion"] + 1
    off, stats = rebuilt(repo, tmp_path, "g", frameworks=(), since="x")
    assert job_records(off) == [] and manifest_value(off) is None
    with off.reader_lease() as lease:
        for table in ("producers", "evidence", "occurrences", "assertions"):
            for (payload,) in lease.db.execute(f"SELECT payload FROM {table}"):
                for name in ("rails_jobs", "rails_job", "async_enqueue", "async_argument"):
                    assert name not in payload
        graph._validate_evidence_projections(lease.db)
    _r, fresh = built(tmp_path, root="fresh-off", repo=repo, frameworks=())
    assert [r["id"] for r in records(off)] == [r["id"] for r in records(fresh)]
    affected_mode(stats)
    on_again, stats = rebuilt(repo, tmp_path, "g", since="x")
    affected_mode(stats)
    assert job_lines(on_again) == expected


def test_producer_exists_only_with_facts(tmp_path):
    repo = tmp_path / "quiet"
    (repo / "app/jobs").mkdir(parents=True)
    (repo / "app/jobs/quiet_job.rb").write_text("class QuietJob < ApplicationJob\nend\n")
    _r, graph = built(tmp_path, repo=repo, root="quiet-g")
    assert job_records(graph) == []
    summary = json.loads(manifest_value(graph))
    assert summary["producer_id"] is None and summary["files"]["captured"] == 1
    assert summary["sites"]["located"] == 0 and summary["classes"] == {"jobs": 1, "unbound": 0}
    empty = tmp_path / "empty"
    empty.mkdir()
    (empty / "a.rb").write_text("class A; end\n")
    _r, graph = built(tmp_path, repo=empty, root="empty-g")
    assert json.loads(manifest_value(graph))["files"] == {
        "captured": 0,
        "failed": 0,
        "limited": 0,
        "scanned": 0,
    }


def test_parser_is_only_used_when_enabled_and_a_job_file_is_captured(tmp_path, monkeypatch):
    calls: list[bytes] = []
    real = pack.get_parser

    class Counting:
        def __init__(self, parser):
            self.parser = parser

        def parse(self, source):
            calls.append(bytes(source))
            return self.parser.parse(source)

    monkeypatch.setattr(pack, "get_parser", lambda lang: Counting(real(lang)))
    repo, _graph = built(tmp_path, frameworks=())  # warm the extraction cache
    calls.clear()
    rebuilt(repo, tmp_path, "g", frameworks=())
    assert calls == []  # OFF: nothing is parsed at all
    for other in (("rails",), ("rails_models",)):
        rebuilt(repo, tmp_path, "g", frameworks=other)
        assert calls == []  # no routes.rb / model file here: the other adapters parse nothing
    rebuilt(repo, tmp_path, "g")
    pattern = rails_jobs.CAPTURE_RE
    expected = sorted(
        p.read_bytes()
        for p in repo.rglob("*.rb")
        if not p.relative_to(repo).as_posix().startswith("tmp/") and pattern.search(p.read_bytes())
    )
    assert len(expected) == EXPECTED["files"]["captured"]
    assert sorted(calls) == expected  # ON: each captured file is re-parsed once, outside the cache
    # No captured file: the adapter never asks for a parser.
    other = tmp_path / "other"
    other.mkdir()
    (other / "a.rb").write_text("class A; end\n")
    built(tmp_path, repo=other, root="other-g")
    calls.clear()
    rebuilt(other, tmp_path, "other-g")
    assert calls == []


def test_export_import_round_trip_keeps_and_validates_the_job_records(tmp_path):
    repo, graph = built(tmp_path)
    exported = tmp_path / "graph.jsonl"
    runner = CliRunner()
    out = runner.invoke(
        cli, ["export", "--repo", str(repo), "--graph-root", str(tmp_path / "g"), str(exported)]
    )
    assert out.exit_code == 0, out.output
    assert exported.read_text().count('"rails_job"') >= OWNED_RECORDS["occurrence"]
    other = tmp_path / "other"
    out = runner.invoke(
        cli, ["import", str(exported), "--repo", str(repo), "--graph-root", str(other)]
    )
    assert out.exit_code == 0, out.output
    imported = CodeGraph(repo, graph_root=other, read_only=True)
    assert job_lines(imported) == job_lines(graph) and job_lines(graph)
    imported.close()


# ---- caps (monkeypatched) ----
def test_site_limit_keeps_earlier_sites_and_marks_truncation(tmp_path, monkeypatch):
    monkeypatch.setattr(rails_jobs, "MAX_SITES", 2)
    repo, graph = built(tmp_path)
    found = facts(graph, repo)
    summary = json.loads(manifest_value(graph))
    paths = [f["occurrence"]["path"] for f in enqueues(found)]
    assert paths == [NIGHTLY, NIGHTLY, ENQUEUER]  # global (path, byte) order
    limit = enqueues(found, ENQUEUER)[0]
    assert limit["occurrence"]["role"] == "rails_job:enqueue:unsupported:site_limit"
    assert limit["raw"] == {
        "construct": "site_limit",
        "kind": "perform_async",
        "mapping": "unexpanded",
    }
    assert limit["assertion"]["unresolved_reason"] == "unsupported_argument_mapping"
    assert summary["truncated"] is True
    assert summary["sites"]["located"] == EXPECTED["sites"]["located"]
    assert summary["sites"]["limited"] == EXPECTED["sites"]["located"] - 3
    # The earlier sites still resolved: class scanning continued over every eligible file.
    first = one(found, NIGHTLY, "SyncJob.perform_later(1)")
    assert first["assertion"]["resolution"] == "AMBIGUOUS"
    assert one(found, NIGHTLY, "ReportJob.perform_later(1, 2)")["raw"]["arguments"] == "mapped"
    rails_jobs.validate_records(copy_db(graph, tmp_path))


def test_argument_limit_is_a_diagnostic_never_a_partial_mapping(tmp_path, monkeypatch):
    monkeypatch.setattr(rails_jobs, "MAX_ARGUMENTS", 1)
    repo, graph = built(tmp_path)
    found = facts(graph, repo)
    two = one(found, ENQUEUER, 'HardWorker.perform_async(user.id, "fast")')
    assert two["assertion"]["resolution"] == "INFERRED"
    assert two["raw"]["arguments"] == "unsupported" and two["raw"]["construct"] == "argument_limit"
    children = arguments_of(found, two)
    assert [c["raw"]["construct"] for c in children] == ["argument_limit"]
    assert children[0]["text"] == '(user.id, "fast")'
    one_argument = one(found, ENQUEUER, "HardWorker.perform_async(user.id)")
    assert (
        one_argument["raw"]["arguments"] == "mapped" and len(arguments_of(found, one_argument)) == 1
    )
    # A call-side construct still wins over the cap, and an AMBIGUOUS enqueue keeps its candidates.
    splat = one(found, ENQUEUER, "HardWorker.perform_async(*ids)")
    assert splat["raw"]["construct"] == "splat"
    rails_jobs.validate_records(copy_db(graph, tmp_path))


def captured_sizes(repo):
    return sorted(
        (p.relative_to(repo).as_posix(), len(p.read_bytes()))
        for p in repo.rglob("*.rb")
        if not p.relative_to(repo).as_posix().startswith("tmp/")
        and rails_jobs.CAPTURE_RE.search(p.read_bytes())
    )


def test_file_caps_limit_the_capture_and_mark_truncation(tmp_path, monkeypatch):
    repo = workspace(tmp_path)
    sizes = captured_sizes(repo)
    assert len(sizes) == EXPECTED["files"]["captured"]
    monkeypatch.setattr(rails_jobs, "MAX_FILE_BYTES", 300)
    _r, graph = built(tmp_path, repo=repo, root="bytes")
    summary = json.loads(manifest_value(graph))
    over = [name for name, size in sizes if size > 300]
    assert over and summary["files"]["limited"] == len(over)
    assert summary["files"]["captured"] == len(sizes) - len(over)
    assert summary["truncated"] is True
    monkeypatch.setattr(rails_jobs, "MAX_FILE_BYTES", 1048576)
    monkeypatch.setattr(rails_jobs, "MAX_CAPTURE_BYTES", 1000)
    _r, graph = built(tmp_path, repo=repo, root="total")
    summary = json.loads(manifest_value(graph))
    kept = 0
    total = 0
    for _name, size in sizes:
        if total + size <= 1000:
            total += size
            kept += 1
    assert summary["files"]["captured"] == kept and summary["files"]["limited"] == len(sizes) - kept
    assert summary["truncated"] is True


def test_names_defined_in_limited_files_never_resolve_to_a_job(tmp_path, monkeypatch):
    repo = workspace(tmp_path)
    full = facts(built(tmp_path, repo=repo, root="full")[1], repo)
    assert (
        one(full, ENQUEUER, "ExportJob.perform_later(1)")["assertion"]["resolution"] == "AMBIGUOUS"
    )
    names = [name for name, _size in captured_sizes(repo)]
    keep = names.index("app/services/reports/daily.rb") + 1
    monkeypatch.setattr(rails_jobs, "MAX_FILES", keep)
    _r, graph = built(tmp_path, repo=repo, root="limited")
    found = facts(graph, repo)
    summary = json.loads(manifest_value(graph))
    assert summary["files"]["captured"] == keep and summary["truncated"] is True
    assert summary["files"]["limited"] == len(names) - keep
    # `lib/legacy/export_job.rb` is limited: the surviving `ExportJob` would otherwise be INFERRED
    # (or the pair AMBIGUOUS); the name is unsafe, so it never resolves to a job.
    export = one(found, ENQUEUER, "ExportJob.perform_later(1)")["assertion"]
    assert export["resolution"] == "UNRESOLVED"
    assert export["unresolved_reason"] == "target_not_recognized_job"
    assert export["target_entity_id"] is None and export["candidate_ids"] == []
    # `HardWorker` lives in a limited file too: it exists, but is never resolved.
    hard = one(found, ENQUEUER, "HardWorker.perform_async(user.id)")["assertion"]
    assert hard["resolution"] == "UNRESOLVED"
    assert hard["unresolved_reason"] == "target_not_recognized_job"
    # A name defined only in captured, eligible files still resolves.
    report = one(found, ENQUEUER, "ReportJob.perform_later(report, :pdf)")["assertion"]
    assert report["resolution"] == "INFERRED"
    assert report["target_entity_id"] == perform_ids(graph, "ReportJob")[0]
    # The same names are exact when nothing is limited.
    assert one(full, ENQUEUER, "HardWorker.perform_async(user.id)")["assertion"]["resolution"] == (
        "INFERRED"
    )
    rails_jobs.validate_records(copy_db(graph, tmp_path))


def test_an_overflowing_limited_path_set_disables_every_resolution(tmp_path, monkeypatch):
    repo = workspace(tmp_path)
    names = [name for name, _size in captured_sizes(repo)]
    keep = names.index("app/services/reports/daily.rb") + 1
    monkeypatch.setattr(rails_jobs, "MAX_FILES", keep)
    monkeypatch.setattr(rails_jobs, "MAX_LIMITED_PATHS", 1)
    capture = rails_jobs.Capture()
    for name in names:
        data = (repo / name).read_bytes()
        capture.offer(name, data, hashlib.sha256(data).hexdigest())
    assert len(capture.limited_paths) == 1 and capture.overflow  # bounded, never silent
    assert capture.limited == len(names) - keep
    _r, graph = built(tmp_path, repo=repo, root="overflow")
    found = facts(graph, repo)
    summary = json.loads(manifest_value(graph))
    assert summary["truncated"] is True and summary["facts"]["async_enqueue"]["INFERRED"] == 0
    assert summary["facts"]["async_enqueue"]["AMBIGUOUS"] == 0
    reasons = {f["assertion"]["unresolved_reason"] for f in enqueues(found)}
    assert reasons <= {
        "target_not_recognized_job",
        "job_class_not_found",
        "unsupported_argument_mapping",
    }
    assert "target_not_recognized_job" in reasons
    rails_jobs.validate_records(copy_db(graph, tmp_path))


def test_a_failed_file_makes_its_names_unsafe(tmp_path):
    repo = workspace(tmp_path)
    # A parse error in a job definition file: the build still indexes the file, the adapter
    # cannot read it, so `ReportJob` must not resolve to a single perform any more.
    (repo / REPORT_JOB).write_text(
        (repo / REPORT_JOB).read_text().replace("[report, format]", "[report, ((format")
    )
    _r, graph = built(tmp_path, repo=repo, root="broken")
    summary = json.loads(manifest_value(graph))
    assert summary["files"]["failed"] == 1
    found = facts(graph, repo)
    e3 = one(found, ENQUEUER, "ReportJob.perform_later(report, :pdf)")["assertion"]
    assert e3["resolution"] == "UNRESOLVED"
    assert e3["unresolved_reason"] in {"target_not_recognized_job", "job_class_not_found"}
    assert e3["target_entity_id"] is None


def test_unbound_namespaces_and_bound_nested_modules(tmp_path):
    repo, graph = built(tmp_path)
    found = facts(graph, repo)
    # `module Billing; class Nightly` is a bound lexical scope: INFERRED through the nesting.
    bound = one(found, NIGHTLY, "ReportJob.perform_later(1, 2)")
    assert bound["assertion"]["resolution"] == "INFERRED"
    # A top-level compact name is bound too.
    assert (
        one(found, DAILY, "ReportJob.perform_later(1, 2)")["assertion"]["resolution"] == "INFERRED"
    )
    # A compact name inside a module and a rooted class are not lexical scopes.
    unbound = [f for f in enqueues(found, "app/services/outer.rb")]
    assert len(unbound) == 2
    assert {f["raw"]["construct"] for f in unbound} == {"unbound_namespace"}
    summary = json.loads(manifest_value(graph))
    assert summary["classes"]["unbound"] == EXPECTED["classes"]["unbound"] == 2


def test_the_jobs_adapter_cannot_be_enabled_by_serve_or_tools():
    assert "frameworks" not in {p.name for p in cli.commands["serve"].params}
    assert "frameworks" not in {p.name for p in cli.commands["tools"].params}
    assert Path(PROJECT).exists() and DAILY.endswith(".rb")


# ---- docs ----
ROOT = Path(__file__).parents[3]


def test_docs_name_every_closed_value_and_the_silent_limits():
    docs = (ROOT / "docs/frameworks/rails.md").read_text()
    section = docs[docs.index("# Jobs and enqueue argument paths (ACI-S26)") :]
    m = rails_jobs
    names = [
        *m.ENQUEUE_METHODS,
        *m.DELAYED_METHODS,
        *m.MECHANISMS,
        *m.ENQUEUE_CONSTRUCTS,
        *m.ARGUMENT_CONSTRUCTS,
        *m.REASONS,
        *m.ARGUMENT_STATES,
        *m.LABELS,
        m.MANIFEST_KEY,
        m.MANIFEST_FORMAT,
        "async_enqueue",
        "async_argument",
        "rails_job",
        "asynchronous boundary",
        "unsupported_argument_mapping",
    ]
    assert [n for n in names if n not in section] == []
    # The silent limits and the S10 statement are named.
    for phrase in (
        "receiverless",
        "`send`",
        "`public_send`",
        "skip pattern",
        "Class.new",
        "constant assignment",
        "first segment",
        "no partial mapping",
        "keeps its resolution",
        "null reason",
        "does not claim that editing a job file puts",
    ):
        assert phrase in section.replace("Constant assignment", "constant assignment"), phrase
    assert "affected_paths" in section
    changelog = (ROOT / "CHANGELOG.md").read_text()
    unreleased = changelog.split("\n## [3.0.0]", 1)[1].split("\n## [", 1)[0]
    assert "(ACI-S26)" in unreleased and "async_enqueue" in unreleased
    readme = (ROOT / "README.md").read_text()
    row = next(line for line in readme.splitlines() if "docs/frameworks/rails.md" in line)
    assert "rails_jobs" in row and row.count("|") == 3  # still a two-cell table row
