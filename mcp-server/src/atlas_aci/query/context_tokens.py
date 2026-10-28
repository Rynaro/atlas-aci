"""Declared local token accounting over the final negotiated SDK result bytes."""

from __future__ import annotations

import copy
import json

from atlas_aci.enforcement import ToolError
from atlas_aci.protocol.results import result_bytes, sdk_result
from atlas_aci.query.indexed import QueryStopped
from atlas_aci.storage import evidence as ev

DOMAIN = "negotiated_mcp_result_utf8_excluding_jsonrpc"
MAX_COUNT = 2**63 - 1
MAX_CALLS = 16


def declaration(tokenizer):
    if tokenizer is None:
        return {"mode": "estimated", "algorithm": "byte_quarter", "version": "1", "domain": DOMAIN}
    algorithm, version = (
        getattr(tokenizer, "algorithm_id", None),
        getattr(tokenizer, "version", None),
    )
    if any(
        not isinstance(v, str) or not 1 <= len(v.encode("utf-8")) <= 64 or "\0" in v
        for v in (algorithm, version)
    ) or not callable(getattr(tokenizer, "encode", None)):
        raise ValueError("Tokenizer needs a bounded local declaration and encode(str)")
    return {"mode": "exact", "algorithm": algorithm, "version": version, "domain": DOMAIN}


def account(payload, *, tokenizer, structured, work=None):
    """No caller mutation; at most16 calls including final same-byte verification.

    Returned payload is final. Callers must never add tokens/metadata afterward.
    Trusted synchronous operator code is not claimed preemptible.
    """
    try:
        usage = {**declaration(tokenizer), "count": 0}
        body = copy.deepcopy(payload)
        body["data"]["token_usage"] = usage
        # The retained registry saves canonical JSON. Count that exact key order
        # now, so decoding a saved page cannot change an order-sensitive algorithm.
        body = json.loads(ev.canonical(body))
        usage = body["data"]["token_usage"]
        seen = set()
        previous_stable = False
        for _ in range(MAX_CALLS):
            if work is not None:
                work.check()
                work.tokenizer_call()
            raw = result_bytes(sdk_result(body, error=False, structured=structured))
            count = (
                (len(raw) + 3) // 4
                if tokenizer is None
                else len(tokenizer.encode(raw.decode("utf-8")))
            )
            if type(count) is not int or not 0 <= count <= MAX_COUNT:
                raise ValueError("Invalid tokenizer count")
            if work is not None:
                work.check()
            if count == usage["count"]:
                if previous_stable:
                    return body
                previous_stable = True
                continue
            previous_stable = False
            if count in seen:
                raise ValueError("Tokenizer fixed-point cycle")
            seen.add(usage["count"])
            usage["count"] = count
        raise ValueError("Tokenizer fixed point did not converge")
    except QueryStopped as exc:
        raise ToolError(
            "CONTEXT_BUDGET_EXCEEDED", "Context work allowance reached.", "refine_request"
        ) from exc
    except Exception as exc:
        raise ToolError("TOKENIZER_FAILED", "Declared token accounting failed.", "none") from exc
