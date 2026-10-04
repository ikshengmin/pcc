"""Native context producers hand owned slots to the shared binding path."""
import re

import pytest

from tests.python.test_builtin_open_binding_ownership import _emit, _body


@pytest.mark.parametrize("source", (
    "import builtins as io\nclass Previous:\n    def __del__(self):\n        pass\n"
    "def read_file(path):\n    stream = Previous()\n"
    "    with io.open(path, 'rb') as stream:\n        return stream.read()\n",
    "class Manager:\n    def __enter__(self):\n        return self\n"
    "    def __exit__(self, kind, value, tb):\n        return False\n"
    "def read_file():\n    with Manager() as manager:\n        return manager\n",
    "def read_file(manager):\n    with manager as entered:\n        return entered\n",
    "import tempfile\ndef read_file():\n"
    "    with tempfile.TemporaryDirectory(prefix='ctx') as directory:\n        return directory\n",
))
def test_native_context_results_move_from_registered_roots(source):
    body = _body(_emit(source))
    assert "@pcc_gc_root_move(" in body
    assert "with.context" in body
    assert "with." in body and ".enter.operand" in body
    assert "@py_cpy_" not in body
    # The former bug stored a raw retained result after releasing the old
    # as-target. The rooted binding path performs the replacement via move.
    assert not re.search(r"store ptr %gc.retain[^,]*, ptr %(stream|entered|manager)\.addr", body)


@pytest.mark.parametrize("ending", ("return entered", "raise ValueError('body')", "entered = None"))
def test_aliasing_enter_return_keeps_existing_unwind_paths(ending):
    body = _body(_emit(
        "class Manager:\n    def __enter__(self):\n        return self\n"
        "    def __exit__(self, kind, value, tb):\n        return False\n"
        "def read_file():\n    with Manager() as entered:\n        " + ending + "\n"
    ))
    assert "@pcc_gc_root_move(" in body
    assert "@py_context_exit(" in body
    assert "with.err" in body


def test_dynamic_context_enter_publishes_before_cleanup():
    body = _body(_emit("def read_file(manager):\n    with manager as entered:\n        return entered\n"))
    result = re.search(r"(?P<value>%[^ ]+) = call [^\n]*@py_context_enter\([^\n]*\)\n(?P<next>[^\n]+)", body)
    assert result is not None
    assert result.group("next").strip().startswith("store ptr " + result.group("value") + ", ptr ")


def test_module_context_uses_a_registered_global_manager_owner():
    text = _emit("import builtins\nwith builtins.open('payload', 'rb') as source:\n    payload = source.read()\n")
    assert ".modvar.owned_open.with_context" in text
    assert "context_rooted" in text
    assert "with.module.root" in text
    assert "@pcc_gc_frame_enter(" in text
    assert "@py_file_close(" in text
