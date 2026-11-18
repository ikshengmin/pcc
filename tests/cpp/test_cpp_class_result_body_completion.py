"""Exact AST completion obligations, supplementary to original-source ABI tests."""
import pytest
from pcc.frontends.c.ast import c_ast as C
from pcc.frontends.cpp.class_result_body import require_constructed_returns
from pcc.frontends.cpp import CppDiagnostic


def test_registered_return_can_precede_unreachable_lexical_cleanup():
    result=C.Return(C.ID('destination'))
    body=C.Compound([C.Compound([C.FuncCall(C.ID('construct'),None),result]),
                     C.FuncCall(C.ID('lexical_cleanup'),None)])
    require_constructed_returns(body,[result],None)


def test_two_branch_constructions_close_body():
    a=C.Return(C.ID('destination')); b=C.Return(C.ID('destination'))
    require_constructed_returns(C.Compound([C.If(C.ID('flag'),C.Compound([a]),C.Compound([b]))]),[a,b],None)


def test_registered_return_nested_in_statement_expression_still_rejects():
    result=C.Return(C.ID('destination'))
    body=C.Compound([C.StmtExpr(C.Compound([result]))])
    with pytest.raises(CppDiagnostic) as error:
        require_constructed_returns(body,[result],None)
    assert error.value.code=='cpp.unsupported.class-result-control-flow'


def test_physical_return_without_construction_is_not_completion():
    with pytest.raises(CppDiagnostic) as error:
        require_constructed_returns(C.Compound([C.Return(C.ID('destination'))]),[],None)
    assert error.value.code=='cpp.unsupported.class-result-construction'


def test_partial_conditional_return_is_not_completion():
    result=C.Return(C.ID('destination'))
    with pytest.raises(CppDiagnostic) as error:
        require_constructed_returns(C.Compound([C.If(C.ID('flag'),result,None)]),[result],None)
    assert error.value.code=='cpp.unsupported.class-result-fallthrough'

def test_goto_or_label_has_no_structured_construction_proof():
    result=C.Return(C.ID('destination'))
    for jump in (C.Goto('label'), C.Label('label',result)):
        with pytest.raises(CppDiagnostic) as error:
            require_constructed_returns(C.Compound([jump,result]),[result],None)
        assert error.value.code=='cpp.unsupported.class-result-control-flow'
