"""File/regex NEW results publish before parking or argument disposal."""
from __future__ import annotations

import re
import textwrap

import pytest

from tests.python.test_shared_call_binding import _emit, _function
from tests.python.test_slot_call_subscript_producers import _assert_immediate_publication
from tests.python.owned_regression_support import (
    assert_owned_program,
    assert_reference_program,
    explicit_owned_runtime,
)


@pytest.mark.parametrize("method,runtime", (
    ("read()", "py_file_read_all"),
    ("read(3)", "py_file_read"),
    ("readline()", "py_file_readline"),
    ("readline(3)", "py_file_readline"),
))
@pytest.mark.parametrize("site", ("return", "argument", "default", "later-error"))
def test_file_read_publishes_before_error_checks(method, runtime, site):
    expression = "stream." + method
    body = {
        "return": "return " + expression,
        "argument": "return take(value=" + expression + ")",
        "default": "def selected(value=" + expression + "):\n            return value\n        return selected()",
        "later-error": "return take(value=" + expression + ", later=fail())",
    }[site]
    text = _emit(
        "def take(*, value, later=None):\n    return value\n"
        "def fail():\n    raise ValueError('later')\n"
        "def probe(path: str):\n    with open(path) as stream:\n        " + body + "\n"
    )
    _assert_immediate_publication(text, runtime)
    assert "@py_cpy_" not in _function(text)


@pytest.mark.parametrize("expression,runtime", (
    ("re.compile(pattern)", "py_re_compile_obj"),
    ("re.compile(pattern, flags)", "py_re_compile_obj"),
    ("re.compile(flags=flags, pattern=pattern)", "py_re_compile_obj"),
    ("re.findall(pattern, text)", "py_re_findall_flags"),
    ("re.findall(pattern, text, flags)", "py_re_findall_flags"),
))
@pytest.mark.parametrize("site", ("return", "argument", "default", "later-error"))
def test_regex_result_publishes_before_operand_cleanup(expression, runtime, site):
    body = {
        "return": "    return " + expression + "\n",
        "argument": "    return take(value=" + expression + ")\n",
        "default": "    def selected(value=" + expression + "):\n        return value\n    return selected()\n",
        "later-error": "    return take(value=" + expression + ", later=fail())\n",
    }[site]
    text = _emit(
        "import re\ndef take(*, value, later=None):\n    return value\n"
        "def fail():\n    raise ValueError('later')\n"
        "def probe(pattern: str, text: str, flags: int):\n" + body
    )
    _assert_immediate_publication(text, runtime)
    call = re.search(r"@" + runtime + r"\(([^\n]+)\)", _function(text))
    assert call and re.search(r", i64 [^,]+$", call.group(1))
    assert "@py_cpy_" not in _function(text)


@pytest.mark.parametrize("imports,expression,runtime", (
    ("import re as regex", "regex.compile(pattern)", "py_re_compile_obj"),
    ("from re import compile as compile_pattern", "compile_pattern(pattern)", "py_re_compile_obj"),
    ("import re as regex", "regex.findall(pattern, text)", "py_re_findall_flags"),
    ("from re import findall as find_words", "find_words(pattern, text)", "py_re_findall_flags"),
))
def test_regex_import_alias_keeps_result_sink(imports, expression, runtime):
    text = _emit(imports + "\ndef take(*, value):\n    return value\n"
                 "def probe(pattern: str, text: str):\n    return take(value=" + expression + ")\n")
    _assert_immediate_publication(text, runtime)


@pytest.mark.parametrize("expression,order,runtime", (
    ("re.compile(pattern(), flags())", ("pattern", "flags"), "py_re_compile_obj"),
    ("re.compile(flags=flags(), pattern=pattern())", ("flags", "pattern"), "py_re_compile_obj"),
    ("re.findall(pattern(), text(), flags())", ("pattern", "text", "flags"), "py_re_findall_flags"),
))
def test_regex_arguments_keep_source_order_and_checked_scalar_flags(expression, order, runtime):
    body = _function(_emit(
        "import re\ndef pattern():\n    return '[a-z]+'\n"
        "def text():\n    return 'alpha beta'\n"
        "def flags():\n    return 0\n"
        "def probe():\n    return " + expression + "\n"
    ))
    positions = []
    for name in order:
        match = re.search(r"(%[^ ]+) = call [^\n]*@user_binding_" + name + r"\([^\n]*\)\n", body)
        assert match, name
        assert body[match.end():].lstrip().startswith("store ptr " + match.group(1) + ",")
        positions.append(match.start())
    assert positions == sorted(positions)
    assert positions[-1] < body.index("@py_index_i64_checked(") < body.index("@" + runtime + "(")
    _assert_immediate_publication(body, runtime)


def test_file_receiver_is_copied_before_limit_callback_and_reloaded_for_read():
    body = _function(_emit(
        "def limit():\n    return 3\n"
        "def probe(path: str):\n    with open(path) as stream:\n        return stream.read(limit())\n"
    ))
    call = body.index("@user_binding_limit(")
    read = body.index("@py_file_read(")
    assert "@pcc_gc_root_copy_lease(" in body[:call]
    assert call < body.index("@py_index_i64_checked(") < read
    _assert_immediate_publication(body, "py_file_read")


PROGRAM = textwrap.dedent('''\
    import gc
    import re
    events = []
    def part(label, value):
        events.append(label)
        gc.collect()
        return value
    def take(*, value, later=None):
        gc.collect()
        return value
    def fail():
        gc.collect()
        raise ValueError('later')
    def main():
        with open('file-re-input.txt', 'w') as stream:
            assert stream.write('alpha beta\\ngamma\\n') == 17
            assert stream.tell() == 17
            assert stream.flush() is None
        with open('file-re-input.txt') as stream:
            assert take(value=stream.read(5)) == 'alpha'
            assert stream.seek(0) == 0
            assert take(value=stream.readline()) == 'alpha beta\\n'
            def saved(value=stream.read()):
                gc.collect()
                return value
            assert saved() == 'gamma\\n'
            assert saved() is saved()
        try:
            stream.read()
        except ValueError:
            pass
        else:
            raise AssertionError('closed file accepted')
        compiled = take(value=re.compile(flags=part('flags', 0), pattern=part('pattern', '[a-z]+')))
        assert compiled.pattern == '[a-z]+'
        assert events == ['flags', 'pattern']
        events.clear()
        assert take(value=re.findall(part('pattern', '[a-z]+'), part('text', 'alpha beta'), part('flags', 0))) == ['alpha', 'beta']
        assert events == ['pattern', 'text', 'flags']
        def saved_pattern(value=re.compile('[a-z]+')):
            gc.collect()
            return value
        assert saved_pattern() is saved_pattern()
        try:
            take(value=re.findall('[a-z]+', 'alpha beta'), later=fail())
        except ValueError as error:
            assert str(error) == 'later'
        else:
            raise AssertionError('later exception lost')
        print('FILE_RE_RESULT_OWNERS_OK')
    main()
''')


def test_file_re_program_reference(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    assert_reference_program(PROGRAM, "FILE_RE_RESULT_OWNERS_OK\n", tmp_path)


@pytest.mark.integration
@pytest.mark.parametrize("python_program_compiler", ("pcc0", "pcc1"), indirect=True)
def test_file_re_result_native_five_gc(python_program_compiler, request,
                                      explicit_owned_runtime, tmp_path, capfd):
    mode = request.node.callspec.params["python_program_compiler"]
    assert_owned_program(PROGRAM, "FILE_RE_RESULT_OWNERS_OK\n", tmp_path,
                         python_program_compiler, mode, explicit_owned_runtime, capfd)
