"""N4659 [cstdarg.syn], [expr.call], [dcl.init]; owned SysV varargs.

Original programs use real <cstdarg> on both routes, with each compiler's
respective owned/system header. No host compiler participates in PCC building.
The scope is scalar retrieval and trivial record-array storage, not other
va_list ABIs, general class arrays or exception unwinding. Ordinary member
variadics have separate coverage in test_cpp_variadic_members.
"""
import os
import shutil
import struct
import subprocess

import pytest

from pcc.frontends.c.ast import c_ast
from pcc.frontends.cpp import CppDiagnostic, CppParser
from pcc.frontends.cpp.driver import preprocess_cpp
from pcc.frontends.cpp.types import PointerType, mangle_function
from tests.cpp.owned_execution import (
    Execution, compile_host_cpp, compile_reference_cpp, write_host_executable,
)
from tests.cpp.test_cpp_class_execution import record_execution
from tests.cpp.test_cpp_owned_execution import owned_only

HEADER='#include <cstdarg>\n'
PROGRAMS=(
    ('adjusted-array-final-parameter',HEADER+'int take(int last[2],...){va_list ap;va_start(ap,last);int n=va_arg(ap,int);va_end(ap);return n;}int cpp_test_entry(){int values[2];return take(values,42);}'),
    ('integer-gp-overflow',HEADER+'int sum(int count,...){va_list ap;va_start(ap,count);int total=0;int i=0;while(i<count){total=total+va_arg(ap,int);i=i+1;}va_end(ap);return total;}int cpp_test_entry(){return sum(8,5,5,5,5,5,5,6,6);}'),
    ('floating-register-and-stack',HEADER+'int sum(int count,...){va_list ap;va_start(ap,count);double total=0.0;int i=0;while(i<count){total=total+va_arg(ap,double);i=i+1;}va_end(ap);return int(total)-3;}int cpp_test_entry(){return sum(9,float(1.0),2.0,3.0,4.0,5.0,6.0,7.0,8.0,9.0);}'),
    ('scalar-promotions',HEADER+'int take(int tag,...){va_list ap;va_start(ap,tag);int a=va_arg(ap,int);int b=va_arg(ap,int);double d=va_arg(ap,double);va_end(ap);return a+b+int(d);}int cpp_test_entry(){return take(0,true,char(17),float(24.0));}'),
    ('pointer-and-unsigned',HEADER+'int take(int tag,...){va_list ap;va_start(ap,tag);int*p=va_arg(ap,int*);bool high=va_arg(ap,unsigned int)>2147483647U;va_end(ap);if(high)return *p;return 0;}int cpp_test_entry(){int n=42;return take(0,&n,4294967295U);}'),
    ('parameter-copy-independent',HEADER+'int peek(va_list input){va_list copy;va_copy(copy,input);int n=va_arg(copy,int);va_end(copy);return n;}int take(int tag,...){va_list ap;va_start(ap,tag);int a=peek(ap);int b=va_arg(ap,int);va_end(ap);return a+b;}int cpp_test_entry(){return take(0,21);}'),
    ('effectful-copy-operands',HEADER+'int calls;va_list*choose(va_list*p){calls=calls+1;return p;}int take(int tag,...){va_list ap;va_list copy;va_start(ap,tag);va_copy(*choose(&copy),*choose(&ap));int a=va_arg(copy,int);int b=va_arg(ap,int);va_end(copy);va_end(ap);if(calls==2)return a+b;return 0;}int cpp_test_entry(){return take(0,21);}'),
    ('effectful-argument-cursor',HEADER+'int calls;va_list*choose(va_list*p){calls=calls+1;return p;}int take(int tag,...){va_list ap;va_start(ap,tag);int a=va_arg(*choose(&ap),int);va_end(ap);if(calls==1)return a;return 0;}int cpp_test_entry(){return take(0,42);}'),
    ('restart-after-end',HEADER+'int take(int tag,...){va_list ap;va_start(ap,tag);int a=va_arg(ap,int);va_end(ap);va_start(ap,tag);int b=va_arg(ap,int);va_end(ap);return a+b;}int cpp_test_entry(){return take(0,21);}'),
    ('c-linkage-definition',HEADER+'extern "C" int take(int tag,...){va_list ap;va_start(ap,tag);int n=va_arg(ap,int);va_end(ap);return n;}int cpp_test_entry(){return take(0,42);}'),
    ('standard-namespace-list',HEADER+'int take(int tag,...){std::va_list ap;va_start(ap,tag);int n=va_arg(ap,int);va_end(ap);return n;}int cpp_test_entry(){return take(0,42);}'),
    ('generic-effectful-array-decay','typedef int A[2];int calls;A*choose(A*p){calls=calls+1;return p;}int cpp_test_entry(){A a={17,25};int*p=*choose(&a);if(calls!=1)return 0;return p[0]+p[1];}'),
    ('trivial-record-array','struct S{int n;};int cpp_test_entry(){S a[2];a[0].n=17;a[1].n=25;return a[0].n+a[1].n;}'),
    ('record-one-past-address','struct S{int n;};int cpp_test_entry(){S a[2];S*p=&a[2];if(p==&a[2])return 42;return 0;}'),
)


def owned_units(units):
    return tuple((name,preprocess_cpp(source)) for name,source in units)


@pytest.mark.parametrize('name,source',PROGRAMS,ids=[p[0] for p in PROGRAMS])
def test_cstdarg_originals_have_byte_stable_owned_artifacts(name,source,owned_only):
    units=owned_units(((name+'.cpp',source),))
    assert compile_host_cpp(units)==compile_host_cpp(units)


@pytest.mark.integration
@pytest.mark.parametrize('name,source',PROGRAMS,ids=[p[0] for p in PROGRAMS])
def test_cstdarg_originals_execute_against_explicit_cpp17_reference(name,source,tmp_path,monkeypatch):
    units=((name+'.cpp',source),)
    with monkeypatch.context() as guard:
        def forbidden(*args,**kwargs):
            raise AssertionError('owned C++ varargs compilation invoked external process')
        guard.setattr(subprocess,'Popen',forbidden)
        first=compile_host_cpp(owned_units(units))
        assert first==compile_host_cpp(owned_units(units))
        write_host_executable(first,tmp_path/'owned')
    compiler=shutil.which(os.environ.get('PCC_CPP_ORACLE','g++'))
    assert compiler
    reference=compile_reference_cpp(units,tmp_path/'reference',compiler=compiler)
    expected=Execution(42,struct.pack('<i',42),b'')
    record_execution(tmp_path/'owned',expected)
    record_execution(reference,expected)


def test_header_va_list_alias_and_builtin_ast_keep_the_real_record_pointer():
    parser=CppParser()
    tree=parser.parse(preprocess_cpp(HEADER+'int take(int tag,...){va_list ap;va_list copy;va_start(ap,tag);va_copy(copy,ap);int n=va_arg(copy,int);va_end(copy);va_end(ap);return n;}'))
    owner=parser.scope.names['__pcc_sysv_va_list'].dtype
    alias=parser.scope.names['va_list'].dtype
    assert alias.bound==1 and alias.element is owner
    assert parser.scope.names['std'].namespace_scope.names['va_list'].dtype==alias
    assert [(m.name,m.dtype) for m in owner.definition.members]==[
        ('gp_offset','unsigned int'),('fp_offset','unsigned int'),
        ('overflow_arg_area',PointerType('void')),('reg_save_area',PointerType('void'))]
    calls=[]
    class Calls(c_ast.NodeVisitor):
        def visit_FuncCall(self,node):
            calls.append(node)
            self.generic_visit(node)
    Calls().visit(tree)
    start=next(call for call in calls if call.name.name=='__builtin_va_start')
    copy=next(call for call in calls if call.name.name=='__builtin_va_copy')
    assert len(start.args.exprs)==1 and len(copy.args.exprs)==2
    for node in start.args.exprs+copy.args.exprs:
        assert isinstance(node,c_ast.Cast) and isinstance(node.to_type.type,c_ast.PtrDecl)
        assert node.to_type.type.type.type.name==owner.c_tag
    function=next(node for node in tree.ext if isinstance(node,c_ast.FuncDef))
    assert function.decl.name=='_Z4takeiz'
    assert isinstance(function.decl.type.args.params[-1],c_ast.EllipsisParam)
    assert mangle_function('take',('int',),variadic=True)=='_Z4takeiz'


def test_va_arg_projection_is_typed_prvalue_not_an_assignable_dereference():
    source=HEADER+'int f(int n,...){va_list ap;va_start(ap,n);va_arg(ap,int)=7;va_end(ap);return 0;}'
    with pytest.raises(CppDiagnostic) as error:
        CppParser().parse(preprocess_cpp(source))
    assert error.value.code=='cpp.not-lvalue'


def test_record_one_past_address_has_no_array_element_fetch_projection():
    parser=CppParser()
    tree=parser.parse(dict(PROGRAMS)['record-one-past-address'])
    arrays=[]
    class Arrays(c_ast.NodeVisitor):
        def visit_ArrayRef(self,node):
            arrays.append(node)
    Arrays().visit(tree)
    assert arrays==[]


@pytest.mark.parametrize('body,code',(
    ('int f(int n){va_list ap;va_start(ap,n);return 0;}','cpp.invalid-va-start'),
    ('int f(int n,int last,...){va_list ap;va_start(ap,n);return 0;}','cpp.invalid-va-start'),
    ('int f(int n,...){va_list ap;{int n=1;va_start(ap,n);}return 0;}','cpp.invalid-va-start'),
    ('int f(int n,...){va_list ap;va_start(ap,n+0);return 0;}','cpp.invalid-va-start'),
    ('int f(float n,...){va_list ap;va_start(ap,n);return 0;}','cpp.invalid-va-start'),
    ('int f(char n,...){va_list ap;va_start(ap,n);return 0;}','cpp.invalid-va-start'),
    ('using N=decltype(nullptr);int f(N n,...){va_list ap;va_start(ap,n);return 0;}','cpp.invalid-va-start'),
    ('int f(int n,...){int ap;va_start(ap,n);return 0;}','cpp.invalid-va-list'),
    ('int f(int n,...){va_list ap;va_start(ap,n);return va_arg(ap,short);}','cpp.invalid-va-arg-type'),
    ('struct S{int n;};int f(int n,...){va_list ap;va_start(ap,n);va_arg(ap,S);return 0;}','cpp.unsupported.va-arg-type'),
    # Exact original is preserved in test_cpp_variadic_members.PROGRAMS.
    ('template<class T>int f(T n,...){return 0;}','cpp.unsupported.variadic-functions'),
))
def test_varargs_invalid_or_unimplemented_contracts_fail_before_emission(body,code):
    with pytest.raises(CppDiagnostic) as error:
        CppParser().parse(preprocess_cpp(HEADER+body))
    assert error.value.code==code


@pytest.mark.parametrize('source,code',(
    ('struct S{const int n;};int f(){S a[1];return 0;}','cpp.deleted-default-constructor'),
    ('struct S{S();};int f(){S a[1];return 0;}','cpp.unsupported.class-array-lifetime'),
    ('struct S{~S();};int f(){S a[1];return 0;}','cpp.unsupported.class-array-lifetime'),
    ('struct S{int n=1;};int f(){S a[1];return 0;}','cpp.unsupported.class-array-lifetime'),
    ('struct S{int n;};int f(){S a[1]{};return 0;}','cpp.unsupported.class-array-initialization'),
    ('struct S{int n;};S global[2];','cpp.unsupported.class-array-lifetime'),
))
def test_record_array_storage_does_not_claim_nontrivial_or_static_lifetimes(source,code):
    with pytest.raises(CppDiagnostic) as error:
        CppParser().parse(source)
    assert error.value.code==code


@pytest.mark.parametrize('target',('aarch64-unknown-linux-gnu','x86_64-pc-windows-msvc'))
def test_variadic_definition_execution_does_not_borrow_another_target_abi(target):
    with pytest.raises(CppDiagnostic) as error:
        CppParser(target_triple=target).parse('int f(int n,...){return n;}')
    assert error.value.code=='cpp.unsupported.varargs-target'


CROSS_TU=(
    ('provider.cpp',HEADER+'int take(int tag,...){va_list ap;va_start(ap,tag);int n=va_arg(ap,int);va_end(ap);return n;}'),
    ('caller.cpp','int take(int,...);int cpp_test_entry(){return take(0,42);}'),
)


def test_variadic_independent_translation_units_keep_stable_ellipsis_symbols(owned_only):
    first=compile_host_cpp(owned_units(CROSS_TU))
    assert first==compile_host_cpp(owned_units(CROSS_TU))
    assert '_Z4takeiz' in first.ir[0] and '_Z4takeiz' in first.ir[1]


@pytest.mark.integration
def test_variadic_independent_translation_units_match_real_cpp17_reference(tmp_path,monkeypatch):
    with monkeypatch.context() as guard:
        def forbidden(*args,**kwargs):
            raise AssertionError('owned variadic build invoked external compiler')
        guard.setattr(subprocess,'Popen',forbidden)
        artifact=compile_host_cpp(owned_units(CROSS_TU))
        write_host_executable(artifact,tmp_path/'owned')
    compiler=shutil.which(os.environ.get('PCC_CPP_ORACLE','g++'))
    assert compiler
    reference=compile_reference_cpp(CROSS_TU,tmp_path/'reference',compiler=compiler)
    expected=Execution(42,struct.pack('<i',42),b'')
    record_execution(tmp_path/'owned',expected)
    record_execution(reference,expected)


def test_cstdarg_and_cstdio_share_one_nominal_va_list_and_public_owned_route():
    from tests.cpp.test_cpp_public_driver import compile_cpp
    source='#include <cstdio>\n'+HEADER+'int take(int n,...){va_list ap;va_start(ap,n);int result=va_arg(ap,int);va_end(ap);return result;}int main(){return take(0,42);}'
    first=compile_cpp(source,'cstdarg.cpp')
    assert first==compile_cpp(source,'cstdarg.cpp')


def test_owned_adapter_and_public_driver_finalize_the_same_typed_varargs_owner(owned_only):
    from tests.cpp.test_cpp_public_driver import compile_cpp
    source=dict(PROGRAMS)['pointer-and-unsigned']
    adapter=compile_host_cpp(owned_units((('varargs.cpp',source),)))
    public=compile_cpp(source,'varargs.cpp')
    for text in (adapter.ir[0],public[0][1]):
        assert '__pcc_va_arg_' not in text
        assert 'va_arg ' in text
        assert 'llvm.va_start' in text and 'llvm.va_end' in text


def test_shared_varargs_finalizer_is_byte_identity_without_internal_markers():
    from pcc.frontends.c.codegen.c_codegen import postprocess_ir_text
    source='define i32 @ordinary() {\nentry:\n  ret i32 42\n}\n'
    assert postprocess_ir_text(source)==source


def test_effectful_array_decay_retains_address_without_fetching_cursor_bytes():
    # This is the original crash input, not a simple-ap replacement. Each
    # intrinsic cursor cast must directly use choose's once-evaluated result.
    source=dict(PROGRAMS)['effectful-argument-cursor']
    parser=CppParser()
    tree=parser.parse(preprocess_cpp(source))
    calls=[]
    def visit(node):
        if isinstance(node,c_ast.FuncCall) and isinstance(node.name,c_ast.ID):
            if node.name.name=='__builtin_va_arg':calls.append(node)
        for _,child in node.children():visit(child)
    visit(tree)
    assert len(calls)==1
    cursor=calls[0].args.exprs[0]
    assert isinstance(cursor,c_ast.Cast)
    assert isinstance(cursor.expr,c_ast.FuncCall)
    assert cursor.expr.name.name==parser.scope.names['choose'].overloads[0].c_name
    assert isinstance(cursor.to_type.type,c_ast.PtrDecl)
    assert isinstance(cursor.to_type.type.type.type,c_ast.Struct)
    assert cursor.to_type.type.type.type.name==parser.scope.names['__pcc_sysv_va_list'].dtype.c_tag
