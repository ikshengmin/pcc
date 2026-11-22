# GC0 atomic CPU-share preflight

The GC0 work-order threshold is 10% of CPU, not a share of reference-count
calls or inferred time from request throughput. Reference-count helper
samples include checks, slow paths and destruction; atomic operations also
occur inline at other call sites. The two measurements are not interchangeable.

On Linux x86-64 at `9ef916de`, opening a user-mode, self-process
`PERF_COUNT_SW_CPU_CLOCK` event succeeded. The host has no `perf`
executable, and `pcc_perf top` invokes the macOS `/usr/bin/sample` tool.
There is no sealed handler arm or sibling `pcc_gateway` package on this
host. No GC0 workload was profiled and no biased-counting decision follows.
Do not add an unreviewed scratch profiler or report call counts as CPU
attribution. The next Linux path is a reusable, tested `pcc_perf` Linux
sampler plus the same gateway source and sealed arm.

From an authorized macOS checkout of the same revision, with the existing
gateway package available (or `PCC_GATEWAY_PACKAGE` set to it), start with:

```sh
uv run python scripts/pcc_perf.py arm gc0-atomic-9ef916de
uv run python scripts/pcc_perf.py top gc0-atomic-9ef916de --gc 0 --workers 1 --seconds 8 --limit 80
uv run python scripts/pcc_perf.py top gc0-atomic-9ef916de --gc 0 --workers 4 --seconds 8 --limit 80
```

These are function-localization runs, not five-pair A/B results; final
attribution also needs instruction-level evidence for atomic sites,
including inlined sites, with the total busy CPU denominator.

## Linux task-clock sample transport

`uv run python scripts/pcc_perf.py profile-preflight --cpu-ms 60` opens
a bounded user-mode task-clock event on the tool's own thread. It
disables the non-inherited event before decoding the mapped ring and
reports only the count of samples and distinct instruction addresses.
It does not attach to a workload or infer a CPU ratio. It uses the
[Linux perf ring buffer contract](https://www.kernel.org/doc/html/latest/userspace-api/perf_ring_buffer.html)
and refuses malformed metadata, invalid records and explicit loss.

Linux x86-64 exercised the real command at 40/60/100 ms, returning
46/72/109 user-mode samples with positive, verified thread identity.
Ninety focused controls passed, including a record that crosses the
ring boundary and broken/lost-record rejection. Linux arm64 is not
yet executed. Next prove samples on a single owned child, then map
those addresses to the sealed executable. Workload coverage and the
gateway arm remain separate prerequisites for GC0 atomic CPU share.

The owned cooperative-child control passed on the same Linux host.
A pcc0/self-produced C ELF sends a ready marker before its loop. The
parent opens a non-inheriting event on that directly spawned task
and requires the kernel's user-mode PID/TID samples to belong to it.
The sampler fails for an invalid or late marker, extra output or a
lingering output pipe instead of including other tasks. This is not
arbitrary PID attachment. Fourteen focused transport/protocol checks
passed. Next attribute the real child's PCs from its ELF sections,
including an explicit unknown bucket rather than assuming every sample
hit the hot function.

The original owned ELF is stripped. Its sampled addresses now map
through file-backed executable PT_LOAD ranges to exact file offsets
without inventing a nearest function. The bounded parser admits static,
little-endian ELF64 ET_EXEC and refuses dynamic images, overlapping or
malformed loads, and addresses in data or zero-filled tails. Unmapped
PCs have no attribution. Twenty-five focused checks passed, including
kernel samples from the actual owned ELF and malformed-image controls.
Next bind acquisition to the bytes of the selected sealed executable;
instruction/GC boundary classification remains a later measurement.

A cooperative sealed capture now hashes one open, regular unprivileged
ELF, executes that descriptor, maps PCs using its bytes, and verifies
the descriptor contents again afterward. A path replacement does not
change the opened executable; an initially different hash or a content
change during capture fails. Tests executed the actual compiled ELF
both normally and after replacing its original path, and checked a
drifted file with a negative control. All 26 focused nodes passed.
Next obtain explicit kernel loss and scheduling counters for the same
sample stream. No atomic classification or workload share is claimed.

The capture now verifies the kernel's explicit `PERF_FORMAT_LOST`
count with the event disabled, as well as nonzero clock and equal
enabled/running time. A full ring may have no room to carry its own
lost-record event; a bounded Linux burst forced a real one-page
overflow and the kernel count rejected it. The normal owned binary
capture stayed loss-free. All 32 focused nodes passed. This qualifies
transport, not an unbiased instruction/CPU-share estimate. Next
qualify a fixed and directly owned child thread cohort before assuming
one sampled task represents four workers.

The fixed-cohort native control creates one leader and three PCC
workers before READY, holds all four through DONE, and receives a
release acknowledgment only after capture. The Linux tool checks the
owned task set at both boundaries, verifies every kernel PID/TID, and
keeps a separate counter and stream for each. The owned four-task
trial and a wrong-count rejection both passed; 33 focused nodes
passed in all. This is a cooperative fixed pool and not a claim that
an arbitrary dynamic workload cannot spawn a transient task. Next
calibrate instruction-site attribution in an owned native control.

## Exact-IP calibration: software task clock cannot supply the GC0 ratio

The x86-64 owned assembler and linker built a minimal static control
that actually executes 20 million `xadd` operations, with and without
`lock`. The native code checks its final counter. The known instruction
start comes from the original owned object labels plus the final ELF
entry; we independently check the opcode bytes in the selected image,
then keep all other/unmapped samples in the denominator.

Across three captures per shape, locked-loop sample totals were
99/90/90, and unlocked totals 86/90/100. Both ran the intended loop,
but every capture had **zero** exact-start samples on `xadd` itself.
Therefore an ordinary task-clock sample's address must not be read as
an exact atomic-instruction CPU-share measurement, even on this known
hot loop. No reference-counting percentage or optimization verdict
follows. Thirty-five focused nodes passed. Next test whether the
Linux host can actually provide precision-qualified hardware samples;
if not, keep this tool as transport/localization and leave the atomic
CPU-share gate open.
