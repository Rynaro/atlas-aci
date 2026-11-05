"""Cross-repository package links: one consumer, one provider, one locked package (ACI-S30).

An operator surface only: it is built from READ-ONLY leases of two active graph snapshots and
one S28 package snapshot, and written to the operator's output path. It is never part of a graph
and never exposed over MCP.
"""
