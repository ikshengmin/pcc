"""N4659 [dcl.link], [expr.call]: C name identity with native scalar FP ABI.

No long-double surrogate or variadic callback/aggregate ABI admission.
"""
import os
import shutil
import struct
import pytest
from pcc.frontends.cpp import CppParser
from tests.cpp.owned_execution import Execution, compile_host_cpp, compile_reference_cpp, write_host_executable
from tests.cpp.test_cpp_class_execution import record_execution, execute_and_record
from tests.cpp.test_cpp_owned_execution import owned_only

ORIGINAL = 'extern "C" double f(double);'
ORIGINAL_UNITS = (('original.cpp', ORIGINAL), ('entry.cpp', 'int cpp_test_entry(){return 42;}'))
CALLER = ('extern "C" double adjust(double,float,int);'
          'int cpp_test_entry(){return int(adjust(20.0,20.0f,2));}')
PROVIDER = 'extern "C" double adjust(double a,float b,int c){return a+b+c;}'
ASSEMBLY = '''.intel_syntax noprefix
.text
.globl adjust
adjust:
  cvtss2sd xmm1, xmm1
  addsd xmm0, xmm1
  cvtsi2sd xmm1, edi
  addsd xmm0, xmm1
  ret
'''
FLOAT_UNITS = (('provider.cpp','extern "C" float twice(float n){return n+n;}'),
               ('caller.cpp','extern "C" float twice(float);int cpp_test_entry(){return int(twice(21.0f));}'))


CONST_UNITS = (
    ('const-double-result', (('original.cpp', 'extern "C" const double value(){return 42.0;}'
                             'int cpp_test_entry(){return int(value());}'),)),
    ('const-float-result', (('original.cpp', 'extern "C" const float value(){return 42.0f;}'
                            'int cpp_test_entry(){return int(value());}'),)),
)


@pytest.mark.parametrize('name,units',CONST_UNITS)
def test_const_floating_result_keeps_declaration_type_and_real_artifact(name,units,owned_only):
    from pcc.frontends.cpp.types import ConstType
    parser = CppParser()
    parser.parse(units[0][1])
    assert isinstance(parser.scope.names['value'].overloads[0].dtype,ConstType)
    assert compile_host_cpp(units)==compile_host_cpp(units)


def test_original_floating_c_declaration_retains_plain_symbol_and_stable_artifact(owned_only):
    parser = CppParser()
    parser.parse(ORIGINAL)
    symbol = parser.scope.names['f'].overloads[0]
    assert symbol.language_linkage == 'C' and symbol.c_name == 'f'
    assert symbol.dtype == 'double' and symbol.parameters == ('double',)
    assert compile_host_cpp(ORIGINAL_UNITS) == compile_host_cpp(ORIGINAL_UNITS)


def test_mixed_float_integer_register_call_and_return_artifact_is_stable(owned_only):
    first = compile_host_cpp((('caller.cpp',CALLER),), owned_support_assembly=(ASSEMBLY,))
    assert first == compile_host_cpp((('caller.cpp',CALLER),), owned_support_assembly=(ASSEMBLY,))


def test_float_c_definition_and_separate_caller_are_byte_stable(owned_only):
    assert compile_host_cpp(FLOAT_UNITS) == compile_host_cpp(FLOAT_UNITS)


@pytest.mark.integration
def test_c_float_double_native_registers_match_real_cpp17_reference(tmp_path):
    artifact = compile_host_cpp((('caller.cpp',CALLER),),owned_support_assembly=(ASSEMBLY,))
    binary = tmp_path/'owned'
    write_host_executable(artifact,binary)
    expected = Execution(42,struct.pack('<i',42),b'')
    record_execution(binary,expected)
    compiler = shutil.which(os.environ.get('PCC_CPP_ORACLE','g++'))
    assert compiler
    reference = compile_reference_cpp((('caller.cpp',CALLER),('provider.cpp',PROVIDER)),tmp_path/'reference',compiler=compiler)
    record_execution(reference,expected)


@pytest.mark.integration
@pytest.mark.parametrize('name,units',[('original-declaration',ORIGINAL_UNITS),('float-return',FLOAT_UNITS),*CONST_UNITS])
def test_c_floating_originals_execute_against_real_cpp17_reference(name,units,tmp_path,monkeypatch):
    execute_and_record(tmp_path,monkeypatch,units,42)
