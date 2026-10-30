"""Owned header/AST/layout contracts; no terminal calls or native Readline run."""

from itertools import permutations
from pathlib import Path
import subprocess

import pytest

from pcc.frontends.c.ast import c_ast
from pcc.frontends.c.parse.c_parser import CParser
from pcc.frontends.c.preprocessor import preprocess


ROOT = Path(__file__).resolve().parents[2]
LINUX_TARGETS = ("x86_64-unknown-linux-gnu", "aarch64-unknown-linux-gnu")
OTHER_TARGETS = ("arm64-apple-darwin", "x86_64-pc-windows-msvc")
HEADERS = ("termio.h", "termios.h", "sys/ioctl.h")
INCLUDE_ORDERS = tuple(permutations(HEADERS))
LEGACY_FIELDS = (
    ("c_iflag", 0, 2), ("c_oflag", 2, 2), ("c_cflag", 4, 2),
    ("c_lflag", 6, 2), ("c_line", 8, 1), ("c_cc", 9, 8),
)
LIBC_FIELDS = (
    ("c_iflag", 0, 4), ("c_oflag", 4, 4), ("c_cflag", 8, 4),
    ("c_lflag", 12, 4), ("c_line", 16, 1), ("c_cc", 17, 32),
    ("c_ispeed", 52, 4), ("c_ospeed", 56, 4),
)
KERNEL_FIELDS = LIBC_FIELDS[:5] + (("c_cc", 17, 19),)
CONSTANTS = {
    "NCC": 8, "NCCS": 32,
    "TCGETA": 0x5405, "TCSETA": 0x5406, "TCSETAW": 0x5407,
    "TCSETAF": 0x5408, "TCXONC": 0x540A,
    "VINTR": 0, "VQUIT": 1, "VERASE": 2, "VKILL": 3, "VEOF": 4,
    "VTIME": 5, "VMIN": 6, "VSWTC": 7, "VSTART": 8, "VSTOP": 9,
    "VSUSP": 10, "VEOL": 11, "VREPRINT": 12, "VDISCARD": 13,
    "VWERASE": 14, "VLNEXT": 15, "VEOL2": 16,
    "INPCK": 0x10, "ISTRIP": 0x20, "INLCR": 0x40, "ICRNL": 0x100,
    "IXON": 0x400, "IXANY": 0x800, "OPOST": 1, "ONLCR": 4,
    "CSIZE": 0x30, "CS8": 0x30, "ISIG": 1, "ICANON": 2,
    "ECHO": 8, "ECHOCTL": 0x200, "FLUSHO": 0x1000,
    "_POSIX_VDISABLE": 0, "TCSANOW": 0, "TCSADRAIN": 1,
    "TCOOFF": 0, "TCOON": 1,
}
LEGACY_CONSTANTS = {name: CONSTANTS[name] for name in (
    "NCC", "TCGETA", "TCSETA", "TCSETAW", "TCSETAF", "TCXONC",
)}
BODY = """
#if defined(VDSUSP) || defined(VSTATUS)
#error BSD control-character indices must not leak into Linux
#endif
/* Reference shape from Linux asm-generic/termbits.h, not a libc export. */
struct pcc_kernel_termios_reference {
    unsigned int c_iflag;
    unsigned int c_oflag;
    unsigned int c_cflag;
    unsigned int c_lflag;
    unsigned char c_line;
    unsigned char c_cc[19];
};
int get_legacy(int fd, struct termio *state) {
    return ioctl(fd, TCGETA, state);
}
int set_legacy(int fd, struct termio *state) {
    state->c_lflag &= ~(ICANON | ECHO);
    state->c_cc[VMIN] = 1;
    state->c_cc[VTIME] = 0;
    return ioctl(fd, TCSETAW, state);
}
int get_posix(int fd, struct termios *state) {
    return tcgetattr(fd, state);
}
"""
READLINE_INCLUDE = """#if defined (TERMIO_TTY_DRIVER)
#  include <termio.h>
#  if !defined (TCOON)
#    define TCOON 1
#  endif
#endif /* TERMIO_TTY_DRIVER */"""


@pytest.fixture(scope="module")
def parser():
    return CParser()


@pytest.fixture(autouse=True)
def no_external_tools(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("termio header contract must stay owned and host-only")

    monkeypatch.setattr(subprocess, "Popen", forbidden)


def _parse(parser, source, target, constants):
    initializers = "\n".join(
        "int constant_" + name + " = " + name + ";" for name in constants
    )
    text = preprocess(source + "\n" + initializers, target_triple=target)
    tree = parser.parse(text, filename="termio_contract.c")
    declarations = {node.name: node for node in tree.ext
                    if isinstance(node, c_ast.Decl) and node.name}
    for name, expected in constants.items():
        value = declarations["constant_" + name].init
        assert isinstance(value, c_ast.Constant)
        assert int(value.value, 0) == expected
    return tree


def _struct(tree, name):
    found = [node.type for node in tree.ext
             if isinstance(node, c_ast.Decl)
             and isinstance(node.type, c_ast.Struct)
             and node.type.name == name and node.type.decls is not None]
    assert len(found) == 1
    return found[0]


def _assert_layout(tree, target, name, fields, size, alignment):
    # Use the real aggregate owners, not ctypes or the host C compiler.
    # Only project types and ask layout queries: no function/IR compilation,
    # object emission, linking, or native execution.
    from pcc.frontends.c.codegen.c_codegen import CCodeGenerator
    from pcc.frontends.c.ssa.builder import SSABuilder

    node = _struct(tree, name)
    assert [field.name for field in node.decls] == [row[0] for row in fields]
    builder = SSABuilder(target)
    builder.index_file_scope(tree)
    assert (builder._ast_type_size(node), builder._ast_type_align(node)) == (size, alignment)
    codegen = CCodeGenerator()
    codegen.set_target_text(target, "")
    for declaration in tree.ext:
        if isinstance(declaration, c_ast.Typedef) and declaration.name in (
            "tcflag_t", "cc_t", "speed_t",
        ):
            codegen.codegen_Typedef(declaration)
    physical = codegen._resolve_ast_type(node)
    assert (codegen._ir_type_size(physical), codegen._ir_type_align(physical)) == (size, alignment)
    for field_name, expected_offset, expected_size in fields:
        offset, field_type = codegen._get_aggregate_field_info(physical, field_name)
        assert (offset, codegen._ir_type_size(field_type)) == (expected_offset, expected_size)
    assert not list(codegen.module.functions)


def _assert_interface(tree):
    declarations = [node for node in tree.ext
                    if isinstance(node, c_ast.Decl) and node.name == "ioctl"]
    assert len(declarations) == 1
    signature = declarations[0].type
    assert isinstance(signature, c_ast.FuncDecl)
    assert signature.type.type.names == ["int"]
    fd, request, variadic = signature.args.params
    assert fd.type.type.names == ["int"]
    assert request.type.type.names == ["unsigned", "long"]
    assert isinstance(variadic, c_ast.EllipsisParam)
    assert not any(isinstance(node, c_ast.FuncDef) and node.decl.name == "ioctl"
                   for node in tree.ext)


def _assert_both(tree, target):
    _assert_interface(tree)
    _assert_layout(tree, target, "termio", LEGACY_FIELDS, 18, 2)
    _assert_layout(tree, target, "termios", LIBC_FIELDS, 60, 4)
    _assert_layout(tree, target, "pcc_kernel_termios_reference", KERNEL_FIELDS, 36, 4)
    typedefs = {node.name: node for node in tree.ext if isinstance(node, c_ast.Typedef)}
    for name in ("tcflag_t", "cc_t", "speed_t"):
        assert sum(isinstance(node, c_ast.Typedef) and node.name == name
                   for node in tree.ext) == 1
    assert typedefs["tcflag_t"].type.type.names == ["unsigned", "int"]
    assert typedefs["cc_t"].type.type.names == ["unsigned", "char"]
    assert typedefs["speed_t"].type.type.names == ["unsigned", "int"]
    # Linux's kernel TCGETS layout has c_cc[19] and no speed fields; it is
    # deliberately not the libc tcgetattr/tcsetattr declaration above.
    libc = _struct(tree, "termios")
    assert libc.decls[5].type.dim.value == "32"
    legacy = _struct(tree, "termio")
    for field in legacy.decls[:4]:
        assert field.type.type.names == ["unsigned", "short"]
    assert legacy.decls[4].type.type.names == ["unsigned", "char"]
    assert legacy.decls[5].type.type.type.names == ["unsigned", "char"]
    assert legacy.decls[5].type.dim.value == "8"
    for name in ("tcgetattr", "tcsetattr"):
        declarations = [node for node in tree.ext
                        if isinstance(node, c_ast.Decl) and node.name == name]
        assert len(declarations) == 1
        signature = declarations[0].type
        assert isinstance(signature, c_ast.FuncDecl)
        assert signature.type.type.names == ["int"]
        parameters = signature.args.params
        assert len(parameters) == (2 if name == "tcgetattr" else 3)
        for parameter in parameters[:-1]:
            assert parameter.type.type.names == ["int"]
        pointer = parameters[-1].type
        assert isinstance(pointer, c_ast.PtrDecl)
        assert isinstance(pointer.type.type, c_ast.Struct)
        assert pointer.type.type.name == "termios"
        assert pointer.type.quals == ([] if name == "tcgetattr" else ["const"])
        assert not any(isinstance(node, c_ast.FuncDef) and node.decl.name == name
                       for node in tree.ext)


@pytest.mark.parametrize("target", LINUX_TARGETS)
@pytest.mark.parametrize("order", INCLUDE_ORDERS, ids=lambda order: "-".join(order))
def test_termio_termios_ioctl_orders_and_repeated_includes(parser, target, order):
    source = "".join("#include <" + name + ">\n" for name in order + order)
    _assert_both(_parse(parser, source + BODY, target, CONSTANTS), target)


@pytest.mark.parametrize("target", LINUX_TARGETS)
def test_termio_alone_exposes_legacy_and_libc_interfaces(parser, target):
    _assert_both(_parse(parser, "#include <termio.h>\n" + BODY, target, CONSTANTS), target)


@pytest.mark.parametrize("target", LINUX_TARGETS)
def test_ioctl_alone_owns_legacy_struct_but_not_libc_termios(parser, target):
    tree = _parse(parser, "#include <sys/ioctl.h>\n#include <sys/ioctl.h>\n", target, LEGACY_CONSTANTS)
    _assert_interface(tree)
    _assert_layout(tree, target, "termio", LEGACY_FIELDS, 18, 2)
    assert not any(isinstance(node, c_ast.Decl) and isinstance(node.type, c_ast.Struct)
                   and node.type.name == "termios" for node in tree.ext)


@pytest.mark.parametrize("target", LINUX_TARGETS)
def test_readline_actual_termio_include_branch(parser, target):
    source = (ROOT / "projects/readline-8.2/rltty.h").read_text(encoding="utf-8")
    assert source.count(READLINE_INCLUDE) == 1
    text = "#define TERMIO_TTY_DRIVER 1\n" + READLINE_INCLUDE + "\n" + BODY
    _assert_both(_parse(parser, text, target, CONSTANTS), target)


@pytest.mark.parametrize("target", OTHER_TARGETS)
def test_termio_rejects_unsupported_target(target):
    with pytest.raises(RuntimeError, match="termio.h: unsupported target ABI"):
        preprocess("#include <termio.h>\n", target_triple=target)


@pytest.mark.parametrize("target", OTHER_TARGETS)
def test_non_linux_ioctl_does_not_acquire_linux_legacy_interface(parser, target):
    source = """
#include <termios.h>
#include <sys/ioctl.h>
#include <sys/ioctl.h>
#if defined(NCC) || defined(TCGETA) || defined(TCSETAW)
#error Linux legacy terminal interface leaked to another target
#endif
"""
    tree = _parse(parser, source, target, {})
    _assert_interface(tree)
    assert not any(isinstance(node, c_ast.Decl) and isinstance(node.type, c_ast.Struct)
                   and node.type.name == "termio" for node in tree.ext)


def test_missing_system_header_still_fails_closed():
    with pytest.raises(RuntimeError, match="owned preprocessor: header not found: pcc_missing_header.h"):
        preprocess("#include <pcc_missing_header.h>\n", target_triple=LINUX_TARGETS[0])
