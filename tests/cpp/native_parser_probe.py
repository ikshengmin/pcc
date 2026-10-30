"""Compiled and executed probe; do not run under Python as native evidence."""

from pcc.frontends.cpp.parser import CppParser


def main():
    parser = CppParser()
    unit = parser.parse(
        "using T=int; int add(T x,T y){return x+y;} "
        "int main(){T(a);a=3;{int T=9;T=T+1;}return add(a,4);}",
        "native-scope.cpp",
    )
    assert unit.ext[0].decl.name == "_Z3addii"
    body = unit.ext[1].body.block_items
    assert body[0].name == "a"
    assert body[2].block_items[0].name == "T"
    assert body[2].block_items[1].op == "="
    assert body[3].expr.name.name == "_Z3addii"
    print("cpp-scope-ok")
    print(unit.ext[0].decl.name)


main()
