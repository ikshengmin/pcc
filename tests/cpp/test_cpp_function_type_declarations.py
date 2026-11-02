"""N4659 [dcl.fct], [dcl.typedef], [dcl.link]; Itanium ABI 5.1.5.3.

Function-type declaration identity/projection only. These tests do not claim
indirect calls, function values, noexcept, variadic callbacks or EH support.
"""
import pytest
from pcc.frontends.c.ast import c_ast
from pcc.frontends.cpp import CppDiagnostic, CppParser
from pcc.frontends.cpp.types import FunctionType, PointerType, unqualified
from tests.cpp.owned_execution import compile_host_cpp
from tests.cpp.test_cpp_owned_execution import owned_only


ORIGINAL = ('extern "C" { using handler = void(); '
            'using compare = int(const void*, const void*); '
            'int register_handler(handler*); int compare_with(compare*); }')
UNITS = (('original.cpp', ORIGINAL), ('entry.cpp', 'int cpp_test_entry(){return 42;}'))


def test_callback_aliases_have_real_function_types_and_c_declarators(owned_only):
    parser = CppParser()
    ast = parser.parse(ORIGINAL)
    handler = parser.scope.names['handler'].dtype
    compare = parser.scope.names['compare'].dtype
    assert isinstance(handler, FunctionType) and handler.result == 'void'
    assert handler.parameters == () and handler.language_linkage == 'C'
    assert compare.result == 'int' and len(compare.parameters) == 2
    symbol = parser.scope.names['register_handler'].overloads[0]
    assert symbol.c_name == 'register_handler'
    assert symbol.parameters == (PointerType(handler),)
    callback = ast.ext[0].type.args.params[0].type
    assert isinstance(callback, c_ast.PtrDecl)
    assert isinstance(callback.type, c_ast.FuncDecl)
    assert len(callback.type.args.params) == 1  # explicit C (void), not unprototyped ()
    first, second = compile_host_cpp(UNITS), compile_host_cpp(UNITS)
    assert first == second


@pytest.mark.integration
def test_callback_declaration_originals_link_against_real_cpp17_reference(tmp_path, monkeypatch):
    from tests.cpp.test_cpp_class_execution import execute_and_record
    execute_and_record(tmp_path, monkeypatch, UNITS, 42)


def test_alias_function_linkage_is_not_name_linkage_or_alias_scope():
    parser = CppParser()
    parser.parse('extern "C" {using C=void(int); } using CPP=void(int);'
                 'using Copy=C; typedef void T(int);'
                 'int f(C*); int f(CPP*);')
    c, cpp = (parser.scope.names[n].dtype for n in ('C', 'CPP'))
    assert c != cpp and c.parameters == cpp.parameters == ('int',)
    assert parser.scope.names['Copy'].dtype is c
    assert parser.scope.names['T'].dtype == cpp
    overloads = parser.scope.names['f'].overloads
    assert len(overloads) == 2
    assert {s.c_name for s in overloads} == {'_Z1fPFYviE', '_Z1fPFviE'}


def test_parameter_cv_removal_function_adjustment_and_scope():
    parser = CppParser()
    parser.parse('using F=int(const int parameter); using G=int(int);'
                 'extern "C" int accepts(F); using parameter=int;')
    f = parser.scope.names['F'].dtype
    assert f == parser.scope.names['G'].dtype
    assert parser.scope.names['accepts'].overloads[0].parameters == (PointerType(f),)
    assert parser.scope.names['parameter'].kind == 'type'


@pytest.mark.parametrize('source,code', [
    ('using F=void(); int f(){return sizeof(F);}', 'cpp.invalid-sizeof'),
    ('using F=void(); typedef F A[2];', 'cpp.invalid-array-element'),
    ('using F=void(); using G=F();', 'cpp.invalid-function-return'),
    ('using F=void(); F f();', 'cpp.invalid-function-return'),
    ('using F=void(); F value;', 'cpp.unsupported.function-pointers'),
    ('using F=void(); F* value;', 'cpp.unsupported.function-pointers'),
    ('using F=void(); struct S{F* member;};', 'cpp.unsupported.function-pointers'),
    ('using F=void(); struct S{S(F*);};', 'cpp.unsupported.function-pointers'),
    ('using F=void(); int f(F* p){return 42;}', 'cpp.unsupported.function-pointers'),
    ('using F=void(); int f(F*); int main(){return f(nullptr);}', 'cpp.unsupported.function-pointers'),
    ('using F=void() const;', 'cpp.unsupported.function-declarators'),
    ('using F=void() noexcept;', 'cpp.unsupported.exceptions'),
    ('using F=void(int,...);', 'cpp.unsupported.variadic-functions'),
    ('struct S{}; using F=void(S*);', 'cpp.unsupported.function-pointers'),
    ('using F=void(); using G=void(F*);', 'cpp.unsupported.function-pointers'),
])
def test_function_type_unimplemented_or_invalid_paths_remain_explicit(source, code):
    with pytest.raises(CppDiagnostic) as error:
        CppParser().parse(source)
    assert error.value.code == code


def test_function_pointer_is_not_convertible_to_object_pointer():
    from pcc.frontends.cpp.types import ArithmeticTypes
    types = ArithmeticTypes()
    callback = PointerType(FunctionType('void', ()))
    assert types.conversion_rank(callback, PointerType('void')) is None
    assert types.conversion_rank(callback, callback) == 0
