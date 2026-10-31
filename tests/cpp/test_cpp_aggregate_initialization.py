"""C++17 aggregate initialization: explicit braces and typed leaf conversion.

These original PCC cases qualify only the explicit-brace scalar/pointer slice.
Unmodeled constant narrowing, brace elision and class copies stay capability gates.
Execution uses the existing owned adapter plus an explicitly labeled G++ oracle.
"""

import pytest

from pcc.frontends.c.ast import c_ast
from pcc.frontends.cpp import CppDiagnostic, CppParser
from tests.cpp.owned_execution import compile_host_cpp
from tests.cpp.test_cpp_class_execution import execute_and_record


AGGREGATE_CASES = (('ordered-members-and-omitted-pointer',
  'struct S{int a;long b;int *p;};int cpp_test_entry(){S s{17,24};return '
  's.a+s.b+int(s.p==nullptr);}',
  42),
 ('copy-list-aggregate-initialization',
  'struct S{int a;int b;};int cpp_test_entry(){S s={17,25};return s.a+s.b;}',
  42),
 ('scalar-subobject-braces',
  'struct S{int a;int b;};int cpp_test_entry(){S s{{17},{25}};return s.a+s.b;}',
  42),
 ('empty-scalar-braces',
  'struct S{int a;int b;};int cpp_test_entry(){S s{{},{42}};return s.a+s.b;}',
  42),
 ('nested-explicit-braces-and-omissions',
  'struct I{int a;int b;};struct O{I i;int c;};int cpp_test_entry(){O s{{10},32};return '
  's.i.a+s.i.b+s.c;}',
  42),
 ('empty-aggregate-requires-an-explicit-empty-clause',
  'struct E{};struct O{E e;int x;};int cpp_test_entry(){O s{{},42};return s.x;}',
  42),
 ('nonaggregate-member-empty-list-value-initialization',
  'class P{int hidden;public:int exposed;};struct O{P p;int x;};int cpp_test_entry(){O '
  's{{},42};return s.p.exposed+s.x;}',
  42),
 ('const-member-bypasses-deleted-default-constructor-as-aggregate',
  'struct S{const int x;};int cpp_test_entry(){S s{42};return s.x;}',
  42),
 ('class-pointer-qualification-in-member-initializer',
  'struct I{int x;};struct O{const I *p;};int cpp_test_entry(){I i{42};O s{&i};return s.p->x;}',
  42),
 ('clause-effects-left-to-right-once',
  'int take(int *n,int v){*n=*n*10+v;return v;}struct S{int a;int b;};int cpp_test_entry(){int '
  'n=0;S s{take(&n,1),take(&n,2)};return n*100+s.a*10+s.b;}',
  1212),
 ('assignment-expressions-in-initializer-order',
  'struct S{int a;int b;};int cpp_test_entry(){int x=0;S s{x=1,x=x+1};return x*100+s.a*10+s.b;}',
  212),
 ('nested-clause-effects-once-in-source-order',
  'int take(int*n,int v){*n=*n*10+v;return v;}struct I{int a;int b;};struct O{I i;int c;};int '
  'cpp_test_entry(){int n=0;O s{{take(&n,1),take(&n,2)},take(&n,3)};return '
  'n*1000+s.i.a*100+s.i.b*10+s.c;}',
  123123),
 ('self-pointer-point-of-declaration',
  'struct S{int value;S*self;};int cpp_test_entry(){S s{42,&s};return s.self->value;}',
  42),
 ('same-type-char-and-bool',
  'struct S{char tag;bool flag;};int cpp_test_entry(){char tag=41;S s{tag,true};return '
  's.tag+int(s.flag);}',
  42),
 ('float-and-double-without-narrowing',
  'struct S{float a;double b;};int cpp_test_entry(){float a=17.5f;S s{a,24.5};return '
  'int(s.a+s.b);}',
  42),
 ('omitted-double-and-bool-value-initialize',
  'struct S{int n;double d;bool b;};int cpp_test_entry(){S s{42};return s.n+int(s.d)+int(s.b);}',
  42),
 ('const-object-and-copy-list-trailing-comma',
  'struct S{const int a;int b;};int cpp_test_entry(){const S s={17,25,};return s.a+s.b;}',
  42),
 ('namespace-alias-class-list-initialization',
  'namespace n{struct S{int a;int b;};}using Alias=n::S;int cpp_test_entry(){Alias s{17,25};return '
  's.a+s.b;}',
  42),
 ('later-clause-observes-earlier-initialized-member',
  'struct S{int a;int b;};int cpp_test_entry(){S s{17,s.a+25};return s.b;}',
  42),
 ('integer-constant-narrowing-exception',
  'struct S{unsigned char x;};int cpp_test_entry(){S s{255};return s.x;}',
  255),
 ('constant-expression-narrowing-exception',
  'struct S{unsigned char x;};int cpp_test_entry(){S s{40+2};return s.x;}',
  42),
 ('const-integral-name-narrowing-exception',
  'struct S{unsigned char x;};int cpp_test_entry(){const int value=42;S s{value};return s.x;}',
  42))


INVALID_CASES = (('too-many-elements', 'struct S{int x;};int f(){S s{1,2};return 0;}', 'cpp.too-many-initializers'),
 ('empty-class-excess-element',
  'struct S{};int f(){S s{{}};return 0;}',
  'cpp.too-many-initializers'),
 ('nested-deleted-default',
  'class P{const int x;};struct S{P p;};int f(){S s{{}};return 0;}',
  'cpp.deleted-default-constructor'),
 ('scalar-too-many-elements',
  'struct S{int x;};int f(){S s{{1,2}};return 0;}',
  'cpp.too-many-initializers'),
 ('scalar-nested-list-has-no-type',
  'struct S{int x;};int f(){S s{{{1}}};return 0;}',
  'cpp.invalid-scalar-initializer'),
 ('pointer-nested-empty-list',
  'struct S{int*p;};int f(){S s{{{}}};return 0;}',
  'cpp.invalid-scalar-initializer'),
 ('float-to-int-narrows',
  'struct S{int x;};int f(){S s{1.0};return 0;}',
  'cpp.narrowing-conversion'),
 ('pointer-to-bool-narrows',
  'struct S{bool x;};int f(){int n=0;S s{&n};return 0;}',
  'cpp.narrowing-conversion'),
 ('incompatible-pointer',
  'struct S{int*p;};int f(){long n=0;S s{&n};return 0;}',
  'cpp.invalid-conversion'),
 ('aggregate-member-copy-init-nullptr-bool',
  'struct S{bool b;};int f(){S s{nullptr};return 0;}',
  'cpp.invalid-conversion'),
 ('aggregate-member-copy-list-nullptr-bool',
  'struct S{bool b;};int f(){S s{{nullptr}};return 0;}',
  'cpp.invalid-conversion'),
 ('negative-constant-to-unsigned',
  'struct S{unsigned x;};int f(){S s{-1};return 0;}',
  'cpp.narrowing-conversion'),
 ('unsigned-constant-outside-int',
  'struct S{int x;};int f(){S s{2147483648u};return 0;}',
  'cpp.narrowing-conversion'),
 ('byte-constant-out-of-range',
  'struct S{unsigned char x;};int f(){S s{256};return 0;}',
  'cpp.narrowing-conversion'),
 ('nonconstant-integer-narrowing',
  'struct S{int x;};int f(){long n=42;S s{n};return 0;}',
  'cpp.narrowing-conversion'),
 ('integer-to-bool-out-of-range',
  'struct S{bool x;};int f(){S s{2};return 0;}',
  'cpp.narrowing-conversion'))


# Contains valid and invalid unmodeled constant forms. Floating proofs and
# other remaining dependencies are capability gaps, not false diagnostics.
DEFERRED_CASES = (('nested-brace-elision',
  'struct I{int a;int b;};struct O{I i;int c;};int cpp_test_entry(){O s{10,20,12};return '
  's.i.a+s.i.b+s.c;}',
  'cpp.unsupported.aggregate-brace-elision'),
 ('integer-constant-to-float-exactness',
  'struct S{float x;};int cpp_test_entry(){S s{16777216};return int(s.x/16777216);}',
  'cpp.unsupported.narrowing-analysis'),
 ('double-constant-to-float-need-not-be-exact',
  'struct S{float x;};int cpp_test_entry(){S s{0.1};return int(s.x*10.0f);}',
  'cpp.unsupported.narrowing-analysis'),
 ('nested-elision-keeps-clause-effect-order',
  'int take(int *n,int v){*n=*n*10+v;return v;}struct I{int a;int b;};struct O{I i;int c;};int '
  'cpp_test_entry(){int n=0;O s{take(&n,1),take(&n,2),take(&n,3)};return '
  'n*1000+s.i.a*100+s.i.b*10+s.c;}',
  'cpp.unsupported.aggregate-brace-elision'),
 ('nonconstant-double-to-float',
  'struct S{float x;};int f(){double n=0.1;S s{n};return 0;}',
  'cpp.unsupported.narrowing-analysis'),
 ('small-integer-to-double-still-needs-constant',
  'struct S{double x;};int f(){short n=42;S s{n};return 0;}',
  'cpp.unsupported.narrowing-analysis'),
 ('integer-to-float-round-trip-loss',
  'struct S{float x;};int f(){S s{16777217};return 0;}',
  'cpp.unsupported.narrowing-analysis'),
 ('double-constant-outside-float-range',
  'struct S{float x;};int f(){S s{1e100};return 0;}',
  'cpp.unsupported.narrowing-analysis'),
 ('brace-elision-into-empty-member',
  'struct E{};struct S{E e;int x;};int f(){S s{42};return 0;}',
  'cpp.unsupported.aggregate-brace-elision'),
 ('nonaggregate-needs-constructor',
  'class S{int x;};int f(){S s{42};return 0;}',
  'cpp.unsupported.class-constructors'),
 ('copy-member-class-expression',
  'struct I{int x;};struct O{I i;};int f(){I i{};O o{i};return 0;}',
  'cpp.unsupported.class-initialization'),
 ('aggregate-same-class-copy',
  'struct I{int x;};int f(){I i{};I other{i};return 0;}',
  'cpp.unsupported.class-initialization'))


@pytest.mark.parametrize("name,source,value", AGGREGATE_CASES,
                         ids=[case[0] for case in AGGREGATE_CASES])
def test_cpp_aggregate_owned_compile_is_byte_stable(name, source, value):
    units = ((name + ".cpp", source),)
    assert compile_host_cpp(units) == compile_host_cpp(units)


@pytest.mark.parametrize("name,source,code", INVALID_CASES + DEFERRED_CASES,
                         ids=[case[0] for case in INVALID_CASES + DEFERRED_CASES])
def test_cpp_aggregate_invalid_or_missing_rules_reject_before_emission(name, source, code, monkeypatch):
    from pcc.backend import owned_object_emit

    def forbidden(*args, **kwargs):
        raise AssertionError("rejected aggregate initializer reached object emission")

    monkeypatch.setattr(owned_object_emit, "emit_owned_object", forbidden)
    with pytest.raises(CppDiagnostic) as error:
        compile_host_cpp(((name + ".cpp", source),))
    assert error.value.code == code
    assert error.value.coord.file == name + ".cpp"


# Preserve the exact source formerly in CLASS_UNIMPLEMENTED_CASES; this newly
# admitted form now has a positive lowering contract rather than disappearing.
PREVIOUS_CAPABILITY_SOURCE = "struct S{int value;};int f(){S a{1};return 0;}"


def test_previously_gated_nonempty_aggregate_now_has_a_checked_initializer():
    tree = CppParser().parse(PREVIOUS_CAPABILITY_SOURCE)
    function = next(node for node in tree.ext if isinstance(node, c_ast.FuncDef))
    declaration = function.body.block_items[0]
    assert isinstance(declaration.init, c_ast.InitList)
    assert declaration.init.exprs[0].value == "1"


@pytest.mark.integration
@pytest.mark.parametrize("name,source,value", AGGREGATE_CASES,
                         ids=[case[0] for case in AGGREGATE_CASES])
def test_cpp_aggregate_owned_execution_matches_cpp17_reference(name, source, value, tmp_path, monkeypatch):
    execute_and_record(tmp_path, monkeypatch, ((name + ".cpp", source),), value)


@pytest.mark.integration
def test_previously_gated_nonempty_aggregate_executes_through_separate_observer(tmp_path, monkeypatch):
    execute_and_record(tmp_path, monkeypatch,
                       (("previous-capability.cpp", PREVIOUS_CAPABILITY_SOURCE),
                        ("observer.cpp", "int f();int cpp_test_entry(){return f();}")), 0)
