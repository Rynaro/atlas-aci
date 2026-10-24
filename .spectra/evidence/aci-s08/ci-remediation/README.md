# S08 CI inventory correction

The private snapshot store is0700 and its files0600, owned by image UID10001.
CI's host runner could not traverse it. The old before `find | sort` pipeline
masked that error, while the strict after step failed. This did not establish
any server write. Exact failed job40131848502/120985025373 logs are retained as
decoded UTF-8 gzip with byte counts and hashes; the prior COMPLETE receipt is an
immutable, non-authoritative historical snapshot. Only the state receipt remains
authoritative, with the original criteria and supporting identity.

The two inventory steps now run one shared task-temp Python program as the same
image/default reader UID, under read-only rootfs, offline network and read-only
source mount. Explicit traversal fails on any inaccessible, linked or nonregular
entry; complete path/type/mode/uid/gid/size/mtime_ns and file SHA256 rows are emitted
only after the entire inventory succeeds. No private permissions were widened.
Every other workflow byte is unchanged from1fc05.

Actual mirror: all five existing basic smoke steps passed, including implicit
memex startup, and complete11-entry before/after inventories matched. The existing
frozen S06+S08 native step passed19 tests without skips. Independent ATLAS Linux
volume evidence reproduces UID1001 denial and default10001 success with private
permissions, unchanged inventories and EROFS. All nine runtime source hashes and
image da089d0b are unchanged; source/full/probe measurements were not rerun.

The successor output gets its own exact detached product/reentry/resume checks;
prior output7217e9f and completion1fc05 remain reachable without rewriting history.

`script-raw-manifest.json` maps the five exact executed step scripts to gzip
archives (mtime0/raw and compressed hashes). Their original terminal blank lines
are retained, rather than normalized to satisfy Git whitespace checks.
