"""Qualified builtin open keeps binding identity and publishes owned files."""
from __future__ import annotations

import re
import pytest
from pcc.frontends.python.codegen.layer1 import L1CodeGen
from pcc.frontends.python.codegen.errors import L1CodegenError
from pcc.frontends.python.py_lift import parse_and_lift
from pcc.frontends.python.type_infer import infer_module
from tests.owned_ir_validation import verify_ir_text


def _emit(source, strict=True):
    module = infer_module(parse_and_lift(source, "owned_open.py", "owned_open"))
    codegen = L1CodeGen(module, ir_scaffold_mode="on")
    codegen._strict_no_libpython = strict
    codegen._prefer_native_callable_values = True
    text = str(codegen.generate(module))
    verify_ir_text(text)
    return text


def _body(text, name="read_file"):
    match = re.search(r"(?ms)^define[^\n]*@user_owned_open_" + name + r"\([^\n]*\).*?^}", text)
    assert match is not None
    return match.group(0)


@pytest.mark.parametrize("binding", ("import builtins", "import builtins as io"))
def test_qualified_builtin_open_read_stays_native(binding):
    alias = "io" if " as " in binding else "builtins"
    text = _emit(binding + "\ndef read_file(path):\n"
        "    with " + alias + ".open(path, 'rb') as source:\n"
        "        payload = source.read(128)\n"
        "    return bytes(payload)\n")
    body = _body(text)
    assert "@py_file_open(" in body
    assert "@py_file_read(" in body
    assert "@py_file_close(" in body
    assert "@py_cpy_" not in body
    assert "strict.nolib.stub" not in body


def test_qualified_open_ignores_unrelated_local_open_binding():
    text = _emit("import builtins\ndef read_file(open, path):\n"
        "    with builtins.open(path, 'rb') as source:\n"
        "        return source.read()\n")
    body = _body(text)
    assert "@py_file_open(" in body
    assert "@py_file_close(" in body
    assert "@py_cpy_" not in body


@pytest.mark.parametrize("name", ("open", "builtins"))
def test_shadowed_callable_or_module_uses_actual_parameter(name):
    call = "open(path)" if name == "open" else "builtins.open(path)"
    text = _emit("import builtins\ndef read_file(" + name + ", path):\n"
                 "    return " + call + "\n")
    body = _body(text)
    assert "@py_file_open(" not in body
    assert "@py_cpy_" not in body
    assert "@py_obj_call" in body


def test_open_publishes_before_leases_tls_or_operand_disposal():
    text = _emit("import builtins\ndef path_value():\n    return 'x'\n"
        "def mode_value():\n    return 'rb'\n"
        "def consume(*, value):\n    return value\n"
        "def read_file():\n"
        "    return consume(value=builtins.open(path_value(), mode_value()))\n")
    body = _body(text)
    match = re.search(r"(?P<value>%[^ ]+) = call [^\n]*@py_file_open\([^\n]*\)\n(?P<next>[^\n]+)", body)
    assert match is not None
    assert match.group("next").strip().startswith("store ptr " + match.group("value") + ", ptr ")
    assert body.index("@user_owned_open_path_value(") < body.index("@user_owned_open_mode_value(") < match.start()
    assert "@pcc_gc_foreign_lease_acquire(" in body
    assert "@py_tls_exc_swap_slot(" in body


@pytest.mark.parametrize("exit_stmt", ("return source.read()", "raise ValueError('bad')", "source = None\n        return b'ok'"))
def test_native_file_with_closes_on_non_fallthrough_exit(exit_stmt):
    text = _emit("import builtins\ndef read_file(path):\n"
        "    with builtins.open(path, 'rb') as source:\n        " + exit_stmt + "\n")
    body = _body(text)
    assert "@py_file_close(" in body
    assert "with.context" in body
    assert "with.err" in body
    assert "@py_cpy_" not in body


def test_known_builtin_open_mutation_is_explicitly_unsupported():
    with pytest.raises(L1CodegenError, match="unmodified builtin namespace"):
        _emit("import builtins\ndef replacement(path):\n    return path\n"
              "builtins.open = replacement\n"
              "def read_file(path):\n    return builtins.open(path)\n")


def test_unsupported_open_options_keep_foreign_dispatch_explicit():
    text = _emit("import builtins\ndef read_file(path, encoding):\n"
        "    return builtins.open(path, 'r', encoding=encoding)\n", strict=False)
    body = _body(text)
    assert "@py_file_open(" not in body
    assert "@py_cpy_" in body


@pytest.mark.parametrize("mutation", (
    "builtins.open = replacement",
    "setattr(builtins, 'open', replacement)",
    "alias = builtins\n    alias.open = replacement",
))
def test_later_function_mutation_or_namespace_escape_is_explicit(mutation):
    with pytest.raises(L1CodegenError, match="unmodified builtin namespace"):
        _emit("import builtins\ndef replacement(path):\n    return path\n"
              "def read_file(path):\n    return builtins.open(path)\n"
              "def mutate():\n    " + mutation + "\n")


def test_unrelated_parameter_namespace_mutation_does_not_mask_builtin():
    text = _emit("import builtins\n"
        "def mutate(builtins, replacement):\n    builtins.open = replacement\n"
        "def read_file(path):\n    return builtins.open(path)\n")
    assert "@py_file_open(" in _body(text)


def test_rebound_module_alias_cannot_use_original_builtin_intrinsic():
    text = _emit("import builtins as io\n"
        "def read_file(io, path):\n    return io.open(path)\n")
    body = _body(text)
    assert "@py_file_open(" not in body
    assert "@py_obj_call" in body


def test_imported_module_alias_rebinding_uses_new_local_value():
    text = _emit("def read_file(target, path):\n"
        "    import builtins as io\n"
        "    io = target\n"
        "    return io.open(path)\n")
    body = _body(text)
    assert "@py_file_open(" not in body
    assert "@py_obj_call" in body


def test_late_global_alias_rebinding_is_explicitly_unsupported():
    with pytest.raises(L1CodegenError, match="unmodified builtin namespace"):
        _emit("import builtins as io\n"
              "def read_file(path):\n    return io.open(path)\n"
              "io = None\n")
