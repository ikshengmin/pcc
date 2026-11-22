# Direct boxed int returns: field, subscript and typed-string audit (#7)

Source and claim: `b89a27b19c635ec8977795b2151f57690917a6f0`.
Linux x86-64 host pcc0, CPython 3.15.0, source-matched threaded atomic runtime
archive SHA-256 `4db649b9685e59c30c84c740a90def3aad8a87988c4e497b4e3569483a004555`.
No macOS, pcc1 or performance result.

| Source case | Called in the executable input | GC0 terminal heap-int events |
| --- | --- | --- |
| Field, alias remains live | `py_instance_get_field` | one `decref(1)` |
| Field, container's old value overwritten before collection | `py_instance_get_field` | one `decref(1)` |
| List subscript, alias remains live | `py_list_getitem` | one `decref(1)` |
| List subscript, container's old value overwritten before collection | `py_list_getitem` | one `decref(1)` |
| `int(text)`, valid typed string | `py_str_utf8`, `py_int_from_cstr_or_raise` | one `decref(1)`, two frees |
| `int(text)`, invalid typed string | same parser | `ValueError`, one free, no residual |

All six pcc0 test nodes matched CPython in all five GC modes (30 executions).
The detached tests drop the original local and overwrite the field/item before
collecting, then read the result. This is a correctness baseline, not a leak
test pass: the baseline deliberately records the GC0 terminal state for the
next fix, which must assert release and preserve these ownership checks.
The emitted input confirms the direct caller and actual callee runtime helpers;
these observations do not qualify other attribute/subscript lowerings.

Reproduce with the explicitly built source-matched archive:

```sh
export PCC_RUNTIME_ARCHIVE=/absolute/path/to/libpy_runtime_pcc_py.a
uv run python scripts/run_process_tree_sample.py \
  --timeout 90 --max-tree-rss-bytes 1073741824 \
  --linux-physical-preflight-reserve-bytes 7516192768 \
  --result result.json --samples samples.tsv --stdout nodes.log --stderr errors.log \
  -- uv run pytest -x -v -m integration --basetemp=./audit-receipts \
  tests/python/test_direct_boxed_int_accessor_owner_audit.py
```

The successful guarded invocation took 13.55 s with 562,286,592 B peak process
tree RSS and six xdist workers. The first harness attempt failed to select a
marker from the indexed IR before any native test run; the successful
attempt selected the unique caller/callee module and used the report's
source-hash binding instead.

## Nonzero literal list indices

On claim `feb1e468`, both `[1]` and `[-1]` selected `py_list_getitem` in
the linked input. Each had one GC0 `decref(1)` in the original-owner and
overwritten-owner form, despite matching CPython behavior across GC0–4.
With the bounded literal-index proof, all four reach `free(0)`. Negative
integer syntax here is a unary minus over an integer literal. Expressions
such as `1 + 0` remain outside the proof; no dynamic index, tuple, dict,
cross-module or foreign ABI conclusion follows. The native matrix was
28 cases, 52.04 s and 711,073,792 B peak process-tree RSS; 26 related IR
controls passed. The previous archive guard and reproduction command apply.

## Dynamic single-argument conversion

On claim `3dd6a0e3`, linked input selected `py_obj_as_int_object_args` for
`return int(value)` with a sole `value: object` formal. A string yielding
a large int and an existing heap-int alias each left one GC0 `decref(1)`.
The invalid string and `None` raised `ValueError` and `TypeError`
without a residual. The candidate now consumes that one returned owner.
Native callback variants also collect inside `__int__` and `__index__`
before returning; retain/release and both exceptions are checked.

The unchanged guards admitted 17 accessor/conversion cases in 34.18 s
(566,546,432 B tree RSS) and 21 adjacent return cases in 65.63 s
(619,249,664 B). Each case compares pcc0 on five collectors to CPython.
There were also 27 adjacent IR controls, including the actual conversion
helper, its lease acquire/release and its error path. No Mac or pcc1.

## Indexed-list ownership follows the selected accessor

Runtime positive and negative indices in an ordinary native
`list[int]` return were a new concrete witness. The actual linked
callee calls `py_list_getitem` for both; Python identity and values
matched CPython across five collectors, but each left one GC0
`decref(1)`. The ordinary out-of-range control raised `IndexError`.

The owning result rule is now shared with the emitter's indexed list
accessor selection. Its proof comes from the native getter, the
receiver's actual local pointer formal and ordinary positional
signature, not the source spelling or count of literals. The caller
consumes that NEW reference once. A computed index inside the callee
is also covered. Neither the list indexing/error algorithm nor the
foreign/CPython or machine-int boundaries change. The Linux pcc0
accessor file passed 21 cases across all five GC modes and CPython,
and 29 adjacent ownership IR checks passed. Negative detached and
positive alias cases reached GC0 `free(0)`. No Mac/pcc1 run, no
general tuple/dict or arbitrary return-annotation claim.

## Homogeneous native tuple indices

The actual linked callee for `tuple[int, int]` calls `py_tuple_getitem`.
Its success path retains the selected element before returning it.
The runtime-index alias and negative detached-owner cases each matched
CPython across GC0–4 but left one GC0 `decref(1)` before the fix.
With the shared accessor provenance, each instead reaches `free(0)`;
the detached boxed value remains intact after releasing the tuple and
calling `gc.collect()`. An out-of-range index still raises `IndexError`.
The scoped Linux pcc0 matrix passed 24 native cases on all five modes
against CPython and 36 IR controls. A heterogeneous tuple deliberately
does not acquire the homogeneous rule. No dict, machine-int or foreign
ABI conclusion, macOS/pcc1 run or throughput claim.

## Typed native dictionary lookup

The linked producer was `py_dict_getitem`; the locked fast path
retains into its result slot, and the rooted fallback calls
`py_incref` on the selected value. With the previous compiler both
successful witnesses matched CPython across GC0–4, but GC0 ended the
value's trace at `decref(1)`; the missing-key control already passed.
The shared accessor selection now also covers typed `dict[int, int]`.
Both witnesses reach `free(0)`. A separately boxed key exercises
the rooted lookup and a missing key still raises `KeyError`. The
IR pins the result during owned-key cleanup and releases that key
on the error edge without publishing a result. Linux pcc0 passed
28 native cases on GC0–4 against CPython and 43 IR checks.
No arbitrary key ABI or generic return-annotation claim, Mac or
pcc1 run, or throughput evidence.
