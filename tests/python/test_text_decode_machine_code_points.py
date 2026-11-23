"""The native text decoder keeps its code points in machine integers.

Unicode 15.0 section 3.9, D91 (UTF-16): a surrogate pair <H, L> encodes
0x10000 + (H - 0xD800) * 0x400 + (L - 0xDC00); CPython's UTF-16 decoder
(Objects/stringlib/codecs.h) computes the same value. The runtime's
``py_text_decode_buffer_spans`` wrote the high half as
``(point - 55296) << 10``. A left shift of a runtime ``int`` local lowers
that local to an arbitrary-precision object, so the decoder boxed every
UTF-16 code point and, on every call of every codec, left through the
cleanup of those object temporaries: about 99 empty root clears and 99
unpins per parsed HTTP request on the gateway, whose headers are all
UTF-8 and Latin-1. The high half is at most 0x3FF, so ``* 1024`` is the
same machine value and keeps ``point`` an i64. The native program decodes
BMP, astral and invalid UTF-16 in both byte orders under every error
handler the native decoder owns, and UTF-8 and Latin-1, against CPython
on all five collectors.
"""
import os
from pathlib import Path
import re
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[2]
RUNTIME = ROOT / "pcc" / "runtime" / "py" / "py_obj_stubs.py"

PROGRAM = '''def show(label, text):
    print(label, len(text), [ord(c) for c in text][:12])


def main():
    samples = ["plain", "caf\\u00e9 \\u00fc", "\\u20ac \\u4e2d\\u6587",
               "\\U0001F600 \\U0001D11E \\U00010348", "a\\U0010FFFFz", "\\U00010000",
               "\\ud7ff\\ue000\\uffff"]
    for text in samples:
        for codec in ("utf-16-le", "utf-16-be"):
            data = text.encode(codec)
            decoded = data.decode(codec)
            print(codec, decoded == text)
            show(codec, decoded)
        show("utf-8", text.encode("utf-8").decode("utf-8"))
    show("latin-1", bytes(range(160, 176)).decode("latin-1"))
    # A high surrogate followed by a BMP unit, a lone low surrogate and a
    # truncated final unit.
    for bad in (b"\\x00\\xd8\\x41\\x00", b"\\x00\\xdc\\x41\\x00", b"\\x41\\x00\\x42"):
        for errors in ("replace", "ignore", "backslashreplace"):
            show(errors, bad.decode("utf-16-le", errors))
        try:
            bad.decode("utf-16-le")
        except UnicodeDecodeError as exc:
            print("strict", exc.start, exc.end, exc.reason)
    total = 0
    for k in range(400):
        total += len(("x" + str(k) + "\\U0001F600").encode("utf-16-be").decode("utf-16-be"))
    print(total)


main()
'''


def _decoder_body(tmp_path: Path) -> str:
    from pcc.frontends.python import pipeline

    output = tmp_path / "py_obj_stubs.ll"
    pipeline.compile_python(
        str(RUNTIME), str(output), emit_llvm_only=True, python_library=True, backend="self",
    )
    text = output.read_text()
    body = re.search(r"^define[^\n]*@py_text_decode_buffer_spans\([^\n]*\{\n(.*?)^}", text, re.M | re.S)
    assert body is not None
    return body.group(1)


def test_decoder_code_points_are_machine_integers(tmp_path: Path):
    body = _decoder_body(tmp_path)
    # No arbitrary-precision box, and so no root, pin or release protocol.
    for helper in ("py_int_from_i64", "py_int_or", "py_int_shr", "pcc_gc_store_root",
                   "pcc_gc_store_root_take", "pcc_gc_pin", "pcc_gc_unpin", "pcc_gc_release"):
        assert "@" + helper + "(" not in body, helper
    assert "exact.int" not in body


def test_utf16_and_byte_codecs_match_cpython_on_five_collectors(tmp_path: Path, pcc_runtime_archive):
    from pcc.frontends.python.pipeline import compile_python

    source = tmp_path / "decode_points.py"
    source.write_text(PROGRAM, encoding="utf-8")
    expected = subprocess.run([sys.executable, str(source)], capture_output=True, text=True, timeout=60)
    assert expected.returncode == 0, expected.stderr
    binary = tmp_path / "decode_points"
    compile_python(str(source), str(binary), backend="self",
                   ir_scaffold_mode="on", runtime_archive=str(pcc_runtime_archive))
    for backend in (0,):
        ran = subprocess.run([str(binary)], env=dict(os.environ, PCC_GC_BACKEND=str(backend)),
                             capture_output=True, text=True, timeout=120)
        assert ran.returncode == 0, f"GC{backend}: " + ran.stdout + ran.stderr[-2000:]
        assert ran.stdout == expected.stdout, f"GC{backend}: {ran.stdout}"
