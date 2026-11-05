"""Opt-in framework adapters (ACI-S24, ACI-S25, ACI-S26).

The registry is closed: ``rails`` (explicit routes), ``rails_jobs`` (direct job enqueues and
positional argument paths) and ``rails_models`` (explicit associations, tables and migration
table references). An adapter is enabled per build with
``CodeGraph(repo, frameworks=("rails",))`` or ``atlas-aci index --frameworks rails``; with the
default empty selection no adapter code runs and no adapter record is written.

This module deliberately imports nothing from the code graph: ``atlas_aci.codegraph`` imports
it, so a back-reference would be an import cycle.
"""

from __future__ import annotations

from collections.abc import Iterable

REGISTRY = frozenset({"rails", "rails_jobs", "rails_models"})
# Each adapter reads a single language; it cannot run when that language is disabled.
REQUIRED_LANGUAGE = {"rails": "ruby", "rails_jobs": "ruby", "rails_models": "ruby"}


def normalize(names: Iterable[str] | str | None, languages: Iterable[str]) -> tuple[str, ...]:
    """Strip, drop empty names, de-duplicate and sort; reject unknown or unusable adapters.

    A single string is read as a comma-separated list (the CLI spelling).
    """
    if names is None:
        return ()
    items = names.split(",") if isinstance(names, str) else list(names)
    selected = sorted({item.strip() for item in items if item.strip()})
    enabled_languages = set(languages)
    for name in selected:
        if name not in REGISTRY:
            raise ValueError(f"Unknown framework adapter: {name[:64]}")
        required = REQUIRED_LANGUAGE[name]
        if required not in enabled_languages:
            raise ValueError(f"Framework adapter {name} requires language {required}")
    return tuple(selected)
