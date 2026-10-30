"""C parser tests defer real parser construction until their first test use."""

import importlib.util
from pathlib import Path
import sys

import pytest


_MODULES = (
    ("test_c_parse_driver_parity", "_ply", False),
    ("test_c_parser", "_c_parser", True),
    ("test_ssa_dse_pass", "_PARSER", True),
    ("test_ssa_gvn", "_PARSER", True),
    ("test_ssa_adce", "_PARSER", True),
    ("test_ssa_builder", "_PARSER", True),
    ("test_ssa_loop_phi", "_PARSER", True),
    ("test_ssa_adce_pass", "_PARSER", True),
    ("test_whole_program", "_PARSER", True),
    ("test_ssa_gvn_pass", "_PARSER", True),
    ("test_ssa_branch_prune", "_PARSER", True),
    ("test_c_parser_oracle", "_parser", False),
    ("test_ssa_sccp_rewrite", "_PARSER", True),
    ("test_ssa_gvn_rewrite", "_PARSER", True),
    ("test_ssa_bootstrap_pass", "_PARSER", True),
    ("test_ssa_sccp", "_PARSER", True),
    ("test_ssa_sccp_pass", "_PARSER", True),
    ("test_c_generator", "_c_parser", True),
)


def _load_test_module(monkeypatch, stem):
    path = Path(__file__).parent / "c" / (stem + ".py")
    name = "_pcc_parser_collection_" + stem
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, name, module)
    old_path = sys.path[:]
    try:
        spec.loader.exec_module(module)
    finally:
        sys.path[:] = old_path
    return module


def _record_real_construction(monkeypatch):
    from pcc.frontends.c.parse import c_parser

    real_parser = c_parser.CParser
    calls = []

    def construct(*args, **kwargs):
        calls.append((args, kwargs))
        return real_parser(*args, **kwargs)

    monkeypatch.setattr(c_parser, "CParser", construct)
    return real_parser, calls


def test_importing_parser_test_modules_constructs_no_parsers(monkeypatch):
    _, calls = _record_real_construction(monkeypatch)
    modules = [
        _load_test_module(monkeypatch, stem)
        for stem, _, _ in _MODULES
    ]
    assert len(modules) == 18
    assert calls == [], f"module imports constructed {len(calls)} real CParser instances"


@pytest.mark.parametrize("stem, cache_name, explicit_options", _MODULES)
def test_first_use_reuses_real_parser_and_preserves_results(
    monkeypatch, stem, cache_name, explicit_options,
):
    from pcc.frontends.c.parse.ast_normalize import normalize

    real_parser, calls = _record_real_construction(monkeypatch)
    module = _load_test_module(monkeypatch, stem)
    assert calls == []
    expected_options = (
        {"lex_optimize": True, "yacc_debug": False, "yacc_optimize": True}
        if explicit_options else {}
    )
    parser = module._get_parser()
    assert type(parser) is real_parser
    assert calls == [((), expected_options)]
    assert getattr(module, cache_name) is parser
    assert module._get_parser() is parser
    assert len(calls) == 1

    reference = real_parser(**expected_options)
    for source in (
        "typedef int Count; Count f(Count n) { return n + 1; }",
        "int sum(int x) { int y = x * 2; return y; }",
    ):
        expected = reference.parse(source, filename="<collection-parity>")
        actual = parser.parse(source, filename="<collection-parity>")
        assert normalize(actual) == normalize(expected)
    assert module._get_parser() is parser
    assert len(calls) == 1
