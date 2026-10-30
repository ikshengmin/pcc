"""pcc1-compiled template/declarator Sema probe; never a C++ execution claim."""

from pcc.frontends.cpp import CppDiagnostic, CppParser
from pcc.frontends.cpp.declarators import DeclaratorParser, require_plain_name
from pcc.frontends.cpp.parser import TokenCursor


def main():
    parser = CppParser("x86_64-unknown-linux-gnu")
    ast = parser.parse(
        "typedef int ((Count)); int ((tag))(long ((x))){return 11;} "
        "template<class T> int ((choose))(T ((x))){return tag(x);} "
        "int tag(int x){return 22;} "
        "template<class T> T twice(T x){return x+x;} "
        "int ((run))(){Count n=3;return choose(n)+twice(n)+int(twice(2.5));}",
        "native-template.cpp",
    )
    choose = parser.scope.lookup("choose").overloads[0]
    twice = parser.scope.lookup("twice").overloads[0]
    assert len(choose.instances) == 1
    assert len(twice.instances) == 2
    chosen = ast.ext[-3].body.block_items[0].expr
    assert chosen.name.name == "_Z3tagl"
    assert chosen.args.exprs[0].to_type.type.type.names == ["long"]
    assert ast.ext[-2].decl.name == "_Z5twiceIiET_S0_"
    assert ast.ext[-1].decl.name == "_Z5twiceIdET_S0_"
    assert ast.ext[-2].body.block_items[0].expr.op == "+"
    assert ast.ext[-1].decl.type.args.params[0].type.type.names == ["double"]
    failed = False
    try:
        parser.parse("template<class T> int f(T x){return x%2;} int f(long x){return 22;} "
                     "int run(){return f(1.5);}", "native-invalid.cpp")
    except CppDiagnostic as error:
        failed = error.code == "cpp.invalid-operands" and "while instantiating" in error.message
    assert failed
    parser.cursor = TokenCursor(parser._tokenize("(*callback)()", "declarator.cpp"))
    declarator = DeclaratorParser(parser.cursor, parser._identifier).parse()
    assert declarator.chunks[0].kind == "pointer"
    assert declarator.chunks[1].kind == "function"
    rejected = False
    try:
        require_plain_name(declarator)
    except CppDiagnostic as error:
        rejected = error.code == "cpp.unsupported.pointers"
    assert rejected
    parser.cursor = TokenCursor(parser._tokenize("*const *volatile value", "declarator.cpp"))
    declarator = DeclaratorParser(parser.cursor, parser._identifier).parse()
    assert declarator.chunks[0].qualifiers == ("volatile",)
    assert declarator.chunks[1].qualifiers == ("const",)
    print("cpp-template-ok")
    print(chosen.name.name)
    print(ast.ext[-2].decl.name)
    print(ast.ext[-1].decl.name)
    print("body-error-no-fallback")
    print("declarator-shape-ok")


if __name__ == "__main__":
    main()
