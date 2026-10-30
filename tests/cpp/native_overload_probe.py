"""Compile this file with pcc1 and execute it; CPython is not native evidence.

Exercises candidate selection and explicit conversion ASTs in the compiled
frontend. It does not compile/run the parsed C++ program or qualify fixed point.
"""

from pcc.frontends.cpp import CppDiagnostic, CppParser
from pcc.frontends.cpp.types import ArithmeticTypes


def main():
    parser = CppParser("x86_64-unknown-linux-gnu")
    ast = parser.parse(
        "int f(int x){return 11;} int f(long x){return 22;} "
        "int f(double x){return 33;} int f(long long x){return 44;} "
        "int run(){short x=2;return f(x)+f(1.5f)+f(1ll);}", "native-overload.cpp"
    )
    expression = ast.ext[4].body.block_items[1].expr
    promoted = expression.left.left
    floating = expression.left.right
    exact = expression.right
    assert promoted.name.name == "_Z1fi"
    assert promoted.args.exprs[0].to_type.type.type.names == ["int"]
    assert floating.name.name == "_Z1fd"
    assert floating.args.exprs[0].to_type.type.type.names == ["double"]
    assert exact.name.name == "_Z1fx"
    assert exact.args.exprs[0].value == "1ll"
    assert len(parser.scope.lookup("f").overloads) == 4
    assert ArithmeticTypes("x86_64-unknown-linux-gnu").common("unsigned int", "long") == "long"
    assert ArithmeticTypes("x86_64-pc-windows-msvc").common("unsigned int", "long") == "unsigned long"
    ambiguous = False
    try:
        parser.parse("int f(short,int,int);int f(int,long,long);"
                     "int run(){short x=1;return f(x,2l,3l);}", "ambiguous.cpp")
    except CppDiagnostic as error:
        ambiguous = error.code == "cpp.ambiguous-overload"
    assert ambiguous
    print("cpp-overload-ok")
    print(promoted.name.name)
    print(floating.name.name)
    print(exact.name.name)
    print("ambiguous-rejected")


if __name__ == "__main__":
    main()
