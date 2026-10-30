"""Canonical ASCII identity survives cold concurrent use and all collectors."""

import ast
import os
from pathlib import Path
import struct
import subprocess

from pcc.runtime.py import py_abi_constants as abi
from scripts.gen_ascii_image_chars import render_table
from tests.owned_c_execution import compile_owned_c_with_runtime

ROOT = Path(__file__).resolve().parents[2]


def test_ascii_image_layout_matches_header_abi():
    source = (ROOT / "pcc/runtime/py/freestanding_ascii_chars.py").read_text()
    assert render_table() in source
    tree = ast.parse(source)
    call = next(
        node.value for node in tree.body
        if isinstance(node, ast.Expr)
        and isinstance(node.value, ast.Call)
        and node.value.args
        and isinstance(node.value.args[0], ast.Constant)
        and node.value.args[0].value == "pcc_ascii_image_chars"
    )
    words = [ast.literal_eval(value) for value in call.args[1:]]
    data = struct.pack("<" + "q" * len(words), *words)
    stride = (abi.PYSTROBJECT_DATA_OFFSET + 9) // 8 * 8
    assert len(data) == 128 * stride
    # This is also the raw accessor's step, so layout changes fail closed.
    assert f'global_addr("pcc_ascii_image_chars"), c * {stride})' in source
    for codepoint in range(128):
        offset = codepoint * stride
        assert struct.unpack_from("<q", data, offset)[0] == 1
        assert struct.unpack_from("<I", data, offset + abi.PYOBJECTHEADER_TYPE_TAG_OFFSET)[0] == abi.PY_TYPE_STR
        assert struct.unpack_from("<I", data, offset + abi.PYOBJECTHEADER_FLAGS_OFFSET)[0] == abi.PY_FLAG_IMMORTAL
        assert struct.unpack_from("<q", data, offset + abi.PYSTROBJECT_BYTE_LEN_OFFSET)[0] == 1
        assert struct.unpack_from("<q", data, offset + abi.PYSTROBJECT_CP_LEN_OFFSET)[0] == 1
        assert struct.unpack_from("<q", data, offset + abi.PYSTROBJECT_HASH_OFFSET)[0] == -1
        payload = offset + abi.PYSTROBJECT_DATA_OFFSET
        assert data[payload:payload + 2] == bytes((codepoint, 0))


def test_cold_ascii_cache_has_one_identity_across_threads_and_backend_changes(
    tmp_path, threaded_pcc_runtime_archive,
):
    binary = tmp_path / "ascii_cache"
    compile_owned_c_with_runtime(
        ROOT / "tests/fixtures/native/ascii_character_cache.c", binary,
        runtime_archive=Path(threaded_pcc_runtime_archive),
    )
    for repeat in range(3):
        run = subprocess.run(
            [str(binary)], capture_output=True, text=True, timeout=30,
            env=dict(os.environ, PATH="/nonexistent", PCC_GC_BACKEND="0"),
        )
        assert (run.returncode, run.stdout, run.stderr) == (
            0, "ASCII_CANONICAL_CACHE_OK\n", "",
        ), repeat


PROGRAM = '''import gc


def copy_chars(value):
    return "".join([ch for ch in value])


def main():
    source = "".join([chr(i) for i in range(128)])
    for repeat in range(3):
        assert copy_chars(source) == source
        for i in range(128):
            assert ord(source[i]) == i
            assert source[i] is source[i]
        gc.collect()
    assert copy_chars("é中😀\\x00z") == "é中😀\\x00z"
    print("ASCII_VALUES_OK")


main()
'''


def test_ascii_and_unicode_values_survive_native_index_iteration_collection(
    tmp_path, pcc_runtime_archive,
):
    from pcc.frontends.python.pipeline import compile_python

    source = tmp_path / "ascii_values.py"
    binary = tmp_path / "ascii_values"
    source.write_text(PROGRAM)
    compile_python(
        str(source), str(binary), backend="self", libpython_mode="off",
        ir_scaffold_mode="on", runtime_archive=str(pcc_runtime_archive),
    )
    for backend in range(5):
        run = subprocess.run(
            [str(binary)], capture_output=True, text=True, timeout=20,
            env=dict(os.environ, PATH="/nonexistent", PCC_GC_BACKEND=str(backend)),
        )
        assert (run.returncode, run.stdout, run.stderr) == (
            0, "ASCII_VALUES_OK\n", "",
        ), backend
