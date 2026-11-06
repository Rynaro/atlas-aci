"""ACI-S41 garbage collection: policy, roots, sweep, cache pruning, busy states and crash seams."""

from __future__ import annotations

import fcntl
import json
import os
import shutil
import signal
from pathlib import Path
from typing import Any

import pytest
import test_s41_support as s
from test_s41_support import lock_canary  # noqa: F401  (session canary, autouse)

NOW = 2000


def gc_run(ns: s.Ns, now: int = NOW, **policy: Any) -> dict[str, Any]:
    from atlas_aci.storage import generations as gen
    from atlas_aci.storage import retention

    if policy:
        s.write_policy(ns, **policy)
    return retention.gc(ns.ns, policy=gen.load_policy(ns.ns), now=now)


def kept(receipt: dict[str, Any]) -> dict[str, str]:
    return {r["generation_id"]: r["reason"] for r in receipt["retained"]}


def gone(receipt: dict[str, Any]) -> dict[str, str]:
    return {r["generation_id"]: r["reason"] for r in receipt["retired"]}


@pytest.fixture(scope="module")
def template(tmp_path_factory: pytest.TempPathFactory) -> dict[str, Any]:
    """Four real farms G1..G4 (captured at 1000..1030) under one repository path.

    Cases copy the store with hardlinks (cheap; GC only renames and unlinks names).
    """
    base = tmp_path_factory.mktemp("s41gc")
    ns = s.Ns(base, graphs="template")
    farms = s.build_farms(ns, (1, 2, 3, 4), now0=1000)
    return {"base": base, "ns": ns, "farms": farms}


def case(template: dict[str, Any], name: str) -> s.Ns:
    ns = s.Ns(template["base"], graphs=f"case-{name}")
    shutil.copytree(template["ns"].graphs, ns.graphs, copy_function=os.link)
    return ns


# ---------------------------------------------------------------------------------- policy
@pytest.mark.parametrize(
    ("reason", "body", "field"),
    [
        ("not_json", b"{nope", ""),
        ("not_json", b'{"format":"atlas-aci-retention.v1","keep_last":NaN}', ""),
        ("duplicate_key", b'{"format":"atlas-aci-retention.v1","keep_last":1,"keep_last":2}', ""),
        ("wrong_type", b"[1]", ""),
        ("bad_format", b'{"keep_last":1}', "/format"),
        ("bad_format", b'{"format":"other"}', "/format"),
        ("unknown_field", b'{"format":"atlas-aci-retention.v1","extra":1}', "/extra"),
        ("wrong_type", b'{"format":"atlas-aci-retention.v1","keep_last":true}', "/keep_last"),
        ("wrong_type", b'{"format":"atlas-aci-retention.v1","max_bytes":"1"}', "/max_bytes"),
        (
            "wrong_type",
            b'{"format":"atlas-aci-retention.v1","max_age_seconds":1.5}',
            "/max_age_seconds",
        ),
        ("out_of_range", b'{"format":"atlas-aci-retention.v1","keep_last":0}', "/keep_last"),
        ("out_of_range", b'{"format":"atlas-aci-retention.v1","keep_last":65}', "/keep_last"),
        (
            "out_of_range",
            b'{"format":"atlas-aci-retention.v1","max_age_seconds":-1}',
            "/max_age_seconds",
        ),
        (
            "out_of_range",
            b'{"format":"atlas-aci-retention.v1","max_concurrent_jobs":17}',
            "/max_concurrent_jobs",
        ),
        (
            "out_of_range",
            b'{"format":"atlas-aci-retention.v1","checkpoint_summary_interval":0}',
            "/checkpoint_summary_interval",
        ),
    ],
)
def test_the_policy_schema_table(reason: str, body: bytes, field: str) -> None:
    from atlas_aci.storage import generations as gen

    with pytest.raises(gen.PolicyError) as caught:
        gen.parse_policy(body)
    assert (caught.value.reason, caught.value.field) == (reason, field)
    assert str(caught.value) == f"invalid retention policy ({reason} at {field or '/'})"


def test_policy_reasons_unreadable_and_too_large_and_every_reason_is_covered(
    tmp_path: Path,
) -> None:
    from atlas_aci.storage import generations as gen

    ns = s.Ns(tmp_path)
    ns.atlas.mkdir(parents=True)
    (ns.atlas / "retention.json").mkdir()  # not a regular file
    with pytest.raises(gen.PolicyError) as unreadable:
        gen.load_policy(ns.ns)
    (ns.atlas / "retention.json").rmdir()
    (ns.atlas / "retention.json").write_bytes(b" " * (64 * 1024 + 1))
    with pytest.raises(gen.PolicyError) as large:
        gen.load_policy(ns.ns)
    assert (unreadable.value.reason, large.value.reason) == ("unreadable", "too_large")
    full = {
        "format": gen.FORMAT_POLICY,
        "keep_last": 3,
        "max_age_seconds": 0,
        "max_bytes": 10,
        "max_cache_bytes": 20,
        "max_concurrent_jobs": 2,
        "checkpoint_summary_interval": 8,
    }
    (ns.atlas / "retention.json").write_text(json.dumps(full))
    policy = gen.load_policy(ns.ns)
    assert (policy.keep_last, policy.max_age_seconds, policy.max_bytes) == (3, 0, 10)
    assert (policy.max_cache_bytes, policy.max_concurrent_jobs) == (20, 2)
    (ns.atlas / "retention.json").unlink()
    assert gen.load_policy(ns.ns) == gen.RetentionPolicy()  # absent means the defaults
    assert len(set(gen.POLICY_REASONS)) == 8


def test_a_bad_policy_is_a_usage_error_before_anything_is_written(tmp_path: Path) -> None:
    ns = s.Ns(tmp_path)
    ns.atlas.mkdir(parents=True)
    (ns.atlas / "retention.json").write_text('{"format":"atlas-aci-retention.v1","keep_last":0}')
    for command in ("status", "gc"):
        done = s.cli(ns.args("jobs", command))
        assert done.returncode == 2 and done.stdout == ""
        assert (
            "retention.json: invalid retention policy (out_of_range at /keep_last)" in done.stderr
        )
    assert sorted(p.name for p in ns.atlas.iterdir()) == ["retention.json"]


# ---------------------------------------------------------------------------- window and trim
def test_window_age_and_byte_trimming_use_the_injected_now(template: dict[str, Any]) -> None:
    from atlas_aci.storage import generations as gen

    g1, g2, g3, g4 = template["farms"]
    ns = case(template, "window")
    receipt = gc_run(ns, 1031, keep_last=2)
    assert kept(receipt) == {g4: "current", g3: "window"}
    assert gone(receipt) == {g1: "outside_window", g2: "outside_window"}

    ns = case(template, "age")
    receipt = gc_run(ns, 1125, keep_last=3, max_age_seconds=100)  # ages: g4 95, g3 105, g2 115
    assert kept(receipt) == {g4: "current"}
    assert gone(receipt) == {g1: "outside_window", g2: "max_age", g3: "max_age"}
    ns = case(template, "age-edge")  # the boundary is strictly older than the limit
    receipt = gc_run(ns, 1120, keep_last=3, max_age_seconds=100)  # g3 is exactly 100 old
    assert set(kept(receipt)) == {g4, g3} and gone(receipt)[g2] == "max_age"

    ns = case(template, "bytes")
    files = [*gen.farm_files(ns.ns, g4), *gen.farm_files(ns.ns, g3)]
    limit = gen.distinct_bytes(files)
    receipt = gc_run(ns, 1031, keep_last=3, max_bytes=limit)  # the oldest window member goes
    assert set(kept(receipt)) == {g4, g3} and gone(receipt)[g2] == "max_bytes"
    assert gone(receipt)[g1] == "outside_window"

    ns = case(template, "hard-roots")  # hard roots are never trimmed
    receipt = gc_run(ns, 99999, keep_last=1, max_age_seconds=0, max_bytes=0)
    assert kept(receipt) == {g4: "current"} and len(gone(receipt)) == 3


def test_a_running_jobs_base_is_a_root_only_while_its_run_lock_is_held(
    template: dict[str, Any],
) -> None:
    """Condition 12: the test holds ``run.lock`` itself (a real build holds the writer lock too,
    so GC reports WRITER_BUSY during builds; see the next test)."""
    from atlas_aci.indexing import jobs

    g1 = template["farms"][0]
    ns = case(template, "job-base")
    s.write_tree(ns.repo, 1)
    ident = jobs.identity(ns.spec())
    store = jobs.JobStore(ns.ns, jobs.job_id(ident))
    store.create()
    doc = jobs._fresh_doc(store.job, ident, ns.ns.graph_root)
    store.write_job({**doc, "state": "running", "base_generation_id": g1})
    s.write_policy(ns, keep_last=1)
    with s.hold_flock(store.path("run.lock"), fcntl.LOCK_EX):
        receipt = gc_run(ns, 1031)
    assert kept(receipt)[g1] == "job_base"
    receipt = gc_run(ns, 1032)  # the runner is gone: the base is no longer protected
    assert g1 in gone(receipt)


def test_gc_is_writer_busy_during_a_build_and_gc_busy_with_another_gc(
    template: dict[str, Any],
) -> None:
    from atlas_aci.storage import generations as gen
    from atlas_aci.storage import retention

    ns = case(template, "busy")
    s.write_policy(ns, keep_last=1)
    policy = gen.load_policy(ns.ns)
    before = s.listing(ns.atlas, exclude=("generations/gc.lock", ".index.lock"))
    with s.hold_flock(ns.atlas / ".index.lock", fcntl.LOCK_EX):
        with pytest.raises(gen.StoreError) as busy:
            retention.gc(ns.ns, policy=policy, now=NOW, wait_seconds=0.2)
        assert busy.value.reason == "writer_busy"
        done = s.cli([*ns.args("jobs", "gc"), "--now", "5", "--wait-seconds", "0.3"])
        assert done.returncode == 75 and s.last_json(done.stdout)["code"] == "WRITER_BUSY"
        assert done.stderr.strip() == "jobs: WRITER_BUSY"
    assert s.listing(ns.atlas, exclude=("generations/gc.lock", ".index.lock")) == before
    with s.hold_flock(ns.atlas / "generations" / "gc.lock", fcntl.LOCK_EX):
        with pytest.raises(gen.StoreError) as other:
            retention.gc(ns.ns, policy=policy, now=NOW)
        assert other.value.reason == "gc_busy"
        with pytest.raises(
            gen.StoreError
        ) as invalid:  # condition 8: one arbiter, no unlocked deletion
            retention.retire_invalid(ns.ns, template["farms"][0])
        assert invalid.value.reason == "gc_busy"
        done = s.cli([*ns.args("jobs", "gc"), "--now", "5"])
        assert done.returncode == 75 and s.last_json(done.stdout)["code"] == "GC_BUSY"
    assert (ns.atlas / "generations" / template["farms"][0]).is_dir()  # nothing was deleted
    assert retention.retire_invalid(ns.ns, template["farms"][0]) is True


def test_recovery_tolerates_a_vanished_entry(template: dict[str, Any], monkeypatch) -> None:
    from atlas_aci.storage import retention

    ns = case(template, "recover")
    retired = ns.atlas / "generations" / "retired"
    retired.mkdir()
    (retired / "real.x").mkdir()
    (retired / "real.x" / "file").write_text("x")
    real = os.listdir
    monkeypatch.setattr(
        os, "listdir", lambda p=".": [*real(p), "ghost.y"] if isinstance(p, int) else real(p)
    )
    assert retention._recover(ns.ns) == 2  # "ghost.y" vanished between the listing and removal
    assert real(retired) == []


# ----------------------------------------------------------------------------------- sweep
def test_the_orphan_sweep_removes_only_staging_and_only_under_the_lock(
    template: dict[str, Any],
) -> None:
    from atlas_aci.indexing import jobs
    from atlas_aci.storage import generations as gen
    from atlas_aci.storage import retention

    ns = case(template, "sweep")
    atlas = ns.atlas
    planted = [
        atlas / ".graph.9.db.tmp.abc",
        atlas / ".graph.9.db.tmp.abc-journal",
        atlas / ".sources-stage.abc" / "blob",
        atlas / ".index-attempt.abc",
        atlas / "extraction-cache" / "v1" / "ab" / ".entry-abc",
        atlas / "generations" / ".stage.abc" / "graph.9.db",
    ]
    for path in planted:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("orphan")
    protected = [
        atlas / "source-snapshots" / ".catalog.abc",  # not GC's: the next publication's retain
        atlas / "index-attempt.v1.json",
    ]
    for path in protected:
        path.write_text("keep")
    s.write_tree(ns.repo, 1)
    ident = jobs.identity(ns.spec())
    idle = jobs.JobStore(ns.ns, jobs.job_id(ident))
    idle.create()
    (idle.dir / ".tmp.idle").write_text("orphan")
    other_ident = {**ident, "source_files": 99}
    busy = jobs.JobStore(ns.ns, jobs.job_id(other_ident))
    busy.create()
    (busy.dir / ".tmp.busy").write_text("live writer")
    live_db = (atlas / "graph.9.db").stat().st_ino
    s.write_policy(ns, keep_last=4)
    policy = gen.load_policy(ns.ns)
    with s.hold_flock(atlas / ".index.lock", fcntl.LOCK_EX):  # a build is running: nothing is swept
        with pytest.raises(gen.StoreError):
            retention.gc(ns.ns, policy=policy, now=NOW, wait_seconds=0.1)
        assert all(p.exists() for p in planted)
    with s.hold_flock(busy.path("run.lock"), fcntl.LOCK_EX):
        receipt = retention.gc(ns.ns, policy=policy, now=NOW)
    assert receipt["swept"] == {
        "graph_stage": 2,
        "sources_stage": 1,
        "attempt_temp": 1,
        "cache_temp": 1,
        "capture_stage": 1,
        "job_temp": 1,
    }
    assert not any(p.exists() for p in planted)
    assert all(p.exists() for p in protected)  # source-snapshots and diagnostics are never swept
    assert not (idle.dir / ".tmp.idle").exists() and (busy.dir / ".tmp.busy").exists()
    assert (atlas / "graph.9.db").stat().st_ino == live_db


def test_an_invalid_farm_is_retired_without_following_links(template: dict[str, Any]) -> None:
    from atlas_aci.storage import generations as gen

    ns = case(template, "invalid")
    farm = ns.atlas / "generations" / ("a" * 64)
    farm.mkdir()
    (farm / "meta.json").write_text("{not json")
    (farm / "lease.lock").write_text("")
    outside = ns.tmp / "outside"
    outside.mkdir()
    (outside / "precious").write_text("keep")
    (ns.atlas / "generations" / ("b" * 64)).symlink_to(outside)
    s.write_policy(ns, keep_last=4)
    metas = {m.generation_id: m for m in gen.list_generations(ns.ns)}
    assert metas["a" * 64].valid is False and metas["b" * 64].valid is False
    receipt = gc_run(ns, NOW)
    assert gone(receipt) == {"a" * 64: "invalid_meta", "b" * 64: "invalid_meta"}
    assert not farm.exists() and not (ns.atlas / "generations" / ("b" * 64)).is_symlink()
    assert (outside / "precious").read_text() == "keep"  # the link target is never followed


# ------------------------------------------------------------------------------- cache pruning
def test_cache_prune_protects_live_checkpoints_and_ages_out_abandoned_jobs(tmp_path: Path) -> None:
    ns = s.Ns(tmp_path)
    s.write_tree(ns.repo, 1)
    done = s.cli(
        [*ns.args("jobs", "submit"), "--langs", "python", "--now", "5"],
        env=s.faults({"checkpoint": {"action": "kill", "at": 2}}),
    )
    assert done.returncode == -signal.SIGKILL
    [job] = [p for p in (ns.atlas / "jobs").iterdir() if p.name != "slots"]
    keys = [json.loads(x)["key"] for x in (job / "checkpoint.log").read_text().splitlines()]
    cache = ns.atlas / "extraction-cache" / "v1"
    files = {k: cache / k[:2] / (k + ".json") for k in keys}
    assert len(keys) == 2 and all(p.is_file() for p in files.values())
    os.utime(job / "job.json", (5000, 5000))
    receipt = gc_run(ns, 5010, max_cache_bytes=1, max_age_seconds=1000)  # live: protected
    assert all(p.is_file() for p in files.values()) and receipt["cache"]["pruned"] == 0
    assert receipt["swept"]["graph_stage"] >= 1  # the killed build's stage was swept
    receipt = gc_run(ns, 20000, max_cache_bytes=1, max_age_seconds=1000)  # abandoned: pruned
    assert receipt["cache"]["pruned"] == 2 and not any(p.exists() for p in files.values())


# --------------------------------------------------------------------------- live-store invariance
def test_gc_never_touches_the_live_store_and_a_live_reader_keeps_reading(
    template: dict[str, Any],
) -> None:
    from atlas_aci.codegraph import CodeGraph

    ns = case(template, "live")
    live = ns.atlas / "graph.9.db"
    inode, digest = live.stat().st_ino, s.sha256_of(live)
    snapshots = s.listing(ns.atlas, under="source-snapshots")
    assert snapshots
    graph = CodeGraph(ns.repo, read_only=True, graph_root=ns.graphs)
    with graph.reader_lease() as reader:
        before = reader.db.execute("SELECT COUNT(*) FROM symbols").fetchone()[0]
        with s.OpSpy() as spy:
            receipt = gc_run(ns, NOW, keep_last=1)
        assert gone(receipt)  # non-vacuous: farms were retired
        assert reader.db.execute("SELECT COUNT(*) FROM symbols").fetchone()[0] == before
        assert reader.read_source("m0.py", None) == s.tree_files(4)["m0.py"]
    assert live.stat().st_ino == inode and s.sha256_of(live) == digest
    assert s.listing(ns.atlas, under="source-snapshots") == snapshots
    forbidden = ("unlink", "rename", "replace", "rmdir")
    live_paths = (ns.atlas / "graph.9.db", ns.atlas / "source-snapshots")
    for path in live_paths:
        assert spy.under(forbidden, path) == [], path
    # Positive control: the observer does see the same kinds of operations under generations/.
    assert spy.under(("rename",), ns.atlas / "generations") or any(
        "retired" in p for _k, p, _f in spy.ops
    )
    assert any(k == "unlink" and "/generations/retired/" in p for k, p, _f in spy.ops)


def test_retain_counts_hold_after_jobs_and_gc(template: dict[str, Any]) -> None:
    ns = case(template, "retain")
    gc_run(ns, NOW, keep_last=1)
    store = ns.atlas / "source-snapshots"
    catalog = json.loads((store / "catalog.json").read_text())
    manifests = sorted(p.name for p in store.glob("manifest.*.json"))
    assert len(catalog) == 2 and len(manifests) == 2  # the inline retain still owns the live store


# ------------------------------------------------------------------------------ crash seams
@pytest.mark.parametrize(
    "seam",
    [
        "gc_post_recover",
        "gc_post_capture",
        "gc_post_sweep",
        "gc_post_reachability",
        "gc_post_lock_ex",
        "gc_post_rename",
        "gc_mid_delete",
        "gc_pre_receipt",
    ],
)
def test_a_gc_killed_at_every_seam_converges(template: dict[str, Any], seam: str) -> None:
    from atlas_aci.storage import retention

    assert seam in retention.GC_SEAMS
    ns = case(template, f"kill-{seam}")
    clean = case(template, f"clean-{seam}")
    code = (
        "import os,signal,sys\n"
        "from pathlib import Path\n"
        "from atlas_aci.storage import generations as g, retention as r\n"
        "ns=g.namespace(Path(sys.argv[1]),Path(sys.argv[2]))\n"
        "def kill():\n os.kill(os.getpid(),signal.SIGKILL); os._exit(137)\n"
        "r.gc(ns,policy=g.load_policy(ns),now=2000,hooks={sys.argv[3]:kill})\n"
    )
    s.write_policy(ns, keep_last=1)
    s.write_policy(clean, keep_last=1)
    done = s.python_c(code, str(ns.repo), str(ns.graphs), seam)
    assert done.returncode == -signal.SIGKILL, (done.returncode, done.stderr[-300:])
    recovered = gc_run(ns, NOW)
    expected = gc_run(clean, NOW)
    assert set(kept(recovered)) == set(kept(expected)) == {template["farms"][3]}
    assert recovered["retained_digest"] == expected["retained_digest"]
    retired = ns.atlas / "generations" / "retired"
    assert not retired.exists() or list(retired.iterdir()) == []
    from atlas_aci.storage import generations as gen

    farms = [p.name for p in (ns.atlas / "generations").iterdir() if gen.GID.fullmatch(p.name)]
    assert farms == [template["farms"][3]]
    again = gc_run(ns, NOW)  # and a further run changes nothing
    assert again["retired"] == [] and again["retained_digest"] == recovered["retained_digest"]
