"""Class unnamed enums: N4659 [dcl.enum], [basic.link]/8.2, [class.access].

https://timsong-cpp.github.io/cppwp/n4659/dcl.enum
https://timsong-cpp.github.io/cppwp/n4659/basic.link#8
https://itanium-cxx-abi.github.io/cxx-abi/abi.html#mangling-type
Original tests; no external compiler implementation is copied.
"""
import hashlib

import pytest

from pcc.frontends.cpp import CppDiagnostic, CppParser
from pcc.frontends.cpp.types import mangle_function, PointerType
from tests.cpp.owned_execution import compile_host_cpp
from tests.cpp.test_cpp_owned_execution import owned_only
from tests.cpp.test_cpp_inline_methods import SET_DECLARATION, SET_DECLARATION_SHA256

ORIGINAL_CLASS_DECLARATION = 'struct S{enum{A};};'
SINGLE_PROGRAMS = (
    ('enum-successors', 'struct S{enum{A=40,B,C};};int cpp_test_entry(){return S::C;}'),
    ('fixed-storage', 'struct S{enum:unsigned char{A=42};};int cpp_test_entry(){if(sizeof(decltype(S::A))!=1)return 1;return S::A;}'),
    ('distinct-overloads', 'struct S{enum{A=20};enum{B=22};};int f(decltype(S::A)x){return int(x);}int f(decltype(S::B)x){return int(x);}int cpp_test_entry(){return f(S::A)+f(S::B);}'),
    ('private-later-member', 'class S{public:int value(){return A;}private:enum{A=42};};int cpp_test_entry(){S s;S*p=&s;return p->value();}'),
    ('private-enumerator-public-alias', 'class S{enum{A=42};public:using E=decltype(A);static E value(){return A;}};int f(S::E x){return int(x);}int cpp_test_entry(){return f(S::value());}'),
    ('constructor-enum-parameter', 'struct S{enum{A=42};int n;S(decltype(A));};S::S(decltype(S::A)x):n(int(x)){}int cpp_test_entry(){S s(S::A);return s.n;}'),
    ('body-phase-and-promotion', 'struct S{enum{A=41,B=A+1};};int cpp_test_entry(){int x=S::B;return x;}'),
)
# Preserve the exact rejected sources. Empty unnamed enum-specifier grammar
# does not override [class.mem]/2's member-name requirement.
INVALID_SHARED_ORIGINAL = 'struct S{enum{};enum Named{N};enum{A=42};enum{B=7};};using E=decltype(S::A);E echo(E);'
INVALID_SYMBOL_ORIGINAL = ('struct S{enum{};enum Named{N};enum{A};enum{B};};'
                           'int f(decltype(S::A)x,S*p){return 0;}'
                           'int f(S*p,decltype(S::B)x){return 0;}'
                           'int f(decltype(S::A)x,decltype(S::A)y){return 0;}'
                           'int cpp_test_entry(){return 0;}')
INVALID_ORIGINAL_PROGRAMS = (
    ('original-cross-tu-empty-enum', (('provider.cpp', INVALID_SHARED_ORIGINAL+'E echo(E x){return x;}'),
                                     ('entry.cpp', INVALID_SHARED_ORIGINAL+'int cpp_test_entry(){return int(echo(S::A));}'))),
    ('original-symbol-empty-enum', (('unnamed-enum-symbols.cpp', INVALID_SYMBOL_ORIGINAL),)),
)
# Separate new legal input: an unused enumerator still introduces a name and
# its unnamed enum consumes the first lexical ordinal. Named empty enum is OK.
SHARED = 'struct S{enum{Unused};enum Named{};enum{A=42};enum{B=7};};using E=decltype(S::A);E echo(E);'
PROGRAMS = tuple((name, ((name+'.cpp', source),)) for name,source in SINGLE_PROGRAMS) + (
    ('cross-tu-unused-enumerator-ordinal', (('provider.cpp', SHARED+'E echo(E x){return x;}'),
                               ('entry.cpp', SHARED+'int cpp_test_entry(){return int(echo(S::A));}'))),
    ('original-class-declaration', (('original.cpp', ORIGINAL_CLASS_DECLARATION),
                                  ('entry.cpp', ORIGINAL_CLASS_DECLARATION+'int cpp_test_entry(){return S::A+42;}'))),
)

# Exact upstream private declaration (TinyXML2 11.0.0 tinyxml2.h:183-186,
# Lee Thomason, Zlib; projects/tinyxml2-11.0.0/pcc-fixture.json).
PRIVATE_DECLARATION = '    enum {\r\n        NEEDS_FLUSH = 0x100,\r\n        NEEDS_DELETE = 0x200\r\n    };'
PRIVATE_SET_PROGRAM = (
    '#define TIXMLASSERT( x ) do {} while(false)\n'
    'int resets=0;class StrPair{public:\n'+SET_DECLARATION+
    '\nvoid Reset();int Inspect(char*a,char*b)const{'
    'if(_start!=a||_end!=b)return 1;if(_flags!=(2|NEEDS_FLUSH))return 2;return 42;}'
    '\nprivate:\n'+PRIVATE_DECLARATION+
    '\nint _flags;char*_start;char*_end;};'
    'void StrPair::Reset(){resets=resets+1;}'
    'int main(){StrPair value;StrPair*p=&value;char bytes[2]={};'
    'p->Set(bytes,bytes+1,2);if(resets!=1)return 3;return p->Inspect(bytes,bytes+1);}'
)


def test_anonymous_enum_has_nominal_identity_without_an_invented_source_name():
    parser = CppParser()
    parser.parse('struct S{enum{A=1,B=2};enum{C=3};};')
    owner = parser.scope.names['S'].dtype
    scope = owner.definition.scope
    first, second = scope.names['A'].dtype, scope.names['C'].dtype
    assert first is scope.names['B'].dtype and first is not second
    assert set(scope.names) == {'A','B','C'}
    assert first.enclosing_class is second.enclosing_class is owner
    assert first.scope.names['A'] is scope.names['A']
    assert scope.names['A'].owner is scope and scope.names['A'].access == 'public'
    assert first.unnamed_ordinal == 0 and second.unnamed_ordinal == 1
    assert first.promotion_type(parser.types) == 'int'
    assert not first.internal_linkage
    assert mangle_function('f', (first, PointerType(owner))) == '_Z1fN1SUt_EPS_'
    assert mangle_function('f', (PointerType(owner), second)) == '_Z1fP1SNS_Ut0_E'


def test_unused_unnamed_enum_counts_but_named_empty_enum_and_alias_do_not_rename_type():
    parser = CppParser()
    parser.parse(SHARED)
    owner = parser.scope.names['S'].dtype
    first = owner.definition.scope.names['A'].dtype
    second = owner.definition.scope.names['B'].dtype
    assert owner.definition.unnamed_type_count == 3
    assert first.unnamed_ordinal == 1 and second.unnamed_ordinal == 2
    assert parser.scope.names['E'].dtype is first
    assert mangle_function('echo', (first,)) == '_Z4echoN1SUt0_E'
    assert mangle_function('echo', (second,)) == '_Z4echoN1SUt1_E'


def test_original_class_declaration_is_preserved_and_gets_a_real_enum_type():
    parser = CppParser()
    parser.parse(ORIGINAL_CLASS_DECLARATION)
    symbol = parser.scope.names['S'].dtype.definition.scope.names['A']
    assert symbol.dtype.cpp_enum_type and symbol.enum_value == 0


@pytest.mark.parametrize('source,code', (
    ('struct S{enum:int;};', 'cpp.enum-definition-required'),
    ('struct S{enum{};};', 'cpp.enum-member-name-required'),
    ('struct S{enum:int{};};', 'cpp.enum-member-name-required'),
    ('struct S{enum{} object;};', 'cpp.unsupported.enum-declarators'),
    ('struct S{enum class{A};};', 'cpp.enum-name-required'),
    ('struct S{enum struct:int{A};};', 'cpp.enum-name-required'),
    ('class S{enum{A=42};};int f(){return S::A;}', 'cpp.member-access'),
    ('struct S{enum{A};enum{A};};', 'cpp.redeclaration'),
    ('struct S{int A;enum{A};};', 'cpp.redeclaration'),
    ('struct S{enum{S};};', 'cpp.class-name-member'),
    ('struct S{enum:float{A};};', 'cpp.enum-underlying-type'),
    ('struct S{enum{A} value;};', 'cpp.unsupported.enum-declarators'),
    ('enum{A};', 'cpp.unsupported.anonymous-enums'),
))
def test_anonymous_enum_invalid_or_unimplemented_routes_are_explicit(source, code):
    with pytest.raises(CppDiagnostic) as error:
        CppParser().parse(source)
    assert error.value.code == code


@pytest.mark.parametrize('name,units', PROGRAMS, ids=[item[0] for item in PROGRAMS])
def test_anonymous_enum_original_artifacts_are_byte_stable(owned_only, name, units):
    assert compile_host_cpp(units) == compile_host_cpp(units)


def test_private_after_set_original_tokens_remain_and_access_binds_after_class_completion():
    from pcc.frontends.cpp.driver import preprocess_cpp
    assert hashlib.sha256(SET_DECLARATION.encode()).hexdigest() == SET_DECLARATION_SHA256
    assert PRIVATE_SET_PROGRAM.index(SET_DECLARATION) < PRIVATE_SET_PROGRAM.index(PRIVATE_DECLARATION)
    parser = CppParser()
    parser.parse(preprocess_cpp(PRIVATE_SET_PROGRAM, target_triple='x86_64-unknown-linux-gnu'))
    definition = parser.scope.names['StrPair'].dtype.definition
    symbol = definition.scope.names['NEEDS_FLUSH']
    assert symbol.access == 'private' and symbol.dtype.unnamed_ordinal == 0
    assert symbol.enum_value == 0x100
    assert next(item for item in definition.inline_methods if item.symbol.name == 'Set').function is not None


@pytest.mark.parametrize('name,units', INVALID_ORIGINAL_PROGRAMS, ids=[item[0] for item in INVALID_ORIGINAL_PROGRAMS])
def test_original_empty_enum_sources_are_rejected_by_member_declaration_rule(name, units):
    for filename, source in units:
        with pytest.raises(CppDiagnostic) as error:
            CppParser().parse(source, filename)
        assert error.value.code == 'cpp.enum-member-name-required'
