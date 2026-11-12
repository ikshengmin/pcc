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

Collection uses a frozen, disjoint plan of at most ten complete modules per
single-process shard. Each shard has the original 90-second/1-GiB guard; the
15-minute job envelope is unchanged. Every requested module, including empty
modules, must have an actual successful collector report. Missing, duplicate,
failed/skipped nested collection, malformed terminal evidence, or contradictory
selection/count/exit evidence stops the job before any test body. Exit 5 is valid
only for zero selected nodes; a globally empty raw inventory remains an error.
Completed shard receipts and modules still awaiting complete inventory survive
a later collection failure. Collection alone is never execution PASS.

Only the exact complete canonical-node union feeds the existing global batch
planner, so undeclared xdist groups spanning collection shards still execute
together, including transitive module/group components above the target size.
The explicit `[tool.pcc-ci.group_policy.<domain>]` tables in `pyproject.toml`
may declare a group `resource-exclusion-only`: it needs mutual exclusion, but has no
cross-module Python session or fixture lifetime requirement. Only those groups
may span multiple **serial** guarded batches. Modules remain indivisible, the
original xdist marker stays unchanged, and combining an opted-in group with
any other xdist marker is rejected because xdist combines their exclusion keys.
Default groups never acquire this permission implicitly. The policy is saved
in the campaign receipt and bound by the existing frozen source manifest.
Invalid domains, modes, empty names, duplicate TOML keys, unreadable policy,
or declared names absent from that domain's complete raw inventory fail closed.

`go-reference` opts in because its oracle calls use separate test-owned outputs
and an explicit shared on-disk `PCC_GO_REFERENCE_CACHE`, not cross-module session
fixtures. Every batch inherits the same cache and tool environment. The runner
waits for each process-tree guard, validates its terminal evidence and records
outcomes before admitting the next batch; an anomalous batch stops admission.
In particular, a guard receipt with nonempty `post_exit_cleanup_pids` stops
admission even when its status is `COMPLETE` and its root returned zero: sending
termination to observed descendants does not prove they have finished exiting.
The unchanged guard's normal receipt omits this field when no cleanup was needed;
an empty list is also accepted. This remains the existing observed-process-tree
boundary, not a claim to detect every possible escaped process.
This is a scheduling contract, not a relaxed time budget or a passing result.
New cross-module session dependencies require withdrawing the declaration.
Worker inventories must match the frozen raw metadata for every file in their
body batch, including other lanes and unmet prerequisites, ignoring only xdist's
final node-ID decoration. Partition-dependent gate, group or identity drift is
an evidence error, never an automatic omission or a new qualification claim.

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

## Exact source-platform coverage

`scripts/data/ci_validation_owners.json` assigns exact canonical source nodes to
host platforms independently of the reference classification. Linux x86_64 is
the default. Explicit platform lists require every listed owner, never an
alternative fallback. The first override assigns the unchanged Darwin errno SDK
reference node to macOS arm64 (`macos-15`). Its real PATH `clang` invocation and
bytes are attested alongside the existing C++ `g++` oracle. This is external
reference evidence, not owned native execution or evidence from a user's Mac.
Unknown prerequisite skips do not create owners or waive tests.
The Java tenth-float Darwin ABI integration node also belongs to macOS arm64.
It uses host-PCC object emission plus real clang caller/control execution, not
a Stage1 compiler. Its explicit clang override is bound to the audited tool
before collection; ordinary cross-object tests and all Linux lanes remain.

The planner retains every original domain/lane campaign and native Stage1
producer, adding only the required nondefault platform campaigns. A C++ change
therefore includes its complete three Linux lanes plus the exact macOS reference
obligation; a Go-only change does not invent a macOS campaign. Both the plan and
receipts bind the tracked ownership-manifest digest and exact repository, source
SHA, workflow run and attempt. Source producers attest their actual host and
preserve the existing frozen source/tool checks, six workers, guarded batches,
90-second/1-GiB limits and 15-minute job envelopes. The macOS SDK reference needs
no native wheel or runtime build. Its inventory retains complete original module
metadata before selecting only the explicitly owned nodes.

Nonlocal obligations remain `TRANSFER_PENDING`. A locally complete campaign
with such transfers is `PLATFORM_COVERAGE_PENDING`, not aggregate PASS. The
required `source-platform-coverage` job runs after all planned source producers,
including when planning or producers fail, skip or cancel. It downloads the exact plan and
unmerged compact `coverage-*` producer artifacts, reconstructs complete phase/subtest outcomes and
joins every required node/platform obligation. Missing downloads still reach the
reconciler and produce failing aggregate evidence. Only download steps may
continue after errors; reconciliation itself may not. Absent, stale, duplicate,
wrong-host, skipped or failed evidence remains incomplete or failing. A passing
wheel/bootstrap cannot satisfy missing source execution. The aggregate receipt
is retained on failure as well as success. These are CI execution contracts;
source review alone does not establish that the campaigns passed.

Full `source-*` artifacts retain original logs and raw diagnostics. The compact
coverage artifacts contain only campaign JSON, collection and batch selection,
process-tree result JSON, live node reports, outcomes and worker inventories.
They omit test binaries and reference caches, so the final join does not need to
download those potentially large outputs. Compact files retain their original
relative paths; evidence is not rewritten to manufacture aggregate success.
Required tool version streams also retain their original bytes in base64 inside
the tool receipt; reconciliation verifies the raw hashes and displayed text.
Node completeness is attested by successful complete-module collection and six
matching execution inventories, not inferred independently from Python syntax.
Native input admission remains the source producer's before/after validation of
the actual Stage1 bundle, runtime provenance and same-run lease. The aggregate
checks those frozen admission receipts without reopening another runner's files.
