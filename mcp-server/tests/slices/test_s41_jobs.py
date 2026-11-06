"""ACI-S41 jobs: identity, the submit/resume table, cancel outcomes, slots and the crash matrix."""

from __future__ import annotations

import hashlib
import json
import shutil
import signal
from pathlib import Path
from typing import Any

import pytest
import test_s41_support as s
from test_s41_support import lock_canary  # noqa: F401  (session canary, autouse)

FIXTURES = s.TESTS / "fixtures" / "rails_models"


def jobs_dir(ns: s.Ns) -> Path:
    return ns.atlas / "jobs"


def job_doc(ns: s.Ns, job: str) -> dict[str, Any]:
    return json.loads((jobs_dir(ns) / job / "job.json").read_text())


def job_of(ns: s.Ns, **kwargs: Any) -> str:
    from atlas_aci.indexing import jobs

    return jobs.job_id(jobs.identity(ns.spec(**kwargs)))


def sargs(ns: s.Ns, *extra: str, now: int = 100) -> list[str]:
    return [*ns.args("jobs", "submit"), "--langs", "python", "--now", str(now), *extra]


def kill_run(ns: s.Ns, spec: dict[str, Any], now: int = 100) -> None:
    done = s.cli(sargs(ns, now=now), env=s.faults(spec))
    assert done.returncode == -signal.SIGKILL, (done.returncode, done.stderr[-300:])


@pytest.fixture
def ns(tmp_path: Path) -> s.Ns:
    return s.Ns(tmp_path)


# ------------------------------------------------------------------------------------ identity
def test_identity_is_canonical_repeatable_and_free_of_time(ns: s.Ns) -> None:
    from atlas_aci.indexing import jobs
    from atlas_aci.storage import evidence as ev

    s.write_tree(ns.repo, 1)
    first = jobs.identity(ns.spec())
    second = jobs.identity(ns.spec())
    assert first == second and jobs.job_id(first) == jobs.job_id(second)
    assert jobs.job_id(first) == hashlib.sha256(ev.canonical(first).encode()).hexdigest()
    text = ev.canonical(first)
    assert "since" not in first and "time" not in text.lower().replace("timeout", "")
    assert first["format"] == jobs.FORMAT_IDENTITY and first["source_files"] == 5
    code = (
        "import sys\nfrom pathlib import Path\n"
        "from atlas_aci.indexing import jobs\nfrom atlas_aci.storage import generations as g\n"
        "ns=g.namespace(Path(sys.argv[1]),Path(sys.argv[2]))\n"
        "print(jobs.job_id(jobs.identity(jobs.JobSpec(ns,('python',),(),None))))\n"
    )
    ids = {
        s.python_c(code, str(ns.repo), str(ns.graphs), env={"PYTHONHASHSEED": seed}).stdout.strip()
        for seed in ("0", "4242")
    }
    assert ids == {jobs.job_id(first)}


def test_identity_changes_with_every_input(tmp_path: Path) -> None:
    from atlas_aci.indexing import jobs

    ns = s.Ns(tmp_path)
    s.write_tree(ns.repo, 1)
    base = jobs.job_id(jobs.identity(ns.spec()))
    s.write_tree(ns.repo, 2)  # a changed file
    changed_file = jobs.job_id(jobs.identity(ns.spec()))
    s.write_tree(ns.repo, 1)
    other_lang = jobs.job_id(jobs.identity(ns.spec(langs=("python", "ruby"))))
    table = (FIXTURES / "table-map.json").read_bytes()
    framework = jobs.job_id(jobs.identity(ns.spec(langs=("python", "ruby"), frameworks=("rails",))))
    models = jobs.job_id(
        jobs.identity(ns.spec(langs=("python", "ruby"), frameworks=("rails_models",)))
    )
    mapped = jobs.job_id(
        jobs.identity(ns.spec(langs=("python", "ruby"), frameworks=("rails_models",), table=table))
    )
    other_root = jobs.job_id(jobs.identity(s.twin(ns).spec()))
    moved = s.Ns(tmp_path / "elsewhere")
    s.write_tree(moved.repo, 1)
    other_repo = jobs.job_id(jobs.identity(moved.spec()))
    ids = [base, changed_file, other_lang, framework, models, mapped, other_repo]
    assert len(set(ids)) == len(ids)
    assert other_root == base  # the graph root is not part of the identity: the repository is


def test_the_identity_tree_is_the_build_source_hash_set(ns: s.Ns) -> None:
    from atlas_aci.indexing import jobs

    s.write_tree(ns.repo, 1)
    (ns.repo / "notes.txt").write_text("not a source file")
    (ns.repo / "node_modules").mkdir()
    (ns.repo / "node_modules" / "skipped.py").write_text("x = 1\n")
    receipt = jobs.submit(ns.spec(), now=100)["receipt"]
    from atlas_aci.storage import leases

    with leases.acquire(ns.ns, receipt["generation_id"]) as lease:
        inputs = json.loads(
            lease.db.execute("SELECT value FROM manifest WHERE key='generation_inputs'").fetchone()[
                0
            ]
        )
    ident = jobs.identity(ns.spec())
    assert inputs["source_hashes"] and set(inputs["source_hashes"]) == {
        f"m{i}.py" for i in range(5)
    }
    assert jobs.tree_digest(inputs["source_hashes"]) == ident["source_tree_sha256"]


def test_a_job_and_a_plain_index_give_the_same_ids(tmp_path: Path) -> None:
    """Condition 14: the job process changes no id (twin namespaces, same repository path)."""
    ns = s.Ns(tmp_path)
    s.write_tree(ns.repo, 1)
    envelope = s.run_json(sargs(ns))
    twin = s.twin(ns)
    done = s.cli(
        ["index", "--repo", str(ns.repo), "--graph-root", str(twin.graphs), "--langs", "python"]
    )
    assert done.returncode == 0, done.stderr[-300:]
    plain = s.current(twin)
    assert plain is not None
    assert plain == (envelope["receipt"]["generation_id"], envelope["receipt"]["snapshot_id"])


def test_an_unreadable_source_is_a_typed_failure_that_creates_nothing(ns: s.Ns) -> None:
    s.write_tree(ns.repo, 1)
    (ns.repo / "linked.py").symlink_to(ns.repo / "m0.py")
    done = s.cli(sargs(ns))
    assert done.returncode == 1 and done.stderr.strip() == "jobs: JOB_FAILED"
    envelope = s.last_json(done.stdout)
    assert envelope["code"] == "JOB_FAILED" and envelope["reason"] == "build_error"
    assert not ns.graphs.exists()  # nothing was created
    assert str(ns.repo) not in done.stdout + done.stderr


def test_any_os_error_is_store_invalid_with_fixed_text(ns: s.Ns) -> None:
    s.write_tree(ns.repo, 1)
    ns.atlas.mkdir(parents=True)
    (ns.atlas / "jobs").write_text("a file where the jobs directory belongs")
    done = s.cli(sargs(ns))
    assert done.returncode == 1 and done.stderr.strip() == "jobs: STORE_INVALID"
    assert s.last_json(done.stdout)["code"] == "STORE_INVALID"
    assert str(ns.tmp) not in done.stdout + done.stderr


# ------------------------------------------------------------------------- submit/resume table
def test_source_changed_fails_the_job_and_publishes_nothing(ns: s.Ns) -> None:
    s.run_job_inprocess(ns, 1, 100)
    pair = s.current(ns)
    s.write_tree(ns.repo, 2)
    job = job_of(ns)
    proc = s.spawn(sargs(ns, now=200), env=s.faults({"predecessor_captured": {"action": "block"}}))
    s.seam_wait(proc, "predecessor_captured")
    (ns.repo / "m0.py").write_text("def edited():\n    return 1\n")  # after the identity
    s.release(proc)
    code, out, _err = s.finish(proc)
    assert code == 1 and s.last_json(out)["reason"] == "source_changed"
    assert job_doc(ns, job)["state"] == "failed"
    assert job_doc(ns, job)["failure_reason"] == "source_changed"
    assert s.current(ns) == pair


def test_the_state_table_interrupted_continues_failed_and_cancelled_retry(ns: s.Ns) -> None:
    s.write_tree(ns.repo, 1)
    job = job_of(ns)
    # interrupted (run.lock free) -> continue as a resume
    kill_run(ns, {"checkpoint": {"action": "kill", "at": 2}})
    assert job_doc(ns, job)["state"] == "running"
    done = s.run_json(sargs(ns, now=200))
    assert done["receipt"]["resumed_from"] == 2 and done["reused"] is False
    assert job_doc(ns, job)["attempt"] == 2
    # completed -> reuse
    assert s.run_json(sargs(ns, now=300))["reused"] is True

    # failed -> a new attempt (attempt += 1), no resume
    other = s.Ns(ns.tmp, graphs="g-failed")
    failed = s.cli(sargs(other), env=s.faults({"staged": {"action": "raise"}}))
    job2 = job_of(other)
    assert failed.returncode == 1 and s.last_json(failed.stdout)["reason"] == "injected_fault"
    assert job_doc(other, job2)["state"] == "failed" and job_doc(other, job2)["attempt"] == 1
    retried = s.run_json(sargs(other, now=200))
    assert retried["receipt"]["resumed_from"] is None
    assert job_doc(other, job2)["attempt"] == 2 and job_doc(other, job2)["state"] == "completed"

    # cancelled -> a new attempt
    third = s.Ns(ns.tmp, graphs="g-cancelled")
    proc = s.spawn(sargs(third), env=s.faults({"staged": {"action": "block"}}))
    s.seam_wait(proc, "staged")
    job3 = job_of(third)
    assert s.run_json([*third.args("jobs", "cancel"), "--job", job3])["outcome"] == "requested"
    s.release(proc)
    assert s.finish(proc)[0] == 4 and job_doc(third, job3)["state"] == "cancelled"
    again = s.run_json(sargs(third, now=200))
    assert job_doc(third, job3)["attempt"] == 2 and again["receipt"]["attempt"] == 2


def test_a_running_job_is_job_running_and_resume_refuses_terminal_states(ns: s.Ns) -> None:
    s.write_tree(ns.repo, 1)
    job = job_of(ns)
    proc = s.spawn(sargs(ns), env=s.faults({"staged": {"action": "block"}}))
    s.seam_wait(proc, "staged")
    same = s.cli(sargs(ns, now=200))  # accepted/running with run.lock held
    assert same.returncode == 75 and s.last_json(same.stdout)["code"] == "JOB_RUNNING"
    resumed = s.cli([*ns.args("jobs", "resume"), "--job", job])
    assert resumed.returncode == 75 and s.last_json(resumed.stdout)["code"] == "JOB_RUNNING"
    status = s.run_json(ns.args("jobs", "status"))
    assert status["jobs"]["running"] == 1 and status["running_jobs"] == [job]
    s.release(proc)
    assert s.finish(proc)[0] == 0
    for expect_code in ("JOB_NOT_RESUMABLE",):  # completed
        done = s.cli([*ns.args("jobs", "resume"), "--job", job])
        assert done.returncode == 1 and s.last_json(done.stdout)["code"] == expect_code
    unknown = s.cli([*ns.args("jobs", "resume"), "--job", "0" * 64])
    assert unknown.returncode == 1 and s.last_json(unknown.stdout)["code"] == "JOB_UNKNOWN"
    bad = s.cli([*ns.args("jobs", "resume"), "--job", "XYZ"])
    assert bad.returncode == 2 and bad.stdout == ""
    assert "--job: must be a 64-character lowercase hex job id" in bad.stderr


def test_the_cancel_outcome_table(ns: s.Ns) -> None:
    s.write_tree(ns.repo, 1)
    job = job_of(ns)
    unknown = s.cli([*ns.args("jobs", "cancel"), "--job", job])
    assert unknown.returncode == 1 and s.last_json(unknown.stdout)["code"] == "JOB_UNKNOWN"
    kill_run(ns, {"checkpoint": {"action": "kill", "at": 1}})  # a dead runner
    dead = s.run_json([*ns.args("jobs", "cancel"), "--job", job])
    assert dead["outcome"] == "cancelled" and job_doc(ns, job)["state"] == "cancelled"
    assert s.run_json([*ns.args("jobs", "cancel"), "--job", job])["outcome"] == "not_running"
    done = s.run_json(sargs(ns, now=200))  # a new attempt completes
    assert done["receipt"]["attempt"] == 2
    assert s.run_json([*ns.args("jobs", "cancel"), "--job", job])["outcome"] == "too_late"


def test_busy_when_every_slot_is_taken_then_success(ns: s.Ns) -> None:
    s.write_tree(ns.repo, 1)
    proc = s.spawn(sargs(ns), env=s.faults({"staged": {"action": "block"}}))
    s.seam_wait(proc, "staged")
    before = s.listing(ns.atlas, exclude=("jobs/slots/0.lock",))
    other = [*ns.args("jobs", "submit"), "--langs", "python,ruby", "--now", "200"]
    busy = s.cli(other)
    assert busy.returncode == 75 and s.last_json(busy.stdout)["code"] == "BUSY"
    assert busy.stderr.strip() == "jobs: BUSY"
    assert s.listing(ns.atlas, exclude=("jobs/slots/0.lock",)) == before  # nothing was written
    s.release(proc)
    assert s.finish(proc)[0] == 0
    assert s.run_json(other)["reused"] is False  # the second job succeeds after the first


def test_recovered_reconciliation_after_a_commit(ns: s.Ns) -> None:
    from atlas_aci.storage import generations as gen

    s.run_job_inprocess(ns, 1, 100)
    s.write_tree(ns.repo, 2)
    job = job_of(ns)
    kill_run(ns, {"post_replace": {"action": "kill"}}, now=200)
    assert job_doc(ns, job)["state"] == "published"
    live = s.current(ns)
    assert live is not None and live[0] == job_doc(ns, job)["staged_generation_id"]
    done = s.run_json([*ns.args("jobs", "resume"), "--job", job, "--now", "300"])
    receipt = done["receipt"]
    assert receipt["outcome"] == "recovered" and receipt["extraction_cache"] is None
    assert receipt["generation_id"] == live[0] and done["receipt_is_current"] is True
    assert gen.read_meta_at(ns.ns, live[0]) is not None  # the capture was repeated


def test_a_rails_table_map_survives_an_interruption(tmp_path: Path) -> None:
    ns = s.Ns(tmp_path)
    shutil.copytree(FIXTURES / "project", ns.repo, dirs_exist_ok=True)
    table = FIXTURES / "table-map.json"
    args = [
        *ns.args("jobs", "submit"),
        "--langs",
        "ruby",
        "--frameworks",
        "rails_models",
        "--rails-table-map",
        str(table),
        "--now",
        "100",
    ]
    done = s.cli(args, env=s.faults({"checkpoint": {"action": "kill", "at": 1}}))
    assert done.returncode == -signal.SIGKILL
    [job] = [p.name for p in jobs_dir(ns).iterdir() if p.name != "slots"]
    stored = (jobs_dir(ns) / job / "rails-table-map.json").read_bytes()
    assert stored == table.read_bytes()
    resumed = s.run_json([*ns.args("jobs", "resume"), "--job", job, "--now", "200"])
    assert resumed["receipt"]["resumed_from"] == 1 and job_doc(ns, job)["state"] == "completed"
    plain = s.run_json(
        [
            *ns.args("jobs", "submit"),
            "--langs",
            "ruby",
            "--frameworks",
            "rails_models",
            "--now",
            "9",
        ]
    )
    assert plain["job_id"] != job  # the table map is part of the identity


def test_faults_are_validated_and_read_only_by_the_cli_entry(ns: s.Ns) -> None:
    s.write_tree(ns.repo, 1)
    for bad in (
        {"nope": {"action": "kill"}},
        {"staged": {"action": "explode"}},
        {"staged": {"action": "kill", "at": 0}},
        {"staged": {"action": "kill", "extra": 1}},
    ):
        done = s.cli(sargs(ns), env=s.faults(bad))
        assert done.returncode == 2 and done.stdout == ""
        assert "invalid fault specification" in done.stderr
        assert not ns.graphs.exists()
    garbage = s.cli(sargs(ns), env={"ATLAS_ACI_JOB_FAULTS": "{not json"})
    assert garbage.returncode == 2 and not ns.graphs.exists()
    source = (s.SRC / "indexing" / "job_runner.py").read_text()
    assert source.count("os.environ") == 1 and source.count("FAULT_ENV") >= 2


# ------------------------------------------------------------------------------ crash matrix
@pytest.fixture(scope="module")
def matrix(tmp_path_factory: pytest.TempPathFactory) -> dict[str, Any]:
    """One repository path shared by every case (content addressing: one expected G2)."""
    base = tmp_path_factory.mktemp("s41matrix")
    expected = s.Ns(base, graphs="expected")
    from atlas_aci.indexing import jobs

    s.write_tree(expected.repo, 2)
    g2 = jobs.submit(expected.spec(), now=1)["receipt"]["generation_id"]
    return {"base": base, "g2": g2}


def verify_every_farm(ns: s.Ns) -> int:
    from atlas_aci.storage import generations as gen

    count = 0
    for meta in gen.list_generations(ns.ns):
        assert meta.valid
        gen.verify_generation(ns.ns, ns.atlas / "generations" / meta.generation_id, meta).close()
        count += 1
    return count


def test_every_publication_seam_kill_recovers(matrix: dict[str, Any]) -> None:
    from atlas_aci.indexing import job_runner, jobs
    from atlas_aci.storage import generations as gen
    from atlas_aci.storage import retention

    assert len(job_runner.JOB_SEAMS) == 12
    for seam in job_runner.JOB_SEAMS:
        ns = s.Ns(matrix["base"], graphs=f"g-{seam}")
        s.write_tree(ns.repo, 1)
        g1 = jobs.submit(ns.spec(), now=100)["receipt"]["generation_id"]
        s.write_tree(ns.repo, 2)
        job = job_of(ns)
        action = {"action": "kill", **({"at": 1} if seam == "checkpoint" else {})}
        done = s.cli(sargs(ns, now=200), env=s.faults({seam: action}))
        assert done.returncode == -signal.SIGKILL, (seam, done.returncode, done.stderr[-300:])
        # (1) the pointer is valid: a live reader lease works and names G1 or G2
        live = s.current(ns)
        assert live is not None and live[0] in {g1, matrix["g2"]}, seam
        # (2) resume converges on the twin's generation and the output verifies
        resumed = jobs.resume(ns.ns, job, now=300)
        assert resumed["receipt"]["generation_id"] == matrix["g2"], seam
        assert resumed["receipt_is_current"] is True and job_doc(ns, job)["state"] == "completed"
        # (3) GC sweeps leftovers; the catalog names manifests that exist and the live one is whole
        receipt = retention.gc(ns.ns, policy=gen.load_policy(ns.ns), now=400)
        names = {p.name for p in ns.atlas.iterdir()}
        assert not [
            n
            for n in names
            if n.startswith((".graph.9.db.tmp.", ".sources-stage.", ".index-attempt."))
        ]
        store = ns.atlas / "source-snapshots"
        catalog = json.loads((store / "catalog.json").read_text())
        manifests = {p.name[9:-5] for p in store.glob("manifest.*.json")}
        assert set(catalog) <= manifests and live_sid_files_present(ns, store), seam
        # (4) every farm verifies
        assert verify_every_farm(ns) >= 1 and receipt["retained"], seam


def live_sid_files_present(ns: s.Ns, store: Path) -> bool:
    current = s.current(ns)
    assert current is not None and current[1] is not None
    manifest = store / f"manifest.{current[1]}.json"
    pack = json.loads(manifest.read_text())["pack"]["sha256"]
    return manifest.is_file() and (store / f"pack.{pack}.bin").is_file()
