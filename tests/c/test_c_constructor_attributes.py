"""GNU constructor/destructor function attributes in the owned C path.

GCC manual, "Common Function Attributes" (constructor, destructor): a
constructor runs before main, a destructor after main returns or exit is
called; with priorities, a smaller constructor priority runs earlier and a
smaller destructor priority runs later, and functions without a priority run
after (constructors) or before (destructors) prioritized ones. Constructors
lower to llvm.global_ctors, which both self backends already emit as
initializer pointers. Destructors are registered with atexit by an internal
priority-0 constructor, so they run after the handlers main registers, as
.fini_array destructors do.
"""
import os
import shutil
import subprocess

import pytest

from pcc.driver.project import TranslationUnit
from pcc.frontends.c.evaluator.c_evaluator import CEvaluator

PROGRAM = r'''
#include <stdio.h>
#include <stdlib.h>
static int state;
static void plain(void) __attribute__((constructor));
static void plain(void) { state = state * 10 + 3; printf("plain %d\n", state); }
__attribute__((constructor(102))) static void second(void) { state = state * 10 + 2; printf("second %d\n", state); }
__attribute__((constructor(101))) static void first(void) { state = state * 10 + 1; printf("first %d\n", state); }
__attribute__((destructor)) static void bye(void) { printf("bye\n"); }
__attribute__((destructor(101))) static void last(void) { printf("last\n"); fflush(stdout); }
__attribute__((destructor(102))) void middle(void) { printf("middle\n"); }
static void from_main(void) { printf("atexit from main\n"); }
int main(void) {
    atexit(from_main);
    printf("main %d\n", state);
    return state == 123 ? 42 : 1;
}
'''

EXPECTED = ("first 1\nsecond 12\nplain 123\nmain 123\n"
            "atexit from main\nbye\nmiddle\nlast\n")


def _run_owned(source):
    from tests.owned_c_corpus import run_owned_c_corpus
    return run_owned_c_corpus(CEvaluator(), [TranslationUnit("ctor.c", "ctor.c", source)])


def test_constructors_and_destructors_run_in_gcc_order():
    result = _run_owned(PROGRAM)
    assert (result.returncode, result.stdout) == (42, EXPECTED)


def test_destructors_run_on_exit():
    source = r'''
        #include <stdio.h>
        #include <stdlib.h>
        __attribute__((destructor)) static void done(void) { printf("done\n"); }
        int main(void) { printf("exiting\n"); exit(7); }
    '''
    result = _run_owned(source)
    assert (result.returncode, result.stdout) == (7, "exiting\ndone\n")


GAPS = {
    "undefined-constructor": ("extern void init(void) __attribute__((constructor)); int main(void) { return 0; }",
                              "does not define"),
    "bad-priority": ("__attribute__((constructor(70000))) static void f(void) {} int main(void) { return 0; }",
                     "priority"),
}


@pytest.mark.parametrize("name", GAPS)
def test_unmodeled_or_invalid_constructor_uses_are_rejected(name):
    from pcc.frontends.c.codegen.c_declaration_state import CodegenError
    source, message = GAPS[name]
    with pytest.raises(CodegenError, match=message):
        CEvaluator().evaluate(source)


def reference_compiler():
    return shutil.which(os.environ.get("PCC_C_REFERENCE", "cc"))


@pytest.mark.integration
@pytest.mark.skipif(reference_compiler() is None, reason="no reference C compiler")
def test_constructor_order_matches_the_reference_compiler(tmp_path):
    unit = tmp_path / "original.c"
    unit.write_text(PROGRAM)
    binary = tmp_path / "reference"
    built = subprocess.run([reference_compiler(), "-std=gnu11", "-O0", "-w", str(unit), "-o", str(binary)],
                           capture_output=True, timeout=60)
    assert built.returncode == 0, built.stderr
    run = subprocess.run([str(binary)], capture_output=True, text=True, timeout=30)
    assert (run.returncode, run.stdout) == (42, EXPECTED)


def test_used_and_constructor_statics_survive_global_dce():
    source = ("__attribute__((used)) static int kept(void) { return 1; }\n"
              "static int dropped(void) { return 2; }\n"
              "__attribute__((constructor)) static void init(void) {}\n"
              "int main(void) { return 0; }\n")
    ir_text = CEvaluator().compile_translation_units(
        [TranslationUnit("used.c", "used.c", source)], use_system_cpp=False, frontend_opt_level=2)[0][1]
    defined = [line.split("@", 1)[1].split("(", 1)[0] for line in ir_text.splitlines() if line.startswith("define")]
    assert any(name.endswith("kept") for name in defined) and any(name.endswith("init") for name in defined)
    assert not any(name.endswith("dropped") for name in defined)
