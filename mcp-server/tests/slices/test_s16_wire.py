"""ACI-S16: the dependency-free SCIP wire decoder against the official parser and a corpus."""

import hashlib
import random
from pathlib import Path

import pytest
from google.protobuf.message import DecodeError
from test_s16_support import (
    ARTIFACT_SHA256,
    FIXTURE,
    INDEX,
    MANIFEST,
    PROVENANCE,
    SCHEMA_SHA256,
    official,
)

from atlas_aci.providers import scip, scip_wire

PROTO = Path(scip.__file__).with_name("scip.proto")


# ---- minimal independent wire encoder for hand-built corpora ----
def varint(value):
    value &= (1 << 64) - 1
    out = bytearray()
    while True:
        byte = value & 0x7F
        value >>= 7
        out.append(byte | (0x80 if value else 0))
        if not value:
            return bytes(out)


def tag(number, wire):
    return varint(number << 3 | wire)


def delimited(number, payload):
    return tag(number, 2) + varint(len(payload)) + payload


def occurrence(*, rng=(0, 0, 1), symbol="local 1", extra=b""):
    body = b"".join(tag(1, 0) + varint(v) for v in rng) + delimited(2, symbol.encode()) + extra
    return delimited(2, delimited(1, b"a.ts") + delimited(2, body))  # Index.documents[0]


def decode_error(data, **limits):
    with pytest.raises(scip_wire.ScipDecodeError) as excinfo:
        scip_wire.decode_index(
            data, scip_wire.Limits(**limits) if limits else scip_wire.DEFAULT_LIMITS
        )
    return excinfo.value.code


def repeated(field):
    return (
        field.is_repeated if hasattr(field, "is_repeated") else field.label == field.LABEL_REPEATED
    )


def plain(message):
    result = {}
    for descriptor, value in message.ListFields():
        if descriptor.type == descriptor.TYPE_MESSAGE:
            result[descriptor.name] = (
                [plain(item) for item in value] if repeated(descriptor) else plain(value)
            )
        else:
            result[descriptor.name] = list(value) if repeated(descriptor) else value
    return result


def official_plain(data):
    index = official().Index()
    index.ParseFromString(data)
    return plain(index)


# ---- pins ----
def test_proto_is_the_pinned_official_schema():
    assert hashlib.sha256(PROTO.read_bytes()).hexdigest() == SCHEMA_SHA256 == scip.SCHEMA_SHA256
    assert PROVENANCE["schema"]["scip_proto_sha256"] == SCHEMA_SHA256
    assert (PROVENANCE["schema"]["tag"], PROVENANCE["schema"]["commit"]) == (
        scip.SCHEMA_TAG,
        scip.SCHEMA_COMMIT,
    )
    assert hashlib.sha256(INDEX.read_bytes()).hexdigest() == ARTIFACT_SHA256
    # The committed fixture files are exactly the ones provenance.json describes.
    sha = lambda path: hashlib.sha256(path.read_bytes()).hexdigest()  # noqa: E731
    assert PROVENANCE["artifact_sha256"] == ARTIFACT_SHA256
    assert PROVENANCE["manifest_sha256"] == sha(MANIFEST)
    assert PROVENANCE["bindings"]["scip_pb2_sha256"] == sha(FIXTURE / "scip_pb2.py")
    assert PROVENANCE["producer"]["package_lock_sha256"] == sha(
        FIXTURE / "project/package-lock.json"
    )
    assert PROVENANCE["producer"]["version"] == "0.4.0"
    assert PROVENANCE["node_image"].startswith("node@sha256:")
    assert PROVENANCE["sources"] == {
        f"src/{p.name}": sha(p) for p in sorted((FIXTURE / "project/src").glob("*.ts"))
    }


def test_schema_table_matches_official_descriptors():
    pb2 = official()
    types = {
        "string": "TYPE_STRING",
        "int32": "TYPE_INT32",
        "bool": "TYPE_BOOL",
        "enum": "TYPE_ENUM",
        "message": "TYPE_MESSAGE",
    }
    for name, fields in scip_wire.SCHEMAS.items():
        descriptor = pb2.DESCRIPTOR.message_types_by_name[name]
        assert {f.number for f in descriptor.fields} == set(fields), name
        for number, (field, kind, is_repeated, sub) in fields.items():
            official_field = descriptor.fields_by_number[number]
            assert official_field.name == field
            assert official_field.type == getattr(official_field, types[kind])
            assert globals()["repeated"](official_field) == is_repeated
            assert (official_field.message_type.name if sub else None) == sub


def test_real_artifact_decodes_identically():
    data = INDEX.read_bytes()
    assert scip_wire.decode_index(data) == official_plain(data)


# ---- accepted encodings ----
def test_packed_and_unpacked_ranges_and_unknown_fields():
    unpacked = occurrence(rng=(1, 2, 3))
    packed = occurrence(rng=(), extra=delimited(1, varint(1) + varint(2) + varint(3)))
    for data in (unpacked, packed):
        assert scip_wire.decode_index(data)["documents"][0]["occurrences"][0]["range"] == [1, 2, 3]
        assert scip_wire.decode_index(data) == official_plain(data)
    mixed = occurrence(rng=(1,), extra=delimited(1, varint(2) + varint(3)) + tag(1, 0) + varint(4))
    assert scip_wire.decode_index(mixed)["documents"][0]["occurrences"][0]["range"] == [1, 2, 3, 4]
    unknown = occurrence(
        extra=tag(40, 0)
        + varint(7)
        + tag(41, 1)
        + b"12345678"
        + tag(42, 5)
        + b"1234"
        + delimited(43, b"xy")
    )
    assert scip_wire.decode_index(unknown) == official_plain(unknown)


def test_last_wins_singulars_and_defaults_dropped():
    data = occurrence(extra=tag(3, 0) + varint(5) + tag(3, 0) + varint(9))
    assert scip_wire.decode_index(data)["documents"][0]["occurrences"][0]["symbol_roles"] == 9
    zero = occurrence(extra=tag(3, 0) + varint(5) + tag(3, 0) + varint(0))
    assert "symbol_roles" not in scip_wire.decode_index(zero)["documents"][0]["occurrences"][0]
    assert scip_wire.decode_index(zero) == official_plain(zero)


def test_negative_int32_uses_the_ten_byte_form():
    # syntax_kind is an open enum: -1 is representable and must round-trip.
    data = occurrence(extra=tag(5, 0) + varint(-1))
    assert len(varint(-1)) == 10
    assert scip_wire.decode_index(data)["documents"][0]["occurrences"][0]["syntax_kind"] == -1
    assert scip_wire.decode_index(data) == official_plain(data)
    # Negative positions and roles are decoded as such, then rejected as values.
    assert decode_error(occurrence(rng=(0, -1, 1))) == "negative_value"
    assert decode_error(occurrence(extra=tag(3, 0) + varint(-4))) == "negative_value"


# ---- rejected encodings ----
@pytest.mark.parametrize(
    ("name", "data", "code"),
    [
        ("truncated_tag", tag(2, 2)[:0] + b"\x80", "truncated"),
        ("truncated_len", tag(2, 2) + varint(5) + b"ab", "length_beyond_buffer"),
        ("eleven_byte_varint", tag(50, 0) + b"\x80" * 10 + b"\x01", "varint_too_long"),
        ("tenth_byte_overflow", tag(9, 0) + b"\xff" * 9 + b"\x02", "varint_overflow"),
        ("field_zero", b"\x00\x00", "invalid_tag"),
        ("huge_field_number", varint((1 << 29) << 3), "invalid_tag"),
        ("tag_over_32_bits", varint(1 << 33), "invalid_tag"),
        ("start_group", tag(1, 3), "invalid_wire_type"),
        ("end_group", tag(1, 4), "invalid_wire_type"),
        ("wire_type_6", tag(50, 6), "invalid_wire_type"),
        ("wire_type_7", tag(50, 7), "invalid_wire_type"),
        ("unknown_fixed64_short", tag(50, 1) + b"1234", "truncated"),
        ("unknown_fixed32_short", tag(50, 5) + b"12", "truncated"),
        ("wrong_wire_type", tag(2, 0) + varint(1), "wire_type_mismatch"),
        (
            "invalid_utf8",
            delimited(2, delimited(1, b"a\xc3\x28.ts")),
            "invalid_utf8",
        ),
        ("surrogate_utf8", delimited(2, delimited(1, b"\xed\xa0\x80")), "invalid_utf8"),
        ("int32_overflow", occurrence(extra=tag(5, 0) + varint(2**31)), "int32_overflow"),
        (
            "int32_five_byte_negative",
            occurrence(extra=tag(5, 0) + varint(2**32 - 1)),
            "int32_overflow",
        ),
        ("bool_two", delimited(2, delimited(3, delimited(4, tag(2, 0) + varint(2)))), "bool_value"),
        ("range_arity_two", occurrence(rng=(1, 2)), "range_arity"),
        ("range_arity_five", occurrence(rng=(1, 2, 3, 4, 5)), "range_arity"),
        ("range_missing", occurrence(rng=()), "range_arity"),
        ("enclosing_arity", occurrence(extra=delimited(7, varint(1) + varint(2))), "range_arity"),
        ("duplicate_message", delimited(1, b"") + delimited(1, b""), "duplicate_message_field"),
    ],
)
def test_malformed_corpus(name, data, code):
    assert decode_error(data) == code, name
    # Everything here is also something the official parser cannot represent identically.
    try:
        official_plain(data)
    except DecodeError:
        return
    assert code in STRICTER_THAN_OFFICIAL


STRICTER_THAN_OFFICIAL = {
    "invalid_wire_type",
    "wire_type_mismatch",
    "int32_overflow",
    "bool_value",
    "duplicate_message_field",
    "range_arity",
    "negative_value",
    "invalid_tag",
    "varint_overflow",
}


def test_bounds():
    data = occurrence(rng=(0, 0, 1)) * 3
    assert decode_error(data, max_bytes=len(data) - 1) == "artifact_too_large"
    assert decode_error(data, max_documents=2) == "too_many_documents"
    assert decode_error(data, max_occurrences=2) == "too_many_occurrences"
    big = delimited(2, delimited(1, b"x" * 100))
    assert decode_error(big, max_string_bytes=99) == "string_too_long"
    symbols = delimited(2, b"".join(delimited(3, delimited(1, b"s")) for _ in range(4)))
    assert decode_error(symbols, max_symbols=3) == "too_many_symbols"
    many = delimited(2, delimited(2, b"".join(tag(1, 0) + varint(1) for _ in range(5))))
    assert decode_error(many, max_repeated=4) == "too_many_elements"
    nested = b""
    for _ in range(3):
        nested = delimited(3, delimited(7, nested))  # Document.symbols[].signature_documentation
    assert decode_error(delimited(2, nested), max_nesting=4) == "nesting_too_deep"
    assert scip_wire.decode_index(delimited(2, nested))  # within the default bound


def test_bounds_are_enforced_by_the_importer_path(tmp_path):
    artifact = tmp_path / "big.scip"
    artifact.write_bytes(INDEX.read_bytes())
    with pytest.raises(scip_wire.ScipDecodeError, match="artifact_too_large"):
        scip.decode_index(artifact.read_bytes(), scip_wire.Limits(max_bytes=100))
    with pytest.raises(ValueError, match="index_too_large"):
        scip._read_bounded(artifact, 100, "index")


def test_duplicate_and_unsafe_document_paths():
    pb2 = official()
    for path, code in (
        ("../x.ts", "invalid_document_path"),
        ("/abs.ts", "invalid_document_path"),
        ("", "invalid_document_path"),
    ):
        index = pb2.Index()
        index.documents.add().relative_path = path
        with pytest.raises(scip.ScipImportError, match=code):
            scip.decode_index(index.SerializeToString())
    index = pb2.Index()
    for _ in range(2):
        index.documents.add().relative_path = "a.ts"
    with pytest.raises(scip.ScipImportError, match="duplicate_document"):
        scip.decode_index(index.SerializeToString())


# ---- seeded differential against the official parser ----
def random_index(rnd):
    pb2 = official()
    index = pb2.Index()
    if rnd.random() < 0.9:
        index.metadata.tool_info.name = rnd.choice(["scip-typescript", "x", "é"])
        index.metadata.tool_info.version = rnd.choice(["0.4.0", "1"])
        index.metadata.project_root = "file:///p"
    for d in range(rnd.randint(0, 4)):
        doc = index.documents.add()
        doc.relative_path = f"src/f{d}.ts"
        if rnd.random() < 0.3:
            doc.position_encoding = rnd.choice([1, 2, 3])
        for _ in range(rnd.randint(0, 12)):
            occ = doc.occurrences.add()
            line = rnd.randint(0, 50)
            occ.range.extend(
                [line, rnd.randint(0, 99), rnd.randint(100, 200)]
                if rnd.random() < 0.7
                else [line, 1, line + 2, 3]
            )
            occ.symbol = rnd.choice(["", "local 1", "scip-typescript npm p 1 a/B#", "ñ😀"])
            occ.symbol_roles = rnd.choice([0, 1, 2, 8, 0x41])
            if rnd.random() < 0.4:
                occ.enclosing_range.extend([line, 0, line + 5, 0])
            if rnd.random() < 0.2:
                occ.syntax_kind = rnd.randint(0, 40)
            if rnd.random() < 0.2:
                occ.diagnostics.add(severity=rnd.randint(0, 4), message="m").tags.extend([1, 2])
        for _ in range(rnd.randint(0, 3)):
            info = doc.symbols.add(
                symbol="local 9", kind=rnd.choice([0, 7, 17]), display_name=rnd.choice(["", "é"])
            )
            info.relationships.add(symbol="local 3", is_reference=rnd.random() < 0.5)
            if rnd.random() < 0.2:
                info.signature_documentation.relative_path = "inner"
    for _ in range(rnd.randint(0, 2)):
        index.external_symbols.add(symbol="ext", documentation=["d"])
    return index.SerializeToString()


def mutate(rnd, data):
    data = bytearray(data)
    for _ in range(rnd.randint(1, 3)):
        choice = rnd.randrange(5)
        if not data:
            break
        position = rnd.randrange(len(data))
        if choice == 0:
            data[position] = rnd.randrange(256)
        elif choice == 1:
            del data[position : position + rnd.randint(1, 4)]
        elif choice == 2:
            data[position:position] = bytes(rnd.randrange(256) for _ in range(rnd.randint(1, 4)))
        elif choice == 3:
            data = data[: max(position, 1)]
        else:
            data[position] ^= 1 << rnd.randrange(8)
    return bytes(data)


def test_seeded_differential_against_official_parser():
    rnd = random.Random(0x5C1F16)
    accepted_both = rejected_both = stricter = 0
    for round_ in range(700):
        valid = random_index(rnd)
        assert scip_wire.decode_index(valid) == official_plain(valid), round_
        for data in (valid, mutate(rnd, valid), mutate(rnd, INDEX.read_bytes())):
            try:
                theirs = official_plain(data)
            except DecodeError:
                theirs = None
            try:
                mine = scip_wire.decode_index(data)
            except scip_wire.ScipDecodeError as exc:
                if theirs is None:
                    rejected_both += 1
                else:
                    assert exc.code in STRICTER_THAN_OFFICIAL, (exc.code, data)
                    stricter += 1
                continue
            # Soundness: whatever this decoder accepts, the official parser accepts identically.
            assert theirs is not None, data
            assert mine == theirs, data
            accepted_both += 1
    assert accepted_both > 300 and rejected_both > 100
    assert stricter >= 0
