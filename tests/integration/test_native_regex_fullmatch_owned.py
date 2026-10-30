"""Require a source-matched archive; never silently build or substitute one."""
import os
from pathlib import Path

import pytest

from tests.python.conftest import native_pcc1_compiler, python_program_compiler
from tests.python.owned_regression_support import explicit_owned_runtime
from tests.python.process_timeout import run_process_group_timeout


@pytest.mark.integration
@pytest.mark.parametrize('python_program_compiler', ('pcc0', 'pcc1'), indirect=True)
def test_guarded_regex_fullmatch_threads_and_all_collectors(
    tmp_path, python_program_compiler, explicit_owned_runtime,
):
    fixture = Path(__file__).parents[1] / 'fixtures/native/regex_fullmatch_owned.py'
    source = tmp_path / fixture.name
    # This capability boundary intentionally differs from CPython: native
    # per-instance fullmatch currently rejects keywords instead of dropping them.
    source.write_text(fixture.read_text() + "\ntry:\n    re.compile('a').fullmatch(string='a')\nexcept TypeError as error:\n    assert str(error) == 'native function does not accept keywords'\nelse:\n    raise AssertionError('unsupported keyword silently accepted')\n")
    executable = tmp_path / 'regex_fullmatch_owned'
    python_program_compiler(str(source), str(executable), backend='self',
                            libpython_mode='off', ir_scaffold_mode='on',
                            runtime_archive=str(explicit_owned_runtime))
    for backend in range(5):
        environment = dict(os.environ, PCC_GC_BACKEND=str(backend), PATH='',
                           PCC_HOST_PYTHON='/nonexistent/host-python')
        result = run_process_group_timeout([str(executable)], env=environment, timeout=20)
        assert result.returncode == 0, (backend, result.stdout, result.stderr)
        assert result.stdout == 'REGEX_FULLMATCH_OK\n', (backend, result.stdout)
        assert result.stderr == '', (backend, result.stderr)
