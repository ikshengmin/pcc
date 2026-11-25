# Borrowed local operands of slot calls

Item 3 (c) J2 of [the work order](../status.md). Design for review; not
implemented. Claude, 2026-11-26.

## What it costs now

A runtime "slot call" (`py_obj_str`, `py_obj_add`, attribute reads and the
other runtime operations that code generation reaches through
`_emit_slot_call_operand`) receives each operand through a root slot. For an
operand that is a local variable, code generation copies the local into a new
owning root (`pcc_gc_root_copy_local`, or `pcc_gc_store_root` in a resumable
body), calls the operation and clears the root again (`pcc_gc_store_root(slot,
null)`): a retain, a release and two runtime calls for a value the local
already owns.

Emitted IR of `s = str(a) + b; t = s + "!"` (host pcc0, 2026-11-26):

* in an ordinary function, 16 `pcc_gc_store_root(..., null)` calls, and on
  the normal path a root copy before each operation and a clear after it;
* the same two statements in a generator body, 66 `pcc_gc_store_root` calls.

On the handler (local Mac, 2026-11-26) `pcc_gc_store_root` runs 304 times per
request and its self time is 1.3 us of 17.3 us. Its largest callers are the
request's generator body (0.40 us, about 88 calls per request: operand roots
filled and cleared, 27 clears of slots that were never filled) and
`json._encode_object` (0.16 us).

## What the code does today

* `_emit_slot_call_operand(expr, label, borrow_stable=...)`
  (`call_object_lowering.py`): with `borrow_stable`, a parameter of an
  ordinary function whose binding never changes and that is not a boxed
  closure capture is passed as its own slot
  (`stable_parameter_operand_source`). Every other operand gets an owning
  root.
* `direct_call_argument_reads_stable_root`: a direct call of a compiled user
  function already passes a rooted local or parameter of an ordinary program
  function without a pin or a copy. Module globals, CPython values, boxed
  captures, resumable bodies and runtime library code are excluded.
* Resumable bodies (generators, `may_park` functions): every operand root is
  enrolled in the heap frame (`_new_slot_call_root`), because an expression
  may suspend in the middle and its operands must then survive in the frame.

## Proposal

Step 1, ordinary functions. A slot-call operand is passed as the local's own
slot, with no copy and no clear, when all of these hold:

1. the operand is a `Name` that `direct_call_argument_reads_stable_root`
   accepts (a rooted local or parameter of an ordinary program function; not
   a module global, a CPython value or a boxed capture);
2. the runtime operation does not consume that input (a per-operation table;
   an operation that steals a reference keeps the owning copy);
3. no part of the same call's argument evaluation can rebind the name: the
   call's argument expressions contain no assignment expression (`:=`) to it.

Step 2, resumable bodies. The same, for a local of a generator or `may_park`
frame, when neither the operands' evaluation nor the operation can suspend:
no `yield` or `await` in the expression, and no call that
`vthread_effect_analysis` marks `may_park`. Operands of an expression that can
suspend keep their heap-frame roots.

Separately, a clear of a slot that code generation knows is empty on that
path (never filled, or already moved out) is not emitted.

## Why nothing observable changes

The object stays alive because the local holds its reference until the call
returns, and nothing else can rebind that local during the call:

* only the running activation writes its non-cell locals; a nested function
  that assigns the name needs a cell, which is excluded;
* pcc frames cannot be written from outside: a traceback frame's `f_locals`
  is a fresh empty dict and pcc has no `sys._getframe`; `exec` and `locals()`
  in a function do not write fast locals;
* another thread cannot reach this activation's locals, and a running
  generator cannot be re-entered;
* condition 3 excludes the only in-expression rebinding (`:=`).

Ownership and timing stay the same: no reference is taken, so none is
released on the normal or the error path, and the object dies when the local
is rebound or the activation ends, as before. Finalizers and weakref
callbacks run at the same points. The local's slot is already an owning root,
so the cycle collector sees the same counts. CPython 3.14 relies on the same
argument when its compiler loads a local as a borrowed reference.

## Checked before implementation (2026-11-26)

1. Consumption. `_slot_call_runtime_call` loads each operand from its root and
   passes the raw value; the caller keeps the root until after the call and
   clears it itself, so no slot-call operation consumes an input, and the
   protocol already accepts borrowed roots (unchanged parameters) for every
   operation: `_release_slot_call_roots` and the cleanup blocks skip them,
   a method receiver is copied instead of moved, `py_list_append_from_slots`
   receives a borrowed mask. One consumer treats the registry as more than
   that: `_stable_parameter_exact_list_guard` (list_method_lowering.py)
   memoizes "is an exact list" for the rest of the function when the
   receiver is registered as borrowed, which holds only for a parameter that
   is never rebound. A borrowed local must therefore not enter
   `_slot_call_borrowed_operands` as it is; it gets its own registry (or the
   guard checks parameter-ness itself), and a test rebinds a local from a
   list to another type after a guarded append.
2. Frame writes. Compiled with host pcc0 and run without libpython: a
   function that writes `locals()`, writes `vars()`, calls `sys.settrace`
   or `sys._getframe` is unavailable (NotImplementedError at its call);
   `exec` is not defined (NameError). CPython 3.15 does write a caller's
   local through `sys._getframe(1).f_locals`. A test pins that pcc has none
   of these, and names this note: adding any of them requires a gate here.
3. Generator re-entrance. pcc does not raise "generator already executing":
   a generator that calls `next` on itself recursed into RecursionError, and
   four threads driving one generator never raised ValueError (CPython does
   both). Step 2 (resumable bodies) waits until that is fixed; step 1 does
   not depend on it.
4. Dynamic calls that park. `vt.spawn` of a function whose dynamically
   dispatched method may park fails to compile ("cannot prove a resumable
   parking boundary ... unresolved user-method may park"), unless the worker
   stays on a pinned carrier, where a park blocks the native stack and every
   operand stays valid.

## Tests before the change

* IR-shape tests: no root copy and no clear for a qualifying operand; the
  copy stays for a module global, a boxed capture, a `:=` in a later
  argument, a consuming operation and an operand that crosses a suspension.
* Executed programs compared with CPython's output: dunder methods
  (`__add__`, `__str__`, `__getattr__`) that rebind globals, mutate
  containers or raise; `__del__` and weakref callbacks that print their
  order; a generator with `yield` inside a binary expression; a virtual
  thread that parks inside the called operation; threads sharing objects.
* The container-lock, shared-container and virtual-thread suites run for J1,
  and the Python code-generation test files that name the edited modules.

## Measurement

`pcc_perf.py instr` (instructions and cycles per handler request) against a
base and a control arm; `prim --against` on call, method, attr, str_concat
and json. Ceiling, an unmeasured estimate: removing about half of the
request body's and `_encode_object`'s root traffic, 1-3% of a request.
