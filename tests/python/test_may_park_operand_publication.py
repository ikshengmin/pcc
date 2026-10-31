"""May-park completion publishes from its authoritative frame cell."""

import os
import re
import subprocess

import pytest

from pcc.backend.self_backend_parse import parse_self_backend_module
from pcc.backend.self_backend_verify import verify_parsed_module
from pcc.backend.self_backend_kernel import get_indexed_function_kernel
from pcc.diagnostics.gc_log import parse_log_lines
from tests.python.root_slot_contract import RootSlotContract
from tests.python.test_slot_call_operand_roots import _emit


def _assert_owned_completion(module):
    function = next(fn for fn in module.functions
                    if fn.name == "user_slot_operand_probe__gen_resume")
    blocks = get_indexed_function_kernel(function).materialize_legacy_blocks(function)
    roots = RootSlotContract(blocks, module.globals_)
    slot, called, args = roots.slot, roots.called, roots.args
    by_name = {block.name: block for block in blocks}
    restored = set()
    for block, index, ins in roots.rows:
        if not called(ins, "py_gen_frame_take"):
            continue
        if not ins.data[0].startswith(("gen.operand.restore", "gen.frame.__pcc_vthread_delegate_")):
            continue
        published = block.instructions[index + 1]
        assert published.kind == "store" and published.data[1] == ins.data[0], (
            "frame restore must immediately publish into its registered owner"
        )
        restored.add(slot(published.data[3]))
        roots.require_owning(published.data[3])
    assert restored

    copies = [(block, index, ins) for block, index, ins in roots.rows
              if called(ins, "pcc_gc_root_copy_local")
              and args(ins)[1].startswith("__pcc_vthread_delegate_")]
    assert copies, "completion must copy from the authoritative frame cell"
    observed_loads = set()
    for block, index, copy in copies:
        destination, source, borrowed = args(copy)
        assert borrowed == "0", "completion needs an independent owner, not a borrowed lease"
        output, original = roots.require_distinct((destination, source))
        assert {output, original} <= restored, "both owners must survive generator suspension"
        checks = [ins for ins in block.instructions[index + 1:]
                  if ins.kind == "icmp" and ins.data[0] == "slt"
                  and ins.data[3:] == (copy.data[0], "0")]
        assert len(checks) == 1
        branch = block.terminator
        assert branch.kind == "br_cond" and branch.data[0] == checks[0].data[1]
        error, ready = (by_name[name] for name in branch.data[1:])
        assert error.name != ready.name
        loads = [(position, ins) for position, ins in enumerate(ready.instructions)
                 if ins.kind == "load" and ins.data[0].startswith("vthread.result.current")
                 and slot(ins.data[3]) == output]
        assert len(loads) == 1, "success must reload the independently owned result"
        position, current = loads[0]
        observed_loads.add(current.data[0])
        prefix = list(block.instructions[index + 1:]) + list(ready.instructions[:position])
        clears = [ins for ins in prefix if called(ins, "pcc_gc_store_root")
                  and slot(args(ins)[0]) == original]
        assert len(clears) == 1, "retire the frame's owner only after successful copying"
        none_loads = {ins.data[0] for ins in prefix if ins.kind == "load"
                      and ins.data[3] == "@py_None"}
        assert args(clears[0])[1] in none_loads
        assert clears[0] in ready.instructions[:position], "failed copy must retain the frame owner"
        for ins in prefix:
            if ins.kind == "store":
                assert slot(ins.data[3]) != output, "completion owner overwritten before consumption"
            if called(ins, "pcc_gc_store_root") or called(ins, "pcc_gc_take_pinned_slot"):
                assert slot(args(ins)[0]) != output, "completion owner retired before consumption"
    expected_loads = {ins.data[0] for _, _, ins in roots.rows if ins.kind == "load"
                      and ins.data[0].startswith("vthread.result.current")}
    assert observed_loads == expected_loads
    roots.assert_frame_exits()


def _completion_ir(expression):
    return _emit(
        "import pcc.virtual_thread as vt\n"
        "def leaf():\n    vt.yield_now()\n    return [42]\n"
        "def plain():\n    return [41]\n"
        "def probe(callback):\n    return " + expression + "\n"
    )


@pytest.mark.parametrize("expression", (
    "slot_operand_probe(leaf())",
    "slot_args_probe(['before'], leaf())",
    "slot_operand_probe(vt.call(callback))",
    "slot_operand_probe(vt.call(callback, [1]))",
    "callback(['before'], leaf())",
    "slot_operand_probe(vt.call(callback, ['before'], leaf()))",
    "slot_operand_probe(vt.call(leaf))",
))
def test_may_park_object_completion_has_an_owned_consumer(expression):
    text = _completion_ir(expression)
    module = parse_self_backend_module(text)
    verify_parsed_module(module)
    body = re.search(
        r"^define [^\n]*@user_slot_operand_probe__gen_resume\([^\n]*\).*?^}",
        text,
        re.M | re.S,
    )
    assert body is not None
    body = body.group(0)
    assert "gen.operand.restore" in body
    _assert_owned_completion(module)
    assert "vthread.delegate.result.retain" not in body
    assert "vthread.call.direct.retain" not in body
    assert "vthread.result.current" in body


@pytest.mark.parametrize("mutation", ["borrowed-copy", "aliased-output", "missing-restore"])
def test_may_park_completion_contract_rejects_nonowning_handoffs(mutation):
    text = _completion_ir("slot_operand_probe(leaf())")
    _assert_owned_completion(parse_self_backend_module(text))
    copy = re.search(
        r"@pcc_gc_root_copy_local\(ptr (%[\w.$]+), "
        r"ptr (%__pcc_vthread_delegate_[\w.$]+), i64 0\)", text,
    )
    assert copy
    if mutation == "borrowed-copy":
        text = text.replace(copy[0], copy[0].replace("i64 0", "i64 1"), 1)
    elif mutation == "aliased-output":
        text = text.replace(copy[0], copy[0].replace(copy[1], copy[2]), 1)
    else:
        body = re.search(r"^define [^\n]*@user_slot_operand_probe__gen_resume\([^\n]*\).*?^}",
                         text, re.M | re.S)
        assert body
        damaged, count = re.subn(r"^  store ptr %gen\.operand\.restore[^\n]+\n", "", body[0], count=1, flags=re.M)
        assert count == 1
        text = text.replace(body[0], damaged, 1)
    with pytest.raises(AssertionError):
        _assert_owned_completion(parse_self_backend_module(text))


_MAY_PARK_NATIVE_PROGRAM = """import gc
import pcc.virtual_thread as vt
from pcc.extern import c_int64
from pcc.extern import extern
actual_backend = extern("pcc_gc_backend", (), c_int64)
events = []
class Token:
    def __init__(self):
        self.value = 40
    def __del__(self):
        events.append("drop")
class Combine:
    def __call__(self, token, value):
        return token.value + value[0]
def leaf():
    vt.yield_now()
    return [2]
def handler(callback):
    return callback(Token(), leaf())
def main():
    print(actual_backend())
    thread = vt.spawn(handler, Combine())
    vt.run(1, 32)
    print(vt.result(thread))
    gc.collect()
    print(events)
main()
"""


@pytest.mark.parametrize("call_kind", ("ordinary", "virtual_thread"))
def test_may_park_completion_native_preserves_prior_argument_and_finalizer(
    tmp_path,
    pcc_runtime_archive,
    call_kind,
):
    from pcc.frontends.python.pipeline import compile_python

    source = tmp_path / "may_park_operands.py"
    executable = tmp_path / "may_park_operands"
    program = _MAY_PARK_NATIVE_PROGRAM
    if call_kind == "virtual_thread":
        program = program.replace(
            "return callback(Token(), leaf())",
            "return vt.call(callback, Token(), leaf())",
        )
    source.write_text(program, encoding="utf-8")
    compile_python(
        str(source),
        str(executable),
        backend="self",
        libpython_mode="off",
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
        assert ran.returncode == 0, ran.stdout + ran.stderr
        assert ran.stdout.splitlines() == [str(requested_backend), "42", "['drop']"]
        assert log.is_file()
        observed_backends = {
            event.fields["value1"]
            for event in parse_log_lines(log.read_text(encoding="utf-8").splitlines())
            if event.fields.get("category") == "gc"
            and event.event in ("collect_start", "collect_stop", "collect_end")
        }
        assert observed_backends == {requested_backend}
