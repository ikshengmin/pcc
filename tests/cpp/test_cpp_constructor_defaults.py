"""Declaration-bound C1 defaults, source-only C++17 obligations; all UNRUN.

N4659 [dcl.fct.default]/4-9, [class.ctor], [over.match.ctor], [over.match.list],
[dcl.init.list]/4,7: https://timsong-cpp.github.io/cppwp/n4659/dcl.fct.default
https://timsong-cpp.github.io/cppwp/n4659/dcl.init.list

TinyXML2 11.0.0 unchanged declaration/enum excerpts: tinyxml2.h:1731 and
1707-1711, revision 9148bdf719e997d1f474be6bcc7943881046dba1, Lee Thomason,
Zlib (projects/tinyxml2-11.0.0/LICENSE.txt and pcc-fixture.json). Surrounding
class members/body/observer are PCC-authored, not whole-project acceptance.
Exact outputs never depend on the relative evaluation order of a default
argument and a supplied initializer-list clause, or of two defaults.
"""

import hashlib

import pytest

from pcc.frontends.c.ast import c_ast
from pcc.frontends.cpp import CppDiagnostic, CppParser
from tests.cpp.owned_execution import compile_host_cpp
from tests.cpp.test_cpp_owned_execution import owned_only


ORIGINAL_DECLARATION = 'struct S{S(int x=1);};'
ORIGINAL_INLINE = 'struct S{S(int n=42){} };'
ORIGINAL_EXPLICIT = 'struct S{explicit S(int n=42){}};'
XML_DECLARATION = '    XMLDocument( bool processEntities = true, Whitespace whitespaceMode = PRESERVE_WHITESPACE );\r\n'
XML_DECLARATION_SHA256 = 'ec9ef4496ffa081088a815822dacc68735bd44e57389c4f36d897c19da33afab'
WHITESPACE_ENUM = ('enum Whitespace {\r\n    PRESERVE_WHITESPACE,\r\n'
                   '    COLLAPSE_WHITESPACE,\r\n    PEDANTIC_WHITESPACE\r\n};\r\n')
WHITESPACE_ENUM_SHA256 = '31bbe43664c686b09e8dcb925ca2893722034965f6d679a70458f91a130249f8'
XML_PROGRAM = (
    WHITESPACE_ENUM + 'class XMLDocument{public:' + XML_DECLARATION +
    'bool entities;Whitespace mode;};'
    'XMLDocument::XMLDocument(bool processEntities,Whitespace whitespaceMode):'
    'entities(processEntities),mode(whitespaceMode){}'
    'int cpp_test_entry(){XMLDocument a;XMLDocument b(false);XMLDocument c{true,COLLAPSE_WHITESPACE};'
    'if(!a.entities||a.mode!=PRESERVE_WHITESPACE||b.entities||b.mode!=PRESERVE_WHITESPACE) return 1;'
    'if(!c.entities||c.mode!=COLLAPSE_WHITESPACE)return 1;return 42;}'
)

SINGLE_PROGRAMS = (
    ('original-inline-default', ORIGINAL_INLINE + 'int cpp_test_entry(){S s;return 42;}'),
    ('original-explicit-default', ORIGINAL_EXPLICIT + 'int cpp_test_entry(){S a;S b{};return 42;}'),
    ('exact-upstream-constructor-defaults', XML_PROGRAM),
    ('inline-default-value', 'struct S{S(int n=42):value(n){}int value;};'
     'int cpp_test_entry(){S s;return s.value;}'),
    ('out-of-line-body-inherits-declaration-default', 'struct S{S(int n=42);int value;};'
     'S::S(int n):value(n){}int cpp_test_entry(){S s;return s.value;}'),
    ('evaluated-once-each-omitted-call', 'int count=0;int next(){count+=1;return 21;}'
     'struct S{S(int n=next()):value(n){}int value;};'
     'int cpp_test_entry(){S a;S b;if(count!=2)return 1;return a.value+b.value;}'),
    ('explicit-argument-suppresses-default-effect', 'int count=0;int next(){count+=1;return 1;}'
     'struct S{S(int n=next()):value(n){}int value;};'
     'int cpp_test_entry(){S s(42);if(count!=0)return 1;return s.value;}'),
    ('two-defaults-no-relative-order-assumption', 'int first_count=0;int second_count=0;'
     'int first(){first_count+=1;return 20;}int second(){second_count+=1;return 22;}'
     'struct S{S(int a=first(),int b=second()):value(a+b){}int value;};'
     'int cpp_test_entry(){S s;if(first_count!=1||second_count!=1)return 1;return s.value;}'),
    ('clauses-ordered-default-independent', 'int trace=0;int defaults=0;'
     'int step(int n){trace=trace*10+n;return n;}int fallback(){defaults+=1;return 3;}'
     'struct S{S(int a,int b,int c=fallback()){value=1;if(a==1&&b==2&&c==3)value=42;}int value;};'
     'int cpp_test_entry(){S s{step(1),step(2)};if(trace!=12||defaults!=1)return 1;return s.value;}'),
    ('declaration-binding-call-time-value', 'int seed=1;struct S{S(int n=seed):value(n){}int value;};'
     'int cpp_test_entry(){seed=42;int seed=7;S s;return s.value;}'),
    ('later-parameter-does-not-hide-outer-default', 'int seed=40;'
     'struct S{S(int a=seed,int seed=2):value(a+seed){}int value;};'
     'int cpp_test_entry(){S s;return s.value;}'),
    ('parameter-in-unevaluated-default', 'struct S{S(int n,int size=sizeof(n)):value(size+38){}int value;};'
     'int cpp_test_entry(){S s(1);return s.value;}'),
    ('completed-class-private-later-enum', 'class S{public:S(int n=Answer):value(n){}int value;private:enum{Answer=42};};'
     'int cpp_test_entry(){S s;return s.value;}'),
    ('completed-class-later-static-function', 'struct S{S(int n=answer()):value(n){}'
     'static int answer(){return 42;}int value;};int cpp_test_entry(){S s{};return s.value;}'),
    ('default-is-not-a-list-clause', 'struct S{S(int n=42.75):value(n){}int value;};'
     'int cpp_test_entry(){S s{};return s.value;}'),
    ('only-supplied-prefix-is-narrowing-checked', 'struct S{S(int a,int b=2.75):value(a+b){}int value;};'
     'int cpp_test_entry(){S s{40};return s.value;}'),
    ('rank-supplied-arguments-not-defaults', 'struct S{S(int a,int b=22):value(a+b){}S(long):value(1){}int value;};'
     'int cpp_test_entry(){S s(20);return s.value;}'),
    ('explicit-default-c1-direct', 'struct S{explicit S(int n=42):value(n){}int value;};'
     'int cpp_test_entry(){S s;return s.value;}'),
    ('scalar-copy-with-trailing-default', 'struct S{S(int a,int b=22):value(a+b){}int value;};'
     'int cpp_test_entry(){S s=20;return s.value;}'),
    ('copy-list-with-trailing-default', 'struct S{S(int a,int b=22):value(a+b){}int value;};'
     'int cpp_test_entry(){S s={20};return s.value;}'),
    ('default-construction-loop-cleanup', 'int trace=0;struct T{T(int n=1):n(n){}~T(){trace=trace*10+n;}int n;};'
     'int cpp_test_entry(){int i=0;for(T init(9);i<2;++i){T body;continue;}'
     'if(trace!=119)return 1;return 42;}'),
    ('pointer-default', 'struct S{S(int*p=nullptr){value=1;if(p==nullptr)value=42;}int value;};'
     'int cpp_test_entry(){S s;return s.value;}'),
)

CROSS_DECLARATION = 'struct S{S(int n=42);int value;};'
PROGRAMS = tuple((name, ((name+'.cpp', source),)) for name, source in SINGLE_PROGRAMS) + (
    ('original-declaration-default', (
        ('original.cpp', ORIGINAL_DECLARATION),
        ('entry.cpp', ORIGINAL_DECLARATION + 'int count=0;S::S(int n){count+=n;}'
         'int cpp_test_entry(){S s;return count+41;}'),
    )),
    ('default-not-in-cross-tu-c1-abi', (
        ('definition.cpp', CROSS_DECLARATION + 'S::S(int n):value(n){}'),
        ('entry.cpp', CROSS_DECLARATION + 'int cpp_test_entry(){S s;return s.value;}'),
    )),
)

INVALID = (
    ('missing-trailing-default', 'struct S{S(int a=1,int b);};', 'cpp.default-argument-trailing'),
    ('self-parameter-evaluated', 'struct S{S(int n=n);};', 'cpp.default-argument-parameter-use'),
    ('earlier-parameter-evaluated', 'struct S{S(int a,int b=a);};', 'cpp.default-argument-parameter-use'),
    ('invalid-default-conversion', 'struct S{S(int*p=42);};', 'cpp.invalid-conversion'),
    ('undeclared-default', 'struct S{S(int n=absent);};', 'cpp.undeclared'),
    ('bare-nonstatic-member-default', 'struct S{int value;S(int n=value);};', 'cpp.unsupported.default-argument-context'),
    ('this-in-default', 'struct S{S(S*p=this);};', 'cpp.this-outside-member'),
    ('duplicate-member-default', 'struct S{S(int n=1);S(int n=1);};', 'cpp.redeclaration'),
    ('ambiguous-defaulted-overloads', 'struct S{S(int n=1);S(long n=2);};int f(){S s;return 0;}', 'cpp.ambiguous-overload'),
    ('ambiguous-default-and-zero-parameter', 'struct S{S();S(int n=1);};int f(){S s{};return 0;}', 'cpp.ambiguous-overload'),
    ('explicit-default-copy-list', 'struct S{explicit S(int n=42);};int f(){S s={};return 0;}', 'cpp.explicit-copy-list'),
    ('supplied-prefix-still-narrows', 'struct S{S(unsigned char n,int extra=0);};'
     'int f(){S s{256};return 0;}', 'cpp.narrowing-conversion'),
    ('private-default-constructor', 'class S{S(int n=42);};int f(){S s;return 0;}', 'cpp.member-access'),
    ('outside-addition-creates-default-constructor', 'struct S{S(int);};S::S(int n=42){}', 'cpp.unsupported.default-argument-context'),
    ('outside-default-redefinition', 'struct S{S(int n=1);};S::S(int n=2){}', 'cpp.unsupported.default-argument-context'),
)

DEFERRED = (
    ('outside-trailing-default-addition', 'struct S{S(int,int);};S::S(int a,int b=22){}', 'cpp.unsupported.default-argument-context'),
    ('reference-default', 'int value=42;struct S{S(const int&n=value);};', 'cpp.unsupported.default-argument-context'),
    ('class-template-constructor-default', 'template<class T>struct Box{Box(T n=1){}};', 'cpp.unsupported.default-argument-context'),
    ('braced-default', 'struct S{S(int n={42});};', 'cpp.unsupported.default-argument-context'),
)


@pytest.mark.parametrize('name,units', PROGRAMS, ids=[row[0] for row in PROGRAMS])
def test_constructor_default_originals_compile_deterministically(owned_only, name, units):
    assert compile_host_cpp(units) == compile_host_cpp(units)


@pytest.mark.parametrize('source', (ORIGINAL_DECLARATION, ORIGINAL_INLINE, ORIGINAL_EXPLICIT))
def test_exact_formerly_rejected_constructor_default_originals_are_positive(source):
    parser = CppParser()
    parser.parse(source)
    constructor, = parser.scope.names['S'].dtype.definition.constructors.overloads
    assert constructor.parameters == ('int',)
    assert constructor.c_name == '_ZN1SC1Ei'  # Defaults do not change the ABI signature.
    assert constructor.default_arguments[0].expression is not None


def test_constructor_default_retains_tokens_scope_and_copies_call_expression():
    parser = CppParser()
    tree = parser.parse('int seed=42;struct S{S(int n=seed){}};int f(){S s;return 0;}')
    definition = parser.scope.names['S'].dtype.definition
    constructor, = definition.constructors.overloads
    default, = constructor.default_arguments
    assert default.function is constructor and default.scope.parent is definition.scope
    assert [token.value for token in default.tokens] == ['seed']
    assert all(any(token is original for original in parser.cursor.tokens) for token in default.tokens)
    assert default.expression.node.name == parser.scope.names['seed'].c_name
    call = tree.ext[-1].body.block_items[1]
    assert isinstance(call, c_ast.FuncCall) and len(call.args.exprs) == 2
    assert call.args.exprs[1] is not default.expression.node


def test_upstream_constructor_default_excerpt_bytes_are_preserved():
    assert hashlib.sha256(XML_DECLARATION.encode()).hexdigest() == XML_DECLARATION_SHA256
    assert hashlib.sha256(WHITESPACE_ENUM.encode()).hexdigest() == WHITESPACE_ENUM_SHA256
    parser = CppParser()
    parser.parse(XML_PROGRAM)
    constructor, = parser.scope.names['XMLDocument'].dtype.definition.constructors.overloads
    assert all(record.expression is not None for record in constructor.default_arguments)


@pytest.mark.parametrize('name,source,code', INVALID, ids=[row[0] for row in INVALID])
def test_invalid_constructor_default_stops_before_emission(name, source, code, monkeypatch):
    from pcc.backend import owned_object_emit

    def forbidden(*args, **kwargs):
        raise AssertionError('invalid constructor default reached object emission')

    monkeypatch.setattr(owned_object_emit, 'emit_owned_object', forbidden)
    with pytest.raises(CppDiagnostic) as error:
        compile_host_cpp(((name+'.cpp', source),))
    assert error.value.code == code


@pytest.mark.parametrize('name,source,code', DEFERRED, ids=[row[0] for row in DEFERRED])
def test_unowned_constructor_default_domains_remain_explicit(name, source, code):
    with pytest.raises(CppDiagnostic) as error:
        CppParser().parse(source)
    assert error.value.code == code
