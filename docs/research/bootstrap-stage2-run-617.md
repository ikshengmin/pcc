# Native bootstrap baseline: qualification run 617

[PCC validation 617](https://github.com/ikshengmin/pcc/actions/runs/44302659573)
finished at commit `19c4ab044e08c1b942daa697aecdd0f19fb3d884`.
Linux x86-64 and Windows x86-64 produced Stage1 compilers. Neither completed
Stage2 before the 1,800-second owner deadline. The run does **not** establish
Stage2 ≤ Stage1, a bootstrap fixed point, or a pass for all five collectors.

## Where the native compiler spent time

These are observed wall intervals from the first worker admission to the last
retirement in the original logs. They are not summed CPU time or projections.

| Platform | Stage1 export | Stage2 export | Last Stage2 work observed |
| --- | ---: | ---: | --- |
| Linux x86-64 | 598 modules, 75 workers, 45.6 s | 598 modules, 598 workers, 922.2 s | GC3: 14 summary workers started, 12 retired (96 modules); no codegen start recorded. |
| Windows x86-64 | 598 modules, 75 workers, 39.8 s | 598 modules, 598 workers, 1,013.2 s | GC2: export results read took 21.742 s; no summary start recorded. |

A separate cost appears **before** those workers start. The initial relative
source-closure pass took 243.318 s on Linux and 323.384 s on Windows; the
following multi-source relative-closure pass took 89.318 s and 110.934 s.
These intervals do not establish how much time an individual scan, file read
or process launch would save. Any comparison must preserve the ordered graph,
individual worker admission and retries, prior resource limits, and output.

Linux and Windows raw artifacts are `14434995597` and `14433283638`.
Read `platform-qualification/shared/stage1.log` and
`platform-bootstrap/gc3/stage2.log` (Linux) or
`platform-bootstrap/gc2/stage2.log` (Windows). The Linux Stage1 bundle
`14430828439` ZIP is 200,904,156 bytes, SHA-256
`d0a0c1cccd257f4724c1dd773792c7944b4f73a036997755084395e66f25fa44`.
Its 469,268,420-byte compiler has SHA-256
`8f392f30ec2f729095e4b20a7d65a523e89be61acbe6b0e56d56747acb1f1d2e`.
The bundle inventory, all 14 member digests, embedded manifest and Stage1/canary
receipts distinguish this compiler from earlier Stage1 binaries.

## Do not combine unlike failures

Linux AArch64 stopped in Stage1 at the 7,200-second deadline (artifact
`14435533228`). macOS ARM64 Stage1 stopped in indexed backend work on
`pcc.frontends.c.codegen.c_codegen`: the worker reached 2,761,736,192 bytes
against 2,685,222,912 bytes of safe worker space (artifact `14427778894`).
Neither provides a Stage2 time. A future comparison needs completed stages on
the same target with the same inputs and collectors, including memory, CPU,
wall time and a working emitted compiler.
