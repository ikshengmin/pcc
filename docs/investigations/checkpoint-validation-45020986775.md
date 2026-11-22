# Exact-source comparison for checkpoint 45020986775

[Candidate](https://github.com/ikshengmin/pcc/actions/runs/45020986775)
`0f638af5bbaf8fb62b3e97d74e15d40fe3b89132` vs
[baseline](https://github.com/ikshengmin/pcc/actions/runs/44358778171)
`7ce5cf6635b0893ceca1f06932b1127745099223`, attempt 1, 53 jobs in each.
Both workflows completed red. Compare by exact job names, by matching
test call identities in the source and native receipts, and by actual
Stage1 receipts rather than a job that also runs a canary.

## Newly identified

- Linux Go ordinary is the only whole job that went from success to
  failure. Three exact nodes transferred from pass to fail; see
  [#10](https://github.com/ikshengmin/pcc/issues/10) for their identities.
- The Rust integration lane was already red, but
  `tests/rust/test_rust_cfg_items.py::test_actual_disabled_item_with_a_macro_body_is_skipped@rust-native-reference`
  changed from an executed pass to an executed failure.
  [#11](https://github.com/ikshengmin/pcc/issues/11) tracks the retained
  inactive AST shape and its semantic guard.
- Windows ordinary is red in both runs. The candidate stops collection
  before any tests run: a Linux-only perf module imports `fcntl` when
  collected. The baseline executed 523 passing cases. This is a newly
  observed collection blocker, not a matching pass-to-fail case.
  [#12](https://github.com/ikshengmin/pcc/issues/12).

## Stage identity and evidence boundaries

| Lane | Baseline Stage1 | Candidate Stage1 | Interpretation |
|---|---:|---:|---|
| Linux x86-64 | 2,202.3 s, emitted pcc1 | 2,315.0 s, emitted pcc1 | +5.1%, below Rule 13 threshold |
| Windows x86-64 | 3,358.1 s, emitted pcc1 | 3,194.0 s, emitted pcc1 | −4.9% |
| Linux ARM64 | 7,200 s timeout, no admitted pcc1 | 6,794.1 s, emitted pcc1 | first successful comparable lane artifact; no baseline completed time |
| macOS ARM64 | Stage1 failed | Stage1 failed | no emitted pcc1 on either side |

The baseline/candidate native ownership JUnit receipts on Linux x86-64,
Linux ARM64 and Windows show the same four failing pcc0 nodes with
`MISSING: indexed completion manifest`. Native Windows and both Linux
wheel lanes still fail Stage2→Stage3. The Linux ARM64 bound-find step
failed after Stage1 first became available; do not count its previously
unexecuted baseline as a passing comparison. Stage2 ≤ Stage1 and the
fixed point are not established here.

Linux C++ integration was cancelled in the candidate. The macOS pcc0
focused lane and Linux shared ordinary lane were cancelled at baseline,
so they are not comparable. The current macOS focused receipt did run
39 `gc-sweep` cases without failure; its `root-joins` typed-loop-unbox
case had exit 143, and the enclosing gate timed out with 124. The newly
runnable Linux ARM64 marker jobs, the unavailable macOS marker inputs,
and the x86 Linux/Windows integration markers without test call receipts
do not establish test-node regressions. Source samples that omit a
baseline call (including newly run WeakSet owner evidence) are not
silently counted as known or previously green.

The matching call receipts from source ci-contracts ordinary and
integration, C++ reference, Go integration, Java integration and
reference, Rust integration, shared integration and reference, and
Linux x86-64 ordinary marker were inspected, including previously red
lanes. Frontier/oracle/inventory jobs without a comparable node
receipt retain their reported job conclusions; a repeated red job is
not treated as a node-level guarantee.

Do not advance the checkpoint while #10 or #11 remains unresolved.
