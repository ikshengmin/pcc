"""#13 descriptor geometry only; no managed object/tag publication yet."""
from __future__ import annotations

import ast
import platform
from pathlib import Path
import subprocess
import sys

import pytest

ROOT = Path(__file__).resolve().parents[2]
SOURCE = ROOT / 'pcc/runtime/py/freestanding_gc_described_layout.py'
HEADER = ROOT / 'pcc/runtime/include/pcc_described_layout.h'
TARGET = 'x86_64-unknown-linux-gnu'
MAX_I64 = (1 << 63) - 1


def descriptor(prefix, stride, fixed=(), repeated=(), version=1):
    return [version, prefix, stride, len(fixed), len(repeated), *fixed, *repeated]


@pytest.fixture
def geometry():
    """Run the actual raw bodies with checked memory; no simulated collector."""
    tree = ast.parse(SOURCE.read_text())
    def checked_load(words, offset):
        assert words is not None and offset >= 0 and offset % 8 == 0
        assert offset // 8 < len(words), 'descriptor table read outside supplied memory'
        return words[offset // 8]
    ns = {'i64': int, 'c_abi_export': lambda _: lambda f: f,
          'ptr_is_null': lambda p: int(p is None), 'load_i64': checked_load}
    nodes = [n for n in tree.body if isinstance(n, (ast.Assign, ast.FunctionDef))]
    exec(compile(ast.Module(body=nodes, type_ignores=[]), str(SOURCE), 'exec'), ns)
    return tuple(ns[n] for n in ('pcc_gc_described_extent', 'pcc_gc_described_slot_count',
                                  'pcc_gc_described_slot_offset'))


@pytest.mark.parametrize('words,repeats,extent,offsets', [
    (descriptor(32, 0), 0, 32, ()),
    (descriptor(32, 1), 0, 32, ()),
    (descriptor(32, 1), 73, 105, ()),
    (descriptor(56, 0, (32, 48)), 0, 56, (32, 48)),
    (descriptor(48, 24, (40,), (0, 16)), 3, 120, (40, 48, 64, 72, 88, 96, 112)),
    (descriptor(32, 8, (), (0,)), 0, 32, ()),
    (descriptor(32, 8, (), (0,)), 1, 40, (32,)),
    (descriptor(32, 1), MAX_I64 - 32, MAX_I64, ()),
])
def test_extent_and_slot_map(geometry, words, repeats, extent, offsets):
    extent_of, count, offset = geometry
    n = len(words) * 8
    assert extent_of(words, n, repeats) == extent
    assert count(words, n, repeats) == len(offsets)
    assert tuple(offset(words, n, repeats, i) for i in range(len(offsets))) == offsets
    assert offset(words, n, repeats, -1) == offset(words, n, repeats, len(offsets)) == -1


@pytest.mark.parametrize('words,n,repeats', [
    (None, 40, 0),
    ([], 0, 0),
    ([1, 32, 0, 0, 0], 39, 0),
    (descriptor(32, 0), 48, 0),  # unexpected bytes cannot authorize another slot table
    (descriptor(32, 0, version=2), 40, 0),
    (descriptor(31, 0), 40, 0),
    (descriptor(32, -1), 40, 0),
    (descriptor(32, 0), 40, 1),
    (descriptor(32, 1), 40, -1),
    (descriptor(32, 1), 40, MAX_I64 - 31),
    (descriptor(32, MAX_I64), 40, 2),
    ([1, 32, 0, -1, 0], 40, 0),
    ([1, 32, 0, MAX_I64, MAX_I64], 40, 0),
    (descriptor(40, 0, (16,)), 48, 0),  # descriptor pointer is never an owned ref
    (descriptor(40, 0, (24,)), 48, 0),  # capacity is scalar metadata
    (descriptor(40, 0, (33,)), 48, 0),
    (descriptor(40, 0, (40,)), 48, 0),
    (descriptor(56, 0, (40, 32)), 56, 0),
    (descriptor(56, 0, (32, 32)), 56, 0),
    (descriptor(32, 0, (), (0,)), 48, 0),
    (descriptor(36, 8, (), (0,)), 48, 1),
    (descriptor(32, 9, (), (0,)), 48, 1),
    (descriptor(32, 8, (), (8,)), 48, 1),
    (descriptor(32, 8, (), (-8,)), 48, 1),
    (descriptor(32, 16, (), (8, 0)), 56, 1),
    (descriptor(32, 16, (), (8, 8)), 56, 1),
])
def test_rejects_bad_extent_before_unbounded_slot_access(geometry, words, n, repeats):
    for helper in geometry[:2]:
        assert helper(words, n, repeats) == -1
    assert geometry[2](words, n, repeats, 0) == -1


def test_maximal_reference_backing_has_bounded_count_and_last_offset(geometry):
    extent, count, offset = geometry
    words = descriptor(32, 8, (), (0,))
    n = len(words) * 8
    repeats = (MAX_I64 - 32) // 8
    expected = 32 + repeats * 8
    assert extent(words, n, repeats) == expected
    assert count(words, n, repeats) == repeats
    assert offset(words, n, repeats, repeats-1) == expected-8
    assert offset(words, n, repeats, repeats) == -1
    assert extent(words, n, repeats+1) == -1


def test_abi_layout_header_and_runtime_module_are_explicit():
    text = HEADER.read_text()
    assert 'PCC_DESCRIBED_DESCRIPTOR_VERSION 1' in text
    assert 'PCC_DESCRIBED_DESCRIPTOR_HEADER_BYTES 40' in text
    assert 'PCC_DESCRIBED_OBJECT_DESCRIPTOR_OFFSET 16' in text
    assert 'PCC_DESCRIBED_OBJECT_REPEAT_COUNT_OFFSET 24' in text
    assert 'PCC_DESCRIBED_OBJECT_FIELDS_OFFSET 32' in text
    from pcc.runtime.py.py_abi_constants import PYOBJECTHEADER_SIZE, C_POINTER_SIZE
    assert PYOBJECTHEADER_SIZE == 16 and C_POINTER_SIZE == 8
    from pcc.frontends.python.owned_runtime_build import runtime_modules
    assert SOURCE.stem in runtime_modules(str(ROOT / 'pcc/runtime'), TARGET)


@pytest.mark.integration
@pytest.mark.pcc_gate(unavailable=None if sys.platform == 'linux' and platform.machine().lower() in (
    'x86_64', 'amd64') else 'requires native Linux x86-64 ELF')
def test_descriptor_geometry_executes_owned_native_code(tmp_path, monkeypatch):
    from pcc.backend.elf_x86_64 import link_static_executable, parse_relocatable
    from pcc.backend.owned_object_emit import emit_owned_object
    from pcc.backend.x86_64_asm_driver import assemble_file
    from pcc.driver.project import TranslationUnit
    from pcc.frontends.c.evaluator.c_evaluator import CEvaluator
    from tests.owned_runtime_c_fixture import runtime_ir
    ir = tmp_path / 'descriptor.ll'
    with monkeypatch.context() as guard:
        guard.setattr(subprocess, 'Popen', lambda *a, **k: pytest.fail('external target tool invoked'))
        runtime_ir(SOURCE, ir, TARGET)
        obj = emit_owned_object(ir.read_text(), TARGET)
        symbols = parse_relocatable(obj).symbols
        assert {s.name for s in symbols if s.name and s.section_index == 0} == set()
        assert {s.name for s in symbols if s.binding == 1 and s.section_index != 0} == {
            'pcc_gc_described_extent', 'pcc_gc_described_slot_count', 'pcc_gc_described_slot_offset'}
        code = r'''#include "pcc_described_layout.h"
int main(void) {
    const int64_t bytes[] = {PCC_DESCRIBED_DESCRIPTOR_VERSION,32,1,0,0};
    const int64_t mixed[] = {1,48,24,1,2,40,0,16};
    const int64_t bad[] = {1,56,0,2,0,32,32};
    if (pcc_gc_described_extent(bytes,sizeof(bytes),73)!=105) return 1;
    if (pcc_gc_described_slot_count(bytes,sizeof(bytes),73)!=0) return 2;
    if (pcc_gc_described_extent(mixed,sizeof(mixed),3)!=120) return 3;
    if (pcc_gc_described_slot_count(mixed,sizeof(mixed),3)!=7) return 4;
    const int64_t expected[] = {40,48,64,72,88,96,112};
    for (int i=0;i<7;i++) {
        if (pcc_gc_described_slot_offset(mixed,sizeof(mixed),3,i)!=expected[i]) return 5;
    }
    if (pcc_gc_described_slot_offset(mixed,sizeof(mixed),3,7)!=-1) return 6;
    if (pcc_gc_described_extent(bad,sizeof(bad),0)!=-1) return 7;
    if (pcc_gc_described_extent(bytes,sizeof(bytes),9223372036854775776LL)!=-1) return 8;
    if (pcc_gc_described_extent(bytes,sizeof(bytes)-1,1)!=-1) return 9;
    return 42;
}'''
        caller = CEvaluator(backend='self', target_triple=TARGET).compile_translation_units(
            [TranslationUnit('descriptor.c', None, code)], use_system_cpp=False,
            include_dirs=[str(HEADER.parent)], frontend_opt_level=0, use_compile_cache=False)
        startup = assemble_file('.intel_syntax noprefix\n.text\n.globl _start\n_start:\n call main\n mov edi,eax\n mov eax,60\n syscall\n')
        image = link_static_executable([startup,parse_relocatable(obj),
            parse_relocatable(emit_owned_object(caller[0][1], TARGET))], entry='_start')
    exe = tmp_path / 'descriptor'; exe.write_bytes(image); exe.chmod(0o700)
    result = subprocess.run([str(exe)], capture_output=True, timeout=10)
    assert (result.returncode, result.stdout, result.stderr) == (42,b'',b'')


@pytest.mark.parametrize('target', (
    'aarch64-unknown-linux-gnu', 'arm64-apple-darwin', 'x86_64-pc-windows-msvc',
))
def test_descriptor_geometry_emits_owned_64_bit_cross_target_objects(tmp_path, monkeypatch, target):
    from pcc.backend.owned_object_emit import emit_owned_object
    from tests.owned_runtime_c_fixture import runtime_ir, object_symbols
    ir = tmp_path / 'descriptor.ll'
    with monkeypatch.context() as guard:
        guard.setattr(subprocess, 'Popen', lambda *a, **k: pytest.fail('external target tool invoked'))
        runtime_ir(SOURCE, ir, target)
        text = ir.read_text()
        for name in ('pcc_gc_described_extent', 'pcc_gc_described_slot_count', 'pcc_gc_described_slot_offset'):
            assert f'define external i64 @{name}(ptr ' in text
        image = emit_owned_object(text, target)
    obj = tmp_path / 'descriptor.o'; obj.write_bytes(image)
    if 'windows' in target:
        from pcc.backend.coff_x86_64 import parse_object
        symbols = parse_object(image).symbols
        assert [s.name for s in symbols if s.name and s.section == 0] == []
        defined = {s.name for s in symbols if s.section > 0}
    else:
        assert object_symbols(obj, undefined=True).stdout == ''
        defined = {line.split()[-1].lstrip('_') for line in object_symbols(obj).stdout.splitlines()}
    assert {'pcc_gc_described_extent', 'pcc_gc_described_slot_count', 'pcc_gc_described_slot_offset'} <= defined
