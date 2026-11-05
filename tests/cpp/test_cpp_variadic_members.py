"""Ordinary variadic members, N4659 [class.mfct], [expr.call], [cstdarg.syn].

https://timsong-cpp.github.io/cppwp/n4659/class.mfct
https://timsong-cpp.github.io/cppwp/n4659/expr.call
https://timsong-cpp.github.io/cppwp/n4659/cstdarg.syn
Itanium ABI 5.1.5: ellipsis is z; implicit this is not a mangled parameter.
Only the existing SysV x86_64 definition/va_list owner is used. Templates,
class-value tails, reference signatures and other va_list ABIs stay bounded.
"""
from pathlib import Path

import pytest

from pcc.frontends.c.ast import c_ast
from pcc.frontends.cpp import CppDiagnostic, CppParser
from pcc.frontends.cpp.driver import preprocess_cpp
from tests.cpp.owned_execution import compile_host_cpp
from tests.cpp.test_cpp_owned_execution import owned_only

HEADER = '#include <cstdarg>\n'
# Exact TinyXML2 11.0.0 declaration/signature and va_start/end lines, CRLF
# included. Revision 9148bdf719e997d1f474be6bcc7943881046dba1, Zlib license.
# The containing class/body below is an authored observer, not full SetError.
TINYXML_DECLARATION = '    void SetError( XMLError error, int lineNum, const char* format, ... );\r\n'
TINYXML_SIGNATURE = 'void XMLDocument::SetError( XMLError error, int lineNum, const char* format, ... )\r\n'
TINYXML_START = '\t\tva_start(va, format);\r\n'
TINYXML_END = '\t\tva_end(va);\r\n'
ORIGINAL_DECLARATION = 'struct S{int f(int,...);};'
ORIGINAL_INLINE = 'struct S{int f(int n,...){return n;}};'
SUM_BODY = ('va_list ap;va_start(ap,count);int n=0;for(int i=0;i<count;++i)n+=va_arg(ap,int);'
            'va_end(ap);return n;')
CASES = (
    ('tinyxml-seterror-exact-ellipsis-and-cursor-lines', HEADER+
     'namespace tinyxml2{enum XMLError{XML_SUCCESS=0};struct XMLDocument{int result;'+
     TINYXML_DECLARATION+'};'+TINYXML_SIGNATURE+'{va_list va;'+TINYXML_START+
     'result=int(error)+lineNum+va_arg(va,int);'+TINYXML_END+'}}'
     'int cpp_test_entry(){tinyxml2::XMLDocument doc{0};'
     'doc.SetError(tinyxml2::XML_SUCCESS,17,nullptr,25);return doc.result;}'),
    ('out-of-class-ordinary-member', HEADER+'struct S{int base;int take(int,...);};'
     'int S::take(int tag,...){va_list ap;va_start(ap,tag);int n=va_arg(ap,int);va_end(ap);return base+n;}'
     'int cpp_test_entry(){S s{17};return s.take(0,25);}'),
    ('hidden-this-crosses-gp-register-boundary', HEADER+'struct S{int sum(int count,...){'+SUM_BODY+'}};'
     'int cpp_test_entry(){S s{};return s.sum(8,5,5,5,5,5,5,6,6);}'),
    ('this-and-five-fixed-integers-force-stack-tail', HEADER+
     'struct S{int take(int a,int b,int c,int d,int last,...){va_list ap;va_start(ap,last);'
     'int n=va_arg(ap,int);va_end(ap);return a+b+c+d+last+n;}};'
     'int cpp_test_entry(){S s{};return s.take(1,2,3,4,5,27);}'),
    ('fixed-fp-and-floating-register-stack-tail', HEADER+
     'struct S{int sum(double start,int count,...){va_list ap;va_start(ap,count);double n=start;'
     'for(int i=0;i<count;++i)n+=va_arg(ap,double);va_end(ap);return int(n)-3;}};'
     'int cpp_test_entry(){S s{};return s.sum(0.0,9,float(1.0),2.0,3.0,4.0,5.0,6.0,7.0,8.0,9.0);}'),
    ('interleaved-gp-fp-overflow-arguments', HEADER+
     'struct S{int take(int tag,...){va_list ap;va_start(ap,tag);int integers=0;double real=0.0;'
     'for(int i=0;i<8;++i){integers+=va_arg(ap,int);real+=va_arg(ap,double);}'
     'real+=va_arg(ap,double);va_end(ap);return integers+int(real);}};'
     'int cpp_test_entry(){S s{};return s.take(0,1,1.0,1,2.0,1,3.0,1,4.0,1,5.0,1,6.0,1,7.0,1,8.0,-2.0);}'),
    ('member-tail-default-promotions', HEADER+'struct S{int take(int tag,...){va_list ap;va_start(ap,tag);'
     'int a=va_arg(ap,int);int b=va_arg(ap,int);double c=va_arg(ap,double);void*p=va_arg(ap,void*);'
     'va_end(ap);if(p!=nullptr)return 1;return a+b+int(c);}};'
     'int cpp_test_entry(){S s{};return s.take(0,true,char(17),float(24.0),nullptr);}'),
    ('const-member-keeps-object-cv', HEADER+'struct S{int base;int take(int tag,...)const{'
     'va_list ap;va_start(ap,tag);int n=va_arg(ap,int);va_end(ap);return base+n;}};'
     'int cpp_test_entry(){const S s{17};return s.take(0,25);}'),
    ('static-member-has-no-hidden-gp-slot', HEADER+'struct S{static int sum(int count,...){'+SUM_BODY+'}};'
     'int cpp_test_entry(){return S::sum(8,5,5,5,5,5,5,6,6);}'),
    ('object-qualified-static-receiver-before-tail', HEADER+'int trace=0;struct S{static int take(int tag,...){'
     'va_list ap;va_start(ap,tag);int n=va_arg(ap,int);va_end(ap);return n;}};'
     'S*receiver(S*p){trace=trace*10+1;return p;}int arg(){trace=trace*10+2;return 42;}'
     'int cpp_test_entry(){S s{};int n=receiver(&s)->take(0,arg());if(trace!=12)return 1;return n;}'),
    ('nonstatic-receiver-before-tail', HEADER+'int trace=0;struct S{int take(int tag,...){'
     'va_list ap;va_start(ap,tag);int n=va_arg(ap,int);va_end(ap);return n;}};'
     'S*receiver(S*p){trace=trace*10+1;return p;}int arg(){trace=trace*10+2;return 42;}'
     'int cpp_test_entry(){S s{};int n=receiver(&s)->take(0,arg());if(trace!=12)return 1;return n;}'),
    ('fixed-conversion-beats-member-ellipsis', 'struct S{int f(int,int){return 42;}int f(int,...){return 1;}};'
     'int cpp_test_entry(){S s{};return s.f(1,2);}'),
    ('fixed-parameter-rank-before-empty-ellipsis', 'struct S{int f(long){return 1;}int f(int,...){return 42;}};'
     'int cpp_test_entry(){S s{};return s.f(1);}'),
    ('same-fixed-signature-keeps-ellipsis-identity', 'struct S{int f(int){return 1;}int f(int,...){return 42;}};'
     'int cpp_test_entry(){S s{};return s.f(0,1);}'),
    ('scalar-reference-result-from-variadic-member', 'struct S{int value;int&take(int,...){return value;}};'
     'int cpp_test_entry(){S s{17};int&r=s.take(0,1);r=42;return s.value;}'),
    ('array-decay-in-member-ellipsis', HEADER+'struct S{int take(int tag,...){va_list ap;va_start(ap,tag);'
     'int*p=va_arg(ap,int*);va_end(ap);return p[0]+p[1];}};'
     'int cpp_test_entry(){S s{};int a[2]={17,25};return s.take(0,a);}'),
    ('enum-unsigned-and-pointer-tail', HEADER+'enum E{Part=17};struct S{int take(int tag,...){'
     'va_list ap;va_start(ap,tag);int a=va_arg(ap,int);unsigned int high=va_arg(ap,unsigned int);'
     'int*p=va_arg(ap,int*);va_end(ap);if(high!=4294967295U)return 1;return a+*p;}};'
     'int cpp_test_entry(){S s{};int n=25;return s.take(0,Part,4294967295U,&n);}'),
    ('sizeof-variadic-member-does-not-call', 'struct S{int f(int,...);};'
     'int cpp_test_entry(){S s{};if(sizeof(s.f(0,1))!=sizeof(int))return 1;return 42;}'),
    ('va-copy-in-member-body', HEADER+'struct S{int take(int tag,...){va_list ap;va_list cp;va_start(ap,tag);'
     'va_copy(cp,ap);int a=va_arg(cp,int);int b=va_arg(ap,int);va_end(cp);va_end(ap);return a+b;}};'
     'int cpp_test_entry(){S s{};return s.take(0,21);}'),
)
PROGRAMS = tuple((name, ((name+'.cpp', source),)) for name, source in CASES) + (
    ('preserved-ordinary-member-declaration', (
        ('original.cpp', ORIGINAL_DECLARATION),
        ('original-cstdarg.cpp', HEADER+ORIGINAL_DECLARATION),
        ('entry.cpp', HEADER+ORIGINAL_DECLARATION+
         'int S::f(int tag,...){va_list ap;va_start(ap,tag);int n=va_arg(ap,int);va_end(ap);return n;}'
         'int cpp_test_entry(){S s{};return s.f(0,42);}'),
    )),
    ('out-of-class-provider-and-caller-translation-units', (
        ('provider.cpp', HEADER+'struct S{int base;int take(int,...);};'
         'int S::take(int tag,...){va_list ap;va_start(ap,tag);int n=va_arg(ap,int);va_end(ap);return base+n;}'),
        ('caller.cpp', 'struct S{int base;int take(int,...);};'
         'int cpp_test_entry(){S s{17};return s.take(0,25);}'),
    )),
    ('preserved-inline-member-definition', (
        ('original.cpp', ORIGINAL_INLINE),
        ('entry.cpp', ORIGINAL_INLINE+'int cpp_test_entry(){S s{};return s.f(42);}'),
    )),
)
PUBLIC_SOURCE = HEADER+'struct S{int sum(int count,...){'+SUM_BODY+'}};int main(){S s{};return s.sum(8,5,5,5,5,5,5,6,6);}'


def owned_units(units):
    return tuple((name, preprocess_cpp(source)) for name, source in units)


def test_tinyxml_member_ellipsis_preserves_exact_upstream_lines():
    root = Path(__file__).resolve().parents[2] / 'projects/tinyxml2-11.0.0'
    header = (root/'tinyxml2.h').read_bytes().splitlines(keepends=True)
    source = (root/'tinyxml2.cpp').read_bytes().splitlines(keepends=True)
    assert header[1962] == TINYXML_DECLARATION.encode()
    assert source[2496] == TINYXML_SIGNATURE.encode()
    assert source[2516] == TINYXML_START.encode()
    assert source[2518] == TINYXML_END.encode()


@pytest.mark.parametrize('name,units', PROGRAMS, ids=[row[0] for row in PROGRAMS])
def test_variadic_member_artifacts_are_owned_and_stable(owned_only, name, units):
    assert compile_host_cpp(owned_units(units)) == compile_host_cpp(owned_units(units))


def test_member_ellipsis_identity_symbols_and_hidden_parameter_projection():
    parser = CppParser()
    tree = parser.parse('struct S{int f(int,...);int f(int);static int g(int,...);int h(int,...)const;};')
    owner = parser.scope.names['S'].dtype
    variadic, fixed = owner.definition.scope.names['f'].overloads
    static, = owner.definition.scope.names['g'].overloads
    const, = owner.definition.scope.names['h'].overloads
    assert variadic.variadic and not fixed.variadic
    assert (variadic.c_name, fixed.c_name, static.c_name, const.c_name) == (
        '_ZN1S1fEiz', '_ZN1S1fEi', '_ZN1S1gEiz', '_ZNK1S1hEiz')
    declarations = {node.name: node for node in tree.ext if isinstance(node, c_ast.Decl) and isinstance(node.type, c_ast.FuncDecl)}
    assert len(declarations[variadic.c_name].type.args.params) == 3
    assert len(declarations[static.c_name].type.args.params) == 2
    assert isinstance(declarations[variadic.c_name].type.args.params[-1], c_ast.EllipsisParam)
    assert isinstance(declarations[static.c_name].type.args.params[-1], c_ast.EllipsisParam)


INVALID = (
    ('duplicate-variadic-member', 'struct S{int f(int,...);int f(int,...);};', 'cpp.redeclaration'),
    ('outside-definition-drops-ellipsis', 'struct S{int f(int,...);};int S::f(int){return 0;}', 'cpp.no-matching-member'),
    ('outside-definition-adds-ellipsis', 'struct S{int f(int);};int S::f(int,...){return 0;}', 'cpp.no-matching-member'),
    ('const-receiver', 'struct S{int f(int,...);};int g(const S*p){return p->f(0,1);}', 'cpp.no-viable-overload'),
    ('private-member', 'class S{int f(int,...);};int g(S*p){return p->f(0,1);}', 'cpp.member-access'),
    ('missing-fixed-argument', 'struct S{int f(int,...);};int g(S*p){return p->f();}', 'cpp.no-viable-overload'),
    ('empty-tail-signature-ambiguity', 'struct S{int f(int);int f(int,...);};int g(S*p){return p->f(0);}', 'cpp.ambiguous-overload'),
    ('static-const', 'struct S{static int f(int,...)const;};', 'cpp.static-member-cv'),
    ('assignment-ellipsis', 'struct S{void operator=(int,...);};', 'cpp.invalid-assignment-operator'),
    ('ellipsis-is-not-final', 'struct S{int f(int,...,int);};', 'cpp.invalid-parameter'),
)
DEFERRED = (
    ('free-template', 'template<class T>int f(T n,...){return 0;}', 'cpp.unsupported.variadic-functions'),
    ('class-template', 'template<class T>struct S{int f(int,...);};', 'cpp.unsupported.variadic-functions'),
    ('reference-fixed-signature', 'struct S{int f(int&,...);};', 'cpp.unsupported.references'),
    ('class-ellipsis', 'struct A{int n;};struct S{int f(int,...);};int g(S*p){A a{42};return p->f(0,a);}', 'cpp.unsupported.class-value-abi'),
    ('scoped-enum-ellipsis', 'enum class E{A};struct S{int f(int,...);};int g(S*p){return p->f(0,E::A);}', 'cpp.unsupported.variadic-argument'),
    ('default-fixed-argument', 'struct S{int f(int n=0,...);};', 'cpp.unsupported.default-argument-context'),
    ('ellipsis-only', 'struct S{int f(...);};', 'cpp.unsupported.variadic-functions'),
)


@pytest.mark.parametrize('name,source,code', INVALID+DEFERRED, ids=[row[0] for row in INVALID+DEFERRED])
def test_variadic_member_boundaries(name, source, code):
    with pytest.raises(CppDiagnostic) as caught:
        CppParser().parse(source)
    assert caught.value.code == code


# These va_start/va_arg contract violations are not promised to be hard g++
# syntax errors, so their owned diagnostics are separate from INVALID.
OWNED_CONTRACT_ERRORS = (
    ('wrong-last-parameter', 'struct S{int f(int n,int last,...){va_list ap;va_start(ap,n);return 0;}};', 'cpp.invalid-va-start'),
    ('shadowed-last-parameter', 'struct S{int f(int n,...){va_list ap;{int n=1;va_start(ap,n);}return 0;}};', 'cpp.invalid-va-start'),
    ('promoted-last-parameter', 'struct S{int f(float n,...){va_list ap;va_start(ap,n);return 0;}};', 'cpp.invalid-va-start'),
    ('not-a-variadic-member', 'struct S{int f(int n){va_list ap;va_start(ap,n);return 0;}};', 'cpp.invalid-va-start'),
)


@pytest.mark.parametrize('name,source,code', OWNED_CONTRACT_ERRORS, ids=[row[0] for row in OWNED_CONTRACT_ERRORS])
def test_variadic_member_cursor_contracts(name, source, code):
    with pytest.raises(CppDiagnostic) as caught:
        CppParser().parse(preprocess_cpp(HEADER+source))
    assert caught.value.code == code


@pytest.mark.parametrize('target', ('aarch64-unknown-linux-gnu', 'x86_64-pc-windows-msvc'))
def test_variadic_member_definition_does_not_borrow_sysv_on_other_targets(target):
    with pytest.raises(CppDiagnostic) as caught:
        CppParser(target_triple=target).parse('struct S{int f(int,...){return 42;}};')
    assert caught.value.code == 'cpp.unsupported.varargs-target'
