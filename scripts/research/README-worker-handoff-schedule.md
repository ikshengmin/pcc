# Frozen worker-handoff scheduling research

This is a reproducible **research model**, not a production scheduler change,
an empirical speedup, or evidence of a Stage2/3 fixed point. Do not replace
memory forecasts, raise a tree-RSS cap, change timeouts, or omit CI tests on its
authority. This model changes no compiler, worker-pool, or CI behavior.

## Run without starting a compiler

Use an already provisioned host Python with the project test dependencies.
Set `PYTHON` to that interpreter; these commands do not synchronize or build the project.
Keep the repository default pytest xdist configuration.

```sh
"$PYTHON" scripts/research/replay_worker_handoff_schedule.py \
  --fixture scripts/research/fixtures/worker_handoff_head_of_line.json
"$PYTHON" scripts/research/replay_worker_handoff_schedule.py \
  --artifact /path/to/existing-bootstrap-mac.zip
"$PYTHON" -m pytest -x -vv --tb=short tests/test_worker_handoff_schedule_replay.py
```

The artifact route reads only its existing `worker-prior-gate/stage1.json` and
`worker-prior-gate/stage1.log`. It does not download files or start processes.
The default three workers/slots match the measured Mac run, not every target.
Other logs without the indexed FE/BE topology are rejected. The printed source
commit comes from the receipt; it does not establish the current source tree.

Each producer holds its own slot until its dependent consumer finishes. At
most three tasks and three outstanding slots exist, and the sum of frozen
reservations cannot exceed the same available-byte allowance under either
policy. `largest` chooses the largest fitting ready task. `drain` prefers a
fitting ready consumer, then uses the same size order. Neither changes task
durations or omits completed work. The small fixture and tests include a tie
and a counterexample where consumer preference makes the result worse.

## Existing Stage1 evidence and decision

Measured source: `ae3dcaa7711af8f9dba399d95d06ac650052572b`, 483/483 modules on
each target. Windows subsequently completed Stage1 at 2846.656016 s, average
workers 3.787172/4, with entry wall 5490.047300 s. Its 605 completed worker
attempts have no cancel/calibrate events; its later native-prior timeout is not
a Stage1 build failure. The Windows artifact ID is `12569462307`.
The existing Mac Actions artifact ID is `12567648740`; the Linux
x86 and ARM artifact IDs are `12568705202` and `12567182802` respectively.

- Mac compile wall: 3033.410 s, average workers 1.546742, target 1800 s and
  average workers at least two. The indexed FE/BE span is 2420.993130 s:
  zero workers 22.514422 s, one 669.152594 s, two 1269.413837 s, three
  459.912277 s. These are wall intervals, not sums of nested phase timers.
- Linux x86 compile wall: 3276.495971 s, average workers 3.836112/4. ARM:
  1464.386668 s, average workers 3.483533/4. Their codegen is not the Mac
  split-handoff topology. Low worker utilization does not explain x86's
  dominant cost; ELF link wall is 92.461280 s (x86) and 161.381617 s (ARM).
- Mac's independently measured serial Mach-O link is 527.604 s. Non-overlapping
  event spans include input detach 75.865 s, stackmap merge 102.513 s, source
  view construction 140.319 s, relocations 64.433 s, and final stackmap payload
  validation 52.187 s. These spans identify owners, not function-level causes.

For the Mac model, both policies use 966 successful FE/BE tasks and the same
2362818560 available bytes, observed on 962 of their starts. Four early starts
instead observed 2354692096 bytes. The underlying process-tree guard selected
2849456128 bytes; the outer preflight's 2869575680 bytes was not its final cap.

With each task's original successful-admission reservation frozen, modeled
wall is 2550.393170 s for largest-first and 2230.188133 s for consumer preference
(difference 320.205037 s). With each reservation raised, if necessary, to that
task's completed padded peak, the results are 2278.441941 s and 2242.843567 s
(difference only 35.598374 s). **Neither model reproduces the actual baseline.**
The variation is material, so this evidence does not justify a production
priority change or a claim that the 1800 s target is close.

## Missing evidence and next decision

- Successful durations are frozen despite different CPU competition and I/O.
- Completion-driven class calibration and live reservation growth are not
  replayed. The completed-peak version deliberately front-loads measured
  growth as a sensitivity check, not as a proposed forecasting algorithm.
- Two cancelled attempts (6.731297 worker-seconds) are reported but excluded;
  their successful exclusive retries retain their observed reservations.
  Changing order can change cancellation and retry decisions.
- Launch, polling, seal verification, and cleanup delay are not modeled.
- Existing `samples.tsv` stores tree totals and only the largest process,
  not all synchronized worker RSS histories. The final worker-state file is
  not a time series. Those files cannot establish every task's growth path.

Stop at this negative decision unless new, authorized evidence can calibrate
the baseline and reproduce a benefit with the same resource and CI contracts.
For the separate source-view bottleneck, the measured ae3 call chain was:
`macho_host_prepare._logical_staged_rows` calls
`macho_link._iter_folded_relocation_rows`, which builds the complete
list through `_folded_relocation_rows` before yielding. A host-only bounded
consumer must preserve canonical companion/name validation, error precedence,
ownership, and native behavior before it is a viable implementation change.

The separate [host implementation commit](https://github.com/ikshengmin/pcc/commit/64d125da33f5ca8d9208d45989f7667a5717ec25)
is saved independently while its normal-CI publication is queued; this research-only
commit does not apply that production change. That implementation batches replay only after the existing
whole-section canonical count succeeds on the same immutable bytes. Batches
hold 256 raw rows, or 257 when needed to keep a companion pair together, and
still use the canonical folder. Its extra scratch is admitted, not omitted.
Standalone eager folding, native folding, and the full semantic validators
remain unchanged. In particular, a special-section validator may retain rows
in its own section-wide index; this bounds the producer's temporary graph,
not every validator's memory.

`tests/python/test_macho_staged_row_batches.py` covers companion boundaries,
bounded producer materialization, large-section final-image parity, first-error
ordering across batches, early close, allocation exceptions, and admission
refusal cleanup. These host checks do not establish a Stage1 wall-time gain,
native execution, or Stage2/3 qualification. The 140.319 s source-view event is
an owner measurement, not time attributed entirely to row materialization.
