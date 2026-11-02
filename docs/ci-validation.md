# PCC validation

`.github/workflows/pcc1-package-parity.yml` is the single validation entrypoint.
The existing release-only publication behavior in `workflow.yml` is separate
and unchanged. This document describes execution policy, not a passing receipt.

## Purposeful publication

Intermediate commits use `[skip ci]`. A deliberately selected validation push
omits it and starts this one workflow. The planner compares the exact checked-out
SHA to the most recent successful same-workflow master run whose SHA is an
ancestor. It does **not** mistake `push.before` for the unvalidated batch base:
skipped intermediate source commits remain in the changed-path union. Missing
history, API access, ancestry, or a qualifying baseline selects every bounded
source domain and the original legacy gates. The plan artifact records the
actual base/run identity, event tip and each selection reason.

Frontend-only batches select the complete affected C++, Rust, Java-source or
Go test corpus. Shared/backend/runtime, unknown, renamed or deleted paths retain
legacy bootstrap, wheel and complete-marker qualification. Workflow/harness
changes retain the original legacy gates and select all bounded domains plus all
existing tests that inspect workflow contracts. Manual scopes only add obligations to a reviewed exact base range.

## Bounded source evidence

The source runner collects the current revision, partitions every raw node into
ordinary, integration or explicitly reviewed external-reference activity, then
executes module/xdist-group-preserving batches. Unknown reference classification
never removes a test. Conditional Rust certification stays with its original
ordinary/integration test. The checked-in shared scope is a module inventory,
refreshed for ELF, Mach-O and ODR tests; historical counts/outcomes are not reused.

Every source-lane test-body process retains six workers, `loadgroup`, the existing
90-second/1-GiB aggregate process-tree guard and 1-GiB free-disk floor. Collection
alone uses one process. Each job retains the 15-minute envelope. Resource,
source-identity or infrastructure anomalies stop that job. Source batches do not
inject fail-fast: ordinary assertion failures retain later nodes and independent
batches. Interrupted/internal-error sessions stop, but their valid reports are
saved first. Full setup/call/teardown PASS is distinct from call-only evidence;
unfinished and later unattempted nodes remain UNRUN. Durable raw inventory,
per-worker selection, node reports, commands,
stdout/stderr, RSS history and source hashes are uploaded even after failure.
Pytest's explicit subtest reports retain their parent-relative identity and
outcomes without adding selected nodes. A parent passes only with complete
phases and passing, non-xfailed subtests; ordinary duplicate phases remain invalid.
The source runner explicitly enables `--pcc-live-subtests`. Other live-report
consumers retain the original raw-report stream and their existing fail-closed
duplicate-phase checks; this does not silently widen legacy qualification.

Missing prerequisites remain `UNRUN_PREREQUISITE`; skipped/unfinished nodes
cannot make a source job green. Actual runner tool identities are recorded.
Rust/shared lanes install official rustc 1.91.1 through rustup with actual
Linux x86_64 and Darwin arm64 standard-library payloads. Installation failure
fails the job, never grants a substitute tool or target. Rust receipts pin the direct executable and actual target core/std files only;
`check_rust_source` still checks each original input, target and options before
minting a source token. A Linux receipt cannot authorize an absent Darwin sysroot.
No tool is silently substituted and no trust flag authorizes reference lowering.

The existing C++ platform corpus retains separate cross-object, actual owned
native execution and external C++17 reference observations on Ubuntu x86_64 and
macOS arm64, with real `uname` checks. These are host-PCC results, not native
pcc1 or user-device evidence. Known reference disagreements remain failures.

Original legacy jobs retain their own time/RSS budgets, Stage1 artifact leases,
platform-local later-stage owners, five-GC checks, prerequisites and publication
boundaries. A source-only lane neither replaces nor claims those guarantees.

The bounded source checkout excludes only `projects/nginx-1.28.3/`, preventing
an unrelated root-conftest configuration warmup. All other original projects and
frontend fixtures remain present. Sparse omissions are named in the source
manifest, not mistaken for deleted source. Original legacy checkouts are intact.
