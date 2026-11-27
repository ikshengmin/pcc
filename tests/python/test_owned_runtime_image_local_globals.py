"""The owned runtime build marks archive-defined globals ``dso_local``.

The runtime archive is linked whole into one image, so a module's
``external global`` declaration of a name another runtime module defines
resolves inside that image. ``build_runtime_archive`` first writes every
module's optimized IR, collects those names (``runtime_image_local_globals``)
and marks the declarations ``external dso_local`` before emitting objects;
the AArch64 backend then addresses them with adrp + PAGEOFF
(tests/python/test_aarch64_global_page_access.py). Platform data
(``environ``), thread-local and internal globals are never marked.
"""
from pcc.frontends.python.owned_runtime_build import (
    mark_image_local_declarations,
    runtime_image_local_globals,
)


DEFINER = """@x = global i32 1, align 4
@"q.r" = internal global i8 0
@t = thread_local global i32 0
@w = weak global i64 0
@k = constant [2 x i8] c"ab"
@p = private constant i8 1
@e = external global i32
"""

USER = """@x = external global i32, align 4
@w = external global i64
@k = external constant [2 x i8]
@"q.r" = external global i8
@e = external global i32
@t = external thread_local global i32
@environ = external global ptr
@h = external hidden global i32
define void @f() {
  ret void
}
"""


def test_only_public_non_tls_definitions_are_image_local():
    assert runtime_image_local_globals([DEFINER, USER]) == frozenset({"@x", "@w", "@k"})


def test_declarations_of_image_local_globals_become_dso_local():
    marked = mark_image_local_declarations(USER, runtime_image_local_globals([DEFINER, USER]))
    assert marked.splitlines() == [
        "@x = external dso_local global i32, align 4",
        "@w = external dso_local global i64",
        "@k = external dso_local constant [2 x i8]",
        '@"q.r" = external global i8',
        "@e = external global i32",
        "@t = external thread_local global i32",
        "@environ = external global ptr",
        "@h = external hidden global i32",
        "define void @f() {",
        "  ret void",
        "}",
    ]
    # Idempotent, and untouched text is returned as is.
    assert mark_image_local_declarations(marked, frozenset({"@x", "@w", "@k"})) == marked
    assert mark_image_local_declarations(USER, frozenset()) is USER


def test_marked_declarations_are_addressed_directly():
    from pcc.backend.self_backend_aarch64_darwin import emit_aarch64_darwin_asm
    ir = 'target triple = "arm64-apple-darwin"\n' + mark_image_local_declarations(
        USER.split("define")[0], frozenset({"@x"})) + """define i32 @g() {
entry:
  %a = load i32, ptr @x, align 4
  %b = load i32, ptr @e, align 4
  %c = add i32 %a, %b
  ret i32 %c
}
"""
    assembly = emit_aarch64_darwin_asm(ir, optimize=True)
    assert "_x@PAGEOFF]" in assembly and "_x@GOTPAGE" not in assembly
    assert "_e@GOTPAGEOFF]" in assembly
