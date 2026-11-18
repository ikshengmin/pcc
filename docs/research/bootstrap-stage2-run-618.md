# Bootstrap qualification 618: compare the same work

[Validation run 618](https://github.com/ikshengmin/pcc/actions/runs/44329580281)
used `80a0a010f235a9ea86bc607edeb66de92b6d4e73`. On Linux x86-64 and
Windows x86-64, CPython 3.15 running the owned PCC backend built a native
`pcc1`. Stage2 ran that executable on the compiler's own sources. Neither
target produced a `pcc2` before the original 1,800-second deadline. There
is no complete Stage2 duration, Stage2 ≤ Stage1 result, or fixed-point result.

## Same phase and platform, seconds of elapsed time

| Work | Linux Stage1 | Linux Stage2, GC4 | Windows Stage1 | Windows Stage2, GC2 |
| --- | ---: | ---: | ---: | ---: |
| Initial relative source closure | 1.893 | 305.095 | 3.861 | 264.259 |
| Multi-source relative closure | 1.840 | 92.370 | 4.478 | 97.226 |
| Recursive standard-library closure plus extension-port sweep | 3.434 | 71.140 | 7.718 | 85.101 |
| 598 exports, first worker start to last retirement | 25.265 | 696.444 | 34.088 | 882.139 |
| Entire Stage1 build | 2,254.734 | — | 3,017.168 | — |

Export intervals are wall time, not sums across workers. On Linux the host
batched 598 exports into 75 tasks; native Stage2 dispatched 598 one-module
tasks. Both ran 75 summary tasks, but the host summary interval was 15.591 s
and the native one 451.329 s. These are measured differences, not an
attribution to individual operations. The 305.095-second initial closure
finished before the export workers began. Do not attribute the whole gap
to GC, serialization, or backend codegen.

The Linux native coordinator last completed the derived class map, 1,760.000 s
after its first recorded phase. Its own peak was 6,407,819,264 bytes; the
inner compiler tree peaked at 11,427,254,272 and the enclosing guard at
11,932,643,328 (16 GiB cap). The outer time was 1,800.127 s. On Windows
only 11 summary tasks started and 9 retired (88 and 72 modules). Its inner
tree peaked at 9,459,355,648 bytes and its enclosing guard at 9,603,936,256;
the outer time was 1,800.081 s. Neither trace records a codegen start.
These are different scopes; none of these peaks constitutes a memory-limit
failure. An unlogged later phase is not evidence that it consumed the deadline.

## Artifacts and separate failures

The Linux and Windows Stage1 bundles are `14442406793` and `14444056029`;
the platform evidence archives are `14444762750` and `14447006892`; the
outer watchdog artifacts are `14445725746` and `14446910966`. Compare the
ordinary `platform-qualification/shared/stage1.log` with
`platform-bootstrap/gc4/stage2.log` (Linux) or `gc2/stage2.log` (Windows).

Linux AArch64 failed earlier: archive `14448364555` records a Stage1 deadline,
all 598 exports and summaries finished, and 82 codegen workers started vs
76 retired. The Stage1 last sample was 7,200.111 s, peak 13,171,294,208 bytes.
The final line names one in-flight worker; it is not a diagnosis of the
entire timeout. There is no ARM Stage2 comparison.

The separate 1 GiB Source/ci-contracts integration gate genuinely exceeded
memory: artifact `14449592312`, 1,181,802,496 bytes at the peak, four
compiler children present, five of six tests set up, zero completed calls.
It is not the 16 GiB Stage2 guard. A future improvement must be measured
against the same real inputs, target, concurrency, outputs, and resource caps.
