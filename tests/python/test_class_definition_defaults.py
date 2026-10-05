"""Class defaults are captured while the class body executes, not at calls."""

import contextlib
import io
import os
from pathlib import Path
import re
import subprocess

import pytest

from pcc.frontends.python.pipeline import compile_python
from pcc.backend.owned_object_emit import emit_owned_object


PROGRAM = '''events = []
def mark(value):
    events.append(value)
    return object()

class Sample:
    sentinel = mark(1)
    original = sentinel
    def __init__(self, value=sentinel):
        self.value = value
    def method(self, value=mark(2)):
        return value
    alias = method
    @staticmethod
    def static(value=sentinel):
        return value
    @classmethod
    def class_method(cls, *, value=sentinel):
        return value
    sentinel = mark(3)

class NewOnly:
    sentinel = object()
    original = sentinel
    def __new__(cls, value=sentinel):
        return value
    sentinel = object()

class Private:
    __sentinel = object()
    original = __sentinel
    def method(self, value=__sentinel):
        return value
    __sentinel = object()

def probe():
    sample = Sample()
    print(sample.value is Sample.original)
    print(sample.method() is sample.method())
    print(sample.alias() is sample.method())
    print(Sample.static() is Sample.original)
    print(Sample.class_method() is Sample.original)
    print(Sample(value=Sample.sentinel).value is Sample.sentinel)
    print(NewOnly() is NewOnly.original)
    print(NewOnly(value=NewOnly.sentinel) is NewOnly.sentinel)
    print(Private().method() is Private.original)
    print(events)
probe()
'''
EXPECTED = "True\n" * 9 + "[1, 2, 3]\n"

PREPARED = '''events = []
def factory():
    events.append(1)
    return object()
class Meta(type):
    @classmethod
    def __prepare__(cls, name, bases):
        return {}
    def __new__(cls, name, bases, namespace):
        return type(name, bases, namespace)
class Sample(metaclass=Meta):
    sentinel = object()
    original = sentinel
    def method(self, value=factory()):
        return value
    def other(self, value=sentinel):
        return value
    sentinel = object()
def probe():
    sample = Sample()
    print(sample.method() is sample.method())
    print(sample.other() is Sample.original)
    print(events)
probe()
'''

FAILED_DEFAULTS = '''import gc
import weakref
references = []
finalized = []
class Produced:
    def __del__(self):
        finalized.append(1)
def first():
    value = Produced()
    references.append(weakref.ref(value))
    return value
def second():
    raise ValueError("second default")
def main():
    try:
        class Broken:
            def method(self, left=first(), right=second()):
                return left
    except ValueError as error:
        assert str(error) == "second default"
    gc.collect()
    assert len(references) == 1
    assert references[0]() is None
    assert finalized == [1]
    print("DEFAULT_OWNERS_RELEASED")
main()
'''


def _inherited_signature_program(frozen, factory):
    decorator = "@dataclass(frozen=True)" if frozen else "@dataclass"
    default = "field(default_factory=list)" if factory else "()"
    identity = "is not" if factory else "is"
    return f'''from dataclasses import dataclass, field
try:
    {decorator}
    class Base:
        first: object = {default}
        second: object = None
    {decorator}
    class Child(Base):
        third: object = ()
    {decorator}
    class Grandchild(Child):
        pass
except Exception:
    raise
def main():
    left = Grandchild()
    right = Grandchild()
    assert left.first {identity} right.first
    assert left.second is None
    assert left.third == ()
    print("INHERITED_SIGNATURE_ROOTS_OK")
main()
'''


INHERITED_SIGNATURE_PROGRAMS = [
    pytest.param(_inherited_signature_program(frozen, factory),
                 "INHERITED_SIGNATURE_ROOTS_OK\n",
                 id=("frozen" if frozen else "plain") +
                    ("-factory" if factory else "-default"))
    for frozen in (False, True)
    for factory in (False, True)
]


def _body(text, symbol):
    match = re.search(r"^define[^\n]*@" + re.escape(symbol) + r"\([^\n]*\)[^\n]*\{\n(.*?)^\}", text, re.M | re.S)
    assert match is not None, symbol
    return match[1]


def test_class_defaults_reference_semantics():
    output = io.StringIO()
    with contextlib.redirect_stdout(output):
        exec(PROGRAM, {})
    assert output.getvalue() == EXPECTED


def test_failed_default_factory_reference_releases_product():
    output = io.StringIO()
    with contextlib.redirect_stdout(output):
        exec(FAILED_DEFAULTS, {})
    assert output.getvalue() == "DEFAULT_OWNERS_RELEASED\n"


def test_class_defaults_ir_captures_factories_only_in_definition_order(tmp_path, monkeypatch):
    monkeypatch.setenv("PCC_PYTHON_IR_PASSES", "off")
    source = tmp_path / "class_defaults.py"
    output = tmp_path / "class_defaults.ll"
    source.write_text(PROGRAM)
    compile_python(str(source), str(output), emit_llvm_only=True,
                   backend="self", libpython_mode="off", ir_scaffold_mode="on")
    text = output.read_text()
    initializer = _body(text, "_pcc_py_module_init_class_defaults")
    probe = _body(text, "user_class_defaults_probe")
    factory_calls = list(re.finditer(r"call ptr[^\n]*@user_class_defaults_mark\(", initializer))
    assert len(factory_calls) == 3
    assert factory_calls[0].start() < initializer.index("@py_func_new_named") < factory_calls[1].start()
    assert factory_calls[1].start() < factory_calls[2].start()
    assert "@user_class_defaults_mark(" not in probe
    assert "@py_obj_load_method" in probe
    assert "@py_obj_call_method_kwargs" in probe
    assert "@py_obj_call_method(" in probe


def test_prepared_namespace_reuses_method_signatures(tmp_path, monkeypatch):
    monkeypatch.setenv("PCC_PYTHON_IR_PASSES", "off")
    source = tmp_path / "prepared_defaults.py"
    output = tmp_path / "prepared_defaults.ll"
    source.write_text(PREPARED)
    compile_python(str(source), str(output), emit_llvm_only=True,
                   backend="self", libpython_mode="off", ir_scaffold_mode="on")
    text = output.read_text()
    for symbol in ("main", "_pcc_py_module_init_prepared_defaults"):
        body = _body(text, symbol)
        assert len(re.findall(r"call ptr[^\n]*@user_prepared_defaults_factory\(", body)) == 1
        assert "namespace.method.method" in body
        assert "%method.method.signature.wrapper" not in body


def test_fully_supplied_defaults_keep_direct_static_abi(tmp_path, monkeypatch):
    monkeypatch.setenv("PCC_PYTHON_IR_PASSES", "off")
    source = tmp_path / "direct_defaults.py"
    output = tmp_path / "direct_defaults.ll"
    source.write_text('''from pcc import i64
class Builder:
    def publish(self, source, condition, error, line: i64, cleanup: i64, payload: i64 = -1):
        return payload
    def opaque(self, value=object()):
        return value
def direct(builder: Builder, value):
    Builder.publish(builder, "source", value, "error", i64(1), i64(2), i64(3))
    builder.opaque(value)
def captured(builder: Builder):
    return builder.opaque()
''')
    compile_python(str(source), str(output), emit_llvm_only=True,
                   backend="self", libpython_mode="off", ir_scaffold_mode="on")
    text = output.read_text()
    direct = _body(text, "user_direct_defaults_direct")
    assert "@user_direct_defaults_Builder_publish" in direct
    assert "@user_direct_defaults_Builder_opaque" in direct
    assert "@py_obj_load_method" not in direct
    assert "@py_obj_load_method" in _body(text, "user_direct_defaults_captured")


@pytest.mark.parametrize("source_text", [PROGRAM, PREPARED], ids=["plain", "prepared"])
def test_definition_default_leases_reach_owned_object_emitter(tmp_path, monkeypatch, source_text):
    monkeypatch.setenv("PCC_PYTHON_IR_PASSES", "off")
    source = tmp_path / "lease_joins.py"
    output = tmp_path / "lease_joins.ll"
    source.write_text(source_text)
    compile_python(str(source), str(output), emit_llvm_only=True,
                   backend="self", libpython_mode="off", ir_scaffold_mode="on")
    object_bytes = emit_owned_object(output.read_text(), "arm64-apple-darwin23.6.0")
    assert object_bytes[:4] == b"\xcf\xfa\xed\xfe"


@pytest.mark.parametrize("prepared", [False, True], ids=["plain-raise", "prepared-raise"])
def test_raising_default_factory_unwinds_every_lease_before_join(tmp_path, monkeypatch, prepared):
    monkeypatch.setenv("PCC_PYTHON_IR_PASSES", "off")
    source = tmp_path / "raising_defaults.py"
    output = tmp_path / "raising_defaults.ll"
    meta = '''class Meta(type):
    @classmethod
    def __prepare__(cls, name, bases):
        return {}
    def __new__(cls, name, bases, namespace):
        return type(name, bases, namespace)
''' if prepared else ""
    header = "class Broken(metaclass=Meta):" if prepared else "class Broken:"
    source.write_text('''def fail():
    raise ValueError("default boom")
''' + meta + '''try:
    ''' + header + '''
        sentinel = object()
        def existing(self, value=sentinel):
            return value
        def failing(self, value=fail()):
            return value
except ValueError:
    print("DEFAULT_FAILED")
''')
    compile_python(str(source), str(output), emit_llvm_only=True,
                   backend="self", libpython_mode="off", ir_scaffold_mode="on")
    ir_text = output.read_text()
    assert "signature.unwind" in ir_text
    assert "pin.unwind" in ir_text
    assert "class.body.unwind" in ir_text
    object_bytes = emit_owned_object(ir_text, "arm64-apple-darwin23.6.0")
    assert object_bytes[:4] == b"\xcf\xfa\xed\xfe"


def test_failed_signature_releases_partial_default_owners_at_emitter(tmp_path, monkeypatch):
    monkeypatch.setenv("PCC_PYTHON_IR_PASSES", "off")
    source = tmp_path / "failed_default_owners.py"
    output = tmp_path / "failed_default_owners.ll"
    source.write_text(FAILED_DEFAULTS)
    compile_python(str(source), str(output), emit_llvm_only=True,
                   backend="self", libpython_mode="off", ir_scaffold_mode="on",
                   target_triple="arm64-apple-darwin23.6.0")
    ir_text = output.read_text()
    assert "func.signature.tuples.unwind" in ir_text
    object_bytes = emit_owned_object(ir_text, "arm64-apple-darwin23.6.0")
    assert object_bytes[:4] == b"\xcf\xfa\xed\xfe"


@pytest.mark.parametrize("source_text,expected", INHERITED_SIGNATURE_PROGRAMS)
def test_inherited_signature_null_guard_retires_nested_roots(tmp_path, monkeypatch, source_text, expected):
    reference = io.StringIO()
    with contextlib.redirect_stdout(reference):
        exec(source_text, {})
    assert reference.getvalue() == expected
    monkeypatch.setenv("PCC_PYTHON_IR_PASSES", "off")
    source = tmp_path / "inherited_signatures.py"
    output = tmp_path / "inherited_signatures.ll"
    source.write_text(source_text)
    compile_python(str(source), str(output), emit_llvm_only=True,
                   backend="self", libpython_mode="off", ir_scaffold_mode="on",
                   target_triple="x86_64-unknown-linux-gnu")
    ir_text = output.read_text()
    initializer = _body(ir_text, "_pcc_py_module_init_inherited_signatures")
    # The tuple lookup's NULL edge must first retire both inherited-metadata
    # roots. A try-only cleanup override used to bypass this block entirely.
    guards = re.findall(
        r"%dataclass\.inherited\.factory[^\n]* = call ptr[^\n]*@py_tuple_get[^\n]*\n"
        r"[^\n]*\n  br i1 [^\n]*label %([^,\n]+),", initializer,
    )
    assert len(guards) >= 5  # two Child defaults, three Grandchild defaults
    for target in guards:
        assert target.startswith("dataclass.base.defaults.unwind.")
        block = re.search(r"^" + re.escape(target) + r":\n(.*?)(?=^\S|\Z)",
                          initializer, re.M | re.S).group(1)
        # The strict root-set validator cannot itself establish stack order.
        # Inspect the actual leave operands as well: metadata was entered last.
        leaves = re.findall(
            r"%gc\.frame\.lifo\.leave\.ptr[^\n]* = bitcast ptr "
            r"%dataclass\.base\.(defaults|init)\.[^\n]*\n"
            r"  call void \(ptr\) @pcc_gc_frame_leave_lifo", block,
        )
        assert leaves == ["defaults", "init"]
        assert "br label %func.signature.tuples.unwind." in block
    object_bytes = emit_owned_object(ir_text, "x86_64-unknown-linux-gnu")
    assert object_bytes[:4] == b"\x7fELF"


def test_inherited_signature_roots_reach_direct_object_worker(tmp_path, monkeypatch):
    from pcc.frontends.python import pipeline, pipeline_frontend_workers as workers

    for name in (
        "PCC_DIRECT_INDEXED_KERNEL_CAPTURE", "PCC_DIRECT_INDEXED_KERNEL_EMIT",
        "PCC_DIRECT_INDEXED_KERNEL_FUSE_USES",
        "PCC_DIRECT_INDEXED_KERNEL_REQUIRE_ZERO_FALLBACK",
        "PCC_DIRECT_INDEXED_KERNEL_RELEASE_FRONTEND",
        "PCC_DIRECT_INDEXED_NATIVE_OBJECT",
    ):
        monkeypatch.setenv(name, "1")
    monkeypatch.setenv("PCC_DIRECT_INDEXED_SIDECAR", "0")
    monkeypatch.setenv("PCC_PYTHON_IR_PASSES", "off")
    source = tmp_path / "inherited_signatures.py"
    source.write_text(_inherited_signature_program(True, False))
    manifest = tmp_path / "worker.manifest"
    result = tmp_path / "result.tsv"
    workers.write_worker_manifest(
        str(manifest), str(result), str(tmp_path), "", "", [str(source)],
        ["inherited_signatures"], [0], entry_module="inherited_signatures",
        sibling_inits=(), libpython_mode="off", ir_scaffold_mode="on", verbose=False,
    )
    assert pipeline.run_python_multi_codegen_worker(str(manifest)) == 0, result.read_text()
    object_path = tmp_path / "module_0.direct.pco"
    assert object_path.stat().st_size > 0
    assert "\tPCO\t" + str(object_path) in result.read_text()


@pytest.mark.integration
@pytest.mark.parametrize("source_text,expected", [
    pytest.param(PROGRAM, EXPECTED, id="plain"),
    pytest.param(PREPARED, "True\nTrue\n[1]\n", id="prepared"),
    pytest.param(FAILED_DEFAULTS, "DEFAULT_OWNERS_RELEASED\n", id="factory-owner-unwind"),
    *INHERITED_SIGNATURE_PROGRAMS,
])
def test_native_class_definition_defaults(tmp_path, pcc_runtime_archive, python_program_compiler, source_text, expected):
    source = tmp_path / "class_defaults.py"
    binary = tmp_path / "class_defaults"
    source.write_text(source_text)
    python_program_compiler(str(source), str(binary), backend="self",
                            libpython_mode="off", runtime_archive=str(pcc_runtime_archive))
    for backend in range(5):
        environment = dict(os.environ, PCC_GC_BACKEND=str(backend))
        environment.pop("LC_ALL", None)
        result = subprocess.run([str(binary)], capture_output=True, text=True,
                                timeout=30, env=environment)
        assert result.returncode == 0, (backend, result.stdout, result.stderr)
        assert result.stdout == expected, (backend, result.stdout, result.stderr)
