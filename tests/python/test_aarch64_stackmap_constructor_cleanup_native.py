"""Native extracted constructor ownership, not full planner/bootstrap coverage.

Uses exact production constructor and full fields plus real CompilerIntArena
malloc/free. Only entry fault/count hooks are added. Real allocator exhaustion,
native list-append/cleanup faults and full planner ABI are not claimed.
"""
import ast
import hashlib
import json
from pathlib import Path
from tests.python.owned_regression_support import assert_owned_program, explicit_owned_runtime

ROOT = Path(__file__).resolve().parents[2]

def sha(text):
    return hashlib.sha256(text.encode()).hexdigest()
def named(source, name):
    return next(n for n in ast.parse(source).body if isinstance(n, ast.ClassDef) and n.name == name)
def segment(source, node):
    return '\n'.join(source.splitlines()[node.lineno - 1:node.end_lineno]) + '\n'

DRIVER = "\nimport sys\nimport gc\n\nREFERENCE = len(sys.argv) == 2 and sys.argv[1] == '--reference'\nif not REFERENCE:\n    assert sys.implementation.name == 'pcc'\n\nclass InjectedCancellation(BaseException):\n    pass\n\n_armed = False\n_fail_at = 0\n_attempts = 0\n_cancel = False\n_owners: list[CompilerIntArena] = []\n_close_counts: list[int] = []\n\ndef _constructor_entry_hook(arena):\n    global _attempts\n    if not _armed:\n        return\n    _attempts += 1\n    # Failure is BEFORE the selected real constructor body/allocator.\n    # Earlier constructors returned normally. Managed references below only\n    # retain their ordinary arena objects; no raw address is read or stored.\n    if _attempts == _fail_at:\n        if _cancel:\n            raise InjectedCancellation('injected allocation boundary')\n        raise MemoryError('injected allocation boundary')\n    _owners.append(arena)\n    _close_counts.append(0)\n\ndef _close_entry_hook(arena):\n    if not _armed:\n        return\n    index = 0\n    while index < len(_owners):\n        if arena is _owners[index]:\n            _close_counts[index] += 1\n            assert _close_counts[index] == 1\n            assert not arena._closed\n            if not REFERENCE:\n                assert arena.uses_native_storage\n            return\n        index += 1\n    raise AssertionError('closing an untracked owner')\n\ndef one_case(fail_at: int, cancel: bool) -> None:\n    global _armed, _fail_at, _attempts, _cancel\n    _owners.clear()\n    _close_counts.clear()\n    _fail_at = fail_at\n    _cancel = cancel\n    _attempts = 0\n    names = ['entry', 'exit']\n    _armed = True\n    caught = False\n    try:\n        packed = PackedPlannedSafepoints(3, names)\n        assert fail_at == 0\n        assert packed.block_names is names\n        assert len(packed) == 0\n        actual = [packed.scalars, packed.spans, packed.location_scalars,\n                  packed.location_group_spans, packed.reload_scalars,\n                  packed.entry_routes, packed.suffix_routes, packed.terminator_routes,\n                  packed.entry_route_spans, packed.suffix_route_spans,\n                  packed.terminator_route_spans]\n        assert len(actual) == len(_owners) == 11\n        index = 0\n        while index < 11:\n            assert actual[index] is _owners[index]\n            assert _close_counts[index] == 0\n            assert not actual[index]._closed\n            if not REFERENCE:\n                assert actual[index].uses_native_storage\n            index += 1\n        gc.collect()\n        packed.close()\n    except MemoryError as error:\n        assert not cancel and fail_at != 0\n        assert str(error) == 'injected allocation boundary'\n        caught = True\n    except InjectedCancellation as error:\n        assert cancel and fail_at != 0\n        assert str(error) == 'injected allocation boundary'\n        caught = True\n    finally:\n        _armed = False\n    expected = 11 if fail_at == 0 else fail_at - 1\n    assert caught == (fail_at != 0)\n    assert _attempts == (11 if fail_at == 0 else fail_at)\n    assert len(_owners) == len(_close_counts) == expected\n    index = 0\n    while index < expected:\n        assert _close_counts[index] == 1\n        assert _owners[index]._closed\n        # Observe only the managed closed flag, not a freed native pointer.\n        index += 1\n    assert names == ['entry', 'exit']\n    gc.collect()\n\none_case(0, False)\nfailure = 2\nwhile failure <= 11:\n    one_case(failure, False)\n    one_case(failure, True)\n    failure += 1\nprint('PLAN_CONSTRUCTOR_NATIVE_BOUNDARY_OK')\n"

def _program():
    arena_source = (ROOT / 'pcc/backend/self_backend_value_arena.py').read_text()
    maps_source = (ROOT / 'pcc/backend/self_backend_precise_stackmaps.py').read_text()
    stop = named(arena_source, 'CompilerRecordSpanArena').lineno - 1
    arena = '\n'.join(arena_source.splitlines()[:stop]) + '\n'
    node = named(arena, 'CompilerIntArena')
    hooks = []
    for method, hook in [('__init__', '_constructor_entry_hook'), ('close', '_close_entry_hook')]:
        method_node = next(n for n in node.body if isinstance(n, ast.FunctionDef) and n.name == method)
        hooks.append((method_node.body[0].lineno - 1, '        ' + hook + '(self)\n'))
    lines = arena.splitlines(keepends=True)
    for offset, line in sorted(hooks, reverse=True):
        lines.insert(offset, line)
    instrumented = ''.join(lines)
    restored = instrumented
    for _, line in hooks:
        assert restored.count(line) == 1
        restored = restored.replace(line, '', 1)
    assert restored == arena
    packed = named(maps_source, 'PackedPlannedSafepoints')
    first = next(n for n in packed.body if isinstance(n, ast.FunctionDef))
    header = '\n'.join(maps_source.splitlines()[packed.lineno - 1:first.lineno - 1]) + '\n'
    methods = {name: segment(maps_source, next(n for n in packed.body if isinstance(n, ast.FunctionDef) and n.name == name))
               for name in ('__init__', '__len__', 'close')}
    source = instrumented + '\n' + header + '\n'.join(methods.values()) + DRIVER
    ast.parse(source)
    receipt = {'constructor_sha256': sha(methods['__init__']),
               'arena_sha256': sha(arena), 'source_sha256': sha(source),
               'scope': 'extracted constructor, full fields, real arena; not full planner'}
    return source, receipt


def test_constructor_cleanup_native(
    request, python_program_compiler, explicit_owned_runtime, tmp_path, capfd,
):
    source, receipt = _program()
    (tmp_path / 'constructor-source.json').write_text(json.dumps(receipt, indent=2) + '\n')
    mode = request.node.callspec.params['python_program_compiler']
    assert_owned_program(source, 'PLAN_CONSTRUCTOR_NATIVE_BOUNDARY_OK\n', tmp_path,
                         python_program_compiler, mode, explicit_owned_runtime, capfd,
                         reference_arguments=('--reference',), provenance_probe='2')
