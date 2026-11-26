"""Owned regex compile admission and verbose lexical semantics."""

import ast
from pathlib import Path
import re

import pytest


def _normalizer():
    source = Path(__file__).resolve().parents[2] / "pcc/runtime/py/py_re_engine_runtime.py"
    tree = ast.parse(source.read_text())
    definitions = [node for node in tree.body if isinstance(node, ast.FunctionDef)
                   and node.name in ("_normalize_verbose_pattern", "_normalize_compile_pattern")]
    for node in definitions:
        node.decorator_list = []
    output = {}
    namespace = {"c_ptr": object, "pcc_gc_retain": lambda value: value,
                 "pcc_re_publish_copy": lambda value, slot: slot.update(value=value) or 0,
                 "PYOBJECTHEADER_FLAGS_OFFSET": 12, "PY_FLAG_GC_PINNED": 64,
                 "load_i32": lambda value, offset: 0, "atomic_rmw_i32": lambda *args: 0, "pcc_gc_pin": lambda value: None,
                 "pcc_gc_unpin": lambda value: None,
                 "pcc_gc_store_root": lambda slot, value: slot.update(value=value),
                 "py_err_occurred": lambda: 0,
                 "store_i64": lambda slot, offset, value: slot.update(token=value),
                 "load_ptr": lambda slot, offset: slot["value"]}
    exec(compile(ast.Module(body=definitions, type_ignores=[]), str(source), "exec"), namespace)
    def run(pattern, flags):
        namespace["_normalize_compile_pattern"](pattern, flags, output, {}, {})
        return output["value"]
    return run


@pytest.mark.parametrize("pattern, flags, texts", [
    ("a b # comment\n c", re.VERBOSE, ["abc", "a b c", "xabc"]),
    (r"[ #]+ \# ", re.VERBOSE, [" ##", "# #", "###"]),
    (r"a\ b", re.VERBOSE, ["a b", "ab"]),
    (r"[^^]+ # caret class", re.VERBOSE, ["abc", "^^"]),
    (r"[] #]+", re.VERBOSE, ["] #", "abc"]),
    (r"a{ 2 }", re.VERBOSE, ["a{2}", "aa", "a{ 2 }"]),
    (r"a{2,3} b", re.VERBOSE, ["aab", "aaab", "ab"]),
    (r"a{,} b", re.VERBOSE, ["b", "ab", "aaaab"]),
    (r"(?x)a b", 0, ["ab", "a b"]),
    (r"(?im)^a b$", re.VERBOSE, ["AB", "abc\nAB"]),
    (r"^a.b$", re.DOTALL, ["a\nb", "axb"]),
])
def test_owned_verbose_projection_matches_cpython(pattern, flags, texts):
    normalized, engine_flags = _normalizer()(pattern, int(flags))
    expected = re.compile(pattern, flags)
    projected = re.compile(normalized, engine_flags)
    for text in texts:
        assert projected.findall(text) == expected.findall(text)


@pytest.mark.parametrize("pattern", ["(? :a)", "a* ?", "a+ # comment\n ?"])
def test_verbose_projection_fails_explicitly_before_joining_invalid_tokens(pattern):
    with pytest.raises(re.error):
        re.compile(pattern, re.VERBOSE)
    with pytest.raises(NotImplementedError, match="separated"):
        _normalizer()(pattern, 64)


def test_toml_regexes_use_the_same_general_verbose_projection():
    from pcc.stdlib.tomllib import _re

    for pattern in (_re.RE_NUMBER.pattern, _re.RE_DATETIME.pattern, _re.RE_LOCALTIME.pattern):
        normalized, flags = _normalizer()(pattern, 64)
        expected = re.compile(pattern, re.VERBOSE)
        projected = re.compile(normalized, flags)
        for text in ("12:34:56.123456789", "1979-05-27T07:32:00Z", "-1_234.5e-2", "0xBAD", "no match"):
            assert projected.findall(text) == expected.findall(text)


def test_keyword_and_dynamic_regex_compile_emit_owned_calls(tmp_path):
    from pcc.frontends.python.pipeline import compile_python

    source = tmp_path / "regex_compile.py"
    source.write_text(
        "import re\n"
        "def positional(): return re.compile('a b', re.VERBOSE)\n"
        "def keyword(): return re.compile('a b', flags=re.VERBOSE)\n"
        "def all_keywords(): return re.compile(flags=re.VERBOSE, pattern='a b')\n"
        "def dynamic(pattern: str, flags: int): return re.compile(pattern, flags=flags)\n"
        "def unsupported(): return re.compile('(?<=a)b')\n"
    )
    output = tmp_path / "regex_compile.ll"
    compile_python(str(source), str(output), emit_llvm_only=True, python_library=True,
                   ir_scaffold_mode="on", backend="self")
    text = output.read_text()
    for name in ("positional", "keyword", "all_keywords", "dynamic", "unsupported"):
        body = re.search(r"^define[^\n]*@user_regex_compile_" + name + r"\(.*?^}", text, re.M | re.S)
        assert body is not None, name
        assert "@py_re_compile_obj" in body.group(), name
        assert "@py_cpy_" not in body.group(), name
        assert "strict.nolib.stub" not in body.group(), name


@pytest.mark.parametrize("verbose", [False, True])
@pytest.mark.parametrize("fail_publish", [0, 1, 2, 3])
def test_normalizer_slot_handoff_publishes_each_owner_and_stops_at_a_failed_copy(verbose, fail_publish):
    """Source handoff model only; the actual generated cleanup is checked in IR.

    The one collector's publish copy returns a status only (item 2 (d) U8).
    """
    source = Path(__file__).resolve().parents[2] / "pcc/runtime/py/py_re_engine_runtime.py"
    nodes = [node for node in ast.parse(source.read_text()).body if isinstance(node, ast.FunctionDef)
             and node.name in ("_normalize_verbose_pattern", "_normalize_compile_pattern")]
    for node in nodes:
        node.decorator_list = []
    publish_calls = []
    def publish(value, slot):
        publish_calls.append(slot)
        if len(publish_calls) == fail_publish:
            return -1
        slot["value"] = value
        return 0
    ns = dict(c_ptr=object, pcc_gc_retain=lambda value: value,
              pcc_gc_store_root=lambda slot,value: slot.update(value=value),
              pcc_re_publish_copy=publish,
              load_ptr=lambda slot,offset: slot["value"], py_err_occurred=lambda: 0)
    exec(compile(ast.Module(body=nodes, type_ignores=[]), str(source), "exec"), ns)
    output, scratch, projection = {}, {}, {}
    result = ns["_normalize_compile_pattern"]("(?i)a b" if verbose else "(?i)ab",
                                                64 if verbose else 0, output, scratch, projection)
    assert result == (-1 if 0 < fail_publish <= (3 if verbose else 1) else 0)
    if verbose and fail_publish == 1:
        assert not projection and not scratch and not output
    elif verbose and fail_publish == 2:
        assert projection and not scratch and not output
    elif fail_publish == (3 if verbose else 1):
        assert not output
    else:
        assert output["value"] == ("ab", 2)
