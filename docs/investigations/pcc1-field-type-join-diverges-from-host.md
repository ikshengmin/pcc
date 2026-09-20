# Investigation: pcc1 infers `NoneType` for a field the host compiler infers as sliceable

**Status: both localized divergences fixed; new Stage1 and scoped host/native
execution pass. Full Stage2/Stage3 remains open (2026-09-19).**

## Symptom

`scripts/bootstrap.sh --backend self --stage 2 --reuse-stage1` runs to
completion of its frontend phase (2404.8 s) and then fails:

```
pcc frontend worker failed: Exception: codegen[pcc.ply.lex]:
  NotImplementedError: Layer 1 slice on type NoneType not supported
error: PCC-PY-COMPILE-001: [python-frontend] parallel frontend codegen worker failed
```

Stage1 compiles the same module without complaint: `pcc.ply.lex` is in the
Stage1 closure (`_pcc_py_module_top_pcc_ply_lex`, `user_pcc_ply_lex_lex`), and
the host compiler also compiles `pcc/ply/lex.py` standalone with rc=0. So this
is not a missing capability. The compiled compiler and the compiler it was
compiled from disagree about a type.

## Reproducer

Ten seconds, no bootstrap:

```python
class Lexer:
    def __init__(self) -> None:
        self.lexdata = None
        self.lexpos = 0

    def input(self, s: str) -> None:
        self.lexdata = s
        self.lexpos = 0

    def tail(self) -> str:
        return self.lexdata[self.lexpos:]


def main() -> None:
    lx = Lexer()
    lx.input("hello world")
    lx.lexpos = 6
    print(lx.tail())


main()
```

```
host pcc   rc=0   prints "world"
pcc1       rc=1   error: Layer 1 slice on type NoneType not supported
```

This is `ply/lex.py`'s shape exactly: `self.lexdata = None` in `__init__`
(line 128), assigned a string in `input()`, sliced in `token()` as
`self.lexdata[lexpos:]` (line 376, and `lexdata[lexpos:]` at 359/385/392).

The host joins the field's assignments across methods -- `None` in `__init__`
and `str` in `input` -- and gets something sliceable. pcc1 keeps only
`NoneType`.

## What this is not

Not the ownership class that blocked Stage1 earlier in this series (the
destructured-literal retain, the double-freed cell, the type-tag-500 refcount
guard). Those were the runtime behaving wrongly and were reproducible under
the host compiler. This one is the *compiled compiler* behaving differently
from the same source run under CPython, which is the bootstrap fixed-point
property itself.

## Next

Find where field types are joined across methods, and what makes that join
produce a different answer once the compiler is compiled. Candidates worth
checking in order, cheapest first:

1. the join iterates a `set`/`dict` whose order or membership differs under
   pcc's runtime, so the last write wins differently;
2. the second assignment is not collected at all -- e.g. the walk that finds
   `self.x = ...` sites misses the one in `input` when compiled;
3. the join exists but its result is discarded for a field whose first
   observed type is `NoneType`.

Both arms of the reproducer can be dumped with `--emit-llvm` and compared,
and the inference itself can be instrumented in the host and then in a
rebuilt pcc1.


## Resolved: the first divergence was a mis-lowered `isinstance`

`PCC_DEBUG_FIELD_INFER=1` run against both compilers narrowed it to one line of
trace output:

```
host:  FIELD match lexdata NoneType NoneType True   -> append lexdata DynType
pcc1:  FIELD match lexdata NoneType NoneType False  -> skip lexdata
```

Both see the same types; only the trailing boolean differs, and that boolean is
`isinstance(known_field_ty, NoneType)` (`type_infer.py:4122`). A two-module
reproducer showed the *host-compiled* binary answering `False` as well, so the
defect was never in pcc1: it is in lowering `isinstance(x, NoneType)` when
`NoneType` is a user class.

The builtin tag tables map the bare name `NoneType` to `PY_TYPE_NONE`. A guard
in `isinstance_lowering.py` already existed to let a user class of that name
win, but it recognised the binding only through `env` or `_module_globals`, and
`from py_ast import NoneType` uses neither -- so the guard missed precisely the
case its own comment described (`PCC_DEBUG_ISINSTANCE_SHADOW` confirmed
`in_classes=True, in_env=False, in_globals=False`).

Under CPython that `isinstance` line is real Python and answers `True`, so the
host widened `self.lexdata` to `DynType` and pcc1 left it `NoneType`. The
divergence was produced entirely by this one lowering.

Fix: `_name_is_imported_into_module` extends the guard to `Import` /
`ImportFrom` bindings (including `import x as N`). Regression:
`tests/python/test_isinstance_imported_type_name_shadows_builtin.py`; the
builtin meanings are pinned in the same test. Verified end to end: a rebuilt
pcc1 agrees with CPython on the reproducer and compiles `pcc/ply/lex.py` rc=0.

## Next divergence: a parenthesized multi-item `with`

Stage2 then ran 2556 s and failed further along:

```
codegen[pcc.package.acquire]: NotImplementedError:
  Layer 1 native with: as-clause must be a bare name
```

Same verification: `pcc.package.acquire` is in the stage1 closure (108
occurrences), the host compiles it rc=0, the rebuilt pcc1 rc=1.

A temporary trace at `async_with_lowering.py:380` in the host shows exactly one
statement reaching the native-with path, and the host sees its as-clause as a
`Name`:

```
NATIVEWITH acquire.py line=513 type=Name
```

Line 513 is a **parenthesized two-item** `with`:

```python
with (
    urllib.request.urlopen(request, timeout=timeout) as response,
    temp_path.open("wb") as out,
):
```

Note that `_emit_native_with` reads only `stmt.items[0]`. Each of the five
single-item `with` statements in that file compiles under pcc1 on its own, and
so do isolated reproductions of each manager (`path.open`, `TemporaryDirectory`,
`ZipFile`, `tarfile.open`, `urlopen`), so the trigger is the parenthesized
multi-item form itself -- the compiled parser or lifter appears to produce a
different `items[0]` shape for it than CPython-run pcc does.

Cheapest next step: have the error carry its span (it currently names neither
file nor line, which is what made this cost a bisect), then compile a
parenthesized two-item `with` with both compilers and compare the lifted AST.

## Correction — 2026-09-19: the first target is freed during type inference

The parentheses hypothesis above is **denied**: two managers fail with or
without parentheses; one manager passes. A diagnostic compiler reports
`Call tuple` for the first item and `Call Name` for the second **on entry** to
`_emit_with`, before its nesting rewrite. The error now includes source
file/line/column and the actual target type.

A compiled `parse_and_lift` / `infer_module` probe separates the boundaries:

```
                         host             compiled probe
parsed with targets      Name, Name       Name, Name
typed with targets       Name, Name       tuple, Name
original after inference Name, Name       Name, Name
```

Tracing the `With` arm of `_infer_stmt` shows both `new_as` values are `Name`
before insertion. The first changes to `tuple` during the second iteration.
This is not a missing `with` capability or a parser disagreement.

### Smallest ordinary-program failure [CONFIRMED]

```python
class Box:
    def __init__(self, value):
        self.value = value

def copy_box(box) -> Box:
    return Box(box.value)

def main():
    result = []
    for source in [Box(1), Box(2)]:
        if source is not None:
            value = copy_box(source)
        else:
            value = None
        result.append((value,))
    print(isinstance(result[0][0], Box), isinstance(result[1][0], Box))

main()
```

CPython prints `True True`; the pre-fix host-emitted binary prints
`False True`. The container use is inferred as `NoneType`, while the local
slot holds a borrowed object pointer. `marshal_to_object` passes that pointer
through. `_container_store_temp_needs_release` nevertheless classifies it as
a fresh scalar box and emits a release after `py_tuple_set_item`. Rebinding
the local releases it again, freeing the container's still-referenced object.
The next tuple allocation reuses the cell. The actual compiler IR exhibits
the same release of the borrowed `new_as` load.

The ownership predicate already checks pointer slots for integer names. Extend
that representation check to the other scalar semantic types whose pointer
representation is also passed through by `marshal_to_object`. Raw scalar
slots still require balancing their fresh boxes. No parser change or rewrite
of `acquire.py` is involved. This fixes the invalid release; it does not claim
to improve the precision of the nullable branch type join.

Regression: `test_nullable_local_survives_container_store_and_reassignment`
in `tests/python/test_container_emitted_ownership.py`. It fails before the
fix, then passes for tuple/list/dict/append stores, including rebinding through
`None`, under all five GC backends. The focused host run reports **23 passed**,
covering those stores, temporary cleanup/error paths, borrowed bignums and
context-manager exits. Native cases are explicitly collected as integration
tests; passing host cases does not certify them.

### Reproduction identities and diagnostic build limits

Evidence root: `/private/tmp/pcc-with-stage2-i8tfiwzr`. HEAD is `7f48f89d`
plus the pre-existing uncommitted work; its initial source manifest digest is
`1ae5d7c835cd117e24b5c3402c9f17677132f2d9e66730e745b76e716177bac1`.
Original pcc1 SHA-256:
`a51ca043a72163f7e8b43e70299732113e8c49443355eae56f9673d11bf97347`.
Initial runtime inventory SHA-256:
`10aff42d1111d0e720f819f37576b68a1789281c38464f6f028574fb658be779`.
Correction on resumption: the diagnostic and fixed Stage1 builds actually
consumed the later frozen archive
`d8f82e49001889450c2d6a7a084397889e0f638e609fec7a9a0ed9932a3aeef6`,
as recorded in their bundle/build receipts and confirmed by hashing both
archives. The initial inventory hash must not identify those builds or their
native regression runs.

`repro-commands.json` records host/native commands and return codes; both use
`--backend self --python-libpython off --emit-llvm=PATH`, scaffold on,
`PCC_PYTHON_IR_PASSES=off`, frontend jobs 1. `infer.host.stdout`,
`infer.native.stdout`, `infer.trace.stdout`, `infer_stmt.ll` and
`multi.diagnostic.stderr` preserve the AST and ownership observations.
`nullable-red.*`, `nullable-green.*` and `ownership-focused.*` retain test
logs, node reports, watchdog results and tree RSS samples.

The initial **diagnostic** Stage1 attempt used the stage-build helper's cold
cache defaults without direct indexed emission: 1110 ASM shards. Its sampler
stopped it at 416.95 s because `ps` exceeded the mandatory one-second deadline,
with peak tree RSS 3,008,708,608 bytes. That is `SAMPLER_ERROR`, not a compiler
failure or a successful Stage1. Its owned children were confirmed gone.
516 completed shards were retained; the remaining 594 were emitted from the
same saved IR. The first ad-hoc link manifest omitted its record count and
was rejected; adding the count passed the existing manifest validator.
The owned linker then succeeded in 204.08 s (131.08 s assembling ASM), yielding
diagnostic pcc1 SHA-256
`3f959f94081a96753ca2e287e6da4edcc616477e5799f12f32160e28713993b9`.
This recovered compiler carries temporary tracing and is **not** a qualified
Stage1 receipt. Selecting that costly diagnostic pipeline before checking its
effective settings was an investigation error, not a measured regression in
the normal bootstrap pipeline.

The clean fix is frozen at source digest
`463a3eb4f8feb7921adac16bcf444b60403a0379cf6340825a0ba7df2da30f73`.
Its new build explicitly selects direct indexed PCO emission, zero fallback,
IR passes off, frontend jobs 2, backend jobs 6, 600 s build / 90 s smoke limits,
and an 8 GiB process-tree cap.

### Final scoped verification — 2026-09-19

`fixed-stage1/manifest.json` and `build-receipt.json` report **SUCCEEDED**.
The cold build took 593.45 s, emitted 391 PCO inputs with no ASM inputs, and
passed the native function compile/run smoke (stdout `42`). The new compiler
SHA-256 is
`0083afb12755dda051a0d0d04f2f3f3a450e41bb974c0e0fc802a5f3eae3358d`;
linkage inspection lists only libSystem. It remains an isolated artifact at
`/private/tmp/pcc-with-stage2-i8tfiwzr/fixed-stage1/pcc1`.

The same binary compiles the unchanged `pcc/package/acquire.py` to IR in
3.184 s, rc=0, with host compiler/interpreter helpers disabled and ownership
audit mode 3 plus known-reference checks enabled. `acquire.fixed.receipt.json`
records its command and hashes; `acquire.fixed.stderr` is empty. The original
module-level blocker is cleared, but an IR compile is not execution of the
module's network/download functionality.

`native-regression.stdout` reports **7 passed** in 169.32 s, with the fixed
pcc1 explicitly selected through `PCC_CURRENT_PCC1`. These are real `-o`
compiles followed by emitted execution: four nullable container store forms,
both multi-manager `with` spellings and context exit/exception ordering. Every
program ran under GC0–GC4, for 35 native-compiled executions. Host helpers are
disabled by the native compiler fixture. Together with the **23 passed** host
checks, these qualify the localized fix, not the whole toolchain.

At the maintainer's request, Stage1 stability/performance and complete Stage2
are separate **unfinished** goals. One successful cold Stage1 is not repeated
qualification or a performance target met. A complete new-source pcc1→pcc2
run, pcc2 emitted execution, pcc2→pcc3 fixed-point comparison, five-GC bootstrap
qualification and the broader performance goals remain open. This round did
not launch the approximately 40-minute full Stage2 scenario.
