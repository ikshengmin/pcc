"""Native list construction transfers element owners once, in source order."""

import os
import re
import subprocess

import pytest


PROGRAM = '''import gc
import weakref
events = []
class Box:
    def __init__(self, value):
        self.value = value
    def __del__(self):
        events.append(self.value)

def pair(a: int) -> list:
    return [a, a]
def repeated(a: Box, b: Box) -> list:
    return [a, b, a]
def collected(a: Box) -> Box:
    gc.collect()
    return a
def through_collection(a: Box, b: Box) -> list:
    return [a, collected(b), a]
def fresh() -> Box:
    return Box("temporary")
def explode() -> Box:
    raise LookupError("expected")
def failed() -> list:
    return [fresh(), explode()]
def nested(a: int) -> list:
    return [pair(a), pair(a + 1)]
def increment(value: int) -> int:
    return value + 1
def functions() -> list:
    return [increment, increment]
def floats(a: float, b: float) -> list:
    return [a, b, a]
global_value = Box("global-old")
global_replacement = Box("global-new")
def replace_global() -> Box:
    global global_value
    global_value = global_replacement
    gc.collect()
    return global_replacement
def globals_in_order() -> list:
    return [global_value, replace_global(), global_value]
def overridden_return() -> list:
    try:
        return [Box("overridden")]
    finally:
        return [Box("kept")]
resurrected = []
class Survivor:
    def __del__(self):
        resurrected.append(self)
def survivor_list(owner: Survivor) -> list:
    return [owner, owner]
def public_factory(owner: Box) -> list:
    result = list()
    result.append(owner)
    return result
def main():
    huge = 1267650600228229401496703205376
    values = pair(huge)
    assert values == [huge, huge]
    assert values[0] is values[1]
    a = Box("a")
    b = Box("b")
    owners = repeated(a, b)
    assert owners[0] is a and owners[1] is b and owners[2] is a
    held = through_collection(a, b)
    assert held[0] is a and held[1] is b and held[2] is a
    assert nested(7) == [[7, 7], [8, 8]]
    try:
        failed()
    except LookupError as error:
        assert str(error) == "expected"
    else:
        assert False
    gc.collect()
    assert events == ["temporary"]
    del a
    del b
    del owners
    gc.collect()
    assert events == ["temporary"]
    assert held[0].value == "a" and held[1].value == "b"
    del held
    gc.collect()
    assert sorted(events) == ["a", "b", "temporary"]
    callbacks = functions()
    assert callbacks[0] is callbacks[1]
    assert callbacks[0](4) == 5
    assert floats(1.25, 3.5) == [1.25, 3.5, 1.25]
    ordered = globals_in_order()
    assert ordered[0].value == "global-old"
    assert ordered[1] is global_replacement and ordered[2] is global_replacement
    del ordered
    gc.collect()
    assert "global-old" in events
    retained = overridden_return()
    gc.collect()
    assert "overridden" in events and "kept" not in events
    assert retained[0].value == "kept"
    del retained
    gc.collect()
    assert "kept" in events
    public_owner = Box("public")
    public_list = public_factory(public_owner)
    reference = weakref.ref(public_owner)
    del public_owner
    gc.collect()
    assert reference() is public_list[0]
    del public_list
    gc.collect()
    assert reference() is None and "public" in events
    survivor = Survivor()
    saved = survivor_list(survivor)
    del survivor
    gc.collect()
    assert len(resurrected) == 0 and saved[0] is saved[1]
    del saved
    gc.collect()
    assert len(resurrected) == 1
    resurrected.clear()
    gc.collect()
    assert len(resurrected) == 0
    print("OWNED_LIST_CONSTRUCTION_OK")
main()
'''


def test_native_list_literal_uses_owned_root_constructor(tmp_path, monkeypatch):
    from pcc.frontends.python.pipeline import compile_python
    from tests.owned_ir_validation import verify_ir_text

    monkeypatch.setenv('PCC_PYTHON_IR_PASSES', 'off')
    source = tmp_path / 'owned_list.py'
    source.write_text('def pair(a: int) -> list:\n    return [a, a]\n')
    output = source.with_suffix('.ll')
    compile_python(str(source), str(output), emit_llvm_only=True,
                   backend='self', libpython_mode='off', ir_scaffold_mode='on')
    text = output.read_text()
    verify_ir_text(text)
    body = re.search(r'(?ms)^define [^\n]*@user_owned_list_pair\([^\n]*\n.*?^}', text).group(0)
    assert re.search(r'call [^\n]*@py_list_from_owned_roots\(', body)
    assert not re.search(r'call [^\n]*@py_list_append\(', body)


@pytest.mark.integration
@pytest.mark.parametrize("passes", ("default", "off"))
def test_list_literal_owned_construction_native_all_gc(
    tmp_path, monkeypatch, pcc_runtime_archive, python_program_compiler, passes,
):
    if passes == "off":
        monkeypatch.setenv("PCC_PYTHON_IR_PASSES", "off")
    else:
        monkeypatch.delenv("PCC_PYTHON_IR_PASSES", raising=False)
    source = tmp_path / 'owned_list.py'
    source.write_text(PROGRAM)
    binary = source.with_suffix('')
    python_program_compiler(str(source), str(binary), backend='self',
                            libpython_mode='off', runtime_archive=str(pcc_runtime_archive))
    for backend in range(5):
        result = subprocess.run(
            [str(binary)], capture_output=True, text=True, timeout=30,
            env=dict(os.environ, PCC_GC_BACKEND=str(backend), PATH='/nonexistent'),
        )
        (tmp_path / ('gc' + str(backend) + '.stdout')).write_text(result.stdout)
        (tmp_path / ('gc' + str(backend) + '.stderr')).write_text(result.stderr)
        assert result.returncode == 0, (backend, result.stdout, result.stderr)
        assert result.stdout == 'OWNED_LIST_CONSTRUCTION_OK\n'


THREADED_PROGRAM = """import gc
import threading
events = []
class Item:
    def __init__(self, value: int):
        self.value = value
    def __del__(self):
        events.append(self.value)
def constructed(a: Item, b: Item) -> list:
    return [a, b, a]
def worker(seed: int, results: list):
    checksum = 0
    for index in range(200):
        first = Item(seed + index)
        second = Item(seed + index + 1)
        values = constructed(first, second)
        assert values[0] is first and values[1] is second and values[2] is first
        del first
        del second
        if index % 16 == 0:
            gc.collect()
        checksum += values[0].value + values[1].value
        del values
    results[seed] = checksum
def main():
    results = [-1, -1, -1, -1]
    threads = []
    for seed in range(4):
        thread = threading.Thread(target=worker, args=(seed, results))
        threads.append(thread)
        thread.start()
    for thread in threads:
        thread.join()
    gc.collect()
    for seed in range(4):
        assert results[seed] == 40000 + 400 * seed
    assert len(events) == 1600
    print("OWNED_LIST_THREADS_OK")
main()
"""


@pytest.mark.parametrize("program, expected", (
    (PROGRAM, "OWNED_LIST_CONSTRUCTION_OK\n"),
    (THREADED_PROGRAM, "OWNED_LIST_THREADS_OK\n"),
))
def test_list_construction_reference(tmp_path, program, expected):
    import sys

    source = tmp_path / "reference.py"
    source.write_text(program)
    result = subprocess.run([sys.executable, str(source)], capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr
    assert result.stdout == expected


@pytest.mark.integration
def test_list_construction_concurrent_native_all_gc(
    tmp_path, monkeypatch, threaded_pcc_runtime_archive,
):
    from pcc.frontends.python.pipeline import compile_python

    monkeypatch.delenv("PCC_PYTHON_IR_PASSES", raising=False)
    source = tmp_path / "threaded_construction.py"
    source.write_text(THREADED_PROGRAM)
    binary = source.with_suffix("")
    compile_python(str(source), str(binary), backend="self", libpython_mode="off",
                   ir_scaffold_mode="on", runtime_archive=str(threaded_pcc_runtime_archive))
    for backend in range(5):
        result = subprocess.run(
            [str(binary)], capture_output=True, text=True, timeout=30,
            env=dict(os.environ, PCC_GC_BACKEND=str(backend), PATH="/nonexistent"),
        )
        (tmp_path / ("gc" + str(backend) + ".stdout")).write_text(result.stdout)
        (tmp_path / ("gc" + str(backend) + ".stderr")).write_text(result.stderr)
        assert result.returncode == 0, (backend, result.stdout, result.stderr)
        assert result.stdout == "OWNED_LIST_THREADS_OK\n"
