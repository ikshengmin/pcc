"""Header-owned annotations must survive both preprocessor entry routes.

The execution checks use pcc's C frontend/self backend and an explicitly
external system linker.  They are not native-pcc1 or full-nginx evidence.
"""

import platform
import sys

import pytest

from pcc.driver.project import TranslationUnit
from pcc.frontends.c.evaluator.c_evaluator import (
    CEvaluator,
    _preprocess_translation_unit_source,
)
from pcc.frontends.c.parse import make_c_parser


CPP_ROUTES = (
    pytest.param(False, id="owned"),
    pytest.param(True, id="system", marks=pytest.mark.pcc_gate(
        probe=CEvaluator._has_system_cpp,
    )),
)


SOURCE = r"""
/* The guarded definition is the contract used by glibc sys/cdefs.h. */
#ifndef __nonnull
#define __nonnull(params) __attribute__((__nonnull__ params))
#endif
int add_to(int *destination, const int *source) __nonnull((1, 2));
int add_to(int *destination, const int *source) {
    *destination += *source;
    return *destination;
}
int main(void) {
    int destination = 19;
    int source = 23;
    int result = add_to(&destination, &source);
    return result != 42 || destination != 42 || source != 23;
}
"""


@pytest.mark.parametrize("system_cpp", CPP_ROUTES)
def test_header_defines_its_function_like_nonnull_macro(system_cpp, tmp_path):
    processed = _preprocess_translation_unit_source(
        SOURCE, str(tmp_path), system_cpp,
    )
    parsed = make_c_parser().parse(processed)
    names = [getattr(node, "name", None) for node in parsed.ext]
    assert "add_to" in names
    assert "__nonnull" not in processed


@pytest.mark.pcc_gate(probe=lambda: sys.platform.startswith("linux")
                      and platform.libc_ver()[0] == "glibc"
                      and CEvaluator._has_system_cpp())
def test_real_glibc_fallback_does_not_leave_annotation_arguments(tmp_path):
    # glob.h is not in the owned fake-libc header set, just as in nginx's
    # ngx_linux_config.h.  It forces the real _system_cpp fallback, whose
    # unistd.h declarations use the function-like __nonnull annotation.
    processed = CEvaluator._system_cpp(
        "#include <glob.h>\n#include <unistd.h>\n",
        base_dir=str(tmp_path),
    )
    parsed = make_c_parser().parse(processed)
    names = [getattr(node, "name", None) for node in parsed.ext]
    assert "glob" in names
    assert "access" in names


@pytest.mark.pcc_gate(probe=lambda: sys.platform.startswith("linux")
                      and CEvaluator._has_system_cpp())
@pytest.mark.parametrize("system_cpp", CPP_ROUTES)
def test_nonnull_declarations_preserve_native_pointer_effects(system_cpp, tmp_path):
    evaluator = CEvaluator(backend="self")
    result = evaluator.run_translation_units_with_system_cc(
        [TranslationUnit("nonnull.c", str(tmp_path / "nonnull.c"), SOURCE)],
        base_dir=str(tmp_path),
        use_system_cpp=system_cpp,
        use_compile_cache=False,
        optimize=False,
        timeout=10,
    )
    assert (result.returncode, result.stdout, result.stderr) == (0, "", "")
