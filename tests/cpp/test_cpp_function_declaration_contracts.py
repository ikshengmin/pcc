"""N4659 [except.spec], [dcl.attr.noreturn]: declaration contracts, not EH.

No noexcept definitions or calls are admitted until runtime enforcement exists.
"""
import pytest
from pcc.frontends.cpp import CppDiagnostic, CppParser
from tests.cpp.owned_execution import compile_host_cpp
from tests.cpp.test_cpp_owned_execution import owned_only

SOURCE = ('extern "C" { [[noreturn]] void fatal() noexcept; '
          'int pure() noexcept(1+1==2); int fallible() noexcept(false); }')
UNITS = (('contracts.cpp', SOURCE), ('entry.cpp', 'int cpp_test_entry(){return 42;}'))


def test_contracts_are_retained_on_symbols_and_noreturn_c_projection(owned_only):
    parser = CppParser()
    ast = parser.parse(SOURCE)
    fatal = parser.scope.names['fatal'].overloads[0]
    assert fatal.nonthrowing and fatal.noreturn and fatal.c_name == 'fatal'
    assert ast.ext[0].funcspec == ['_Noreturn']
    assert parser.scope.names['pure'].overloads[0].nonthrowing
    assert not parser.scope.names['fallible'].overloads[0].nonthrowing
    first, second = compile_host_cpp(UNITS), compile_host_cpp(UNITS)
    assert first == second


@pytest.mark.integration
def test_declaration_contract_originals_match_real_cpp17_reference(tmp_path, monkeypatch):
    from tests.cpp.test_cpp_class_execution import execute_and_record
    execute_and_record(tmp_path, monkeypatch, UNITS, 42)


def test_redeclarations_preserve_the_canonical_contract():
    parser = CppParser()
    parser.parse('[[noreturn]] void f(); void f(); '
                 'extern "C" int g() noexcept; namespace n{extern "C" int g() noexcept(true);}')
    assert parser.scope.names['f'].overloads[0].noreturn
    assert parser.scope.names['g'].overloads[0] is parser.scope.names['n'].namespace_scope.names['g'].overloads[0]


@pytest.mark.parametrize('source,code', [
    ('int f() noexcept; int f();', 'cpp.conflicting-exception-specification'),
    ('extern "C" int f() noexcept; namespace n{extern "C" int f();}', 'cpp.conflicting-exception-specification'),
    ('void f(); [[noreturn]] void f();', 'cpp.invalid-noreturn'),
    ('[[noreturn]] int object;', 'cpp.invalid-noreturn'),
    ('int f() noexcept{return 42;}', 'cpp.unsupported.exceptions'),
    ('[[noreturn]] void f(){}', 'cpp.unsupported.exceptions'),
    ('[[noreturn]] void f(); void f(){}', 'cpp.unsupported.exceptions'),
    ('int f() noexcept; template<class T> int g(T n){return f();}', 'cpp.unsupported.exceptions'),
    ('int f() noexcept; int main(){return f();}', 'cpp.unsupported.exceptions'),
    ('[[noreturn]] void f(); int main(){f();return 0;}', 'cpp.unsupported.exceptions'),
    ('int x; int f() noexcept(x);', 'cpp.unsupported.exceptions'),
    ('int x; int f() noexcept(x=1);', 'cpp.unsupported.exceptions'),
    ('int side(); int f() noexcept(side());', 'cpp.unsupported.exceptions'),
    ('int f() noexcept(1/0);', 'cpp.unsupported.exceptions'),
    ('int f() throw();', 'cpp.unsupported.exceptions'),
    ('[[nodiscard]] int f();', 'cpp.unsupported.attributes'),
])
def test_contract_conflicts_or_unimplemented_enforcement_are_explicit(source, code):
    with pytest.raises(CppDiagnostic) as error:
        CppParser().parse(source)
    assert error.value.code == code
