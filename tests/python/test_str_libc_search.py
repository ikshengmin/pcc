"""String equality, search and module-name matching over the C library.

``py_str_eq`` and ``_byte_find`` (``in``, ``find`` and their users) compare
with ``memcmp`` and find each candidate first byte with ``memchr``, and the
module attribute name cache matches a C-string name with ``strlen`` and
``memcmp``, instead of compiled byte loops. This runs those paths on every
collector against CPython: repeated prefixes, first and last positions,
needles longer than the text, empty strings, multi-byte UTF-8, strings built
at run time (distinct objects, so equality reads bytes, not identity), dict
keys found by such strings, and module attributes whose names differ in
length or in their last byte.
"""
from __future__ import annotations

import os
import subprocess
import sys
import textwrap

PROGRAM = textwrap.dedent("""
    import json
    import math
    import time


    def built(*parts: str) -> str:
        return "".join(parts)


    def main() -> None:
        s = built("abc", "abc", "abd")
        for needle in ("abd", "abc", "d", "x", "abcabcabdX", "", "cab", "bd"):
            print(repr(needle), needle in s, s.find(needle), s.rfind(needle), s.count(needle))
        print("aaaaab".find("aab"), "aaaaab".find("ab"), "aaaaab".find("b"), "aaaaab".find("ba"))
        u = built("h", "é", "llo w", "ö", "rld ", "日本語テキスト", " a😀b😀c")
        for needle in ("ö", "wö", "テキ", "😀c", "😀", "é", "語テ", "ä", "c"):
            print(repr(needle), needle in u, u.find(needle), u.count(needle))
        long_text = "x" * 1000 + "y"
        print("xy" in long_text, long_text.find("xy"), long_text.find("yx"), long_text.index("y"))
        print(s.replace("ab", "-"), s.split("b"), u.split("😀"), u.replace("ö", "o"))
        try:
            print(s.index("zz"))
        except ValueError:
            print("ValueError")
        pairs = [("abc", built("a", "bc")), ("abc", built("a", "bd")), ("", built()),
                 ("é", built("é")), ("é", built("e")), ("abc", built("ab")),
                 ("日本語", built("日本", "語")), ("😀", built("😀"))]
        for left, right in pairs:
            print(repr(left), repr(right), left == right, left != right)
        table = {"name": 1, "names": 2, "nam": 3, "plan": 4, "テキ": 5}
        for key in (built("na", "me"), built("na", "mes"), built("na", "m"),
                    built("pl", "an"), built("テ", "キ"), built("pla", "m")):
            print(repr(key), table.get(key))
        print(json.dumps({"b": [1, "é"], "a": "q\\"r"}, sort_keys=True))
        print(time.perf_counter() > 0.0, math.floor(2.5), math.pi > 3.0, math.sqrt(16.0))


    if __name__ == "__main__":
        main()
    """).lstrip()


def test_str_search_equality_and_module_names_match_cpython(tmp_path):
    from pcc.frontends.python.pipeline import compile_python

    source = tmp_path / "strsearch.py"
    executable = tmp_path / "strsearch.out"
    source.write_text(PROGRAM, encoding="utf-8")
    compile_python(str(source), str(executable), ir_scaffold_mode="on", backend="self")
    reference = subprocess.run(
        [sys.executable, str(source)], capture_output=True, text=True, timeout=30,
    )
    assert reference.returncode == 0, reference.stderr
    for backend in range(5):
        result = subprocess.run(
            [str(executable)], capture_output=True, text=True, timeout=30,
            env=dict(os.environ, PCC_GC_BACKEND=str(backend)),
        )
        assert result.returncode == 0, (backend, result.stderr)
        assert result.stdout == reference.stdout, backend
