# Exact-source comparison for checkpoint 45427972345

[Candidate 45427972345](https://github.com/ikshengmin/pcc/actions/runs/45427972345)
(`7ec14d388`) vs [baseline 44358778171](https://github.com/ikshengmin/pcc/actions/runs/44358778171)
(`7ce5cf663`), 53 matching job names, both workflows red. Same-named
jobs were compared by conclusion; for node evidence the join was on
the complete pytest node ID and completed call result (not collection,
setup, or a missing call). A sample may repeat a node with a named
worker-resource fixture suffix.

## Regressions and disposition

- Darwin pcc0 float/class-doc: only whole job success → failure. The
  same JUnit node `test_canonical_float_callback_movement_gc4[pcc0]`
  passed in the baseline and now links against missing
  `_pcc_gc_select_relocation_set` / `_pcc_gc_step`.
  [#19](https://github.com/ikshengmin/pcc/issues/19); replacement is on
  master at `6c1c280f`, Linux test evidence only so far.
- Shared ordinary: despite cancellation of the baseline job, its
  partial receipt has exact calls for 12 candidate failures that
  previously passed: five checked frame-registration cases (deleted
  scheduler source), one slow comparison IR expectation and six
  operation IR cases. The candidate has 4,311 matching calls overall.
  Do not infer a whole-lane comparison from those receipts.
- Linux CI contracts ordinary: 23 pass → fail receipt records
  (including the same worker-resource node in two lanes of its plan).
  Includes the Darwin GC sweep watchlist, bootstrap path/validation
  contract checks, and native receipt contract cases. Scope of both
  groups and exact evidence: [#20](https://github.com/ikshengmin/pcc/issues/20).
  Keep active behavioral tests; retire only obsolete GC modes.
- Worker prior Linux x86-64: its candidate cleanup IR node was red;
  the baseline has no matching call, so this is not a proven regression
  from the baseline. The stale expectation was fixed at `f14d6097`.
- Windows ordinary has no candidate call evidence: its receipt reports
  `test_pcc_perf_linux.py` cannot import `fcntl`. This is the already
  tracked [#12](https://github.com/ikshengmin/pcc/issues/12) blocker.
  Darwin focused and several other jobs were cancelled; do not count
  them as green. No new pass → fail call was found in the compared
  coverage records for Linux C++ ordinary/reference/integration, Go
  ordinary/reference/integration, Java ordinary/reference/integration,
  Rust ordinary/reference/integration, shared reference/integration,
  Linux ordinary complete pytest, or worker prior on Linux/Windows.
  This statement is limited to emitted calls.

## Prior reported nodes

- [#10](https://github.com/ikshengmin/pcc/issues/10): Linux Go ordinary
  and Darwin Go ordinary passed. Both named Go record diagnostics
  passed in the candidate's Linux artifact. The old bounds-panic
  rejection node was replaced; its new target capability node passed
  in Linux ordinary. The separate new run-and-trap native integration
  node has no call in this checkpoint; do not count it as passing.
- [#11](https://github.com/ikshengmin/pcc/issues/11): the Linux Rust
  integration lane is still red, but both exact
  `test_actual_disabled_item_with_a_macro_body_is_skipped` calls
  (with and without `@rust-native-reference`) passed. The two unrelated
  recorded failures were already red. Rust ordinary also passed.

## Stage1

Times below are the `compile_python_total` records in the native Stage1
logs, not durations of jobs that include different canaries.

| Lane | Baseline | Candidate | Result |
|---|---:|---:|---|
| Linux x86-64 | 2,202.0 s | 2,659.2 s | +20.8%, emitted pcc1 in both |
| Windows x86-64 | 3,357.5 s | 2,762.3 s | −17.7%, emitted pcc1 in both |
| Linux ARM64 | 7,200 s timeout | 7,200 s timeout | No admitted pcc1 in either |
| Darwin ARM64 | Worker memory limit | Worker memory limit | No admitted pcc1 in either; C codegen in indexed backend |

No comparable Stage1 lane crossed the +25% threshold. The wheel
and fixed-point gates remain unqualified; these records do not
establish Stage2 ≤ Stage1. Keep the previous checkpoint.
