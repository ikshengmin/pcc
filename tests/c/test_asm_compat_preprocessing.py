"""GNU asm around preprocessing: the owned path keeps it, system cpp erases it.

The owned preprocessor no longer defines asm away; the parser keeps each
statement as an AsmStatement (see test_c_gnu_asm.py for its semantics). The
system-cpp compatibility route still erases asm as text. Either way the
surrounding C must be unchanged.
"""

import subprocess

import pytest

from pcc.frontends.c.ast import c_ast
from pcc.frontends.c.evaluator.c_evaluator import _preprocess_translation_unit_source
from pcc.frontends.c.parse import make_c_parser
from pcc.frontends.c.parse.ast_normalize import normalize


_FOUR_OPERANDS = '\"\" : : \"r\"(a), \"r\"(b), \"r\"(c), \"r\"(d) : \"memory\"'
_FORMS = (
    ("basic", '\"literal, comma\"', False),
    ("four-operands", _FOUR_OPERANDS, False),
    ("nested-commas", '\"\" : : \"r\"((a, b)), \"r\"((c, d)) : \"memory\"', False),
    ("indirect-macro", _FOUR_OPERANDS, True),
)
_EXPECTED = """
int outside = 9;
int keep(int a, int b, int c, int d) {
    int before = 1;
    ;
    return a + b + c + d + before + outside;
}
"""


@pytest.mark.parametrize("use_system_cpp", (False, True), ids=("owned", "system"))
@pytest.mark.parametrize("spelling", ("asm", "__asm__", "__asm"))
@pytest.mark.parametrize("case,operands,indirect", _FORMS, ids=[x[0] for x in _FORMS])
def test_asm_compat_preserves_the_surrounding_c_ast(
    tmp_path, monkeypatch, use_system_cpp, spelling, case, operands, indirect
):
    calls = []
    real_run = subprocess.run

    def run_preprocessor_only(command, *args, **kwargs):
        assert use_system_cpp, "owned preprocessing must not invoke a tool"
        assert isinstance(command, list)
        assert "-E" in command, command
        assert not {"-c", "-S"}.intersection(command), command
        calls.append(tuple(command))
        return real_run(command, *args, **kwargs)

    monkeypatch.setattr(subprocess, "run", run_preprocessor_only)
    if not use_system_cpp:
        def forbidden_process(*args, **kwargs):
            raise AssertionError("owned preprocessing invoked an external process")
        monkeypatch.setattr(subprocess, "Popen", forbidden_process)

    prefix = f"#define PCC_ASM_ALIAS {spelling}\n" if indirect else ""
    token = "PCC_ASM_ALIAS" if indirect else spelling
    source = prefix + _EXPECTED.replace("    ;", f"    {token}({operands});")
    processed = _preprocess_translation_unit_source(
        source,
        str(tmp_path),
        use_system_cpp,
        target_triple="x86_64-unknown-linux-gnu",
    )
    parser = make_c_parser()
    ast = parser.parse(processed)
    if not use_system_cpp:
        body = ast.ext[-1].body.block_items
        kept = [index for index, item in enumerate(body) if isinstance(item, c_ast.AsmStatement)]
        assert len(kept) == 1, "owned preprocessing must keep the asm statement"
        body[kept[0]] = c_ast.EmptyStatement(body[kept[0]].coord)
    assert normalize(ast) == normalize(parser.parse(_EXPECTED))
    assert bool(calls) is use_system_cpp
