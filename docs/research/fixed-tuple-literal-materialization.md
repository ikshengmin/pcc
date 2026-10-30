# Fixed synchronous tuple operands

## Decision and scope

At base 206553ae8732c91b35ed9d022a0708863cad41fe, ordinary tuple operands in
CallObjectLoweringMixin._emit_slot_call_operand used a temporary list even
without splats. The existing _emit_slot_call_args_tuple already constructs
bounded fixed argument tuples without that list. Reuse it for **1–8 element
synchronous TupleExpr operands**, with no new runtime primitive or ABI.

This is a source-level engineering deduction, not a result asserted by the
user's supplied papers. [PerfR](https://arxiv.org/html/2610.23044v1),
[Zipp](https://www.zipp.org/journal/python-at-1-75x-cpython-and-v0-0-21/), and
[Trident](https://arxiv.org/html/2609.37241v1) motivated checking local
intermediate work and validating a narrowly guarded rewrite. Their speedup
figures are not evidence about PCC.

Unchanged: empty tuples, >8 elements (including ten-argument cases), list
literals, splats, generator/resume and suspending operands, tuple identity,
arbitrary-precision values, runtime layouts, ownership/collector protocols,
and public call ABIs. The helper sends any splat back to the original
streaming path. No JIT, LLVM dependency, escape analysis, or GC redesign is
introduced. This is not a claim that every tuple-literal lowering was changed.

## Ownership and error behavior

- Evaluate elements once, left to right, before allocating the result.
- Each captured value remains in an independent registered owning slot.
  Rebinding a source during later evaluation cannot change an earlier element.
- Publish the tuple in its empty result root, then use the existing retaining
  py_tuple_set_item calls and counted-lease protocol. Release input roots in
  reverse order only after stores complete.
- Reuse the existing cleanup program for user-expression errors, allocation
  boundaries and partially filled tuples. No borrowed raw pointer is reused.
- Keep the existing bound of eight: prefix error cleanup grows with retained
  operands. This optimization does not make cleanup construction unbounded.

## Evidence and limits

The representative real source is:

    def probe(capture, value):
        return capture((value, value))

These are host-emitted **static call sites**, not dynamic execution counts:

| Literal size | list_new before/after | list_append before/after | tuple_from_list before/after | all call sites before/after |
|---:|---:|---:|---:|---:|
| 1 | 1 / 0 | 1 / 0 | 1 / 0 | 184 / 160 |
| 2 | 1 / 0 | 2 / 0 | 1 / 0 | 227 / 201 |
| 8 | 1 / 0 | 8 / 0 | 1 / 0 | 485 / 489 |
| 9 | 1 / 1 | 9 / 9 | 1 / 1 | 528 / 528 |

The eight-element case has four more total static call sites from prefix
cleanup even though it removes the intermediate list and conversion. Do not
present every IR metric as improved. No end-to-end timing, native throughput,
RSS, allocation telemetry, or five-GC qualification was measured. No
"12 containers to 6" claim follows from this scoped experiment.

On 2026-10-24, the six-file focused host selection below passed **123 tests**,
including **30 new tuple-literal checks**. The existing generator operand
suite passed **66 tests**, for **189 host checks** in total. The two-element
new shape test fails against exact old production code, which emits no
direct py_tuple_new for that literal.

The tests cover 1–8 elements; unchanged empty/wide/splat/list/generator cases;
normal dynamic-call and module paths; real IR and precise-stack-map
verification; aliasing/rebinding; left-to-right evaluation; seven modeled
failure boundaries; cleanup lifetime; and a CPython reference program with
weakrefs, finalizers and a user exception. Models and reference execution do
not establish native runtime semantics.

Execution used a partial source snapshot whose **288 imported source files**
were checked against base Git blobs, except the identified changes. Actual
host interpreter: **CPython 3.15.0rc1**, pytest 9.1.1, pytest-xdist 3.8.0.
The repository selects final 3.15.0, so this is not a final-3.15.0 host receipt.
Repository addopts were unchanged: -n 6 --dist=loadgroup -m 'not integration'.
The --confcutdir=tests/python option excluded unrelated root fixture setup.
Snapshot runs used uv run --offline --no-project --python with the available
host and a 100-second process-group timeout; generator checks used 80 seconds.

Reproduce the focused host scope in a complete checkout with the selected
host interpreter:

    timeout --signal=TERM --kill-after=5s 100s uv run pytest -x -vv --tb=short \
      --confcutdir=tests/python \
      tests/python/test_fixed_tuple_literal.py \
      tests/python/test_fixed_call_argument_tuple.py \
      tests/python/test_slot_call_operand_roots.py \
      tests/python/test_slot_call_lexical_roots.py \
      tests/python/test_slot_call_cleanup_reuse.py \
      tests/python/test_slot_call_exception_cleanup.py
    timeout --signal=TERM --kill-after=5s 80s uv run pytest -x -vv --tb=short \
      --confcutdir=tests/python tests/python/test_generator_operand_root_groups.py

## Remaining qualification

Native builds/execution were excluded from this task. The integration test
test_fixed_tuple_literal_native is provided for an independently authorized
run with an existing source-matched owned runtime and pcc0/pcc1 compiler.
It never provisions an archive. Native allocation-failure injection, the
five-GC runtime gate, final-3.15.0 host rerun, native performance comparison
and bootstrap fixed-point qualification remain open. Existing broad PCC
goals are not closed by this local change.
