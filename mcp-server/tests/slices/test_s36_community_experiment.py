"""ACI-S36 contract: the seven frozen criteria, through the real scripts and real processes.

The fixture provider is a real deterministic partitioner placed on a child's PYTHONPATH only;
the production manifest is run with `python -S` so no installed provider can be seen.
"""

from __future__ import annotations

import gzip
import json
import shutil
from fractions import Fraction
from itertools import combinations
from pathlib import Path

import pytest
from test_s36_support import (
    BUNDLE,
    DOCS,
    FIX_MANIFEST,
    FX,
    LPA_FILE,
    PROD_MANIFEST,
    PRODUCTION_SHA,
    ROOT,
    S18_GRAPH,
    SITE,
    USEFUL,
    canonical,
    child_env,
    copy_run,
    digest,
    fixture_manifest_sha,
    leftover,
    lpa_module,
    make_prod_run,
    manifest_copy,
    projected,
    projection_sha,
    read_partitions,
    read_result,
    rejected,
    repo_copy,
    reseal_raw,
    run_experiment,
    run_script,
    run_verify,
    sha,
    shared_fx,
    shared_prod,
    tamper_partitions,
    tamper_result,
    verifier,
    write_manifest,
)

from atlas_aci import codegraph
from atlas_aci import label_propagation as lp_module
from atlas_aci.codegraph import CodeGraph

GRAPHS = {"s18-task-graph": 7, "synthetic-cliques": 51}


@pytest.fixture(scope="module")
def fx(tmp_path_factory):
    return shared_fx(tmp_path_factory)


@pytest.fixture(scope="module")
def prod(tmp_path_factory):
    return shared_prod(tmp_path_factory)


def stamps(node):
    """Every `manifest_sha256` value found anywhere in a document."""
    found = []
    stack = [node]
    while stack:
        item = stack.pop()
        if isinstance(item, dict):
            for key, value in item.items():
                if key == "manifest_sha256":
                    found.append(value)
                stack.append(value)
        elif isinstance(item, list):
            stack.extend(item)
    return found


# ------------------------------------------------------------------------------- AC-S36-001


def test_community_experiment_uses_frozen_manifest(fx, tmp_path):
    manifest = json.loads(FIX_MANIFEST.read_bytes())
    frozen = sha(canonical(manifest))
    assert frozen == fixture_manifest_sha() == sha(FIX_MANIFEST.read_bytes())
    result = read_result(fx["dir"])
    documents = {
        "partitions": read_partitions(fx["dir"]),
        "result": result,
        "telemetry": json.loads((fx["dir"] / "telemetry.json").read_bytes()),
    }
    for name, document in documents.items():
        found = stamps(document)
        assert found, name
        assert set(found) == {frozen}, name
    assert len(stamps(documents["partitions"])) == 1 + 2 * 11 + 0  # doc plus every run
    assert len(stamps(result["usefulness"])) == 1 + 11 * 3 * 4 + 11 * 3
    assert len(result["graphs"]) == 2 and len(stamps(result["graphs"])) == 2 + 22
    assert result["decision"]["manifest_sha256"] == frozen
    # Production: the digest of the committed bytes is the hard-coded verifier constant.
    assert sha(canonical(json.loads(PROD_MANIFEST.read_bytes()))) == PRODUCTION_SHA
    assert sha(PROD_MANIFEST.read_bytes()) == PRODUCTION_SHA
    assert verifier().PRODUCTION_MANIFEST_SHA256 == PRODUCTION_SHA
    assert PRODUCTION_SHA in DOCS.read_text()
    assert frozen != PRODUCTION_SHA


def negative(tmp_path, name, manifest, expected, **kwargs):
    out = tmp_path / name
    sha_value = kwargs.pop("sha_value", None)
    path = tmp_path / (name + ".json")
    computed = write_manifest(path, manifest)
    done = run_experiment(path, sha_value or computed, out, **kwargs)
    rejected(done, expected)
    assert not out.exists() and leftover(tmp_path) == []


def test_frozen_manifest_negatives_are_rejected_before_any_output(tmp_path):
    good = manifest_copy()
    negative(tmp_path, "wrong-sha", good, "manifest_digest_mismatch", sha_value="0" * 64)
    floor = manifest_copy()
    floor["decision"]["stability_min_mean_pairwise_ari"] = {"num": 8, "den": 10}
    negative(tmp_path, "floor", floor, "manifest_constants_mismatch")
    extra = manifest_copy()
    extra["unexpected"] = 1
    negative(tmp_path, "extra", extra, "manifest_invalid")
    pin = manifest_copy()
    pin["algorithms"]["leiden"]["provider"]["distributions"] = {
        "igraph": "1.0.0",
        "leidenalg": "0.12.0",
    }
    negative(tmp_path, "pin", pin, "provider_pin_invalid")
    root = repo_copy(tmp_path / "copy")
    graph = root / "mcp-server/tests/fixtures/community_experiment/s18-task-graph.v1.json"
    graph.write_bytes(graph.read_bytes().replace(b"send_request", b"send_requesx"))
    negative(
        tmp_path,
        "flip",
        good,
        "input_hash_mismatch: s18-task-graph",
        repo_root=root,
    )


# ------------------------------------------------------------------------------- AC-S36-002


def test_community_algorithms_share_input_projection(fx, tmp_path):
    captures = sorted(fx["captures"].glob("*.json"))
    assert len(captures) == 20
    by_size: dict[int, list[dict]] = {}
    for path in captures:
        by_size.setdefault(json.loads(path.read_bytes())["n"], []).append(
            json.loads(path.read_bytes())
        )
    assert {n: len(v) for n, v in by_size.items()} == {7: 10, 51: 10}
    for graph_id, n in GRAPHS.items():
        nodes, edges = projected(graph_id)
        assert len(nodes) == n
        expected = digest({"edges": edges, "n": n})
        got = by_size[n]
        assert sorted(c["seed"] for c in got) == list(range(10))
        assert all(digest({"edges": c["edges"], "n": c["n"]}) == expected for c in got)
    result = read_result(fx["dir"])
    document = read_partitions(fx["dir"])
    lpa = lpa_module()
    for graph_id, entry in zip(GRAPHS, document["graphs"], strict=True):
        wanted = projection_sha(graph_id)
        nodes, edges = projected(graph_id)
        assert entry["graph_id"] == graph_id and entry["projection_sha256"] == wanted
        assert len(entry["runs"]) == 11
        assert all(run["input_projection_sha256"] == wanted for run in entry["runs"])
        row = next(g for g in result["graphs"] if g["graph_id"] == graph_id)
        assert row["projection_sha256"] == wanted
        assert all(r["input_projection_sha256"] == wanted for r in row["runs"])
        adjacency = {i: set() for i in range(len(nodes))}
        for u, v in edges:
            adjacency[u].add(v)
            adjacency[v].add(u)
        labels = lpa.label_propagation(adjacency, 100)
        groups: dict[int, list[int]] = {}
        for node in range(len(nodes)):
            groups.setdefault(labels[node], []).append(node)
        assert entry["runs"][0]["communities"] == sorted(groups.values(), key=min)
    # A fault inside the partitioner call surfaces as a rejection and leaves nothing behind.
    out = tmp_path / "omit"
    done = run_experiment(FIX_MANIFEST, fixture_manifest_sha(), out, ATLAS_S36_FIXTURE_FAULT="omit")
    rejected(done, "partition_invalid: leiden-seed-0")
    assert not out.exists() and leftover(tmp_path) == []


# ------------------------------------------------------------------------------- AC-S36-003


def pair_ari(first, second, n):
    """Independent O(n^2) pair-counting adjusted Rand index as an exact Fraction."""
    left = {v: i for i, members in enumerate(first) for v in members}
    right = {v: i for i, members in enumerate(second) for v in members}
    a = b = c = d = 0
    for x, y in combinations(range(n), 2):
        same_left, same_right = left[x] == left[y], right[x] == right[y]
        if same_left and same_right:
            a += 1
        elif same_left:
            b += 1
        elif same_right:
            c += 1
        else:
            d += 1
    return Fraction(2 * (a * d - b * c), (a + b) * (b + d) + (a + c) * (c + d))


def modularity(n, edges, parts):
    m = len(edges)
    label = {v: i for i, members in enumerate(parts) for v in members}
    degree = [0] * n
    for u, v in edges:
        degree[u] += 1
        degree[v] += 1
    total = Fraction(0)
    for i, members in enumerate(parts):
        inside = sum(1 for u, v in edges if label[u] == i and label[v] == i)
        total += Fraction(inside, m) - Fraction(sum(degree[v] for v in members), 2 * m) ** 2
    return total


def at(ratio):
    return Fraction(ratio["num"], ratio["den"])


def test_community_metrics_are_rederived(fx, tmp_path):
    done = run_verify(fx["dir"])
    assert done.returncode == 0, done.stderr
    summary = json.loads(done.stdout)
    assert {"lpa_rerun", "quality", "stability", "usefulness", "decision"} <= set(summary["checks"])
    assert summary["production_manifest"] is False and summary["outcome"] == "inconclusive"
    result = read_result(fx["dir"])
    document = read_partitions(fx["dir"])
    assert summary["canonical_sha256"] == result["canonical_sha256"]
    for graph_id, entry in zip(GRAPHS, document["graphs"], strict=True):
        nodes, edges = projected(graph_id)
        row = next(g for g in result["graphs"] if g["graph_id"] == graph_id)
        assert [r["run_id"] for r in row["runs"]] == [r["run_id"] for r in entry["runs"]]
        for run, reported in zip(entry["runs"], row["runs"], strict=True):
            assert at(reported["quality"]["modularity"]) == modularity(
                len(nodes), edges, run["communities"]
            )
        leiden = [r["communities"] for r in entry["runs"][1:]]
        pairs = [pair_ari(a, b, len(nodes)) for a, b in combinations(leiden, 2)]
        assert [at(r) for r in row["stability"]["pairwise_ari"]] == pairs
        assert len(pairs) == 45
        assert at(row["stability"]["mean_pairwise_ari"]) == sum(pairs, Fraction(0)) / 45
        lpa = entry["runs"][0]["communities"]
        assert [at(r) for r in row["stability"]["lpa_vs_leiden_ari"]] == [
            pair_ari(lpa, p, len(nodes)) for p in leiden
        ]
    rederive_quality_and_stability(result, document)
    rederive_usefulness(result, document)
    base = fx["dir"]

    def edit_quality(res):
        res["graphs"][0]["runs"][0]["quality"]["modularity"]["num"] += 1

    def edit_ari(res):
        res["graphs"][1]["stability"]["pairwise_ari"][0]["num"] += 1

    def edit_hits(res):
        res["usefulness"]["rows"][0]["hits"] += 1

    def edit_gold(res):
        res["usefulness"]["tasks"][0]["gold"].append(6)

    def edit_outcome(res):
        res["decision"]["outcome"] = "retain_lpa"

    for name, edit, reason in (
        ("quality", edit_quality, "metric_mismatch"),
        ("ari", edit_ari, "metric_mismatch"),
        ("hits", edit_hits, "usefulness_mismatch"),
        ("gold", edit_gold, "usefulness_mismatch"),
        ("outcome", edit_outcome, "decision_mismatch"),
    ):
        copy = copy_run(base, tmp_path / name)
        tamper_result(copy, edit)
        rejected(run_verify(copy), reason, "verification")

    def move_node(doc):
        runs = doc["graphs"][1]["runs"][1]["communities"]
        donor = next(i for i, members in enumerate(runs) if len(members) > 1)
        node = runs[donor].pop()
        target = next(i for i in range(len(runs)) if i != donor)
        runs[target] = sorted([*runs[target], node])
        runs.sort(key=lambda members: members[0])

    moved = copy_run(base, tmp_path / "moved")
    tamper_partitions(moved, move_node)
    rejected(run_verify(moved), "metric_mismatch", "verification")

    def swap_lpa(doc):
        runs = doc["graphs"][1]["runs"]
        assert runs[0]["communities"] != runs[1]["communities"]
        runs[0]["communities"] = runs[1]["communities"]

    swapped = copy_run(base, tmp_path / "swapped")
    tamper_partitions(swapped, swap_lpa)
    rejected(run_verify(swapped), "lpa_rerun_mismatch: synthetic-cliques", "verification")
    # Telemetry is bound but never judged: other timings and memory still verify.
    free = copy_run(base, tmp_path / "telemetry")
    telemetry = json.loads((free / "telemetry.json").read_bytes())
    for run in telemetry["runs"]:
        run["elapsed_ns"] += 12345
        run["peak_rss_bytes_after_call"] += 1
    (free / "telemetry.json").write_bytes(canonical(telemetry))
    assert run_verify(free).returncode == 0


def components(members, edges):
    """Connected components of the subgraph induced by the edges inside one community."""
    inside = set(members)
    neighbours = {v: [] for v in members}
    for u, v in edges:
        if u in inside and v in inside:
            neighbours[u].append(v)
            neighbours[v].append(u)
    seen, count = set(), 0
    for start in members:
        if start in seen:
            continue
        count += 1
        queue = [start]
        seen.add(start)
        while queue:
            node = queue.pop()
            for other in neighbours[node]:
                if other not in seen:
                    seen.add(other)
                    queue.append(other)
    return count


def rederive_quality_and_stability(result, document):
    """Every quality and stability field recomputed without the library under test."""
    for graph_id, entry in zip(GRAPHS, document["graphs"], strict=True):
        nodes, edges = projected(graph_id)
        row = next(g for g in result["graphs"] if g["graph_id"] == graph_id)
        m = len(edges)
        assert row["m"] == m and row["n"] == len(nodes)
        degree = [0] * len(nodes)
        for u, v in edges:
            degree[u] += 1
            degree[v] += 1
        assert row["isolated_nodes"] == degree.count(0)
        for run, reported in zip(entry["runs"], row["runs"], strict=True):
            parts = run["communities"]
            label = {v: i for i, members in enumerate(parts) for v in members}
            inside = sum(1 for u, v in edges if label[u] == label[v])
            sizes = sorted(len(members) for members in parts)
            k = len(sizes)
            cut = [c for c in parts if components(c, edges) > 1]
            quality = reported["quality"]
            assert at(quality["coverage"]) == Fraction(inside, m)
            assert quality["community_count"] == k
            assert quality["singleton_count"] == sizes.count(1)
            assert quality["size_max"] == sizes[-1]
            assert quality["size_median"] == sizes[-(-k // 2) - 1]  # nearest rank, ceil(k/2)
            assert quality["size_p90"] == sizes[-(-9 * k // 10) - 1]  # nearest rank, ceil(0.9k)
            assert quality["disconnected_communities"] == len(cut)
            assert at(quality["disconnected_share"]) == Fraction(len(cut), k)
        leiden = [r["communities"] for r in entry["runs"][1:]]
        stability = row["stability"]
        pairs = [pair_ari(a, b, len(nodes)) for a, b in combinations(leiden, 2)]
        same = sum(1 for a, b in combinations(leiden, 2) if a == b)
        assert at(stability["min_pairwise_ari"]) == min(pairs)
        assert stability["identical_pairs"] == same
        assert at(stability["identical_pair_share"]) == Fraction(same, 45)
        assert stability["distinct_leiden_partitions"] == len({json.dumps(p) for p in leiden})
        assert stability["lpa"] == "deterministic_no_seed"
        assert len(stability["lpa_vs_leiden_ari"]) == 10


def rederive_usefulness(result, document):
    """Seeds, gold, expansion and counts recomputed from the frozen S18 evidence only."""
    useful = json.loads(USEFUL.read_bytes())
    nodes, _edges = projected("s18-task-graph")
    index = {tuple(n): i for i, n in enumerate(nodes)}
    members = {}
    for entry in document["graphs"]:
        if entry["graph_id"] == "s18-task-graph":
            members = {r["run_id"]: r["communities"] for r in entry["runs"]}
    rows = []
    for task in useful["tasks"]:
        raw = useful["source_files"]
        gold = set()
        for (path, line, _name), i in index.items():
            data = raw[path].encode()
            start = 0
            for _ in range(line - 1):
                start = data.index(b"\n", start) + 1
            if any(
                s["path"] == path and s["start_byte"] <= start < s["end_byte"]
                for s in task["useful_spans"]
            ):
                gold.add(i)
        terms = {"payment": ["process_payment"], "request": ["send_request"]}.get(
            task["id"], [task["text"]] if task["id"] == "unicode" else ["retry", "transport"]
        )
        seeds = [i for (_p, _l, n), i in sorted(index.items(), key=lambda kv: kv[1]) if n in terms]
        if not seeds:
            seeds = [i for (p, _l, _n), i in sorted(index.items(), key=lambda kv: kv[1])
                     if task["text"] in raw[p]]  # fmt: skip
        for run_id, comms in members.items():
            owner = {v: c for c, group in enumerate(comms) for v in group}
            order = list(seeds)
            for seed in seeds:
                order += [v for v in comms[owner[seed]] if v not in order]
            for budget in (2, 4, 8):
                chosen = order[:budget]
                rows.append((run_id, budget, task["id"], chosen, len(gold & set(chosen)),
                             len(gold)))  # fmt: skip
    reported = [
        (r["run_id"], r["budget"], r["task_id"], r["selected"], r["hits"], r["gold_count"])
        for r in result["usefulness"]["rows"]
    ]
    assert rows and sorted(rows) == sorted(reported)


# ------------------------------------------------------------------------------- AC-S36-004


def test_community_replay_preserves_deterministic_results(fx, tmp_path):
    second = tmp_path / "second"
    done = run_experiment(FIX_MANIFEST, fixture_manifest_sha(), second, PYTHONHASHSEED="4242")
    assert done.returncode == 0, done.stderr
    first = fx["dir"]
    assert (first / "result.json").read_bytes() == (second / "result.json").read_bytes()
    plain_a = gzip.decompress((first / "partitions.json.gz").read_bytes())
    plain_b = gzip.decompress((second / "partitions.json.gz").read_bytes())
    assert plain_a == plain_b
    summary = json.loads(done.stdout)
    assert summary["canonical_sha256"] == read_result(first)["canonical_sha256"]
    tel_a = json.loads((first / "telemetry.json").read_bytes())
    tel_b = json.loads((second / "telemetry.json").read_bytes())
    assert tel_a["result_canonical_sha256"] == tel_b["result_canonical_sha256"]
    assert tel_a["canonical"] is False and tel_a["runs"] != []
    # Non-vacuity: the synthetic graph separates the partitioners and the seeds.
    runs = read_partitions(first)["graphs"][1]["runs"]
    leiden = [json.dumps(r["communities"]) for r in runs[1:]]
    assert len(set(leiden)) >= 2
    assert any(c != json.dumps(runs[0]["communities"]) for c in leiden)
    # A manifest over different graphs is a different experiment.
    other = manifest_copy()
    other["graphs"] = other["graphs"][:1]
    path = tmp_path / "other.json"
    other_sha = write_manifest(path, other)
    third = tmp_path / "third"
    done = run_experiment(path, other_sha, third)
    assert done.returncode == 0, done.stderr
    assert read_result(third)["canonical_sha256"] != read_result(first)["canonical_sha256"]
    assert read_result(third)["manifest_sha256"] == other_sha


def test_skipped_run_replays_identically(prod, tmp_path):
    again = make_prod_run(tmp_path, "again")
    assert (prod / "result.json").read_bytes() == (again / "result.json").read_bytes()
    assert gzip.decompress((prod / "partitions.json.gz").read_bytes()) == gzip.decompress(
        (again / "partitions.json.gz").read_bytes()
    )


# ------------------------------------------------------------------------------- AC-S36-005


def test_community_missing_provider_is_skipped(prod, tmp_path):
    result = read_result(prod)
    assert result["status"] == "skipped_missing_provider"
    assert result["decision"]["outcome"] == "skipped_missing_provider"
    assert result["decision"]["reasons"] == ["provider_missing"]
    assert result["provider"] == {
        "available": False,
        "kind": None,
        "missing": ["igraph", "leidenalg"],
        "versions": None,
    }
    document = read_partitions(prod)
    runs = [r for g in document["graphs"] for r in g["runs"]]
    assert len(document["graphs"]) == 3 and len(runs) == 3
    assert all(r["run_id"] == "lpa" and r["algorithm"] == "lpa" for r in runs)
    assert all(g["stability"] is None for g in result["graphs"])
    assert all(len(g["runs"]) == 1 and g["runs"][0]["quality"] for g in result["graphs"])
    assert result["manifest_sha256"] == PRODUCTION_SHA
    done = run_verify(prod, PROD_MANIFEST, PRODUCTION_SHA)
    assert done.returncode == 0, done.stderr
    assert json.loads(done.stdout)["production_manifest"] is True
    default = run_script(ROOT / "scripts/verify-community-experiment.py", ["--results", str(prod)])
    assert default.returncode == 0 and json.loads(default.stdout)["outcome"] == (
        "skipped_missing_provider"
    )
    out = tmp_path / "required"
    done = run_experiment(
        PROD_MANIFEST, PRODUCTION_SHA, out, site=False, flags=("-S",), extra=["--require-provider"]
    )
    rejected(done, "provider_required_missing")
    assert not out.exists() and leftover(tmp_path) == []
    # Without -S the dev environment itself has no provider (unless the host installed one).
    import importlib.util

    if importlib.util.find_spec("leidenalg") is None and importlib.util.find_spec("igraph") is None:
        plain = tmp_path / "plain"
        done = run_experiment(PROD_MANIFEST, PRODUCTION_SHA, plain, site=False)
        assert done.returncode == 0, done.stderr
        assert (plain / "result.json").read_bytes() == (prod / "result.json").read_bytes()
    # Partial provider: only igraph is importable, so leidenalg is the one reported missing.
    partial = tmp_path / "partial-site"
    shutil.copytree(SITE / "igraph", partial / "igraph")
    shutil.copytree(
        SITE / "igraph-0.0.0+atlas.fixture.dist-info",
        partial / "igraph-0.0.0+atlas.fixture.dist-info",
    )
    out = tmp_path / "partial"
    done = run_experiment(
        PROD_MANIFEST,
        PRODUCTION_SHA,
        out,
        site=False,
        flags=("-S",),
        PYTHONPATH=str(partial),
    )
    assert done.returncode == 0, done.stderr
    assert read_result(out)["provider"]["missing"] == ["leidenalg"]
    assert read_result(out)["status"] == "skipped_missing_provider"


# ------------------------------------------------------------------------------- AC-S36-006

PARTITION_TAMPERS = {
    "omitted": (lambda runs: runs[1]["communities"][0].pop(), "partition_node_omitted"),
    "duplicated": (
        lambda runs: runs[1]["communities"][1].append(runs[1]["communities"][0][0]),
        "partition_node_duplicated",
    ),
    "unknown_high": (
        lambda runs: runs[1]["communities"][0].append(51),
        "partition_node_unknown",
    ),
    "unknown_negative": (
        lambda runs: runs[1]["communities"][0].append(-1),
        "partition_node_unknown",
    ),
    "unsorted": (
        lambda runs: runs[1]["communities"][0].reverse(),
        "partition_noncanonical",
    ),
    "bool": (
        lambda runs: runs[1]["communities"][0].__setitem__(0, True),
        "partition_invalid_type",
    ),
}


@pytest.mark.parametrize("name", sorted(PARTITION_TAMPERS))
def test_community_invalid_partition_rejected(fx, tmp_path, name):
    action, reason = PARTITION_TAMPERS[name]
    copy = copy_run(fx["dir"], tmp_path / name)

    def edit(doc):
        runs = doc["graphs"][1]["runs"]
        assert len(runs[1]["communities"][0]) > 1
        action(runs)

    tamper_partitions(copy, edit)
    rejected(run_verify(copy), f"{reason}: leiden-seed-0", "verification")


def test_community_invalid_partition_table_and_run_set(fx, tmp_path):
    v = verifier()
    cases = [
        ([[0, 1], [2]], 3, None),
        ([[0, True], [2]], 3, "partition_invalid_type"),
        ([[0, 1], [3]], 3, "partition_node_unknown"),
        ([[0, 1], [1, 2]], 3, "partition_node_duplicated"),
        ([[0], [2]], 3, "partition_node_omitted"),
        ([[1, 0], [2]], 3, "partition_noncanonical"),
        ([[2], [0, 1]], 3, "partition_noncanonical"),
        # Precedence: type, unknown, duplicated, omitted, noncanonical.
        ([[0, 5, 5]], 3, "partition_node_unknown"),
        ([[0, 0], []], 3, "partition_node_duplicated"),
        ([[1, 0], []], 3, "partition_node_omitted"),
    ]
    assert all(v.validate_partition(c, n) == want for c, n, want in cases)
    # `nodes_sha256` of a foreign node table is its own reason, found before the partitions.
    copy = copy_run(fx["dir"], tmp_path / "table")
    tamper_partitions(copy, lambda doc: doc["graphs"][0].__setitem__("nodes_sha256", "0" * 64))
    rejected(run_verify(copy), "partition_node_table_mismatch: s18-task-graph", "verification")
    # A missing seed run is a different run set.
    gone = copy_run(fx["dir"], tmp_path / "gone")
    tamper_partitions(gone, lambda doc: doc["graphs"][1]["runs"].pop())
    rejected(run_verify(gone), "run_set_mismatch: synthetic-cliques", "verification")
    reseal_raw(gone)


# ------------------------------------------------------------------------------- AC-S36-007


def communities_of(repo: Path):
    return CodeGraph(repo).communities()


def s18_repo(base: Path) -> Path:
    repo = base / "s18repo"
    repo.mkdir()
    for name, text in json.loads(USEFUL.read_bytes())["source_files"].items():
        target = repo / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(text.encode())
    CodeGraph(repo).build()
    return repo


def test_community_experiment_preserves_default(fx, prod, tmp_path):
    assert codegraph.label_propagation is lp_module.label_propagation
    assert codegraph._LPA_MAX_ITERATIONS == 100
    assert (
        "label_propagation(adjacency, _LPA_MAX_ITERATIONS)"
        in (ROOT / "mcp-server/src/atlas_aci/codegraph.py").read_text()
    )
    locks = [ROOT / "mcp-server/pyproject.toml", ROOT / "mcp-server/uv.lock"]
    before = [sha(p.read_bytes()) for p in locks]
    repo = s18_repo(tmp_path)
    first = communities_of(repo)
    assert first["community_count"] == 3
    run_experiment(FIX_MANIFEST, fixture_manifest_sha(), tmp_path / "again")
    make_prod_run(tmp_path, "prod-again")
    assert communities_of(repo) == first
    # The default selection equals the LPA re-run on the independently projected export.
    nodes, edges = projected("s18-task-graph")
    adjacency = {i: set() for i in range(len(nodes))}
    for u, v in edges:
        adjacency[u].add(v)
        adjacency[v].add(u)
    labels = lpa_module().label_propagation(adjacency, 100)
    by_identity = {
        (m["path"], m["line"], m["name"]): m["community_id"] for m in first["communities"]
    }
    assert by_identity == {tuple(nodes[i]): labels[i] for i in range(len(nodes))}
    assert before == [sha(p.read_bytes()) for p in locks]
    for path in locks:
        text = path.read_text().lower()
        for name in ("leidenalg", "igraph", "graspologic", "networkx"):
            assert name not in text, (path, name)
    assert (
        LPA_FILE.read_bytes()
        and sha(LPA_FILE.read_bytes())
        == (json.loads(FIX_MANIFEST.read_bytes())["algorithms"]["lpa"]["module_sha256"])
    )
    # The output may never be placed under mcp-server/, directly or through a symlink.
    for target in (ROOT / "mcp-server" / "s36-never-created", ROOT / "mcp-server"):
        done = run_experiment(FIX_MANIFEST, fixture_manifest_sha(), target)
        assert done.returncode == 2 and done.stdout == ""
        assert done.stderr == "Usage error: out_inside_mcp_server\n"
    link = tmp_path / "link"
    link.symlink_to(ROOT / "mcp-server")
    done = run_experiment(FIX_MANIFEST, fixture_manifest_sha(), link / "s36-never-created")
    assert done.returncode == 2 and done.stderr == "Usage error: out_inside_mcp_server\n"
    assert not (ROOT / "mcp-server" / "s36-never-created").exists()
    assert BUNDLE.is_file() and S18_GRAPH.is_file() and FX.is_dir() and child_env()
