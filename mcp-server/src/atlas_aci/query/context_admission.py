"""Frozen inspectable diversity and prefix admission of indivisible context atoms."""

from __future__ import annotations

import copy
from collections import defaultdict

from atlas_aci.protocol.resources import MAX_URI_LENGTH
from atlas_aci.protocol.results import result_bytes, sdk_result
from atlas_aci.query.admission import lists
from atlas_aci.query.context_bundles import KINDS, TIERS
from atlas_aci.query.context_tokens import MAX_COUNT, declaration
from atlas_aci.query.leases import MAX_TOKEN_BYTES, canonical_bytes

RANKER_ID = "atlas-context-diversity.v1"


def rank_key(atom):
    rank = atom["rank"]
    return (
        TIERS.index(rank["tier"]),
        rank["seed_rank"],
        rank["distance"],
        rank["source_unavailable"],
        KINDS.index(atom["kind"]),
        atom["id"],
    )


def ranked(atoms):
    unique = {}
    for atom in atoms:
        if atom["id"] not in unique or rank_key(atom) < rank_key(unique[atom["id"]]):
            unique[atom["id"]] = atom
    remaining = sorted(unique.values(), key=rank_key)
    result = []
    exact = [a for a in remaining if a["kind"] == "definition" and a["rank"]["tier"] != "lexical"]
    if exact:
        anchor = exact[0]
        result.append(anchor)
        remaining.remove(anchor)
    queues = defaultdict(lambda: defaultdict(list))
    for atom in remaining:
        queues[atom["rank"]["primary_path"]][canonical_bytes(atom["rank"]["owners"])].append(atom)
    owner_positions = {}
    owners = {}
    for path, groups in queues.items():
        owners[path] = sorted(groups, key=lambda owner: rank_key(groups[owner][0]))
        owner_positions[path] = 0
    while queues:
        paths = sorted(queues, key=lambda path: min(rank_key(q[0]) for q in queues[path].values()))
        for path in paths:
            groups = queues[path]
            order = owners[path]
            start = owner_positions[path]
            for step in range(len(order)):
                index = (start + step) % len(order)
                owner = order[index]
                if owner in groups:
                    result.append(groups[owner].pop(0))
                    owner_positions[path] = (index + 1) % len(order)
                    if not groups[owner]:
                        del groups[owner]
                    break
            if not groups:
                del queues[path]
    return result


def reserved_payload(payload, tokenizer):
    reserved = copy.deepcopy(payload)
    reserved["data"]["token_usage"] = {**declaration(tokenizer), "count": MAX_COUNT}
    if reserved["data"].get("next_cursor") is not None:
        reserved["data"]["next_cursor"] = "x" * MAX_TOKEN_BYTES
    for descriptor in reserved["data"].get("resources", []):
        descriptor["uri"] = "x" * MAX_URI_LENGTH
    return reserved


def prefix(atoms, build, *, budget, structured, element_limit, tokenizer):
    """No later-small-atom skipping and no list/source/proof clipping."""
    selected = []
    reason = None
    for atom in atoms:
        if any(len(items) > element_limit for items in lists(atom)):
            reason = "element_limit"
            break
        proposal = build([*selected, atom], len(selected) + 1 < len(atoms))
        if any(len(items) > element_limit for items in lists(proposal["data"])):
            reason = "element_limit"
            break
        reserved = reserved_payload(proposal, tokenizer)
        if len(result_bytes(sdk_result(reserved, error=False, structured=structured))) > budget:
            reason = "byte_limit"
            break
        selected.append(atom)
    return selected, reason
