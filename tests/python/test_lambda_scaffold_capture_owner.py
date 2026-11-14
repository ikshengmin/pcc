"""Lambda captures follow the non-emitting owner of provider attributes."""
from dataclasses import fields, is_dataclass

import pytest

from pcc.frontends.python.codegen.layer1 import L1CodeGen
from pcc.frontends.python.codegen.hoist_boxing import function_local_bindings
from pcc.frontends.python.py_ast import FuncDef, Lambda
from pcc.frontends.python.py_lift import parse_and_lift
from pcc.frontends.python.type_infer import infer_module


def nodes(value, kind):
    if isinstance(value, kind):
        yield value
    if isinstance(value, tuple):
        for item in value:
            yield from nodes(item, kind)
    elif is_dataclass(value):
        for field in fields(value):
            if field.name not in ("span", "ty", "annotation", "return_ty"):
                yield from nodes(getattr(value, field.name), kind)


def host_for(source, *, mode="on"):
    module = infer_module(parse_and_lift(source, "capture_owner.py", "capture_owner"))
    host = L1CodeGen(module, ir_scaffold_mode=mode)
    host._strict_no_libpython = True
    host._prefer_native_callable_values = True
    for statement in module.body:
        if type(statement).__name__ in ("Import", "ImportFrom"):
            host._prescan_nested_imports(statement)
    functions = list(nodes(module, FuncDef))
    host.current_func_def = functions[0] if functions else None
    return host, list(nodes(module, Lambda))


def free(host, expression):
    return host._lambda_free_vars(expression, [arg.name for arg in expression.params])


@pytest.mark.parametrize("member", ["Constant", "IntType", "Function", "values.Constant"])
def test_owned_symbol_attribute_does_not_capture_namespace(member):
    host, expressions = host_for(
        "from pcc.ir.compat import ir\ndef probe():\n    return lambda: ir." + member + "\n"
    )
    assert host._extern_bindings["ir"] == "ir"
    assert "ir" not in host.env and "ir" not in host._module_globals
    assert free(host, expressions[0]) == set()
    assert host._ir_scaffold_owns_symbol_value(expressions[0].body)
    before = str(host.module)
    assert free(host, expressions[0]) == set()
    assert str(host.module) == before


def test_provider_export_uses_the_same_owner_as_symbol_emission():
    host, expressions = host_for(
        "from pcc.ir.compat import ir\ndef probe():\n    return lambda: ir.Undefined\n"
    )
    host._native_module_exports = {"pcc.ir.ir": {"Undefined": {"kind": "module_global"}}}
    assert free(host, expressions[0]) == set()
    host._native_module_exports["pcc.ir.ir"]["Undefined"] = None
    assert free(host, expressions[0]) == {"ir"}


@pytest.mark.parametrize("source,mode", [
    ("from pcc.ir.compat import ir\ndef probe():\n    return lambda: ir\n", "on"),
    ("from pcc.ir.compat import ir\ndef probe():\n    return lambda: ir.missing\n", "on"),
    ("from pcc.ir import ir\ndef probe():\n    return lambda: ir.Constant\n", "off"),
    ("def probe():\n    return lambda: ir.Constant\n", "on"),
    ("from pcc.extern import ir\ndef probe():\n    return lambda: ir.Constant\n", "on"),
    ("from pcc.ir.compat import ir as other\ndef probe():\n    return lambda: other.Constant\n", "on"),
])
def test_unowned_namespace_remains_a_preflight_rejection(source, mode):
    host, expressions = host_for(source, mode=mode)
    assert free(host, expressions[0]) in ({"ir"}, {"other"})
    receipt = []
    before = str(host.module)
    assert host._maybe_emit_native_lambda_func(expressions[0], receipt) is None
    assert receipt == ["free-variable"]
    assert str(host.module) == before
    assert host._native_lambda_func_counter == 0


@pytest.mark.parametrize("binding", ["environment", "lexical-marker", "parameter", "later-local"])
def test_lexical_shadow_is_not_an_owned_namespace(binding):
    header = "def probe(ir):" if binding == "parameter" else "def probe():"
    tail = "    ir = None\n" if binding == "later-local" else ""
    host, expressions = host_for(
        "from pcc.ir.compat import ir\n" + header + "\n    callback = lambda: ir.Constant\n" + tail
    )
    if binding == "environment":
        host.env["ir"] = object()
    if binding == "lexical-marker":
        host._lambda_lexical_shadow_names = {"ir"}
    assert free(host, expressions[0]) == {"ir"}


@pytest.mark.parametrize("expression", [
    "lambda ir: ir.Constant",
    "lambda: (lambda ir: ir.Constant)",
    "lambda ir: (lambda: ir.Constant)",
])
def test_lambda_parameters_are_bound_before_provider_query(expression):
    host, expressions = host_for(
        "from pcc.ir.compat import ir\ndef probe():\n    return " + expression + "\n"
    )
    def forbidden(_expression):
        raise AssertionError("a lambda-bound receiver reached the provider owner")
    host._ir_scaffold_owns_symbol_value = forbidden
    assert free(host, expressions[0]) == set()


@pytest.mark.parametrize("expression,expected", [
    ("lambda value: ir.Constant(kind, value)", {"kind"}),
    ("lambda: ir.Constant(kind, make(value))", {"kind", "make", "value"}),
    ("lambda: (ir.Constant(kind, value), unknown.member)", {"kind", "value", "unknown"}),
])
def test_owned_callee_still_collects_every_argument_and_other_receiver(expression, expected):
    host, expressions = host_for(
        "from pcc.ir.compat import ir\ndef probe():\n    return " + expression + "\n"
    )
    assert free(host, expressions[0]) == expected


def test_real_callback_shape_keeps_self_cell_and_argument_effects():
    host, expressions = host_for('''from pcc.ir.compat import ir
WORD = ir.IntType(64)
class Forward:
    def callback(self, adapter):
        return lambda index, label: self.builder.call(
            self.runtime["target"], [adapter.args[1], ir.Constant(WORD, index)], name=label)
''')
    host.env = {"self": object(), "adapter": object()}
    host._module_globals["WORD"] = object()
    assert free(host, expressions[0]) == {"self", "adapter"}
    assert expressions[0].body.kwargs[0][0] == "name"


@pytest.mark.parametrize("source", [
    "from pcc.ir.compat import ir\ndef probe(value):\n    return lambda: ir.Constant(ir.IntType(64), value)\n",
    "from pcc.ir.compat import ir\ndef probe(kind):\n    return lambda value, label: (ir.Constant(kind, value), label)\n",
])
def test_real_native_lambda_body_codegen_reaches_owned_provider(source):
    module = infer_module(parse_and_lift(source, "capture_owner.py", "capture_owner"))
    host = L1CodeGen(module, ir_scaffold_mode="on")
    host._strict_no_libpython = True
    host._prefer_native_callable_values = True
    text = str(host.generate(module))
    assert "__native_lambda_" in text
    assert "user_pcc_ir_ir_scaffold_Constant" in text
    assert not any("call " in line and "@py_cpy_" in line for line in text.splitlines())


def test_metadata_only_local_import_still_shadows_module_provider():
    host, expressions = host_for('''from pcc.ir.compat import ir
def probe():
    callback = lambda: ir.Constant
    from pcc.extern import ir
    return callback
''')
    assert "ir" not in host.env
    assert free(host, expressions[0]) == {"ir"}
    receipt = []
    assert host._maybe_emit_native_lambda_func(expressions[0], receipt) is None
    assert receipt == ["free-variable"]


def test_comprehension_target_is_bound_before_provider_query():
    host, expressions = host_for('''from pcc.ir.compat import ir
def probe():
    return lambda: [ir.Constant for ir in values]
''')
    def forbidden(_expression):
        raise AssertionError("a comprehension-bound receiver reached the provider owner")
    host._ir_scaffold_owns_symbol_value = forbidden
    assert free(host, expressions[0]) == {"values"}


def test_global_declaration_retains_provider_mutation_guard():
    host, expressions = host_for('''from pcc.ir.compat import ir
def probe():
    global ir
    return lambda: ir.Constant
''')
    assert "ir" not in function_local_bindings(host.current_func_def)
    assert not host._ir_scaffold_owns_symbol_value(expressions[0].body)
    assert free(host, expressions[0]) == {"ir"}
