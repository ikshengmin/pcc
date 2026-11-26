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
def geometry_namespace():
    """Run the actual raw bodies with checked memory; no simulated collector."""
    tree = ast.parse(SOURCE.read_text())
    def checked_load(words, offset):
        assert words is not None and offset >= 0 and offset % 8 == 0
        assert offset // 8 < len(words), 'descriptor table read outside supplied memory'
        return words[offset // 8]
    ns = {'i64': int, 'c_abi_export': lambda _: lambda f: f,
          'ptr_is_null': lambda p: int(p is None), 'load_i64': checked_load,
          'load_ptr': checked_load,
          'store_ptr': lambda p, off, val: p.__setitem__(off // 8, val),
          'store_i64': lambda p, off, val: p.__setitem__(off // 8, val),
          'ptr_add': lambda p, off: (p,off),
          'call_void_ptr_i64_ptr': lambda f, slot, role, ctx: f(slot,role,ctx)}
    nodes = [n for n in tree.body if isinstance(n, (ast.Assign, ast.FunctionDef))]
    exec(compile(ast.Module(body=nodes, type_ignores=[]), str(SOURCE), 'exec'), ns)
    return ns


@pytest.fixture
def geometry(geometry_namespace):
    ns = geometry_namespace
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


def test_validated_private_initialization_is_exact_and_one_time(geometry_namespace):
    bind = geometry_namespace['pcc_gc_described_initialize_private']
    data = descriptor(48,24,(40,),(0,16))
    owner = [7, 11, None, 0] + [0] * 11
    before = list(owner)
    for extent, desc, size, repeat in ((119,data,64,3),(121,data,64,3),
            (120,data,56,3),(120,descriptor(32,0),40,3),(24,data,64,3),
            (120,data,64,-1)):
        assert bind(owner,extent,desc,size,repeat) == 0
        assert owner == before
    assert bind(owner,120,data,64,3) == 1
    assert owner[0:2] == [7,11] and owner[2] is data and owner[3] == 3
    assert owner[4:] == before[4:]
    after = list(owner)
    assert bind(owner,120,data,64,3) == 0 and owner == after


@pytest.mark.parametrize('budget',(1,2,3,7,MAX_I64))
def test_bounded_visitor_preserves_owned_slot_order_and_ignores_scalar_bits(geometry_namespace,budget):
    bind = geometry_namespace['pcc_gc_described_initialize_private']
    visit = geometry_namespace['pcc_gc_described_visit_slots_slice']
    data = descriptor(48,24,(40,),(0,16))
    owner = [0,0,None,0] + [0] * 11
    assert bind(owner,120,data,64,3) == 1
    sentinel = object()
    for i in range(4,15): owner[i] = sentinel
    seen=[]
    def callback(slot,role,ctx):
        base,offset=slot
        assert base is owner and role==1 and ctx is sentinel
        assert base[offset//8] is sentinel
        seen.append(offset)
        base[offset//8]=None
    cursor=0
    while cursor>=0:
        state=[123,456]
        assert visit(owner,cursor,budget,callback,sentinel,state)==1
        assert 0 < state[1] <= budget
        cursor=state[0]
    assert seen == [40,48,64,72,88,96,112]
    assert [owner[i] for i in (4,7,10,13)] == [sentinel]*4
    assert owner[2] is data and owner[3]==3


def test_empty_backing_and_invalid_slices_never_call_visitor(geometry_namespace):
    bind = geometry_namespace['pcc_gc_described_initialize_private']
    visit = geometry_namespace['pcc_gc_described_visit_slots_slice']
    owner=[0,0,None,0]
    called=lambda *_: pytest.fail('must not visit scalar metadata or invalid input')
    state=[1,2]
    assert visit(owner,0,1,called,None,state)==0 and state==[-1,0]
    assert bind(owner,32,descriptor(32,8,(),(0,)),48,0)==1
    for cursor,limit,callback in ((-1,1,called),(0,0,called),(0,-1,called),(0,1,None)):
        state=[1,2]
        assert visit(owner,cursor,limit,callback,None,state)==0 and state==[-1,0]
    assert visit(owner,0,1,called,None,state)==1 and state==[-1,0]
    assert visit(owner,MAX_I64,1,called,None,state)==1 and state==[-1,0]
    assert visit(owner,0,1,called,None,None)==0


def test_slice_work_reads_only_selected_offsets_not_full_descriptor(geometry_namespace):
    bind = geometry_namespace['pcc_gc_described_initialize_private']
    visit = geometry_namespace['pcc_gc_described_visit_slots_slice']
    class Observed(list):
        def __init__(self,values): super().__init__(values); self.reads=[]
        def __getitem__(self,index): self.reads.append(index); return super().__getitem__(index)
    data=Observed(descriptor(32+8000,0,tuple(range(32,8032,8))))
    owner=[0,0,None,0]+[0]*1000
    assert bind(owner,len(owner)*8,data,len(data)*8,0)==1
    data.reads.clear()
    seen=[]
    state=[-1,0]
    assert visit(owner,998,1,lambda slot,role,ctx: seen.append(slot[1]),None,state)==1
    assert state==[999,1] and seen==[32+998*8]
    assert len(data.reads)<=7
    assert [i for i in data.reads if i>=5]==[5+998]


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
            'pcc_gc_described_extent', 'pcc_gc_described_slot_count', 'pcc_gc_described_slot_offset',
            'pcc_gc_described_initialize_private','pcc_gc_described_visit_slots_slice'}
        code = r'''#include "pcc_described_layout.h"
typedef struct Trace { int64_t *base; int64_t *offsets; int64_t count; int64_t bad; int64_t value; } Trace;
static void record(void *slot,int64_t role,void *context) {
    Trace *t=(Trace *)context;
    int64_t *p=(int64_t *)slot;
    if(role!=1 || t->count>=7 || *p!=t->value) {t->bad=1;return;}
    t->offsets[t->count]=(p-t->base)*8;
    t->count++;
    *p=0;
}
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
    int64_t owner[15]={0};
    owner[0]=7;owner[1]=11;
    if (pcc_gc_described_initialize_private(owner,119,mixed,sizeof(mixed),3)) return 10;
    if (owner[2]!=0 || owner[3]!=0) return 11;
    if (!pcc_gc_described_initialize_private(owner,sizeof(owner),mixed,sizeof(mixed),3)) return 12;
    if (pcc_gc_described_initialize_private(owner,sizeof(owner),mixed,sizeof(mixed),3)) return 13;
    int64_t value=(int64_t)(void*)owner;
    for (int i=4;i<15;i++) owner[i]=value;
    int64_t offsets[7]={0};
    Trace t;t.base=owner;t.offsets=offsets;t.count=0;t.bad=0;t.value=value;
    int64_t state[2]={0,0};int64_t cursor=0;
    while(cursor>=0){
        if (!pcc_gc_described_visit_slots_slice(owner,cursor,2,record,&t,state)) return 14;
        if(state[1]<=0 || state[1]>2) return 15;
        cursor=state[0];
    }
    if(t.bad || t.count!=7) return 16;
    for(int i=0;i<7;i++) if(offsets[i]!=expected[i] || owner[expected[i]/8]!=0) return 17;
    if(owner[4]!=value || owner[7]!=value || owner[10]!=value || owner[13]!=value) return 18;
    if(owner[0]!=7 || owner[1]!=11 || owner[2]!=(int64_t)(void*)mixed || owner[3]!=3) return 19;
    if(pcc_gc_described_visit_slots_slice(owner,-1,2,record,&t,state) || state[0]!=-1 || state[1]!=0) return 20;
    int64_t empty[4]={0};
    if(!pcc_gc_described_initialize_private(empty,sizeof(empty),bytes,sizeof(bytes),0)) return 21;
    if(!pcc_gc_described_visit_slots_slice(empty,0,2,record,&t,state) || state[0]!=-1 || state[1]!=0 || t.bad || t.count!=7) return 22;
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
        for name in ('pcc_gc_described_extent', 'pcc_gc_described_slot_count', 'pcc_gc_described_slot_offset',
                     'pcc_gc_described_initialize_private','pcc_gc_described_visit_slots_slice'):
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
    assert {'pcc_gc_described_extent', 'pcc_gc_described_slot_count', 'pcc_gc_described_slot_offset',
            'pcc_gc_described_initialize_private','pcc_gc_described_visit_slots_slice'} <= defined
