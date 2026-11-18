"""Declaration projection only; these tests do not claim a callable ABI."""
from pcc.frontends.cpp import CppParser
from pcc.frontends.c.ast import c_ast
from pcc.frontends.cpp.types import unqualified

SOURCE = 'struct R{int n; R(const R& other); R result();}; R make(); R make();'


def test_class_result_declarations_keep_source_identity_and_no_pending_ast():
    parser=CppParser('x86_64-unknown-linux-gnu')
    ast=parser.parse(SOURCE,'declarations.cpp')
    record=parser.scope.lookup('R').dtype
    method=record.definition.scope.lookup('result').overloads[0]
    free=parser.scope.lookup('make').overloads[0]
    assert len(parser.scope.lookup('make').overloads)==1
    assert method.dtype is record and free.dtype is record
    assert method.parameters==() and free.parameters==()
    assert method.class_result_abi.result_type is record
    assert free.class_result_abi.result_type is record
    assert not method.defined and not free.defined
    for symbol,count in ((method,2),(free,1)):
        declarations=[n for n in ast.ext if isinstance(n,c_ast.Decl) and n.name==symbol.c_name]
        assert declarations
        for declaration in declarations:
            assert isinstance(declaration.type.type,c_ast.PtrDecl)
            assert len(declaration.type.args.params)==count
            assert declaration.type.args.params[0].name==symbol.class_result_hidden_name
    assert all(isinstance(n,(c_ast.Decl,c_ast.FuncDef,c_ast.Typedef)) for n in ast.ext)
