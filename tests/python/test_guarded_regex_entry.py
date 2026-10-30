"""Raw bridge model/IR only; the managed fullmatch body is not implemented here."""
import ast
from pathlib import Path
import re

import pytest

SOURCE = Path(__file__).resolve().parents[2] / "pcc/runtime/py/py_re_bridge_entry.py"


class EntryModel:
    def __init__(self, fail_registration=0, result_value=900):
        self.depth = 0
        self.next_address = 100
        self.memory = {}
        self.roots = []
        self.count = 0
        self.fail_registration = fail_registration
        self.result_value = result_value
        self.events = []
        self.pinned = False
        namespace = {
            "i64": int,
            "c_ptr": int,
            "pcc_thread_no_park_depth": lambda: self.depth,
            "pcc_thread_no_park_enter": self.enter,
            "pcc_thread_no_park_exit": self.exit,
            "stack_alloc": self.allocate,
            "store_ptr": lambda base, offset, value: self.memory[base].__setitem__(offset, value),
            "memset": lambda base, zero, size: self.memory[base].__setitem__(0, zero),
            "global_addr": lambda name: name,
            "pcc_gc_note_frame_enter_checked": self.register,
            "pcc_gc_note_frame_leave": self.leave,
            "pcc_re_engine_fullmatch_into": self.body,
            "pcc_gc_take_pinned_slot": self.take,
            "pcc_platform_abort": lambda: (_ for _ in ()).throw(RuntimeError("root failure")),
            "null": lambda: 0,
        }
        nodes = [node for node in ast.parse(SOURCE.read_text()).body if isinstance(node, ast.FunctionDef)]
        for node in nodes:
            node.decorator_list = []
        exec(compile(ast.Module(body=nodes, type_ignores=[]), str(SOURCE), "exec"), namespace)
        self.run = namespace["pcc_re_engine_fullmatch_guarded"]

    def enter(self):
        self.events.append("guard")
        self.depth += 1

    def allocate(self, size):
        assert self.depth == 1
        self.next_address += size
        self.memory[self.next_address] = {}
        return self.next_address

    def register(self, frame_map, slots):
        assert self.depth == 1
        self.count += 1
        if self.count == self.fail_registration:
            return 0
        if frame_map.endswith("borrowed_map"):
            assert self.memory[slots] == {0: 10, 8: 20}
        else:
            assert self.memory[slots] == {0: 0}
        self.roots.append(slots)
        self.events.append("root")
        return 1

    def exit(self):
        assert len(self.roots) == 2
        self.depth -= 1
        # A pending stop moves both borrowed inputs. Body must reload slots.
        self.memory[self.roots[0]] = {0: 11, 8: 21}
        self.events.append("poll")

    def body(self, borrowed, result, flags):
        assert self.depth == 0 and self.roots == [borrowed, result]
        assert self.memory[borrowed] == {0: 11, 8: 21}
        assert flags == 2
        self.memory[result][0] = self.result_value
        self.events.append("body")
        assert result in self.roots
        self.pinned = True
        self.events.append("pin")
        return 123

    def leave(self, slots):
        if self.depth == 0:
            assert self.pinned
        self.roots.remove(slots)
        self.events.append("leave")

    def take(self, result, prior):
        assert not self.roots and self.pinned and prior == 123
        self.events.append("take")
        value = self.memory[result][0]
        self.memory[result][0] = 0
        return value


@pytest.mark.parametrize("result_value", [0, 900])
def test_raw_trampoline_roots_before_poll_and_pins_before_retiring_roots(result_value):
    model = EntryModel(result_value=result_value)
    assert model.run(10, 20, 2) == result_value
    assert model.events == ["guard", "root", "root", "poll", "body", "pin", "leave", "leave", "take"]
    assert model.depth == 0 and not model.roots


@pytest.mark.parametrize("failed", [1, 2])
def test_failed_root_bootstrap_never_polls_or_calls_managed_body(failed):
    model = EntryModel(fail_registration=failed)
    with pytest.raises(RuntimeError, match="root failure"):
        model.run(10, 20, 2)
    assert "poll" not in model.events and "body" not in model.events
    assert model.depth == 1 and not model.roots


def test_raw_trampoline_ir_has_no_poll_before_guard_or_managed_prologue(tmp_path, monkeypatch):
    monkeypatch.delenv("PCC_IMPLICIT_THREAD_SAFEPOINTS", raising=False)
    from pcc.frontends.python.owned_runtime_build import _compile_runtime_module
    output = tmp_path / "entry.ll"
    _compile_runtime_module("py_re_bridge_entry", str(SOURCE), str(output), "x86_64-unknown-linux-gnu")
    text = output.read_text()
    match = re.search(r"^define (?:external )?ptr @pcc_re_engine_fullmatch_guarded\(ptr [^,]+, ptr [^,]+, i64 [^)]+\).*?^}", text, re.M | re.S)
    assert match is not None
    wrapper = match.group()
    assert "@pcc_thread_stop_requested" not in wrapper
    assert "@pcc_gc_frame_enter(" not in wrapper
    helper = re.search(r"^define[^\n]*@user_py_re_bridge_entry__re_guarded\(.*?^}", text, re.M | re.S)
    assert helper is not None
    body = helper.group()
    assert "@pcc_thread_stop_requested" not in body
    assert "@pcc_gc_frame_enter(" not in body
    calls = re.findall(r"\bcall\b[^@\n]*@([A-Za-z_][A-Za-z_0-9.]*)\(", body)
    assert calls[0] == "pcc_thread_no_park_depth"
    assert calls.index("pcc_thread_no_park_enter") < calls.index("pcc_gc_note_frame_enter_checked")
    assert calls.count("pcc_gc_note_frame_enter_checked") == 2
    assert calls.index("pcc_thread_no_park_exit") < calls.index("pcc_re_engine_fullmatch_into")
    # LLVM block layout is not execution order: callback branch blocks can be
    # printed after the join. The take block must return without another call.
    take_tail = body.split("@pcc_gc_take_pinned_slot(", 1)[1].split("\n}", 1)[0]
    take_block = re.split(r"\n[A-Za-z_][^\n]*:\n", take_tail, maxsplit=1)[0]
    assert "ret ptr" in take_block
    assert not re.search(r"\bcall\b", take_block)



def test_managed_body_keeps_ordinary_entry_poll_and_build_setting_is_restored(tmp_path, monkeypatch):
    import os
    from pcc.frontends.python.owned_runtime_build import _compile_runtime_module
    monkeypatch.delenv("PCC_IMPLICIT_THREAD_SAFEPOINTS", raising=False)
    entry = tmp_path / "entry.ll"
    _compile_runtime_module("py_re_bridge_entry", str(SOURCE), str(entry), "x86_64-unknown-linux-gnu")
    assert "PCC_IMPLICIT_THREAD_SAFEPOINTS" not in os.environ
    source = tmp_path / "managed_body_control.py"
    source.write_text('__pcc_runtime_port__ = True\nfrom pcc.extern import c_abi_export\n@c_abi_export("pcc_re_managed_body_control")\ndef body(borrowed, result, flags: int) -> int:\n    return flags\n')
    output = tmp_path / "body.ll"
    _compile_runtime_module("managed_body_control", str(source), str(output), "x86_64-unknown-linux-gnu")
    assert "@pcc_thread_stop_requested" in output.read_text()
    assert "PCC_IMPLICIT_THREAD_SAFEPOINTS" not in os.environ


def test_real_managed_match_body_uses_raw_slot_abi_and_keeps_polls(tmp_path, monkeypatch):
    """IR proof, not a native/five-collector acceptance test."""
    from pcc.frontends.python.owned_runtime_build import _compile_runtime_module
    source = SOURCE.with_name("py_re_match_runtime.py")
    monkeypatch.setenv("PCC_RUNTIME_DIR", str(source.parent.parent))
    monkeypatch.delenv("PCC_IMPLICIT_THREAD_SAFEPOINTS", raising=False)
    output = tmp_path / "match.ll"
    _compile_runtime_module("py_re_match_runtime", str(source), str(output), "x86_64-unknown-linux-gnu")
    text = output.read_text()
    assert "strict.nolib.stub" not in text
    names = []
    for node in ast.parse(source.read_text()).body:
        if not isinstance(node, ast.FunctionDef):
            continue
        name = node.name if node.decorator_list else "user_py_re_match_runtime_" + node.name
        body = re.search(r"^define[^\n]*@" + re.escape(name) + r"\([^\n]*\).*?^}", text, re.M | re.S)
        assert body is not None, name
        body = body.group()
        assert "@pcc_thread_stop_requested" in body, name
        assert not re.search(r"call[^\n]*@pcc_gc_(?:retain|release|frame_enter|load_borrowed)", body), name
        names.append(name)
    assert "pcc_re_engine_fullmatch_into" in names
    assert "pcc_re_match_method_into" in names
    assert len(names) >= 17


def test_inherited_no_park_is_fail_stop_before_root_or_managed_work():
    model = EntryModel()
    model.depth = 1
    with pytest.raises(RuntimeError, match="root failure"):
        model.run(10, 20, 2)
    assert model.events == []
    assert model.depth == 1 and not model.roots
