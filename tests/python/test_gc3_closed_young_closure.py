"""Source/typed receipt tests, not native Gateway or collector acceptance.

Contract: docs/project-intent.md, G-track section 12 (unchanged managed
semantics), and Python language reference sections 5 and 7.11 (imports).
"""

from copy import deepcopy
from dataclasses import replace
import hashlib
import json
from types import SimpleNamespace

import pytest

from pcc.frontends.python.closed_young_closure import (
    closed_young_typed_module_denial,
    closed_young_untyped_module_denial,
    load_closed_young_transport,
    prepare_closed_young_closure,
    prepare_closed_young_transport,
    validate_closed_young_closure,
    validate_closed_young_transport,
)
from pcc.frontends.python.closed_young_domain import closed_young_module_denial
from pcc.frontends.python.py_lift import parse_and_lift
from pcc.frontends.python.type_infer import infer_module


def _sources(tmp_path, entries):
    paths, names = [], []
    for name, relative, source in entries:
        path = tmp_path / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(source, encoding="utf-8")
        paths.append(str(path))
        names.append(name)
    return paths, names


def _pair(tmp_path, entry="from helper import value\nprint(value())\n"):
    return _sources(tmp_path, [
        ("main", "main.py", entry),
        ("helper", "helper.py", "def value():\n    return 'managed'\n"),
    ])


def _untyped(path, name):
    with open(path, encoding="utf-8") as source:
        return parse_and_lift(source.read(), path, name)


def _typed(path, name):
    return infer_module(_untyped(path, name))


def _resign(proof):
    unsigned = {key: value for key, value in proof.items() if key != "digest"}
    proof["digest"] = hashlib.sha256(json.dumps(
        unsigned, sort_keys=True, separators=(",", ":"), ensure_ascii=True,
        allow_nan=False,
    ).encode("utf-8")).hexdigest()


def test_managed_two_module_receipt_and_actual_typed_inputs(tmp_path):
    paths, names = _pair(tmp_path)
    proof, reason = prepare_closed_young_closure(paths, names, "main")
    assert reason == ""
    assert proof["schema"] == "pcc.closed_young_closure"
    assert proof["version"] == 2
    assert proof["entry_module"] == "main"
    assert len(proof["digest"]) == 64
    assert validate_closed_young_closure(proof, paths, names, "main") == ""
    # Canonical receipt ordering does not depend on scheduling order.
    reordered, reason = prepare_closed_young_closure(paths[::-1], names[::-1], "main")
    assert reason == ""
    assert reordered == proof
    for path, name in zip(paths, names):
        assert closed_young_untyped_module_denial(proof, _untyped(path, name), path) == ""
        assert closed_young_typed_module_denial(proof, _typed(path, name), path) == ""


def test_nested_package_relative_and_package_initializer_imports(tmp_path):
    paths, names = _sources(tmp_path, [
        ("main", "main.py", "from pkg.sub.worker import run\nprint(run())\n"),
        ("pkg", "pkg/__init__.py", "from .values import answer\n"),
        ("pkg.values", "pkg/values.py", "answer = 42\n"),
        ("pkg.sub", "pkg/sub/__init__.py", "pass\n"),
        ("pkg.sub.worker", "pkg/sub/worker.py",
         "from ..values import answer\nfrom . import helper\ndef run():\n    return answer\n"),
        ("pkg.sub.helper", "pkg/sub/helper.py", "value = 1\n"),
    ])
    proof, reason = prepare_closed_young_closure(paths, names, "main")
    assert reason == ""
    assert validate_closed_young_closure(proof, paths, names, "main") == ""
    for path, name in zip(paths, names):
        assert closed_young_untyped_module_denial(proof, _untyped(path, name), path) == ""
        assert closed_young_typed_module_denial(proof, _typed(path, name), path) == ""


def test_existing_single_source_import_denial_is_unchanged():
    module = parse_and_lift("import helper\n", "main.py", "main")
    assert "dependency-closure" in closed_young_module_denial(module)
    assert closed_young_module_denial(module, closure_modules=("main", "helper")) == ""
    assert "outside source closure" in closed_young_module_denial(module, closure_modules=("main",))


@pytest.mark.parametrize("entry,reason", [
    ("import missing\n", "outside source closure"),
    ("from helper import missing\n", "unresolved from-import"),
    ("from helper import *\n", "star import"),
    ("from .helper import value\n", "escapes its source package"),
    ("from helper import __dict__\n", "dynamic or foreign"),
    ("if False:\n    import pcc.unsafe\n", "raw compiler/runtime"),
    ("def f():\n    from pcc.extern import call\n", "raw compiler/runtime"),
    ("class C:\n    def f(self):\n        import pcc.arbitrary\n", "raw compiler/runtime"),
    ("try:\n    pass\nfinally:\n    import unknown\n", "outside source closure"),
    ("loader = __import__\n", "dynamic or foreign"),
    ("def f(x=eval('1')):\n    return x\n", "dynamic or foreign"),
    ("def f(x: __import__('unsafe')):\n    return x\n", "source closure could not be proven: ParseError"),
    ("x: __import__('unsafe') = 1\n", "source closure could not be proven: ParseError"),
    ("x = ().__class__\n", "reflective"),
    ("async def f():\n    return 1\n", "asynchronous"),
    ("@c_abi_export('entry')\ndef f():\n    return 0\n", "decorator"),
    ("__pcc_runtime_port__ = True\n", "raw runtime marker"),
    ("import helper\ndel helper.value\n", "provider deletion"),
])
def test_complete_source_rejects_hidden_or_unresolved_capabilities(tmp_path, entry, reason):
    paths, names = _pair(tmp_path, entry)
    proof, denial = prepare_closed_young_closure(paths, names, "main")
    assert proof is None
    assert reason in denial


def test_every_provider_is_checked_even_when_unused(tmp_path):
    paths, names = _sources(tmp_path, [
        ("main", "main.py", "print(1)\n"),
        ("helper", "helper.py", "if False:\n    import pcc.unsafe\n"),
    ])
    proof, reason = prepare_closed_young_closure(paths, names, "main")
    assert proof is None
    assert "raw compiler/runtime" in reason


def test_cyclic_partial_initialization_is_not_a_value_export_proof(tmp_path):
    paths, names = _sources(tmp_path, [
        ("main", "main.py", "from helper import value\nanswer = 1\n"),
        ("helper", "helper.py", "from main import answer\nvalue = 2\n"),
    ])
    proof, reason = prepare_closed_young_closure(paths, names, "main")
    assert proof is None
    assert "initialization-order proof" in reason



@pytest.mark.parametrize("entry_import", [
    "import pkg.child", "import pkg.child as child",
    "from pkg.child import value", "from pkg import child",
])
def test_implicit_parent_package_initialization_cycle_is_denied(tmp_path, entry_import):
    # Import machinery executes pkg before pkg.child. Its from-import sees
    # entry while entry.later is still unbound, even though every eventual
    # provider and binding appears in the source closure.
    paths, names = _sources(tmp_path, [
        ("entry", "entry.py", entry_import + "\nlater = 1\n"),
        ("pkg", "pkg/__init__.py", "from entry import later\n"),
        ("pkg.child", "pkg/child.py", "value = 1\n"),
    ])
    proof, reason = prepare_closed_young_closure(paths, names, "entry")
    assert proof is None
    assert "initialization-order proof" in reason


@pytest.mark.parametrize("parent", ["pkg", "pkg.sub"])
@pytest.mark.parametrize("entry_import", [
    "import pkg.sub.child", "from pkg.sub.child import value",
    "from pkg.sub import child",
])
def test_every_implicit_parent_package_has_an_initialization_edge(tmp_path, parent, entry_import):
    paths, names = _sources(tmp_path, [
        ("entry", "entry.py", entry_import + "\nlater = 1\n"),
        ("pkg", "pkg/__init__.py", "from entry import later\n" if parent == "pkg" else "pass\n"),
        ("pkg.sub", "pkg/sub/__init__.py",
         "from entry import later\n" if parent == "pkg.sub" else "pass\n"),
        ("pkg.sub.child", "pkg/sub/child.py", "value = 1\n"),
    ])
    proof, reason = prepare_closed_young_closure(paths, names, "entry")
    assert proof is None
    assert "initialization-order proof" in reason


@pytest.mark.parametrize("entry_import", [
    "from .pkg.child import value", "from .pkg import child",
])
def test_relative_import_parent_initialization_cycle_is_denied(tmp_path, entry_import):
    paths, names = _sources(tmp_path, [
        ("app", "app/__init__.py", "pass\n"),
        ("app.entry", "app/entry.py", entry_import + "\nlater = 1\n"),
        ("app.pkg", "app/pkg/__init__.py", "from ..entry import later\n"),
        ("app.pkg.child", "app/pkg/child.py", "value = 1\n"),
    ])
    proof, reason = prepare_closed_young_closure(paths, names, "app.entry")
    assert proof is None
    assert "initialization-order proof" in reason


@pytest.mark.parametrize("package_import", [
    "import pkg.child", "from .child import value",
])
def test_package_importing_own_child_has_no_implicit_self_cycle(tmp_path, package_import):
    paths, names = _sources(tmp_path, [
        ("entry", "entry.py", "import pkg.child\n"),
        ("pkg", "pkg/__init__.py", package_import + "\n"),
        ("pkg.child", "pkg/child.py", "value = 1\n"),
    ])
    proof, reason = prepare_closed_young_closure(paths, names, "entry")
    assert reason == ""
    assert validate_closed_young_closure(proof, paths, names, "entry") == ""


def test_arbitrary_pcc_namespace_cannot_be_certified(tmp_path):
    paths, names = _sources(tmp_path, [("pcc.innocent", "innocent.py", "value = 1\n")])
    proof, reason = prepare_closed_young_closure(paths, names, "pcc.innocent")
    assert proof is None
    assert "raw compiler/runtime" in reason


@pytest.mark.parametrize("provider", [
    "os", "time", "builtins", "typing", "dataclasses", "pytest", "llvmlite",
])
def test_supplied_source_does_not_certify_intercepted_native_provider(tmp_path, provider):
    paths, names = _sources(tmp_path, [
        ("main", "main.py", "import " + provider + "\nprint(1)\n"),
        (provider, provider + ".py", "value = 1\n"),
    ])
    proof, reason = prepare_closed_young_closure(paths, names, "main")
    assert proof is None
    assert "compiler-intercepted import provider" in reason


def test_rejection_tracks_actual_compiler_import_policy(tmp_path, monkeypatch):
    from pcc.frontends.python.codegen.import_lowering import ImportLoweringMixin

    paths, names = _pair(tmp_path)
    monkeypatch.setattr(
        ImportLoweringMixin, "_NATIVE_BUILTIN_IMPORT_MODULES",
        ImportLoweringMixin._NATIVE_BUILTIN_IMPORT_MODULES | {"helper"},
    )
    proof, reason = prepare_closed_young_closure(paths, names, "main")
    assert proof is None
    assert "compiler-intercepted import provider" in reason


def test_distinct_source_names_cannot_share_emitted_module_namespace(tmp_path):
    paths, names = _sources(tmp_path, [
        ("a", "a/__init__.py", "pass\n"),
        ("a.b", "a/b.py", "value = 1\n"),
        ("a_b", "a_b.py", "value = 2\n"),
    ])
    proof, reason = prepare_closed_young_closure(paths, names, "a_b")
    assert proof is None
    assert "duplicate emitted module namespace: a.b and a_b" in reason


def test_reconstruction_uses_actual_emitted_namespace_mapping(tmp_path, monkeypatch):
    from pcc.frontends.python.codegen import module_name_lowering

    paths, names = _pair(tmp_path)
    proof, reason = prepare_closed_young_closure(paths, names, "main")
    assert reason == ""
    monkeypatch.setattr(module_name_lowering, "module_symbol_suffix", lambda name: "collision")
    assert "duplicate emitted module namespace" in validate_closed_young_closure(
        proof, paths, names, "main",
    )


@pytest.mark.parametrize("suffix", [".so", ".dylib", ".pyd", ".dll", ".owned.so"])
def test_selected_extension_invalidates_supplied_source_receipt(tmp_path, monkeypatch, suffix):
    from pcc.frontends.python.codegen import import_lowering

    site = tmp_path / "site"
    site.mkdir()
    monkeypatch.setattr(import_lowering, "package_site_roots", lambda: [str(site)])
    paths, names = _pair(tmp_path, "import helper\nprint(1)\n")
    proof, reason = prepare_closed_young_closure(paths, names, "main")
    assert reason == ""
    typed = _typed(paths[0], "main")
    (site / ("helper" + suffix)).write_bytes(b"inert selection fixture; never loaded")
    replacement, reason = prepare_closed_young_closure(paths, names, "main")
    assert replacement is None
    assert "selected native extension provider" in reason
    assert "selected native extension provider" in validate_closed_young_closure(
        proof, paths, names, "main",
    )
    assert "selected native extension provider" in closed_young_typed_module_denial(
        proof, typed, paths[0],
    )


@pytest.mark.parametrize("with_python_submodule", [False, True])
@pytest.mark.parametrize("relative", [False, True])
def test_from_candidate_extension_precedes_values_and_python_submodules(
    tmp_path, monkeypatch, with_python_submodule, relative,
):
    from pcc.frontends.python.codegen import import_lowering

    site = tmp_path / "site"
    (site / "provider").mkdir(parents=True)
    monkeypatch.setattr(import_lowering, "package_site_roots", lambda: [str(site)])
    entry = "provider.worker" if relative else "main"
    entries = [
        (entry, "provider/worker.py" if relative else "main.py",
         "from . import value\nprint(1)\n" if relative else "from provider import value\nprint(1)\n"),
        ("provider", "provider/__init__.py", "value = 1\n"),
    ]
    if with_python_submodule:
        entries.append(("provider.value", "provider/value.py", "answer = 2\n"))
    paths, names = _sources(tmp_path, entries)
    proof, reason = prepare_closed_young_closure(paths, names, entry)
    assert reason == ""
    (site / "provider" / "value.so").write_bytes(b"inert selection fixture; never loaded")
    replacement, reason = prepare_closed_young_closure(paths, names, entry)
    assert replacement is None
    assert "selected native extension provider" in reason
    assert "provider.value" in reason
    assert "selected native extension provider" in validate_closed_young_closure(
        proof, paths, names, entry,
    )


@pytest.mark.parametrize("change,reason", [
    ("duplicate_name", "duplicate source module"),
    ("duplicate_path", "duplicate canonical source"),
    ("missing_entry", "entry module"),
    ("count", "count mismatch"),
    ("missing_source", "missing regular source"),
])
def test_source_inventory_is_exact(tmp_path, change, reason):
    paths, names = _pair(tmp_path)
    entry = "main"
    if change == "duplicate_name":
        names[1] = names[0]
    elif change == "duplicate_path":
        paths[1] = paths[0]
    elif change == "missing_entry":
        entry = "absent"
    elif change == "count":
        names.pop()
    elif change == "missing_source":
        paths[1] += ".missing.py"
    proof, denial = prepare_closed_young_closure(paths, names, entry)
    assert proof is None
    assert reason in denial


def test_package_requires_actual_parent_source_and_path(tmp_path):
    paths, names = _sources(tmp_path, [
        ("pkg.worker", "pkg/worker.py", "print(1)\n"),
    ])
    assert "missing source package" in prepare_closed_young_closure(paths, names, names[0])[1]
    init = tmp_path / "pkg" / "__init__.py"
    init.write_text("pass\n")
    paths.append(str(init))
    names.append("pkg")
    other = tmp_path / "other.py"
    other.write_text("print(1)\n")
    paths[0] = str(other)
    assert "path mismatch" in prepare_closed_young_closure(paths, names, names[0])[1]


def test_over_relative_import_never_becomes_absolute(tmp_path):
    paths, names = _sources(tmp_path, [
        ("pkg", "pkg/__init__.py", "from ..helper import value\n"),
        ("helper", "helper.py", "value = 1\n"),
    ])
    assert "escapes its source package" in prepare_closed_young_closure(paths, names, "pkg")[1]


def test_source_mutation_invalidates_receipt_and_typed_admission(tmp_path):
    paths, names = _pair(tmp_path)
    proof, reason = prepare_closed_young_closure(paths, names, "main")
    assert reason == ""
    typed = _typed(paths[0], "main")
    with open(paths[1], "a", encoding="utf-8") as source:
        source.write("# source identity changed without changing syntax\n")
    assert "does not match" in validate_closed_young_closure(proof, paths, names, "main")
    assert "does not match" in closed_young_typed_module_denial(proof, typed, paths[0])


def test_proof_digest_and_reconstruction_both_reject_tampering(tmp_path):
    paths, names = _pair(tmp_path)
    proof, reason = prepare_closed_young_closure(paths, names, "main")
    assert reason == ""
    altered = deepcopy(proof)
    altered["modules"][0]["sha256"] = "0" * 64
    assert "digest mismatch" in validate_closed_young_closure(altered, paths, names, "main")
    _resign(altered)
    assert "does not match" in validate_closed_young_closure(altered, paths, names, "main")
    assert validate_closed_young_closure(proof, paths[:1], names[:1], "main")
    assert validate_closed_young_closure(proof, paths, names, "helper")


@pytest.mark.parametrize("proof", [None, True, {}, {"approved": True}, {"modules": []}])
def test_malformed_proofs_are_denials_not_assertion_flags(tmp_path, proof):
    paths, names = _pair(tmp_path)
    assert validate_closed_young_closure(proof, paths, names, "main")
    assert closed_young_untyped_module_denial(proof, _untyped(paths[0], "main"), paths[0])
    assert closed_young_typed_module_denial(proof, _typed(paths[0], "main"), paths[0])


@pytest.mark.parametrize("change", ["argument", "return", "assignment", "is_method", "has_return_annotation"])
def test_untyped_receipt_binds_declared_annotations_and_function_flags(tmp_path, change):
    from pcc.frontends.python.py_ast import IntType

    paths, names = _sources(tmp_path, [
        ("main", "main.py", "from helper import value\nprint(value(1))\n"),
        ("helper", "helper.py", "def value(x: int) -> int:\n    result: int = x\n    return result\n"),
    ])
    proof, reason = prepare_closed_young_closure(paths, names, "main")
    assert reason == ""
    module = _untyped(paths[1], "helper")
    assert closed_young_untyped_module_denial(proof, module, paths[1]) == ""
    fn = module.body[0]
    if change == "argument":
        fn = replace(fn, args=(replace(fn.args[0], annotation=IntType("i64", 64, True)),))
    elif change == "return":
        fn = replace(fn, return_ty=IntType("i64", 64, True))
    elif change == "assignment":
        assignment = replace(fn.body[0], annotation=IntType("i64", 64, True))
        fn = replace(fn, body=(assignment,) + fn.body[1:])
    elif change == "is_method":
        fn = replace(fn, is_method=not fn.is_method)
    elif change == "has_return_annotation":
        fn = replace(fn, has_return_annotation=not fn.has_return_annotation)
    altered = replace(module, body=(fn,))
    assert "untyped syntax/declarations" in closed_young_untyped_module_denial(
        proof, altered, paths[1],
    )


def test_untyped_receipt_rechecks_manual_abi_and_raw_declared_types(tmp_path):
    from pcc.frontends.python.py_ast import RawPointerType

    paths, names = _pair(tmp_path)
    proof, reason = prepare_closed_young_closure(paths, names, "main")
    assert reason == ""
    module = _untyped(paths[1], "helper")
    fn = module.body[0]
    manual = replace(module, body=(replace(fn, manual_pointer_abi=True),))
    assert "manual pointer ABI" in closed_young_untyped_module_denial(proof, manual, paths[1])
    raw = replace(module, body=(replace(fn, return_ty=RawPointerType("ptr")),))
    assert "raw pointer" in closed_young_untyped_module_denial(proof, raw, paths[1])


@pytest.mark.parametrize("raw_type", [False, True])
def test_untyped_receipt_binds_lambda_body_type_preserved_by_inference(tmp_path, raw_type):
    from pcc.frontends.python.type_infer import TYPE_I64, TYPE_INT

    paths, names = _pair(tmp_path, "f = lambda: 'managed'\nprint(f())\n")
    proof, reason = prepare_closed_young_closure(paths, names, "main")
    assert reason == ""
    module = _untyped(paths[0], "main")
    assert closed_young_untyped_module_denial(proof, module, paths[0]) == ""
    assignment = module.body[0]
    expression = assignment.value
    replacement_type = TYPE_I64 if raw_type else TYPE_INT
    changed_body = replace(expression.body, ty=replacement_type)
    altered = replace(module, body=(
        replace(assignment, value=replace(expression, body=changed_body)),
    ) + module.body[1:])
    # This is why the pre-inference identity cannot discard expression types:
    # real inference changes the lambda's type but preserves its body metadata.
    inferred = infer_module(altered)
    assert inferred.body[0].value.body.ty == replacement_type
    denial = closed_young_untyped_module_denial(proof, altered, paths[0])
    assert denial
    if not raw_type:
        assert "untyped syntax/declarations" in denial


@pytest.mark.parametrize("source", [
    "print(True and (False and True and False))\n",
    "print(False or (True or False or True))\n",
    "print((True and False) or (False and (True or False)))\n",
    "print(1 and (2 and (3 and 4)))\n",
    "print(True and (\n    False and True and False\n))\n",
])
def test_typed_receipt_accepts_real_same_operator_boolean_rebalancing(tmp_path, source):
    paths, names = _pair(tmp_path, source)
    proof, reason = prepare_closed_young_closure(paths, names, "main")
    assert reason == ""
    module = _untyped(paths[0], "main")
    assert closed_young_untyped_module_denial(proof, module, paths[0]) == ""
    assert closed_young_typed_module_denial(proof, infer_module(module), paths[0]) == ""


@pytest.mark.parametrize("change", ["reorder", "substitute", "operator", "span"])
def test_boolean_canonicalization_preserves_order_leaves_operators_and_root_span(tmp_path, change):
    from pcc.frontends.python.py_ast import BoolExpr

    paths, names = _pair(tmp_path, "print(1 and (2 and (3 and 4)))\n")
    proof, reason = prepare_closed_young_closure(paths, names, "main")
    assert reason == ""
    typed = _typed(paths[0], "main")
    statement = typed.body[0]
    call = statement.expr
    boolean = call.args[0]
    if change == "reorder":
        boolean = replace(boolean, left=boolean.right, right=boolean.left)
    elif change == "operator":
        boolean = replace(boolean, op="or")
    elif change == "span":
        boolean = replace(boolean, span=replace(boolean.span, line=boolean.span.line + 1))
    else:
        def substitute_first_leaf(node):
            if isinstance(node, BoolExpr):
                return replace(node, left=substitute_first_leaf(node.left))
            return replace(node, value=99)
        boolean = substitute_first_leaf(boolean)
    altered = replace(typed, body=(replace(statement, expr=replace(call, args=(boolean,))),))
    assert "typed syntax" in closed_young_typed_module_denial(proof, altered, paths[0])


def test_boolean_rebalancing_does_not_relax_pre_inference_identity(tmp_path):
    from pcc.frontends.python.py_ast import BoolExpr

    paths, names = _pair(tmp_path, "print(1 and (2 and (3 and 4)))\n")
    proof, reason = prepare_closed_young_closure(paths, names, "main")
    assert reason == ""
    module = _untyped(paths[0], "main")
    statement = module.body[0]
    call = statement.expr
    boolean = call.args[0]
    leaves = []
    pending = [boolean]
    while pending:
        node = pending.pop()
        if isinstance(node, BoolExpr) and node.op == boolean.op:
            pending.extend((node.right, node.left))
        else:
            leaves.append(node)
    regrouped = leaves[0]
    for leaf in leaves[1:]:
        regrouped = replace(boolean, left=regrouped, right=leaf)
    altered = replace(module, body=(replace(statement, expr=replace(call, args=(regrouped,))),))
    assert "untyped syntax/declarations" in closed_young_untyped_module_denial(proof, altered, paths[0])


def test_untyped_digest_cannot_be_tampered_even_with_recomputed_proof_digest(tmp_path):
    paths, names = _pair(tmp_path)
    proof, reason = prepare_closed_young_closure(paths, names, "main")
    assert reason == ""
    proof["modules"][0]["untyped_sha256"] = "0" * 64
    _resign(proof)
    assert "does not match" in validate_closed_young_closure(proof, paths, names, "main")


def test_typed_module_substitution_and_raw_metadata_are_denied(tmp_path):
    from pcc.frontends.python.py_ast import RawPointerType

    paths, names = _pair(tmp_path)
    proof, reason = prepare_closed_young_closure(paths, names, "main")
    assert reason == ""
    typed = _typed(paths[1], "helper")
    assert closed_young_typed_module_denial(proof, typed, paths[1]) == ""
    substituted = infer_module(parse_and_lift("def value():\n    return 'different'\n", paths[1], "helper"))
    assert "typed syntax" in closed_young_typed_module_denial(proof, substituted, paths[1])
    assert "identity" in closed_young_typed_module_denial(proof, replace(typed, name="other"), paths[1])
    manual = replace(typed, body=(replace(typed.body[0], manual_pointer_abi=True),))
    assert "manual pointer ABI" in closed_young_typed_module_denial(proof, manual, paths[1])
    raw = replace(typed, body=(replace(typed.body[0], return_ty=RawPointerType("ptr")),))
    assert "raw pointer" in closed_young_typed_module_denial(proof, raw, paths[1])
    unknown = replace(typed, body=(SimpleNamespace(),))
    assert "unverified syntax" in closed_young_typed_module_denial(proof, unknown, paths[1])


def test_source_parse_failure_has_no_permissive_fallback(tmp_path, monkeypatch):
    from pcc.frontends.python import py_parse

    paths, names = _pair(tmp_path)

    def unavailable(*args, **kwargs):
        raise py_parse.ParseError("deliberately unavailable")

    monkeypatch.setattr(py_parse, "parse", unavailable)
    proof, reason = prepare_closed_young_closure(paths, names, "main")
    assert proof is None
    assert "ParseError" in reason


def _transport_fixture(tmp_path, *, indexed=False):
    from pcc.frontends.python import pipeline_ast_wire, pipeline_exports

    exports_path = tmp_path / "exports.wire"
    ast_dir = tmp_path / "asts"
    ast_dir.mkdir()
    native = {"main": {}, "helper": {}}
    options = {}
    if indexed:
        options = {
            "module_dependencies": {"main": ("helper",), "helper": ()},
            "unique_class_preload_index": {
                "types": (), "base_keys": (),
                "roots": {name: ((), ()) for name in native},
            },
        }
    pipeline_exports._write_native_exports_wire(str(exports_path), native, {}, **options)
    for index, name in enumerate(native):
        module = parse_and_lift("value = 1\n", str(tmp_path / (name + ".py")), name)
        pipeline_ast_wire._write_py_ast_wire(str(ast_dir / ("module_" + str(index) + ".json")), module)
    return str(exports_path), str(ast_dir)


@pytest.mark.parametrize("indexed", [False, True])
def test_transport_binds_production_wire_and_exact_assigned_ast_snapshots(tmp_path, indexed):
    from pcc.frontends.python import pipeline_ast_wire, pipeline_exports

    exports_path, ast_dir = _transport_fixture(tmp_path, indexed=indexed)
    token, reason = prepare_closed_young_transport(exports_path, ast_dir, [1, 0])
    assert reason == ""
    assert token["assigned_indices"] == [1, 0]
    assert [record["index"] for record in token["asts"]] == [1, 0]
    assert validate_closed_young_transport(token, exports_path, ast_dir, [1, 0]) == ""
    snapshots, reason = load_closed_young_transport(token, exports_path, ast_dir, [1, 0])
    assert reason == ""
    text, asts = snapshots
    assert pipeline_exports._decode_native_exports_wire(text) == ({"main": {}, "helper": {}}, {})
    assert pipeline_ast_wire._decode_py_ast_wire(asts[0]).name == "main"
    if indexed:
        native, _derived, preload, selected_indexed = pipeline_exports._decode_native_exports_wire_for_module(text, "main")
        assert set(native) == {"main", "helper"}
        assert preload is not None
        assert selected_indexed is True
    assert validate_closed_young_transport(token, exports_path, ast_dir, [0, 1])
    assert validate_closed_young_transport(token, exports_path, ast_dir, [0])


@pytest.mark.parametrize("tag", ["D", "F", "P", "T", "G", "C", "H", "U", "M"])
def test_transport_hashes_every_inline_indexed_member(tmp_path, tag):
    exports_path, ast_dir = _transport_fixture(tmp_path, indexed=True)
    token, reason = prepare_closed_young_transport(exports_path, ast_dir, [0])
    assert reason == ""
    with open(exports_path, encoding="utf-8") as stream:
        lines = stream.read().splitlines()
    for index, line in enumerate(lines):
        if line.startswith(tag + "\t"):
            lines[index] += " "  # Same JSON semantics, changed transport bytes.
            break
    else:
        pytest.fail("production writer omitted expected inline member " + tag)
    with open(exports_path, "w", encoding="utf-8") as stream:
        stream.write("\n".join(lines) + "\n")
    assert "does not match" in validate_closed_young_transport(token, exports_path, ast_dir, [0])


@pytest.mark.parametrize("target", ["exports", "ast"])
def test_transport_load_returns_hashed_bytes_not_reopened_paths(tmp_path, monkeypatch, target):
    from pcc.frontends.python import closed_young_closure

    exports_path, ast_dir = _transport_fixture(tmp_path)
    token, reason = prepare_closed_young_transport(exports_path, ast_dir, [0])
    assert reason == ""
    changed_path = exports_path if target == "exports" else str(tmp_path / "asts" / "module_0.json")
    with open(changed_path, encoding="utf-8") as stream:
        original_text = stream.read()
    read = closed_young_closure._transport_file

    def replace_after_read(path):
        record, contents = read(path)
        if str(path) == changed_path:
            with open(changed_path, "w", encoding="utf-8") as stream:
                stream.write(original_text + "\n")
        return record, contents

    monkeypatch.setattr(closed_young_closure, "_transport_file", replace_after_read)
    snapshots, reason = load_closed_young_transport(token, exports_path, ast_dir, [0])
    assert reason == ""
    assert (snapshots[0] if target == "exports" else snapshots[1][0]) == original_text
    assert validate_closed_young_transport(token, exports_path, ast_dir, [0])


def test_transport_denies_missing_artifacts_and_resigned_digest_tampering(tmp_path):
    exports_path, ast_dir = _transport_fixture(tmp_path)
    token, reason = prepare_closed_young_transport(exports_path, ast_dir, [0])
    assert reason == ""
    token["asts"][0]["sha256"] = "0" * 64
    assert "digest mismatch" in validate_closed_young_transport(token, exports_path, ast_dir, [0])
    _resign(token)
    assert "does not match" in validate_closed_young_transport(token, exports_path, ast_dir, [0])
    assert prepare_closed_young_transport(exports_path, ast_dir, [2])[0] is None
    assert prepare_closed_young_transport(exports_path + ".absent", ast_dir, [0])[0] is None


@pytest.mark.parametrize("indices", [[], [True], [-1], [0, 0], ["0"], None])
def test_transport_rejects_malformed_assignments(tmp_path, indices):
    exports_path, ast_dir = _transport_fixture(tmp_path)
    token, reason = prepare_closed_young_transport(exports_path, ast_dir, indices)
    assert token is None
    assert reason


@pytest.mark.parametrize("token", [None, True, {}, {"approved": True}])
def test_transport_rejects_malformed_tokens(tmp_path, token):
    exports_path, ast_dir = _transport_fixture(tmp_path)
    assert validate_closed_young_transport(token, exports_path, ast_dir, [0])
    snapshots, reason = load_closed_young_transport(token, exports_path, ast_dir, [0])
    assert snapshots is None
    assert reason


def test_transport_supports_source_rebuilt_and_exports_only_workers(tmp_path):
    exports_path, _ast_dir = _transport_fixture(tmp_path)
    token, reason = prepare_closed_young_transport("", "", [0])
    assert reason == ""
    assert load_closed_young_transport(token, "", "", [0]) == ((None, {}), "")
    token, reason = prepare_closed_young_transport(exports_path, "", [0])
    assert reason == ""
    snapshots, reason = load_closed_young_transport(token, exports_path, "", [0])
    assert reason == ""
    assert snapshots[0]
    assert snapshots[1] == {}
