"""Ordinary string literals: N4659 [lex.string]/8,9,13,14, [lex.ccon]/7-8, [conv.array].

https://timsong-cpp.github.io/cppwp/n4659/lex.string
An ordinary string literal is an lvalue `const char[N]`; adjacent literals
concatenate after escapes are decoded; the execution character set is UTF-8.
A class template member may pass a literal to a non-template function (the
array argument decays; no deduction is involved), as TinyXML2's
MemPoolT::Trace does with printf (projects/tinyxml2-11.0.0, Zlib).
Original tests; no external compiler implementation is copied.
"""
import pytest

from pcc.frontends.cpp import CppDiagnostic, CppParser
from tests.cpp.owned_execution import compile_host_cpp
from tests.cpp.test_cpp_owned_execution import owned_only

LENGTH = 'typedef unsigned long size_t;size_t length(const char*s){size_t n=0;while(s[n])n=n+1;return n;}'

PROGRAMS = (
    ('concatenation-escapes-and-utf8',
     LENGTH + 'int cpp_test_entry(){const char*a="hello";const char*b="\\x41" "2" "\\101\\n\\u00e9";'
              'int sum=0;for(size_t i=0;b[i];i=i+1)sum=sum+(unsigned char)b[i];'
              'return (int)(length(a)+length(b)+sizeof("abc")+sizeof("\\u00e9"))+a[1]-77+(sum==554?0:1);}'),
    ('literal-in-a-class-template-member',
     LENGTH + 'template<int N>struct M{size_t name(){return length("pool" "-" "x")+N;}};'
              'int cpp_test_entry(){M<36> m;return (int)m.name();}'),
    ('source-utf8-and-octal-boundaries',
     LENGTH + 'int cpp_test_entry(){const char*s="\\0012" "é";'
              'return (int)length(s+1)+(s[0]==1)*10+(s[1]==\'2\')*20+(sizeof("é")==3)*9;}'),
)


@pytest.mark.parametrize('name,source', PROGRAMS, ids=[item[0] for item in PROGRAMS])
def test_string_literal_programs_compile_to_stable_artifacts(owned_only, name, source):
    units = ((name + '.cpp', source),)
    assert compile_host_cpp(units) == compile_host_cpp(units)


@pytest.mark.integration
@pytest.mark.parametrize('name,source', PROGRAMS, ids=[item[0] for item in PROGRAMS])
def test_string_literal_programs_execute_against_real_cpp17_reference(name, source, tmp_path, monkeypatch):
    from tests.cpp.sizeof_execution import execute_row_pair
    execute_row_pair(tmp_path, monkeypatch, ((name + '.cpp', source),))


# Former boundary rows from test_cpp_inline_methods and
# test_cpp_inline_constructors, moved verbatim: literals now type as const char[N].
MOVED_ORIGINALS = (
    'struct S{const char*f(){return "identity";}};',
    'struct S{S():p("same-address"){} const char*p;};',
)


@pytest.mark.parametrize('source', MOVED_ORIGINALS)
def test_moved_string_literal_originals_are_positive(source):
    CppParser(target_triple='x86_64-unknown-linux-gnu').parse(source)


def test_literal_bytes_are_spelled_exactly():
    from pcc.frontends.cpp.string_literals import decode, spelling_for
    data = decode('"\\x41"', None) + decode('"2"', None) + decode('"\\u00e9\\0"', None)
    assert data == b'A2\xc3\xa9\x00'
    # Octal re-spelling never merges with a following digit.
    assert spelling_for(b'\x01' + b'2') == '"\\0012"'


@pytest.mark.parametrize('source,code', (
    ('const char*s=L"wide";', 'cpp.unsupported.string-literals'),
    ('const char*s="\\q";', 'cpp.unsupported.string-literals'),
    ('const char*s="\\x100";', 'cpp.invalid-string-literal'),
    ('const char*s="\\777";', 'cpp.invalid-string-literal'),
    ('const char*s="\\u12";', 'cpp.invalid-string-literal'),
    ('const char*s="\\uD800";', 'cpp.invalid-string-literal'),
))
def test_unmodeled_or_invalid_string_literals_are_explicit(source, code):
    with pytest.raises(CppDiagnostic) as error:
        CppParser(target_triple='x86_64-unknown-linux-gnu').parse('void f(){' + source + '}')
    assert error.value.code == code
