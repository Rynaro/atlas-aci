"""ACI-S31: declarative forbidden-import rules checked against one indexed snapshot.

`rules` parses and validates the closed `atlas-architecture-rules.v1` document;
`checker` evaluates it, read-only, over a leased graph and emits the canonical
`atlas-architecture-report.v1`. Operator surface only: nothing here is reachable
from an MCP tool.
"""
