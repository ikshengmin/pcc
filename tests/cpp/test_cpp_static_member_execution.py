"""Exact-source static-member execution and independent ABI observations.

Host-owned Linux ELF and explicit G++17 are separate lanes. No native pcc1,
inline/COMDAT, static-data, original TinyXML2 TU, or complete C++17 claim.
"""

import json
import os
import re
import shutil
import struct
import subprocess

import pytest

from tests.cpp.owned_execution import (
    Execution, compile_host_cpp, compile_reference_cpp, write_host_executable,
)
from tests.cpp.test_cpp_class_execution import execute_and_record, record_execution
from tests.cpp.test_cpp_static_members import STATIC_CASES


@pytest.mark.integration
@pytest.mark.parametrize('name,source', STATIC_CASES, ids=[row[0] for row in STATIC_CASES])
def test_static_original_program_matches_explicit_reference(tmp_path, monkeypatch, name, source):
    execute_and_record(tmp_path, monkeypatch, ((name + '.cpp', source),), 42)


@pytest.mark.integration
@pytest.mark.parametrize('access', ('pick(&trace)->f(argument(&trace))',
                                    '(*pick(&trace)).f(argument(&trace))'))
def test_static_object_postfix_evaluates_once_before_arguments(tmp_path, monkeypatch, access):
    header = 'struct S{int unused;static int f(int);};'
    source = (header + 'extern "C" S*fixture();int S::f(int n){return n;}'
              'S*pick(int*n){*n=*n*10+1;return fixture();}'
              'int argument(int*n){*n=*n*10+2;return 42;}'
              'int cpp_test_entry(){int trace=0;int result=' + access + ';'
              'if(trace!=12)return 1;return result;}')
    provider = header + 'extern "C" S*fixture(){static S value{};return &value;}'
    assembly = ('.intel_syntax noprefix\n.data\n.p2align 3\nstatic_fixture_object:\n'
                '.quad 0\n.text\n.globl fixture\nfixture:\n'
                'lea rax, static_fixture_object[rip]\nret\n')
    _execute_foreign_fixture(tmp_path, monkeypatch, (('static-effects.cpp', source),),
                             (('provider.cpp', provider),), assembly)


@pytest.mark.integration
def test_nonstatic_body_can_call_static_without_forwarding_this(tmp_path, monkeypatch):
    header = 'struct S{static int f(int);int g();};'
    source = (header + 'extern "C" S*fixture();int S::f(int n){return n;}'
              'int S::g(){return f(42);}int cpp_test_entry(){return fixture()->g();}')
    provider = header + 'extern "C" S*fixture(){static S value{};return &value;}'
    assembly = ('.intel_syntax noprefix\n.data\nstatic_fixture_object:\n.byte 0\n'
                '.text\n.globl fixture\nfixture:\nlea rax, static_fixture_object[rip]\nret\n')
    _execute_foreign_fixture(tmp_path, monkeypatch, (('static-in-member.cpp', source),),
                             (('provider.cpp', provider),), assembly)


def _execute_foreign_fixture(tmp_path, monkeypatch, units, reference_support, assembly):
    with monkeypatch.context() as guard:
        def forbidden(*args, **kwargs):
            raise AssertionError('owned static-member compilation launched external process')

        guard.setattr(subprocess, 'Popen', forbidden)
        artifact = compile_host_cpp(units, owned_support_assembly=(assembly,))
        assert artifact == compile_host_cpp(units, owned_support_assembly=(assembly,))
    binary = tmp_path / 'owned'
    write_host_executable(artifact, binary)
    expected = Execution(42, struct.pack('<i', 42), b'')
    record_execution(binary, expected)
    record_execution(binary, expected)
    compiler = shutil.which(os.environ.get('PCC_CPP_ORACLE', 'g++'))
    assert compiler, 'explicit G++17 reference unavailable'
    reference = compile_reference_cpp(units + reference_support, tmp_path / 'reference', compiler=compiler)
    record_execution(reference, expected)
    (tmp_path / 'support.s').write_text(assembly)


@pytest.mark.integration
def test_static_abi_registers_and_stack_have_no_hidden_object(tmp_path, monkeypatch):
    header = 'struct S{static int check(int,int,int,int,int,int,int,int);};'
    caller = header + 'int cpp_test_entry(){return S::check(1,2,3,4,5,6,7,14);}'
    implementation = (header + 'int S::check(int a,int b,int c,int d,int e,int f,int g,int h)'
                      '{return a+b+c+d+e+f+g+h;}')
    assembly = ('.intel_syntax noprefix\n.text\n.globl _ZN1S5checkEiiiiiiii\n'
                '_ZN1S5checkEiiiiiiii:\nmov eax,edi\nadd eax,esi\nadd eax,edx\n'
                'add eax,ecx\nadd eax,r8d\nadd eax,r9d\n'
                'add eax,dword ptr [rsp+8]\nadd eax,dword ptr [rsp+16]\nret\n')
    _execute_foreign_fixture(tmp_path, monkeypatch, (('static-caller.cpp', caller),),
                             (('implementation.cpp', implementation),), assembly)


@pytest.mark.integration
def test_static_methods_link_across_independent_translation_units(tmp_path, monkeypatch):
    header = 'namespace n{struct S{static int add(int,int);};}'
    units = (
        ('caller.cpp', header + 'int cpp_test_entry(){return n::S::add(17,25);}'),
        ('definition.cpp', header + 'int n::S::add(int a,int b){return a+b;}'),
    )
    execute_and_record(tmp_path, monkeypatch, units, 42)


@pytest.mark.integration
def test_static_mangled_exports_match_independent_reference(tmp_path, monkeypatch):
    from pcc.backend.elf_x86_64 import parse_relocatable

    source = ('struct S{static int f(int);static int f(S*);};'
              'int S::f(int n){return n;}int S::f(S*p){return 1;}'
              'namespace n{struct S{static int f(const S*);};'
              'int S::f(const S*p){return 2;}}int cpp_test_entry(){return 42;}')
    with monkeypatch.context() as guard:
        def forbidden(*args, **kwargs):
            raise AssertionError('owned static-member compilation launched external process')

        guard.setattr(subprocess, 'Popen', forbidden)
        artifact = compile_host_cpp((('static-exports.cpp', source),))
    unit, output = tmp_path / 'static-exports.cpp', tmp_path / 'reference.s'
    unit.write_text(source)
    compiler = shutil.which(os.environ.get('PCC_CPP_ORACLE', 'g++'))
    assert compiler, 'explicit G++17 ABI reference unavailable'
    result = subprocess.run([compiler, '-std=c++17', '-pedantic-errors', '-O0', '-S',
                             str(unit), '-o', str(output)], capture_output=True, timeout=20)
    assert result.returncode == 0, result.stderr
    reference = set(re.findall(r'^\s*\.globl\s+(_Z\w+)', output.read_text(), re.MULTILINE))
    owned = {symbol.name for symbol in parse_relocatable(artifact.objects[0]).symbols
             if symbol.binding == 1 and symbol.section_index and symbol.name.startswith('_Z')}
    assert len(owned) == 4 and owned == reference
    (tmp_path / 'owned.o').write_bytes(artifact.objects[0])
    (tmp_path / 'exports.json').write_text(json.dumps({'owned': sorted(owned), 'reference': sorted(reference)}, indent=2))
