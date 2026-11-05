"""Same-original member varargs execution and independent real C++17 oracle.

Owned preprocessing uses PCC's actual ABI header; g++ receives the unchanged
original and uses its own standard header. No oracle header or native tool is
substituted into PCC compilation. Preserve both outcomes before asserting.
"""
import os
import shutil
import traceback

import pytest

from tests.cpp.owned_execution import (
    Execution, assert_exact, compile_host_cpp, compile_reference_cpp,
    write_host_executable,
)
from tests.cpp.sizeof_execution import capture_row_execution, execute_public_row_pair
from tests.cpp.test_cpp_constructor_direct_list_execution import syntax_reference
from tests.cpp.test_cpp_variadic_members import (
    DEFERRED, INVALID, PROGRAMS, PUBLIC_SOURCE, owned_units,
)


@pytest.mark.integration
@pytest.mark.parametrize('name,units', PROGRAMS, ids=[row[0] for row in PROGRAMS])
def test_variadic_members_execute_against_real_cpp17(name, units, tmp_path, monkeypatch):
    originals = tmp_path/'originals'
    originals.mkdir()
    for filename, source in units:
        (originals/filename).write_text(source)
    first = second = actual = owned_error = None
    try:
        with monkeypatch.context() as guard:
            def forbidden(*args, **kwargs):
                raise AssertionError('owned variadic-member compilation invoked an external build tool')
            guard.setattr('subprocess.Popen', forbidden)
            first = compile_host_cpp(owned_units(units))
            second = compile_host_cpp(owned_units(units))
        for index, (ir, obj) in enumerate(zip(first.ir, first.objects)):
            (tmp_path/f'owned-{index}.ll').write_text(ir)
            (tmp_path/f'owned-{index}.o').write_bytes(obj)
        binary = tmp_path/'owned'
        write_host_executable(first, binary)
        actual = capture_row_execution(binary)
    except Exception as error:
        owned_error = error
        (tmp_path/'owned-error.txt').write_text(traceback.format_exc())
    compiler = shutil.which(os.environ.get('PCC_CPP_ORACLE', 'g++'))
    assert compiler, 'an actual C++17 reference compiler is required'
    try:
        reference = compile_reference_cpp(units, tmp_path/'reference', compiler=compiler)
        observed_reference = capture_row_execution(reference)
    except Exception:
        (tmp_path/'reference-error.txt').write_text(traceback.format_exc())
        raise
    if owned_error is not None:
        raise owned_error
    assert first == second
    expected = Execution(42, b'\x2a\x00\x00\x00', b'')
    assert_exact(actual, expected)
    assert_exact(observed_reference, expected)


@pytest.mark.integration
@pytest.mark.parametrize('opt', (0, 2))
def test_public_member_varargs_keep_hidden_this_abi(opt, tmp_path, monkeypatch):
    execute_public_row_pair(PUBLIC_SOURCE, opt, tmp_path, monkeypatch)


@pytest.mark.integration
@pytest.mark.parametrize('name,source,code', INVALID, ids=[row[0] for row in INVALID])
def test_invalid_member_varargs_are_rejected_by_reference(name, source, code, tmp_path):
    assert syntax_reference(tmp_path, name, source).returncode != 0


@pytest.mark.integration
@pytest.mark.parametrize('name,source,code', DEFERRED, ids=[row[0] for row in DEFERRED])
def test_deferred_member_varargs_remain_valid_cpp17(name, source, code, tmp_path):
    result = syntax_reference(tmp_path, name, source)
    assert result.returncode == 0, result.stderr
