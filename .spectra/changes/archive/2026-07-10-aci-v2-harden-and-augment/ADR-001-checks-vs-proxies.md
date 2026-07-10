---
artifact: architecture-decision-record
id: ADR-001
title: Checks vs. Proxies — Verifying Data Provenance, Not Properties Alone
status: accepted
context: atlas-aci v2.0.0 campaign retrospective (RETRO.md)
decision_date: 2026-07-10
reviewed_by: vigil (checker), vivi (maker)
---

# ADR-001: Checks vs. Proxies — Verifying Data Provenance, Not Properties Alone

## Context

The atlas-aci v2.0.0 hardening campaign discovered and refined a recurring pattern across eleven distinct defects. Each shared the same structural failure: **a check that validated some property of the data it was handed, while the provenance and completeness of that data itself remained unchecked.**

Examples span the entire stack:
- P0 F-1: SQL query cap and central cap collided at exactly 200, validating "count == boundary" without signaling "is this complete?" (checker-verdict-p0.md F-1)
- P0 F-3: Gate grep validated "marker phrase is present" without checking "does gate actually run?" (checker-verdict-p0.md F-3)
- A3 MAJOR-1: Probe verifier validated "word 'PASS' is present" without checking "do numbers close the arithmetic?" (checker-verdict-a3.md MAJOR-1)
- A4-A5 MAJOR-1: Import validated "content matches hash" without checking "is path semantically safe?" (checker-verdict-a4-a5.md MAJOR-1)

Individually, each check was mechanically correct. Collectively, they allowed false confidence through the gate because **they validated the shape of the data without validating its origin.**

## Problem Statement

A **proxy check** is a check on a property of the data (size, format, presence) serving as a stand-in for a property of the *source* (completeness, derivation, safety). Proxies fail when:

1. **Data can be valid per proxy while having false source.** A forged probe sidecar with `LPA_Q: 0.11111` is valid JSON; proxy ("check if PASS label exists") passes. Source ("are these numbers real measurement?") is false. (checker-verdict-a3.md MAJOR-1)

2. **Proxy is orthogonal to what it guards.** SQL LIMIT validates "stop at row 200"; central cap validates "signal incompleteness." At exactly 200, both pass, but neither validates *whether signal is actually set*. (checker-verdict-p0.md F-1)

3. **Proxy answers question check never asks.** Marker string validates "file contains text"; gate's purpose is "prevent augmentation before hardening." Marker presence unrelated to whether gate-criteria step runs. (checker-verdict-p0.md F-3)

4. **Data crafted to pass threshold while threshold itself never validated.** Probe sidecar claims `louvain_q_by_seed: [0.35, 0.35, ...]` (median 0.35); proxy ("list of 10 numbers?") passes. True Louvain Q is 0.74; rule `LPA_Q >= 0.85 * median` only as honest as input. (checker-verdict-a3.md MAJOR-1)

**Why this matters for mechanical systems:** Atlas-aci's thesis is **mechanical honesty** — bounds and criteria enforced by code, not trust. System whose gates are all proxy-checks is trustless in name only; it shifted trust from "please be truthful" to "please forge data to pass format checks." Gate's teeth are illusory.

## Decision

**Thresholds and required record-sets are external facts. They belong in the verifier, never in the artefact under audit.**

A verifier—code that decides pass/fail—must never accept threshold *from the thing it is verifying*. Pass rule must be hardcoded, required repos must be asserted, expected digest must be pre-committed. Artefact may carry this for humans, but verifier never reads these fields for decision logic.

### Observable Signals

#### 1. A recorded number becomes an assertion; derive it fresh

**Before:** "Sidecar says `LPA_Q: 0.6691`. Accept it."

**After:** "Recompute `Q` from edge list. Reject if mismatch to float tolerance."

(Evidence: checker-verdict-a3.md MAJOR-1, lines 104-106; probe-lpa-vs-louvain.md lines 99-106 document recomputation in pure Python; all 22 Q values reproduced bit-for-bit)

#### 2. Required set must be asserted, not discovered

**Before:** "Check if `repos` field exists. Verify one repo passed."

**After:** "Assert sidecar contains exactly two pinned repos by SHA. Verify both passed independently."

(Evidence: acceptance-criteria.md preamble lines 31-35 hardcodes both SHA constants; pass rule requires BOTH independently, never averaged)

#### 3. Criterion naming artefact must name one that exists and runs

**Before:** "Check filename exists. Assume test runs."

**After:** "At phase exit, verify every VERIFY naming test file names test that exists in tree and runs in CI."

(Evidence: checker-verdict-a1.md MINOR-2, lines 114-124; AC-H-18 named phantom test never in git history, written retroactively)

#### 4. Threshold constant frozen before data it grades

**Before:** "Compute rule from measurements. Accept if internally consistent."

**After:** "Write rule to criteria before measurement. Verifier hardcodes. Data feeds rule, never derives it."

(Evidence: deliberation-amendment-d3a-d4a.md D3a lines 26-112; probe-lpa-vs-louvain.json hardcodes `R = 0.85`, `Q_struct = 0.30`, frozen before probe)

#### 5. Gate tested before used to decide

**Before:** "Check workflow file mentions pytest. Assume job runs."

**After:** "Test gate by running deliberately failing test. Verify it rejects."

(Evidence: checker-verdict-p0.md second pass lines 178-241 documents F-1 empirical reproduction and fix-retest cycle)

## Validation by Campaign

Three correction rounds:

1. **Round 1 (A3 MAJOR-1):** Verifier was `grep "verdict.*pass"`. Corrected to: recompute rule from hardcoded constants. Result: PASS now requires honest Q values. (checker-verdict-a3.md MAJOR-1, lines 94-116)

2. **Round 2 (P0 F-1/F-2):** Cap checked at boundary without signaling. Corrected to: cap triggers at `cap+1` so central middleware sees `>cap` and must flag. Result: Signal unconditional on boundary. (checker-verdict-p0.md F-1 lines 114-118; second pass lines 206-207)

3. **Round 3 (A4-A5 MAJOR-1):** Import validated hash without path scope. Corrected to: import asserts paths relative and repo-contained. Result: Provenance now enforced. (checker-verdict-a4-a5.md MAJOR-1 lines 72-74)

Each moved check upstream: "does result look right?" → "is result correct?"

## How to Apply in Future Work

When designing verifier (gate, test, acceptance check):

1. **Identify external fact.** ("This Q from LPA." "Export under limit." "Canonical repo.")

2. **Push check upstream.** Don't check "did we get Q?" Ask "did we compute Q correctly from primitives?"

3. **Hardcode boundary, never read from data.** Don't accept `R` from sidecar. Hardcode `R = 0.85`, verify sidecar matches.

4. **Commit primitives (not summaries).** Let verifier re-derive and cross-check.

5. **Test verifier on forged defects.** Only-passes-real-case = proxy. Fails-loudly-on-knowns = real check.

(Evidence across campaign: RETRO.md §1-11; checker-verdict-p0.md F-1..F-8 + NEW-1..NEW-3; checker-verdict-a1.md MAJOR-1; checker-verdict-a3.md MAJOR-1; checker-verdict-a4-a5.md MAJOR-1)

## Reversal Condition

Revisit if:

- **Ground truth becomes inaccessible** (repos deleted; unlikely; mitigation: long-term mirror)
- **Re-derivation becomes prohibitive** (billion-node Q takes hours; unlikely; current: 22 values in milliseconds)
- **More trustworthy method emerges** (crypto commitment; none now; re-derivation is current honesty method)

## References

- **RETRO.md:** Eleven defects grounded in checker artefacts.
- **checker-verdict-p0.md:** F-1..F-8 + NEW-1..NEW-3 (hardening gate).
- **checker-verdict-a1.md:** MAJOR-1 (shadowing tier).
- **checker-verdict-a3.md:** MAJOR-1 (probe grep); second pass documents fix.
- **checker-verdict-a4-a5.md:** MAJOR-1 (import path).
- **probe-lpa-vs-louvain.md:** "Anti-circularity discipline" section (verifier hardening).

---

**Status:** Accepted for atlas-aci v2.0.0 and forward. Encoded in harden-gate, criteria, verifier. Future releases inherit discipline.
