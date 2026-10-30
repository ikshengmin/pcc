"""Bounded scalar UTF-8 decoding keeps full codepoint and raw-byte behavior."""

import os
from pathlib import Path
import subprocess
import sys

import pytest

from pcc.runtime.py import py_abi_constants as abi
from tests.owned_c_execution import compile_owned_c_with_runtime
from tests.python.test_str_encode_binding import _EncoderModel, _Memory


@pytest.mark.parametrize("signed_bytes", (False, True))
def test_decoder_matches_codepoints_offsets_and_signed_byte_loads(signed_bytes):
    points = (0, 1, 127, 128, 255, 2047, 2048, 0xd7ff, 0xd800, 0xdbff,
              0xdc00, 0xdfff, 0xe000, 0xffff, 0x10000, 0x10ffff)
    text = "".join(chr(point) for point in points)
    model = _EncoderModel()
    obj = model.object(text)
    if signed_bytes:
        model.namespace["load_i8"] = lambda p, n: (model.load_byte(p, n) + 128) % 256 - 128
    decode = model.namespace["_utf8_ord_at_byte"]
    offset = 0
    for point in points:
        assert decode(obj, offset) == point
        width = len(chr(point).encode("utf8", "surrogatepass"))
        for continuation in range(1, width):
            assert decode(obj, offset + continuation) == -1
        offset += width
    for invalid in (-2**63, -1, offset, offset + 1, 2**63 - 1):
        assert decode(obj, invalid) == -1
    assert model.error is None and not model.allocations


@pytest.mark.parametrize("raw,expected", (
    (b"", -1), (b"\xc2", -1), (b"\xe0\xa0", -1), (b"\xf0\x90\x80", -1),
    (b"\x80", -1), (b"\xff", -1), (b"\xf7\xbf\xbf\xbf", 0x1fffff),
    (b"\xc2A", 129),
))
def test_decoder_preserves_truncated_and_raw_masking_contract(raw, expected):
    model = _EncoderModel()
    obj = _Memory(bytes(abi.PYSTROBJECT_DATA_OFFSET) + raw)
    assert model.namespace["_utf8_ord_at_byte"](obj, 0) == expected


PROBE = r'''#include "py_internal.h"
#include <stdint.h>
#include <stdio.h>
#include <string.h>
extern int64_t user_py_str_accessors__utf8_ord_at_byte(void *, int64_t);
static union { int64_t align; unsigned char bytes[sizeof(PyStrObject) + 8]; } storage;
int main(void) {
    PyStrObject *text = (PyStrObject *)storage.bytes;
    (void)pcc_gc_backend();
    text->h.refcount = 1;
    text->h.type_tag = PY_TYPE_STR;
    text->h.flags = PY_FLAG_IMMORTAL;
    text->cp_len = -1;
    text->hash = -1;
    for (uint32_t cp = 0; cp <= 0x10ffff; cp++) {
        int64_t width;
        if (cp < 0x80) { text->data[0] = (char)cp; width = 1; }
        else if (cp < 0x800) {
            text->data[0] = (char)(0xc0 | (cp >> 6));
            text->data[1] = (char)(0x80 | (cp & 63)); width = 2;
        } else if (cp < 0x10000) {
            text->data[0] = (char)(0xe0 | (cp >> 12));
            text->data[1] = (char)(0x80 | ((cp >> 6) & 63));
            text->data[2] = (char)(0x80 | (cp & 63)); width = 3;
        } else {
            text->data[0] = (char)(0xf0 | (cp >> 18));
            text->data[1] = (char)(0x80 | ((cp >> 12) & 63));
            text->data[2] = (char)(0x80 | ((cp >> 6) & 63));
            text->data[3] = (char)(0x80 | (cp & 63)); width = 4;
        }
        text->data[width] = 0;
        text->byte_len = width;
        if (user_py_str_accessors__utf8_ord_at_byte(text, 0) != cp) return 10;
        if (py_str_ord((PyObject *)text) != cp) return 11;
        if (user_py_str_accessors__utf8_ord_at_byte(text, -1) != -1) return 12;
        if (user_py_str_accessors__utf8_ord_at_byte(text, width) != -1) return 13;
        for (int64_t index = 1; index < width; index++) {
            if (user_py_str_accessors__utf8_ord_at_byte(text, index) != -1) return 14;
        }
        for (int64_t truncated = 0; truncated < width; truncated++) {
            text->byte_len = truncated;
            if (user_py_str_accessors__utf8_ord_at_byte(text, 0) != -1) return 15;
        }
    }
    puts("UTF8_ALL_CODEPOINTS_OK");
    return 0;
}
'''


@pytest.mark.integration
def test_decoder_exhaustive_native_owned_c(tmp_path, threaded_pcc_runtime_archive):
    source = tmp_path / "decode_all.c"
    source.write_text(PROBE)
    binary = tmp_path / "decode_all"
    runtime = Path(__file__).resolve().parents[2] / "pcc/runtime"
    compile_owned_c_with_runtime(source, binary, runtime_archive=threaded_pcc_runtime_archive,
                                 cpp_args=("-I" + str(runtime / "src"),))
    # A raw scalar/span control on GC0. The managed encoding programs test
    # the actual source/output lifetime and exceptions separately on GC0–4.
    run = subprocess.run([str(binary)], capture_output=True, text=True, timeout=30,
                         env=dict(os.environ, PCC_GC_BACKEND="0", PATH="/nonexistent"))
    (tmp_path / "stdout").write_text(run.stdout)
    (tmp_path / "stderr").write_text(run.stderr)
    assert run.returncode == 0 and run.stderr == "" and run.stdout == "UTF8_ALL_CODEPOINTS_OK\n", (run.returncode, run.stdout, run.stderr)


def encoding_contract_program():
    # Fixture data comes from CPython, independently of the runtime decoder.
    cases = []
    values = ("", "ASCII\0", "é日本🙂", "A\udc80\ud800\udcffZ", "\U0010ffff")
    for value in values:
        for codec in ("utf8", "ascii", "latin1"):
            for mode in ("strict", "ignore", "replace", "surrogateescape", "surrogatepass",
                         "backslashreplace", "xmlcharrefreplace", "unknown"):
                try:
                    expected = (0, value.encode(codec, mode))
                except UnicodeEncodeError as error:
                    expected = (1, (error.encoding, error.start, error.end, error.reason))
                except LookupError:
                    expected = (2, None)
                cases.append((tuple(ord(char) for char in value), codec, mode, expected))
    return "import gc\nCASES = " + repr(cases) + '''
def main():
    checked = 0
    for points, codec, mode, expected in CASES:
        # Construct the codec input from scalar values; the separate saved
        # baseline reproducer tracks surrogate pairs changed in constants.
        value = ""
        for point in points:
            value += chr(point)
        assert len(value) == len(points)
        for index in range(len(points)):
            assert ord(value[index]) == points[index]
        gc.collect()
        try:
            result = value.encode(codec, mode)
        except UnicodeEncodeError as error:
            assert expected[0] == 1
            assert error.object is value
            assert (error.encoding, error.start, error.end, error.reason) == expected[1]
            assert error.args == (error.encoding, value, error.start, error.end, error.reason)
        except LookupError:
            assert expected[0] == 2
        else:
            assert expected[0] == 0 and result == expected[1]
        checked += 1
    assert checked == 120
    for point in (0, 127, 128, 2047, 2048, 0xd7ff, 0xd800, 0xdfff, 0xe000, 0xffff, 0x10000, 0x10ffff):
        value = chr(point)
        gc.collect()
        assert ord(value) == point
    print("UTF8_ENCODING_CONTRACT_OK")
main()
'''


@pytest.mark.integration
def test_managed_encoding_bytes_and_exception_payload_all_gc(tmp_path, threaded_pcc_runtime_archive):
    from pcc.frontends.python.pipeline import compile_python
    source = tmp_path / "encoding_contract.py"
    source.write_text(encoding_contract_program())
    reference = subprocess.run([sys.executable, str(source)], capture_output=True, text=True, timeout=20)
    assert reference.returncode == 0 and reference.stderr == "" and reference.stdout == "UTF8_ENCODING_CONTRACT_OK\n"
    binary = source.with_suffix("")
    compile_python(str(source), str(binary), backend="self", libpython_mode="off",
                   ir_scaffold_mode="on", runtime_archive=str(threaded_pcc_runtime_archive))
    for backend in range(5):
        run = subprocess.run([str(binary)], capture_output=True, text=True, timeout=30,
                             env=dict(os.environ, PCC_GC_BACKEND=str(backend), PCC_GC_MINOR_ALLOC_MAX="4096", PATH="/nonexistent"))
        (tmp_path / ("contract-gc" + str(backend) + ".stdout")).write_text(run.stdout)
        (tmp_path / ("contract-gc" + str(backend) + ".stderr")).write_text(run.stderr)
        assert run.returncode == 0 and run.stderr == "" and run.stdout == reference.stdout, (backend, run.returncode, run.stdout, run.stderr)
