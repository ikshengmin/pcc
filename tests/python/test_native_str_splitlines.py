"""Native str.splitlines content and boundary parity against CPython."""

from __future__ import annotations

import subprocess
import sys



_SEPARATORS = (
    "\n", "\r", "\r\n", "\v", "\f", "\x1c", "\x1d", "\x1e",
    "\x85", "\u2028", "\u2029",
)
# Whitespace is not generally a line boundary. Include Unicode code points
# whose UTF-8 encodings share bytes with an actual separator, and NUL.
_NEAR_MISSES = " \t\x00\x1b\x1f\x84\x86\xa0\xc5\u2027\u202a\u2038\u2228"
_CASES = (
    "", "ascii", "中文😀", _NEAR_MISSES, "前" + _NEAR_MISSES + "后",
    "\r\r\n\n", "\n\r\n", "\r\x85\n", "\x85\r\n\u2028",
    "".join(_SEPARATORS),
    "前" + "".join(_SEPARATORS) + "\x00后",
    *(
        case
        for sep in _SEPARATORS
        for case in (
            sep, sep + sep, "前😀" + sep, sep + "中文",
            "前\x00😀" + sep + "中文后", "前" + sep + sep + "后",
            _NEAR_MISSES + sep + _NEAR_MISSES,
        )
    ),
)

# Print numeric code points rather than repr/terminal Unicode. List shape,
# each fragment length and every scalar (including NUL) remain observable.
_PROBE = '''\
def emit(items: list[str]) -> None:
    print(len(items))
    for item in items:
        print([ord(c) for c in item])

def probe(text: str) -> None:
    emit(text.splitlines())
    emit(text.splitlines(False))
    emit(text.splitlines(True))
    emit(text.splitlines(keepends=False))
    emit(text.splitlines(keepends=True))

def main() -> None:
    for text in CASES:
        probe(text)

main()
'''


def test_native_splitlines_content_matches_cpython(
    tmp_path, pcc_runtime_archive, python_program_compiler,
):
    source = tmp_path / "splitlines_probe.py"
    source.write_text("CASES = " + repr(_CASES) + "\n" + _PROBE, encoding="utf-8")
    expected = subprocess.run(
        [sys.executable, str(source)], capture_output=True, timeout=30,
    )
    assert expected.returncode == 0, expected.stderr
    assert expected.stderr == b""
    executable = tmp_path / "splitlines_probe"
    python_program_compiler(
        str(source), str(executable), backend="self", ir_scaffold_mode="on",
        runtime_archive=str(pcc_runtime_archive),
    )
    actual = subprocess.run(
        [str(executable)], capture_output=True, timeout=60,
    )
    assert (actual.returncode, actual.stderr) == (0, b"")
    assert actual.stdout == expected.stdout
