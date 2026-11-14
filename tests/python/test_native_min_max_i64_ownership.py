"""Fixed-width iterable folds obey min/max's empty and ownership contracts.

Python Library Reference: built-in functions min/max. Explicit pcc.i64
elements retain the scalar fold; ordinary int containers remain object folds.
"""
import os
import re
import textwrap

import pytest

from pcc.frontends.python.codegen.layer1 import L1CodeGen
from pcc.frontends.python.py_lift import parse_and_lift
from pcc.frontends.python.type_infer import infer_module
from tests.python.owned_regression_support import (
    assert_owned_program,
    assert_reference_program,
    explicit_owned_runtime,
)
from tests.python.process_timeout import run_process_group_timeout


FUNCTIONS = textwrap.dedent('''\
    from pcc import i64
    def list_min(values: list[i64]) -> i64:
        return min(values)
    def list_max(values: list[i64]) -> i64:
        return max(values)
    def tuple_min(values: tuple[i64, i64]) -> i64:
        return min(values)
    def tuple_max(values: tuple[i64, i64]) -> i64:
        return max(values)
    def singleton(values: tuple[i64]) -> i64:
        return min(values) + max(values)
    def defaults(values: list[i64]):
        return min(values, default=71), max(values, default=-71)
    def tuple_defaults(values: tuple[i64, i64]):
        return min(values, default=71), max(values, default=-71)
''')


PROGRAM = FUNCTIONS + textwrap.dedent('''\
    import gc
    def main():
        low = int('-9223372036854775808')
        high = int('9223372036854775807')
        # pcc.i64 converts an ordinary int only explicitly: the fixed-width
        # results compare with fixed-width expectations.
        low_i64: i64 = i64(low)
        high_i64: i64 = i64(high)
        assert list_min([high, low, high]) == low_i64
        assert list_max([low, high, low]) == high_i64
        assert tuple_min((high, low)) == low_i64
        assert tuple_max((low, high)) == high_i64
        assert list_min([high]) == high_i64
        assert list_max([low]) == low_i64
        assert singleton((7,)) == 14
        assert defaults([]) == (71, -71)
        assert defaults([3]) == (3, 3)
        assert tuple_defaults(()) == (71, -71)
        assert tuple_defaults((3, 5)) == (3, 5)
        # The argument annotation selects the fixed-width tuple fold even
        # when a caller supplies an empty tuple at runtime.
        for repeat in range(4):
            try:
                list_min([])
            except ValueError as error:
                assert str(error) == 'min() iterable argument is empty'
            else:
                raise AssertionError('empty list min')
            try:
                list_max([])
            except ValueError as error:
                assert str(error) == 'max() iterable argument is empty'
            else:
                raise AssertionError('empty list max')
            try:
                tuple_min(())
            except ValueError as error:
                assert str(error) == 'min() iterable argument is empty'
            else:
                raise AssertionError('empty tuple min')
            try:
                tuple_max(())
            except ValueError as error:
                assert str(error) == 'max() iterable argument is empty'
            else:
                raise AssertionError('empty tuple max')
            gc.collect()
            assert list_max([high]) == high_i64
        print('I64_MIN_MAX_OK')
    main()
''')


@pytest.mark.parametrize('name', ('min', 'max'))
@pytest.mark.parametrize('annotation', ('list[i64]', 'tuple[i64, i64]'))
def test_i64_fold_publishes_each_item_before_cleanup(name, annotation):
    source = ('from pcc import i64\ndef probe(values: ' + annotation + ') -> i64:\n'
              '    return ' + name + '(values)\n')
    module = infer_module(parse_and_lift(source, 'i64_fold.py', 'i64_fold'))
    codegen = L1CodeGen(module, ir_scaffold_mode='on')
    codegen._strict_no_libpython = True
    codegen._prefer_native_callable_values = True
    text = str(codegen.generate(module))
    body = re.search(r'^define [^\n]*@user_i64_fold_probe\([^\n]*\).*?^}',
                     text, re.M | re.S)
    assert body is not None
    body = body.group(0)
    calls = list(re.finditer(
        r'^  (%[^ ]+) = call [^\n]*@py_obj_getitem_i64\([^\n]*\)\n', body, re.M,
    ))
    assert len(calls) == 2  # seed and loop, never the ordinary object route
    for call in calls:
        assert body[call.end():].splitlines()[0].lstrip().startswith(
            'store ptr ' + call.group(1) + ', ptr '
        )
    assert '@py_obj_min_max(' not in body
    assert '@py_obj_getitem(' not in body
    assert '@py_int_to_i64_lane(' in body
    assert '@pcc_gc_foreign_lease_release(' in body
    assert '@pcc_gc_store_root(' in body


def test_i64_min_max_reference(tmp_path):
    assert_reference_program(PROGRAM, 'I64_MIN_MAX_OK\n', tmp_path)


@pytest.mark.integration
@pytest.mark.parametrize('python_program_compiler', ('pcc0', 'pcc1'), indirect=True)
def test_i64_min_max_five_gc(python_program_compiler, request,
                            explicit_owned_runtime, tmp_path, capfd):
    mode = request.node.callspec.params['python_program_compiler']
    assert_owned_program(PROGRAM, 'I64_MIN_MAX_OK\n', tmp_path,
                         python_program_compiler, mode, explicit_owned_runtime, capfd,
                         provenance_probe='2')


OWNER_PROGRAM = FUNCTIONS + textwrap.dedent('''\
    import gc
    from pcc.extern import c_int64, extern
    live_bytes = extern('pcc_os_heap_in_use_bytes', (), c_int64)
    actual_backend = extern('pcc_gc_backend', (), c_int64)
    def check_unbox_errors():
        # Runtime container elements can violate their static annotation.
        # Both seed and loop conversion errors must unwind item and source.
        huge = int('1267650600228229401496703205376')
        try:
            list_min([huge, 1])
        except OverflowError:
            pass
        else:
            raise AssertionError('seed conversion error lost')
        try:
            list_max([1, huge])
        except OverflowError:
            pass
        else:
            raise AssertionError('loop conversion error lost')
        try:
            tuple_min((huge, 1))
        except OverflowError:
            pass
        else:
            raise AssertionError('tuple seed conversion error lost')
        try:
            tuple_max((1, huge))
        except OverflowError:
            pass
        else:
            raise AssertionError('tuple loop conversion error lost')
        assert list_max([7]) == 7
    def exercise():
        # Parse fresh boxed integers beyond the tagged lane on every pass.
        # A missing release in either the seed or loop retains their storage.
        high = int('9223372036854775807')
        low = int('-9223372036854775808')
        low_i64: i64 = i64(low)
        high_i64: i64 = i64(high)
        assert list_min([high, low]) == low_i64
        assert list_max([low, high]) == high_i64
        assert tuple_min((high, low)) == low_i64
        assert tuple_max((low, high)) == high_i64
        try:
            list_min([])
        except ValueError:
            pass
        try:
            tuple_max(())
        except ValueError:
            pass
    def scan():
        for index in range(512):
            exercise()
            check_unbox_errors()
        gc.collect()
        gc.collect()
    def main():
        scan()
        before = live_bytes()
        scan()
        middle = live_bytes()
        scan()
        after = live_bytes()
        print(actual_backend(), middle - before, after - middle)
    main()
''')


@pytest.mark.integration
@pytest.mark.parametrize('python_program_compiler', ('pcc0', 'pcc1'), indirect=True)
def test_i64_min_max_releases_boxed_items(python_program_compiler,
                                        explicit_owned_runtime, tmp_path, capfd):
    source = tmp_path / 'i64_min_max_owners.py'
    source.write_text(OWNER_PROGRAM)
    binary = tmp_path / 'i64_min_max_owners'
    python_program_compiler(str(source), str(binary), backend='self',
                            ir_scaffold_mode='on', runtime_archive=str(explicit_owned_runtime))
    captured = capfd.readouterr()
    (tmp_path / 'compiler.stdout').write_text(captured.out)
    (tmp_path / 'compiler.stderr').write_text(captured.err)
    for backend in range(5):
        result = run_process_group_timeout(
            [str(binary)], timeout=10,
            env=dict(os.environ, PATH='', PCC_GC_BACKEND=str(backend),
                     PCC_GC_REFCOUNT_PROVENANCE_PROBE='2'),
        )
        (tmp_path / ('gc' + str(backend) + '.stdout')).write_text(result.stdout)
        (tmp_path / ('gc' + str(backend) + '.stderr')).write_text(result.stderr)
        assert result.returncode == 0, (backend, result.stdout, result.stderr)
        observed_backend, first, second = map(int, result.stdout.split())
        assert observed_backend == backend, (backend, result.stdout, result.stderr)
        if observed_backend == 0:
            # Count-free collectors need not immediately return allocator
            # pages. GC0 must retire every NEW item and temporary container.
            assert first < 16384 and second < 16384, (first, second)
