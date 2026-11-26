from __future__ import annotations

import re
import subprocess
import sys

import pytest


def _sources(tmp_path, binding, *, override=False):
    owner = tmp_path / "owner.py"
    owner.write_text(
        "class Parent:\n    def ping(self) -> int:\n        return 7\n"
        "    def label(self) -> str:\n        return 'parent'\n"
        "class Duck:\n    def ping(self) -> int:\n        return 42\n"
        "    def label(self) -> str:\n        return 'duck'\n"
        + ("class Child(Parent):\n    def ping(self) -> int:\n        return 19\n"
           "def descriptor_ping():\n    return 31\n"
           "class Descriptor(Parent):\n"
           "    @property\n    def ping(self):\n        return descriptor_ping\n"
           if override else ""),
        encoding="utf-8",
    )
    imports, annotation = {
        "unique": ("", "Parent"),
        "imported": ("from owner import Parent\n", "Parent"),
        "qualified": ("import owner as model\n", "model.Parent"),
    }[binding]
    user = tmp_path / "user.py"
    user.write_text(
        "from __future__ import annotations\n" + imports
        + "def run(parent: " + annotation + ") -> int:\n    return parent.ping()\n"
        + "def label(parent: " + annotation + ") -> str:\n    return parent.label()\n",
        encoding="utf-8",
    )
    return user, owner


def _body(ir_text, name):
    match = re.search(
        r"define[^\n]+@" + re.escape(name) + r"[^{]+\{(.+?)\n\}",
        ir_text,
        re.DOTALL,
    )
    assert match is not None, ir_text[:500]
    return match.group(1)


@pytest.mark.parametrize("binding", ("unique", "imported", "qualified"))
def test_unique_external_class_annotation_dispatches_method_natively(tmp_path, binding):
    from pcc.frontends.python.pipeline import compile_python_multi
    from pcc.frontends.python.pipeline_context import build_closed_world_context
    from pcc.frontends.python.type_infer import infer_module

    user, owner = _sources(tmp_path, binding)
    paths = [str(user), str(owner)]
    names = ["user", "owner"]
    parsed, exports, derived = build_closed_world_context(paths, names)
    typed = infer_module(parsed[0], external_exports={"owner": exports["owner"]},
                         derived_class_map=derived)
    run = next(stmt for stmt in typed.body if getattr(stmt, "name", None) == "run")
    assert (run.args[0].annotation.module, run.args[0].annotation.name) == ("owner", "Parent")
    out = tmp_path / "combined.ll"
    compile_python_multi(paths, str(out), emit_llvm_only=True,
                         entry_module="user", module_names=names)
    ir_text = out.read_text(encoding="utf-8")
    # The type identity survives; a Python annotation is NOT an exact live
    # receiver proof. A caller can pass a dynamically obtained unrelated
    # implementation or None. Native lookup, not CPython or a fixed body.
    for name, method in (("run", "ping"), ("label", "label")):
        body = _body(ir_text, "user_user_" + name)
        assert "user_owner_Parent_" + method not in body
        assert re.search(r"@py_obj_(?:load_method|getattr)", body)
        assert re.search(r"@py_obj_call", body)
        assert "py_cpy_getattr" not in body
        assert "py_cpy_call" not in body


def test_duplicate_unqualified_annotation_does_not_choose_an_owner(tmp_path):
    from pcc.frontends.python.pipeline import compile_python_multi
    from pcc.frontends.python.pipeline_context import build_closed_world_context
    from pcc.frontends.python.type_infer import infer_module

    user, owner = _sources(tmp_path, "unique")
    duplicate = tmp_path / "other.py"
    duplicate.write_text("class Parent:\n    def ping(self) -> int:\n        return 99\n",
                         encoding="utf-8")
    paths = [str(user), str(owner), str(duplicate)]
    names = ["user", "owner", "other"]
    parsed, exports, derived = build_closed_world_context(paths, names)
    typed = infer_module(parsed[0], external_exports={
        "owner": exports["owner"], "other": exports["other"],
    }, derived_class_map=derived)
    run = next(stmt for stmt in typed.body if getattr(stmt, "name", None) == "run")
    assert run.args[0].annotation.module == ""
    out = tmp_path / "combined.ll"
    compile_python_multi(paths, str(out), emit_llvm_only=True,
                         entry_module="user", module_names=names)
    body = _body(out.read_text(encoding="utf-8"), "user_user_run")
    assert "user_owner_Parent_ping" not in body
    assert "user_other_Parent_ping" not in body
    assert re.search(r"@py_obj_(?:load_method|getattr)", body)
    assert re.search(r"@py_obj_call", body)
    assert "py_cpy_getattr" not in body and "py_cpy_call" not in body


def test_concrete_external_constructor_keeps_native_method_body(tmp_path):
    from pcc.frontends.python.pipeline import compile_python_multi

    user, owner = _sources(tmp_path, "imported")
    with user.open("a", encoding="utf-8") as stream:
        stream.write("def concrete() -> int:\n    return Parent().ping()\n")
    out = tmp_path / "combined.ll"
    compile_python_multi([str(user), str(owner)], str(out), emit_llvm_only=True,
                         entry_module="user", module_names=["user", "owner"])
    body = _body(out.read_text(encoding="utf-8"), "user_user_concrete")
    assert "user_owner_Parent_ping" in body
    assert "py_cpy_getattr" not in body and "py_cpy_call" not in body


@pytest.mark.integration
@pytest.mark.parametrize("binding", ("unique", "imported", "qualified"))
def test_external_annotation_is_not_runtime_receiver_proof(
    tmp_path, threaded_pcc_runtime_archive, binding,
):
    from pcc.frontends.python.pipeline import compile_python_multi

    user, owner = _sources(tmp_path, binding)
    direct_import = ""
    direct_call = ""
    direct_expected = b""
    if binding == "imported":
        # Exercise the separately proved exact constructor directly in the
        # real executable, not just the host IR model.
        with user.open("a", encoding="utf-8") as stream:
            stream.write("def concrete() -> int:\n    return Parent().ping()\n")
        direct_import = "from user import concrete\n"
        direct_call = "print(concrete())\n"
        direct_expected = b"7\n"
    main = tmp_path / "main.py"
    main.write_text(
        direct_import + "from user import run, label\nfrom owner import Parent, Duck\n"
        "def opaque(obj):\n    return obj\n"
        "print(run(opaque(Parent())))\n"
        "print(run(opaque(Duck())))\n"
        "print(label(opaque(Parent())))\n"
        "print(label(opaque(Duck())))\n"
        "try:\n    print(run(opaque(None)))\n"
        "except AttributeError:\n    print('attribute-error')\n" + direct_call,
        encoding="utf-8",
    )
    expected = b"7\n42\nparent\nduck\nattribute-error\n" + direct_expected
    reference = subprocess.run([sys.executable, str(main)], capture_output=True, timeout=10)
    assert (reference.returncode, reference.stdout, reference.stderr) == (0, expected, b"")
    native = tmp_path / "native"
    compile_python_multi([str(main), str(user), str(owner)], str(native),
                         entry_module="main", module_names=["main", "user", "owner"],
                         backend="self", ir_scaffold_mode="on",
                         runtime_archive=str(threaded_pcc_runtime_archive))
    actual = subprocess.run([str(native)], capture_output=True, timeout=10)
    assert (actual.returncode, actual.stdout, actual.stderr) == (0, expected, b"")


@pytest.mark.integration
def test_external_annotation_keeps_live_override_and_mutation(
    tmp_path, threaded_pcc_runtime_archive,
):
    from pcc.frontends.python.pipeline import compile_python_multi

    user, owner = _sources(tmp_path, "imported", override=True)
    main = tmp_path / "main.py"
    main.write_text(
        "from user import run\nfrom owner import Parent, Child, Descriptor\n"
        "p = Parent()\nc = Child()\nd = Descriptor()\n"
        "print(run(p))\nprint(run(c))\nprint(run(d))\n"
        "def replacement(self):\n    return 27\n"
        "Parent.ping = replacement\nprint(run(p))\nprint(run(c))\nprint(run(d))\n",
        encoding="utf-8",
    )
    expected = b"7\n19\n31\n27\n19\n31\n"
    reference = subprocess.run([sys.executable, str(main)], capture_output=True, timeout=10)
    assert (reference.returncode, reference.stdout, reference.stderr) == (0, expected, b"")
    native = tmp_path / "native"
    compile_python_multi([str(main), str(user), str(owner)], str(native),
                         entry_module="main", module_names=["main", "user", "owner"],
                         backend="self", ir_scaffold_mode="on",
                         runtime_archive=str(threaded_pcc_runtime_archive))
    actual = subprocess.run([str(native)], capture_output=True, timeout=10)
    assert (actual.returncode, actual.stdout, actual.stderr) == (0, expected, b"")


@pytest.mark.parametrize("annotation", ("Parent", "Parent | None"))
def test_external_void_method_retains_independent_direct_abi(tmp_path, annotation):
    """A live method guard keeps the direct ABI, never annotation alone."""
    from pcc.frontends.python.pipeline import compile_python_multi

    owner = tmp_path / "owner.py"
    owner.write_text(
        "class Parent:\n    def touch(self) -> None:\n        print('touch')\n",
        encoding="utf-8",
    )
    user = tmp_path / "user.py"
    user.write_text(
        "from owner import Parent\ndef run(parent: " + annotation + ") -> None:\n"
        + ("    if parent is None:\n        return\n" if "None" in annotation else "")
        + "    parent.touch()\n",
        encoding="utf-8",
    )
    out = tmp_path / "combined.ll"
    compile_python_multi([str(user), str(owner)], str(out), emit_llvm_only=True,
                         entry_module="user", module_names=["user", "owner"])
    body = _body(out.read_text(encoding="utf-8"), "user_user_run")
    assert "user_owner_Parent_touch" in body
    assert "@user_owner_Parent_touch_method_native_adapter" in body
    assert "@py_obj_load_method" in body
    assert "@py_func_slot_has_native_entry" in body
    assert "@py_obj_call_loaded_method" in body
    assert "py_cpy_getattr" not in body and "py_cpy_call" not in body


_VOID_OWNER = '''class Parent:
    def touch(self, value: int) -> None:
        print("parent", value)
class Duck:
    def touch(self, value: int) -> None:
        print("duck", value)
'''

_VOID_USER = '''from owner import Parent

def run(parent: Parent | None, argument) -> None:
    if parent is not None:
        parent.touch(argument())

def strict(parent: Parent, argument) -> None:
    parent.touch(argument())

def nested(parent: Parent | None, argument) -> None:
    def inner() -> None:
        if parent is not None:
            typed: Parent = parent
            typed.touch(argument())
    inner()

def concrete() -> None:
    Parent().touch(11)
'''

_VOID_MAIN = '''from user import run, strict, nested, concrete
from owner import Parent, Duck
def opaque(obj):
    return obj
def argument():
    print("argument")
    return 5
p = Parent()
d = Duck()
run(opaque(p), argument)
run(opaque(p), argument)
run(opaque(d), argument)
run(opaque(None), argument)
nested(opaque(d), argument)
try:
    strict(opaque(None), argument)
except AttributeError:
    print("attribute-error")
concrete()
'''

_VOID_EXPECTED = b"argument\nparent 5\nargument\nparent 5\nargument\nduck 5\nargument\nduck 5\nattribute-error\nparent 11\n"


def _void_program(tmp_path, *, mutation=False):
    owner = tmp_path / "owner.py"
    owner_source = _VOID_OWNER
    if mutation:
        owner_source += '''class Child(Parent):
    def touch(self, value: int) -> None:
        print("child", value)
def plain(value):
    print("descriptor", value)
class Descriptor(Parent):
    @property
    def touch(self):
        print("lookup")
        return plain
'''
    owner.write_text(owner_source, encoding="utf-8")
    user = tmp_path / "user.py"
    user.write_text(_VOID_USER, encoding="utf-8")
    main = tmp_path / "main.py"
    main_source = _VOID_MAIN
    expected = _VOID_EXPECTED
    if mutation:
        main_source = '''from user import run, strict
from owner import Parent, Child, Descriptor
def opaque(obj):
    return obj
def argument():
    print("argument")
    return 5
def replacement(self, value):
    print("replacement", value)
def mutate():
    print("mutate")
    Parent.touch = replacement
    return 6
def fail():
    print("fail")
    raise ValueError("argument")
p = Parent()
run(opaque(p), argument)
run(opaque(p), argument)
run(opaque(Child()), argument)
run(opaque(Descriptor()), argument)
run(opaque(p), mutate)
run(opaque(p), argument)
try:
    run(opaque(p), fail)
except ValueError:
    print("argument-error")
run(opaque(p), argument)
'''
        expected = b"argument\nparent 5\nargument\nparent 5\nargument\nchild 5\nlookup\nargument\ndescriptor 5\nmutate\nparent 6\nargument\nreplacement 5\nfail\nargument-error\nargument\nreplacement 5\n"
    main.write_text(main_source, encoding="utf-8")
    return main, user, owner, expected


def test_void_external_guard_preserves_live_direct_and_nested_abi(tmp_path):
    from pcc.frontends.python.pipeline import compile_python_multi

    main, user, owner, _ = _void_program(tmp_path)
    out = tmp_path / "combined.ll"
    compile_python_multi([str(main), str(user), str(owner)], str(out),
                         emit_llvm_only=True, entry_module="main",
                         module_names=["main", "user", "owner"])
    ir_text = out.read_text(encoding="utf-8")
    for name in ("user_user_run", "user_user_strict", "user_user___nested_inner"):
        body = _body(ir_text, name)
        assert re.search(r"call void[^\n]*@user_owner_Parent_touch\(", body)
        assert "@user_owner_Parent_touch_method_native_adapter" in body
        assert "@py_func_slot_has_native_entry" in body
        assert "@py_obj_load_method" in body
        assert "@py_obj_call_loaded_method" in body
        assert "py_cpy_getattr" not in body and "py_cpy_call" not in body
    concrete = _body(ir_text, "user_user_concrete")
    assert "@user_owner_Parent_touch(" in concrete
    assert "@py_func_slot_has_native_entry" not in concrete


@pytest.mark.integration
@pytest.mark.parametrize("mutation", (False, True))
def test_void_external_annotation_guard_native(tmp_path, threaded_pcc_runtime_archive, mutation):
    from pcc.frontends.python.pipeline import compile_python_multi

    main, user, owner, expected = _void_program(tmp_path, mutation=mutation)
    reference = subprocess.run([sys.executable, str(main)], capture_output=True, timeout=10)
    assert (reference.returncode, reference.stdout, reference.stderr) == (0, expected, b"")
    native = tmp_path / "native"
    compile_python_multi([str(main), str(user), str(owner)], str(native),
                         entry_module="main", module_names=["main", "user", "owner"],
                         backend="self", ir_scaffold_mode="on",
                         runtime_archive=str(threaded_pcc_runtime_archive))
    actual = subprocess.run([str(native)], capture_output=True, timeout=10)
    assert (actual.returncode, actual.stdout, actual.stderr) == (0, expected, b"")


@pytest.mark.parametrize("collision", ("body", "adapter"))
def test_external_void_noncanonical_or_colliding_adapter_uses_live_fallback(tmp_path, collision):
    from pcc.frontends.python.pipeline import compile_python_multi

    owner = tmp_path / "owner.py"
    collision_name = "Parent_touch" if collision == "body" else "Parent_touch_method"
    owner.write_text("def " + collision_name + "():\n    return None\n"
                     "class Parent:\n    def touch(self) -> None:\n        print('touch')\n",
                     encoding="utf-8")
    user = tmp_path / "user.py"
    user.write_text("from owner import Parent\ndef run(parent: Parent) -> None:\n    parent.touch()\n",
                    encoding="utf-8")
    out = tmp_path / "combined.ll"
    compile_python_multi([str(user), str(owner)], str(out), emit_llvm_only=True,
                         entry_module="user", module_names=["user", "owner"])
    body = _body(out.read_text(encoding="utf-8"), "user_user_run")
    assert "@py_func_slot_has_native_entry" not in body
    assert "@user_owner_Parent__method_touch_method_native_adapter" not in body
    assert re.search(r"@py_obj_(?:load_method|getattr)", body)
    assert re.search(r"@py_obj_call", body)
    assert "py_cpy_getattr" not in body and "py_cpy_call" not in body


def test_external_void_raw_machine_argument_does_not_enter_guard(tmp_path):
    from pcc.frontends.python.pipeline import compile_python_multi
    from pcc.frontends.python.pipeline_context import build_closed_world_context

    owner = tmp_path / "owner.py"
    owner.write_text("from pcc import i64\nclass Parent:\n"
                     "    def touch(self, value: i64) -> None:\n        pass\n",
                     encoding="utf-8")
    user = tmp_path / "user.py"
    user.write_text("from pcc import i64\nfrom owner import Parent\n"
                    "def run(parent: Parent, value: i64) -> None:\n    parent.touch(value)\n",
                    encoding="utf-8")
    paths = [str(user), str(owner)]
    names = ["user", "owner"]
    _, exports, _ = build_closed_world_context(paths, names)
    method = exports["owner"]["Parent"]["methods"][0]
    assert method["void_managed_pointer_abi"] is False
    out = tmp_path / "combined.ll"
    compile_python_multi(paths, str(out), emit_llvm_only=True,
                         entry_module="user", module_names=names)
    body = _body(out.read_text(encoding="utf-8"), "user_user_run")
    # The separately known cross-module raw integer export mismatch is not
    # an admission for this guard. Never use its erased int descriptor to
    # claim a managed-pointer direct ABI or generate a new guarded branch.
    assert "@py_func_slot_has_native_entry" not in body
    assert "live_void" not in body


@pytest.mark.parametrize("flag", ("missing", False, None, 1, "true", True))
def test_external_void_guard_requires_exact_source_owned_abi_fact(tmp_path, flag):
    from types import SimpleNamespace

    from pcc.frontends.python.codegen.method_call_expression_lowering import MethodCallExpressionLoweringMixin
    from pcc.frontends.python.pipeline_context import build_closed_world_context

    _, user, owner, _ = _void_program(tmp_path)
    _, exports, _ = build_closed_world_context([str(user), str(owner)], ["user", "owner"])
    provider = exports["owner"]
    source_method = provider["Parent"]["methods"][0]
    assert source_method["void_managed_pointer_abi"] is True
    if flag == "missing":
        del source_method["void_managed_pointer_abi"]
    else:
        source_method["void_managed_pointer_abi"] = flag
    host = SimpleNamespace(_native_module_exports=exports,
                           ast_module=SimpleNamespace(name="user"))
    info = SimpleNamespace(owning_module="owner", export_class_name="Parent")
    fn = SimpleNamespace(name="user_owner_Parent_touch")
    result = MethodCallExpressionLoweringMixin._external_void_managed_source(host, info, fn, "touch")
    assert (result is provider) is (flag is True)
