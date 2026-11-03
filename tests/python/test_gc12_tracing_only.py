"""Execute the shared count-free liveness contract on GC1 and GC2."""

import os
import subprocess
import sys

import pytest

from test_gc4_tracing_only import COUNT_PROBE, LIFECYCLE, VALUES


@pytest.fixture(scope="module", params=["values", "lifecycle"])
def tracing_program(request, tmp_path_factory, pcc_runtime_archive):
    from pcc.backend.owned_object_emit import emit_owned_object
    from pcc.frontends.python.pipeline import compile_python
    from pcc.frontends.python.pipeline_targets import host_target_triple
    from tests.python.test_owned_external_tls import _c_ir

    directory = tmp_path_factory.mktemp("gc12_" + request.param)
    program = VALUES if request.param == "values" else LIFECYCLE
    reference = subprocess.run([sys.executable, "-c", program],
                               capture_output=True, text=True, timeout=20)
    assert reference.returncode == 0 and reference.stderr == "", reference
    expected = reference.stdout
    objects = ()
    if request.param == "values":
        target = host_target_triple()
        helper = directory / "count.o"
        helper.write_bytes(emit_owned_object(_c_ir(COUNT_PROBE, target), target))
        objects = (str(helper),)
        program = '''from pcc.extern import extern, c_int64
count_probe = extern("tracing_count_probe", (), c_int64)
print(count_probe())
''' + program
        expected = "1\n" + expected
    source = directory / "program.py"
    source.write_text(program)
    binary = directory / "program"
    compile_python(str(source), str(binary), backend="self",
                   runtime_archive=str(pcc_runtime_archive), object_inputs=objects)
    return binary, expected


@pytest.mark.integration
@pytest.mark.xdist_group("gc12_tracing")
@pytest.mark.parametrize("backend", [1, 2])
def test_count_free_values_and_lifecycle_execute(tracing_program, backend):
    binary, expected = tracing_program
    result = subprocess.run(
        [str(binary)], capture_output=True, text=True, timeout=30,
        env=dict(os.environ, PATH="/nonexistent", PCC_GC_BACKEND=str(backend),
                 PCC_GC_TRACING_ONLY="1"),
    )
    assert (result.returncode, result.stdout, result.stderr) == (0, expected, ""), backend
