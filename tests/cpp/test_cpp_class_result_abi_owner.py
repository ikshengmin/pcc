"""Physical ABI owner tests, not class-expression admission or native evidence."""
import pytest
from pcc.frontends.cpp import CppParser, CppDiagnostic
from pcc.frontends.cpp.class_result_abi import classify_result, IndirectResult
from pcc.frontends.cpp.sema import Symbol
from pcc.frontends.c.ast import c_ast


def parsed(source, target='x86_64-unknown-linux-gnu'):
    parser=CppParser(target_triple=target)
    parser.parse(source)
    return parser, parser.scope.lookup('H').dtype


def test_user_copy_is_indirect_but_small_trivial_is_not():
    parser,dtype=parsed('struct H{int*p;H(const H&);};')
    assert classify_result(parser,dtype,None)=='indirect'
    parser,dtype=parsed('struct H{int*p;};')
    assert classify_result(parser,dtype,None)=='c-aggregate'


def test_projection_preserves_source_and_places_result_before_this():
    parser,dtype=parsed('struct H{int*p;H(const H&);};')
    symbol=Symbol('copy','function',None,dtype=dtype,parameters=('int',),c_name='_ZNK1H4copyEi')
    plan=IndirectResult(parser,symbol,None)
    this=parser._decl('object',plan.pointer_type,None)
    param=parser._decl('n','int',None)
    decl=plan.declaration(parser,[this,param],'result',None)
    assert [p.name for p in decl.type.args.params]==['result','object','n']
    assert symbol.dtype is dtype and symbol.parameters==('int',)
    assert decl.name==symbol.c_name
    destination=c_ast.FuncCall(c_ast.ID('destination',None),None,None)
    call=plan.call(destination,[c_ast.ID('object'),c_ast.ID('n')],None)
    assert call.args.exprs[0] is destination
    assert len(call.args.exprs)==3
    assert plan.return_after_construction('result',None).expr.name=='result'


@pytest.mark.parametrize('source',[
    'struct H{int*p;H(int*);};',
    'struct H{int*p;H(const H&);~H();};',
    'struct Inner{int n;Inner();};struct H{Inner value;H(const H&);};',
])
def test_unknown_shapes_rejected(source):
    parser,dtype=parsed(source)
    with pytest.raises(CppDiagnostic,match='class-value-abi'):
        classify_result(parser,dtype,None)


def test_external_result_plan_needs_no_fabricated_closed_body():
    from pcc.frontends.cpp.class_result_calls import CallResultPlan
    from pcc.frontends.cpp.parser import Expression
    from pcc.frontends.cpp.types import PointerType
    parser,dtype=parsed('struct H{int*p;H(const H&);};')
    symbol=Symbol('make','function',None,dtype=dtype,parameters=(),c_name='_Z4makev')
    plan=CallResultPlan(parser,symbol,[],None)
    assert not hasattr(symbol,'closed_construction_body')
    plan.require_closed(parser,None)
    destination=Expression(c_ast.ID('result'),PointerType(dtype))
    call=plan.emit_into(parser,destination,None)
    assert call.name.name=='_Z4makev' and call.args.exprs[0] is destination.node
    parser.lifetimes.frames=[[(object(),object(),None)]]
    with pytest.raises(CppDiagnostic):
        plan.require_closed(parser,None)
    symbol.nonthrowing=True
    with pytest.raises(CppDiagnostic, match='live cleanup'):
        plan.require_closed(parser,None)


def test_nonthrowing_result_callee_does_not_hide_throwing_argument():
    from pcc.frontends.cpp.class_result_calls import CallResultPlan
    parser,dtype=parsed('struct H{int*p;H(const H&);};')
    symbol=Symbol('make','function',None,dtype=dtype,parameters=('int',),c_name='_Z4makei')
    symbol.nonthrowing=True
    argument=c_ast.FuncCall(c_ast.ID('unknown'),None)
    plan=CallResultPlan(parser,symbol,[argument],None)
    parser.lifetimes.frames=[[(object(),object(),None)]]
    with pytest.raises(CppDiagnostic):
        plan.require_closed(parser,None)


def test_implicit_member_constructor_still_has_its_own_gate():
    with pytest.raises(CppDiagnostic, match='constructor-subobjects'):
        parsed('struct Inner{int n;};struct H{Inner value;H(const H&);};')


def test_call_preserves_qualified_source_type_separately_from_physical_storage():
    from pcc.frontends.cpp.class_result_calls import selected_call
    from pcc.frontends.cpp.types import ConstType
    parser,dtype=parsed('struct H{int*p;H(const H&);};')
    symbol=Symbol('make','function',None,dtype=ConstType(dtype),parameters=(),c_name='_Z4makev')
    expression=selected_call(parser,symbol,[],None)
    assert expression.dtype==ConstType(dtype)
    assert expression.node.abi.result_type is dtype
