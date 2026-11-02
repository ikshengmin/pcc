"""Same-original member Pattern / C1 native versus real C++17 obligations."""
import pytest
from tests.cpp.sizeof_execution import execute_row_pair
from tests.cpp.test_cpp_class_template_members import PROGRAMS


@pytest.mark.integration
@pytest.mark.parametrize('name,units', PROGRAMS, ids=[row[0] for row in PROGRAMS])
def test_member_pattern_originals_match_real_cpp17(name, units, tmp_path, monkeypatch):
    execute_row_pair(tmp_path, monkeypatch, units)


@pytest.mark.integration
def test_tu_local_different_stride_original_has_real_reference_acceptance(tmp_path):
    import os
    import shutil
    from tests.cpp.owned_execution import Execution, assert_exact, compile_reference_cpp, run_executable
    from tests.cpp.test_cpp_class_template_members import TU_LOCAL_PROGRAM
    compiler = shutil.which(os.environ.get('PCC_CPP_ORACLE', 'g++'))
    assert compiler, 'an explicit real C++17 reference compiler is required'
    binary = compile_reference_cpp(TU_LOCAL_PROGRAM, tmp_path / 'reference', compiler=compiler)
    assert_exact(run_executable(binary), Execution(42, b'\x2a\x00\x00\x00', b''))
