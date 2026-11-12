"""Generated operand owners survive coroutine suspension and completion."""
from __future__ import annotations

import os
import re
import subprocess
import sys

import pytest

from pcc.backend.self_backend_parse import parse_self_backend_module
from pcc.backend.self_backend_verify import verify_parsed_module
from pcc.diagnostics.gc_log import parse_log_lines
from tests.python.root_slot_contract import RootSlotContract
from tests.python.test_slot_call_operand_roots import _emit


def _assert_yield_handoff_contract(module, blocks, function_name):
    """Only the resume ABI's two borrowed cells may be cleared after take.

    A physical cell, not its SSA spelling, identifies the permitted writes.
    Keep frame retirement on every CFG path and immediate await publication
    separate from the final no-call ownership transfer. This is an IR contract,
    not execution evidence for native suspension or a moving collector.
    """
    function = next(fn for fn in module.functions if fn.name == function_name)
    assert (function.ret_type.is_ptr and not function.is_vararg
            and len(function.args) == 2
            and all(arg.type.is_ptr for arg in function.args)), "invalid resume ABI"
    roots = RootSlotContract(blocks, module.globals_)
    entry = next(block for block in blocks if block.name == "entry")

    def allocation_base(value):
        # Select the borrowed allocation before decoding offsets, so unrelated
        # address expressions cannot become candidates for permitted clears.
        seen = set()
        while value in roots.aliases or value in roots.geps:
            assert value not in seen, "cyclic borrowed root address"
            seen.add(value)
            value = (roots.aliases[value] if value in roots.aliases
                     else roots.geps[value].data[3])
        return value

    publications = []
    for argument in function.args:
        stores = [(index, ins) for index, ins in enumerate(entry.instructions)
                  if ins.kind == "store" and ins.data[1] == argument.name.lstrip("%")]
        assert len(stores) == 1, "each borrowed ABI argument needs one entry publication"
        index, store = stores[0]
        assert (store.data[0].is_ptr and store.data[2].is_ptr
                and not store.is_volatile), "invalid borrowed ABI publication"
        publications.append((index, roots.slot(store.data[3])))
    borrowed = tuple(cell for _, cell in publications)
    base = borrowed[0][0]
    assert (roots._pointer_cells(base) == 2
            and borrowed == ((base, 0), (base, roots.POINTER_BYTES))), (
        "borrowed ABI arguments need distinct cells in one two-pointer entry alloca"
    )
    registrations = [(block, index, ins) for block, index, ins in roots.rows
                     if roots.called(ins, "pcc_gc_frame_enter")
                     and len(roots.args(ins)) == 2
                     and allocation_base(roots.args(ins)[1]) == base]
    assert len(registrations) == 1, "borrowed ABI pair needs one regular registration"
    block, registration_index, registration = registrations[0]
    assert (block.name == "entry" and not registration.data[3]
            and registration.data[1].is_void and not registration.data[6]
            and registration.data[5] in (0, 2)
            and all(ty.is_ptr for ty, _ in registration.data[4])
            and roots.slot(roots.args(registration)[1]) == (base, 0)), (
        "invalid borrowed registration ABI or base"
    )
    descriptor = roots.globals.get(roots.symbol(roots.args(registration)[0]).lstrip("@"))
    assert (descriptor is not None and descriptor.is_constant
            and descriptor.type.describe() == "i32"
            and descriptor.initializer == "-2"), "borrowed ABI pair needs a constant -2 map"
    for index, cell in publications:
        assert index < registration_index, "borrowed argument must be stored before entry"
        for later in entry.instructions[index + 1:registration_index]:
            if later.kind != "store" or allocation_base(later.data[3]) != base:
                continue
            _, offset = roots.slot(later.data[3])
            assert not (offset < cell[1] + roots.POINTER_BYTES
                        and cell[1] < offset + later.data[0].slot_size), (
                "borrowed ABI publication was overwritten before entry"
            )

    await_cells = set()
    for block, index, ins in roots.rows:
        if not roots.called(ins, "py_await_step"):
            continue
        following = block.instructions[index + 1:index + 2]
        assert following, "await result must be published immediately"
        store = following[0]
        assert (ins.data[1].is_ptr and store.kind == "store"
                and store.data[0].is_ptr and store.data[2].is_ptr
                and store.data[1] == ins.data[0] and not store.is_volatile), (
            "await result must be published immediately"
        )
        cell = roots.slot(store.data[3])
        roots.require_owning(cell)
        await_cells.add(cell)
    assert await_cells, "handoff needs an actual published await result"
    assert not await_cells.intersection(borrowed), "borrowed cells must not alias await owners"

    # This existing CFG ledger proves all registered frames are retired on
    # every reachable return, including paths which join before the handoff.
    returns = roots.assert_frame_exits()
    handoffs = [(block, index, ins) for block, index, ins in roots.rows
                if ins.kind == "call" and (ins.data[0] or "").startswith("gen.yield.owner")]
    assert handoffs, "resume needs a yielded-owner handoff"
    for block, index, take in handoffs:
        assert (roots.called(take, "pcc_gc_take_pinned_slot")
                and not take.data[3] and not take.data[6]
                and take.data[5] in (0, 2)
                and take.data[1].is_ptr and len(take.data[4]) == 2
                and take.data[4][0][0].is_ptr
                and take.data[4][1][0].describe() == "i64"), "invalid yielded take ABI"
        taken = roots.slot(roots.args(take)[0])
        assert taken not in borrowed, "borrowed cells must not alias the yielded owner"
        assert taken in await_cells, "yield must take the published await owner"
        assert returns.get(block.name) == {True}, "handoff must reach a retired-frame return"
        term = block.terminator
        assert (term.kind == "ret" and term.data[0].is_ptr
                and term.data[1] == take.data[0]), "return must use the exact take result"
        cleared = []
        for later in block.instructions[index + 1:]:
            assert (later.kind == "store" and not later.is_volatile
                    and later.data[0].is_ptr and later.data[2].is_ptr
                    and later.data[1] == "null"), (
                "only nonvolatile NULL borrowed clears may follow the yielded take"
            )
            cell = roots.slot(later.data[3])
            assert cell in borrowed and cell != taken, "post-take clear must target a borrowed ABI cell"
            cleared.append(cell)
        assert len(cleared) == 2 and set(cleared) == set(borrowed), (
            "handoff must clear each borrowed ABI cell exactly once"
        )


@pytest.mark.parametrize("expression", (
    "slot_operand_probe(await child())",
    "slot_args_probe(['before'], await child())",
    "slot_operand_probe((['before'], await child()))",
    "slot_operand_probe({'before': ['kept'], 'after': await child()})",
))
def test_awaited_operand_has_persistent_roots_and_valid_resume_edges(expression):
    text = _emit(
        "async def child():\n    return 42\n"
        "async def probe():\n    return " + expression + "\n"
    )
    verify_parsed_module(parse_self_backend_module(text))
    body = re.search(r"^define [^\n]*@user_slot_operand_probe__gen_resume\([^\n]*\).*?^}", text, re.M | re.S)
    assert body is not None
    body = body.group(0)
    # Suspension dispatch must restore generated argument owners as well as
    # source locals; a root only registered on the previous native stack is
    # insufficient. The yielded owner is transferred after local cleanup.
    dispatch = re.search(
        r"^gen\.dispatch:\n(.*?)(?=^[^ \n][^\n]*:\n|^})",
        body,
        re.M | re.S,
    )
    assert dispatch is not None
    restores = re.findall(r"^  %gen\.operand\.restore[^\n]* = call ptr[^\n]*", body, re.M)
    assert restores
    # Restores may form a success/scalar-fallback chain before gen.select.
    # Check every CFG path, rather than requiring one textual dispatch block.
    from tests.python.test_generator_operand_root_groups import _parsed_function
    from tests.python.test_generator_frame_take_group import assert_group_restore_contract
    module, blocks = _parsed_function(text, "user_slot_operand_probe__gen_resume")
    assert_group_restore_contract(blocks, module.globals_)
    assert "@py_tls_exc_swap_slot(" in body
    _assert_yield_handoff_contract(module, blocks, "user_slot_operand_probe__gen_resume")
    step = re.search(r"(%[^ ]+) = call ptr[^\n]*@py_await_step[^\n]*\n([^\n]*)", body)
    assert step is not None
    assert "store ptr " + step.group(1) in step.group(2)


@pytest.mark.parametrize("bulk", ("0", "1"))
def test_generated_coroutine_output_slots_start_empty(monkeypatch, bulk):
    monkeypatch.setenv("PCC_DISABLE_BULK_GENERATOR_FRAME_INIT", bulk)
    text = _emit(
        "async def child():\n    return 42\n"
        "async def probe():\n    return slot_operand_probe(await child())\n"
    )
    verify_parsed_module(parse_self_backend_module(text))
    resume = re.search(r"^define [^\n]*@user_slot_operand_probe__gen_resume\([^\n]*\).*?^}", text, re.M | re.S)
    factory = re.search(r"^define [^\n]*@user_slot_operand_probe\([^\n]*\).*?^}", text, re.M | re.S)
    assert resume is not None and factory is not None
    restored = re.findall(r"%gen.operand.restore[^ ]* = call ptr", resume.group(0))
    empty = re.findall(r"call void[^\n]*@(?:py_list_append|py_gen_frame_set)\([^\n]*, ptr null\)", factory.group(0))
    assert len(empty) == len(restored) > 0


# Isolated, parsed ownership-control fixture. The four source regressions
# above remain the production emitter evidence; these controls add no emit.
# Different GEP/bitcast spellings deliberately address the same borrowed cells.
_YIELD_HANDOFF_IR = """
target triple = "x86_64-unknown-linux-gnu"
@borrowed.map = private constant i32 -2
@operand.map = private constant i32 6
declare void @pcc_gc_frame_enter_local(ptr, ptr)
declare void @pcc_gc_frame_leave_local(ptr)
declare ptr @py_await_step(ptr, ptr, ptr)
declare ptr @pcc_gc_take_pinned_slot(ptr, i64)
declare void @py_recursion_leave(i64)
define ptr @resume(ptr %gen, ptr %frame) {
entry:
  %borrowed = alloca [2 x ptr]
  %gen.slot = getelementptr [2 x ptr], ptr %borrowed, i32 0, i32 0
  %frame.slot = getelementptr [2 x ptr], ptr %borrowed, i32 0, i32 1
  %clear.gen = bitcast ptr %gen.slot to ptr
  %clear.frame = getelementptr i8, ptr %borrowed, i64 8
  %operands = alloca [6 x ptr]
  %operand.0 = getelementptr [6 x ptr], ptr %operands, i32 0, i32 0
  %operand.1 = getelementptr [6 x ptr], ptr %operands, i32 0, i32 1
  %operand.2 = getelementptr [6 x ptr], ptr %operands, i32 0, i32 2
  %operand.3 = getelementptr [6 x ptr], ptr %operands, i32 0, i32 3
  %await.owner = getelementptr [6 x ptr], ptr %operands, i32 0, i32 4
  %operand.5 = getelementptr [6 x ptr], ptr %operands, i32 0, i32 5
  store ptr null, ptr %operand.0
  store ptr null, ptr %operand.1
  store ptr null, ptr %operand.2
  store ptr null, ptr %operand.3
  store ptr null, ptr %await.owner
  store ptr null, ptr %operand.5
  call void @pcc_gc_frame_enter_local(ptr @operand.map, ptr %operands)
  store ptr %gen, ptr %gen.slot
  store ptr %frame, ptr %frame.slot
  call void @pcc_gc_frame_enter_local(ptr @borrowed.map, ptr %borrowed)
  br label %await
await:
  %step = call ptr @py_await_step(ptr null, ptr null, ptr null)
  store ptr %step, ptr %await.owner
  br label %handoff
handoff:
  call void @pcc_gc_frame_leave_local(ptr %operands)
  call void @pcc_gc_frame_leave_local(ptr %borrowed)
  call void @py_recursion_leave(i64 1)
  %gen.yield.owner = call ptr @pcc_gc_take_pinned_slot(ptr %await.owner, i64 0)
  store ptr null, ptr %clear.gen
  store ptr null, ptr %clear.frame
  ret ptr %gen.yield.owner
}
"""


def _check_yield_handoff_ir(text):
    from tests.python.test_generator_operand_root_groups import _parsed_function

    module, blocks = _parsed_function(text, "resume")
    _assert_yield_handoff_contract(module, blocks, "resume")


def test_yield_handoff_accepts_only_physical_borrowed_cell_clears():
    _check_yield_handoff_ir(_YIELD_HANDOFF_IR)


@pytest.mark.parametrize("before,after,message", (
    pytest.param(
        "  store ptr null, ptr %clear.gen",
        "  call void @py_recursion_leave(i64 1)\n  store ptr null, ptr %clear.gen",
        "only nonvolatile NULL borrowed clears", id="post-take-call",
    ),
    pytest.param(
        "  store ptr null, ptr %clear.gen",
        "  %extra = add i64 0, 1\n  store ptr null, ptr %clear.gen",
        "only nonvolatile NULL borrowed clears", id="post-take-operation",
    ),
    pytest.param(
        "store ptr null, ptr %clear.gen", "store ptr %gen, ptr %clear.gen",
        "only nonvolatile NULL borrowed clears", id="nonnull-clear",
    ),
    pytest.param(
        "store ptr null, ptr %clear.gen", "store volatile ptr null, ptr %clear.gen",
        "only nonvolatile NULL borrowed clears", id="volatile-clear",
    ),
    pytest.param(
        "store ptr null, ptr %clear.gen", "store ptr null, ptr %await.owner",
        "post-take clear must target a borrowed ABI cell", id="clear-yielded-owner",
    ),
    pytest.param(
        "ret ptr %gen.yield.owner", "ret ptr %gen",
        "return must use the exact take result", id="wrong-return",
    ),
    pytest.param(
        "  store ptr null, ptr %clear.gen\n", "",
        "clear each borrowed ABI cell exactly once", id="missing-clear",
    ),
    pytest.param(
        "store ptr null, ptr %clear.frame", "store ptr null, ptr %clear.gen",
        "clear each borrowed ABI cell exactly once", id="duplicate-clear",
    ),
    pytest.param(
        "ptr %await.owner, i64 0)", "ptr %await.owner, i32 0)",
        "invalid yielded take ABI", id="wrong-take-abi",
    ),
    pytest.param(
        "store ptr %frame, ptr %frame.slot", "store ptr null, ptr %frame.slot",
        "each borrowed ABI argument needs one entry publication", id="fake-borrowed-source",
    ),
    pytest.param(
        "@borrowed.map = private constant i32 -2", "@borrowed.map = private constant i32 2",
        "borrowed ABI pair needs a constant -2 map", id="owning-not-borrowed-map",
    ),
    pytest.param(
        "  call void @pcc_gc_frame_leave_local(ptr %borrowed)\n", "",
        "return reached with active root frames", id="missing-frame-retirement",
    ),
    pytest.param(
        "store ptr %step, ptr %await.owner", "store ptr null, ptr %await.owner",
        "await result must be published immediately", id="wrong-await-publication",
    ),
))
def test_yield_handoff_rejects_invalid_retirement(before, after, message):
    assert _YIELD_HANDOFF_IR.count(before) == 1
    # Baseline acceptance belongs in this node too: a rejected fixture is not
    # evidence that a deliberately broken handoff was detected.
    _check_yield_handoff_ir(_YIELD_HANDOFF_IR)
    with pytest.raises(AssertionError, match=message):
        _check_yield_handoff_ir(_YIELD_HANDOFF_IR.replace(before, after, 1))


_ASYNC_SUSPENSION_NATIVE_PROGRAM = r"""import gc

class Pause:
    def __await__(self):
        yield 'pause'
        return {'value': 42}

def keep(*args):
    print(args[0][0], args[1]['value'])
    return args[1]

async def child():
    return await Pause()

async def parent():
    return keep(['held'], await child())

marker = ValueError('cancel')

async def cancelled():
    try:
        return keep(['cancelled'], await Pause())
    except ValueError as error:
        print('same', error is marker)
        return 17

class Manager:
    async def __aenter__(self):
        return 'entered'

    async def __aexit__(self, cls, error, traceback):
        print('exit', error is marker)
        await Pause()
        return False

async def managed():
    async with Manager() as value:
        print(value)
        await Pause()
        raise marker

events = []

class Token:
    def __del__(self):
        events.append('drop')

def second(*args):
    return args[1]

async def held():
    return second(Token(), await Pause())

def main():
    coro = parent()
    print(coro.send(None))
    try:
        coro.send(None)
    except StopIteration as error:
        print('done', error.value['value'])
    coro = cancelled()
    print(coro.send(None))
    try:
        coro.throw(marker)
    except StopIteration as error:
        print('cancelled', error.value)
    coro = managed()
    print(coro.send(None))
    print(coro.send(None))
    try:
        coro.send(None)
    except ValueError as error:
        print('propagated', error is marker)
    coro = held()
    print(coro.send(None))
    print('alive', len(events))
    coro.close()
    gc.collect()
    print('closed', len(events))

main()
"""


def _assert_native_async_program_matches_cpython(
    tmp_path,
    monkeypatch,
    pcc_runtime_archive,
    program,
):
    from pcc.frontends.python.pipeline import compile_python

    source = tmp_path / "await_lifetime.py"
    executable = tmp_path / "await_lifetime"
    source.write_text(program, encoding="utf-8")
    oracle = subprocess.run(
        [sys.executable, "-B", str(source)],
        capture_output=True,
        text=True,
        timeout=20,
    )
    assert oracle.returncode == 0, oracle.stdout + oracle.stderr
    monkeypatch.setenv("PCC_HOST_PYTHON", sys.executable)
    compile_python(
        str(source),
        str(executable),
        backend="self",
        ir_scaffold_mode="on",
        runtime_archive=str(pcc_runtime_archive),
    )
    for requested_backend in range(5):
        log = tmp_path / ("gc" + str(requested_backend) + ".jsonl")
        ran = subprocess.run(
            [str(executable)],
            capture_output=True,
            text=True,
            timeout=20,
            env=dict(
                os.environ,
                PCC_GC_BACKEND=str(requested_backend),
                PCC_LOG="gc",
                PCC_LOG_FORMAT="json",
                PCC_LOG_FILE=str(log),
            ),
        )
        assert ran.returncode == oracle.returncode, ran.stdout + ran.stderr
        assert ran.stdout == oracle.stdout
        assert ran.stderr == oracle.stderr
        assert log.is_file()
        observed_backends = {
            event.fields["value1"]
            for event in parse_log_lines(log.read_text(encoding="utf-8").splitlines())
            if event.fields.get("category") == "gc"
            and event.event in ("collect_start", "collect_stop", "collect_end")
        }
        assert observed_backends == {requested_backend}


_ASYNCIO_LIFECYCLE_PROGRAM = r"""import asyncio
import gc

events = []
marker = ValueError('identity')

async def worker():
    try:
        await asyncio.sleep(0)
        await asyncio.sleep(0)
    finally:
        events.append('cancel-cleanup')

class Manager:
    async def __aenter__(self):
        return 'entered'

    async def __aexit__(self, cls, error, traceback):
        print('exit-identity', error is marker)
        await asyncio.sleep(0)
        events.append('exit-cleanup')
        return False

async def main():
    task = asyncio.create_task(worker())
    await asyncio.sleep(0)
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        print('cancelled', task.cancelled())
    print(events)
    try:
        async with Manager() as value:
            print(value)
            await asyncio.sleep(0)
            raise marker
    except ValueError as error:
        print('caught-identity', error is marker)
    gc.collect()
    print(events)

asyncio.run(main())
"""


@pytest.mark.integration
@pytest.mark.parametrize("bulk", ("0", "1"))
def test_native_await_operands_preserve_exceptions_cleanup_and_lifetime(
    tmp_path,
    monkeypatch,
    pcc_runtime_archive,
    bulk,
):
    monkeypatch.setenv("PCC_DISABLE_BULK_GENERATOR_FRAME_INIT", bulk)
    _assert_native_async_program_matches_cpython(
        tmp_path, monkeypatch, pcc_runtime_archive, _ASYNC_SUSPENSION_NATIVE_PROGRAM,
    )


@pytest.mark.integration
def test_native_asyncio_cancellation_and_async_context_exception_identity(
    tmp_path,
    monkeypatch,
    pcc_runtime_archive,
):
    _assert_native_async_program_matches_cpython(
        tmp_path, monkeypatch, pcc_runtime_archive, _ASYNCIO_LIFECYCLE_PROGRAM,
    )
