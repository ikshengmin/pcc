"""A delegated park publishes one activation-only owner before frame saves."""

import re
import pytest

from tests.python.process_timeout import run_process_group_timeout


PROGRAM = """
import gc
import sys
from pcc.virtual_thread import spawn, run, result, sleep_current

class Canary:
    def __init__(self, value, ledger):
        self.value = value
        self.ledger = ledger
    def __del__(self):
        self.ledger.append(self.value)

def leaf(seed: int):
    sleep_current(0)
    return [seed]

def failing():
    sleep_current(0)
    raise ValueError("boom")

def work(seed: int) -> int:
    a = [seed]
    b = a
    c = [seed+1]
    d = [seed+2]
    e = [seed+3]
    f = [seed+4]
    g = [seed+5]
    h = [seed+6]
    ledger = []
    held = Canary(seed, ledger)
    if seed % 2:
        one = leaf(seed)
    else:
        one = leaf(seed)
    b.append(one[0])
    two = leaf(seed+1)
    three = leaf(seed+2)
    four = leaf(seed+3)
    try:
        failing()
    except ValueError as error:
        if str(error) != "boom":
            return -100000
    if len(ledger) != 0:
        return -200000
    held_value = held.value
    held = None
    if ledger != [seed]:
        return -300000
    return a[0]+b[1]+c[0]+d[0]+e[0]+f[0]+g[0]+h[0]+held_value+one[0]+two[0]+three[0]+four[0]

def main() -> None:
    carriers = int(sys.argv[1])
    jobs = []
    for i in range(12):
        jobs.append(spawn(work, i))
    run(carriers, 100000)
    total = 0
    for job in jobs:
        total += result(job)
    gc.collect()
    print(total)

if __name__ == "__main__":
    main()
"""

# Cross the performance threshold with 32 extra live locals and 14 real
# additional delegated lexical sites; none are compiler exact-int temps.
_PROGRAM_PADS = 32
_PROGRAM_EXTRA_DELEGATES = 14
_PROGRAM_DELEGATE_SITES = 6 + _PROGRAM_EXTRA_DELEGATES
PROGRAM = PROGRAM.replace(
    "    held = Canary(seed, ledger)\n",
    "    held = Canary(seed, ledger)\n"
    + "".join(f"    pad{i} = [seed]\n" for i in range(_PROGRAM_PADS)),
)
PROGRAM = PROGRAM.replace(
    "    four = leaf(seed+3)\n",
    "    four = leaf(seed+3)\n"
    + "".join(f"    extra{i} = leaf(seed)\n" for i in range(_PROGRAM_EXTRA_DELEGATES)),
)
PROGRAM = PROGRAM.replace(
    "+four[0]\n",
    "+four[0]"
    + "".join(f"+pad{i}[0]" for i in range(_PROGRAM_PADS))
    + "".join(f"+extra{i}[0]" for i in range(_PROGRAM_EXTRA_DELEGATES))
    + "\n",
)
# Per task: 13*seed+27 from the original program, plus one seed for
# each added local and delegate result.
_EXPECTED = str(sum((13 + _PROGRAM_PADS + _PROGRAM_EXTRA_DELEGATES) * i + 27
                    for i in range(12)))



def test_delegate_parks_share_only_one_registered_activation_owner(tmp_path, monkeypatch):
    from pcc.frontends.python.pipeline import compile_python
    from pcc.backend.self_backend_parse import parse_self_backend_module
    from pcc.backend.self_backend_verify import verify_parsed_module

    source = tmp_path / "delegate_handoff.py"
    source.write_text(PROGRAM)
    arms = []
    for enabled in ("0", "1"):
        monkeypatch.setenv("PCC_GENERATOR_SHARED_FRAME_SAVE", enabled)
        output = tmp_path / ("delegate_" + enabled + ".ll")
        compile_python(str(source), str(output), backend="self",
                       ir_scaffold_mode="on", emit_llvm_only=True)
        text = output.read_text()
        verify_parsed_module(parse_self_backend_module(text))
        body = re.search(r"define[^\n]*work__gen_resume[^\n]*\{\n(.*?)\n\}", text, re.S)
        assert body is not None
        arms.append(body.group(1))
    old, new = arms
    assert "gen.delegate.handoff" not in old
    cells = re.findall(r"(%gen\.delegate\.handoff[^ ]+) = alloca ptr", new)
    assert len(cells) == 1
    cell = re.escape(cells[0])
    initialization = re.search(r"store ptr null, ptr " + cell + r"\b", new)
    registration = re.search(r"@pcc_gc_frame_enter[^\n]*ptr " + cell + r"\)", new)
    assert initialization is not None and registration is not None
    assert len(re.findall(r"store ptr null, ptr " + cell + r"\b", new)) == 1
    assert len(re.findall(r"@pcc_gc_frame_enter[^\n]*ptr " + cell + r"\)", new)) == 1
    assert initialization.start() < registration.start()
    maps = re.findall(r"@pcc_gc_frame_enter[^\n]*ptr @([^,]+), ptr " + cell + r"\)", new)
    assert len(maps) == 1
    assert re.search(r"@" + re.escape(maps[0]) + r" = [^\n]*constant i32 1\b", text)
    child_slots = r"%__pcc_vthread_delegate_(?:leaf|failing)_[^ ]+ = alloca ptr"
    assert len(re.findall(child_slots, old)) == len(re.findall(child_slots, new)) == _PROGRAM_DELEGATE_SITES
    publications = re.findall(r"store ptr (%vthread\.delegate\.value[^,]+), ptr " + cell + r"\b", new)
    assert len(publications) == _PROGRAM_DELEGATE_SITES

    # The returned flag and null comparisons are scalar-only CFG edges.
    # Prove each publication consumes the exact fresh producer rather than
    # an SSA alias which crossed a runtime safepoint or a release.
    from tests.python.test_generator_resume_gate_reload import _function_blocks
    _name, blocks, _order = _function_blocks(text, r"[^ (]*_work__gen_resume")
    for value in publications:
        producers = [(label, index) for label, lines in blocks.items()
                     for index, line in enumerate(lines)
                     if line.startswith(value + " = call") and "@py_gen_next_direct(" in line]
        assert len(producers) == 1
        label, index = producers[0]
        tail = blocks[label][index + 1:]
        assert not any("call " in line for line in tail)
        unreturned = [target for target in re.findall(r"label %([-.\w]+)", tail[-1])
                      if target.startswith("vthread.delegate.unreturned")]
        assert len(unreturned) == 1
        checks = blocks[unreturned[0]]
        assert not any("call " in line for line in checks)
        yielded = [target for target in re.findall(r"label %([-.\w]+)", checks[-1])
                   if target.startswith("vthread.delegate.yielded")]
        assert len(yielded) == 1
        lines = blocks[yielded[0]]
        store = [i for i, line in enumerate(lines)
                 if re.match(r"store ptr " + re.escape(value) + r", ptr " + cell + r"\b", line)]
        assert len(store) == 1
        assert not any("call " in line for line in lines[:store[0]])
    assert not re.search(r"@py_gen_frame_(?:move|set)[^\n]*" + cell + r"\b", new)
    assert not re.search(r"@pcc_gc_store_root[^\n]*" + cell + r"\b", new)
    assert "@pcc_gc_take_slot" in new
    calls = lambda text: len(re.findall(r"call[^\n]*@py_gen_frame_move\(", text))
    assert calls(new) < calls(old) / 2


@pytest.mark.integration
@pytest.mark.parametrize("direct", [False, True])
def test_native_delegated_handoff_preserves_runtime_on_one_and_four_carriers(
        tmp_path, monkeypatch, pcc_runtime_archive, direct):
    from pcc.frontends.python.pipeline import compile_python

    monkeypatch.setenv("PCC_WITH_THREADS", "1")
    if direct:
        monkeypatch.setenv("PCC_PYTHON_IR_PASSES", "off")
        monkeypatch.setenv("PCC_DIRECT_INDEXED_KERNEL_CAPTURE", "1")
        monkeypatch.setenv("PCC_DIRECT_INDEXED_KERNEL_EMIT", "1")
        monkeypatch.setenv("PCC_DIRECT_INLINE_ERROR_EDGE_CAPTURE", "1")
    else:
        monkeypatch.setenv("PCC_DIRECT_INDEXED_KERNEL_CAPTURE", "0")
        monkeypatch.setenv("PCC_DIRECT_INDEXED_KERNEL_EMIT", "0")
    source = tmp_path / "delegate_handoff.py"
    source.write_text(PROGRAM)
    for enabled in ("0", "1"):
        monkeypatch.setenv("PCC_GENERATOR_SHARED_FRAME_SAVE", enabled)
        binary = tmp_path / ("delegate_" + enabled)
        compile_python(str(source), str(binary), backend="self",
                       ir_scaffold_mode="on", runtime_archive=str(pcc_runtime_archive))
        for carriers in ("1", "4"):
            ran = run_process_group_timeout([str(binary), carriers], timeout=30)
            assert (ran.returncode, ran.stderr, ran.stdout.strip()) == (0, "", _EXPECTED)


def test_single_delegated_site_retains_original_inline_save(tmp_path, monkeypatch):
    from pcc.frontends.python.pipeline import compile_python

    source = tmp_path / "single_delegate.py"
    source.write_text(
        "from pcc.virtual_thread import sleep_current\n\n"
        "def leaf(seed: int):\n    sleep_current(0)\n    return seed\n\n"
        "def work(seed: int):\n"
        + "".join(f"    pad{i} = [seed]\n" for i in range(70))
        + "    value = leaf(seed)\n    return value"
        + "".join(f"+pad{i}[0]" for i in range(70)) + "\n"
    )
    monkeypatch.setenv("PCC_GENERATOR_SHARED_FRAME_SAVE", "1")
    output = tmp_path / "single.ll"
    compile_python(str(source), str(output), backend="self",
                   ir_scaffold_mode="on", emit_llvm_only=True)
    assert "gen.delegate.handoff" not in output.read_text()


def test_delegated_handoff_reaches_owned_darwin_stackmap_emitter(tmp_path, monkeypatch):
    from pcc.frontends.python.pipeline import compile_python
    from pcc.backend.owned_object_emit import emit_owned_object

    source = tmp_path / "delegate_handoff.py"
    source.write_text(PROGRAM)
    monkeypatch.setenv("PCC_GENERATOR_SHARED_FRAME_SAVE", "1")
    output = tmp_path / "darwin.ll"
    compile_python(str(source), str(output), backend="self",
                   ir_scaffold_mode="on", emit_llvm_only=True,
                   target_triple="arm64-apple-darwin")
    text = output.read_text()
    assert "gen.delegate.handoff" in text
    assert emit_owned_object(text, "arm64-apple-darwin")[:4] == b"\xcf\xfa\xed\xfe"


def test_many_delegates_without_large_static_frame_stay_inline(tmp_path, monkeypatch):
    from pcc.frontends.python.pipeline import compile_python

    source = tmp_path / "small_frame.py"
    source.write_text(
        "from pcc.virtual_thread import sleep_current\n\n"
        "def leaf(seed: int):\n    sleep_current(0)\n    return seed\n\n"
        "def work(seed: int):\n"
        + "    leaf(seed)\n" * 16 + "    return seed\n"
    )
    monkeypatch.setenv("PCC_GENERATOR_SHARED_FRAME_SAVE", "1")
    output = tmp_path / "small.ll"
    compile_python(str(source), str(output), backend="self",
                   ir_scaffold_mode="on", emit_llvm_only=True)
    text = output.read_text()
    assert "gen.delegate.handoff" not in text
    assert "gen.save.shared" not in text
