"""Changed-shape native parser probe; not compilation of the parsed C++ TU."""

from pcc.frontends.cpp import CppDiagnostic, CppParser


def main():
    parser = CppParser("x86_64-unknown-linux-gnu")
    ast = parser.parse(
        "using V=void; void empty(V){return;} void emit(int); "
        "template<class T> void forward(T x){return emit(x);} "
        "int tag(bool x){return 11;} int tag(int x){return 22;} "
        "template<class T> int count(T n){T x=0;while(x<n){x=x+T(1);"
        "if(x==T(2))continue;if(x==T(5))break;}return tag(x<T(8));} "
        "int run(){empty();forward(7);int x=0;false&&(x=9);return count(8);}",
        "native-control.cpp",
    )
    assert parser.scope.lookup("empty").overloads[0].parameters == ()
    count = ast.ext[-1]
    forward = ast.ext[-2]
    assert forward.decl.name == "_Z7forwardIiEvT_"
    assert forward.body.block_items[0].expr.name.name == "_Z4emiti"
    assert count.decl.name == "_Z5countIiEiT_"
    assert count.body.block_items[1].__class__.__name__ == "While"
    statements = count.body.block_items[1].stmt.block_items
    assert statements[1].iftrue.__class__.__name__ == "Continue"
    assert statements[2].iftrue.__class__.__name__ == "Break"
    call = count.body.block_items[2].expr
    assert call.name.name == "_Z3tagb"
    assert call.args.exprs[0].to_type.type.type.names == ["_Bool"]
    rejected = False
    try:
        parser.parse("int run(){if(1)int x=2;return x;}", "scope.cpp")
    except CppDiagnostic as error:
        rejected = error.code == "cpp.undeclared"
    assert rejected
    rejected = False
    try:
        parser.parse("void f(){} int run(){while(f()){}return 0;}", "condition.cpp")
    except CppDiagnostic as error:
        rejected = error.code == "cpp.void-value"
    assert rejected
    rejected = False
    try:
        parser.parse("void f(int,void);", "parameter.cpp")
    except CppDiagnostic as error:
        rejected = error.code == "cpp.invalid-parameter"
    assert rejected
    rejected = False
    try:
        parser.parse("template<class T> void f(T x){return x;} "
                     "void f(long x){} int run(){f(7);return 0;}", "return.cpp")
    except CppDiagnostic as error:
        rejected = error.code == "cpp.return-value-in-void" and "while instantiating" in error.message
    assert rejected
    print("cpp-control-ok")
    print(count.decl.name)
    print(call.name.name)
    print("void-forward-and-loop-scopes-preserved")


if __name__ == "__main__":
    main()
