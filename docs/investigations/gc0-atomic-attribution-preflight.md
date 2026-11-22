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
