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
    """This boxed-return correction does not widen into the existing void ABI."""
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
    assert "py_cpy_getattr" not in body and "py_cpy_call" not in body
