"""Owned cstdio declaration/ABI boundary, not a full stdio implementation.

Rules: N4659 [cstdio.syn], [dcl.fct], [expr.call], [over.ics.ellipsis].
"""
import importlib.util
from pathlib import Path
import pytest

from pcc.frontends.cpp import CppDiagnostic, CppParser
from pcc.frontends.cpp.driver import preprocess_cpp
from pcc.frontends.cpp.types import ArrayType, PointerType, unqualified
from pcc.runtime.freestanding_abi_spec import STDIO_FILE_FIELDS
from tests.cpp.test_cpp_cctype_header import compile_public


ROOT = Path(__file__).resolve().parents[2]
FUNCTIONS = ('remove rename tmpfile tmpnam fclose fflush fopen freopen setbuf setvbuf '
             'fprintf fscanf printf scanf snprintf sprintf sscanf vfprintf vfscanf '
             'vprintf vscanf vsnprintf vsprintf vsscanf fgetc fgets fputc fputs getc getchar '
             'putc putchar puts ungetc fread fwrite fgetpos fseek fsetpos ftell rewind clearerr '
             'feof ferror perror').split()


def parse(tail=''):
    parser = CppParser(target_triple='x86_64-unknown-linux-gnu')
    parser.parse(preprocess_cpp('#include <cstdio>\n'+tail, target_triple='x86_64-unknown-linux-gnu'))
    return parser


@pytest.mark.parametrize('name', FUNCTIONS)
def test_cstdio_standard_names_retain_one_c_linkage_entity(name):
    parser = parse()
    global_symbol = parser.scope.names[name].overloads[0]
    standard_symbol = parser.scope.names['std'].namespace_scope.names[name].overloads[0]
    assert standard_symbol is global_symbol
    assert global_symbol.language_linkage == 'C' and global_symbol.c_name == name
    assert not global_symbol.defined


def test_file_declaration_is_generated_from_real_owned_abi():
    specification = importlib.util.spec_from_file_location('stdio_abi_generator', ROOT/'scripts/gen_freestanding_stdio_abi.py')
    generator = importlib.util.module_from_spec(specification)
    specification.loader.exec_module(generator)
    assert generator.CPP_HEADER_OUTPUT.read_text() == generator.render_cpp_header()
    parser = parse()
    file_type = unqualified(parser.scope.names['FILE'].dtype)
    assert file_type.complete
    assert [member.name for member in file_type.definition.members] == [name for name,_ in STDIO_FILE_FIELDS]
    assert len(file_type.definition.members) == 8


def test_va_list_preserves_array_type_and_adjusted_parameter():
    parser = parse('extern "C" int observe(va_list);')
    dtype = parser.scope.names['va_list'].dtype
    assert isinstance(dtype, ArrayType) and dtype.bound == 1
    assert [m.name for m in dtype.element.definition.members] == ['gp_offset','fp_offset','overflow_arg_area','reg_save_area']
    parameter = parser.scope.names['observe'].overloads[0].parameters[0]
    assert isinstance(parameter, PointerType) and parameter.pointee is dtype.element


@pytest.mark.parametrize('tail', ['fpos_t position;', 'int f(){return sizeof(fpos_t);}',
                                'int f(){return BUFSIZ;}', 'int f(){return FOPEN_MAX;}',
                                'int f(){return FILENAME_MAX;}', 'int f(){return L_tmpnam;}',
                                'int f(){return TMP_MAX;}'])
def test_unimplemented_position_or_capacity_is_not_fake_storage(tail):
    with pytest.raises(CppDiagnostic):
        parse(tail)


def test_unimplemented_capacity_is_not_a_preprocessor_zero():
    with pytest.raises(RuntimeError, match='failed to evaluate'):
        preprocess_cpp('#include <cstdio>\n#if BUFSIZ\nint x;\n#endif\n', target_triple='x86_64-unknown-linux-gnu')


ORIGINAL_VA_LIST_STORAGE='int f(){va_list args;return 0;}'
VA_LIST_STORAGE_UNITS=(('original.cpp','#include <cstdio>\n'+ORIGINAL_VA_LIST_STORAGE),
                       ('entry.cpp','int f();int cpp_test_entry(){return f()+42;}'))


def test_preserved_va_list_storage_original_is_real_trivial_record_array():
    from pcc.frontends.c.ast import c_ast
    from tests.cpp.owned_execution import compile_host_cpp
    from tests.cpp.test_cpp_cstdarg import owned_units
    parser=CppParser()
    tree=parser.parse(preprocess_cpp(VA_LIST_STORAGE_UNITS[0][1]))
    function=next(node for node in tree.ext if isinstance(node,c_ast.FuncDef))
    storage=function.body.block_items[0]
    assert isinstance(storage,c_ast.Decl) and storage.name=='args' and storage.init is None
    assert isinstance(storage.type,c_ast.ArrayDecl) and storage.type.dim.value=='1'
    assert storage.type.type.type.name==parser.scope.names['__pcc_sysv_va_list'].dtype.c_tag
    units=owned_units(VA_LIST_STORAGE_UNITS)
    assert compile_host_cpp(units)==compile_host_cpp(units)


def test_nontrivial_record_arrays_still_require_lifetime_ownership():
    with pytest.raises(CppDiagnostic, match='class-array'):
        parse('struct Nontrivial{~Nontrivial();};int f(){Nontrivial args[1];return 0;}')


@pytest.mark.integration
def test_preserved_va_list_storage_original_executes_against_cpp17_reference(tmp_path,monkeypatch):
    import os
    import shutil
    import struct
    import subprocess
    from tests.cpp.owned_execution import Execution,compile_host_cpp,compile_reference_cpp,write_host_executable
    from tests.cpp.test_cpp_class_execution import record_execution
    from tests.cpp.test_cpp_cstdarg import owned_units
    with monkeypatch.context() as guard:
        def forbidden(*args,**kwargs):
            raise AssertionError('owned va_list storage build invoked external compiler')
        guard.setattr(subprocess,'Popen',forbidden)
        artifact=compile_host_cpp(owned_units(VA_LIST_STORAGE_UNITS))
        write_host_executable(artifact,tmp_path/'owned')
    compiler=shutil.which(os.environ.get('PCC_CPP_ORACLE','g++'))
    assert compiler
    reference=compile_reference_cpp(VA_LIST_STORAGE_UNITS,tmp_path/'reference',compiler=compiler)
    expected=Execution(42,struct.pack('<i',42),b'')
    record_execution(tmp_path/'owned',expected)
    record_execution(reference,expected)


def test_cstdio_public_fixed_call_retains_unresolved_runtime_symbol():
    first = compile_public('#include <cstdio>\nint main(){return std::putchar(42);}', use_compile_cache=False)
    second = compile_public('#include <cstdio>\nint main(){return std::putchar(42);}', use_compile_cache=False)
    assert first == second
    assert 'putchar' in first[0][1]
