"""Owned POSIX header parsing, not a flock implementation or native Readline run."""

from pathlib import Path
import subprocess

import pytest

from pcc.frontends.c.ast import c_ast
from pcc.frontends.c.parse.c_parser import CParser
from pcc.frontends.c.preprocessor import preprocess


ROOT = Path(__file__).resolve().parents[2]
TARGETS = (
    "x86_64-unknown-linux-gnu",
    "aarch64-unknown-linux-gnu",
    "arm64-apple-darwin",
)
INCLUDES = (
    "#include <sys/file.h>\n#include <sys/file.h>\n",
    "#include <fcntl.h>\n#include <sys/file.h>\n",
    "#include <sys/file.h>\n#include <fcntl.h>\n",
)
BODY = """
int lock_constants[] = {LOCK_SH, LOCK_EX, LOCK_NB, LOCK_UN};
int call_lock(int fd) { return flock(fd, LOCK_EX | LOCK_NB); }
"""


@pytest.fixture(scope="module")
def parser():
    # Construct only when the selected tests run, never at collection.
    return CParser()


@pytest.fixture(autouse=True)
def no_external_preprocessor(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("header regression must use the owned preprocessor")

    monkeypatch.setattr(subprocess, "Popen", forbidden)


def _int_type(node):
    assert isinstance(node, c_ast.TypeDecl)
    assert isinstance(node.type, c_ast.IdentifierType)
    assert node.type.names == ["int"]


def _assert_interface(tree):
    declarations = [node for node in tree.ext
                    if isinstance(node, c_ast.Decl) and node.name == "flock"]
    assert len(declarations) == 1
    declaration = declarations[0]
    assert isinstance(declaration.type, c_ast.FuncDecl)
    _int_type(declaration.type.type)
    assert len(declaration.type.args.params) == 2
    for argument in declaration.type.args.params:
        assert isinstance(argument, c_ast.Decl)
        _int_type(argument.type)
    assert not any(isinstance(node, c_ast.FuncDef) and node.decl.name == "flock"
                   for node in tree.ext)

    values = next(node for node in tree.ext
                  if isinstance(node, c_ast.Decl) and node.name == "lock_constants")
    assert isinstance(values.init, c_ast.InitList)
    assert [(node.type, node.value) for node in values.init.exprs] == [
        ("int", "1"), ("int", "2"), ("int", "4"), ("int", "8"),
    ]
    function = next(node for node in tree.ext
                    if isinstance(node, c_ast.FuncDef) and node.decl.name == "call_lock")
    result = function.body.block_items[0]
    assert isinstance(result, c_ast.Return)
    assert isinstance(result.expr, c_ast.FuncCall)
    assert result.expr.name.name == "flock"
    fd, operation = result.expr.args.exprs
    assert isinstance(fd, c_ast.ID) and fd.name == "fd"
    assert isinstance(operation, c_ast.BinaryOp) and operation.op == "|"
    assert (operation.left.type, operation.left.value) == ("int", "2")
    assert (operation.right.type, operation.right.value) == ("int", "4")


@pytest.mark.parametrize("target", TARGETS)
@pytest.mark.parametrize("includes", INCLUDES, ids=("repeated", "fcntl-first", "file-first"))
def test_sys_file_header_has_real_constants_and_function_declaration(parser, target, includes):
    text = preprocess(includes + BODY, target_triple=target)
    _assert_interface(parser.parse(text, filename="sys_file_contract.c"))


@pytest.mark.parametrize("name,include_block", (
    ("readline.c", "#if defined (HAVE_SYS_FILE_H)\n#  include <sys/file.h>\n#endif /* HAVE_SYS_FILE_H */"),
    ("histfile.c", "#if ! defined (_MINIX) && defined (HAVE_SYS_FILE_H)\n#  include <sys/file.h>\n#endif"),
))
def test_real_readline_conditional_include_is_resolved(parser, name, include_block):
    source = (ROOT / "projects" / "readline-8.2" / name).read_text(encoding="utf-8")
    assert source.count(include_block) == 1
    # Exercise the actual guarded include with the feature enabled, not a
    # vendor-macro workaround. This is not a full configured Readline build.
    text = preprocess("#define HAVE_SYS_FILE_H 1\n" + include_block + "\n" + BODY,
                      target_triple="x86_64-unknown-linux-gnu")
    _assert_interface(parser.parse(text, filename=name + "-include-contract.c"))


def test_missing_system_header_still_fails_closed():
    with pytest.raises(RuntimeError, match="owned preprocessor: header not found: pcc_missing_header.h"):
        preprocess("#include <pcc_missing_header.h>\n", target_triple=TARGETS[0])
