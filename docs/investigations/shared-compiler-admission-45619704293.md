# Independent compiler pool admission in one source guard

Source: [run 45619704293, shared ordinary job 139281129025](https://github.com/ikshengmin/pcc/actions/runs/45619704293/job/139281129025), full receipt artifact 15032924110, batch-016.

The synchronized sample at 17.199375 s measured 945,692,672 bytes:
controller plus six pytest worker processes 745,877,504 bytes; descendants
of three independent compile owners 199,815,168 bytes. The existing
134,217,728-byte headroom left 184.7 MiB for descendants. The app/rawmod
summary worker (task 0) measured 50,147,328 bytes against available
43,978,752; the actual failure is `WorkerMemoryError`, not an ABI mismatch.

The candidate preserves both the outer watchdog's fixed cap and deadline
and every individual pool reservation. The explicit source guard path owns
one OS advisory lock for independent resource-managed pools; a descendant
worker is in its parent's admitted subtree and cannot recursively contend
for the same lease. Acquisition precedes sampling: no stale pre-wait RSS
record admits the next owner. Implicit and unrelated state paths remain
unchanged. The lock is a rendezvous file, not the atomically replaced state
inode. Tests retain nonzero worker failure and original deadline behavior.

## Local Linux x86-64 execution

Candidate based on f81ac60f (codegen checksum
`0c8e20421bde3ab268725a29309b12e76a16ea6fa80f276b885575002e2ef129`).
CPython 3.15.0. Both edited frontend modules independently emitted compiler
IR. Regenerated runtime: 192 verified members, current compiler checksum,
60.69 s and 535,126,016-byte peak under the separately authorized
runtime-build guard (four jobs / 3 GiB). No old runtime fallback.

A real two-independent-owner fixture originally observed `start A, start B,
end B, end A`. It now runs A before B and an actual nested C pool inside B.
The fixture also propagates worker status 17, releases the lease, checks
unrelated guards, invalid identities and a contended absolute deadline.
Seven focused tests, nine CI/host-boundary assertions, and the complete
219-node CI-validation model file pass.

The actual helper was also imported into an owned native ELF built from this
source (30.95 s, 520,810,496-byte compilation peak). Under the same 1-GiB,
90-s process-tree guard, a CPython peer held the rendezvous file with flock
for 0.4 s. The native executable was still waiting; after release it acquired,
closed, and printed the expected result. Guard COMPLETE, rc 0, 0.494 s and
28,753,920-byte peak. This does not certify Stage1/2 or Windows/macOS.

The exact failing native-program node and overlapping original control were
then executed with six workers, loadgroup, 90 s and exactly 1 GiB, using the
fresh runtime: both PASS, guard COMPLETE, 42.37 s, 808,480,768-byte peak.

```sh
PCC_RUNTIME_ARCHIVE=<verified archive> PCC_NO_AUTO_PCC1=1 PCC_TEST_NO_NATIVE_PROVISIONING=1 \
PCC_WORKER_POOL_ADMISSION_STATE=<guard result>.worker-rss.tsv \
python scripts/run_process_tree_sample.py --timeout 90 --max-tree-rss-bytes 1073741824 \
  --performance-lock --process-rss-history --cwd . --result <guard result> \
  --samples <samples.tsv> --stdout <stdout> --stderr <stderr> -- \
  python -m pytest -x -vv -n6 --dist=loadgroup -m 'not integration' \
  tests/python/test_cross_module_int_abi_slots.py::test_boxed_ints_reach_a_raw_int_siblings_scalar_slots \
  tests/python/test_cross_module_inherited_field_abi.py::test_transitive_inherited_raw_arena_control
```

The complete frozen original selection (216 nodes, ten files, original
validation inventory plugin and six workers) was also executed. The local
cgroup preflight is unavailable; an explicit equal 1-GiB cap retains the
hard guard without changing the hosted automatic policy. Final receipt:
TIMEOUT at 90.174 s, peak 927,485,952 bytes, 214 passing call reports and
two failing call reports. Both resource witnesses completed; no
WorkerMemoryError. The independent
`test_cross_module_none_return_abi.py::test_cross_module_declarations_match_definitions`
encountered a local nested-uv cache access failure; its old hosted failure
was a distinct IR parser assertion. The final
`test_cross_module_valueclass_abi.py::test_valueclass_attribute_keeps_projection_in_class_constructor_argument`
reported `BackendUnavailable: self precise stack-map analysis in
'user_functools_update_wrapper': managed slot range exceeds its alloca`.
Those are not silently waived. The batch has not passed.

Original hosted 42.80 s (rawmod failed after 5.08 s; inherited control PASS
5.95 s; final valueclass PASS 16.72 s) is not a comparable elapsed-time
baseline for the local completed rawmod 63.17 s, inherited 48.60 s and
valueclass failure 37.68 s. Independent-pool serialization adds possible
waiting; no throughput claim follows. Rerun complete hosted lane under the
same existing limits. A separate shared integration sample reached
1,076,121,600 bytes with one native compiler subtree and six resident
workers; this peer admission alone does not lower that single-owner demand.
