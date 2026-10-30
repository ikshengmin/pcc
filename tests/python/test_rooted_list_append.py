"""Existing roots stay authoritative through append waits and growth retries."""

import ast
from pathlib import Path

import pytest

from pcc.runtime.py import py_abi_constants as abi


class AppendRoots:
    def __init__(self, backend=0, action='move', commit=True, sane=True):
        self.memory = {100: 1000, 200: 5000, 300: 9000, 400: 9100,
                       1016: 0, 1024: 4, 1032: 2000}
        self.depth = 0
        self.calls = []
        self.action = action
        self.commit_ok = commit
        self.sane = sane
        self.serial = 20000
        self.locks = 0
        self.ns = {
            'stack_alloc': self.allocate, 'null': lambda: 0,
            'load_ptr': self.load, 'store_ptr': self.store,
            'load_i64': self.load, 'store_i64': self.store,
            'ptr_add': lambda pointer, offset: pointer + offset,
            'ptr_is_null': lambda pointer: int(pointer == 0),
            'pcc_threads_enabled': lambda: 1, 'pcc_gc_backend': lambda: backend,
            'pcc_py_gc_minor_graph_lock': self.lock,
            'pcc_py_gc_minor_graph_unlock': self.unlock,
            # The list's transaction lock (container or graph lock).
            '_list_lock': lambda _list: self.lock(),
            '_list_unlock': lambda _list: self.unlock(),
            'pcc_gc_load_ptr': self.heal,
            '_list_is_sane': self.validate,
            '_grow_if_needed': self.grow,
            'pcc_gc_store_ptr_plan_init': self.initialize,
            'pcc_gc_store_ptr_plan_commit_locked': self.commit,
            'pcc_gc_store_ptr_plan_finish': self.finish,
        }
        for name in dir(abi):
            if name.startswith('PYLISTOBJECT_'):
                self.ns[name] = getattr(abi, name)
        source = Path(__file__).resolve().parents[2] / 'pcc/runtime/py/py_list.py'
        selected = [node for node in ast.parse(source.read_text()).body
                    if isinstance(node, ast.FunctionDef)
                    and node.name in ('_append_rooted_slots', '_reload_moving_root')]
        assert len(selected) == 2
        exec(compile(ast.Module(selected, type_ignores=[]), str(source), 'exec'), self.ns)

    def allocate(self, size):
        self.serial += size
        return self.serial

    def load(self, pointer, offset):
        return self.memory.get(pointer + offset, 0)

    def store(self, pointer, offset, value):
        self.memory[pointer + offset] = value

    def heal(self, owner, slot):
        assert self.depth == 1
        self.calls.append(('reload', slot, self.memory[slot]))
        return self.memory[slot]

    def lock(self):
        assert self.depth == 0
        self.depth = 1
        self.locks += 1
        if self.locks == 1:
            # Model relocation while acquisition waits; old raw addresses die.
            for offset in (16, 24, 32):
                self.memory[1100 + offset] = self.memory.pop(1000 + offset)
            self.memory[100] = 1100
            self.memory[200] = 5100
            if self.action in ('grow', 'oom'):
                self.memory[1116] = 4
        self.calls.append(('lock',))

    def unlock(self):
        assert self.depth == 1
        self.depth = 0
        self.calls.append(('unlock',))

    def validate(self, owner, code):
        assert self.depth == 1 and owner == self.memory[100]
        return self.sane

    def initialize(self, plan, owner, backend):
        assert self.depth == 0
        self.calls.append(('prepare', backend))

    def commit(self, plan, owner, slot, value):
        assert self.depth == 1
        assert owner == self.memory[100] and value == self.memory[200]
        assert self.memory[slot] == 0
        self.calls.append(('commit', owner, slot, value))
        if self.commit_ok:
            self.memory[slot] = value
        return int(self.commit_ok)

    def finish(self, plan):
        assert self.depth == 0
        self.calls.append(('finish',))

    def grow(self, owner, want):
        assert self.depth == 0 and owner == self.memory[100]
        self.calls.append(('grow', want))
        if self.action == 'oom':
            return 0
        self.memory[owner + 24] = 8
        self.memory[owner + 16] = 5  # A concurrent append won during growth.
        self.memory[2032] = 6000
        self.memory[200] = 5200
        return owner

    def run(self):
        result = self.ns['_append_rooted_slots'](100, 200, 300, 400, 0)
        assert self.depth == 0
        assert self.memory[300] == 9000 and self.memory[400] == 9100
        assert self.memory[100] == 1100 and self.memory[200] in (5100, 5200)
        return result


@pytest.mark.parametrize('backend', range(5))
def test_existing_append_roots_reload_after_lock_wait_and_remain_owned(backend):
    model = AppendRoots(backend)
    assert model.run() == 0
    assert model.memory[2000] == 5100 and model.memory[1116] == 1
    assert model.calls.index(('reload', 200, 5100)) < model.calls.index(('commit', 1100, 2000, 5100))
    assert model.calls[-2:] == [('unlock',), ('finish',)]


def test_append_rechecks_length_after_growth_races():
    model = AppendRoots(action='grow')
    assert model.run() == 0
    assert model.locks == 2
    assert model.memory[2032] == 6000 and model.memory[2040] == 5200
    assert model.memory[1116] == 6


def test_append_rejected_store_does_not_publish_length():
    model = AppendRoots(commit=False)
    assert model.run() == 0
    assert model.memory[2000] == 0 and model.memory[1116] == 0
    assert model.calls[-1] == ('finish',)


@pytest.mark.parametrize('action,sane,status', (('oom', True, -1), ('move', False, -2)))
def test_append_failures_do_not_take_or_destroy_caller_roots(action, sane, status):
    model = AppendRoots(action=action, sane=sane)
    assert model.run() == status
    assert not any(call[0] == 'commit' for call in model.calls)


CONCAT_PROGRAM = '''import gc
import threading

events = []
class Record:
    def __init__(self, value):
        self.value = value
    def __del__(self):
        events.append(self.value)
def lifetimes():
    first = Record(1)
    second = Record(2)
    huge = 1267650600228229401496703205376
    left = [first, huge, None]
    right = [second, first, -7]
    joined = left + right
    repeated = left + left
    assert joined[0] is first and joined[4] is first
    assert joined[3] is second and joined[1] == huge
    assert repeated[0] is repeated[3] and repeated[0] is first
    assert [] + [] == []
    assert left + [] == left and [] + right == right
    assert (left + []) is not left
    gc.collect()
    assert events == []
    joined[0].value = 11
    assert first.value == 11 and repeated[3].value == 11
    left = None
    right = None
    repeated = None
    first = None
    second = None
    gc.collect()
    assert events == []
    joined = None
    gc.collect()
    assert sorted(events) == [2, 11]
def worker(index, result):
    left = [index, index + 1]
    right = [-1, None]
    checksum = 0
    iteration = 0
    while iteration < 100:
        joined = left + right
        assert len(joined) == 4 and joined[3] is None
        assert joined[0] == index and joined[1] == index + 1
        checksum += joined[0] + joined[1] + joined[2]
        if iteration % 10 == 0:
            gc.collect()
        iteration += 1
    result[index] = checksum
def main():
    lifetimes()
    result = [-1, -1]
    first = threading.Thread(target=worker, args=(0, result))
    second = threading.Thread(target=worker, args=(1, result))
    first.start()
    second.start()
    first.join()
    second.join()
    assert result == [0, 200]
    print("ROOTED_LIST_CONCAT_OK")
main()
'''


def test_concat_lifetimes_and_threads_reference(tmp_path):
    import subprocess
    import sys

    source = tmp_path / 'concat_reference.py'
    source.write_text(CONCAT_PROGRAM)
    ran = subprocess.run([sys.executable, str(source)], capture_output=True, text=True, timeout=20)
    assert ran.returncode == 0, ran.stderr
    assert ran.stdout == 'ROOTED_LIST_CONCAT_OK\n'


@pytest.mark.integration
def test_concat_existing_roots_native_all_gc(tmp_path, monkeypatch, threaded_pcc_runtime_archive):
    import os
    import subprocess

    from pcc.frontends.python.pipeline import compile_python

    monkeypatch.setenv('PCC_WITH_THREADS', '1')
    monkeypatch.delenv('PCC_PYTHON_IR_PASSES', raising=False)
    source = tmp_path / 'concat_owned.py'
    source.write_text(CONCAT_PROGRAM)
    binary = source.with_suffix('')
    compile_python(str(source), str(binary), backend='self', libpython_mode='off',
                   ir_scaffold_mode='on', runtime_archive=str(threaded_pcc_runtime_archive))
    for backend in range(5):
        ran = subprocess.run([str(binary)], capture_output=True, text=True, timeout=40,
                             env=dict(os.environ, PATH='/nonexistent', PCC_GC_BACKEND=str(backend),
                                      PCC_GC_REFCOUNT_PROVENANCE_PROBE='2'))
        (tmp_path / ('gc' + str(backend) + '.stdout')).write_text(ran.stdout)
        (tmp_path / ('gc' + str(backend) + '.stderr')).write_text(ran.stderr)
        assert ran.returncode == 0, (backend, ran.stdout, ran.stderr)
        assert ran.stdout == 'ROOTED_LIST_CONCAT_OK\n' and ran.stderr == ''
