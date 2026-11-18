"""Public parser checks of definition-bound named explicit template calls."""
import pytest
from pcc.frontends.cpp import CppParser, CppDiagnostic


def parse(source):
    return CppParser(target_triple='x86_64-unknown-linux-gnu').parse(source)


PREFIX = 'class S{template<class U>U hidden(U x){return x;}public:template<class T>T f(T x){return hidden<T>(x);}};'


def test_private_same_owner_explicit_call():
    source = PREFIX+'int entry(){S s;return s.f(42);}'
    parse(source)
    parse(source)


def test_same_private_template_is_forbidden_from_nonmember():
    with pytest.raises(CppDiagnostic) as error:
        parse(PREFIX+'int entry(){S s;return s.hidden<int>(42);}')
    assert error.value.code == 'cpp.member-access'


def test_empty_explicit_list_excludes_ordinary_overload():
    parser = CppParser(target_triple='x86_64-unknown-linux-gnu')
    parser.parse('struct S{int hidden(int);template<class U>U hidden(U x){return x;}template<class T>int f(T x){return hidden<>(42);}};')
    method = parser.scope.names['S'].dtype.definition.scope.names['f'].overloads[0]
    def calls(pattern):
        node = getattr(pattern, 'node', pattern)
        if getattr(node, 'kind', None) == 'function-member-call':
            yield node
        for child in getattr(node, 'children', ()):
            yield from calls(child)
    call, = list(calls(method.pattern))
    assert call.value[-1] == ()
    assert call.value[4].template_origin is not None


def test_nondependent_explicit_argument_mismatch_rejected_in_unused_body():
    with pytest.raises(CppDiagnostic) as error:
        parse('struct S{template<class U>U hidden(U*);template<class T>int f(T x){return hidden<int>(42);}};')
    assert error.value.code == 'cpp.no-viable-overload'


def test_explicit_value_parameter_replays_existing_value_pattern():
    parse('struct S{template<int N>int hidden(int x){return x+N;}template<int M>int f(int x){return hidden<M>(x);}};int entry(){S s;return s.f<2>(40);}')


@pytest.mark.integration
@pytest.mark.parametrize('name,source', (
    ('private-type', PREFIX + 'int cpp_test_entry(){S s;return s.f(42);}'),
    ('private-value',
     'class S{template<int N>int hidden(int x){return x+N;}public:'
     'template<int M>int f(int x){return hidden<M>(x);}};'
     'int cpp_test_entry(){S s;return s.f<2>(40);}'),
))
def test_named_private_template_arguments_execute_from_the_declaring_context(name, source, tmp_path):
    import os
    import shutil
    from tests.cpp.owned_execution import (
        Execution, assert_exact, compile_host_cpp, compile_reference_cpp,
        run_executable, write_host_executable,
    )
    units = ((name + '.cpp', source),)
    compiler = shutil.which(os.environ.get('PCC_CPP_ORACLE', 'g++'))
    assert compiler
    oracle = compile_reference_cpp(units, tmp_path / 'reference', compiler=compiler)
    owned = tmp_path / 'owned'
    write_host_executable(compile_host_cpp(units), owned)
    expected = Execution(42, b'\x2a\x00\x00\x00', b'')
    assert_exact(run_executable(oracle), expected)
    assert_exact(run_executable(owned), expected)
