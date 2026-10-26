"""Nonmutating whole-candidate admission against actual dual SDK result bytes."""

from __future__ import annotations

import copy

from atlas_aci.protocol.graph import envelope, validate_retrieval
from atlas_aci.protocol.results import result_bytes, sdk_result
from atlas_aci.query.admission import lists


class RetrievalAdmission:
    def __init__(self, reader, *, budget, element_limit):
        self.reader = reader
        self.budget = budget
        self.element_limit = element_limit
        self.reason = "byte_limit"
        self.result = None

    def project(self, data):
        projected = copy.deepcopy(data)
        projected["ranking_statistics_scope"] = "generation_global"
        projected["sql_count_scope"] = "explicit_driver_statements"
        projected["coverage"]["unavailable_count"] = (
            0 if data["coverage"]["source_policy"] == "available" else None
        )
        for candidate in projected["candidates"]:
            source = candidate["source"]
            if source["state"] == "available":
                source["source_size"] = (
                    source["end_byte"]
                    if source["granularity"] == "file"
                    else candidate["occurrence"]["source_size"]
                )
        return projected

    def encode(self, data):
        result = envelope(
            data,
            operation="retrieve",
            generation_id=self.reader.generation_id,
            snapshot_id=self.reader.snapshot_id,
            evidence_status=self.reader._lease_evidence_status,
        )
        result["bounds"]["element_limit"] = self.element_limit
        result["bounds"]["truncated"] = data["truncated"]
        return result

    def __call__(self, data):
        projected = self.project(data)
        if any(len(items) > self.element_limit for items in lists(projected)):
            self.reason = "element_limit"
            return False
        self.reason = "byte_limit"
        # Reserve the final counters, longest diagnostic/reason and count digits.
        # These are byte placeholders, never emitted or claimed as measured work.
        projected["work"] = {key: 9999999999 for key in projected["work"]}
        projected["returned_count"] = 100
        projected["truncated"] = False
        projected["truncation_reason"] = "candidate_limit"
        projected["diagnostic_reason"] = "filtered_repository"
        return (
            len(result_bytes(sdk_result(self.encode(projected), error=False, structured=True)))
            <= self.budget
        )

    def finish(self, data):
        result = self.encode(self.project(data))
        validate_retrieval(result, self.element_limit)
        if len(result_bytes(sdk_result(result, error=False, structured=True))) > self.budget:
            raise ValueError("Retrieval result admission invariant")
        self.result = result
        return result
