"""Unpacking Python integers preserves their object projection until storage."""

import subprocess
import sys


PROGRAM = '''def main():
    big = 2**80 + 11
    negative = -(2**90 + 22)
    values = [big, negative]
    left, right = values
    assert left == big and right == negative
    [left, right] = values
    assert left == big and right == negative
    pair = (big, negative)
    left, right = pair
    assert left == big and right == negative
    members = {big, negative}
    left, right = members
    assert {left, right} == members
    extended = [big, 0, negative]
    first, *middle, last = extended
    assert first == big and middle == [0] and last == negative
    first, *tail = values
    assert first == big and tail == [negative]
    *head, last = values
    assert head == [big] and last == negative
    print(big, negative)

main()
'''


def test_unpacked_container_integers_keep_arbitrary_precision(
    tmp_path, python_program_compiler, pcc_runtime_archive,
):
    source = tmp_path / 'unpack_integers.py'
    source.write_text(PROGRAM, encoding='utf-8')
    reference = subprocess.run([sys.executable, str(source)], capture_output=True,
                               text=True, check=True, timeout=30)
    binary = tmp_path / 'unpack_integers'
    python_program_compiler(
        str(source), str(binary), backend='self', libpython_mode='off',
        runtime_archive=str(pcc_runtime_archive),
    )
    result = subprocess.run([str(binary)], capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr
    assert result.stdout == reference.stdout
