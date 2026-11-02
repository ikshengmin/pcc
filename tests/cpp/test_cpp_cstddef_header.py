"""N4659 [cstddef.syn], [dcl.type.simple]/4, [conv.ptr], [conv.bool].

nullptr_t retains semantic identity (Itanium Dn) despite void* C ABI storage.
Header acquisition is not complete std::byte/offsetof/max_align_t support.
"""
import pytest

from pcc.frontends.c.ast import c_ast
from pcc.frontends.cpp import CppDiagnostic, CppParser
from pcc.frontends.cpp.driver import preprocess_cpp
from pcc.frontends.cpp.types import ConstType, PointerType, mangle_function
from tests.cpp.owned_execution import compile_host_cpp
from tests.cpp.test_cpp_class_execution import execute_and_record
from tests.cpp.test_cpp_owned_execution import owned_only


@pytest.mark.parametrize('target,signed,unsigned', (
    ('x86_64-unknown-linux-gnu', 'long', 'unsigned long'),
    ('aarch64-unknown-linux-gnu', 'long', 'unsigned long'),
    ('x86_64-pc-windows-msvc', 'long long', 'unsigned long long'),
))
def test_cstddef_uses_owned_target_integer_and_distinct_nullptr_types(target,signed,unsigned):
    parser=CppParser(target_triple=target)
    parser.parse(preprocess_cpp('#include <cstddef>\n#include <cstddef>\n',target_triple=target))
    std=parser.scope.names['std'].namespace_scope
    assert std.names['size_t'].dtype==unsigned
    assert std.names['ptrdiff_t'].dtype==signed
    assert std.names['nullptr_t'].dtype=='nullptr'
    assert std.names['nullptr_t'].dtype!=PointerType('void')
    assert std.names['byte'].dtype.scoped
    assert not std.names['max_align_t'].dtype.complete


def test_nullptr_function_projection_is_pointer_storage_with_distinct_abi_name():
    parser=CppParser()
    tree=parser.parse('using N=decltype(nullptr);N identity(N n){return n;}')
    symbol=parser.scope.names['identity'].overloads[0]
    assert symbol.dtype=='nullptr' and symbol.parameters==('nullptr',)
    assert symbol.c_name=='_Z8identityDn'
    function=next(n for n in tree.ext if isinstance(n,c_ast.FuncDef))
    assert isinstance(function.decl.type.type,c_ast.PtrDecl)
    assert isinstance(function.decl.type.args.params[0].type,c_ast.PtrDecl)
    assert mangle_function('f',('nullptr','nullptr'))=='_Z1fDnDn'
    assert mangle_function('f',(PointerType('nullptr'),))=='_Z1fPDn'


@pytest.mark.parametrize('source,expected', (
    ('int x;using T=decltype(x);','int'),
    ('const int x=1;using T=decltype(x);',ConstType('int')),
    ('int f();using T=decltype(f());','int'),
    ('int x;using T=decltype(x+1);','int'),
    ('struct S{int x;};const S*p;using T=decltype(p->x);','int'),
    ('struct S{int x;};using T=decltype(S::x);','int'),
    ('using T=decltype((nullptr));','nullptr'),
    ('void f();using T=decltype(f());','void'),
    ('using CI=const int;using T=decltype(CI(1));','int'),
    ('using CI=const int;using T=decltype(CI());','int'),
    ('using CN=const decltype(nullptr);using T=decltype(CN());','nullptr'),
    ('using P=int*const;using T=decltype(P(nullptr));',PointerType('int')),
))
def test_decltype_uses_entity_type_or_prvalue_without_evaluating_operand(source,expected):
    parser=CppParser()
    tree=parser.parse(source)
    assert parser.scope.names['T'].dtype==expected
    calls=[]
    class Calls(c_ast.NodeVisitor):
        def visit_FuncCall(self,node):
            calls.append(node)
    Calls().visit(tree)
    assert calls==[]


PREFIX='using N=decltype(nullptr);'
PROGRAMS=(
    ('identity',PREFIX+'N id(N n){return n;}int cpp_test_entry(){N n=nullptr;int*p=id(n);if(p==nullptr)return 42;return 0;}'),
    ('overload',PREFIX+'int pick(N){return 42;}int pick(void*){return 1;}int cpp_test_entry(){N n=nullptr;return pick(n);}'),
    ('const-equality',PREFIX+'int cpp_test_entry(){const N n=nullptr;N m=nullptr;if(n==m&&!n)return 42;return 0;}'),
    ('assignment',PREFIX+'int cpp_test_entry(){N n=nullptr;N m=nullptr;n=m;int*p=n;if(p==n)return 42;return 0;}'),
    ('value-initialization',PREFIX+'int cpp_test_entry(){N n=N();if(n==nullptr)return 42;return 0;}'),
    ('sizeof',PREFIX+'int cpp_test_entry(){if(sizeof(N)==sizeof(void*)&&sizeof(nullptr)==sizeof(N))return 42;return 0;}'),
    ('unevaluated-call','int count;int next(){count=count+1;return 7;}using T=decltype(next());int cpp_test_entry(){T n=42;return n+count;}'),
    ('member-entity','struct S{int n;};int cpp_test_entry(){S s{7};const S*p=&s;decltype(p->n) n=41;n=n+1;return n;}'),
)


@pytest.mark.parametrize('name,source',PROGRAMS,ids=[p[0] for p in PROGRAMS])
def test_decltype_nullptr_original_artifacts_are_byte_stable(name,source,owned_only):
    units=((name+'.cpp',source),)
    first=compile_host_cpp(units)
    assert first==compile_host_cpp(units)


@pytest.mark.integration
@pytest.mark.parametrize('name,source',PROGRAMS,ids=[p[0] for p in PROGRAMS])
def test_decltype_nullptr_originals_execute_against_explicit_cpp17_reference(name,source,tmp_path,monkeypatch):
    execute_and_record(tmp_path,monkeypatch,((name+'.cpp',source),),42)


@pytest.mark.parametrize('source,code', (
    ('int x;using T=decltype((x));','cpp.unsupported.references'),
    ('int x;using T=decltype(++x);','cpp.unsupported.references'),
    ('int x;using T=decltype(x=1);','cpp.unsupported.references'),
    ('int x;using T=decltype(x+=1);','cpp.unsupported.references'),
    ('using T=decltype(auto);','cpp.unsupported.decltype-auto'),
    ('template<class T> int f(T x){using U=decltype(x);return 0;}','cpp.unsupported.decltype-templates'),
    ('using T=decltype(missing);','cpp.undeclared'),
    ('int f(int);int f(double);using T=decltype(f);','cpp.unsupported.decltype-functions'),
    (PREFIX+'int f(){N n=0;return 0;}','cpp.invalid-conversion'),
    (PREFIX+'int f(){N n=nullptr;int x=n;return x;}','cpp.invalid-conversion'),
    (PREFIX+'int f(){N n=nullptr;bool b=n;return 0;}','cpp.invalid-conversion'),
    (PREFIX+'int f(){const N n=nullptr;return +n;}','cpp.unsupported.pointer-operators'),
    (PREFIX+'int f(){N n=nullptr;++n;return 0;}','cpp.invalid-operands'),
    (PREFIX+'int f(){N n=nullptr;return *n;}','cpp.invalid-dereference'),
))
def test_missing_or_invalid_semantics_are_not_erased(source,code):
    with pytest.raises(CppDiagnostic) as error:
        CppParser().parse(source)
    assert error.value.code==code


@pytest.mark.parametrize('tail',(
    'int f(){return sizeof(std::max_align_t);}',
    'struct S{int n;};int f(){return offsetof(S,n);}',
    'int f(){std::byte b;return int(b<<1);}',
))
def test_cstddef_unclosed_layout_and_byte_operations_stay_explicit(tail):
    with pytest.raises(CppDiagnostic):
        CppParser().parse(preprocess_cpp('#include <cstddef>\n'+tail))


HEADER_ORIGINAL='#include <cstddef>\nint main(){std::size_t n=42;std::ptrdiff_t d=0;std::nullptr_t p=nullptr;if(p!=nullptr)return 1;return int(n)+int(d);}'


def test_real_cstddef_original_has_stable_public_owned_artifacts():
    from pcc.backend.owned_object_emit import emit_owned_object
    from tests.cpp.owned_execution import TARGET
    from tests.cpp.test_cpp_public_driver import compile_cpp
    first=compile_cpp(HEADER_ORIGINAL,'cstddef.cpp')
    assert first==compile_cpp(HEADER_ORIGINAL,'cstddef.cpp')
    assert emit_owned_object(first[0][1],TARGET)==emit_owned_object(first[0][1],TARGET)


@pytest.mark.integration
def test_real_cstddef_original_executes_with_separate_system_header_reference(tmp_path,monkeypatch):
    import os
    import shutil
    import subprocess
    from pcc.backend.owned_object_emit import emit_owned_object
    from tests.cpp.owned_execution import Execution,TARGET
    from tests.cpp.test_cpp_class_execution import record_execution
    from tests.cpp.test_cpp_public_driver import compile_cpp
    from tests.cpp.test_cpp_public_driver_execution import link_object
    with monkeypatch.context() as guard:
        def forbidden(*args,**kwargs):
            raise AssertionError('owned cstddef compilation invoked external process')
        guard.setattr(subprocess,'Popen',forbidden)
        first=compile_cpp(HEADER_ORIGINAL,'cstddef.cpp')
        assert first==compile_cpp(HEADER_ORIGINAL,'cstddef.cpp')
        executable=link_object(emit_owned_object(first[0][1],TARGET))
    binary=tmp_path/'owned'
    binary.write_bytes(executable)
    binary.chmod(0o700)
    expected=Execution(42,b'',b'')
    record_execution(binary,expected)
    original=tmp_path/'cstddef.cpp'
    original.write_text(HEADER_ORIGINAL)
    oracle=tmp_path/'oracle'
    compiler=shutil.which(os.environ.get('PCC_CPP_ORACLE','g++'))
    assert compiler
    result=subprocess.run([compiler,'-std=c++17','-pedantic-errors','-O0',str(original),'-o',str(oracle)],capture_output=True,timeout=20)
    assert result.returncode==0,result.stderr
    record_execution(oracle,expected)


@pytest.mark.integration
def test_nullptr_parameter_and_result_cross_translation_units(tmp_path,monkeypatch):
    units=(('provider.cpp',PREFIX+'N identity(N n){return n;}int pick(N){return 42;}'),
           ('caller.cpp',PREFIX+'N identity(N);int pick(N);int cpp_test_entry(){return pick(identity(nullptr));}'))
    execute_and_record(tmp_path,monkeypatch,units,42)


NO_FETCH_PROGRAMS=(
    ('dot-member-base-once',PREFIX+'int count;struct S{N n;};S*pick(S*p){count=count+1;return p;}int cpp_test_entry(){S s;int*p=(*pick(&s)).n;if(p==nullptr&&count==1)return 42;return 0;}'),
    ('subscript-postincrement',PREFIX+'int cpp_test_entry(){N values[2];int i=0;int*p=values[i++];if(i==1&&p==nullptr)return 42;return 0;}'),
    ('assignment-result-conversion',PREFIX+'int cpp_test_entry(){N n;int*p=(n=nullptr);if(!n&&p==nullptr)return 42;return 0;}'),
    ('scalar-prvalue-cv','using CI=const int;int cpp_test_entry(){decltype(CI(1)) n=41;n=n+1;return n;}'),
    ('nullptr-prvalue-cv',PREFIX+'using CN=const N;int cpp_test_entry(){decltype(CN()) n;n=nullptr;if(n==nullptr)return 42;return 0;}'),
    ('uninitialized-copy-argument-return',PREFIX+'N id(N n){return n;}int cpp_test_entry(){N n;N m=n;int*p=id(m);if(p==nullptr)return 42;return 0;}'),
    ('uninitialized-comparison-not',PREFIX+'int cpp_test_entry(){N n;N m;if(n==m&&!n)return 42;return 0;}'),
    ('member-base-once',PREFIX+'int count;struct S{N n;};S*pick(S*p){count=count+1;return p;}int cpp_test_entry(){S s;int*p=pick(&s)->n;if(p==nullptr&&count==1)return 42;return 0;}'),
    ('dereference-once',PREFIX+'int count;N*pick(N*p){count=count+1;return p;}int cpp_test_entry(){N n;int*p=*pick(&n);if(p==nullptr&&count==1)return 42;return 0;}'),
    ('subscript-left-before-right',PREFIX+'int trace;N*base(N*p){trace=trace*10+1;return p;}int index(){trace=trace*10+2;return 0;}int cpp_test_entry(){N n[1];int*p=base(n)[index()];if(p==nullptr&&trace==12)return 42;return 0;}'),
    ('discarded-member-once',PREFIX+'int count;struct S{N n;};S*pick(S*p){count=count+1;return p;}int cpp_test_entry(){S s;pick(&s)->n;if(count==1)return 42;return 0;}'),
    ('assignment-effects',PREFIX+'int count;N source(){count=count+1;return nullptr;}int cpp_test_entry(){N n;n=source();if(!n&&count==1)return 42;return 0;}'),
)


@pytest.mark.parametrize('name,source',NO_FETCH_PROGRAMS,ids=[p[0] for p in NO_FETCH_PROGRAMS])
def test_nullptr_no_fetch_originals_are_stable(name,source,owned_only):
    units=((name+'.cpp',source),)
    assert compile_host_cpp(units)==compile_host_cpp(units)


@pytest.mark.integration
@pytest.mark.parametrize('name,source',NO_FETCH_PROGRAMS,ids=[p[0] for p in NO_FETCH_PROGRAMS])
def test_nullptr_no_fetch_originals_match_explicit_cpp17_reference(name,source,tmp_path,monkeypatch):
    execute_and_record(tmp_path,monkeypatch,((name+'.cpp',source),),42)


def test_nullptr_lvalue_conversion_does_not_fetch_source_storage_in_c_ast():
    # Native zero-filled stacks alone cannot prove the no-fetch requirement.
    parser=CppParser()
    tree=parser.parse(PREFIX+'void* f(){N n;return n;}')
    ids=[]
    class Reads(c_ast.NodeVisitor):
        def visit_ID(self,node):
            ids.append(node.name)
    Reads().visit(tree)
    assert 'n' not in ids
    function=next(node for node in tree.ext if isinstance(node,c_ast.FuncDef))
    assert any(isinstance(node,c_ast.Decl) and node.name=='n' and node.init is None
               for node in function.body.block_items)


def test_nullptr_member_rvalue_omits_terminal_member_fetch_in_c_ast():
    parser=CppParser()
    tree=parser.parse(PREFIX+'struct S{N n;};S*pick(S*);void*f(S*p){return pick(p)->n;}')
    refs=[]
    calls=[]
    class Reads(c_ast.NodeVisitor):
        def visit_StructRef(self,node):
            refs.append(node)
        def visit_FuncCall(self,node):
            calls.append(node)
            self.generic_visit(node)
    Reads().visit(tree)
    assert refs==[] and len(calls)==1


def test_const_enum_functional_prvalue_loses_cv_but_entity_does_not():
    parser=CppParser()
    parser.parse('enum E{answer=42};using CE=const E;'
                 'using T=decltype(CE());using U=decltype(CE(answer));'
                 'const E object=answer;using V=decltype(object);')
    enum=parser.scope.names['E'].dtype
    assert parser.scope.names['T'].dtype is enum
    assert parser.scope.names['U'].dtype is enum
    assert parser.scope.names['V'].dtype==ConstType(enum)


def test_nullptr_unevaluated_no_object_member_keeps_type_checks_without_runtime_plan():
    parser=CppParser()
    tree=parser.parse(PREFIX+'struct S{N n;};using T=decltype(S::n==nullptr);'
                      'int f(){return sizeof(S::n==nullptr);}')
    assert parser.scope.names['T'].dtype=='bool'
    ids=[]
    class Reads(c_ast.NodeVisitor):
        def visit_ID(self,node):
            ids.append(node.name)
    Reads().visit(tree)
    assert 'n' not in ids


def test_nullptr_volatile_is_not_discarded_to_admit_unsupported_access():
    with pytest.raises(CppDiagnostic) as error:
        CppParser().parse(PREFIX+'int f(){volatile N n;int*p=n;return 0;}')
    assert error.value.code=='cpp.unsupported.volatile'


def test_pure_nullptr_keeps_static_pointer_initializer_constant_shape():
    parser=CppParser()
    tree=parser.parse('int*q=nullptr;')
    declaration=next(node for node in tree.ext if isinstance(node,c_ast.Decl) and node.name=='q')
    node=declaration.init
    while isinstance(node,c_ast.Cast):
        node=node.expr
    assert isinstance(node,c_ast.Constant) and node.value=='0'
    units=(('static-null.cpp','int*q=nullptr;int cpp_test_entry(){if(q==nullptr)return 42;return 0;}'),)
    assert compile_host_cpp(units)==compile_host_cpp(units)


@pytest.mark.integration
def test_exact_namespace_nullptr_initializer_executes_against_cpp17_reference(tmp_path,monkeypatch):
    units=(('static-null.cpp','int*q=nullptr;int cpp_test_entry(){if(q==nullptr)return 42;return 0;}'),)
    execute_and_record(tmp_path,monkeypatch,units,42)
