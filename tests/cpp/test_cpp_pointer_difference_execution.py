"""Same-original C++17 pointer subtraction through owned and reference binaries."""

import pytest

from tests.cpp.sizeof_execution import execute_public_row_pair, execute_row_pair
from tests.cpp.test_cpp_pointer_difference import PROGRAMS


@pytest.mark.integration
@pytest.mark.parametrize('name,units', PROGRAMS, ids=[row[0] for row in PROGRAMS])
def test_pointer_difference_originals_execute_against_cpp17(name, units, tmp_path, monkeypatch):
    execute_row_pair(tmp_path, monkeypatch, units)


@pytest.mark.integration
@pytest.mark.parametrize('opt', (0, 2))
def test_public_pointer_difference_matches_cstddef_and_signed_stride(opt, tmp_path, monkeypatch):
    source = (
        '#include <cstddef>\n'
        'int pick(std::ptrdiff_t n){if(n==-3)return 42;return 1;}'
        'int pick(long long n){return 2;}'
        'int main(){int a[4][3]{};return pick(a-(a+3));}'
    )
    execute_public_row_pair(source, opt, tmp_path, monkeypatch)
