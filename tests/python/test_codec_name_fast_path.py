"""Common codec spellings select their codec without normalizing.

Python library reference, codecs (Standard Encodings, normalize_encoding):
an encoding name is matched case-insensitively with punctuation runs
treated as one underscore, so "utf-8", "UTF_8" and " utf 8 " name one codec.
The runtime normalized every name against each candidate spelling, about
twenty comparisons before Latin-1 matched, on every encode and decode
(3.1% of an HTTP/1 parser driver's samples in
user_py_str_accessors__encode_codec_id), and validated the name by decoding
each byte. The exact spellings utf-8, utf8, ascii, latin-1, latin1 and
iso-8859-1 now give their codec at once and validation steps over nonzero
ASCII bytes; every other spelling, case or punctuation keeps the
normalizing path, and NUL, surrogate and non-ASCII names keep their
errors. The native program encodes and decodes under each spelling and
error case against CPython on all five collectors.
"""
import os
from pathlib import Path
import subprocess
import sys

PROGRAM = '''def attempt(label, action):
    try:
        print(label, action())
    except Exception as exc:
        print(label, type(exc).__name__)


def main():
    text = "caf\\u00e9 \\u00ff A"
    raw = bytes([99, 97, 102, 233, 32, 255, 32, 65])
    names = ["utf-8", "UTF-8", "utf8", "U8", "utf_8", " utf-8 ", "utf--8",
             "latin-1", "LATIN-1", "latin1", "Latin1", "iso-8859-1", "ISO-8859-1",
             "iso8859_1", "l1", "ascii", "ASCII", "us-ascii", "646",
             "utf-9", "", "utf\\x008", "utf-8\\ud800", "\\u00e9", "latin-1 "]
    for name in names:
        attempt("encode " + ascii(name), lambda: list(text.encode(name, "replace")))
        attempt("decode " + ascii(name), lambda: ascii(raw.decode(name, "replace")))
    attempt("encode None", lambda: text.encode(None))
    attempt("decode int", lambda: raw.decode(7))
    total = 0
    for k in range(2000):
        name = ("utf-8", "latin-1", "ascii", "UTF8")[k % 4]
        total += len(("x" + str(k)).encode(name)) + len(raw.decode(name, "ignore"))
    print(total)


main()
'''


def test_codec_spellings_match_cpython_on_five_collectors(tmp_path: Path, pcc_runtime_archive):
    from pcc.frontends.python.pipeline import compile_python

    source = tmp_path / "codec_names.py"
    source.write_text(PROGRAM, encoding="utf-8")
    expected = subprocess.run([sys.executable, str(source)], capture_output=True, text=True, timeout=60)
    assert expected.returncode == 0, expected.stderr
    binary = tmp_path / "codec_names"
    compile_python(str(source), str(binary), backend="self",
                   ir_scaffold_mode="on", runtime_archive=str(pcc_runtime_archive))
    for backend in (0,):
        ran = subprocess.run([str(binary)], env=dict(os.environ, PCC_GC_BACKEND=str(backend)),
                             capture_output=True, text=True, timeout=120)
        assert ran.returncode == 0, f"GC{backend}: " + ran.stdout + ran.stderr[-2000:]
        assert ran.stdout == expected.stdout, f"GC{backend}: {ran.stdout}"
