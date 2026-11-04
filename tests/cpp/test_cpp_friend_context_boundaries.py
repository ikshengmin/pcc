"""Friend-class access contexts, not additional friendship syntax.

N4659 [class.friend]/1-2, [dcl.fct.default]/5,9 and [basic.lookup.argdep]/2.
These sources exercise declaration-bound access through the existing default
argument, member initializer, overload and ADL owners. They require only the
saved existing-class friendship candidate. Hidden friend functions, templates,
inheritance and whole-project acceptance remain outside this test slice.
"""

import pytest

from pcc.frontends.c.ast import c_ast
from pcc.frontends.cpp import CppDiagnostic, CppParser
from pcc.frontends.cpp.sema import argument_dependent_lookup
from pcc.frontends.cpp.types import PointerType
from tests.cpp.owned_execution import compile_host_cpp
from tests.cpp.test_cpp_owned_execution import owned_only


DEFAULT_PROGRAM = (
    'class Reader;int calls=0;class Owner{friend class Reader;'
    'static int hidden(){calls+=1;return 41;}};'
    'class Reader{public:static int value(int n=Owner::hidden()){return n+1;}};'
    'int cpp_test_entry(){if(calls!=0)return 1;'
    'int first=Reader::value();if(calls!=1)return 2;'
    'int second=Reader::value();if(calls!=2)return 3;'
    'Reader::value(0);if(calls!=2)return 4;'
    'if(first!=second)return 5;return first;}'
)
ADL_PROGRAM = (
    'namespace helpers{class Reader;}namespace data{'
    'class Box{friend class helpers::Reader;int n;};'
    'int pick(Box*p){if(p==nullptr)return 1;return 42;}}'
    'namespace helpers{int pick(data::Box*p){return 7;}'
    'class Reader{public:static void set(data::Box*p){p->n=42;}};}'
    'int cpp_test_entry(){data::Box b;helpers::Reader::set(&b);return pick(&b);}'
)
CASES = (
    ('default-bound-in-friend-evaluated-at-call', DEFAULT_PROGRAM),
    ('member-initializer-retains-friend-context',
     'class Reader;class Owner{friend class Reader;enum{answer=42};};'
     'class Reader{int n=Owner::answer;public:int get()const{return n;}};'
     'int cpp_test_entry(){Reader r;return r.get();}'),
    ('overload-checks-selected-private-member',
     'class Reader;class Owner{friend class Reader;static int pick(int){return 42;}'
     'public:static int pick(long){return 7;}};'
     'class Reader{public:static int run(){return Owner::pick(0);}};'
     'int cpp_test_entry(){if(Owner::pick(0L)!=7)return 1;return Reader::run();}'),
    ('friend-namespace-does-not-expand-adl', ADL_PROGRAM),
    ('private-enumerator-in-friend-member-bound',
     'class Reader;class Owner{friend class Reader;enum{count=2};};'
     'class Reader{int values[Owner::count];public:static int run(){Reader r;'
     'r.values[0]=17;r.values[1]=25;return r.values[0]+r.values[1];}};'
     'int cpp_test_entry(){return Reader::run();}'),
)
PROGRAMS = tuple((name, ((name + '.cpp', source),)) for name, source in CASES)

INVALID = (
    ('friendship-does-not-inject-members',
     'class Reader;class Owner{friend class Reader;enum{hidden=42};};'
     'class Reader{public:static int run(){return hidden;}};', 'cpp.undeclared'),
    ('friendship-does-not-grant-namespace-access',
     'namespace helpers{class Reader;}class Owner{friend class helpers::Reader;int n;};'
     'namespace helpers{int read(Owner*p){return p->n;}}', 'cpp.member-access'),
    ('default-does-not-borrow-friend-caller-access',
     'class Reader;class Owner{friend class Reader;static int hidden(){return 42;}};'
     'int outside(int n=Owner::hidden()){return n;}'
     'class Reader{public:static int run(){return outside();}};', 'cpp.member-access'),
    ('completed-friend-binding-does-not-leak-access',
     'class Reader;class Owner{friend class Reader;static int hidden(){return 42;}};'
     'class Reader{public:static int value(int n=Owner::hidden()){return n;}};'
     'int outside(int n=Owner::hidden()){return n;}', 'cpp.member-access'),
    ('friend-only-namespace-is-not-an-adl-source',
     'namespace helpers{class Reader;}namespace data{class Box{friend class helpers::Reader;};}'
     'namespace helpers{int hidden(data::Box*){return 42;}class Reader{};}'
     'int cpp_test_entry(){data::Box b;return hidden(&b);}', 'cpp.undeclared'),
)


def walk(node):
    yield node
    for _, child in node.children():
        yield from walk(child)


def test_default_argument_keeps_declaring_friend_scope_and_callee_identity():
    parser = CppParser()
    parser.parse(DEFAULT_PROGRAM)
    reader = parser.scope.names['Reader'].dtype.definition
    owner = parser.scope.names['Owner'].dtype.definition
    function = reader.scope.names['value'].overloads[0]
    record, = function.default_arguments
    hidden = owner.scope.names['hidden'].overloads[0]
    assert record.scope.parent is reader.scope
    assert record.function is function
    calls = [node for node in walk(record.expression.node) if isinstance(node, c_ast.FuncCall)]
    assert len(calls) == 1
    assert isinstance(calls[0].name, c_ast.ID)
    assert calls[0].name.name == hidden.c_name
    assert parser.scope is parser.scope.global_scope()
    assert getattr(parser, 'default_argument_binding', None) is None
    assert parser.member_function is parser.this_symbol is None


def test_adl_public_owner_excludes_befriended_class_namespace():
    parser = CppParser()
    parser.parse(ADL_PROGRAM)
    data = parser.scope.names['data'].namespace_scope
    helpers = parser.scope.names['helpers'].namespace_scope
    box = data.names['Box'].dtype
    assert box.definition.friend_classes == [helpers.names['Reader'].dtype]
    result = argument_dependent_lookup('pick', [PointerType(box)], None, box.coord)
    assert result.overloads == data.names['pick'].overloads
    assert all(candidate not in result.overloads for candidate in helpers.names['pick'].overloads)


@pytest.mark.parametrize('name,units', PROGRAMS, ids=[row[0] for row in PROGRAMS])
def test_friend_context_original_owned_artifacts_are_stable(owned_only, name, units):
    assert compile_host_cpp(units) == compile_host_cpp(units)


@pytest.mark.parametrize('name,source,code', INVALID, ids=[row[0] for row in INVALID])
def test_invalid_friend_context_originals_preserve_diagnostics(name, source, code):
    with pytest.raises(CppDiagnostic) as error:
        CppParser().parse(source, filename=name + '.cpp')
    assert error.value.code == code
    assert error.value.coord.file == name + '.cpp'
