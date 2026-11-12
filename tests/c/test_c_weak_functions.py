"""GNU weak function definitions in the owned C path.

GCC manual, "Common Function Attributes" (weak): a weak definition is emitted
as a weak symbol that a strong definition elsewhere overrides. The IR carries
``define weak``; the self backend lowers it to the same weak symbol as
``weak_odr`` (Mach-O N_WEAK_DEF, ELF STB_WEAK). Because the body may be
replaced, no pass may rewrite its callers from that body: the IR inliner,
function-attribute inference and the AST eta reduction all leave it alone.
Weak objects and undefined weak references remain explicit gaps.
"""
import os
import shutil
import subprocess

import pytest

from pcc.driver.project import TranslationUnit
from pcc.frontends.c.evaluator.c_evaluator import CEvaluator

PROGRAM = r'''
__attribute__((weak)) int hook(void) { return 30; }
extern int proto(void) __attribute__((weak));
int proto(void) { return 2; }
__attribute__((weak)) int wrap(int x) { return x + 10; }
int main(void) { int (*volatile f)(void) = hook; return f() + proto() + wrap(0); }
'''


def _ir(source, level=2):
    evaluator = CEvaluator()
    units = evaluator.compile_translation_units(
        [TranslationUnit("weak.c", "weak.c", source)], use_system_cpp=False, frontend_opt_level=level)
    return units[0]


def test_weak_definitions_execute():
    assert CEvaluator().evaluate(PROGRAM) == 42


def test_weak_definition_is_weak_in_ir_and_yields_across_units():
    _name, ir_text, _return_type, external_defs = _ir(PROGRAM)
    defines = {line.split("@", 1)[1].split("(", 1)[0]: line for line in ir_text.splitlines()
               if line.startswith("define")}
    assert defines["hook"].startswith("define weak ") and defines["proto"].startswith("define weak ")
    # A body that may be replaced gets no attributes proven from that body.
    assert "willreturn" not in defines["hook"] and "nounwind" not in defines["hook"]
    assert ("weak function", "hook", "hook") in external_defs
    assert ("function", "main", "main") in external_defs


def test_calls_through_a_weak_wrapper_are_not_redirected():
    source = ("int target(int x) { return x + 1; }\n"
              "__attribute__((weak)) int wrap(int x) { return target(x); }\n"
              "int use(int y) { return wrap(y); }\n")
    ir_text = _ir(source)[1]
    use = ir_text[ir_text.index("@use("):]
    use = use[:use.index("\n}")]
    assert "@wrap(" in use and "@target(" not in use


GAPS = {
    "weak-object": ("int w __attribute__((weak)) = 1; int main(void) { return w; }", "GNU attribute 'weak'"),
    "undefined-weak-reference": ("extern int maybe(void) __attribute__((weak));"
                                 "int main(void) { return maybe ? maybe() : 0; }", "weak reference"),
    "static-weak": ("__attribute__((weak)) static int s(void) { return 1; } int main(void) { return s(); }",
                    "must be public"),
}


@pytest.mark.parametrize("name", GAPS)
def test_unmodeled_or_invalid_weak_uses_are_rejected(name):
    from pcc.frontends.c.codegen.c_declaration_state import CodegenError
    source, message = GAPS[name]
    with pytest.raises(CodegenError, match=message):
        CEvaluator().evaluate(source)


def reference_compiler():
    return shutil.which(os.environ.get("PCC_C_REFERENCE", "cc"))


@pytest.mark.integration
@pytest.mark.skipif(reference_compiler() is None, reason="no reference C compiler")
def test_weak_definitions_match_the_reference_compiler(tmp_path):
    unit = tmp_path / "original.c"
    unit.write_text(PROGRAM)
    binary = tmp_path / "reference"
    built = subprocess.run([reference_compiler(), "-std=gnu11", "-O0", "-w", str(unit), "-o", str(binary)],
                           capture_output=True, timeout=60)
    assert built.returncode == 0, built.stderr
    assert subprocess.run([str(binary)], capture_output=True, timeout=30).returncode == 42
