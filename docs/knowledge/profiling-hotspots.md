# Profiling and hotspot diagnosis

Maintained usage guide, added 2026-09-23. This is not a benchmark receipt.
Read current CLI help and bind measurements to current inputs before reusing a
historical finding. Start with `docs/development-tools.md`; do not invent a new
temporary harness for an operation already covered there.

## Find the work, then the calling site

1. Read the Stage1/Stage2 receipt and phase report. Confirm compiler/runtime/
   source identity, actual pass selection, cache and worker inputs. Use
   `scripts/bootstrap_profile_report.py PROFILE_DIR` for phase totals and
   `scripts/pcc_emit_rank.py --help` for retained indexed-PCO input ranking.
   Phase totals may overlap; worker CPU sums are not elapsed build time.
2. Run a bounded replay under `scripts/run_process_tree_sample.py`: shared
   performance lock, frozen sources/inputs, isolated outputs, timeout, tree-RSS
   cap and normal Darwin reserve. Select the actual working process from the
   receipt/TSV. `scripts/replay_pcc_codegen_worker.py --help` accepts a retained
   `codegen_worker.v4` manifest and its Stage2 receipt; it does not rebuild the
   full chain. Indexed-PCO inputs use `pcc_emit_rank.py --input-format indexed-pco`.
3. Capture the selected process, keeping the raw data. Example while that
   owned replay is running (replace PID and use fresh output paths):

   ```sh
   env -u LC_ALL uv run python scripts/pcc_flamegraph.py cpu PID 15 \
     --exact-pid --evidence-dir /tmp/pcc-capture-NEW \
     --folded /tmp/pcc-NEW.folded -o /tmp/pcc-NEW.svg \
     --report-json /tmp/pcc-NEW.json
   ```

4. Re-analyze without another build or sampling run:

   ```sh
   env -u LC_ALL uv run python scripts/pcc_flamegraph.py report \
     --input-folded /tmp/pcc-NEW.folded \
     --focus decode_indexed_module_file --report-json /tmp/pcc-decode-NEW.json
   ```

   `--focus` accepts an exact symbol or a unique substring; ambiguous/missing
   names fail. JSON includes full symbols, aggregated self/inclusive weights,
   focus callers and disjoint immediate children. Recursive occurrences count
   once for inclusive attribution; focus partitions at its first stack entry.
   `percent_total` uses all captured weights, `percent_selected` uses the focus
   subtree. Inclusive percentages overlap; do not add them. For heap/peak
   folded files explicitly pass `--units bytes`.
5. Trace the dominant calling site in source and its generated IR. Determine
   direct versus dynamic call, boxing, temporary owners and repeated work.
   Sampling implicates a path; a minimal native execution must verify the
   proposed mechanism. Do not optimize a ubiquitous leaf solely because it
   ranks first globally.

## Read stage time before choosing an optimization

For deferred Stage2, record the coordinator end, last PIDX, last PCO, pcc2
publication and final barrier as timestamps since launch. Subtract adjacent
timestamps to obtain phase durations: a last-PIDX timestamp of 600 s after a
200-s coordinator means 400 s of deferred frontend work, not 600 s. Profile
phase totals can overlap, so do not add them to reconstruct wall time.

Compute effective CPU use as `(user_s + system_s) / compile_wall_s`; a worker
count is not CPU utilization. Before changing an admission floor, compare the
current stage with both `total_tree_cpu_s / available_cores` and its longest
serial segment. If either lower bound already exceeds the target, scheduling
alone cannot meet it. Report a component's saved seconds as a component result
until a full source-frozen stage measures the end-to-end result. A single run
against a different source or resource cap is an observation, not an A/B win.

## Large mechanisms can be spread over small sites

Prioritize total attributable cost, not patch size or the largest single
function. Dynamic dispatch, boxing/refcount traffic, repeated decoding and
representation conversion can be individually small at hundreds of sites yet
dominate together. Examine both caller-focused partitions and global aggregated
self weights. For a cross-site mechanism, classify each leaf sample once; do
not sum overlapping inclusive percentages for getattr/call/decref. Combine the
profile with operation counts, bytes/records moved and pass/phase boundaries.

If no single function dominates, investigate repeated work and representation
changes across the pipeline. A compiler-wide design cost is still actionable;
absence of one large function does not justify a queue of tiny local tweaks.
Verify the proposed mechanism on the real native path and measure end-to-end
gain. Three low-ceiling candidates against a large gap require reassessing the
owner, as specified in AGENTS.md. Current reports do not automatically infer
these semantic categories or prove which runtime work is removable.

## Evidence and acceptance

Capture modes print an evidence directory. Native modes retain `native.sample`,
`stderr`, `capture.json` and validated `capture.folded`. Symbolization failures
leave raw evidence with `CAPTURED_UNVALIDATED`; a process dying after usable
sampling produces `PARTIAL`. Host mode retains each process's folded file and
stdout/stderr, including for nonzero exits; an aggregate of a failed workload
returns nonzero. Hard-killed Python children may not execute their exit handler
and may supply no folded file. An outer process-tree watchdog is still required
for long host commands; this tool is not that watchdog.

`correctness=NOT_CHECKED` is intentional. A profiler proves neither success nor
correct output. The watchdog's `COMPLETE` means it finished observing the
command: always inspect `returncode`. Require the original downstream operation,
output hash or semantic oracle, and execution of an emitted program before
accepting a speed improvement. Decode counts alone are inadequate: the rejected
PIDX candidate decoded rapidly but subsequently failed in `publish_value_type_id`.

Use the existing `run_pcc_compile_ab.py`, `run_pcc_link_ab.py` or
`run_pcc_stage_ab.py` for their supported scope. Separate instrumented diagnosis
from uninstrumented alternating measurements. A component's sample share is not
its whole-Stage2 share or a whole-build Amdahl bound.

The GC0 worker envelopes in `tests/data/` are dated calibration corpora. Their
tests prove that today's floor covers those recorded RSS/footprint peaks with
the stated margin; they cannot bound a changed compiler, source closure, GC or
runtime, and they prove no Stage2 speedup. Before refreshing an envelope, bind
the compiler, source, runtime, input hashes and effective options to a retained
receipt; replay every frozen worker with the existing worker tools, require
byte-identical outputs, and keep the system high-water measurements. Record the
replay command and receipt location beside the experiment so another agent can
regenerate the fixture rather than copying unexplained numbers.

## Pass and memory diagnostics

`scripts/pcc_passes_explain.py --telemetry PATH --format json` reads existing
`PCC_PYTHON_IR_PASS_TELEMETRY_PATH` JSONL. It preserves module, execution order,
status, available timing/IR-byte fields and hashes the input. With no observed
pass events it says `UNKNOWN`, never fabricates `ran=true`. This telemetry is
route-specific; do not enable an external optimizer to obtain it or interpret
cache hits as newly executed passes. Actual hot-IR effects and complete native
pass instrumentation remain separate work.

For memory, distinguish RSS, allocator capacity, live requested bytes, object
counts and retained ownership. `malloc_history` commonly sees arena refills,
not individual pcc objects. Existing `gc.get_referrers` inspects tracked objects;
it is not a complete root/stack or raw-allocation ownership graph. Use a bounded
object probe and generated retain/release IR before naming a leak owner.

## Current limits

Native capture still selects one PID (or follows the busiest leaf); it is not
a synchronized whole-process-tree CPU profile. Automatic critical-path
reconstruction, PC-to-source-operation attribution and complete retained-owner
paths are not implemented by this update. Do not represent them as available.

## Regression checks

```sh
gtimeout 60s env -u LC_ALL uv run pytest -x -n0 -vv --tb=short \
  tests/python/test_pcc_flamegraph_tool.py tests/c/test_passes_explain_script.py
```

The corpus includes a real failing host subprocess whose samples and stderr
must survive, offline CLI execution, recursive attribution, binary replacement,
partial native capture, exact PID selection and observed pass status/order.
