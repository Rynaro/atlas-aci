"""ACI-S35: verified test-outcome feedback and an optional, separate ranking overlay.

`outcomes` checks a claimed test outcome against a pytest JUnit artifact, one retrieval record
and the leased snapshot, and records only what an artifact bears out. `ranking` re-orders the
unchanged baseline context result from those records, only when the operator asks for it.
Both commands are read-only with respect to the graph and the repository.
"""
