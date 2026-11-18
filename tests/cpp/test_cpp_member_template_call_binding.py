"""Public parser regressions. No host compiler or native execution."""
import pytest
from pcc.frontends.cpp import CppParser, CppDiagnostic


def parse(source):
    return CppParser(target_triple='x86_64-unknown-linux-gnu').parse(source)


@pytest.mark.parametrize('source', [
 'struct S{int h(int);template<class T>int f(T x){return h(x);}};int S::h(int x){return x;}int entry(){S s;return s.f(42);}',
 'struct S{static int h(int);template<class T>int f(T x){return h(x);}};int S::h(int x){return x;}int entry(){S s;return s.f(42);}',
 'struct S{template<class U>U h(U x){return x;}template<class T>int f(T x){return h(42);}};int entry(){S s;return s.f(1);}',
 'struct S{template<class U>U h(U x){return x;}template<class T>T f(T x){return h(x);}};int entry(){S s;return s.f(42);}',
])
def test_cold_parser_binds_and_replays_named_calls(source):
    # Separate parser instances ensure no warmed cache supplies missing bodies.
    parse(source)
    parse(source)


@pytest.mark.parametrize('source,code', [
    ('struct S{int h(int,int);template<class T>int f(T x){return h(x);}};',
     'cpp.no-viable-overload'),
    ('struct S{int h(int,int,...);template<class T>int f(T x){return h(x);}};',
     'cpp.no-viable-overload'),
    ('struct S{template<class U>int h(U*);template<class T>int f(T x){return h(42);}};',
     'cpp.no-viable-overload'),
    # This original local-object case stops at a separate retained capability
    # boundary; it is not evidence for the access rejection in the next row.
    ('class H{template<class U>U h(U x){return x;}};struct S{template<class T>int f(T x){H h;return h.h(42);}};',
     'cpp.unsupported.class-templates'),
    ('class H{template<class U>U h(U x){return x;}};struct S{template<class T>int f(H& object,T x){return object.h(42);}};',
     'cpp.member-access'),
])
def test_unused_body_reports_the_actual_declaration_time_boundary(source, code):
    with pytest.raises(CppDiagnostic) as error:
        parse(source)
    assert error.value.code == code
