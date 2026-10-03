"""Dictionary lookup owns its selected result across branch and cleanup paths."""
from __future__ import annotations

import ast
from pathlib import Path
import re

import pytest

from pcc.runtime.py import py_abi_constants as abi
from tests.python.test_shared_call_binding import _emit
from tests.python.test_dict_super_slot_roots import DictMemory
from tests.python.test_set_call_slot_roots import Object


@pytest.mark.parametrize('annotation', ('', ': dict'))
@pytest.mark.parametrize('arguments', ('key', 'key, fallback'))
@pytest.mark.parametrize('site', ('return', 'argument', 'default', 'binary'))
def test_dict_get_branches_publish_same_caller_owner(annotation, arguments, site, tmp_path):
    expression = 'mapping.get(' + arguments + ')'
    prefix = 'def take(*, value):\n    return value\n'
    if site == 'return':
        body = '    return ' + expression + '\n'
    elif site == 'default':
        body = '    def saved(value=' + expression + '):\n        return value\n    return saved()\n'
    elif site == 'binary':
        body = '    return ' + expression + ' + (1,)\n'
    else:
        body = '    return take(value=' + expression + ')\n'
    source = prefix + 'def probe(mapping' + annotation + ', key, fallback):\n' + body
    text = _emit(source)
    (tmp_path / 'input.py').write_text(source)
    (tmp_path / 'program.ll').write_text(text)
    native = re.findall(r'^  %dict\.get\.invoke[^ ]* = call [^\n]*@py_dict_get_default_slots\(([^\n]*)\)', text, re.M)
    generic = re.findall(r'^  %dict\.get\.generic\.invoke[^ ]* = call [^\n]*@py_obj_call_slots\(([^\n]*)\)', text, re.M)
    assert native and len(native) == len(generic)
    casts = dict(re.findall(r'^  (%[^ ]+) = (?:bitcast|addrspacecast) [^ ]+ (%[^ ]+) to [^\n]+$', text, re.M))
    def owner(row):
        value = row.rsplit(',', 1)[-1].strip().rsplit(' ', 1)[-1]
        seen = set()
        while value in casts:
            assert value not in seen
            seen.add(value)
            value = casts[value]
        return value
    assert [owner(row) for row in native] == [owner(row) for row in generic]
    assert not re.search(r'%dyn\.dict\.result[^\n]* = phi ptr', text)
    assert '@pcc_gc_root_copy_lease(' in text


class _GetMemory(DictMemory):
    def __init__(self, phase='register', **kwargs):
        super().__init__(phase, **kwargs)
        path = Path(__file__).parents[2] / 'pcc/runtime/py/py_dict.py'
        tree = ast.parse(path.read_text())
        for node in tree.body:
            if (isinstance(node, ast.Assign) and len(node.targets) == 1
                    and isinstance(node.targets[0], ast.Name)
                    and node.targets[0].id == '_DICT_SLOT_GET_ONLY'):
                self.ns[node.targets[0].id] = ast.literal_eval(node.value)
        names = {'_dict_slot_set_core', '_dict_slot_set_bound', 'py_dict_get_default_slots'}
        body = [node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name in names]
        assert len(body) == 3
        for node in body:
            node.decorator_list = []
        exec(compile(ast.Module(body=body, type_ignores=[]), str(path), 'exec'), self.ns)

    def get(self, values, key, default):
        target, query, fallback = self.wrap(values), self.wrap(key), self.wrap(default)
        caller = self.input_roots((target, query, fallback, None))
        before = self.unwrap(self.read(caller, 0))
        status = self.ns['py_dict_get_default_slots'](caller, self.add(caller, 8), self.add(caller, 16), self.add(caller, 24))
        result = self.read(caller, 24)
        assert len(self.roots) == 4 and self.depth == 0
        assert all(obj.leases == (1000 if obj is self.none else 0) for obj in self.objects if obj.alive)
        assert self.unwrap(self.read(caller, 0)) == before
        return status, result, caller


@pytest.mark.parametrize('phase', ('register', 'copy', 'acquire', 'callback', 'release', 'move', 'drop', 'unregister', 'lock'))
@pytest.mark.parametrize('hit', (False, True))
def test_actual_get_result_owner_moves_without_mutation(phase, hit):
    memory = _GetMemory(phase)
    status, result, caller = memory.get({'present': 'selected'}, 'present' if hit else 'missing', 'fallback')
    assert status == 0 and memory.error is None
    assert memory.unwrap(result) == ('selected' if hit else 'fallback')
    assert isinstance(result, Object) and result.alive and result.refs > 0
    if not hit:
        assert result is memory.read(caller, 16)
    assert memory.collections > 0


@pytest.mark.parametrize('failure', (1, 2, 3))
def test_actual_get_copy_error_leaves_output_empty(failure):
    memory = _GetMemory('copy', fail_copy=failure)
    status, result, _ = memory.get({'present': 'selected'}, 'present', 'fallback')
    assert status == -1 and result is None and memory.error is not None


def test_actual_get_does_not_grow_full_storage_on_miss():
    memory = _GetMemory('none')
    target = memory.wrap({})
    target.fields[abi.PYDICTOBJECT_ENTRIES_USED_OFFSET] = target.fields[abi.PYDICTOBJECT_CAPACITY_OFFSET]
    caller = memory.input_roots((target, memory.wrap('missing'), memory.wrap('fallback'), None))
    memory.ns['_maybe_grow'] = lambda owner: pytest.fail('read-only get attempted growth')
    status = memory.ns['py_dict_get_default_slots'](caller, memory.add(caller, 8), memory.add(caller, 16), memory.add(caller, 24))
    assert status == 0 and memory.read(caller, 24) is memory.read(caller, 16)
    assert memory.unwrap(memory.read(caller, 0)) == {}


def test_actual_get_hash_error_preserves_error_and_empty_output():
    memory = _GetMemory('callback')
    memory.ns['py_obj_hash'] = lambda key: setattr(memory, 'error', (2, 'hash-error')) or 17
    status, result, _ = memory.get({}, 'key', 'fallback')
    assert status == -1 and result is None and memory.error == (2, 'hash-error')
