# Measured AArch64 assembler reuse (experimental, inactive)

`owner.py` contains a one-shot owner around the existing canonical module
builder. No production module imports this package; it does not change compiler
defaults, optimize flags, scheduler reservations, or resource limits.

The optimized Darwin PCO route currently parses/finalizes assembly to obtain
metadata offsets, discards that builder, then parses the same prefix again at
section assembly. This experiment retains the first builder, consumes its
private input containers, appends only the non-text metadata suffix, and calls
the original finish. It uses the existing idempotent layout cache, not a second
offset calculator. Public mutable/repeatable transports remain unchanged.

## Preconditions and memory HOLD

The caller must exclusively own the prefix list, encoded arena and symbol-name
table. Once measured, no instruction, text label/alignment, data-in-code marker
or symbol-name binding may change. Only non-text metadata can follow.
`release_prefix` consumes the private line list/arena; never pass a public or
shared transport to it. Keep the owner in a `try/finally` calling `close`, including
exceptions while constructing metadata. Finish/close are one-shot/idempotent.

The retained builder now overlaps metadata construction. Clearing the parsed
inputs removes redundant containers, but it does **not** prove a lower peak:
builder arenas, data chunks, symbols and copied offsets remain live. Numeric
allocation/reservation coverage and timing benefits are unmeasured. Production
activation is HOLD; unchanged caps are not evidence that arbitrary inputs fit.

## Controls and current evidence

Run from the repository root, using the ordinary six-worker/loadgroup default:

```sh
python -m pytest -x experimental/aarch64_measured_assembly/test_owner.py
```

The 20 bounded host cases cover an existing pinned emitter assembly shape,
encoded records, data/unwind payloads, conditional-branch relaxation using a
small data gap, full section/relocation/undefined parity, error order, closure,
public transport mutation/reuse, and one prefix parse. They do not link or
execute native code. The packaged 20 cases passed on CPython 3.15.0rc1 with the ordinary
six-worker/loadgroup defaults, 120-second / 2-GiB limits, and unchanged
production source inputs. This qualifies only the experimental owner,
not the separate worker integration, memory reservation or performance.

A separate isolated worker prototype (not included or enabled here) had
23 host cases pass, including three fresh tiny PIDX→PCO byte comparisons;
one AST-version-sensitive test failed. Its exact source-byte correction then
passed separately. This is 23+1, not one 24-case pass, and does not qualify this
repackaging or production memory/timing. CPython was 3.15.0rc1; both gates kept
six workers, 120 seconds and 2 GiB limits. No Stage1 saving is claimed.
