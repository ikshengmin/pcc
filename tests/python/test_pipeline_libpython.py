"""Focused contracts for the extracted import/libpython pipeline seams."""

from __future__ import annotations


def test_pipeline_import_policy_facade_reexports_single_table_owners():
    from pcc.frontends.python import pipeline
    from pcc.frontends.python import pipeline_import_policy as policy

    assert pipeline._COMPILE_TIME_ONLY_IMPORT_FROMS is policy.COMPILE_TIME_ONLY_IMPORT_FROMS
    assert pipeline._COMPILE_TIME_ONLY_IMPORT_MODULES is policy.COMPILE_TIME_ONLY_IMPORT_MODULES
    assert pipeline._TEST_FACADE_IMPORT_MODULES is policy.TEST_FACADE_IMPORT_MODULES
    assert pipeline._ANNOTATION_ONLY_IMPORT_MODULES is policy.ANNOTATION_ONLY_IMPORT_MODULES
    assert pipeline._NATIVE_BUILTIN_IMPORTS is policy.NATIVE_BUILTIN_IMPORTS
    assert pipeline._NATIVE_IMPORT_FROMS is policy.NATIVE_IMPORT_FROMS
    assert pipeline._SCAFFOLD_IMPORT_MODULES is policy.SCAFFOLD_IMPORT_MODULES


def test_pipeline_libpython_ir_facade_has_one_owner():
    from pcc.frontends.python import pipeline
    from pcc.frontends.python import pipeline_libpython

    assert pipeline._ir_needs_libpython is pipeline_libpython.ir_needs_libpython
    assert not hasattr(pipeline, "_ensure_libpython_main_thread_init")
    assert not hasattr(pipeline_libpython, "link_flags")


def test_libpython_ir_scan_ignores_declarations_and_finds_calls():
    from pcc.frontends.python.pipeline_libpython import ir_needs_libpython

    assert not ir_needs_libpython("declare ptr @py_cpy_import(ptr)\n")
    assert ir_needs_libpython("%value = call ptr @py_cpy_import(ptr %name)\n")
