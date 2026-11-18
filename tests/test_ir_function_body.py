import pytest

from tests.ir_function_body import function_body


BODY = "define external { i64, i64 } @user_pick(ptr %arg) {\nentry:\n  ret { i64, i64 } zeroinitializer\n}"
DECOYS = '''@user_pick_native_adapter.vector.desc = global ptr @user_pick_native_adapter
declare external { i64, i64 } @user_pick(ptr)
define external ptr @user_pick_extra(ptr %arg) {
  %x = call ptr @py_obj_call(ptr %arg)
  ret ptr %x
}
'''


def test_exact_definition_ignores_descriptors_declarations_and_prefixes():
    assert function_body(DECOYS + BODY + "\n" + DECOYS, "user_pick") == BODY


@pytest.mark.parametrize("text", [DECOYS, BODY + "\n" + BODY])
def test_missing_or_ambiguous_definition_fails_closed(text):
    with pytest.raises(AssertionError, match="expected exactly one"):
        function_body(text, "user_pick")


def test_wrong_function_cannot_hide_forbidden_calls():
    body = BODY.replace("  ret", "  call ptr @py_obj_call(ptr %arg)\n  ret")
    assert "@py_obj_call" in function_body(DECOYS + body, "user_pick")
