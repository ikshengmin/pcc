"""N4659 [expr.post.incr]/[expr.pre.incr]: typed update and old-value result.

The first original actually returned 7 under PCC versus 42 under G++ before
this fix. Preserve its bytes; do not replace it with a locally declared pointer.
Other originals cover row width, decrement, once-only lvalue evaluation and
scalar postfix behavior through the same existing C AST owner.
"""
import pytest
from pcc.frontends.c.ast import c_ast
from pcc.frontends.cpp import CppParser
from tests.cpp.owned_execution import compile_host_cpp
from tests.cpp.test_cpp_class_execution import execute_and_record
from tests.cpp.test_cpp_owned_execution import owned_only

ORIGINAL='int read(int(*p)[3]){p++;return (*p)[0];}int cpp_test_entry(){int a[2][3]={{0,0,7},{42,0,0}};return read(a);}'
CASES=(
 ('reported-formal-row-stride',ORIGINAL),
 ('old-pointer-value','int read(int(*p)[3]){int(*old)[3]=p++;return (*old)[0]+(*p)[0];}int cpp_test_entry(){int a[2][3]={{17,0,0},{25,0,0}};return read(a);}'),
 ('decrement-old-pointer','int read(int(*p)[3]){int(*old)[3]=p--;return (*old)[0]+(*p)[0];}int cpp_test_entry(){int a[2][3]={{17,0,0},{25,0,0}};return read(&a[1]);}'),
 ('one-int-row','int read(int(*p)[1]){p++;return (*p)[0];}int cpp_test_entry(){int a[2][1]={{7},{42}};return read(a);}'),
 ('five-long-long-row','int read(long long(*p)[5]){p++;return int((*p)[0]);}int cpp_test_entry(){long long a[2][5]={{0,7},{42}};return read(a);}'),
 ('three-char-row','int read(char(*p)[3]){p++;return int((*p)[0]);}int cpp_test_entry(){char a[2][3]={{7,0,0},{42,0,0}};return read(a);}'),
 ('const-element-row','int read(const int(*p)[3]){p++;return (*p)[0];}int cpp_test_entry(){const int a[2][3]={{0,0,7},{42,0,0}};return read(a);}'),
 ('effectful-increment-address','typedef int Row[3];int count;Row**pick(Row**p){count=count+1;return p;}int read(Row*p){Row*old=(*pick(&p))++;if(count!=1)return 0;return (*old)[0]+(*p)[0];}int cpp_test_entry(){Row a[2]={{17,0,0},{25,0,0}};return read(a);}'),
 ('effectful-decrement-address','typedef int Row[3];int count;Row**pick(Row**p){count=count+1;return p;}int read(Row*p){Row*old=(*pick(&p))--;if(count!=1)return 0;return (*old)[0]+(*p)[0];}int cpp_test_entry(){Row a[2]={{17,0,0},{25,0,0}};return read(&a[1]);}'),
 ('nested-row-pointer','int read(int(*p)[2][3]){p++;return (*p)[0][0];}int cpp_test_entry(){int a[2][2][3]={{{0,0,7},{0,0,0}},{{42,0,0},{0,0,0}}};return read(a);}'),
 ('scalar-old-value','int cpp_test_entry(){int n=20;int old=n++;return old+n+1;}'),
 ('unsigned-char-wrap','int cpp_test_entry(){unsigned char n=255;int old=n++;if(old==255&&n==0)return 42;return 0;}'),
 ('floating-decrement','int cpp_test_entry(){double n=21.5;double old=n--;return int(old+n);}'),
 ('scalar-side-effect-address','int count;int*pick(int*p){count=count+1;return p;}int cpp_test_entry(){int n=20;int old=(*pick(&n))++;if(count!=1)return 0;return old+n+1;}'),
)

@pytest.mark.parametrize('name,source',CASES,ids=[r[0] for r in CASES])
def test_postfix_original_artifacts_are_byte_stable(name,source,owned_only):
    units=((name+'.cpp',source),)
    assert compile_host_cpp(units)==compile_host_cpp(units)

@pytest.mark.integration
@pytest.mark.parametrize('name,source',CASES,ids=[r[0] for r in CASES])
def test_postfix_originals_match_same_cpp17_reference(name,source,tmp_path,monkeypatch):
    execute_and_record(tmp_path,monkeypatch,((name+'.cpp',source),),42)


def test_postfix_array_formal_update_uses_typed_once_only_capture():
    tree=CppParser().parse(ORIGINAL)
    function=next(n for n in tree.ext if isinstance(n,c_ast.FuncDef))
    statement=function.body.block_items[0]
    assert isinstance(statement,c_ast.StmtExpr)
    capture,old=statement.stmt.block_items
    assert isinstance(capture,c_ast.Decl)
    assert isinstance(capture.type,c_ast.PtrDecl)
    assert isinstance(capture.type.type,c_ast.PtrDecl)
    row=capture.type.type.type
    assert isinstance(row,c_ast.ArrayDecl) and row.dim.value=='3'
    assert isinstance(capture.init,c_ast.UnaryOp) and capture.init.op=='&'
    assert isinstance(old,c_ast.UnaryOp) and old.op=='p++'
    assert isinstance(old.expr,c_ast.UnaryOp) and old.expr.op=='*'
    assert old.expr.expr.name==capture.name
