"""Bounded Linux x86 CMP memory/immediate; original same-source GNU as oracle."""
import platform
import shutil
import subprocess
import sys

import pytest

from pcc.backend.elf_x86_64 import parse_relocatable
from pcc.backend.x86_64_encode import X86EncodeError, encode_instruction


def owned(instruction, pc=0):
    return encode_instruction(instruction, pc=pc, labels={}, section_name='.text')


# Exact instructions copied from the unchanged class-result assembly observer.
ORIGINAL = [
    ('cmp qword ptr [rsp+32], 7654346', '48817c2420cacb7400'),
    ('cmp dword ptr [rsp+64], 0', '837c244000'),
    ('cmp qword ptr [rsp+32], 7654349', '48817c2420cdcb7400'),
    ('cmp qword ptr [rsp+32], 3', '48837c242003'),
    ('cmp dword ptr [rsp+64], 1', '837c244001'),
]


@pytest.mark.parametrize('instruction,hex_bytes', ORIGINAL)
def test_original_observer_exact_bytes(instruction, hex_bytes):
    result = owned(instruction)
    assert result.code.hex() == hex_bytes
    assert result.relocations == ()


@pytest.mark.parametrize('width,value,hex_bytes,offset,addend', [
    ('byte', 255, '803d00000000ff', 2, -5),
    ('word', 128, '66813d000000008000', 3, -6),
    ('dword', 128, '813d0000000080000000', 2, -8),
    ('qword', -128, '48833d0000000080', 3, -5),
    ('qword', 128, '48813d0000000080000000', 3, -8),
    ('dword', 0xffffffff, '833d00000000ff', 2, -5),
])
def test_rip_relative_immediate_tail(width,value,hex_bytes,offset,addend):
    result = owned(f'cmp {width} ptr value[rip], {value}', pc=23)
    assert result.code.hex() == hex_bytes
    assert len(result.relocations) == 1
    rel = result.relocations[0]
    assert (rel.offset,rel.type,rel.symbol,rel.addend) == (23+offset,2,'value',addend)


CONTROLS = [
    'cmp [rax], 1', 'cmp xmmword ptr [rax], 1',
    'cmp qword ptr [r13+rsp*4], 10',
    'cmp qword ptr [rax], 0xffffffff', 'cmp qword ptr [rax], 0xffffffff7fffffff',
    'cmp qword ptr [rax], 0x10000000000000000', 'cmp qword ptr [rax], 0xfffffffeffffffff',
    'cmp qword ptr [rax], 2147483648', 'cmp qword ptr [rax], -2147483649',
    'cmp byte ptr [rax], 256', 'cmp byte ptr [rax], -129',
    'cmp word ptr [rax], 65536', 'cmp word ptr [rax], -32769',
    'cmp dword ptr [rax], 4294967296', 'cmp dword ptr [rax], -2147483649',
    'cmp qword ptr [rax], qword ptr [rbx]',
    'cmp qword ptr [rax], OFFSET FLAT:value',
    # This patch does not expand the sibling arithmetic vocabulary.
    'add qword ptr [rax], 1', 'sub dword ptr [rax], 1',
]


@pytest.mark.parametrize('instruction', CONTROLS)
def test_unproven_forms_remain_rejected(instruction):
    with pytest.raises(X86EncodeError):
        owned(instruction)


@pytest.fixture
def gnu_as():
    if not sys.platform.startswith('linux') or platform.machine().lower() not in ('x86_64','amd64'):
        pytest.skip('fixed Linux x86-64 observer')
    assembler = shutil.which('as')
    assert assembler, 'GNU as is required for the Linux x86-64 differential'
    version = subprocess.run([assembler,'--version'],capture_output=True,check=True,timeout=10)
    assert b'GNU assembler' in version.stdout, 'genuine GNU as is required'
    return assembler


DIFFERENTIAL = [x[0] for x in ORIGINAL] + [
    f'cmp {width} ptr [r13+r12*4+128], {value}'
    for width in ('byte','word','dword','qword') for value in (-128,0,127,128)
] + [
    'cmp word ptr [r12], 65535', 'cmp dword ptr [r12], 4294967295',
    'cmp qword ptr [r12], -2147483648', 'cmp qword ptr [r12], 2147483647',
    'cmp qword ptr [rax], 0xffffffffffffffff', 'cmp qword ptr [rax], 0xffffffff80000000',
    'cmp qword ptr [r12+r12*8+120], 0xffffffffffffff80',
    'cmp byte ptr [r13+r12], 1', 'cmp dword ptr [r9+r12*2], 255',
] + [
    f'cmp {width} ptr value[rip], {value}'
    for width in ('byte','word','dword','qword') for value in (-128,127,128)
] + [
    f'cmp {width} ptr [rax], {value}'
    for width,values in (
        ('byte',(-128,255)), ('word',(-32768,0xff7f,0xff80,65535)),
        ('dword',(-2147483648,0xffffff7f,0xffffff80,4294967295)),
        ('qword',(0xffffffffffffff7f,0xffffffffffffff80,0xffffffffffffffff)),
    ) for value in values
] + [
    f'mov DWORD PTR [r13+r12*{scale}+{displacement}], edi'
    for scale in (1,2,4,8) for displacement in (0,127,128)
]


@pytest.mark.parametrize('instruction', DIFFERENTIAL)
def test_identical_original_instruction_against_gnu_as(instruction,tmp_path,gnu_as):
    source = '.intel_syntax noprefix\n.text\n.globl probe\nprobe:\n  '+instruction+'\n'
    asm,obj=tmp_path/'probe.s',tmp_path/'probe.o'
    asm.write_text(source)
    process=subprocess.run([gnu_as,'--64',str(asm),'-o',str(obj)],capture_output=True,timeout=20)
    assert process.returncode == 0, process.stderr
    parsed=parse_relocatable(obj.read_bytes())
    section=next(s for s in parsed.sections if s.name=='.text')
    oracle=tuple((r.offset,r.type,r.addend,parsed.symbols[r.symbol_index].name)
                 for r in section.relocations)
    result=owned(instruction)
    actual=tuple((r.offset,r.type,r.addend,r.symbol) for r in result.relocations)
    assert result.code == section.data
    assert actual == oracle
