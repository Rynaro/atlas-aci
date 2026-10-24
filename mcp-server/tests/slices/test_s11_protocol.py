"""Schema/result boundary anchors independent of status storage assembly."""

import json

import pytest
from jsonschema import Draft202012Validator

from atlas_aci.enforcement import ToolError
from atlas_aci.protocol.graph import GRAPH_INPUT_SCHEMA, GRAPH_OUTPUT_SCHEMA, envelope, request_mode
from atlas_aci.protocol.results import (
    FIXED_ERROR_ENVELOPE_CAP,
    FIXED_MESSAGES,
    bounded_result,
    fixed_result,
    negotiated_version,
    result_bytes,
)


@pytest.mark.parametrize(
    "arguments",
    [
        {"op": "unknown", "schema_version": "atlas.graph.v1"},
        {"op": "status", "schema_version": "future"},
        {"query": "rationale:", "op": "status", "schema_version": "atlas.graph.v1"},
        {"query": 1},
        {"op": "status"},
        {},
        {"op": "status", "schema_version": "atlas.graph.v1", "extra": True},
        {"op": "status", "schema_version": "atlas.graph.v1", "expected_generation_id": "A" * 64},
    ],
)
def test_explicit_invalid_request(arguments):
    with pytest.raises(ToolError):
        request_mode(arguments)


def test_closed_modes_and_schemas():
    Draft202012Validator.check_schema(GRAPH_INPUT_SCHEMA)
    Draft202012Validator.check_schema(GRAPH_OUTPUT_SCHEMA)
    assert request_mode({"query": "rationale:", "unrelated_legacy_option": True}) == "legacy"
    assert request_mode({"op": "status", "schema_version": "atlas.graph.v1"}) == "structured"
    assert negotiated_version("2099-01-01") == "2025-11-25"
    assert negotiated_version("2024-11-05") == "2024-11-05"


@pytest.mark.parametrize("mode", ["legacy", "structured"])
@pytest.mark.parametrize("structured", [False, True])
def test_actual_result_minimum_and_escaping(mode, structured):
    errors = {
        code: len(result_bytes(fixed_result(code, mode=mode, structured=structured)))
        for code in FIXED_MESSAGES
    }
    minimum = errors["RESPONSE_TOO_LARGE"]
    assert minimum == min(errors.values())
    payload = (
        envelope(None, error={"code": "INTERNAL", "message": 'é"\\' * 10000, "retry_hint": "none"})
        if mode == "structured"
        else {"error": "INTERNAL", "message": 'é"\\' * 10000}
    )
    for budget in [0, minimum - 1, minimum, minimum + 1, 2000]:
        result = bounded_result(
            payload, error=True, mode=mode, structured=structured, budget=budget
        )
        decoded = json.loads(result.content[0].text)
        assert result.isError
        assert result.structuredContent == (decoded if structured else None)
        if budget < minimum:
            assert len(result_bytes(result)) <= FIXED_ERROR_ENVELOPE_CAP
            assert (
                decoded["error"]["code"] if mode == "structured" else decoded["error"]
            ) == "INVALID_BUDGET"
        else:
            assert len(result_bytes(result)) <= budget


def test_expected_structured_output_cannot_fall_through_legacy_union():
    result = bounded_result(
        {"definitions": []}, error=False, mode="structured", structured=True, budget=10000
    )
    assert result.isError and result.structuredContent["error"]["code"] == "INVALID_RESULT"
    valid = bounded_result(
        {"definitions": []}, error=False, mode="legacy", structured=True, budget=10000
    )
    assert not valid.isError
    for malformed in ({"value": float("nan")}, {"value": object()}, []):
        result = bounded_result(
            malformed, error=False, mode="legacy", structured=True, budget=10000
        )
        assert result.isError


@pytest.mark.parametrize("status_error", [False, True])
def test_structured_flag_disagreement_fails_closed(status_error):
    from atlas_aci.protocol.graph import no_evidence_data

    payload = (
        envelope(None, error={"code": "INTERNAL", "message": "failed", "retry_hint": "none"})
        if status_error
        else envelope(no_evidence_data())
    )
    result = bounded_result(
        payload, error=not status_error, mode="structured", structured=True, budget=10000
    )
    assert result.isError
    assert result.structuredContent["status"] == "error"
    assert result.structuredContent["error"]["code"] == "INVALID_RESULT"


def test_status_bounds_alias_independent_and_nonmutating():
    from atlas_aci.protocol.graph import STRUCTURED_VALIDATOR, bound_status, no_evidence_data

    data = no_evidence_data()
    values = ["a", "b", "c"]
    data["indexed"]["configuration"] = {"languages": values, "skip_patterns": values}
    payload = envelope(data)
    STRUCTURED_VALIDATOR.validate(payload)
    bounded = bound_status(payload, 1)
    assert values == ["a", "b", "c"]
    for path in (
        "/data/indexed/configuration/languages",
        "/data/indexed/configuration/skip_patterns",
    ):
        assert bounded["bounds"]["lists"][path] == {
            "total_count": 3,
            "returned_count": 1,
            "truncated": True,
        }
    assert bounded["data"]["indexed"]["configuration"] == {
        "languages": ["a"],
        "skip_patterns": ["a"],
    }


def test_mcp_schema_roots_are_explicit_objects():
    # MCP Tool requires type: object even when each oneOf arm already implies it.
    from atlas_aci.protocol.graph import GRAPH_INPUT_SCHEMA, GRAPH_OUTPUT_SCHEMA

    assert GRAPH_INPUT_SCHEMA["type"] == "object"
    assert GRAPH_OUTPUT_SCHEMA["type"] == "object"
