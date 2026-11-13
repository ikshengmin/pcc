"""Execute the production import filter through host and native compilers.

String membership and identity follow Python Language Reference 6.10.2 and
6.10.3. The emitted program includes the actual helper source, not a rewritten
model. This is changed-shape execution, not a Stage2 performance measurement.
"""

import inspect
import os
import subprocess
import sys

import pytest

from pcc.frontends.python import pipeline_import_scan


PROGRAM = r'''
events = []

class CustomText(str):
    def __contains__(self, item):
        raise AssertionError("custom containment must not run")

    def splitlines(self, keepends=False):
        assert keepends is True
        events.append("subclass splitlines")
        return ["except AttributeError:\n", "    import legacy\n", "import current\n"]

class CustomSource:
    def __contains__(self, item):
        raise AssertionError("duck containment must not run")

    def splitlines(self, keepends=False):
        assert keepends is True
        events.append("duck splitlines")
        return ["except AttributeError:\n", "    import legacy\n", "import current"]

class FailingText(str):
    def splitlines(self, keepends=False):
        assert keepends is True
        events.append("failing splitlines")
        raise ValueError("source split failure")

def main():
    ordinary = "from .current import VALUE\r\ntext = '中文'\n"
    assert _without_attribute_error_handler_imports(ordinary) is ordinary
    assert _without_attribute_error_handler_imports("") == ""
    ordinary_handler = "try:\n    modern()\nexcept ValueError:\n    import current\n"
    assert _without_attribute_error_handler_imports(ordinary_handler) is ordinary_handler
    fallback = "try:\n    modern()\nexcept AttributeError as error:\n    from legacy import old\nimport current"
    assert _without_attribute_error_handler_imports(fallback) == (
        "try:\n    modern()\nexcept AttributeError as error:\n\nimport current"
    )
    false_positive = "text = 'except AttributeError'\nimport current\n"
    assert _without_attribute_error_handler_imports(false_positive) == false_positive
    custom = CustomText("no marker in the underlying text")
    assert type(custom) is not str
    assert _without_attribute_error_handler_imports(custom) == (
        "except AttributeError:\n\nimport current\n"
    )
    assert _without_attribute_error_handler_imports(CustomSource()) == (
        "except AttributeError:\n\nimport current"
    )
    caught = False
    try:
        _without_attribute_error_handler_imports(FailingText("import ordinary\n"))
    except ValueError as error:
        assert str(error) == "source split failure"
        caught = True
    assert caught
    assert events == ["subclass splitlines", "duck splitlines", "failing splitlines"]
    print("attribute-error-import-filter-ok")

main()
'''

EXPECTED = "attribute-error-import-filter-ok\n"

GC_REPORT = '''
from pcc.extern import c_int64, extern
observed_gc_backend = extern("pcc_gc_backend", (), c_int64)
print("GC", observed_gc_backend())
'''


def _write_program(tmp_path, *, native=False):
    helper = inspect.getsource(
        pipeline_import_scan._without_attribute_error_handler_imports
    )
    source = tmp_path / "attribute_error_filter.py"
    source.write_text(
        helper + "\n" + PROGRAM + (GC_REPORT if native else ""),
        encoding="utf-8",
    )
    return source


def test_attribute_error_filter_cpython_reference(tmp_path):
    source = _write_program(tmp_path)
    result = subprocess.run(
        [sys.executable, str(source)], cwd=tmp_path,
        capture_output=True, text=True, timeout=20,
    )
    assert (result.returncode, result.stdout, result.stderr) == (0, EXPECTED, "")


@pytest.mark.integration
def test_attribute_error_filter_native(
    tmp_path, pcc_runtime_archive, python_program_compiler,
):
    source = _write_program(tmp_path, native=True)
    output = tmp_path / "attribute-error-filter"
    python_program_compiler(
        str(source), str(output), backend="self", ir_scaffold_mode="on",
        runtime_archive=str(pcc_runtime_archive),
    )
    for backend in range(5):
        result = subprocess.run(
            [str(output)], cwd=tmp_path, capture_output=True, text=True, timeout=20,
            env=dict(os.environ, PATH="/nonexistent", PCC_GC_BACKEND=str(backend)),
        )
        assert (result.returncode, result.stdout, result.stderr) == (
            0, EXPECTED + f"GC {backend}\n", "",
        ), backend
