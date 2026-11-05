"""Object-pointer casts: N4659 [expr.reinterpret.cast]/1,2,7,9; [expr.const].

https://timsong-cpp.github.io/cppwp/n4659/expr.reinterpret.cast
https://timsong-cpp.github.io/cppwp/n4659/expr.const
Only legal character access, pointer-interconvertible first members and
correctly aligned round trips are observed. No lifetime creation, type-punned
scalar access, whole TinyXML2 acceptance or native-pcc1 execution is implied.
"""

from pathlib import Path

import pytest

from pcc.frontends.c.ast import c_ast
from pcc.frontends.c.parse.plyparser import Coord
from pcc.frontends.cpp import CppDiagnostic, CppParser
from pcc.frontends.cpp.parser import Expression
from pcc.frontends.cpp.reinterpret_casts import convert
from pcc.frontends.cpp.types import ConstType, FunctionType, PointerType
from tests.cpp.owned_execution import compile_host_cpp
from tests.cpp.test_cpp_owned_execution import owned_only


# TinyXML2 11.0.0, upstream 9148bdf719e997d1f474be6bcc7943881046dba1,
# Zlib license. Exact original lines, used in authored standalone observers.
READ_BOM_CAST = '    const unsigned char* pu = reinterpret_cast<const unsigned char*>(p);\r\n'
WRITE_BOM_CAST = '        Write( reinterpret_cast< const char* >( bom ) );\r\n'
# Exact former negative from test_cpp_static_void_pointer_casts, now positive.
ORIGINAL_UNRELATED = 'int*f(double*p){return reinterpret_cast<int*>(p);}'

CASES = (
    ('tinyxml-read-bom-line',
     'int read(const char*p){' + READ_BOM_CAST +
     'return *pu;}int cpp_test_entry(){char a[1]={42};return read(a);}'),
    ('tinyxml-write-bom-line-and-array-decay',
     'int total=0;void Write(const char*p){total=*p;}'
     'int cpp_test_entry(){const unsigned char bom[2]={42,0};' + WRITE_BOM_CAST + 'return total;}'),
    ('original-unrelated-pointer-roundtrip', ORIGINAL_UNRELATED +
     'int cpp_test_entry(){double value=42.0;int*view=f(&value);'
     'double*back=reinterpret_cast<double*>(view);if(back!=&value)return 1;return int(*back);}'),
    ('object-character-roundtrip',
     'int cpp_test_entry(){int value=42;unsigned char*bytes=reinterpret_cast<unsigned char*>(&value);'
     'int*back=reinterpret_cast<int*>(bytes);if(back!=&value)return 1;return *back;}'),
    ('character-write-to-character-object',
     'int cpp_test_entry(){char value=0;unsigned char*p=reinterpret_cast<unsigned char*>(&value);'
     '*p=42;return value;}'),
    ('pointer-interconvertible-first-member',
     'struct Pair{int value;};int cpp_test_entry(){Pair p{42};'
     'int*q=reinterpret_cast<int*>(&p);if(q!=&p.value)return 1;return *q;}'),
    ('void-object-roundtrip',
     'int cpp_test_entry(){int value=42;void*storage=reinterpret_cast<void*>(&value);'
     'int*p=reinterpret_cast<int*>(storage);return *p;}'),
    ('const-preserving-result-type',
     'int choose(unsigned char*p){return 1;}int choose(const unsigned char*p){return *p;}'
     'int cpp_test_entry(){const char value=42;return choose(reinterpret_cast<const unsigned char*>(&value));}'),
    ('add-const-and-drop-only-top-level-pointer-cv',
     'int cpp_test_entry(){char value=42;char*const p=&value;'
     'const unsigned char*q=reinterpret_cast<const unsigned char*const>(p);return *q;}'),
    ('typed-null-and-incomplete-pointee',
     'struct O;O*view(int*p){return reinterpret_cast<O*>(p);}'
     'int cpp_test_entry(){if(view(nullptr)==nullptr)return 42;return 1;}'),
    ('nominal-enum-roundtrip',
     'enum class E{Answer=42};int cpp_test_entry(){E value=E::Answer;'
     'unsigned char*bytes=reinterpret_cast<unsigned char*>(&value);'
     'E*p=reinterpret_cast<E*>(bytes);return static_cast<int>(*p);}'),
    ('operand-once',
     'int calls=0;char*choose(char*p){calls+=1;return p;}'
     'int cpp_test_entry(){char value=42;unsigned char*p=reinterpret_cast<unsigned char*>(choose(&value));'
     'if(calls!=1)return 1;return *p;}'),
    ('unevaluated-operand-never-called',
     'int calls=0;char*choose(char*p){calls+=1;return p;}'
     'int cpp_test_entry(){char value=42;'
     'if(sizeof(reinterpret_cast<unsigned char*>(choose(&value)))!=sizeof(unsigned char*))return 1;'
     'if(calls!=0)return 2;return 42;}'),
    ('default-argument-evaluates-at-call',
     'char value=42;char*p=&value;int read(const unsigned char*q=reinterpret_cast<const unsigned char*>(p))'
     '{return *q;}int cpp_test_entry(){return read();}'),
)
PROGRAMS = tuple((name, ((name + '.cpp', source),)) for name, source in CASES)


def test_tinyxml_reinterpret_excerpts_remain_exact_upstream_bytes():
    original = Path(__file__).resolve().parents[2] / 'projects/tinyxml2-11.0.0/tinyxml2.cpp'
    lines = original.read_bytes().splitlines(keepends=True)
    assert lines[403] == READ_BOM_CAST.encode()
    assert lines[2722] == WRITE_BOM_CAST.encode()


@pytest.mark.parametrize('name,units', PROGRAMS, ids=[row[0] for row in PROGRAMS])
def test_reinterpret_pointer_artifacts_are_owned_and_stable(owned_only, name, units):
    assert compile_host_cpp(units) == compile_host_cpp(units)


INVALID = (
    ('remove-pointee-const', 'unsigned char*f(const char*p){return reinterpret_cast<unsigned char*>(p);}',
     'cpp.invalid-reinterpret-cast'),
    ('remove-const-to-void', 'void*f(const int*p){return reinterpret_cast<void*>(p);}',
     'cpp.invalid-reinterpret-cast'),
    ('nullptr-literal-to-pointer', 'int*f(){return reinterpret_cast<int*>(nullptr);}',
     'cpp.invalid-reinterpret-cast'),
    ('cast-result-is-prvalue', 'void f(char*p){reinterpret_cast<unsigned char*>(p)=nullptr;}',
     'cpp.not-lvalue'),
    ('private-target-type', 'class Owner{using Hidden=int;};'
     'int*f(char*p){return reinterpret_cast<Owner::Hidden*>(p);}', 'cpp.member-access'),
)
DEFERRED = (
    ('integer-to-pointer', 'int*f(){return reinterpret_cast<int*>(42);}', 'cpp.unsupported.syntax'),
    ('pointer-to-integer', 'unsigned long long f(int*p){return reinterpret_cast<unsigned long long>(p);}',
     'cpp.unsupported.syntax'),
    ('nullptr-to-integer', 'unsigned long long f(){return reinterpret_cast<unsigned long long>(nullptr);}',
     'cpp.unsupported.syntax'),
    ('nested-pointer', 'char**f(int**p){return reinterpret_cast<char**>(p);}',
     'cpp.unsupported.multilevel-pointers'),
    ('array-pointer', 'void f(int(*p)[2]){reinterpret_cast<char(*)[8]>(p);}', 'cpp.unsupported.syntax'),
    ('reference-result', 'void f(int&n){reinterpret_cast<char&>(n);}', 'cpp.unsupported.references'),
    ('function-pointer', 'using F=int();void f(void*p){reinterpret_cast<F*>(p);}',
     'cpp.unsupported.function-pointers'),
    ('volatile-pointee', 'void f(int*p){reinterpret_cast<volatile char*>(p);}', 'cpp.unsupported.volatile'),
    ('dependent-target', 'template<class T>void f(int*p){reinterpret_cast<T*>(p);}',
     'cpp.unsupported.pointer-templates'),
    ('concrete-cast-in-template', 'using P=char*;template<class T>void f(T x){reinterpret_cast<P>(x);}',
     'cpp.unsupported.pointer-templates'),
    ('namespace-dynamic-initializer', 'int n;char*p=reinterpret_cast<char*>(&n);',
     'cpp.unsupported.static-initialization-analysis'),
)


@pytest.mark.parametrize('name,source,code', INVALID + DEFERRED, ids=[row[0] for row in INVALID + DEFERRED])
def test_reinterpret_pointer_diagnostics_precede_emission(name, source, code):
    with pytest.raises(CppDiagnostic) as caught:
        CppParser().parse(source)
    assert caught.value.code == code


def test_reinterpret_pointer_result_retains_one_operand_without_constant_address_proof():
    parser = CppParser()
    parser.parse('')
    coord = Coord('reinterpret.cpp', 1, 1)
    operand = Expression(c_ast.FuncCall(c_ast.ID('choose', coord), None, coord), PointerType('char'))
    target = ConstType(PointerType(ConstType('unsigned char')))
    result = convert(parser, target, operand, coord)
    assert result.dtype == PointerType(ConstType('unsigned char')) and not result.lvalue
    assert isinstance(result.node, c_ast.Cast) and result.node.expr is operand.node
    assert result.static_address is None and not result.null_pointer
    assert result.constant_fact.status == 'NOT_CONSTANT'


def test_function_pointer_cannot_enter_through_the_semantic_helper():
    parser = CppParser()
    parser.parse('')
    coord = Coord('reinterpret-function.cpp', 1, 1)
    dtype = PointerType(FunctionType('int', ()))
    operand = Expression(c_ast.ID('p', coord), dtype)
    with pytest.raises(CppDiagnostic) as caught:
        convert(parser, PointerType('void'), operand, coord)
    assert caught.value.code == 'cpp.unsupported.function-pointers'


def test_parsed_result_type_drops_only_top_level_pointer_const():
    parser = CppParser()
    parser.parse('char*p=nullptr;using Result=decltype(reinterpret_cast<const unsigned char*const>(p));')
    assert parser.scope.names['Result'].dtype == PointerType(ConstType('unsigned char'))
