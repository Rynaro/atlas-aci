# S06 Linux CI portability correction

PR70 head4b123e4 failed Docker CI job120741939874 after successful read-only/offline indexing: the Linux host UID could not read production UID10001 mode0600 graph files. The prior COMPLETE receipt and the actual failure report are preserved here. The authoritative receipt was reopened IN_PROGRESS with unchanged slice/criteria identity before test edits.

Graph bytes and metadata are now inspected in the production image as UID10001 with /graphs mounted read-only. No product source, ownership, mode or mount enforcement is relaxed. Source snapshots stay on the fixture-owning host. Descendant graph UID/GID/mode/hash/mtime/size remain compared. Docker Desktop's synthetic bind-root uid/gid can differ between containers; both are recorded diagnostically while all original root mode/mtime/size fields stay compared. The actual intermediate17pass1fail observation is preserved.

Corrected focused18 tests and independent ATLAS recheck pass with required Docker cases enabled and zero skips. Lint/format/mypy pass. Earlier full320 and green non-Docker CI are historical evidence for unchanged product source; no new full-suite result is claimed. Corrective output-commit tests,11 refreshed digests and clean reentry/resume are recorded separately before completion.
