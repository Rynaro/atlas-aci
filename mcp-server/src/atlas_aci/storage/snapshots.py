"""Immutable source packs: authenticated bytes, one retained descriptor per lease.

No locks/registry protect readers. Published objects are immutable; open POSIX
file descriptions survive unlink. Directory entries retain current + previous.
"""

from __future__ import annotations

import hashlib
import heapq
import json
import os
import secrets
import stat
import subprocess
from contextlib import suppress
from pathlib import Path

from atlas_aci.storage import evidence as ev

HEADER = b"ATLAS-SOURCE-PACK\x00v1\n"
FORMAT = "atlas-source-snapshot.v2"
V1 = "atlas-source-snapshot.v1"
RANGE_DOMAIN = b"atlas-source-ranges.v2\x00"
CHUNK = 1024 * 1024
AUTH_CHUNK = 64 * 1024


def require(condition, message):
    if not condition:
        raise ValueError("Invalid source snapshot: " + message)


def digest(value):
    return hashlib.sha256(ev.canonical(value).encode()).hexdigest()


def identity(repo: Path):
    env = {**os.environ, "GIT_OPTIONAL_LOCKS": "0"}
    values = []
    for flag in ("--show-toplevel", "--absolute-git-dir", "--git-common-dir"):
        try:
            result = subprocess.run(
                ["git", "-C", str(repo), "rev-parse", flag],
                env=env,
                capture_output=True,
                text=True,
                timeout=3,
            )
        except (OSError, subprocess.TimeoutExpired):
            break
        if result.returncode:
            break
        values.append(str((repo / result.stdout.strip()).resolve()))
    if len(values) == 3:
        return dict(
            kind="git",
            root=str(repo),
            worktree_root=values[0],
            git_dir=values[1],
            common_dir=values[2],
        )
    marker = any((p / ".git").exists() for p in (repo, *repo.parents))
    return {"kind": "git_unavailable" if marker else "non_git", "root": str(repo)}


def _array_digest(rows, domain=b""):
    """Hash exactly canonical JSON array bytes without constructing the array."""
    sha = hashlib.sha256(domain)
    sha.update(b"[")
    first = True
    for row in rows:
        if not first:
            sha.update(b",")
        first = False
        sha.update(ev.canonical(row).encode())
    sha.update(b"]")
    return sha.hexdigest()


def _merged_records(records, observations):
    # Historical bundles already exist in validated observation metadata. Active
    # graph records remain cursor-backed; only ID fingerprints are retained.
    history = sorted(
        (
            r
            for item in observations
            if item["type"] == "history"
            for r in item["records"]
            if r["type"] != "projection"
        ),
        key=lambda r: (r["type"], r["id"]),
    )
    seen = {}
    for item in heapq.merge(records, history, key=lambda r: (r["type"], r["id"])):
        fingerprint = hashlib.sha256(ev.canonical(item).encode()).digest()
        previous = seen.get(item["id"])
        require(previous is None or previous == fingerprint, "conflicting graph ID")
        if previous is None:
            seen[item["id"]] = fingerprint
            yield item


def _range_row(item, occurrence):
    require(occurrence["type"] == "occurrence", "range occurrence")
    row = {
        "id": item["id"],
        "kind": item["type"],
        "path": occurrence["path"],
        "sha256": item["source_sha256"],
        "size": item["source_size"],
        "start": item["start_byte"],
        "end": item["end_byte"],
        "provenance": item["provenance_state"],
    }
    _validate_range(row)
    return row


def _validate_range(row):
    require(
        isinstance(row, dict)
        and set(row) == {"id", "kind", "path", "sha256", "size", "start", "end", "provenance"},
        "range shape",
    )
    require(
        ev.hash_value(row["id"])
        and row["kind"] in {"occurrence", "evidence"}
        and ev.relative_path(row["path"]),
        "range identity",
    )
    if row["provenance"] == "source":
        require(
            ev.hash_value(row["sha256"])
            and all(type(row[k]) is int and row[k] >= 0 for k in ("size", "start", "end"))
            and row["start"] <= row["end"] <= row["size"],
            "range bounds",
        )
    else:
        require(
            row["provenance"] == "legacy_unavailable"
            and all(row[k] is None for k in ("sha256", "size", "start", "end")),
            "legacy range",
        )


def _range_commitment(rows, current):
    counts = {"total": 0, "occurrence": 0, "evidence": 0, "legacy": 0}
    bounds = {
        (r["path"], r["sha256"]): {
            "path": r["path"],
            "sha256": r["sha256"],
            "size": None,
            "range_count": 0,
            "min_start": 0,
            "max_end": 0,
        }
        for r in current
    }
    previous = None

    def checked():
        nonlocal previous
        for row in rows:
            _validate_range(row)
            key = (row["id"], row["kind"])
            require(previous is None or previous < key, "range order")
            previous = key
            counts["total"] += 1
            counts[row["kind"]] += 1
            if row["provenance"] == "legacy_unavailable":
                counts["legacy"] += 1
            else:
                pair = (row["path"], row["sha256"])
                group = bounds.setdefault(
                    pair,
                    {
                        "path": pair[0],
                        "sha256": pair[1],
                        "size": None,
                        "range_count": 0,
                        "min_start": 0,
                        "max_end": 0,
                    },
                )
                require(
                    group["size"] is None or group["size"] == row["size"],
                    "source size contradiction",
                )
                group["size"] = row["size"]
                group["min_start"] = (
                    min(group["min_start"], row["start"]) if group["range_count"] else row["start"]
                )
                group["max_end"] = max(group["max_end"], row["end"])
                group["range_count"] += 1
            yield row

    checksum = _array_digest(checked(), RANGE_DOMAIN)
    return (
        {"version": 1, "algorithm": "sha256-canonical-json-array-v1", **counts, "sha256": checksum},
        [bounds[key] for key in sorted(bounds)],
    )


def closure(records, observations, *, range_records=None, occurrence=None):
    """Own-connection proof, streaming active records and retaining history IDs."""
    if not callable(records):
        # Small independent callers may supply a replayable sequence. Production
        # passes cursor factories; never silently consume an iterator into a list.
        require(isinstance(records, (list, tuple)), "replayable graph records")
        sequence = records

        def records():
            return iter(sorted(sequence, key=lambda r: (r["type"], r["id"])))

    graph_digest = _array_digest(_merged_records(records(), observations))
    historical = {
        r["id"]: r
        for h in observations
        if h["type"] == "history"
        for r in h["records"]
        if r["type"] != "projection"
    }
    if occurrence is None:
        active = {r["id"]: r for r in records() if r["type"] == "occurrence"}

        def occurrence(identifier):
            return active[identifier]

    if range_records is None:

        def range_records():
            return iter(
                sorted(
                    (r for r in records() if r["type"] in {"occurrence", "evidence"}),
                    key=lambda r: (r["id"], r["type"]),
                )
            )

    old_ranges = sorted(
        (r for r in historical.values() if r["type"] in {"occurrence", "evidence"}),
        key=lambda r: (r["id"], r["type"]),
    )

    def ranges():
        previous = None
        previous_row = None
        for item in heapq.merge(range_records(), old_ranges, key=lambda r: (r["id"], r["type"])):
            linked = item if item["type"] == "occurrence" else historical.get(item["occurrence_id"])
            if linked is None:
                linked = occurrence(item["occurrence_id"])
            row = _range_row(item, linked)
            key = (row["id"], row["kind"])
            if key == previous:
                require(row == previous_row, "conflicting source range")
                continue
            previous, previous_row = key, row
            yield row

    current = sorted(
        (
            {"path": r["path"], "sha256": r["observed_source_sha256"]}
            for r in observations
            if r["type"] == "observation"
        ),
        key=lambda r: r["path"],
    )
    proof, bounds = _range_commitment(ranges(), current)
    return {
        "current": current,
        "range_proof": proof,
        "source_bounds": bounds,
        "graph_digest": graph_digest,
        "observation_digest": digest(observations),
    }


def directory(path: Path, create=False):
    """Open every canonical component without following a symlink; caller owns fd."""
    require(path.is_absolute(), "absolute storage directory")
    fd = os.open("/", os.O_RDONLY | os.O_DIRECTORY)
    try:
        for component in path.parts[1:]:
            if create:
                with suppress(FileExistsError):
                    os.mkdir(component, mode=0o700, dir_fd=fd)
            next_fd = os.open(component, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
            os.close(fd)
            fd = next_fd
        return fd
    except BaseException:
        os.close(fd)
        raise


def open_file(fd, name):
    result = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=fd)
    if not stat.S_ISREG(os.fstat(result).st_mode):
        os.close(result)
        raise ValueError("Invalid source snapshot: nonregular file")
    return result


def read_file(fd, name):
    child = open_file(fd, name)
    try:
        with os.fdopen(child, "rb", closefd=False) as stream:
            return stream.read()
    finally:
        os.close(child)


def safe_source(repo, path):
    require(ev.relative_path(path), "source relative path")
    parent = directory(repo)
    try:
        components = path.split("/")
        for component in components[:-1]:
            child = os.open(component, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent)
            os.close(parent)
            parent = child
        child = open_file(parent, components[-1])
        with os.fdopen(child, "rb") as stream:
            metadata = os.fstat(stream.fileno())
            return stream.read(), metadata
    finally:
        os.close(parent)


def _validate_v1(value):
    require(
        isinstance(value, dict)
        and set(value)
        == {
            "format",
            "context",
            "current",
            "ranges",
            "graph_digest",
            "observation_digest",
            "bindings",
            "pack",
        },
        "manifest shape",
    )
    require(value["format"] == V1, "manifest version")
    context = value["context"]
    require(
        isinstance(context, dict)
        and set(context) == {"repository", "identity", "schema", "configuration", "producer"},
        "context",
    )
    require(
        isinstance(context["repository"], str) and Path(context["repository"]).is_absolute(),
        "repository",
    )
    schema = context["schema"]
    require(
        isinstance(schema, dict)
        and set(schema) == {"epoch", "ddl_hash"}
        and type(schema["epoch"]) is int
        and schema["epoch"] in {7, 8}
        and ev.hash_value(schema["ddl_hash"]),
        "schema",
    )
    config = context["configuration"]
    require(
        isinstance(config, dict)
        and set(config) == {"languages", "skip_patterns"}
        and all(
            isinstance(value, list) and all(isinstance(v, str) and bool(v) for v in value)
            for value in config.values()
        )
        and config["languages"] == sorted(set(config["languages"])),
        "configuration",
    )
    ev.validate_record(ev.producer(context["producer"]))
    ident = context["identity"]
    require(
        isinstance(ident, dict) and ident.get("kind") in {"git", "git_unavailable", "non_git"},
        "identity",
    )
    require(
        set(ident)
        == (
            {"kind", "root", "worktree_root", "git_dir", "common_dir"}
            if ident["kind"] == "git"
            else {"kind", "root"}
        ),
        "identity shape",
    )
    require(
        ident["root"] == context["repository"]
        and all(isinstance(v, str) and v for v in ident.values()),
        "identity root",
    )
    require(
        ev.hash_value(value["graph_digest"]) and ev.hash_value(value["observation_digest"]),
        "graph digests",
    )
    require(isinstance(value["current"], list), "current vector")
    current = []
    for row in value["current"]:
        require(
            isinstance(row, dict)
            and set(row) == {"path", "sha256"}
            and ev.relative_path(row["path"])
            and ev.hash_value(row["sha256"]),
            "current binding",
        )
        current.append(row["path"])
    require(current == sorted(set(current)), "current order")
    require(isinstance(value["bindings"], list), "bindings")
    pairs = []
    for row in value["bindings"]:
        require(
            isinstance(row, dict)
            and set(row) == {"path", "sha256", "available"}
            and ev.relative_path(row["path"])
            and ev.hash_value(row["sha256"])
            and type(row["available"]) is bool,
            "binding",
        )
        pairs.append((row["path"], row["sha256"]))
    require(pairs == sorted(set(pairs)), "binding order")
    require(isinstance(value["ranges"], list), "ranges")
    range_ids = []
    authorized = set(pairs)
    for row in value["ranges"]:
        require(
            isinstance(row, dict)
            and set(row) == {"id", "kind", "path", "sha256", "size", "start", "end", "provenance"},
            "range shape",
        )
        require(
            ev.hash_value(row["id"])
            and row["kind"] in {"occurrence", "evidence"}
            and ev.relative_path(row["path"]),
            "range identity",
        )
        if row["provenance"] == "source":
            require(
                ev.hash_value(row["sha256"])
                and all(type(row[k]) is int and row[k] >= 0 for k in ("size", "start", "end"))
                and row["start"] <= row["end"] <= row["size"],
                "range bounds",
            )
            require((row["path"], row["sha256"]) in authorized, "range binding")
        else:
            require(
                row["provenance"] == "legacy_unavailable"
                and all(row[k] is None for k in ("sha256", "size", "start", "end")),
                "legacy range",
            )
        range_ids.append((row["id"], row["kind"]))
    require(range_ids == sorted(set(range_ids)), "range order")
    needed = {(r["path"], r["sha256"]) for r in value["current"]} | {
        (r["path"], r["sha256"]) for r in value["ranges"] if r["provenance"] == "source"
    }
    require(needed == authorized, "complete binding closure")
    require(
        all(
            row["available"]
            for row in value["bindings"]
            if (row["path"], row["sha256"]) in {(r["path"], r["sha256"]) for r in value["current"]}
        ),
        "current bytes missing",
    )
    pack = value["pack"]
    require(
        isinstance(pack, dict)
        and set(pack) == {"sha256", "size", "index"}
        and ev.hash_value(pack["sha256"])
        and type(pack["size"]) is int
        and pack["size"] >= len(HEADER)
        and isinstance(pack["index"], list),
        "pack shape",
    )
    hashes = []
    end = len(HEADER)
    for row in pack["index"]:
        require(
            isinstance(row, dict)
            and set(row) == {"sha256", "offset", "length"}
            and ev.hash_value(row["sha256"]),
            "pack index",
        )
        require(
            type(row["offset"]) is int
            and type(row["length"]) is int
            and row["offset"] == end
            and row["length"] >= 0
            and row["length"] <= pack["size"] - end,
            "pack range",
        )
        hashes.append(row["sha256"])
        end += row["length"]
    require(hashes == sorted(set(hashes)) and end == pack["size"], "pack coverage")
    require(
        set(hashes) == {r["sha256"] for r in value["bindings"] if r["available"]},
        "pack source closure",
    )
    lengths = {r["sha256"]: r["length"] for r in pack["index"]}
    require(
        all(
            r["size"] == lengths[r["sha256"]]
            for r in value["ranges"]
            if r["provenance"] == "source" and r["sha256"] in lengths
        ),
        "source size",
    )
    return value


def validate_manifest(value):
    if isinstance(value, dict) and value.get("format") == V1:
        return _validate_v1(value)
    require(
        isinstance(value, dict)
        and set(value)
        == {
            "format",
            "context",
            "current",
            "range_proof",
            "graph_digest",
            "observation_digest",
            "bindings",
            "pack",
        }
        and value["format"] == FORMAT,
        "manifest shape/version",
    )
    proof = value["range_proof"]
    require(
        isinstance(proof, dict)
        and set(proof)
        == {"version", "algorithm", "total", "occurrence", "evidence", "legacy", "sha256"}
        and type(proof["version"]) is int
        and proof["version"] == 1
        and proof["algorithm"] == "sha256-canonical-json-array-v1"
        and all(
            type(proof[k]) is int and proof[k] >= 0
            for k in ("total", "occurrence", "evidence", "legacy")
        )
        and proof["total"] == proof["occurrence"] + proof["evidence"]
        and proof["legacy"] <= proof["total"]
        and ev.hash_value(proof["sha256"]),
        "range proof",
    )
    require(isinstance(value["bindings"], list), "bindings")
    pairs = []
    for row in value["bindings"]:
        require(
            isinstance(row, dict)
            and set(row)
            == {"path", "sha256", "available", "size", "range_count", "min_start", "max_end"}
            and ev.relative_path(row["path"])
            and ev.hash_value(row["sha256"])
            and type(row["available"]) is bool
            and all(
                type(row[k]) is int and row[k] >= 0
                for k in ("size", "range_count", "min_start", "max_end")
            )
            and row["min_start"] <= row["max_end"] <= row["size"],
            "source bounds",
        )
        require(
            row["range_count"] > 0 or (row["min_start"], row["max_end"]) == (0, 0),
            "empty source bounds",
        )
        pairs.append((row["path"], row["sha256"]))
    require(pairs == sorted(set(pairs)), "binding order")
    require(
        sum(r["range_count"] for r in value["bindings"]) == proof["total"] - proof["legacy"],
        "range count closure",
    )
    current = {(r["path"], r["sha256"]) for r in value["current"]}
    require(
        all(r["range_count"] or (r["path"], r["sha256"]) in current for r in value["bindings"]),
        "extraneous source binding",
    )
    # Reuse original context/current/pack/path/availability validation, retaining
    # ALL bindings as explicit synthetic current entries for pack closure only.
    adapter = {k: value[k] for k in ("context", "graph_digest", "observation_digest", "pack")}
    adapter.update(
        format=V1,
        current=value["current"],
        ranges=[],
        bindings=[{k: r[k] for k in ("path", "sha256", "available")} for r in value["bindings"]],
    )
    # v1's exact authorized vector normally derives historical pairs from ranges.
    # Supply zero-length validation rows solely for its shared closure validation.
    adapter["ranges"] = [
        {
            "id": hashlib.sha256(ev.canonical([r["path"], r["sha256"]]).encode()).hexdigest(),
            "kind": "occurrence",
            "path": r["path"],
            "sha256": r["sha256"],
            "size": r["size"],
            "start": 0,
            "end": 0,
            "provenance": "source",
        }
        for r in value["bindings"]
    ]
    adapter["ranges"].sort(key=lambda r: (r["id"], r["kind"]))
    _validate_v1(adapter)
    lengths = {r["sha256"]: r["length"] for r in value["pack"]["index"]}
    require(
        all(not r["available"] or r["size"] == lengths[r["sha256"]] for r in value["bindings"]),
        "source size",
    )
    return value


def _compare_binding(manifest, expected):
    for key in ("context", "current", "graph_digest", "observation_digest"):
        if key in expected:
            require(manifest[key] == expected[key], "graph source closure")
    if manifest["format"] == V1:
        proof, bounds = _range_commitment(iter(manifest["ranges"]), manifest["current"])
    else:
        proof, bounds = manifest["range_proof"], manifest["bindings"]
    require(proof == expected["range_proof"], "graph range commitment")
    actual = {(r["path"], r["sha256"]): r for r in bounds}
    require(
        set(actual) == {(r["path"], r["sha256"]) for r in expected["source_bounds"]},
        "graph source catalog",
    )
    for row in expected["source_bounds"]:
        other = actual[(row["path"], row["sha256"])]
        for key in ("range_count", "min_start", "max_end"):
            require(other[key] == row[key], "graph source bounds")
        if row["size"] is not None:
            require(other["size"] == row["size"], "graph source size")
        # A current failed/disabled file can have zero graph ranges. Its exact
        # consumed hash is still independently bound by the observation vector;
        # the authenticated blob supplies its length, not a fabricated DB fact.


class SourceLease:
    def __init__(self, fd, manifest, snapshot_id):
        self.fd = fd
        self.chunk_catalog = None
        self.chunk_catalog_bytes = 0
        self.manifest = manifest
        self.snapshot_id = snapshot_id
        self.index = {r["sha256"]: r for r in manifest["pack"]["index"]}
        self.current = {r["path"]: r["sha256"] for r in manifest["current"]}
        self.bindings = {(r["path"], r["sha256"]): r["available"] for r in manifest["bindings"]}

    def close(self):
        if self.fd is not None:
            os.close(self.fd)
            self.fd = None

    def read(self, path, source_hash=None):
        if self.fd is None:
            raise RuntimeError("Source lease is closed")
        require(ev.relative_path(path), "source relative path")
        source_hash = source_hash if source_hash is not None else self.current.get(path)
        require(ev.hash_value(source_hash), "source hash")
        require(
            self.bindings.get((path, source_hash)) is True, "source unavailable or unauthorized"
        )
        row = self.index[source_hash]
        data = bytearray()
        offset = row["offset"]
        remaining = row["length"]
        while remaining:
            block = os.pread(self.fd, min(CHUNK, remaining), offset)
            require(bool(block), "truncated source")
            data.extend(block)
            offset += len(block)
            remaining -= len(block)
        require(hashlib.sha256(data).hexdigest() == source_hash, "source checksum")
        return bytes(data)

    def read_span(self, path, source_hash, start, end, offset, max_bytes):
        """Read only verified aligned buffers; caller separately authorizes exact evidence span."""
        require(self.fd is not None, "closed source lease")
        require(self.chunk_catalog is not None, "source chunk catalog unavailable")
        require(ev.relative_path(path) and ev.hash_value(source_hash), "source identity")
        require(self.bindings.get((path, source_hash)) is True, "source membership")
        require(all(type(v) is int for v in (start, end, offset, max_bytes)), "span integers")
        row = self.index[source_hash]
        require(
            0 <= start <= end <= row["length"] and 0 <= offset <= end - start and max_bytes >= 0,
            "span bounds",
        )
        begin = start + offset
        finish = min(end, begin + max_bytes)
        result = bytearray()
        for position in range(begin // AUTH_CHUNK, (finish + AUTH_CHUNK - 1) // AUTH_CHUNK):
            if begin == finish:
                break
            chunk_start = position * AUTH_CHUNK
            count = min(AUTH_CHUNK, row["length"] - chunk_start)
            block = os.pread(self.fd, count, row["offset"] + chunk_start)
            require(
                len(block) == count
                and hashlib.sha256(block).hexdigest() == self.chunk_catalog[source_hash][position],
                "source chunk checksum",
            )
            # Return bytes from exactly this authenticated buffer, never a reread.
            result.extend(block[max(begin - chunk_start, 0) : min(finish - chunk_start, count)])
        return bytes(result)

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()


def acquire(atlas_dir, snapshot_id, *, repository, expected=None, chunk_catalog_bytes=None):
    require(ev.hash_value(snapshot_id), "snapshot identifier")
    directory_fd = directory(atlas_dir / "source-snapshots")
    pack_fd = None
    try:
        raw = read_file(directory_fd, "manifest." + snapshot_id + ".json")
        require(hashlib.sha256(raw).hexdigest() == snapshot_id, "manifest checksum")
        manifest = validate_manifest(json.loads(raw))
        require(raw == ev.canonical(manifest).encode(), "canonical manifest")
        require(manifest["context"]["repository"] == str(repository), "foreign repository")
        if expected is not None:
            _compare_binding(manifest, expected)
        pack = manifest["pack"]
        catalog = None
        catalog_size = 0
        if chunk_catalog_bytes is not None:
            require(
                type(chunk_catalog_bytes) is int and chunk_catalog_bytes > 0,
                "source chunk catalog allowance",
            )
            # Exact canonical size depends only on fixed hash widths and blob lengths.
            catalog_size = (
                2
                + sum(
                    (
                        69
                        if r["length"] == 0
                        else 68 + ((r["length"] + AUTH_CHUNK - 1) // AUTH_CHUNK) * 67
                    )
                    for r in pack["index"]
                )
                + max(0, len(pack["index"]) - 1)
            )
            require(catalog_size <= chunk_catalog_bytes, "source chunk catalog capacity")
            catalog = {}
        pack_fd = open_file(directory_fd, "pack." + pack["sha256"] + ".bin")
        require(os.fstat(pack_fd).st_size == pack["size"], "pack size")
        require(os.pread(pack_fd, len(HEADER), 0) == HEADER, "pack header")
        sha = hashlib.sha256()
        position = 0
        while position < pack["size"]:
            block = os.pread(pack_fd, min(CHUNK, pack["size"] - position), position)
            require(bool(block), "truncated pack")
            sha.update(block)
            position += len(block)
        require(sha.hexdigest() == pack["sha256"], "pack checksum")
        lease = SourceLease(pack_fd, manifest, snapshot_id)
        for row in manifest["pack"]["index"]:
            # Authenticate ranges individually without retaining all bytes.
            index = lease.index[row["sha256"]]
            offset = index["offset"]
            remaining = index["length"]
            blob = hashlib.sha256()
            hashes = []
            while remaining:
                step = min(AUTH_CHUNK if catalog is not None else CHUNK, remaining)
                data = os.pread(pack_fd, step, offset)
                require(bool(data) and (catalog is None or len(data) == step), "blob truncation")
                blob.update(data)
                if catalog is not None:
                    hashes.append(hashlib.sha256(data).hexdigest())
                offset += len(data)
                remaining -= len(data)
            require(blob.hexdigest() == row["sha256"], "blob checksum")
            if catalog is not None:
                catalog[row["sha256"]] = hashes
        if catalog is not None:
            actual_size = len(ev.canonical(catalog).encode())
            require(
                actual_size <= catalog_size <= chunk_catalog_bytes, "source chunk catalog capacity"
            )
            lease.chunk_catalog = catalog
            lease.chunk_catalog_bytes = actual_size
        pack_fd = None
        return lease
    finally:
        if pack_fd is not None:
            os.close(pack_fd)
        os.close(directory_fd)


class Builder:
    def __init__(self, atlas_dir):
        self.atlas_dir = atlas_dir
        self.root = directory(atlas_dir, create=True)
        self.name = ".sources-stage." + secrets.token_hex(12)
        created = False
        try:
            os.mkdir(self.name, mode=0o700, dir_fd=self.root)
            created = True
            self.fd = os.open(
                self.name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=self.root
            )
        except BaseException:
            if created:
                os.rmdir(self.name, dir_fd=self.root)
            os.close(self.root)
            raise
        self.blobs = {}
        self.installed = []
        self.published = False

    def add(self, source):
        sha = hashlib.sha256(source).hexdigest()
        if sha not in self.blobs:
            fd = os.open(
                sha, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=self.fd
            )
            with os.fdopen(fd, "wb") as stream:
                stream.write(source)
                stream.flush()
                os.fsync(stream.fileno())
            self.blobs[sha] = len(source)
        return sha

    def _install(self, store_fd, name, data_fd):
        try:
            existing = open_file(store_fd, name)
        except FileNotFoundError:
            os.link(name, name, src_dir_fd=self.fd, dst_dir_fd=store_fd, follow_symlinks=False)
            self.installed.append(name)
            return
        try:
            require(os.fstat(existing).st_size == os.fstat(data_fd).st_size, "existing object size")
            position = 0
            while position < os.fstat(data_fd).st_size:
                a = os.pread(existing, CHUNK, position)
                b = os.pread(data_fd, CHUNK, position)
                require(a == b, "existing object content")
                position += len(b)
        finally:
            os.close(existing)

    def publish(self, context, binding, previous=None):
        pairs = {(r["path"], r["sha256"]): r for r in binding["source_bounds"]}
        current = {(r["path"], r["sha256"]) for r in binding["current"]}
        available = []
        for path, sha in sorted(pairs):
            if (
                sha not in self.blobs
                and previous is not None
                and previous.bindings.get((path, sha)) is True
            ):
                self.add(previous.read(path, sha))
            require(
                (path, sha) not in current or sha in self.blobs, "current source staging missing"
            )
            # Failed/disabled files can have no typed range. Their consumed hash
            # authenticates the staged blob; its actual length supplies size.
            row = dict(pairs[(path, sha)])
            size = self.blobs.get(sha, row["size"])
            require(type(size) is int and size >= 0, "declared source size")
            require(row["size"] is None or row["size"] == size, "staged source size")
            row.update(size=size, available=sha in self.blobs)
            available.append(row)
        used = sorted({r["sha256"] for r in available if r["available"]})
        fd = os.open(
            "pack-stage", os.O_RDWR | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=self.fd
        )
        try:
            with os.fdopen(fd, "wb", closefd=False) as stream:
                stream.write(HEADER)
                index = []
                offset = len(HEADER)
                for sha in used:
                    index.append({"sha256": sha, "offset": offset, "length": self.blobs[sha]})
                    child = open_file(self.fd, sha)
                    check = hashlib.sha256()
                    try:
                        with os.fdopen(child, "rb", closefd=False) as source:
                            while data := source.read(CHUNK):
                                stream.write(data)
                                check.update(data)
                    finally:
                        os.close(child)
                    require(check.hexdigest() == sha, "staged blob checksum")
                    offset += self.blobs[sha]
                stream.flush()
                os.fsync(fd)
            sha = hashlib.sha256()
            position = 0
            while data := os.pread(fd, CHUNK, position):
                sha.update(data)
                position += len(data)
            pack = {"sha256": sha.hexdigest(), "size": position, "index": index}
            manifest = {
                "format": FORMAT,
                "context": context,
                **{k: v for k, v in binding.items() if k != "source_bounds"},
                "bindings": available,
                "pack": pack,
            }
            validate_manifest(manifest)
            raw = ev.canonical(manifest).encode()
            identifier = hashlib.sha256(raw).hexdigest()
            pack_name = "pack." + pack["sha256"] + ".bin"
            os.rename("pack-stage", pack_name, src_dir_fd=self.fd, dst_dir_fd=self.fd)
            store = directory(self.atlas_dir / "source-snapshots", create=True)
            try:
                self._install(store, pack_name, fd)
                name = "manifest." + identifier + ".json"
                mfd = os.open(
                    name, os.O_RDWR | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=self.fd
                )
                try:
                    os.write(mfd, raw)
                    os.fsync(mfd)
                    self._install(store, name, mfd)
                finally:
                    os.close(mfd)
                os.fsync(store)
            finally:
                os.close(store)
            # Validate complete installed objects before the graph can publish.
            with acquire(
                self.atlas_dir,
                identifier,
                repository=Path(context["repository"]),
                expected={"context": context, **binding},
            ):
                pass
            return identifier
        finally:
            os.close(fd)

    def close(self):
        if not self.published and self.installed:
            store = directory(self.atlas_dir / "source-snapshots")
            try:
                for name in reversed(self.installed):
                    os.unlink(name, dir_fd=store)
            finally:
                os.close(store)
        for name in os.listdir(self.fd):
            os.unlink(name, dir_fd=self.fd)
        os.close(self.fd)
        os.rmdir(self.name, dir_fd=self.root)
        os.close(self.root)


def retain(atlas_dir, identifier):
    """Writer lock only, after graph replacement; never used by a reader."""
    fd = directory(atlas_dir / "source-snapshots", create=True)
    try:
        try:
            old = json.loads(read_file(fd, "catalog.json"))
        except FileNotFoundError:
            old = []
        require(isinstance(old, list) and all(ev.hash_value(v) for v in old), "catalog")
        retained = ([identifier] if identifier else []) + [v for v in old if v != identifier]
        retained = retained[:2] if identifier else retained[:1]
        packs = set()
        for value in retained:
            raw = read_file(fd, "manifest." + value + ".json")
            require(hashlib.sha256(raw).hexdigest() == value, "retained manifest")
            packs.add(validate_manifest(json.loads(raw))["pack"]["sha256"])
        temp = ".catalog." + secrets.token_hex(12)
        child = os.open(
            temp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=fd
        )
        with os.fdopen(child, "wb") as stream:
            stream.write(ev.canonical(retained).encode())
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp, "catalog.json", src_dir_fd=fd, dst_dir_fd=fd)
        os.fsync(fd)
        for name in os.listdir(fd):
            if (
                name.startswith("manifest.")
                and name.endswith(".json")
                and name[9:-5] not in retained
            ):
                os.unlink(name, dir_fd=fd)
            if name.startswith("pack.") and name.endswith(".bin") and name[5:-4] not in packs:
                os.unlink(name, dir_fd=fd)
    finally:
        os.close(fd)
