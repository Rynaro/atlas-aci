"""Atomically retain complete context pages and typed same-G1 source memberships."""

from __future__ import annotations

import asyncio
import copy
import json
import time

from atlas_aci.enforcement import ToolError
from atlas_aci.protocol.context import validate_context
from atlas_aci.protocol.resources import next_uri
from atlas_aci.protocol.results import result_bytes, sdk_result
from atlas_aci.query.context import collect, presentation
from atlas_aci.query.context_admission import prefix
from atlas_aci.query.context_bundles import resource_descriptor
from atlas_aci.query.context_limits import normalize
from atlas_aci.query.context_tokens import account
from atlas_aci.query.indexed import QueryStopped
from atlas_aci.query.leases import canonical_bytes


async def issue_context(registry, factory, arguments, *, structured):
    config = registry.config
    query = normalize(arguments, config)
    async with registry.pending() as reservation:
        with reservation.owner as owner:
            reader = owner.enter_context(
                factory.reader_lease(chunk_catalog_bytes=config.max_lease_state_bytes)
            )
            expected = arguments.get("expected_generation_id")
            if expected is not None and expected != reader.generation_id:
                raise ToolError("SNAPSHOT_MISMATCH", "Requested generation does not match.")
            setup_deadline = time.monotonic() + config.lease_setup_ms / 1000
            work = None

            def check():
                if time.monotonic() >= setup_deadline:
                    raise ToolError("SETUP_DEADLINE", "Context setup deadline reached.")
                if work is not None:
                    work.check()

            try:
                atoms, captured, work = await collect(reader, query, config, structured=structured)
                check()
                resources = {}
                for atom in atoms:
                    for source in atom["sources"]:
                        descriptor = resource_descriptor(reader, source)
                        if descriptor is not None:
                            resources[descriptor["id"]] = descriptor
                            if len(resources) > config.max_lease_resources:
                                raise ToolError(
                                    "LEASE_CAPACITY", "Too many context source resources."
                                )
                    check()
                authority = registry.context_authority()
                await asyncio.sleep(0)

                def build(lease):
                    pages = []
                    offset = 0

                    def page(items, more):
                        views = {}
                        for atom in items:
                            for source in atom["sources"]:
                                descriptor = resource_descriptor(reader, source)
                                if descriptor is None:
                                    continue
                                identifier = descriptor["id"]
                                if "uri" not in resources[identifier]:
                                    resources[identifier]["uri"] = next_uri(
                                        identifier,
                                        registry.token(lease, "resource", identifier),
                                        0,
                                        min(config.max_response_bytes, config.max_bytes_per_call),
                                    )
                                views[identifier] = copy.deepcopy(resources[identifier])
                        return presentation(
                            reader,
                            query,
                            items,
                            captured,
                            offset=offset,
                            total=len(atoms),
                            more=more,
                            cursor=registry.token(lease, "cursor", len(pages) + 1)
                            if more
                            else None,
                            resources=list(views.values()),
                            operation="context" if not pages else "continue",
                            element_limit=config.max_bound_field_elements,
                        )

                    while offset < len(atoms) or not pages:
                        check()
                        selected, _reason = prefix(
                            atoms[offset:],
                            page,
                            budget=query["max_bytes"],
                            structured=structured,
                            element_limit=config.max_bound_field_elements,
                            tokenizer=config.context_tokenizer,
                        )
                        if offset < len(atoms) and not selected:
                            raise ToolError(
                                "PAGE_TOO_SMALL", "Context page cannot contain one complete bundle."
                            )
                        payload = page(selected, offset + len(selected) < len(atoms))
                        check()
                        result = account(
                            payload,
                            tokenizer=config.context_tokenizer,
                            structured=structured,
                            work=work,
                        )
                        validate_context(
                            result, result["operation"], config.max_bound_field_elements
                        )
                        if (
                            len(
                                result_bytes(sdk_result(result, error=False, structured=structured))
                            )
                            > query["max_bytes"]
                        ):
                            raise ToolError(
                                "PAGE_TOO_SMALL", "Context page cannot contain bounded metadata."
                            )
                        pages.append(canonical_bytes(result))
                        if (
                            len(pages) > config.max_lease_pages
                            or sum(map(len, pages)) > config.max_lease_state_bytes
                        ):
                            raise ToolError(
                                "LEASE_CAPACITY", "Context pages exceed retained capacity."
                            )
                        offset += len(selected)
                        if offset >= len(atoms):
                            break
                    check()
                    return pages

                lease = await registry.install(
                    owner,
                    reader,
                    captured["query_digest"],
                    build,
                    resources,
                    reservation=reservation,
                    check_setup=check,
                    context_authority=authority,
                    structured=structured,
                )
                return json.loads(lease.pages[0])
            except QueryStopped as exc:
                raise ToolError(
                    "CONTEXT_BUDGET_EXCEEDED", "Shared context work limit reached."
                ) from exc
