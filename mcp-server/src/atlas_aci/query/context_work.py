"""One post-acquisition absolute deadline and cumulative context work allowance."""

from __future__ import annotations

import time

from atlas_aci.query.indexed import QueryStopped


class ContextWork:
    """Counts explicit driver work, not hidden FTS C-work or process memory.

    Each statement prepays one VM quantum; every observed callback is charged.
    Source-byte charges cover the authenticated blob read, not only inline output.
    """

    vm_quantum = 1

    def __init__(
        self,
        *,
        max_vm_operations=1_000_000,
        max_examined_edges=256,
        deadline_ms=2000,
        max_proof_queries=2048,
        max_source_reads=128,
        max_source_bytes=8 * 1024 * 1024,
        max_tokenizer_calls=16 * 129,
    ):
        values = {
            "vm_operations_reserved": max_vm_operations,
            "examined_edges": max_examined_edges,
            "proof_queries": max_proof_queries,
            "source_reads": max_source_reads,
            "source_bytes": max_source_bytes,
            "tokenizer_calls": max_tokenizer_calls,
        }
        if any(type(v) is not int or v < 0 for v in values.values()):
            raise ValueError("Context work limits must be finite nonnegative integers")
        if type(deadline_ms) is not int or not 0 <= deadline_ms <= 2000:
            raise ValueError("Invalid context deadline")
        self.limits = values
        self.deadline_ns = time.monotonic_ns() + deadline_ms * 1_000_000
        self.counts = {name: 0 for name in values}
        self.counts.update(sql_queries=0, vm_progress_callbacks=0)
        self.stop_reason = None

    def stop(self, reason):
        self.stop_reason = reason
        raise QueryStopped(reason)

    def check(self):
        if time.monotonic_ns() >= self.deadline_ns:
            self.stop("context_deadline")

    def spend(self, name, amount=1):
        self.check()
        if type(amount) is not int or amount < 0:
            raise ValueError("Invalid work reservation")
        if self.counts[name] + amount > self.limits[name]:
            self.stop("context_" + name + "_limit")
        self.counts[name] += amount

    def before_sql(self, family):
        self.check()
        proof = family in {"endpoint", "hydration"}
        if proof and self.counts["proof_queries"] >= self.limits["proof_queries"]:
            self.stop("context_proof_queries_limit")
        self.spend("vm_operations_reserved")
        if proof:
            self.counts["proof_queries"] += 1
        self.counts["sql_queries"] += 1

    def progress(self, quantum):
        self.counts["vm_progress_callbacks"] += 1
        self.spend("vm_operations_reserved", quantum)

    def reserve_edge(self):
        self.spend("examined_edges")

    def refund_edge(self):
        if self.counts["examined_edges"] <= 0:
            raise RuntimeError("Unbalanced shared edge reservation")
        self.counts["examined_edges"] -= 1

    def source_read(self, size):
        self.check()
        if type(size) is not int or size < 0:
            raise ValueError("Invalid source size")
        if self.counts["source_reads"] >= self.limits["source_reads"]:
            self.stop("context_source_reads_limit")
        self.spend("source_bytes", size)
        self.counts["source_reads"] += 1

    def tokenizer_call(self):
        self.spend("tokenizer_calls")

    def remaining(self, name):
        return self.limits[name] - self.counts[name]

    def remaining_ms(self):
        self.check()
        return min(2000, max(0, (self.deadline_ns - time.monotonic_ns() + 999999) // 1_000_000))
