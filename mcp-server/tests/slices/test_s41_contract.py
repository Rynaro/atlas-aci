"""ACI-S41 acceptance tests: index jobs, retained generations, reader leases and garbage collection.

Real builds, real processes and real flocks; nothing under test is mocked. "Retained generation via
`storage.leases`" means the S41 acquire API and `atlas-aci jobs acquire`: the MCP query server
still reads only the current generation (it never takes these leases), which the live-store tests
in test_s41_gc.py and the production scenario prove stays untouched by garbage collection.
"""

from __future__ import annotations

import json
import os
import signal
import threading
import time
from pathlib import Path
from typing import Any

import pytest
import test_s41_support as s
from test_s41_support import lock_canary  # noqa: F401  (session canary, autouse)

NOW = 10_000


def jobs_dir(ns: s.Ns) -> Path:
    return ns.atlas / "jobs"


def job_doc(ns: s.Ns, job: str) -> dict[str, Any]:
    return json.loads((jobs_dir(ns) / job / "job.json").read_text())


def job_id_for(ns: s.Ns, **kwargs: Any) -> str:
    from atlas_aci.indexing import jobs

    return jobs.job_id(jobs.identity(ns.spec(**kwargs)))


def submit_args(ns: s.Ns, *extra: str, now: int = 100) -> list[str]:
    return [*ns.args("jobs", "submit"), "--langs", "python", "--now", str(now), *extra]


def gc_now(ns: s.Ns, now: int = NOW) -> dict[str, Any]:
    from atlas_aci.storage import generations as gen
    from atlas_aci.storage import retention

    return retention.gc(ns.ns, policy=gen.load_policy(ns.ns), now=now)


def reasons(receipt: dict[str, Any], key: str) -> dict[str, str]:
    return {row["generation_id"]: row["reason"] for row in receipt[key]}


@pytest.fixture
def ns(tmp_path: Path) -> s.Ns:
    return s.Ns(tmp_path)


# ------------------------------------------------------------------------------------ AC-001
def test_job_dedup(ns: s.Ns, monkeypatch: pytest.MonkeyPatch) -> None:
    """AC-S41-001: an identical job with a retained, digest-verified output is reused."""
    from atlas_aci.codegraph import CodeGraph
    from atlas_aci.indexing import extraction_cache as xc
    from atlas_aci.indexing import job_runner, jobs

    s.write_tree(ns.repo, 1)
    first = s.run_json(submit_args(ns))
    assert first["reused"] is False and first["decline_reason"] is None
    assert first["receipt"]["extraction_cache"]["parser_invocations"] == 5  # positive control
    gid = first["receipt"]["generation_id"]
    job = first["job_id"]
    assert (ns.atlas / "generations" / gid / "meta.json").is_file()
    assert first["current_generation_id"] == gid and first["receipt_is_current"] is True
    watched = [("job.json",), ("generations",), ("extraction-cache",)]
    before = {
        "job": s.listing(ns.atlas, under=f"jobs/{job}", exclude=(f"jobs/{job}/run.lock",)),
        "gens": s.listing(ns.atlas, under="generations"),
        "cache": s.listing(ns.atlas, under="extraction-cache"),
    }
    assert watched and all(before.values())
    stored = (jobs_dir(ns) / job / "receipt.json").read_bytes()

    again = s.run_json(submit_args(ns, now=200))
    assert again["reused"] is True and again["decline_reason"] is None
    assert again["receipt"] == json.loads(stored) == first["receipt"]
    after = {
        "job": s.listing(ns.atlas, under=f"jobs/{job}", exclude=(f"jobs/{job}/run.lock",)),
        "gens": s.listing(ns.atlas, under="generations"),
        "cache": s.listing(ns.atlas, under="extraction-cache"),
    }
    assert after == before

    # In-process: no job run, no build, no cache write.
    counts = {"run_job": 0, "build": 0, "write": 0}
    for owner, name, key in (
        (job_runner, "run_job", "run_job"),
        (CodeGraph, "build", "build"),
        (xc.ExtractionCache, "write", "write"),
    ):
        original = getattr(owner, name)

        def counting(*a: Any, _o: Any = original, _k: str = key, **kw: Any) -> Any:
            counts[_k] += 1
            return _o(*a, **kw)

        monkeypatch.setattr(owner, name, counting)
    envelope = jobs.submit(ns.spec(), now=300)
    assert envelope["reused"] is True and counts == {"run_job": 0, "build": 0, "write": 0}
    assert s.current(ns) is not None and s.current(ns)[0] == gid

    # Variant: another language set is another job and is not reused.
    other = s.run_json([*ns.args("jobs", "submit"), "--langs", "python,ruby", "--now", "400"])
    assert other["job_id"] != job and other["reused"] is False


def test_job_dedup_non_current_output_reports_the_live_generation(ns: s.Ns) -> None:
    """AC-S41-001 variant (condition 6): reuse of a retained non-current output says so."""
    s.write_tree(ns.repo, 1)
    first = s.run_json(submit_args(ns))
    gid1 = first["receipt"]["generation_id"]
    s.write_tree(ns.repo, 2)
    second = s.run_json(submit_args(ns, now=200))
    gid2 = second["receipt"]["generation_id"]
    assert gid1 != gid2 and second["receipt_is_current"] is True
    s.write_tree(ns.repo, 1)  # the identity includes the tree: v1 again is the first job
    live_inode = os.stat(ns.atlas / "graph.9.db").st_ino
    stored = (jobs_dir(ns) / first["job_id"] / "receipt.json").read_bytes()
    again = s.run_json(submit_args(ns, now=300))
    assert again["job_id"] == first["job_id"] and again["reused"] is True
    assert again["receipt"] == json.loads(stored)
    assert again["current_generation_id"] == gid2 and again["receipt_is_current"] is False
    assert os.stat(ns.atlas / "graph.9.db").st_ino == live_inode  # reuse never publishes


# ------------------------------------------------------------------------------------ AC-002
def kill_at_checkpoint(ns: s.Ns, at: int) -> str:
    job = job_id_for(ns)
    done = s.cli(
        submit_args(ns),
        env=s.faults({"checkpoint": {"action": "kill", "at": at}}),
    )
    assert done.returncode == -signal.SIGKILL, (done.returncode, done.stderr[-300:])
    return job


def checkpoint_lines(ns: s.Ns, job: str) -> list[dict[str, Any]]:
    raw = (jobs_dir(ns) / job / "checkpoint.log").read_bytes()
    lines = [json.loads(piece) for piece in raw.split(b"\n") if piece]
    assert lines and all(line["state"] in ("complete", "failed") for line in lines)
    return lines


def test_job_resume(tmp_path: Path) -> None:
    """AC-S41-002: a process that dies after a durable checkpoint resumes from it."""
    ns = s.Ns(tmp_path / "a")
    s.write_tree(ns.repo, 1)
    job = kill_at_checkpoint(ns, 2)
    assert job_doc(ns, job)["state"] == "running"
    assert s.current(ns) is None  # nothing was committed
    status = s.run_json(ns.args("jobs", "status"))
    assert status["jobs"]["interrupted"] == 1 and status["jobs"]["running"] == 0
    assert status["interrupted_jobs"] == [{"code": "JOB_INTERRUPTED", "job_id": job}]
    assert len(checkpoint_lines(ns, job)) == 2

    resumed = s.run_json([*ns.args("jobs", "resume"), "--job", job, "--now", "200"])
    receipt = resumed["receipt"]
    assert receipt["resumed_from"] == 2
    assert receipt["extraction_cache"]["parser_invocations"] == 3
    assert receipt["extraction_cache"]["disk_hits"] == 2
    assert job_doc(ns, job)["state"] == "completed" and job_doc(ns, job)["attempt"] == 2

    twin = s.twin(ns)  # an uninterrupted run is the control: 5 parses, the same generation id
    control = s.run_job_inprocess(twin, 1, 300)["receipt"]
    assert control["extraction_cache"]["parser_invocations"] == 5
    assert control["generation_id"] == receipt["generation_id"]


def test_job_resume_ignores_a_corrupt_entry_and_a_torn_tail(tmp_path: Path) -> None:
    """AC-S41-002 variants: the prefix ends at the first entry ``lookup`` rejects."""
    ns = s.Ns(tmp_path / "b")
    s.write_tree(ns.repo, 1)
    job = kill_at_checkpoint(ns, 2)
    key = checkpoint_lines(ns, job)[1]["key"]
    (ns.atlas / "extraction-cache" / "v1" / key[:2] / (key + ".json")).write_text('{"format":"x"}')
    receipt = s.run_json([*ns.args("jobs", "resume"), "--job", job, "--now", "200"])["receipt"]
    assert receipt["resumed_from"] == 1
    assert receipt["extraction_cache"]["parser_invocations"] == 4

    torn = s.Ns(tmp_path / "c")
    s.write_tree(torn.repo, 1)
    job = kill_at_checkpoint(torn, 2)
    log = jobs_dir(torn) / job / "checkpoint.log"
    log.write_bytes(log.read_bytes() + b'{"n":3,"path":"m2.py","sou')  # a torn, unchained tail
    receipt = s.run_json([*torn.args("jobs", "resume"), "--job", job, "--now", "200"])["receipt"]
    assert receipt["resumed_from"] == 2
    assert receipt["extraction_cache"]["parser_invocations"] == 3
    lines = checkpoint_lines(torn, job)
    assert [line["n"] for line in lines] == [1, 2, 3, 4, 5]  # the tail was cut, then appended


# ------------------------------------------------------------------------------------ AC-003
def blocked(ns: s.Ns, seam: str, now: int = 300) -> Any:
    proc = s.spawn(submit_args(ns, now=now), env=s.faults({seam: {"action": "block"}}))
    s.seam_wait(proc, seam)
    return proc


def live_pair(ns: s.Ns) -> tuple[str, str | None]:
    found = s.current(ns)
    assert found is not None
    return found


@pytest.mark.parametrize("how", ["marker", "sigterm"])
def test_cancel_no_publish(ns: s.Ns, how: str) -> None:
    """AC-S41-003: cancellation before publication leaves the previous generation selected."""
    first = s.run_job_inprocess(ns, 1, 100)
    gid1 = first["receipt"]["generation_id"]
    pair = live_pair(ns)
    s.write_tree(ns.repo, 2)
    job = job_id_for(ns)
    snapshots_before = s.content_listing(ns.atlas, under="source-snapshots")
    assert snapshots_before
    proc = blocked(ns, "staged")
    if how == "marker":
        outcome = s.run_json([*ns.args("jobs", "cancel"), "--job", job])
        assert outcome["outcome"] == "requested"
    else:
        proc.send_signal(signal.SIGTERM)
        time.sleep(0.3)
    s.release(proc)
    code, out, _err = s.finish(proc)
    assert code == 4 and s.last_json(out)["code"] == "JOB_CANCELLED"
    doc = job_doc(ns, job)
    assert doc["state"] == "cancelled" and doc["cancel"] == "honoured"
    assert doc["staged_generation_id"] not in (None, gid1)
    assert live_pair(ns) == pair  # the previous active generation stays selected
    assert s.content_listing(ns.atlas, under="source-snapshots") == snapshots_before
    assert not (ns.atlas / "generations" / doc["staged_generation_id"]).exists()
    assert list(ns.atlas.glob(".graph.9.db.tmp.*")) == []

    # Control: the same job without a cancel publishes a different generation.
    envelope = s.run_json(submit_args(ns, now=400))
    assert envelope["receipt"]["generation_id"] != gid1
    assert live_pair(ns)[0] == envelope["receipt"]["generation_id"]


def test_cancel_after_the_commit_is_too_late(ns: s.Ns) -> None:
    """AC-S41-003 control: a cancel after publication completes the job as `too_late`."""
    s.run_job_inprocess(ns, 1, 100)
    s.write_tree(ns.repo, 2)
    job = job_id_for(ns)
    proc = blocked(ns, "post_replace")
    outcome = s.run_json([*ns.args("jobs", "cancel"), "--job", job])
    assert outcome["outcome"] == "too_late"
    s.release(proc)
    code, out, _err = s.finish(proc)
    assert code == 0
    receipt = s.last_json(out)["receipt"]
    assert receipt["cancel"] == "too_late" and job_doc(ns, job)["state"] == "completed"
    assert live_pair(ns)[0] == receipt["generation_id"]


@pytest.mark.parametrize("how", ["marker", "sigterm"])
def test_cancel_while_waiting_for_the_writer_lock(ns: s.Ns, how: str) -> None:
    """AC-S41-003 variant (condition 7): the wait for `.index.lock` is cancellable."""
    s.run_job_inprocess(ns, 1, 100)
    s.write_tree(ns.repo, 2)
    job = job_id_for(ns)
    before = s.listing(ns.atlas, exclude=("jobs", ".index.lock"))
    before = {k: v for k, v in before.items() if not k.startswith("jobs/")}
    with s.hold_flock(ns.atlas / ".index.lock", 2):  # LOCK_EX held by another party
        proc = blocked(ns, "runner_start")
        s.release(proc)
        time.sleep(0.5)  # the job is now polling for the lock
        if how == "marker":
            assert s.run_json([*ns.args("jobs", "cancel"), "--job", job])["outcome"] == "requested"
        else:
            proc.send_signal(signal.SIGTERM)
        code, out, _err = s.finish(proc, timeout=30)
    assert code == 4 and s.last_json(out)["code"] == "JOB_CANCELLED"
    after = {
        k: v
        for k, v in s.listing(ns.atlas, exclude=("jobs", ".index.lock")).items()
        if not k.startswith("jobs/")
    }
    assert after == before  # nothing was written to the store
    assert job_doc(ns, job)["state"] == "cancelled"


# ------------------------------------------------------------------------------------ AC-004
@pytest.mark.parametrize("holder", ["in_process", "separate_process"])
def test_leased_generation_retained(ns: s.Ns, holder: str, monkeypatch: pytest.MonkeyPatch) -> None:
    """AC-S41-004: a retained generation held through `storage.leases` survives GC."""
    from atlas_aci.storage import generations as gen
    from atlas_aci.storage import leases

    g1, g2, g3 = s.build_farms(ns, (1, 2, 3))
    s.write_policy(ns, keep_last=1)
    spy = s.FlockSpy(monkeypatch)
    proc = None
    lease = None
    if holder == "in_process":
        lease = leases.acquire(ns.ns, g1)
        assert lease.origin == "retained"
        assert spy.count("leases", "SH|NB", "granted") == 1
    else:
        proc = s.spawn([*ns.args("jobs", "acquire"), "--generation", g1, "--hold"])
        held = json.loads(s.read_line(proc))
        assert held["generation_id"] == g1 and held["origin"] == "retained"
    receipt = gc_now(ns)
    kept, gone = reasons(receipt, "retained"), reasons(receipt, "retired")
    assert kept[g1] == "leased" and kept[g3] == "current"
    assert gone == {g2: "outside_window"}  # positive control: GC retires what it may
    assert not (ns.atlas / "generations" / g2).exists()
    if lease is not None:
        data = lease.read_source("m0.py")
        assert data == s.tree_files(1)["m0.py"]
        gid = lease.db.execute("SELECT value FROM manifest WHERE key='generation_id'").fetchone()[0]
        assert gid == g1
        lease.close()
    else:
        assert proc is not None and proc.stdin is not None
        proc.stdin.close()
        assert s.finish(proc)[0] == 0
    again = gc_now(ns)
    assert reasons(again, "retired") == {g1: "outside_window"}
    assert gen.read_meta_at(ns.ns, g1) is None


# ------------------------------------------------------------------------------------ AC-005
def test_expired_reference_visible(ns: s.Ns) -> None:
    """AC-S41-005: a retired retained generation reports explicit reference expiry."""
    from atlas_aci.enforcement import ToolError
    from atlas_aci.protocol import resources
    from atlas_aci.storage import leases

    g1, _g2, g3 = s.build_farms(ns, (1, 2, 3))
    s.write_policy(ns, keep_last=1)
    control = leases.acquire(ns.ns, g1)  # before GC the reference is valid
    assert control.generation_id == g1
    control.close()
    assert g1 in reasons(gc_now(ns), "retired")
    with pytest.raises(ToolError) as caught:
        leases.acquire(ns.ns, g1)
    assert caught.value.code == "EXPIRED_REFERENCE" and caught.value.retry_hint == "retry_current"
    assert caught.value.message == leases.EXPIRED_MESSAGE
    done = s.cli([*ns.args("jobs", "acquire"), "--generation", g1])
    assert done.returncode == 3
    envelope = s.last_json(done.stdout)
    assert envelope["code"] == "EXPIRED_REFERENCE" and envelope["retry_hint"] == "retry_current"
    assert envelope["generation_id"] == g1
    assert resources.error_data("EXPIRED_REFERENCE").code == -32002
    with pytest.raises(ToolError) as bad:
        leases.acquire(ns.ns, "not-a-generation")
    assert bad.value.code == "INVALID_REFERENCE"
    ok = leases.acquire(ns.ns, g3)  # the current generation is still acquirable
    ok.close()


# ------------------------------------------------------------------------------------ AC-006
def test_gc_replay(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """AC-S41-006: a second GC over the same reachable set changes nothing."""
    from atlas_aci.storage import leases

    ns = s.Ns(tmp_path / "a")
    farms = s.build_farms(ns, (1, 2, 3, 4))
    s.write_tree(ns.repo, 5)
    job = kill_at_checkpoint(ns, 1)  # an interrupted job holding a checkpointed cache key
    protected = checkpoint_lines(ns, job)[0]["key"]
    s.write_policy(ns, keep_last=1, max_cache_bytes=1)
    lease = leases.acquire(ns.ns, farms[0])
    first = gc_now(ns)
    assert first["retired"] and first["cache"]["pruned"] >= 1  # non-vacuous
    assert first["retained"] and any(r["reason"] == "leased" for r in first["retained"])
    assert sum(first["swept"].values()) >= 1
    key_file = ns.atlas / "extraction-cache" / "v1" / protected[:2] / (protected + ".json")
    assert key_file.is_file()  # the interrupted job's checkpoint survived the prune

    quiet = ("gc-receipt.json", "generations/gc-receipt.json", "generations/gc.lock", ".index.lock")
    before = s.listing(ns.atlas, exclude=quiet)
    with s.OpSpy() as spy:
        second = gc_now(ns)

    def receipt_io(path: str) -> bool:
        return os.path.basename(path).startswith(".tmp.") or path.endswith("gc-receipt.json")

    assert second["retired"] == [] and second["cache"]["pruned"] == 0
    assert set(second["swept"].values()) == {0} and second["recovered"] == 0
    assert second["retained_digest"] == first["retained_digest"]
    assert s.listing(ns.atlas, exclude=quiet) == before  # bytes, mtimes and inodes
    changed = [
        op
        for op in spy.mutations(receipt_io)
        if op[0] in ("link", "rename", "unlink", "rmdir", "mkdir", "replace")
    ]
    assert changed == [], changed
    assert spy.ops  # the observer saw the run
    lease.close()
    third = gc_now(ns)  # control: releasing the lease changes the retained set
    assert third["retained_digest"] != first["retained_digest"] and third["retired"]


def test_gc_replay_after_a_crash_converges(tmp_path: Path) -> None:
    """AC-S41-006 crash variant: GC killed after the rename, then rerun, equals a clean GC."""
    ns = s.Ns(tmp_path / "a")
    s.build_farms(ns, (1, 2, 3, 4))
    s.write_policy(ns, keep_last=1)
    import shutil

    backup = tmp_path / "backup"  # hardlinked: the same inodes come back after the first GC
    shutil.copytree(ns.graphs, backup, copy_function=os.link)
    code = (
        "import os,signal,sys\n"
        "from pathlib import Path\n"
        "from atlas_aci.storage import generations as g, retention as r\n"
        "ns=g.namespace(Path(sys.argv[1]),Path(sys.argv[2]))\n"
        "def kill():\n os.kill(os.getpid(),signal.SIGKILL); os._exit(137)\n"
        "r.gc(ns,policy=g.load_policy(ns),now=10000,hooks={sys.argv[3]:kill})\n"
    )
    done = s.python_c(code, str(ns.repo), str(ns.graphs), "gc_post_rename")
    assert done.returncode == -signal.SIGKILL, done.stderr[-300:]
    retired = ns.atlas / "generations" / "retired"
    assert retired.is_dir() and list(retired.iterdir())  # the crash left a renamed farm behind
    recovered = gc_now(ns)
    assert recovered["recovered"] >= 1 and list(retired.iterdir()) == []
    shutil.rmtree(ns.graphs)
    shutil.copytree(backup, ns.graphs, copy_function=os.link)
    clean = gc_now(ns)  # an uninterrupted GC of the identical starting store
    assert recovered["retained_digest"] == clean["retained_digest"]
    assert reasons(recovered, "retained") == reasons(clean, "retained")


# ------------------------------------------------------------------------------------ AC-007
def test_current_generation_retained(ns: s.Ns) -> None:
    """AC-S41-007: the current generation stays acquirable whatever the policy says."""
    from atlas_aci.storage import leases

    g1, g2 = s.build_farms(ns, (1, 2))
    s.write_policy(ns, keep_last=1, max_age_seconds=0, max_bytes=0)
    receipt = gc_now(ns, NOW)
    assert reasons(receipt, "retained") == {g2: "current"}
    assert reasons(receipt, "retired") == {g1: "outside_window"}  # control: the old one goes
    with leases.acquire(ns.ns, g2) as lease:
        assert lease.origin == "retained" and lease.generation_id == g2
        assert lease.read_source("m0.py") == s.tree_files(2)["m0.py"]
    assert live_pair(ns)[0] == g2

    # Variant: a generation published by plain `index` (never captured) is captured by GC.
    s.write_tree(ns.repo, 3)
    done = s.cli(
        ["index", "--repo", str(ns.repo), "--graph-root", str(ns.graphs), "--langs", "python"]
    )
    assert done.returncode == 0, done.stderr[-300:]
    g3 = live_pair(ns)[0]
    assert not (ns.atlas / "generations" / g3).exists()
    receipt = gc_now(ns, NOW + 1)
    assert reasons(receipt, "retained") == {g3: "current"} and g2 in reasons(receipt, "retired")
    with leases.acquire(ns.ns, g3) as lease:
        assert lease.origin == "retained" and lease.generation_id == g3


# ------------------------------------------------------------------------------------ AC-008
def acquire_in_thread(ns: s.Ns, gid: str, hooks: dict[str, Any]) -> tuple[threading.Thread, dict]:
    from atlas_aci.storage import leases

    box: dict[str, Any] = {}

    def run() -> None:
        try:
            box["lease"] = leases.acquire(ns.ns, gid, hooks=hooks)
        except BaseException as exc:  # recorded and asserted by the caller
            box["error"] = exc

    thread = threading.Thread(target=run, daemon=True)
    thread.start()
    return thread, box


def gc_in_thread(ns: s.Ns, hooks: dict[str, Any]) -> tuple[threading.Thread, dict]:
    from atlas_aci.storage import generations as gen
    from atlas_aci.storage import retention

    box: dict[str, Any] = {}

    def run() -> None:
        try:
            box["receipt"] = retention.gc(
                ns.ns, policy=gen.load_policy(ns.ns), now=NOW, hooks=hooks
            )
        except BaseException as exc:  # recorded and asserted by the caller
            box["error"] = exc

    thread = threading.Thread(target=run, daemon=True)
    thread.start()
    return thread, box


def assert_outcome(box: dict[str, Any]) -> str:
    """Every outcome is a readable lease or an explicit, retryable expiry."""
    from atlas_aci.enforcement import ToolError

    if "lease" in box:
        return "readable"
    error = box["error"]
    assert isinstance(error, ToolError) and error.code == "EXPIRED_REFERENCE", repr(error)
    assert error.retry_hint == "retry_current"
    return "expired"


@pytest.mark.parametrize("order", ["reader_first", "gc_first", "open_then_retire", "recapture"])
def test_lease_gc_race(ns: s.Ns, order: str, monkeypatch: pytest.MonkeyPatch) -> None:
    """AC-S41-008: lease acquisition racing reclamation, under deterministic barriers."""
    from atlas_aci.storage import leases

    g1, _g2, g3 = s.build_farms(ns, (1, 2, 3))
    s.write_policy(ns, keep_last=1)
    spy = s.FlockSpy(monkeypatch)
    reached, go = threading.Event(), threading.Event()

    def barrier() -> None:
        reached.set()
        assert go.wait(10)

    if order == "reader_first":  # the reader holds SH before GC tries EX
        thread, box = acquire_in_thread(ns, g1, {"post_lock": barrier})
        assert reached.wait(10)
        receipt = gc_now(ns)
        assert reasons(receipt, "retained")[g1] == "leased"
        go.set()
        thread.join(10)
        assert assert_outcome(box) == "readable"
        assert box["lease"].read_source("m1.py") == s.tree_files(1)["m1.py"]
        box["lease"].close()
    elif order == "gc_first":  # GC holds EX when the reader tries
        thread, box = gc_in_thread(ns, {"gc_post_lock_ex": barrier})
        assert reached.wait(10)
        with pytest.raises(Exception) as caught:
            leases.acquire(ns.ns, g1)
        assert (
            caught.value.code == "EXPIRED_REFERENCE" and caught.value.retry_hint == "retry_current"
        )
        assert spy.count("leases", "SH|NB", "EWOULDBLOCK") == leases.ACQUIRE_ATTEMPTS
        go.set()
        thread.join(10)
        assert g1 in reasons(box["receipt"], "retired")
        with leases.acquire(ns.ns, g3) as lease:  # retry with the current generation
            assert lease.generation_id == g3
    elif order == "open_then_retire":  # the reader has opened the lock before GC retires the farm
        thread, box = acquire_in_thread(ns, g1, {"pre_lock": barrier})
        assert reached.wait(10)
        assert g1 in reasons(gc_now(ns), "retired")
        assert not (ns.atlas / "generations" / g1).exists()
        go.set()
        thread.join(10)
        assert assert_outcome(box) == "expired"
        # The shared lock WAS granted (the inode lives on); the name check is what refuses it.
        assert spy.count("leases", "SH|NB", "granted") == 1
    else:  # recapture: the lock file or the directory behind the open descriptors was replaced
        farm = ns.atlas / "generations" / g1

        def swap_lock() -> None:
            s.replace_by_copy(farm / "lease.lock", lambda data: data)
            barrier()

        thread, box = acquire_in_thread(ns, g1, {"pre_lock": swap_lock})
        assert reached.wait(10)
        go.set()
        thread.join(10)
        assert assert_outcome(box) == "expired"  # the lock inode no longer matches the name
        with leases.acquire(ns.ns, g1) as lease:  # a retry finds the (new) lock file readable
            assert lease.generation_id == g1


# ------------------------------------------------------------------------------------ AC-009
def tamper_farm_pack(farm: Path) -> None:
    pack = next((farm / "source-snapshots").glob("pack.*.bin"))
    s.replace_by_copy(pack, s.flip_byte)


def tamper_farm_db(farm: Path) -> None:
    s.replace_by_copy(farm / "graph.9.db", s.flip_byte)


def delete_farm_manifest(farm: Path) -> None:
    next((farm / "source-snapshots").glob("manifest.*.json")).unlink()


def rewrite_meta_sha(farm: Path) -> None:
    def mutate(data: bytes) -> bytes:
        doc = json.loads(data)
        doc["db_sha256"] = "0" * 64
        return (json.dumps(doc, sort_keys=True, separators=(",", ":")) + "\n").encode()

    s.replace_by_copy(farm / "meta.json", mutate)


@pytest.mark.parametrize(
    ("case", "reason"),
    [
        ("retired", "output_retired"),
        ("pack", "output_digest_invalid"),
        ("db", "output_digest_invalid"),
        ("manifest", "output_digest_invalid"),
        ("meta", "output_digest_invalid"),
    ],
)
def test_missing_job_output(
    ns: s.Ns, case: str, reason: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """AC-S41-009: a receipt pointing at an expired or digest-invalid output is not reused."""
    from atlas_aci.indexing import job_runner, jobs
    from atlas_aci.storage import leases

    s.write_tree(ns.repo, 1)
    first = jobs.submit(ns.spec(), now=100)
    gid1 = first["receipt"]["generation_id"]
    farm = ns.atlas / "generations" / gid1
    if case == "retired":
        s.run_job_inprocess(ns, 2, 200)  # v2 becomes current; G1 is then outside the window
        s.write_policy(ns, keep_last=1)
        assert gid1 in reasons(gc_now(ns), "retired")
        s.write_tree(ns.repo, 1)  # the identity names the tree: restore v1
    else:
        live_inode = os.stat(ns.atlas / "graph.9.db").st_ino
        {
            "pack": tamper_farm_pack,
            "db": tamper_farm_db,
            "manifest": delete_farm_manifest,
            "meta": rewrite_meta_sha,
        }[case](farm)
        # Non-vacuity: the live store and a live reader are untouched by the tamper.
        assert os.stat(ns.atlas / "graph.9.db").st_ino == live_inode
        assert s.current(ns) is not None and s.current(ns)[0] == gid1
    calls = {"n": 0}
    original = job_runner.run_job

    def counting(*a: Any, **kw: Any) -> Any:
        calls["n"] += 1
        return original(*a, **kw)

    monkeypatch.setattr(job_runner, "run_job", counting)
    again = jobs.submit(ns.spec(), now=300)
    assert again["reused"] is False and again["decline_reason"] == reason
    assert jobs_doc_attempt(ns, again["job_id"]) == 2 and calls["n"] == 1
    assert again["receipt"]["generation_id"] == gid1  # content addressed: the same generation
    with leases.acquire(ns.ns, gid1) as lease:  # the new farm acquires and verifies
        assert lease.origin == "retained"


def jobs_doc_attempt(ns: s.Ns, job: str) -> int:
    return int(job_doc(ns, job)["attempt"])


def test_missing_job_output_control_is_reused(ns: s.Ns) -> None:
    """AC-S41-009 control: an untampered output is reused."""
    from atlas_aci.indexing import jobs

    s.write_tree(ns.repo, 1)
    jobs.submit(ns.spec(), now=100)
    again = jobs.submit(ns.spec(), now=200)
    assert again["reused"] is True and again["decline_reason"] is None
