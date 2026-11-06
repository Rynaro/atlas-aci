"""ACI-S41 reader leases on retained generations: cross-process holders, read-only acquisition,
farm verification parity with `CodeGraph.reader_lease`, probes and the same-device rule."""

from __future__ import annotations

import errno
import json
import os
import shutil
import signal
import sqlite3
import threading
from pathlib import Path
from typing import Any

import pytest
import test_s41_support as s
from test_s41_support import lock_canary  # noqa: F401  (session canary, autouse)

NOW = 9000


@pytest.fixture
def ns(tmp_path: Path) -> s.Ns:
    return s.Ns(tmp_path)


def gc_run(ns: s.Ns, **policy: Any) -> dict[str, Any]:
    from atlas_aci.storage import generations as gen
    from atlas_aci.storage import retention

    s.write_policy(ns, **policy)
    return retention.gc(ns.ns, policy=gen.load_policy(ns.ns), now=NOW)


def retired(receipt: dict[str, Any]) -> set[str]:
    return {r["generation_id"] for r in receipt["retired"]}


def holder(ns: s.Ns, gid: str) -> Any:
    proc = s.spawn([*ns.args("jobs", "acquire"), "--generation", gid, "--hold"])
    lease = json.loads(s.read_line(proc))
    assert lease["generation_id"] == gid and lease["origin"] == "retained"
    return proc


# ------------------------------------------------------------------------- cross-process
@pytest.mark.parametrize("ending", ["eof", "sigkill", "sigterm"])
def test_a_holder_process_protects_a_farm_until_it_ends(ns: s.Ns, ending: str) -> None:
    """A separate process holds a retained generation via `jobs acquire --hold`; its end of
    life, however it comes, drops the lease (the kernel releases the flock)."""
    g1, g2, g3 = s.build_farms(ns, (1, 2, 3))
    proc = holder(ns, g1)
    first = gc_run(ns, keep_last=1)
    assert retired(first) == {g2} and g1 in {r["generation_id"] for r in first["retained"]}
    assert (ns.atlas / "generations" / g1).is_dir()
    assert proc.poll() is None  # a read-only holder: it never wrote to the store
    if ending == "eof":
        assert proc.stdin is not None
        proc.stdin.close()
        assert s.finish(proc)[0] == 0
    elif ending == "sigkill":
        proc.kill()
        proc.wait(timeout=15)
        assert proc.returncode == -signal.SIGKILL
    else:  # the handler also makes the holder stop when it is PID 1 of a container
        proc.send_signal(signal.SIGTERM)
        assert s.finish(proc)[0] == 0
    assert retired(gc_run(ns, keep_last=1)) == {g1}
    assert (ns.atlas / "generations" / g3).is_dir()


def test_status_counts_leases_and_probe_leased_is_read_only(ns: s.Ns) -> None:
    from atlas_aci.storage import generations as gen
    from atlas_aci.storage import leases

    g1, g2 = s.build_farms(ns, (1, 2))
    proc = holder(ns, g1)
    assert leases.probe_leased(ns.atlas / "generations" / g1) is True
    assert leases.probe_leased(ns.atlas / "generations" / g2) is False
    assert leases.probe_leased(ns.atlas / "generations" / ("c" * 64)) is False  # no farm, no lock
    before = s.listing(ns.atlas)
    status = s.run_json(ns.args("jobs", "status"))
    assert s.listing(ns.atlas) == before  # status writes nothing
    assert status["retained"]["generations"] == 2 and status["retained"]["leased"] == 1
    rows = {row["generation_id"]: row for row in status["generations"]}
    assert rows[g1]["leased"] is True and rows[g2]["leased"] is False
    assert rows[g2]["current"] is True and status["current_generation_id"] == g2
    farms = [ns.atlas / "generations" / g for g in (g1, g2)]
    seen: dict[tuple[int, int], int] = {}
    for farm in farms:
        for row in gen.farm_files(ns.ns, farm.name):
            seen[(row[0], row[1])] = row[2]
    assert status["retained"]["bytes"] == sum(seen.values())
    assert rows[g1]["bytes"] > 0 and rows[g2]["seq"] > rows[g1]["seq"]
    assert proc.stdin is not None
    proc.stdin.close()
    assert s.finish(proc)[0] == 0


def test_a_probe_is_absorbed_by_the_acquisition_backoff(
    ns: s.Ns, monkeypatch: pytest.MonkeyPatch
) -> None:
    import fcntl

    from atlas_aci.storage import leases

    g1, _g2 = s.build_farms(ns, (1, 2))
    spy = s.FlockSpy(monkeypatch)
    held = threading.Event()

    def probe() -> None:
        with s.hold_flock(ns.atlas / "generations" / g1 / "lease.lock", fcntl.LOCK_EX):
            held.set()
            for _ in range(1000):  # released as soon as the reader's first attempt was refused
                if spy.count("leases", "SH|NB", "EWOULDBLOCK") >= 1:
                    return
                threading.Event().wait(0.01)

    thread = threading.Thread(target=probe, daemon=True)
    thread.start()
    assert held.wait(10)
    with leases.acquire(ns.ns, g1) as lease:
        assert lease.origin == "retained"
    thread.join(10)
    assert spy.count("leases", "SH|NB", "EWOULDBLOCK") >= 1
    assert spy.count("leases", "SH|NB", "granted") == 1


# ----------------------------------------------------------------------------- read-only
def stat_listing(root: Path) -> dict[str, tuple[Any, ...]]:
    found = {}
    for path in sorted(root.rglob("*")):
        if path.is_file() and not path.is_symlink():
            info = path.stat()
            found[str(path.relative_to(root))] = (
                s.sha256_of(path),
                info.st_size,
                info.st_mtime_ns,
                info.st_ctime_ns,
                info.st_ino,
            )
    return found


def test_acquisition_is_read_only_and_a_capture_is_not(ns: s.Ns) -> None:
    from atlas_aci.storage import leases

    with s.OpSpy() as writes:  # control: capturing writes, links and renames
        g1, g2 = s.build_farms(ns, (1, 2))
    kinds = {kind for kind, _path in writes.mutations()}
    assert {"link", "rename", "mkdir"} <= kinds
    before = stat_listing(ns.atlas)
    dirs_before = sorted(str(p) for p in ns.atlas.rglob("*") if p.is_dir())
    with s.OpSpy() as spy:
        for gid in (g1, g2):
            with leases.acquire(ns.ns, gid) as lease:
                assert lease.read_source("m0.py")
                lease.db.execute("SELECT COUNT(*) FROM symbols").fetchone()
    assert (
        spy.ops and spy.mutations() == []
    )  # no write-flag open, link, rename, unlink, mkdir, utime
    assert spy.uris and all("mode=ro" in uri for uri in spy.uris)
    assert stat_listing(ns.atlas) == before  # bytes, mtime, ctime and inode
    assert sorted(str(p) for p in ns.atlas.rglob("*") if p.is_dir()) == dirs_before


def test_acquire_and_status_create_nothing_in_a_store_without_jobs(tmp_path: Path) -> None:
    ns = s.Ns(tmp_path)
    s.write_tree(ns.repo, 1)
    done = s.cli(
        ["index", "--repo", str(ns.repo), "--graph-root", str(ns.graphs), "--langs", "python"]
    )
    assert done.returncode == 0, done.stderr[-300:]
    before = sorted(str(p.relative_to(ns.graphs)) for p in ns.graphs.rglob("*"))
    status = s.run_json(ns.args("jobs", "status"))
    assert status["jobs"] == {
        "running": 0,
        "interrupted": 0,
        "completed": 0,
        "cancelled": 0,
        "failed": 0,
    }
    current = s.current(ns)
    assert current is not None
    live = s.run_json([*ns.args("jobs", "acquire"), "--generation", current[0]])
    assert live["origin"] == "live" and live["generation_id"] == current[0]  # the FD-pinned path
    other = s.cli([*ns.args("jobs", "acquire"), "--generation", "d" * 64])
    assert other.returncode == 3 and s.last_json(other.stdout)["code"] == "EXPIRED_REFERENCE"
    assert sorted(str(p.relative_to(ns.graphs)) for p in ns.graphs.rglob("*")) == before
    assert not (ns.atlas / "generations").exists() and not (ns.atlas / "jobs").exists()


# ------------------------------------------------------------------------------- L-PARITY
def sql_mutation(statement: str):
    def mutate(path: Path) -> None:
        temp = path.with_name(path.name + ".tamper")
        shutil.copyfile(path, temp)
        con = sqlite3.connect(temp)
        con.execute(statement)
        con.commit()
        con.close()
        os.replace(temp, path)

    return mutate


def file_mutation(path: Path) -> None:
    s.replace_by_copy(path, s.flip_byte)


def refresh_meta(farm: Path) -> None:
    """Make the meta agree with a mutated db, so that the rest of the verification decides."""

    def mutate(data: bytes) -> bytes:
        doc = json.loads(data)
        doc["db_sha256"] = s.sha256_of(farm / "graph.9.db")
        doc["db_size"] = (farm / "graph.9.db").stat().st_size
        return (json.dumps(doc, sort_keys=True, separators=(",", ":")) + "\n").encode()

    s.replace_by_copy(farm / "meta.json", mutate)


def test_farm_verification_equals_reader_lease_on_a_healthy_store(ns: s.Ns) -> None:
    from atlas_aci.codegraph import CodeGraph
    from atlas_aci.storage import generations as gen

    (g1,) = s.build_farms(ns, (1,))
    graph = CodeGraph(ns.repo, read_only=True, graph_root=ns.graphs)
    with graph.reader_lease() as reader:
        live = (reader._lease_generation_id, reader.snapshot_id)
    farm = ns.atlas / "generations" / g1
    meta = gen.read_meta_at(ns.ns, g1)
    assert meta is not None
    verified = gen.verify_generation(ns.ns, farm, meta)
    try:
        assert (verified.generation_id, verified.snapshot_id) == live
    finally:
        verified.close()
    # A farm still verifies after the live `retain` unlinked its manifest and pack.
    s.run_job_inprocess(ns, 2, NOW)
    s.run_job_inprocess(ns, 3, NOW + 1)
    store = ns.atlas / "source-snapshots"
    assert not (store / f"manifest.{live[1]}.json").exists()
    gen.verify_generation(ns.ns, farm, meta).close()


@pytest.mark.parametrize(
    "mutation",
    ["db_gid", "db_retrieval", "db_binding", "pack", "manifest"],
)
def test_farm_verification_rejects_what_reader_lease_rejects(ns: s.Ns, mutation: str) -> None:
    """Parity: the same corruption (applied to the live store and to the farm, never in place
    on a shared inode) is refused by both paths."""
    from atlas_aci.codegraph import CodeGraph
    from atlas_aci.enforcement import ToolError
    from atlas_aci.storage import generations as gen

    (g1,) = s.build_farms(ns, (1,))
    farm = ns.atlas / "generations" / g1
    live_store, farm_store = ns.atlas / "source-snapshots", farm / "source-snapshots"
    sid = s.current(ns)[1]  # type: ignore[index]
    pack = next(live_store.glob("pack.*.bin")).name
    table = {
        "db_gid": (
            sql_mutation("UPDATE manifest SET value='" + "0" * 64 + "' WHERE key='generation_id'"),
            "graph.9.db",
        ),
        "db_retrieval": (sql_mutation("DELETE FROM retrieval_documents"), "graph.9.db"),
        "db_binding": (
            sql_mutation(
                "DELETE FROM occurrences WHERE rowid=(SELECT MIN(rowid) FROM occurrences)"
            ),
            "graph.9.db",
        ),
        "pack": (file_mutation, pack),
        "manifest": (file_mutation, f"manifest.{sid}.json"),
    }
    mutate, name = table[mutation]
    meta = gen.read_meta_at(ns.ns, g1)
    assert meta is not None
    live_inode = (ns.atlas / "graph.9.db").stat().st_ino
    if name == "graph.9.db":
        mutate(ns.atlas / name)
        mutate(farm / name)
        refresh_meta(farm)
        meta = gen.read_meta_at(ns.ns, g1)
        assert meta is not None
    else:
        mutate(live_store / name)
        mutate(farm_store / name)
    assert (ns.atlas / "graph.9.db").stat().st_ino != live_inode or name != "graph.9.db"
    graph = CodeGraph(ns.repo, read_only=True, graph_root=ns.graphs)
    with pytest.raises(ToolError), graph.reader_lease():
        pass
    with pytest.raises(gen.GenerationInvalid):
        gen.verify_generation(ns.ns, farm, meta)


def test_a_foreign_repository_is_refused(ns: s.Ns, tmp_path: Path) -> None:
    from atlas_aci.storage import generations as gen
    from atlas_aci.storage import snapshots as snap

    (g1,) = s.build_farms(ns, (1,))
    farm = ns.atlas / "generations" / g1
    meta = gen.read_meta_at(ns.ns, g1)
    assert meta is not None
    foreign = gen.Namespace(
        repo=tmp_path / "elsewhere", graph_root=ns.ns.graph_root, atlas_dir=ns.atlas
    )
    (tmp_path / "elsewhere").mkdir()
    with pytest.raises(gen.GenerationInvalid):
        gen.verify_generation(foreign, farm, meta)
    with pytest.raises(ValueError, match="foreign repository"):
        snap.acquire(farm, str(meta.snapshot_id), repository=tmp_path / "elsewhere")


# ----------------------------------------------------------------------------- same device
def test_a_cross_device_link_is_a_typed_refusal(ns: s.Ns, monkeypatch: pytest.MonkeyPatch) -> None:
    """The real tmpfs case needs a mount, which a test cannot make; EXDEV is injected instead
    (the production scenario records the real kernel behaviour)."""
    from atlas_aci.indexing import jobs
    from atlas_aci.storage import generations as gen

    s.write_tree(ns.repo, 1)

    real_link = os.link

    def refuse(*args: Any, **kwargs: Any) -> None:
        fd = kwargs.get("dst_dir_fd")
        if fd is not None and "/generations/" in os.readlink(f"/proc/self/fd/{fd}"):
            raise OSError(errno.EXDEV, "Invalid cross-device link")  # only farm links cross devices
        real_link(*args, **kwargs)

    monkeypatch.setattr(os, "link", refuse)
    with pytest.raises(jobs.JobError) as failed:
        jobs.submit(ns.spec(), now=100)
    assert failed.value.code == "JOB_FAILED" and failed.value.reason == "capture_failed"
    live = s.current(ns)
    assert live is not None  # the commit happened; the job is failed, and a rerun reconciles
    with pytest.raises(gen.StoreError) as capture:
        gen.capture_current(ns.ns, now=1, locked=False)
    assert capture.value.reason == "cross_device"
    assert jobs.store_error(capture.value).code == "CROSS_DEVICE"
    monkeypatch.undo()
    done = jobs.submit(ns.spec(), now=200)  # the failed job starts a new attempt, converges
    assert done["receipt"]["generation_id"] == live[0]
    assert gen.read_meta_at(ns.ns, live[0]) is not None


def test_the_canary_describes_the_directory_in_use(lock_canary: dict[str, Any]) -> None:  # noqa: F811
    assert lock_canary["nlink"] == 2 and lock_canary["second_fd_blocks"]
    assert lock_canary["process_blocks"] and lock_canary["same_inode"]


def test_equal_content_rebuilt_into_a_new_inode_is_recaptured_unless_leased(ns: s.Ns) -> None:
    """Condition 2: gids collide for equal content (the probe: frameworks that add no facts, or any
    rebuild), so a farm whose db is another inode than the live db is retired and recaptured; a
    leased one is a typed `capture_conflict`. `retire_invalid` returns False for a leased farm."""
    from atlas_aci.storage import generations as gen
    from atlas_aci.storage import leases, retention

    (g1,) = s.build_farms(ns, (1,))
    farm_db = ns.atlas / "generations" / g1 / "graph.9.db"
    assert farm_db.stat().st_ino == (ns.atlas / "graph.9.db").stat().st_ino
    done = s.cli(
        ["index", "--repo", str(ns.repo), "--graph-root", str(ns.graphs), "--langs", "python"]
    )
    assert done.returncode == 0, done.stderr[-300:]
    assert s.current(ns) is not None and s.current(ns)[0] == g1  # the same content: the same id
    assert farm_db.stat().st_ino != (ns.atlas / "graph.9.db").stat().st_ino  # a new publication
    lease = leases.acquire(ns.ns, g1)
    with pytest.raises(gen.StoreError) as conflict:
        gen.capture_current(ns.ns, now=5, locked=False)
    assert conflict.value.reason == "capture_conflict"
    assert retention.retire_invalid(ns.ns, g1) is False  # a leased farm is never retired
    assert farm_db.is_file()  # the leased farm is intact
    lease.close()
    meta = gen.capture_current(ns.ns, now=6, locked=False)
    assert meta is not None and meta.generation_id == g1 and meta.captured_at == 6
    assert farm_db.stat().st_ino == (ns.atlas / "graph.9.db").stat().st_ino
    assert retention.retire_invalid(ns.ns, "e" * 64) is True  # absent: nothing to retire
    again = gen.capture_current(ns.ns, now=7, locked=False)  # idempotent: inode equality
    assert again == meta


def test_farm_verification_equals_reader_lease_on_a_framework_corpus(tmp_path: Path) -> None:
    """A wider L-PARITY corpus: a Ruby store with the rails_models adapter and a table map."""
    from atlas_aci.codegraph import CodeGraph
    from atlas_aci.indexing import jobs
    from atlas_aci.storage import generations as gen

    ns = s.Ns(tmp_path)
    fixtures = s.TESTS / "fixtures" / "rails_models"
    shutil.copytree(fixtures / "project", ns.repo, dirs_exist_ok=True)
    table = (fixtures / "table-map.json").read_bytes()
    spec = ns.spec(langs=("ruby",), frameworks=("rails_models",), table=table)
    receipt = jobs.submit(spec, now=10)["receipt"]
    graph = CodeGraph(
        ns.repo,
        langs=["ruby"],
        read_only=True,
        graph_root=ns.graphs,
        frameworks=("rails_models",),
        rails_table_map=table,
    )
    with graph.reader_lease() as reader:
        live = (reader._lease_generation_id, reader.snapshot_id)
    meta = gen.read_meta_at(ns.ns, receipt["generation_id"])
    assert meta is not None
    verified = gen.verify_generation(ns.ns, ns.atlas / "generations" / meta.generation_id, meta)
    try:
        assert (verified.generation_id, verified.snapshot_id) == live
    finally:
        verified.close()


def test_the_patched_context_is_not_reentrant(ns: s.Ns) -> None:
    """Condition 16: `patched()` rebinds module globals, so a second build cannot overlap it."""
    from atlas_aci.indexing import extraction_cache as xc
    from atlas_aci.indexing import job_runner, jobs

    s.write_tree(ns.repo, 1)
    ident = jobs.identity(ns.spec())
    store = jobs.JobStore(ns.ns, jobs.job_id(ident))
    store.create()
    run = job_runner.Run(
        ns.spec(),
        __import__("atlas_aci.storage.generations", fromlist=["x"]).RetentionPolicy(),
        store,
        ident,
        jobs._fresh_doc(store.job, ident, ns.ns.graph_root),
        now=1,
        hooks=None,
        signals=False,
        continuing=False,
    )
    original = xc.ExtractionCache
    with job_runner.patched(run):
        assert xc.ExtractionCache is not original
        with pytest.raises(RuntimeError, match="not reentrant"), job_runner.patched(run):
            pass
    assert xc.ExtractionCache is original  # restored
