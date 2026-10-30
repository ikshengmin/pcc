"""Native-compiler parser probe; separate from executing compiled C++ programs."""

from pcc.frontends.cpp import CppDiagnostic, CppParser


def main():
    parser = CppParser("x86_64-unknown-linux-gnu")
    ast = parser.parse(
        "using V=void; void emit(int); void empty(V){return;} "
        "template<class T> void forward(T x){return emit(x);} "
        "int run(){empty();forward(7);int n=2;void(n=n+3);return n;}",
        "native-void.cpp",
    )
    assert parser.scope.lookup("empty").overloads[0].parameters == ()
    instance = ast.ext[-1]
    assert instance.decl.name == "_Z7forwardIiEvT_"
    assert instance.decl.type.type.type.names == ["void"]
    returned = instance.body.block_items[0]
    assert returned.expr.name.name == "_Z4emiti"
    assert returned.expr.args.exprs[0].name == "x"
    rejected = False
    try:
        parser.parse("template<class T> void f(T x){return x;} "
                     "void f(long x){} int run(){f(7);return 0;}", "invalid-void.cpp")
    except CppDiagnostic as error:
        rejected = error.code == "cpp.return-value-in-void" and "while instantiating" in error.message
    assert rejected
    rejected = False
    try:
        parser.parse("void f(int,void);", "invalid-parameter.cpp")
    except CppDiagnostic as error:
        rejected = error.code == "cpp.invalid-parameter"
    assert rejected
    print("cpp-void-ok")
    print(instance.decl.name)
    print(returned.expr.name.name)
    print("void-parameter-and-return-errors-preserved")


if __name__ == "__main__":
    main()
