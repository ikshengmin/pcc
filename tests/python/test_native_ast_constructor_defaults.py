"""Native module-alias AST constructors must run omitted field defaults."""
import os
from pathlib import Path
import subprocess
import sys

import pytest


def test_ast_class_constructor_keeps_omitted_defaults(tmp_path: Path, pcc_runtime_archive):
    from pcc.frontends.python.pipeline import compile_python_multi
    source = tmp_path / "ast_defaults.py"
    source.write_text('''from . import py_ast as pa

def main():
    value = pa.ClassType("Item", "", (), ())
    print(value.properties, value.valueclass)
main()
''')
    definitions = tmp_path / "py_ast.py"
    definitions.write_text('''from dataclasses import dataclass
@dataclass(frozen=True)
class ClassType:
    name: str
    module: str
    fields: tuple = ()
    bases: tuple = ()
    properties: tuple = ()
    valueclass: bool = False
''')
    binary = tmp_path / "ast_defaults"
    compile_python_multi([str(source), str(definitions)], str(binary),
        module_names=["pcc.frontends.python.entry", "pcc.frontends.python.py_ast"],
        entry_module="pcc.frontends.python.entry", backend="self",
        ir_scaffold_mode="on", runtime_archive=str(pcc_runtime_archive))
    ran = subprocess.run([str(binary)], capture_output=True, text=True, timeout=20)
    assert ran.returncode == 0, ran.stdout + ran.stderr
    assert ran.stdout.strip() == "() False"


@pytest.mark.integration
@pytest.mark.parametrize("provider_name", [
    "pcc.frontends.python.py_ast", "relocated.py_ast",
])
@pytest.mark.parametrize("enabled", [False, True])
@pytest.mark.xdist_group(name="full_field_constructor")
def test_full_field_constructor_keeps_live_import_binding(
    tmp_path, pcc_runtime_archive, monkeypatch, provider_name, enabled,
):
    """Python data model 3.3.1: preserve class arguments and field values.

    A live imported class must retain its normal binder even if its historical
    provider has the static full-field capability. Observe the real emitter;
    do not substitute a constructor result. Omitted defaults have a separate
    regression above.
    """
    from pcc.frontends.python.codegen.layer1 import L1CodeGen
    from pcc.frontends.python.codegen.self_module_contracts import (
        PY_AST_FIELD_CONSTRUCTOR_CONTRACT,
        SELF_MODULE_CONTRACTS,
    )
    from pcc.frontends.python.pipeline import compile_python_multi

    monkeypatch.setitem(
        SELF_MODULE_CONTRACTS, provider_name,
        (PY_AST_FIELD_CONSTRUCTOR_CONTRACT,) if enabled else (),
    )
    package_name = provider_name.rpartition(".")[0]
    package = tmp_path
    for part in package_name.split("."):
        package = package / part
        package.mkdir()
        (package / "__init__.py").write_text("")
    definitions = package / "py_ast.py"
    definitions.write_text('''from dataclasses import dataclass
@dataclass(frozen=True)
class ClassType:
    name: str
    module: str
    fields: tuple = ()
    bases: tuple = ()
    properties: tuple = ()
    valueclass: bool = False
''')
    program = '''from . import py_ast as pa

def main():
    value = pa.ClassType("Item", "model", ("field",), (), ("property",), True)
    print(value.name, value.module, value.fields, value.bases,
          value.properties, value.valueclass)
main()
'''
    source = package / "consumer.py"
    source.write_text(program)
    reference = subprocess.run(
        [sys.executable, "-m", package_name + ".consumer"],
        cwd=tmp_path, env=dict(os.environ, PYTHONPATH=str(tmp_path)),
        capture_output=True, text=True, timeout=10,
    )
    assert reference.returncode == 0, reference.stdout + reference.stderr

    requests = []
    original = L1CodeGen._emit_no_init_field_instance

    def observed(self, class_name, args, kwargs, *, force=False, expr=None):
        info = self.class_lowering.classes.get(class_name)
        if info is not None and info.owning_module == provider_name:
            requests.append(force)
        return original(self, class_name, args, kwargs, force=force, expr=expr)

    monkeypatch.setattr(L1CodeGen, "_emit_no_init_field_instance", observed)
    source.write_text(program + '''
from pcc.extern import extern, c_int64
observed_gc = extern("pcc_gc_backend", (), c_int64)
print("GC", observed_gc())
''')
    binary = tmp_path / "full_fields"
    compile_python_multi(
        [str(source), str(definitions)], str(binary),
        module_names=[package_name + ".consumer", provider_name],
        entry_module=package_name + ".consumer", backend="self",
        ir_scaffold_mode="on", runtime_archive=str(pcc_runtime_archive),
    )
    assert requests == [], (provider_name, enabled, requests)
    for collector in range(5):
        result = subprocess.run(
            [str(binary)], capture_output=True, text=True, timeout=10,
            env=dict(os.environ, PCC_GC_BACKEND=str(collector)),
        )
        assert (result.returncode, result.stdout, result.stderr) == (
            0, reference.stdout + "GC " + str(collector) + "\n", "",
        ), (collector, result)
