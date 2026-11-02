"""N4659 [dcl.fct], [class.copy]: record declarations do not implement values.

Only nonempty public trivial scalar/pointer records reuse their existing C
layout for declarations. No copy, parameter passing or returned temporary is
claimed by these no-call original programs.
"""
import pytest
from pcc.frontends.cpp import CppDiagnostic, CppParser
from pcc.frontends.c.ast import c_ast
from pcc.frontends.cpp.types import ConstType, unqualified
from tests.cpp.owned_execution import compile_host_cpp
from tests.cpp.test_cpp_class_execution import execute_and_record
from tests.cpp.test_cpp_owned_execution import owned_only

ORIGINAL='struct R{int value;};extern "C" R f(R);'
UNITS=(('original.cpp',ORIGINAL),('entry.cpp','int cpp_test_entry(){return 42;}'))
CONST_ORIGINAL='struct R{const int value;};extern "C" const R f(const R);'
CONST_UNITS=(('original.cpp',CONST_ORIGINAL),('entry.cpp','int cpp_test_entry(){return 42;}'))


@pytest.mark.parametrize('source,units,const',[(ORIGINAL,UNITS,False),(CONST_ORIGINAL,CONST_UNITS,True)])
def test_original_record_declaration_retains_nominal_result_parameter_and_ir(source,units,const,owned_only):
    parser=CppParser()
    ast=parser.parse(source)
    record=parser.scope.names['R'].dtype
    symbol=parser.scope.names['f'].overloads[0]
    assert unqualified(symbol.dtype) is record and symbol.parameters==(record,)
    declaration=next(node for node in ast.ext if isinstance(node,c_ast.Decl) and node.name=='f')
    result=declaration.type.type
    parameter=declaration.type.args.params[0].type
    assert isinstance(result,c_ast.TypeDecl) and isinstance(result.type,c_ast.Struct)
    assert isinstance(parameter,c_ast.TypeDecl) and isinstance(parameter.type,c_ast.Struct)
    assert result.type.name==parameter.type.name==record.c_tag
    assert result.quals==parameter.quals==(['const'] if const else [])
    assert isinstance(record.definition.members[0].dtype,ConstType) is const
    assert symbol.c_name=='f' and not symbol.defined
    first=compile_host_cpp(units)
    assert record.c_tag in first.ir[0]
    assert first==compile_host_cpp(units)


@pytest.mark.integration
@pytest.mark.parametrize('units',[UNITS,CONST_UNITS])
def test_original_record_declaration_matches_real_cpp17_reference(units,tmp_path,monkeypatch):
    execute_and_record(tmp_path,monkeypatch,units,42)


@pytest.mark.parametrize('source',[
    'struct R{}; R make();',
    'struct R; R make();',
    'class R{int value;};R make();',
    'struct R{int value;R();};R make();',
    'struct R{int value;~R();};R make();',
    'struct R{int value=42;};R make();',
    'struct S{int value;};struct R{S value;};R make();',
    'struct R{int value;};R make(){R r{};return r;}',
    'struct R{int value;};R make();int main(){make();return 0;}',
    'struct R{int value;};R make();template<class T>int g(T x){make();return 0;}',
])
def test_declarations_do_not_bypass_class_value_or_record_shape_boundaries(source):
    with pytest.raises(CppDiagnostic) as error:
        CppParser().parse(source)
    assert error.value.code=='cpp.unsupported.class-value-abi'
