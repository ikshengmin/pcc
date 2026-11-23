"""Scalar value constructors cross direct calls as initialized payloads."""
import os
import re
import subprocess

import pytest

from pcc.backend.owned_object_emit import emit_owned_object
from pcc.frontends.python.codegen.layer1 import L1CodeGen
from pcc.frontends.python.pipeline_context import build_closed_world_context
from pcc.frontends.python.type_infer import infer_module
from tests.python.test_shared_call_binding import _emit, _function
from tests.python.owned_regression_support import (
    assert_owned_program,
    explicit_owned_runtime,
)



def _body(text, symbol):
    match = re.search(r"(?ms)^define [^\n]*@" + symbol + r"\([^\n]*\n.*?^\}", text)
    assert match is not None, symbol
    return match.group(0)


def _generate_modules(tmp_path, monkeypatch, sources):
    monkeypatch.setenv("PCC_PYTHON_IR_PASSES", "off")
    monkeypatch.setenv("PCC_DIRECT_INDEXED_KERNEL_EMIT", "0")
    monkeypatch.setenv("PCC_DIRECT_INDEXED_KERNEL_CAPTURE", "0")
    paths = []
    for name, source in sources.items():
        path = tmp_path / (name.rsplit(".", 1)[-1] + ".py")
        path.write_text(source)
        paths.append(str(path))
    modules, exports, derived = build_closed_world_context(paths, list(sources))
    texts = {}
    codegens = {}
    for module in modules:
        typed = infer_module(module, external_exports=exports, derived_class_map=derived)
        codegen = L1CodeGen(typed, emit_cpy_main_exitcode=False, ir_scaffold_mode="on")
        codegen._native_module_exports = exports
        codegen._strict_no_libpython = True
        codegen._prefer_native_callable_values = True
        text = str(codegen.generate(typed))
        (tmp_path / (module.name + ".ll")).write_text(text)
        assert emit_owned_object(text, "x86_64-unknown-linux-gnu")[:4] == b"\x7fELF"
        texts[module.name] = text
        codegens[module.name] = codegen
    return exports, texts, codegens


@pytest.mark.parametrize("integer_type,lane", [("int", "ptr"), ("i64", "i64")])
def test_scalar_integer_contract_is_independent_of_aggregate_parameter(
    tmp_path, monkeypatch, integer_type, lane,
):
    """Inspect independent definitions and importing callers, including bignums."""
    sources = {
        "pcc.backend.records": """def valueclass(cls):
    return cls
@valueclass
class Triple:
    first: int
    second: int
    third: int
    def first_value(self) -> int:
        return self.first
""",
        "pcc.backend.provider": "from pcc import i64\n"
        "from pcc.backend.records import Triple as Span\n"
        f"def identity(span: Span, number: {integer_type}) -> {integer_type}:\n"
        "    return number\n",
        "pcc.backend.consumer": "from pcc import i64\n"
        "from pcc.backend.records import Triple as Span\n"
        "from pcc.backend.provider import identity\n"
        f"def forward(span: Span, number: {integer_type}) -> int:\n"
        "    return identity(span, number)\n"
        "def literal(span: Span) -> int:\n"
        "    return identity(span, "
        + ("1267650600228229401496703205376" if integer_type == "int" else "7") + ")\n",
    }
    exports, texts, _codegens = _generate_modules(tmp_path, monkeypatch, sources)
    info = exports["pcc.backend.provider"]["identity"]
    assert info["param_types"][0][:3] == ("valueclass", "Triple", "pcc.backend.records")
    aggregate = r"\{ i64, i64, i64 \}"
    definition = _body(texts["pcc.backend.provider"], "user_pcc_backend_provider_identity")
    assert re.match(rf"define external {lane} @[^\n]*\({aggregate} %span, {lane} %number\)", definition)
    caller = texts["pcc.backend.consumer"]
    assert re.search(rf"^declare external {lane} @user_pcc_backend_provider_identity\({aggregate}, {lane}\)", caller, re.M)
    for name in ("forward", "literal"):
        body = _body(caller, "user_pcc_backend_consumer_" + name)
        assert re.search(rf"call {lane} \({aggregate}, {lane}\) @user_pcc_backend_provider_identity\({aggregate}", body)
        assert "@py_instance_new" not in body
        assert "@py_valuebox_new" not in body
        assert "@py_int_to_i64_lane" not in body
        assert "@py_obj_call" not in body
    if integer_type == "int":
        assert info["box_int_abi"] is True
        assert "@py_int_from_cstr" in _body(caller, "user_pcc_backend_consumer_literal")


@pytest.mark.parametrize("nested", [False, True])
def test_fresh_scalar_payload_constructor_initializes_every_direct_argument_field(
    tmp_path, monkeypatch, nested,
):
    extra = "@valueclass\nclass Outer:\n    inner: Triple\n    last: int\n" if nested else ""
    target = "Outer" if nested else "Triple"
    value = "Outer(Triple(1, 2, 3), 4)" if nested else "Triple(1, 2, 3)"
    read = "span.inner.first + span.last" if nested else "span.first + span.third"
    sources = {
        "pcc.backend.records": "def valueclass(cls):\n    return cls\n"
        "@valueclass\nclass Triple:\n    first: int\n    second: int\n    third: int\n"
        "    def first_value(self) -> int:\n        return self.first\n" + extra,
        "pcc.backend.consumer": "from pcc.backend.records import Triple, " + target + " as Span\n"
        + ("from pcc.backend.records import Outer\n" if nested else "")
        + "def read(span: Span) -> int:\n    return " + read + "\n"
        + "def probe() -> int:\n    return read(" + value + ")\n",
    }
    _exports, texts, _codegens = _generate_modules(tmp_path, monkeypatch, sources)
    body = _body(texts["pcc.backend.consumer"], "user_pcc_backend_consumer_probe")
    assert "@py_instance_new" not in body, body
    assert "@py_valuebox_new" not in body, body
    assert "@py_valuebox_get_field" not in body, body
    assert "@py_obj_call" not in body, body
    for value in range(1, 5 if nested else 4):
        assert re.search(r"store i64 " + str(value) + r", ptr %value\.", body), body
    assert "@user_pcc_backend_consumer_read({ " in body, body


def test_methodless_valueclass_export_preserves_module_and_alias_identity(
    tmp_path, monkeypatch,
):
    sources = {
        "pcc.backend.left": "def valueclass(cls):\n    return cls\n"
        "@valueclass\nclass Pair:\n    first: int\n    second: int\n",
        "pcc.backend.right": "class Pair:\n    first: int\n    second: int\n",
        "pcc.backend.consumer": "from pcc.backend.left import Pair as Value\n"
        "from pcc.backend.right import Pair as Object\n"
        "def make() -> Value:\n    return Value(3, 4)\n",
    }
    exports, texts, codegens = _generate_modules(tmp_path, monkeypatch, sources)
    assert exports["pcc.backend.left"]["Pair"]["methods"] == ()
    assert exports["pcc.backend.left"]["Pair"]["valueclass"] is True
    assert exports["pcc.backend.right"]["Pair"]["valueclass"] is False
    lowering = codegens["pcc.backend.consumer"].class_lowering
    for alias, owner, is_value in (("Value", "left", True), ("Object", "right", False)):
        info = lowering.classes[alias]
        assert info.valueclass is is_value
        assert info.owning_module == "pcc.backend." + owner
        assert info.export_class_name == "Pair"
        assert info.global_var.name == ".class.pcc_backend_" + owner + ".Pair"
        repeated = lowering.declare_extern_class(
            info.owning_module, "Pair", tuple(info.field_names), (),
            local_name=alias, field_types=(),
        )
        assert repeated is info
        assert repeated.valueclass is is_value
    body = _body(texts["pcc.backend.consumer"], "user_pcc_backend_consumer_make")
    assert "@py_instance_new" not in body
    assert "@py_valuebox_new" not in body


@pytest.mark.integration
def test_original_imported_valueclass_argument_program_native(
    tmp_path, pcc_runtime_archive,
):
    from pcc.frontends.python.pipeline import compile_python_multi
    from pcc.frontends.python.pipeline_targets import host_target_triple
    from tests.python.test_cross_module_valueclass_abi import (
        test_imported_valueclass_parameter_keeps_aggregate_abi,
    )

    # Reuse the original causal program byte for byte, then add an execution
    # harness. The original helper also checks its direct aggregate method ABI.
    test_imported_valueclass_parameter_keeps_aggregate_abi(tmp_path)
    consumer = tmp_path / "consumer.py"
    original = consumer.read_text()
    consumer.write_text(original + """
from pcc.extern import extern, c_int64
actual_gc_backend = extern('pcc_gc_backend', (), c_int64)

def preserve_integer(span: Span, value: int) -> int:
    return value

def main():
    assert use_span() == 5
    assert preserve_integer(Span(1, 2, 3), 1267650600228229401496703205376) == 1267650600228229401496703205376
    print('VALUECLASS_ARGUMENT_ABI_OK')
    print(actual_gc_backend())
main()
""")
    assert consumer.read_text().startswith(original)
    executable = tmp_path / "valueclass_argument"
    compile_python_multi(
        [str(consumer), str(tmp_path / "records.py")], str(executable),
        entry_module="pcc.backend.consumer",
        module_names=["pcc.backend.consumer", "pcc.backend.records"],
        backend="self", ir_scaffold_mode="on",
        recursive_stdlib=False, target_triple=host_target_triple(),
        runtime_archive=str(pcc_runtime_archive),
    )
    for backend in (0,):
        ran = subprocess.run(
            [str(executable)], text=True, capture_output=True, timeout=30,
            env=dict(os.environ, PATH="/nonexistent", PCC_GC_BACKEND=str(backend)),
        )
        (tmp_path / f"gc{backend}.stdout").write_text(ran.stdout)
        (tmp_path / f"gc{backend}.stderr").write_text(ran.stderr)
        assert ran.returncode == 0, (backend, ran.stdout, ran.stderr)
        assert ran.stdout == f"VALUECLASS_ARGUMENT_ABI_OK\n{backend}\n"
        assert ran.stderr == ""



CONDITIONAL_PREFIX = '''import pcc

@pcc.valueclass
class CompilerInt2:
    first: int
    second: int
'''


def _initializer(expression, extra=""):
    return CONDITIONAL_PREFIX + extra + '''
class Fragments:
    def new_fragment(self) -> CompilerInt2:
        return CompilerInt2(7, 9)
    def close(self):
        pass
class Emission:
    def __init__(self, fragments: Fragments, optimize: bool):
        self.fragments: Fragments = fragments
        try:
            self.module_span: CompilerInt2 = ''' + expression + '''
        except Exception:
            self.close()
            raise
    def close(self):
        self.fragments.close()
'''


@pytest.mark.parametrize("expression", [
    "self.fragments.new_fragment() if optimize else CompilerInt2(-1, 0)",
    "CompilerInt2(-1, 0) if optimize else self.fragments.new_fragment()",
    "self.fragments.new_fragment() if True else CompilerInt2(-1, 0)",
    "CompilerInt2(-1, 0) if False else self.fragments.new_fragment()",
    "(self.fragments.new_fragment() if optimize else CompilerInt2(3, 4)) if optimize else CompilerInt2(-1, 0)",
])
def test_original_initializer_shape_projects_selected_boxed_branch(expression):
    from pcc.frontends.python.py_lift import parse_and_lift
    from pcc.frontends.python.py_ast import ClassDef, FuncDef, Try

    source = _initializer(expression)
    typed = infer_module(parse_and_lift(source, "binding.py", "binding"))
    owner = next(node for node in typed.body if isinstance(node, ClassDef) and node.name == "Emission")
    init = next(node for node in owner.body if isinstance(node, FuncDef) and node.name == "__init__")
    protected = next(node for node in init.body if isinstance(node, Try))
    assert protected.body[0].value.ty.name == "CompilerInt2"
    body = _function(_emit(source), "user_binding_Emission___init__")
    assert "@py_valuebox_get_field(" in body
    assert "@py_obj_isinstance(" in body
    assert "@pcc_gc_foreign_lease_acquire(" in body
    assert "@pcc_gc_foreign_lease_release(" in body
    assert "value.unbox.source" in body


@pytest.mark.parametrize("expression", [
    "CompilerInt2(1, 2) if optimize else CompilerInt2(3, 4)",
    "(CompilerInt2(1, 2) if optimize else CompilerInt2(3, 4)) if optimize else CompilerInt2(5, 6)",
])
def test_inline_scalar_branches_are_not_boxed_for_the_join(expression):
    body = _function(_emit(_initializer(expression)), "user_binding_Emission___init__")
    # The final object attribute store may box once; the branch join must not
    # round-trip an already-native aggregate through object field projection.
    assert "@py_valuebox_get_field(" not in body
    assert "phi {i64, i64}" in body or "phi { i64, i64 }" in body


CONDITIONAL_PROGRAM = CONDITIONAL_PREFIX + '''
import gc

events = []
class Fragments:
    def new_fragment(self) -> CompilerInt2:
        events.append('produce')
        return CompilerInt2(7, 9)
    def close(self):
        events.append('close')
class Emission:
    def __init__(self, fragments: Fragments, optimize: bool):
        self.fragments: Fragments = fragments
        try:
            self.module_span: CompilerInt2 = self.fragments.new_fragment() if optimize else CompilerInt2(-1, 0)
            # CPython models the explicit value projection's checked boundary.
            if not isinstance(self.module_span, CompilerInt2):
                raise TypeError('wrong boxed valueclass')
        except Exception:
            self.close()
            raise
    def close(self):
        self.fragments.close()
class Broken(Fragments):
    def new_fragment(self) -> CompilerInt2:
        events.append('fail')
        raise ValueError('producer failed')
    def close(self):
        events.append('close')
@pcc.valueclass
class Other:
    first: int
    second: int
class Wrong(Fragments):
    def new_fragment(self):
        events.append('wrong')
        return Other(1, 2)
class WrongScalar(Fragments):
    def new_fragment(self):
        events.append('scalar')
        return 7
@pcc.valueclass
class Managed:
    values: list[int]
    flag: int
class ManagedSource:
    def make(self) -> Managed:
        events.append('managed')
        return Managed([11], 13)
class ManagedHolder:
    def __init__(self, source: ManagedSource, choose: bool):
        self.value: Managed = source.make() if choose else Managed([17], 19)
def main():
    first = Emission(Fragments(), True)
    second = Emission(Fragments(), False)
    assert first.module_span.first == 7
    assert first.module_span.second == 9
    assert second.module_span.first == -1
    assert second.module_span.second == 0
    assert events == ['produce']
    try:
        Emission(Broken(), True)
    except ValueError:
        pass
    else:
        raise AssertionError('producer exception lost')
    assert events == ['produce', 'fail', 'close']
    Emission(Broken(), False)
    assert events == ['produce', 'fail', 'close']
    try:
        Emission(Wrong(), True)
    except TypeError:
        pass
    else:
        raise AssertionError('different valueclass accepted')
    try:
        Emission(WrongScalar(), True)
    except TypeError:
        pass
    else:
        raise AssertionError('non-valueclass accepted')
    assert events == ['produce', 'fail', 'close', 'wrong', 'close', 'scalar', 'close']
    managed = ManagedHolder(ManagedSource(), True)
    fallback = ManagedHolder(ManagedSource(), False)
    gc.collect()
    assert managed.value.values == [11]
    assert fallback.value.values == [17]
    assert events == ['produce', 'fail', 'close', 'wrong', 'close', 'scalar', 'close', 'managed']
    print('CONDITIONAL_PROJECTION_OK')
main()
'''


def test_scalar_and_managed_original_shapes_lower_without_foreign_fallback():
    text = _emit(CONDITIONAL_PROGRAM)
    assert "strict.nolib.stub" not in text


@pytest.mark.parametrize("branch_kind", ["different", "unknown", "raw"])
def test_nonmatching_branch_preserves_original_expression_route(branch_kind):
    from types import SimpleNamespace
    from pcc.frontends.python.codegen.control_flow_lowering import (
        _emit_scalar_valueclass_conditional_branch,
    )

    target = SimpleNamespace(name="CompilerInt2")
    other = SimpleNamespace(name="Other" if branch_kind == "different" else branch_kind)
    branch = SimpleNamespace(ty=other)
    calls = []
    class Host:
        def _is_valueclass_payload_type(self, ty):
            return ty.name in ("CompilerInt2", "Other")
        def _emit_expr(self, value):
            calls.append(value)
            return "original"
        def _emit_valueclass_payload_expr(self, value, ty):
            raise AssertionError("incompatible branch entered target constructor")
    assert _emit_scalar_valueclass_conditional_branch(Host(), branch, target) == "original"
    assert calls == [branch]


@pytest.mark.integration
class TestConditionalProjectionNative:
    @pytest.fixture(autouse=True)
    def _explicit_inputs_before_compiler(self, explicit_owned_runtime, monkeypatch):
        # Establish explicit inputs before pytest resolves either compiler.
        # This prerequisite applies only to this integration class.
        monkeypatch.setenv("PCC_TEST_COMPILER_STRICT", "1")

    @pytest.mark.parametrize("python_program_compiler", ["pcc0", "pcc1"], indirect=True)
    def test_conditional_projection_branch_selection_and_errors_on_five_collectors(
        self, tmp_path, python_program_compiler, explicit_owned_runtime, capfd, request,
    ):
        assert_owned_program(
            CONDITIONAL_PROGRAM, "CONDITIONAL_PROJECTION_OK\n", tmp_path,
            python_program_compiler,
            request.node.callspec.params["python_program_compiler"],
            explicit_owned_runtime, capfd,
        )
