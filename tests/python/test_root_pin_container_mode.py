"""A root store's barrier and a root pin take the graph lock only outside container mode.

Slot-frame helpers (the _adopt functions of special-method calls, sequence
repeat, method calls, ...) published a producer's result and then reread
and barriered the slot under the global graph lock; compiled code reloaded
and pinned a heap return value, a generator's yielded value, a valueclass
return's payload roots and an extern argument's root under it. In container
mode (every collector without the provenance probe) every collector scans
roots with the world stopped, an object a running thread names never moves
and the count-free store barrier is the unlocked one, so neither needs the
lock: pcc_gc_note_root_slot_barrier and the compiled root pin
(OwnershipLoweringMixin._emit_root_pin_lock/_unlock) read the mode and take
the lock only without it.
"""
from __future__ import annotations

from pathlib import Path
import re

import pytest

from pcc.frontends.python.pipeline import compile_python
from test_class_namespace_writer import load_functions


ROOT = Path(__file__).resolve().parents[2]
RUNTIME = ROOT / "pcc" / "runtime" / "py"
LOCKED_BARRIER = re.compile(
    r"pcc_py_gc_minor_graph_lock\(\)\n\s+pcc_gc_note_slot_write_barrier\(null\(\), [^\n]+\)\n"
    r"\s+pcc_py_gc_minor_graph_unlock\(\)"
)


@pytest.mark.parametrize("container_mode,expected", [
    (1, ["barrier"]),
    (0, ["lock", "barrier", "unlock"]),
])
def test_root_slot_barrier_locks_only_outside_container_mode(container_mode, expected):
    events = []
    namespace = {
        "load_i32": lambda name, offset: container_mode,
        "global_addr": lambda name: name,
        "load_ptr": lambda slot, offset: slot[0],
        "null": lambda: None,
        "pcc_py_gc_minor_graph_lock": lambda: events.append("lock"),
        "pcc_py_gc_minor_graph_unlock": lambda: events.append("unlock"),
        "pcc_gc_note_slot_write_barrier": lambda owner, slot, value: events.append(
            "barrier" if owner is None and value == "value" else "bad"
        ),
    }
    load_functions(RUNTIME / "freestanding_gc_barrier_dispatcher.py",
                   ("pcc_gc_note_root_slot_barrier",), namespace)
    namespace["pcc_gc_note_root_slot_barrier"](["value"])
    assert events == expected


def test_no_runtime_module_barriers_a_root_slot_under_the_graph_lock_itself():
    found = []
    for path in sorted(RUNTIME.glob("*.py")):
        text = path.read_text(encoding="utf-8")
        for match in LOCKED_BARRIER.finditer(text):
            found.append((path.name, text[:match.start()].count("\n") + 1))
    # The helper itself is the one place that holds this sequence.
    assert [name for name, _line in found] == ["freestanding_gc_barrier_dispatcher.py"], found


SOURCE = '''
def join(left, right):
    return left + right


def main():
    print(join("a", "b"), join([1], [2]))


main()
'''


def _blocks(body):
    blocks, label = {}, None
    for line in body.splitlines():
        match = re.match(r"^([\w.$-]+):", line)
        if match:
            label = match[1]
            blocks[label] = []
        elif label is not None and line.strip():
            blocks[label].append(line.strip())
    return blocks


def test_compiled_root_pins_lock_only_outside_container_mode(tmp_path):
    source = tmp_path / "root_pins.py"
    source.write_text(SOURCE, encoding="utf-8")
    output = tmp_path / "root_pins.ll"
    compile_python(str(source), str(output), backend="self",
                   ir_scaffold_mode="on", emit_llvm_only=True)
    text = output.read_text(encoding="utf-8")
    bodies = {m.group(1): m.group(0)
              for m in re.finditer(r"^define [^\n]*@(user_root_pins_\w+)\(.*?^}", text, re.M | re.S)}
    checked = 0
    for name, body in bodies.items():
        blocks = _blocks(body)
        for label, lines in blocks.items():
            if not any("@pcc_py_gc_minor_graph_lock(" in line for line in lines):
                continue
            assert label.startswith("gc.root.pin.lock"), (name, label)
            entries = [other for other, other_lines in blocks.items()
                       if any(l.startswith("br ") and "%" + label in l for l in other_lines)]
            assert entries, (name, label)
            for entry in entries:
                assert any("@pcc_gc_list_container_mode" in l for l in blocks[entry]), (name, entry)
            checked += 1
    # join's heap result of a dynamic binary operation pins its root.
    assert checked >= 1 and "user_root_pins_join" in bodies, (checked, sorted(bodies))
