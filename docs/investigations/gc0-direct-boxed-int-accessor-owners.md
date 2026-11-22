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
