"""ACI-S20: one shared propagation allowance across every seed and boundary probe.

Mirrors the `ContextWork` accounting interface that `IndexedReader` and
`navigate` already consume (S18 precedent) but owns the impact counters. Totals
are never reset between seeds: each navigate gets the *remaining* allowance.
"""

from __future__ import annotations

import time

from atlas_aci.query.impact_limits import IMPACT_LIMITS
from atlas_aci.query.indexed import VM_OP_QUANTUM, QueryStopped


class ImpactWork:
    """Counts explicit driver work (statements, VM quanta, edges, nodes, probes)."""

    vm_quantum = VM_OP_QUANTUM

    def __init__(self, **limits: int):
        for name in IMPACT_LIMITS:
            value = limits.get(name, IMPACT_LIMITS[name][0])
            low, high = IMPACT_LIMITS[name][1:]
            if type(value) is not int or not low <= value <= high:
                raise ValueError("Invalid impact work limit: " + name)
        self.effective = {name: limits.get(name, IMPACT_LIMITS[name][0]) for name in IMPACT_LIMITS}
        self.limits = {
            "vm_operations_reserved": self.effective["max_vm_operations"],
            "examined_edges": self.effective["max_examined_edges"],
            "visited_nodes": self.effective["max_visited_nodes"],
            "boundary_probes": self.effective["max_boundary_probes"],
        }
        self.deadline_ns = time.monotonic_ns() + self.effective["deadline_ms"] * 1_000_000
        self.counts = {name: 0 for name in self.limits}
        self.counts.update(sql_queries=0, vm_progress_callbacks=0, boundary_probes_completed=0)
        self.stop_reason: str | None = None

    # ---- stop / deadline ----------------------------------------------------
    def stop(self, reason: str):
        self.stop_reason = self.stop_reason or reason
        raise QueryStopped(reason)

    def check(self):
        if time.monotonic_ns() >= self.deadline_ns:
            self.stop("impact_deadline")

    def spend(self, name: str, amount: int = 1):
        self.check()
        if type(amount) is not int or amount < 0:
            raise ValueError("Invalid work reservation")
        if self.counts[name] + amount > self.limits[name]:
            self.stop("impact_" + name + "_limit")
        self.counts[name] += amount

    # ---- IndexedReader accounting interface ----------------------------------
    def before_sql(self, family: str):
        self.check()
        self.spend("vm_operations_reserved")
        self.counts["sql_queries"] += 1

    def progress(self, quantum: int):
        self.counts["vm_progress_callbacks"] += 1
        self.spend("vm_operations_reserved", quantum)

    def reserve_edge(self):
        self.spend("examined_edges")

    def refund_edge(self):
        if self.counts["examined_edges"] <= 0:
            raise RuntimeError("Unbalanced shared edge reservation")
        self.counts["examined_edges"] -= 1

    # ---- allowances ----------------------------------------------------------
    def remaining(self, name: str) -> int:
        return self.limits[name] - self.counts[name]

    def remaining_ms(self) -> int:
        self.check()
        return min(2000, max(0, (self.deadline_ns - time.monotonic_ns() + 999999) // 1_000_000))

    def add_visited(self, count: int):
        """Visited nodes are charged after each navigate from its own final frontier."""
        self.counts["visited_nodes"] += count
